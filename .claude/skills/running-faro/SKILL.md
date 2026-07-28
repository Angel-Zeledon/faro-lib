---
name: running-faro
description: Use when starting, restarting or verifying the Faro app in a browser, when API calls return 500 with an empty body or 404 {"detail":"Not Found"} on routes that exist, when a code change "does not show up", or before running the backend test suite. Covers the port/proxy traps that make a working app look broken.
---

# Running Faro without chasing ghosts

Three traps in this repo make a healthy app look broken. Each one has cost an
hour. Check them before debugging anything else.

## 1. Which backend is actually serving?

`Frontend/.env.local` is gitignored, per-machine, and **beats a shell
`BACKEND_URL=...`** — exporting the variable before `npm run dev` does nothing
if that file names a different port. Read the file first:

```bash
cat Frontend/.env.local          # this wins
```

`next.config.mjs` defaults to `http://127.0.0.1:8010`, the port CLAUDE.md tells
you to run the backend on.

## 2. Two symptoms that lie

| What you see | What it actually is |
|---|---|
| `500` with an **empty body** on every `/api/*` | The proxy target is unreachable. Usually `localhost` resolving to `::1` while uvicorn binds IPv4 only — always use `127.0.0.1`. |
| `404 {"detail":"Not Found"}` on a route you just added | FastAPI's own 404: the running backend is **old code**. `uvicorn` does not reload. |

Confirm the second in one call before touching anything:

```bash
curl -s localhost:8002/openapi.json | grep -o "your-new-route"
```

## 3. Restarting the backend

It does not reload. After any backend edit:

```bash
# kill whatever holds the port, then, from the repo root:
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

## Before running the backend test suite: stop the dev server

The job queue **is** the `jobs` table, and `dequeue` takes any `QUEUED` job
regardless of which process created it. A dev server on the same database will
claim jobs the tests create, run them, write into their logs and flip their
sessions to FAILED. This is measured, not theoretical — it is why one test read
22 log lines after writing 20.

It also halves the wall-clock: a full run is ~25 min alone and over an hour
while anything else is competing.

```bash
cd backend && python -m pytest tests/ -q -p no:cacheprovider
```

Never run the backend suite and the ForecastingCore suite at the same time —
the timing-sensitive tests then fail for load, not for defects.

## Driving the browser

- Frontend `http://localhost:5000`, backend `127.0.0.1:8002` (or whatever
  `.env.local` says).
- The proxy rewrites `/api/:path*` → `/api/v1/:path*`. From the browser console
  call `/api/sessions/...`, **not** `/api/v1/...` — the latter 404s as a
  duplicated prefix.
- Auth token: `localStorage.fp_access_token`.
- Test email addresses must use `@faro-e2e.io` — that domain has no MX, so
  nothing can reach a real person. `backend/.env` holds REAL SMTP and Twilio
  credentials; never use a domain that could receive mail.

## Never edit source files with PowerShell

`Get-Content | Set-Content` re-encodes: it adds a BOM and turns every accented
character and em-dash into mojibake. The loss happens on the READ, so changing
`-Encoding` does not help. This repo is full of Spanish copy and em-dashes, so
the damage is guaranteed. Use the Edit tool. If it already happened:
`git checkout -- <file>` and redo the edit.
