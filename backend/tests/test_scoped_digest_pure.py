"""Pure checks of the warehouse-scoped digest: which rows a scoped recipient's
digest keeps and what the rendered message says. No database: the scope is
preloaded on the CurrentUser (`scope_cache`), exactly where
`warehouse_scope.scope_names` caches it per request.
"""

from backend.auth.guards import CurrentUser
from backend.notifications import email as email_mod
from backend.notifications import scoped_digest as sd
from backend.notifications import whatsapp as wa_mod


def _user(names):
    u = CurrentUser("u1", "t1", "analyst")
    u.scope_cache = None if names is None else frozenset(names)
    return u


def _row(sku, wh, signal="PEDIR_YA", **extra):
    return {"sku": sku, "display_name": f"Name {sku}", "warehouse": wh, "signal": signal,
            "coverage_days": 2.0, "recommended_qty": 10, "supplier": "Acme", **extra}


ROWS = [
    _row("N-1", "Norte"),
    _row("N-2", "Norte", "PEDIR_PRONTO"),
    _row("N-3", "Norte", "OK"),
    _row("S-1", "Sur"),
    _row("S-2", "Sur", "PEDIR_PRONTO"),
]


class TestScopeRestriction:
    def test_only_the_recipients_warehouses_are_kept(self):
        c = sd.scoped_alert_content(_user({"Norte"}), ROWS)
        assert c["warehouses"] == ["Norte"]
        assert [r["sku"] for r in c["critical"]] == ["N-1"]
        assert [r["sku"] for r in c["warning"]] == ["N-2"]

    def test_two_warehouses_keep_both_and_never_a_third(self):
        rows = ROWS + [_row("E-1", "Este")]
        c = sd.scoped_alert_content(_user({"Norte", "Sur"}), rows)
        assert c["warehouses"] == ["Norte", "Sur"]
        assert {r["sku"] for r in c["critical"]} == {"N-1", "S-1"}

    def test_empty_scope_reports_nothing(self):
        c = sd.scoped_alert_content(_user(set()), ROWS)
        assert c == {"warehouses": [], "critical": [], "warning": [], "transfer_count": 0}

    def test_scope_with_nothing_at_risk_is_empty(self):
        c = sd.scoped_alert_content(_user({"Oeste"}), ROWS + [_row("O-1", "Oeste", "OK")])
        assert c["critical"] == [] and c["warning"] == []

    def test_transfer_from_outside_the_scope_is_not_counted_or_named(self):
        rows = [
            _row("N-1", "Norte", recommended_action="transfer",
                 transfer_suggestion={"from_warehouse": "Sur", "qty": 5}),
            _row("N-2", "Norte", recommended_action="transfer",
                 transfer_suggestion={"from_warehouse": "Norte2", "qty": 5}),
        ]
        c = sd.scoped_alert_content(_user({"Norte", "Norte2"}), rows)
        # Only the transfer whose source is also theirs survives.
        assert c["transfer_count"] == 1
        c =sd.scoped_alert_content(_user({"Norte"}), rows)
        assert c["transfer_count"] == 0
        assert all(r["transfer_suggestion"] is None for r in c["critical"])
        assert "Sur" not in repr(c)

    def test_inputs_are_not_mutated(self):
        rows = [_row("N-1", "Norte", recommended_action="transfer",
                     transfer_suggestion={"from_warehouse": "Sur"})]
        sd.scoped_alert_content(_user({"Norte"}), rows)
        assert rows[0]["transfer_suggestion"] == {"from_warehouse": "Sur"}

    def test_label_names_are_sorted_and_deduplicated(self):
        assert sd.scope_label_names(["sur", "Norte", "Norte", ""]) == ["Norte", "sur"]
        assert sd.scope_label_names(None) == []


class TestFreshnessRestriction:
    FRESH = {"warehouses": {"items": [
        {"name": "Norte", "lagging": True, "silent_days": 20},
        {"name": "Sur", "lagging": True, "silent_days": 30},
        {"name": "Este", "lagging": False, "silent_days": 1},
    ]}}

    def test_only_own_silent_warehouses_are_named(self):
        c = sd.scoped_freshness_content(_user({"Norte", "Este"}), self.FRESH)
        assert c["silent"] == [{"name": "Norte", "days": 20}]
        assert "Sur" not in repr(c)

    def test_no_silent_warehouse_in_scope(self):
        assert sd.scoped_freshness_content(_user({"Este"}), self.FRESH)["silent"] == []


class TestRendering:
    def _capture(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(email_mod, "_send",
                            lambda to, subject, html, **kw: sent.update(subject=subject, html=html))
        return sent

    def test_scoped_email_names_the_warehouses_and_holds_only_their_rows(self, monkeypatch):
        sent = self._capture(monkeypatch)
        c = sd.scoped_alert_content(_user({"Norte"}), ROWS)
        assert email_mod.send_inventory_alert_email(
            to="a@b.c", critical_items=c["critical"], warning_items=c["warning"],
            inventory_url="http://x/hoy", scope_warehouses=c["warehouses"])
        assert "Norte" in sent["subject"]
        assert "Norte" in sent["html"]
        assert "N-1" in sent["html"] and "N-2" in sent["html"]
        assert "S-1" not in sent["html"] and "S-2" not in sent["html"]
        assert "Sur" not in sent["html"] and "Sur" not in sent["subject"]

    def test_company_email_is_unchanged_without_a_scope(self, monkeypatch):
        sent = self._capture(monkeypatch)
        email_mod.send_inventory_alert_email(
            to="a@b.c", critical_items=[_row("N-1", "Norte")], warning_items=[],
            inventory_url="http://x/hoy")
        assert "bodegas" not in sent["subject"]
        assert "Este resumen cubre solo" not in sent["html"]
        assert "Norte" not in sent["html"]  # no per-row warehouse tag either

    def test_warehouse_names_are_escaped(self, monkeypatch):
        sent = self._capture(monkeypatch)
        email_mod.send_inventory_alert_email(
            to="a@b.c", critical_items=[_row("N-1", "<b>x</b>")], warning_items=[],
            inventory_url="http://x/hoy", scope_warehouses=["<b>x</b>"])
        assert "<b>x</b>" not in sent["html"]

    def test_freshness_email_names_the_scope(self, monkeypatch):
        sent = self._capture(monkeypatch)
        email_mod.send_data_freshness_reminder_email(
            to="a@b.c", sales_age_days=None, stock_age_days=None, upload_url="http://x",
            silent_warehouses=[{"name": "Norte", "days": 20}], scope_warehouses=["Norte"])
        assert "Norte" in sent["subject"] and "Este resumen cubre solo" in sent["html"]

    def test_whatsapp_text_names_the_scope(self):
        text = wa_mod.build_inventory_alert_text(
            [_row("N-1", "Norte")], [], "http://x/hoy", scope_warehouses=["Norte"])
        assert "Norte" in text.splitlines()[0]
        plain = wa_mod.build_inventory_alert_text([_row("N-1", "Norte")], [], "http://x/hoy")
        assert "Solo tus bodegas" not in plain
