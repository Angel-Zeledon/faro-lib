"""A file that cannot produce a forecast must say so at /inspect, not at the end.

The defect: a one-row upload reached the mapping screen with no warning, under a
button reading "this looks good", and the user waited ~2 minutes before the job
failed with `no_models_trained`. The profiler already knew the row count per SKU
at inspection time.

The engine-side rule is pinned in
`ForecastingCore/tests/test_profiler_trainability.py`. These tests pin the API
contract the mapping screen depends on: `blocking` must survive the trip through
the worker, the response envelope and the cached inspection blob — a rule the
frontend cannot see is a rule that does not exist.
"""

from datetime import date, timedelta
from uuid import uuid4


def _csv(rows: list[str]) -> bytes:
    return ("sku,date,sales\n" + "\n".join(rows) + "\n").encode()


def _daily(sku: str, n: int, start: date = date(2025, 1, 1)) -> list[str]:
    return [f"{sku},{(start + timedelta(days=i)).isoformat()},{10 + i % 5}" for i in range(n)]


ONE_ROW = b"fecha,producto,cantidad\n2026-01-01,A,5\n"

# Three established products plus one launched last month. Normal, and it must
# still train — the rule is "nothing can train", not "something is short".
CATALOGUE = _csv(
    _daily("SKU-A", 120) + _daily("SKU-B", 120) + _daily("SKU-C", 120)
    + _daily("SKU-NEW", 5, start=date(2025, 6, 1))
)


def _inspect(client, headers, csv: bytes) -> dict:
    ds = client.post(
        "/api/v1/datasets",
        files={"file": (f"train-{uuid4().hex[:6]}.csv", csv, "text/csv")},
        headers=headers,
    )
    assert ds.status_code == 201, ds.text
    sess = client.post("/api/v1/sessions", json={"name": f"train-{uuid4().hex[:6]}"},
                       headers=headers)
    assert sess.status_code in (200, 201), sess.text
    sid = sess.json()["data"]["id"]
    att = client.post(f"/api/v1/sessions/{sid}/dataset",
                      json={"dataset_id": ds.json()["data"]["id"]}, headers=headers)
    assert att.status_code == 200, att.text
    r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=headers)
    assert r.status_code == 200, r.text
    return {"sid": sid, "data": r.json()["data"]}


def _data_quality(inspection: dict) -> dict:
    return inspection["data"]["profile"]["data_quality"]


class TestInspectFlagsAnUntrainableFile:
    def test_a_one_row_upload_is_blocking(self, client, auth_headers):
        dq = _data_quality(_inspect(client, auth_headers, ONE_ROW))
        assert dq["blocking"] is True

    def test_the_reason_is_named_so_the_screen_can_translate_it(self, client, auth_headers):
        """A bare boolean would leave the panel with nothing to say."""
        dq = _data_quality(_inspect(client, auth_headers, ONE_ROW))
        blocking = [i for i in dq["issues"] if i.get("blocking")]
        assert [i["type"] for i in blocking] == ["no_trainable_history"]
        assert blocking[0]["min_required"] == 20
        assert blocking[0]["longest_history"] == 1

    def test_it_rides_the_existing_issues_channel(self, client, auth_headers):
        """Same key the mapping screen already renders — no second surface."""
        dq = _data_quality(_inspect(client, auth_headers, ONE_ROW))
        assert dq["issues"], "DataIssuesPanel renders nothing from an empty list"
        assert dq["issues"][0]["severity"] == "error"


class TestInspectLeavesAHealthyCatalogueAlone:
    def test_a_catalogue_with_one_new_product_is_not_blocking(self, client, auth_headers):
        """The false positive that would be worse than the original defect."""
        dq = _data_quality(_inspect(client, auth_headers, CATALOGUE))
        assert dq["blocking"] is False
        assert [i for i in dq["issues"] if i.get("blocking")] == []

    def test_the_new_product_is_still_reported_softly(self, client, auth_headers):
        dq = _data_quality(_inspect(client, auth_headers, CATALOGUE))
        short = [i for i in dq["issues"] if i["type"] == "short_history"]
        assert short, "the newcomer must still be mentioned as a soft warning"
        assert not short[0].get("blocking")


class TestTheFlagIsPersistedNotRecomputed:
    def test_it_survives_a_second_inspect(self, client, auth_headers):
        """The second call is served from the cached blob."""
        first = _inspect(client, auth_headers, ONE_ROW)
        again = client.get(f"/api/v1/sessions/{first['sid']}/inspect", headers=auth_headers)
        assert again.status_code == 200, again.text
        assert again.json()["data"]["profile"]["data_quality"]["blocking"] is True

    def test_it_is_stored_on_the_session_row(self, client, auth_headers, test_tenant):
        """Direct DB read: the API response is not the only place it lives."""
        from backend.db import session_store
        sid = _inspect(client, auth_headers, ONE_ROW)["sid"]
        stored = session_store.get_field(test_tenant["id"], sid, "inspection")
        assert stored["profile"]["data_quality"]["blocking"] is True

    def test_the_profile_alias_route_carries_it_too(self, client, auth_headers):
        """/profile delegates to /inspect; a divergence here is a silent gap."""
        sid = _inspect(client, auth_headers, ONE_ROW)["sid"]
        r = client.get(f"/api/v1/sessions/{sid}/profile", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["profile"]["data_quality"]["blocking"] is True


class TestPermissions:
    def test_a_viewer_can_read_the_blocking_flag(self, client, auth_headers, viewer_headers):
        """Inspect is a read: a viewer must see the dead end too."""
        sid = _inspect(client, auth_headers, ONE_ROW)["sid"]
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=viewer_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["profile"]["data_quality"]["blocking"] is True

    def test_no_token_is_refused(self, client, auth_headers):
        sid = _inspect(client, auth_headers, ONE_ROW)["sid"]
        assert client.get(f"/api/v1/sessions/{sid}/inspect").status_code in (401, 403)
