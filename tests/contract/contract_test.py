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


# ── R3: webhooks CRUD, API keys, audit trail reads ───────────────────────────
#
# A sequence rather than a flat list: keys minted by one service are used on
# the other, revoked on one and tried on both, and the audit reads run before
# the exports (an export writes an audit row the next read would show).
#
# Verdicts: PASS, FAIL, SKIP (cannot be exercised here, says why) and STALE:
# the running Python process predates the Python SOURCE for that behaviour
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
            if stale_spec is not None:
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
                               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s, %s, NOW(), NOW() - %s * INTERVAL '1 minute')""",
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
        results += run_r3(args, fx, db)
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
    ap.add_argument("--erase-orphans", action="store_true",
                    help="only erase throwaway tenants left by a crashed run, then exit")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
