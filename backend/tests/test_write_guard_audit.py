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
    "POST /api/v1/scim/v2/Users": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "PUT /api/v1/scim/v2/Users/{user_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "PATCH /api/v1/scim/v2/Users/{user_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "DELETE /api/v1/scim/v2/Users/{user_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "POST /api/v1/scim/v2/Groups": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "PUT /api/v1/scim/v2/Groups/{group_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "PATCH /api/v1/scim/v2/Groups/{group_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "DELETE /api/v1/scim/v2/Groups/{group_id}": "authenticated by the per-tenant SCIM bearer token, never a user JWT",
    "POST /api/v1/billing/stripe/webhook": "authenticated by the provider's signature (HMAC) over the raw body",
    "POST /api/v1/billing/paypal/webhook": "authenticated by PayPal's verify-webhook-signature call",
    "POST /api/v1/auth/login": "you cannot be authorised before you log in",
    "POST /api/v1/auth/signup": "creates the account and its tenant",
    "POST /api/v1/trial": "the landing visitor has no account yet; it creates one",
    "POST /api/v1/supplier-portal/{token}/confirm": "a supplier answers a purchase order from the link in the message, no account; the 256-bit link token is the credential",
    "POST /api/v1/auth/refresh": "the refresh token IS the credential",
    "POST /api/v1/auth/logout": "revoking your own session needs no role",
    "POST /api/v1/auth/forgot-password": "you are locked out by definition",
    "POST /api/v1/auth/forgot-password/verify": "same flow, still locked out",
    "POST /api/v1/auth/reset-password": "the emailed token is the credential",
    "POST /api/v1/auth/verify-email": "the emailed token is the credential",
    "POST /api/v1/auth/resend-verification": "you cannot verify without it",
    "POST /api/v1/auth/oauth/{provider}/callback": "Apple's form_post; the single-use state is the credential",
    "POST /api/v1/auth/oauth/exchange": "the one-time handoff code is the credential",
    "POST /api/v1/auth/sso/discover": "a lookup typed as POST so the address is not in a URL; writes nothing",
    "POST /api/v1/auth/saml/acs": "the identity provider's form_post; the single-use RelayState, the browser cookie and the XML signature are the credential",
}

SELF = {
    # Acting on your own account or your own workspace. A viewer must be able to
    # change their own password, verify their own phone, and hold their own
    # conversations — none of that is company state.
    "PATCH /api/v1/users/me": "your own profile",
    "PATCH /api/v1/analyst/messages/{message_id}/star": "starring your own chat message",
    "POST /api/v1/feedback": "reporting a problem about the app, not company state",
    "POST /api/v1/users/me/whatsapp/link": "your own phone number",
    "POST /api/v1/users/me/whatsapp/confirm": "your own phone number",
    "POST /api/v1/users/me/change-password/request": "your own password",
    "POST /api/v1/users/me/change-password/confirm": "your own password",
    "DELETE /api/v1/auth/identities/{provider}": "unlinking your own sign-in provider",
    "PATCH /api/v1/me/preferences": "your own preferences",
    "POST /api/v1/analyst/chats": "your own conversation with the analyst",
    "POST /api/v1/analyst/chats/{chat_id}/messages": "your own conversation",
    "PATCH /api/v1/analyst/chats/{chat_id}": "renaming your own conversation",
    "DELETE /api/v1/analyst/chats/{chat_id}": "deleting your own conversation",
    "POST /api/v1/messages": "sending a message is communication, not company state",
    "POST /api/v1/messages/read": "marking your own unread count",
    # Same category, and for a concrete reason: the bell now carries tenant-wide
    # SYSTEM events (a failed training is addressed to nobody in particular), so
    # a viewer collects a badge. Requiring analyst-or-above to clear it would
    # leave the one role that cannot clear it staring at it forever.
    "POST /api/v1/alerts/read": "marking your own unread count",
}

INFRA = {
    # Not a user at all: Twilio posting an inbound WhatsApp message. Authorised
    # by request signature, so a role guard here would reject the only caller.
    "POST /api/v1/whatsapp/inbound": "Twilio webhook, verified by signature",
    # The mail provider posting an inbound message. No user session exists; it
    # is refused (401) before anything is parsed unless it carries the
    # installation's webhook secret (HMAC with a replay window, or Basic), and it
    # is off (503) when no secret is configured. Past that, the secret per-tenant
    # address picks the tenant and only verified analysts/admins or listed
    # senders get a file ingested (backend/inbound_email/ingest.py).
    "POST /api/v1/inbound/email": "mail-provider webhook, verified by shared secret + per-tenant address + sender allow-list",
}

# POSTs that read. HTTP makes you POST anything with a body, so a query, an
# export or a connection probe is a POST without being a write.
READ_ONLY_POSTS = {
    # NOT excused: execute-query, export-query and test-connection. The first two
    # run caller-written SQL against the CUSTOMER's database (see
    # test_datasource_sql_guard.py); the probe stores `datasets.connection_status`,
    # so it is a write too. All three carry require_analyst_or_above.
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
    "budget/plan": "splits the current recommendations into funded / deferred under a budget and returns it; creates no order",
    "budget/check": "previews whether an order would exceed a budget; writes nothing",
    # MCP is JSON-RPC: the method lives in the BODY, so `tools/list` and every
    # read tool arrive as a POST. The excuse holds only because the catalogue
    # (`backend/mcp/catalog.py`) is closed and every entry in it reads — which
    # `test_mcp_server.py::test_every_tool_actually_only_calls_GET_endpoints`
    # enforces structurally, by resolving each handler's calls to their FastAPI
    # routes and demanding {GET}. Add a write tool there and this line becomes
    # a lie; that test goes red first, which is the point.
    "/mcp": "JSON-RPC over a closed catalogue of reads; see backend/mcp/catalog.py",
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
# name alone tells you nothing about what it authorises. The qualname is what
# separates a role check from any other factory-made dependency.
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

    def test_customer_database_sql_routes_need_a_write_guard(self, app):
        """Running caller-written SQL on the customer's database is not a
        viewer's (or a read-scope key's) to do, even though it only reads."""
        routes = {rid: route for rid, route in _mutating_routes(app)}
        for rid in ("POST /api/v1/data-sources/{source_id}/execute-query",
                    "POST /api/v1/data-sources/{source_id}/export-query"):
            assert rid in routes, f"{rid} disappeared — update this test"
            assert _is_guarded(routes[rid]), f"{rid} is reachable by a viewer"

    def test_the_connection_probe_is_a_guarded_write(self, app):
        """It used to be excused as a read, but it writes connection_status -
        which gates execute-query and materialize for the whole tenant."""
        rid = "POST /api/v1/data-sources/{source_id}/test-connection"
        routes = {r: route for r, route in _mutating_routes(app)}
        assert rid in routes
        assert not _excused(rid), "the probe must not hide behind an allow-list entry"
        assert _is_guarded(routes[rid])

    def test_the_inbound_mail_webhook_refuses_without_the_secret(self):
        """The INFRA excuse holds only while the webhook checks its secret; the
        two verifiers it relies on must reject a wrong or missing one."""
        import base64
        import hashlib
        import hmac
        import time
        from backend.api.v1 import inbound_email as ie

        body = b'{"x": 1}'
        now = int(time.time())
        good = hmac.new(b"s3cret", f"{now}.".encode() + body, hashlib.sha256).hexdigest()
        assert ie.verify_signature("s3cret", f"t={now},v1={good}", body, now=now)
        assert not ie.verify_signature("other", f"t={now},v1={good}", body, now=now)
        assert not ie.verify_signature("s3cret", f"t={now},v1={good}", body + b" ", now=now)
        assert not ie.verify_signature(
            "s3cret", f"t={now},v1={good}", body, now=now + ie.REPLAY_WINDOW_SECONDS + 1)
        assert not ie.verify_signature("s3cret", "garbage", body, now=now)
        basic = "Basic " + base64.b64encode(b"x:s3cret").decode()
        assert ie.verify_basic("s3cret", basic)
        assert not ie.verify_basic("other", basic)
        assert not ie.verify_basic("s3cret", "Bearer s3cret")


class TestTheAllowlistCannotRot:
    def test_every_excused_path_still_exists(self, app):
        live = {rid for rid, _ in _mutating_routes(app)}
        stale = [rid for rid in (set(PUBLIC) | set(SELF) | set(INFRA)) if rid not in live]
        assert not stale, (
            "These paths are excused from the write-guard audit but no longer "
            "exist. Delete them from this file so the exception list keeps "
            "meaning something:\n  " + "\n  ".join(sorted(stale)))
