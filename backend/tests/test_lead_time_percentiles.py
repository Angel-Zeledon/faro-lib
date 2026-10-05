"""Observed lead-time tail (p80 / p95): the pure shaping gate, plus DB tests of
the SQL PERCENTILE_CONT (linear interpolation) on the supplier card and scorecard."""
import pytest

from backend.inventory.supplier_service import shape_lead_time_percentiles

NEEDED = 3


def test_below_threshold_returns_none():
    assert shape_lead_time_percentiles(8.0, 12.0, 2, NEEDED) == (None, None)


def test_at_threshold_is_reported_and_rounded():
    assert shape_lead_time_percentiles(8.04, 11.96, 3, NEEDED) == (8.0, 12.0)


def test_missing_or_nan_values_return_none():
    assert shape_lead_time_percentiles(None, 10.0, 5, NEEDED) == (None, None)
    assert shape_lead_time_percentiles(8.0, None, 5, NEEDED) == (None, None)
    assert shape_lead_time_percentiles(float("nan"), 10.0, 5, NEEDED) == (None, None)


def test_all_same_day_deliveries_are_unusable():
    # duplicates all at 0 days: percentile is 0, which measures nothing
    assert shape_lead_time_percentiles(0.0, 0.0, 6, NEEDED) == (None, None)


def test_duplicate_values_give_equal_percentiles():
    assert shape_lead_time_percentiles(7.0, 7.0, 6, NEEDED) == (7.0, 7.0)


def test_p95_never_prints_below_p80():
    assert shape_lead_time_percentiles(9.0, 8.0, 5, NEEDED) == (9.0, 9.0)


# --- DB tests (written for the central run; they need Postgres) -------------

def _seed(tenant_id, supplier, days):
    from uuid import uuid4
    from backend.db.connection import execute
    po_id = str(uuid4())
    execute(
        "INSERT INTO inventory_po_log (id, tenant_id, session_id) VALUES (%s, %s, %s)",
        (po_id, tenant_id, "s"),
    )
    for d in days:
        execute(
            """INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
               VALUES (%s, %s, %s, %s)""",
            (tenant_id, supplier, po_id, d),
        )


def _supplier_row(tid, name):
    from backend.db.connection import execute
    from backend.inventory.supplier_service import list_suppliers
    execute("INSERT INTO suppliers (tenant_id, name) VALUES (%s, %s)", (tid, name))
    return next(s for s in list_suppliers(tid) if s["name"] == name)


def test_supplier_list_exposes_interpolated_percentiles(test_tenant):
    tid = test_tenant["id"]
    _seed(tid, "Acme", [5, 6, 7, 8, 20])
    row = _supplier_row(tid, "Acme")
    # PERCENTILE_CONT position = (n-1)*p: p80 -> 3.2 -> 8 + 0.2*12 = 10.4
    assert row["lead_time_p80_days"] == pytest.approx(10.4)
    # p95 -> 3.8 -> 8 + 0.8*12 = 17.6
    assert row["lead_time_p95_days"] == pytest.approx(17.6)


def test_supplier_list_hides_percentiles_below_threshold(test_tenant):
    tid = test_tenant["id"]
    _seed(tid, "Thin", [5, 30])
    row = _supplier_row(tid, "Thin")
    assert row["lead_time_p80_days"] is None and row["lead_time_p95_days"] is None


def test_scorecard_carries_the_same_percentiles(test_tenant):
    from backend.inventory.reception_service import get_supplier_scorecard
    tid = test_tenant["id"]
    _seed(tid, "Acme", [5, 6, 7, 8, 20])
    row = next(r for r in get_supplier_scorecard(tid) if r["supplier"] == "Acme")
    assert row["lead_time_p80_days"] == pytest.approx(10.4)
    assert row["lead_time_p95_days"] == pytest.approx(17.6)
