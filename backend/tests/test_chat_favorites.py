"""
Starring assistant messages: PATCH /analyst/messages/{id}/star and
GET /analyst/favorites.

A message can only be starred by the owner of its chat. Another user of the
same tenant, and a user of another tenant, get a 404 and the row is unchanged
(asserted against the database, not the response).
"""

from uuid import uuid4

import pytest

from backend.db import chat_store
from backend.db.connection import query_one


@pytest.fixture
def seeded(registered_user):
    """A chat owned by the admin with one question and its answer."""
    tenant_id = registered_user["tenant"]["id"]
    user_id = registered_user["user"]["id"]
    chat = chat_store.create_chat(tenant_id, user_id, title="Stock questions")
    question = chat_store.add_message(chat["id"], tenant_id, "user", "What should I order?")
    answer = chat_store.add_message(
        chat["id"], tenant_id, "assistant", "Order **SKU-1** now.", source="assistant",
    )
    return {"chat": chat, "question": question, "answer": answer, "tenant_id": tenant_id}


def _starred_at(message_id: str):
    return query_one("SELECT starred_at FROM chat_messages WHERE id = %s", (message_id,))["starred_at"]


class TestStarMessage:

    def test_owner_stars_and_unstars(self, client, auth_headers, seeded):
        mid = seeded["answer"]["id"]
        assert _starred_at(mid) is None

        resp = client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        first = _starred_at(mid)
        assert first is not None

        # Starring again keeps the original timestamp.
        client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=auth_headers)
        assert _starred_at(mid) == first

        resp = client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": False}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert _starred_at(mid) is None

    def test_starred_must_be_a_boolean(self, client, auth_headers, seeded):
        mid = seeded["answer"]["id"]
        resp = client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": "yes"}, headers=auth_headers)
        assert resp.status_code == 400
        assert _starred_at(mid) is None

    def test_unknown_message_is_404(self, client, auth_headers):
        resp = client.patch(
            f"/api/v1/analyst/messages/{uuid4()}/star", json={"starred": True}, headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_other_user_same_tenant_is_denied(self, client, analyst_headers, seeded):
        mid = seeded["answer"]["id"]
        resp = client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=analyst_headers)
        assert resp.status_code == 404
        assert _starred_at(mid) is None

    def test_other_tenant_is_denied(self, client, make_tenant_user_headers, seeded):
        other = make_tenant_user_headers(role="admin")
        mid = seeded["answer"]["id"]
        resp = client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=other)
        assert resp.status_code == 404
        assert _starred_at(mid) is None

    def test_a_viewer_can_star_their_own_message(self, client, viewer_headers, viewer_user):
        # No role is needed: it only changes the user's own list.
        tenant_id = viewer_user["tenant"]["id"]
        chat = chat_store.create_chat(tenant_id, viewer_user["user"]["id"])
        msg = chat_store.add_message(chat["id"], tenant_id, "assistant", "hello", source="assistant")
        resp = client.patch(f"/api/v1/analyst/messages/{msg['id']}/star", json={"starred": True}, headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        assert _starred_at(msg["id"]) is not None

    def test_message_listing_carries_the_flag(self, client, auth_headers, seeded):
        mid = seeded["answer"]["id"]
        client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=auth_headers)
        resp = client.get(f"/api/v1/analyst/chats/{seeded['chat']['id']}/messages", headers=auth_headers)
        by_id = {m["id"]: m for m in resp.json()["data"]["messages"]}
        assert by_id[mid]["starred_at"] is not None
        assert by_id[seeded["question"]["id"]]["starred_at"] is None


class TestFavorites:

    def test_lists_only_own_starred_messages_with_question_and_title(
        self, client, auth_headers, analyst_headers, analyst_user, seeded,
    ):
        mid = seeded["answer"]["id"]
        client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=auth_headers)

        # A teammate's own starred message must not appear for the admin.
        tenant_id = seeded["tenant_id"]
        other_chat = chat_store.create_chat(tenant_id, analyst_user["user"]["id"], title="Mine")
        other_msg = chat_store.add_message(other_chat["id"], tenant_id, "assistant", "private", source="assistant")
        client.patch(f"/api/v1/analyst/messages/{other_msg['id']}/star", json={"starred": True}, headers=analyst_headers)
        assert _starred_at(other_msg["id"]) is not None

        rows = client.get("/api/v1/analyst/favorites", headers=auth_headers).json()["data"]
        assert [r["id"] for r in rows] == [mid]
        assert rows[0]["question"] == "What should I order?"
        assert rows[0]["chat_title"] == "Stock questions"
        assert rows[0]["chat_id"] == seeded["chat"]["id"]

        mine = client.get("/api/v1/analyst/favorites", headers=analyst_headers).json()["data"]
        assert [r["id"] for r in mine] == [other_msg["id"]]

    def test_other_tenant_sees_nothing(self, client, auth_headers, make_tenant_user_headers, seeded):
        client.patch(
            f"/api/v1/analyst/messages/{seeded['answer']['id']}/star", json={"starred": True}, headers=auth_headers,
        )
        other = make_tenant_user_headers(role="admin")
        assert client.get("/api/v1/analyst/favorites", headers=other).json()["data"] == []

    def test_unstarred_message_leaves_the_list(self, client, auth_headers, seeded):
        mid = seeded["answer"]["id"]
        client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": True}, headers=auth_headers)
        client.patch(f"/api/v1/analyst/messages/{mid}/star", json={"starred": False}, headers=auth_headers)
        assert client.get("/api/v1/analyst/favorites", headers=auth_headers).json()["data"] == []
