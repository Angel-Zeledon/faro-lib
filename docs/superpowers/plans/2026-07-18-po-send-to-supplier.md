# 2.2 — Envío de PO al Proveedor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Enviar pedido" action that sends a purchase order's PDF to its supplier(s) by email and WhatsApp, closing the loop the spec describes ("hoy el ciclo termina en 'descargar CSV'... si la orden nace Y se envía desde Faro, Faro se vuelve el sistema donde viven las compras").

**Architecture:** A PO can span multiple suppliers (each line carries a free-text `proveedor` name). The new endpoint groups a PO's lines by supplier, generates one PDF per supplier (reusing the `reportlab` pattern already used for session reports), and sends it via whichever channels that supplier has on file (email with a real attachment, WhatsApp with a `MediaUrl` pointing at a new — intentionally unauthenticated, since Twilio can't carry a Bearer token — PDF-serving endpoint). WhatsApp delivery only works once the app has a real public URL (not yet true locally); this is accepted and documented, not blocking.

**Tech Stack:** FastAPI, reportlab (already a dependency, used in `backend/api/v1/reports.py`), Resend/SMTP (existing `backend/notifications/email.py`), Twilio (existing `backend/notifications/whatsapp.py`), Next.js/TypeScript.

## Global Constraints

- A PO's lines are grouped by `proveedor` (a free-text name on `inventory_po_items`, not a foreign key) — a single "send" action must resolve to one send per distinct supplier name present in the PO's approved/modified lines, not one send for the whole PO.
- Supplier lookup by name is case-insensitive (`LOWER(name) = LOWER(proveedor)`), matching the existing convention in `backend/inventory/reception_service.py::get_supplier_scorecard`'s join.
- Lines whose `proveedor` is null/empty, or whose name doesn't match any saved supplier, or whose matched supplier has neither `email` nor `whatsapp` on file, are skipped (not sent) and reported back as such — never silently dropped, never a 500.
- The endpoint is mutating and cost-incurring (a real email/WhatsApp send) — gated by `require_analyst_or_above`, matching every other mutating endpoint in `backend/api/v1/inventory.py` (`receive_po`, `log_po`, etc.), unlike the read-only computed endpoints (`get_current_user`) also in this file.
- The PDF-serving endpoint Twilio's `MediaUrl` fetches is **intentionally unauthenticated** — Twilio's servers cannot present this app's Bearer token. This is accepted: the URL embeds an unguessable `po_log_id` plus a supplier slug, is read-only, and serves nothing more sensitive than what's already emailed to the same supplier.
- WhatsApp's `MediaUrl` must be built from `settings.frontend_url` (the Next.js dev server proxies `/api/*` to the backend, per this repo's `CLAUDE.md`) — reusing the exact pattern already used for other user-facing links (e.g. `send_alert_now`'s `inventory_url = f"{settings.frontend_url}/inventory"`), not a new setting. On `localhost`, this URL is unreachable from Twilio's servers — accepted per the user's explicit decision to build this now and let it become live once the app is deployed publicly.
- Do not introduce a new PDF library — reuse `reportlab`, already a dependency and already used in `backend/api/v1/reports.py::_export_pdf`.
- Do not use `window.confirm()` for the frontend's send confirmation — this codebase explicitly replaced native `alert()`/`confirm()` with in-UI patterns elsewhere (commit `135ecab`). Use an inline two-step button (first click asks to confirm, second click within a few seconds actually sends) rather than a new modal component, since this is a single low-complexity action.

---

### Task 1: `get_supplier_by_name` lookup

**Files:**
- Modify: `backend/inventory/supplier_service.py`
- Test: `backend/tests/test_suppliers.py` (create if it doesn't already cover this; check for an existing file first — if `backend/tests/test_supplier_scorecard.py` or similar already exists, add a new test class there instead of creating a new file)

**Interfaces:**
- Consumes: nothing new — uses the existing `query_one` import already used by every other function in this file.
- Produces: `get_supplier_by_name(tenant_id: str, name: str) -> Optional[dict]` — case-insensitive match, returns `None` if `name` is falsy or no match exists.

- [ ] **Step 1: Check for an existing supplier test file**

Run: `ls backend/tests/ | grep -i supplier` — if a file like `test_supplier_scorecard.py` exists, add the new test class to it. If nothing suitable exists, create `backend/tests/test_suppliers.py`.

- [ ] **Step 2: Write the failing test**

```python
# add to the chosen test file
from uuid import uuid4


class TestGetSupplierByName:
    def test_matches_case_insensitively(self, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        tid = test_tenant["id"]
        name = f"Distribuidora {uuid4().hex[:8]}"
        created = sup_svc.create_supplier(tid, {"name": name, "email": "ventas@example.com"})

        found = sup_svc.get_supplier_by_name(tid, name.upper())
        assert found is not None
        assert found["id"] == created["id"]

    def test_returns_none_for_no_match(self, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        assert sup_svc.get_supplier_by_name(test_tenant["id"], "Nonexistent Supplier XYZ") is None

    def test_returns_none_for_empty_name(self, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        assert sup_svc.get_supplier_by_name(test_tenant["id"], "") is None
        assert sup_svc.get_supplier_by_name(test_tenant["id"], None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/<chosen_file>.py::TestGetSupplierByName -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'get_supplier_by_name'`

- [ ] **Step 3: Write the implementation**

Add to `backend/inventory/supplier_service.py` (near `get_supplier`, using the same `query_one` import already at the top of this file):

```python
def get_supplier_by_name(tenant_id: str, name: Optional[str]) -> Optional[dict]:
    """Case-insensitive lookup by name — PO line items store a free-text
    proveedor name, not a supplier_id, so sending a PO to its supplier
    needs to resolve that name back to a supplier record."""
    if not name:
        return None
    return query_one(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND LOWER(name) = LOWER(%s)",
        (tenant_id, name),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/<chosen_file>.py::TestGetSupplierByName -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/supplier_service.py backend/tests/<chosen_file>.py
git commit -m "feat(suppliers): add case-insensitive lookup by name"
```

---

### Task 2: PO PDF generator

**Files:**
- Create: `backend/inventory/po_pdf.py`
- Modify: `backend/storage/paths.py` (add one function)
- Test: `backend/tests/test_po_pdf.py`

**Interfaces:**
- Consumes: `backend.storage.paths` module pattern (mirror `reports_artifact_dir`); no DB access in this module — pure function operating on data passed in.
- Produces: `generate_po_pdf(tenant_id: str, po_log_id: str, supplier_name: str, items: list[dict], po_meta: dict) -> Path` where `items` is a list of dicts each shaped like `backend.inventory.reception_service.get_po_items`'s row (`sku`, `display_name`, `proveedor`, `cantidad_final`, `costo_unitario`) and `po_meta` is `{"generated_at": str, "po_log_id": str}`. Returns the `Path` the PDF was written to. Also produces `paths.po_pdf_file(tenant_id: str, po_log_id: str, supplier_slug: str) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_po_pdf.py
from uuid import uuid4


def test_generate_po_pdf_writes_a_real_pdf_file(tmp_path, monkeypatch):
    from backend.storage import paths
    monkeypatch.setattr(paths, "_base", lambda: tmp_path)

    from backend.inventory.po_pdf import generate_po_pdf

    tid = f"tenant_{uuid4().hex[:8]}"
    po_log_id = f"po_{uuid4().hex[:8]}"
    items = [
        {"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "proveedor": "Distribuidora Andina",
         "cantidad_final": 312.0, "costo_unitario": 8.5},
        {"sku": "SKU-002", "display_name": "Arroz 5kg", "proveedor": "Distribuidora Andina",
         "cantidad_final": 475.0, "costo_unitario": 5.2},
    ]
    po_meta = {"generated_at": "2026-07-18T10:00:00", "po_log_id": po_log_id}

    path = generate_po_pdf(tid, po_log_id, "Distribuidora Andina", items, po_meta)

    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    # A real PDF file always starts with this magic header.
    assert path.read_bytes()[:5] == b"%PDF-"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_po_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.inventory.po_pdf'`

- [ ] **Step 3: Add the paths helper**

Add to `backend/storage/paths.py`, in the "Artifacts" section (near `reports_artifact_dir`):

```python
def po_pdf_dir(tenant_id: str) -> Path:
    return _base() / "pos" / tenant_id

def po_pdf_file(tenant_id: str, po_log_id: str, supplier_slug: str) -> Path:
    return po_pdf_dir(tenant_id) / f"{po_log_id}_{supplier_slug}.pdf"
```

- [ ] **Step 4: Write the PDF generator**

```python
# backend/inventory/po_pdf.py
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
    return slug or "proveedor"


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

    total_value = sum((i.get("cantidad_final") or 0) * (i.get("costo_unitario") or 0) for i in items)

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
            qty = i.get("cantidad_final") or 0
            cost = i.get("costo_unitario") or 0
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
            qty = i.get("cantidad_final") or 0
            cost = i.get("costo_unitario") or 0
            lines.append(f"  {i.get('sku')}: {i.get('display_name') or ''} — {qty:,.0f} x ${cost:,.2f}")
        lines.append(f"\nTotal: ${total_value:,.2f}")
        path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        return path.with_suffix(".txt")

    return path
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_po_pdf.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/po_pdf.py backend/storage/paths.py backend/tests/test_po_pdf.py
git commit -m "feat(inventory): generate a per-supplier PO PDF (reportlab)"
```

---

### Task 3: Email attachment support + supplier-targeted email

**Files:**
- Modify: `backend/notifications/email.py`
- Test: `backend/tests/test_notifications.py` (check if this file exists first; if a different name already covers `email.py`, add to that instead)

**Interfaces:**
- Consumes: nothing new.
- Produces: `send_po_to_supplier_email(to: str, supplier_name: str, po_log_id: str, items: list[dict], pdf_bytes: bytes, pdf_filename: str) -> bool` (same `bool`-return, try/except-swallowing convention as `send_verification_email`/`send_account_setup_email`). `_transport_send`/`_send_resend`/`_send_smtp`/`_send` all gain an optional `attachment: Optional[dict] = None` parameter, shaped `{"filename": str, "content_bytes": bytes}`.

- [ ] **Step 1: Check for an existing notifications test file**

Run: `ls backend/tests/ | grep -i notif` — if nothing exists, create `backend/tests/test_notifications.py`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_notifications.py
from unittest import mock


class TestSendPOToSupplierEmail:
    def test_calls_send_with_attachment_and_supplier_name_in_body(self, monkeypatch):
        from backend.notifications import email as email_mod

        captured = {}

        def fake_send(to, subject, html, attachment=None):
            captured["to"] = to
            captured["subject"] = subject
            captured["html"] = html
            captured["attachment"] = attachment

        monkeypatch.setattr(email_mod, "_send", fake_send)

        items = [{"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "cantidad_final": 312.0, "costo_unitario": 8.5}]
        result = email_mod.send_po_to_supplier_email(
            to="ventas@distribuidoraandina.com",
            supplier_name="Distribuidora Andina",
            po_log_id="po_abc123",
            items=items,
            pdf_bytes=b"%PDF-1.4 fake content",
            pdf_filename="po_abc123_distribuidora-andina.pdf",
        )

        assert result is True
        assert captured["to"] == "ventas@distribuidoraandina.com"
        assert "Distribuidora Andina" in captured["html"]
        assert "Aceite de Oliva 1L" in captured["html"]
        assert captured["attachment"] == {
            "filename": "po_abc123_distribuidora-andina.pdf",
            "content_bytes": b"%PDF-1.4 fake content",
        }

    def test_returns_false_on_send_failure(self, monkeypatch):
        from backend.notifications import email as email_mod

        def failing_send(to, subject, html, attachment=None):
            raise RuntimeError("transport down")

        monkeypatch.setattr(email_mod, "_send", failing_send)

        result = email_mod.send_po_to_supplier_email(
            to="x@example.com", supplier_name="X", po_log_id="po_1",
            items=[], pdf_bytes=b"", pdf_filename="po_1_x.pdf",
        )
        assert result is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_notifications.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'send_po_to_supplier_email'`

- [ ] **Step 4: Add attachment support to the transport layer**

In `backend/notifications/email.py`, modify the imports and the three transport functions:

```python
import base64
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
```

Replace `_send_resend`:

```python
def _send_resend(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """Send via the Resend HTTP API. Raises on failure."""
    import httpx

    payload = {
        "from": settings.email_from,
        "to": [to],
        "subject": f"[{_APP_NAME}] {subject}",
        "html": html,
    }
    if attachment:
        payload["attachments"] = [{
            "filename": attachment["filename"],
            "content": base64.b64encode(attachment["content_bytes"]).decode("ascii"),
        }]

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
```

Replace `_send_smtp`:

```python
def _send_smtp(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """Send via SMTP TLS (fallback transport). Raises on failure."""
    msg = MIMEMultipart("mixed" if attachment else "alternative")
    msg["Subject"] = f"[{_APP_NAME}] {subject}"
    msg["From"]    = f"{_APP_NAME} <{settings.smtp_user}>"
    msg["To"]      = to

    if attachment:
        body_part = MIMEMultipart("alternative")
        body_part.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body_part)
        part = MIMEApplication(attachment["content_bytes"], Name=attachment["filename"])
        part["Content-Disposition"] = f'attachment; filename="{attachment["filename"]}"'
        msg.attach(part)
    else:
        msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.sendmail(settings.smtp_user, to, msg.as_string())
```

Replace `_transport_send` and `_send`:

```python
def _transport_send(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """
    Dispatch an email: Resend when RESEND_API_KEY is set, SMTP as fallback,
    logged no-op with neither. Raises on transport failure so callers can
    report `email_sent=False`.
    """
    if settings.resend_api_key:
        _send_resend(to, subject, html, attachment)
        log.info("Email sent via Resend → %s | subject: %s", to, subject)
        return

    if not settings.smtp_user or not settings.smtp_pass:
        log.warning("No email transport configured (RESEND_API_KEY / SMTP) — email not sent to %s", to)
        return

    _send_smtp(to, subject, html, attachment)
    log.info("Email sent via SMTP → %s | subject: %s", to, subject)


def _send(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    # Thin wrapper so tests (conftest) can patch the single `_send` entrypoint
    # while the dispatch logic in _transport_send stays independently testable.
    _transport_send(to, subject, html, attachment)
```

- [ ] **Step 5: Add the supplier-facing email function**

Append to `backend/notifications/email.py`:

```python
def send_po_to_supplier_email(
    to: str,
    supplier_name: str,
    po_log_id: str,
    items: list[dict],
    pdf_bytes: bytes,
    pdf_filename: str,
) -> bool:
    """Send a purchase order's PDF to its supplier. Returns True if sent."""
    def _row(item: dict) -> str:
        sku = item.get("sku", "")
        name = item.get("display_name") or sku
        qty = item.get("cantidad_final") or 0
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td style="padding:8px 10px;font-family:monospace;font-size:12px;">{sku}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:{_DIM};">{name}</td>'
            f'<td style="padding:8px 10px;font-size:12px;font-weight:600;">{qty:,.0f}</td>'
            f'</tr>'
        )

    table_html = (
        '<table width="100%" style="border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#13141e;">'
        f'<th style="padding:8px 10px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">SKU</th>'
        f'<th style="padding:8px 10px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Producto</th>'
        f'<th style="padding:8px 10px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Cantidad</th>'
        '</tr></thead><tbody>' + "".join(_row(i) for i in items) + '</tbody></table>'
    )

    html = _base_html(
        "Nueva orden de compra",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Nueva orden de compra</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          Hola {supplier_name}, adjuntamos una nueva orden de compra. El detalle completo
          está en el PDF adjunto.
        </p>
        {table_html}
        <p style="color:{_DIM};font-size:11px;margin:20px 0 0;">
          Referencia: {po_log_id}
        </p>
        """,
    )
    try:
        _send(to, f"Orden de compra — {po_log_id}", html,
              attachment={"filename": pdf_filename, "content_bytes": pdf_bytes})
        return True
    except Exception as exc:
        log.error("Failed to send PO email to supplier %s <%s>: %s", supplier_name, to, exc)
        return False
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_notifications.py -v`
Expected: 2 passed

- [ ] **Step 7: Run the full email-related regression to confirm the attachment param didn't break existing callers**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_endpoints.py tests/test_endpoints_offline.py tests/test_canonical_api.py -v`
Expected: all passing (these are the 3 files whose conftest/tests patch `_send`/`_transport_send`, per this repo's `CLAUDE.md` testing standards note)

- [ ] **Step 8: Commit**

```bash
git add backend/notifications/email.py backend/tests/test_notifications.py
git commit -m "feat(notifications): email attachment support + send PO to supplier"
```

---

### Task 4: WhatsApp MediaUrl support + supplier-targeted message

**Files:**
- Modify: `backend/notifications/whatsapp.py`
- Test: `backend/tests/test_notifications.py` (append to the file Task 3 created)

**Interfaces:**
- Consumes: nothing new.
- Produces: `send_whatsapp(to_number: str, body: str, media_url: str | None = None) -> bool` (extended signature, backward compatible — existing callers passing only `to_number`/`body` are unaffected). `build_po_supplier_text(supplier_name: str, po_log_id: str, items: list[dict]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_notifications.py

class TestSendWhatsAppMediaUrl:
    def test_includes_media_url_in_request_when_provided(self, monkeypatch):
        from backend.notifications import whatsapp as wa_mod

        monkeypatch.setattr(wa_mod.settings, "twilio_account_sid", "ACtest")
        monkeypatch.setattr(wa_mod.settings, "twilio_auth_token", "token")
        monkeypatch.setattr(wa_mod.settings, "twilio_whatsapp_from", "whatsapp:+10000000000")

        captured = {}

        class FakeResponse:
            def raise_for_status(self): pass

        def fake_post(url, auth=None, data=None, timeout=None):
            captured["data"] = data
            return FakeResponse()

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        result = wa_mod.send_whatsapp("+15551234567", "Nueva orden de compra", media_url="https://example.com/po.pdf")
        assert result is True
        assert captured["data"]["MediaUrl"] == "https://example.com/po.pdf"

    def test_omits_media_url_key_when_not_provided(self, monkeypatch):
        from backend.notifications import whatsapp as wa_mod

        monkeypatch.setattr(wa_mod.settings, "twilio_account_sid", "ACtest")
        monkeypatch.setattr(wa_mod.settings, "twilio_auth_token", "token")
        monkeypatch.setattr(wa_mod.settings, "twilio_whatsapp_from", "whatsapp:+10000000000")

        captured = {}

        class FakeResponse:
            def raise_for_status(self): pass

        def fake_post(url, auth=None, data=None, timeout=None):
            captured["data"] = data
            return FakeResponse()

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        wa_mod.send_whatsapp("+15551234567", "hello")
        assert "MediaUrl" not in captured["data"]


class TestBuildPOSupplierText:
    def test_includes_supplier_name_and_sku_count(self):
        from backend.notifications.whatsapp import build_po_supplier_text

        items = [
            {"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "cantidad_final": 312.0},
            {"sku": "SKU-002", "display_name": "Arroz 5kg", "cantidad_final": 475.0},
        ]
        text = build_po_supplier_text("Distribuidora Andina", "po_abc123", items)
        assert "Distribuidora Andina" in text
        assert "2" in text  # sku count somewhere in the message
        assert "Aceite de Oliva 1L" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_notifications.py::TestSendWhatsAppMediaUrl tests/test_notifications.py::TestBuildPOSupplierText -v`
Expected: FAIL — `send_whatsapp` doesn't accept `media_url`, `build_po_supplier_text` doesn't exist.

- [ ] **Step 3: Add MediaUrl support**

Replace `send_whatsapp` in `backend/notifications/whatsapp.py`:

```python
def send_whatsapp(to_number: str, body: str, media_url: str | None = None) -> bool:
    """
    Send a WhatsApp text (optionally with a media attachment, e.g. a PDF URL
    Twilio will fetch and deliver) to +E164 number. Returns True on success.
    Never raises — alerting must not break the caller's loop.
    """
    if not is_configured():
        log.warning("Twilio not configured — WhatsApp not sent to %s", to_number)
        return False
    if not to_number:
        return False

    try:
        import httpx

        sid = settings.twilio_account_sid
        data = {
            "From": settings.twilio_whatsapp_from,
            "To": f"whatsapp:{to_number}",
            "Body": body,
        }
        if media_url:
            data["MediaUrl"] = media_url

        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, settings.twilio_auth_token),
            data=data,
            timeout=15,
        )
        resp.raise_for_status()
        log.info("WhatsApp sent → %s", to_number)
        return True
    except Exception as exc:
        log.error("WhatsApp send failed to %s: %s", to_number, exc)
        return False
```

- [ ] **Step 4: Add the supplier-facing message builder**

Append to `backend/notifications/whatsapp.py`:

```python
def build_po_supplier_text(supplier_name: str, po_log_id: str, items: list[dict]) -> str:
    """Short WhatsApp message accompanying a PO PDF sent to a supplier."""
    n = len(items)
    lines = [
        f"📦 *Nueva orden de compra* para {supplier_name}",
        f"{n} producto{'s' if n != 1 else ''}:",
    ]
    for i in items[:10]:
        qty = i.get("cantidad_final") or 0
        lines.append(f"  • {i.get('display_name') or i.get('sku')} — {qty:,.0f}")
    if n > 10:
        lines.append(f"  … y {n - 10} más")
    lines.append(f"\nDetalle completo en el PDF adjunto. Referencia: {po_log_id}")
    return "\n".join(lines)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_notifications.py -v`
Expected: all passing (5 total: 2 from Task 3, 3 from this task)

- [ ] **Step 6: Confirm the existing daily-alert WhatsApp callers still work (backward compatibility)**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/ -k "alert" -v`
Expected: all passing — `run_daily_inventory_alerts`/`send_alert_now` call `send_whatsapp(number, text)` with no `media_url`, which must still work unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/notifications/whatsapp.py backend/tests/test_notifications.py
git commit -m "feat(notifications): WhatsApp MediaUrl support + PO supplier message"
```

---

### Task 5: Public PDF-serving route + `POST /inventory/po/{po_log_id}/send` orchestration endpoint

**Files:**
- Modify: `backend/api/v1/inventory.py`
- Test: `backend/tests/test_po_send.py`

**Interfaces:**
- Consumes: `backend.inventory.reception_service.get_po`/`get_po_items` (existing), `backend.inventory.supplier_service.get_supplier_by_name` (Task 1), `backend.inventory.po_pdf.generate_po_pdf`/`slugify_supplier_name` (Task 2), `backend.notifications.email.send_po_to_supplier_email` (Task 3), `backend.notifications.whatsapp.send_whatsapp`/`build_po_supplier_text` (Task 4).
- Produces: `GET /inventory/po/{po_log_id}/pdf/{supplier_slug}` (unauthenticated), `POST /inventory/po/{po_log_id}/send` (analyst+) returning `{"sent": [{"supplier": str, "email": bool, "whatsapp": bool}], "skipped": [{"supplier": str | None, "reason": str}]}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_po_send.py
from uuid import uuid4


def _sku():
    return f"SEND_{uuid4().hex[:8]}"


class TestSendPOEndpoint:
    def test_viewer_denied(self, client, viewer_headers, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        sku = _sku()
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 10, "status": "approved", "proveedor": "Acme",
        }])
        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=viewer_headers)
        assert resp.status_code == 403

    def test_sends_email_and_whatsapp_when_supplier_has_both(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        sku = _sku()
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {
            "name": supplier_name, "email": "ventas@proveedor.com", "whatsapp": "+15551234567",
        })
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 20, "costo_unitario": 3.0,
            "status": "approved", "proveedor": supplier_name,
        }])

        email_calls = []
        wa_calls = []
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                            lambda **kw: email_calls.append(kw) or True)
        monkeypatch.setattr(wa_mod, "send_whatsapp",
                            lambda *a, **kw: wa_calls.append((a, kw)) or True)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["sent"]) == 1
        assert data["sent"][0]["supplier"] == supplier_name
        assert data["sent"][0]["email"] is True
        assert data["sent"][0]["whatsapp"] is True
        assert data["skipped"] == []
        assert len(email_calls) == 1
        assert len(wa_calls) == 1

    def test_skips_supplier_with_no_contact_info_on_file(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        sku = _sku()
        unknown_supplier = f"Proveedor Desconocido {uuid4().hex[:6]}"
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 5, "status": "approved", "proveedor": unknown_supplier,
        }])

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sent"] == []
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["supplier"] == unknown_supplier

    def test_pdf_endpoint_is_reachable_without_auth(self, client, auth_headers, test_tenant, monkeypatch):
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        sku = _sku()
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 8, "costo_unitario": 2.0,
            "status": "approved", "proveedor": supplier_name,
        }])
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email", lambda **kw: True)
        monkeypatch.setattr(wa_mod, "send_whatsapp", lambda *a, **kw: True)

        send_resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert send_resp.status_code == 200

        from backend.inventory.po_pdf import slugify_supplier_name
        slug = slugify_supplier_name(supplier_name)
        # No Authorization header — this route must be publicly reachable
        # (Twilio's MediaUrl fetch can't carry our Bearer token).
        pdf_resp = client.get(f"/api/v1/inventory/po/{po['id']}/pdf/{slug}")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_po_send.py -v`
Expected: FAIL with 404 (routes don't exist yet)

- [ ] **Step 3: Write the implementation**

Add to the imports near the top of `backend/api/v1/inventory.py` (alongside the existing `from backend.inventory import warehouse_service as wh_svc` / `optimizer_service as opt_svc` lines):

```python
from backend.inventory import supplier_service as sup_svc
from backend.inventory import po_pdf
```

(If `sup_svc` is already imported under a different alias, reuse that alias instead of re-importing.)

Add near the `receive_po`/`supplier_scorecard` routes (after `supplier_scorecard`, before the `# ── Suppliers ──` section):

```python
# ── PO → supplier (feature 2.2) ──────────────────────────────────────────────

@router.get("/po/{po_log_id}/pdf/{supplier_slug}")
def download_po_pdf(po_log_id: str, supplier_slug: str):
    """
    Serves a generated PO PDF by (po_log_id, supplier_slug) — INTENTIONALLY
    unauthenticated. Twilio's WhatsApp MediaUrl fetch cannot carry this app's
    Bearer token, and this endpoint is the only way to deliver a PO PDF via
    WhatsApp. po_log_id is an unguessable id; this serves nothing more
    sensitive than what's already emailed to the same supplier.
    """
    from backend.storage import paths as storage_paths

    # po_log_id is not tenant-scoped here on purpose (see docstring) — we
    # don't have a tenant to scope by without auth, so we search every
    # tenant's directory for a matching file. In practice this is a single
    # glob since po_log_id is unique. po_pdf_dir("") == _base()/"pos" (an
    # empty tenant_id segment is a no-op in pathlib's `/` join), giving the
    # root directory one level ABOVE each per-tenant pos/ subdirectory —
    # do not call .parent on this, that would search one level too high.
    pos_root = storage_paths.po_pdf_dir("")
    for candidate in pos_root.glob(f"*/{po_log_id}_{supplier_slug}.pdf"):
        return FileResponse(candidate, media_type="application/pdf", filename=candidate.name)
    raise HTTPException(status_code=404, detail="PO PDF not found")


@router.post("/po/{po_log_id}/send", status_code=200)
def send_po_to_suppliers(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Sends a PO's PDF to each of its suppliers by email and WhatsApp,
    grouping the PO's lines by supplier name (a PO can span more than one
    supplier). Lines with no supplier name, or whose supplier has no
    saved contact info, are skipped and reported back — never a 500.
    """
    from backend.inventory import reception_service as rec_svc
    from backend.notifications import email as email_mod
    from backend.notifications import whatsapp as wa_mod

    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    items = rec_svc.get_po_items(user.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]

    by_supplier: dict[str, list[dict]] = {}
    for i in ordered:
        name = (i.get("proveedor") or "").strip()
        if not name:
            continue
        by_supplier.setdefault(name, []).append(i)

    sent: list[dict] = []
    skipped: list[dict] = []
    po_meta = {
        "generated_at": po["generated_at"].isoformat() if po.get("generated_at") else None,
        "po_log_id": po_log_id,
    }

    for supplier_name, supplier_items in by_supplier.items():
        supplier = sup_svc.get_supplier_by_name(user.tenant_id, supplier_name)
        if not supplier or not (supplier.get("email") or supplier.get("whatsapp")):
            skipped.append({"supplier": supplier_name, "reason": "Sin datos de contacto en la ficha del proveedor"})
            continue

        pdf_path = po_pdf.generate_po_pdf(user.tenant_id, po_log_id, supplier_name, supplier_items, po_meta)
        pdf_bytes = pdf_path.read_bytes()
        slug = po_pdf.slugify_supplier_name(supplier_name)

        email_ok = False
        if supplier.get("email"):
            email_ok = email_mod.send_po_to_supplier_email(
                to=supplier["email"], supplier_name=supplier_name, po_log_id=po_log_id,
                items=supplier_items, pdf_bytes=pdf_bytes, pdf_filename=pdf_path.name,
            )

        whatsapp_ok = False
        if supplier.get("whatsapp"):
            media_url = f"{settings.frontend_url}/api/v1/inventory/po/{po_log_id}/pdf/{slug}"
            text = wa_mod.build_po_supplier_text(supplier_name, po_log_id, supplier_items)
            whatsapp_ok = wa_mod.send_whatsapp(supplier["whatsapp"], text, media_url=media_url)

        sent.append({"supplier": supplier_name, "email": email_ok, "whatsapp": whatsapp_ok})

    return ok({"sent": sent, "skipped": skipped})
```

Also add `from backend.config import settings` near the top of `inventory.py` if not already imported (check first — many files already import this).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_po_send.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full backend regression**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: no new failures vs. the pre-task baseline

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_po_send.py
git commit -m "feat(inventory): POST /inventory/po/{id}/send + public PDF route"
```

---

### Task 6: Frontend API client + types

**Files:**
- Modify: `Frontend/src/lib/types.ts`
- Modify: `Frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: the response shape from Task 5's `POST /inventory/po/{po_log_id}/send`.
- Produces: `SendPOResult` type, `sendPOToSuppliers(poLogId: string): Promise<SendPOResult>`.

- [ ] **Step 1: Add the type**

In `Frontend/src/lib/types.ts`, add near `POItemsResponse`/`ReceptionResult` (around line 794-803):

```typescript
export interface SendPOResult {
  sent:    { supplier: string; email: boolean; whatsapp: boolean }[]
  skipped: { supplier: string | null; reason: string }[]
}
```

- [ ] **Step 2: Add the API client function**

In `Frontend/src/lib/api.ts`, add right after `receivePO` (around line 659):

```typescript
export const sendPOToSuppliers = (poLogId: string) =>
  request<import('./types').SendPOResult>('POST', `/inventory/po/${poLogId}/send`)
```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(frontend): API client for sending a PO to its suppliers"
```

---

### Task 7: "Enviar pedido" button in `POHistoryTable`

**Files:**
- Modify: `Frontend/src/components/po/POHistory.tsx`
- Modify: `Frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: `sendPOToSuppliers` and `SendPOResult` (Task 6).
- Produces: nothing consumed by later tasks — this is the final task.

- [ ] **Step 1: Add i18n keys**

In `Frontend/src/i18n/translations.ts`, find the Spanish block's `roi.*` keys (search for `'roi.col_datetime'`) and add nearby:

```typescript
    'roi.send_po':              'Enviar pedido',
    'roi.send_po_confirm':      '¿Confirmar envío?',
    'roi.send_po_sending':      'Enviando…',
    'roi.send_po_success':      'Pedido enviado',
    'roi.send_po_partial':      'Enviado parcialmente — revisa los proveedores omitidos',
    'roi.send_po_none_sent':    'No se pudo enviar — ningún proveedor tiene datos de contacto',
    'roi.send_po_error':        'Error al enviar el pedido',
```

Find the matching English block (search for `'roi.col_datetime'` a second time) and add:

```typescript
    'roi.send_po':              'Send order',
    'roi.send_po_confirm':      'Confirm send?',
    'roi.send_po_sending':      'Sending…',
    'roi.send_po_success':      'Order sent',
    'roi.send_po_partial':      'Partially sent — check skipped suppliers',
    'roi.send_po_none_sent':    'Could not send — no supplier has contact info on file',
    'roi.send_po_error':        'Error sending the order',
```

- [ ] **Step 2: Add the button with inline two-step confirm**

In `Frontend/src/components/po/POHistory.tsx`, add `Send` to the lucide-react import (line 6):

```typescript
import { Truck, X, Send } from 'lucide-react'
```

Add `sendPOToSuppliers` to the import from `@/lib/api` (line 3):

```typescript
import { getPOItems, receivePO, sendPOToSuppliers } from '@/lib/api'
```

Add a new component right before `POHistoryTable` (after `ReceptionModal`'s closing, around line 200):

```typescript
function SendPOButton({ poLogId }: { poLogId: string }) {
  const { t } = useLanguage()
  const [state, setState] = useState<'idle' | 'confirm' | 'sending' | 'done'>('idle')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  useEffect(() => {
    if (state !== 'confirm') return
    const timer = setTimeout(() => setState('idle'), 4000)
    return () => clearTimeout(timer)
  }, [state])

  async function handleConfirm() {
    setState('sending')
    try {
      const res = await sendPOToSuppliers(poLogId)
      const anySent = res.sent.length > 0
      const anySkipped = res.skipped.length > 0
      const message = !anySent
        ? t('roi.send_po_none_sent')
        : anySkipped ? t('roi.send_po_partial') : t('roi.send_po_success')
      setResult({ ok: anySent, message })
    } catch (e: unknown) {
      setResult({ ok: false, message: e instanceof Error ? e.message : t('roi.send_po_error') })
    } finally {
      setState('done')
    }
  }

  if (state === 'done' && result) {
    return (
      <span style={{ fontSize: 11, color: result.ok ? C.green : C.red, fontWeight: 600 }}>
        {result.message}
      </span>
    )
  }

  return (
    <button
      onClick={() => (state === 'confirm' ? handleConfirm() : setState('confirm'))}
      disabled={state === 'sending'}
      style={{
        all: 'unset', cursor: state === 'sending' ? 'not-allowed' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
        border: `1px solid ${state === 'confirm' ? C.indigo : C.border}`,
        color: state === 'confirm' ? C.indigo : C.text,
      }}
    >
      <Send size={11} />
      {state === 'sending' ? t('roi.send_po_sending') : state === 'confirm' ? t('roi.send_po_confirm') : t('roi.send_po')}
    </button>
  )
}
```

- [ ] **Step 3: Render it next to "Registrar llegada"**

In `POHistoryTable`'s reception-status cell (the `(() => { ... })()` IIFE around line 274-303), add the new button inside the same `<span>` that already holds the badge and the receive button:

```typescript
              <td style={{ padding: '11px 14px', whiteSpace: 'nowrap' }}>
                {(() => {
                  const status = entry.reception_status || 'pending'
                  const badge = RECEPTION_LABEL[status] || RECEPTION_LABEL.pending
                  const receivable = status === 'pending' || status === 'partial'
                  return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: badge.bg, color: badge.color,
                      }}>
                        {badge.label}
                      </span>
                      {receivable && (
                        <button
                          onClick={() => onReceive(entry.id)}
                          style={{
                            all: 'unset', cursor: 'pointer',
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
                            border: `1px solid ${C.border}`, color: C.text,
                          }}
                        >
                          <Truck size={11} /> Registrar llegada
                        </button>
                      )}
                      <SendPOButton poLogId={entry.id} />
                    </span>
                  )
                })()}
              </td>
```

- [ ] **Step 4: Typecheck and manual verification**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

Manual check (dev server must already be running per `CLAUDE.md`'s run instructions — do not restart it unless it's already stale from a prior code change):
1. Log in, navigate to `/pedidos` (or `/inventory/roi` if that's where `POHistoryTable` also renders — check both).
2. Confirm the "Enviar pedido" button appears next to "Registrar llegada" (if the PO is still receivable) on each history row.
3. Click it once — confirm it changes to "¿Confirmar envío?" and reverts to "Enviar pedido" after ~4s if not clicked again.
4. Click again within the window — confirm it shows "Enviando…" then a result message (since no supplier has real contact info in the demo dataset by default, expect "No se pudo enviar — ningún proveedor tiene datos de contacto" unless you've added a supplier with an email/whatsapp for the PO's SKUs first via `/inventory/suppliers`).

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/components/po/POHistory.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(pedidos): Enviar pedido button with inline confirm"
```

---

## Final Regression

After all 7 tasks:

```bash
cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/ -q
cd Frontend && npx tsc --noEmit
```

Both must be clean before this branch is considered ready for a final whole-branch review.
