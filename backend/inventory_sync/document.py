"""Profiles as code: a versioned JSON (or YAML) document developers keep in git.

    {
      "schema": "stockai.inventory-sync/v1",
      "slug": "erp-stock",                       # the external key: apply is idempotent by it
      "name": "ERP stock",
      "source": {"data_source": {"ref": "ERP Postgres"},   # by NAME or id, never a password
                 "kind": "table", "schema": "public", "object": "existencias", "query": null},
      "model": {"stock_model": "snapshot", "config": {}},
      "mapping": {"sku": {"column": "codigo"}, "current_stock": {"column": "existencia", "multiplier": 12}},
      "filters": [{"column": "activo", "op": "eq", "value": "1"}],
      "freshness": {"mode": "scheduled", "interval_minutes": 60, "poll_seconds": 60,
                    "reconcile_minutes": 360, "strategy": "auto",
                    "incremental": {"enabled": false, "watermark_column": null, "overlap_minutes": 10}},
      "policies": {"missing": "ignore", "negative": "reject", "duplicates": "reject",
                   "allow_new_warehouses": false, "default_warehouse": null, "priority": 100},
      "brake": {"max_vanished_pct": 20, "min_row_ratio": 0.5, "max_rejected_pct": 50, "max_jump_pct": 20}
    }

ONE spec (`SPEC`) drives both the validator — which answers every problem with
its exact path, e.g. ``mapping.current_stock.cast: 'int' is not one of auto,
number, integer, text`` — and the JSON Schema published at a stable route, so
the two can never disagree. Secrets never travel: the data source is a
reference the importing account resolves to its own connection.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.errors import AppError
from backend.inventory_sync import fields as F

SCHEMA_ID = "stockai.inventory-sync/v1"


class S:
    """A tiny schema node: enough to validate the document and to emit JSON Schema."""

    def __init__(self, type_: str, *, enum=None, props=None, items=None, required=(), nullable=False,
                 minimum=None, maximum=None, pattern=None, max_len=None, extra=None, desc=None):
        self.type, self.enum, self.props, self.items = type_, enum, props or {}, items
        self.required, self.nullable = tuple(required), nullable
        self.minimum, self.maximum, self.pattern, self.max_len = minimum, maximum, pattern, max_len
        self.extra, self.desc = extra, desc

    def json_schema(self) -> dict:
        t = {"str": "string", "int": "integer", "num": "number", "bool": "boolean",
             "obj": "object", "list": "array", "any": None}[self.type]
        out: dict = {}
        if t:
            out["type"] = [t, "null"] if self.nullable else t
        if self.desc:
            out["description"] = self.desc
        if self.enum is not None:
            out["enum"] = list(self.enum) + ([None] if self.nullable else [])
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.pattern:
            out["pattern"] = self.pattern
        if self.max_len:
            out["maxLength" if self.type == "str" else "maxItems"] = self.max_len
        if self.type == "obj":
            if self.props:
                out["properties"] = {k: v.json_schema() for k, v in self.props.items()}
                out["additionalProperties"] = self.extra.json_schema() if isinstance(self.extra, S) else False
            elif isinstance(self.extra, S):
                out["additionalProperties"] = self.extra.json_schema()
            if self.required:
                out["required"] = list(self.required)
        if self.type == "list" and self.items:
            out["items"] = self.items.json_schema()
        return out

    def validate(self, value: Any, path: str, errors: list[dict]) -> None:
        def err(code: str, message: str, **params):
            errors.append({"path": path or "$", "code": code, "message": f"{path or '$'}: {message}",
                           "params": params})
        if value is None:
            if not self.nullable and self.type != "any":
                err("required", "must not be null")
            return
        kinds = {"str": str, "int": int, "num": (int, float), "bool": bool, "obj": dict, "list": list}
        if self.type != "any":
            want = kinds[self.type]
            ok = isinstance(value, want) and not (self.type in ("int", "num") and isinstance(value, bool))
            if self.type == "int" and isinstance(value, float) and value.is_integer():
                ok = True
            if not ok:
                err("type", f"must be {self.type}, got {type(value).__name__}", expected=self.type)
                return
        if self.enum is not None and value not in self.enum:
            err("enum", f"{value!r} is not one of {', '.join(map(str, self.enum))}",
                value=str(value)[:60], allowed=", ".join(map(str, self.enum)))
        if self.minimum is not None and isinstance(value, (int, float)) and value < self.minimum:
            err("minimum", f"must be >= {self.minimum}", min=self.minimum)
        if self.maximum is not None and isinstance(value, (int, float)) and value > self.maximum:
            err("maximum", f"must be <= {self.maximum}", max=self.maximum)
        if self.pattern and isinstance(value, str) and not re.fullmatch(self.pattern, value):
            err("pattern", f"{value[:60]!r} does not match {self.pattern}", pattern=self.pattern)
        if self.max_len and hasattr(value, "__len__") and len(value) > self.max_len:
            err("max_length", f"is longer than {self.max_len}", max=self.max_len)
        if self.type == "obj":
            for r in self.required:
                if r not in value:
                    errors.append({"path": f"{path}.{r}" if path else r, "code": "required",
                                   "message": f"{(path + '.') if path else ''}{r}: is required", "params": {}})
            for k, v in value.items():
                sub = f"{path}.{k}" if path else str(k)
                if k in self.props:
                    self.props[k].validate(v, sub, errors)
                elif isinstance(self.extra, S):
                    self.extra.validate(v, sub, errors)
                else:
                    errors.append({"path": sub, "code": "unknown_key",
                                   "message": f"{sub}: unknown key", "params": {"key": str(k)[:60]}})
        if self.type == "list" and self.items:
            for i, v in enumerate(value):
                self.items.validate(v, f"{path}[{i}]", errors)


_COL = S("str", max_len=256, nullable=True)
_FIELD = S("obj", props={
    "column": _COL, "cast": S("str", enum=F.CASTS), "trim": S("bool"),
    "case": S("str", enum=F.CASES), "decimal": S("str", enum=F.DECIMALS),
    "multiplier": S("num", minimum=0, maximum=1_000_000, nullable=True),
    "default": S("str", max_len=F.MAX_TEXT, nullable=True),
    "value_map": S("obj", extra=S("str", max_len=F.MAX_TEXT), nullable=True),
}, desc="One canonical field: where it comes from and how it is transformed.")

SPEC = S("obj", required=("schema", "name", "source", "mapping"), props={
    "schema": S("str", enum=(SCHEMA_ID,)),
    "slug": S("str", pattern=r"[a-z0-9][a-z0-9_-]{0,62}", nullable=True,
              desc="External key: applying the same slug updates instead of creating."),
    "name": S("str", max_len=120),
    "source": S("obj", required=("kind",), props={
        "data_source": S("obj", nullable=True, required=("ref",), props={
            "ref": S("str", max_len=200, desc="The data source's NAME or id in the importing account.")}),
        "kind": S("str", enum=("table", "query", "collection", "pipeline", "push")),
        "schema": S("str", max_len=256, nullable=True),
        "object": S("str", max_len=256, nullable=True),
        "query": S("str", max_len=100_000, nullable=True),
    }),
    "model": S("obj", props={
        "stock_model": S("str", enum=("snapshot", "aggregate", "ledger")),
        "config": S("obj", extra=S("any")),
    }),
    "mapping": S("obj", props={f: _FIELD for f in F.CANONICAL_FIELDS}, required=F.REQUIRED_FIELDS),
    "filters": S("list", max_len=F.MAX_FILTERS, items=S("obj", required=("column", "op"), props={
        "column": S("str", max_len=256), "op": S("str", enum=F.FILTER_OPS), "value": S("any")})),
    "freshness": S("obj", props={
        "mode": S("str", enum=("scheduled", "continuous", "on_demand")),
        "interval_minutes": S("int", minimum=1, maximum=7 * 24 * 60),
        "poll_seconds": S("int", minimum=5, maximum=24 * 3600),
        "reconcile_minutes": S("int", minimum=5, maximum=7 * 24 * 60),
        "strategy": S("str", enum=("auto", "full", "watermark", "ledger_cursor", "change_tracking",
                                   "oracle_rowscn", "change_stream")),
        "incremental": S("obj", props={
            "enabled": S("bool"), "watermark_column": _COL,
            "overlap_minutes": S("int", minimum=0, maximum=24 * 60)}),
    }),
    "policies": S("obj", props={
        "missing": S("str", enum=("ignore", "mark_inactive", "zero")),
        "negative": S("str", enum=("reject", "zero")),
        "duplicates": S("str", enum=("reject", "sum")),
        "allow_new_warehouses": S("bool"),
        "default_warehouse": S("str", max_len=F.MAX_TEXT, nullable=True),
        "priority": S("int", minimum=1, maximum=10_000),
    }),
    "brake": S("obj", props={
        "max_vanished_pct": S("num", minimum=0, maximum=100),
        "min_row_ratio": S("num", minimum=0, maximum=1),
        "max_rejected_pct": S("num", minimum=0, maximum=100),
        "max_jump_pct": S("num", minimum=0, maximum=100),
    }),
})


def json_schema() -> dict:
    out = SPEC.json_schema()
    out.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://stockai.es/schemas/inventory-sync-profile-v1.json",
                "title": "StockAI inventory sync profile (v1)"})
    return out


def validate(doc: Any) -> list[dict]:
    errors: list[dict] = []
    SPEC.validate(doc, "", errors)
    return errors


def _invalid(errors: list[dict]) -> AppError:
    first = errors[0]
    return AppError("inventory_sync_document_invalid", first["message"], status_code=422,
                    params={"path": first["path"], "count": len(errors), "errors": errors[:50]})


def _resolve_source(tenant_id: str, ref: Optional[dict]) -> Optional[str]:
    from backend.db.connection import query
    if not ref:
        return None
    key = str(ref.get("ref") or "")
    rows = query("SELECT id FROM datasets WHERE tenant_id = %s AND source_type = 'sql' AND (id = %s OR name = %s)",
                 (tenant_id, key, key))
    if len(rows) != 1:
        raise AppError("inventory_sync_document_invalid",
                       f"source.data_source.ref: no single data source named {key!r} in this account.",
                       status_code=422, params={"path": "source.data_source.ref", "count": 1, "errors": [{
                           "path": "source.data_source.ref", "code": "not_found" if not rows else "ambiguous",
                           "message": f"source.data_source.ref: {key!r} " + ("not found" if not rows else "is ambiguous"),
                           "params": {"ref": key[:200]}}]})
    return rows[0]["id"]


def to_body(tenant_id: str, doc: dict) -> dict:
    """A validated document as a profiles.normalize body."""
    errors = validate(doc)
    if errors:
        raise _invalid(errors)
    src, model = doc["source"], doc.get("model") or {}
    fresh, pol, brake = doc.get("freshness") or {}, doc.get("policies") or {}, doc.get("brake") or {}
    inc = fresh.get("incremental") or {}
    body: dict = {
        "name": doc["name"], "slug": doc.get("slug"),
        "data_source_id": _resolve_source(tenant_id, src.get("data_source")),
        "source_kind": src["kind"], "source_schema": src.get("schema"), "source_object": src.get("object"),
        "source_query": src.get("query"),
        "stock_model": model.get("stock_model", "snapshot"), "model_config": model.get("config") or {},
        "mapping": doc["mapping"], "filters": doc.get("filters") or [],
        "mode": "incremental" if inc.get("enabled") else "full",
        "watermark_column": inc.get("watermark_column"), "overlap_minutes": inc.get("overlap_minutes", 10),
        "freshness": fresh.get("mode", "scheduled"), "interval_minutes": fresh.get("interval_minutes", 60),
        "poll_seconds": fresh.get("poll_seconds", 60), "reconcile_minutes": fresh.get("reconcile_minutes", 360),
        "strategy": fresh.get("strategy", "auto"),
        "missing_policy": pol.get("missing", "ignore"), "negative_policy": pol.get("negative", "reject"),
        "duplicate_policy": pol.get("duplicates", "reject"),
        "allow_new_warehouses": pol.get("allow_new_warehouses", False),
        "default_warehouse": pol.get("default_warehouse"), "priority": pol.get("priority", 100),
        "brake_max_vanished_pct": brake.get("max_vanished_pct", 20),
        "brake_min_row_ratio": brake.get("min_row_ratio", 0.5),
        "brake_max_rejected_pct": brake.get("max_rejected_pct", 50),
        "brake_max_jump_pct": brake.get("max_jump_pct", 20),
    }
    return body


_CODE_PATH = {
    "inventory_sync_mapping_invalid": "mapping", "inventory_sync_watermark_required": "freshness.incremental.watermark_column",
    "inventory_sync_policy_invalid": "policies", "inventory_sync_model_invalid": "model.config",
    "inventory_sync_strategy_invalid": "freshness.strategy", "inventory_sync_continuous_needs_incremental": "freshness.mode",
    "inventory_sync_object_required": "source.object", "inventory_sync_query_required": "source.query",
    "inventory_sync_source_required": "source.data_source", "inventory_sync_source_kind_invalid": "source.kind",
    "inventory_sync_value_out_of_range": "", "inventory_sync_slug_invalid": "slug",
    "sql_not_a_select": "source.query", "mongo_stage_not_allowed": "source.query",
    "mongo_query_invalid": "source.query", "mongo_query_forbidden_operator": "source.query",
}


def semantic_error(exc: AppError) -> AppError:
    """A normalize() refusal re-said with the document path it is about."""
    path = _CODE_PATH.get(exc.code)
    if path is None:
        if exc.code.startswith("sql_"):
            path = "source.query"
        else:
            return exc
    params = dict(exc.params or {})
    if exc.code == "inventory_sync_mapping_invalid" and params.get("field"):
        path = f"mapping.{params['field']}"
    if exc.code == "inventory_sync_value_out_of_range" and params.get("field"):
        path = params["field"]
    return AppError("inventory_sync_document_invalid", f"{path}: {exc.message}", status_code=422,
                    params={"path": path, "count": 1, "errors": [
                        {"path": path, "code": exc.code, "message": f"{path}: {exc.message}", "params": params}]})


def from_profile(row: dict) -> dict:
    """A stored profile as a document (the data source by name; no secrets)."""
    from backend.db.connection import query_one
    ds = query_one("SELECT name FROM datasets WHERE id = %s", (row["data_source_id"],)) \
        if row.get("data_source_id") else None
    mapping = {}
    for f, m in (row.get("mapping") or {}).items():
        mapping[f] = {k: v for k, v in m.items() if v not in (None, {}, [])}
    return {
        "schema": SCHEMA_ID, "slug": row.get("slug"), "name": row["name"],
        "source": {"data_source": {"ref": ds["name"]} if ds else None, "kind": row["source_kind"],
                   "schema": row.get("source_schema"), "object": row.get("source_object"),
                   "query": row.get("source_query")},
        "model": {"stock_model": row.get("stock_model") or "snapshot", "config": row.get("model_config") or {}},
        "mapping": mapping, "filters": row.get("filters") or [],
        "freshness": {"mode": row.get("freshness") or "scheduled", "interval_minutes": row["interval_minutes"],
                      "poll_seconds": row.get("poll_seconds") or 60,
                      "reconcile_minutes": row.get("reconcile_minutes") or 360,
                      "strategy": row.get("strategy") or "auto",
                      "incremental": {"enabled": row.get("mode") == "incremental",
                                      "watermark_column": row.get("watermark_column"),
                                      "overlap_minutes": row.get("overlap_minutes") or 0}},
        "policies": {"missing": row["missing_policy"], "negative": row["negative_policy"],
                     "duplicates": row["duplicate_policy"], "allow_new_warehouses": bool(row["allow_new_warehouses"]),
                     "default_warehouse": row.get("default_warehouse"), "priority": int(row.get("priority") or 100)},
        "brake": {"max_vanished_pct": float(row["brake_max_vanished_pct"]),
                  "min_row_ratio": float(row["brake_min_row_ratio"]),
                  "max_rejected_pct": float(row["brake_max_rejected_pct"]),
                  "max_jump_pct": float(row.get("brake_max_jump_pct") or 20)},
    }


def apply(tenant_id: str, user_id: str, doc: dict, *, dry_run: bool = False) -> dict:
    """Create or update by slug (idempotent: the same document twice changes
    nothing the second time). `dry_run` validates and previews without saving."""
    from backend.db.connection import query_one
    from backend.inventory_sync import engine, profiles
    body = to_body(tenant_id, doc)
    slug = body.get("slug")
    existing = query_one("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s AND slug = %s",
                         (tenant_id, slug)) if slug else None
    try:
        cfg = profiles.normalize(tenant_id, body, existing)
    except AppError as exc:
        raise semantic_error(exc) from None
    if dry_run:
        preview = engine.dry_run(tenant_id, {**cfg, "id": (existing or {}).get("id") or "dry-run",
                                             "tenant_id": tenant_id}) if cfg.get("data_source_id") else None
        return {"action": "update" if existing else "create", "valid": True, "dry_run": True, "preview": preview}
    if existing:
        before = existing.get("config_hash")
        row, disabled = profiles.update(tenant_id, existing["id"], body)
        action = "unchanged" if row.get("config_hash") == before and row["name"] == existing["name"] else "updated"
        return {"action": action, "profile_id": row["id"], "disabled_by_edit": disabled}
    try:
        row = profiles.create(tenant_id, user_id, body)
    except AppError as exc:
        raise semantic_error(exc) from None
    return {"action": "created", "profile_id": row["id"], "disabled_by_edit": False}
