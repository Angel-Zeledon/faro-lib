#!/usr/bin/env bash
# ============================================================================
#  Faro - arrancar la app en local (Git Bash / macOS / Linux)
#  Levanta: Postgres (docker faro_db) + backend :8010 + frontend :5000
#  Uso:  ./run.sh    (Ctrl+C detiene backend y frontend)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Ruta al python del venv (Windows usa Scripts/, Linux/mac usan bin/).
if [ -x "backend/.venv/Scripts/python.exe" ]; then
  PY="backend/.venv/Scripts/python.exe"
else
  PY="backend/.venv/bin/python"
fi

echo "[Faro] Iniciando Postgres (docker faro_db)..."
docker start faro_db >/dev/null 2>&1 || echo "  AVISO: no pude iniciar 'faro_db'. Verifica que Docker este corriendo."

echo "[Faro] Backend  -> http://localhost:8010"
"$PY" -m uvicorn backend.main:app --port 8010 &
BACK=$!

echo "[Faro] Frontend -> http://localhost:5000"
( cd Frontend && BACKEND_URL=http://localhost:8010 npm run dev ) &
FRONT=$!

echo ""
echo "[Faro] Listo. Abre http://localhost:5000  (login: demo@faro.app / demo1234)"
echo "       Ctrl+C para detener ambos."

trap 'echo; echo "[Faro] Deteniendo..."; kill "$BACK" "$FRONT" 2>/dev/null || true' INT TERM
wait
