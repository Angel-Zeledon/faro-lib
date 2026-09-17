#!/usr/bin/env bash
# ============================================================================
#  Faro - start the app locally (Git Bash / macOS / Linux)
#  Brings up: Postgres (docker faro_db) + backend :8010 + frontend :5000
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

echo "[Faro] Backend  -> http://localhost:8010"
"$PY" -m uvicorn backend.main:app --port 8010 &
BACK=$!

echo "[Faro] Frontend -> http://localhost:5000"
( cd Frontend && BACKEND_URL=http://localhost:8010 npm run dev ) &
FRONT=$!

echo ""
echo "[Faro] Ready. Open http://localhost:5000  (login: demo@faro.app / demo1234)"
echo "       Ctrl+C to stop both."

trap 'echo; echo "[Faro] Stopping..."; kill "$BACK" "$FRONT" 2>/dev/null || true' INT TERM
wait
