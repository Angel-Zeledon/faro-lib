"""The restore drill's checker logic, on small fixtures.

The drill itself needs a Postgres server and a real backup, so what is tested
here is everything that decides pass or fail: reading row counts out of a dump,
comparing them to a restored database, the orphan query, path rebasing, archive
safety and the orchestration with a scripted `psql`. A test that cannot fail is
worthless, so each check has a case where it MUST report a failure.
"""

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill.py"
_spec = importlib.util.spec_from_file_location("restore_drill", SCRIPT)
drill = importlib.util.module_from_spec(_spec)
sys.modules["restore_drill"] = drill  # dataclasses resolves annotations through it
_spec.loader.exec_module(drill)

DUMP = """\
SET statement_timeout = 0;
COPY public.tenants (id, name) FROM stdin;
t1\tAcme
t2\tGlobex
\\.

COPY public.datasets (id, tenant_id, file_path) FROM stdin;
d1\tt1\t/app/storage/datasets/t1/d1/data.csv
\\.

COPY public.empty_table (id) FROM stdin;
\\.

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_tenant_fk FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
"""


# ── pure helpers ────────────────────────────────────────────────────────────

def test_latest_backup_picks_the_newest_date_in_the_name():
    names = ["faro-2026-10-01.sql.gz", "faro-2026-10-04.sql.gz", "faro-2026-09-30.sql.gz",
             "faro-storage-2026-10-09.tar.gz", "notes.txt"]
    assert drill.latest_backup(names, drill.DB_FILE_RE) == "faro-2026-10-04.sql.gz"
    assert drill.latest_backup(names, drill.STORAGE_FILE_RE) == "faro-storage-2026-10-09.tar.gz"
    assert drill.latest_backup(["x"], drill.DB_FILE_RE) is None


def test_dump_rows_and_foreign_keys_are_counted_from_the_file():
    counts, fk = drill.count_dump_rows(DUMP.splitlines(keepends=True))
    assert counts == {"public.tenants": 2, "public.datasets": 1, "public.empty_table": 0}
    assert fk == 1


def test_insert_style_dumps_are_counted_too():
    lines = ["INSERT INTO public.t (id) VALUES (1);\n", "INSERT INTO public.t (id) VALUES (2);\n"]
    assert drill.count_dump_rows(lines)[0] == {"public.t": 2}


def test_compare_counts_reports_every_kind_of_difference():
    expected = {"public.a": 2, "public.b": 5, "public.c": 1}
    restored = {"public.a": 2, "public.b": 4, "public.extra": 3}
    problems = drill.compare_counts(expected, restored)
    assert len(problems) == 3
    assert any("public.b" in p and "5" in p and "4" in p for p in problems)
    assert any("public.c" in p and "absent" in p for p in problems)
    assert any("public.extra" in p for p in problems)
    assert drill.compare_counts(expected, dict(expected)) == []
    # An empty extra table is not a difference.
    assert drill.compare_counts({}, {"public.x": 0}) == []


def test_orphan_query_looks_for_children_without_a_parent():
    sql = drill.orphan_sql("public.datasets", "tenant_id", "public.tenants", "id")
    assert "NOT EXISTS" in sql and "c.tenant_id IS NOT NULL" in sql and "p.id = c.tenant_id" in sql


def test_storage_paths_are_rebased_and_unmappable_ones_say_so(tmp_path):
    root = tmp_path / "storage"
    assert drill.rebase_storage_path("/app/storage/datasets/t/d/data.csv", "/app/storage", root) \
        == root / "datasets/t/d/data.csv"
    assert drill.rebase_storage_path("/old/place/storage/datasets/x.csv", "/app/storage", root) \
        == root / "datasets/x.csv"
    assert drill.rebase_storage_path("/somewhere/else/x.csv", "/app/storage", root) is None


def test_read_fully_detects_missing_and_hashes_content(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    ok, size, digest = drill.read_fully(f)
    assert (ok, size) == (True, 5) and digest == hashlib.sha256(b"hello").hexdigest()
    assert drill.read_fully(tmp_path / "missing.bin")[0] is False


def _tar_with(tmp_path, name, payload=b"x"):
    path = tmp_path / "s.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return path


def test_archive_members_that_escape_the_root_are_refused(tmp_path):
    with tarfile.open(_tar_with(tmp_path, "../evil.txt"), "r:gz") as tar:
        with pytest.raises(ValueError):
            drill.safe_members(tar)
    with tarfile.open(_tar_with(tmp_path, "/abs.txt"), "r:gz") as tar:
        with pytest.raises(ValueError):
            drill.safe_members(tar)


def test_summary_and_rto_estimate():
    ok = drill.CheckResult("a", drill.PASS, "")
    warn = drill.CheckResult("b", drill.WARN, "")
    bad = drill.CheckResult("c", drill.FAIL, "")
    assert drill.summarize([ok]) == drill.PASS
    assert drill.summarize([ok, warn]) == drill.WARN
    assert drill.summarize([ok, warn, bad]) == drill.FAIL
    rto = drill.rto_estimate({"restore_db": 10.0, "storage_extract": 5.0, "row_counts": 2.0})
    assert rto["measured_restore_seconds"] == 15.0 and rto["measured_total_seconds"] == 17.0
    assert "provisioning a replacement server" in rto["excludes"]


# ── orchestration with a scripted psql ──────────────────────────────────────

class FakePsql:
    """Answers the drill's queries from a dict instead of a server."""

    def __init__(self, restored_counts, orphans=0, fk_count=1, dataset_path=None):
        self.restored_counts = restored_counts
        self.orphans = orphans
        self.fk_count = fk_count
        self.dataset_path = dataset_path
        self.statements = []

    def run(self, sql, db="postgres"):
        self.statements.append((db, sql))
        if sql.startswith("CREATE DATABASE") or sql.startswith("DROP DATABASE"):
            return []
        if "query_to_xml" in sql:
            return [[t, str(n)] for t, n in self.restored_counts.items()]
        if "contype = 'f'" in sql and "count(*)" in sql and "array_length" not in sql:
            return [[str(self.fk_count)]]
        if "array_length(c.conkey" in sql:
            return [["public.datasets", "tenant_id", "public.tenants", "id"]]
        if sql.startswith("SELECT 'public.datasets.tenant_id'"):
            return [["public.datasets.tenant_id", str(self.orphans)]]
        if "column_name = 'tenant_id'" in sql:
            return []
        if "FROM datasets WHERE file_path" in sql:
            return [["d1", self.dataset_path]] if self.dataset_path else []
        if "FROM documents WHERE file_path" in sql:
            return []
        if "FROM sessions s JOIN" in sql:
            return []
        raise AssertionError(f"unscripted SQL: {sql[:80]}")

    def restore(self, db, dump_gz):
        self.statements.append((db, "RESTORE"))
        return []


def _backup_dir(tmp_path, with_key=True, marker_hash=None):
    backups = tmp_path / "backups"
    backups.mkdir()
    dump = backups / "faro-2026-10-04.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as fh:
        fh.write(DUMP)
    st = backups / "faro-storage-2026-10-04.tar.gz"
    with tarfile.open(st, "w:gz") as tar:
        def add(name, payload):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        add("datasets/t1/d1/data.csv", b"sku,date,qty\n" * 1000)
        if with_key:
            add("instance_secret.key", b"k" * 44)
        add("filler.bin", os.urandom(20_000))  # incompressible: past the empty-volume floor
    marker = {
        "status": "ok", "db_dump": dump.name, "storage_archive": st.name,
        "db_sha256": drill.sha256_file(dump) if marker_hash is None else marker_hash,
        "storage_sha256": drill.sha256_file(st),
    }
    (backups / "last_success.json").write_text(json.dumps(marker), encoding="utf-8")
    return backups


def _args(backups, tmp_path):
    return argparse.Namespace(
        backup_dir=str(backups), marker="", psql="psql", work_dir=str(tmp_path / "work"),
        original_storage="/app/storage", live_db="faro", live_storage="", sample=5,
        keep=False, json="")


GOOD_COUNTS = {"public.tenants": 2, "public.datasets": 1, "public.empty_table": 0}


def test_a_clean_restore_passes_every_check(tmp_path):
    backups = _backup_dir(tmp_path)
    fake = FakePsql(GOOD_COUNTS, dataset_path="/app/storage/datasets/t1/d1/data.csv")
    results, info = drill.run_drill(_args(backups, tmp_path), fake)
    by = {r.name: r for r in results}
    assert {n: r.status for n, r in by.items() if r.status != drill.PASS} == {
        "artifacts": drill.WARN}  # no completed sessions in the fixture: stated, not assumed
    assert by["row_counts"].status == drill.PASS
    assert info["rto"]["measured_restore_seconds"] >= 0
    # It created and dropped ONLY a drill_ database, and never touched "faro".
    ddl = [s for _, s in fake.statements if s.startswith(("CREATE", "DROP"))]
    assert len(ddl) == 2 and all('"drill_' in s for s in ddl)
    assert not (tmp_path / "work").exists()  # the scratch directory is removed


def test_a_row_count_mismatch_fails_the_drill(tmp_path):
    backups = _backup_dir(tmp_path)
    fake = FakePsql({**GOOD_COUNTS, "public.tenants": 1})
    results, _ = drill.run_drill(_args(backups, tmp_path), fake)
    by = {r.name: r for r in results}
    assert by["row_counts"].status == drill.FAIL
    assert "public.tenants" in by["row_counts"].detail
    assert drill.summarize(results) == drill.FAIL


def test_orphan_rows_and_missing_fks_fail(tmp_path):
    backups = _backup_dir(tmp_path)
    results, _ = drill.run_drill(_args(backups, tmp_path), FakePsql(GOOD_COUNTS, orphans=3, fk_count=0))
    detail = {r.name: r for r in results}["foreign_keys"]
    assert detail.status == drill.FAIL
    assert "orphan" in detail.detail and "declares 1" in detail.detail


def test_a_missing_dataset_file_fails(tmp_path):
    backups = _backup_dir(tmp_path)
    fake = FakePsql(GOOD_COUNTS, dataset_path="/app/storage/datasets/t1/d1/GONE.csv")
    results, _ = drill.run_drill(_args(backups, tmp_path), fake)
    assert {r.name: r for r in results}["datasets"].status == drill.FAIL


def test_a_tampered_archive_fails_the_hash_check(tmp_path):
    backups = _backup_dir(tmp_path, marker_hash="0" * 64)
    results, _ = drill.run_drill(_args(backups, tmp_path), FakePsql(GOOD_COUNTS))
    assert {r.name: r for r in results}["archives"].status == drill.FAIL


def test_a_missing_secret_key_is_a_warning_that_names_the_consequence(tmp_path):
    backups = _backup_dir(tmp_path, with_key=False)
    results, _ = drill.run_drill(_args(backups, tmp_path), FakePsql(GOOD_COUNTS))
    storage = {r.name: r for r in results}["storage"]
    assert storage.status == drill.WARN and "INTEGRATIONS_SECRET_KEY" in storage.detail


def test_no_backup_at_all_is_a_failure_not_a_pass(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    results, _ = drill.run_drill(_args(empty, tmp_path), FakePsql({}))
    assert results[0].status == drill.FAIL


def test_a_work_directory_that_is_not_empty_is_refused(tmp_path):
    backups = _backup_dir(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "precious.txt").write_text("do not delete me")
    with pytest.raises(SystemExit):
        drill.run_drill(_args(backups, tmp_path), FakePsql(GOOD_COUNTS))
    assert (work / "precious.txt").exists()
