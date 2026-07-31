"""
The scenario simulator's refusals must be readable by a Spanish-speaking user.

Two things were wrong with how it said no, and they pulled in opposite
directions:

  * `raise ValueError("end_date no puede ser anterior a start_date")` — a
    hardcoded Spanish string inside backend logic, which CLAUDE.md forbids
    outright. The endpoint re-raised it as `HTTPException(422, str(e))`, so the
    Spanish went out on the wire with no code the frontend could key on.

  * An unparseable date never reached that check at all: `date.fromisoformat`
    raised first, and the SAME `except ValueError` handed the user Python's own
    "Invalid isoformat string: 'ayer'" — English, and naming an internal
    function to a distributor.

Both now raise `AppError` with a stable code plus params, and the Spanish lives
in `translations.ts` where the language rule says it belongs.
"""

import pytest

from backend.errors import AppError
from backend.inventory.service import simulate_event_impact


def _simulate(tenant_id, session_id, **kw):
    kwargs = {"start_date": "2026-12-01", "end_date": "2026-12-24",
              "multiplier": 2.0}
    kwargs.update(kw)
    return simulate_event_impact(tenant_id, session_id, **kwargs)


class TestAnUnreadableDate:

    @pytest.mark.parametrize("bad", [
        "ayer", "24-12-2026", "2026-13-01", "2026-02-30", "", "hoy", "12/24/2026",
    ])
    def test_it_raises_a_coded_error_not_pythons(self, test_tenant, test_session, bad):
        with pytest.raises(AppError) as exc:
            _simulate(test_tenant["id"], test_session["id"], start_date=bad)
        assert exc.value.code == "event_date_invalid"
        assert exc.value.status_code == 422

    def test_the_error_says_which_field_and_what_was_typed(
        self, test_tenant, test_session,
    ):
        """Without these the frontend can only print a generic sentence, and the
        user has two date fields to guess between."""
        with pytest.raises(AppError) as exc:
            _simulate(test_tenant["id"], test_session["id"], end_date="mañana")
        assert exc.value.params["field"] == "end_date"
        assert exc.value.params["value"] == "mañana"

    def test_no_spanish_leaks_from_the_backend_message(
        self, test_tenant, test_session,
    ):
        """The English message is the fallback for a frontend that has no
        mapping yet; Spanish in it would be the defect this file exists for."""
        with pytest.raises(AppError) as exc:
            _simulate(test_tenant["id"], test_session["id"], start_date="ayer")
        assert "puede" not in exc.value.message
        assert "isoformat" not in exc.value.message, (
            "an internal function name is not something to show a distributor"
        )


class TestAnImpossibleEvent:

    def test_ending_before_it_starts_is_refused(self, test_tenant, test_session):
        with pytest.raises(AppError) as exc:
            _simulate(test_tenant["id"], test_session["id"],
                      start_date="2026-12-24", end_date="2026-12-01")
        assert exc.value.code == "event_end_before_start"
        assert exc.value.params["start"] == "2026-12-24"
        assert exc.value.params["end"] == "2026-12-01"

    def test_a_single_day_event_is_legal(self, test_tenant, test_session):
        """start == end is one day long, not zero — it must not be refused."""
        result = _simulate(test_tenant["id"], test_session["id"],
                           start_date="2026-12-24", end_date="2026-12-24")
        assert result is not None

    @pytest.mark.parametrize("mult", [0.0, -1.0, -0.5])
    def test_a_non_positive_multiplier_is_refused(
        self, test_tenant, test_session, mult,
    ):
        with pytest.raises(AppError) as exc:
            _simulate(test_tenant["id"], test_session["id"], multiplier=mult)
        assert exc.value.code == "event_multiplier_not_positive"
        assert exc.value.params["multiplier"] == mult


class TestTheEndpointSurfacesTheCode:
    """AppError carries its code through the envelope; HTTPException did not."""

    def test_the_response_body_has_a_code_the_frontend_can_key_on(
        self, client, analyst_headers, test_session,
    ):
        resp = client.post(
            "/api/v1/inventory/events/simulate",
            json={"session_id": test_session["id"], "start_date": "ayer",
                  "end_date": "2026-12-24", "multiplier": 2.0},
            headers=analyst_headers,
        )
        # 403 here means the plan lacks EVENT_SIMULATOR — the guard runs before
        # the body is validated, and that is a different test's business.
        if resp.status_code == 403:
            pytest.skip("tenant plan has no EVENT_SIMULATOR feature")
        assert resp.status_code == 422, resp.text
        assert resp.json().get("error_code") == "event_date_invalid", resp.text
