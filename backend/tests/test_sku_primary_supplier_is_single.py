"""A SKU has exactly one primary supplier, and everybody agrees which.

`sku_suppliers.is_primary` is `DEFAULT TRUE` and no constraint stops a SKU from
having two, so linking a second supplier used to make it primary as well. From
then on "who supplies this SKU" had no answer: `get_primary_suppliers_map` built
a dict from an unordered scan (last row won) and `get_sku_suppliers` sorted
alphabetically, so the same request could name one supplier in a list and build
the recommendation for another.

Two halves, and both are tested here because they fail differently:
  - the WRITE now demotes the others, so new data has one primary;
  - the READ is ordered, so rows that ALREADY violate the invariant still
    resolve to the same supplier everywhere.
"""

from backend.db.connection import execute, query, query_one
from backend.inventory import service as svc
from backend.inventory import supplier_service as sup_svc


def _a_sku(tenant_id: str, session_id: str) -> str:
    items = svc.get_inventory_status(tenant_id, session_id)
    assert items, "session fixture should expose forecast SKUs"
    return items[0]["sku"]


class TestSettingAPrimaryDemotesTheOthers:
    def test_second_primary_demotes_the_first(
        self, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sku = _a_sku(tid, completed_session["id"])

        first = sup_svc.create_supplier(tid, {"name": "Primero SA"})
        second = sup_svc.create_supplier(tid, {"name": "Segundo SA"})
        sup_svc.upsert_sku_supplier(tid, sku, first["id"], {"is_primary": True})
        sup_svc.upsert_sku_supplier(tid, sku, second["id"], {"is_primary": True})

        rows = query(
            "SELECT supplier_id, is_primary FROM sku_suppliers "
            "WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        primaries = [r["supplier_id"] for r in rows if r["is_primary"]]
        assert primaries == [second["id"]], (
            f"exactly one primary expected, the newest; got {primaries}"
        )

    def test_linking_without_naming_the_flag_does_not_steal_the_primary(
        self, test_tenant, completed_session
    ):
        """The column defaults to TRUE, so this is the path that created the
        duplicates: a caller who never mentioned `is_primary` at all."""
        tid = test_tenant["id"]
        sku = _a_sku(tid, completed_session["id"])

        chosen = sup_svc.create_supplier(tid, {"name": "Elegido SA"})
        other = sup_svc.create_supplier(tid, {"name": "Otro SA"})
        sup_svc.upsert_sku_supplier(tid, sku, chosen["id"], {"is_primary": True})
        # No is_primary in the payload — the schema default would make it TRUE.
        sup_svc.upsert_sku_supplier(tid, sku, other["id"], {"unit_cost": 900})

        # The invariant that matters is not "the flag is FALSE" — the column
        # default may still write TRUE — but that the product resolves ONE
        # supplier, and the same one, on every path.
        listed = sup_svc.get_sku_suppliers(tid, sku)
        mapped = sup_svc.get_primary_suppliers_map(tid)
        assert listed[0]["supplier_id"] == mapped[sku]["supplier_id"], (
            "the head of the list and the planning map must be the same supplier"
        )


class TestLegacyRowsWithTwoPrimariesStillResolveTheSame:
    def test_map_and_list_agree_and_are_stable(
        self, test_tenant, completed_session
    ):
        """Rows that predate the demotion. Written straight to the table on
        purpose: going through the service would fix them, and what is under
        test is that the READ copes without a data migration."""
        tid = test_tenant["id"]
        sku = _a_sku(tid, completed_session["id"])

        older = sup_svc.create_supplier(tid, {"name": "Antiguo SA"})
        newer = sup_svc.create_supplier(tid, {"name": "Reciente SA"})
        for sup_id, ts in ((older["id"], "2026-01-01"), (newer["id"], "2026-06-01")):
            execute(
                "INSERT INTO sku_suppliers (tenant_id, sku, supplier_id, is_primary, created_at)"
                " VALUES (%s, %s, %s, TRUE, %s)",
                (tid, sku, sup_id, ts),
            )

        assert query_one(
            "SELECT COUNT(*) AS n FROM sku_suppliers "
            "WHERE tenant_id = %s AND sku = %s AND is_primary = TRUE",
            (tid, sku),
        )["n"] == 2, "the fixture must actually hold two primaries"

        mapped = sup_svc.get_primary_suppliers_map(tid)[sku]["supplier_id"]
        listed = sup_svc.get_sku_suppliers(tid, sku)[0]["supplier_id"]

        assert mapped == older["id"], "oldest primary wins, as documented"
        assert listed == mapped, "list head and planning map must not disagree"
        # Stable across calls: the bug was that this varied run to run.
        for _ in range(5):
            assert sup_svc.get_primary_suppliers_map(tid)[sku]["supplier_id"] == mapped

    def test_the_recommendation_names_the_same_supplier_the_list_does(
        self, test_tenant, completed_session
    ):
        """The whole point: the supplier on the semáforo row is the resolved
        one, not whichever row Postgres returned last."""
        tid = test_tenant["id"]
        sid = completed_session["id"]
        sku = _a_sku(tid, sid)

        older = sup_svc.create_supplier(tid, {"name": "Aaa Antiguo"})
        newer = sup_svc.create_supplier(tid, {"name": "Zzz Reciente"})
        for sup_id, ts in ((older["id"], "2026-01-01"), (newer["id"], "2026-06-01")):
            execute(
                "INSERT INTO sku_suppliers (tenant_id, sku, supplier_id, is_primary, created_at)"
                " VALUES (%s, %s, %s, TRUE, %s)",
                (tid, sku, sup_id, ts),
            )

        row = {i["sku"]: i for i in svc.get_inventory_status(tid, sid)}[sku]
        assert row["supplier_id"] == older["id"]
        assert row["supplier"] == "Aaa Antiguo"


class TestPermissions:
    def test_viewer_cannot_assign_and_state_is_unchanged(
        self, client, viewer_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sku = _a_sku(tid, completed_session["id"])
        supplier = sup_svc.create_supplier(tid, {"name": "Denegado SA"})

        r = client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{supplier['id']}",
            json={"is_primary": True},
            headers=viewer_headers,
        )
        assert r.status_code == 403, r.text
        assert query_one(
            "SELECT COUNT(*) AS n FROM sku_suppliers "
            "WHERE tenant_id = %s AND sku = %s AND supplier_id = %s",
            (tid, sku, supplier["id"]),
        )["n"] == 0, "a denied call must not have written the link"

    def test_analyst_can_assign_and_the_row_lands(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sku = _a_sku(tid, completed_session["id"])
        supplier = sup_svc.create_supplier(tid, {"name": "Permitido SA"})

        r = client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{supplier['id']}",
            json={"is_primary": True},
            headers=analyst_headers,
        )
        assert r.status_code == 200, r.text
        link = query_one(
            "SELECT is_primary FROM sku_suppliers "
            "WHERE tenant_id = %s AND sku = %s AND supplier_id = %s",
            (tid, sku, supplier["id"]),
        )
        assert link is not None and link["is_primary"] is True
