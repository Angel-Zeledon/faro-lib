@echo off
REM ============================================================================
REM  StockAI - start the app locally (Windows)
REM
REM      start.bat            Postgres + backend + frontend
REM      start.bat --seed     ...and fill the demo tenant first
REM
REM  Opens two windows, "StockAI Backend" and "StockAI Frontend". Close both to stop.
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "SEED=0"
if /i "%~1"=="--seed" set "SEED=1"

REM ---- The interpreter -------------------------------------------------------
set "PY=backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [StockAI] No virtualenv at backend\.venv.
  echo        Create it and install backend\requirements.txt first.
  exit /b 1
)
if not exist "Frontend\node_modules" (
  echo [StockAI] Frontend\node_modules is missing.
  echo        Run:  cd Frontend ^&^& npm install
  exit /b 1
)

REM ---- Postgres --------------------------------------------------------------
echo [StockAI] Starting Postgres (docker faro_db)...
docker start faro_db 1>nul 2>nul
if errorlevel 1 (
  echo [StockAI] Could not start the "faro_db" container. Is Docker Desktop running?
  echo        If it does not exist yet:
  echo          docker run -d --name faro_db -p 5544:5432 -e POSTGRES_PASSWORD=postgres postgres:16
  exit /b 1
)

REM ---- Which port the backend listens on -------------------------------------
REM NOT a constant, and this file used to hardcode 8010 — which on a machine
REM where another project's container already holds that port means uvicorn
REM cannot bind and the app looks broken.
REM
REM Frontend\.env.local is gitignored, per-machine, read by Next, and it BEATS
REM a shell BACKEND_URL — so it is the only thing that really decides where the
REM browser's /api/* calls land. Ask it, and fall back to the same default
REM next.config.mjs uses.
set "PORT=8010"
if exist "Frontend\.env.local" (
  for /f "usebackq tokens=1,* delims==" %%A in ("Frontend\.env.local") do (
    if /i "%%A"=="BACKEND_URL" (
      for /f "tokens=3 delims=:/" %%P in ("%%B") do set "PORT=%%P"
    )
  )
)

REM ---- Backend ---------------------------------------------------------------
REM 127.0.0.1, never "localhost": the latter can resolve to ::1 while uvicorn
REM listens on IPv4 only, which surfaces as a 500 with an empty body.
echo [StockAI] Backend  -^> http://127.0.0.1:!PORT!
start "StockAI Backend" cmd /k ""%PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port !PORT!"

REM ---- Wait for it to actually answer ----------------------------------------
REM A fixed sleep was what this used to do, and it opened the browser on a
REM screen that 500s whenever the machine was slower than the guess. The port
REM being bound is also not the same as the app being up, so ask /health.
echo [StockAI] Waiting for the backend...
set "READY=0"
for /l %%I in (1,1,40) do (
  if !READY!==0 (
    curl -fsS "http://127.0.0.1:!PORT!/health" >nul 2>&1
    if not errorlevel 1 (set "READY=1") else (timeout /t 1 /nobreak >nul)
  )
)
if !READY!==0 (
  echo [StockAI] The backend never answered /health on port !PORT!.
  echo        Look at the "StockAI Backend" window for the error.
  exit /b 1
)

REM ---- Optional: fill the demo tenant ----------------------------------------
REM Three scripts, in order: the operational core, the screens it leaves thin,
REM then the ones that shipped after those two were written. All idempotent.
if "%SEED%"=="1" (
  echo [StockAI] Seeding the demo tenant...
  "%PY%" -m backend.scripts.seed_demo
  "%PY%" -m backend.scripts.seed_demo_screens
  "%PY%" -m backend.scripts.seed_demo_today
)

REM ---- Frontend --------------------------------------------------------------
echo [StockAI] Frontend -^> http://localhost:5000
start "StockAI Frontend" cmd /k "cd Frontend && set BACKEND_URL=http://127.0.0.1:!PORT!&& npm run dev"

echo.
echo [StockAI] Backend is up. The frontend takes ~15-30s the first time.
echo        App:   http://localhost:5000
echo        Login: demo@faro.app / demo1234
echo.
echo        Note: uvicorn does NOT reload on edits - restart after backend changes.
echo        Do NOT run "npm run build" while this is up; it corrupts .next.
timeout /t 20 /nobreak 1>nul
start "" http://localhost:5000
endlocal
