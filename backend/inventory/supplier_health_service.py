"""
Supplier health: contact-data completeness (feature 2.5) and lead-time
deviation detection (feature 3.3).

Both answer the same underlying question — "which supplier is about to cost
me something?" — from data Faro already owns:

  2.5  `POST /inventory/po/{id}/send` silently skips any supplier with no
       email and no whatsapp on file (and any supplier name that has no
       supplier record at all). The buyer only finds out when the goods
       never arrive. get_contact_health() surfaces that gap BEFORE sending.

  3.3  supplier_lead_time_obs accumulates one real (order date -> reception
       date) observation per reception. get_lead_time_deviations() detects
       when a supplier's recent lead time has shifted away from its own
       history — "Acme está tardando 12 días, no 7".
"""

from __future__ import annotations

import logging
import statistics
from typing import Optional

from backend.db.connection import query

log = logging.getLogger(__name__)


# ── 2.5 Supplier contact health ──────────────────────────────────────────────
#
# "Incomplete contact" is defined here to match EXACTLY what
# send_po_to_suppliers() skips, so the warning never lies:
#   - a registered supplier with neither email nor whatsapp, or
#   - a supplier name on a PO line with no supplier record at all.
# phone is deliberately NOT counted: nothing in the send path uses it.

# English text, per the language mandate: `reason` is the stable code the
# frontend renders through i18n, and `reason_text` is only the readable
# fallback for a consumer that has no catalogue.
_SEND_BLOCKING_REASONS = {
    "no_supplier_record": "No supplier record",
    "no_contact":         "Neither email nor WhatsApp",
}

# PO line statuses that were actually ordered (mirrors reception_service._ORDERED)
_ORDERED = ("approved", "modified")

# PO header states that still have something to send / receive
_OPEN_PO_STATES = ("pending", "partial")


def get_contact_health(tenant_id: str) -> list[dict]:
    """
    Every supplier the PO-send path would skip, with the context needed to
    decide whether it matters right now.

    Returns one row per supplier name:
        supplier      supplier name as it appears on PO lines / supplier record
        supplier_id   None when there is no supplier record at all
        reason        'no_supplier_record' | 'no_contact'
        reason_text   readable English fallback; the frontend renders `reason`
        has_email / has_whatsapp
                      booleans (both False by construction)
        open_pos      count of open POs (pending/partial) naming it
        has_open_pos  open_pos > 0

    Both relevant and not-yet-relevant suppliers are returned, each flagged.
    The caller decides which to surface — see the module note in the API
    layer on why filtering happens per surface rather than here.
    """
    # Registered suppliers with no usable contact channel.
    registered = query(
        """SELECT id AS supplier_id, name AS supplier,
                  (email    IS NOT NULL AND email    <> '') AS has_email,
                  (whatsapp IS NOT NULL AND whatsapp <> '') AS has_whatsapp
           FROM suppliers
           WHERE tenant_id = %s
             AND active = TRUE
             AND COALESCE(email, '')    = ''
             AND COALESCE(whatsapp, '') = ''
           ORDER BY name""",
        (tenant_id,),
    )

    # Supplier names used on ordered PO lines that have NO supplier record.
    # These are just as un-sendable, and easier to miss because nothing in
    # the suppliers screen hints they exist.
    orphans = query(
        f"""SELECT DISTINCT TRIM(poi.supplier) AS supplier
            FROM inventory_po_items poi
            WHERE poi.tenant_id = %s
              AND poi.status IN %s
              AND COALESCE(TRIM(poi.supplier), '') <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM suppliers s
                  WHERE s.tenant_id = poi.tenant_id
                    AND s.active = TRUE
                    AND LOWER(s.name) = LOWER(TRIM(poi.supplier))
              )
            ORDER BY supplier""",
        (tenant_id, _ORDERED),
    )

    # How many still-open POs name each supplier — this is what makes the
    # warning actionable rather than housekeeping noise.
    open_counts_rows = query(
        f"""SELECT LOWER(TRIM(poi.supplier)) AS key, COUNT(DISTINCT pol.id)::int AS n
            FROM inventory_po_items poi
            JOIN inventory_po_log pol ON pol.id = poi.po_log_id
            WHERE poi.tenant_id = %s
              AND poi.status IN %s
              AND COALESCE(TRIM(poi.supplier), '') <> ''
              AND pol.reception_status IN %s
            GROUP BY LOWER(TRIM(poi.supplier))""",
        (tenant_id, _ORDERED, _OPEN_PO_STATES),
    )
    open_counts = {r["key"]: r["n"] for r in open_counts_rows}

    out: list[dict] = []
    for r in registered:
        out.append(_contact_row(r["supplier"], r["supplier_id"], "no_contact", open_counts))
    for r in orphans:
        out.append(_contact_row(r["supplier"], None, "no_supplier_record", open_counts))

    # Relevant first (most open orders), then alphabetical — the buyer should
    # see the supplier blocking today's order before the dormant one.
    out.sort(key=lambda d: (-d["open_pos"], d["supplier"].lower()))
    return out


def _contact_row(supplier: str, supplier_id: Optional[str], reason: str,
                 open_counts: dict[str, int]) -> dict:
    n_open = open_counts.get(supplier.strip().lower(), 0)
    return {
        "supplier":             supplier,
        "supplier_id":           supplier_id,
        "reason":                reason,
        "reason_text":          _SEND_BLOCKING_REASONS[reason],
        "has_email":           False,
        "has_whatsapp":        False,
        "open_pos":    n_open,
        "has_open_pos": n_open > 0,
    }


# ── 3.3 Lead-time deviation ──────────────────────────────────────────────────
#
# THRESHOLD RATIONALE (why these numbers and not a magic one)
# ------------------------------------------------------------------
# A supplier's lead time is a *process*, and the canonical discipline for
# "has this process shifted?" is statistical process control (SPC). We apply
# SPC's standard out-of-control rule — a point beyond 3 sigma — but with two
# adaptations forced by this data's shape:
#
# 1. ROBUST location/scale (median + IQR) instead of mean + standard
#    deviation. Lead-time distributions are right-skewed and bounded below
#    (a delivery can be arbitrarily late, never negatively early), and n is
#    small. With mean/SD, the very late deliveries we want to DETECT inflate
#    the baseline and the sigma simultaneously, raising the alert bar exactly
#    when it should fire — the classic masking problem.
#
#    Scale is estimated as IQR / 1.349 — the standard normal-consistent
#    robust estimator (1.349 is the interquartile range of a standard
#    normal), so "3 sigma" keeps its usual meaning: one-sided p ~ 0.001.
#    IQR rather than MAD specifically because a chronically erratic supplier
#    tends to have a BIMODAL history (some deliveries quick, some very
#    slow). MAD measures spread around the median and collapses toward zero
#    on such data, making an ordinary slow delivery look like a shift; IQR
#    spans the bulk of the distribution and correctly reports that this
#    supplier's normal range is simply wide. See
#    TestDeviationRuleStaysQuiet::test_naturally_erratic_supplier_does_not_fire,
#    which MAD fails and IQR passes.
#
# 2. A PRACTICAL-SIGNIFICANCE FLOOR on sigma. A perfectly consistent supplier
#    has IQR = 0, which would make the z-score infinite and fire on a
#    one-day wobble. The floor (the larger of 1 day and 10% of the baseline)
#    means an alert must be both statistically detectable AND materially
#    late. Without it the feature would be technically correct and
#    practically unusable.
#
# The test is ONE-SIDED: only being slower than history is a problem. A
# supplier that suddenly delivers faster is good news, not an alert.
#
# Sample-size gates: the recent window is up to the last 3 receptions
# summarised by their median, so a single freak delivery cannot fire an
# alert; at least 2 of them must exist. The baseline is every observation
# BEFORE that window (not including it — including it would dilute the very
# shift we are testing for) and needs at least 4 points for the IQR to mean
# anything. The window shrinks rather than starving the baseline, so a
# supplier needs >= 6 recorded receptions before it can ever be alerted on.
# That is a real limitation, stated rather than tuned away: you cannot
# establish that a distribution moved from three data points.

# Maximum number of most-recent receptions summarised as "how it's going now".
RECENT_WINDOW = 3
# Minimum observations inside that window before we'll judge it.
MIN_RECENT = 2
# Minimum observations in the historical baseline for the IQR to be meaningful.
MIN_BASELINE = 4
# SPC out-of-control rule: 3 sigma, one-sided.
Z_THRESHOLD = 3.0
# Interquartile range of a standard normal — divides IQR into a sigma estimate.
_IQR_TO_SIGMA = 1.349
# Practical-significance floor on sigma (days, and as a fraction of baseline).
_SIGMA_FLOOR_DAYS = 1.0
_SIGMA_FLOOR_FRACTION = 0.10


def _robust_sigma(values: list[float], baseline: float) -> float:
    """IQR / 1.349, floored so a zero-variance history can't fire on noise."""
    if len(values) >= 2:
        q1, _, q3 = statistics.quantiles(sorted(values), n=4, method="inclusive")
        sigma = (q3 - q1) / _IQR_TO_SIGMA
    else:
        sigma = 0.0
    floor = max(_SIGMA_FLOOR_DAYS, _SIGMA_FLOOR_FRACTION * baseline)
    return max(sigma, floor)


def get_lead_time_deviations(tenant_id: str) -> list[dict]:
    """
    Suppliers whose recent lead time is significantly slower than their own
    history, by the robust 3-sigma SPC rule documented above.

    Returns one row per ALERTING supplier (callers get a list they can render
    directly; non-alerting suppliers are omitted, not returned with a false
    flag, so a truthiness check on the list is meaningful):

        supplier                supplier name
        lead_time_historical    baseline median, days
        lead_time_recent        recent median, days
        deviation_days          recent - baseline (always > 0 here)
        z_score                 robust one-sided z
        sigma                   robust sigma actually used (after the floor)
        n_baseline / n_recent
        severity                'high' when z >= 2 * Z_THRESHOLD else 'medium'
        message                 English fallback sentence
        message_code / message_params
                                what the frontend renders in the reader's
                                language. The sentence used to exist only as
                                Spanish built here, so /proveedores/scorecard
                                showed "Acme está tardando 12 días, no 7" with
                                the rest of the page in English.
    """
    rows = query(
        """SELECT supplier, lead_time_days, observed_at
           FROM supplier_lead_time_obs
           WHERE tenant_id = %s
           ORDER BY LOWER(supplier), observed_at""",
        (tenant_id,),
    )
    if not rows:
        return []

    by_supplier: dict[str, list[dict]] = {}
    for r in rows:
        by_supplier.setdefault(r["supplier"], []).append(r)

    out: list[dict] = []
    for supplier, obs in by_supplier.items():
        alert = _evaluate_supplier(supplier, [float(o["lead_time_days"]) for o in obs])
        if alert:
            out.append(alert)

    out.sort(key=lambda d: -d["z_score"])
    return out


def _evaluate_supplier(supplier: str, series: list[float]) -> Optional[dict]:
    """
    Applies the deviation rule to one supplier's chronologically-ordered
    lead-time observations. Returns an alert dict, or None when the supplier
    is within its normal range or has too little history to judge.

    Split out from get_lead_time_deviations so the statistics can be tested
    directly on a series, without staging six PO receptions per case.
    """
    if len(series) < MIN_BASELINE + MIN_RECENT:
        return None

    # Shrink the recent window rather than starve the baseline: with exactly
    # 6 observations we compare the last 2 against the first 4, instead of
    # taking 3 and leaving an unusable 3-point baseline.
    window = min(RECENT_WINDOW, len(series) - MIN_BASELINE)
    recent   = series[-window:]
    baseline = series[:-window]
    if len(recent) < MIN_RECENT or len(baseline) < MIN_BASELINE:
        return None

    baseline_median = statistics.median(baseline)
    recent_median   = statistics.median(recent)
    delta = recent_median - baseline_median
    if delta <= 0:
        return None  # one-sided: faster than history is not an alert

    sigma = _robust_sigma(baseline, baseline_median)
    z = delta / sigma
    if z < Z_THRESHOLD:
        return None

    recent_s, baseline_s = _fmt_days(recent_median), _fmt_days(baseline_median)
    return {
        "supplier":             supplier,
        "lead_time_historical": round(baseline_median, 1),
        "lead_time_recent":     round(recent_median, 1),
        "deviation_days":       round(delta, 1),
        "z_score":              round(z, 2),
        "sigma":                round(sigma, 2),
        "n_baseline":           len(baseline),
        "n_recent":             len(recent),
        "severity":             "high" if z >= 2 * Z_THRESHOLD else "medium",
        "message":              f"{supplier} is taking {recent_s} days, not {baseline_s}",
        "message_code":         "supplier_taking_longer",
        "message_params":       {"supplier": supplier, "recent": recent_s,
                                 "historical": baseline_s},
    }


def _fmt_days(v: float) -> str:
    """9.0 -> '9', 9.5 -> '9.5' — avoids '9.0 días' in user-facing text."""
    return str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


# ── Daily notification (worker integration) ──────────────────────────────────

def run_daily_supplier_lead_time_alerts() -> None:
    """
    Called once a day by the inventory alert scheduler (8:00 UTC, see
    backend/workers/worker.py). Emails each tenant's admins about suppliers
    that have drifted off their historical lead time.

    Deliberately a separate pass from run_daily_inventory_alerts(): that one
    returns early for tenants with no PEDIR_YA/PEDIR_PRONTO SKUs, and a
    supplier drifting late is worth knowing precisely when stock still looks
    fine — it is the early warning that the semáforo hasn't caught yet.
    """
    from backend.config import settings
    from backend.inventory.service import (
        get_tenant_alert_recipients,
        record_notification_delivery,
    )
    from backend.notifications import email as email_mod
    from backend.notifications.email import send_supplier_lead_time_alert_email

    tenants = query("SELECT DISTINCT tenant_id FROM supplier_lead_time_obs")
    log.info("supplier_lead_time_alert: checking %d tenants", len(tenants))

    for row in tenants:
        tid = row["tenant_id"]
        try:
            deviations = get_lead_time_deviations(tid)
            if not deviations:
                continue

            app_url = getattr(settings, "frontend_url", "http://localhost:3000")
            scorecard_url = f"{app_url}/inventory/suppliers/scorecard"

            for r in get_tenant_alert_recipients(tid):
                if not r.get("email"):
                    continue
                delivered = send_supplier_lead_time_alert_email(
                    to=r["email"], deviations=deviations, scorecard_url=scorecard_url,
                )
                if not delivered:
                    log.warning("lead-time alert email not delivered to=%s", r["email"])
                record_notification_delivery(
                    tid, r["id"], "supplier_lead_time_alert_email", delivered,
                    context={
                        "channel": "email",
                        "recipient": r["email"],
                        "suppliers": len(deviations),
                        **({} if delivered else {"reason": email_mod.failure_reason()}),
                    },
                )
        except Exception as e:
            log.error("supplier_lead_time_alert: tenant=%s error=%s", tid, e)
