"""File-drop sources against the database: what a poll decides, what it writes, and
who may change it. The company's server is replaced by an in-memory transport (the
protocols themselves are pinned in test_file_drop_transports.py)."""

import json
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.file_drop import service
from backend.file_drop.transports import RemoteFile

CSV = b"date,sku,qty\n2026-01-01,A,1\n2026-01-02,B,2\n2026-01-03,C,3\n2026-01-04,D,4\n"


class FakeTransport:
    def __init__(self, files=None):
        self.files = dict(files or {})            # name -> (bytes, mtime)
        self.moves, self.notes = [], {}
        self.deny_move = False

    def list_files(self):
        return [RemoteFile(n, len(b), m) for n, (b, m) in self.files.items()]

    def read(self, name, max_bytes):
        return self.files[name][0]

    def move(self, name, folder):
        if self.deny_move:
            raise PermissionError("no")
        self.moves.append((name, folder))
        self.files.pop(name)

    def put_text(self, folder, name, text):
        self.notes[(folder, name)] = text

    def close(self):
        pass


@pytest.fixture()
def drop(test_tenant, registered_user, monkeypatch):
    """A source (min age 0) wired to a fake server, plus helpers."""
    tid = test_tenant["id"]
    transport = FakeTransport()
    monkeypatch.setattr(service, "open_transport", lambda row, **kw: transport)
    src = service.create_source(tid, registered_user["user"]["id"], {
        "name": f"drop-{uuid4().hex[:6]}", "kind": "sftp",
        "config": {"host": "files.example.com", "username": "u"},
        "secrets": {"password": "hunter2-secret"}, "min_age_seconds": 0,
        "file_pattern": "*.csv"})

    def look():
        return service.poll(service.get_source(tid, src["id"]))

    def ledger():
        return query("SELECT remote_name, status, reason_code, moved FROM file_drop_files "
                     "WHERE source_id = %s ORDER BY seen_at, remote_name", (src["id"],))

    return {"tid": tid, "src": src, "t": transport, "look": look, "ledger": ledger,
            "uid": registered_user["user"]["id"]}


def _dataset(tid, drop_id):
    return query_one("SELECT target_dataset_id FROM file_drop_sources WHERE id = %s",
                     (drop_id,))["target_dataset_id"]


class TestPolling:

    def test_a_file_is_read_only_after_two_agreeing_looks(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        first = drop["look"]()
        assert first["waiting"] == 1 and first["applied"] == 0
        assert drop["ledger"]() == []
        second = drop["look"]()
        assert second["applied"] == 1
        assert [r["status"] for r in drop["ledger"]()] == ["applied"]

    def test_a_file_still_growing_is_never_read(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"]()
        drop["t"].files["sales.csv"] = (CSV + b"2026-01-05,E,5\n", 1000.0)   # grew between looks
        assert drop["look"]()["applied"] == 0
        assert drop["ledger"]() == []

    def test_first_file_creates_the_dataset_and_later_ones_replace_it(self, drop):
        tid = drop["tid"]
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); drop["look"]()
        ds = _dataset(tid, drop["src"]["id"])
        assert ds
        row = query_one("SELECT row_count, source_type FROM datasets WHERE id = %s", (ds,))
        assert row["source_type"] == "file" and row["row_count"] == 4
        bigger = CSV + b"2026-01-05,E,5\n2026-01-06,F,6\n"
        drop["t"].files["sales2.csv"] = (bigger, 2000.0)
        drop["look"](); drop["look"]()
        assert _dataset(tid, drop["src"]["id"]) == ds
        assert query_one("SELECT row_count FROM datasets WHERE id = %s", (ds,))["row_count"] == 6

    def test_applied_files_are_moved_aside_and_never_deleted(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); drop["look"]()
        assert drop["t"].moves == [("sales.csv", "processed")]
        assert drop["ledger"]()[0]["moved"] is True

    def test_the_same_bytes_dropped_again_are_applied_once(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); drop["look"]()
        drop["t"].files["sales.csv"] = (CSV, 3000.0)          # same content, new upload
        drop["look"](); again = drop["look"]()
        assert again["applied"] == 0 and again["already_known"] == 1
        assert len(drop["ledger"]()) == 1

    def test_a_move_the_account_may_not_make_is_reported_and_does_not_reapply(self, drop):
        drop["t"].deny_move = True
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"]()
        done = drop["look"]()
        assert done["applied"] == 1 and done["move_failed"] == 1
        assert drop["ledger"]()[0]["moved"] is False
        again = drop["look"]()                                 # still listed: skipped, not re-applied
        assert again["applied"] == 0
        assert len(drop["ledger"]()) == 1


class TestBrake:

    def _seed(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); drop["look"]()

    def test_a_truncated_file_is_held_and_the_data_is_untouched(self, drop):
        self._seed(drop)
        ds = _dataset(drop["tid"], drop["src"]["id"])
        before = query_one("SELECT row_count, file_path FROM datasets WHERE id = %s", (ds,))
        drop["t"].files["sales2.csv"] = (b"date,sku,qty\n2026-01-01,A,1\n", 2000.0)   # 1 of 4 rows
        drop["look"](); held = drop["look"]()
        assert held["held"] == 1 and held["applied"] == 0
        assert query_one("SELECT row_count, file_path FROM datasets WHERE id = %s", (ds,)) == before
        row = [r for r in drop["ledger"]() if r["remote_name"] == "sales2.csv"][0]
        assert row["status"] == "held" and row["reason_code"] == "file_drop_rows_dropped"
        assert "sales2.csv" in drop["t"].files               # left where it was for the person

    def test_changed_columns_are_held(self, drop):
        self._seed(drop)
        drop["t"].files["sales2.csv"] = (b"date,sku,units\n2026-01-01,A,1\n" * 1 + CSV[20:], 2000.0)
        drop["look"](); drop["look"]()
        row = [r for r in drop["ledger"]() if r["remote_name"] == "sales2.csv"][0]
        assert row["status"] == "held" and row["reason_code"] == "file_drop_columns_changed"

    def test_an_empty_file_is_held(self, drop):
        drop["t"].files["empty.csv"] = (b"date,sku,qty\n", 1000.0)
        drop["look"](); drop["look"]()
        assert drop["ledger"]()[0]["reason_code"] == "file_drop_file_empty"
        assert _dataset(drop["tid"], drop["src"]["id"]) is None

    def test_a_held_file_waits_instead_of_being_judged_every_look(self, drop):
        self._seed(drop)
        drop["t"].files["short.csv"] = (b"date,sku,qty\n2026-01-01,A,1\n", 2000.0)
        drop["look"](); drop["look"]()
        later = drop["look"]()
        assert later["held"] == 0 and later["already_known"] == 1
        assert len([r for r in drop["ledger"]() if r["remote_name"] == "short.csv"]) == 1

    def test_a_person_applies_a_held_file_anyway(self, drop, monkeypatch):
        self._seed(drop)
        ds = _dataset(drop["tid"], drop["src"]["id"])
        drop["t"].files["short.csv"] = (b"date,sku,qty\n2026-01-01,A,1\n", 2000.0)
        drop["look"](); drop["look"]()
        held = query_one("SELECT id FROM file_drop_files WHERE status = 'held' AND source_id = %s",
                         (drop["src"]["id"],))
        result = service.decide(drop["tid"], drop["uid"], held["id"], "apply")
        assert result["status"] == "applied"
        assert query_one("SELECT row_count FROM datasets WHERE id = %s", (ds,))["row_count"] == 1
        done = query_one("SELECT status, decided_by FROM file_drop_files WHERE source_id = %s "
                         "AND remote_name = 'short.csv'", (drop["src"]["id"],))
        assert done["status"] == "applied" and done["decided_by"] == drop["uid"]

    def test_a_person_rejects_a_held_file(self, drop):
        self._seed(drop)
        ds = _dataset(drop["tid"], drop["src"]["id"])
        drop["t"].files["short.csv"] = (b"date,sku,qty\n2026-01-01,A,1\n", 2000.0)
        drop["look"](); drop["look"]()
        held = query_one("SELECT id FROM file_drop_files WHERE status = 'held' AND source_id = %s",
                         (drop["src"]["id"],))
        service.decide(drop["tid"], drop["uid"], held["id"], "reject")
        assert query_one("SELECT status, reason_code FROM file_drop_files WHERE id = %s",
                         (held["id"],)) == {"status": "rejected",
                                            "reason_code": "file_drop_rejected_by_person"}
        assert ("rejected", "short.csv.reason.txt") in drop["t"].notes
        assert query_one("SELECT row_count FROM datasets WHERE id = %s", (ds,))["row_count"] == 4

    def test_only_a_held_file_can_be_decided(self, drop):
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); drop["look"]()
        fid = query_one("SELECT id FROM file_drop_files WHERE source_id = %s",
                        (drop["src"]["id"],))["id"]
        with pytest.raises(AppError) as exc:
            service.decide(drop["tid"], drop["uid"], fid, "apply")
        assert exc.value.code == "file_drop_file_not_held"


class TestRejections:

    def test_an_unreadable_file_is_rejected_with_a_note_beside_it(self, drop):
        service.update_source(drop["tid"], drop["src"]["id"], {"file_pattern": "*.xlsx"})
        drop["t"].files["bad.xlsx"] = (b"this is not a workbook", 1000.0)
        drop["look"](); done = drop["look"]()
        assert done["rejected"] == 1
        row = drop["ledger"]()[0]
        assert row["status"] == "rejected" and row["reason_code"] == "file_drop_file_unreadable"
        assert row["moved"] is True and drop["t"].moves == [("bad.xlsx", "rejected")]
        assert "file_drop_file_unreadable" in drop["t"].notes[("rejected", "bad.xlsx.reason.txt")]

    def test_a_file_with_a_header_and_no_rows_is_held(self, drop):
        drop["t"].files["noheaderrows.csv"] = (b"\xff\xfe\x00\x00", 1000.0)
        drop["look"](); drop["look"]()
        row = drop["ledger"]()[0]
        assert row["status"] == "held" and row["reason_code"] == "file_drop_file_empty"

    def test_a_file_over_the_size_ceiling_is_rejected_unread_with_its_code(self, drop, monkeypatch):
        from backend.datasources import service as ds
        monkeypatch.setattr(ds, "MAX_FILE_MB", 0)
        drop["t"].files["sales.csv"] = (CSV, 1000.0)
        drop["look"](); done = drop["look"]()
        assert done["rejected"] == 1
        assert drop["ledger"]()[0]["reason_code"] == "file_drop_file_too_large"
        assert ("rejected", "sales.csv.reason.txt") in drop["t"].notes
        assert _dataset(drop["tid"], drop["src"]["id"]) is None

    def test_files_that_do_not_match_the_pattern_are_ignored(self, drop):
        drop["t"].files["notes.txt"] = (b"hi", 1000.0)
        drop["t"].files["stock.xlsx"] = (b"PK", 1000.0)
        drop["look"](); done = drop["look"]()
        assert done["seen"] == 0 and drop["ledger"]() == []


class TestSourceState:

    def test_a_refused_login_blocks_the_source_and_it_is_not_claimed_again(
            self, drop, monkeypatch):
        def refuse(row, **kw):
            raise AppError("file_drop_auth_failed", "refused", status_code=401)
        monkeypatch.setattr(service, "open_transport", refuse)
        result = drop["look"]()
        assert result["error"] == "file_drop_auth_failed"
        row = service.get_source(drop["tid"], drop["src"]["id"])
        assert row["status"] == "blocked" and row["last_error_code"] == "file_drop_auth_failed"
        execute("UPDATE file_drop_sources SET next_run_at = NOW() - interval '1 minute' "
                "WHERE id = %s", (drop["src"]["id"],))
        assert drop["src"]["id"] not in [r["id"] for r in service.claim_due(50)]

    def test_a_network_error_retries_later_instead_of_blocking(self, drop, monkeypatch):
        def flaky(row, **kw):
            raise AppError("file_drop_connection_failed", "down", status_code=400)
        monkeypatch.setattr(service, "open_transport", flaky)
        drop["look"]()
        assert service.get_source(drop["tid"], drop["src"]["id"])["status"] == "error"

    def test_editing_a_blocked_source_makes_it_due_again(self, drop, monkeypatch):
        execute("UPDATE file_drop_sources SET status = 'blocked', last_error_code = 'x' "
                "WHERE id = %s", (drop["src"]["id"],))
        service.update_source(drop["tid"], drop["src"]["id"], {"secrets": {"password": "new"}})
        row = service.get_source(drop["tid"], drop["src"]["id"])
        assert row["status"] == "new" and row["last_error_code"] is None

    def test_changing_the_host_forgets_the_pinned_key(self, drop):
        execute("UPDATE file_drop_sources SET host_key_fingerprint = 'SHA256:abc', "
                "host_key_pinned_at = NOW() WHERE id = %s", (drop["src"]["id"],))
        service.update_source(drop["tid"], drop["src"]["id"], {"config": {"host": "other.example.com"}})
        assert service.get_source(drop["tid"], drop["src"]["id"])["host_key_fingerprint"] is None

    def test_credentials_are_encrypted_at_rest_and_never_returned(self, drop):
        stored = query_one("SELECT secrets FROM file_drop_sources WHERE id = %s",
                           (drop["src"]["id"],))["secrets"]
        assert "hunter2-secret" not in stored
        assert "hunter2-secret" not in json.dumps(drop["src"])
        assert "hunter2-secret" not in json.dumps(service.list_sources(drop["tid"]))
        assert drop["src"]["has_secret"] is True

    def test_a_private_host_is_refused_when_the_installation_does_not_allow_it(
            self, test_tenant, registered_user, monkeypatch):
        from backend.datasources import network
        monkeypatch.setattr(network, "allow_private_hosts", lambda: False)
        with pytest.raises(AppError):
            service.create_source(test_tenant["id"], registered_user["user"]["id"], {
                "name": "evil", "kind": "sftp", "config": {"host": "169.254.169.254", "username": "u"},
                "secrets": {"password": "x"}})


class TestApi:

    BODY = {"name": "api-drop", "kind": "s3",
            "config": {"bucket": "b", "endpoint": "", "prefix": "in"},
            "secrets": {"access_key": "AK", "secret_key": "SK-very-secret"}}

    def test_viewer_cannot_add_a_source(self, client, viewer_headers, test_tenant):
        resp = client.post("/api/v1/file-drop/sources", json=self.BODY, headers=viewer_headers)
        assert resp.status_code == 403
        assert query("SELECT 1 FROM file_drop_sources WHERE tenant_id = %s",
                     (test_tenant["id"],)) == []

    def test_analyst_cannot_manage_credentials_but_admin_can(
            self, client, analyst_headers, auth_headers, test_tenant):
        denied = client.post("/api/v1/file-drop/sources", json=self.BODY, headers=analyst_headers)
        assert denied.status_code == 403
        made = client.post("/api/v1/file-drop/sources", json=self.BODY, headers=auth_headers)
        assert made.status_code == 201, made.text
        body = made.json()["data"]
        assert "SK-very-secret" not in made.text and body["has_secret"] is True
        row = query_one("SELECT secrets, kind FROM file_drop_sources WHERE id = %s", (body["id"],))
        assert row["kind"] == "s3" and "SK-very-secret" not in row["secrets"]

    def test_viewer_cannot_decide_a_held_file_and_nothing_changes(
            self, client, viewer_headers, drop):
        execute("INSERT INTO file_drop_files (tenant_id, source_id, remote_name, content_hash, "
                "status) VALUES (%s, %s, 'x.csv', 'h', 'held')", (drop["tid"], drop["src"]["id"]))
        fid = query_one("SELECT id FROM file_drop_files WHERE source_id = %s",
                        (drop["src"]["id"],))["id"]
        resp = client.post(f"/api/v1/file-drop/files/{fid}/decision", json={"action": "reject"},
                           headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one("SELECT status FROM file_drop_files WHERE id = %s", (fid,))["status"] == "held"

    def test_another_tenants_source_is_not_found(self, client, auth_headers, drop):
        from backend.tenants.service import create_tenant
        other = create_tenant("fd-other")["id"]
        try:
            mine = service.create_source(other, None, {
                "name": "theirs", "kind": "s3", "config": {"bucket": "b"},
                "secrets": {"access_key": "a", "secret_key": "b"}})
            resp = client.post(f"/api/v1/file-drop/sources/{mine['id']}/test", headers=auth_headers)
            assert resp.status_code == 404
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other,))
