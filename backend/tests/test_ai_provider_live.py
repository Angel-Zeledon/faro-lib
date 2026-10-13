"""Live smoke tests against REAL providers — skipped unless a key is exported.

Each test spends a few tokens on the key named in its env var, so none of them
runs in the normal suite. To run one:

    STOCKAI_LIVE_OPENAI_KEY=sk-...  python -m pytest tests/test_ai_provider_live.py -q

Optional model overrides: STOCKAI_LIVE_<PROVIDER>_MODEL.
"""
from __future__ import annotations

import os

import pytest

from backend.ai.providers import catalog, client, store

PROVIDERS = ["openai", "anthropic", "google", "groq", "openrouter", "deepseek"]


@pytest.mark.live_llm
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_real_provider_answers_and_lists_models(provider):
    key = os.environ.get(f"STOCKAI_LIVE_{provider.upper()}_KEY", "")
    if not key:
        pytest.skip(f"STOCKAI_LIVE_{provider.upper()}_KEY not set")
    spec = catalog.PROVIDERS[provider]
    model = os.environ.get(f"STOCKAI_LIVE_{provider.upper()}_MODEL") or spec.default_model
    cfg = store.ProviderConfig(tenant_id="live-smoke", provider=provider, model=model, api_key=key)
    result = client.test_connection(cfg, budget_s=30.0)
    assert result["ok"], result["error"]
    assert result["models"], result["models_error"]
    assert key not in repr(result)
