"""Data tables and AI settings for the Studio page."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from . import config, db, ledger
from .ai import insights

STATUS_GROUPS = {
    "COMPLETED": "Completed", "SUCCESS": "Completed", "PAID": "Completed",
    "PENDING": "In progress", "UNCLAIMED": "In progress", "ONHOLD": "In progress", "AUTHORIZED": "In progress",
    "HELD_FOR_REVIEW": "Held by PayMinute",
    "FAILED": "Failed / voided", "DENIED": "Failed / voided", "VOIDED": "Failed / voided", "BLOCKED": "Failed / voided",
    "RETURNED": "Returned / refunded", "REFUNDED": "Returned / refunded", "REVERSED": "Returned / refunded",
    "CANCELLED": "Failed / voided", "CREATED": "Not started", "DRAFT": "Not started", "SENT": "Invoice sent",
}
CHANNELS = {"payout": "PayPal Payouts", "checkout": "PayPal Checkout", "invoice": "PayPal Invoicing",
            "synced": "PayPal account (synced)", "refund": "Refund"}


def _date(ts: str) -> str:
    return (ts[:19] + "Z") if ts else ts


def build_tables() -> dict[str, Any]:
    txs = ledger.history()
    payments, signals, events = [], [], []
    for t in txs:
        status = (t.get("status") or "").upper()
        group = STATUS_GROUPS.get(status, status.title())
        counted = insights.counted(t)
        amount = float(t["amount"])
        risk_level = t.get("risk_level") or "unscored"
        payments.append({
            "id": t["id"],
            "date": _date(t["created_at"]),
            "direction": "Money in" if t["direction"] == "in" else "Money out",
            "channel": CHANNELS.get(t["kind"], t["kind"]),
            "counterparty": t.get("counterparty") or t.get("counterparty_email") or "Unknown",
            "country": t.get("counterparty_country") or "—",
            "category": t.get("category") or "Uncategorised",
            "categorySource": {"rule": "Rules", "memory": "Learned", "user": "You", "ai": "AI (LLM)",
                               "default": "Default"}.get(t.get("category_source") or "", "—"),
            "status": status.replace("_", " ").title(),
            "statusGroup": group,
            "statusReason": (t.get("status_reason") or "").replace("_", " ").title() or "—",
            "amount": amount,
            "moneyIn": amount if t["direction"] == "in" and counted else 0.0,
            "moneyOut": amount if t["direction"] == "out" and counted else 0.0,
            "net": (amount if t["direction"] == "in" else -amount) if counted else 0.0,
            "inFlightAmount": amount if group in {"In progress", "Held by PayMinute"} else 0.0,
            "heldAmount": amount if group == "Held by PayMinute" else 0.0,
            "riskScore": float(t["risk_score"]) if t.get("risk_score") is not None else None,
            "riskLevel": {"low": "1 Low", "medium": "2 Medium", "high": "3 High"}.get(risk_level, "0 Unscored"),
            "flagged": 1 if risk_level in {"medium", "high"} else 0,
            "highRisk": 1 if risk_level == "high" else 0,
            "payments": 1,
            "dataSource": {"paypal_sync": "PayPal sync", "agent": "Assistant"}.get(t.get("source"), "Live"),
        })
        for r in t.get("risk_reasons") or []:
            signals.append({
                "id": f"{t['id']}:{r['code']}",
                "paymentId": t["id"],
                "signal": r["label"],
                "code": r["code"],
                "weight": float(r["weight"]),
                "isMl": 1 if r["code"] == "ml_anomaly" else 0,
            })
    with db.connect() as conn:
        rows = conn.execute("SELECT id, transaction_id, status, reason, source, at FROM status_history ORDER BY id").fetchall()
    # flattened so timeline widgets don't fan out
    by_id = {p["id"]: p for p in payments}
    for r in rows:
        p = by_id.get(r["transaction_id"])
        if p:
            events.append({
                "id": str(r["id"]), "paymentId": r["transaction_id"],
                "counterparty": p["counterparty"], "channel": p["channel"], "amount": p["amount"],
                "status": r["status"].replace("_", " ").title(),
                "reason": (r["reason"] or "").replace("_", " ").title() or "—",
                "via": {"created": "PayMinute", "paypal": "PayPal API", "webhook": "PayPal webhook", "user": "You",
                        "payminute": "PayMinute risk hold", "update": "PayMinute"}.get(r["source"] or "", r["source"] or "—"),
                "at": _date(r["at"]),
            })
    budgets = [{"category": b["category"], "limit": b["limit"], "spent": b["spent"], "forecast": b["forecast"],
                "state": {"ok": "On track", "at_risk": "At risk", "over": "Over"}[b["state"]]}
               for b in insights.budget_status(txs)]
    return {
        "asOf": datetime.now(timezone.utc).isoformat(),
        "currency": config.settings.currency,
        "payments": payments,
        "riskSignals": signals,
        "statusEvents": events,
        "budgets": budgets,
    }


def studio_ai_settings() -> dict[str, Any]:
    """OpenAI when a Studio key is set, otherwise the main LLM."""
    from .ai.llm import get_llm
    s = config.settings
    picked = [m.strip() for m in os.getenv("STUDIO_AI_MODELS", "").split(",") if m.strip()]
    key = os.getenv("STUDIO_OPENAI_API_KEY", "").strip() or (s.llm_api_key if s.resolved_llm_provider == "openai" else "")
    if key:
        models = picked or ([s.llm_model] if s.resolved_llm_provider == "openai" and s.llm_model else ["gpt-4.1-mini"])
        base = os.getenv("STUDIO_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return {"mode": "openai", "key": key, "models": models, "base_url": base, "enabled": True}
    llm = get_llm()
    if llm is not None:
        return {"mode": "llm", "key": "", "models": [llm.model or s.resolved_llm_provider], "base_url": "",
                "enabled": True, "provider": s.resolved_llm_provider}
    return {"mode": "off", "key": "", "models": [], "base_url": "", "enabled": False}
