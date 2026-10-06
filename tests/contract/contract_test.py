"""Contract tests: replay the same HTTP cases against the Python API and the
Rust API and diff what comes back.

    python tests/contract/contract_test.py \
        --python http://127.0.0.1:8011 --rust http://127.0.0.1:8021 \
        --env-file backend/.env --db postgresql://postgres:postgres@127.0.0.1:5546/forecasting

What it does, in order:

1. Signs up a THROWAWAY tenant through the Python API, with a login on the
   `stockai.demo` domain the email transport refuses (backend/trial/), so no
   mail leaves the machine. Verifies it, logs in, invites an analyst and a
   viewer, and mints a read key and a write key - all through the Python API.
2. For each case, sends the identical request to both services and compares
   the status code, the error code and the normalized body (ids, timestamps
   and other per-call values are masked, see `normalize`).
3. Where a case writes, also compares the ROW each implementation wrote and
   the activity_logs row it recorded, read straight from the database (the
   testing mandate: assert state, not just status codes).
4. Erases the throwaway tenant through the Python API (`DELETE /tenant` with
   the typed confirmation), unless `--keep` is given.

Only the standard library is required. `--db` needs psycopg2 (the backend
venv has it); without `--db` the state checks are skipped and reported so.

The Python test suite (backend/tests) is the SPEC; this file is the bridge
that proves a Rust route answers like the Python route it replaces.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

API = "/api/v1"
# Generous: a dev backend talking to its database through an SSH tunnel takes
# tens of seconds on a read that runs eight queries.
HTTP_TIMEOUT = 180

# Keys whose VALUES differ per call by construction; masked before comparing.
VOLATILE_KEYS = {
    "id", "ids", "timestamp", "created_at", "updated_at", "status_changed_at",
    "last_run_at",
}


# ── HTTP ─────────────────────────────────────────────────────────────────────

@dataclass
class Resp:
    status: int
    body: Any
    raw: bytes
    headers: dict


def http(base: str, method: str, path: str, *, token: Optional[str] = None,
         body: Any = None, raw_body: Optional[bytes] = None,
         content_type: Optional[str] = "application/json",
         headers: Optional[dict] = None) -> Resp:
    url = base.rstrip("/") + path
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and content_type:
        req.add_header("Content-Type", content_type)
    if token is not None:
        req.add_header("Authorization", token if " " in token else f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
            status, hdrs = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        status, hdrs = e.code, dict(e.headers)
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = {"__non_json__": raw.decode("utf-8", "replace")[:200]}
    return Resp(status, parsed, raw, {k.lower(): v for k, v in hdrs.items()})


# ── Token minting (same claims and algorithm as jwt_handler.create_access_token)

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_access_token(secret: str, user_id: str, tenant_id: str, role: str,
                      email_verified: bool = True, *, exp_minutes: float = 15,
                      token_type: str = "access", extra: Optional[dict] = None,
                      alg: str = "HS256") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id, "tenant_id": tenant_id, "role": role,
        "email_verified": email_verified, "jti": secrets.token_hex(8),
        "type": token_type, "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=exp_minutes)).timestamp(),
    }
    payload.update(extra or {})
    header = {"alg": alg, "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    digest = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}[alg]
    sig = hmac.new(secret.encode(), signing_input.encode(), digest).digest()
    return f"{signing_input}.{_b64(sig)}"


def read_env_file(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip().upper()] = v.strip().strip('"').strip("'")
    return out


# ── Normalization and diff ───────────────────────────────────────────────────

def normalize(value: Any, extra_volatile: set) -> Any:
    volatile = VOLATILE_KEYS | extra_volatile
    if isinstance(value, dict):
        return {k: ("<masked>" if k in volatile else normalize(v, extra_volatile))
                for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, extra_volatile) for v in value]
    if isinstance(value, float) and value.is_integer():
        # 5 vs 5.0 is the same JSON number to every client; keep the diff on
        # values, not on the formatter's choice.
        return value
    return value


def diff(a: Any, b: Any, path: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: only in rust = {json.dumps(b[k])[:120]}")
            elif k not in b:
                out.append(f"{path}.{k}: only in python = {json.dumps(a[k])[:120]}")
            else:
                out.extend(diff(a[k], b[k], f"{path}.{k}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: list length python={len(a)} rust={len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff(x, y, f"{path}[{i}]"))
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return [] if float(a) == float(b) else [f"{path}: python={a!r} rust={b!r}"]
    return [] if a == b else [f"{path}: python={json.dumps(a)[:160]} rust={json.dumps(b)[:160]}"]


# ── Fixture: one throwaway tenant ────────────────────────────────────────────

@dataclass
class Fixture:
    tenant_id: str
    admin_id: str
    admin_token: str
    analyst_id: str
    viewer_id: str
    secret: str
    read_key: str = ""
    write_key: str = ""
    tokens: dict = field(default_factory=dict)

    def token(self, who: str) -> Optional[str]:
        return self.tokens.get(who)


def make_fixture(py: str, secret: str) -> Fixture:
    tag = secrets.token_hex(4)
    email = f"contract-{tag}@stockai.demo"
    password = "Contract-" + secrets.token_urlsafe(12) + "9a"
    phone = "+5068" + "".join(secrets.choice("0123456789") for _ in range(7))
    r = http(py, "POST", f"{API}/auth/signup", body={
        "email": email, "password": password, "tenant_name": f"Contract {tag}",
        "full_name": "Contract Harness", "whatsapp_number": phone, "accept_terms": True,
    })
    if r.status != 201:
        raise SystemExit(f"signup failed: {r.status} {r.body}")
    data = r.body["data"]
    tenant_id, admin_id = data["tenant"]["id"], data["user"]["id"]
    verify_url = data.get("verify_url")
    if verify_url:
        token = verify_url.split("token=", 1)[1]
        v = http(py, "POST", f"{API}/auth/verify-email", body={"token": token})
        if v.status != 200:
            raise SystemExit(f"verify failed: {v.status} {v.body}")
    r = http(py, "POST", f"{API}/auth/login", body={"email": email, "password": password})
    if r.status != 200:
        raise SystemExit(f"login failed: {r.status} {r.body}")
    admin_token = r.body["data"]["access_token"]

    ids = {}
    for role in ("analyst", "viewer"):
        r = http(py, "POST", f"{API}/users", token=admin_token, body={
            "email": f"contract-{tag}-{role}@stockai.demo", "role": role,
            "full_name": f"Contract {role}"})
        if r.status != 201:
            http(py, "DELETE", f"{API}/tenant", token=admin_token, body={"confirm": "DELETE"})
            raise SystemExit(f"creating the {role} failed: {r.status} {r.body}")
        ids[role] = r.body["data"]["user"]["id"]

    fx = Fixture(tenant_id, admin_id, admin_token, ids["analyst"], ids["viewer"], secret)
    fx.tokens = {
        "admin": admin_token,
        # The invited users have a temporary password nobody knows; their
        # tokens are minted with the deployment secret, with exactly the
        # claims the login endpoint would have put in them.
        "analyst": mint_access_token(secret, ids["analyst"], tenant_id, "analyst"),
        "viewer": mint_access_token(secret, ids["viewer"], tenant_id, "viewer"),
        "none": None,
    }
    for scope in ("read", "write"):
        r = http(py, "POST", f"{API}/api-keys", token=admin_token,
                 body={"name": f"contract-{scope}", "scope": scope})
        if r.status == 200:
            setattr(fx, f"{scope}_key", r.body["data"]["key"])
            fx.tokens[f"key_{scope}"] = r.body["data"]["key"]
        else:
            print(f"  (no {scope} API key: {r.status} {r.body.get('error_code') if isinstance(r.body, dict) else r.body})")
    return fx


def erase_fixture(py: str, fx: Fixture) -> None:
    # A fresh token: the login one may have expired during a long run.
    token = mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin")
    r = http(py, "DELETE", f"{API}/tenant", token=token, body={"confirm": "DELETE"})
    print(f"\nthrowaway tenant {fx.tenant_id} erased: {r.status}")


# ── Cases ────────────────────────────────────────────────────────────────────

@dataclass
class Case:
    name: str
    method: str
    path: str                      # may contain {cd_py}/{cd_rs} placeholders
    who: str = "admin"
    body: Any = None
    raw_body: Optional[bytes] = None
    content_type: Optional[str] = "application/json"
    volatile: set = field(default_factory=set)
    # Called with (fixture, python Resp, rust Resp, db) -> list of problems.
    state_check: Optional[Callable] = None
    route: str = ""                # which migrated route this exercises
    # Called with (fixture, db, side) right before EACH side's request, side
    # being "py" then "rs": resets the state both must start from, and may
    # snapshot what the Python call left before the Rust call runs.
    setup: Optional[Callable] = None
    # Extra request headers; a value "{key_read}" / "{key_write}" is replaced
    # by the fixture's key.
    headers: dict = field(default_factory=dict)


def today_plus(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def build_cases(fx: Fixture) -> list[Case]:
    future = today_plus(60)
    good = {"sku": "CT-SKU-1", "delivery_date": future, "quantity": 120,
            "customer": "  ACME Corp  ", "probability": 0.8, "note": " first "}
    cases = [
        # ── health ─────────────────────────────────────────────────────────
        Case("health", "GET", "/health", who="none", route="GET /health",
             volatile={"queued_jobs", "last_boundary", "last_status", "last_error"}),
        # ── entitlements ───────────────────────────────────────────────────
        Case("entitlements admin", "GET", f"{API}/entitlements", route="GET /entitlements"),
        Case("entitlements viewer", "GET", f"{API}/entitlements", who="viewer", route="GET /entitlements"),
        Case("entitlements read key", "GET", f"{API}/entitlements", who="key_read", route="GET /entitlements"),
        Case("entitlements no auth", "GET", f"{API}/entitlements", who="none", route="GET /entitlements"),
        Case("entitlements basic scheme", "GET", f"{API}/entitlements", who="raw:Basic abc", route="GET /entitlements"),
        Case("entitlements garbage jwt", "GET", f"{API}/entitlements", who="raw:Bearer abc.def", route="GET /entitlements"),
        Case("entitlements bad signature", "GET", f"{API}/entitlements", who="bad_signature", route="GET /entitlements"),
        Case("entitlements expired", "GET", f"{API}/entitlements", who="expired", route="GET /entitlements"),
        Case("entitlements wrong type", "GET", f"{API}/entitlements", who="refresh_type", route="GET /entitlements"),
        Case("entitlements HS512", "GET", f"{API}/entitlements", who="hs512", route="GET /entitlements"),
        Case("entitlements unknown key", "GET", f"{API}/entitlements", who="raw:Bearer sk_live_nope", route="GET /entitlements"),
        # ── committed demand: create ───────────────────────────────────────
        Case("cd create analyst", "POST", f"{API}/committed-demand", who="analyst", body=good,
             route="POST /committed-demand", state_check=check_created_row),
        Case("cd create admin minimal", "POST", f"{API}/committed-demand",
             body={"sku": "CT-SKU-2", "delivery_date": future, "quantity": "7.5"},
             route="POST /committed-demand", state_check=check_created_row),
        Case("cd create week date", "POST", f"{API}/committed-demand",
             body={"sku": "CT-SKU-3", "delivery_date": "2027-W10-2", "quantity": 1,
                   "on_top_of_base": "no"},
             route="POST /committed-demand", state_check=check_created_row),
        Case("cd create viewer denied", "POST", f"{API}/committed-demand", who="viewer", body=good,
             route="POST /committed-demand", state_check=check_nothing_written),
        Case("cd create no auth", "POST", f"{API}/committed-demand", who="none", body=good,
             route="POST /committed-demand"),
        Case("cd create write key refused", "POST", f"{API}/committed-demand", who="key_write",
             body=good, route="POST /committed-demand"),
        Case("cd create bad date", "POST", f"{API}/committed-demand",
             body={**good, "delivery_date": "2026/12/01"}, route="POST /committed-demand"),
        Case("cd create date too far", "POST", f"{API}/committed-demand",
             body={**good, "delivery_date": today_plus(3651)}, route="POST /committed-demand"),
        Case("cd create blank sku", "POST", f"{API}/committed-demand",
             body={**good, "sku": "   "}, route="POST /committed-demand"),
        Case("cd create unknown warehouse", "POST", f"{API}/committed-demand",
             body={**good, "warehouse_id": "wh_does_not_exist"}, route="POST /committed-demand"),
        Case("cd create validation zero qty", "POST", f"{API}/committed-demand",
             body={**good, "quantity": 0}, route="POST /committed-demand"),
        Case("cd create validation many", "POST", f"{API}/committed-demand",
             body={"sku": "", "quantity": "abc", "probability": 1.5, "on_top_of_base": "maybe",
                   "customer": 12, "note": "x" * 301}, route="POST /committed-demand"),
        Case("cd create validation huge qty", "POST", f"{API}/committed-demand",
             body={**good, "quantity": 2e9, "warehouse_id": "w" * 65}, route="POST /committed-demand"),
        Case("cd create body is a list", "POST", f"{API}/committed-demand", body=[1, 2],
             route="POST /committed-demand"),
        Case("cd create no body", "POST", f"{API}/committed-demand", route="POST /committed-demand"),
        Case("cd create invalid json", "POST", f"{API}/committed-demand", raw_body=b"{nope",
             route="POST /committed-demand", volatile={"ctx", "loc"}),
        Case("cd create invalid json no auth", "POST", f"{API}/committed-demand", who="none",
             raw_body=b"{nope", route="POST /committed-demand", volatile={"ctx", "loc"}),
        # ── committed demand: bulk ─────────────────────────────────────────
        Case("cd bulk ok", "POST", f"{API}/committed-demand/bulk", who="analyst",
             body={"rows": [{"sku": "CT-B1", "delivery_date": future, "quantity": 3},
                            {"sku": "CT-B2", "delivery_date": future, "quantity": 4,
                             "customer": "Beta"}]},
             route="POST /committed-demand/bulk", state_check=check_bulk_rows),
        Case("cd bulk one bad row", "POST", f"{API}/committed-demand/bulk",
             body={"rows": [{"sku": "CT-B3", "delivery_date": future, "quantity": 3},
                            {"sku": " ", "delivery_date": "nope", "quantity": 4},
                            {"sku": "CT-B5", "delivery_date": today_plus(4000), "quantity": 4}]},
             route="POST /committed-demand/bulk"),
        Case("cd bulk empty", "POST", f"{API}/committed-demand/bulk", body={"rows": []},
             route="POST /committed-demand/bulk"),
        Case("cd bulk nested validation", "POST", f"{API}/committed-demand/bulk",
             body={"rows": [{"sku": "A", "delivery_date": future, "quantity": -1}, "x"]},
             route="POST /committed-demand/bulk"),
        Case("cd bulk viewer denied", "POST", f"{API}/committed-demand/bulk", who="viewer",
             body={"rows": [{"sku": "CT-B9", "delivery_date": future, "quantity": 3}]},
             route="POST /committed-demand/bulk"),
        # ── committed demand: patch / status (each side edits its own row) ──
        Case("cd patch", "PATCH", f"{API}/committed-demand/{{cd}}", who="analyst",
             body={"quantity": 99, "customer": None, "note": "  edited  "},
             route="PATCH /committed-demand/{id}", state_check=check_patched_row),
        Case("cd patch null sku", "PATCH", f"{API}/committed-demand/{{cd}}", body={"sku": None},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch null quantity", "PATCH", f"{API}/committed-demand/{{cd}}", body={"quantity": None},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch null date", "PATCH", f"{API}/committed-demand/{{cd}}", body={"delivery_date": None},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch empty", "PATCH", f"{API}/committed-demand/{{cd}}", body={},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch not found", "PATCH", f"{API}/committed-demand/nope-123", body={"quantity": 1},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch literal bulk", "PATCH", f"{API}/committed-demand/bulk", body={"quantity": 1},
             route="PATCH /committed-demand/{id}"),
        Case("cd patch viewer denied", "PATCH", f"{API}/committed-demand/{{cd}}", who="viewer",
             body={"quantity": 1}, route="PATCH /committed-demand/{id}"),
        Case("cd status fulfilled", "POST", f"{API}/committed-demand/{{cd}}/status", who="analyst",
             body={"status": "fulfilled"}, route="POST /committed-demand/{id}/status",
             state_check=check_status_row),
        Case("cd patch closed", "PATCH", f"{API}/committed-demand/{{cd}}", body={"quantity": 5},
             route="PATCH /committed-demand/{id}"),
        Case("cd status reopen", "POST", f"{API}/committed-demand/{{cd}}/status",
             body={"status": "open"}, route="POST /committed-demand/{id}/status"),
        Case("cd status invalid", "POST", f"{API}/committed-demand/{{cd}}/status",
             body={"status": "done"}, route="POST /committed-demand/{id}/status"),
        Case("cd status not found", "POST", f"{API}/committed-demand/nope-123/status",
             body={"status": "cancelled"}, route="POST /committed-demand/{id}/status"),
        Case("cd status viewer denied", "POST", f"{API}/committed-demand/{{cd}}/status", who="viewer",
             body={"status": "cancelled"}, route="POST /committed-demand/{id}/status"),
        Case("cd wrong method", "GET", f"{API}/committed-demand/{{cd}}", route="(405 shape)"),
    ]
    cases += build_r1_cases(fx)
    return cases


# ── State checks (direct DB reads) ───────────────────────────────────────────

ROW_COLS = ("sku", "warehouse_id", "delivery_date", "quantity", "customer", "probability",
            "on_top_of_base", "status", "note", "created_by", "status_changed_by")


def _row(db, cid):
    cur = db.cursor()
    cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM committed_demand WHERE id = %s", (cid,))
    r = cur.fetchone()
    return dict(zip(ROW_COLS, r)) if r else None


def _events(db, tenant_id, resource):
    cur = db.cursor()
    cur.execute("""SELECT action, user_id, context, status FROM activity_logs
                    WHERE tenant_id = %s AND resource = %s ORDER BY created_at""",
                (tenant_id, resource))
    return [dict(zip(("action", "user_id", "context", "status"), r)) for r in cur.fetchall()]


def _compare_rows(db, fx, py_id, rs_id) -> list[str]:
    a, b = _row(db, py_id), _row(db, rs_id)
    if a is None or b is None:
        return [f"row missing: python={a is not None} rust={b is not None}"]
    problems = [f"db column {k}: python={a[k]!r} rust={b[k]!r}" for k in ROW_COLS if a[k] != b[k]]
    ea, eb = _events(db, fx.tenant_id, py_id), _events(db, fx.tenant_id, rs_id)
    if ea != eb:
        problems.append(f"activity_logs differ: python={ea} rust={eb}")
    if not ea:
        problems.append("no activity_logs row was recorded")
    return problems


def check_created_row(fx, rp, rr, db):
    if rp.status != 201 or rr.status != 201:
        return []
    return _compare_rows(db, fx, rp.body["data"]["id"], rr.body["data"]["id"])


def check_patched_row(fx, rp, rr, db):
    if rp.status != 200 or rr.status != 200:
        return []
    return _compare_rows(db, fx, rp.body["data"]["id"], rr.body["data"]["id"])


check_status_row = check_patched_row


def check_bulk_rows(fx, rp, rr, db):
    if rp.status != 201 or rr.status != 201:
        return []
    problems = []
    for pid, rid in zip(rp.body["data"]["ids"], rr.body["data"]["ids"]):
        a, b = _row(db, pid), _row(db, rid)
        problems += [f"bulk row {k}: python={a[k]!r} rust={b[k]!r}" for k in ROW_COLS if a[k] != b[k]]
    ea, eb = _events(db, fx.tenant_id, "bulk"), []
    # Both sides write resource='bulk'; compare the last two rows pairwise.
    if len(ea) < 2 or ea[-1]["context"] != ea[-2]["context"] or ea[-1]["action"] != ea[-2]["action"]:
        problems.append(f"bulk activity rows differ or are missing: {ea[-2:]}")
    return problems + [p for p in eb]


def check_nothing_written(fx, rp, rr, db):
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM committed_demand WHERE tenant_id = %s AND sku = %s "
                "AND created_by = %s", (fx.tenant_id, "CT-SKU-1", fx.viewer_id))
    n = cur.fetchone()[0]
    return [] if n == 0 else [f"viewer wrote {n} rows"]


# ── Route group R1: preferences, activity, models, alerts, currency, timezone

R1_OTHER_TENANT = "t_contract_nowhere"


def _r1_scratch(fx) -> dict:
    if not hasattr(fx, "r1"):
        fx.r1 = {}
    return fx.r1


def _r1_exec(db, sql, params=()):
    cur = db.cursor()
    cur.execute(sql, params)
    return cur


# Rows the bell and the history read back. Minutes ago, action, status,
# context. Deliveries 1-3 are one stockout fan-out (email + WhatsApp within
# 15 minutes of the newest) and a second run 16 minutes older; the rest are
# system events of every severity and a failed delivery of another kind.
R1_SEED = [
    (10, "inventory_alert_email", "success",
     {"channel": "email", "critical": 3, "warning": 2, "recipient": "a@x.test"}),
    (11, "inventory_alert_whatsapp", "failed",
     {"channel": "whatsapp", "reason": "transport_error", "critical": 3, "warning": 2}),
    (26, "inventory_alert_email", "success", {"critical": 1, "warning": 0}),
    (30, "training.failed", "success",
     {"session_id": "s_ct", "session_name": "Contract", "started_by": "u", "severity": "critical",
      "kind": "training", "reason": "engine_error"}),
    (60, "limit.reached", "success",
     {"limit": "max_skus", "ceiling": 100, "severity": "warning", "kind": "limit",
      "reason": "plan_limit_reached", "reason_params": {"limit": "max_skus"}}),
    (120, "monthly_roi_email", "failed", {"reason": "not_configured", "month": "2026-09"}),
    (180, "data.stock_imported", "success",
     {"rows_read": 10, "rows_written": 10, "severity": "info", "kind": "data", "secret": "x"}),
    (240, "training.blocked", "failed",
     {"session_id": "s_ct2", "issues": 2, "severity": "warning", "kind": "training",
      "reason": "data_gate_blocked"}),
    (300, "supplier_lead_time_alert_email", "success", {"suppliers": ["A", "B"], "channel": "fax"}),
    (360, "data_freshness_reminder_whatsapp", "success",
     {"sales_age_days": 9, "stock_age_days": None, "silent_warehouses": 1}),
]


def r1_seed_alerts(fx, db, side):
    """Seed the throwaway tenant's activity_logs once (both sides read it)."""
    sc = _r1_scratch(fx)
    if sc.get("seeded"):
        return
    for i, (mins, action, status, ctx) in enumerate(R1_SEED):
        _r1_exec(db, """INSERT INTO activity_logs (id, tenant_id, user_id, action, resource, context, status, created_at)
                        VALUES (%s, %s, %s, %s, NULL, %s::jsonb, %s, NOW() - make_interval(mins => %s))""",
                 (f"act_ct{secrets.token_hex(4)}", fx.tenant_id, fx.admin_id, action, json.dumps(ctx), status, mins))
    sc["seeded"] = True


# preferences ---------------------------------------------------------------

def _prefs_row(db, user_id):
    r = _r1_exec(db, "SELECT tenant_id, language, theme, dm_sms_enabled FROM user_preferences WHERE user_id = %s",
                 (user_id,)).fetchone()
    return None if r is None else dict(zip(("tenant_id", "language", "theme", "dm_sms_enabled"), r))


def r1_prefs_reset(user_attr, preset=None):
    """Before each side: snapshot what Python left (before Rust runs), then
    reset the user's row to `preset` (None = no row, i.e. the defaults)."""
    def setup(fx, db, side):
        user_id = getattr(fx, user_attr)
        if side == "rs":
            _r1_scratch(fx)[f"prefs_py_{user_id}"] = _prefs_row(db, user_id)
        _r1_exec(db, "DELETE FROM user_preferences WHERE user_id = %s", (user_id,))
        if preset is not None:
            _r1_exec(db, """INSERT INTO user_preferences (user_id, tenant_id, language, theme, dm_sms_enabled)
                            VALUES (%s, %s, %s, %s, %s)""", (user_id, fx.tenant_id, *preset))
    return setup


def r1_prefs_check(user_attr, expect):
    def check(fx, rp, rr, db):
        user_id = getattr(fx, user_attr)
        a, b = _r1_scratch(fx).get(f"prefs_py_{user_id}"), _prefs_row(db, user_id)
        problems = [] if a == b else [f"user_preferences differ: python={a} rust={b}"]
        if expect is not None and b != expect(fx):
            problems.append(f"user_preferences not as expected: {b}")
        return problems
    return check


# alerts/read ----------------------------------------------------------------

def r1_check_mark_read(fx, rp, rr, db):
    rows = _r1_exec(db, """SELECT user_id, resource, context, status FROM activity_logs
                           WHERE tenant_id = %s AND action = 'alerts_marked_read' AND user_id = %s
                           ORDER BY created_at DESC LIMIT 2""", (fx.tenant_id, fx.viewer_id)).fetchall()
    if len(rows) != 2:
        return [f"expected one marker row per side, found {len(rows)}"]
    return [] if rows[0] == rows[1] else [f"marker rows differ: {rows}"]


# tenant settings (currency / timezone) --------------------------------------

def _settings(db, tenant_id):
    r = _r1_exec(db, "SELECT settings FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    return r[0] if r else None


def r1_settings_reset(key, value=None, *, remove=False):
    """Before each side: snapshot (rs), then put settings[key] back to `value`
    (or drop the key), keeping every other key as it was."""
    def setup(fx, db, side):
        if side == "py":
            _r1_scratch(fx)["audit_before"] = len(_audit_rows(db, fx.tenant_id, 10_000))
        if side == "rs":
            _r1_scratch(fx)["settings_py"] = _settings(db, fx.tenant_id)
        if remove:
            _r1_exec(db, "UPDATE tenants SET settings = COALESCE(settings, '{}'::jsonb) - %s WHERE id = %s",
                     (key, fx.tenant_id))
        else:
            _r1_exec(db, """UPDATE tenants SET settings = COALESCE(settings, '{}'::jsonb)
                            || jsonb_build_object(%s, %s::jsonb) WHERE id = %s""",
                     (key, json.dumps(value), fx.tenant_id))
    return setup


def _audit_rows(db, tenant_id, n=2):
    return _r1_exec(db, """SELECT user_id, resource, context, status FROM activity_logs
                           WHERE tenant_id = %s AND action = 'audit.config.changed'
                           ORDER BY created_at DESC LIMIT %s""", (tenant_id, n)).fetchall()


def r1_check_currency_write(expected_code):
    def check(fx, rp, rr, db):
        problems = []
        a, b = _r1_scratch(fx).get("settings_py"), _settings(db, fx.tenant_id)
        if a != b:
            problems.append(f"tenants.settings differ: python={a} rust={b}")
        if rp.status != 200 or rr.status != 200:
            return problems + [f"expected 200 on both sides: python={rp.status} rust={rr.status}"]
        if (b or {}).get("currency") != expected_code:
            problems.append(f"settings.currency is {(b or {}).get('currency')!r}")
        rows = _audit_rows(db, fx.tenant_id)
        if len(rows) != 2 or rows[0] != rows[1]:
            problems.append(f"audit rows differ or are missing: {rows}")
        return problems
    return check


def r1_check_no_audit_and_settings(fx, rp, rr, db):
    """A refused write changed nothing: settings as the setup left them and
    no audit row added by either side."""
    problems = []
    a, b = _r1_scratch(fx).get("settings_py"), _settings(db, fx.tenant_id)
    if a != b:
        problems.append(f"tenants.settings changed: python={a} rust={b}")
    n = len(_audit_rows(db, fx.tenant_id, 10_000)) - _r1_scratch(fx).get("audit_before", 0)
    if n:
        problems.append(f"{n} audit row(s) written by a refused call")
    return problems


def r1_set_tier(tier, quota=None):
    """Put the throwaway tenant on `tier` (and `quota`) before each side."""
    def setup(fx, db, side):
        _r1_exec(db, "UPDATE tenants SET tier = %s, quota = %s::jsonb WHERE id = %s",
                 (tier, json.dumps(quota or {}), fx.tenant_id))
    return setup


def r1_seed_trainings(fx, db, side):
    """Training jobs the daily counter must and must not count, seeded once.
    Every job is COMPLETED or FAILED, never QUEUED: the worker only claims
    QUEUED rows, so nothing here is ever picked up."""
    sc = _r1_scratch(fx)
    if sc.get("trainings"):
        return
    tag = secrets.token_hex(3)
    for name, backtest in (("base", False), ("bt", True)):
        _r1_exec(db, """INSERT INTO sessions (id, tenant_id, name, status, is_backtest)
                        VALUES (%s, %s, %s, 'COMPLETED', %s)""",
                 (f"ses_ct{name}{tag}", fx.tenant_id, f"contract {name}", backtest))
    jobs = [  # (session, status, started, created minutes ago)
        ("base", "COMPLETED", True, 1),        # counted
        ("base", "FAILED", False, 1),          # not: failed before a worker started it
        ("bt", "COMPLETED", True, 1),          # not: back-test
        ("base", "COMPLETED", True, 60 * 48),  # not: two days ago
    ]
    for i, (sess, status, started, mins) in enumerate(jobs):
        _r1_exec(db, """INSERT INTO jobs (id, tenant_id, session_id, created_by, status, created_at, started_at)
                        VALUES (%s, %s, %s, %s, %s, NOW() - make_interval(mins => %s),
                                CASE WHEN %s THEN NOW() - make_interval(mins => %s) END)""",
                 (f"job_ct{i}{tag}", fx.tenant_id, f"ses_ct{sess}{tag}", fx.admin_id, status, mins,
                  started, mins))
    sc["trainings"] = True


def r1_check_one_training(fx, rp, rr, db):
    got = [r.body["data"]["usage"].get("trainings_today") if r.status == 200 else None for r in (rp, rr)]
    return [] if got == [1, 1] else [f"trainings_today python={got[0]} rust={got[1]} (expected 1)"]


def r1_both(*setups):
    def setup(fx, db, side):
        for s in setups:
            s(fx, db, side)
    return setup


def build_r1_cases(fx: Fixture) -> list[Case]:
    other = "raw:Bearer " + mint_access_token(fx.secret, fx.admin_id, R1_OTHER_TENANT, "admin")
    me_prefs, me_act = f"{API}/me/preferences", f"{API}/me/activity"
    cur, tz = f"{API}/tenant/currency", f"{API}/tenant/timezone"
    defaults_for = lambda lang, theme, sms: (lambda f: {  # noqa: E731
        "tenant_id": f.tenant_id, "language": lang, "theme": theme, "dm_sms_enabled": sms})
    R = {
        "prefs_get": "GET /me/preferences", "prefs_patch": "PATCH /me/preferences",
        "act": "GET /me/activity", "act_types": "GET /me/activity/action-types",
        "models": "GET /models", "alerts": "GET /alerts", "alerts_act": "GET /alerts/activity",
        "kinds": "GET /alerts/kinds", "read": "POST /alerts/read",
        "cur_get": "GET /tenant/currency", "cur_patch": "PATCH /tenant/currency",
        "tz_get": "GET /tenant/timezone",
    }
    seed = r1_seed_alerts
    return [
        # ── models (unauthenticated) ───────────────────────────────────────
        Case("r1 models no auth", "GET", f"{API}/models", who="none", route=R["models"]),
        Case("r1 models garbage auth", "GET", f"{API}/models", who="raw:Bearer abc.def", route=R["models"]),
        Case("r1 models read key", "GET", f"{API}/models", who="key_read", route=R["models"]),
        # ── preferences ────────────────────────────────────────────────────
        Case("r1 prefs get defaults viewer", "GET", me_prefs, who="viewer", route=R["prefs_get"],
             setup=r1_prefs_reset("viewer_id")),
        Case("r1 prefs patch viewer", "PATCH", me_prefs, who="viewer", body={"language": "en"},
             route=R["prefs_patch"], setup=r1_prefs_reset("viewer_id"),
             state_check=r1_prefs_check("viewer_id", defaults_for("en", "dark", False))),
        Case("r1 prefs get stored viewer", "GET", me_prefs, who="viewer", route=R["prefs_get"]),
        Case("r1 prefs patch analyst all lax", "PATCH", me_prefs, who="analyst",
             body={"language": "en", "theme": "light", "dm_sms_enabled": "yes"},
             route=R["prefs_patch"], setup=r1_prefs_reset("analyst_id"),
             state_check=r1_prefs_check("analyst_id", defaults_for("en", "light", True))),
        Case("r1 prefs patch admin partial nulls", "PATCH", me_prefs,
             body={"language": "en", "theme": None}, route=R["prefs_patch"],
             setup=r1_prefs_reset("admin_id", ("es", "light", True)),
             state_check=r1_prefs_check("admin_id", defaults_for("en", "light", True))),
        Case("r1 prefs patch empty body object", "PATCH", me_prefs, body={}, route=R["prefs_patch"],
             setup=r1_prefs_reset("admin_id"),
             state_check=r1_prefs_check("admin_id", defaults_for("es", "dark", False))),
        Case("r1 prefs patch bad language", "PATCH", me_prefs, body={"language": "fr", "theme": "blue"},
             route=R["prefs_patch"]),
        Case("r1 prefs patch bad theme", "PATCH", me_prefs, body={"language": "es", "theme": "blue"},
             route=R["prefs_patch"]),
        Case("r1 prefs patch validation", "PATCH", me_prefs,
             body={"language": 3, "theme": ["x"], "dm_sms_enabled": "maybe"}, route=R["prefs_patch"]),
        Case("r1 prefs patch no body", "PATCH", me_prefs, route=R["prefs_patch"]),
        Case("r1 prefs patch list body", "PATCH", me_prefs, body=[1], route=R["prefs_patch"]),
        Case("r1 prefs patch text body", "PATCH", me_prefs, raw_body=b"language=en",
             content_type="text/plain", route=R["prefs_patch"]),
        Case("r1 prefs patch invalid json no auth", "PATCH", me_prefs, who="none", raw_body=b"{x",
             route=R["prefs_patch"], volatile={"ctx", "loc"}),
        Case("r1 prefs patch no auth", "PATCH", me_prefs, who="none", body={"language": "en"},
             route=R["prefs_patch"]),
        Case("r1 prefs patch read key", "PATCH", me_prefs, who="key_read", body={"language": "en"},
             route=R["prefs_patch"]),
        Case("r1 prefs patch write key", "PATCH", me_prefs, who="key_write", body={"language": "en"},
             route=R["prefs_patch"]),
        Case("r1 prefs get read key", "GET", me_prefs, who="key_read", route=R["prefs_get"]),
        Case("r1 prefs get no auth", "GET", me_prefs, who="none", route=R["prefs_get"]),
        Case("r1 prefs get expired", "GET", me_prefs, who="expired", route=R["prefs_get"]),
        Case("r1 prefs get wrong tenant", "GET", me_prefs, who=other, route=R["prefs_get"],
             setup=r1_prefs_reset("admin_id", ("en", "light", True))),
        Case("r1 prefs patch wrong tenant", "PATCH", me_prefs, who=other, body={"theme": "light"},
             route=R["prefs_patch"], setup=r1_prefs_reset("admin_id", ("en", "dark", True)),
             state_check=r1_prefs_check("admin_id", None)),
        # ── alerts (reads first, on seeded rows, before anyone marks read) ──
        Case("r1 alerts admin", "GET", f"{API}/alerts", route=R["alerts"], setup=seed),
        Case("r1 alerts viewer limit 2", "GET", f"{API}/alerts?limit=2", who="viewer", route=R["alerts"]),
        Case("r1 alerts read key", "GET", f"{API}/alerts", who="key_read", route=R["alerts"]),
        Case("r1 alerts limit repeated", "GET", f"{API}/alerts?limit=1&limit=3", route=R["alerts"]),
        Case("r1 alerts limit 0", "GET", f"{API}/alerts?limit=0", route=R["alerts"]),
        Case("r1 alerts limit 101", "GET", f"{API}/alerts?limit=101", route=R["alerts"]),
        Case("r1 alerts limit text", "GET", f"{API}/alerts?limit=5.5", route=R["alerts"]),
        Case("r1 alerts limit 5.00", "GET", f"{API}/alerts?limit=5.00", route=R["alerts"]),
        Case("r1 alerts no auth", "GET", f"{API}/alerts?limit=0", who="none", route=R["alerts"]),
        Case("r1 alerts wrong tenant", "GET", f"{API}/alerts", who=other, route=R["alerts"]),
        Case("r1 alerts activity", "GET", f"{API}/alerts/activity", route=R["alerts_act"]),
        Case("r1 alerts activity viewer page", "GET", f"{API}/alerts/activity?limit=2&offset=1",
             who="viewer", route=R["alerts_act"]),
        Case("r1 alerts activity read key", "GET", f"{API}/alerts/activity", who="key_read",
             route=R["alerts_act"]),
        Case("r1 alerts activity kind", "GET", f"{API}/alerts/activity?kind=stockout_digest",
             route=R["alerts_act"]),
        Case("r1 alerts activity unknown kind", "GET", f"{API}/alerts/activity?kind=nope&limit=7",
             route=R["alerts_act"]),
        Case("r1 alerts activity blank kind", "GET", f"{API}/alerts/activity?kind=", route=R["alerts_act"]),
        Case("r1 alerts activity critical", "GET", f"{API}/alerts/activity?severity=critical",
             route=R["alerts_act"]),
        Case("r1 alerts activity warning", "GET", f"{API}/alerts/activity?severity=warning",
             route=R["alerts_act"]),
        Case("r1 alerts activity info+kind", "GET", f"{API}/alerts/activity?severity=info&kind=data",
             route=R["alerts_act"]),
        Case("r1 alerts activity training critical", "GET",
             f"{API}/alerts/activity?kind=training&severity=critical", route=R["alerts_act"]),
        Case("r1 alerts activity bad severity", "GET", f"{API}/alerts/activity?severity=bogus",
             route=R["alerts_act"]),
        Case("r1 alerts activity blank severity", "GET", f"{API}/alerts/activity?severity=",
             route=R["alerts_act"]),
        Case("r1 alerts activity three errors", "GET",
             f"{API}/alerts/activity?limit=0&offset=x&severity=CRITICAL", route=R["alerts_act"]),
        Case("r1 alerts activity huge offset", "GET",
             f"{API}/alerts/activity?offset=99999999999999999999", route=R["alerts_act"]),
        Case("r1 alerts activity no auth", "GET", f"{API}/alerts/activity", who="none", route=R["alerts_act"]),
        Case("r1 alerts kinds", "GET", f"{API}/alerts/kinds", who="viewer", route=R["kinds"]),
        Case("r1 alerts kinds read key", "GET", f"{API}/alerts/kinds", who="key_read", route=R["kinds"]),
        Case("r1 alerts kinds no auth", "GET", f"{API}/alerts/kinds", who="none", route=R["kinds"]),
        Case("r1 alerts read viewer", "POST", f"{API}/alerts/read", who="viewer", route=R["read"],
             volatile={"last_read_at"}, state_check=r1_check_mark_read),
        Case("r1 alerts after read viewer", "GET", f"{API}/alerts", who="viewer", route=R["alerts"]),
        Case("r1 alerts read junk body", "POST", f"{API}/alerts/read", who="viewer", raw_body=b"{nope",
             route=R["read"], volatile={"last_read_at"}, state_check=r1_check_mark_read),
        Case("r1 alerts read read key", "POST", f"{API}/alerts/read", who="key_read", route=R["read"]),
        Case("r1 alerts read write key", "POST", f"{API}/alerts/read", who="key_write", route=R["read"]),
        Case("r1 alerts read no auth", "POST", f"{API}/alerts/read", who="none", route=R["read"]),
        Case("r1 alerts read wrong method", "GET", f"{API}/alerts/read", route=R["read"]),
        # ── /me/activity (after the markers, so the feed has the viewer's rows)
        Case("r1 me activity admin", "GET", me_act, route=R["act"]),
        Case("r1 me activity page", "GET", f"{me_act}?limit=2&offset=1", route=R["act"]),
        Case("r1 me activity action", "GET", f"{me_act}?action=training.failed", route=R["act"]),
        Case("r1 me activity blank action", "GET", f"{me_act}?action=&limit=3", route=R["act"]),
        Case("r1 me activity viewer", "GET", me_act, who="viewer", route=R["act"]),
        Case("r1 me activity errors", "GET", f"{me_act}?limit=201&offset=-1", route=R["act"]),
        Case("r1 me activity limit text", "GET", f"{me_act}?limit=+0_5", route=R["act"]),
        Case("r1 me activity read key", "GET", me_act, who="key_read", route=R["act"]),
        Case("r1 me activity no auth", "GET", me_act, who="none", route=R["act"]),
        Case("r1 me activity wrong tenant", "GET", me_act, who=other, route=R["act"]),
        Case("r1 me action types admin", "GET", f"{me_act}/action-types", route=R["act_types"]),
        Case("r1 me action types viewer", "GET", f"{me_act}/action-types", who="viewer", route=R["act_types"]),
        Case("r1 me action types write key", "GET", f"{me_act}/action-types", who="key_write",
             route=R["act_types"]),
        # ── currency ───────────────────────────────────────────────────────
        Case("r1 currency get default viewer", "GET", cur, who="viewer", route=R["cur_get"],
             setup=r1_settings_reset("currency", remove=True)),
        Case("r1 currency get read key", "GET", cur, who="key_read", route=R["cur_get"]),
        Case("r1 currency get no auth", "GET", cur, who="none", route=R["cur_get"]),
        Case("r1 currency get wrong tenant", "GET", cur, who=other, route=R["cur_get"]),
        Case("r1 currency patch admin", "PATCH", cur, body={"code": " usd "}, route=R["cur_patch"],
             setup=r1_settings_reset("currency", remove=True), state_check=r1_check_currency_write("USD")),
        Case("r1 currency patch from usd", "PATCH", cur, body={"code": "eur"}, route=R["cur_patch"],
             setup=r1_settings_reset("currency", "USD"), state_check=r1_check_currency_write("EUR")),
        Case("r1 currency get stored", "GET", cur, who="analyst", route=R["cur_get"]),
        Case("r1 currency patch analyst denied", "PATCH", cur, who="analyst", body={"code": "MXN"},
             route=R["cur_patch"], setup=r1_settings_reset("currency", "USD"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 currency patch viewer denied", "PATCH", cur, who="viewer", body={"code": "MXN"},
             route=R["cur_patch"], setup=r1_settings_reset("currency", "USD"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 currency patch write key", "PATCH", cur, who="key_write", body={"code": "MXN"},
             route=R["cur_patch"]),
        Case("r1 currency patch read key", "PATCH", cur, who="key_read", body={"code": "MXN"},
             route=R["cur_patch"]),
        Case("r1 currency patch no auth", "PATCH", cur, who="none", body={"code": "MXN"}, route=R["cur_patch"]),
        Case("r1 currency patch unsupported", "PATCH", cur, body={"code": " xyz"}, route=R["cur_patch"],
             setup=r1_settings_reset("currency", "USD"), state_check=r1_check_no_audit_and_settings),
        Case("r1 currency patch not a string", "PATCH", cur, body={"code": 5}, route=R["cur_patch"]),
        Case("r1 currency patch missing code", "PATCH", cur, body={"currency": "USD"}, route=R["cur_patch"]),
        Case("r1 currency patch no body", "PATCH", cur, route=R["cur_patch"]),
        Case("r1 currency patch invalid json viewer", "PATCH", cur, who="viewer", raw_body=b"[1,",
             route=R["cur_patch"], volatile={"ctx", "loc"}),
        Case("r1 currency get unsupported stored", "GET", cur, route=R["cur_get"],
             setup=r1_settings_reset("currency", "XXX")),
        Case("r1 currency get list stored", "GET", cur, route=R["cur_get"],
             setup=r1_settings_reset("currency", ["USD"])),
        # ── timezone (GET only; PATCH stays on Python) ─────────────────────
        Case("r1 timezone get default", "GET", tz, who="viewer", route=R["tz_get"],
             setup=r1_settings_reset("timezone", remove=True)),
        Case("r1 timezone get utc", "GET", tz, who="analyst", route=R["tz_get"],
             setup=r1_settings_reset("timezone", "UTC")),
        Case("r1 timezone get unsupported", "GET", tz, route=R["tz_get"],
             setup=r1_settings_reset("timezone", "Mars/Base")),
        Case("r1 timezone get empty string", "GET", tz, route=R["tz_get"],
             setup=r1_settings_reset("timezone", "")),
        Case("r1 timezone get read key", "GET", tz, who="key_read", route=R["tz_get"],
             setup=r1_settings_reset("timezone", "Europe/Madrid")),
        Case("r1 timezone get no auth", "GET", tz, who="none", route=R["tz_get"]),
        Case("r1 timezone get wrong tenant", "GET", tz, who=other, route=R["tz_get"]),
        # ── entitlements on main's plans (Full 1000/5/3, daily training cap) ─
        Case("r1 entitlements free + trainings", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_both(r1_set_tier("free"), r1_seed_trainings), state_check=r1_check_one_training),
        Case("r1 entitlements paid", "GET", f"{API}/entitlements", who="viewer", route="GET /entitlements",
             setup=r1_set_tier("paid")),
        Case("r1 entitlements corporate", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_set_tier("corporate")),
        Case("r1 entitlements corporate read key", "GET", f"{API}/entitlements", who="key_read",
             route="GET /entitlements", setup=r1_set_tier("corporate")),
        Case("r1 entitlements demo", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_set_tier("demo")),
        Case("r1 entitlements quota override", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_set_tier("paid", {"max_trainings_per_day": 2, "max_skus": None, "mcp_access": False})),
        Case("r1 entitlements tz madrid", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_both(r1_set_tier("free"), r1_settings_reset("timezone", "Europe/Madrid")),
             state_check=r1_check_one_training),
        Case("r1 entitlements unknown tier", "GET", f"{API}/entitlements", route="GET /entitlements",
             setup=r1_set_tier("enterprise")),
        # ── X-API-Key header (guards._BearerOrApiKey) ──────────────────────
        Case("r1 x-api-key read key", "GET", cur, who="none", headers={"X-API-Key": "{key_read}"},
             route=R["cur_get"], setup=r1_set_tier("free")),
        Case("r1 x-api-key on internal route", "GET", me_prefs, who="none",
             headers={"X-API-Key": "{key_read}"}, route=R["prefs_get"]),
        Case("r1 x-api-key jwt is no credential", "GET", cur, who="none",
             headers={"X-API-Key": "eyJhbGciOi.x.y"}, route=R["cur_get"]),
        Case("r1 x-api-key loses to authorization", "GET", cur, who="viewer",
             headers={"X-API-Key": "sk_live_nope"}, route=R["cur_get"]),
        Case("r1 x-api-key unknown key", "GET", cur, who="none",
             headers={"X-API-Key": "sk_live_nope"}, route=R["cur_get"]),
        Case("r1 x-api-key write on read key", "PATCH", cur, who="none",
             headers={"X-API-Key": "{key_read}"}, body={"code": "USD"}, route=R["cur_patch"]),
    ]


# ── Runner ───────────────────────────────────────────────────────────────────

def auth_for(fx: Fixture, who: str) -> Optional[str]:
    if who.startswith("raw:"):
        return who[4:]
    if who == "bad_signature":
        return mint_access_token("not-the-secret", fx.admin_id, fx.tenant_id, "admin")
    if who == "expired":
        return mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin", exp_minutes=-1)
    if who == "refresh_type":
        return mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin", token_type="refresh")
    if who == "hs512":
        return mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin", alg="HS512")
    # Person tokens live 15 minutes and a full run over a slow tunnel takes
    # longer: mint a fresh one per case (same claims as the login's).
    people = {"admin": fx.admin_id, "analyst": fx.analyst_id, "viewer": fx.viewer_id}
    if who in people:
        return mint_access_token(fx.secret, people[who], fx.tenant_id, who)
    return fx.token(who)


def seed_commitment(base: str, fx: Fixture) -> str:
    r = http(base, "POST", f"{API}/committed-demand", token=fx.token("admin"),
             body={"sku": "CT-EDIT", "delivery_date": today_plus(30), "quantity": 10,
                   "customer": "Gamma", "probability": 0.5})
    if r.status != 201:
        raise SystemExit(f"seeding a commitment on {base} failed: {r.status} {r.body}")
    return r.body["data"]["id"]


def erase_orphans(py: str, secret: str, db) -> None:
    """Erase throwaway tenants a crashed run left behind (name 'Contract <hex>',
    admin login on stockai.demo), through the Python API like a normal run."""
    cur = db.cursor()
    cur.execute("""SELECT t.id, u.id FROM tenants t JOIN users u ON u.tenant_id = t.id
                    WHERE t.name LIKE 'Contract %%' AND u.role = 'admin'
                      AND u.email LIKE 'contract-%%@stockai.demo'""")
    for tenant_id, admin_id in cur.fetchall():
        token = mint_access_token(secret, admin_id, tenant_id, "admin")
        r = http(py, "DELETE", f"{API}/tenant", token=token, body={"confirm": "DELETE"})
        print(f"orphan {tenant_id}: {r.status}")


def run(args) -> int:
    env = read_env_file(args.env_file) if args.env_file else {}
    secret = os.environ.get("SECRET_KEY") or env.get("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY not found (pass --env-file or set SECRET_KEY)")
    db = None
    if args.db:
        import psycopg2  # noqa: PLC0415 - optional dependency
        db = psycopg2.connect(args.db)
        db.autocommit = True
    if args.erase_orphans:
        if db is None:
            raise SystemExit("--erase-orphans needs --db")
        erase_orphans(args.python, secret, db)
        return 0

    fx = make_fixture(args.python, secret)
    print(f"throwaway tenant {fx.tenant_id} (admin {fx.admin_id})")
    results = []
    try:
        # Each implementation edits a row it created itself, so a PATCH on one
        # side never changes what the other side's PATCH starts from.
        cd = {"py": seed_commitment(args.python, fx), "rs": seed_commitment(args.rust, fx)}
        for case in build_cases(fx):
            if args.only and args.only not in case.name:
                continue
            token = auth_for(fx, case.who)
            if case.who.startswith("key_") and token is None:
                results.append((case, "SKIP", ["no API key in this tenant"]))
                continue
            path_py = case.path.replace("{cd}", cd["py"])
            path_rs = case.path.replace("{cd}", cd["rs"])
            extra = {k: (fx.tokens.get(v[1:-1], "") if v.startswith("{") else v)
                     for k, v in case.headers.items()}
            kw = dict(token=token, body=case.body, raw_body=case.raw_body,
                      content_type=case.content_type, headers=extra)
            if case.setup is not None and db is not None:
                case.setup(fx, db, "py")
            rp = http(args.python, case.method, path_py, **kw)
            if case.setup is not None and db is not None:
                case.setup(fx, db, "rs")
            rr = http(args.rust, case.method, path_rs, **kw)
            problems = []
            if rp.status != rr.status:
                problems.append(f"status python={rp.status} rust={rr.status}")
            np_ = normalize(rp.body, case.volatile)
            nr_ = normalize(rr.body, case.volatile)
            problems += diff(np_, nr_)
            for h in ("www-authenticate", "retry-after"):
                if rp.headers.get(h) != rr.headers.get(h):
                    problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")
            if case.state_check is not None:
                if db is None:
                    problems.append("(state check skipped: no --db)")
                else:
                    problems += case.state_check(fx, rp, rr, db)
            if args.dump:
                print(f"\n--- {case.name}\nPY {rp.status} {json.dumps(rp.body)[:1500]}"
                      f"\nRS {rr.status} {json.dumps(rr.body)[:1500]}")
            hard = [p for p in problems if not p.startswith("(")]
            results.append((case, "FAIL" if hard else "PASS", problems))
    finally:
        if not args.keep:
            erase_fixture(args.python, fx)

    width = max(len(c.name) for c, _, _ in results)
    print()
    for case, verdict, problems in results:
        print(f"{verdict:4}  {case.name:<{width}}  {case.route}")
        for p in problems:
            print(f"        - {p}")
    by_route: dict[str, list[str]] = {}
    for case, verdict, _ in results:
        by_route.setdefault(case.route, []).append(verdict)
    print("\nper route:")
    for route, verdicts in by_route.items():
        print(f"  {route:<36} {verdicts.count('PASS')}/{len(verdicts)} pass")
    failed = sum(1 for _, v, _ in results if v == "FAIL")
    print(f"\n{len(results) - failed}/{len(results)} cases pass")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None, help="run only cases whose name contains this")
    ap.add_argument("--dump", action="store_true", help="print both bodies for every case")
    ap.add_argument("--keep", action="store_true", help="do not erase the throwaway tenant")
    ap.add_argument("--erase-orphans", action="store_true",
                    help="only erase throwaway tenants left by a crashed run, then exit")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
