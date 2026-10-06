"""Webhook event allowlist: an event is offered for subscription only when some
code path really emits it (accuracy.degraded was advertised once with no
emitter and was removed). The emitters, with the code path that causes each:

  job.completed / job.failed      workers/runner.py            (fire_webhooks)
  purchase_order.approved/rejected inventory/po_approval_service.decide
  purchase_order.sent              inventory/reception_service.mark_po_sent
  purchase_order.cancelled         inventory/po_cancel_service.cancel
  stockout.imminent                inventory/service._stockout_webhook_pass
  commitment.at_risk               webhooks/service.commitment_risk_transitions
  commitment.fulfilled             inventory/committed_demand_service.set_status
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.v1.webhooks import SUPPORTED_EVENTS, CreateWebhookRequest

BACKEND = Path(__file__).resolve().parents[1]

# event -> (source file, the call that emits it)
EMITTERS = {
    "job.completed": ("workers/runner.py", '"job.completed"'),
    "job.failed": ("workers/runner.py", '"job.failed"'),
    "purchase_order.approved": ("inventory/po_approval_service.py", 'f"purchase_order.{decision}"'),
    "purchase_order.rejected": ("inventory/po_approval_service.py", 'f"purchase_order.{decision}"'),
    "purchase_order.sent": ("inventory/reception_service.py", '"purchase_order.sent"'),
    "purchase_order.cancelled": ("inventory/po_cancel_service.py", '"purchase_order.cancelled"'),
    "stockout.imminent": ("webhooks/service.py", '"stockout.imminent"'),
    "commitment.at_risk": ("webhooks/service.py", '"commitment.at_risk"'),
    "commitment.fulfilled": ("inventory/committed_demand_service.py", '"commitment.fulfilled"'),
}


@pytest.mark.offline
def test_supported_events_are_only_the_emitted_ones():
    assert SUPPORTED_EVENTS == set(EMITTERS)
    assert "accuracy.degraded" not in SUPPORTED_EVENTS
    assert "webhook.test" not in SUPPORTED_EVENTS     # delivered, never subscribed


@pytest.mark.offline
@pytest.mark.parametrize("event", sorted(EMITTERS))
def test_every_offered_event_has_an_emitting_call_in_its_code_path(event):
    rel, needle = EMITTERS[event]
    source = (BACKEND / rel).read_text(encoding="utf-8")
    assert needle in source, f"{event}: no emitter found in {rel}"


@pytest.mark.offline
def test_create_request_accepts_emitted_events():
    req = CreateWebhookRequest(url="https://example.com/hook", events=["job.completed", "job.failed"])
    assert req.events == ["job.completed", "job.failed"]


@pytest.mark.offline
def test_create_request_accepts_the_business_events():
    req = CreateWebhookRequest(
        url="https://example.com/hook",
        events=["purchase_order.approved", "stockout.imminent", "commitment.at_risk"])
    assert len(req.events) == 3


@pytest.mark.offline
def test_create_request_rejects_never_emitted_event():
    with pytest.raises(ValidationError) as exc:
        CreateWebhookRequest(url="https://example.com/hook", events=["accuracy.degraded"])
    assert "accuracy.degraded" in str(exc.value)


@pytest.mark.offline
def test_the_test_event_cannot_be_subscribed_to():
    with pytest.raises(ValidationError):
        CreateWebhookRequest(url="https://example.com/hook", events=["webhook.test"])


@pytest.mark.offline
def test_create_request_still_requires_https():
    with pytest.raises(ValidationError):
        CreateWebhookRequest(url="http://example.com/hook", events=["job.completed"])
