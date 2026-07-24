# Pending Polish — Faro (2026-07-24)

Consolidated backlog after landing the in-app dataset editor + conversational
WhatsApp bot and the full-flow adversarial walkthrough. Nothing here is
critical or a security/data-integrity defect; almost everything is LOW.

Severity: **P1** = needed to actually ship the WhatsApp bot; **P2** = visible
inconsistency worth fixing; **P3** = backlog / by-design.

Legend: 🔨 = picked up now (parallel agents).

---

## P1 — Needed to turn the WhatsApp bot on in production

### 1. 🔨 Profile UI to link a WhatsApp number
The backend is complete (`POST /users/me/whatsapp/link` + `/confirm`), but there
is **no screen** in the profile where a user enters their number and the 6-digit
code. Without it nobody can enrol in the bot from the app. Was out of v1 scope
for the build agent; it's what's missing to use the bot for real.
- Touch: profile/settings page (`Frontend/src/app/...`), `api.ts`, `types.ts`,
  `translations.ts`. Backend endpoints already exist.

### 2. 🔨 Twilio signature behind a proxy
The inbound webhook validates against `request.url`. Behind the frontend proxy /
TLS termination, that URL won't match the one Twilio signed → permanent 403.
Needs the public URL fixed (a configured base URL, or reading `X-Forwarded-*`).
- Touch: `backend/api/v1/whatsapp.py`, `backend/config.py`, `test_whatsapp_webhook.py`.

### 3. Idempotency is last-`MessageSid` only
Dedupes Twilio's realistic retry (re-delivery of the most recent message), not an
arbitrary replay set. Acceptable for v1; note it. (No work planned now.)

---

## P2 — Visible inconsistencies (weekly / multi-period mode)

### 4. 🔨 Weekly-mode `/hoy` summary contradicts the KPI tile
Executive summary claimed "2 en riesgo inmediato" while the KPI read "Riesgo hoy
0" and one card showed. Both derive from `kpis['order_now']`
(`backend/ai/narrative_service.py:108`), so it is NOT a counting-basis bug —
likely a **stale/cached narrative** or an **active-session mismatch** (daily
default session narrated while KPIs read the weekly active session). Needs
code-level diagnosis (and browser repro if feasible) before fixing.

### 5. Transfer-suggestion coverage in days under a weekly horizon
The network transfer path (`backend/inventory/service.py:~1080-1115`) computes
`donor_coverage_days_after = after / daily_demand` — genuinely in DAYS regardless
of the active period, and `hoy.transfers_line` hardcodes "días". A real
period-awareness gap (the value, not just the label): express donor coverage and
the `TRANSFER_MIN_DONOR_COVERAGE_DAYS` threshold in the active period's units,
plus a `{unit}` placeholder in the copy. Do it with a test, not a relabel.

---

## P3 — UX frictions / accessibility (backlog)

6. `/skus` doesn't auto-select the active session (user must pick it manually).
7. `/skus` "Inventario" tab reflects training-time stock, not post-reception live
   stock (forecast snapshot vs live inventory inconsistency).
8. Quick-start progress bar appeared stuck at 40% and didn't visibly auto-redirect
   after both fan-out sessions completed (training itself succeeded).
9. Accessibility: several form fields lack `id`/`name`/`label` (mermas, analysis,
   …); the console flags it across screens.

---

## Tech debt / security

### 10. Suspected latent bugs (ruff F841 — computed-then-dropped values)
Left by the dead-code cleanup because they smell like bugs, not dead code — a
value computed and then discarded. Worth investigating, not blindly deleting:
- `backend/tenants/service.py:45` `last_exc` (retry loop — probably meant to
  re-raise the last exception).
- `backend/workers/runner.py:185` `ref`, `:580` `report`.
- `ForecastingCore/forecasting_core/validation/leakage.py:31-32` `group_col`,
  `train_ratio`; `.../validation/auto_correct.py:111,113` `dt_col`, `freq`;
  `.../data/profiler.py:440` `warned_dupe`; `.../analysis/seasonality.py:57`
  `max_power`.

### 11. Foreign refresh-cookie race (defensive, Fase 4)
A pre-existing httpOnly refresh cookie for a *foreign* tenant could repopulate the
access token after `localStorage.clear()`, briefly showing another tenant's data
in the same browser. Only reachable with two accounts in one browser without a
server-side logout. Login doesn't appear to invalidate a pre-existing foreign
refresh cookie. Harden when httpOnly-cookie auth is finalized. Not exploitable
across users/machines.

---

## By design, not bugs (dataset editor v1)
No SQL-source editing, CSV-only output, no undo/redo. All deliberate v1 scope.

---

## Picked up now (parallel)
- **#1** Profile UI to link WhatsApp number.
- **#2** Twilio signature behind a proxy.
- **#4** Weekly-mode `/hoy` summary vs KPI contradiction.

---

## Update 2026-07-24 (evening) — after the real-browser QA pass

**Done & merged (all 7 polish items + the two feature specs):** #1, #2, #4, #5,
plus #6/#7 (/skus), #8 (quick-start), #9 (a11y). Full suite green each step.

**QA found & fixed 2 real bugs (real DB-verified, committed to main):**
- BUG A (`814af57`) — weekly mode printed per-period coverage with a hardcoded
  "días" in the recommendation generator, AI narrative, and `/hoy` cards (value
  was right, unit label wrong, and the "excess" mixed units). Now labeled in the
  active period unit; daily byte-identical.
- BUG B (`a89f9bc`) — unlinking a WhatsApp number left a dangling
  `whatsapp_verified_at`; now clears both columns. Also added the 409
  already-verified-by-another-user test.

**WhatsApp bot — live status (real, verified):** outbound send DELIVERED to a
real phone via the Twilio sandbox. Anthropic key authenticates but the account
has **no credit**, and the local Ollama models are too slow (23s / timeouts) for
a real-time turn — so the bot runs in **generic mode** (`WHATSAPP_BOT_GENERIC_MODE`,
`fa50b9e`): fast honest canned reply + deterministic confirmations. Smart mode
returns automatically once ANTHROPIC_API_KEY has credit. Inbound still needs a
public tunnel (ngrok) for Twilio to reach the webhook.

**Test-suite false-positive audit (QA):** all new tests assert real DB/disk
state and would fail if the feature broke — none weak.

**Still open (minor, from QA):**
- `/skus` Inventario coverage tile: correct value, label still "Días de
  Cobertura" (same class as BUG A). → folded into the /skus cleanup agent.
- `/config` WhatsApp phone input + `/skus` session `<select>` lack id/name. →
  folded into the cleanup agents.
- **PlanningControl 401 race:** after the quick-start→`/hoy` auto-redirect, the
  period toggle can briefly vanish because `PlanningContext` caches `null` on a
  `/planning` 401 with no retry. Self-heals on any navigation. Worth a real fix
  (don't cache null on 401; retry once) — not yet done.

**In flight now:** two frontend visual-noise cleanup agents (emojis/decoration,
density, unclear buttons) in worktrees.
