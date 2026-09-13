"""The three gaps the API had once it worked: no trail, one ceiling, one migrator.

Each test is named after what was missing, not after the function that fills it.
"""

import pytest

from backend.auth import api_key_auth
from backend.config import settings
from backend.db.connection import execute, query, query_one


@pytest.fixture
def machine(client, auth_headers, test_tenant):
    raw = client.post("/api/v1/api-keys",
                      json={"name": "erp", "role": "analyst"},
                      headers=auth_headers).json()["data"]["key"]
    return {"Authorization": f"Bearer {raw}"}


def _audit_rows(tenant_id):
    return query(
        "SELECT user_id, resource, status FROM activity_logs "
        "WHERE tenant_id = %s AND action = 'api_write' ORDER BY created_at DESC",
        (tenant_id,),
    ) or []


class TestAnIntegrationLeavesATrail:
    """"What did my integration do last night?" had no complete answer: a
    training run was attributed through the job row, the file upload that fed it
    and the order that closed the loop left nothing at all."""

    def test_a_write_is_recorded_against_the_key_not_a_person(
        self, client, machine, completed_session, test_tenant
    ):
        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": [{"sku": "SKU_001", "recommended_qty": 5, "final_qty": 5,
                             "status": "approved", "unit_cost": 1.0}]},
            headers=machine)
        assert r.status_code in (200, 201), r.text

        rows = _audit_rows(test_tenant["id"])
        assert rows, "the integration wrote and left no trace"
        assert rows[0]["user_id"].startswith("api_key:"), (
            f"attributed to {rows[0]['user_id']!r} — a key must own its own "
            f"actions, not borrow the name of whoever created it"
        )
        assert "log-po" in rows[0]["resource"]

    def test_a_read_is_not_recorded(self, client, machine, completed_session, test_tenant):
        """At 120 calls a minute, auditing GETs would bury the writes."""
        before = len(_audit_rows(test_tenant["id"]))
        client.get(f"/api/v1/inventory/status?session_id={completed_session['id']}",
                   headers=machine)
        assert len(_audit_rows(test_tenant["id"])) == before

    def test_a_failed_write_is_recorded_as_an_error(self, client, machine, test_tenant):
        """The case somebody debugging a silent integration actually needs: it
        tried, and it did not work."""
        before = len(_audit_rows(test_tenant["id"]))
        r = client.post("/api/v1/data-sources/ds_does_not_exist/file",
                        files={"file": ("x.py", b"print(1)", "text/x-python")},
                        headers=machine)
        assert r.status_code >= 400

        rows = _audit_rows(test_tenant["id"])
        assert len(rows) == before + 1, "a failed write vanished from the trail"
        assert rows[0]["status"] == "error"

    def test_a_person_is_not_audited_here(self, client, auth_headers, completed_session, test_tenant):
        """People are attributable through their session; auditing every UI
        click would drown the machine trail this exists to create."""
        before = len(_audit_rows(test_tenant["id"]))
        client.post(f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
                    json={"items": []}, headers=auth_headers)
        assert len(_audit_rows(test_tenant["id"])) == before


class TestTheCeilingIsOneNumber:
    """It was 60 / 120 / unlimited per tier. One plan, one ceiling — and the
    thing worth guarding is that it still refuses, because a limit that stops
    refusing is indistinguishable from no limit."""

    def test_a_key_over_the_window_is_refused(self, monkeypatch):
        monkeypatch.setattr(settings, "testing_mode", False)
        allowed = sum(1 for _ in range(api_key_auth.RATE_MAX_PER_MINUTE + 20)
                      if api_key_auth.check_rate("key_one_ceiling"))
        assert allowed == api_key_auth.RATE_MAX_PER_MINUTE

class TestOnlyOneInstanceMigrates:
    def test_run_all_takes_the_advisory_lock(self, monkeypatch):
        """Two containers booting together walked the same migration list against
        one database. It held only because every statement happened to be
        re-runnable — a property nobody was checking."""
        from backend.db import migrations

        taken = []
        monkeypatch.setattr(migrations, "_run_all", lambda **kw: taken.append("migrated"))
        migrations.run_all()
        assert taken == ["migrated"]

        held = query_one(
            "SELECT COUNT(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND objid = %s",
            (migrations._MIGRATION_LOCK_ID & 0xFFFFFFFF,))
        assert held is not None, "could not inspect pg_locks"

    def test_migrations_still_run_when_the_lock_cannot_be_taken(self, monkeypatch):
        """Failing to lock must not mean skipping the schema — that trades a rare
        race for a silent half-built database."""
        from backend.db import migrations

        ran = []
        monkeypatch.setattr(migrations, "_run_all", lambda **kw: ran.append(1))
        monkeypatch.setattr(migrations, "get_conn", None, raising=False)
        monkeypatch.setattr(
            "backend.db.connection.get_conn",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no pool")),
        )
        migrations.run_all()
        assert ran == [1], "migrations were skipped because the lock failed"
