"""AI completion client — one factory, one backend: DeepSeek.

Every AI feature in the product goes through `get_local_llm_client()`: the
morning narrative, the inventory insight, the RAG analyst, the chat, and the
data-quality diagnosis. There used to be three possible backends behind it —
DeepSeek, Anthropic, and a local Ollama server — chosen by whichever key
happened to be set.

That is gone. This deployment runs on DeepSeek, so DeepSeek is the only thing
here. Three reasons, in order of how much they cost:

  1. A fallback chain decides silently. "Whichever key is set" means a missing
     or mistyped `DEEPSEEK_API_KEY` did not fail — it quietly answered from
     somewhere else, and the only symptom was a different bill or a worse
     answer. Now a missing key raises, at the call, saying which variable.
  2. The Ollama branch could not work in production anyway: there is no Ollama
     container in `deploy/docker-compose.prod.yml`. It was a local-dev
     convenience that read, from the code, like a supported deployment mode.
  3. Two dead branches are two branches every future reader has to understand
     before changing anything here.

The surface every consumer already uses is unchanged:

    client.messages.create(model=..., max_tokens=..., system=..., messages=[...])
        -> resp.content[0].text
        -> resp.usage.input_tokens / resp.usage.output_tokens

`model` is accepted and ignored — call sites pass whatever string they were
written against, and the configured `DEEPSEEK_MODEL` is what runs.

Configuration is read through `backend/service_config/resolver.effective()`
rather than straight off `settings`, so a key entered in the configuration
screen takes effect on the next call instead of at the next restart. With no
override stored, that resolves to exactly what `settings` holds.

No SDK: DeepSeek's API is OpenAI-shaped (`POST {base}/chat/completions`, bearer
auth) and this is one POST, so a dependency would buy nothing and add a version
to keep pinned.

Note for test authors: `backend/tests/conftest.py` patches this factory
session-wide, so the suite never reaches the real API with a live key in `.env`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from backend.service_config.resolver import effective

log = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    """Remove a chain-of-thought block a model emitted inline.

    `deepseek-reasoner` normally puts its reasoning in a separate
    `reasoning_content` field, which is never read here. A model or gateway that
    inlines it as `<think>…</think>` instead would otherwise ship a paragraph of
    the model thinking out loud straight into a customer's narrative.
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
class _LLMResponse:
    content: list = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


class _DeepSeekMessages:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def create(
        self,
        model: str | None = None,   # accepted for interface compatibility, ignored
        max_tokens: int = 1024,
        system: str | None = None,
        messages: list | None = None,
        **_ignored,
    ) -> _LLMResponse:
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages or [])

        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": payload_messages,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        raw_text = ((choices[0] if choices else {}).get("message") or {}).get("content") or ""
        text = _strip_thinking(raw_text)

        # DeepSeek reports OpenAI's names for the two counts every caller in this
        # codebase reads as Anthropic's. Translated here, once, so no consumer
        # has to learn whose API answered.
        usage_in = data.get("usage") or {}
        usage = _Usage(
            input_tokens=usage_in.get("prompt_tokens", 0),
            output_tokens=usage_in.get("completion_tokens", 0),
        )
        return _LLMResponse(content=[_ContentBlock(text=text)], usage=usage)


class DeepSeekClient:
    """The AI client. Same shape the consumers were always written against."""

    def __init__(self, timeout: float = 60.0):
        # Read through the override layer, not straight off `settings`, so a key
        # pasted into the configuration panel takes effect without a restart.
        # With no override stored this returns exactly what `settings` holds.
        cfg = effective()
        self.messages = _DeepSeekMessages(
            cfg.deepseek_api_key,
            cfg.deepseek_base_url,
            cfg.deepseek_model,
            timeout,
        )


class LLMNotConfigured(RuntimeError):
    """No `DEEPSEEK_API_KEY`. Raised where the call is made, not at import.

    Deliberately loud. The chain this replaced answered from a different
    provider when the key was missing, so a typo in the variable name produced
    working AI features and a surprising invoice — the failure mode that costs
    the most to notice. Callers already wrap AI calls in try/except and degrade
    to their rule-based text, so this degrades the same way, while the log line
    names the actual problem.
    """


def get_local_llm_client(timeout: float = 60.0) -> DeepSeekClient:
    """The AI client for this deployment.

    Named `get_local_llm_client` for the same reason the module is still
    `local_llm.py`: five consumers import it under that name, and renaming a
    working seam across all of them buys nothing here. What it returns has been
    a hosted client since 2026-08-22.
    """
    if not effective().deepseek_api_key:
        raise LLMNotConfigured(
            "DEEPSEEK_API_KEY is not set — AI features (narrative, analyst, "
            "chat, data-quality diagnosis) cannot run. Set it in the "
            "environment, or from the configuration screen."
        )
    return DeepSeekClient(timeout=timeout)
