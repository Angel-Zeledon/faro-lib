# Changelog

What changed, for somebody deciding whether to upgrade. Not a git log — a
release with no entry here is not a release.

Format: `vMAJOR.MINOR.PATCH`. MINOR adds, PATCH fixes, MAJOR is reserved for a
release whose migration is **not** additive. Every migration to date is
additive (`ADD COLUMN IF NOT EXISTS` and friends), which is why rolling back is
putting the previous image back and nothing else — see
[`deploy/UPGRADE.md`](deploy/UPGRADE.md).

Set `APP_VERSION` in `deploy/.env` when you tag, so `/health` can say what it
is running.

---

## Unreleased

### Changed

* **The product is now StockAI.** Every screen, email, WhatsApp body, PDF
  manual and document says StockAI instead of Faro, and the lightning-bolt
  badge is gone: the mark is the name itself, "stock" + "ai" set in Space
  Grotesk (`Frontend/src/components/brand/Wordmark.tsx`), on the sidebar, the
  landing and every sign-in screen. The manuals are now
  `stockai-manual-{es,en}.pdf` and `stockai-technical-{es,en}.pdf`; the CSV
  template is `plantilla_stockai.csv`.
  **One breaking rename for anyone who already wired a desktop MCP client:**
  the stdio adapter is `mcp_server/stockai_mcp.py` and reads `STOCKAI_URL` /
  `STOCKAI_API_KEY`. The MCP server now reports its name as `stockai`.
  **Deliberately unchanged**, because an existing install depends on them:
  the compose project, database name and user, Docker volumes and image users
  (`faro`), the demo tenant id `ten_faro_demo` and its `@faro.app` logins, and
  the `faro-core` package name.

### Added

* **An MCP server, so a customer's AI client can read their tenant.**
  `POST /api/v1/mcp` speaks the Model Context Protocol over the same
  `sk_live_*` key and the same 120-per-minute ceiling as the REST API, with
  **five read-only tools**: the active planning context, the morning briefing,
  the stock signal per SKU, the data sources and a training run's status. Point
  Claude (or anything else that speaks MCP) at the URL and ask what to buy.
  For desktop clients that speak stdio instead of HTTP,
  [`mcp_server/stockai_mcp.py`](mcp_server/stockai_mcp.py) is a standard-library-only
  pipe to the same endpoint — one file, nothing to install.
  **Nothing there writes**, deliberately: an AI client cannot render StockAI's
  confirmation card or hold an undo token, so uploading, training and logging
  orders stay on the REST calls where a person triggers them. The MCP endpoint
  is on the published surface, so `PUBLIC_API_ONLY` still serves it.
  [`mcp_server/README.md`](mcp_server/README.md), and a new section on the
  `/api` screen with the URL and the tool list.
* **"Qué ha pasado" (`/actividad`) and a bell that carries it.** Every event
  the product can record is declared in one registry with a severity and a
  reason code: trainings, ERP syncs, purchase orders generated / sent / **not
  sent**, stock imported, shrinkage, transfers, plan ceilings, users, roles and
  API keys. `critical` and `warning` reach the bell; the full history, `info`
  included, lives on the new screen, filterable by topic and importance and
  readable by every role. Before this, a training that failed at 3 a.m. left no
  trace a tenant could read.
* **Restore, upgrade and egress runbooks.** [`deploy/RESTORE.md`](deploy/RESTORE.md)
  was written by performing the restore; [`deploy/UPGRADE.md`](deploy/UPGRADE.md)
  states the rollback property and how to check it; [`docs/data-that-leaves.md`](docs/data-that-leaves.md)
  lists every outbound connection, what it sends and how to turn it off.
* **`/health` reports loop freshness.** Each recurring loop records the
  boundary it last completed, so a scheduler that is up but has not done its
  rounds since Tuesday stops looking healthy.
* The import wizard **asks** whether `1.250` means 1250 or 1.25 instead of
  guessing, shows the parsed rows before committing, and offers "do not
  overwrite what I corrected by hand".

### Fixed

**The forecast a user receives is now produced by a model that has seen all of
their history.** The ML and cross-learning models were fitted on the first 80%
of the data and served from there — the statistical models (ETS, ARIMA, Prophet,
Croston) had always refitted on the whole series before forecasting, so two
rules were in force in the same run and the results were compared in one table.
The model that is *graded* still stops at the cutoff, because it is scored on
what comes after it; only the model that *forecasts* changed. Measured on the
demo catalogue (10 SKUs x 450 daily buckets, the last 30 held out and shown to
nothing): the cross-learning model was over-forecasting by 7-21% on **every
series**, and refitting removed it — forecast/actual 1.141 → 0.997 and WAPE
0.150 → 0.077, better on 10 series out of 10.

**Croston no longer inflates the demand rate on intermittent SKUs.** Its
inter-arrival intervals were built with a zero-length first gap, which seeded
the interval smoothing at zero and, at alpha=0.1, took dozens of demand events
to recover from. A too-small interval is a too-large rate: 10 units every 5
buckets came out as 7.4 per bucket instead of 2.

**Prophet stopped inventing a yearly cycle it cannot see.** `yearly_seasonality`
was forced on regardless of history length; a tenant with 8 or 14 months got a
yearly pattern fitted to one incomplete pass and extrapolated over the horizon.
Both seasonalities are now Prophet's own `auto`, which also switches the weekly
one off on sessions resampled to monthly buckets. Prophet now also receives the
tenant's **holiday calendar**, which the ML path has always had.

**The supplier's delivery variability was asked for, shown back, and ignored.**
`suppliers.lead_time_std` has a column, a validated API field, a form input
("La variabilidad del tiempo de entrega") and a supplier card that prints it
back as "±N d" — and the safety stock was `z · σ · √L`, which never read it. The
cushion now uses the standard combined variance, `√(L·σ_d² + d²·σ_L²)`, with the
spread taken from that supplier's real receptions once there are enough of them
and from the configured value until then. A SKU with no supplier is unchanged to
the digit. **This moves money**: on the demo tenant's own suppliers the cushion
grows roughly threefold and the suggested order by about a quarter.

**Phone users were told the daily screen needs a computer — and sent to it.**
The route list behind that notice still named `/hoy`, which is now a redirect to
`/compras`, a screen that does have a phone layout. `/mensajes` was in the same
position. Both now resolve.

**The top bar had no name for eight screens.** Its title table was keyed by the
pre-rename routes, so it fell back to "StockAI" on `/ventas`, `/archivos`,
`/pronosticos`, `/historial`, `/asistente`, `/mi-cuenta`, `/usuarios` and
`/automatizacion`. The same staleness put the global session badge beside the
forecasts screen's own session picker — the exact contradiction the code
comment there says the list exists to prevent.

**The daily digests could be mailed twice.** The scheduler guards against
re-sending on a restart, but not after its own sleep: a clock corrected
backwards during it wakes the loop before the boundary, resolves to a pass that
already ran, and sends everything again. Both daily loops now skip a boundary
they have already recorded. The monthly overstock loop had the same shape, where
a duplicate would re-snapshot a closed month and re-send its ROI recap.

**`/instalacion` opened on the installation operator's panel** — which a tenant
admin is not meant to read and cannot use — and flashed it for a moment even
when it then corrected itself. It now opens on the tab that belongs to whoever
is reading, and shows no tab at all until it knows which.

**`/automatizacion` opened on API keys.** Scheduled recalculation is the tab a
distributor uses; it is now first.

**The landing promised a paying customer something the code refuses.** Its plan
table rendered one blanket "Sin límite" for the whole paid column, including
file size, which is capped at 2 GB. Each row now states what **each** tier
gets, and the type refuses a row that does not.

**Every product was shown as urgent on a tenant that had not imported stock.**
`/pronosticos` falls back to the engine's training-time recommendation when a SKU
has no live inventory row, and the engine never receives stock levels — so
`current_stock` was 0 for every SKU, `current_stock <= reorder_point` was true for
every SKU, and the screen rendered **PEDIR_YA** across the whole catalogue. "No
stock supplied" is now its own state: no risk figure, no coverage figure, and the
screen shows `SIN_DATOS`. A caller that passes a real stock level — including a
real zero — is unaffected.

**The engine's safety stock was computed from the wrong quantity.** It used the
dispersion of the forecast *path* rather than of the forecast *error*, so a flat
forecast — what a good model produces on a stable product — asked for a cushion
of roughly zero, and a seasonal one asked for a cushion the size of its
seasonality. It now derives sigma from the forecast's own q90 band, the same way
the purchase quantity already did, so the two layers finally mean the same thing
by "sigma".

**Croston over-forecast the slowest-moving stock, twice over.** Its
inter-arrival intervals were built with a spurious zero-length first gap, which
seeded the interval average at zero and took dozens of demand events to recover
from — a too-small interval is a too-large demand rate. On top of that, classic
Croston is a biased estimator by construction (it divides two independently
smoothed quantities), and the bias is upward. Both are fixed: real intervals, and
the standard Syntetos-Boylan correction. Concretely, 10 units every 5 buckets used
to come out as 7.4 per bucket and now comes out as 1.9.

**Hyperparameter tuning optimised a different question from the one that picks
the model.** It minimised one-step MAE — which is blind to the difference between
being short and being long — with no gap between a fold's training data and the
window it is scored on, while the model it tuned is selected on the asymmetric
cost over the whole horizon, with that gap. It now optimises the same thing the
selection uses, and it shares the walk-forward splitter instead of carrying its
own copy. (Tuning remains off by default.)

**The ensemble line is no longer built from a metric nothing else uses.** It was
weighted by the 1-step MAE of the ML models only, which silently gave every
statistical model in the run a weight of zero while still feeding it into the
average, and it shrank toward zero whenever a weighted model produced no
forecast. It is now weighted on the same metric that crowns the champion, over
every model that competed, and renormalized over the models that actually
produced points. It remains display-only: no purchase order is computed from it.

36 findings from a parallel-agent review of the surfaces nobody had walked.
The ones that cost money:

* Every analyst was excluded from every alert — the recipient queries asked for
  a role this product does not have, so only admins were ever reached while the
  UI invited anyone to link WhatsApp "to receive inventory alerts".
* Accepting a price break raised the quantity and never the price, so the
  saving reached nothing StockAI stores or prints.
* A scheduled retrain ran on the session the whole app was reading and marked
  it FAILED on any error, taking /hoy, the semáforo and the daily digest with
  it. Each run now trains a new session and only replaces what the buyer reads
  once it succeeds.
* An ERP-synced tenant was told to buy a full reorder for every branch while
  the goods sat in the main warehouse: a missing stock row was read as zero.
  It now reads SIN DATOS, with the reason on screen.
* A purchase order's header committed without its lines when a line failed,
  so the order said twelve lines and the database held eleven.
* Supplier fill rate counted deliveries still inside their promised window, so
  a supplier who had shorted nothing printed 50%.
* Stock snapshots had no warehouse, so one SKU's history was two warehouses
  interleaved and the trend read "+585% demand" that never happened.
* A restart at 08:02 skipped the whole day's alerts, silently.
* "Export all SKUs" could freeze the tab in an infinite loop; the CSV writers
  escaped nothing and shipped no BOM; three downloads threw `HTTP 401` instead
  of refreshing the token.

### Changed

* **The screens a buyer opens no longer talk like an analyst.** "Precisión
  promedio" is gone from the daily screen, where it appeared three times — as a
  headline number, inside the written summary and again in the footer — coloured
  amber for a normal tenant and impossible to act on. Its three explanations
  disagreed with each other and with the code. `/mi-cuenta` no longer lists nine
  algorithms by their raw ids (`lightgbm`, `xgboost`, `prophet`, `lstm`) under
  "MACHINE LEARNING" / "DEEP LEARNING" tags; it says, in one line, that StockAI
  tries several methods per product and keeps whichever is least wrong on your
  own history, and that there is nothing to configure. The product list on
  `/pronosticos` no longer prints `MAE 8.61` on each card — the Alta / Media /
  Baja reliability that was already there says the same thing in a word.
* **One word per concept.** The Spanish catalogue used "lead time", "tiempo de
  entrega" and "plazo" for a single idea, and "sesión", "carga", "actualización"
  and "datos en uso" for another. They are now "tiempo de entrega" and
  "actualización" everywhere, including the alert emails. ("Plazo de pago" is a
  different thing and was left alone.) `/historial` says "Planifica hasta" and
  "Detalle" instead of "Horizonte" and "Granularidad", and `/escenarios` no
  longer shows the raw stored `PEDIR_YA` on screen — the stored value is
  unchanged, only its label.
* `POST /alerts/read` no longer requires analyst — the bell now carries
  tenant-wide events, so a viewer can collect a badge and must be able to clear
  it.
* `POST /inventory/bulk` **refuses** a file whose decimal mark is ambiguous
  (422 `inventory_import_number_format_unclear`) instead of guessing. Machine
  clients can answer with `thousands_dot`.
* `GET /inventory/status/export-po` takes an optional `warehouse`.
* `POST /inventory/price-breaks/evaluate` accepts `supplier_id` per cart line.
* Runtime data under `backend/storage/` is no longer tracked by git. It was
  never meant to be (`.gitignore` lists it; `.gitignore` does not untrack what
  is already tracked), and it included a tenant that no longer exists.

### Removed

* **The separate p10/p50/p90 quantile models.** Training fitted three extra
  regressors per ML model per SKU — measured at **58% of all ML training time**,
  the single most expensive step in a run. Their metrics were discarded by
  design, and the layer that turns a band into a purchase quantity refused
  their band on the record: a quantile model fitted on the training set and run
  recursively tightens on its own residuals, and its p90 measured 43% narrower
  than the honest one. Traced end to end, their numbers survived into exactly
  one pair of stored fields (`lower`/`upper`) that the forecast chart reads only
  as a fallback for sessions older than the quantile columns, and that the
  reorder point explicitly refuses. The majority of every training run was being
  spent on a number nothing current displays or decides with. There is **one**
  band now — the empirical quantiles of the out-of-fold residual bank, the one
  both the chart and the reorder point already used — and `p10/p50/p90` are
  aliases of `q10/q50/q90`. No configuration changes; forecasts keep every
  column they had.
* **A second, worse recursive forecaster.** `_ml_recursive_forecast` held the
  rolling and calendar features constant across the whole horizon — its own
  docstring called that "a valid approximation for short horizons", against a
  default horizon of 30. Production never called it; the only thing keeping it
  alive was its own six tests. The real path rebuilds every feature at every step
  from the same builders training used.
* **The Alegra and Siigo accounting integrations**, the `/integraciones`
  screen, the 6:00 UTC sync loop, `ALEGRA_BASE_URL`, `SIIGO_BASE_URL` and the
  `integration_connections` table. The code was written and tested and had
  **never run against a live account**; the stock fetch was known wrong for a
  multi-branch tenant; and both are Colombian accounting software while the
  anchor market is Costa Rica. Selling an integration nobody had run was the
  part that could not stand — data still comes in as an export, uploaded or
  pushed to the API.

  **This is the one migration in the product's history that is not additive.**
  `drop_integration_connections` deletes a table, so rolling back to a release
  that still has the integrations screen leaves it reading something that is no
  longer there. Deliberate: the alternative — keeping a table of encrypted
  third-party ERP credentials that nothing reads — is worse. Those rows should
  not outlive the feature that needed them. If you had a connection configured,
  export first ([`deploy/UPGRADE.md`](deploy/UPGRADE.md)); the credentials
  cannot be recovered afterwards and were never recoverable in plaintext
  anyway.

  What did NOT change: `INTEGRATIONS_SECRET_KEY` keeps its name and its job. It
  still encrypts every secret `/instalacion` stores, and renaming it would
  orphan every existing deployment's stored credentials.

---

## Before this file

675 commits of it. `git log` is the record; from here on, this is.
