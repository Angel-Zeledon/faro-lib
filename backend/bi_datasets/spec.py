"""The contract of the flat datasets: names, columns, types, sort order.

This is the ONLY place a dataset's columns are declared. The loaders in
`service.py` produce dicts keyed by these names, `render.py` shapes them into
JSON / CSV in exactly this order, and `GET /datasets/schema` publishes this
table. A BI report built on a dataset (Power BI, Excel, Looker...) binds to
column NAMES and ORDER, so the rules are:

  * adding a column is allowed (append it at the END of the list);
  * renaming, removing, reordering or retyping a column is a breaking change and
    needs a new dataset version (`version`), never an edit in place.

`test_bi_datasets_contract.py` pins the exact column set of every dataset, so a
rename fails a test instead of breaking somebody's dashboard overnight.

Pure: no I/O, no pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Column types. They are the contract a BI tool maps to its own types:
#   string    text
#   integer   whole number
#   number    decimal, '.' as the separator, no thousands separator, no exponent
#   boolean   true / false
#   date      ISO 8601 calendar date, YYYY-MM-DD
#   datetime  ISO 8601 instant in UTC, YYYY-MM-DDTHH:MM:SSZ
TYPES = ("string", "integer", "number", "boolean", "date", "datetime")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown column type {self.type!r} for {self.name!r}")


@dataclass(frozen=True)
class Dataset:
    name: str
    version: str
    description: str
    columns: tuple[Column, ...]
    # The sort the rows come back in, as column names. Always unique per row, so
    # paging never repeats or skips a row between two calls.
    sort: tuple[str, ...]
    # Query parameters this dataset accepts besides format/page/limit. Anything
    # else is refused (see `unknown_parameters`): a filter that is silently
    # ignored is a report that quietly shows the wrong numbers.
    parameters: tuple[str, ...] = ()
    # The datetime column `updated_since` filters on, if the dataset has one.
    updated_since_column: str | None = None
    # What a warehouse-scoped caller gets.
    scope_note: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def accepted_parameters(self) -> frozenset[str]:
        base = {"format", "page", "limit"} | set(self.parameters)
        if self.updated_since_column:
            base.add("updated_since")
        return frozenset(base)


def _c(name: str, type_: str, description: str) -> Column:
    return Column(name, type_, description)


INVENTORY_STATUS = Dataset(
    name="inventory-status",
    version="1",
    description=(
        "One row per SKU and warehouse that has a forecast in the session, with the "
        "stock signal and the purchase recommendation. The same rows and the same "
        "numbers as the per-warehouse view of the Inventory status screen."
    ),
    columns=(
        _c("session_id", "string", "The forecast session the row was computed from."),
        _c("period", "string", "Planning period of the figures: daily, weekly or monthly. "
                               "daily_demand, coverage_days and reorder_point are in this unit."),
        _c("sku", "string", "Product code."),
        _c("warehouse", "string", "Warehouse name."),
        _c("warehouse_id", "string", "Warehouse id (null when the name matches no warehouse)."),
        _c("display_name", "string", "Product name."),
        _c("supplier", "string", "Supplier the recommendation is placed with."),
        _c("signal", "string", "PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK or SIN_DATOS."),
        _c("current_stock", "number", "Units on hand (null when no stock was recorded here)."),
        _c("incoming_qty", "number", "Units already on their way: open purchase orders and transfers."),
        _c("daily_demand", "number", "Forecast demand per planning period."),
        _c("coverage_days", "number", "How long the stock lasts, in planning periods "
                                      "(null when it is not bounded)."),
        _c("lead_time_days", "number", "Supplier lead time in days."),
        _c("lead_time_source", "string", "Where the lead time comes from "
                                         "(learned, supplier rule, default...)."),
        _c("moq", "number", "Minimum order quantity."),
        _c("reorder_point", "number", "Stock level at which to reorder."),
        _c("recommended_qty", "number", "Units to order now."),
        _c("recommended_action", "string", "order or transfer (empty when nothing is needed)."),
        _c("unit_cost", "number", "Cost per unit (null when unknown)."),
        _c("forecast_source", "string", "trained when the demand comes from the SKU's own "
                                        "model, analogy when it borrows another product's "
                                        "history (null when the SKU has no forecast)."),
        _c("money_at_risk", "number", "Sales value a purchase delay puts in danger. Present "
                                      "only for PEDIR_YA / PEDIR_PRONTO rows with a price or "
                                      "cost; null otherwise (never 0 for unknown)."),
        _c("money_at_risk_basis", "string", "price, cost or unknown: what money_at_risk is valued at."),
    ),
    sort=("sku", "warehouse"),
    parameters=("session_id",),
    scope_note="Rows of the caller's warehouses only; transfers pointing at a warehouse "
               "outside the scope are removed.",
    notes=("SKUs with a forecast in the session. Stock rows without a forecast are not listed.",),
)

PURCHASE_ORDER_LINES = Dataset(
    name="purchase-order-lines",
    version="1",
    description=(
        "Every line of every purchase order, with its order, supplier, state, dates, "
        "quantity, cost and received quantity: one call, no per-order requests."
    ),
    columns=(
        _c("po_number", "integer", "The order number shown to people (OC-000123 is 123)."),
        _c("po_log_id", "string", "Stable id of the order."),
        _c("line_id", "string", "Stable id of the line."),
        _c("ordered_at", "datetime", "When the order was generated."),
        _c("updated_at", "datetime", "Latest of generated, sent, paid, received and cancelled."),
        _c("sent_at", "datetime", "When the order was sent to the supplier."),
        _c("paid_at", "datetime", "When the invoice was marked paid."),
        _c("received_at", "datetime", "Date of the latest reception recorded."),
        _c("cancelled_at", "datetime", "When the order was cancelled."),
        _c("reception_status", "string", "pending, partial, received or not_received."),
        _c("approval_status", "string", "Approval state when the tenant uses approval rules."),
        _c("source", "string", "How the order was created."),
        _c("destination_warehouse", "string", "Warehouse the order arrives at."),
        _c("line_warehouse", "string", "Warehouse this line is for."),
        _c("supplier", "string", "Supplier of the line."),
        _c("sku", "string", "Product code."),
        _c("display_name", "string", "Product name."),
        _c("signal", "string", "Signal the recommendation had when it was made."),
        _c("line_status", "string", "approved, modified or rejected."),
        _c("is_ordered", "boolean", "True when the line is part of the order (approved or "
                                    "modified); rejected lines are kept for the adoption rate."),
        _c("recommended_qty", "number", "Units StockAI recommended."),
        _c("final_qty", "number", "Units the buyer kept."),
        _c("received_qty", "number", "Units received so far (null before any reception)."),
        _c("outstanding_qty", "number", "Ordered units still to arrive (0 for rejected lines)."),
        _c("unit_cost", "number", "Cost per unit (null when unknown)."),
        _c("line_value", "number", "final_qty x unit_cost (null when the cost is unknown)."),
    ),
    sort=("ordered_at", "po_log_id", "supplier", "sku", "line_id"),
    updated_since_column="updated_at",
    scope_note="Orders whose destination warehouse is one of the caller's.",
)

RECEPTIONS = Dataset(
    name="receptions",
    version="1",
    description=(
        "Goods received: one row per purchase order line with units received, with "
        "the order date and the date of the latest reception."
    ),
    columns=(
        _c("po_number", "integer", "The order number shown to people."),
        _c("po_log_id", "string", "Stable id of the order."),
        _c("line_id", "string", "Stable id of the line."),
        _c("received_at", "datetime", "Date of the latest reception recorded on the order."),
        _c("ordered_at", "datetime", "When the order was generated."),
        _c("days_to_last_reception", "number", "Days from the order to the latest reception."),
        _c("reception_status", "string", "The order's reception state: partial or received."),
        _c("po_cancelled", "boolean", "True when the order was cancelled after receiving."),
        _c("supplier", "string", "Supplier of the line."),
        _c("sku", "string", "Product code."),
        _c("display_name", "string", "Product name."),
        _c("warehouse", "string", "Warehouse the goods went to."),
        _c("final_qty", "number", "Units ordered."),
        _c("received_qty", "number", "Units received so far."),
        _c("outstanding_qty", "number", "Ordered units still to arrive."),
        _c("unit_cost", "number", "Cost per unit (null when unknown)."),
        _c("received_value", "number", "received_qty x unit_cost (null when the cost is unknown)."),
    ),
    sort=("received_at", "po_log_id", "sku", "line_id"),
    updated_since_column="received_at",
    scope_note="Orders whose destination warehouse is one of the caller's.",
    notes=("Lines with no units received are not listed; the receptions are per order, "
           "so received_at is the latest event, not one date per partial delivery.",),
)

FORECAST_POINTS = Dataset(
    name="forecast-points",
    version="1",
    description=(
        "The champion forecast of each SKU, one row per period, with its band. The "
        "model is the one the purchase recommendations are computed from."
    ),
    columns=(
        _c("session_id", "string", "The forecast session."),
        _c("period", "string", "Granularity of the session: daily, weekly or monthly."),
        _c("sku", "string", "Product code."),
        _c("warehouse", "string", "Warehouse, when the forecast is per warehouse (else null)."),
        _c("model", "string", "Model the points come from."),
        _c("model_is_champion", "boolean", "False when the champion had no stored forecast and "
                                           "another model is shown instead."),
        _c("step", "integer", "1 for the first forecast period, 2 for the next..."),
        _c("date", "date", "Start of the forecast period."),
        _c("forecast", "number", "Expected demand in the period."),
        _c("lower", "number", "Lower end of the band (null when the model has none)."),
        _c("upper", "number", "Upper end of the band (null when the model has none)."),
    ),
    sort=("sku", "warehouse", "date"),
    parameters=("session_id",),
    scope_note="Only forecasts kept per warehouse, for the caller's warehouses. A session "
               "forecast company-wide is refused (warehouse_scope_company_totals).",
)

ACCURACY = Dataset(
    name="accuracy",
    version="1",
    description=(
        "How good the champion model of each SKU was on its validation window: WAPE "
        "and bias. The same model the purchases and the headline accuracy come from."
    ),
    columns=(
        _c("session_id", "string", "The forecast session."),
        _c("sku", "string", "Product code."),
        _c("warehouse", "string", "Warehouse, when the series is per warehouse (else null)."),
        _c("model", "string", "Champion model."),
        _c("wape", "number", "Weighted absolute percentage error, 0.12 = 12 percent "
                             "(null when the window had no demand to measure against)."),
        _c("bias", "number", "Mean signed error in units per period. Positive = the forecast "
                             "ran above the actual demand, negative = below (null when unknown)."),
        _c("mae", "number", "Mean absolute error in units per period."),
        _c("rmse", "number", "Root mean squared error in units per period."),
    ),
    sort=("sku", "warehouse"),
    parameters=("session_id",),
    scope_note="Only series kept per warehouse, for the caller's warehouses. A session "
               "forecast company-wide is refused (warehouse_scope_company_totals).",
)

COMMITTED_DEMAND = Dataset(
    name="committed-demand",
    version="1",
    description=(
        "Customer orders placed ahead of time (committed demand), with their delivery "
        "date, quantity, probability and status, and whether the supply covers them."
    ),
    columns=(
        _c("id", "string", "Stable id of the commitment."),
        _c("sku", "string", "Product code."),
        _c("customer", "string", "Customer (null when not recorded)."),
        _c("delivery_date", "date", "Date the customer needs the goods."),
        _c("quantity", "number", "Units committed."),
        _c("probability", "number", "1 = firm; below 1 = the likelihood it happens."),
        _c("on_top_of_base", "boolean", "True when the units are on top of the usual forecast."),
        _c("status", "string", "open, fulfilled or cancelled."),
        _c("overdue", "boolean", "True when open and the delivery date has passed."),
        _c("warehouse_id", "string", "Warehouse the commitment is for (null = the whole company)."),
        _c("warehouse", "string", "Name of that warehouse."),
        _c("note", "string", "Free-text note."),
        _c("created_at", "datetime", "When it was recorded."),
        _c("updated_at", "datetime", "When it last changed."),
        _c("at_risk", "boolean", "Company-wide check: the stock and incoming supply will not "
                                 "cover it (null when closed, no stock row, or the caller is "
                                 "limited to some warehouses)."),
        _c("shortfall", "number", "Units short when at_risk (null as above)."),
        _c("latest_safe_order_date", "date", "Last date an order still arrives in time, when at_risk."),
    ),
    sort=("delivery_date", "created_at", "id"),
    updated_since_column="updated_at",
    scope_note="Commitments for the caller's warehouses. Company-wide commitments (no "
               "warehouse) are not shown, and the company-wide risk columns are null.",
)

DATASETS: dict[str, Dataset] = {d.name: d for d in (
    INVENTORY_STATUS, PURCHASE_ORDER_LINES, RECEPTIONS, FORECAST_POINTS, ACCURACY,
    COMMITTED_DEMAND,
)}

# Surfaced as `X-Schema-Version` and in `GET /datasets/schema`. Bumped only when
# the SET of datasets or this contract's rules change; each dataset carries its
# own `version` for its columns.
SCHEMA_VERSION = "1"


def unknown_parameters(dataset: Dataset, names) -> list[str]:
    """Query parameter names the dataset does not accept, sorted."""
    return sorted(set(names) - dataset.accepted_parameters)


def describe(dataset: Dataset) -> dict:
    """The published description of one dataset (JSON-safe)."""
    return {
        "name": dataset.name,
        "version": dataset.version,
        "path": f"/api/v1/datasets/{dataset.name}",
        "description": dataset.description,
        "sort": list(dataset.sort),
        "parameters": sorted(dataset.accepted_parameters),
        "updated_since_column": dataset.updated_since_column,
        "warehouse_scope": dataset.scope_note,
        "notes": list(dataset.notes),
        "columns": [
            {"name": c.name, "type": c.type, "description": c.description}
            for c in dataset.columns
        ],
    }
