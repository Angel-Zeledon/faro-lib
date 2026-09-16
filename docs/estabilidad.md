# Stability — what is left before the app has no bugs

**Created:** 2026-08-11
**Rule that governs it:** CLAUDE.md, "Priority: stability over scope". Nothing
here is a feature. If fixing something on this list **needs** a new capability —
an endpoint, a field, a screen, a toggle — it is flagged and asked about before
being built.

This document replaces seven plan, audit and proposal documents that had gone
stale (see "What was deleted" at the end). It is the only live backlog. The
living table is still `screen-inventory.md`; this is the order the work runs in.

---

## First: what "no bugs" cannot mean

**The frontend has not a single test.** `npx tsc --noEmit` checks types, not
behaviour, and there is no end-to-end suite. On 2026-08-06 the suite was green
with 27 live defects in the application.

So "no bugs at all" is not a state anybody can certify. What is reachable, and
what this list pursues, is:

> **Every path a user can take, exercised at least once by a person in a
> browser, and whatever comes out of it fixed.**

And the fact that sets the priority is not an intuition: **every time a screen
was walked seriously, defects appeared.** `/pronosticos` gave 4, `/compras` 1,
`/archivos` 1, `/usuarios` 1, and `/api` gave 6 from a close look alone. There is
no reason left to assume the never-exercised actions are healthy.

---

## 1. The bug that used to head this list — **[FIXED bd38436]**

### `ConfirmDialog` lost the first confirmation's promise — **[FIXED bd38436]**

> Verified in the code on 2026-08-23: a second `confirm()` now calls
> `settle(false)` on the pending one instead of overwriting the resolver, there
> is an `Escape` handler in the capture phase, and focus is trapped inside the
> panel. The three defects that made it reachable are closed.

`Frontend/src/components/ui/ConfirmDialog.tsx:43` kept **one** `resolver` in a
`useRef`. Opening a second confirmation before closing the first made the second
**overwrite** the first's resolver: that promise never resolves. The original
action hangs forever — no message, no spinner, no error. It fails on the safe
side (it does not over-write), but it fails **invisibly**, which is the worst way
to fail.

Two defects in the same component made it reachable rather than theoretical:

- **It did not close on `Escape`.** There was no keyboard handler.
- **It did not trap focus**, and the page behind was not inert. With a keyboard
  you tab out of the "modal" and reach the buttons below — which is exactly how
  the second confirmation gets triggered.

**6 screens** use it, including the delete ones and the purchase-order one. Found
while reviewing `/api`, but it is not an `/api` problem.

**This is the first thing to fix.**

---

## 1.bis Six defects the chaos suite found (2026-08-22)

> **State at the close of 2026-08-22: all six fixed.**
> Each verified by the test that found it. (a) plan ceilings, (b) the rate
> limiter, (c) a NUL in the URL, (d) a blank SKU, (e) `sniff_separator`, and (f)
> pandas truncating silently — a file with a NUL is now **refused** in
> `dataframes/io.py` (binary formats exempt: an .xlsx is a ZIP and is full of
> NULs), and the stock importer, which does have per-row reporting, refuses the
> individual row with `inventory_import_row_has_nul`.
>
> **The `limit_guard` lock now covers every route**: stock, user creation,
> invitation, sessions, API keys, warehouses, bulk import, transfers, PO
> reception and integration sync. The ones that already had their own transaction
> use `take_tenant_lock(tenant_id, conn)` inside it — one connection, and the
> lock is released by the same commit that makes the rows visible.

Three hostile test files were added — massive volume, corrupt data and real
concurrency — which did not exist until then:
`backend/tests/test_chaos_ingestion.py`, `test_chaos_evil_path.py` and
`test_chaos_concurrency.py`. They found this **on the first run**. The tests were
left **deliberately red**: none uses `xfail`, because the house rule forbids
hiding a real bug behind a marker.

Ordered by what they cost if they reach production:

### a) Plan ceilings could be walked through with two simultaneous clicks — **[FIXED 2026-08-22]**

`enforce_limit` is a `SELECT COUNT(*)` followed by an `INSERT`, with nothing
atomic in between. Measured:

| Limit | Concurrency | Ceiling | Rows left behind |
|---|---|---|---|
| `max_skus` | 12 requests | 5 | **10** |
| `max_users` | 8 requests | 2 | **9** |

This is not a performance annoyance: on the free plan **it is the product's
entire commercial boundary**. A bulk import in one tab and a manual save in
another are enough. Fixing it needs a design decision (a partial unique index, an
`INSERT … SELECT` with the condition inside, or a per-tenant advisory lock),
which is why it is asked about before being touched.
*Test:* `test_a_plan_ceiling_cannot_be_walked_through_by_clicking_twice`.

### b) The API-key rate limiter allowed 3× its ceiling under parallelism — **[FIXED 2026-08-22]**

Same pattern (read the counter, insert afterwards): 20 simultaneous calls against
a ceiling of 5 let **16** through. A machine credential — the only one that runs
unattended, on a cron, with retries — can multiply its quota by opening sockets.
It also affects the per-minute ceiling, which is older than the tiers.
*Test:* `test_the_rate_limiter_counts_exactly_under_parallel_hammering`.

### c) A NUL in the URL was reported as a server failure — **[FIXED 2026-08-22]**

`GET /api/v1/sessions/%00x/results` → the byte travels intact to psycopg2, which
refuses it, and the exception handler turns it into a **500 `internal_error`**.
The envelope exists (that day's fix works), but the verdict is the wrong one: the
user is told the server broke over a URL they malformed, and whoever is on call
gets woken up. It should be 400/404, validated where the id is read.
*Test:* `test_a_nul_byte_in_a_path_is_the_callers_mistake_not_the_servers`.

### d) A SKU of nothing but spaces created an invisible row — **[FIXED 2026-08-22]**

`PUT /api/v1/inventory/stock/%20%20%20` answers 200 and leaves a row whose SKU is
`"   "`. Nothing shows on screen: an inventory row nobody can find or delete from
the UI.
*Test:* `test_an_empty_sku_cannot_create_a_nameless_row`.

### e) `sniff_separator` blew up with `IndexError` on a BOM-only file — **[FIXED 2026-08-22]**

`backend/dataframes/io.py:32`. Excel writes a BOM into an empty export;
`sample.strip()` does not treat it as whitespace, `lstrip` of the BOM leaves the
string empty, and `splitlines()[0]` explodes. Uploading an empty export = a 500
"unexpected error". It is a one-line guard.
*Test:* `test_separator_sniffing_survives_files_designed_to_fool_it`.

### f) pandas truncated the cell at the NUL, silently — **[FIXED 2026-08-22]**

`SKU-\0-1` enters the database as `SKU-`. No error, no warning: the file says one
thing and inventory stores another. A buyer ends up ordering against a SKU their
supplier does not recognise. It has to be detected on read and the row refused,
not let through changed.
*Test:* `test_a_nul_byte_is_carried_or_refused_but_never_silently_dropped`.

### And one measurement, which is not a bug but looks like one

Previewing a 200,000-row `.xlsx` took **139.8 s** and ~700 MB to return 20 rows;
the same data as CSV takes under a second. The Excel branch of `dataset_preview`
reads the whole sheet before slicing. It runs on the request thread: a single
upload pins a worker for more than two minutes.
*Test:* `test_an_excel_bomb_does_not_take_the_process_with_it` (with the
threshold set as a regression guard, not as a blessing).

### What was already fixed, because the same change introduced it

The same race as (a) was in `POST /entitlements/upgrade-request`, written that
same day: 6 simultaneous clicks left 4 open requests. It was closed with a
partial unique index on `(tenant_id) WHERE status = 'new'` and an
`ON CONFLICT … DO UPDATE`, which is exactly the shape (a) needs.

---

## 1.ter Finding from the browser walk (2026-08-22) — FIXED

**The purchase panel's executive summary reported euros in a colón tenant.** The
KPI on the same screen says `₡196K` and the paragraph generated right below it
says "Total inventory amounts to 195,755.6 €". The tenant's currency is CRC
(`/mi-cuenta` confirms it).

It is the AI narrator: it receives the figures but not the tenant's currency, or
does not respect it in the prompt. Two different symbols for the same number, ten
pixels apart — the user does not know which to believe, and the wrong one is the
one in prose, which is the one read first.

Seen on the walk with the demo tenant seeded; it was not a defect of the
synthetic data.

**Cause:** the amounts entered the prompt as bare numbers
(`"total_inventory_value": 195755.6`), with no currency. The model was not
careless — it had nothing to go on: it had to choose a symbol and it chose one.
`key_points` and the rule-based fallback already went through `money()`; only the
LLM branch did not.

**Fix:** amounts go pre-formatted into the prompt, with an explicit instruction
to copy them verbatim. Same change in `generate_inventory_insight`. `_as_money` /
`_has_money` were added because the fallback and the key points read the SAME
dicts and now receive text where there used to be numbers — a `> 0` over that
blew up (the test itself caught it).

**Verified:** in the browser, 0 euros and 20 colones on screen; the paragraph
says `₡195,756`, identical to the KPI above. Regression test:
`test_the_prompt_itself_carries_the_currency_not_a_bare_number`, which captures
the real prompt and fails if a bare number arrives again (confirmed to fail
without the fix).

---

## 1.quater The last three from the agent report — **[FIXED 2026-08-23]**

The three the agents reported without fixing, because they fell outside their
files.

**`/cash-calendar/fit` discarded `result.status`.** The optimiser degrades to a
greedy shortcut when the solver cannot cope, and says so — but this endpoint
threw that signal away, so a cash answer built on the shortcut arrived with the
same confidence as one built on the optimum. The number was not wrong; the plan
it describes was a different one, and nothing on the wire said so. It now sends
`plan_status`, and **only** on the path that solves: somebody who sent their own
cart is being answered about their cart, and there is no plan to qualify.

**The approximate-plan notice was not drawn when the plan came back empty.** The
whole section was conditioned on there being lines, so a greedy shortcut with no
results drew **nothing** — and the buyer read that silence as "there is nothing
to buy". The truth was "the optimiser gave up and we do not know", which is a
different sentence and the expensive one to get wrong. The condition now includes
`status === 'fallback'`, and that case has its own copy: the normal notice talks
about "this list" over a list that does not exist, which reads as reassurance
instead of a warning.

**`/proveedores` showed raw English in its banner.** It printed `e.message`,
which for an `AppError` is the fallback text the backend sends to clients with no
catalogue. This screen has a catalogue. The global toast already rendered
`errors.<code>` in the user's language, so the same failure read in Spanish in
the corner and in English in the panel — and the panel is the one attached to the
form in front of you.

---

## 1.quinquies What came out of capturing the app in English — **[FIXED 2026-08-23]**

Walking the eighteen screens again with the interface in English exposed five
defects that were invisible in Spanish, because in Spanish the wrong value
coincides with the right one.

**`/pedidos` dates came out in Spanish.** `POHistory.tsx` pinned the locale to
`'es'`, so a user reading an English screen saw `22 ago 2026`. It now follows the
interface language.

**Twenty-three figures were formatted with Spanish separators.** `1.234` on an
English screen is not a misaligned number: it is the same digits read as another
quantity. Threading `lang` through ten components was more noise than the bug, so
the locale lives in `lib/numberLocale.ts` and `LanguageProvider` keeps it current
— set **during** render, not in an effect, because children format on their first
paint.

**Two more dates followed the browser's language**, not the app's
(`/pronosticos` and `/historial` passed `undefined` as the locale). They matched
by accident as long as the browser was in Spanish.

**The fifteen column-mapping labels on `/ventas` were hardcoded Spanish.** It is
the one screen where getting a column wrong costs a whole training run, and it
was the one left untranslated. Twenty new keys, es/en.

**The assistant printed markdown `###`.** Its renderer understood bold and
bullets but not headings, so the model's answer arrived with the hashes showing.
It was visible in the very screenshot the landing showed visitors.

**In passing:** this file contained a literal NUL inside an example, which made
ripgrep classify the only live backlog as binary and skip it in every search. The
example now writes `\0`.

---

## 1.sexies The three short ones left from level 4 — **[FIXED 2026-08-23]**

**Two primary suppliers for the same SKU, and who won was whatever order
Postgres returned the rows in.** `sku_suppliers.is_primary` is `DEFAULT TRUE` and
nothing in the schema prevents two, so linking a second supplier without naming
the flag made it primary as well. From there, "who supplies this SKU" had no
answer: `get_primary_suppliers_map` built a dict over a `SELECT` with no
`ORDER BY` (the last row won) and `get_sku_suppliers` ordered by name, so the same
request could name one supplier in the list and build the recommendation for
another. Two halves: the **write** now unmarks the others in the same
transaction, and the **read** is ordered — the oldest primary wins — so rows that
already violate the invariant resolve the same way everywhere, with no data
migration. `get_primary_supplier` was deleted: it had **zero callers** and was a
`LIMIT 1` with no order, so the day somebody wired it up it would have answered
differently from the map, in the same request.

**Two holding-cost percentages in the same product.** `/inventario` costed dead
stock at a **hardcoded 25% a year** while the price-break panel and the MILP
optimiser costed the **same** warehousing at the tenant's `holding_cost_pct` (20%
by default). The buyer read "holding it costs you X a month" on one screen and
received purchasing advice built on a different cost of money on the other. Now
`/inventory/dead-stock` resolves the rate the same way `/price-breaks/evaluate`
does and **returns it**, because the caption that narrates it has to name the
number that was used: the figure came out of the sentence and became an i18n
parameter.

**What was NOT fixed, and why**: `sku_suppliers.lead_time_days / moq / unit_cost`
still reach no planning path. With a correction to the original finding: **no
screen shows them either** — the endpoint exists, the client exists in `api.ts`
and `types.ts`, and no component calls it; all of it landed in the initial commit
and the interface half was never built. Putting them into the cascade is **a new
precedence level** that changes the semáforo of any tenant holding those rows: an
owner's decision, not a fix.

---

## 1.septies `/inventario` had 26 controls before the first row — **[SIMPLIFIED 2026-08-23]**

Measured against the running app, not counting the sidebar: **26 controls and 7
colours** before the buyer reached a SKU. Five of the buttons were exports, two
were links to screens already in the sidebar, and three coloured alert lines
repeated word for word the KPI cards right below them. The owner put it better:
he did not understand his own screen.

What was done, without removing **any** function:

- The five exports live in a **Download** menu; CSV import, refresh and shrinkage
  in a **⋯**. The duplicated navigation was deleted.
- The three warehouse-configuration buttons came out of the tab row — where they
  were passing themselves off as warehouses — and sit behind a gear.
- The three alert lines merged into their card: the sentence became the subtitle
  of the number it describes.
- `components/ui/MenuButton.tsx` was extracted rather than writing a third
  dropdown by hand — `/pronosticos` has its own copy and can adopt it.

Result: **from 26 to 16 controls**, and from 14 loose actions to 4.

**A defect introduced and caught in the browser, on the same pass:** on removing
the three lines, the guard for the message "All inventory is well covered" was
`!lines.length` — true **always**, once the lines were gone. The screen wrote in
green that everything was covered over four products in PEDIR_YA. The condition
is now the signal count, not a sibling's side effect. It is exactly the kind of
unsupported claim this document chases, and I produced it: without walking the
screen it would have reached production with the suite green.

---

## 1.octies The WhatsApp bot — findings from the assistant design — **[FIXED 2026-09-15]**

They came out of designing `docs/assistant-actions.md`. **Nothing was touched**:
all three depended on an owner decision that was open.

**a) Two live actions nobody can undo.** `WRITE_TOOLS`
(`backend/whatsapp/tools.py:237`) exposes `approve_po` and `register_reception`.
The second calls `receive_po`: it adds units to real stock and writes
`supplier_lead_time_obs`, which moves the learned lead time and the supplier's
scorecard. **There is no un-receive.** `approve_po` stamps `sent_at`, which
anchors the cash calendar, and that is not cleared either. Twilio is configured
in `backend/.env`, so this is live **as soon as it is deployed** (on localhost
Twilio cannot reach the webhook). It contradicts the rule the owner called
fundamental for the assistant: every LLM action is reversible. Closing it is one
line — take them out of `WRITE_TOOLS` until the inverses exist — or wait for the
undo work. **Owner's decision, raised 2026-08-23.**

**b) The router fails silently.** `_route` (`agent.py:87` and `:91`) returns
`{"tool": None}` when the model's answer does not parse, **without writing a
single log line**. The user is not left without an answer — it falls to the "no
tool" path and gets the help text — but they asked to record a reception and got
a menu, and the logs hold no trace. Nobody operating the bot can measure how
often it happens. It is the exact signature of the `silent-failures` skill.

**c) The regex that extracts the JSON is greedy.** `_JSON_RE =
re.compile(r"\{.*\}", re.DOTALL)` (`agent.py:73`) takes from the first brace to
the last. If the model emits prose with braces before the object, or two objects,
the captured chunk does not parse and falls into (b).

**What the design confirmed and is worth not forgetting:** the mechanism for
choosing a function already exists and is proven — the model never names an
endpoint, it names a key in a dictionary we wrote, and that is the allowlist.
What is missing to meet the owner's three rules is a backend-computed preview,
proposal expiry and undo.

### How they ended (2026-09-15)

**(a) Suspended, not deleted.** `WRITE_TOOLS` is empty; `approve_po` and
`register_reception` are in `SUSPENDED_WRITE_TOOLS`. The functions that propose,
the confirmation gate, the executors and their tests are all still there and
still work — turning them back on is that one line, once `receive_po` and
`mark_po_sent` have inverses. Three more things closed with them:

- a proposal saved BEFORE the suspension no longer executes on a "yes" today (it
  is discarded and the answer says where to do it);
- the model still **sees** both actions in the prompt, but as actions that are
  not done here, so somebody asking to record a reception gets "that is done in
  the app" and not the help menu;
- the tests that exercised the gate through `approve_po` now exercise it with a
  reversible test write tool: **the action was turned off, not the mechanism's
  coverage**.

**This is the only product decision taken without asking**, and it is reversible
in one line: if the owner prefers the risk, `WRITE_TOOLS` gets both entries back.

**(b) The router no longer fails silently.** A model answer with no JSON writes a
`WARNING` with the first 400 characters of what arrived. The user still gets the
help text; the difference is that it can now be measured.

**(c) The greedy regex is gone.** `_first_json_object` walks the opening braces
and uses `JSONDecoder.raw_decode`, which stops at the close of the first valid
object: prose with braces before, prose after, or two objects no longer break the
turn. Five shape tests in `test_whatsapp_agent.py`.

---

## 1.nonies `PATCH /inventory/stock/{sku}` fabricated phantom stock in `principal` — **[FIXED 2026-09-15]**

It came out of the API walk for the assistant design. **Read in the code, not
reproduced in a browser** — but the path leaves no ambiguity.

`patch_stock` (`backend/api/v1/inventory.py:187`) does two things that do not
talk to each other:

1. It checks the SKU exists with `svc.get_stock(tenant_id, sku)` — **with no
   warehouse filter** (`service.py:268`, the `warehouse` parameter is optional
   and is not passed). It finds the row wherever it is.
2. It writes with `svc.upsert_stock(tenant_id, sku, data)`, and `data` comes from
   `StockPatch`, **which has no `warehouse` field** (`inventory.py:84-96`). So it
   enters `upsert_stock` with no warehouse and falls to the default:
   `if "warehouse" not in data: data = {**data, "warehouse": "principal"}`
   (`service.py:96-97`).

A SKU that lives only in `Norte`: the PATCH passes the 404 check by looking at
the Norte row, and **creates a new row in `principal`** with the patched value.
The Norte one is left intact. The SKU now has stock in two warehouses, and the
consolidated view adds them up.

**Why this is level 1 and not cosmetic:** phantom stock inflates coverage. A
product that should have come out as PEDIR_YA can read OK because half its units
do not exist. It is the same family as finding 1.2 — inventing stock and deciding
purchases on it — but inverted: instead of an invented zero that over-buys, it is
an invented positive that **stops buying**.

The code already knew about this hole for another reason: the comment at
`service.py:135` says this endpoint "404-checks get_stock() without a warehouse
filter, so it never knew the target (sku, warehouse) pair was new" — written
while fixing the warehouse-ceiling bypass, without closing the door that causes
it.

**How it ended (2026-09-15).** Neither of the two options raised was necessary:
`StockPatch` **already had** an optional `warehouse` field — what was missing was
for the endpoint's two halves to talk about the same row. The destination
warehouse is now resolved FIRST and the existence check is done against that row:

- `warehouse` in the body → that row; 404 `stock_sku_not_found_in_warehouse` if
  the SKU is not there (before: it created the row);
- the SKU lives in exactly one warehouse → that one, whatever it is called;
- it lives in several and one is `principal` → `principal`, which is exactly what
  this endpoint always did, and it is a real row;
- it lives in several and none is `principal` → 422 `stock_warehouse_required`
  naming them. That is precisely the case that fabricated the phantom, and there
  is no safe guess to make on the user's behalf.

A side effect worth having: the PATCH can no longer **create** a row, so the two
ceiling bypasses this path had (`max_skus` and `max_locations`) stop depending on
the lock catching them — they are closed at the root. The two tests in
`test_entitlements.py` that covered them now assert the stronger contract (404
and the count intact, at any plan size).

*Tests:* `backend/tests/test_patch_stock_never_invents_a_warehouse.py` (10).
Verified to fail against the previous code: 4 of the 10 red.

---
## 2. The "what is missing" column of `screen-inventory.md` is the backlog

All 26 screens have had **some** walk. **None is walked end to end.** That column
is the honest part of the table and it is the work.

The biggest unexercised areas, grouped by where the risk lives:

| Screen | Not walked |
|---|---|
| `/pronosticos` | The 3 exports (per-SKU Excel, "All SKUs", PDF), full screen, detailed statistical analysis |
| `/inventario` | Shrinkage, dead stock, PDF export, Supplier view, events and seasons, stock CSV import |
| `/archivos` | Replace file, connect a SQL source, search, run a full Analysis |
| `/compras` | Sending to suppliers, destination warehouse, quantity editing, undoing an approval, the "Create transfer" gate |
| `/mi-cuenta` | Everything but the time zone: currency, WhatsApp, password change, theme/language, granularity, logs |
| `/pedidos` | Full arrival, new manual order, sending an order, WhatsApp, over-receiving |
| `/ventas` | Reusing an uploaded file, repeating the previous load, sample data, cancelling mid-run |

---

## 3. Three screens a plan wall was hiding — the wall is gone

**There are no plans any more** (2026-08-16, owner's decision: one plan, no
Stripe, the price is discussed with us). With that the ~40 `require_feature`
walls disappeared, and these three stopped being blocked:

- **`/escenarios`** — previously only the wall had been seen. **Walked
  2026-08-16**: demand change and supplier delay, both exercised and checked
  against the current plan (see the 2026-08-16 block at the end).
- **`/integraciones`** — the wall is gone, but the real flow is still
  **unwalked**: connect, test credentials, sync, see the error when they fail. It
  stays out of the menu on purpose, and now the reason is that and not a
  commercial one.
- **`/proveedores`** — verified over the API only. The screen was never opened;
  creating and editing a supplier untouched.

---

## 4. Recent work, still unseen in a browser — **[PARTIALLY CLOSED 2026-08-23]**

- ~~**The `/api` redesign**~~ — **walked 2026-08-23**: the header band, the key
  bar, the endpoint rail and the two-column sections all look right with the demo
  tenant seeded.
- ~~**A write all the way through**~~ — **executed 2026-08-23**: a real API key
  was created from the screen ("nightly ERP"), verified in the database to have
  left **one** row (the `limit_guard` added that day did not duplicate it), and
  revoked from the same screen.
- **The `log-po` fix** (commit `7866c64`): still unseen.
- Still unseen: **file upload** through `/api`, `train`, and the **narrow view**.

**Defect found while walking it, and fixed:** after revoking a key, the panel
still showed "Key generated — copy it now" with its *Copy* button. The user
copied a credential that had just died, and a 401 in the integration reads as "it
is broken", not as "I revoked it". `handleRevoke` now clears the banner. Verified
on screen: it appears on creation, disappears on revocation.

**A second defect, this one introduced the same day:** the page promised "120
calls per minute per key, **for everyone**" — false from the moment the free plan
added a ceiling of 500 calls a day. Corrected in es and en.

---

## 5. Findings from the `/api` review that were deliberately not fixed

Real, verified, and consciously outside that day's fix. They are here so the
decision is visible and reviewable, not so they are forgotten:

| # | What it is | Why it was left |
|---|---|---|
| a | A route parameter used twice in the same route would only be substituted the first time (`String.replace` with a text needle) | Latent: no current route does it |
| b | `values` is keyed by bare name, so a path parameter and a query parameter with the **same name** would share a state cell | Latent: no current endpoint collides |
| c | A `/` inside a parameter is encoded to `%2F` and does not survive the two hops (rewrite + uvicorn) | The ids are UUIDs; the diagnosis would be confusing, not the result |
| d | A future page in `Frontend/src/app/api/<something>/` would **shadow** the backend endpoint of that name | There is none today; it deserves a warning comment in the file |
| e | The raw exception text is shown next to translated copy | It is a developer console; the technical detail is useful there |

---

# Adversarial sweep of 2026-08-11 — six agents

Six read-only reviews, in parallel, over what already exists: lead-time learning,
odd supplier arrangements, the optimiser, `/impacto`'s fidelity, consistency of
the same number across screens, and copy that claims more than the data supports.

**How to read this.** Each agent marked CONFIRMED (traced end to end) or
SUSPECTED. The ones marked **[verified]** I read directly in the code myself as
well as the agent. One finding was **refuted** on verification and is recorded
below with what the agent missed — it matters as much as the real ones.

**State at 2026-08-12.** Level 1 and level 2 are closed except for one thing,
named below. Each finding carries its `[FIXED]` marker and what covers it,
because the written finding is what explains why the fix is that one and not
another.

- `3e0bde6` closed **1.1**, **1.4**, **1.5**, **1.6**, **2.7**, **3.2**, **3.5**
  and the panel half of **1.2**. Its commit message mentioned only seven things;
  1.4 and 2.7 went in without being named.
- The 2026-08-12 work closed **1.3**, **1.7**, **1.8**, **2.1**, **2.2**,
  **2.3**, **2.4**, **2.5**, **2.6** and **2.8**, plus a new finding that
  appeared while writing 1.3's test (see **1.9**).

**The only level-1 item left:** the import half of **1.2**, which needs the
`current_stock_set_by` column — new capability, not a fix.

Nothing in levels 3 and 4 was touched. Several are a one-line guard with
precedent in this very repository; **3.6** needs new capability (a MOQ field on
the optimiser's input) and is therefore asked about first.

## Level 1 — the user loses money by acting on this

### 1.1 The 08:00 UTC alert reads the active session at the wrong grain [verified] — **[FIXED 3e0bde6]**

> Both schedulers and the monthly overstock snapshot pass `period`
> (`service.py:3453`, `:3472`, `:3495`, `:3600`), and the coverage in the email
> and the WhatsApp message is rendered in the tenant's unit, singular included —
> so "4 weeks" is no longer printed as "4 days".

`service.py:3391` resolves the active session — the same one the screens show —
and `:3405-3410` computes it by calling `_compute_inventory_status` **without
`period`**, which by signature falls to `"daily"` (`:1257`). The same at
`:3423-3428` (transfers in the WhatsApp notice) and `:3547` (the snapshot that
feeds "capital freed" on `/impacto`).

A tenant on `weekly`, a SKU with 40 in stock, a 14-day lead time, a forecast of
10 units a week. On screen: 4 weeks of coverage against 2 of lead time → **OK**,
nothing to order. The 08:00 email reads the same session as daily: 4 "days"
against 14 → **PEDIR_YA**, order ~100 units. The buyer opens `/inventario` and
sees green.

Aggravating factor: the email and the WhatsApp message pin the unit to "days"
whatever happens (`email.py:368`, `whatsapp.py:163`), so even fixing the loop
would leave it mislabelled. The suite covers `/hoy` and the narrative for this;
the alert loop was never covered.

### 1.2 Faro invents `current_stock = 0` and then flags goods in the warehouse red [verified] — **[HALF FIXED 3e0bde6]**

> **The gaps panel no longer does it:** it stopped sending `current_stock: 0`
> alongside the cost and asks for the count.
>
> **The import is still live, and cannot be fixed without an owner decision.**
> The column is `current_stock FLOAT NOT NULL DEFAULT 0` (`migrations.py:369`)
> and `POST /inventory/bulk` requires only `sku` (`inventory.py:499`): a price
> list with no stock column creates new rows with a zero that nothing
> distinguishes from a counted one. Covering it properly is the
> `current_stock_set_by` column this very finding names — **new capability, it
> gets asked about first**.

`SetupGapsPanel.tsx:85` — `upsertInventoryStock(sku, { current_stock: 0, ...body })`.
If the user filled in only the cost, the zero persists. There is no
`current_stock_set_by` column, so nothing distinguishes an invented zero from a
counted one: `coverage_days = 0` → **PEDIR_YA**.

Thirty costed rows of a product there are full pallets of → thirty emergency
orders for inventory already on the shelf. The same on importing a price list
with no stock column, and the assistant reports nothing but good news. The
dataset-sync path **does** have the guard (`service.py:465-474`); the gaps panel
and `/inventory/bulk` dodge it.

### 1.3 The lead time is never reported as missing, and the screen promises it is required — **[FIXED 2026-08-12]**

> It is reported when the cascade would fall to the invented 15
> (`resolve_field` returning `SOURCE_DEFAULT`), and **not** when a supplier or
> category rule already resolves it — asking the user for something they already
> gave us trains them to ignore the column. It stays out of `BLOCKING_FIELDS`: a
> missing lead time makes the plan wrong, not impossible.
>
> The copy now says what actually happens: with no stock or no cost the product
> does not appear in the semáforo; with no lead time it **does** appear,
> calculated on an assumed 15.
>
> **Limitation pinned with a test:** `items` carries only the `is_gap` ones, so a
> SKU missing **only** the lead time still does not appear on that screen. Making
> it visible means marking it blocking (false: the SKU does appear in the
> semáforo, and it would move `covered_pct` and the bar) or a second list in the
> response and a new section — new capability, owner's decision.

The copy (`stockSetup.ts:18`): *"it needs three more things: how much you hold,
what it costs you and how many days it takes to arrive. While they are missing,
that product does not appear in the semáforo."*

`setup_gaps_service.py:118-137` only checks `unit_cost`, `sale_price` and
`supplier`; `BLOCKING_FIELDS = ("stock", "cost")`. The SKU **does** appear in the
semáforo, planned over an invented 15 days. An importer with 45 days of transit
reorders 30 days late on their best sellers, cycle after cycle. The
`lead_time_set_by` column that would allow warning about it exists and is not
read.

### 1.4 The first crumb of a partial delivery sets the supplier's lead time [verified] — **[FIXED 3e0bde6]**

> The observation is written only when the PO reaches `received`, dated by the
> event that completed it (`reception_service.py:361`). The lead time that
> matters for planning is when the buyer can **count on** the order, that is,
> when the last unit lands. A PO that never completes produces no observation —
> which is the honest answer: we do not know yet how long it took.

`reception_service.py:330` computes `lead_days` from **that** event, and the
`already_observed` lock (`:337-343`) means the later deliveries of that PO are
never measured.

A PO of 5,000 units on 1 August; 20 samples arrive on the 3rd; the rest on 10
September. Faro learns **2 days**. By the third PO like that, the learned value
overrides the declared one for every SKU of that supplier, the reorder point
collapses, and the scorecard shows "real 2d vs declared 30d" at 100% on time.

No code revisits the observation when the PO completes.

### 1.5 Recording "nothing arrived" leaves the PO dead forever [verified] — **[FIXED 3e0bde6]**

> `not_received` entered `RECEIVABLE_STATES` (`reception_service.py:47`): it is a
> late order, not a cancelled one. It stays receivable, stays in overdue
> receptions, stays in in-transit stock and in the open-PO count — so the
> semáforo no longer re-orders the units a buyer has just reported as not
> arrived.

`reception_service.py:312` — if no line receives any quantity, the state becomes
`not_received`, which is not in `RECEIVABLE_STATES` (`:39`), so the guard at
`:111-118` returns 409 forever. The PO also leaves overdue receptions,
`isAwaitingReception` and `get_incoming_qty`.

When the goods do arrive there is no way to record them: no stock, no lead-time
observation. "It has not arrived yet" and "it is never going to arrive" are the
same terminal state. It is reachable from the interface by entering zeros in the
quantities.

### 1.6 The horizon is built in days; the demand and lead time inside it, in periods [verified] — **[FIXED 3e0bde6]**

> The optimiser speaks **one** unit: calendar days at the boundary (which is what
> the endpoint asks for), buckets of the active period inside, with the lead time
> and the holding cost converted the same way. The response no longer reports a
> bucket count under a key called `horizon_days`, and the cash-fit path quotes
> the horizon it was asked for instead of a fixed 30 days at the wrong grain.
> With that, the monthly tenant comes out of the permanent greedy shortcut.

`inventory.py:2600` — `horizon_days = plan.horizon * _days_per_period(period)`.
The curve that fills those buckets is **per period** and the lead time is
converted the other way: `ceil(raw_lead / days_per_period)`
(`optimizer_service.py:227`). The comment at `:223-227` states that the endpoint
expresses the horizon in period buckets; the endpoint **multiplies**. Code and
comment contradict each other.

A monthly plan, horizon 4, a 30-day supplier: 120 buckets, of which only 0-3 have
demand; the model believes the supplier delivers in **1 bucket**; the storage
cost is underestimated ~30× (it charges `/365` per bucket over buckets that are
months); and the variables inflate 30×, so **with 6 SKUs** the 5,000 ceiling is
crossed and every monthly tenant falls permanently into the greedy shortcut,
which cannot do transfers.

`api.ts:1378-1384` already documents the symptom; the fix that was applied was to
stop passing 30, not to correct the unit.

### 1.7 MOQ is applied as a multiple, not as a minimum — and with no overstock guard — **[FIXED 2026-08-12]**

> **Owner's decision (2026-08-12): `moq` is a MINIMUM**, which is what the
> field's name says. `max(ceil(raw), moq)`, with a `raw > 0` guard so a
> well-stocked SKU does not receive a minimum order out of nowhere — the old
> `ceil` gave 0 for a `raw` of 0 and that had to stay. Rounding to whole units is
> now explicit instead of a side effect of the MOQ arithmetic. Needing 520 with a
> minimum of 500 orders 520, not 1000.
>
> **What was NOT done, and why:** the overstock guard. When the supplier's
> minimum alone already creates months of coverage, trimming below the minimum
> produces a quantity the supplier will not ship. The repository's precedent
> (`price_break_service`) does not trim: it **refuses the opportunity and returns
> the `reason_code`** so the UI can explain it. Doing the same here is a new
> field on the semáforo row — new capability, it gets asked about.

`service.py:948-949` — `raw = ceil(raw/moq) * moq`. You need 520 with an MOQ of
500 → it recommends **1000**, 92% overshoot, with a convert-to-PO button.

With no coverage guard: a SKU selling 2/day with a container MOQ of 1000 → it
recommends **500 days of stock**, and on refresh it comes out SOBRESTOCK.
`price_break_service.py:263-265` implements exactly that check and refuses a
140-day jump with `would_overstock`.

There is no concept of pack size (`pack_size`) anywhere.

### 1.8 "The recommendation is always a multiple of this number" is false on every path that reaches a PO — **[FIXED 2026-08-12]**

> With 1.7 the claim became doubly false, so the copy says what the product does:
> we never recommend less than that minimum, above it we order what is needed
> without rounding up, and **if you edit the quantity by hand we respect what you
> write**. That last sentence is what closes the finding: the six paths that
> persisted `final_qty` without re-applying the rounding no longer contradict any
> promise, because there is no longer a promise of "always".

`translations.ts:2985` and `:2996` claimed "always". The rounding is applied at a
single point (`service.py:948`) and never re-applied. These persist `final_qty`
without it: editing the quantity in the table, the optimiser ("Convert to PO"),
the price break (it pins the quantity to the rung's `min_qty`), the manual cart,
`POST /log-po` and `bom_service`. The transfer path uses **floor**, not ceil.

MOQ 12 with a rung at 100 → the cart sits at 100; the supplier invoices 108.

### 1.9 Creating a stock row sealed the defaults as chosen by the user — **[FIXED 2026-08-12]**

It did not come from the sweep: it appeared while writing 1.3's test, when a
freshly created SKU insisted on having `lead_time_set_by = 'user'` without
anybody having written a lead time.

`StockUpsert` has three non-Optional fields (`min_stock`, `lead_time_days`,
`moq`), so Pydantic materialises them to 0 / 15 / 1 and `model_dump` hands them
over as if the user had typed them. The endpoint filtered that **only for
existing rows** — "a new row has to start somewhere" — and with that
`upsert_stock` stamped `<field>_set_by = 'user'` over an assumption.

The value did not change (the column is `NOT NULL DEFAULT 15`); the stamp did,
and the stamp is what gets read. `resolve_field` lets the SKU's row beat a rule
**only** if its provenance says somebody set it: a tenant who configured "Acme
delivers in 45 days" as a supplier rule saw it silently discarded on every SKU
created through the normal screen, and bought on 15. And it made "the user chose
15" and "nobody touched this" indistinguishable, which is exactly the bug the
provenance columns exist to kill (`defaults.py`, `SOURCE_DEFAULT`).

The fix is to apply the same filter to new rows: what the caller sent is written,
the schema default fills the rest, and `<field>_set_by` stays NULL — which is how
you spell "we assumed this".
## Level 2 — numbers that do not mean what their label says

### 2.1 `/impacto`'s adoption rate uses a denominator its copy contradicts [verified] — **[FIXED 2026-08-12, copy]**

> **Owner's decision: fix the copy, not the number.** Counting ignored
> recommendations would require persisting what was **shown**, which the product
> stores nowhere — new capability, and a large one.
>
> The figure is now presented as what it is: "of the recommendations you
> decided", with a note that the ones you let pass untouched are on neither side.
> The caption that called it "the most honest measure of value" now says how to
> read it: *when you decide, how often you follow Faro* — not *what share of
> everything it suggested you followed*. Changed on screen and in the monthly
> email (`locale.py`), which repeated the same claim.

The copy: *"You followed N of M recommendations **Faro put in front of you**"*
(`translations.ts:2222`). `M` is `total_suggested`, which only counts lines that
reached `log_po_generation`; `/compras` filters `status !== 'pending'` before
logging, with the comment "the buyer never acted on them".

Faro recommends 20, you approve 3, reject 1, ignore 16 → **"75%, you followed 3
of 4"**. The real proportion is 15%. Five times inflated, always on the
flattering side. The backend's docstring describes it well; the screen claims
something stronger. And the caption calls this figure "the most honest measure of
value".

### 2.2 Two `/inventario` buttons manufacture 100% adoption out of a download [verified] — **[FIXED 2026-08-12]**

> **"Export edited"** filtered zeroed lines **before** building the decisions, so
> `rejected` was unreachable by construction. A line the buyer zeroes out is a
> rejection and is now recorded as one: it is the only thing that can move
> adoption off that green 100%.
>
> **"Export PO"** calls the legacy path with no body, and the server re-derived
> everything marking it `approved`. The order is still logged in full — the buyer
> took the file and will act on it — but the four decision counters stay at 0 and
> the row is marked `source='export'`, exactly as `create_manual_po` already did
> for hand-written orders. A download is evidence the list was taken away, not
> that the buyer agreed with every line, and that difference is the whole meaning
> of the metric.
>
> **Still live:** no deduplication. Pressing "Export" three times writes three
> POs. It no longer triples adoption, but it does triple the month's orders and
> units.

"Export PO" calls `logPOGeneration(sessionId, **undefined**, …)`, which falls to
the backend's legacy path (`inventory.py:1233-1238`): it re-derives every
PEDIR_YA/PEDIR_PRONTO and `_normalize_decisions` marks them `approved` by
default. `exportEditedPO` can only emit `approved` or `modified` — `rejected` is
structurally impossible.

A tenant working from `/inventario` sees **100% adoption, in green, forever**,
and every urgent SKU counted as a risk handled. With no deduplication: pressing
"Export" three times writes three POs and triples the month's figures.

It is the same hole that was closed in the `/api` console on 2026-08-11. The
product's own screen had been living with it for longer.

### 2.3 "Capital freed from overstock" attributes to Faro a subtraction nobody attributed — **[FIXED 2026-08-12]**

> **The attribution, by copy.** The column is now called "Overstock reduction",
> the headline "your idle inventory went down this month", and the note names the
> other causes: selling, recording shrinkage, deleting products, retraining. The
> provenance block adds that "recorded" is not the same as "attributed to Faro".
> Same in the monthly email.
>
> **The two `None`s, by code.** `_capital_freed_during` now returns
> `(value, state)` with `measured` / `not_measured` / `grew`. It used to answer
> `None` to two different questions — "we never took one of the measurements" and
> "we took both and your overstock **grew**" — and the UI printed *"we need two
> measurements"* for both: a tenant whose dead stock had just grown was told data
> was missing, and the column was structurally incapable of delivering bad news.

`roi_service.py:347-363` — `snapshot(M) - snapshot(M+1)` of the value in
SOBRESTOCK, from a different session each month. Nothing ties the difference to
any action: it moves on selling, on recording shrinkage, on **deleting SKUs**, on
editing costs and above all on retraining.

Deleting 200 discontinued SKUs holding ₡8M of dead stock headlines **"₡8,000,000
freed from idle inventory"**. Right below it, `recap.provenance_body` assures the
reader "we do not estimate savings… we only show what was recorded", which makes
it read as audited.

And `:363` returns `None` both when a snapshot is missing and when overstock
**grew**, and the UI paints both as *"We need two consecutive monthly
measurements"*. The column can only show gains.

### 2.4 "Stockout risks handled" counts lines and claims a punctuality nobody measures — **[FIXED 2026-08-12, copy]**

> The headline becomes "urgent lines you ordered", and the detail says the three
> things the number is not: it counts **lines**, not distinct products (the same
> 30 urgent ones every month for a year add up to 360); it does not measure
> whether they arrived on time; and it includes orders that have not arrived.
> Before, the detail literally said "that you did order **on time**", about
> something nobody measures.

`roi_service.py:246` — `SUM(skus_order_now)` over every PO. 30 urgent SKUs
ordered monthly for a year read as **360**. Nothing checks "on time". The recap
card carries the right nuance; the headline, which is the larger surface, claims
the opposite.

### 2.5 Orders that never arrived count as managed — **[FIXED 2026-08-12, copy]**

> **Owner's decision: the number does not change.** The module's declared policy
> is to count **the action taken, not the outcome**, and generating the order is
> the action. Filtering by `reception_status` would turn these figures into a
> measure of deliveries, which is a different metric.
>
> What is fixed is that the screen says so: "urgent lines you ordered" clarifies
> that it includes orders that have not arrived, and "managed purchases" says
> "whether they arrived or not". The scorecard still excludes them, and now that
> difference is visible instead of being a silent contradiction between two
> screens.

No `/impacto` query filters `reception_status`. A ₡12M PO that was never
delivered still counts as 40 risks handled and ₡12M managed. The scorecard, one
screen away, **does** exclude the unreceived ones.

### 2.6 "Managed purchases" silently drops the lines with no unit cost — **[FIXED 2026-08-12]**

> `managed_purchase_value_complete` comes from counting ordered lines against
> lines with a cost in `inventory_po_items` — on read, not in a new column, so
> there is no migration and no way for it to drift from the value it qualifies.
> When coverage is partial the screen shows `≥ ₡2.1M` and explains that costs are
> missing, instead of printing a total that is not one.

`roi_service.py:95-101`. The "no line has a cost" case is handled honestly. The
**partial** case is not: 40 lines with 6 costed report those 6 as the total. ₡30M
can read as ₡2.1M, looking exact.

### 2.7 The scorecard's "% on time" scores against a declared value nobody declared — **[FIXED 3e0bde6]**

> The `LEFT JOIN` accepts the declared value only when `lead_time_set_by` is set
> (`reception_service.py:476`), so a supplier imported from a CSV comes out as
> "not declared" instead of being graded against a promise they never made — and
> `on_time_rate` and `deviation_days` go with it. `MIN_RATE_OBSERVATIONS` also
> landed: the row's two percentages no longer speak below the sample floor, and
> `lead_time_unusable` covers the "every delivery the same day" case, which
> printed "0d" next to "100%".

`reception_service.py:382-384` reads `s.lead_time_days` raw, which is
`INT NOT NULL DEFAULT 15`. The guard is in the **same file, 140 lines below**
(`:520-528`), with the comment explaining why it was added.

A supplier imported from a CSV who delivers in 12 days: "DECLARED 15d, 100% on
time". One who promised 20 comes out at 0%, in red. On top of that,
`/proveedores` launders the default: the form pre-fills 15 and
`supplier_service.py:29-30` stamps it as `SOURCE_USER` — walking past the field
and saving turns it into a deliberate decision forever.

With no minimum N either: a supplier with counter pickups (`lead_time = 0`)
prints **"Not conclusive"** and **"100%"** on the same row.

### 2.8 "Purchased value" shows a confident ₡0 where `/impacto` would say "not available" — **[FIXED 2026-08-12]**

> The `COALESCE(..., 0)` was removed: with not a single costed line the cell is
> `null` and the screen paints the same dash it already uses for everything
> unmeasurable, with an explanation that it is not a zero. With partial coverage
> it shows `≥`. The same rule `/impacto` applies to the same figure, which was
> half the finding: same data, two policies, one screen apart.

`reception_service.py:398` — `SUM(final_qty * unit_cost)`; a NULL cost annuls the
product and SQL discards it. A supplier you bought ₡40M from with no recorded
costs reads **₡0** in green. `roi_service.py:538-540` applies the opposite rule
to the same quantity. Same data, two policies, one screen apart.

## Level 3 — the same number, two authorities

### 3.1 `/inventario`'s "Export PO" downloads different quantities from the table's — **[FIXED 2026-08-12]**

> There were **ten** calls to `get_inventory_status` without a period outside
> tests, not one. They all now resolve the tenant's grain with the same pattern
> `GET /status` already used: the CSV, `dashboard-summary`, dead stock, the
> price-break fallback cart, the alert test trigger, `log-po`'s legacy path, the
> event simulator, `production-requirements` and the PDF.
>
> The PDF and `explode_requirements` had to have **the parameter added**: it did
> not exist, so they were always daily. The PDF matters twice over because it is
> the only copy of these numbers that leaves the app, and it is read by people
> who cannot check it against any screen. `simulate_event_impact` and
> `get_demand_spikes` accept it now too, defaulting to `"daily"` so no existing
> caller changes behaviour.
>
> Pinned with three end-to-end tests against the endpoints — not against the
> calculation — because the defect was never in the arithmetic but in what the
> endpoint passed it.

The table is period-aware (`inventory.py:701`); the export (`:2511`) calls
`get_inventory_status` **without a period** and re-derives the list on the
server. The same weekly tenant from 1.1: the screen offers nothing to order and
the CSV carries 100 units, plus a PO in `/pedidos` the buyer never saw.

The same period-less recomputation, smaller in scope: the PDF
(`service.py:2536`, which **has no** `period` parameter), dead stock, the
price-break fallback cart, the alert test trigger, `dashboard-summary`,
`production-requirements` and `simulate_event_impact`.

### 3.2 The same `/inventario` row shows two different "LT demand" figures [verified] — **[FIXED 3e0bde6]**

> The column publishes `_demand_lt` (`service.py:1575`), the same value the
> reorder point and the "how it is calculated" breakdown use. One calculation,
> one number, and the CSV inherits the right one.

`service.py:1432` — `avg_daily * lt_periods`, which feeds the reorder point.
`service.py:1563` — `avg_daily * lead_time`, in calendar days. **Both in the same
dictionary.** The "LT demand" column paints the second; expanding the row shows
the first. Weekly, 10/week, 14 days: the column says **140**, the breakdown says
**20**. A factor of 7 weekly, 30 monthly. The CSV uses the column's, so it
contradicts the breakdown.

### 3.3 The WhatsApp bot answers about another session, at another grain and from another model — **[OUT OF SCOPE]**

> The owner excluded the WhatsApp bot from the stability work (2026-08-12), along
> with the LLM and Stripe items. The finding stays written and untouched: it is
> still the **only** point in the whole product that skips
> `resolve_active_session`.

`whatsapp/tools.py:62,106` uses `get_latest_completed_session` — an
`ORDER BY updated_at DESC LIMIT 1` — instead of `resolve_active_session`. It is
the **only** point in the whole product that skips the resolver. `:65` passes no
period. And `:114` does `next(iter(models.values()))`: **the first model in the
dictionary**, which is exactly the `/pronosticos` bug already fixed at
`forecasts.py:512` and never ported here.

### 3.4 `/pronosticos` crowns the champion by a different rule from the server's three — **[FIXED 2026-08-12]**

> `championRank` was `r.cost_horizon ?? r.cost ?? r.wape` evaluated **per row**,
> so it compared one model's `cost_horizon` against another's `cost` — two
> quantities on different scales, and `test_horizon_comparability` says the
> second is systematically smaller. `makeChampionRank` now picks **one** metric
> for the whole set and compares within it, which is what
> `service._champion_metric` does. Changed at all five uses: the SKU card, the
> statistics strip, the metrics table, the PDF and the accuracy next to the
> chart.
>
> One scope difference remains, noted in the code on purpose: the server picks
> the metric over the rows of **the whole session** and the browser over the ones
> it holds (a single SKU's, on almost all these surfaces). They diverge only in a
> session where some SKUs carry `cost_horizon` and others do not.

The server and the engine pick **one** metric column for the whole set and
discard the rows without it. The browser (`pronosticos/page.tsx:71-72`) does
`r.cost_horizon ?? r.cost ?? r.wape` **per row**, so it compares one model's
`cost_horizon` against another's `cost` — and `test_horizon_comparability`
asserts the second is systematically smaller.

The excluded model wins the browser's comparison: the statistics strip announces
"Best model: XGBoost" with its WAPE, while the drawn curve and the purchase order
come from Prophet. Mechanism CONFIRMED; frequency SUSPECTED (it depends on
`cost_horizon` being None on ML rows, which `trainer.py:438-485` produces through
several real paths).

### 3.5 The aggregate status and the per-warehouse one resolve the supplier differently — **[FIXED 3e0bde6]**

> `service.py:1725` now falls back to the configured primary just as the
> aggregate view does, so the four resolutions hanging off that word — learned
> lead time, `lead_time_days`, `moq`, `service_level` — give the same answer on
> both tabs.

`service.py:1355-1357` — `stock.supplier or primary.supplier_name`.
`service.py:1696` — `stock.get("supplier")`, **without the primary fallback**.
That word propagates into four resolutions: the learned lead time, and the rules
for `lead_time_days`, `moq` and `service_level`.

A SKU with a blank supplier on the row and "Acme" as primary: the "Todas" tab
gives **PEDIR_YA** with Acme on the row; the warehouse tab gives
**PEDIR_PRONTO** with an empty supplier. One SKU, one warehouse, two signals on
two tabs of the same page.

### 3.6 The optimiser ignores every supplier input the semáforo uses — **[FIXED 2026-08-22]**

> There is **one** resolver: `optimizer_service.resolve_planning_inputs` calls
> the semáforo's — `stock_defaults_service.resolve_field` for the cascade (a row
> somebody set > supplier rule > category > global > default) and
> `service.resolve_lead_time` so real receptions win — over the **same**
> representative row per SKU (`_aggregate_stock_rows_by_sku`, anchored to the
> default warehouse) and the same primary-supplier fallback. The endpoint
> resolves it once per request and passes it to both build and serialize, so the
> plan is **solved** and **reported** with the same numbers.
>
> MOQ does not fit in the MILP (the model has no minimum variable), so it is
> applied as a **floor** at serialisation, exactly as `_calc_recommended` does:
> the floor of ONE order to the supplier, not one per warehouse — applied per
> line it would multiply the minimum by the number of warehouses the solver
> splits across — and never over a 0, because "there is nothing to order" has to
> keep meaning that.
>
> `test_optimizer_agrees_with_the_semaforo.py` pins it: without the fix the MILP
> solved over 15 days while `/hoy` showed 20 learned, and offered 50 units
> against a supplier minimum of 500.

`optimizer_service.py:220-230` reads `inventory_stock.lead_time_days` raw — no
rule, no provenance, no learned lead time — and the MOQ is not passed: there is
no MOQ field on `OptimizationInput`. `/hoy` plans over 45 days *"learned from
your receptions"* and an MOQ of 500; `/planning` solves over 15 and 137 units.
Both screens offer to convert to a PO.

### 3.7 "Which is the default warehouse" has two answers — **[FIXED 2026-08-12]**

> There is now **one** resolver, `warehouse_service.get_default_warehouse_name`:
> the `is_default` flag anchored first, `name_precedence_key` second.
> `get_demand_shares` calls it and `_aggregate_stock_rows_by_sku` receives it as
> an argument — one query per request, not one per row, which was the old
> comment's legitimate objection.
>
> The comment claiming the question "is answered the same way everywhere" was
> false and is now true. What it cost: a tenant whose first warehouse was "Zona
> Sur" had 100% of the demand there while the aggregate row took cost, lead time,
> MOQ and supplier — and with them the headline warehouse value — from
> "principal". One row describing two different buildings.

`warehouse_service.py:145-148` looks at the `is_default` flag and then the name;
`service.py:1222` only at the name, because it only has stock rows to hand. The
key's docstring claims both answer the same "everywhere". They do not.

If the first warehouse is "Bodega Sur" and "principal" was created later, 100% of
the demand goes to Bodega Sur while the aggregate row takes cost, lead time, MOQ
and supplier from principal — and with them the headline "warehouse value".

### 3.8 The optimiser split demand by where the stock already was — **[FIXED 3e0bde6, tests 2026-08-12]**

It did not come from the six-agent sweep; it appeared while fixing 1.6, in the
same file. `optimizer_service` divided a SKU's demand as
`stock0[(sku,w)] / stock_total`, which makes half the model's transfers
**self-defeating**: a warehouse's need was defined as proportional to what it
already had. A branch with 0 units of a SKU that does sell got 0 demand and could
never be a transfer destination; the central depot that had everything took 100%
of the demand and was told to buy more. With 0 stock everywhere the denominator
was 0 and an **even** split was invented — which is not a neutral default: it
pushes a SKU into warehouses that have never held it.

It now reads the same thing the per-warehouse semáforo does, in the same order of
preference: per-store forecasts if they exist, otherwise `warehouses.demand_share`
from `/bodegas`, renormalised over the warehouses that do have stock rows — a
share assigned to a warehouse the model cannot supply would swallow demand
silently — and with nothing configured, everything to the default warehouse,
which is a claim the tenant can see and change. Five tests in
`test_optimizer_service.py` pin it, including that the total split is still the
whole demand.

## Level 4 — claims with nothing behind them

### 4.1 "Clean series — no warnings" is structurally incapable of saying anything else [verified] — **[FIXED 2026-08-12]**

> The rule for "how often does this series report" was written **twice**: the
> profiler inferred it from the data (which is why the chart footer did say "7
> gaps"), and `DataQualityChecker` received `date_freq: None` from the runner and
> answered 0 gaps for every session ever trained. Now there is one:
> `quality.infer_freq_days` — the median gap between dates, not `pd.infer_freq`,
> which returns None on any irregularity, that is, on almost every real file —
> and the profiler uses it too. A configured `freq` still wins: whoever knows the
> calendar knows more than an inference.
>
> With that the warning can fire, the score can lose its 20 points and "Low"
> stops being unreachable.
>
> The word **"interpolated"** left the chart footer: `gap_fill` defaults to
> `leave` — nothing is filled — so it asserted a treatment that in most sessions
> never happened. Saying they were **detected** is true under any strategy;
> saying what was done with them requires the strategy in the payload, and it
> does not travel.


`runner.py:135` pins `"date_freq": None` (it is only read, never assigned) and
`quality.py:249` returns 0 when there is no frequency. For **every** trained
session `missing_dates = 0`, the warning never fires and the score never loses
its 20 points: the floor sits at 0.65 against a "Low" threshold of 0.45, so
**"Low" is unreachable**. The chart footer for that same SKU may say "7 gaps
detected" one tab away.

Also unconditional in that footer: the word "interpolated". The default is
`gap_fill = "leave"` — nothing is filled.

### 4.2 `/escenarios`: "Supplier delay" does nothing, and reports that as the result — **[FIXED 2026-08-12]**

> **Two** things were missing, not one:
>
> 1. **That the result be believed.** The scenario wrote the number and never the
>    provenance, and `resolve_field` only honours the row's value when
>    `lead_time_set_by` says somebody set it. Now `SOURCE_USER` is stamped on the
>    in-memory copies — which never touch the DB — which is exactly what a "what
>    if" asserts: the user is declaring that lead time.
> 2. **That it be added to the right base.** `row["lead_time_days"]` is the raw
>    column, `NOT NULL DEFAULT 15`. A SKU whose real lead time came from a
>    45-day supplier rule ended up at 15+21=36 — **shorter than its reality with
>    no delay at all**. The base is now the value resolved by the same cascade
>    the semáforo uses.
>
> Note: the fix for **1.9** enlarges this finding, because it leaves more rows
> with `set_by` NULL. The two go together.


`scenarios/service.py:304-319` writes the lead time on the row but never sets
`lead_time_set_by`, and `stock_defaults_service.py:314-318` only honours the
row's value if that column is set. For every SKU whose lead time was not set by
hand — most of them — the supplier rule or the default of 15 wins.

You simulate "my supplier is 21 days late" ahead of high season, the screen
answers *"This scenario changes no purchasing decision"*, you do not buy ahead,
and you stock out.

### 4.3 `/pedidos` on mobile claims "all" over a window of 50 — **[FIXED 2026-08-12]**

> The sentence is bounded to what the screen can see ("among your recent
> orders") and points at the desktop one for anything older.
>
> And `not_received` joined `OPEN_RECEPTION_STATUSES`, which is the mirror of
> `RECEIVABLE_STATES`. This was **an inconsistency introduced by the fix for
> 1.5**: the backend already treated it as receivable and the frontend kept
> sending it to "Already recorded", so the screen asserted the *arrival* of goods
> it had itself just recorded as not arrived.


`'mobile.pedidos_awaiting_none'` says *"you have already recorded the arrival of
**all** your orders"*. The query is `ORDER BY generated_at DESC LIMIT 50`: order
51 in `pending` is invisible, and a distributor generating one PO a day passes 50
in under two months. On top of that `not_received` falls into "closed", so the
screen asserts the *arrival* of goods that demonstrably did not arrive.

### 4.4 `/inventario`: "All inventory is well covered" ignores `SIN_DATOS` — **[HALF FIXED 2026-08-12]**

> **The sentence, fixed.** `summary.sin_datos` travelled in the payload and was
> not read. The sentence now splits: "N well covered, and M with no signal yet —
> they are missing their stock count", in amber instead of green. And the case
> that showed **nothing** — no actionables, no OKs, with unjudged SKUs — now says
> what is happening, because an empty panel reads as "no news is good news".
>
> **The `9999` is still alive, and needs your decision.** `coverage_days = 9999`
> when mean demand is 0 produces SOBRESTOCK, and then the coverage is nulled for
> display: blue badge, coverage "—", caption "consider pausing the order". I did
> not touch it because it changes the semáforo's verdict for a whole class of
> SKU, and `_calc_signal` is the single authority on the signal — the piece this
> very document lists as solid. There are two defensible readings (a forecast of
> zero demand **is** dead stock / "we do not know" is not "you have plenty") and
> it is a product decision, not a fix.


The sentence is pushed when there are no action lines and there is at least one
OK. `summary.sin_datos` travels in the payload and is not read. Five SKUs in OK
and 500 with no stock record produce a green sentence asserting that *all*
inventory is covered.

Adjacent: `service.py:1414` sets `coverage_days = 9999` when mean demand is 0,
which gives **SOBRESTOCK**, and then the coverage is nulled for display. The row
ends up with a blue overstock badge, coverage "—" and the caption "consider
pausing the order". "We do not know" painted as "you have plenty".

### 4.5 The landing carried fifteen figures with no source, and two false claims — **[FIXED 2026-08-23]**

> The fifteen figures are gone; the industries panel went from «Typical impact in
> {industry}» with green percentages to «What Faro does in {industry}» with
> sentences checkable against the code. The heading was part of the lie: it
> promised a measured result, and that made the numbers read as measurements.
>
> The «6 months» claim appeared in **two** places, not one. The real threshold is
> 20 periods (`config.py:107`, `gate.py:59`), and the engine **discards** those
> series (`quality.py:216`) instead of classifying them: the category «high
> uncertainty» does not exist in the repo — checked with grep.
>
> The encryption one was replaced by what the code actually does: per-tenant
> queries, roles, integration credentials encrypted with Fernet (`crypto.py`) and
> real deletion (`data_export.py:213`). The datasets are flat files, and it now
> says so.
>
> Along the way, the adjacent contradiction: «Scales from 50 up to 50,000 SKUs»
> against «5K+ SKUs per instance» in the same file, an order of magnitude apart.
> The 5K+ stays, which is the one with something behind it.
>
> Verified in the browser: zero percentages on screen, none of the three claims
> present, `tsc` clean.

Under *"Typical impact in {industry}"* (`page.tsx:277-321`): "stockouts down
20-35%", "emergency purchases −30-50%", "shrinkage −25-40%", and twelve more. It
is the same defect as the hero strip that **was** deliberately cleaned, with the
reasoning written 400 lines further down in the same file.

Two more, both confirmed:
- *"Products with less than 6 months of data are classified as 'high
  uncertainty'"* — the engine **discards** them; the threshold is 20 periods, not
  6 months; and that classification does not exist in the repo.
- *"Transmission and storage are encrypted"* — the only encrypted thing is the
  integration credentials. Datasets and artifacts are flat files under
  `storage/`.

### 4.6 The transfer asserts a lead time and a price comparison it never made — **[FIXED 2026-08-12]**

> **The comparison that never happened** now has its own code:
> `transfer_faster_price_unknown`. With no unit cost anywhere the price test does
> not run, and returning `transfer_faster_and_cheaper` made the screen say "costs
> less than buying" about a comparison that never took place. The transfer is
> still accepted — arriving sooner is a real argument on its own — but under a
> sentence that only asserts that.
>
> **The invented lane** is now declared: `lane_is_default` travels in `params`
> and the UI adds the note that it was computed with 1 day and zero cost. That
> default is precisely the one that wins any transfer-vs-buy comparison, so
> distinguishing it from a measurement is not a detail.


`transfer_lane_service.py:14-18` resolves an unconfigured pair at **1 day and
zero cost**, and says so: *"deliberately optimistic"*. The resolved lane carries
`is_default: True` and `_evaluate_transfer_lane` never puts it in `params`, so
the UI cannot tell a measured lane from an invented one. Worse: when there is no
unit cost the price test is skipped entirely, `saving` stays null — and the code
still returns `reason_code = "transfer_faster_and_cheaper"`, so the sentence goes
on asserting *"costs less than buying"*. It withholds the figure and keeps the
claim.

### 4.7 More, in brief

- ~~**`50% anticipo` reads as 50 days of credit.**~~ **[FIXED 2026-08-23]** — the
  stem was widened to `anticip|adelant` (= 0 days of credit), instalment plans
  (`2x30`, `30/60/90`) return `None` and fall into `unknown_terms` as the module
  promises, and percentages are stripped before the number extractor. The same
  correction was applied to the SQL backfill, because a test pins SQL/Python
  parity — and that test passed against the old code: SQL and Python were
  **consistently wrong**, which is why parity alone never caught it. Original:
  `cash_service.py:34-37`
  looks for `anticipad`, not `anticipo`. Falls through to the number extractor →
  50. Reports `terms_known: True`, contradicting the module's promise that
  anything unreadable stays None. Same family: `2x30` → 2, `30/60/90` → 30.
- ~~**Two price ladders from different suppliers are merged.**~~ **[FIXED
  2026-08-23]** — the rungs are grouped by `(sku, supplier_id)`; the ladder of
  the SKU's supplier is quoted, and if the SKU names no supplier the best ladder
  wins **credited to whoever offered it**. Original:
  `price_break_service.py:298-312` groups only by SKU and names the owner of the
  lowest rung. The panel can say "Andina: go up to 500 and save ~1400" about a
  price Andina never offered.
- **A supplier cannot be reactivated.** **[FULLY FIXED 2026-08-23]** — it now
  answers 409 with two distinct codes (`supplier_name_taken` and
  `supplier_name_taken_by_deactivated`), because the user's next move differs in
  each case; it also covers the race of two simultaneous inserts. The route
  **was** built (`POST /inventory/suppliers/{id}/reactivate`, analyst+,
  idempotent, with a permission pair in test): without it deactivation was a
  one-way door and the 409 named a row the user could not touch — a dead end
  created by the error message itself. It carries **no name re-check**, on
  purpose: `UNIQUE (tenant_id, name)` does not exclude inactive rows, so the
  collision such a check would prevent cannot exist in the database. A guard that
  cannot fire reads as protection and protects nothing. There is a test pinning
  that invariant for the day the index becomes partial. Original: deactivation is
  logical, there is no reactivation route anywhere, and the unique index does not
  exclude inactive rows: re-creating it with the same name gives a **generic
  500**.
- ~~**Deactivating a supplier takes money you still owe out of the cash
  view.**~~ **[THE CASH HALF, FIXED 2026-08-23]** — accounts payable now loads
  **every** supplier (an active one wins a name collision); `supplier_service`
  keeps its filter, because a PO must not auto-send itself to a deactivated
  supplier. ~~**The lead-time half**~~ **[DECIDED AND FIXED 2026-08-23]** — the
  rule is: deactivating = **stop acting** towards the supplier (do not auto-send
  POs, do not offer them for new work), **not** forget what we know about them.
  Under that rule the odd one out was `build_rule_index`, which filtered actives
  and dropped SKUs to the 15-day default — worse data than what the user wrote,
  applied without saying anything. It no longer filters: the three sources now
  agree on not reacting. `supplier_service` keeps its filter, which is the
  correct half of the same rule. Original description: three sources react in
  three different directions to a deactivation — the card disappears
  (`stock_defaults_service.build_rule_index` filters actives → the SKUs go back to
  15 days), the `stock_defaults` rule with the same name **keeps applying** (it is
  indexed by free text, it never joins `suppliers`), and the lead time **learned**
  from receptions also keeps applying. Closing it is a decision, not a local edit:
  either deactivation cuts all three or it cuts none, and it changes the
  semáforo's inputs for every SKU of that supplier. Original:
  `cash_service.py:132-138` only loads actives, so a sent and unpaid PO falls to
  `unknown_terms` and leaves `committed_total`. And its SKUs go back to 15 days
  while a `stock_defaults` rule with the same name keeps applying: the two halves
  of "this supplier's lead time" react in opposite directions.
- **`unit_cost = 0` makes a SKU invisible to the optimiser, without marking it.**
  — **[FIXED 2026-08-22]** The coefficients were already computed with
  `_usable_unit_cost` (a 0 is a blank that happened to be a number, not a price),
  so the SKU enters the plan at the assumed cost. What went on lying was the
  flag: `assumed_unit_cost` checked `is None`, so those lines were reported as
  real prices over a `total_cost` computed with 1.0. It now checks the same thing
  the arithmetic does, and the SKU either enters marked or does not enter.
- **Two buyers can receive two different plans from the optimiser.** —
  **[FIXED 2026-08-22]** The concurrent-solve quota was 2 and the engine runs
  **every** solve on a single dedicated thread (the HiGHS deadlock fix, which is
  not being touched): the second one admitted did not solve in parallel, it
  **queued**, and its wait — `time_limit + grace`, counted from submit — was
  spent while the first was still solving. On expiry, `optimize()` treats it like
  any other unsolved case and returns the greedy plan. The quota is now **1**:
  the second buyer gets an honest 503 that the browser retries, instead of a
  silently different plan. Nothing is lost — that second slot never bought
  concurrency, only a wait that ended in degradation.
- ~~**The "this is the shortcut" notice sits inside the block that is only
  painted when there are orders or transfers.**~~ **[FIXED 2026-08-23]** — it is
  the same defect as section 1.quater: the condition now includes
  `status === 'fallback'` and that case has its own copy. Original: a shortcut
  that finds nothing showed nothing, so "there is nothing to do", "we degraded to
  a simpler rule" and "the problem was too large" looked identical: a blank
  space.
- **`sku_suppliers.lead_time_days / moq / unit_cost` reach no planning path** —
  they are only displayed. `GET /inventory/stock/{sku}/suppliers` answers 60
  days; the semáforo plans over 15.
- **Two suppliers can be primary for the same SKU** and which one wins is
  undefined (a dict with no `ORDER BY`, a `LIMIT 1` with no order). Nothing ever
  compares lead time, cost or MOQ between two suppliers: "which one is better to
  buy from" is answered nowhere.
- **The 20% holding cost is never shown** although it travels in the payload, and
  `/ventas` always writes it into `business_cfg`, so it looks configured without
  anybody having been asked. The dead-stock view uses a fixed **25%**.
## One refuted finding, and why it matters

The supplier agent reported as serious that typing in a stock count overwrites
the lead time and the MOQ with the model's defaults, sealing them as chosen by
the user. **It is false for existing rows.**

It read `data = body.model_dump(exclude_none=True)` at `inventory.py:145` and
stopped. At `:162` there is `data = {k: v for k, v in data.items() if k in
body.model_fields_set}`, and above it a comment describing that exact bug as
**already fixed**, with the measurement that found it ("a supplier minimum of 100
became 1 and the recommendation dropped from 100 to 81").

What is alive is the **new row** case, where the defaults are applied and sealed
as `user` — which is finding 1.2 through another door.

It is written down because the failure mode repeats: read up to the first line
that confirms the suspicion and stop. Everything marked **[verified]** in this
document was read in full before being written.

## What was tested and is solid

This is worth as much as the list of defects, because it says where **not** to
look:

- **The semáforo signal has a single authority.** `_calc_signal` is the only
  place the four values are derived; the aggregate, per-warehouse, briefing,
  email, PDF, CSV and the public API all read the string it produces. The
  frontend never re-derives it. The divergences in this document are in the
  **inputs**, never in a second threshold table.
- **The order-quantity formula exists exactly once**, and the reorder point, the
  recommendation and the breakdown all call the same `_safety_stock`.
- **Champion selection agrees between engine, backend and chart**, with
  `CHAMPION_METRIC_ORDER` pinned by a parity test. Only the browser disagrees
  (3.4).
- **The active session goes through `resolve_active_session`** on every HTTP
  endpoint and in both schedulers. The WhatsApp bot is the only bypass.
- **Transfer cycles are structurally impossible**, rounding never invents units,
  and the 3,692.67-unit regression is closed with a test guarding it.
- **SKUs with no stock in the file are excluded rather than guessed**, and they
  travel in the response so they can be shown: the one place where "no data" and
  "no suggestions" are properly distinguished.
- **The defaults cascade is correct and well argued**: a rule that leaves a field
  NULL says nothing, the scope travels to the UI, and it refuses to invent a
  `unit_cost`.
- **An MOQ ≤ 0 cannot reach the arithmetic**, thanks to four independent guards.
- **The economics of price breaks is genuinely good** — holding cost against the
  discount, a coverage cap, a materiality floor, and rejected opportunities come
  back with their reason. Its only defect is not filtering by supplier.
- **Supplier deviation alerts** use a robust IQR, one tail, sigma with a floor
  and n≥6, with the discarded alternative documented.
- **`compute_session_accuracy` is the "Best WAPE" defect properly repaired**: it
  reports the WAPE of the model the numbers came from, excludes baselines, and
  returns `None` instead of a triumphant 100% over a catalogue that did not sell.
- **`/hoy`'s KPI row is the best-behaved surface in the product** — "—" instead
  of a number when there is no count or no cost, the stale-data notice, and the
  sentence saying the figures do not mean "there is no risk" but "we do not
  know".
- **`/pronosticos`' sales-pattern tab** closes every caption with "This is not a
  prediction", has declared observation minimums and refuses on weekly data. The
  most honest panel in the app.
- **The landing's checkable mechanics do hold up**: "by the third reception" = 3
  exactly, the plan-limits table matches the code, and the semáforo threshold
  table matches `_calc_signal`.

## Order of work

1. ~~Fix `ConfirmDialog` — all three defects.~~ **Done** (`bd38436`): the pending
   resolver is settled before the second confirmation overwrites it, it closes on
   `Escape` and it traps focus.
2. ~~The six from the sweep that needed no new capability.~~ **Done**
   (`3e0bde6`): 1.1, 1.5, 1.6, 3.2, 3.5, the panel half of 1.2, and the 3.8 that
   appeared inside it.
3. ~~Levels 1 and 2 complete.~~ **Done** (2026-08-12): 1.3, 1.7, 1.8, 2.1, 2.2,
   2.3, 2.4, 2.5, 2.6, 2.8, plus the 1.9 that appeared inside. Two owner
   decisions were written into their findings: `moq` is a **minimum**, and
   `/impacto` is corrected **by copy**, not by changing figures that have already
   been emailed out.
4. ~~Level 3, except what is excluded.~~ **Done** (2026-08-12): 3.1, 3.4 and 3.7.
   3.2, 3.5 and 3.8 already came from `3e0bde6`; **3.3 is out of scope** by the
   owner's decision (WhatsApp bot) and **3.6 needs new capability**.
5. Level 4 — claims with nothing behind them. **In progress.**
6. Run the full suite **once, with nothing else on top** (`python
   scripts/run_tests.py`). The 2026-08-12 run took 3 hours instead of 36 minutes
   because a loose pytest and a parallel `tsc` were left running: that is load,
   not defects, and it ruins the timing-sensitive tests.
7. Walk `/api`: the redesign, a write end to end, upload and `train`.
8. Go down the table in section 2, screen by screen, fixing what appears and
   recording the walk in `screen-inventory.md`.
9. With a green light: raise the test tenant's plan and walk the three in
   section 3.

**Still open, and none of it is an oversight:**

| What | Why it was not done |
|---|---|
| The import half of **1.2** | Needs the `current_stock_set_by` column. New capability. |
| The overstock guard of **1.7** | Trimming below the supplier's minimum gives a quantity that cannot be ordered; flagging it the way `price_break_service` does is a new field on the row. |
| The "only the lead time is missing" list of **1.3** | A second list in the response and a new section on the screen. |
| The deduplication of "Export PO" in **2.2** | Three clicks still write three POs. Needs a decision on what a duplicate is. |
| The `9999` of **4.4** | `coverage_days = 9999` with mean demand 0 gives SOBRESTOCK. It changes the semáforo's verdict for a whole class of SKU: a product decision. |
| **3.3** (WhatsApp bot) | Out of scope by the owner's decision. |
| Four short ones from **4.7** | `sku_suppliers.*` reaches no planning path; two suppliers can be primary for the same SKU with no tie-break; the 20% holding cost is not shown and dead stock uses a fixed 25%. |

*(3.6 and levels 3 and 4 left this table on 2026-08-23: their headings mark them
fixed, and listing them here made it look as if more work was left than there
is.)*

---

## Two things that are the owner's decision, not fixes

- **Raising the test tenant's plan**, to walk scenarios, integrations, suppliers
  and the walls from the other side.
- **End-to-end frontend tests.** It is the only way "no bugs" holds over time
  instead of being repeated by hand on every change. But it is **new
  capability**, not a fix, so it does not start until it is asked for.

---

# 2026-08-16 — one plan only, and the event simulation walked

## The plans are gone (owner's decision)

Faro sold three tiers — starter / professional / enterprise — with a feature set
each and a Stripe subscription behind them. **Now there is a single product:
everything included, no ceilings on products, users, warehouses or sessions, and
the price is discussed with us.** What was done:

- **Backend.** `entitlements/plans.py` goes from a catalogue of three to one
  `PLAN` with the only two ceilings left, and they are infrastructural, not
  commercial: 8 concurrent jobs and 2 GB per file. `require_feature` and
  `has_feature` **were deleted** rather than left answering yes to everything: an
  authorisation that cannot say no reads as a guard and guards nothing. With that
  went ~40 route walls, the ABC-XYZ trim in `/dead-stock` and the two WhatsApp
  guards on the daily email and the freshness reminder.
- **Stripe, deleted entirely**: `backend/billing/`, its router, the webhook, its
  configuration settings and its test. The new migration **drops** the
  `stripe_events` table and the four columns that existed only for it, including
  `tenants.plan` — a dead column nobody reads is the one somebody reads by
  mistake two years later. `trial_ends_at` and `quota` stay: the first is still
  enforced, the second is how **one** customer's limit gets widened without a
  deploy, and it is now the only mechanism that does it.
- **`GET /entitlements`** no longer reports `plan`, `features` or
  `feature_plans`. It returns the trial state, the limits and `read_only`, which
  is the only thing that still decides anything. A feature map that answers
  `true` to everything only invites the browser to keep asking.
- **Frontend.** Out went `/planes`, the billing panel in `/mi-cuenta`,
  `FeatureGate` and the feature filtering of the sidebar and the command palette.
  The expired-trial notice no longer sends you to compare plans: it asks you to
  write to us.
- **Copy.** The landing loses the three-column table, the "which plan is for me?"
  and the tier mentions in the FAQ; the pricing section says what is true — one
  plan, everything in, the price is built around the operation — and offers to be
  written to. The user guide (`docs/help/index.html`) loses its 15 "Professional"
  badges and its limits table. `docs/public-api.md` stops saying "included from
  Professional" and stops promising a per-tier ceiling.
- **Tests.** The ones that existed to prove the wall were rewritten as the
  opposite: that the route **answers** with `testing_mode` off, which was the
  switch that turned the wall on. The limit tests are still alive with an
  explicit `quota`, because what they tested was the **bypass** — importing by
  CSV skipped `max_locations` — and that hole still deserves a guard even though
  the default number no longer exists.
## The event simulation, walked in a browser

With the wall gone, `/escenarios` and the "Events and seasons" panel of
`/inventario` were exercised by hand on 2026-08-16 against the hardware-store
tenant. What works, and what does not.

**Works, verified against the arithmetic:**

- **Demand change** ×1.4: 38.8 → 54.4 daily demand, 131 → 240 units.
- **Supplier delay** +7 days: lead time 9 → 16, signal **Pedir pronto → Pedir
  YA**, 131 → 422 units. This is finding **4.2** of this document seen from the
  browser: before, it answered "this scenario changes no decision".
- **Event simulation** ×2.0 at 103 days: 38.8 × 4 days × 2 = 310.6, order 311,
  "order before 18 November" (start − 9 days of lead time), ₡3,888.
- **Per-SKU multiplier** ×3.0: 465.9 units, and the row explains where the
  multiplier comes from ("per SKU" versus "from the event").

**Three defects, all three fixed the same day:**

### E1. The headline asked for an order with a date already past

An event 3 days out with a 9-day supplier: the headline said **"Order before 10
August"** — six days in the past — while the row for the same product said "today!"
and the red line below said it was already too late. Three claims about one
product, one of them impossible, and the impossible one in the largest sentence
on the screen.

`summary.order_before` is the **earliest** `order_by` among the products at risk,
whether it has passed or not. The sentence now takes the earliest that has **not**
passed; the ones that no longer arrive in time are covered by the red line, which
is the honest thing that can be said about them, and if none arrives in time the
sentence disappears.

### E2. Changing a product's multiplier did not re-simulate

Setting SPIKE-01 to ×3.0 saved the override and left the table showing ×2.0 and
311 units. The user changes the number the whole screen is derived from and the
screen keeps answering what it answered before, without saying it is stale.
`MultiplierExplainer` called `onEdited`, which reloaded **the event list** of the
screen behind and never the simulation. It now re-simulates, and deliberately
does **not** clear the previous result meanwhile: the editor the user is typing
in is only painted when there is a result, and emptying it would pull it out from
under their hands.

### E3. The headline and the footer described a calculation that never happened

With the override applied, the top said "+100% demand" and the footer "× 4 days ×
2.0" while the only row ran at ×3.0. Both numbers came from `ev.multiplier` — the
event's — and not from what was applied. They now come from
`multipliers_applied`, which the backend was already sending: if every product
shares a multiplier, the sentence names it; if not, it stops asserting a single
"+X%" and says each product carries its own (five new copy keys, es/en).

**What could not be seen on screen:** the mixed-multiplier case was verified over
the API (200 SKUs, one with an override: `multipliers_applied` returns two
entries) and in the code, not in the browser — that test's session is weekly and
the tenant is on daily, so the screen does not pick it up without changing the
period.

**Still unwalked in the simulator:** the "LatAm calendar" tab (seeding the season
catalogue), editing and deleting an event, the per-family and per-category
multiplier, and the simulator on a weekly or monthly tenant.

---

## 6. Technology — three infrastructure gaps (2026-09-01)

These came from reviewing what technology is missing for the app to be better
"in general", not from walking a screen. Verified in the code, not guessed: there
is no Sentry/structlog/OpenTelemetry in `backend/` (grep returns nothing), there
is no Redis/Celery (`workers/worker.py` uses a `ThreadPoolExecutor` + the `jobs`
table), and RAG search **already** runs on Pinecone + Voyage AI
(`backend/ai/rag_service.py`) — that is not missing, it is already there.

### a) The form login and the CSV upload had no script — **[DONE 2026-09-01]**

A correction to what this entry used to say: **it is not true that Playwright was
unused.** `Frontend/tests/smoke.mjs` has existed since 2026-07-29 and already
covers layout/render regressions on 8 screens — but it signs in by injecting the
token through `fetch('/api/auth/login')`, so it never exercises the `/login`
**form**, and it does not touch `/ventas` at all. Neither of the two is in
`run_tests.py`; they are run by hand, as `smoke.mjs` already was.

New: `Frontend/tests/critical_flows.mjs`, same style (raw `playwright`, no
`@playwright/test` — that dependency is not installed and there was no need to
add it). It covers what `smoke.mjs` did not:

- The `/login` form: a wrong password stays on the screen and shows the error;
  the right one gets in and stores a real token.
- New signup (`/signup`, `@faro-e2e.io` domain, does not touch the demo tenant) →
  email verification → login → upload `scripts/sample_sales.csv` in `/ventas` →
  reach the column-mapping step, with no console errors.

**A test-infrastructure defect, not a product one, found while writing it:**
`next dev` compiles each route the first time and attaches React's handlers only
after hydrating. A click on the button before that falls through to the **native
HTML submit**, a full-page GET to `/login?email=...&password=...` — the password
ends up in the URL and in the browser history, and React's state is lost.
`submitFormSafely()` detects it by the signature (a `?` glued to the same route)
and retries once after reloading. It does not show up in production (the build
has no cold-hydration window like this), but it confirms that **you must NOT
interact with a form before it finishes loading** — which holds for any script
touching these screens.

**Still unwalked:** generating a purchase order from the semáforo. It needed an
already-trained session (real data, minutes of compute) to have something to
generate the PO over, and it fell outside this pass's scope — it can be picked up
with a session seeded by `seed_demo.py` instead of training one from scratch.

Verified by running `node tests/critical_flows.mjs` against the real app
(backend :8011, frontend :5000, `faro_db` on :5544): 8/8, repeated.

**Polished 2026-09-01, after finding the clash with `demo@faro.app`:** the login
block no longer reuses the demo account — it uses the same fresh `@faro-e2e.io`
account as the rest of the run, so two consecutive runs do not collide with the
5-attempts/5-min limit of `POST /auth/login` (`auth.py:285`). Verified twice in a
row with no pause: 7/7 both times.

**Cleanup added:** `backend/scripts/cleanup_e2e_tenants.py` deletes every tenant
whose user is `@faro-e2e.io` (`ON DELETE CASCADE` takes the rest). The e2e script
runs it itself at the end of each run, best-effort. It found and deleted **261
tenants** accumulated since 2026-07-27 — they were not from this session, they
were months of earlier tests left uncleaned.

### b) There was no record of training metrics — **[DONE 2026-09-01, own table]**

Owner's decision: our own table in Postgres, not MLflow — zero new
infrastructure. `engine.get_metrics()`
(`ForecastingCore/forecasting_core/engine.py:848`) **already** computes
`by_model`: average MAE/RMSE/WAPE/bias/MAPE/SMAPE per model, on every run — it
just lived in `session_results.training_result`, a JSONB the next run
**overwrites**. There was nowhere to compare "this session's LightGBM against the
one from two weeks ago".

What was added:
- Migration `create_training_run_metrics` (`backend/db/migrations.py`): table
  `training_run_metrics` (`tenant_id, session_id, model` + the six metrics +
  `trained_at`), `UNIQUE (session_id, model)` — retraining the same session
  overwrites its row, exactly as `session_results` already does, instead of
  piling up duplicates.
- `backend/training/metrics_history.py` — `record_training_metrics()` does the
  upsert; `list_metrics_for_tenant()` is ready for whenever showing it on screen
  is decided (that was **not** done — it is new UI capability, outside this
  pass).
- `runner.py`, right after `engine.get_metrics()`: the write is in a **non-fatal**
  `try/except` — a failure there can never bring down a training run, the same
  pattern as the rest of that file's non-critical writes (`Inventory stock sync
  failed (non-fatal)`, etc.).

**Verified against a real DB, not only the mock:** two new tests in
`test_integration_forecasting.py` run the (mocked) training pipeline against the
real `faro_db` and `SELECT` directly on `training_run_metrics` —
`test_e2e_training_records_model_metrics_history` (the row exists, with the right
numbers) and `test_retraining_the_same_session_overwrites_its_metrics_row`
(retraining twice leaves **one** row, not two). All 10 tests in that file pass.

**What is missing and is a separate decision:** no screen reads this table yet.
Showing it (in `/historial` or `/pronosticos`, whichever) is exactly the kind of
new capability this document asks to be announced before building — the table
exists so that decision already has data to work with.

### c) `storage/` has no declared backup — **UNFIXED, owner's decision**

Uploaded datasets and trained-model artifacts live on local disk under `storage/`
(gitignored, confirmed in `CLAUDE.md`). There is no backup routine in the repo:
if the disk corrupts or the container is recreated without the volume, both what
the user uploaded and what training produced are lost — not just files, whole
sessions are orphaned. It needs an owner's decision on **the destination** (where
it is copied to) and **the frequency**, because the two reasonable options are of
different sizes: a scheduled backup script (cheap, touches no business code) or
migrating storage to something S3-compatible (larger, touches how files are read
and written throughout `backend/`).

---
## 7. Walk of /compras, /pedidos, /inventario (2026-09-01)

First pass at the owner's request to validate the rest of the screens, beyond
login/upload. The `claude-in-chrome` connector was unavailable (extension not
connected), so the walk used headless Playwright with the seeded demo tenant —
the same mechanism as `critical_flows.mjs`, leaving no permanent script behind
for this pass.

**What was verified:** the initial load and the visible content of the three
screens, with full-page screenshots. **What was not:** the internal flows the
table above already lists (sending to suppliers, recording an outbound, a new
manual order, etc.) — they remain unwalked.

### /pedidos — no findings

Loads cleanly with real data: 4 orders in the three states the demo produces (in
transit, partial, received), action buttons visible (Record arrival, Send order,
Open in WhatsApp, Copy message). Nothing to report in this pass.

### /inventario — no findings, one false positive discarded along the way

The 40-SKU semáforo, the KPIs and the per-warehouse filters load correctly.
**Methodological note, not a product one:** a first screenshot with a short wait
(6s) showed the SKU list completely empty under the search box — it looked like a
render defect. With a longer wait (14s) everything appeared; the content was
already in the DOM (confirmed with `getBoundingClientRect`: `opacity:1`, real
sizes, nothing collapsed) — the screenshot was simply taken in the middle of a
fetch still in flight. It is written down because any future script walking this
screen can fall into the same trap.

### /compras — one real finding: the executive summary races its own timeout

`NarrativeCard` ("Today's executive summary") calls `POST /ai/narrative/morning`
(DeepSeek) on every load of the product's main screen. Measured directly against
the backend: **8.8 seconds** of real response time. The client
(`compras/page.tsx:945-948`) gives it a local timeout of **8000ms** before giving
up to the rule-based text — that is, in the *normal* case, not the degraded one,
the local timeout expires **before** the real response arrives. The user sees the
generic rule-based text for a fraction of a second and, immediately, it is
replaced by the real AI text when the request does answer — a visible flicker on
a screen a buyer looks at every morning.

Verified on screen: with a 14s wait the box was still on "Analysing data…"; with
22s it already showed the full analysis ("Current situation", "Priority risks",
"Opportunities").

**A second, smaller related finding:** the "Refresh" button of that same box
(`onRefresh`, `compras/page.tsx:1522`) does not have the 8s timeout the initial
load does — if DeepSeek is slow or down, pressing "Refresh" leaves the spinner
turning with no way out until the request answers or fails on its own.

**What gets asked before touching the code:** raise the local timeout to ~10-12s
(less flicker, a little more visible wait) vs. cache the response per session/day
(the summary does not change unless the data does) vs. leave it as it is.
**Owner's decision (2026-09-01): raise the timeout.** Done —
`compras/page.tsx:945-950`, from 8000ms to 11000ms. Verified with Playwright: in
the confirmation run the box went straight from "not loaded" to "Analysing data…"
(t=13.4s) to the real AI text (t=21.5s), never showing the rule-based text in
between (`sawFallbackFirst: false`). The "Refresh" button finding, with no such
guard, is **still untouched** — it was not what was asked to be fixed in this
pass.

### /mi-cuenta — one real finding: the left column leaves a gap the size of the right one

`mi-cuenta/page.tsx:1333` puts `<ProfileSection />` and the other eight cards
(Usage and limits, Currency, Time zone, Language/Theme, Granularity, WhatsApp,
Team messages, Security) in a `1fr 1fr` `grid`, two columns, a single row.
`ProfileSection` is short — name, email, role, status — while the right column
stacks eight cards and is several times taller. CSS Grid stretches both columns
to the row's height by default (`align-items: stretch`, with no override), so the
left column ends up with a box as tall as the right one and **nothing to paint in
most of it**: a user scrolling down sees a short card on the left and, below it,
a white gap the size of the whole screen while the right keeps showing cards.

Verified with screenshots and with `getBoundingClientRect` on the "User profile"
node: the content exists and has a real size, it simply ends long before its
column does. It is not a data or loading defect — it is the `grid` without
`align-items: start` (or without redistributing the cards to balance the two
columns' height).

**Owner's decision (2026-09-01): fix it.** Done — `mi-cuenta/page.tsx:1333`,
`alignItems: 'start'` added to the grid. Verified with Playwright: before the
change the left column measured 2103px tall (the same as the right, with all that
empty space under the profile card); afterwards it measures 258px — its real
height, unstretched. A screenshot confirms the left column ending naturally after
"User profile" while the right carries on with its eight cards.

### /pronosticos, /proveedores, /archivos — no findings

All three load fully, with real data and no API call outside 2xx. `/pronosticos`
shows the forecast chart, the model selector, the metrics table and the "We found
problems in your data" banner — functional. `/proveedores` (which CLAUDE.md
marked as "never opened, only verified over the API") renders the full supplier
table with lead time, variability, learning and actions — now walked, with no
surprises. `/archivos` shows the connected data source ("Ventas Demo Faro", 21,640
rows) behind a guided welcome tour that fires in every new browser context (empty
localStorage) — expected in a script that does not persist a session between
runs, not a defect for a real user who closed it once.

**The two console warnings that appear on ALL the screens walked this pass** (not
only these three) — noted here once because they are systemic, not per-screen:
- `Warning: Extra attributes from the server: %s%s data-theme` — a React
  hydration mismatch on the `data-theme` attribute of `<html>`. It appears on
  every load, including `/login`. It was not investigated in depth this pass; if
  it shows up again while walking more screens, its cause is worth tracing.
- `Failed to fetch RSC payload... Falling back to browser navigation` — this is
  an artifact of the test method (`page.goto()` does a hard navigation between
  routes; a real user navigates with a client-side `<Link>`). Not reported as a
  product finding.

---

## 8. Service configuration: what the deployment needs and what turns off without it (2026-09-13)

**Owner's request (2026-09-10):** sell the source code. Make it clear which
environment variables it needs, make a missing key mean the service *simply does
not activate* — the chat, the alerts — and make all of that configurable with
every error handled. That day's session built the core and was cut off by the
weekly limit before wiring the consumers; this finishes it.

### The three gaps it had, and how they ended up

| Gap | How it was | How it ended up |
|---|---|---|
| `.env.example` lied by omission | documented 20 of 45 variables | **generated** from `backend/service_config/registry.py`, like `docs/configuracion.md`; a `Settings` field with no descriptor turns the suite red |
| Nobody could see the state | `/health` said `ok` with the chat, the alerts, RAG and the integrations dead | `/health` reports each service's state and survives a downed database (`degraded`, not a 500 with no body); the `/instalacion` panel shows it along with what is lost in each case |
| Degradation was uneven | notifications did it well, RAG and integrations halfway, and «configured but failing» was indistinguishable from «not configured» | all ten consumers read through `service_config.resolver`; the `degraded` state exists and comes from a real connection test |

### What was built

- **A single registry** (`registry.py`): 47 fields, each with what it does,
  whether it is required, whether it is secret, whether it can be edited from the
  app — and **what is lost without it**, written for whoever has to decide, not
  for whoever wrote the code.
- **Two layers with visible precedence**: the environment (the floor) and the
  panel (which wins, stored encrypted in the database with the integrations'
  Fernet key). The panel says which one wins per field. A secret goes in and does
  not come back out: reading returns the last four characters, and nothing
  reverses them.
- **A connection test** per service, with stable codes leading to different
  actions: `auth_failed` (bad key) ≠ `unreachable` (network) ≠ `timeout` (the
  provider accepted and went quiet). No test raises; it reports.
- **`/capabilities`** for any signed-in user: booleans, no variable names. It is
  what lets a screen say «the assistant is unavailable» *before* somebody types,
  instead of after waiting.

### The decision that was not mine, and needs to be known

`admin` is a role **inside a tenant**: every company that signs up has one. Using
it to edit the deployment's credentials would have let anybody who opens an
account rewrite everyone's DeepSeek key. **`INSTANCE_ADMIN_EMAILS`** was added
(environment only, never editable from the screen where credentials are pasted).
Empty = nobody edits the instance's configuration from the app, and the panel
says so, naming the variable. A tenant does configure **its own channels** (its
email sender, its WhatsApp number): the scope comes from the token, not from the
request body.

### What walking it in the browser found

Three real defects, all three fixed in the same step:

1. **The «no model» notice lived inside the composer**, which is only drawn with
   a conversation open. A user reached an empty state inviting them to start,
   created the conversation, typed the question and *then* found out. Moved above
   the branch: it is visible before the first click.
2. **The panel's error codes were not in the catalogue**, so the toast showed the
   backend's English prose. Nine new entries in `translations.ts`, each naming
   the fix.
3. **At 400px the two columns squeezed the input to the width of one word**: the
   field was on screen and impossible to type into. They now stack.

And a fourth that is not the panel's: `test_endpoints.py` still demanded a trial
clock (`trial_ends_at`) that the 2026-08-22 tier change removed, and
`test_pagination_pages_are_disjoint_and_complete` created 5 sessions against a
free ceiling of 3. Two reds accusing the product of bugs it did not have.

### How it was verified

- 47 new tests (`test_service_config.py`, `test_service_config_i18n.py`):
  precedence, types, secrets, refusals, the permission pair on every write, and
  that the copy catalogue covers exactly the registry's keys.
- Walked in the browser with the key set **and without it**: saving takes effect,
  the origin label changes, emptying it returns to the environment, the
  connection test reaches DeepSeek, the tenant does not see the installation's
  credentials, and with the key removed nothing 500ed — the app says what is
  missing and keeps working.

---

## 9. The suite does not declare what it needs: it inherits a `.env` (2026-09-13)

The first full run after the configuration work gave **48 failures**. None was a
product defect. **34 of them had a single cause: `backend/.env` with
`TESTING_MODE=false`.**

CLAUDE.md says the local `.env` runs with `TESTING_MODE=true`, and the whole
suite is written against that — the house rule is that *a test that depends on
quotas turns the mode off itself*. With the quotas alive, the free plan's ceiling
of `max_locations = 1` fires at `inventory/service.py:164` as soon as a test
seeds a **second warehouse**, and the eight affected files are precisely the
multi-warehouse ones. Measured, same tree, changing only the variable:

| File | with `false` | with `true` |
|---|---|---|
| `test_status_by_warehouse.py` | 10 failures | 12 pass |
| `test_transfer_lanes.py` | 9 failures | 25 pass |
| `test_optimizer_service.py` | 5 failures | 14 pass |
| `test_warehouses.py` | 3 failures | 16 pass |
| `test_inventory_multi_bodega.py` | 3 failures | 3 pass |
| `test_reception_bodega.py` | 2 failures | 8 pass |
| `test_warehouse_import_destination.py` | 1 failure | 7 pass |
| `test_stock_upsert_preserves_config.py` | 1 failure | 10 pass |

**96 pass, 0 fail.** There was nothing hidden behind the 403.

What was expensive was not the fix — one line in a gitignored file — but who it
accused: for half an hour the report said that demand sharing between warehouses,
the transfer lanes and the optimiser's input were broken. A `.env` nobody sees in
`git status` pointed the finger at the code that costs the most to review.

**FIXED on 2026-09-14.** The suite no longer inherits the mode: an autouse
fixture in `conftest.py` pins it to `True`, and the 68 tests that depend on a
quota turn it off themselves, which is what the standard already asked for.
Checked by deliberately putting `TESTING_MODE=false` in the `.env` and running
the five files that fell over today: **70 pass, 0 fail**. The configuration that
this morning turned 34 tests red accusing the optimiser can no longer do it.

The remaining 14 failures of that run: 4 were test doubles with the old signature
(the `tenant_id` the senders gained that day), 1 was the Spanish guard against
the new documentation generator, 1 was a stale assertion asking for the trial
clock that the 2026-08-22 tier change removed, and the rest went to separate
verification on suspicion of load — the run was done with 1.4 GB free and took
111 minutes instead of 40.

---

## 10. The virgin install, which is the only one that matters for selling (2026-09-14)

The services panel had existed since 2026-09-13 and **did not work on day one**,
which is exactly the day it was built for. Two locks, each reasonable on its own:

1. **Nobody could open it.** An empty `INSTANCE_ADMIN_EMAILS` meant "nobody
   edits", and a freshly installed deployment has that variable empty by
   definition. The buyer was sent off to edit the file and restart the container
   — which is what the screen exists to end.
2. **Whoever did open it could save nothing.** Saving a secret needs a Fernet
   key, the key came only from `INTEGRATIONS_SECRET_KEY`, and a new installation
   does not have that either. In other words: the first thing anybody tries —
   pasting the DeepSeek key — failed.

Both resolved without loosening security:

- As long as the deployment has **exactly one tenant** and has named nobody, that
  tenant's admins operate it. Whoever installs is whoever signs up. **It ends at
  two**: as soon as a second company exists, "the only tenant" stops meaning
  ownership and would start meaning "whoever signed up first", which would be a
  door to everybody's credentials. The panel warns while there is still a single
  company and time to react.
- An empty `INTEGRATIONS_SECRET_KEY` no longer means "off" but "make me one": it
  is generated at `storage/instance_secret.key` on first use. The environment
  always wins. Both costs are stated at the moment they happen — back up
  `storage/`, and promote the key to the variable before running a second process
  on another volume — in the log and in the panel.
- An unreachable database **grants nothing**: `sole_tenant_id` returns None if the
  query fails. Failing "open" there would have handed the panel to any admin the
  moment Postgres coughed.
- A read-only disk still turns encryption off, with the reason stated and never
  degrading to plain text.

### How it was verified, which is the part that counts

`test_virgin_install.py` (14 tests, none skipped) turns **every** optional
credential off and exercises what depends on them: no endpoint answers 5xx over a
missing key, everything names what is missing, the core does not notice, and the
two scheduled 8:00 loops **finish** instead of blowing up towards the scheduler
and taking the other tenants' alerts with them.

And then, outside the tests: **a clean clone of the repo, an empty database,
three variables in the `.env`, nothing else.** It boots, the owner signs up,
opens the panel, **pastes the DeepSeek key into the screen**, and
`capabilities.assistant` turns `true` without restarting anything. The secret
does not come back out: reading gives four final characters. Zero problems across
the seven steps.

---

## Service-level finding (2026-09-14) — **[CLOSED 2026-09-15]**

`backend/inventory/service.py` resolved z with `_Z.get(service_level, 1.645)`:
any level outside the table's four silently received the 95% cushion. **Fixed** —
`_z_for` now computes with Acklam's approximation and clamps to the range the API
accepts.

What is worth looking at, and **was not touched** because it is the other layer:
`ForecastingCore` has its own path (`InventoryAdvisor`, with `scipy.ppf`) and its
two boundary tests are written like this:

```python
# CRITICAL FIX REQUIRED: service_level=1.0 causes ppf(1.0)=inf
def test_service_level_boundary_1_causes_inf(self):
    with pytest.raises(Exception):
        ...
```

It is the same pattern that kept the backend's defect alive for months: the test
does not check that the behaviour is correct, it checks that the symptom occurs,
and `pytest.raises(Exception)` accepts any exception — including a `TypeError`
from a changed signature. While the test stays green, nobody finds out whether
the engine clamps, blows up or returns infinity.

**It is not urgent**: the API rejects with 422 anything outside [0.5, 0.999]
(`test_service_level_boundary_via_api`), so those values only arrive from an
internal caller or a stored default.

**Closed on 2026-09-15, and the owner's decision turned out not to be needed.**
Looked at closely, the engine **already rejects**: `InventoryAdvisor.__init__`
validates the open interval (0, 1) and raises a `ValueError` naming the argument
and giving typical values. There never was an `inf`. What was rotten were the two
tests, which checked that the symptom occurred and therefore passed **before and
after** the fix they were shouting for in a comment.

Replaced by what the advisor actually promises:

- four invalid levels (1.0, 0.0, -0.1, 1.5) rejected with a `ValueError` that
  mentions `service_level` — not `Exception`, which accepts even a `TypeError`
  from a changed signature;
- five valid levels (0.5 … 0.999) with a finite z, and a finite reorder point and
  safety stock;
- **the property the buyer uses**: raising the service level never buys less
  cushion. That is the one that would have exposed the backend's defect — a z
  collapsed onto a single value — and no single-point assertion sees it.

---
## 11. Parallel-agent sweep (2026-09-15) and its fixes (2026-09-16)

**Written in English on the owner's instruction (2026-09-16, "todo en inglés").**
The rest of this document is historical Spanish; converting it is a separate
pass and is not started here.

**Method, so each line carries its real weight.** Six agents in parallel, one
per surface, using the `silent-failures` skill as the lens and forbidden from
touching code: they report, they do not fix. Each was asked for `file:line`, a
concrete failure scenario, and an explicit statement of whether it had CONFIRMED
the whole path or was inferring. 36 findings. Of those, **9 were verified by
hand against the code** (marked ✅); the rest carry the label the finder gave
them. Two were established with a runnable probe rather than by reading (📏).

Surfaces chosen were the ones `inventario-pantallas.md` reports as never walked:
integrations, suppliers, exports, stock movements, scheduled work, and the whole
frontend.

**Status: all 36 closed.** 23 were fixed on 2026-09-16 as part of the sweep.
The other 13 were open because the fix was a product choice rather than a code
change; the owner made those four calls the same day and they were built. §13
records each decision and what it cost. The one that is not closed in full is
**11.5**, where the wrong CONSEQUENCE is fixed and the ERP fetch itself still
needs a real account to read against — deliberately, because guessing a payload
there writes wrong stock, which is the defect being fixed.

---

### Level 1 — the product lies or goes quiet, and it costs money

**11.1 Every analyst was excluded from every alert — [FIXED 2026-09-16]** ✅
`inventory/service.py:3533`, `:3544`, `:3560`, `notifications/freshness_service.py:231`
— all four recipient queries read `role IN ('admin', 'manager')`. **`manager` is
not a role this product has**: `VALID_ROLES` is {admin, analyst, viewer}
(`users/roles.py`), `users.role` **defaults to `analyst`**
(`db/migrations.py:172`), and `analyst` is also what the invite dialog proposes.
So the real recipient set was admins only. Meanwhile `/mi-cuenta` invites any
role to link WhatsApp "to receive inventory alerts", walks them through the OTP
and shows a green **Verificado**. That person then received nothing — no
stockout digest, no lead-time warning, no freshness reminder, no monthly recap,
on either channel — and was not written to `activity_logs` either, so their
alert bell was empty too. An excluded person and a quiet week looked identical.
Now `('admin', 'analyst')`; `viewer` stays out on purpose, because a stockout
digest is a call to action addressed to whoever can act on it.
*Tests:* `test_agent_sweep_fixes.py::TestAlertRecipientsIncludeAnalysts` (5),
including a source guard so nothing asks for a non-existent role again.

**11.2 A thousands-dot file imported every quantity divided by 1000 — [FIXED 2026-09-16]** 📏
`utils/stock_import.py:296` (and `has_decimal_comma`, `:219`). The file-level
verdict only detects a decimal **comma**; there is no mirror rule for
dot-as-thousands. Measured against the real module: `["1.250","980","12.500"]` →
`1.25`, `12.5`. No row errors, the wizard says "1,200 products imported", and
the whole catalogue drops to PEDIR_YA. Adding one `3,50` anywhere in the file
fixes it — which is why every hand-made test file passes. `/bulk/preview`
returns `sample_rows` with the parsed values and `StockImportWizard.tsx` does
not render them, so there is nowhere to catch the 1.25 either.
**Why it was open, and how it closed (see §13):** the safe fix is to stop guessing and ASK — the same
thing the upload gate already does for other ambiguities — and that is a new
question in the import wizard, i.e. a new screen state. Owner's call. The
cheaper half (render `sample_rows` in the wizard so the user sees `1.25` before
committing) is also a UI addition and is bundled into the same decision.

**11.3 The nightly sync walked past `max_sessions` and never stopped — [FIXED 2026-09-16]** ✅
`integrations/sync_service.py`. The sync enforced `max_skus` and `max_locations`
and then called `create_session` bare. The other two callers do check
(`api/v1/sessions.py:33` under a `limit_guard`; `demo.py`'s comment says in so
many words that skipping it would let a tenant bypass the cap). The third is the
only one that runs **unattended**: a free tenant read 4 of 3 on day four and 60
of 3 on day sixty, and left one full sales CSV in `storage/` per connection per
night, with no prune. Now checked twice — once at the top of `sync_connection`
before any provider call, so a tenant already at the cap does not pay for the
fetch, the stock upserts and the dataset write; and once atomically under
`limit_guard` around `create_session`, which is the check that actually holds
against a person starting a forecast in the browser at the same moment.
**Note for the owner:** this makes the nightly sync fail loudly on a full free
tenant (the error lands on the connection row and the /integraciones card), and
that is what a ceiling does everywhere else in this product. If you would rather
a sync REUSE its own session instead of consuming a slot every night, that is a
different design and needs a column on the connection — say so and it changes.

**11.4 Accepting a price break raised the quantity and never the price — [FIXED 2026-09-16]** ✅
`Frontend/src/app/compras/page.tsx` — `applyStepUp` called `changeQty` alone, and
`price_break_service.effective_unit_price` had **no caller outside its own
evaluation** (verified by grep). So the panel promised "order 500 instead of 100
and save ~1,400", the cart total went *up* by the extra units at the old price,
and the old price was what the decisions payload wrote into
`inventory_po_items.unit_cost` — the single authority for the PDF the supplier
receives, the cash-calendar payable, /impacto's managed purchase value and the
scorecard's `purchased_value`. The saving reached nothing Faro stores or prints.
Now the rung's `step_unit_price` travels with the quantity, and `unit_margin` is
recomputed from it so the cart does not report the old margin on the new price.

**11.5 ERP stock all lands in `principal` while sales carry the branch — [CONSEQUENCE FIXED 2026-09-16]**
`integrations/alegra.py:68`, `siigo.py:85` hardcode `warehouse="principal"`,
while `fetch_sales` reads the real warehouse off each invoice — which sets
`has_store=True` and trains per `(sku, store)`. For a multi-branch distributor
Norte and Sur then resolve `current_stock = 0` → **PEDIR_YA at full reorder
quantity for the entire catalogue at every branch**, with the goods sitting
there; and `principal`, which holds the units, reads `SIN_DATOS`.
**Why it was open, and how it closed (see §13):** the fix needs each provider's per-warehouse inventory
endpoint, and neither module's docstring claims to have verified that shape
against the live API. Guessing a payload here writes wrong stock, which is the
defect we are fixing. Needs a real account to read against.

**11.6 A scheduled retrain runs on the COMPLETED session and can blank the product — [FIXED 2026-09-16]**
`workers/worker.py:217` calls `create_job` bare. The user-facing path
(`api/v1/training.py:38`) validates state, configs and the active-job cap and
transitions to QUEUED; the scheduler does none of it. When the run raises,
`runner.py:1638` marks FAILED the session that was serving the whole app;
`resolve_active_session` filters on COMPLETED, so /hoy, the semáforo and the
digest go quiet, and the digest's `if not sid: continue` writes no row at all.
The only trace is `scheduled_jobs.last_error`, on a screen nobody opens because
nothing announced a problem. Same line, second effect: no per-session dedupe, so
an hourly preset over a >1h training queues B while A runs and both write
results for one `session_id`.
**Why it was open, and how it closed (see §13):** "retrain" can mean *refresh this session in place*
(today's behaviour, and the failure mode above) or *create a new session and
switch to it once it succeeds* (safe, but it consumes a saved-forecast slot per
run — see 11.3). That is the owner's product decision, and it is the same
decision as 11.3.

**11.7 Every `/inventario` export ignores the open warehouse tab — [FIXED 2026-09-16]**
`GET /inventory/status/export-po` (`api/v1/inventory.py`) **has no warehouse
parameter at all**; it re-derives the list at network level. The download menu
sits in the page header, above the warehouse selector, and stays enabled with a
warehouse tab open. The buyer reads "Norte needs 40" and downloads a CSV saying
150 — and `logPOGeneration` writes that into `/pedidos` as an order they never
saw. This is §3.1 recurring on the warehouse axis instead of the period axis.
"Export edited" is worse: it iterates the network list, so the per-warehouse
edits are not even in scope.
**Why it was open, and how it closed (see §13):** the honest fix is a `warehouse` parameter on the
endpoint — a new API capability — or disabling the menu while a warehouse tab is
open, which is a UX decision about a button people use. Owner's call; the
recommendation is the parameter.

**11.8 Shrinkage always decrements `principal` — [FIXED 2026-09-16]**
`inventory/shrinkage_service.py:70` resolves `principal`, while the modal shows
stock **summed** across warehouses and never asks which one
(`inventario/page.tsx:1001` never sends `warehouse`, though the API model
accepts it). A crate breaks in Norte, the units come off principal: the tenant
total still reconciles, so nothing looks wrong, while the per-warehouse semáforo
believes Norte holds 400 units that do not exist. Loud variant: a tenant whose
stock arrived with a warehouse column has no `principal` row at all, so every
shrinkage 404s blaming the SKU for a warehouse the user never chose.
`record_shrinkage` also skips `resolve_canonical_name`, so `norte` against an
existing `Norte` 404s too.
**Fixed half:** `record_shrinkage` was the only stock write path that skipped
`resolve_canonical_name`, so `norte` against an existing `Norte` 404'd. It now
normalises like every other path (*test:*
`TestShrinkageResolvesTheWarehouseSpelling`).
**Why the rest was open, and how it closed (see §13):** the modal has to ASK which warehouse — a new
control on a screen that was deliberately simplified down to 26 controls
(§1.septies). Owner's call.

**11.9 A monthly ERP re-import reverts every hand-corrected lead time — [FIXED 2026-09-16]**
`inventory/service.py:680` has `only_fill_missing: bool = False`, and
`_fields_to_fill` exists precisely so that "a lead time the buyer corrected by
hand in March is not silently reverted by April's ERP export". The only caller
passing `True` is a test; `POST /inventory/bulk` takes the default. Worse,
`upsert_stock` then re-stamps provenance to `'file'`, so the UI can no longer
badge the value as the tenant's own either.
**Why it was open, and how it closed (see §13):** whether a re-import overwrites or fills gaps is a
choice the user has to make per import, which means a toggle in the wizard.
Owner's call.

---

### Level 2 — numbers that do not mean what their label says

**11.10 "Deshacer" on `/compras` logged a REJECTION — [FIXED 2026-09-16]** ✅
`compras/page.tsx` wired the undo button on an approved line to `onReject` →
`rejectItem`. The code already knew better: `unapproveItem` exists for this case
and its comment says so — "the buyer is undoing their own tap, not telling us
the recommendation was bad, and rejections are logged as adoption feedback". It
was wired into the mobile card and never reached the desktop one, so the two
views recorded different things for the same gesture, and the rejection reached
`log-po`, persisted on `inventory_po_items` and contaminated /impacto's adoption
rate. `ActionCard` now takes an explicit `onUndo`.

**11.11 The scorecard's "we are not sure" flags never reached the screen — [FIXED 2026-09-16]**
`reception_service.py:576` produces `on_time_measurable` / `fill_rate_measurable`
with a comment naming the defect ("one reception printed 100% in bold green"),
and `proveedores/scorecard/page.tsx` printed the raw number. Neither field
appeared anywhere in `Frontend/src`, nor in `SupplierScorecardRow`. The fix had
shipped backend-only and the defect it describes was still live on the page.
Both flags are now declared and honoured: below the sample floor the number
still shows, marked provisional and in the dim colour, instead of bold green.

**11.12 Fill rate punishes orders still in transit — [FIXED 2026-09-16]**
`reception_service.py:533` includes `partial` and `not_received` POs, summing
received against the full `final_qty`, with no exclusion for deliveries whose
window has not closed. A supplier with two half-delivered orders, both on
schedule, prints **50%** — presented as a performance verdict. The one who has
shorted nothing reads worst on the page.
**Why it was open, and how it closed (see §13):** "still in transit" needs a definition — expected date
+ grace, or simply excluding POs inside their declared lead time. That is a
business rule, not a code fix.

**11.13 Lead-time alerts grouped by exact-case supplier name — [FIXED 2026-09-16]**
`supplier_health_service.py:262` normalised with `LOWER()` for **ordering only**
and then grouped on the raw spelling, while the scorecard,
`get_learned_lead_times` and `_effective_lead_time` all group case-insensitively.
`receive_po` stores whichever spelling that PO carried, so eight receptions from
Acme split 5/3 across "Acme" and "ACME" produced two series, neither reaching
`MIN_BASELINE + MIN_RECENT`. The supplier's lead time had doubled and neither
the banner nor the 08:00 email fired — and a split history was indistinguishable
from too little history. Now grouped by casefolded key, with the first-seen
spelling kept for display so the alert keys the same way the scorecard row does.
*Test:* `TestLeadTimeDeviationGroupsCaseInsensitively`.

**11.14 The price-break panel quotes a supplier the buyer already changed — [FIXED 2026-09-16]**
`compras/page.tsx:1079` sends only `{sku, quantity}`; the supplier comes from
`status_items`, not from the cart, and the effect's dependency is `sku:qty`, so
switching supplier does not even re-evaluate. It is exactly what
`evaluate_cart`'s own docstring says it fixed, reintroduced through the
supplier-switch path.
**Why it was open, and how it closed (see §13):** the evaluate endpoint needs a `supplier_id` per line —
a new field on a public request model. Small, but it is an API change.

**11.15 Stock snapshots have no warehouse column — [FIXED 2026-09-16]**
`db/migrations.py:381`. `/inventario`'s sparkline and the briefing's
`demand_trend_pct` are computed over interleaved series: principal 500 and Norte
20 give `500, 20, 500, 20…`, and `_calc_demand_trend` reads that difference as
real consumption — "+585% demand" that never happened. A single inter-warehouse
transfer produces the same artefact on its own.
**Why it was open, and how it closed (see §13):** a migration plus a backfill decision for existing rows
(there is no way to attribute historical snapshots to a warehouse after the
fact). Owner's call on what happens to the history.

**11.16 The WhatsApp digest reported days to weekly tenants — [FIXED 2026-09-16]**
`notifications/whatsapp.py`. The defect already fixed **for email only**:
`build_inventory_alert_text` had no `period` parameter at all, so no caller
could have passed one. The 08:00 email said "4 semanas" and the WhatsApp sent in
the same loop iteration, off the same list, said "4d" — on the channel with the
highest open rate in the region. The compact labels now live in the locale
catalogue (`unit_*_short`) next to the long ones, with the period→stem map
shared by both channels, because two copies of that map is how they came to
disagree.
*Tests:* `TestWhatsAppDigestSpeaksThePlanningGrain` (4), including one that
asserts the two channels agree on the same list.

**11.17 "Send now" previewed something else — [FIXED 2026-09-16]**
`api/v1/inventory.py` resolved the period for the status and then called the
email **without passing it**, falling back to the daily default — while its own
docstring says a test fire that skips one of the loop's steps "would prove less
than it appears to". It also linked to `/inventory`, which redirects to a
different screen than the loop's `/hoy`. Both fixed.

**11.18 An unknown unit cost was exported as a confident 0 — [FIXED 2026-09-16]**
`compras/page.tsx` and `inventario/page.tsx` computed `qty * (unit_cost ?? 0)`
under a header that says "estimated value" — exactly what `po_pdf.py:69`
documents having fixed for the PDF ("a line whose cost nobody recorded is priced
as UNKNOWN, not as zero… the supplier has no way to tell that apart from a price
the buyer meant"). The backend CSV had the mirror bug, `cost or ""`, which
merged a real cost of 0 with "unknown". Both now distinguish None from 0.

**11.19 Duplicate import rows won silently and the count over-reported — [FIXED 2026-09-16]**
`api/v1/inventory.py` de-duplicated `new_keys` for the ceiling check but not the
write loop, and `imported` counted calls, so an ERP exporting one row per branch
under an unmapped header put every branch on `principal`, where the last row won:
300 + 200 + 40 was stored as 40 while the toast said "3 of 3". Rows are now
collapsed per `(sku, warehouse)` **field-wise** (two rows for one SKU often carry
different columns, so a wholesale last-row-wins would lose data the file did
contain), the response carries `duplicate_rows`, and `total_rows` counts what was
read while `imported` counts what was written. `sede`, `punto de venta` and
`pdv` were also added as warehouse aliases — `sucursal` and `tienda` were there
and the Colombian ERP's own word was not.
*Tests:* `TestBulkImportReportsWhatItActuallyWrote` (4, incl. a permission pair).

**11.20 The profile name saved, said "Guardado", and showed the old one — [FIXED 2026-09-16]** ✅
`mi-cuenta/page.tsx` called `getUser()`, which re-parses `localStorage` on every
call, so `if (me) me.full_name = ...` mutated a throwaway object. `fp_user` is
written only by `setAuth`, which only runs at login, so the old name survived
reloads, the sidebar footer and the /compras greeting until the next login. The
save had worked and nothing on screen admitted it. Added `patchUser` to the auth
layer; the section now holds the user in state and the handler has a `catch`
that renders the failure through `useErrorDetail` (it was `try/finally`).

---

### Level 3 — friction, raw errors, and dormant traps

**11.21 "Export all SKUs" froze the tab for good — [FIXED 2026-09-16]** 📏
`Frontend/src/lib/excel.ts`: `safeSheetName` truncates to 31 characters, so
appending `_2` to a name already 31+ characters produced the identical string
and `while (used.has(name))` never terminated — synchronously, on the main
thread, right after the progress counter reached N/N. Two SKUs sharing their
first 31 characters was enough. The suffix is now placed INSIDE the 31-character
budget; measured with a probe: terminates, unique, within the limit.

**11.22 The language switch people actually use was undone by `/mi-cuenta` — [FIXED 2026-09-16]**
The sidebar ES/EN toggle (on every screen) and the Ctrl-K palette called
`setLang` only, so the choice lived in `localStorage`; `/mi-cuenta` then called
`getPreferences()` on mount and wrote the server's untouched value back. The app
flipped to Spanish mid-session while the ES/EN buttons on that very screen showed
ES as selected, and the tour copy promises "both are saved to your account".
Both contexts now persist through `lib/persistPreference`, which covers every
caller at once instead of each remembering.

**11.23 Raw backend prose as on-screen error text — [FIXED 2026-09-16]**
`hooks/useAutoSession.ts` did `e.message`, and since every `ApiError` **is** an
`Error` the Spanish fallback beside it was dead code (and a hardcoded Spanish
literal in logic, which the repo forbids). `ApiError.message` is
`detail || "HTTP <status>"` and a network failure is constructed as status 0, so
the buyer's main screen rendered a large centred **"HTTP 0"** offline, and the
backend's English sentence on a 500. Fixed there and at the other three sites:
the supplier send on `/compras`, the password change on `/mi-cuenta`, and
`/integraciones`. The hook now returns the raw error and each screen renders it
through `useErrorDetail`, which already translates `error_code` + `params`.
Two behaviours went with it: a failed send is no longer drawn as an amber
*skipped* line (same shape as "this supplier has no email on file") and the Send
button stays, so there is a retry; and `/integraciones` reloads on the failure
path too, so the card stops showing a green **Conectado** while the row says
`status='error'`.

**11.24 The frontend CSV writers escaped nothing — [FIXED 2026-09-16]**
Both wrote the same `purchase_order.csv` the backend carefully protects, by raw
interpolation: an embedded `"` shifted every later column in a document a
supplier acts on, and a supplier name starting with `=` or `@` executed on open.
Now both go through `lib/csvWriter`, which mirrors `backend/utils/csv_safe.py`.

**11.25 Purchase-order CSVs had no UTF-8 BOM — [FIXED 2026-09-16]**
`api/v1/inventory.py` (both writers) and the two frontend ones. Headers come
from the locale catalogue (`Señal`, `Días cobertura`), and
`Frontend/src/lib/csvCheck.ts` already prefixed the BOM on its template — the
product knew and applied it in one writer out of five. Excel on a Spanish-locale
Windows read `SeÃ±al` and `Distribuidora PeÃ±a`.
*Test:* `TestExportedCsvIsHonestAndOpensInExcel`.

**11.26 Three downloads skipped the silent token refresh — [FIXED 2026-09-16]**
`lib/api.ts` — `downloadInventoryPDF`, `exportInventoryPO` and
`downloadInventoryTemplate` did a bare `fetch` and threw `'HTTP ' + status`,
while the shared helpers 700 lines above handle a 401 with `tryRefresh()` and a
retry. Access tokens live 15 minutes and the refresh is reactive, so reading the
semáforo for twenty minutes and pressing "Exportar OC" printed **"HTTP 401"** in
the banner: no file, no PO logged, no hint that reloading would fix it. All
three now go through `downloadBlob`.

**11.27 Leaving `/ventas` did not stop training, and yanked you to `/compras` — [FIXED 2026-09-16]**
`ventas/page.tsx:1034` — the poll is a self-recursive async closure with no
cancellation flag and no unmount cleanup, unlike every other effect on that page,
and on completion it called `router.push('/compras')`. Four minutes after
navigating away the app moved itself, discarding whatever was in the form. A
mount-scoped flag now stops the loop and, above all, the navigation.
**Still open, separately:** there is no cancel endpoint in the client, so the
only way to stop a run you regret is to close the tab — which is what the screen
tells you not to do. That is a new capability; owner's call.

**11.28 The daily and monthly loops keep no last-run marker — [FIXED 2026-09-16]**
`workers/worker.py:245`, `:307`, `:337`. Each iteration computes `next_run` from
`datetime.now()`; nothing is persisted, unlike `scheduled_jobs.last_run`. A
worker killed at 07:55 and restarted at 08:02 makes `_next_daily_run` return
*tomorrow*: that day nobody gets a digest, a lead-time alert or a freshness
reminder, and no activity row is written, so it looks like a calm day. The
monthly variant skips the overstock snapshot and permanently breaks that month's
"capital freed" figure.
**Why it was open, and how it closed (see §13):** it needs somewhere to persist "last fired", i.e. a
table or a column. That is a new field; owner's call.

**11.29 `or 0` in the providers defeated "never invent a zero" — [FIXED 2026-09-16]**
`integrations/alegra.py:67` and `siigo.py:84` did `.get(...) or 0` **before**
`parse_provider_number`, whose contract (`base.py:66`) is that "the ERP sent
something we could not read" and "the ERP sold none" stay different facts.
`_merge_products_and_stock` acts on that — a None leaves `current_stock` unset so
the tenant keeps the count they had — and the `or 0` destroyed the distinction
one layer above it. The same applied to sale quantities, where
`_build_sales_csv` reports unreadable lines instead of teaching the model a day
with zero sales. All four now pass the raw value; the DTO types say
`Optional[float]` so the intent is visible.
*Tests:* `TestProvidersDoNotInventZeros` (4, incl. one asserting a real zero
still writes a zero).

**11.30 A failed send was filed under the wrong reason — [FIXED 2026-09-16]**
`supplier_health_service.py:378` and `roi_service.py:725` called
`failure_reason()` with no argument while the send two lines above passed
`tenant_id`. The bare call asks the INSTANCE config, so a tenant running its own
Resend key was told "no transport configured" — pointing the admin at an
operator setting instead of at the credential they own and can fix; the mirror
case reported `transport_error` and never named the real cause. Fixed in all
three places (the WhatsApp one in `service.py` had it too).
*Tests:* `TestFailureReasonIsScopedToTheTenant` (2).

**11.31 ERP values were stamped as "the buyer typed it" — [FIXED 2026-09-16]**
`integrations/sync_service.py` omitted `source=`, so it took the `SOURCE_USER`
default — whose vocabulary means "the buyer typed it on the SKU card", and whose
docstring says the dataset sync passes `'file'` explicitly. `unit_cost` is a
provenance field, so the row claimed human authorship for a number Alegra sent.
Now passes `SOURCE_FILE`. (A dedicated `SOURCE_INTEGRATION` would be more
precise and is a new value in `VALUE_SOURCES` — not added unprompted.)

**11.32 The supplier form pre-fills the system default — [FIXED 2026-09-16]**
`proveedores/page.tsx:125`, `:438` always send `lead_time_days`, so
`_stamp_lead_time_provenance` records `SOURCE_USER` for every supplier created
in the UI. Three backend call sites gate on `lead_time_set_by` precisely to keep
Faro's own assumption from being reported as the supplier's promise; the create
form defeats that guard, and the scorecard prints **DECLARADO 15d** for a
supplier who declared nothing.
**Why it was open, and how it closed (see §13):** the field is visibly pre-filled in a labelled required
input, so this sits on the line between defect and design. Leaving it blank with
a placeholder is the fix, and it changes a form the owner has seen.

**11.33 A PO line that fails to insert is swallowed — [FIXED 2026-09-16]**
`roi_service.py:155-180` logs a warning while the header keeps its full
`sku_count` and `total_value`. The line disappears from the supplier's
`fill_rate`, from `purchased_value` and from the PDF the supplier receives,
without telling anyone.
**Why it was open, and how it closed (see §13):** the alternative is to fail the whole PO generation,
which loses the buyer's work. Doing it properly means a transaction around the
header and its lines — a real change to that write path, worth doing
deliberately rather than as part of a sweep.

**11.34 An import row that fails to write is dropped with a log — [FIXED 2026-09-16]**
`inventory/service.py:730`. `imported` does shrink, so the number is not a lie,
but "83 products imported" after a clean 120-row preview is the only signal and
nothing names the 37 rows or why. PLAUSIBLE: the path is confirmed, the trigger
was not demonstrated.
**Why it was open, and how it closed (see §13):** the response already has an `errors` channel; wiring
write-stage failures into it is straightforward, but the row-level reasons need
copy, and the wizard needs to show them. Bundle with 11.2.

**11.35 Reconnecting left the stale gate verdict behind — [FIXED 2026-09-16]**
`integrations/store.py:33` cleared `last_error` but not `last_error_code` /
`last_error_details`, unlike `mark_synced`'s success path. A healthy row kept
carrying `training_blocked_unresolved` and a `session_id` pointing at a dead
session — invisible today only because the panel gates on `last_error`, which
made it a trap for the next reader. All three now clear together.
*Test:* `TestReconnectClearsEveryErrorColumn`.

**11.36 `get_sku_suppliers` ordered differently from `_PRIMARY_ORDER` — [FIXED 2026-09-16]**
`supplier_service.py:334` used `is_primary DESC, s.name` while the canonical rule
is `is_primary DESC, created_at ASC, supplier_id ASC`, and its own docstring
claimed they were the same rule — the module footer even tells readers to use
`get_sku_suppliers(...)[0]` as the primary. On a legacy row with two primaries
the answers differed (alphabetical vs oldest), so anyone following that
instruction would have resolved a different supplier than the semáforo built the
recommendation for. Now `_PRIMARY_ORDER` verbatim, with the name as a display
tie-break.

---

### Checked and found sound

Worth recording so it is not re-audited: `lib/api.ts` (`request` /
`downloadBlob` branch on every non-2xx; 401→refresh→retry→`_sessionLost` is
correct), `lib/auth.ts` (the auth-epoch guard and the cross-tab `storage`
listener close real session-resurrection holes), `ConfirmDialog` (the unresolved
promise really is fixed), es/en catalogue parity (3,095/3,095, zero duplicates),
`po_pdf.py` (unknown cost, currency precision and partial totals all handled),
the download-vs-run-status branching in `api/v1/reports.py` (the "generate one
first" trap is genuinely closed), the path-traversal guard in `artifacts.py`,
the `/pronosticos` per-SKU exports (they serve the object the chart rendered
rather than recomputing), transfer double-counting (`get_incoming_qty` credits
only the destination), `cancel_transfer`, the concurrency floors that prevent
negative stock, `claim()` with `FOR UPDATE SKIP LOCKED`, per-tenant isolation in
all four daily passes, and `_effective_lead_time`, whose n≥3 floor stops one
atypical reception from moving a learned lead time.

---

## 12. The user should always know what happened, and why (2026-09-16)

**Owner's instruction, 2026-09-16.** Not a finding from the sweep — a rule the
sweep kept running into. Half of section 11 is one shape repeating: the product
did something, or failed to, and told nobody. An analyst excluded from every
alert (11.1), a nightly sync walking past a ceiling (11.3), an order that
reached no supplier (11.4), an import that dropped 37 rows (11.34), a retrain
that blanked the active session at 3 a.m. (11.6). Each was fixed where it
happened. This is the other half: **whatever happens, the user can find out
that it happened and why.**

### What existed before

`activity_logs` held almost nothing a tenant would want to read: one row for a
deleted session, one per API-key call, and four scheduled sends — the ones the
bell already showed. Everything else lived in a log file the tenant cannot
open.

### The vocabulary, and why it is a registry and not a string

`backend/activity/events.py` declares every event the product can record: 20
actions in six kinds, each with a severity and a **whitelist** of the detail
keys it may carry. Three rules it exists to enforce:

1. **`record_event` refuses an undeclared action.** A typo'd action name writes
   a row nothing can read back — the exact silent failure this feature is
   against.
2. **Anything that is not `info` must carry a reason**, and the reason is a
   declared CODE, never prose. The frontend renders `events.reason.<code>` with
   `reason_params` interpolated, the same contract as `AppError`. A warning the
   user cannot act on costs attention and returns nothing, so the call site —
   which knows why — is made to say.
3. **Severity routes, it does not gate.** Everything is recorded. `critical`
   and `warning` reach the bell; `info` is history. A bell that announces every
   successful import is a bell people stop reading, and an event that only
   reaches a log file may as well not exist — recording everything and routing
   by severity avoids both.

`record_event` never raises. These calls sit inside a reception, a sync, a
training; losing an audit row is bad, failing the user's actual work over it is
worse.

### Two reads over one store

* `GET /alerts` — the bell. Scheduled sends **plus** critical and warning
  events, merged into one timeline. Deliveries stay fan-out-grouped (six rows
  for one digest is noise); system events are not grouped (three failures in a
  minute are three failures).
* `GET /alerts/activity` — everything, `info` included, filtered by kind and
  severity, paged. Deliveries are NOT grouped here: on an audit screen the
  per-recipient outcome is the point.
* `GET /alerts/kinds` — the filter vocabulary, from the same registry the
  writers use, so the screen cannot offer a topic nothing can be recorded
  under.

`POST /alerts/read` **dropped its analyst guard**. It used to require
analyst-or-above on the reasoning that no alert is ever addressed to a viewer.
That stopped being true the moment the bell started carrying tenant-wide system
events, and a viewer would have collected a badge with no way on earth to clear
it. The row it writes is the caller's own unread marker, not company state —
the same category as `POST /messages/read`.

### Where it is recorded from

| Kind | Events | Written by |
|---|---|---|
| `training` | completed / failed / blocked | `workers/runner.py` |
| `integration` | sync completed / failed / blocked | `integrations/sync_service.py` |
| `purchase` | order generated / sent / **not sent** / reception | `api/v1/inventory.py` |
| `data` | stock imported / **import partial** / shrinkage / transfer | `api/v1/inventory.py` |
| `limit` | ceiling reached | `entitlements/service.py` |
| `account` | user invited / role changed / deactivated / API key created / revoked | `api/v1/users.py`, `api/v1/api_keys.py` |

Two of those close gaps the sweep left open rather than only reporting them:
**a ceiling that stops an unattended write** (the nightly sync, a bulk import)
now leaves a row instead of nothing at all, and **an import that writes fewer
rows than the file had** says so with the count and the reason, which is the
signal 11.34 asked for. `purchase.order_not_sent` is critical on purpose: the
buyer believes the order is on its way, and it is not.

`account.*` carries `changed_by_an_account_admin` as its reason — the only
useful reaction to "I did not do that" is to look at who has access.

### The screens

`/actividad` ("Qué ha pasado" / "What happened"), under Análisis because it
answers a buyer's question, not an administrator's. Every role reads it — the
point of the screen is that nobody has to ask. Rows are rendered by the same
`AlertRow` the bell uses, so how an event reads changes in one place, and the
bell gained a permanent link to it: the bell is a subset by design, and a
subset with no way through to the rest is a filing cabinet with no handle.

Copy is `events.action.*`, `events.reason.*`, `events.kind.*`,
`events.severity.*` and `events.detail.*` in both languages. The last family is
the one that stops a regression this product has already had: every number an
event carries is rendered `Label: value` through its own key, so a new detail
field cannot reach a buyer's screen as `rows_written` — it does not compile
past the vocabulary test first.

### How it is guarded

`backend/tests/test_system_events.py` (32 tests). The important half is not the
feed, it is the vocabulary: **a declared event whose copy does not exist would
print its own key at a buyer**, and this product has done that. So the tests
read `translations.ts` and fail unless every action, reason, kind, severity and
detail key has copy in BOTH language blocks. The rest assert the routing rule
(only critical and warning in the bell), the refusals (undeclared action,
undeclared reason, a warning with no reason), that a write failure never
propagates, tenant isolation, that a viewer can clear their own badge, and —
one per call site — that walking the real endpoint leaves the right row in
`activity_logs`, read back with a direct query.

Walked in a browser on 2026-09-16: both languages, the bell (`info` correctly
absent, reasons rendered, badge clears on open), `/actividad` with both filters
and the filtered-empty state, no console errors.

### What is deliberately NOT here

- **No retention policy.** `activity_logs` grows; nothing prunes it. It is a
  small table and the feed reads at most 200 rows, but a tenant syncing nightly
  for two years will have a long tail nobody has measured.
- **No per-user muting and no email digest of events.** The bell and the screen
  are the whole surface. Adding a channel is a product decision.
- **`data.transfer_created` counts lines, it does not name SKUs.** One document
  is one row; a per-SKU event would put ten rows in the feed for one decision.
- The open items of section 11 stay open. This does not close 11.2, 11.5, 11.6,
  11.7, 11.9, 11.28, 11.32 or 11.33 — it makes two of them (11.34's silence, a
  ceiling hit with nobody watching) audible, which is not the same as fixed.

---

## 13. The 13 that needed a decision, and the decisions (2026-09-16)

Section 11 left 13 findings open. None of them was hard: each one needed an
answer that belongs to whoever owns the product, not to whoever writes the
code — what "retrain" means, what counts as "still in transit", what happens to
history a migration cannot attribute, whether to guess at an ERP payload.

Four calls were made, and everything else followed from them. All the work is
guarded by `backend/tests/test_open_findings_of_the_sweep.py` (37 tests), one
class per finding, each named after the defect rather than the fix so a failure
says which promise broke.

### The four decisions

**A scheduled retrain creates a NEW session and switches on success.** The
owner asked for the most complete option, and this is it. The schedule's
session — the one a person created and pointed it at — becomes a template that
is never trained again; each run builds a fresh session from it, and
`resolve_active_session` (newest family wins) switches to it only once it
COMPLETES. A 3 a.m. engine error now fails a session nobody is reading, and the
buyer opens the app to yesterday's numbers, which are numbers.

The reason this was a decision and not a fix is the slot economics: a saved
forecast is a plan ceiling (3 on free) and a daily schedule that kept every run
would fill it in three days. So **the schedule reuses its own slots**: before
each run it deletes the sessions it created itself except the one currently
serving, which bounds a schedule at two — what the buyer is reading, and what is
training to replace it. It can only ever reach rows carrying its own
`scheduled_job_id`; a session a person made never has one.

**A supplier is judged on deliveries that are actually due.** An order is left
out of `fill_rate` while `today ≤ generated_at + lead time + 2 days`, where the
lead time is `_effective_lead_time` — the same learned-then-declared-then-default
rule the overdue screen and the semáforo already use, so two screens cannot
disagree about whether a supplier is late. A fully received order is judged
immediately: it has nothing left to arrive. `purchased_value` is NOT held back
by the window, because the money left the company when the order was placed.

**Stock snapshots get their warehouse, and the old rows keep NULL.** There is
no way to attribute a historical snapshot to a location after the fact, so
nothing is backfilled: a NULL row is read as the tenant-wide total, which is
exactly what it was. Per-warehouse history starts on 2026-09-16; the aggregate
keeps its full history, because summing today's per-warehouse rows per day
continues the same series the old rows were.

**No ERP payload is guessed.** 11.5's fetch stays as it is until there is a real
account to read against — writing wrong stock is the defect being fixed. What is
fixed is the consequence, which cost the same money: a tenant whose stock is
recorded in ONE location while its sales carry several now reads **SIN_DATOS**
at the other branches instead of an invented zero, so nobody is told to buy a
full reorder for goods sitting in another warehouse. The row says why, on
screen, in the reader's language.

### What each one turned into

| # | What was decided | Where it lives |
|---|---|---|
| 11.2 | **Ask, never guess.** A file with `1.250` and no comma anywhere is ambiguous; the preview says so and the import is **refused** until the wizard's question is answered — in the file's own numbers ("1250" or "1.25"), because nobody should need to know what a thousands separator is. `sample_rows` is rendered too, so the parse is visible before committing. | `utils/stock_import.py` (`dot_is_ambiguous`), `POST /inventory/bulk` (`thousands_dot`), `StockImportWizard.tsx` |
| 11.5 | Consequence only, see above. | `inventory/service.py` (`stock_is_single_location`), `WarehouseStatusTable.tsx` |
| 11.6 | New session per run, switch on success, prune its own previous runs, validate the template, and skip while one is still training. | `sessions/retrain_service.py`, `workers/worker.py` |
| 11.7 | The endpoint takes a `warehouse`; the download follows the tab that is open, and the order logged in /pedidos carries the same destination. "Export edited" defers to it while a tab is open, because the edits it would export belong to a view nobody is looking at. | `GET /inventory/status/export-po`, `inventario/page.tsx` |
| 11.8 | The modal ASKS which warehouse — one control, and only for tenants with more than one place to lose stock from. It opens on the tab the buyer has open. | `inventario/page.tsx` (`ShrinkageModal`) |
| 11.9 | A checkbox in the wizard: *do not overwrite what I corrected by hand*. Off by default, so every existing caller behaves exactly as before; on, it passes the `only_fill_missing` that already existed and had no caller but a test. | `POST /inventory/bulk`, `StockImportWizard.tsx` |
| 11.12 | The window rule above. An empty fill rate now says **"2 on the way"** instead of looking like a supplier nobody buys from. | `inventory/reception_service.py` (`_fill_counts_for`), `proveedores/scorecard/page.tsx` |
| 11.14 | The cart line carries its `supplier_id` and the evaluation key includes it, so switching supplier re-evaluates. A supplier who quotes no ladder for that SKU is quoted **nothing** rather than somebody else's price. | `price_break_service.py`, `compras/page.tsx` |
| 11.15 | Column added, old rows NULL. The tenant-wide series is now per-warehouse rows collapsed to one value per location per day and summed — not their rows interleaved, which `_calc_demand_trend` read as "+585% demand" that never happened. | `db/migrations.py`, `inventory/service.py` (`get_stock_history`) |
| 11.28 | One row per loop holding the BOUNDARY it last completed, so a restart at 08:02 runs the 08:00 pass instead of sleeping until tomorrow. Catch-up is bounded — six hours for the daily loops, three days for the monthly one, because its snapshot is the closing measurement of a month and nothing else can produce it. Past the window the boundary is recorded as **skipped**, so the gap is visible. `/health` carries the markers. | `workers/loop_state.py`, `workers/worker.py`, `main.py` |
| 11.32 | The lead-time field opens **empty**, with the assumption as a placeholder, and is omitted from the payload when blank — so `_stamp_lead_time_provenance` stops recording SOURCE_USER for a supplier who declared nothing, and the scorecard stops printing DECLARADO 15d. | `proveedores/page.tsx`, `lib/api.ts` (`SupplierInput`) |
| 11.33 | The header and its lines commit as ONE transaction. The old `except: log.warning` per line is gone: the order rolls back whole, the API returns the error, and the buyer's cart is still in the browser to retry. Manual orders too. | `inventory/roi_service.py` (`_write_po_atomically`) |
| 11.34 | Rows that parsed cleanly and did not reach the database travel back in the same `errors` channel as the parse failures, with the same `{row, sku, code, params, error}` shape, and the wizard names them. The event feed counts them too (§12). | `inventory/service.py` (`bulk_upsert(failures=…)`), `POST /inventory/bulk`, `StockImportWizard.tsx` |

### Two more, found walking the screens

Neither was in the sweep's 36. Both were found by using the product after the
13 were "done", which is the whole argument for walking it: the tests were
green and green was not enough.

**The API model was declaring lead times nobody typed.** 11.32 was fixed in the
form — and creating a supplier in the browser came back **422**.
`SupplierCreate.lead_time_days` was `int = Field(default=15)`, so an omitted
field arrived at the service as a 15 and `_stamp_lead_time_provenance` filed it
as the supplier's own declaration. The form fix could not work while the model
held that default, and every API client was affected too. It is
`Optional[int] = None` now, `exclude_none=True` drops it, and the column's own
DEFAULT still supplies the number the planner needs. The tests missed it because
they called the service directly, where the field was always passed explicitly.
*Tests:* `TestASupplierOnlyDeclaresALeadTimeWhenSomebodyTypesOne` (3), plus the
inverted assertion in `test_lead_time_provenance.py`, which had PINNED this gap
and said in so many words that making the field Optional should flip it.

**11.7 survived one function to the left.** With the Norte tab open the export
correctly downloaded Norte's rows — and the `log-po` call right behind it
re-derived the list TENANT-WIDE, so /pedidos showed an order for two SKUs the
file never contained. Same defect, same screen, one endpoint over: the fallback
path in `log_po` now re-derives per warehouse when the request carries a
destination.
*Test:* `test_the_order_logged_behind_the_file_is_the_same_list`.

**One thing the walk changed that was not a defect:** with "do not overwrite
what I corrected by hand" ticked, a re-import that finds nothing to fill
reports **"Productos importados: 0"**, which is true and unhelpful. It now says
there was nothing to fill and why.

### What this did NOT close

- **11.5's fetch.** Alegra and Siigo still write every unit to `principal`.
  Reopening it needs an account, not a decision.
- **11.27's other half.** There is still no way to cancel a training run you
  regret; the screen still tells you not to close the tab. That is a new
  capability and nobody has asked for it.
- **Retention.** `activity_logs` and now `system_loop_runs` grow without a
  prune. Small tables, no measurement behind that statement.

---

## 14. What a company needs before it buys this to run itself (2026-09-16)

The question that produced this section: *what else does a company need before
it buys Faro for its own operation?* Not "what features are missing" — what a
buyer's owner and IT department ask before they sign, and what they discover
three months in.

A lot of it already existed and is worth not rebuilding: the `deploy/` stack
with its three growth paths, the virgin install (§10) with a test that guards
it, `/instalacion` and the configuration registry, services that degrade out
loud, both manuals in both languages, tenant export and erasure written against
Ley 8968, and — since §12 — an audit trail the tenant can read. The gaps below
are what was left.

### Done in this pass

**a) A restore nobody had ever performed — [DONE 2026-09-16]**
`deploy/README.md` had the commands and named the Fernet trap. Nobody had run
the other direction. Doing it produced [`deploy/RESTORE.md`](../deploy/RESTORE.md),
written from the actual run (159 tenants, 89 users, 42 completed sessions,
2,156 stock rows restored clean), and found three things the commands alone did
not:

* **The backup target is ambiguous on a bare checkout.** `README.md` said
  "a backup of `storage/`"; the default `STORAGE_PATH` is `backend/storage`;
  both directories exist. On this machine they held different things — the
  Fernet key was in the one the instruction did not name. Fixed in both READMEs:
  ask the app where `STORAGE_PATH` is, do not guess.
* **`docker run -v faro_storage:/s` creates an empty volume when the name is
  wrong** — and compose prefixes volumes with the project (directory) name. The
  backup succeeds, the archive is ~100 bytes, nothing says so. The runbook now
  checks the volume name and refuses to trust an archive that small.
* **The failure mode, reproduced.** A database-only restore comes back with
  159 tenants, 89 users, 2,156 stock rows — identical — and every stored
  credential unreadable. To the product's credit it is loud: it names the
  variable and says the value must be entered again.

**b) Runtime data was tracked by git — [FIXED 2026-09-16]**
`backend/storage/` is both the default `STORAGE_PATH` **and** a Python package
(`__init__.py`, `file_store.py`, `paths.py`). 234 data files (1.1 MB) under it
were tracked despite `.gitignore` listing the directory — gitignore does not
untrack what is already tracked — including the uploaded CSV of a tenant that
no longer exists in the database. That defeats `delete_tenant()`: erasure
removes the rows and version control keeps the file.

Untracked now (`git rm --cached`, working copies untouched; nothing in the code
reads those paths — checked). **Two decisions left to the owner:**
* the blobs remain in git HISTORY; purging them is a rewrite, which breaks every
  existing clone. Worth doing before the repository is handed to a buyer,
  pointless afterwards.
* the default `STORAGE_PATH` still points inside the source tree. The deployed
  stack sets `/app/storage` so production is unaffected, but a bare checkout
  writes customer data into a package directory, where a `git clean -xfd` or a
  fresh clone removes it. Changing the default is a one-line change with a
  migration problem attached (existing installs).

**c) What leaves the buyer's network — [DONE 2026-09-16]**
[`docs/data-that-leaves.md`](data-that-leaves.md): the five outbound
destinations (DeepSeek, Resend/SMTP, Twilio, Alegra, Siigo), what each one is
sent, and how to turn it off — plus the commands to verify the list without
trusting the page. No telemetry, no analytics, no licence check; the frontend
loads nothing external. The one that decides an IT review is the assistant:
with `DEEPSEEK_API_KEY` set, questions and the business context to answer them
go to a third party, and without it every AI feature degrades to rule-based
text rather than failing.

**d) Upgrade and rollback — [DONE 2026-09-16]**
[`deploy/UPGRADE.md`](../deploy/UPGRADE.md) plus a `CHANGELOG.md`. The property
that makes rollback cheap — every migration is additive, so the old code runs
against the new schema — is now stated, with the one case that would break it
(a release that changes the meaning of stored data; none has).

**e) A release gate a person can run — [DONE 2026-09-16]**
[`scripts/SMOKE.md`](../scripts/SMOKE.md): nine paths, twenty minutes, one
fresh tenant. Written from the walk that found two defects on 2026-09-16 while
2,938 backend tests were green.

### Open, and whose call each one is

**f) The ERP integrations have never touched a real account — [OPEN, needs an account]**
This is §11.5 from the other side: for a LatAm distributor, "does it read my
Alegra/Siigo?" is often *the* buying question, and the honest answer today is
that the code is written, nobody has run it against a live account, and the
stock fetch is known to be wrong for a multi-branch tenant. Either a sandbox
account closes it, or the sales conversation says plainly that the integration
is a project and not a checkbox. **Blocked on credentials, not on work.**

**g) The operator cannot see failures — [OPEN, owner's call]**
`/health` now carries service state and loop freshness, and the tenant has
`/actividad`. But when a training fails at 3 a.m. on a customer's own server,
nothing reaches a person. The cheap version is a daily operator digest over the
mail channel that already exists; the thorough version is error aggregation,
which is a dependency and a decision. A new channel, so: ask first.

**h) Capacity has no measured number — [OPEN]**
A buyer with 20,000 SKUs and three years of history will ask whether it holds,
and there is no figure to give them. The stress suite exercises concurrency,
not scale. One honest benchmark (N SKUs × M months: training time, peak memory,
semáforo latency) belongs in the technical manual. Signal worth taking
seriously: on 2026-09-16 the full backend suite could not run in one process on
a 16 GB machine — it was killed twice for memory and had to be sliced.

**i) Nothing is ever pruned — [OPEN, owner's call]**
`activity_logs` grows with every sync, import and order; `system_loop_runs` and
`inventory_snapshots` grow too. Small today, unbounded on a customer's disk.
Deleting a tenant's history is a data-policy decision, not a code change, which
is why it is here and not done.

**j) The frontend still has no automated test — [OPEN, owner's call]**
`scripts/SMOKE.md` is a person with a browser. Automating paths 1–6 means
Playwright in the repo, and with no CI it would still be a script somebody runs
before tagging. Worth it the day two people are shipping.

---

## Lo que se borró el 2026-08-11, y por qué

Siete documentos de planes, propuestas y auditorías ya ejecutados o superados.
Todos siguen en el historial de git; ninguno describía trabajo abierto:

| Documento | Por qué se fue |
|---|---|
| `plan_general_faro_2026-07-18.md` | Plan general superado; no lo citaba nadie |
| `features_propuestas_faro_2026-07-05.md` | Propuestas de features — justo lo que ya no se quiere |
| `auditoria_integral_faro_2026-07-04.md` | Auditoría de julio, ejecutada; sus hallazgos viven hoy como tests |
| `animaciones-plan.md` | Ejecutado. Su último pendiente (`pulse`/`slideUp` locales) está cerrado: ambos viven solo en `globals.css` |
| `pending-polish-2026-07-24.md` | Lista de pulido; su propio encabezado decía que nada era crítico |
| `friccion-onboarding-2026-07-27.md` | Ejecutado |
| `qa/2026-07-23-fullflow-walkthrough-findings.md` | Superado por `inventario-pantallas.md`, que cubre las 26 pantallas |

Los comentarios del código que citaban estos archivos se reescribieron para no
apuntar a rutas muertas.

**Lo que se conservó, y no es basura:** `api-publica.md` (documentación para el
cliente), `inventario-pantallas.md` (la tabla viva), `direccion-complemento-2026-08-10.md`
(la dirección de producto vigente), `demo-script.md`, `help/index.html` (la guía
bilingüe de usuario) y `paper/` + el PDF del motor. Las tres skills de
`.claude/skills/` —`faro-i18n`, `running-faro`, `silent-failures`— están
vigentes y CLAUDE.md las referencia.
