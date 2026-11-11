"""Plain-language explanations for pending and stuck payments."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

FINAL_OK = {"COMPLETED", "SUCCESS", "PAID", "CAPTURED"}
FINAL_BAD = {"FAILED", "DENIED", "VOIDED", "RETURNED", "REFUNDED", "REVERSED", "BLOCKED", "CANCELLED", "REJECTED"}
IN_PROGRESS = {"PENDING", "UNCLAIMED", "ONHOLD", "HELD_FOR_REVIEW", "AUTHORIZED", "PROCESSING", "SENT", "APPROVED"}

# key -> (title, explanation, min_hours, max_hours, likely_outcome, actions)
KNOWLEDGE: dict[str, dict[str, Any]] = {
    "PENDING_REVIEW": dict(
        title="Under PayPal risk review",
        explain="PayPal is reviewing this payment (PENDING_REVIEW). It can already show in your PayPal activity, but PayPal "
                "hasn't released it yet.",
        min_h=1, max_h=24, outcome="Usually completes after review",
        actions=["Tap “Check with PayPal” to compare the Payments API with PayPal's transaction ledger",
                 "Sandbox: in the Developer Dashboard open your sandbox business account → Settings and turn Payment Review off, "
                 "otherwise every new payment is held for review",
                 "Don't ship or deliver until it completes"],
    ),
    "ECHECK": dict(
        title="Waiting for an eCheck to clear",
        explain="The buyer paid from a bank account by eCheck. Funds arrive once the bank transfer clears.",
        min_h=72, max_h=144, outcome="Usually completes in 3–6 business days",
        actions=["Hold delivery until the payment completes", "No action needed from you"],
    ),
    "UNILATERAL": dict(
        title="Receiver has no PayPal account yet",
        explain="Money was sent to an email that isn't linked to a PayPal account. PayPal has emailed them to claim it.",
        min_h=1, max_h=30 * 24, outcome="Returned to you after 30 days if never claimed",
        actions=["Ask the receiver to sign up with this exact email", "Check the email address for typos"],
    ),
    "VERIFICATION_REQUIRED": dict(
        title="Receiving account needs verification",
        explain="The receiving PayPal account must be verified before it can accept this payment.",
        min_h=24, max_h=7 * 24, outcome="Completes after verification",
        actions=["Verify your PayPal account (sandbox: Dashboard → Accounts)"],
    ),
    "RECEIVING_PREFERENCE_MANDATES_MANUAL_ACTION": dict(
        title="Receiver must accept manually",
        explain="The receiving account is set to manually accept payments (for example in a different currency).",
        min_h=1, max_h=30 * 24, outcome="Completes once accepted",
        actions=["Accept the payment in the receiving PayPal account"],
    ),
    "TRANSACTION_APPROVED_AWAITING_FUNDING": dict(
        title="Approved, waiting for funding",
        explain="The buyer approved but their funding source hasn't settled yet.",
        min_h=1, max_h=72, outcome="Usually completes",
        actions=["Wait for funding to complete"],
    ),
    "INTERNATIONAL_WITHDRAWAL": dict(
        title="Cross-border processing",
        explain="This international payment needs extra processing before release.",
        min_h=24, max_h=96, outcome="Usually completes",
        actions=["No action needed"],
    ),
    "BUYER_COMPLAINT": dict(
        title="On hold: buyer opened a dispute",
        explain="Funds are on hold because the buyer raised a dispute.",
        min_h=24 * 10, max_h=24 * 30, outcome="Depends on the dispute outcome",
        actions=["Respond to the dispute with proof of delivery"],
    ),
    "CHARGEBACK": dict(
        title="On hold: chargeback",
        explain="The buyer's card issuer opened a chargeback.",
        min_h=24 * 10, max_h=24 * 75, outcome="Depends on the card issuer",
        actions=["Provide evidence through PayPal's Resolution Center"],
    ),
    "PAYOUT_PENDING": dict(
        title="Payout processing",
        explain="PayPal accepted the payout and is processing it. PayMinute checks PayPal again every minute.",
        min_h=0.05, max_h=24, outcome="Usually succeeds within minutes",
        actions=["Tap Refresh from PayPal to check now"],
    ),
    "PAYOUT_SELF_PAY": dict(
        title="Can't pay your own account",
        explain="PayPal refused this payout because the receiver is the same PayPal account that sends PayMinute's "
                "payouts. No money was moved.",
        min_h=0, max_h=0, outcome="Final — funds remain with you",
        actions=["Pay a different PayPal account (in the sandbox, a Personal test account)"],
    ),
    "PAYOUT_UNCLAIMED": dict(
        title="Sent but not claimed",
        explain="The money left your balance but the receiver has no PayPal account for this email (or hasn't "
                "accepted it). PayPal emailed them a link to claim it.",
        min_h=1, max_h=30 * 24, outcome="Returned to you automatically after 30 days if never claimed",
        actions=["Ask the receiver to sign up with this exact email", "Double-check the email for typos",
                 "Cancel it now to get the money back immediately"],
    ),
    "PAYOUT_ONHOLD": dict(
        title="Payout held for review",
        explain="PayPal placed this payout on hold for an internal review.",
        min_h=1, max_h=72, outcome="Usually released after review",
        actions=["No action needed unless PayPal contacts you"],
    ),
    "PAYOUT_BLOCKED": dict(
        title="Payout blocked",
        explain="PayPal blocked this payout. The money stays in your balance.",
        min_h=0, max_h=0, outcome="Final — funds not sent",
        actions=["Check the recipient and try a different payment method"],
    ),
    "PAYOUT_FAILED": dict(
        title="Payout failed",
        explain="The payout could not be completed. Funds were not sent.",
        min_h=0, max_h=0, outcome="Final — funds remain with you",
        actions=["Check the recipient's email or account status and try again"],
    ),
    "PAYOUT_RETURNED": dict(
        title="Returned to you",
        explain="The payout was not claimed (or was cancelled) and the money is back in your balance.",
        min_h=0, max_h=0, outcome="Final — funds returned",
        actions=[],
    ),
    "HELD_FOR_REVIEW": dict(
        title="Held by PayMinute for your review",
        explain="PayMinute's risk check flagged this customer payment, so it was authorized but not captured. The buyer "
                "has not been charged yet.",
        min_h=0, max_h=24 * 3, outcome="You decide: capture or void (authorizations are honoured for 3 days)",
        actions=["Review the risk reasons", "Capture to accept, or void to release the buyer's funds"],
    ),
    "PENDING": dict(
        title="Pending",
        explain="PayPal reports this payment as pending.",
        min_h=1, max_h=72, outcome="Usually completes",
        actions=["PayMinute will keep checking the status"],
    ),
}


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def knowledge_key(tx: dict[str, Any]) -> str | None:
    status = (tx.get("status") or "").upper()
    reason = (tx.get("status_reason") or "").upper()
    if tx.get("kind") == "payout":
        if reason in {"SELF_PAY_NOT_ALLOWED", "CANNOT_PAY_SELF"}:
            return "PAYOUT_SELF_PAY"
        if status in {"PENDING", "UNCLAIMED", "ONHOLD", "BLOCKED", "FAILED", "RETURNED"}:
            return f"PAYOUT_{status}"
        return None
    if status == "HELD_FOR_REVIEW":
        return "HELD_FOR_REVIEW"
    if status == "PENDING":
        return reason if reason in KNOWLEDGE else "PENDING"
    return None


def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{max(1, round(h * 60))} min"
    if h < 48:
        return f"{round(h)} h"
    return f"{round(h / 24)} days"


def learned_duration(tx: dict[str, Any], history: list[dict[str, Any]], histories: dict[str, list[dict]]) -> float | None:
    """Median hours from this status to a final status, from the user's own resolved payments."""
    key = knowledge_key(tx)
    samples = []
    for other in history:
        if other["id"] == tx["id"]:
            continue
        hist = histories.get(other["id"]) or []
        start = None
        for h in hist:
            k = knowledge_key({**other, "status": h["status"], "status_reason": h.get("reason")})
            if k == key and start is None:
                start = _parse(h["at"])
            elif start is not None and h["status"].upper() in FINAL_OK | FINAL_BAD:
                samples.append((_parse(h["at"]) - start).total_seconds() / 3600)
                break
    return statistics.median(samples) if len(samples) >= 3 else None


def explain(tx: dict[str, Any], history: list[dict[str, Any]] | None = None,
            histories: dict[str, list[dict]] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    status = (tx.get("status") or "").upper()
    if status in FINAL_OK:
        return {"state": "done", "title": "Completed", "explain": "This payment has completed.", "actions": []}
    key = knowledge_key(tx)
    if key is None:
        if status in FINAL_BAD:
            return {"state": "final", "title": status.title(), "explain": f"This payment is {status.lower()}.", "actions": []}
        return {"state": "info", "title": status.title() or "Unknown", "explain": "No action needed.", "actions": []}

    k = KNOWLEDGE[key]
    explanation = k["explain"]
    detail = tx.get("paypal_detail") or {}
    if detail.get("error_message") or detail.get("error_name"):
        # PayPal's own words come first
        explanation = f"PayPal says: {detail.get('error_message') or detail.get('error_name')}" + (
            f" ({detail['error_name']})" if detail.get("error_message") and detail.get("error_name") else "")
    elif detail.get("check_error"):
        explanation = f"{k['explain']} PayMinute couldn't check with PayPal just now: {detail['check_error']}"
    elif detail.get("capture_status"):
        report = {"S": "settled", "P": "pending", "D": "denied", "V": "reversed"}.get(detail.get("report_status") or "", None)
        explanation = (f"{k['explain']} PayPal's Payments API reports {detail['capture_status']}"
                       + (f" ({detail['capture_reason']})" if detail.get("capture_reason") else "")
                       + (f"; PayPal's transaction ledger shows it {report}." if report else
                          "; it isn't in PayPal's transaction ledger yet (the sandbox can lag up to 3 hours)."))
    elif detail.get("transaction_status") or detail.get("batch_status"):
        parts = [f"item {detail['transaction_status']}" if detail.get("transaction_status") else None,
                 f"batch {detail['batch_status']}" if detail.get("batch_status") else None]
        explanation = f"{k['explain']} PayPal reports: {', '.join(p for p in parts if p)}."
    since = _parse(tx.get("status_changed_at") or tx["created_at"])
    elapsed_h = (now - since).total_seconds() / 3600
    learned = learned_duration(tx, history or [], histories or {}) if history else None
    final = k["max_h"] == 0
    eta_from = since + timedelta(hours=k["min_h"])
    eta_to = since + timedelta(hours=(learned * 1.5 if learned else k["max_h"]))
    overdue = not final and elapsed_h > (learned * 2 if learned else k["max_h"])

    estimate = None
    if key == "PAYOUT_UNCLAIMED":
        deadline = since + timedelta(days=30)
        estimate = (f"The receiver can claim it until {deadline.strftime('%b %d')}; after that PayPal returns it to you "
                    "automatically. You can also cancel it now.")
    elif key == "HELD_FOR_REVIEW":
        estimate = ("Waiting on you. PayPal honours the authorization in full for 3 days (it stays valid for 29), "
                    "so decide soon.")
    elif not final:
        if learned:
            estimate = f"Based on your past payments, this usually resolves in about {_fmt_hours(learned)}."
        else:
            estimate = f"Typically resolves within {_fmt_hours(k['min_h'])}–{_fmt_hours(k['max_h'])}."
    actions = list(k["actions"])
    if overdue:
        actions.insert(0, "This is taking longer than usual: contact PayPal support with the transaction ID")

    return {
        "state": "final" if final else ("overdue" if overdue else "in_progress"),
        "key": key,
        "title": k["title"],
        "explain": explanation,
        "paypal": detail or None,
        "likely_outcome": k["outcome"],
        "estimate": estimate,
        "eta_from": None if final else eta_from.isoformat(),
        "eta_to": None if final else eta_to.isoformat(),
        "elapsed": _fmt_hours(elapsed_h),
        "elapsed_hours": round(elapsed_h, 2),
        "learned_from_history": learned is not None,
        "actions": actions,
        "can_cancel": tx.get("kind") == "payout" and status == "UNCLAIMED",
        "can_review": status == "HELD_FOR_REVIEW",
    }


def is_in_progress(tx: dict[str, Any]) -> bool:
    return (tx.get("status") or "").upper() in IN_PROGRESS - {"SENT", "APPROVED"}
