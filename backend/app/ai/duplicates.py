"""Duplicate payment detection."""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

DEAD_STATUSES = {"FAILED", "DENIED", "VOIDED", "CANCELLED", "RETURNED", "REJECTED", "BLOCKED", "DRAFT", "CREATED"}
WINDOW_MINUTES = {"out": 24 * 60, "in": 60}


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _same_party(a: dict[str, Any], email: str | None, name: str | None, phone: str | None = None) -> bool:
    if phone and a.get("counterparty_phone"):
        return a["counterparty_phone"] == phone
    if email and a.get("counterparty_email"):
        return a["counterparty_email"].strip().lower() == email.strip().lower()
    if name and a.get("counterparty"):
        return SequenceMatcher(None, a["counterparty"].lower(), name.lower()).ratio() > 0.9
    return False


def find_duplicates(
    history: list[dict[str, Any]],
    direction: str,
    amount: float,
    email: str | None,
    name: str | None = None,
    memo: str | None = None,
    at: datetime | None = None,
    exclude_id: str | None = None,
    phone: str | None = None,
) -> list[dict[str, Any]]:
    at = at or datetime.now(timezone.utc)
    window = WINDOW_MINUTES.get(direction, 60)
    matches = []
    for tx in history:
        if tx.get("id") == exclude_id or tx.get("direction") != direction:
            continue
        if (tx.get("status") or "").upper() in DEAD_STATUSES:
            continue
        if abs(float(tx.get("amount") or 0) - amount) > 0.009:
            continue
        if not _same_party(tx, email, name, phone):
            continue
        minutes_ago = (at - _parse(tx["created_at"])).total_seconds() / 60
        if minutes_ago < 0 or minutes_ago > window:
            continue
        memo_similarity = None
        if memo and tx.get("memo"):
            memo_similarity = round(SequenceMatcher(None, memo.lower(), tx["memo"].lower()).ratio(), 2)
        matches.append({
            "transaction_id": tx["id"],
            "minutes_ago": round(minutes_ago, 1),
            "amount": tx["amount"],
            "status": tx["status"],
            "memo": tx.get("memo"),
            "memo_similarity": memo_similarity,
        })
    matches.sort(key=lambda m: m["minutes_ago"])
    return matches


def describe(matches: list[dict[str, Any]], direction: str) -> str | None:
    if not matches:
        return None
    m = matches[0]
    when = f"{int(m['minutes_ago'])} min ago" if m["minutes_ago"] < 120 else f"{m['minutes_ago'] / 60:.0f} h ago"
    if direction == "out":
        return f"You already sent this exact amount to this recipient {when}."
    return f"This customer already paid this exact amount {when} — possible accidental double payment."
