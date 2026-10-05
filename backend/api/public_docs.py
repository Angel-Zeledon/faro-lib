"""Public summaries and descriptions for API-key routes that carry none.

The developer reference is generated from OpenAPI, and OpenAPI takes a route's
description from its docstring. About half the exposed routes were written for
the product's own screens and have no docstring, so their public entry read
"Get Columns Alias" and nothing else. This catalogue supplies the missing text
WITHOUT touching those routers. An entry here wins over the docstring and
over the generated summary ("Log Po" is a function name, not English): the
docstrings in this codebase are written for maintainers, and a few open with a
sentence that means nothing to an integrator.

`test_public_api_surface.py` fails when an exposed operation ends up with an
empty description, so a new exposed route needs a docstring or a line here.
English: this is a developer reference, the same audience as the source.
"""
from __future__ import annotations

# (method, path without /api/v1) → (summary, description)
DOCS: dict[tuple[str, str], tuple[str, str]] = {
    # ── analyst / artifacts ─────────────────────────────────────────────────
    ("POST", "/sessions/{session_id}/analyst/query"): (
        "Ask the analyst about a session",
        "Answers a natural-language question about a completed session's results, grounded in its data and the tenant's documents."),
    ("GET", "/sessions/{session_id}/analyst/rag-status"): (
        "Analyst index status",
        "Whether the session's results and documents are indexed for the analyst, and how many chunks."),
    ("GET", "/sessions/{session_id}/artifacts"): (
        "List training artifacts",
        "Files produced by the session's training run (models, metrics, exports)."),
    ("GET", "/sessions/{session_id}/artifacts/download/{artifact_path}"): (
        "Download a training artifact",
        "Streams one artifact file listed by the artifacts endpoint."),

    # ── configuration (the training wizard) ─────────────────────────────────
    ("GET", "/sessions/{session_id}/columns"): (
        "Read the column mapping (alias)",
        "Alias of GET /sessions/{session_id}/configure/columns."),
    ("POST", "/sessions/{session_id}/columns"): (
        "Save the column mapping (alias)",
        "Alias of POST /sessions/{session_id}/configure/columns."),
    ("GET", "/sessions/{session_id}/config-summary"): (
        "Configuration summary",
        "Every configuration block of the session in one response, as the training run will read it."),
    ("GET", "/sessions/{session_id}/config/business"): (
        "Read business parameters",
        "Service level, lead time and cost parameters used to turn the forecast into stock signals."),
    ("POST", "/sessions/{session_id}/config/business"): (
        "Save business parameters",
        "Sets service level, default lead time, holding cost and stockout cost for the session."),
    ("POST", "/sessions/{session_id}/config/features"): (
        "Save feature settings (alias)",
        "Alias of POST /sessions/{session_id}/configure/features."),
    ("GET", "/sessions/{session_id}/config/forecast"): (
        "Read forecast settings",
        "Horizon and quantiles the session forecasts."),
    ("POST", "/sessions/{session_id}/config/forecast"): (
        "Save forecast settings",
        "Sets the forecast horizon, its mode and the quantiles to produce."),
    ("POST", "/sessions/{session_id}/config/models"): (
        "Save model selection (alias)",
        "Alias of POST /sessions/{session_id}/configure/models."),
    ("POST", "/sessions/{session_id}/config/training"): (
        "Save validation settings (alias)",
        "Alias of POST /sessions/{session_id}/configure/validation."),
    ("GET", "/sessions/{session_id}/configure/columns"): (
        "Read the column mapping",
        "Which dataset columns are the SKU, date, demand and optional fields."),
    ("POST", "/sessions/{session_id}/configure/columns"): (
        "Save the column mapping",
        "Maps the dataset's columns to the canonical fields (sku, date, demand, …)."),
    ("GET", "/sessions/{session_id}/configure/features"): (
        "Read feature settings",
        "Lags, rolling windows, calendar and Fourier features the session generates."),
    ("POST", "/sessions/{session_id}/configure/features"): (
        "Save feature settings",
        "Sets the lags, rolling windows, calendar, Fourier and holiday features to generate."),
    ("GET", "/sessions/{session_id}/configure/models"): (
        "Read model selection",
        "Which forecasting models the session trains and how the best one is chosen."),
    ("POST", "/sessions/{session_id}/configure/models"): (
        "Save model selection",
        "Selects the models to train, their hyperparameters and the selection metric. Routing only ever narrows this list."),
    ("GET", "/sessions/{session_id}/configure/remediations"): (
        "Read data remediations",
        "Data-quality fixes chosen for the session's dataset."),
    ("GET", "/sessions/{session_id}/configure/validation"): (
        "Read validation settings",
        "Train/test split, walk-forward validation and minimum history."),
    ("POST", "/sessions/{session_id}/configure/validation"): (
        "Save validation settings",
        "Sets the train/test split, walk-forward folds, minimum history and seasonal period."),
    ("POST", "/sessions/{session_id}/dataset"): (
        "Attach a dataset",
        "Attaches an uploaded dataset to the session."),
    ("GET", "/sessions/{session_id}/inspect"): (
        "Inspect the attached dataset",
        "Columns, types and sample rows of the session's dataset, with detected candidates for each canonical field."),
    ("GET", "/sessions/{session_id}/profile"): (
        "Dataset profile (alias)",
        "Alias of GET /sessions/{session_id}/inspect."),
    ("POST", "/sessions/{session_id}/upload"): (
        "Upload and attach a dataset",
        "Uploads a file (multipart) and attaches it to the session in one call."),

    # ── data sources ────────────────────────────────────────────────────────
    ("GET", "/data-sources"): (
        "List data sources",
        "The tenant's file and SQL data sources. Paginated with skip and limit."),
    ("POST", "/data-sources/file"): (
        "Create a file data source",
        "Uploads a CSV/Excel file (multipart) as a new data source."),
    ("POST", "/data-sources/sql"): (
        "Create a SQL data source",
        "Registers a database connection as a data source. Fields may come from a pasted `connection_string` (URL, JDBC, ADO.NET or libpq form); typed fields win. `ssl_mode` is disable, prefer, require, verify-ca or verify-full; `ssl_ca` is the server's CA certificate (PEM), stored encrypted like the password. Private and link-local hosts are refused unless the installation allows them (`data_source_host_not_allowed`)."),
    ("POST", "/data-sources/sql/parse"): (
        "Parse a connection string",
        "Reads a connection string into engine, host, port, database, user and TLS mode. The password is never returned, only `has_password`."),
    ("DELETE", "/data-sources/{source_id}"): (
        "Delete a data source",
        "Removes the data source."),
    ("GET", "/data-sources/{source_id}"): (
        "Get a data source",
        "One data source's metadata."),
    ("PATCH", "/data-sources/{source_id}"): (
        "Rename a data source",
        "Changes the data source's name and description."),
    ("GET", "/data-sources/{source_id}/analyze"): (
        "Analyze a data source",
        "Demand statistics over the source: totals, seasonality and per-SKU summaries, optionally within a date range."),
    ("GET", "/data-sources/{source_id}/analyze/{sku_id}"): (
        "Analyze one SKU in a data source",
        "The same analysis for a single SKU, with its time series."),
    ("GET", "/data-sources/{source_id}/edit-table"): (
        "Load a data source as an editable table",
        "Columns and rows of the source, for editing and saving as a new source."),
    ("POST", "/data-sources/{source_id}/execute-query"): (
        "Run a SQL query against a SQL source",
        "Executes ONE read statement (SELECT or WITH ... SELECT) on the customer's database, inside a read-only transaction that is always rolled back, and returns up to `limit` rows starting at `offset`, with `has_more`. Needs a `write`-scope key: it runs caller-written SQL on the customer's database. Anything else is refused with `sql_multiple_statements`, `sql_not_a_select`, `sql_forbidden_keyword`, `sql_forbidden_function` or `sql_unsupported_syntax`."),
    ("POST", "/data-sources/{source_id}/export-query"): (
        "Export a SQL query as Excel or CSV",
        "Runs the query (same read-only rules as execute-query) and returns the FULL result as an .xlsx (default) or .csv file, refusing rather than truncating past the row ceiling. Needs a `write`-scope key."),
    ("POST", "/data-sources/{source_id}/file"): (
        "Replace a data source's file",
        "Uploads a new file (multipart) IN PLACE: the source keeps its id and column mapping, so the next training run needs no reconfiguration. The nightly-export endpoint."),
    ("POST", "/data-sources/{source_id}/materialize"): (
        "Materialize a SQL query as a dataset",
        "Runs the query and stores the result as a dataset that sessions can train on."),
    ("GET", "/data-sources/{source_id}/preview"): (
        "Preview a data source",
        "The first rows of the source (and of a given sheet, for Excel)."),
    ("PATCH", "/data-sources/{source_id}/query"): (
        "Save a SQL source's query",
        "Stores the query the SQL source materializes."),
    ("POST", "/data-sources/{source_id}/save-as-new"): (
        "Save an edited table as a new source",
        "Creates a new data source from edited columns and rows."),
    ("PATCH", "/data-sources/{source_id}/sql-config"): (
        "Update a SQL source's connection",
        "Changes any connection field of a SQL source. Omitted fields keep their stored value, the password included, except when the engine, host or port changes: then the password must be sent again (`data_source_password_required`)."),
    ("POST", "/data-sources/{source_id}/test-connection"): (
        "Test a SQL source's connection",
        "Runs a staged test (dns, tcp, tls, auth, privileges, select, tables) and reports each stage as ok, warning, failed or skipped with a code. Warns when the login can write, with the least-privilege GRANT statements."),
    ("GET", "/data-sources/{source_id}/schema"): (
        "List a SQL source's tables",
        "Tables and views the connection can read, with row estimates and a safely quoted preview statement for each. Cached for a few minutes; `refresh=true` re-reads."),
    ("GET", "/data-sources/{source_id}/schema/columns"): (
        "List a table's columns",
        "Column names, types and nullability of one table of a SQL source."),

    # ── datasets ────────────────────────────────────────────────────────────
    ("GET", "/datasets"): (
        "List datasets",
        "Uploaded datasets. Paginated with skip and limit."),
    ("POST", "/datasets"): (
        "Upload a dataset",
        "Uploads a sales-history file (multipart) and profiles it."),
    ("GET", "/datasets/{dataset_id}"): (
        "Get a dataset",
        "One dataset's metadata and profile."),

    # ── documents ───────────────────────────────────────────────────────────
    ("GET", "/documents"): (
        "List documents",
        "Documents uploaded for the analyst to read."),
    ("DELETE", "/documents/{doc_id}"): (
        "Delete a document",
        "Removes the document and its index."),
    ("GET", "/documents/{doc_id}"): (
        "Get a document",
        "One document's metadata."),
    ("GET", "/documents/{doc_id}/status"): (
        "Document indexing status",
        "Whether the document has been processed and indexed."),

    # ── forecasts ───────────────────────────────────────────────────────────
    ("GET", "/config/schema"): (
        "Configuration schema",
        "The JSON schema of every configuration block, independent of a session."),
    ("GET", "/models/available"): (
        "Available forecasting models",
        "The forecasting models this deployment can train."),
    ("GET", "/sessions/{session_id}/accuracy"): (
        "Forecast accuracy",
        "Accuracy of the session's forecasts against baselines, per SKU and overall."),
    ("GET", "/sessions/{session_id}/available-models"): (
        "Models available to a session",
        "The models that can be trained on this session's data."),
    ("GET", "/sessions/{session_id}/config-schema"): (
        "Session configuration schema",
        "The configuration schema with this session's current values."),
    ("POST", "/sessions/{session_id}/drift"): (
        "Detect drift",
        "Compares a new file (multipart) with the data the session was trained on and reports distribution drift."),
    ("GET", "/sessions/{session_id}/export-config"): (
        "Export configuration (alias)",
        "The session's full configuration as one exportable document."),
    ("GET", "/sessions/{session_id}/forecast-series/{sku}"): (
        "Forecast series for a SKU",
        "History and forecast (with quantiles) for one SKU, optionally for a given model."),
    ("GET", "/sessions/{session_id}/inventory"): (
        "Session inventory recommendations",
        "Per-SKU inventory recommendations computed from this session's forecast."),
    ("GET", "/sessions/{session_id}/metrics"): (
        "Training metrics",
        "Validation metrics per model and SKU."),
    ("GET", "/sessions/{session_id}/overrides"): (
        "List forecast overrides",
        "Manual adjustments made to the session's forecast."),
    ("PATCH", "/sessions/{session_id}/overrides"): (
        "Save forecast overrides",
        "Upserts manual forecast values per SKU and date, with a reason. The body is a list."),
    ("POST", "/sessions/{session_id}/predict"): (
        "Predict on demand",
        "Runs the trained model for one SKU with the given inputs and horizon."),
    ("GET", "/sessions/{session_id}/quality"): (
        "Data quality",
        "Data-quality findings for the session's dataset."),
    ("GET", "/sessions/{session_id}/report"): (
        "Session report (alias)",
        "The session's results summarized as one report document."),
    ("GET", "/sessions/{session_id}/results"): (
        "Training results",
        "Forecasts and per-model results of a completed session, at SKU or aggregate level."),
    ("GET", "/sessions/{session_id}/routing"): (
        "Model routing",
        "Which model was routed to each SKU and why."),
    ("GET", "/sessions/{session_id}/routing-plan"): (
        "Model routing plan (alias)",
        "The routing decisions as a plan, per SKU segment."),
    ("GET", "/sessions/{session_id}/sku-intelligence/{sku}"): (
        "SKU intelligence",
        "Everything known about one SKU in the session: series, model, drivers and recommendation."),

    # ── inventory ───────────────────────────────────────────────────────────
    ("POST", "/inventory/log-po"): (
        "Record a purchase order",
        "Records that an order was placed, with its lines. Reception tracking and supplier lead-time learning read it. A body without `items` records every actionable SKU of the session."),
    ("DELETE", "/inventory/bom/{parent_sku}/{child_sku}"): (
        "Remove a bill-of-materials line",
        "Removes a component from a parent SKU's bill of materials."),
    ("PUT", "/inventory/bom/{parent_sku}/{child_sku}"): (
        "Set a bill-of-materials line",
        "Creates or updates how much of a component one unit of the parent SKU uses."),
    ("GET", "/inventory/events"): (
        "List demand events",
        "Promotions and seasonal events that multiply expected demand."),
    ("POST", "/inventory/events"): (
        "Create a demand event",
        "Adds an event with a date range and a demand multiplier."),
    ("GET", "/inventory/events/upcoming"): (
        "Upcoming demand events",
        "Events starting within the next `days` days."),
    ("DELETE", "/inventory/events/{event_id}"): (
        "Delete a demand event",
        "Removes the event."),
    ("PATCH", "/inventory/events/{event_id}"): (
        "Update a demand event",
        "Changes an event's name, dates, multiplier, notes or active flag."),
    ("DELETE", "/inventory/price-breaks/{price_break_id}"): (
        "Delete a price break",
        "Removes a supplier's quantity price break."),
    ("GET", "/inventory/stock"): (
        "List stock",
        "Every SKU's stock record: on hand, minimum, lead time, cost, supplier and catalogue fields."),
    ("DELETE", "/inventory/stock/{sku}"): (
        "Delete a stock record",
        "Removes the SKU's stock record."),
    ("GET", "/inventory/stock/{sku}"): (
        "Get a stock record",
        "One SKU's stock record."),
    ("PUT", "/inventory/stock/{sku}"): (
        "Create or update a stock record",
        "Upserts a SKU's stock: on hand, minimum, lead time, cost, MOQ, supplier, price and catalogue fields."),
    ("PATCH", "/inventory/stock/{sku}/product-type"): (
        "Set a SKU's product type",
        "finished_good, semi_finished, component, raw_material, packaging or service."),
    ("GET", "/inventory/stock/{sku}/suppliers"): (
        "A SKU's suppliers",
        "Suppliers that sell this SKU, with cost, MOQ and lead time per supplier."),
    ("DELETE", "/inventory/stock/{sku}/suppliers/{supplier_id}"): (
        "Unlink a supplier from a SKU",
        "Removes the supplier from the SKU's supplier list."),
    ("PUT", "/inventory/stock/{sku}/suppliers/{supplier_id}"): (
        "Link a supplier to a SKU",
        "Sets the supplier's cost, MOQ, lead time and primary flag for the SKU."),
    ("GET", "/inventory/suppliers"): (
        "List suppliers",
        "The tenant's suppliers with contact details, lead times and payment terms."),
    ("POST", "/inventory/suppliers"): (
        "Create a supplier",
        "Adds a supplier with contact details, lead time and payment terms."),
    ("DELETE", "/inventory/suppliers/{supplier_id}"): (
        "Deactivate a supplier",
        "Deactivates the supplier (see the reactivate endpoint)."),
    ("PATCH", "/inventory/suppliers/{supplier_id}"): (
        "Update a supplier",
        "Changes a supplier's contact details, lead time, review period or payment terms."),
    ("POST", "/inventory/suppliers/{supplier_id}/price-breaks"): (
        "Set a price break",
        "Creates or updates the unit price a supplier charges from a minimum quantity of a SKU."),
    ("GET", "/inventory/transfers"): (
        "List transfers",
        "Stock transfers between warehouses, optionally filtered by status."),
    ("POST", "/inventory/transfers"): (
        "Create a transfer",
        "Creates a stock transfer from one warehouse to another, with its lines."),
    ("POST", "/inventory/transfers/{transfer_id}/cancel"): (
        "Cancel a transfer",
        "Cancels a transfer that has not been received."),
    ("POST", "/inventory/transfers/{transfer_id}/receive"): (
        "Receive a transfer",
        "Records the quantities that arrived at the destination warehouse."),
    ("GET", "/inventory/warehouses"): (
        "List warehouses",
        "The tenant's warehouses."),
    ("POST", "/inventory/warehouses"): (
        "Create a warehouse",
        "Adds a warehouse, optionally as the default."),
    ("PUT", "/inventory/warehouses/lanes"): (
        "Set a transfer lane",
        "Creates or updates the lead time and costs of moving stock between two warehouses."),

    # ── planning / reports / scenarios / schedule ───────────────────────────
    ("GET", "/planning"): (
        "Planning context",
        "The active planning period and `active_session_id` — the session the app's own screens show."),
    ("POST", "/sessions/{session_id}/reports/generate"): (
        "Generate a report",
        "Starts generating the session's report files; poll the status endpoint."),
    ("GET", "/sessions/{session_id}/reports/{format}"): (
        "Download a report",
        "Downloads a generated report in the given format."),
    ("DELETE", "/scenarios/{scenario_id}"): (
        "Delete a scenario",
        "Removes a saved what-if scenario."),
    ("GET", "/scenarios/{scenario_id}"): (
        "Get a scenario",
        "One saved what-if scenario with its parameters and results."),
    ("DELETE", "/sessions/{session_id}/schedule"): (
        "Delete a retraining schedule",
        "Stops the session's scheduled retraining."),
    ("GET", "/sessions/{session_id}/schedule"): (
        "Get a retraining schedule",
        "The session's cron schedule for automatic retraining."),
    ("POST", "/sessions/{session_id}/schedule"): (
        "Save a retraining schedule",
        "Sets a cron expression for automatic retraining, and whether it is enabled."),

    # ── sessions / training ─────────────────────────────────────────────────
    ("GET", "/sessions"): (
        "List sessions",
        "Forecast sessions with their state. Paginated with skip and limit."),
    ("POST", "/sessions"): (
        "Create a session",
        "Creates a forecast session in DRAFT."),
    ("DELETE", "/sessions/{session_id}"): (
        "Delete a session",
        "Deletes the session and its results."),
    ("GET", "/sessions/{session_id}"): (
        "Get a session",
        "One session's metadata and state."),
    ("PATCH", "/sessions/{session_id}"): (
        "Update a session",
        "Changes a session's name, description or tags."),
    ("DELETE", "/jobs/{job_id}"): (
        "Cancel a job",
        "Cancels a queued or running training job."),
    ("GET", "/jobs/{job_id}"): (
        "Get a job",
        "A training job's state and progress."),
    ("GET", "/jobs/{job_id}/logs"): (
        "Job logs",
        "The last `tail` log lines of a training job."),
    ("GET", "/sessions/{session_id}/jobs"): (
        "List a session's jobs",
        "Training jobs run for the session."),
    ("POST", "/sessions/{session_id}/train"): (
        "Start training",
        "Queues a training run for a configured session. Poll /sessions/{session_id}/train/status."),

    # ── webhooks ────────────────────────────────────────────────────────────
    ("GET", "/webhooks"): (
        "List webhooks",
        "Outgoing webhooks and the events they subscribe to."),
    ("POST", "/webhooks"): (
        "Create a webhook",
        "Subscribes an https URL to job.completed / job.failed events."),
    ("DELETE", "/webhooks/{webhook_id}"): (
        "Delete a webhook",
        "Removes the webhook."),
}


# (method, path) -> summary, for routes whose description is fine but whose generated
# summary is a function name ("Get Roi", "Po Items"). A DOCS entry above wins.
SUMMARIES: dict[tuple[str, str], str] = {
    ("POST", "/ai/narrative/forecast-explanation"): "Explain a SKU's recommendation",
    ("POST", "/ai/narrative/inventory"): "Write an inventory insight",
    ("POST", "/ai/narrative/morning"): "Write the morning briefing narrative",
    ("POST", "/ai/suggested-questions"): "Suggest questions for the analyst",
    ("GET", "/alerts"): "List alerts",
    ("GET", "/alerts/activity"): "List account activity",
    ("GET", "/alerts/kinds"): "List alert kinds",
    ("POST", "/sessions/{session_id}/analyst/narrate"): "Put data into words",
    ("GET", "/sessions/{session_id}/analysis"): "Analyze the attached dataset",
    ("POST", "/sessions/{session_id}/configure/remediations"): "Answer the data-quality gate",
    ("GET", "/sessions/{session_id}/data-gate"): "Read the data-quality gate",
    ("GET", "/sessions/{session_id}/health"): "Check dataset health",
    ("GET", "/sessions/{session_id}/models/hyperparams"): "List model hyperparameters",
    ("GET", "/tenant/currency"): "Read the currency",
    ("POST", "/documents"): "Upload a document",
    ("GET", "/documents/{doc_id}/content"): "Download a document",
    ("GET", "/entitlements"): "Read plan and limits",
    ("GET", "/sessions/{session_id}/decomposition/{sku}"): "Decompose a SKU's history",
    ("GET", "/sessions/{session_id}/forecast-vs-actual"): "Compare forecast with actual sales",
    ("POST", "/sessions/{session_id}/reconcile"): "Reconcile actual values",
    ("GET", "/sessions/{session_id}/shap/{sku}"): "Read a SKU's feature importances",
    ("GET", "/sessions/{session_id}/warnings"): "List run warnings",
    ("GET", "/data-freshness"): "Read data freshness",
    ("POST", "/inventory/alerts/send-now"): "Send the daily alert now",
    ("GET", "/inventory/bom/{child_sku}/used-in"): "List where a component is used",
    ("GET", "/inventory/bom/{parent_sku}"): "Read a bill of materials",
    ("POST", "/inventory/bulk"): "Import stock from a file",
    ("POST", "/inventory/bulk/preview"): "Preview a stock import",
    ("GET", "/inventory/cash-calendar"): "Read the cash calendar",
    ("POST", "/inventory/cash-calendar/fit"): "Fit a purchase into a budget",
    ("GET", "/inventory/dashboard-summary"): "Read the dashboard summary",
    ("GET", "/inventory/dead-capital"): "List dead capital",
    ("GET", "/inventory/events/catalog"): "Read the event catalog",
    ("POST", "/inventory/events/catalog/seed"): "Seed the event catalog",
    ("PATCH", "/inventory/events/catalog/{catalog_key}"): "Toggle a catalog event",
    ("POST", "/inventory/events/simulate"): "Simulate a demand event",
    ("GET", "/inventory/events/{event_id}/multipliers"): "List an event's multipliers",
    ("PUT", "/inventory/events/{event_id}/multipliers"): "Set an event multiplier",
    ("DELETE", "/inventory/events/{event_id}/multipliers/{override_id}"): "Remove an event multiplier",
    ("GET", "/inventory/forecast-money"): "Project revenue, cost and margin",
    ("GET", "/inventory/margin-erosion"): "List margin erosion",
    ("GET", "/inventory/morning-briefing"): "Read the morning briefing",
    ("GET", "/inventory/optimize"): "Optimize purchases and transfers",
    ("POST", "/inventory/po"): "Create a manual purchase order",
    ("GET", "/inventory/po-history"): "List recent purchase orders",
    ("POST", "/inventory/po/import"): "Import purchase orders from a file",
    ("POST", "/inventory/po/import/preview"): "Preview a purchase-order import",
    ("GET", "/inventory/po/import/template"): "Download the purchase-order import template",
    ("GET", "/inventory/po/overdue"): "List overdue purchase orders",
    ("GET", "/inventory/po/{po_log_id}/items"): "List a purchase order's lines",
    ("POST", "/inventory/po/{po_log_id}/receive"): "Receive a purchase order",
    ("POST", "/inventory/po/{po_log_id}/send"): "Send a purchase order to suppliers",
    ("GET", "/inventory/price-breaks"): "List price breaks",
    ("POST", "/inventory/price-breaks/evaluate"): "Evaluate price breaks for a cart",
    ("GET", "/inventory/product-types"): "List product types",
    ("GET", "/inventory/production-requirements"): "Explode demand into components",
    ("GET", "/inventory/report/pdf"): "Download the inventory PDF",
    ("GET", "/inventory/roi"): "Read accumulated ROI",
    ("GET", "/inventory/roi/month-report"): "Read one month's ROI report",
    ("GET", "/inventory/roi/monthly"): "List monthly ROI",
    ("GET", "/inventory/setup-gaps"): "List SKUs missing setup",
    ("GET", "/inventory/shrinkage"): "List shrinkage",
    ("POST", "/inventory/shrinkage"): "Record shrinkage",
    ("GET", "/inventory/shrinkage/reasons"): "List shrinkage reasons",
    ("GET", "/inventory/status"): "Read the inventory traffic light",
    ("GET", "/inventory/status/export-po"): "Export the purchase order as CSV",
    ("PATCH", "/inventory/stock/{sku}"): "Update part of a stock record",
    ("GET", "/inventory/stock/{sku}/history"): "Read a SKU's stock history",
    ("GET", "/inventory/supplier-cost-inflation"): "List supplier cost inflation",
    ("GET", "/inventory/suppliers/contact-health"): "Check supplier contact details",
    ("POST", "/inventory/suppliers/import"): "Import suppliers from a file",
    ("POST", "/inventory/suppliers/import/preview"): "Preview a suppliers import",
    ("GET", "/inventory/suppliers/import/template"): "Download the suppliers import template",
    ("GET", "/inventory/suppliers/lead-time-alerts"): "List supplier lead-time alerts",
    ("GET", "/inventory/suppliers/scorecard"): "Read the supplier scorecard",
    ("POST", "/inventory/suppliers/{supplier_id}/reactivate"): "Reactivate a supplier",
    ("GET", "/inventory/template.csv"): "Download the inventory import template",
    ("POST", "/inventory/transfers/{transfer_id}/close"): "Close a transfer",
    ("DELETE", "/inventory/warehouses/lanes"): "Delete a transfer lane",
    ("GET", "/inventory/warehouses/lanes"): "List transfer lanes",
    ("PATCH", "/inventory/warehouses/{name}"): "Update a warehouse",
    ("GET", "/inventory/recommendation-log/cost-of-ignoring"): "Read the cost of ignoring recommendations",
    ("GET", "/inventory/recommendation-log/{sku}/why-changed"): "Explain why a recommendation changed",
    ("POST", "/inventory/po/{po_log_id}/unreceive"): "Undo a purchase-order reception",
    ("POST", "/inventory/po/{po_log_id}/unsend"): "Undo marking a purchase order as sent",
    ("POST", "/mcp"): "Call the MCP server (JSON-RPC)",
    ("GET", "/sessions/{session_id}/reports/status"): "Read report generation status",
    ("GET", "/sessions/{session_id}/scenarios"): "List scenarios",
    ("POST", "/sessions/{session_id}/scenarios"): "Save a scenario",
    ("POST", "/sessions/{session_id}/scenarios/preview"): "Preview a scenario",
    ("POST", "/sessions/{session_id}/scenarios/{scenario_id}/run"): "Run a saved scenario",
    ("GET", "/schedules"): "List retraining schedules",
    ("GET", "/schedules/history"): "List scheduler history",
    ("GET", "/sessions/summary"): "List session summaries",
    ("GET", "/tenant/timezone"): "Read the time zone",
    ("GET", "/jobs/active"): "List active jobs",
    ("GET", "/sessions/{session_id}/train/status"): "Read training status",
}
