"""
AI Analyst chat endpoint regression tests.

Covers two audit findings:
  - No maximum length validation on the 'question' field (unbounded payload
    forwarded straight to the LLM).
  - No rate limiting on message sending (a tenant could flood the endpoint,
    each call billed against the tenant's Anthropic usage).

The LLM call itself is mocked out — these tests exercise the validation
gate, not the Anthropic integration (which is covered elsewhere and would
otherwise spend real API credit).
"""

from unittest import mock

import pytest

from backend.api.v1.chats import MAX_QUESTION_LENGTH, RATE_LIMIT_MAX_MESSAGES


@pytest.fixture
def chat(client, auth_headers):
    resp = client.post("/api/v1/analyst/chats", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestMessageValidation:

    def test_question_too_long_rejected(self, client, auth_headers, chat):
        resp = client.post(
            f"/api/v1/analyst/chats/{chat['id']}/messages",
            json={"question": "x" * (MAX_QUESTION_LENGTH + 1)},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text

    def test_question_at_limit_accepted(self, client, auth_headers, chat):
        with mock.patch("backend.api.v1.chats._general_answer", return_value="ok"):
            resp = client.post(
                f"/api/v1/analyst/chats/{chat['id']}/messages",
                json={"question": "x" * MAX_QUESTION_LENGTH},
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text


class TestRateLimit:

    def test_exceeding_rate_limit_returns_429(self, client, auth_headers, chat):
        # The rate limit uses a sliding window keyed on RATE_LIMIT_WINDOW_SECONDS
        # (60s in production). Sending RATE_LIMIT_MAX_MESSAGES requests against a
        # real remote DB takes long enough in wall-clock time that, at the
        # production window, the earliest messages would legitimately age out
        # before the last one is sent — masking the limit instead of testing it.
        # Widening the window here (independent of the count threshold under
        # test) makes the test immune to its own network latency.
        with mock.patch("backend.api.v1.chats._general_answer", return_value="ok"), \
             mock.patch("backend.api.v1.chats.RATE_LIMIT_WINDOW_SECONDS", 3600):
            for i in range(RATE_LIMIT_MAX_MESSAGES):
                resp = client.post(
                    f"/api/v1/analyst/chats/{chat['id']}/messages",
                    json={"question": f"message {i}"},
                    headers=auth_headers,
                )
                assert resp.status_code == 200, resp.text

            resp = client.post(
                f"/api/v1/analyst/chats/{chat['id']}/messages",
                json={"question": "one too many"},
                headers=auth_headers,
            )
        assert resp.status_code == 429, resp.text
