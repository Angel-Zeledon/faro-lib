"""The giant-database harness as a test (opt-in: STOCKAI_GIANT=1).

Runs `backend/scripts/giant_sync.py` in a subprocess against the engines it
can reach, at the scale given by STOCKAI_GIANT_ROWS / STOCKAI_GIANT_LEDGER
(defaults 20 000 / 200 000, minutes not hours), and asserts every measured
step succeeded and matched the independent oracle 100 %, every chaos case
ended in a visible outcome with stock untouched on failure, and the source
never saw a lock stronger than a read lock from the sync.

Skipped by default: it loads data into containers and takes minutes. The
full-scale numbers in the report come from the same script run by hand.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("STOCKAI_GIANT") != "1",
                                reason="giant-database harness is opt-in (STOCKAI_GIANT=1)")

ROOT = Path(__file__).resolve().parents[2]
ROWS = os.environ.get("STOCKAI_GIANT_ROWS", "20000")
LEDGER = os.environ.get("STOCKAI_GIANT_LEDGER", "200000")
READ_LOCKS = {"AccessShareLock", "S", "IS", "Sch-S", "TM2", "TM0"}


def _up(engine: str) -> bool:
    sys.path.insert(0, str(ROOT))
    from backend.scripts.giant_sync import target
    h, p, _, _ = target(engine)
    try:
        with socket.create_connection((h, p), timeout=2):
            return True
    except OSError:
        return False


def _harness(*args: str, out: Path) -> list[dict]:
    cmd = [sys.executable, "-m", "backend.scripts.giant_sync", *args, "--rows", ROWS, "--ledger", LEDGER,
           "--out", str(out)]
    subprocess.run(cmd, cwd=ROOT, check=True, timeout=3600)
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("engine,scenarios", [
    ("postgresql", "snapshot,lots,ledger,wide,history,union,views,push"),
    ("mysql", "snapshot,lots,ledger,history,union"),
    ("mssql", "snapshot,lots,ledger,history,union"),
    ("oracle", "snapshot,lots,ledger,history"),
    ("mongodb", "snapshot,ledger,poly"),
])
def test_every_mode_matches_the_oracle(engine, scenarios, tmp_path):
    if not _up(engine):
        pytest.skip(f"{engine} target not reachable")
    load = [s for s in scenarios.split(",") if s != "push"]
    subprocess.run([sys.executable, "-m", "backend.scripts.giant_sync", "load", "--engine", engine,
                    "--scenarios", ",".join(load), "--rows", ROWS, "--ledger", LEDGER], cwd=ROOT, check=True,
                   timeout=3600)
    recs = _harness("measure", "--engine", engine, "--scenarios", scenarios, out=tmp_path / "r.jsonl")
    assert recs
    for r in recs:
        assert r["status"] == "success", r
        if r.get("oracle") is not None:
            assert r["oracle"]["match"], r
        assert set(r.get("src_locks") or []) <= READ_LOCKS, r


@pytest.mark.parametrize("engine", ["postgresql", "mysql", "mssql"])
def test_chaos_ends_visible_and_leaves_stock_alone(engine, tmp_path):
    if not _up(engine):
        pytest.skip(f"{engine} target not reachable")
    cases = "zero_rows,mass_delete,schema_drift,kill_worker"
    if engine != "postgresql":           # the PostgreSQL source shares a container with StockAI's own database
        cases += ",kill_source,pause_source"
    recs = _harness("chaos", "--engine", engine, "--cases", cases, out=tmp_path / "c.jsonl")
    outcomes = [r for r in recs if str(r.get("mode", "")).endswith(":outcome")]
    assert outcomes
    for o in outcomes:
        assert o["status"] in ("failed", "braked", "success"), o
        if o["status"] != "success":
            assert o["error"], o
            assert o["stock_untouched"], o
    resumed = [r for r in recs if r.get("mode") == "resumed:oracle"]
    for r in resumed:
        assert r["oracle"]["match"], r
    final = [r for r in recs if r.get("mode") == "converges_after_chaos"]
    assert final and final[-1]["status"] == "success"
