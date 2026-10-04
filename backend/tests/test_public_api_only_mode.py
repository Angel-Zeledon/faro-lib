"""PUBLIC_API_ONLY: the API-key surface as a wall instead of a rule.

`public_surface.py` decides which routes an API key may call. In the normal
deployment every route is mounted and the key is refused on the rest at
request time. This mode is for running the public API on its own
infrastructure: same image, `PUBLIC_API_ONLY=true`, worker and scheduler off.
Then a route a key may not call is not even mounted on that host.

The tests build a second app under the flag rather than trusting the running
one, because the pruning happens at import time and the flag is off here.
"""

import importlib

import pytest

from backend.api.public_surface import public_endpoints


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
def apps():
    full = _build_app(False)
    published = public_endpoints(full)
    public = _build_app(True)
    yield full, public, published
    # Leave the module as the rest of the session expects to find it.
    _build_app(False)


def _pairs(app) -> set[tuple[str, str]]:
    out = set()
    for r in app.routes:
        for m in getattr(r, "methods", None) or ():
            out.add((m.upper(), getattr(r, "path", "")))
    return out


class TestOnlyThePromisedSurfaceIsServed:
    def test_every_public_endpoint_survives(self, apps):
        _full, public, published = apps
        served = _pairs(public)
        for method, path in published:
            assert (method, f"/api/v1{path}") in served, (
                f"{method} {path} is callable with a key but PUBLIC_API_ONLY dropped "
                f"it — the mode would break exactly the integrations it exists to serve"
            )

    def test_nothing_else_is_served(self, apps):
        _full, public, published = apps
        allowed = {(m, f"/api/v1{p}") for m, p in published}
        extra = {
            (m, p) for m, p in _pairs(public)
            if p.startswith("/api/v1") and (m, p) not in allowed
        }
        assert not extra, f"served under PUBLIC_API_ONLY but not key-callable: {sorted(extra)[:10]}"

    def test_health_survives(self, apps):
        """A load balancer has to be able to ask."""
        _full, public, _ = apps
        assert "/health" in {getattr(r, "path", "") for r in public.routes}

    @pytest.mark.parametrize("internal", [
        "/api/v1/auth/login",       # a machine has a key; it never logs in
        "/api/v1/users",            # tenant administration
        "/api/v1/tenant",           # the erasure endpoint
        "/api/v1/api-keys",         # keys are minted from the app, not the API
        "/api/v1/messages",         # a person's inbox
        "/api/v1/service-config/services",  # instance configuration
    ])
    def test_internal_routes_are_gone(self, apps, internal):
        _full, public, _ = apps
        assert internal not in {getattr(r, "path", "") for r in public.routes}, (
            f"{internal} is still mounted under PUBLIC_API_ONLY; the wall leaks"
        )

    def test_the_surface_really_shrank(self, apps):
        """Guards against a pruning bug that silently keeps everything."""
        full, public, _ = apps
        assert len(public.routes) < len(full.routes)


class TestTheDefaultIsUntouched:
    def test_without_the_flag_the_whole_product_is_served(self, apps):
        full, _public, _ = apps
        served = {getattr(r, "path", "") for r in full.routes}
        assert "/api/v1/auth/login" in served
        assert "/api/v1/users" in served
        assert len(served) > 100
