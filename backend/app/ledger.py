"""Payment flows. Risk checks run before any money moves."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import config, db
import re

from .ai import categorize, risk, status as status_ai
from .paypal import service as pp
from .paypal.client import PayPalError

log = logging.getLogger("payminute.ledger")


class FlowError(Exception):
    def __init__(self, message: str, status_code: int = 400, paypal: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.paypal = paypal


def _require_paypal() -> None:
    if not config.settings.paypal_configured:
        raise FlowError("PayPal sandbox isn't connected yet: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET on the server.", 503)


def history() -> list[dict[str, Any]]:
    return db.list_tx(limit=2000)


def _risk_fields(assessment: dict[str, Any]) -> dict[str, Any]:
    return {"risk_score": assessment["score"], "risk_level": assessment["level"], "risk_reasons": assessment["reasons"]}


PHONE_LIKE = re.compile(r"^\+?[\d\s().-]{7,}$")


def resolve_receiver(receiver: str) -> dict[str, Any]:
    """A PayPal sandbox email (…@personal.example.com). PayMinute doesn't pay phone numbers."""
    r = (receiver or "").strip().lower()
    rule = config.settings.email_rule
    if "@" not in r and PHONE_LIKE.match(r):
        raise FlowError(f"PayMinute pays PayPal emails only, not phone numbers. Use the person's PayPal email ({rule}).")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", r):
        raise FlowError(f"Enter the recipient's PayPal email ({rule}).")
    if not config.settings.email_allowed(r):
        raise FlowError(f"Only PayPal sandbox emails ending in {rule.replace('…', '')} can be paid.")
    known = db.find_email(r)
    return {"type": "EMAIL", "value": r, "email": r, "phone": None, "name": (known or {}).get("name"), "display": r,
            "on_payminute": bool(known)}


def email_check(who: dict[str, Any]) -> dict[str, Any]:
    """What PayMinute knows about a recipient's PayPal email (shown before paying)."""
    known = db.find_email(who["email"])
    if known:
        return {"status": "on_payminute", "label": f"{known['name'] or 'PayMinute user'}'s PayPal email", "name": known["name"],
                "detail": ("PayPal shared this email when they logged in to PayMinute with PayPal."
                           if known["source"] == "paypal" else "They added it as their PayPal email in PayMinute.")}
    return {"status": "sandbox", "label": "PayPal sandbox email", "name": None,
            "detail": "A PayPal sandbox personal account. PayPal delivers the payout to it, or it waits as unclaimed if "
                      "no account uses this email yet."}


def assess_payout(receiver: str, amount: float, note: str | None, name: str | None = None) -> dict[str, Any]:
    who = resolve_receiver(receiver)
    hist = history()
    name = name or who.get("name")
    assessment = risk.assess("out", amount, who["email"], name, note, hist,
                             verified_identity=bool(who.get("on_payminute")))
    category, _ = categorize.categorize_one("out", name or who["display"], who["display"], note)
    assessment["suggested_category"] = category
    assessment["recipient"] = who
    assessment["email_check"] = email_check(who)
    return assessment


def send_payout(receiver: str, amount: float, note: str | None, name: str | None, client_ref: str,
                acknowledge_risk: bool = False, source: str = "app") -> dict[str, Any]:
    _require_paypal()
    if amount <= 0:
        raise FlowError("Amount must be greater than zero.")
    existing = db.find_tx(request_id=client_ref)
    if existing:  # idempotent: the same Send click never pays twice
        return {"transaction": existing, "risk": None, "idempotent_replay": True}
    if db.find_any_tx(request_id=client_ref):  # someone else's request id: never return their payment
        raise FlowError("This payment reference was already used. Reload the page and try again.", 409)

    assessment = assess_payout(receiver, amount, note, name)
    who = assessment["recipient"]
    if assessment["level"] == "high" and not acknowledge_risk:
        raise FlowError("High risk payment needs your explicit confirmation.", 409, {"risk": assessment})

    name = name or who.get("name")
    display = name or who["display"]
    category, cat_source = categorize.categorize_one("out", name or who["display"], who["display"], note)
    tx = db.insert_tx({
        "kind": "payout", "direction": "out", "amount": round(amount, 2), "counterparty": display,
        "counterparty_email": who["email"], "counterparty_phone": None, "memo": note, "category": category,
        "category_source": cat_source, "status": "PENDING", "request_id": client_ref, "source": source,
        "counterparty_account": _other_account(who.get("email")),
        **_risk_fields(assessment),
    })
    try:
        resp = pp.send_payout(who["value"], amount, note or "", sender_item_id=tx["id"], recipient_type=who["type"])
    except PayPalError as exc:
        db.update_tx(tx["id"], status="FAILED", status_reason=exc.name, _source="paypal")
        raise FlowError(f"PayPal rejected the payout: {exc.message}", 502, exc.to_dict()) from exc

    batch_id = (resp.get("batch_header") or {}).get("payout_batch_id")
    db.update_tx(tx["id"], paypal_payout_batch_id=batch_id)
    tx = refresh_payout(db.get_tx(tx["id"]))  # type: ignore[arg-type]
    return {"transaction": tx, "risk": assessment, "idempotent_replay": False}


def refresh_payout(tx: dict[str, Any]) -> dict[str, Any]:
    batch = None
    try:
        if tx.get("paypal_payout_item_id"):
            item = pp.get_payout_item(tx["paypal_payout_item_id"])
        elif tx.get("paypal_payout_batch_id"):
            batch = pp.get_payout_batch(tx["paypal_payout_batch_id"])
            items = batch.get("items") or []
            item = next((i for i in items if (i.get("payout_item") or {}).get("sender_item_id") == tx["id"]),
                        items[0] if items else None)
            if not item:
                return tx
        else:
            return tx
    except PayPalError as exc:
        log.warning("Could not refresh payout %s: %s", tx["id"], exc)
        return db.update_tx(tx["id"], paypal_detail={
            "checked_at": db.now_iso(), "check_error": exc.message, "check_error_name": exc.name,
            "debug_id": exc.debug_id}) or tx
    status = (item.get("transaction_status") or "PENDING").upper()
    errors = item.get("errors") or {}
    if not isinstance(errors, dict):
        errors = {}
    if not errors and status in {"FAILED", "BLOCKED", "RETURNED", "DENIED"} and item.get("payout_item_id"):
        try:  # batch listings can leave out errors; the item endpoint has them
            errors = pp.get_payout_item(item["payout_item_id"]).get("errors") or {}
        except PayPalError:
            pass
    detail = {
        "checked_at": db.now_iso(),
        "transaction_status": item.get("transaction_status"),
        "batch_status": ((batch or {}).get("batch_header") or {}).get("batch_status"),
        "error_name": errors.get("name"),
        "error_message": errors.get("message"),
        "error_info": errors.get("information_link"),
        "time_processed": item.get("time_processed"),
    }
    updated = db.update_tx(
        tx["id"],
        status=status,
        paypal_detail=detail,
        status_reason=errors.get("name"),
        paypal_payout_item_id=item.get("payout_item_id"),
        paypal_transaction_id=item.get("transaction_id") or tx.get("paypal_transaction_id"),
        _source="paypal",
    )
    return updated or tx


def cancel_unclaimed(tx_id: str) -> dict[str, Any]:
    tx = db.get_tx(tx_id)
    if not tx or tx["kind"] != "payout":
        raise FlowError("Payout not found", 404)
    if tx["status"] != "UNCLAIMED" or not tx.get("paypal_payout_item_id"):
        raise FlowError("Only unclaimed payouts can be cancelled.")
    try:
        item = pp.cancel_payout_item(tx["paypal_payout_item_id"])
    except PayPalError as exc:
        raise FlowError(f"PayPal could not cancel: {exc.message}", 502, exc.to_dict()) from exc
    return db.update_tx(tx_id, status=(item.get("transaction_status") or "RETURNED").upper(),
                       status_reason="CANCELLED_BY_SENDER", _source="user")  # type: ignore[return-value]


def _other_account(email: str | None) -> str | None:
    """The other side's PayMinute account (if they've logged in), so the payment shows up for them too."""
    other = db.account_for_email(email)
    return other if other and other != db.current_account() else None


def create_checkout(amount: float, description: str, client_ref: str) -> dict[str, Any]:
    _require_paypal()
    if amount <= 0:
        raise FlowError("Amount must be greater than zero.")
    existing = db.find_tx(request_id=client_ref)
    if existing and existing.get("paypal_order_id"):
        return {"transaction": existing, "order_id": existing["paypal_order_id"]}
    if db.find_any_tx(request_id=client_ref):
        raise FlowError("This payment reference was already used. Reload the page and try again.", 409)
    category, cat_source = categorize.categorize_one("in", None, None, description)
    tx = db.insert_tx({
        "kind": "checkout", "direction": "in", "amount": round(amount, 2), "counterparty": "Awaiting buyer",
        "memo": description, "category": category, "category_source": cat_source, "status": "CREATED",
        "request_id": client_ref,
    })
    try:
        order = pp.create_checkout_order(amount, description, invoice_ref=tx["id"], request_id=f"order-{client_ref}")
    except PayPalError as exc:
        db.update_tx(tx["id"], status="FAILED", status_reason=exc.name, _source="paypal")
        raise FlowError(f"PayPal could not create the order: {exc.message}", 502, exc.to_dict()) from exc
    tx = db.update_tx(tx["id"], paypal_order_id=order["id"])
    approve = next((l["href"] for l in order.get("links", []) if l.get("rel") in {"approve", "payer-action"}), None)
    return {"transaction": tx, "order_id": order["id"], "approve_url": approve}


def complete_checkout(order_id: str) -> dict[str, Any]:
    """Buyer approved in PayPal: authorize, risk-check the payer, then capture or hold."""
    tx = db.find_tx(paypal_order_id=order_id)
    if not tx:
        raise FlowError("Unknown order", 404)
    if tx["status"] not in {"CREATED", "APPROVED"}:
        return {"transaction": tx, "risk": None, "decision": "already_processed"}
    try:
        order = pp.authorize_order(order_id, request_id=f"auth-{order_id}")
    except PayPalError as exc:
        if exc.name == "INSTRUMENT_DECLINED" or "INSTRUMENT_DECLINED" in exc.message:
            raise FlowError("The buyer's payment method was declined. Ask them to choose another.", 422, exc.to_dict()) from exc
        db.update_tx(tx["id"], status="FAILED", status_reason=exc.name, _source="paypal")
        raise FlowError(f"PayPal could not authorize: {exc.message}", 502, exc.to_dict()) from exc

    payer = pp.extract_payer(order)
    auth = pp.extract_authorization(order)
    category, cat_source = categorize.categorize_one("in", payer["name"], payer["email"], tx.get("memo"))
    db.update_tx(tx["id"], counterparty=payer["name"], counterparty_email=(payer["email"] or "").lower() or None,
                 counterparty_country=payer["country"], paypal_authorization_id=auth.get("id"),
                 counterparty_account=_other_account(payer["email"]),
                 status="AUTHORIZED", category=category if cat_source != "default" else tx["category"],
                 category_source=cat_source if cat_source != "default" else tx["category_source"], _source="paypal")
    assessment = risk.assess("in", tx["amount"], payer["email"], payer["name"], tx.get("memo"), history(),
                             country=payer["country"], exclude_id=tx["id"])
    db.update_tx(tx["id"], **_risk_fields(assessment))

    if assessment["score"] >= config.settings.hold_threshold:
        assessment["recommendation"] = risk.recommendation("in", "high", assessment["reasons"])
        tx = db.update_tx(tx["id"], status="HELD_FOR_REVIEW", status_reason="PAYMINUTE_RISK_HOLD", _source="payminute")
        return {"transaction": tx, "risk": assessment, "decision": "held"}
    tx = capture(tx["id"])
    return {"transaction": tx, "risk": assessment, "decision": "captured"}


def capture(tx_id: str) -> dict[str, Any]:
    tx = db.get_tx(tx_id)
    if not tx or not tx.get("paypal_authorization_id"):
        raise FlowError("Nothing to capture", 404)
    try:
        cap = pp.capture_authorization(tx["paypal_authorization_id"], request_id=f"cap-{tx['id']}", invoice_ref=tx["id"])
    except PayPalError as exc:
        raise FlowError(f"PayPal could not capture: {exc.message}", 502, exc.to_dict()) from exc
    status = (cap.get("status") or "COMPLETED").upper()
    reason = (cap.get("status_details") or {}).get("reason")
    return db.update_tx(tx_id, status=status, status_reason=reason, paypal_capture_id=cap.get("id"),
                        paypal_transaction_id=cap.get("id"), _source="paypal")  # type: ignore[return-value]


def review_decision(tx_id: str, approve: bool) -> dict[str, Any]:
    tx = db.get_tx(tx_id)
    if not tx or tx["status"] != "HELD_FOR_REVIEW":
        raise FlowError("This payment is not waiting for review.")
    if approve:
        return capture(tx_id)
    try:
        pp.void_authorization(tx["paypal_authorization_id"], request_id=f"void-{tx_id}")
    except PayPalError as exc:
        raise FlowError(f"PayPal could not void: {exc.message}", 502, exc.to_dict()) from exc
    return db.update_tx(tx_id, status="VOIDED", status_reason="DECLINED_BY_OWNER", _source="user")  # type: ignore[return-value]


def refresh_capture(tx: dict[str, Any]) -> dict[str, Any]:
    if not tx.get("paypal_capture_id"):
        return tx
    try:
        cap = pp.get_capture(tx["paypal_capture_id"])
    except PayPalError as exc:
        log.warning("Could not refresh capture %s: %s", tx["id"], exc)
        return tx
    status = (cap.get("status") or tx["status"]).upper()
    reason = (cap.get("status_details") or {}).get("reason")
    return db.update_tx(tx["id"], status=status, status_reason=reason, _source="paypal") or tx


REPORT_STATUS = {"S": "COMPLETED", "P": "PENDING", "D": "DENIED", "V": "REFUNDED"}


def _money(m: dict[str, Any] | None) -> float | None:
    try:
        return float((m or {}).get("value"))
    except (TypeError, ValueError):
        return None


def check_capture(tx: dict[str, Any]) -> dict[str, Any]:
    """Ask PayPal twice: the Payments API (capture status) and Transaction Search (PayPal's ledger).

    A capture can read PENDING / PENDING_REVIEW in the Payments API while the money already shows in the
    account; when PayPal's ledger reports the transaction as settled ("S"), PayMinute marks it completed.
    """
    detail: dict[str, Any] = {"checked_at": db.now_iso()}
    status, reason = tx["status"], tx.get("status_reason")
    try:
        cap = pp.get_capture(tx["paypal_capture_id"])
        status = (cap.get("status") or status).upper()
        reason = (cap.get("status_details") or {}).get("reason")
        brk = cap.get("seller_receivable_breakdown") or {}
        detail.update(capture_status=cap.get("status"), capture_reason=reason, transaction_status=cap.get("status"),
                      gross=_money(brk.get("gross_amount")), fee=_money(brk.get("paypal_fee")), net=_money(brk.get("net_amount")),
                      update_time=cap.get("update_time"))
    except PayPalError as exc:
        detail.update(check_error=exc.message, check_error_name=exc.name, debug_id=exc.debug_id)
    try:
        since = datetime.fromisoformat(tx["created_at"])
        info = pp.find_reported_transaction(tx["paypal_capture_id"], since)
        if info:
            detail.update(report_status=info.get("transaction_status"), report_updated=info.get("transaction_updated_date"))
            if status == "PENDING" and info.get("transaction_status") == "S":
                status, reason = "COMPLETED", None
                detail["settled_by"] = "transaction_search"
        else:
            detail["report_status"] = None
    except PayPalError as exc:
        detail["report_error"] = exc.message
    return db.update_tx(tx["id"], status=status, status_reason=reason, paypal_detail=detail, _source="paypal") or tx


def check_with_paypal(tx_id: str) -> dict[str, Any]:
    """The user asked: what does PayPal say about this payment right now?"""
    tx = db.get_tx(tx_id)
    if not tx:
        raise FlowError("Payment not found", 404)
    _require_paypal()
    if tx["kind"] == "checkout" and tx.get("paypal_capture_id"):
        return check_capture(tx)
    return refresh(tx)


def refund(tx_id: str, note: str | None = None) -> dict[str, Any]:
    tx = db.get_tx(tx_id)
    if not tx or not tx.get("paypal_capture_id"):
        raise FlowError("Only captured customer payments can be refunded.", 404)
    try:
        pp.refund_capture(tx["paypal_capture_id"], request_id=f"refund-{tx_id}", note=note)
    except PayPalError as exc:
        raise FlowError(f"PayPal could not refund: {exc.message}", 502, exc.to_dict()) from exc
    return db.update_tx(tx_id, status="REFUNDED", status_reason="REFUNDED_BY_OWNER", _source="user")  # type: ignore[return-value]


def refresh(tx: dict[str, Any]) -> dict[str, Any]:
    if tx["kind"] == "payout":
        return refresh_payout(tx)
    if tx["kind"] == "checkout":
        if tx["status"] == "PENDING" and tx.get("paypal_capture_id") and _due_for_ledger_check(tx):
            return check_capture(tx)
        return refresh_capture(tx)
    return tx


def _due_for_ledger_check(tx: dict[str, Any]) -> bool:
    """Pending for 10+ minutes and not checked against PayPal's ledger in the last 15: check again."""
    now = datetime.now(timezone.utc)
    try:
        if (now - datetime.fromisoformat(tx["created_at"])).total_seconds() < 600:
            return False
        last = (tx.get("paypal_detail") or {}).get("checked_at")
        return not last or (now - datetime.fromisoformat(last)).total_seconds() > 900
    except (TypeError, ValueError):
        return True


def refresh_in_progress() -> list[dict[str, Any]]:
    changed = []
    for tx in db.list_tx(limit=200, statuses=["PENDING", "UNCLAIMED", "ONHOLD"]):
        before = tx["status"]
        after = refresh(tx)
        if after["status"] != before:
            changed.append(after)
    return changed


def in_progress_with_explanations() -> list[dict[str, Any]]:
    hist = history()
    histories = {t["id"]: db.status_history(t["id"]) for t in hist if t.get("kind") in {"payout", "checkout"}}
    out = []
    for tx in hist:
        if status_ai.is_in_progress(tx):
            out.append({"transaction": tx, "insight": status_ai.explain(tx, hist, histories)})
    return out


def sync_from_paypal(days: int = 30) -> dict[str, Any]:
    _require_paypal()
    try:
        found = pp.search_transactions(days)
    except PayPalError as exc:
        hint = ""
        if exc.status in (403, 401):
            hint = " Turn on 'Transaction search' for your sandbox app in the PayPal Developer Dashboard."
        elif exc.name == "NETWORK_ERROR":
            hint = " PayPal's sandbox reporting is slow at times; try again in a minute."
        raise FlowError(f"Transaction Search failed: {exc.message}.{hint}", 502, exc.to_dict()) from exc
    details = found["items"]
    added, skipped, settled, others = 0, 0, 0, 0
    for d in details:
        info = d.get("transaction_info") or {}
        txn_id = info.get("transaction_id")
        if not txn_id:
            continue
        known = db.find_any_tx(paypal_transaction_id=txn_id) or db.find_any_tx(paypal_capture_id=txn_id)
        if known:
            # PayPal's ledger says it settled: don't leave it pending here
            reported = REPORT_STATUS.get(info.get("transaction_status") or "")
            if known["status"] == "PENDING" and reported in {"COMPLETED", "DENIED", "REFUNDED"}:
                db.update_tx(known["id"], status=reported, status_reason=None, _source="paypal_sync")
                settled += 1
            skipped += 1
            continue
        if db.current_account():
            # the business account is shared: activity PayMinute didn't create isn't assigned to whoever syncs
            others += 1
            continue
        value = float((info.get("transaction_amount") or {}).get("value") or 0)
        if value == 0:
            continue
        payer = d.get("payer_info") or {}
        name = (payer.get("payer_name") or {}).get("alternate_full_name") or payer.get("email_address")
        items = ((d.get("cart_info") or {}).get("item_details") or [])
        memo = info.get("transaction_note") or info.get("transaction_subject") or \
            (items[0].get("item_name") if items else None)
        direction = "in" if value > 0 else "out"
        category, cat_source = categorize.categorize_one(direction, name, payer.get("email_address"), memo,
                                                         info.get("transaction_event_code"))
        status = {"S": "COMPLETED", "P": "PENDING", "D": "DENIED", "V": "REFUNDED"}.get(info.get("transaction_status"), "COMPLETED")
        created = info.get("transaction_initiation_date") or db.now_iso()
        try:
            db.insert_tx({
                "kind": "synced", "direction": direction, "amount": abs(value),
                "currency": (info.get("transaction_amount") or {}).get("currency_code"),
                "counterparty": name, "counterparty_email": (payer.get("email_address") or "").lower() or None,
                "counterparty_country": payer.get("country_code"), "memo": memo, "category": category,
                "category_source": cat_source, "status": status, "paypal_transaction_id": txn_id,
                "source": "paypal_sync", "created_at": _norm_ts(created),
            })
            added += 1
        except Exception as exc:  # unique index race etc.
            log.info("skip synced %s: %s", txn_id, exc)
            skipped += 1
    ai_updated = categorize.categorize_uncategorized()
    note = None
    if not details:
        note = ("PayPal returned no transactions for the last 30 days. Transaction Search only shows activity on your "
                "app's business account, and the sandbox can take up to 3 hours to include new payments.")
        if found.get("last_refreshed"):
            note += f" PayPal's data is current up to {found['last_refreshed']}."
    elif not added:
        note = "Everything PayPal returned is already in PayMinute."
    if others:
        note = (f"{others} other transaction(s) on the app's PayPal business account weren't made in PayMinute, so they "
                "aren't added to your history.")
    if settled:
        note = f"Updated {settled} pending payment(s) that PayPal now reports as settled." + (f" {note}" if note else "")
    return {"fetched": len(details), "added": added, "skipped": skipped, "settled": settled, "ai_categorized": ai_updated,
            "last_refreshed": found.get("last_refreshed"), "note": note}


def _norm_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return db.now_iso()


def handle_webhook_event(event: dict[str, Any]) -> str:
    et = event.get("event_type", "")
    res = event.get("resource") or {}
    if et.startswith("PAYMENT.CAPTURE."):
        cap_id = res.get("id")
        if et in {"PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"}:
            # the refund's "up" link points at the capture
            up = next((l.get("href", "") for l in res.get("links", []) if l.get("rel") == "up"), "")
            if "/captures/" in up:
                cap_id = up.rstrip("/").split("/captures/")[-1].split("/")[0]
            tx = db.find_any_tx(paypal_capture_id=cap_id) if cap_id else None
            if not tx:
                return "ignored: unknown capture"
            new_status = "REFUNDED" if et.endswith("REFUNDED") else "REVERSED"
            db.update_tx(tx["id"], status=new_status, _source="webhook")
            return f"updated {tx['id']} -> {new_status}"
        tx = db.find_any_tx(paypal_capture_id=cap_id) if cap_id else None
        if not tx and res.get("invoice_id"):
            tx = db.get_tx(res["invoice_id"])
        if not tx:
            return "ignored: unknown capture"
        status = (res.get("status") or et.rsplit(".", 1)[-1]).upper()
        reason = (res.get("status_details") or {}).get("reason")
        db.update_tx(tx["id"], status=status, status_reason=reason, paypal_capture_id=cap_id, _source="webhook")
        return f"updated {tx['id']} -> {status}"
    if et.startswith("PAYMENT.PAYOUTS-ITEM."):
        item_id = res.get("payout_item_id")
        tx = db.find_any_tx(paypal_payout_item_id=item_id) if item_id else None
        if not tx:
            sender_item = (res.get("payout_item") or {}).get("sender_item_id")
            tx = db.get_tx(sender_item) if sender_item else None
        if not tx:
            return "ignored: unknown payout item"
        suffix = et.rsplit(".", 1)[-1]
        fallback = {"SUCCEEDED": "SUCCESS", "CANCELED": "RETURNED", "HELD": "ONHOLD"}.get(suffix, suffix)
        status = (res.get("transaction_status") or fallback).upper()
        db.update_tx(tx["id"], status=status, paypal_payout_item_id=item_id, _source="webhook")
        return f"updated {tx['id']} -> {status}"
    if et == "PAYMENT.AUTHORIZATION.VOIDED":
        tx = db.find_any_tx(paypal_authorization_id=res.get("id"))
        if tx:
            db.update_tx(tx["id"], status="VOIDED", _source="webhook")
            return f"updated {tx['id']} -> VOIDED"
    return "ignored"
