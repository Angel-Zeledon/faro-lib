"""A mixed-granularity file must reach the user, not just the response body.

The profiler has always detected this and the API has always carried it in
`inspection.granularity`, but nothing read it. It matters because every SKU
trains on ONE time axis: a product reported monthly gets modeled as a daily
series, so its reorder quantity comes out roughly 30x below what it needs.

These tests pin the API contract the Quick Start panel now depends on.
"""

from uuid import uuid4

import pytest


def _csv(rows: list[str]) -> bytes:
    return ("sku,date,sales\n" + "\n".join(rows) + "\n").encode()


def _daily(sku: str, n: int = 40, start_day: int = 1) -> list[str]:
    from datetime import date, timedelta
    d0 = date(2025, 1, start_day)
    return [f"{sku},{(d0 + timedelta(days=i)).isoformat()},{10 + i % 5}" for i in range(n)]


def _monthly(sku: str, n: int = 18) -> list[str]:
    from datetime import date
    out = []
    for i in range(n):
        y, m = 2023 + (i // 12), (i % 12) + 1
        out.append(f"{sku},{date(y, m, 1).isoformat()},{300 + i}")
    return out


def _inspect(client, headers, csv: bytes) -> dict:
    ds = client.post(
        "/api/v1/datasets",
        files={"file": (f"gran-{uuid4().hex[:6]}.csv", csv, "text/csv")},
        headers=headers,
    )
    assert ds.status_code == 201, ds.text
    sess = client.post("/api/v1/sessions", json={"name": f"gran-{uuid4().hex[:6]}"},
                       headers=headers)
    assert sess.status_code in (200, 201), sess.text
    sid = sess.json()["data"]["id"]
    att = client.post(f"/api/v1/sessions/{sid}/dataset",
                      json={"dataset_id": ds.json()["data"]["id"]}, headers=headers)
    assert att.status_code == 200, att.text
    r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=headers)
    assert r.status_code == 200, r.text
    return {"sid": sid, "data": r.json()["data"]}


class TestInspectCarriesGranularity:
    def test_a_mixed_file_is_reported_as_a_conflict(self, client, auth_headers):
        """Daily SKUs next to monthly SKUs in one file."""
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        gran = _inspect(client, auth_headers, csv)["data"].get("granularity")
        assert gran is not None, "the panel has nothing to render without this key"
        assert gran["status"] == "conflict"
        assert len(gran["detected"]) > 1

    def test_the_conflict_names_which_skus_sit_in_which_bucket(self, client, auth_headers):
        """Without the SKU lists the user cannot go split the file."""
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        gran = _inspect(client, auth_headers, csv)["data"]["granularity"]
        all_skus = {s for skus in gran["skus_by_frequency"].values() for s in skus}
        assert {"SKU-DAILY", "SKU-MONTHLY"} <= all_skus

    def test_a_conflict_suggests_the_coarsest_bucket(self, client, auth_headers):
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        gran = _inspect(client, auth_headers, csv)["data"]["granularity"]
        assert gran["suggested_target"] == gran["detected"][-1]

    def test_a_uniform_file_is_not_flagged(self, client, auth_headers):
        """The panel must stay silent on a healthy file."""
        csv = _csv(_daily("SKU-A") + _daily("SKU-B", start_day=1))
        gran = _inspect(client, auth_headers, csv)["data"]["granularity"]
        assert gran["status"] == "homogeneous"
        assert len(gran["detected"]) == 1


class TestGranularityIsPersistedNotRecomputed:
    def test_the_conflict_survives_a_second_inspect(self, client, auth_headers):
        """The second call is served from the cached blob — it must still carry it."""
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        first = _inspect(client, auth_headers, csv)
        again = client.get(f"/api/v1/sessions/{first['sid']}/inspect", headers=auth_headers)
        assert again.json()["data"]["granularity"]["status"] == "conflict"

    def test_it_is_stored_on_the_session_row(self, client, auth_headers, test_tenant):
        """Direct DB read: the API response is not the only place it lives."""
        from backend.db import session_store
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        sid = _inspect(client, auth_headers, csv)["sid"]
        stored = session_store.get_field(test_tenant["id"], sid, "inspection")
        assert stored["granularity"]["status"] == "conflict"


class TestPermissions:
    def test_a_viewer_can_read_the_inspection(self, client, auth_headers, viewer_headers):
        """Inspect is a read; a viewer must be able to see the warning too."""
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        sid = _inspect(client, auth_headers, csv)["sid"]
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=viewer_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["granularity"]["status"] == "conflict"

    def test_no_token_is_refused(self, client, auth_headers):
        csv = _csv(_daily("SKU-DAILY") + _monthly("SKU-MONTHLY"))
        sid = _inspect(client, auth_headers, csv)["sid"]
        assert client.get(f"/api/v1/sessions/{sid}/inspect").status_code in (401, 403)
