"""Contract cases for money at risk on the commitment outlook (Rust-only routes).

Same method as cf_outlook_cases.py: no Python twin, so each case seeds a
hand-computed scenario in the database and asserts the exact figures the Rust API
must answer (money as exact decimal strings), including what must NOT be
counted: a missing or zero price is excluded and counted, never a 0 or an
invented price.

Called from contract_test.py's `run` with the harness module as `ns`.
"""

from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from decimal import Decimal

OUTLOOK = "GET /committed-demand/outlook"
SUMMARY = "GET /committed-demand/outlook/summary"
DETAIL = "GET /committed-demand/{id}/outlook"


def run_money(args, fx, db, ns) -> list:
    Case, http, auth_for, API = ns.Case, ns.http, ns.auth_for, ns.API
    out: list = []
    if db is None:
        return [(Case("money (all)", "-", "-", route=OUTLOOK), "SKIP",
                 ["needs --db: the scenarios are seeded in the database"])]
    cur = db.cursor()
    tag = secrets.token_hex(3)
    today = date.today()
    tid, admin = fx.tenant_id, fx.admin_id
    cust, cust_c, cust_s = f"MR-{tag}", f"MRC-{tag}", f"MRS-{tag}"

    def d(days):
        return (today + timedelta(days=days)).isoformat()

    def stock(sku, qty, price=None, cost=None, wh="principal", lead=40, supplier=None):
        cur.execute(
            """INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, lead_time_days,
                                            lead_time_set_by, supplier, sale_price, unit_cost)
               VALUES (%s, %s, %s, %s, %s, 'user', %s, %s, %s)""",
            (tid, sku, wh, qty, lead, supplier, price, cost))

    def commit(sku, days, qty, customer, wh_id=None, contract=None):
        cur.execute(
            """INSERT INTO committed_demand (tenant_id, sku, warehouse_id, delivery_date, quantity, customer,
                                             probability, status, created_by, source, contract_id,
                                             contract_root_id)
               VALUES (%s, %s, %s, %s, %s, %s, 1, 'open', %s, %s, %s, %s) RETURNING id""",
            (tid, sku, wh_id, d(days), qty, customer, admin,
             "contract" if contract else "manual", contract, contract))
        return cur.fetchone()[0]

    P = lambda n: f"MR-{n}-{tag}"  # noqa: E731
    ids = {}
    # ── plain SKU prices; every SKU is 20 in stock with a 40-day lead time and a
    # commitment of 100 in 30 days, so the shortfall is 80 and the verdict will_miss.
    stock(P("full"), 20, price=12.5, cost=8.0)
    ids["full"] = commit(P("full"), 30, 100, cust)                          # 80 x 12.50 = 1000.00, margin 360.00
    stock(P("nocost"), 20, price=3.333, lead=10)
    ids["nocost"] = commit(P("nocost"), 30, 100, cust)                      # at_risk, 80 x 3.3330 = 266.64
    stock(P("noprice"), 20, price=None, cost=2.0)
    ids["noprice"] = commit(P("noprice"), 30, 100, cust)                    # excluded, never 0
    stock(P("zero"), 20, price=0.0, cost=2.0)
    ids["zero"] = commit(P("zero"), 30, 100, cust)                          # a zero is not a price
    stock(P("min"), 10, price=2.0, cost=1.5, lead=5)
    cur.execute("""INSERT INTO inventory_po_log (tenant_id, sku_count, total_units, po_number, reception_status,
                       generated_at)
                   VALUES (%s, 1, 30, (SELECT COALESCE(MAX(po_number), 0) + 1 FROM inventory_po_log
                                       WHERE tenant_id = %s), 'pending', NOW()) RETURNING id""", (tid, tid))
    po_id = cur.fetchone()[0]
    cur.execute("""INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, warehouse,
                       recommended_qty, final_qty, status)
                   VALUES (%s, %s, %s, %s, 'principal', 30, 30, 'approved')""",
                (po_id, tid, P("min"), "MR Nobody " + tag))
    ids["min"] = commit(P("min"), 30, 100, cust)                            # undated 30 units: minimum 60 x 2 = 120.00
    stock(P("ok"), 500, price=9.0, cost=1.0)
    ids["ok"] = commit(P("ok"), 30, 100, cust)                              # on_track: no money
    ids["nostock"] = commit(P("nostock"), 30, 100, cust)                    # insufficient_data: no money

    # ── contract prices (one contract, three lines)
    root = f"mr-contract-{tag}"
    lines = [
        {"sku": P("cp"), "total_quantity": 100, "unit_price": 9.0},
        {"sku": P("cn"), "total_quantity": 100, "unit_price": None},
        {"sku": P("cc"), "total_quantity": 50, "unit_price": 1.0},
        {"sku": P("cc"), "total_quantity": 50, "unit_price": 2.0},
    ]
    cur.execute(
        """INSERT INTO supply_contracts (id, tenant_id, root_id, revision, customer, reference, lines,
                                         period_start, period_end, schedule_kind, status, created_by)
           VALUES (%s, %s, %s, 1, %s, %s, %s::jsonb, %s, %s, 'monthly', 'active', %s)""",
        (root, tid, root, cust_c, f"MR-REF-{tag}", json.dumps(lines), d(-5), d(200), admin))
    stock(P("cp"), 20, price=12.0, cost=5.0)
    ids["cp"] = commit(P("cp"), 30, 100, cust_c, contract=root)             # contract 9.00 beats SKU 12.00: 720.00, margin 320.00
    stock(P("cn"), 20, price=4.0)
    ids["cn"] = commit(P("cn"), 30, 100, cust_c, contract=root)             # line has no price: SKU 4.00 -> 320.00
    stock(P("cc"), 20, price=7.0)
    ids["cc"] = commit(P("cc"), 30, 100, cust_c, contract=root)             # two prices: not available, not SKU 7.00

    # ── warehouse scope: the price comes from the caller's own warehouse row
    wh = {}
    for key, name in (("a", f"MR-Aaa-{tag}"), ("z", f"MR-Zzz-{tag}")):
        cur.execute("""INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s)
                       ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                    (tid, name))
        wh[key] = (cur.fetchone()[0], name)
    stock(P("scope"), 20, price=99.0, wh=wh["a"][1])
    stock(P("scope"), 20, price=10.0, wh=wh["z"][1])
    ids["scope"] = commit(P("scope"), 30, 100, cust_s, wh_id=wh["z"][0])
    r = http(args.python, "POST", f"{API}/users", token=auth_for(fx, "admin"), body={
        "email": f"contract-{tag}-mrscoped@stockai.demo", "role": "analyst", "full_name": "MR scoped"})
    scoped_ok = r.status == 201
    if scoped_ok:
        uid = r.body["data"]["user"]["id"]
        r = http(args.python, "PUT", f"{API}/users/{uid}/warehouse-scope", token=auth_for(fx, "admin"),
                 body={"warehouse_ids": [wh["z"][0]]})
        scoped_ok = r.status == 200
        fx.tokens["mr_scoped"] = ns.mint_access_token(fx.secret, uid, tid, "analyst")

    def snapshot():
        cur.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM committed_demand WHERE tenant_id = %s", (tid,))
        a = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s", (tid,))
        n_log = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), COALESCE(SUM(sale_price), 0) FROM inventory_stock WHERE tenant_id = %s", (tid,))
        return a, n_log, cur.fetchone()

    def record(name, route, problems, who="admin"):
        out.append((Case(name, "GET", "", who=who, route=route), "FAIL" if problems else "PASS", problems))

    def call(path, who="admin"):
        return http(args.rust, "GET", path, token=auth_for(fx, who))

    cd = f"{API}/committed-demand"
    full = call(f"{cd}/outlook?limit=2000")
    items = {i["id"]: i for i in full.body["data"]["items"]} if full.status == 200 else {}
    record("money list answers 200 and names the tenant currency", OUTLOOK,
           [] if full.status == 200 and full.body["data"]["currency"]["code"] else [f"{full.status}"])
    if full.status == 200:
        cur.execute("SELECT settings FROM tenants WHERE id = %s", (tid,))
        stored = (cur.fetchone()[0] or {}).get("currency") or "CRC"
        got = full.body["data"]["currency"]["code"]
        record("money currency is the tenant's base currency", OUTLOOK,
               [] if got == stored else [f"tenant {stored}, answer {got}"])

    def expect(name, key, **want):
        m = (items.get(ids[key]) or {}).get("money")
        problems = []
        if m is None:
            problems.append("row has no money object")
        else:
            for k, v in want.items():
                if m.get(k) != v:
                    problems.append(f"{k}: want {v!r}, got {m.get(k)!r}")
        record(name, OUTLOOK, problems)

    expect("money will_miss: shortfall x SKU price, margin from the cost", "full", status="computed",
           amount_at_risk="1000.00", margin_at_risk="360.00", margin_status="computed",
           unit_price="12.5000", price_source="sku", unit_cost="8.0000", is_minimum=False)
    expect("money at_risk without a cost: amount counts, margin not available", "nocost", status="computed",
           amount_at_risk="266.64", margin_at_risk=None, margin_status="no_cost", unit_cost=None)
    expect("money no price: not available, never 0", "noprice", status="no_price", amount_at_risk=None,
           margin_at_risk=None, unit_price=None, price_source=None)
    expect("money a zero price is not a price", "zero", status="no_price", amount_at_risk=None, unit_price=None)
    expect("money a minimum shortfall makes a minimum amount", "min", status="computed",
           amount_at_risk="120.00", margin_at_risk="30.00", is_minimum=True)
    expect("money on_track has no money", "ok", status="not_applicable", amount_at_risk=None)
    expect("money insufficient_data has no money", "nostock", status="not_applicable", amount_at_risk=None)
    expect("money contract price beats the SKU price", "cp", status="computed", amount_at_risk="720.00",
           margin_at_risk="320.00", unit_price="9.0000", price_source="contract")
    expect("money a contract line with no price falls back to the SKU price", "cn", status="computed",
           amount_at_risk="320.00", unit_price="4.0000", price_source="sku", margin_status="no_cost")
    expect("money two prices for one SKU on a contract: not available, SKU price not used", "cc",
           status="no_price", amount_at_risk=None, unit_price=None)

    # ── roll-ups by customer, by contract, tenant
    def money_of(entry_summary):
        return entry_summary["money"]

    r = call(f"{cd}/outlook/summary")
    problems = []
    if r.status != 200:
        problems.append(f"status {r.status}")
    else:
        data = r.body["data"]
        by_c = {c["customer"]: money_of(c["summary"]) for c in data["by_customer"]}
        want = {"eligible": 5, "computed": 3, "excluded_no_price": 2, "excluded_no_shortfall": 0,
                "amount_at_risk": "1386.64", "has_minimum": True, "margin_rows": 2,
                "margin_at_risk": "390.00", "margin_excluded": 3}
        for k, v in want.items():
            if by_c.get(cust, {}).get(k) != v:
                problems.append(f"customer {k}: want {v!r}, got {by_c.get(cust, {}).get(k)!r}")
        contract = next((c for c in data["by_contract"] if c["contract_root_id"] == root), None)
        wantc = {"eligible": 3, "computed": 2, "excluded_no_price": 1, "amount_at_risk": "1040.00",
                 "has_minimum": False, "margin_rows": 1, "margin_at_risk": "320.00", "margin_excluded": 2}
        for k, v in wantc.items():
            if not contract or money_of(contract["summary"]).get(k) != v:
                problems.append(f"contract {k}: want {v!r}, got {contract and money_of(contract['summary']).get(k)!r}")
        # The tenant total is the exact sum of the rows' cents and counts every exclusion.
        elig = [i for i in items.values() if i["verdict"] in ("at_risk", "will_miss")]
        tot = money_of(data["summary"])
        amount = sum((Decimal(i["money"]["amount_at_risk"]) for i in elig if i["money"]["status"] == "computed"),
                     Decimal(0))
        margin = sum((Decimal(i["money"]["margin_at_risk"]) for i in elig if i["money"]["margin_at_risk"] is not None),
                     Decimal(0))
        if Decimal(tot["amount_at_risk"]) != amount:
            problems.append(f"tenant amount {tot['amount_at_risk']} != sum of rows {amount}")
        if Decimal(tot["margin_at_risk"]) != margin:
            problems.append(f"tenant margin {tot['margin_at_risk']} != sum of rows {margin}")
        if tot["eligible"] != len(elig) or tot["excluded_no_price"] != sum(
                1 for i in elig if i["money"]["status"] == "no_price"):
            problems.append(f"tenant counts {tot} vs {len(elig)} eligible rows")
        if sum(Decimal(c["amount_at_risk"]) for c in by_c.values()) != amount:
            problems.append("customer amounts do not add up to the tenant total")
        if "currency" not in data:
            problems.append("summary has no currency")
    record("money roll-ups by customer, by contract and tenant, with the exclusion counts", SUMMARY, problems)

    r = call(f"{cd}/outlook?customer={cust}&limit=2000")
    record("money list summary follows the filter", OUTLOOK,
           [] if r.status == 200 and r.body["data"]["summary"]["money"]["amount_at_risk"] == "1386.64"
           and r.body["data"]["summary"]["money"]["excluded_no_price"] == 2 else [f"{r.status} {r.body}"])
    r = call(f"{cd}/outlook?customer={cust}&verdict=on_track")
    record("money a filter with no at-risk rows totals a true zero with nothing excluded", OUTLOOK,
           [] if r.status == 200 and r.body["data"]["summary"]["money"]["eligible"] == 0
           and r.body["data"]["summary"]["money"]["amount_at_risk"] == "0.00" else [f"{r.status} {r.body}"])

    r = call(f"{cd}/{ids['full']}/outlook")
    record("money on the detail route", DETAIL,
           [] if r.status == 200 and r.body["data"]["commitment"]["money"]["amount_at_risk"] == "1000.00"
           and r.body["data"]["currency"]["code"] else [f"{r.status}"])

    # ── permissions (reads) and nothing written
    before = snapshot()
    for who, want in (("viewer", 200), ("analyst", 200), ("none", 401)):
        r = call(f"{cd}/outlook?customer={cust}", who)
        got = (r.body or {}).get("data", {}).get("summary", {}).get("money", {}).get("amount_at_risk") \
            if r.status == 200 else None
        record(f"money {who} -> {want} on the outlook", OUTLOOK,
               [] if r.status == want and (want != 200 or got == "1386.64") else [f"status {r.status} amount {got}"],
               who=who)
    after = snapshot()
    record("money the reads wrote nothing (rows, quantities, prices)", OUTLOOK,
           [] if before == after else [f"{before} -> {after}"])

    # ── warehouse scope
    if not scoped_ok:
        record("money warehouse scope setup", OUTLOOK, ["could not create the scoped analyst"])
    else:
        a = call(f"{cd}/outlook?customer={cust_s}")
        s = call(f"{cd}/outlook?customer={cust_s}", "mr_scoped")
        problems = []
        if a.status != 200 or s.status != 200 or not a.body["data"]["items"] or not s.body["data"]["items"]:
            problems.append(f"statuses {a.status}/{s.status}")
        else:
            ma, ms = a.body["data"]["items"][0]["money"], s.body["data"]["items"][0]["money"]
            # Company view: stock 40 -> short 60, representative warehouse Aaa (99.00).
            if (ma["amount_at_risk"], ma["unit_price"]) != ("5940.00", "99.0000"):
                problems.append(f"company view {ma}")
            # Scoped view: stock 20 -> short 80, only the caller's warehouse price (10.00).
            if (ms["amount_at_risk"], ms["unit_price"]) != ("800.00", "10.0000"):
                problems.append(f"scoped view {ms}")
        record("money a warehouse-scoped caller is priced from their own warehouse only", OUTLOOK, problems,
               who="mr_scoped")
    return out
