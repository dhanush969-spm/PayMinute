"""Agent marketplace: sellers list items; a buyer agent compares them, bargains and prepares a locked checkout.

Security model:
- A listing always pays the seller's own PayPal-linked phone or PayPal email (taken from their login).
- The seller's lowest price is private; only the seller sees it.
- A deal becomes a one-time quote stored on the server (buyer, listing, payee, price, 10-minute expiry).
  Checkout pays the quote by id, so the browser can never change who is paid or how much.
- An item is reserved atomically before paying, so it can't be sold twice; a failed payout releases it.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import automation, db, ledger
from .ai import phone as phone_ai

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    seller_account TEXT,
    seller_name TEXT,
    payee TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    floor REAL NOT NULL,
    status TEXT NOT NULL,            -- active | reserved | sold
    buyer_account TEXT,
    sold_price REAL,
    transaction_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    buyer_account TEXT,
    payee TEXT NOT NULL,
    seller_name TEXT,
    title TEXT NOT NULL,
    list_price REAL NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL,            -- open | paid | expired | failed
    transaction_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""
TABLES = ["listings", "quotes"]
QUOTE_MINUTES = 10
STOP = {"a", "an", "the", "for", "me", "my", "buy", "find", "get", "shop", "under", "below", "less", "than", "max",
        "at", "most", "budget", "of", "with", "some", "cheap", "good", "best", "i", "want", "need", "to", "please",
        "can", "you", "and", "price", "deal", "purchase", "order", "around", "about", "up", "in"}

# ------------------------------------------------------------------ feature analysis
UNITS = {
    "mp": ("megapixels", 1.0), "megapixel": ("megapixels", 1.0), "gb": ("storage GB", 1.0), "tb": ("storage GB", 1000.0),
    "inch": ("screen inches", 1.0), "in": ("screen inches", 1.0), '"': ("screen inches", 1.0), "mah": ("battery mAh", 1.0),
    "hz": ("refresh Hz", 1.0), "w": ("power W", 1.0), "lens": ("lenses", 1.0), "lenses": ("lenses", 1.0),
    "year": ("warranty years", 1.0), "years": ("warranty years", 1.0), "yr": ("warranty years", 1.0),
    "km": ("km used", -1.0), "hours": ("hours used", -1.0),
}
# longest phrases first, so "like new" isn't read as "new"
CONDITION = [("brand new", 1.0, "brand new"), ("sealed", 1.0, "sealed"), ("like new", 0.85, "like new"), ("mint", 0.85, "mint"),
             ("new", 0.9, "new"), ("excellent", 0.75, "excellent condition"), ("good condition", 0.6, "good condition"),
             ("refurbished", 0.6, "refurbished"), ("used", 0.45, "used"), ("fair", 0.35, "fair condition")]
# accessories PayMinute knows by name (multi-word first); anything else the seller lists is kept as free text
EXTRAS = ["cleaning kit", "memory card", "extra battery", "screen protector", "lens hood", "warranty card", "tripod", "strap",
          "earphones", "adapter", "filter", "cover", "dock", "warranty", "box", "charger", "case", "receipt", "invoice",
          "accessories", "bag", "cable", "manual", "stand", "remote"]
# other ways people write the same accessory
SYNONYMS = {"clean kit": "cleaning kit", "cleaning cloth": "cleaning kit", "sd card": "memory card", "micro sd": "memory card",
            "spare battery": "extra battery", "second battery": "extra battery", "tempered glass": "screen protector",
            "bill": "receipt", "original box": "box", "carry case": "case", "pouch": "case", "power adapter": "adapter",
            "earbuds": "earphones", "neck strap": "strap", "usb cable": "cable", "charging cable": "cable"}
SPLIT = re.compile(r",|;|\+|/|\band\b|\bwith\b|\bplus\b|\bincludes?\b|\bincluding\b|\bcomes\b|\balso\b", re.I)
FILLER = re.compile(r"^(?:it|has|have|having|comes|come|the|a|an|its|original|free|extra free|all|also|full|set of|one|1)\s+", re.I)
FLAWS = ["scratch", "scratches", "crack", "cracked", "dent", "broken", "faulty", "repair", "damaged", "missing", "worn", "stain"]


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOP or w.isdigit():
            continue
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def _canon(phrase: str) -> str:
    p = re.sub(r"\s+", " ", phrase.lower()).strip(" .-:")
    return SYNONYMS.get(p, p)


def _same(a: str, b: str) -> bool:
    """'cleaning kit' ~ 'clean kit', 'charger' ~ 'fast charger'."""
    stem = lambda x: " ".join(w[:5] for w in _canon(x).split())  # noqa: E731
    a2, b2 = stem(a), stem(b)
    return a2 == b2 or (len(a2) > 3 and (a2 in b2 or b2 in a2))


def _free_features(details: str | None, known: list[str]) -> list[str]:
    """Everything else the seller or buyer listed ("cleaning kit", "90 min battery", "noise cancelling")."""
    out: list[str] = []
    for frag in SPLIT.split(details or ""):
        frag = frag.strip(" .-:!")
        for _ in range(3):
            frag = FILLER.sub("", frag).strip()
        low = frag.lower()
        if not frag or not re.search(r"[a-z]", low) or len(frag.split()) > 5:
            continue
        if any(re.search(rf"\b{w}\b", low) for w, _, _ in CONDITION) or any(re.search(rf"\b{x}", low) for x in FLAWS):
            continue
        if re.fullmatch(r"(?:\$|usd\s*|rs\.?\s*|₹\s*)?\d+(?:[.,]\d+)?\s*(?:mp|megapixels?|gb|tb|inch(?:es)?|in|\"|mah|hz|w|lens(?:es)?|"
                        r"years?|yr|km|hours|dollars?|usd)?(?: of warranty| warranty)?", low):
            continue  # a spec or a price, already counted
        if re.search(r"\b(?:lowest|minimum|price|under|budget|asking|selling|for sale)\b|\bmin\s*\$?\d", low):
            continue
        c = _canon(frag)
        if any(_same(c, k) for k in known + out):
            continue
        out.append(c)
    return out


def features(text: str, details: str | None = None) -> dict[str, Any]:
    """Pull specs, condition, extras and flaws out of a listing's title and description.
    `details` (the description) is also read for free-text features PayMinute has no name for."""
    low = (text or "").lower()
    for k, v in SYNONYMS.items():
        low = re.sub(rf"\b{re.escape(k)}\b", v, low)
    specs: dict[str, float] = {}
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mp|megapixels?|gb|tb|inch(?:es)?|in\b|\"|mah|hz|w\b|lens(?:es)?|years?|yr|km|hours)", low):
        unit = m.group(2).rstrip("es") if m.group(2).startswith("inch") else m.group(2)
        unit = {"megapixels": "megapixel", "inche": "inch", "inches": "inch"}.get(unit, unit)
        key, factor = UNITS.get(unit, UNITS.get(m.group(2), (None, 0)))
        if key:
            specs[key] = max(specs.get(key, 0), float(m.group(1)) * abs(factor)) if factor > 0 else float(m.group(1))
    condition = next(((score, label) for word, score, label in CONDITION if re.search(rf"\b{word}\b", low)), (0.55, "condition not stated"))
    extras: list[str] = []
    for e in EXTRAS:  # longest names first, so "warranty card" isn't also counted as "warranty"
        if re.search(rf"\b{e}s?\b", low) and not any(e in x for x in extras):
            extras.append(e)
    flaws = [f for f in FLAWS if re.search(rf"\b{f}\b", low)]
    others = _free_features(details, extras)
    return {"specs": specs, "condition": condition[1], "condition_score": condition[0], "extras": extras + others,
            "known_extras": extras, "flaws": flaws}


def _quality(f: dict[str, Any], peers: list[dict[str, Any]]) -> float:
    """0..1: condition, extras, flaws, and specs compared with the other candidates."""
    spec_score, n = 0.0, 0
    for key, value in f["specs"].items():
        values = [p["specs"][key] for p in peers if key in p["specs"]]
        if len(values) < 2 or max(values) == min(values):
            continue
        rel = (value - min(values)) / (max(values) - min(values))
        spec_score += (1 - rel) if key in {"km used", "hours used"} else rel
        n += 1
    spec_part = spec_score / n if n else 0.5
    return max(0.0, min(1.0, 0.45 * f["condition_score"] + 0.3 * spec_part + 0.05 * min(len(f["extras"]), 4)
                        - 0.15 * min(len(f["flaws"]), 3)))


def _relevance(query: str, text: str) -> float:
    want, have = set(_words(query)), set(_words(text))
    if not want:
        return 0.5
    exact = len(want & have) / len(want)
    partial = sum(1 for w in want - have if any(h.startswith(w[:4]) for h in have if len(w) >= 4)) / len(want)
    return min(1.0, exact + 0.5 * partial)


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ listings
def create_listing(title: str, price: float, payee: str, seller_name: str | None, seller_account: str | None,
                   description: str | None = None, floor: float | None = None) -> dict[str, Any]:
    if not (title or "").strip():
        raise ledger.FlowError("Enter what you're selling.")
    if price <= 0:
        raise ledger.FlowError("Price must be greater than zero.")
    if floor is not None and not 0 < floor <= price:
        raise ledger.FlowError("Your lowest price must be more than 0 and no higher than the price.")
    who = ledger.resolve_receiver(payee)
    if who["type"] == "PHONE_UNLINKED":
        raise ledger.FlowError("Your phone isn't linked to a PayPal account yet, so buyers couldn't pay you.")
    floor = round(floor if floor is not None and 0 < floor <= price else price * 0.9, 2)
    item = {"id": db.new_id("lst_"), "seller_account": seller_account, "seller_name": seller_name, "payee": payee.strip(),
            "title": title.strip()[:80], "description": (description or "").strip()[:400] or None, "price": round(price, 2),
            "floor": floor, "status": "active", "buyer_account": None, "sold_price": None, "transaction_id": None,
            "created_at": db.now_iso()}
    with db.connect() as conn:
        conn.execute("INSERT INTO listings (id, seller_account, seller_name, payee, title, description, price, floor, status, "
                     "buyer_account, sold_price, transaction_id, created_at) VALUES (:id,:seller_account,:seller_name,:payee,"
                     ":title,:description,:price,:floor,:status,:buyer_account,:sold_price,:transaction_id,:created_at)", item)
    return public(item)


def public(item: dict[str, Any]) -> dict[str, Any]:
    """What buyers see: never the seller's lowest price or account ids."""
    return {k: v for k, v in item.items() if k not in {"floor", "seller_account", "buyer_account", "payee"}}


def listings(status: str | None = "active") -> list[dict[str, Any]]:
    if status:
        return _rows("SELECT * FROM listings WHERE status=? ORDER BY created_at DESC", (status,))
    return _rows("SELECT * FROM listings ORDER BY created_at DESC")


def get_listing(listing_id: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM listings WHERE id=?", (listing_id,))
    return rows[0] if rows else None


def my_listings(account: str | None) -> list[dict[str, Any]]:
    if not account:
        return []
    return _rows("SELECT * FROM listings WHERE seller_account=? ORDER BY created_at DESC", (account,))


def remove_listing(listing_id: str, account: str | None) -> None:
    item = get_listing(listing_id)
    if not item:
        raise ledger.FlowError("Listing not found", 404)
    if not account or item["seller_account"] != account:
        raise ledger.FlowError("You can only remove your own listings.", 403)
    with db.connect() as conn:
        conn.execute("DELETE FROM listings WHERE id=? AND status='active'", (listing_id,))


def _trust(payee: str) -> dict[str, Any]:
    """How much PayPal vouches for the seller's account."""
    who = ledger.resolve_receiver(payee)
    if who["phone"]:
        check = phone_ai.analyze(who["phone"], ledger.history())
        status = check["paypal"]["status"]
        score = {"verified": 1.0, "linked": 0.7}.get(status, 0.2) * (1 - check.get("score", 0))
        return {"score": round(score, 2), "label": check["paypal"]["label"], "level": check["level"]}
    return {"score": 0.6, "label": "PayPal email", "level": "low"}


def _spec(key: str, value: float) -> str:
    """'1 lens', '2 lenses', '24 megapixels'."""
    if value == 1 and key.endswith("es") and key.split()[-1] in {"lenses"}:
        key = key[:-2]
    elif value == 1 and key.endswith("s"):
        key = key[:-1]
    return f"{value:g} {key}"


def _masked(payee: str) -> str:
    if "@" in payee:
        name, domain = payee.split("@", 1)
        return f"{name[:2]}•••@{domain}"
    digits = re.sub(r"\D", "", payee)
    return f"••• ••• {digits[-4:]}" if len(digits) > 4 else "•••"


# ------------------------------------------------------------------ understand a one-line request
MONEY = r"(?:\$|usd\s*|rs\.?\s*|₹\s*|inr\s*)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?!\d)(?![.,]\d)(?!\s*(?:mp|megapixels?|gb|tb|inch|in\b|\"|mah|hz|w\b|lens|years?|yr|km|hours|%))"
BUDGET_WORDS = r"(?:under|below|less than|max(?:imum)?|budget(?: of| is)?|up to|upto|within|at most|no more than|around|about|for|at|price(?: is)?|asking|selling for|sell (?:it )?for|list(?: it)? (?:for|at))"
FLOOR_WORDS = r"(?:lowest|minimum|min|at least|not less than|no less than|floor|won'?t go below|bottom)"
LEAD = re.compile(r"^\s*(?:hi|hey|please|pls|for|i\s+(?:want|need|would like)(?:\s+to)?|i'?m\s+(?:looking for|selling)|looking for|"
                  r"find(?:\s+me)?|get(?:\s+me)?|buy(?:\s+me)?|shop(?:\s+for)?|sell(?:ing)?|list(?:ing)?|put\s+up|"
                  r"my|an?|the|old|used item|some)\b[\s,:-]*", re.I)
NOISE = re.compile(r"\b(?:for sale|on the market|please|pls|asap|today)\b", re.I)


def _find_money(text: str, words: str) -> re.Match | None:
    return re.search(rf"\b{words}\s*{MONEY}", text, re.I)


def parse_request(text: str) -> dict[str, Any]:
    """One free-text line -> item name, the features and condition mentioned, price/budget and lowest price.

    e.g. "Selling my Canon EOS camera, 24MP, 2 lenses, like new with box, $300, lowest 260"
         "camera with 2 lenses, like new, under $280"
    """
    raw = (text or "").strip()
    spans: list[tuple[int, int]] = []
    fm = _find_money(raw, FLOOR_WORDS)
    floor = float(fm.group(1).replace(",", "")) if fm else None
    if fm:
        spans.append(fm.span())
    masked = raw[:fm.start()] + " " * (fm.end() - fm.start()) + raw[fm.end():] if fm else raw
    pm = _find_money(masked, BUDGET_WORDS) or re.search(r"(?:\$|usd\s*|rs\.?\s*|₹\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)", masked, re.I)
    if not pm:  # a bare number that isn't a spec or a year, e.g. "..., excellent, 650"
        bare = [m for m in re.finditer(r"(?<![\w.$-])(\d{2,6}(?:\.\d{1,2})?)(?![\w%-])(?![.,]\d)(?!\s*(?:mp|megapixels?|gb|tb|inch|in\b|\"|mah|hz|w\b|lens|years?|yr|km|hours|gears?|speed))",
                                        masked, re.I) if not 1990 <= float(m.group(1)) <= 2035]
        pm = bare[-1] if bare else None
    price = float(pm.group(1).replace(",", "")) if pm else None
    if pm:
        spans.append(pm.span())
    rest = raw
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    # the item is the first phrase before a comma / "with" / a spec
    chunk = re.split(r",|;|\.\s|\bwith\b|\bwhich\b|\bthat\b|\b\d+(?:\.\d+)?\s*(?:mp|megapixels?|gb|tb|inch(?:es)?|\"|mah|hz|w|"
                     r"lens(?:es)?|years?|yr|km|hours)\b", rest, maxsplit=1, flags=re.I)[0]
    head = chunk
    for _ in range(4):
        head = LEAD.sub("", head)
    head = NOISE.sub(" ", head)
    for word, _, _ in CONDITION:
        head = re.sub(rf"\b{word}\b", " ", head, flags=re.I)
    title = re.sub(r"\s+", " ", head).strip(" ,.-:")
    title = " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in title.split())[:80] or None
    details = re.sub(r"\s+", " ", NOISE.sub(" ", rest[len(chunk):])).strip(" ,.-:")
    details = re.sub(r"^(?:with|and|that|which|is|it'?s)\s+", "", details, flags=re.I).strip(" ,.-:") or None
    f = features(raw, details)
    wanted = ([f["condition"]] if f["condition"] != "condition not stated" else []) + \
             [_spec(k, v) for k, v in f["specs"].items()] + f["extras"]
    return {"title": title, "details": details, "price": price, "floor": floor if floor and (not price or floor <= price) else None,
            "features": f, "wanted": wanted, "text": raw}


# ------------------------------------------------------------------ AI price estimate
# typical used-market prices (USD) when PayMinute has no similar listings to learn from
BASE_PRICES = {
    "camera": 450, "lens": 250, "phone": 450, "iphone": 600, "laptop": 700, "macbook": 900, "tablet": 300, "ipad": 350,
    "tv": 450, "monitor": 180, "headphone": 120, "earbud": 80, "speaker": 90, "watch": 180, "bike": 300, "bicycle": 300,
    "guitar": 250, "piano": 400, "keyboard": 60, "mouse": 30, "desk": 120, "chair": 90, "sofa": 350, "lamp": 35,
    "console": 350, "playstation": 400, "xbox": 350, "drone": 400, "printer": 120, "router": 60, "book": 15,
    "shoe": 60, "bag": 60, "jacket": 70, "fridge": 400, "microwave": 70, "coffee": 90, "mixer": 60,
}
COND_FACTOR = {"brand new": 1.0, "sealed": 1.0, "new": 0.95, "like new": 0.85, "mint": 0.85, "excellent condition": 0.78,
               "good condition": 0.65, "refurbished": 0.7, "used": 0.6, "fair condition": 0.45, "condition not stated": 0.65}


def _worth(f: dict[str, Any]) -> float:
    """A relative value for an item's condition, extras and flaws (1.0 = typical used item)."""
    return max(0.2, COND_FACTOR.get(f["condition"], 0.65) / 0.65 * (1 + 0.04 * min(len(f["extras"]), 5))
               * (1 - 0.12 * min(len(f["flaws"]), 3)))


def _round_price(x: float) -> float:
    return float(round(x)) if x < 50 else float(round(x / 5) * 5)


def suggest_price(title: str, details: str | None = None, exclude_id: str | None = None) -> dict[str, Any] | None:
    """Estimate a fair price from similar listings on PayMinute (adjusted for condition and features),
    or from typical market prices for the item type."""
    if not (title or "").strip():
        return None
    f = features(f"{title} {details or ''}", details)
    mine = _worth(f)
    comps = []
    for x in listings(None):
        if x["id"] == exclude_id or _relevance(title, x["title"]) < 0.5:
            continue
        fx = features(f"{x['title']} {x['description'] or ''}", x["description"])
        paid = x["sold_price"] if x["status"] == "sold" and x["sold_price"] else x["price"] * 0.93  # asks run high
        adj = (mine / _worth(fx)) ** 0.5  # condition, extras and flaws, damped
        for k, v in f["specs"].items():  # more megapixels, storage, lenses... is worth a bit more
            if fx["specs"].get(k):
                adj *= 1 + 0.12 * (v - fx["specs"][k]) / max(v, fx["specs"][k])
        comps.append(paid * max(0.6, min(1.6, adj)))
    if comps:
        comps.sort()
        mid = comps[len(comps) // 2] if len(comps) % 2 else (comps[len(comps) // 2 - 1] + comps[len(comps) // 2]) / 2
        basis = f"Based on {len(comps)} similar listing{'s' if len(comps) > 1 else ''} on PayMinute, adjusted for condition and features"
    else:
        words = _words(title)
        base = next((BASE_PRICES[w] for w in words if w in BASE_PRICES), None)
        if base is None:
            return None
        mid = base * mine ** 0.5 * (1 + 0.03 * len(f["specs"]))
        basis = f"Based on typical prices for a {next(w for w in words if w in BASE_PRICES)} in this condition"
    price = _round_price(mid)
    return {"price": price, "low": _round_price(mid * 0.85), "high": _round_price(mid * 1.15), "basis": basis,
            "comparables": len(comps), "condition": f["condition"]}


# ------------------------------------------------------------------ compare
def _match(wanted: dict[str, Any] | None, f: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Which of the buyer's requested features this listing has, and which it lacks."""
    if not wanted:
        return [], []
    has, lacks = [], []
    for k, v in wanted["specs"].items():
        got = f["specs"].get(k)
        if got is None:
            lacks.append(f"no {k} listed" if k not in {"lenses"} else "lens count not listed")
        elif (got < v) if k not in {"km used", "hours used"} else (got > v):
            lacks.append(f"only {_spec(k, got)}" if k not in {"km used", "hours used"} else f"{_spec(k, got)}")
        else:
            has.append(_spec(k, got))
    if wanted["condition"] != "condition not stated":
        if f["condition_score"] + 0.01 >= wanted["condition_score"]:
            has.append(f["condition"])
        else:
            lacks.append(f"{f['condition']}, not {wanted['condition']}")
    for e in wanted["extras"]:
        got = next((x for x in f["extras"] if _same(e, x)), None)
        (has if got else lacks).append(got or f"no {e}")
    return has, lacks


def compare(query: str, budget: float | None = None, account: str | None = None,
            wanted: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rank listings for a request: relevance, requested features, quality, value for money and seller trust.
    The seller's private lowest price is never used here."""
    pool = [x for x in listings() if not (account and x["seller_account"] == account)]
    scored = []
    for item in pool:
        text = f"{item['title']} {item['description'] or ''}"
        rel = _relevance(query, text)
        if rel < 0.34:
            continue
        scored.append({"item": item, "relevance": rel, "features": features(text, item["description"])})
    if not scored:
        return []
    peers = [s["features"] for s in scored]
    prices = [s["item"]["price"] for s in scored]
    avg = sum(prices) / len(prices)
    out = []
    for s in scored:
        item, f = s["item"], s["features"]
        quality = _quality(f, peers)
        trust = _trust(item["payee"])
        has, lacks = _match(wanted, f)
        asked = len(has) + len(lacks)
        fit = len(has) / asked if asked else 1.0
        price_vs_avg = (item["price"] - avg) / avg if avg else 0
        far = budget is not None and item["price"] > budget * 1.5
        score = (0.45 * s["relevance"] + 0.15 * fit + 0.2 * quality + 0.1 * trust["score"]
                 + 0.1 * max(0.0, 1 - max(0.0, price_vs_avg)))
        pros, cons = [], []
        if f["condition_score"] >= 0.85:
            pros.append(f["condition"])
        elif f["condition_score"] < 0.5:
            cons.append(f["condition"])
        pros += [_spec(k, v) for k, v in f["specs"].items() if k not in {"km used", "hours used"}]
        pros += f["extras"][:3]
        cons += [f"mentions “{x}”" for x in f["flaws"]]
        cons += [f"you asked for more: {x}" for x in lacks]
        if price_vs_avg <= -0.1:
            pros.append(f"{abs(price_vs_avg):.0%} below the average price")
        elif price_vs_avg >= 0.15:
            cons.append(f"{price_vs_avg:.0%} above the average price")
        if trust["label"] == "Verified by PayPal":
            pros.append("seller verified by PayPal")
        elif trust["level"] == "high":
            cons.append("seller account looks risky")
        if far:
            cons.append("listed far above your budget")
        out.append({**public(item), "score": round(score, 3), "quality": round(quality, 2), "fit": round(fit, 2),
                    "relevance": round(s["relevance"], 2), "trust": trust, "features": f, "has": has, "lacks": lacks,
                    "pros": pros, "cons": cons, "reachable": not far and trust["level"] != "high"})
    return sorted(out, key=lambda r: (r["reachable"], r["score"]), reverse=True)


def search(query: str, budget: float | None = None, account: str | None = None) -> list[dict[str, Any]]:
    return compare(query, budget, account)


def _reason(pick: dict[str, Any], others: list[dict[str, Any]]) -> str:
    bits = pick["pros"][:4] or ["closest match to what you asked for"]
    beaten = [o["title"] for o in others if o["id"] != pick["id"]][:2]
    return (f"Picked “{pick['title']}”: " + ", ".join(bits) + "."
            + (f" Compared with {', '.join(f'“{b}”' for b in beaten)}." if beaten else ""))


def _leverage(pick: dict[str, Any], others: list[dict[str, Any]], fair: float | None, budget: float) -> list[str]:
    """What the buyer agent can point at to get the price down: flaws, features it lacks, a cheaper
    listing of similar quality, the fair price and the budget."""
    out = [f"it has {x[len('mentions “'):].rstrip('”')}" for x in pick["cons"] if x.startswith("mentions")]
    out += [f"it's missing what I asked for ({x})" for x in pick["lacks"][:2]]
    if pick["features"]["condition_score"] < 0.7 and pick["features"]["condition"] != "condition not stated":
        out.append(f"it's {pick['features']['condition']}")
    if fair and pick["price"] > fair * 1.03:
        out.append(f"similar items go for about {fair:.2f}")
    similar = [o for o in others if o["id"] != pick["id"] and o["price"] < pick["price"] and o["quality"] >= pick["quality"] - 0.1]
    if similar:
        c = min(similar, key=lambda o: o["price"])
        out.append(f"there's a comparable “{c['title']}” listed at {c['price']:.2f}")
    if pick["price"] > budget:
        out.append(f"my budget is {budget:.2f}")
    if len(out) < 3:
        out += ["I'm comparing other listings too", "I can pay right away through PayPal"][: 3 - len(out)]
    return out


def _strengths(item: dict[str, Any]) -> list[str]:
    """What the seller agent defends its price with, as short clauses."""
    f = features(f"{item['title']} {item['description'] or ''}", item["description"])
    out = []
    if f["condition_score"] >= 0.75:
        out.append(f"it's {f['condition']}" + (f" with {' and '.join(f['extras'][:2])}" if f["extras"] else ""))
    elif f["extras"]:
        out.append(f"it comes with the {' and '.join(f['extras'][:2])}")
    for k, v in list(f["specs"].items())[:2]:
        if k == "lenses":
            out.append(f"it comes with {_spec(k, v)}")
        elif k == "warranty years":
            out.append(f"it still has {_spec(k, v)} of warranty")
        elif k not in {"km used", "hours used"}:
            out.append(f"it has {_spec(k, v)}")
    return out or ["it works well", "it's in demand"]


# ------------------------------------------------------------------ buy: compare, bargain, quote
def _budget_for(req: dict[str, Any], budget: float | None) -> tuple[float | None, dict[str, Any] | None]:
    fair = suggest_price(req["title"] or req["text"], req["details"])
    return (budget or req["price"] or (fair or {}).get("price")), fair


def shop_events(prompt: str, budget: float | None, account: str | None, pause: float = 0.0):
    """The whole shopping run as a stream of events (for the live view), ending with a "done" event."""
    import time
    req = parse_request(prompt)
    query = req["title"] or prompt
    budget, fair_req = _budget_for(req, budget)
    yield {"type": "understood", "request": {k: req[k] for k in ("title", "details", "wanted", "price")},
           "budget": budget, "fair": fair_req}
    if not budget or budget <= 0:
        yield {"type": "done", "outcome": "need_budget",
               "summary": "Tell me your maximum budget, e.g. “camera with 2 lenses under $300”."}
        return
    yield {"type": "stage", "text": "Searching listings and comparing features"}
    candidates = compare(query, budget, account, req["features"])
    yield {"type": "candidates", "candidates": candidates[:6]}
    if not candidates:
        yield {"type": "done", "outcome": "not_found", "summary": f"Nothing for sale matches “{query}”."}
        return
    tried = 0
    for pick in [c for c in candidates if c["reachable"]][:2]:  # if the best one won't sell, try the next best
        tried += 1
        item = get_listing(pick["id"])
        if not item or item["status"] != "active":
            continue
        fair = (suggest_price(item["title"], item["description"], exclude_id=item["id"]) or {}).get("price") \
            or (fair_req or {}).get("price")
        yield {"type": "pick", "listing": pick, "fair": fair, "reason": _reason(pick, candidates),
               "text": f"Bargaining with the seller of “{item['title']}”" + (f" (AI fair price ${fair:.2f})" if fair else "")}
        buyer = automation.BuyerAgent(budget, fair, _leverage(pick, candidates, fair, budget))
        seller = automation.SellerAgent(item["price"], item["floor"], _strengths(item))
        price = None
        for step in automation.bargain_steps(buyer, seller, item["title"]):
            if isinstance(step, tuple):
                price = step[1]
                break
            if pause:
                time.sleep(pause)
            yield {"type": "message", **step}
        if price is not None:
            quote = _create_quote(item, account, price)
            saved = item["price"] - price
            yield {"type": "done", "outcome": "quote", "quote": quote, "listing": pick, "price": price, "fair": fair,
                   "reason": _reason(pick, candidates),
                   "summary": f"Agreed ${price:.2f} for “{item['title']}” (listed ${item['price']:.2f}"
                              + (f", you save ${saved:.2f}). " if saved >= 0.01 else "). ") + "Opening checkout."}
            return
        yield {"type": "stage", "text": f"No deal on “{item['title']}”: the seller held at {seller.ask:.2f}."}
    yield {"type": "done", "outcome": "no_deal",
           "summary": (f"No seller would go down to your budget of {budget:.2f}. Raise your budget and try again."
                       if tried else f"Found {len(candidates)} listing(s), but none from a trusted seller near your budget.")}


def shop(query: str, budget: float | None, account: str | None = None) -> dict[str, Any]:
    """Compare listings, let the buyer agent bargain with the seller's agent, and return a locked checkout quote."""
    if budget is not None and budget <= 0:
        raise ledger.FlowError("Budget must be greater than zero.")
    out: dict[str, Any] = {"candidates": [], "transcript": []}
    for ev in shop_events(query, budget, account):
        if ev["type"] == "candidates":
            out["candidates"] = ev["candidates"]
        elif ev["type"] == "message":
            out["transcript"].append({k: ev[k] for k in ("agent", "action", "amount", "text")})
        elif ev["type"] == "understood":
            out["understood"] = ev
        elif ev["type"] == "pick":
            out["listing"] = ev["listing"]
        elif ev["type"] == "done":
            out.update({k: v for k, v in ev.items() if k != "type"})
    return out


def _create_quote(item: dict[str, Any], account: str | None, price: float) -> dict[str, Any]:
    now = _now()
    q = {"id": db.new_id("quo_"), "listing_id": item["id"], "buyer_account": account, "payee": item["payee"],
         "seller_name": item["seller_name"], "title": item["title"], "list_price": item["price"], "price": round(price, 2),
         "status": "open", "transaction_id": None, "created_at": now.isoformat(),
         "expires_at": (now + timedelta(minutes=QUOTE_MINUTES)).isoformat()}
    with db.connect() as conn:
        conn.execute("INSERT INTO quotes (id, listing_id, buyer_account, payee, seller_name, title, list_price, price, status, "
                     "transaction_id, created_at, expires_at) VALUES (:id,:listing_id,:buyer_account,:payee,:seller_name,"
                     ":title,:list_price,:price,:status,:transaction_id,:created_at,:expires_at)", q)
    return quote_view(q)


def _get_quote(quote_id: str, account: str | None) -> dict[str, Any]:
    rows = _rows("SELECT * FROM quotes WHERE id=?", (quote_id,))
    if not rows:
        raise ledger.FlowError("Checkout not found.", 404)
    q = rows[0]
    if q["buyer_account"] != account:
        raise ledger.FlowError("This checkout belongs to another account.", 403)
    return q


def quote_view(q: dict[str, Any]) -> dict[str, Any]:
    expired = datetime.fromisoformat(q["expires_at"]) < _now()
    status = "expired" if q["status"] == "open" and expired else q["status"]
    return {"id": q["id"], "listing_id": q["listing_id"], "title": q["title"], "seller_name": q["seller_name"],
            "pay_to": _masked(q["payee"]), "list_price": q["list_price"], "price": q["price"], "status": status,
            "expires_at": q["expires_at"], "transaction_id": q["transaction_id"]}


def get_quote(quote_id: str, account: str | None) -> dict[str, Any]:
    q = _get_quote(quote_id, account)
    view = quote_view(q)
    check = ledger.assess_payout(q["payee"], q["price"], f"Bought: {q['title']}", q["seller_name"])
    view["risk"] = {k: check.get(k) for k in ("score", "level", "reasons", "recommendation")}
    view["recipient"] = {"type": check["recipient"]["type"], "verified": (check.get("phone_check") or {}).get("paypal", {}).get("label")}
    return view


def pay_quote(quote_id: str, account: str | None, acknowledge_risk: bool = False) -> dict[str, Any]:
    """Pay a locked quote. Payee and amount come only from the server-side quote."""
    q = _get_quote(quote_id, account)
    if q["status"] == "paid":
        return {"outcome": "paid", "summary": "Already paid.", "quote": quote_view(q)}
    if q["status"] != "open" or datetime.fromisoformat(q["expires_at"]) < _now():
        raise ledger.FlowError("This checkout expired. Ask the agent to find it again.", 410)
    with db.connect() as conn:  # reserve: only one buyer can win
        took = conn.execute("UPDATE listings SET status='reserved', buyer_account=? WHERE id=? AND status='active'",
                            (account, q["listing_id"])).rowcount
    if not took:
        raise ledger.FlowError("Sorry, this item was just sold to someone else.", 409)
    try:
        res = ledger.send_payout(q["payee"], q["price"], f"Bought: {q['title']}", q["seller_name"],
                                 client_ref=f"quote-{q['id']}", acknowledge_risk=acknowledge_risk, source="agent")
    except ledger.FlowError:
        with db.connect() as conn:
            conn.execute("UPDATE listings SET status='active', buyer_account=NULL WHERE id=? AND status='reserved'", (q["listing_id"],))
        raise
    tx = res["transaction"]
    ok = (tx.get("status") or "").upper() not in {"FAILED", "DENIED", "BLOCKED"}
    with db.connect() as conn:
        conn.execute("UPDATE quotes SET status=?, transaction_id=? WHERE id=?", ("paid" if ok else "failed", tx["id"], q["id"]))
        conn.execute("UPDATE listings SET status=?, sold_price=?, transaction_id=? WHERE id=?",
                     ("sold" if ok else "active", q["price"] if ok else None, tx["id"] if ok else None, q["listing_id"]))
    automation._log("shop", q["id"], "paid" if ok else "failed",
                    f"Bought “{q['title']}” from {q['seller_name'] or 'the seller'} for {q['price']:.2f}. PayPal status: {tx['status']}.",
                    tx["id"], q["price"], True)
    q = _get_quote(quote_id, account)
    return {"outcome": "paid" if ok else "failed", "transaction": tx, "quote": quote_view(q),
            "summary": f"Paid {q['price']:.2f} to {q['seller_name'] or 'the seller'} through PayPal. Status: {tx['status']}."}
