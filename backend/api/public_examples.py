"""Request-body documentation for the routes whose schema cannot say it.

The developer reference builds each request body's schema and example from the
route's OpenAPI. Five routes accept a bare `dict` (the handler reads keys out of
it by hand), so OpenAPI can only say "an object" and the reference would show
`{}`. This module supplies what those handlers actually read, and an example
for them. An example is also accepted here for any other route whose generated
one is not good enough.

`test_public_api_examples.py` checks every entry against the handler it
documents: the keys named here must be read by the route, and every example
must be accepted by it (a 422 on the documented example fails the suite).
Keys here are `(METHOD, path without /api/v1)`.
"""
from __future__ import annotations

# Documented fields of a free-form object body, in display order. Each entry is
# a schema node (see export_public_api._node) plus `name` and `required`.
BODY_FIELDS: dict[tuple[str, str], list[dict]] = {
    ("POST", "/mcp"): [
        {"name": "jsonrpc", "required": True, "type": "string", "enum": ["2.0"],
         "description": "JSON-RPC version; always \"2.0\"."},
        {"name": "id", "required": False, "type": "string | integer",
         "description": "Request id echoed in the answer. Omit it to send a notification (no answer body)."},
        {"name": "method", "required": True, "type": "string",
         "enum": ["initialize", "tools/list", "tools/call", "ping"],
         "description": "The MCP method."},
        {"name": "params", "required": False, "type": "object", "free_form": True,
         "description": "Method parameters. `tools/call` takes {name, arguments}."},
    ],
    ("POST", "/sessions/{session_id}/configure/columns"): [
        {"name": "canonical_mapping", "required": True, "type": "object",
         "values": {"type": "string", "nullable": True},
         "description": "Maps each canonical field (sku, date, demand, ...) to a column of the uploaded file. sku, date and demand are required; the others may be null."},
        {"name": "defaults_override", "required": False, "type": "object", "free_form": True,
         "description": "Values that replace the canonical defaults for this session."},
    ],
    ("POST", "/sessions/{session_id}/columns"): [
        {"name": "canonical_mapping", "required": True, "type": "object",
         "values": {"type": "string", "nullable": True},
         "description": "Maps each canonical field (sku, date, demand, ...) to a column of the uploaded file. sku, date and demand are required; the others may be null."},
        {"name": "defaults_override", "required": False, "type": "object", "free_form": True,
         "description": "Values that replace the canonical defaults for this session."},
    ],
    ("POST", "/sessions/{session_id}/reports/generate"): [
        {"name": "type", "required": False, "type": "string", "default": "operational",
         "enum": ["executive", "operational", "technical", "inventory"],
         "description": "Which report to build."},
        {"name": "formats", "required": False, "type": "array", "default": ["excel"],
         "items": {"type": "string", "enum": ["excel", "pdf"]},
         "description": "One or more output formats."},
    ],
    ("POST", "/sessions/{session_id}/analyst/query"): [
        {"name": "question", "required": True, "type": "string",
         "description": "The question to answer from the session's results."},
        {"name": "sku", "required": False, "type": "string", "nullable": True,
         "description": "Restrict the answer to one SKU."},
        {"name": "history", "required": False, "type": "array", "items": {"type": "object", "free_form": True},
         "description": "Earlier turns of the conversation, as {role, content} objects."},
        {"name": "top_k", "required": False, "type": "integer", "default": 8,
         "description": "How many passages to retrieve."},
    ],
    ("POST", "/sessions/{session_id}/analyst/narrate"): [
        {"name": "data", "required": True, "type": "any",
         "description": "The JSON to put into words: any object or list."},
        {"name": "context", "required": True, "type": "string",
         "description": "What the data is, in a sentence."},
        {"name": "question", "required": False, "type": "string", "nullable": True,
         "description": "A specific question about the data."},
        {"name": "history", "required": False, "type": "array", "items": {"type": "object", "free_form": True},
         "description": "Earlier turns of the conversation."},
    ],
}

# Bodies the generated example gets wrong or leaves empty.
BODY_EXAMPLES: dict[tuple[str, str], object] = {
    ("POST", "/inventory/warehouses"): {"name": "secondary", "is_default": False},
    ("PUT", "/inventory/warehouses/lanes"): {
        "from_warehouse": "principal", "to_warehouse": "secondary",
        "lead_time_days": 3, "cost_per_unit": 0.1, "fixed_cost": 5.0,
    },
    ("POST", "/inventory/transfers"): {
        "from_warehouse": "principal", "to_warehouse": "secondary",
        "items": [{"sku": "SKU-001", "qty": 10}], "notes": "Weekly rebalancing",
    },
    ("POST", "/inventory/shrinkage"): {
        "sku": "SKU-001", "quantity": 2, "reason": "breakage", "warehouse": "principal",
        "notes": "Dropped pallet", "occurred_at": "2026-10-01T08:00:00Z",
    },
    ("POST", "/inventory/suppliers"): {
        "name": "Distribuidora Andina", "email": "ventas@andina.example", "lead_time_days": 7,
    },
    ("POST", "/inventory/po/{po_log_id}/receive"): {"lines": [{"sku": "SKU-001", "received_qty": 100}]},
    ("POST", "/sessions/{session_id}/configure/remediations"): {"remediations": {"duplicates": "duplicates_sum"}},
    ("POST", "/inventory/transfers/{transfer_id}/receive"): {"lines": [{"sku": "SKU-001", "received_qty": 10}]},
    ("POST", "/data-sources/sql"): {
        "name": "ERP sales", "host": "db.example.com", "port": 5432, "database": "erp",
        "username": "readonly", "password": "<password>", "engine": "postgresql",
        "description": "Nightly sales view",
    },
    ("PATCH", "/data-sources/{source_id}/sql-config"): {
        "host": "db.example.com", "port": 5432, "database": "erp",
        "username": "readonly", "engine": "postgresql", "password": "<password>",
    },
    ("POST", "/sessions/{session_id}/configure/models"): {
        "mode": "selected", "selected_models": ["lightgbm", "prophet"],
        "auto_select_best": True, "selection_metric": "wape",
    },
    ("POST", "/sessions/{session_id}/config/models"): {
        "mode": "selected", "selected_models": ["lightgbm", "prophet"],
        "auto_select_best": True, "selection_metric": "wape",
    },
    ("POST", "/sessions/{session_id}/config/forecast"): {
        "horizon": 14, "quantiles": [0.1, 0.9], "horizon_mode": "unified",
    },
    ("POST", "/webhooks"): {"url": "https://example.com/hooks/stockai", "events": ["job.completed"]},
    ("POST", "/sessions/{session_id}/schedule"): {"cron_expr": "0 6 * * 1", "enabled": True},
    ("POST", "/sessions/{session_id}/scenarios"): {
        "name": "Black Friday +30%",
        "rules": [{"type": "demand_multiplier", "multiplier": 1.3, "category": "Electronics"}],
    },
    ("POST", "/sessions/{session_id}/scenarios/preview"): {
        "name": "Black Friday +30%",
        "rules": [{"type": "demand_multiplier", "multiplier": 1.3, "category": "Electronics"}],
    },
    ("PATCH", "/inventory/stock/{sku}"): {"current_stock": 120},
    ("PATCH", "/inventory/suppliers/{supplier_id}"): {"lead_time_days": 10},
    ("POST", "/sessions"): {"name": "October forecast", "description": "Monthly run", "tags": ["monthly"]},
    ("PATCH", "/sessions/{session_id}"): {"name": "October forecast (final)"},
    ("POST", "/inventory/events/catalog/seed"): {"country": "CR", "years": [2026, 2027]},
    ("POST", "/data-sources/{source_id}/execute-query"): {"sql": "SELECT sku, date, quantity FROM sales", "limit": 500},
    ("POST", "/data-sources/{source_id}/materialize"): {"sql": "SELECT sku, date, quantity FROM sales", "name": "Sales snapshot"},
    ("POST", "/data-sources/{source_id}/export-query"): {"sql": "SELECT sku, date, quantity FROM sales", "name": "Sales snapshot"},
    ("PATCH", "/data-sources/{source_id}/query"): {"sql": "SELECT sku, date, quantity FROM sales"},
    ("POST", "/data-sources/{source_id}/save-as-new"): {
        "name": "Sales (edited)", "columns": ["sku", "date", "quantity"],
        "rows": [{"sku": "SKU-001", "date": "2026-10-01", "quantity": 12}],
    },
    ("PUT", "/inventory/events/{event_id}/multipliers"): {"scope": "category", "scope_value": "Electronics", "multiplier": 1.5},
    ("POST", "/inventory/cash-calendar/fit"): {
        "items": [{"sku": "SKU-001", "supplier_name": "Distribuidora Andina", "quantity": 100, "unit_cost": 2.5}],
        "budget": 5000,
    },
    ("POST", "/inventory/log-po"): {
        "items": [{"sku": "SKU-001", "recommended_qty": 120, "final_qty": 100, "status": "modified", "unit_cost": 2.5}],
        "destination_warehouse": "principal",
    },
    ("POST", "/mcp"): {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ("POST", "/sessions/{session_id}/configure/columns"): {
        "canonical_mapping": {"sku": "sku", "date": "date", "demand": "quantity"},
    },
    ("POST", "/sessions/{session_id}/columns"): {
        "canonical_mapping": {"sku": "sku", "date": "date", "demand": "quantity"},
    },
    ("POST", "/sessions/{session_id}/reports/generate"): {"type": "operational", "formats": ["excel"]},
    ("POST", "/sessions/{session_id}/analyst/query"): {
        "question": "Which SKUs are most likely to run out next month?",
    },
    ("POST", "/sessions/{session_id}/analyst/narrate"): {
        "data": {"sku": "SKU-001", "forecast_next_30_days": 420},
        "context": "Forecast for one product",
    },
}
