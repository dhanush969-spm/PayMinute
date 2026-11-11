"""SQLite storage for payments, status history and app state."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

_lock = threading.RLock()

# The signed-in PayPal account for this request ({account, name}). None = no login (single-user mode) or
# PayMinute's own background work, which sees everything.
CURRENT: ContextVar[dict[str, Any] | None] = ContextVar("payminute_account", default=None)


def current_account() -> str | None:
    return (CURRENT.get() or {}).get("account")


@contextmanager
def as_account(account: str | None, name: str | None = None) -> Iterator[None]:
    """Run as one user (used by the background monitor for that user's scheduled payments)."""
    token = CURRENT.set({"account": account, "name": name} if account else None)
    try:
        yield
    finally:
        CURRENT.reset(token)


def scoped(key: str) -> str:
    """Per-user key for settings stored as key/value rows."""
    acct = current_account()
    return f"{acct}|{key}" if acct else key

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,               -- checkout | payout | invoice | refund | synced
    direction TEXT NOT NULL,          -- in | out
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    counterparty TEXT,
    counterparty_email TEXT,
    counterparty_phone TEXT,
    counterparty_country TEXT,
    memo TEXT,
    category TEXT,
    category_source TEXT,             -- rule | ai | user | paypal
    status TEXT NOT NULL,
    status_reason TEXT,
    paypal_order_id TEXT,
    paypal_authorization_id TEXT,
    paypal_capture_id TEXT,
    paypal_payout_batch_id TEXT,
    paypal_payout_item_id TEXT,
    paypal_invoice_id TEXT,
    paypal_transaction_id TEXT,
    request_id TEXT,
    risk_score REAL,
    risk_level TEXT,
    risk_reasons TEXT,                -- JSON list
    ai_note TEXT,
    source TEXT NOT NULL DEFAULT 'app', -- app | paypal_sync | agent
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status_changed_at TEXT,
    paypal_detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_tx_counterparty ON transactions(counterparty_email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_paypal_txn ON transactions(paypal_transaction_id)
    WHERE paypal_transaction_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    source TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS category_memory (
    key TEXT PRIMARY KEY,             -- normalized counterparty
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT,
    verified INTEGER,
    received_at TEXT,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message TEXT NOT NULL,            -- JSON internal message
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_actions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    summary TEXT,
    risk TEXT,
    status TEXT NOT NULL,             -- pending | executed | rejected | failed
    result TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- Phone numbers linked to PayPal accounts. Filled when someone logs in to PayMinute with PayPal:
-- source 'paypal' = the number came from PayPal's own profile (userinfo phone_number),
-- source 'user'   = PayPal didn't share one and the signed-in user added it themselves.
CREATE TABLE IF NOT EXISTS paypal_directory (
    phone TEXT PRIMARY KEY,           -- E.164
    payer_id TEXT NOT NULL,
    name TEXT,
    email TEXT,
    verified_account INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.settings.database_path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    from .automation import SCHEMA as AGENT_SCHEMA
    from .market import SCHEMA as MARKET_SCHEMA
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(AGENT_SCHEMA)
        from .automation import migrate
        migrate(conn)
        conn.executescript(MARKET_SCHEMA)
        from .auth import SESSION_SCHEMA
        conn.executescript(SESSION_SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "counterparty_phone" not in cols:  # databases created before phone payments
            conn.execute("ALTER TABLE transactions ADD COLUMN counterparty_phone TEXT")
        if "paypal_detail" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN paypal_detail TEXT")
        # each payment belongs to the account that made it; the other side sees it too if they use PayMinute
        for col in ("owner_account", "owner_name", "counterparty_account"):
            if col not in cols:
                conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_tx_owner ON transactions(owner_account)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_tx_counterparty_account ON transactions(counterparty_account)")
        for table in ("agent_actions", "agent_messages"):
            if "owner_account" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_account TEXT")


def reset_db() -> None:
    with connect() as conn:
        for table in [
            "transactions", "status_history", "budgets", "category_memory",
            "webhook_events", "agent_messages", "agent_actions", "paypal_directory",
            "jobs", "schedules", "agent_runs", "deals", "automation_settings", "listings", "quotes", "session_versions",
        ]:
            conn.execute(f"DELETE FROM {table}")


TX_FIELDS = [
    "id", "kind", "direction", "amount", "currency", "counterparty", "counterparty_email", "counterparty_phone",
    "counterparty_country", "memo", "category", "category_source", "status", "status_reason",
    "paypal_order_id", "paypal_authorization_id", "paypal_capture_id", "paypal_payout_batch_id",
    "paypal_payout_item_id", "paypal_invoice_id", "paypal_transaction_id", "request_id",
    "risk_score", "risk_level", "risk_reasons", "ai_note", "source", "created_at", "updated_at",
    "status_changed_at", "paypal_detail", "owner_account", "owner_name", "counterparty_account",
]


def row_to_tx(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    tx = dict(row)
    try:
        tx["risk_reasons"] = json.loads(tx.get("risk_reasons") or "[]")
    except json.JSONDecodeError:
        tx["risk_reasons"] = []
    try:
        tx["paypal_detail"] = json.loads(tx.get("paypal_detail") or "null")
    except json.JSONDecodeError:
        tx["paypal_detail"] = None
    return tx


def as_seen_by(tx: dict[str, Any] | None, account: str | None) -> dict[str, Any] | None:
    """The payment from this user's side: their own as is; one they received (or paid) from another
    PayMinute user is mirrored, without the sender's private risk check or category."""
    if tx is None or not account:
        return tx
    if tx.get("owner_account") == account:
        return {**tx, "mine": True}
    if tx.get("counterparty_account") == account:
        incoming = tx["direction"] == "out"
        return {**tx, "mine": False, "direction": "in" if incoming else "out",
                "counterparty": tx.get("owner_name") or "PayMinute user", "counterparty_email": None,
                "counterparty_phone": None, "counterparty_country": None,
                "category": "Transfers In" if incoming else None, "category_source": "default",
                "risk_score": None, "risk_level": None, "risk_reasons": [], "ai_note": None, "paypal_detail": None}
    return None


def _visible(sql: str, args: list[Any]) -> tuple[str, list[Any]]:
    acct = current_account()
    if acct:
        sql += " AND (owner_account=? OR counterparty_account=?)"
        args += [acct, acct]
    return sql, args


def insert_tx(data: dict[str, Any]) -> dict[str, Any]:
    tx = {k: None for k in TX_FIELDS}
    tx.update(data)
    tx["id"] = tx["id"] or new_id("tx_")
    ts = tx.get("created_at") or now_iso()
    tx["created_at"] = ts
    tx["updated_at"] = tx.get("updated_at") or ts
    tx["status_changed_at"] = tx.get("status_changed_at") or ts
    tx["source"] = tx.get("source") or "app"
    tx["currency"] = tx.get("currency") or config.settings.currency
    me = CURRENT.get() or {}
    tx["owner_account"] = tx.get("owner_account") or me.get("account")
    tx["owner_name"] = tx.get("owner_name") or me.get("name") or (
        (phone_for_payer(tx["owner_account"]) or {}).get("name") if tx["owner_account"] else None)
    if isinstance(tx.get("risk_reasons"), (list, tuple)):
        tx["risk_reasons"] = json.dumps(tx["risk_reasons"])
    cols = ",".join(TX_FIELDS)
    marks = ",".join("?" for _ in TX_FIELDS)
    with connect() as conn:
        conn.execute(f"INSERT INTO transactions ({cols}) VALUES ({marks})", [tx[k] for k in TX_FIELDS])
        conn.execute(
            "INSERT INTO status_history (transaction_id, status, reason, source, at) VALUES (?,?,?,?,?)",
            (tx["id"], tx["status"], tx.get("status_reason"), "created", tx["created_at"]),
        )
    return get_tx(tx["id"])  # type: ignore[return-value]


def get_tx(tx_id: str) -> dict[str, Any] | None:
    """Raw row, for PayMinute's own bookkeeping. API responses use visible_tx."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    return row_to_tx(row)


def visible_tx(tx_id: str) -> dict[str, Any] | None:
    """The payment if the signed-in user may see it (theirs, or sent to them), from their side."""
    tx = get_tx(tx_id)
    acct = current_account()
    return as_seen_by(tx, acct) if acct else tx


def find_tx(**where: Any) -> dict[str, Any] | None:
    """Newest matching payment the current user owns (all payments for PayMinute's own background work)."""
    if not where:
        return None
    clause = " AND ".join(f"{k}=?" for k in where)
    args = list(where.values())
    acct = current_account()
    if acct:
        clause += " AND owner_account=?"
        args.append(acct)
    with connect() as conn:
        row = conn.execute(f"SELECT * FROM transactions WHERE {clause} ORDER BY created_at DESC LIMIT 1", args).fetchone()
    return row_to_tx(row)


def find_any_tx(**where: Any) -> dict[str, Any] | None:
    """Like find_tx but across every account (webhooks, PayPal sync de-duplication)."""
    with as_account(None):
        return find_tx(**where)


def update_tx(tx_id: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_tx(tx_id)
    current = get_tx(tx_id)
    if current is None:
        return None
    history_source = fields.pop("_source", "update")
    if "risk_reasons" in fields and isinstance(fields["risk_reasons"], (list, tuple)):
        fields["risk_reasons"] = json.dumps(fields["risk_reasons"])
    if isinstance(fields.get("paypal_detail"), dict):
        fields["paypal_detail"] = json.dumps(fields["paypal_detail"])
    fields["updated_at"] = now_iso()
    status_changed = "status" in fields and fields["status"] != current["status"]
    if status_changed:
        fields["status_changed_at"] = fields["updated_at"]
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE transactions SET {sets} WHERE id=?", [*fields.values(), tx_id])
        if status_changed:
            conn.execute(
                "INSERT INTO status_history (transaction_id, status, reason, source, at) VALUES (?,?,?,?,?)",
                (tx_id, fields["status"], fields.get("status_reason"), history_source, fields["updated_at"]),
            )
    return get_tx(tx_id)


def list_tx(
    limit: int = 200,
    direction: str | None = None,
    category: str | None = None,
    statuses: list[str] | None = None,
    since: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    sql, args = _visible("SELECT * FROM transactions WHERE 1=1", [])
    acct = current_account()
    if direction and acct:  # a mirrored payment flips direction for the other side
        sql += " AND ((owner_account=? AND direction=?) OR (owner_account IS NOT ? AND counterparty_account=? AND direction!=?))"
        args += [acct, direction, acct, acct, direction]
    elif direction:
        sql += " AND direction=?"
        args.append(direction)
    if category:
        sql += " AND category=?"
        args.append(category)
    if statuses:
        sql += f" AND status IN ({','.join('?' for _ in statuses)})"
        args.extend(statuses)
    if since:
        sql += " AND created_at>=?"
        args.append(since)
    if search:
        sql += " AND (counterparty LIKE ? OR counterparty_email LIKE ? OR counterparty_phone LIKE ? OR memo LIKE ?)"
        like = f"%{search}%"
        args.extend([like, like, like, like])
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [as_seen_by(row_to_tx(r), acct) if acct else row_to_tx(r) for r in rows]  # type: ignore[misc]


def status_history(tx_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, reason, source, at FROM status_history WHERE transaction_id=? ORDER BY id", (tx_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_budgets() -> dict[str, float]:
    with connect() as conn:
        rows = conn.execute("SELECT category, monthly_limit FROM budgets").fetchall()
    acct = current_account()
    prefix = f"{acct}|" if acct else None
    out = {}
    for r in rows:
        if prefix and r["category"].startswith(prefix):
            out[r["category"][len(prefix):]] = r["monthly_limit"]
        elif not prefix and "|" not in r["category"]:
            out[r["category"]] = r["monthly_limit"]
    return out


def set_budget(category: str, monthly_limit: float) -> None:
    category = scoped(category)
    with connect() as conn:
        if monthly_limit <= 0:
            conn.execute("DELETE FROM budgets WHERE category=?", (category,))
        else:
            conn.execute(
                "INSERT INTO budgets (category, monthly_limit) VALUES (?,?) "
                "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit",
                (category, monthly_limit),
            )


def remember_category(key: str, category: str) -> None:
    key = scoped(key)
    with connect() as conn:
        conn.execute(
            "INSERT INTO category_memory (key, category) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET category=excluded.category",
            (key, category),
        )


def recall_category(key: str) -> str | None:
    key = scoped(key)
    with connect() as conn:
        row = conn.execute("SELECT category FROM category_memory WHERE key=?", (key,)).fetchone()
    return row["category"] if row else None


def webhook_seen(event_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM webhook_events WHERE id=?", (event_id,)).fetchone() is not None


def record_webhook(event_id: str, event_type: str, verified: bool, payload: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO webhook_events (id, event_type, verified, received_at, payload) VALUES (?,?,?,?,?)",
            (event_id, event_type, int(verified), now_iso(), json.dumps(payload)),
        )


def recent_webhooks(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, event_type, verified, received_at FROM webhook_events ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_agent_message(session_id: str, message: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_messages (session_id, message, created_at, owner_account) VALUES (?,?,?,?)",
            (session_id, json.dumps(message), now_iso(), current_account()),
        )


def get_agent_messages(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT message FROM agent_messages WHERE session_id=? AND owner_account IS ? ORDER BY id DESC LIMIT ?",
            (session_id, current_account(), limit),
        ).fetchall()
    return [json.loads(r["message"]) for r in reversed(rows)]


def create_action(session_id: str, tool: str, args: dict[str, Any], summary: str, risk: dict | None) -> dict[str, Any]:
    action_id = new_id("act_")
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_actions (id, session_id, tool, args, summary, risk, status, created_at, owner_account) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (action_id, session_id, tool, json.dumps(args), summary, json.dumps(risk) if risk else None,
             "pending", now_iso(), current_account()),
        )
    return get_action(action_id)  # type: ignore[return-value]


def get_action(action_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_actions WHERE id=?", (action_id,)).fetchone()
    if row is None:
        return None
    acct = current_account()
    if acct and row["owner_account"] != acct:  # someone else's approval card
        return None
    action = dict(row)
    action["args"] = json.loads(action["args"])
    action["risk"] = json.loads(action["risk"]) if action.get("risk") else None
    return action


def update_action(action_id: str, status: str, result: Any = None) -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute(
            "UPDATE agent_actions SET status=?, result=?, decided_at=? WHERE id=?",
            (status, json.dumps(result) if result is not None else None, now_iso(), action_id),
        )
    return get_action(action_id)


def link_phone(phone: str, payer_id: str, name: str | None, email: str | None, verified: bool, source: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM paypal_directory WHERE payer_id = ? AND phone != ?", (payer_id, phone))
        conn.execute(
            "INSERT INTO paypal_directory (phone, payer_id, name, email, verified_account, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(phone) DO UPDATE SET payer_id=excluded.payer_id, "
            "name=excluded.name, email=excluded.email, verified_account=excluded.verified_account, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (phone, payer_id, name, email, 1 if verified else 0, source, now_iso()),
        )


def find_phone(phone: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM paypal_directory WHERE phone = ?", (phone,)).fetchone()
    return dict(row) if row else None


def account_for_email(email: str | None) -> str | None:
    """The PayMinute account behind a PayPal email, if that person has logged in to PayMinute."""
    if not email:
        return None
    with connect() as conn:
        row = conn.execute("SELECT payer_id FROM paypal_directory WHERE lower(email) = lower(?) LIMIT 1", (email,)).fetchone()
    return row["payer_id"] if row else None


def phone_for_payer(payer_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM paypal_directory WHERE payer_id = ?", (payer_id,)).fetchone()
    return dict(row) if row else None
