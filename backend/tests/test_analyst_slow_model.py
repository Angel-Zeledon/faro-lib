"""The analyst must fail inside the window it is given, and say why.

Found by asking the AI analyst a question with a local Ollama model behind it.
The chat path gave the LLM a 60s budget, ABOVE the frontend proxy's ~30s ceiling,
so a slow model could not win: the browser got a bare `500 Internal Server Error`
at exactly 30.0s — no code, no envelope, no explanation — and the model went on
to answer successfully at 63s, an answer nobody could ever see. On top of that,
the first message of a chat spent that whole budget generating a TITLE before
anything tried to answer.

These pin the two budgets and the shape of the failure. They deliberately assert
the ORDER (title < answer < proxy ceiling) rather than the literal seconds, so
tuning the numbers does not require editing the test — only breaking the
relationship does.
"""

import pytest

from backend.api.v1 import chats as chats_mod
from backend.errors import AppError

# Read from the module: the ceiling is a property of the proxy, and the test
# should track it rather than restate it.
PROXY_CEILING_S = chats_mod.PROXY_CEILING_S


class TestTheBudgetsFitTheWindow:
    def test_the_answer_budget_is_under_the_proxy_ceiling(self):
        assert chats_mod.LLM_BUDGET_S < PROXY_CEILING_S, (
            "the proxy will cut the request before the backend can report anything")

    def test_a_title_cannot_eat_the_answer_budget(self):
        """The title runs FIRST, so title + answer must still fit."""
        assert chats_mod._TITLE_BUDGET_S < chats_mod.LLM_BUDGET_S
        assert chats_mod._TITLE_BUDGET_S + chats_mod.LLM_BUDGET_S < PROXY_CEILING_S, (
            "a first question pays for a title and then runs out of window")


class TestTheFailureIsReportable:
    def test_a_slow_model_raises_a_coded_error_not_english_prose(self, monkeypatch):
        """It used to RETURN an English sentence as the analyst's own answer."""
        class _Slow:
            class messages:
                @staticmethod
                def create(**_kw):
                    raise TimeoutError("timed out")

        monkeypatch.setattr(
            "backend.ai.local_llm.get_local_llm_client", lambda timeout=0: _Slow())

        with pytest.raises(AppError) as caught:
            chats_mod._general_answer("¿Cuánto capital tengo inmovilizado?", [])

        err = caught.value
        assert err.code == "ai_unavailable"
        assert err.status_code == 503, "a slow dependency is not a 500"
        # The number the copy quotes has to come from the budget, not a literal.
        assert err.params["budget_seconds"] == int(chats_mod.LLM_BUDGET_S)

    def test_the_title_falls_back_to_the_question_without_failing(self, monkeypatch):
        """A title is decoration: losing it must not cost the user their answer."""
        class _Slow:
            class messages:
                @staticmethod
                def create(**_kw):
                    raise TimeoutError("timed out")

        monkeypatch.setattr(
            "backend.ai.local_llm.get_local_llm_client", lambda timeout=0: _Slow())

        title = chats_mod._auto_title("¿Cuáles son mis productos con mayor riesgo?")
        assert title.startswith("¿Cuáles son mis productos")

    def test_a_working_model_still_answers(self, monkeypatch):
        class _Block:
            text = "Tienes ₡14 743 inmovilizados."

        class _Fast:
            class messages:
                @staticmethod
                def create(**_kw):
                    return type("R", (), {"content": [_Block()]})()

        monkeypatch.setattr(
            "backend.ai.local_llm.get_local_llm_client", lambda timeout=0: _Fast())
        assert "14 743" in chats_mod._general_answer("¿Y el capital?", [])
