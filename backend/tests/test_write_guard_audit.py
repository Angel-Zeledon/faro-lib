"""Every mutating route must be guarded, and the exceptions must be deliberate.

CLAUDE.md has required `require_analyst_or_above` on every mutating endpoint all
along. Ten of the twelve mutating routes in `data-sources` were nonetheless
guarded only by `get_current_user`, which asks nothing but that you are logged
in — a read-only account could point the tenant at any database it liked and
store the credentials, or delete a source outright. It was found by driving the
live API with a viewer token, not by a test, because the existing audit
(`test_permission_audit.py`) checks cross-tenant leakage per resource and never
reaches that router.

This test is the systematic version. It walks the app's own route table, so a
router added tomorrow is covered the moment it is mounted. A mutating route
either carries a write guard, or its path appears in PUBLIC/SELF/READ_ONLY below
with a reason. Both halves matter: an unguarded route fails, and an allowlisted
path that no longer exists also fails, so the list cannot rot into a rubber
stamp.
"""
import pytest

from backend.auth import guards as g

# ── The guards that actually authorise a write ────────────────────────────────

WRITE_GUARDS = {
    g.require_analyst_or_above,
    g.require_verified_analyst_or_above,
    g.require_verified_admin,
}
# `require_role(...)` returns a closure, so it cannot be compared by identity.
# Any dependency defined inside guards.py counts as a role check.
GUARD_MODULE = g.__name__

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


# ── Deliberate exceptions ─────────────────────────────────────────────────────
# Keyed by "METHOD /path" exactly as FastAPI reports it. The value is why.
PUBLIC = {
    "POST /api/v1/auth/login": "you cannot be authorised before you log in",
    "POST /api/v1/auth/signup": "creates the account and its tenant",
    "POST /api/v1/auth/refresh": "the refresh token IS the credential",
    "POST /api/v1/auth/logout": "revoking your own session needs no role",
    "POST /api/v1/auth/forgot-password": "you are locked out by definition",
    "POST /api/v1/auth/forgot-password/verify": "same flow, still locked out",
    "POST /api/v1/auth/reset-password": "the emailed token is the credential",
    "POST /api/v1/auth/verify-email": "the emailed token is the credential",
    "POST /api/v1/auth/resend-verification": "you cannot verify without it",
}

SELF = {
    # Acting on your own account or your own workspace. A viewer must be able to
    # change their own password, verify their own phone, and hold their own
    # conversations — none of that is company state.
    "PATCH /api/v1/users/me": "your own profile",
    "POST /api/v1/users/me/whatsapp/link": "your own phone number",
    "POST /api/v1/users/me/whatsapp/confirm": "your own phone number",
    "POST /api/v1/users/me/change-password/request": "your own password",
    "POST /api/v1/users/me/change-password/confirm": "your own password",
    "PATCH /api/v1/me/preferences": "your own preferences",
    "POST /api/v1/analyst/chats": "your own conversation with the analyst",
    "POST /api/v1/analyst/chats/{chat_id}/messages": "your own conversation",
    "PATCH /api/v1/analyst/chats/{chat_id}": "renaming your own conversation",
    "DELETE /api/v1/analyst/chats/{chat_id}": "deleting your own conversation",
    "POST /api/v1/messages": "sending a message is communication, not company state",
    "POST /api/v1/messages/read": "marking your own unread count",
}

INFRA = {
    # Not a user at all: Twilio posting an inbound WhatsApp message. Authorised
    # by request signature, so a role guard here would reject the only caller.
    "POST /api/v1/whatsapp/inbound": "Twilio webhook, verified by signature",
}

# POSTs that read. HTTP makes you POST anything with a body, so a query, an
# export or a connection probe is a POST without being a write.
READ_ONLY_POSTS = {
    "test-connection": "probes a stored connection, changes nothing",
    "execute-query": "runs a SELECT and returns rows",
    "export-query": "downloads what a SELECT returned",
    "simulate": "computes a scenario without persisting it",
    "preview": "renders what an action would do",
    "narrative": "asks the LLM to describe existing data",
    "narrate": "asks the LLM to describe existing data",
    "suggested-questions": "asks the LLM what you might want to ask",
    "analyst/query": "answers a question about existing results",
    "forecast-explanation": "asks the LLM to describe existing data",
    "explain": "describes existing data",
    "evaluate": "scores an option without saving it",
    "/drift": "compares stored results against newer data",
    "/predict": "runs inference over a trained session and returns it",
    "/reconcile": "recomputes a hierarchy total and returns it",
    "/run": "runs a saved scenario and returns the comparison",
    "cash-calendar/fit": "fits a payment pattern and returns it",
}


def _route_id(route) -> list[str]:
    return [f"{m} {route.path}" for m in sorted(route.methods or []) if m in MUTATING]


def _dependency_calls(dependant) -> list:
    """Every dependency function reachable from a route, at any depth."""
    out = []
    stack = list(getattr(dependant, "dependencies", []) or [])
    while stack:
        d = stack.pop()
        if getattr(d, "call", None) is not None:
            out.append(d.call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


# A dependency created by a factory is an inner function called `guard`, so the
# name alone tells you nothing: `require_role("admin")` and
# `require_feature(Feature.TEAM_MESSAGING)` both produce one. Only the first
# authorises a write — a plan gate says the tenant PAID for something, not that
# this user may change it. The qualname is what separates them.
ROLE_FACTORIES = ("require_role", "require_analyst", "require_admin", "require_verified")


def _is_guarded(route) -> bool:
    for call in _dependency_calls(route.dependant):
        if call in WRITE_GUARDS:
            return True
        qual = getattr(call, "__qualname__", "")
        if any(qual.startswith(f) for f in ROLE_FACTORIES):
            return True
    return False


def _mutating_routes(app):
    for route in app.routes:
        if not hasattr(route, "dependant") or not getattr(route, "methods", None):
            continue
        for rid in _route_id(route):
            yield rid, route


def _excused(rid: str) -> bool:
    if rid in PUBLIC or rid in SELF or rid in INFRA:
        return True
    if rid.startswith("POST "):
        tail = rid.split(" ", 1)[1]
        return any(seg in tail for seg in READ_ONLY_POSTS)
    return False


@pytest.fixture(scope="module")
def app():
    from backend.main import app as fastapi_app
    return fastapi_app


class TestEveryMutatingRouteIsGuarded:
    def test_no_mutating_route_relies_on_being_merely_logged_in(self, app):
        unguarded = [
            rid for rid, route in _mutating_routes(app)
            if not _excused(rid) and not _is_guarded(route)
        ]
        assert not unguarded, (
            "These routes change state but only require a logged-in user, so a "
            "viewer can call them. Add require_analyst_or_above, or add the path "
            "to PUBLIC/SELF/READ_ONLY_POSTS in this file with a reason:\n  "
            + "\n  ".join(sorted(unguarded))
        )

    def test_the_audit_actually_sees_routes(self, app):
        """A silent zero would make the assertion above meaningless."""
        found = list(_mutating_routes(app))
        assert len(found) > 60, (
            f"only {len(found)} mutating routes discovered — the walk is probably "
            "broken, which would make this whole audit pass vacuously")

    def test_the_data_sources_router_is_covered(self, app):
        """The router whose hole started this. Guards the guard."""
        ids = {rid for rid, _ in _mutating_routes(app) if "/data-sources" in rid}
        assert "POST /api/v1/data-sources/sql" in ids
        assert "DELETE /api/v1/data-sources/{source_id}" in ids


class TestTheAllowlistCannotRot:
    def test_every_excused_path_still_exists(self, app):
        live = {rid for rid, _ in _mutating_routes(app)}
        stale = [rid for rid in (set(PUBLIC) | set(SELF) | set(INFRA)) if rid not in live]
        assert not stale, (
            "These paths are excused from the write-guard audit but no longer "
            "exist. Delete them from this file so the exception list keeps "
            "meaning something:\n  " + "\n  ".join(sorted(stale)))
