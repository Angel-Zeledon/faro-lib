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

        items = [{"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "final_qty": 312.0, "unit_cost": 8.5}]
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
            def json(self): return {"sid": "SMtest"}

        def fake_post(url, auth=None, data=None, timeout=None):
            captured["data"] = data
            return FakeResponse()

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        # Targets _transport_send: conftest patches the `_send` wrapper
        # session-wide because the local .env holds real Twilio credentials.
        wa_mod._transport_send("+15551234567", "Nueva orden de compra", "https://example.com/po.pdf")
        assert captured["data"]["MediaUrl"] == "https://example.com/po.pdf"

    def test_omits_media_url_key_when_not_provided(self, monkeypatch):
        from backend.notifications import whatsapp as wa_mod

        monkeypatch.setattr(wa_mod.settings, "twilio_account_sid", "ACtest")
        monkeypatch.setattr(wa_mod.settings, "twilio_auth_token", "token")
        monkeypatch.setattr(wa_mod.settings, "twilio_whatsapp_from", "whatsapp:+10000000000")

        captured = {}

        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return {"sid": "SMtest"}

        def fake_post(url, auth=None, data=None, timeout=None):
            captured["data"] = data
            return FakeResponse()

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        wa_mod._transport_send("+15551234567", "hello", None)
        assert "MediaUrl" not in captured["data"]


class TestBuildPOSupplierText:
    def test_includes_supplier_name_and_sku_count(self):
        from backend.notifications.whatsapp import build_po_supplier_text

        items = [
            {"sku": "SKU-001", "display_name": "Aceite de Oliva 1L", "final_qty": 312.0},
            {"sku": "SKU-002", "display_name": "Arroz 5kg", "final_qty": 475.0},
        ]
        text = build_po_supplier_text("Distribuidora Andina", "po_abc123", items)
        assert "Distribuidora Andina" in text
        assert "2" in text  # sku count somewhere in the message
        assert "Aceite de Oliva 1L" in text


# ── The language mandate for backend-only channels ───────────────────────────
# Email and WhatsApp are never rendered by the frontend, so their Spanish lives
# in `notifications.locale` behind English keys. These tests do not check that a
# key exists — they render a real message and prove the words in it came from
# the catalog, which is what a literal pasted back into a template would break.

import ast
import pathlib
import re

import pytest

from backend.notifications import email as email_mod
from backend.notifications import locale as locale_mod
from backend.notifications import whatsapp as wa_mod
from backend.notifications.locale import render_es

_SENTINEL = "CATALOG-SENTINEL-7f3a"


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Capture at `_send`, the boundary conftest already owns."""
    captured: list[dict] = []
    monkeypatch.setattr(
        email_mod, "_send",
        lambda to, subject, html, attachment=None: captured.append(
            {"to": to, "subject": subject, "html": html, "attachment": attachment}),
    )
    return captured


def _items(n: int) -> list[dict]:
    return [
        {"sku": f"SKU-{i:03d}", "display_name": f"Producto {i}", "coverage_days": 2.0,
         "recommended_qty": 100.0, "supplier": "Andina", "final_qty": 100.0}
        for i in range(n)
    ]


class TestInventoryAlertEmailCopyComesFromTheCatalog:
    def test_summary_subject_and_columns_render_the_catalog_sentences(self, sent):
        assert email_mod.send_inventory_alert_email(
            "buyer@faro-e2e.io", _items(3), _items(1), "https://faro.test/hoy") is True

        msg = sent[0]
        assert msg["subject"] == render_es("alert_email_subject_critical", n=3, s="s")
        assert msg["subject"] == "🔴 3 SKUs en riesgo de stockout"
        html = msg["html"]
        assert render_es("alert_email_summary_critical", n=3, s="s") in html
        assert "3 productos en riesgo inmediato de stockout." in html
        assert render_es("alert_email_summary_warning", n=1, s="") in html
        for key in ("alert_email_col_signal", "alert_email_col_coverage",
                    "alert_email_col_supplier", "alert_email_badge_critical",
                    "alert_email_cta", "alert_email_footer"):
            assert render_es(key) in html, key
        assert render_es("alert_email_coverage_days", days="2") in html

    def test_warning_only_digest_uses_the_warning_subject(self, sent):
        email_mod.send_inventory_alert_email(
            "buyer@faro-e2e.io", [], _items(4), "https://faro.test/hoy")
        assert sent[0]["subject"] == render_es("alert_email_subject_warning", n=4, s="s")

    def test_editing_the_catalog_changes_the_email(self, sent, monkeypatch):
        """A Spanish literal pasted back into the template would ignore this."""
        monkeypatch.setitem(locale_mod._ES, "alert_email_footer", _SENTINEL)
        monkeypatch.setitem(locale_mod._ES, "alert_email_cta", _SENTINEL + "-cta")

        email_mod.send_inventory_alert_email(
            "buyer@faro-e2e.io", _items(1), [], "https://faro.test/hoy")

        html = sent[0]["html"]
        assert _SENTINEL in html
        assert _SENTINEL + "-cta" in html
        assert "Esta alerta se genera automáticamente" not in html


class TestLeadTimeAlertEmailCopyComesFromTheCatalog:
    def _deviation(self) -> dict:
        return {"supplier": "Andina", "severidad": "alta", "lead_time_reciente": 21,
                "lead_time_historico": 7, "deviation_days": 14,
                "n_reciente": 3, "n_baseline": 18}

    def test_plural_body_and_subject_agree_with_the_count(self, sent):
        email_mod.send_supplier_lead_time_alert_email(
            "buyer@faro-e2e.io", [self._deviation(), self._deviation()],
            "https://faro.test/proveedores")

        msg = sent[0]
        assert msg["subject"] == render_es("lead_time_email_subject", n=2, s="es")
        assert msg["subject"] == "⏱️ 2 proveedores tardando más de lo habitual"
        assert render_es("lead_time_email_body_many", n=2) in msg["html"]
        assert "2 proveedores se han desviado" in msg["html"]

    def test_singular_body_and_subject_agree_with_the_count(self, sent):
        email_mod.send_supplier_lead_time_alert_email(
            "buyer@faro-e2e.io", [self._deviation()], "https://faro.test/proveedores")

        msg = sent[0]
        assert msg["subject"] == "⏱️ 1 proveedor tardando más de lo habitual"
        assert render_es("lead_time_email_body_one", n=1) in msg["html"]
        assert "1 proveedor se ha desviado" in msg["html"]
        assert "se han desviado" not in msg["html"]

    def test_row_units_and_headers_render_the_catalog(self, sent):
        email_mod.send_supplier_lead_time_alert_email(
            "buyer@faro-e2e.io", [self._deviation()], "https://faro.test/proveedores")

        html = sent[0]["html"]
        assert render_es("lead_time_email_days", days=21) in html
        assert render_es("lead_time_email_deviation_days", days=14) in html
        assert render_es("lead_time_email_receptions_ratio", recent=3, baseline=18) in html
        assert "3 de 18" in html
        for key in ("lead_time_email_col_historical", "lead_time_email_col_deviation",
                    "lead_time_email_cta", "lead_time_email_footer"):
            assert render_es(key) in html, key


class TestAuthEmailCopyComesFromTheCatalog:
    def test_change_password_code_email(self, sent, monkeypatch):
        monkeypatch.setitem(locale_mod._ES, "change_password_email_heading", _SENTINEL)
        assert email_mod.send_change_password_code("user@faro-e2e.io", "482913") is True

        msg = sent[0]
        assert msg["subject"] == render_es("change_password_email_subject")
        assert "482913" in msg["html"]
        assert _SENTINEL in msg["html"], "the heading is not read from the catalog"
        assert render_es("change_password_email_intro", app="ForecastPlatform") in msg["html"]
        # The TTL number stays in code, the unit word comes from the catalog.
        assert render_es("hours_duration", hours=30) in msg["html"]
        assert "<strong" in msg["html"], "emphasis markup must stay in the module"

    def test_password_reset_otp_email(self, sent):
        email_mod.send_password_reset_otp("user@faro-e2e.io", "112233")
        msg = sent[0]
        assert msg["subject"] == render_es("password_reset_otp_email_subject")
        assert render_es("password_reset_otp_email_heading") in msg["html"]
        assert render_es("password_reset_otp_email_expiry",
                         duration=email_mod._strong(render_es("hours_duration", hours=30))
                         ) in msg["html"]

    def test_account_setup_email_interpolates_name_and_app(self, sent):
        email_mod.send_account_setup_email(
            "nuevo@faro-e2e.io", "Ana Rojas", "https://faro.test/setup?t=x")
        msg = sent[0]
        assert msg["subject"] == render_es("account_setup_email_subject", app="ForecastPlatform")
        assert msg["subject"] == "Activa tu cuenta en ForecastPlatform"
        assert render_es("account_setup_email_heading",
                         app="ForecastPlatform", name="Ana Rojas") in msg["html"]
        assert render_es("account_setup_email_cta") in msg["html"]


class TestPurchaseOrderEmailCopyComesFromTheCatalog:
    def test_subject_body_and_reference_render_the_catalog(self, sent):
        email_mod.send_po_to_supplier_email(
            to="ventas@faro-e2e.io", supplier_name="Distribuidora Andina",
            po_log_id="po_abc123", items=_items(2),
            pdf_bytes=b"%PDF-1.4", pdf_filename="po_abc123.pdf", po_ref="OC-0007",
        )
        msg = sent[0]
        assert msg["subject"] == render_es("po_email_subject", reference="OC-0007")
        assert msg["subject"] == "Orden de compra OC-0007"
        assert render_es("po_email_body", supplier="Distribuidora Andina") in msg["html"]
        assert render_es("po_email_reference", reference="OC-0007") in msg["html"]
        assert render_es("po_email_col_qty") in msg["html"]


class TestMonthlyRecapCopyComesFromTheCatalog:
    _REPORT = {
        "month": "2026-06", "adoption_rate": 0.75,
        "recommendations_followed": 6, "recommendations_shown": 8,
        "stockout_risks_handled": 3, "capital_freed": 1250000.0,
        "managed_purchase_value": 890000.0,
    }

    def test_month_label_is_built_from_the_catalog(self, monkeypatch):
        assert email_mod._month_label("2026-06") == "junio de 2026"
        monkeypatch.setitem(locale_mod._ES, "month_name_june", _SENTINEL)
        assert email_mod._month_label("2026-06") == f"{_SENTINEL} de 2026"

    def test_every_month_resolves_to_a_catalog_name(self):
        labels = [email_mod._month_label(f"2026-{m:02d}") for m in range(1, 13)]
        assert len(set(labels)) == 12
        assert labels[0] == "enero de 2026" and labels[11] == "diciembre de 2026"

    def test_subject_headline_and_tiles_render_the_catalog(self, sent):
        email_mod.send_monthly_roi_email("buyer@faro-e2e.io", dict(self._REPORT),
                                         "https://faro.test/roi")
        msg = sent[0]
        amount = email_mod._fmt_crc(1250000.0)
        assert msg["subject"] == render_es("roi_email_subject_capital",
                                           month="junio de 2026", amount=amount)
        assert msg["subject"] == "Faro — liberaste ₡1.250.000 en junio de 2026"
        html = msg["html"]
        assert render_es("roi_email_headline_capital",
                         month="junio de 2026", amount=amount) in html
        assert render_es("roi_email_metric_adoption_note", followed=6, shown=8) in html
        assert "Seguiste 6 de 8 líneas" in html
        for key in ("roi_email_metric_risks_label", "roi_email_metric_capital_note",
                    "roi_email_metric_purchases_label", "roi_email_cta", "roi_email_footer"):
            assert render_es(key) in html, key

    def test_subject_without_capital_uses_the_default_catalog_entry(self, sent):
        report = dict(self._REPORT, capital_freed=None)
        email_mod.send_monthly_roi_email("buyer@faro-e2e.io", report, "https://faro.test/roi")
        assert sent[0]["subject"] == render_es("roi_email_subject_default",
                                               month="junio de 2026")
        assert "liberaste" not in sent[0]["subject"]


class TestWhatsAppAlertCopyComesFromTheCatalog:
    def test_plural_lines_agree_with_the_counts(self):
        text = wa_mod.build_inventory_alert_text(
            _items(7), _items(12), "https://faro.test/hoy", transfer_count=2)
        assert render_es("alert_whatsapp_critical_many", n=7) in text
        assert "7 productos se agotan" in text
        assert render_es("alert_whatsapp_warning", n=12) in text
        assert render_es("alert_whatsapp_transfer_many", n=2) in text
        assert render_es("alert_whatsapp_more", n=2) in text  # 7 - 5 listed
        assert render_es("alert_whatsapp_cta", url="https://faro.test/hoy") in text
        assert render_es("alert_whatsapp_order_qty", qty="100") in text

    def test_singular_lines_agree_with_the_counts(self):
        text = wa_mod.build_inventory_alert_text(
            _items(1), [], "https://faro.test/hoy", transfer_count=1)
        assert render_es("alert_whatsapp_critical_one", n=1) in text
        assert "1 producto se agota antes" in text
        assert "se agotan" not in text
        assert render_es("alert_whatsapp_transfer_one", n=1) in text
        assert "se resuelven" not in text

    def test_editing_the_catalog_changes_the_message(self, monkeypatch):
        monkeypatch.setitem(locale_mod._ES, "alert_whatsapp_warning", _SENTINEL + " {n}")
        text = wa_mod.build_inventory_alert_text([], _items(3), "https://faro.test/hoy")
        assert f"{_SENTINEL} 3" in text
        assert "por reabastecer" not in text


# Spanish letters, plus function words that only appear in Spanish prose. A key
# name or a CSS fragment is a single underscore/colon-joined token, so the word
# boundaries keep them out.
_SPANISH_PROSE = re.compile(
    r"[áéíóúüñÁÉÍÓÚÑ¿¡]"
    r"|\b(?:de|del|la|el|los|las|un|una|unos|unas|que|tu|tus|para|con|por|se|"
    r"su|sus|en|al|lo|producto|productos|proveedor|proveedores|pedir|orden|"
    r"resumen|mes|horas|correo|cuenta)\b",
    re.IGNORECASE,
)


def _non_docstring_literals(module) -> list[tuple[int, str]]:
    """Every string literal in `module` except docstrings (f-string parts included)."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and ast.get_docstring(node, clean=False) is not None
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class TestNoSpanishLiteralsLeftInTheSendingModules:
    """CLAUDE.md: backend-only channels keep their Spanish in the locale catalog."""

    def test_the_detector_recognises_the_copy_it_is_guarding(self):
        """Without this the scan below could pass by detecting nothing at all."""
        for key in ("alert_email_footer", "lead_time_email_body_one",
                    "po_email_body", "roi_email_headline_default",
                    "alert_whatsapp_critical_many", "account_setup_email_intro"):
            assert _SPANISH_PROSE.search(locale_mod._ES[key]), key

    @pytest.mark.parametrize("module", [email_mod, wa_mod])
    def test_templates_hold_no_spanish_prose(self, module):
        offenders = [
            (line, text) for line, text in _non_docstring_literals(module)
            if _SPANISH_PROSE.search(text)
        ]
        assert not offenders, (
            f"Spanish copy hardcoded in {pathlib.Path(module.__file__).name}: "
            f"{offenders} — move it to notifications/locale.py"
        )

    @pytest.mark.parametrize("module", [email_mod, wa_mod])
    def test_every_render_es_key_used_exists_in_the_catalog(self, module):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        keys = set(re.findall(r'render_es\(\s*"([a-z0-9_]+)"', source))
        assert keys, "no catalog lookups found — did the module stop using render_es?"
        assert keys <= set(locale_mod._ES), sorted(keys - set(locale_mod._ES))
