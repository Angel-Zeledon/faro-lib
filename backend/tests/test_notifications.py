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
