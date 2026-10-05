"""Chaos: the HTTP surface, driven by someone who wants it to break.

The suite that exists sends well-formed requests and checks the answer. This
file sends what a real client sends when something has gone wrong upstream — a
NUL byte from a C string, a 100 KB path segment from a runaway loop, a SKU that
is `'; DROP TABLE tenants;--` because that is genuinely what the product was
called, an ERP that stringifies `Infinity` into JSON.

Two rules, and they are the whole file:

1. **Never an unclassifiable 500.** Every refusal must arrive as the API's own
   envelope — JSON, with `detail`, and `error_code` when the backend raised it
   deliberately. A 500 with an empty body is indistinguishable from the proxy
   being down, which is the single most expensive minute in this codebase's
   debugging history (CLAUDE.md documents exactly that trap).
2. **Never a silent mutation.** A request that is refused must leave the
   database exactly as it was, and a request that is accepted must store what
   was sent — byte for byte, accents, emoji and all.

Nothing here is marked `stress`: all of it is fast, and all of it should run on
every commit.
"""

import json

import pytest
from starlette.testclient import TestClient

from backend.db.connection import query, query_one


@pytest.fixture(scope="module")
def wire_client(app):
    """A client that reports what a REAL caller would receive.

    The shared `client` fixture runs with `raise_server_exceptions=True`, which
    re-raises an unhandled exception into the test instead of letting
    `backend/main.py`'s handler turn it into a 500 envelope. That is the right
    default for most tests — a traceback beats a status code when you are
    debugging. It is the wrong one here: the whole question this file asks is
    *what the user sees*, and the user never sees the traceback.
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Helpers ──────────────────────────────────────────────────────────────────

def _assert_survivable(resp, what: str):
    """The response is something the frontend can act on.

    Anything is allowed EXCEPT the two outcomes that leave a user stranded: a
    non-JSON body, and a 500 with no code to look up. FastAPI's own 404s and
    422s pass; so does a deliberate 500 carrying `internal_error`.
    """
    assert resp.status_code != 0, f"{what}: no response at all"
    try:
        body = resp.json()
    except Exception:                              # noqa: BLE001
        pytest.fail(
            f"{what}: answered {resp.status_code} with a non-JSON body "
            f"{resp.content[:200]!r} — indistinguishable from the proxy being down"
        )
    assert "detail" in body or "data" in body, (
        f"{what}: {resp.status_code} with neither `detail` nor `data`: {body}"
    )
    if resp.status_code >= 500:
        assert body.get("error_code"), (
            f"{what}: a 5xx with no error_code — nothing upstream can name this "
            f"failure to the user"
        )
    return body


def _stock(**over):
    return {"current_stock": 1, "min_stock": 0, "lead_time_days": 5, **over}


def _stock_rows(tenant_id: str):
    return query("SELECT sku FROM inventory_stock WHERE tenant_id = %s", (tenant_id,))


# ── 1. Bytes no URL was meant to carry ───────────────────────────────────────

# Path segments that have each, at some point, turned a route into a 500 in some
# codebase. The NUL one is not hypothetical here: psycopg2 rejects NUL in a bind
# parameter, so `/sessions/%00x/results` used to reach the unhandled handler.
HOSTILE_SEGMENTS = [
    "%00",                       # NUL — psycopg2 refuses it as a bind param
    "%00x",
    "..%2f..%2f..%2fetc%2fpasswd",
    "%2e%2e%5c%2e%2e%5cwindows",
    "%FF%FE",                    # a UTF-16 BOM where a string was expected
    "%C0%80",                    # overlong UTF-8 encoding of NUL
    "a" * 4096,                  # a path segment longer than most servers allow
    "%20" * 500,                 # nothing but whitespace
    "🧃",                        # a real SKU, in a real catalogue
    "‮evil",                     # RTL override: renders backwards in a terminal
    "%27%20OR%201%3D1%20--",     # ' OR 1=1 --
    "%3Cscript%3Ealert(1)%3C%2fscript%3E",
    "-1",
    "0",
    "null",
    "undefined",
    "%7B%22%24ne%22%3Anull%7D",  # {"$ne":null} — mongo-shaped, still a string here
]

# One route per shape of handler: a lookup by id, a lookup by natural key, a
# nested collection, and a delete. Enough surface that a handler class that
# forgot to defend itself shows up.
ROUTE_TEMPLATES = [
    ("GET", "/api/v1/inventory/stock/{seg}"),
    ("GET", "/api/v1/sessions/{seg}"),
    ("GET", "/api/v1/sessions/{seg}/results"),
    ("GET", "/api/v1/datasets/{seg}"),
    ("GET", "/api/v1/inventory/suppliers/{seg}"),
]


@pytest.mark.parametrize("segment", HOSTILE_SEGMENTS)
def test_a_hostile_path_segment_is_answered_not_swallowed(
    segment, wire_client, auth_headers,
):
    """Every one of these must come back as the API's envelope.

    Not "must 404" — some of these are legitimately 422, and a 500 is even
    acceptable as long as it names itself. What is never acceptable is
    `text/plain` "Internal Server Error", because that is the exact symptom the
    team is trained to read as "the backend is not running".
    """
    for method, template in ROUTE_TEMPLATES:
        url = template.format(seg=segment)
        resp = wire_client.request(method, url, headers=auth_headers)
        _assert_survivable(resp, f"{method} {url}")


def test_a_nul_byte_in_a_path_is_the_callers_mistake_not_the_servers(
    wire_client, auth_headers,
):
    """A NUL in a path segment is a malformed REQUEST. It must be answered as
    one.

    Today it is not: the byte travels intact into `query()`, psycopg2 refuses it
    (`A string literal cannot contain NUL`), and the unhandled-error handler
    turns that into a 500 `internal_error`. The envelope is there — the earlier
    fix did its job — but the verdict is wrong in the way that costs money: the
    user is told the server broke on a URL they malformed, and it pages whoever
    is on call. A 400/404 is the honest answer, and the guard belongs where the
    id is read, not at the driver.
    """
    resp = wire_client.get("/api/v1/sessions/%00x/results", headers=auth_headers)
    body = _assert_survivable(resp, "NUL in a session id")
    assert resp.status_code < 500, (
        f"a malformed id was reported as a server fault ({resp.status_code}, "
        f"{body.get('error_code')})"
    )


# ── 2. Bodies that are valid JSON and nothing else ───────────────────────────

def test_non_finite_numbers_are_refused_as_input_not_as_a_server_fault(
    client, analyst_headers,
):
    """`json.dumps` emits bare `NaN` and `Infinity` by default, so any Python
    client sends them without trying. Starlette's JSONResponse then refuses to
    serialize them BACK in the 422 echo — which turned an invalid request into
    an unhandled 500 blaming the server."""
    for literal in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
        raw = '{"current_stock": %s, "min_stock": 0, "lead_time_days": 5}' % literal
        resp = client.put(
            "/api/v1/inventory/stock/SKU-NONFINITE",
            content=raw,
            headers={**analyst_headers, "Content-Type": "application/json"},
        )
        body = _assert_survivable(resp, f"current_stock={literal}")
        assert resp.status_code < 500, (
            f"{literal} in a request body was reported as a SERVER error: {body}"
        )


def test_numbers_far_outside_the_field_bounds_are_field_errors(client, analyst_headers):
    """The bounds exist; what matters is that breaking them is a 422 naming the
    field, not a 500 and not a silently clamped value."""
    for value in (-1, 10 ** 20, -(10 ** 20), 2 ** 63, 1.7976931348623157e308):
        resp = client.put(
            "/api/v1/inventory/stock/SKU-BOUNDS",
            json=_stock(current_stock=value),
            headers=analyst_headers,
        )
        _assert_survivable(resp, f"current_stock={value}")
        assert resp.status_code == 422, (
            f"current_stock={value} was accepted or crashed ({resp.status_code})"
        )
    assert query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE sku = %s", ("SKU-BOUNDS",)
    )["c"] == 0, "a rejected value still created the row"


def test_a_deeply_nested_body_does_not_blow_the_stack(client, analyst_headers):
    """5.000 levels of nesting. Python's recursion limit is 1.000, so a parser
    that descends recursively raises RecursionError — which is not a
    ValidationError and lands in the unhandled handler."""
    payload = "[" * 5000 + "1" + "]" * 5000
    resp = client.put(
        "/api/v1/inventory/stock/SKU-DEEP",
        content='{"current_stock": %s}' % payload,
        headers={**analyst_headers, "Content-Type": "application/json"},
    )
    _assert_survivable(resp, "5.000-level nested body")
    assert resp.status_code < 500 or resp.json().get("error_code") == "internal_error"


def test_a_body_that_is_not_the_shape_the_route_expects(client, analyst_headers):
    """Six wrong shapes, all valid JSON. Each must be a 422, never a 500 and
    never a partial write."""
    for payload in ('[]', '"a string"', '42', 'null', 'true', '{"": {"": {}}}'):
        resp = client.put(
            "/api/v1/inventory/stock/SKU-SHAPE",
            content=payload,
            headers={**analyst_headers, "Content-Type": "application/json"},
        )
        _assert_survivable(resp, f"body={payload}")
        assert resp.status_code == 422, f"body {payload} answered {resp.status_code}"


def test_a_lying_content_type_is_refused_cleanly(client, analyst_headers):
    """A client that sends JSON as text/plain, or form data as JSON. Both are
    somebody's misconfigured integration on a Tuesday morning."""
    resp = client.put(
        "/api/v1/inventory/stock/SKU-CT",
        content=json.dumps(_stock()),
        headers={**analyst_headers, "Content-Type": "text/plain"},
    )
    _assert_survivable(resp, "JSON sent as text/plain")
    assert resp.status_code < 500

    resp = client.put(
        "/api/v1/inventory/stock/SKU-CT2",
        content="current_stock=1&min_stock=0",
        headers={**analyst_headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    _assert_survivable(resp, "form data on a JSON route")
    assert resp.status_code < 500


# ── 3. Strings that must survive a round trip unchanged ──────────────────────

ROUND_TRIP_SKUS = [
    "SKU-ÑÁÉÍÓÚ",                 # the alphabet the customers actually use
    "🧃-JUGO-1L",                 # emoji in a product code: real, and increasing
    "SKU con espacios",
    "SKU'CON'COMILLAS",
    'SKU"DOBLES"',
    "SKU\\BACKSLASH",
    "SKU%PORCENTAJE",
    "SKU&AMP;",
    "SKU<script>",
    "'; DROP TABLE tenants;--",
    '" OR 1=1 --',
    "../../../../etc/passwd",
    "C:\\Windows\\system32",
    "SKU" + "0" * 200,            # long, but legal
    "ＳＫＵ－ＦＵＬＬＷＩＤＴＨ",    # full-width homoglyphs
]


@pytest.mark.parametrize("sku", ROUND_TRIP_SKUS)
def test_a_hostile_sku_is_stored_verbatim_and_read_back_identical(
    sku, client, analyst_headers, analyst_user,
):
    """Data, not code. Every one of these is a legal product code somewhere, and
    a product code that comes back changed is worse than one that is refused:
    the buyer orders against a SKU their supplier has never heard of.

    The two SQL-shaped ones double as the injection test — the assertion that
    `tenants` still exists afterwards is the point.
    """
    tenant_id = analyst_user["tenant"]["id"]
    resp = client.put(
        f"/api/v1/inventory/stock/{sku}",
        json=_stock(display_name=sku),
        headers=analyst_headers,
    )
    _assert_survivable(resp, f"PUT sku={sku!r}")
    if resp.status_code >= 400:
        # Refusing is allowed; refusing while half-writing is not.
        assert not [r for r in _stock_rows(tenant_id) if r["sku"] == sku], (
            f"{sku!r} was refused ({resp.status_code}) but the row exists"
        )
        return

    stored = query_one(
        "SELECT sku, display_name FROM inventory_stock "
        "WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
    assert stored is not None, f"{sku!r} answered {resp.status_code} but stored nothing"
    assert stored["sku"] == sku, f"stored as {stored['sku']!r}, sent {sku!r}"
    assert stored["display_name"] == sku

    # The injection assertion, and it is not decoration: if any of these ever
    # concatenates into SQL, this is the line that notices.
    assert query_one("SELECT to_regclass('public.tenants') AS t")["t"] == "tenants", (
        "the tenants table is gone — a value reached the parser as SQL"
    )


def test_an_empty_sku_cannot_create_a_nameless_row(client, analyst_headers, analyst_user):
    """An empty path segment collapses the route; an all-whitespace one does
    not, and is the one that quietly creates a row nobody can ever find in the
    UI because it renders as nothing."""
    tenant_id = analyst_user["tenant"]["id"]
    # Percent-encoded forms only for the control characters: httpx refuses to
    # put a raw TAB in a URL at all, so a literal "\t" here would be testing the
    # HTTP client rather than the API. %09 is what a real caller can send.
    for sku in ("   ", "%20", "%09", "%20%20%20", "%0b"):
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}", json=_stock(), headers=analyst_headers,
        )
        _assert_survivable(resp, f"whitespace sku {sku!r}")
        if resp.status_code < 400:
            rows = [r["sku"] for r in _stock_rows(tenant_id)]
            assert not any(s.strip() == "" for s in rows), (
                f"a blank SKU {sku!r} was accepted and is now an invisible row"
            )


def test_header_injection_through_a_stored_value(client, analyst_headers):
    """CR/LF in a value that later appears in a response header or an email
    subject. Nothing should reflect it, and nothing should split a header."""
    evil = "SKU\r\nX-Injected: yes"
    resp = client.put(
        "/api/v1/inventory/stock/SKU-CRLF",
        json=_stock(display_name=evil),
        headers=analyst_headers,
    )
    _assert_survivable(resp, "CRLF in display_name")
    assert "X-Injected" not in resp.headers
    assert not any("Injected" in k for k in resp.headers)


# ── 4. Isolation, which volume and malice both attack ────────────────────────

def test_one_tenant_cannot_read_another_tenants_row_by_guessing_its_id(
    client, make_tenant_user_headers,
):
    """The most valuable thing an attacker can do with a valid account is use
    it against somebody else's data. Two real tenants, one real id, and the
    answer must be 404 — never 200, and never 403 either, which would confirm
    the id exists."""
    a_headers, a_tenant = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    b_headers, b_tenant = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    made = client.put("/api/v1/inventory/stock/SECRET-SKU",
                      json=_stock(display_name="tenant A only"), headers=a_headers)
    assert made.status_code < 400, made.text

    seen = client.get("/api/v1/inventory/stock/SECRET-SKU", headers=b_headers)
    assert seen.status_code == 404, (
        f"tenant B read tenant A's SKU: {seen.status_code} {seen.text[:200]}"
    )

    listed = client.get("/api/v1/inventory/stock", headers=b_headers)
    if listed.status_code == 200:
        payload = listed.json().get("data") or []
        assert not any(r.get("sku") == "SECRET-SKU" for r in payload), (
            "tenant A's SKU appears in tenant B's list"
        )


def test_a_viewer_cannot_write_through_any_of_these_shapes(
    client, viewer_headers, viewer_user,
):
    """The permission pair, under hostile input. A guard that is bypassed by a
    weird payload is not a guard — and the 403 must land BEFORE the body is
    parsed, so a viewer never gets a 422 that tells them the shape was right."""
    tenant_id = viewer_user["tenant"]["id"]
    before = len(_stock_rows(tenant_id))
    for payload in (_stock(), {}, [], "null", '{"current_stock": 1e400}'):
        kwargs = ({"json": payload} if isinstance(payload, (dict, list))
                  else {"content": payload,
                        "headers_extra": {"Content-Type": "application/json"}})
        headers = {**viewer_headers, **kwargs.pop("headers_extra", {})}
        resp = client.put("/api/v1/inventory/stock/VIEWER-SKU",
                          headers=headers, **kwargs)
        _assert_survivable(resp, f"viewer PUT {payload!r}")
        assert resp.status_code == 403, (
            f"a viewer got {resp.status_code} for {payload!r} — not a role refusal"
        )
    assert len(_stock_rows(tenant_id)) == before, "a viewer changed the database"


# ── 5. Uploads: the filename is attacker-controlled ──────────────────────────

TRAVERSAL_NAMES = [
    "../../../../etc/passwd.csv",
    "..\\..\\..\\windows\\system32\\evil.csv",
    "....//....//evil.csv",
    "/absolute/path/evil.csv",
    "C:\\Windows\\Temp\\evil.csv",
    "con.csv",                    # a reserved device name on Windows
    "a" * 300 + ".csv",           # longer than NAME_MAX on most filesystems
    ".csv",
    "🧃.csv",
    "evil.csv\x00.txt",           # NUL truncation, the classic
]


@pytest.mark.parametrize("filename", TRAVERSAL_NAMES)
def test_an_uploaded_file_can_never_land_outside_its_tenants_directory(
    filename, client, auth_headers, registered_user,
):
    """`upload_dataset` builds its own path from ids, but the filename still
    reaches `Path(...).stem` and the database. The invariant that matters is
    physical: whatever is written must be under storage/<tenant>/, always."""
    from backend.config import settings

    resp = client.post(
        "/api/v1/datasets",
        files={"file": (filename, b"sku,date,demand\nA,2024-01-01,1\n", "text/csv")},
        headers=auth_headers,
    )
    body = _assert_survivable(resp, f"upload named {filename!r}")
    if resp.status_code >= 400:
        return                     # refusing a hostile filename is a fine answer

    stored_path = query_one(
        "SELECT file_path FROM datasets WHERE id = %s", (body["data"]["id"],)
    )["file_path"]
    from pathlib import Path
    resolved = Path(stored_path).resolve()
    root = Path(settings.storage_path).resolve()
    assert root in resolved.parents, (
        f"{filename!r} was written to {resolved} — outside {root}"
    )
    assert registered_user["tenant"]["id"] in str(resolved), (
        f"{filename!r} landed outside its tenant's directory: {resolved}"
    )


def test_an_upload_with_no_body_and_an_upload_that_is_not_a_table(
    client, auth_headers,
):
    """Zero bytes, and 5 MB of random bytes wearing a .csv extension. Both are
    what a broken exporter produces, and both must be answered."""
    import os
    for name, payload in (
        ("empty.csv", b""),
        ("random.csv", os.urandom(5 * 1024 * 1024)),
        ("html.csv", b"<!doctype html><html><body>login page</body></html>"),
        ("zip.csv", b"PK\x03\x04" + b"\x00" * 1000),
    ):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": (name, payload, "text/csv")},
            headers=auth_headers,
        )
        _assert_survivable(resp, f"upload {name}")
        assert resp.status_code < 500 or resp.json().get("error_code"), (
            f"{name} produced an unnamed server error"
        )
