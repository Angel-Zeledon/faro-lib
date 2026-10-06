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
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_outlook_cases  # noqa: E402  (fulfillment outlook cases, Rust-only routes)

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
    # The admin's login, for the sections that sign in through Python.
    admin_email: str = ""
    admin_password: str = ""

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

    fx = Fixture(tenant_id, admin_id, admin_token, ids["analyst"], ids["viewer"], secret,
                 admin_email=email, admin_password=password)
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
    # A fresh token: a run through a slow tunnel outlives the 15-minute login
    # token, and an expired one would leave the throwaway tenant behind.
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


# ── R2: sessions (reads, archive/restore), schedule, spike edits ────────────
#
# Fixture sessions are created through the Python API; the facts no route of
# this group can set (a dataset id, a back-test flag, a running job, a past
# scheduler run, a spike application) are written straight into the
# throwaway tenant's rows. Every write case acts on a PER-SIDE object
# (`{x}` resolves to the Python copy for the Python call and to the Rust copy
# for the Rust call), so the two sides never edit the same row.

R2_PAST = (date.today() - timedelta(days=40)).isoformat()


def _r2_session(py: str, fx: Fixture, name: str) -> str:
    r = http(py, "POST", f"{API}/sessions", token=fx.admin_token,
             body={"name": name, "description": "contract", "tags": ["ct", "r2"]})
    if r.status != 201:
        raise SystemExit(f"creating session {name!r} failed: {r.status} {r.body}")
    return r.body["data"]["id"]


def r2_prepare(py: str, rs: str, fx: Fixture, db) -> dict:
    """Placeholders `{name}` -> (python value, rust value)."""
    ph: dict[str, tuple[str, str]] = {}
    shared = {
        "main": _r2_session(py, fx, "CT R2 main"),
        "nodata": _r2_session(py, fx, "CT R2 no data"),
        "backtest": _r2_session(py, fx, "CT R2 backtest"),
        "running": _r2_session(py, fx, "CT R2 running"),
        "archived": _r2_session(py, fx, "CT R2 archived"),
    }
    for k, v in shared.items():
        ph[f"{{s_{k}}}"] = (v, v)
    for k in ("arch", "archkey", "restore", "sched", "spike", "patch"):
        ph[f"{{s_{k}}}"] = (_r2_session(py, fx, f"CT R2 {k}"), _r2_session(py, fx, f"CT R2 {k}"))
    for sid in (shared["archived"], *ph["{s_restore}"]):
        r = http(py, "DELETE", f"{API}/sessions/{sid}", token=fx.admin_token)
        if r.status != 204:
            raise SystemExit(f"archiving {sid} failed: {r.status} {r.body}")
    cur = db.cursor()
    cur.execute("UPDATE sessions SET dataset_id = 'ds_ct_r2_main' WHERE id = %s", (shared["main"],))
    cur.execute("UPDATE sessions SET is_backtest = TRUE, backtest_holdout_periods = 4, "
                "backtest_source_dataset_id = 'ds_ct_r2_main', dataset_id = 'ds_ct_r2_main' WHERE id = %s",
                (shared["backtest"],))
    # A trained-looking result and configs, so the library's computed columns
    # (accuracy, models, sku_count, horizon, granularity) carry real values.
    metrics = {"metrics": {"rows": [
        {"sku": "A", "model": "lightgbm", "wape": 0.1234567890123},
        {"sku": "A", "model": "ets", "wape": 0.2},
        {"sku": "A", "model": "naive", "type": "baseline", "wape": 0.01},
        {"sku": "B", "model": "arima", "wape": 0.3333333333333333},
        {"sku": "B", "model": "lightgbm", "wape": "n/a"},
    ]}}
    cur.execute("INSERT INTO session_results (session_id, tenant_id, training_result) VALUES (%s, %s, %s)",
                (shared["main"], fx.tenant_id, json.dumps(metrics)))
    cur.execute("UPDATE session_configs SET forecast_cfg = '{\"horizon\": 12}' WHERE session_id = %s",
                (shared["main"],))
    cur.execute("UPDATE session_configs SET validation_cfg = '{\"horizon\": \"8\"}', "
                "granularity_cfg = '{\"target_freq\": \"W\"}' WHERE session_id = %s", (shared["backtest"],))
    sp_py, sp_rs = ph["{s_spike}"]
    cur.execute("UPDATE sessions SET dataset_id = 'ds_ct_r2_py' WHERE id = %s", (sp_py,))
    cur.execute("UPDATE sessions SET dataset_id = 'ds_ct_r2_rs' WHERE id = %s", (sp_rs,))
    # A job "in flight": RUNNING with a worker id no live worker owns, so no
    # worker claims it (only QUEUED rows are claimed) and nothing trains.
    cur.execute("""INSERT INTO jobs (id, tenant_id, session_id, created_by, status, started_at, worker_id)
                   VALUES (%s, %s, %s, %s, 'RUNNING', NOW(), 'contract-harness')""",
                (f"job_ct_{secrets.token_hex(5)}", fx.tenant_id, shared["running"], fx.admin_id))
    # Scheduler history: two finished runs and one run that did not launch.
    for i, status in enumerate(("COMPLETED", "FAILED")):
        cur.execute("""INSERT INTO jobs (id, tenant_id, session_id, created_by, status, created_at,
                                         started_at, completed_at, error)
                       VALUES (%s, %s, %s, 'scheduler', %s, NOW() - %s * INTERVAL '1 day',
                               NOW() - %s * INTERVAL '1 day', NOW() - %s * INTERVAL '1 day', %s)""",
                    (f"job_ct_{secrets.token_hex(5)}", fx.tenant_id, shared["main"], status, i + 1, i + 1, i + 1,
                     None if status == "COMPLETED" else "boom"))
    cur.execute("""INSERT INTO schedule_runs (id, tenant_id, schedule_id, ran_at, outcome, reason,
                                              reason_params, session_id)
                   VALUES (%s, %s, 'sched_ct_none', NOW() - INTERVAL '36 hours', 'skipped', 'nothing_new',
                           '{"rows": 3}', %s)""",
                (f"run_ct_{secrets.token_hex(5)}", fx.tenant_id, shared["main"]))
    # One spike mark per side, made through each side's own API.
    marks = []
    for base, sid in ((py, sp_py), (rs, sp_rs)):
        r = http(base, "POST", f"{API}/sessions/{sid}/spike-edits", token=fx.admin_token,
                 body={"sku": "CT-SPK", "start_date": R2_PAST, "end_date": R2_PAST,
                       "reason_code": "promotion", "reason_note": "seed"})
        if r.status != 201:
            raise SystemExit(f"seeding a spike mark on {base} failed: {r.status} {r.body}")
        marks.append(r.body["data"]["id"])
        cur.execute("""INSERT INTO spike_edit_applications (tenant_id, session_id, spike_edit_id, sku, status,
                                                            points_treated, original_total, replacement_total)
                       VALUES (%s, %s, %s, 'CT-SPK', 'applied', 1, 120.5, 30.25)""",
                    (fx.tenant_id, sid, r.body["data"]["id"]))
    ph["{spk}"] = (marks[0], marks[1])
    cur.execute("SELECT id FROM sessions WHERE tenant_id <> %s LIMIT 1", (fx.tenant_id,))
    other = cur.fetchone()
    ph["{s_other_tenant}"] = (other[0], other[0]) if other else ("sess_none_elsewhere",) * 2
    w1b_seed_manifests(db, fx, shared["main"], shared["backtest"])
    return ph


def _session_row(db, sid):
    cur = db.cursor()
    cur.execute("SELECT archived_at IS NOT NULL, archived_by, status, name FROM sessions WHERE id = %s", (sid,))
    return cur.fetchone()


def _activity(db, tenant_id, resource, action_prefix):
    cur = db.cursor()
    cur.execute("""SELECT action, user_id, context, status FROM activity_logs
                    WHERE tenant_id = %s AND resource = %s AND action LIKE %s ORDER BY created_at""",
                (tenant_id, resource, action_prefix + "%"))
    return [dict(zip(("action", "user_id", "context", "status"), r)) for r in cur.fetchall()]


def r2_check_session_state(key: str, action: str, ph: dict):
    """The session row (archived flag, archived_by) and its `session.*` rows."""
    def check(fx, rp, rr, db):
        sp, sr = ph[key]
        a, b = _session_row(db, sp), _session_row(db, sr)
        problems = [] if a[:3] == b[:3] else [f"session row: python={a} rust={b}"]
        ea, eb = _activity(db, fx.tenant_id, sp, action), _activity(db, fx.tenant_id, sr, action)
        if ea != eb:
            problems.append(f"{action} rows differ: python={ea} rust={eb}")
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


# timezone PATCH: schedules are re-anchored ---------------------------------

TZ_SCHEDULES = (("sched_ct_tz_weekly", "0 6 * * 1", True), ("sched_ct_tz_daily", "30 23 * * *", True),
                ("sched_ct_tz_off", "0 6 * * 1", False))


def _tz_schedule_rows(db, tenant_id):
    return _r1_exec(db, "SELECT id, cron_expr, next_run, enabled FROM scheduled_jobs "
                        "WHERE tenant_id = %s AND id LIKE 'sched_ct_tz_%%' ORDER BY id", (tenant_id,)).fetchall()


def r1_tz_setup(zone):
    """Settings back to `zone` and three schedules (two armed, one off) whose
    next_run is a sentinel in the year 2000, so only a re-anchor can move it."""
    base = r1_settings_reset("timezone", zone)

    def setup(fx, db, side):
        base(fx, db, side)
        if side == "rs":
            _r1_scratch(fx)["tz_rows_py"] = _tz_schedule_rows(db, fx.tenant_id)
        _r1_exec(db, "DELETE FROM scheduled_jobs WHERE tenant_id = %s AND id LIKE 'sched_ct_tz_%%'", (fx.tenant_id,))
        for sid, cron_expr, enabled in TZ_SCHEDULES:
            _r1_exec(db, """INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run, enabled,
                                                        retrain_mode)
                            VALUES (%s, %s, %s, %s, '2000-01-01T00:00:00+00', %s, 'refit')""",
                     (sid, fx.tenant_id, "sess_" + sid, cron_expr, enabled))
    return setup


def r1_check_tz_write(expected_zone, moved):
    def check(fx, rp, rr, db):
        problems = []
        a, b = _r1_scratch(fx).get("settings_py"), _settings(db, fx.tenant_id)
        if a != b:
            problems.append(f"tenants.settings differ: python={a} rust={b}")
        if rp.status != 200 or rr.status != 200:
            return problems + [f"expected 200 on both sides: python={rp.status} rust={rr.status}"]
        if (b or {}).get("timezone") != expected_zone:
            problems.append(f"settings.timezone is {(b or {}).get('timezone')!r}")
        for side in (rp, rr):
            if side.body["data"].get("schedules_rescheduled") != moved:
                problems.append(f"schedules_rescheduled is {side.body['data'].get('schedules_rescheduled')}")
        pr, rr_ = _r1_scratch(fx).get("tz_rows_py"), _tz_schedule_rows(db, fx.tenant_id)
        if pr != rr_:
            problems.append(f"scheduled_jobs differ: python={pr} rust={rr_}")
        for row in rr_:
            armed = row[3]
            sentinel = row[2].year < 2001
            if armed and sentinel:
                problems.append(f"armed schedule {row[0]} was not re-anchored")
            if not armed and not sentinel:
                problems.append(f"disabled schedule {row[0]} was moved")
        rows = _audit_rows(db, fx.tenant_id)
        if len(rows) != 2 or rows[0] != rows[1]:
            problems.append(f"audit rows differ or are missing: {rows}")
        return problems
    return check


def r2_check_unchanged(key: str, archived: bool, ph: dict):
    def check(fx, rp, rr, db):
        out = []
        for side, sid in zip(("python", "rust"), ph[key]):
            row = _session_row(db, sid)
            if row[0] is not archived:
                out.append(f"{side}: archived={row[0]}, expected {archived}")
        return out
    return check


def r2_check_schedule(ph: dict, audit_action: str):
    def check(fx, rp, rr, db):
        cur = db.cursor()
        sp, sr = ph["{s_sched}"]
        rows = []
        for sid in (sp, sr):
            cur.execute("SELECT cron_expr, next_run, enabled, retrain_mode FROM scheduled_jobs "
                        "WHERE session_id = %s AND tenant_id = %s", (sid, fx.tenant_id))
            rows.append(cur.fetchone())
        problems = [] if rows[0] == rows[1] else [f"scheduled_jobs row: python={rows[0]} rust={rows[1]}"]
        ea = _activity(db, fx.tenant_id, sp, audit_action)
        eb = _activity(db, fx.tenant_id, sr, audit_action)
        strip = lambda rows_: [{**r, "context": {k: v for k, v in r["context"].items() if k != "target_id"}}  # noqa: E731
                               for r in rows_]
        if strip(ea) != strip(eb):
            problems.append(f"{audit_action} rows differ: python={ea[-1:]} rust={eb[-1:]}")
        if not ea:
            problems.append(f"no {audit_action} row was recorded")
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
    sc["training_job_ids"] = [f"job_ct{i}{tag}" for i in range(len(jobs))]


def r1_check_one_training(fx, rp, rr, db):
    """One of the R1 seeds counts, plus whatever other sections of this run
    launched in the tenant just now (R2 seeds a job of its own), counted with
    `daily_cap.count_trainings_today`'s conditions."""
    other = _r1_exec(db, """SELECT COUNT(*) FROM jobs j
                              JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
                             WHERE j.tenant_id = %s AND NOT (j.id = ANY(%s::text[]))
                               AND j.created_at >= NOW() - INTERVAL '2 hours'
                               AND NOT s.is_backtest AND NOT s.is_reforecast
                               AND (s.family_id IS NULL OR s.family_id = s.id)
                               AND NOT (j.started_at IS NULL AND j.status IN ('FAILED', 'CANCELLED'))""",
                     (fx.tenant_id, _r1_scratch(fx).get("training_job_ids", []))).fetchone()[0]
    want = 1 + other
    got = [r.body["data"]["usage"].get("trainings_today") if r.status == 200 else None for r in (rp, rr)]
    return [] if got == [want, want] else [f"trainings_today python={got[0]} rust={got[1]} (expected {want})"]


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
        # ── timezone ───────────────────────────────────────────────────────
        Case("r1 timezone patch admin madrid", "PATCH", tz, body={"timezone": " Europe/Madrid "},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("America/Costa_Rica"),
             state_check=r1_check_tz_write("Europe/Madrid", 2)),
        Case("r1 timezone patch to santiago", "PATCH", tz, body={"timezone": "America/Santiago"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Europe/Madrid"),
             state_check=r1_check_tz_write("America/Santiago", 2)),
        Case("r1 timezone patch to utc from nothing", "PATCH", tz, body={"timezone": "UTC"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Mars/Base"),
             state_check=r1_check_tz_write("UTC", 2)),
        Case("r1 timezone patch analyst denied", "PATCH", tz, who="analyst", body={"timezone": "UTC"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Europe/Madrid"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 timezone patch viewer denied", "PATCH", tz, who="viewer", body={"timezone": "UTC"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Europe/Madrid"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 timezone patch write key", "PATCH", tz, who="key_write", body={"timezone": "UTC"},
             route="PATCH /tenant/timezone"),
        Case("r1 timezone patch read key", "PATCH", tz, who="key_read", body={"timezone": "UTC"},
             route="PATCH /tenant/timezone"),
        Case("r1 timezone patch no auth", "PATCH", tz, who="none", body={"timezone": "UTC"},
             route="PATCH /tenant/timezone"),
        Case("r1 timezone patch unsupported", "PATCH", tz, body={"timezone": "Europe/Paris"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Europe/Madrid"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 timezone patch lowercase utc", "PATCH", tz, body={"timezone": "utc"},
             route="PATCH /tenant/timezone", setup=r1_tz_setup("Europe/Madrid"),
             state_check=r1_check_no_audit_and_settings),
        Case("r1 timezone patch not a string", "PATCH", tz, body={"timezone": 5}, route="PATCH /tenant/timezone"),
        Case("r1 timezone patch missing", "PATCH", tz, body={"tz": "UTC"}, route="PATCH /tenant/timezone"),
        Case("r1 timezone patch no body", "PATCH", tz, route="PATCH /tenant/timezone"),
        Case("r1 timezone patch invalid json", "PATCH", tz, raw_body=b"[1,", route="PATCH /tenant/timezone",
             volatile={"ctx", "loc"}),
        Case("r1 timezone patch wrong tenant", "PATCH", tz, who=other, body={"timezone": "UTC"},
             route="PATCH /tenant/timezone"),
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


def r2_check_spike(fx, rp, rr, db):
    if rp.status not in (200, 201) or rr.status not in (200, 201):
        return []
    a_id, b_id = rp.body["data"]["id"], rr.body["data"]["id"]
    cur = db.cursor()
    cols = ("sku", "start_date", "end_date", "reason_code", "reason_note", "created_by", "reverted_by",
            "reverted_at IS NOT NULL")
    rows = []
    for i in (a_id, b_id):
        cur.execute(f"SELECT {', '.join(cols)} FROM spike_edits WHERE id = %s", (i,))
        rows.append(cur.fetchone())
    problems = [] if rows[0] == rows[1] else [f"spike_edits row: python={rows[0]} rust={rows[1]}"]
    ea, eb = _activity(db, fx.tenant_id, a_id, "forecast."), _activity(db, fx.tenant_id, b_id, "forecast.")
    if ea != eb:
        problems.append(f"activity rows differ: python={ea} rust={eb}")
    if not ea:
        problems.append("no forecast.spike_* row was recorded")
    return problems


def r2_check_no_spike_written(fx, rp, rr, db):
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM spike_edits WHERE tenant_id = %s AND created_by = %s",
                (fx.tenant_id, fx.viewer_id))
    n = cur.fetchone()[0]
    return [] if n == 0 else [f"viewer wrote {n} spike marks"]


def build_r2_cases(fx: Fixture, ph: dict) -> list[Case]:
    S, SS = f"{API}/sessions", f"{API}/sessions/summary"
    sess = "GET /sessions"
    summ = "GET /sessions/summary"
    one = "GET /sessions/{id}"
    arch = "DELETE /sessions/{id}"
    rest = "POST /sessions/{id}/restore"
    sch = "/sessions/{id}/schedule"
    spk = "spike-edits"
    good_spike = {"sku": " CT-NEW ", "start_date": (date.today() - timedelta(days=10)).isoformat(),
                  "end_date": (date.today() - timedelta(days=3)).isoformat(), "reason_code": "one_off_order",
                  "reason_note": "  big order  "}
    sv = {"session_id"}   # per-side sessions: their ids differ by construction
    return [
        # ── sessions: list ──────────────────────────────────────────────────
        Case("r2 sessions list", "GET", S, route=sess),
        Case("r2 sessions list viewer", "GET", S, who="viewer", route=sess),
        Case("r2 sessions list read key", "GET", S, who="key_read", route=sess),
        Case("r2 sessions list all", "GET", f"{S}?archived=all&limit=3&skip=1", route=sess),
        Case("r2 sessions list archived", "GET", f"{S}?archived=archived", route=sess),
        Case("r2 sessions list bad scope", "GET", f"{S}?archived=deleted", route=sess),
        Case("r2 sessions list bad ints", "GET", f"{S}?skip=-1&limit=501", route=sess),
        Case("r2 sessions list int parsing", "GET", f"{S}?limit=abc&skip=1.0", route=sess),
        Case("r2 sessions list last wins", "GET", f"{S}?limit=0&limit=2", route=sess),
        Case("r2 sessions list no auth", "GET", S, who="none", route=sess),
        Case("r2 sessions list expired", "GET", S, who="expired", route=sess),
        # ── sessions: summary ───────────────────────────────────────────────
        Case("r2 summary", "GET", SS, route=summ),
        Case("r2 summary search", "GET", f"{SS}?q=%20r2%20MAIN&archived=all", route=summ),
        Case("r2 summary wildcard literal", "GET", f"{SS}?q=50%25_x", route=summ),
        Case("r2 summary statuses", "GET", f"{SS}?status=DRAFT&status=QUEUED&archived=all", route=summ),
        Case("r2 summary dataset", "GET", f"{SS}?dataset_id=ds_ct_r2_main&archived=all", route=summ),
        Case("r2 summary sort name asc", "GET", f"{SS}?sort=name&order=asc&archived=all&limit=4", route=summ),
        Case("r2 summary sort accuracy", "GET", f"{SS}?sort=accuracy", route=summ),
        Case("r2 summary sort horizon", "GET", f"{SS}?sort=horizon&order=asc", route=summ),
        Case("r2 summary dates", "GET", f"{SS}?created_from=2020-01-01&created_to=2099-12-31", route=summ),
        Case("r2 summary bad patterns", "GET",
             f"{SS}?created_from=2026-1-1&sort=size&order=up&archived=x&q={'q' * 201}&dataset_id={'d' * 101}",
             route=summ),
        Case("r2 summary impossible date", "GET", f"{SS}?created_from=2026-13-45", route=summ),
        Case("r2 summary page past end", "GET", f"{SS}?skip=400&limit=500", route=summ),
        Case("r2 summary read key", "GET", SS, who="key_read", route=summ),
        # ── sessions: one ───────────────────────────────────────────────────
        Case("r2 session get", "GET", f"{S}/{{s_main}}", route=one),
        Case("r2 session get viewer", "GET", f"{S}/{{s_main}}", who="viewer", route=one),
        Case("r2 session get read key", "GET", f"{S}/{{s_main}}", who="key_read", route=one),
        Case("r2 session get archived", "GET", f"{S}/{{s_archived}}", route=one),
        Case("r2 session get backtest", "GET", f"{S}/{{s_backtest}}", route=one),
        Case("r2 session get not found", "GET", f"{S}/sess_nope", route=one),
        Case("r2 session get wrong tenant", "GET", f"{S}/{{s_other_tenant}}", route=one),
        # ── sessions: archive ───────────────────────────────────────────────
        Case("r2 archive viewer denied", "DELETE", f"{S}/{{s_arch}}", who="viewer", route=arch,
             state_check=r2_check_unchanged("{s_arch}", False, ph)),
        Case("r2 archive read key denied", "DELETE", f"{S}/{{s_arch}}", who="key_read", route=arch,
             state_check=r2_check_unchanged("{s_arch}", False, ph)),
        Case("r2 archive analyst", "DELETE", f"{S}/{{s_arch}}", who="analyst", route=arch,
             state_check=r2_check_session_state("{s_arch}", "session.archive", ph)),
        Case("r2 archive again (no second row)", "DELETE", f"{S}/{{s_arch}}", who="analyst", route=arch,
             state_check=r2_check_session_state("{s_arch}", "session.archive", ph)),
        Case("r2 archive write key", "DELETE", f"{S}/{{s_archkey}}", who="key_write", route=arch,
             state_check=r2_check_session_state("{s_archkey}", "session.archive", ph)),
        Case("r2 archive running refused", "DELETE", f"{S}/{{s_running}}", route=arch,
             state_check=lambda fx, rp, rr, db: [] if _session_row(db, ph["{s_running}"][0])[0] is False
             else ["the running session was archived"]),
        Case("r2 archive not found", "DELETE", f"{S}/sess_nope", route=arch),
        Case("r2 archive wrong tenant", "DELETE", f"{S}/{{s_other_tenant}}", route=arch),
        Case("r2 archive literal summary", "DELETE", f"{S}/summary", route=arch),
        Case("r2 archive no auth", "DELETE", f"{S}/{{s_arch}}", who="none", route=arch),
        Case("r2 archived session still readable", "GET", f"{S}/{{s_arch}}", route=one,
             volatile=sv | {"archived_at"}),
        # ── sessions: restore ───────────────────────────────────────────────
        Case("r2 restore viewer denied", "POST", f"{S}/{{s_restore}}/restore", who="viewer", route=rest,
             state_check=r2_check_unchanged("{s_restore}", True, ph)),
        Case("r2 restore read key denied", "POST", f"{S}/{{s_restore}}/restore", who="key_read", route=rest),
        Case("r2 restore analyst", "POST", f"{S}/{{s_restore}}/restore", who="analyst", route=rest,
             volatile=sv, state_check=r2_check_session_state("{s_restore}", "session.restore", ph)),
        Case("r2 restore active (no-op)", "POST", f"{S}/{{s_restore}}/restore", route=rest, volatile=sv,
             state_check=r2_check_session_state("{s_restore}", "session.restore", ph)),
        Case("r2 restore write key", "POST", f"{S}/{{s_archkey}}/restore", who="key_write", route=rest,
             volatile=sv, state_check=r2_check_session_state("{s_archkey}", "session.restore", ph)),
        Case("r2 restore not found", "POST", f"{S}/sess_nope/restore", route=rest),
        Case("r2 restore wrong tenant", "POST", f"{S}/{{s_other_tenant}}/restore", route=rest),
        Case("r2 restore with a body", "POST", f"{S}/{{s_main}}/restore", body={"x": 1}, route=rest),
        # ── schedule ────────────────────────────────────────────────────────
        Case("r2 schedule get none", "GET", f"{S}/{{s_sched}}/schedule", route="GET " + sch),
        Case("r2 schedule get not found", "GET", f"{S}/sess_nope/schedule", route="GET " + sch),
        Case("r2 schedule save viewer denied", "POST", f"{S}/{{s_sched}}/schedule", who="viewer",
             body={"cron_expr": "0 6 * * 1"}, route="POST " + sch),
        Case("r2 schedule save read key denied", "POST", f"{S}/{{s_sched}}/schedule", who="key_read",
             body={"cron_expr": "0 6 * * 1"}, route="POST " + sch),
        Case("r2 schedule save create", "POST", f"{S}/{{s_sched}}/schedule", who="analyst",
             body={"cron_expr": "  0 6 * * 1 ", "enabled": True}, route="POST " + sch, volatile=sv,
             state_check=r2_check_schedule(ph, "audit.schedule.saved")),
        Case("r2 schedule save update keeps mode", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "30 2 * * 1-5", "enabled": "no"}, route="POST " + sch, volatile=sv,
             state_check=r2_check_schedule(ph, "audit.schedule.saved")),
        Case("r2 schedule save reforecast", "POST", f"{S}/{{s_sched}}/schedule", who="key_write",
             body={"cron_expr": "0 0 L * *", "retrain_mode": "reforecast"}, route="POST " + sch, volatile=sv,
             state_check=r2_check_schedule(ph, "audit.schedule.saved")),
        Case("r2 schedule save nth weekday", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "15 8 * JAN-MAR mon#2", "retrain_mode": None}, route="POST " + sch, volatile=sv,
             state_check=r2_check_schedule(ph, "audit.schedule.saved")),
        Case("r2 schedule save impossible date", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "0 6 31 2 *"}, route="POST " + sch, volatile=sv | {"next_run"}),
        Case("r2 schedule get", "GET", f"{S}/{{s_sched}}/schedule", route="GET " + sch,
             volatile=sv | {"next_run"}),
        Case("r2 schedule save four fields", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "0 6 * *"}, route="POST " + sch),
        Case("r2 schedule save croniter refuses", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "0 99 * * 1", "enabled": "maybe", "retrain_mode": "weekly"}, route="POST " + sch),
        Case("r2 schedule save more refusals", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "*/0 * * * *", "retrain_mode": 5}, route="POST " + sch),
        Case("r2 schedule save hashed", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "H 6 * * 1"}, route="POST " + sch),
        Case("r2 schedule save mixed nth", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": "0 0 * * 1,2#3"}, route="POST " + sch),
        Case("r2 schedule save wrong types", "POST", f"{S}/{{s_sched}}/schedule",
             body={"cron_expr": 6}, route="POST " + sch),
        Case("r2 schedule save no body", "POST", f"{S}/{{s_sched}}/schedule", route="POST " + sch),
        Case("r2 schedule save invalid json", "POST", f"{S}/{{s_sched}}/schedule", raw_body=b"{nope",
             route="POST " + sch, volatile={"ctx", "loc"}),
        Case("r2 schedule save not found", "POST", f"{S}/sess_nope/schedule",
             body={"cron_expr": "0 6 * * 1"}, route="POST " + sch),
        Case("r2 schedules list", "GET", f"{API}/schedules", route="GET /schedules",
             volatile={"session_id", "session_name", "next_run"}),
        Case("r2 schedules list read key", "GET", f"{API}/schedules", who="key_read", route="GET /schedules",
             volatile={"session_id", "session_name", "next_run"}),
        Case("r2 schedule history", "GET", f"{API}/schedules/history", route="GET /schedules/history"),
        Case("r2 schedule history limit 1", "GET", f"{API}/schedules/history?limit=0", route="GET /schedules/history"),
        Case("r2 schedule history huge", "GET", f"{API}/schedules/history?limit=99999999999999999999",
             route="GET /schedules/history"),
        Case("r2 schedule history bad", "GET", f"{API}/schedules/history?limit=x", route="GET /schedules/history"),
        Case("r2 schedule delete viewer denied", "DELETE", f"{S}/{{s_sched}}/schedule", who="viewer",
             route="DELETE " + sch),
        Case("r2 schedule delete", "DELETE", f"{S}/{{s_sched}}/schedule", who="analyst", route="DELETE " + sch,
             volatile=sv | {"deleted"}, state_check=r2_check_schedule(ph, "audit.schedule.deleted")),
        Case("r2 schedule delete again", "DELETE", f"{S}/{{s_sched}}/schedule", route="DELETE " + sch,
             volatile=sv | {"deleted"}, state_check=r2_check_schedule(ph, "audit.schedule.deleted")),
        Case("r2 schedule delete not found", "DELETE", f"{S}/sess_nope/schedule", route="DELETE " + sch),
        # ── spike edits ─────────────────────────────────────────────────────
        Case("r2 spike list", "GET", f"{S}/{{s_spike}}/spike-edits", route="GET " + spk,
             volatile={"dataset_id", "applied_at"}),
        Case("r2 spike list viewer", "GET", f"{S}/{{s_spike}}/spike-edits?include_reverted=yes&sku=CT-SPK",
             who="viewer", route="GET " + spk, volatile={"dataset_id", "applied_at"}),
        Case("r2 spike list other sku", "GET", f"{S}/{{s_spike}}/spike-edits?sku=NOPE", route="GET " + spk),
        Case("r2 spike list bad query", "GET",
             f"{S}/{{s_spike}}/spike-edits?include_reverted=maybe&sku={'s' * 201}", route="GET " + spk),
        Case("r2 spike list no dataset", "GET", f"{S}/{{s_nodata}}/spike-edits", route="GET " + spk),
        Case("r2 spike list not found", "GET", f"{S}/sess_nope/spike-edits", route="GET " + spk),
        Case("r2 spike list read key refused", "GET", f"{S}/{{s_spike}}/spike-edits", who="key_read",
             route="GET " + spk),
        Case("r2 spike create analyst", "POST", f"{S}/{{s_spike}}/spike-edits", who="analyst", body=good_spike,
             route="POST " + spk, volatile={"dataset_id"}, state_check=r2_check_spike),
        Case("r2 spike create overlap", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "sku": "CT-SPK", "start_date": R2_PAST}, route="POST " + spk,
             volatile={"spike_edit_id"}),
        Case("r2 spike create viewer denied", "POST", f"{S}/{{s_spike}}/spike-edits", who="viewer",
             body=good_spike, route="POST " + spk, state_check=r2_check_no_spike_written),
        Case("r2 spike create write key refused", "POST", f"{S}/{{s_spike}}/spike-edits", who="key_write",
             body=good_spike, route="POST " + spk),
        Case("r2 spike create validation", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={"sku": "", "start_date": 5, "reason_note": "n" * 301}, route="POST " + spk),
        Case("r2 spike create blank sku", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "sku": "   ", "reason_code": "nope"}, route="POST " + spk),
        Case("r2 spike create bad reason", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "reason_code": "nope"}, route="POST " + spk),
        Case("r2 spike create other without note", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "reason_code": "other", "reason_note": "   "}, route="POST " + spk),
        Case("r2 spike create bad date", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "end_date": "2026/01/01"}, route="POST " + spk),
        Case("r2 spike create reversed", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "start_date": "2026-03-01", "end_date": "2026-02-01"}, route="POST " + spk),
        Case("r2 spike create too long", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "start_date": "2024-01-01", "end_date": "2025-06-01"}, route="POST " + spk),
        Case("r2 spike create future", "POST", f"{S}/{{s_spike}}/spike-edits",
             body={**good_spike, "end_date": (date.today() + timedelta(days=2)).isoformat()}, route="POST " + spk),
        Case("r2 spike create no dataset", "POST", f"{S}/{{s_nodata}}/spike-edits", body=good_spike,
             route="POST " + spk),
        Case("r2 spike create not found", "POST", f"{S}/sess_nope/spike-edits", body=good_spike,
             route="POST " + spk),
        Case("r2 spike create no body", "POST", f"{S}/{{s_spike}}/spike-edits", route="POST " + spk),
        Case("r2 spike revert viewer denied", "POST", f"{API}/spike-edits/{{spk}}/revert", who="viewer",
             route="POST /spike-edits/{id}/revert"),
        Case("r2 spike revert analyst", "POST", f"{API}/spike-edits/{{spk}}/revert", who="analyst",
             route="POST /spike-edits/{id}/revert", volatile={"dataset_id", "reverted_at"},
             state_check=r2_check_spike),
        Case("r2 spike revert again", "POST", f"{API}/spike-edits/{{spk}}/revert",
             route="POST /spike-edits/{id}/revert", volatile={"spike_edit_id"}),
        Case("r2 spike revert not found", "POST", f"{API}/spike-edits/nope/revert",
             route="POST /spike-edits/{id}/revert"),
        Case("r2 spike revert write key refused", "POST", f"{API}/spike-edits/{{spk}}/revert", who="key_write",
             route="POST /spike-edits/{id}/revert"),
        Case("r2 spike list after revert", "GET", f"{S}/{{s_spike}}/spike-edits?include_reverted=1",
             route="GET " + spk, volatile={"dataset_id", "reverted_at", "applied_at"}),
    ]

# ── Wave 1b: POST /sessions, PATCH /sessions/{id} ────────────────────────────

SESSION_COLS = "name, description, status, pipeline_step, created_by, tags, version, archived_at, dataset_id"


def _strip_target(rows_):
    return [{**x, "context": {k: v for k, v in x["context"].items() if k != "target_id"}} for x in rows_]


def w1b_check_created(name: str, expect: int):
    """`expect` sessions called `name` exist (one per side that created one),
    with the same columns, each with its session_configs row and the same
    `audit.session.created` row."""
    def check(fx, rp, rr, db):
        cur = db.cursor()
        cur.execute(f"SELECT id, {SESSION_COLS} FROM sessions WHERE tenant_id = %s AND name = %s ORDER BY created_at",
                    (fx.tenant_id, name))
        rows = cur.fetchall()
        problems = []
        if len(rows) != expect:
            return [f"{len(rows)} session(s) named {name!r}, expected {expect}"]
        if expect == 2:
            if rows[0][1:] != rows[1][1:]:
                problems.append(f"session rows differ: python={rows[0][1:]} rust={rows[1][1:]}")
            audits = []
            for r in rows:
                cur.execute("SELECT 1 FROM session_configs WHERE session_id = %s AND tenant_id = %s",
                            (r[0], fx.tenant_id))
                if cur.fetchone() is None:
                    problems.append(f"no session_configs row for {r[0]}")
                audits.append(_activity(db, fx.tenant_id, r[0], "audit.session.created"))
            if len(audits[0]) != 1 or _strip_target(audits[0]) != _strip_target(audits[1]):
                problems.append(f"audit.session.created rows differ or are missing: {audits}")
            for r, a in zip(rows, audits):
                if a and a[0]["context"].get("target_id") != r[0]:
                    problems.append("audit target_id is not the new session id")
        return problems
    return check


def w1b_check_patched(key: str, ph: dict, expect_audit: int):
    def check(fx, rp, rr, db):
        sp, sr = ph[key]
        cur = db.cursor()
        out = []
        for sid in (sp, sr):
            cur.execute(f"SELECT {SESSION_COLS} FROM sessions WHERE id = %s", (sid,))
            out.append(cur.fetchone())
        problems = [] if out[0] == out[1] else [f"session rows differ: python={out[0]} rust={out[1]}"]
        ea = _activity(db, fx.tenant_id, sp, "audit.session.updated")
        eb = _activity(db, fx.tenant_id, sr, "audit.session.updated")
        if _strip_target(ea) != _strip_target(eb) or len(ea) != expect_audit:
            problems.append(f"audit.session.updated rows: python={ea} rust={eb} (expected {expect_audit})")
        return problems
    return check


def build_w1b_session_cases(fx: Fixture, ph: dict) -> list[Case]:
    S = f"{API}/sessions"
    cr, up = "POST /sessions", "PATCH /sessions/{id}"
    sv = {"session_id"}
    # Each success case runs on both sides, each creating a session with the same unique name.
    created = {"id", "session_id", "created_at", "updated_at"}
    upd = sv | {"updated_at"}
    P = f"{S}/{{s_patch}}"
    return [
        Case("w1b create analyst", "POST", S, who="analyst", route=cr, volatile=created,
             body={"name": "CT W1B full", "description": "d", "tags": ["a", "b"]},
             state_check=w1b_check_created("CT W1B full", 2)),
        Case("w1b create minimal admin", "POST", S, route=cr, volatile=created, body={"name": " CT W1B min "},
             state_check=w1b_check_created(" CT W1B min ", 2)),
        Case("w1b create null description", "POST", S, who="key_write", route=cr, volatile=created,
             body={"name": "CT W1B key", "description": None, "tags": []},
             state_check=w1b_check_created("CT W1B key", 2)),
        Case("w1b create unicode", "POST", S, route=cr, volatile=created,
             body={"name": "Pronóstico ☃ CT W1B", "tags": ["ñ"]},
             state_check=w1b_check_created("Pronóstico ☃ CT W1B", 2)),
        Case("w1b create max lengths", "POST", S, route=cr, volatile=created,
             body={"name": "n" * 200, "description": "d" * 2000, "tags": ["t"] * 50},
             state_check=w1b_check_created("n" * 200, 2)),
        Case("w1b create viewer denied", "POST", S, who="viewer", route=cr, body={"name": "CT W1B viewer"},
             state_check=w1b_check_created("CT W1B viewer", 0)),
        Case("w1b create read key denied", "POST", S, who="key_read", route=cr, body={"name": "CT W1B rk"},
             state_check=w1b_check_created("CT W1B rk", 0)),
        Case("w1b create no auth", "POST", S, who="none", route=cr, body={"name": "CT W1B none"}),
        Case("w1b create expired token", "POST", S, who="expired", route=cr, body={"name": "CT W1B exp"}),
        Case("w1b create empty name", "POST", S, route=cr, body={"name": ""},
             state_check=w1b_check_created("", 0)),
        Case("w1b create name too long", "POST", S, route=cr, body={"name": "n" * 201}),
        Case("w1b create description too long", "POST", S, route=cr, body={"name": "x", "description": "d" * 2001}),
        Case("w1b create too many tags", "POST", S, route=cr, body={"name": "x", "tags": ["t"] * 51}),
        Case("w1b create bad tag types", "POST", S, route=cr, body={"name": "x", "tags": ["ok", 5, None, ["z"]]}),
        Case("w1b create too many bad tags", "POST", S, route=cr, body={"name": "x", "tags": [1] * 51}),
        Case("w1b create tags null", "POST", S, route=cr, body={"name": "x", "tags": None}),
        Case("w1b create tags not a list", "POST", S, route=cr, body={"name": "x", "tags": "abc"}),
        Case("w1b create name wrong type", "POST", S, route=cr, body={"name": 5, "description": 7}),
        Case("w1b create missing name", "POST", S, route=cr, body={"description": "only"}),
        Case("w1b create no body", "POST", S, route=cr),
        Case("w1b create body is a list", "POST", S, route=cr, body=["x"]),
        Case("w1b create invalid json", "POST", S, route=cr, raw_body=b"{nope", volatile={"ctx", "loc"}),
        Case("w1b create text/plain body", "POST", S, route=cr, raw_body=b'{"name": "x"}', content_type="text/plain"),
        Case("w1b create nul in name", "POST", S, route=cr, body={"name": "a\u0000b"}),
        # ── PATCH ─────────────────────────────────────────────────────────
        Case("w1b patch name analyst", "PATCH", P, who="analyst", route=up, volatile=upd,
             body={"name": "CT W1B renamed"}, state_check=w1b_check_patched("{s_patch}", ph, 1)),
        Case("w1b patch description and tags", "PATCH", P, route=up, volatile=upd,
             body={"description": "new d", "tags": ["x", "y", "z"]}, state_check=w1b_check_patched("{s_patch}", ph, 2)),
        Case("w1b patch tags empty", "PATCH", P, who="key_write", route=up, volatile=upd, body={"tags": []},
             state_check=w1b_check_patched("{s_patch}", ph, 3)),
        Case("w1b patch nulls change nothing", "PATCH", P, route=up, volatile=upd,
             body={"name": None, "description": None, "tags": None},
             state_check=w1b_check_patched("{s_patch}", ph, 4)),
        Case("w1b patch empty body object", "PATCH", P, route=up, volatile=upd, body={},
             state_check=w1b_check_patched("{s_patch}", ph, 5)),
        Case("w1b patch unknown field ignored", "PATCH", P, route=up, volatile=upd,
             body={"status": "COMPLETED", "name": "CT W1B kept"}, state_check=w1b_check_patched("{s_patch}", ph, 6)),
        Case("w1b patch clear description", "PATCH", P, route=up, volatile=upd, body={"description": ""},
             state_check=w1b_check_patched("{s_patch}", ph, 7)),
        Case("w1b patch viewer denied", "PATCH", P, who="viewer", route=up, body={"name": "nope"},
             state_check=w1b_check_patched("{s_patch}", ph, 7)),
        Case("w1b patch read key denied", "PATCH", P, who="key_read", route=up, body={"name": "nope"},
             state_check=w1b_check_patched("{s_patch}", ph, 7)),
        Case("w1b patch no auth", "PATCH", P, who="none", route=up, body={"name": "nope"}),
        Case("w1b patch not found", "PATCH", f"{S}/sess_nope", route=up, body={"name": "nope"}),
        Case("w1b patch wrong tenant", "PATCH", f"{S}/{{s_other_tenant}}", route=up, body={"name": "nope"}),
        Case("w1b patch validation beats not found", "PATCH", f"{S}/sess_nope", route=up,
             body={"name": "", "tags": ["a", 1]}),
        Case("w1b patch name empty", "PATCH", P, route=up, body={"name": ""},
             state_check=w1b_check_patched("{s_patch}", ph, 7)),
        Case("w1b patch name too long", "PATCH", P, route=up, body={"name": "n" * 201}),
        Case("w1b patch description too long", "PATCH", P, route=up, body={"description": "d" * 2001}),
        Case("w1b patch too many tags", "PATCH", P, route=up, body={"tags": ["t"] * 51}),
        Case("w1b patch bad tag types", "PATCH", P, route=up, body={"tags": [1, "a", 2.5]}),
        Case("w1b patch tags not a list", "PATCH", P, route=up, body={"tags": {"a": 1}}),
        Case("w1b patch wrong types", "PATCH", P, route=up, body={"name": 5, "description": []}),
        Case("w1b patch no body", "PATCH", P, route=up),
        Case("w1b patch invalid json", "PATCH", P, route=up, raw_body=b"[1,", volatile={"ctx", "loc"}),
        Case("w1b patch literal summary", "PATCH", f"{S}/summary", route=up, body={"name": "nope"}),
        Case("w1b patch then get", "GET", P, route="GET /sessions/{id}", volatile=upd),
    ]


# ── Wave 1b: lineage manifests (read-only) ───────────────────────────────────

def w1b_seed_manifests(db, fx: Fixture, main_sid: str, backtest_sid: str) -> None:
    """Manifests the worker would have written: two for the main session (the
    newer one FAILED), and a spread of runs for the duration aggregate that
    covers every branch of run_metrics (untimed, odd medians, size buckets,
    missing granularity, a string duration, float rounding ties)."""
    cur = db.cursor()
    rich = {"schema_version": 1, "timing": {"duration_seconds": 12.345, "stages": {"fit": 0.1, "predict": 1e-05}},
            "counts": {"skus_forecast": 7, "ratio": 0.30000000000000004}, "session": {"granularity": "W"},
            "trigger": {"kind": "user", "actor_id": "u", "label": "a@b.c"}, "note": "Pronóstico ☃",
            "nested": {"z": [1, 2.5, None, True], "a": {}}}
    cur.execute("DELETE FROM session_manifests WHERE tenant_id = %s", (fx.tenant_id,))

    def put(minutes_ago, sid, outcome, manifest, job="job_ct_m"):
        cur.execute("""INSERT INTO session_manifests (id, session_id, tenant_id, job_id, outcome, manifest, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NOW() - %s * INTERVAL '1 minute')""",
                    (f"man_ct_{secrets.token_hex(5)}", sid, fx.tenant_id, job, outcome, json.dumps(manifest),
                     minutes_ago))
    put(500, main_sid, "COMPLETED", rich)
    put(400, main_sid, "FAILED", {"schema_version": 1, "error": "boom", "timing": {"duration_seconds": 3}})
    runs = [  # minutes ago, outcome, duration, series, granularity
        (300, "COMPLETED", 10.0, 5, "D"), (290, "COMPLETED", 20.0, 40, "D"), (280, "COMPLETED", 31.25, 60, "W"),
        (270, "COMPLETED", 0.25, 100, "W"), (260, "COMPLETED", 2.675, 300, "M"), (250, "COMPLETED", 99.99, 1500, "M"),
        (240, "COMPLETED", 5.5, 5000, None), (230, "FAILED", 8.0, 10, "D"), (220, "COMPLETED", "abc", 10, "D"),
        (210, "COMPLETED", None, 10, "D"), (200, "COMPLETED", " 7.5 ", "12", "D"), (190, "COMPLETED", 4.0, "12.5", "D"),
        (180, "COMPLETED", 6.0, 0, "D"), (170, "COMPLETED", 9.0, None, "W"), (160, "COMPLETED", 1e-05, 51, "W"),
        (150, "COMPLETED", 123456789.123, 200, "W"), (140, "COMPLETED", 15.0, 201, ""),
    ]
    for ago, outcome, dur, series, gran in runs:
        m = {"timing": {"duration_seconds": dur}, "counts": {"skus_forecast": series}, "session": {"granularity": gran}}
        if dur is None:
            m = {"counts": {"skus_forecast": series}, "session": {"granularity": gran}}
        put(ago, backtest_sid, outcome, m)


def build_w1b_manifest_cases(fx: Fixture, ph: dict) -> list[Case]:
    S = f"{API}/sessions"
    other = "raw:Bearer " + mint_access_token(fx.secret, fx.admin_id, R1_OTHER_TENANT, "admin")
    man, dur = "GET /sessions/{id}/manifest", "GET /training/run-durations"
    D = f"{API}/training/run-durations"
    return [
        Case("w1b manifest latest", "GET", f"{S}/{{s_main}}/manifest", route=man),
        Case("w1b manifest viewer", "GET", f"{S}/{{s_main}}/manifest", who="viewer", route=man),
        Case("w1b manifest read key", "GET", f"{S}/{{s_main}}/manifest", who="key_read", route=man),
        Case("w1b manifest write key", "GET", f"{S}/{{s_main}}/manifest", who="key_write", route=man),
        Case("w1b manifest none yet", "GET", f"{S}/{{s_nodata}}/manifest", route=man),
        Case("w1b manifest session not found", "GET", f"{S}/sess_nope/manifest", route=man),
        Case("w1b manifest wrong tenant", "GET", f"{S}/{{s_other_tenant}}/manifest", route=man),
        Case("w1b manifest no auth", "GET", f"{S}/{{s_main}}/manifest", who="none", route=man),
        Case("w1b manifest expired", "GET", f"{S}/{{s_main}}/manifest", who="expired", route=man),
        Case("w1b manifest other tenant token", "GET", f"{S}/{{s_main}}/manifest", who=other, route=man),
        Case("w1b manifest wrong method", "POST", f"{S}/{{s_main}}/manifest", route=man),
        Case("w1b durations default", "GET", D, route=dur),
        Case("w1b durations viewer", "GET", D, who="viewer", route=dur),
        Case("w1b durations read key", "GET", D, who="key_read", route=dur),
        Case("w1b durations limit 1", "GET", f"{D}?limit=1", route=dur),
        Case("w1b durations limit 3", "GET", f"{D}?limit=3", route=dur),
        Case("w1b durations limit 7", "GET", f"{D}?limit=7", route=dur),
        Case("w1b durations limit 12", "GET", f"{D}?limit=12", route=dur),
        Case("w1b durations limit 200", "GET", f"{D}?limit=200", route=dur),
        Case("w1b durations limit float text", "GET", f"{D}?limit=5.0", route=dur),
        Case("w1b durations limit zero", "GET", f"{D}?limit=0", route=dur),
        Case("w1b durations limit too big", "GET", f"{D}?limit=201", route=dur),
        Case("w1b durations limit text", "GET", f"{D}?limit=abc", route=dur),
        Case("w1b durations limit last wins", "GET", f"{D}?limit=1&limit=2", route=dur),
        Case("w1b durations no runs", "GET", D, who=other, route=dur),
        Case("w1b durations no auth", "GET", D, who="none", route=dur),
        Case("w1b durations wrong method", "DELETE", D, route=dur),
    ]


# ── Wave 2: the message outbox ───────────────────────────────────────────────
#
# No Rust ROUTE writes to the outbox yet, so this section drives the Rust
# WRITER directly (an ignored cargo test, `outbox::tests::writes_what_python_writes`)
# and Python's own `outbox.enqueue` with the same scenarios, then compares the
# rows each wrote. After that Python's drain runs over both sets of rows with
# the senders replaced by a recorder (nothing is mailed) and the outcomes are
# compared again: what a row written by Rust turns into is exactly what a row
# written by Python turns into.

OUTBOX_SCENARIOS = [
    {"label": "otp", "channel": "email", "kind": "password_reset_otp", "recipient": " Who@Example.com ",
     "params": {"code": "123456"}},
    {"label": "verification", "channel": "email", "kind": "verification", "recipient": "v@example.com",
     "params": {"verify_url": "https://app.test/verify?token=abc", "full_name": "Vera Ñ"}},
    {"label": "reset", "channel": "email", "kind": "password_reset", "recipient": "r@example.com",
     "params": {"reset_url": "https://app.test/reset?token=def"}},
    {"label": "setup", "channel": "email", "kind": "account_setup", "recipient": "s@example.com",
     "params": {"setup_url": "https://app.test/setup?token=ghi"}},
    {"label": "change code", "channel": "email", "kind": "change_password_code", "recipient": "c@example.com",
     "params": {"code": "654321"}},
    {"label": "approval request", "channel": "email", "kind": "po_approval_request", "recipient": "a@example.com",
     "params": {"po_log_id": "po-nowhere", "amount": 1234.5, "requester_id": "usr_x", "approver_id": "usr_y"}},
    {"label": "approval decision", "channel": "email", "kind": "po_approval_decision", "recipient": "d@example.com",
     "params": {"po_log_id": "po-nowhere", "amount": 99, "approved": False, "comment": "too much é",
                "decider_id": "usr_z"}},
    {"label": "whatsapp code", "channel": "whatsapp", "kind": "verification_code", "recipient": "+50688887777",
     "params": {"code": "222222"}},
    {"label": "trial address", "channel": "email", "kind": "password_reset_otp",
     "recipient": "demo-abc123@stockai.demo", "params": {"code": "000000"}},
    {"label": "dedupe first", "channel": "email", "kind": "password_reset_otp", "recipient": "dd@example.com",
     "params": {"code": "1"}, "dedupe_key": "once"},
    {"label": "dedupe second", "channel": "email", "kind": "password_reset_otp", "recipient": "dd@example.com",
     "params": {"code": "1"}, "dedupe_key": "once"},
    {"label": "ttl clamped", "channel": "email", "kind": "password_reset_otp", "recipient": "t@example.com",
     "params": {"code": "1"}, "ttl_seconds": 10 ** 9},
    {"label": "ttl floor", "channel": "email", "kind": "password_reset_otp", "recipient": "t2@example.com",
     "params": {"code": "1"}, "ttl_seconds": 0},
    {"label": "refused unknown kind", "channel": "email", "kind": "nope", "recipient": "x@example.com", "params": {}},
    {"label": "refused wrong channel", "channel": "whatsapp", "kind": "password_reset_otp",
     "recipient": "+50688887777", "params": {"code": "1"}},
    {"label": "refused missing param", "channel": "email", "kind": "verification", "recipient": "x@example.com",
     "params": {"full_name": "n"}},
    {"label": "refused null param", "channel": "email", "kind": "password_reset_otp", "recipient": "x@example.com",
     "params": {"code": None}},
    {"label": "refused extra param", "channel": "email", "kind": "password_reset_otp", "recipient": "x@example.com",
     "params": {"code": "1", "extra": True}},
    {"label": "refused params list", "channel": "email", "kind": "password_reset_otp", "recipient": "x@example.com",
     "params": ["code"]},
    {"label": "refused blank recipient", "channel": "email", "kind": "password_reset_otp", "recipient": "   ",
     "params": {"code": "1"}},
    {"label": "refused unknown channel", "channel": "sms", "kind": "password_reset_otp", "recipient": "x",
     "params": {"code": "1"}},
]

OUTBOX_PY_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
from backend.config import settings
from backend.db.connection import init_pool
init_pool(settings.database_url, min_conn=1, max_conn=2)
from backend.notifications import outbox
tenant, side, scenarios = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
for s in scenarios:
    s = dict(s)
    label = s.pop("label")
    ids = outbox.enqueue(tenant, s["channel"], s["kind"], s["recipient"], s["params"],
                         created_by=s.get("created_by"), dedupe_key=s.get("dedupe_key"),
                         ttl_seconds=s.get("ttl_seconds", outbox.DEFAULT_TTL_SECONDS))
    print("ROW", label, ids or "none")
"""

OUTBOX_DRAIN_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
from backend.config import settings
from backend.db.connection import init_pool
init_pool(settings.database_url, min_conn=1, max_conn=2)
from backend.notifications import email, whatsapp, outbox
sent = []
email._send = lambda to, subject, html, attachment=None, tenant_id=None: sent.append(["email", to, subject])
whatsapp._send = lambda to, body, media_url=None, tenant_id=None: sent.append(["whatsapp", to, body])
email.is_configured = lambda tenant_id=None: True
whatsapp.is_configured = lambda tenant_id=None: True
total = 0
while True:
    n = outbox.process_due()
    total += n
    if n == 0:
        break
print("DRAINED", total)
print("SENT", json.dumps(sorted(sent)))
"""


def _outbox_rows(db, tenant_id: str, side: str):
    cur = db.cursor()
    cur.execute("""SELECT channel, kind, recipient, params, status, attempts, last_error, created_by, dedupe_key,
                          ROUND(EXTRACT(EPOCH FROM (expires_at - created_at)))::int, sent_at IS NOT NULL
                     FROM outbound_messages WHERE tenant_id = %s AND created_by = %s
                    ORDER BY created_at, kind, recipient""", (tenant_id, f"ct-{side}"))
    out = []
    for r in cur.fetchall():
        r = list(r)
        r[7] = "ct"                                        # the writer's tag, per side
        r[8] = r[8].rsplit("-", 1)[0] if r[8] else None   # the per-side suffix of the dedupe key
        out.append(r)
    return out


def run_outbox(args, fx: Fixture, db) -> list:
    import subprocess

    def verdict(name, problems):
        return (Case(name, "-", "-", route="outbox"), "FAIL" if problems else "PASS", problems)

    if db is None:
        print("outbox cases skipped: they need --db")
        return []
    if args.only and args.only not in "outbox":
        return []
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, **(read_env_file(args.env_file) if args.env_file else {}), "DATABASE_URL": args.db}
    tenant = fx.tenant_id
    cur = db.cursor()
    cur.execute("DELETE FROM outbound_messages WHERE tenant_id = %s", (tenant,))
    ids: dict[str, dict[str, str]] = {"py": {}, "rs": {}}
    for side in ("rs", "py"):
        scenarios = []
        for s in OUTBOX_SCENARIOS:
            s = dict(s)
            s["created_by"] = f"ct-{side}"
            if s.get("dedupe_key"):
                s["dedupe_key"] = f"{s['dedupe_key']}-{side}"
            scenarios.append(s)
        payload = json.dumps(scenarios)
        if side == "rs":
            cmd = ["cargo", "test", "--manifest-path", os.path.join(root, "backend-rs", "Cargo.toml"), "--quiet",
                   "outbox::tests::writes_what_python_writes", "--", "--ignored", "--nocapture"]
            run_env = {**env, "OUTBOX_TEST_DATABASE_URL": args.db, "OUTBOX_TEST_TENANT": tenant,
                       "OUTBOX_TEST_SCENARIOS": payload}
        else:
            cmd = [sys.executable, "-c", OUTBOX_PY_SCRIPT, tenant, side, payload]
            run_env = env
        proc = subprocess.run(cmd, cwd=root, env=run_env, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            return [verdict(f"outbox {side} writer ran", [f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}"])]
        for line in proc.stdout.splitlines():
            if line.startswith("ROW "):
                label, _, mid = line[4:].rpartition(" ")
                ids[side][label] = mid
    results = []
    # 1. Which scenarios were accepted.
    problems = []
    for s in OUTBOX_SCENARIOS:
        a, b = ids["py"].get(s["label"]), ids["rs"].get(s["label"])
        if (a == "none") != (b == "none") or a is None or b is None:
            problems.append(f"{s['label']}: python={a} rust={b}")
    results.append(verdict("outbox acceptance matches", problems))
    accepted = [s["label"] for s in OUTBOX_SCENARIOS if ids["py"].get(s["label"]) not in (None, "none")]
    results.append(verdict("outbox refused requests write nothing",
                           [] if all(not s["label"].startswith("refused") or ids["rs"][s["label"]] == "none"
                                     for s in OUTBOX_SCENARIOS) else ["a refused scenario was queued by Rust"]))
    # 2. The rows themselves.
    rp, rr = _outbox_rows(db, tenant, "py"), _outbox_rows(db, tenant, "rs")
    results.append(verdict("outbox rows are identical",
                           [] if rp == rr else [f"python={rp}", f"rust={rr}"]))
    results.append(verdict("outbox queued the expected number of rows",
                           [] if len(rr) == len(accepted) else [f"{len(rr)} rows for {len(accepted)} accepted scenarios"]))
    # 3. The drain turns both into the same outcome.
    proc = subprocess.run([sys.executable, "-c", OUTBOX_DRAIN_SCRIPT], cwd=root, env=env, capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        return results + [verdict("outbox drain ran", [f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}"])]
    sent = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("SENT "))[5:])
    after_p, after_r = _outbox_rows(db, tenant, "py"), _outbox_rows(db, tenant, "rs")
    results.append(verdict("outbox drain: same outcome per row", [] if after_p == after_r else
                           [f"python={after_p}", f"rust={after_r}"]))
    problems = []
    statuses = sorted((r[1], r[2], r[4], r[6]) for r in after_r)
    if not all(r[3] == {} for r in after_r):
        problems.append("a final row kept its params")
    if ("verification_code", "+50688887777", "sent", None) not in statuses:
        problems.append(f"the WhatsApp row did not send: {statuses}")
    if ("password_reset_otp", "demo-abc123@stockai.demo", "abandoned", "trial_address") not in statuses:
        problems.append("the trial address was not abandoned")
    if len(sent) != 2 * sum(1 for r in after_r if r[4] == "sent"):
        problems.append(f"{len(sent)} sends for {sum(1 for r in after_r if r[4] == 'sent')} sent rows per side")
    mails = [s for s in sent if s[1] == "Who@Example.com"]
    if len(mails) != 2 or not all(m[2] for m in mails):
        problems.append(f"the otp mail (recipient stripped) was not sent once per side: {mails}")
    results.append(verdict("outbox drain: what is sent and what is refused", problems))
    cur.execute("DELETE FROM outbound_messages WHERE tenant_id = %s", (tenant,))
    return results


# ── R3: webhooks CRUD, API keys, audit trail reads ───────────────────────────
#
# A sequence rather than a flat list: keys minted by one service are used on
# the other, revoked on one and tried on both, and the audit reads run before
# the exports (an export writes an audit row the next read would show).
#
# Verdicts: PASS, FAIL, SKIP (cannot be exercised here, says why) and, only
# with --allow-stale, STALE: the running Python process predates the Python
# SOURCE for that behaviour
# (uvicorn does not reload), so Python's answer is not the spec; Rust was
# checked against an expectation computed from the source instead, and that
# check passed. A STALE whose Rust check fails is a FAIL.

R3_PY_STALE = ("the Python dev process predates this source (webhooks 2592d73, scoped audit 50db9ad, X-API-Key "
               "header a593214, audit catalog additions); Rust checked against the source instead")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _source_catalog():
    """backend/audit/catalog.py as it is on disk (the spec), or None."""
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.audit import catalog  # noqa: PLC0415
        from backend.audit.service import audit_actions  # noqa: PLC0415
        return catalog, audit_actions
    except Exception as exc:  # noqa: BLE001 - optional: the venv may lack deps
        print(f"  (audit catalog source not importable: {exc})")
        return None


def run_r3(args, fx: Fixture, db) -> list:
    py, rs = args.python, args.rust
    out: list = []
    if db is None:
        return [(Case("r3 (all)", "-", "-", route="R3"), "SKIP",
                 ["R3 needs --db: key hashes, secrets and audit rows are checked in the database"])]

    def q1(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    def qa(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def cmp(name, route, method, path, *, who="admin", token=None, body=None, raw_body=None,
            content_type="application/json", volatile=(), path_rs=None, headers=None,
            check=None, stale_spec=None, csv=False, csv_filter=None):
        """Send one request to both services and diff. `check(rp, rr)` adds
        state problems; `stale_spec(rp, rr)` is the source-derived expectation
        for Rust when the live Python is known to be stale."""
        if args.only and args.only not in name:
            return None, None
        tok = token if token is not None else auth_for(fx, who)
        kw = dict(token=tok, body=body, raw_body=raw_body, content_type=content_type, headers=headers)
        rp = http(py, method, path, **kw)
        rr = http(rs, method, path_rs or path, **kw)
        problems = []
        if rp.status != rr.status:
            problems.append(f"status python={rp.status} rust={rr.status}")
        if csv:
            same = (csv_filter(rp.raw) == csv_filter(rr.raw)) if csv_filter else rp.raw == rr.raw
            if not same:
                problems.append(f"csv differs: python={rp.raw[:300]!r} rust={rr.raw[:300]!r}")
            for h in ("content-type", "content-disposition"):
                if rp.headers.get(h) != rr.headers.get(h):
                    problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")
        else:
            problems += diff(normalize(rp.body, set(volatile)), normalize(rr.body, set(volatile)))
        for h in ("www-authenticate", "retry-after"):
            if rp.headers.get(h) != rr.headers.get(h):
                problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")
        if check is not None and db is not None:
            problems += check(rp, rr)
        elif check is not None:
            problems.append("(state check skipped: no --db)")
        hard = [p for p in problems if not p.startswith("(")]
        verdict = "PASS"
        if hard:
            verdict = "FAIL"
            # Only on request: against a Python process running current main,
            # Python's answer IS the spec and a difference is a failure.
            if stale_spec is not None and getattr(args, "allow_stale", False):
                spec_problems = stale_spec(rp, rr)
                if not spec_problems:
                    verdict = "STALE"
                    problems = [f"({R3_PY_STALE})"] + [f"(python diff: {p})" for p in hard]
                else:
                    problems += [f"rust vs source: {p}" for p in spec_problems]
        if args.dump:
            print(f"\n--- {name}\nPY {rp.status} {rp.raw[:1200]!r}\nRS {rr.status} {rr.raw[:1200]!r}")
        out.append((Case(name, method, path, who=who, route=route), verdict, problems))
        return rp, rr

    def skip(name, route, why):
        if args.only and args.only not in name:
            return
        out.append((Case(name, "-", "-", route=route), "SKIP", [why]))

    def data(r):
        return r.body.get("data") if isinstance(r.body, dict) else None

    def last_events(action, resource=None, n=2):
        sql = ("SELECT user_id, action, resource, context, status FROM activity_logs "
               "WHERE tenant_id = %s AND action = %s")
        params = [fx.tenant_id, action]
        if resource is not None:
            sql += " AND resource = %s"
            params.append(resource)
        return qa(sql + " ORDER BY created_at DESC LIMIT %s", (*params, n))

    def same_events(action, resource=None, mask_resource=False, mask_target=False):
        rows = last_events(action, resource)
        if len(rows) < 2:
            return [f"expected an {action} row from each side, found {len(rows)}"]
        (u_r, a_r, res_r, c_r, s_r), (u_p, a_p, res_p, c_p, s_p) = rows[0], rows[1]
        if mask_target:
            c_r = {**c_r, "target_id": "<id>"}
            c_p = {**c_p, "target_id": "<id>"}
        probs = []
        if (u_r, a_r, s_r, c_r) != (u_p, a_p, s_p, c_p):
            probs.append(f"{action} rows differ: python={(u_p, s_p, c_p)} rust={(u_r, s_r, c_r)}")
        if not mask_resource and res_r != res_p:
            probs.append(f"{action} resource python={res_p!r} rust={res_r!r}")
        return probs

    # ── Fixture extensions: warehouses, a scoped admin, a scoped analyst ─────
    # Fresh tokens: the login token is 15 minutes old by now, and this
    # section can outlast it over a slow tunnel.
    for who_, uid_, role_ in (("admin", fx.admin_id, "admin"), ("analyst", fx.analyst_id, "analyst"),
                              ("viewer", fx.viewer_id, "viewer")):
        fx.tokens[who_] = mint_access_token(fx.secret, uid_, fx.tenant_id, role_, exp_minutes=120)
    fx.admin_token = admin = fx.tokens["admin"]
    for wh in ("R3-Norte", "R3-Sur"):
        r = http(py, "POST", f"{API}/inventory/warehouses", token=admin, body={"name": wh})
        if r.status not in (200, 201):
            print(f"  (warehouse {wh} not created: {r.status} {r.body})")
    whs = dict(qa("SELECT name, id FROM warehouses WHERE tenant_id = %s AND name IN ('R3-Norte', 'R3-Sur')",
                  (fx.tenant_id,))) if db else {}
    norte, sur = whs.get("R3-Norte", "wh-missing"), whs.get("R3-Sur", "wh-missing")
    tag = fx.tenant_id[-6:]
    extra = {}
    for who, role in (("scoped_admin", "admin"), ("scoped_analyst", "analyst")):
        r = http(py, "POST", f"{API}/users", token=admin, body={
            "email": f"contract-{tag}-{who.replace('_', '-')}@stockai.demo", "role": role,
            "full_name": f"Contract {who}"})
        if r.status != 201:
            print(f"  ({who} not invited: {r.status} {r.body})")
            continue
        uid = r.body["data"]["user"]["id"]
        s = http(py, "PUT", f"{API}/users/{uid}/warehouse-scope", token=admin, body={"warehouse_ids": [norte]})
        if s.status != 200:
            print(f"  ({who} not scoped: {s.status} {s.body})")
        extra[who] = mint_access_token(fx.secret, uid, fx.tenant_id, role, exp_minutes=120)
    fx.tokens.update(extra)

    # A second tenant, for the wrong-tenant cases.
    fxb = make_fixture(py, fx.secret)
    print(f"second throwaway tenant {fxb.tenant_id}")
    try:
        # A real public host: the current POST /webhooks resolves it (SSRF guard).
        rb = http(py, "POST", f"{API}/webhooks", token=fxb.admin_token,
                  body={"url": "https://example.com/r3-other-tenant/hook", "events": ["job.failed"]})
        b_row = q1("SELECT id FROM webhooks WHERE tenant_id = %s", (fxb.tenant_id,))
        if b_row is None:
            raise SystemExit(f"could not create the other tenant's webhook: {rb.status} {rb.body}")
        b_hook = b_row[0]
        b_key = q1("SELECT id FROM api_keys WHERE tenant_id = %s AND key_hash = %s",
                   (fxb.tenant_id, _sha256(fxb.read_key)))[0] if (db and fxb.read_key) else "x"

        # ── webhooks (2592d73 version: scoped hooks, delivery log) ───────
        # POST /webhooks and POST /webhooks/{id}/test stay Python, so hooks
        # are created through Python here and only the migrated routes are
        # compared.
        R_WE, R_WL, R_WD = "GET /webhooks/events", "GET /webhooks", "DELETE /webhooks/{id}"
        R_WR, R_WN, R_WV = ("POST /webhooks/{id}/rotate-secret", "POST /webhooks/{id}/enable",
                            "GET /webhooks/{id}/deliveries")

        def make_hook(url, scope=None):
            rc = http(py, "POST", f"{API}/webhooks", token=admin, body={"url": url, "events": ["job.failed"]})
            row = q1("SELECT id FROM webhooks WHERE tenant_id = %s AND url = %s ORDER BY created_at DESC",
                     (fx.tenant_id, url))
            if row is None:
                raise SystemExit(f"could not create the webhook {url} through Python: {rc.status} {rc.body}")
            if scope is not None:
                # Data on this run's own throwaway rows (the live Python predates
                # the create-with-scope API).
                db.cursor().execute("UPDATE webhooks SET warehouse_scope = %s::jsonb WHERE id = %s",
                                    (json.dumps(scope), row[0]))
            return row[0]

        has_new_schema = q1("""SELECT COUNT(*) FROM information_schema.columns
                                WHERE table_name = 'webhooks' AND column_name = 'warehouse_scope'""")[0] == 1 \
            and q1("SELECT to_regclass('webhook_deliveries') IS NOT NULL")[0]
        if not has_new_schema:
            skip("webhooks (all)", R_WL, "the dev database lacks the 2592d73 webhook columns / "
                 "webhook_deliveries (the Python that would migrate it has not run): nothing to compare")
        else:
            h_all = make_hook("https://example.com/r3/company-wide")
            h_norte = make_hook("https://example.com/r3/norte", [norte])
            h_both = make_hook("https://example.com/r3/norte-sur", [norte, sur])

            def py_present(row):
                (hid, url, events, created_at, scope, dis_at, dis_reason, fdays, rot) = row
                parsed = None if scope is None else (
                    [str(i) for i in scope] if isinstance(scope, list) else [])
                iso = lambda d: None if d is None else d.isoformat()  # noqa: E731
                return {"id": hid, "url": url, "events": events, "created_at": iso(created_at),
                        "warehouse_ids": parsed, "disabled_at": iso(dis_at), "disabled_reason": dis_reason,
                        "failure_days": fdays or 0, "secret_rotated_at": iso(rot)}

            def expected_list(user_scope):
                rows = qa("""SELECT id, url, events, created_at, warehouse_scope, disabled_at, disabled_reason,
                                    failure_days, secret_rotated_at
                               FROM webhooks WHERE tenant_id = %s ORDER BY created_at DESC""", (fx.tenant_id,))
                out_ = []
                for r in rows:
                    p = py_present(r)
                    hs = p["warehouse_ids"]
                    if user_scope is None or (hs is not None and all(w in user_scope for w in hs)):
                        out_.append(p)
                return out_

            def spec_list(user_scope):
                def spec(rp, rr):
                    if rr.status != 200:
                        return [f"rust status {rr.status}"]
                    return diff(expected_list(user_scope), data(rr))
                return spec

            cmp("wh list admin", R_WL, "GET", f"{API}/webhooks", stale_spec=spec_list(None),
                check=lambda rp, rr: [] if b"r3-other-tenant" not in rr.raw else ["rust lists another tenant's hook"])
            cmp("wh list viewer", R_WL, "GET", f"{API}/webhooks", who="viewer", stale_spec=spec_list(None))
            rp_list, rr_list = cmp("wh list read key", R_WL, "GET", f"{API}/webhooks", who="key_read",
                                   stale_spec=spec_list(None))
            cmp("wh list scoped analyst sees only its hooks", R_WL, "GET", f"{API}/webhooks",
                who="scoped_analyst", stale_spec=spec_list([norte]))
            cmp("wh list no auth", R_WL, "GET", f"{API}/webhooks", who="none")

            def spec_x_api_key(rp, rr):
                if rr.status != 200:
                    return [f"rust status {rr.status}, expected 200 (same as the Bearer read key)"]
                return diff(normalize(rr_list.body, {"timestamp"}), normalize(rr.body, {"timestamp"}))
            cmp("wh list X-API-Key header", R_WL, "GET", f"{API}/webhooks", who="none",
                headers={"X-API-Key": fx.read_key}, stale_spec=spec_x_api_key)
            cmp("wh list X-API-Key with a JWT", R_WL, "GET", f"{API}/webhooks", who="none",
                headers={"X-API-Key": admin})

            src_hooks = None
            try:
                _source_catalog()  # puts the repository root on sys.path
                from backend.webhooks import catalog as hook_catalog  # noqa: PLC0415
                src_hooks = {"api_version": hook_catalog.API_VERSION, "events": [
                    {"type": e.name, "data_keys": list(e.data_keys), "warehouse_aware": e.warehouse_aware}
                    for e in hook_catalog.EVENT_TYPES.values() if e.subscribable]}
            except Exception as exc:  # noqa: BLE001
                print(f"  (backend.webhooks.catalog not importable: {exc})")

            def spec_events(rp, rr):
                if src_hooks is None:
                    return ["source catalogue not importable"]
                return ([] if rr.status == 200 else [f"rust status {rr.status}"]) + diff(src_hooks, data(rr))
            cmp("wh events viewer", R_WE, "GET", f"{API}/webhooks/events", who="viewer", stale_spec=spec_events)
            cmp("wh events read key", R_WE, "GET", f"{API}/webhooks/events", who="key_read", stale_spec=spec_events)
            cmp("wh events no auth", R_WE, "GET", f"{API}/webhooks/events", who="none")

            NOT_FOUND = {"detail": "Webhook not found", "error_code": "webhook_not_found", "error_params": {}}

            def spec_status(status, body=None, extra=None):
                def spec(rp, rr):
                    probs = [] if rr.status == status else [f"rust status {rr.status}, expected {status}"]
                    if body is not None:
                        probs += diff(body, rr.body)
                    return probs + (extra(rr) if extra else [])
                return spec

            # ── deliveries (rows inserted for this run's own hook) ──
            cur = db.cursor()
            for i, (st, test) in enumerate((("delivered", False), ("failed", False), ("pending", True))):
                cur.execute("""INSERT INTO webhook_deliveries
                                   (tenant_id, webhook_id, event_id, event_type, is_test, payload, status,
                                    attempts, last_status_code, last_error, next_attempt_at, created_at)
                               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s, %s,
                                       -- not due for a day: the live Python delivery loop
                                       -- would otherwise claim the pending row mid-case
                                       NOW() + INTERVAL '1 day', NOW() - %s * INTERVAL '1 minute')""",
                            (fx.tenant_id, h_all, f"evt_r3_{i}", "webhook.test" if test else "job.failed", test, st,
                             i + 1, 200 if st == "delivered" else 503, None if st == "delivered" else "HTTP 503", i))

            def expected_deliveries(hid, status=None, limit=50):
                sql = """SELECT id, event_id, event_type, is_test, status, attempts, last_status_code, last_error,
                                next_attempt_at, created_at, last_attempt_at, delivered_at
                           FROM webhook_deliveries WHERE webhook_id = %s AND tenant_id = %s"""
                params = [hid, fx.tenant_id]
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                cols = ("id", "event_id", "event_type", "is_test", "status", "attempts", "last_status_code",
                        "last_error", "next_attempt_at", "created_at", "last_attempt_at", "delivered_at")
                rows = qa(sql + " ORDER BY created_at DESC LIMIT %s", (*params, limit))
                return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, r)} for r in rows]

            def spec_deliveries(hid, status=None, limit=50):
                return spec_status(200, extra=lambda rr: diff(expected_deliveries(hid, status, limit), data(rr)))
            cmp("wh deliveries viewer", R_WV, "GET", f"{API}/webhooks/{h_all}/deliveries", who="viewer",
                stale_spec=spec_deliveries(h_all))
            cmp("wh deliveries status + limit", R_WV, "GET",
                f"{API}/webhooks/{h_all}/deliveries?status=failed&limit=1", stale_spec=spec_deliveries(h_all, "failed", 1))
            cmp("wh deliveries validation", R_WV, "GET", f"{API}/webhooks/{h_all}/deliveries?status=nope&limit=0",
                stale_spec=lambda rp, rr: [] if rr.status == 422 and [e["loc"] for e in rr.body["detail"]] ==
                [["query", "status"], ["query", "limit"]] else [f"rust {rr.status} {rr.body}"])
            cmp("wh deliveries scoped analyst 404", R_WV, "GET", f"{API}/webhooks/{h_all}/deliveries",
                who="scoped_analyst", stale_spec=spec_status(404, NOT_FOUND))
            cmp("wh deliveries other tenant 404", R_WV, "GET", f"{API}/webhooks/{b_hook}/deliveries",
                stale_spec=spec_status(404, NOT_FOUND))
            cmp("wh deliveries no auth", R_WV, "GET", f"{API}/webhooks/{h_all}/deliveries?limit=0", who="none")

            # ── enable (each side re-enables a hook of its own) ──
            en = {s: make_hook(f"https://example.com/r3/enable-{s}") for s in ("py", "rs")}
            db.cursor().execute("""UPDATE webhooks SET disabled_at = NOW(), disabled_reason = 'failing',
                                          failure_days = 3, last_failure_on = CURRENT_DATE
                                    WHERE id IN (%s, %s)""", (en["py"], en["rs"]))

            def enabled_check(rr):
                row = q1("SELECT disabled_at, disabled_reason, failure_days, last_failure_on FROM webhooks WHERE id = %s",
                         (en["rs"],))
                probs = [] if row == (None, None, 0, None) else [f"rust left the hook disabled: {row}"]
                ev = last_events("audit.webhook.enabled", n=1)
                if not ev or ev[0][2] != en["rs"] or ev[0][3].get("target_id") != en["rs"] \
                        or ev[0][3].get("path") != "/webhooks/{webhook_id}/enable":
                    probs.append(f"audit.webhook.enabled row wrong: {ev}")
                return probs + diff({"id": en["rs"], "enabled": True}, data(rr))
            cmp("wh enable viewer denied", R_WN, "POST", f"{API}/webhooks/{en['py']}/enable",
                path_rs=f"{API}/webhooks/{en['rs']}/enable", who="viewer",
                stale_spec=spec_status(403, extra=lambda rr: [] if rr.body.get("error_code") == "role_not_permitted"
                                       else [f"rust {rr.body}"]))
            cmp("wh enable read key refused", R_WN, "POST", f"{API}/webhooks/{en['py']}/enable",
                path_rs=f"{API}/webhooks/{en['rs']}/enable", who="key_read",
                stale_spec=spec_status(403, extra=lambda rr: [] if rr.body.get("error_code") ==
                                       "api_key_scope_insufficient" else [f"rust {rr.body}"]))
            cmp("wh enable analyst", R_WN, "POST", f"{API}/webhooks/{en['py']}/enable",
                path_rs=f"{API}/webhooks/{en['rs']}/enable", who="analyst", volatile={"id"},
                stale_spec=spec_status(200, extra=enabled_check))
            cmp("wh enable scoped analyst 404", R_WN, "POST", f"{API}/webhooks/{h_both}/enable",
                who="scoped_analyst", stale_spec=spec_status(404, NOT_FOUND))

            # ── rotate-secret ──
            def rotated_check(rr):
                d = data(rr) or {}
                s = d.get("secret") or ""
                row = q1("SELECT secret, secret_rotated_at FROM webhooks WHERE id = %s", (h_norte,))
                probs = []
                if not (len(s) == 64 and all(c in "0123456789abcdef" for c in s)):
                    probs.append("rotated secret is not token_hex(32)")
                if row is None or row[0] != s or row[1] is None:
                    probs.append("stored secret / secret_rotated_at not updated to the returned one")
                ev = last_events("audit.webhook.secret_rotated", n=1)
                if not ev or ev[0][3].get("target_id") != h_norte or s in json.dumps(ev[0][3]):
                    probs.append(f"audit.webhook.secret_rotated row wrong or carries the secret: {ev}")
                listed = http(rs, "GET", f"{API}/webhooks", token=admin)
                if s.encode() in listed.raw:
                    probs.append("the list shows the secret")
                return probs
            cmp("wh rotate scoped analyst own hook", R_WR, "POST", f"{API}/webhooks/{h_norte}/rotate-secret",
                who="scoped_analyst", volatile={"secret"}, stale_spec=spec_status(200, extra=rotated_check))
            cmp("wh rotate viewer denied", R_WR, "POST", f"{API}/webhooks/{h_norte}/rotate-secret", who="viewer",
                stale_spec=spec_status(403))
            cmp("wh rotate not found", R_WR, "POST", f"{API}/webhooks/nope-123/rotate-secret",
                stale_spec=spec_status(404, NOT_FOUND))
            skip("wh rotate/enable plan_feature_locked", R_WR,
                 "TESTING_MODE=true skips ensure_feature(api) on both sides")

            # ── delete (each side deletes a hook of its own, deliveries too) ──
            dl = {s: make_hook(f"https://example.com/r3/delete-{s}") for s in ("py", "rs")}
            for s in ("py", "rs"):
                db.cursor().execute("""INSERT INTO webhook_deliveries (tenant_id, webhook_id, event_id, event_type, payload)
                                       VALUES (%s, %s, 'evt_r3_del', 'job.failed', '{}')""", (fx.tenant_id, dl[s]))

            def check_hook_deleted(rp, rr):
                if rr.status != 200:
                    return []
                probs = []
                if q1("SELECT COUNT(*) FROM webhooks WHERE id = %s", (dl["rs"],))[0]:
                    probs.append("rust left the webhook")
                if q1("SELECT COUNT(*) FROM webhook_deliveries WHERE webhook_id = %s", (dl["rs"],))[0]:
                    probs.append("rust left the hook's deliveries")
                if rp.status == 200:
                    probs += same_events("audit.webhook.deleted", mask_resource=True, mask_target=True)
                return probs
            cmp("wh delete viewer denied", R_WD, "DELETE", f"{API}/webhooks/{dl['py']}",
                path_rs=f"{API}/webhooks/{dl['rs']}", who="viewer")
            cmp("wh delete read key refused", R_WD, "DELETE", f"{API}/webhooks/{dl['py']}",
                path_rs=f"{API}/webhooks/{dl['rs']}", who="key_read")
            # Python gets a different out-of-scope hook: the live (pre-2592d73)
            # process has no scope check and would really delete it.
            cmp("wh delete scoped analyst out-of-scope hook 404", R_WD, "DELETE", f"{API}/webhooks/{h_both}",
                path_rs=f"{API}/webhooks/{h_all}", who="scoped_analyst", stale_spec=spec_status(404, NOT_FOUND, extra=lambda rr: [] if q1(
                    "SELECT COUNT(*) FROM webhooks WHERE id = %s", (h_all,))[0] == 1 else ["the hook was deleted"]))
            cmp("wh delete analyst", R_WD, "DELETE", f"{API}/webhooks/{dl['py']}",
                path_rs=f"{API}/webhooks/{dl['rs']}", who="analyst", volatile={"deleted"},
                check=check_hook_deleted)
            cmp("wh delete not found", R_WD, "DELETE", f"{API}/webhooks/nope-123")
            cmp("wh delete literal events", R_WD, "DELETE", f"{API}/webhooks/events")

            def other_hook_survives(rp, rr):
                n = q1("SELECT COUNT(*) FROM webhooks WHERE id = %s", (b_hook,))[0]
                return [] if n == 1 else ["the other tenant's webhook was deleted"]
            cmp("wh delete other tenant", R_WD, "DELETE", f"{API}/webhooks/{b_hook}", check=other_hook_survives)
            cmp("wh wrong method", R_WD, "PATCH", f"{API}/webhooks/{b_hook}", body={})

        # ── API keys ─────────────────────────────────────────────────────
        R_KC, R_KL, R_KU, R_KD = "POST /api-keys", "GET /api-keys", "GET /api-keys/usage", "DELETE /api-keys/{id}"
        minted = {}

        def key_row(raw):
            cur = db.cursor()
            cur.execute("""SELECT id, name, role, scope, last4, created_by, expires_at, warehouse_scope,
                                  key_hash, row_to_json(k)::text
                             FROM api_keys k WHERE tenant_id = %s AND key_hash = %s""",
                        (fx.tenant_id, _sha256(raw)))
            r = cur.fetchone()
            cols = ("id", "name", "role", "scope", "last4", "created_by", "expires_at",
                    "warehouse_scope", "key_hash", "row_text")
            return dict(zip(cols, r)) if r else None

        def check_minted(label, expect_days=None):
            def check(rp, rr):
                if rp.status != 200 or rr.status != 200:
                    return []
                kp, kr = data(rp)["key"], data(rr)["key"]
                minted[label] = {"py": kp, "rs": kr}
                probs = []
                import re as _re  # noqa: PLC0415
                for svc, k in (("python", kp), ("rust", kr)):
                    if not _re.fullmatch(r"sk_live_[A-Za-z0-9_-]{43}", k):
                        probs.append(f"{svc} key has the wrong format: {len(k)} chars")
                a, b = key_row(kp), key_row(kr)
                if a is None or b is None:
                    return probs + [f"row not found by sha256(key): python={a is not None} rust={b is not None}"]
                for svc, row, k in (("python", a, kp), ("rust", b, kr)):
                    if row["last4"] != k[-4:]:
                        probs.append(f"{svc} last4 {row['last4']!r} is not the key's")
                    if k in row["row_text"]:
                        probs.append(f"{svc} stored the raw key")
                    for r in (rp, rr):
                        if row["key_hash"].encode() in r.raw:
                            probs.append("a key hash was returned")
                for col in ("name", "role", "scope", "created_by", "warehouse_scope"):
                    if a[col] != b[col]:
                        probs.append(f"db column {col}: python={a[col]!r} rust={b[col]!r}")
                if (a["expires_at"] is None) != (b["expires_at"] is None):
                    probs.append(f"expires_at python={a['expires_at']} rust={b['expires_at']}")
                elif expect_days is not None:
                    gap = abs((a["expires_at"] - b["expires_at"]).total_seconds())
                    ahead = (b["expires_at"] - datetime.now(timezone.utc)).total_seconds() / 86400
                    if gap > 300 or not (expect_days - 1 < ahead <= expect_days):
                        probs.append(f"expires_at off: gap {gap}s, {ahead:.3f} days ahead")
                minted[label]["ids"] = {"py": a["id"], "rs": b["id"]}
                return probs + same_events("account.api_key_created", resource=a["name"])
            return check

        cmp("keys create admin write", R_KC, "POST", f"{API}/api-keys",
            body={"name": "  r3-nightly  ", "scope": "write"}, volatile={"key"},
            check=check_minted("write"))
        w = minted.get("write", {})
        if w:
            # Cross-service: a key minted by one service authenticates on both.
            # GET /api-keys is internal, so an AUTHENTICATED key gets 403
            # api_key_route_not_exposed (an unknown or revoked one gets 401
            # first): a schema-independent proof that the lookup succeeded.
            cmp("keys rust-minted key authenticates on both", R_KC, "GET", f"{API}/api-keys", token=w["rs"])
            cmp("keys python-minted key authenticates on both", R_KC, "GET", f"{API}/api-keys", token=w["py"])
            if has_new_schema:
                # A write route both serve: past the scope check to the handler's 404.
                cmp("keys rust-minted write key can write on both", R_KC, "DELETE",
                    f"{API}/webhooks/nope-by-key", token=w["rs"])

        def never_reveals(rp, rr):
            probs = []
            for svc, r in (("python", rp), ("rust", rr)):
                text = r.raw.decode()
                for k in [v for m in minted.values() for s, v in m.items() if s in ("py", "rs")]:
                    if k in text or _sha256(k) in text:
                        probs.append(f"{svc} list reveals a key or its hash")
                for item in (data(r) or []):
                    if "key" in item or "key_hash" in item:
                        probs.append(f"{svc} list item carries {sorted(set(item) & {'key', 'key_hash'})}")
            return probs
        cmp("keys list admin (no reveal)", R_KL, "GET", f"{API}/api-keys", volatile={"last_used"},
            check=never_reveals)
        cmp("keys list viewer", R_KL, "GET", f"{API}/api-keys", who="viewer", volatile={"last_used"})
        cmp("keys list read key refused", R_KL, "GET", f"{API}/api-keys", who="key_read")
        cmp("keys list no auth", R_KL, "GET", f"{API}/api-keys", who="none")
        cmp("keys create write key refused", R_KC, "POST", f"{API}/api-keys", who="key_write",
            body={"name": "by-a-key"})
        cmp("keys create analyst", R_KC, "POST", f"{API}/api-keys", who="analyst",
            body={"name": "r3-analyst"}, volatile={"key"}, check=check_minted("analyst"))

        def no_key_named(name):
            def check(rp, rr):
                n = q1("SELECT COUNT(*) FROM api_keys WHERE tenant_id = %s AND name = %s", (fx.tenant_id, name))[0]
                return [] if n == 0 else [f"{n} keys named {name!r} were written"]
            return check
        cmp("keys create viewer denied", R_KC, "POST", f"{API}/api-keys", who="viewer",
            body={"name": "r3-viewer"}, check=no_key_named("r3-viewer"))
        cmp("keys create no auth", R_KC, "POST", f"{API}/api-keys", who="none", body={"name": "x"})
        cmp("keys create invalid json no auth", R_KC, "POST", f"{API}/api-keys", who="none",
            raw_body=b"{nope", volatile={"ctx", "loc"})
        cmp("keys create expires 30", R_KC, "POST", f"{API}/api-keys", body={"name": "r3-exp", "expires_in_days": 30},
            volatile={"key"}, check=check_minted("exp", expect_days=30))
        cmp("keys create legacy role", R_KC, "POST", f"{API}/api-keys",
            body={"name": "r3-legacy", "role": "analyst", "expires_in_days": "7.0"}, volatile={"key"},
            check=check_minted("legacy", expect_days=7))
        cmp("keys create scope/role disagree", R_KC, "POST", f"{API}/api-keys",
            body={"name": "x", "scope": "read", "role": "analyst"})
        cmp("keys create validation many", R_KC, "POST", f"{API}/api-keys",
            body={"name": "  ", "scope": "x", "role": "admin", "expires_in_days": 0, "warehouse_ids": "a"})
        cmp("keys create validation types", R_KC, "POST", f"{API}/api-keys",
            body={"name": 5, "expires_in_days": [1], "warehouse_ids": [1, "a"]})
        cmp("keys create fractional days", R_KC, "POST", f"{API}/api-keys", body={"name": "x", "expires_in_days": 2.5})
        cmp("keys create days not a number", R_KC, "POST", f"{API}/api-keys", body={"name": "x", "expires_in_days": "abc"})
        cmp("keys create missing name", R_KC, "POST", f"{API}/api-keys", body={"scope": "read"})
        cmp("keys create body list", R_KC, "POST", f"{API}/api-keys", body=["x"])
        cmp("keys create no body", R_KC, "POST", f"{API}/api-keys")
        cmp("keys create huge expiry", R_KC, "POST", f"{API}/api-keys",
            body={"name": "r3-huge", "expires_in_days": 10 ** 12}, check=no_key_named("r3-huge"))
        cmp("keys create unknown warehouse", R_KC, "POST", f"{API}/api-keys",
            body={"name": "r3-wh", "warehouse_ids": ["wh_nope", norte]}, check=no_key_named("r3-wh"))
        cmp("keys create warehouses cleaned", R_KC, "POST", f"{API}/api-keys",
            body={"name": "r3-wh2", "warehouse_ids": [f" {norte} ", norte, " "]}, volatile={"key"},
            check=check_minted("wh2"))
        cmp("keys create scoped creator default", R_KC, "POST", f"{API}/api-keys", who="scoped_analyst",
            body={"name": "r3-scoped"}, volatile={"key"}, check=check_minted("scoped"))
        cmp("keys create scoped creator outside", R_KC, "POST", f"{API}/api-keys", who="scoped_analyst",
            body={"name": "r3-scoped2", "warehouse_ids": [sur]}, check=no_key_named("r3-scoped2"))
        cmp("keys create scoped creator inside", R_KC, "POST", f"{API}/api-keys", who="scoped_analyst",
            body={"name": "r3-scoped3", "warehouse_ids": [norte]}, volatile={"key"}, check=check_minted("scoped3"))
        skip("keys ceiling max_api_keys", R_KC,
             "TESTING_MODE=true on the dev API turns enforce_limit off on both sides; "
             "the Rust decision is unit-tested (limits::tests) against entitlements/service.py")
        skip("keys plan_feature_locked", R_KC,
             "TESTING_MODE=true skips ensure_feature(api) on both sides; covered by "
             "entitlements::tests::locked_feature_error_shape")

        this_month = datetime.now(timezone.utc).strftime("%Y-%m")
        cmp("keys usage admin", R_KU, "GET", f"{API}/api-keys/usage")
        cmp("keys usage month param", R_KU, "GET", f"{API}/api-keys/usage?month={this_month}")
        cmp("keys usage trailing newline", R_KU, "GET", f"{API}/api-keys/usage?month={this_month}%0A")
        cmp("keys usage past month", R_KU, "GET", f"{API}/api-keys/usage?month=2020-02")
        cmp("keys usage future month", R_KU, "GET", f"{API}/api-keys/usage?month=2099-12")
        cmp("keys usage bad month", R_KU, "GET", f"{API}/api-keys/usage?month=2026-13")
        cmp("keys usage blank month", R_KU, "GET", f"{API}/api-keys/usage?month=")
        cmp("keys usage year zero", R_KU, "GET", f"{API}/api-keys/usage?month=0000-01")
        cmp("keys usage analyst denied", R_KU, "GET", f"{API}/api-keys/usage", who="analyst")
        cmp("keys usage write key refused", R_KU, "GET", f"{API}/api-keys/usage", who="key_write")

        if w.get("ids"):
            def check_revoked(rp, rr):
                if rp.status != 200 or rr.status != 200:
                    return []
                n = q1("SELECT COUNT(*) FROM api_keys WHERE id IN (%s, %s)", (w["ids"]["py"], w["ids"]["rs"]))[0]
                probs = [] if n == 0 else [f"{n} revoked keys still exist"]
                return probs + same_events("account.api_key_revoked", mask_resource=True)
            cmp("keys revoke viewer denied", R_KD, "DELETE", f"{API}/api-keys/{w['ids']['py']}",
                path_rs=f"{API}/api-keys/{w['ids']['rs']}", who="viewer")
            cmp("keys revoke read key refused", R_KD, "DELETE", f"{API}/api-keys/{w['ids']['py']}",
                path_rs=f"{API}/api-keys/{w['ids']['rs']}", who="key_read")
            cmp("keys revoke analyst", R_KD, "DELETE", f"{API}/api-keys/{w['ids']['py']}",
                path_rs=f"{API}/api-keys/{w['ids']['rs']}", who="analyst", volatile={"revoked"},
                check=check_revoked)
            # Revoked on one service, refused by both.
            cmp("keys python-revoked key refused on both", R_KD, "GET", f"{API}/api-keys", token=w["py"])
            cmp("keys rust-revoked key refused on both", R_KD, "GET", f"{API}/api-keys", token=w["rs"])
        cmp("keys revoke not found", R_KD, "DELETE", f"{API}/api-keys/nope-123")
        cmp("keys revoke literal usage", R_KD, "DELETE", f"{API}/api-keys/usage")

        def other_key_survives(rp, rr):
            n = q1("SELECT COUNT(*) FROM api_keys WHERE id = %s", (b_key,))[0]
            probs = [] if n == 1 else ["the other tenant's key was revoked"]
            still = http(py, "GET", f"{API}/webhooks", token=fxb.read_key)
            return probs + ([] if still.status == 200 else [f"the other tenant's key stopped working: {still.status}"])
        cmp("keys revoke other tenant", R_KD, "DELETE", f"{API}/api-keys/{b_key}", check=other_key_survives)
        cmp("keys wrong method on usage", R_KD, "POST", f"{API}/api-keys/usage", body={})

        # ── audit trail reads (before any export: an export writes a row) ─
        R_AL, R_AF, R_AE = "GET /audit", "GET /audit/filters", "GET /audit/export"
        today = datetime.now(timezone.utc).date().isoformat()
        cmp("audit list admin", R_AL, "GET", f"{API}/audit")
        cmp("audit list api_key page 2", R_AL, "GET", f"{API}/audit?target_type=api_key&limit=3&offset=1")
        cmp("audit list by action", R_AL, "GET", f"{API}/audit?action=webhook.created")
        cmp("audit list actor status dates", R_AL, "GET",
            f"{API}/audit?actor={fx.admin_id}&status=success&date_from={today}&date_to={today}")
        cmp("audit list target id", R_AL, "GET", f"{API}/audit?target_id=r3-nightly")
        cmp("audit list unknown action", R_AL, "GET", f"{API}/audit?action=nope.never")
        cmp("audit list blank params", R_AL, "GET", f"{API}/audit?actor=&action=&target_type=api_call&status=error")
        cmp("audit list validation", R_AL, "GET",
            f"{API}/audit?limit=abc&offset=-1&status=foo&date_from=&date_to=2026-13-01&target_id={'x' * 201}")
        cmp("audit list validation 2", R_AL, "GET",
            f"{API}/audit?limit=0&date_from=2026-10-05T00:00:00&date_to=1700000000")
        cmp("audit list validation 3", R_AL, "GET",
            f"{API}/audit?limit=201&offset=5.0&date_from=2026-10-05T01:00:00Z&date_to=0")
        cmp("audit list validation no auth", R_AL, "GET", f"{API}/audit?limit=abc", who="none")
        cmp("audit list validation viewer", R_AL, "GET", f"{API}/audit?limit=abc", who="viewer")
        cmp("audit list analyst denied", R_AL, "GET", f"{API}/audit", who="analyst")
        cmp("audit list read key refused", R_AL, "GET", f"{API}/audit", who="key_read")
        def spec_company_wide(rp, rr):
            # backend/auth/warehouse_scope.py::require_company_wide (50db9ad).
            want = {"detail": "This shows company-wide totals, which are not available to a user "
                              "limited to some warehouses.",
                    "error_code": "warehouse_scope_company_totals", "error_params": {}}
            probs = [] if rr.status == 403 else [f"rust status {rr.status}, expected 403"]
            return probs + diff(want, rr.body)
        cmp("audit list scoped admin refused", R_AL, "GET", f"{API}/audit", who="scoped_admin",
            stale_spec=spec_company_wide)

        src = _source_catalog()

        def spec_filters(rp, rr):
            if src is None:
                return ["source catalog not importable; cannot judge"]
            catalog, audit_actions = src
            d = data(rr) or {}
            probs = []
            if d.get("target_types") != catalog.TARGET_TYPES:
                probs.append("target_types differ from backend/audit/catalog.py")
            if d.get("actions") != audit_actions():
                probs.append("actions differ from backend/audit/service.py")
            if d.get("actors") != (data(rp) or {}).get("actors"):
                probs.append("actors differ from Python's")
            return probs
        cmp("audit filters admin", R_AF, "GET", f"{API}/audit/filters", stale_spec=spec_filters)
        cmp("audit filters analyst denied", R_AF, "GET", f"{API}/audit/filters", who="analyst")
        cmp("audit filters scoped admin refused", R_AF, "GET", f"{API}/audit/filters", who="scoped_admin",
            stale_spec=spec_company_wide)
        cmp("audit filters write key refused", R_AF, "GET", f"{API}/audit/filters", who="key_write")

        def export_rows_check(expected_filters, unfiltered):
            """Each export writes an audit.export.audit_log row (Python's first,
            then Rust's). Both rows must have the same context; in an
            unfiltered file each export lists its OWN row first (it is
            committed before page 1 is read) and the note's `rows` is the
            file's row count minus that row."""
            def check(rp, rr):
                rows = qa("SELECT user_id, resource, context, status FROM activity_logs WHERE tenant_id = %s "
                          "AND action = 'audit.export.audit_log' ORDER BY created_at DESC LIMIT 2", (fx.tenant_id,))
                if len(rows) < 2:
                    return [f"expected an export row from each side, found {len(rows)}"]
                probs = []
                for svc, (user_id, resource, ctx, status), resp in (("rust", rows[0], rr), ("python", rows[1], rp)):
                    after = ctx.get("after") or {}
                    if user_id != fx.admin_id or resource is not None or status != "success":
                        probs.append(f"{svc} export row actor/resource/status: {(user_id, resource, status)}")
                    want = {"target_type": "audit_log", "target_id": None, "target_label": None, "before": None,
                            "actor_kind": "user", "method": "GET", "path": "/audit/export", "status_code": 200}
                    got = {k: ctx.get(k) for k in want}
                    if got != want:
                        probs.append(f"{svc} export row context {got}")
                    if after.get("format") != "csv" or after.get("filters") != expected_filters:
                        probs.append(f"{svc} export row after {after}")
                    lines = [ln for ln in resp.raw.decode().split("\r\n")[1:] if ln]
                    if unfiltered:
                        if after.get("rows") != len(lines) - 1:
                            probs.append(f"{svc} note rows={after.get('rows')} but the file has {len(lines)} rows")
                        if not lines or ",export.audit_log," not in lines[0] or fx.admin_id not in lines[0]:
                            probs.append(f"{svc}: the export's own row is not the newest line of the file")
                    elif after.get("rows") != len(lines):
                        probs.append(f"{svc} note rows={after.get('rows')} but the file has {len(lines)} rows")
                return probs
            return check

        def without_exports(raw):
            return b"\r\n".join(ln for ln in raw.split(b"\r\n") if b",export.audit_log," not in ln)
        cmp("audit export api_key", R_AE, "GET", f"{API}/audit/export?target_type=api_key", csv=True,
            check=export_rows_check({"target_type": "api_key"}, unfiltered=False))
        # The two unfiltered files differ by construction (Rust's lists
        # Python's export row too): compared without the export rows.
        cmp("audit export all", R_AE, "GET", f"{API}/audit/export", csv=True, csv_filter=without_exports,
            check=export_rows_check({}, unfiltered=True))
        cmp("audit export validation", R_AE, "GET", f"{API}/audit/export?status=nope&date_to=x")
        cmp("audit export analyst denied", R_AE, "GET", f"{API}/audit/export", who="analyst")
        cmp("audit export scoped admin refused", R_AE, "GET", f"{API}/audit/export", who="scoped_admin",
            stale_spec=spec_company_wide)
        cmp("audit export read key refused", R_AE, "GET", f"{API}/audit/export", who="key_read")
    finally:
        if not args.keep:
            erase_fixture(py, fxb)
    return out


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


# ── R4: purchase-order payments / cancellation, signal thresholds ───────────
#
# These routes change shared state (a PO's paid/cancelled marker, the tenant's
# threshold rows), so the cases do not send the same request to both services
# against the same object. Instead:
#
# * every PO case runs on a PAIR of purchase orders seeded identically in the
#   database, one per service (`po=` names the pair; a sequence of cases on
#   the same name continues on the same pair, which is how idempotency,
#   cancel-then-reopen and paid-then-unpaid are exercised);
# * every threshold write is preceded by `prepare`, which puts the tenant's
#   `stock_defaults` rows back to the same starting point for each side;
# * `capture` reads the state each side left behind (the PO row, the
#   activity rows on it, the units still counted as incoming, whether the
#   Python history screen lists it as cancelled/unpaid, the threshold rows
#   and the audit rows) and the two captures must be equal once each side's
#   own ids are replaced by placeholders.
#
# The fixture rows (warehouses, POs) are inserted straight into the throwaway
# tenant; every table involved cascades on the tenant, so the erasure at the
# end removes them.

R4_RECEIVABLE = ("pending", "partial", "not_received")
R4_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class R4Case:
    name: str
    method: str
    path: str                       # "{po}" is replaced by the side's PO id
    who: str = "admin"
    body: Any = None
    raw_body: Optional[bytes] = None
    content_type: Optional[str] = "application/json"
    po: Optional[tuple] = None      # (pair name, spec dict)
    prepare: Optional[Callable] = None   # (env, side) -> None, before each side
    capture: Optional[Callable] = None   # (env, side, po_id, since) -> value
    route: str = ""
    # The response lists orders of BOTH sides (an inbox spans the tenant):
    # mask every seeded order, not only this side's.
    mask_all: bool = False


@dataclass
class R4Env:
    fx: Fixture
    db: Any
    py: str
    norte_id: str = ""
    other_tenant: Optional[Fixture] = None
    other_po: str = ""
    supplier_id: str = ""
    pairs: dict = field(default_factory=dict)
    pa_mail: dict = field(default_factory=dict)   # case name -> outbox rows Rust queued


def _r4_signup(py: str, secret: str) -> Fixture:
    """A second throwaway tenant (signup, verify, login only) whose PO the first
    tenant's users must not reach."""
    tag = secrets.token_hex(4)
    email = f"contract-{tag}@stockai.demo"
    password = "Contract-" + secrets.token_urlsafe(12) + "9a"
    phone = "+5068" + "".join(secrets.choice("0123456789") for _ in range(7))
    r = http(py, "POST", f"{API}/auth/signup", body={
        "email": email, "password": password, "tenant_name": f"Contract {tag}",
        "full_name": "Contract Other", "whatsapp_number": phone, "accept_terms": True})
    if r.status != 201:
        raise SystemExit(f"second signup failed: {r.status} {r.body}")
    data = r.body["data"]
    if data.get("verify_url"):
        http(py, "POST", f"{API}/auth/verify-email",
             body={"token": data["verify_url"].split("token=", 1)[1]})
    r = http(py, "POST", f"{API}/auth/login", body={"email": email, "password": password})
    if r.status != 200:
        raise SystemExit(f"second login failed: {r.status} {r.body}")
    return Fixture(data["tenant"]["id"], data["user"]["id"], r.body["data"]["access_token"],
                   "", "", secret)


def _r4_seed_po(db, tenant_id: str, admin_id: str, sku: str, spec: dict) -> str:
    """One purchase order with one line, as the spec describes it."""
    cur = db.cursor()
    number = spec.get("po_number", "next")
    cur.execute(
        """INSERT INTO inventory_po_log
               (tenant_id, sku_count, total_units, po_number, sent_at, paid_at, paid_by,
                cancelled_at, cancelled_by, cancel_reason, reception_status,
                destination_warehouse)
           VALUES (%s, 1, %s,
                   CASE WHEN %s THEN (SELECT COALESCE(MAX(po_number), 0) + 1
                                        FROM inventory_po_log WHERE tenant_id = %s) END,
                   CASE WHEN %s THEN NOW() - INTERVAL '5 days' END,
                   CASE WHEN %s THEN NOW() - INTERVAL '1 day' END,
                   CASE WHEN %s THEN %s END,
                   CASE WHEN %s THEN NOW() - INTERVAL '2 days' END,
                   CASE WHEN %s THEN %s END, %s, %s, %s)
           RETURNING id""",
        (tenant_id, spec.get("qty", 10), number == "next", tenant_id,
         spec.get("sent", True), spec.get("paid", False), spec.get("paid", False), admin_id,
         spec.get("cancelled", False), spec.get("cancelled", False), admin_id,
         spec.get("cancel_reason"), spec.get("reception_status", "pending"),
         spec.get("destination")))
    po_id = cur.fetchone()[0]
    lines = spec.get("lines") or [{"sfx": "", "qty": spec.get("qty", 10), "received": spec.get("received")}]
    for ln in lines:
        cur.execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, recommended_qty, final_qty, received_qty, status, supplier,
                    warehouse, unit_cost, supplier_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (po_id, tenant_id, sku + ln.get("sfx", ""), ln["qty"], ln["qty"], ln.get("received"),
             ln.get("status", "approved"), ln.get("supplier"), ln.get("warehouse", "principal"),
             ln.get("unit_cost"), ln.get("supplier_id")))
    ap = spec.get("approval")
    if ap:
        cur.execute("""INSERT INTO po_approvals (tenant_id, po_log_id, status, amount, requested_by, request_note,
                                                 decided_by, decided_at, comment)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() END, %s)""",
                    (tenant_id, po_id, ap.get("status", "requested"), ap["amount"], ap["by_id"], ap.get("note"),
                     ap.get("decided_by_id"), ap.get("status", "requested") != "requested", ap.get("comment")))
        cur.execute("UPDATE inventory_po_log SET approval_status = %s, approved_amount = %s WHERE id = %s",
                    (ap.get("po_state", "pending_approval"), ap.get("approved_amount"), po_id))
    if spec.get("received_at"):
        cur.execute("UPDATE inventory_po_log SET received_at = NOW() - INTERVAL '1 day', received_by = %s "
                    "WHERE id = %s", (admin_id, po_id))
    for (sfx, warehouse), stock in (spec.get("stock") or {}).items():
        cur.execute("""INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (tenant_id, sku, warehouse) DO UPDATE SET current_stock = EXCLUDED.current_stock""",
                    (tenant_id, sku + sfx, warehouse, stock))
    for supplier in (spec.get("obs") or []):
        cur.execute("""INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
                       VALUES (%s, %s, %s, 9.5)""", (tenant_id, supplier, po_id))
    return po_id


def _r4_pair(env: R4Env, name: str, spec: dict) -> dict:
    if name not in env.pairs:
        if spec.get("approval"):
            ids = {"admin": env.fx.admin_id, "analyst": env.fx.analyst_id, "viewer": env.fx.viewer_id}
            ap = dict(spec["approval"])
            ap["by_id"] = ids[ap.pop("by", "analyst")]
            if ap.get("decided_by"):
                ap["decided_by_id"] = ids[ap.pop("decided_by")]
            spec = {**spec, "approval": ap}
        env.pairs[name] = {
            side: _r4_seed_po(env.db, env.fx.tenant_id, env.fx.admin_id, f"R4-{name}-{side}", spec)
            for side in ("py", "rs")}
    return env.pairs[name]


def _r4_number(db, po_id: str):
    cur = db.cursor()
    cur.execute("SELECT po_number FROM inventory_po_log WHERE id = %s", (po_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _r4_mask(value: Any, subs: dict) -> Any:
    """Replace this side's own ids / numbers / timestamps with placeholders."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("timestamp",):
                out[k] = "<masked>"
            elif k in ("paid_at", "cancelled_at", "updated_at", "created_at", "requested_at", "decided_at") \
                    and v is not None:
                out[k] = "<ts>"
            elif k == "po_number" and v is not None and v == subs.get("__number__"):
                out[k] = "<num>"
            else:
                out[k] = _r4_mask(v, subs)
        return out
    if isinstance(value, list):
        return [_r4_mask(v, subs) for v in value]
    if isinstance(value, str) and value in subs:
        return subs[value]
    if isinstance(value, str) and R4_UUID.match(value):
        return "<uuid>"          # a rule / approval / webhook id, per side
    if isinstance(value, str) and value.startswith("R4-"):
        # A seeded SKU carries its side (the stock rows are per tenant, so the
        # two sides cannot share a SKU): R4-<name>-py<line> becomes R4-<name><line>.
        return re.sub(r"-(py|rs)(?=[a-z]*$)", "", value)
    return value


def _r4_subs(env: R4Env, po_id: Optional[str], all_pos: bool = False) -> dict:
    """Strings to replace anywhere (the side's PO id and its OC reference),
    plus the side's PO number, replaced only under a `po_number` key."""
    if not po_id:
        return {}
    subs = {po_id: "<po>"}
    n = _r4_number(env.db, po_id)
    if n:
        subs["__number__"] = n
        subs[f"OC-{int(n):06d}"] = "<ref>"
    if all_pos:
        for sides in env.pairs.values():
            for other in sides.values():
                if other != po_id:
                    subs[other] = "<po>"
                    m = _r4_number(env.db, other)
                    if m:
                        subs[f"OC-{int(m):06d}"] = "<ref>"
    return subs


def _r4_since(db):
    cur = db.cursor()
    cur.execute("SELECT clock_timestamp()")
    return cur.fetchone()[0]


def r4_capture_po(env: R4Env, side: str, po_id: str, since, *, history: Optional[str] = None):
    """The PO row, its activity rows since the request, the units the
    inventory hub still counts as incoming (the WHERE of
    `service.get_incoming_detail`, copied), and - when `history` names a
    filter - whether Python's own /po-history/page lists the order under it."""
    cur = env.db.cursor()
    cur.execute("""SELECT paid_by, paid_at IS NOT NULL, cancelled_by, cancel_reason,
                          cancelled_at IS NOT NULL, reception_status, destination_warehouse
                     FROM inventory_po_log WHERE id = %s""", (po_id,))
    row = cur.fetchone()
    cur.execute("""SELECT action, user_id, resource, context, status FROM activity_logs
                    WHERE tenant_id = %s AND resource = %s AND created_at >= %s
                    ORDER BY created_at""", (env.fx.tenant_id, po_id, since))
    events = [list(r) for r in cur.fetchall()]
    cur.execute("""SELECT COALESCE(SUM(GREATEST(poi.final_qty - COALESCE(poi.received_qty, 0), 0)), 0)
                     FROM inventory_po_items poi
                     JOIN inventory_po_log pol ON pol.id = poi.po_log_id
                    WHERE poi.po_log_id = %s AND pol.reception_status IN %s
                      AND pol.cancelled_at IS NULL
                      AND poi.status IN ('approved', 'modified')""", (po_id, R4_RECEIVABLE))
    incoming = float(cur.fetchone()[0])
    out = {"row": list(row) if row else None, "events": events, "incoming": incoming}
    if history:
        r = http(env.py, "GET", f"{API}/inventory/po-history/page?status={history}&limit=100",
                 token=env.fx.token("admin"))
        ids = {i.get("id") for i in (r.body or {}).get("data", {}).get("items", [])} \
            if r.status == 200 else {"<history read failed>"}
        out[f"listed_as_{history}"] = po_id in ids
    return _r4_mask(out, _r4_subs(env, po_id))


def r4_capture_reversal(env: R4Env, side: str, po_id: str, since):
    """Everything an unreceive / unsend may touch: the PO header, its lines,
    the stock rows and snapshots of the order's SKUs (side tag removed), the
    lead-time observations, and the activity rows since the request."""
    cur = env.db.cursor()
    prefix = f"R4-{po_name_of(po_id, env)}-{side}"
    strip = lambda sku: sku[len(prefix):]  # noqa: E731
    cur.execute("""SELECT reception_status, received_at IS NOT NULL, received_by, sent_at IS NOT NULL,
                          paid_at IS NOT NULL FROM inventory_po_log WHERE id = %s""", (po_id,))
    header = list(cur.fetchone())
    cur.execute("SELECT sku, received_qty, status, warehouse FROM inventory_po_items WHERE po_log_id = %s "
                "ORDER BY sku", (po_id,))
    items = [[strip(r[0]), *r[1:]] for r in cur.fetchall()]
    cur.execute("SELECT sku, warehouse, current_stock FROM inventory_stock WHERE tenant_id = %s AND sku LIKE %s "
                "ORDER BY sku, warehouse", (env.fx.tenant_id, prefix + "%"))
    stock = [[strip(r[0]), *r[1:]] for r in cur.fetchall()]
    cur.execute("SELECT sku, warehouse, current_stock FROM inventory_snapshots WHERE tenant_id = %s "
                "AND sku LIKE %s ORDER BY recorded_at, sku, warehouse", (env.fx.tenant_id, prefix + "%"))
    snaps = [[strip(r[0]), *r[1:]] for r in cur.fetchall()]
    cur.execute("SELECT supplier FROM supplier_lead_time_obs WHERE tenant_id = %s AND po_log_id = %s ORDER BY 1",
                (env.fx.tenant_id, po_id))
    obs = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT action, user_id, resource, context, status FROM activity_logs
                    WHERE tenant_id = %s AND resource = %s AND created_at >= %s ORDER BY created_at""",
                (env.fx.tenant_id, po_id, since))
    events = [list(r) for r in cur.fetchall()]
    return _r4_mask({"header": header, "items": items, "stock": stock, "snapshots": snaps, "obs": obs,
                     "events": events}, _r4_subs(env, po_id))


def po_name_of(po_id: str, env: R4Env) -> str:
    """The pair name a PO id was seeded under."""
    for name, sides in env.pairs.items():
        if po_id in sides.values():
            return name
    return ""


# ── Wave 2: purchase-order approval ─────────────────────────────────────────

def pa_state(rules=(), approvers=("admin", "analyst")):
    """prepare: the tenant's approval rules become exactly `rules` and the
    people flagged as approvers exactly `approvers` (admin / analyst /
    scoped), identically before each side runs."""
    def prepare(env: R4Env, side: str) -> None:
        cur = env.db.cursor()
        fx = env.fx
        ensure_contract_hook(fx, env.db)
        cur.execute("DELETE FROM po_approval_rules WHERE tenant_id = %s", (fx.tenant_id,))
        # Invited users stay 'invited' until they verify; only an active person can approve.
        cur.execute("UPDATE users SET can_approve_po = FALSE, status = 'active' WHERE tenant_id = %s",
                    (fx.tenant_id,))
        ids = {"admin": fx.admin_id, "analyst": fx.analyst_id, "viewer": fx.viewer_id,
               **getattr(fx, "user_ids", {})}
        for who in approvers:
            cur.execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (ids[who],))
        for i, rule in enumerate(rules):
            cur.execute("""INSERT INTO po_approval_rules (tenant_id, threshold, warehouse, supplier_id,
                               self_approve_below, active, created_by, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, NOW() - %s * INTERVAL '1 minute')""",
                        (fx.tenant_id, rule["threshold"], rule.get("warehouse"),
                         env.supplier_id if rule.get("supplier") else None, rule.get("self_approve_below"),
                         rule.get("active", True), fx.admin_id, 100 - i))
    return prepare


def _norm_uuids(value):
    if isinstance(value, dict):
        return {k: _norm_uuids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm_uuids(v) for v in value]
    return "<uuid>" if isinstance(value, str) and R4_UUID.match(value) else value


def r4_capture_pa(env: R4Env, side: str, po_id: str, since, *, case: str = ""):
    """Everything an approval route may touch: the order's approval state and
    requests, the tenant's rules and approver flags, the activity and audit
    rows since the request, the webhook deliveries queued for the order, and
    (Rust side only, kept apart) the mail it queued on the outbox."""
    cur = env.db.cursor()
    out = {}
    if po_id:
        cur.execute("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = %s", (po_id,))
        out["header"] = list(cur.fetchone() or [])
        cur.execute("""SELECT status, amount, requested_by, request_note, decided_by, comment,
                              decided_at IS NOT NULL FROM po_approvals WHERE po_log_id = %s
                        ORDER BY requested_at, id""", (po_id,))
        out["approvals"] = [list(r) for r in cur.fetchall()]
    cur.execute("""SELECT threshold, warehouse, supplier_id, self_approve_below, active, created_by
                     FROM po_approval_rules WHERE tenant_id = %s ORDER BY threshold, warehouse, supplier_id""",
                (env.fx.tenant_id,))
    out["rules"] = [list(r) for r in cur.fetchall()]
    cur.execute("SELECT id, can_approve_po FROM users WHERE tenant_id = %s ORDER BY id", (env.fx.tenant_id,))
    out["flags"] = [list(r) for r in cur.fetchall()]
    cur.execute("""SELECT action, user_id, resource, context, status FROM activity_logs
                    WHERE tenant_id = %s AND created_at >= %s
                      AND (resource = %s OR action LIKE 'audit.%%')
                    ORDER BY created_at, action""", (env.fx.tenant_id, since, po_id or ""))
    out["events"] = [list(r) for r in cur.fetchall()]
    out["webhooks"] = hook_deliveries(env.db, env.fx.tenant_id, "po_log_id", po_id, since) if po_id else []
    cur.execute("""SELECT channel, kind, recipient, params, status, created_by, dedupe_key FROM outbound_messages
                    WHERE tenant_id = %s AND created_at >= %s ORDER BY created_at""",
                (env.fx.tenant_id, since))
    mail = [list(r) for r in cur.fetchall()]
    if side == "rs":
        env.pa_mail[case] = _norm_uuids(_r4_mask(mail, _r4_subs(env, po_id)))
    elif mail:
        out["python_queued_mail"] = mail        # Python sends directly: the outbox stays empty
    return _norm_uuids(_r4_mask(out, _r4_subs(env, po_id)))


def pa_capture(case: str):
    return lambda env, side, po_id, since: r4_capture_pa(env, side, po_id, since, case=case)



def r4_capture_po_history(history: str):
    return lambda env, side, po_id, since: r4_capture_po(env, side, po_id, since, history=history)


def r4_capture_thresholds(env: R4Env, side: str, po_id, since):
    cur = env.db.cursor()
    cur.execute("""SELECT scope_type, scope_value, lead_time_days, order_now_factor, overstock_factor
                     FROM stock_defaults WHERE tenant_id = %s ORDER BY scope_type, scope_value""",
                (env.fx.tenant_id,))
    rows = [list(r) for r in cur.fetchall()]
    cur.execute("""SELECT action, user_id, resource, context, status FROM activity_logs
                    WHERE tenant_id = %s AND action LIKE 'audit.%%' AND created_at >= %s
                    ORDER BY created_at""", (env.fx.tenant_id, since))
    return {"rows": rows, "audit": [list(r) for r in cur.fetchall()]}


def r4_reset_thresholds(seed: Optional[list] = None):
    """prepare: the tenant's stock_defaults rows become exactly `seed`."""
    def prepare(env: R4Env, side: str) -> None:
        cur = env.db.cursor()
        cur.execute("DELETE FROM stock_defaults WHERE tenant_id = %s", (env.fx.tenant_id,))
        for scope_type, scope_value, lead, on, os_ in (seed or []):
            cur.execute("""INSERT INTO stock_defaults (tenant_id, scope_type, scope_value,
                               lead_time_days, order_now_factor, overstock_factor)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (env.fx.tenant_id, scope_type, scope_value, lead, on, os_))
    return prepare


def r4_setup(args, fx: Fixture, db) -> R4Env:
    env = R4Env(fx=fx, db=db, py=args.python)
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM warehouses WHERE tenant_id = %s AND is_default", (fx.tenant_id,))
    has_default = cur.fetchone()[0] > 0
    cur.execute("""INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, 'principal', %s)
                   ON CONFLICT (tenant_id, name) DO NOTHING""", (fx.tenant_id, not has_default))
    cur.execute("""INSERT INTO warehouses (tenant_id, name) VALUES (%s, 'Norte')
                   ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                (fx.tenant_id,))
    env.norte_id = cur.fetchone()[0]
    # Two warehouse-scoped analysts, set up through the Python API: one
    # limited to Norte, one limited to nothing at all.
    for who, ids, role in (("scoped", [env.norte_id], "analyst"), ("scoped_none", [], "analyst"),
                           ("scoped_admin", [env.norte_id], "admin")):
        r = http(args.python, "POST", f"{API}/users", token=fx.admin_token, body={
            "email": f"contract-{secrets.token_hex(3)}-{who}@stockai.demo".replace("_", "-"),
            "role": role, "full_name": f"Contract {who}"})
        if r.status != 201:
            raise SystemExit(f"creating the {who} analyst failed: {r.status} {r.body}")
        uid = r.body["data"]["user"]["id"]
        r = http(args.python, "PUT", f"{API}/users/{uid}/warehouse-scope", token=fx.admin_token,
                 body={"warehouse_ids": ids})
        if r.status != 200:
            raise SystemExit(f"scoping the {who} analyst failed: {r.status} {r.body}")
        fx.tokens[who] = mint_access_token(fx.secret, uid, fx.tenant_id, role)
        fx.user_ids = {**getattr(fx, "user_ids", {}), who: uid}
    cur.execute("""INSERT INTO suppliers (tenant_id, name) VALUES (%s, 'ACME PA') RETURNING id""", (fx.tenant_id,))
    env.supplier_id = cur.fetchone()[0]
    env.other_tenant = _r4_signup(args.python, fx.secret)
    env.other_po = _r4_seed_po(db, env.other_tenant.tenant_id, env.other_tenant.admin_id,
                               "R4-OTHER", {"sent": True})
    return env


def build_r4_cases(env: R4Env) -> list[R4Case]:
    sent = {"sent": True}
    po = f"{API}/inventory/po/{{po}}"
    st = f"{API}/inventory/signal-thresholds"
    pay, unpay, cancel, uncancel = (
        "POST /inventory/po/{id}/mark-paid", "POST /inventory/po/{id}/mark-unpaid",
        "POST /inventory/po/{id}/cancel", "POST /inventory/po/{id}/uncancel")
    sget, sput, sdel = ("GET /inventory/signal-thresholds", "PUT /inventory/signal-thresholds",
                        "DELETE /inventory/signal-thresholds")
    cap = r4_capture_po
    cases = [
        # ── payments ───────────────────────────────────────────────────────
        R4Case("pay analyst", "POST", f"{po}/mark-paid", who="analyst", po=("pay-seq", sent),
               capture=r4_capture_po_history("unpaid"), route=pay),
        R4Case("pay again is idempotent", "POST", f"{po}/mark-paid", po=("pay-seq", sent),
               capture=cap, route=pay),
        R4Case("unpay analyst", "POST", f"{po}/mark-unpaid", who="analyst", po=("pay-seq", sent),
               capture=r4_capture_po_history("unpaid"), route=unpay),
        R4Case("unpay again is idempotent", "POST", f"{po}/mark-unpaid", po=("pay-seq", sent),
               capture=cap, route=unpay),
        R4Case("pay draft refused", "POST", f"{po}/mark-paid", po=("draft", {"sent": False}),
               capture=cap, route=pay),
        R4Case("pay cancelled refused", "POST", f"{po}/mark-paid",
               po=("cancelled", {"cancelled": True, "cancel_reason": "old"}), capture=cap, route=pay),
        R4Case("pay unnumbered", "POST", f"{po}/mark-paid", po=("unnumbered", {"po_number": None}),
               capture=cap, route=pay),
        R4Case("pay ignores a malformed body", "POST", f"{po}/mark-paid", po=("pay-body", sent),
               raw_body=b"{nope", capture=cap, route=pay),
        R4Case("pay not found", "POST", f"{API}/inventory/po/nope-123/mark-paid", route=pay),
        R4Case("pay other tenant's po", "POST", f"{API}/inventory/po/{env.other_po}/mark-paid",
               route=pay),
        R4Case("pay viewer denied", "POST", f"{po}/mark-paid", who="viewer", po=("pay-viewer", sent),
               capture=cap, route=pay),
        R4Case("pay no auth", "POST", f"{po}/mark-paid", who="none", po=("pay-viewer", sent), route=pay),
        R4Case("pay read key", "POST", f"{po}/mark-paid", who="key_read", po=("pay-viewer", sent),
               route=pay),
        R4Case("pay write key", "POST", f"{po}/mark-paid", who="key_write", po=("pay-viewer", sent),
               capture=cap, route=pay),
        R4Case("pay scoped: default warehouse refused", "POST", f"{po}/mark-paid", who="scoped",
               po=("pay-default-wh", sent), capture=cap, route=pay),
        R4Case("pay scoped: own warehouse", "POST", f"{po}/mark-paid", who="scoped",
               po=("pay-norte", {"destination": "Norte"}), capture=cap, route=pay),
        R4Case("pay scoped: own warehouse, other spelling", "POST", f"{po}/mark-paid", who="scoped",
               po=("pay-norte-spaced", {"destination": "  norte "}), capture=cap, route=pay),
        R4Case("pay scoped: unknown warehouse refused", "POST", f"{po}/mark-paid", who="scoped",
               po=("pay-sur", {"destination": "Sur"}), capture=cap, route=pay),
        R4Case("pay scoped: not found", "POST", f"{API}/inventory/po/nope-123/mark-paid",
               who="scoped", route=pay),
        R4Case("pay scoped to nothing", "POST", f"{po}/mark-paid", who="scoped_none",
               po=("pay-norte", {"destination": "Norte"}), capture=cap, route=pay),
        R4Case("unpay viewer denied", "POST", f"{po}/mark-unpaid", who="viewer",
               po=("paid", {"paid": True}), capture=cap, route=unpay),
        R4Case("unpay scoped refused", "POST", f"{po}/mark-unpaid", who="scoped",
               po=("paid", {"paid": True}), capture=cap, route=unpay),
        R4Case("unpay paid", "POST", f"{po}/mark-unpaid", po=("paid", {"paid": True}),
               capture=cap, route=unpay),
        R4Case("unpay not found", "POST", f"{API}/inventory/po/nope-123/mark-unpaid", route=unpay),
        # ── cancellation ───────────────────────────────────────────────────
        # Both cancel cases also capture the purchase_order.cancelled webhook
        # deliveries queued for the order: one on the real cancel, none on
        # the idempotent repeat.
        R4Case("cancel with reason", "POST", f"{po}/cancel", who="analyst", po=("cancel-seq", sent),
               body={"reason": "  supplier closed  "},
               prepare=lambda env, side: ensure_contract_hook(env.fx, env.db),
               capture=with_hook_deliveries(r4_capture_po_history("cancelled"), "po_log_id"),
               route=cancel),
        R4Case("cancel again is idempotent", "POST", f"{po}/cancel", po=("cancel-seq", sent),
               body={"reason": "another"}, capture=with_hook_deliveries(cap, "po_log_id"), route=cancel),
        R4Case("pay a cancelled order", "POST", f"{po}/mark-paid", po=("cancel-seq", sent),
               capture=cap, route=pay),
        R4Case("uncancel analyst", "POST", f"{po}/uncancel", who="analyst", po=("cancel-seq", sent),
               capture=r4_capture_po_history("cancelled"), route=uncancel),
        R4Case("uncancel again is idempotent", "POST", f"{po}/uncancel", po=("cancel-seq", sent),
               capture=cap, route=uncancel),
        R4Case("cancel without body", "POST", f"{po}/cancel", po=("cancel-nobody", sent),
               capture=cap, route=cancel),
        R4Case("cancel null body", "POST", f"{po}/cancel", po=("cancel-null", sent),
               raw_body=b"null", capture=cap, route=cancel),
        R4Case("cancel blank reason", "POST", f"{po}/cancel", po=("cancel-blank", sent),
               body={"reason": "   \n "}, capture=cap, route=cancel),
        R4Case("cancel long reason is cut", "POST", f"{po}/cancel", po=("cancel-long", sent),
               body={"reason": "  " + "é" * 498}, capture=cap, route=cancel),
        R4Case("cancel after partial reception", "POST", f"{po}/cancel",
               po=("partial", {"reception_status": "partial"}), capture=cap, route=cancel),
        R4Case("cancel after received units", "POST", f"{po}/cancel",
               po=("received-units", {"received": 2}), capture=cap, route=cancel),
        R4Case("cancel a paid order", "POST", f"{po}/cancel", po=("paid-2", {"paid": True}),
               capture=cap, route=cancel),
        R4Case("cancel reason too long", "POST", f"{po}/cancel", po=("cancel-val", sent),
               body={"reason": "x" * 501}, capture=cap, route=cancel),
        R4Case("cancel reason not a string", "POST", f"{po}/cancel", po=("cancel-val", sent),
               body={"reason": 5}, route=cancel),
        R4Case("cancel body is a list", "POST", f"{po}/cancel", po=("cancel-val", sent),
               body=[1], route=cancel),
        R4Case("cancel text body", "POST", f"{po}/cancel", po=("cancel-val", sent),
               raw_body=b"stop it", content_type="text/plain", route=cancel),
        R4Case("cancel invalid json", "POST", f"{po}/cancel", po=("cancel-val", sent),
               raw_body=b"{nope", route=cancel),
        R4Case("cancel invalid json no auth", "POST", f"{po}/cancel", who="none",
               po=("cancel-val", sent), raw_body=b"{nope", route=cancel),
        R4Case("cancel viewer denied before validation", "POST", f"{po}/cancel", who="viewer",
               po=("cancel-val", sent), body={"reason": 5}, capture=cap, route=cancel),
        R4Case("cancel scoped refused before validation", "POST", f"{po}/cancel", who="scoped",
               po=("cancel-val", sent), body={"reason": 5}, capture=cap, route=cancel),
        R4Case("cancel scoped own warehouse", "POST", f"{po}/cancel", who="scoped",
               po=("cancel-norte", {"destination": "Norte"}), body={"reason": "scoped"},
               capture=cap, route=cancel),
        R4Case("cancel write key", "POST", f"{po}/cancel", who="key_write", po=("cancel-val", sent),
               capture=cap, route=cancel),
        R4Case("cancel not found", "POST", f"{API}/inventory/po/nope-123/cancel", route=cancel),
        R4Case("cancel other tenant's po", "POST", f"{API}/inventory/po/{env.other_po}/cancel",
               route=cancel),
        R4Case("uncancel viewer denied", "POST", f"{po}/uncancel", who="viewer",
               po=("cancelled", {"cancelled": True, "cancel_reason": "old"}), capture=cap,
               route=uncancel),
        R4Case("uncancel cancelled order", "POST", f"{po}/uncancel",
               po=("cancelled", {"cancelled": True, "cancel_reason": "old"}), capture=cap,
               route=uncancel),
        R4Case("uncancel other tenant's po", "POST", f"{API}/inventory/po/{env.other_po}/uncancel",
               route=uncancel),
        R4Case("uncancel read key", "POST", f"{po}/uncancel", who="key_read",
               po=("cancelled", {"cancelled": True}), route=uncancel),
        # ── signal thresholds ──────────────────────────────────────────────
        R4Case("thresholds get defaults", "GET", st, prepare=r4_reset_thresholds(), route=sget),
        R4Case("thresholds get viewer with rows", "GET", st, who="viewer",
               prepare=r4_reset_thresholds([("global", "", None, 0.4, 4.0),
                                            ("supplier", "acme", 7, 0.3, 2.5),
                                            ("category", "food", None, None, None)]), route=sget),
        R4Case("thresholds get scoped", "GET", st, who="scoped", route=sget),
        R4Case("thresholds get no auth", "GET", st, who="none", route=sget),
        R4Case("thresholds get read key", "GET", st, who="key_read", route=sget),
        R4Case("thresholds put global", "PUT", st, who="analyst",
               body={"order_now_factor": 0.4, "overstock_factor": 4},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put overwrites", "PUT", st,
               body={"scope_type": "global", "scope_value": "ignored", "order_now_factor": "0.45",
                     "overstock_factor": "5"},
               prepare=r4_reset_thresholds([("global", "", 30, 0.4, 4.0)]),
               capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put supplier rounds", "PUT", st,
               body={"scope_type": "supplier", "scope_value": "  ACME Co ",
                     "order_now_factor": 0.333, "overstock_factor": 2.999},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put scoped analyst", "PUT", st, who="scoped",
               body={"scope_type": "category", "scope_value": "Food", "order_now_factor": 0.2,
                     "overstock_factor": 3},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put missing factor", "PUT", st, body={"order_now_factor": 0.4},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put empty body object", "PUT", st, body={},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put out of range", "PUT", st,
               body={"order_now_factor": 0.05, "overstock_factor": 3}, route=sput),
        R4Case("thresholds put overstock out of range", "PUT", st,
               body={"order_now_factor": 0.5, "overstock_factor": 13}, route=sput),
        R4Case("thresholds put infinite", "PUT", st,
               body={"order_now_factor": "inf", "overstock_factor": 3}, route=sput),
        R4Case("thresholds put supplier without name", "PUT", st,
               body={"scope_type": "supplier", "scope_value": "  ", "order_now_factor": 0.5,
                     "overstock_factor": 3}, route=sput),
        R4Case("thresholds put bad scope", "PUT", st,
               body={"scope_type": "warehouse", "order_now_factor": 0.5, "overstock_factor": 3},
               route=sput),
        R4Case("thresholds put null scope", "PUT", st, body={"scope_type": None}, route=sput),
        R4Case("thresholds put many field errors", "PUT", st,
               body={"scope_type": 3, "scope_value": 4, "order_now_factor": "abc",
                     "overstock_factor": [1]}, route=sput),
        R4Case("thresholds put no body", "PUT", st, route=sput),
        R4Case("thresholds put list body", "PUT", st, body=[1], route=sput),
        R4Case("thresholds put invalid json no auth", "PUT", st, who="none", raw_body=b"{nope",
               route=sput),
        R4Case("thresholds put viewer denied", "PUT", st, who="viewer",
               body={"order_now_factor": 0.4, "overstock_factor": 4},
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sput),
        R4Case("thresholds put write key", "PUT", st, who="key_write",
               body={"order_now_factor": 0.4, "overstock_factor": 4}, route=sput),
        R4Case("thresholds delete global", "DELETE", st,
               prepare=r4_reset_thresholds([("global", "", None, 0.4, 4.0)]),
               capture=r4_capture_thresholds, route=sdel),
        R4Case("thresholds delete keeps other rules", "DELETE",
               f"{st}?scope_type=supplier&scope_value=+ACME+",
               prepare=r4_reset_thresholds([("supplier", "acme", 7, 0.3, 2.5),
                                            ("global", "", None, 0.4, 4.0)]),
               capture=r4_capture_thresholds, route=sdel),
        R4Case("thresholds delete nothing", "DELETE", f"{st}?scope_type=category&scope_value=x",
               prepare=r4_reset_thresholds(), capture=r4_capture_thresholds, route=sdel),
        R4Case("thresholds delete bad scope", "DELETE", f"{st}?scope_type=bogus", route=sdel),
        R4Case("thresholds delete category without name", "DELETE", f"{st}?scope_type=category",
               route=sdel),
        R4Case("thresholds delete viewer denied", "DELETE", st, who="viewer",
               prepare=r4_reset_thresholds([("global", "", None, 0.4, 4.0)]),
               capture=r4_capture_thresholds, route=sdel),
        R4Case("thresholds delete write key", "DELETE", st, who="key_write", route=sdel),
    ]
    cases += build_pa_cases(env)
    # ── wave 1b: the reception / send reversals ────────────────────────────
    unrec, unsend = "POST /inventory/po/{id}/unreceive", "POST /inventory/po/{id}/unsend"
    revcap = r4_capture_reversal
    rec2 = {"reception_status": "received", "received_at": True, "obs": ["ACME", "Zeta"],
            "lines": [{"sfx": "a", "qty": 10, "received": 6, "supplier": "ACME"},
                      {"sfx": "b", "qty": 5, "received": 5, "supplier": "Zeta", "warehouse": "Norte"},
                      {"sfx": "r", "qty": 4, "received": 4, "supplier": "ACME", "status": "rejected"}],
            "stock": {("a", "principal"): 20, ("b", "Norte"): 5, ("r", "principal"): 4}}
    cases += [
        R4Case("unreceive analyst", "POST", f"{po}/unreceive", who="analyst", po=("rev-main", rec2),
               capture=revcap, route=unrec),
        R4Case("unreceive again refused", "POST", f"{po}/unreceive", po=("rev-main", rec2),
               capture=revcap, route=unrec),
        R4Case("unreceive nothing received yet", "POST", f"{po}/unreceive", po=("rev-none", sent),
               capture=revcap, route=unrec),
        R4Case("unreceive stock short refuses all", "POST", f"{po}/unreceive",
               po=("rev-short", {**rec2, "stock": {("a", "principal"): 20, ("b", "Norte"): 2}}),
               capture=revcap, route=unrec),
        R4Case("unreceive missing stock row", "POST", f"{po}/unreceive",
               po=("rev-nostock", {**rec2, "stock": {("a", "principal"): 20}}), capture=revcap, route=unrec),
        R4Case("unreceive exact stock", "POST", f"{po}/unreceive",
               po=("rev-exact", {**rec2, "stock": {("a", "principal"): 6, ("b", "Norte"): 5}}),
               capture=revcap, route=unrec),
        R4Case("unreceive same sku on two lines", "POST", f"{po}/unreceive",
               po=("rev-dup", {"reception_status": "partial", "received_at": True, "obs": [],
                               "lines": [{"sfx": "a", "qty": 5, "received": 1.5, "supplier": "ACME"},
                                         {"sfx": "a", "qty": 5, "received": 2.25, "supplier": "ACME"}],
                               "stock": {("a", "principal"): 10}}),
               capture=revcap, route=unrec),
        R4Case("unreceive line falls back to the destination", "POST", f"{po}/unreceive",
               po=("rev-dest", {"reception_status": "partial", "received_at": True, "destination": "Norte",
                                "lines": [{"sfx": "a", "qty": 5, "received": 3, "warehouse": ""}],
                                "stock": {("a", "Norte"): 9}}),
               capture=revcap, route=unrec),
        R4Case("unreceive received_at but nothing counted", "POST", f"{po}/unreceive",
               po=("rev-zero", {"reception_status": "received", "received_at": True,
                                "lines": [{"sfx": "a", "qty": 5, "received": 0}]}),
               capture=revcap, route=unrec),
        R4Case("unreceive viewer denied", "POST", f"{po}/unreceive", who="viewer",
               po=("rev-viewer", rec2), capture=revcap, route=unrec),
        R4Case("unreceive read key denied", "POST", f"{po}/unreceive", who="key_read",
               po=("rev-viewer", rec2), capture=revcap, route=unrec),
        R4Case("unreceive no auth", "POST", f"{po}/unreceive", who="none", po=("rev-viewer", rec2),
               route=unrec),
        R4Case("unreceive write key", "POST", f"{po}/unreceive", who="key_write",
               po=("rev-key", rec2), capture=revcap, route=unrec),
        R4Case("unreceive scoped default warehouse refused", "POST", f"{po}/unreceive", who="scoped",
               po=("rev-scoped-no", rec2), capture=revcap, route=unrec),
        R4Case("unreceive scoped own warehouse", "POST", f"{po}/unreceive", who="scoped",
               po=("rev-scoped-yes", {**rec2, "destination": "Norte"}), capture=revcap, route=unrec),
        R4Case("unreceive not found", "POST", f"{API}/inventory/po/nope-123/unreceive", route=unrec),
        R4Case("unreceive other tenant's po", "POST", f"{API}/inventory/po/{env.other_po}/unreceive",
               route=unrec),
        R4Case("unreceive ignores a malformed body", "POST", f"{po}/unreceive", raw_body=b"{nope",
               po=("rev-body", rec2), capture=revcap, route=unrec),
        R4Case("unsend analyst", "POST", f"{po}/unsend", who="analyst", po=("uns-main", sent),
               capture=revcap, route=unsend),
        R4Case("unsend again refused", "POST", f"{po}/unsend", po=("uns-main", sent), capture=revcap,
               route=unsend),
        R4Case("unsend a draft", "POST", f"{po}/unsend", po=("uns-draft", {"sent": False}), capture=revcap,
               route=unsend),
        R4Case("unsend after reception", "POST", f"{po}/unsend",
               po=("uns-recv", {"reception_status": "partial"}), capture=revcap, route=unsend),
        R4Case("unsend after payment", "POST", f"{po}/unsend", po=("uns-paid", {"paid": True}),
               capture=revcap, route=unsend),
        R4Case("unsend viewer denied", "POST", f"{po}/unsend", who="viewer", po=("uns-viewer", sent),
               capture=revcap, route=unsend),
        R4Case("unsend read key denied", "POST", f"{po}/unsend", who="key_read", po=("uns-viewer", sent),
               route=unsend),
        R4Case("unsend write key", "POST", f"{po}/unsend", who="key_write", po=("uns-key", sent),
               capture=revcap, route=unsend),
        R4Case("unsend scoped refused", "POST", f"{po}/unsend", who="scoped", po=("uns-scoped", sent),
               capture=revcap, route=unsend),
        R4Case("unsend scoped own warehouse", "POST", f"{po}/unsend", who="scoped",
               po=("uns-scoped-yes", {"destination": "Norte"}), capture=revcap, route=unsend),
        R4Case("unsend unnumbered", "POST", f"{po}/unsend", po=("uns-unnumbered", {"po_number": None}),
               capture=revcap, route=unsend),
        R4Case("unsend no auth", "POST", f"{po}/unsend", who="none", po=("uns-viewer", sent), route=unsend),
        R4Case("unsend not found", "POST", f"{API}/inventory/po/nope-123/unsend", route=unsend),
        R4Case("unsend other tenant's po", "POST", f"{API}/inventory/po/{env.other_po}/unsend", route=unsend),
    ]
    return cases


# What the Rust side must have queued for the requester, by case. Python sends
# the mail itself, so there is no row to compare; instead the row Rust wrote
# is checked against what the decision implies.
PA_MAIL_EXPECTED: dict[str, Optional[dict]] = {}


def pa_mail_verdicts(env: R4Env, args) -> list:
    out = []
    for name, rows in env.pa_mail.items():
        want = PA_MAIL_EXPECTED.get(name)
        problems = []
        if want is None:
            if rows:
                problems.append(f"expected no mail, Rust queued {rows}")
        elif len(rows) != 1:
            problems.append(f"expected one queued mail, got {rows}")
        else:
            channel, kind, recipient, params, status, created_by, dedupe = rows[0]
            people = {"admin": env.fx.admin_id, "analyst": env.fx.analyst_id, **getattr(env.fx, "user_ids", {})}
            requester, decider = people[want.get("requester", "analyst")], people[want.get("decider", "admin")]
            expected_params = {"po_log_id": "<po>", "amount": want["amount"], "approved": want["approved"],
                               "requester_id": requester, "decider_id": decider}
            if want.get("comment"):
                expected_params["comment"] = want["comment"]
            if (channel, kind, status, created_by) != ("email", "po_approval_decision", "pending", decider):
                problems.append(f"row shape: {rows[0]}")
            if params != expected_params:
                problems.append(f"params {params} != {expected_params}")
            if not str(dedupe).startswith("po_approval_decision:"):
                problems.append(f"dedupe key {dedupe!r}")
            cur = env.db.cursor()
            cur.execute("SELECT email FROM users WHERE id = %s", (requester,))
            if recipient != cur.fetchone()[0]:
                problems.append(f"recipient {recipient!r} is not the requester")
        out.append((R4Case(f"mail: {name}", "-", "-", route="outbox (approvals)"),
                    "FAIL" if problems else "PASS", problems))
    return out


def build_pa_cases(env: R4Env) -> list[R4Case]:
    A = f"{API}/inventory"
    po = f"{A}/po/{{po}}/approval"
    S, RULES, APPR, PEND = (f"{A}/po-approval/settings", f"{A}/po-approval/rules", f"{A}/po-approval/approvers",
                            f"{A}/po-approval/pending")
    st, cr, pa, de, ap, pe, ga, aa, rj = (
        "GET /inventory/po-approval/settings", "POST /inventory/po-approval/rules",
        "PATCH /inventory/po-approval/rules/{id}", "DELETE /inventory/po-approval/rules/{id}",
        "PUT /inventory/po-approval/approvers/{id}", "GET /inventory/po-approval/pending",
        "GET /inventory/po/{id}/approval", "POST /inventory/po/{id}/approval/approve",
        "POST /inventory/po/{id}/approval/reject")
    sup = env.supplier_id

    def line(cost, qty=10, **kw):
        return {"sfx": "a", "qty": qty, "unit_cost": cost, "supplier": "ACME PA", "supplier_id": sup, **kw}

    big = {"lines": [line(100)]}                                  # 1,000
    small = {"lines": [line(5)]}                                  # 50
    unpriced = {"lines": [{"sfx": "a", "qty": 10}]}
    norte_big = {"lines": [line(100, warehouse="Norte")], "destination": "Norte"}
    asked = lambda by, **kw: {"by": by, "amount": 1000.0, "note": "please", **kw}  # noqa: E731
    open_analyst = {**big, "approval": asked("analyst")}
    open_admin = {**big, "approval": asked("admin")}
    approved = {**big, "approval": asked("analyst", status="approved", decided_by="admin", comment="fine",
                                         po_state="approved", approved_amount=1000.0)}
    grown = {**big, "approval": asked("analyst", status="approved", decided_by="admin", comment="fine",
                                      po_state="approved", approved_amount=500.0, amount=500.0)}
    rejected = {**big, "approval": asked("analyst", status="rejected", decided_by="admin", comment="no way",
                                         po_state="rejected")}
    r100 = [{"threshold": 100}]
    r_self = [{"threshold": 100, "self_approve_below": 5000}]
    r_mixed = [{"threshold": 100, "warehouse": "Norte"}, {"threshold": 500},
               {"threshold": 300, "supplier": True, "self_approve_below": 900}]
    r_off = [{"threshold": 100, "active": False}]
    both = pa_state(r100)
    none = pa_state([], approvers=())
    cap = pa_capture
    good_rule = {"threshold": 250.5, "self_approve_below": 1000, "warehouse": "  norte ", "supplier_id": sup}

    def C(name, method, path, **kw):
        kw.setdefault("route", {"GET": "", "POST": "", "PATCH": "", "PUT": "", "DELETE": ""}[method])
        return R4Case(name, method, path, capture=kw.pop("capture", cap(name)), **kw)

    def mail(name, **want):
        PA_MAIL_EXPECTED[name] = want

    mail("pa approve by admin", approved=True, amount=1000.0)
    mail("pa reject by admin", approved=False, amount=1000.0, comment="too expensive")
    mail("pa approve with comment", approved=True, amount=1000.0, comment="go ahead")
    mail("pa approve with a null body", approved=True, amount=1000.0)
    mail("pa approve scoped own warehouse", approved=True, amount=1000.0, requester="admin", decider="scoped")
    mail("pa approve rule dropped afterwards", approved=True, amount=1000.0)
    mail("pa approve when the order grew", approved=True, amount=400.0)
    cases = [
        # ── settings ───────────────────────────────────────────────────────
        C("pa settings none", "GET", S, prepare=none, route=st),
        C("pa settings rules and approvers", "GET", S, prepare=pa_state(r_mixed + r_off), route=st),
        C("pa settings viewer", "GET", S, who="viewer", prepare=pa_state(r_mixed, ("admin",)), route=st),
        C("pa settings approver sees is_approver", "GET", S, who="analyst", prepare=both, route=st),
        C("pa settings scoped hides other warehouses", "GET", S, who="scoped",
          prepare=pa_state(r_mixed + [{"threshold": 50, "warehouse": "Sur"}]), route=st),
        C("pa settings scoped to nothing", "GET", S, who="scoped_none", prepare=pa_state(r_mixed), route=st),
        C("pa settings read key", "GET", S, who="key_read", route=st),
        C("pa settings no auth", "GET", S, who="none", route=st),
        # ── rules: create ──────────────────────────────────────────────────
        C("pa rule create", "POST", RULES, body=good_rule, prepare=both, route=cr),
        C("pa rule create minimal", "POST", RULES, body={"threshold": 10}, prepare=both, route=cr),
        C("pa rule create no approver", "POST", RULES, body={"threshold": 10}, prepare=none, route=cr),
        C("pa rule create analyst denied", "POST", RULES, who="analyst", body={"threshold": 10}, prepare=both,
          route=cr),
        C("pa rule create viewer denied", "POST", RULES, who="viewer", body={"threshold": 10}, prepare=both,
          route=cr),
        C("pa rule create scoped admin refused", "POST", RULES, who="scoped_admin", body={"threshold": 10},
          prepare=both, route=cr),
        C("pa rule create write key", "POST", RULES, who="key_write", body={"threshold": 10}, route=cr),
        C("pa rule create no auth", "POST", RULES, who="none", body={"threshold": 10}, route=cr),
        C("pa rule create limit not above threshold", "POST", RULES, body={"threshold": 10, "self_approve_below": 10},
          prepare=both, route=cr),
        C("pa rule create unknown supplier", "POST", RULES, body={"threshold": 10, "supplier_id": "sup-nowhere"},
          prepare=both, route=cr),
        C("pa rule create unknown warehouse is kept", "POST", RULES, body={"threshold": 10, "warehouse": " Sur\u200b "},
          prepare=both, route=cr),
        C("pa rule create warehouse only invisible", "POST", RULES, body={"threshold": 10, "warehouse": "\u200b\u202e"},
          prepare=both, route=cr),
        C("pa rule create blank warehouse and supplier", "POST", RULES,
          body={"threshold": 10, "warehouse": "   ", "supplier_id": " "}, prepare=both, route=cr),
        C("pa rule create nulls", "POST", RULES,
          body={"threshold": 10, "warehouse": None, "supplier_id": None, "self_approve_below": None},
          prepare=both, route=cr),
        C("pa rule create zero threshold", "POST", RULES, body={"threshold": 0}, route=cr),
        C("pa rule create huge threshold", "POST", RULES, body={"threshold": 1e12 * 2, "self_approve_below": -1},
          route=cr),
        C("pa rule create max threshold", "POST", RULES, body={"threshold": 1e12}, prepare=both, route=cr),
        C("pa rule create text threshold", "POST", RULES, body={"threshold": "12.5"}, prepare=both, route=cr),
        C("pa rule create wrong types", "POST", RULES,
          body={"threshold": "abc", "warehouse": 5, "supplier_id": [], "self_approve_below": {}}, route=cr),
        C("pa rule create long names", "POST", RULES,
          body={"threshold": 5, "warehouse": "w" * 121, "supplier_id": "s" * 65}, route=cr),
        C("pa rule create missing threshold", "POST", RULES, body={"warehouse": "Norte"}, route=cr),
        C("pa rule create no body", "POST", RULES, route=cr),
        C("pa rule create invalid json", "POST", RULES, raw_body=b"{nope", route=cr),
        # ── rules: update and delete ───────────────────────────────────────
        C("pa rule patch threshold", "PATCH", f"{RULES}/{{rule}}", body={"threshold": 200},
          prepare=pa_state([{"threshold": 100, "warehouse": "Norte", "supplier": True}]), route=pa),
        C("pa rule patch clears with null", "PATCH", f"{RULES}/{{rule}}",
          body={"warehouse": None, "supplier_id": None, "self_approve_below": None},
          prepare=pa_state([{"threshold": 100, "warehouse": "Norte", "supplier": True, "self_approve_below": 500}]),
          route=pa),
        C("pa rule patch explicit null threshold", "PATCH", f"{RULES}/{{rule}}", body={"threshold": None},
          prepare=both, route=pa),
        C("pa rule patch deactivate", "PATCH", f"{RULES}/{{rule}}", body={"active": False}, prepare=both, route=pa),
        C("pa rule patch active null keeps", "PATCH", f"{RULES}/{{rule}}", body={"active": None}, prepare=r_off and
          pa_state(r_off), route=pa),
        C("pa rule patch reactivate", "PATCH", f"{RULES}/{{rule}}", body={"active": "yes"}, prepare=pa_state(r_off),
          route=pa),
        C("pa rule patch reactivate no approver", "PATCH", f"{RULES}/{{rule}}", body={"active": True},
          prepare=pa_state(r_off, ()), route=pa),
        C("pa rule patch empty body", "PATCH", f"{RULES}/{{rule}}", body={}, prepare=both, route=pa),
        C("pa rule patch limit below the threshold", "PATCH", f"{RULES}/{{rule}}",
          body={"threshold": 900}, prepare=pa_state([{"threshold": 100, "self_approve_below": 500}]), route=pa),
        C("pa rule patch warehouse spelling", "PATCH", f"{RULES}/{{rule}}", body={"warehouse": "NORTE"},
          prepare=both, route=pa),
        C("pa rule patch unknown supplier", "PATCH", f"{RULES}/{{rule}}", body={"supplier_id": "sup-nowhere"},
          prepare=both, route=pa),
        C("pa rule patch not found", "PATCH", f"{RULES}/rule-nowhere", body={"threshold": 5}, prepare=both, route=pa),
        C("pa rule patch validation before not found", "PATCH", f"{RULES}/rule-nowhere", body={"threshold": -1},
          route=pa),
        C("pa rule patch analyst denied", "PATCH", f"{RULES}/{{rule}}", who="analyst", body={"threshold": 5},
          prepare=both, route=pa),
        C("pa rule patch scoped admin refused", "PATCH", f"{RULES}/{{rule}}", who="scoped_admin",
          body={"threshold": 5}, prepare=both, route=pa),
        C("pa rule patch no body", "PATCH", f"{RULES}/{{rule}}", prepare=both, route=pa),
        C("pa rule patch wrong types", "PATCH", f"{RULES}/{{rule}}", body={"active": "maybe", "threshold": []},
          prepare=both, route=pa),
        C("pa rule delete", "DELETE", f"{RULES}/{{rule}}", prepare=pa_state(r_mixed), route=de),
        C("pa rule delete not found", "DELETE", f"{RULES}/rule-nowhere", prepare=both, route=de),
        C("pa rule delete analyst denied", "DELETE", f"{RULES}/{{rule}}", who="analyst", prepare=both, route=de),
        C("pa rule delete scoped admin refused", "DELETE", f"{RULES}/{{rule}}", who="scoped_admin", prepare=both,
          route=de),
        C("pa rule delete no auth", "DELETE", f"{RULES}/{{rule}}", who="none", route=de),
        # ── approvers ──────────────────────────────────────────────────────
        C("pa approver set", "PUT", f"{APPR}/{env.fx.analyst_id}", body={"can_approve": True},
          prepare=pa_state([], ("admin",)), route=ap),
        C("pa approver lax bool", "PUT", f"{APPR}/{env.fx.analyst_id}", body={"can_approve": "yes"},
          prepare=pa_state([], ("admin",)), route=ap),
        C("pa approver unset", "PUT", f"{APPR}/{env.fx.analyst_id}", body={"can_approve": False},
          prepare=both, route=ap),
        C("pa approver last one refused", "PUT", f"{APPR}/{env.fx.admin_id}", body={"can_approve": False},
          prepare=pa_state(r100, ("admin",)), route=ap),
        C("pa approver last one fine without rules", "PUT", f"{APPR}/{env.fx.admin_id}",
          body={"can_approve": False}, prepare=pa_state([], ("admin",)), route=ap),
        C("pa approver inactive rule does not hold", "PUT", f"{APPR}/{env.fx.admin_id}",
          body={"can_approve": False}, prepare=pa_state(r_off, ("admin",)), route=ap),
        C("pa approver viewer cannot be one", "PUT", f"{APPR}/{env.fx.viewer_id}", body={"can_approve": True},
          prepare=both, route=ap),
        C("pa approver viewer can be removed", "PUT", f"{APPR}/{env.fx.viewer_id}", body={"can_approve": False},
          prepare=both, route=ap),
        C("pa approver unknown user", "PUT", f"{APPR}/usr-nowhere", body={"can_approve": True}, prepare=both,
          route=ap),
        C("pa approver analyst denied", "PUT", f"{APPR}/{env.fx.analyst_id}", who="analyst",
          body={"can_approve": True}, prepare=both, route=ap),
        C("pa approver scoped admin refused", "PUT", f"{APPR}/{env.fx.analyst_id}", who="scoped_admin",
          body={"can_approve": True}, prepare=both, route=ap),
        C("pa approver missing flag", "PUT", f"{APPR}/{env.fx.analyst_id}", body={}, prepare=both, route=ap),
        C("pa approver null flag", "PUT", f"{APPR}/{env.fx.analyst_id}", body={"can_approve": None},
          prepare=both, route=ap),
        C("pa approver junk flag", "PUT", f"{APPR}/{env.fx.analyst_id}", body={"can_approve": "perhaps"},
          prepare=both, route=ap),
        C("pa approver no body", "PUT", f"{APPR}/{env.fx.analyst_id}", prepare=both, route=ap),
        C("pa approver no auth", "PUT", f"{APPR}/{env.fx.analyst_id}", who="none", body={"can_approve": True},
          route=ap),
        # ── the approver's inbox ───────────────────────────────────────────
        C("pa pending as approver", "GET", PEND, mask_all=True, po=("pa-pend-1", open_analyst), prepare=both, route=pe),
        C("pa pending shows own request too", "GET", PEND, mask_all=True, po=("pa-pend-2", open_admin), prepare=pa_state(r_self),
          route=pe),
        C("pa pending self approval allowed above limit", "GET", PEND, mask_all=True, po=("pa-pend-2", open_admin),
          prepare=pa_state([{"threshold": 100, "self_approve_below": 800}]), route=pe),
        C("pa pending not an approver", "GET", PEND, mask_all=True, who="viewer", prepare=both, route=pe),
        C("pa pending approver without rules", "GET", PEND, mask_all=True, prepare=pa_state([], ("admin",)), route=pe),
        C("pa pending scoped approver", "GET", PEND, mask_all=True, who="scoped", po=("pa-pend-norte", {**norte_big,
          "approval": asked("admin")}), prepare=pa_state(r100, ("admin", "scoped")), route=pe),
        C("pa pending scoped does not see the default warehouse", "GET", PEND, mask_all=True, who="scoped",
          prepare=pa_state(r100, ("admin", "scoped")), route=pe),
        C("pa pending no auth", "GET", PEND, mask_all=True, who="none", route=pe),
        C("pa pending key refused", "GET", PEND, mask_all=True, who="key_write", route=pe),
        # ── one order ──────────────────────────────────────────────────────
        C("pa get no rules", "GET", po, po=("pa-get-none", small), prepare=none, route=ga),
        C("pa get below every threshold", "GET", po, po=("pa-get-small", small), prepare=both, route=ga),
        C("pa get unpriced", "GET", po, po=("pa-get-unpriced", unpriced), prepare=both, route=ga),
        C("pa get needs approval", "GET", po, po=("pa-get-big", big), prepare=both, route=ga),
        C("pa get strictest rule wins", "GET", po, po=("pa-get-big", big), prepare=pa_state(r_mixed), route=ga),
        C("pa get supplier rule", "GET", po, po=("pa-get-big", big),
          prepare=pa_state([{"threshold": 100, "supplier": True, "self_approve_below": 2000}]), route=ga),
        C("pa get warehouse rule other warehouse", "GET", po, po=("pa-get-big", big),
          prepare=pa_state([{"threshold": 100, "warehouse": "Sur"}]), route=ga),
        C("pa get inactive rule", "GET", po, po=("pa-get-big", big), prepare=pa_state(r_off), route=ga),
        C("pa get pending as approver", "GET", po, po=("pa-get-open", open_analyst), prepare=both, route=ga),
        C("pa get pending as the requester", "GET", po, who="analyst", po=("pa-get-open", open_analyst),
          prepare=pa_state(r100, ("analyst",)), route=ga),
        C("pa get pending viewer", "GET", po, who="viewer", po=("pa-get-open", open_analyst), prepare=both,
          route=ga),
        C("pa get own request below the self limit", "GET", po, po=("pa-get-own", open_admin),
          prepare=pa_state(r_self), route=ga),
        C("pa get own request above the self limit", "GET", po, po=("pa-get-own", open_admin),
          prepare=pa_state([{"threshold": 100, "self_approve_below": 500}]), route=ga),
        C("pa get approved", "GET", po, po=("pa-get-approved", approved), prepare=both, route=ga),
        C("pa get approved then grown", "GET", po, po=("pa-get-grown", grown), prepare=both, route=ga),
        C("pa get rejected", "GET", po, po=("pa-get-rejected", rejected), prepare=both, route=ga),
        C("pa get scoped default warehouse refused", "GET", po, who="scoped", po=("pa-get-big", big),
          prepare=both, route=ga),
        C("pa get scoped own warehouse", "GET", po, who="scoped", po=("pa-get-norte", norte_big), prepare=both,
          route=ga),
        C("pa get not found", "GET", f"{A}/po/nope-123/approval", prepare=both, route=ga),
        C("pa get other tenant's po", "GET", f"{A}/po/{env.other_po}/approval", prepare=both, route=ga),
        C("pa get no auth", "GET", po, who="none", po=("pa-get-big", big), route=ga),
        C("pa get key refused", "GET", po, who="key_read", po=("pa-get-big", big), route=ga),
        # ── decisions ──────────────────────────────────────────────────────
        C("pa approve by admin", "POST", f"{po}/approve", po=("pa-dec-1", open_analyst), prepare=both, route=aa),
        C("pa approve again is idempotent", "POST", f"{po}/approve", po=("pa-dec-1", open_analyst),
          prepare=both, route=aa),
        C("pa reject after approve refused", "POST", f"{po}/reject", po=("pa-dec-1", open_analyst),
          body={"comment": "changed my mind"}, prepare=both, route=rj),
        C("pa reject by admin", "POST", f"{po}/reject", po=("pa-dec-2", open_analyst),
          body={"comment": "  too expensive  "}, prepare=both, route=rj),
        C("pa reject again is idempotent", "POST", f"{po}/reject", po=("pa-dec-2", open_analyst),
          body={"comment": "whatever"}, prepare=both, route=rj),
        C("pa approve after reject refused", "POST", f"{po}/approve", po=("pa-dec-2", open_analyst),
          prepare=both, route=aa),
        C("pa approve with comment", "POST", f"{po}/approve", po=("pa-dec-3", open_analyst),
          body={"comment": " go ahead "}, prepare=both, route=aa),
        C("pa approve with a null body", "POST", f"{po}/approve", po=("pa-dec-4", open_analyst),
          raw_body=b"null", prepare=both, route=aa),
        C("pa approve own request refused", "POST", f"{po}/approve", po=("pa-dec-own", open_admin),
          prepare=pa_state(r100), route=aa),
        C("pa approve own request below the self limit", "POST", f"{po}/approve", po=("pa-dec-own-ok", open_admin),
          prepare=pa_state([{"threshold": 100, "self_approve_below": 5000}]), route=aa),
        C("pa reject own request is allowed", "POST", f"{po}/reject", po=("pa-dec-own-rej", open_admin),
          body={"comment": "my mistake"}, prepare=pa_state(r100), route=rj),
        C("pa approve not an approver", "POST", f"{po}/approve", who="analyst", po=("pa-dec-5", open_admin),
          prepare=pa_state(r100, ("admin",)), route=aa),
        C("pa approve nobody asked", "POST", f"{po}/approve", po=("pa-dec-none", big), prepare=both, route=aa),
        C("pa reject nobody asked", "POST", f"{po}/reject", po=("pa-dec-none", big), body={"comment": "nope"},
          prepare=both, route=rj),
        C("pa reject without a comment", "POST", f"{po}/reject", po=("pa-dec-6", open_analyst),
          body={"comment": "   "}, prepare=both, route=rj),
        C("pa reject comment too short", "POST", f"{po}/reject", po=("pa-dec-6", open_analyst),
          body={"comment": "no"}, prepare=both, route=rj),
        C("pa reject comment missing", "POST", f"{po}/reject", po=("pa-dec-6", open_analyst), body={},
          prepare=both, route=rj),
        C("pa reject no body", "POST", f"{po}/reject", po=("pa-dec-6", open_analyst), prepare=both, route=rj),
        C("pa reject body is a list", "POST", f"{po}/reject", po=("pa-dec-6", open_analyst), body=["x"],
          prepare=both, route=rj),
        C("pa approve comment too long", "POST", f"{po}/approve", po=("pa-dec-6", open_analyst),
          body={"comment": "c" * 501}, prepare=both, route=aa),
        C("pa approve comment wrong type", "POST", f"{po}/approve", po=("pa-dec-6", open_analyst),
          body={"comment": 5}, prepare=both, route=aa),
        C("pa approve text body", "POST", f"{po}/approve", po=("pa-dec-6", open_analyst), raw_body=b"stop it",
          content_type="text/plain", prepare=both, route=aa),
        C("pa approve invalid json", "POST", f"{po}/approve", po=("pa-dec-6", open_analyst), raw_body=b"{nope",
          prepare=both, route=aa),
        C("pa approve viewer denied", "POST", f"{po}/approve", who="viewer", po=("pa-dec-7", open_analyst),
          prepare=both, route=aa),
        C("pa approve no auth", "POST", f"{po}/approve", who="none", po=("pa-dec-7", open_analyst), route=aa),
        C("pa approve key refused", "POST", f"{po}/approve", who="key_write", po=("pa-dec-7", open_analyst),
          route=aa),
        C("pa approve scoped default warehouse refused", "POST", f"{po}/approve", who="scoped",
          po=("pa-dec-7", open_analyst), prepare=pa_state(r100, ("admin", "scoped")), route=aa),
        C("pa approve scoped own warehouse", "POST", f"{po}/approve", who="scoped",
          po=("pa-dec-norte", {**norte_big, "approval": asked("admin")}),
          prepare=pa_state(r100, ("admin", "scoped")), route=aa),
        C("pa approve not found", "POST", f"{A}/po/nope-123/approval/approve", prepare=both, route=aa),
        C("pa reject other tenant's po", "POST", f"{A}/po/{env.other_po}/approval/reject",
          body={"comment": "nope nope"}, prepare=both, route=rj),
        C("pa approve then the order is approved", "GET", po, po=("pa-dec-1", open_analyst), prepare=both, route=ga),
        C("pa approve rule dropped afterwards", "POST", f"{po}/approve", po=("pa-dec-8", open_analyst),
          prepare=pa_state([]), route=aa),
        C("pa approve when the order grew", "POST", f"{po}/approve", po=("pa-dec-9", {**big, "approval":
          asked("analyst", amount=400.0)}), prepare=both, route=aa),
    ]
    return cases


def run_r4(args, fx: Fixture, db) -> list:
    """The R4 section; returns (case, verdict, problems) like the main loop."""
    if db is None:
        print("R4 cases skipped: they need --db (the fixtures are database rows)")
        return []
    env = r4_setup(args, fx, db)
    results = []
    try:
        for case in build_r4_cases(env):
            if args.only and args.only not in case.name:
                continue
            token = auth_for(fx, case.who)
            if case.who.startswith("key_") and token is None:
                results.append((case, "SKIP", ["no API key in this tenant"]))
                continue
            resps, caps, masked = {}, {}, {}
            for side, base in (("py", args.python), ("rs", args.rust)):
                po_id = _r4_pair(env, *case.po)[side] if case.po else None
                if case.prepare:
                    case.prepare(env, side)
                since = _r4_since(db)
                path = case.path.replace("{po}", po_id or "")
                if "{rule" in path:
                    cur = db.cursor()
                    cur.execute("SELECT id FROM po_approval_rules WHERE tenant_id = %s "
                                "ORDER BY threshold, created_at", (fx.tenant_id,))
                    rule_ids = [r[0] for r in cur.fetchall()] + ["rule-nowhere"] * 2
                    path = path.replace("{rule}", rule_ids[0]).replace("{rule2}", rule_ids[1])
                r = http(base, case.method, path, token=token, body=case.body,
                         raw_body=case.raw_body, content_type=case.content_type)
                resps[side] = r
                masked[side] = _r4_mask(r.body, _r4_subs(env, po_id, all_pos=case.mask_all))
                if case.capture:
                    caps[side] = case.capture(env, side, po_id, since)
            rp, rr = resps["py"], resps["rs"]
            problems = []
            if rp.status != rr.status:
                problems.append(f"status python={rp.status} rust={rr.status}")
            volatile = {"loc", "ctx"} if case.raw_body == b"{nope" else set()
            problems += diff(normalize(masked["py"], volatile), normalize(masked["rs"], volatile))
            for h in ("www-authenticate", "retry-after"):
                if rp.headers.get(h) != rr.headers.get(h):
                    problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")
            if case.capture:
                problems += [f"state {p}" for p in diff(caps["py"], caps["rs"])]
            if args.dump:
                print(f"\n--- {case.name}\nPY {rp.status} {json.dumps(rp.body)[:1500]}"
                      f"\nRS {rr.status} {json.dumps(rr.body)[:1500]}")
                if case.capture:
                    print(f"STATE PY {json.dumps(caps['py'], default=str)[:1500]}"
                          f"\nSTATE RS {json.dumps(caps['rs'], default=str)[:1500]}")
            results.append((case, "FAIL" if problems else "PASS", problems))
        results += pa_mail_verdicts(env, args)
        # The other tenant's order must be exactly as it was seeded.
        cur = db.cursor()
        cur.execute("""SELECT paid_at, cancelled_at FROM inventory_po_log WHERE id = %s""",
                    (env.other_po,))
        untouched = cur.fetchone() == (None, None)
        results.append((R4Case("other tenant's po untouched", "-", "-", route="(tenant scope)"),
                        "PASS" if untouched else "FAIL",
                        [] if untouched else ["another tenant's PO was written"]))
    finally:
        if env.other_tenant is not None and not args.keep:
            erase_fixture(args.python, env.other_tenant)
    return results


# ── Webhook deliveries queued by a migrated write ───────────────────────────
#
# One hook per throwaway tenant, subscribed to the events a migrated route
# emits. Its URL is on a reserved `.invalid` name: the Python delivery loop
# may try it, and the name never resolves, so nothing leaves the machine.

HOOK_EVENTS = ["purchase_order.cancelled", "commitment.fulfilled", "purchase_order.approved",
               "purchase_order.rejected"]


def ensure_contract_hook(fx: Fixture, db) -> None:
    if getattr(fx, "hook_id", None):
        return
    cur = db.cursor()
    cur.execute("""INSERT INTO webhooks (id, tenant_id, url, events, secret, created_by)
                   VALUES (gen_random_uuid()::text, %s, 'https://contract-harness.invalid/hook', %s, %s, %s)
                   RETURNING id""", (fx.tenant_id, HOOK_EVENTS, secrets.token_hex(32), fx.admin_id))
    fx.hook_id = cur.fetchone()[0]


# Keys of `data` that name the per-side object or the moment: masked.
HOOK_SIDE_KEYS = {"po_log_id", "po_number", "commitment_id", "cancelled_at", "fulfilled_at"}


def hook_deliveries(db, tenant_id: str, id_key: str, obj_id: str, since) -> list:
    """Deliveries queued since `since` whose data names `obj_id`: type,
    is_test, envelope keys and data (side-specific values masked)."""
    cur = db.cursor()
    cur.execute("""SELECT event_type, is_test, payload FROM webhook_deliveries
                    WHERE tenant_id = %s AND created_at >= %s
                      AND (payload::jsonb -> 'data' ->> %s) = %s
                    ORDER BY created_at""", (tenant_id, since, id_key, obj_id))
    out = []
    for event_type, is_test, payload in cur.fetchall():
        env = json.loads(payload)
        data = {k: ("<side>" if k in HOOK_SIDE_KEYS and v is not None else v)
                for k, v in env.get("data", {}).items()}
        out.append({"event_type": event_type, "is_test": is_test, "envelope_keys": list(env),
                    "type": env.get("type"), "api_version": env.get("api_version"),
                    "tenant_is_ours": env.get("tenant_id") == tenant_id,
                    "id_shape": bool(str(env.get("id", "")).startswith("evt_")
                                     and len(env.get("id", "")) == 36),
                    "compact": "\": " not in payload and "\", " not in payload,
                    "data_keys": list(env.get("data", {})), "data": data})
    return out


def with_hook_deliveries(inner: Callable, id_key: str) -> Callable:
    def capture(env, side, obj_id, since):
        return {"state": inner(env, side, obj_id, since),
                "webhooks": hook_deliveries(env.db, env.fx.tenant_id, id_key, obj_id, since)}
    return capture


# ── Committed demand: main's warehouse-scope and contract rules ─────────────
#
# Each side acts on its own commitment (created through its own service), so
# a write on one side never changes what the other starts from. Contract rows
# are made by setting the contract columns directly: materialising one goes
# through `supply_contract_service`, which no migrated route calls.

CD_VOLATILE = {"id", "created_at", "updated_at", "status_changed_at", "contract_withdrawn_at"}


def run_cd_resync(args, fx: Fixture, db) -> list:
    if db is None:
        return [(Case("cd resync (all)", "-", "-", route="(committed demand resync)"), "SKIP",
                 ["needs --db: the rows are seeded and checked in the database"])]
    out: list = []
    cur = db.cursor()
    ensure_contract_hook(fx, db)
    tag = secrets.token_hex(3)
    wh = {}
    for name in ("CD-Norte", "CD-Sur"):
        cur.execute("""INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s)
                       ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                    (fx.tenant_id, name))
        wh[name] = cur.fetchone()[0]
    r = http(args.python, "POST", f"{API}/users", token=auth_for(fx, "admin"), body={
        "email": f"contract-{tag}-cdscoped@stockai.demo", "role": "analyst", "full_name": "Contract cd scoped"})
    if r.status != 201:
        return [(Case("cd resync setup", "-", "-", route="(committed demand resync)"), "FAIL",
                 [f"creating the scoped analyst failed: {r.status} {r.body}"])]
    uid = r.body["data"]["user"]["id"]
    r = http(args.python, "PUT", f"{API}/users/{uid}/warehouse-scope", token=auth_for(fx, "admin"),
             body={"warehouse_ids": [wh["CD-Norte"]]})
    if r.status != 200:
        return [(Case("cd resync setup", "-", "-", route="(committed demand resync)"), "FAIL",
                 [f"scoping the analyst failed: {r.status} {r.body}"])]
    fx.tokens["cd_scoped"] = mint_access_token(fx.secret, uid, fx.tenant_id, "analyst")
    future = today_plus(45)

    def seed(base, **extra):
        body = {"sku": f"CT-RS-{tag}", "delivery_date": future, "quantity": 10, "customer": "Delta",
                "probability": 0.7, **extra}
        rr = http(base, "POST", f"{API}/committed-demand", token=auth_for(fx, "admin"), body=body)
        if rr.status != 201:
            raise SystemExit(f"seeding a commitment on {base} failed: {rr.status} {rr.body}")
        return rr.body["data"]["id"]

    def row(cid):
        cur.execute("""SELECT sku, warehouse_id, quantity::float8, customer, probability::float8, status,
                              status_changed_by, source, contract_root_id, contract_withdrawn_at IS NOT NULL
                         FROM committed_demand WHERE id = %s""", (cid,))
        return cur.fetchone()

    def since():
        cur.execute("SELECT clock_timestamp()")
        return cur.fetchone()[0]

    def pair(name, route, method, path, ids=None, *, who="admin", body=None, state=False, hooks=False):
        """`path` may hold "{id}", replaced by each side's own commitment."""
        resps, states = {}, {}
        for side, base in (("py", args.python), ("rs", args.rust)):
            oid = ids[side] if ids else None
            t0 = since()
            rr = http(base, method, path.replace("{id}", oid or ""), token=auth_for(fx, who), body=body)
            resps[side] = rr
            st = {}
            if state and oid:
                st["row"] = list(row(oid))
            if hooks and oid:
                st["webhooks"] = hook_deliveries(db, fx.tenant_id, "commitment_id", oid, t0)
            states[side] = st
        rp, rr = resps["py"], resps["rs"]
        problems = []
        if rp.status != rr.status:
            problems.append(f"status python={rp.status} rust={rr.status}")
        problems += diff(normalize(_cd_mask(rp.body, "py"), CD_VOLATILE),
                         normalize(_cd_mask(rr.body, "rs"), CD_VOLATILE))
        problems += [f"state {p}" for p in diff(_cd_mask(states["py"], "py"), _cd_mask(states["rs"], "rs"))]
        if args.dump:
            print(f"\n--- {name}\nPY {rp.status} {json.dumps(rp.body)[:1500]}\nRS {rr.status} "
                  f"{json.dumps(rr.body)[:1500]}\nSTATE {json.dumps(states, default=str)[:1500]}")
        out.append((Case(name, method, path, who=who, route=route), "FAIL" if problems else "PASS", problems))
        return rp, rr

    # The release key (tenant, root, sku, release date) is unique, so each
    # side's contract row has its own root id, masked in every comparison.
    roots = {side: f"root-{tag}-{side}" for side in ("py", "rs")}

    def _cd_mask(value, side):
        if isinstance(value, dict):
            return {k: _cd_mask(v, side) for k, v in value.items()}
        if isinstance(value, list):
            return [_cd_mask(v, side) for v in value]
        return "<root>" if value == roots[side] else value

    C, B, P, S = ("POST /committed-demand", "POST /committed-demand/bulk",
                  "PATCH /committed-demand/{id}", "POST /committed-demand/{id}/status")
    cd = f"{API}/committed-demand"
    try:
        # ── warehouse scope ────────────────────────────────────────────────
        base = {"sku": f"CT-SC-{tag}", "delivery_date": future, "quantity": 2}
        pair("cdx scoped create without warehouse", C, "POST", cd, who="cd_scoped", body=base)
        pair("cdx scoped create blank warehouse", C, "POST", cd, who="cd_scoped",
             body={**base, "warehouse_id": "   "})
        pair("cdx scoped create other warehouse", C, "POST", cd, who="cd_scoped",
             body={**base, "warehouse_id": wh["CD-Sur"]})
        pair("cdx scoped create unknown warehouse", C, "POST", cd, who="cd_scoped",
             body={**base, "warehouse_id": "wh-nowhere"})
        rp, rr = pair("cdx scoped create own warehouse", C, "POST", cd, who="cd_scoped",
                      body={**base, "warehouse_id": wh["CD-Norte"]})
        own = {"py": (rp.body or {}).get("data", {}).get("id"), "rs": (rr.body or {}).get("data", {}).get("id")}
        pair("cdx scoped bulk with a company-wide row", B, "POST", f"{cd}/bulk", who="cd_scoped",
             body={"rows": [{**base, "warehouse_id": wh["CD-Norte"]}, base]})
        company = {"py": seed(args.python), "rs": seed(args.rust)}
        pair("cdx scoped patch company-wide row", P, "PATCH", f"{cd}/{{id}}", company, who="cd_scoped",
             body={"quantity": 3}, state=True)
        pair("cdx scoped status company-wide row", S, "POST", f"{cd}/{{id}}/status", company,
             who="cd_scoped", body={"status": "cancelled"}, state=True)
        if own["py"] and own["rs"]:
            pair("cdx scoped move own row out of scope", P, "PATCH", f"{cd}/{{id}}", own, who="cd_scoped",
                 body={"warehouse_id": wh["CD-Sur"]}, state=True)
            pair("cdx scoped edit own row", P, "PATCH", f"{cd}/{{id}}", own, who="cd_scoped",
                 body={"quantity": 4}, state=True)
        # ── contract-materialised rows ─────────────────────────────────────
        contract = {"py": seed(args.python), "rs": seed(args.rust)}
        for side in ("py", "rs"):
            cur.execute("""UPDATE committed_demand SET source = 'contract', contract_id = %s,
                                  contract_root_id = %s, contract_release_date = %s
                            WHERE id = %s""",
                        (f"sc-{tag}", roots[side], future, contract[side]))
        pair("cdx contract patch locked sku", P, "PATCH", f"{cd}/{{id}}", contract,
             body={"sku": "OTHER", "quantity": 5}, state=True)
        pair("cdx contract patch locked warehouse", P, "PATCH", f"{cd}/{{id}}", contract,
             body={"warehouse_id": wh["CD-Norte"]}, state=True)
        pair("cdx contract patch same values allowed", P, "PATCH", f"{cd}/{{id}}", contract,
             body={"customer": "Delta", "probability": 0.7, "warehouse_id": None, "quantity": 6}, state=True)
        pair("cdx contract patch null customer locked", P, "PATCH", f"{cd}/{{id}}", contract,
             body={"customer": None}, state=True)
        pair("cdx contract fulfilled emits", S, "POST", f"{cd}/{{id}}/status", contract,
             body={"status": "fulfilled"}, state=True, hooks=True)
        pair("cdx contract fulfilled again emits nothing", S, "POST", f"{cd}/{{id}}/status", contract,
             body={"status": "fulfilled"}, state=True, hooks=True)
        pair("cdx contract reopen without a live contract", S, "POST", f"{cd}/{{id}}/status", contract,
             body={"status": "open"}, state=True)
        for side in ("py", "rs"):
            cur.execute("UPDATE committed_demand SET contract_withdrawn_at = NOW() WHERE id = %s",
                        (contract[side],))
        pair("cdx withdrawn status refused", S, "POST", f"{cd}/{{id}}/status", contract,
             body={"status": "cancelled"}, state=True)
        manual = {"py": seed(args.python, warehouse_id=wh["CD-Norte"]),
                  "rs": seed(args.rust, warehouse_id=wh["CD-Norte"])}
        pair("cdx manual fulfilled emits with warehouse", S, "POST", f"{cd}/{{id}}/status", manual,
             body={"status": "fulfilled"}, state=True, hooks=True)
    finally:
        cur.execute("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (uid,))
    return out


# ── Approval delegation: Rust-only routes, honoured by Python ────────────────
#
# The three delegation routes exist ONLY in Rust, so there is nothing to diff.
# What the harness pins instead is the cross-service contract: rows written by
# Rust are honoured by Python's approve / reject (still Python), a revoked or
# expired delegation is refused there, and every refusal leaves the database
# as it was. Needs --db (the people and the order are database rows).

DELEGATIONS = f"{API}/inventory/po-approval/delegations"


def _dg_count(db, tenant_id: str) -> int:
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM po_approval_delegations WHERE tenant_id = %s", (tenant_id,))
    return cur.fetchone()[0]


def run_delegation(args, fx: Fixture, db) -> list:
    if db is None:
        print("delegation cases skipped: they need --db")
        return []
    results = []
    cur = db.cursor()

    def record(name: str, problems: list) -> None:
        results.append((R4Case(name, "-", "-", route="po-approval delegations"),
                        "FAIL" if problems else "PASS", problems))

    def expect(name: str, r: Resp, status: int, code: Optional[str] = None, extra=None) -> None:
        problems = []
        if r.status != status:
            problems.append(f"status {r.status} (wanted {status}): {json.dumps(r.body)[:300]}")
        elif code and (r.body or {}).get("error_code") != code:
            problems.append(f"error_code {(r.body or {}).get('error_code')!r} (wanted {code!r})")
        if extra and not problems:
            problems += extra()
        record(name, problems)

    admin, analyst, viewer = (auth_for(fx, w) for w in ("admin", "analyst", "viewer"))
    # A second analyst, the substitute (the fixture's own analyst is the approver).
    tag = secrets.token_hex(4)
    r = http(args.python, "POST", f"{API}/users", token=admin, body={
        "email": f"contract-{tag}-sub@stockai.demo", "role": "analyst", "full_name": "Contract substitute"})
    if r.status != 201:
        record("setup: create the substitute", [f"{r.status} {r.body}"])
        return results
    sub_id = r.body["data"]["user"]["id"]
    sub = mint_access_token(fx.secret, sub_id, fx.tenant_id, "analyst")

    # The workflow: the analyst may approve, a rule needs approval above 500,
    # three orders worth 1000 asked for by the admin.
    # Invited people have no password yet, so their status is not 'active'.
    cur.execute("UPDATE users SET status = 'active' WHERE id IN (%s, %s, %s)",
                (fx.analyst_id, sub_id, fx.viewer_id))
    cur.execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (fx.analyst_id,))
    cur.execute("""INSERT INTO po_approval_rules (tenant_id, threshold, created_by)
                   VALUES (%s, 500, %s)""", (fx.tenant_id, fx.admin_id))
    pos = []
    for n in range(3):
        po = _r4_seed_po(db, fx.tenant_id, fx.admin_id, f"DG-{tag}-{n}", {"sent": False, "qty": 10})
        cur.execute("UPDATE inventory_po_items SET unit_cost = 100 WHERE po_log_id = %s", (po,))
        pos.append(po)
        rr = http(args.python, "POST", f"{API}/inventory/po/{po}/approval/request", token=admin, body={})
        if rr.status != 200:
            record("setup: request approval", [f"{rr.status} {rr.body}"])
            return results

    start, end = today_plus(0), today_plus(3)
    good = {"delegate_id": sub_id, "starts_on": start, "ends_on": end, "note": "holiday"}
    before = _dg_count(db, fx.tenant_id)

    expect("viewer cannot delegate", http(args.rust, "POST", DELEGATIONS, token=viewer, body=good),
           403, "role_not_permitted")
    expect("a non-approver cannot delegate", http(args.rust, "POST", DELEGATIONS, token=admin, body=good),
           403, "po_delegation_not_approver")
    expect("never to yourself", http(args.rust, "POST", DELEGATIONS, token=analyst,
           body={**good, "delegate_id": fx.analyst_id}), 422, "po_delegation_self")
    expect("never to a viewer", http(args.rust, "POST", DELEGATIONS, token=analyst,
           body={**good, "delegate_id": fx.viewer_id}), 409, "po_delegation_delegate_role")
    expect("dates backwards", http(args.rust, "POST", DELEGATIONS, token=analyst,
           body={**good, "starts_on": end, "ends_on": start}), 422, "po_delegation_dates_invalid")
    expect("already over", http(args.rust, "POST", DELEGATIONS, token=analyst,
           body={**good, "starts_on": today_plus(-9), "ends_on": today_plus(-2)}), 422, "po_delegation_dates_invalid")
    expect("unknown delegate", http(args.rust, "POST", DELEGATIONS, token=analyst,
           body={**good, "delegate_id": "no-such-user"}), 404, "user_not_found")
    expect("missing fields are a 422", http(args.rust, "POST", DELEGATIONS, token=analyst, body={}),
           422, "validation_error")
    record("refused creates wrote nothing",
           [] if _dg_count(db, fx.tenant_id) == before else ["a refused create wrote a row"])
    if fx.write_key:
        expect("an API key never reaches it", http(args.rust, "POST", DELEGATIONS,
               token=fx.write_key, body=good), 403, "api_key_route_not_exposed")

    r = http(args.rust, "POST", DELEGATIONS, token=analyst, body=good)

    def created_ok():
        cur.execute("""SELECT delegator_id, delegate_id, note, created_by, revoked_at
                         FROM po_approval_delegations WHERE tenant_id = %s""", (fx.tenant_id,))
        rows = cur.fetchall()
        problems = []
        if rows != [(fx.analyst_id, sub_id, "holiday", fx.analyst_id, None)]:
            problems.append(f"stored row {rows}")
        cur.execute("""SELECT user_id, context FROM activity_logs
                        WHERE tenant_id = %s AND action = 'approval_delegation.created'""", (fx.tenant_id,))
        ev = cur.fetchall()
        if len(ev) != 1 or ev[0][0] != fx.analyst_id or ev[0][1].get("delegate") != "Contract substitute":
            problems.append(f"activity {ev}")
        if (r.body["data"] or {}).get("status") != "active":
            problems.append(f"status {(r.body['data'] or {}).get('status')}")
        return problems
    expect("an approver delegates to a colleague", r, 201, extra=created_ok)
    did = (r.body or {}).get("data", {}).get("id") if r.status == 201 else None
    expect("an overlapping delegation is refused", http(args.rust, "POST", DELEGATIONS, token=analyst, body=good),
           409, "po_delegation_overlap")

    lst = http(args.rust, "GET", DELEGATIONS, token=analyst)
    expect("the giver lists it and sees candidates", lst, 200, extra=lambda: (
        [] if [i["id"] for i in lst.body["data"]["items"]] == [did]
        and sub_id in [c["id"] for c in lst.body["data"]["candidates"]]
        and fx.analyst_id not in [c["id"] for c in lst.body["data"]["candidates"]]
        else [f"list {json.dumps(lst.body)[:400]}"]))
    lst = http(args.rust, "GET", DELEGATIONS, token=sub)
    expect("the substitute lists it and gets no candidates", lst, 200, extra=lambda: (
        [] if [i["id"] for i in lst.body["data"]["items"]] == [did] and lst.body["data"]["candidates"] == []
        else [f"list {json.dumps(lst.body)[:400]}"]))
    lst = http(args.rust, "GET", DELEGATIONS, token=viewer)
    expect("a viewer lists nothing", lst, 200, extra=lambda: (
        [] if lst.body["data"]["items"] == [] else ["a viewer saw delegations"]))
    expect("all=true is for admins", http(args.rust, "GET", DELEGATIONS + "?all=true", token=analyst),
           403, "role_not_permitted")
    expect("an admin lists the whole company", http(args.rust, "GET", DELEGATIONS + "?all=true", token=admin), 200)

    # Python honours what Rust wrote.
    def decided_for(po, who_id, behalf_id):
        cur.execute("""SELECT status, decided_by, decided_on_behalf_of, delegation_id
                         FROM po_approvals WHERE po_log_id = %s""", (po,))
        row = cur.fetchone()
        return [] if row and row[1] == who_id and row[2] == behalf_id and row[3] == did else [f"row {row}"]

    r = http(args.python, "POST", f"{API}/inventory/po/{pos[0]}/approval/approve", token=sub, body={})
    expect("python: the substitute approves on behalf of the approver", r, 200,
           extra=lambda: decided_for(pos[0], sub_id, fx.analyst_id) + (
               [] if r.body["data"].get("on_behalf_of_name") else ["no on_behalf_of_name"]))

    # Revoking.
    expect("only the giver or an admin revokes (the substitute sees 404)",
           http(args.rust, "POST", f"{DELEGATIONS}/{did}/revoke", token=sub, body={}), 404, "po_delegation_not_found")
    r = http(args.rust, "POST", f"{DELEGATIONS}/{did}/revoke", token=analyst, body={})
    expect("the giver revokes it", r, 200, extra=lambda: (
        [] if r.body["data"]["changed"] is True and r.body["data"]["status"] == "revoked" else [f"body {r.body}"]))
    r = http(args.rust, "POST", f"{DELEGATIONS}/{did}/revoke", token=analyst, body={})
    expect("revoking again changes nothing", r, 200, extra=lambda: (
        [] if r.body["data"]["changed"] is False else [f"body {r.body}"]))
    cur.execute("""SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s
                     AND action = 'approval_delegation.revoked'""", (fx.tenant_id,))
    record("one revocation, one event", [] if cur.fetchone()[0] == 1 else ["event count"])

    def untouched(po):
        cur.execute("SELECT status, decided_by FROM po_approvals WHERE po_log_id = %s", (po,))
        return [] if cur.fetchone() == ("requested", None) else ["the order was decided"]

    r = http(args.python, "POST", f"{API}/inventory/po/{pos[1]}/approval/approve", token=sub, body={})
    expect("python: a revoked delegation no longer approves", r, 403, "po_approval_not_approver",
           extra=lambda: untouched(pos[1]))

    # Expiry needs no job: the same row, a few days older.
    r = http(args.rust, "POST", DELEGATIONS, token=analyst,
             body={**good, "starts_on": today_plus(1), "ends_on": today_plus(2)})
    expect("a delegation can start in the future", r, 201, extra=lambda: (
        [] if r.body["data"]["status"] == "scheduled" else [f"status {r.body['data']['status']}"]))
    future_id = (r.body or {}).get("data", {}).get("id")
    r2 = http(args.python, "POST", f"{API}/inventory/po/{pos[2]}/approval/approve", token=sub, body={})
    expect("python: it does not work before it starts", r2, 403, "po_approval_not_approver",
           extra=lambda: untouched(pos[2]))
    cur.execute("""UPDATE po_approval_delegations
                      SET starts_on = CURRENT_DATE - 5, ends_on = CURRENT_DATE - 1
                    WHERE id = %s""", (future_id,))
    r3 = http(args.python, "POST", f"{API}/inventory/po/{pos[2]}/approval/approve", token=sub, body={})
    expect("python: an expired delegation is refused with no cleanup job", r3, 403,
           "po_approval_not_approver", extra=lambda: untouched(pos[2]))
    return results
# ── Customer portal: Rust-only routes, asserted against expectations ─────────
#
# There is no Python twin to diff against (the group is new in Rust, with no
# failover), so these cases assert the contract itself: status, error code, the
# SHAPE of the public page, and the rows and activity events left in the
# database. They run against `--rust` only; Python is used to seed the tenant
# (commitments through the real API) and to erase it.
#
# Needs `--db` (stock and promise rows are seeded and checked there) and a
# database whose schema includes the three `customer_portal_*` tables, i.e. one
# the Python API of THIS branch has migrated.

PORTAL = f"{API}/customer-portal"
# Strings that live in the throwaway tenant's INTERNAL columns. None of them may
# appear anywhere in anything a customer can fetch.
PORTAL_SECRETS = {
    "stock level": "4242.5", "unit cost": "77.77", "supplier name": "SECRETSUPPLIER-XYZ",
    "stock notes": "INTERNAL-STOCK-NOTE", "commitment note": "INTERNAL-COMMIT-NOTE",
    "other customer": "Other Corp", "min stock": "1313",
}
PORTAL_BANNED_KEYS = {
    "stock", "current_stock", "min_stock", "unit_cost", "cost", "probability", "note", "notes", "warehouse",
    "warehouse_id", "supplier", "customer_key", "token", "token_hash", "created_by", "on_top_of_base",
    "contract_id", "source", "at_risk", "verdict", "shortfall",
}


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_keys(v)


def run_portal(args, fx: Fixture, db) -> list:
    route = "customer portal (rust only)"
    out: list = []
    if db is None:
        return [(Case("customer portal (all)", "-", "-", route=route), "SKIP",
                 ["needs --db: rows are seeded and checked in the database"])]
    rs = args.rust
    cur = db.cursor()
    tag = secrets.token_hex(3)

    def step(name, fn):
        try:
            problems = fn() or []
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a dead run
            problems = [f"raised {type(exc).__name__}: {exc}"]
        out.append((Case(name, "-", "-", route=route), "FAIL" if problems else "PASS", problems))

    def call(method, path, who="analyst", body=None, raw_body=None, headers=None):
        return http(rs, method, path, token=auth_for(fx, who) if who else None, body=body,
                    raw_body=raw_body, headers=headers)

    def seed(customer, sku, qty, note=None, prob=0.37):
        r = http(args.python, "POST", f"{API}/committed-demand", token=auth_for(fx, "admin"),
                 body={"sku": sku, "delivery_date": today_plus(60), "quantity": qty, "customer": customer,
                       "probability": prob, "note": note})
        if r.status != 201:
            raise SystemExit(f"seeding a commitment failed: {r.status} {r.body}")
        return r.body["data"]["id"]

    skus = {"a": f"PT-A-{tag}", "b": f"PT-B-{tag}", "x": f"PT-X-{tag}"}
    for key, name in (("a", "Widget Alpha"), ("b", "Widget Beta"), ("x", "Other Widget")):
        cur.execute("""INSERT INTO inventory_stock (tenant_id, sku, display_name, current_stock, min_stock,
                                                     lead_time_days, unit_cost, supplier, notes)
                       VALUES (%s, %s, %s, 4242.5, 1313, 9, 77.77, 'SECRETSUPPLIER-XYZ', 'INTERNAL-STOCK-NOTE')
                       ON CONFLICT (tenant_id, sku, warehouse) DO NOTHING""", (fx.tenant_id, skus[key], name))
        # The stock table has one row per warehouse: a second one must not repeat the commitment.
        cur.execute("""INSERT INTO inventory_stock (tenant_id, sku, display_name, current_stock, warehouse)
                       VALUES (%s, %s, %s, 1, 'norte') ON CONFLICT (tenant_id, sku, warehouse) DO NOTHING""",
                    (fx.tenant_id, skus[key], name))
    c_a = seed("Portal Co", skus["a"], 40, "INTERNAL-COMMIT-NOTE")
    c_b = seed("PORTAL CO", skus["b"], 15)             # same customer, different casing
    c_x = seed("Other Corp", skus["x"], 99)
    cur.execute("SELECT delivery_date::text, quantity::float8, status FROM committed_demand WHERE id = %s", (c_a,))
    before_a = cur.fetchone()

    def links_count():
        cur.execute("SELECT COUNT(*) FROM customer_portal_links WHERE tenant_id = %s", (fx.tenant_id,))
        return cur.fetchone()[0]

    def events_of(link_id, action):
        cur.execute("""SELECT user_id, context FROM activity_logs
                        WHERE tenant_id = %s AND resource = %s AND action = %s ORDER BY created_at""",
                    (fx.tenant_id, link_id, action))
        return cur.fetchall()

    S: dict = {}

    # ── permission pairs and validation on create ────────────────────────────
    def create_denied():
        n = links_count()
        p = []
        r = call("POST", f"{PORTAL}/links", "viewer", {"customer": "Portal Co"})
        if r.status != 403 or r.body.get("error_code") != "role_not_permitted":
            p.append(f"viewer create: {r.status} {r.body}")
        r = call("POST", f"{PORTAL}/links", None, {"customer": "Portal Co"})
        if r.status != 401:
            p.append(f"no token create: {r.status}")
        for who in ("key_read", "key_write"):
            if auth_for(fx, who) is None:
                continue
            r = call("POST", f"{PORTAL}/links", who, {"customer": "Portal Co"})
            if r.status != 403 or r.body.get("error_code") != "api_key_route_not_exposed":
                p.append(f"{who} create: {r.status} {r.body.get('error_code') if isinstance(r.body, dict) else r.body}")
        if links_count() != n:
            p.append("a refused create wrote a link")
        return p
    step("portal create: viewer, no token and API keys are refused, nothing written", create_denied)

    def create_validation():
        n = links_count()
        p = []
        for label, body, code in (
            ("unknown customer", {"customer": "Nobody Inc"}, "customer_portal_customer_unknown"),
            ("blank customer", {"customer": "   "}, "customer_portal_customer_required"),
            ("bad language", {"customer": "Portal Co", "language": "fr"}, "validation_error"),
            ("zero days", {"customer": "Portal Co", "expires_in_days": 0}, "validation_error"),
            ("366 days", {"customer": "Portal Co", "expires_in_days": 366}, "validation_error"),
            ("fractional days", {"customer": "Portal Co", "expires_in_days": 1.5}, "validation_error"),
            ("missing customer", {}, "validation_error"),
            ("share not a bool", {"customer": "Portal Co", "share_dates": "maybe"}, "validation_error"),
        ):
            r = call("POST", f"{PORTAL}/links", "analyst", body)
            if r.status != 422 or r.body.get("error_code") != code:
                p.append(f"{label}: {r.status} {r.body.get('error_code')} (wanted 422 {code})")
        if links_count() != n:
            p.append("a refused create wrote a link")
        return p
    step("portal create: validation and unknown customer are 422, nothing written", create_validation)

    def create_ok():
        p = []
        r = call("POST", f"{PORTAL}/links", "analyst", {"customer": "portal co", "language": "en"})
        if r.status != 201:
            return [f"create: {r.status} {r.body}"]
        d = r.body["data"]
        S["token"], S["link"] = d["token"], d["link"]["id"]
        if len(S["token"]) != 43 or not d["url"].endswith("/cliente/" + S["token"]):
            p.append(f"token/url shape: {len(S['token'])} {d['url']}")
        cur.execute("""SELECT token_hash, customer, customer_key, language, share_dates, created_by,
                              expires_at > NOW() + interval '89 days'
                         FROM customer_portal_links WHERE id = %s AND tenant_id = %s""", (S["link"], fx.tenant_id))
        row = cur.fetchone()
        if not row:
            return ["no row stored"]
        if row[0] != hashlib.sha256(S["token"].encode()).hexdigest():
            p.append("stored hash is not sha256(token)")
        if row[2] != "portal co" or row[3] != "en" or row[4] is not False or row[5] != fx.analyst_id or not row[6]:
            p.append(f"row fields: {row[1:]}")
        cur.execute("SELECT to_jsonb(l)::text FROM customer_portal_links l WHERE id = %s", (S["link"],))
        if S["token"] in cur.fetchone()[0]:
            p.append("the plaintext token is in the stored row")
        if "token_hash" in json.dumps(d["link"]) or S["token"] in json.dumps(d["link"]):
            p.append("the link object carries the token or its hash")
        ev = events_of(S["link"], "customer_portal.link_created")
        if len(ev) != 1 or ev[0][0] != fx.analyst_id:
            p.append(f"link_created events: {ev}")
        # The token is never shown again.
        for path in (f"{PORTAL}/links", f"{PORTAL}/links/{S['link']}"):
            rr = call("GET", path, "viewer")
            if rr.status != 200 or S["token"] in rr.raw.decode() or "token_hash" in rr.raw.decode():
                p.append(f"GET {path} leaks the token or hash ({rr.status})")
        return p
    step("portal create: analyst gets the link once; only sha256(token) is stored; event recorded", create_ok)

    # ── the public page ──────────────────────────────────────────────────────
    PUB = f"{PORTAL}/public"

    def page(token=None):
        return call("GET", f"{PUB}/{token or S['token']}", None)

    def public_shape():
        p = []
        r = page()
        if r.status != 200:
            return [f"public GET: {r.status} {r.body}"]
        for h, v in (("cache-control", "no-store"), ("referrer-policy", "no-referrer"),
                     ("x-robots-tag", "noindex, nofollow")):
            if r.headers.get(h) != v:
                p.append(f"header {h}={r.headers.get(h)!r}")
        d = r.body["data"]
        if set(d) != {"company", "customer", "language", "share_dates", "expires_at", "commitments"}:
            p.append(f"page keys: {sorted(d)}")
        ids = {c["id"] for c in d["commitments"]}
        if ids != {c_a, c_b}:
            p.append(f"commitments shown: {ids} (want {c_a}, {c_b}; never {c_x})")
        for c in d["commitments"]:
            if set(c) != {"id", "sku", "description", "quantity", "requested_date", "status", "my_response"}:
                p.append(f"commitment keys: {sorted(c)}")
        if {c["description"] for c in d["commitments"]} != {"Widget Alpha", "Widget Beta"}:
            p.append("descriptions are not the stock display names")
        if d["language"] != "en" or d["share_dates"] is not False:
            p.append(f"language/share: {d['language']} {d['share_dates']}")
        cur.execute("SELECT last_viewed_at IS NOT NULL FROM customer_portal_links WHERE id = %s", (S["link"],))
        if not cur.fetchone()[0]:
            p.append("last_viewed_at not recorded")
        return p
    step("portal public: whitelist shape, own commitments only, privacy headers", public_shape)

    def public_leak():
        """Nothing internal appears anywhere in anything a customer can fetch."""
        p = []
        raw = page().raw.decode()
        body = json.loads(raw)
        for key in set(_walk_keys(body)) & PORTAL_BANNED_KEYS:
            p.append(f"banned key in the page: {key}")
        for label, needle in PORTAL_SECRETS.items():
            if needle in raw:
                p.append(f"{label} ({needle}) leaked into the page")
        if S["token"] in raw or hashlib.sha256(S["token"].encode()).hexdigest() in raw:
            p.append("the token or its hash is in the page")
        if skus["x"] in raw or c_x in raw:
            p.append("another customer's commitment leaked")
        # the same must hold for what the customer gets back after answering
        r = call("POST", f"{PUB}/{S['token']}/respond", None,
                 {"commitment_id": c_b, "response": "received"})
        raw2 = r.raw.decode()
        for label, needle in PORTAL_SECRETS.items():
            if needle in raw2:
                p.append(f"{label} leaked into the answer response")
        if r.status not in (200, 201):
            p.append(f"respond: {r.status} {r.body}")
        return p
    step("portal public: leak test (no stock, cost, supplier, notes, probability, other customers)", public_leak)

    def promised_dates():
        p = []
        d = today_plus(75)
        r = call("PUT", f"{PORTAL}/links/{S['link']}/promised-dates", "viewer",
                 {"commitment_id": c_a, "promised_date": d})
        if r.status != 403:
            p.append(f"viewer promise: {r.status}")
        cur.execute("SELECT COUNT(*) FROM customer_portal_promised_dates WHERE tenant_id = %s", (fx.tenant_id,))
        if cur.fetchone()[0] != 0:
            p.append("a refused promise wrote a row")
        r = call("PUT", f"{PORTAL}/links/{S['link']}/promised-dates", "analyst", {"commitment_id": c_a, "promised_date": d})
        if r.status != 200 or r.body["data"]["promised_date"] != d:
            p.append(f"analyst promise: {r.status} {r.body}")
        r = call("PUT", f"{PORTAL}/links/{S['link']}/promised-dates", "analyst",
                 {"commitment_id": c_x, "promised_date": d})
        if r.status != 404 or r.body.get("error_code") != "customer_portal_commitment_not_found":
            p.append(f"another customer's commitment: {r.status} {r.body.get('error_code')}")
        for label, bad in (("not a date", "next week"), ("far future", "2099-01-01"), ("ancient", "1990-01-01")):
            r = call("PUT", f"{PORTAL}/links/{S['link']}/promised-dates", "analyst",
                     {"commitment_id": c_a, "promised_date": bad})
            if r.status != 422:
                p.append(f"{label}: {r.status}")
        # shared off: the customer still sees no promised date
        if any("promised_date" in c for c in page().body["data"]["commitments"]):
            p.append("a promised date reached the page while share_dates is off")
        r = call("PATCH", f"{PORTAL}/links/{S['link']}", "viewer", {"share_dates": True})
        cur.execute("SELECT share_dates FROM customer_portal_links WHERE id = %s", (S["link"],))
        if r.status != 403 or cur.fetchone()[0] is not False:
            p.append(f"viewer share toggle: {r.status}")
        r = call("PATCH", f"{PORTAL}/links/{S['link']}", "analyst", {"share_dates": True})
        if r.status != 200 or r.body["data"]["changed"] is not True:
            p.append(f"analyst share toggle: {r.status} {r.body}")
        shown = {c["id"]: c.get("promised_date") for c in page().body["data"]["commitments"]}
        if shown.get(c_a) != d or shown.get(c_b) is not None:
            p.append(f"shared promised dates: {shown}")
        if len(events_of(S["link"], "customer_portal.promise_set")) != 1 or \
                len(events_of(S["link"], "customer_portal.link_updated")) != 1:
            p.append("promise_set / link_updated events missing")
        cur.execute("SELECT delivery_date::text FROM committed_demand WHERE id = %s", (c_a,))
        if cur.fetchone()[0] != before_a[0]:
            p.append("setting a promised date changed the commitment's own date")
        r = call("PUT", f"{PORTAL}/links/{S['link']}/promised-dates", "analyst", {"commitment_id": c_a, "promised_date": None})
        cur.execute("SELECT COUNT(*) FROM customer_portal_promised_dates WHERE commitment_id = %s", (c_a,))
        if r.status != 200 or cur.fetchone()[0] != 0:
            p.append("clearing the promised date failed")
        return p
    step("portal promised dates: permission pair, scope, hidden unless shared, commitment untouched", promised_dates)

    # ── probing: every bad link is the same 404 ──────────────────────────────
    def probing():
        p = []
        cur.execute("""INSERT INTO customer_portal_links (tenant_id, customer, customer_key, token_hash, expires_at,
                                                          created_by)
                       VALUES (%s, 'Portal Co', 'portal co', %s, NOW() - interval '1 day', %s)""",
                    (fx.tenant_id, hashlib.sha256(b"E" * 43).hexdigest(), fx.analyst_id))
        cur.execute("""INSERT INTO customer_portal_links (tenant_id, customer, customer_key, token_hash, expires_at,
                                                          revoked_at, created_by)
                       VALUES (%s, 'Portal Co', 'portal co', %s, NOW() + interval '5 days', NOW(), %s)""",
                    (fx.tenant_id, hashlib.sha256(b"R" * 43).hexdigest(), fx.analyst_id))
        probes = {
            "unknown": secrets.token_urlsafe(32), "expired": "E" * 43, "revoked": "R" * 43,
            "too short": "abc", "bad chars": "!" * 43, "overlong": "a" * 5000,
            "one off": S["token"][:-1] + ("A" if S["token"][-1] != "A" else "B"),
            "sql-ish": "' OR '1'='1' --" + "a" * 30, "unicode": "é" * 43,
        }
        seen = {}
        for label, tok in probes.items():
            quoted = urllib.parse.quote(tok, safe="")
            r = http(rs, "GET", f"{PUB}/{quoted}")
            r2 = http(rs, "POST", f"{PUB}/{quoted}/respond", body={"commitment_id": c_a, "response": "received"})
            seen[label] = (r.status, json.dumps(r.body, sort_keys=True), r2.status,
                           json.dumps(r2.body, sort_keys=True), r.headers.get("cache-control"))
        first = next(iter(seen.values()))
        for label, got in seen.items():
            if got != first:
                p.append(f"{label} answers differently from the others: {got}")
        if first[0] != 404 or first[2] != 404 or '"customer_portal_unavailable"' not in first[1]:
            p.append(f"the uniform answer is not a 404 customer_portal_unavailable: {first}")
        cur.execute("""SELECT COUNT(*) FROM customer_portal_events
                        WHERE tenant_id = %s AND link_id <> %s""", (fx.tenant_id, S["link"]))
        if cur.fetchone()[0] != 0:
            p.append("a bad link wrote an answer")
        return p
    step("portal public: unknown, expired, revoked, malformed and overlong tokens all answer one identical 404", probing)

    # ── the customer's answers ───────────────────────────────────────────────
    def responses():
        p = []
        base = f"{PUB}/{S['token']}/respond"
        cur.execute("SELECT COUNT(*) FROM customer_portal_events WHERE link_id = %s AND commitment_id = %s",
                    (S["link"], c_a))
        n0 = cur.fetchone()[0]
        r = call("POST", base, None, {"commitment_id": c_a, "response": "received"})
        if r.status != 201 or r.body["data"] != {"recorded": True, "duplicate": False, "response": "received"}:
            p.append(f"received: {r.status} {r.body}")
        r = call("POST", base, None, {"commitment_id": c_a, "response": "received"})
        if r.status != 200 or r.body["data"]["duplicate"] is not True:
            p.append(f"repeat received: {r.status} {r.body}")
        cur.execute("SELECT COUNT(*) FROM customer_portal_events WHERE link_id = %s AND commitment_id = %s",
                    (S["link"], c_a))
        if cur.fetchone()[0] != n0 + 1:
            p.append("a repeated 'received' wrote a second row")
        ev = events_of(S["link"], "customer_portal.received")
        if len([e for e in ev if e[1].get("sku") == skus["a"]]) != 1 or any(e[0] != fx.analyst_id for e in ev):
            p.append(f"received events: {ev}")
        for label, body, reason in (
            ("no comment", {"commitment_id": c_a, "response": "date_objection"}, "comment_required"),
            ("blank comment", {"commitment_id": c_a, "response": "date_objection", "comment": "  "}, "comment_required"),
            ("unknown field", {"commitment_id": c_a, "response": "received", "x": 1}, "unknown_field"),
            ("bad response", {"commitment_id": c_a, "response": "fulfilled"}, "response_invalid"),
            ("bad id", {"commitment_id": "a b;", "response": "received"}, "commitment_invalid"),
            ("long comment", {"commitment_id": c_a, "response": "date_objection", "comment": "x" * 501}, "comment_too_long"),
            ("control char", {"commitment_id": c_a, "response": "date_objection", "comment": "a\u0000b"}, "comment_invalid"),
        ):
            r = call("POST", base, None, body)
            got = (r.body.get("error_params") or {}).get("reason") if isinstance(r.body, dict) else None
            if r.status != 422 or got != reason:
                p.append(f"{label}: {r.status} {got} (wanted 422 {reason})")
        r = call("POST", base, None, raw_body=b"not json", headers={"Content-Type": "application/json"})
        if r.status != 422:
            p.append(f"non-JSON body: {r.status}")
        r = call("POST", base, None, raw_body=b" " * (20 * 1024), headers={"Content-Type": "application/json"})
        if r.status != 413 or r.body.get("error_code") != "customer_portal_body_too_large":
            p.append(f"oversized body: {r.status} {r.body}")
        r = call("POST", base, None, {"commitment_id": c_x, "response": "received"})
        if r.status != 404 or r.body.get("error_code") != "customer_portal_commitment_not_found":
            p.append(f"another customer's commitment: {r.status} {r.body.get('error_code')}")
        cur.execute("SELECT COUNT(*) FROM customer_portal_events WHERE commitment_id = %s", (c_x,))
        if cur.fetchone()[0] != 0:
            p.append("an answer was stored against another customer's commitment")
        comment = "We need it two weeks earlier <b>please</b>"
        r = call("POST", base, None, {"commitment_id": c_a, "response": "date_objection", "comment": comment})
        if r.status != 201:
            p.append(f"objection: {r.status} {r.body}")
        cur.execute("SELECT response, comment, ip_hash FROM customer_portal_events WHERE link_id = %s AND commitment_id = %s "
                    "AND response = 'date_objection'", (S["link"], c_a))
        row = cur.fetchone()
        if not row or row[1] != comment or not row[2] or len(row[2]) != 32:
            p.append(f"objection row: {row}")
        ev = events_of(S["link"], "customer_portal.date_objected")
        if len(ev) != 1 or ev[0][1].get("severity") != "warning" or ev[0][1].get("reason") != "customer_date_objection" \
                or ev[0][1].get("sku") != skus["a"] or "comment" in json.dumps(ev[0][1]):
            p.append(f"date_objected event: {ev}")
        cur.execute("SELECT delivery_date::text, quantity::float8, status FROM committed_demand WHERE id = %s", (c_a,))
        if cur.fetchone() != before_a:
            p.append("a customer answer changed the commitment")
        v = page().body["data"]["commitments"]
        mine = {c["id"]: c["my_response"] for c in v}
        if mine.get(c_a) != "date_objection" or mine.get(c_b) != "received":
            p.append(f"my_response: {mine}")
        d = call("GET", f"{PORTAL}/links/{S['link']}", "viewer").body["data"]
        if len(d["events"]) < 3 or not any(e["comment"] == comment for e in d["events"]):
            p.append("the tenant does not see the customer's comment")
        # closed commitments: an objection needs an open one, nothing is taken for a cancelled one
        for status, response, want in (("fulfilled", "date_objection", (409,)), ("cancelled", "received", (409,)),
                                       ("fulfilled", "received", (200, 201))):
            cur.execute("UPDATE committed_demand SET status = %s WHERE id = %s", (status, c_b))
            r = call("POST", base, None, {"commitment_id": c_b, "response": response,
                                           **({"comment": "late"} if response == "date_objection" else {})})
            if r.status not in want:
                p.append(f"{status}/{response}: {r.status} (wanted one of {want})")
        cur.execute("UPDATE committed_demand SET status = 'open' WHERE id = %s", (c_b,))
        return p
    step("portal respond: strict input, own commitments only, events and bell, commitment never changed", responses)

    def per_commitment_cap():
        p = []
        base = f"{PUB}/{S['token']}/respond"
        last = None
        for _ in range(25):
            last = call("POST", base, None, {"commitment_id": c_a, "response": "date_objection", "comment": "again"})
            if last.status == 429:
                break
        if last is None or last.status != 429 or last.body.get("error_code") != "too_many_attempts":
            p.append(f"no cap on repeated answers: {last.status if last else None}")
        cur.execute("SELECT COUNT(*) FROM customer_portal_events WHERE link_id = %s AND commitment_id = %s",
                    (S["link"], c_a))
        if cur.fetchone()[0] > 20:
            p.append("more than 20 answers stored for one commitment")
        return p
    step("portal respond: a link cannot flood one commitment with answers", per_commitment_cap)

    # ── revoke / reopen ──────────────────────────────────────────────────────
    def revoke_reopen():
        p = []
        n = len(events_of(S["link"], "customer_portal.link_revoked"))
        r = call("POST", f"{PORTAL}/links/{S['link']}/revoke", "viewer")
        cur.execute("SELECT revoked_at IS NULL FROM customer_portal_links WHERE id = %s", (S["link"],))
        if r.status != 403 or not cur.fetchone()[0]:
            p.append(f"viewer revoke: {r.status}")
        r = call("POST", f"{PORTAL}/links/{S['link']}/revoke", "analyst")
        if r.status != 200 or r.body["data"]["changed"] is not True or r.body["data"]["link"]["status"] != "revoked":
            p.append(f"analyst revoke: {r.status} {r.body}")
        r = call("POST", f"{PORTAL}/links/{S['link']}/revoke", "analyst")
        if r.status != 200 or r.body["data"]["changed"] is not False:
            p.append(f"repeat revoke: {r.status} {r.body}")
        if len(events_of(S["link"], "customer_portal.link_revoked")) != n + 1:
            p.append("revoke event count is not exactly one")
        cur.execute("SELECT revoked_by FROM customer_portal_links WHERE id = %s", (S["link"],))
        if cur.fetchone()[0] != fx.analyst_id:
            p.append("revoked_by is not the analyst")
        g = page()
        post = call("POST", f"{PUB}/{S['token']}/respond", None, {"commitment_id": c_a, "response": "received"})
        if g.status != 404 or post.status != 404 or g.body.get("error_code") != "customer_portal_unavailable":
            p.append(f"a revoked link still answers: {g.status} {post.status}")
        r = call("POST", f"{PORTAL}/links/{S['link']}/reopen", "viewer")
        if r.status != 403:
            p.append(f"viewer reopen: {r.status}")
        r = call("POST", f"{PORTAL}/links/{S['link']}/reopen", "analyst")
        if r.status != 200 or r.body["data"]["changed"] is not True or r.body["data"]["link"]["status"] != "active":
            p.append(f"analyst reopen: {r.status} {r.body}")
        if call("POST", f"{PORTAL}/links/{S['link']}/reopen", "analyst").body["data"]["changed"] is not False:
            p.append("repeat reopen reported a change")
        if page().status != 200:
            p.append("the reopened link does not answer")
        # an expired link is reopened too (and the earlier answers are history)
        cur.execute("UPDATE customer_portal_links SET expires_at = NOW() - interval '1 hour' WHERE id = %s", (S["link"],))
        if page().status != 404:
            p.append("an expired link still answers")
        r = call("POST", f"{PORTAL}/links/{S['link']}/reopen", "analyst")
        if r.status != 200 or page().status != 200:
            p.append(f"reopening an expired link: {r.status}")
        cur.execute("SELECT COUNT(*) FROM customer_portal_events WHERE link_id = %s", (S["link"],))
        if cur.fetchone()[0] < 3:
            p.append("answers did not survive the revoke and reopen")
        return p
    step("portal revoke / reopen: permission pair, exactly one event, bad link is the uniform 404, answers kept", revoke_reopen)

    # ── another tenant can neither read nor change this link ─────────────────
    other = make_fixture(args.python, fx.secret)
    other_tenant = other.tenant_id
    erased = False
    try:
        def tenant_scope():
            p = []
            lid = S["link"]
            tok = mint_access_token(other.secret, other.admin_id, other.tenant_id, "admin")
            r = http(rs, "GET", f"{PORTAL}/links/{lid}", token=tok)
            if r.status != 404 or r.body.get("error_code") != "customer_portal_link_not_found":
                p.append(f"foreign GET: {r.status} {r.body.get('error_code')}")
            for verb, path, body in (("POST", f"{PORTAL}/links/{lid}/revoke", None),
                                     ("PATCH", f"{PORTAL}/links/{lid}", {"share_dates": False}),
                                     ("PUT", f"{PORTAL}/links/{lid}/promised-dates",
                                      {"commitment_id": c_a, "promised_date": today_plus(10)})):
                r = http(rs, verb, path, token=tok, body=body)
                if r.status != 404:
                    p.append(f"foreign {verb}: {r.status}")
            if any(l["id"] == lid for l in http(rs, "GET", f"{PORTAL}/links", token=tok).body["data"]):
                p.append("the foreign tenant lists this tenant's link")
            r = http(rs, "POST", f"{PORTAL}/links", token=tok, body={"customer": "Portal Co"})
            if r.status != 422 or r.body.get("error_code") != "customer_portal_customer_unknown":
                p.append(f"foreign create for this tenant's customer: {r.status} {r.body.get('error_code')}")
            cur.execute("SELECT revoked_at IS NULL, share_dates FROM customer_portal_links WHERE id = %s", (lid,))
            if cur.fetchone() != (True, True):
                p.append("a foreign call changed the link")
            return p
        step("portal tenant scope: another tenant gets 404 on read and every write, link unchanged", tenant_scope)

        def erasure():
            p = []
            tok = mint_access_token(other.secret, other.admin_id, other.tenant_id, "admin")
            cur.execute("""INSERT INTO committed_demand (tenant_id, sku, delivery_date, quantity, customer, created_by)
                           VALUES (%s, 'ER-1', CURRENT_DATE + 30, 5, 'Erase Co', %s) RETURNING id""",
                        (other_tenant, other.admin_id))
            cid = cur.fetchone()[0]
            r = http(rs, "POST", f"{PORTAL}/links", token=tok, body={"customer": "Erase Co"})
            if r.status != 201:
                return [f"create on the second tenant: {r.status} {r.body}"]
            lid, token = r.body["data"]["link"]["id"], r.body["data"]["token"]
            http(rs, "POST", f"{PUB}/{token}/respond", body={"commitment_id": cid, "response": "received"})
            http(rs, "PUT", f"{PORTAL}/links/{lid}/promised-dates", token=tok,
                 body={"commitment_id": cid, "promised_date": today_plus(20)})
            for table in ("customer_portal_links", "customer_portal_events", "customer_portal_promised_dates"):
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s", (other_tenant,))
                if cur.fetchone()[0] != 1:
                    p.append(f"{table} was not populated before the erasure")
            nonlocal erased
            erase_fixture(args.python, other)
            erased = True
            for table in ("customer_portal_links", "customer_portal_events", "customer_portal_promised_dates"):
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s", (other_tenant,))
                if cur.fetchone()[0] != 0:
                    p.append(f"{table} rows survive the tenant's erasure")
            if http(rs, "GET", f"{PUB}/{token}").status != 404:
                p.append("the erased tenant's link still answers")
            return p
        step("portal erasure: whole-tenant erasure removes links, answers and promised dates", erasure)
    finally:
        if not erased:
            erase_fixture(args.python, other)

    out.append((Case("portal rate limits", "-", "-", route=route), "SKIP",
                ["per-address and per-link limits are off under TESTING_MODE=true; unit-tested (rate_keys) only"]))
    return out
# ── IP allowlist: Rust-only admin routes, enforced by BOTH services ─────────
#
# The four admin routes exist only in Rust, so there is nothing to diff them
# against: each step asserts the Rust answer and the database rows. The
# enforcement is the part both services share, so the same request goes to
# Python and to Rust and the two refusals must be identical.
#
# Needs both servers started with TRUSTED_PROXY_HOPS=1 (the harness sends the
# client address the way one proxy would, in X-Forwarded-For). Without it the
# section skips itself rather than report a configuration problem as a defect.

IPA = f"{API}/ip-allowlist"
IPA_OFFICE = "203.0.113.0/24"
IPA_ME = "203.0.113.5"
IPA_OUTSIDE = "192.0.2.9"


def run_ip_allowlist(args, fx: Fixture, db) -> list:
    results: list = []
    if db is None:
        print("IP allowlist cases skipped: they need --db (state is asserted on rows)")
        return results
    route = "ip-allowlist"
    cur = db.cursor()

    def record(name: str, problems: list[str]) -> None:
        if args.only and args.only not in name:
            return
        results.append((Case(name, "-", "-", route=route), "FAIL" if problems else "PASS", problems))

    def rs(method, path, who="admin", body=None, ip=IPA_ME, raw_body=None):
        return http(args.rust, method, path, token=auth_for(fx, who), body=body, raw_body=raw_body,
                    headers={"X-Forwarded-For": ip} if ip else {})

    def py(method, path, who="admin", ip=IPA_ME):
        return http(args.python, method, path, token=auth_for(fx, who),
                    headers={"X-Forwarded-For": ip} if ip else {})

    def rows():
        cur.execute("SELECT cidr, label FROM ip_allowlist_entries WHERE tenant_id = %s ORDER BY cidr",
                    (fx.tenant_id,))
        return cur.fetchall()

    def enabled():
        cur.execute("SELECT enabled FROM ip_allowlist_policies WHERE tenant_id = %s", (fx.tenant_id,))
        r = cur.fetchone()
        return bool(r and r[0])

    def events(action, extra=""):
        cur.execute(f"SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s {extra} "
                    "ORDER BY created_at", (fx.tenant_id, action))
        return [r[0] for r in cur.fetchall()]

    def expect(r, status, code=None):
        out = []
        if r.status != status:
            out.append(f"status {r.status}, wanted {status}: {json.dumps(r.body)[:200]}")
        elif code is not None and (r.body or {}).get("error_code") != code:
            out.append(f"error_code {(r.body or {}).get('error_code')!r}, wanted {code!r}")
        return out

    probe = rs("GET", IPA)
    if probe.status != 200 or probe.body["data"].get("your_ip") != IPA_ME:
        print(f"IP allowlist cases skipped: Rust did not read {IPA_ME} from X-Forwarded-For "
              f"(status {probe.status}); start both servers with TRUSTED_PROXY_HOPS=1")
        return results

    # Permission pairs and the three refusals before any role check.
    r = rs("GET", IPA, who="viewer")
    record("viewer cannot read the allowlist", expect(r, 403, "role_not_permitted"))
    r = rs("GET", IPA, who="analyst")
    record("analyst cannot read the allowlist", expect(r, 403, "role_not_permitted"))
    r = rs("GET", IPA, who="none")
    record("no token is 401", expect(r, 401))
    if fx.write_key:
        r = rs("GET", IPA, who="key_write")
        record("a key never reaches the admin routes", expect(r, 403, "api_key_route_not_exposed"))
    r = rs("POST", f"{IPA}/entries", who="viewer", body={"cidr": IPA_OFFICE})
    record("viewer cannot add an entry (nothing written)", expect(r, 403) + ([] if rows() == [] else ["row written"]))
    r = rs("PUT", f"{IPA}/policy", who="analyst", body={"enabled": True})
    record("analyst cannot enable (nothing written)", expect(r, 403) + ([] if not enabled() else ["enabled"]))

    # Initial state.
    r = rs("GET", IPA)
    d = (r.body or {}).get("data", {})
    record("admin reads the empty allowlist", expect(r, 200) + (
        [] if d.get("enabled") is False and d.get("entries") == [] and d.get("your_ip") == IPA_ME
        and d.get("your_ip_covered") is False and d.get("max_entries") == 100 else [f"unexpected state {d}"]))

    # Lockout guard on enabling.
    r = rs("PUT", f"{IPA}/policy", body={"enabled": True})
    record("enabling without covering yourself is a lockout", expect(r, 409, "ip_allowlist_lockout")
           + ([] if (r.body or {}).get("error_params") == {"ip": IPA_ME} else ["params lack the ip"])
           + ([] if not enabled() else ["policy was enabled"]))
    r = rs("PUT", f"{IPA}/policy", body={"enabled": True}, ip="garbage")
    record("an unreadable address cannot enable", expect(r, 409, "ip_allowlist_address_unknown"))
    r = rs("PUT", f"{IPA}/policy", body={})
    record("policy without enabled is a validation error", expect(r, 422, "validation_error"))

    # Validation and the stored (normalised) form.
    for bad in ("300.1.1.1", "10.0.0.0/33", "::ffff:1.2.3.4", "a, b", ""):
        r = rs("POST", f"{IPA}/entries", body={"cidr": bad})
        record(f"entry {bad!r} is refused", expect(r, 422, "ip_allowlist_invalid_cidr") + ([] if rows() == [] else ["row written"]))
    r = rs("POST", f"{IPA}/entries", body={"label": "no cidr"})
    record("entry without cidr is a validation error", expect(r, 422, "validation_error"))
    r = rs("POST", f"{IPA}/entries", body={"cidr": IPA_OFFICE, "label": "x" * 101})
    record("a 101-character label is refused", expect(r, 422, "validation_error") + ([] if rows() == [] else ["row written"]))

    r = rs("POST", f"{IPA}/entries", body={"cidr": "203.0.113.77/24", "label": " Head office "})
    record("a range is stored normalised, label stripped", expect(r, 201) + (
        [] if rows() == [(IPA_OFFICE, "Head office")] else [f"rows {rows()}"])
        + ([] if (r.body or {}).get("data", {}).get("your_ip_covered") is True else ["view says not covered"]))
    r = rs("POST", f"{IPA}/entries", body={"cidr": IPA_OFFICE})
    record("the same range twice is a duplicate", expect(r, 409, "ip_allowlist_duplicate_entry") + (
        [] if len(rows()) == 1 else ["duplicate row"]))
    r = rs("POST", f"{IPA}/entries", body={"cidr": "2001:db8::1/32"})
    record("an IPv6 range is stored compressed", expect(r, 201) + (
        [] if ("2001:db8::/32", "") in rows() else [f"rows {rows()}"]))
    ev = events("account.ip_allowlist_changed")
    record("adding writes warning events with the reason", [] if len(ev) == 2 and all(
        e.get("severity") == "warning" and e.get("reason") == "ip_allowlist_entry_added" for e in ev)
        and ev[0].get("cidr") == IPA_OFFICE and ev[0].get("label") == "Head office"
        else [f"events {ev}"])

    # Enable now that the caller is covered.
    r = rs("PUT", f"{IPA}/policy", body={"enabled": True})
    record("enabling a covering list works", expect(r, 200) + (
        [] if enabled() and (r.body or {}).get("data", {}).get("enabled") is True else ["not enabled"]))
    r = rs("PUT", f"{IPA}/policy", body={"enabled": True})
    ev = events("account.ip_allowlist_changed", "AND context->>'reason' = 'ip_allowlist_enabled'")
    record("enabling twice records one event", expect(r, 200) + ([] if len(ev) == 1 else [f"{len(ev)} events"]))

    # Enforcement on both services: same request, same refusal.
    for label, who in (("person", "admin"), ("read key", "key_read")):
        if who.startswith("key_") and not fx.read_key:
            continue
        for ip, status in ((IPA_ME, 200), (IPA_OUTSIDE, 403), (f"{IPA_ME}, {IPA_OUTSIDE}", 403)):
            rp = py("GET", f"{API}/entitlements", who=who, ip=ip)
            rr = rs("GET", f"{API}/entitlements", who=who, ip=ip)
            problems = expect(rp, status, "ip_not_allowed" if status == 403 else None)
            problems += [f"rust: {p}" for p in expect(rr, status, "ip_not_allowed" if status == 403 else None)]
            if status == 403:
                problems += diff(normalize(rp.body, set()), normalize(rr.body, set()))
                shown = ip.split(",")[-1].strip()
                if (rr.body or {}).get("error_params") != {"ip": shown}:
                    problems.append(f"params {(rr.body or {}).get('error_params')}, wanted ip {shown}")
            record(f"{label} from {ip!r} is {status} on python and rust", problems)
    ev = events("account.ip_access_refused", f"AND context->>'ip' = '{IPA_OUTSIDE}'")
    # Python wrote the first refusal; the others (Rust, the key, the forged
    # chain) fall inside the 10-minute window for the same address.
    record("refusals of one address are one row across both services",
           [] if len(ev) == 1 else [f"{len(ev)} rows"])
    record("a refusal row carries the reason and severity",
           [] if ev and ev[0].get("reason") == "ip_not_in_allowlist" and ev[0].get("severity") == "warning"
           else [f"events {ev}"])

    # Lockout guard on deleting; the delete that does not strand the caller.
    cur.execute("SELECT id, cidr FROM ip_allowlist_entries WHERE tenant_id = %s", (fx.tenant_id,))
    ids = {c: i for i, c in cur.fetchall()}
    r = rs("DELETE", f"{IPA}/entries/{ids[IPA_OFFICE]}")
    record("deleting the entry that covers you is a lockout", expect(r, 409, "ip_allowlist_lockout")
           + ([] if (IPA_OFFICE, "Head office") in rows() else ["row was deleted"]))
    r = rs("DELETE", f"{IPA}/entries/{ids['2001:db8::/32']}")
    record("deleting another entry works", expect(r, 200) + (
        [] if ("2001:db8::/32", "") not in rows() else ["row survives"]))
    r = rs("DELETE", f"{IPA}/entries/ipa_nope")
    record("deleting an unknown id is 404", expect(r, 404, "ip_allowlist_entry_not_found"))
    r = rs("DELETE", f"{IPA}/entries/{ids[IPA_OFFICE]}", who="viewer")
    record("viewer cannot delete (row kept)", expect(r, 403) + ([] if (IPA_OFFICE, "Head office") in rows() else ["deleted"]))

    # Disabling is never blocked, and lifts the filter on both services.
    r = rs("PUT", f"{IPA}/policy", body={"enabled": False}, ip=IPA_OUTSIDE)
    record("an outside caller cannot even disable", expect(r, 403, "ip_not_allowed") + (
        [] if enabled() else ["policy was disabled"]))
    r = rs("PUT", f"{IPA}/policy", body={"enabled": False})
    record("disabling works", expect(r, 200) + ([] if not enabled() else ["still enabled"]))
    r = rs("PUT", f"{IPA}/policy", body={"enabled": False})
    ev = events("account.ip_allowlist_changed", "AND context->>'reason' = 'ip_allowlist_disabled'")
    record("disabling twice records one event", expect(r, 200) + ([] if len(ev) == 1 else [f"{len(ev)} events"]))
    for name, call in (("python", py), ("rust", rs)):
        r = call("GET", f"{API}/entitlements", ip=IPA_OUTSIDE)
        record(f"a disabled policy filters nobody on {name}", expect(r, 200))
    return results
def run_fx_section(args, fx: Fixture, db) -> list:
    """Multi-currency (new Rust-only routes + the Python order path): tests/contract/fx_cases.py."""
    import fx_cases  # noqa: PLC0415 - lives next to this file
    return fx_cases.run(args, fx, db, sys.modules[__name__])
# ── Session and password policy (Rust-only admin routes) ────────────────────
#
# The admin routes exist only in Rust, so there is nothing to diff them
# against: this section asserts their answers and the rows they write. What IS
# compared python-vs-rust is the TOKEN enforcement: the same token (a session
# past its maximum length, a person past the idle limit) must be refused with
# the same status and error code by both guards, and a background poll must
# leave the idle clock alone on both.

SP_FIELDS = ("max_session_hours", "idle_timeout_minutes", "min_password_length",
             "require_mixed_case", "require_symbol", "password_max_age_days",
             "max_concurrent_sessions", "lockout_threshold", "lockout_minutes")


class _SP:
    def __init__(self) -> None:
        self.results: list = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        self.results.append((Case(name, "-", "-", route="session policy"),
                             "PASS" if cond else "FAIL", [] if cond else [detail]))
        return cond


def _sp_ctx(raw):
    """An activity_logs context column as a dict (psycopg2 may hand back text)."""
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def run_session_policy(args, fx: Fixture, db) -> list:
    if db is None:
        return [(Case("session policy (all)", "-", "-", route="session policy"), "SKIP",
                 ["needs --db: the policy rows and the events are checked in the database"])]
    sp = _SP()
    cur = db.cursor()
    rs, py = args.rust, args.python
    url = f"{API}/session-policy"

    def admin():
        return auth_for(fx, "admin")  # a fresh token per call

    def row():
        cur.execute("SELECT " + ", ".join(SP_FIELDS) + ", password_max_age_since "
                    "FROM tenant_session_policies WHERE tenant_id = %s", (fx.tenant_id,))
        return cur.fetchone()

    def events(action):
        cur.execute("SELECT context, user_id, resource FROM activity_logs "
                    "WHERE tenant_id = %s AND action = %s ORDER BY created_at",
                    (fx.tenant_id, action))
        return cur.fetchall()

    def user(uid):
        cur.execute("SELECT failed_login_count, locked_until, last_activity_at "
                    "FROM users WHERE id = %s", (uid,))
        return cur.fetchone()

    def reset_policy_rows():
        cur.execute("DELETE FROM tenant_session_policies WHERE tenant_id = %s", (fx.tenant_id,))
        cur.execute("UPDATE users SET failed_login_count = 0, locked_until = NULL, "
                    "last_activity_at = NOW() WHERE tenant_id = %s", (fx.tenant_id,))

    def login(password=None):
        return http(py, "POST", f"{API}/auth/login", body={
            "email": fx.admin_email, "password": password or fx.admin_password})

    def refresh_count():
        cur.execute("SELECT COUNT(*) FROM refresh_tokens WHERE user_id = %s", (fx.admin_id,))
        return cur.fetchone()[0]

    reset_policy_rows()
    try:
        # ── permission pairs and the default view ──────────────────────────
        for who in ("viewer", "analyst"):
            for method, path, body in (("GET", url, None), ("PUT", url, {"max_session_hours": 8}),
                                       ("DELETE", url, None),
                                       ("POST", f"{url}/unlock/{fx.admin_id}", None)):
                r = http(rs, method, path, token=auth_for(fx, who), body=body)
                sp.check(f"{who} denied {method} {path.replace(API, '')}",
                         r.status == 403 and r.body.get("error_code") == "role_not_permitted"
                         and row() is None,
                         f"{r.status} {r.body}")
        r = http(rs, "GET", url, token=None)
        sp.check("no auth is 401", r.status == 401, f"{r.status}")
        for scope in ("read", "write"):
            key = getattr(fx, f"{scope}_key")
            if not key:
                continue
            r = http(rs, "PUT", url, token=key, body={"max_session_hours": 8})
            sp.check(f"{scope} key refused",
                     r.status == 403 and r.body.get("error_code") == "api_key_route_not_exposed"
                     and row() is None, f"{r.status} {r.body}")
        r = http(rs, "GET", url, token=admin())
        d = (r.body or {}).get("data", {})
        sp.check("default view", r.status == 200 and d.get("is_default") is True
                 and all(d["policy"][f] in (None, False) for f in SP_FIELDS)
                 and d["locked_users"] == [] and row() is None, f"{r.status} {r.body}")

        # ── validation: nothing is stored for a refused body ───────────────
        bad_bodies = [
            ("unknown field", {"max_session_hour": 8}, "session_policy_unknown_field"),
            ("hours too small", {"max_session_hours": 0}, "session_policy_out_of_range"),
            ("hours too big", {"max_session_hours": 169}, "session_policy_out_of_range"),
            ("idle too small", {"idle_timeout_minutes": 4}, "session_policy_out_of_range"),
            ("length too small", {"min_password_length": 7}, "session_policy_out_of_range"),
            ("length too big", {"min_password_length": 65}, "session_policy_out_of_range"),
            ("age too small", {"password_max_age_days": 6}, "session_policy_out_of_range"),
            ("sessions zero", {"max_concurrent_sessions": 0}, "session_policy_out_of_range"),
            ("threshold small", {"lockout_threshold": 2}, "session_policy_out_of_range"),
            ("string int", {"max_session_hours": "8"}, "session_policy_not_an_integer"),
            ("float int", {"max_session_hours": 8.5}, "session_policy_not_an_integer"),
            ("bool as int", {"max_session_hours": True}, "session_policy_not_an_integer"),
            ("string bool", {"require_symbol": "yes"}, "session_policy_not_a_boolean"),
            ("period without threshold", {"lockout_minutes": 10},
             "session_policy_lockout_needs_threshold"),
            ("wraps in 32 bits", {"max_session_hours": 4294967304}, "session_policy_out_of_range"),
        ]
        for label, body, code in bad_bodies:
            r = http(rs, "PUT", url, token=admin(), body=body)
            sp.check(f"PUT refused: {label}",
                     r.status == 422 and r.body.get("error_code") == code and row() is None,
                     f"{r.status} {r.body}")
        r = http(rs, "PUT", url, token=admin(), raw_body=b"[1]")
        sp.check("PUT refused: not an object", r.status == 422 and row() is None, f"{r.status}")

        # ── a valid replace, the row and the event ─────────────────────────
        full = {"max_session_hours": 8, "idle_timeout_minutes": 30, "min_password_length": 12,
                "require_mixed_case": True, "require_symbol": False, "password_max_age_days": 90,
                "max_concurrent_sessions": 3, "lockout_threshold": 5}
        r = http(rs, "PUT", url, token=admin(), body=full)
        d = (r.body or {}).get("data", {})
        stored = dict(zip(SP_FIELDS, row()[:9])) if row() else {}
        sp.check("PUT stores and echoes the policy",
                 r.status == 200 and d.get("is_default") is False
                 and stored == {**full, "lockout_minutes": 15}
                 and d["policy"]["lockout_minutes"] == 15 and d["updated_by"] == fx.admin_id,
                 f"{r.status} stored={stored}")
        ev = events("account.session_policy_changed")
        ctx = _sp_ctx(ev[0][0]) if ev else {}
        sp.check("PUT recorded one warning event naming the settings, no values",
                 len(ev) == 1 and ctx.get("severity") == "warning" and ctx.get("kind") == "account"
                 and ctx.get("reason") == "changed_by_an_account_admin"
                 and ctx.get("settings") == ["max_session_hours", "idle_timeout_minutes",
                                            "min_password_length", "password_max_age_days",
                                            "max_concurrent_sessions", "lockout_threshold",
                                            "lockout_minutes", "require_mixed_case"]
                 and ev[0][1] == fx.admin_id,
                 f"{ev}")
        since0 = row()[9] if row() else None
        sp.check("password age clock started", since0 is not None, "password_max_age_since is NULL")
        r = http(rs, "PUT", url, token=admin(), body=full)
        sp.check("an identical PUT records nothing and keeps the age clock",
                 r.status == 200 and len(events("account.session_policy_changed")) == 1
                 and row()[9] == since0, f"{r.status}")
        r = http(rs, "PUT", url, token=admin(), body={**full, "password_max_age_days": 60})
        sp.check("changing the age limit restarts the clock",
                 r.status == 200 and since0 is not None and row()[9] > since0
                 and len(events("account.session_policy_changed")) == 2,
                 f"{r.status} {row()}")
        r = http(rs, "PUT", url, token=admin(), body={})
        sp.check("an empty PUT clears every setting",
                 r.status == 200 and r.body["data"]["is_default"] is True
                 and all(v in (None, False) for v in row()[:9]) and row()[9] is None,
                 f"{r.status} {row()}")

        # ── token enforcement: the same verdict from both guards ───────────
        def both(name, token, headers=None, expect=None):
            rp = http(py, "GET", f"{API}/entitlements", token=token, headers=headers)
            rr = http(rs, "GET", f"{API}/entitlements", token=token, headers=headers)

            def code(x):
                return (x.body or {}).get("error_code")
            sp.check(f"{name}: python and rust agree",
                     rp.status == rr.status and code(rp) == code(rr)
                     and (expect is None or (rp.status, code(rp)) == expect),
                     f"python {rp.status} {code(rp)} / rust {rr.status} {code(rr)}")

        def token(**extra):
            return mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin", extra=extra)

        now = datetime.now(timezone.utc).timestamp()
        both("no policy: a 100-hour-old session is fine", token(sat=now - 100 * 3600),
             expect=(200, None))
        http(rs, "PUT", url, token=admin(), body={"max_session_hours": 8, "idle_timeout_minutes": 30})
        both("8h limit: iat 9h old", token(iat=now - 9 * 3600, exp=now + 900),
             expect=(401, "session_max_lifetime"))
        both("8h limit: iat 7h old", token(iat=now - 7 * 3600, exp=now + 900), expect=(200, None))
        both("8h limit: fresh iat but sat 9h old", token(sat=now - 9 * 3600),
             expect=(401, "session_max_lifetime"))
        both("8h limit: fresh iat, sat 7h old", token(sat=now - 7 * 3600), expect=(200, None))
        cur.execute("UPDATE users SET last_activity_at = NOW() - interval '31 minutes' WHERE id = %s",
                    (fx.admin_id,))
        both("30m idle limit: idle 31m", token(), expect=(401, "session_idle_timeout"))
        sp.check("a refused request is not activity",
                 user(fx.admin_id)[2] < datetime.now(timezone.utc) - timedelta(minutes=30),
                 f"{user(fx.admin_id)}")
        for side, base in (("python", py), ("rust", rs)):
            cur.execute("UPDATE users SET last_activity_at = NOW() - interval '10 minutes' "
                        "WHERE id = %s", (fx.admin_id,))
            before = user(fx.admin_id)[2]
            http(base, "GET", f"{API}/entitlements", token=token(),
                 headers={"X-StockAI-Background": "1"})
            sp.check(f"{side}: a background poll does not count as activity",
                     user(fx.admin_id)[2] == before, f"{user(fx.admin_id)[2]} vs {before}")
            http(base, "GET", f"{API}/entitlements", token=token())
            sp.check(f"{side}: a person's request counts as activity",
                     user(fx.admin_id)[2] > before, f"{user(fx.admin_id)[2]} vs {before}")
        key = fx.read_key
        if key:
            cur.execute("UPDATE users SET last_activity_at = NOW() - interval '9 hours' "
                        "WHERE id = %s", (fx.admin_id,))
            for side, base in (("python", py), ("rust", rs)):
                r = http(base, "GET", f"{API}/entitlements", token=key)
                sp.check(f"{side}: an API key ignores the session policy", r.status == 200,
                         f"{r.status} {r.body}")
            cur.execute("UPDATE users SET last_activity_at = NOW() WHERE id = %s", (fx.admin_id,))

        # ── Python login and refresh enforcement, state read back ──────────
        http(rs, "PUT", url, token=admin(), body={"max_concurrent_sessions": 1})
        l1, l2 = login(), login()
        sp.check("one concurrent session: the second login evicts the first",
                 l1.status == 200 and l2.status == 200 and refresh_count() == 1,
                 "refresh_tokens != 1")
        r_old = http(py, "POST", f"{API}/auth/refresh",
                     body={"refresh_token": l1.body["data"]["refresh_token"]})
        r_new = http(py, "POST", f"{API}/auth/refresh",
                     body={"refresh_token": l2.body["data"]["refresh_token"]})
        sp.check("the evicted session cannot refresh, the new one can",
                 r_old.status == 401 and r_new.status == 200, f"{r_old.status} {r_new.status}")

        http(rs, "PUT", url, token=admin(), body={"max_session_hours": 8})
        raw = login().body["data"]["refresh_token"]
        cur.execute("UPDATE refresh_tokens SET created_at = NOW() - interval '9 hours' "
                    "WHERE user_id = %s", (fx.admin_id,))
        held = refresh_count()
        r = http(py, "POST", f"{API}/auth/refresh", body={"refresh_token": raw})
        sp.check("refresh past the maximum session is refused and deletes that token",
                 r.status == 401 and r.body.get("error_code") == "session_max_lifetime"
                 and refresh_count() == held - 1, f"{r.status} {r.body} {held}->{refresh_count()}")
        r = http(py, "POST", f"{API}/auth/refresh", body={"refresh_token": raw})
        sp.check("the refused refresh token is now simply invalid",
                 r.status == 401 and r.body.get("error_code") == "refresh_token_invalid",
                 f"{r.status} {r.body}")

        # ── lockout: Python counts and locks, Rust lists and unlocks ───────
        http(rs, "PUT", url, token=admin(), body={"lockout_threshold": 3, "lockout_minutes": 20})
        for _ in range(3):
            login("Wrong-password-1")
        locked = user(fx.admin_id)
        sp.check("three wrong passwords lock the account",
                 locked[0] == 3 and locked[1] is not None, f"{locked}")
        r = login()
        sp.check("a locked account refuses the correct password",
                 r.status == 403 and r.body.get("error_code") == "account_locked"
                 and r.body["error_params"]["minutes"] in (19, 20), f"{r.status} {r.body}")
        r = http(rs, "GET", url, token=admin())
        lu = r.body["data"]["locked_users"]
        sp.check("Rust lists the locked person", len(lu) == 1 and lu[0]["user_id"] == fx.admin_id
                 and lu[0]["email"] == fx.admin_email and lu[0]["failed_attempts"] == 3, f"{lu}")
        sp.check("one lockout event", len(events("account.user_locked_out")) == 1,
                 f"{events('account.user_locked_out')}")
        cur.execute("SELECT id, failed_login_count, locked_until FROM users WHERE tenant_id <> %s "
                    "ORDER BY id LIMIT 1", (fx.tenant_id,))
        foreign = cur.fetchone()
        r = http(rs, "POST", f"{url}/unlock/usr_does_not_exist", token=admin())
        sp.check("unlock of an unknown user is 404", r.status == 404
                 and r.body.get("error_code") == "user_not_found", f"{r.status} {r.body}")
        if foreign:
            r = http(rs, "POST", f"{url}/unlock/{foreign[0]}", token=admin())
            cur.execute("SELECT id, failed_login_count, locked_until FROM users WHERE id = %s",
                        (foreign[0],))
            sp.check("unlock of another tenant's user is 404 and writes nothing",
                     r.status == 404 and cur.fetchone() == foreign, f"{r.status}")
        r = http(rs, "POST", f"{url}/unlock/{fx.admin_id}", token=admin())
        ue = events("account.user_unlocked")
        uctx = _sp_ctx(ue[0][0]) if ue else {}
        sp.check("unlock clears the lock and records who and for whom",
                 r.status == 200 and r.body["data"] == {"user_id": fx.admin_id, "unlocked": True}
                 and user(fx.admin_id)[:2] == (0, None) and len(ue) == 1
                 and uctx.get("email") == fx.admin_email and uctx.get("severity") == "warning"
                 and uctx.get("reason") == "changed_by_an_account_admin" and ue[0][1] == fx.admin_id
                 and ue[0][2] == fx.admin_id, f"{r.status} {r.body} {ue}")
        sp.check("the unlocked person signs in", login().status == 200, "login refused")
        r = http(rs, "POST", f"{url}/unlock/{fx.admin_id}", token=admin())
        sp.check("unlocking again is a no-op without a second event",
                 r.status == 200 and r.body["data"]["unlocked"] is False
                 and len(events("account.user_unlocked")) == 1, f"{r.status} {r.body}")
        for _ in range(3):
            login("Wrong-password-1")
        http(rs, "PUT", url, token=admin(), body={"max_session_hours": 8})
        sp.check("removing the lockout setting clears existing locks",
                 user(fx.admin_id)[:2] == (0, None) and login().status == 200,
                 f"{user(fx.admin_id)}")

        # ── password rules reach the Python endpoints ──────────────────────
        http(rs, "PUT", url, token=admin(), body={"min_password_length": 14, "require_symbol": True})
        cur.execute("SELECT hashed_password FROM users WHERE id = %s", (fx.admin_id,))
        before_hash = cur.fetchone()[0]
        reset = mint_access_token(fx.secret, fx.admin_id, fx.tenant_id, "admin",
                                  extra={"purpose": "password_reset"})
        for label, pw, broken in (("short and no symbol", "Abcdefg12345", ["min_length", "symbol"]),
                                  ("long, no symbol", "Abcdefghij12345", ["symbol"])):
            r = http(py, "POST", f"{API}/auth/reset-password",
                     body={"token": reset, "new_password": pw})
            cur.execute("SELECT hashed_password FROM users WHERE id = %s", (fx.admin_id,))
            sp.check(f"reset refused by the policy: {label}",
                     r.status == 400 and r.body.get("error_code") == "password_policy"
                     and r.body["error_params"]["broken"] == broken
                     and cur.fetchone()[0] == before_hash, f"{r.status} {r.body}")

        # ── DELETE returns to "no policy" ───────────────────────────────────
        n_events = len(events("account.session_policy_changed"))
        r = http(rs, "DELETE", url, token=admin())
        sp.check("DELETE removes the row, records the change, shows the default",
                 r.status == 200 and r.body["data"]["is_default"] is True and row() is None
                 and len(events("account.session_policy_changed")) == n_events + 1,
                 f"{r.status} {r.body}")
        r = http(rs, "DELETE", url, token=admin())
        sp.check("DELETE again records nothing",
                 r.status == 200 and len(events("account.session_policy_changed")) == n_events + 1,
                 f"{r.status}")
        # The audit trail shows the new actions on both services (LEGACY).
        r = http(rs, "GET", f"{API}/audit?target_type=session_policy", token=admin())
        items = (r.body or {}).get("data", {}).get("items", [])
        sp.check("the audit trail lists the policy changes",
                 r.status == 200 and items
                 and all(i["action"] == "session_policy.changed" for i in items),
                 f"{r.status} {items[:1]}")
        rp = http(py, "GET", f"{API}/audit?target_type=session_policy", token=admin())
        sp.check("python reads the same audit entries",
                 rp.status == 200 and len(rp.body["data"]["items"]) == len(items),
                 f"{rp.status}")
    finally:
        reset_policy_rows()
    return sp.results


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
        # R2 (sessions / schedule / spike edits) needs --db to seed its rows.
        ph = r2_prepare(args.python, args.rust, fx, db) if db is not None else {}
        for case in build_cases(fx) + (build_r2_cases(fx, ph) + build_w1b_session_cases(fx, ph) + build_w1b_manifest_cases(fx, ph) if ph else []):
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
            for k, (v_py, v_rs) in ph.items():
                path_py, path_rs = path_py.replace(k, v_py), path_rs.replace(k, v_rs)
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
        results += run_r3(args, fx, db)
        results += run_r4(args, fx, db)
        results += run_ip_allowlist(args, fx, db)
        results += run_cd_resync(args, fx, db)
        results += run_outbox(args, fx, db)
        import w2b_cases  # noqa: PLC0415 - wave 2b section, its own file
        results += w2b_cases.run_w2b(args, secret, db)
        import w3_cases  # wave 3 (inventory hub): its own file, its own tenants
        results += w3_cases.run_w3(args, fx, db, sys.modules[__name__])
        results += run_delegation(args, fx, db)
        results += cf_outlook_cases.run_cf(args, fx, db, sys.modules[__name__])
        results += run_portal(args, fx, db)
        # MFA (tests/contract/mfa_cases.py): Rust-only routes + the Python login challenge.
        from mfa_cases import run_mfa  # noqa: PLC0415
        results += run_mfa(sys.modules[__name__], args, fx, db)
        results += run_fx_section(args, fx, db)
        # Custom roles (Rust-only routes + enforcement on both): own file.
        from custom_roles_cases import run_custom_roles
        results += run_custom_roles(args, fx, db)
        results += run_session_policy(args, fx, db)
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
        extra = "".join(f", {verdicts.count(v)} {v.lower()}" for v in ("STALE", "SKIP") if v in verdicts)
        print(f"  {route:<36} {verdicts.count('PASS')}/{len(verdicts)} pass{extra}")
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
    ap.add_argument("--allow-stale", action="store_true",
                    help="R3: accept a Python answer that predates the source when Rust matches the source")
    ap.add_argument("--rust-strict", default=None,
                    help="MFA: URL of a second Rust API started with TESTING_MODE=false (throttle cases)")
    ap.add_argument("--erase-orphans", action="store_true",
                    help="only erase throwaway tenants left by a crashed run, then exit")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
