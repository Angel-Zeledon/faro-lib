"""
Local LLM client — replaces the paid Anthropic API for text generation.

Talks to a local Ollama server (default http://localhost:11434) running DeepSeek.
Exposes the same minimal surface the codebase used from the Anthropic SDK:

    client.messages.create(model=..., max_tokens=..., system=..., messages=[...])
        -> resp.content[0].text
        -> resp.usage.input_tokens / resp.usage.output_tokens

This lets every call site stay structurally the same — only the client
construction changed (anthropic.Anthropic(api_key=...) -> get_local_llm_client()).
The `model` argument passed by callers is accepted for signature compatibility
but ignored; the actual model served is controlled by settings.local_llm_model,
since the local server only has one model loaded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from backend.config import settings

log = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    """
    DeepSeek-R1 emits its chain-of-thought wrapped in <think>...</think> before the
    actual answer. Older Ollama versions / templates return it inline in
    message.content — strip it so callers only see the final answer, matching what
    they got from Claude (which never exposed reasoning this way).
    """
    return _THINK_BLOCK_RE.sub("", text).strip()


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _ContentBlock:
    text: str


@dataclass
class _LocalLLMResponse:
    content: list = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


class _Messages:
    def __init__(self, base_url: str, model: str, timeout: float):
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    def create(
        self,
        model: str | None = None,   # accepted for Anthropic-SDK compatibility, ignored
        max_tokens: int = 1024,
        system: str | None = None,
        messages: list | None = None,
        **_ignored,
    ) -> _LocalLLMResponse:
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages or [])

        resp = httpx.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": payload_messages,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        raw_text = (data.get("message") or {}).get("content", "")
        text = _strip_thinking(raw_text)

        usage = _Usage(
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )
        return _LocalLLMResponse(content=[_ContentBlock(text=text)], usage=usage)


class LocalLLMClient:
    """Drop-in replacement for anthropic.Anthropic() backed by a local Ollama server."""

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 60.0):
        self._base_url = (base_url or settings.local_llm_base_url).rstrip("/")
        self._model = model or settings.local_llm_model
        self.messages = _Messages(self._base_url, self._model, timeout)


def get_local_llm_client(timeout: float = 60.0) -> LocalLLMClient:
    """Always returns a usable client — no API key required. Connectivity/model
    errors surface as exceptions from messages.create(), same as the Anthropic SDK
    did when the API was unreachable, so existing try/except fallback paths still work."""
    return LocalLLMClient(timeout=timeout)
