# backend/tests/test_po_pdf.py
from uuid import uuid4


# `client` is requested only for the DB pool it opens: the document now resolves
# the tenant's own currency setting, so generating one is no longer offline.
# (What it renders per currency is asserted in test_currency_reaches_backend_strings.py.)
def test_generate_po_pdf_writes_a_real_pdf_file(client, tmp_path, monkeypatch):
    from backend.storage import paths
    monkeypatch.setattr(paths, "_base", lambda: tmp_path)

    from backend.inventory.po_pdf import generate_po_pdf

    tid = f"tenant_{uuid4().hex[:8]}"
    po_log_id = f"po_{uuid4().hex[:8]}"
    items = [
        {"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "supplier": "Distribuidora Andina",
         "final_qty": 312.0, "unit_cost": 8.5},
        {"sku": "SKU-002", "display_name": "Arroz 5kg", "supplier": "Distribuidora Andina",
         "final_qty": 475.0, "unit_cost": 5.2},
    ]
    po_meta = {"generated_at": "2026-07-18T10:00:00", "po_log_id": po_log_id}

    path = generate_po_pdf(tid, po_log_id, "Distribuidora Andina", items, po_meta)

    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    # A real PDF file always starts with this magic header.
    assert path.read_bytes()[:5] == b"%PDF-"
