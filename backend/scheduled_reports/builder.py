"""Builds the sections of a scheduled report.

Why Python builds them (docs/rust-migration.md, "Scheduled reports"): three of
the four sections stand on code that stays in Python on purpose. The
committed-demand verdict needs the inventory hub (stock, open orders, the
lead-time cascade), the budget figures need the budget service's scope and
parent rules, and the supplier on-time rate has the declared-lead-time gate
(`lead_time_set_by`). Porting any of them to compute a mail body would create
a second answer to "how much of the budget is spent?" one screen away from the
first. One builder, used by the worker AND by the preview route (the Rust API
asks for it over the internal render endpoint), so the preview is exactly what
the mail will say.

Rules every section obeys (silent-failures lens):
* A figure that cannot be known is `None` with a reason code, never 0.
* A section that cannot be built at all is `{"available": False, "reason": ...}`
  and the mail says so; it never disappears and never shows a made-up number.
* A list that was cut says how many rows it left out.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from backend.db.connection import query, query_one
from backend.scheduled_reports import catalog
from backend.scheduled_reports.schedule_math import window_bounds_utc

log = logging.getLogger(__name__)


def _unavailable(code: str, reason: str) -> dict[str, Any]:
    return {"code": code, "available": False, "reason": reason}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ── purchasing_summary ───────────────────────────────────────────────────────

def purchasing_summary(tenant_id: str, start: date, end: date, tz_name: str) -> dict[str, Any]:
    """Orders generated, sent and received in [start, end] (tenant-local days)."""
    lo, hi = window_bounds_utc(start, end, tz_name)
    row = query_one(
        """SELECT
             COUNT(*) FILTER (WHERE generated_at >= %(lo)s AND generated_at < %(hi)s
                                AND cancelled_at IS NULL)                      AS generated,
             COUNT(*) FILTER (WHERE generated_at >= %(lo)s AND generated_at < %(hi)s
                                AND cancelled_at IS NOT NULL)                  AS generated_then_cancelled,
             COUNT(*) FILTER (WHERE sent_at >= %(lo)s AND sent_at < %(hi)s
                                AND cancelled_at IS NULL)                      AS sent,
             COUNT(*) FILTER (WHERE received_at >= %(lo)s AND received_at < %(hi)s
                                AND reception_status = 'received')             AS received,
             COUNT(*) FILTER (WHERE received_at >= %(lo)s AND received_at < %(hi)s
                                AND reception_status = 'partial')              AS received_partially,
             COUNT(*) FILTER (WHERE generated_at >= %(lo)s AND generated_at < %(hi)s
                                AND cancelled_at IS NULL
                                AND total_value IS NOT NULL)                   AS generated_with_value,
             SUM(total_value) FILTER (WHERE generated_at >= %(lo)s AND generated_at < %(hi)s
                                AND cancelled_at IS NULL)                      AS generated_value
           FROM inventory_po_log
          WHERE tenant_id = %(tenant)s""",
        {"tenant": tenant_id, "lo": lo, "hi": hi}) or {}
    generated = int(row.get("generated") or 0)
    with_value = int(row.get("generated_with_value") or 0)
    # A value is only a total when every order carries one. With none costed it
    # is not known; with some, the figure is a floor and the mail says how many
    # orders it leaves out.
    value: Optional[float] = None
    if with_value:
        value = round(float(row.get("generated_value") or 0.0), 2)
    return {
        "code": catalog.PURCHASING_SUMMARY, "available": True,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generated": generated,
        "generated_then_cancelled": int(row.get("generated_then_cancelled") or 0),
        "sent": int(row.get("sent") or 0),
        "received": int(row.get("received") or 0),
        "received_partially": int(row.get("received_partially") or 0),
        "generated_value": value,
        "orders_without_value": generated - with_value,
    }


# ── budget_vs_spend ──────────────────────────────────────────────────────────

def budget_vs_spend(tenant_id: str, today: date) -> dict[str, Any]:
    from backend.inventory import purchase_budget_service as svc
    rows = [r for r in svc._current_rows(tenant_id)
            if r["active"] and r["period_start"] <= today <= r["period_end"]]
    if not rows:
        return _unavailable(catalog.BUDGET_VS_SPEND, "no_running_budget")
    rank = {"company": 0, "warehouse": 1, "supplier": 2, "category": 3}
    rows.sort(key=lambda r: (rank[r["scope_type"]], -r["period_start"].toordinal()))
    names = svc._names(tenant_id, rows)
    shown = rows[:catalog.MAX_BUDGET_ROWS]
    out = []
    for r in shown:
        use = svc.usage(tenant_id, r, today)
        free = svc.free_money(tenant_id, r, today, use)
        burn = use["burn"]
        out.append({
            "scope_type": r["scope_type"],
            "scope_label": svc._fmt(r, names)["scope_label"],
            "currency": r["currency"],
            "amount": float(r["amount"]),
            "spent": use["spent"],
            "committed": use["committed"],
            "remaining": free["free"],
            "limited_by_parent": free["limited_by"] == "parent",
            "used_fraction": burn.get("used_fraction"),
            "pace": burn.get("pace"),
            "projected_overrun": burn.get("projected_overrun"),
            "days_left": burn.get("days_left"),
            "period_start": r["period_start"].isoformat(),
            "period_end": r["period_end"].isoformat(),
            "hard_cap": bool(r["hard_cap"]),
            # Ordered lines with no unit cost are NOT in `spent`/`committed`.
            "unknown_cost_lines": use["unknown_cost_lines"],
        })
    return {"code": catalog.BUDGET_VS_SPEND, "available": True, "budgets": out,
            "budgets_not_shown": len(rows) - len(shown)}


# ── committed_demand ─────────────────────────────────────────────────────────

def committed_demand(tenant_id: str) -> dict[str, Any]:
    from backend.inventory import committed_demand_service as svc
    items = svc.list_for_tenant(tenant_id, limit=catalog.COMMITMENT_LIMIT)
    by_status = {s: 0 for s in svc.STATUSES}
    for i in items:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
    out: dict[str, Any] = {
        "code": catalog.COMMITTED_DEMAND, "available": True,
        "by_status": by_status, "total": len(items),
        # The list is capped; say so rather than let a cut count pass as whole.
        "truncated": len(items) >= catalog.COMMITMENT_LIMIT,
    }
    open_items = [i for i in items if i["status"] == "open"]
    if not open_items:
        out["verdict"] = {"available": True, "at_risk": 0, "covered": 0, "no_verdict": 0}
        return out
    try:
        svc.annotate_risk(tenant_id, open_items)
    except Exception:  # noqa: BLE001 - the counts above are still true
        log.exception("[scheduled-reports] commitment verdict failed for tenant %s", tenant_id)
        out["verdict"] = {"available": False, "reason": "verdict_unavailable"}
        return out
    at_risk = sum(1 for i in open_items if i.get("at_risk") is True)
    covered = sum(1 for i in open_items if i.get("at_risk") is False)
    out["verdict"] = {"available": True, "at_risk": at_risk, "covered": covered,
                      # An open commitment whose SKU has no stock row has no
                      # verdict. It is counted here, not as covered.
                      "no_verdict": len(open_items) - at_risk - covered}
    return out


# ── supplier_scorecard ───────────────────────────────────────────────────────

def supplier_scorecard(tenant_id: str) -> dict[str, Any]:
    from backend.inventory import reception_service as rec
    rows = rec.get_supplier_scorecard(tenant_id)
    if not rows:
        return _unavailable(catalog.SUPPLIER_SCORECARD, "no_receptions_recorded")
    graded = [r for r in rows if r.get("on_time_rate") is not None]
    ungraded = [r for r in rows if r.get("on_time_rate") is None]
    graded.sort(key=lambda r: (float(r["on_time_rate"]), -int(r.get("n_receptions") or 0)))
    ordered = graded + ungraded
    shown = ordered[:catalog.MAX_SUPPLIER_ROWS]
    return {
        "code": catalog.SUPPLIER_SCORECARD, "available": True,
        "suppliers": [{
            "supplier": r["supplier"],
            "receptions": int(r.get("n_receptions") or 0),
            # None = the supplier never declared a lead time, so there is
            # nothing to be on time against. Not 0 %, not 100 %.
            "on_time_rate": None if r.get("on_time_rate") is None else round(float(r["on_time_rate"]), 4),
            "declared_lead_time": r.get("lead_time_declarado"),
            "last_reception": _iso(r.get("last_reception")),
        } for r in shown],
        "suppliers_total": len(rows),
        "suppliers_without_declared_lead_time": len(ungraded),
        "suppliers_not_shown": len(ordered) - len(shown),
    }


# ── The report ───────────────────────────────────────────────────────────────

def build_sections(tenant_id: str, sections: list[str], *, frequency: str, local_today: date,
                   window: tuple[date, date], tz_name: str) -> list[dict[str, Any]]:
    """Every requested section, in the order requested. One section failing
    does not take the others down: it is reported as unavailable with
    `build_failed`, and the failure is logged with its traceback."""
    makers = {
        catalog.PURCHASING_SUMMARY: lambda: purchasing_summary(tenant_id, window[0], window[1], tz_name),
        catalog.BUDGET_VS_SPEND: lambda: budget_vs_spend(tenant_id, local_today),
        catalog.COMMITTED_DEMAND: lambda: committed_demand(tenant_id),
        catalog.SUPPLIER_SCORECARD: lambda: supplier_scorecard(tenant_id),
    }
    out = []
    for code in sections:
        make = makers.get(code)
        if make is None:
            out.append(_unavailable(code, "unknown_section"))
            continue
        try:
            out.append(make())
        except Exception:  # noqa: BLE001
            log.exception("[scheduled-reports] section %s failed for tenant %s", code, tenant_id)
            out.append(_unavailable(code, "build_failed"))
    return out


def build_report(tenant_id: str, sections: list[str], frequency: str,
                 now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """The snapshot a run stores and a preview returns."""
    from backend.api.v1.currency import currency_of
    from backend.api.v1.timezone import timezone_of
    from backend.scheduled_reports.schedule_math import window_for
    from zoneinfo import ZoneInfo
    now_utc = now_utc or datetime.now(timezone.utc)
    tz_name = timezone_of(tenant_id)
    local_today = now_utc.astimezone(ZoneInfo(tz_name)).date()
    window = window_for(frequency, local_today)
    return {
        "generated_at": now_utc.isoformat(),
        "timezone": tz_name,
        "frequency": frequency,
        "local_date": local_today.isoformat(),
        "currency": currency_of(tenant_id),
        "sections": build_sections(tenant_id, sections, frequency=frequency, local_today=local_today,
                                   window=window, tz_name=tz_name),
    }
