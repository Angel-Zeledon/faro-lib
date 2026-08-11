"""The public API list must describe routes that actually exist.

A published endpoint list is a promise, and a promise nobody checks rots the
first time somebody renames a path for a screen. That is not hypothetical here:
this API's routes are shaped for the product's own UI and several changed in the
same week the list was written.

These tests are the difference between "we documented an API" and "the API we
documented is the one running".
"""

import pytest

from backend.api.public_surface import PUBLIC_ENDPOINTS
from backend.main import app


def _registered() -> set[tuple[str, str]]:
    """Every (method, path) FastAPI actually serves, minus the /api/v1 prefix."""
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/v1"):
            continue
        trimmed = path[len("/api/v1"):]
        for m in methods:
            out.add((m.upper(), trimmed))
    return out


class TestThePromiseStillResolves:
    @pytest.mark.parametrize("method,path", PUBLIC_ENDPOINTS)
    def test_every_public_endpoint_is_a_real_route(self, method, path):
        assert (method, path) in _registered(), (
            f"{method} {path} is published as public API but no such route is "
            f"registered. Either it moved — in which case an integration is "
            f"already broken — or the list is describing something that never "
            f"shipped."
        )

    def test_the_check_can_actually_see_routes(self):
        """A registry that comes back empty would make the test above pass for
        an empty reason. This is the canary the audit needs."""
        assert len(_registered()) > 100


class TestTheListStaysHonest:
    def test_no_duplicates(self):
        assert len(set(PUBLIC_ENDPOINTS)) == len(PUBLIC_ENDPOINTS)

    def test_the_public_surface_stays_small(self):
        """Not style. The value of this list is that it is short enough to keep
        a promise about; the moment it drifts toward "everything", it stops
        meaning anything and we are back to 246 unversioned routes."""
        assert len(PUBLIC_ENDPOINTS) <= 12, (
            f"{len(PUBLIC_ENDPOINTS)} public endpoints. Adding one is a"
            f" commitment to keep it stable — decide that deliberately."
        )

    def test_nothing_admin_only_is_published(self):
        """An API key can only ever be viewer or analyst, so publishing an
        admin route would advertise something no key can call."""
        admin_paths = {"/tenant"}
        for _method, path in PUBLIC_ENDPOINTS:
            assert path not in admin_paths, f"{path} needs an admin, no key has one"
