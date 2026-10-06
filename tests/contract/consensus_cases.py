"""Contract cases for the S&OP consensus routes (Rust only, no Python twin).

Called from `contract_test.py::run` as `run_consensus(args, fx, db, h)`, with `h`
the harness module (its `http`, `Case`, `auth_for`, `make_fixture`, ...).

There is nothing to diff against: these routes exist only in Rust, so every case
asserts the status and error code it must answer AND the rows the database must
hold afterwards (the testing mandate: state, not echoes). Where the consensus
arithmetic is involved the expected lines are computed with the Python reference
(`consensus_reference.py`), so the route is held to the same spec the Rust unit
test is.

Needs --db. Seeds a completed session straight into the database (a trained
forecast is not what is under test) and erases nothing itself: the harness erases
the throwaway tenant, and every row here hangs from it.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consensus_reference as ref  # noqa: E402

ROUTE = "consensus (rust only)"


class _Safe(dict):
    """A dict whose missing keys are empty, so a failed call is REPORTED by the
    comparison that follows it instead of raising inside the case."""

    def __missing__(self, key):
        return _Safe()


def _wrap(value):
    if isinstance(value, dict):
        return _Safe({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def D(r):
    """The response's `data`, or an empty dict when there is none."""
    body = r.body if isinstance(r.body, dict) else {}
    return _wrap(body.get("data") or {})


def _stamp(n):
    return (date.today() + timedelta(days=n)).isoformat()


def run_consensus(args, fx, db, h) -> list:
    if db is None:
        return [(h.Case("consensus (all)", "-", "-", route=ROUTE), "SKIP",
                 ["needs --db: the session is seeded and every write is checked in the database"])]
    rs, py, API = args.rust, args.python, h.API
    cur = db.cursor()
    out: list = []
    tag = secrets.token_hex(3)

    def rec(name, problems, method="-", path="-"):
        out.append((h.Case(name, method, path, route=ROUTE), "FAIL" if problems else "PASS", problems))

    def call(method, path, who="admin", body=None, headers=None):
        return h.http(rs, method, path, token=h.auth_for(fx, who), body=body, headers=headers or {})

    def expect(name, r, status, code=None, **checks):
        """status/code of a response plus any (label, actual, expected) triples."""
        problems = []
        if r.status != status:
            problems.append(f"status {r.status} (wanted {status}): {json.dumps(r.body)[:300]}")
        elif code is not None:
            got = r.body.get("error_code") if isinstance(r.body, dict) else None
            if got != code:
                problems.append(f"error_code {got!r} (wanted {code!r}): {json.dumps(r.body)[:300]}")
        for label, (actual, wanted) in checks.items():
            if actual != wanted:
                problems.append(f"{label}: {actual!r} (wanted {wanted!r})")
        rec(name, problems)
        return r

    def one(sql, params=()):
        """The first row, or a row of Nones: a missing row is REPORTED by the
        comparison that follows, never raised inside the case."""
        cur.execute(sql, params)
        return cur.fetchone() or (None,) * 16

    def count(table, where="", params=()):
        return one(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s {where}", (fx.tenant_id, *params))[0]

    def activity(action):
        return one("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = %s AND action = %s",
                   (fx.tenant_id, action))[0]

    # The harness invites its analyst and viewer with a temporary password nobody
    # knows, so they stay 'invited'; the consensus only counts active people.
    cur.execute("UPDATE users SET status = 'active' WHERE tenant_id = %s", (fx.tenant_id,))

    # -- a completed forecast with two products (one of them per store) ------------
    r = h.http(py, "POST", f"{API}/sessions", token=h.auth_for(fx, "admin"),
               body={"name": f"consensus-{tag}"})
    if r.status not in (200, 201):
        return [(h.Case("consensus setup", "-", "-", route=ROUTE), "FAIL",
                 [f"creating the session failed: {r.status} {r.body}"])]
    sid = D(r).get("session_id") or D(r)["id"]
    cur.execute("UPDATE sessions SET status = 'COMPLETED' WHERE id = %s AND tenant_id = %s", (sid, fx.tenant_id))
    days = [(date.today() + timedelta(days=d)).isoformat() for d in range(-20, 80)]
    series = lambda v: {"m": {"forecast": [{"date": d, "value": v} for d in days]}}   # noqa: E731
    cur.execute(
        """INSERT INTO session_results (session_id, tenant_id, forecasts) VALUES (%s, %s, %s::jsonb)
           ON CONFLICT (session_id) DO UPDATE SET forecasts = EXCLUDED.forecasts""",
        (sid, fx.tenant_id, json.dumps({"SKU-A": series(10.0), "SKU-B│North": series(5.0)})))
    base = f"{API}/sessions/{sid}/consensus"

    # -- access ----------------------------------------------------------------------
    expect("settings: a viewer may read", call("GET", f"{API}/consensus/settings", "viewer"), 200)
    r = call("GET", f"{API}/consensus/settings", "admin")
    expect("settings: nothing chosen yet is reported as not configured", r, 200,
           configured=(D(r)["configured"], False))
    for who in ("key_read", "key_write"):
        expect(f"settings: an API key ({who}) is refused", call("GET", f"{API}/consensus/settings", who), 403,
               "api_key_route_not_exposed")
    expect("settings: no token", call("GET", f"{API}/consensus/settings", "none"), 401)
    before = count("consensus_settings")
    expect("settings: a viewer cannot write", call("PUT", f"{API}/consensus/settings", "viewer", {}), 403,
           "role_not_permitted", rows_written=(count("consensus_settings") - before, 0))
    expect("settings: an analyst cannot write", call("PUT", f"{API}/consensus/settings", "analyst", {}), 403,
           "role_not_permitted", rows_written=(count("consensus_settings") - before, 0))

    # -- the rule ------------------------------------------------------------------------
    good = {"rule": "weighted", "priority": ["finance", "sales", "operations"],
            "weights": {"sales": 3, "finance": 1, "operations": 0}, "cap_down_bp": -5000, "cap_up_bp": 3000,
            "members": {"sales": [fx.analyst_id], "finance": [fx.admin_id], "operations": []}}
    bad_cases = [
        ("a rule that is not one", {**good, "rule": "vote"}, 422, "consensus_rule_invalid"),
        ("a priority that repeats a function", {**good, "priority": ["sales", "sales", "finance"]}, 422,
         "consensus_priority_invalid"),
        ("weighted with every weight at zero", {**good, "weights": {"sales": 0, "finance": 0, "operations": 0}},
         422, "consensus_weights_invalid"),
        ("a cap beyond the lowest possible demand", {**good, "cap_down_bp": -10001}, 422, "validation_error"),
        ("a cap that is a fraction", {**good, "cap_up_bp": 10.5}, 422, "validation_error"),
        ("a viewer as a function member", {**good, "members": {"sales": [fx.viewer_id]}}, 422,
         "consensus_member_invalid"),
        ("an unknown person as a function member", {**good, "members": {"sales": ["nobody"]}}, 422,
         "consensus_member_invalid"),
    ]
    for label, body, status, code in bad_cases:
        r = call("PUT", f"{API}/consensus/settings", "admin", body)
        expect(f"settings: refuses {label}", r, status, code,
               rows_written=(count("consensus_settings") + count("consensus_members"), 0))
    expect("propose before a rule is chosen", call("POST", f"{base}/versions", "analyst", {"name": "x"}), 409,
           "consensus_rule_not_configured")
    r = call("PUT", f"{API}/consensus/settings", "admin", good)
    row = one("""SELECT rule, priority, weight_sales, weight_finance, weight_operations, cap_down_bp, cap_up_bp,
                        updated_by FROM consensus_settings WHERE tenant_id = %s""", (fx.tenant_id,))
    members = sorted(one("SELECT array_agg(function || ':' || user_id) FROM consensus_members WHERE tenant_id = %s",
                         (fx.tenant_id,))[0] or [])
    expect("settings: the admin sets the rule", r, 200,
           stored=(row, ("weighted", ["finance", "sales", "operations"], 3, 1, 0, -5000, 3000, fx.admin_id)),
           members=(members, sorted([f"sales:{fx.analyst_id}", f"finance:{fx.admin_id}"])),
           event=(activity("consensus.rule_changed"), 1))

    # -- submissions ------------------------------------------------------------------------
    sub = {"sku": "SKU-A", "function": "sales", "start_date": _stamp(10), "end_date": _stamp(39),
           "pct_bp": 1000, "reason_code": "promotion"}
    sub_url = f"{base}/submissions"
    r = call("POST", sub_url, "analyst", sub)
    first_id = D(r)["id"] if r.status == 201 else None
    row = one("""SELECT function, pct_bp, revision, created_by, superseded_by FROM consensus_submissions
                  WHERE id = %s""", (first_id,)) if first_id else None
    expect("submit: a member speaks for her function", r, 201,
           stored=(row, ("sales", 1000, 1, fx.analyst_id, None)),
           event=(activity("consensus.adjustment_submitted"), 1))
    n0 = count("consensus_submissions")

    def refused(label, who, body, status, code):
        r = call("POST", sub_url, who, body)
        expect(f"submit: {label}", r, status, code, rows_written=(count("consensus_submissions") - n0, 0))

    refused("a viewer", "viewer", sub, 403, "role_not_permitted")
    refused("a key", "key_write", sub, 403, "api_key_route_not_exposed")
    refused("an analyst for a function she does not speak for", "analyst", {**sub, "function": "finance"}, 403,
            "consensus_not_member")
    refused("an unknown function", "admin", {**sub, "function": "legal"}, 422, "consensus_function_invalid")
    refused("a product the forecast does not have", "analyst", {**sub, "sku": "TYPO"}, 404, "consensus_sku_unknown")
    refused("a reason outside the list", "analyst", {**sub, "reason_code": "vibes"}, 422, "consensus_reason_invalid")
    refused("'other' without a note", "analyst", {**sub, "reason_code": "other"}, 422, "consensus_note_required")
    refused("an end before the start", "analyst", {**sub, "start_date": _stamp(5), "end_date": _stamp(1)}, 422,
            "consensus_dates_invalid")
    refused("a period longer than 400 days", "analyst", {**sub, "end_date": _stamp(500)}, 422,
            "consensus_dates_invalid")
    refused("demand past ten times the forecast", "analyst", {**sub, "pct_bp": 100001}, 422,
            "consensus_out_of_range")
    refused("demand below zero", "analyst", {**sub, "pct_bp": -10001}, 422, "consensus_out_of_range")
    refused("a fractional basis-point figure", "analyst", {**sub, "pct_bp": 12.5}, 422, "validation_error")
    refused("an overlapping but different period of the same function", "analyst",
            {**sub, "start_date": _stamp(30), "end_date": _stamp(60)}, 409, "consensus_overlap")
    r = call("POST", f"{API}/sessions/nonexistent/consensus/submissions", "analyst", sub)
    expect("submit: an unknown session", r, 404, "session_not_found")

    # The same period again is a REVISION: the old row stays, marked.
    r = call("POST", sub_url, "analyst", {**sub, "pct_bp": 1500, "reason_note": "bigger order confirmed"})
    second_id = D(r)["id"] if r.status == 201 else None
    old = one("SELECT superseded_by, pct_bp FROM consensus_submissions WHERE id = %s", (first_id,))
    new = one("SELECT revision, pct_bp, superseded_by FROM consensus_submissions WHERE id = %s", (second_id,)) \
        if second_id else None
    expect("submit: a revision supersedes, never overwrites", r, 201,
           old_row=(old, (second_id, 1000)), new_row=(new, (2, 1500, None)),
           history_kept=(count("consensus_submissions", "AND sku = 'SKU-A'"), 2))

    # Finance disagrees on part of the same dates (an admin may speak for any function).
    r = call("POST", sub_url, "admin", {"sku": "SKU-A", "function": "finance", "start_date": _stamp(25),
                                         "end_date": _stamp(50), "pct_bp": -500, "reason_code": "budget_target"})
    expect("submit: finance's figure for part of the same dates", r, 201)
    r = call("POST", sub_url, "admin", {"sku": "SKU-B", "function": "operations", "start_date": _stamp(10),
                                         "end_date": _stamp(20), "pct_bp": 9000, "reason_code": "supply_issue"})
    expect("submit: a store-level series is found by its product code", r, 201)

    # The database refuses to rewrite a submission or a frozen version.
    for label, sql, params in [
        ("a submission's figure", "UPDATE consensus_submissions SET pct_bp = 0 WHERE id = %s", (second_id,)),
        ("a submission's author", "UPDATE consensus_submissions SET created_by = 'x' WHERE id = %s", (second_id,)),
    ]:
        try:
            cur.execute(sql, params)
            rec(f"database: rewriting {label} is refused", ["the UPDATE went through"])
        except Exception as exc:  # noqa: BLE001 - psycopg2 raises its own classes
            rec(f"database: rewriting {label} is refused", [] if "append-only" in str(exc) else [str(exc)[:200]])

    # -- reading the ledger ------------------------------------------------------------------
    r = call("GET", f"{sub_url}?sku=SKU-A", "viewer")
    expect("list: current adjustments only", r, 200, total=(D(r)["total"], 2))
    r = call("GET", f"{sub_url}?sku=SKU-A&include_superseded=true", "viewer")
    expect("list: the whole history on request", r, 200, total=(D(r)["total"], 3),
           authors=(sorted({i["created_by_name"] is not None for i in D(r)["items"]}), [True]))

    # -- preview = the Python reference ------------------------------------------------------
    cfg = {"rule": "weighted", "priority": ["finance", "sales", "operations"],
           "weights": {"sales": 3, "finance": 1, "operations": 0}, "cap_down_bp": -5000, "cap_up_bp": 3000}

    def reference_lines():
        cur.execute("""SELECT id, sku, function, start_date, end_date, pct_bp FROM consensus_submissions
                        WHERE tenant_id = %s AND session_id = %s AND superseded_by IS NULL""", (fx.tenant_id, sid))
        subs = [{"id": i, "sku": s, "function": f, "start": a.toordinal(), "end": b.toordinal(), "pct_bp": p}
                for i, s, f, a, b, p in cur.fetchall()]
        return [{"sku": ln["sku"], "start_date": date.fromordinal(ln["start"]).isoformat(),
                 "end_date": date.fromordinal(ln["end"]).isoformat(), "pct_bp": ln["pct_bp"],
                 "source": ln["source"], "inputs": ln["inputs"]}
                for ln in ref.consensus_lines(cfg, subs)]

    r = call("GET", f"{base}/preview", "viewer")
    want = reference_lines()
    expect("preview: the lines are what the Python reference computes", r, 200,
           lines=(D(r)["lines"]["items"], want), line_count=(D(r)["line_count"], len(want)))
    # Operations has weight 0 and no vote: SKU-B alone has no line at all.
    expect("preview: a zero-weight function alone decides nothing",
           r, 200, skus=(sorted({ln["sku"] for ln in D(r)["lines"]["items"]}), ["SKU-A"]))

    # -- proposing and deciding ----------------------------------------------------------------
    pv = f"{base}/versions"
    expect("propose: a viewer is refused", call("POST", pv, "viewer", {"name": "x"}), 403, "role_not_permitted",
           rows=(count("consensus_versions"), 0))
    expect("propose: a name is required", call("POST", pv, "analyst", {"name": ""}), 422, "validation_error",
           rows=(count("consensus_versions"), 0))
    r = call("POST", pv, "analyst", {"name": f"Q4 plan {tag}", "note": "first cut"})
    v1 = D(r)["id"] if r.status == 201 else None
    stored = one("SELECT status, line_count, sku_count, created_by, lines FROM consensus_versions WHERE id = %s",
                 (v1,)) if v1 else None
    expect("propose: the lines are frozen from the current adjustments", r, 201,
           header=((stored[0], stored[1], stored[2], stored[3]), ("proposed", len(want), 1, fx.analyst_id)) if stored
           else (None, "a row"),
           lines_equal_reference=(stored[4] if stored else None, want),
           events=(count("consensus_version_events", "AND version_id = %s", (v1,)), 1),
           activity=(activity("consensus.version_proposed"), 1))
    try:
        cur.execute("UPDATE consensus_versions SET lines = '[]'::jsonb WHERE id = %s", (v1,))
        rec("database: rewriting a frozen version is refused", ["the UPDATE went through"])
    except Exception as exc:  # noqa: BLE001
        rec("database: rewriting a frozen version is refused", [] if "frozen" in str(exc) else [str(exc)[:200]])

    dec = lambda v, a: f"{API}/consensus/versions/{v}/{a}"   # noqa: E731
    expect("approve: an analyst who is not an approver is refused", call("POST", dec(v1, "approve"), "analyst", {}),
           403, "consensus_not_approver", status_unchanged=(one("SELECT status FROM consensus_versions WHERE id = %s",
                                                                 (v1,))[0], "proposed"))
    expect("approve: a viewer is refused", call("POST", dec(v1, "approve"), "viewer", {}), 403, "role_not_permitted")
    expect("approve: an unknown version", call("POST", dec("nope", "approve"), "admin", {}), 404,
           "consensus_version_not_found")
    r = call("POST", dec(v1, "approve"), "admin", {"comment": "agreed in the S&OP meeting"})
    row = one("SELECT status, decided_by, decision_comment, self_approved FROM consensus_versions WHERE id = %s", (v1,))
    expect("approve: the approver publishes", r, 200,
           stored=(row, ("approved", fx.admin_id, "agreed in the S&OP meeting", False)),
           events=(count("consensus_version_events", "AND version_id = %s", (v1,)), 2),
           activity=(activity("consensus.version_approved"), 1))
    expect("approve: twice is a conflict", call("POST", dec(v1, "approve"), "admin", {}), 409,
           "consensus_transition_invalid")

    # A second proposal, now with two approvers: nobody approves her own.
    cur.execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (fx.analyst_id,))
    r = call("POST", pv, "analyst", {"name": f"Q4 plan v2 {tag}"})
    v2 = D(r)["id"] if r.status == 201 else None
    expect("approve: the proposer cannot approve her own while another approver exists",
           call("POST", dec(v2, "approve"), "analyst", {}), 403, "consensus_self_approval")
    r = call("POST", dec(v2, "approve"), "admin", {})
    st = {k: one("SELECT status FROM consensus_versions WHERE id = %s", (k,))[0] for k in (v1, v2)}
    expect("approve: a newer version replaces the published one, which stays", r, 200,
           statuses=(st, {v1: "superseded", v2: "approved"}),
           one_published=(count("consensus_versions", "AND session_id = %s AND status = 'approved'", (sid,)), 1),
           superseded_reported=(D(r).get("superseded"), 1))
    try:
        cur.execute("UPDATE consensus_versions SET status = 'approved' WHERE id = %s", (v1,))
        rec("database: two published consensuses for one forecast are impossible", ["the UPDATE went through"])
    except Exception as exc:  # noqa: BLE001
        rec("database: two published consensuses for one forecast are impossible",
            [] if "consensus_versions_one_published" in str(exc) else [str(exc)[:200]])

    # A function revises after a proposal: approving would publish a withdrawn figure.
    r = call("POST", pv, "analyst", {"name": f"Q4 plan v3 {tag}"})
    v3 = D(r)["id"] if r.status == 201 else None
    call("POST", sub_url, "analyst", {**sub, "pct_bp": 800, "reason_note": "order shrank"})
    expect("approve: a proposal built on a since-revised figure is refused",
           call("POST", dec(v3, "approve"), "admin", {}), 409, "consensus_version_stale",
           status_unchanged=(one("SELECT status FROM consensus_versions WHERE id = %s", (v3,))[0], "proposed"))
    expect("reject: a reason is required", call("POST", dec(v3, "reject"), "admin", {"comment": "no"}), 422,
           "consensus_reason_required")
    r = call("POST", dec(v3, "reject"), "admin", {"comment": "stale, propose it again"})
    expect("reject: with a reason", r, 200,
           state=(one("SELECT status FROM consensus_versions WHERE id = %s", (v3,))[0], "rejected"),
           activity=(activity("consensus.version_rejected"), 1))

    # Withdrawing hands planning back to the statistical forecast.
    expect("withdraw: a reason is required", call("POST", dec(v2, "withdraw"), "admin", {}), 422,
           "consensus_reason_required")
    expect("withdraw: only a published version", call("POST", dec(v3, "withdraw"), "admin", {"comment": "mistake"}),
           409, "consensus_transition_invalid")
    r = call("POST", dec(v2, "withdraw"), "admin", {"comment": "the order was cancelled"})
    expect("withdraw: the approver takes it back", r, 200,
           state=(one("SELECT status FROM consensus_versions WHERE id = %s", (v2,))[0], "withdrawn"),
           none_published=(count("consensus_versions", "AND status = 'approved'"), 0),
           activity=(activity("consensus.version_withdrawn"), 1))

    r = call("GET", f"{API}/consensus/versions?session_id={sid}", "viewer")
    expect("versions: the list names every status and who decided", r, 200,
           count_=(len(D(r)["items"]), 3))
    r = call("GET", f"{API}/consensus/versions/{v1}?limit=1", "viewer")
    expect("version: detail pages its lines and carries its history", r, 200,
           page=(len(D(r)["lines"]["items"]), 1), events=(len(D(r)["events"]), 3))

    # -- accuracy: statistical vs adjusted vs actual ---------------------------------------------
    r = call("GET", f"{base}/fva", "viewer")
    expect("fva: adjustments but no actuals yet say so", r, 200, status_=(D(r)["status"], "no_evidence"))
    # Evidence the Python grader would have written: SKU-A forecast 10/day; it really sold 12/day.
    rows = [(fx.tenant_id, sid, "SKU-A", date.today() + timedelta(days=d), 10.0, 12.0) for d in range(12, 38)]
    cur.executemany("""INSERT INTO consensus_evidence (tenant_id, session_id, sku, period, base, actual)
                       VALUES (%s, %s, %s, %s, %s, %s)""", rows)
    r = call("GET", f"{base}/fva", "viewer")
    d = D(r) if isinstance(r.body, dict) and r.body.get("data") else {}
    # Sales said +8% (the revision), finance -5%: sales moved the forecast the right way, finance the wrong way.
    pts = lambda lo, hi, bp: [{"base": 10.0, "pct_bp": bp, "actual": 12.0} for dd in range(12, 38)  # noqa: E731
                              if lo <= dd <= hi]
    sales = ref.forecast_value_added([{"base": 10.0, "pct_bp": 800, "actual": 12.0} for dd in range(12, 38)
                                      if 10 <= dd <= 39])
    finance = ref.forecast_value_added([{"base": 10.0, "pct_bp": -500, "actual": 12.0} for dd in range(12, 38)
                                        if 25 <= dd <= 50])
    by_fn = {x["function"]: x for x in d.get("by_function", [])}
    problems = []
    if r.status != 200 or d.get("status") != "ok":
        problems.append(f"{r.status} {json.dumps(r.body)[:300]}")
    else:
        for name, want_fva in (("sales", sales), ("finance", finance)):
            got = {k: v for k, v in by_fn.get(name, {}).items() if k != "function"}
            if got != want_fva:
                problems.append(f"{name}: {got} != reference {want_fva}")
        if by_fn.get("sales", {}).get("verdict") != "improved":
            problems.append("sales verdict should be improved")
        if by_fn.get("finance", {}).get("verdict") != "worsened":
            problems.append("finance verdict should be worsened")
        if [x["function"] for x in d["by_function"]] != ["sales", "finance"]:
            problems.append(f"order should put the best first: {[x['function'] for x in d['by_function']]}")
        if len(d["consensus"]) != 2:
            problems.append("both published consensuses (one superseded, one withdrawn) are graded")
        if d["n_graded_submissions"] != 2 or d["n_ungraded"] != 1:
            problems.append(f"graded/ungraded: {d['n_graded_submissions']}/{d['n_ungraded']} (SKU-B has no evidence)")
    rec("fva: each function is graded against the statistical forecast and the actuals", problems)

    # -- scope and tenants ---------------------------------------------------------------------------
    cur.execute("""INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s)
                   ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                (fx.tenant_id, f"CS-Norte-{tag}"))
    wh = cur.fetchone()[0]
    r = h.http(py, "POST", f"{API}/users", token=h.auth_for(fx, "admin"), body={
        "email": f"contract-{tag}-cscoped@stockai.demo", "role": "analyst", "full_name": "Consensus scoped"})
    if r.status == 201:
        uid = D(r)["user"]["id"]
        h.http(py, "PUT", f"{API}/users/{uid}/warehouse-scope", token=h.auth_for(fx, "admin"),
               body={"warehouse_ids": [wh]})
        scoped = h.mint_access_token(fx.secret, uid, fx.tenant_id, "analyst")
        for label, m, p, b in (("read", "GET", f"{API}/consensus/settings", None),
                               ("submit", "POST", sub_url, sub), ("propose", "POST", pv, {"name": "x"})):
            rr = h.http(rs, m, p, token=scoped, body=b)
            expect(f"scope: a warehouse-limited user cannot {label}", rr, 403, "warehouse_scope_company_totals")
    else:
        rec("scope: a warehouse-limited user", [f"creating the scoped analyst failed: {r.status} {r.body}"])
    other = h.make_fixture(py, fx.secret)
    try:
        rr = h.http(rs, "GET", f"{API}/consensus/versions/{v1}", token=h.auth_for(other, "admin"))
        expect("tenant: another company cannot read this version", rr, 404, "consensus_version_not_found")
        rr = h.http(rs, "POST", dec(v3, "reject"), token=h.auth_for(other, "admin"), body={"comment": "intruder"})
        expect("tenant: another company cannot decide on it", rr, 404, "consensus_version_not_found")
        rr = h.http(rs, "GET", f"{base}/submissions", token=h.auth_for(other, "admin"))
        expect("tenant: another company cannot read the session's adjustments", rr, 404, "session_not_found")
    finally:
        h.erase_fixture(py, other)
    return out
