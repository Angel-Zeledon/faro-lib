"""One command that actually means "everything".

Until this existed, "the full suite" meant `cd backend && pytest`, and the
forecasting engine had a second suite of its own that you had to remember. On
2026-08-11 that is exactly how a change to `ForecastingCore/validation/leakage.py`
shipped with only a `-k` subset run against it: nothing was skipped on purpose,
the command just did not cover it.

It also carries the three traps that live in `.claude/skills/running-faro` and
nowhere the runner could enforce them:

1. A dev server on the same database CLAIMS jobs the tests create, runs them,
   and flips their sessions to FAILED. Measured, not theorised — it is why one
   test once read 22 log lines after writing 20. So this refuses to start while
   something is listening.
2. The two suites must not run CONCURRENTLY: the timing-sensitive tests then
   fail for load rather than for defects. So they run one after the other, never
   in parallel, however tempting the wall-clock saving is.
3. The frontend has no tests at all. `tsc --noEmit` is the only automated check
   it has, and calling it a test would be a lie — it is reported as what it is,
   so "everything passed" never gets read as "the product works".

Usage:
    python scripts/run_tests.py              # everything
    python scripts/run_tests.py --backend    # just one part
    python scripts/run_tests.py --core --frontend
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():                       # non-Windows checkout
    PYTHON = ROOT / "backend" / ".venv" / "bin" / "python"

# Ports a dev server would hold. Both matter: the backend steals jobs, and the
# frontend keeps a backend alive behind it.
DEV_PORTS = {8010: "backend (uvicorn)", 5000: "frontend (next dev)", 8001: "backend (alt port)"}


def _listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _refuse_if_dev_servers_are_up() -> None:
    busy = [f"  :{p}  {name}" for p, name in DEV_PORTS.items() if _listening(p)]
    if not busy:
        return
    print("\n".join([
        "",
        "REFUSING TO RUN — a dev server is listening:",
        *busy,
        "",
        "The job queue IS the `jobs` table, and dequeue takes any QUEUED job no",
        "matter which process created it. A dev server will claim jobs these",
        "tests create, run them, and flip their sessions to FAILED — so the run",
        "would report defects that are not there.",
        "",
        "Stop it and run again.",
    ]))
    sys.exit(2)


def _resolve(program: str) -> str | None:
    """Absolute path to `program`, or None.

    On Windows `npx` is `npx.cmd`, and `subprocess.run` without a shell asks
    CreateProcess for the literal name — which fails with WinError 2. The first
    run that ever reached the frontend stage died there with a traceback, after
    the backend and the engine had already passed: forty minutes of real work
    thrown away by a lookup. `shutil.which` finds the `.cmd`.
    """
    return shutil.which(program)


def _run(label: str, cwd: Path, args: list[str]) -> tuple[str, bool, float]:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    started = time.time()

    # A stage that cannot even START is a FAIL with a reason, never a crash.
    # Raising here loses every result already collected and prints a traceback
    # where a summary belongs.
    resolved = _resolve(args[0]) if not Path(args[0]).exists() else args[0]
    if resolved is None:
        print(f"\n  !! `{args[0]}` not found on PATH — this stage did not run.")
        return f"{label}  [NOT RUN]", False, 0.0

    result = subprocess.run([resolved, *args[1:]], cwd=cwd)
    elapsed = time.time() - started

    # Checked again at the END, not only before starting. A dev server that came
    # up DURING the run poisons it exactly as much as one that was already
    # there, and a start-up check cannot see the future — which is how a run was
    # left competing with a browser session somebody opened mid-way to look at a
    # page. A green result from a poisoned run is worse than no run at all, so
    # this reports FAIL and says why rather than letting it pass quietly.
    intruders = [name for port, name in DEV_PORTS.items() if _listening(port)]
    if intruders:
        print(f"\n  !! A dev server came up during this stage ({', '.join(intruders)}).")
        print("     It claims jobs these tests create, so this result is NOT trustworthy.")
        return f"{label}  [POISONED]", False, elapsed

    return label, result.returncode == 0, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the project's automated checks.")
    ap.add_argument("--backend", action="store_true", help="backend/tests only")
    ap.add_argument("--core", action="store_true", help="ForecastingCore/tests only")
    ap.add_argument("--frontend", action="store_true", help="frontend typecheck only")
    ap.add_argument("--allow-dev-servers", action="store_true",
                    help="skip the safety check (results will not be trustworthy)")
    args = ap.parse_args()

    everything = not (args.backend or args.core or args.frontend)
    if not args.allow_dev_servers:
        _refuse_if_dev_servers_are_up()

    results: list[tuple[str, bool, float]] = []

    # Backend first: it is the longest, so a failure surfaces while you are still
    # watching rather than forty minutes in.
    if everything or args.backend:
        results.append(_run(
            "BACKEND — 159 files, the API, permissions, calculations, guards",
            ROOT / "backend",
            [str(PYTHON), "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider", "-rf"],
        ))

    # Never at the same time as the backend — see the module docstring.
    if everything or args.core:
        results.append(_run(
            "FORECASTING CORE — 56 files, the engine: models, validation, metrics",
            ROOT / "ForecastingCore",
            [str(PYTHON), "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider", "-rf"],
        ))

    if everything or args.frontend:
        results.append(_run(
            "FRONTEND — typecheck only. There are NO frontend tests.",
            ROOT / "Frontend",
            ["npx", "tsc", "--noEmit"],
        ))

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for label, ok, secs in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {secs / 60:5.1f} min  {label.split(' — ')[0]}")

    failed = [label for label, ok, _ in results if not ok]
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(l.split(' — ')[0] for l in failed)}")
        return 1

    print("\nAll green — for what is covered.")
    print("Not covered by anything above: the browser. No end-to-end test exists;")
    print("every screen walk in docs/inventario-pantallas.md was done by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
