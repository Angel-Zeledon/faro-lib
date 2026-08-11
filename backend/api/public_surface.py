"""The endpoints Faro promises to a customer's own system.

Everything in `backend/api/v1` is reachable with an API key. That is not the
same as being PUBLIC: 246 routes exist to serve this product's own screens, and
they change whenever a screen changes — several changed this week. An integration
built against one of those breaks without warning and without anyone deciding to
break it.

So the public API is a LIST, not a permission. These are the routes a customer's
ERP or POS is invited to call, chosen for the five jobs it actually has:

    push the export → retrain → read the semáforo → read what to buy → record
    the order

Being on this list is a promise: the path, the method and the shape of the
response do not change under an integration without a deprecation. Everything
absent is internal — still callable, never promised.

`backend/tests/test_public_api_surface.py` asserts every entry still resolves to
a real route, so this file cannot quietly describe an API that no longer exists.
The prose version customers read is `docs/api-publica.md`.
"""

# (method, path) exactly as FastAPI registers it, without the /api/v1 prefix.
PUBLIC_ENDPOINTS: tuple[tuple[str, str], ...] = (
    # ── Get the data in ───────────────────────────────────────────────────
    # The nightly export. Replaces the file IN PLACE, so the source keeps its
    # id and its column mapping and the next training run needs no wizard.
    ("POST", "/data-sources/{source_id}/file"),
    # Which sources exist, so a script can find the id it should replace
    # without a human reading it off a screen.
    ("GET", "/data-sources"),

    # ── Turn it into decisions ────────────────────────────────────────────
    ("POST", "/sessions/{session_id}/train"),
    ("GET", "/sessions/{session_id}/train/status"),

    # ── Read the decisions ────────────────────────────────────────────────
    # The semáforo: per SKU, what state it is in and how much to order.
    ("GET", "/inventory/status"),
    # The same thing ranked as a day's work, with the reasons attached.
    ("GET", "/inventory/morning-briefing"),

    # ── Close the loop ────────────────────────────────────────────────────
    # Records that an order was placed. Without this the order does not exist
    # for Faro: reception tracking and supplier lead-time learning both read it,
    # which is what makes the NEXT forecast better than this one.
    ("POST", "/inventory/log-po"),
)
