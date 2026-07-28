---
name: silent-failures
description: Use when reviewing or writing code that reports an outcome to a user — sends, saves, imports, background jobs, scheduled work, defaults, or anything with a fallback. Also when a user says a feature "doesn't work" but nothing errors, or when hunting for bugs in Faro. The lens that found almost every real defect in this codebase.
---

# Silent failures

Faro's expensive bugs share one shape: **the app reported success while nothing
happened.** None of them raised a visible error — which is exactly why none of
them had been found. Reading the diff never surfaces these; asking these
questions does.

## The questions

**1. Does the caller branch on the result, or assume it?**
`_transport_send` returned quietly when no mail transport was configured, so
every `try: _send() ... return True` reported an invite, a verification link and
a purchase order as sent to nobody. A function that cannot fail visibly will be
treated as infallible.

**2. If this fails, does anything survive it?**
Report generation ran as a background task; a failure wrote nothing anywhere,
and the later download answered `404 "generate one first"` — telling the user to
redo exactly what had just failed. Every attempt now writes a row.

**3. Does a default look identical to a choice?**
`lead_time_days INT NOT NULL DEFAULT 15` made "the user configured 15" and
"nobody ever touched this" the same row — and the app told the buyer "tu
proveedor tarda 15 días (lead time configurado)" when nobody had configured it.
If a value can be assumed, the schema must record who set it.

**4. Is the finding reaching the user, or only the log?**
Validation layers run in WARNING mode and only logged. Target leakage yields a
near-perfect accuracy on a worthless forecast, and there was no channel at all
to say the 97% was fake.

**5. Was the user's explicit choice honoured — and if not, were they told?**
Quick Start lets you pick "Mensual"; when the data cannot support it the fan-out
silently trains daily instead. The user believes they have a monthly plan.

**6. Do the two halves speak the same vocabulary?**
The stock seeding existed and ran on every training — and seeded nothing,
because it looked for `current_stock`/`unit_cost` while the wizard produces
`inventory`/`cost`. Wiring that exists is not wiring that works: **verify with
data**, not by reading.

**7. Does the number shown match the number meant?**
The daily digest counted the rows it had listed (10) instead of the SKUs at risk
(47), and never said how many were hidden.

## Verify with data, not by reading

Every real finding here was confirmed by measurement before any fix:

- 0 rows in `inventory_stock` after a completed training whose mapping *did*
  include price → the vocabulary mismatch.
- 8 threads, 1 job: the old claim sequence handed it to **all 8** in most rounds.
- One `append_log()` call under an injected commit fault → **2 rows**, caller
  told it succeeded.
- Login measured at 350 ms against a live server, not the 2.9 s the test claimed.

A hypothesis that sounds right and a hypothesis that is right look identical
until you measure. Write the throwaway probe.

## Tests

CLAUDE.md's mandate exists because of this class. Two failure modes to watch for
in tests you write *or inherit*:

- **A test that cannot fail.** `test_put_planning_over_reach_422` passed
  `horizon: 99`, which the request model rejected with `le=90` before the
  service ran — the guard it is named after never executed. After changing a
  budget or a bound, break it deliberately once and confirm it goes red.
- **A test that measures the machine.** An absolute wall-clock assertion inside
  a 25-minute suite fails for load, not for defects. Measure against a baseline
  taken in the same conditions. Same for a frozen `NOW` read back through an
  endpoint that calls `datetime.now()` — it drifts a day at UTC midnight, and if
  the value is stamped by the DATABASE the frozen clock only ever agreed by
  coincidence.

## When you fix one

Say what the user could not have known, not what the code did. The commit
message and the copy should both answer: *what were they being told, and what
was actually true?*
