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
    r = http(py, "DELETE", f"{API}/tenant", token=fx.admin_token, body={"confirm": "DELETE"})
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


@dataclass
class R4Env:
    fx: Fixture
    db: Any
    py: str
    norte_id: str = ""
    other_tenant: Optional[Fixture] = None
    other_po: str = ""
    pairs: dict = field(default_factory=dict)


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
    cur.execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, recommended_qty, final_qty, received_qty, status)
           VALUES (%s, %s, %s, %s, %s, %s, 'approved')""",
        (po_id, tenant_id, sku, spec.get("qty", 10), spec.get("qty", 10), spec.get("received")))
    return po_id


def _r4_pair(env: R4Env, name: str, spec: dict) -> dict:
    if name not in env.pairs:
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
            elif k in ("paid_at", "cancelled_at", "updated_at") and v is not None:
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
    return value


def _r4_subs(env: R4Env, po_id: Optional[str]) -> dict:
    """Strings to replace anywhere (the side's PO id and its OC reference),
    plus the side's PO number, replaced only under a `po_number` key."""
    if not po_id:
        return {}
    subs = {po_id: "<po>"}
    n = _r4_number(env.db, po_id)
    if n:
        subs["__number__"] = n
        subs[f"OC-{int(n):06d}"] = "<ref>"
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
    for who, ids in (("scoped", [env.norte_id]), ("scoped_none", [])):
        r = http(args.python, "POST", f"{API}/users", token=fx.admin_token, body={
            "email": f"contract-{secrets.token_hex(3)}-{who}@stockai.demo".replace("_", "-"),
            "role": "analyst", "full_name": f"Contract {who}"})
        if r.status != 201:
            raise SystemExit(f"creating the {who} analyst failed: {r.status} {r.body}")
        uid = r.body["data"]["user"]["id"]
        r = http(args.python, "PUT", f"{API}/users/{uid}/warehouse-scope", token=fx.admin_token,
                 body={"warehouse_ids": ids})
        if r.status != 200:
            raise SystemExit(f"scoping the {who} analyst failed: {r.status} {r.body}")
        fx.tokens[who] = mint_access_token(fx.secret, uid, fx.tenant_id, "analyst")
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
        R4Case("cancel with reason", "POST", f"{po}/cancel", who="analyst", po=("cancel-seq", sent),
               body={"reason": "  supplier closed  "}, capture=r4_capture_po_history("cancelled"),
               route=cancel),
        R4Case("cancel again is idempotent", "POST", f"{po}/cancel", po=("cancel-seq", sent),
               body={"reason": "another"}, capture=cap, route=cancel),
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
                r = http(base, case.method, path, token=token, body=case.body,
                         raw_body=case.raw_body, content_type=case.content_type)
                resps[side] = r
                masked[side] = _r4_mask(r.body, _r4_subs(env, po_id))
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
            kw = dict(token=token, body=case.body, raw_body=case.raw_body,
                      content_type=case.content_type)
            rp = http(args.python, case.method, path_py, **kw)
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
        results += run_r4(args, fx, db)
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
