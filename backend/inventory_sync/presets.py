"""ERP recipe presets: a starting profile for the schemas large companies run.

A preset is a read-only query template + the mapping + the stock model and its
sign rules + the filters the schema REQUIRES (client / company / organisation /
plant). Rendering one fills those parameters (validated, never free SQL) and
returns a profile body the wizard or `POST /inventory-sync/profiles` accepts;
it still goes through the dry run, the brake and the read-only SQL guard like
any other profile.

Honesty rule: every ERP preset below was written from the vendor's published
table / field documentation and community references, reproduced against a
test schema, and is flagged ``verified: False`` — "unverified against a live
<vendor> system". The UI says so next to it.

Sources consulted (2026-10-12), cited so the next person can re-check them:

* SAP: MARD stock per storage location (LABST unrestricted, INSME quality,
  SPEME blocked, UMLME in transfer — different categories, never summed by
  default); MCHB per batch (CLABS); MSEG.SHKZG 'S' = debit = receipt (+),
  'H' = credit = issue (-) — https://leanx.eu/sap/table/mseg/ ,
  https://community.sap.com/t5/application-development-and-automation-discussions/query/m-p/1531783 .
  On S/4HANA the MARD/MCHB stock fields are no longer stored: they are
  computed from MATDOC / MATDOC_EXTRACT (NSDM_V_MARD) —
  https://blogs.sap.com/2020/05/12/mardh-in-sap-s-4hana-is-different-from-mardh-in-sap-erp/ ,
  https://answers.sap.com/questions/174768/material-master-tables-in-s4-hana-enterprise-manag.html .
  S/4HANA runs on SAP HANA, which StockAI does not connect to: replicate
  (SLT / ODP) into a warehouse StockAI reads, or push.
  Every SAP table is client-dependent: MANDT MUST be filtered.
* Oracle E-Business Suite: MTL_ONHAND_QUANTITIES_DETAIL (one row per
  org / subinventory / locator / lot; PRIMARY_TRANSACTION_QUANTITY) —
  https://forums.sqlteam.com/t/need-query-to-find-onhand-quantity-based-on-lot-and-serial/9941 ,
  https://doyensys.com/blogs/onhand-inventory-by-revision-report/ ;
  MTL_MATERIAL_TRANSACTIONS (signed PRIMARY_QUANTITY, TRANSACTION_ID from a
  sequence) — https://community.oracle.com/mosc/discussion/2921568/on-hand-query .
  ORGANIZATION_ID scopes every row to an inventory organisation.
* Dynamics 365 F&O / AX: INVENTSUM joined to INVENTDIM per DATAAREAID (legal
  entity) and PARTITION; AvailPhysical is deprecated for direct reads, so the
  preset reads PHYSICALINVENT —
  https://cloudblogs.microsoft.com/dynamics365/?p=43643 ,
  https://msdn.microsoft.com/en-us/library/jj761660(v=ax.60) . In the cloud
  the database is reached through Export to Data Lake / Synapse Link / BYOD
  (an Azure SQL database: the mssql engine).
* Business Central / NAV: company tables are named
  ``<Company>$<Table>$<app id>`` (NAV <= 2018 without the app id);
  Item Ledger Entry carries a signed Quantity per [Entry No_] —
  https://bctables.azurecurve.co.uk/tables/Company/item-ledger-entry ,
  https://learn.microsoft.com/en-us/previous-versions/dynamics-nav-2013-r2/hh172203(v=nav.71) .
* JD Edwards: F41021 item location balances (on hand PQOH per item / branch /
  location / lot), F4111 item ledger; quantities carry the data dictionary's
  display decimals as implied decimals —
  https://docs.oracle.com/cd/E16582_01/doc.91/e15119/tables_used_by_inv_mngmnt.htm ,
  https://www.syntax.com/blog/correcting-item-balance-discrepancies-jde/ .
* Infor M3: MITBAL item / warehouse balance per company (MBCONO) —
  https://docs.infor.com/m3analytics/2026.x/en-us/maug_foodbev/hpq1613538077212.html .
* Odoo: stock_quant (quantity, reserved_quantity) on internal locations, per
  company — https://deploymonkey.com/blog/odoo-reservation-not-clearing-fix .
* Magento / Adobe Commerce MSI: inventory_source_item (sku, source_code,
  quantity, status); salable quantity subtracts inventory_reservation —
  https://github.com/magento/inventory/wiki/Inventory-configuration-Design-and-DB-structure .
* NetSuite: SuiteAnalytics Connect is a proprietary ODBC/JDBC service, not one
  of StockAI's engines -> push (a SuiteQL script); Shopify, Manhattan and Blue
  Yonder are SaaS APIs -> push.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from backend.errors import AppError

# ── Parameters ───────────────────────────────────────────────────────────────

# Parameters land inside SQL text (as literals or identifiers), so each kind
# has a strict pattern; nothing else is accepted. No quotes, no brackets, no
# semicolons, no comments can pass any of these.
PARAM_PATTERNS: dict[str, str] = {
    "sap_client": r"\d{3}",
    "code": r"[A-Za-z0-9_\-]{1,20}",
    "code_list": r"[A-Za-z0-9_\-]{1,20}(,[A-Za-z0-9_\-]{1,20}){0,49}",
    "int_list": r"\d{1,12}(,\d{1,12}){0,49}",
    "int": r"\d{1,12}",
    "decimals": r"[0-6]",
    "schema": r"[A-Za-z_][A-Za-z0-9_$#]{0,63}",
    "bc_company": r"[A-Za-z0-9 _\-]{1,60}",
    "guid": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    "prefix": r"[A-Za-z0-9_]{0,20}",
}


def _p(name: str, kind: str, required: bool = True, default: Optional[str] = None) -> dict:
    return {"name": name, "kind": kind, "required": required, "default": default,
            "pattern": PARAM_PATTERNS[kind]}


def _lit_list(values: str) -> str:
    return ", ".join(f"'{v.strip()}'" for v in values.split(",") if v.strip())


def _concat(engine: str, *parts: str) -> str:
    """Engine-specific string concatenation of SQL expressions."""
    if engine in ("oracle", "postgresql"):
        return " || ".join(parts)
    if engine == "mssql":
        return " + ".join(f"CAST({p} AS NVARCHAR(100))" if not p.startswith("'") else p for p in parts)
    return f"CONCAT({', '.join(parts)})"


def _trim(engine: str, expr: str) -> str:
    return f"LTRIM(RTRIM({expr}))" if engine == "mssql" else f"TRIM({expr})"


def _sch(params: dict, key: str = "schema") -> str:
    s = params.get(key)
    return f"{s}." if s else ""


# ── The presets ──────────────────────────────────────────────────────────────

def _sap_mard(engine: str, p: dict) -> str:
    plants = f"\n  AND WERKS IN ({_lit_list(p['plants'])})" if p.get("plants") else ""
    return (f"SELECT MATNR AS sku, {_concat(engine, 'WERKS', chr(39) + '-' + chr(39), 'LGORT')} AS warehouse,\n"
            f"       LABST AS current_stock\n"
            f"FROM {_sch(p)}MARD\n"
            f"WHERE MANDT = '{p['client']}'{plants}")


def _sap_mchb(engine: str, p: dict) -> str:
    plants = f"\n  AND WERKS IN ({_lit_list(p['plants'])})" if p.get("plants") else ""
    return (f"SELECT MATNR AS sku, {_concat(engine, 'WERKS', chr(39) + '-' + chr(39), 'LGORT')} AS warehouse,\n"
            f"       CHARG AS batch, CLABS AS current_stock\n"
            f"FROM {_sch(p)}MCHB\n"
            f"WHERE MANDT = '{p['client']}'{plants}")


def _sap_mseg(engine: str, p: dict) -> str:
    plants = f"\n  AND s.WERKS IN ({_lit_list(p['plants'])})" if p.get("plants") else ""
    mid = _concat(engine, "s.MJAHR", "s.MBLNR", "s.ZEILE")
    return (f"SELECT {mid} AS movement_id, s.MATNR AS sku,\n"
            f"       {_concat(engine, 's.WERKS', chr(39) + '-' + chr(39), 's.LGORT')} AS warehouse,\n"
            f"       s.MENGE AS quantity, s.SHKZG AS direction, s.BWART AS movement_type, k.BUDAT AS posted_on\n"
            f"FROM {_sch(p)}MSEG s\n"
            f"JOIN {_sch(p)}MKPF k ON k.MANDT = s.MANDT AND k.MBLNR = s.MBLNR AND k.MJAHR = s.MJAHR\n"
            f"WHERE s.MANDT = '{p['client']}'\n"
            f"  AND s.LGORT <> ' '\n"
            f"  AND s.SOBKZ = ' '{plants}")


def _sap_matdoc(engine: str, p: dict) -> str:
    plants = f"\n  AND WERKS IN ({_lit_list(p['plants'])})" if p.get("plants") else ""
    mid = _concat(engine, "MJAHR", "MBLNR", "ZEILE")
    return (f"SELECT {mid} AS movement_id, MATNR AS sku,\n"
            f"       {_concat(engine, 'WERKS', chr(39) + '-' + chr(39), 'LGORT')} AS warehouse,\n"
            f"       MENGE AS quantity, SHKZG AS direction, BWART AS movement_type, BUDAT AS posted_on\n"
            f"FROM {_sch(p)}MATDOC\n"
            f"WHERE MANDT = '{p['client']}'\n"
            f"  AND LGORT <> ' '{plants}")


def _ebs_moqd(engine: str, p: dict) -> str:
    return ("SELECT msi.SEGMENT1 AS sku,\n"
            "       mp.ORGANIZATION_CODE || '-' || moqd.SUBINVENTORY_CODE AS warehouse,\n"
            "       moqd.LOT_NUMBER AS lot, moqd.PRIMARY_TRANSACTION_QUANTITY AS current_stock\n"
            f"FROM {_sch(p)}MTL_ONHAND_QUANTITIES_DETAIL moqd\n"
            f"JOIN {_sch(p)}MTL_SYSTEM_ITEMS_B msi ON msi.INVENTORY_ITEM_ID = moqd.INVENTORY_ITEM_ID\n"
            "  AND msi.ORGANIZATION_ID = moqd.ORGANIZATION_ID\n"
            f"JOIN {_sch(p)}MTL_PARAMETERS mp ON mp.ORGANIZATION_ID = moqd.ORGANIZATION_ID\n"
            f"WHERE moqd.ORGANIZATION_ID IN ({p['organizations']})")


def _ebs_mmt(engine: str, p: dict) -> str:
    return ("SELECT mmt.TRANSACTION_ID AS movement_id, msi.SEGMENT1 AS sku,\n"
            "       mp.ORGANIZATION_CODE || '-' || mmt.SUBINVENTORY_CODE AS warehouse,\n"
            "       mmt.PRIMARY_QUANTITY AS quantity, mmt.TRANSACTION_TYPE_ID AS movement_type,\n"
            "       mmt.TRANSACTION_DATE AS posted_on\n"
            f"FROM {_sch(p)}MTL_MATERIAL_TRANSACTIONS mmt\n"
            f"JOIN {_sch(p)}MTL_SYSTEM_ITEMS_B msi ON msi.INVENTORY_ITEM_ID = mmt.INVENTORY_ITEM_ID\n"
            "  AND msi.ORGANIZATION_ID = mmt.ORGANIZATION_ID\n"
            f"JOIN {_sch(p)}MTL_PARAMETERS mp ON mp.ORGANIZATION_ID = mmt.ORGANIZATION_ID\n"
            f"WHERE mmt.ORGANIZATION_ID IN ({p['organizations']})\n"
            "  AND mmt.SUBINVENTORY_CODE IS NOT NULL\n"
            "  AND NVL(mmt.LOGICAL_TRANSACTION, 2) <> 1")


def _d365_inventsum(engine: str, p: dict) -> str:
    return ("SELECT s.ITEMID AS sku, d.INVENTLOCATIONID AS warehouse,\n"
            "       s.PHYSICALINVENT AS current_stock\n"
            f"FROM {_sch(p)}INVENTSUM s\n"
            f"JOIN {_sch(p)}INVENTDIM d ON d.INVENTDIMID = s.INVENTDIMID\n"
            "  AND d.DATAAREAID = s.DATAAREAID AND d.PARTITION = s.PARTITION\n"
            f"WHERE s.DATAAREAID = '{p['company'].lower()}'\n"
            "  AND s.CLOSED = 0")


def _bc_ledger(engine: str, p: dict) -> str:
    suffix = ("$" + p["app_id"]) if p.get("app_id") else ""     # BC's table-name separator, not money
    company = p["company"].replace(".", "_")
    table = f"[{company}$Item Ledger Entry{suffix}]"
    return ("SELECT [Entry No_] AS movement_id, [Item No_] AS sku, [Location Code] AS warehouse,\n"
            "       [Quantity] AS quantity, [Entry Type] AS movement_type, [Posting Date] AS posted_on\n"
            f"FROM {_sch(p)}{table}")


def _jde_f41021(engine: str, p: dict) -> str:
    branches = f"\n  AND {_trim(engine, 'li.LIMCU')} IN ({_lit_list(p['branches'])})" if p.get("branches") else ""
    return (f"SELECT {_trim(engine, 'im.IMLITM')} AS sku, {_trim(engine, 'li.LIMCU')} AS warehouse,\n"
            f"       li.LILOCN AS location, li.LILOTN AS lot, li.LIPQOH AS current_stock\n"
            f"FROM {_sch(p)}F41021 li\n"
            f"JOIN {_sch(p)}F4101 im ON im.IMITM = li.LIITM\n"
            f"WHERE 1 = 1{branches}")


def _jde_f4111(engine: str, p: dict) -> str:
    branches = f"\n  AND {_trim(engine, 'il.ILMCU')} IN ({_lit_list(p['branches'])})" if p.get("branches") else ""
    return (f"SELECT il.ILUKID AS movement_id, {_trim(engine, 'im.IMLITM')} AS sku,\n"
            f"       {_trim(engine, 'il.ILMCU')} AS warehouse, il.ILTRQT AS quantity, il.ILDCT AS movement_type\n"
            f"FROM {_sch(p)}F4111 il\n"
            f"JOIN {_sch(p)}F4101 im ON im.IMITM = il.ILITM\n"
            f"WHERE 1 = 1{branches}")


def _m3_mitbal(engine: str, p: dict) -> str:
    return ("SELECT MBITNO AS sku, MBWHLO AS warehouse, MBSTQT AS current_stock\n"
            f"FROM {_sch(p)}MITBAL\n"
            f"WHERE MBCONO = {int(p['company'])}")


def _odoo_quant(engine: str, p: dict) -> str:
    company = f"\n  AND sq.company_id = {int(p['company_id'])}" if p.get("company_id") else ""
    return ("SELECT pp.default_code AS sku, COALESCE(sw.code, sl.complete_name) AS warehouse,\n"
            "       sq.quantity AS current_stock\n"
            "FROM stock_quant sq\n"
            "JOIN product_product pp ON pp.id = sq.product_id\n"
            "JOIN stock_location sl ON sl.id = sq.location_id\n"
            "LEFT JOIN stock_warehouse sw ON sw.id = sl.warehouse_id\n"
            f"WHERE sl.usage = 'internal'{company}")


def _magento_msi(engine: str, p: dict) -> str:
    pre = p.get("table_prefix") or ""
    return ("SELECT sku, source_code AS warehouse, quantity AS current_stock\n"
            f"FROM {pre}inventory_source_item")


def _woo(engine: str, p: dict) -> str:
    pre = p.get("table_prefix") or "wp_"
    return ("SELECT sku.meta_value AS sku, stock.meta_value AS current_stock\n"
            f"FROM {pre}posts po\n"
            f"JOIN {pre}postmeta sku ON sku.post_id = po.ID AND sku.meta_key = '_sku'\n"
            f"JOIN {pre}postmeta stock ON stock.post_id = po.ID AND stock.meta_key = '_stock'\n"
            "WHERE po.post_type IN ('product', 'product_variation')\n"
            "  AND po.post_status <> 'trash'\n"
            "  AND sku.meta_value <> ''")


def _scd2(engine: str, p: dict) -> str:
    return ("SELECT item_code AS sku, warehouse_code AS warehouse, on_hand AS current_stock\n"
            "FROM stock_history\n"
            "WHERE valid_to IS NULL          -- only the current version of each row\n"
            "  AND deleted_at IS NULL        -- soft-deleted rows are not stock")


def _union_ledger(engine: str, p: dict) -> str:
    return ("-- One signed stream from several documents; ids are made unique per source.\n"
            "SELECT receipt_id * 10 + 1 AS movement_id, item_code AS sku, warehouse_code AS warehouse,\n"
            "       quantity AS quantity, posted_at FROM purchase_receipts WHERE status = 'posted'\n"
            "UNION ALL\n"
            "SELECT line_id * 10 + 2, item_code, warehouse_code, -quantity, posted_at\n"
            "FROM sales_lines WHERE status = 'posted'\n"
            "UNION ALL\n"
            "SELECT adjustment_id * 10 + 3, item_code, warehouse_code, quantity_delta, posted_at\n"
            "FROM stock_adjustments\n"
            "UNION ALL\n"
            "SELECT transfer_id * 10 + 4, item_code, from_warehouse, -quantity, shipped_at FROM transfers\n"
            "UNION ALL\n"
            "SELECT transfer_id * 10 + 5, item_code, to_warehouse, quantity, received_at FROM transfers\n"
            "WHERE received_at IS NOT NULL")


_SNAP = {"sku": {"column": "sku"}, "warehouse": {"column": "warehouse"}, "current_stock": {"column": "current_stock"}}
_LEDGER_MAP = {"sku": {"column": "sku"}, "warehouse": {"column": "warehouse"}, "current_stock": {"column": "quantity"}}
_SQL_ENGINES = ("postgresql", "mysql", "mssql", "oracle")

PRESETS: dict[str, dict[str, Any]] = {
    "sap_ecc_mard": {
        "vendor": "SAP", "product": "SAP ECC (on Oracle / SQL Server)", "engines": ("oracle", "mssql"),
        "build": _sap_mard, "stock_model": "snapshot", "mapping": _SNAP, "model_config": {},
        "params": [_p("client", "sap_client"), _p("schema", "schema", False), _p("plants", "code_list", False)],
        "strategy": "full", "gotchas": ["sap_client", "sap_stock_categories", "sap_s4_calculated", "sap_leading_zeros"],
    },
    "sap_ecc_mchb": {
        "vendor": "SAP", "product": "SAP ECC - batches (MCHB)", "engines": ("oracle", "mssql"),
        "build": _sap_mchb, "stock_model": "aggregate", "mapping": _SNAP, "model_config": {"aggregate": "sum"},
        "params": [_p("client", "sap_client"), _p("schema", "schema", False), _p("plants", "code_list", False)],
        "strategy": "full", "gotchas": ["sap_client", "sap_stock_categories", "sap_s4_calculated", "sap_leading_zeros"],
    },
    "sap_ecc_mseg_ledger": {
        "vendor": "SAP", "product": "SAP ECC - material documents (MSEG + MKPF)", "engines": ("oracle", "mssql"),
        "build": _sap_mseg, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id",
                         "sign": {"mode": "type_map", "type_column": "direction",
                                  "type_map": {"S": 1, "H": -1}, "unknown": "reject"}},
        "params": [_p("client", "sap_client"), _p("schema", "schema", False), _p("plants", "code_list", False)],
        "strategy": "full", "gotchas": ["sap_client", "sap_special_stock", "ledger_reconcile_only", "back_postings"],
    },
    "sap_s4_matdoc_replica": {
        "vendor": "SAP", "product": "SAP S/4HANA - MATDOC replicated to a warehouse", "engines": _SQL_ENGINES,
        "build": _sap_matdoc, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id",
                         "sign": {"mode": "type_map", "type_column": "direction",
                                  "type_map": {"S": 1, "H": -1}, "unknown": "reject"}},
        "params": [_p("client", "sap_client"), _p("schema", "schema", False), _p("plants", "code_list", False)],
        "strategy": "full", "gotchas": ["sap_client", "s4_hana_not_reachable", "ledger_reconcile_only", "back_postings"],
    },
    "oracle_ebs_onhand": {
        "vendor": "Oracle", "product": "Oracle E-Business Suite - on hand (MOQD)", "engines": ("oracle",),
        "build": _ebs_moqd, "stock_model": "aggregate", "mapping": _SNAP, "model_config": {"aggregate": "sum"},
        "params": [_p("organizations", "int_list"), _p("schema", "schema", False, "INV")],
        "strategy": "full", "gotchas": ["ebs_organization", "lots_summed"],
    },
    "oracle_ebs_transactions": {
        "vendor": "Oracle", "product": "Oracle E-Business Suite - material transactions (MMT)", "engines": ("oracle",),
        "build": _ebs_mmt, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id", "sign": {"mode": "signed"}},
        "params": [_p("organizations", "int_list"), _p("schema", "schema", False, "INV")],
        "strategy": "ledger_cursor", "gotchas": ["ebs_organization", "sequence_cursor_rac", "back_postings"],
    },
    "dynamics_inventsum": {
        "vendor": "Microsoft", "product": "Dynamics 365 F&O / AX 2012 - InventSum", "engines": ("mssql",),
        "build": _d365_inventsum, "stock_model": "aggregate", "mapping": _SNAP, "model_config": {"aggregate": "sum"},
        "params": [_p("company", "code"), _p("schema", "schema", False, "dbo")],
        "strategy": "full", "gotchas": ["d365_dataareaid", "d365_cloud_byod"],
    },
    "business_central_item_ledger": {
        "vendor": "Microsoft", "product": "Business Central / NAV - Item Ledger Entry", "engines": ("mssql",),
        "build": _bc_ledger, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id", "sign": {"mode": "signed"}},
        "params": [_p("company", "bc_company"), _p("app_id", "guid", False), _p("schema", "schema", False, "dbo")],
        "strategy": "ledger_cursor", "gotchas": ["bc_company_table", "back_postings"],
    },
    "jde_item_location": {
        "vendor": "Oracle", "product": "JD Edwards EnterpriseOne - F41021", "engines": ("oracle", "mssql"),
        "build": _jde_f41021, "stock_model": "aggregate", "mapping": _SNAP, "model_config": {"aggregate": "sum"},
        "params": [_p("decimals", "decimals", True, "0"), _p("schema", "schema", False, "PRODDTA"),
                   _p("branches", "code_list", False)],
        "strategy": "full", "gotchas": ["jde_implied_decimals", "jde_padded_codes", "lots_summed"],
    },
    "jde_item_ledger": {
        "vendor": "Oracle", "product": "JD Edwards EnterpriseOne - F4111 item ledger", "engines": ("oracle", "mssql"),
        "build": _jde_f4111, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id", "sign": {"mode": "signed"}},
        "params": [_p("decimals", "decimals", True, "0"), _p("schema", "schema", False, "PRODDTA"),
                   _p("branches", "code_list", False)],
        "strategy": "ledger_cursor", "gotchas": ["jde_implied_decimals", "jde_padded_codes", "back_postings"],
    },
    "infor_m3_mitbal": {
        "vendor": "Infor", "product": "Infor M3 - MITBAL", "engines": ("mssql", "oracle"),
        "build": _m3_mitbal, "stock_model": "snapshot", "mapping": _SNAP, "model_config": {},
        "params": [_p("company", "int"), _p("schema", "schema", False, "MVXJDTA")],
        "strategy": "full", "gotchas": ["m3_company"],
    },
    "odoo_stock_quant": {
        "vendor": "Odoo", "product": "Odoo - stock_quant", "engines": ("postgresql",),
        "build": _odoo_quant, "stock_model": "aggregate", "mapping": _SNAP, "model_config": {"aggregate": "sum"},
        "params": [_p("company_id", "int", False)],
        "strategy": "full", "gotchas": ["odoo_company", "lots_summed"],
    },
    "magento_msi": {
        "vendor": "Adobe", "product": "Magento / Adobe Commerce MSI", "engines": ("mysql",),
        "build": _magento_msi, "stock_model": "snapshot", "mapping": _SNAP, "model_config": {},
        "params": [_p("table_prefix", "prefix", False)],
        "strategy": "full", "gotchas": ["magento_salable"],
    },
    "woocommerce": {
        "vendor": "WooCommerce", "product": "WooCommerce (one warehouse)", "engines": ("mysql",),
        "build": _woo, "stock_model": "snapshot", "mapping": {"sku": {"column": "sku"},
                                                              "current_stock": {"column": "current_stock"}},
        "model_config": {}, "params": [_p("table_prefix", "prefix", False, "wp_")],
        "strategy": "full", "gotchas": ["single_warehouse"],
    },
    "generic_scd2": {
        "vendor": "Generic", "product": "History table (SCD2 / soft deletes)", "engines": _SQL_ENGINES,
        "build": _scd2, "stock_model": "snapshot", "mapping": _SNAP, "model_config": {}, "params": [],
        "strategy": "full", "gotchas": ["scd2_current_row", "soft_deletes"], "verified": True,
    },
    "generic_union_ledger": {
        "vendor": "Generic", "product": "Receipts + sales + adjustments + transfers (UNION ALL)",
        "engines": _SQL_ENGINES, "build": _union_ledger, "stock_model": "ledger", "mapping": _LEDGER_MAP,
        "model_config": {"id_column": "movement_id", "cursor_column": "movement_id", "sign": {"mode": "signed"}},
        "params": [], "strategy": "full", "gotchas": ["ledger_reconcile_only", "back_postings"], "verified": True,
    },
}

# Systems StockAI does not read directly, and what to do instead.
NOT_DIRECT: dict[str, dict[str, str]] = {
    "sap_s4hana": {"vendor": "SAP", "path": "push_or_replica"},
    "netsuite": {"vendor": "Oracle NetSuite", "path": "push"},
    "shopify": {"vendor": "Shopify", "path": "push"},
    "manhattan": {"vendor": "Manhattan Associates", "path": "push_or_replica"},
    "blue_yonder": {"vendor": "Blue Yonder", "path": "push_or_replica"},
    "snowflake": {"vendor": "Snowflake", "path": "push"},
    "bigquery": {"vendor": "Google BigQuery", "path": "push"},
    "databricks": {"vendor": "Databricks", "path": "push"},
    "redshift": {"vendor": "Amazon Redshift", "path": "engine_postgresql"},
    "synapse": {"vendor": "Azure Synapse / Fabric SQL endpoint / Azure SQL", "path": "engine_mssql"},
    "aurora_mariadb": {"vendor": "Aurora MySQL / MariaDB / TiDB", "path": "engine_mysql"},
    "aurora_postgres": {"vendor": "Aurora / AlloyDB / Cloud SQL PostgreSQL", "path": "engine_postgresql"},
}


def _bad(reason: str, **params: Any) -> AppError:
    return AppError("inventory_sync_preset_invalid", f"The preset cannot be used: {reason}.",
                    status_code=422, params={"reason": reason, **params})


def catalogue() -> dict:
    out = []
    for pid, pr in PRESETS.items():
        out.append({
            "id": pid, "vendor": pr["vendor"], "product": pr["product"], "engines": list(pr["engines"]),
            "stock_model": pr["stock_model"], "strategy": pr["strategy"],
            "params": [{k: v for k, v in p.items()} for p in pr["params"]],
            "gotchas": pr["gotchas"], "verified": bool(pr.get("verified", False)),
        })
    return {"presets": out, "not_direct": NOT_DIRECT}


def render(preset_id: str, engine: str, params: Optional[dict] = None) -> dict:
    """The profile body for `preset_id` on `engine`, parameters validated."""
    pr = PRESETS.get(preset_id)
    if pr is None:
        raise AppError("inventory_sync_preset_not_found", "Unknown preset.", status_code=404,
                       params={"preset": str(preset_id)[:60]})
    if engine not in pr["engines"]:
        raise _bad("engine", param="engine", engine=str(engine)[:20], engines=", ".join(pr["engines"]))
    given = {k: ("" if v is None else str(v).strip()) for k, v in (params or {}).items()}
    clean: dict[str, str] = {}
    for spec in pr["params"]:
        v = given.get(spec["name"]) or (spec["default"] or "")
        if not v:
            if spec["required"]:
                raise _bad("param_required", param=spec["name"])
            continue
        if not re.fullmatch(spec["pattern"], v):
            raise _bad("param_format", param=spec["name"])
        clean[spec["name"]] = v
    unknown = set(given) - {s["name"] for s in pr["params"]}
    if unknown:
        raise _bad("param_unknown", param=sorted(unknown)[0][:40])
    build: Callable[[str, dict], str] = pr["build"]
    mapping = {f: dict(m) for f, m in pr["mapping"].items()}
    if "decimals" in clean and clean["decimals"] != "0":
        # JD Edwards stores quantities as integers with implied decimals.
        mapping["current_stock"]["multiplier"] = 10 ** -int(clean["decimals"])
    profile = {
        "name": pr["product"][:120],
        "source_kind": "query",
        "source_query": build(engine, clean),
        "mapping": mapping,
        "stock_model": pr["stock_model"],
        "model_config": dict(pr["model_config"]),
        "strategy": pr["strategy"] if pr["strategy"] != "ledger_cursor" or pr["stock_model"] == "ledger" else "auto",
        "mode": "full",
    }
    return {"profile": profile,
            "preset": {"id": preset_id, "verified": bool(pr.get("verified", False)), "gotchas": pr["gotchas"]}}
