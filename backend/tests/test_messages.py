"""Team messaging (direct_messages): send/read/thread endpoints, tenant
isolation, plan gating and the SMS heads-up trigger.

Role note: messaging is deliberately open to every role (viewer included) —
it is communication, not a business-data mutation — so the permission pair
here is same-tenant-any-role success vs cross-tenant denial.
"""

from uuid import uuid4

from backend.db.connection import execute, query_one


def _msg_row(msg_id):
    return query_one("SELECT * FROM direct_messages WHERE id = %s", (msg_id,))


def _count_between(tenant_id, a, b):
    row = query_one(
        "SELECT COUNT(*) AS n FROM direct_messages "
        "WHERE tenant_id = %s AND sender_id = %s AND recipient_id = %s",
        (tenant_id, a, b),
    )
    return int(row["n"])


class TestSendMessage:
    def test_send_persists_row(self, client, auth_headers, registered_user, analyst_user):
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "hello there"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        row = _msg_row(data["id"])
        assert row is not None
        assert row["tenant_id"] == registered_user["tenant"]["id"]
        assert row["sender_id"] == registered_user["user"]["id"]
        assert row["recipient_id"] == analyst_user["user"]["id"]
        assert row["body"] == "hello there"
        assert row["read_at"] is None

    def test_viewer_can_send(self, client, viewer_headers, viewer_user, registered_user):
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": registered_user["user"]["id"], "body": "from viewer"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _msg_row(resp.json()["data"]["id"])["sender_id"] == viewer_user["user"]["id"]

    def test_send_to_self_rejected(self, client, auth_headers, registered_user):
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": registered_user["user"]["id"], "body": "hi me"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert _count_between(
            registered_user["tenant"]["id"],
            registered_user["user"]["id"], registered_user["user"]["id"],
        ) == 0

    def test_send_to_unknown_user_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": f"no-such-{uuid4().hex[:8]}", "body": "hi"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_send_to_inactive_user_rejected(self, client, auth_headers, registered_user, analyst_user):
        execute(
            "UPDATE users SET status = 'disabled' WHERE id = %s",
            (analyst_user["user"]["id"],),
        )
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "hi"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert _count_between(
            registered_user["tenant"]["id"],
            registered_user["user"]["id"], analyst_user["user"]["id"],
        ) == 0

    def test_blank_body_rejected(self, client, auth_headers, analyst_user, registered_user):
        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "   "},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert _count_between(
            registered_user["tenant"]["id"],
            registered_user["user"]["id"], analyst_user["user"]["id"],
        ) == 0


class TestTenantIsolation:
    def test_cannot_message_user_of_another_tenant(
        self, client, auth_headers, registered_user, make_tenant_user_headers,
    ):
        other_headers, other_tenant_id = make_tenant_user_headers(
            plan="professional", role="admin", return_tenant_id=True,
        )
        other_user = query_one(
            "SELECT id FROM users WHERE tenant_id = %s", (other_tenant_id,),
        )

        resp = client.post(
            "/api/v1/messages",
            json={"recipient_id": other_user["id"], "body": "cross-tenant"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert _count_between(
            registered_user["tenant"]["id"],
            registered_user["user"]["id"], other_user["id"],
        ) == 0

    def test_cannot_read_thread_of_another_tenant(
        self, client, registered_user, analyst_user, auth_headers, make_tenant_user_headers,
    ):
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "secret"},
            headers=auth_headers,
        )
        other_headers = make_tenant_user_headers(plan="professional", role="admin")
        resp = client.get(
            f"/api/v1/messages/thread?with_user={registered_user['user']['id']}",
            headers=other_headers,
        )
        assert resp.status_code == 404


class TestReadAndCounts:
    def test_unread_then_mark_read(self, client, auth_headers, analyst_headers, registered_user, analyst_user):
        sent = client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "read me"},
            headers=auth_headers,
        ).json()["data"]

        resp = client.get("/api/v1/messages/unread-count", headers=analyst_headers)
        assert resp.json()["data"]["unread"] == 1

        resp = client.post(
            "/api/v1/messages/read",
            json={"with_user": registered_user["user"]["id"]},
            headers=analyst_headers,
        )
        assert resp.status_code == 200

        assert _msg_row(sent["id"])["read_at"] is not None
        resp = client.get("/api/v1/messages/unread-count", headers=analyst_headers)
        assert resp.json()["data"]["unread"] == 0

    def test_mark_read_does_not_touch_other_senders(
        self, client, auth_headers, viewer_headers, analyst_headers,
        registered_user, viewer_user, analyst_user,
    ):
        for headers in (auth_headers, viewer_headers):
            client.post(
                "/api/v1/messages",
                json={"recipient_id": analyst_user["user"]["id"], "body": "hi"},
                headers=headers,
            )
        client.post(
            "/api/v1/messages/read",
            json={"with_user": registered_user["user"]["id"]},
            headers=analyst_headers,
        )
        row = query_one(
            "SELECT read_at FROM direct_messages "
            "WHERE recipient_id = %s AND sender_id = %s",
            (analyst_user["user"]["id"], viewer_user["user"]["id"]),
        )
        assert row["read_at"] is None

    def test_conversations_list(self, client, auth_headers, analyst_headers, registered_user, analyst_user):
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "first"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "second"},
            headers=auth_headers,
        )

        convos = client.get("/api/v1/messages/conversations", headers=analyst_headers).json()["data"]
        assert len(convos) == 1
        c = convos[0]
        assert c["counterpart_id"] == registered_user["user"]["id"]
        assert c["last_body"] == "second"
        assert c["unread_count"] == 2
        assert c["last_is_mine"] is False

    def test_thread_returns_both_directions_oldest_first(
        self, client, auth_headers, analyst_headers, registered_user, analyst_user,
    ):
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "ping"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/messages",
            json={"recipient_id": registered_user["user"]["id"], "body": "pong"},
            headers=analyst_headers,
        )
        data = client.get(
            f"/api/v1/messages/thread?with_user={analyst_user['user']['id']}",
            headers=auth_headers,
        ).json()["data"]
        bodies = [m["body"] for m in data["messages"]]
        assert bodies == ["ping", "pong"]
        assert data["counterpart"]["id"] == analyst_user["user"]["id"]


class TestPlanGating:
    def test_starter_plan_denied(self, client, make_tenant_user_headers, monkeypatch):
        from backend.config import settings
        headers = make_tenant_user_headers(plan="starter", role="admin")
        monkeypatch.setattr(settings, "testing_mode", False)
        resp = client.get("/api/v1/messages/unread-count", headers=headers)
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "PLAN_UPGRADE_REQUIRED"
        assert detail["feature"] == "team_messaging"
        assert "professional" in detail["required_plans"]

    def test_professional_plan_allowed(self, client, make_tenant_user_headers, monkeypatch):
        from backend.config import settings
        headers = make_tenant_user_headers(plan="professional", role="admin")
        monkeypatch.setattr(settings, "testing_mode", False)
        resp = client.get("/api/v1/messages/unread-count", headers=headers)
        assert resp.status_code == 200


class TestHeadsUpNotification:
    """WhatsApp is the primary channel; SMS only fires when WhatsApp fails."""

    def _enable(self, tenant_id, user_id, phone="+573001112233"):
        execute(
            "UPDATE users SET whatsapp_number = %s WHERE id = %s",
            (phone, user_id),
        )
        execute(
            "INSERT INTO user_preferences (user_id, tenant_id, dm_sms_enabled) "
            "VALUES (%s, %s, TRUE) "
            "ON CONFLICT (user_id) DO UPDATE SET dm_sms_enabled = TRUE",
            (user_id, tenant_id),
        )

    def _patch_channels(self, monkeypatch, whatsapp_ok):
        wa_calls, sms_calls = [], []
        monkeypatch.setattr(
            "backend.api.v1.messages.whatsapp.send_whatsapp_and_confirm",
            lambda to, body, wait_seconds=8.0: wa_calls.append((to, body)) or whatsapp_ok,
        )
        monkeypatch.setattr(
            "backend.api.v1.messages.sms.send_sms",
            lambda to, body: sms_calls.append((to, body)) or True,
        )
        return wa_calls, sms_calls

    def test_whatsapp_sent_and_no_sms_when_it_succeeds(
        self, client, auth_headers, registered_user, analyst_user, monkeypatch,
    ):
        wa_calls, sms_calls = self._patch_channels(monkeypatch, whatsapp_ok=True)
        phone = f"+57300{uuid4().hex[:7]}"
        self._enable(registered_user["tenant"]["id"], analyst_user["user"]["id"], phone)

        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "heads-up trigger"},
            headers=auth_headers,
        )
        assert len(wa_calls) == 1
        to, body = wa_calls[0]
        assert to == phone
        assert registered_user["user"]["full_name"] in body
        assert sms_calls == []

    def test_sms_fallback_when_whatsapp_fails(
        self, client, auth_headers, registered_user, analyst_user, monkeypatch,
    ):
        wa_calls, sms_calls = self._patch_channels(monkeypatch, whatsapp_ok=False)
        phone = f"+57300{uuid4().hex[:7]}"
        self._enable(registered_user["tenant"]["id"], analyst_user["user"]["id"], phone)

        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "fallback trigger"},
            headers=auth_headers,
        )
        assert len(wa_calls) == 1
        assert len(sms_calls) == 1
        to, body = sms_calls[0]
        assert to == phone
        assert registered_user["user"]["full_name"] in body

    def test_not_sent_when_disabled(self, client, auth_headers, analyst_user, registered_user, monkeypatch):
        wa_calls, sms_calls = self._patch_channels(monkeypatch, whatsapp_ok=True)
        execute(
            "UPDATE users SET whatsapp_number = %s WHERE id = %s",
            (f"+57300{uuid4().hex[:7]}", analyst_user["user"]["id"]),
        )
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "no heads-up"},
            headers=auth_headers,
        )
        assert wa_calls == []
        assert sms_calls == []

    def test_not_sent_without_phone(self, client, auth_headers, analyst_user, registered_user, monkeypatch):
        wa_calls, sms_calls = self._patch_channels(monkeypatch, whatsapp_ok=True)
        execute(
            "INSERT INTO user_preferences (user_id, tenant_id, dm_sms_enabled) "
            "VALUES (%s, %s, TRUE) "
            "ON CONFLICT (user_id) DO UPDATE SET dm_sms_enabled = TRUE",
            (analyst_user["user"]["id"], registered_user["tenant"]["id"]),
        )
        client.post(
            "/api/v1/messages",
            json={"recipient_id": analyst_user["user"]["id"], "body": "no phone"},
            headers=auth_headers,
        )
        assert wa_calls == []
        assert sms_calls == []

    def test_throttled_while_unread_backlog(self, client, auth_headers, registered_user, analyst_user, monkeypatch):
        wa_calls, sms_calls = self._patch_channels(monkeypatch, whatsapp_ok=True)
        self._enable(
            registered_user["tenant"]["id"], analyst_user["user"]["id"],
            f"+57300{uuid4().hex[:7]}",
        )
        for text in ("first", "second", "third"):
            client.post(
                "/api/v1/messages",
                json={"recipient_id": analyst_user["user"]["id"], "body": text},
                headers=auth_headers,
            )
        assert len(wa_calls) == 1
        assert sms_calls == []


class TestPreferencesToggle:
    def test_dm_sms_enabled_roundtrip(self, client, auth_headers, registered_user):
        resp = client.patch(
            "/api/v1/me/preferences",
            json={"dm_sms_enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["dm_sms_enabled"] is True

        row = query_one(
            "SELECT dm_sms_enabled FROM user_preferences WHERE user_id = %s",
            (registered_user["user"]["id"],),
        )
        assert row["dm_sms_enabled"] is True

        resp = client.get("/api/v1/me/preferences", headers=auth_headers)
        assert resp.json()["data"]["dm_sms_enabled"] is True
