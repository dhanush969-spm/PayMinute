"""PayPal REST client with token caching, retries and idempotency keys."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import httpx

log = logging.getLogger("payminute.paypal")

SANDBOX_API = "https://api-m.sandbox.paypal.com"


class PayPalError(Exception):
    def __init__(self, status: int, name: str, message: str, debug_id: str | None = None, details: Any = None):
        super().__init__(f"{status} {name}: {message}" + (f" (debug_id={debug_id})" if debug_id else ""))
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "name": self.name,
            "message": self.message,
            "debug_id": self.debug_id,
            "details": self.details,
        }


class PayPalHTTPClient:
    """PayPal sandbox REST client."""

    mode = "sandbox"

    def __init__(self, client_id: str, client_secret: str, base_url: str = SANDBOX_API, timeout: float = 30.0):
        if not client_id or not client_secret:
            raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required for sandbox mode")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def access_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 120:
                return self._token
            resp = self._http.post(
                f"{self.base_url}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                body = _safe_json(resp)
                raise PayPalError(
                    resp.status_code,
                    body.get("error", "AUTHENTICATION_FAILURE"),
                    body.get("error_description", "Could not get a PayPal access token. Check your sandbox credentials."),
                    resp.headers.get("paypal-debug-id"),
                )
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
            return self._token

    def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        max_retries: int = 3,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        if method.upper() == "POST":
            req_headers.setdefault("PayPal-Request-Id", idempotency_key or str(uuid.uuid4()))

        attempt = 0
        while True:
            req_headers["Authorization"] = f"Bearer {self.access_token()}"
            try:
                resp = self._http.request(method, url, json=json, params=params, headers=req_headers,
                                          **({"timeout": timeout} if timeout else {}))
            except httpx.TransportError as exc:
                if attempt >= max_retries:
                    raise PayPalError(503, "NETWORK_ERROR", f"Could not reach PayPal: {exc}") from exc
                time.sleep(2 ** attempt)
                attempt += 1
                continue

            if resp.status_code == 401 and attempt == 0:
                # token expired early: refresh once
                self._token = None
                attempt += 1
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= max_retries:
                    break
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                log.warning("PayPal %s %s -> %s, retrying in %ss", method, path, resp.status_code, delay)
                time.sleep(min(delay, 10))
                attempt += 1
                continue
            break

        if resp.status_code == 204 or not resp.content:
            if resp.is_success:
                return {}
        body = _safe_json(resp)
        if not resp.is_success:
            debug_id = body.get("debug_id") or resp.headers.get("paypal-debug-id")
            log.error("PayPal error %s %s -> %s debug_id=%s body=%s", method, path, resp.status_code, debug_id, body)
            raise PayPalError(
                resp.status_code,
                body.get("name") or body.get("error") or "PAYPAL_ERROR",
                body.get("message") or body.get("error_description") or resp.text[:300],
                debug_id,
                body.get("details"),
            )
        return body

    def exchange_auth_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Authorization code → the signed-in user's access token (Log in with PayPal)."""
        resp = self._http.post(
            f"{self.base_url}/v1/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
        )
        body = _safe_json(resp)
        if not resp.is_success:
            raise PayPalError(resp.status_code, body.get("error", "LOGIN_FAILED"),
                              body.get("error_description", "PayPal login failed"), resp.headers.get("paypal-debug-id"))
        return body

    def userinfo(self, user_token: str) -> dict[str, Any]:
        resp = self._http.get(
            f"{self.base_url}/v1/identity/oauth2/userinfo", params={"schema": "paypalv1.1"},
            headers={"Authorization": f"Bearer {user_token}", "Accept": "application/json"},
        )
        body = _safe_json(resp)
        if not resp.is_success:
            raise PayPalError(resp.status_code, body.get("name", "USERINFO_FAILED"),
                              body.get("message", "Could not read your PayPal profile"), resp.headers.get("paypal-debug-id"))
        return body

    def get(self, uri: str, payload: Any = None) -> dict[str, Any]:  # toolkit signature
        return self.request("GET", uri, params=payload if isinstance(payload, dict) else None)

    def post(self, uri: str, payload: Any = None) -> dict[str, Any]:
        return self.request("POST", uri, json=payload)

    def put(self, uri: str, payload: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self.request("PUT", uri, json=payload, headers=headers)

    def delete(self, uri: str) -> dict[str, Any]:
        return self.request("DELETE", uri)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
