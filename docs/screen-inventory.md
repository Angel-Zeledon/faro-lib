# Screen inventory — what has been walked and what has not

**Created:** 2026-08-06

This document exists because "I already checked it" meant nothing. Each pass
covered the screens somebody happened to open that day, and the rest **were not
left clean: they were left unknown**. Without this list, "how much is missing"
is a feeling; with it, it is a number.

## What counts as "walked"

Opening the screen does **not** count. A walk is:

1. Clicking every action on the screen, not just the main one.
2. Looking at every number and asking **where it comes from and whether it is
   true**. Most of this product's defects do not raise an error: they show a
   wrong number perfectly calmly.
3. Provoking the unhappy path at least once — the malformed file, the role
   without permission, the network down, the date already past.
4. Writing down here the date and **what was looked at**, not just that it was.

A screen walked along one path is not walked along the others. The "what is
missing" column is the honest part of the table.

## State as of 2026-08-06

`Actions` = number of `onClick`/`onSubmit` handlers on the page. It is a crude
measure of the surface's size, not of its risk.

| Screen | Route | Actions | Last walk | What was verified | What is missing |
|---|---|---:|---|---|---|
| Inventory | `/inventario` | 60 | 2026-08-09 | Semáforo, per-warehouse tab, stock editing, read-only "Todas", the "Aún no" label, the import hint; bulk stock/lead-time editing and the semáforo recomputed against real data (coverage, quantity to order) | Shrinkage, dead stock, PDF export, Supplier view, stock CSV import. **Events and seasons walked on 2026-08-16** (create an event, simulate, per-SKU multiplier; three defects found and fixed — see `stability.md`); still missing the LatAm calendar, editing/deleting an event and the per-family and per-category multiplier |
| Forecasts | `/pronosticos` | 17 | 2026-08-10 | The quality notice and its 5 details; the Forecast / How it sells / Metrics / Quality / Inventory tabs; D/W/M/Q/Y granularity (aggregation is coherent: 180 → 26 → 6 → 2 → 1 points and the average scales); multi-model selection and the confidence band; search; two sessions compared side by side; the backtest panel; the metrics table against the API | The three exports (per-SKU Excel, "All SKUs", PDF), full screen, the tutorial, "See detailed statistical analysis". **Four findings, one fixed and three open — see below** |
| Files / Sources | `/archivos` | 40 | 2026-08-09 | Preview (a cp1252 file with accents intact — a different reader from the training one); column and row editor over all 360 rows; "Save as new"; rename (persists `Ñ`, `ú` and an em dash); delete with a confirmation that names the file and clears the database **and the disk**; the Analysis tab | Connecting a SQL source ("New item"), "Replace file", search, the 9-step tutorial, running a full Analysis |
| Purchase panel | `/compras` | 14 | 2026-08-10 | Optimiser (horizon, transfers without cycles, explanation vs semáforo); approving and rejecting recommendations; the approved cart; generating a PO (it lands in the database: 348 units, ₡417,600); executive summary over real data; **permissions exercised with a real viewer and a real admin, at normal and narrow widths** (see below) | Sending to suppliers, destination warehouse selection, quantity editing, undoing an approval. The "Create transfer" gate was left **unwalked**: the active session produced no transfer suggestions in the briefing |
| My sales | `/ventas` | 11 | 2026-08-09 | Upload, mapping, the gate with remediations, a full training run; **a cp1252 file with `;`, dd/mm/yyyy dates and accented SKUs** — accents intact and day-first resolved on its own | Reusing an already-uploaded file, repeating the previous load, sample data, cancelling mid-run |
| My account | `/mi-cuenta` | 23 | 2026-08-06 | Time zone (read and change), model list | Currency, WhatsApp, password change, theme/language, granularity, activity log |
| Landing | `/` | 8 | 2026-08-10 | **The functional promises checked against the code**: "on the third reception" = `MIN_LEAD_TIME_OBSERVATIONS = 3` exactly, and "included in every plan" is true (there is no gate); the CTAs point at `/signup?demo=1`, which signup does read and which wires into `demo_quickstart` | Actually running the demo (it would create another tenant), anchor navigation, the contact form, mobile view. **The hero's row of figures is unsupported — see below** |
| AI assistant | `/asistente` | 14 | 2026-08-10 | Conversation list, opening one, session selector, **sending a question with no LLM behind it**: it answers in Spanish, honestly, labelled as an error ("The assistant took too long… your question is still here") | New conversation, search, rename/delete |
| Users | `/usuarios` | 19 | 2026-08-09 | Creating a user with a role (lands `pending_confirmation`, unverified); status and role filters; **permissions exercised as a real viewer**: writes refused with 403 and state unchanged (see below) | Editing a user, suspend/reactivate, changing somebody else's role, resending an invitation, being unable to demote yourself |
| Scenarios | `/escenarios` | 6 | 2026-08-16 | There is no wall any more (the plans were removed). A **demand change** rule ×1.4 (38.8 → 54.4 daily, 131 → 240 units) and a **supplier delay** of +7 days (lead time 9 → 16, Pedir pronto → **Pedir YA**, 131 → 422): both compared against the current plan and against the arithmetic | Promotion and safety stock, saving and re-running a scenario, several rules at once, filters by SKU/category/date, the 50-rule ceiling |
| Automation | `/automatizacion` | 14 | 2026-08-06 | Schedules built, history, time zone, re-anchoring | API keys, webhooks, pausing/deleting a schedule |
| Suppliers | `/proveedores` | 6 | 2026-08-06 (API only) | The learned/unusable lead-time fields | The screen itself; creating and editing a supplier; the scorecard |
| Impact | `/impacto` | 0 | 2026-08-10 | **The four headline numbers reconciled against the database one by one** (see below); the monthly summary; the evolution table; empty states | The tutorial, navigation links, a month with real freed capital (needs two consecutive monthly measurements) |
| Integrations | `/integraciones` | 3 | 2026-08-10 (the wall only) | **Verified against the code that what it promises exists**: real Alegra/Siigo connectors, encrypted credentials, and `run_daily_integration_syncs` running from the worker's daily loop. The plan wall was removed on 2026-08-16 | **The entire screen**: connecting, testing the connection, syncing, seeing credential errors. None of the real flow has been walked, and the screen is still out of the menu |
| Messages | `/mensajes` | 4 | 2026-08-10 | Conversation list, opening one (**marks read for real in the database**), sending — the message lands in `direct_messages` with accents, an em dash and € intact, and stays unread for the recipient | Finding a person, starting a new conversation, long messages, attachments if they exist |
| Sign-up | `/signup` | 2 | 2026-08-09 | Full registration (tenant + admin), refusal on a duplicate WhatsApp number without stranding rows, an honest notice when the email cannot be sent | Duplicate email, password validations one by one, resending verification |
| Forgot password | `/forgot-password` | 4 | 2026-08-10 | All **3 steps**: unknown email (does not leak whether the account exists), wrong code, valid code, short password, passwords that do not match, successful change and redirect. Verified in the database: OTP burned (`used=t`), refresh revoked, old password rejected, new one accepted | Resending the code ("Try again"), expired OTP, attempt limit |
| History | `/historial` | 7 | 2026-08-09 | Failure reason on failed sessions; the full list with file, horizon, granularity and SKUs | Rename, delete, compare. **"Activate session" does not exist** — the active session is derived from the newest family plus the active period, it is not chosen; it was wrongly listed as a pending action |
| Supplier scorecard | `/proveedores/scorecard` | 0 | 2026-08-10 | The full table over real data (receptions, real vs declared lead time, trend, % on time, fill rate, purchased value) | A supplier whose deliveries actually measure something; sorting/filtering if it exists. The two missing nuances were **FIXED on 2026-08-10**; the 2026-08-11 sweep found **two more** figures with the same defect (`% on time` against a declared 15 that nobody declared, and `purchased value` as a confident ₡0) — see `stability.md` |
| Sign-in | `/login` | 3 | 2026-08-06 | Login with three accounts in different roles | Bad credentials, suspended account, cross-tab sign-out (verified by event, not with two real tabs) |
| Orders | `/pedidos` | 3 | 2026-08-09 | List with a generated PO (number, urgent lines, units, "In transit" state); recording a **partial** arrival — adds only what arrived and leaves the PO `partial` | Full arrival, a new manual order, sending an order, WhatsApp (open/copy/send to me), over-receiving |
| Reset password | `/reset-password` | 2 | 2026-08-10 | With no token (it warns and **disables** the button — it does not offer what it cannot deliver); with a real token from the product itself: successful change, old password rejected, sessions cut; **replaying the same link** (see below) | Expired token, a token for another purpose, the emailed link — **which nobody sends today** |
| Verify email | `/verify-email` | 1 | 2026-08-09 | A valid token activates the account and enables login | Expired token, already-used token, tampered token |
| Public API | `/api` | 8 | 2026-08-11 | **New.** Renders in ES and EN with no raw keys; token pasted; `GET /planning` actually executed (200 in 177 ms, formatted JSON); **an invalid key → 401 on screen without ending the session**; a write confirmation that names the consequence | Running a write all the way through (it mutates real data), file upload, `train`, narrow view |
| Set up inventory | `/configurar-inventario` | 0 | 2026-08-10 | The list prioritised by money; saving a complete row (**"12,50" is stored as 12.5**, as the copy promises); the progress bar and its recomputation; the central promise verified end to end — the configured product enters the semáforo (`PEDIR_PRONTO`) and the other stays `SIN_DATOS` | Uploading a file ("Choose file"), saving incomplete rows, the tutorial, the large-catalogue case |

**Honest summary (2026-08-11):** **all 26 screens have had some walk.** None is
walked end to end — the "what is missing" column is still the honest part of the
table, and an empty cell there means unknown, not correct.

### Regression sweep of 2026-08-10

After a day of changes touching every request (two in authentication),
**21 routes were walked in a row** with an admin session: all render, **zero
console errors, zero raw i18n keys on screen**. And it was re-verified as a real
viewer that the `/compras` gates still hold after touching auth: zero write
controls, zero action buttons, the role notice present and the reads intact.

**`/asistente`, the last one outstanding.** With no LLM behind it, the live
route answers **in Spanish and labelled as an error**: "The assistant took too
long to answer and the query was cut. Your question is still here — try again,
or make it shorter." That is fine.

The saved thread does contain raw English answers ("The AI service is
temporarily unavailable…") and a couple of questions with no answer at all.
**Not a live defect:** that sentence no longer exists in the code. They are
fossils of an earlier version, and history is not rewritten. Noted so nobody
chases it on seeing it.

Discarded on checking, so nobody chases it: it looked as if two cards on
`/planes` said "Your current plan". That was my own reading of the flattened
text — both occurrences live inside the same, correct card.

What WAS covered end to end on 2026-08-09 is **the chain that produces the
money**, with a fresh tenant and its own data: sign up → verify email → sign in
→ upload sales (a cp1252 file with `;` and dd/mm/yyyy dates) → train → semáforo
→ record stock → approve a recommendation → generate a PO → record a partial
arrival → stock updated by what arrived, not by what was ordered. Zero console
errors, zero 500s and zero FK violations across the whole route.

## Permissions: what was tested with a real viewer (2026-08-09)

A `read only` user was created from the screen, activated, and signed in as.
**The backend holds**: `PUT /inventory/stock/{sku}`, `POST /users`,
`DELETE /tenant` and `POST /inventory/log-po` all return 403, and it was
verified in the database that **nothing changed** — stock stayed at 40/406/300,
the user somebody tried to sneak in was not created, and the tenant is alive.
The sidebar does not show them "Users" either. That part is fine.

What is **not** fine is what the user sees before and after the refusal:

1. `/inventario` offered a viewer the entire write toolbar — "Update stock",
   "Record shrinkage", "Dead stock", "Add warehouse", "Import CSV" — let them
   open the editor, type a value and reach a green, enabled button reading "Save
   1 change". The refusal arrived only on save.
2. The refusal notice **said something other than what happened**: the title was
   "You do not have permission to see this" when it was a write, contradicting
   its own body ("Your role cannot do this"). The user was seeing it perfectly
   well; what they could not do was save.
3. The second notice, "Incomplete save — check and try again", promised a way
   out that does not exist: retrying will never work, because it is not bad data
   but a permission. That sends the user to repeat something useless.

None is a security hole — the backend does not yield. They are copy lies and an
editing surface offered to somebody who cannot use it.

**All three fixed on 2026-08-09**: `states.err_permission_title` stopped saying
"see" and says "Your role does not allow this action"; the stock save
distinguishes a 403 from bad data and in that case says retrying will not help;
and `/inventario` no longer offers a viewer the editor, "Record shrinkage", "Add
warehouse" or the CSV import (which is a write, even though the button only says
"CSV"). Reads — template, exports, PDF — remain for everybody, and the admin
keeps everything, verified by signing in as both roles.

## `/archivos`: the list lied after saving (2026-08-09, fixed 2026-08-10)

Using "Save as new" in the editor, the source **is** created — it is in the
database, on disk, and it opens in the right-hand panel — but the sidebar **did
not refresh**: it still said "1 SOURCE" and listed only the original. Pressing
refresh showed both. Nothing is lost; what fails is that the screen asserts a
number that is not true, right after a successful action.

**Cause, found 2026-08-10:** the same screen has two paths that create a source
and only one was wired correctly. The SQL materialisation reports through
`onDatasetCreated`, which adds it to the list; the spreadsheet editor reported
through `onUpdated`, which walks the list looking for the id **and finds
nothing**, because the id is new. That is why it selected the copy on the right
without adding it on the left. The editor now uses the same channel as the SQL
path. Walked: the bar goes from "4 SOURCES" to "5 SOURCES" with the copy on top,
without pressing refresh, and the copy is in `datasets` with its 360 rows.

Discarded on checking, so nobody chases it: the counter **does** pluralise
("2 SOURCES"). It looked broken because of a partial regex match of mine, not
because of the product.

**What was still open, found while verifying the above:** `/compras` had the
same pattern — it showed "Approve" and "Reject" to a viewer, and on generating
the order `POST /log-po` returned 403. There it **did** warn ("You have the CSV,
but we could not record the order…"), so it was not silent; but it closed with
"Generate it again", which for a viewer is the same empty promise already
corrected in inventory.

## `/compras`: what was closed on 2026-08-10

Gating "Approve" and "Reject" was not enough. The cart fills from **two**
states, `approved` and `modified`, and two controls that looked decorative set
`modified` on their own: changing the quantity and pointing the line at another
supplier. Leaving either one, a viewer could raise the green "Download purchase
order" bar again without having approved anything. That is why the gate covers
the quantity and the supplier selector as well as the two buttons.

Gated by role: approve/reject/undo/restore, the quantity, the supplier selector,
"Download purchase order", the optimiser's "Convert to PO", "Record arrival" on
overdue orders and "Create transfer". The overdue notice, the optimiser's plan,
the reason behind each recommendation and every number stay visible: what is
removed is the decision, not the reading. Where the buttons were, the viewer
reads "Your role cannot generate orders", so the absence does not look like a
broken screen.

`downloadOC` now distinguishes a 403 from the rest too: when the refusal was the
role, it stops saying "Generate it again" and uses the permissions notice. It is
still reachable with the buttons hidden, because the role is read from a local
copy that goes stale the moment an admin demotes the user from another session.

Walked with both accounts and at both widths: the viewer sees none of those
controls (zero `input`, zero `select` on the screen) and does see the SKUs, the
reason and the plan; the admin sees them all, and a real order generated from
the screen landed in `inventory_po_log` with 348 units, ₡417,600 and
`approved_count = 1`. At narrow width the viewer loses the stepper and "Add to
order"; the admin keeps them. Zero console errors.

**"Create transfer", walked the same day:** the component was not drawing
because the active session carried no transfer suggestions. The real condition
was provoked — leaving a surplus of SKU-A in Bodega Cartago while principal sits
at zero — and the briefing produced it by itself. The viewer reads the full
suggestion (move 226 from Bodega Cartago, the coverage the donor is left with,
and why transferring beats buying) **without** the button; the admin has it, and
pressing it left the Cartago → principal row in `inventory_transfer_log`,
`in_transit`, with 226 units of SKU-A. Box closed.

Two boxes the table listed as pending that **do not exist as an action**:
"activate session" on `/historial`, and account deletion — `DELETE /tenant` and
`/tenant/export` have no screen, they are API only, so there is no way to walk
them and they are covered by tests alone.

## `/pronosticos`: four findings from the first walk (2026-08-10)

A real session, 2 SKUs, 180 daily points. The first three were variants of the
**same underlying defect**: the screen showed numbers from three different
models without saying they were different. All four were closed, and so was the
English text at the end.

**Observation, not a defect:** on SKU-B the `naive` baseline wins on cost (8.69)
against every trained model (best: 8.94). The code excludes baselines on purpose
when choosing what to buy, and the engine already warns in the log ("no model
beat a naive baseline on cost_horizon"). That the user is not told is a product
decision, not an arithmetic error.

For SKU-A, the screen's own metrics table says:

| Model | cost | MAE | WAPE |
|---|---:|---:|---:|
| Model 2 (xgboost) | **14.60** | 10.04 | 24.6% |
| Model 1 (lightgbm) | 16.80 | 8.79 | 21.5% |
| Model 3 (prophet) | 18.32 | 17.55 | **45.7%** |
| Model 9 (global_lgbm) | 20.88 | **8.61** | **17.9%** |

The champion is chosen by **asymmetric cost** — a correct and well-documented
decision: running short costs more than running over — so Model 2 wins. But:

1. **`Best WAPE` was not the best WAPE. FIXED.** The card labelled "Best WAPE:
   24.6%" directly above a table containing 17.9% and 21.5%. The value is right
   (it is the error of the forecast the purchases come from); the label asserted
   a false superlative. It now says "WAPE of the chosen model".

2. **The chart drew a model that was not the champion. FIXED.** In
   `backend/api/v1/forecasts.py:479` the model served by default is
   `next(iter(sku_forecasts.keys()))` — **the first one in the dictionary**. For
   SKU-A that is Model 3, WAPE 45.7%, while the purchase order is computed with
   Model 2 (24.6%). The buyer looks at one model's curve and orders by another,
   and nothing on screen says so: the caption says "Model: Model 3" and the card
   next to it "Best model: Model 2". Reproduced in another session and another
   SKU (panel B: serves Model 3, champion Model 9), so it is systematic, not a
   one-off. It is exactly the drift the comment at
   `backend/inventory/service.py:3037` claims to have closed once between
   engine, semáforo and accuracy: it was still alive in the chart. The endpoint
   now delegates to `best_model_by_sku` — the same authority — and falls back to
   the first available model only when the champion has no stored series.
   Verified across 6 SKUs in 3 sessions: served == champion in all of them, and
   `avail=0` does not blow up.

3. **The list card announced anybody's best MAE. FIXED.** `page.tsx:421` took
   the lowest MAE of **all** rows, without excluding baselines. For SKU-A it
   showed "MAE 8.61", which belongs to Model 9, not to the chosen one (10.04).
   With other data it could have announced a baseline's MAE — exactly what the
   rest of the code excludes on purpose because "they exist to be beaten". It
   now uses the same champion as the statistics strip: SKU-A shows 10.04 and
   SKU-B 5.37, both from the model that buys.

4. **The same screen said 5 outliers and 0 outliers. FIXED.** Not two
   implementations but two **thresholds**: the engine uses a 3×IQR fence
   (`outlier_iqr_factor = 3.0`) and the frontend used 1.5×IQR, the textbook
   "mild outlier". The engine rules — its count is what feeds the quality score
   and the warnings — so the frontend adopted its factor. It publishes only a
   **count**, never positions, so the markers are still placed in the frontend
   and that is why the constant has to keep matching the engine's; the comment
   says so. Verified: the caption no longer reports outliers for SKU-A and the
   Quality tab still says 0.
   **Walked in full on 2026-08-10.** No series in the tenant crossed 3× at any
   granularity, so a session was trained with a deliberate spike ("Outlier
   demo": 150 days, one SKU selling ~40 and one day 400). The engine counts 1,
   the caption says "1 outlier detected", the Quality tab says 1, and the amber
   point is drawn on the spike. The two definitions agree over real data, in the
   positive case and the negative one.

   It was measured in passing that the contradiction was **systematic**, not
   anecdotal: with the old fence the frontend marked points in 8 of 12 series
   while the engine reported 0 in all 12.

5. **The Quality tab said "1 outliers". FIXED.** Found while creating that data:
   the per-SKU warnings are English sentences the engine assembles by hand
   (`quality.py`: `f"{outliers} outliers"`, `f"{missing} missing dates"`,
   `"Only {n} rows (min={min})"`, `"Intermittent: {pct} zeros"`) and the screen
   printed them verbatim, next to translated labels. They are now rebuilt from
   the fields the engine **already publishes** — its own outlier count, its own
   missing-date count, its `has_min_history`, its `series_flags` — without
   inventing a single threshold: re-deriving "is it intermittent?" from
   `zero_ratio` and a guessed cut would have recreated exactly the 1.5-vs-3.0
   split that had just been repaired. Anything the engine warns about that is
   not modelled still appears, in English, rather than disappearing.

   The block was **duplicated** in the file and the first fix touched the copy
   nobody sees; walking it is what exposed that. Verified in both languages and
   both cases: SPIKE-01 says "1 sale(s) far outside the normal range…" in both,
   CALM-02 says "Clean series".

   The `QualityReport` type declared 7 of the 12 fields the API sends; the other
   5 had always been there, just invisible to the frontend — which is why this
   screen ended up reprinting the engine's sentences instead of rebuilding them.

**Also, not a number but a language lie. FIXED.** "See detail" printed the
engine's raw English text to a Spanish user — `SKU 'SKU-A' / model 'croston':
Croston is designed for intermittent series (zero_ratio=0% < 20%)` — and named
the algorithm that **this very screen hides on purpose** behind "Model N".

The structure to fix it already existed: each sample carries `code` + `context`,
and the corrections block in that same panel already used the code → i18n →
fallback pattern. The samples now use it too, in three steps: the
`runwarn.<CODE>.sample` template, then the neutral line from `context`, and only
then the engine's English. `modelLabel` moved to `lib/modelLabel.ts` so the
panel uses **the same** numbering as the chart — a second copy of the fix would
have been a second numbering, which is exactly what that mapping prevents.

One case could not be translated without touching the engine: `UNSORTED_DATES`
sent `context: {}` and the column name lived only inside the English sentence.
`leakage.py` now passes `context={"column": dt_col}` — additive, the message
does not change. Walked in both languages, with zero engine prose and zero
algorithm names.

**Discarded on checking:** the names "Model 1..9" look arbitrary — they reach 9
with only 5 buttons — but they are a fixed positional mapping (`MODEL_ORDER`),
stable across SKUs, exports and reloads. It is deliberate and documented. Do not
chase it.

## `/impacto`: the numbers hold, two sentences point at the wrong month (2026-08-10)

First walk. This screen has almost no buttons: it is pure derived number, which
is exactly where a false figure makes no noise. **All four headline numbers
reconcile exactly** against `inventory_po_log`:

| On screen | Where it comes from | Matches |
|---|---|---|
| 4 orders generated | 4 rows | yes |
| ₡421,058 managed | 3006 + 425 + 27 + 417,600 | exact |
| 2 risks handled | sum of `skus_order_now` | yes |
| 3 of 3 recommendations (100%) | `approved_count` / `suggested_count` | yes |

**FIXED — the monthly summary said "this month" about another one.** The card
always covers the **closed** month (the same period as the monthly email, a
deliberate decision documented at `inventory.py:1307`), so on 10 August the
heading said "Summary of July 2026" and the body "we recorded no purchase order
**this month**" — while the evolution table, five centimetres below, listed
August with 4 orders. Both sentences now name the month.

**FIXED — "5 days active" were not active days.** `roi_service.py` computed
`(last order − first order).days`, i.e. the **span** between the first and last
order. It gave 5 and looked right, but a tenant who ordered once and came back a
year later would have read "365 days active" after using Faro on two days. It is
`COUNT(DISTINCT generated_at::date)` now, which cannot overstate: it is bounded
by the days the buyer showed up. On screen it went from 5 to **2**, which is
exactly what the database says (4 and 10 August). With a test named after the
failure (`test_roi_active_days.py`) and a mutation gate: restoring the old
calculation turns two of the three red.

## `/configurar-inventario`: a sentence that becomes false as you progress (2026-08-10)

First walk, with the 2-product "Outlier demo" session. The substance holds: the
list prioritises by money, saving a row writes it exactly into the database
(`250`, `12.5`, `9`) — including the **"12,50" with a comma**, which is what the
copy promises to read — and the central promise is kept end to end: the
configured product appears in the semáforo with a real signal and the other
stays `SIN_DATOS`.

**FIXED — the heading became false exactly as you progressed.** It said "With
{n} of your {total} products you cover {pct}% of this month's purchase". With
nothing configured it was true by coincidence (2 of 2 = 100%). After configuring
the first it read "With **1** of your 2 products you cover **100%** of this
month's purchase" — while the bar two lines below said "65% already configured"
and the remaining product was worth 34.8%.

The backend's calculation is right: `cumulative_pct` includes what is already
covered, so the 100% is the total you would reach. What failed was the wording,
which presented it as the coverage of a subset. Now: "**Completing** 1 of your 2
products **gets you to** 100% of this month's purchase". Both variants (money
and units), in both catalogues — `translations.ts` and the `i18n/stockSetup.ts`
fallback, which have to be touched together or the fallback revives the old
text.

**Observation, not a defect:** the copy says "while they are missing, that
product does not appear in the semáforo", and in fact it **does** appear, marked
`SIN_DATOS`. That is better — the product is not hidden, it is declared
unmeasured, which is the line the whole product takes — but the sentence
promises otherwise. Not touched.

## Forgot password: "all sessions have been revoked" was not true (2026-08-10)

All 3 steps walked with a real account. **Almost everything holds**, including
the unhappy paths: a non-existent email proceeds anyway (it does not leak
whether the account exists — deliberate and documented in `auth.py`), a wrong
code is refused without hints, a short password and mismatched passwords are
stopped on the client. Verified in the database after the change: the OTP was
`used=t`, the refresh token returns 401, the old password returns 401 and the
new one works.

**What was not true.** `POST /auth/reset-password` answered:

> `"Password updated. All sessions have been revoked."`

`update_password` deletes the **refresh tokens** and nothing else. Measured:
with a session open before the change, after the reset the **access token still
returned 200** while its refresh returned 401. So: the session cannot be
*renewed*, but whoever already holds an access token keeps full access —
including writing — until it expires (15 minutes). For the case that motivates a
reset ("I think somebody got in"), those minutes are exactly the ones that
matter.

**FIXED the same day, with the owner's agreement.** `/logout` could revoke the
access token because it is an *authenticated* request: it has the `jti` in hand
for the blacklist `guards.py` consults. A reset is an **unauthenticated** flow
and never sees the intruder's token, so the cut is expressed by **user and
date**: `users.sessions_invalid_before` (a new, additive column created by the
migration at boot) against the token's `iat`, which tokens now carry.

A token with no `iat` — those issued before this change — cannot prove when it
was made, so it is rejected; but **only** in accounts that actually cut.
Somebody who never changed their password has `NULL` and never reaches that
branch, so their old tokens keep working.

**The part that nearly went wrong, and which the suite caught.** The first
version truncated both sides to the second, so that a login made in the same
second as the reset would not read as older than the cut and lock the user out
of the account they had just recovered. A test caught that. But running the full
selection exposed the mirror: **under load, the token from before the reset also
fell in that same second, and survived the password change**. One second is not
enough to separate "issued just before" from "issued just after". Microseconds
are, so neither side rounds. That failure **only appeared in the big run**,
never in isolation.

Walked against the real server after restarting: session open → reset → the
previous access token returns **401** (it used to return 200) and an immediate
login returns **200**, with no lockout. The endpoint can say "All sessions have
been signed out" again because it is now true. Five tests named after the
failure, with a mutation gate (commenting out the guard call turns two red), and
222 tests in the auth neighbourhood green.

## `/reset-password`: the link worked twice (2026-08-10)

Walked with a real token issued by the product itself. What holds: with no token
the screen warns **and disables** the button — it does not offer what it cannot
deliver, the opposite of the pattern that had to be corrected on `/inventario`;
with a valid token it changes the password, rejects the previous one and cuts
the sessions.

**FIXED — the same link changed the password twice.** The OTP is burned
(`pw_change_codes.used`), but the token received in exchange was a signed JWT
with nothing marking it spent: it stayed valid for its 15 minutes. Reproduced
against the server: after a complete reset, **replaying the same token returned
200** and left the password as something else, locking out the owner who had
just recovered the account.

It weighs more than an ordinary replay because **that token travels in the URL**
of this screen: it survives in browser history, on a shared screen, in any
proxy's logs.

The fix needed no new machinery: the token already carries a `jti` and the
blacklist `/logout` uses is exactly the right store. It is burned **after** a
successful change, never before — a password rejected as too weak must leave the
link usable, or the first typo costs the user their only attempt. Verified live:
first use 200, replay refused with `reset_token_invalid` (which does have
Spanish copy), the owner's password works and the replay's does not.

**Noted, not a defect but worth knowing:** `send_password_reset_email` — the one
that sends a *link* — exists and has a test, but **nobody calls it**. The
product sends a 6-digit code, not a link. So this screen is today only reachable
with a token no email produces; the endpoint behind it, on the other hand, is
what step 3 of `/forgot-password` uses and is very much alive.

## Landing: the specific claims hold, the hero figure does not (2026-08-10)

What is striking is that **the concrete promises hold**. The landing describes
lead-time learning in detail specific enough to falsify:

> "On the third reception from that supplier it stops using the lead time you
> wrote and starts using the observed average — and it tells you which of the
> two it is applying. This comes with every plan."

`MIN_LEAD_TIME_OBSERVATIONS = 3` — the third reception, exactly. And there is no
plan gate on it, so "every plan" is true. The CTAs point at `/signup?demo=1`, a
flag the sign-up screen does read and which wires into the `demo_quickstart`
endpoint. None of that is smoke.

**What is worth looking at: "94% average forecast accuracy".** That week the
product showed its own user 75.1%, 75.2% and 89% in real sessions. The hero's
figure is not the one the application displays. It may come from a benchmark or
be a legitimate aspiration — I do not know — but today a buyer who arrives
through the landing and reaches `/pronosticos` sees two different numbers about
the same thing.

The other three hero figures (−75% of the time, 50K+ SKUs per instance, 1-day
implementation) have nothing inside the repository to check them against.

**Not touched.** A marketing figure is a business decision — and potentially a
legal commitment — not a defect for me to correct on my own. Recorded with the
measured evidence.

## Suppliers and their scorecard: the same data, with and without the nuance (2026-08-10)

`/proveedores` has the best column of copy in the product. It explains **why** it
has not learned yet, supplier by supplier:

> "I recorded 3 deliveries, but they all arrived the same day you ordered them,
> so they say nothing about this supplier's lead time. I am still using the 10
> days you configured."

That is exactly the landing's promise, kept, and protected against degenerate
data as well: it has its 3 receptions and still refuses to learn from them
because they measure nothing.

**FIXED the same day — the scorecard presented those same numbers flat.** For
that supplier it showed `REAL LEAD TIME 0d` next to `DECLARED 10d`, without a
word of the nuance the other screen had just given. A buyer who lands directly
on the scorecard concludes Andina delivers same-day and lowers their lead time —
precisely the wrong decision `/proveedores` works to prevent. The `% ON TIME
100%` comes from the same place: zero-day deliveries are trivially punctual.

And a second one: **`TREND: Stable` computed over ONE reception** (Granos del
Valle). A trend needs at least two points; with one, the honest word is "not
yet".

It is the same family as the 5-vs-0 outliers on `/pronosticos`: two surfaces
over the same data, one with a criterion and the other without.

**How it ended** (option chosen by the owner: reuse the sentence that already
exists). The backend exposes two new flags on the scorecard, computed with **the
same rule** as `supplier_service` — deliberately: a second definition of
"usable" is exactly how two screens start contradicting each other:

- `lead_time_unusable` (≥3 receptions and an average ≤ 0) → the cell says "Not
  conclusive" and the tooltip is **literally** the `/proveedores` sentence.
- `trend_measurable` (≥2 receptions) → with only one, the trend says "Not yet"
  instead of "Stable", with its explanation. The code comment said the absence
  of an alert means "within its normal range, not a lack of data" — true only
  once there *is* a range.

Walked with the real data that exposed it: Andina went from `0d` to "Not
conclusive"; Granos del Valle from "Stable" to "Not yet".

**Residue recorded, not buried:** with **fewer than 3** receptions the column
can still show `0d` (Granos del Valle, n=1). Widening the rule here would have
created exactly the second definition the comment warns against; doing it
properly means changing the shared threshold on both sides, and that is a
product decision, not a local fix.

## Why the suite does not replace this

On 2026-08-06 the backend suite passed **2,407 tests green while 27 defects were
live** — among them that signing out did not sign you out, and that a LatAm file
lost 60% of its rows.

It was not that the tests lied: breaking the code deliberately at three critical
points turned all three red. The problem is that **none of them pointed at those
behaviours**. They were all written to confirm that a function does what its
author intended; the defect lived precisely in what the author believed.

The only tests that found anything in that batch are named after a failure, not
after a function: `test_notification_delivery_honesty`, `test_stock_seeding_zero`,
`test_transfer_rejection_reason`.

## The rules that follow

1. **A test is born from a failure, not from a function.** You do not write a
   test because an endpoint exists. You write it because a lie visible to the
   user was observed — or is suspected with reason.
2. **Mutation gate.** No new test lands without showing that it goes red when
   its target is broken. Break the line it watches, run it, it has to fail. It
   costs seconds and turns "the test passes" into evidence.
3. **Discovery comes from walking, not from the suite.** The suite defends what
   is already known; it finds nothing new. This table is the map of discovery.
4. **No silent ceilings.** If a pass covers only part of a screen, it is
   recorded under "what is missing". An empty cell means unknown, never correct.

## Update 2026-08-23

- **`Planes` was removed from the table.** The screen does not exist: it went
  with the tiers on 2026-08-16, and what replaces it is the "Usage and limits"
  panel inside `Mi cuenta`, already listed here.
- **The AI assistant no longer depends on Ollama or an Anthropic key.** The only
  backend is DeepSeek (`DEEPSEEK_API_KEY`); with no key the AI features fail out
  loud instead of answering from another provider.
- **Walked today, with the demo tenant seeded**: all 16 menu screens, to capture
  the landing's route. Five defects came out, all fixed and recorded in
  `stability.md`: the chat died because it depended on Pinecone; it read the
  training result instead of the semáforo and claimed 100% of the catalogue was
  at risk; it was hanging off the wrong session; the API-keys panel still
  offered to copy a credential that had just been revoked; and the API page
  promised a limit that stopped being true when the free plan appeared.
