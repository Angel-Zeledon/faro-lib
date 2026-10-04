# The smoke walk, before you tag a release

`python scripts/run_tests.py` says the backend behaves. It does not say the
product works: **the frontend has no tests at all**, and on 2026-08-06 the
suite was green with 27 live defects. On 2026-09-16, 2,938 backend tests were
green while two real defects sat in the product — both found in the ten minutes
of clicking described below.

So: run the suite, then walk this. Twenty minutes, one browser, one fresh
tenant. Anything that reads wrong here is a release blocker, because it is what
the buyer sees first.

## Before

```sh
python scripts/run_tests.py          # backend, engine, typecheck
python scripts/e2e_client.py         # the API flow end to end, signup → results
```

Then start both servers (see the `running-stockai` skill for the port traps) and
**sign up a brand new tenant**. Not your usual one: half of what breaks only
breaks on an account with no history.

## The walk

Each line is a path and the thing that must be true at the end of it. Read the
browser console as you go — it should stay empty.

1. **Sign up → verify → sign in.** You land on `/compras` with an empty state
   that tells you what to do next, not a spinner and not a blank page.

2. **Upload sales → train → wait.** `/ventas`, a CSV, the wizard's defaults,
   launch. Leave the page while it trains and come back: the app must not move
   you anywhere on its own, and the run must still be there.

3. **`/inventario` with two warehouses.** Create a second warehouse, import
   stock into one of them only. The other warehouse's tab reads **SIN DATOS
   with a reason**, never PEDIR YA at full quantity. Open the download menu
   with a warehouse tab selected: the file and `/pedidos` must agree with the
   tab — not with the tenant-wide total.

4. **Generate a purchase order and send it.** `/compras`, approve a line,
   change its supplier, accept a price break if one is offered (the quantity
   *and* the price must move), export. The order appears in `/pedidos` with the
   quantities you saw. Send it to a supplier with no email on file: the screen
   says it did not go out and offers the retry — it must never be drawn as a
   quiet amber "skipped".

5. **Receive it partially.** `/pedidos` → receive half. Stock goes up by what
   arrived, the supplier's scorecard does not print a fill-rate verdict while
   the delivery is still inside its window ("N en camino"), and `/actividad`
   has the reception.

6. **Import a file with `1.250` in it.** `/configurar-inventario`. The wizard
   must ASK whether that is 1250 or 1.25 and refuse to import until answered,
   and the preview must show the parsed rows. This one is worth its own line:
   guessing it wrong divides a whole catalogue by a thousand and reports
   success.

7. **The bell and `/actividad`.** Everything above should be visible there,
   with a reason on anything that failed. `info` events stay out of the bell.

8. **Switch to EN and re-walk two screens.** Any raw key on screen
   (`inventory.source_file`) is a blocker; the catalogue is checked by
   `check_parity.py` but a key nobody looked up is not.

9. **`/instalacion`** reports each service as configured or off, by name, and
   says which ones an unconfigured service takes down.

10. **The key a customer's system uses, on both surfaces.** `/automatizacion` →
    generate a **read-only** key. Paste it into `/api`, run `GET /planning`
    (200, with an `active_session_id`). Then, from the MCP section on the same
    page, the curl it offers:

    ```sh
    curl -X POST "$STOCKAI/mcp" -H "Authorization: Bearer $KEY"       -H "Content-Type: application/json"       -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    ```

    Five tools come back. Now the part worth the line: call
    `get_inventory_status` and check that `summary` counts the whole catalogue
    even when `truncated` is true — a summary that shrank with the page tells
    an assistant there is nothing to buy. Then send a wrong key and confirm the
    answer is a **401 naming the key**, not a 403 and not a hang.

## What this walk has caught

Kept as evidence that it is worth the twenty minutes, not as history:

* A supplier form that could not create a supplier at all (422) because the API
  model defaulted a field the form had stopped sending.
* An export scoped to the open warehouse whose **logged order** was still
  tenant-wide, so `/pedidos` showed an order the file never contained.
* `ConfirmDialog` losing the first confirmation's promise — an action that hung
  forever with no message, no spinner and no error.
* Six screens' worth of numbers that did not mean what their label said.

## Automating it

Deliberately not automated yet. Doing it properly means a browser driver
(Playwright) in the repo and something to run it — and the owner's standing
decision is no CI/CD, so it would be a script a person runs anyway. If that
trade looks right later, paths 1–6 are the ones to write first; they are the
whole product in one line each.
