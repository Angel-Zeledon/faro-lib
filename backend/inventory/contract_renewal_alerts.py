"""The daily contract-renewal alert pass.

Runs inside the 08:00 UTC loop (`workers/worker.py::_inventory_alert_loop`),
right after the contract materialisation. For every ACTIVE contract that has
not been renewed it asks `contract_renewal.due_alert` whether an alert is owed
today and, if so, records `supply_contract.renewal_due` through the one alert
registry (`activity/events.py`), so the bell, the alert history and the
Rust-served `/alerts` all show it with no second channel.

Idempotence without a new table: the event row IS the record that the alert
was sent. Before recording, the pass reads the contract's earlier
`supply_contract.renewal_due` rows and skips a `(expiry date, lead time)` it
already raised, so a catch-up re-run of the day, or two workers racing, never
raise one twice, and extending a contract's term (a new end date) starts its
countdown afresh. The check and the write sit under one advisory lock per
contract. One contract failing is logged with its id and never stops the
others (the same rule as the materialisation pass).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from backend.activity.events import record_event
from backend.db.connection import query, query_one, transaction
from backend.inventory import contract_renewal as renewal

log = logging.getLogger(__name__)

ALERT_ACTION = "supply_contract.renewal_due"
SYSTEM_USER = "system"


def _sent_pairs(rows: list[dict]) -> set:
    out = set()
    for r in rows:
        ctx = r.get("context") or {}
        lead = ctx.get("lead_days")
        out.add((ctx.get("expiry_date"), None if lead is None else int(lead)))
    return out


def _already_renewed_sql() -> str:
    """A contract counts as renewed when a non-cancelled contract (its current
    revision) names it in `renewed_from_root_id`. A cancelled renewal frees it
    to be renewed again, and keeps it in the alert pass meanwhile."""
    return """EXISTS (SELECT 1 FROM supply_contracts r
                       WHERE r.tenant_id = s.tenant_id
                         AND r.renewed_from_root_id = s.root_id
                         AND r.superseded_by IS NULL
                         AND r.status <> 'cancelled')"""


def alert_renewals(tenant_id: Optional[str] = None, today: Optional[date] = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    clauses, params = ["s.superseded_by IS NULL", "s.status = 'active'",
                       f"NOT {_already_renewed_sql()}"], []
    if tenant_id:
        clauses.append("s.tenant_id = %s")
        params.append(tenant_id)
    rows = query(
        f"""SELECT s.tenant_id, s.root_id, s.customer, s.period_end, s.notice_days,
                   s.auto_renew, s.renewal_lead_days
              FROM supply_contracts s WHERE {' AND '.join(clauses)}""", tuple(params))
    summary = {"contracts": len(rows), "alerted": 0, "failed": 0}
    for row in rows:
        try:
            if _alert_one(row, today):
                summary["alerted"] += 1
        except Exception as e:  # noqa: BLE001 - one contract must not stop the pass
            summary["failed"] += 1
            log.error("Contract renewal alert failed tenant=%s contract=%s: %s",
                      row["tenant_id"], row["root_id"], e, exc_info=True)
    return summary


def _alert_one(row: dict, today: date) -> bool:
    leads = renewal.effective_lead_days(row.get("renewal_lead_days"))
    period_end = row["period_end"]
    # Cheap exit before taking a lock: nothing is owed yet.
    if renewal.due_alert(period_end, row.get("notice_days"), leads, today, set()) is None:
        return False
    with transaction() as conn:
        query_one("SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                  (f"contract_renewal_alert:{row['tenant_id']}:{row['root_id']}",), conn=conn)
        sent = _sent_pairs(query(
            """SELECT context FROM activity_logs
                WHERE tenant_id = %s AND action = %s AND resource = %s""",
            (row["tenant_id"], ALERT_ACTION, row["root_id"]), conn=conn))
        due = renewal.due_alert(period_end, row.get("notice_days"), leads, today, sent)
        if due is None:
            return False
        notice = (None if row.get("notice_days") is None
                  else (period_end - timedelta(days=int(row["notice_days"]))).isoformat())
        record_event(
            row["tenant_id"], SYSTEM_USER, ALERT_ACTION, resource=row["root_id"],
            reason=due["reason"],
            details={"customer": row["customer"], "days_left": due["days_left"],
                     "lead_days": due["lead_days"], "expiry_date": due["expiry_date"],
                     "notice_deadline": notice, "auto_renew": bool(row.get("auto_renew"))})
    return True


def run_daily_contract_renewal_alerts() -> dict:
    summary = alert_renewals()
    log.info("Contract renewal alerts: %s", summary)
    return summary
