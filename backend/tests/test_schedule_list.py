"""`GET /schedules` — what is armed, without having to guess the session.

The automation screen edits ONE session's schedule and opens on the first
completed session it finds. Driving it as an admin: the retrain was armed on
"Demo Faro", the form opened on "Corrida buena 200 SKUs", and the page therefore
showed the empty "create a schedule" form — no sign that a schedule existed at
all, and nothing stopping a second one being armed. The DB row was there the
whole time.
"""

from backend.db.connection import execute, query_one


def _arm(tenant_id: str, session_id: str, cron: str = "0 * * * *",
         enabled: bool = True, last_error: str | None = None) -> str:
    row = query_one(
        """INSERT INTO scheduled_jobs
               (id, tenant_id, session_id, cron_expr, next_run, enabled, last_error)
           VALUES (gen_random_uuid()::text, %s, %s, %s, NOW() + interval '1 hour', %s, %s)
           RETURNING id""",
        (tenant_id, session_id, cron, enabled, last_error),
    )
    return row["id"]


class TestListSchedules:
    def test_it_returns_the_schedule_with_its_session_name(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """The name is the point: an id tells the admin nothing about which
        retrain they are looking at."""
        session_id = completed_session["id"]
        _arm(test_tenant["id"], session_id)

        r = client.get("/api/v1/schedules", headers=auth_headers)
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        mine = [x for x in rows if x["session_id"] == session_id]
        assert len(mine) == 1
        assert mine[0]["session_name"], "no session name to show the admin"
        assert mine[0]["cron_expr"] == "0 * * * *"
        assert mine[0]["next_run"]

    def test_a_failing_trigger_is_visible_from_the_list(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """A schedule that has been failing for weeks used to look healthy."""
        session_id = completed_session["id"]
        _arm(test_tenant["id"], session_id, last_error="croniter exploded")

        rows = client.get("/api/v1/schedules", headers=auth_headers).json()["data"]
        mine = next(x for x in rows if x["session_id"] == session_id)
        assert mine["last_error"] == "croniter exploded"

    def test_a_paused_schedule_is_still_listed(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """Disabled is a state to SEE, not a reason to hide the row — otherwise
        'nothing is scheduled' and 'it is scheduled but off' look identical."""
        session_id = completed_session["id"]
        _arm(test_tenant["id"], session_id, enabled=False)

        rows = client.get("/api/v1/schedules", headers=auth_headers).json()["data"]
        mine = next(x for x in rows if x["session_id"] == session_id)
        assert mine["enabled"] is False

    def test_it_lists_nothing_when_nothing_is_armed(self, client, auth_headers):
        rows = client.get("/api/v1/schedules", headers=auth_headers).json()["data"]
        assert rows == []

    def test_another_tenants_schedule_is_not_visible(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """The join is on (session, tenant); a cross-tenant leak here would hand
        one company another's session names."""
        session_id = completed_session["id"]
        _arm("ten_someone_else", session_id)
        try:
            rows = client.get("/api/v1/schedules", headers=auth_headers).json()["data"]
            assert all(x["session_id"] != session_id for x in rows) or all(
                x["session_name"] for x in rows)
            # Nothing armed for THIS tenant, so nothing may come back.
            assert rows == []
        finally:
            execute("DELETE FROM scheduled_jobs WHERE tenant_id = %s", ("ten_someone_else",))

    def test_a_viewer_can_read_the_list(self, client, viewer_headers, test_tenant,
                                        completed_session):
        """Reading what is scheduled is not a mutation; a viewer may look."""
        session_id = completed_session["id"]
        _arm(test_tenant["id"], session_id)
        r = client.get("/api/v1/schedules", headers=viewer_headers)
        assert r.status_code == 200, r.text
