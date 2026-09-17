#!/usr/bin/env bash
# ============================================================================
#  Faro - start the app locally (Git Bash / macOS / Linux)
#  Brings up: Postgres (docker faro_db) + backend :8011 + frontend :5000
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

# 8011, not 8010: another project's container publishes :8010 on this
# machine, so uvicorn could not bind and the script died on `set -e`.
# Bound to 127.0.0.1 on purpose — `localhost` can resolve to ::1 while
# uvicorn listens on IPv4 only, and the symptom is a 500 with an empty
# body on every /api/* call rather than a connection error.
echo "[Faro] Backend  -> http://127.0.0.1:8011"
"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8011 &
BACK=$!

echo "[Faro] Frontend -> http://localhost:5000"
# No BACKEND_URL here: Frontend/.env.local is read by Next and BEATS a
# shell variable, so exporting one looked like it worked and did
# nothing. That file is the single place the port is named.
( cd Frontend && npm run dev ) &
FRONT=$!

echo ""
echo "[Faro] Ready. Open http://localhost:5000  (login: demo@faro.app / demo1234)"
echo "       Ctrl+C to stop both."

trap 'echo; echo "[Faro] Stopping..."; kill "$BACK" "$FRONT" 2>/dev/null || true' INT TERM
wait
