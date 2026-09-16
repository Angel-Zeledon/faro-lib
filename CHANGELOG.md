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

### Added

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
  states the rollback property and how to check it; [`docs/datos-que-salen.md`](docs/datos-que-salen.md)
  lists every outbound connection, what it sends and how to turn it off.
* **`/health` reports loop freshness.** Each recurring loop records the
  boundary it last completed, so a scheduler that is up but has not done its
  rounds since Tuesday stops looking healthy.
* The import wizard **asks** whether `1.250` means 1250 or 1.25 instead of
  guessing, shows the parsed rows before committing, and offers "do not
  overwrite what I corrected by hand".

### Fixed

36 findings from a parallel-agent review of the surfaces nobody had walked.
The ones that cost money:

* Every analyst was excluded from every alert — the recipient queries asked for
  a role this product does not have, so only admins were ever reached while the
  UI invited anyone to link WhatsApp "to receive inventory alerts".
* Accepting a price break raised the quantity and never the price, so the
  saving reached nothing Faro stores or prints.
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

---

## Before this file

675 commits of it. `git log` is the record; from here on, this is.
