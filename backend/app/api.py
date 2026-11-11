"""HTTP API for the React app."""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import auth, config, db, ledger
from .ai import agent, categorize, insights, llm as llm_mod, phone as phone_ai, status as status_ai
from .integrations import kernel_preview, mcp_bridge
from .paypal import service as pp

log = logging.getLogger("payminute.api")
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


class AssessIn(BaseModel):
    receiver: str = Field(min_length=3, max_length=254)
    amount: float = Field(gt=0, le=20000)
    note: str | None = Field(default=None, max_length=1000)
    name: str | None = Field(default=None, max_length=120)


class PayoutIn(AssessIn):
    client_ref: str = Field(min_length=8, max_length=64)
    acknowledge_risk: bool = False


class CheckoutIn(BaseModel):
    amount: float = Field(gt=0, le=10000)
    description: str = Field(min_length=1, max_length=127)
    client_ref: str = Field(min_length=8, max_length=64)


class PhoneIn(BaseModel):
    phone: str = Field(min_length=4, max_length=32)
    amount: float | None = Field(default=None, gt=0, le=20000)


class ReviewIn(BaseModel):
    approve: bool


class CategoryIn(BaseModel):
    category: str


class BudgetIn(BaseModel):
    category: str
    monthly_limit: float = Field(ge=0, le=1_000_000)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class ActionIn(BaseModel):
    approve: bool


def _tx_view(tx: dict[str, Any], with_history: bool = False) -> dict[str, Any]:
    view = dict(tx)
    view["insight"] = status_ai.explain(tx) if status_ai.is_in_progress(tx) or tx["status"] == "HELD_FOR_REVIEW" else None
    if with_history:
        view["history"] = db.status_history(tx["id"])
    return view


def _studio_public() -> dict[str, Any]:
    import os

    from . import studio
    cfg = studio.studio_ai_settings()
    # AG Studio licence keys are meant for the browser
    return {"license_key": os.getenv("AG_STUDIO_LICENSE_KEY", "").strip() or None,
            "ai_models": cfg["models"] if cfg["enabled"] else []}


def _integrations_public() -> dict[str, Any]:
    return {"mcp_servers": mcp_bridge.public_status(), "kernel": kernel_preview.enabled()}


def _need(tx_id: str, own: bool = False) -> dict[str, Any]:
    """The payment as the signed-in user sees it; 404 if it isn't theirs or sent to them.
    own=True: only the account that made the payment may act on it (refund, cancel, recategorise...)."""
    tx = db.visible_tx(tx_id)
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if own and tx.get("mine") is False:
        raise HTTPException(403, "Only the account that made this payment can change it.")
    return tx


@router.get("/config")
def get_config(request: Request) -> dict[str, Any]:
    s = config.settings
    user = auth.current_user(request)
    signed_in = bool(user) or not s.auth_required
    return {
        "mode": s.paypal_mode,
        "configured": s.paypal_configured,
        "auth_required": s.auth_required,
        "user": user,
        "session": {"idle_minutes": s.session_idle_minutes, "expires_at": (user or {}).get("exp"),
                    "ended": None if user else getattr(request.state, "session_ended", None)},
        "currency": s.currency,
        "locale": s.locale,
        "phone_region": s.phone_region,
        "business_name": s.business_name,
        "paypal_client_id": s.paypal_client_id if s.paypal_configured else None,
        "paypal_sdk_url": "https://www.sandbox.paypal.com/web-sdk/v6/core",
        "login_redirect_uri": auth.redirect_uri(),
        "llm": llm_mod.describe(),
        "webhook_configured": bool(s.paypal_webhook_id),
        "studio": _studio_public() if signed_in else None,
        "integrations": _integrations_public() if signed_in else None,
        "categories": {"expense": categorize.EXPENSE_CATEGORIES, "income": categorize.INCOME_CATEGORIES},
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "mode": config.settings.paypal_mode, "configured": config.settings.paypal_configured}


@router.get("/home")
def home() -> dict[str, Any]:
    hist = ledger.history()
    tracker = ledger.in_progress_with_explanations()
    return {
        "summary": insights.month_summary(hist),
        "forecast": insights.forecast_month(hist),
        "budgets": insights.budget_status(hist),
        "in_progress": [{"transaction": i["transaction"], "insight": i["insight"]} for i in tracker[:6]],
        "recent": [_tx_view(t) for t in hist[:8]],
        "recommendations": insights.rule_recommendations(hist)[:3],
        "risk_blocked_count": sum(1 for t in hist if t.get("risk_level") == "high"),
    }


@router.post("/phone/verify")
def verify_phone(body: PhoneIn) -> dict[str, Any]:
    """Check a phone number before paying it."""
    return phone_ai.analyze(body.phone, db.list_tx(limit=2000), body.amount, explain=True)


@router.post("/payouts/assess")
def assess_payout(body: AssessIn) -> dict[str, Any]:
    return ledger.assess_payout(body.receiver.strip(), body.amount, body.note, body.name)


@router.post("/payouts")
def create_payout(body: PayoutIn) -> dict[str, Any]:
    res = ledger.send_payout(body.receiver.strip(), body.amount, body.note, body.name, body.client_ref,
                             body.acknowledge_risk)
    res["transaction"] = _tx_view(res["transaction"], with_history=True)
    return res


@router.post("/transactions/{tx_id}/cancel")
def cancel_payout(tx_id: str) -> dict[str, Any]:
    _need(tx_id, own=True)
    return _tx_view(ledger.cancel_unclaimed(tx_id), with_history=True)


@router.post("/checkout/orders")
def create_order(body: CheckoutIn) -> dict[str, Any]:
    return ledger.create_checkout(body.amount, body.description.strip(), body.client_ref)


@router.post("/checkout/orders/{order_id}/complete")
def complete_order(order_id: str) -> dict[str, Any]:
    res = ledger.complete_checkout(order_id)
    res["transaction"] = _tx_view(res["transaction"], with_history=True)
    return res


@router.post("/transactions/{tx_id}/review")
def review(tx_id: str, body: ReviewIn) -> dict[str, Any]:
    _need(tx_id, own=True)
    return _tx_view(ledger.review_decision(tx_id, body.approve), with_history=True)


@router.post("/transactions/{tx_id}/refund")
def refund(tx_id: str) -> dict[str, Any]:
    _need(tx_id, own=True)
    return _tx_view(ledger.refund(tx_id), with_history=True)


@router.get("/transactions")
def transactions(
    direction: Literal["in", "out"] | None = None,
    category: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, le=1000),
) -> list[dict[str, Any]]:
    statuses = [s.strip().upper() for s in status.split(",")] if status else None
    return [_tx_view(t) for t in db.list_tx(limit=limit, direction=direction, category=category,
                                            statuses=statuses, search=search)]


@router.get("/transactions/{tx_id}")
def transaction(tx_id: str) -> dict[str, Any]:
    tx = _need(tx_id)
    view = _tx_view(tx, with_history=True)
    hist = ledger.history()
    histories = {t["id"]: db.status_history(t["id"]) for t in hist if t.get("kind") in {"payout", "checkout"}}
    view["insight"] = status_ai.explain(tx, hist, histories)
    return view


@router.patch("/transactions/{tx_id}/category")
def set_category(tx_id: str, body: CategoryIn) -> dict[str, Any]:
    if body.category not in categorize.ALL_CATEGORIES:
        raise HTTPException(400, "Unknown category")
    _need(tx_id, own=True)
    return _tx_view(categorize.apply_user_correction(tx_id, body.category))  # type: ignore[arg-type]


@router.post("/transactions/{tx_id}/refresh")
def refresh_tx(tx_id: str) -> dict[str, Any]:
    _need(tx_id, own=True)
    return _tx_view(ledger.refresh(db.get_tx(tx_id)), with_history=True)  # type: ignore[arg-type]


@router.post("/transactions/{tx_id}/check")
def check_tx(tx_id: str) -> dict[str, Any]:
    """Re-read the payment from PayPal (Payments API + Transaction Search) and show what PayPal says."""
    _need(tx_id, own=True)
    tx = ledger.check_with_paypal(tx_id)
    view = _tx_view(tx, with_history=True)
    view["insight"] = status_ai.explain(tx, ledger.history())
    return view


# what each Log in with PayPal scope adds to your profile
LOGIN_SCOPES = {
    "profile": "your full name",
    "email": "your PayPal email",
    "phone": "your phone number",
    "address": "your city and country",
    "https://uri.paypal.com/services/paypalattributes": "account verification and PayPal account ID",
}


def _amount(m: dict[str, Any] | None) -> float | None:
    try:
        return float((m or {}).get("value"))
    except (TypeError, ValueError):
        return None


@router.get("/account")
def paypal_account(request: Request) -> dict[str, Any]:
    """Your PayPal profile from Log in with PayPal, plus the balance of the app's PayPal business account."""
    from .paypal.client import PayPalError
    user = auth.current_user(request) or {}
    granted = config.settings.login_scopes.split()
    profile = {k: user.get(k) for k in ("name", "email", "phone", "phone_source", "verified", "payer_id", "account", "address")}
    business: dict[str, Any] | None = None
    error = None
    try:
        b = pp.get_balances()
        business = {
            "account_id": b.get("account_id"), "as_of": b.get("as_of_time") or b.get("last_refresh_time"),
            "balances": [{"currency": x.get("currency"), "primary": bool(x.get("primary")), "total": _amount(x.get("total_balance")),
                          "available": _amount(x.get("available_balance")), "withheld": _amount(x.get("withheld_balance"))}
                         for x in b.get("balances") or []],
        }
    except PayPalError as exc:
        error = exc.message + (" Turn on Transaction search for your sandbox app (Developer Dashboard → your app → Features) "
                               "so PayPal allows the List Balances API." if exc.status in (401, 403) else "")
    return {
        "profile": profile,
        "missing_scopes": [{"scope": k, "gives": v} for k, v in LOGIN_SCOPES.items() if k not in granted],
        "recommended_scopes": " ".join(["openid", *LOGIN_SCOPES]),
        "business": business, "business_error": error,
    }


OPEN_INVOICE = {"SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED"}


@router.get("/requests")
def payment_requests(request: Request) -> dict[str, Any]:
    """Payment requests (PayPal invoices) from this account, with their live PayPal status."""
    from .paypal.client import PayPalError
    me = ((auth.current_user(request) or {}).get("email") or "").lower()
    # the PayPal business account is shared by every user: keep the invoices this user created
    mine = {t["paypal_invoice_id"] for t in db.list_tx(limit=1000) if t.get("paypal_invoice_id") and t.get("mine", True)}
    try:
        data = pp.list_invoices(30)
    except PayPalError as exc:
        hint = " Turn on Invoicing for your sandbox app in the Developer Dashboard." if exc.status in (401, 403) else ""
        raise HTTPException(502, f"PayPal couldn't list your payment requests: {exc.message}.{hint}") from exc
    items = []
    for inv in data.get("items") or []:
        detail = inv.get("detail") or {}
        meta = detail.get("metadata") or {}
        billing = ((inv.get("primary_recipients") or [{}])[0] or {}).get("billing_info") or {}
        nm = billing.get("name") or {}
        name = nm.get("full_name") or " ".join(x for x in (nm.get("given_name"), nm.get("surname")) if x) or None
        email = (billing.get("email_address") or "").lower() or None
        status = (inv.get("status") or "").upper()
        to_me = bool(me and email == me)
        if db.current_account() and inv.get("id") not in mine and not to_me:
            continue
        items.append({
            "id": inv.get("id"), "number": detail.get("invoice_number"), "status": status, "open": status in OPEN_INVOICE,
            "to_name": name, "to_email": email, "to_me": to_me,
            "amount": _amount(inv.get("amount")), "due": _amount(inv.get("due_amount")),
            "currency": (inv.get("amount") or {}).get("currency_code") or detail.get("currency_code"),
            "note": detail.get("note") or detail.get("memo"), "created": meta.get("create_time"),
            "link": meta.get("recipient_view_url"),
        })
    open_items = [i for i in items if i["open"]]
    return {"items": items, "open_count": len(open_items),
            "open_total": round(sum((i["due"] if i["due"] is not None else i["amount"]) or 0 for i in open_items), 2)}


@router.get("/tracker")
def tracker() -> list[dict[str, Any]]:
    ledger.refresh_in_progress()
    return ledger.in_progress_with_explanations()


@router.post("/sync")
def sync() -> dict[str, Any]:
    return ledger.sync_from_paypal()


@router.get("/insights")
def get_insights() -> dict[str, Any]:
    hist = ledger.history()
    return {
        "summary": insights.month_summary(hist),
        "forecast": insights.forecast_month(hist),
        "budgets": insights.budget_status(hist),
        "suggested_budgets": insights.suggested_budgets(hist),
        "recurring": insights.detect_recurring(hist),
        "recommendations": insights.rule_recommendations(hist),
        "monthly_trend": _monthly_trend(hist),
    }


def _monthly_trend(hist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import OrderedDict
    from datetime import datetime, timezone
    buckets: "OrderedDict[str, dict[str, float]]" = OrderedDict()
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    for _ in range(4):
        buckets[f"{y}-{m:02d}"] = {"in": 0.0, "out": 0.0}
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for t in hist:
        key = t["created_at"][:7]
        if key in buckets and insights.counted(t):
            buckets[key][t["direction"]] += t["amount"]
    return [{"month": k, "money_in": round(v["in"], 2), "money_out": round(v["out"], 2)}
            for k, v in reversed(list(buckets.items()))]


@router.get("/insights/coach")
def coach() -> dict[str, Any]:
    return insights.ai_coach(ledger.history())


@router.put("/budgets")
def put_budget(body: BudgetIn) -> dict[str, Any]:
    if body.category not in categorize.EXPENSE_CATEGORIES:
        raise HTTPException(400, "Unknown expense category")
    db.set_budget(body.category, body.monthly_limit)
    return {"budgets": insights.budget_status(ledger.history())}


@router.post("/agent/chat")
async def chat(body: ChatIn, request: Request) -> dict[str, Any]:
    user = auth.current_user(request) or {}
    agent.CURRENT_USER.set(user)
    return await run_in_threadpool(agent.run_turn, body.session_id, body.message.strip(), user.get("name"))


@router.post("/agent/actions/{action_id}")
async def decide(action_id: str, body: ActionIn) -> dict[str, Any]:
    return await run_in_threadpool(agent.decide_action, action_id, body.approve)


@router.get("/agent/tools")
def agent_tools() -> dict[str, Any]:
    specs = agent.tool_specs()
    return {
        "llm": llm_mod.describe(),
        "mcp_servers": mcp_bridge.public_status(),
        "tools": [{"name": s["name"], "source": s["source"], "approval": agent.needs_approval(s["name"]),
                   "description": s["description"].split("\n")[0][:160]} for s in specs],
    }


class JobIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payee: str = Field(min_length=3, max_length=254)
    amount: float = Field(gt=0, le=20000)
    payee_name: str | None = Field(default=None, max_length=120)


class ScheduleIn(BaseModel):
    payee: str = Field(min_length=3, max_length=254)
    amount: float = Field(gt=0, le=20000)
    every: Literal["once", "daily", "weekly", "monthly"]
    start: str | None = None
    note: str | None = Field(default=None, max_length=200)
    payee_name: str | None = Field(default=None, max_length=120)


class DealIn(BaseModel):
    item: str = Field(min_length=1, max_length=80)
    seller: str = Field(min_length=3, max_length=254)
    budget: float = Field(gt=0, le=20000)
    list_price: float | None = Field(default=None, gt=0)
    floor: float | None = Field(default=None, ge=0)
    seller_name: str | None = Field(default=None, max_length=120)


class ListingIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    price: float = Field(gt=0, le=20000)
    description: str | None = Field(default=None, max_length=300)
    floor: float | None = Field(default=None, gt=0)


class ShopIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)  # one line: item, features, condition and budget
    budget: float | None = Field(default=None, gt=0, le=20000)


class ParseIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=400)
    role: Literal["buy", "sell"] = "buy"


class QuotePayIn(BaseModel):
    acknowledge_risk: bool = False


def _account(request: Request) -> tuple[dict[str, Any], str | None]:
    user = auth.current_user(request) or {}
    return user, user.get("account") or user.get("payer_id")


def _signed_in(request: Request) -> tuple[dict[str, Any], str]:
    user, account = _account(request)
    if not account:
        raise HTTPException(401, "Log in with PayPal first.")
    return user, account


@router.get("/market")
def market_overview(request: Request, q: str | None = None, budget: float | None = None) -> dict[str, Any]:
    from . import market
    _, account = _account(request)
    found = market.search(q, budget, account) if q else [market.public(x) | {"trust": market._trust(x["payee"])}
                                                          for x in market.listings() if x["seller_account"] != account]
    return {"listings": found, "mine": [market.public(x) | {"floor": x["floor"]} for x in market.my_listings(account)]}


@router.post("/market/listings")
def market_list(body: ListingIn, request: Request) -> dict[str, Any]:
    from . import market
    user, account = _signed_in(request)
    # buyers always pay the seller's own login phone or PayPal email; it can't be set from the request
    payee = user.get("phone") or user.get("email")
    if not payee:
        raise HTTPException(400, "Add your phone in the account menu so buyers can pay you.")
    return market.create_listing(body.title, body.price, payee, user.get("name"), account, body.description, body.floor)


@router.delete("/market/listings/{listing_id}")
def market_remove(listing_id: str, request: Request) -> dict[str, Any]:
    from . import market
    market.remove_listing(listing_id, _account(request)[1])
    return {"ok": True}


@router.post("/market/shop")
def market_shop(body: ShopIn, request: Request) -> dict[str, Any]:
    from . import market
    return market.shop(body.query, body.budget, _signed_in(request)[1])


@router.post("/market/parse")
def market_parse(body: ParseIn) -> dict[str, Any]:
    """Read a one-line description and suggest a fair price for it."""
    from . import market
    req = market.parse_request(body.prompt)
    hint = market.suggest_price(req["title"] or body.prompt, req["details"]) if req["title"] else None
    return {"request": {k: req[k] for k in ("title", "details", "price", "floor", "wanted")},
            "flaws": req["features"]["flaws"], "condition": req["features"]["condition"], "suggestion": hint}


@router.post("/market/shop/stream")
def market_shop_stream(body: ShopIn, request: Request):
    """The same shopping run, streamed live: one JSON event per line as the agents compare and bargain."""
    import os

    from fastapi.responses import StreamingResponse

    from . import market
    user, account = _signed_in(request)
    pause = float(os.getenv("MARKET_STREAM_PAUSE", "0.4"))  # the page adds its own typing delay

    def events():
        run = market.shop_events(body.query, body.budget, account, pause)
        while True:
            # each step may run on a different worker thread, so set the account for every step
            with db.as_account(account, user.get("name")):
                try:
                    ev = next(run)
                except StopIteration:
                    return
                except ledger.FlowError as exc:
                    ev = {"type": "done", "outcome": "error", "summary": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    log.exception("shop stream failed")
                    ev = {"type": "done", "outcome": "error", "summary": f"Shopping stopped: {type(exc).__name__}"}
            yield json.dumps(ev, default=str) + "\n"
            if ev["type"] == "done":
                return

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/market/compare")
def market_compare(request: Request, q: str, budget: float | None = None) -> dict[str, Any]:
    from . import market
    return {"candidates": market.compare(q, budget, _account(request)[1])}


@router.get("/market/quotes/{quote_id}")
def market_quote(quote_id: str, request: Request) -> dict[str, Any]:
    from . import market
    return market.get_quote(quote_id, _signed_in(request)[1])


@router.post("/market/quotes/{quote_id}/pay")
def market_pay_quote(quote_id: str, body: QuotePayIn, request: Request) -> dict[str, Any]:
    from . import market
    return market.pay_quote(quote_id, _signed_in(request)[1], body.acknowledge_risk)


class AgentPayIn(BaseModel):
    kind: Literal["job", "schedule", "deal"]
    ref: str = Field(min_length=1, max_length=64)


class AgentSettingsIn(BaseModel):
    auto_pay_max: float | None = Field(default=None, ge=0, le=20000)
    daily_limit: float | None = Field(default=None, ge=0, le=100000)
    allow_medium_risk: bool | None = None
    enabled: bool | None = None


@router.get("/agents")
def agents_overview() -> dict[str, Any]:
    from . import automation
    return {"settings": automation.settings(), "spent_today": automation.spent_today(), "jobs": automation.jobs(),
            "schedules": automation.schedules(), "runs": automation.runs()}


@router.put("/agents/settings")
def agents_settings(body: AgentSettingsIn) -> dict[str, Any]:
    from . import automation
    return automation.update_settings(**body.model_dump())


@router.post("/agents/jobs")
def agents_create_job(body: JobIn) -> dict[str, Any]:
    from . import automation
    return automation.create_job(body.title, body.payee, body.amount, body.payee_name)


@router.post("/agents/jobs/{job_id}/complete")
def agents_complete_job(job_id: str, approve: bool = False) -> dict[str, Any]:
    from . import automation
    return automation.complete_job(job_id, approved=approve)


@router.post("/agents/schedules")
def agents_create_schedule(body: ScheduleIn) -> dict[str, Any]:
    from datetime import datetime, timezone
    from . import automation
    start = datetime.fromisoformat(body.start) if body.start else None
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return automation.create_schedule(body.payee, body.amount, body.every, start, body.note, body.payee_name)


@router.post("/agents/schedules/{schedule_id}/{action}")
def agents_schedule_action(schedule_id: str, action: Literal["pause", "resume", "run"]) -> dict[str, Any]:
    from . import automation
    if action == "run":
        return {"results": automation.run_now(schedule_id)}
    return automation.set_schedule_active(schedule_id, action == "resume")


@router.post("/agents/deals")
def agents_deal(body: DealIn) -> dict[str, Any]:
    from . import automation
    return automation.negotiate(body.item, body.seller, body.budget, body.list_price, body.floor, body.seller_name)


@router.post("/agents/pay")
def agents_pay(body: AgentPayIn) -> dict[str, Any]:
    """The user approved a payment an agent held back."""
    from . import automation
    return automation.approve(body.kind, body.ref)


_ai_status: dict[str, Any] = {}


@router.get("/ai/status")
def ai_status() -> dict[str, Any]:
    """Make a tiny LLM call so the UI shows whether the AI really answers."""
    import time
    llm = llm_mod.get_llm()
    if llm is None:
        return {"ok": True, "provider": "planner", "model": None,
                "detail": "AI Guard runs on the built-in planner with PayPal Agent Toolkit and MCP server tools."}
    if _ai_status.get("at", 0) > time.time() - 300 and _ai_status.get("model") == llm.model:
        return _ai_status["result"]
    try:
        reply = llm.chat([{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=10, temperature=0)
        result = {"ok": bool((reply.get("content") or "").strip()), "provider": llm.provider, "model": llm.model,
                  "detail": "AI is answering."}
    except llm_mod.LLMError as exc:
        result = {"ok": False, "provider": llm.provider, "model": llm.model, "detail": str(exc)[:300]}
    _ai_status.update(at=time.time(), model=llm.model, result=result)
    return result


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    event_id = event.get("id") or ""
    if event_id and db.webhook_seen(event_id):
        return {"ok": True, "duplicate": True}
    verified = await run_in_threadpool(pp.verify_webhook, dict(request.headers), raw)
    db.record_webhook(event_id or db.new_id("evt_"), event.get("event_type", ""), verified, event)
    if not verified:
        raise HTTPException(400, "Webhook signature verification failed")
    outcome = await run_in_threadpool(ledger.handle_webhook_event, event)
    log.info("webhook %s %s: %s", event.get("event_type"), event_id, outcome)
    return {"ok": True, "result": outcome}


@router.get("/webhooks/recent")
def webhooks_recent() -> list[dict[str, Any]]:
    return db.recent_webhooks()


@router.get("/studio/data")
def studio_data() -> dict[str, Any]:
    from . import studio
    return studio.build_tables()


@router.post("/ai/responses")
async def studio_ai_responses(request: Request):
    """Proxy for the Studio AI assistant, so API keys stay on the server."""
    import httpx
    from fastapi.responses import JSONResponse, StreamingResponse

    from . import studio
    cfg = studio.studio_ai_settings()
    if not cfg["enabled"]:
        return JSONResponse({"error": {"message": "Studio AI is off: set STUDIO_OPENAI_API_KEY to enable it."}}, 503)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": {"message": "Request body must be JSON."}}, 400)
    if cfg["mode"] == "llm":
        # no OpenAI key: use the main LLM
        from .ai import responses_bridge
        from .ai.llm import LLMError, get_llm
        try:
            result = await run_in_threadpool(responses_bridge.run, get_llm(), body)
        except LLMError as exc:
            return JSONResponse({"error": {"message": str(exc)[:400]}}, 502)
        if not body.get("stream"):
            return JSONResponse(result)
        return StreamingResponse(responses_bridge.sse(result), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})
    model = body.get("model") or cfg["models"][0]
    if model not in cfg["models"]:
        return JSONResponse({"error": {"message": f'Model "{model}" is not enabled.'}}, 400)
    body.update(model=model, max_output_tokens=min(int(body.get("max_output_tokens") or 16000), 16000))

    client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
    upstream_req = client.build_request(
        "POST", f"{cfg['base_url']}/responses", json=body,
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
    )
    upstream = await client.send(upstream_req, stream=True)

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "application/json"),
                             headers={"Cache-Control": "no-store"})
