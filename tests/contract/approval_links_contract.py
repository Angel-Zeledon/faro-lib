"""Contract cases for "approve or reject a purchase order from a message".

Hooked into `contract_test.py` with a few lines (`run_approval_links`). Unlike
the other groups there is NO Python twin to diff against: the public page, the
decision endpoint and the issue/revoke hooks exist in Rust only. So this file
checks the Rust service over real HTTP against a real database, and checks the
INTEROP with Python, which is the point of a strangler migration:

* links issued by the PYTHON code (what `POST /approval/request` does when
  APPROVAL_LINKS_ENABLED is on) are accepted by Rust, and links issued by
  Rust are queued in the outbox kinds the Python worker renders;
* a decision taken in the app through Python (or Rust) revokes the links, and
  Rust then answers the neutral 404;
* the state each call leaves is read straight from the database.

Needs `--db` and a Rust service started with `APPROVAL_LINKS_ENABLED=true`
(without it the cases say so and are skipped; one case asserts the switch-off
answer). Does not need the Python API to have the switch on.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from typing import Any, Callable


def run_approval_links(args, fx, db, ns: dict) -> list:
    """`ns` is contract_test's own helpers: http, Case, API, auth_for, signup."""
    http: Callable = ns["http"]
    Case = ns["Case"]
    auth_for: Callable = ns["auth_for"]
    API: str = ns["API"]
    rs, py = args.rust, args.python

    out: list = []

    def verdict(name: str, problems: list[str], *, skip: str | None = None):
        if skip:
            out.append((Case(name, "-", "-", route="approval-links"), "SKIP", [skip]))
        else:
            out.append((Case(name, "-", "-", route="approval-links"), "FAIL" if problems else "PASS", problems))

    if db is None:
        return [(Case("approval links (all)", "-", "-", route="approval-links"), "SKIP",
                 ["approval links need --db: tokens, links and decisions are checked in the database"])]
    if args.only and args.only not in "approval links":
        return []

    cur = db.cursor()

    def q(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []

    def one(sql, params=()):
        rows = q(sql, params)
        return rows[0][0] if rows else None

    tenant = fx.tenant_id

    # ── fixture: a requester (the fixture analyst), two approvers, a rule ─────
    def add_approver(role="analyst"):
        uid = f"usr_al{secrets.token_hex(5)}"
        q("""INSERT INTO users (id, tenant_id, email, hashed_password, role, status, can_approve_po, email_verified, full_name)
             VALUES (%s, %s, %s, 'x', %s, 'active', TRUE, TRUE, %s)""",
          (uid, tenant, f"{uid}@stockai.demo", role, f"Approver {role}"))
        return uid, f"{uid}@stockai.demo"

    a1, a1_email = add_approver("analyst")
    a2, a2_email = add_approver("analyst")
    requester = fx.analyst_id
    q("DELETE FROM po_approval_rules WHERE tenant_id = %s", (tenant,))
    q("INSERT INTO po_approval_rules (tenant_id, threshold, created_by) VALUES (%s, 5000, 'contract')", (tenant,))

    def seed_po(tenant_id=tenant, requested_by=requester, amount=6000.0, destination=None):
        po = one("""INSERT INTO inventory_po_log (tenant_id, sku_count, total_units, po_number, destination_warehouse, approval_status)
                    VALUES (%s, 1, 20, (SELECT COALESCE(MAX(po_number), 0) + 1 FROM inventory_po_log WHERE tenant_id = %s),
                            %s, 'pending_approval') RETURNING id""", (tenant_id, tenant_id, destination))
        q("""INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, display_name, supplier, recommended_qty, final_qty,
                                             unit_cost, status, warehouse)
             VALUES (%s, %s, 'AL-LEAK-SKU', 'Widget', 'Acme', 20, 20, 301.23, 'approved', 'principal')""", (po, tenant_id))
        ap = one("""INSERT INTO po_approvals (tenant_id, po_log_id, status, amount, requested_by, request_note)
                    VALUES (%s, %s, 'requested', %s, %s, 'contract') RETURNING id""", (tenant_id, po, amount, requested_by))
        return po, ap

    def state(po, ap):
        return {
            "approval": q("SELECT status, decided_by, decided_channel FROM po_approvals WHERE id = %s", (ap,)),
            "po": q("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = %s", (po,)),
            "links": q("SELECT approver_id, channel, token_hash, used_at IS NOT NULL, used_decision, revoked_at IS NOT NULL, "
                       "revoked_reason FROM po_approval_links WHERE po_log_id = %s ORDER BY approver_id, channel", (po,)),
            "activity": one("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s AND resource = %s", (tenant, po)),
            "outbox": one("SELECT COUNT(*) FROM outbound_messages WHERE tenant_id = %s", (tenant,)),
        }

    def hook(path, who="analyst", base=None, method="POST"):
        return http(base or rs, method, f"{API}{path}", token=auth_for(fx, who), body={} if method == "POST" else None)

    def issue_via_rust(po):
        return hook(f"/inventory/po/{po}/approval/links")

    def token_from_outbox(po, email):
        row = q("""SELECT params->>'decision_token' FROM outbound_messages
                    WHERE tenant_id = %s AND recipient = %s AND params->>'po_log_id' = %s AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1""", (tenant, email, po))
        return row[0][0] if row else None

    def view(token, method="GET", base=None):
        return http(base or rs, method, f"{API}/approval-links/{token}")

    def decide(token, body, base=None, method="POST"):
        return http(base or rs, method, f"{API}/approval-links/{token}/decision", body=body)

    neutral_body = None

    def is_neutral(r, label, problems):
        nonlocal neutral_body
        if r.status != 404 or not isinstance(r.body, dict) or r.body.get("error_code") != "approval_link_not_found":
            problems.append(f"{label}: expected the neutral 404, got {r.status} {str(r.body)[:120]}")
            return
        shown = {k: v for k, v in r.body.items()}
        if neutral_body is None:
            neutral_body = shown
        elif shown != neutral_body:
            problems.append(f"{label}: the neutral answer differs between cases: {shown} vs {neutral_body}")

    po1, ap1 = seed_po()

    # ── switch and permission pair on the hooks ───────────────────────────────
    r = issue_via_rust(po1)
    if r.status == 409 and (r.body or {}).get("error_code") == "approval_links_disabled":
        verdict("approval links: the issue hook says so when the switch is off", [])
        verdict("approval links (the rest)", [], skip="start the Rust service with APPROVAL_LINKS_ENABLED=true to run the rest")
        q("DELETE FROM inventory_po_log WHERE id = %s", (po1,))
        return out
    problems = []
    if r.status != 200:
        problems.append(f"issue as analyst: {r.status} {r.body}")
    else:
        d = r.body["data"]
        if d.get("approvers") != 2 or d.get("links") != 2 or d.get("queued", {}).get("email") != 2:
            problems.append(f"issue answer: {d}")
    verdict("approval links: analyst issues one link per approver, never for the requester", problems)

    problems = []
    n_links = one("SELECT COUNT(*) FROM po_approval_links WHERE po_log_id = %s", (po1,))
    if n_links != 2:
        problems.append(f"{n_links} link rows, expected 2")
    if one("SELECT COUNT(*) FROM po_approval_links WHERE po_log_id = %s AND approver_id = %s", (po1, requester)):
        problems.append("a link was issued for the requester")
    t1, t2 = token_from_outbox(po1, a1_email), token_from_outbox(po1, a2_email)
    if not t1 or not t2 or t1 == t2 or len(t1) != 43:
        problems.append(f"tokens in the outbox: {t1!r} {t2!r}")
    else:
        import hashlib
        stored = {r_[0]: r_[2] for r_ in state(po1, ap1)["links"]}
        if stored.get(a1) != hashlib.sha256(t1.encode()).hexdigest():
            problems.append("the stored hash is not the hash of the queued token")
        if q("SELECT 1 FROM po_approval_links WHERE row(po_approval_links.*)::text LIKE %s", (f"%{t1}%",)):
            problems.append("the raw token is in the links table")
    kinds = {r_[0] for r_ in q("SELECT kind FROM outbound_messages WHERE tenant_id = %s AND params->>'po_log_id' = %s",
                               (tenant, po1))}
    if kinds != {"po_approval_request"}:
        problems.append(f"outbox kinds: {kinds}")
    verdict("approval links: stored hashed, bound, queued in the registered outbox kind", problems)

    problems = []
    for who, want in (("viewer", 403), (None, 401)):
        before = state(po1, ap1)
        r = http(rs, "POST", f"{API}/inventory/po/{po1}/approval/links", token=auth_for(fx, who) if who else None, body={})
        if r.status != want:
            problems.append(f"issue as {who}: {r.status}, expected {want}")
        if state(po1, ap1) != before:
            problems.append(f"issue as {who} changed state")
        r = http(rs, "POST", f"{API}/inventory/po/{po1}/approval/links/revoke", token=auth_for(fx, who) if who else None, body={})
        if r.status != want:
            problems.append(f"revoke as {who}: {r.status}, expected {want}")
        if state(po1, ap1) != before:
            problems.append(f"revoke as {who} changed state")
    verdict("approval links: permission pairs on issue and revoke (viewer and anonymous denied, state unchanged)", problems)

    # ── the page: reads only, whitelist, headers ──────────────────────────────
    problems = []
    before = state(po1, ap1)
    r = view(t1)
    if r.status != 200:
        problems.append(f"GET: {r.status} {r.body}")
    else:
        data = r.body["data"]
        want_keys = {"amount", "approver_name", "buyer", "can_approve", "can_reject", "comment_max", "currency", "expires_at",
                     "line_count", "lines", "note", "reference", "requested_at", "requested_by_name", "suppliers", "warehouse"}
        if set(data) != want_keys:
            problems.append(f"page keys differ: extra={set(data) - want_keys} missing={want_keys - set(data)}")
        if data.get("amount") != 6000.0 or data["lines"][0] != {"sku": "AL-LEAK-SKU", "name": "Widget", "quantity": 20.0, "supplier": "Acme"}:
            problems.append(f"page content: {data}")
        text = json.dumps(r.body)
        for forbidden in ("301.23", "unit_cost", "6024", po1, ap1, tenant, a1):
            if forbidden in text:
                problems.append(f"the page leaks {forbidden!r}")
    for h, want in (("cache-control", "no-store"), ("referrer-policy", "no-referrer"), ("x-robots-tag", "noindex, nofollow")):
        if r.headers.get(h) != want:
            problems.append(f"header {h}: {r.headers.get(h)!r}")
    verdict("approval links: the page shows the summary, no cost data, never cached", problems)

    problems = []
    for method in ("GET", "GET", "HEAD", "GET"):
        view(t1, method)           # a scanner prefetching, over and over
    http(rs, "GET", f"{API}/approval-links/{t1}/decision")                      # the decision URL is POST only
    http(rs, "POST", f"{API}/approval-links/{t1}", body={"decision": "approved"})  # and the page URL is GET only
    after = state(po1, ap1)
    if after != before:
        problems.append(f"a GET/HEAD/wrong-method call changed state: {before} -> {after}")
    verdict("approval links: prefetching (GET, HEAD, wrong methods) changes nothing", problems)

    # ── deciding ──────────────────────────────────────────────────────────────
    problems = []
    before = state(po1, ap1)
    r = decide(t1, {"decision": "rejected"})
    if r.status != 422 or r.body.get("error_code") != "po_approval_reason_required":
        problems.append(f"reject without a reason: {r.status} {r.body}")
    r = decide(t1, {"decision": "maybe"})
    if r.status != 422 or r.body.get("error_code") != "approval_link_invalid_request":
        problems.append(f"bad decision: {r.status} {r.body}")
    r = decide(t1, {"decision": "approved", "comment": "x" * 501})
    if r.status != 422:
        problems.append(f"comment too long: {r.status}")
    if state(po1, ap1) != before:
        problems.append("a refused decision changed state (it must not even burn the link)")
    verdict("approval links: refused decisions (no reason, bad body) change nothing and keep the link", problems)

    problems = []
    r = decide(t1, {"decision": "approved", "comment": "ok by contract"})
    if r.status != 200 or r.body["data"].get("decision") != "approved":
        problems.append(f"approve: {r.status} {r.body}")
    st = state(po1, ap1)
    if st["approval"] != [("approved", a1, "message")]:
        problems.append(f"approval row: {st['approval']}")
    # approved_amount is the order's value AT DECISION (20 x 301.23), exactly as the in-app path records it,
    # while the request (and so the page) carried the 6,000 it was asked at.
    if st["po"] != [("approved", 6024.6)]:
        problems.append(f"order row: {st['po']}")
    used = [l for l in st["links"] if l[0] == a1]
    other = [l for l in st["links"] if l[0] == a2]
    if not used or not (used[0][3] and used[0][4] == "approved"):
        problems.append(f"the used link: {used}")
    if not other or not (other[0][5] and other[0][6] == "decided"):
        problems.append(f"the other approver's link did not die with the decision: {other}")
    ev = q("SELECT user_id, context FROM activity_logs WHERE tenant_id = %s AND action = 'purchase.approval_approved' AND resource = %s",
           (tenant, po1))
    if len(ev) != 1 or ev[0][0] != a1 or ev[0][1].get("channel") != "message" or ev[0][1].get("decision_comment") != "ok by contract":
        problems.append(f"audit event: {ev}")
    if one("SELECT COUNT(*) FROM outbound_messages WHERE tenant_id = %s AND kind = 'po_approval_decision' AND params->>'po_log_id' = %s",
           (tenant, po1)) != 1:
        problems.append("the requester's decision mail was not queued exactly once")
    verdict("approval links: approve decides through the in-app path, channel=message in the trail", problems)

    problems = []
    before = state(po1, ap1)
    for label, r in (("replay approve", decide(t1, {"decision": "approved"})),
                     ("replay reject", decide(t1, {"decision": "rejected", "comment": "second thoughts"})),
                     ("GET after use", view(t1)),
                     ("the other approver's dead link", view(t2))):
        is_neutral(r, label, problems)
    if state(po1, ap1) != before:
        problems.append("a replay changed state")
    verdict("approval links: a used or revoked link is one neutral answer and changes nothing", problems)

    # ── expired, unknown, malformed ───────────────────────────────────────────
    po2, ap2 = seed_po()
    issue_via_rust(po2)
    e1 = token_from_outbox(po2, a1_email)
    q("UPDATE po_approval_links SET expires_at = NOW() - INTERVAL '1 second' WHERE po_log_id = %s", (po2,))
    problems = []
    before = state(po2, ap2)
    is_neutral(view(e1), "expired GET", problems)
    is_neutral(decide(e1, {"decision": "approved"}), "expired POST", problems)
    for label, tok in (("unknown", secrets.token_urlsafe(32)), ("short", "abc"), ("long", "a" * 300), ("traversal", "..%2F..%2Fetc")):
        is_neutral(view(tok), f"{label} GET", problems)
        is_neutral(decide(tok, {"decision": "approved"}), f"{label} POST", problems)
    if state(po2, ap2) != before:
        problems.append("an expired or unknown link changed state")
    verdict("approval links: expired, unknown and malformed tokens are the same neutral answer", problems)

    # ── revoke hook, re-send ──────────────────────────────────────────────────
    problems = []
    issue_via_rust(po2)                       # re-send: rotates, clears the expiry
    e2 = token_from_outbox(po2, a1_email)
    if e2 == e1:
        problems.append("re-sending did not rotate the token")
    is_neutral(view(e1), "the rotated-away token", problems)
    if view(e2).status != 200:
        problems.append("the fresh token does not open the page")
    r = hook(f"/inventory/po/{po2}/approval/links/revoke")
    if r.status != 200 or r.body["data"].get("revoked") != 2:
        problems.append(f"revoke: {r.status} {r.body}")
    r = hook(f"/inventory/po/{po2}/approval/links/revoke")
    if r.status != 200 or r.body["data"].get("revoked") != 0:
        problems.append(f"revoke again (idempotent): {r.status} {r.body}")
    is_neutral(view(e2), "a revoked link", problems)
    is_neutral(decide(e2, {"decision": "approved"}), "a revoked link POST", problems)
    if state(po2, ap2)["approval"] != [("requested", None, None)]:
        problems.append("a revoked link decided something")
    if not one("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s AND action = 'purchase.approval_links_revoked' AND resource = %s",
               (tenant, po2)):
        problems.append("no revoke event in the trail")
    verdict("approval links: re-send rotates, revoke kills (idempotent, audited)", problems)

    # ── interop: links issued by the PYTHON code are accepted by Rust ─────────
    interop = _python_issue(args, db, tenant, ns)
    if interop is None:
        verdict("approval links: interop with Python-issued links", [], skip="backend not importable from the harness environment")
    else:
        po3, ap3 = seed_po()
        problems = []
        issued = interop(po3, ap3, [{"id": a1, "email": a1_email}], requester)
        if not issued:
            problems.append("the Python issuer returned no link")
        else:
            tok = issued[0]["token"]
            r = view(tok)
            if r.status != 200:
                problems.append(f"Rust did not open a Python-issued link: {r.status} {r.body}")
            r = decide(tok, {"decision": "rejected", "comment": "interop reject"})
            if r.status != 200:
                problems.append(f"Rust did not decide with a Python-issued link: {r.status} {r.body}")
            if state(po3, ap3)["approval"] != [("rejected", a1, "message")]:
                problems.append(f"state after the interop decision: {state(po3, ap3)['approval']}")
        verdict("approval links: Python-issued links are accepted by Rust", problems)

        # a decision taken in the app by PYTHON kills Rust's links
        q("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (fx.admin_id,))   # the fixture admin may decide in the app
        po4, ap4 = seed_po()
        problems = []
        issue_via_rust(po4)
        tok = token_from_outbox(po4, a1_email)
        r = http(py, "POST", f"{API}/inventory/po/{po4}/approval/approve", token=auth_for(fx, "admin"), body={})
        if r.status != 200:
            problems.append(f"in-app approve through Python: {r.status} {r.body}")
        else:
            st = state(po4, ap4)
            if st["approval"] != [("approved", fx.admin_id, None)]:
                problems.append(f"the in-app decision recorded a channel: {st['approval']}")
            if not all(l[5] and l[6] == "decided" for l in st["links"]):
                problems.append(f"Python's decision left a link open: {st['links']}")
            is_neutral(view(tok), "a link after Python decided in the app", problems)
        verdict("approval links: a decision in the app (Python) revokes the links", problems)

        # ... and so does one taken in the app by RUST
        po5, ap5 = seed_po()
        problems = []
        issue_via_rust(po5)
        tok = token_from_outbox(po5, a2_email)
        r = http(rs, "POST", f"{API}/inventory/po/{po5}/approval/approve", token=auth_for(fx, "admin"), body={})
        if r.status != 200:
            problems.append(f"in-app approve through Rust: {r.status} {r.body}")
        else:
            st = state(po5, ap5)
            if not all(l[5] and l[6] == "decided" for l in st["links"]):
                problems.append(f"Rust's in-app decision left a link open: {st['links']}")
            is_neutral(view(tok), "a link after Rust decided in the app", problems)
        verdict("approval links: a decision in the app (Rust) revokes the links", problems)

    # ── wrong order / wrong approver ──────────────────────────────────────────
    po6, ap6 = seed_po()
    po7, ap7 = seed_po()
    issue_via_rust(po6)
    issue_via_rust(po7)
    t6, t7 = token_from_outbox(po6, a1_email), token_from_outbox(po7, a1_email)
    problems = []
    r = decide(t6, {"decision": "approved"})
    if r.status != 200:
        problems.append(f"approve order 6: {r.status}")
    if state(po7, ap7)["approval"] != [("requested", None, None)]:
        problems.append("the link for another order decided order 7")
    verdict("approval links: a link decides only its own order", problems)

    problems = []
    q("UPDATE users SET can_approve_po = FALSE WHERE id = %s", (a1,))
    before = state(po7, ap7)
    is_neutral(view(t7), "an approver who lost the flag (GET)", problems)
    is_neutral(decide(t7, {"decision": "approved"}), "an approver who lost the flag (POST)", problems)
    if state(po7, ap7) != before:
        problems.append("a link of a demoted approver changed state")
    q("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (a1,))
    verdict("approval links: a link is dead once its approver may no longer decide", problems)

    # ── cross-tenant ──────────────────────────────────────────────────────────
    other = ns["signup"](py, fx.secret)
    try:
        problems = []
        o_user = other.admin_id
        q("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (o_user,))
        o_req = f"usr_alr{secrets.token_hex(5)}"
        q("""INSERT INTO users (id, tenant_id, email, hashed_password, role, status, email_verified)
             VALUES (%s, %s, %s, 'x', 'analyst', 'active', TRUE)""", (o_req, other.tenant_id, f"{o_req}@stockai.demo"))
        q("INSERT INTO po_approval_rules (tenant_id, threshold, created_by) VALUES (%s, 5000, 'contract')", (other.tenant_id,))
        po8, ap8 = seed_po(tenant_id=other.tenant_id, requested_by=o_req)
        # the other tenant's analyst token cannot issue links for OUR order
        r = http(rs, "POST", f"{API}/inventory/po/{po7}/approval/links",
                 token=ns["mint"](fx.secret, o_user, other.tenant_id, "admin"), body={})
        if r.status != 404:
            problems.append(f"another tenant issuing for our order: {r.status}, expected 404")
        # a forged row that mixes tenants resolves to nothing
        forged = secrets.token_urlsafe(32)
        import hashlib
        q("""INSERT INTO po_approval_links (tenant_id, po_log_id, approval_id, approver_id, token_hash, channel, expires_at, created_by)
             VALUES (%s, %s, %s, %s, %s, 'whatsapp', NOW() + INTERVAL '1 hour', 'contract')""",
          (tenant, po8, ap8, o_user, hashlib.sha256(forged.encode()).hexdigest()))
        before = state(po8, ap8)
        is_neutral(view(forged), "a link mixing tenants (GET)", problems)
        is_neutral(decide(forged, {"decision": "approved"}), "a link mixing tenants (POST)", problems)
        if state(po8, ap8) != before:
            problems.append("a link mixing tenants changed the other tenant's order")
        verdict("approval links: nothing crosses tenants", problems)
    finally:
        try:
            token = ns["mint"](fx.secret, other.admin_id, other.tenant_id, "admin")
            http(py, "DELETE", f"{API}/tenant", token=token, body={"confirm": "DELETE"})
        except Exception as exc:   # noqa: BLE001 - cleanup must not hide the verdicts
            print(f"  (could not erase the second tenant: {exc})")

    # ── cleanup of what this group seeded in the shared fixture tenant ────────
    q("DELETE FROM po_approval_rules WHERE tenant_id = %s", (tenant,))
    q("DELETE FROM users WHERE id IN (%s, %s)", (a1, a2))
    return out


def _python_issue(args, db, tenant, ns):
    """A callable that issues links with the PYTHON code, or None when the
    backend cannot be imported here (the harness is stdlib-only by design)."""
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        os.environ.setdefault("DATABASE_URL", args.db)
        from backend.db import connection as conn_mod  # noqa: PLC0415
        if hasattr(conn_mod, "init_pool"):
            try:
                conn_mod.init_pool(args.db)
            except Exception:   # noqa: BLE001 - already initialised
                pass
        from backend.inventory import po_approval_link_service as links  # noqa: PLC0415
    except Exception as exc:   # noqa: BLE001
        print(f"  (Python issuer not importable: {exc})")
        return None

    def issue(po, approval, approvers, created_by):
        return links.issue_links(tenant, po, approval, approvers, created_by=created_by)
    return issue
