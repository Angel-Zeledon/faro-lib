"""Renders a stored report snapshot as an email (Spanish, from the locale
catalog: no sentence is written here).

The body only ever says what the snapshot says. A section that was not
available says so with its reason; a figure that is `None` is never printed as
a number.
"""

from __future__ import annotations

import html as html_lib
from typing import Any, Optional

from backend.formatting import money
from backend.notifications.locale import render_es

_TEXT = "#1f2937"
_DIM = "#6b7280"
_LINE = "#e5e7eb"


def _cur(code: Optional[str], fallback: dict) -> dict:
    from backend.api.v1.currency import SUPPORTED
    if code and code in SUPPORTED:
        return {"code": code, **SUPPORTED[code]}
    return {**fallback, "symbol": f"{code} " if code else fallback.get("symbol", "")}


def _e(value: Any) -> str:
    return html_lib.escape(str(value))


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _section_title(code: str) -> str:
    return render_es(f"report_section_{code}")


def _unavailable(section: dict) -> str:
    reason = section.get("reason") or "build_failed"
    try:
        why = render_es(f"report_reason_{reason}")
    except KeyError:
        why = render_es("report_reason_build_failed")
    return f'<p style="color:{_DIM};margin:0;">{_e(render_es("report_not_available", reason=why))}</p>'


def _kv(label: str, value: str) -> str:
    return (f'<tr><td style="padding:4px 12px 4px 0;color:{_DIM};">{_e(label)}</td>'
            f'<td style="padding:4px 0;color:{_TEXT};font-weight:600;">{_e(value)}</td></tr>')


def _purchasing(section: dict, cur: dict) -> str:
    p = section["period"]
    rows = [
        _kv(render_es("report_orders_generated"), str(section["generated"])),
        _kv(render_es("report_orders_sent"), str(section["sent"])),
        _kv(render_es("report_orders_received"), str(section["received"])),
        _kv(render_es("report_orders_received_partial"), str(section["received_partially"])),
    ]
    if section["generated_then_cancelled"]:
        rows.append(_kv(render_es("report_orders_cancelled"), str(section["generated_then_cancelled"])))
    value = section.get("generated_value")
    if value is None:
        shown = render_es("report_value_unknown") if section["generated"] else "-"
    elif section["orders_without_value"]:
        shown = render_es("report_value_partial", value=money(value, currency=cur),
                          n=section["orders_without_value"])
    else:
        shown = money(value, currency=cur)
    rows.append(_kv(render_es("report_orders_value"), shown))
    return (f'<p style="color:{_DIM};margin:0 0 8px;">'
            f'{_e(render_es("report_period", start=p["start"], end=p["end"]))}</p>'
            f'<table cellpadding="0" cellspacing="0">{"".join(rows)}</table>')


def _budgets(section: dict, cur: dict) -> str:
    lines = []
    for b in section["budgets"]:
        c = _cur(b["currency"], cur)
        label = render_es("report_scope_company") if b["scope_type"] == "company" else (b["scope_label"] or "-")
        text = render_es("report_budget_line", label=label, spent=money(b["spent"], currency=c),
                         committed=money(b["committed"], currency=c),
                         remaining=money(b["remaining"], currency=c), amount=money(b["amount"], currency=c))
        notes = []
        if b["pace"] == "over":
            notes.append(render_es("report_budget_pace_over"))
        elif b["pace"] == "ahead" and b.get("projected_overrun"):
            notes.append(render_es("report_budget_pace_ahead",
                                   overrun=money(b["projected_overrun"], currency=c)))
        elif b["pace"] == "on_track":
            notes.append(render_es("report_budget_pace_on_track"))
        if b["unknown_cost_lines"]:
            notes.append(render_es("report_budget_unknown_cost", n=b["unknown_cost_lines"]))
        if b["limited_by_parent"]:
            notes.append(render_es("report_budget_limited_parent"))
        sub = f'<br><span style="color:{_DIM};font-size:12px;">{_e(" ".join(notes))}</span>' if notes else ""
        lines.append(f'<li style="margin:0 0 8px;">{_e(text)}{sub}</li>')
    more = ""
    if section.get("budgets_not_shown"):
        more = f'<p style="color:{_DIM};margin:4px 0 0;">{_e(render_es("report_budget_more", n=section["budgets_not_shown"]))}</p>'
    return f'<ul style="margin:0;padding-left:18px;">{"".join(lines)}</ul>{more}'


def _commitments(section: dict) -> str:
    s = section["by_status"]
    out = [f'<p style="margin:0 0 4px;">{_e(render_es("report_commitments_total", n=section["total"]))}</p>',
           f'<p style="margin:0 0 4px;color:{_DIM};">{_e(render_es("report_commitments_status", open=s.get("open", 0), fulfilled=s.get("fulfilled", 0), cancelled=s.get("cancelled", 0)))}</p>']
    v = section["verdict"]
    if v.get("available"):
        out.append(f'<p style="margin:0;font-weight:600;">{_e(render_es("report_commitments_verdict", at_risk=v["at_risk"], covered=v["covered"], no_verdict=v["no_verdict"]))}</p>')
    else:
        out.append(_unavailable({"reason": v.get("reason")}))
    if section.get("truncated"):
        out.append(f'<p style="margin:4px 0 0;color:{_DIM};">{_e(render_es("report_commitments_truncated", n=section["total"]))}</p>')
    return "".join(out)


def _suppliers(section: dict) -> str:
    lines = []
    for s in section["suppliers"]:
        if s["on_time_rate"] is None:
            text = f'{s["supplier"]}: {render_es("report_supplier_not_declared")}'
        else:
            text = render_es("report_supplier_line", supplier=s["supplier"], rate=_pct(s["on_time_rate"]),
                             n=s["receptions"])
        lines.append(f'<li style="margin:0 0 4px;">{_e(text)}</li>')
    summary = render_es("report_supplier_summary", n=section["suppliers_total"],
                        m=section["suppliers_without_declared_lead_time"])
    more = ""
    if section.get("suppliers_not_shown"):
        more = f'<p style="color:{_DIM};margin:4px 0 0;">{_e(render_es("report_supplier_more", n=section["suppliers_not_shown"]))}</p>'
    return (f'<ul style="margin:0 0 6px;padding-left:18px;">{"".join(lines)}</ul>'
            f'<p style="color:{_DIM};margin:0;">{_e(summary)}</p>{more}')


def render_sections_html(snapshot: dict) -> str:
    cur = snapshot.get("currency") or {}
    blocks = []
    for section in snapshot["sections"]:
        code = section["code"]
        if not section.get("available"):
            body = _unavailable(section)
        elif code == "purchasing_summary":
            body = _purchasing(section, cur)
        elif code == "budget_vs_spend":
            body = _budgets(section, cur)
        elif code == "committed_demand":
            body = _commitments(section)
        elif code == "supplier_scorecard":
            body = _suppliers(section)
        else:
            body = _unavailable({"reason": "unknown_section"})
        try:
            title = _section_title(code)
        except KeyError:
            title = code
        blocks.append(
            f'<div style="margin:0 0 20px;padding:0 0 16px;border-bottom:1px solid {_LINE};">'
            f'<p style="font-size:16px;font-weight:700;margin:0 0 8px;color:{_TEXT};">{_e(title)}</p>'
            f'{body}</div>')
    return "".join(blocks)


def render_email(snapshot: dict, *, schedule_name: str, open_url: str,
                 unsubscribe_url: str) -> tuple[str, str]:
    """(subject, html) for one recipient. The unsubscribe link is personal."""
    intro = render_es("report_intro", name=schedule_name, date=snapshot["local_date"],
                      zone=snapshot["timezone"])
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{_e(render_es("report_title"))}</title></head>
<body style="margin:0;padding:24px;background:#f9fafb;font-family:system-ui,sans-serif;color:{_TEXT};font-size:14px;line-height:1.6;">
  <div style="max-width:620px;margin:0 auto;background:#ffffff;border:1px solid {_LINE};border-radius:10px;padding:28px;">
    <p style="font-size:20px;font-weight:700;margin:0 0 4px;">{_e(render_es("report_title"))}</p>
    <p style="color:{_DIM};margin:0 0 20px;">{_e(intro)}</p>
    {render_sections_html(snapshot)}
    <p style="margin:0 0 16px;"><a href="{_e(open_url)}" style="color:#0C3A40;font-weight:600;">{_e(render_es("report_footer_open_app"))}</a></p>
    <p style="color:{_DIM};font-size:12px;margin:0;">{_e(render_es("report_footer_why"))}
      <a href="{_e(unsubscribe_url)}" style="color:{_DIM};">{_e(render_es("report_footer_unsubscribe"))}</a></p>
  </div>
</body></html>"""
    return render_es("report_subject", name=schedule_name), html
