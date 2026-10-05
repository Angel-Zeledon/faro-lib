"""The tools an AI client may call, and nothing else.

## Why every tool here only reads

The owner's rule for anything an LLM drives (`docs/assistant-actions.md`): it
never executes without a confirmation, and every action it takes is reversible.
An MCP client is the one caller that can satisfy neither — StockAI cannot render a
before/after card inside somebody else's chat window, and the confirmation would
arrive as text from the same model that proposed the change.

So this catalogue is an allowlist of reads. There is deliberately no tool that
uploads a file, starts a training run or logs a purchase order, even though the
public REST API offers all three to the same key: `log-po` in particular has no
inverse anywhere in the product (there is no "void an order"), and handing an
irreversible write to a model is the one thing the rule forbids outright.

A key with `analyst` role changes nothing here. The catalogue is the ceiling,
not the role.

## Why each handler calls the HTTP endpoint's own function

Each tool below calls the exact function FastAPI routes the public endpoint to,
with every parameter passed explicitly. Not a copy of its body, and not the
service layer underneath it: the endpoint function is where "no session_id means
the tenant's active session" and the signal/supplier filtering live, and a
second implementation of those would drift on the first change and give an
integration one answer over REST and another over MCP.

Explicit arguments matter — the parameters carry `Query(...)` objects as their
defaults, so calling one of these with arguments omitted would pass a
`Query` instance where a value belongs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.auth.guards import CurrentUser

# The semáforo, ordered the way a buyer reads it. Used when a catalogue is
# larger than one answer can carry: truncating an arbitrary slice would hide
# exactly the rows the question was about.
_URGENCY = {
    "PEDIR_YA": 0,
    "PEDIR_PRONTO": 1,
    "SIN_DATOS": 2,      # not knowing is not the same as being fine
    "SOBRESTOCK": 3,
    "OK": 4,
}

# How many SKUs one `get_inventory_status` answer carries by default.
#
# The REST endpoint returns every product with no pagination, which is right for
# a script and wrong here: a 20,000-SKU catalogue does not fit in a model's
# context, and the failure would be a truncated answer nobody was told about.
DEFAULT_ITEM_LIMIT = 50
MAX_ITEM_LIMIT = 500

# How many of the excluded SKUs come back as a sample. The COUNT always
# comes back; the list is diagnostic and a long one earns nothing.
_EXCLUDED_SKU_SAMPLE = 20


@dataclass(frozen=True)
class Tool:
    """One serialisable tool descriptor plus the function behind it.

    The fields above `handler` are exactly what `tools/list` publishes, which is
    why they are plain data: the same descriptor serialises to MCP today and to
    a provider's `tools` array if the in-app assistant ever consumes this
    catalogue (`docs/assistant-actions.md` §1).
    """
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[CurrentUser, dict[str, Any]], dict[str, Any]]

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            # Hints are advisory — a client may ignore them — but a read-only
            # server that does not say so invites a client to ask for a
            # confirmation StockAI cannot render.
            "annotations": {
                "title": self.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


# ── The handlers ─────────────────────────────────────────────────────────────

def _planning_context(user: CurrentUser, args: dict) -> dict:
    from backend.api.v1 import planning as planning_router
    return planning_router.get_planning(user=user)["data"]


def _list_data_sources(user: CurrentUser, args: dict) -> dict:
    from backend.api.v1 import datasources as datasources_router
    limit = _clamp_int(args.get("limit"), default=50, low=1, high=200)
    return datasources_router.list_sources(skip=0, limit=limit, user=user)["data"]


def _inventory_status(user: CurrentUser, args: dict) -> dict:
    from backend.api.v1 import inventory as inventory_router

    # A signal we do not have would otherwise filter everything away and come
    # back as a clean, confident "nothing to order" — the exact shape of an
    # answer that costs money. The REST endpoint can afford to pass an unknown
    # value through, because a script author reads the empty array and checks
    # their spelling; a model reads it and tells somebody they are fine.
    signal = (args.get("signal") or "").strip().upper() or None
    if signal and signal not in _URGENCY:
        raise ValueError(
            f"Unknown signal {signal!r}. Valid signals: "
            + ", ".join(_URGENCY) + "."
        )

    supplier = (args.get("supplier") or "").strip() or None

    data = inventory_router.inventory_status(
        session_id=args.get("session_id") or None,
        service_level=0.95,
        signal=signal,
        supplier=supplier,
        by_warehouse=False,
        limit=None, offset=0, sort="urgency", order=None, q=None, skus=None,  # the endpoint's own paging is off here
        user=user,
    )["data"]

    items = data.get("items") or []
    total = len(items)
    limit = _clamp_int(args.get("limit"), default=DEFAULT_ITEM_LIMIT,
                       low=1, high=MAX_ITEM_LIMIT)

    ordered = sorted(
        items,
        key=lambda i: (
            _URGENCY.get(i.get("signal"), 9),
            # Least cover first inside a signal. A missing figure sorts last:
            # it is the row with nothing to rank, not the most urgent one.
            float("inf") if i.get("coverage_days") is None else i["coverage_days"],
            i.get("sku") or "",
        ),
    )
    data["items"] = ordered[:limit]

    # `summary` stays computed over the WHOLE filtered set, not the page. A
    # summary that counted only what fitted would tell a model there are 4 SKUs
    # to order when there are 300 — the most expensive possible lie here.
    data["returned_items"] = len(data["items"])
    data["total_matching_items"] = total
    data["truncated"] = total > len(data["items"])
    data["ordering"] = "urgency: PEDIR_YA, PEDIR_PRONTO, SIN_DATOS, SOBRESTOCK, OK; least coverage first"
    if data["truncated"]:
        data["truncation_note"] = (
            f"Showing the {len(data['items'])} most urgent of {total} matching "
            f"SKUs. `summary` counts all {total}. Narrow with `signal` or "
            f"`supplier`, or raise `limit` (max {MAX_ITEM_LIMIT})."
        )

    # "The filter matched nothing" and "there is nothing to buy" are different
    # facts, and an empty list looks like the second. A supplier name spelled
    # the way the ERP spells it rather than the way StockAI stores it lands here,
    # and without this line the assistant reports all clear.
    if total == 0 and (signal or supplier):
        applied = ", ".join(
            part for part in (
                f"signal={signal}" if signal else None,
                f"supplier={supplier!r}" if supplier else None,
            ) if part
        )
        data["empty_reason"] = (
            f"No SKU matched the filter ({applied}). This is the filter "
            f"matching nothing, NOT a catalogue with nothing to order — call "
            f"again without the filter before reporting that stock is fine."
        )

    # The excluded list is diagnostic and can be long; its size is the part that
    # matters to a reader, and dropping it silently would be the same defect
    # this tool avoids for `items`. The total is written even when nothing was
    # cut, so a list of exactly 20 is never ambiguous.
    excluded = data.get("excluded_skus") or []
    data["excluded_skus_total"] = len(excluded)
    data["excluded_skus"] = excluded[:_EXCLUDED_SKU_SAMPLE]
    return data


def _morning_briefing(user: CurrentUser, args: dict) -> dict:
    from backend.api.v1 import inventory as inventory_router
    return inventory_router.morning_briefing(
        session_id=args.get("session_id") or None,
        service_level=0.95,
        user=user,
    )["data"]


def _training_status(user: CurrentUser, args: dict) -> dict:
    from backend.api.v1 import training as training_router
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    return training_router.get_train_status(session_id=session_id, user=user)["data"]


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    """A number a model wrote, made safe without refusing the call.

    Models send `"50"`, `50.0` and occasionally nonsense. Refusing costs a whole
    round trip for something with an obvious reading, so anything unreadable
    falls back to the default and anything out of range is clamped.
    """
    if value is None:
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# ── The catalogue ────────────────────────────────────────────────────────────

_SESSION_ARG = {
    "type": "string",
    "description": (
        "Forecast session to read. Omit it to use the tenant's active session — "
        "the same one the StockAI app is showing on screen."
    ),
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="get_planning_context",
        title="Planning context",
        description=(
            "Start here. Returns which forecast session is active, at what "
            "granularity (daily/weekly/monthly) and over how many periods the "
            "numbers in the other tools were computed. `active_session_id` is "
            "what the other tools accept as `session_id`; read it each time "
            "rather than storing it, because it changes when a new run trains."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_planning_context,
    ),
    Tool(
        name="get_inventory_status",
        title="Stock signal per SKU",
        description=(
            "The stock traffic light for every product: what state it is in and "
            "how much to order.\n\n"
            "Signals: PEDIR_YA (order now), PEDIR_PRONTO (order soon), OK, "
            "SOBRESTOCK (too much), SIN_DATOS (no stock on record — which means "
            "unknown, NOT safe).\n\n"
            "Per row, `recommended_qty` is how much to order, `coverage_days` "
            "how long the stock lasts at the forecast rate, and "
            "`lead_time_source` / `unit_cost_source` / `moq_source` say where "
            "each assumption came from: `user` (typed into StockAI), `file` (their "
            "own export), `supplier_rule`, `learned` (from their receptions) or "
            "`default` (StockAI had nothing and assumed). Treat a `default` as an "
            "assumption when you report it, not as the customer's own figure.\n\n"
            "Answers are capped and ordered by urgency; `summary` always counts "
            "the whole matching set, and `truncated` says when rows were left "
            "out. Filter with `signal` or `supplier` rather than raising "
            "`limit` when you can.\n\n"
            "If a filter matches nothing the answer carries `empty_reason`. "
            "That is the filter finding nothing, NOT a catalogue with nothing "
            "to order — never report stock as fine on the strength of it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": _SESSION_ARG,
                "signal": {
                    "type": "string",
                    "enum": ["PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK", "SIN_DATOS"],
                    "description": "Return only SKUs carrying this signal.",
                },
                "supplier": {
                    "type": "string",
                    "description": "Return only SKUs whose supplier matches this name.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_ITEM_LIMIT,
                    "default": DEFAULT_ITEM_LIMIT,
                    "description": "How many SKUs to return, most urgent first.",
                },
            },
            "additionalProperties": False,
        },
        handler=_inventory_status,
    ),
    Tool(
        name="get_morning_briefing",
        title="What to buy today",
        description=(
            "The same data as get_inventory_status, ordered as a day's work: "
            "the risks worth acting on, the reason behind each recommendation, "
            "the suggested supplier and the estimated value. This is the tool "
            "for \"what should I buy today?\"; get_inventory_status is for "
            "\"what is the state of X?\"."
        ),
        input_schema={
            "type": "object",
            "properties": {"session_id": _SESSION_ARG},
            "additionalProperties": False,
        },
        handler=_morning_briefing,
    ),
    Tool(
        name="list_data_sources",
        title="Sales data sources",
        description=(
            "The sales-history sources this tenant's forecasts are trained on, "
            "with the id, the file name and when each was last updated. Use it "
            "to answer whether the numbers are based on fresh data."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 200, "default": 50,
                    "description": "How many sources to return.",
                },
            },
            "additionalProperties": False,
        },
        handler=_list_data_sources,
    ),
    Tool(
        name="get_training_status",
        title="Forecast run status",
        description=(
            "Whether a forecast session has finished training. `status` goes "
            "QUEUED -> RUNNING -> COMPLETED or FAILED. Useful to say whether "
            "the numbers being read are the latest run or the previous one."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session to report on. Required.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        handler=_training_status,
    ),
)

BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def descriptors() -> list[dict[str, Any]]:
    """What `tools/list` publishes."""
    return [t.descriptor() for t in TOOLS]
