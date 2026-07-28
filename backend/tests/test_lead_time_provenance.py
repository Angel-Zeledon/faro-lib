"""
Can the user tell THEIR data from OUR assumption?

Before the provenance migration the answer was no, and the product exploited
that: `lead_time_days INT NOT NULL DEFAULT 15` made "the buyer chose 15 days"
and "nobody has ever opened this SKU" the same row, and `build_explanation`
told the user, verbatim, *"tu proveedor tarda 15 días en entregar (lead time
configurado)"* — asserting something false inside the sentence we use to earn
their trust.

The test that proves the migration bought something is
`test_a_sku_the_user_set_to_exactly_the_default_reports_user`: a SKU explicitly
edited to 15 — the same number we would have assumed — must report 'user'. Any
implementation that infers provenance from the VALUE fails it, which is the
point; only a real provenance column can pass.

Every assertion here reads inventory_stock (or the resolved API payload)
directly rather than echoing back a request body.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import stock_defaults_service as sd_svc
from backend.inventory.defaults import (
    DEFAULT_LEAD_TIME_DAYS,
    SOURCE_DEFAULT,
    SOURCE_FILE,
    SOURCE_LEARNED,
    SOURCE_SUPPLIER_RULE,
    SOURCE_USER,
    VALUE_SOURCES,
)
from backend.inventory.service import build_explanation, bulk_upsert, upsert_stock


# ── Fixture override: @pytest.local fails email-validator — use @example.com ──

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"], email=email, password=password,
        role="admin", full_name="Test Admin",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"], "password": registered_user["password"],
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _sku():
    return f"SKU-{uuid.uuid4().hex[:8].upper()}"


def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _row(tid, sku):
    return query_one(
        "SELECT lead_time_days, lead_time_set_by, service_level, service_level_set_by, "
        "       unit_cost, unit_cost_set_by, moq, moq_set_by "
        "FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tid, sku),
    )


def _flat_forecast(daily: float, spread: float = 0.0, days: int = 30) -> dict:
    """One model, constant daily demand — every derived number hand-checkable."""
    return {"lightgbm": {"forecast": [
        {
            "date": (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
            "value": daily,
            "lower": max(0.0, daily - spread),
            "upper": daily + spread,
        }
        for i in range(days)
    ]}}


def _status_item(client, headers, tid, sku, session_name="prov"):
    from backend.db import session_store
    from backend.sessions.service import create_session
    session_id = create_session(tid, "usr_test", session_name)["id"]
    session_store.set_forecasts(tid, session_id, {sku: _flat_forecast(10.0, 0.0)})
    items = _ok(client.get(
        f"/api/v1/inventory/status?session_id={session_id}", headers=headers,
    ))["items"]
    return next(i for i in items if i["sku"] == sku)


# ── One constant, not three ──────────────────────────────────────────────────

class TestOneDefault:
    @pytest.mark.offline
    def test_forecasting_core_and_backend_agree_on_the_default(self):
        """The product shipped THREE lead-time defaults: 7 in the canonical
        column defaults, 7 in the training runner, 15 in the DB schema / wizard
        / inventory UI. ForecastingCore cannot import `backend` (layering), so
        the literal is duplicated on purpose — this test is what keeps the
        duplicate from drifting back into a third answer."""
        from forecasting_core.data.canonical import (
            DEFAULT_LEAD_TIME_DAYS as CORE_DEFAULT,
            FIELD_DEFAULTS,
        )
        assert CORE_DEFAULT == DEFAULT_LEAD_TIME_DAYS
        assert FIELD_DEFAULTS["lead_time"] == DEFAULT_LEAD_TIME_DAYS

    @pytest.mark.offline
    def test_reception_and_semaforo_share_one_vocabulary(self):
        """reception_service used to answer 'observed'|'declared'|'default' for
        the same question service.py answered 'learned'|'configured'."""
        from backend.inventory import reception_service as rs
        assert rs.SOURCE_LEARNED in VALUE_SOURCES
        assert rs.SOURCE_SUPPLIER_RULE in VALUE_SOURCES
        assert rs.SOURCE_DEFAULT in VALUE_SOURCES
        assert float(rs._DEFAULT_LEAD_TIME_DAYS) == float(DEFAULT_LEAD_TIME_DAYS)

    @pytest.mark.offline
    def test_optimizer_shares_the_same_default(self):
        from backend.inventory import optimizer_service as os_
        assert os_._DEFAULT_LEAD_TIME_DAYS == DEFAULT_LEAD_TIME_DAYS


# ── Provenance stamped on write ──────────────────────────────────────────────

class TestProvenanceIsPersisted:
    def test_a_sku_nobody_ever_touched_reports_default(
        self, client, auth_headers, test_tenant,
    ):
        """A row created by a write that never mentioned a lead time must not
        claim one. The DB default of 15 is still in the column; what changed is
        that we can now say it is OURS."""
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 20})

        row = _row(tid, sku)
        assert row is not None
        assert int(row["lead_time_days"]) == DEFAULT_LEAD_TIME_DAYS
        assert row["lead_time_set_by"] is None, (
            "an untouched lead time must stay unclaimed in the DB")

        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_DEFAULT
        assert item["lead_time_days"] == DEFAULT_LEAD_TIME_DAYS

    def test_a_sku_the_user_set_to_exactly_the_default_reports_user(
        self, client, auth_headers, test_tenant,
    ):
        """THE test. The buyer deliberately typed 15 — the same number we would
        have assumed. Value-based inference cannot tell this apart from the
        untouched row above; a provenance column can, and must."""
        tid, sku = test_tenant["id"], _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 20, "lead_time_days": DEFAULT_LEAD_TIME_DAYS, "moq": 1},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text

        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == DEFAULT_LEAD_TIME_DAYS
        assert row["lead_time_set_by"] == SOURCE_USER

        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_USER, (
            "a value the user chose must never be reported as our assumption, "
            "even when it happens to equal our assumption")

    def test_only_the_fields_actually_written_are_claimed(self, test_tenant):
        """A reception updates current_stock. It must not thereby claim
        authorship of the lead time, the MOQ or the service level."""
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 5, "lead_time_days": 9})
        first = _row(tid, sku)
        assert first["lead_time_set_by"] == SOURCE_USER
        assert first["moq_set_by"] is None
        assert first["service_level_set_by"] is None

        upsert_stock(tid, sku, {"current_stock": 42}, source=SOURCE_FILE)
        after = _row(tid, sku)
        stock_now = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))
        assert float(stock_now["current_stock"]) == 42.0, "the write did happen"
        assert after["lead_time_set_by"] == SOURCE_USER, (
            "a write that never mentioned the lead time must not re-stamp it")
        assert int(after["lead_time_days"]) == 9
        assert after["moq_set_by"] is None

    def test_file_sourced_values_are_marked_file_not_user(self, test_tenant):
        """`sync_stock_from_dataset` seeds from the user's upload. That is their
        data — but not something they typed on the SKU card, and the copy says
        so ("el lead time que venía en tu archivo")."""
        pd = pytest.importorskip("pandas")
        from backend.inventory.service import sync_stock_from_dataset

        tid, sku = test_tenant["id"], _sku()
        df = pd.DataFrame({
            "sku": [sku, sku],
            "date": ["2025-01-01", "2025-01-02"],
            "inventory": [10.0, 42.0],
            "lead_time": [6, 6],
        })
        n = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"inventory": "existencias", "lead_time": "dias"},
        )
        assert n == 1
        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == 6
        assert row["lead_time_set_by"] == SOURCE_FILE

    def test_all_four_tracked_fields_carry_provenance(self, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {
            "current_stock": 1, "lead_time_days": 20, "service_level": 0.99,
            "unit_cost": 3.5, "moq": 12,
        })
        row = _row(tid, sku)
        assert row["lead_time_set_by"] == SOURCE_USER
        assert row["service_level_set_by"] == SOURCE_USER
        assert row["unit_cost_set_by"] == SOURCE_USER
        assert row["moq_set_by"] == SOURCE_USER

    def test_an_unknown_source_is_rejected(self, test_tenant):
        """The vocabulary is closed. A typo must fail loudly, not persist a
        value nobody can interpret."""
        with pytest.raises(ValueError):
            upsert_stock(test_tenant["id"], _sku(),
                         {"current_stock": 1, "lead_time_days": 3}, source="guessed")


# ── The explanation ──────────────────────────────────────────────────────────

class TestTheExplanationStopsLying:
    @pytest.mark.offline
    def test_default_source_admits_the_assumption(self):
        exp = build_explanation(
            current_stock=20, daily_demand=10, coverage_days=2.0,
            lead_time=DEFAULT_LEAD_TIME_DAYS, lead_time_source=SOURCE_DEFAULT,
            reorder_point=150, signal="PEDIR_YA",
        )
        assert exp["code"] == "inventory_explain_reorder"
        assert exp["params"]["lead_time_source"] == SOURCE_DEFAULT
        assert exp["params"]["lead_time_days"] == DEFAULT_LEAD_TIME_DAYS
        assert "assume" in exp["text"]
        assert "not configured" in exp["text"]
        assert "configured)" not in exp["text"], (
            "the old sentence claimed the lead time was configured")

    @pytest.mark.offline
    def test_user_source_says_you_configured_it(self):
        exp = build_explanation(
            current_stock=20, daily_demand=10, coverage_days=2.0,
            lead_time=8, lead_time_source=SOURCE_USER,
            reorder_point=80, signal="OK",
        )
        assert exp["params"]["lead_time_source"] == SOURCE_USER
        assert "you configured" in exp["text"]
        assert "assume" not in exp["text"]

    @pytest.mark.offline
    def test_supplier_rule_source_names_the_scope(self):
        exp = build_explanation(
            current_stock=20, daily_demand=10, coverage_days=2.0,
            lead_time=12, lead_time_source=SOURCE_SUPPLIER_RULE,
            reorder_point=120, signal="OK", lead_time_rule_scope="category",
        )
        assert exp["params"]["lead_time_rule_scope"] == "category"
        assert "category" in exp["text"], (
            "the copy must state WHICH level of the cascade won")

    @pytest.mark.offline
    def test_no_spanish_leaks_into_the_backend_sentence(self):
        """CLAUDE.md: backend user-facing output is English text or a code +
        params; the Spanish lives in the frontend i18n catalog."""
        for source in VALUE_SOURCES:
            exp = build_explanation(
                current_stock=20, daily_demand=10, coverage_days=2.0,
                lead_time=9, lead_time_source=source,
                reorder_point=90, signal="PEDIR_PRONTO",
            )
            text = exp["text"]
            for spanish in ("días", "proveedor", "unidades", "configurado", "Tienes"):
                assert spanish not in text, f"Spanish leaked for source={source}: {text}"

    @pytest.mark.offline
    def test_no_demand_case_keeps_its_own_code(self):
        exp = build_explanation(
            current_stock=20, daily_demand=0.0, coverage_days=None,
            lead_time=15, lead_time_source=SOURCE_DEFAULT,
            reorder_point=0, signal="OK",
        )
        assert exp["code"] == "inventory_explain_no_demand"
        assert exp["params"] == {"current_stock": 20.0}

    def test_the_api_ships_code_params_and_an_english_fallback(
        self, client, auth_headers, test_tenant,
    ):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 20})
        item = _status_item(client, auth_headers, tid, sku)

        assert item["explanation_code"] == "inventory_explain_reorder"
        assert item["explanation_params"]["lead_time_source"] == SOURCE_DEFAULT
        assert item["explanation_params"]["current_stock"] == 20.0
        assert "assume" in item["explanation"]


# ── Learned from real receptions ─────────────────────────────────────────────

class TestLearnedStillWins:
    def test_receptions_report_learned(self, client, auth_headers, test_tenant):
        """Evidence outranks declaration — unchanged behavior, new word for it
        ('learned' instead of the old 'learned'/'configured' pair's winner)."""
        from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS

        tid, sku = test_tenant["id"], _sku()
        supplier = f"Lento-{uuid.uuid4().hex[:6]}"
        for _ in range(MIN_LEAD_TIME_OBSERVATIONS):
            po = query_one(
                "INSERT INTO inventory_po_log (tenant_id, session_id) VALUES (%s, %s) "
                "RETURNING id",
                (tid, f"ses_{uuid.uuid4().hex[:8]}"),
            )
            execute(
                "INSERT INTO supplier_lead_time_obs (tenant_id, po_log_id, supplier, lead_time_days) "
                "VALUES (%s, %s, %s, %s)",
                (tid, po["id"], supplier, 12.0),
            )
        obs = query_one(
            "SELECT COUNT(*)::int AS n FROM supplier_lead_time_obs "
            "WHERE tenant_id = %s AND LOWER(supplier) = LOWER(%s)",
            (tid, supplier),
        )
        assert obs["n"] == MIN_LEAD_TIME_OBSERVATIONS

        upsert_stock(tid, sku, {"current_stock": 20, "lead_time_days": 5,
                                "supplier": supplier})
        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_LEARNED
        assert item["lead_time_days"] == 12, "the learned average, not the typed 5"
        assert item["lead_time_configured"] == 5


# ── Supplier / category / global rules ───────────────────────────────────────

class TestCascadeReportsWhichLevelWon:
    def test_a_supplier_rule_reaches_a_sku_that_configured_nothing(
        self, client, auth_headers, test_tenant,
    ):
        """A distributor has 12 suppliers, not 2.000 lead times. One rule must
        move every SKU of that supplier — and say where the number came from."""
        tid, sku = test_tenant["id"], _sku()
        supplier = f"Prov-{uuid.uuid4().hex[:6]}"
        upsert_stock(tid, sku, {"current_stock": 20, "supplier": supplier})
        sd_svc.set_stock_default(tid, "supplier", supplier, {"lead_time_days": 12})

        rule = query_one(
            "SELECT lead_time_days, scope_value FROM stock_defaults "
            "WHERE tenant_id = %s AND scope_type = 'supplier' AND scope_value = %s",
            (tid, supplier.lower()),
        )
        assert rule is not None and int(rule["lead_time_days"]) == 12

        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_SUPPLIER_RULE
        assert item["lead_time_rule_scope"] == "supplier"
        assert item["lead_time_days"] == 12, "the resolved value comes FROM the rule"

    def test_the_supplier_card_lead_time_reaches_the_semaforo(
        self, client, auth_headers, test_tenant,
    ):
        """A buyer who fills in "Distribuidora Sur: 12 días" on the supplier card
        expects that to govern that supplier's SKUs. The semáforo used to ignore
        the field entirely — only the overdue-receptions screen read it — so the
        same tenant saw 12 on one screen and 15 on another."""
        tid, sku = test_tenant["id"], _sku()
        name = f"Distribuidora-{uuid.uuid4().hex[:6]}"
        created = _ok(client.post(
            "/api/v1/inventory/suppliers",
            json={"name": name, "lead_time_days": 12},
            headers=auth_headers,
        ), 201)
        assert int(query_one(
            "SELECT lead_time_days FROM suppliers WHERE id = %s", (created["id"],),
        )["lead_time_days"]) == 12

        upsert_stock(tid, sku, {"current_stock": 20, "supplier": name})
        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_days"] == 12
        assert item["lead_time_source"] == SOURCE_SUPPLIER_RULE
        assert item["lead_time_rule_scope"] == "supplier"

    def test_a_supplier_card_with_no_lead_time_imposes_nothing(
        self, client, auth_headers, test_tenant,
    ):
        """`suppliers.lead_time_days` is NOT NULL DEFAULT 15. Without provenance
        on that column too, merely having a supplier row would inject a 15 nobody
        chose and the SKU would report 'supplier_rule' for our own assumption —
        the same lie, one table over.

        Written against the service (not POST /suppliers) on purpose: the API's
        `SupplierCreate.lead_time_days` is `int = Field(default=15)`, so the
        endpoint cannot currently tell an omitted field from a typed 15 and
        always stamps 'user'. Making it `Optional[int] = None` closes that last
        gap — see the handoff; `backend/api/v1/inventory.py` is another agent's
        file this pass."""
        from backend.inventory import supplier_service as sup_svc

        tid, sku = test_tenant["id"], _sku()
        name = f"Sin-LT-{uuid.uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": name})
        card = query_one(
            "SELECT lead_time_days, lead_time_set_by FROM suppliers "
            "WHERE tenant_id = %s AND name = %s", (tid, name))
        assert int(card["lead_time_days"]) == DEFAULT_LEAD_TIME_DAYS
        assert card["lead_time_set_by"] is None, "nobody claimed this number"

        upsert_stock(tid, sku, {"current_stock": 20, "supplier": name})
        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_DEFAULT
        assert item["lead_time_rule_scope"] is None

    def test_the_api_default_is_the_remaining_gap(
        self, client, auth_headers, test_tenant,
    ):
        """Documents (and pins) the one place provenance is still coarser than it
        should be, so closing it is a visible change rather than a silent one."""
        tid = test_tenant["id"]
        name = f"Via-API-{uuid.uuid4().hex[:6]}"
        _ok(client.post("/api/v1/inventory/suppliers",
                        json={"name": name}, headers=auth_headers), 201)
        card = query_one(
            "SELECT lead_time_set_by FROM suppliers WHERE tenant_id = %s AND name = %s",
            (tid, name))
        assert card["lead_time_set_by"] == SOURCE_USER, (
            "SupplierCreate.lead_time_days defaults to 15, so the endpoint cannot "
            "distinguish an omitted field from a typed one; making it Optional "
            "should flip this to None")

    def test_an_explicit_rule_overrides_the_supplier_card(self, test_tenant):
        """The dedicated rule is the more specific statement of intent."""
        from backend.inventory import supplier_service as sup_svc
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Acme", "lead_time_days": 12})
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 20})
        idx = sd_svc.build_rule_index(tid)
        assert sd_svc.resolve_field("lead_time_days", None, idx, supplier="Acme")[0] == 20

    def test_a_rule_on_another_field_keeps_the_supplier_cards_lead_time(self, test_tenant):
        from backend.inventory import supplier_service as sup_svc
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Acme", "lead_time_days": 12})
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"moq": 48})
        idx = sd_svc.build_rule_index(tid)
        assert sd_svc.resolve_field("lead_time_days", None, idx, supplier="Acme")[0] == 12
        assert sd_svc.resolve_field("moq", None, idx, supplier="Acme")[0] == 48

    def test_a_per_sku_value_beats_the_supplier_rule(
        self, client, auth_headers, test_tenant,
    ):
        """The rule is a default, not an override. A buyer who set this SKU by
        hand must keep their number, or the feature reads as the app silently
        overwriting their work."""
        tid, sku = test_tenant["id"], _sku()
        supplier = f"Prov-{uuid.uuid4().hex[:6]}"
        sd_svc.set_stock_default(tid, "supplier", supplier, {"lead_time_days": 12})
        upsert_stock(tid, sku, {"current_stock": 20, "supplier": supplier,
                                "lead_time_days": 4})

        item = _status_item(client, auth_headers, tid, sku)
        assert item["lead_time_source"] == SOURCE_USER
        assert item["lead_time_days"] == 4
        assert item["lead_time_rule_scope"] is None

    def test_supplier_beats_category_beats_global(self, test_tenant):
        """Narrowest wins — the same precedence the event multipliers use."""
        tid = test_tenant["id"]
        sd_svc.set_stock_default(tid, "global", None, {"lead_time_days": 30})
        sd_svc.set_stock_default(tid, "category", "Bebidas", {"lead_time_days": 20})
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10})
        idx = sd_svc.build_rule_index(tid)

        assert sd_svc.resolve_field("lead_time_days", None, idx,
                                    supplier="Acme", category="Bebidas") == (10, SOURCE_SUPPLIER_RULE, "supplier")
        assert sd_svc.resolve_field("lead_time_days", None, idx,
                                    supplier="Otro", category="Bebidas") == (20, SOURCE_SUPPLIER_RULE, "category")
        assert sd_svc.resolve_field("lead_time_days", None, idx,
                                    supplier="Otro", category="Abarrotes") == (30, SOURCE_SUPPLIER_RULE, "global")
        # No rules at all -> our assumption, honestly labelled.
        assert sd_svc.resolve_field("lead_time_days", None, sd_svc.build_rule_index("nope")) == (
            DEFAULT_LEAD_TIME_DAYS, SOURCE_DEFAULT, None)

    def test_a_rule_that_is_silent_on_a_field_keeps_the_cascade_falling(self, test_tenant):
        """A supplier rule that only sets a lead time must NOT also impose a
        service level of NULL — the next level down still gets its turn."""
        tid = test_tenant["id"]
        sd_svc.set_stock_default(tid, "global", None, {"service_level": 0.99})
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10})
        idx = sd_svc.build_rule_index(tid)

        value, source, scope = sd_svc.resolve_field(
            "service_level", None, idx, supplier="Acme")
        assert (value, source, scope) == (0.99, SOURCE_SUPPLIER_RULE, "global")

    def test_scope_values_are_matched_case_insensitively(self, test_tenant):
        tid = test_tenant["id"]
        sd_svc.set_stock_default(tid, "supplier", "  Distribuidora Sur  ", {"lead_time_days": 9})
        idx = sd_svc.build_rule_index(tid)
        assert sd_svc.resolve_field("lead_time_days", None, idx,
                                    supplier="DISTRIBUIDORA SUR")[0] == 9
        # And the second write updates the same row instead of creating a twin.
        sd_svc.set_stock_default(tid, "supplier", "distribuidora sur", {"lead_time_days": 11})
        rows = query_one(
            "SELECT COUNT(*)::int AS n FROM stock_defaults "
            "WHERE tenant_id = %s AND scope_type = 'supplier'", (tid,))
        assert rows["n"] == 1

    def test_setting_one_field_does_not_wipe_the_others(self, test_tenant):
        tid = test_tenant["id"]
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10, "moq": 24})
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 14})
        row = query_one(
            "SELECT lead_time_days, moq FROM stock_defaults "
            "WHERE tenant_id = %s AND scope_type = 'supplier' AND scope_value = 'acme'",
            (tid,))
        assert int(row["lead_time_days"]) == 14
        assert float(row["moq"]) == 24.0

    def test_out_of_range_and_unknown_scopes_are_rejected(self, test_tenant):
        from backend.errors import AppError
        tid = test_tenant["id"]
        with pytest.raises(AppError) as e1:
            sd_svc.set_stock_default(tid, "warehouse", "Norte", {"lead_time_days": 5})
        assert e1.value.code == "stock_default_bad_scope"
        with pytest.raises(AppError) as e2:
            sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 0})
        assert e2.value.code == "stock_default_out_of_range"
        with pytest.raises(AppError) as e3:
            sd_svc.set_stock_default(tid, "supplier", "", {"lead_time_days": 5})
        assert e3.value.code == "stock_default_missing_scope_value"
        # Nothing was written by any of the three rejected calls.
        assert query_one(
            "SELECT COUNT(*)::int AS n FROM stock_defaults WHERE tenant_id = %s", (tid,)
        )["n"] == 0


class TestReimportDoesNotErasePeopleWork:
    """`bulk_upsert(..., only_fill_missing=True)`.

    A distributor corrects a lead time by hand in March and re-imports their ERP
    stock export in April. Without this, the correction is gone and nothing says
    so. The decision input is the provenance column, not a NULL check — a NULL
    check could not work at all here, because lead_time_days / moq /
    service_level are NOT NULL with a schema default and are therefore never
    null.
    """

    def test_a_hand_edited_value_survives_a_reimport(self, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10, "lead_time_days": 4},
                     source=SOURCE_USER)

        n = bulk_upsert(tid, [{"sku": sku, "current_stock": 99, "lead_time_days": 30}],
                        only_fill_missing=True)
        assert n == 1

        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == 4, "the March correction must survive April"
        assert row["lead_time_set_by"] == SOURCE_USER
        # ...while the quantity, which is a measurement and not a correction,
        # is refreshed — otherwise the importer is a no-op on existing SKUs.
        assert float(query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))["current_stock"]) == 99.0

    def test_a_value_from_a_previous_import_is_refreshed(self, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10, "lead_time_days": 4},
                     source=SOURCE_FILE)

        bulk_upsert(tid, [{"sku": sku, "lead_time_days": 30}], only_fill_missing=True)
        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == 30, (
            "a file value is the importer's own; a newer file must refresh it")
        assert row["lead_time_set_by"] == SOURCE_FILE

    def test_a_value_nobody_ever_set_is_filled(self, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10})
        assert _row(tid, sku)["lead_time_set_by"] is None

        bulk_upsert(tid, [{"sku": sku, "lead_time_days": 30, "supplier": "Acme"}],
                    only_fill_missing=True)
        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == 30
        assert row["lead_time_set_by"] == SOURCE_FILE
        assert query_one(
            "SELECT supplier FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))["supplier"] == "Acme"

    def test_a_non_provenance_field_the_user_filled_is_left_alone(self, test_tenant):
        """supplier/category/notes have no provenance column, so they fall back
        to the plain "only if currently empty" rule."""
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10, "supplier": "Mi Proveedor"})

        bulk_upsert(tid, [{"sku": sku, "supplier": "ERP Export Co"}],
                    only_fill_missing=True)
        assert query_one(
            "SELECT supplier FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))["supplier"] == "Mi Proveedor"

    def test_the_default_is_still_a_full_overwrite(self, test_tenant):
        """Backward compatibility: every existing caller (POST /inventory/bulk
        today) must behave exactly as before when the flag is not passed."""
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10, "lead_time_days": 4},
                     source=SOURCE_USER)

        bulk_upsert(tid, [{"sku": sku, "lead_time_days": 30}])
        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == 30
        assert row["lead_time_set_by"] == SOURCE_FILE

    def test_a_row_with_nothing_left_to_fill_is_not_counted(self, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        upsert_stock(tid, sku, {"current_stock": 10, "lead_time_days": 4},
                     source=SOURCE_USER)
        before = query_one(
            "SELECT updated_at FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))["updated_at"]

        n = bulk_upsert(tid, [{"sku": sku, "lead_time_days": 30}], only_fill_missing=True)
        assert n == 0, "an import that changes nothing must not report a save"
        after = query_one(
            "SELECT updated_at, lead_time_days FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s", (tid, sku))
        assert after["updated_at"] == before, "the row was not touched at all"
        assert int(after["lead_time_days"]) == 4


class TestImpactPreview:
    def test_preview_counts_reach_and_how_many_would_actually_change(self, test_tenant):
        """The UI must show this BEFORE the buyer confirms: 'applies to N SKUs,
        of which M already have their own lead time and keep it'."""
        tid = test_tenant["id"]
        supplier = f"Prov-{uuid.uuid4().hex[:6]}"
        untouched = [_sku() for _ in range(3)]
        for s in untouched:
            upsert_stock(tid, s, {"current_stock": 1, "supplier": supplier})
        owned = _sku()
        upsert_stock(tid, owned, {"current_stock": 1, "supplier": supplier,
                                  "lead_time_days": 7})
        # A SKU of a different supplier must not be counted.
        upsert_stock(tid, _sku(), {"current_stock": 1, "supplier": "Otro"})

        preview = sd_svc.count_affected_skus(tid, "supplier", supplier)
        assert preview["matched_skus"] == 4
        assert preview["would_change"]["lead_time_days"] == 3, (
            "the SKU with its own lead time keeps it and must be excluded")

    def test_delete_removes_the_rule_and_the_cascade_falls_back(self, test_tenant):
        tid = test_tenant["id"]
        rule = sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10})
        assert sd_svc.delete_stock_default(tid, rule["id"]) is True
        assert query_one(
            "SELECT COUNT(*)::int AS n FROM stock_defaults WHERE id = %s", (rule["id"],)
        )["n"] == 0
        assert sd_svc.resolve_field(
            "lead_time_days", None, sd_svc.build_rule_index(tid), supplier="Acme",
        ) == (DEFAULT_LEAD_TIME_DAYS, SOURCE_DEFAULT, None)
        assert sd_svc.delete_stock_default(tid, rule["id"]) is False

    def test_a_rule_is_scoped_to_its_tenant(self, test_tenant):
        """Cross-tenant leakage on a table keyed by a free-text supplier name is
        the failure mode worth a dedicated test."""
        tid = test_tenant["id"]
        sd_svc.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10})
        other_idx = sd_svc.build_rule_index(f"tnt_{uuid.uuid4().hex[:8]}")
        assert sd_svc.resolve_field("lead_time_days", None, other_idx, supplier="Acme") == (
            DEFAULT_LEAD_TIME_DAYS, SOURCE_DEFAULT, None)
