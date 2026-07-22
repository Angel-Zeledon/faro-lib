from uuid import uuid4

from backend.db.connection import execute, query, query_one
from backend.inventory import roi_service
from backend.inventory.roi_service import format_po_number


def _line():
    return {
        "sku": f"PON_{uuid4().hex[:8]}", "final_qty": 5,
        "status": "approved", "supplier": "Acme",
    }


class TestPONumberAssignment:
    def test_sequential_per_tenant_starting_at_one(self, client, test_tenant):
        tid = test_tenant["id"]
        po1 = roi_service.log_po_generation(tid, "sess-test", [_line()])
        po2 = roi_service.log_po_generation(tid, "sess-test", [_line()])
        row1 = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po1["id"],))
        row2 = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po2["id"],))
        assert row1["po_number"] == 1
        assert row2["po_number"] == 2

    def test_sequences_are_independent_across_tenants(self, client, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            tid_a, tid_b = test_tenant["id"], other["id"]
            roi_service.log_po_generation(tid_a, "sess-test", [_line()])
            roi_service.log_po_generation(tid_a, "sess-test", [_line()])
            po_b = roi_service.log_po_generation(tid_b, "sess-test", [_line()])
            row_b = query_one(
                "SELECT po_number FROM inventory_po_log WHERE id = %s", (po_b["id"],)
            )
            # Tenant B starts its own sequence — A's two orders must not advance it.
            assert row_b["po_number"] == 1
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_po_history_endpoint_carries_po_number(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po = roi_service.log_po_generation(tid, "sess-test", [_line()])
        resp = client.get("/api/v1/inventory/po-history?limit=5", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.json()["data"]
        match = [r for r in rows if r["id"] == po["id"]]
        assert match and match[0]["po_number"] == 1


class TestPONumberBackfill:
    def test_backfill_numbers_null_rows_in_created_order(self, client, test_tenant):
        from backend.db.migrations import run_all
        tid = test_tenant["id"]
        # Simulate pre-feature rows: insert directly with NULL po_number and
        # staggered timestamps, oldest first.
        ids = []
        for offset_min in (30, 20, 10):
            row = query_one(
                """INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, generated_at)
                   VALUES (%s, %s, 1, 5, NOW() - (%s || ' minutes')::interval)
                   RETURNING id""",
                (tid, "sess-backfill", offset_min),
            )
            ids.append(row["id"])
        run_all()  # idempotent — re-runs every migration incl. the backfill
        numbered = query(
            """SELECT id, po_number FROM inventory_po_log
               WHERE tenant_id = %s ORDER BY generated_at""",
            (tid,),
        )
        assert [r["id"] for r in numbered] == ids
        assert [r["po_number"] for r in numbered] == [1, 2, 3]

    def test_backfill_offsets_past_existing_numbers(self, test_tenant):
        from backend.db.migrations import run_all
        tid = test_tenant["id"]
        # Rows 1 and 2 assigned normally by the insert path…
        roi_service.log_po_generation(tid, "sess-mixed", [_line()])
        roi_service.log_po_generation(tid, "sess-mixed", [_line()])
        # …then a NULL row appears (e.g. an old-code instance during a rolling deploy).
        row = query_one(
            """INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units)
               VALUES (%s, %s, 1, 5) RETURNING id""",
            (tid, "sess-mixed"),
        )
        run_all()  # must not collide with the unique index, must not renumber 1/2
        numbered = query(
            "SELECT po_number FROM inventory_po_log WHERE tenant_id = %s ORDER BY po_number",
            (tid,),
        )
        assert [r["po_number"] for r in numbered] == [1, 2, 3]
        got = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (row["id"],))
        assert got["po_number"] == 3


class TestFormatPONumber:
    def test_pads_to_six_digits(self):
        assert format_po_number(123, "fallback") == "OC-000123"

    def test_falls_back_when_unnumbered(self):
        assert format_po_number(None, "abc-uuid") == "abc-uuid"
