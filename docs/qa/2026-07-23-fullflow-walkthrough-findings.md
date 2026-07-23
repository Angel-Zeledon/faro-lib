# Full-Flow Adversarial Walkthrough — Findings (2026-07-23)

Read-only browser QA over the whole app on `main` (post pandas-boundary merge).
**15/15 happy-path PASS. DB integrity fully clean. No security or data-integrity
defects.** All confirmed bugs are LOW/MINOR.

## Fixed (merged to main — commit d8d9ff0)

- **[1] Weekly-mode fractional order/transfer quantities.** The optimizer
  serializer rounded to 2 decimals, so weekly/monthly solves showed fractional
  units (e.g. `3692.67`). Now ceils to whole units (daily was already integer →
  unchanged). `backend/inventory/optimizer_service.py` + test.
- **[3a] Coverage header label.** `/inventory` detail table header hardcoded
  "Días cobertura" while cells adapt to the active period ("1 sem"). Header now
  shows the active period's unit. `Frontend/src/app/inventory/page.tsx`.

## Deferred (needs more than a safe one-liner)

- **[2] Weekly-mode /hoy summary contradicts the KPI tile.** Executive summary
  claimed "2 en riesgo inmediato" while the KPI read "Riesgo hoy 0" and one card
  showed. Both the summary (`products_at_immediate_risk`) and the KPI derive from
  the same `kpis['order_now']` in `backend/ai/narrative_service.py:108`, so the
  contradiction is not a counting-basis bug — likely a **stale/cached narrative**
  or an **active-session mismatch** (daily default session narrated while KPIs
  read the weekly active session). Needs browser reproduction in weekly mode to
  pin down which, before fixing. LOW.
- **[3b] Transfer-suggestion coverage in days under a weekly horizon.** The
  network transfer path (`backend/inventory/service.py:~1080-1115`) computes
  `donor_coverage_days_after = after / daily_demand` — genuinely in DAYS,
  regardless of the active period, and the copy `hoy.transfers_line` hardcodes
  "días". This is a real period-awareness gap (the value, not just the label):
  the transfer heuristic still reasons in daily units while the rest of the
  screen is period-aware. Fixing means expressing donor coverage and the
  `TRANSFER_MIN_DONOR_COVERAGE_DAYS` threshold in the active period's units, plus
  a `{unit}` placeholder in the copy. LOW but touches period semantics — do it
  with a proper test, not a relabel.
- **[4] Datasource metadata card shows "COLUMNAS —".** The preview table renders
  all columns correctly, but the metadata card's column count is unpopulated.
  Overlaps the in-app dataset-editor work (same `datasources` area / column-count
  path) — fold into that feature's merge rather than touching it concurrently.

## UX frictions (LOW, backlog)

- `/skus` doesn't auto-select the active session (user must pick it manually).
- `/skus` "Inventario" tab reflects training-time stock, not post-reception live
  stock (forecast snapshot vs live inventory inconsistency).
- Quick-start progress bar appeared stuck at 40% and didn't visibly auto-redirect
  after both fan-out sessions completed (training itself succeeded).
- Accessibility: console flags form fields without `id`/`name`/label across
  several screens (mermas, analysis, …).

## Security note (defensive — Fase 4)

A pre-existing httpOnly refresh cookie for a *foreign* tenant was able to
repopulate the access token after `localStorage.clear()`, briefly showing another
tenant's data in the same browser. Only reachable when two accounts are used in
one browser without a server-side logout. Login does not appear to invalidate a
pre-existing foreign refresh cookie, so a silent-refresh can race a fresh login.
Worth hardening when httpOnly-cookie auth is finalized (Fase 4). Not exploitable
across users/machines.
