"""Daily alert mentions network transfer suggestions (feature 5.4, spec §2)."""

from backend.notifications.whatsapp import build_inventory_alert_text


class TestAlertTransferCopy:
    def test_transfer_line_present_when_count_positive(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy",
                                          transfer_count=2)
        assert "🔁 2 productos se resuelven moviendo stock" in text
        # URL stays the last line
        assert text.strip().splitlines()[-1].endswith("http://x/hoy")

    def test_singular_form(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy",
                                          transfer_count=1)
        assert "🔁 1 producto se resuelve moviendo stock" in text

    def test_absent_when_zero(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy")
        assert "🔁" not in text
