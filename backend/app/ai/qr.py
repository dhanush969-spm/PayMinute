"""QR code safety check."""
from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .llm import LLMError, get_llm
from .risk import BRAND_NAMES, lookalike_brand, scam_phrases

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "ow.ly", "cutt.ly", "rb.gy", "shorturl.at",
              "rebrand.ly", "t.ly", "s.id", "tiny.cc"}
RISKY_TLDS = {"zip", "mov", "xyz", "top", "click", "country", "gq", "tk", "ml", "cf", "ga", "work", "rest", "cam",
              "loan", "men", "bid", "icu", "buzz", "monster"}
PAYPAL_HOSTS = {"paypal.com", "www.paypal.com", "paypal.me", "www.paypal.me", "sandbox.paypal.com",
                "www.sandbox.paypal.com", "py.pl"}
CREDENTIAL_WORDS = ["login", "signin", "sign-in", "verify", "verification", "account-update", "password", "secure",
                    "unlock", "suspended", "confirm", "wallet-connect", "kyc"]


def _signal(code: str, label: str, detail: str, weight: float) -> dict[str, Any]:
    return {"code": code, "label": label, "detail": detail, "weight": weight}


def analyze(payload: str) -> dict[str, Any]:
    text = (payload or "").strip()
    signals: list[dict[str, Any]] = []
    parsed_payment: dict[str, Any] | None = None
    kind = "text"

    if re.match(r"^upi://", text, re.I):
        kind = "upi"
        q = {k.lower(): v[0] for k, v in parse_qs(urlparse(text).query).items()}
        parsed_payment = {"scheme": "UPI", "payee": q.get("pa"), "name": q.get("pn"), "amount": q.get("am"),
                          "note": q.get("tn"), "currency": q.get("cu", "INR")}
        signals.append(_signal("not_paypal", "Not a PayPal payment",
                               "This is a UPI request. PayMinute can check it, but paying happens in your UPI app, not PayPal.", 0.1))
        if q.get("am"):
            signals.append(_signal("prefilled_amount", "Amount is pre-filled",
                                   f"The QR fixes the amount at {q.get('am')} {q.get('cu', 'INR')}. Make sure you expected to pay this.", 0.12))
        if q.get("pa") and q.get("pn"):
            handle = q["pa"].split("@")[0].lower()
            name_tokens = [t for t in re.split(r"\W+", q["pn"].lower()) if len(t) > 2]
            if name_tokens and not any(t in handle for t in name_tokens):
                signals.append(_signal("name_mismatch", "Name and ID don't match",
                                       f"Display name “{q['pn']}” doesn't appear in the payee ID “{q['pa']}”.", 0.18))
        for p in scam_phrases(q.get("tn")):
            signals.append(_signal("scam_language", "Scam-style note", f"The note mentions “{p}”.", 0.3))
        if "receive" in (q.get("tn") or "").lower() or "refund" in (q.get("tn") or "").lower():
            signals.append(_signal("receive_trick", "“Scan to receive” trick",
                                   "You never need to scan a QR or enter a PIN to RECEIVE money. This is a common scam.", 0.45))
    elif re.match(r"^(https?://|www\.)", text, re.I) or re.match(r"^[\w-]+(\.[\w-]+)+/", text):
        kind = "url"
        url = text if re.match(r"^https?://", text, re.I) else "https://" + text
        u = urlparse(url)
        host = (u.hostname or "").lower()
        path = unquote(u.path or "")
        if u.scheme == "http":
            signals.append(_signal("no_https", "Not encrypted", "The link uses http:// instead of https://.", 0.15))
        if "@" in (u.netloc or ""):
            signals.append(_signal("userinfo", "Hidden destination",
                                   "The link contains '@', which hides the real website it opens.", 0.4))
        try:
            ipaddress.ip_address(host)
            signals.append(_signal("ip_host", "Raw IP address", "Real payment sites don't use bare IP addresses.", 0.35))
        except ValueError:
            pass
        if host.startswith("xn--") or ".xn--" in host:
            signals.append(_signal("punycode", "Disguised characters",
                                   "The domain uses look-alike Unicode characters (punycode).", 0.4))
        if host in SHORTENERS:
            signals.append(_signal("shortener", "Shortened link",
                                   f"{host} hides the final destination. PayMinute can't verify where it goes.", 0.25))
        tld = host.rsplit(".", 1)[-1] if "." in host else ""
        if tld in RISKY_TLDS:
            signals.append(_signal("risky_tld", "Unusual domain ending", f".{tld} domains are frequently abused.", 0.15))
        is_paypal = host in PAYPAL_HOSTS or host.endswith(".paypal.com")
        brand = lookalike_brand(host)
        if not is_paypal and brand:
            signals.append(_signal("lookalike", f"Fake {BRAND_NAMES[brand]} site",
                                   f"“{host}” looks like {BRAND_NAMES[brand]} but is not an official domain.", 0.6))
        if not is_paypal and "paypal" in (path + (u.query or "")).lower():
            signals.append(_signal("brand_in_path", "Uses PayPal's name off-site",
                                   "The link mentions PayPal but doesn't go to paypal.com.", 0.3))
        if host.count(".") >= 4:
            signals.append(_signal("deep_subdomain", "Very long domain", f"{host} has many sub-domains, a phishing trick.", 0.12))
        if any(w in (host + path).lower() for w in CREDENTIAL_WORDS) and not is_paypal:
            signals.append(_signal("credential_path", "Asks you to log in or verify",
                                   "Payment QR codes shouldn't ask you to log in or verify your account.", 0.3))
        if is_paypal and host in {"paypal.me", "www.paypal.me", "py.pl"}:
            parts = [p for p in path.split("/") if p]
            if parts:
                amount = None
                currency = None
                if len(parts) > 1:
                    m = re.match(r"^(\d+(?:\.\d{1,2})?)([A-Za-z]{3})?$", parts[1])
                    if m:
                        amount, currency = m.group(1), (m.group(2) or "").upper() or None
                parsed_payment = {"scheme": "PayPal.Me", "payee": parts[0], "amount": amount, "currency": currency,
                                  "link": f"https://www.paypal.me/{parts[0]}"}
        elif is_paypal and "/invoice/" in path:
            parsed_payment = {"scheme": "PayPal invoice", "link": url}
        if is_paypal and not signals:
            signals.append(_signal("official", "Official PayPal domain", f"{host} belongs to PayPal.", 0.0))
    else:
        for p in scam_phrases(text):
            signals.append(_signal("scam_language", "Scam-style text", f"The QR text mentions “{p}”.", 0.3))

    p_safe = 1.0
    for s in signals:
        p_safe *= 1 - s["weight"]
    score = round(1 - p_safe, 3)
    level = "high" if score >= 0.5 else ("medium" if score >= 0.2 else "low")
    verdict = {
        "high": "This QR code shows strong signs of a scam.",
        "medium": "Check the details with the person you're paying before you continue.",
        "low": "Still confirm the recipient's name before paying.",
    }[level]
    return {"kind": kind, "payload": text[:500], "score": score, "level": level, "verdict": verdict,
            "signals": sorted(signals, key=lambda s: s["weight"], reverse=True), "payment": parsed_payment}


def ai_explain(result: dict[str, Any]) -> str | None:
    llm = get_llm()
    if not llm or result["level"] == "low":
        return None
    try:
        res = llm.chat([{"role": "user", "content": f"QR check result: {result}"}],
                       system="Explain to a non-technical person, in at most 2 sentences, why this QR code may be "
                              "dangerous and what to do. Be calm and concrete.", max_tokens=150)
        return res.get("content") or None
    except LLMError:
        return None
