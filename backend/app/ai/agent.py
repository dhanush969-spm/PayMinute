"""AI Guard assistant: LLM tool calling over PayMinute and PayPal Agent Toolkit tools."""
from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .. import config, db, ledger
from ..paypal import service as pp
from ..paypal.client import PayPalError
from . import intent as intent_mod
from . import categorize, phone as phone_ai, insights, risk, status as status_ai
from .llm import LLMError, get_llm
from ..integrations import mcp_bridge

log = logging.getLogger("payminute.agent")
# the signed-in PayPal user for the current request (set by the API)
CURRENT_USER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("current_user", default=None)
MAX_STEPS = 6


def system_prompt(user_name: str | None = None) -> str:
    s = config.settings
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    who = f"{user_name}, who is signed in with PayPal" if user_name else "the signed-in PayPal user"
    return f"""You are PayMinute AI Guard, an AI payment guardian inside a PayPal-powered money app for {who}
({s.business_name}). Today is {today}. Currency: {s.currency}. Environment: PayPal sandbox.

You can read the user's payments, explain pending or stuck payments, analyse spending and budgets, check payments
for fraud/scam risk, create and send PayPal invoices, and send money via PayPal Payouts.

Rules:
- Use tools for every fact about the user's money; never invent amounts, statuses or IDs.
- People are paid by phone number (PayPal Payouts, recipient type PHONE) or by PayPal email. When the receiver is a
  phone number, ALWAYS call verify_phone first and tell the user what PayPal knows about the number (verified by
  PayPal / linked / not linked yet), the line type and country. Only name the owner if verify_phone returns a name.
  If verify_phone says payable=false, do NOT call send_money: explain the number must be linked to a PayPal account
  first (its owner logs in to PayMinute with PayPal once) or ask for their PayPal email.
- Before sending money ALWAYS call check_payment_risk first and mention the risk level in your reply.
- Actions that move money or email someone are queued for the user's approval; tell the user to review the
  approval card. Never claim money was sent until a tool result says it was executed.
- To bill someone: call create_invoice (currency_code "{s.currency}", primary_recipients=[{{"billing_info":
  {{"email_address": ...}}}}], items=[{{"name", "quantity": "1", "unit_amount": {{"currency_code", "value"}}}}],
  invoicer_business_name "{s.business_name}"), read the invoice id from the returned href, then call send_invoice.
- Tools whose names start with zapier__ (or another server prefix) come from the user's MCP servers and act in
  their other apps (email, Slack, sheets). Use them only when asked; they wait for approval like payments.
- Be concise: short paragraphs or bullets, amounts with 2 decimals. No investment, tax or legal advice.
"""


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


def _tx_brief(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t.get(k) for k in ("id", "kind", "direction", "amount", "currency", "counterparty", "counterparty_email",
                                  "memo", "category", "status", "status_reason", "risk_level", "created_at", "source")}


def t_overview(_: dict) -> Any:
    hist = ledger.history()
    s = insights.month_summary(hist)
    return {"this_month": {k: s[k] for k in ("month", "money_in", "money_out", "net", "prev_money_in", "prev_money_out")},
            "forecast_month_end": insights.forecast_month(hist),
            "payments_in_progress": len(ledger.in_progress_with_explanations())}


def t_recent(args: dict) -> Any:
    rows = db.list_tx(limit=int(args.get("limit") or 10), direction=args.get("direction"),
                      category=args.get("category"), search=args.get("search"))
    return [_tx_brief(t) for t in rows]


def t_spending(_: dict) -> Any:
    s = insights.month_summary(ledger.history())
    return {"month": s["month"], "money_out": s["money_out"], "by_category": s["by_category"]}


def t_budgets(_: dict) -> Any:
    hist = ledger.history()
    return {"budgets": insights.budget_status(hist), "suggested_if_missing": insights.suggested_budgets(hist)}


def t_set_budget(args: dict) -> Any:
    cat = args["category"]
    if cat not in categorize.EXPENSE_CATEGORIES:
        return {"error": f"Unknown category. Use one of: {', '.join(categorize.EXPENSE_CATEGORIES)}"}
    db.set_budget(cat, float(args["monthly_limit"]))
    return {"ok": True, "category": cat, "monthly_limit": float(args["monthly_limit"])}


def t_in_progress(_: dict) -> Any:
    ledger.refresh_in_progress()
    return [{"transaction": _tx_brief(i["transaction"]), "insight": i["insight"]}
            for i in ledger.in_progress_with_explanations()]


def t_explain(args: dict) -> Any:
    tx = db.visible_tx(args.get("transaction_id", "")) if args.get("transaction_id") else None
    if not tx and args.get("search"):
        found = db.list_tx(limit=1, search=args["search"])
        tx = found[0] if found else None
    if not tx:
        return {"error": "Transaction not found"}
    if tx.get("mine", True):  # refresh your own payments; one sent to you is shown from your side
        ledger.refresh(db.get_tx(tx["id"]))  # type: ignore[arg-type]
    tx = db.visible_tx(tx["id"]) or tx
    hist = ledger.history()
    return {"transaction": _tx_brief(tx), "status_insight": status_ai.explain(tx, hist),
            "risk_reasons": tx.get("risk_reasons"), "history": db.status_history(tx["id"])}


def _receiver(args: dict) -> str:
    return str(args.get("receiver") or args.get("receiver_email") or args.get("phone") or "").strip()


def t_check_risk(args: dict) -> Any:
    a = ledger.assess_payout(_receiver(args), float(args["amount"]), args.get("note"), args.get("name"))
    return {k: a.get(k) for k in ("score", "level", "reasons", "duplicates", "recommendation", "model", "recipient")}


def t_verify_phone(args: dict) -> Any:
    r = phone_ai.analyze(_receiver(args), ledger.history(), args.get("amount"))
    return {k: r.get(k) for k in ("input", "valid", "payable", "display", "e164", "country", "place", "line_type", "network",
                                  "paypal", "signals", "level", "verdict")}


def t_recommendations(_: dict) -> Any:
    return insights.rule_recommendations(ledger.history())


def t_recurring(_: dict) -> Any:
    return insights.detect_recurring(ledger.history())


def t_sync(_: dict) -> Any:
    return ledger.sync_from_paypal()


def exec_send_money(args: dict) -> Any:
    res = ledger.send_payout(_receiver(args), float(args["amount"]), args.get("note"), args.get("name"),
                             client_ref=args["_action_id"], acknowledge_risk=True, source="agent")
    tx = res["transaction"]
    return {"executed": True, "transaction": _tx_brief(tx), "status_insight": status_ai.explain(tx)}


def _complete_job(args: dict) -> Any:
    from .. import automation
    job = next((j for j in automation.jobs() if j["id"] == args.get("job_id")), None) or automation.find_job(args.get("search") or "")
    if not job:
        return {"error": "No matching open job"}
    return automation.complete_job(job["id"])


def _schedule(args: dict) -> Any:
    from .. import automation
    start = datetime.fromisoformat(args["start"]) if args.get("start") else None
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return automation.create_schedule(args["payee"], float(args["amount"]), args["every"], start, args.get("note"))


def exec_agent_payment(args: dict) -> Any:
    from .. import automation
    # payee and amount come from the stored job / schedule / deal, never from the tool arguments
    res = automation.approve(args.get("kind") or "deal", str(args.get("ref") or ""))
    tx = res.get("transaction") or {}
    return {"executed": res["outcome"] == "paid", "summary": res["summary"], "transaction": _tx_brief(tx) if tx else None}


def exec_cancel_unclaimed(args: dict) -> Any:
    mine = db.visible_tx(args.get("transaction_id", ""))
    if not mine or mine.get("mine") is False:
        raise ledger.FlowError("You can only cancel your own payouts.", 403)
    tx = ledger.cancel_unclaimed(args["transaction_id"])
    return {"executed": True, "transaction": _tx_brief(tx)}


PAYMINUTE_TOOLS: dict[str, dict[str, Any]] = {
    "get_money_overview": dict(
        description="This month's money in/out/net, last month, month-end forecast and count of payments in progress.",
        parameters=_obj({}), handler=t_overview),
    "get_recent_transactions": dict(
        description="List recent payments from PayMinute's ledger (payouts, customer payments, synced PayPal activity).",
        parameters=_obj({"limit": {"type": "integer", "description": "max rows, default 10"},
                         "direction": {"type": "string", "enum": ["in", "out"]},
                         "category": {"type": "string"},
                         "search": {"type": "string", "description": "name, email or note text"}}),
        handler=t_recent),
    "get_spending_by_category": dict(
        description="This month's spending grouped by category, with last month's amount for comparison.",
        parameters=_obj({}), handler=t_spending),
    "get_budget_status": dict(
        description="Budgets with spent so far, month-end forecast and state (ok/at_risk/over); plus suggested budgets.",
        parameters=_obj({}), handler=t_budgets),
    "set_budget": dict(
        description="Create or update a monthly budget for an expense category (0 removes it).",
        parameters=_obj({"category": {"type": "string", "enum": categorize.EXPENSE_CATEGORIES},
                         "monthly_limit": {"type": "number"}}, ["category", "monthly_limit"]),
        handler=t_set_budget),
    "get_payments_in_progress": dict(
        description="Payments that are pending, unclaimed, on hold or held for review, each with a plain-language "
                    "explanation, expected resolution window and next steps. Refreshes status from PayPal first.",
        parameters=_obj({}), handler=t_in_progress),
    "explain_payment": dict(
        description="Explain one payment's status, risk reasons and status history. Pass transaction_id or search.",
        parameters=_obj({"transaction_id": {"type": "string"}, "search": {"type": "string"}}), handler=t_explain),
    "verify_phone": dict(
        description="AI Guard phone check before paying a phone number: validity, country, line type (mobile, VoIP, "
                    "premium…), what PayPal reported for earlier payouts to it (verified / no PayPal account) and a risk level.",
        parameters=_obj({"receiver": {"type": "string", "description": "phone number, ideally with country code"},
                         "amount": {"type": "number"}}, ["receiver"]),
        handler=t_verify_phone),
    "check_payment_risk": dict(
        description="Score an outgoing payment for fraud/scam/duplicate risk BEFORE sending. Returns level and reasons.",
        parameters=_obj({"receiver": {"type": "string", "description": "phone number (+15555550123) or PayPal email"},
                         "amount": {"type": "number"},
                         "note": {"type": "string"}, "name": {"type": "string"}}, ["receiver", "amount"]),
        handler=t_check_risk),
    "get_recommendations": dict(
        description="Personalised budgeting and cash-flow recommendations computed from the user's data.",
        parameters=_obj({}), handler=t_recommendations),
    "find_recurring_payments": dict(
        description="Detect subscriptions and other recurring payments with their monthly cost.",
        parameters=_obj({}), handler=t_recurring),
    "sync_paypal_transactions": dict(
        description="Import the last 31 days of PayPal account activity via the Transaction Search API.",
        parameters=_obj({}), handler=t_sync),
    "send_money": dict(
        description="Send money to a phone number or PayPal email via PayPal Payouts. REQUIRES USER APPROVAL: this "
                    "queues an approval card.",
        parameters=_obj({"receiver": {"type": "string", "description": "phone number (+15555550123) or PayPal email"},
                         "amount": {"type": "number"},
                         "note": {"type": "string", "description": "message shown to the receiver"},
                         "name": {"type": "string", "description": "receiver's name if known"}},
                        ["receiver", "amount"]),
        handler=exec_send_money, approval=True),
    "create_job": dict(
        description="Create a job for a contractor. When it's marked complete, PayMinute pays them automatically "
                    "(within the user's agent limits and only if the risk check passes).",
        parameters=_obj({"title": {"type": "string"}, "payee": {"type": "string", "description": "phone or PayPal email"},
                         "amount": {"type": "number"}, "payee_name": {"type": "string"}}, ["title", "payee", "amount"]),
        handler=lambda a: __import__("app.automation", fromlist=["x"]).create_job(a["title"], a["payee"], float(a["amount"]), a.get("payee_name"))),
    "complete_job": dict(
        description="Mark a job complete; the job agent checks the payee and pays through PayPal Payouts.",
        parameters=_obj({"job_id": {"type": "string"}, "search": {"type": "string", "description": "words from the job title"}}),
        handler=lambda a: _complete_job(a)),
    "schedule_payment": dict(
        description="Schedule a payment: once, daily, weekly or monthly. Each run is risk-checked and limited.",
        parameters=_obj({"payee": {"type": "string"}, "amount": {"type": "number"},
                         "every": {"type": "string", "enum": ["once", "daily", "weekly", "monthly"]},
                         "start": {"type": "string", "description": "ISO date-time of the first run; empty = now"},
                         "note": {"type": "string"}}, ["payee", "amount", "every"]),
        handler=lambda a: _schedule(a)),
    "negotiate_and_pay": dict(
        description="Buyer agent negotiates with the seller's agent within the budget and pays the agreed price via PayPal.",
        parameters=_obj({"item": {"type": "string"}, "seller": {"type": "string", "description": "seller phone or PayPal email"},
                         "budget": {"type": "number"}}, ["item", "seller", "budget"]),
        handler=lambda a: __import__("app.automation", fromlist=["x"]).negotiate(a["item"], a["seller"], float(a["budget"]))),
    "shop_for_me": dict(
        description="Shopping agent: compare PayMinute market listings (features, price, seller trust), bargain with the "
                    "seller's agent within the budget and return a locked checkout quote for the user to pay.",
        parameters=_obj({"query": {"type": "string"}, "budget": {"type": "number"}}, ["query", "budget"]),
        handler=lambda a: __import__("app.market", fromlist=["x"]).shop(a["query"], float(a["budget"]),
                                                                        (CURRENT_USER.get() or {}).get("account"))),
    "pay_checkout": dict(
        description="Pay a locked checkout quote from the shopping agent (seller and price are fixed on the server). "
                    "REQUIRES USER APPROVAL.",
        parameters=_obj({"quote_id": {"type": "string"}, "title": {"type": "string"}, "price": {"type": "number"}}, ["quote_id"]),
        handler=lambda a: __import__("app.market", fromlist=["x"]).pay_quote(
            a["quote_id"], (CURRENT_USER.get() or {}).get("account") or (CURRENT_USER.get() or {}).get("payer_id"),
            acknowledge_risk=True), approval=True),
    "approve_agent_payment": dict(
        description="Pay something one of PayMinute's agents held back (job, schedule or negotiated deal). REQUIRES USER APPROVAL.",
        parameters=_obj({"kind": {"type": "string", "enum": ["job", "schedule", "deal"]}, "ref": {"type": "string"},
                         "payee": {"type": "string"}, "amount": {"type": "number"}, "note": {"type": "string"},
                         "name": {"type": "string"}}, ["kind", "ref"]),
        handler=lambda a: exec_agent_payment(a), approval=True),
    "cancel_unclaimed_payout": dict(
        description="Cancel an UNCLAIMED payout so the money returns immediately. REQUIRES USER APPROVAL.",
        parameters=_obj({"transaction_id": {"type": "string"}}, ["transaction_id"]),
        handler=exec_cancel_unclaimed, approval=True),
}

# Toolkit tools that contact customers also need approval.
TOOLKIT_APPROVAL = {"send_invoice", "send_invoice_reminder", "cancel_sent_invoice"}


def _mcp_specs() -> list[dict[str, Any]]:
    try:
        return mcp_bridge.all_tools()
    except Exception as exc:  # an unreachable MCP server is skipped
        log.warning("MCP tools unavailable: %s", exc)
        return []


def tool_specs() -> list[dict[str, Any]]:
    specs = [{"name": n, "description": t["description"], "parameters": t["parameters"], "source": "payminute"}
             for n, t in PAYMINUTE_TOOLS.items()]
    mcp_specs = _mcp_specs()
    mcp_names = {m["name"] for m in mcp_specs}
    try:
        # PayPal MCP tools first, then any Agent Toolkit tools it doesn't cover.
        specs += [t for t in pp.get_toolkit().specs() if t["name"] not in mcp_names]
    except Exception as exc:  # toolkit unavailable: keep our own tools
        log.error("PayPal Agent Toolkit unavailable: %s", exc)
    specs += [{k: m[k] for k in ("name", "description", "parameters", "source")} for m in mcp_specs
              if m["name"] not in PAYMINUTE_TOOLS]
    return specs


def _mcp_spec(name: str) -> dict[str, Any] | None:
    if name in PAYMINUTE_TOOLS:
        return None
    return next((m for m in _mcp_specs() if m["name"] == name), None)


def tool_source(name: str) -> str:
    if name in PAYMINUTE_TOOLS:
        return "payminute"
    spec = _mcp_spec(name)
    return spec["source"] if spec else "paypal-agent-toolkit"


def needs_approval(name: str) -> bool:
    if name in PAYMINUTE_TOOLS:
        return bool(PAYMINUTE_TOOLS[name].get("approval"))
    if name in TOOLKIT_APPROVAL:
        return True
    spec = _mcp_spec(name)
    return bool(spec and spec["approval"])


def _invoice_id_from(result: Any) -> str | None:
    text = json.dumps(result) if not isinstance(result, str) else result
    m = re.search(r"(INV2-[A-Z0-9-]+)", text)
    return m.group(1) if m else None


def _record_invoice(args: dict, result: Any) -> None:
    inv_id = _invoice_id_from(result)
    if not inv_id or db.find_tx(paypal_invoice_id=inv_id):
        return
    try:
        rec = (args.get("primary_recipients") or [{}])[0].get("billing_info") or {}
        email = rec.get("email_address")
        name = rec.get("business_name") or " ".join(
            x for x in [(rec.get("name") or {}).get("given_name"), (rec.get("name") or {}).get("surname")] if x) or email
        total = sum(float(i["unit_amount"]["value"]) * float(i.get("quantity", 1)) for i in args.get("items", []))
        memo = ", ".join(i.get("name", "") for i in args.get("items", []))[:200]
    except (KeyError, TypeError, ValueError):
        return
    category, src = categorize.categorize_one("in", name, email, memo)
    db.insert_tx({"kind": "invoice", "direction": "in", "amount": round(total, 2), "counterparty": name,
                  "counterparty_email": (email or "").lower() or None, "memo": memo, "category": category,
                  "category_source": src, "status": "DRAFT", "paypal_invoice_id": inv_id, "source": "agent"})


# PayPal tools work on the app's business account, which every PayMinute user shares. For a signed-in user
# only these run, and only on that user's own invoices, orders and payments.
OWN_REF_TOOLS = {"get_invoice", "send_invoice", "send_invoice_reminder", "cancel_sent_invoice", "generate_invoice_qr_code",
                 "get_order"}
USER_SAFE_TOOLS = OWN_REF_TOOLS | {"create_invoice", "list_invoices", "create_order", "list_transactions",
                                   "list_disputes", "get_dispute"}


def _own_paypal_ids() -> set[str]:
    own = [t for t in db.list_tx(limit=2000) if t.get("mine", True)]
    return {v for t in own for v in (t.get("paypal_invoice_id"), t.get("paypal_order_id"), t.get("paypal_capture_id"),
                                    t.get("paypal_transaction_id"), t.get("paypal_authorization_id")) if v}


def _guard_paypal_tool(base: str, args: dict[str, Any]) -> None:
    if not db.current_account():
        return
    if base not in USER_SAFE_TOOLS:
        raise ledger.FlowError(f"“{base}” reads PayPal data for the whole business account, so PayMinute doesn't run it "
                               "for individual users.", 403)
    if base in OWN_REF_TOOLS:
        ref = args.get("invoice_id") or args.get("order_id") or args.get("id")
        if not ref or ref not in _own_paypal_ids():
            raise ledger.FlowError("That isn't one of your PayPal invoices or orders.", 403)


def _only_mine(base: str, result: Any) -> Any:
    """Drop other users' records from PayPal list results."""
    if not db.current_account() or base not in {"list_invoices", "list_transactions", "list_disputes", "get_dispute"}:
        return result
    was_text = isinstance(result, str)
    try:
        data = json.loads(result) if was_text else result
    except ValueError:
        return result
    if not isinstance(data, dict):
        return result
    ids = _own_paypal_ids()

    def disputed(d: dict) -> bool:
        return any((x or {}).get("seller_transaction_id") in ids for x in d.get("disputed_transactions") or [])
    if base == "get_dispute" and not disputed(data):
        raise ledger.FlowError("That dispute isn't about one of your payments.", 403)
    if base == "list_invoices":
        data = {**data, "items": [i for i in data.get("items") or [] if i.get("id") in ids]}
    elif base == "list_transactions":
        data = {**data, "transaction_details": [t for t in data.get("transaction_details") or []
                                                if (t.get("transaction_info") or {}).get("transaction_id") in ids]}
    elif base == "list_disputes":
        data = {**data, "items": [d for d in data.get("items") or [] if disputed(d)]}
    return json.dumps(data) if was_text else data


def run_tool(name: str, args: dict[str, Any]) -> Any:
    if name in PAYMINUTE_TOOLS:
        return PAYMINUTE_TOOLS[name]["handler"](args)
    clean = {k: v for k, v in args.items() if not k.startswith("_")}
    spec = _mcp_spec(name)
    base = spec["remote_name"] if spec else name
    _guard_paypal_tool(base, clean)
    if spec:
        result = _only_mine(base, mcp_bridge.call(name, clean))
    else:
        result = _only_mine(base, pp.get_toolkit().run(name, clean))
    if name == "create_invoice":
        _record_invoice(clean, result)
    elif name == "send_invoice" and clean.get("invoice_id"):
        tx = db.find_tx(paypal_invoice_id=clean["invoice_id"])
        if tx:
            db.update_tx(tx["id"], status="SENT", _source="paypal")
    elif name == "cancel_sent_invoice" and clean.get("invoice_id"):
        tx = db.find_tx(paypal_invoice_id=clean["invoice_id"])
        if tx:
            db.update_tx(tx["id"], status="CANCELLED", _source="paypal")
    return result


def summarize_action(name: str, args: dict[str, Any]) -> tuple[str, dict | None]:
    cur = config.settings.currency
    if name == "send_money":
        a = ledger.assess_payout(_receiver(args), float(args.get("amount") or 0), args.get("note"),
                                 args.get("name"))
        rec = a.get("recipient") or {}
        who = args.get("name") or (f"{rec['name']} ({rec['display']})" if rec.get("name") else rec.get("display")) or _receiver(args)
        return (f"Send {float(args.get('amount') or 0):,.2f} {cur} to {who} via PayPal Payouts"
                + (f" — “{args['note']}”" if args.get("note") else ""),
                {k: a[k] for k in ("score", "level", "reasons", "recommendation")})
    if name == "pay_checkout":
        return f"Buy “{args.get('title') or 'the item'}” for {float(args.get('price') or 0):,.2f} {cur} through PayPal", None
    if name == "approve_agent_payment":
        return (f"Pay {float(args.get('amount') or 0):,.2f} {cur} to {args.get('name') or args.get('payee')} "
                f"({args.get('kind', 'agent')} payment held for your approval)", None)
    if name == "cancel_unclaimed_payout":
        tx = db.visible_tx(args.get("transaction_id", ""))
        return (f"Cancel unclaimed payout of {tx['amount']:,.2f} {cur} to {tx['counterparty']} and return the money"
                if tx else "Cancel unclaimed payout", None)
    if name == "send_invoice":
        tx = db.find_tx(paypal_invoice_id=args.get("invoice_id", ""))
        if tx:
            return f"Email invoice {args.get('invoice_id')} for {tx['amount']:,.2f} {cur} to {tx['counterparty']}", None
        return f"Email PayPal invoice {args.get('invoice_id')} to the recipient", None
    if name == "send_invoice_reminder":
        return f"Send a payment reminder for invoice {args.get('invoice_id')}", None
    if name == "cancel_sent_invoice":
        return f"Cancel sent invoice {args.get('invoice_id')}", None
    spec = _mcp_spec(name)
    if spec:
        shown = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(args.items())[:4])
        return f"Run {spec['remote_name']} on the {spec['server']} MCP server ({shown})", None
    return f"Run {name}", None


def _short(result: Any, limit: int = 6000) -> str:
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    return text if len(text) <= limit else text[:limit] + "…(truncated)"


def execute_call(session_id: str, call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Run or queue one tool call. Returns (tool_message_content, step_info)."""
    name, args = call["name"], call.get("arguments") or {}
    step = {"tool": name, "source": tool_source(name), "args": args}
    if name not in PAYMINUTE_TOOLS and name not in {s["name"] for s in tool_specs()}:
        step.update(ok=False, error="unknown tool")
        return json.dumps({"error": f"Unknown tool {name}"}), step
    if needs_approval(name):
        summary, risk_info = summarize_action(name, args)
        action = db.create_action(session_id, name, args, summary, risk_info)
        step.update(ok=True, queued=True, action=action)
        return json.dumps({"status": "awaiting_user_approval", "action_id": action["id"], "summary": summary,
                           "risk": risk_info, "note": "Not executed yet. Ask the user to approve the card."}), step
    try:
        result = run_tool(name, args)
        step.update(ok=True)
        return _short(result), step
    except (PayPalError, ledger.FlowError) as exc:
        step.update(ok=False, error=str(exc))
        return json.dumps({"error": str(exc)}), step
    except Exception as exc:  # e.g. toolkit validation errors
        log.exception("tool %s failed", name)
        step.update(ok=False, error=str(exc)[:300])
        return json.dumps({"error": str(exc)[:1500]}), step


def _history_for_llm(session_id: str) -> list[dict[str, Any]]:
    msgs = db.get_agent_messages(session_id, limit=30)
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def run_turn(session_id: str, text: str, user_name: str | None = None) -> dict[str, Any]:
    db.add_agent_message(session_id, {"role": "user", "content": text})
    llm = get_llm()
    if llm is None:
        return offline_turn(session_id, text)
    specs = tool_specs()
    steps: list[dict[str, Any]] = []
    messages = _history_for_llm(session_id)
    reply = ""
    try:
        for _ in range(MAX_STEPS):
            res = llm.chat(messages, tools=specs, system=system_prompt(user_name))
            if not res["tool_calls"]:
                reply = res["content"].strip()
                break
            assistant = {"role": "assistant", "content": res["content"], "tool_calls": res["tool_calls"]}
            messages.append(assistant)
            db.add_agent_message(session_id, assistant)
            for call in res["tool_calls"]:
                content, step = execute_call(session_id, call)
                steps.append(step)
                tool_msg = {"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": content}
                messages.append(tool_msg)
                db.add_agent_message(session_id, tool_msg)
        else:
            reply = "I ran out of steps for this request. Could you narrow it down?"
    except LLMError as exc:
        log.error("LLM error: %s", exc)
        if not steps:
            # model down: answer with the built-in planner instead of an error
            out = offline_turn(session_id, text)
            out["reply"] = "_The AI model is busy right now, so I used PayMinute's built-in planner._\n\n" + out["reply"]
            return out
        reply = "The AI model stopped responding partway through. Anything waiting for approval is listed below."
    if not reply:
        reply = "Done."
    db.add_agent_message(session_id, {"role": "assistant", "content": reply})
    return {"reply": reply, "steps": steps, "pending_actions": [s["action"] for s in steps if s.get("queued")],
            "mode": "llm"}


def decide_action(action_id: str, approve: bool) -> dict[str, Any]:
    action = db.get_action(action_id)
    if not action:
        raise ledger.FlowError("Action not found", 404)
    if action["status"] != "pending":
        return {"action": action, "reply": f"This action was already {action['status']}."}
    if not approve:
        action = db.update_action(action_id, "rejected")
        reply = f"Cancelled: {action['summary']}. Nothing was sent."
        db.add_agent_message(action["session_id"], {"role": "assistant", "content": reply})
        return {"action": action, "reply": reply}
    try:
        result = run_tool(action["tool"], {**action["args"], "_action_id": action_id})
        action = db.update_action(action_id, "executed", result)
        reply = _executed_reply(action, result)
    except (PayPalError, ledger.FlowError, Exception) as exc:  # noqa: BLE001
        action = db.update_action(action_id, "failed", {"error": str(exc)})
        reply = f"That didn't go through: {exc}"
    db.add_agent_message(action["session_id"], {"role": "assistant", "content": f"[Approved action] {reply}"})
    return {"action": action, "reply": reply}


def _executed_reply(action: dict[str, Any], result: Any) -> str:
    if action["tool"] == "send_money" and isinstance(result, dict):
        tx = result.get("transaction") or {}
        insight = result.get("status_insight") or {}
        line = f"Sent {tx.get('amount', 0):,.2f} {tx.get('currency', '')} to {tx.get('counterparty')}. PayPal status: {tx.get('status')}."
        if insight.get("state") in {"in_progress", "overdue"}:
            line += f" {insight.get('title')}: {insight.get('explain')} {insight.get('estimate') or ''}"
        return line.strip()
    if action["tool"] in {"approve_agent_payment", "pay_checkout"} and isinstance(result, dict):
        return result.get("summary") or "Done."
    if action["tool"] == "send_invoice":
        link = _first_href(result)
        return f"Invoice sent. The customer can pay at {link}." if link else "Invoice sent."
    return f"Done: {action['summary']}."


def _first_href(result: Any) -> str | None:
    if isinstance(result, dict):
        return result.get("href")
    return None


def _phone_line(v: dict[str, Any]) -> str:
    if not v.get("valid"):
        why = "; ".join(x["detail"] for x in v.get("signals", [])) or "It isn't a dialable number."
        return f"**Phone check — {v.get('input')}:** not a valid phone number. {why} {v.get('verdict')}"
    pp = v.get("paypal") or {}
    flags = "\n".join(f"- **{x['label']}**: {x['detail']}" for x in v.get("signals", []))
    return (f"**Phone check — {v.get('display')}** ({v.get('place')}, {v.get('line_type')}"
            f"{', ' + v['network'] if v.get('network') else ''})\n- **PayPal:** {pp.get('label')}"
            f"{' — ' + pp['name'] if pp.get('name') else ''}. {pp.get('detail')}"
            + (f"\n{flags}" if flags else "") + f"\n\nAI Guard: **{v.get('level', '?').upper()}**. {v.get('verdict')}")

AMOUNT_RE = re.compile(r"(?:\$|usd\s*|₹|rs\.?\s*)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# e.g. +1 408 555 0134 or (408) 555-0134
PHONE_RE = re.compile(r"(?<![\w$₹.])(\+\d[\d\s().-]{6,18}\d|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?![\w.])")


SAFETY_Q = re.compile(r"\b(safe|risk|risky|scam|legit|fraud|check|verify|valid|who)\b")
SEND_INTENT = re.compile(r"\b(send|pay|transfer)\b")


def _amount(text: str) -> float | None:
    cleaned = PHONE_RE.sub(" ", EMAIL_RE.sub(" ", text))
    for m in AMOUNT_RE.finditer(cleaned):
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            continue
    return None


HINTS = {"stuck": "stuck", "spending": "spending", "budget": "budget", "recurring": "recurring", "tips": "tips",
         "invoice": "invoice", "disputes": "dispute", "sync": "sync"}
JOB_WORDS = re.compile(r"\b(job|task|gig)s?\b", re.I)
DONE_WORDS = re.compile(r"\b(done|complete|completed|finished|delivered)\b", re.I)
SHOP_WORDS = re.compile(r"\b(buy|shop|purchase|order|check ?out)\b", re.I)
SELL_WORDS = re.compile(r"\b(sell|selling|for sale|list my|listing)\b", re.I)
SPLIT_WORDS = re.compile(r"\b(split|halves|share the|divide)\b", re.I)
TITLE_NOISE = re.compile(r"\b(buy|shop|for|purchase|order|check ?out|find|get|me|a|an|the|my|some|under|below|less|than|"
                         r"at|most|max|budget|of|sell|selling|sale|list|listing|put|on|market|create|i|want|to|need|please|"
                         r"and|it|old|used|price|cheap|good|best|with|from|dollars?|bucks)\b", re.I)
DEAL_WORDS = re.compile(r"\b(negotiat\w*|haggle|bargain|best price|deal)\b", re.I)


def _contacts() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    with db.connect() as conn:
        for r in conn.execute("SELECT phone, name, email FROM paypal_directory").fetchall():
            seen[r["phone"]] = {"name": r["name"], "phone": r["phone"], "email": r["email"]}
    for t in ledger.history():
        if t.get("direction") == "out" and t.get("counterparty") and (t.get("counterparty_phone") or t.get("counterparty_email")):
            key = t.get("counterparty_phone") or t["counterparty_email"]
            seen.setdefault(key, {"name": t["counterparty"], "phone": t.get("counterparty_phone"), "email": t.get("counterparty_email")})
    # one entry per person: someone paid by phone once and by email once is still one contact
    people: dict[str, dict[str, Any]] = {}
    for c in seen.values():
        p = people.setdefault((c.get("name") or c.get("email") or c.get("phone") or "").strip().lower(), dict(c))
        p["phone"] = p.get("phone") or c.get("phone")
        p["email"] = p.get("email") or c.get("email")
    return list(people.values())


def _strip_entities(text: str, *words: str) -> str:
    out = PHONE_RE.sub(" ", EMAIL_RE.sub(" ", text))
    out = re.sub(r"(?:\$|usd\s*|₹|rs\.?\s*)?\d+(?:,\d{3})*(?:\.\d{1,2})?", " ", out, flags=re.I)
    for w in words:
        if w:
            out = re.sub(re.escape(w), " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" .,:-")


def _next_weekday(day: int) -> datetime:
    now = datetime.now(timezone.utc)
    ahead = (day - now.weekday()) % 7 or 7
    return (now + timedelta(days=ahead)).replace(hour=9, minute=0, second=0, microsecond=0)


def offline_turn(session_id: str, text: str, _hinted: bool = False) -> dict[str, Any]:
    """AI Guard without an external LLM: local intent model + entity extraction, driving the same tools."""
    from .. import automation
    low = text.lower()
    email = (EMAIL_RE.search(text) or [None])[0] if EMAIL_RE.search(text) else None
    phone_m = PHONE_RE.search(EMAIL_RE.sub(" ", text))
    phone = phone_m.group(1).strip() if phone_m else None
    contact = None
    if not email and not phone:
        contact = intent_mod.contact_name(text, _contacts())
        if contact:
            phone, email = contact.get("phone"), None if contact.get("phone") else contact.get("email")
    receiver = email or phone
    navigate = None
    who_name = contact["name"] if contact else None
    amount = _amount(text)
    intent, confidence = intent_mod.classify(text)
    steps: list[dict[str, Any]] = []
    cur = config.settings.currency

    def call(name: str, args: dict[str, Any]) -> Any:
        content, step = execute_call(session_id, {"id": f"offline_{name}", "name": name, "arguments": args})
        steps.append(step)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content

    def queue_approval(kind: str, ref: str, payee: str, amt: float, note: str, name: str | None) -> None:
        call("approve_agent_payment", {"kind": kind, "ref": ref, "payee": payee, "amount": amt, "note": note, "name": name})

    user = CURRENT_USER.get() or {}
    if (SPLIT_WORDS.search(low) or intent == "split" and confidence > 0.45) and amount:
        people = intent_mod.contact_names(text, _contacts())
        for e in EMAIL_RE.findall(text):
            if not any((p.get("email") or "").lower() == e.lower() for p in people):
                people.append({"name": e, "email": e})
        if not people:
            reply = "Who should I split it with? Name people you've paid before, or give their PayPal emails."
        else:
            share = round(amount / (len(people) + 1), 2)
            what = _strip_entities(re.sub(r"\b(split|halves|share|divide|between|the|bill|with|me|and|go|on)\b", " ", text, flags=re.I),
                                   *[p.get("name") or "" for p in people]) or "Shared bill"
            sent, skipped = [], []
            for person in people:
                if not person.get("email"):
                    skipped.append(person["name"])
                    continue
                created = call("create_invoice", {
                    "currency_code": cur, "invoicer_business_name": config.settings.business_name,
                    "primary_recipients": [{"billing_info": {"email_address": person["email"]}}],
                    "items": [{"name": f"Your share: {what}"[:120], "quantity": "1",
                               "unit_amount": {"currency_code": cur, "value": f"{share:.2f}"}}]})
                inv = _invoice_id_from(created)
                if inv:
                    call("send_invoice", {"invoice_id": inv, "send_to_recipient": True})
                    sent.append(person["name"].split()[0])
            reply = (f"Split {amount:,.2f} {cur} {len(people) + 1} ways: {share:,.2f} each. "
                     + (f"I prepared PayPal invoices for {', '.join(sent)}; approve the cards to send them." if sent else "")
                     + (f" No PayPal email for {', '.join(skipped)}." if skipped else ""))
    elif (SELL_WORDS.search(low) or intent == "sell" and confidence > 0.5) and amount and not SHOP_WORDS.search(low):
        from .. import market
        payee = user.get("phone") or user.get("email")
        if not payee:
            reply = "Add your phone or PayPal email in the account menu first, so buyers can pay you."
        else:
            req = market.parse_request(text)
            title = req["title"] or re.sub(r"\s+", " ", TITLE_NOISE.sub(" ", _strip_entities(text))).strip(" .,:-").title() or "Item"
            price = req["price"] or amount
            item = market.create_listing(title, price, payee, user.get("name"), user.get("account") or user.get("payer_id"),
                                         req["details"], req["floor"])
            steps.append({"tool": "create_listing", "source": "payminute", "ok": True})
            hint = market.suggest_price(title, req["details"], exclude_id=item["id"])
            reply = (f"Listed **{item['title']}** for {price:,.2f} {cur}. Buyers' shopping agents can now find it; your seller "
                     f"agent bargains hard and won't accept less than {req['floor'] or price * 0.9:,.2f}."
                     + (f" AI price check: about {hint['price']:,.2f} ({hint['basis'].lower()})." if hint else ""))
    elif (SHOP_WORDS.search(low) or intent == "shop" and confidence > 0.45) and amount and not receiver:
        from .. import market
        query = re.sub(r"\s+", " ", TITLE_NOISE.sub(" ", _strip_entities(text))).strip(" .,:-")
        account = user.get("account") or user.get("payer_id")
        res = market.shop(text, amount, account) if account else \
            {"outcome": "login", "summary": "Log in with PayPal first so the agent can check out for you.", "candidates": []}
        steps.append({"tool": "shopping_agent", "source": "payminute", "ok": res["outcome"] not in {"failed"}})
        lines = []
        for c in res.get("candidates", [])[:3]:
            lines.append(f"- {c['title']} — {c['price']:,.2f} from {c['seller_name'] or 'a seller'} "
                         f"({c['trust']['label']}, score {round(c['score'] * 100)})")
        talk = "\n".join(f"- **{'Your agent' if m['agent'] == 'buyer' else 'Seller agent'}:** {m['text']}"
                          for m in res.get("transcript", []))
        reply = (f"I searched the PayMinute market for “{query}” (budget {amount:,.2f}):\n" + ("\n".join(lines) or "- nothing found")
                 + (f"\n\nNegotiation:\n{talk}" if talk else "") + f"\n\n{res['summary']}")
        if res["outcome"] == "quote":
            reply += f"\n\n{res['reason']}"
            navigate = f"/checkout/{res['quote']['id']}"
    elif JOB_WORDS.search(low) and DONE_WORDS.search(low) or intent == "job_complete" and confidence > 0.4:
        job = automation.find_job(text)
        if not job:
            open_jobs = [j for j in automation.jobs() if j["status"] == "open"]
            reply = ("Which job? Open jobs: " + ", ".join(f"“{j['title']}”" for j in open_jobs)) if open_jobs else \
                "You have no open jobs. Create one like: “Create job logo design for +1 201 555 0123 for $150”."
        else:
            res = automation.complete_job(job["id"])
            steps.append({"tool": "complete_job", "source": "payminute", "ok": res["outcome"] in {"paid", "needs_approval"}})
            reply = f"Job “{job['title']}” marked complete. {res['summary']}"
            if res["outcome"] == "needs_approval":
                queue_approval("job", job["id"], job["payee"], job["amount"], f"Job complete: {job['title']}", job["payee_name"])
                reply += " Approve the card below to pay anyway."
    elif (JOB_WORDS.search(low) and re.search(r"\b(create|add|new|make|set up|track)\b", low) or intent == "job_create" and confidence > 0.4) \
            and receiver and amount:
        title = _strip_entities(text, who_name or "", (who_name or "").split(" ")[0])
        title = re.sub(r"\b(create|add|new|make|set up|track|a|an|the|job|task|gig|for|to|paying|pay|of|called|and|when|done|it'?s)\b",
                       " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" .,:-") or "Job"
        job = automation.create_job(title[:80], receiver, amount, who_name)
        steps.append({"tool": "create_job", "source": "payminute", "ok": True})
        reply = (f"Created job **{job['title']}**: {amount:,.2f} {cur} to {who_name or receiver}. "
                 "When you mark it complete, I'll check the payee and pay them through PayPal automatically "
                 f"(up to {automation.settings()['auto_pay_max']:,.2f} without asking).")
    elif JOB_WORDS.search(low) and re.search(r"\b(show|list|which|open|unpaid|pending|my)\b", low) or intent == "jobs" and confidence > 0.5:
        js = automation.jobs()
        reply = ("Your jobs:\n" + "\n".join(f"- {j['title']} — {j['amount']:,.2f} {cur} to {j['payee_name'] or j['payee']} — {j['status']}"
                                              for j in js[:10])) if js else "No jobs yet."
    elif receiver and amount and (intent_mod.frequency(text)[0] or intent == "schedule" and confidence > 0.5):
        every, day = intent_mod.frequency(text)
        every = every or "once"
        start = _next_weekday(day) if day is not None else None
        note_m = re.search(r"\bfor\s+(.+)$", _strip_entities(text, who_name or ""), re.I)
        sched = automation.create_schedule(receiver, amount, every, start, note_m.group(1) if note_m else None, who_name)
        steps.append({"tool": "create_schedule", "source": "payminute", "ok": True})
        when = datetime.fromisoformat(sched["next_run"]).strftime("%a %d %b, %H:%M UTC") if start else "now"
        reply = (f"Scheduled {amount:,.2f} {cur} to {who_name or receiver}, {every if every != 'once' else 'one time'}, "
                 f"first payment {when}. Each run is checked like a manual payment and stays within your agent limits.")
    elif (DEAL_WORDS.search(low) or intent == "negotiate" and confidence > 0.45) and receiver and amount:
        item_m = re.search(r"\bfor\s+(?:an?\s+|the\s+)?(.+?)(?:\s+(?:with|from|under|budget|at most|max)\b|$)", _strip_entities(text, who_name or ""), re.I)
        item = (item_m.group(1) if item_m else "the item").strip(" .,") or "the item"
        deal = automation.negotiate(item[:60], receiver, amount, seller_name=who_name)
        steps.append({"tool": "agent_negotiation", "source": "payminute", "ok": deal["outcome"] != "failed"})
        lines = "\n".join(f"- **{'Your agent' if m['agent'] == 'buyer' else 'Seller agent'}:** {m['text']}" for m in deal["transcript"])
        reply = f"Your buyer agent negotiated with the seller's agent for {item} (budget {amount:,.2f}):\n{lines}\n\n{deal['summary']}"
        if deal["outcome"] == "needs_approval":
            queue_approval("deal", deal["deal_id"], receiver, deal["price"], f"Agreed by agents: {item}", who_name)
            reply += " Approve the card below to pay the agreed price."
    elif intent == "agents" and confidence > 0.4 or re.search(r"\b(agent|automation)s?\b.*\b(did|log|history|activity)\b", low):
        rs = automation.runs(10)
        reply = ("What your agents did:\n" + "\n".join(f"- {r['at'][:16].replace('T', ' ')} · {r['agent']} · {r['summary']}" for r in rs)
                 ) if rs else "Your agents haven't made any payments yet."
    elif email and amount and re.search(r"\b(invoice|bill|request|charge)\b", low):
        note = re.sub(EMAIL_RE, "", text)
        item = re.search(r"\bfor\s+(.+?)(?:\s+to\b|$)", note, re.I)
        item_name = (item.group(1).strip(" .") if item else "Services")[:120]
        created = call("create_invoice", {
            "currency_code": cur, "invoicer_business_name": config.settings.business_name,
            "primary_recipients": [{"billing_info": {"email_address": email}}],
            "items": [{"name": item_name, "quantity": "1", "unit_amount": {"currency_code": cur, "value": f"{amount:.2f}"}}],
        })
        inv = _invoice_id_from(created)
        if inv:
            call("send_invoice", {"invoice_id": inv, "send_to_recipient": True})
            reply = (f"I created PayPal invoice {inv} for {amount:,.2f} {cur} ({item_name}) to {email}. "
                     "Approve the card below to email it.")
        else:
            reply = f"PayPal didn't create the invoice: {created}"
    elif receiver and amount and SAFETY_Q.search(low) and not (phone and not email):
        r = call("check_payment_risk", {"receiver": receiver, "amount": amount})
        lines = "\n".join(f"- **{x['label']}**: {x['detail']}" for x in r.get("reasons", [])) or "- No warning signs."
        reply = (f"Risk level **{r.get('level', '?').upper()}** (score {r.get('score')}).\n{lines}\n\n{r.get('recommendation')}"
                 "\n\n_I haven't prepared any payment. Say “send …” if you want to go ahead._")
    elif phone and not email and (SAFETY_Q.search(low) or not (amount and SEND_INTENT.search(low))):
        v = call("verify_phone", {"receiver": phone, "amount": amount})
        reply = _phone_line(v)
        if amount and v.get("valid"):
            r = call("check_payment_risk", {"receiver": phone, "amount": amount})
            lines = "\n".join(f"- **{x['label']}**: {x['detail']}" for x in r.get("reasons", [])) or "- No warning signs."
            reply += f"\n\nPayment risk for {amount:,.2f} {cur}: **{r.get('level', '?').upper()}**\n{lines}\n\n{r.get('recommendation')}"
    elif receiver and amount and SEND_INTENT.search(low):
        note_m = re.search(r"\bfor\s+(.+)$", PHONE_RE.sub("", EMAIL_RE.sub("", text)), re.I)
        note = note_m.group(1).strip(" .") if note_m else None
        pre = ""
        if phone and not email:
            v = call("verify_phone", {"receiver": phone, "amount": amount})
            pre = _phone_line(v) + "\n\n"
            if not v.get("valid") or not v.get("payable"):
                reply = pre + ("I didn't prepare the payment. " + (
                    "Check the number and include the country code." if not v.get("valid") else
                    "PayPal can only deliver to this number once it's linked to a PayPal account: ask them to log in "
                    "to PayMinute with PayPal, or give me their PayPal email."))
                db.add_agent_message(session_id, {"role": "assistant", "content": reply})
                return {"reply": reply, "steps": steps, "pending_actions": [], "mode": "offline"}
        r = call("check_payment_risk", {"receiver": receiver, "amount": amount, "note": note})
        call("send_money", {"receiver": receiver, "amount": amount, "note": note})
        reasons = "; ".join(x["label"] for x in r.get("reasons", [])[:3]) or "no warning signs"
        rec = r.get("recipient") or {}
        shown = (f"{rec['name']} ({rec['display']})" if rec.get("name") else rec.get("display")) or receiver
        reply = (f"{pre}Risk check: **{r.get('level', '?').upper()}** ({reasons}). {r.get('recommendation', '')} "
                 f"I've prepared the payout of {amount:,.2f} {cur} to {shown}. Review and approve the card to send it.")
    elif amount and SEND_INTENT.search(low) and re.search(r"\d{3,}", PHONE_RE.sub(" ", text).replace(str(int(amount)), "", 1)):
        reply = ("I couldn't find a valid phone number or PayPal email to pay. Include the country code, "
                 "e.g. “Send $20 to +1 408 555 0134”.")
    elif re.search(r"\b(pending|stuck|where is|not received|didn'?t arrive|status|unclaimed|on hold|held)\b", low):
        items = call("get_payments_in_progress", {})
        if not items:
            reply = "Nothing is pending right now. All your payments have completed."
        else:
            parts = []
            for i in items[:5]:
                t, ins = i["transaction"], i["insight"]
                parts.append(f"- **{t['counterparty']}** {t['amount']:,.2f} {t['currency']} — {ins['title']}. "
                             f"{ins['explain']} {ins.get('estimate') or ''}")
            reply = "Here's what's still in progress:\n" + "\n".join(parts)
    elif re.search(r"\b(budgets?|over ?spend\w*)\b", low):
        b = call("get_budget_status", {})
        if b["budgets"]:
            reply = "Budgets this month:\n" + "\n".join(
                f"- {x['category']}: {x['spent']:,.2f} of {x['limit']:,.2f} (forecast {x['forecast']:,.2f}, {x['state'].replace('_', ' ')})"
                for x in b["budgets"])
        else:
            sug = b.get("suggested_if_missing") or {}
            reply = "You haven't set budgets yet." + (
                " Based on the last two months I'd suggest: " + ", ".join(f"{k} {v:,.0f}" for k, v in sug.items()) if sug else "")
    elif re.search(r"\b(recurring|subscription)", low):
        rec = call("find_recurring_payments", {})
        reply = ("Recurring payments I found:\n" + "\n".join(
            f"- {r['counterparty']}: {r['typical_amount']:,.2f} {r['cadence']} (~{r['monthly_cost']:,.2f}/month)" for r in rec)
            if rec else "I didn't find recurring payments yet.")
    elif re.search(r"\b(tip|advice|recommend|save|saving|improve|suggest)", low):
        recs = call("get_recommendations", {})
        reply = ("Suggestions:\n" + "\n".join(f"- **{r['title']}** — {r['body']}" for r in recs[:5])
                 if recs else "Everything looks healthy this month.")
    elif re.search(r"\binvoice", low):
        res = call("list_invoices", {"page": 1, "page_size": 10})
        items = res.get("items", []) if isinstance(res, dict) else []
        reply = ("Your recent invoices:\n" + "\n".join(
            f"- {i.get('id')} — {i.get('status')} — {(i.get('amount') or {}).get('value', '?')} {(i.get('amount') or {}).get('currency_code', '')}"
            for i in items[:8]) if items else "You have no invoices yet. Try: “Invoice maya@example.com $250 for logo design”.")
    elif re.search(r"\b(dispute|chargeback)", low):
        res = call("list_disputes", {})
        n = len(res.get("items", [])) if isinstance(res, dict) else 0
        reply = f"You have {n} open dispute(s)." if n else "No open disputes."
    elif re.search(r"\bsync|import\b", low):
        res = call("sync_paypal_transactions", {})
        reply = f"Imported {res.get('added', 0)} new PayPal transactions ({res.get('skipped', 0)} already known)." \
            if isinstance(res, dict) and "added" in res else f"Sync failed: {res}"
    elif re.search(r"\b(spend|spent|spending|expense|expenses|where.*money|category|categories|overview|month|summary)\b", low):
        ov = call("get_money_overview", {})
        sp = call("get_spending_by_category", {})
        m = ov["this_month"]
        top = "\n".join(f"- {c['category']}: {c['amount']:,.2f}" + (f" (last month {c['previous']:,.2f})" if c['previous'] else "")
                        for c in sp["by_category"][:6])
        reply = (f"**{m['month']}** so far: money in {m['money_in']:,.2f}, money out {m['money_out']:,.2f}, "
                 f"net {m['net']:,.2f}. Forecast for month end: out ≈ {ov['forecast_month_end']['money_out']:,.2f}.\n\n"
                 f"Top spending:\n{top or '- No spending yet.'}")
    elif not _hinted and intent in HINTS and confidence > 0.25:
        return offline_turn(session_id, f"{text} {HINTS[intent]}", _hinted=True)
    elif intent in {"send", "risk", "schedule", "negotiate", "job_create"} and confidence > 0.25 and not (receiver and amount):
        missing = "who to pay (a phone number, PayPal email or the name of someone you've paid)" if not receiver else "the amount"
        reply = f"Sure. Tell me {missing}, e.g. “Pay +1 408 555 0134 $25 for lunch”."
    else:
        reply = ("I can help with things like:\n- “Where did my money go this month?”\n- “Which payments are stuck?”\n"
                 "- “Verify +1 408 555 0134”\n- “Send $120 to +1 408 555 0134 for the landing page”\n"
                 "- “Is it safe to send $400 to someone@example.com?”\n"
                 "- “Invoice maya@example.com $250 for logo design”\n- “How are my budgets?”\n"
                 "- “Create job logo design for +1 408 555 0134 for $150”, then “the logo job is done”\n"
                 "- “Pay +1 408 555 0134 $50 every Friday”\n- “Negotiate a logo from +1 408 555 0134, budget $300”\n"
                 "- “Buy a camera under $280”, “Sell my bike for $150”, “Split $90 dinner with Priya and Sam”\n\n"
                 "_I run on PayMinute's built-in planner with PayPal's Agent Toolkit and MCP server tools, so I understand requests like these._")
    db.add_agent_message(session_id, {"role": "assistant", "content": reply})
    return {"reply": reply, "steps": steps, "pending_actions": [s["action"] for s in steps if s.get("queued")],
            "mode": "offline", "navigate": navigate}
