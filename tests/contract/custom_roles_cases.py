"""Contract cases for custom roles.

Hooked into `contract_test.py` by `run_custom_roles(args, fx, db)`; also runnable
on its own (`python tests/contract/custom_roles_cases.py --python ... --rust ...
--db ...`, same flags).

The role CRUD and assignment routes exist ONLY in Rust, so there is nothing to
diff them against: each case asserts the answer AND the database (row, audit
row with before/after, activity event). Where a rule is enforced on BOTH
servers (a permission that a custom role lacks), the same request is sent to
Python and to Rust and the two answers are diffed, which is the parity proof
for the shared catalogue.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_test as ct  # noqa: E402
from contract_test import API, Case, diff, http, mint_access_token, normalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads((ROOT / "backend" / "auth" / "permissions.json").read_text(encoding="utf-8"))
ALL_PERMS = [p["name"] for p in CATALOGUE["permissions"]]


def run_custom_roles(args, fx, db) -> list:
    py, rs = args.python, args.rust
    out: list = []
    if db is None:
        return [(Case("custom roles (all)", "-", "-", route="roles"), "SKIP",
                 ["custom roles need --db: rows, audit and activity are checked in the database"])]

    def qa(sql, params=()):
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def q1(sql, params=()):
        rows = qa(sql, params)
        return rows[0] if rows else None

    def case(name, route, problems):
        if args.only and args.only not in name:
            return
        hard = [p for p in problems if not p.startswith("(")]
        out.append((Case(name, "-", "-", route=route), "FAIL" if hard else "PASS", problems))

    def code(r):
        return r.body.get("error_code") if isinstance(r.body, dict) else None

    def data(r):
        return r.body.get("data") if isinstance(r.body, dict) else None

    def expect(r, status, error=None, params=None):
        p = []
        if r.status != status:
            p.append(f"status {r.status} (wanted {status}) body={json.dumps(r.body)[:200]}")
        elif error is not None and code(r) != error:
            p.append(f"error_code {code(r)!r} (wanted {error!r})")
        if params is not None and isinstance(r.body, dict) and r.body.get("error_params") != params:
            p.append(f"error_params {r.body.get('error_params')!r} (wanted {params!r})")
        return p

    tid = fx.tenant_id
    tag = secrets.token_hex(3)

    def fresh(who, uid, role):
        fx.tokens[who] = mint_access_token(fx.secret, uid, tid, role, exp_minutes=120)

    for who, uid, role in (("admin", fx.admin_id, "admin"), ("analyst", fx.analyst_id, "analyst"),
                           ("viewer", fx.viewer_id, "viewer")):
        fresh(who, uid, role)
    admin = fx.tokens["admin"]

    def make_user(label, role):
        r = http(py, "POST", f"{API}/users", token=admin, body={
            "email": f"roles-{tag}-{label}@stockai.demo", "role": role, "full_name": f"Roles {label}"})
        if r.status != 201:
            raise SystemExit(f"custom roles: could not create {label}: {r.status} {r.body}")
        uid = r.body["data"]["user"]["id"]
        fx.tokens[label] = mint_access_token(fx.secret, uid, tid, role, exp_minutes=120)
        return uid

    editor_id = make_user("editor", "admin")      # becomes a restricted role editor
    plain_id = make_user("plainadmin", "admin")   # restricted without manage_roles
    target_id = make_user("target", "analyst")    # the person roles are assigned to

    def roles_count():
        return q1("SELECT COUNT(*) FROM custom_roles WHERE tenant_id = %s", (tid,))[0]

    def role_row(name):
        return q1("SELECT id, permissions, created_by, tenant_id FROM custom_roles "
                  "WHERE tenant_id = %s AND lower(name) = lower(%s)", (tid, name))

    def user_role(uid):
        return q1("SELECT custom_role_id FROM users WHERE id = %s", (uid,))[0]

    def audit_rows(action, target_id_=None):
        sql = ("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s")
        params = [tid, f"audit.{action}"]
        if target_id_:
            sql += " AND resource = %s"
            params.append(target_id_)
        return [r[0] for r in qa(sql + " ORDER BY created_at", tuple(params))]

    def events(action, resource=None):
        sql = "SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s"
        params = [tid, action]
        if resource:
            sql += " AND resource = %s"
            params.append(resource)
        return [r[0] for r in qa(sql, tuple(params))]

    def post_role(who, name, perms, desc=None):
        body = {"name": name, "permissions": perms}
        if desc is not None:
            body["description"] = desc
        return http(rs, "POST", f"{API}/roles", token=fx.tokens[who], body=body)

    def assign(who, uid, role_id):
        return http(rs, "PUT", f"{API}/users/{uid}/custom-role", token=fx.tokens[who],
                    body={"custom_role_id": role_id})

    def both(method, path, who, body=None):
        a = http(py, method, path, token=fx.tokens[who], body=body)
        b = http(rs, method, path, token=fx.tokens[who], body=body)
        problems = []
        if a.status != b.status:
            problems.append(f"status python={a.status} rust={b.status}")
        problems += diff(normalize(a.body, set()), normalize(b.body, set()))
        return a, b, problems

    R = "roles"

    # ── catalogue and guards ────────────────────────────────────────────────
    r = http(rs, "GET", f"{API}/roles/permissions", token=admin)
    names = [p["name"] for p in (data(r) or {}).get("permissions", [])]
    case("roles: catalogue served by Rust equals the Python catalogue file", R,
         expect(r, 200) + ([] if names == ALL_PERMS else [f"names {names} != {ALL_PERMS}"]))

    for who in ("analyst", "viewer", "none"):
        r = http(rs, "GET", f"{API}/roles", token=fx.tokens.get(who))
        case(f"roles: list refused for {who}", R, expect(r, 401 if who == "none" else 403,
             None if who == "none" else "role_not_permitted"))
    for scope in ("key_read", "key_write"):
        if fx.tokens.get(scope):
            r = http(rs, "GET", f"{API}/roles", token=fx.tokens[scope])
            case(f"roles: an API key ({scope}) can never read roles", R,
                 expect(r, 403, "api_key_route_not_exposed"))

    before = roles_count()
    for who in ("analyst", "viewer"):
        r = post_role(who, f"Nope {who}", ["view_users"])
        case(f"roles: create refused for {who}, nothing written", R,
             expect(r, 403, "role_not_permitted") + ([] if roles_count() == before else ["a row was written"]))

    # ── create ──────────────────────────────────────────────────────────────
    r = post_role("admin", "  Buyers  ", ["view_inventory", "manage_inventory", "view_inventory"], "Buys things")
    row = role_row("Buyers")
    probs = expect(r, 200)
    if row is None:
        probs.append("no custom_roles row")
    else:
        if sorted(row[1]) != ["manage_inventory", "view_inventory"]:
            probs.append(f"stored permissions {row[1]} (deduplicated and sorted expected)")
        if row[2] != fx.admin_id or row[3] != tid:
            probs.append(f"created_by/tenant {row[2]}/{row[3]}")
    buyers = row[0] if row else None
    aud = audit_rows("role.created", buyers)
    if len(aud) != 1 or aud[0].get("after", {}).get("permissions") != ["manage_inventory", "view_inventory"] \
            or aud[0].get("before") is not None or aud[0].get("target_type") != "role":
        probs.append(f"audit row {aud}")
    if len(events("account.custom_role_created", buyers)) != 1:
        probs.append("no account.custom_role_created event")
    case("roles: create writes the row, an audit row with after, and an event", R, probs)

    r = post_role("admin", "buyers", ["view_users"])
    case("roles: duplicate name (any case) is refused", R,
         expect(r, 409, "custom_role_name_taken") + ([] if roles_count() == before + 1 else ["row count moved"]))
    r = post_role("admin", "Typo", ["view_users", "root"])
    case("roles: an unknown permission is refused, never dropped", R,
         expect(r, 422, "custom_role_unknown_permission", {"permission": "root"})
         + ([] if role_row("Typo") is None else ["a row was written"]))
    for label, body in (("no permissions field", {"name": "NoPerms"}),
                        ("blank name", {"name": "   ", "permissions": []}),
                        ("permissions not a list", {"name": "X1", "permissions": "view_users"}),
                        ("empty body", {})):
        r = http(rs, "POST", f"{API}/roles", token=admin, body=body)
        case(f"roles: invalid body ({label})", R, expect(r, 422, "validation_error"))
    r = post_role("admin", "Empty role", [])
    case("roles: an empty role is allowed (a way to park somebody on read-only-open)", R, expect(r, 200))

    # ── list / get / cross tenant ───────────────────────────────────────────
    r = http(rs, "GET", f"{API}/roles", token=admin)
    listed = {x["name"]: x for x in (data(r) or {}).get("roles", [])}
    case("roles: list shows the roles with their user counts", R,
         expect(r, 200) + ([] if "Buyers" in listed and listed["Buyers"]["user_count"] == 0
                           else [f"listed {list(listed)}"]))
    fxb = ct.make_fixture(py, fx.secret)
    try:
        r = http(rs, "GET", f"{API}/roles/{buyers}", token=fxb.admin_token)
        case("roles: another tenant gets 404 on read", R, expect(r, 404, "custom_role_not_found"))
        r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=fxb.admin_token, body={"name": "Hijacked"})
        case("roles: another tenant cannot edit (404, row unchanged)", R,
             expect(r, 404, "custom_role_not_found") + ([] if role_row("Buyers") else ["name changed"]))
        r = http(rs, "DELETE", f"{API}/roles/{buyers}", token=fxb.admin_token)
        case("roles: another tenant cannot delete (404, row kept)", R,
             expect(r, 404, "custom_role_not_found") + ([] if role_row("Buyers") else ["row deleted"]))
        r = http(rs, "PUT", f"{API}/users/{fxb.analyst_id}/custom-role", token=fxb.admin_token,
                 body={"custom_role_id": buyers})
        case("roles: another tenant's role cannot be assigned (404)", R,
             expect(r, 404, "custom_role_not_found") + ([] if user_role(fxb.analyst_id) is None else ["assigned"]))
        r = http(rs, "PUT", f"{API}/users/{target_id}/custom-role", token=fxb.admin_token,
                 body={"custom_role_id": None})
        case("roles: another tenant's user cannot be touched (404)", R, expect(r, 404, "user_not_found"))
    finally:
        ct.erase_fixture(py, fxb)

    # ── update, with before/after ───────────────────────────────────────────
    r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=admin,
             body={"permissions": ["view_inventory", "export_data"], "description": "Buys and exports"})
    probs = expect(r, 200)
    aud = audit_rows("role.updated", buyers)
    if len(aud) != 1 or aud[0]["before"]["permissions"] != ["manage_inventory", "view_inventory"] \
            or aud[0]["after"]["permissions"] != ["export_data", "view_inventory"]:
        probs.append(f"audit before/after {aud}")
    if len(events("account.custom_role_updated", buyers)) != 1:
        probs.append("no account.custom_role_updated event")
    case("roles: update audits before and after", R, probs)
    r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=admin, body={})
    case("roles: an empty patch is refused", R, expect(r, 422, "custom_role_nothing_to_change"))
    r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=admin, body={"name": "Empty role"})
    case("roles: renaming onto an existing name is refused", R, expect(r, 409, "custom_role_name_taken"))
    r = http(rs, "PATCH", f"{API}/roles/role-nope", token=admin, body={"name": "Z"})
    case("roles: patching a missing role is 404", R, expect(r, 404, "custom_role_not_found"))

    # ── assignment and enforcement on BOTH servers ─────────────────────────
    r = assign("admin", target_id, buyers)
    probs = expect(r, 200)
    if user_role(target_id) != buyers:
        probs.append("users.custom_role_id not set")
    aud = audit_rows("user.role_assigned", target_id)
    if len(aud) != 1 or aud[0]["before"] != {"custom_role": None} \
            or aud[0]["after"]["custom_role"]["id"] != buyers:
        probs.append(f"audit before/after {aud}")
    if len(events("account.custom_role_assigned", target_id)) != 1:
        probs.append("no account.custom_role_assigned event")
    case("assign: sets the column and audits before/after", R, probs)

    r = http(rs, "GET", f"{API}/roles/me", token=fx.tokens["target"])
    d = data(r) or {}
    case("assign: /roles/me tells the person what they may do", R,
         expect(r, 200) + ([] if d.get("restricted") is True and d.get("permissions") == ["export_data", "view_inventory"]
                           and d.get("custom_role", {}).get("name") == "Buyers" else [f"me={d}"]))
    r = http(rs, "GET", f"{API}/roles/me", token=admin)
    d = data(r) or {}
    case("assign: an unrestricted admin sees the whole catalogue in /roles/me", R,
         expect(r, 200) + ([] if d.get("restricted") is False and d.get("permissions") == sorted(ALL_PERMS)
                           else [f"me={d}"]))

    a, b, probs = both("GET", f"{API}/inventory/signal-thresholds", "target")
    case("enforce: a held permission passes on Python and Rust alike", "GET /inventory/signal-thresholds",
         probs + ([] if a.status == 200 and b.status == 200 else [f"statuses {a.status}/{b.status}"]))
    a, b, probs = both("PUT", f"{API}/inventory/signal-thresholds", "target", body={"thresholds": {}})
    case("enforce: a missing permission is the SAME 403 on Python and Rust", "PUT /inventory/signal-thresholds",
         probs + expect(a, 403, "permission_denied", {"permission": "manage_settings"})
         + expect(b, 403, "permission_denied", {"permission": "manage_settings"}))
    r = http(rs, "POST", f"{API}/inventory/po/po-x/mark-paid", token=fx.tokens["target"], body={})
    case("enforce: a Rust-served mutating route is guarded too (manage_inventory missing)",
         "POST /inventory/po/{id}/mark-paid", expect(r, 403, "permission_denied", {"permission": "manage_inventory"}))
    r = http(py, "POST", f"{API}/inventory/warehouses", token=fx.tokens["target"], body={"name": f"RB-{tag}"})
    case("enforce: Python-served write refused and nothing written", "POST /inventory/warehouses",
         expect(r, 403, "permission_denied", {"permission": "manage_settings"})
         + ([] if q1("SELECT 1 FROM warehouses WHERE tenant_id = %s AND name = %s", (tid, f"RB-{tag}")) is None
            else ["warehouse written"]))

    r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=admin, body={"permissions": []})
    a, b, probs = both("GET", f"{API}/inventory/signal-thresholds", "target")
    case("enforce: a permission removed is felt on the NEXT request, same token, both servers",
         "GET /inventory/signal-thresholds",
         expect(r, 200) + probs + expect(a, 403, "permission_denied") + expect(b, 403, "permission_denied"))
    r = http(rs, "PATCH", f"{API}/roles/{buyers}", token=admin, body={"permissions": ["view_inventory", "manage_inventory"]})
    a, b, probs = both("GET", f"{API}/inventory/signal-thresholds", "target")
    case("enforce: and a permission added is felt just as fast", "GET /inventory/signal-thresholds",
         expect(r, 200) + probs + expect(a, 200) + expect(b, 200))

    a, b, probs = both("GET", f"{API}/users/me", "target")
    case("enforce: open routes stay open for a restricted user", "GET /users/me",
         probs + expect(a, 200) + expect(b, 200) if b.status != 404 else expect(a, 200))

    r = http(rs, "DELETE", f"{API}/roles/{buyers}", token=admin)
    case("roles: a role in use cannot be deleted (unassigning would widen people)", R,
         expect(r, 409, "custom_role_in_use", {"users": 1}) + ([] if role_row("Buyers") else ["row deleted"]))

    # ── self-protection ─────────────────────────────────────────────────────
    r = assign("admin", fx.admin_id, buyers)
    case("assign: nobody assigns a role to themselves", R,
         expect(r, 403, "custom_role_self_assign") + ([] if user_role(fx.admin_id) is None else ["self-assigned"]))
    for who in ("analyst", "viewer"):
        r = assign(who, target_id, None)
        case(f"assign: refused for {who}, row unchanged", R,
             expect(r, 403, "role_not_permitted") + ([] if user_role(target_id) == buyers else ["role changed"]))
    r = assign("admin", target_id, "role-nope")
    case("assign: an unknown role is 404", R, expect(r, 404, "custom_role_not_found"))
    r = assign("admin", "usr-nope", buyers)
    case("assign: an unknown user is 404", R, expect(r, 404, "user_not_found"))
    r = http(rs, "PUT", f"{API}/users/{target_id}/custom-role", token=admin, body={})
    case("assign: the body must name custom_role_id (null clears it)", R, expect(r, 422, "validation_error"))

    # ── privilege escalation ────────────────────────────────────────────────
    r = post_role("admin", "Role manager", ["manage_roles", "view_users", "view_inventory"])
    mgr_role = (role_row("Role manager") or [None])[0]
    r2 = assign("admin", editor_id, mgr_role)
    case("setup: the editor is a restricted role manager", R, expect(r, 200) + expect(r2, 200))
    r = post_role("admin", "Small", ["view_inventory"])
    small = (role_row("Small") or [None])[0]
    r = post_role("admin", "Big", ["manage_settings", "view_inventory"])
    big = (role_row("Big") or [None])[0]
    r = post_role("admin", "Nothing", ["view_users"])
    only_users = (role_row("Nothing") or [None])[0]
    assign("admin", plain_id, only_users)

    n = roles_count()
    r = post_role("editor", "Mine", ["manage_roles", "manage_settings"])
    case("escalation: a role editor cannot create a role above their own set", R,
         expect(r, 403, "custom_role_escalation", {"permissions": ["manage_settings"]})
         + ([] if roles_count() == n else ["a row was written"]))
    r = post_role("editor", "Within", ["view_users"])
    case("escalation: a role inside their own set is fine", R, expect(r, 200))
    r = http(rs, "PATCH", f"{API}/roles/{mgr_role}", token=fx.tokens["editor"],
             body={"permissions": list(ALL_PERMS)})
    case("escalation: granting themselves more by editing their own role is refused", R,
         expect(r, 403, "custom_role_self_edit")
         + ([] if sorted(q1("SELECT permissions FROM custom_roles WHERE id = %s", (mgr_role,))[0])
            == sorted(["manage_roles", "view_users", "view_inventory"]) else ["role changed"]))
    r = http(rs, "DELETE", f"{API}/roles/{mgr_role}", token=fx.tokens["editor"])
    case("escalation: deleting their own role is refused", R, expect(r, 403, "custom_role_self_edit"))
    r = http(rs, "PATCH", f"{API}/roles/{big}", token=fx.tokens["editor"], body={"permissions": []})
    case("escalation: a role above their own cannot be edited (that would reduce somebody else's)", R,
         expect(r, 403, "custom_role_escalation")
         + ([] if sorted(q1("SELECT permissions FROM custom_roles WHERE id = %s", (big,))[0])
            == ["manage_settings", "view_inventory"] else ["role changed"]))
    r = http(rs, "DELETE", f"{API}/roles/{big}", token=fx.tokens["editor"])
    case("escalation: nor deleted", R, expect(r, 403, "custom_role_escalation") + ([] if role_row("Big") else ["deleted"]))
    r = assign("editor", fx.analyst_id, big)
    case("escalation: assigning a bigger role than their own is refused", R,
         expect(r, 403, "custom_role_escalation") + ([] if user_role(fx.analyst_id) is None else ["assigned"]))
    r = assign("editor", fx.viewer_id, small)
    case("escalation: a restricted editor cannot touch somebody with NO role (they hold everything)", R,
         expect(r, 403, "custom_role_escalation") + ([] if user_role(fx.viewer_id) is None else ["assigned"]))
    # An unrestricted admin limits the analyst; the editor may then move them
    # between roles that sit inside the editor's own set.
    assign("admin", fx.analyst_id, small)
    within = (role_row("Within") or [None])[0]
    r = assign("editor", fx.analyst_id, within)
    case("escalation: moving a role holder to a role inside their own set works", R,
         expect(r, 200) + ([] if user_role(fx.analyst_id) == within else ["not assigned"]))
    r = assign("editor", fx.analyst_id, None)
    case("escalation: removing a role is widening, so it needs the whole catalogue (refused for the restricted editor)",
         R, expect(r, 403, "custom_role_escalation") + ([] if user_role(fx.analyst_id) == within else ["role removed"]))
    r = assign("editor", fx.admin_id, small)
    case("escalation: a restricted editor cannot demote the unrestricted admin", R,
         expect(r, 403, "custom_role_escalation") + ([] if user_role(fx.admin_id) is None else ["admin restricted"]))
    r = assign("editor", editor_id, small)
    case("escalation: nor reassign themselves", R, expect(r, 403, "custom_role_self_assign"))
    r = post_role("plainadmin", "Sneaky", ["view_users"])
    case("escalation: a restricted admin without manage_roles is stopped by the guard", R,
         expect(r, 403, "permission_denied", {"permission": "manage_roles"})
         + ([] if role_row("Sneaky") is None else ["a row was written"]))
    r = http(rs, "GET", f"{API}/roles", token=fx.tokens["plainadmin"])
    case("escalation: view_users may read the roles", R, expect(r, 200))
    r = http(py, "POST", f"{API}/users", token=fx.tokens["editor"], body={
        "email": f"roles-{tag}-x@stockai.demo", "role": "viewer", "full_name": "X"})
    case("escalation: a role manager without manage_users cannot invite (Python route)", R,
         expect(r, 403, "permission_denied", {"permission": "manage_users"})
         + ([] if q1("SELECT 1 FROM users WHERE email = %s", (f"roles-{tag}-x@stockai.demo",)) is None
            else ["user created"]))

    # ── last-admin lockout ──────────────────────────────────────────────────
    # The only other unrestricted admin is suspended (its token still lives for
    # 15 minutes): assigning a role to the last ACTIVE unrestricted admin must
    # be refused, and allowed once another one is active.
    fxc = ct.make_fixture(py, fx.secret)
    try:
        r = http(py, "POST", f"{API}/users", token=fxc.admin_token, body={
            "email": f"roles-{tag}-b@stockai.demo", "role": "admin", "full_name": "B"})
        b_id = r.body["data"]["user"]["id"]
        b_token = mint_access_token(fx.secret, b_id, fxc.tenant_id, "admin")
        rr = http(rs, "POST", f"{API}/roles", token=fxc.admin_token, body={"name": "Limited", "permissions": ["view_users"]})
        limited = data(rr)["id"]
        # With B active, A may limit B...
        r = http(rs, "PUT", f"{API}/users/{b_id}/custom-role", token=fxc.admin_token, body={"custom_role_id": limited})
        case("lockout: limiting an admin is fine while another active admin stays unrestricted", R,
             expect(r, 200))
        r = http(rs, "PUT", f"{API}/users/{b_id}/custom-role", token=fxc.admin_token, body={"custom_role_id": None})
        ok_clear = expect(r, 200)
        # ...now B is unrestricted but suspended, and acts with a still-valid token.
        cur = db.cursor()
        cur.execute("UPDATE users SET status = 'inactive' WHERE id = %s", (b_id,))
        r = http(rs, "PUT", f"{API}/users/{fxc.admin_id}/custom-role", token=b_token, body={"custom_role_id": limited})
        stuck = cur.execute("SELECT custom_role_id FROM users WHERE id = %s", (fxc.admin_id,)) or cur.fetchone()[0]
        case("lockout: the last active unrestricted admin cannot be limited", R,
             ok_clear + expect(r, 409, "custom_role_last_admin") + ([] if stuck is None else ["admin was limited"]))
    finally:
        ct.erase_fixture(py, fxc)

    # ── the audit trail reads the new rows the same way on both servers ────
    a = http(py, "GET", f"{API}/audit?target_type=role&limit=50", token=admin)
    b = http(rs, "GET", f"{API}/audit?target_type=role&limit=50", token=admin)
    probs = [] if a.status == b.status == 200 else [f"statuses {a.status}/{b.status}"]
    probs += diff(normalize(a.body, set()), normalize(b.body, set()))
    n_rows = len((data(a) or {}).get("items", (data(a) or {}).get("entries", []))) if isinstance(data(a), dict) else len(data(a) or [])
    if n_rows < 3:
        probs.append(f"only {n_rows} role rows in the trail")
    case("audit: the trail shows the role rows identically from Python and Rust", "GET /audit", probs)
    a = http(py, "GET", f"{API}/audit/filters", token=admin)
    b = http(rs, "GET", f"{API}/audit/filters", token=admin)
    case("audit: the filter vocabulary (new target and actions) matches", "GET /audit/filters",
         diff(normalize(a.body, set()), normalize(b.body, set())))

    # ── clean up what this section made, so the tenant erasure is the real test
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    secret = os.environ.get("SECRET_KEY") or ct.read_env_file(args.env_file).get("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY not found")
    import psycopg2
    db = psycopg2.connect(args.db)
    db.autocommit = True
    fx = ct.make_fixture(args.python, secret)
    try:
        results = run_custom_roles(args, fx, db)
    finally:
        if not args.keep:
            ct.erase_fixture(args.python, fx)
    width = max(len(c.name) for c, _, _ in results)
    for c, verdict, problems in results:
        print(f"{verdict:4}  {c.name:<{width}}  {c.route}")
        for p in problems:
            print(f"        - {p}")
    failed = sum(1 for _, v, _ in results if v == "FAIL")
    print(f"\n{len(results) - failed}/{len(results)} cases pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
