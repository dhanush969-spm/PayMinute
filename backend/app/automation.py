"""Payment agents that act on the user's behalf: job payouts, scheduled payments and agent-to-agent deals.

Every agent payment goes through the same checks as a manual one (phone verification, risk score,
duplicate check) plus spending limits. Low-risk payments inside the limits are sent automatically;
anything else waits for the user's approval.
"""
from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, ledger

log = logging.getLogger("payminute.automation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    payee TEXT NOT NULL,
    payee_name TEXT,
    amount REAL NOT NULL,
    status TEXT NOT NULL,            -- open | paid | needs_approval | blocked | failed
    detail TEXT,
    transaction_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    payee TEXT NOT NULL,
    payee_name TEXT,
    amount REAL NOT NULL,
    note TEXT,
    every TEXT NOT NULL,             -- once | daily | weekly | monthly
    next_run TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_result TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,             -- job | schedule | deal
    ref TEXT,
    outcome TEXT NOT NULL,           -- paid | needs_approval | blocked | failed | no_deal
    summary TEXT NOT NULL,
    transaction_id TEXT,
    amount REAL,
    approved INTEGER NOT NULL DEFAULT 0,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id TEXT PRIMARY KEY,
    item TEXT NOT NULL,
    payee TEXT NOT NULL,
    payee_name TEXT,
    price REAL NOT NULL,
    status TEXT NOT NULL,            -- pending | paid
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
TABLES = ["jobs", "schedules", "agent_runs", "deals", "automation_settings"]
DEFAULTS = {"auto_pay_max": 200.0, "daily_limit": 500.0, "allow_medium_risk": False, "enabled": True}
EVERY = {"once", "daily", "weekly", "monthly"}


def init() -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)


def migrate(conn) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
    if "amount" not in cols:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN amount REAL")
    if "approved" not in cols:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
    # each job, schedule, deal and log line belongs to the account that created it
    for table in ("jobs", "schedules", "deals", "agent_runs"):
        if "owner_account" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_account TEXT")


def _mine(alias: str = "") -> tuple[str, tuple]:
    """SQL filter for the signed-in user's rows (no filter for PayMinute's own background work)."""
    acct = db.current_account()
    return (f" AND {alias}owner_account=?", (acct,)) if acct else ("", ())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ------------------------------------------------------------------ settings and limits
def settings() -> dict[str, Any]:
    out = dict(DEFAULTS)
    acct = db.current_account()
    for r in _rows("SELECT key, value FROM automation_settings"):
        key = r["key"]
        if acct and key.startswith(f"{acct}|"):
            out[key.split("|", 1)[1]] = json.loads(r["value"])
        elif not acct and "|" not in key:
            out[key] = json.loads(r["value"])
    return out


def update_settings(**values: Any) -> dict[str, Any]:
    with db.connect() as conn:
        for k, v in values.items():
            if k in DEFAULTS and v is not None:
                conn.execute("INSERT INTO automation_settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (db.scoped(k), json.dumps(v)))
    return settings()


def spent_today() -> float:
    """What agents paid automatically today (payments the user approved don't count)."""
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    where, args = _mine("r.")
    rows = _rows("SELECT r.amount, t.status FROM agent_runs r LEFT JOIN transactions t ON t.id = r.transaction_id "
                 "WHERE r.outcome='paid' AND r.approved=0 AND r.at >= ?" + where, (start, *args))
    return round(sum(float(r["amount"] or 0) for r in rows
                     if (r["status"] or "").upper() not in {"FAILED", "CANCELLED", "RETURNED"}), 2)


def _log(agent: str, ref: str | None, outcome: str, summary: str, tx_id: str | None = None,
         amount: float | None = None, approved: bool = False) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO agent_runs (agent, ref, outcome, summary, transaction_id, amount, approved, at, owner_account) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (agent, ref, outcome, summary, tx_id, amount, 1 if approved else 0,
                                                   db.now_iso(), db.current_account()))


def runs(limit: int = 30) -> list[dict[str, Any]]:
    where, args = _mine()
    return _rows("SELECT * FROM agent_runs WHERE 1=1" + where + " ORDER BY id DESC LIMIT ?", (*args, limit))


def pay(agent: str, ref: str, payee: str, amount: float, note: str, name: str | None, approved: bool = False) -> dict[str, Any]:
    """Check, then pay. Returns {outcome, summary, risk, transaction?}."""
    cfg = settings()
    try:
        check = ledger.assess_payout(payee, amount, note, name)
    except ledger.FlowError as exc:
        _log(agent, ref, "blocked", str(exc))
        return {"outcome": "blocked", "summary": str(exc), "risk": None}
    who = check["recipient"]
    shown = name or who.get("name") or who.get("display")
    phone = check.get("phone_check")
    reasons: list[str] = []
    if who["type"] == "PHONE_UNLINKED":
        reasons.append("the number isn't linked to a PayPal account")
    if not approved:
        if not cfg["enabled"]:
            reasons.append("automatic payments are paused")
        if amount > cfg["auto_pay_max"]:
            reasons.append(f"it's above your automatic limit of {cfg['auto_pay_max']:.2f}")
        if spent_today() + amount > cfg["daily_limit"]:
            reasons.append(f"it would pass your daily agent limit of {cfg['daily_limit']:.2f}")
        if check["level"] == "high" or (check["level"] == "medium" and not cfg["allow_medium_risk"]):
            reasons.append(f"the risk check says {check['level']} ({', '.join(r['label'] for r in check['reasons'][:3])})")
    if reasons:
        blocked = who["type"] == "PHONE_UNLINKED"
        outcome = "blocked" if blocked else "needs_approval"
        summary = f"Didn't pay {amount:.2f} to {shown}: " + "; ".join(reasons) + "."
        _log(agent, ref, outcome, summary)
        return {"outcome": outcome, "summary": summary, "risk": _risk_brief(check), "phone": phone}
    try:
        res = ledger.send_payout(payee, amount, note, name, client_ref=f"{agent}-{ref}"[:64],
                                 acknowledge_risk=True, source="agent")
    except ledger.FlowError as exc:
        _log(agent, ref, "failed", str(exc))
        return {"outcome": "failed", "summary": str(exc), "risk": _risk_brief(check)}
    tx = res["transaction"]
    summary = f"Paid {amount:.2f} to {shown} through PayPal Payouts. PayPal status: {tx['status']}."
    _log(agent, ref, "paid", summary, tx["id"], amount, approved)
    return {"outcome": "paid", "summary": summary, "risk": _risk_brief(check), "transaction": tx}


def _risk_brief(check: dict[str, Any]) -> dict[str, Any]:
    return {k: check.get(k) for k in ("score", "level", "reasons", "recommendation")}


# ------------------------------------------------------------------ jobs: pay when marked complete
def create_job(title: str, payee: str, amount: float, payee_name: str | None = None) -> dict[str, Any]:
    if amount <= 0:
        raise ledger.FlowError("Amount must be greater than zero.")
    ledger.resolve_receiver(payee)  # validates the phone / email early
    job = {"id": db.new_id("job_"), "title": title.strip()[:120], "payee": payee.strip(), "payee_name": payee_name,
           "amount": round(amount, 2), "status": "open", "detail": None, "transaction_id": None,
           "created_at": db.now_iso(), "completed_at": None, "owner_account": db.current_account()}
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs (id, title, payee, payee_name, amount, status, detail, transaction_id, created_at, "
                     "completed_at, owner_account) VALUES (:id,:title,:payee,:payee_name,:amount,:status,:detail,:transaction_id,"
                     ":created_at,:completed_at,:owner_account)", job)
    return job


def jobs() -> list[dict[str, Any]]:
    where, args = _mine()
    return _rows("SELECT * FROM jobs WHERE 1=1" + where + " ORDER BY created_at DESC", args)


def find_job(text: str) -> dict[str, Any] | None:
    open_jobs = [j for j in jobs() if j["status"] in {"open", "needs_approval"}]
    low = text.lower()
    best = None
    for j in open_jobs:
        words = [w for w in j["title"].lower().split() if len(w) > 2]
        hits = sum(1 for w in words if w in low)
        if hits and (best is None or hits > best[0]):
            best = (hits, j)
    return best[1] if best else (open_jobs[0] if len(open_jobs) == 1 else None)


def complete_job(job_id: str, approved: bool = False) -> dict[str, Any]:
    job = next((j for j in jobs() if j["id"] == job_id), None)
    if not job:
        raise ledger.FlowError("Job not found", 404)
    if job["status"] == "paid":
        return {"job": job, "outcome": "paid", "summary": "Already paid."}
    res = pay("job", job["id"], job["payee"], job["amount"], f"Job complete: {job['title']}", job["payee_name"], approved)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status=?, detail=?, transaction_id=?, completed_at=? WHERE id=?",
                     (res["outcome"], res["summary"], (res.get("transaction") or {}).get("id"), db.now_iso(), job["id"]))
    return {"job": next(j for j in jobs() if j["id"] == job_id), **res}


# ------------------------------------------------------------------ scheduled payments
def _next(run: datetime, every: str) -> datetime | None:
    if every == "once":
        return None
    if every == "daily":
        return run + timedelta(days=1)
    if every == "weekly":
        return run + timedelta(weeks=1)
    month = run.month % 12 + 1
    year = run.year + (1 if run.month == 12 else 0)
    return run.replace(year=year, month=month, day=min(run.day, calendar.monthrange(year, month)[1]))


def create_schedule(payee: str, amount: float, every: str, start: datetime | None = None, note: str | None = None,
                    payee_name: str | None = None) -> dict[str, Any]:
    if every not in EVERY:
        raise ledger.FlowError("Frequency must be once, daily, weekly or monthly.")
    if amount <= 0:
        raise ledger.FlowError("Amount must be greater than zero.")
    ledger.resolve_receiver(payee)
    item = {"id": db.new_id("sch_"), "payee": payee.strip(), "payee_name": payee_name, "amount": round(amount, 2),
            "note": note, "every": every, "next_run": (start or _now()).isoformat(), "active": 1, "last_result": None,
            "created_at": db.now_iso(), "owner_account": db.current_account()}
    with db.connect() as conn:
        conn.execute("INSERT INTO schedules (id, payee, payee_name, amount, note, every, next_run, active, last_result, "
                     "created_at, owner_account) VALUES (:id,:payee,:payee_name,:amount,:note,:every,:next_run,:active,"
                     ":last_result,:created_at,:owner_account)", item)
    return item


def schedules() -> list[dict[str, Any]]:
    where, args = _mine()
    return _rows("SELECT * FROM schedules WHERE 1=1" + where + " ORDER BY created_at DESC", args)


def set_schedule_active(schedule_id: str, active: bool) -> dict[str, Any]:
    item = next((s for s in schedules() if s["id"] == schedule_id), None)
    if not item:
        raise ledger.FlowError("Schedule not found", 404)
    with db.connect() as conn:
        conn.execute("UPDATE schedules SET active=? WHERE id=?", (1 if active else 0, schedule_id))
    return next(s for s in schedules() if s["id"] == schedule_id)


def run_now(schedule_id: str) -> list[dict[str, Any]]:
    """Make one of your schedules due now and run it."""
    if not any(s["id"] == schedule_id for s in schedules()):
        raise ledger.FlowError("Schedule not found", 404)
    with db.connect() as conn:
        conn.execute("UPDATE schedules SET next_run=?, active=1 WHERE id=?", (_now().isoformat(), schedule_id))
    return [r for r in run_due_schedules() if r["schedule"] == schedule_id]


def run_due_schedules(now: datetime | None = None) -> list[dict[str, Any]]:
    """Called by the background monitor every minute."""
    now = now or _now()
    done = []
    for s in schedules():
        if not s["active"] or not s["next_run"] or datetime.fromisoformat(s["next_run"]) > now:
            continue
        run_at = datetime.fromisoformat(s["next_run"])
        with db.as_account(s.get("owner_account") or db.current_account()):  # pays as the schedule's owner
            res = pay("schedule", f"{s['id']}-{run_at.date().isoformat()}", s["payee"], s["amount"],
                      s["note"] or "Scheduled payment", s["payee_name"])
        nxt = _next(run_at, s["every"])
        while nxt and nxt <= now:  # skip runs missed while the server was off
            nxt = _next(nxt, s["every"])
        with db.connect() as conn:
            conn.execute("UPDATE schedules SET next_run=?, active=?, last_result=? WHERE id=?",
                         (nxt.isoformat() if nxt else None, 1 if nxt else 0, res["summary"], s["id"]))
        done.append({"schedule": s["id"], **res})
    return done


# ------------------------------------------------------------------ agent-to-agent deal
def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


class BuyerAgent:
    """Acts for the user. Never pays above the budget or the seller's ask.

    With a fair-price estimate it buys at once when the ask is already fair, otherwise it opens below the
    fair price, uses the listing's weak points (missing features, flaws, cheaper alternatives) as leverage,
    and won't go much above the fair price even when the budget allows it.
    """

    OPEN = ["{why}, so I can offer {x}.", "{why}. My offer is {x}."]
    PUSH = ["{why}. {x} is as far as I'll go right now.", "Remember, {why_l}. I can do {x}.",
            "{why}. Meet me at {x}?", "I'll move to {x}, but {why_l}."]
    PLAIN = ["I can offer {x}.", "How about {x}?", "I'll go to {x}.", "{x} is my offer."]

    def __init__(self, budget: float, fair: float | None = None, leverage: list[str] | None = None):
        self.budget = budget
        self.fair = fair
        self.leverage = list(leverage or [])
        self.offer: float | None = None
        self.target: float | None = None
        self.limit: float | None = None
        self.last_ask: float | None = None
        self.stalls = 0

    def _say(self, kind: list[str], round_no: int, x: float) -> str:
        why = self.leverage[(round_no - 1) % len(self.leverage)] if self.leverage else ""
        if not why:
            return self.PLAIN[(round_no - 1) % len(self.PLAIN)].format(x=f"{x:.2f}")
        return kind[(round_no - 1) % len(kind)].format(why=_cap(why), why_l=why, x=f"{x:.2f}")

    def respond(self, ask: float, round_no: int) -> tuple[str, float, str]:
        self.stalls = self.stalls + 1 if self.last_ask is not None and ask >= self.last_ask - 0.01 else 0
        self.last_ask = ask
        if self.offer is None:
            if self.fair and ask <= self.fair * 1.02 and ask <= self.budget:
                return "accept", ask, f"{ask:.2f} is fair for what it is (similar items go for about {self.fair:.2f}). Deal."
            if ask > self.budget * 1.8:
                return "walk", ask, f"{ask:.2f} is far above my budget, so I'll pass."
            self.target = round(min(self.budget, self.fair) if self.fair else min(self.budget, ask * 0.88), 2)
            # the most the agent will pay: the budget, and not much above a fair price
            self.limit = round(min(self.budget, ask, self.fair * 1.1) if self.fair else min(self.budget, ask), 2)
            self.offer = round(min(self.target * 0.85, ask * 0.8), 2)
            return "offer", self.offer, self._say(self.OPEN, round_no, self.offer)
        limit = min(self.limit or self.budget, ask)
        if ask <= self.offer + 0.01 or ask <= self.target:
            return "accept", ask, f"{ask:.2f} works for me. Deal."
        # at its limit and the seller is still far away (or not moving): stop wasting time
        if self.offer >= limit - 0.01 and (round_no >= 9 or self.stalls >= 1
                                               or (self.fair and round_no >= 5 and ask > limit * 1.05)):
            reason = "that's over my budget" if ask > self.budget else "that's more than it's worth"
            return "walk", self.offer, f"{self.offer:.2f} was my best and {reason}. I'll pass."
        if self.stalls >= 2 and ask > limit:
            return "walk", self.offer, f"You're not moving from {ask:.2f}. I'll pass."
        aim = self.target if round_no <= 3 else limit  # stay near the fair price first
        step = 0.35 if round_no <= 3 else 0.5
        self.offer = round(min(limit, self.offer + max(0.5, (aim - self.offer) * step)), 2)
        if limit - self.offer < limit * 0.02:  # close enough: show the real limit
            self.offer = round(limit, 2)
        if self.offer >= ask - 0.01:
            return "accept", ask, f"Fine, {ask:.2f}. Deal."
        return "offer", self.offer, self._say(self.PUSH, round_no, self.offer)


class SellerAgent:
    """The other side, and stubborn: holds its price at first, makes small concessions, never goes below its floor."""

    # strengths are short clauses, e.g. "it's like new with box", "it comes with 2 lenses"
    HOLD = ["That's too low: {plus}. The price stays {x}.", "No. {Plus}. Still {x}."]
    GIVE = ["{Plus}. I can do {x}, not less.", "I could take {x}, and {plus}.",
            "{x}. That's already a good deal, {plus}.", "{Plus}. {x} is my price."]
    PLAIN = ["I can come down to {x}.", "{x}, and that's generous.", "Best I can do is {x}.", "{x}. No lower for now."]

    def __init__(self, list_price: float, floor: float, strengths: list[str] | None = None):
        self.floor = floor
        self.ask = list_price
        self.strengths = list(strengths or [])

    def _say(self, kind: list[str], round_no: int, x: float) -> str:
        plus = self.strengths[(round_no - 1) % len(self.strengths)] if self.strengths else ""
        if not plus:
            return self.PLAIN[(round_no - 1) % len(self.PLAIN)].format(x=f"{x:.2f}")
        return kind[(round_no - 1) % len(kind)].format(plus=plus, Plus=_cap(plus), x=f"{x:.2f}")

    def respond(self, offer: float, round_no: int) -> tuple[str, float, str]:
        if offer >= self.ask * 0.99:
            return "accept", round(offer, 2), f"Deal at {offer:.2f}."
        if offer >= self.floor and (round_no >= 8 or offer >= self.ask * 0.97):
            return "accept", round(offer, 2), f"Alright, {offer:.2f}. Deal."
        if round_no == 1 and offer < self.ask * 0.85:  # a lowball first offer: hold the price
            return "counter", self.ask, self._say(self.HOLD, round_no, self.ask)
        if self.ask <= self.floor + 0.01:
            return "counter", self.ask, f"{self.ask:.2f} is my lowest. Take it or leave it."
        share = 0.1 if round_no <= 3 else 0.2 if round_no <= 6 else 0.3
        new_ask = round(max(self.floor, self.ask - (self.ask - max(offer, self.floor)) * share), 2)
        self.ask = new_ask if self.ask - new_ask >= 0.5 else round(max(self.floor, self.ask - 0.5), 2)
        return "counter", self.ask, self._say(self.GIVE, round_no, self.ask)


def bargain_steps(buyer: BuyerAgent, seller: SellerAgent, item: str, max_rounds: int = 10):
    """Yield each message as the agents trade offers; the last yielded value is ("result", price or None)."""
    yield {"agent": "seller", "action": "ask", "amount": seller.ask, "text": f"{item}: {seller.ask:.2f}."}
    ask = seller.ask
    for rnd in range(1, max_rounds + 1):
        act, value, text = buyer.respond(ask, rnd)
        yield {"agent": "buyer", "action": act, "amount": value, "text": text}
        if act == "accept":
            yield ("result", value)
            return
        if act == "walk":
            yield ("result", None)
            return
        act, value, text = seller.respond(value, rnd)
        yield {"agent": "seller", "action": act, "amount": value, "text": text}
        if act == "accept":
            yield ("result", value)
            return
        ask = value
    yield ("result", None)


def bargain(buyer: BuyerAgent, seller: SellerAgent, item: str, max_rounds: int = 10) -> tuple[list[dict[str, Any]], float | None]:
    """Agents trade offers; returns the transcript and the agreed price (None if no deal)."""
    transcript: list[dict[str, Any]] = []
    price = None
    for step in bargain_steps(buyer, seller, item, max_rounds):
        if isinstance(step, tuple):
            price = step[1]
        else:
            transcript.append(step)
    return transcript, price


def negotiate(item: str, seller: str, budget: float, list_price: float | None = None, floor: float | None = None,
              seller_name: str | None = None, max_rounds: int = 8) -> dict[str, Any]:
    """Buyer and seller agents trade offers; on a deal the buyer agent pays through PayPal."""
    if budget <= 0:
        raise ledger.FlowError("Budget must be greater than zero.")
    ledger.resolve_receiver(seller)
    list_price = list_price or round(budget * 1.25, 2)
    floor = floor if floor is not None else round(list_price * 0.7, 2)
    sell = SellerAgent(list_price, floor)
    transcript, price = bargain(BuyerAgent(budget), sell, item, max_rounds)
    if price is None:
        summary = f"No deal on {item}: the seller wouldn't go under {sell.ask:.2f} and your budget is {budget:.2f}."
        _log("deal", item, "no_deal", summary)
        return {"outcome": "no_deal", "summary": summary, "transcript": transcript, "price": None}
    res = pay("deal", f"{item[:30]}-{db.new_id()[:6]}", seller, price, f"Agreed by agents: {item}", seller_name)
    if res["outcome"] == "needs_approval":  # keep the agreed terms on the server so approval can't change them
        deal = {"id": db.new_id("dea_"), "item": item, "payee": seller.strip(), "payee_name": seller_name, "price": price,
                "status": "pending", "created_at": db.now_iso(), "owner_account": db.current_account()}
        with db.connect() as conn:
            conn.execute("INSERT INTO deals (id, item, payee, payee_name, price, status, created_at, owner_account) "
                         "VALUES (:id,:item,:payee,:payee_name,:price,:status,:created_at,:owner_account)", deal)
        res["deal_id"] = deal["id"]
    return {**res, "transcript": transcript, "price": price, "item": item, "seller": seller}


def approve(kind: str, ref: str) -> dict[str, Any]:
    """The user approved a held agent payment. Payee and amount always come from the stored record."""
    if kind == "job":
        return complete_job(ref, approved=True)
    if kind == "schedule":
        s = next((x for x in schedules() if x["id"] == ref), None)
        if not s:
            raise ledger.FlowError("Schedule not found", 404)
        return pay("schedule", f"{s['id']}-approved-{_now().date().isoformat()}", s["payee"], s["amount"],
                   s["note"] or "Scheduled payment", s["payee_name"], approved=True)
    where, args = _mine()
    rows = _rows("SELECT * FROM deals WHERE id=?" + where, (ref, *args))
    if not rows:
        raise ledger.FlowError("Deal not found", 404)
    d = rows[0]
    if d["status"] != "pending":
        raise ledger.FlowError("This deal was already paid.", 409)
    with db.connect() as conn:  # claim it first so a double click can't pay twice
        if not conn.execute("UPDATE deals SET status='paid' WHERE id=? AND status='pending'", (ref,)).rowcount:
            raise ledger.FlowError("This deal was already paid.", 409)
    res = pay("deal", f"{d['id']}-approved", d["payee"], d["price"], f"Agreed by agents: {d['item']}", d["payee_name"], approved=True)
    if res["outcome"] != "paid":
        with db.connect() as conn:
            conn.execute("UPDATE deals SET status='pending' WHERE id=?", (ref,))
    return res
