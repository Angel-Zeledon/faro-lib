"""Wave 2b contract cases: data-freshness, tenant export, whole-tenant erasure.

Called from contract_test.py (`run`), kept in its own file so the shared
harness gains three lines, not a section. It reuses the harness' own helpers
(`http`, `make_fixture`, `mint_access_token`, `diff`, ...) through a plain
import, so a fix there fixes both.

Everything runs against throwaway tenants (`Contract <hex>`, `@stockai.demo`
logins the mail transport refuses) on whatever database `--db` names, which
must be disposable. Erasure is destructive by nature: every erase case runs on
tenants created for it, one per side, and a bystander tenant proves the
other tenants' rows survive.

The strongest check is the row-for-row one: before and after each erasure the
row count of EVERY table in the database is read, and the per-table deltas of
the tenant Python erased and the tenant Rust erased (seeded identically) must
be the same. A table Rust forgot to clear shows up as a nonzero residue in
one delta and not the other, whether or not it has a `tenant_id` column.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import secrets
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
D = 24  # hours in a day; offsets below add 12h so no case sits on a day boundary


def _ct():
    import contract_test as ct  # noqa: PLC0415 - the harness itself
    return ct


# ── Comparison plumbing ──────────────────────────────────────────────────────

def _subst(value: Any, subs: dict) -> Any:
    if not subs:
        return value
    text = json.dumps(value)
    for old, new in subs.items():
        text = text.replace(old, new)
    return json.loads(text)


def compare(ct, args, name, route, method, path, *, token_py, token_rs, body=None, raw_body=None,
            content_type="application/json", headers=None, subs_py=None, subs_rs=None,
            exact=False, check=None, mask_keys=()):
    """One request to each service; returns (Case, verdict, problems)."""
    case = ct.Case(name, method, path, route=route)
    if args.only and args.only not in name:
        return None
    kw = dict(body=body, raw_body=raw_body, content_type=content_type, headers=headers or {})
    rp = ct.http(args.python, method, path, token=token_py, **kw)
    rr = ct.http(args.rust, method, path, token=token_rs, **kw)
    problems: list[str] = []
    if rp.status != rr.status:
        problems.append(f"status python={rp.status} rust={rr.status}")
    bp, br = _subst(rp.body, subs_py), _subst(rr.body, subs_rs)
    if rp.headers.get("content-type") == "application/zip" and rr.headers.get("content-type") == "application/zip":
        pass  # an archive: compared member by member in `check`
    elif exact:
        # Raw equality (timestamps included), apart from `meta.timestamp`.
        for b in (bp, br):
            if isinstance(b, dict) and isinstance(b.get("meta"), dict):
                b["meta"]["timestamp"] = "<masked>"
        problems += ct.diff(bp, br)
    else:
        problems += ct.diff(ct.normalize(bp, set(mask_keys)), ct.normalize(br, set(mask_keys)))
    for h in ("www-authenticate", "retry-after"):
        if rp.headers.get(h) != rr.headers.get(h):
            problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")
    if check is not None:
        problems += check(rp, rr)
    hard = [p for p in problems if not p.startswith("(")]
    return case, ("FAIL" if hard else "PASS"), problems


def _cur(db):
    return db.cursor()


def _q(db, sql, params=()):
    cur = db.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


# ── 1. data-freshness ────────────────────────────────────────────────────────

def _iso_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def fr_seed(db, fx, spec: dict) -> dict:
    """Replace the tenant's stock, warehouses and sessions with `spec`."""
    cur, tid = db.cursor(), fx.tenant_id
    for t in ("inventory_stock", "warehouses"):
        cur.execute(f"DELETE FROM {t} WHERE tenant_id = %s", (tid,))
    cur.execute("DELETE FROM sessions WHERE tenant_id = %s", (tid,))
    wh: dict[str, str] = {}
    for name in spec.get("warehouses", []):
        cur.execute("INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) RETURNING id", (tid, name))
        wh[name] = cur.fetchone()[0]
    for sku, warehouse, hours in spec.get("stock", []):
        cur.execute("""INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, updated_at)
                       VALUES (%s, %s, %s, 5, NOW() - %s * INTERVAL '1 hour')""", (tid, sku, warehouse, hours))
    for s in spec.get("sessions", []):
        sid = "sess_fr_" + secrets.token_hex(5)
        cur.execute("""INSERT INTO sessions (id, tenant_id, name, status, updated_at, archived_at, is_backtest)
                       VALUES (%s, %s, %s, %s, NOW() - %s * INTERVAL '1 hour',
                               CASE WHEN %s THEN NOW() ELSE NULL END, %s)""",
                    (sid, tid, s.get("name", "Fresh " + sid[-4:]), s.get("status", "COMPLETED"),
                     s.get("hours", 2 * D + 12), s.get("archived", False), s.get("backtest", False)))
        if s.get("inspection") is not None:
            cur.execute("INSERT INTO session_configs (session_id, tenant_id, inspection) VALUES (%s, %s, %s)",
                        (sid, tid, json.dumps(s["inspection"])))
        if s.get("result") is not None:
            cur.execute("INSERT INTO session_results (session_id, tenant_id, training_result) VALUES (%s, %s, %s)",
                        (sid, tid, json.dumps(s["result"])))
    return wh


def _insp(date_max: Any) -> dict:
    return {"profile": {"stats": {"date_max": date_max}}}


def freshness_specs() -> list[tuple[str, dict]]:
    sess = lambda **kw: dict(kw)  # noqa: E731
    return [
        ("empty tenant", {}),
        ("fresh data", dict(warehouses=["principal"],
                            stock=[("A", "principal", D + 12), ("B", "principal", 2 * D + 12)],
                            sessions=[sess(hours=2 * D + 12, inspection=_insp(_iso_ago(3)))])),
        ("sales stale by file date, stock stale", dict(
            warehouses=["principal"], stock=[("A", "principal", 8 * D + 12)],
            sessions=[sess(hours=D + 12, inspection=_insp(_iso_ago(20)))])),
        ("both blind: degraded by stock and sales", dict(
            stock=[("A", "principal", 25 * D + 12)],
            sessions=[sess(hours=D + 12, inspection=_insp(_iso_ago(50)))])),
        ("sales blind only", dict(
            stock=[("A", "principal", 2 * D + 12)],
            sessions=[sess(hours=D + 12, inspection=_insp(_iso_ago(46)))])),
        ("unparseable date falls back to upload date", dict(
            stock=[("A", "principal", D + 12)],
            sessions=[sess(hours=16 * D + 12, inspection=_insp("not-a-date"))])),
        ("no config row falls back to upload date", dict(
            stock=[("A", "principal", D + 12)], sessions=[sess(hours=3 * D + 12)])),
        ("date_max missing", dict(sessions=[sess(hours=5 * D + 12, inspection={"profile": {"stats": {}}})])),
        ("date_max empty string", dict(sessions=[sess(hours=5 * D + 12, inspection=_insp(""))])),
        ("date_max with a time", dict(
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(10) + " 10:30:00"))])),
        ("date_max ISO with Z and fraction", dict(
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(10) + "T10:30:15.250Z"))])),
        ("date_max compact", dict(
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(10).replace("-", "")))])),
        ("date_max a number", dict(sessions=[sess(hours=6 * D + 12, inspection=_insp(20260101))])),
        ("date_max in the future", dict(sessions=[sess(hours=D, inspection=_insp(_iso_ago(-9)))])),
        ("archived newest session is ignored", dict(sessions=[
            sess(hours=D + 12, archived=True, name="Newer archived", inspection=_insp(_iso_ago(1))),
            sess(hours=9 * D + 12, name="Older live", inspection=_insp(_iso_ago(12)))])),
        ("backtest newest session is ignored", dict(sessions=[
            sess(hours=D + 12, backtest=True, name="Newer backtest", inspection=_insp(_iso_ago(1))),
            sess(hours=9 * D + 12, name="Older real", inspection=_insp(_iso_ago(12)))])),
        ("running newest session is ignored", dict(sessions=[
            sess(hours=D + 12, status="RUNNING", name="Newer running", inspection=_insp(_iso_ago(1))),
            sess(hours=9 * D + 12, name="Older done", inspection=_insp(_iso_ago(12)))])),
        ("one warehouse stale: no per-warehouse story", dict(
            warehouses=["principal"], stock=[("A", "principal", 12 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)))])),
        ("one warehouse lags while others report", dict(
            warehouses=["Norte", "Sur", "Este"],
            stock=[("A", "Norte", D + 12), ("B", "Norte", 2 * D + 12), ("C", "Sur", 12 * D + 12),
                   ("D", "este", 30 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)),
                           result={"store_data_through": {"Sur": _iso_ago(15), "NORTE": _iso_ago(2),
                                                          "  Este ": _iso_ago(40)}})])),
        ("a warehouse the sales file reports newer than its stock", dict(
            warehouses=["Norte", "Sur"],
            stock=[("A", "Norte", D + 12), ("C", "Sur", 12 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)),
                           result={"store_data_through": {"Sur": _iso_ago(1)}})])),
        ("every warehouse late: nothing lags", dict(
            warehouses=["Norte", "Sur"],
            stock=[("A", "Norte", 12 * D + 12), ("C", "Sur", 14 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)))])),
        ("registered warehouse with no stock and no sales", dict(
            warehouses=["Norte", "Vacia"], stock=[("A", "Norte", D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)))])),
        ("warehouse names: unicode, casefold, sort", dict(
            warehouses=["STRASSE", "b", "A", "Ñandú"],
            stock=[("S", "straße", D + 12), ("X", "c", D + 12), ("N", "ñandú", 20 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)))])),
        ("store_data_through is not an object", dict(
            warehouses=["Norte", "Sur"], stock=[("A", "Norte", D + 12), ("C", "Sur", D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)), result={"store_data_through": ["Sur"]})])),
        ("store dates: junk, number, empty", dict(
            warehouses=["Norte", "Sur", "Este"],
            stock=[("A", "Norte", D + 12), ("C", "Sur", 13 * D + 12), ("E", "Este", 13 * D + 12)],
            sessions=[sess(hours=D, inspection=_insp(_iso_ago(2)),
                           result={"store_data_through": {"Norte": "junk", "Sur": 20260101, "Este": ""}})])),
    ]


# What the Python answer must say for the named specs: a case that passes
# because BOTH sides returned an empty or error answer proves nothing.
FRESHNESS_EXPECT = {
    "fresh data": lambda d: d["semaphore"] == "current" and d["warn"] is False
    and d["stock"]["tracked_skus"] == 2 and d["sales"]["basis"] == "data_date",
    "both blind: degraded by stock and sales": lambda d: d["semaphore"] == "degraded"
    and d["degraded_by"] == ["stock", "sales"] and d["warn"] is True,
    "unparseable date falls back to upload date": lambda d: d["sales"]["basis"] == "upload_date"
    and d["sales"]["state"] == "stale",
    "archived newest session is ignored": lambda d: d["sales"]["session_name"] == "Older live",
    "one warehouse lags while others report": lambda d: d["warehouses"]["lagging"] == ["Este", "Sur"]
    and [i["name"] for i in d["warehouses"]["items"]] == ["Este", "Norte", "Sur"],
    "every warehouse late: nothing lags": lambda d: d["warehouses"]["lagging"] == []
    and d["warehouses"]["multi"] is True,
    "warehouse names: unicode, casefold, sort": lambda d: [i["name"] for i in d["warehouses"]["items"]]
    == ["A", "b", "c", "STRASSE", "Ñandú"],
}


def _expect(name):
    fn = FRESHNESS_EXPECT.get(name)
    if fn is None:
        return None

    def check(rp, rr):
        try:
            ok = rp.status == 200 and fn(rp.body["data"])
        except (KeyError, TypeError, IndexError):
            ok = False
        return [] if ok else [f"the Python answer is not the scenario the case names: {json.dumps(rp.body)[:300]}"]
    return check


def run_freshness(args, db, py_secret_ctx) -> list:
    ct = _ct()
    out: list = []
    fx = ct.make_fixture(args.python, py_secret_ctx)
    print(f"w2b: freshness tenant {fx.tenant_id}")
    try:
        # Another tenant's data must never leak in: seed the SAME tenant-independent
        # rows into a second tenant that is not queried.
        other = ct.make_fixture(args.python, py_secret_ctx)
        try:
            fr_seed(db, other, dict(warehouses=["Intruder"], stock=[("Z", "Intruder", 1)],
                                    sessions=[dict(hours=1, inspection=_insp(_iso_ago(0)),
                                                   result={"store_data_through": {"Intruder": _iso_ago(0)}})]))
            for name, spec in freshness_specs():
                def seeded(spec=spec):
                    fr_seed(db, fx, spec)
                seeded()
                tok = ct.auth_for(fx, "admin")
                res = compare(ct, args, f"freshness: {name}", "GET /data-freshness", "GET", f"{ct.API}/data-freshness",
                              token_py=tok, token_rs=tok, exact=True, check=_expect(name))
                if res:
                    out.append(res)

            # Roles, keys and auth failures on one populated tenant.
            fr_seed(db, fx, freshness_specs()[18][1])
            for who in ("viewer", "analyst", "key_read", "key_write", "none", "bad_signature", "expired",
                        "refresh_type", "hs512"):
                tok = ct.auth_for(fx, who)
                if who.startswith("key_") and tok is None:
                    out.append((ct.Case(f"freshness: {who}", "GET", "-", route="GET /data-freshness"), "SKIP",
                                ["no API key in this tenant"]))
                    continue
                res = compare(ct, args, f"freshness: as {who}", "GET /data-freshness", "GET",
                              f"{ct.API}/data-freshness", token_py=tok, token_rs=tok, exact=True)
                if res:
                    out.append(res)
            res = compare(ct, args, "freshness: X-API-Key header", "GET /data-freshness", "GET",
                          f"{ct.API}/data-freshness", token_py=None, token_rs=None,
                          headers={"X-API-Key": fx.read_key}, exact=True) if fx.read_key else None
            if res:
                out.append(res)

            # Warehouse-scoped callers (users.warehouse_scope holds warehouse ids).
            wh = fr_seed(db, fx, freshness_specs()[18][1])
            cur = db.cursor()
            for label, scope in (
                ("one warehouse", [wh["Norte"]]),
                ("the lagging one only", [wh["Sur"]]),
                ("two of three", [wh["Norte"], wh["Este"]]),
                ("empty scope", []),
                ("only a stale foreign id", ["wh-does-not-exist"]),
            ):
                cur.execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                            (json.dumps(scope), fx.analyst_id))
                tok = ct.auth_for(fx, "analyst")
                res = compare(ct, args, f"freshness: scoped analyst ({label})", "GET /data-freshness", "GET",
                              f"{ct.API}/data-freshness", token_py=tok, token_rs=tok, exact=True)
                if res:
                    out.append(res)
            cur.execute("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (fx.analyst_id,))
        finally:
            ct.erase_fixture(args.python, other)
    finally:
        ct.erase_fixture(args.python, fx)
    out.append(_constants_case(ct))
    return out


def _constants_case(ct):
    """The thresholds in the Rust file are the Python constants."""
    py = (ROOT / "backend" / "notifications" / "freshness_service.py").read_text(encoding="utf-8")
    rs = (ROOT / "backend-rs" / "src" / "routes" / "w2b" / "freshness.rs").read_text(encoding="utf-8")
    problems = []
    for name in ("SALES_STALE_DAYS", "SALES_REMINDER_DAYS", "SALES_BLIND_DAYS", "STOCK_STALE_DAYS", "STOCK_BLIND_DAYS"):
        a = re.search(rf"^{name}\s*=\s*(\d+)", py, re.M)
        b = re.search(rf"pub const {name}: i64 = (\d+);", rs)
        if not a or not b or a.group(1) != b.group(1):
            problems.append(f"{name}: python={a and a.group(1)} rust={b and b.group(1)}")
    return (ct.Case("freshness: thresholds equal the Python constants", "-", "-", route="GET /data-freshness"),
            "FAIL" if problems else "PASS", problems)


# ── 2. generic seeding of every tenant table ─────────────────────────────────

def _tenant_tables(cur) -> list[str]:
    cur.execute("""SELECT c.table_name FROM information_schema.columns c
                   JOIN information_schema.tables t
                     ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                  WHERE c.column_name = 'tenant_id' AND c.table_schema = 'public'
                    AND t.table_type = 'BASE TABLE' ORDER BY c.table_name""")
    return [r[0] for r in cur.fetchall()]


def _columns(cur, table):
    cur.execute("""SELECT column_name, data_type, udt_name, is_nullable, column_default, is_generated, is_identity
                     FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s
                    ORDER BY ordinal_position""", (table,))
    return cur.fetchall()


def _fks(cur, table) -> dict:
    cur.execute("""SELECT a.attname, cf.relname, af.attname
                     FROM pg_constraint k
                     JOIN pg_class c ON c.oid = k.conrelid
                     JOIN pg_class cf ON cf.oid = k.confrelid
                     JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
                     JOIN pg_attribute af ON af.attrelid = k.confrelid AND af.attnum = k.confkey[1]
                    WHERE k.contype = 'f' AND c.relname = %s AND array_length(k.conkey, 1) = 1""", (table,))
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _check_values(cur, table) -> dict:
    """First allowed literal of every `col IN ('a', 'b')` CHECK on the table."""
    cur.execute("""SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
                    WHERE k.contype = 'c' AND c.relname = %s""", (table,))
    out = {}
    for (definition,) in cur.fetchall():
        m = re.search(r"\(\(?(\w+) = ANY \(ARRAY\[(.*?)\]", definition)
        if m:
            lits = re.findall(r"'([^']*)'::text", m.group(2))
            if lits:
                out[m.group(1)] = lits[0]
    # Multi-column rules the pattern above cannot read.
    out.update({"purchase_budgets": {"scope_type": "company"}}.get(table, {}))
    return out


def _synth(data_type, udt, name):
    if data_type == "ARRAY":
        return "{}", f"{udt[1:]}[]"
    if udt in ("text", "varchar", "bpchar"):
        return "ct-" + name, udt
    if udt in ("int2", "int4", "int8"):
        return 1, udt
    if udt in ("float4", "float8", "numeric"):
        return 1.5, udt
    if udt == "bool":
        return False, "bool"
    if udt in ("timestamptz", "timestamp"):
        return None, udt  # NOW(), spliced below
    if udt == "date":
        return None, "date"
    if udt in ("jsonb", "json"):
        return "{}", udt
    if udt == "uuid":
        return None, "uuid"
    return "ct", udt


REFUSALS: dict = {}


def seed_generic(dsn: str, tenant_id: str, user_id: str, *, skip=("tenants", "users")) -> tuple[int, list[str]]:
    """One synthetic row in every tenant table that accepts one. Foreign keys
    are satisfied from the tenant's own rows; tables are retried in passes so
    parents go first. Returns (tables seeded, tables that refused a row)."""
    import psycopg2  # noqa: PLC0415
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    tables = [t for t in _tenant_tables(cur) if t not in skip]
    pending = list(tables)
    seeded = 0
    for _ in range(6):
        progress = False
        for table in list(pending):
            cur.execute(f"SELECT 1 FROM {table} WHERE tenant_id = %s LIMIT 1", (tenant_id,))
            if cur.fetchone():
                pending.remove(table)
                seeded += 1
                continue
            cols = _columns(cur, table)
            fks = _fks(cur, table)
            names, holders, values = [], [], []
            allowed = _check_values(cur, table)
            ok = True
            for name, dtype, udt, nullable, default, generated, identity in cols:
                if generated == "ALWAYS" or identity == "YES":
                    continue
                if name == "tenant_id":
                    names.append(name); holders.append("%s"); values.append(tenant_id)
                    continue
                if name in fks:
                    rt, rc = fks[name]
                    if rt == "tenants":
                        v = tenant_id
                    elif rt == "users":
                        v = user_id
                    else:
                        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s "
                                    "AND column_name = 'tenant_id' AND table_schema = 'public'", (rt,))
                        scoped = cur.fetchone()
                        cur.execute(f"SELECT {rc} FROM {rt}" + (" WHERE tenant_id = %s" if scoped else "") + " LIMIT 1",
                                    (tenant_id,) if scoped else ())
                        row = cur.fetchone()
                        v = row[0] if row else None
                    if v is None:
                        if nullable == "NO":
                            ok = False
                            break
                        continue
                    names.append(name); holders.append("%s"); values.append(v)
                    continue
                if name in allowed:
                    names.append(name); holders.append("%s"); values.append(allowed[name])
                    continue
                if nullable == "YES" or default is not None:
                    continue
                v, cast = _synth(dtype, udt, name)
                names.append(name)
                if v is None and udt in ("timestamptz", "timestamp"):
                    holders.append("NOW()")
                elif v is None and udt == "date":
                    holders.append("CURRENT_DATE")
                elif v is None and udt == "uuid":
                    holders.append("gen_random_uuid()")
                else:
                    holders.append(f"%s::{cast}")
                    values.append(v)
            if not ok:
                continue
            cur.execute("SAVEPOINT s")
            try:
                cur.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(holders)})", values)
                cur.execute("RELEASE SAVEPOINT s")
                pending.remove(table)
                seeded += 1
                progress = True
            except Exception as exc:  # noqa: BLE001 - a table that refuses a synthetic row is reported, not fatal
                cur.execute("ROLLBACK TO SAVEPOINT s")
                REFUSALS[table] = str(exc).splitlines()[0][:140]
        conn.commit()
        if not progress:
            break
    conn.commit()
    conn.close()
    return seeded, pending


def table_counts(db) -> dict[str, int]:
    cur = db.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_type = 'BASE TABLE' ORDER BY table_name")
    out = {}
    for (t,) in cur.fetchall():
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        out[t] = cur.fetchone()[0]
    return out


def tenant_counts(db, tenant_id: str) -> dict[str, int]:
    cur = db.cursor()
    out = {}
    for t in _tenant_tables(cur):
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant_id = %s", (tenant_id,))
        out[t] = cur.fetchone()[0]
    return out


# ── 3. tenant export ─────────────────────────────────────────────────────────

def _exotics(db, fx):
    """Values the JSON rendering has to get exactly like Python's."""
    cur = db.cursor()
    cur.execute("DELETE FROM inventory_stock WHERE tenant_id = %s", (fx.tenant_id,))
    rows = [
        ("S-NAN", "Nan 'quote' \"dq\" \\ back\nline\ttab éñ ☃ \U0001f600", "NaN"),
        ("S-INF", "ctrl \x01\x1f del \x7f", "Infinity"),
        ("S-NEG", "plain", "0"),
        ("S-F", "float", "0.1"),
        ("S-BIG", "big", "1e22"),
        ("S-SMALL", "small", "0.00001234"),
    ]
    for sku, name, val in rows:
        cur.execute("""INSERT INTO inventory_stock (tenant_id, sku, display_name, current_stock, updated_at)
                       VALUES (%s, %s, %s, %s::float8, TIMESTAMPTZ '2026-03-04 05:06:07.080900+00')""",
                    (fx.tenant_id, sku, name, val))
    cur.execute("UPDATE inventory_stock SET unit_cost = '-Infinity'::float8 WHERE tenant_id = %s AND sku = 'S-NEG'",
                (fx.tenant_id,))
    cur.execute("UPDATE sessions SET tags = %s::jsonb WHERE tenant_id = %s",
                (json.dumps(["a", 1, 2.0, 1e20, {"k": [None, True, 1.5e-7, "xé"]}, {}, []]), fx.tenant_id))
    cur.execute("UPDATE webhooks SET events = ARRAY['a.b', 'c d'] WHERE tenant_id = %s", (fx.tenant_id,))
    cur.execute("UPDATE tenants SET quota = %s::jsonb WHERE id = %s",
                (json.dumps({"max_skus": 7, "ratio": 0.25, "big": 12345678901234}), fx.tenant_id))


def _zip_members(raw: bytes) -> tuple[list[str], dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        return names, {n: zf.read(n) for n in names}


def _export_check(db, fx, storage: Path):
    def check(rp, rr):
        problems = []
        if rp.status != 200 or rr.status != 200:
            return problems
        for r in (rp, rr):
            if r.headers.get("content-type") != "application/zip":
                problems.append(f"content-type {r.headers.get('content-type')!r}")
        dp, dr = rp.headers.get("content-disposition"), rr.headers.get("content-disposition")
        if dp != dr:
            problems.append(f"content-disposition python={dp!r} rust={dr!r}")
        np_, mp = _zip_members(rp.raw)
        nr, mr = _zip_members(rr.raw)
        if np_ != nr:
            problems.append(f"member list python={np_} rust={nr}")
        reordered = 0
        for name in np_:
            if name not in mr:
                continue
            a, b = mp[name], mr[name]
            if name == "manifest.json":
                ja, jb = json.loads(a), json.loads(b)
                ja.pop("generated_at", None), jb.pop("generated_at", None)
                if ja != jb:
                    problems.append(f"manifest differs: {ja} vs {jb}")
                continue
            if a == b:
                continue
            if name.endswith(".json"):
                try:
                    la, lb = json.loads(a), json.loads(b)
                except ValueError:
                    problems.append(f"{name}: not JSON on one side")
                    continue
                if isinstance(la, list) and isinstance(lb, list) and \
                        sorted(json.dumps(x, sort_keys=True) for x in la) == sorted(json.dumps(x, sort_keys=True) for x in lb):
                    reordered += 1
                    continue
            i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
            problems.append(f"{name}: bytes differ (python {len(a)}B, rust {len(b)}B) at {i}: "
                            f"python {a[max(0, i - 40):i + 60]!r} rust {b[max(0, i - 40):i + 60]!r}")
        if reordered:
            problems.append(f"(note: {reordered} member(s) hold the same rows in a different order; "
                            "the query has no ORDER BY on either side)")
        # The scenario really is in the Python archive (a pass over two empty
        # archives would prove nothing).
        inv = mp.get("inventory_stock.json", b"").decode("utf-8", "replace")
        sess = mp.get("sessions.json", b"").decode("utf-8", "replace")
        for label, ok in (
            ("NaN, Infinity and -Infinity floats", all(t in inv for t in (": NaN", ": Infinity"))
             and '"unit_cost": -Infinity' in inv),
            ("a control-character and emoji string", r"\u0001" in inv and "😀" in inv),
            ("a 10^20 integer inside jsonb", "100000000000000000000" in sess),
            ("the screenshot file", "feedback_screenshots/shot1.png" in np_
             and "feedback_screenshots/other.png" not in np_ and len([n for n in np_ if n.startswith("feedback_screenshots/")]) == 1),
            ("one row per seeded table", all(json.loads(mp[n]) for n in np_ if n.endswith(".json")
                                              and n not in ("manifest.json",) and json.loads(mp[n]) != [])),
        ):
            if not ok:
                problems.append(f"the Python archive lacks the scenario: {label}")
        # No credential in the file: the stored hashes must appear nowhere.
        cur = db.cursor()
        cur.execute("SELECT hashed_password FROM users WHERE tenant_id = %s AND hashed_password IS NOT NULL",
                    (fx.tenant_id,))
        secrets_in_db = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT key_hash FROM api_keys WHERE tenant_id = %s", (fx.tenant_id,))
        secrets_in_db += [r[0] for r in cur.fetchall()]
        for side, members in (("python", mp), ("rust", mr)):
            blob = b"".join(members.values())
            for s in secrets_in_db:
                if s and s.encode() in blob:
                    problems.append(f"{side} export contains a stored credential hash")
        return problems
    return check


def run_export(args, db, secret) -> list:
    ct = _ct()
    out: list = []
    fx = ct.make_fixture(args.python, secret)
    storage = _storage_path(args)
    try:
        seeded, refused = seed_generic(args.db, fx.tenant_id, fx.admin_id)
        _exotics(db, fx)
        shots_dir = storage / "feedback" / fx.tenant_id
        shots_dir.mkdir(parents=True, exist_ok=True)
        (shots_dir / "shot1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + secrets.token_bytes(64))
        (shots_dir / "other.png").write_bytes(b"not exported: no row names it")
        cur = db.cursor()
        cur.execute("DELETE FROM feedback_reports WHERE tenant_id = %s", (fx.tenant_id,))
        for i, path in enumerate(("shot1.png", "../escape.png", "sub/x.png", "/etc/passwd", "gone.png", "")):
            cur.execute("""INSERT INTO feedback_reports (id, tenant_id, user_id, message, screenshot_path)
                           VALUES (%s, %s, %s, 'm', %s)""", (f"fb_ct_{i}", fx.tenant_id, fx.admin_id, path))
        cur.execute("INSERT INTO feedback_reports (id, tenant_id, user_id, message) VALUES ('fb_ct_n', %s, %s, 'm')",
                    (fx.tenant_id, fx.admin_id))
        out.append((ct.Case("export: generic seed coverage", "-", "-", route="GET /tenant/export"), "PASS",
                    [f"(seeded {seeded} tables; {len(refused)} refused a synthetic row: {', '.join(refused[:12])})"]))

        cur.execute("DELETE FROM activity_logs WHERE tenant_id = %s AND action = 'audit.export.tenant_data'",
                    (fx.tenant_id,))
        tok = ct.auth_for(fx, "admin")
        res = compare(ct, args, "export: admin gets the same archive", "GET /tenant/export", "GET",
                      f"{ct.API}/tenant/export", token_py=tok, token_rs=tok, check=_export_check(db, fx, storage))
        if res:
            out.append(res)

        def audit_problems():
            cur2 = db.cursor()
            cur2.execute("""SELECT user_id, resource, context, status FROM activity_logs
                             WHERE tenant_id = %s AND action = 'audit.export.tenant_data' ORDER BY created_at""",
                         (fx.tenant_id,))
            rows = cur2.fetchall()
            if len(rows) != 2:
                return [f"expected 2 audit rows (one per side), found {len(rows)}"]
            return [] if rows[0] == rows[1] else [f"audit rows differ: {rows[0]} vs {rows[1]}"]
        probs = audit_problems()
        out.append((ct.Case("export: audit row written the same by both", "GET", "-", route="GET /tenant/export"),
                    "FAIL" if probs else "PASS", probs))

        for who in ("viewer", "analyst", "key_read", "key_write", "none", "bad_signature", "expired"):
            tok = ct.auth_for(fx, who)
            if who.startswith("key_") and tok is None:
                continue
            res = compare(ct, args, f"export: as {who}", "GET /tenant/export", "GET", f"{ct.API}/tenant/export",
                          token_py=tok, token_rs=tok)
            if res:
                out.append(res)
        cur.execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (fx.admin_id,))
        tok = ct.auth_for(fx, "admin")
        res = compare(ct, args, "export: warehouse-scoped admin refused", "GET /tenant/export", "GET",
                      f"{ct.API}/tenant/export", token_py=tok, token_rs=tok)
        if res:
            out.append(res)
        cur.execute("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (fx.admin_id,))
    finally:
        ct.erase_fixture(args.python, fx)
    return out


def _storage_path(args) -> Path:
    env = _ct().read_env_file(args.env_file) if args.env_file else {}
    p = env.get("STORAGE_PATH")
    return Path(p) if p else ROOT / "backend" / "storage"


# ── 4. tenant erasure ────────────────────────────────────────────────────────

STORAGE_CATEGORIES = ("tenants", "users", "sessions", "datasets", "jobs", "artifacts", "pos",
                      "documents", "logs", "feedback")


def _seed_storage(storage: Path, tenant_id: str):
    for cat in STORAGE_CATEGORIES:
        d = storage / cat / tenant_id
        if cat == "logs":
            # A plain FILE where a directory is expected: rmtree refuses it on
            # both sides, so it stays and is not reported as removed.
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text("not a directory")
        else:
            (d / "nested").mkdir(parents=True, exist_ok=True)
            (d / "nested" / "f.bin").write_bytes(secrets.token_bytes(16))


def _pair(ct, args, secret, label, *, seed=True):
    """Two identically seeded tenants: [0] is erased by Python, [1] by Rust."""
    sides = []
    for _ in range(2):
        fx = ct.make_fixture(args.python, secret)
        if seed:
            seed_generic(args.db, fx.tenant_id, fx.admin_id)
            _seed_storage(_storage_path(args), fx.tenant_id)
        sides.append(fx)
    return sides


def _slug(db, tenant_id):
    return _q(db, "SELECT slug FROM tenants WHERE id = %s", (tenant_id,))[0][0]


def _set_sub(db, tenant_id, status, **kw):
    cur = db.cursor()
    cur.execute("DELETE FROM billing_subscriptions WHERE tenant_id = %s", (tenant_id,))
    if status is None:
        return
    cur.execute("""INSERT INTO billing_subscriptions
                     (id, tenant_id, provider, provider_subscription_id, status, current_period_end,
                      cancel_at_period_end, past_due_since, ended_at)
                   VALUES (%s, %s, %s, %s, %s,
                           NOW() + %s * INTERVAL '1 hour', %s, NOW() - %s * INTERVAL '1 hour',
                           NOW() - %s * INTERVAL '1 hour')""",
                (f"sub_ct_{secrets.token_hex(4)}", tenant_id, kw.get("provider", "stripe"),
                 f"sub_{secrets.token_hex(6)}", status,
                 kw.get("period_end_h", 24 * 20), kw.get("cancel_at_end", False),
                 kw.get("past_due_h", 0) if kw.get("past_due_h") is not None else 0, kw.get("ended_h", 0)))
    if "past_due_h" not in kw:
        cur.execute("UPDATE billing_subscriptions SET past_due_since = NULL WHERE tenant_id = %s", (tenant_id,))
    if "ended_h" not in kw:
        cur.execute("UPDATE billing_subscriptions SET ended_at = NULL WHERE tenant_id = %s", (tenant_id,))
    if kw.get("period_end_h", 1) is None:
        cur.execute("UPDATE billing_subscriptions SET current_period_end = NULL WHERE tenant_id = %s", (tenant_id,))


def _erase_pair_case(ct, args, db, secret, name, pair, *, body, who="admin", expect_erased: Optional[bool],
                     prep=None, token_override=None, headers=None, raw_body=None, mask_keys=()):
    """Send the same DELETE /tenant from each side's own tenant and compare.

    `expect_erased` True: both tenants must be gone, every table's delta equal,
    storage dirs gone; False: both must be intact, every count unchanged."""
    py_fx, rs_fx = pair
    if args.only and args.only not in name:
        return None
    if prep:
        prep(py_fx)
        prep(rs_fx)
    snaps_before = {}
    for side, fx in (("py", py_fx), ("rs", rs_fx)):
        snaps_before[side] = (table_counts(db), tenant_counts(db, fx.tenant_id))
    tok_py = token_override if token_override is not None else ct.auth_for(py_fx, who)
    tok_rs = token_override if token_override is not None else ct.auth_for(rs_fx, who)

    case = ct.Case(name, "DELETE", f"{ct.API}/tenant", route="DELETE /tenant")
    kw = dict(body=body, raw_body=raw_body, headers=headers or {})
    # Python first, with its own tenant; the whole-database snapshot in between
    # isolates each side's effect.
    rp = ct.http(args.python, "DELETE", f"{ct.API}/tenant", token=tok_py, **kw)
    after_py = table_counts(db)
    rr = ct.http(args.rust, "DELETE", f"{ct.API}/tenant", token=tok_rs, **kw)
    after_rs = table_counts(db)

    problems = []
    if rp.status != rr.status:
        problems.append(f"status python={rp.status} rust={rr.status}")
    subs_py = {py_fx.tenant_id: "<TENANT>"}
    subs_rs = {rs_fx.tenant_id: "<TENANT>"}
    bp, br = _subst(rp.body, subs_py), _subst(rr.body, subs_rs)
    if isinstance(bp, dict) and isinstance(bp.get("data"), dict) and "removed_storage_dirs" in bp["data"]:
        for b in (bp, br):
            b["data"]["removed_storage_dirs"] = sorted(
                p.replace("\\", "/").replace(str(_storage_path(args)).replace("\\", "/"), "<STORAGE>")
                for p in b["data"]["removed_storage_dirs"])
    problems += ct.diff(ct.normalize(bp, set(mask_keys)), ct.normalize(br, set(mask_keys)))
    for h in ("www-authenticate", "retry-after"):
        if rp.headers.get(h) != rr.headers.get(h):
            problems.append(f"header {h}: python={rp.headers.get(h)!r} rust={rr.headers.get(h)!r}")

    # Row-for-row.
    delta_py = {t: after_py[t] - snaps_before["py"][0][t] for t in after_py}
    # Python's call happens first, so Rust's delta is measured against the state after it.
    delta_rs = {t: after_rs[t] - after_py[t] for t in after_rs}
    bad = {t: (delta_py.get(t, 0), delta_rs.get(t, 0)) for t in set(delta_py) | set(delta_rs)
           if delta_py.get(t, 0) != delta_rs.get(t, 0)}
    if bad:
        problems.append(f"per-table row deltas differ (python, rust): {dict(sorted(bad.items()))}")
    for side, fx, delta in (("python", py_fx, delta_py), ("rust", rs_fx, delta_rs)):
        gone = _q(db, "SELECT COUNT(*) FROM tenants WHERE id = %s", (fx.tenant_id,))[0][0] == 0
        residue = {t: n for t, n in tenant_counts(db, fx.tenant_id).items() if n}
        if expect_erased is True:
            if not gone:
                problems.append(f"{side}: tenant row survived")
            if residue:
                problems.append(f"{side}: rows left behind in {residue}")
            removed_rows = sum(-d for d in delta.values() if d < 0)
            if removed_rows == 0:
                problems.append(f"{side}: nothing was deleted")
            else:
                problems.append(f"(note: {side} erased {removed_rows} rows across "
                                f"{sum(1 for d in delta.values() if d < 0)} tables)")
            if any(d > 0 for d in delta.values()):
                problems.append(f"{side}: erasure ADDED rows: {({t: d for t, d in delta.items() if d > 0})}")
            for cat in STORAGE_CATEGORIES:
                p = _storage_path(args) / cat / fx.tenant_id
                if cat == "logs":
                    if not p.is_file():
                        problems.append(f"{side}: the plain file at {cat}/ should have survived rmtree")
                elif p.exists():
                    problems.append(f"{side}: storage dir {cat}/{fx.tenant_id} survived")
        elif expect_erased is False:
            if gone:
                problems.append(f"{side}: tenant was erased but must not have been")
            if any(delta.values()):
                problems.append(f"{side}: rows changed on a refused request: "
                                f"{({t: d for t, d in delta.items() if d})}")
            for cat in STORAGE_CATEGORIES:
                p = _storage_path(args) / cat / fx.tenant_id
                if not p.exists():
                    problems.append(f"{side}: storage {cat}/{fx.tenant_id} vanished on a refused request")
    hard = [p for p in problems if not p.startswith("(")]
    return case, ("FAIL" if hard else "PASS"), problems


def run_erase(args, db, secret) -> list:
    ct = _ct()
    out: list = []
    created: list = []

    def add(res):
        if res:
            out.append(res)

    def mk_pair(label):
        pair = _pair(ct, args, secret, label)
        created.extend(pair)
        return pair

    try:
        # A bystander proves other tenants' rows are untouched by every erase.
        bystander = ct.make_fixture(args.python, secret)
        created.append(bystander)
        seed_generic(args.db, bystander.tenant_id, bystander.admin_id)
        _seed_storage(_storage_path(args), bystander.tenant_id)
        by_before = tenant_counts(db, bystander.tenant_id)

        # ── Refusals: nothing may change ────────────────────────────────────
        pair = mk_pair("refusals")
        for name, kw in (
            ("viewer is refused", dict(who="viewer", body={"confirm": "DELETE"})),
            ("analyst is refused", dict(who="analyst", body={"confirm": "DELETE"})),
            ("no token", dict(who="none", body={"confirm": "DELETE"})),
            ("bad signature", dict(who="bad_signature", body={"confirm": "DELETE"})),
            ("expired token", dict(who="expired", body={"confirm": "DELETE"})),
            ("read key refused (internal tag)", dict(who="key_read", body={"confirm": "DELETE"})),
            ("write key refused (internal tag)", dict(who="key_write", body={"confirm": "DELETE"})),
            ("wrong confirmation", dict(body={"confirm": "please"})),
            ("empty confirmation", dict(body={"confirm": ""})),
            ("another tenant's slug is not the confirmation", dict(body={"confirm": "contract-other"})),
            ("missing confirm", dict(body={})),
            ("confirm is a number", dict(body={"confirm": 5})),
            ("confirm is null", dict(body={"confirm": None})),
            ("body is a list", dict(body=["DELETE"])),
            ("no body", dict(body=None)),
            ("invalid JSON", dict(body=None, raw_body=b"{nope", mask_keys=("ctx", "loc"))),
        ):
            add(_erase_pair_case(ct, args, db, secret, f"erase: {name}", pair, expect_erased=False, **kw))

        # Warehouse-scoped admin: company totals refused.
        def scope_on(fx):
            db.cursor().execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (fx.admin_id,))

        def scope_off(fx):
            db.cursor().execute("UPDATE users SET warehouse_scope = NULL WHERE id = %s", (fx.admin_id,))
        add(_erase_pair_case(ct, args, db, secret, "erase: warehouse-scoped admin refused", pair,
                             body={"confirm": "DELETE"}, expect_erased=False, prep=scope_on))
        scope_off(pair[0]); scope_off(pair[1])

        # Subscriptions that still renew block the erasure; each is a 409.
        for label, status, kw in (
            ("active stripe", "active", dict(provider="stripe")),
            ("active paypal", "active", dict(provider="paypal")),
            ("trialing", "trialing", dict()),
            ("past_due inside grace", "past_due", dict(past_due_h=24)),
            ("past_due start unknown", "past_due", dict()),
            ("canceled but period still running is NOT live: it passes the check", None, None),
        ):
            if status is None:
                break
            add(_erase_pair_case(ct, args, db, secret, f"erase: blocked by {label} subscription", pair,
                                 body={"confirm": "DELETE"}, expect_erased=False,
                                 prep=lambda fx, status=status, kw=kw: _set_sub(db, fx.tenant_id, status, **kw)))
        for fx in pair:
            _set_sub(db, fx.tenant_id, None)

        # ── Successful erasures, each on a fresh seeded pair ────────────────
        def erase_ok(name, body, prep=None, who="admin"):
            p = mk_pair(name)
            res = _erase_pair_case(ct, args, db, secret, f"erase: {name}", p, body=body, expect_erased=True,
                                   prep=prep, who=who)
            add(res)
            return p

        erase_ok("typed DELETE", {"confirm": "DELETE"})
        erase_ok("lowercase delete with spaces", {"confirm": "  delete \t"})
        # The slug confirmation needs each side's OWN slug: sent per side.
        p = mk_pair("slug")
        slug_py, slug_rs = _slug(db, p[0].tenant_id), _slug(db, p[1].tenant_id)
        res = _erase_pair_case_slug(ct, args, db, "erase: tenant slug as confirmation", p, slug_py, slug_rs)
        add(res)
        erase_ok("canceled subscription whose end passed",
                 {"confirm": "DELETE"},
                 prep=lambda fx: _set_sub(db, fx.tenant_id, "canceled", ended_h=48, period_end_h=-48))
        erase_ok("active but set to cancel at period end",
                 {"confirm": "DELETE"},
                 prep=lambda fx: _set_sub(db, fx.tenant_id, "active", cancel_at_end=True, period_end_h=24 * 10))
        erase_ok("past_due beyond the grace week",
                 {"confirm": "DELETE"},
                 prep=lambda fx: _set_sub(db, fx.tenant_id, "past_due", past_due_h=24 * 9))
        erase_ok("expired and unpaid subscriptions",
                 {"confirm": "DELETE"},
                 prep=lambda fx: (_set_sub(db, fx.tenant_id, "expired"),
                                  db.cursor().execute(
                                      "INSERT INTO billing_subscriptions (id, tenant_id, provider, "
                                      "provider_subscription_id, status) VALUES (%s, %s, 'paypal', %s, 'unpaid')",
                                      (f"sub_ct_{secrets.token_hex(4)}", fx.tenant_id, secrets.token_hex(6)))))

        # Erasing twice: the second call's token is for a tenant that no longer exists.
        p = mk_pair("double")
        add(_erase_pair_case(ct, args, db, secret, "erase: first call", p, body={"confirm": "DELETE"},
                             expect_erased=True))
        stale_py = ct.mint_access_token(secret, p[0].admin_id, p[0].tenant_id, "admin")
        stale_rs = ct.mint_access_token(secret, p[1].admin_id, p[1].tenant_id, "admin")
        r_py = ct.http(args.python, "DELETE", f"{ct.API}/tenant", token=stale_py, body={"confirm": "DELETE"})
        r_rs = ct.http(args.rust, "DELETE", f"{ct.API}/tenant", token=stale_rs, body={"confirm": "DELETE"})
        probs = []
        if r_py.status != r_rs.status:
            probs.append(f"status python={r_py.status} rust={r_rs.status}")
        probs += ct.diff(_subst(r_py.body, {p[0].tenant_id: "<T>", p[0].admin_id: "<U>"}),
                         _subst(r_rs.body, {p[1].tenant_id: "<T>", p[1].admin_id: "<U>"}))
        out.append((ct.Case("erase: second call on an erased tenant", "DELETE", "-", route="DELETE /tenant"),
                    "FAIL" if probs else "PASS", probs))

        # The bystander is intact after everything above.
        by_after = tenant_counts(db, bystander.tenant_id)
        probs = [] if by_before == by_after else [f"bystander rows changed: {({t: (by_before[t], by_after[t]) for t in by_before if by_before[t] != by_after[t]})}"]
        storage_ok = all((_storage_path(args) / c / bystander.tenant_id).exists() for c in STORAGE_CATEGORIES)
        if not storage_ok:
            probs.append("bystander storage vanished")
        out.append((ct.Case("erase: another tenant is untouched by every erasure", "-", "-", route="DELETE /tenant"),
                    "FAIL" if probs else "PASS", probs))
    finally:
        for fx in created:
            if _q(db, "SELECT 1 FROM tenants WHERE id = %s", (fx.tenant_id,)):
                ct.erase_fixture(args.python, fx)
    out.append(_tables_case(ct, db))
    return out


def _erase_pair_case_slug(ct, args, db, name, pair, slug_py, slug_rs):
    """The slug confirmation differs per side: run the pair with each slug."""
    py_fx, rs_fx = pair
    if args.only and args.only not in name:
        return None
    before = (table_counts(db), table_counts(db))
    tok_py, tok_rs = ct.auth_for(py_fx, "admin"), ct.auth_for(rs_fx, "admin")
    rp = ct.http(args.python, "DELETE", f"{ct.API}/tenant", token=tok_py, body={"confirm": slug_py})
    mid = table_counts(db)
    rr = ct.http(args.rust, "DELETE", f"{ct.API}/tenant", token=tok_rs, body={"confirm": slug_rs})
    end = table_counts(db)
    problems = []
    if rp.status != rr.status:
        problems.append(f"status python={rp.status} rust={rr.status}")
    d_py = {t: mid[t] - before[0][t] for t in mid}
    d_rs = {t: end[t] - mid[t] for t in end}
    bad = {t: (d_py[t], d_rs[t]) for t in d_py if d_py[t] != d_rs.get(t)}
    if bad:
        problems.append(f"per-table row deltas differ (python, rust): {bad}")
    for side, fx in (("python", py_fx), ("rust", rs_fx)):
        if _q(db, "SELECT 1 FROM tenants WHERE id = %s", (fx.tenant_id,)):
            problems.append(f"{side}: tenant survived")
    return ct.Case(name, "DELETE", "-", route="DELETE /tenant"), ("FAIL" if problems else "PASS"), problems


def _tables_case(ct, db):
    """The erasure list is the Python list (generated file current) and it
    covers every table that names a tenant."""
    problems = []
    spec = importlib.util.spec_from_file_location("gen_rust_tenant_tables", ROOT / "scripts" / "gen_rust_tenant_tables.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not mod.is_current():
        problems.append("backend-rs/src/routes/w2b/tenant_tables.rs is stale: run scripts/gen_rust_tenant_tables.py")
    lit = mod._literals()
    order = set(lit["_DELETE_ORDER"])
    cur = db.cursor()
    covered = set(_tenant_tables(cur))
    if covered - order:
        problems.append(f"tenant tables missing from the erasure list: {sorted(covered - order)}")
    cur.execute("""SELECT c.relname FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
                    JOIN pg_class p ON p.oid = k.confrelid WHERE k.contype = 'f' AND p.relname = 'tenants'""")
    fk = {r[0] for r in cur.fetchall()}
    if fk - order - {"tenants"}:
        problems.append(f"tables with an FK to tenants missing from the erasure list: {sorted(fk - order)}")
    rs_src = (ROOT / "backend-rs" / "src" / "routes" / "w2b" / "tenant_data.rs").read_text(encoding="utf-8")
    if "DELETE_ORDER" not in rs_src or "tenant_tables" not in rs_src:
        problems.append("tenant_data.rs does not use the generated lists")
    return (ct.Case("erase: Rust table lists are generated from the Python source and cover every tenant table",
                    "-", "-", route="DELETE /tenant"), "FAIL" if problems else "PASS", problems)


# ── 5. service-config capabilities ───────────────────────────────────────────

def _cap_expect(**overrides):
    base = {"assistant": False, "ai_narrative": False, "documents_search": False, "email": False,
            "whatsapp": False, "sms": False, "whatsapp_bot": False,
            "contact_channels": {"whatsapp": False, "email": False}, "online_payments": False,
            "background_worker": False, "scheduled_jobs": False}
    base.update(overrides)
    return base


def _cap_check(expected: dict):
    def check(rp, rr):
        data = rp.body.get("data") if isinstance(rp.body, dict) else None
        if rp.status != 200 or data != expected:
            return [f"the Python answer is not what the scenario says it must be: want {expected}, "
                    f"got {rp.status} {data}"]
        return []
    return check


def _fernet(env: dict):
    key = env.get("INTEGRATIONS_SECRET_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet  # noqa: PLC0415
        return Fernet(key.encode())
    except Exception:  # noqa: BLE001
        return None


def run_capabilities(args, db, secret) -> list:
    import time  # noqa: PLC0415
    ct = _ct()
    env = ct.read_env_file(args.env_file) if args.env_file else {}
    fer = _fernet(env)
    out: list = []
    route = "GET /service-config/capabilities"
    if fer is None:
        return [(ct.Case("capabilities (all)", "-", "-", route=route), "SKIP",
                 ["needs INTEGRATIONS_SECRET_KEY in --env-file (both services must share it) and `cryptography`"])]
    fa = ct.make_fixture(args.python, secret)
    fb = ct.make_fixture(args.python, secret)
    cur = db.cursor()
    path = f"{ct.API}/service-config/capabilities"
    secret_fields = {"deepseek_api_key", "resend_api_key", "stripe_secret_key", "stripe_webhook_secret",
                     "twilio_auth_token", "paypal_client_secret", "smtp_pass"}
    try:
        def set_rows(instance: dict, tenant_a: dict, tenant_b: dict):
            cur.execute("DELETE FROM service_config WHERE updated_by = 'contract-w2b'")
            for tid, rows in ((None, instance), (fa.tenant_id, tenant_a), (fb.tenant_id, tenant_b)):
                for field, value in rows.items():
                    enc = fer.encrypt(value.encode()).decode() if field in secret_fields else None
                    cur.execute("""INSERT INTO service_config (id, tenant_id, service, field, value_plain,
                                                               value_encrypted, updated_by)
                                   VALUES (%s, %s, 'ct', %s, %s, %s, 'contract-w2b')""",
                                (f"sc_ct_{secrets.token_hex(5)}", tid, field, None if enc else value, enc))
            # Python caches the override table for 10 seconds per process.
            time.sleep(11)

        def phase(label, a_expect, b_expect, with_auth_cases=False):
            for who_label, fx, who, expect in (("tenant A admin", fa, "admin", a_expect),
                                               ("tenant A viewer", fa, "viewer", a_expect),
                                               ("tenant B analyst", fb, "analyst", b_expect)):
                tok = ct.auth_for(fx, who)
                res = compare(ct, args, f"capabilities [{label}]: {who_label}", route, "GET", path,
                              token_py=tok, token_rs=tok, exact=True, check=_cap_check(expect))
                if res:
                    out.append(res)
            if with_auth_cases:
                for who in ("none", "bad_signature", "expired", "key_read", "key_write"):
                    tok = ct.auth_for(fa, who)
                    if who.startswith("key_") and tok is None:
                        continue
                    res = compare(ct, args, f"capabilities [{label}]: as {who}", route, "GET", path,
                                  token_py=tok, token_rs=tok, exact=True)
                    if res:
                        out.append(res)

        # A. nothing configured (both services share the environment).
        set_rows({}, {}, {})
        phase("nothing configured", _cap_expect(), _cap_expect(), with_auth_cases=True)

        # B. instance channels + a tenant that brought its own WhatsApp.
        inst = {"deepseek_api_key": "dk-1234567890", "resend_api_key": "re_abcdef",
                "contact_whatsapp": "+50688887777", "stripe_secret_key": "sk_test_x",
                "stripe_webhook_secret": "whsec_x", "stripe_price_id_full": "price_x"}
        ten_a = {"twilio_account_sid": "AC123", "twilio_auth_token": "tok", "twilio_whatsapp_from": "+14155550100",
                 "twilio_sms_from": "+14155550101"}
        set_rows(inst, ten_a, {"whatsapp_bot_generic_mode": "true"})
        phase("instance llm+email+stripe, tenant A twilio",
              _cap_expect(assistant=True, ai_narrative=True, email=True, whatsapp=True, sms=True, whatsapp_bot=True,
                          contact_channels={"whatsapp": True, "email": False}, online_payments=True),
              _cap_expect(assistant=True, ai_narrative=True, email=True,
                          contact_channels={"whatsapp": True, "email": False}, online_payments=True))

        # C1. no LLM: the bot lives on generic mode only where the tenant says so; half an SMTP
        #     login; a tenant row for an instance-only field is ignored; PayPal with a bad mode.
        inst = {"smtp_user": "u", "paypal_client_id": "a", "paypal_client_secret": "b", "paypal_webhook_id": "c",
                "paypal_plan_id_full": "d", "paypal_mode": "prod", "not_a_registry_field": "ignored"}
        ten_a = {"twilio_account_sid": "AC123", "twilio_auth_token": "tok", "twilio_whatsapp_from": "+14155550100",
                 "whatsapp_bot_generic_mode": "true"}
        ten_b = {"deepseek_api_key": "tenant-cannot-bring-an-llm", "whatsapp_bot_generic_mode": "perhaps"}
        set_rows(inst, ten_a, ten_b)
        phase("no llm, half smtp, bad paypal mode", _cap_expect(whatsapp=True, whatsapp_bot=True), _cap_expect())

        # C2. smtp complete, PayPal valid with a padded mixed-case mode, but a zero price.
        inst = {"smtp_user": "u", "smtp_pass": "p", "paypal_client_id": "a", "paypal_client_secret": "b",
                "paypal_webhook_id": "c", "paypal_plan_id_full": "d", "paypal_mode": " LiVe ",
                "billing_price_usd_full": "0", "voyageai_api_key": "v", "pinecone_api_key": "p",
                "pinecone_index": "i"}
        set_rows(inst, {}, {})
        phase("smtp pair, zero price, rag complete",
              _cap_expect(email=True, documents_search=True), _cap_expect(email=True, documents_search=True))

        # C3. a real price turns PayPal on; a junk stored float is ignored (the default price
        #     applies); RAG missing one field is off.
        inst["billing_price_usd_full"] = "12.5"
        set_rows(inst, {}, {})
        phase("paypal valid, price 12.5",
              _cap_expect(email=True, documents_search=True, online_payments=True),
              _cap_expect(email=True, documents_search=True, online_payments=True))
        inst["billing_price_usd_full"] = "abc"
        set_rows(inst, {}, {})
        phase("junk price is ignored",
              _cap_expect(email=True, documents_search=True, online_payments=True),
              _cap_expect(email=True, documents_search=True, online_payments=True))
        inst.pop("pinecone_index")
        set_rows(inst, {}, {})
        phase("rag missing its index",
              _cap_expect(email=True, online_payments=True), _cap_expect(email=True, online_payments=True))
    finally:
        cur.execute("DELETE FROM service_config WHERE updated_by = 'contract-w2b'")
        ct.erase_fixture(args.python, fa)
        ct.erase_fixture(args.python, fb)
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def run_w2b(args, secret: str, db) -> list:
    ct = _ct()
    if db is None:
        return [(ct.Case("w2b (all)", "-", "-", route="W2B"), "SKIP",
                 ["wave 2b needs --db: seeding, row counts and storage checks are read from the database"])]
    wanted = (getattr(args, "sections", None) or "freshness,export,erase,capabilities").split(",")
    out = []
    if "freshness" in wanted:
        out += run_freshness(args, db, secret)
    if "export" in wanted:
        out += run_export(args, db, secret)
    if "erase" in wanted:
        out += run_erase(args, db, secret)
    if "capabilities" in wanted:
        out += run_capabilities(args, db, secret)
    return out


def main() -> None:
    """Run only this section: `python tests/contract/w2b_cases.py --db ...`
    (same arguments as contract_test.py)."""
    import argparse
    import sys
    ct = _ct()
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--sections", default="freshness,export,erase,capabilities")
    args = ap.parse_args()
    secret = ct.read_env_file(args.env_file)["SECRET_KEY"]
    import psycopg2
    db = psycopg2.connect(args.db) if args.db else None
    if db is not None:
        db.autocommit = True
    results = run_w2b(args, secret, db)
    width = max(len(c.name) for c, _, _ in results)
    print()
    for case, verdict, problems in results:
        print(f"{verdict:4}  {case.name:<{width}}  {case.route}")
        for p in problems:
            print(f"        - {p}")
    by_route: dict = {}
    for case, verdict, _ in results:
        by_route.setdefault(case.route, []).append(verdict)
    print()
    print("per route:")
    for route, v in by_route.items():
        print(f"  {route:<36} {v.count('PASS')}/{len(v)} pass" + (f", {v.count('SKIP')} skip" if "SKIP" in v else ""))
    failed = sum(1 for _, v, _ in results if v == "FAIL")
    print()
    print(f"{len(results) - failed}/{len(results)} cases pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
