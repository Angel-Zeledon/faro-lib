"""The purchase-order file each ERP imports, rendered from feed documents.

Every layout here was written from the vendor's published import description and
has NOT been run against a live system. Each carries `verified = False` and the
screen says so; flip a flag only after a real import succeeded. Nothing here
invents a value: a field the company has not mapped (a vendor number, a plant) is
an empty cell, and `unmapped_fields` tells the person which ones to fill in.

A document is the dict `service.build_documents` returns:
{document_id, number, supplier{name, erp_code}, currency, order_date,
 expected_date, warehouse, lines[{line_id, sku, name, quantity, unit_cost,
 currency, total}]}.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Callable, Optional

from backend.utils.csv_safe import csv_safe

_BOM = "﻿"


def _num(value: Optional[float]) -> str:
    """A plain decimal an importer parses in any locale; blank when unknown."""
    if value is None:
        return ""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _csv(rows: list[list], *, delimiter: str = ",") -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerows(rows)
    return (_BOM + buf.getvalue()).encode("utf-8")


def _vendor(doc: dict) -> str:
    """The vendor number the ERP knows, else the name as typed."""
    sup = doc.get("supplier") or {}
    return csv_safe(sup.get("erp_code") or sup.get("name") or "")


def generic_csv(docs: list[dict]) -> bytes:
    rows: list[list] = [[
        "document_id", "order_number", "line_id", "supplier_code", "supplier_name",
        "order_date", "expected_date", "warehouse", "sku", "description",
        "quantity", "unit_cost", "currency", "line_total",
    ]]
    for d in docs:
        sup = d.get("supplier") or {}
        for ln in d["lines"]:
            rows.append([
                d["document_id"], d["number"], ln["line_id"],
                csv_safe(sup.get("erp_code") or ""), csv_safe(sup.get("name") or ""),
                d.get("order_date") or "", d.get("expected_date") or "",
                csv_safe(d.get("warehouse") or ""), csv_safe(ln["sku"]),
                csv_safe(ln.get("name") or ""), _num(ln["quantity"]),
                _num(ln.get("unit_cost")), ln.get("currency") or d.get("currency") or "",
                _num(ln.get("total")),
            ])
    return _csv(rows)


def generic_xlsx(docs: list[dict]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "purchase_orders"
    # Decode the CSV rendering so the two files can never disagree.
    text = generic_csv(docs).decode("utf-8").lstrip(_BOM)
    for row in csv.reader(io.StringIO(text)):
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def sap_bapi(docs: list[dict]) -> bytes:
    """Tab-separated records in the BAPI_PO_CREATE1 structures: one `H` record per
    document (POHEADER), then `I` (POITEM) and `S` (POSCHEDULE) per line.
    Company code, purchasing organisation and group, plant and material number
    are the company's own SAP customising and are left blank for them to map."""
    rows: list[list] = [
        ["REC", "ID", "COMP_CODE", "DOC_TYPE", "VENDOR", "PURCH_ORG", "PUR_GROUP",
         "CURRENCY", "DOC_DATE", "PO_ITEM", "MATERIAL", "SHORT_TEXT", "PLANT",
         "QUANTITY", "NET_PRICE", "DELIVERY_DATE"],
    ]
    for d in docs:
        rows.append(["H", d["document_id"], "", "NB", _vendor(d), "", "",
                     d.get("currency") or "", (d.get("order_date") or "").replace("-", ""),
                     "", "", "", "", "", "", ""])
        for n, ln in enumerate(d["lines"], start=1):
            item = f"{n * 10:05d}"
            rows.append(["I", d["document_id"], "", "", "", "", "", "", "", item,
                         csv_safe(ln["sku"]), csv_safe((ln.get("name") or "")[:40]), "",
                         _num(ln["quantity"]), _num(ln.get("unit_cost")), ""])
            rows.append(["S", d["document_id"], "", "", "", "", "", "", "", item,
                         "", "", "", _num(ln["quantity"]), "",
                         (d.get("expected_date") or "").replace("-", "")])
    return _csv(rows, delimiter="\t")


def dynamics_bc(docs: list[dict]) -> bytes:
    """Business Central / Dynamics 365 purchase order header and lines in one
    configuration-package friendly CSV (Document Type = Order)."""
    rows: list[list] = [[
        "Document Type", "Document No.", "Buy-from Vendor No.", "Order Date",
        "Expected Receipt Date", "Currency Code", "Line No.", "Type", "No.",
        "Description", "Quantity", "Direct Unit Cost", "Location Code",
    ]]
    for d in docs:
        for n, ln in enumerate(d["lines"], start=1):
            rows.append(["Order", d["number"], _vendor(d), d.get("order_date") or "",
                         d.get("expected_date") or "", d.get("currency") or "",
                         n * 10000, "Item", csv_safe(ln["sku"]),
                         csv_safe(ln.get("name") or ""), _num(ln["quantity"]),
                         _num(ln.get("unit_cost")), csv_safe(d.get("warehouse") or "")])
    return _csv(rows)


def netsuite(docs: list[dict]) -> bytes:
    """NetSuite CSV import of purchase orders. `External ID` carries the document
    id so a second import of the same order updates it instead of duplicating."""
    rows: list[list] = [[
        "External ID", "Vendor", "Date", "Currency", "Location", "Item", "Quantity",
        "Rate", "Receive By",
    ]]
    for d in docs:
        for ln in d["lines"]:
            rows.append([d["document_id"], _vendor(d), d.get("order_date") or "",
                         d.get("currency") or "", csv_safe(d.get("warehouse") or ""),
                         csv_safe(ln["sku"]), _num(ln["quantity"]),
                         _num(ln.get("unit_cost")), d.get("expected_date") or ""])
    return _csv(rows)


def odoo(docs: list[dict]) -> bytes:
    """Odoo purchase.order import. The header columns repeat only on a document's
    first line, as Odoo's import expects for one2many lines."""
    rows: list[list] = [[
        "id", "partner_ref", "partner_id", "date_order", "currency_id",
        "order_line/product_id", "order_line/name", "order_line/product_qty",
        "order_line/price_unit", "order_line/date_planned",
    ]]
    for d in docs:
        for n, ln in enumerate(d["lines"]):
            first = n == 0
            rows.append([
                d["document_id"] if first else "", d["number"] if first else "",
                _vendor(d) if first else "", (d.get("order_date") or "") if first else "",
                (d.get("currency") or "") if first else "", csv_safe(ln["sku"]),
                csv_safe(ln.get("name") or ln["sku"]), _num(ln["quantity"]),
                _num(ln.get("unit_cost")), d.get("expected_date") or "",
            ])
    return _csv(rows)


def oracle_ebs(docs: list[dict]) -> bytes:
    """Flat rows for the PO_HEADERS_INTERFACE / PO_LINES_INTERFACE open interface
    (standard purchase orders, action ORIGINAL). The importer loads header columns
    and line columns from the same row keyed by INTERFACE_HEADER_KEY."""
    rows: list[list] = [[
        "INTERFACE_HEADER_KEY", "ACTION", "DOCUMENT_TYPE_CODE", "VENDOR_NUM",
        "CURRENCY_CODE", "DOCUMENT_NUM", "LINE_NUM", "ITEM", "QUANTITY",
        "UNIT_PRICE", "NEED_BY_DATE", "SHIP_TO_ORGANIZATION_CODE",
    ]]
    for d in docs:
        for n, ln in enumerate(d["lines"], start=1):
            rows.append([d["document_id"], "ORIGINAL", "STANDARD", _vendor(d),
                         d.get("currency") or "", d["number"], n, csv_safe(ln["sku"]),
                         _num(ln["quantity"]), _num(ln.get("unit_cost")),
                         d.get("expected_date") or "", ""])
    return _csv(rows)


@dataclass(frozen=True)
class ErpFormat:
    key: str
    label: str
    extension: str
    media_type: str
    render: Callable[[list[dict]], bytes]
    # Written from the vendor's documentation, never run against a live system.
    verified: bool
    # Fields the company must fill in on the ERP side because StockAI does not
    # know them (customising the ERP owns them).
    unmapped_fields: tuple[str, ...] = ()


_CSV = "text/csv"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

FORMATS: dict[str, ErpFormat] = {f.key: f for f in (
    ErpFormat("csv", "Generic CSV", "csv", _CSV, generic_csv, verified=True),
    ErpFormat("xlsx", "Generic Excel", "xlsx", _XLSX, generic_xlsx, verified=True),
    ErpFormat("sap", "SAP (BAPI_PO_CREATE1 layout)", "txt", "text/tab-separated-values",
              sap_bapi, verified=False,
              unmapped_fields=("COMP_CODE", "PURCH_ORG", "PUR_GROUP", "PLANT")),
    ErpFormat("dynamics_bc", "Dynamics 365 / Business Central", "csv", _CSV,
              dynamics_bc, verified=False),
    ErpFormat("netsuite", "NetSuite CSV import", "csv", _CSV, netsuite, verified=False),
    ErpFormat("odoo", "Odoo", "csv", _CSV, odoo, verified=False),
    ErpFormat("oracle_ebs", "Oracle E-Business Suite (open interface)", "csv", _CSV,
              oracle_ebs, verified=False,
              unmapped_fields=("SHIP_TO_ORGANIZATION_CODE",)),
)}
