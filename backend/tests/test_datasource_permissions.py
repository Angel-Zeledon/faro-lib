"""A viewer must not be able to change what the company's data sources are.

Every mutating route in `data-sources` was guarded by `get_current_user`, which
only asks that you are logged in. A read-only account could therefore point the
tenant at any database it liked and store the credentials, replace the file
behind an existing source, rename one, or delete one outright — found by driving
the live API with a viewer token during a multi-tenant pass.

Each case is a pair: the viewer is refused AND the state is unchanged, then an
analyst does the same thing successfully.
"""
import io

from backend.db.connection import query_one


def _make_sql_source(client, headers, name="ERP de prueba"):
    return client.post("/api/v1/data-sources/sql", headers=headers, json={
        "name": name, "host": "127.0.0.1", "port": 5432, "database": "nope",
        "username": "u", "password": "p", "engine": "postgresql",
    })


def _tid(test_tenant):
    """The fixture hands back the whole tenant row, not just its id."""
    return test_tenant["id"] if isinstance(test_tenant, dict) else test_tenant


def _count_sources(tenant_id):
    row = query_one(
        "SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (tenant_id,))
    return row["n"] if row else 0


class TestViewerCannotCreate:
    def test_viewer_cannot_create_a_sql_connection(
        self, client, viewer_headers, analyst_headers, test_tenant
    ):
        tid = _tid(test_tenant)
        before = _count_sources(tid)

        r = client.post("/api/v1/data-sources/sql", headers=viewer_headers, json={
            "name": "Base ajena", "host": "10.0.0.9", "port": 5432,
            "database": "otra", "username": "root", "password": "s3cret",
            "engine": "postgresql",
        })
        assert r.status_code == 403, r.text
        assert _count_sources(tid) == before, "a row was written anyway"
        assert query_one(
            "SELECT id FROM datasets WHERE tenant_id = %s AND name = %s",
            (tid, "Base ajena")) is None

    def test_viewer_cannot_upload_a_file_source(self, client, viewer_headers, test_tenant):
        tid = _tid(test_tenant)
        before = _count_sources(tid)
        r = client.post(
            "/api/v1/data-sources/file", headers=viewer_headers,
            files={"file": ("ventas.csv", io.BytesIO(b"fecha,sku,ventas\n2026-01-01,A,5\n"),
                            "text/csv")})
        assert r.status_code == 403, r.text
        assert _count_sources(tid) == before

    def test_analyst_can_create_the_same_connection(
        self, client, analyst_headers, test_tenant
    ):
        tid = _tid(test_tenant)
        before = _count_sources(tid)
        r = _make_sql_source(client, analyst_headers, name="ERP del analista")
        assert r.status_code in (200, 201), r.text
        assert _count_sources(tid) == before + 1
        assert query_one(
            "SELECT id FROM datasets WHERE tenant_id = %s AND name = %s",
            (tid, "ERP del analista")) is not None


class TestViewerCannotChangeAnExistingSource:
    def test_viewer_cannot_rename(self, client, analyst_headers, viewer_headers, test_tenant):
        sid = _make_sql_source(client, analyst_headers, "Nombre original").json()["data"]["id"]

        r = client.patch(f"/api/v1/data-sources/{sid}", headers=viewer_headers,
                         json={"name": "Renombrada por un lector"})
        assert r.status_code == 403, r.text

        row = query_one("SELECT name FROM datasets WHERE id = %s", (sid,))
        assert row["name"] == "Nombre original", "the rename went through anyway"

    def test_viewer_cannot_delete(self, client, analyst_headers, viewer_headers, test_tenant):
        sid = _make_sql_source(client, analyst_headers, "Para borrar").json()["data"]["id"]

        r = client.delete(f"/api/v1/data-sources/{sid}", headers=viewer_headers)
        assert r.status_code == 403, r.text
        assert query_one("SELECT id FROM datasets WHERE id = %s", (sid,)) is not None, \
            "the source was deleted by a read-only account"

    def test_analyst_can_delete(self, client, analyst_headers):
        sid = _make_sql_source(client, analyst_headers, "Borrable").json()["data"]["id"]
        r = client.delete(f"/api/v1/data-sources/{sid}", headers=analyst_headers)
        assert r.status_code in (200, 204), r.text
        assert query_one("SELECT id FROM datasets WHERE id = %s", (sid,)) is None

    def test_viewer_cannot_repoint_the_connection(
        self, client, analyst_headers, viewer_headers
    ):
        sid = _make_sql_source(client, analyst_headers, "Config fija").json()["data"]["id"]

        r = client.patch(f"/api/v1/data-sources/{sid}/sql-config", headers=viewer_headers,
                         json={"host": "10.9.9.9", "port": 5432, "database": "ajena",
                               "username": "root", "password": "otra", "engine": "postgresql"})
        assert r.status_code == 403, r.text

    def test_viewer_cannot_save_a_query_onto_the_source(
        self, client, analyst_headers, viewer_headers
    ):
        sid = _make_sql_source(client, analyst_headers, "Consulta fija").json()["data"]["id"]

        r = client.patch(f"/api/v1/data-sources/{sid}/query", headers=viewer_headers,
                         json={"sql": "SELECT 1"})
        assert r.status_code == 403, r.text


class TestReadsStayOpenToViewers:
    """Reading is the viewer role's whole purpose — the fix must not take it away."""

    def test_viewer_can_still_list_sources(self, client, viewer_headers):
        assert client.get("/api/v1/data-sources", headers=viewer_headers).status_code == 200

    def test_viewer_can_still_read_one_source(self, client, analyst_headers, viewer_headers):
        sid = _make_sql_source(client, analyst_headers, "Legible").json()["data"]["id"]
        assert client.get(f"/api/v1/data-sources/{sid}",
                          headers=viewer_headers).status_code == 200
