# Integration templates

Importable workflows for no-code tools, built on the public REST API and the signed
webhooks. They need no hosted StockAI app and no third-party account to ship.

| File | What it does |
|---|---|
| `n8n/order-approved-to-slack-and-sheets.json` | `purchase_order.approved` -> a Slack message and a Google Sheets row |
| `n8n/stockout-imminent-to-jira.json` | `stockout.imminent` -> a Jira issue |
| `n8n/drive-file-to-dataset.json` | a new file in a Google Drive folder replaces a StockAI dataset (`POST /api/v1/data-sources/{id}/file`) |

**Status.** These were written from n8n's documented node parameters and are checked
by `backend/tests/test_integration_templates.py` (valid graph, only real endpoints and
real event fields, no secrets, and the signature step run under Node against real
signatures). They have not been imported into a live n8n by the authors: after
importing, open each node, pick your credentials and fill the blanks.

**Webhook templates.** Create the webhook in StockAI (Automation > Webhooks), point it
at the workflow's production URL and subscribe it to the one event the template
handles. Put the signing secret (shown once) in the n8n variable
`STOCKAI_WEBHOOK_SECRET` and allow the crypto module (`NODE_FUNCTION_ALLOW_BUILTIN=crypto`).
The first node of every webhook template verifies the signature and age of each
delivery and drops repeats; do not remove it.

**Make and Zapier** do not take an importable file from us, so build the same flows
with their generic modules:

- *Webhook in:* a custom webhook trigger (Make: "Custom webhook"; Zapier: "Catch Hook").
  StockAI signs each delivery; verification needs code, so in Zapier use a "Code by
  Zapier" step with the recipe in `backend/webhooks/signing.py`.
- *Call StockAI:* Make "HTTP > Make a request" / Zapier "Webhooks by Zapier > Custom
  Request". URL `https://app.stockai.es/api/v1/<path>`, header
  `Authorization: Bearer <key>` (a write key for POST/PATCH/DELETE). A file goes as
  `multipart/form-data` with the field name `file`.
- A hosted Zapier or Make app (a connector that appears in their catalogue) is not
  built: it needs the vendor's review and an account. It will be built when a customer
  asks for it by name.
