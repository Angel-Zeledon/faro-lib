"""End-to-end tests for the in-app dataset editor (edit-table + save-as-new).

Asserts DB + on-disk state directly, a viewer/analyst permission pair, tenant
isolation, CSV-injection neutralization, and SQL-source rejection.
"""
from pathlib import Path

from backend.db.connection import query_one


def _upload_csv(client, headers, content: bytes, name="editor_src.csv"):
    resp = client.post(
        "/api/v1/data-sources/file",
        files={"file": (name, content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


SMALL_CSV = b"sku,qty,note\nA,1,hello\nB,2,world\nC,3,foo\n"


def test_edit_table_loads_small_file(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["columns"] == ["sku", "qty", "note"]
    assert len(data["rows"]) == 3
    assert data["rows"][0] == {"sku": "A", "qty": 1, "note": "hello"}


def test_edit_table_rejects_over_row_guard(client, analyst_headers):
    from backend.db.connection import execute
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    # Force the stored row_count over the guard without a huge file: the guard
    # must trip from stored stats BEFORE the (small) file would be read.
    execute("UPDATE datasets SET row_count=%s WHERE id=%s", (10_000_000, src["id"]))
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 400
    assert "too large to edit online" in resp.json()["detail"]


def test_edit_table_rejects_over_byte_guard(client, analyst_headers):
    from backend.db.connection import execute
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    execute("UPDATE datasets SET size_bytes=%s WHERE id=%s", (999_000_000, src["id"]))
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 400
    assert "too large to edit online" in resp.json()["detail"]


def test_save_as_new_creates_distinct_dataset(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    orig_path = query_one("SELECT file_path FROM datasets WHERE id=%s", (src["id"],))["file_path"]
    orig_bytes = Path(orig_path).read_bytes()

    body = {
        "name": "My Edited Set",
        "columns": ["sku", "qty", "note"],
        "rows": [
            {"sku": "A", "qty": 1, "note": "hello"},
            {"sku": "B", "qty": 2, "note": "world"},
            {"sku": "C", "qty": 3, "note": "foo"},
        ],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_ds = resp.json()["data"]
    assert new_ds["id"] != src["id"]

    # DB: new row exists with parent_id = source id, correct row_count
    db_row = query_one("SELECT parent_id, row_count, file_path, name FROM datasets WHERE id=%s", (new_ds["id"],))
    assert db_row["parent_id"] == src["id"]
    assert db_row["row_count"] == 3
    assert db_row["name"] == "My Edited Set"

    # On disk: new file exists and is a distinct path
    assert db_row["file_path"] != orig_path
    assert Path(db_row["file_path"]).exists()

    # Original untouched: row unchanged AND file bytes unchanged
    orig_after = query_one("SELECT row_count, parent_id FROM datasets WHERE id=%s", (src["id"],))
    assert orig_after["parent_id"] is None
    assert Path(orig_path).read_bytes() == orig_bytes


def test_save_as_new_persists_edits(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    # changed cell (A qty 1->99 dropped anyway), deleted row (C removed), renamed
    # column (note->comment), dropped column (qty gone from columns list).
    body = {
        "name": "edited",
        "columns": ["sku", "comment"],
        "rows": [
            {"sku": "A", "comment": "changed"},
            {"sku": "B", "comment": "world"},
        ],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]

    read = client.get(f"/api/v1/data-sources/{new_id}/edit-table", headers=analyst_headers)
    data = read.json()["data"]
    assert data["columns"] == ["sku", "comment"]          # renamed + dropped column
    assert len(data["rows"]) == 2                          # deleted row gone
    assert data["rows"][0] == {"sku": "A", "comment": "changed"}  # changed cell


def test_save_as_new_permission_pair(client, viewer_headers, analyst_headers):
    # Source uploaded by analyst; both users share the same tenant via test_tenant.
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    tid = query_one("SELECT tenant_id FROM datasets WHERE id=%s", (src["id"],))["tenant_id"]
    before = query_one("SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=%s", (tid,))["c"]

    body = {"name": "x", "columns": ["sku"], "rows": [{"sku": "A"}]}

    # Viewer denied — 403 and no new row
    denied = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=viewer_headers)
    assert denied.status_code == 403
    after_denied = query_one("SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=%s", (tid,))["c"]
    assert after_denied == before

    # Analyst allowed — success and a new row present
    allowed = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert allowed.status_code == 200, allowed.text
    new_id = allowed.json()["data"]["id"]
    assert query_one("SELECT id FROM datasets WHERE id=%s", (new_id,)) is not None


def test_tenant_isolation(client, analyst_headers, make_tenant_user_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)   # tenant A
    other = make_tenant_user_headers(role="analyst")        # tenant B

    r1 = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=other)
    assert r1.status_code == 404
    r2 = client.post(
        f"/api/v1/data-sources/{src['id']}/save-as-new",
        json={"name": "x", "columns": ["sku"], "rows": [{"sku": "A"}]},
        headers=other,
    )
    assert r2.status_code == 404


def test_csv_injection_sanitized(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    body = {
        "name": "danger",
        "columns": ["sku", "payload"],
        "rows": [{"sku": "A", "payload": "=cmd()|calc"}],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_path = query_one("SELECT file_path FROM datasets WHERE id=%s", (resp.json()["data"]["id"],))["file_path"]
    written = Path(new_path).read_text(encoding="utf-8")
    # The dangerous value is neutralized with a leading quote, so no raw "=cmd"
    assert "'=cmd()|calc" in written
    assert "\n=cmd" not in written and ",=cmd" not in written


def test_sql_source_rejected(client, analyst_headers):
    # Insert a SQL-typed dataset row directly (the editor rejects the type
    # outright — no live connection or the file-only create path is involved).
    from backend.db.connection import execute
    from backend.utils.ids import generate_id
    # Borrow the analyst's tenant id from an uploaded file source.
    tmp = _upload_csv(client, analyst_headers, SMALL_CSV)
    tid = query_one("SELECT tenant_id FROM datasets WHERE id=%s", (tmp["id"],))["tenant_id"]
    sid = generate_id("ds")
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, source_type,
            connection_status, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,'sqlsrc','n/a','postgresql','sql','pending','tester',NOW(),NOW())""",
        (sid, tid),
    )

    r1 = client.get(f"/api/v1/data-sources/{sid}/edit-table", headers=analyst_headers)
    assert r1.status_code == 400
    assert "file datasets are editable" in r1.json()["detail"]

    r2 = client.post(
        f"/api/v1/data-sources/{sid}/save-as-new",
        json={"name": "x", "columns": ["a"], "rows": []},
        headers=analyst_headers,
    )
    assert r2.status_code == 400
    assert "file datasets are editable" in r2.json()["detail"]
