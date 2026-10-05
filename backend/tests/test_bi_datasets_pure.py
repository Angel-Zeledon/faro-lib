"""The flat BI datasets, without a database: the contract, the bytes and the routes.

Pure on purpose (runs with `--noconftest`): rendering, paging and the column
contract are what a report binds to, so they are pinned here where nothing but the
code can move them. The routes are exercised with the auth dependency overridden
and the loaders replaced, so what is measured is the HTTP surface (format, paging,
refusals, headers, route order, key exposure) and not the data.

Scope, tenant isolation, the plan lock and the real loaders are in
`test_bi_datasets_db.py`.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.bi_datasets import render, spec
from backend.bi_datasets.spec import DATASETS


# ── The contract: the column set of every dataset is pinned ──────────────────

PINNED_COLUMNS = {
    "inventory-status": [
        "session_id", "period", "sku", "warehouse", "warehouse_id", "display_name",
        "supplier", "signal", "current_stock", "incoming_qty", "daily_demand",
        "coverage_days", "lead_time_days", "lead_time_source", "moq", "reorder_point",
        "recommended_qty", "recommended_action", "unit_cost", "forecast_source",
        "money_at_risk", "money_at_risk_basis",
    ],
    "purchase-order-lines": [
        "po_number", "po_log_id", "line_id", "ordered_at", "updated_at", "sent_at",
        "paid_at", "received_at", "cancelled_at", "reception_status", "approval_status",
        "source", "destination_warehouse", "line_warehouse", "supplier", "sku",
        "display_name", "signal", "line_status", "is_ordered", "recommended_qty",
        "final_qty", "received_qty", "outstanding_qty", "unit_cost", "line_value",
    ],
    "receptions": [
        "po_number", "po_log_id", "line_id", "received_at", "ordered_at",
        "days_to_last_reception", "reception_status", "po_cancelled", "supplier", "sku",
        "display_name", "warehouse", "final_qty", "received_qty", "outstanding_qty",
        "unit_cost", "received_value",
    ],
    "forecast-points": [
        "session_id", "period", "sku", "warehouse", "model", "model_is_champion",
        "step", "date", "forecast", "lower", "upper",
    ],
    "accuracy": [
        "session_id", "sku", "warehouse", "model", "wape", "bias", "mae", "rmse",
    ],
    "committed-demand": [
        "id", "sku", "customer", "delivery_date", "quantity", "probability",
        "on_top_of_base", "status", "overdue", "warehouse_id", "warehouse", "note",
        "created_at", "updated_at", "at_risk", "shortfall", "latest_safe_order_date",
    ],
}


class TestTheColumnContract:
    def test_every_dataset_is_pinned_and_in_this_order(self):
        """Renaming, removing or reordering a column breaks somebody's report.
        Adding one is allowed: append it to the pinned list AT THE END."""
        assert set(DATASETS) == set(PINNED_COLUMNS)
        for name, pinned in PINNED_COLUMNS.items():
            assert list(DATASETS[name].column_names) == pinned, name

    def test_columns_are_unique_typed_and_described(self):
        for d in DATASETS.values():
            assert len(set(d.column_names)) == len(d.column_names), d.name
            for c in d.columns:
                assert c.type in spec.TYPES and c.description.strip(), (d.name, c.name)

    def test_the_sort_is_made_of_real_columns_and_the_timestamp_filter_is_a_datetime(self):
        for d in DATASETS.values():
            assert set(d.sort) <= set(d.column_names) | {"created_at"}, d.name
            if d.updated_since_column:
                col = next(c for c in d.columns if c.name == d.updated_since_column)
                assert col.type == "datetime", d.name

    def test_the_wanted_columns_are_there(self):
        """The columns the brief names for each dataset."""
        want = {
            "inventory-status": {"sku", "warehouse", "signal", "current_stock",
                                 "incoming_qty", "coverage_days", "reorder_point",
                                 "recommended_qty", "forecast_source", "money_at_risk"},
            "purchase-order-lines": {"po_number", "supplier", "reception_status",
                                     "ordered_at", "final_qty", "unit_cost", "received_qty"},
            "forecast-points": {"sku", "date", "forecast", "lower", "upper"},
            "accuracy": {"sku", "wape", "bias"},
            "committed-demand": {"sku", "delivery_date", "quantity", "status"},
        }
        for name, cols in want.items():
            assert cols <= set(DATASETS[name].column_names), name

    def test_only_the_datasets_with_a_timestamp_accept_updated_since(self):
        has = {n for n, d in DATASETS.items() if "updated_since" in d.accepted_parameters}
        assert has == {"purchase-order-lines", "receptions", "committed-demand"}

    def test_describe_is_json_safe_and_lists_every_column(self):
        import json
        for d in DATASETS.values():
            info = spec.describe(d)
            json.dumps(info)
            assert [c["name"] for c in info["columns"]] == list(d.column_names)
            assert info["path"] == f"/api/v1/datasets/{d.name}"


# ── Numbers, dates, booleans ─────────────────────────────────────────────────

class TestInvariantFormats:
    @pytest.mark.parametrize("value,text", [
        (12.0, "12"), (12.5, "12.5"), (0.1 + 0.2, "0.3"), (-3.25, "-3.25"),
        (0.00001, "0.00001"), (1e-10, "0.0000000001"), (1e22, "10000000000000000000000"),
        (-0.0, "0"), (0.0, "0"), (7, "7"), (1234567.891, "1234567.891"),
    ])
    def test_numbers_are_plain_decimal_text(self, value, text):
        assert render.number_text(render.normalize(value, "number")) == text

    def test_no_thousands_separator_comma_or_exponent_ever(self):
        for v in (1234567.5, 1e-7, 123456789012.0, 5e-324):
            t = render.number_text(render.normalize(v, "number"))
            assert "," not in t and "e" not in t.lower()

    def test_a_locale_cannot_change_the_decimal_separator(self):
        import locale
        before = locale.setlocale(locale.LC_NUMERIC)
        try:
            for loc in ("de_DE.UTF-8", "es_ES.UTF-8", "fr_FR.UTF-8"):
                try:
                    locale.setlocale(locale.LC_NUMERIC, loc)
                except locale.Error:
                    continue
                assert render.number_text(1234.5) == "1234.5"
        finally:
            locale.setlocale(locale.LC_NUMERIC, before)

    def test_non_finite_numbers_are_null_not_nan(self):
        for v in (float("nan"), float("inf"), float("-inf")):
            assert render.normalize(v, "number") is None

    def test_dates_are_iso_and_instants_are_utc(self):
        assert render.normalize(date(2026, 10, 5), "date") == "2026-10-05"
        assert render.normalize("2026-10-05T13:00:00", "date") == "2026-10-05"
        aware = datetime(2026, 10, 5, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
        assert render.normalize(aware, "datetime") == "2026-10-06T04:30:00Z"
        assert render.normalize(datetime(2026, 10, 5, 8, 0), "datetime") == "2026-10-05T08:00:00Z"
        assert render.normalize("2026-10-05T08:00:00+00:00", "datetime") == "2026-10-05T08:00:00Z"
        assert render.normalize("2026-10-05T08:00:00Z", "datetime") == "2026-10-05T08:00:00Z"

    def test_integer_booleans_and_strings(self):
        assert render.normalize(3.0, "integer") == 3 and isinstance(
            render.normalize(3.0, "integer"), int)
        assert render.normalize(True, "boolean") is True
        assert render.normalize(12, "string") == "12"

    def test_a_value_that_is_not_its_type_becomes_null_and_is_logged(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend.bi_datasets.render"):
            assert render.normalize("abc", "number", "qty") is None
            assert render.normalize(2.5, "integer", "n") is None
            assert render.normalize("yes", "boolean", "flag") is None
            assert render.normalize("not a date", "date", "d") is None
            assert render.normalize(True, "number", "q") is None
        assert len(caplog.records) == 5, "every bad cell leaves a trace"

    def test_an_unknown_type_is_a_bug_not_a_null(self):
        with pytest.raises(ValueError):
            render.normalize(1, "money")


# ── CSV ──────────────────────────────────────────────────────────────────────

def _parse(text: str):
    assert text.startswith(render.CSV_BOM)
    return list(csv.reader(io.StringIO(text[1:])))


class TestCsv:
    D = DATASETS["receptions"]

    def _row(self, **kw):
        base = {"po_number": 12, "po_log_id": "p1", "line_id": "l1",
                "received_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
                "ordered_at": date(2026, 9, 20), "days_to_last_reception": 11.375,
                "reception_status": "received", "po_cancelled": False,
                "supplier": "Acme, S.A.", "sku": "A-1", "display_name": 'Tornillo "inox"',
                "warehouse": "principal", "final_qty": 100, "received_qty": 100.0,
                "outstanding_qty": 0.0, "unit_cost": None, "received_value": None}
        return {**base, **kw}

    def test_header_is_the_declared_columns_in_order(self):
        rows = _parse(render.to_csv(self.D, render.shape_rows(self.D, [self._row()])))
        assert rows[0] == list(self.D.column_names)
        assert len(rows) == 2 and all(len(r) == len(rows[0]) for r in rows)

    def test_an_empty_dataset_still_has_its_header(self):
        rows = _parse(render.to_csv(self.D, []))
        assert rows == [list(self.D.column_names)]

    def test_values_are_invariant_text_and_null_is_an_empty_cell(self):
        rows = _parse(render.to_csv(self.D, render.shape_rows(self.D, [self._row()])))
        got = dict(zip(rows[0], rows[1]))
        assert got["po_number"] == "12"
        assert got["received_at"] == "2026-10-01T09:00:00Z"
        assert got["ordered_at"] == "2026-09-20T00:00:00Z"
        assert got["days_to_last_reception"] == "11.375"
        assert got["po_cancelled"] == "false"
        assert got["final_qty"] == "100" and got["outstanding_qty"] == "0"
        assert got["unit_cost"] == "" and got["received_value"] == ""
        # commas and quotes survive the round trip
        assert got["supplier"] == "Acme, S.A." and got["display_name"] == 'Tornillo "inox"'

    def test_utf8_with_bom_and_crlf(self):
        text = render.to_csv(self.D, render.shape_rows(
            self.D, [self._row(display_name="Ñandú ácido")]))
        raw = text.encode("utf-8")
        assert raw.startswith(b"\xef\xbb\xbf")
        assert "Ñandú ácido" in raw.decode("utf-8-sig")
        assert raw.count(b"\r\n") == 2 and b"\n" not in raw.replace(b"\r\n", b"")

    def test_the_same_rows_always_give_the_same_bytes(self):
        shaped = render.shape_rows(self.D, [self._row(), self._row(line_id="l2")])
        assert render.to_csv(self.D, shaped) == render.to_csv(self.D, shaped)

    def test_extra_keys_never_add_columns_and_missing_keys_are_null(self):
        shaped = render.shape_row(self.D, {"sku": "X", "surprise": 1})
        assert list(shaped) == list(self.D.column_names)
        assert shaped["sku"] == "X" and shaped["po_number"] is None

    @pytest.mark.parametrize("text,safe", [
        ("=SUM(A1)", "'=SUM(A1)"), ("@cmd", "'@cmd"), ("+1+1", "'+1+1"),
        ("-A12", "'-A12"), ("\tx", "'\tx"),
        ("-12.5", "-12.5"), ("+7", "+7"), ("Tornillo", "Tornillo"), ("", ""),
    ])
    def test_text_that_a_spreadsheet_would_run_is_neutralised(self, text, safe):
        assert render.text_cell(text) == safe

    def test_json_keeps_the_text_untouched(self):
        shaped = render.shape_row(self.D, self._row(display_name="=1+1"))
        assert shaped["display_name"] == "=1+1"


# ── Paging ───────────────────────────────────────────────────────────────────

class TestPaging:
    def test_bounds(self):
        assert render.page_bounds(0, 1, 10) == {
            "page": 1, "limit": 10, "total": 0, "pages": 1, "offset": 0, "next_page": None}
        assert render.page_bounds(25, 1, 10)["next_page"] == 2
        assert render.page_bounds(25, 3, 10)["next_page"] is None
        assert render.page_bounds(30, 3, 10)["pages"] == 3
        assert render.page_bounds(25, 9, 10)["next_page"] is None   # past the end

    def test_pages_cover_every_row_once_in_order(self):
        rows = [{"k": i} for i in range(23)]
        seen = []
        page = 1
        while page:
            chunk, info = render.slice_page(rows, page, 5)
            seen += chunk
            page = info["next_page"]
        assert seen == rows

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        chunk, info = render.slice_page([{"k": 1}], 5, 10)
        assert chunk == [] and info["total"] == 1

    def test_invalid_paging_is_refused(self):
        with pytest.raises(ValueError):
            render.page_bounds(10, 0, 10)
        with pytest.raises(ValueError):
            render.page_bounds(10, 1, 0)

    def test_the_cap_and_the_default(self):
        assert render.clamp_limit(None) == render.DEFAULT_LIMIT
        assert render.clamp_limit(10**9) == render.MAX_LIMIT
        assert render.clamp_limit(0) == 1

    def test_the_sort_key_is_deterministic_with_nulls(self):
        rows = [{"sku": "B", "warehouse": None}, {"sku": "A", "warehouse": "Z"},
                {"sku": "A", "warehouse": None}]
        got = sorted(rows, key=render.sort_key("sku", "warehouse"))
        assert [(r["sku"], r["warehouse"]) for r in got] == [("A", None), ("A", "Z"), ("B", None)]

    def test_the_headers_carry_the_paging_facts(self):
        h = render.page_headers(render.page_bounds(25, 2, 10))
        assert h == {"X-Total-Count": "25", "X-Page": "2", "X-Page-Size": "10",
                     "X-Page-Count": "3", "X-Next-Page": "3"}
        assert "X-Next-Page" not in render.page_headers(render.page_bounds(25, 3, 10))


# ── The HTTP surface, with the data stubbed ──────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    """The real app, auth overridden and the loaders replaced by canned rows."""
    from backend.api.v1.bi_datasets import bi_user
    from backend.auth.guards import CurrentUser
    from backend.bi_datasets import service
    from backend.main import app

    state = {"scope": None, "calls": []}

    def fake_user():
        return CurrentUser("u1", "t1", "viewer")

    app.dependency_overrides[bi_user] = fake_user
    monkeypatch.setattr("backend.auth.warehouse_scope.scope_names",
                        lambda user: state["scope"])

    rows = [{"sku": f"S{i:02d}", "warehouse": "principal", "signal": "OK",
             "current_stock": float(i), "display_name": "=bad"} for i in range(12)]

    def fake_loader(user, params, page, limit):
        state["calls"].append((params, page, limit))
        chunk, info = render.slice_page(rows, page, limit)
        return chunk, info

    for name in list(service.LOADERS):
        monkeypatch.setitem(service.LOADERS, name, fake_loader)
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.pop(bi_user, None)


ALL_PATHS = [f"/api/v1/datasets/{n}" for n in DATASETS]


class TestRoutes:
    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_json_is_the_default_with_the_version_header(self, api, path):
        client, _ = api
        r = client.get(path)
        assert r.status_code == 200, r.text
        assert r.headers["X-Schema-Version"] == "1"
        assert r.headers["X-Total-Count"] == "12"
        assert r.headers["Cache-Control"] == "no-store"
        data = r.json()["data"]
        assert data["columns"] == list(DATASETS[path.rsplit("/", 1)[1]].column_names)
        assert data["total"] == 12 and data["next_page"] is None
        assert "scope" not in data

    def test_the_fixed_paths_beat_the_upload_router_id_route(self, api):
        """`/datasets/{dataset_id}` is registered too; if it won, this would be a
        404 dataset_not_found, not the feed."""
        client, state = api
        assert client.get("/api/v1/datasets/inventory-status").status_code == 200
        assert state["calls"], "the loader was not reached"

    def test_csv_is_a_file_a_bi_tool_can_load(self, api):
        client, _ = api
        r = client.get("/api/v1/datasets/inventory-status?format=csv")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/csv; charset=utf-8"
        assert r.headers["X-Schema-Version"] == "1"
        assert 'filename="inventory-status.csv"' in r.headers["content-disposition"]
        rows = _parse(r.content.decode("utf-8"))
        assert rows[0] == list(DATASETS["inventory-status"].column_names)
        assert len(rows) == 13
        got = dict(zip(rows[0], rows[1]))
        assert got["sku"] == "S00" and got["display_name"] == "'=bad"

    def test_paging_walks_the_whole_table(self, api):
        client, _ = api
        seen, page = [], 1
        while page:
            r = client.get(f"/api/v1/datasets/inventory-status?limit=5&page={page}")
            d = r.json()["data"]
            seen += [i["sku"] for i in d["items"]]
            page = d["next_page"]
        assert seen == [f"S{i:02d}" for i in range(12)]

    def test_csv_paging_is_in_the_headers(self, api):
        client, _ = api
        r = client.get("/api/v1/datasets/inventory-status?format=csv&limit=5&page=2")
        assert (r.headers["X-Page"], r.headers["X-Page-Count"], r.headers["X-Next-Page"]) \
            == ("2", "3", "3")

    @pytest.mark.parametrize("query", [
        "limit=0", f"limit={render.MAX_LIMIT + 1}", "page=0", "format=xml", "page=abc",
    ])
    def test_out_of_range_paging_and_formats_are_refused(self, api, query):
        client, state = api
        r = client.get(f"/api/v1/datasets/inventory-status?{query}")
        assert r.status_code == 422, r.text
        assert state["calls"] == []

    def test_the_cap_is_accepted_exactly(self, api):
        client, _ = api
        assert client.get(
            f"/api/v1/datasets/inventory-status?limit={render.MAX_LIMIT}").status_code == 200

    def test_an_unknown_parameter_is_refused_not_ignored(self, api):
        """`updated_since` on a dataset without a timestamp would otherwise return
        everything while the report believes it is incremental."""
        client, state = api
        r = client.get("/api/v1/datasets/inventory-status?updated_since=2026-10-01")
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "dataset_parameter_unsupported"
        assert body["error_params"]["parameter"] == "updated_since"
        assert state["calls"] == []
        r = client.get("/api/v1/datasets/purchase-order-lines?pagesize=5")
        assert r.json()["error_code"] == "dataset_parameter_unsupported"

    def test_updated_since_is_parsed_where_it_is_accepted(self, api):
        client, state = api
        r = client.get("/api/v1/datasets/purchase-order-lines?updated_since=2026-10-01T00:00:00Z")
        assert r.status_code == 200, r.text
        since = state["calls"][-1][0]["updated_since"]
        assert since == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert client.get(
            "/api/v1/datasets/receptions?updated_since=not-a-date").status_code == 422

    def test_a_session_id_reaches_the_loader(self, api):
        client, state = api
        client.get("/api/v1/datasets/accuracy?session_id=abc")
        assert state["calls"][-1][0] == {"session_id": "abc"}

    def test_a_scoped_caller_is_told_so(self, api):
        client, state = api
        state["scope"] = frozenset({"Norte"})
        r = client.get("/api/v1/datasets/inventory-status")
        assert r.headers["X-Warehouse-Scope"] == "limited"
        assert r.json()["data"]["scope"] == {"warehouses": ["Norte"]}
        c = client.get("/api/v1/datasets/inventory-status?format=csv")
        assert c.headers["X-Warehouse-Scope"] == "limited"

    def test_an_unrestricted_caller_gets_no_scope_marker(self, api):
        client, _ = api
        r = client.get("/api/v1/datasets/inventory-status")
        assert "X-Warehouse-Scope" not in r.headers

    def test_the_schema_endpoint_lists_every_dataset_and_column(self, api):
        client, _ = api
        r = client.get("/api/v1/datasets/schema")
        assert r.status_code == 200 and r.headers["X-Schema-Version"] == "1"
        data = r.json()["data"]
        assert {d["name"] for d in data["datasets"]} == set(DATASETS)
        for d in data["datasets"]:
            assert [c["name"] for c in d["columns"]] == PINNED_COLUMNS[d["name"]]
            assert all(c["type"] in spec.TYPES for c in d["columns"])
        assert "csv" in data["formats"] and "paging" in data["formats"]

    def test_the_old_upload_routes_still_answer(self):
        """The new router must not have shadowed the dataset-upload area."""
        from backend.main import app
        paths = {(m, r.path) for r in app.routes if hasattr(r, "methods")
                 for m in r.methods}
        assert ("GET", "/api/v1/datasets/{dataset_id}") in paths
        assert ("POST", "/api/v1/datasets") in paths
        assert ("GET", "/api/v1/datasets") in paths


# ── Who may call it: the public surface ──────────────────────────────────────

class TestPublicSurface:
    def test_every_new_route_is_a_read_route_for_keys(self):
        from backend.api.public_surface import exposure
        from backend.main import app
        routes = [r for r in app.routes if "bi-datasets" in (getattr(r, "tags", None) or [])]
        assert len(routes) == len(DATASETS) + 1   # the datasets and /schema
        for r in routes:
            exp = exposure(r)
            assert exp.exposed and exp.scope == "read", (r.path, exp.reason)
            assert r.methods == {"GET"}

    def test_the_tag_is_classified(self):
        from backend.api.public_surface import EXPOSED_TAGS, INTERNAL_TAGS
        assert "bi-datasets" in EXPOSED_TAGS and "bi-datasets" not in INTERNAL_TAGS

    def test_the_committed_reference_lists_the_feeds(self):
        import json
        from pathlib import Path
        ref = json.loads((Path(__file__).resolve().parents[2] / "Frontend" / "src" / "data"
                          / "public-api.json").read_text(encoding="utf-8"))
        paths = {e["path"] for t in ref["tags"] for e in t["endpoints"]
                 if t["tag"] == "bi-datasets"}
        assert paths == {"/datasets/schema"} | {f"/datasets/{n}" for n in DATASETS}


# ── How a key may be presented: Bearer or X-API-Key, never the URL ───────────

def _request(headers: dict[str, str], query: str = "") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/x", "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


KEY = "sk_live_" + "a" * 32


class TestKeyHeaders:
    def _creds(self, headers, query=""):
        from backend.auth.guards import security
        return asyncio.run(security(_request(headers, query)))

    def test_bearer_still_works(self):
        c = self._creds({"Authorization": f"Bearer {KEY}"})
        assert c.credentials == KEY

    def test_x_api_key_works_and_becomes_the_same_credential(self):
        c = self._creds({"X-API-Key": KEY})
        assert c.scheme == "Bearer" and c.credentials == KEY

    def test_authorization_wins_when_both_are_sent(self):
        other = "sk_live_" + "b" * 32
        c = self._creds({"Authorization": f"Bearer {other}", "X-API-Key": KEY})
        assert c.credentials == other

    def test_a_key_in_the_url_is_not_a_credential(self):
        with pytest.raises(HTTPException) as e:
            self._creds({}, query=f"api_key={KEY}")
        assert e.value.status_code in (401, 403)

    def test_x_api_key_that_is_not_a_key_is_no_credential(self):
        with pytest.raises(HTTPException) as e:
            self._creds({"X-API-Key": "eyJhbGciOiJIUzI1NiJ9.e30.x"})
        assert e.value.status_code in (401, 403)

    def test_a_missing_credential_answers_as_before(self):
        with pytest.raises(HTTPException) as e:
            self._creds({})
        assert e.value.status_code in (401, 403)

    def test_a_non_bearer_authorization_is_not_rescued_by_the_other_header(self):
        with pytest.raises(HTTPException):
            self._creds({"Authorization": "Basic abc", "X-API-Key": KEY})

    def test_it_reaches_the_key_path_of_get_current_user(self, monkeypatch):
        """Through the real dependency: the header value is resolved as a key."""
        from backend.auth import guards
        seen = {}

        def fake_auth(credential, scope=None):
            seen["credential"] = credential
            return guards.CurrentUser("api_key:1", "t1", "viewer", api_key_id="1")

        monkeypatch.setattr(guards, "_authenticate_api_key", fake_auth)
        req = _request({"X-API-Key": KEY})
        creds = asyncio.run(guards.security(req))
        user = guards.get_current_user(req, creds)
        assert seen["credential"] == KEY and user.is_machine
