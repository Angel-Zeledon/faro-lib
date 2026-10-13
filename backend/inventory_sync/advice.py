"""Schema gotchas a dry run warns about before a profile is turned on.

Each rule comes from how large ERPs actually store stock (see the sources in
`presets.py`'s header):

* **client / company columns** — SAP tables are client-dependent (MANDT),
  Dynamics per legal entity (DATAAREAID), Oracle EBS per inventory
  organisation (ORGANIZATION_ID), Infor M3 per company (MBCONO), Odoo per
  company_id. Reading without that filter mixes companies: the same SKU and
  plant from a test client and production collide (duplicate keys) or, worse,
  add up.
* **history rows** — SCD2 / snapshot-history tables keep every version of a
  row (valid_to, is_current, dbt_valid_to); only the current one is stock.
* **soft deletes** — deleted_at / is_deleted / SAP LVORM rows are not stock.
* **S/4HANA calculated stock** — MARD / MCHB stock fields are computed from
  MATDOC there; a replica of the raw table carries zeros.
* **JD Edwards implied decimals** — quantities are integers scaled by the
  data dictionary's decimals.
* **Business Central Remaining Quantity** — open-entry bookkeeping, not on hand.

Advice never blocks anything: it is a list of {code, params} the screen
renders next to the preview (`isync.advice.<code>`).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_CLIENT_RULES: tuple[tuple[str, re.Pattern, tuple[str, ...]], ...] = (
    ("SAP", re.compile(r"\b(MARD|MCHB|MSEG|MKPF|MATDOC|MARC|MSKA|MSLB|MKOL|MBEW|NSDM_V_MARD)\b", re.I), ("MANDT",)),
    ("Dynamics", re.compile(r"\b(INVENTSUM|INVENTTRANS|INVENTDIM)\b", re.I), ("DATAAREAID",)),
    ("Oracle EBS", re.compile(r"\bMTL_(ONHAND_QUANTITIES(_DETAIL)?|MATERIAL_TRANSACTIONS)\b", re.I),
     ("ORGANIZATION_ID",)),
    ("Infor M3", re.compile(r"\b(MITBAL|MITLOC)\b", re.I), ("MBCONO", "MLCONO")),
    ("Odoo", re.compile(r"\bstock_quant\b", re.I), ("company_id",)),
)
_HISTORY = ("valid_to", "validto", "valid_until", "effective_to", "effective_end", "is_current",
            "current_flag", "is_latest", "dbt_valid_to", "row_end", "sysendtime", "sys_end_time")
_SOFT_DELETE = ("deleted_at", "is_deleted", "deleted", "deleted_flag", "deletion_flag", "lvorm", "loekz",
                "is_archived", "archived_at", "removed_at")
_S4_CALCULATED = re.compile(r"\b(MARD|MCHB|MARC)\b", re.I)
_JDE = re.compile(r"\b(F41021|F4111)\b", re.I)
_BC_REMAINING = re.compile(r"remaining\s+quantity", re.I)


def _text(profile: dict) -> str:
    return " ".join(str(profile.get(k) or "") for k in ("source_query", "source_object"))


def _mentioned(name: str, text: str, filters: Iterable[dict]) -> bool:
    if re.search(rf"\b{re.escape(name)}\b", text, re.I):
        return True
    return any(str(f.get("column") or "").lower() == name.lower() for f in filters)


def lint(profile: dict, seen_columns: Iterable[str]) -> list[dict[str, Any]]:
    text = _text(profile)
    filters = [f for f in (profile.get("filters") or []) if isinstance(f, dict)]
    seen = {str(c).lower(): str(c) for c in seen_columns}
    out: list[dict[str, Any]] = []
    for vendor, pattern, columns in _CLIENT_RULES:
        m = pattern.search(text)
        if m and not any(_mentioned(c, text, filters) for c in columns):
            out.append({"code": "erp_client_filter_missing",
                        "params": {"vendor": vendor, "table": m.group(0), "column": columns[0]}})
    for marker in _HISTORY:
        if marker in seen and not _mentioned(seen[marker], text, filters):
            out.append({"code": "history_rows_unfiltered", "params": {"column": seen[marker]}})
            break
    for marker in _SOFT_DELETE:
        if marker in seen and not _mentioned(seen[marker], text, filters):
            out.append({"code": "soft_deleted_rows_unfiltered", "params": {"column": seen[marker]}})
            break
    if _S4_CALCULATED.search(text):
        out.append({"code": "sap_s4_calculated_stock", "params": {}})
    mapping = profile.get("mapping") or {}
    qty = (mapping.get("current_stock") or {}) if isinstance(mapping.get("current_stock"), dict) else {}
    if _JDE.search(text) and not qty.get("multiplier"):
        out.append({"code": "jde_implied_decimals", "params": {}})
    if _BC_REMAINING.search(text) or str(qty.get("column") or "").lower() in ("remaining quantity",
                                                                             "remaining_quantity"):
        out.append({"code": "bc_remaining_quantity", "params": {}})
    return out
