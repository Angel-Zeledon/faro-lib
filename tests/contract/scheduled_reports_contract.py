"""Contract cases for scheduled reports (Rust routes + the Python worker pass).

Unlike the other groups there is nothing to diff against: these routes exist
only in Rust. Each case asserts the answer AND the rows it left (or did not
leave) in the database, with a permission pair on every write, a second tenant
for isolation, and the real Python worker pass (`service.process_due`) run in a
subprocess with a recording mail transport.

Hooked into `contract_test.py` with two lines (see `run()` there), and runnable
alone:

    python tests/contract/scheduled_reports_contract.py --rust http://127.0.0.1:8040 \
        --python http://127.0.0.1:8012 --db postgresql://... --env-file backend/.env

Needs `--db` (psycopg2). Uses the backend venv's Python so `croniter` is
available to compute the expected next run independently of the Rust port.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

API = "/api/v1"
ROUTE = "scheduled-reports"
SECTIONS = ["purchasing_summary", "budget_vs_spend", "committed_demand", "supplier_scorecard"]

PASS_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
from datetime import datetime, timezone
from backend.config import settings
from backend.db.connection import init_pool
init_pool(settings.database_url, min_conn=1, max_conn=3)
from backend.notifications import email, outbox
sent = []
email._send = lambda to, subject, html, attachment=None, tenant_id=None: sent.append([to, subject, html])
email.is_configured = lambda tenant_id=None: (sys.argv[1] != "no_transport")
from backend.scheduled_reports import service
made = service.process_due(datetime.now(timezone.utc))
drained = 0
while True:
    n = outbox.process_due()
    drained += n
    if n == 0:
        break
print("MADE", made)
print("DRAINED", drained)
print("SENT", json.dumps(sent))
"""


def _unsub_token(secret: str, recipient_id: str) -> str:
    """An independent re-implementation of the token (tokens.py / logic.rs)."""
    digest = hmac.new(secret.encode(), f"report-unsubscribe|{recipient_id}".encode(), hashlib.sha256).digest()
    return f"{recipient_id}." + base64.urlsafe_b64encode(digest).decode().rstrip("=")


class Recorder:
    def __init__(self, Case):
        self.Case, self.results = Case, []

    def case(self, name: str, problems: list[str]) -> None:
        self.results.append((self.Case(name, "-", "-", route=ROUTE), "FAIL" if problems else "PASS", problems))


def run(args, fx, db, ct) -> list:
    """`ct` is the contract_test module (its http helper, Case, fixtures)."""
    if db is None:
        print("scheduled-reports cases skipped: they need --db")
        return []
    if args.only and args.only not in ROUTE:
        return []
    rec = Recorder(ct.Case)
    rs, py = args.rust, args.python
    cur = db.cursor()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, **(ct.read_env_file(args.env_file) if args.env_file and os.path.exists(args.env_file) else {}), "DATABASE_URL": args.db}

    def call(who: str, method: str, path: str, body: Any = None, base: Optional[str] = None, fixture=None):
        f = fixture or fx
        return ct.http(base or rs, method, f"{API}{path}", token=f.tokens.get(who), body=body)

    def q(sql: str, params: tuple = ()):
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []

    def one(sql: str, params: tuple = ()):
        rows = q(sql, params)
        return rows[0] if rows else None

    def expect(resp, status: int, code: Optional[str] = None) -> list[str]:
        problems = []
        if resp.status != status:
            problems.append(f"status {resp.status}, expected {status}: {json.dumps(resp.body)[:300]}")
        elif code is not None:
            got = resp.body.get("error_code") if isinstance(resp.body, dict) else None
            if got != code:
                problems.append(f"error_code {got!r}, expected {code!r}")
        return problems

    tenant = fx.tenant_id

    def count(table: str, where: str = "tenant_id = %s", params: tuple = None) -> int:
        return one(f"SELECT COUNT(*) FROM {table} WHERE {where}", params if params is not None else (tenant,))[0]

    def reset() -> None:
        for t in ("report_schedule_runs", "report_schedule_recipients", "report_schedules", "report_external_allowlist"):
            q(f"DELETE FROM {t} WHERE tenant_id = %s", (tenant,))
        q("DELETE FROM outbound_messages WHERE tenant_id = %s AND kind = 'scheduled_report'", (tenant,))
        q("UPDATE users SET status = 'active', warehouse_scope = NULL WHERE tenant_id = %s", (tenant,))

    def allow(*addresses: str) -> None:
        for a in addresses:
            q("INSERT INTO report_external_allowlist (tenant_id, email, added_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
              (tenant, a, fx.admin_id))

    def make(**over) -> Any:
        body = {"name": "Weekly management", "sections": ["purchasing_summary", "committed_demand"],
                "frequency": "weekly", "weekday": 1, "hour": 6, "user_ids": [fx.admin_id]}
        body.update(over)
        return call("analyst", "POST", "/scheduled-reports", body)

    def sched(sid: str):
        return one("SELECT name, sections, frequency, weekday, day_of_month, hour, cron_expr, anchored_tz, enabled, "
                   "paused_reason, consecutive_failures, next_run_at, last_status FROM report_schedules "
                   "WHERE id = %s", (sid,))

    reset()

    # ── Access ───────────────────────────────────────────────────────────────
    r = call("viewer", "GET", "/scheduled-reports/catalog")
    rec.case("catalog: a viewer is refused", expect(r, 403, "role_not_permitted"))
    r = call("none", "GET", "/scheduled-reports")
    rec.case("list: no token is 401", expect(r, 401))
    r = call("key_read", "GET", "/scheduled-reports") if fx.tokens.get("key_read") else None
    if r is not None:
        rec.case("list: an API key is refused (internal tag)", expect(r, 403, "api_key_route_not_exposed"))
    r = call("analyst", "GET", "/scheduled-reports/catalog")
    problems = expect(r, 200)
    if not problems:
        d = r.body["data"]
        src = open(os.path.join(root, "backend", "scheduled_reports", "catalog.py"), encoding="utf-8").read()
        if d["sections"] != SECTIONS or d["frequencies"] != ["weekly", "monthly"]:
            problems.append(f"catalogue differs: {d}")
        for name in SECTIONS:
            if f'"{name}"' not in src:
                problems.append(f"{name} is not in catalog.py")
        if d["timezone"] != "America/Costa_Rica":
            problems.append(f"zone {d['timezone']}")
    rec.case("catalog: an analyst reads the fixed catalogue and the tenant zone", problems)

    # ── The external allow-list ──────────────────────────────────────────────
    r = call("analyst", "POST", "/scheduled-reports/allowed-recipients", {"email": "board@example.com"})
    problems = expect(r, 403, "role_not_permitted")
    if count("report_external_allowlist"):
        problems.append("an analyst wrote to the allow-list")
    rec.case("allow-list: an analyst cannot add (403, nothing written)", problems)
    r = call("viewer", "POST", "/scheduled-reports/allowed-recipients", {"email": "board@example.com"})
    rec.case("allow-list: a viewer cannot add", expect(r, 403) + (["written"] if count("report_external_allowlist") else []))
    r = call("admin", "POST", "/scheduled-reports/allowed-recipients", {"email": "  Board@Example.COM "})
    problems = expect(r, 201)
    row = one("SELECT email, added_by FROM report_external_allowlist WHERE tenant_id = %s", (tenant,))
    if row != ("board@example.com", fx.admin_id):
        problems.append(f"row {row}")
    if not one("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'audit.report_recipient.allowed' AND user_id = %s",
               (tenant, fx.admin_id)):
        problems.append("no audit row for the addition")
    rec.case("allow-list: an admin adds (normalised, audited)", problems)
    r = call("admin", "POST", "/scheduled-reports/allowed-recipients", {"email": "board@example.com"})
    rec.case("allow-list: adding twice is 200, not a second row",
             expect(r, 200) + ([] if count("report_external_allowlist") == 1 else ["duplicate row"]))
    for bad in ("not-an-email", "a@b", "a b@c.com", ""):
        r = call("admin", "POST", "/scheduled-reports/allowed-recipients", {"email": bad})
        rec.case(f"allow-list: {bad!r} is refused", expect(r, 422, "scheduled_report_external_invalid" if bad else None))
    r = call("analyst", "GET", "/scheduled-reports/allowed-recipients")
    problems = expect(r, 200)
    if not problems and [i["email"] for i in r.body["data"]["items"]] != ["board@example.com"]:
        problems.append(f"{r.body['data']}")
    rec.case("allow-list: an analyst can read it", problems)
    r = call("analyst", "DELETE", "/scheduled-reports/allowed-recipients/board@example.com")
    rec.case("allow-list: an analyst cannot remove", expect(r, 403) + ([] if count("report_external_allowlist") == 1 else ["removed"]))

    # ── Create ───────────────────────────────────────────────────────────────
    before = count("report_schedules")
    r = call("viewer", "POST", "/scheduled-reports", {"name": "x", "sections": ["committed_demand"], "frequency": "weekly",
                                                      "weekday": 1, "hour": 6, "user_ids": [fx.admin_id]})
    rec.case("create: a viewer is refused (403, nothing written)",
             expect(r, 403) + ([] if count("report_schedules") == before else ["a schedule was written"]))
    r = make(external_emails=["board@example.com"], user_ids=[fx.admin_id, fx.analyst_id])
    problems = expect(r, 201)
    sid = r.body["data"]["id"] if not problems else None
    if sid:
        s = sched(sid)
        if s[0] != "Weekly management" or s[1] != ["purchasing_summary", "committed_demand"] or s[2:4] != ("weekly", 1):
            problems.append(f"row {s}")
        if s[6] != "0 6 * * 1" or s[7] != "America/Costa_Rica" or s[8] is not True or s[10] != 0:
            problems.append(f"cron/zone/state {s[6:11]}")
        # Independent expectation: croniter in the tenant zone from 'now'.
        try:
            from croniter import croniter
            from zoneinfo import ZoneInfo
            z = ZoneInfo("America/Costa_Rica")
            exp = croniter("0 6 * * 1", datetime.now(z)).get_next(datetime)
            if abs((s[11] - exp.astimezone(timezone.utc)).total_seconds()) > 120:
                problems.append(f"next_run_at {s[11]} != croniter {exp.astimezone(timezone.utc)}")
        except ImportError:
            if s[11].astimezone(timezone.utc).weekday() != 0 or s[11].astimezone(timezone.utc).hour != 12:
                problems.append(f"next_run_at {s[11]} is not a Monday 12:00 UTC (06:00 Costa Rica)")
        recipients = q("SELECT kind, user_id, email, unsubscribed_at FROM report_schedule_recipients WHERE schedule_id = %s "
                       "ORDER BY kind, email NULLS FIRST", (sid,))
        kinds = sorted((k, e) for k, _, e, _ in recipients)
        if kinds != [("external", "board@example.com"), ("user", None), ("user", None)]:
            problems.append(f"recipients {recipients}")
        audit = one("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = 'audit.report_schedule.created' "
                    "AND resource = %s", (tenant, sid))
        if not audit or audit[0].get("target_type") != "report_schedule" or audit[0].get("after", {}).get("name") != "Weekly management":
            problems.append(f"audit {audit}")
        body = r.body["data"]
        if len(body["recipients"]) != 3 or not all(x["would_send"] for x in body["recipients"]):
            problems.append(f"response recipients {body['recipients']}")
    rec.case("create: an analyst creates (row, cron, zone, next run, recipients, audit)", problems)

    # Validation: every refusal writes nothing.
    base_count = count("report_schedules")
    other = ct.make_fixture(py, fx.secret)          # a second tenant, erased at the end
    try:
        refusals = [
            ("unknown section", {"sections": ["salaries"]}, 422, "scheduled_report_section_unknown"),
            ("no sections", {"sections": []}, 422, None),
            ("no recipients", {"user_ids": []}, 422, "scheduled_report_no_recipients"),
            ("weekly without weekday", {"weekday": None}, 422, None),
            ("monthly with a weekday", {"frequency": "monthly", "day_of_month": 5}, 422, None),
            ("monthly without a day", {"frequency": "monthly", "weekday": None}, 422, None),
            ("hour 24", {"hour": 24}, 422, None),
            ("day 29", {"frequency": "monthly", "weekday": None, "day_of_month": 29}, 422, None),
            ("frequency daily", {"frequency": "daily"}, 422, None),
            ("empty name", {"name": "  "}, 422, None),
            ("a user of another tenant", {"user_ids": [other.admin_id]}, 422, "scheduled_report_recipient_not_in_tenant"),
            ("a user id that does not exist", {"user_ids": ["nobody"]}, 422, "scheduled_report_recipient_not_in_tenant"),
            ("an external not on the list", {"external_emails": ["stranger@example.net"]}, 422, "scheduled_report_external_not_allowed"),
            ("another tenant's allowed external", {"external_emails": ["only-b@example.org"]}, 422, "scheduled_report_external_not_allowed"),
            ("an invalid external", {"external_emails": ["nope"]}, 422, "scheduled_report_external_invalid"),
            ("26 recipients", {"external_emails": [f"p{i}@example.com" for i in range(26)]}, 422, None),
        ]
        q("INSERT INTO report_external_allowlist (tenant_id, email, added_by) VALUES (%s, 'only-b@example.org', %s)",
          (other.tenant_id, other.admin_id))
        for name, over, status, code in refusals:
            r = make(**over)
            problems = expect(r, status, code)
            if count("report_schedules") != base_count:
                problems.append("a refused create wrote a schedule")
            rec.case(f"create: refuses {name}", problems)
        # A suspended and a warehouse-limited user.
        q("UPDATE users SET status = 'suspended' WHERE id = %s", (fx.viewer_id,))
        r = make(user_ids=[fx.viewer_id])
        rec.case("create: refuses a suspended user", expect(r, 422, "scheduled_report_recipient_not_in_tenant"))
        q("UPDATE users SET status = 'active', warehouse_scope = '[]'::jsonb WHERE id = %s", (fx.viewer_id,))
        r = make(user_ids=[fx.viewer_id])
        rec.case("create: refuses a warehouse-limited user (the report is company-wide)",
                 expect(r, 422, "scheduled_report_recipient_scoped"))
        q("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (fx.viewer_id,))
        rec.case("create: none of the refusals wrote anything", [] if count("report_schedules") == base_count else ["wrote"])

        # ── Isolation between tenants ────────────────────────────────────────
        problems = []
        r = call("admin", "GET", "/scheduled-reports", fixture=other)
        if expect(r, 200) or r.body["data"]["items"]:
            problems.append(f"the other tenant lists {r.body}")
        snapshot = sched(sid)
        for method, path, body in [("GET", f"/scheduled-reports/{sid}", None),
                                   ("PATCH", f"/scheduled-reports/{sid}", {"name": "hijack"}),
                                   ("POST", f"/scheduled-reports/{sid}/pause", None),
                                   ("POST", f"/scheduled-reports/{sid}/resume", None),
                                   ("GET", f"/scheduled-reports/{sid}/runs", None),
                                   ("POST", "/scheduled-reports/preview", {"schedule_id": sid}),
                                   ("DELETE", f"/scheduled-reports/{sid}", None)]:
            r = call("admin", method, path, body, fixture=other)
            problems += [f"{method} {path}: {p}" for p in expect(r, 404, "scheduled_report_not_found")]
        if sched(sid) != snapshot or count("report_schedule_recipients", "schedule_id = %s", (sid,)) != 3:
            problems.append("the schedule changed or lost recipients")
        r = call("admin", "GET", "/scheduled-reports/allowed-recipients", fixture=other)
        if expect(r, 200) or [i["email"] for i in r.body["data"]["items"]] != ["only-b@example.org"]:
            problems.append(f"allow-list leaked or lost: {r.body}")
        r = call("admin", "DELETE", "/scheduled-reports/allowed-recipients/board@example.com", fixture=other)
        problems += expect(r, 404)
        if count("report_external_allowlist", "tenant_id = %s", (tenant,)) != 1:
            problems.append("another tenant removed an allow-list entry")
        rec.case("isolation: another tenant sees and changes nothing (404 everywhere, rows intact)", problems)
    finally:
        ct.erase_fixture(py, other)

    # ── Ceiling ──────────────────────────────────────────────────────────────
    created = [sid]
    for i in range(9):
        r = make(name=f"Report {i}")
        if r.status == 201:
            created.append(r.body["data"]["id"])
    r = make(name="One too many")
    problems = expect(r, 409, "scheduled_report_limit_reached")
    if count("report_schedules") != 10:
        problems.append(f"{count('report_schedules')} schedules")
    rec.case("create: the eleventh is refused and exactly ten exist", problems)
    for extra in created[1:]:
        call("analyst", "DELETE", f"/scheduled-reports/{extra}")

    # ── Read: who would be skipped ───────────────────────────────────────────
    q("UPDATE users SET status = 'suspended' WHERE id = %s", (fx.analyst_id,))
    r = call("analyst", "GET", f"/scheduled-reports/{sid}")
    problems = expect(r, 200)
    if not problems:
        by = {x["user_id"] or x["email"]: x for x in r.body["data"]["recipients"]}
        if by[fx.analyst_id]["skip_reason"] != "user_inactive" or by[fx.analyst_id]["would_send"]:
            problems.append(f"suspended analyst: {by[fx.analyst_id]}")
        if not by[fx.admin_id]["would_send"] or not by["board@example.com"]["would_send"]:
            problems.append("an eligible recipient is flagged")
    rec.case("read: a suspended recipient is shown as skipped (user_inactive)", problems)
    q("UPDATE users SET status = 'active' WHERE id = %s", (fx.analyst_id,))
    q("DELETE FROM report_external_allowlist WHERE tenant_id = %s AND email = 'board@example.com'", (tenant,))
    r = call("analyst", "GET", f"/scheduled-reports/{sid}")
    ext = [x for x in r.body["data"]["recipients"] if x["kind"] == "external"][0] if r.status == 200 else {}
    rec.case("read: an external removed from the allow-list is shown as skipped (external_not_allowed)",
             [] if ext.get("skip_reason") == "external_not_allowed" else [f"{ext}"])
    allow("board@example.com")

    # ── Update ───────────────────────────────────────────────────────────────
    before = sched(sid)
    r = call("viewer", "PATCH", f"/scheduled-reports/{sid}", {"name": "nope"})
    rec.case("update: a viewer is refused (403, unchanged)", expect(r, 403) + ([] if sched(sid) == before else ["changed"]))
    r = call("analyst", "PATCH", f"/scheduled-reports/{sid}", {"name": "Renamed", "hour": 8, "weekday": 3})
    problems = expect(r, 200)
    s = sched(sid)
    if s[0] != "Renamed" or s[6] != "0 8 * * 3" or s[11] == before[11]:
        problems.append(f"{s}")
    if not one("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'audit.report_schedule.updated' AND resource = %s",
               (tenant, sid)):
        problems.append("no audit row")
    rec.case("update: rename + retime recomputes the cron and the next run, audited", problems)
    r = call("analyst", "PATCH", f"/scheduled-reports/{sid}", {"frequency": "monthly"})
    rec.case("update: switching to monthly needs a day", expect(r, 422) + ([] if sched(sid)[2] == "weekly" else ["changed"]))
    r = call("analyst", "PATCH", f"/scheduled-reports/{sid}", {"frequency": "monthly", "day_of_month": 15})
    s = sched(sid)
    rec.case("update: weekly to monthly", expect(r, 200) + ([] if (s[2], s[3], s[4], s[6]) == ("monthly", None, 15, "0 8 15 * *")
                                                           else [f"{s}"]))
    # Replacing the user recipients keeps an unsubscribe the person made.
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE schedule_id = %s AND user_id = %s", (sid, fx.admin_id))
    r = call("analyst", "PATCH", f"/scheduled-reports/{sid}", {"user_ids": [fx.admin_id]})
    problems = expect(r, 200)
    rows = q("SELECT user_id, unsubscribed_at IS NOT NULL FROM report_schedule_recipients WHERE schedule_id = %s AND kind = 'user'", (sid,))
    if rows != [(fx.admin_id, True)]:
        problems.append(f"user rows {rows}")
    rec.case("update: replacing recipients drops the removed and keeps an unsubscribe", problems)
    r = call("analyst", "PATCH", f"/scheduled-reports/{sid}", {"user_ids": [], "external_emails": []})
    rec.case("update: removing every recipient is refused and changes nothing",
             expect(r, 422, "scheduled_report_no_recipients") +
             ([] if count("report_schedule_recipients", "schedule_id = %s", (sid,)) == 2 else ["recipients changed"]))
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NULL WHERE schedule_id = %s", (sid,))

    # ── Pause / resume ───────────────────────────────────────────────────────
    r = call("viewer", "POST", f"/scheduled-reports/{sid}/pause")
    rec.case("pause: a viewer is refused", expect(r, 403) + ([] if sched(sid)[8] else ["paused"]))
    r = call("analyst", "POST", f"/scheduled-reports/{sid}/pause")
    problems = expect(r, 200)
    s = sched(sid)
    if s[8] is not False or s[9] != "user":
        problems.append(f"{s}")
    first_paused_at = one("SELECT paused_at FROM report_schedules WHERE id = %s", (sid,))[0]
    r = call("analyst", "POST", f"/scheduled-reports/{sid}/pause")
    if expect(r, 200) or one("SELECT paused_at FROM report_schedules WHERE id = %s", (sid,))[0] != first_paused_at:
        problems.append("pausing twice changed the original pause")
    rec.case("pause: stops the schedule, and is idempotent", problems)
    q("UPDATE report_schedules SET consecutive_failures = 2, last_error = 'x', next_run_at = NOW() - INTERVAL '30 days' WHERE id = %s", (sid,))
    r = call("analyst", "POST", f"/scheduled-reports/{sid}/resume")
    problems = expect(r, 200)
    s = sched(sid)
    if s[8] is not True or s[9] is not None or s[10] != 0:
        problems.append(f"{s}")
    if s[11] <= datetime.now(timezone.utc):
        problems.append("a resumed schedule must not be due in the past (no catch-up of paused weeks)")
    rec.case("resume: re-arms from now, clears the failure count and reason", problems)
    q("UPDATE report_schedules SET enabled = FALSE, paused_reason = 'user' WHERE id = %s", (sid,))
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE schedule_id = %s", (sid,))
    r = call("analyst", "POST", f"/scheduled-reports/{sid}/resume")
    rec.case("resume: refused while everyone is unsubscribed (409)",
             expect(r, 409, "scheduled_report_no_recipients") + ([] if sched(sid)[8] is False else ["resumed"]))
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NULL WHERE schedule_id = %s", (sid,))
    call("analyst", "POST", f"/scheduled-reports/{sid}/resume")

    # ── Unsubscribe (public) ─────────────────────────────────────────────────
    ext_rid = one("SELECT id FROM report_schedule_recipients WHERE schedule_id = %s AND kind = 'external'", (sid,))[0]
    token = _unsub_token(fx.secret, ext_rid)
    r = ct.http(rs, "GET", f"{API}/public/report-unsubscribe?token={token}")
    problems = expect(r, 200)
    if one("SELECT unsubscribed_at FROM report_schedule_recipients WHERE id = %s", (ext_rid,))[0] is not None:
        problems.append("a GET unsubscribed (a mail scanner would do it)")
    rec.case("unsubscribe: GET only describes the link, it changes nothing", problems)
    for bad in (token[:-1] + ("A" if token[-1] != "A" else "B"), "garbage", f"{ext_rid}.", _unsub_token("other-secret", ext_rid)):
        r = ct.http(rs, "POST", f"{API}/public/report-unsubscribe", body={"token": bad})
        rec.case(f"unsubscribe: a forged token ({bad[:12]}...) is refused",
                 expect(r, 400, "report_unsubscribe_link_invalid") +
                 (["unsubscribed"] if one("SELECT unsubscribed_at FROM report_schedule_recipients WHERE id = %s", (ext_rid,))[0] else []))
    r = ct.http(rs, "POST", f"{API}/public/report-unsubscribe", body={"token": token})
    problems = expect(r, 200)
    if not problems and (r.body["data"]["already"] is not False or r.body["data"]["schedule_name"] != "Renamed"):
        problems.append(f"{r.body}")
    if one("SELECT unsubscribed_at FROM report_schedule_recipients WHERE id = %s", (ext_rid,))[0] is None:
        problems.append("not unsubscribed")
    ev = one("SELECT context, user_id FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.unsubscribed' AND resource = %s",
             (tenant, sid))
    if not ev or ev[0].get("email") != "board@example.com" or ev[1] != "system":
        problems.append(f"event {ev}")
    rec.case("unsubscribe: POST marks the recipient and records the event", problems)
    r = ct.http(rs, "POST", f"{API}/public/report-unsubscribe", body={"token": token})
    problems = expect(r, 200)
    if count("activity_logs", "tenant_id = %s AND action = 'scheduled_report.unsubscribed'") != 1:
        problems.append("a second event was written")
    if not problems and r.body["data"]["already"] is not True:
        problems.append("not marked already")
    rec.case("unsubscribe: repeating it is idempotent (one event)", problems)
    # The last active recipient leaving pauses the schedule, out loud.
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE schedule_id = %s AND id <> %s AND user_id <> %s",
      (sid, ext_rid, fx.admin_id))
    admin_rid = one("SELECT id FROM report_schedule_recipients WHERE schedule_id = %s AND user_id = %s", (sid, fx.admin_id))[0]
    r = ct.http(rs, "POST", f"{API}/public/report-unsubscribe", body={"token": _unsub_token(fx.secret, admin_rid)})
    problems = expect(r, 200)
    s = sched(sid)
    if s[8] is not False or s[9] != "no_recipients":
        problems.append(f"not paused: {s}")
    ev = one("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.auto_paused' AND resource = %s",
             (tenant, sid))
    if not ev or ev[0].get("reason") != "report_no_recipients" or ev[0].get("severity") != "warning":
        problems.append(f"event {ev}")
    rec.case("unsubscribe: the last recipient leaving pauses the schedule with an event", problems)
    q("UPDATE report_schedule_recipients SET unsubscribed_at = NULL WHERE schedule_id = %s", (sid,))
    call("analyst", "POST", f"/scheduled-reports/{sid}/resume")

    # ── Preview: renders for the caller, writes nothing ──────────────────────
    r = call("viewer", "POST", "/scheduled-reports/preview", {"sections": ["purchasing_summary"]})
    rec.case("preview: a viewer is refused", expect(r, 403))
    before_rows = (count("report_schedule_runs"), count("outbound_messages"), count("report_schedules"))
    r = call("analyst", "POST", "/scheduled-reports/preview", {"sections": SECTIONS, "frequency": "weekly", "name": "Try"})
    problems = expect(r, 200)
    if not problems:
        d = r.body["data"]
        codes = [s["code"] for s in d["report"]["sections"]]
        if d["sent"] is not False or d["preview"] is not True or codes != SECTIONS:
            problems.append(f"{d['sent']} {codes}")
        by = {s["code"]: s for s in d["report"]["sections"]}
        if by["budget_vs_spend"].get("reason") != "no_running_budget" or by["supplier_scorecard"].get("available") is not False:
            problems.append("a section with no data did not say not-available")
        if "Resumen de compras" not in d["html"] or "No disponible" not in d["html"]:
            problems.append("the html does not carry the sections")
    if (count("report_schedule_runs"), count("outbound_messages"), count("report_schedules")) != before_rows:
        problems.append("a preview wrote rows")
    rec.case("preview: an analyst gets the report, says not-available where it cannot know, writes nothing", problems)
    r = call("analyst", "POST", "/scheduled-reports/preview", {"schedule_id": sid})
    rec.case("preview: of a saved schedule", expect(r, 200))
    r = call("analyst", "POST", "/scheduled-reports/preview", {"sections": ["salaries"]})
    rec.case("preview: an unknown section is refused", expect(r, 422, "scheduled_report_section_unknown"))
    r = call("analyst", "POST", "/scheduled-reports/preview", {})
    rec.case("preview: without sections or a schedule is 422", expect(r, 422))

    # ── The worker, end to end ───────────────────────────────────────────────
    for t in ("report_schedule_runs", "report_schedule_recipients", "report_schedules"):
        q(f"DELETE FROM {t} WHERE tenant_id = %s", (tenant,))
    q("DELETE FROM outbound_messages WHERE tenant_id = %s", (tenant,))
    r = make(name="Due now", user_ids=[fx.admin_id, fx.analyst_id], external_emails=["board@example.com"])
    due_id = r.body["data"]["id"]
    q("UPDATE report_schedules SET next_run_at = NOW() - INTERVAL '1 minute' WHERE id = %s", (due_id,))
    q("UPDATE users SET status = 'suspended' WHERE id = %s", (fx.analyst_id,))      # left after it was defined

    def pass_once(mode: str = "ok"):
        return subprocess.run([sys.executable, "-c", PASS_SCRIPT, mode], cwd=root, env=env, capture_output=True,
                              text=True, timeout=600)

    procs: list = []
    threads = [threading.Thread(target=lambda: procs.append(pass_once())) for _ in range(3)]   # three workers at once
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    problems = [f"pass exit {p.returncode}: {(p.stderr or p.stdout)[-400:]}" for p in procs if p.returncode != 0]
    if not problems:
        made = sorted(int(next(l for l in p.stdout.splitlines() if l.startswith("MADE "))[5:]) for p in procs)
        sent = [json.loads(next(l for l in p.stdout.splitlines() if l.startswith("SENT "))[5:]) for p in procs]
        runs = q("SELECT status, recipients_queued, recipients_skipped FROM report_schedule_runs WHERE schedule_id = %s", (due_id,))
        if sum(made) != 1 or len(runs) != 1:
            problems.append(f"three workers made {made} runs, rows={runs}")
        mails = q("SELECT recipient, status, last_error FROM outbound_messages WHERE tenant_id = %s AND kind = 'scheduled_report' ORDER BY recipient", (tenant,))
        if len(mails) != 2:
            problems.append(f"expected a mail for the admin and the external only: {mails}")
        if runs and (runs[0][0] != "queued" or runs[0][1] != 2 or runs[0][2] != [{"recipient": fx.analyst_id, "kind": "user", "reason": "user_inactive"}]):
            problems.append(f"run {runs[0]}")
        flat = [m for per in sent for m in per]
        board = [m for m in flat if m[0] == "board@example.com"]
        if len(board) != 1:
            problems.append(f"board@example.com received {len(board)} mails: {[m[:2] for m in flat]}")
        else:
            rid = one("SELECT id FROM report_schedule_recipients WHERE schedule_id = %s AND email = 'board@example.com'", (due_id,))[0]
            if f"/reportes-programados/baja?token={_unsub_token(fx.secret, rid)}" not in board[0][2]:
                problems.append("the mail has no valid personal unsubscribe link")
            if "Resumen de compras" not in board[0][2]:
                problems.append("the mail has no report body")
        if [m for m in flat if fx.analyst_id in m[0] or m[0].startswith("contract-") and "analyst" in m[0]]:
            problems.append("the suspended analyst was mailed")
        trial = [m for m in mails if m[0].endswith("@stockai.demo")]
        if len(trial) != 1 or trial[0][1] != "abandoned" or trial[0][2] != "trial_address":
            problems.append(f"the made-up trial address must be refused by the transport: {mails}")
        s = sched(due_id)
        if s[12] != "queued" or s[10] != 0 or not s[11] > datetime.now(timezone.utc) + timedelta(days=1):
            problems.append(f"schedule after the run {s}")
    rec.case("worker: three simultaneous passes make ONE run, mail once per eligible recipient, skip the suspended one", problems)

    r = call("analyst", "GET", f"/scheduled-reports/{due_id}/runs")
    problems = expect(r, 200)
    if not problems:
        items = r.body["data"]["items"]
        if len(items) != 1 or items[0]["status"] != "queued" or items[0]["recipients_queued"] != 2:
            problems.append(f"{items}")
        else:
            d = items[0]["delivery"]
            if d != {"sent": 1, "pending": 0, "failed": 0, "abandoned": 1}:
                problems.append(f"delivery {d}")
            if items[0]["recipients_skipped"][0]["reason"] != "user_inactive":
                problems.append("skipped reason missing")
    rec.case("runs: the history says what was queued, skipped and what the outbox then did", problems)
    r = call("analyst", "GET", f"/scheduled-reports/{due_id}/runs?limit=0")
    rec.case("runs: limit 0 is 422", expect(r, 422))

    # Restart after the advance was lost: the period is owned, nothing is sent again.
    q("UPDATE report_schedules SET next_run_at = (SELECT due_at FROM report_schedule_runs WHERE schedule_id = %s) WHERE id = %s", (due_id, due_id))
    p = pass_once()
    problems = [] if p.returncode == 0 else [f"exit {p.returncode}: {p.stderr[-300:]}"]
    if count("report_schedule_runs", "schedule_id = %s", (due_id,)) != 1 or count("outbound_messages", "tenant_id = %s AND kind = 'scheduled_report'") != 2:
        problems.append("a re-claimed period produced a second run or mail")
    if "SENT []" not in p.stdout:
        problems.append(f"something was sent again: {p.stdout[-200:]}")
    rec.case("worker: a restart that finds its own period does nothing (no second run, no second mail)", problems)

    # No transport: a failed run, never a 'queued' one; three in a row pause the schedule.
    problems = []
    for week in range(3):
        # A different local minute each time: a different period, so each is a run of its own.
        # Offset by 30 minutes so the first one can never share a minute with the
        # "Due now" run above (their period keys would collide whenever the cases
        # above ran inside one wall-clock minute, and the pass would rightly skip it).
        q("UPDATE report_schedules SET next_run_at = NOW() - ((30 + 60 * %s) || ' minutes')::interval WHERE id = %s",
          (week, due_id))
        p = pass_once("no_transport")
        if p.returncode != 0:
            problems.append(f"exit {p.returncode}: {p.stderr[-300:]}")
            break
    s = sched(due_id)
    if s[8] is not False or s[9] != "failures" or s[10] != 3:
        problems.append(f"after three failures {s}")
    failed = q("SELECT status, error FROM report_schedule_runs WHERE schedule_id = %s AND status = 'failed'", (due_id,))
    if len(failed) != 3 or {f[1] for f in failed} != {"not_configured"}:
        problems.append(f"failed runs {failed}")
    ev = one("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.auto_paused' AND resource = %s ORDER BY created_at DESC LIMIT 1",
             (tenant, due_id))
    if not ev or ev[0].get("reason") != "report_failed_repeatedly" or ev[0].get("failures") != 3:
        problems.append(f"auto-pause event {ev}")
    rec.case("worker: with no mail transport the run FAILS (not queued); the third failure pauses the schedule with an event", problems)

    # Time zone: the tenant moves zones, the next pass re-anchors instead of firing at the old hour.
    q("UPDATE report_schedules SET enabled = TRUE, paused_reason = NULL, consecutive_failures = 0, next_run_at = NOW() - INTERVAL '1 minute' WHERE id = %s", (due_id,))
    q("DELETE FROM report_schedule_runs WHERE schedule_id = %s", (due_id,))
    q("DELETE FROM outbound_messages WHERE tenant_id = %s AND kind = 'scheduled_report'", (tenant,))
    r = call("admin", "PATCH", "/tenant/timezone", {"timezone": "America/Bogota"})
    problems = expect(r, 200)
    p = pass_once()
    problems += [] if p.returncode == 0 else [f"exit {p.returncode}: {p.stderr[-300:]}"]
    s = sched(due_id)
    if s[7] != "America/Bogota" or count("report_schedule_runs", "schedule_id = %s", (due_id,)) != 0:
        problems.append(f"zone {s[7]}, runs {count('report_schedule_runs', 'schedule_id = %s', (due_id,))}")
    try:
        from croniter import croniter
        from zoneinfo import ZoneInfo
        z = ZoneInfo("America/Bogota")
        exp = croniter(s[6], datetime.now(z)).get_next(datetime).astimezone(timezone.utc)
        if abs((s[11] - exp).total_seconds()) > 120:
            problems.append(f"next_run_at {s[11]} != {exp}")
    except ImportError:
        pass
    call("admin", "PATCH", "/tenant/timezone", {"timezone": "America/Costa_Rica"})
    rec.case("worker: a tenant that changed zone is re-anchored, not fired at the old hour", problems)

    # ── Health ───────────────────────────────────────────────────────────────
    for name, base in (("rust", rs), ("python", py)):
        h = ct.http(base, "GET", "/health")
        loops = [l["loop"] for l in (h.body or {}).get("loops", [])]
        rec.case(f"health ({name}): lists the scheduled_reports loop", [] if "scheduled_reports" in loops else [f"{loops}"])

    # ── Delete ───────────────────────────────────────────────────────────────
    q("INSERT INTO report_schedule_runs (tenant_id, schedule_id, period_key, due_at) VALUES (%s, %s, 'k', NOW())", (tenant, due_id))
    r = call("viewer", "DELETE", f"/scheduled-reports/{due_id}")
    rec.case("delete: a viewer is refused (403, intact)", expect(r, 403) + ([] if sched(due_id) else ["deleted"]))
    r = call("analyst", "DELETE", f"/scheduled-reports/{due_id}")
    problems = expect(r, 200)
    for t in ("report_schedules", "report_schedule_recipients", "report_schedule_runs"):
        if count(t, "tenant_id = %s AND " + ("id" if t == "report_schedules" else "schedule_id") + " = %s", (tenant, due_id)):
            problems.append(f"{t} kept rows")
    if not one("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'audit.report_schedule.deleted' AND resource = %s", (tenant, due_id)):
        problems.append("no audit row")
    rec.case("delete: removes the schedule, its recipients and its history, audited", problems)
    r = call("analyst", "DELETE", f"/scheduled-reports/{due_id}")
    rec.case("delete: again is 404", expect(r, 404, "scheduled_report_not_found"))

    reset()
    return rec.results


def main() -> None:
    import argparse
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import contract_test as ct
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", required=True)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    env = ct.read_env_file(args.env_file) if args.env_file and os.path.exists(args.env_file) else {}
    secret = os.environ.get("SECRET_KEY") or env.get("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY not found")
    import psycopg2
    db = psycopg2.connect(args.db)
    db.autocommit = True
    fx = ct.make_fixture(args.python, secret)
    print(f"throwaway tenant {fx.tenant_id}")
    try:
        results = run(args, fx, db, ct)
    finally:
        ct.erase_fixture(args.python, fx)
    width = max(len(c.name) for c, _, _ in results) if results else 0
    for case, verdict, problems in results:
        print(f"{verdict:4}  {case.name:<{width}}")
        for p in problems:
            print(f"        - {p}")
    failed = sum(1 for _, v, _ in results if v == "FAIL")
    print(f"\n{len(results) - failed}/{len(results)} cases pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
