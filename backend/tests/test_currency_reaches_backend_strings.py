"""
The tenant currency setting must reach the strings the BACKEND composes.

It shipped working for every figure the FRONTEND renders and for none of the text
the backend writes itself: `formatting.money` hardcoded the colón, so a customer
who had chosen dollars still read ₡ in the executive summary, in the purchase-order
PDF and in the monthly recap email — precisely the three surfaces that LEAVE the
app, forwarded to a supplier or sitting in someone's inbox.

Each test here names the break it catches, because a test that cannot fail is not
a test:

  · `money()` ignoring the currency it was handed, or its declared precision.
  · A new hardcoded ₡ appearing anywhere in backend code — an audit, so code
    written tomorrow is covered the moment it exists.
  · `generate_po_pdf`, `generate_recommendations` or the morning narrative losing
    the thread from the `tenants.settings` row to the rendered string. These read
    the real document / the real endpoint, not the parameter they were passed.
  · The monthly recap email subject.
  · A non-admin being able to relabel a whole company's books.

The setting RELABELS, it never converts, so every assertion below checks the
symbol and the precision — never a different amount.
"""

import ast
import base64
import re
import sys
import zlib
from pathlib import Path

import pytest

from backend.api.v1.currency import DEFAULT_CODE, SUPPORTED, currency_of
from backend.db.connection import query_one
from backend.formatting import DEFAULT_CURRENCY, money

USD = {"code": "USD", **SUPPORTED["USD"]}
CRC = {"code": "CRC", **SUPPORTED["CRC"]}

_BACKEND = Path(__file__).resolve().parent.parent


# ── The formatter ─────────────────────────────────────────────────────────────

class TestMoneyFollowsTheCurrencyItIsGiven:
    def test_symbol_comes_from_the_currency_not_from_the_module(self):
        # Break to check: return f"{CURRENCY_SYMBOL}{...}" again -> both become ₡.
        assert money(1234.5, currency=USD) == "$1,234.50"
        assert money(1234.5, currency=CRC) == "₡1,234"

    def test_precision_defaults_to_the_currencys_own_declared_decimals(self):
        # USD declares 2 and CRC declares 0. The caller must not have to know.
        assert money(8.49, currency=USD) == "$8.49"
        assert money(8.49, currency=CRC) == "₡8"
        assert money(1234.56, currency=CRC) == "₡1,235"

    def test_explicit_decimals_still_overrides_the_currency(self):
        assert money(1234.56, 2, CRC) == "₡1,234.56"
        assert money(1234.56, 0, USD) == "$1,235"

    def test_no_currency_renders_the_anchor_market_exactly_as_before(self):
        # Un-migrated callers must degrade to the old behaviour, not to a bare
        # number with no symbol at all.
        assert money(1234.5) == "₡1,234"
        assert money(1234.5, 2) == "₡1,234.50"
        assert DEFAULT_CURRENCY["code"] == DEFAULT_CODE

    def test_every_supported_currency_renders_its_own_symbol_and_precision(self):
        """Audit over SUPPORTED, so a 14th currency added tomorrow is covered the
        moment it exists. Break to check: hardcode decimals=2 in money()."""
        for code, info in SUPPORTED.items():
            rendered = money(1234.5, currency={"code": code, **info})
            assert rendered.startswith(info["symbol"]), f"{code}: {rendered}"
            digits = rendered[len(info["symbol"]):]
            _, _, frac = digits.partition(".")
            assert len(frac) == info["decimals"], f"{code} rendered {rendered}"


class TestNoBackendModuleHardcodesTheColon:
    """The bug was one hardcoded symbol in one module reaching three surfaces.
    This asserts the rule everywhere instead of once per known site.

    Only two modules may name a currency symbol: the one that DEFINES the
    supported currencies and the one that holds the anchor-market fallback.
    Docstrings are exempt (a docstring cannot reach a user); comments are not
    string constants, so they never reach this check.
    """

    _ALLOWED = {
        Path("formatting.py"),
        Path("api/v1/currency.py"),
    }

    def _docstring_ids(self, tree: ast.AST) -> set[int]:
        ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None) or []
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    ids.add(id(body[0].value))
        return ids

    def _first_party_modules(self):
        """Backend source only: `.venv` holds third-party packages (and fixtures
        in other encodings), and the tests themselves quote ₡ on purpose."""
        for path in _BACKEND.rglob("*.py"):
            rel = path.relative_to(_BACKEND)
            if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                continue
            if rel.parts[0] == "tests" or rel in self._ALLOWED:
                continue
            yield rel, path

    def test_the_audit_actually_reads_the_modules_it_claims_to(self):
        """Guard against the audit silently walking nothing — the failure mode the
        tests README calls out (a check that prints OK while doing no work)."""
        scanned = {rel.as_posix() for rel, _ in self._first_party_modules()}
        for expected in ("inventory/po_pdf.py", "inventory/service.py",
                         "ai/narrative_service.py", "notifications/email.py"):
            assert expected in scanned, f"the audit never opened {expected}"
        assert len(scanned) > 50, f"only {len(scanned)} modules scanned"

    def test_no_symbol_is_glued_to_an_interpolated_amount(self):
        """The other shape of the same bug, and the one that hid longest: an
        f-string that welds a symbol onto the number it formats, as in
        f"total ${v:,.0f}". Two of these were live in the WhatsApp bot — including
        the total a buyer confirms by replying SÍ — and they wrote "$" even for
        the anchor market, so no ₡ audit would ever have found them."""
        offenders = []
        for rel, path in self._first_party_modules():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.JoinedStr):
                    continue
                for before, after in zip(node.values, node.values[1:]):
                    if (isinstance(before, ast.Constant)
                            and isinstance(before.value, str)
                            and before.value.rstrip().endswith(("$", "₡", "€", "Q", "S/"))
                            and isinstance(after, ast.FormattedValue)):
                        offenders.append(f"{rel.as_posix()}:{node.lineno}")
        assert not offenders, (
            "a currency symbol is hardcoded next to an interpolated amount — "
            f"format it with formatting.money(..., currency=...) instead: {offenders}"
        )

    def test_no_currency_symbol_literal_outside_the_currency_modules(self):
        offenders = []
        for rel, path in self._first_party_modules():
            # utf-8-sig: at least one module carries a BOM (a PowerShell
            # Set-Content casualty) and plain utf-8 makes ast.parse choke on it.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            skip = self._docstring_ids(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and "₡" in node.value and id(node) not in skip):
                    offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, (
            "hardcoded currency symbol in backend code — take the symbol from "
            f"the tenant's currency instead: {offenders}"
        )


# ── The purchase-order PDF ────────────────────────────────────────────────────

def _pdf_text(path: Path) -> str:
    """The strings actually drawn on the page, pulled out of the PDF's own
    content stream (ASCII85 + Flate, as reportlab writes it). Asserting on the
    rendered text is the only way to know what the supplier receives — the raw
    bytes are compressed, so `b"$" in raw` would pass on an empty document.

    Runs are concatenated with nothing between them: reportlab splits a single
    drawn amount across several runs for kerning, so "$5,000.00" arrives as
    "$5,00" + "0.00" and any separator would hide it.
    """
    raw = path.read_bytes()
    assert raw[:5] == b"%PDF-", "not a PDF"
    out = []
    for chunk in re.findall(rb"stream(.*?)endstream", raw, re.S):
        try:
            out.append(zlib.decompress(base64.a85decode(chunk.strip(b"\r\n"),
                                                        adobe=True)))
        except Exception:
            continue
    assert out, "no readable content stream in the PDF"
    body = b"\n".join(out).decode("latin-1")
    return "".join(re.findall(r"\((.*?)\)", body))


_PO_ITEMS = [{"sku": "SKU-001", "display_name": "Aceite de Oliva 1L",
              "supplier": "Distribuidora Andina", "final_qty": 312.0,
              "unit_cost": 8.5}]


class TestPurchaseOrderPdfCarriesTheTenantsCurrency:
    """`generate_po_pdf` is given a tenant_id, so these do NOT pass a currency in:
    the document has to resolve the tenant's own setting. That is the threading
    under test — passing the dict would only test the parameter."""

    def _pdf_for(self, tmp_path, monkeypatch, tenant_id: str) -> Path:
        from backend.storage import paths
        monkeypatch.setattr(paths, "_base", lambda: tmp_path)
        from backend.inventory.po_pdf import generate_po_pdf
        return generate_po_pdf(tenant_id, "po_cur1", "Distribuidora Andina",
                               _PO_ITEMS, {"generated_at": "2026-07-29T10:00:00",
                                           "po_log_id": "po_cur1"})

    def test_amounts_follow_the_currency_stored_on_the_tenant_row(
        self, client, tmp_path, monkeypatch, test_tenant
    ):
        """Break to check: drop the `currency=currency` on po_pdf's money() calls
        -> the dollar tenant's PDF says ₡ again and this goes red."""
        from backend.tenants.service import update_settings
        tid = test_tenant["id"]
        update_settings(tid, {"currency": "USD"})
        row = query_one("SELECT settings FROM tenants WHERE id = %s", (tid,))
        assert row["settings"]["currency"] == "USD", "the setting was not stored"

        text = _pdf_text(self._pdf_for(tmp_path, monkeypatch, tid))
        # Unit cost and subtotal, both with the two decimals USD declares.
        assert "$8.50" in text, text
        assert "$2,652.00" in text, text
        assert "Total: $2,652.00" in text, text
        assert "₡" not in text, "the colón survived on a dollar tenant's PO"

    def test_anchor_market_pdf_has_no_phantom_cents(
        self, client, tmp_path, monkeypatch, test_tenant
    ):
        """CRC declares 0 decimals, and the app shows colones with none. The PDF
        used to hardcode 2. Break to check: pass `2` again -> "2,652.00" appears."""
        tid = test_tenant["id"]
        assert currency_of(tid)["code"] == "CRC", "a fresh tenant must default to CRC"

        text = _pdf_text(self._pdf_for(tmp_path, monkeypatch, tid))
        assert "2,652" in text, text
        assert "2,652.00" not in text, "colones rendered with cents"
        assert "$" not in text, "a dollar sign on a colón tenant's PO"

    def test_plain_text_fallback_carries_the_symbol_too(
        self, client, tmp_path, monkeypatch, test_tenant
    ):
        """The reportlab-less fallback is a real code path with two more money()
        calls in it. Break to check: revert either of them."""
        from backend.tenants.service import update_settings
        tid = test_tenant["id"]
        update_settings(tid, {"currency": "GTQ"})    # symbol Q, 2 decimals

        # A None entry in sys.modules makes `import reportlab...` raise
        # ImportError, which is exactly what an install without it does. Every
        # already-imported submodule has to be blanked too: a cached
        # `reportlab.lib.pagesizes` is returned without ever consulting its
        # parent, so nulling only "reportlab" works alone and silently stops
        # working after any earlier test has built a PDF.
        for name in [n for n in sys.modules if n.split(".")[0] == "reportlab"]:
            monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.setitem(sys.modules, "reportlab", None)
        path = self._pdf_for(tmp_path, monkeypatch, tid)
        assert path.suffix == ".txt", "the fallback did not trigger"

        body = path.read_text(encoding="utf-8")
        assert "Q8.50" in body, body
        assert "Total: Q2,652.00" in body, body
        assert "₡" not in body


# ── The executive summary ─────────────────────────────────────────────────────

_OVERSTOCK_ITEM = {
    "sku": "OVR-1", "display_name": "Arroz 5kg", "signal": "SOBRESTOCK",
    "coverage_days": 120.0, "lead_time_days": 10, "abc": "A",
    "inventory_value": 12500.0, "recommended_qty": 0,
}


class TestOverstockSentenceCarriesTheTenantsCurrency:
    def _overstock_text(self, currency) -> str:
        from backend.inventory.service import generate_recommendations
        recs = generate_recommendations([dict(_OVERSTOCK_ITEM)], "daily", currency)
        rec = next(r for r in recs if r["rec_type"] == "OVERSTOCK")
        return rec["text"]

    def test_the_sentence_uses_the_tenants_symbol(self):
        """Break to check: drop `currency=currency` from the OVERSTOCK text."""
        text = self._overstock_text(USD)
        assert "$12,500.00" in text, text
        assert "₡" not in text

    def test_the_anchor_market_sentence_is_unchanged(self):
        text = self._overstock_text(None)
        assert "liberaría ₡12,500 en capital de trabajo" in text, text


class TestBriefingAndNarrativeThreadTheSetting:
    """End to end through the real endpoints: the currency is set with the real
    PATCH, and the assertions read the composed Spanish the API returns. This is
    what breaks if the tenant_id stops being threaded anywhere along
    endpoint -> get_morning_briefing -> generate_recommendations, or
    endpoint -> generate_morning_narrative -> _extract_key_points.
    """

    def _pile_of_stock(self, client, headers, tid, sid, sku):
        """500 units against 1/day: 500 days of coverage on a 10-day lead time is
        SOBRESTOCK, and 500 x 100.0 = 50,000 of capital sitting in it. (Coverage
        is deliberately under the 9990-day ceiling at which the service nulls it —
        past that there is no coverage figure and no overstock sentence.)"""
        from backend.db import session_store
        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 500, "lead_time_days": 10,
                             "moq": 1, "unit_cost": 100.0}, headers=headers)
        assert r.status_code == 200, r.text
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [
                {"date": f"2026-01-{i + 1:02d}", "value": 1.0,
                 "lower": 1.0, "upper": 1.0} for i in range(14)]}},
        })

    def _set_currency(self, client, headers, code):
        r = client.patch("/api/v1/tenant/currency", json={"code": code},
                         headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["current"]["code"] == code

    def test_briefing_recommendation_follows_the_tenants_currency(
        self, client, auth_headers, test_tenant
    ):
        from backend.sessions.service import create_session
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "cur-briefing")["id"]
        self._pile_of_stock(client, auth_headers, tid, sid, "CURBRF")
        self._set_currency(client, auth_headers, "USD")

        r = client.get(f"/api/v1/inventory/morning-briefing?session_id={sid}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        over = [x for x in data["recommendations"] if x["rec_type"] == "OVERSTOCK"]
        assert over, f"no overstock recommendation to check: {data['recommendations']}"
        # 500 units x 100.0 = 50,000 of trapped capital, relabelled not converted.
        assert "$50,000.00" in over[0]["text"], over[0]["text"]
        assert "₡" not in over[0]["text"]

    def test_narrative_key_points_follow_the_tenants_currency(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        """The rule-based fallback is pinned so the assertion is deterministic and
        no LLM is called; the currency threading under test is upstream of it."""
        import backend.ai.narrative_service as ns
        from backend.sessions.service import create_session
        monkeypatch.setattr(ns, "_get_client", lambda: None)

        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "cur-narrative")["id"]
        self._pile_of_stock(client, auth_headers, tid, sid, "CURNAR")
        self._set_currency(client, auth_headers, "USD")

        r = client.post("/api/v1/ai/narrative/morning",
                        json={"session_id": sid, "profile": "distributor"},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        blob = " ".join(data["key_points"]) + " " + data["narrative"]
        assert "$50,000.00" in blob, blob
        assert "₡" not in blob, "the colón survived in the executive summary"

    def test_the_same_tenant_on_colones_still_reads_colones(
        self, client, auth_headers, test_tenant
    ):
        """The anchor market must be untouched by all of the above."""
        from backend.sessions.service import create_session
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "cur-anchor")["id"]
        self._pile_of_stock(client, auth_headers, tid, sid, "CURCRC")

        r = client.get(f"/api/v1/inventory/morning-briefing?session_id={sid}",
                       headers=auth_headers)
        over = [x for x in r.json()["data"]["recommendations"]
                if x["rec_type"] == "OVERSTOCK"]
        assert over and "₡50,000" in over[0]["text"], over
        assert "$" not in over[0]["text"]


class TestInventoryPdfCarriesTheTenantsCurrency:
    def test_warehouse_value_tile_uses_the_tenants_symbol(
        self, client, auth_headers, test_tenant, tmp_path, monkeypatch
    ):
        """The /compras executive-summary PDF. Break to check: drop the
        `currency=currency` on the "Valor bodega" tile."""
        from backend.db import session_store
        from backend.sessions.service import create_session
        from backend.inventory import service as svc

        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "cur-pdf")["id"]
        r = client.put("/api/v1/inventory/stock/PDFCUR",
                       json={"current_stock": 50, "lead_time_days": 10,
                             "moq": 1, "unit_cost": 100.0}, headers=auth_headers)
        assert r.status_code == 200, r.text
        session_store.set_forecasts(tid, sid, {
            "PDFCUR": {"lightgbm": {"forecast": [
                {"date": f"2026-01-{i + 1:02d}", "value": 5.0,
                 "lower": 5.0, "upper": 5.0} for i in range(14)]}},
        })
        client.patch("/api/v1/tenant/currency", json={"code": "USD"},
                     headers=auth_headers)

        out = tmp_path / "inventory.pdf"
        out.write_bytes(svc.generate_inventory_pdf(tid, sid))
        text = _pdf_text(out)
        assert "$5,000.00" in text, text
        assert "₡" not in text


# ── The monthly recap email ───────────────────────────────────────────────────

_RECAP = {"month": "2026-06", "adoption_rate": 0.75,
          "recommendations_followed": 6, "recommendations_shown": 8,
          "stockout_risks_handled": 3, "capital_freed": 1250000.0,
          "managed_purchase_value": 890000.0}


class TestMonthlyRecapEmailCarriesTheTenantsCurrency:
    def _send(self, monkeypatch, currency):
        from backend.notifications import email as email_mod
        captured = {}
        monkeypatch.setattr(email_mod, "_send",
                            lambda to, subject, html, attachment=None:
                            captured.update(subject=subject, html=html))
        assert email_mod.send_monthly_roi_email(
            "buyer@faro-e2e.io", dict(_RECAP), "https://faro.test/roi",
            currency=currency) is True
        return captured

    def test_subject_and_tiles_use_the_tenants_symbol(self, monkeypatch):
        """Break to check: revert `_fmt_money` to the hardcoded ₡."""
        msg = self._send(monkeypatch, USD)
        assert msg["subject"] == "Faro — liberaste $1.250.000,00 en junio de 2026"
        assert "$890.000,00" in msg["html"]
        assert "₡" not in msg["subject"] and "₡" not in msg["html"]

    def test_the_anchor_market_recap_is_byte_identical_to_before(self, monkeypatch):
        msg = self._send(monkeypatch, None)
        assert msg["subject"] == "Faro — liberaste ₡1.250.000 en junio de 2026"
        assert "₡890.000" in msg["html"]


# ── Who may relabel a company's books ─────────────────────────────────────────

class TestOnlyAnAdminMayChangeTheCurrency:
    """Changing this mislabels every figure the company has already loaded, so it
    is admin-only. Each denial also asserts the stored setting did NOT move — a
    403 alone would pass on an endpoint that returned 403 and wrote anyway."""

    def _stored(self, tid):
        row = query_one("SELECT settings FROM tenants WHERE id = %s", (tid,))
        return (row["settings"] or {}).get("currency")

    def _assert_role(self, headers, expected):
        """The role in the token the fixture handed us, so a failure below can
        never be blamed on the fixture: it says whether the guard let the wrong
        role through or the test was holding the wrong token."""
        from backend.auth.jwt_handler import decode_token
        role = decode_token(headers["Authorization"].split()[1])["role"]
        assert role == expected, f"fixture handed a {role} token, not {expected}"

    def test_viewer_is_denied_and_the_setting_does_not_move(
        self, client, viewer_headers, test_tenant
    ):
        tid = test_tenant["id"]
        self._assert_role(viewer_headers, "viewer")
        before = self._stored(tid)
        r = client.patch("/api/v1/tenant/currency", json={"code": "USD"},
                         headers=viewer_headers)
        assert r.status_code == 403, r.text
        assert self._stored(tid) == before
        assert currency_of(tid)["code"] == "CRC"

    def test_analyst_is_denied_and_the_setting_does_not_move(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        self._assert_role(analyst_headers, "analyst")
        r = client.patch("/api/v1/tenant/currency", json={"code": "USD"},
                         headers=analyst_headers)
        assert r.status_code == 403, r.text
        assert self._stored(tid) is None
        assert currency_of(tid)["code"] == "CRC"

    def test_admin_succeeds_and_the_row_changes(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        r = client.patch("/api/v1/tenant/currency", json={"code": "MXN"},
                         headers=auth_headers)
        assert r.status_code == 200, r.text
        assert self._stored(tid) == "MXN"
        assert currency_of(tid)["symbol"] == SUPPORTED["MXN"]["symbol"]

    def test_an_unsupported_code_is_rejected_and_nothing_is_written(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        r = client.patch("/api/v1/tenant/currency", json={"code": "XYZ"},
                         headers=auth_headers)
        assert r.status_code == 400, r.text
        assert self._stored(tid) is None
        # An unsupported code must never leave a figure with no symbol at all.
        assert currency_of(tid)["symbol"] == "₡"
