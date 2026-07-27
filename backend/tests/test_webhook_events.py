"""Webhook event allowlist: accuracy.degraded was advertised but no code path
ever emitted it, so it was removed from SUPPORTED_EVENTS until an emitter
exists. These tests pin that decision."""

import pytest
from pydantic import ValidationError

from backend.api.v1.webhooks import SUPPORTED_EVENTS, CreateWebhookRequest


@pytest.mark.offline
def test_supported_events_are_only_the_emitted_ones():
    # fire_webhooks is called only for job.completed / job.failed (workers/runner.py)
    assert SUPPORTED_EVENTS == {"job.completed", "job.failed"}


@pytest.mark.offline
def test_create_request_accepts_emitted_events():
    req = CreateWebhookRequest(url="https://example.com/hook", events=["job.completed", "job.failed"])
    assert req.events == ["job.completed", "job.failed"]


@pytest.mark.offline
def test_create_request_rejects_never_emitted_event():
    with pytest.raises(ValidationError) as exc:
        CreateWebhookRequest(url="https://example.com/hook", events=["accuracy.degraded"])
    assert "accuracy.degraded" in str(exc.value)


@pytest.mark.offline
def test_create_request_still_requires_https():
    with pytest.raises(ValidationError):
        CreateWebhookRequest(url="http://example.com/hook", events=["job.completed"])
