"""Answers the Studio AI's OpenAI Responses API calls with the configured LLM."""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any, Iterator

from .llm import BaseLLM, LLMError, _parse_args

MAX_TOKENS = 8192


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") in {"input_text", "output_text", "text"}:
            parts.append(c.get("text") or "")
        elif isinstance(c, dict) and c.get("type") == "refusal":
            parts.append(c.get("refusal") or "")
    return "\n".join(p for p in parts if p)


def to_chat(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Responses `instructions` + `input` items → (system prompt, chat messages)."""
    system_parts = [body["instructions"]] if body.get("instructions") else []
    messages: list[dict[str, Any]] = []
    items = body.get("input") or []
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    for item in items:
        kind = item.get("type") or "message"
        if kind == "message":
            role = item.get("role", "user")
            text = _text_of(item.get("content"))
            if role in {"system", "developer"}:
                system_parts.append(text)
            elif role == "assistant":
                messages.append({"role": "assistant", "content": text})
            else:
                messages.append({"role": "user", "content": text})
        elif kind == "function_call":
            call = {"id": item.get("call_id") or item.get("id"), "name": item["name"],
                    "arguments": _parse_args(item.get("arguments"))}
            last = messages[-1] if messages else None
            if last and last["role"] == "assistant":
                last.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif kind == "function_call_output":
            out = item.get("output")
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"),
                             "content": out if isinstance(out, str) else json.dumps(out)})
        # skip reasoning items
    # must start with a user message
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return "\n\n".join(p for p in system_parts if p), messages


def _json_format(body: dict[str, Any]) -> dict[str, Any] | None:
    fmt = ((body.get("text") or {}).get("format")) or {}
    return fmt if fmt.get("type") == "json_schema" else None


def run(llm: BaseLLM, body: dict[str, Any]) -> dict[str, Any]:
    """Run one Responses request on the backup LLM and return a Responses `response` object."""
    system, messages = to_chat(body)
    if not messages:
        raise LLMError("The request has no user message.")
    fmt = _json_format(body)
    if fmt:
        system += ("\n\nReply with ONLY a JSON object (no prose, no code fences) that matches this JSON schema:\n"
                   + json.dumps(fmt.get("schema") or {})[:12000])
    tools = [{"name": t["name"], "description": t.get("description") or "", "parameters": t.get("parameters") or {}}
             for t in body.get("tools") or [] if t.get("type") == "function"]
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        choice = {"name": choice.get("name")}
    res = llm.chat(messages, tools=tools or None, system=system or None,
                   max_tokens=min(int(body.get("max_output_tokens") or MAX_TOKENS), MAX_TOKENS),
                   temperature=0.2, tool_choice=choice)
    text = res.get("content") or ""
    if fmt and text:
        m = re.search(r"\{.*\}", text, re.S)
        text = m.group(0) if m else text
    output: list[dict[str, Any]] = []
    if text:
        output.append({"id": f"msg_{secrets.token_hex(8)}", "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]})
    for call in res.get("tool_calls") or []:
        output.append({"id": f"fc_{secrets.token_hex(8)}", "type": "function_call", "status": "completed",
                       "call_id": call["id"], "name": call["name"], "arguments": json.dumps(call.get("arguments") or {})})
    return {"id": f"resp_{secrets.token_hex(12)}", "object": "response", "created_at": int(time.time()),
            "model": getattr(llm, "model", ""), "status": "completed", "output": output, "error": None,
            "incomplete_details": None, "usage": None}


def sse(response: dict[str, Any]) -> Iterator[bytes]:
    """The Responses streaming events for a finished response."""
    seq = 0

    def event(payload: dict[str, Any]) -> bytes:
        nonlocal seq
        payload["sequence_number"] = seq
        seq += 1
        return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()

    shell = {**response, "status": "in_progress", "output": []}
    yield event({"type": "response.created", "response": shell})
    yield event({"type": "response.in_progress", "response": shell})
    for index, item in enumerate(response["output"]):
        if item["type"] == "message":
            yield event({"type": "response.output_item.added", "output_index": index,
                         "item": {**item, "status": "in_progress", "content": []}})
            text = item["content"][0]["text"]
            part = {"type": "output_text", "text": "", "annotations": []}
            yield event({"type": "response.content_part.added", "item_id": item["id"], "output_index": index,
                         "content_index": 0, "part": part})
            for i in range(0, len(text), 60):  # stream in small chunks
                yield event({"type": "response.output_text.delta", "item_id": item["id"], "output_index": index,
                             "content_index": 0, "delta": text[i:i + 60]})
            yield event({"type": "response.output_text.done", "item_id": item["id"], "output_index": index,
                         "content_index": 0, "text": text})
            yield event({"type": "response.content_part.done", "item_id": item["id"], "output_index": index,
                         "content_index": 0, "part": {**part, "text": text}})
        else:
            yield event({"type": "response.output_item.added", "output_index": index,
                         "item": {**item, "status": "in_progress", "arguments": ""}})
            yield event({"type": "response.function_call_arguments.delta", "item_id": item["id"],
                         "output_index": index, "delta": item["arguments"]})
            yield event({"type": "response.function_call_arguments.done", "item_id": item["id"],
                         "output_index": index, "arguments": item["arguments"]})
        yield event({"type": "response.output_item.done", "output_index": index, "item": item})
    yield event({"type": "response.completed", "response": response})
