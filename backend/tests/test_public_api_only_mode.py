"""PUBLIC_API_ONLY: the promise as a wall instead of a list.

`public_surface.py` says which seven endpoints a customer's system may call. In
the normal deployment that is a convention — all 246 routes are mounted and only
documentation keeps an integrator away from the internal ones.

This mode is for running the public API on its own infrastructure: same image,
`PUBLIC_API_ONLY=true`, worker and scheduler off. Then an integration cannot
reach an internal route on that host even by guessing.

The tests build a second app under the flag rather than trusting the running
one, because the pruning happens at import time and the flag is off here.
"""

import importlib

import pytest

from backend.api.public_surface import PUBLIC_ENDPOINTS


def _build_app(public_only: bool):
    """A fresh app object with the flag applied.

    `backend.main` prunes at module scope, so the setting has to be in place
    before the reload — patching afterwards would test nothing.
    """
    from backend.config import settings

    original = settings.public_api_only
    settings.public_api_only = public_only
    try:
        import backend.main as main_mod
        importlib.reload(main_mod)
        return main_mod.app
    finally:
        settings.public_api_only = original


@pytest.fixture(scope="module")
def public_app():
    app = _build_app(True)
    yield app
    # Leave the module as the rest of the session expects to find it.
    _build_app(False)


def _paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


class TestOnlyThePromisedSurfaceIsServed:
    def test_every_public_endpoint_survives(self, public_app):
        served = _paths(public_app)
        for _method, path in PUBLIC_ENDPOINTS:
            assert f"/api/v1{path}" in served, (
                f"{path} is published but PUBLIC_API_ONLY dropped it — the mode "
                f"would break exactly the integrations it exists to serve"
            )

    def test_health_survives(self, public_app):
        """A load balancer has to be able to ask. Removing it would make the
        instance look dead to whatever is in front of it."""
        assert "/health" in _paths(public_app)

    @pytest.mark.parametrize("internal", [
        "/api/v1/auth/login",       # a machine has a key; it never logs in
        "/api/v1/users",            # tenant administration
        "/api/v1/tenant",           # the erasure endpoint
        "/api/v1/api-keys",         # keys are minted from the app, not the API
        "/api/v1/messages",         # a product screen
    ])
    def test_internal_routes_are_gone(self, public_app, internal):
        assert internal not in _paths(public_app), (
            f"{internal} is still mounted under PUBLIC_API_ONLY; the wall leaks"
        )

    def test_the_surface_really_shrank(self, public_app):
        """Guards against a pruning bug that silently keeps everything — the
        failure mode where every assertion above still passes."""
        full = _build_app(False)
        assert len(_paths(public_app)) < len(_paths(full)) / 5, (
            "PUBLIC_API_ONLY barely removed anything; it is not doing its job"
        )


class TestTheDefaultIsUntouched:
    def test_without_the_flag_the_whole_product_is_served(self):
        full = _build_app(False)
        served = _paths(full)
        assert "/api/v1/auth/login" in served
        assert "/api/v1/users" in served
        assert len(served) > 100, (
            "the default deployment lost routes — this flag must be opt-in only"
        )
