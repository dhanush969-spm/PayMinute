"""PayPal API calls used by the app."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import config
from .client import PayPalError, PayPalHTTPClient

log = logging.getLogger("payminute.paypal")

_client: Any = None


class PayPalNotConfigured(PayPalError):
    def __init__(self) -> None:
        super().__init__(503, "NOT_CONFIGURED",
                         "PayPal sandbox credentials are missing: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env")


def get_client() -> PayPalHTTPClient:
    global _client
    if _client is None:
        s = config.settings
        if not s.paypal_configured:
            raise PayPalNotConfigured()
        _client = PayPalHTTPClient(s.paypal_client_id, s.paypal_client_secret)
    return _client


def reset_client() -> None:
    global _client, _toolkit
    _client = None
    _toolkit = None


def money(value: float) -> str:
    return f"{value:.2f}"


def create_checkout_order(amount: float, description: str, invoice_ref: str, request_id: str) -> dict[str, Any]:
    """Create an AUTHORIZE-intent order so PayMinute can risk-check the payer before capturing."""
    s = config.settings
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [{
            "reference_id": invoice_ref,
            "custom_id": invoice_ref,
            "description": description[:127],
            "soft_descriptor": "PAYMINUTE",
            "amount": {
                "currency_code": s.currency,
                "value": money(amount),
                "breakdown": {"item_total": {"currency_code": s.currency, "value": money(amount)}},
            },
            "items": [{
                "name": description[:127] or "Payment",
                "quantity": "1",
                "category": "DIGITAL_GOODS",
                "unit_amount": {"currency_code": s.currency, "value": money(amount)},
            }],
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": s.business_name[:127],
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "CONTINUE",
                    "return_url": f"{s.app_base_url}/get-paid?status=approved",
                    "cancel_url": f"{s.app_base_url}/get-paid?status=cancelled",
                }
            }
        },
    }
    return get_client().request("POST", "/v2/checkout/orders", json=body, idempotency_key=request_id)


def get_order(order_id: str) -> dict[str, Any]:
    return get_client().request("GET", f"/v2/checkout/orders/{order_id}")


def authorize_order(order_id: str, request_id: str) -> dict[str, Any]:
    return get_client().request("POST", f"/v2/checkout/orders/{order_id}/authorize", json={},
                                idempotency_key=request_id)


def capture_authorization(authorization_id: str, request_id: str, invoice_ref: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"final_capture": True}
    if invoice_ref:
        body["invoice_id"] = invoice_ref
    return get_client().request("POST", f"/v2/payments/authorizations/{authorization_id}/capture", json=body,
                                idempotency_key=request_id)


def void_authorization(authorization_id: str, request_id: str) -> dict[str, Any]:
    return get_client().request("POST", f"/v2/payments/authorizations/{authorization_id}/void", json={},
                                idempotency_key=request_id)


def get_capture(capture_id: str) -> dict[str, Any]:
    return get_client().request("GET", f"/v2/payments/captures/{capture_id}")


def refund_capture(capture_id: str, request_id: str, note: str | None = None) -> dict[str, Any]:
    body = {"note_to_payer": note[:255]} if note else {}
    return get_client().request("POST", f"/v2/payments/captures/{capture_id}/refund", json=body,
                                idempotency_key=request_id)


def extract_payer(order: dict[str, Any]) -> dict[str, Any]:
    payer = order.get("payer") or {}
    if not payer:
        payer = ((order.get("payment_source") or {}).get("paypal") or {})
    name = payer.get("name") or {}
    full = " ".join(x for x in [name.get("given_name"), name.get("surname")] if x).strip()
    return {
        "name": full or payer.get("email_address") or "PayPal buyer",
        "email": payer.get("email_address"),
        "payer_id": payer.get("payer_id") or payer.get("account_id"),
        "country": (payer.get("address") or {}).get("country_code"),
    }


def extract_authorization(order: dict[str, Any]) -> dict[str, Any]:
    units = order.get("purchase_units") or [{}]
    auths = ((units[0].get("payments") or {}).get("authorizations")) or [{}]
    return auths[0]


def send_payout(receiver: str, amount: float, note: str, sender_item_id: str, recipient_type: str = "EMAIL") -> dict[str, Any]:
    s = config.settings
    body = {
        "sender_batch_header": {
            "sender_batch_id": f"payminute_{sender_item_id}",
            "email_subject": f"You have a payment from {s.business_name}",
            "email_message": note[:1000] if note else f"Payment from {s.business_name}",
        },
        "items": [{
            "recipient_type": recipient_type,
            "amount": {"value": money(amount), "currency": s.currency},
            "note": (note or "Payment")[:4000],
            "receiver": receiver,
            "sender_item_id": sender_item_id,
        }],
    }
    # sender_item_id is the idempotency key
    return get_client().request("POST", "/v1/payments/payouts", json=body, idempotency_key=f"payout-{sender_item_id}")


def get_payout_batch(batch_id: str) -> dict[str, Any]:
    return get_client().request("GET", f"/v1/payments/payouts/{batch_id}")


def get_payout_item(item_id: str) -> dict[str, Any]:
    return get_client().request("GET", f"/v1/payments/payouts-item/{item_id}")


def cancel_payout_item(item_id: str) -> dict[str, Any]:
    return get_client().request("POST", f"/v1/payments/payouts-item/{item_id}/cancel", json={},
                                idempotency_key=f"cancel-{item_id}")


def search_transactions(days: int = 30, max_pages: int = 3) -> dict[str, Any]:
    """Transaction Search: the app's business account activity for the last `days` (max 31).

    PayPal only has data up to `last_refreshed_datetime` (sandbox lags up to ~3 hours), so the window ends there.
    """
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=min(days, 30))
    out: list[dict[str, Any]] = []
    last_refreshed = None
    page, total_pages = 1, 1
    while page <= min(total_pages, max_pages):
        params = {
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields": "transaction_info,payer_info,cart_info",
            "page_size": "100",
            "page": str(page),
        }
        try:
            data = get_client().request("GET", "/v1/reporting/transactions", params=params, max_retries=1, timeout=20)
        except PayPalError as exc:
            # asked past PayPal's last refresh: retry once with an earlier end date
            if page == 1 and exc.status == 400 and "date" in (exc.message or "").lower() and end > start + timedelta(hours=4):
                end = end - timedelta(hours=4)
                continue
            raise
        out += data.get("transaction_details") or []
        last_refreshed = data.get("last_refreshed_datetime") or last_refreshed
        total_pages = int(data.get("total_pages") or 1)
        page += 1
    return {"items": out, "last_refreshed": last_refreshed, "start": params["start_date"], "end": params["end_date"]}


def find_reported_transaction(transaction_id: str, since: datetime | None = None) -> dict[str, Any] | None:
    """PayPal's own ledger entry for one transaction (Transaction Search), or None if it isn't reported yet."""
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = max((since or end) - timedelta(days=1), end - timedelta(days=30))
    for _ in range(2):
        params = {"transaction_id": transaction_id, "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "fields": "transaction_info"}
        try:
            data = get_client().request("GET", "/v1/reporting/transactions", params=params, max_retries=1, timeout=20)
        except PayPalError as exc:
            # asked past PayPal's last refresh: try once with an earlier end date
            if exc.status == 400 and "date" in (exc.message or "").lower() and end - timedelta(hours=4) > start:
                end = end - timedelta(hours=4)
                continue
            raise
        found = data.get("transaction_details") or []
        return (found[0].get("transaction_info") or {}) if found else None
    return None


def get_balances(currency: str | None = None) -> dict[str, Any]:
    """Balances of the business account these API credentials belong to (GET /v1/reporting/balances)."""
    params = {"currency_code": currency} if currency else None
    return get_client().request("GET", "/v1/reporting/balances", params=params, max_retries=1, timeout=20)


def list_invoices(page_size: int = 20) -> dict[str, Any]:
    """Invoices (payment requests) this business account has created."""
    return get_client().request("GET", "/v2/invoicing/invoices",
                                params={"page": "1", "page_size": str(page_size), "total_required": "true"},
                                max_retries=1, timeout=20)


def verify_webhook(headers: dict[str, str], raw_body: bytes) -> bool:
    s = config.settings
    if not s.paypal_webhook_id:
        log.warning("PAYPAL_WEBHOOK_ID not set; rejecting webhook")
        return False
    h = {k.lower(): v for k, v in headers.items()}
    # send the raw event: re-serialising breaks the signature
    body = (
        '{"auth_algo":' + json.dumps(h.get("paypal-auth-algo", ""))
        + ',"cert_url":' + json.dumps(h.get("paypal-cert-url", ""))
        + ',"transmission_id":' + json.dumps(h.get("paypal-transmission-id", ""))
        + ',"transmission_sig":' + json.dumps(h.get("paypal-transmission-sig", ""))
        + ',"transmission_time":' + json.dumps(h.get("paypal-transmission-time", ""))
        + ',"webhook_id":' + json.dumps(s.paypal_webhook_id)
        + ',"webhook_event":' + raw_body.decode("utf-8") + "}"
    )
    client = get_client()
    resp = client._http.post(
        f"{client.base_url}/v1/notifications/verify-webhook-signature",
        content=body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {client.access_token()}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
    )
    ok = resp.is_success and resp.json().get("verification_status") == "SUCCESS"
    if not ok:
        log.warning("Webhook verification failed: %s %s", resp.status_code, resp.text[:300])
    return ok


# PayPal Agent Toolkit, routed through our client
TOOLKIT_ACTIONS = {
    "invoices": {
        "create": True, "list": True, "get": True, "send": True, "sendReminder": True,
        "cancel": True, "generateQRC": True, "generateInvoiceNumber": True,
    },
    "orders": {"create": True, "get": True},
    "transactions": {"list": True},
    "disputes": {"list": True, "get": True},
}

_toolkit: Any = None


class AgentToolkit:
    def __init__(self) -> None:
        from paypal_agent_toolkit.shared.api import PayPalAPI
        from paypal_agent_toolkit.shared.configuration import Configuration, Context, is_tool_allowed
        from paypal_agent_toolkit.shared.tools import tools as toolkit_tools

        s = config.settings
        configuration = Configuration(actions=TOOLKIT_ACTIONS, context=Context(sandbox=True))
        configuration.context.source = "PAYMINUTE"
        self.api = PayPalAPI(client_id=s.paypal_client_id or "unset", secret=s.paypal_client_secret or "unset",
                             context=configuration.context)
        # reuse our client
        self.api._paypal_client = get_client()
        self.tools = {t["method"]: t for t in toolkit_tools if is_tool_allowed(t, configuration)}

    def specs(self) -> list[dict[str, Any]]:
        out = []
        for name, t in self.tools.items():
            out.append({
                "name": name,
                "description": t["description"],
                "parameters": t["args_schema"].model_json_schema(),
                "source": "paypal-agent-toolkit",
            })
        return out

    def run(self, method: str, params: dict[str, Any]) -> Any:
        result = self.api.run(method, params)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"result": result}
        return result


def get_toolkit() -> AgentToolkit:
    global _toolkit
    if _toolkit is None:
        _toolkit = AgentToolkit()
    return _toolkit


__all__ = ["PayPalError", "get_client", "get_toolkit"]
