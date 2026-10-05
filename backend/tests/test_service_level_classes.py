"""
Suggested service level per ABC class: shown, then applied only by a person.

Pinned here (needs the local Postgres, like the rest of the suite):
  * the GET describes and writes nothing;
  * POST /apply writes the class's suggested level onto the SKUs of that class
    that nobody configured — checked in `inventory_stock`, not in the response;
  * a SKU whose level somebody set (provenance 'user' or 'file') keeps it, and
    the response says how many were left alone;
  * only the named class is touched;
  * permission pair: viewer 403 and the table unchanged, analyst 200 and the
    table changed;
  * a bad class is a 422 with a stable code and writes nothing;
  * the apply leaves an audit row.
The status computation is replaced by a fixed list of rows: this file is about
what the endpoint does with a classification, and the classification math has
its own pure tests in test_abc_xyz.py.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.db.connection import query, query_one
from backend.inventory import service as inv_svc
from backend.inventory import service_level_classes as slc

URL = "/api/v1/inventory/service-level-classes"


def _item(sku, abc, source="default", level=0.95, demand=10.0, cost=2.0):
    return {"sku": sku, "abc": abc, "service_level": level, "service_level_source": source,
            "daily_demand": demand, "unit_cost": cost}


@pytest.fixture
def catalogue(test_tenant, monkeypatch):
    """Four stock rows: a1 and a2 unconfigured A SKUs, a3 an A SKU the buyer set,
    c1 an unconfigured C SKU. The 'status' the endpoint classifies is fixed."""
    tid = test_tenant["id"]
    tag = uuid4().hex[:6]
    skus = {name: f"{name}-{tag}" for name in ("a1", "a2", "a3", "c1")}
    for name, sku in skus.items():
        inv_svc.upsert_stock(tid, sku, {"current_stock": 5, "unit_cost": 2.0, "warehouse": "principal"},
                             source="file")
    # The buyer's own level on a3 (stamps provenance 'user').
    inv_svc.upsert_stock(tid, skus["a3"], {"service_level": 0.99, "warehouse": "principal"})

    items = [
        _item(skus["a1"], "A"), _item(skus["a2"], "A"),
        _item(skus["a3"], "A", source="user", level=0.99),
        _item(skus["c1"], "C"),
    ]
    monkeypatch.setattr(slc, "_status_items", lambda *a, **k: items)
    monkeypatch.setattr(slc, "_resolve_session", lambda tenant_id, session_id: ("sess", "daily"))
    return {"tenant_id": tid, "skus": skus}


def _level(tid, sku):
    return query_one(
        "SELECT service_level, service_level_set_by FROM inventory_stock "
        "WHERE tenant_id = %s AND sku = %s", (tid, sku))


class TestGet:
    def test_describes_and_writes_nothing(self, client, analyst_headers, catalogue):
        tid, skus = catalogue["tenant_id"], catalogue["skus"]
        before = {n: _level(tid, s) for n, s in skus.items()}
        r = client.get(URL, headers=analyst_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["available"] is True
        by = {c["abc"]: c for c in data["classes"]}
        assert by["A"]["skus"] == 3 and by["A"]["owned"] == 1 and by["A"]["would_change"] == 2
        assert by["C"]["skus"] == 1 and by["C"]["suggested_service_level"] == 0.90
        assert {n: _level(tid, s) for n, s in skus.items()} == before

    def test_viewer_can_read(self, client, viewer_headers, catalogue):
        assert client.get(URL, headers=viewer_headers).status_code == 200


class TestApply:
    def test_viewer_denied_and_nothing_written(self, client, viewer_headers, catalogue):
        tid, skus = catalogue["tenant_id"], catalogue["skus"]
        before = {n: _level(tid, s) for n, s in skus.items()}
        r = client.post(f"{URL}/apply", headers=viewer_headers, json={"abc": "A"})
        assert r.status_code == 403
        assert {n: _level(tid, s) for n, s in skus.items()} == before

    def test_analyst_applies_to_unconfigured_skus_of_that_class_only(
        self, client, analyst_headers, catalogue,
    ):
        tid, skus = catalogue["tenant_id"], catalogue["skus"]
        r = client.post(f"{URL}/apply", headers=analyst_headers, json={"abc": "A"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["updated"] == 2 and data["kept_own_level"] == 1 and data["service_level"] == 0.98

        for name in ("a1", "a2"):
            row = _level(tid, skus[name])
            assert float(row["service_level"]) == 0.98
            assert row["service_level_set_by"] == "user"
        # The buyer's own value survives, and the other class is untouched.
        assert float(_level(tid, skus["a3"])["service_level"]) == 0.99
        assert _level(tid, skus["c1"])["service_level_set_by"] is None

    def test_a_level_set_after_the_status_was_read_is_not_overwritten(
        self, client, analyst_headers, catalogue,
    ):
        tid, skus = catalogue["tenant_id"], catalogue["skus"]
        # The (stale) status still says a1 is on the default, but the buyer has
        # just typed 0.92 on it.
        inv_svc.upsert_stock(tid, skus["a1"], {"service_level": 0.92, "warehouse": "principal"})
        r = client.post(f"{URL}/apply", headers=analyst_headers, json={"abc": "A"})
        assert r.status_code == 200
        assert float(_level(tid, skus["a1"])["service_level"]) == 0.92
        assert float(_level(tid, skus["a2"])["service_level"]) == 0.98
        assert r.json()["data"]["updated"] == 1

    def test_bad_class_is_a_422_and_writes_nothing(self, client, analyst_headers, catalogue):
        tid, skus = catalogue["tenant_id"], catalogue["skus"]
        r = client.post(f"{URL}/apply", headers=analyst_headers, json={"abc": "Z"})
        assert r.status_code == 422
        assert _level(tid, skus["a1"])["service_level_set_by"] is None

    def test_apply_is_audited(self, client, analyst_headers, catalogue):
        tid = catalogue["tenant_id"]
        r = client.post(f"{URL}/apply", headers=analyst_headers, json={"abc": "C"})
        assert r.status_code == 200
        rows = query(
            "SELECT action FROM activity_logs WHERE tenant_id = %s AND action = %s",
            (tid, "audit.config.changed"))
        assert rows, "applying a class suggestion must leave an audit row"
