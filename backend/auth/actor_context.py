"""Who is acting on this request, for code that runs outside the dependency tree.

FastAPI guards build a `CurrentUser` and hand it to the endpoint. Middleware
wraps all of that and never sees it — which is why the machine-audit middleware
could not tell an integration's call from a person's without resolving the
credential a second time.

The answer is carried in the ASGI **scope**, which is one dict shared by the
whole request: middleware, dependencies and endpoint all hold the same object.

It was a ContextVar first, and that silently did not work. `BaseHTTPMiddleware`
runs the downstream application in a separate anyio task, so a ContextVar set
inside a dependency is set in a child context and is gone by the time the
response comes back out. Nothing raised; the audit table simply stayed empty,
which is exactly the kind of quiet nothing this trail exists to catch. Walking
it against a live server is what found it — no unit test of mine would have.
"""

from typing import Optional

_SCOPE_KEY = "stockai_machine_actor"


def set_machine_actor(scope: dict, tenant_id: str, actor_id: str) -> None:
    """Record that an API KEY is acting. Never called for people: their actions
    are attributable through their session, and auditing every UI click would
    drown the trail this exists to create."""
    scope[_SCOPE_KEY] = (tenant_id, actor_id)


def get_machine_actor(scope: dict) -> Optional[tuple[str, str]]:
    return scope.get(_SCOPE_KEY)


_PERSON_KEY = "stockai_audit_person"


def set_person_actor(scope: dict, tenant_id: str, user_id: str) -> None:
    """Record which PERSON is acting, for the audit trail's middleware. Kept
    apart from the machine actor on purpose: the machine audit exists to catch
    integrations and must not start logging every UI click."""
    scope[_PERSON_KEY] = (tenant_id, user_id)


def get_person_actor(scope: dict) -> Optional[tuple[str, str]]:
    return scope.get(_PERSON_KEY)
