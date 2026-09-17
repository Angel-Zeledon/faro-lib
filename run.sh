#!/usr/bin/env bash
# ============================================================================
#  Faro - start the app locally (Git Bash / macOS / Linux)
#  Brings up: Postgres (docker faro_db) + backend + frontend :5000
#  Usage:  ./run.sh    (Ctrl+C stops backend and frontend)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Path to the venv's python (Windows uses Scripts/, Linux/mac use bin/).
if [ -x "backend/.venv/Scripts/python.exe" ]; then
  PY="backend/.venv/Scripts/python.exe"
else
  PY="backend/.venv/bin/python"
fi

echo "[Faro] Starting Postgres (docker faro_db)..."
docker start faro_db >/dev/null 2>&1 || echo "  WARNING: could not start 'faro_db'. Check that Docker is running."

# Which port the backend listens on is NOT a constant, and hardcoding it has
# now broken this script twice in opposite directions.
#
# `Frontend/.env.local` is gitignored and per-machine, Next reads it, and it
# BEATS a shell `BACKEND_URL=...` — so it is the only thing that actually
# decides where the browser's /api/* calls land. Hardcode 8010 and this
# machine breaks (another project's container holds that port, uvicorn cannot
# bind, `set -e` kills the script). Hardcode 8011 and a FRESH CLONE breaks: it
# has no .env.local, so Next falls back to next.config.mjs's 8010 and proxies
# to a port nothing is listening on — 500 with an empty body on every call,
# which reads like a broken product rather than a wrong port.
#
# So: ask the file, and fall back to the same default next.config.mjs uses.
PORT=8010
if [ -f Frontend/.env.local ]; then
  FROM_ENV=$(grep -E "^BACKEND_URL=" Frontend/.env.local | tail -1 | sed "s#.*:##; s#/.*##")
  case "$FROM_ENV" in (*[!0-9]*|"") ;; (*) PORT="$FROM_ENV" ;; esac
fi

# 127.0.0.1, never `localhost`: the latter can resolve to ::1 while uvicorn
# listens on IPv4 only, and that also shows up as an empty-bodied 500.
echo "[Faro] Backend  -> http://127.0.0.1:$PORT"
"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" &
BACK=$!

echo "[Faro] Frontend -> http://localhost:5000"
# BACKEND_URL is passed for the no-.env.local case only; when that file
# exists Next ignores this, which is correct — the file is meant to win.
( cd Frontend && BACKEND_URL="http://127.0.0.1:$PORT" npm run dev ) &
FRONT=$!

echo ""
echo "[Faro] Ready. Open http://localhost:5000  (login: demo@faro.app / demo1234)"
echo "       Ctrl+C to stop both."

trap 'echo; echo "[Faro] Stopping..."; kill "$BACK" "$FRONT" 2>/dev/null || true' INT TERM
wait
