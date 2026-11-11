"""Expense and income categories: saved choices, rules, then the LLM."""
from __future__ import annotations

import logging
import re
from typing import Any

from .. import db
from .llm import LLMError, get_llm

log = logging.getLogger("payminute.categorize")

EXPENSE_CATEGORIES = [
    "Contractors", "Software & Subscriptions", "Marketing", "Office & Equipment", "Travel", "Food & Dining",
    "Groceries", "Transport", "Bills & Utilities", "Shopping", "Entertainment", "Health", "Education",
    "Rent", "Fees", "Transfers", "Other",
]
INCOME_CATEGORIES = ["Client Payments", "Sales", "Refunds", "Transfers In", "Other Income"]
ALL_CATEGORIES = EXPENSE_CATEGORIES + INCOME_CATEGORIES

RULES: list[tuple[str, str]] = [
    (r"\b(rent|landlord|lease|coworking|wework)", "Rent"),
    (r"\b(freelanc|contractor|designer|developer|illustrat|editor|consult|invoice for|retainer|upwork|fiverr|logo|copywrit|va\b|virtual assistant)", "Contractors"),
    (r"\b(figma|notion|slack|github|adobe|canva|aws|vercel|render|heroku|openai|anthropic|google workspace|dropbox|zoom|subscription|saas|hosting|domain|license|jetbrains|spotify premium)", "Software & Subscriptions"),
    (r"\b(ads?\b|advert|facebook ads|google ads|promotion|sponsor|marketing|seo|newsletter)", "Marketing"),
    (r"\b(laptop|monitor|keyboard|desk|chair|printer|office|stationery|equipment|camera|microphone)", "Office & Equipment"),
    (r"\b(flight|airline|hotel|airbnb|booking\.com|train ticket|visa fee|travel)", "Travel"),
    (r"\b(restaurant|cafe|coffee|starbucks|pizza|lunch|dinner|swiggy|zomato|doordash|ubereats|food)", "Food & Dining"),
    (r"\b(grocery|groceries|supermarket|walmart|costco|bigbasket|whole foods|instacart)", "Groceries"),
    (r"\b(uber|lyft|ola|taxi|cab|metro|fuel|petrol|gas station|parking)", "Transport"),
    (r"\b(electric|water bill|internet|broadband|phone bill|mobile recharge|utility|utilities)", "Bills & Utilities"),
    (r"\b(amazon|ebay|etsy|shop|store|clothing|shoes|flipkart|myntra)", "Shopping"),
    (r"\b(netflix|prime video|disney|movie|cinema|concert|game|steam)", "Entertainment"),
    (r"\b(pharmacy|doctor|clinic|hospital|gym|fitness|medic)", "Health"),
    (r"\b(course|udemy|coursera|tuition|book|training|workshop|school)", "Education"),
    (r"\b(fee|charge|commission)\b", "Fees"),
]
INCOME_RULES: list[tuple[str, str]] = [
    (r"\b(refund|reversal|returned)", "Refunds"),
    (r"\b(invoice|project|milestone|retainer|consulting|design work|website|logo|deposit)", "Client Payments"),
    (r"\b(order|purchase|product|sale|shop|item|template|ebook|course)", "Sales"),
    (r"\b(transfer|top.?up|add money)", "Transfers In"),
]
# Transaction Search event codes for transfers, refunds and fees
EVENT_CODE_MAP = {
    "T1107": "Refunds", "T1106": "Refunds", "T0400": "Transfers", "T0300": "Transfers In",
    "T1201": "Fees", "T0001": "Contractors",
}


def counterparty_key(name: str | None, email: str | None) -> str:
    return re.sub(r"\s+", " ", (email or name or "").strip().lower())


def rule_category(direction: str, counterparty: str | None, memo: str | None, event_code: str | None = None) -> str | None:
    if event_code and event_code in EVENT_CODE_MAP:
        cat = EVENT_CODE_MAP[event_code]
        if (direction == "in") == (cat in INCOME_CATEGORIES):
            return cat
    text = f"{counterparty or ''} {memo or ''}".lower()
    for pattern, cat in (INCOME_RULES if direction == "in" else RULES):
        if re.search(pattern, text):
            return cat
    return None


def categorize_one(direction: str, counterparty: str | None, email: str | None, memo: str | None,
                   event_code: str | None = None) -> tuple[str, str]:
    """Return (category, source) without calling the LLM."""
    remembered = db.recall_category(counterparty_key(counterparty, email))
    if remembered:
        return remembered, "memory"
    cat = rule_category(direction, counterparty, memo, event_code)
    if cat:
        return cat, "rule"
    return ("Other Income" if direction == "in" else "Other"), "default"


def llm_categorize(items: list[dict[str, Any]]) -> dict[str, str]:
    """Batch-classify uncategorised transactions with the LLM. Returns {id: category}."""
    llm = get_llm()
    if not llm or not items:
        return {}
    lines = [
        f'{{"id": "{t["id"]}", "direction": "{t["direction"]}", "counterparty": {t.get("counterparty")!r}, '
        f'"note": {t.get("memo")!r}, "amount": {t["amount"]}}}'
        for t in items[:40]
    ]
    system = (
        "You categorise payments for a freelancer/small business ledger. "
        f"Outgoing payments must use one of: {', '.join(EXPENSE_CATEGORIES)}. "
        f"Incoming payments must use one of: {', '.join(INCOME_CATEGORIES)}. "
        'Return a JSON object mapping id to category, e.g. {"tx_1": "Marketing"}.'
    )
    try:
        result = llm.complete_json(system, "Categorise:\n" + "\n".join(lines))
    except (LLMError, ValueError) as exc:
        log.warning("LLM categorisation failed: %s", exc)
        return {}
    if not isinstance(result, dict):
        return {}
    return {k: v for k, v in result.items() if v in ALL_CATEGORIES}


def categorize_uncategorized() -> int:
    """Upgrade 'default' categories using the LLM. Returns how many were updated."""
    pending = [t for t in db.list_tx(limit=500) if t.get("category_source") == "default" and t.get("mine", True)]
    mapping = llm_categorize(pending)
    for tx_id, cat in mapping.items():
        db.update_tx(tx_id, category=cat, category_source="ai")
    return len(mapping)


def apply_user_correction(tx_id: str, category: str) -> dict[str, Any] | None:
    tx = db.get_tx(tx_id)
    if not tx:
        return None
    db.remember_category(counterparty_key(tx.get("counterparty"), tx.get("counterparty_email")), category)
    updated = db.update_tx(tx_id, category=category, category_source="user")
    # relabel this contact's other payments
    key = counterparty_key(tx.get("counterparty"), tx.get("counterparty_email"))
    for other in db.list_tx(limit=1000):
        if other.get("mine", True) and other["id"] != tx_id and other.get("category_source") != "user" and \
                counterparty_key(other.get("counterparty"), other.get("counterparty_email")) == key and \
                other["direction"] == tx["direction"]:
            db.update_tx(other["id"], category=category, category_source="memory")
    return updated
