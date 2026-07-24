@echo off
REM ============================================================================
REM  Faro - arrancar la app en local (Windows)
REM  Levanta: Postgres (docker faro_db) + backend :8010 + frontend :5000
REM  Requisitos ya instalados en esta maquina: Docker, el venv de backend y
REM  node_modules del Frontend. Doble clic para prender todo.
REM ============================================================================
cd /d "%~dp0"

echo [Faro] Iniciando Postgres (docker faro_db)...
docker start faro_db 1>nul 2>nul
if errorlevel 1 echo   AVISO: no pude iniciar el contenedor "faro_db". Abre Docker Desktop y reintenta.

echo [Faro] Backend  -^> http://localhost:8010
start "Faro Backend" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8010"

echo [Faro] Frontend -^> http://localhost:5000
start "Faro Frontend" cmd /k "cd Frontend && set BACKEND_URL=http://localhost:8010&& npm run dev"

echo.
echo [Faro] Arrancando... el frontend tarda ~15-30s la primera vez.
echo        App:   http://localhost:5000
echo        Login: demo@faro.app / demo1234
echo        (Cierra las dos ventanas "Faro Backend" y "Faro Frontend" para detener.)
timeout /t 22 1>nul
start "" http://localhost:5000
