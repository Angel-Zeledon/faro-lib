"""StockAI API client (Python). GENERATED - do not edit by hand.

Regenerate with `python -m backend.scripts.generate_clients`; the test suite fails
when this file is out of step with the public API. Standard library only.

    from stockai_client import StockAI
    api = StockAI("sk_live_...")
    envelope = api.get_alerts()

A call returns the parsed JSON envelope exactly as the API sent it (or the bytes of
a file download). A refused call raises `StockAIError` carrying `status`,
`error_code`, `error_params` and, when the API sent one, `retry_after` seconds.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional

DEFAULT_BASE_URL = "https://app.stockai.es"
API_PREFIX = "/api/v1"


class StockAIError(Exception):
    def __init__(self, status: int, message: str, error_code: Optional[str] = None,
                 error_params: Optional[dict] = None, retry_after: Optional[int] = None):
        super().__init__(f"{status} {error_code or ''} {message}".strip())
        self.status = status
        self.message = message
        self.error_code = error_code
        self.error_params = error_params or {}
        self.retry_after = retry_after


class StockAI:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0):
        if not api_key.startswith("sk_"):
            raise ValueError("api_key must be a StockAI key (sk_...)")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def request(self, method: str, path: str, *, query: Optional[dict] = None,
                headers: Optional[dict] = None, body: Any = None,
                files: Optional[dict] = None, raw: bool = False) -> Any:
        """One call. `path` is relative to the API prefix and already filled in."""
        url = self._base + API_PREFIX + path
        params = {k: v for k, v in (query or {}).items() if v is not None}
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        hdrs = {"Authorization": "Bearer " + self._key, "Accept": "application/json"}
        hdrs.update({k: str(v) for k, v in (headers or {}).items() if v is not None})
        data = None
        if files is not None:
            data, content_type = _multipart(files, body if isinstance(body, dict) else {})
            hdrs["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = resp.read()
                kind = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise _error(exc) from None
        if raw or "json" not in kind:
            return payload
        return json.loads(payload) if payload else None

    def delete_data_sources_source_id(self, source_id) -> Any:
        """Delete a data source  [DELETE /data-sources/{source_id}, needs a write key]"""
        return self.request("DELETE", f"/data-sources/{_q(source_id)}")

    def delete_documents_doc_id(self, doc_id) -> Any:
        """Delete a document  [DELETE /documents/{doc_id}, needs a write key]"""
        return self.request("DELETE", f"/documents/{_q(doc_id)}")

    def delete_inventory_bom_parent_sku_child_sku(self, parent_sku, child_sku) -> Any:
        """Remove a bill-of-materials line  [DELETE /inventory/bom/{parent_sku}/{child_sku}, needs a write key]"""
        return self.request("DELETE", f"/inventory/bom/{_q(parent_sku)}/{_q(child_sku)}")

    def delete_inventory_events_event_id(self, event_id) -> Any:
        """Delete a demand event  [DELETE /inventory/events/{event_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory/events/{_q(event_id)}")

    def delete_inventory_events_event_id_multipliers_override_id(self, event_id, override_id) -> Any:
        """Remove an event multiplier  [DELETE /inventory/events/{event_id}/multipliers/{override_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory/events/{_q(event_id)}/multipliers/{_q(override_id)}")

    def delete_inventory_price_breaks_price_break_id(self, price_break_id) -> Any:
        """Delete a price break  [DELETE /inventory/price-breaks/{price_break_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory/price-breaks/{_q(price_break_id)}")

    def delete_inventory_stock_counts_count_id_lines_sku(self, count_id, sku) -> Any:
        """Delete Line  [DELETE /inventory/stock-counts/{count_id}/lines/{sku}, needs a write key]"""
        return self.request("DELETE", f"/inventory/stock-counts/{_q(count_id)}/lines/{_q(sku)}")

    def delete_inventory_stock_sku(self, sku) -> Any:
        """Delete a stock record  [DELETE /inventory/stock/{sku}, needs a write key]"""
        return self.request("DELETE", f"/inventory/stock/{_q(sku)}")

    def delete_inventory_stock_sku_suppliers_supplier_id(self, sku, supplier_id) -> Any:
        """Unlink a supplier from a SKU  [DELETE /inventory/stock/{sku}/suppliers/{supplier_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory/stock/{_q(sku)}/suppliers/{_q(supplier_id)}")

    def delete_inventory_suppliers_supplier_id(self, supplier_id) -> Any:
        """Deactivate a supplier  [DELETE /inventory/suppliers/{supplier_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory/suppliers/{_q(supplier_id)}")

    def delete_inventory_sync_profiles_profile_id(self, profile_id) -> Any:
        """Delete Profile  [DELETE /inventory-sync/profiles/{profile_id}, needs a write key]"""
        return self.request("DELETE", f"/inventory-sync/profiles/{_q(profile_id)}")

    def delete_inventory_warehouses_lanes(self, *, from_warehouse: Any, to_warehouse: Any) -> Any:
        """Delete a transfer lane  [DELETE /inventory/warehouses/lanes, needs a write key]"""
        return self.request("DELETE", f"/inventory/warehouses/lanes", query={"from_warehouse": from_warehouse, "to_warehouse": to_warehouse})

    def delete_jobs_job_id(self, job_id) -> Any:
        """Cancel a job  [DELETE /jobs/{job_id}, needs a write key]"""
        return self.request("DELETE", f"/jobs/{_q(job_id)}")

    def delete_scenarios_scenario_id(self, scenario_id) -> Any:
        """Delete a scenario  [DELETE /scenarios/{scenario_id}, needs a write key]"""
        return self.request("DELETE", f"/scenarios/{_q(scenario_id)}")

    def delete_sessions_session_id(self, session_id) -> Any:
        """Delete a session  [DELETE /sessions/{session_id}, needs a write key]"""
        return self.request("DELETE", f"/sessions/{_q(session_id)}")

    def delete_sessions_session_id_schedule(self, session_id) -> Any:
        """Delete a retraining schedule  [DELETE /sessions/{session_id}/schedule, needs a write key]"""
        return self.request("DELETE", f"/sessions/{_q(session_id)}/schedule")

    def delete_webhooks_webhook_id(self, webhook_id) -> Any:
        """Delete a webhook  [DELETE /webhooks/{webhook_id}, needs a write key]"""
        return self.request("DELETE", f"/webhooks/{_q(webhook_id)}")

    def get_alerts(self, *, limit: Any = None) -> Any:
        """List alerts  [GET /alerts, needs a read key]"""
        return self.request("GET", f"/alerts", query={"limit": limit})

    def get_alerts_activity(self, *, limit: Any = None, offset: Any = None, kind: Any = None, severity: Any = None) -> Any:
        """List account activity  [GET /alerts/activity, needs a read key]"""
        return self.request("GET", f"/alerts/activity", query={"limit": limit, "offset": offset, "kind": kind, "severity": severity})

    def get_alerts_kinds(self) -> Any:
        """List alert kinds  [GET /alerts/kinds, needs a read key]"""
        return self.request("GET", f"/alerts/kinds")

    def get_config_schema(self) -> Any:
        """Configuration schema  [GET /config/schema, needs a read key]"""
        return self.request("GET", f"/config/schema")

    def get_data_freshness(self) -> Any:
        """Read data freshness  [GET /data-freshness, needs a read key]"""
        return self.request("GET", f"/data-freshness")

    def get_data_sources(self, *, skip: Any = None, limit: Any = None) -> Any:
        """List data sources  [GET /data-sources, needs a read key]"""
        return self.request("GET", f"/data-sources", query={"skip": skip, "limit": limit})

    def get_data_sources_source_id(self, source_id) -> Any:
        """Get a data source  [GET /data-sources/{source_id}, needs a read key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}")

    def get_data_sources_source_id_analyze(self, source_id, *, date_col: Any = None, target_col: Any = None, sku_col: Any = None, sheet: Any = None, date_from: Any = None, date_to: Any = None) -> Any:
        """Analyze a data source  [GET /data-sources/{source_id}/analyze, needs a read key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/analyze", query={"date_col": date_col, "target_col": target_col, "sku_col": sku_col, "sheet": sheet, "date_from": date_from, "date_to": date_to})

    def get_data_sources_source_id_analyze_sku_id(self, source_id, sku_id, *, date_col: Any, target_col: Any, sku_col: Any = None, sheet: Any = None, date_from: Any = None, date_to: Any = None) -> Any:
        """Analyze one SKU in a data source  [GET /data-sources/{source_id}/analyze/{sku_id}, needs a read key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/analyze/{_q(sku_id)}", query={"date_col": date_col, "target_col": target_col, "sku_col": sku_col, "sheet": sheet, "date_from": date_from, "date_to": date_to})

    def get_data_sources_source_id_edit_table(self, source_id) -> Any:
        """Load a data source as an editable table  [GET /data-sources/{source_id}/edit-table, needs a read key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/edit-table")

    def get_data_sources_source_id_preview(self, source_id, *, rows: Any = None, sheet: Any = None) -> Any:
        """Preview a data source  [GET /data-sources/{source_id}/preview, needs a read key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/preview", query={"rows": rows, "sheet": sheet})

    def get_data_sources_source_id_schema(self, source_id, *, refresh: Any = None) -> Any:
        """List a SQL source's tables  [GET /data-sources/{source_id}/schema, needs a write key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/schema", query={"refresh": refresh})

    def get_data_sources_source_id_schema_columns(self, source_id, *, table: Any, schema: Any = None, refresh: Any = None) -> Any:
        """List a table's columns  [GET /data-sources/{source_id}/schema/columns, needs a write key]"""
        return self.request("GET", f"/data-sources/{_q(source_id)}/schema/columns", query={"table": table, "schema": schema, "refresh": refresh})

    def get_datasets(self, *, skip: Any = None, limit: Any = None) -> Any:
        """List datasets  [GET /datasets, needs a read key]"""
        return self.request("GET", f"/datasets", query={"skip": skip, "limit": limit})

    def get_datasets_accuracy(self, *, format: Any = None, page: Any = None, limit: Any = None, session_id: Any = None) -> Any:
        """Accuracy Dataset  [GET /datasets/accuracy, needs a read key]"""
        return self.request("GET", f"/datasets/accuracy", query={"format": format, "page": page, "limit": limit, "session_id": session_id})

    def get_datasets_committed_demand(self, *, format: Any = None, page: Any = None, limit: Any = None, updated_since: Any = None) -> Any:
        """Committed Demand Dataset  [GET /datasets/committed-demand, needs a read key]"""
        return self.request("GET", f"/datasets/committed-demand", query={"format": format, "page": page, "limit": limit, "updated_since": updated_since})

    def get_datasets_dataset_id(self, dataset_id) -> Any:
        """Get a dataset  [GET /datasets/{dataset_id}, needs a read key]"""
        return self.request("GET", f"/datasets/{_q(dataset_id)}")

    def get_datasets_forecast_points(self, *, format: Any = None, page: Any = None, limit: Any = None, session_id: Any = None) -> Any:
        """Forecast Points Dataset  [GET /datasets/forecast-points, needs a read key]"""
        return self.request("GET", f"/datasets/forecast-points", query={"format": format, "page": page, "limit": limit, "session_id": session_id})

    def get_datasets_inventory_status(self, *, format: Any = None, page: Any = None, limit: Any = None, session_id: Any = None) -> Any:
        """Inventory Status Dataset  [GET /datasets/inventory-status, needs a read key]"""
        return self.request("GET", f"/datasets/inventory-status", query={"format": format, "page": page, "limit": limit, "session_id": session_id})

    def get_datasets_purchase_order_lines(self, *, format: Any = None, page: Any = None, limit: Any = None, updated_since: Any = None) -> Any:
        """Purchase Order Lines Dataset  [GET /datasets/purchase-order-lines, needs a read key]"""
        return self.request("GET", f"/datasets/purchase-order-lines", query={"format": format, "page": page, "limit": limit, "updated_since": updated_since})

    def get_datasets_receptions(self, *, format: Any = None, page: Any = None, limit: Any = None, updated_since: Any = None) -> Any:
        """Receptions Dataset  [GET /datasets/receptions, needs a read key]"""
        return self.request("GET", f"/datasets/receptions", query={"format": format, "page": page, "limit": limit, "updated_since": updated_since})

    def get_datasets_schema(self) -> Any:
        """Datasets Schema  [GET /datasets/schema, needs a read key]"""
        return self.request("GET", f"/datasets/schema")

    def get_documents(self) -> Any:
        """List documents  [GET /documents, needs a read key]"""
        return self.request("GET", f"/documents")

    def get_documents_doc_id(self, doc_id) -> Any:
        """Get a document  [GET /documents/{doc_id}, needs a read key]"""
        return self.request("GET", f"/documents/{_q(doc_id)}")

    def get_documents_doc_id_content(self, doc_id) -> Any:
        """Download a document  [GET /documents/{doc_id}/content, needs a read key]"""
        return self.request("GET", f"/documents/{_q(doc_id)}/content")

    def get_documents_doc_id_status(self, doc_id) -> Any:
        """Document indexing status  [GET /documents/{doc_id}/status, needs a read key]"""
        return self.request("GET", f"/documents/{_q(doc_id)}/status")

    def get_entitlements(self) -> Any:
        """Read plan and limits  [GET /entitlements, needs a read key]"""
        return self.request("GET", f"/entitlements")

    def get_inventory_bom_child_sku_used_in(self, child_sku) -> Any:
        """List where a component is used  [GET /inventory/bom/{child_sku}/used-in, needs a read key]"""
        return self.request("GET", f"/inventory/bom/{_q(child_sku)}/used-in")

    def get_inventory_bom_parent_sku(self, parent_sku) -> Any:
        """Read a bill of materials  [GET /inventory/bom/{parent_sku}, needs a read key]"""
        return self.request("GET", f"/inventory/bom/{_q(parent_sku)}")

    def get_inventory_cash_calendar(self, *, horizon_days: Any = None) -> Any:
        """Read the cash calendar  [GET /inventory/cash-calendar, needs a read key]"""
        return self.request("GET", f"/inventory/cash-calendar", query={"horizon_days": horizon_days})

    def get_inventory_dashboard_summary(self, *, session_id: Any) -> Any:
        """Read the dashboard summary  [GET /inventory/dashboard-summary, needs a read key]"""
        return self.request("GET", f"/inventory/dashboard-summary", query={"session_id": session_id})

    def get_inventory_dead_capital(self, *, window_days: Any = None) -> Any:
        """List dead capital  [GET /inventory/dead-capital, needs a read key]"""
        return self.request("GET", f"/inventory/dead-capital", query={"window_days": window_days})

    def get_inventory_events(self) -> Any:
        """List demand events  [GET /inventory/events, needs a read key]"""
        return self.request("GET", f"/inventory/events")

    def get_inventory_events_catalog(self, *, country: Any = None) -> Any:
        """Read the event catalog  [GET /inventory/events/catalog, needs a read key]"""
        return self.request("GET", f"/inventory/events/catalog", query={"country": country})

    def get_inventory_events_event_id_multipliers(self, event_id) -> Any:
        """List an event's multipliers  [GET /inventory/events/{event_id}/multipliers, needs a read key]"""
        return self.request("GET", f"/inventory/events/{_q(event_id)}/multipliers")

    def get_inventory_events_upcoming(self, *, days: Any = None) -> Any:
        """Upcoming demand events  [GET /inventory/events/upcoming, needs a read key]"""
        return self.request("GET", f"/inventory/events/upcoming", query={"days": days})

    def get_inventory_forecast_money(self, *, session_id: Any = None) -> Any:
        """Project revenue, cost and margin  [GET /inventory/forecast-money, needs a read key]"""
        return self.request("GET", f"/inventory/forecast-money", query={"session_id": session_id})

    def get_inventory_margin_erosion(self, *, window_days: Any = None, min_erosion_pts: Any = None) -> Any:
        """List margin erosion  [GET /inventory/margin-erosion, needs a read key]"""
        return self.request("GET", f"/inventory/margin-erosion", query={"window_days": window_days, "min_erosion_pts": min_erosion_pts})

    def get_inventory_morning_briefing(self, *, session_id: Any = None, service_level: Any = None) -> Any:
        """Read the morning briefing  [GET /inventory/morning-briefing, needs a read key]"""
        return self.request("GET", f"/inventory/morning-briefing", query={"session_id": session_id, "service_level": service_level})

    def get_inventory_optimize(self, *, session_id: Any = None, horizon_days: Any = None) -> Any:
        """Optimize purchases and transfers  [GET /inventory/optimize, needs a read key]"""
        return self.request("GET", f"/inventory/optimize", query={"session_id": session_id, "horizon_days": horizon_days})

    def get_inventory_po_history(self, *, limit: Any = None) -> Any:
        """List recent purchase orders  [GET /inventory/po-history, needs a read key]"""
        return self.request("GET", f"/inventory/po-history", query={"limit": limit})

    def get_inventory_po_history_page(self, *, limit: Any = None, offset: Any = None, status: Any = None, q: Any = None) -> Any:
        """Po History Page  [GET /inventory/po-history/page, needs a read key]"""
        return self.request("GET", f"/inventory/po-history/page", query={"limit": limit, "offset": offset, "status": status, "q": q})

    def get_inventory_po_import_template(self, *, format: Any = None) -> Any:
        """Download the purchase-order import template  [GET /inventory/po/import/template, needs a read key]"""
        return self.request("GET", f"/inventory/po/import/template", query={"format": format})

    def get_inventory_po_overdue(self) -> Any:
        """List overdue purchase orders  [GET /inventory/po/overdue, needs a read key]"""
        return self.request("GET", f"/inventory/po/overdue")

    def get_inventory_po_po_log_id_items(self, po_log_id) -> Any:
        """List a purchase order's lines  [GET /inventory/po/{po_log_id}/items, needs a read key]"""
        return self.request("GET", f"/inventory/po/{_q(po_log_id)}/items")

    def get_inventory_price_breaks(self, *, supplier_id: Any = None, sku: Any = None) -> Any:
        """List price breaks  [GET /inventory/price-breaks, needs a read key]"""
        return self.request("GET", f"/inventory/price-breaks", query={"supplier_id": supplier_id, "sku": sku})

    def get_inventory_product_types(self) -> Any:
        """List product types  [GET /inventory/product-types, needs a read key]"""
        return self.request("GET", f"/inventory/product-types")

    def get_inventory_production_requirements(self, *, session_id: Any, horizon_days: Any = None) -> Any:
        """Explode demand into components  [GET /inventory/production-requirements, needs a read key]"""
        return self.request("GET", f"/inventory/production-requirements", query={"session_id": session_id, "horizon_days": horizon_days})

    def get_inventory_recommendation_log_cost_of_ignoring(self, *, from_date: Any = None, to_date: Any = None, po_window_days: Any = None) -> Any:
        """Read the cost of ignoring recommendations  [GET /inventory/recommendation-log/cost-of-ignoring, needs a read key]"""
        return self.request("GET", f"/inventory/recommendation-log/cost-of-ignoring", query={"from_date": from_date, "to_date": to_date, "po_window_days": po_window_days})

    def get_inventory_recommendation_log_sku_why_changed(self, sku) -> Any:
        """Explain why a recommendation changed  [GET /inventory/recommendation-log/{sku}/why-changed, needs a read key]"""
        return self.request("GET", f"/inventory/recommendation-log/{_q(sku)}/why-changed")

    def get_inventory_report_pdf(self, *, session_id: Any, service_level: Any = None) -> Any:
        """Download the inventory PDF  [GET /inventory/report/pdf, needs a read key]"""
        return self.request("GET", f"/inventory/report/pdf", query={"session_id": session_id, "service_level": service_level})

    def get_inventory_roi(self) -> Any:
        """Read accumulated ROI  [GET /inventory/roi, needs a read key]"""
        return self.request("GET", f"/inventory/roi")

    def get_inventory_roi_month_report(self, *, year: Any = None, month: Any = None) -> Any:
        """Read one month's ROI report  [GET /inventory/roi/month-report, needs a read key]"""
        return self.request("GET", f"/inventory/roi/month-report", query={"year": year, "month": month})

    def get_inventory_roi_monthly(self, *, months: Any = None) -> Any:
        """List monthly ROI  [GET /inventory/roi/monthly, needs a read key]"""
        return self.request("GET", f"/inventory/roi/monthly", query={"months": months})

    def get_inventory_setup_gaps(self, *, session_id: Any = None, horizon_days: Any = None, limit: Any = None, target_pct: Any = None) -> Any:
        """List SKUs missing setup  [GET /inventory/setup-gaps, needs a read key]"""
        return self.request("GET", f"/inventory/setup-gaps", query={"session_id": session_id, "horizon_days": horizon_days, "limit": limit, "target_pct": target_pct})

    def get_inventory_shrinkage(self, *, sku: Any = None, limit: Any = None) -> Any:
        """List shrinkage  [GET /inventory/shrinkage, needs a read key]"""
        return self.request("GET", f"/inventory/shrinkage", query={"sku": sku, "limit": limit})

    def get_inventory_shrinkage_reasons(self) -> Any:
        """List shrinkage reasons  [GET /inventory/shrinkage/reasons, needs a read key]"""
        return self.request("GET", f"/inventory/shrinkage/reasons")

    def get_inventory_status(self, *, session_id: Any = None, service_level: Any = None, signal: Any = None, supplier: Any = None, abc: Any = None, by_warehouse: Any = None, limit: Any = None, offset: Any = None, sort: Any = None, order: Any = None, q: Any = None, skus: Any = None, without_forecast: Any = None) -> Any:
        """Read the inventory traffic light  [GET /inventory/status, needs a read key]"""
        return self.request("GET", f"/inventory/status", query={"session_id": session_id, "service_level": service_level, "signal": signal, "supplier": supplier, "abc": abc, "by_warehouse": by_warehouse, "limit": limit, "offset": offset, "sort": sort, "order": order, "q": q, "skus": skus, "without_forecast": without_forecast})

    def get_inventory_status_export_po(self, *, session_id: Any, service_level: Any = None, signals: Any = None, warehouse: Any = None) -> Any:
        """Export the purchase order as CSV  [GET /inventory/status/export-po, needs a read key]"""
        return self.request("GET", f"/inventory/status/export-po", query={"session_id": session_id, "service_level": service_level, "signals": signals, "warehouse": warehouse})

    def get_inventory_stock(self) -> Any:
        """List stock  [GET /inventory/stock, needs a read key]"""
        return self.request("GET", f"/inventory/stock")

    def get_inventory_stock_counts(self, *, status: Any = None, limit: Any = None) -> Any:
        """List Counts  [GET /inventory/stock-counts, needs a read key]"""
        return self.request("GET", f"/inventory/stock-counts", query={"status": status, "limit": limit})

    def get_inventory_stock_counts_count_id(self, count_id) -> Any:
        """Get Count  [GET /inventory/stock-counts/{count_id}, needs a read key]"""
        return self.request("GET", f"/inventory/stock-counts/{_q(count_id)}")

    def get_inventory_stock_counts_count_id_preview(self, count_id) -> Any:
        """Preview Count  [GET /inventory/stock-counts/{count_id}/preview, needs a read key]"""
        return self.request("GET", f"/inventory/stock-counts/{_q(count_id)}/preview")

    def get_inventory_stock_lookup(self, *, code: Any, warehouse: Any = None) -> Any:
        """Lookup Stock  [GET /inventory/stock/lookup, needs a read key]"""
        return self.request("GET", f"/inventory/stock/lookup", query={"code": code, "warehouse": warehouse})

    def get_inventory_stock_page(self, *, limit: Any = None, offset: Any = None, q: Any = None, warehouse: Any = None) -> Any:
        """List Stock Page  [GET /inventory/stock/page, needs a read key]"""
        return self.request("GET", f"/inventory/stock/page", query={"limit": limit, "offset": offset, "q": q, "warehouse": warehouse})

    def get_inventory_stock_sku(self, sku) -> Any:
        """Get a stock record  [GET /inventory/stock/{sku}, needs a read key]"""
        return self.request("GET", f"/inventory/stock/{_q(sku)}")

    def get_inventory_stock_sku_history(self, sku, *, days: Any = None, warehouse: Any = None) -> Any:
        """Read a SKU's stock history  [GET /inventory/stock/{sku}/history, needs a read key]"""
        return self.request("GET", f"/inventory/stock/{_q(sku)}/history", query={"days": days, "warehouse": warehouse})

    def get_inventory_stock_sku_suppliers(self, sku) -> Any:
        """A SKU's suppliers  [GET /inventory/stock/{sku}/suppliers, needs a read key]"""
        return self.request("GET", f"/inventory/stock/{_q(sku)}/suppliers")

    def get_inventory_supplier_cost_inflation(self, *, window_days: Any = None) -> Any:
        """List supplier cost inflation  [GET /inventory/supplier-cost-inflation, needs a read key]"""
        return self.request("GET", f"/inventory/supplier-cost-inflation", query={"window_days": window_days})

    def get_inventory_suppliers(self) -> Any:
        """List suppliers  [GET /inventory/suppliers, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers")

    def get_inventory_suppliers_contact_health(self) -> Any:
        """Check supplier contact details  [GET /inventory/suppliers/contact-health, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers/contact-health")

    def get_inventory_suppliers_import_template(self, *, format: Any = None) -> Any:
        """Download the suppliers import template  [GET /inventory/suppliers/import/template, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers/import/template", query={"format": format})

    def get_inventory_suppliers_lead_time_alerts(self) -> Any:
        """List supplier lead-time alerts  [GET /inventory/suppliers/lead-time-alerts, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers/lead-time-alerts")

    def get_inventory_suppliers_page(self, *, limit: Any = None, offset: Any = None, q: Any = None) -> Any:
        """List Suppliers Page  [GET /inventory/suppliers/page, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers/page", query={"limit": limit, "offset": offset, "q": q})

    def get_inventory_suppliers_scorecard(self) -> Any:
        """Read the supplier scorecard  [GET /inventory/suppliers/scorecard, needs a read key]"""
        return self.request("GET", f"/inventory/suppliers/scorecard")

    def get_inventory_sync_fields(self) -> Any:
        """Get Fields  [GET /inventory-sync/fields, needs a read key]"""
        return self.request("GET", f"/inventory-sync/fields")

    def get_inventory_sync_freshness(self) -> Any:
        """Get Freshness  [GET /inventory-sync/freshness, needs a read key]"""
        return self.request("GET", f"/inventory-sync/freshness")

    def get_inventory_sync_health(self, *, strict: Any = None) -> Any:
        """Tenant Health  [GET /inventory-sync/health, needs a read key]"""
        return self.request("GET", f"/inventory-sync/health", query={"strict": strict})

    def get_inventory_sync_ownership(self, *, skus: Any = None) -> Any:
        """Get Ownership  [GET /inventory-sync/ownership, needs a read key]"""
        return self.request("GET", f"/inventory-sync/ownership", query={"skus": skus})

    def get_inventory_sync_presets(self) -> Any:
        """List Presets  [GET /inventory-sync/presets, needs a write key]"""
        return self.request("GET", f"/inventory-sync/presets")

    def get_inventory_sync_profiles(self) -> Any:
        """List Profiles  [GET /inventory-sync/profiles, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles")

    def get_inventory_sync_profiles_profile_id(self, profile_id) -> Any:
        """Get Profile  [GET /inventory-sync/profiles/{profile_id}, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}")

    def get_inventory_sync_profiles_profile_id_export(self, profile_id, *, format: Any = None) -> Any:
        """Export Profile  [GET /inventory-sync/profiles/{profile_id}/export, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/export", query={"format": format})

    def get_inventory_sync_profiles_profile_id_health(self, profile_id, *, strict: Any = None) -> Any:
        """Profile Health  [GET /inventory-sync/profiles/{profile_id}/health, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/health", query={"strict": strict})

    def get_inventory_sync_profiles_profile_id_reconcile(self, profile_id, *, sku: Any, warehouse: Any = None) -> Any:
        """Reconcile  [GET /inventory-sync/profiles/{profile_id}/reconcile, needs a write key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/reconcile", query={"sku": sku, "warehouse": warehouse})

    def get_inventory_sync_profiles_profile_id_runs(self, profile_id, *, limit: Any = None) -> Any:
        """List Runs  [GET /inventory-sync/profiles/{profile_id}/runs, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/runs", query={"limit": limit})

    def get_inventory_sync_profiles_profile_id_runs_run_id(self, profile_id, run_id) -> Any:
        """Run Detail  [GET /inventory-sync/profiles/{profile_id}/runs/{run_id}, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/runs/{_q(run_id)}")

    def get_inventory_sync_profiles_profile_id_runs_run_id_rejected_csv(self, profile_id, run_id) -> Any:
        """Run Rejected Csv  [GET /inventory-sync/profiles/{profile_id}/runs/{run_id}/rejected.csv, needs a read key]"""
        return self.request("GET", f"/inventory-sync/profiles/{_q(profile_id)}/runs/{_q(run_id)}/rejected.csv")

    def get_inventory_sync_recipes(self, *, engine: Any = None, source_id: Any = None) -> Any:
        """Recipes  [GET /inventory-sync/recipes, needs a write key]"""
        return self.request("GET", f"/inventory-sync/recipes", query={"engine": engine, "source_id": source_id})

    def get_inventory_sync_schema_profile_json(self) -> Any:
        """Profile Schema  [GET /inventory-sync/schema/profile.json, needs a read key]"""
        return self.request("GET", f"/inventory-sync/schema/profile.json")

    def get_inventory_sync_sources(self) -> Any:
        """List Database Sources  [GET /inventory-sync/sources, needs a write key]"""
        return self.request("GET", f"/inventory-sync/sources")

    def get_inventory_sync_sources_source_id_columns(self, source_id, *, table: Any, schema: Any = None) -> Any:
        """Columns  [GET /inventory-sync/sources/{source_id}/columns, needs a write key]"""
        return self.request("GET", f"/inventory-sync/sources/{_q(source_id)}/columns", query={"table": table, "schema": schema})

    def get_inventory_sync_sources_source_id_discover(self, source_id, *, refresh: Any = None) -> Any:
        """Discover  [GET /inventory-sync/sources/{source_id}/discover, needs a write key]"""
        return self.request("GET", f"/inventory-sync/sources/{_q(source_id)}/discover", query={"refresh": refresh})

    def get_inventory_template_csv(self) -> Any:
        """Download the inventory import template  [GET /inventory/template.csv, needs a read key]"""
        return self.request("GET", f"/inventory/template.csv")

    def get_inventory_transfers(self, *, status: Any = None) -> Any:
        """List transfers  [GET /inventory/transfers, needs a read key]"""
        return self.request("GET", f"/inventory/transfers", query={"status": status})

    def get_inventory_warehouses(self) -> Any:
        """List warehouses  [GET /inventory/warehouses, needs a read key]"""
        return self.request("GET", f"/inventory/warehouses")

    def get_inventory_warehouses_lanes(self) -> Any:
        """List transfer lanes  [GET /inventory/warehouses/lanes, needs a read key]"""
        return self.request("GET", f"/inventory/warehouses/lanes")

    def get_jobs_active(self) -> Any:
        """List active jobs  [GET /jobs/active, needs a read key]"""
        return self.request("GET", f"/jobs/active")

    def get_jobs_job_id(self, job_id) -> Any:
        """Get a job  [GET /jobs/{job_id}, needs a read key]"""
        return self.request("GET", f"/jobs/{_q(job_id)}")

    def get_jobs_job_id_logs(self, job_id, *, tail: Any = None) -> Any:
        """Job logs  [GET /jobs/{job_id}/logs, needs a read key]"""
        return self.request("GET", f"/jobs/{_q(job_id)}/logs", query={"tail": tail})

    def get_models_available(self) -> Any:
        """Available forecasting models  [GET /models/available, needs a read key]"""
        return self.request("GET", f"/models/available")

    def get_planning(self) -> Any:
        """Planning context  [GET /planning, needs a read key]"""
        return self.request("GET", f"/planning")

    def get_purchase_orders_export(self, *, since: Any = None, limit: Any = None) -> Any:
        """Export Feed  [GET /purchase-orders/export, needs a read key]"""
        return self.request("GET", f"/purchase-orders/export", query={"since": since, "limit": limit})

    def get_purchase_orders_export_file(self, *, po_ids: Any, format: Any = None) -> Any:
        """Export File  [GET /purchase-orders/export/file, needs a read key]"""
        return self.request("GET", f"/purchase-orders/export/file", query={"po_ids": po_ids, "format": format})

    def get_purchase_orders_export_formats(self) -> Any:
        """Export Formats  [GET /purchase-orders/export/formats, needs a read key]"""
        return self.request("GET", f"/purchase-orders/export/formats")

    def get_purchase_orders_unacknowledged(self) -> Any:
        """Unacknowledged  [GET /purchase-orders/unacknowledged, needs a read key]"""
        return self.request("GET", f"/purchase-orders/unacknowledged")

    def get_scenarios_scenario_id(self, scenario_id) -> Any:
        """Get a scenario  [GET /scenarios/{scenario_id}, needs a read key]"""
        return self.request("GET", f"/scenarios/{_q(scenario_id)}")

    def get_schedules(self) -> Any:
        """List retraining schedules  [GET /schedules, needs a read key]"""
        return self.request("GET", f"/schedules")

    def get_schedules_history(self, *, limit: Any = None) -> Any:
        """List scheduler history  [GET /schedules/history, needs a read key]"""
        return self.request("GET", f"/schedules/history", query={"limit": limit})

    def get_sessions(self, *, skip: Any = None, limit: Any = None, archived: Any = None) -> Any:
        """List sessions  [GET /sessions, needs a read key]"""
        return self.request("GET", f"/sessions", query={"skip": skip, "limit": limit, "archived": archived})

    def get_sessions_session_id(self, session_id) -> Any:
        """Get a session  [GET /sessions/{session_id}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}")

    def get_sessions_session_id_accuracy(self, session_id, *, threshold: Any = None) -> Any:
        """Forecast accuracy  [GET /sessions/{session_id}/accuracy, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/accuracy", query={"threshold": threshold})

    def get_sessions_session_id_accuracy_tracking(self, session_id) -> Any:
        """Get Accuracy Tracking  [GET /sessions/{session_id}/accuracy-tracking, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/accuracy-tracking")

    def get_sessions_session_id_analysis(self, session_id) -> Any:
        """Analyze the attached dataset  [GET /sessions/{session_id}/analysis, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/analysis")

    def get_sessions_session_id_analyst_rag_status(self, session_id) -> Any:
        """Analyst index status  [GET /sessions/{session_id}/analyst/rag-status, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/analyst/rag-status")

    def get_sessions_session_id_artifacts(self, session_id) -> Any:
        """List training artifacts  [GET /sessions/{session_id}/artifacts, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/artifacts")

    def get_sessions_session_id_artifacts_download_artifact_path(self, session_id, artifact_path) -> Any:
        """Download a training artifact  [GET /sessions/{session_id}/artifacts/download/{artifact_path}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/artifacts/download/{_q(artifact_path)}")

    def get_sessions_session_id_available_models(self, session_id) -> Any:
        """Models available to a session  [GET /sessions/{session_id}/available-models, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/available-models")

    def get_sessions_session_id_columns(self, session_id) -> Any:
        """Read the column mapping (alias)  [GET /sessions/{session_id}/columns, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/columns")

    def get_sessions_session_id_config_business(self, session_id) -> Any:
        """Read business parameters  [GET /sessions/{session_id}/config/business, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/config/business")

    def get_sessions_session_id_config_forecast(self, session_id) -> Any:
        """Read forecast settings  [GET /sessions/{session_id}/config/forecast, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/config/forecast")

    def get_sessions_session_id_config_schema(self, session_id) -> Any:
        """Session configuration schema  [GET /sessions/{session_id}/config-schema, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/config-schema")

    def get_sessions_session_id_config_summary(self, session_id) -> Any:
        """Configuration summary  [GET /sessions/{session_id}/config-summary, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/config-summary")

    def get_sessions_session_id_configure_columns(self, session_id) -> Any:
        """Read the column mapping  [GET /sessions/{session_id}/configure/columns, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/configure/columns")

    def get_sessions_session_id_configure_features(self, session_id) -> Any:
        """Read feature settings  [GET /sessions/{session_id}/configure/features, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/configure/features")

    def get_sessions_session_id_configure_models(self, session_id) -> Any:
        """Read model selection  [GET /sessions/{session_id}/configure/models, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/configure/models")

    def get_sessions_session_id_configure_remediations(self, session_id) -> Any:
        """Read data remediations  [GET /sessions/{session_id}/configure/remediations, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/configure/remediations")

    def get_sessions_session_id_configure_validation(self, session_id) -> Any:
        """Read validation settings  [GET /sessions/{session_id}/configure/validation, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/configure/validation")

    def get_sessions_session_id_data_gate(self, session_id) -> Any:
        """Read the data-quality gate  [GET /sessions/{session_id}/data-gate, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/data-gate")

    def get_sessions_session_id_decomposition_sku(self, session_id, sku, *, granularity: Any = None) -> Any:
        """Decompose a SKU's history  [GET /sessions/{session_id}/decomposition/{sku}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/decomposition/{_q(sku)}", query={"granularity": granularity})

    def get_sessions_session_id_export_config(self, session_id) -> Any:
        """Export configuration (alias)  [GET /sessions/{session_id}/export-config, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/export-config")

    def get_sessions_session_id_forecast_series_sku(self, session_id, sku, *, model: Any = None) -> Any:
        """Forecast series for a SKU  [GET /sessions/{session_id}/forecast-series/{sku}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/forecast-series/{_q(sku)}", query={"model": model})

    def get_sessions_session_id_forecast_total(self, session_id, *, granularity: Any = None) -> Any:
        """Get Forecast Total  [GET /sessions/{session_id}/forecast-total, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/forecast-total", query={"granularity": granularity})

    def get_sessions_session_id_forecast_vs_actual(self, session_id, *, dataset_id: Any = None) -> Any:
        """Compare forecast with actual sales  [GET /sessions/{session_id}/forecast-vs-actual, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/forecast-vs-actual", query={"dataset_id": dataset_id})

    def get_sessions_session_id_guided_reading(self, session_id, *, decisions: Any = None, mapping: Any = None) -> Any:
        """Get Guided Reading  [GET /sessions/{session_id}/guided-reading, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/guided-reading", query={"decisions": decisions, "mapping": mapping})

    def get_sessions_session_id_health(self, session_id, *, refresh: Any = None) -> Any:
        """Check dataset health  [GET /sessions/{session_id}/health, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/health", query={"refresh": refresh})

    def get_sessions_session_id_inspect(self, session_id) -> Any:
        """Inspect the attached dataset  [GET /sessions/{session_id}/inspect, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/inspect")

    def get_sessions_session_id_inventory(self, session_id) -> Any:
        """Session inventory recommendations  [GET /sessions/{session_id}/inventory, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/inventory")

    def get_sessions_session_id_jobs(self, session_id) -> Any:
        """List a session's jobs  [GET /sessions/{session_id}/jobs, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/jobs")

    def get_sessions_session_id_manifest(self, session_id) -> Any:
        """Get Manifest  [GET /sessions/{session_id}/manifest, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/manifest")

    def get_sessions_session_id_metrics(self, session_id) -> Any:
        """Training metrics  [GET /sessions/{session_id}/metrics, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/metrics")

    def get_sessions_session_id_models_hyperparams(self, session_id) -> Any:
        """List model hyperparameters  [GET /sessions/{session_id}/models/hyperparams, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/models/hyperparams")

    def get_sessions_session_id_overrides(self, session_id) -> Any:
        """List forecast overrides  [GET /sessions/{session_id}/overrides, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/overrides")

    def get_sessions_session_id_profile(self, session_id) -> Any:
        """Dataset profile (alias)  [GET /sessions/{session_id}/profile, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/profile")

    def get_sessions_session_id_quality(self, session_id) -> Any:
        """Data quality  [GET /sessions/{session_id}/quality, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/quality")

    def get_sessions_session_id_reforecast_status(self, session_id) -> Any:
        """Reforecast Status  [GET /sessions/{session_id}/reforecast/status, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/reforecast/status")

    def get_sessions_session_id_report(self, session_id) -> Any:
        """Session report (alias)  [GET /sessions/{session_id}/report, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/report")

    def get_sessions_session_id_reports_format(self, session_id, format) -> Any:
        """Download a report  [GET /sessions/{session_id}/reports/{format}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/reports/{_q(format)}")

    def get_sessions_session_id_reports_status(self, session_id) -> Any:
        """Read report generation status  [GET /sessions/{session_id}/reports/status, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/reports/status")

    def get_sessions_session_id_results(self, session_id, *, level: Any = None) -> Any:
        """Training results  [GET /sessions/{session_id}/results, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/results", query={"level": level})

    def get_sessions_session_id_routing(self, session_id) -> Any:
        """Model routing  [GET /sessions/{session_id}/routing, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/routing")

    def get_sessions_session_id_routing_plan(self, session_id) -> Any:
        """Model routing plan (alias)  [GET /sessions/{session_id}/routing-plan, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/routing-plan")

    def get_sessions_session_id_scenarios(self, session_id) -> Any:
        """List scenarios  [GET /sessions/{session_id}/scenarios, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/scenarios")

    def get_sessions_session_id_schedule(self, session_id) -> Any:
        """Get a retraining schedule  [GET /sessions/{session_id}/schedule, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/schedule")

    def get_sessions_session_id_shap_sku(self, session_id, sku) -> Any:
        """Read a SKU's feature importances  [GET /sessions/{session_id}/shap/{sku}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/shap/{_q(sku)}")

    def get_sessions_session_id_sku_intelligence_sku(self, session_id, sku, *, model: Any = None, granularity: Any = None, agg: Any = None) -> Any:
        """SKU intelligence  [GET /sessions/{session_id}/sku-intelligence/{sku}, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/sku-intelligence/{_q(sku)}", query={"model": model, "granularity": granularity, "agg": agg})

    def get_sessions_session_id_train_status(self, session_id) -> Any:
        """Read training status  [GET /sessions/{session_id}/train/status, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/train/status")

    def get_sessions_session_id_warnings(self, session_id) -> Any:
        """List run warnings  [GET /sessions/{session_id}/warnings, needs a read key]"""
        return self.request("GET", f"/sessions/{_q(session_id)}/warnings")

    def get_sessions_summary(self, *, skip: Any = None, limit: Any = None, q: Any = None, status: Any = None, dataset_id: Any = None, archived: Any = None, created_from: Any = None, created_to: Any = None, sort: Any = None, order: Any = None) -> Any:
        """List session summaries  [GET /sessions/summary, needs a read key]"""
        return self.request("GET", f"/sessions/summary", query={"skip": skip, "limit": limit, "q": q, "status": status, "dataset_id": dataset_id, "archived": archived, "created_from": created_from, "created_to": created_to, "sort": sort, "order": order})

    def get_tenant_currency(self) -> Any:
        """Read the currency  [GET /tenant/currency, needs a read key]"""
        return self.request("GET", f"/tenant/currency")

    def get_tenant_timezone(self) -> Any:
        """Read the time zone  [GET /tenant/timezone, needs a read key]"""
        return self.request("GET", f"/tenant/timezone")

    def get_training_run_durations(self, *, limit: Any = None) -> Any:
        """Get Run Durations  [GET /training/run-durations, needs a read key]"""
        return self.request("GET", f"/training/run-durations", query={"limit": limit})

    def get_webhooks(self) -> Any:
        """List webhooks  [GET /webhooks, needs a read key]"""
        return self.request("GET", f"/webhooks")

    def get_webhooks_events(self) -> Any:
        """List webhook events  [GET /webhooks/events, needs a read key]"""
        return self.request("GET", f"/webhooks/events")

    def get_webhooks_webhook_id_deliveries(self, webhook_id, *, status: Any = None, limit: Any = None) -> Any:
        """List webhook deliveries  [GET /webhooks/{webhook_id}/deliveries, needs a read key]"""
        return self.request("GET", f"/webhooks/{_q(webhook_id)}/deliveries", query={"status": status, "limit": limit})

    def patch_data_sources_source_id(self, source_id, *, body: Any) -> Any:
        """Rename a data source  [PATCH /data-sources/{source_id}, needs a write key]"""
        return self.request("PATCH", f"/data-sources/{_q(source_id)}", body=body)

    def patch_data_sources_source_id_query(self, source_id, *, body: Any) -> Any:
        """Save a SQL source's query  [PATCH /data-sources/{source_id}/query, needs a write key]"""
        return self.request("PATCH", f"/data-sources/{_q(source_id)}/query", body=body)

    def patch_data_sources_source_id_sql_config(self, source_id, *, body: Any) -> Any:
        """Update a SQL source's connection  [PATCH /data-sources/{source_id}/sql-config, needs a write key]"""
        return self.request("PATCH", f"/data-sources/{_q(source_id)}/sql-config", body=body)

    def patch_inventory_events_catalog_catalog_key(self, catalog_key, *, body: Any) -> Any:
        """Toggle a catalog event  [PATCH /inventory/events/catalog/{catalog_key}, needs a write key]"""
        return self.request("PATCH", f"/inventory/events/catalog/{_q(catalog_key)}", body=body)

    def patch_inventory_events_event_id(self, event_id, *, body: Any) -> Any:
        """Update a demand event  [PATCH /inventory/events/{event_id}, needs a write key]"""
        return self.request("PATCH", f"/inventory/events/{_q(event_id)}", body=body)

    def patch_inventory_stock_sku(self, sku, *, body: Any) -> Any:
        """Update part of a stock record  [PATCH /inventory/stock/{sku}, needs a write key]"""
        return self.request("PATCH", f"/inventory/stock/{_q(sku)}", body=body)

    def patch_inventory_stock_sku_product_type(self, sku, *, product_type: Any) -> Any:
        """Set a SKU's product type  [PATCH /inventory/stock/{sku}/product-type, needs a write key]"""
        return self.request("PATCH", f"/inventory/stock/{_q(sku)}/product-type", query={"product_type": product_type})

    def patch_inventory_suppliers_supplier_id(self, supplier_id, *, body: Any) -> Any:
        """Update a supplier  [PATCH /inventory/suppliers/{supplier_id}, needs a write key]"""
        return self.request("PATCH", f"/inventory/suppliers/{_q(supplier_id)}", body=body)

    def patch_inventory_sync_profiles_profile_id(self, profile_id, *, body: Any) -> Any:
        """Update Profile  [PATCH /inventory-sync/profiles/{profile_id}, needs a write key]"""
        return self.request("PATCH", f"/inventory-sync/profiles/{_q(profile_id)}", body=body)

    def patch_inventory_warehouses_name(self, name, *, body: Any) -> Any:
        """Update a warehouse  [PATCH /inventory/warehouses/{name}, needs a write key]"""
        return self.request("PATCH", f"/inventory/warehouses/{_q(name)}", body=body)

    def patch_sessions_session_id(self, session_id, *, body: Any) -> Any:
        """Update a session  [PATCH /sessions/{session_id}, needs a write key]"""
        return self.request("PATCH", f"/sessions/{_q(session_id)}", body=body)

    def patch_sessions_session_id_overrides(self, session_id, *, body: Any) -> Any:
        """Save forecast overrides  [PATCH /sessions/{session_id}/overrides, needs a write key]"""
        return self.request("PATCH", f"/sessions/{_q(session_id)}/overrides", body=body)

    def post_ai_narrative_forecast_explanation(self, *, body: Any) -> Any:
        """Explain a SKU's recommendation  [POST /ai/narrative/forecast-explanation, needs a read key]"""
        return self.request("POST", f"/ai/narrative/forecast-explanation", body=body)

    def post_ai_narrative_inventory(self, *, body: Any) -> Any:
        """Write an inventory insight  [POST /ai/narrative/inventory, needs a read key]"""
        return self.request("POST", f"/ai/narrative/inventory", body=body)

    def post_ai_narrative_morning(self, *, body: Any) -> Any:
        """Write the morning briefing narrative  [POST /ai/narrative/morning, needs a read key]"""
        return self.request("POST", f"/ai/narrative/morning", body=body)

    def post_ai_suggested_questions(self, *, body: Any) -> Any:
        """Suggest questions for the analyst  [POST /ai/suggested-questions, needs a read key]"""
        return self.request("POST", f"/ai/suggested-questions", body=body)

    def post_data_sources_file(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Create a file data source  [POST /data-sources/file, needs a write key]"""
        return self.request("POST", f"/data-sources/file", body=body, files=files)

    def post_data_sources_source_id_execute_query(self, source_id, *, body: Any) -> Any:
        """Run a SQL query against a SQL source  [POST /data-sources/{source_id}/execute-query, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/execute-query", body=body)

    def post_data_sources_source_id_export_query(self, source_id, *, body: Any) -> Any:
        """Export a SQL query as Excel or CSV  [POST /data-sources/{source_id}/export-query, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/export-query", body=body)

    def post_data_sources_source_id_file(self, source_id, *, files: dict, body: Optional[dict] = None) -> Any:
        """Replace a data source's file  [POST /data-sources/{source_id}/file, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/file", body=body, files=files)

    def post_data_sources_source_id_materialize(self, source_id, *, body: Any) -> Any:
        """Materialize a SQL query as a dataset  [POST /data-sources/{source_id}/materialize, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/materialize", body=body)

    def post_data_sources_source_id_save_as_new(self, source_id, *, body: Any) -> Any:
        """Save an edited table as a new source  [POST /data-sources/{source_id}/save-as-new, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/save-as-new", body=body)

    def post_data_sources_source_id_test_connection(self, source_id) -> Any:
        """Test a SQL source's connection  [POST /data-sources/{source_id}/test-connection, needs a write key]"""
        return self.request("POST", f"/data-sources/{_q(source_id)}/test-connection")

    def post_data_sources_sql(self, *, body: Any) -> Any:
        """Create a SQL data source  [POST /data-sources/sql, needs a write key]"""
        return self.request("POST", f"/data-sources/sql", body=body)

    def post_data_sources_sql_parse(self, *, body: Any) -> Any:
        """Parse a connection string  [POST /data-sources/sql/parse, needs a write key]"""
        return self.request("POST", f"/data-sources/sql/parse", body=body)

    def post_datasets(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Upload a dataset  [POST /datasets, needs a write key]"""
        return self.request("POST", f"/datasets", body=body, files=files)

    def post_documents(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Upload a document  [POST /documents, needs a write key]"""
        return self.request("POST", f"/documents", body=body, files=files)

    def post_inventory_alerts_send_now(self, *, session_id: Any) -> Any:
        """Send the daily alert now  [POST /inventory/alerts/send-now, needs a write key]"""
        return self.request("POST", f"/inventory/alerts/send-now", query={"session_id": session_id})

    def post_inventory_bulk(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Import stock from a file  [POST /inventory/bulk, needs a write key]"""
        return self.request("POST", f"/inventory/bulk", body=body, files=files)

    def post_inventory_bulk_preview(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Preview a stock import  [POST /inventory/bulk/preview, needs a write key]"""
        return self.request("POST", f"/inventory/bulk/preview", body=body, files=files)

    def post_inventory_cash_calendar_fit(self, *, horizon_days: Any = None, session_id: Any = None, body: Any = None) -> Any:
        """Fit a purchase into a budget  [POST /inventory/cash-calendar/fit, needs a read key]"""
        return self.request("POST", f"/inventory/cash-calendar/fit", query={"horizon_days": horizon_days, "session_id": session_id}, body=body)

    def post_inventory_events(self, *, body: Any) -> Any:
        """Create a demand event  [POST /inventory/events, needs a write key]"""
        return self.request("POST", f"/inventory/events", body=body)

    def post_inventory_events_catalog_seed(self, *, body: Any) -> Any:
        """Seed the event catalog  [POST /inventory/events/catalog/seed, needs a write key]"""
        return self.request("POST", f"/inventory/events/catalog/seed", body=body)

    def post_inventory_events_simulate(self, *, body: Any) -> Any:
        """Simulate a demand event  [POST /inventory/events/simulate, needs a read key]"""
        return self.request("POST", f"/inventory/events/simulate", body=body)

    def post_inventory_log_po(self, *, session_id: Any, idempotency_key: Any = None, body: Any = None) -> Any:
        """Record a purchase order  [POST /inventory/log-po, needs a write key]"""
        return self.request("POST", f"/inventory/log-po", query={"session_id": session_id}, headers={"Idempotency-Key": idempotency_key}, body=body)

    def post_inventory_po(self, *, idempotency_key: Any = None, body: Any) -> Any:
        """Create a manual purchase order  [POST /inventory/po, needs a write key]"""
        return self.request("POST", f"/inventory/po", headers={"Idempotency-Key": idempotency_key}, body=body)

    def post_inventory_po_import(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Import purchase orders from a file  [POST /inventory/po/import, needs a write key]"""
        return self.request("POST", f"/inventory/po/import", body=body, files=files)

    def post_inventory_po_import_preview(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Preview a purchase-order import  [POST /inventory/po/import/preview, needs a write key]"""
        return self.request("POST", f"/inventory/po/import/preview", body=body, files=files)

    def post_inventory_po_po_log_id_receive(self, po_log_id, *, body: Any = None) -> Any:
        """Receive a purchase order  [POST /inventory/po/{po_log_id}/receive, needs a write key]"""
        return self.request("POST", f"/inventory/po/{_q(po_log_id)}/receive", body=body)

    def post_inventory_po_po_log_id_send(self, po_log_id, *, body: Any = None) -> Any:
        """Send a purchase order to suppliers  [POST /inventory/po/{po_log_id}/send, needs a write key]"""
        return self.request("POST", f"/inventory/po/{_q(po_log_id)}/send", body=body)

    def post_inventory_po_po_log_id_unreceive(self, po_log_id) -> Any:
        """Undo a purchase-order reception  [POST /inventory/po/{po_log_id}/unreceive, needs a write key]"""
        return self.request("POST", f"/inventory/po/{_q(po_log_id)}/unreceive")

    def post_inventory_po_po_log_id_unsend(self, po_log_id) -> Any:
        """Undo marking a purchase order as sent  [POST /inventory/po/{po_log_id}/unsend, needs a write key]"""
        return self.request("POST", f"/inventory/po/{_q(po_log_id)}/unsend")

    def post_inventory_price_breaks_evaluate(self, *, session_id: Any, body: Any = None) -> Any:
        """Evaluate price breaks for a cart  [POST /inventory/price-breaks/evaluate, needs a read key]"""
        return self.request("POST", f"/inventory/price-breaks/evaluate", query={"session_id": session_id}, body=body)

    def post_inventory_shrinkage(self, *, body: Any) -> Any:
        """Record shrinkage  [POST /inventory/shrinkage, needs a write key]"""
        return self.request("POST", f"/inventory/shrinkage", body=body)

    def post_inventory_stock_counts(self, *, body: Any) -> Any:
        """Create Count  [POST /inventory/stock-counts, needs a write key]"""
        return self.request("POST", f"/inventory/stock-counts", body=body)

    def post_inventory_stock_counts_count_id_apply(self, count_id, *, body: Any = None) -> Any:
        """Apply Count  [POST /inventory/stock-counts/{count_id}/apply, needs a write key]"""
        return self.request("POST", f"/inventory/stock-counts/{_q(count_id)}/apply", body=body)

    def post_inventory_stock_counts_count_id_cancel(self, count_id) -> Any:
        """Cancel Count  [POST /inventory/stock-counts/{count_id}/cancel, needs a write key]"""
        return self.request("POST", f"/inventory/stock-counts/{_q(count_id)}/cancel")

    def post_inventory_stock_counts_count_id_close(self, count_id) -> Any:
        """Close Count  [POST /inventory/stock-counts/{count_id}/close, needs a write key]"""
        return self.request("POST", f"/inventory/stock-counts/{_q(count_id)}/close")

    def post_inventory_suppliers(self, *, body: Any) -> Any:
        """Create a supplier  [POST /inventory/suppliers, needs a write key]"""
        return self.request("POST", f"/inventory/suppliers", body=body)

    def post_inventory_suppliers_import(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Import suppliers from a file  [POST /inventory/suppliers/import, needs a write key]"""
        return self.request("POST", f"/inventory/suppliers/import", body=body, files=files)

    def post_inventory_suppliers_import_preview(self, *, files: dict, body: Optional[dict] = None) -> Any:
        """Preview a suppliers import  [POST /inventory/suppliers/import/preview, needs a write key]"""
        return self.request("POST", f"/inventory/suppliers/import/preview", body=body, files=files)

    def post_inventory_suppliers_supplier_id_price_breaks(self, supplier_id, *, body: Any) -> Any:
        """Set a price break  [POST /inventory/suppliers/{supplier_id}/price-breaks, needs a write key]"""
        return self.request("POST", f"/inventory/suppliers/{_q(supplier_id)}/price-breaks", body=body)

    def post_inventory_suppliers_supplier_id_reactivate(self, supplier_id) -> Any:
        """Reactivate a supplier  [POST /inventory/suppliers/{supplier_id}/reactivate, needs a write key]"""
        return self.request("POST", f"/inventory/suppliers/{_q(supplier_id)}/reactivate")

    def post_inventory_sync_doctor(self, *, body: Any) -> Any:
        """Draft Doctor  [POST /inventory-sync/doctor, needs a write key]"""
        return self.request("POST", f"/inventory-sync/doctor", body=body)

    def post_inventory_sync_dry_run(self, *, body: Any) -> Any:
        """Dry Run Unsaved  [POST /inventory-sync/dry-run, needs a write key]"""
        return self.request("POST", f"/inventory-sync/dry-run", body=body)

    def post_inventory_sync_presets_preset_id_render(self, preset_id, *, body: Any) -> Any:
        """Render Preset  [POST /inventory-sync/presets/{preset_id}/render, needs a write key]"""
        return self.request("POST", f"/inventory-sync/presets/{_q(preset_id)}/render", body=body)

    def post_inventory_sync_profiles(self, *, idempotency_key: Any = None, body: Any) -> Any:
        """Create Profile  [POST /inventory-sync/profiles, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles", headers={"Idempotency-Key": idempotency_key}, body=body)

    def post_inventory_sync_profiles_apply(self, *, dry_run: Any = None, idempotency_key: Any = None) -> Any:
        """Apply Document  [POST /inventory-sync/profiles/apply, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/apply", query={"dry_run": dry_run}, headers={"Idempotency-Key": idempotency_key})

    def post_inventory_sync_profiles_profile_id_disable(self, profile_id) -> Any:
        """Disable Profile  [POST /inventory-sync/profiles/{profile_id}/disable, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/disable")

    def post_inventory_sync_profiles_profile_id_doctor(self, profile_id) -> Any:
        """Profile Doctor  [POST /inventory-sync/profiles/{profile_id}/doctor, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/doctor")

    def post_inventory_sync_profiles_profile_id_dry_run(self, profile_id) -> Any:
        """Dry Run Saved  [POST /inventory-sync/profiles/{profile_id}/dry-run, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/dry-run")

    def post_inventory_sync_profiles_profile_id_enable(self, profile_id) -> Any:
        """Enable Profile  [POST /inventory-sync/profiles/{profile_id}/enable, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/enable")

    def post_inventory_sync_profiles_profile_id_ingest(self, profile_id, *, batch_id: Any = None, snapshot_id: Any = None, last: Any = None, dry_run: Any = None) -> Any:
        """Ingest Rows  [POST /inventory-sync/profiles/{profile_id}/ingest, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/ingest", query={"batch_id": batch_id, "snapshot_id": snapshot_id, "last": last, "dry_run": dry_run})

    def post_inventory_sync_profiles_profile_id_run(self, profile_id, *, idempotency_key: Any = None, body: Any = None) -> Any:
        """Run Now  [POST /inventory-sync/profiles/{profile_id}/run, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/{_q(profile_id)}/run", headers={"Idempotency-Key": idempotency_key}, body=body)

    def post_inventory_sync_profiles_validate(self) -> Any:
        """Validate Document  [POST /inventory-sync/profiles/validate, needs a write key]"""
        return self.request("POST", f"/inventory-sync/profiles/validate")

    def post_inventory_sync_refresh(self, *, idempotency_key: Any = None, body: Any) -> Any:
        """Refresh  [POST /inventory-sync/refresh, needs a write key]"""
        return self.request("POST", f"/inventory-sync/refresh", headers={"Idempotency-Key": idempotency_key}, body=body)

    def post_inventory_sync_sources_source_id_inspect(self, source_id, *, body: Any) -> Any:
        """Inspect  [POST /inventory-sync/sources/{source_id}/inspect, needs a write key]"""
        return self.request("POST", f"/inventory-sync/sources/{_q(source_id)}/inspect", body=body)

    def post_inventory_sync_sources_source_id_suggest_view(self, source_id, *, body: Any) -> Any:
        """Suggest View  [POST /inventory-sync/sources/{source_id}/suggest-view, needs a write key]"""
        return self.request("POST", f"/inventory-sync/sources/{_q(source_id)}/suggest-view", body=body)

    def post_inventory_sync_transform_test(self, *, body: Any) -> Any:
        """Transform Test  [POST /inventory-sync/transform-test, needs a write key]"""
        return self.request("POST", f"/inventory-sync/transform-test", body=body)

    def post_inventory_transfers(self, *, body: Any) -> Any:
        """Create a transfer  [POST /inventory/transfers, needs a write key]"""
        return self.request("POST", f"/inventory/transfers", body=body)

    def post_inventory_transfers_transfer_id_cancel(self, transfer_id) -> Any:
        """Cancel a transfer  [POST /inventory/transfers/{transfer_id}/cancel, needs a write key]"""
        return self.request("POST", f"/inventory/transfers/{_q(transfer_id)}/cancel")

    def post_inventory_transfers_transfer_id_close(self, transfer_id) -> Any:
        """Close a transfer  [POST /inventory/transfers/{transfer_id}/close, needs a write key]"""
        return self.request("POST", f"/inventory/transfers/{_q(transfer_id)}/close")

    def post_inventory_transfers_transfer_id_receive(self, transfer_id, *, body: Any) -> Any:
        """Receive a transfer  [POST /inventory/transfers/{transfer_id}/receive, needs a write key]"""
        return self.request("POST", f"/inventory/transfers/{_q(transfer_id)}/receive", body=body)

    def post_inventory_warehouses(self, *, body: Any) -> Any:
        """Create a warehouse  [POST /inventory/warehouses, needs a write key]"""
        return self.request("POST", f"/inventory/warehouses", body=body)

    def post_mcp(self, *, mcp_protocol_version: Any = None, body: Any) -> Any:
        """Call the MCP server (JSON-RPC)  [POST /mcp, needs a read key]"""
        return self.request("POST", f"/mcp", headers={"MCP-Protocol-Version": mcp_protocol_version}, body=body)

    def post_purchase_orders_po_log_id_erp_reference(self, po_log_id, *, body: Any) -> Any:
        """Set Erp Reference  [POST /purchase-orders/{po_log_id}/erp-reference, needs a write key]"""
        return self.request("POST", f"/purchase-orders/{_q(po_log_id)}/erp-reference", body=body)

    def post_sessions(self, *, body: Any) -> Any:
        """Create a session  [POST /sessions, needs a write key]"""
        return self.request("POST", f"/sessions", body=body)

    def post_sessions_session_id_analyst_narrate(self, session_id, *, body: Any) -> Any:
        """Put data into words  [POST /sessions/{session_id}/analyst/narrate, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/analyst/narrate", body=body)

    def post_sessions_session_id_analyst_query(self, session_id, *, body: Any) -> Any:
        """Ask the analyst about a session  [POST /sessions/{session_id}/analyst/query, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/analyst/query", body=body)

    def post_sessions_session_id_backtest(self, session_id, *, body: Any) -> Any:
        """Start Backtest  [POST /sessions/{session_id}/backtest, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/backtest", body=body)

    def post_sessions_session_id_columns(self, session_id, *, body: Any) -> Any:
        """Save the column mapping (alias)  [POST /sessions/{session_id}/columns, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/columns", body=body)

    def post_sessions_session_id_config_business(self, session_id, *, body: Any) -> Any:
        """Save business parameters  [POST /sessions/{session_id}/config/business, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/config/business", body=body)

    def post_sessions_session_id_config_features(self, session_id, *, body: Any) -> Any:
        """Save feature settings (alias)  [POST /sessions/{session_id}/config/features, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/config/features", body=body)

    def post_sessions_session_id_config_forecast(self, session_id, *, body: Any) -> Any:
        """Save forecast settings  [POST /sessions/{session_id}/config/forecast, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/config/forecast", body=body)

    def post_sessions_session_id_config_models(self, session_id, *, body: Any) -> Any:
        """Save model selection (alias)  [POST /sessions/{session_id}/config/models, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/config/models", body=body)

    def post_sessions_session_id_config_training(self, session_id, *, body: Any) -> Any:
        """Save validation settings (alias)  [POST /sessions/{session_id}/config/training, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/config/training", body=body)

    def post_sessions_session_id_configure_columns(self, session_id, *, body: Any) -> Any:
        """Save the column mapping  [POST /sessions/{session_id}/configure/columns, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/columns", body=body)

    def post_sessions_session_id_configure_features(self, session_id, *, body: Any) -> Any:
        """Save feature settings  [POST /sessions/{session_id}/configure/features, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/features", body=body)

    def post_sessions_session_id_configure_guided_reading(self, session_id, *, body: Any) -> Any:
        """Configure Guided Reading  [POST /sessions/{session_id}/configure/guided-reading, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/guided-reading", body=body)

    def post_sessions_session_id_configure_models(self, session_id, *, body: Any) -> Any:
        """Save model selection  [POST /sessions/{session_id}/configure/models, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/models", body=body)

    def post_sessions_session_id_configure_remediations(self, session_id, *, body: Any) -> Any:
        """Answer the data-quality gate  [POST /sessions/{session_id}/configure/remediations, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/remediations", body=body)

    def post_sessions_session_id_configure_validation(self, session_id, *, body: Any) -> Any:
        """Save validation settings  [POST /sessions/{session_id}/configure/validation, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/configure/validation", body=body)

    def post_sessions_session_id_dataset(self, session_id, *, body: Any) -> Any:
        """Attach a dataset  [POST /sessions/{session_id}/dataset, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/dataset", body=body)

    def post_sessions_session_id_drift(self, session_id, *, files: dict, body: Optional[dict] = None) -> Any:
        """Detect drift  [POST /sessions/{session_id}/drift, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/drift", body=body, files=files)

    def post_sessions_session_id_predict(self, session_id, *, body: Any) -> Any:
        """Predict on demand  [POST /sessions/{session_id}/predict, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/predict", body=body)

    def post_sessions_session_id_reconcile(self, session_id, *, date_col: Any = None, sku_col: Any = None, target_col: Any = None, files: dict, body: Optional[dict] = None) -> Any:
        """Reconcile actual values  [POST /sessions/{session_id}/reconcile, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/reconcile", query={"date_col": date_col, "sku_col": sku_col, "target_col": target_col}, body=body, files=files)

    def post_sessions_session_id_reforecast(self, session_id, *, body: Any = None) -> Any:
        """Start Reforecast  [POST /sessions/{session_id}/reforecast, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/reforecast", body=body)

    def post_sessions_session_id_reports_generate(self, session_id, *, body: Any) -> Any:
        """Generate a report  [POST /sessions/{session_id}/reports/generate, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/reports/generate", body=body)

    def post_sessions_session_id_restore(self, session_id) -> Any:
        """Restore Session  [POST /sessions/{session_id}/restore, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/restore")

    def post_sessions_session_id_scenarios(self, session_id, *, body: Any) -> Any:
        """Save a scenario  [POST /sessions/{session_id}/scenarios, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/scenarios", body=body)

    def post_sessions_session_id_scenarios_preview(self, session_id, *, top_changes: Any = None, body: Any) -> Any:
        """Preview a scenario  [POST /sessions/{session_id}/scenarios/preview, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/scenarios/preview", query={"top_changes": top_changes}, body=body)

    def post_sessions_session_id_scenarios_scenario_id_run(self, session_id, scenario_id, *, top_changes: Any = None) -> Any:
        """Run a saved scenario  [POST /sessions/{session_id}/scenarios/{scenario_id}/run, needs a read key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/scenarios/{_q(scenario_id)}/run", query={"top_changes": top_changes})

    def post_sessions_session_id_schedule(self, session_id, *, body: Any) -> Any:
        """Save a retraining schedule  [POST /sessions/{session_id}/schedule, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/schedule", body=body)

    def post_sessions_session_id_train(self, session_id, *, body: Any = None) -> Any:
        """Start training  [POST /sessions/{session_id}/train, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/train", body=body)

    def post_sessions_session_id_upload(self, session_id, *, files: dict, body: Optional[dict] = None) -> Any:
        """Upload and attach a dataset  [POST /sessions/{session_id}/upload, needs a write key]"""
        return self.request("POST", f"/sessions/{_q(session_id)}/upload", body=body, files=files)

    def post_webhooks(self, *, body: Any) -> Any:
        """Create a webhook  [POST /webhooks, needs a write key]"""
        return self.request("POST", f"/webhooks", body=body)

    def post_webhooks_webhook_id_enable(self, webhook_id) -> Any:
        """Re-enable a webhook  [POST /webhooks/{webhook_id}/enable, needs a write key]"""
        return self.request("POST", f"/webhooks/{_q(webhook_id)}/enable")

    def post_webhooks_webhook_id_rotate_secret(self, webhook_id) -> Any:
        """Rotate a webhook secret  [POST /webhooks/{webhook_id}/rotate-secret, needs a write key]"""
        return self.request("POST", f"/webhooks/{_q(webhook_id)}/rotate-secret")

    def post_webhooks_webhook_id_test(self, webhook_id) -> Any:
        """Send a test event  [POST /webhooks/{webhook_id}/test, needs a write key]"""
        return self.request("POST", f"/webhooks/{_q(webhook_id)}/test")

    def put_inventory_bom_parent_sku_child_sku(self, parent_sku, child_sku, *, body: Any) -> Any:
        """Set a bill-of-materials line  [PUT /inventory/bom/{parent_sku}/{child_sku}, needs a write key]"""
        return self.request("PUT", f"/inventory/bom/{_q(parent_sku)}/{_q(child_sku)}", body=body)

    def put_inventory_events_event_id_multipliers(self, event_id, *, body: Any) -> Any:
        """Set an event multiplier  [PUT /inventory/events/{event_id}/multipliers, needs a write key]"""
        return self.request("PUT", f"/inventory/events/{_q(event_id)}/multipliers", body=body)

    def put_inventory_stock_counts_count_id_lines(self, count_id, *, body: Any) -> Any:
        """Upsert Line  [PUT /inventory/stock-counts/{count_id}/lines, needs a write key]"""
        return self.request("PUT", f"/inventory/stock-counts/{_q(count_id)}/lines", body=body)

    def put_inventory_stock_sku(self, sku, *, body: Any) -> Any:
        """Create or update a stock record  [PUT /inventory/stock/{sku}, needs a write key]"""
        return self.request("PUT", f"/inventory/stock/{_q(sku)}", body=body)

    def put_inventory_stock_sku_suppliers_supplier_id(self, sku, supplier_id, *, body: Any) -> Any:
        """Link a supplier to a SKU  [PUT /inventory/stock/{sku}/suppliers/{supplier_id}, needs a write key]"""
        return self.request("PUT", f"/inventory/stock/{_q(sku)}/suppliers/{_q(supplier_id)}", body=body)

    def put_inventory_warehouses_lanes(self, *, body: Any) -> Any:
        """Set a transfer lane  [PUT /inventory/warehouses/lanes, needs a write key]"""
        return self.request("PUT", f"/inventory/warehouses/lanes", body=body)


def _q(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="")


def _error(exc: "urllib.error.HTTPError") -> StockAIError:
    raw = exc.read()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {}
    retry = exc.headers.get("Retry-After")
    detail = doc.get("detail") if isinstance(doc, dict) else None
    return StockAIError(
        exc.code, detail if isinstance(detail, str) else (raw[:200].decode("utf-8", "replace")),
        error_code=doc.get("error_code") if isinstance(doc, dict) else None,
        error_params=doc.get("error_params") if isinstance(doc, dict) else None,
        retry_after=int(retry) if retry and retry.isdigit() else None)


def _multipart(files: dict, fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode()
    for name, spec in files.items():
        filename, content = spec if isinstance(spec, tuple) else (name, spec)
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
