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

log = logging.getLogger(__name__)


def slugify_supplier_name(name: str) -> str:
    """Filesystem/URL-safe slug for a supplier name, used in the PDF's
    filename and in the public serving URL's path."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "supplier"


def generate_po_pdf(
    tenant_id: str,
    po_log_id: str,
    supplier_name: str,
    items: list[dict],
    po_meta: dict,
) -> Path:
    slug = slugify_supplier_name(supplier_name)
    path = paths.po_pdf_file(tenant_id, po_log_id, slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    total_value = sum((i.get("final_qty") or 0) * (i.get("unit_cost") or 0) for i in items)

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
            Paragraph("Orden de Compra", h1),
            HRFlowable(width="100%", thickness=1, color=colors.grey),
            Spacer(1, 0.1*inch),
        ]

        meta = [
            ["Proveedor", supplier_name],
            ["Fecha de emisión", str(po_meta.get("generated_at", "N/A"))],
            ["Referencia", str(po_meta.get("po_log_id", ""))],
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

        story.append(Paragraph("Líneas del pedido", h2))
        header = ["SKU", "Producto", "Cantidad", "Costo unitario", "Subtotal"]
        rows = [header]
        for i in items:
            qty = i.get("final_qty") or 0
            cost = i.get("unit_cost") or 0
            rows.append([
                str(i.get("sku", "")),
                str(i.get("display_name") or i.get("sku", "")),
                f"{qty:,.0f}",
                f"${cost:,.2f}",
                f"${qty * cost:,.2f}",
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
        story.append(Paragraph(f"<b>Total: ${total_value:,.2f}</b>", body))

        doc.build(story)

    except ImportError:
        log.warning("reportlab not installed — writing plain-text PO at %s", path.with_suffix(".txt"))
        lines = [
            "ORDEN DE COMPRA",
            "=" * 50,
            f"Proveedor: {supplier_name}",
            f"Fecha: {po_meta.get('generated_at', 'N/A')}",
            "",
        ]
        for i in items:
            qty = i.get("final_qty") or 0
            cost = i.get("unit_cost") or 0
            lines.append(f"  {i.get('sku')}: {i.get('display_name') or ''} — {qty:,.0f} x ${cost:,.2f}")
        lines.append(f"\nTotal: ${total_value:,.2f}")
        path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        return path.with_suffix(".txt")

    return path
