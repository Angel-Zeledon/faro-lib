"""Inventory sync: keep StockAI's stock in step with the customer's own database.

Corporate customers keep inventory in their ERP's database — Oracle,
PostgreSQL, MySQL/MariaDB, SQL Server or MongoDB — under table and column
names nobody chose for us. A *sync profile* says, once, where the stock lives
there (a table, a view, a collection, or a saved read-only query / join /
pipeline) and how its columns map to StockAI's canonical fields; from then on
a worker loop re-reads it on a schedule and upserts `inventory_stock`.

Modules:

* `fields`    — the canonical fields and the per-field transforms (pure).
* `mapper`    — one source row -> one canonical row or a rejection with a
                reason; the same guards the bulk import has (pure).
* `suggest`   — the mapping assistant: ranks candidate tables and proposes a
                mapping from name heuristics and sample values (pure,
                deterministic; it never calls an LLM).
* `profiles`  — the profile store and its validation.
* `reader`    — reads a source in bounded batches through the existing
                read-only clients (`datasources.client` / `datasources.mongo`).
* `engine`    — the dry run and the run itself: stage, brake, apply.
* `scheduler` — the lease, the worker loop, backoff and the stale-sync alert.

Guarantees (the ones a wrong purchase decision would come from):

1. **A failing source never touches stock.** Nothing is written until the
   whole source has been read and staged.
2. **The safety brake.** Before anything is applied, a full-snapshot run is
   stopped — nothing changed, a critical alert raised — when the source
   returned zero rows, when more than `brake_max_vanished_pct` of the SKUs this
   profile knows vanished, when the valid row count fell below
   `brake_min_row_ratio` of the last successful run, or when more than
   `brake_max_rejected_pct` of the rows were rejected (a broken mapping).
3. **Idempotent.** A row whose values already match is not written at all: no
   `updated_at` bump, no snapshot. `inventory_snapshots` gets a row only when
   the quantity actually changed. Re-running a run changes nothing.
4. **A SKU that disappears from the source is never deleted.** The missing
   policy is `ignore` (default), `mark_inactive` (flagged on the profile's key
   list and reported; stock untouched) or `zero` (stock set to 0, with a
   snapshot).
5. **The watermark only advances on success.**

The conflict rule with manual edits (one rule, everywhere): **the source wins
for the fields a profile maps.** A field a person edits by hand on a synced
SKU is overwritten by the next run; the run counts and samples those
overwrites (`manual_overwritten`, `overwritten_sample`), raises a warning in
the activity feed, and the profile lists which fields it owns so the screen
can say so before anyone edits. Fields the profile does not map are never
touched.
"""
