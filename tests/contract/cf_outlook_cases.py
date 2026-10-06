"""Contract cases for the commitment fulfillment outlook (Rust-only routes).

These routes have NO Python twin, so there is nothing to diff against: each case
seeds a small, hand-computed scenario straight into the database and asserts
the figures the Rust API must answer, the permission pair, the scope rules and
that a read writes nothing. Where Python already owns a number (the at-risk
flag and the safe order date on `GET /committed-demand`, the lead time of the
semaforo's cascade) the case compares the Rust answer to Python's, live.

Called from contract_test.py's `run` with the harness module as `ns`
(http, auth_for, Case, API, today_plus), so the two files share one fixture.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

OUTLOOK = "GET /committed-demand/outlook"
SUMMARY = "GET /committed-demand/outlook/summary"
DETAIL = "GET /committed-demand/{id}/outlook"


def run_cf(args, fx, db, ns) -> list:
    Case, http, auth_for, API = ns.Case, ns.http, ns.auth_for, ns.API
    out: list = []
    if db is None:
        return [(Case("cf outlook (all)", "-", "-", route=OUTLOOK), "SKIP",
                 ["needs --db: the scenarios are seeded in the database"])]
    cur = db.cursor()
    tag = secrets.token_hex(3)
    today = date.today()
    tid, admin = fx.tenant_id, fx.admin_id

    def d(days):
        return (today + timedelta(days=days)).isoformat()

    # ── seeding helpers ────────────────────────────────────────────────────
    def stock(sku, qty, wh="principal", lead=None, declared=None, supplier=None, category=None):
        cur.execute(
            """INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, lead_time_days,
                                            lead_time_set_by, supplier, category)
               VALUES (%s, %s, %s, %s, COALESCE(%s, 15), %s, %s, %s)""",
            (tid, sku, wh, qty, lead, declared, supplier, category))

    def commit(sku, days, qty, customer="Acme", prob=1.0, wh_id=None, status="open"):
        cur.execute(
            """INSERT INTO committed_demand (tenant_id, sku, warehouse_id, delivery_date, quantity,
                                             customer, probability, status, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (tid, sku, wh_id, d(days), qty, customer, prob, status, admin))
        return cur.fetchone()[0]

    def card(name, lead, declared="user"):
        cur.execute(
            """INSERT INTO suppliers (tenant_id, name, lead_time_days, lead_time_set_by)
               VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, name) DO UPDATE
               SET lead_time_days = EXCLUDED.lead_time_days, lead_time_set_by = EXCLUDED.lead_time_set_by""",
            (tid, name, lead, declared))

    def po(sku, qty, supplier, generated_days_ago=0, wh="principal", received=None):
        cur.execute(
            """INSERT INTO inventory_po_log (tenant_id, sku_count, total_units, po_number, reception_status,
                                             generated_at)
               VALUES (%s, 1, %s, (SELECT COALESCE(MAX(po_number), 0) + 1 FROM inventory_po_log WHERE tenant_id = %s),
                       'pending', NOW() - make_interval(days => %s)) RETURNING id""",
            (tid, qty, tid, generated_days_ago))
        po_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, warehouse, recommended_qty,
                                               final_qty, received_qty, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'approved') RETURNING id""",
            (po_id, tid, sku, supplier, wh, qty, qty, received))
        return po_id, cur.fetchone()[0]

    def accept_promise(po_id, item_id, supplier, days):
        cur.execute(
            """INSERT INTO po_confirmation_requests (tenant_id, po_log_id, supplier, token_hash, expires_at,
                                                     created_by)
               VALUES (%s, %s, %s, %s, NOW() + INTERVAL '21 days', %s) RETURNING id""",
            (tid, po_id, supplier, secrets.token_hex(32), admin))
        req = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO po_line_confirmations (tenant_id, request_id, po_log_id, po_item_id, revision,
                                                  confirmed_qty, promised_date, status, submission_id)
               VALUES (%s, %s, %s, %s, 1, 1, %s, 'changed', %s) RETURNING id""",
            (tid, req, po_id, item_id, d(days), secrets.token_hex(4)))
        conf = cur.fetchone()[0]
        cur.execute("""INSERT INTO po_confirmation_acceptances (confirmation_id, tenant_id, po_log_id, accepted_by)
                       VALUES (%s, %s, %s, %s)""", (conf, tid, po_id, admin))

    def receptions(supplier, days_list):
        for n in days_list:
            cur.execute(
                """INSERT INTO inventory_po_log (tenant_id, sku_count, total_units, reception_status)
                   VALUES (%s, 1, 1, 'received') RETURNING id""", (tid,))
            cur.execute(
                """INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
                   VALUES (%s, %s, %s, %s)""", (tid, supplier, cur.fetchone()[0], n))

    # ── the scenarios (each product is its own, so each is hand-checkable) ─
    P = lambda name: f"CF-{name}-{tag}"  # noqa: E731
    ids = {}
    stock(P("ontrack"), 500)
    ids["ontrack"] = commit(P("ontrack"), 30, 100)
    ids["nostock"] = commit(P("nostock"), 30, 100)
    stock(P("short"), 20, lead=10, declared="user")
    ids["short"] = commit(P("short"), 30, 100)
    stock(P("miss"), 20, lead=40, declared="user")
    ids["miss"] = commit(P("miss"), 30, 100)
    stock(P("leadunk"), 20)  # lead_time_days 15 is the schema default: nobody set it
    ids["leadunk"] = commit(P("leadunk"), 30, 100)
    stock(P("pass"), 10, lead=10, declared="user")
    ids["pass"] = commit(P("pass"), -3, 100)
    stock(P("prob"), 50, lead=5, declared="user")
    ids["prob"] = commit(P("prob"), 20, 100, prob=0.5)
    # two commitments, one stock
    stock(P("comp"), 100, lead=5, declared="user")
    ids["comp_a"] = commit(P("comp"), 10, 80)
    ids["comp_b"] = commit(P("comp"), 20, 80)
    # purchase orders
    card("CF Prom " + tag, 30)
    stock(P("promise"), 0, lead=5, declared="user", supplier="CF Prom " + tag)
    po_id, item = po(P("promise"), 100, "CF Prom " + tag, generated_days_ago=5)
    accept_promise(po_id, item, "CF Prom " + tag, 10)
    ids["promise"] = commit(P("promise"), 30, 100)
    card("CF Decl " + tag, 12)
    stock(P("leadpo"), 0, lead=5, declared="user", supplier="CF Decl " + tag)
    po(P("leadpo"), 40, "CF Decl " + tag, generated_days_ago=2)
    ids["leadpo"] = commit(P("leadpo"), 40, 40)
    card("CF Slow " + tag, 20)
    stock(P("tight"), 0, lead=5, declared="user", supplier="CF Slow " + tag)
    po(P("tight"), 100, "CF Slow " + tag, generated_days_ago=0)
    ids["tight"] = commit(P("tight"), 21, 100)
    card("CF Old " + tag, 10)
    stock(P("overduepo"), 10, lead=5, declared="user", supplier="CF Old " + tag)
    po(P("overduepo"), 200, "CF Old " + tag, generated_days_ago=50)
    ids["overduepo"] = commit(P("overduepo"), 30, 100)
    stock(P("nolead"), 10, lead=5, declared="user", supplier="CF Nobody " + tag)
    po(P("nolead"), 200, "CF Nobody " + tag, generated_days_ago=1)
    ids["nolead"] = commit(P("nolead"), 30, 100)
    receptions("CF Learn " + tag, [6, 7, 8])
    stock(P("learned"), 20, lead=30, declared="user", supplier="CF Learn " + tag)
    ids["learned"] = commit(P("learned"), 30, 100)
    cur.execute("""INSERT INTO inventory_transfer_log (tenant_id, from_warehouse, to_warehouse, status, created_by)
                   VALUES (%s, 'CF-Origen', 'principal', 'in_transit', %s) RETURNING id""", (tid, admin))
    tr = cur.fetchone()[0]
    cur.execute("""INSERT INTO inventory_transfer_items (tenant_id, transfer_id, sku, qty_sent, qty_received)
                   VALUES (%s, %s, %s, 100, 0)""", (tid, tr, P("transfer")))
    stock(P("transfer"), 0, lead=5, declared="user")
    ids["transfer"] = commit(P("transfer"), 5, 100)
    ids["closed"] = commit(P("ontrack"), 5, 1, status="fulfilled")

    # a warehouse-scoped analyst and two warehouses with their own stock
    wh = {}
    for name in ("CF-Norte", "CF-Sur"):
        cur.execute("""INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s)
                       ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                    (tid, name))
        wh[name] = cur.fetchone()[0]
    stock(P("scoped"), 30, wh="CF-Norte", lead=5, declared="user")
    stock(P("scoped"), 500, wh="CF-Sur", lead=5, declared="user")
    ids["scoped_norte"] = commit(P("scoped"), 30, 100, wh_id=wh["CF-Norte"])
    ids["scoped_sur"] = commit(P("scoped"), 31, 100, wh_id=wh["CF-Sur"])
    r = http(args.python, "POST", f"{API}/users", token=auth_for(fx, "admin"), body={
        "email": f"contract-{tag}-cfscoped@stockai.demo", "role": "analyst", "full_name": "CF scoped"})
    scoped_ok = r.status == 201
    if scoped_ok:
        uid = r.body["data"]["user"]["id"]
        r = http(args.python, "PUT", f"{API}/users/{uid}/warehouse-scope", token=auth_for(fx, "admin"),
                 body={"warehouse_ids": [wh["CF-Norte"]]})
        scoped_ok = r.status == 200
        fx.tokens["cf_scoped"] = ns.mint_access_token(fx.secret, uid, tid, "analyst")

    def snapshot():
        cur.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0), COUNT(*) FILTER (WHERE status = 'open') "
                    "FROM committed_demand WHERE tenant_id = %s", (tid,))
        a = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s", (tid,))
        return a, cur.fetchone()[0]

    # ── checking helpers ───────────────────────────────────────────────────
    def record(name, route, problems, method="GET", path="", who="admin"):
        out.append((Case(name, method, path, who=who, route=route),
                    "FAIL" if problems else "PASS", problems))

    def call(path, who="admin"):
        return http(args.rust, "GET", path, token=auth_for(fx, who))

    cd = f"{API}/committed-demand"
    full = call(f"{cd}/outlook?limit=2000")
    items = {}
    if full.status == 200:
        items = {i["id"]: i for i in full.body["data"]["items"]}
    record("cf list answers 200 with every open row and no closed one", OUTLOOK,
           [] if full.status == 200 and ids["closed"] not in items and ids["ontrack"] in items
           else [f"status {full.status}, closed row present: {ids['closed'] in items}"])

    def expect(name, key, **want):
        it = items.get(ids[key])
        problems = []
        if it is None:
            problems.append("row missing from the list")
        else:
            for field_name, value in want.items():
                got = it.get(field_name)
                if field_name == "reason":
                    got = it["reason"]["code"]
                if got != value:
                    problems.append(f"{field_name}: want {value!r}, got {got!r}")
        record(name, OUTLOOK, problems)

    expect("cf stock covers: on_track covered_by_stock", "ontrack", verdict="on_track",
           reason="covered_by_stock", shortfall_units=0.0, cover_date=d(0), cover_source="stock")
    expect("cf no stock row: insufficient_data, shortfall null (never zero)", "nostock",
           verdict="insufficient_data", reason="no_stock_figure", shortfall_units=None, stock=None)
    expect("cf short with time to order: at_risk order_in_time", "short", verdict="at_risk",
           reason="order_in_time", shortfall_units=80.0, latest_safe_order_date=d(20),
           order_date_passed=False, lead_time_days=10, lead_time_source="sku",
           cover_date=d(10), cover_source="new_order")
    expect("cf short, order date gone: will_miss order_too_late", "miss", verdict="will_miss",
           reason="order_too_late", shortfall_units=80.0, order_date_passed=True, late_days=10,
           cover_date=d(40))
    expect("cf short and no declared lead time: insufficient_data, shortfall kept", "leadunk",
           verdict="insufficient_data", reason="lead_time_unknown", shortfall_units=80.0,
           lead_time_days=None)
    expect("cf delivery already passed and short: will_miss delivery_passed", "pass",
           verdict="will_miss", reason="delivery_passed", shortfall_units=90.0, overdue=True)
    expect("cf probability scales the units", "prob", verdict="on_track", expected_units=50.0)
    expect("cf earlier commitment gets the stock first", "comp_a", verdict="on_track",
           cumulative_units=80.0, shortfall_units=0.0)
    expect("cf later commitment is short by the overlap", "comp_b", verdict="at_risk",
           cumulative_units=160.0, shortfall_units=60.0, reason="order_in_time")
    expect("cf supplier-promised date drives the arrival", "promise", verdict="on_track",
           reason="covered_by_arrivals", cover_date=d(10), cover_source="purchase_order")
    expect("cf supplier lead time dates an open order", "leadpo", verdict="on_track",
           reason="covered_by_arrivals", cover_date=d(10))
    expect("cf order landing 1 day before delivery is at_risk covered_tight", "tight",
           verdict="at_risk", reason="covered_tight", cover_date=d(20), shortfall_units=0.0)
    expect("cf overdue order is undated: insufficient_data unknown_arrival", "overduepo",
           verdict="insufficient_data", reason="unknown_arrival", shortfall_units=None,
           undated_units=200.0)
    expect("cf order from a supplier with no lead time is undated", "nolead",
           verdict="insufficient_data", reason="unknown_arrival", undated_units=200.0)
    expect("cf lead time learned from receptions beats the SKU's", "learned", verdict="at_risk",
           lead_time_days=7, lead_time_source="learned", latest_safe_order_date=d(23))
    expect("cf transfer in transit counts as available now", "transfer", verdict="on_track",
           reason="covered_by_arrivals", cover_date=d(0))

    # The Python committed-demand list owns the at-risk flag and the safe order
    # date where no purchase order is involved: the outlook must agree.
    py = http(args.python, "GET", f"{cd}?status=open&limit=2000", token=auth_for(fx, "admin"))
    problems = []
    if py.status != 200:
        problems.append(f"python list answered {py.status}")
    else:
        pyi = {i["id"]: i for i in py.body["data"]["items"]}
        for key in ("short", "miss", "comp_a", "comp_b", "prob", "learned", "ontrack"):
            a, b = pyi[ids[key]], items[ids[key]]
            if a["at_risk"] != (b["shortfall_units"] > 0):
                problems.append(f"{key}: python at_risk={a['at_risk']} outlook shortfall={b['shortfall_units']}")
            if a["at_risk"] and round(a["shortfall"], 2) != b["shortfall_units"]:
                problems.append(f"{key}: python shortfall={a['shortfall']} outlook={b['shortfall_units']}")
            if a["at_risk"] and a["latest_safe_order_date"] != b["latest_safe_order_date"]:
                problems.append(f"{key}: python safe date={a['latest_safe_order_date']} "
                                f"outlook={b['latest_safe_order_date']}")
    record("cf agrees with python's at-risk flag, shortfall and safe date (no purchase orders)",
           OUTLOOK, problems)

    # ── filters and validation ─────────────────────────────────────────────
    r = call(f"{cd}/outlook?verdict=will_miss&limit=2000")
    got = {i["verdict"] for i in r.body["data"]["items"]} if r.status == 200 else {"?"}
    record("cf filter verdict=will_miss", OUTLOOK,
           [] if got == {"will_miss"} and r.body["data"]["summary"]["total"] == len(r.body["data"]["items"])
           else [f"verdicts {got}"])
    r = call(f"{cd}/outlook?sku={P('comp')}")
    record("cf filter sku", OUTLOOK,
           [] if r.status == 200 and sorted(i["id"] for i in r.body["data"]["items"])
           == sorted([ids["comp_a"], ids["comp_b"]]) else [f"{r.status} {r.body}"])
    r = call(f"{cd}/outlook?customer=ACME&sku={P('ontrack')}")
    record("cf filter customer is case-insensitive", OUTLOOK,
           [] if r.status == 200 and r.body["data"]["total"] == 1 else [f"{r.status} {r.body}"])
    r = call(f"{cd}/outlook?sku={P('comp')}&delivery_from={d(15)}")
    record("cf filter delivery_from keeps only the later row", OUTLOOK,
           [] if r.status == 200 and [i["id"] for i in r.body["data"]["items"]] == [ids["comp_b"]]
           else [f"{r.status} {r.body}"])
    # the competing set is NOT narrowed by a filter: comp_b stays short by 60
    problems = []
    if r.status == 200 and r.body["data"]["items"][0]["shortfall_units"] != 60.0:
        problems.append(f"filtered row lost its competitor: {r.body['data']['items'][0]['shortfall_units']}")
    record("cf a filter does not change who competes for the stock", OUTLOOK, problems)
    for name, qs in (("bad verdict", "verdict=maybe"), ("limit 0", "limit=0"), ("limit 2001", "limit=2001"),
                     ("bad date", "delivery_from=nope")):
        r = call(f"{cd}/outlook?{qs}")
        record(f"cf 422 on {name}", OUTLOOK, [] if r.status == 422 else [f"status {r.status}"])
    r = call(f"{cd}/outlook?limit=3")
    record("cf limit caps the items but total counts all", OUTLOOK,
           [] if r.status == 200 and len(r.body["data"]["items"]) == 3 and r.body["data"]["total"] > 3
           else [f"{r.status}"])

    # ── summary ────────────────────────────────────────────────────────────
    r = call(f"{cd}/outlook/summary")
    problems = []
    if r.status != 200:
        problems.append(f"status {r.status}")
    else:
        s = r.body["data"]["summary"]
        counts = {}
        for it in items.values():
            counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
        for v in ("on_track", "at_risk", "will_miss", "insufficient_data"):
            if s[v] != counts.get(v, 0):
                problems.append(f"{v}: summary {s[v]} vs list {counts.get(v, 0)}")
        if s["total"] != len(items):
            problems.append(f"total {s['total']} vs {len(items)}")
        gaps = {g["reason"] for g in r.body["data"]["data_gaps"]}
        if not {"no_stock_figure", "lead_time_unknown", "unknown_arrival"} <= gaps:
            problems.append(f"data_gaps {gaps}")
        if not any(c["customer"] == "Acme" for c in r.body["data"]["by_customer"]):
            problems.append("by_customer misses Acme")
    record("cf summary agrees with the list and names the data gaps", SUMMARY, problems)

    # ── detail ─────────────────────────────────────────────────────────────
    r = call(f"{cd}/{ids['comp_b']}/outlook")
    problems = []
    if r.status != 200:
        problems.append(f"status {r.status}")
    else:
        dd = r.body["data"]
        if [c["id"] for c in dd["competing"]] != [ids["comp_a"], ids["comp_b"]] or not dd["competing"][1]["is_this"]:
            problems.append(f"competing {dd['competing']}")
        if dd["supply"]["stock"] != 100.0 or dd["supply"]["lead_time_days"] != 5:
            problems.append(f"supply {dd['supply']}")
    record("cf detail lists the commitments competing for the stock", DETAIL, problems)
    r = call(f"{cd}/{ids['promise']}/outlook")
    problems = []
    if r.status == 200:
        arr = r.body["data"]["supply"]["arrivals"]
        if len(arr) != 1 or arr[0]["source"] != "supplier_promise" or arr[0]["date"] != d(10) or not arr[0]["counted"]:
            problems.append(f"arrivals {arr}")
    else:
        problems.append(f"status {r.status}")
    record("cf detail shows the promised date and that it counts", DETAIL, problems)
    r = call(f"{cd}/{ids['overduepo']}/outlook")
    problems = []
    if r.status == 200:
        arr = r.body["data"]["supply"]["arrivals"]
        if len(arr) != 1 or arr[0]["source"] != "overdue" or arr[0]["date"] is not None or arr[0]["counted"]:
            problems.append(f"arrivals {arr}")
    else:
        problems.append(f"status {r.status}")
    record("cf detail shows an overdue order as undated and not counted", DETAIL, problems)
    r = call(f"{cd}/{ids['closed']}/outlook")
    record("cf detail of a closed commitment is 409", DETAIL,
           [] if r.status == 409 and r.body.get("error_code") == "commitment_outlook_not_open"
           else [f"{r.status} {r.body}"])
    r = call(f"{cd}/does-not-exist/outlook")
    record("cf detail of an unknown id is 404", DETAIL,
           [] if r.status == 404 and r.body.get("error_code") == "committed_demand_not_found"
           else [f"{r.status} {r.body}"])

    # ── permissions: reads, so the pair is allowed / refused, and nothing is written
    before = snapshot()
    for who, want in (("viewer", 200), ("analyst", 200), ("admin", 200), ("none", 401)):
        for route, path in ((OUTLOOK, f"{cd}/outlook"), (SUMMARY, f"{cd}/outlook/summary"),
                            (DETAIL, f"{cd}/{ids['short']}/outlook")):
            r = call(path, who)
            record(f"cf {who} -> {want} on {route.split(' ', 1)[1]}", route,
                   [] if r.status == want else [f"status {r.status} {r.body}"], who=who)
    for key in ("key_read", "key_write"):
        if fx.token(key) is None:
            continue
        py_r = http(args.python, "GET", f"{cd}?limit=1", token=fx.token(key))
        rs_r = call(f"{cd}/outlook", key)
        record(f"cf {key} is refused like python refuses it on the internal committed-demand tag", OUTLOOK,
               [] if (py_r.status, (py_r.body or {}).get("error_code")) == (rs_r.status, (rs_r.body or {}).get("error_code"))
               else [f"python {py_r.status} {py_r.body}, rust {rs_r.status} {rs_r.body}"], who=key)
    after = snapshot()
    record("cf the reads wrote nothing (rows, quantities, activity log unchanged)", OUTLOOK,
           [] if before == after else [f"{before} -> {after}"])

    # ── warehouse scope ────────────────────────────────────────────────────
    if not scoped_ok:
        record("cf warehouse scope setup", OUTLOOK, ["could not create the scoped analyst"])
    else:
        r = call(f"{cd}/outlook?limit=2000", "cf_scoped")
        problems = []
        if r.status != 200:
            problems.append(f"status {r.status}")
        else:
            got = {i["id"]: i for i in r.body["data"]["items"]}
            if set(got) != {ids["scoped_norte"]}:
                problems.append(f"scoped caller sees {len(got)} rows, want only their own warehouse's")
            if r.body["data"]["scope"] != "warehouses":
                problems.append("scope label is not 'warehouses'")
            it = got.get(ids["scoped_norte"])
            # Norte holds 30; the company holds 530. The verdict must use Norte's stock only.
            if it and (it["stock"] != 30.0 or it["shortfall_units"] != 70.0):
                problems.append(f"verdict leaned on stock outside the scope: stock={it['stock']} short={it['shortfall_units']}")
        record("cf scoped caller sees only own rows, judged on own stock", OUTLOOK, problems, who="cf_scoped")
        r = call(f"{cd}/{ids['scoped_sur']}/outlook", "cf_scoped")
        record("cf scoped caller gets 404 for another warehouse's commitment", DETAIL,
               [] if r.status == 404 else [f"status {r.status}"], who="cf_scoped")
        r = call(f"{cd}/{ids['ontrack']}/outlook", "cf_scoped")
        record("cf scoped caller gets 404 for a company-wide commitment", DETAIL,
               [] if r.status == 404 else [f"status {r.status}"], who="cf_scoped")
        r = call(f"{cd}/outlook/summary", "cf_scoped")
        record("cf scoped summary covers their rows only", SUMMARY,
               [] if r.status == 200 and r.body["data"]["summary"]["total"] == 1 else [f"{r.status} {r.body}"],
               who="cf_scoped")
        cur.execute("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (uid,))
    return out
