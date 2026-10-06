"""TSB is offered as an opt-in model, and nothing selects it for the user.

Pure tests: no database, no client. The catalogue (`GET /models`), the
selection validator and the default model lists are read directly.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SELECTION = ["global_lgbm", "lightgbm", "prophet", "croston", "xgboost"]


def _catalogue():
    from backend.api.v1.models import _MODELS
    return {m["name"]: m for m in _MODELS}


def test_tsb_catalogue_entry():
    tsb = _catalogue()["tsb"]
    assert tsb["category"] == "Statistical"
    assert tsb["status"] == "available"
    text = tsb["recommended_for"].lower()
    assert "discontinued" in text and "end-of-life" in text and "intermittent" in text
    assert "not selected by default" in text


def test_catalogue_is_exactly_what_the_engine_offers():
    from forecasting_core.models.factory import ModelFactory
    assert set(_catalogue()) == set(ModelFactory.available_models())
    # ADIDA / IMAPA are engine-only (benchmark), never offered.
    assert not set(_catalogue()) & {"adida", "imapa"}


def test_selection_accepts_tsb_and_refuses_the_engine_only_models():
    from backend.schemas.configuration import ModelsConfigRequest
    req = ModelsConfigRequest(mode="selected", selected_models=["tsb", "croston"])
    assert req.selected_models == ["tsb", "croston"]
    for name in ("adida", "imapa"):
        with pytest.raises(ValidationError, match="unknown_model|Unknown model"):
            ModelsConfigRequest(mode="selected", selected_models=[name])


def test_backend_default_selection_is_unchanged():
    from backend.sessions.defaults import default_quickstart_configs
    selected = default_quickstart_configs()["models_cfg"]["selected_models"]
    assert selected == DEFAULT_SELECTION
    assert "tsb" not in selected


def test_wizard_default_selection_is_unchanged():
    """The sales wizard posts a fixed list; TSB must not have crept into it."""
    page = (REPO / "Frontend" / "src" / "app" / "ventas" / "page.tsx").read_text(encoding="utf-8")
    line = next(ln for ln in page.splitlines() if "await setModels(" in ln)
    assert line.strip() == ("await setModels(sessionId, ['global_lgbm', 'lightgbm', "
                            "'prophet', 'croston', 'xgboost'])")


def test_tsb_has_a_stable_model_number_in_the_ui():
    """modelLabel numbers models by their position; TSB is appended, so every
    number a user already learned stays the same."""
    src = (REPO / "Frontend" / "src" / "lib" / "modelLabel.ts").read_text(encoding="utf-8")
    body = src.split("export const MODEL_ORDER = [", 1)[1].split("]", 1)[0]
    order = [t.strip().strip("'") for t in body.replace("\n", " ").split(",") if t.strip()]
    assert order == ["lightgbm", "xgboost", "prophet", "arima", "ets", "croston",
                     "sarimax", "lstm", "global_lgbm", "tsb"]
