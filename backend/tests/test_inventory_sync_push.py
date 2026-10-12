"""Push ingest: the same pipeline, rows POSTed instead of pulled.

State-based: every claim about stock reads inventory_stock; idempotency is
checked by counting runs and rows.
"""

from __future__ import annotations

import json

import pytest

from backend.db.connection import query, query_one
from backend.errors import AppError
from backend.inventory_sync import ingest, profiles
from backend.tests.test_inventory_sync import _booted, _stock  # noqa: F401

URL = "/api/v1/inventory-sync"


def _push_profile(tid, **kw):
    body = {"name": "push", "source_kind": "push",
            "mapping": {"sku": "code", "warehouse": "store", "current_stock": "qty", "unit_cost": "cost"},
            "allow_new_warehouses": True, "brake_max_vanished_pct": 50, "brake_min_row_ratio": 0}
    body.update(kw)
    p = profiles.create(tid, "usr_test", body)
    return p


def _enable(tid, pid, sample):
    ingest.ingest(tid, "usr_test", pid, batch_id="preview", rows=sample, dry_run=True)
    profiles.set_enabled(tid, pid, True)


class TestPush:

    def test_dry_run_writes_nothing_and_unlocks_enabling(self, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid)
        with pytest.raises(AppError) as exc:
            profiles.set_enabled(tid, p["id"], True)
        assert exc.value.code == "inventory_sync_dry_run_required"
        out = ingest.ingest(tid, "usr_test", p["id"], batch_id="b0",
                            rows=[{"code": "A", "store": "N", "qty": 3}], dry_run=True)
        assert out["dry_run"] and out["result"]["would_create"] == 1
        assert _stock(tid) == {}
        profiles.set_enabled(tid, p["id"], True)
        assert profiles.get(tid, p["id"])["enabled"] is True

    def test_partial_batch_applies_and_is_idempotent(self, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid)
        rows = [{"code": "A", "store": "N", "qty": 3, "cost": 1.5}, {"code": "B", "store": "N", "qty": "x"}]
        _enable(tid, p["id"], rows)
        out = ingest.ingest(tid, "usr_test", p["id"], batch_id="b1", rows=rows)
        run = out["run"]
        assert run["status"] == "success" and run["created"] == 1 and run["rejected"] == 1
        assert run["rejected_sample"][0]["code"] == "not_a_number"
        assert float(_stock(tid)[("A", "N")]["current_stock"]) == 3
        again = ingest.ingest(tid, "usr_test", p["id"], batch_id="b1", rows=rows)
        assert again["replayed"] is True and again["run"]["id"] == run["id"]
        assert len(query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s AND trigger_kind = 'push'",
                         (p["id"],))) == 1
        with pytest.raises(AppError) as exc:
            ingest.ingest(tid, "usr_test", p["id"], batch_id="b1", rows=[{"code": "A", "store": "N", "qty": 9}])
        assert exc.value.code == "inventory_sync_batch_id_reused"
        assert float(_stock(tid)[("A", "N")]["current_stock"]) == 3

    def test_partial_batches_never_treat_absent_skus_as_gone(self, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid, missing_policy="zero")
        _enable(tid, p["id"], [{"code": "A", "store": "N", "qty": 1}])
        ingest.ingest(tid, "usr_test", p["id"], batch_id="b1",
                      rows=[{"code": "A", "store": "N", "qty": 3}, {"code": "B", "store": "N", "qty": 4}])
        ingest.ingest(tid, "usr_test", p["id"], batch_id="b2", rows=[{"code": "A", "store": "N", "qty": 5}])
        stock = _stock(tid)
        assert float(stock[("A", "N")]["current_stock"]) == 5 and float(stock[("B", "N")]["current_stock"]) == 4

    def test_multi_part_snapshot_applies_on_last_with_the_full_brake(self, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid, missing_policy="zero")
        _enable(tid, p["id"], [{"code": "A", "store": "N", "qty": 1}])
        ingest.ingest(tid, "usr_test", p["id"], batch_id="s1-1", snapshot_id="s1", last=False,
                      rows=[{"code": f"S{i}", "store": "N", "qty": 1} for i in range(5)])
        assert _stock(tid) == {}                                   # nothing until the last part
        out = ingest.ingest(tid, "usr_test", p["id"], batch_id="s1-2", snapshot_id="s1", last=True,
                            rows=[{"code": f"S{i}", "store": "N", "qty": 1} for i in range(5, 10)])
        assert out["run"]["status"] == "success" and out["run"]["created"] == 10
        # A second snapshot missing 8 of 10 SKUs trips the vanished brake: nothing changes.
        out = ingest.ingest(tid, "usr_test", p["id"], batch_id="s2-1", snapshot_id="s2", last=True,
                            rows=[{"code": "S0", "store": "N", "qty": 2}, {"code": "S1", "store": "N", "qty": 2}])
        assert out["run"]["status"] == "braked" and out["run"]["error_code"] == "brake_skus_vanished"
        assert all(float(r["current_stock"]) == 1 for r in _stock(tid).values())

    def test_ledger_push_applies_movements_once_in_any_order(self, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid, stock_model="ledger",
                          mapping={"sku": "code", "warehouse": "store", "current_stock": "qty"},
                          model_config={"sign": {"mode": "type_map", "type_column": "type",
                                                 "type_map": {"IN": 1, "OUT": -1}}, "id_column": "mid"},
                          negative_policy="zero")
        mv = [{"mid": i, "code": "A", "store": "N", "qty": q, "type": t}
              for i, (q, t) in enumerate([(40, "IN"), (12, "OUT"), (3, "OUT"), (10, "IN")], start=1)]
        _enable(tid, p["id"], mv[:1])
        ingest.ingest(tid, "usr_test", p["id"], batch_id="m1", rows=[mv[3], mv[0]])      # out of order
        ingest.ingest(tid, "usr_test", p["id"], batch_id="m2", rows=[mv[1], mv[2], mv[0]])  # mv[0] replayed
        assert float(_stock(tid)[("A", "N")]["current_stock"]) == 35
        bal = query_one("SELECT balance FROM inventory_sync_ledger_balances WHERE profile_id = %s", (p["id"],))
        assert float(bal["balance"]) == 35

    def test_limits_and_shape_errors_have_paths(self, test_tenant, monkeypatch):
        from backend.config import settings
        tid = test_tenant["id"]
        p = _push_profile(tid)
        _enable(tid, p["id"], [{"code": "A", "store": "N", "qty": 1}])
        monkeypatch.setattr(settings, "inventory_sync_max_ingest_rows", 2)
        with pytest.raises(AppError) as exc:
            ingest.ingest(tid, "usr_test", p["id"], batch_id="big", rows=[{"code": "A", "qty": 1}] * 3)
        assert exc.value.code == "inventory_sync_ingest_too_large"
        with pytest.raises(AppError) as exc:
            ingest.ingest(tid, "usr_test", p["id"], batch_id="bad", rows=[{"code": "A"}, 5])
        assert exc.value.params["path"] == "rows[1]"
        with pytest.raises(AppError) as exc:
            ingest.ingest(tid, "usr_test", p["id"], batch_id="no spaces allowed", rows=[])
        assert exc.value.params["path"] == "batch_id"
        assert query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s AND trigger_kind = 'push'",
                     (p["id"],)) == []

    def test_api_ndjson_and_permission_pair(self, client, viewer_headers, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        p = _push_profile(tid)
        _enable(tid, p["id"], [{"code": "A", "store": "N", "qty": 1}])
        body = "\n".join(json.dumps(r) for r in [{"code": "A", "store": "N", "qty": 7},
                                                  {"code": "B", "store": "N", "qty": 2}])
        r = client.post(f"{URL}/profiles/{p['id']}/ingest?batch_id=nd1", content=body,
                        headers={**viewer_headers, "Content-Type": "application/x-ndjson"})
        assert r.status_code == 403 and _stock(tid) == {}
        r = client.post(f"{URL}/profiles/{p['id']}/ingest?batch_id=nd1", content=body,
                        headers={**analyst_headers, "Content-Type": "application/x-ndjson"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["run"]["created"] == 2
        assert float(_stock(tid)[("A", "N")]["current_stock"]) == 7
