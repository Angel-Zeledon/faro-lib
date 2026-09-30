"""One tenant, one cost of holding stock.

`/inventory/dead-stock` once priced dead stock at a hardcoded 25% a year while
`/inventory/price-breaks/evaluate` and the MILP optimizer priced the SAME
warehouse at the tenant's `business_cfg.holding_cost_pct` (0.20 by default).
A buyer read "this costs you X a month to keep" on /inventario and then got
purchase advice built on a different cost of money on /compras.

`/dead-stock` was retired on 2026-09-30 (stability.md 19.2). The property it
broke still matters for what remains: every surface that prices holding stock
reads the tenant's configured rate, and every place that assumes a rate when
none is configured assumes the SAME one.
"""

import pytest

from backend.db import session_store
from backend.inventory import defaults as inv_defaults
from backend.inventory import price_break_service as pb_svc
from backend.schemas.configuration import BusinessConfigRequest


def _evaluate(client, headers, session_id: str) -> dict:
    r = client.post(
        "/api/v1/inventory/price-breaks/evaluate",
        params={"session_id": session_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


class TestTheFallbackIsOneNumber:
    def test_every_declared_default_agrees(self):
        """The price-break panel, the planning defaults and the business-config
        schema each declare a fallback. If one drifts, the same unconfigured
        tenant is priced at two different costs of money."""
        schema_default = BusinessConfigRequest.model_fields["holding_cost_pct"].default
        assert pb_svc.DEFAULT_HOLDING_COST_PCT == pytest.approx(inv_defaults.DEFAULT_HOLDING_COST_PCT)
        assert schema_default == pytest.approx(inv_defaults.DEFAULT_HOLDING_COST_PCT)
        assert inv_defaults.DEFAULT_HOLDING_COST_PCT != pytest.approx(0.25), (
            "0.25 was the retired dead-stock view's private rate"
        )


class TestTheRateComesFromTheTenant:
    def test_unconfigured_session_uses_the_shared_default(
        self, client, auth_headers, test_tenant, completed_session
    ):
        sid = completed_session["id"]
        cfg = session_store.get_field(test_tenant["id"], sid, "business_cfg") or {}
        cfg.pop("holding_cost_pct", None)
        session_store.set_field(test_tenant["id"], sid, "business_cfg", cfg)

        data = _evaluate(client, auth_headers, sid)
        assert data["holding_cost_pct"] == pytest.approx(inv_defaults.DEFAULT_HOLDING_COST_PCT)

    def test_a_configured_rate_reaches_the_endpoint(
        self, client, auth_headers, test_tenant, completed_session
    ):
        sid = completed_session["id"]
        cfg = session_store.get_field(test_tenant["id"], sid, "business_cfg") or {}
        session_store.set_field(
            test_tenant["id"], sid, "business_cfg", {**cfg, "holding_cost_pct": 0.4},
        )
        stored = session_store.get_field(test_tenant["id"], sid, "business_cfg")
        assert stored["holding_cost_pct"] == pytest.approx(0.4)

        data = _evaluate(client, auth_headers, sid)
        assert data["holding_cost_pct"] == pytest.approx(0.4)
