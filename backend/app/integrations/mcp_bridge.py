"""Lets the assistant use tools from remote MCP servers (PayPal, Zapier and others)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("payminute.mcp")

PAYPAL_MCP_SANDBOX_URL = "https://mcp.sandbox.paypal.com/sse"
READ_ONLY = re.compile(r"^(get|list|search|show|find|fetch|retrieve|read|lookup|query|describe|count|check)[_\-]?", re.I)
TOOL_CACHE_SECONDS = 600


@dataclass
class Server:
    name: str
    url: str
    transport: str = "http"            # "http" (streamable HTTP) or "sse"
    headers: dict[str, str] = field(default_factory=dict)
    prefix: str = ""                    # prefix added to tool names ("" keeps original names)
    token_provider: Any = None          # callable returning a bearer token (PayPal)
    replaces_toolkit: bool = False

    def resolved_headers(self) -> dict[str, str]:
        h = dict(self.headers)
        if self.token_provider:
            h["Authorization"] = f"Bearer {self.token_provider()}"
        return h


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "on", "yes"}


def configured_servers() -> list[Server]:
    from .. import config
    servers: list[Server] = []
    s = config.settings
    env_token = os.getenv("PAYPAL_SANDBOX_ACCESS_TOKEN", "").strip()
    if _truthy(os.getenv("PAYPAL_MCP", "on")) and (s.paypal_configured or env_token):
        # PayPal's sandbox MCP server, as in PayPal's AI Toolkit. Uses the app's own token (refreshed
        # automatically), or PAYPAL_SANDBOX_ACCESS_TOKEN when no app keys are set.
        def paypal_token() -> str:
            if not s.paypal_configured:
                return env_token
            from ..paypal.service import get_client
            return get_client().access_token()
        servers.append(Server(name="paypal", url=os.getenv("PAYPAL_MCP_URL", PAYPAL_MCP_SANDBOX_URL),
                              transport="sse" if os.getenv("PAYPAL_MCP_URL", PAYPAL_MCP_SANDBOX_URL).rstrip("/").endswith("/sse") else "http",
                              token_provider=paypal_token, replaces_toolkit=True))
    if os.getenv("ZAPIER_MCP_URL", "").strip():
        headers = {}
        if os.getenv("ZAPIER_MCP_API_KEY", "").strip():
            headers["Authorization"] = f"Bearer {os.getenv('ZAPIER_MCP_API_KEY').strip()}"
        servers.append(Server(name="zapier", url=os.getenv("ZAPIER_MCP_URL").strip(), transport="http",
                              headers=headers, prefix="zapier__"))
    raw = os.getenv("MCP_SERVERS", "").strip()
    if raw:
        try:
            for item in json.loads(raw):
                servers.append(Server(name=item["name"], url=item["url"], transport=item.get("transport", "http"),
                                      headers=item.get("headers") or {}, prefix=f"{item['name']}__"))
        except (ValueError, KeyError, TypeError) as exc:
            log.error("Invalid MCP_SERVERS JSON: %s", exc)
    return servers


async def _with_session(server: Server, fn):
    from mcp import ClientSession

    headers = server.resolved_headers()
    if server.transport == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(server.url, headers=headers, timeout=15, sse_read_timeout=60) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(server.url, headers=headers, timeout=20, sse_read_timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def _run(coro_factory, timeout: float = 45):
    """Run an async MCP call from sync code (the agent runs in a worker thread)."""
    result: dict[str, Any] = {}

    def target():
        try:
            result["value"] = asyncio.run(asyncio.wait_for(coro_factory(), timeout))
        except BaseException as exc:  # noqa: BLE001 - surface any transport error
            result["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout + 5)
    if "error" in result:
        err = result["error"]
        if isinstance(err, BaseExceptionGroup):  # anyio task groups wrap transport errors
            err = err.exceptions[0]
        raise RuntimeError(f"MCP error: {err}") from None
    if "value" not in result:
        raise RuntimeError("MCP call timed out")
    return result["value"]


_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_errors: dict[str, str] = {}


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


def list_server_tools(server: Server, refresh: bool = False) -> list[dict[str, Any]]:
    cached = _cache.get(server.name)
    if cached and not refresh and time.time() - cached[0] < TOOL_CACHE_SECONDS:
        return cached[1]

    async def go(session):
        res = await session.list_tools()
        return [{"name": t.name, "description": t.description or "", "schema": t.inputSchema or {"type": "object"}}
                for t in res.tools]

    try:
        tools = _run(lambda: _with_session(server, go))
        _errors.pop(server.name, None)
    except Exception as exc:  # unreachable server must not break the assistant
        _errors[server.name] = str(exc)[:300]
        log.warning("MCP server %s unavailable: %s", server.name, exc)
        tools = []
    specs = [{
        "name": _safe_name(server.prefix + t["name"]),
        "remote_name": t["name"],
        "server": server.name,
        "description": (f"[{server.name} MCP] " + t["description"])[:1024],
        "parameters": t["schema"] if t["schema"].get("type") == "object" else {"type": "object", "properties": {}},
        "source": f"mcp:{server.name}",
        "approval": not READ_ONLY.match(t["name"]),
    } for t in tools]
    _cache[server.name] = (time.time(), specs)
    return specs


def all_tools() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for server in configured_servers():
        out.extend(list_server_tools(server))
    return out


def toolkit_replaced() -> bool:
    return any(s.replaces_toolkit for s in configured_servers())


def call(tool_name: str, args: dict[str, Any]) -> Any:
    spec = next((t for t in all_tools() if t["name"] == tool_name), None)
    if not spec:
        raise RuntimeError(f"Unknown MCP tool {tool_name}")
    server = next(s for s in configured_servers() if s.name == spec["server"])

    async def go(session):
        res = await session.call_tool(spec["remote_name"], args)
        texts = [getattr(c, "text", None) for c in res.content]
        payload: Any = getattr(res, "structuredContent", None) or "\n".join(t for t in texts if t)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                pass
        if res.isError:
            raise RuntimeError(f"{tool_name} failed: {payload}")
        return payload

    return _run(lambda: _with_session(server, go), timeout=60)


def public_status() -> list[dict[str, Any]]:
    """What the UI shows. Never includes URLs or headers (Zapier URLs embed a secret)."""
    out = []
    for s in configured_servers():
        cached = _cache.get(s.name)
        out.append({"name": s.name, "transport": s.transport, "tools": len(cached[1]) if cached else None,
                    "error": _errors.get(s.name)})
    return out
