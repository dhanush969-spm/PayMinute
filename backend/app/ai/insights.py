"""Spending analysis, budgets, forecasts and recurring payments."""
from __future__ import annotations

import calendar
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from .categorize import EXPENSE_CATEGORIES
from .fmt import fmt_money
from .llm import LLMError, get_llm

log = logging.getLogger("payminute.insights")

COUNTED = {"COMPLETED", "SUCCESS", "PENDING", "UNCLAIMED", "ONHOLD", "PAID"}


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def counted(tx: dict[str, Any]) -> bool:
    return (tx.get("status") or "").upper() in COUNTED


def month_summary(txs: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    start = _month_start(now)
    prev_start = _month_start(start - timedelta(days=1))
    cur = [t for t in txs if counted(t) and _parse(t["created_at"]) >= start]
    prev = [t for t in txs if counted(t) and prev_start <= _parse(t["created_at"]) < start]

    def total(rows: list[dict], direction: str) -> float:
        return round(sum(t["amount"] for t in rows if t["direction"] == direction), 2)

    by_cat: dict[str, float] = defaultdict(float)
    prev_by_cat: dict[str, float] = defaultdict(float)
    for t in cur:
        if t["direction"] == "out":
            by_cat[t.get("category") or "Other"] += t["amount"]
    for t in prev:
        if t["direction"] == "out":
            prev_by_cat[t.get("category") or "Other"] += t["amount"]

    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day = max(now.day, 1)
    return {
        "month": start.strftime("%B %Y"),
        "money_in": total(cur, "in"),
        "money_out": total(cur, "out"),
        "net": round(total(cur, "in") - total(cur, "out"), 2),
        "prev_money_in": total(prev, "in"),
        "prev_money_out": total(prev, "out"),
        "by_category": sorted(
            [{"category": c, "amount": round(a, 2), "previous": round(prev_by_cat.get(c, 0), 2)} for c, a in by_cat.items()],
            key=lambda x: x["amount"], reverse=True,
        ),
        "day_of_month": day,
        "days_in_month": days_in_month,
        "transaction_count": len(cur),
    }


def detect_recurring(txs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find outgoing payments that repeat roughly monthly/weekly to the same counterparty."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        if t["direction"] == "out" and counted(t):
            groups[(t.get("counterparty_email") or t.get("counterparty") or "").lower()].append(t)
    found = []
    for key, rows in groups.items():
        if len(rows) < 3 or not key:
            continue
        rows.sort(key=lambda r: r["created_at"])
        gaps = [(_parse(b["created_at"]) - _parse(a["created_at"])).days for a, b in zip(rows, rows[1:])]
        median_gap = statistics.median(gaps)
        amounts = [r["amount"] for r in rows]
        stable = statistics.pstdev(amounts) <= max(2.0, 0.15 * statistics.mean(amounts))
        if 25 <= median_gap <= 35 and stable:
            cadence, per_month = "monthly", statistics.mean(amounts[-3:])
        elif 6 <= median_gap <= 8 and stable:
            cadence, per_month = "weekly", statistics.mean(amounts[-3:]) * 52 / 12
        else:
            continue
        last = rows[-1]
        found.append({
            "counterparty": last.get("counterparty") or key,
            "category": last.get("category"),
            "cadence": cadence,
            "typical_amount": round(statistics.mean(amounts[-3:]), 2),
            "monthly_cost": round(per_month, 2),
            "last_paid": last["created_at"],
            "next_expected": (_parse(last["created_at"]) + timedelta(days=median_gap)).isoformat(),
            "occurrences": len(rows),
        })
    return sorted(found, key=lambda r: r["monthly_cost"], reverse=True)


def budget_status(txs: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    summary = month_summary(txs, now)
    spent = {c["category"]: c["amount"] for c in summary["by_category"]}
    previous = {c["category"]: c["previous"] for c in summary["by_category"]}
    recurring = detect_recurring(txs)
    recurring_parties = {r["counterparty"].lower(): r for r in recurring}
    start = _month_start(now)
    budgets = db.get_budgets()
    factor = summary["days_in_month"] / summary["day_of_month"]
    weight = min(1.0, summary["day_of_month"] / 15)  # trust the run-rate more as the month progresses
    days_left = summary["days_in_month"] - summary["day_of_month"]
    this_month = [t for t in txs if t["direction"] == "out" and counted(t) and _parse(t["created_at"]) >= start]
    out = []
    for cat, limit in sorted(budgets.items()):
        so_far = spent.get(cat, 0.0)
        # forecast = recurring paid + recurring due + projected variable spend
        rec_paid = sum(t["amount"] for t in this_month if t.get("category") == cat
                       and (t.get("counterparty") or "").lower() in recurring_parties)
        variable_so_far = so_far - rec_paid
        rec_monthly = sum(r["typical_amount"] for r in recurring if r["category"] == cat and r["cadence"] == "monthly")
        prev_variable = max(0.0, previous.get(cat, 0.0) - rec_monthly)
        variable = weight * variable_so_far * factor + (1 - weight) * max(variable_so_far, prev_variable)
        upcoming = sum(r["typical_amount"] for r in recurring if r["category"] == cat
                       and start <= _parse(r["next_expected"]) <= now + timedelta(days=days_left))
        forecast = round(max(so_far, rec_paid + variable + upcoming), 2)
        pct = round(100 * so_far / limit, 1) if limit else 0
        state = "over" if so_far > limit else ("at_risk" if forecast > limit else "ok")
        out.append({"category": cat, "limit": limit, "spent": round(so_far, 2), "forecast": forecast,
                    "percent": pct, "state": state, "remaining": round(limit - so_far, 2)})
    return out


def forecast_month(txs: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    s = month_summary(txs, now)
    factor = s["days_in_month"] / s["day_of_month"]
    # blend with last month early in the month
    weight = min(1.0, s["day_of_month"] / 15)
    out_forecast = weight * s["money_out"] * factor + (1 - weight) * max(s["prev_money_out"], s["money_out"])
    in_forecast = weight * s["money_in"] * factor + (1 - weight) * max(s["prev_money_in"], s["money_in"])
    return {"money_out": round(out_forecast, 2), "money_in": round(in_forecast, 2),
            "net": round(in_forecast - out_forecast, 2), "confidence": "low" if s["day_of_month"] < 7 else "medium"}


def rule_recommendations(txs: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    recs: list[dict[str, Any]] = []
    s = month_summary(txs, now)
    for b in budget_status(txs, now):
        if b["state"] == "over":
            recs.append({"kind": "budget", "severity": "high", "title": f"{b['category']} budget exceeded",
                         "body": f"Spent {fmt_money(b['spent'])} of {fmt_money(b['limit'])} this month."})
        elif b["state"] == "at_risk":
            recs.append({"kind": "budget", "severity": "medium", "title": f"{b['category']} is trending over budget",
                         "body": f"At this pace you'll spend about {fmt_money(b['forecast'])} vs a {fmt_money(b['limit'])} budget. "
                                 f"{fmt_money(b['remaining'])} left for the rest of the month."})
    for c in s["by_category"]:
        if c["previous"] > 0 and c["amount"] > c["previous"] * 1.4 and c["amount"] - c["previous"] > 50:
            pct = round(100 * (c["amount"] - c["previous"]) / c["previous"])
            recs.append({"kind": "trend", "severity": "medium", "title": f"{c['category']} up {pct}% vs last month",
                         "body": f"{fmt_money(c['amount'])} so far this month vs {fmt_money(c['previous'])} in all of last month."})
    recurring = detect_recurring(txs)
    subs = [r for r in recurring if r["category"] == "Software & Subscriptions"]
    if len(subs) >= 2:
        total = sum(r["monthly_cost"] for r in subs)
        recs.append({"kind": "recurring", "severity": "low", "title": f"{len(subs)} subscriptions cost {fmt_money(total)}/month",
                     "body": "Review which tools you still use: " + ", ".join(r["counterparty"] for r in subs[:4]) + "."})
    pending_in = [t for t in txs if t["direction"] == "in" and (t.get("status") or "") in {"PENDING", "HELD_FOR_REVIEW"}]
    if pending_in:
        recs.append({"kind": "cashflow", "severity": "medium",
                     "title": f"{fmt_money(sum(t['amount'] for t in pending_in))} of income not yet available",
                     "body": f"{len(pending_in)} customer payment(s) are pending or held. Don't count on this cash yet."})
    if s["money_out"] > 0 and s["money_in"] > 0 and s["money_out"] > s["money_in"]:
        recs.append({"kind": "cashflow", "severity": "medium", "title": "Spending more than you earned this month",
                     "body": f"Money out {fmt_money(s['money_out'])} vs money in {fmt_money(s['money_in'])}."})
    if not db.get_budgets():
        top = s["by_category"][:3]
        if top:
            recs.append({"kind": "setup", "severity": "low", "title": "Set budgets for your top categories",
                         "body": "Biggest categories this month: " + ", ".join(f"{c['category']} ({fmt_money(c['amount'])})" for c in top) + "."})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(recs, key=lambda r: order[r["severity"]])


def ai_coach(txs: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM-written, personalised advice grounded only on the computed numbers."""
    recs = rule_recommendations(txs)
    llm = get_llm()
    if not llm:
        return {"source": "rules", "summary": None, "recommendations": recs}
    facts = {
        "month": month_summary(txs),
        "forecast": forecast_month(txs),
        "budgets": budget_status(txs),
        "recurring": detect_recurring(txs)[:6],
        "signals": recs,
    }
    system = (
        "You are PayMinute, a friendly money coach for a freelancer who gets paid and pays others through PayPal. "
        "Using ONLY the JSON facts provided, write 3 short, specific, actionable suggestions (max 2 sentences each) "
        "about budgeting, cash flow and cutting waste. Use the numbers. No investment, tax or legal advice. "
        'Return JSON: {"summary": "one sentence overview", "tips": ["...", "...", "..."]}'
    )
    try:
        out = llm.complete_json(system, str(facts), max_tokens=700)
        return {"source": "llm", "summary": out.get("summary"), "tips": out.get("tips", [])[:4], "recommendations": recs}
    except (LLMError, ValueError, AttributeError) as exc:
        log.warning("AI coach failed: %s", exc)
        return {"source": "rules", "summary": None, "recommendations": recs}


def suggested_budgets(txs: list[dict[str, Any]]) -> dict[str, float]:
    """Suggest budgets ≈ 10% under the average of the last two full months per category."""
    now = datetime.now(timezone.utc)
    m1 = _month_start(now)
    m0 = _month_start(m1 - timedelta(days=1))
    mm = _month_start(m0 - timedelta(days=1))
    totals: dict[str, float] = defaultdict(float)
    for t in txs:
        at = _parse(t["created_at"])
        if t["direction"] == "out" and counted(t) and mm <= at < m1:
            totals[t.get("category") or "Other"] += t["amount"]
    return {c: round(v / 2 * 0.9 / 10) * 10 for c, v in totals.items() if v > 20 and c in EXPENSE_CATEGORIES}
