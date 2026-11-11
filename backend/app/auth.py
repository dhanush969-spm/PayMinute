"""Log in with PayPal (OAuth code flow) and the login session."""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from pydantic import BaseModel, Field

from . import config, db
from .ai import phone as phone_ai
from .paypal import service as pp
from .paypal.client import PayPalError

log = logging.getLogger("payminute.auth")
router = APIRouter(prefix="/api/auth")

SANDBOX_CONNECT = "https://www.sandbox.paypal.com/connect"
PUBLIC_PATHS = {"/api/health", "/api/config", "/api/webhooks/paypal"}


def redirect_uri() -> str:
    return f"{config.settings.app_base_url}/api/auth/callback"


SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_versions (
    account TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""


def _version(account: str | None) -> int:
    if not account:
        return 0
    with db.connect() as conn:
        row = conn.execute("SELECT version FROM session_versions WHERE account=?", (account,)).fetchone()
    return row["version"] if row else 0


def session_problem(user: dict[str, Any] | None, now: float | None = None) -> str | None:
    """Why a stored login is no longer valid: 'expired' (too old), 'idle' (inactive too long),
    'revoked' (logged out on all devices), or None while it's fine."""
    if not user:
        return "missing"
    now = now or time.time()
    if user.get("exp", 0) <= now:
        return "expired"
    idle = config.settings.session_idle_minutes * 60
    if idle and now - user.get("seen", now) > idle:
        return "idle"
    if user.get("sv", 0) != _version(user.get("account") or user.get("payer_id")):
        return "revoked"
    return None


ENDED = {
    "expired": "Your session ended. Log in with PayPal again.",
    "idle": "You were logged out after {m} without activity. Log in with PayPal again.",
    "revoked": "You were logged out on all devices. Log in with PayPal again.",
}


def current_user(request: Request) -> dict[str, Any] | None:
    """The signed-in user; ends the session when it's too old, idle too long or revoked."""
    user = request.session.get("user") if "session" in request.scope else None
    if not user:
        return None
    problem = session_problem(user)
    if problem:
        request.session.clear()
        request.state.session_ended = problem
        return None
    now = int(time.time())
    if now - user.get("seen", 0) > 30:  # keep the activity clock fresh without rewriting the cookie every call
        user["seen"] = now
        request.session["user"] = user
    return user


def ended_message(request: Request) -> str:
    why = getattr(request.state, "session_ended", None)
    m = config.settings.session_idle_minutes
    return ENDED.get(why or "", "Please log in with PayPal.").format(m=f"{m} minute{'' if m == 1 else 's'}")


def require_user(request: Request) -> dict[str, Any] | None:
    """FastAPI dependency for every /api route except login, health, config and PayPal webhooks."""
    if not config.settings.auth_required:
        return None
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/api/auth/"):
        return None
    user = current_user(request)
    if not user:
        raise HTTPException(401, ended_message(request))
    return user


@router.get("/login")
def login(request: Request):
    s = config.settings
    if not s.paypal_configured:
        raise HTTPException(503, "PayPal sandbox credentials are missing on the server.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "flowEntry": "static",
        "client_id": s.paypal_client_id,
        "response_type": "code",
        "scope": config.settings.login_scopes,
        "redirect_uri": redirect_uri(),
        "state": state,
        "fullPage": "true",
    }
    return RedirectResponse(f"{SANDBOX_CONNECT}?{urlencode(params)}", status_code=302)


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
             error_description: str | None = None):
    home = config.settings.app_base_url or "/"
    if error:
        return RedirectResponse(f"{home}/?login_error={error_description or error}", status_code=302)
    expected = request.session.pop("oauth_state", None)
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse(f"{home}/?login_error=Login expired, please try again", status_code=302)
    try:
        client = pp.get_client()
        token = client.exchange_auth_code(code, redirect_uri())
        info = client.userinfo(token["access_token"])
    except PayPalError as exc:
        log.warning("PayPal login failed: %s", exc)
        return RedirectResponse(f"{home}/?login_error={exc.message}", status_code=302)
    except Exception as exc:  # network errors etc.
        log.exception("PayPal login failed")
        return RedirectResponse(f"{home}/?login_error=Could not reach PayPal ({type(exc).__name__})", status_code=302)
    emails = info.get("emails") or []
    email = next((e.get("value") for e in emails if e.get("primary")), None) or (emails[0].get("value") if emails else None)
    allowed = config.settings.allowed_emails
    if allowed and (email or "").lower() not in allowed:
        return RedirectResponse(f"{home}/?login_error=This PayPal account isn't allowed to use this PayMinute", status_code=302)
    name = info.get("name") or email or "PayPal user"
    payer_id = info.get("payer_id")
    # with only the openid scope PayPal returns just user_id, so use it as the account key
    account = payer_id or (str(info.get("user_id") or "").rstrip("/").split("/")[-1] or None)
    verified = str(info.get("verified_account", "")).lower() == "true"
    saved = db.phone_for_payer(account) if account else None
    if saved and not email:
        email, name = saved.get("email"), saved.get("name") or name
    phone, phone_source = None, None
    paypal_phone = phone_ai.normalize(str(info.get("phone_number") or "")) if info.get("phone_number") else None
    if paypal_phone and account:
        # number comes from the PayPal profile, so link it
        db.link_phone(paypal_phone, account, name, email, verified, "paypal")
        phone, phone_source = paypal_phone, "paypal"
    elif saved:
        phone, phone_source = saved["phone"], saved["source"]
    addr = info.get("address") or {}
    # city and country only: no street address in the session
    place = ", ".join(x for x in (addr.get("locality"), addr.get("region"), addr.get("country")) if x) or None
    request.session["user"] = {
        "name": name, "email": email, "payer_id": payer_id, "account": account, "verified": verified,
        "phone": phone, "phone_source": phone_source, "address": place,
        "exp": int(time.time()) + config.settings.session_hours * 3600, "seen": int(time.time()), "sv": _version(account),
    }
    return RedirectResponse(home + "/", status_code=302)


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user and config.settings.auth_required:
        raise HTTPException(401, ended_message(request))
    return {"user": user}


class PhoneIn(BaseModel):
    phone: str = Field(min_length=4, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    name: str | None = Field(default=None, max_length=120)


@router.post("/phone")
def set_my_phone(body: PhoneIn, request: Request) -> dict[str, Any]:
    """Link your own number to your PayPal account when PayPal didn't share one at login."""
    user = current_user(request)
    account = (user or {}).get("account") or (user or {}).get("payer_id")
    if not account:
        raise HTTPException(401, "Log in with PayPal first.")
    if user.get("phone_source") == "paypal":
        raise HTTPException(409, "Your number already comes from your PayPal profile. Change it on PayPal and log in again.")
    e164 = phone_ai.normalize(body.phone)
    if not e164:
        raise HTTPException(400, "That phone number isn't valid. Include the country code.")
    email = user.get("email") or (body.email or "").strip().lower() or None
    if not email or "@" not in email:
        raise HTTPException(400, "Add the email of your PayPal account so payments can reach you.")
    name = user.get("name") if user.get("name") not in {None, "", "PayPal user"} else (body.name or "").strip() or "PayPal user"
    owner = db.find_phone(e164)
    if owner and owner["payer_id"] != account and owner["source"] == "paypal":
        raise HTTPException(409, "PayPal says this number belongs to another PayPal account.")
    db.link_phone(e164, account, name, email, bool(user.get("verified")), "user")
    user.update(phone=e164, phone_source="user", email=email, name=name)
    request.session["user"] = user
    return {"user": user}


@router.post("/logout")
def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@router.post("/logout-everywhere")
def logout_everywhere(request: Request) -> dict[str, Any]:
    """End every session of this PayPal account, on every device and browser."""
    user = current_user(request)
    account = (user or {}).get("account") or (user or {}).get("payer_id")
    if not account:
        raise HTTPException(401, ended_message(request))
    with db.connect() as conn:
        conn.execute("INSERT INTO session_versions (account, version) VALUES (?, 1) "
                     "ON CONFLICT(account) DO UPDATE SET version = version + 1", (account,))
    request.session.clear()
    return {"ok": True}


@router.post("/ping")
def ping(request: Request) -> dict[str, Any]:
    """The user is still here: keep the session alive and say when it ends."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, ended_message(request))
    return {"ok": True, "idle_minutes": config.settings.session_idle_minutes, "expires_at": user.get("exp")}
