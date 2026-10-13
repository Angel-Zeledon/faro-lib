"""The providers a company may choose, and what the form needs to know of each.

Endpoints were taken from each provider's own documentation on 2026-10-13
(see the package header in `__init__.py` for the exact URLs and sources). The
suggested model names are a starting point only: the form offers the provider's
LIVE model list (`list models`) whenever the provider has one, so a renamed or
retired model shows up as a real choice, not as a stale string from this file.

Text the user reads (the hint on where to get a key, the free-tier note) is
NOT here: it lives in the frontend catalogue under `aiProvider.hint.<key>` and
`aiProvider.free_note`, keyed by the provider key below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Wire = Literal["openai", "anthropic"]


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str                       # a product name, the same in every language
    wire: Wire                       # which request/response shape it speaks
    base_url: str                    # "" for `custom`: the company supplies it
    free: bool                       # has a free tier a company can start on
    key_required: bool
    docs_url: str                    # where a company gets a key
    suggested_models: tuple[str, ...] = field(default_factory=tuple)
    lists_models: bool = True
    custom_base_url: bool = False
    supports_org: bool = False       # OpenAI organisation / project headers

    @property
    def default_model(self) -> str:
        return self.suggested_models[0] if self.suggested_models else ""


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        key="openai", label="OpenAI", wire="openai",
        base_url="https://api.openai.com/v1",
        free=False, key_required=True,
        docs_url="https://platform.openai.com/api-keys",
        suggested_models=("gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"),
        supports_org=True,
    ),
    "anthropic": ProviderSpec(
        key="anthropic", label="Claude (Anthropic)", wire="anthropic",
        base_url="https://api.anthropic.com/v1",
        free=False, key_required=True,
        docs_url="https://console.anthropic.com/settings/keys",
        suggested_models=("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
    ),
    "google": ProviderSpec(
        key="google", label="Google Gemini", wire="openai",
        # Gemini's OpenAI-compatible layer: same request shape, same bearer
        # auth, one adapter less to keep correct.
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        free=True, key_required=True,
        docs_url="https://aistudio.google.com/apikey",
        suggested_models=("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"),
    ),
    "groq": ProviderSpec(
        key="groq", label="Groq", wire="openai",
        base_url="https://api.groq.com/openai/v1",
        free=True, key_required=True,
        docs_url="https://console.groq.com/keys",
        suggested_models=("llama-3.3-70b-versatile", "openai/gpt-oss-120b",
                          "llama-3.1-8b-instant"),
    ),
    "openrouter": ProviderSpec(
        key="openrouter", label="OpenRouter", wire="openai",
        base_url="https://openrouter.ai/api/v1",
        free=True, key_required=True,
        docs_url="https://openrouter.ai/settings/keys",
        # `:free` variants cost nothing and are rate limited per account.
        suggested_models=("meta-llama/llama-3.3-70b-instruct:free",
                          "deepseek/deepseek-chat-v3-0324:free",
                          "openai/gpt-4.1-mini"),
    ),
    "deepseek": ProviderSpec(
        key="deepseek", label="DeepSeek", wire="openai",
        base_url="https://api.deepseek.com",
        free=False, key_required=True,
        docs_url="https://platform.deepseek.com/api_keys",
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
    ),
    "custom": ProviderSpec(
        key="custom", label="OpenAI-compatible URL", wire="openai",
        base_url="",
        free=False, key_required=False,
        docs_url="",
        suggested_models=(),
        custom_base_url=True,
    ),
}


def get(provider: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider)


def public_catalogue() -> list[dict]:
    """The catalogue as the form reads it. No copy: codes and flags only."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "wire": p.wire,
            "free": p.free,
            "key_required": p.key_required,
            "docs_url": p.docs_url,
            "suggested_models": list(p.suggested_models),
            "default_model": p.default_model,
            "lists_models": p.lists_models,
            "custom_base_url": p.custom_base_url,
            "supports_org": p.supports_org,
        }
        for p in PROVIDERS.values()
    ]
