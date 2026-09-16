# Faro — inventory purchasing decisions for distributors

Faro turns a distributor's sales history into the purchase decision of the day:
what to order, how much, from whom, and when it has to be placed so it arrives
before the stock runs out.

The pipeline, end to end:

```
sales history (CSV/Excel/SQL)
  → per-SKU forecasting (LightGBM, XGBoost, Prophet, ARIMA, ETS, Croston, LSTM)
  → coverage in days against current stock and the supplier's lead time
  → stock signal: PEDIR_YA · PEDIR_PRONTO · OK · SOBRESTOCK
  → purchase order, sent to the supplier, received back, lead time learned
```

Multi-tenant, Spanish and English throughout, and built to be deployed by
whoever runs it rather than by whoever wrote it.

---

## What you need to run it

| | |
|---|---|
| Python | 3.11+ (the backend and the forecasting engine) |
| Node | 18+ (the Next.js frontend) |
| PostgreSQL | 14+ — the only datastore. An empty database bootstraps itself. |
| Disk | a writable `storage/` directory for datasets, model artifacts and documents |

**Nothing else is required.** Every external service — the language model,
email, WhatsApp, SMS, document search, accounting integrations — is optional,
and each one turns itself off and says so on the screen that would have used
it. A deployment with no API keys at all still forecasts, still computes the
signal, and still produces purchase orders.

---

## Local development

```bash
# 1. A database. Anything Postgres; this is the one the tests expect.
docker run -d --name faro_db -p 5544:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres postgres:16

# 2. Python dependencies
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt   # Windows
# backend/.venv/bin/pip install -r backend/requirements.txt     # macOS/Linux
pip install -e ForecastingCore/

# 3. Configuration: copy the example and fill in the four required values
cp backend/.env.example backend/.env

# 4. The API — creates every table on first boot
backend/.venv/Scripts/python -m uvicorn backend.main:app --port 8010

# 5. The web app (proxies /api/* to the backend on 8010 by default)
cd Frontend && npm install && npm run dev
```

Then open <http://localhost:5000> and create the first account.

Steps 4 and 5 are what `./run.sh` (or `run.bat` on Windows) does for you, Postgres
included. **8010 is the port everything agrees on**: the frontend proxies there
unless `BACKEND_URL` says otherwise, and both run scripts start the API on it.

Two traps worth knowing before they cost you an hour:

- `Frontend/.env.local`, if you create one, is per-machine and **beats** a shell
  `BACKEND_URL`. When every `/api/*` call returns `500` with an empty body, it is
  pointing at a port nothing listens on.
- `uvicorn` does not reload on edits here — restart it after backend changes. A
  backend running old code answers `404 {"detail":"Not Found"}` on new routes,
  which looks like a missing endpoint and is a stale process.

---

## Configuration

Two layers, and the app always says which one is in effect:

1. **The environment** — `backend/.env`, or real environment variables. It is
   the floor: the database, the signing key and the deployment switches are
   read only from here, at boot.
2. **The panel at `/instalacion`** — every optional service can also be
   configured from inside the app. Values are stored encrypted in the database
   and take effect without a restart. A stored value **wins** over the file.

Who may open that panel is `INSTANCE_ADMIN_EMAILS`, not the `admin` role:
`admin` exists inside a tenant and every company that signs up has one, so it
cannot be what grants access to the deployment's credentials.

**On a fresh install you do not need it yet.** While the deployment has exactly
one company and has named nobody, that company's admins operate it — the person
who installs it is the person who signs up. That ends the moment a second
company exists, and the panel warns you while there is still time to act: put
your address in `INSTANCE_ADMIN_EMAILS` before you invite anyone, or nobody will
be able to open the screen again.

The encryption key behaves the same way. `INTEGRATIONS_SECRET_KEY` protects
every credential stored from the panel; leave it empty and one is generated at
`storage/instance_secret.key` on first use, so day one is not spent editing the
file this screen exists to replace. Back `storage/` up — losing that file means
re-entering every credential — and promote the key into the variable before
running a second process on a different volume, or each will make its own and
be unable to read the other's.

So the honest minimum to go from `git clone` to a working product is three
values: `SECRET_KEY`, `DATABASE_URL`, `FRONTEND_URL`. Everything else is
reachable from inside the running app.

The complete reference — every variable, what it does, and exactly what stops
working without it — is **[`docs/configuracion.md`](docs/configuracion.md)**. It
and `backend/.env.example` are both generated from
`backend/service_config/registry.py`:

```bash
python -m backend.scripts.gen_config_docs           # regenerate
python -m backend.scripts.gen_config_docs --check   # CI-style verification
```

A `Settings` field with no entry in that registry fails the test suite, which
is how the example file and the documentation stay true.

---

## Tests

```bash
python scripts/run_tests.py             # backend, then engine, then typecheck
python scripts/run_tests.py --backend   # one stage at a time
```

The script refuses to start while a dev server is listening: the job queue *is*
the `jobs` table, so a running backend claims the jobs the tests create and
reports defects that are not there.

What the suite does and does not prove: the backend and the engine are covered
(state asserted against the database, and every mutating endpoint has a
permission pair); the **frontend has no behavioural tests** — `tsc` checks
types. Screens are verified by hand in a browser, and the record of those walks
is in `docs/screen-inventory.md`.

---

## Repository layout

```
ForecastingCore/     the ML engine. ALL forecasting intelligence lives here.
backend/             FastAPI multi-tenant API — orchestration only, no ML.
  service_config/    what this deployment has, what is on, what is off.
  entitlements/      the two usage tiers. No billing: a tier is a column.
Frontend/            Next.js 14 app (pages in src/app, API client in src/lib).
deploy/              single-VPS Docker Compose production stack.
docs/                configuration reference, public API, screen inventory,
                     user manual (es/en) and the engine paper.
```

Two manuals ship built, each in both languages, and they answer to two
different readers:

| Manual | For | Source | Rebuild |
|---|---|---|---|
| `faro-manual-{es,en}.pdf` (62 pp.) | the purchasing manager: screen by screen, what to click, what the numbers mean | `docs/manual/` | `python -m backend.scripts.build_manual` |
| `faro-tecnico-{es,en}.pdf` (19 pp.) | **you**: architecture, algorithms, the exact formulas, and what each number does NOT mean | `docs/tech/` | `python -m backend.scripts.build_tech_manual` |

The user manual is built into `Frontend/public/` and is downloadable from the
landing page in the visitor's language. The technical one is built into
`docs/` and is deliberately **not** in `Frontend/public/` — anything in that
directory is served at the site root and downloadable by anyone who guesses the
filename, which is right for the user manual and wrong for a document that
lists the hard-coded constants and what the product does not do.

The technical manual is written against the code with `path:line` references,
and it names the assumptions and the gaps rather than hiding them — start there
before reading the source. Those references carry a `· symbol` anchor and
`backend/tests/test_tech_manual_citations_are_real.py` checks every one of
them, so a citation cannot quietly drift when code moves above it.

A new screen belongs in the user manual in both languages, or the manual is
lying about the product.

The separation is enforced, not merely intended: a test fails if pandas or
numpy are imported anywhere in `backend/` outside the three modules allowed to
touch them.

---

## Commercial model in the code

There is no checkout and no payment integration. Two tiers — `free` and `paid`
— ship **every feature**; the tier only decides how much fits (SKUs, users,
warehouses, saved forecasts, API keys, upload size). A tenant becomes `paid`
because somebody talked to the owner and the column was set:

```sql
UPDATE tenants SET tier = 'paid' WHERE slug = 'their-slug';
```

That single statement is the entire billing system, by design. The app's only
commercial surface is a "write to us" dialog, which hides itself when no
contact channel is configured.

---

## Deployment

`deploy/` holds a single-VPS Docker Compose stack (Caddy → Next.js → FastAPI,
a dedicated worker, and optionally a bundled Postgres), with the three growth
paths pre-wired: splitting the worker, moving to managed Postgres, and adding
claim-only workers. See **[`deploy/README.md`](deploy/README.md)**.

Two things that are your responsibility and are not automatic: a backup of the
`STORAGE_PATH` directory (it is not in the database dump, and it holds the key
that decrypts every credential in that database) and `SCHEDULER_ENABLED=true`
in exactly one instance — two schedulers send every daily alert twice.

**[`deploy/RESTORE.md`](deploy/RESTORE.md)** is the restore runbook, written by
performing one. Do the drill before you need it: a database-only restore boots,
lists every customer, and cannot send a single email.

Three more for whoever runs this in production:
**[`deploy/UPGRADE.md`](deploy/UPGRADE.md)** (upgrading, and why rolling back is
just the previous image), **[`docs/data-that-leaves.md`](docs/data-that-leaves.md)**
(every outbound connection, what it sends, how to turn it off — the answer to
your IT department's first question), and
**[`scripts/SMOKE.md`](scripts/SMOKE.md)** (the twenty-minute walk before you
tag: the suite says the backend behaves, not that the product works).

---

## License

MIT. See [`LICENSE`](LICENSE).
