# What leaves your network

*Last verified against the code on 2026-09-16.*

Faro runs on your server. Your sales history, your stock, your suppliers and
your purchase orders live in your Postgres and on your disk, and nothing moves
them. What follows is the complete list of the times the product opens a
connection to somebody else, what it sends, and how to turn each one off.

The list is short by design, and every entry is **off until you configure it**.
A Faro with no credentials in `/instalacion` talks to nothing.

---

## The five destinations

| What | Where it goes | What is sent | Turned off by |
|---|---|---|---|
| **AI assistant, RAG, narratives, data-quality diagnosis** | `api.deepseek.com` (`DEEPSEEK_BASE_URL`) | The question the user typed, plus the inventory/forecast context needed to answer it: SKU codes, names, quantities, costs, supplier names | Leave `DEEPSEEK_API_KEY` empty |
| **Email** (alerts, purchase orders to suppliers, invitations, password changes) | `api.resend.com`, or your own SMTP server | Recipient address, subject, body, and for a purchase order the PDF — which names products, quantities and prices | Leave `RESEND_API_KEY` and the SMTP settings empty |
| **WhatsApp / SMS** | `api.twilio.com` | Destination number and message text (the stockout digest, the order summary) | Leave the `TWILIO_*` settings empty |
| **ERP sync — Alegra** | `api.alegra.com` (`ALEGRA_BASE_URL`) | Your Alegra credentials, to READ products, stock and invoices. Nothing of yours is written there | Do not connect the integration |
| **ERP sync — Siigo** | `api.siigo.com` (`SIIGO_BASE_URL`) | Same: credentials in, catalogue and invoices out | Do not connect the integration |

There is no telemetry, no analytics, no crash reporting, no licence check and
no "call home". The product never contacts the vendor, and it does not need
outbound internet to work: with all five unconfigured, forecasting, the stock
semáforo, purchase orders, receptions and every screen work exactly the same.

---

## The one that usually decides an IT review: the AI assistant

If `DEEPSEEK_API_KEY` is set, the questions users ask the assistant and the
business context needed to answer them are sent to DeepSeek, a third party,
over HTTPS. That includes product names, quantities, costs and supplier names
for the SKUs relevant to the question.

**Without the key, every AI feature degrades to a rule-based answer rather than
failing.** That is not a fallback that happens to work; it is how the code is
written — `get_local_llm_client()` raises `LLMNotConfigured` and each consumer
already has its own non-AI text. The assistant reports itself as unavailable
before anybody types, through `/service-config/capabilities`.

What you lose without it: the conversational assistant, the RAG answers over
your uploaded documents, the narrative summaries and the plain-language
data-quality diagnosis. What you keep: the forecasting engine, the semáforo,
the recommendations, the purchase orders and every alert — all of which are
computed locally and never involved the model.

There is deliberately **one** AI provider and no fallback chain. A missing or
mistyped key cannot quietly route your data somewhere else; it turns the
feature off.

---

## What stays, always

* Sales history, datasets and trained model artifacts — your Postgres and your
  `STORAGE_PATH` disk. Never uploaded anywhere.
* The forecasting itself — LightGBM, XGBoost, Prophet, ARIMA, ETS, Croston and
  the LSTM all train in your process, on your CPU.
* Credentials you type into `/instalacion` — encrypted at rest with a Fernet
  key that also lives on your disk (see `deploy/RESTORE.md`).
* The audit trail — who did what, and every alert that was sent or failed, in
  your database.

---

## Checking it yourself

Two ways, and neither requires trusting this page:

```sh
# 1. Every outbound host in the source, from the code that opens the socket.
#    `git ls-files` rather than a bare grep: `backend/` also contains the
#    virtualenv, and grepping that returns every URL in every dependency.
git ls-files 'backend/**/*.py' | grep -v "^backend/tests/"   | xargs grep -h "https://" | grep -oE "https://[a-z0-9.-]+" | sort -u

# 2. What the browser loads. Nothing external: no CDN, no font service,
#    no analytics — the only https:// in the frontend source is the wa.me
#    deep link below and two placeholder examples.
git ls-files 'Frontend/src/**' | xargs grep -h "https://"   | grep -oE "https://[a-z0-9.-]+" | sort -u

# 3. What the running instance believes it can reach
curl -s localhost:8000/health | jq .services
```

Run on 2026-09-16 the first command returns exactly the five hosts in the table
above, plus `wa.me` and two `example.com` placeholders from comments.
**`wa.me` is not a connection the server makes**: it is a link put inside a
WhatsApp message so the person receiving it can open the chat. Nothing is sent
to it by Faro.

`/health` reports every optional service as configured or not, by name. A
service that reports itself off is a connection that will not be opened.

---

## Data ownership and erasure

* A tenant's whole dataset can be exported as a ZIP and permanently deleted —
  `backend/tenants/data_export.py`, written against Costa Rica's Ley 8968 and
  GDPR-shaped erasure. Secrets (password hashes, token hashes, API-key hashes,
  webhook signing secrets) are excluded from the export by an explicit column
  list rather than by omission.
* Deleting a tenant removes its rows from every tenant-scoped table, not just
  the four with FK cascades — the function enumerates the rest deliberately,
  because a bare `DELETE FROM tenants` would orphan them.
* The database and the files are yours; there is no vendor copy to ask about.
