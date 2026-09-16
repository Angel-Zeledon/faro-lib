# Faro complements, it does not replace

**Written:** 2026-08-10. This is a **direction** document, not a plan and not a
task list. Its use is deciding what **not** to build.

> **Note added 2026-09-16.** The packaging question this document keeps
> circling — which plan the automatic data paths belong to — was settled on
> 2026-08-22 by removing feature gates entirely. There are two tiers, both ship
> every feature, and the tier only decides how much fits. Wherever the text
> below argues about Professional vs Enterprise, read it as a record of the
> reasoning, not of the product. Everything else still holds.

## The rule

> Faro owns the decisions and what it learns from them.
> The customer's system owns the inventory, the catalogue and the suppliers.

Everything else follows from that.

## Why now

The owner put it this way: Faro should not be "the software companies live in",
SAP-style, but something that **complements** what they already have. This is
written down because the code had been drifting the other way.

Today Faro stores stock per warehouse, warehouses, transfers, shrinkage,
suppliers, purchase orders, receptions. Those are ERP tables, and each of them
already has an owner inside the customer's company. Having them is not the
problem; the problem is that Faro asks to be **the second source of truth
without any way to stay in sync**. Measured on the 2026-08-10 walk:

- `/configurar-inventario` asks a person to type stock, cost and lead time
  product by product — data the customer's system already holds.
- That session's semáforo was computed over sales from **407 days ago**. The
  screen says so honestly, but the only way out it offers is uploading another
  file by hand.
- Receptions by hand. Stock imported from a CSV by hand.

Being the system of record forces everything else: concurrency, audit,
corrections, migrations, fine-grained permissions. That is the road to looking
like SAP — while competing against something the customer cannot remove.

## The four consequences of the rule

1. **Never ask a person to type what a machine already knows.**
2. The customer's data lives in Faro as a **cache with provenance and a date**,
   not as a record.
3. The only thing that is Faro's own is what nobody else stores: **learned lead
   times, service levels, and the history of decisions with their outcome.**
4. The value arrives **without opening the app**. The daily message is the
   product; the app is where you go when you want to argue with it.

Point 3 is the moat. No ERP says "this supplier promises 7 days and delivers in
11". Faro already computes it.

## Customers use "everything" — and that decides the shape

Asked which systems their real customers use, the owner answered: **everything**.
That rules out the glamorous answer. You cannot integrate with N heterogeneous
LatAm SMB systems; several have no usable API and some are Excel.

But **all of them can export a file**. The common denominator is not an API, it
is the export. So the honest version of "complement" in this market is
**file-first, not API-first**.

And that is the good news: **the hard part is already built.** The product
already detects columns by Spanish alias, already survives cp1252 with accents,
already resolves dd/mm/yyyy dates, already reads `;` separators and comma
decimals, and already has a data gate with remediations. That is the
differentiator, and it exists.

**What is missing is not reading ability: it is that reading should be a cycle
and not an event.** Today importing a file is a wizard you run once. The same
source, with the same mapping, re-importable in one click and schedulable, is
what turns "upload your sales" from a monthly chore into something that happens
by itself — and it is what attacks the 407 days. The pieces exist separately:
`/archivos` has sources, `/ventas` remembers the mapping, `/automatizacion` has
schedules.

### Correction from walking `/integraciones` (same day)

Having written the above, it turned out that **the automatic cycle already
exists — but only over the API path**. There are real connectors for Alegra and
Siigo (`backend/integrations/`, with a registry, DTOs, credential encryption and
a test suite), and `run_daily_integration_syncs` runs from the worker's daily
loop, pulling catalogue, stock and sales without anybody opening anything. That
is exactly the shape this document asks for. None of it is unbuilt.

That does not invalidate the thesis; it sharpens it, in three ways:

1. **The pattern is already proven in-house.** Nobody has to invent what "it
   comes in by itself" looks like: it already looks like that for two providers.
   What is missing is giving the **file** the same treatment — and the file is
   where the volume will come from.
2. **Two connectors do not cover "everything".** They cover a slice. The file
   path is still the only one that serves everybody else.
3. **They sat behind the most expensive plan.** That is: the capability that
   makes Faro complement rather than replace was the priciest thing in the
   catalogue, while typing the inventory by hand was available to all. (Settled
   since — see the 2026-09-16 note at the top.)

Also, Alegra and Siigo are **Colombian** accounting software, and the declared
anchor market is **Costa Rica** (see the strategy memory, which already flagged
this misalignment on 2026-07-19). The real Costa Rican equivalent is still
undefined.

### Second correction: the file cycle is not unbuilt either

On going to implement it, it turned out that **the three pieces already exist
and already compose**:

1. `POST /datasources/{source_id}/file` replaces the file **in place**,
   atomically (writes `.tmp`, checks the size, and only then swaps). The source
   keeps its identity, its id and its mapping.
2. `scheduled_jobs` + `_run_due_scheduled_jobs` retrain **one session** on a
   cron. The session points at that dataset.
3. `/ventas` already offers cloning a completed session reusing **the dataset
   and the column mapping**.

So: replace the file + a schedule = automatic import, with no new code. A
customer can put a cron on their side that uploads their system's nightly
export, and Faro retrains by itself.

**What is missing is not machinery, it is two cheaper and more uncomfortable
things:**

- **Nobody knows.** There is nothing in the interface that says "you can
  automate this", let alone explains how. It is built and invisible.
- **The key was not behind the most expensive plan** — a correction to what this
  same document asserted first. API access lived one tier down from the
  connectors, with the reasoning already written in the code: *"the customer who
  most needs to stop uploading files by hand is the one with an ERP and a few
  thousand SKUs"*. (Moot since the tiers were flattened; kept because the
  reasoning about who needs what is still right.)

*Verified by reading the code, not walked end to end:* upload-by-API-key plus
scheduled retraining still needs one real run.

## What to do, in order of return

1. **Say it in the interface.** The automatic paths exist and nothing tells
   anybody. This replaces what the document originally asked for as "turn the
   upload into a cycle" — the cycle is already there.
2. **Provenance and a date on every number that moves money.** Half done
   already (the "estimated" badges, "with an estimated cost", "we do not know"
   instead of "no risk") and it is the best thing in the product. An ERP gives
   you a number; Faro says where it came from and whether it made it up.
3. **Get the decision out of the building**: the PO in the customer's format,
   the message to the supplier, the daily summary. Partially there, and
   under-invested compared to the record-keeping half.

## What not to do

- **Freeze the ERP surface**, do not delete it: manual orders, warehouse
  administration, transfers, shrinkage. Just stop growing it.
- **Do not build an integrations platform.** It is the natural trap after
  buying this thesis, and it fails precisely on "everything".
- **Do not overcorrect**: Faro cannot stop storing stock, because without stock
  there is no semáforo. The rule is not "do not store", it is **"do not be the
  authority"** — always show where each number came from and when, and make
  refreshing it trivial.

## How to use this document

Faced with a new feature, the question is: *does this store something the
customer already has somewhere else?* If the answer is yes, the right answer is
almost always to read it from their system, or not to do it — not to store it
better.
