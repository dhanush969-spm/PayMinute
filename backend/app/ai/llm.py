"""LLM client with tool calling (OpenAI-compatible APIs and Anthropic)."""
from __future__ import annotations

import collections
import contextlib
import contextvars
import copy
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import config

log = logging.getLogger("payminute.llm")


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    """The provider said 429 / RESOURCE_EXHAUSTED (e.g. Gemini's per-minute limit)."""


# which feature is calling the model, for the error log ("bargaining", "AI Guard chat", ...)
FEATURE: contextvars.ContextVar[str] = contextvars.ContextVar("ai_feature", default="AI")
_events: collections.deque[dict[str, Any]] = collections.deque(maxlen=60)
_events_lock = threading.Lock()


@contextlib.contextmanager
def feature(name: str):
    token = FEATURE.set(name)
    try:
        yield
    finally:
        FEATURE.reset(token)


def record(kind: str, detail: str, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Log a model problem (rate limit, error) and keep it for /api/ai/status and the UI."""
    ev = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "feature": FEATURE.get(),
          "provider": provider, "model": model, "detail": re.sub(r"\s+", " ", str(detail))[:300]}
    with _events_lock:
        _events.appendleft(ev)
    (log.warning if kind == "rate_limit" else log.error)("AI %s in %s (%s %s): %s", kind, ev["feature"], provider,
                                                          model, ev["detail"])
    return ev


def events(limit: int = 20) -> list[dict[str, Any]]:
    with _events_lock:
        return list(_events)[:limit]


def _retry_after(resp: httpx.Response) -> float | None:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', resp.text or "")
    if m:
        return float(m.group(1))
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


_KEEP = {"type", "description", "enum", "properties", "required", "items", "minimum", "maximum"}


def sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $refs and reduce a Pydantic JSON schema to the subset every provider accepts."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node: Any, depth: int = 0) -> Any:
        if not isinstance(node, dict) or depth > 12:
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            merged = {**copy.deepcopy(defs.get(name, {})), **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, depth + 1)
        for union in ("anyOf", "oneOf"):
            if union in node:
                options = [o for o in node[union] if o.get("type") != "null"]
                base = {k: v for k, v in node.items() if k != union}
                if options:
                    picked = walk(options[0], depth + 1)
                    return {**picked, **{k: v for k, v in base.items() if k in {"description"}}}
                return {"type": "string", **({"description": base["description"]} if "description" in base else {})}
        if "allOf" in node and len(node["allOf"]) == 1:
            return walk({**node["allOf"][0], **{k: v for k, v in node.items() if k != "allOf"}}, depth + 1)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _KEEP:
                continue
            if key == "type" and isinstance(value, list):  # some providers need a single type
                value = next((t for t in value if t != "null"), "string")
            if key == "enum" and isinstance(value, list):
                value = [v for v in value if v is not None] or value
            if key == "properties":
                out[key] = {p: walk(v, depth + 1) for p, v in value.items()}
            elif key == "items":
                out[key] = walk(value, depth + 1)
            else:
                out[key] = value
        if "type" not in out:
            out["type"] = "object" if "properties" in out else "string"
        if out.get("type") == "object":
            out.setdefault("properties", {})
            if "required" in out:
                out["required"] = [r for r in out["required"] if r in out["properties"]]
                if not out["required"]:
                    out.pop("required")
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out

    return walk(schema)


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", str(raw), re.S)
        return json.loads(m.group(0)) if m else {}


class BaseLLM:
    provider = "base"
    model = ""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             system: str | None = None, max_tokens: int = 1200, temperature: float = 0.2,
             tool_choice: str | dict[str, Any] | None = None) -> dict[str, Any]:
        """tool_choice: None/"auto", "none", "required", or {"name": "<tool>"}."""
        raise NotImplementedError

    def complete_json(self, system: str, user: str, max_tokens: int = 1200) -> Any:
        res = self.chat([{"role": "user", "content": user}], system=system + "\nRespond with JSON only.",
                        max_tokens=max_tokens, temperature=0)
        text = res.get("content") or ""
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not m:
            raise LLMError("Model did not return JSON")
        return json.loads(m.group(1))


BUSY = ("503", "429", "high demand", "overloaded", "unavailable", "resource_exhausted")


class OpenAICompatLLM(BaseLLM):
    def __init__(self, provider: str, base_url: str, api_key: str, model: str, fallbacks: list[str] | None = None):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallbacks = fallbacks or []
        self._http = httpx.Client(timeout=60)

    def _post(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        # a busy model: try the backup models before giving up
        last: LLMError | None = None
        for model in [self.model, *self.fallbacks]:
            try:
                return _post_with_retry(self._http, f"{self.base_url}/chat/completions", {**body, "model": model}, headers,
                                        provider=self.provider)
            except LLMError as exc:
                last = exc
                if not any(k in str(exc).lower() for k in BUSY):
                    raise
                log.warning("LLM %s busy, trying the next model", model)
        raise last or LLMError("LLM request failed")

    def chat(self, messages, tools=None, system=None, max_tokens=1200, temperature=0.2, tool_choice=None):
        payload_msgs: list[dict[str, Any]] = []
        if system:
            payload_msgs.append({"role": "system", "content": system})
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                payload_msgs.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": [{
                        "id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {})},
                    } for tc in m["tool_calls"]],
                })
            elif m["role"] == "tool":
                payload_msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                payload_msgs.append({"role": m["role"], "content": m.get("content") or ""})
        body: dict[str, Any] = {"model": self.model, "messages": payload_msgs, "temperature": temperature,
                                "max_tokens": max_tokens, **getattr(self, "extra", {})}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"][:1024],
                "parameters": sanitize_schema(t["parameters"])}} for t in tools]
            if isinstance(tool_choice, dict) and tool_choice.get("name"):
                body["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
            else:
                body["tool_choice"] = tool_choice if tool_choice in {"none", "required"} else "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            data = self._post(body, headers)
        except RateLimited:
            raise
        except LLMError as exc:
            if "reasoning" in str(exc).lower() and "reasoning_effort" in body:
                self.extra = {}  # this model has no thinking setting
                body.pop("reasoning_effort", None)
                data = self._post(body, headers)
            elif "max_tokens" in str(exc) or "temperature" in str(exc):  # newer OpenAI models reject these
                body.pop("temperature", None)
                body["max_completion_tokens"] = body.pop("max_tokens", max_tokens)
                data = self._post(body, headers)
            else:
                raise
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        calls = [{
            "id": tc.get("id") or f"call_{i}_{int(time.time())}",
            "name": tc["function"]["name"],
            "arguments": _parse_args(tc["function"].get("arguments")),
        } for i, tc in enumerate(choice.get("tool_calls") or [])]
        return {"content": choice.get("content") or "", "tool_calls": calls}


class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=60)

    def chat(self, messages, tools=None, system=None, max_tokens=1200, temperature=0.2, tool_choice=None):
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                content: list[dict[str, Any]] = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                                    "input": tc.get("arguments") or {}})
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
            elif m["role"] == "user":
                out.append({"role": "user", "content": m.get("content") or ""})
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": out,
                                "temperature": temperature}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"][:1024],
                              "input_schema": sanitize_schema(t["parameters"])} for t in tools]
            if isinstance(tool_choice, dict) and tool_choice.get("name"):
                body["tool_choice"] = {"type": "tool", "name": tool_choice["name"]}
            elif tool_choice == "required":
                body["tool_choice"] = {"type": "any"}
            elif tool_choice == "none":
                body["tool_choice"] = {"type": "none"}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = _post_with_retry(self._http, f"{self.base_url}/v1/messages", body, headers, provider="anthropic")
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input") or {}}
                 for b in data.get("content", []) if b.get("type") == "tool_use"]
        return {"content": text, "tool_calls": calls}


def _post_with_retry(http: httpx.Client, url: str, body: dict, headers: dict, retries: int = 2,
                     provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    model = model or body.get("model")
    for attempt in range(retries + 1):
        try:
            resp = http.post(url, json=body, headers=headers)
        except httpx.TransportError as exc:
            if attempt == retries:
                record("error", f"could not reach the provider: {exc}", provider, model)
                raise LLMError(f"Could not reach the LLM provider: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 429 or "RESOURCE_EXHAUSTED" in (resp.text or "")[:2000] and not resp.is_success:
            wait = _retry_after(resp)
            record("rate_limit", f"HTTP {resp.status_code}, retry after {wait or '?'}s: {resp.text[:200]}", provider, model)
            if attempt < retries and (wait or 2) <= 12:  # a short wait is fine; a long one means try the backup model
                time.sleep(max(1.0, wait or 2 * (attempt + 1)))
                continue
            raise RateLimited(f"Rate limit hit (HTTP 429) on {provider or 'the AI model'} {model}: {resp.text[:200]}")
        if resp.status_code in (500, 502, 503, 529) and attempt < retries:
            time.sleep(2 * (attempt + 1))
            continue
        if not resp.is_success:
            record("error", f"HTTP {resp.status_code}: {resp.text[:200]}", provider, model)
            raise LLMError(f"LLM provider error {resp.status_code}: {resp.text[:400]}")
        return resp.json()
    raise LLMError("LLM request failed")


_llm: BaseLLM | None = None
_llm_key: tuple | None = None


def get_llm() -> BaseLLM | None:
    """Return the configured LLM, or None when running offline."""
    global _llm, _llm_key
    s = config.settings
    provider = s.resolved_llm_provider
    key = (provider, s.llm_model, s.llm_base_url, bool(s.llm_api_key), tuple(s.llm_fallback_models))
    if key == _llm_key:
        return _llm
    _llm_key = key
    if provider == "gemini":  # one Gemini client for every AI feature
        _llm = get_gemini()
    elif provider == "offline":
        _llm = None
    elif provider == "anthropic":
        _llm = AnthropicLLM(s.llm_api_key, s.llm_model, s.llm_base_url or "https://api.anthropic.com")
    else:
        _llm = OpenAICompatLLM(provider, s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_fallback_models)
    return _llm


GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
_gemini: BaseLLM | None = None
_gemini_key: tuple | None = None


def get_gemini() -> BaseLLM | None:
    """Google Gemini (OpenAI-compatible endpoint), used by every AI agent, or None when GEMINI_API_KEY isn't set."""
    global _gemini, _gemini_key
    s = config.settings
    key = (bool(s.gemini_api_key), s.gemini_model, s.gemini_thinking)
    if key == _gemini_key:
        return _gemini
    _gemini_key = key
    _gemini = None
    if s.gemini_api_key:
        _gemini = OpenAICompatLLM("gemini", GEMINI_BASE_URL, s.gemini_api_key, s.gemini_model or "gemini-flash-latest",
                                  [m for m in ("gemini-2.5-flash",) if m != s.gemini_model])
        _gemini._http = httpx.Client(timeout=30)  # a live negotiation can't wait long
        if s.gemini_thinking in {"none", "low", "medium", "high"}:
            _gemini.extra = {"reasoning_effort": s.gemini_thinking}  # how much Gemini thinks before answering
    return _gemini


GEMINI_NATIVE_URL = os.getenv("GEMINI_NATIVE_URL", GEMINI_BASE_URL.removesuffix("/openai")).rstrip("/")
_ground_http: httpx.Client | None = None


def gemini_grounded(system: str, user: str, temperature: float = 0.2) -> dict[str, Any] | None:
    """Gemini with Google Search grounding, so it can look up real, current market prices on the web.
    Returns {"text", "thoughts", "sources": [{"title", "uri"}], "model"}, or None when no Gemini key is set."""
    global _ground_http
    s = config.settings
    if not s.gemini_api_key:
        return None
    _ground_http = _ground_http or httpx.Client(timeout=45)
    headers = {"Content-Type": "application/json", "x-goog-api-key": s.gemini_api_key}
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": temperature, "thinkingConfig": {"includeThoughts": True}},
    }
    models = [s.gemini_model or "gemini-flash-latest"] + [m for m in ("gemini-2.5-flash",) if m != s.gemini_model]
    last: LLMError | None = None
    for model in models:
        url = f"{GEMINI_NATIVE_URL}/models/{model}:generateContent"
        try:
            try:
                data = _post_with_retry(_ground_http, url, {**body}, headers, provider="gemini", model=model)
            except RateLimited:
                raise
            except LLMError as exc:
                if "thinking" not in str(exc).lower():
                    raise
                plain = {**body, "generationConfig": {"temperature": temperature}}  # model without thought summaries
                data = _post_with_retry(_ground_http, url, plain, headers, provider="gemini", model=model)
        except LLMError as exc:
            last = exc
            if isinstance(exc, RateLimited) or any(k in str(exc).lower() for k in BUSY):
                continue  # each model has its own quota: try the backup
            raise
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        thoughts = " ".join(p.get("text", "") for p in parts if p.get("thought")).strip()
        chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
        sources, seen = [], set()
        for c in chunks:
            web = c.get("web") or {}
            title, uri = (web.get("title") or "").strip(), (web.get("uri") or "").strip()
            if uri.startswith("https://") and title and title not in seen:
                seen.add(title)
                sources.append({"title": title[:80], "uri": uri})
        return {"text": text, "thoughts": re.sub(r"\s+", " ", thoughts)[:600], "sources": sources[:5], "model": model}
    raise last or LLMError("Gemini request failed")


def describe() -> dict[str, Any]:
    s = config.settings
    llm = get_llm()
    g = get_gemini()
    return {"provider": s.resolved_llm_provider, "model": llm.model if llm else None, "online": llm is not None,
            "gemini": {"online": g is not None, "model": g.model if g else None}}
