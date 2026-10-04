#!/usr/bin/env bash
# ============================================================================
#  StockAI - start the app locally (Git Bash / macOS / Linux)
#
#      ./start.sh            Postgres + backend + frontend
#      ./start.sh --seed     ...and fill the demo tenant first
#
#  Ctrl+C stops both servers.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

SEED=0
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 2 ;;
  esac
done

die() { echo; echo "[StockAI] $1" >&2; exit 1; }

# ── The interpreter ─────────────────────────────────────────────────────────
if [ -x "backend/.venv/Scripts/python.exe" ]; then
  PY="backend/.venv/Scripts/python.exe"       # Windows
elif [ -x "backend/.venv/bin/python" ]; then
  PY="backend/.venv/bin/python"               # Linux / macOS
else
  die "No virtualenv at backend/.venv — create it and install backend/requirements.txt first."
fi

[ -d Frontend/node_modules ] || die "Frontend/node_modules is missing — run 'cd Frontend && npm install' first."

# ── Refuse to start on top of a test run ────────────────────────────────────
# The job queue IS the `jobs` table. A backend started while the suite is
# running claims the jobs those tests create and flips their sessions to
# FAILED, which reports defects that are not there. `scripts/run_tests.py`
# refuses to start while a server is listening; this is the same guard from
# the other side.
if pgrep -f "run_tests.py" >/dev/null 2>&1 || pgrep -f "pytest" >/dev/null 2>&1; then
  die "The test suite is running. Starting a server now would steal its jobs and
       mark its sessions FAILED. Wait for it to finish."
fi

# ── Postgres ────────────────────────────────────────────────────────────────
echo "[StockAI] Starting Postgres (docker faro_db)..."
if ! docker start faro_db >/dev/null 2>&1; then
  die "Could not start the 'faro_db' container. Is Docker running?
       If the container does not exist yet, create it:
         docker run -d --name faro_db -p 5544:5432 \\
           -e POSTGRES_PASSWORD=postgres postgres:16"
fi

# ── Which port the backend listens on ───────────────────────────────────────
# NOT a constant, and hardcoding it has broken this script twice in opposite
# directions. `Frontend/.env.local` is gitignored, per-machine, read by Next,
# and it BEATS a shell `BACKEND_URL=...` — so it is the only thing that really
# decides where the browser's /api/* calls land. Hardcode 8010 and this machine
# breaks (another project's container holds that port). Hardcode 8011 and a
# fresh clone breaks: no .env.local, Next falls back to next.config.mjs's 8010
# and proxies to a port nothing listens on — a 500 with an empty body, which
# reads like a broken product rather than a wrong port.
PORT=8010
if [ -f Frontend/.env.local ]; then
  FROM_ENV=$(grep -E "^BACKEND_URL=" Frontend/.env.local | tail -1 | sed "s#.*:##; s#/.*##")
  case "$FROM_ENV" in (*[!0-9]*|"") ;; (*) PORT="$FROM_ENV" ;; esac
fi

# ── Backend ─────────────────────────────────────────────────────────────────
# 127.0.0.1, never `localhost`: the latter can resolve to ::1 while uvicorn
# listens on IPv4 only, and that also surfaces as an empty-bodied 500.
echo "[StockAI] Backend  -> http://127.0.0.1:$PORT"
"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" &
BACK=$!

cleanup() {
  echo; echo "[StockAI] Stopping..."
  kill "$BACK" ${FRONT:-} 2>/dev/null || true
}
trap cleanup INT TERM

# Wait for it to actually answer before claiming anything is ready. Printing
# "Ready" while uvicorn is still importing sends the user to a screen that
# 500s, and the port being bound is not the same as the app being up.
echo -n "[StockAI] Waiting for the backend"
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then READY=1; break; fi
  kill -0 "$BACK" 2>/dev/null || die "The backend exited while starting. Its error is above."
  echo -n "."; sleep 1
done
echo
[ "${READY:-0}" = "1" ] || die "The backend never answered /health on port $PORT."

# ── Optional: fill the demo tenant ──────────────────────────────────────────
# Three scripts, in order: the operational core, then the screens that core
# leaves thin, then the ones that shipped after those two were written. All
# three are idempotent, so re-running is safe.
if [ "$SEED" = "1" ]; then
  echo "[StockAI] Seeding the demo tenant..."
  "$PY" -m backend.scripts.seed_demo        || die "seed_demo failed (see above)."
  "$PY" -m backend.scripts.seed_demo_screens || echo "  seed_demo_screens failed — continuing."
  "$PY" -m backend.scripts.seed_demo_today   || echo "  seed_demo_today failed — continuing."
fi

# ── Frontend ────────────────────────────────────────────────────────────────
# BACKEND_URL is for the no-.env.local case only; when that file exists Next
# ignores this, which is correct — the file is meant to win.
echo "[StockAI] Frontend -> http://localhost:5000"
( cd Frontend && BACKEND_URL="http://127.0.0.1:$PORT" npm run dev ) &
FRONT=$!

echo
echo "[StockAI] Ready. Open http://localhost:5000   (demo@faro.app / demo1234)"
echo "       Ctrl+C stops both."
echo
echo "       Note: uvicorn does NOT reload on edits — restart after backend changes."
echo "       Do NOT run 'npm run build' while this is up; it corrupts .next."
wait
