#!/usr/bin/env python3
"""Restore the latest backup into a THROWAWAY database and directory, then
check that what came back is whole. Prints a pass/fail report with timings.

Why this exists: a backup nobody has restored is a hope (deploy/RESTORE.md).
This turns the manual runbook into one command you can run monthly, and it
measures the one number a continuity plan needs - how long a restore takes.

What it NEVER touches: the live database and the live storage. It creates a
database named `drill_<timestamp>`, a fresh temp directory, and drops the
database at the end (unless --keep). Refusals are built in: the database name
must start with `drill_`, and the work directory must be new.

It needs only `psql` (the PostgreSQL client) and the standard library. Every
SQL statement goes through psql, so on the production host you can point it at
the database container:

    python scripts/restore_drill.py --backup-dir /var/backups \\
        --psql "docker exec -i faro-db-1 psql -U faro" \\
        --marker /var/backups/last_success.json

Checks, in order (each is pass / warn / fail; exit code 1 if any fail):

  archives     the marker's sha256 of both archives matches the files on disk
               (skipped with a warning when there is no marker)
  restore_db   the dump loads; psql ERROR lines are surfaced as a warning
  row_counts   rows per table in the restored database equal the rows the
               dump FILE says it carries (counted from its COPY blocks - so the
               "manifest" is the dump itself and cannot drift from live writes)
  foreign_keys the same number of FK constraints exist as the dump declares,
               and no single-column FK has an orphan row
  tenant_orphans (warn only) rows whose tenant_id has no tenants row, in tables
               without a FK - the app deliberately has none on most tables
  storage      the storage archive extracts, and instance_secret.key is there
               (or a warning that INTEGRATIONS_SECRET_KEY must hold the key)
  datasets     a sample of dataset and document files exist and read to the end
  artifacts    a sample of completed sessions still have a readable artifact
               directory, and dataset files match the lineage manifest hash
               (a mismatch is a warning: a dataset may be edited in place)

What this does not prove: that the application boots against the restore (do
RESTORE.md step 5 by hand after a real incident), or anything about a copy of
the backups that lives off the server.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

PASS, WARN, FAIL = "pass", "warn", "fail"
DB_PREFIX = "drill_"
SECRET_KEY_FILE = "instance_secret.key"
_CHUNK = 1024 * 1024

DB_FILE_RE = re.compile(r"^faro-(\d{4}-\d{2}-\d{2})\.sql\.gz$")
STORAGE_FILE_RE = re.compile(r"^faro-storage-(\d{4}-\d{2}-\d{2})\.tar\.gz$")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────

def latest_backup(names: Iterable[str], pattern: re.Pattern) -> Optional[str]:
    """The newest file name matching `pattern`, by the date inside the name."""
    best: tuple[str, str] | None = None
    for name in names:
        m = pattern.match(name)
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), name)
    return best[1] if best else None


def count_dump_rows(lines: Iterable[str]) -> tuple[dict[str, int], int]:
    """Rows per table and FK declarations, read from a plain-SQL pg_dump.

    Understands the default `COPY ... FROM stdin;` blocks (rows until a line
    that is exactly `\\.`) and single-line `INSERT INTO` statements
    (`--inserts`). Returns `({"schema.table": rows}, foreign_key_count)`.
    """
    copy_re = re.compile(r"^COPY (\S+) \(.*\) FROM stdin;")
    copy_bare = re.compile(r"^COPY (\S+) FROM stdin;")
    insert_re = re.compile(r"^INSERT INTO (\S+)[ (]")
    counts: dict[str, int] = {}
    fk = 0
    current: Optional[str] = None
    for raw in lines:
        line = raw.rstrip("\n")
        if current is not None:
            if line == "\\.":
                current = None
            else:
                counts[current] += 1
            continue
        m = copy_re.match(line) or copy_bare.match(line)
        if m:
            current = _norm_table(m.group(1))
            counts.setdefault(current, 0)
            continue
        m = insert_re.match(line)
        if m:
            table = _norm_table(m.group(1))
            counts[table] = counts.get(table, 0) + 1
            continue
        if " FOREIGN KEY " in line and "ADD CONSTRAINT" in line:
            fk += 1
    return counts, fk


def _norm_table(name: str) -> str:
    """`public."Users"` and `public.users` compare as plain `public.users`."""
    schema, _, table = name.partition(".")
    if not table:
        schema, table = "public", schema
    return f"{schema.strip(chr(34))}.{table.strip(chr(34))}"


def compare_counts(expected: dict[str, int], restored: dict[str, int]) -> list[str]:
    """Human-readable differences; an empty list means the counts match."""
    problems = []
    for table, want in sorted(expected.items()):
        got = restored.get(table)
        if got is None:
            problems.append(f"{table}: in the dump ({want} rows) but absent after restore")
        elif got != want:
            problems.append(f"{table}: dump has {want} rows, restored has {got}")
    for table, got in sorted(restored.items()):
        if table not in expected and got > 0:
            problems.append(f"{table}: {got} rows after restore but not in the dump")
    return problems


def orphan_sql(child: str, child_col: str, parent: str, parent_col: str) -> str:
    """Count child rows whose FK value has no parent. Identifiers are already
    quoted by Postgres (`regclass` text, `quote_ident`)."""
    return (
        f"SELECT count(*) FROM {child} c WHERE c.{child_col} IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{parent_col} = c.{child_col})"
    )


def rebase_storage_path(file_path: str, original_prefix: str, new_root: Path) -> Optional[Path]:
    """Where a path recorded by the live app lives inside the restored copy.

    The DB stores absolute paths as the app saw them (`/app/storage/...` in the
    container). Strip `original_prefix`; failing that, fall back to the part of
    the path after the last `/storage/`. None when neither applies - reported,
    never guessed.
    """
    norm = file_path.replace("\\", "/")
    prefix = original_prefix.rstrip("/") + "/"
    if norm.startswith(prefix):
        return new_root / norm[len(prefix):]
    marker = "/storage/"
    if marker in norm:
        return new_root / norm.rsplit(marker, 1)[1]
    return None


def read_fully(path: Path) -> tuple[bool, int, str]:
    """(readable, bytes, sha256). Reads to the end so a truncated or unreadable
    file fails here and not on the first customer click."""
    digest = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
    except OSError:
        return False, 0, ""
    return True, total, digest.hexdigest()


def sha256_file(path: Path) -> str:
    return read_fully(path)[2]


def rto_estimate(timings: dict[str, float]) -> dict:
    """What was measured, and what the number leaves out. The estimate is the
    sum of the steps a restore on THIS machine needs; it does not include
    provisioning a replacement server, copying backups to it, or DNS."""
    steps = {k: round(v, 1) for k, v in timings.items()}
    return {
        "measured_restore_seconds": round(sum(timings.get(k, 0.0) for k in (
            "restore_db", "storage_extract")), 1),
        "measured_total_seconds": round(sum(timings.values()), 1),
        "steps_seconds": steps,
        "excludes": ["provisioning a replacement server",
                     "transferring the backups to it",
                     "DNS / certificate changes",
                     "the human decision time before the restore starts"],
    }


def summarize(results: list[CheckResult]) -> str:
    if any(r.status == FAIL for r in results):
        return FAIL
    if any(r.status == WARN for r in results):
        return WARN
    return PASS


def safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Members that stay inside the extraction root. A backup is data from
    disk, not a trusted source: refuse absolute paths, `..`, links out."""
    safe = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if m.name.startswith(("/", "\\")) or ".." in parts:
            raise ValueError(f"unsafe path in archive: {m.name}")
        if m.issym() or m.islnk():
            continue  # links are skipped, never followed
        safe.append(m)
    return safe


# ── psql runner ─────────────────────────────────────────────────────────────

class Psql:
    """Every database operation goes through one psql command line."""

    def __init__(self, argv: list[str]):
        self.argv = argv

    def run(self, sql: str, db: str = "postgres") -> list[list[str]]:
        proc = subprocess.run(
            [*self.argv, "-d", db, "-X", "-q", "-A", "-t", "-F", "\t",
             "-v", "ON_ERROR_STOP=1", "-c", sql],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed: {proc.stderr.strip()[:300]}")
        return [line.split("\t") for line in proc.stdout.splitlines() if line.strip()]

    def restore(self, db: str, dump_gz: Path) -> list[str]:
        """Feed the dump to psql; returns its ERROR lines (empty = clean)."""
        proc = subprocess.Popen(
            [*self.argv, "-d", db, "-X", "-q"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        try:
            with gzip.open(dump_gz, "rb") as src:
                shutil.copyfileobj(src, proc.stdin, _CHUNK)
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()
        return [ln for ln in err.splitlines() if "ERROR" in ln]


COUNT_ALL_SQL = (
    "SELECT table_schema || '.' || table_name, "
    "(xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', "
    "table_schema, table_name), false, true, '')))[1]::text "
    "FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
)
FK_LIST_SQL = (
    "SELECT c.conrelid::regclass::text, quote_ident(a.attname), "
    "c.confrelid::regclass::text, quote_ident(af.attname) "
    "FROM pg_constraint c "
    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
    "JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1] "
    "WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1"
)
FK_COUNT_SQL = "SELECT count(*) FROM pg_constraint WHERE contype = 'f'"
TENANT_TABLES_SQL = (
    "SELECT table_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
    "AND table_name <> 'tenants' "
    "AND table_name NOT IN (SELECT conrelid::regclass::text FROM pg_constraint "
    "                       WHERE contype = 'f' AND confrelid = 'public.tenants'::regclass)"
)


# ── The drill ───────────────────────────────────────────────────────────────

def _timed(results: list[CheckResult], timings: dict[str, float], key: str,
           fn: Callable[[], CheckResult]) -> CheckResult:
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - a crashed check is a FAILED check
        result = CheckResult(key, FAIL, f"{type(exc).__name__}: {exc}")
    result.seconds = round(time.perf_counter() - start, 2)
    timings[key] = result.seconds
    results.append(result)
    return result


def run_drill(args: argparse.Namespace, psql: Psql) -> tuple[list[CheckResult], dict]:
    backup_dir = Path(args.backup_dir)
    names = [p.name for p in backup_dir.iterdir()] if backup_dir.is_dir() else []
    db_name = latest_backup(names, DB_FILE_RE)
    st_name = latest_backup(names, STORAGE_FILE_RE)
    results: list[CheckResult] = []
    timings: dict[str, float] = {}
    info: dict = {"backup_dir": str(backup_dir)}

    if not db_name:
        results.append(CheckResult("archives", FAIL, f"no faro-YYYY-MM-DD.sql.gz in {backup_dir}"))
        return results, info
    dump = backup_dir / db_name
    storage_tar = backup_dir / st_name if st_name else None
    info.update(db_dump=db_name, storage_archive=st_name)
    age_h = (time.time() - dump.stat().st_mtime) / 3600.0
    info["backup_age_hours"] = round(age_h, 1)  # the RPO this restore delivers

    def archives() -> CheckResult:
        problems, notes = [], []
        if storage_tar is None:
            problems.append("no faro-storage-YYYY-MM-DD.tar.gz found")
        elif storage_tar.stat().st_size < 10_000:
            problems.append(f"{st_name} is {storage_tar.stat().st_size} bytes: an empty volume?")
        marker_path = Path(args.marker) if args.marker else backup_dir / "last_success.json"
        if marker_path.is_file():
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            for key, path in (("db_sha256", dump), ("storage_sha256", storage_tar)):
                want = marker.get(key)
                if want and path is not None and path.name in (
                        marker.get("db_dump"), marker.get("storage_archive")):
                    if sha256_file(path) != want:
                        problems.append(f"{path.name}: sha256 differs from the marker")
                    else:
                        notes.append(f"{path.name} sha256 matches")
                elif path is not None:
                    notes.append(f"{path.name} is not the file the marker describes")
        else:
            notes.append("no success marker found: archive hashes not verified")
        if problems:
            return CheckResult("archives", FAIL, "; ".join(problems))
        status = WARN if any("not verified" in n or "not the file" in n for n in notes) else PASS
        return CheckResult("archives", status, "; ".join(notes) or "archives present")

    _timed(results, timings, "archives", archives)

    drill_db = f"{DB_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    if not drill_db.startswith(DB_PREFIX) or drill_db == args.live_db:
        raise SystemExit("refusing: the drill database name must start with 'drill_'")
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="stockai_drill_"))
    if args.live_storage and work.resolve() == Path(args.live_storage).resolve():
        raise SystemExit("refusing: the work directory is the live storage")
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise SystemExit(f"refusing: work directory {work} is not empty")
    storage_root = work / "storage"
    storage_root.mkdir()
    info.update(drill_db=drill_db, work_dir=str(work))

    created = False
    try:
        psql.run(f'CREATE DATABASE "{drill_db}"')
        created = True

        def restore_db() -> CheckResult:
            errors = psql.restore(drill_db, dump)
            if errors:
                return CheckResult("restore_db", WARN,
                                   f"{len(errors)} ERROR line(s); first: {errors[0][:200]}")
            return CheckResult("restore_db", PASS, f"{db_name} loaded without errors")

        _timed(results, timings, "restore_db", restore_db)

        def row_counts() -> CheckResult:
            with gzip.open(dump, "rt", encoding="utf-8", errors="replace") as fh:
                expected, fk_declared = count_dump_rows(fh)
            info["_fk_declared"] = fk_declared
            restored = {t: int(n) for t, n in psql.run(COUNT_ALL_SQL, drill_db)}
            problems = compare_counts(expected, restored)
            total = sum(restored.values())
            info["tables"], info["total_rows"] = len(restored), total
            if problems:
                return CheckResult("row_counts", FAIL, "; ".join(problems[:8]),
                                   extra={"problems": len(problems)})
            return CheckResult("row_counts", PASS,
                               f"{len(restored)} tables, {total} rows, all equal to the dump")

        _timed(results, timings, "row_counts", row_counts)

        def foreign_keys() -> CheckResult:
            have = int(psql.run(FK_COUNT_SQL, drill_db)[0][0])
            want = info.get("_fk_declared", 0)
            problems = []
            if have < want:
                problems.append(f"dump declares {want} FK constraints, restore has {have}")
            orphans = []
            fks = psql.run(FK_LIST_SQL, drill_db)
            if fks:
                # One round trip for all of them: a psql call per FK costs
                # seconds each through `docker exec`.
                union = " UNION ALL ".join(
                    f"SELECT '{child}.{ccol}', ({orphan_sql(child, ccol, parent, pcol)})"
                    for child, ccol, parent, pcol in fks)
                for label, n in psql.run(union, drill_db):
                    if int(n):
                        orphans.append(f"{label}: {n} orphan row(s)")
            if orphans:
                problems.append("; ".join(orphans[:8]))
            if problems:
                return CheckResult("foreign_keys", FAIL, "; ".join(problems))
            return CheckResult("foreign_keys", PASS,
                               f"{have} FK constraints present, no orphan rows (single-column FKs)")

        _timed(results, timings, "foreign_keys", foreign_keys)

        def tenant_orphans() -> CheckResult:
            found = []
            tables = [row[0] for row in psql.run(TENANT_TABLES_SQL, drill_db)]
            if tables:
                union = " UNION ALL ".join(
                    f"SELECT '{t}', (SELECT count(*) FROM \"{t}\" t WHERE t.tenant_id IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM tenants x WHERE x.id = t.tenant_id))"
                    for t in tables)
                found = [f"{t}: {n}" for t, n in psql.run(union, drill_db) if int(n)]
            if found:
                return CheckResult("tenant_orphans", WARN,
                                   "rows whose tenant no longer exists (no FK by design): "
                                   + ", ".join(found[:10]))
            return CheckResult("tenant_orphans", PASS, "no logical tenant orphans")

        _timed(results, timings, "tenant_orphans", tenant_orphans)

        def storage() -> CheckResult:
            if storage_tar is None:
                return CheckResult("storage", FAIL, "no storage archive to extract")
            with tarfile.open(storage_tar, "r:gz") as tar:
                members = safe_members(tar)
                extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tar.extractall(storage_root, members=members, **extra)  # noqa: S202 - vetted above
            files = sum(1 for p in storage_root.rglob("*") if p.is_file())
            key = storage_root / SECRET_KEY_FILE
            if not key.is_file():
                return CheckResult("storage", WARN,
                                   f"{files} files extracted but {SECRET_KEY_FILE} is absent: "
                                   "INTEGRATIONS_SECRET_KEY must hold the Fernet key, or every "
                                   "stored credential must be entered again")
            return CheckResult("storage", PASS, f"{files} files extracted; {SECRET_KEY_FILE} present")

        _timed(results, timings, "storage_extract", storage)
        results[-1].name = "storage"

        sample = int(args.sample)

        def datasets() -> CheckResult:
            missing, unreadable, checked, unmapped = [], [], 0, 0
            for table in ("datasets", "documents"):
                rows = psql.run(
                    f"SELECT id, file_path FROM {table} WHERE file_path IS NOT NULL "
                    f"AND file_path <> '' ORDER BY random() LIMIT {sample}", drill_db)
                for row_id, file_path in rows:
                    target = rebase_storage_path(file_path, args.original_storage, storage_root)
                    if target is None:
                        unmapped += 1
                        continue
                    checked += 1
                    if not target.is_file():
                        missing.append(f"{table}:{row_id}")
                        continue
                    ok, _, _ = read_fully(target)
                    if not ok:
                        unreadable.append(f"{table}:{row_id}")
            if missing or unreadable:
                return CheckResult("datasets", FAIL,
                                   f"{checked} sampled; missing: {missing[:5]}; unreadable: {unreadable[:5]}")
            if unmapped:
                return CheckResult("datasets", WARN,
                                   f"{checked} readable, {unmapped} path(s) could not be mapped: "
                                   "pass --original-storage with the path the app used")
            if checked == 0:
                return CheckResult("datasets", WARN, "no dataset or document rows to sample")
            return CheckResult("datasets", PASS, f"{checked} sampled files exist and read to the end")

        _timed(results, timings, "datasets", datasets)

        def artifacts() -> CheckResult:
            rows = psql.run(
                "SELECT s.tenant_id, s.id, m.manifest->'dataset'->>'id', "
                "m.manifest->'dataset'->>'content_hash' "
                "FROM sessions s JOIN LATERAL (SELECT manifest FROM session_manifests m "
                "WHERE m.session_id = s.id ORDER BY created_at DESC LIMIT 1) m ON true "
                f"WHERE s.status = 'COMPLETED' ORDER BY random() LIMIT {sample}", drill_db)
            if not rows:
                return CheckResult("artifacts", WARN, "no completed session with a manifest to sample")
            empty, mismatch, checked = [], [], 0
            for tenant, session, dataset_id, content_hash in rows:
                art = storage_root / "artifacts" / tenant / session
                files = [p for p in art.rglob("*") if p.is_file()] if art.is_dir() else []
                if not files:
                    empty.append(session)
                    continue
                checked += 1
                for p in files[:20]:
                    if not read_fully(p)[0]:
                        empty.append(f"{session}:{p.name}")
                if dataset_id and content_hash:
                    path_rows = psql.run(
                        f"SELECT file_path FROM datasets WHERE id = '{dataset_id}'", drill_db)
                    target = (rebase_storage_path(path_rows[0][0], args.original_storage, storage_root)
                              if path_rows else None)
                    if target and target.is_file() and sha256_file(target) != content_hash:
                        mismatch.append(session)
            if empty:
                return CheckResult("artifacts", FAIL,
                                   f"sessions with missing/unreadable artifacts: {empty[:5]}")
            if mismatch:
                return CheckResult("artifacts", WARN,
                                   f"{checked} sessions have artifacts; dataset hash differs from the "
                                   f"lineage manifest for {mismatch[:5]} (edited in place, or damaged)")
            return CheckResult("artifacts", PASS,
                               f"{checked} sampled sessions: artifacts readable, dataset hashes match")

        _timed(results, timings, "artifacts", artifacts)
    finally:
        if created and not args.keep:
            try:
                psql.run(f'DROP DATABASE IF EXISTS "{drill_db}"')
            except Exception as exc:  # noqa: BLE001
                print(f"WARNING: could not drop {drill_db}: {exc}", file=sys.stderr)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    info.pop("_fk_declared", None)
    info["rto"] = rto_estimate({k: v for k, v in timings.items() if k != "archives"})
    return results, info


def render(results: list[CheckResult], info: dict) -> str:
    lines = ["", "StockAI restore drill", "=" * 60]
    for key in ("backup_dir", "db_dump", "storage_archive"):
        if key in info:
            lines.append(f"{key:<16} {info[key]}")
    if "backup_age_hours" in info:
        lines.append(f"{'backup age':<16} {info['backup_age_hours']} h  (the RPO this restore delivers)")
    lines.append("-" * 60)
    for r in results:
        lines.append(f"[{r.status.upper():<4}] {r.name:<15} {r.seconds:>7.1f}s  {r.detail}")
    lines.append("-" * 60)
    overall = summarize(results)
    lines.append(f"RESULT: {overall.upper()}")
    rto = info.get("rto")
    if rto:
        lines.append(f"Measured restore time (database + storage): {rto['measured_restore_seconds']} s; "
                     f"whole drill: {rto['measured_total_seconds']} s")
        lines.append("RTO estimate EXCLUDES: " + "; ".join(rto["excludes"]))
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--backup-dir", default="/var/backups")
    ap.add_argument("--marker", default="", help="success marker JSON (default <backup-dir>/last_success.json)")
    ap.add_argument("--psql", default=os.environ.get("DRILL_PSQL", "psql"),
                    help='psql command line, e.g. "docker exec -i faro-db-1 psql -U faro"')
    ap.add_argument("--work-dir", default="", help="must not exist or be empty (default: a temp dir)")
    ap.add_argument("--original-storage", default="/app/storage",
                    help="STORAGE_PATH as the live app saw it (the prefix of paths in the database)")
    ap.add_argument("--live-db", default="faro", help="name of the live database, never touched")
    ap.add_argument("--live-storage", default="", help="live storage path; refused as a work dir")
    ap.add_argument("--sample", type=int, default=25, help="rows sampled per file check")
    ap.add_argument("--keep", action="store_true", help="keep the drill database and directory")
    ap.add_argument("--json", default="", help="also write the report as JSON to this path")
    args = ap.parse_args(argv)

    results, info = run_drill(args, Psql(shlex.split(args.psql)))
    print(render(results, info))
    if args.json:
        Path(args.json).write_text(json.dumps({
            "result": summarize(results), "info": info,
            "checks": [r.__dict__ for r in results],
        }, indent=2), encoding="utf-8")
    return 1 if summarize(results) == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
