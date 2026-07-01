"""
Seed the LOCAL Postgres with realistic volume for load/stress testing.

Run from backend/:  PYTHONPATH=.. python -m tests._seed_volume

Creates:
  - 5 tenants (real create_tenant → correct quota json)
  - 3 users per tenant via real create_user (so login works), all verified
  - A primary "stress" tenant with heavy volume:
      * N_SESSIONS sessions
      * N_SKUS    inventory_stock rows
      * N_JOBS    jobs
      * session_results JSON on a slice of sessions
Prints a credentials block for the stress driver to use.
"""
import json
import random
import sys
import time
from uuid import uuid4

import psycopg2.extras

from backend.config import settings
from backend.db.connection import init_pool, get_conn, execute, _json
from backend.tenants.service import create_tenant
from backend.users import service as user_svc

# Volume knobs
N_TENANTS      = 5
USERS_PER_TEN  = 3
N_SESSIONS     = 2000
N_SKUS         = 50_000
N_JOBS         = 500
N_RESULTS      = 200
STRESS_PASSWORD = "StressPass123!"

random.seed(42)


def main():
    init_pool(settings.database_url, min_conn=2, max_conn=10)
    t0 = time.time()

    creds = []
    primary = None
    for ti in range(N_TENANTS):
        ten = create_tenant(f"stress-{uuid4().hex[:6]}")
        if ti == 0:
            primary = ten
        for ui in range(USERS_PER_TEN):
            email = f"u{ui}-{uuid4().hex[:6]}@stress.test"
            role = "admin" if ui == 0 else "analyst"
            u = user_svc.create_user(ten["id"], email, STRESS_PASSWORD, role, f"Stress U{ui}")
            user_svc.mark_verified(ten["id"], u["id"])
            if ui == 0:
                creds.append({"tenant_id": ten["id"], "email": email, "password": STRESS_PASSWORD})

    pid = primary["id"]
    admin_uid = None
    # fetch an admin user id for created_by
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id FROM users WHERE tenant_id=%s LIMIT 1", (pid,))
        admin_uid = cur.fetchone()["id"]

    # ── Bulk sessions ────────────────────────────────────────────────────
    session_ids = [f"sess_{uuid4().hex[:12]}" for _ in range(N_SESSIONS)]
    statuses = ["DRAFT", "QUEUED", "RUNNING", "COMPLETED", "FAILED"]
    sess_rows = [
        (sid, pid, f"Session {i}", None, random.choice(statuses), "results",
         admin_uid, json.dumps([]), 1)
        for i, sid in enumerate(session_ids)
    ]
    with get_conn() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO sessions
               (id, tenant_id, name, description, status, pipeline_step,
                created_by, created_at, updated_at, tags, version)
               VALUES %s""",
            sess_rows,
            template="(%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s,%s)",
            page_size=1000,
        )
        conn.commit()

    # ── Bulk inventory_stock ─────────────────────────────────────────────
    inv_rows = []
    for i in range(N_SKUS):
        inv_rows.append((
            f"inv_{uuid4().hex[:12]}", pid, f"SKU-{i:06d}", f"Producto {i}",
            float(random.randint(0, 5000)), float(random.randint(10, 200)),
            random.randint(1, 30), round(random.uniform(0.5, 100), 2),
            float(random.randint(1, 50)), "finished_good", 0.95,
        ))
    with get_conn() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO inventory_stock
               (id, tenant_id, sku, display_name, stock_actual, stock_minimo,
                lead_time_dias, costo_unitario, moq, product_type, service_level,
                created_at, updated_at)
               VALUES %s""",
            inv_rows,
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
            page_size=2000,
        )
        conn.commit()

    # ── Bulk jobs ────────────────────────────────────────────────────────
    job_rows = [
        (f"job_{uuid4().hex[:12]}", pid, random.choice(session_ids), admin_uid,
         random.choice(["COMPLETED", "FAILED", "QUEUED"]), json.dumps({"percent": 100}))
        for _ in range(N_JOBS)
    ]
    with get_conn() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO jobs
               (id, tenant_id, session_id, created_by, status, created_at, progress)
               VALUES %s""",
            job_rows,
            template="(%s,%s,%s,%s,%s,NOW(),%s)",
            page_size=1000,
        )
        conn.commit()

    # ── session_results JSON on a slice ──────────────────────────────────
    def fake_result(sid):
        rows = [{"sku": f"SKU-{j:06d}", "model": "lightgbm", "mae": round(random.random()*10, 3),
                 "wape": round(random.random(), 3)} for j in range(50)]
        return {"metrics": {"rows": rows}, "inventory": {}, "routing": {}}

    res_rows = [(sid, pid, _json(fake_result(sid)), _json({})) for sid in session_ids[:N_RESULTS]]
    with get_conn() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO session_results
               (session_id, tenant_id, training_result, forecasts, updated_at)
               VALUES %s""",
            res_rows,
            template="(%s,%s,%s,%s,NOW())",
            page_size=200,
        )
        conn.commit()

    # ── Counts ───────────────────────────────────────────────────────────
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        counts = {}
        for t in ("tenants", "users", "sessions", "inventory_stock", "jobs", "session_results"):
            cur.execute(f"SELECT count(*) AS c FROM {t}")
            counts[t] = cur.fetchone()["c"]

    out = {
        "elapsed_s": round(time.time() - t0, 1),
        "counts": counts,
        "primary_tenant": pid,
        "credentials": creds,
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
