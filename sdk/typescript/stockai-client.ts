/**
 * StockAI API client (TypeScript). GENERATED - do not edit by hand.
 *
 * Regenerate with `python -m backend.scripts.generate_clients`; the test suite
 * fails when this file is out of step with the public API. No dependencies
 * (fetch only: Node 18+, Deno, Bun and browsers).
 *
 *   const api = new StockAI('sk_live_...')
 *   const envelope = await api.get_alerts()
 *
 * A call resolves to the parsed JSON envelope exactly as the API sent it (or the
 * bytes of a file download). A refused call rejects with `StockAIError`.
 */
export const DEFAULT_BASE_URL = 'https://app.stockai.es'
const API_PREFIX = '/api/v1'

export class StockAIError extends Error {
  constructor(
    public status: number,
    message: string,
    public errorCode?: string,
    public errorParams: Record<string, unknown> = {},
    public retryAfter?: number,
  ) {
    super(`${status} ${errorCode ?? ''} ${message}`.trim())
    this.name = 'StockAIError'
  }
}

type Query = Record<string, unknown>

export class StockAI {
  private base: string

  constructor(private apiKey: string, baseUrl: string = DEFAULT_BASE_URL) {
    if (!apiKey.startsWith('sk_')) throw new Error('apiKey must be a StockAI key (sk_...)')
    this.base = baseUrl.replace(/\/+$/, '')
  }

  /** One call. `path` is relative to the API prefix and already filled in. */
  async request(method: string, path: string, opts: {
    query?: Query; headers?: Query; body?: unknown; files?: Record<string, Blob | [string, Blob]>; raw?: boolean
  } = {}): Promise<any> {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(opts.query ?? {})) {
      if (v === undefined || v === null) continue
      for (const item of Array.isArray(v) ? v : [v]) qs.append(k, String(item))
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.apiKey}`, Accept: 'application/json' }
    for (const [k, v] of Object.entries(opts.headers ?? {})) if (v !== undefined && v !== null) headers[k] = String(v)
    let body: BodyInit | undefined
    if (opts.files) {
      const form = new FormData()
      for (const [k, v] of Object.entries((opts.body as Query) ?? {})) form.append(k, String(v))
      for (const [k, v] of Object.entries(opts.files)) {
        if (Array.isArray(v)) form.append(k, v[1], v[0]); else form.append(k, v)
      }
      body = form
    } else if (opts.body !== undefined) {
      body = JSON.stringify(opts.body)
      headers['Content-Type'] = 'application/json'
    }
    const query = qs.toString()
    const res = await fetch(`${this.base}${API_PREFIX}${path}${query ? `?${query}` : ''}`, { method, headers, body })
    if (!res.ok) {
      let doc: any = {}
      try { doc = await res.json() } catch { /* not JSON */ }
      const retry = Number(res.headers.get('Retry-After'))
      throw new StockAIError(res.status, typeof doc.detail === 'string' ? doc.detail : res.statusText,
        doc.error_code, doc.error_params, Number.isFinite(retry) && retry > 0 ? retry : undefined)
    }
    if (opts.raw || !(res.headers.get('Content-Type') ?? '').includes('json')) return new Uint8Array(await res.arrayBuffer())
    const text = await res.text()
    return text ? JSON.parse(text) : null
  }

  /** Delete a data source  [DELETE /data-sources/{source_id}, needs a write key] */
  delete_data_sources_source_id(source_id: string | number): Promise<any> {
    return this.request('DELETE', `/data-sources/${encodeURIComponent(String(source_id))}`, {})
  }

  /** Delete a document  [DELETE /documents/{doc_id}, needs a write key] */
  delete_documents_doc_id(doc_id: string | number): Promise<any> {
    return this.request('DELETE', `/documents/${encodeURIComponent(String(doc_id))}`, {})
  }

  /** Remove a bill-of-materials line  [DELETE /inventory/bom/{parent_sku}/{child_sku}, needs a write key] */
  delete_inventory_bom_parent_sku_child_sku(parent_sku: string | number, child_sku: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/bom/${encodeURIComponent(String(parent_sku))}/${encodeURIComponent(String(child_sku))}`, {})
  }

  /** Delete a demand event  [DELETE /inventory/events/{event_id}, needs a write key] */
  delete_inventory_events_event_id(event_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/events/${encodeURIComponent(String(event_id))}`, {})
  }

  /** Remove an event multiplier  [DELETE /inventory/events/{event_id}/multipliers/{override_id}, needs a write key] */
  delete_inventory_events_event_id_multipliers_override_id(event_id: string | number, override_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/events/${encodeURIComponent(String(event_id))}/multipliers/${encodeURIComponent(String(override_id))}`, {})
  }

  /** Delete a price break  [DELETE /inventory/price-breaks/{price_break_id}, needs a write key] */
  delete_inventory_price_breaks_price_break_id(price_break_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/price-breaks/${encodeURIComponent(String(price_break_id))}`, {})
  }

  /** Delete Line  [DELETE /inventory/stock-counts/{count_id}/lines/{sku}, needs a write key] */
  delete_inventory_stock_counts_count_id_lines_sku(count_id: string | number, sku: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/lines/${encodeURIComponent(String(sku))}`, {})
  }

  /** Delete a stock record  [DELETE /inventory/stock/{sku}, needs a write key] */
  delete_inventory_stock_sku(sku: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/stock/${encodeURIComponent(String(sku))}`, {})
  }

  /** Unlink a supplier from a SKU  [DELETE /inventory/stock/{sku}/suppliers/{supplier_id}, needs a write key] */
  delete_inventory_stock_sku_suppliers_supplier_id(sku: string | number, supplier_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/stock/${encodeURIComponent(String(sku))}/suppliers/${encodeURIComponent(String(supplier_id))}`, {})
  }

  /** Deactivate a supplier  [DELETE /inventory/suppliers/{supplier_id}, needs a write key] */
  delete_inventory_suppliers_supplier_id(supplier_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory/suppliers/${encodeURIComponent(String(supplier_id))}`, {})
  }

  /** Delete Profile  [DELETE /inventory-sync/profiles/{profile_id}, needs a write key] */
  delete_inventory_sync_profiles_profile_id(profile_id: string | number): Promise<any> {
    return this.request('DELETE', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}`, {})
  }

  /** Delete a transfer lane  [DELETE /inventory/warehouses/lanes, needs a write key] */
  delete_inventory_warehouses_lanes(o: { "from_warehouse": unknown; "to_warehouse": unknown }): Promise<any> {
    return this.request('DELETE', `/inventory/warehouses/lanes`, { query: { "from_warehouse": o["from_warehouse"], "to_warehouse": o["to_warehouse"] } })
  }

  /** Cancel a job  [DELETE /jobs/{job_id}, needs a write key] */
  delete_jobs_job_id(job_id: string | number): Promise<any> {
    return this.request('DELETE', `/jobs/${encodeURIComponent(String(job_id))}`, {})
  }

  /** Delete a scenario  [DELETE /scenarios/{scenario_id}, needs a write key] */
  delete_scenarios_scenario_id(scenario_id: string | number): Promise<any> {
    return this.request('DELETE', `/scenarios/${encodeURIComponent(String(scenario_id))}`, {})
  }

  /** Delete a session  [DELETE /sessions/{session_id}, needs a write key] */
  delete_sessions_session_id(session_id: string | number): Promise<any> {
    return this.request('DELETE', `/sessions/${encodeURIComponent(String(session_id))}`, {})
  }

  /** Delete a retraining schedule  [DELETE /sessions/{session_id}/schedule, needs a write key] */
  delete_sessions_session_id_schedule(session_id: string | number): Promise<any> {
    return this.request('DELETE', `/sessions/${encodeURIComponent(String(session_id))}/schedule`, {})
  }

  /** Delete a webhook  [DELETE /webhooks/{webhook_id}, needs a write key] */
  delete_webhooks_webhook_id(webhook_id: string | number): Promise<any> {
    return this.request('DELETE', `/webhooks/${encodeURIComponent(String(webhook_id))}`, {})
  }

  /** List alerts  [GET /alerts, needs a read key] */
  get_alerts(o: { "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/alerts`, { query: { "limit": o["limit"] } })
  }

  /** List account activity  [GET /alerts/activity, needs a read key] */
  get_alerts_activity(o: { "limit"?: unknown; "offset"?: unknown; "kind"?: unknown; "severity"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/alerts/activity`, { query: { "limit": o["limit"], "offset": o["offset"], "kind": o["kind"], "severity": o["severity"] } })
  }

  /** List alert kinds  [GET /alerts/kinds, needs a read key] */
  get_alerts_kinds(): Promise<any> {
    return this.request('GET', `/alerts/kinds`, {})
  }

  /** Configuration schema  [GET /config/schema, needs a read key] */
  get_config_schema(): Promise<any> {
    return this.request('GET', `/config/schema`, {})
  }

  /** Read data freshness  [GET /data-freshness, needs a read key] */
  get_data_freshness(): Promise<any> {
    return this.request('GET', `/data-freshness`, {})
  }

  /** List data sources  [GET /data-sources, needs a read key] */
  get_data_sources(o: { "skip"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/data-sources`, { query: { "skip": o["skip"], "limit": o["limit"] } })
  }

  /** Get a data source  [GET /data-sources/{source_id}, needs a read key] */
  get_data_sources_source_id(source_id: string | number): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}`, {})
  }

  /** Analyze a data source  [GET /data-sources/{source_id}/analyze, needs a read key] */
  get_data_sources_source_id_analyze(source_id: string | number, o: { "date_col"?: unknown; "target_col"?: unknown; "sku_col"?: unknown; "sheet"?: unknown; "date_from"?: unknown; "date_to"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/analyze`, { query: { "date_col": o["date_col"], "target_col": o["target_col"], "sku_col": o["sku_col"], "sheet": o["sheet"], "date_from": o["date_from"], "date_to": o["date_to"] } })
  }

  /** Analyze one SKU in a data source  [GET /data-sources/{source_id}/analyze/{sku_id}, needs a read key] */
  get_data_sources_source_id_analyze_sku_id(source_id: string | number, sku_id: string | number, o: { "date_col": unknown; "target_col": unknown; "sku_col"?: unknown; "sheet"?: unknown; "date_from"?: unknown; "date_to"?: unknown }): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/analyze/${encodeURIComponent(String(sku_id))}`, { query: { "date_col": o["date_col"], "target_col": o["target_col"], "sku_col": o["sku_col"], "sheet": o["sheet"], "date_from": o["date_from"], "date_to": o["date_to"] } })
  }

  /** Load a data source as an editable table  [GET /data-sources/{source_id}/edit-table, needs a read key] */
  get_data_sources_source_id_edit_table(source_id: string | number): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/edit-table`, {})
  }

  /** Preview a data source  [GET /data-sources/{source_id}/preview, needs a read key] */
  get_data_sources_source_id_preview(source_id: string | number, o: { "rows"?: unknown; "sheet"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/preview`, { query: { "rows": o["rows"], "sheet": o["sheet"] } })
  }

  /** List a SQL source's tables  [GET /data-sources/{source_id}/schema, needs a write key] */
  get_data_sources_source_id_schema(source_id: string | number, o: { "refresh"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/schema`, { query: { "refresh": o["refresh"] } })
  }

  /** List a table's columns  [GET /data-sources/{source_id}/schema/columns, needs a write key] */
  get_data_sources_source_id_schema_columns(source_id: string | number, o: { "table": unknown; "schema"?: unknown; "refresh"?: unknown }): Promise<any> {
    return this.request('GET', `/data-sources/${encodeURIComponent(String(source_id))}/schema/columns`, { query: { "table": o["table"], "schema": o["schema"], "refresh": o["refresh"] } })
  }

  /** List datasets  [GET /datasets, needs a read key] */
  get_datasets(o: { "skip"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets`, { query: { "skip": o["skip"], "limit": o["limit"] } })
  }

  /** Accuracy Dataset  [GET /datasets/accuracy, needs a read key] */
  get_datasets_accuracy(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "session_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/accuracy`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "session_id": o["session_id"] } })
  }

  /** Committed Demand Dataset  [GET /datasets/committed-demand, needs a read key] */
  get_datasets_committed_demand(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "updated_since"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/committed-demand`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "updated_since": o["updated_since"] } })
  }

  /** Get a dataset  [GET /datasets/{dataset_id}, needs a read key] */
  get_datasets_dataset_id(dataset_id: string | number): Promise<any> {
    return this.request('GET', `/datasets/${encodeURIComponent(String(dataset_id))}`, {})
  }

  /** Forecast Points Dataset  [GET /datasets/forecast-points, needs a read key] */
  get_datasets_forecast_points(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "session_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/forecast-points`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "session_id": o["session_id"] } })
  }

  /** Inventory Status Dataset  [GET /datasets/inventory-status, needs a read key] */
  get_datasets_inventory_status(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "session_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/inventory-status`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "session_id": o["session_id"] } })
  }

  /** Purchase Order Lines Dataset  [GET /datasets/purchase-order-lines, needs a read key] */
  get_datasets_purchase_order_lines(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "updated_since"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/purchase-order-lines`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "updated_since": o["updated_since"] } })
  }

  /** Receptions Dataset  [GET /datasets/receptions, needs a read key] */
  get_datasets_receptions(o: { "format"?: unknown; "page"?: unknown; "limit"?: unknown; "updated_since"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/datasets/receptions`, { query: { "format": o["format"], "page": o["page"], "limit": o["limit"], "updated_since": o["updated_since"] } })
  }

  /** Datasets Schema  [GET /datasets/schema, needs a read key] */
  get_datasets_schema(): Promise<any> {
    return this.request('GET', `/datasets/schema`, {})
  }

  /** List documents  [GET /documents, needs a read key] */
  get_documents(): Promise<any> {
    return this.request('GET', `/documents`, {})
  }

  /** Get a document  [GET /documents/{doc_id}, needs a read key] */
  get_documents_doc_id(doc_id: string | number): Promise<any> {
    return this.request('GET', `/documents/${encodeURIComponent(String(doc_id))}`, {})
  }

  /** Download a document  [GET /documents/{doc_id}/content, needs a read key] */
  get_documents_doc_id_content(doc_id: string | number): Promise<any> {
    return this.request('GET', `/documents/${encodeURIComponent(String(doc_id))}/content`, {})
  }

  /** Document indexing status  [GET /documents/{doc_id}/status, needs a read key] */
  get_documents_doc_id_status(doc_id: string | number): Promise<any> {
    return this.request('GET', `/documents/${encodeURIComponent(String(doc_id))}/status`, {})
  }

  /** Read plan and limits  [GET /entitlements, needs a read key] */
  get_entitlements(): Promise<any> {
    return this.request('GET', `/entitlements`, {})
  }

  /** List where a component is used  [GET /inventory/bom/{child_sku}/used-in, needs a read key] */
  get_inventory_bom_child_sku_used_in(child_sku: string | number): Promise<any> {
    return this.request('GET', `/inventory/bom/${encodeURIComponent(String(child_sku))}/used-in`, {})
  }

  /** Read a bill of materials  [GET /inventory/bom/{parent_sku}, needs a read key] */
  get_inventory_bom_parent_sku(parent_sku: string | number): Promise<any> {
    return this.request('GET', `/inventory/bom/${encodeURIComponent(String(parent_sku))}`, {})
  }

  /** Read the cash calendar  [GET /inventory/cash-calendar, needs a read key] */
  get_inventory_cash_calendar(o: { "horizon_days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/cash-calendar`, { query: { "horizon_days": o["horizon_days"] } })
  }

  /** Read the dashboard summary  [GET /inventory/dashboard-summary, needs a read key] */
  get_inventory_dashboard_summary(o: { "session_id": unknown }): Promise<any> {
    return this.request('GET', `/inventory/dashboard-summary`, { query: { "session_id": o["session_id"] } })
  }

  /** List dead capital  [GET /inventory/dead-capital, needs a read key] */
  get_inventory_dead_capital(o: { "window_days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/dead-capital`, { query: { "window_days": o["window_days"] } })
  }

  /** List demand events  [GET /inventory/events, needs a read key] */
  get_inventory_events(): Promise<any> {
    return this.request('GET', `/inventory/events`, {})
  }

  /** Read the event catalog  [GET /inventory/events/catalog, needs a read key] */
  get_inventory_events_catalog(o: { "country"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/events/catalog`, { query: { "country": o["country"] } })
  }

  /** List an event's multipliers  [GET /inventory/events/{event_id}/multipliers, needs a read key] */
  get_inventory_events_event_id_multipliers(event_id: string | number): Promise<any> {
    return this.request('GET', `/inventory/events/${encodeURIComponent(String(event_id))}/multipliers`, {})
  }

  /** Upcoming demand events  [GET /inventory/events/upcoming, needs a read key] */
  get_inventory_events_upcoming(o: { "days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/events/upcoming`, { query: { "days": o["days"] } })
  }

  /** Project revenue, cost and margin  [GET /inventory/forecast-money, needs a read key] */
  get_inventory_forecast_money(o: { "session_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/forecast-money`, { query: { "session_id": o["session_id"] } })
  }

  /** List margin erosion  [GET /inventory/margin-erosion, needs a read key] */
  get_inventory_margin_erosion(o: { "window_days"?: unknown; "min_erosion_pts"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/margin-erosion`, { query: { "window_days": o["window_days"], "min_erosion_pts": o["min_erosion_pts"] } })
  }

  /** Read the morning briefing  [GET /inventory/morning-briefing, needs a read key] */
  get_inventory_morning_briefing(o: { "session_id"?: unknown; "service_level"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/morning-briefing`, { query: { "session_id": o["session_id"], "service_level": o["service_level"] } })
  }

  /** Optimize purchases and transfers  [GET /inventory/optimize, needs a read key] */
  get_inventory_optimize(o: { "session_id"?: unknown; "horizon_days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/optimize`, { query: { "session_id": o["session_id"], "horizon_days": o["horizon_days"] } })
  }

  /** List recent purchase orders  [GET /inventory/po-history, needs a read key] */
  get_inventory_po_history(o: { "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/po-history`, { query: { "limit": o["limit"] } })
  }

  /** Po History Page  [GET /inventory/po-history/page, needs a read key] */
  get_inventory_po_history_page(o: { "limit"?: unknown; "offset"?: unknown; "status"?: unknown; "q"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/po-history/page`, { query: { "limit": o["limit"], "offset": o["offset"], "status": o["status"], "q": o["q"] } })
  }

  /** Download the purchase-order import template  [GET /inventory/po/import/template, needs a read key] */
  get_inventory_po_import_template(o: { "format"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/po/import/template`, { query: { "format": o["format"] } })
  }

  /** List overdue purchase orders  [GET /inventory/po/overdue, needs a read key] */
  get_inventory_po_overdue(): Promise<any> {
    return this.request('GET', `/inventory/po/overdue`, {})
  }

  /** List a purchase order's lines  [GET /inventory/po/{po_log_id}/items, needs a read key] */
  get_inventory_po_po_log_id_items(po_log_id: string | number): Promise<any> {
    return this.request('GET', `/inventory/po/${encodeURIComponent(String(po_log_id))}/items`, {})
  }

  /** List price breaks  [GET /inventory/price-breaks, needs a read key] */
  get_inventory_price_breaks(o: { "supplier_id"?: unknown; "sku"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/price-breaks`, { query: { "supplier_id": o["supplier_id"], "sku": o["sku"] } })
  }

  /** List product types  [GET /inventory/product-types, needs a read key] */
  get_inventory_product_types(): Promise<any> {
    return this.request('GET', `/inventory/product-types`, {})
  }

  /** Explode demand into components  [GET /inventory/production-requirements, needs a read key] */
  get_inventory_production_requirements(o: { "session_id": unknown; "horizon_days"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory/production-requirements`, { query: { "session_id": o["session_id"], "horizon_days": o["horizon_days"] } })
  }

  /** Read the cost of ignoring recommendations  [GET /inventory/recommendation-log/cost-of-ignoring, needs a read key] */
  get_inventory_recommendation_log_cost_of_ignoring(o: { "from_date"?: unknown; "to_date"?: unknown; "po_window_days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/recommendation-log/cost-of-ignoring`, { query: { "from_date": o["from_date"], "to_date": o["to_date"], "po_window_days": o["po_window_days"] } })
  }

  /** Explain why a recommendation changed  [GET /inventory/recommendation-log/{sku}/why-changed, needs a read key] */
  get_inventory_recommendation_log_sku_why_changed(sku: string | number): Promise<any> {
    return this.request('GET', `/inventory/recommendation-log/${encodeURIComponent(String(sku))}/why-changed`, {})
  }

  /** Download the inventory PDF  [GET /inventory/report/pdf, needs a read key] */
  get_inventory_report_pdf(o: { "session_id": unknown; "service_level"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory/report/pdf`, { query: { "session_id": o["session_id"], "service_level": o["service_level"] } })
  }

  /** Read accumulated ROI  [GET /inventory/roi, needs a read key] */
  get_inventory_roi(): Promise<any> {
    return this.request('GET', `/inventory/roi`, {})
  }

  /** Read one month's ROI report  [GET /inventory/roi/month-report, needs a read key] */
  get_inventory_roi_month_report(o: { "year"?: unknown; "month"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/roi/month-report`, { query: { "year": o["year"], "month": o["month"] } })
  }

  /** List monthly ROI  [GET /inventory/roi/monthly, needs a read key] */
  get_inventory_roi_monthly(o: { "months"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/roi/monthly`, { query: { "months": o["months"] } })
  }

  /** List SKUs missing setup  [GET /inventory/setup-gaps, needs a read key] */
  get_inventory_setup_gaps(o: { "session_id"?: unknown; "horizon_days"?: unknown; "limit"?: unknown; "target_pct"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/setup-gaps`, { query: { "session_id": o["session_id"], "horizon_days": o["horizon_days"], "limit": o["limit"], "target_pct": o["target_pct"] } })
  }

  /** List shrinkage  [GET /inventory/shrinkage, needs a read key] */
  get_inventory_shrinkage(o: { "sku"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/shrinkage`, { query: { "sku": o["sku"], "limit": o["limit"] } })
  }

  /** List shrinkage reasons  [GET /inventory/shrinkage/reasons, needs a read key] */
  get_inventory_shrinkage_reasons(): Promise<any> {
    return this.request('GET', `/inventory/shrinkage/reasons`, {})
  }

  /** Read the inventory traffic light  [GET /inventory/status, needs a read key] */
  get_inventory_status(o: { "session_id"?: unknown; "service_level"?: unknown; "signal"?: unknown; "supplier"?: unknown; "abc"?: unknown; "by_warehouse"?: unknown; "limit"?: unknown; "offset"?: unknown; "sort"?: unknown; "order"?: unknown; "q"?: unknown; "skus"?: unknown; "without_forecast"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/status`, { query: { "session_id": o["session_id"], "service_level": o["service_level"], "signal": o["signal"], "supplier": o["supplier"], "abc": o["abc"], "by_warehouse": o["by_warehouse"], "limit": o["limit"], "offset": o["offset"], "sort": o["sort"], "order": o["order"], "q": o["q"], "skus": o["skus"], "without_forecast": o["without_forecast"] } })
  }

  /** Export the purchase order as CSV  [GET /inventory/status/export-po, needs a read key] */
  get_inventory_status_export_po(o: { "session_id": unknown; "service_level"?: unknown; "signals"?: unknown; "warehouse"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory/status/export-po`, { query: { "session_id": o["session_id"], "service_level": o["service_level"], "signals": o["signals"], "warehouse": o["warehouse"] } })
  }

  /** List stock  [GET /inventory/stock, needs a read key] */
  get_inventory_stock(): Promise<any> {
    return this.request('GET', `/inventory/stock`, {})
  }

  /** List Counts  [GET /inventory/stock-counts, needs a read key] */
  get_inventory_stock_counts(o: { "status"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/stock-counts`, { query: { "status": o["status"], "limit": o["limit"] } })
  }

  /** Get Count  [GET /inventory/stock-counts/{count_id}, needs a read key] */
  get_inventory_stock_counts_count_id(count_id: string | number): Promise<any> {
    return this.request('GET', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}`, {})
  }

  /** Preview Count  [GET /inventory/stock-counts/{count_id}/preview, needs a read key] */
  get_inventory_stock_counts_count_id_preview(count_id: string | number): Promise<any> {
    return this.request('GET', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/preview`, {})
  }

  /** Lookup Stock  [GET /inventory/stock/lookup, needs a read key] */
  get_inventory_stock_lookup(o: { "code": unknown; "warehouse"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory/stock/lookup`, { query: { "code": o["code"], "warehouse": o["warehouse"] } })
  }

  /** List Stock Page  [GET /inventory/stock/page, needs a read key] */
  get_inventory_stock_page(o: { "limit"?: unknown; "offset"?: unknown; "q"?: unknown; "warehouse"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/stock/page`, { query: { "limit": o["limit"], "offset": o["offset"], "q": o["q"], "warehouse": o["warehouse"] } })
  }

  /** Get a stock record  [GET /inventory/stock/{sku}, needs a read key] */
  get_inventory_stock_sku(sku: string | number): Promise<any> {
    return this.request('GET', `/inventory/stock/${encodeURIComponent(String(sku))}`, {})
  }

  /** Read a SKU's stock history  [GET /inventory/stock/{sku}/history, needs a read key] */
  get_inventory_stock_sku_history(sku: string | number, o: { "days"?: unknown; "warehouse"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/stock/${encodeURIComponent(String(sku))}/history`, { query: { "days": o["days"], "warehouse": o["warehouse"] } })
  }

  /** A SKU's suppliers  [GET /inventory/stock/{sku}/suppliers, needs a read key] */
  get_inventory_stock_sku_suppliers(sku: string | number): Promise<any> {
    return this.request('GET', `/inventory/stock/${encodeURIComponent(String(sku))}/suppliers`, {})
  }

  /** List supplier cost inflation  [GET /inventory/supplier-cost-inflation, needs a read key] */
  get_inventory_supplier_cost_inflation(o: { "window_days"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/supplier-cost-inflation`, { query: { "window_days": o["window_days"] } })
  }

  /** List suppliers  [GET /inventory/suppliers, needs a read key] */
  get_inventory_suppliers(): Promise<any> {
    return this.request('GET', `/inventory/suppliers`, {})
  }

  /** Check supplier contact details  [GET /inventory/suppliers/contact-health, needs a read key] */
  get_inventory_suppliers_contact_health(): Promise<any> {
    return this.request('GET', `/inventory/suppliers/contact-health`, {})
  }

  /** Download the suppliers import template  [GET /inventory/suppliers/import/template, needs a read key] */
  get_inventory_suppliers_import_template(o: { "format"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/suppliers/import/template`, { query: { "format": o["format"] } })
  }

  /** List supplier lead-time alerts  [GET /inventory/suppliers/lead-time-alerts, needs a read key] */
  get_inventory_suppliers_lead_time_alerts(): Promise<any> {
    return this.request('GET', `/inventory/suppliers/lead-time-alerts`, {})
  }

  /** List Suppliers Page  [GET /inventory/suppliers/page, needs a read key] */
  get_inventory_suppliers_page(o: { "limit"?: unknown; "offset"?: unknown; "q"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/suppliers/page`, { query: { "limit": o["limit"], "offset": o["offset"], "q": o["q"] } })
  }

  /** Read the supplier scorecard  [GET /inventory/suppliers/scorecard, needs a read key] */
  get_inventory_suppliers_scorecard(): Promise<any> {
    return this.request('GET', `/inventory/suppliers/scorecard`, {})
  }

  /** Get Fields  [GET /inventory-sync/fields, needs a read key] */
  get_inventory_sync_fields(): Promise<any> {
    return this.request('GET', `/inventory-sync/fields`, {})
  }

  /** Get Freshness  [GET /inventory-sync/freshness, needs a read key] */
  get_inventory_sync_freshness(): Promise<any> {
    return this.request('GET', `/inventory-sync/freshness`, {})
  }

  /** Tenant Health  [GET /inventory-sync/health, needs a read key] */
  get_inventory_sync_health(o: { "strict"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/health`, { query: { "strict": o["strict"] } })
  }

  /** Get Ownership  [GET /inventory-sync/ownership, needs a read key] */
  get_inventory_sync_ownership(o: { "skus"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/ownership`, { query: { "skus": o["skus"] } })
  }

  /** List Presets  [GET /inventory-sync/presets, needs a write key] */
  get_inventory_sync_presets(): Promise<any> {
    return this.request('GET', `/inventory-sync/presets`, {})
  }

  /** List Profiles  [GET /inventory-sync/profiles, needs a read key] */
  get_inventory_sync_profiles(): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles`, {})
  }

  /** Get Profile  [GET /inventory-sync/profiles/{profile_id}, needs a read key] */
  get_inventory_sync_profiles_profile_id(profile_id: string | number): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}`, {})
  }

  /** Export Profile  [GET /inventory-sync/profiles/{profile_id}/export, needs a read key] */
  get_inventory_sync_profiles_profile_id_export(profile_id: string | number, o: { "format"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/export`, { query: { "format": o["format"] } })
  }

  /** Profile Health  [GET /inventory-sync/profiles/{profile_id}/health, needs a read key] */
  get_inventory_sync_profiles_profile_id_health(profile_id: string | number, o: { "strict"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/health`, { query: { "strict": o["strict"] } })
  }

  /** Reconcile  [GET /inventory-sync/profiles/{profile_id}/reconcile, needs a write key] */
  get_inventory_sync_profiles_profile_id_reconcile(profile_id: string | number, o: { "sku": unknown; "warehouse"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/reconcile`, { query: { "sku": o["sku"], "warehouse": o["warehouse"] } })
  }

  /** List Runs  [GET /inventory-sync/profiles/{profile_id}/runs, needs a read key] */
  get_inventory_sync_profiles_profile_id_runs(profile_id: string | number, o: { "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/runs`, { query: { "limit": o["limit"] } })
  }

  /** Run Detail  [GET /inventory-sync/profiles/{profile_id}/runs/{run_id}, needs a read key] */
  get_inventory_sync_profiles_profile_id_runs_run_id(profile_id: string | number, run_id: string | number): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/runs/${encodeURIComponent(String(run_id))}`, {})
  }

  /** Run Rejected Csv  [GET /inventory-sync/profiles/{profile_id}/runs/{run_id}/rejected.csv, needs a read key] */
  get_inventory_sync_profiles_profile_id_runs_run_id_rejected_csv(profile_id: string | number, run_id: string | number): Promise<any> {
    return this.request('GET', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/runs/${encodeURIComponent(String(run_id))}/rejected.csv`, {})
  }

  /** Recipes  [GET /inventory-sync/recipes, needs a write key] */
  get_inventory_sync_recipes(o: { "engine"?: unknown; "source_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/recipes`, { query: { "engine": o["engine"], "source_id": o["source_id"] } })
  }

  /** Profile Schema  [GET /inventory-sync/schema/profile.json, needs a read key] */
  get_inventory_sync_schema_profile_json(): Promise<any> {
    return this.request('GET', `/inventory-sync/schema/profile.json`, {})
  }

  /** List Database Sources  [GET /inventory-sync/sources, needs a write key] */
  get_inventory_sync_sources(): Promise<any> {
    return this.request('GET', `/inventory-sync/sources`, {})
  }

  /** Columns  [GET /inventory-sync/sources/{source_id}/columns, needs a write key] */
  get_inventory_sync_sources_source_id_columns(source_id: string | number, o: { "table": unknown; "schema"?: unknown }): Promise<any> {
    return this.request('GET', `/inventory-sync/sources/${encodeURIComponent(String(source_id))}/columns`, { query: { "table": o["table"], "schema": o["schema"] } })
  }

  /** Discover  [GET /inventory-sync/sources/{source_id}/discover, needs a write key] */
  get_inventory_sync_sources_source_id_discover(source_id: string | number, o: { "refresh"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory-sync/sources/${encodeURIComponent(String(source_id))}/discover`, { query: { "refresh": o["refresh"] } })
  }

  /** Download the inventory import template  [GET /inventory/template.csv, needs a read key] */
  get_inventory_template_csv(): Promise<any> {
    return this.request('GET', `/inventory/template.csv`, {})
  }

  /** List transfers  [GET /inventory/transfers, needs a read key] */
  get_inventory_transfers(o: { "status"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/inventory/transfers`, { query: { "status": o["status"] } })
  }

  /** List warehouses  [GET /inventory/warehouses, needs a read key] */
  get_inventory_warehouses(): Promise<any> {
    return this.request('GET', `/inventory/warehouses`, {})
  }

  /** List transfer lanes  [GET /inventory/warehouses/lanes, needs a read key] */
  get_inventory_warehouses_lanes(): Promise<any> {
    return this.request('GET', `/inventory/warehouses/lanes`, {})
  }

  /** List active jobs  [GET /jobs/active, needs a read key] */
  get_jobs_active(): Promise<any> {
    return this.request('GET', `/jobs/active`, {})
  }

  /** Get a job  [GET /jobs/{job_id}, needs a read key] */
  get_jobs_job_id(job_id: string | number): Promise<any> {
    return this.request('GET', `/jobs/${encodeURIComponent(String(job_id))}`, {})
  }

  /** Job logs  [GET /jobs/{job_id}/logs, needs a read key] */
  get_jobs_job_id_logs(job_id: string | number, o: { "tail"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/jobs/${encodeURIComponent(String(job_id))}/logs`, { query: { "tail": o["tail"] } })
  }

  /** Available forecasting models  [GET /models/available, needs a read key] */
  get_models_available(): Promise<any> {
    return this.request('GET', `/models/available`, {})
  }

  /** Planning context  [GET /planning, needs a read key] */
  get_planning(): Promise<any> {
    return this.request('GET', `/planning`, {})
  }

  /** Export Feed  [GET /purchase-orders/export, needs a read key] */
  get_purchase_orders_export(o: { "since"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/purchase-orders/export`, { query: { "since": o["since"], "limit": o["limit"] } })
  }

  /** Export File  [GET /purchase-orders/export/file, needs a read key] */
  get_purchase_orders_export_file(o: { "po_ids": unknown; "format"?: unknown }): Promise<any> {
    return this.request('GET', `/purchase-orders/export/file`, { query: { "po_ids": o["po_ids"], "format": o["format"] } })
  }

  /** Export Formats  [GET /purchase-orders/export/formats, needs a read key] */
  get_purchase_orders_export_formats(): Promise<any> {
    return this.request('GET', `/purchase-orders/export/formats`, {})
  }

  /** Unacknowledged  [GET /purchase-orders/unacknowledged, needs a read key] */
  get_purchase_orders_unacknowledged(): Promise<any> {
    return this.request('GET', `/purchase-orders/unacknowledged`, {})
  }

  /** Get a scenario  [GET /scenarios/{scenario_id}, needs a read key] */
  get_scenarios_scenario_id(scenario_id: string | number): Promise<any> {
    return this.request('GET', `/scenarios/${encodeURIComponent(String(scenario_id))}`, {})
  }

  /** List retraining schedules  [GET /schedules, needs a read key] */
  get_schedules(): Promise<any> {
    return this.request('GET', `/schedules`, {})
  }

  /** List scheduler history  [GET /schedules/history, needs a read key] */
  get_schedules_history(o: { "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/schedules/history`, { query: { "limit": o["limit"] } })
  }

  /** List sessions  [GET /sessions, needs a read key] */
  get_sessions(o: { "skip"?: unknown; "limit"?: unknown; "archived"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions`, { query: { "skip": o["skip"], "limit": o["limit"], "archived": o["archived"] } })
  }

  /** Get a session  [GET /sessions/{session_id}, needs a read key] */
  get_sessions_session_id(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}`, {})
  }

  /** Forecast accuracy  [GET /sessions/{session_id}/accuracy, needs a read key] */
  get_sessions_session_id_accuracy(session_id: string | number, o: { "threshold"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/accuracy`, { query: { "threshold": o["threshold"] } })
  }

  /** Get Accuracy Tracking  [GET /sessions/{session_id}/accuracy-tracking, needs a read key] */
  get_sessions_session_id_accuracy_tracking(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/accuracy-tracking`, {})
  }

  /** Analyze the attached dataset  [GET /sessions/{session_id}/analysis, needs a read key] */
  get_sessions_session_id_analysis(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/analysis`, {})
  }

  /** Analyst index status  [GET /sessions/{session_id}/analyst/rag-status, needs a read key] */
  get_sessions_session_id_analyst_rag_status(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/analyst/rag-status`, {})
  }

  /** List training artifacts  [GET /sessions/{session_id}/artifacts, needs a read key] */
  get_sessions_session_id_artifacts(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/artifacts`, {})
  }

  /** Download a training artifact  [GET /sessions/{session_id}/artifacts/download/{artifact_path}, needs a read key] */
  get_sessions_session_id_artifacts_download_artifact_path(session_id: string | number, artifact_path: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/artifacts/download/${encodeURIComponent(String(artifact_path))}`, {})
  }

  /** Models available to a session  [GET /sessions/{session_id}/available-models, needs a read key] */
  get_sessions_session_id_available_models(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/available-models`, {})
  }

  /** Read the column mapping (alias)  [GET /sessions/{session_id}/columns, needs a read key] */
  get_sessions_session_id_columns(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/columns`, {})
  }

  /** Read business parameters  [GET /sessions/{session_id}/config/business, needs a read key] */
  get_sessions_session_id_config_business(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/config/business`, {})
  }

  /** Read forecast settings  [GET /sessions/{session_id}/config/forecast, needs a read key] */
  get_sessions_session_id_config_forecast(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/config/forecast`, {})
  }

  /** Session configuration schema  [GET /sessions/{session_id}/config-schema, needs a read key] */
  get_sessions_session_id_config_schema(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/config-schema`, {})
  }

  /** Configuration summary  [GET /sessions/{session_id}/config-summary, needs a read key] */
  get_sessions_session_id_config_summary(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/config-summary`, {})
  }

  /** Read the column mapping  [GET /sessions/{session_id}/configure/columns, needs a read key] */
  get_sessions_session_id_configure_columns(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/configure/columns`, {})
  }

  /** Read feature settings  [GET /sessions/{session_id}/configure/features, needs a read key] */
  get_sessions_session_id_configure_features(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/configure/features`, {})
  }

  /** Read model selection  [GET /sessions/{session_id}/configure/models, needs a read key] */
  get_sessions_session_id_configure_models(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/configure/models`, {})
  }

  /** Read data remediations  [GET /sessions/{session_id}/configure/remediations, needs a read key] */
  get_sessions_session_id_configure_remediations(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/configure/remediations`, {})
  }

  /** Read validation settings  [GET /sessions/{session_id}/configure/validation, needs a read key] */
  get_sessions_session_id_configure_validation(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/configure/validation`, {})
  }

  /** Read the data-quality gate  [GET /sessions/{session_id}/data-gate, needs a read key] */
  get_sessions_session_id_data_gate(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/data-gate`, {})
  }

  /** Decompose a SKU's history  [GET /sessions/{session_id}/decomposition/{sku}, needs a read key] */
  get_sessions_session_id_decomposition_sku(session_id: string | number, sku: string | number, o: { "granularity"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/decomposition/${encodeURIComponent(String(sku))}`, { query: { "granularity": o["granularity"] } })
  }

  /** Export configuration (alias)  [GET /sessions/{session_id}/export-config, needs a read key] */
  get_sessions_session_id_export_config(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/export-config`, {})
  }

  /** Forecast series for a SKU  [GET /sessions/{session_id}/forecast-series/{sku}, needs a read key] */
  get_sessions_session_id_forecast_series_sku(session_id: string | number, sku: string | number, o: { "model"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/forecast-series/${encodeURIComponent(String(sku))}`, { query: { "model": o["model"] } })
  }

  /** Get Forecast Total  [GET /sessions/{session_id}/forecast-total, needs a read key] */
  get_sessions_session_id_forecast_total(session_id: string | number, o: { "granularity"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/forecast-total`, { query: { "granularity": o["granularity"] } })
  }

  /** Compare forecast with actual sales  [GET /sessions/{session_id}/forecast-vs-actual, needs a read key] */
  get_sessions_session_id_forecast_vs_actual(session_id: string | number, o: { "dataset_id"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/forecast-vs-actual`, { query: { "dataset_id": o["dataset_id"] } })
  }

  /** Get Guided Reading  [GET /sessions/{session_id}/guided-reading, needs a read key] */
  get_sessions_session_id_guided_reading(session_id: string | number, o: { "decisions"?: unknown; "mapping"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/guided-reading`, { query: { "decisions": o["decisions"], "mapping": o["mapping"] } })
  }

  /** Check dataset health  [GET /sessions/{session_id}/health, needs a read key] */
  get_sessions_session_id_health(session_id: string | number, o: { "refresh"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/health`, { query: { "refresh": o["refresh"] } })
  }

  /** Inspect the attached dataset  [GET /sessions/{session_id}/inspect, needs a read key] */
  get_sessions_session_id_inspect(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/inspect`, {})
  }

  /** Session inventory recommendations  [GET /sessions/{session_id}/inventory, needs a read key] */
  get_sessions_session_id_inventory(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/inventory`, {})
  }

  /** List a session's jobs  [GET /sessions/{session_id}/jobs, needs a read key] */
  get_sessions_session_id_jobs(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/jobs`, {})
  }

  /** Get Manifest  [GET /sessions/{session_id}/manifest, needs a read key] */
  get_sessions_session_id_manifest(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/manifest`, {})
  }

  /** Training metrics  [GET /sessions/{session_id}/metrics, needs a read key] */
  get_sessions_session_id_metrics(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/metrics`, {})
  }

  /** List model hyperparameters  [GET /sessions/{session_id}/models/hyperparams, needs a read key] */
  get_sessions_session_id_models_hyperparams(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/models/hyperparams`, {})
  }

  /** List forecast overrides  [GET /sessions/{session_id}/overrides, needs a read key] */
  get_sessions_session_id_overrides(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/overrides`, {})
  }

  /** Dataset profile (alias)  [GET /sessions/{session_id}/profile, needs a read key] */
  get_sessions_session_id_profile(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/profile`, {})
  }

  /** Data quality  [GET /sessions/{session_id}/quality, needs a read key] */
  get_sessions_session_id_quality(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/quality`, {})
  }

  /** Reforecast Status  [GET /sessions/{session_id}/reforecast/status, needs a read key] */
  get_sessions_session_id_reforecast_status(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/reforecast/status`, {})
  }

  /** Session report (alias)  [GET /sessions/{session_id}/report, needs a read key] */
  get_sessions_session_id_report(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/report`, {})
  }

  /** Download a report  [GET /sessions/{session_id}/reports/{format}, needs a read key] */
  get_sessions_session_id_reports_format(session_id: string | number, format: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/reports/${encodeURIComponent(String(format))}`, {})
  }

  /** Read report generation status  [GET /sessions/{session_id}/reports/status, needs a read key] */
  get_sessions_session_id_reports_status(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/reports/status`, {})
  }

  /** Training results  [GET /sessions/{session_id}/results, needs a read key] */
  get_sessions_session_id_results(session_id: string | number, o: { "level"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/results`, { query: { "level": o["level"] } })
  }

  /** Model routing  [GET /sessions/{session_id}/routing, needs a read key] */
  get_sessions_session_id_routing(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/routing`, {})
  }

  /** Model routing plan (alias)  [GET /sessions/{session_id}/routing-plan, needs a read key] */
  get_sessions_session_id_routing_plan(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/routing-plan`, {})
  }

  /** List scenarios  [GET /sessions/{session_id}/scenarios, needs a read key] */
  get_sessions_session_id_scenarios(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/scenarios`, {})
  }

  /** Get a retraining schedule  [GET /sessions/{session_id}/schedule, needs a read key] */
  get_sessions_session_id_schedule(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/schedule`, {})
  }

  /** Read a SKU's feature importances  [GET /sessions/{session_id}/shap/{sku}, needs a read key] */
  get_sessions_session_id_shap_sku(session_id: string | number, sku: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/shap/${encodeURIComponent(String(sku))}`, {})
  }

  /** SKU intelligence  [GET /sessions/{session_id}/sku-intelligence/{sku}, needs a read key] */
  get_sessions_session_id_sku_intelligence_sku(session_id: string | number, sku: string | number, o: { "model"?: unknown; "granularity"?: unknown; "agg"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/sku-intelligence/${encodeURIComponent(String(sku))}`, { query: { "model": o["model"], "granularity": o["granularity"], "agg": o["agg"] } })
  }

  /** Read training status  [GET /sessions/{session_id}/train/status, needs a read key] */
  get_sessions_session_id_train_status(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/train/status`, {})
  }

  /** List run warnings  [GET /sessions/{session_id}/warnings, needs a read key] */
  get_sessions_session_id_warnings(session_id: string | number): Promise<any> {
    return this.request('GET', `/sessions/${encodeURIComponent(String(session_id))}/warnings`, {})
  }

  /** List session summaries  [GET /sessions/summary, needs a read key] */
  get_sessions_summary(o: { "skip"?: unknown; "limit"?: unknown; "q"?: unknown; "status"?: unknown; "dataset_id"?: unknown; "archived"?: unknown; "created_from"?: unknown; "created_to"?: unknown; "sort"?: unknown; "order"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/sessions/summary`, { query: { "skip": o["skip"], "limit": o["limit"], "q": o["q"], "status": o["status"], "dataset_id": o["dataset_id"], "archived": o["archived"], "created_from": o["created_from"], "created_to": o["created_to"], "sort": o["sort"], "order": o["order"] } })
  }

  /** Read the currency  [GET /tenant/currency, needs a read key] */
  get_tenant_currency(): Promise<any> {
    return this.request('GET', `/tenant/currency`, {})
  }

  /** Read the time zone  [GET /tenant/timezone, needs a read key] */
  get_tenant_timezone(): Promise<any> {
    return this.request('GET', `/tenant/timezone`, {})
  }

  /** Get Run Durations  [GET /training/run-durations, needs a read key] */
  get_training_run_durations(o: { "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/training/run-durations`, { query: { "limit": o["limit"] } })
  }

  /** List webhooks  [GET /webhooks, needs a read key] */
  get_webhooks(): Promise<any> {
    return this.request('GET', `/webhooks`, {})
  }

  /** List webhook events  [GET /webhooks/events, needs a read key] */
  get_webhooks_events(): Promise<any> {
    return this.request('GET', `/webhooks/events`, {})
  }

  /** List webhook deliveries  [GET /webhooks/{webhook_id}/deliveries, needs a read key] */
  get_webhooks_webhook_id_deliveries(webhook_id: string | number, o: { "status"?: unknown; "limit"?: unknown } = {}): Promise<any> {
    return this.request('GET', `/webhooks/${encodeURIComponent(String(webhook_id))}/deliveries`, { query: { "status": o["status"], "limit": o["limit"] } })
  }

  /** Rename a data source  [PATCH /data-sources/{source_id}, needs a write key] */
  patch_data_sources_source_id(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/data-sources/${encodeURIComponent(String(source_id))}`, { body: o.body })
  }

  /** Save a SQL source's query  [PATCH /data-sources/{source_id}/query, needs a write key] */
  patch_data_sources_source_id_query(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/data-sources/${encodeURIComponent(String(source_id))}/query`, { body: o.body })
  }

  /** Update a SQL source's connection  [PATCH /data-sources/{source_id}/sql-config, needs a write key] */
  patch_data_sources_source_id_sql_config(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/data-sources/${encodeURIComponent(String(source_id))}/sql-config`, { body: o.body })
  }

  /** Toggle a catalog event  [PATCH /inventory/events/catalog/{catalog_key}, needs a write key] */
  patch_inventory_events_catalog_catalog_key(catalog_key: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/events/catalog/${encodeURIComponent(String(catalog_key))}`, { body: o.body })
  }

  /** Update a demand event  [PATCH /inventory/events/{event_id}, needs a write key] */
  patch_inventory_events_event_id(event_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/events/${encodeURIComponent(String(event_id))}`, { body: o.body })
  }

  /** Update part of a stock record  [PATCH /inventory/stock/{sku}, needs a write key] */
  patch_inventory_stock_sku(sku: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/stock/${encodeURIComponent(String(sku))}`, { body: o.body })
  }

  /** Set a SKU's product type  [PATCH /inventory/stock/{sku}/product-type, needs a write key] */
  patch_inventory_stock_sku_product_type(sku: string | number, o: { "product_type": unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/stock/${encodeURIComponent(String(sku))}/product-type`, { query: { "product_type": o["product_type"] } })
  }

  /** Update a supplier  [PATCH /inventory/suppliers/{supplier_id}, needs a write key] */
  patch_inventory_suppliers_supplier_id(supplier_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/suppliers/${encodeURIComponent(String(supplier_id))}`, { body: o.body })
  }

  /** Update Profile  [PATCH /inventory-sync/profiles/{profile_id}, needs a write key] */
  patch_inventory_sync_profiles_profile_id(profile_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}`, { body: o.body })
  }

  /** Update a warehouse  [PATCH /inventory/warehouses/{name}, needs a write key] */
  patch_inventory_warehouses_name(name: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/inventory/warehouses/${encodeURIComponent(String(name))}`, { body: o.body })
  }

  /** Update a session  [PATCH /sessions/{session_id}, needs a write key] */
  patch_sessions_session_id(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/sessions/${encodeURIComponent(String(session_id))}`, { body: o.body })
  }

  /** Save forecast overrides  [PATCH /sessions/{session_id}/overrides, needs a write key] */
  patch_sessions_session_id_overrides(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PATCH', `/sessions/${encodeURIComponent(String(session_id))}/overrides`, { body: o.body })
  }

  /** Explain a SKU's recommendation  [POST /ai/narrative/forecast-explanation, needs a read key] */
  post_ai_narrative_forecast_explanation(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/ai/narrative/forecast-explanation`, { body: o.body })
  }

  /** Write an inventory insight  [POST /ai/narrative/inventory, needs a read key] */
  post_ai_narrative_inventory(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/ai/narrative/inventory`, { body: o.body })
  }

  /** Write the morning briefing narrative  [POST /ai/narrative/morning, needs a read key] */
  post_ai_narrative_morning(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/ai/narrative/morning`, { body: o.body })
  }

  /** Suggest questions for the analyst  [POST /ai/suggested-questions, needs a read key] */
  post_ai_suggested_questions(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/ai/suggested-questions`, { body: o.body })
  }

  /** Create a file data source  [POST /data-sources/file, needs a write key] */
  post_data_sources_file(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/data-sources/file`, { body: o.body, files: o.files })
  }

  /** Run a SQL query against a SQL source  [POST /data-sources/{source_id}/execute-query, needs a write key] */
  post_data_sources_source_id_execute_query(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/execute-query`, { body: o.body })
  }

  /** Export a SQL query as Excel or CSV  [POST /data-sources/{source_id}/export-query, needs a write key] */
  post_data_sources_source_id_export_query(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/export-query`, { body: o.body })
  }

  /** Replace a data source's file  [POST /data-sources/{source_id}/file, needs a write key] */
  post_data_sources_source_id_file(source_id: string | number, o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/file`, { body: o.body, files: o.files })
  }

  /** Materialize a SQL query as a dataset  [POST /data-sources/{source_id}/materialize, needs a write key] */
  post_data_sources_source_id_materialize(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/materialize`, { body: o.body })
  }

  /** Save an edited table as a new source  [POST /data-sources/{source_id}/save-as-new, needs a write key] */
  post_data_sources_source_id_save_as_new(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/save-as-new`, { body: o.body })
  }

  /** Test a SQL source's connection  [POST /data-sources/{source_id}/test-connection, needs a write key] */
  post_data_sources_source_id_test_connection(source_id: string | number): Promise<any> {
    return this.request('POST', `/data-sources/${encodeURIComponent(String(source_id))}/test-connection`, {})
  }

  /** Create a SQL data source  [POST /data-sources/sql, needs a write key] */
  post_data_sources_sql(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/sql`, { body: o.body })
  }

  /** Parse a connection string  [POST /data-sources/sql/parse, needs a write key] */
  post_data_sources_sql_parse(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/data-sources/sql/parse`, { body: o.body })
  }

  /** Upload a dataset  [POST /datasets, needs a write key] */
  post_datasets(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/datasets`, { body: o.body, files: o.files })
  }

  /** Upload a document  [POST /documents, needs a write key] */
  post_documents(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/documents`, { body: o.body, files: o.files })
  }

  /** Send the daily alert now  [POST /inventory/alerts/send-now, needs a write key] */
  post_inventory_alerts_send_now(o: { "session_id": unknown }): Promise<any> {
    return this.request('POST', `/inventory/alerts/send-now`, { query: { "session_id": o["session_id"] } })
  }

  /** Import stock from a file  [POST /inventory/bulk, needs a write key] */
  post_inventory_bulk(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/bulk`, { body: o.body, files: o.files })
  }

  /** Preview a stock import  [POST /inventory/bulk/preview, needs a write key] */
  post_inventory_bulk_preview(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/bulk/preview`, { body: o.body, files: o.files })
  }

  /** Fit a purchase into a budget  [POST /inventory/cash-calendar/fit, needs a read key] */
  post_inventory_cash_calendar_fit(o: { "horizon_days"?: unknown; "session_id"?: unknown; body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory/cash-calendar/fit`, { query: { "horizon_days": o["horizon_days"], "session_id": o["session_id"] }, body: o.body })
  }

  /** Create a demand event  [POST /inventory/events, needs a write key] */
  post_inventory_events(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/events`, { body: o.body })
  }

  /** Seed the event catalog  [POST /inventory/events/catalog/seed, needs a write key] */
  post_inventory_events_catalog_seed(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/events/catalog/seed`, { body: o.body })
  }

  /** Simulate a demand event  [POST /inventory/events/simulate, needs a read key] */
  post_inventory_events_simulate(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/events/simulate`, { body: o.body })
  }

  /** Record a purchase order  [POST /inventory/log-po, needs a write key] */
  post_inventory_log_po(o: { "session_id": unknown; "Idempotency-Key"?: unknown; body?: unknown }): Promise<any> {
    return this.request('POST', `/inventory/log-po`, { query: { "session_id": o["session_id"] }, headers: { "Idempotency-Key": o["Idempotency-Key"] }, body: o.body })
  }

  /** Create a manual purchase order  [POST /inventory/po, needs a write key] */
  post_inventory_po(o: { "Idempotency-Key"?: unknown; body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/po`, { headers: { "Idempotency-Key": o["Idempotency-Key"] }, body: o.body })
  }

  /** Import purchase orders from a file  [POST /inventory/po/import, needs a write key] */
  post_inventory_po_import(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/po/import`, { body: o.body, files: o.files })
  }

  /** Preview a purchase-order import  [POST /inventory/po/import/preview, needs a write key] */
  post_inventory_po_import_preview(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/po/import/preview`, { body: o.body, files: o.files })
  }

  /** Receive a purchase order  [POST /inventory/po/{po_log_id}/receive, needs a write key] */
  post_inventory_po_po_log_id_receive(po_log_id: string | number, o: { body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory/po/${encodeURIComponent(String(po_log_id))}/receive`, { body: o.body })
  }

  /** Send a purchase order to suppliers  [POST /inventory/po/{po_log_id}/send, needs a write key] */
  post_inventory_po_po_log_id_send(po_log_id: string | number, o: { body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory/po/${encodeURIComponent(String(po_log_id))}/send`, { body: o.body })
  }

  /** Undo a purchase-order reception  [POST /inventory/po/{po_log_id}/unreceive, needs a write key] */
  post_inventory_po_po_log_id_unreceive(po_log_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/po/${encodeURIComponent(String(po_log_id))}/unreceive`, {})
  }

  /** Undo marking a purchase order as sent  [POST /inventory/po/{po_log_id}/unsend, needs a write key] */
  post_inventory_po_po_log_id_unsend(po_log_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/po/${encodeURIComponent(String(po_log_id))}/unsend`, {})
  }

  /** Evaluate price breaks for a cart  [POST /inventory/price-breaks/evaluate, needs a read key] */
  post_inventory_price_breaks_evaluate(o: { "session_id": unknown; body?: unknown }): Promise<any> {
    return this.request('POST', `/inventory/price-breaks/evaluate`, { query: { "session_id": o["session_id"] }, body: o.body })
  }

  /** Record shrinkage  [POST /inventory/shrinkage, needs a write key] */
  post_inventory_shrinkage(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/shrinkage`, { body: o.body })
  }

  /** Create Count  [POST /inventory/stock-counts, needs a write key] */
  post_inventory_stock_counts(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/stock-counts`, { body: o.body })
  }

  /** Apply Count  [POST /inventory/stock-counts/{count_id}/apply, needs a write key] */
  post_inventory_stock_counts_count_id_apply(count_id: string | number, o: { body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/apply`, { body: o.body })
  }

  /** Cancel Count  [POST /inventory/stock-counts/{count_id}/cancel, needs a write key] */
  post_inventory_stock_counts_count_id_cancel(count_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/cancel`, {})
  }

  /** Close Count  [POST /inventory/stock-counts/{count_id}/close, needs a write key] */
  post_inventory_stock_counts_count_id_close(count_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/close`, {})
  }

  /** Create a supplier  [POST /inventory/suppliers, needs a write key] */
  post_inventory_suppliers(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/suppliers`, { body: o.body })
  }

  /** Import suppliers from a file  [POST /inventory/suppliers/import, needs a write key] */
  post_inventory_suppliers_import(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/suppliers/import`, { body: o.body, files: o.files })
  }

  /** Preview a suppliers import  [POST /inventory/suppliers/import/preview, needs a write key] */
  post_inventory_suppliers_import_preview(o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/inventory/suppliers/import/preview`, { body: o.body, files: o.files })
  }

  /** Set a price break  [POST /inventory/suppliers/{supplier_id}/price-breaks, needs a write key] */
  post_inventory_suppliers_supplier_id_price_breaks(supplier_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/suppliers/${encodeURIComponent(String(supplier_id))}/price-breaks`, { body: o.body })
  }

  /** Reactivate a supplier  [POST /inventory/suppliers/{supplier_id}/reactivate, needs a write key] */
  post_inventory_suppliers_supplier_id_reactivate(supplier_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/suppliers/${encodeURIComponent(String(supplier_id))}/reactivate`, {})
  }

  /** Draft Doctor  [POST /inventory-sync/doctor, needs a write key] */
  post_inventory_sync_doctor(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/doctor`, { body: o.body })
  }

  /** Dry Run Unsaved  [POST /inventory-sync/dry-run, needs a write key] */
  post_inventory_sync_dry_run(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/dry-run`, { body: o.body })
  }

  /** Render Preset  [POST /inventory-sync/presets/{preset_id}/render, needs a write key] */
  post_inventory_sync_presets_preset_id_render(preset_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/presets/${encodeURIComponent(String(preset_id))}/render`, { body: o.body })
  }

  /** Create Profile  [POST /inventory-sync/profiles, needs a write key] */
  post_inventory_sync_profiles(o: { "Idempotency-Key"?: unknown; body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles`, { headers: { "Idempotency-Key": o["Idempotency-Key"] }, body: o.body })
  }

  /** Apply Document  [POST /inventory-sync/profiles/apply, needs a write key] */
  post_inventory_sync_profiles_apply(o: { "dry_run"?: unknown; "Idempotency-Key"?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/apply`, { query: { "dry_run": o["dry_run"] }, headers: { "Idempotency-Key": o["Idempotency-Key"] } })
  }

  /** Disable Profile  [POST /inventory-sync/profiles/{profile_id}/disable, needs a write key] */
  post_inventory_sync_profiles_profile_id_disable(profile_id: string | number): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/disable`, {})
  }

  /** Profile Doctor  [POST /inventory-sync/profiles/{profile_id}/doctor, needs a write key] */
  post_inventory_sync_profiles_profile_id_doctor(profile_id: string | number): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/doctor`, {})
  }

  /** Dry Run Saved  [POST /inventory-sync/profiles/{profile_id}/dry-run, needs a write key] */
  post_inventory_sync_profiles_profile_id_dry_run(profile_id: string | number): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/dry-run`, {})
  }

  /** Enable Profile  [POST /inventory-sync/profiles/{profile_id}/enable, needs a write key] */
  post_inventory_sync_profiles_profile_id_enable(profile_id: string | number): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/enable`, {})
  }

  /** Ingest Rows  [POST /inventory-sync/profiles/{profile_id}/ingest, needs a write key] */
  post_inventory_sync_profiles_profile_id_ingest(profile_id: string | number, o: { "batch_id"?: unknown; "snapshot_id"?: unknown; "last"?: unknown; "dry_run"?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/ingest`, { query: { "batch_id": o["batch_id"], "snapshot_id": o["snapshot_id"], "last": o["last"], "dry_run": o["dry_run"] } })
  }

  /** Run Now  [POST /inventory-sync/profiles/{profile_id}/run, needs a write key] */
  post_inventory_sync_profiles_profile_id_run(profile_id: string | number, o: { "Idempotency-Key"?: unknown; body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/${encodeURIComponent(String(profile_id))}/run`, { headers: { "Idempotency-Key": o["Idempotency-Key"] }, body: o.body })
  }

  /** Validate Document  [POST /inventory-sync/profiles/validate, needs a write key] */
  post_inventory_sync_profiles_validate(): Promise<any> {
    return this.request('POST', `/inventory-sync/profiles/validate`, {})
  }

  /** Refresh  [POST /inventory-sync/refresh, needs a write key] */
  post_inventory_sync_refresh(o: { "Idempotency-Key"?: unknown; body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/refresh`, { headers: { "Idempotency-Key": o["Idempotency-Key"] }, body: o.body })
  }

  /** Inspect  [POST /inventory-sync/sources/{source_id}/inspect, needs a write key] */
  post_inventory_sync_sources_source_id_inspect(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/sources/${encodeURIComponent(String(source_id))}/inspect`, { body: o.body })
  }

  /** Suggest View  [POST /inventory-sync/sources/{source_id}/suggest-view, needs a write key] */
  post_inventory_sync_sources_source_id_suggest_view(source_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/sources/${encodeURIComponent(String(source_id))}/suggest-view`, { body: o.body })
  }

  /** Transform Test  [POST /inventory-sync/transform-test, needs a write key] */
  post_inventory_sync_transform_test(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory-sync/transform-test`, { body: o.body })
  }

  /** Create a transfer  [POST /inventory/transfers, needs a write key] */
  post_inventory_transfers(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/transfers`, { body: o.body })
  }

  /** Cancel a transfer  [POST /inventory/transfers/{transfer_id}/cancel, needs a write key] */
  post_inventory_transfers_transfer_id_cancel(transfer_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/transfers/${encodeURIComponent(String(transfer_id))}/cancel`, {})
  }

  /** Close a transfer  [POST /inventory/transfers/{transfer_id}/close, needs a write key] */
  post_inventory_transfers_transfer_id_close(transfer_id: string | number): Promise<any> {
    return this.request('POST', `/inventory/transfers/${encodeURIComponent(String(transfer_id))}/close`, {})
  }

  /** Receive a transfer  [POST /inventory/transfers/{transfer_id}/receive, needs a write key] */
  post_inventory_transfers_transfer_id_receive(transfer_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/transfers/${encodeURIComponent(String(transfer_id))}/receive`, { body: o.body })
  }

  /** Create a warehouse  [POST /inventory/warehouses, needs a write key] */
  post_inventory_warehouses(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/inventory/warehouses`, { body: o.body })
  }

  /** Call the MCP server (JSON-RPC)  [POST /mcp, needs a read key] */
  post_mcp(o: { "MCP-Protocol-Version"?: unknown; body: unknown }): Promise<any> {
    return this.request('POST', `/mcp`, { headers: { "MCP-Protocol-Version": o["MCP-Protocol-Version"] }, body: o.body })
  }

  /** Set Erp Reference  [POST /purchase-orders/{po_log_id}/erp-reference, needs a write key] */
  post_purchase_orders_po_log_id_erp_reference(po_log_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/purchase-orders/${encodeURIComponent(String(po_log_id))}/erp-reference`, { body: o.body })
  }

  /** Create a session  [POST /sessions, needs a write key] */
  post_sessions(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions`, { body: o.body })
  }

  /** Put data into words  [POST /sessions/{session_id}/analyst/narrate, needs a read key] */
  post_sessions_session_id_analyst_narrate(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/analyst/narrate`, { body: o.body })
  }

  /** Ask the analyst about a session  [POST /sessions/{session_id}/analyst/query, needs a read key] */
  post_sessions_session_id_analyst_query(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/analyst/query`, { body: o.body })
  }

  /** Start Backtest  [POST /sessions/{session_id}/backtest, needs a write key] */
  post_sessions_session_id_backtest(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/backtest`, { body: o.body })
  }

  /** Save the column mapping (alias)  [POST /sessions/{session_id}/columns, needs a write key] */
  post_sessions_session_id_columns(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/columns`, { body: o.body })
  }

  /** Save business parameters  [POST /sessions/{session_id}/config/business, needs a write key] */
  post_sessions_session_id_config_business(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/config/business`, { body: o.body })
  }

  /** Save feature settings (alias)  [POST /sessions/{session_id}/config/features, needs a write key] */
  post_sessions_session_id_config_features(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/config/features`, { body: o.body })
  }

  /** Save forecast settings  [POST /sessions/{session_id}/config/forecast, needs a write key] */
  post_sessions_session_id_config_forecast(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/config/forecast`, { body: o.body })
  }

  /** Save model selection (alias)  [POST /sessions/{session_id}/config/models, needs a write key] */
  post_sessions_session_id_config_models(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/config/models`, { body: o.body })
  }

  /** Save validation settings (alias)  [POST /sessions/{session_id}/config/training, needs a write key] */
  post_sessions_session_id_config_training(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/config/training`, { body: o.body })
  }

  /** Save the column mapping  [POST /sessions/{session_id}/configure/columns, needs a write key] */
  post_sessions_session_id_configure_columns(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/columns`, { body: o.body })
  }

  /** Save feature settings  [POST /sessions/{session_id}/configure/features, needs a write key] */
  post_sessions_session_id_configure_features(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/features`, { body: o.body })
  }

  /** Configure Guided Reading  [POST /sessions/{session_id}/configure/guided-reading, needs a write key] */
  post_sessions_session_id_configure_guided_reading(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/guided-reading`, { body: o.body })
  }

  /** Save model selection  [POST /sessions/{session_id}/configure/models, needs a write key] */
  post_sessions_session_id_configure_models(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/models`, { body: o.body })
  }

  /** Answer the data-quality gate  [POST /sessions/{session_id}/configure/remediations, needs a write key] */
  post_sessions_session_id_configure_remediations(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/remediations`, { body: o.body })
  }

  /** Save validation settings  [POST /sessions/{session_id}/configure/validation, needs a write key] */
  post_sessions_session_id_configure_validation(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/configure/validation`, { body: o.body })
  }

  /** Attach a dataset  [POST /sessions/{session_id}/dataset, needs a write key] */
  post_sessions_session_id_dataset(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/dataset`, { body: o.body })
  }

  /** Detect drift  [POST /sessions/{session_id}/drift, needs a read key] */
  post_sessions_session_id_drift(session_id: string | number, o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/drift`, { body: o.body, files: o.files })
  }

  /** Predict on demand  [POST /sessions/{session_id}/predict, needs a read key] */
  post_sessions_session_id_predict(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/predict`, { body: o.body })
  }

  /** Reconcile actual values  [POST /sessions/{session_id}/reconcile, needs a read key] */
  post_sessions_session_id_reconcile(session_id: string | number, o: { "date_col"?: unknown; "sku_col"?: unknown; "target_col"?: unknown; body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/reconcile`, { query: { "date_col": o["date_col"], "sku_col": o["sku_col"], "target_col": o["target_col"] }, body: o.body, files: o.files })
  }

  /** Start Reforecast  [POST /sessions/{session_id}/reforecast, needs a write key] */
  post_sessions_session_id_reforecast(session_id: string | number, o: { body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/reforecast`, { body: o.body })
  }

  /** Generate a report  [POST /sessions/{session_id}/reports/generate, needs a write key] */
  post_sessions_session_id_reports_generate(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/reports/generate`, { body: o.body })
  }

  /** Restore Session  [POST /sessions/{session_id}/restore, needs a write key] */
  post_sessions_session_id_restore(session_id: string | number): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/restore`, {})
  }

  /** Save a scenario  [POST /sessions/{session_id}/scenarios, needs a write key] */
  post_sessions_session_id_scenarios(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/scenarios`, { body: o.body })
  }

  /** Preview a scenario  [POST /sessions/{session_id}/scenarios/preview, needs a read key] */
  post_sessions_session_id_scenarios_preview(session_id: string | number, o: { "top_changes"?: unknown; body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/scenarios/preview`, { query: { "top_changes": o["top_changes"] }, body: o.body })
  }

  /** Run a saved scenario  [POST /sessions/{session_id}/scenarios/{scenario_id}/run, needs a read key] */
  post_sessions_session_id_scenarios_scenario_id_run(session_id: string | number, scenario_id: string | number, o: { "top_changes"?: unknown } = {}): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/scenarios/${encodeURIComponent(String(scenario_id))}/run`, { query: { "top_changes": o["top_changes"] } })
  }

  /** Save a retraining schedule  [POST /sessions/{session_id}/schedule, needs a write key] */
  post_sessions_session_id_schedule(session_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/schedule`, { body: o.body })
  }

  /** Start training  [POST /sessions/{session_id}/train, needs a write key] */
  post_sessions_session_id_train(session_id: string | number, o: { body?: unknown } = {}): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/train`, { body: o.body })
  }

  /** Upload and attach a dataset  [POST /sessions/{session_id}/upload, needs a write key] */
  post_sessions_session_id_upload(session_id: string | number, o: { body: Record<string, unknown>; files: Record<string, Blob | [string, Blob]> }): Promise<any> {
    return this.request('POST', `/sessions/${encodeURIComponent(String(session_id))}/upload`, { body: o.body, files: o.files })
  }

  /** Create a webhook  [POST /webhooks, needs a write key] */
  post_webhooks(o: { body: unknown }): Promise<any> {
    return this.request('POST', `/webhooks`, { body: o.body })
  }

  /** Re-enable a webhook  [POST /webhooks/{webhook_id}/enable, needs a write key] */
  post_webhooks_webhook_id_enable(webhook_id: string | number): Promise<any> {
    return this.request('POST', `/webhooks/${encodeURIComponent(String(webhook_id))}/enable`, {})
  }

  /** Rotate a webhook secret  [POST /webhooks/{webhook_id}/rotate-secret, needs a write key] */
  post_webhooks_webhook_id_rotate_secret(webhook_id: string | number): Promise<any> {
    return this.request('POST', `/webhooks/${encodeURIComponent(String(webhook_id))}/rotate-secret`, {})
  }

  /** Send a test event  [POST /webhooks/{webhook_id}/test, needs a write key] */
  post_webhooks_webhook_id_test(webhook_id: string | number): Promise<any> {
    return this.request('POST', `/webhooks/${encodeURIComponent(String(webhook_id))}/test`, {})
  }

  /** Set a bill-of-materials line  [PUT /inventory/bom/{parent_sku}/{child_sku}, needs a write key] */
  put_inventory_bom_parent_sku_child_sku(parent_sku: string | number, child_sku: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/bom/${encodeURIComponent(String(parent_sku))}/${encodeURIComponent(String(child_sku))}`, { body: o.body })
  }

  /** Set an event multiplier  [PUT /inventory/events/{event_id}/multipliers, needs a write key] */
  put_inventory_events_event_id_multipliers(event_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/events/${encodeURIComponent(String(event_id))}/multipliers`, { body: o.body })
  }

  /** Upsert Line  [PUT /inventory/stock-counts/{count_id}/lines, needs a write key] */
  put_inventory_stock_counts_count_id_lines(count_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/stock-counts/${encodeURIComponent(String(count_id))}/lines`, { body: o.body })
  }

  /** Create or update a stock record  [PUT /inventory/stock/{sku}, needs a write key] */
  put_inventory_stock_sku(sku: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/stock/${encodeURIComponent(String(sku))}`, { body: o.body })
  }

  /** Link a supplier to a SKU  [PUT /inventory/stock/{sku}/suppliers/{supplier_id}, needs a write key] */
  put_inventory_stock_sku_suppliers_supplier_id(sku: string | number, supplier_id: string | number, o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/stock/${encodeURIComponent(String(sku))}/suppliers/${encodeURIComponent(String(supplier_id))}`, { body: o.body })
  }

  /** Set a transfer lane  [PUT /inventory/warehouses/lanes, needs a write key] */
  put_inventory_warehouses_lanes(o: { body: unknown }): Promise<any> {
    return this.request('PUT', `/inventory/warehouses/lanes`, { body: o.body })
  }
}
