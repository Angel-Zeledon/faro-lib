"""MFA section of the contract harness: enrollment / policy (Rust only) and the
Python login challenge.

Kept in its own module so the shared `contract_test.py` only gains one call.
`run_mfa(ct, args, fx, db)` receives the harness module as `ct` (http, Case,
auth_for, make_fixture ... live there), which also avoids an import cycle when
the harness runs as `__main__`.

The enrollment and policy routes have no Python twin, so there is nothing to
diff: each case asserts the Rust answer AND the rows it wrote, straight from
the database. The cross-service cases prove what Rust enrols, Python verifies
at sign-in (TOTP, replay across services, recovery codes, the enrollment token
a required-MFA tenant's user receives at login).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time

API = "/api/v1"


def totp(secret_b32: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    val = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(val % 10 ** digits).zfill(digits)


def step(offset: int = 0) -> int:
    return int(time.time() // 30) + offset


def recovery_hash(secret_key: str, code: str) -> str:
    norm = "".join(c for c in code.upper() if c not in " -\t\r\n")
    return hmac.new(secret_key.encode(), ("mfa-recovery:" + norm).encode(), hashlib.sha256).hexdigest()


def run_mfa(ct, args, fx, db) -> list:
    results: list = []
    if db is None:
        results.append((ct.Case("mfa: needs --db", "-", "-", route="mfa"), "SKIP", ["--db not given"]))
        return results
    py, rs = args.python, args.rust
    secret_key = fx.secret

    def q(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []

    def rec(name, route, problems):
        results.append((ct.Case(name, "-", "-", route=route), "FAIL" if problems else "PASS", problems))

    def expect(problems, label, cond):
        if not cond:
            problems.append(label)

    def call(method, path, who=None, body=None, base=None, headers=None):
        tok = ct.auth_for(fx, who) if who else None
        return ct.http(base or rs, method, f"{API}{path}", token=tok, body=body, headers=headers)

    def code_of(r):
        return r.body.get("error_code") if isinstance(r.body, dict) else None

    def events(tenant_id, action, user_id=None):
        rows = q("SELECT context, resource, user_id FROM activity_logs WHERE tenant_id=%s AND action=%s",
                 (tenant_id, action))
        return [r for r in rows if user_id is None or r[2] == user_id]

    def wrong_code(secret):
        return "000000" if totp(secret, step()) != "000000" else "111111"

    def py_login(f):
        return call("POST", "/auth/login", base=py, body={"email": f.admin_email, "password": f.admin_password})

    fx2 = None
    try:
        # ── access control and the permission pairs ──────────────────────────
        p = []
        r = call("GET", "/mfa/status")
        expect(p, f"no token: {r.status}", r.status == 401 and r.headers.get("www-authenticate") == "Bearer")
        for who in ("viewer", "analyst", "admin"):
            r = call("GET", "/mfa/status", who)
            expect(p, f"status {who}: {r.status}", r.status == 200)
            if r.status == 200:
                d = r.body["data"]
                expect(p, f"status {who} body {d}", d["enrolled"] is False and d["pending"] is False
                       and d["required_by_tenant"] is False and d["recovery_codes_remaining"] == 0)
        rec("mfa status: any signed-in role, nothing enrolled", "GET /mfa/status", p)

        p = []
        for who in ("key_read", "key_write"):
            if fx.token(who):
                for method, path in (("GET", "/mfa/status"), ("POST", "/mfa/enroll/begin"), ("GET", "/mfa/policy")):
                    r = call(method, path, who)
                    expect(p, f"{who} {method} {path}: {r.status} {code_of(r)}",
                           r.status == 403 and code_of(r) == "api_key_route_not_exposed")
        if not (fx.token("key_read") or fx.token("key_write")):
            results.append((ct.Case("mfa: API keys are refused on every route", "-", "-", route="mfa (keys)"),
                            "SKIP", ["no API key in this tenant"]))
        else:
            rec("mfa: API keys are refused on every route", "mfa (keys)", p)

        p = []
        for who in ("viewer", "analyst"):
            r = call("GET", "/mfa/policy", who)
            expect(p, f"GET policy {who}: {r.status} {code_of(r)}", r.status == 403 and code_of(r) == "role_not_permitted")
            r = call("PUT", "/mfa/policy", who, {"required": True})
            expect(p, f"PUT policy {who}: {r.status}", r.status == 403)
            r = call("POST", f"/mfa/users/{fx.admin_id}/reset", who)
            expect(p, f"reset by {who}: {r.status}", r.status == 403)
        expect(p, "policy unchanged by denied calls",
               q("SELECT mfa_required FROM tenants WHERE id=%s", (fx.tenant_id,))[0][0] is False)
        r = call("GET", "/mfa/policy", "admin")
        expect(p, f"GET policy admin: {r.status}", r.status == 200 and r.body["data"]["required"] is False)
        if r.status == 200:
            ids = {u["id"] for u in r.body["data"]["users"]}
            expect(p, "policy lists the tenant's users", {fx.admin_id, fx.analyst_id, fx.viewer_id} <= ids)
        rec("mfa policy: viewer and analyst denied, admin allowed, state unchanged", "GET/PUT /mfa/policy", p)

        # ── a policy that would lock its author out is refused ───────────────
        p = []
        r = call("PUT", "/mfa/policy", "admin", {"required": True})
        expect(p, f"require before enrolling: {r.status} {code_of(r)}",
               r.status == 409 and code_of(r) == "mfa_admin_not_enrolled")
        expect(p, "tenant not changed",
               q("SELECT mfa_required FROM tenants WHERE id=%s", (fx.tenant_id,))[0][0] is False)
        for body in ({}, {"required": "maybe"}):
            r = call("PUT", "/mfa/policy", "admin", body)
            expect(p, f"PUT policy {body}: {r.status} {code_of(r)}",
                   r.status == 422 and code_of(r) == "validation_error")
        rec("mfa policy: refused until the acting admin is enrolled; validation", "PUT /mfa/policy", p)

        # ── the analyst enrols (begin, wrong code, confirm) ──────────────────
        p = []
        r = call("POST", "/mfa/enroll/confirm", "analyst", {"code": "123456"})
        expect(p, f"confirm before begin: {r.status} {code_of(r)}",
               r.status == 409 and code_of(r) == "mfa_enrollment_not_started")
        r = call("POST", "/mfa/enroll/begin", "analyst")
        expect(p, f"begin: {r.status} {r.body if r.status != 200 else ''}", r.status == 200)
        a_secret = r.body["data"]["secret"] if r.status == 200 else ""
        if r.status == 200:
            d = r.body["data"]
            expect(p, "otpauth uri", d["otpauth_uri"].startswith("otpauth://totp/StockAI:")
                   and f"secret={a_secret}" in d["otpauth_uri"] and d["digits"] == 6 and d["period"] == 30)
            row = q("SELECT status, secret_enc, last_used_step FROM user_mfa WHERE user_id=%s", (fx.analyst_id,))
            expect(p, f"pending row {row}", len(row) == 1 and row[0][0] == "pending" and row[0][2] is None)
            if row:
                expect(p, "secret stored encrypted, not in the clear",
                       a_secret not in row[0][1] and row[0][1].startswith("gAAAA"))
                ik = os.environ.get("INTEGRATIONS_SECRET_KEY") or (
                    ct.read_env_file(args.env_file).get("INTEGRATIONS_SECRET_KEY") if args.env_file else None)
                if ik:
                    try:
                        from cryptography.fernet import Fernet  # noqa: PLC0415
                        expect(p, "Fernet (Python) decrypts what Rust encrypted",
                               Fernet(ik.encode()).decrypt(row[0][1].encode()).decode() == a_secret)
                    except ImportError:
                        pass
        r = call("POST", "/mfa/enroll/confirm", "analyst", {"code": wrong_code(a_secret)})
        expect(p, f"wrong confirm: {r.status} {code_of(r)}", r.status == 401 and code_of(r) == "mfa_code_invalid")
        expect(p, "still pending after a wrong code",
               q("SELECT status FROM user_mfa WHERE user_id=%s", (fx.analyst_id,))[0][0] == "pending")
        r = call("POST", "/mfa/enroll/confirm", "analyst", {})
        expect(p, f"confirm without a code: {r.status}", r.status == 422)
        rec("mfa enroll: begin stores an encrypted pending secret; a wrong code does not activate",
            "POST /mfa/enroll/begin|confirm", p)

        p = []
        r = call("POST", "/mfa/enroll/confirm", "analyst", {"code": totp(a_secret, step())})
        expect(p, f"confirm: {r.status} {r.body if r.status != 200 else ''}", r.status == 200)
        a_codes = r.body["data"]["recovery_codes"] if r.status == 200 else []
        expect(p, f"ten distinct recovery codes {a_codes}",
               len(set(a_codes)) == 10 and all(len(c) == 11 for c in a_codes))
        rows = q("SELECT status, last_used_step, confirmed_at FROM user_mfa WHERE user_id=%s", (fx.analyst_id,))
        expect(p, f"active row {rows}",
               len(rows) == 1 and rows[0][0] == "active" and rows[0][1] is not None and rows[0][2] is not None)
        hashes = {h[0] for h in q("SELECT code_hash FROM user_mfa_recovery_codes WHERE user_id=%s AND used_at IS NULL",
                                  (fx.analyst_id,))}
        expect(p, "only HMACs of the codes are stored", hashes == {recovery_hash(secret_key, c) for c in a_codes})
        expect(p, "no plaintext code in the table",
               not any(c.replace("-", "") in h for c in a_codes for h in hashes))
        expect(p, "enrolled event", len(events(fx.tenant_id, "account.mfa_enrolled", fx.analyst_id)) == 1)
        r = call("POST", "/mfa/enroll/begin", "analyst")
        expect(p, f"begin when active: {r.status} {code_of(r)}", r.status == 409 and code_of(r) == "mfa_already_enrolled")
        r = call("POST", "/mfa/enroll/confirm", "analyst", {"code": totp(a_secret, step())})
        expect(p, f"confirm when active: {r.status}", r.status == 409)
        r = call("GET", "/mfa/status", "analyst")
        expect(p, f"status after: {r.body}",
               r.status == 200 and r.body["data"]["enrolled"] and r.body["data"]["recovery_codes_remaining"] == 10
               and r.body["data"]["can_disable"] is True)
        rec("mfa enroll: confirm activates, returns ten recovery codes once, stores only their HMACs",
            "POST /mfa/enroll/confirm", p)

        # ── admin enrols, then Python signs the admin in through the challenge ──
        p = []
        r = call("POST", "/mfa/enroll/begin", "admin")
        adm_secret = r.body["data"]["secret"]
        r = call("POST", "/mfa/enroll/confirm", "admin", {"code": totp(adm_secret, step())})
        expect(p, f"admin confirm: {r.status}", r.status == 200)
        adm_codes = r.body["data"]["recovery_codes"] if r.status == 200 else []
        lg = py_login(fx)
        expect(p, f"python login: {lg.status} {lg.body if lg.status != 200 else ''}", lg.status == 200)
        ld = lg.body["data"] if lg.status == 200 else {}
        expect(p, f"challenge, no tokens {sorted(ld)}", ld.get("mfa_required") is True and "access_token" not in ld)
        # The confirm used this step: the SAME code cannot sign in (replay across the services).
        v = call("POST", "/auth/mfa/verify", base=py,
                 body={"mfa_token": ld.get("mfa_token", ""), "code": totp(adm_secret, step())})
        expect(p, f"replay of the confirm code: {v.status} {code_of(v)}",
               v.status == 401 and code_of(v) == "mfa_code_invalid")
        # The next step is a different code and opens the door.
        lg = py_login(fx)
        v = call("POST", "/auth/mfa/verify", base=py,
                 body={"mfa_token": lg.body["data"]["mfa_token"], "code": totp(adm_secret, step(1))})
        expect(p, f"verify: {v.status} {v.body if v.status != 200 else ''}", v.status == 200)
        if v.status == 200:
            who = ct.http(rs, "GET", f"{API}/mfa/status", token=v.body["data"]["access_token"])
            expect(p, f"the Python-issued token works on Rust: {who.status}",
                   who.status == 200 and who.body["data"]["enrolled"] is True)
        rec("mfa cross-service: what Rust enrols Python verifies (TOTP, replay across services)",
            "POST /auth/mfa/verify", p)

        # ── regenerate with a recovery code as re-auth ───────────────────────
        p = []
        r = call("POST", "/mfa/recovery-codes/regenerate", "admin", {"code": "000000"})
        expect(p, f"regenerate wrong code: {r.status} {code_of(r)}", r.status == 401 and code_of(r) == "mfa_code_invalid")
        old_hashes = {h[0] for h in q("SELECT code_hash FROM user_mfa_recovery_codes WHERE user_id=%s", (fx.admin_id,))}
        r = call("POST", "/mfa/recovery-codes/regenerate", "admin", {"code": adm_codes[0].lower()})
        expect(p, f"regenerate with a recovery code: {r.status}", r.status == 200)
        new_codes = r.body["data"]["recovery_codes"] if r.status == 200 else []
        now_hashes = {h[0] for h in q(
            "SELECT code_hash FROM user_mfa_recovery_codes WHERE user_id=%s AND used_at IS NULL", (fx.admin_id,))}
        expect(p, "the old set is gone and the new one stored",
               len(new_codes) == 10 and not (old_hashes & now_hashes)
               and now_hashes == {recovery_hash(secret_key, c) for c in new_codes})
        r = call("POST", "/mfa/recovery-codes/regenerate", "admin", {"code": adm_codes[0]})
        expect(p, f"the spent recovery code is dead: {r.status}", r.status == 401)
        r = call("POST", "/mfa/recovery-codes/regenerate", "viewer", {"code": "123456"})
        expect(p, f"not enrolled: {r.status} {code_of(r)}", r.status == 409 and code_of(r) == "mfa_not_enrolled")
        expect(p, "regenerated event",
               len(events(fx.tenant_id, "account.mfa_recovery_codes_regenerated", fx.admin_id)) == 1)
        rec("mfa recovery codes: regenerate replaces the set; re-auth required; spent code dead",
            "POST /mfa/recovery-codes/regenerate", p)

        # a recovery code from the NEW set signs in through Python, once
        p = []
        lg = py_login(fx)
        v = call("POST", "/auth/mfa/verify", base=py,
                 body={"mfa_token": lg.body["data"]["mfa_token"], "code": new_codes[0]})
        expect(p, f"python accepts a Rust-issued recovery code: {v.status}", v.status == 200)
        lg = py_login(fx)
        v = call("POST", "/auth/mfa/verify", base=py,
                 body={"mfa_token": lg.body["data"]["mfa_token"], "code": new_codes[0]})
        expect(p, f"...and only once: {v.status}", v.status == 401)
        expect(p, "recovery-code-used event with a reason", any(
            e[0].get("reason") == "recovery_code_used_to_sign_in"
            for e in events(fx.tenant_id, "account.mfa_recovery_code_used", fx.admin_id)))
        rec("mfa cross-service: a recovery code works once", "POST /auth/mfa/verify", p)

        # ── the policy: requiring MFA ────────────────────────────────────────
        p = []
        viewer_old = ct.mint_access_token(fx.secret, fx.viewer_id, fx.tenant_id, "viewer")
        time.sleep(1.1)
        r = call("PUT", "/mfa/policy", "admin", {"required": True})
        expect(p, f"require: {r.status} {r.body if r.status != 200 else ''}", r.status == 200)
        expect(p, "tenant now requires MFA",
               q("SELECT mfa_required FROM tenants WHERE id=%s", (fx.tenant_id,))[0][0] is True)
        cut = {row[0]: row[1] for row in q(
            "SELECT id, sessions_invalid_before FROM users WHERE tenant_id=%s", (fx.tenant_id,))}
        expect(p, "unenrolled users' sessions were cut", cut[fx.viewer_id] is not None)
        expect(p, "enrolled users' sessions were not", cut[fx.admin_id] is None and cut[fx.analyst_id] is None)
        r = ct.http(rs, "GET", f"{API}/mfa/status", token=viewer_old)
        expect(p, f"an older viewer token is refused: {r.status}", r.status == 401)
        r = call("GET", "/mfa/status", "viewer")
        expect(p, f"a fresh viewer token works and shows the policy: {r.status}",
               r.status == 200 and r.body["data"]["required_by_tenant"] is True)
        expect(p, "policy event with a reason", any(
            e[0].get("reason") == "changed_by_an_account_admin" and e[0].get("mfa_required") is True
            for e in events(fx.tenant_id, "account.mfa_policy_changed", fx.admin_id)))
        aud = q("SELECT context FROM activity_logs WHERE tenant_id=%s AND action='audit.config.changed' AND user_id=%s",
                (fx.tenant_id, fx.admin_id))
        expect(p, f"audit row {aud}",
               any(a[0].get("path") == "/mfa/policy" and a[0].get("after") == {"mfa_required": True} for a in aud))
        r = call("PUT", "/mfa/policy", "admin", {"required": True})
        expect(p, f"idempotent repeat: {r.status}", r.status == 200)
        expect(p, "a repeat is no new change event",
               len(events(fx.tenant_id, "account.mfa_policy_changed", fx.admin_id)) == 1)
        r = call("POST", "/mfa/disable", "analyst", {"code": "123456"})
        expect(p, f"cannot disable while required: {r.status} {code_of(r)}",
               r.status == 403 and code_of(r) == "mfa_required_by_tenant")
        expect(p, "still enrolled", q("SELECT status FROM user_mfa WHERE user_id=%s", (fx.analyst_id,))[0][0] == "active")
        rec("mfa policy: require, ends unenrolled sessions, audited, blocks disable", "PUT /mfa/policy", p)

        # ── admin reset of a locked-out user ─────────────────────────────────
        p = []
        analyst_old = ct.mint_access_token(fx.secret, fx.analyst_id, fx.tenant_id, "analyst")
        time.sleep(1.1)
        r = call("POST", f"/mfa/users/{fx.admin_id}/reset", "admin")
        expect(p, f"reset self: {r.status} {code_of(r)}", r.status == 400 and code_of(r) == "mfa_reset_self_refused")
        r = call("POST", "/mfa/users/does-not-exist/reset", "admin")
        expect(p, f"unknown user: {r.status}", r.status == 404)
        r = call("POST", f"/mfa/users/{fx.viewer_id}/reset", "admin")
        expect(p, f"not enrolled: {r.status} {code_of(r)}", r.status == 409 and code_of(r) == "mfa_not_enrolled")
        r = call("POST", f"/mfa/users/{fx.analyst_id}/reset", "admin")
        expect(p, f"reset: {r.status}", r.status == 200 and r.body["data"]["reset"] == fx.analyst_id)
        for table in ("user_mfa", "user_mfa_recovery_codes"):
            expect(p, f"{table} rows gone", q(f"SELECT COUNT(*) FROM {table} WHERE user_id=%s", (fx.analyst_id,))[0][0] == 0)
        expect(p, "session cut",
               q("SELECT sessions_invalid_before FROM users WHERE id=%s", (fx.analyst_id,))[0][0] is not None)
        r = ct.http(rs, "GET", f"{API}/mfa/status", token=analyst_old)
        expect(p, f"the analyst's older token is dead: {r.status}", r.status == 401)
        ev = events(fx.tenant_id, "account.mfa_reset", fx.admin_id)
        expect(p, f"reset event {ev}", any(
            e[1] == fx.analyst_id and e[0].get("reason") == "mfa_reset_by_an_account_admin" for e in ev))
        aud = q("SELECT context, resource FROM activity_logs WHERE tenant_id=%s AND action='audit.user.mfa_reset'",
                (fx.tenant_id,))
        expect(p, f"audit row {aud}",
               len(aud) == 1 and aud[0][1] == fx.analyst_id and aud[0][0]["path"] == "/mfa/users/{user_id}/reset")
        rec("mfa reset: admin clears a user, ends their sessions, audited", "POST /mfa/users/{id}/reset", p)

        # ── a required-MFA tenant: the enrollment token, end to end ──────────
        fx2 = ct.make_fixture(py, secret_key)
        q("UPDATE tenants SET mfa_required = TRUE WHERE id=%s", (fx2.tenant_id,))
        p = []
        sessions_before = q("SELECT COUNT(*) FROM refresh_tokens WHERE user_id=%s", (fx2.admin_id,))[0][0]
        lg = py_login(fx2)
        ld = lg.body["data"] if lg.status == 200 else {}
        expect(p, f"login in a required tenant: {lg.status} {sorted(ld)}",
               ld.get("mfa_enrollment_required") is True and "access_token" not in ld)
        etok = ld.get("enrollment_token", "")
        expect(p, "no session was created",
               q("SELECT COUNT(*) FROM refresh_tokens WHERE user_id=%s", (fx2.admin_id,))[0][0] == sessions_before)
        r = call("POST", "/mfa/enroll/begin", body={"enrollment_token": "nope"})
        expect(p, f"unknown enrollment token: {r.status} {code_of(r)}",
               r.status == 401 and code_of(r) == "mfa_challenge_invalid")
        r = call("POST", "/mfa/enroll/begin", body={"enrollment_token": etok})
        expect(p, f"begin with the token: {r.status}", r.status == 200)
        e_secret = r.body["data"]["secret"] if r.status == 200 else ""
        r = call("POST", "/mfa/enroll/confirm", body={"enrollment_token": etok, "code": wrong_code(e_secret)})
        expect(p, f"wrong code: {r.status}", r.status == 401 and code_of(r) == "mfa_code_invalid")
        expect(p, "the token spent a guess",
               q("SELECT attempts FROM mfa_challenges WHERE user_id=%s", (fx2.admin_id,))[0][0] == 1)
        r = call("POST", "/mfa/enroll/confirm", body={"enrollment_token": etok, "code": totp(e_secret, step())})
        expect(p, f"confirm with the token: {r.status}", r.status == 200 and r.body["data"]["sign_in_again"] is True)
        e_codes = r.body["data"]["recovery_codes"] if r.status == 200 else []
        expect(p, "the enrollment token is spent", q(
            "SELECT consumed_at FROM mfa_challenges WHERE user_id=%s AND purpose='enroll'", (fx2.admin_id,))[0][0] is not None)
        r = call("POST", "/mfa/enroll/begin", body={"enrollment_token": etok})
        expect(p, f"a spent token is refused: {r.status}", r.status == 401)
        lg = py_login(fx2)
        expect(p, "now the login is a normal challenge", lg.status == 200 and lg.body["data"].get("mfa_required") is True)
        v = call("POST", "/auth/mfa/verify", base=py,
                 body={"mfa_token": lg.body["data"]["mfa_token"], "code": totp(e_secret, step(1))})
        expect(p, f"and the new factor signs in: {v.status}", v.status == 200 and "refresh_token" in v.body["data"])
        rec("mfa required tenant: login -> enrollment token -> enrol -> challenge -> session",
            "POST /mfa/enroll/* (token)", p)

        p = []
        # five wrong guesses burn an enrollment token
        q("UPDATE user_mfa SET status='pending' WHERE user_id=%s", (fx2.admin_id,))
        q("DELETE FROM mfa_challenges WHERE user_id=%s", (fx2.admin_id,))
        raw = secrets.token_urlsafe(48)
        q("INSERT INTO mfa_challenges (token_hash, user_id, tenant_id, purpose, expires_at) "
          "VALUES (%s,%s,%s,'enroll', NOW() + INTERVAL '10 minutes')",
          (hashlib.sha256(raw.encode()).hexdigest(), fx2.admin_id, fx2.tenant_id))
        for i in range(5):
            r = call("POST", "/mfa/enroll/confirm", body={"enrollment_token": raw, "code": "000000"})
            expect(p, f"guess {i + 1}: {r.status}", r.status == 401 and code_of(r) == "mfa_code_invalid")
        r = call("POST", "/mfa/enroll/confirm", body={"enrollment_token": raw, "code": totp(e_secret, step(1))})
        expect(p, f"the sixth try is refused even with the right code: {r.status} {code_of(r)}",
               r.status == 401 and code_of(r) == "mfa_challenge_invalid")
        expect(p, "and nothing was activated",
               q("SELECT status FROM user_mfa WHERE user_id=%s", (fx2.admin_id,))[0][0] == "pending")
        # an admin of ANOTHER tenant can neither reset nor see this user
        q("UPDATE user_mfa SET status='active' WHERE user_id=%s", (fx2.admin_id,))
        r = call("POST", f"/mfa/users/{fx2.admin_id}/reset", "admin")
        expect(p, f"cross-tenant reset is a 404: {r.status}", r.status == 404)
        expect(p, "and the other tenant's enrollment survives",
               q("SELECT COUNT(*) FROM user_mfa WHERE user_id=%s", (fx2.admin_id,))[0][0] == 1)
        rec("mfa enrollment token: five guesses and it is dead; tenant scope on reset",
            "POST /mfa/enroll/confirm, reset", p)

        # ── lifting the policy and disabling ─────────────────────────────────
        p = []
        r = call("PUT", "/mfa/policy", "admin", {"required": False})
        expect(p, f"lift: {r.status} {r.body}", r.status == 200 and r.body["data"]["sessions_ended"] == 0)
        expect(p, "tenant no longer requires MFA",
               q("SELECT mfa_required FROM tenants WHERE id=%s", (fx.tenant_id,))[0][0] is False)
        r = call("POST", "/mfa/disable", "admin", {"code": "000000"})
        expect(p, f"disable with a wrong code: {r.status} {code_of(r)}", r.status == 401 and code_of(r) == "mfa_code_invalid")
        expect(p, "still enrolled", q("SELECT COUNT(*) FROM user_mfa WHERE user_id=%s", (fx.admin_id,))[0][0] == 1)
        r = call("POST", "/mfa/disable", "admin", {"code": new_codes[1]})
        expect(p, f"disable with a recovery code: {r.status}", r.status == 200 and r.body["data"]["enrolled"] is False)
        for table in ("user_mfa", "user_mfa_recovery_codes"):
            expect(p, f"{table} rows gone", q(f"SELECT COUNT(*) FROM {table} WHERE user_id=%s", (fx.admin_id,))[0][0] == 0)
        ev = events(fx.tenant_id, "account.mfa_disabled", fx.admin_id)
        expect(p, f"disabled event with a reason {ev}", any(e[0].get("reason") == "mfa_disabled_by_the_user" for e in ev))
        lg = py_login(fx)
        expect(p, "Python login is password-only again", lg.status == 200 and "access_token" in lg.body["data"])
        r = call("POST", "/mfa/disable", "admin", {"code": "123456"})
        expect(p, f"disable when not enrolled: {r.status} {code_of(r)}",
               r.status == 409 and code_of(r) == "mfa_not_enrolled")
        rec("mfa disable: re-auth with a code, rows deleted, login back to password-only", "POST /mfa/disable", p)

        # ── the throttle (needs a Rust started with TESTING_MODE=false) ──────
        strict = getattr(args, "rust_strict", None)
        if not strict:
            results.append((ct.Case("mfa throttle: needs --rust-strict", "-", "-", route="mfa (throttle)"),
                            "SKIP", ["pass --rust-strict URL of a Rust API running with TESTING_MODE=false"]))
        else:
            p = []
            q("DELETE FROM auth_rate_events WHERE key=%s", (f"mfa:{fx2.admin_id}",))
            for i in range(10):
                r = ct.http(strict, "POST", f"{API}/mfa/recovery-codes/regenerate",
                            token=ct.auth_for(fx2, "admin"), body={"code": "000000"})
                expect(p, f"guess {i + 1}: {r.status} {code_of(r)}", r.status == 401 and code_of(r) == "mfa_code_invalid")
            r = ct.http(strict, "POST", f"{API}/mfa/recovery-codes/regenerate",
                        token=ct.auth_for(fx2, "admin"), body={"code": e_codes[0]})
            expect(p, f"the 11th is throttled even with a valid recovery code: {r.status} {code_of(r)}",
                   r.status == 429 and code_of(r) == "too_many_attempts")
            expect(p, "the throttled call burned no recovery code", q(
                "SELECT COUNT(*) FROM user_mfa_recovery_codes WHERE user_id=%s AND used_at IS NULL", (fx2.admin_id,))[0][0] == 10)
            expect(p, "ten events in the shared bucket", q(
                "SELECT COUNT(*) FROM auth_rate_events WHERE key=%s", (f"mfa:{fx2.admin_id}",))[0][0] == 10)
            # `mfa:<user_id>` is the key Python's /auth/mfa/verify throttles on too (test_mfa.py asserts it).
            q("DELETE FROM auth_rate_events WHERE key=%s", (f"mfa:{fx2.admin_id}",))
            rec("mfa throttle: ten guesses per user per ten minutes, then 429 even for a valid code",
                "POST /mfa/recovery-codes/regenerate (throttle)", p)
    finally:
        if fx2 is not None:
            ct.erase_fixture(py, fx2)
    return results
