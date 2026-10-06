"""Contract cases for multi-currency (exchange rates + conversion preview).

These routes are NEW and exist only in Rust, so there is no Python answer to
diff against. The section asserts the Rust contract itself (status, error code,
the rows and the activity events it wrote, the permission pairs) and the
promise that crosses the two services: a rate entered through the Rust routes is
the rate the PYTHON purchase-order path converts with, the order records it, and
nothing entered or deleted later moves an order already written.

Hooked into `contract_test.py` by `run_fx_section` (a few lines). Needs --db.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

API = "/api/v1"
ROUTE = "multi-currency"


def _today():
    return datetime.now(timezone.utc).date()


def run(args, fx, db, h) -> list:
    """`h` is the contract_test module (http, Case, auth_for, make_fixture, ...)."""
    py, rs = args.python, args.rust
    out: list = []
    if db is None:
        return [(h.Case("fx (all)", "-", "-", route=ROUTE), "SKIP",
                 ["multi-currency needs --db: rows and events are checked in the database"])]

    def q1(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    def qa(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def rates_count(tenant=None):
        return q1("SELECT COUNT(*) FROM exchange_rates WHERE tenant_id = %s", (tenant or fx.tenant_id,))[0]

    def call(base, method, path, who="admin", body=None, token=None, raw_body=None):
        tok = token if token is not None else h.auth_for(fx, who)
        return h.http(base, method, path, token=tok, body=body, raw_body=raw_body)

    def data(r):
        return r.body.get("data") if isinstance(r.body, dict) else None

    def code(r):
        return r.body.get("error_code") if isinstance(r.body, dict) else None

    def record(name, method, path, who, problems):
        if args.only and args.only not in name:
            return
        hard = [p for p in problems if not p.startswith("(")]
        out.append((h.Case(name, method, path, who=who, route=ROUTE), "FAIL" if hard else "PASS", problems))

    def expect(r, status, error_code=None):
        problems = []
        if r.status != status:
            problems.append(f"status {r.status}, expected {status}: {json.dumps(r.body)[:300]}")
        elif error_code is not None and code(r) != error_code:
            problems.append(f"error_code {code(r)!r}, expected {error_code!r}")
        return problems

    def events(action, n=5):
        return qa("SELECT user_id, resource, context FROM activity_logs WHERE tenant_id = %s AND action = %s "
                  "ORDER BY created_at DESC LIMIT %s", (fx.tenant_id, action, n))

    R = f"{API}/tenant/currency/rates"
    CONVERT = f"{API}/tenant/currency/convert"
    today = _today()
    iso = today.isoformat()

    # A clean slate on this throwaway tenant (the base currency is the default, CRC).
    db.cursor().execute("DELETE FROM exchange_rates WHERE tenant_id = %s", (fx.tenant_id,))
    base = data(call(rs, "GET", f"{API}/tenant/currency"))["current"]["code"]

    # ── reads, every role ────────────────────────────────────────────────────
    for who in ("admin", "analyst", "viewer", "key_read"):
        r = call(rs, "GET", R, who)
        p = expect(r, 200)
        if not p and (data(r)["base_currency"] != base or data(r)["items"] != [] or data(r)["total"] != 0):
            p.append(f"empty list expected on a fresh tenant: {data(r)}")
        record(f"fx list empty ({who})", "GET", R, who, p)
    r = call(rs, "GET", R, "none")
    record("fx list without a token is 401", "GET", R, "none", expect(r, 401))

    # ── writes: the permission pair, and state unchanged on every refusal ────
    good = {"currency": "USD", "rate": "520.50", "effective_date": iso, "source_note": "central bank"}
    for who, status, ecode in (("viewer", 403, "role_not_permitted"), ("analyst", 403, "role_not_permitted"),
                               ("key_write", 403, "api_key_route_not_exposed"), ("none", 401, None)):
        n0 = rates_count()
        r = call(rs, "POST", R, who, body=good)
        p = expect(r, status, ecode)
        if rates_count() != n0:
            p.append("a refused write changed exchange_rates")
        record(f"fx create refused ({who})", "POST", R, who, p)

    n0 = rates_count()
    r = call(rs, "POST", R, "admin", body=good)
    p = expect(r, 201)
    usd_id = None
    if not p:
        row = data(r)
        usd_id = row["id"]
        if (row["rate"], row["currency"], row["base_currency"], row["effective_date"], row["source_note"]) \
                != ("520.5", "USD", base, iso, "central bank"):
            p.append(f"response row wrong: {row}")
        db_row = q1("SELECT rate::text, currency, base_currency, effective_date, source_note, created_by "
                    "FROM exchange_rates WHERE tenant_id = %s AND id = %s", (fx.tenant_id, usd_id))
        if db_row is None or (db_row[0], db_row[1], db_row[2], db_row[3], db_row[4], db_row[5]) \
                != ("520.5000000000", "USD", base, today, "central bank", fx.admin_id):
            p.append(f"stored row wrong: {db_row}")
        if rates_count() != n0 + 1:
            p.append("exactly one row expected")
        ev = events("currency_rate.created")
        if len(ev) != 1 or ev[0][1] != usd_id or ev[0][0] != fx.admin_id or ev[0][2] != {
                "currency": "USD", "rate": "520.5", "effective_date": iso, "severity": "info", "kind": "purchase"}:
            p.append(f"activity event wrong: {ev}")
    record("fx create (admin)", "POST", R, "admin", p)

    n0 = rates_count()
    r = call(rs, "POST", R, "admin", body={**good, "rate": "999"})
    p = expect(r, 409, "fx_rate_exists")
    if rates_count() != n0 or q1("SELECT rate::text FROM exchange_rates WHERE id = %s", (usd_id,))[0] != "520.5000000000":
        p.append("a duplicate must change nothing")
    record("fx create duplicate date is 409", "POST", R, "admin", p)

    bad_cases = [
        ("rate zero", {"currency": "USD", "rate": "0", "effective_date": "2026-01-01"}, 422, "fx_rate_invalid"),
        ("rate too precise", {"currency": "USD", "rate": "1.00000000001", "effective_date": "2026-01-02"}, 422, "fx_rate_invalid"),
        ("rate negative", {"currency": "USD", "rate": -3, "effective_date": "2026-01-03"}, 422, "fx_rate_invalid"),
        ("rate not a number", {"currency": "USD", "rate": "abc", "effective_date": "2026-01-04"}, 422, "fx_rate_invalid"),
        ("rate a list", {"currency": "USD", "rate": [1], "effective_date": "2026-01-05"}, 422, "fx_rate_invalid"),
        ("rate too large", {"currency": "USD", "rate": "1000000001", "effective_date": "2026-01-06"}, 422, "fx_rate_invalid"),
        ("rate missing", {"currency": "USD"}, 422, "validation_error"),
        ("currency missing", {"rate": "1"}, 422, "validation_error"),
        ("currency unsupported", {"currency": "XXX", "rate": "1"}, 400, "currency_not_supported"),
        ("currency is the base", {"currency": base, "rate": "1"}, 422, "fx_rate_currency_is_base"),
        ("date not a date", {"currency": "USD", "rate": "1", "effective_date": "yesterday"}, 422, "fx_rate_date_invalid"),
        ("date before 2000", {"currency": "USD", "rate": "1", "effective_date": "1999-12-31"}, 422, "fx_rate_date_invalid"),
        ("date too far ahead", {"currency": "USD", "rate": "1",
                                "effective_date": (today + timedelta(days=400)).isoformat()}, 422, "fx_rate_date_invalid"),
        ("note too long", {"currency": "USD", "rate": "1", "effective_date": "2026-01-07",
                           "source_note": "x" * 201}, 422, "validation_error"),
        ("body not an object", [1, 2], 422, "validation_error"),
    ]
    for name, body, status, ecode in bad_cases:
        n0 = rates_count()
        r = call(rs, "POST", R, "admin", body=body)
        p = expect(r, status, ecode)
        if rates_count() != n0:
            p.append("a refused create wrote a row")
        record(f"fx create invalid: {name}", "POST", R, "admin", p)
    r = call(rs, "POST", R, "admin", raw_body=b"{not json")
    record("fx create invalid: broken json", "POST", R, "admin", expect(r, 422, "validation_error"))

    # A JSON number is taken through its shortest text: 0.1 is one tenth.
    r = call(rs, "POST", R, "admin", body={"currency": "EUR", "rate": 0.1, "effective_date": iso})
    p = expect(r, 201)
    if not p and q1("SELECT rate::text FROM exchange_rates WHERE tenant_id = %s AND currency = 'EUR'",
                    (fx.tenant_id,))[0] != "0.1000000000":
        p.append("0.1 must be stored as exactly 0.1")
    record("fx create from a JSON number is exact", "POST", R, "admin", p)

    # A future-dated EUR rate and an older USD one, to exercise "in force".
    call(rs, "POST", R, "admin", body={"currency": "EUR", "rate": "600", "effective_date": (today + timedelta(days=5)).isoformat()})
    call(rs, "POST", R, "admin", body={"currency": "USD", "rate": "500", "effective_date": (today - timedelta(days=30)).isoformat(),
                                       "source_note": "  "})
    r = call(rs, "GET", R, "viewer")
    p = expect(r, 200)
    if not p:
        items = data(r)["items"]
        flags = {(i["currency"], i["effective_date"]): i["in_force"] for i in items}
        want = {("EUR", (today + timedelta(days=5)).isoformat()): False, ("EUR", iso): True,
                ("USD", iso): True, ("USD", (today - timedelta(days=30)).isoformat()): False}
        if flags != want:
            p.append(f"in_force flags {flags}")
        if [i["currency"] for i in items] != sorted(i["currency"] for i in items):
            p.append("rates must come sorted by currency")
        note = [i for i in items if i["effective_date"] == (today - timedelta(days=30)).isoformat()][0]
        if note["source_note"] is not None:
            p.append("a blank note must be stored as null")
    record("fx list shows which rate is in force", "GET", R, "viewer", p)

    for q, status, ecode in (("?currency=USD", 200, None), ("?limit=0", 422, "validation_error"),
                             ("?limit=501", 422, "validation_error"), ("?offset=-1", 422, "validation_error"),
                             ("?limit=abc", 422, "validation_error")):
        r = call(rs, "GET", R + q, "key_read")
        p = expect(r, status, ecode)
        if q == "?currency=USD" and not p and {i["currency"] for i in data(r)["items"]} != {"USD"}:
            p.append("filter by currency")
        record(f"fx list {q}", "GET", R + q, "key_read", p)

    # ── resolve ──────────────────────────────────────────────────────────────
    r = call(rs, "GET", f"{R}/resolve?currency=usd", "viewer")
    p = expect(r, 200)
    if not p and (data(r)["rate"]["rate"], data(r)["rate"]["effective_date"], data(r)["same_currency"]) != ("520.5", iso, False):
        p.append(f"resolve: {data(r)}")
    record("fx resolve today", "GET", f"{R}/resolve?currency=usd", "viewer", p)
    r = call(rs, "GET", f"{R}/resolve?currency=USD&on={(today - timedelta(days=10)).isoformat()}", "analyst")
    p = expect(r, 200)
    if not p and (data(r)["rate"]["rate"], data(r)["rate"]["age_days"]) != ("500", 20):
        p.append(f"resolve an earlier day must use the earlier rate: {data(r)}")
    record("fx resolve an earlier date", "GET", f"{R}/resolve?currency=USD&on=...", "analyst", p)
    r = call(rs, "GET", f"{R}/resolve?currency=USD&on={(today - timedelta(days=60)).isoformat()}", "viewer")
    p = expect(r, 404, "fx_rate_missing")
    if not p and r.body["error_params"]["currency"] != "USD":
        p.append("params name the currency")
    record("fx resolve before the first rate is missing (never the nearest future rate)", "GET", R, "viewer", p)
    r = call(rs, "GET", f"{R}/resolve?currency=EUR", "viewer")
    p = expect(r, 200)
    if not p and data(r)["rate"]["rate"] != "0.1":
        p.append("a future-dated rate must not be used today")
    record("fx resolve ignores a future-dated rate", "GET", R, "viewer", p)
    r = call(rs, "GET", f"{R}/resolve?currency=MXN", "viewer")
    record("fx resolve with no rate is a 404, not 1.0", "GET", R, "viewer", expect(r, 404, "fx_rate_missing"))
    r = call(rs, "GET", f"{R}/resolve?currency={base}", "viewer")
    p = expect(r, 200)
    if not p and (data(r)["same_currency"], data(r)["rate"]) != (True, None):
        p.append("the base currency needs no rate")
    record("fx resolve the base currency", "GET", R, "viewer", p)
    record("fx resolve unsupported", "GET", R, "viewer", expect(call(rs, "GET", f"{R}/resolve?currency=XXX", "viewer"), 400, "currency_not_supported"))
    record("fx resolve needs a currency", "GET", R, "viewer", expect(call(rs, "GET", f"{R}/resolve", "viewer"), 422, "validation_error"))
    record("fx resolve bad date", "GET", R, "viewer", expect(call(rs, "GET", f"{R}/resolve?currency=USD&on=nope", "viewer"), 422, "validation_error"))

    # ── convert (a read-only preview: any role, a read key included) ─────────
    for who in ("viewer", "key_read"):
        r = call(rs, "POST", CONVERT, who, body={"amount": "100", "currency": "USD"})
        p = expect(r, 200)
        if not p and (data(r)["converted"], data(r)["rate"]["rate"], data(r)["same_currency"]) != ("52050.00", "520.5", False):
            p.append(f"convert: {data(r)}")
        record(f"fx convert ({who})", "POST", CONVERT, who, p)
    for name, body, status, ecode in (
        ("no rate for the currency", {"amount": "10", "currency": "MXN"}, 404, "fx_rate_missing"),
        ("unsupported currency", {"amount": "10", "currency": "XXX"}, 400, "currency_not_supported"),
        ("negative amount", {"amount": "-1", "currency": "USD"}, 422, "fx_amount_invalid"),
        ("amount not a number", {"amount": "abc", "currency": "USD"}, 422, "fx_amount_invalid"),
        ("amount missing", {"currency": "USD"}, 422, "validation_error"),
        ("a future date's rate", {"amount": "1", "currency": "EUR", "on": (today + timedelta(days=6)).isoformat()}, 200, None),
    ):
        r = call(rs, "POST", CONVERT, "viewer", body=body)
        p = expect(r, status, ecode)
        if name == "a future date's rate" and not p and data(r)["converted"] != "600.00":
            p.append(f"the future rate applies on its own date: {data(r)}")
        record(f"fx convert: {name}", "POST", CONVERT, "viewer", p)
    r = call(rs, "POST", CONVERT, "viewer", body={"amount": "2.675", "currency": base})
    p = expect(r, 200)
    if not p and (data(r)["converted"], data(r)["same_currency"], data(r)["rate"]) != ("2.68", True, None):
        p.append(f"the base currency converts to itself, rounded half up: {data(r)}")
    record("fx convert the base currency", "POST", CONVERT, "viewer", p)
    r = call(rs, "POST", CONVERT, "viewer", body={"amount": 0.1, "currency": "EUR"})
    p = expect(r, 200)
    if not p and data(r)["converted"] != "0.01":
        p.append(f"0.1 x 0.1 = 0.01 exactly: {data(r)}")
    record("fx convert is exact (no float noise)", "POST", CONVERT, "viewer", p)

    # ── correct and remove ───────────────────────────────────────────────────
    r = call(rs, "PATCH", f"{R}/{usd_id}", "viewer", body={"rate": "1"})
    p = expect(r, 403, "role_not_permitted")
    if q1("SELECT rate::text FROM exchange_rates WHERE id = %s", (usd_id,))[0] != "520.5000000000":
        p.append("viewer changed a rate")
    record("fx patch refused (viewer)", "PATCH", R, "viewer", p)
    r = call(rs, "PATCH", f"{R}/{usd_id}", "analyst", body={"rate": "1"})
    p = expect(r, 403, "role_not_permitted")
    if q1("SELECT rate::text FROM exchange_rates WHERE id = %s", (usd_id,))[0] != "520.5000000000":
        p.append("analyst changed a rate")
    record("fx patch refused (analyst)", "PATCH", R, "analyst", p)
    r = call(rs, "PATCH", f"{R}/{usd_id}", "admin", body={})
    record("fx patch nothing to change", "PATCH", R, "admin", expect(r, 422, "fx_rate_nothing_to_change"))
    r = call(rs, "PATCH", f"{R}/{usd_id}", "admin", body={"rate": "0"})
    record("fx patch invalid rate", "PATCH", R, "admin", expect(r, 422, "fx_rate_invalid"))
    before = q1("SELECT updated_at FROM exchange_rates WHERE id = %s", (usd_id,))[0]
    r = call(rs, "PATCH", f"{R}/{usd_id}", "admin", body={"rate": "521.25", "source_note": "corrected"})
    p = expect(r, 200)
    if not p:
        row = q1("SELECT rate::text, source_note, updated_at, currency, effective_date FROM exchange_rates WHERE id = %s", (usd_id,))
        if (row[0], row[1], row[3], row[4]) != ("521.2500000000", "corrected", "USD", today) or row[2] <= before:
            p.append(f"patched row wrong: {row}")
        ev = events("currency_rate.changed")
        if len(ev) != 1 or ev[0][2].get("rate") != "521.25":
            p.append(f"changed event wrong: {ev}")
    record("fx patch (admin)", "PATCH", R, "admin", p)
    r = call(rs, "PATCH", f"{R}/{usd_id}", "admin", body={"source_note": None})
    p = expect(r, 200)
    if not p and q1("SELECT source_note FROM exchange_rates WHERE id = %s", (usd_id,))[0] is not None:
        p.append("an explicit null clears the note")
    record("fx patch clears the note", "PATCH", R, "admin", p)
    call(rs, "PATCH", f"{R}/{usd_id}", "admin", body={"rate": "520.5"})

    other = h.make_fixture(py, fx.secret)
    try:
        foreign = "fx" + other.tenant_id[-10:]
        db.cursor().execute(
            "INSERT INTO exchange_rates (id, tenant_id, currency, base_currency, rate, effective_date, created_by) "
            "VALUES (%s, %s, 'USD', 'CRC', 99, %s, 'x')", (foreign, other.tenant_id, today))
        for method, body in (("PATCH", {"rate": "1"}), ("DELETE", None)):
            r = call(rs, method, f"{R}/{foreign}", "admin", body=body)
            p = expect(r, 404, "fx_rate_not_found")
            if q1("SELECT rate::text FROM exchange_rates WHERE id = %s", (foreign,))[0] != "99.0000000000":
                p.append("another tenant's rate was touched")
            record(f"fx {method.lower()} another tenant's rate is 404", method, R, "admin", p)
        r = call(rs, "GET", R, "admin")
        if not any(i["id"] == foreign for i in data(r)["items"]):
            record("fx list never shows another tenant's rates", "GET", R, "admin", [])
        else:
            record("fx list never shows another tenant's rates", "GET", R, "admin", ["leaked another tenant's rate"])
    finally:
        h.erase_fixture(py, other)

    r = call(rs, "PATCH", f"{R}/does-not-exist", "admin", body={"rate": "1"})
    record("fx patch unknown id is 404", "PATCH", R, "admin", expect(r, 404, "fx_rate_not_found"))

    # ── the cross-service promise: Python orders use the rates entered here ──
    call(rs, "POST", R, "admin", body={"currency": "PEN", "rate": "3.7351", "effective_date": iso, "source_note": "contract"})
    pen_id = q1("SELECT id FROM exchange_rates WHERE tenant_id = %s AND currency = 'PEN'", (fx.tenant_id,))[0]
    rc = call(py, "POST", f"{API}/inventory/suppliers", "analyst", body={"name": f"fx-sup-{fx.tenant_id[-6:]}"})
    sup = data(rc)
    lines = [{"sku": "FX-A", "qty": 3, "unit_cost": 0.335, "currency": "PEN"},
             {"sku": "FX-B", "qty": 10, "unit_cost": 2.5},
             {"sku": "FX-C", "qty": 4, "unit_cost": 1.0, "currency": "MXN"}]
    r = call(py, "POST", f"{API}/inventory/po", "analyst", body={"supplier_id": sup["id"], "lines": lines})
    p = expect(r, 201)
    po_id = None
    if not p:
        po_id = data(r)["id"]
        rows = {x[0]: x for x in qa("SELECT sku, currency, fx_rate::text, fx_rate_id, fx_rate_date, value_base::text, fx_base_currency "
                                    "FROM inventory_po_items WHERE po_log_id = %s", (po_id,))}
        quoted = call(rs, "POST", CONVERT, "viewer", body={"amount": "1.005", "currency": "PEN"})  # 3 x 0.335
        a = rows["FX-A"]
        if (a[1], a[2], a[3], a[4], a[6]) != ("PEN", "3.7351000000", pen_id, today, base):
            p.append(f"line A must record the rate it used: {a}")
        if data(quoted)["converted"] != a[5]:
            p.append(f"Rust convert says {data(quoted)['converted']}, the Python order stored {a[5]}")
        if (rows["FX-B"][1], rows["FX-B"][5]) != (None, None):
            p.append(f"a base-currency line has no currency and no converted value: {rows['FX-B']}")
        c = rows["FX-C"]
        if (c[1], c[2], c[5]) != ("MXN", None, None):
            p.append(f"line C has no rate: unconverted, never valued at 1.0: {c}")
        hdr = q1("SELECT total_value, fx_unconverted_lines FROM inventory_po_log WHERE id = %s", (po_id,))
        want_total = float(a[5]) + 25.0
        if (round(hdr[0], 2), hdr[1]) != (round(want_total, 2), 1):
            p.append(f"header total {hdr}, expected ({want_total}, 1)")
    record("fx a Python order uses the rate entered through Rust", "POST", f"{API}/inventory/po", "analyst", p)

    snapshot = qa("SELECT sku, fx_rate::text, value_base::text, fx_rate_id FROM inventory_po_items WHERE po_log_id = %s ORDER BY sku",
                  (po_id,)) if po_id else None
    total_before = q1("SELECT total_value FROM inventory_po_log WHERE id = %s", (po_id,))[0] if po_id else None
    call(rs, "PATCH", f"{R}/{pen_id}", "admin", body={"rate": "4.5"})
    p = []
    if po_id:
        if qa("SELECT sku, fx_rate::text, value_base::text, fx_rate_id FROM inventory_po_items WHERE po_log_id = %s ORDER BY sku", (po_id,)) != snapshot:
            p.append("editing a rate moved an order already written")
        r2 = call(py, "POST", f"{API}/inventory/po", "analyst",
                  body={"supplier_id": sup["id"], "lines": [{"sku": "FX-A", "qty": 3, "unit_cost": 0.335, "currency": "PEN"}]})
        if expect(r2, 201):
            p.append("second order failed")
        elif q1("SELECT fx_rate::text FROM inventory_po_items WHERE po_log_id = %s", (data(r2)["id"],))[0] != "4.5000000000":
            p.append("a NEW order must use the corrected rate")
    record("fx editing a rate never re-converts a written order", "PATCH", R, "admin", p)

    call(rs, "DELETE", f"{R}/{pen_id}", "analyst")  # refused: analyst
    p = []
    if q1("SELECT COUNT(*) FROM exchange_rates WHERE id = %s", (pen_id,))[0] != 1:
        p.append("analyst deleted a rate")
    n0 = rates_count()
    r = call(rs, "DELETE", f"{R}/{pen_id}", "admin")
    p += expect(r, 200)
    if not p:
        if rates_count() != n0 - 1 or q1("SELECT COUNT(*) FROM exchange_rates WHERE id = %s", (pen_id,))[0] != 0:
            p.append("the rate must be gone")
        ev = events("currency_rate.deleted")
        if len(ev) != 1 or ev[0][1] != pen_id or ev[0][2].get("currency") != "PEN":
            p.append(f"deleted event wrong: {ev}")
        if po_id and q1("SELECT total_value FROM inventory_po_log WHERE id = %s", (po_id,))[0] != total_before:
            p.append("deleting a rate moved an order's total")
        if po_id and qa("SELECT sku, fx_rate::text, value_base::text, fx_rate_id FROM inventory_po_items WHERE po_log_id = %s ORDER BY sku", (po_id,)) != snapshot:
            p.append("deleting a rate moved an order already written")
    record("fx delete (admin) leaves written orders untouched", "DELETE", R, "admin", p)
    r = call(rs, "DELETE", f"{R}/{pen_id}", "admin")
    record("fx delete twice is 404", "DELETE", R, "admin", expect(r, 404, "fx_rate_not_found"))
    r3 = call(py, "POST", f"{API}/inventory/po", "analyst",
              body={"supplier_id": sup["id"], "lines": [{"sku": "FX-A", "qty": 1, "unit_cost": 1.0, "currency": "PEN"}]})
    p = expect(r3, 201)
    if not p:
        row = q1("SELECT fx_rate, value_base FROM inventory_po_items WHERE po_log_id = %s", (data(r3)["id"],))
        if row != (None, None):
            p.append(f"with the rate deleted a new order is unconverted, never 1.0: {row}")
    record("fx an order written after the rate is deleted is unconverted", "POST", f"{API}/inventory/po", "analyst", p)

    # ── relabelling the base currency: old rates stop applying ───────────────
    p = expect(call(py, "PATCH", f"{API}/tenant/currency", "admin", body={"code": "MXN"}), 200)
    r = call(rs, "GET", R, "viewer")
    p += expect(r, 200)
    if not p and (data(r)["base_currency"], data(r)["items"], data(r)["other_base_count"] >= 1) != ("MXN", [], True):
        p.append(f"after relabelling to MXN no CRC rate may apply: {data(r)}")
    r = call(rs, "GET", f"{R}/resolve?currency=USD", "viewer")
    p += expect(r, 404, "fx_rate_missing")
    r = call(rs, "POST", R, "admin", body={"currency": "MXN", "rate": "1"})
    p += expect(r, 422, "fx_rate_currency_is_base")
    call(py, "PATCH", f"{API}/tenant/currency", "admin", body={"code": base})
    record("fx relabelling the base currency makes the old rates stop applying", "PATCH", f"{API}/tenant/currency", "admin", p)

    # ── the arithmetic itself: Python reference vs the Rust binary, fresh seeds ─
    if not args.only or args.only in "fx differential":
        import fx_differential  # noqa: PLC0415 - next to this file
        binary = fx_differential.find_binary()
        case = h.Case("fx differential (python reference vs rust fx-eval, 20000 fresh cases)", "-", "-", route=ROUTE)
        if binary is None:
            out.append((case, "SKIP", ["no stockai-api binary found (set STOCKAI_RS_BIN or build backend-rs)"]))
        else:
            seeds = [int(datetime.now(timezone.utc).timestamp()) % 10**6 + i for i in range(4)]
            total, problems = fx_differential.check(binary, seeds, 5000)
            out.append((case, "FAIL" if problems else "PASS", problems or [f"({total} cases, seeds {seeds})"]))

    return out
