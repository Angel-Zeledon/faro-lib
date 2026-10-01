"""The API-key surface is decided by rule, and the rule holds.

`backend/api/public_surface.py` replaced a hand list of eight endpoints with a
rule: every tenant-scoped action a person can take is callable with a key,
except the classes a credential must never touch. These tests pin the rule's
edges — the ones that would hurt if they moved — and the guard that makes a
NEW router's exposure a decision instead of an accident.
"""

import pytest

from backend.api.public_surface import (
    EXPOSED_TAGS, INTERNAL_ROUTES, INTERNAL_TAGS, excluded_routes, exposed_routes,
    exposure, public_endpoints,
)
from backend.main import app


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        for m in getattr(route, "methods", None) or set():
            out.add((m.upper(), path[len("/api/v1"):]))
    return out


def _route(method: str, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == f"/api/v1{path}" and method in (route.methods or ()):
            return route
    raise AssertionError(f"{method} {path} is not a registered route")


class TestEveryTagIsADecision:
    def test_every_router_tag_is_classified_exactly_once(self):
        """A new router whose tag is in neither set is refused to keys (fail
        closed) — and this goes red so somebody decides, instead of the new
        area silently being unreachable or silently being public."""
        tags = {t for r in app.routes for t in (getattr(r, "tags", None) or [])}
        unclassified = tags - EXPOSED_TAGS - set(INTERNAL_TAGS)
        assert not unclassified, (
            f"router tag(s) {sorted(unclassified)} are neither exposed to API keys "
            f"nor internal. Add each to EXPOSED_TAGS or INTERNAL_TAGS in "
            f"backend/api/public_surface.py."
        )
        assert not EXPOSED_TAGS & set(INTERNAL_TAGS)

    def test_an_unclassified_tag_is_refused(self):
        """The fail-closed branch, exercised: if it returned exposed, the test
        above would be the only thing between a new router and the public."""
        from fastapi import APIRouter, Depends
        from fastapi.routing import APIRoute
        from backend.auth.guards import get_current_user

        r = APIRouter(tags=["brand-new-area"])

        @r.get("/x")
        def _x(user=Depends(get_current_user)):
            return {}

        route = next(x for x in r.routes if isinstance(x, APIRoute))
        assert exposure(route).exposed is False

    def test_per_route_exceptions_name_real_routes(self):
        for method, path in INTERNAL_ROUTES:
            assert (method, path) in _registered(), f"{method} {path} no longer exists"


class TestTheExclusionsTheOwnerNamed:
    @pytest.mark.parametrize("method,path", [
        ("POST", "/auth/login"),
        ("POST", "/auth/refresh"),
        ("POST", "/users"),
        ("PATCH", "/users/{user_id}"),
        ("POST", "/users/me/change-password/request"),
        ("PUT", "/service-config/services/{service_key}"),
        ("GET", "/service-config/services"),
        ("DELETE", "/tenant"),
        ("GET", "/tenant/export"),
        ("POST", "/api-keys"),
        ("GET", "/api-keys"),
        ("DELETE", "/api-keys/{key_id}"),
        ("GET", "/api-keys/usage"),
    ])
    def test_never_callable_with_a_key(self, method, path):
        assert exposure(_route(method, path)).exposed is False

    def test_mcp_stays_exposed_and_read_only(self):
        assert exposure(_route("POST", "/mcp")).scope == "read"
        assert ("POST", "/mcp") in public_endpoints(app)
        assert ("GET", "/mcp") in public_endpoints(app)


class TestScopeIsReadOffTheGuards:
    @pytest.mark.parametrize("method,path,scope", [
        ("GET", "/inventory/status", "read"),
        ("GET", "/data-sources", "read"),
        ("GET", "/planning", "read"),
        ("POST", "/inventory/log-po", "write"),
        ("PUT", "/inventory/stock/{sku}", "write"),
        ("POST", "/data-sources/{source_id}/file", "write"),
        ("POST", "/sessions/{session_id}/train", "write"),
    ])
    def test_scope(self, method, path, scope):
        exp = exposure(_route(method, path))
        assert exp.exposed and exp.scope == scope

    def test_admin_only_routes_are_not_published(self):
        """No key can be admin, so an admin route would answer 403 to every key
        it was advertised to."""
        for method, path in [("PUT", "/planning"), ("PATCH", "/tenant/currency")]:
            exp = exposure(_route(method, path))
            assert exp.exposed is False and "admin" in exp.reason

    def test_the_old_eight_are_all_still_public(self):
        """The previous published list was a promise; widening the surface must
        not drop any of it."""
        old = [
            ("POST", "/data-sources/{source_id}/file"), ("GET", "/data-sources"),
            ("GET", "/planning"), ("POST", "/sessions/{session_id}/train"),
            ("GET", "/sessions/{session_id}/train/status"), ("GET", "/inventory/status"),
            ("GET", "/inventory/morning-briefing"), ("POST", "/inventory/log-po"),
        ]
        published = set(public_endpoints(app))
        assert set(old) <= published


class TestTheSurfaceIsReal:
    def test_every_exposed_endpoint_is_a_registered_route(self):
        reg = _registered()
        for method, path, _scope in exposed_routes(app):
            assert (method, path) in reg

    def test_the_surface_is_large_and_the_exclusions_are_not(self):
        """The owner asked for every action; guard against a rule bug that
        quietly exposes nothing (every assertion above could still pass)."""
        assert len(exposed_routes(app)) > 150
        assert 0 < len(excluded_routes(app)) < len(exposed_routes(app))


class TestEveryExposedOperationIsDocumented:
    def test_summary_description_scope_and_envelope(self):
        schema = app.openapi()
        missing = []
        for method, path, scope in exposed_routes(app):
            route = _route(method, path)
            op = schema["paths"][route.path_format][method.lower()]
            if not (op.get("summary") and op.get("description")):
                missing.append(f"{method} {path}")
            assert op["x-stockai-api"]["scope"] == scope
            assert "403" in op["responses"] and "429" in op["responses"]
        assert not missing, (
            f"{len(missing)} exposed operation(s) without a summary and description "
            f"— give the route a docstring or an entry in backend/api/public_docs.py: "
            f"{missing[:10]}"
        )

    def test_internal_operations_carry_no_api_marker(self):
        schema = app.openapi()
        op = schema["paths"]["/api/v1/auth/login"]["post"]
        assert "x-stockai-api" not in op
