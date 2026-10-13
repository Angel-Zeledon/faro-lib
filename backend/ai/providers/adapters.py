"""Request and response mapping for the two wire shapes, into one interface.

Every consumer in this codebase speaks the surface of `backend/ai/local_llm.py`:

    client.messages.create(max_tokens=, system=, messages=[...], tools=[...])
        -> _LLMResponse(content=[_ContentBlock(text)], usage, tool_calls, message)

with `messages` and `tools` in the OpenAI shape (the assistant's tool loop
appends `resp.message` and `{"role": "tool", ...}` turns). The OpenAI-shaped
providers take that almost verbatim; Anthropic's Messages API gets it
translated here (system prompt hoisted, tool calls as `tool_use` blocks, tool
results as `tool_result` blocks in one user turn) and its answer translated
back, so the tool loop never learns whose API answered.

Validation lives here too: a 200 that carries no usable answer (no choices,
an empty or truncated text, a provider-side error inside a 200, an absurd
size) becomes `malformed_response` / `content_blocked` — never an exception
the user sees and never an empty bubble presented as an answer.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.ai.providers.catalog import ProviderSpec
from backend.ai.providers.errors import ProviderError, classify_http

ANTHROPIC_VERSION = "2023-06-01"
# Current Claude models think before answering and that thinking counts
# against `max_tokens`; a 300-token budget can end before any visible text.
ANTHROPIC_MIN_MAX_TOKENS = 1024
MAX_TEXT_CHARS = 200_000

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text).strip()


def _join_url(base: str, path: str) -> str:
    """`base` may carry a query (Azure's `?api-version=`): the path goes before it."""
    if "?" in base:
        b, q = base.split("?", 1)
        return f"{b.rstrip('/')}{path}?{q}"
    return f"{base.rstrip('/')}{path}"


def _azure(base_url: str) -> bool:
    host = re.sub(r"^https?://", "", base_url or "").split("/", 1)[0].split(":", 1)[0].lower()
    return host.endswith(".openai.azure.com") or host.endswith(".cognitiveservices.azure.com")


# ── OpenAI-shaped (OpenAI, Gemini compat, Groq, OpenRouter, DeepSeek, custom) ─

def openai_headers(spec: ProviderSpec, *, api_key: str, base_url: str,
                   organization: str = "", project: str = "") -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
        if spec.key == "custom" and _azure(base_url):
            h["api-key"] = api_key
    if spec.supports_org:
        if organization:
            h["OpenAI-Organization"] = organization
        if project:
            h["OpenAI-Project"] = project
    if spec.key == "openrouter":
        # Optional attribution headers OpenRouter documents; no secret in them.
        h["X-Title"] = "StockAI"
    return h


def openai_body(spec: ProviderSpec, *, model: str, system: Optional[str], messages: list,
                max_tokens: int, tools: Optional[list], tool_choice: Optional[str],
                temperature: Optional[float]) -> dict:
    payload_messages: list = []
    if system:
        payload_messages.append({"role": "system", "content": system})
    payload_messages.extend(messages or [])
    body: dict[str, Any] = {"model": model, "messages": payload_messages, "stream": False}
    # OpenAI deprecated `max_tokens` for `max_completion_tokens` (its reasoning
    # models refuse the old name); the compatible providers still read `max_tokens`.
    if spec.key == "openai":
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    return body


def _text_of_openai_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some gateways return content parts
        return "".join(str(p.get("text") or "") for p in content
                       if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    return ""


def parse_openai(data: Any, *, model: str, secrets=()):
    from backend.ai.local_llm import _ContentBlock, _LLMResponse, _ToolCall, _Usage

    if not isinstance(data, dict):
        raise ProviderError("malformed_response", "answer is not a JSON object",
                            params={"reason": "not_an_object"}, phase="read")
    if data.get("error"):
        # OpenRouter reports some generation failures inside a 200.
        err = data["error"]
        status = err.get("code") if isinstance(err, dict) and isinstance(err.get("code"), int) else 502
        e = classify_http(status, json.dumps(data).encode(), {}, model=model, secrets=secrets)
        e.phase = "read"
        raise e
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("malformed_response", "answer has no choices",
                            params={"reason": "no_choices"}, phase="read")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderError("malformed_response", "choice has no message",
                            params={"reason": "no_message"}, phase="read")
    text = _strip_thinking(_text_of_openai_content(message.get("content")))
    if len(text) > MAX_TEXT_CHARS:
        raise ProviderError("malformed_response", "answer too large",
                            params={"reason": "too_large"}, phase="read")

    tool_calls = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        if isinstance(fn, dict) and fn.get("name"):
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args if args is not None else {})
            tool_calls.append(_ToolCall(id=str(call.get("id") or ""), name=str(fn["name"]),
                                        arguments=args or "{}"))

    finish = str(choice.get("finish_reason") or "")
    if not text and not tool_calls:
        if finish == "content_filter":
            raise ProviderError("content_blocked", "the provider filtered the answer", phase="read")
        if finish == "length":
            raise ProviderError("malformed_response", "answer cut at the token limit",
                                params={"reason": "truncated"}, phase="read")
        raise ProviderError("malformed_response", "empty answer",
                            params={"reason": "empty"}, phase="read")

    usage_in = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    usage = _Usage(input_tokens=_int(usage_in.get("prompt_tokens")),
                   output_tokens=_int(usage_in.get("completion_tokens")))
    out_message = {"role": "assistant", "content": message.get("content") if isinstance(
        message.get("content"), str) else text}
    if message.get("tool_calls"):
        out_message["tool_calls"] = message["tool_calls"]
    return _LLMResponse(content=[_ContentBlock(text=text)], usage=usage,
                        tool_calls=tool_calls, message=out_message)


def _int(v: Any) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


# ── Anthropic Messages API ───────────────────────────────────────────────────

def anthropic_headers(*, api_key: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION}


def _to_anthropic_messages(system: Optional[str], messages: list) -> tuple[str, list]:
    """OpenAI-shaped history -> (system, Anthropic messages). Consecutive turns
    of one role are merged, tool results are grouped into one user turn."""
    systems = [system] if system else []
    out: list[dict] = []

    def push(role: str, blocks: list):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                systems.append(str(content))
            continue
        if role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""),
                           "content": str(content or "")}])
            continue
        blocks: list = []
        text = _text_of_openai_content(content)
        if text:
            blocks.append({"type": "text", "text": text})
        if role == "assistant":
            for call in m.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                blocks.append({"type": "tool_use", "id": str(call.get("id") or ""),
                               "name": str(fn.get("name") or ""), "input": args})
            push("assistant", blocks)
        else:
            push("user", blocks)
    return "\n\n".join(s for s in systems if s), out


def anthropic_body(*, model: str, system: Optional[str], messages: list, max_tokens: int,
                   tools: Optional[list], tool_choice: Optional[str]) -> dict:
    sys_text, msgs = _to_anthropic_messages(system, messages)
    body: dict[str, Any] = {"model": model, "messages": msgs,
                            "max_tokens": max(max_tokens, ANTHROPIC_MIN_MAX_TOKENS)}
    if sys_text:
        body["system"] = sys_text
    # No `temperature`: current Claude models reject sampling parameters.
    if tools:
        converted = []
        for t in tools:
            fn = (t or {}).get("function") or {}
            if fn.get("name"):
                converted.append({"name": fn["name"], "description": fn.get("description") or "",
                                  "input_schema": fn.get("parameters") or {"type": "object",
                                                                           "properties": {}}})
        if converted:
            body["tools"] = converted
            # Forced tool choice is refused by current models; only these two map.
            if tool_choice in ("auto", "none"):
                body["tool_choice"] = {"type": tool_choice}
    return body


def parse_anthropic(data: Any, *, model: str, secrets=()):
    from backend.ai.local_llm import _ContentBlock, _LLMResponse, _ToolCall, _Usage

    if not isinstance(data, dict):
        raise ProviderError("malformed_response", "answer is not a JSON object",
                            params={"reason": "not_an_object"}, phase="read")
    if data.get("type") == "error":
        e = classify_http(500, json.dumps(data).encode(), {}, model=model, secrets=secrets)
        e.phase = "read"
        raise e
    content = data.get("content")
    if not isinstance(content, list):
        raise ProviderError("malformed_response", "answer has no content",
                            params={"reason": "no_choices"}, phase="read")
    texts, tool_calls, oa_calls = [], [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use" and block.get("name"):
            args = json.dumps(block.get("input") or {})
            tool_calls.append(_ToolCall(id=str(block.get("id") or ""), name=str(block["name"]),
                                        arguments=args))
            oa_calls.append({"id": str(block.get("id") or ""), "type": "function",
                             "function": {"name": str(block["name"]), "arguments": args}})
        # thinking / redacted_thinking and anything newer: not shown, not echoed.
    text = _strip_thinking("".join(texts))
    if len(text) > MAX_TEXT_CHARS:
        raise ProviderError("malformed_response", "answer too large",
                            params={"reason": "too_large"}, phase="read")
    stop = str(data.get("stop_reason") or "")
    if stop == "refusal" and not text:
        raise ProviderError("content_blocked", "the model declined the request", phase="read")
    if not text and not tool_calls:
        if stop == "max_tokens":
            raise ProviderError("malformed_response", "answer cut at the token limit",
                                params={"reason": "truncated"}, phase="read")
        raise ProviderError("malformed_response", "empty answer",
                            params={"reason": "empty"}, phase="read")
    usage_in = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    usage = _Usage(input_tokens=_int(usage_in.get("input_tokens")),
                   output_tokens=_int(usage_in.get("output_tokens")))
    message: dict = {"role": "assistant", "content": text}
    if oa_calls:
        message["tool_calls"] = oa_calls
    return _LLMResponse(content=[_ContentBlock(text=text)], usage=usage,
                        tool_calls=tool_calls, message=message)


# ── Model lists ──────────────────────────────────────────────────────────────

MAX_MODELS = 400


def parse_models(data: Any, spec: ProviderSpec) -> list[dict]:
    """`GET {base}/models` -> [{"id", "free"}]. Same `data[].id` shape for every
    provider here (OpenAI, Anthropic, Gemini compat, Groq, OpenRouter,
    DeepSeek, Ollama's /v1/models)."""
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ProviderError("malformed_response", "model list has no data",
                            params={"reason": "no_models"}, phase="read")
    out = []
    for it in items[:MAX_MODELS * 3]:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        mid = str(it["id"])
        if spec.key == "google":
            mid = mid.removeprefix("models/")
            if "embedding" in mid or "imagen" in mid or "veo" in mid:
                continue
        free = False
        if spec.key == "openrouter":
            pricing = it.get("pricing") or {}
            free = mid.endswith(":free") or (str(pricing.get("prompt")) == "0"
                                             and str(pricing.get("completion")) == "0")
        out.append({"id": mid, "free": free})
    out.sort(key=lambda m: (not m["free"], m["id"]))
    return out[:MAX_MODELS]


def models_path() -> str:
    return "/models"


def chat_path(spec: ProviderSpec) -> str:
    return "/messages" if spec.wire == "anthropic" else "/chat/completions"


join_url = _join_url
