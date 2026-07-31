"""Tests for the accounting-integrations sync service: fetch provider data
-> import stock -> build a sales dataset -> auto-train (backend/integrations/
sync_service.py). Provider is faked via monkeypatching registry.get_provider
so no real network call is ever made.
"""
from datetime import date

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key", Fernet.generate_key().decode()
    )


def test_sync_imports_stock_dataset_and_enqueues_training(client, test_tenant, monkeypatch, fernet_key):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one

    class FakeProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0) for d in range(1, 32)]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: FakeProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})
    result = sync_service.sync_connection(conn["id"])

    assert result["session_id"] and result["job_id"] and result["dataset_id"]
    assert "SKU-Z" in result["stock_synced"]

    # stock imported
    row = query_one("SELECT current_stock, unit_cost FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
                     (tid, "SKU-Z"))
    assert float(row["current_stock"]) == 12.0
    assert float(row["unit_cost"]) == 5.0

    # a dataset + session were created and a job enqueued
    assert query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"] >= 1
    assert query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"] >= 1

    sess = query_one("SELECT status FROM sessions WHERE id=%s AND tenant_id=%s",
                      (result["session_id"], tid))
    assert sess["status"] == "QUEUED"

    cfg = query_one("SELECT * FROM session_configs WHERE session_id=%s AND tenant_id=%s",
                     (result["session_id"], tid))
    assert cfg["columns_cfg"]["schema_version"] == "canonical_v1"
    assert cfg["models_cfg"]["selected_models"]

    # Store-less provider regression: dataset keeps the exact pre-store
    # 3-column shape and the canonical mapping gains no store entry.
    assert "store" not in cfg["columns_cfg"]["canonical_mapping"]
    from pathlib import Path
    dataset_row = query_one("SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
                             (result["dataset_id"], tid))
    csv_lines = Path(dataset_row["file_path"]).read_text(encoding="utf-8").splitlines()
    assert csv_lines[0] == "fecha,sku,cantidad"

    # last_sync_at set, no error
    conn_row = query_one("SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
                          (conn["id"],))
    assert conn_row["last_sync_at"] is not None
    assert conn_row["last_error"] is None
    assert conn_row["status"] == "connected"


def test_sync_with_store_lines_builds_store_column_and_mapping(client, test_tenant, monkeypatch, fernet_key):
    """When any provider sale line carries a branch/warehouse, the sales
    dataset gains a `store` column (lines without one fall back to
    'principal') and the session's canonical mapping maps it, so training
    groups per (sku, store)."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one
    from pathlib import Path

    class StoreProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            lines = [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0, store="Norte")
                     for d in range(1, 32)]
            # Same sku+date as a Norte line but a different store: must stay
            # a separate dataset row, not be summed into Norte's.
            lines.append(base.ProviderSaleLine(date(2026, 1, 1), "SKU-Z", 5.0, 8.0, store="Sur"))
            # A line with no warehouse in a store-bearing sync falls back to
            # 'principal' so every row keeps a concrete store value.
            lines.append(base.ProviderSaleLine(date(2026, 1, 2), "SKU-Z", 7.0, 8.0))
            return lines
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: StoreProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})
    result = sync_service.sync_connection(conn["id"])

    # Canonical mapping persisted with the store entry (direct DB assert).
    cfg = query_one("SELECT columns_cfg FROM session_configs WHERE session_id=%s AND tenant_id=%s",
                     (result["session_id"], tid))
    assert cfg["columns_cfg"]["canonical_mapping"]["store"] == "store"

    # Dataset on disk has the store column with per-store aggregation.
    dataset_row = query_one("SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
                             (result["dataset_id"], tid))
    csv_lines = Path(dataset_row["file_path"]).read_text(encoding="utf-8").splitlines()
    assert csv_lines[0] == "fecha,sku,cantidad,store"
    rows = {tuple(line.split(",")) for line in csv_lines[1:]}
    assert ("2026-01-01", "SKU-Z", "3.0", "Norte") in rows
    assert ("2026-01-01", "SKU-Z", "5.0", "Sur") in rows
    assert ("2026-01-02", "SKU-Z", "7.0", "principal") in rows  # store-less line fallback


def test_sync_normalizes_warehouse_and_store_spellings(client, test_tenant, monkeypatch, fernet_key):
    """Normalize-at-write at the integrations boundary: a provider sending
    case-variants of an existing warehouse ('NORTE' vs the tenant's 'Norte')
    must land on the existing warehouse/stock rows, and the sales CSV's store
    values must use the same canonical spellings — including intra-sync
    consistency for a warehouse first seen in this very sync ('Sur'/'sur')."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.inventory import warehouse_service as wh_svc
    from backend.db.connection import execute, query, query_one
    from pathlib import Path

    tid = test_tenant["id"]
    # testing_mode=False re-enables plan limits; the default plan allows only
    # 1 location and this scenario legitimately needs 2 ('Norte' + 'Sur').
    execute("UPDATE tenants SET plan = 'enterprise' WHERE id = %s", (tid,))
    wh_svc.create_warehouse(tid, "Norte")  # canonical spelling on file

    class CaseProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self):
            # Distinct SKUs: _merge_products_and_stock keys by sku, one
            # warehouse per sku.
            return [
                base.ProviderStock("SKU-Z", 12.0, "NORTE"),  # variant of existing 'Norte'
                base.ProviderStock("SKU-Y", 4.0, "Sur"),     # new name, first seen here
            ]
        def fetch_sales(self, since=None):
            lines = [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0, store="norte")
                     for d in range(1, 32)]
            # 'sur' must match the 'Sur' spelling introduced by THIS sync's
            # stock import, even though it isn't committed yet.
            lines.append(base.ProviderSaleLine(date(2026, 1, 1), "SKU-Y", 5.0, 8.0, store="sur"))
            return lines
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: CaseProvider(creds))

    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})
    result = sync_service.sync_connection(conn["id"])

    # Stock rows landed on canonical spellings — no 'NORTE' duplicate.
    stock_rows = query(
        "SELECT sku, warehouse FROM inventory_stock WHERE tenant_id=%s AND sku IN ('SKU-Z','SKU-Y') ORDER BY sku",
        (tid,),
    )
    assert [(r["sku"], r["warehouse"]) for r in stock_rows] == [("SKU-Y", "Sur"), ("SKU-Z", "Norte")]
    wh_rows = query(
        "SELECT name FROM warehouses WHERE tenant_id=%s AND LOWER(name) IN ('norte', 'sur') ORDER BY name",
        (tid,),
    )
    assert [w["name"] for w in wh_rows] == ["Norte", "Sur"]

    # Sales CSV store values use the canonical spellings too.
    dataset_row = query_one("SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
                             (result["dataset_id"], tid))
    csv_lines = Path(dataset_row["file_path"]).read_text(encoding="utf-8").splitlines()
    assert csv_lines[0] == "fecha,sku,cantidad,store"
    stores_in_csv = {line.rsplit(",", 1)[1] for line in csv_lines[1:]}
    assert stores_in_csv == {"Norte", "Sur"}


def test_sync_records_error_on_provider_failure(client, test_tenant, monkeypatch, fernet_key):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one

    class BoomProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): raise base.IntegrationSyncError("provider is down")
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: BoomProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "siigo", {"partner_id": "p", "username": "u", "access_key": "k"})

    datasets_before = query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"]
    jobs_before = query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"]

    with pytest.raises(base.IntegrationSyncError):
        sync_service.sync_connection(conn["id"])

    conn_row = query_one("SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
                          (conn["id"],))
    assert conn_row["status"] == "error"
    assert "provider is down" in conn_row["last_error"]
    assert conn_row["last_sync_at"] is not None

    # No dataset/job created on failure
    assert query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"] == datasets_before
    assert query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"] == jobs_before


def test_second_sync_still_trains_on_full_history(client, test_tenant, monkeypatch, fernet_key):
    """Regression guard: sync #1 has `last_sync_at=None` so it naturally sees
    full history, but every later sync must ALSO build the training dataset
    from full history rather than being limited to `since=last_sync_at`
    (which would starve the trainer down to ~1 day of rows, well under
    `validation_cfg.min_history`, and defeat auto-train on every sync after
    the first)."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one

    since_calls = []

    class FakeProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            since_calls.append(since)
            # 30 days of history for one SKU, returned in full regardless of
            # `since` — a real incremental provider call would only return
            # ~1 day's worth on the second sync.
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0) for d in range(1, 31)]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: FakeProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})

    first = sync_service.sync_connection(conn["id"])

    # Sanity: last_sync_at is now set, so a naive `since=last_sync_at` fetch
    # on the second call would only return the last day or so of sales.
    conn_row = query_one("SELECT last_sync_at FROM integration_connections WHERE id=%s", (conn["id"],))
    assert conn_row["last_sync_at"] is not None

    second = sync_service.sync_connection(conn["id"])

    # The fetch used to build the dataset must never be limited by since —
    # neither call should have been passed a non-None value.
    assert since_calls == [None, None]

    # The second sync's dataset must carry the full 30-row history, not a
    # starved slice — proving the trainer won't skip the SKU for being under
    # min_history on repeated syncs.
    dataset_row = query_one(
        "SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
        (second["dataset_id"], tid),
    )
    from pathlib import Path
    csv_text = Path(dataset_row["file_path"]).read_text(encoding="utf-8")
    data_rows = [line for line in csv_text.splitlines() if line.strip()][1:]  # drop header
    assert len(data_rows) >= 20  # >= validation_cfg.min_history (backend/sessions/defaults.py)
    assert first["dataset_id"] != second["dataset_id"]


def test_run_daily_syncs_all_connections_isolating_failures(client, test_tenant, monkeypatch, fernet_key):
    """The daily scheduler loop body (`run_daily_integration_syncs`) must
    attempt every connection across every tenant and isolate per-connection
    failures: one tenant's broken provider must not stop another tenant's
    sync from completing, and the loop itself must never raise."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one, execute
    from backend.tenants.service import create_tenant
    from uuid import uuid4

    class HealthyProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-OK", "Okay", 4.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-OK", 7.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-OK", 2.0, 6.0) for d in range(1, 32)]

    class BrokenProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): raise base.IntegrationSyncError("connection reset by provider")
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []

    def fake_get_provider(name, creds):
        if name == "alegra":
            return HealthyProvider(creds)
        return BrokenProvider(creds)

    monkeypatch.setattr(registry, "get_provider", fake_get_provider)

    healthy_tenant = test_tenant
    broken_tenant = create_tenant(f"pytest-{uuid4().hex[:10]}")
    try:
        healthy_conn = store.create_connection(
            healthy_tenant["id"], "alegra", {"email": "a@b.com", "token": "t"}
        )
        broken_conn = store.create_connection(
            broken_tenant["id"], "siigo", {"partner_id": "p", "username": "u", "access_key": "k"}
        )

        # The loop body must not raise even though one connection fails.
        sync_service.run_daily_integration_syncs()

        healthy_row = query_one(
            "SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
            (healthy_conn["id"],),
        )
        assert healthy_row["last_sync_at"] is not None
        assert healthy_row["status"] == "connected"
        assert healthy_row["last_error"] is None

        broken_row = query_one(
            "SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
            (broken_conn["id"],),
        )
        assert broken_row["status"] == "error"
        assert "connection reset by provider" in broken_row["last_error"]
    finally:
        execute("DELETE FROM tenants WHERE id = %s", (broken_tenant["id"],))


# ── The gate stops an unattended sync, and the tenant has to be able to see it ─
#
# The pre-training gate holds ERP data to the same standard as an upload, which
# is what the product promises. But the upload screen has a human in front of it
# who can pick a remediation, and `run_daily_integration_syncs` runs at 3 a.m.
# with nobody watching. Left as a bare raise, the whole event was one swallowed
# log line: the tenant kept yesterday's forecast, forever, and the only visible
# trace was a red dot with no way to act on it.

def test_a_provider_reporting_nothing_cannot_train_and_the_sync_is_refused(
    client, test_tenant, monkeypatch, fernet_key
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one
    from backend.errors import AppError
    from backend.integrations import base, registry, store, sync_service

    class EmptyProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return []
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: EmptyProvider(creds))

    conn = store.create_connection(test_tenant["id"], "alegra",
                                   {"email": "a@b.com", "token": "t"})
    with pytest.raises(AppError) as exc:
        sync_service.sync_connection(conn["id"])
    assert exc.value.code == "training_blocked_data_fatal"

    row = query_one(
        "SELECT status, last_error, last_error_code, last_error_details "
        "FROM integration_connections WHERE id=%s", (conn["id"],))
    assert row["status"] == "error"
    assert row["last_error_code"] == "training_blocked_data_fatal"
    assert row["last_error_details"]["remediable"] is False
    assert row["last_error_details"]["issues"], "the screen has nothing to name otherwise"


def test_a_sync_blocked_on_a_fixable_finding_carries_the_options_to_the_screen(
    client, test_tenant, monkeypatch, fernet_key
):
    """The tenant must be told WHICH decision is waiting, not just that one is."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one
    from backend.errors import AppError
    from backend.integrations import base, registry, store, sync_service

    class ReturningProvider(base.AccountingProvider):
        """An ERP that books returns as negative sale lines."""
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-R", "Retornos", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-R", 10.0, "principal")]
        def fetch_sales(self, since=None):
            lines = [base.ProviderSaleLine(date(2026, 1, d), "SKU-R", 4.0 + (d % 3), 9.0)
                     for d in range(1, 32)]
            lines.append(base.ProviderSaleLine(date(2026, 2, 2), "SKU-R", -6.0, 9.0))
            return lines
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: ReturningProvider(creds))

    conn = store.create_connection(test_tenant["id"], "alegra",
                                   {"email": "a@b.com", "token": "t"})
    with pytest.raises(AppError) as exc:
        sync_service.sync_connection(conn["id"])
    assert exc.value.code == "training_blocked_unresolved"

    row = query_one(
        "SELECT last_error_code, last_error_details FROM integration_connections "
        "WHERE id=%s", (conn["id"],))
    details = row["last_error_details"]
    assert row["last_error_code"] == "training_blocked_unresolved"
    assert details["remediable"] is True
    assert "negative_target" in details["issues"]
    # The exact options, so the screen can offer them instead of a support ticket.
    assert set(details["options"]["negative_target"]) == {
        "negatives_net_into_period", "negatives_as_zero", "negatives_drop_rows"}
    # And the session the decision belongs to.
    assert details["session_id"]


def test_a_successful_sync_clears_a_previous_gate_refusal(
    client, test_tenant, monkeypatch, fernet_key
):
    """A stale 'blocked' badge would send the user to fix a solved problem."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one
    from backend.integrations import base, registry, store, sync_service

    conn = store.create_connection(test_tenant["id"], "alegra",
                                   {"email": "a@b.com", "token": "t"})
    store.mark_synced(conn["id"], error="blocked", error_code="training_blocked_data_fatal",
                      error_details={"issues": ["all_zeros"]})

    class HealthyProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-H", "Healthy", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-H", 10.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-H", 3.0 + (d % 5), 8.0)
                    for d in range(1, 32)]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: HealthyProvider(creds))

    sync_service.sync_connection(conn["id"])

    row = query_one("SELECT status, last_error_code, last_error_details "
                    "FROM integration_connections WHERE id=%s", (conn["id"],))
    assert row["status"] == "connected"
    assert row["last_error_code"] is None
    assert row["last_error_details"] is None


def test_two_branches_selling_the_same_sku_on_the_same_day_is_not_a_duplicate(
    client, test_tenant, monkeypatch, fernet_key
):
    """The over-block that would have stopped every multi-warehouse tenant forever.

    A session that maps a store column trains on (sku, store); a gate that
    grouped by sku alone would see the two branches' rows as a duplicate and
    demand a decision with no correct answer, on every single sync.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one
    from backend.integrations import base, registry, store, sync_service

    class TwoBranchProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-B", "Both", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-B", 10.0, "Norte")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-B", 3.0 + (d % 4), 8.0,
                                          store=branch)
                    for d in range(1, 32) for branch in ("Norte", "Sur")]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: TwoBranchProvider(creds))

    conn = store.create_connection(test_tenant["id"], "alegra",
                                   {"email": "a@b.com", "token": "t"})
    result = sync_service.sync_connection(conn["id"])

    assert result["job_id"], "a healthy two-branch sync must actually queue a job"
    row = query_one("SELECT status FROM integration_connections WHERE id=%s",
                    (conn["id"],))
    assert row["status"] == "connected"
