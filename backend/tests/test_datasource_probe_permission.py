"""The SQL connection probe stores its verdict in `datasets.connection_status`
(which gates execute-query and materialize), so it is a write: a viewer is
refused and nothing changes; an analyst probes and the verdict is stored."""

from uuid import uuid4

from backend.db.connection import execute, query_one
from backend.tests.test_sql_materialize import _own_db_config


def _source(client, auth_headers):
    r = client.post("/api/v1/data-sources/sql", headers=auth_headers,
                    json={"name": f"erp-{uuid4().hex[:6]}", **_own_db_config()})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _status(source_id):
    return query_one("SELECT connection_status, updated_at FROM datasets WHERE id = %s",
                     (source_id,))


def test_viewer_is_refused_and_the_status_is_untouched(client, auth_headers, viewer_headers):
    sid = _source(client, auth_headers)
    execute("UPDATE datasets SET connection_status = 'pending' WHERE id = %s", (sid,))
    before = _status(sid)
    r = client.post(f"/api/v1/data-sources/{sid}/test-connection", headers=viewer_headers)
    assert r.status_code == 403, r.text
    assert _status(sid) == before


def test_analyst_probes_and_the_verdict_is_stored(client, auth_headers, analyst_headers):
    sid = _source(client, auth_headers)
    execute("UPDATE datasets SET connection_status = 'pending' WHERE id = %s", (sid,))
    r = client.post(f"/api/v1/data-sources/{sid}/test-connection", headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["ok"] is True, r.text
    assert _status(sid)["connection_status"] == "connected"
