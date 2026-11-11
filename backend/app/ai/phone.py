"""Phone number checks before paying: validity, line type, country and PayPal link status."""
from __future__ import annotations

import logging
import re
from typing import Any

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType, carrier, geocoder

from .. import config, db
from .llm import LLMError, get_llm

log = logging.getLogger("payminute.phone")

PHONE_CHARS = re.compile(r"^\+?[\d\s().-]{6,20}$")
LINE_TYPES = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "landline",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "mobile or landline",
    PhoneNumberType.VOIP: "internet (VoIP)",
    PhoneNumberType.TOLL_FREE: "toll-free",
    PhoneNumberType.PREMIUM_RATE: "premium-rate",
    PhoneNumberType.SHARED_COST: "shared-cost",
    PhoneNumberType.PERSONAL_NUMBER: "personal number",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "company number",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}


def looks_like_phone(value: str | None) -> bool:
    v = (value or "").strip()
    return "@" not in v and bool(PHONE_CHARS.match(v)) and sum(c.isdigit() for c in v) >= 6


def normalize(raw: str, region: str | None = None) -> str | None:
    """E.164 (+15555550123) or None when the number isn't valid."""
    try:
        num = phonenumbers.parse(raw.strip(), region or config.settings.phone_region)
    except NumberParseException:
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164) if phonenumbers.is_valid_number(num) else None


def _paypal_evidence(e164: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Is this number linked to a PayPal account, and did PayPal confirm it?"""
    payouts = [t for t in history if t.get("kind") == "payout" and t.get("counterparty_phone") == e164]
    ok = [t for t in payouts if (t.get("status") or "").upper() == "SUCCESS"]
    delivered = f" PayPal has delivered {len(ok)} earlier payout(s) to this account." if ok else ""
    link = db.find_phone(e164)
    if link and link["source"] == "paypal":
        return {"status": "verified", "label": "Verified by PayPal", "name": link["name"], "payer_id": link["payer_id"],
                "payable": True,
                "detail": f"PayPal confirmed this number is on {link['name'] or 'a'}'s PayPal account"
                          f"{' (verified account)' if link['verified_account'] else ''} when they logged in with PayPal.{delivered}"}
    if link:
        return {"status": "linked", "label": "Linked to a PayPal account", "name": link["name"], "payer_id": link["payer_id"],
                "payable": True,
                "detail": f"{link['name'] or 'The owner'} linked this number to their PayPal account in PayMinute. PayPal "
                          f"confirmed who they are, but didn't share their phone, so PayPal hasn't confirmed the number itself.{delivered}"}
    unclaimed = [t for t in payouts if (t.get("status") or "").upper() in {"UNCLAIMED", "RETURNED"}]
    if unclaimed:
        return {"status": "not_on_paypal", "label": "No PayPal account found", "payable": False,
                "detail": "PayPal reported an earlier payout to this number as unclaimed: it isn't linked to a PayPal account."}
    return {"status": "unknown", "label": "Not linked to PayPal yet", "payable": False,
            "detail": "No PayPal account in PayMinute has this number yet. Ask them to log in to PayMinute with PayPal "
                      "once (that links their number), or pay their PayPal email instead."}


def analyze(raw: str, history: list[dict[str, Any]] | None = None, amount: float | None = None,
            explain: bool = False) -> dict[str, Any]:
    history = history or []
    region = config.settings.phone_region
    signals: list[dict[str, Any]] = []

    def sig(code: str, label: str, detail: str, weight: float) -> None:
        signals.append({"code": code, "label": label, "detail": detail, "weight": weight})

    try:
        num = phonenumbers.parse(raw.strip(), region)
    except NumberParseException:
        return {"input": raw, "valid": False, "e164": None, "level": "high", "signals": [],
                "verdict": "That doesn't look like a phone number. Include the country code, e.g. +1 408 555 0123.",
                "payable": False, "paypal": {"status": "unknown", "label": "Can't check", "detail": "Enter a valid number first."}}

    valid = phonenumbers.is_valid_number(num)
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164) if valid else None
    country = phonenumbers.region_code_for_number(num) or phonenumbers.region_code_for_country_code(num.country_code) or "?"
    line = LINE_TYPES.get(phonenumbers.number_type(num), "unknown")
    place = geocoder.description_for_number(num, "en") or country
    network = carrier.name_for_number(num, "en") or None

    if not valid:
        sig("invalid_number", "Not a real phone number", f"{raw} isn't a valid number for {country}.", 0.6)
    else:
        if line in {"premium-rate", "shared-cost"}:
            sig("premium_number", "Premium-rate number", "Scammers use premium numbers; PayPal accounts are linked to personal mobiles.", 0.5)
        elif line in {"toll-free", "company number", "pager", "voicemail"}:
            sig("not_personal", "Not a personal phone", f"This is a {line} number, which usually isn't linked to a personal PayPal account.", 0.3)
        elif line == "internet (VoIP)":
            sig("voip", "Internet (VoIP) number", "VoIP numbers are easy to create anonymously; confirm who owns it.", 0.25)
        elif line == "landline":
            sig("landline", "Landline", "PayPal phone payments normally go to a mobile number.", 0.15)
        if country != region:
            sig("foreign_number", "Number from another country", f"This number is from {place}; your default country is {region}.", 0.12)

    paypal = _paypal_evidence(e164, history) if e164 else {"status": "unknown", "label": "Can't check", "detail": "Invalid number.", "payable": False}
    if paypal["status"] == "not_on_paypal":
        sig("not_on_paypal", "No PayPal account for this number", paypal["detail"], 0.2)
    if paypal["status"] == "linked":
        sig("phone_not_confirmed", "Number not confirmed by PayPal", "PayPal didn't share this account's phone number; "
            "the owner added it. Confirm it with them if this is a large payment.", 0.08)
    if paypal["status"] in {"verified", "linked"}:
        signals = [s for s in signals if s["code"] not in {"landline", "foreign_number"}]

    p_safe = 1.0
    for s in signals:
        p_safe *= 1 - s["weight"]
    score = round(1 - p_safe, 3)
    level = "high" if score >= 0.5 else ("medium" if score >= 0.2 else "low")
    verdict = {
        "high": "Don't send to this number until you've confirmed it with the person directly.",
        "medium": "Double-check this number with the person before paying.",
        "low": "This number looks fine." + (" PayPal confirmed whose account it is." if paypal["status"] == "verified" else ""),
    }[level]

    if valid and not paypal.get("payable") and level == "low":
        verdict = "The number is real, but it isn't linked to a PayPal account yet, so PayPal can't deliver to it."
    result = {
        "input": raw, "valid": valid, "e164": e164,
        "display": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL) if valid else raw,
        "country": country, "place": place, "line_type": line, "network": network,
        "paypal": paypal, "payable": bool(valid and paypal.get("payable")), "signals": sorted(signals, key=lambda s: s["weight"], reverse=True),
        "score": score, "level": level, "verdict": verdict, "ai_summary": None,
    }
    if explain:
        result["ai_summary"] = ai_explain(result, amount)
    return result


def ai_explain(result: dict[str, Any], amount: float | None) -> str | None:
    llm = get_llm()
    if not llm:
        return None
    facts = {k: result[k] for k in ("display", "country", "place", "line_type", "network", "paypal", "payable", "signals", "level")}
    try:
        res = llm.chat(
            [{"role": "user", "content": f"Phone check for a PayPal payment{f' of {amount:.2f}' if amount else ''}: {facts}"}],
            system="You are PayMinute's AI Guard. In at most 2 short sentences, tell the user whether this phone number "
                   "looks safe to pay via PayPal and what to check. Only use the facts given; name the owner only if PayPal "
                   "evidence gives a name. If payable is false, say it can't be paid until it's linked to PayPal.",
            max_tokens=120,
        )
        return (res.get("content") or "").strip() or None
    except LLMError as exc:
        log.info("phone explain failed: %s", exc)
        return None
