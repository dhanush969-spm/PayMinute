"""Payment risk score: rules plus a per-user Isolation Forest."""
from __future__ import annotations

import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from . import duplicates
from .fmt import fmt_money

try:  # scikit-learn is optional
    from sklearn.ensemble import IsolationForest
except Exception:
    IsolationForest = None  # type: ignore[assignment]

MIN_TRAINING_SAMPLES = 12

SCAM_PHRASES = [
    "gift card", "giftcard", "itunes card", "steam card", "google play card", "crypto", "bitcoin", "btc", "usdt",
    "wallet address", "urgent", "immediately", "asap", "verify your account", "account suspended", "lottery",
    "prize", "you won", "inheritance", "investment return", "guaranteed return", "double your", "overpayment",
    "refund the difference", "customs fee", "release fee", "processing fee", "otp", "one time password",
    "password", "tech support", "remote access", "irs", "tax penalty", "arrest", "western union", "advance fee",
    "romance", "dating", "sugar", "kindly", "do not tell",
]
DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "temp-mail.org", "guerrillamail.com", "10minutemail.com", "yopmail.com",
    "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
    "fakeinbox.com", "mintemail.com", "emailondeck.com", "mohmal.com", "tempmailo.com",
}
BRAND_NAMES = {"paypal": "PayPal", "venmo": "Venmo", "amazon": "Amazon", "apple": "Apple", "google": "Google",
               "microsoft": "Microsoft", "netflix": "Netflix", "chase": "Chase", "wellsfargo": "Wells Fargo",
               "hdfc": "HDFC", "sbi": "SBI"}
BRANDS = ["paypal", "venmo", "amazon", "apple", "google", "microsoft", "netflix", "chase", "wellsfargo", "hdfc", "sbi"]
OFFICIAL_DOMAINS = {"paypal.com", "venmo.com", "amazon.com", "apple.com", "google.com", "microsoft.com",
                    "netflix.com", "chase.com", "wellsfargo.com", "hdfcbank.com", "sbi.co.in"}

LEVELS = [(0.6, "high"), (0.3, "medium"), (0.0, "low")]
RULES_CHECKED = 13


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _domain(email: str | None) -> str:
    return email.split("@", 1)[1].lower().strip() if email and "@" in email else ""


def _normalize_leet(s: str) -> str:
    return s.translate(str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"}))


def lookalike_brand(domain: str) -> str | None:
    """Return the impersonated brand if `domain` imitates a well-known brand."""
    if not domain or domain in OFFICIAL_DOMAINS or any(domain.endswith("." + d) for d in OFFICIAL_DOMAINS):
        return None
    flat = _normalize_leet(domain.replace("-", "").replace("_", ""))
    labels = re.split(r"[.]", flat)
    for brand in BRANDS:
        if brand in flat:
            return brand
        for label in labels:
            if len(label) >= 4 and _levenshtein(label, brand) == 1:
                return brand
    return None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def scam_phrases(text: str | None) -> list[str]:
    if not text:
        return []
    low = text.lower()
    return [p for p in SCAM_PHRASES if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low)]


def same_party(t: dict[str, Any], email: str | None, phone: str | None, name: str | None = None) -> bool:
    """Same person if the phone or the email matches (a phone payee also has a linked email)."""
    if phone and t.get("counterparty_phone") == phone:
        return True
    if email and (t.get("counterparty_email") or "").lower() == email:
        return True
    if not email and not phone and name:
        return (t.get("counterparty") or "").strip().lower() == name.strip().lower()
    return False


def _features(amount: float, at: datetime, velocity_24h: int, known_party: bool) -> list[float]:
    hour = at.hour + at.minute / 60
    return [
        math.log1p(max(amount, 0)),
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        float(min(velocity_24h, 10)),
        0.0 if known_party else 1.0,
    ]


def _history_features(history: list[dict[str, Any]]) -> np.ndarray:
    rows = []
    ordered = sorted(history, key=lambda t: t["created_at"])
    seen: set[str] = set()
    times: list[datetime] = []
    for tx in ordered:
        at = _parse(tx["created_at"])
        velocity = sum(1 for t in times if at - t <= timedelta(hours=24))
        key = (tx.get("counterparty_phone") or tx.get("counterparty_email") or tx.get("counterparty") or "").lower()
        rows.append(_features(float(tx["amount"]), at, velocity, key in seen))
        seen.add(key)
        times.append(at)
    return np.array(rows, dtype=float)


def anomaly_score(history: list[dict[str, Any]], amount: float, at: datetime, velocity_24h: int,
                  known_party: bool) -> tuple[float | None, int]:
    """0..1 anomaly score from an Isolation Forest fitted on this user's history."""
    if IsolationForest is None or len(history) < MIN_TRAINING_SAMPLES:
        return None, len(history)
    X = _history_features(history)
    model = IsolationForest(n_estimators=150, contamination="auto", random_state=7)
    model.fit(X)
    raw = -float(model.score_samples(np.array([_features(amount, at, velocity_24h, known_party)]))[0])
    baseline = float(np.median(-model.score_samples(X)))
    # ~0.15 above the baseline counts as clearly unusual
    return float(np.clip((raw - baseline) / 0.15, 0.0, 1.0)), len(history)


def assess(
    direction: str,
    amount: float,
    counterparty_email: str | None,
    counterparty_name: str | None,
    memo: str | None,
    history: list[dict[str, Any]],
    country: str | None = None,
    at: datetime | None = None,
    exclude_id: str | None = None,
    counterparty_phone: str | None = None,
    extra_reasons: list[dict[str, Any]] | None = None,
    verified_identity: bool = False,
) -> dict[str, Any]:
    at = at or datetime.now(timezone.utc)
    same_dir = [t for t in history if t.get("direction") == direction and t.get("id") != exclude_id
                and (t.get("status") or "").upper() not in {"FAILED", "VOIDED", "CREATED", "DRAFT", "REJECTED"}]
    reasons: list[dict[str, Any]] = []

    def add(code: str, label: str, detail: str, weight: float) -> None:
        reasons.append({"code": code, "label": label, "detail": detail, "weight": round(weight, 2)})

    email = (counterparty_email or "").strip().lower() or None
    phone = (counterparty_phone or "").strip() or None
    party_hist = [t for t in same_dir if same_party(t, email, phone, counterparty_name)]
    known_party = bool(party_hist)
    amounts = [float(t["amount"]) for t in same_dir]
    typical = statistics.median(amounts) if amounts else None
    last_hour = [t for t in same_dir if timedelta(0) <= at - _parse(t["created_at"]) <= timedelta(hours=1)]
    last_day = [t for t in same_dir if timedelta(0) <= at - _parse(t["created_at"]) <= timedelta(hours=24)]
    domain = _domain(email)

    if not known_party:
        if direction == "out":
            # bigger first payments to a stranger carry more risk
            weight = 0.08 if amount < 50 else 0.14 if amount < 250 else 0.2
            detail = "You have never sent money to this recipient before."
            if verified_identity:
                weight *= 0.6
                detail += " PayPal confirmed whose account this number belongs to."
            add("new_recipient", "New recipient", detail, weight)
        else:
            add("new_payer", "First-time customer", "This customer has not paid you before.", 0.08)

    if not known_party and len(amounts) >= 5 and amount > 50:
        # compare with this user's own amounts
        p90 = float(np.percentile(amounts, 90))
        p75 = float(np.percentile(amounts, 75))
        noun = "payments" if direction == "out" else "sales"
        if amount > p90 * 1.2:
            add("amount_spike", "Unusually large amount",
                f"Bigger than 9 in 10 of your {noun} (usually up to {fmt_money(p90)}; median {fmt_money(typical)}).", 0.24)
        elif amount > p75 * 1.3:
            add("amount_high", "Higher than usual", f"Larger than most of your {noun} (median {fmt_money(typical)}).", 0.1)

    if direction == "out" and not known_party and len(amounts) < 5:
        # not enough history yet to compare with: use fixed thresholds
        if amount >= 1000:
            add("large_first", "Large payment to someone new",
                f"{fmt_money(amount)} to a recipient you have never paid. Scams often ask for a large first payment.", 0.3)
        elif amount >= 300:
            add("large_first", "Sizeable payment to someone new",
                f"{fmt_money(amount)} to a recipient you have never paid.", 0.14)

    if party_hist:
        avg_party = statistics.mean(float(t["amount"]) for t in party_hist)
        if avg_party > 0 and amount / avg_party >= 2.5 and amount > 50:
            add("party_spike", "Bigger than usual for this contact",
                f"Usually {fmt_money(avg_party)} with this contact; this is {amount / avg_party:.1f}× more.", 0.2)

    phrases = scam_phrases(memo)
    if phrases and direction == "out":
        add("scam_language", "Scam-style wording",
            "The note mentions " + ", ".join(f"“{p}”" for p in phrases[:3]) + " — common in payment scams.", 0.32)

    if domain in DISPOSABLE_DOMAINS:
        add("disposable_email", "Throwaway email address",
            f"{domain} is a disposable email service often used to hide identity.", 0.22)

    brand = lookalike_brand(domain)
    if brand:
        add("lookalike_domain", "Impersonation risk",
            f"The email domain “{domain}” imitates {BRAND_NAMES[brand]} but is not an official {BRAND_NAMES[brand]} domain.", 0.6)

    if direction == "out" and len(last_hour) >= 3:
        add("velocity", "Many payments in a short time", f"{len(last_hour)} payments sent in the last hour.", 0.15)
    if direction == "in":
        same_payer_hour = [t for t in last_hour if t in party_hist]
        if len(same_payer_hour) >= 2:
            add("card_testing", "Rapid repeat payments",
                f"{len(same_payer_hour)} payments from this customer in the last hour (card-testing pattern).", 0.26)

    if direction == "in" and country:
        seen_countries = {t.get("counterparty_country") for t in same_dir if t.get("counterparty_country")}
        if len(same_dir) >= 3 and seen_countries and country not in seen_countries:
            add("new_country", "Payment from a new country",
                f"Buyer country {country} differs from your usual customers ({', '.join(sorted(seen_countries))}).", 0.16)

    if amount >= 500 and amount % 100 == 0 and direction == "out":
        add("round_amount", "Large round amount", "Scam requests often use large round numbers.", 0.06)

    dups = duplicates.find_duplicates(history, direction, amount, email, counterparty_name, memo, at, exclude_id, phone=phone)
    if dups:
        add("duplicate", "Possible duplicate", duplicates.describe(dups, direction) or "", 0.3)

    anomaly, trained_on = anomaly_score(same_dir, amount, at, len(last_day), known_party)
    if anomaly is not None and anomaly >= 0.35:
        add("ml_anomaly", "Unusual for you (ML)",
            f"Isolation Forest trained on your last {trained_on} payments rates this as unusual "
            f"(anomaly {anomaly:.2f}).", 0.22 * anomaly)

    for r in extra_reasons or []:
        add(r["code"], r["label"], r["detail"], r["weight"])

    p_safe = 1.0
    for r in reasons:
        p_safe *= 1 - r["weight"]
    score = round(1 - p_safe, 3)
    level = next(name for threshold, name in LEVELS if score >= threshold)
    reasons.sort(key=lambda r: r["weight"], reverse=True)

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
        "duplicates": dups,
        "model": {
            "type": "isolation_forest+rules",
            "anomaly": None if anomaly is None else round(anomaly, 3),
            "trained_on": trained_on,
            "min_samples": MIN_TRAINING_SAMPLES,
            "rules_checked": RULES_CHECKED,
        },
        "recommendation": recommendation(direction, level, reasons),
    }


def recommendation(direction: str, level: str, reasons: list[dict[str, Any]]) -> str:
    codes = {r["code"] for r in reasons}
    if direction == "out":
        if "duplicate" in codes:
            return "Looks like a repeat of a payment you already made. Only continue if you meant to pay twice."
        if level == "high":
            return ("Contact the recipient through a channel you already trust (not one from this request) before "
                    "sending. Money sent to a scammer is very hard to get back.")
        if level == "medium":
            return "Double-check who you're paying (their PayPal email) and the amount before sending."
        return "Looks normal for you. Safe to continue."
    if "duplicate" in codes:
        return "The customer may have paid twice. Capture one payment and void or refund the other."
    if level == "high":
        return "Hold this payment. PayMinute will keep the authorization un-captured so you can review or void it."
    if level == "medium":
        return "Capturing automatically, but keep an eye on this customer."
    return "Looks normal. Capturing automatically."
