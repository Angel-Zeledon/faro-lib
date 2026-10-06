"""The permission catalogue is shared by the Python and Rust APIs, and every
mutating route must have a declared permission.

No database needed: this guards the catalogue file itself, the two
implementations of the matcher, and route coverage (a new mutating route with
no rule turns this red, the same way a new router tag turns
test_public_api_surface.py red).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.auth import permissions as perms

ROOT = Path(__file__).resolve().parents[2]
PY_JSON = ROOT / "backend" / "auth" / "permissions.json"
RS_JSON = ROOT / "backend-rs" / "src" / "auth" / "permissions.json"
RS_ROUTES = ROOT / "backend-rs" / "src" / "routes"


class TestSharedCatalogue:
    def test_rust_copy_is_byte_identical(self):
        assert PY_JSON.read_bytes() == RS_JSON.read_bytes(), (
            "backend/auth/permissions.json and backend-rs/src/auth/permissions.json "
            "must be the same file: copy one over the other")

    def test_seventeen_permissions_and_the_rust_test_says_so_too(self):
        # backend-rs/src/auth/permissions.rs asserts the same count.
        assert len(perms.PERMISSIONS) == 17
        assert len(set(perms.PERMISSIONS)) == 17
        rs = (ROOT / "backend-rs" / "src" / "auth" / "permissions.rs").read_text(encoding="utf-8")
        assert "assert_eq!(c.permissions.len(), 17)" in rs

    def test_legacy_permissions_are_a_subset_and_unchanged(self):
        assert set(perms.LEGACY_PERMISSIONS) <= set(perms.PERMISSIONS)
        assert len(perms.LEGACY_PERMISSIONS) == 12

    def test_no_permission_is_dead(self):
        """A permission no rule names would be grantable and enforce nothing:
        the exact defect this feature exists to end."""
        used = {r["permission"] for r in perms._DATA["rules"]}
        dead = set(perms.PERMISSIONS) - used
        assert not dead, f"permissions no route needs: {sorted(dead)}"

    def test_every_rule_names_a_catalogue_permission_or_open(self):
        for r in perms._DATA["rules"]:
            assert r["permission"] == "open" or r["permission"] in perms.PERMISSION_SET, r

    def test_probes_resolve_in_python(self):
        """The Rust unit test `every_probe_resolves_like_python` runs the same table."""
        for method, path, expected in perms._DATA["probes"]:
            assert perms.requirement(method, path) == expected, (method, path)

    def test_a_prefix_matches_whole_segments_only(self):
        assert perms.requirement("POST", "/sessions-archive") == perms.NONE
        assert perms.requirement("POST", "/api/v1/sessions/{id}/train") == "run_training"
        assert perms.requirement("POST", "/sessions/{session_id}/train") == "run_training"


class TestEveryMutatingRouteIsDeclared:
    def test_every_python_mutating_route_has_a_rule(self):
        from fastapi.routing import APIRoute
        from backend.main import app

        undeclared = []
        for r in app.routes:
            if not isinstance(r, APIRoute):
                continue
            for method in r.methods:
                if method in ("HEAD", "OPTIONS", "GET"):
                    continue
                if perms.requirement(method, r.path) == perms.NONE:
                    undeclared.append(f"{method} {r.path}")
        assert not undeclared, (
            "mutating routes with no permission rule (add one to "
            f"backend/auth/permissions.json and copy it to backend-rs): {sorted(undeclared)}")

    def test_every_rust_mutating_route_has_a_rule(self):
        """Scan the Rust router sources: `.route("/api/v1/...", get(..).post(..))`."""
        found = []
        for path in RS_ROUTES.rglob("*.rs"):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"\.route\(\s*\"(/api/v1[^\"]*)\"", src):
                start = m.end()
                depth, i = 1, start
                while i < len(src) and depth:
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    i += 1
                body = src[start:i]
                for verb in re.findall(r"\b(post|put|patch|delete)\(", body):
                    found.append((verb.upper(), m.group(1), path.name))
        assert found, "the scanner found no Rust routes: it has rotted"
        undeclared = [f"{v} {p} ({f})" for v, p, f in found if perms.requirement(v, p) == perms.NONE]
        assert not undeclared, f"Rust mutating routes with no permission rule: {undeclared}"

    def test_the_scanner_would_notice_an_undeclared_route(self):
        assert perms.requirement("POST", "/api/v1/brand-new-router/things") == perms.NONE

    def test_every_machine_free_route_is_listed_open_on_purpose(self):
        """`open` writes are the escape hatch; keep the list short and known."""
        open_writes = sorted(r["prefix"] for r in perms._DATA["rules"]
                             if r["class"] == "write" and r["permission"] == "open")
        assert open_writes == sorted([
            "/auth", "/scim", "/billing/stripe", "/billing/paypal", "/inbound/email",
            "/whatsapp", "/supplier-portal", "/trial", "/feedback", "/me", "/users/me",
            "/alerts", "/messages", "/entitlements", "/mcp",
        ])


class TestRolesRoutesAreRustOnly:
    def test_python_does_not_serve_the_role_routes(self):
        """No Python failover: if somebody adds them, the owner decides
        which side owns them."""
        from fastapi.routing import APIRoute
        from backend.main import app

        paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
        assert not any(p.startswith("/api/v1/roles") or p.endswith("/custom-role") for p in paths)


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/inventory/stock", "view_inventory"),
    ("PUT", "/inventory/signal-thresholds", "manage_settings"),
    ("POST", "/users/{user_id}/custom-role", "manage_roles"),
])
def test_spot_checks(method, path, expected):
    assert perms.requirement(method, path) == expected
