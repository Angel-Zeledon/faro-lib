"""Wave 3b contract cases: PO reception, transfers, shrinkage.

Imported by `w3_cases` (`build_cases_3b`, `seed_pos_3b`); kept in its own file
so the first group's file stays small.
"""

from __future__ import annotations

API = "/api/v1"


def seed_pos_3b(db, side, seed_po) -> None:
    v = side.vars
    cur = db.cursor()
    v["po_all"] = seed_po(db, side, 2001, destination="principal", sent=True, lines=[
        ("A1", "principal", None, "approved", "Acme", 20), ("A2", "principal", None, "modified", "ACME ", 10),
        ("P-001", "principal", None, "rejected", "Beta", 4)])
    v["po_part"] = seed_po(db, side, 2002, destination="principal", lines=[
        ("A1", "principal", None, "approved", "Acme", 20), ("P-002", "principal", None, "approved", "Beta", 5)])
    v["po_multi_wh"] = seed_po(db, side, 2003, destination="principal", lines=[
        ("A1", "principal", None, "approved", "Acme", 6), ("A1", "Norte", None, "approved", "Acme", 6)])
    v["po_newrow"] = seed_po(db, side, 2004, destination="Norte", lines=[
        ("NEWSKU", "", None, "approved", "Zeta", 7), ("NEW2", "Este", None, "approved", "Zeta", 3)])
    v["po_cancel"] = seed_po(db, side, 2005, destination="principal",
                             lines=[("A1", "principal", None, "approved", "Acme", 3)])
    cur.execute("UPDATE inventory_po_log SET cancelled_at = NOW() WHERE id = %s", (v["po_cancel"],))
    v["po_dates"] = seed_po(db, side, 2006, destination="principal",
                            lines=[("A1", "principal", None, "approved", "Acme", 1000)])
    v["po_empty"] = seed_po(db, side, 2007, destination="principal",
                            lines=[("A1", "principal", None, "rejected", "Acme", 3)])
    cur.execute("INSERT INTO transfer_lanes (tenant_id, from_warehouse, to_warehouse, lead_time_days) "
                "VALUES (%s, 'principal', 'Norte', 4)", (side.fx.tenant_id,))


def build_cases_3b(C) -> list:
    cs: list = []
    add = cs.append
    P = f"{API}/inventory/po"
    T = f"{API}/inventory/transfers"
    H = f"{API}/inventory/shrinkage"

    r = "GET /inventory/po/{id}/items"
    add(C("items pending", "GET", f"{P}/{{po_all}}/items", route=r))
    add(C("items viewer", "GET", f"{P}/{{po_all}}/items", who="viewer", route=r))
    add(C("items not found", "GET", f"{P}/nope/items", route=r))
    add(C("items foreign", "GET", f"{P}/{{foreign_po}}/items", route=r))
    add(C("items no auth", "GET", f"{P}/{{po_all}}/items", who="none", route=r))
    add(C("items scoped out of scope", "GET", f"{P}/{{po_all}}/items", who="analyst", scope=["Norte"], route=r))

    r = "POST /inventory/po/{id}/receive"

    def R(k):
        return f"{P}/{{{k}}}/receive"

    add(C("receive viewer denied", "POST", R("po_all"), who="viewer", route=r))
    add(C("receive no auth", "POST", R("po_all"), who="none", route=r))
    add(C("receive read key refused", "POST", R("po_all"), who="key_read", route=r))
    add(C("receive not found", "POST", f"{P}/nope/receive", route=r))
    add(C("receive foreign", "POST", R("foreign_po"), route=r))
    add(C("receive scoped out of scope", "POST", R("po_all"), who="analyst", scope=["Norte"], route=r))
    add(C("receive cancelled order", "POST", R("po_cancel"), route=r))
    add(C("receive no ordered lines", "POST", R("po_empty"), route=r))
    add(C("receive invalid json", "POST", R("po_all"), raw=b"{nope", route=r, vol={"ctx", "loc"}))
    add(C("receive body not an object", "POST", R("po_all"), body=[1], route=r))
    add(C("receive lines wrong shape", "POST", R("po_all"), body={"lines": "x", "received_at": 5}, route=r))
    add(C("receive line validation", "POST", R("po_all"),
          body={"lines": [{"sku": 1, "received_qty": -1}, 5, {}]}, route=r))
    add(C("receive bad date", "POST", R("po_all"), body={"received_at": "nope"}, route=r))
    add(C("receive date before the order", "POST", R("po_all"), body={"received_at": "2020-01-01"}, route=r))
    add(C("receive sku not in order", "POST", R("po_all"),
          body={"lines": [{"sku": "NOPE", "received_qty": 1}]}, route=r))
    add(C("receive rejected line sku", "POST", R("po_all"),
          body={"lines": [{"sku": "P-001", "received_qty": 1}]}, route=r))
    add(C("receive over pending", "POST", R("po_all"),
          body={"lines": [{"sku": "A1", "received_qty": 20.5}]}, route=r))
    add(C("receive over pending big", "POST", R("po_all"),
          body={"lines": [{"sku": "A1", "received_qty": 1234567.5}]}, route=r))
    add(C("receive sku in two warehouses", "POST", R("po_multi_wh"),
          body={"lines": [{"sku": "A1", "received_qty": 1}]}, route=r))
    add(C("receive empty lines is not_received", "POST", R("po_part"), body={"lines": []}, route=r))
    add(C("receive partial", "POST", R("po_part"), who="analyst",
          body={"lines": [{"sku": "A1", "received_qty": 7.5}], "received_at": "2026-10-03"}, route=r))
    add(C("receive partial again", "POST", R("po_part"),
          body={"lines": [{"sku": "A1", "received_qty": 12.5}, {"sku": "P-002", "received_qty": 5}],
                "received_at": "2026-10-04T12:30:00-05:00"}, route=r))
    add(C("receive complete order again", "POST", R("po_part"), route=r))
    add(C("receive everything, two spellings of one supplier", "POST", R("po_all"), who="analyst", route=r))
    add(C("receive creates rows and a warehouse", "POST", R("po_newrow"),
          body={"received_at": "2026-10-05T08:00:00Z"}, route=r))
    add(C("receive all in a second order", "POST", R("po_multi_wh"),
          body={"received_at": "20261005T101500.5+0530"}, route=r))
    add(C("receive week date", "POST", R("po_dates"),
          body={"lines": [{"sku": "A1", "received_qty": 1}], "received_at": "2026-W40-3"}, route=r))
    add(C("receive naive datetime with micros", "POST", R("po_dates"),
          body={"lines": [{"sku": "A1", "received_qty": 2}], "received_at": "2026-10-05 10:15:30.123456789"},
          route=r))
    add(C("receive text quantity", "POST", R("po_dates"),
          body={"lines": [{"sku": "A1", "received_qty": "3.5"}]}, route=r))
    add(C("receive empty received_at", "POST", R("po_dates"),
          body={"lines": [{"sku": "A1", "received_qty": 1}], "received_at": ""}, route=r))

    # ── transfers ───────────────────────────────────────────────────────────
    r = "POST /inventory/transfers"
    ok_items = [{"sku": "A1", "qty": 1}]
    add(C("transfer create", "POST", T, who="analyst", route=r, save={"tr1": "data.id"},
          body={"from_warehouse": "principal", "to_warehouse": "Norte",
                "items": [{"sku": "A1", "qty": 5}, {"sku": "A2", "qty": 2.5}, {"sku": "A1", "qty": 1}],
                "notes": "w3 transfer"}))
    add(C("transfer create other lane", "POST", T, route=r, save={"tr2": "data.id"},
          body={"from_warehouse": "Norte", "to_warehouse": "Sur",
                "items": [{"sku": "M1", "qty": 3}, {"sku": "A1", "qty": 10}]}))
    add(C("transfer create for the loss", "POST", T, route=r, save={"tr3": "data.id"},
          body={"from_warehouse": "Sur", "to_warehouse": "principal",
                "items": [{"sku": "SUR1", "qty": 6}, {"sku": "P-003", "qty": 4}]}))
    add(C("transfer create insufficient", "POST", T, route=r,
          body={"from_warehouse": "principal", "to_warehouse": "Norte", "items": [{"sku": "A2", "qty": 1234567}]}))
    add(C("transfer create sku not at origin", "POST", T, route=r,
          body={"from_warehouse": "principal", "to_warehouse": "Norte", "items": [{"sku": "B1", "qty": 1}]}))
    add(C("transfer create same warehouse", "POST", T, route=r,
          body={"from_warehouse": "Norte", "to_warehouse": " Norte ", "items": ok_items}))
    add(C("transfer create unknown warehouse", "POST", T, route=r,
          body={"from_warehouse": "Nowhere", "to_warehouse": "Norte", "items": ok_items}))
    add(C("transfer create unknown destination", "POST", T, route=r,
          body={"from_warehouse": "Norte", "to_warehouse": "Nowhere", "items": ok_items}))
    add(C("transfer create wrong case is not canonical here", "POST", T, route=r,
          body={"from_warehouse": "norte", "to_warehouse": "Sur", "items": ok_items}))
    add(C("transfer create blank names", "POST", T, route=r,
          body={"from_warehouse": "  ", "to_warehouse": "Sur", "items": ok_items}))
    add(C("transfer create no items", "POST", T, route=r,
          body={"from_warehouse": "Norte", "to_warehouse": "Sur", "items": []}))
    add(C("transfer create blank sku", "POST", T, route=r,
          body={"from_warehouse": "Norte", "to_warehouse": "Sur", "items": [{"sku": "  ", "qty": 1}]}))
    add(C("transfer create validation", "POST", T, route=r,
          body={"from_warehouse": 1, "items": [{"sku": 5, "qty": 0}, "x"], "notes": "n" * 2001}))
    add(C("transfer create missing items", "POST", T, route=r, body={"from_warehouse": "a", "to_warehouse": "b"}))
    add(C("transfer create viewer denied", "POST", T, who="viewer", route=r,
          body={"from_warehouse": "Norte", "to_warehouse": "Sur", "items": ok_items}))
    add(C("transfer create no auth", "POST", T, who="none", route=r, body={}))
    add(C("transfer create scoped origin out of scope", "POST", T, who="analyst", scope=["Sur"], route=r,
          body={"from_warehouse": "Norte", "to_warehouse": "Sur", "items": ok_items}))

    r = "GET /inventory/transfers"
    add(C("transfer list", "GET", T, route=r))
    add(C("transfer list status", "GET", f"{T}?status=in_transit", route=r))
    add(C("transfer list unknown status", "GET", f"{T}?status=nope", route=r))
    add(C("transfer list scoped either end", "GET", T, who="analyst", scope=["Sur"], route=r))
    add(C("transfer list viewer", "GET", T, who="viewer", route=r))
    add(C("transfer list no auth", "GET", T, who="none", route=r))

    def X(k, a):
        return f"{T}/{{{k}}}/{a}"

    r = "POST /inventory/transfers/{id}/receive"
    add(C("transfer receive viewer denied", "POST", X("tr1", "receive"), who="viewer", body={}, route=r))
    add(C("transfer receive no body", "POST", X("tr1", "receive"), route=r))
    add(C("transfer receive lines wrong", "POST", X("tr1", "receive"), body={"lines": [1, "x"]}, route=r))
    add(C("transfer receive lines not a list", "POST", X("tr1", "receive"), body={"lines": 5}, route=r))
    add(C("transfer receive scoped out of scope", "POST", X("tr1", "receive"), who="analyst",
          scope=["principal"], body={}, route=r))
    add(C("transfer receive not found", "POST", f"{T}/nope/receive", body={}, route=r))
    add(C("transfer receive sku not in it", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "ZZ", "received_qty": 1}]}, route=r))
    add(C("transfer receive over outstanding", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "A2", "received_qty": 99.5}]}, route=r))
    add(C("transfer receive text quantity", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "A2", "received_qty": "abc"}]}, route=r))
    add(C("transfer receive list quantity", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "A2", "received_qty": [1]}]}, route=r))
    add(C("transfer receive negative", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "A2", "received_qty": -1}]}, route=r))
    add(C("transfer receive nothing", "POST", X("tr1", "receive"),
          body={"lines": [{"sku": "A2", "received_qty": 0}]}, route=r))
    add(C("transfer receive partial", "POST", X("tr1", "receive"), who="analyst",
          body={"lines": [{"sku": "A2", "received_qty": "1.5"}]}, route=r))
    add(C("transfer cancel after a receipt", "POST", X("tr1", "cancel"),
          route="POST /inventory/transfers/{id}/cancel"))
    add(C("transfer receive the rest", "POST", X("tr1", "receive"), body={}, route=r))
    add(C("transfer receive a received one", "POST", X("tr1", "receive"), body={}, route=r))
    add(C("transfer receive creates the row at destination", "POST", X("tr2", "receive"), body={}, route=r))

    r = "POST /inventory/transfers/{id}/cancel"
    add(C("transfer cancel viewer denied", "POST", X("tr3", "cancel"), who="viewer", route=r))
    add(C("transfer cancel scoped out of scope", "POST", X("tr3", "cancel"), who="analyst",
          scope=["principal"], route=r))
    add(C("transfer cancel not found", "POST", f"{T}/nope/cancel", route=r))
    rc = "POST /inventory/transfers/{id}/close"
    add(C("transfer close an in-transit one", "POST", X("tr3", "close"), route=rc))
    add(C("transfer receive partial for a loss", "POST", X("tr3", "receive"),
          body={"lines": [{"sku": "SUR1", "received_qty": 2}]}, route="POST /inventory/transfers/{id}/receive"))
    add(C("transfer close with loss", "POST", X("tr3", "close"), who="analyst", route=rc))
    add(C("transfer close again", "POST", X("tr3", "close"), route=rc))
    add(C("transfer close viewer denied", "POST", X("tr3", "close"), who="viewer", route=rc))
    add(C("transfer cancel a closed one", "POST", X("tr3", "cancel"), route=r))
    add(C("transfer create to cancel", "POST", T, route="POST /inventory/transfers", save={"tr4": "data.id"},
          body={"from_warehouse": "principal", "to_warehouse": "Sur",
                "items": [{"sku": "A1", "qty": 4}, {"sku": "P-001", "qty": 1}]}))
    add(C("transfer cancel", "POST", X("tr4", "cancel"), who="analyst", route=r))
    add(C("transfer cancel again", "POST", X("tr4", "cancel"), route=r))
    add(C("transfer list at the end", "GET", T, route="GET /inventory/transfers"))

    # ── shrinkage ───────────────────────────────────────────────────────────
    r = "POST /inventory/shrinkage"
    base = {"sku": "A1", "quantity": 1, "reason": "gift"}
    add(C("shrinkage record", "POST", H, who="analyst", route=r,
          body={"sku": "A1", "quantity": 2, "reason": "breakage", "notes": "caja"}))
    add(C("shrinkage canonical warehouse and date", "POST", H, route=r,
          body={**base, "quantity": 1.25, "warehouse": " norte ", "occurred_at": "2026-10-02T09:00:00-06:00"}))
    add(C("shrinkage without a cost", "POST", H, route=r, body={"sku": "A2", "quantity": 1, "reason": "expiry"}))
    add(C("shrinkage invalid reason", "POST", H, route=r, body={**base, "reason": "theft"}))
    add(C("shrinkage more than stock", "POST", H, route=r, body={"sku": "A2", "quantity": 1e6, "reason": "gift"}))
    add(C("shrinkage unknown sku", "POST", H, route=r, body={**base, "sku": "NOPE"}))
    add(C("shrinkage sku in another warehouse", "POST", H, route=r,
          body={"sku": "A2", "quantity": 1, "reason": "gift", "warehouse": "Sur"}))
    add(C("shrinkage bad date", "POST", H, route=r, body={**base, "occurred_at": "yesterday"}))
    add(C("shrinkage zero quantity", "POST", H, route=r, body={**base, "quantity": 0}))
    add(C("shrinkage validation", "POST", H, route=r, body={"sku": 3, "quantity": "x"}))
    add(C("shrinkage viewer denied", "POST", H, who="viewer", route=r, body=base))
    add(C("shrinkage read key refused", "POST", H, who="key_read", route=r, body=base))
    add(C("shrinkage write key", "POST", H, who="key_write", route=r, body=base))
    add(C("shrinkage scoped out of scope", "POST", H, who="analyst", scope=["Norte"], route=r,
          body={"sku": "A2", "quantity": 1, "reason": "gift"}))
    r = "GET /inventory/shrinkage"
    add(C("shrinkage list", "GET", H, route=r))
    add(C("shrinkage list by sku", "GET", f"{H}?sku=A1&limit=2", route=r))
    add(C("shrinkage list limit invalid", "GET", f"{H}?limit=0", route=r))
    add(C("shrinkage list scoped", "GET", H, who="analyst", scope=["Norte"], route=r))
    add(C("shrinkage list no auth", "GET", H, who="none", route=r))
    rr = "GET /inventory/shrinkage/reasons"
    add(C("shrinkage reasons", "GET", f"{H}/reasons", route=rr))
    add(C("shrinkage reasons viewer", "GET", f"{H}/reasons", who="viewer", route=rr))
    add(C("shrinkage reasons no auth", "GET", f"{H}/reasons", who="none", route=rr))
    return cs
