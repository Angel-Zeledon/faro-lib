# Demo script — Faro

A practical guide to presenting Faro from end to end, against a tenant already
seeded with rich, coherent data. Written to be read while driving the app: each
step carries a "what to say" line that names the business value.

> **Routes were corrected on 2026-09-16.** An earlier version of this script
> sent the presenter to `/hoy`, `/skus`, `/data`, `/config` and `/inventory` —
> none of which exist. Everything below was checked against
> `Frontend/src/app/`.

---

## 0. Before you start (setup)

### Bring the environment up

1. **Postgres** (Docker, usually already running):
   ```bash
   docker start faro_db   # container on :5544, user/pass postgres/postgres
   ```

2. **Backend** (port 8011 — 8010 is taken by another project's container on the
   development machine), from the repository root:
   ```bash
   backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
   ```

3. **Frontend** (port 5000), from `Frontend/`:
   ```bash
   npm run dev
   ```
   The proxy target comes from `Frontend/.env.local`, and that file **beats** a
   shell `BACKEND_URL=…`. If every `/api/*` call returns 500 with an empty
   body, that file is naming a port nothing is listening on. If `node_modules`
   is broken: `npm install`. Do **not** run `npm run build` while `next dev` is
   up — it corrupts the `.next` cache.

4. Open **http://localhost:5000** in Chrome.

### Demo login

| Field | Value |
|-------|-------|
| Email | `demo@faro.app` |
| Password | `demo1234` |

The email is already verified. Every feature is available: there are no feature
gates in this product — the tier decides how MUCH fits (SKUs, users,
warehouses, saved forecasts), never what the product can do.

### Reset and reseed before a demo

The seed is **idempotent**: it deletes the demo tenant and rebuilds it from
scratch with coherent data. Run this to leave the demo "as new":

```bash
backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo
```

- Takes **~3–4 minutes** because it trains the forecasts for real (a daily +
  weekly family over 14 SKUs). It prints the summary and the semáforo when it
  finishes (`PEDIR_YA: 2, PEDIR_PRONTO: 3, OK: 6, SOBRESTOCK: 3`).
- It can run with the backend up; the worker does not interfere. If you had a
  browser session open, sign in again afterwards — the user is recreated.
- `--no-train` resets in seconds **but leaves the semáforo empty** (there is no
  forecast). Use it for quick checks, never for a real demo.

### What gets seeded

- **14 grocery SKUs** (olive oil, premium rice, milk, coffee, tuna…) with ~18
  months of daily sales and a trained forecast (~88% accuracy).
- **3 warehouses** (principal / Norte / Sur) with a demand split; **Detergente**
  is deliberately unbalanced so a transfer suggestion fires.
- **3 suppliers** with lead times, mapped to SKUs.
- **4 purchase orders** in different states (received, partial, in transit, to
  be sent) plus **1 closed transfer**.
- **2 shrinkage records**, and a **LatAm calendar** (Colombian fortnights and
  Holy Week).

---

## 1. The value story (click-through)

> The thread: **upload data → forecast → what to order → generate the order →
> receive it and learn the lead time → multi-warehouse and transfers →
> multi-period → calendar and seasonality → data editor → WhatsApp.**

### A. Upload sales → from a CSV to decisions

1. Go to **Mis ventas** (`/ventas`) and open **Mis archivos** (`/archivos`).
   Select **"Ventas Demo Faro"**.
   > *"It starts with what the distributor already has: their sales history in
   > a CSV. No complex integration to get going."*
2. Show the size and the row count.
   > *"Eighteen months of daily sales, 14 products. That is what Faro trains a
   > model per SKU on."*

### B. Forecast → one model per product

3. Go to **Pronósticos** (`/pronosticos`). The "Demo Faro" session is selected
   automatically.
   > *"Faro trains several models (LightGBM, XGBoost, Prophet, Croston…) and
   > picks the best one per SKU. Here the winner was XGBoost at ~3% error."*
4. Click a SKU (e.g. **Aceite de Oliva 1L**): the history, the forecast and the
   uncertainty band.
   > *"This is not a fixed 'order when it drops below X' rule. It is projected
   > demand, with weekend and payday seasonality."*
5. Switch the **granularity D → W** (the D/W/M buttons on the chart) and the
   **session** selector between "Demo Faro" and "Demo Faro · weekly".
   > *"The same data, by day or by week, depending on how the customer buys."*

### C. The semáforo → what to order today

6. Go to **Inventario** (`/inventario`) and show the signals:
   **2 Pedir YA · 3 Pedir pronto · 6 OK · 3 Sobrestock**.
   > *"This is the screen: at a glance, what is at risk and what is excess. Red
   > runs out before the supplier arrives; blue is sleeping capital."*
7. Point at an **overstocked** line (rice / sugar): 48 days of coverage.
   > *"That is money standing still. Faro suggests pausing the next order."*

### D. Generate the order → from the signal to a PO

8. Back to **Panel de compras** (`/compras`). Show the "URGENTE" cards (oil,
   flour) with the suggested quantity and the approximate cost.
   > *"Faro does not just warn: it says how much to order, from which supplier,
   > at what estimated cost."*
9. Click **"Ver por qué"** on an urgent line to open the breakdown (daily
   demand, lead time, safety stock).
   > *"All of it is explainable: the buyer sees the arithmetic, not a black
   > box."*
10. **Approve** a card to build the cart, then export the order.
    > *"One click turns the recommendation into a purchase order ready to
    > send."*

### E. Receive it and learn the lead time → closing the loop

11. Go to **Pedidos** (`/pedidos`). Show the four POs in different states:
    **OC-000001 received**, **OC-000002 partial**, **OC-000003/004 in transit**.
    > *"The cycle does not end at the order: the arrival is recorded."*
12. On an in-transit PO, click **"Registrar llegada"** and confirm the
    reception.
    > *"When you record the arrival, Faro compares the real date against the
    > promised one and learns the supplier's true lead time."*
13. Go to **Proveedores → Scorecard** (`/proveedores/scorecard`).
    > *"Look: Granos del Valle says 12 days and delivers in 5–8. Faro plans on
    > the REAL lead time, not the one on paper."*

### F. Multi-warehouse and transfers → move before you buy

14. In **Inventario**, switch to the **principal** warehouse tab.
    > *"The same semáforo, per warehouse."*
15. Find **Detergente 1kg**: in principal the action is not "buy", it is
    **"Transferir 228 desde Norte"**.
    > *"Before spending on a purchase, Faro checks whether another warehouse has
    > a surplus. Here it is cheaper to move stock than to buy it."*
16. (Optional) The purchase panel says the same at the top: *"1 se resuelve
    moviendo stock, sin comprar"* → **Crear transferencia**.

### G. Multi-period → day ↔ week

17. In the **"Ver por"** selector at the top, switch **day → week**.
    > *"A buyer who plans weekly sees coverage, quantities and lead time in
    > weeks — which is how they actually work."*
18. Note that quantities stay whole units and coverage switches from days to
    weeks consistently with the KPI above.

### H. Calendar and seasonality → getting ahead of the peaks

19. In **Inventario**, open **"Eventos y temporadas"** or the **"Simular:
    Quincena…"** buttons.
    > *"Faro ships the LatAm commercial calendar: paydays, Holy Week. One click
    > simulates the event's impact on demand."*
20. Run **"Simular: Quincena"** and show how the recommendations move with the
    event multiplier.
    > *"Payday drives consumption; Faro anticipates it before the signal turns
    > red."*

### I. Data editor → fixing without leaving the app

21. In **Mis archivos**, select the source → **Editar**.
    > *"If the customer spots bad data, they fix it right here."*
22. Change a `cantidad` cell → **"Guardar como nuevo"** (creates a new version
    without touching the original).
    > *"Save as new leaves the original dataset intact and produces a version
    > ready to retrain."*
    > ⚠️ The editor table loads EVERY row of the file (see "Known limits").
    > Do not scroll through the ~7,500 rows: show the header, edit one cell,
    > save.

### J. WhatsApp → alerts and the bot

23. Go to **Mi cuenta** (`/mi-cuenta`), section **"Vincular WhatsApp"**: the
    number is already there (`+506 8888 7777`), then **"Enviar código"**.
    > *"The buyer links their WhatsApp and gets the inventory alerts there, and
    > can ask about their purchases by chatting with the bot."*
    See the honest note about the bot below.

### K. Closing — language and the rest

24. The **ES / EN** button (sidebar or `/mi-cuenta`): toggle to English and
    back.
    > *"Bilingual out of the box."*
25. **Ctrl-K** (or the search box at the top): find a product and see its
    per-warehouse breakdown.
26. Mention **Impacto** (`/impacto`): accumulated ROI, recommendation adoption,
    capital freed. And **Qué ha pasado** (`/actividad`): everything the product
    did and why, which is what an auditor or a returning buyer reads first.
    > *"And all of it is measured: how many recommendations they followed, how
    > much capital they freed."*

---

## 2. An honest note about the WhatsApp bot

- **Outbound alerts work for real.** With Twilio (sandbox) credentials
  configured, Faro sends the daily inventory alerts over WhatsApp. Without
  credentials the send is a no-op recorded in the logs.
- **The inbound conversational bot needs extra setup.** For a live round trip
  (the user writes to the bot and it answers with their data) you need:
  1. A **public tunnel** (e.g. `ngrok`) pointing at the backend webhook, because
     Twilio needs a public URL to deliver incoming messages.
  2. **DeepSeek credit** (`DEEPSEEK_API_KEY`) for the intelligent replies; today
     the bot runs in **generic mode** (`WHATSAPP_BOT_GENERIC_MODE`) as a stopgap
     when there is no funded LLM.
- **Recommendation for the demo:** show the linking UI in `/mi-cuenta` and
  *explain* the bot. If the presenter wants the live round trip, bring up
  `ngrok` and set a funded `DEEPSEEK_API_KEY` **before** the session.

---

## 3. Known limits / "do not click here"

- **The data editor with a large file.** It renders EVERY row. The demo file has
  ~7,500, so the table takes a moment to paint and scrolling feels heavy. For
  the demo: open it, edit ONE visible cell, use "Guardar como nuevo", do not
  scroll.
- **"Resumen ejecutivo del día" (purchase panel).** The narrative is generated
  by the LLM. With no `DEEPSEEK_API_KEY` credit it can sit on "Analizando
  datos…". That is not a data error and the rest of the panel works; do not wait
  for that card.
- **"Cambios en demanda" showing -99%.** That section compares the last real day
  against the forecast and sometimes shows a large drop (noise in the final
  point). It is informative, not a bug — do not dwell on it.
- **AI assistant / chat.** Like the bot, it depends on funded
  `DEEPSEEK_API_KEY`. Without it, it answers in a limited mode.
- **Reseed means re-login.** If you run `seed_demo` with a session open, the
  user is recreated; sign in again.
