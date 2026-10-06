"""Contract renewal tracking against Postgres: the Python-owned half.

Python owns the schema and still serves create / revise / status, so it must
carry the renewal fields (notice period, auto-renew, alert lead times) through
every revision, and it runs the daily alert pass. The renewals list, the
commitment comparison and the renew action are Rust-only (see
`tests/contract/contract_test.py`); their maths is pinned by
`test_contract_renewal_pure.py`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.activity.events import EVENTS, REASONS
from backend.db.connection import execute, query, query_one
from backend.inventory import contract_renewal_alerts as alerts
from backend.notifications import alert_history

URL = "/api/v1/supply-contracts"


def _body(**over):
    today = date.today()
    b = {
        "customer": "Renewal Corp",
        "lines": [{"sku": "SKU-R", "total_quantity": 1200}],
        "period_start": (today - timedelta(days=60)).isoformat(),
        "period_end": (today + timedelta(days=100)).isoformat(),
        "schedule_kind": "monthly",
        "status": "active",
    }
    b.update(over)
    return b


def _create(client, headers, **over):
    r = client.post(URL, json=_body(**over), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _row(root_id, revision=None):
    sql = "SELECT * FROM supply_contracts WHERE root_id = %s"
    params = [root_id]
    if revision is None:
        sql += " AND superseded_by IS NULL"
    else:
        sql += " AND revision = %s"
        params.append(revision)
    return query_one(sql, tuple(params))


def _alert_rows(tid, root_id=None):
    sql = ("SELECT context, user_id, resource, status FROM activity_logs "
           "WHERE tenant_id = %s AND action = 'supply_contract.renewal_due'")
    params = [tid]
    if root_id:
        sql += " AND resource = %s"
        params.append(root_id)
    return query(sql + " ORDER BY created_at", tuple(params))


class TestFieldsSurviveTheRevisionChain:

    def test_defaults_are_not_a_choice(self, client, analyst_headers):
        c = _create(client, analyst_headers)
        row = _row(c["root_id"])
        assert (row["notice_days"], row["auto_renew"], row["renewal_lead_days"],
                row["renewed_from_root_id"]) == (None, False, None, None)
        assert c["renewal_lead_days_is_default"] is True
        assert c["renewal_lead_days_effective"] == [60, 30, 7]
        assert c["renewal"]["bucket"] == "upcoming" and c["renewal"]["days_to_expiry"] == 100

    def test_create_stores_the_fields_and_the_view_uses_them(self, client, analyst_headers):
        end = date.today() + timedelta(days=40)
        c = _create(client, analyst_headers, period_end=end.isoformat(), notice_days=30,
                    auto_renew=True, renewal_lead_days=[7, 14, 14, 45])
        row = _row(c["root_id"])
        assert (row["notice_days"], row["auto_renew"]) == (30, True)
        assert row["renewal_lead_days"] == [45, 14, 7]
        assert c["renewal_lead_days_is_default"] is False
        assert c["renewal_lead_days_effective"] == [45, 14, 7]
        assert c["renewal"]["notice_deadline"] == (end - timedelta(days=30)).isoformat()
        assert c["renewal"]["days_to_notice"] == 10
        assert c["renewal"]["auto_renew"] is True and c["renewal"]["bucket"] == "due_soon"

    @pytest.mark.parametrize("field,value,code", [
        ("notice_days", 5000, None),               # pydantic bound (422 before the service)
        ("renewal_lead_days", [0], "supply_contract_lead_days_invalid"),
        ("renewal_lead_days", [731], "supply_contract_lead_days_invalid"),
        ("renewal_lead_days", [], "supply_contract_lead_days_invalid"),
        ("renewal_lead_days", [1, 2, 3, 4, 5, 6, 7], None),
    ])
    def test_invalid_renewal_fields_are_refused_and_nothing_is_written(
            self, client, analyst_headers, registered_user, field, value, code):
        tid = registered_user["tenant"]["id"]
        r = client.post(URL, json=_body(**{field: value}), headers=analyst_headers)
        assert r.status_code == 422, r.text
        if code:
            assert r.json()["error_code"] == code
        assert query("SELECT 1 FROM supply_contracts WHERE tenant_id = %s", (tid,)) == []

    def test_a_revision_that_omits_the_fields_keeps_them(
            self, client, analyst_headers, viewer_headers):
        c = _create(client, analyst_headers, notice_days=45, auto_renew=True,
                    renewal_lead_days=[90, 30])
        body = _body(customer="Renamed Corp")
        for k in ("status", "notice_days", "auto_renew", "renewal_lead_days"):
            body.pop(k, None)
        body["expected_revision"] = 1
        denied = client.post(f"{URL}/{c['root_id']}/revisions", json=body, headers=viewer_headers)
        assert denied.status_code == 403
        assert _row(c["root_id"])["revision"] == 1
        r = client.post(f"{URL}/{c['root_id']}/revisions", json=body, headers=analyst_headers)
        assert r.status_code == 200, r.text
        row = _row(c["root_id"])
        assert row["revision"] == 2 and row["customer"] == "Renamed Corp"
        assert (row["notice_days"], row["auto_renew"], row["renewal_lead_days"]) == (45, True, [90, 30])

    def test_a_revision_that_sends_them_changes_them_and_null_means_default_again(
            self, client, analyst_headers):
        c = _create(client, analyst_headers, notice_days=45, auto_renew=True,
                    renewal_lead_days=[90, 30])
        body = _body(notice_days=None, auto_renew=False, renewal_lead_days=None)
        body.pop("status")
        body["expected_revision"] = 1
        r = client.post(f"{URL}/{c['root_id']}/revisions", json=body, headers=analyst_headers)
        assert r.status_code == 200, r.text
        row = _row(c["root_id"])
        assert (row["notice_days"], row["auto_renew"], row["renewal_lead_days"]) == (None, False, None)
        assert r.json()["data"]["renewal_lead_days_is_default"] is True

    def test_a_status_change_and_a_revision_carry_renewed_from(self, client, analyst_headers):
        c = _create(client, analyst_headers, status="draft", notice_days=10)
        # What the Rust renew action writes on the new contract.
        execute("UPDATE supply_contracts SET renewed_from_root_id = %s WHERE id = %s",
                ("previous-lineage", c["id"]))
        r = client.post(f"{URL}/{c['root_id']}/status",
                        json={"status": "active", "expected_revision": 1}, headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert _row(c["root_id"])["renewed_from_root_id"] == "previous-lineage"
        assert _row(c["root_id"])["notice_days"] == 10
        body = _body(customer="Again")
        body.pop("status")
        body["expected_revision"] = 2
        r = client.post(f"{URL}/{c['root_id']}/revisions", json=body, headers=analyst_headers)
        assert r.status_code == 200, r.text
        row = _row(c["root_id"])
        assert row["revision"] == 3 and row["renewed_from_root_id"] == "previous-lineage"
        assert row["notice_days"] == 10


class TestDailyAlerts:

    def test_the_alert_is_registered_and_reaches_the_bell(self, client, analyst_headers,
                                                          registered_user):
        spec = EVENTS["supply_contract.renewal_due"]
        assert spec.severity == "warning"
        assert "supply_contract.renewal_due" in alert_history._bell_event_actions()
        for r in ("contract_expiring", "contract_notice_deadline", "contract_expired"):
            assert r in REASONS
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers, period_end=(date.today() + timedelta(days=5)).isoformat())
        summary = alerts.alert_renewals(tid)
        assert summary["alerted"] == 1
        bell = client.get("/api/v1/alerts", headers=analyst_headers).json()["data"]
        entries = [e for e in bell["items"] if e.get("action") == "supply_contract.renewal_due"]
        assert len(entries) == 1
        e = entries[0]
        assert e["severity"] == "warning" and e["reason"] == "contract_expiring"
        assert e["details"]["customer"] == "Renewal Corp" and e["details"]["lead_days"] == 7
        assert c["root_id"]

    def test_countdown_60_30_7_then_expired_each_exactly_once(self, client, analyst_headers,
                                                              registered_user):
        tid = registered_user["tenant"]["id"]
        end = date.today() + timedelta(days=200)
        c = _create(client, analyst_headers, period_end=end.isoformat())
        root = c["root_id"]
        seq = [(61, None), (60, 60), (59, None), (31, None), (30, 30), (10, None), (7, 7),
               (3, None), (-1, "expired"), (-2, None)]
        got = []
        for days_left, expect in seq:
            today = end - timedelta(days=days_left)
            s = alerts.alert_renewals(tid, today=today)
            assert s["failed"] == 0
            rows = _alert_rows(tid, root)
            new = rows[len(got):]
            got = rows
            if expect is None:
                assert new == [], (days_left, new)
            else:
                assert len(new) == 1, (days_left, new)
                ctx = new[0]["context"]
                assert ctx["expiry_date"] == end.isoformat()
                if expect == "expired":
                    assert ctx["reason"] == "contract_expired" and "lead_days" not in ctx
                else:
                    assert ctx["lead_days"] == expect and ctx["reason"] == "contract_expiring"
                assert ctx["severity"] == "warning" and ctx["kind"] == "purchase"
                assert new[0]["user_id"] == "system" and new[0]["status"] == "success"
        assert len(_alert_rows(tid, root)) == 4
        # Re-running the same day (a catch-up, a second worker) writes nothing.
        before = len(_alert_rows(tid, root))
        alerts.alert_renewals(tid, today=end + timedelta(days=5))
        alerts.alert_renewals(tid, today=end - timedelta(days=7))
        assert len(_alert_rows(tid, root)) == before

    def test_a_contract_entered_late_raises_one_alert_not_three(self, client, analyst_headers,
                                                                registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers, period_end=(date.today() + timedelta(days=4)).isoformat())
        alerts.alert_renewals(tid)
        alerts.alert_renewals(tid)
        rows = _alert_rows(tid, c["root_id"])
        assert [r["context"]["lead_days"] for r in rows] == [7]

    def test_the_notice_deadline_drives_the_countdown(self, client, analyst_headers,
                                                      registered_user):
        tid = registered_user["tenant"]["id"]
        end = date.today() + timedelta(days=37)
        c = _create(client, analyst_headers, period_end=end.isoformat(), notice_days=30,
                    auto_renew=True)
        # 37 days to the end, 7 to the notice deadline: the 7-day lead is crossed.
        alerts.alert_renewals(tid)
        rows = _alert_rows(tid, c["root_id"])
        assert len(rows) == 1
        ctx = rows[0]["context"]
        assert ctx["reason"] == "contract_notice_deadline" and ctx["lead_days"] == 7
        assert ctx["days_left"] == 37 and ctx["auto_renew"] is True
        assert ctx["notice_deadline"] == (end - timedelta(days=30)).isoformat()

    def test_configured_lead_times_replace_the_default(self, client, analyst_headers,
                                                       registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers,
                    period_end=(date.today() + timedelta(days=50)).isoformat(),
                    renewal_lead_days=[14])
        alerts.alert_renewals(tid)
        assert _alert_rows(tid, c["root_id"]) == []        # default 60 would have fired
        alerts.alert_renewals(tid, today=date.today() + timedelta(days=36))
        assert [r["context"]["lead_days"] for r in _alert_rows(tid, c["root_id"])] == [14]

    def test_extending_the_term_restarts_the_countdown(self, client, analyst_headers,
                                                       registered_user):
        tid = registered_user["tenant"]["id"]
        end = date.today() + timedelta(days=20)
        c = _create(client, analyst_headers, period_end=end.isoformat())
        alerts.alert_renewals(tid)
        assert [r["context"]["lead_days"] for r in _alert_rows(tid, c["root_id"])] == [30]
        body = _body(period_end=(end + timedelta(days=100)).isoformat())
        body.pop("status")
        body["expected_revision"] = 1
        assert client.post(f"{URL}/{c['root_id']}/revisions", json=body,
                           headers=analyst_headers).status_code == 200
        alerts.alert_renewals(tid, today=end + timedelta(days=100) - timedelta(days=30))
        rows = _alert_rows(tid, c["root_id"])
        assert [r["context"]["expiry_date"] for r in rows] == [
            end.isoformat(), (end + timedelta(days=100)).isoformat()]

    def test_only_active_unrenewed_contracts_are_alerted(self, client, analyst_headers,
                                                         registered_user):
        tid = registered_user["tenant"]["id"]
        soon = (date.today() + timedelta(days=3)).isoformat()
        active = _create(client, analyst_headers, customer="Active", period_end=soon)
        draft = _create(client, analyst_headers, customer="Draft", period_end=soon, status="draft")
        closed = _create(client, analyst_headers, customer="Closed", period_end=soon)
        assert client.post(f"{URL}/{closed['root_id']}/status",
                           json={"status": "closed", "expected_revision": 1},
                           headers=analyst_headers).status_code == 200
        renewed = _create(client, analyst_headers, customer="Renewed", period_end=soon)
        successor = _create(client, analyst_headers, customer="Next term", status="draft")
        execute("UPDATE supply_contracts SET renewed_from_root_id = %s WHERE id = %s",
                (renewed["root_id"], successor["id"]))
        s = alerts.alert_renewals(tid)
        who = {r["resource"] for r in _alert_rows(tid)}
        assert who == {active["root_id"]}, who
        assert s["contracts"] == 1 and s["alerted"] == 1
        assert draft["root_id"] not in who and closed["root_id"] not in who

    def test_a_cancelled_renewal_does_not_silence_the_alert(self, client, analyst_headers,
                                                            registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers, period_end=(date.today() + timedelta(days=3)).isoformat())
        nxt = _create(client, analyst_headers, customer="Next", status="draft")
        execute("UPDATE supply_contracts SET renewed_from_root_id = %s WHERE id = %s",
                (c["root_id"], nxt["id"]))
        alerts.alert_renewals(tid)
        assert _alert_rows(tid, c["root_id"]) == []
        assert client.post(f"{URL}/{nxt['root_id']}/status",
                           json={"status": "cancelled", "expected_revision": 1},
                           headers=analyst_headers).status_code == 200
        alerts.alert_renewals(tid)
        assert len(_alert_rows(tid, c["root_id"])) == 1

    def test_one_failing_contract_does_not_stop_the_pass(self, client, analyst_headers,
                                                         registered_user, monkeypatch):
        tid = registered_user["tenant"]["id"]
        soon = (date.today() + timedelta(days=3)).isoformat()
        bad = _create(client, analyst_headers, customer="Bad", period_end=soon)
        good = _create(client, analyst_headers, customer="Good", period_end=soon)
        real = alerts.record_event

        def flaky(tenant_id, user_id, action, **kw):
            if kw["details"]["customer"] == "Bad":
                raise RuntimeError("boom")
            return real(tenant_id, user_id, action, **kw)

        monkeypatch.setattr(alerts, "record_event", flaky)
        s = alerts.alert_renewals(tid)
        assert s["failed"] == 1 and s["alerted"] == 1
        assert {r["resource"] for r in _alert_rows(tid)} == {good["root_id"]}
        # The failure left no half-written record: the next pass raises it.
        monkeypatch.setattr(alerts, "record_event", real)
        alerts.alert_renewals(tid)
        assert {r["resource"] for r in _alert_rows(tid)} == {good["root_id"], bad["root_id"]}

    def test_the_daily_loop_runs_the_pass(self):
        import inspect
        from backend.workers import worker
        src = inspect.getsource(worker._inventory_alert_loop)
        assert "run_daily_contract_renewal_alerts" in src
        assert src.index("run_daily_contract_materialisation") < src.index(
            "run_daily_contract_renewal_alerts")
