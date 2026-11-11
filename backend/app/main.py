"""FastAPI app entry point."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from . import auth, automation, config, db, ledger
from .api import router
from .paypal.client import PayPalError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("payminute")

FRONTEND_DIST = config.ROOT_DIR / "frontend" / "dist"


async def _status_poller() -> None:
    """Background monitor: refresh pending/unclaimed payments from PayPal."""
    while True:
        await asyncio.sleep(max(15, config.settings.status_poll_seconds))
        try:
            changed = await run_in_threadpool(ledger.refresh_in_progress)
            for run in await run_in_threadpool(automation.run_due_schedules):
                log.info("scheduled payment %s: %s", run["schedule"], run["summary"])
            for tx in changed:
                log.info("status monitor: %s -> %s", tx["id"], tx["status"])
        except Exception as exc:  # never let the monitor die
            log.warning("status monitor error: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    s = config.settings
    log.info("PayMinute starting — PayPal sandbox %s, login %s, LLM: %s",
             "configured" if s.paypal_configured else "NOT configured (set PAYPAL_CLIENT_ID/SECRET)",
             "required" if s.auth_required else "off", s.resolved_llm_provider)
    task = asyncio.create_task(_status_poller())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="PayMinute API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def block_cross_site_writes(request: Request, call_next):
    """Another website can't make the browser move money here (CSRF), even when login is off."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        allowed = {"http://localhost:5173", "http://127.0.0.1:5173", config.settings.app_base_url.rstrip("/"),
                   f"{request.url.scheme}://{request.url.netloc}"}
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in allowed):
            return JSONResponse(status_code=403, content={"detail": "Blocked a request from another website."})
    return await call_next(request)


class AccountScope:
    """Every request runs as the signed-in PayPal account, so each user only sees their own data."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        user = (scope.get("session") or {}).get("user") or {}
        account = (user.get("account") or user.get("payer_id")) if user and auth.session_problem(user) is None else None
        token = db.CURRENT.set({"account": account, "name": user.get("name")} if account else None)
        try:
            await self.app(scope, receive, send)
        finally:
            db.CURRENT.reset(token)


# runs inside SessionMiddleware (added below), so the session is already decoded
app.add_middleware(AccountScope)
# signed cookie with the PayPal login (no tokens stored)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.settings.session_secret,
    session_cookie="payminute_session",
    max_age=8 * 3600,
    same_site="lax",
    https_only=config.settings.app_base_url.startswith("https://"),
)


@app.exception_handler(ledger.FlowError)
async def flow_error_handler(_: Request, exc: ledger.FlowError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "paypal": exc.paypal})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Server error: {type(exc).__name__}: {exc}"[:300]})


@app.exception_handler(PayPalError)
async def paypal_error_handler(_: Request, exc: PayPalError) -> JSONResponse:
    if exc.status == 503 and exc.name == "NOT_CONFIGURED":
        return JSONResponse(status_code=503, content={"detail": exc.message, "paypal": exc.to_dict()})
    return JSONResponse(status_code=502, content={"detail": f"PayPal error: {exc.message}", "paypal": exc.to_dict()})


app.include_router(auth.router)
app.include_router(router)

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file() and Path(candidate).resolve().is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
