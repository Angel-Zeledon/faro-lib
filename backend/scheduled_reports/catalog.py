"""The fixed catalogue a scheduled report is chosen from.

Four sections, each built from data the product already has. The tuple is the
CONTRACT with the Rust API (`backend-rs/src/routes/scheduled_reports.rs`
re-declares it and a unit test reads this file), and with the frontend copy
(`scheduledReports.section.<code>`).
"""

from __future__ import annotations

PURCHASING_SUMMARY = "purchasing_summary"
BUDGET_VS_SPEND = "budget_vs_spend"
COMMITTED_DEMAND = "committed_demand"
SUPPLIER_SCORECARD = "supplier_scorecard"

SECTIONS: tuple[str, ...] = (
    PURCHASING_SUMMARY,
    BUDGET_VS_SPEND,
    COMMITTED_DEMAND,
    SUPPLIER_SCORECARD,
)

FREQUENCIES: tuple[str, ...] = ("weekly", "monthly")

# Three runs in a row that could not build or hand over the report and the
# schedule stops by itself (and says so): a report nobody receives is worse
# than a report that is visibly paused.
MAX_CONSECUTIVE_FAILURES = 3

# How late a due run may still be made. A weekly report mailed two days late
# describes a week the readers already lived through; past the window the run
# is recorded as skipped instead of sent stale.
CATCHUP_HOURS = {"weekly": 12, "monthly": 48}

# Rows a section lists before it says how many it left out.
MAX_BUDGET_ROWS = 10
MAX_SUPPLIER_ROWS = 10
COMMITMENT_LIMIT = 2000

# The worker looks this often. A run is at most this late.
POLL_SECONDS = 60
# A run row still `building` after this long belonged to a worker that died.
STALE_RUN_SECONDS = 600
MAX_RUN_ATTEMPTS = 3

LOOP_NAME = "scheduled_reports"
