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
