"""The audit trail: who did what to which object, when, and what changed.

`note()` is the one function handlers call to say more than the middleware can
know on its own (the previous value, or the id of the object they just made).
"""

from __future__ import annotations

from typing import Any, Optional

_NOTE_KEY = "stockai_audit_note"


def note(
    request: Any, *, target_id: Optional[str] = None, label: Optional[str] = None,
    before: Optional[dict] = None, after: Optional[dict] = None,
) -> None:
    """Attach what the handler knows to the audit row the middleware is about
    to write for this request. Safe to call from any handler: it only writes to
    the request's ASGI scope, which the middleware reads on the way out.

    Put SUMMARIES in `before`/`after` (a name, a role, a cron expression),
    never a secret and never a whole document.
    """
    existing = request.scope.get(_NOTE_KEY) or {}
    for key, value in (("target_id", target_id), ("label", label),
                       ("before", before), ("after", after)):
        if value is not None:
            existing[key] = value
    request.scope[_NOTE_KEY] = existing


def read_note(scope: dict) -> dict:
    return scope.get(_NOTE_KEY) or {}
