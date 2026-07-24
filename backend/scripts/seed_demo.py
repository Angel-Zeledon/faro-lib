"""
Rich, repeatable demo seed for a presentation-ready Faro tenant.

Builds ONE coherent demo tenant — login `demo@faro.app` / `demo1234`, email
pre-verified, enterprise plan so every screen is unlocked — with
business-consistent data across the whole product:

  * 14 SKUs (realistic abarrotes names/categories) with ~18 months of daily
    sales history, trained through the REAL training family (daily + weekly)
    so `/skus` charts and the semáforo are populated.
  * 3 warehouses (principal / Norte / Sur) with stock split by demand share,
    plus one SKU deliberately imbalanced so the network view suggests a
    transfer, and several SKUs that need an order (PEDIR_YA / PEDIR_PRONTO).
  * 3 suppliers with lead times, mapped to SKUs, so the supplier scorecard
    has data (real lead times learned from recorded receptions).
  * PO history in every state — pending, sent, partially received, fully
    received — created through the real ROI + reception services.
  * A couple of shrinkage (merma) records and a seeded LatAm calendar
    (Colombia quincenas + a manual Semana Santa event).

Everything routes through the real services so invariants hold; the script
asserts the key ones (no negative stock, no over-receipt, coherent signals)
with direct DB queries before it finishes.

This module lives under backend/ and therefore MUST stay pandas/numpy free
(enforced by backend/tests/test_no_pandas_in_backend.py) — the dataset CSV is
generated with the stdlib `csv` module.

Run standalone (idempotent — safe to reset + reseed before a demo):

    backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo
    backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo --no-train  # skip ML (fast)
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("seed_demo")

# ── Fixed identity so a reseed always lands on the same login ────────────────
DEMO_TENANT_ID = "ten_faro_demo"
DEMO_EMAIL = "demo@faro.app"
DEMO_PASSWORD = "demo1234"
DEMO_FULL_NAME = "Demo Faro"
DEMO_WHATSAPP = "+50688887777"

WAREHOUSES = [
    {"name": "principal", "is_default": True, "share": 0.60},
    {"name": "Norte", "is_default": False, "share": 0.25},
    {"name": "Sur", "is_default": False, "share": 0.15},
]

SUPPLIERS = [
    {"name": "Distribuidora Andina S.A.", "lead_time_days": 7, "lead_time_std": 2,
     "email": "ventas@andina.co", "whatsapp": "+573001112233", "payment_terms": "contado"},
    {"name": "Granos del Valle", "lead_time_days": 12, "lead_time_std": 3,
     "email": "pedidos@granosdelvalle.co", "whatsapp": "+573004445566", "payment_terms": "quincenal"},
    {"name": "ImportMax LatAm", "lead_time_days": 21, "lead_time_std": 5,
     "email": "orders@importmax.com", "whatsapp": "+573007778899", "payment_terms": "contra entrega"},
]

# SKU catalog. `target` is the semáforo band we want the demo to show for this
# SKU (coverage is expressed as a multiple of the lead time; see
# service._calc_signal thresholds: <0.5 PEDIR_YA, <1.2 PEDIR_PRONTO, <3 OK,
# else SOBRESTOCK). `base` is the mean daily units, `wk` a weekend uplift
# factor, `trend` the fractional growth across the whole window.
#   supplier index -> SUPPLIERS[i]
SKUS = [
    # sku,      name,                    category,     base, wk,  trend, moq, cost,  price, supp, target
    ("SKU-001", "Aceite de Oliva 1L",    "Aceites",     18,  1.15, 0.20, 12,  8.50, 13.90, 0, "PEDIR_YA"),
    ("SKU-002", "Arroz Premium 5kg",     "Granos",      42,  1.10, 0.10, 25,  5.20,  7.80, 1, "SOBRESTOCK"),
    ("SKU-003", "Leche Entera 1L",       "Lácteos",    120,  1.05, 0.05, 50,  1.10,  1.65, 0, "OK"),
    ("SKU-004", "Azúcar Blanca 2kg",     "Endulzantes", 55,  1.08, 0.02, 100, 2.40,  3.30, 1, "SOBRESTOCK"),
    ("SKU-005", "Sal Refinada 1kg",      "Condimentos", 30,  1.02, 0.00, 24,  0.90,  1.40, 0, "OK"),
    ("SKU-006", "Café Molido 500g",      "Bebidas",     26,  1.20, 0.25, 12,  4.80,  7.50, 2, "PEDIR_PRONTO"),
    ("SKU-007", "Frijol Rojo 1kg",       "Granos",      34,  1.06, 0.08, 20,  1.80,  2.80, 1, "OK"),
    ("SKU-008", "Harina de Trigo 1kg",   "Harinas",     40,  1.10, 0.05, 25,  1.30,  2.10, 1, "PEDIR_YA"),
    ("SKU-009", "Pasta Espagueti 500g",  "Pastas",      48,  1.12, 0.06, 24,  0.95,  1.55, 0, "OK"),
    ("SKU-010", "Atún en Lata 140g",     "Enlatados",   22,  1.25, 0.12, 24,  1.15,  1.95, 2, "PEDIR_PRONTO"),
    ("SKU-011", "Papel Higiénico 4un",   "Higiene",     38,  1.05, 0.15, 12,  2.10,  3.40, 0, "OK"),
    ("SKU-012", "Detergente 1kg",        "Limpieza",    28,  1.08, 0.10, 12,  2.60,  4.20, 2, "TRANSFER"),
    ("SKU-013", "Gaseosa Cola 2L",       "Bebidas",     64,  1.30, 0.10, 24,  1.40,  2.30, 0, "OK"),
    ("SKU-014", "Galletas Surtidas 400g","Snacks",      31,  1.18, 0.14, 12,  1.70,  2.90, 2, "PEDIR_PRONTO"),
]

# Coverage multiple of the lead time we aim for per band (mid-band so small
# forecast noise doesn't tip it into a neighbour).
_TARGET_COVERAGE = {
    "PEDIR_YA": 0.30,
    "PEDIR_PRONTO": 0.85,
    "OK": 1.9,
    "SOBRESTOCK": 4.0,
    # Transfer SKU: principal is short (needs an order) but the network holds
    # plenty elsewhere, so the per-warehouse view suggests a transfer instead.
    "TRANSFER": 0.30,
}

HISTORY_DAYS = 540  # ~18 months
_RNG = random.Random("faro-demo-seed-v1")


# ─────────────────────────────────────────────────────────────────────────────
# Reset + identity
# ─────────────────────────────────────────────────────────────────────────────

def _reset_tenant() -> None:
    """Delete any prior demo tenant (all data + storage) so a reseed is clean."""
    from backend.db.connection import execute
    from backend.tenants.data_export import delete_tenant

    # inventory_transfer_* is not covered by delete_tenant's cascade list yet,
    # so clear it explicitly first (children before parent).
    execute("DELETE FROM inventory_transfer_items WHERE tenant_id = %s", (DEMO_TENANT_ID,))
    execute("DELETE FROM inventory_transfer_log WHERE tenant_id = %s", (DEMO_TENANT_ID,))
    try:
        delete_tenant(DEMO_TENANT_ID)
    except Exception as e:  # tenant may not exist yet on first run
        log.info("reset: delete_tenant skipped (%s)", e)


def _create_tenant_and_user() -> str:
    """Create the enterprise demo tenant (fixed id) + verified admin user."""
    from backend.auth.password import hash_password
    from backend.db.connection import execute
    from backend.utils.ids import generate_id

    execute(
        """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, created_at)
           VALUES (%s, %s, %s, 'enterprise', 'active', '{}', '{}', NOW())""",
        (DEMO_TENANT_ID, "Faro Demo", "faro-demo"),
    )
    user_id = generate_id("usr")
    execute(
        """INSERT INTO users
           (id, tenant_id, email, full_name, role, hashed_password,
            email_verified, status, whatsapp_number, created_at, updated_at)
           VALUES (%s, %s, %s, %s, 'admin', %s, TRUE, 'active', %s, NOW(), NOW())""",
        (user_id, DEMO_TENANT_ID, DEMO_EMAIL, DEMO_FULL_NAME,
         hash_password(DEMO_PASSWORD), DEMO_WHATSAPP),
    )
    return user_id


# ─────────────────────────────────────────────────────────────────────────────
# Dataset generation (stdlib csv — no pandas in backend/)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_sales_csv(path: Path) -> None:
    """Write fecha,sku,nombre,cantidad daily sales with weekly seasonality +
    gentle trend + noise. Deterministic."""
    end = date.today()
    start = end - timedelta(days=HISTORY_DAYS)
    weekday_boost = {5: 1.0, 6: 1.0}  # placeholder; per-SKU weekend uplift below
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "sku", "nombre", "cantidad"])
        for sku, name, _cat, base, wk, trend, *_ in SKUS:
            intermittent = sku == "SKU-010"  # canned tuna: bursty demand
            t = 0
            d = start
            while d <= end:
                frac = t / HISTORY_DAYS
                level = base * (1.0 + trend * frac)
                factor = wk if d.weekday() >= 5 else 1.0
                # Payday bumps around the 15th and month end (quincenas).
                if d.day in (14, 15, 16, 29, 30, 1):
                    factor *= 1.12
                noise = _RNG.uniform(0.78, 1.22)
                qty = level * factor * noise
                if intermittent and _RNG.random() < 0.35:
                    qty = 0
                w.writerow([d.isoformat(), sku, name, int(round(max(0, qty)))])
                d += timedelta(days=1)
                t += 1
    _ = weekday_boost


def _register_dataset(tenant_id: str, user_id: str, csv_path: Path) -> str:
    from backend.db.connection import execute
    from backend.storage import paths
    from backend.utils.ids import generate_id

    dataset_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, dataset_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "data.csv"
    dst.write_bytes(csv_path.read_bytes())
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, file_path,
            size_bytes, uploaded_by, uploaded_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (dataset_id, tenant_id, "Ventas Demo Faro", "ventas_demo.csv", "csv",
         str(dst), dst.stat().st_size, user_id),
    )
    return dataset_id


# ─────────────────────────────────────────────────────────────────────────────
# Training (real family, run synchronously)
# ─────────────────────────────────────────────────────────────────────────────

def _train(tenant_id: str, user_id: str, dataset_id: str) -> str:
    """Create a session, seed the quickstart configs, fan out the granularity
    family and run every queued job synchronously. Returns the base (daily)
    session id — the one the semáforo/skus screens use by default."""
    from backend.db import session_store
    from backend.sessions import family_service as fam
    from backend.sessions import service as session_svc
    from backend.sessions.defaults import default_quickstart_configs
    from backend.training.job_service import list_jobs_for_session
    from backend.workers.runner import run_training_job

    s = session_svc.create_session(tenant_id, user_id, "Demo Faro")
    session_id = s.get("session_id") or s["id"]
    session_svc.attach_dataset(tenant_id, session_id, dataset_id)
    for field, cfg in default_quickstart_configs().items():
        session_store.set_field(tenant_id, session_id, field, cfg)
    session_svc.force_status(tenant_id, session_id, "MODELS_CONFIGURED")

    family = fam.launch_training_family(tenant_id, session_id, user_id)
    members = family["sessions"]
    log.info("training %d family member(s) synchronously...", len(members))
    for m in members:
        sid = m["session_id"]
        jobs = list_jobs_for_session(tenant_id, sid)
        if not jobs:
            continue
        job_id = jobs[0]["id"]
        log.info("  training %s (%s)...", m["granularity"], sid)
        run_training_job(tenant_id, sid, job_id)
    return session_id


# ─────────────────────────────────────────────────────────────────────────────
# Warehouses / suppliers / stock
# ─────────────────────────────────────────────────────────────────────────────

def _seed_warehouses(tenant_id: str) -> None:
    from backend.inventory import warehouse_service as wh
    for w in WAREHOUSES:
        wh.create_warehouse(tenant_id, w["name"], is_default=w["is_default"])
        wh.set_demand_share(tenant_id, w["name"], w["share"] * 100)


def _seed_suppliers(tenant_id: str) -> dict[str, str]:
    """Create suppliers, return name -> supplier_id."""
    from backend.inventory import supplier_service as sup
    ids: dict[str, str] = {}
    for s in SUPPLIERS:
        row = sup.create_supplier(tenant_id, s)
        ids[s["name"]] = row["id"]
    return ids


def _avg_daily_for(tenant_id: str, session_id: str, sku: str, lead_time: int) -> float:
    """Average daily forecast demand over the lead-time window, exactly as the
    semáforo computes it — used to place stock precisely in a target band."""
    from backend.db import session_store
    from backend.inventory.series import rollup_by_sku
    from backend.inventory.service import _avg_daily_forecast

    forecasts = rollup_by_sku(session_store.get_forecasts(tenant_id, session_id) or {})
    model_forecasts = forecasts.get(sku, {})
    avg_daily, _ = _avg_daily_forecast(model_forecasts, max(1, lead_time))
    return avg_daily


def _seed_stock_and_sku_suppliers(
    tenant_id: str, session_id: str, supplier_ids: dict[str, str], trained: bool
) -> None:
    from backend.inventory import service as inv
    from backend.inventory import supplier_service as sup

    for (sku, name, category, base, wk, trend, moq, cost, price, supp_idx, target) in SKUS:
        supplier = SUPPLIERS[supp_idx]
        lead_time = supplier["lead_time_days"]

        # Precise placement: coverage_target * avg_daily = total desired stock.
        # Fall back to the generated mean when training was skipped.
        avg_daily = _avg_daily_for(tenant_id, session_id, sku, lead_time) if trained else 0.0
        if avg_daily <= 0:
            avg_daily = base
        total_stock = max(moq, round(_TARGET_COVERAGE[target] * lead_time * avg_daily))

        if target == "TRANSFER":
            # principal starved, Norte holding a big surplus of the same SKU →
            # network view proposes moving Norte→principal instead of buying.
            splits = {
                "principal": max(1, round(0.30 * lead_time * avg_daily)),
                "Norte": max(moq, round(6.0 * lead_time * avg_daily)),
                "Sur": max(1, round(1.5 * lead_time * avg_daily)),
            }
        else:
            splits = {w["name"]: max(0, round(total_stock * w["share"])) for w in WAREHOUSES}

        for wh_name, qty in splits.items():
            inv.upsert_stock(tenant_id, sku, {
                "display_name": name,
                "category": category,
                "current_stock": qty,
                "min_stock": max(1, round(0.5 * lead_time * avg_daily)),
                "lead_time_days": lead_time,
                "unit_cost": cost,
                "sale_price": price,
                "moq": moq,
                "supplier": supplier["name"],
                "warehouse": wh_name,
            })

        sup.upsert_sku_supplier(tenant_id, sku, supplier_ids[supplier["name"]], {
            "is_primary": True, "unit_cost": cost, "moq": moq, "lead_time_days": lead_time,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Purchase orders (real ROI + reception services), in varied states
# ─────────────────────────────────────────────────────────────────────────────

def _po_line(sku_row, qty, signal, status="approved"):
    sku, name, _cat, base, wk, trend, moq, cost, price, supp_idx, target = sku_row
    return {
        "sku": sku, "display_name": name, "supplier": SUPPLIERS[supp_idx]["name"],
        "signal": signal, "recommended_qty": qty, "final_qty": qty,
        "unit_cost": cost, "status": status, "warehouse": "principal",
    }


def _backdate_po(po_id: str, generated_days_ago: int, sent_days_ago: int | None) -> None:
    from backend.db.connection import execute
    gen = datetime.now(timezone.utc) - timedelta(days=generated_days_ago)
    execute("UPDATE inventory_po_log SET generated_at = %s WHERE id = %s", (gen, po_id))
    if sent_days_ago is not None:
        sent = datetime.now(timezone.utc) - timedelta(days=sent_days_ago)
        execute("UPDATE inventory_po_log SET sent_at = %s WHERE id = %s", (sent, po_id))


def _seed_purchase_orders(tenant_id: str, session_id: str, user_id: str) -> None:
    from backend.db.connection import execute
    from backend.inventory import reception_service as rec
    from backend.inventory.roi_service import log_po_generation

    by_sku = {r[0]: r for r in SKUS}

    # PO A — fully received 25 days ago (feeds supplier lead-time learning).
    po_a = log_po_generation(tenant_id, session_id, [
        _po_line(by_sku["SKU-002"], 100, "SOBRESTOCK"),
        _po_line(by_sku["SKU-004"], 200, "SOBRESTOCK"),
        _po_line(by_sku["SKU-007"], 60, "PEDIR_PRONTO"),
    ], destination_warehouse="principal")
    _backdate_po(po_a["id"], generated_days_ago=25, sent_days_ago=24)
    rec.receive_po(tenant_id, po_a["id"], user_id,
                   received_at=datetime.now(timezone.utc) - timedelta(days=17))

    # PO B — partially received 6 days ago (still open on the floor).
    po_b = log_po_generation(tenant_id, session_id, [
        _po_line(by_sku["SKU-001"], 48, "PEDIR_YA"),
        _po_line(by_sku["SKU-008"], 50, "PEDIR_YA"),
        _po_line(by_sku["SKU-006"], 24, "PEDIR_PRONTO"),
    ], destination_warehouse="principal")
    _backdate_po(po_b["id"], generated_days_ago=6, sent_days_ago=5)
    rec.receive_po(tenant_id, po_b["id"], user_id,
                   lines=[{"sku": "SKU-001", "received_qty": 48},
                          {"sku": "SKU-008", "received_qty": 25}],
                   received_at=datetime.now(timezone.utc) - timedelta(days=1))

    # PO C — sent 2 days ago, not received yet (in transit).
    po_c = log_po_generation(tenant_id, session_id, [
        _po_line(by_sku["SKU-014"], 24, "PEDIR_PRONTO"),
        _po_line(by_sku["SKU-010"], 48, "PEDIR_PRONTO"),
    ], destination_warehouse="principal")
    _backdate_po(po_c["id"], generated_days_ago=2, sent_days_ago=2)
    execute("UPDATE inventory_po_log SET reception_status = 'pending' WHERE id = %s", (po_c["id"],))

    # PO D — generated today, still pending send (fresh in the queue).
    po_d = log_po_generation(tenant_id, session_id, [
        _po_line(by_sku["SKU-001"], 24, "PEDIR_YA"),
        _po_line(by_sku["SKU-008"], 25, "PEDIR_YA"),
    ], destination_warehouse="principal")
    _ = po_d


# ─────────────────────────────────────────────────────────────────────────────
# Transfers, shrinkage, calendar
# ─────────────────────────────────────────────────────────────────────────────

def _seed_transfers(tenant_id: str, user_id: str) -> None:
    """One completed transfer (history) + leave the imbalanced SKU-012 for the
    presenter to create live from the suggestion."""
    from backend.inventory import transfer_service as tr
    t = tr.create_transfer(
        tenant_id, user_id, from_warehouse="Norte", to_warehouse="Sur",
        items=[{"sku": "SKU-003", "qty": 40}],
        notes="Reabastecimiento satélite Sur",
    )
    # Receive it so /pedidos transfer history shows a closed loop.
    tr.receive_transfer(tenant_id, t["id"], lines=[{"sku": "SKU-003", "received_qty": 40}])


def _seed_shrinkage(tenant_id: str, user_id: str) -> None:
    from backend.inventory import shrinkage_service as sh
    sh.record_shrinkage(tenant_id, "SKU-003", 12, "expiry", user_id=user_id,
                        warehouse="principal", notes="Lote vencido en góndola",
                        occurred_at=datetime.now(timezone.utc) - timedelta(days=4))
    sh.record_shrinkage(tenant_id, "SKU-013", 6, "breakage", user_id=user_id,
                        warehouse="principal", notes="Botellas rotas en bodega",
                        occurred_at=datetime.now(timezone.utc) - timedelta(days=2))


def _seed_calendar(tenant_id: str) -> None:
    from backend.inventory import service as inv
    years = [date.today().year, date.today().year + 1]
    inv.seed_calendar_events(tenant_id, country="CO", years=years)
    # A manual event on top of the catalog so the simulator has an obvious hook.
    y = date.today().year + (0 if date.today().month <= 3 else 1)
    inv.create_event(tenant_id, {
        "name": "Semana Santa",
        "start_date": date(y, 3, 24).isoformat(),
        "end_date": date(y, 3, 31).isoformat(),
        "multiplier": 1.6,
        "notes": "Pico de demanda por vacaciones (demo)",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Invariants
# ─────────────────────────────────────────────────────────────────────────────

def _assert_invariants(tenant_id: str, session_id: str, trained: bool) -> dict:
    from backend.db.connection import query, query_one
    from backend.inventory import service as inv

    # No negative stock anywhere.
    neg = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s AND current_stock < 0",
        (tenant_id,))
    assert neg["c"] == 0, f"negative stock rows: {neg['c']}"

    # No over-receipt: received_qty must never exceed final_qty on any line.
    over = query_one(
        "SELECT COUNT(*) AS c FROM inventory_po_items "
        "WHERE tenant_id = %s AND received_qty > final_qty + 0.001",
        (tenant_id,))
    assert over["c"] == 0, f"over-received PO lines: {over['c']}"

    stock_rows = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tenant_id,))
    po_count = query_one(
        "SELECT COUNT(*) AS c FROM inventory_po_log WHERE tenant_id = %s", (tenant_id,))
    events = query_one(
        "SELECT COUNT(*) AS c FROM inventory_events WHERE tenant_id = %s", (tenant_id,))

    signals: dict[str, int] = {}
    if trained:
        status = inv.get_inventory_status(tenant_id, session_id)
        for row in status:
            signals[row["signal"]] = signals.get(row["signal"], 0) + 1
        # A good demo must show variety — at least an order and a healthy row.
        assert signals.get("PEDIR_YA", 0) >= 1, f"expected >=1 PEDIR_YA, got {signals}"
        assert signals.get("OK", 0) >= 1, f"expected >=1 OK, got {signals}"

    return {
        "stock_rows": stock_rows["c"],
        "pos": po_count["c"],
        "events": events["c"],
        "signals": signals,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def seed(train: bool = True) -> dict:
    from backend.config import settings
    from backend.db.connection import init_pool
    from backend.db import migrations

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_pool(settings.database_url)
    migrations.run_all()

    log.info("[1/9] resetting demo tenant %s ...", DEMO_TENANT_ID)
    _reset_tenant()
    user_id = _create_tenant_and_user()

    log.info("[2/9] generating sales history (%d SKUs x %d days) ...", len(SKUS), HISTORY_DAYS)
    tmp_csv = Path(settings.storage_path) / "_seed_tmp" / "ventas_demo.csv"
    _generate_sales_csv(tmp_csv)
    dataset_id = _register_dataset(DEMO_TENANT_ID, user_id, tmp_csv)

    session_id = None
    if train:
        log.info("[3/9] training forecast family (real ML — this takes a few minutes) ...")
        session_id = _train(DEMO_TENANT_ID, user_id, dataset_id)
    else:
        log.info("[3/9] --no-train: creating session without ML (semáforo will be sparse)")
        from backend.db import session_store
        from backend.sessions import service as session_svc
        from backend.sessions.defaults import default_quickstart_configs
        s = session_svc.create_session(DEMO_TENANT_ID, user_id, "Demo Faro")
        session_id = s.get("session_id") or s["id"]
        session_svc.attach_dataset(DEMO_TENANT_ID, session_id, dataset_id)
        for field, cfg in default_quickstart_configs().items():
            session_store.set_field(DEMO_TENANT_ID, session_id, field, cfg)
        session_svc.force_status(DEMO_TENANT_ID, session_id, "MODELS_CONFIGURED")

    log.info("[4/9] warehouses + demand shares ...")
    _seed_warehouses(DEMO_TENANT_ID)

    log.info("[5/9] suppliers + SKU mappings ...")
    supplier_ids = _seed_suppliers(DEMO_TENANT_ID)

    log.info("[6/9] purchase orders (varied states) ...")
    # POs mutate stock via receptions, so run them BEFORE placing final stock.
    _seed_purchase_orders(DEMO_TENANT_ID, session_id, user_id)

    log.info("[7/9] stock levels tuned to the semáforo bands ...")
    _seed_stock_and_sku_suppliers(DEMO_TENANT_ID, session_id, supplier_ids, trained=train)

    log.info("[8/9] transfers, mermas, calendar ...")
    _seed_transfers(DEMO_TENANT_ID, user_id)
    _seed_shrinkage(DEMO_TENANT_ID, user_id)
    _seed_calendar(DEMO_TENANT_ID)

    log.info("[9/9] verifying invariants ...")
    summary = _assert_invariants(DEMO_TENANT_ID, session_id, trained=train)

    summary.update({
        "tenant_id": DEMO_TENANT_ID,
        "login": {"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
        "session_id": session_id,
        "dataset_id": dataset_id,
        "trained": train,
    })
    log.info("\n=== DEMO SEED COMPLETE ===")
    log.info("login: %s / %s", DEMO_EMAIL, DEMO_PASSWORD)
    log.info("stock rows: %s | POs: %s | events: %s", summary["stock_rows"], summary["pos"], summary["events"])
    log.info("semáforo: %s", summary["signals"])
    return summary


def _main() -> None:
    ap = argparse.ArgumentParser(description="Seed a presentation-ready Faro demo tenant.")
    ap.add_argument("--no-train", action="store_true", help="Skip real ML training (fast, sparse semáforo)")
    args = ap.parse_args()
    seed(train=not args.no_train)


if __name__ == "__main__":
    _main()
