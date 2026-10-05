"""Sales by e-mail: a forwarded report is ingested like an upload.

The state that matters is asserted in the database, not echoed from responses:
which datasets exist, which message rows were written and with which outcome,
which events the tenant's feed received. Every webhook test goes through the
real HTTP route with a real signature.
"""
import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one

SECRET = "inbound-test-secret-0123456789"
DOMAIN = "in.example.test"
CSV = b"sku,date,demand\nA1,2024-01-01,5\nA1,2024-01-02,7\n"
CSV_OTHER_HEADER = b"producto,fecha,cantidad\nA1,2024-01-01,5\n"


@pytest.fixture(autouse=True)
def _inbound_on(monkeypatch, tmp_path):
    from backend.config import settings
    monkeypatch.setattr(settings, "inbound_email_domain", DOMAIN)
    monkeypatch.setattr(settings, "inbound_email_secret", SECRET)
    # Real plan ceilings, and files in a throwaway directory.
    monkeypatch.setattr(settings, "testing_mode", False)
    monkeypatch.setattr(settings, "storage_path", tmp_path)


def _sign(body: bytes, secret: str = SECRET, stamp: int | None = None) -> dict:
    t = int(time.time()) if stamp is None else stamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-StockAI-Signature": f"t={t},v1={mac}", "Content-Type": "application/json"}


def _payload(token, sender, content=CSV, filename="ventas.csv", message_id=None):
    return {
        "from": sender,
        "to": [f"sales+{token}@{DOMAIN}"],
        "message_id": message_id or f"<{uuid4().hex}@mail.test>",
        "attachments": [{
            "filename": filename,
            "content_base64": base64.b64encode(content).decode(),
            "content_type": "text/csv",
        }],
    }


def _post(client, payload, headers=None):
    body = json.dumps(payload).encode()
    return client.post("/api/v1/inbound/email", content=body, headers=headers or _sign(body))


def _token(tenant_id):
    from backend.inbound_email import service
    return service.get_or_create(tenant_id)["token"]


def _confirm_mapping(tenant_id, user_id="u1"):
    """A session whose columns the user already confirmed."""
    sid = f"sess_{uuid4().hex[:10]}"
    execute("INSERT INTO sessions (id, tenant_id, name) VALUES (%s, %s, 'prior')",
            (sid, tenant_id))
    execute(
        "INSERT INTO session_configs (session_id, tenant_id, columns_cfg) "
        "VALUES (%s, %s, %s)",
        (sid, tenant_id, json.dumps({"sku_column": "sku", "date_column": "date",
                                     "target_column": "demand"})),
    )
    return sid


def _rows(tenant_id):
    return query("SELECT * FROM inbound_email_messages WHERE tenant_id = %s "
                 "ORDER BY received_at", (tenant_id,))


def _datasets(tenant_id):
    return query("SELECT * FROM datasets WHERE tenant_id = %s", (tenant_id,))


class TestDisabledInstallation:
    def test_webhook_answers_a_structured_error_and_writes_nothing(
            self, client, test_tenant, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "inbound_email_secret", "")
        body = json.dumps({"from": "a@b.co"}).encode()
        r = client.post("/api/v1/inbound/email", content=body, headers=_sign(body))
        assert r.status_code == 503
        assert r.json()["error_code"] == "inbound_email_disabled"
        assert _rows(test_tenant["id"]) == []

    def test_card_says_so_and_no_token_is_minted(
            self, client, auth_headers, test_tenant, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "inbound_email_domain", "")
        r = client.get("/api/v1/inbound-email", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["enabled"] is False and data["address"] is None
        assert query_one("SELECT 1 FROM inbound_email_addresses WHERE tenant_id=%s",
                         (test_tenant["id"],)) is None

    def test_management_writes_are_refused_when_disabled(
            self, client, auth_headers, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "inbound_email_domain", "")
        r = client.post("/api/v1/inbound-email/regenerate", headers=auth_headers)
        assert r.status_code == 503 and r.json()["error_code"] == "inbound_email_disabled"


class TestWebhookAuthentication:
    def test_no_signature_is_refused_and_nothing_is_stored(
            self, client, registered_user, test_tenant):
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        r = client.post("/api/v1/inbound/email", content=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 401
        assert r.json()["error_code"] == "inbound_email_unauthorized"
        assert _rows(test_tenant["id"]) == [] and _datasets(test_tenant["id"]) == []

    def test_wrong_secret_is_refused(self, client, registered_user, test_tenant):
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        body = json.dumps(payload).encode()
        r = client.post("/api/v1/inbound/email", content=body,
                        headers=_sign(body, secret="not-the-secret"))
        assert r.status_code == 401
        assert _datasets(test_tenant["id"]) == []

    def test_signature_over_a_different_body_is_refused(
            self, client, registered_user, test_tenant):
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        body = json.dumps(payload).encode()
        headers = _sign(b"something else")
        r = client.post("/api/v1/inbound/email", content=body, headers=headers)
        assert r.status_code == 401

    def test_a_replayed_old_signature_is_refused(
            self, client, registered_user, test_tenant):
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        body = json.dumps(payload).encode()
        r = client.post("/api/v1/inbound/email", content=body,
                        headers=_sign(body, stamp=int(time.time()) - 3600))
        assert r.status_code == 401
        assert _rows(test_tenant["id"]) == []

    def test_basic_auth_with_the_secret_is_accepted(
            self, client, registered_user, test_tenant):
        _confirm_mapping(test_tenant["id"])
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        cred = base64.b64encode(f"provider:{SECRET}".encode()).decode()
        r = client.post("/api/v1/inbound/email", content=json.dumps(payload).encode(),
                        headers={"Authorization": f"Basic {cred}",
                                 "Content-Type": "application/json"})
        assert r.status_code == 200
        assert len(_datasets(test_tenant["id"])) == 1

    def test_basic_auth_with_a_wrong_password_is_refused(
            self, client, registered_user, test_tenant):
        payload = _payload(_token(test_tenant["id"]), registered_user["email"])
        cred = base64.b64encode(b"provider:nope").decode()
        r = client.post("/api/v1/inbound/email", content=json.dumps(payload).encode(),
                        headers={"Authorization": f"Basic {cred}",
                                 "Content-Type": "application/json"})
        assert r.status_code == 401

    def test_content_type_outside_the_allow_list_is_refused(self, client):
        r = client.post("/api/v1/inbound/email", content=b"hello",
                        headers={**_sign(b"hello"), "Content-Type": "text/plain"})
        assert r.status_code == 415
        assert r.json()["error_code"] == "inbound_email_unsupported_content_type"

    def test_an_oversized_body_is_refused_before_it_is_read(
            self, client, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "max_upload_size_mb", 1)
        body = b"x" * (3 * 1024 * 1024)
        r = client.post("/api/v1/inbound/email", content=body, headers=_sign(body))
        assert r.status_code == 413
        assert r.json()["error_code"] == "inbound_email_too_large"

    def test_garbage_json_is_a_structured_400(self, client):
        body = b"{not json"
        r = client.post("/api/v1/inbound/email", content=body, headers=_sign(body))
        assert r.status_code == 400
        assert r.json()["error_code"] == "inbound_email_malformed"


class TestRouting:
    def test_unknown_token_is_dropped_without_any_row(self, client, test_tenant):
        r = _post(client, _payload("deadbeef" * 3, "x@y.co"))
        assert r.status_code == 200
        assert r.json()["data"] == {"outcome": "rejected", "reason": "unknown_address"}
        assert _rows(test_tenant["id"]) == []

    def test_unknown_sender_is_logged_without_reply_and_stores_nothing(
            self, client, test_tenant):
        sent = []
        import backend.notifications.email as mail
        orig = mail._send
        mail._send = lambda *a, **k: sent.append(a) or True
        try:
            r = _post(client, _payload(_token(test_tenant["id"]), "stranger@spam.test"))
        finally:
            mail._send = orig
        assert r.status_code == 200
        assert r.json()["data"]["reason"] == "unknown_sender"
        rows = _rows(test_tenant["id"])
        assert len(rows) == 1 and rows[0]["outcome"] == "rejected"
        assert rows[0]["reason"] == "unknown_sender" and rows[0]["dataset_id"] is None
        assert _datasets(test_tenant["id"]) == []
        assert sent == []                      # never answered: no backscatter
        assert query_one("SELECT 1 FROM activity_logs WHERE tenant_id=%s "
                         "AND action LIKE 'inbound_email.%%'", (test_tenant["id"],)) is None

    def test_a_viewer_mailbox_is_not_a_sender(
            self, client, viewer_user, test_tenant):
        r = _post(client, _payload(_token(test_tenant["id"]), viewer_user["email"]))
        assert r.json()["data"]["reason"] == "unknown_sender"
        assert _datasets(test_tenant["id"]) == []

    def test_cross_tenant_a_foreign_user_cannot_use_my_address(
            self, client, test_tenant, make_tenant_user_headers):
        from backend.users import service as user_svc  # noqa: F401
        other_headers, other_tenant = make_tenant_user_headers(
            role="admin", return_tenant_id=True)
        other_email = query_one("SELECT email FROM users WHERE tenant_id=%s",
                                (other_tenant,))["email"]
        r = _post(client, _payload(_token(test_tenant["id"]), other_email))
        assert r.json()["data"]["reason"] == "unknown_sender"
        assert _datasets(test_tenant["id"]) == [] and _datasets(other_tenant) == []
        assert _rows(other_tenant) == []


class TestIngest:
    def test_happy_path_creates_the_dataset_the_log_row_and_the_trail(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        r = _post(client, _payload(_token(tid), registered_user["email"]))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "ingested"

        ds = _datasets(tid)
        assert len(ds) == 1 and ds[0]["original_filename"] == "ventas.csv"
        assert ds[0]["size_bytes"] == len(CSV) and ds[0]["uploaded_by"] == "inbound_email"
        from pathlib import Path
        assert Path(ds[0]["file_path"]).read_bytes() == CSV

        rows = _rows(tid)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ingested" and rows[0]["dataset_id"] == ds[0]["id"]
        assert rows[0]["sender"] == registered_user["email"].lower()
        assert rows[0]["attachment_sha256"] == hashlib.sha256(CSV).hexdigest()
        assert rows[0]["retrain"] == "none"

        audit = query_one("SELECT context FROM activity_logs WHERE tenant_id=%s "
                          "AND action='audit.inbound_email.received'", (tid,))
        assert audit["context"]["target_id"] == ds[0]["id"]
        assert audit["context"]["after"]["outcome"] == "ingested"
        assert query_one("SELECT 1 FROM activity_logs WHERE tenant_id=%s "
                         "AND action='inbound_email.ingested'", (tid,)) is not None

    def test_no_training_is_started_without_a_schedule(
            self, client, registered_user, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        calls = []
        import backend.sessions.retrain_service as rs
        monkeypatch.setattr(rs, "launch_scheduled_retrain",
                            lambda *a, **k: calls.append(a))
        _post(client, _payload(_token(tid), registered_user["email"]))
        assert calls == []
        assert query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id=%s", (tid,))["n"] == 0

    def test_an_active_schedule_with_matching_columns_is_refreshed_and_run_once(
            self, client, registered_user, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        sid = _confirm_mapping(tid)
        # The schedule's own dataset: same columns, older content.
        import asyncio
        from fastapi import UploadFile
        import io
        from backend.datasets import service as ds_svc
        meta = asyncio.run(ds_svc.upload_dataset(
            tid, "u", UploadFile(file=io.BytesIO(b"sku,date,demand\nZ,2023-01-01,1\n"),
                                 filename="old.csv"), track=False))
        execute("UPDATE sessions SET dataset_id=%s WHERE id=%s", (meta["id"], sid))
        execute("INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, "
                "next_run, enabled) VALUES (%s,%s,%s,'0 6 * * 1', NOW(), TRUE)",
                (f"sj_{uuid4().hex[:8]}", tid, sid))
        calls = []
        import backend.sessions.retrain_service as rs
        monkeypatch.setattr(rs, "launch_scheduled_retrain",
                            lambda *a, **k: calls.append(a) or {"id": "fam"})
        r = _post(client, _payload(_token(tid), registered_user["email"]))
        assert r.json()["data"]["results"][0]["retrain"] == "launched"
        assert len(calls) == 1 and calls[0][2] == sid
        from pathlib import Path
        template = query_one("SELECT file_path FROM datasets WHERE id=%s", (meta["id"],))
        assert Path(template["file_path"]).read_bytes() == CSV
        assert _rows(tid)[0]["retrain"] == "launched"

    def test_different_columns_are_stored_for_review_never_guessed(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        r = _post(client, _payload(_token(tid), registered_user["email"],
                                   content=CSV_OTHER_HEADER))
        assert r.json()["data"]["outcome"] == "needs_review"
        rows = _rows(tid)
        assert rows[0]["outcome"] == "needs_review" and rows[0]["reason"] == "columns_changed"
        assert len(_datasets(tid)) == 1
        ev = query_one("SELECT context FROM activity_logs WHERE tenant_id=%s "
                       "AND action='inbound_email.needs_review'", (tid,))
        assert ev["context"]["severity"] == "warning"
        assert ev["context"]["reason"] == "inbound_columns_unconfirmed"
        # And it did not claim to have been ingested.
        assert query_one("SELECT 1 FROM activity_logs WHERE tenant_id=%s "
                         "AND action='inbound_email.ingested'", (tid,)) is None

    def test_a_tenant_with_no_confirmed_mapping_needs_review(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        r = _post(client, _payload(_token(tid), registered_user["email"]))
        assert r.json()["data"]["outcome"] == "needs_review"
        assert _rows(tid)[0]["reason"] == "no_confirmed_mapping"

    def test_the_same_message_twice_does_nothing_the_second_time(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        payload = _payload(_token(tid), registered_user["email"], message_id="<one@x>")
        _post(client, payload)
        r2 = _post(client, payload)
        assert r2.status_code == 200
        assert r2.json()["data"]["results"] == [{"outcome": "duplicate"}]
        assert len(_datasets(tid)) == 1 and len(_rows(tid)) == 1

    def test_the_same_file_in_a_new_message_is_not_stored_twice(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        _post(client, _payload(_token(tid), registered_user["email"]))
        r = _post(client, _payload(_token(tid), registered_user["email"]))
        assert r.json()["data"]["results"][0]["reason"] == "duplicate_attachment"
        assert len(_datasets(tid)) == 1
        assert sorted(x["outcome"] for x in _rows(tid)) == ["ingested", "rejected"]

    def test_plan_ceiling_on_file_size_is_respected(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        execute("UPDATE tenants SET quota = %s WHERE id = %s",
                (json.dumps({"max_dataset_size_mb": 0}), tid))
        r = _post(client, _payload(_token(tid), registered_user["email"]))
        assert r.json()["data"]["results"][0]["reason"] == "plan_limit_reached"
        assert _datasets(tid) == []
        row = _rows(tid)[0]
        assert row["outcome"] == "rejected" and row["reason"] == "plan_limit_reached"
        ev = query_one("SELECT context FROM activity_logs WHERE tenant_id=%s "
                       "AND action='inbound_email.rejected'", (tid,))
        assert ev["context"]["reason"] == "plan_limit_reached"

    def test_a_file_with_a_nul_byte_is_refused_not_truncated(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        bad = b"sku,date,demand\nA\x001,2024-01-01,5\n"
        r = _post(client, _payload(_token(tid), registered_user["email"], content=bad))
        assert r.json()["data"]["results"][0]["reason"] == "file_has_nul_byte"
        assert _datasets(tid) == []
        assert _rows(tid)[0]["outcome"] == "rejected"

    def test_a_message_without_a_usable_attachment_is_recorded_with_its_reason(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        r = _post(client, _payload(_token(tid), registered_user["email"],
                                   filename="logo.png", content=b"\x89PNG"))
        assert r.json()["data"]["reason"] == "unsupported_type"
        assert _rows(tid)[0]["reason"] == "unsupported_type"
        assert _datasets(tid) == []

    def test_allow_listed_outsider_can_send(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        r = client.put("/api/v1/inbound-email/senders", headers=auth_headers,
                       json={"emails": ["Bookkeeper@Firm.test"]})
        assert r.status_code == 200
        r = _post(client, _payload(_token(tid), "Ana <bookkeeper@firm.test>"))
        assert r.json()["data"]["outcome"] == "ingested"
        assert len(_datasets(tid)) == 1

    def test_multipart_mailgun_style_is_read(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        boundary = "BOUNDARY123"
        parts = []
        for k, v in (("sender", registered_user["email"]),
                     ("recipient", f"sales+{_token(tid)}@{DOMAIN}"),
                     ("Message-Id", "<mg-1@x>")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="attachment-1"; '
                     f'filename="ventas.csv"\r\nContent-Type: text/csv\r\n\r\n')
        body = "".join(parts).encode() + CSV + f"\r\n--{boundary}--\r\n".encode()
        headers = _sign(body)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        r = client.post("/api/v1/inbound/email", content=body, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "ingested"
        assert _rows(tid)[0]["message_id"] == "<mg-1@x>"

    def test_postmark_json_shape_is_read(self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        payload = {
            "FromFull": {"Email": registered_user["email"]},
            "OriginalRecipient": f"sales+{_token(tid)}@{DOMAIN}",
            "MessageID": "pm-1",
            "Attachments": [{"Name": "ventas.csv", "ContentType": "text/csv",
                             "Content": base64.b64encode(CSV).decode()}],
        }
        r = _post(client, payload)
        assert r.json()["data"]["outcome"] == "ingested"
        assert _rows(tid)[0]["message_id"] == "pm-1"


class TestAddressAndAllowList:
    def test_admin_reads_the_address_and_the_last_messages(
            self, client, auth_headers, registered_user, test_tenant):
        tid = test_tenant["id"]
        _post(client, _payload(_token(tid), "stranger@spam.test"))
        r = client.get("/api/v1/inbound-email", headers=auth_headers)
        data = r.json()["data"]
        assert data["enabled"] is True
        assert data["address"] == f"sales+{_token(tid)}@{DOMAIN}"
        assert [m["outcome"] for m in data["messages"]] == ["rejected"]

    def test_only_the_last_twenty_messages_are_listed(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        for i in range(25):
            _post(client, _payload(_token(tid), "stranger@spam.test", message_id=f"<m{i}>"))
        data = client.get("/api/v1/inbound-email", headers=auth_headers).json()["data"]
        assert len(data["messages"]) == 20

    def test_card_never_shows_another_tenants_messages(
            self, client, auth_headers, test_tenant, make_tenant_user_headers):
        _, other = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _post(client, _payload(_token(other), "stranger@spam.test"))
        data = client.get("/api/v1/inbound-email", headers=auth_headers).json()["data"]
        assert data["messages"] == []

    def test_viewer_and_analyst_cannot_read_the_address(
            self, client, viewer_headers, analyst_headers):
        assert client.get("/api/v1/inbound-email", headers=viewer_headers).status_code == 403
        assert client.get("/api/v1/inbound-email", headers=analyst_headers).status_code == 403

    def test_regenerate_viewer_denied_and_state_unchanged(
            self, client, viewer_headers, test_tenant):
        before = _token(test_tenant["id"])
        r = client.post("/api/v1/inbound-email/regenerate", headers=viewer_headers)
        assert r.status_code == 403
        assert _token(test_tenant["id"]) == before

    def test_regenerate_admin_ok_audited_and_old_address_dies(
            self, client, auth_headers, registered_user, test_tenant):
        tid = test_tenant["id"]
        _confirm_mapping(tid)
        old = _token(tid)
        r = client.post("/api/v1/inbound-email/regenerate", headers=auth_headers)
        assert r.status_code == 200
        new = _token(tid)
        assert new != old and r.json()["data"]["address"].startswith(f"sales+{new}@")
        assert query_one("SELECT rotated_at FROM inbound_email_addresses "
                         "WHERE tenant_id=%s", (tid,))["rotated_at"] is not None

        dead = _post(client, _payload(old, registered_user["email"]))
        assert dead.json()["data"]["reason"] == "unknown_address"
        assert _datasets(tid) == []
        live = _post(client, _payload(new, registered_user["email"]))
        assert live.json()["data"]["outcome"] == "ingested"

        trail = query_one("SELECT context FROM activity_logs WHERE tenant_id=%s "
                          "AND action='audit.inbound_email.address_regenerated'", (tid,))
        assert trail is not None
        assert old not in json.dumps(trail["context"])
        assert new not in json.dumps(trail["context"])

    def test_senders_viewer_denied_and_state_unchanged(
            self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        _token(tid)
        r = client.put("/api/v1/inbound-email/senders", headers=viewer_headers,
                       json={"emails": ["a@b.co"]})
        assert r.status_code == 403
        assert query_one("SELECT allowed_senders FROM inbound_email_addresses "
                         "WHERE tenant_id=%s", (tid,))["allowed_senders"] == []

    def test_senders_admin_ok_audited_and_validated(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        r = client.put("/api/v1/inbound-email/senders", headers=auth_headers,
                       json={"emails": ["A@B.co", "a@b.co", "c@d.co"]})
        assert r.status_code == 200
        assert query_one("SELECT allowed_senders FROM inbound_email_addresses "
                         "WHERE tenant_id=%s", (tid,))["allowed_senders"] == ["a@b.co", "c@d.co"]
        assert query_one("SELECT 1 FROM activity_logs WHERE tenant_id=%s "
                         "AND action='audit.inbound_email.senders_changed'", (tid,))
        bad = client.put("/api/v1/inbound-email/senders", headers=auth_headers,
                         json={"emails": ["not-an-email"]})
        assert bad.status_code == 422 and bad.json()["error_code"] == "inbound_email_invalid_sender"
        assert query_one("SELECT allowed_senders FROM inbound_email_addresses "
                         "WHERE tenant_id=%s", (tid,))["allowed_senders"] == ["a@b.co", "c@d.co"]

    def test_the_token_is_not_in_the_tenant_export_spec(self):
        from backend.tenants.data_export import _EXPORT_SPECS
        spec = {t: cols for _, t, cols in _EXPORT_SPECS}
        assert "token" not in spec["inbound_email_addresses"]


class TestApiKeySurface:
    def test_neither_router_is_callable_with_an_api_key(self):
        from backend.main import app
        from backend.api.public_surface import exposure
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/inbound"):
                assert not exposure(route).exposed, path
