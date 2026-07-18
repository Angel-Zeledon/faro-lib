"""
AI completion client factory — one factory, two possible backends.

get_local_llm_client() returns a real Anthropic-backed client when
settings.anthropic_api_key is configured, else a local Ollama shim. Both
expose the identical minimal surface every AI consumer already uses:

    client.messages.create(model=..., max_tokens=..., system=..., messages=[...])
        -> resp.content[0].text
        -> resp.usage.input_tokens / resp.usage.output_tokens

so every call site (rag_service.py, chats.py, narrator.py,
narrative_service.py, configuration.py's data-quality diagnosis) stays
structurally the same regardless of which backend is active — setting or
clearing ANTHROPIC_API_KEY alone switches all of them. Both backends also
ignore whatever `model` string a caller passes (some pass a stale/informal
placeholder, some pass none at all) and use their own configured model
instead — settings.anthropic_model for the Anthropic path, since a caller
targeting one backend's model naming shouldn't need to know or care which
backend actually served the request.
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


class _AnthropicMessages:
    """Wraps the real Anthropic SDK's .messages, forcing our configured model
    regardless of whatever (possibly stale or backend-specific) model string
    a caller passes — mirrors _Messages.create's same "accept but ignore the
    caller's model" contract above."""

    def __init__(self, real_client, model: str):
        self._real = real_client
        self._model = model

    def create(
        self,
        model: str | None = None,   # accepted for interface compatibility, ignored
        max_tokens: int = 1024,
        system: str | None = None,
        messages: list | None = None,
        **kwargs,
    ):
        call_kwargs = {"model": self._model, "max_tokens": max_tokens, "messages": messages or []}
        if system:
            call_kwargs["system"] = system
        call_kwargs.update(kwargs)
        return self._real.messages.create(**call_kwargs)


class _AnthropicBackedClient:
    """Drop-in replacement for LocalLLMClient, backed by the real Anthropic API."""

    def __init__(self, timeout: float = 60.0):
        import anthropic

        real = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout)
        self.messages = _AnthropicMessages(real, settings.anthropic_model)


def get_local_llm_client(timeout: float = 60.0):
    """
    Returns the active AI client for this deployment: a real Anthropic-backed
    client when settings.anthropic_api_key is set, else the local Ollama shim
    (no API key required for the local path). Connectivity/model errors
    surface as exceptions from messages.create() either way, so existing
    try/except fallback paths in callers work unchanged.
    """
    if settings.anthropic_api_key:
        return _AnthropicBackedClient(timeout=timeout)
    return LocalLLMClient(timeout=timeout)
