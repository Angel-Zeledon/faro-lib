"""Contract scenarios for stock allocation (`/api/v1/allocation/...`).

These routes exist ONLY in Rust (no Python twin to diff against), so the
harness cannot compare two answers. It does what the testing mandate asks
instead: it sends real requests to the Rust service and asserts the state it
left in the database, with a permission pair on every write, plus the one
promise that matters for a purchasing product: the allocation is advisory and
never changes `inventory_stock`.

Called from `contract_test.run` as `run_allocation(args, fx, db, deps)`; the
shared helpers come in through `deps` so this file does not import the harness
(and a merge of the harness stays a one-line change).
"""

from __future__ import annotations

import secrets
from typing import Any, Callable

MICRO = 1_000_000


def run_allocation(args, fx, db, deps: dict) -> list:
    http, API, auth_for, today_plus, Case = (deps[k] for k in ("http", "API", "auth_for", "today_plus", "Case"))
    route = "/allocation/*"
    out: list = []

    if db is None:
        return [(Case("allocation (all)", "-", "-", route=route), "SKIP",
                 ["needs --db: rows are seeded and asserted in the database"])]

    cur = db.cursor()
    tag = secrets.token_hex(3)
    rs = args.rust
    py = args.python
    sku, sku_nostock = f"AL-{tag}", f"AL-NS-{tag}"

    def call(method, path, who="admin", body=None, base=None, headers=None):
        return http(base or rs, method, f"{API}{path}", token=auth_for(fx, who), body=body, headers=headers or {})

    def q1(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchone()

    def qa(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchall()

    def record(name, problems):
        hard = [p for p in problems if p]
        out.append((Case(f"allocation: {name}", "-", "-", route=route), "FAIL" if hard else "PASS", hard))

    def expect(resp, status, code=None):
        errs = []
        if resp.status != status:
            errs.append(f"status {resp.status} (wanted {status}): {str(resp.body)[:300]}")
        elif code is not None:
            got = resp.body.get("error_code") if isinstance(resp.body, dict) else None
            if got != code:
                errs.append(f"error_code {got!r} (wanted {code!r})")
        return errs

    def stock_value():
        r = q1("SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (fx.tenant_id, sku))
        return None if r is None else float(r[0])

    def count(table, extra="", params=()):
        return q1(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s {extra}", (fx.tenant_id, *params))[0]

    def events(action):
        return qa("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s ORDER BY created_at",
                  (fx.tenant_id, action))

    def line_by_customer(data, name):
        return [ln for ln in data["lines"] if ln["customer"] == name]

    # ── Seed: stock 10, three open commitments ──────────────────────────────
    cur.execute("INSERT INTO inventory_stock (tenant_id, sku, current_stock) VALUES (%s, %s, 10)", (fx.tenant_id, sku))
    ids = {}
    for customer, qty, days in (("Acme", 8, 10), ("Globex", 8, 10), ("Initech", 4, 20)):
        r = http(py, "POST", f"{API}/committed-demand", token=auth_for(fx, "admin"),
                 body={"sku": sku, "delivery_date": today_plus(days), "quantity": qty, "customer": customer,
                       "probability": 1})
        if r.status != 201:
            return [(Case("allocation setup", "-", "-", route=route), "FAIL", [f"seeding {customer}: {r.status} {r.body}"])]
        ids[customer] = r.body["data"]["id"]
    # A product with a commitment and NO stock row.
    r = http(py, "POST", f"{API}/committed-demand", token=auth_for(fx, "admin"),
             body={"sku": sku_nostock, "delivery_date": today_plus(15), "quantity": 5, "customer": "Acme", "probability": 1})
    if r.status != 201:
        return [(Case("allocation setup", "-", "-", route=route), "FAIL", [f"seeding no-stock sku: {r.status} {r.body}"])]

    # ── Priorities: permission pair, validation, state ──────────────────────
    r = call("GET", "/allocation/priorities", "viewer")
    record("GET priorities (viewer may read)", expect(r, 200) + (
        [] if r.status != 200 or {c["customer"] for c in r.body["data"]["unassigned_customers"]} >= {"Acme", "Globex", "Initech"}
        else ["unassigned customers do not list the three seeded ones"]))

    before = count("allocation_customer_priorities")
    r = call("PUT", "/allocation/priorities", "viewer", {"priorities": [{"customer": "Acme", "tier": 1}]})
    record("PUT priorities (viewer denied, nothing written)", expect(r, 403, "role_not_permitted") + (
        [] if count("allocation_customer_priorities") == before else ["viewer wrote a priority"]))

    for label, body, status, code in (
        ("tier 10", {"priorities": [{"customer": "Acme", "tier": 10}]}, 422, "validation_error"),
        ("tier as text", {"priorities": [{"customer": "Acme", "tier": "1"}]}, 422, "validation_error"),
        ("empty body", {}, 422, "validation_error"),
        ("same customer twice", {"priorities": [{"customer": "Acme", "tier": 1}, {"customer": "  ACME ", "tier": 2}]},
         422, "allocation_duplicate_customer"),
    ):
        r = call("PUT", "/allocation/priorities", "analyst", body)
        record(f"PUT priorities ({label} refused, nothing written)", expect(r, status, code) + (
            [] if count("allocation_customer_priorities") == before else ["a refused request wrote a row"]))

    r = call("PUT", "/allocation/priorities", "analyst",
             {"priorities": [{"customer": "Acme", "tier": 1}, {"customer": "Globex", "tier": 2}]})
    rows = {k: t for k, t in qa("SELECT customer_key, tier FROM allocation_customer_priorities WHERE tenant_id = %s", (fx.tenant_id,))}
    ev = events("allocation.priorities_changed")
    record("PUT priorities (analyst saves; rows and event)", expect(r, 200) + (
        [] if rows == {"acme": 1, "globex": 2} else [f"rows are {rows}"]) + (
        [] if len(ev) == 1 and ev[0][0].get("customers") == 2 else [f"event rows: {ev}"]))

    r = call("PUT", "/allocation/priorities", "analyst",
             {"priorities": [{"customer": "Acme", "tier": 1}, {"customer": "Globex", "tier": 2}]})
    record("PUT priorities (same values again: no change, no second event)", expect(r, 200) + (
        [] if len(events("allocation.priorities_changed")) == 1 else ["a no-op wrote an event"]))

    # ── Preview: numbers, what-if, advisory ─────────────────────────────────
    r = call("POST", "/allocation/preview", "viewer", {"sku": sku})
    d = r.body.get("data", {}) if isinstance(r.body, dict) else {}
    problems = expect(r, 200)
    if not problems:
        acme, globex, initech = (line_by_customer(d, n)[0] for n in ("Acme", "Globex", "Initech"))
        # stock 10: Acme (tier 1) 8, Globex (tier 2) 2, Initech (default tier 5) 0.
        if (acme["allocated"], globex["allocated"], initech["allocated"]) != (8.0, 2.0, 0.0):
            problems.append(f"allocation {acme['allocated']}/{globex['allocated']}/{initech['allocated']}, wanted 8/2/0")
        if d["totals"]["demand"] != 20.0 or d["totals"]["short"] != 10.0 or d["totals"]["allocated"] != 10.0:
            problems.append(f"totals {d['totals']}")
        if initech["tier"] != 5 or initech["tier_source"] != "default":
            problems.append("an unassigned customer should be tier 5 / default")
        if d["advisory"] is not True or d["status"] != "ok" or not d["contested"]:
            problems.append("advisory/status/contested flags")
    problems += [] if stock_value() == 10.0 else ["PREVIEW CHANGED THE STOCK"]
    problems += [] if count("stock_reservations") == 0 else ["a preview wrote reservations"]
    record("preview (viewer; 8/2/0; stock untouched; nothing written)", problems)
    base_hash = d.get("result_hash")

    r = call("POST", "/allocation/preview", "analyst", {"sku": sku, "tier_overrides": [{"customer": "Acme", "tier": 9}]})
    d2 = r.body.get("data", {}) if isinstance(r.body, dict) else {}
    problems = expect(r, 200)
    if not problems:
        got = (line_by_customer(d2, "Acme")[0]["allocated"], line_by_customer(d2, "Globex")[0]["allocated"])
        if got != (0.0, 8.0):
            problems.append(f"what-if tiers gave Acme/Globex {got}")
        if not d2["what_if"]:
            problems.append("what_if flag not set")
        if d2["result_hash"] == base_hash:
            problems.append("a what-if has the same hash as the saved policy")
    problems += [] if count("allocation_customer_priorities") == 2 else ["a what-if changed the saved priorities"]
    record("preview what-if tiers (nothing saved)", problems)

    r = call("POST", "/allocation/preview", "admin",
             {"sku": sku, "extra_arrivals": [{"date": today_plus(5), "quantity": 20}]})
    d3 = r.body.get("data", {}) if isinstance(r.body, dict) else {}
    record("preview what-if extra order covers everyone", expect(r, 200) + (
        [] if not expect(r, 200) and d3["totals"]["short"] == 0.0 else ["extra order of 20 should leave nothing short"]))

    r = call("POST", "/allocation/preview", "admin", {"sku": "NO-SUCH-" + tag})
    record("preview unknown product -> 404", expect(r, 404, "allocation_no_commitments"))

    r = call("POST", "/allocation/preview", "admin", {"sku": sku_nostock})
    d4 = r.body.get("data", {}) if isinstance(r.body, dict) else {}
    record("preview with no stock row is 'stock_unknown', never all-short", expect(r, 200) + (
        [] if not expect(r, 200) and d4["status"] == "stock_unknown" and d4["totals"]["short"] is None
        and all(ln["allocated"] is None for ln in d4["lines"]) else [f"no-stock answer: {str(d4)[:300]}"]))

    for label, body in (
        ("past date", {"sku": sku, "extra_arrivals": [{"date": "2020-01-01", "quantity": 5}]}),
        ("zero quantity", {"sku": sku, "extra_arrivals": [{"date": today_plus(3), "quantity": 0}]}),
        ("bad tier override", {"sku": sku, "tier_overrides": [{"customer": "Acme", "tier": 0}]}),
        ("missing sku", {}),
    ):
        record(f"preview ({label}) -> 422", expect(call("POST", "/allocation/preview", "admin", body), 422, "validation_error"))
    r = call("POST", "/allocation/preview", "none")
    record("preview without a token -> 401", expect(r, 401))

    # ── Fair share ──────────────────────────────────────────────────────────
    r = call("PUT", "/allocation/priorities", "analyst",
             {"priorities": [{"customer": "Globex", "tier": 1}], "fair_share_tiers": [1]})
    fair_rows = [t for (t,) in qa("SELECT tier FROM allocation_tier_policy WHERE tenant_id = %s AND fair_share", (fx.tenant_id,))]
    problems = expect(r, 200) + ([] if fair_rows == [1] else [f"fair tiers stored: {fair_rows}"])
    r = call("POST", "/allocation/preview", "admin", {"sku": sku})
    if r.status == 200:
        dd = r.body["data"]
        got = (line_by_customer(dd, "Acme")[0]["allocated"], line_by_customer(dd, "Globex")[0]["allocated"])
        if got != (5.0, 5.0):
            problems.append(f"fair-share split of 10 between two claims of 8 gave {got}, wanted (5, 5)")
    else:
        problems += expect(r, 200)
    record("fair-share tier splits proportionally (5/5)", problems)
    call("PUT", "/allocation/priorities", "analyst",
         {"priorities": [{"customer": "Globex", "tier": 2}], "fair_share_tiers": []})
    if qa("SELECT 1 FROM allocation_tier_policy WHERE tenant_id = %s AND fair_share", (fx.tenant_id,)):
        record("fair-share tiers can be cleared", ["fair-share row survived an empty list"])

    # ── Apply ───────────────────────────────────────────────────────────────
    r = call("POST", "/allocation/preview", "admin", {"sku": sku})
    good_hash = r.body["data"]["result_hash"]

    r = call("POST", "/allocation/apply", "viewer", {"sku": sku, "result_hash": good_hash})
    record("apply (viewer denied, nothing written)", expect(r, 403, "role_not_permitted") + (
        [] if count("stock_reservations") == 0 else ["viewer wrote reservations"]))

    r = call("POST", "/allocation/apply", "analyst", {"sku": sku, "result_hash": "0" * 64})
    record("apply with a stale hash -> 409, nothing written", expect(r, 409, "allocation_stale") + (
        [] if count("stock_reservations") == 0 and count("allocation_runs") == 0 else ["a stale apply wrote rows"]))

    r = call("POST", "/allocation/apply", "analyst", {"sku": sku, "result_hash": "short"})
    record("apply with a malformed hash -> 422", expect(r, 422, "validation_error"))

    r = call("POST", "/allocation/apply", "analyst",
             {"sku": sku, "result_hash": good_hash, "extra_arrivals": [{"date": today_plus(3), "quantity": 5}]})
    record("apply against a hypothetical order refused", expect(r, 422, "allocation_apply_hypothetical_supply") + (
        [] if count("stock_reservations") == 0 else ["wrote reservations"]))

    r = call("POST", "/allocation/apply", "analyst", {"sku": sku_nostock, "result_hash": good_hash})
    record("apply with no stock row refused", expect(r, 409, "allocation_stock_unknown") + (
        [] if count("stock_reservations") == 0 else ["wrote reservations"]))

    r = call("POST", "/allocation/apply", "analyst", {"sku": sku, "result_hash": good_hash})
    problems = expect(r, 200)
    res = {cid: (res_, short, units, status) for cid, res_, short, units, status in qa(
        "SELECT commitment_id, reserved_micro, short_micro, units_micro, status FROM stock_reservations "
        "WHERE tenant_id = %s AND sku = %s", (fx.tenant_id, sku))}
    want = {ids["Acme"]: (8 * MICRO, 0, 8 * MICRO, "active"), ids["Globex"]: (2 * MICRO, 6 * MICRO, 8 * MICRO, "active"),
            ids["Initech"]: (0, 4 * MICRO, 4 * MICRO, "active")}
    if res != want:
        problems.append(f"reservations {res}")
    if count("allocation_runs", "AND sku = %s", (sku,)) != 1:
        problems.append("no run row")
    ev = events("allocation.applied")
    if len(ev) != 1 or ev[0][0].get("sku") != sku or ev[0][0].get("reserved") != 10.0 or ev[0][0].get("short") != 10.0:
        problems.append(f"applied event: {ev}")
    if stock_value() != 10.0:
        problems.append(f"APPLY CHANGED THE STOCK: {stock_value()}")
    if q1("SELECT COUNT(*) FROM inventory_stock WHERE tenant_id = %s", (fx.tenant_id,))[0] != 1:
        problems.append("apply wrote or removed a stock row")
    record("apply (analyst): reservations, run, event; stock untouched", problems)

    r = call("GET", "/allocation/reservations", "viewer", headers=None)
    items = r.body["data"]["items"] if r.status == 200 else []
    record("GET reservations (viewer): three, none stale", expect(r, 200) + (
        [] if len(items) == 3 and r.body["data"]["stale"] == 0 else [f"items={len(items)} stale={r.body['data'].get('stale')}"]))

    r = call("POST", "/allocation/apply", "analyst", {"sku": sku, "result_hash": good_hash})
    active = count("stock_reservations", "AND status = 'active' AND sku = %s", (sku,))
    released = count("stock_reservations", "AND status = 'released' AND release_reason = 'replaced' AND sku = %s", (sku,))
    record("re-apply replaces: still one active per commitment, history kept",
           expect(r, 200) + ([] if (active, released) == (3, 3) else [f"active={active} released={released}"]))

    # ── Stale detection: the order moves after the reservation ──────────────
    r = http(py, "PATCH", f"{API}/committed-demand/{ids['Acme']}", token=auth_for(fx, "admin"), body={"quantity": 9})
    problems = expect(r, 200)
    r = call("GET", "/allocation/reservations", "viewer")
    by = {i["commitment_id"]: i for i in r.body["data"]["items"]} if r.status == 200 else {}
    if by.get(ids["Acme"], {}).get("stale_reason") != "commitment_changed":
        problems.append(f"edited commitment not flagged: {by.get(ids['Acme'])}")
    if by.get(ids["Globex"], {}).get("stale"):
        problems.append("an untouched reservation was flagged stale")
    record("editing a commitment flags its reservation stale", problems)

    r = http(py, "POST", f"{API}/committed-demand/{ids['Globex']}/status", token=auth_for(fx, "admin"), body={"status": "fulfilled"})
    problems = expect(r, 200)
    r = call("GET", "/allocation/reservations", "viewer")
    by = {i["commitment_id"]: i for i in r.body["data"]["items"]} if r.status == 200 else {}
    if by.get(ids["Globex"], {}).get("stale_reason") != "commitment_closed":
        problems.append(f"closed commitment not flagged: {by.get(ids['Globex'])}")
    record("closing a commitment flags its reservation stale", problems)
    # Apply on the moved data needs a fresh preview: the old hash is refused.
    r = call("POST", "/allocation/apply", "analyst", {"sku": sku, "result_hash": good_hash})
    record("apply after the data moved -> 409 allocation_stale", expect(r, 409, "allocation_stale"))
    http(py, "POST", f"{API}/committed-demand/{ids['Globex']}/status", token=auth_for(fx, "admin"), body={"status": "open"})
    http(py, "PATCH", f"{API}/committed-demand/{ids['Acme']}", token=auth_for(fx, "admin"), body={"quantity": 8})

    # ── Overview ────────────────────────────────────────────────────────────
    r = call("GET", "/allocation/overview", "viewer")
    problems = expect(r, 200)
    if r.status == 200:
        dd = r.body["data"]
        mine = [c for c in dd["contested"] if c["sku"] == sku]
        if len(mine) != 1 or mine[0]["short"] != 10.0 or not mine[0]["has_reservations"]:
            problems.append(f"overview row: {mine}")
        if sku_nostock not in {u["sku"] for u in dd["stock_unknown"]}:
            problems.append("no-stock product missing from stock_unknown")
        if sku_nostock in {c["sku"] for c in dd["contested"]}:
            problems.append("no-stock product listed as contested")
    record("overview: contested product, no-stock product apart", problems)

    # ── Release ─────────────────────────────────────────────────────────────
    r = call("POST", "/allocation/release", "viewer", {"sku": sku})
    record("release (viewer denied, still active)", expect(r, 403, "role_not_permitted") + (
        [] if count("stock_reservations", "AND status = 'active' AND sku = %s", (sku,)) == 3 else ["viewer released"]))
    r = call("POST", "/allocation/release", "analyst", {"sku": sku})
    ev = events("allocation.released")
    record("release (analyst): all released, event", expect(r, 200) + (
        [] if count("stock_reservations", "AND status = 'active' AND sku = %s", (sku,)) == 0
        and r.body["data"]["released"] == 3 else ["not all released"]) + (
        [] if len(ev) == 1 and ev[0][0].get("reservations") == 3 else [f"released event: {ev}"]) + (
        [] if stock_value() == 10.0 else ["RELEASE CHANGED THE STOCK"]))
    r = call("POST", "/allocation/release", "analyst", {"sku": sku})
    record("release again is a no-op without an event", expect(r, 200) + (
        [] if r.body["data"]["released"] == 0 and len(events("allocation.released")) == 1 else ["a no-op released or wrote an event"]))

    # ── Access: keys, scoped callers, other tenants ─────────────────────────
    if fx.read_key:
        r = call("GET", "/allocation/priorities", "key_read")
        record("API key refused (internal route)", expect(r, 403, "api_key_route_not_exposed"))

    wh = None
    cur.execute("INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) ON CONFLICT (tenant_id, name) "
                "DO UPDATE SET name = EXCLUDED.name RETURNING id", (fx.tenant_id, f"AL-Norte-{tag}"))
    wh = cur.fetchone()[0]
    r = http(py, "POST", f"{API}/users", token=auth_for(fx, "admin"), body={
        "email": f"contract-{tag}-alscoped@stockai.demo", "role": "analyst", "full_name": "Contract al scoped"})
    if r.status == 201:
        uid = r.body["data"]["user"]["id"]
        http(py, "PUT", f"{API}/users/{uid}/warehouse-scope", token=auth_for(fx, "admin"), body={"warehouse_ids": [wh]})
        fx.tokens["al_scoped"] = deps["mint_access_token"](fx.secret, uid, fx.tenant_id, "analyst")
        for method, path, body in (("GET", "/allocation/overview", None), ("POST", "/allocation/preview", {"sku": sku}),
                                   ("POST", "/allocation/apply", {"sku": sku, "result_hash": "0" * 64})):
            rr = http(rs, method, f"{API}{path}", token=fx.tokens["al_scoped"], body=body)
            record(f"warehouse-scoped caller refused ({method} {path})", expect(rr, 403, "warehouse_scope_company_totals"))
    else:
        record("warehouse-scoped setup", [f"creating the scoped analyst failed: {r.status} {r.body}"])

    other = deps["make_fixture"](py, fx.secret)
    try:
        r = http(rs, "GET", f"{API}/allocation/overview", token=deps["mint_access_token"](
            other.secret, other.admin_id, other.tenant_id, "admin"))
        record("another tenant sees none of it", expect(r, 200) + (
            [] if r.status == 200 and r.body["data"]["contested"] == [] and r.body["data"]["skus_with_commitments"] == 0
            else ["other tenant's overview is not empty"]))
        r = http(rs, "POST", f"{API}/allocation/preview", token=deps["mint_access_token"](
            other.secret, other.admin_id, other.tenant_id, "admin"), body={"sku": sku})
        record("another tenant cannot preview my product", expect(r, 404, "allocation_no_commitments"))
    finally:
        deps["erase_fixture"](py, other)

    return out
