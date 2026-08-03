"""
Generates a per-supplier PDF for a purchase order — one PDF per distinct
supplier name present in the PO's lines, since a single PO can span
multiple suppliers. Reuses the reportlab pattern already established in
backend/api/v1/reports.py::_export_pdf; falls back to a plain-text file if
reportlab isn't installed (same fallback contract as that module).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.storage import paths
from backend.formatting import money
from backend.notifications.locale import render_es

log = logging.getLogger(__name__)


def slugify_supplier_name(name: str) -> str:
    """Filesystem/URL-safe slug for a supplier name, used in the PDF's
    filename and in the public serving URL's path."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "supplier"


def _total_line(priced: list, items: list, total_value: float, currency) -> str:
    """The order's total, stated for exactly the lines that have a price.

    Three cases, and only the first is the old behaviour:
      * every line priced  -> "Total: ₡X"
      * some priced        -> "Total (3 de 5 líneas con precio): ₡X"
      * none priced        -> "Total: pendiente de cotizar"

    Summing unknown costs as zero would have understated the order to the
    supplier, which is the one direction a purchase document must never err in.
    """
    if not priced:
        return render_es("po_pdf_total_none")
    amount = money(total_value, currency=currency)
    if len(priced) == len(items):
        return render_es("po_pdf_total", amount=amount)
    return render_es("po_pdf_total_partial",
                     priced=len(priced), total=len(items), amount=amount)


def generate_po_pdf(
    tenant_id: str,
    po_log_id: str,
    supplier_name: str,
    items: list[dict],
    po_meta: dict,
    currency: dict | None = None,
) -> Path:
    """`currency` is the tenant's currency setting (`currency_of(tenant_id)`).
    Resolved ONCE here, not per row: this document formats two amounts for every
    line of the order and the reader is a DB query. Callers that already hold the
    resolved dict (a loop over suppliers builds one PDF each) should pass it."""
    slug = slugify_supplier_name(supplier_name)
    path = paths.po_pdf_file(tenant_id, po_log_id, slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    if currency is None:
        from backend.api.v1.currency import currency_of
        currency = currency_of(tenant_id)

    # A line whose cost nobody recorded is priced as UNKNOWN, not as zero.
    # `unit_cost or 0` used to make this document — which leaves the tenant and
    # arrives at their supplier, in their name — quote "₡0" per unit and a
    # "Total: ₡0" for the order. The supplier has no way to tell that apart from
    # a price the buyer meant. The total below therefore covers only the priced
    # lines and says how many those are.
    priced = [i for i in items if i.get("unit_cost") is not None]
    total_value = sum((i.get("final_qty") or 0) * float(i["unit_cost"]) for i in priced)

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
        )

        doc = SimpleDocTemplate(str(path), pagesize=letter,
                                leftMargin=0.75*inch, rightMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
        h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceAfter=4)
        body = styles["Normal"]

        story = [
            Paragraph(render_es("po_pdf_heading"), h1),
            HRFlowable(width="100%", thickness=1, color=colors.grey),
            Spacer(1, 0.1*inch),
        ]

        meta = [
            [render_es("po_pdf_supplier"), supplier_name],
            [render_es("po_pdf_issued_on"), str(po_meta.get("generated_at", "N/A"))],
            [render_es("po_pdf_reference"), str(po_meta.get("po_log_id", ""))],
        ]
        t = Table(meta, colWidths=[1.8*inch, 4.7*inch])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.2*inch))

        story.append(Paragraph(render_es("po_pdf_section_lines"), h2))
        header = [render_es("po_pdf_col_sku"), render_es("po_pdf_col_product"),
                  render_es("po_pdf_col_qty"), render_es("po_pdf_col_unit_cost"),
                  render_es("po_pdf_col_subtotal")]
        rows = [header]
        for i in items:
            qty = i.get("final_qty") or 0
            cost = i.get("unit_cost")
            unknown = render_es("po_pdf_cost_unknown")
            rows.append([
                str(i.get("sku", "")),
                str(i.get("display_name") or i.get("sku", "")),
                f"{qty:,.0f}",
                # Precision comes from the currency, not from this table: the two
                # decimals hardcoded here rendered "₡8.50" for a colón cost the
                # app itself shows as "₡9" everywhere else, and would have shown
                # phantom cents for every 0-decimal currency in SUPPORTED.
                unknown if cost is None else money(cost, currency=currency),
                unknown if cost is None else money(qty * float(cost), currency=currency),
            ])
        table = Table(rows, colWidths=[1.1*inch, 2.3*inch, 1*inch, 1.1*inch, 1*inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.15*inch))
        story.append(Paragraph(f"<b>{_total_line(priced, items, total_value, currency)}</b>", body))

        doc.build(story)

    except ImportError:
        log.warning("reportlab not installed — writing plain-text PO at %s", path.with_suffix(".txt"))
        lines = [
            render_es("po_pdf_title"),
            "=" * 50,
            f"{render_es('po_pdf_supplier')}: {supplier_name}",
            f"{render_es('po_pdf_date')}: {po_meta.get('generated_at', 'N/A')}",
            "",
        ]
        for i in items:
            qty = i.get("final_qty") or 0
            cost = i.get("unit_cost")
            shown = (render_es("po_pdf_cost_unknown") if cost is None
                     else money(cost, currency=currency))
            lines.append(f"  {i.get('sku')}: {i.get('display_name') or ''} — {qty:,.0f} x {shown}")
        lines.append(f"\n{_total_line(priced, items, total_value, currency)}")
        path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        return path.with_suffix(".txt")

    return path
