"""The developer reference on the landing describes the API that is running.

/desarrolladores renders `Frontend/src/data/public-api.json`, which
`backend/scripts/export_public_api.py` generates from the app's OpenAPI. A route
added to an exposed area, removed, or given different parameters makes the
committed file stale — and this test red, until somebody regenerates it and
reads the diff. That review is the deliberate step in "a new route's exposure
is a choice".
"""
import json

from backend.scripts import export_public_api as exporter


def test_committed_snapshot_is_current():
    committed = exporter.SNAPSHOT.read_text(encoding="utf-8")
    fresh = exporter.render()
    assert committed == fresh, (
        "Frontend/src/data/public-api.json is stale. Run\n"
        "    python -m backend.scripts.export_public_api\n"
        "and review the diff: every new entry is a route customers will be told "
        "they can call with an API key."
    )


def test_check_mode_fails_on_a_stale_snapshot(tmp_path, monkeypatch):
    """The --check path, broken on purpose once, so it is known to bite."""
    stale = tmp_path / "public-api.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(exporter, "SNAPSHOT", stale)
    assert exporter.main(["--check"]) == 1


def test_snapshot_holds_only_key_callable_routes():
    data = json.loads(exporter.SNAPSHOT.read_text(encoding="utf-8"))
    paths = {(e["method"], e["path"]) for t in data["tags"] for e in t["endpoints"]}
    assert ("POST", "/auth/login") not in paths
    assert ("POST", "/api-keys") not in paths
    assert ("DELETE", "/tenant") not in paths
    assert ("POST", "/inventory/log-po") in paths
    assert data["counts"]["total"] == len(paths)
