"""Contract renewal tracking: the pure rules (backend/inventory/contract_renewal.py)
and the DIFFERENTIAL fixture shared with the Rust implementation.

`tests/contract/renewal_diff_cases.json` holds randomly generated inputs and
the outputs THIS (Python) reference gives for them. The Rust unit test
`contract_renewal::tests::matches_the_python_reference` replays every case and
demands the same numbers. `test_the_fixture_is_current` regenerates the cases
and fails when the committed file differs, so a change to a rule here cannot
leave the Rust side checking yesterday's answers. Regenerate with:

    REGEN_RENEWAL_FIXTURE=1 python -m pytest backend/tests/test_contract_renewal_pure.py -k fixture
"""

from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.errors import AppError
from backend.inventory import contract_renewal as cr
from backend.inventory import supply_contract_service as svc

# Pure maths, no database: runs even when Postgres is down.
pytestmark = pytest.mark.offline

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "contract" / "renewal_diff_cases.json"
SEED = 20261006


def d(s: str) -> date:
    return date.fromisoformat(s)


# ── Hand-written rules ───────────────────────────────────────────────────────

def test_lead_days_default_is_not_a_choice():
    assert cr.clean_lead_days(None) is None
    assert cr.effective_lead_days(None) == [60, 30, 7]
    assert cr.effective_lead_days([]) == [60, 30, 7]
    assert cr.clean_lead_days([7, 30, 30, 90]) == [90, 30, 7]
    assert cr.effective_lead_days([7, 90]) == [90, 7]


@pytest.mark.parametrize("bad", [[], [0], [-5], [731], [1.5], ["7"], [True], "30",
                                  [1, 2, 3, 4, 5, 6, 7]])
def test_lead_days_refused(bad):
    with pytest.raises(AppError) as e:
        cr.clean_lead_days(bad)
    assert e.value.code == "supply_contract_lead_days_invalid"


@pytest.mark.parametrize("bad", [-1, 731, 1.5, "30", True])
def test_notice_days_refused(bad):
    with pytest.raises(AppError) as e:
        cr.clean_notice_days(bad)
    assert e.value.code == "supply_contract_notice_days_invalid"


def test_notice_days_none_and_zero_differ():
    assert cr.clean_notice_days(None) is None
    assert cr.clean_notice_days("") is None
    assert cr.clean_notice_days(0) == 0


def test_buckets():
    today = d("2027-06-01")
    v = lambda end, notice=None: cr.renewal_view(d(end), notice, False, [60, 30, 7], today)["bucket"]  # noqa: E731
    assert v("2027-05-31") == "expired"
    assert v("2027-06-01") == "due_soon"
    assert v("2027-07-31") == "due_soon"
    assert v("2027-08-01") == "upcoming"
    assert v("2027-06-21", 30) == "notice_passed"


def test_alert_is_the_nearest_crossed_lead_and_once():
    none: set = set()
    a = cr.due_alert(d("2027-06-06"), None, [60, 30, 7], d("2027-06-01"), none)
    assert a["lead_days"] == 7 and a["reason"] == "contract_expiring"
    assert cr.due_alert(d("2027-06-06"), None, [60, 30, 7], d("2027-06-02"),
                        {("2027-06-06", 7)}) is None
    assert cr.due_alert(d("2027-08-01"), None, [60, 30, 7], d("2027-06-01"), none) is None
    b = cr.due_alert(d("2027-07-31"), 30, [60, 30, 7], d("2027-06-01"), none)
    assert b["lead_days"] == 30 and b["reason"] == "contract_notice_deadline"
    c = cr.due_alert(d("2027-05-01"), None, [60, 30, 7], d("2027-06-01"), none)
    assert c["reason"] == "contract_expired" and c["lead_days"] is None
    assert cr.due_alert(d("2027-05-01"), None, [60, 30, 7], d("2027-06-02"),
                        {("2027-05-01", None)}) is None
    # a new end date restarts the countdown
    assert cr.due_alert(d("2028-06-06"), None, [60, 30, 7], d("2028-06-01"),
                        {("2027-06-06", 7)}) is not None


def test_next_term_keeps_month_boundaries():
    assert cr.next_term(d("2027-01-01"), d("2027-12-31"))[:3] == (
        d("2028-01-01"), d("2028-12-31"), 12)
    assert cr.next_term(d("2028-01-01"), d("2028-12-31"))[:2] == (
        d("2029-01-01"), d("2029-12-31"))
    assert cr.next_term(d("2027-01-01"), d("2027-02-10"))[:3] == (
        d("2027-02-11"), d("2027-03-23"), None)
    assert cr.next_term(d("2027-02-15"), d("2027-03-14"))[:3] == (
        d("2027-03-15"), d("2027-04-14"), 1)


def test_explicit_release_out_of_the_renewed_term_refuses():
    # Not a whole number of months: shifted by days, a release at the very end
    # still lands on the new end (same length), so build a real mismatch:
    # a release dated outside its own term (as old data could be).
    rel = [{"sku": "A", "date": d("2027-03-01"), "quantity": 5.0}]
    with pytest.raises(AppError) as e:
        cr.renewal_terms(d("2027-01-01"), d("2027-01-31"), rel)
    assert e.value.code == "supply_contract_renewal_release_out_of_period"
    assert e.value.status_code == 422


def test_comparison_late_overdue_undated():
    today = d("2027-03-15")
    releases = [{"sku": "A", "date": d(x), "quantity": 100.0}
                for x in ("2027-01-01", "2027-02-01", "2027-03-01", "2027-04-01")]
    commitments = [
        {"sku": "A", "contract_release_date": d("2027-01-01"), "quantity": 100.0,
         "status": "fulfilled", "fulfilled_on": d("2027-01-01")},
        {"sku": "A", "contract_release_date": d("2027-02-01"), "quantity": 90.0,
         "status": "fulfilled", "fulfilled_on": d("2027-02-05")},
        {"sku": "A", "contract_release_date": d("2027-03-01"), "quantity": 100.0,
         "status": "open", "fulfilled_on": None},
        {"sku": "A", "contract_release_date": d("2027-04-01"), "quantity": 100.0,
         "status": "open", "fulfilled_on": None},
    ]
    r = cr.commitment_comparison(releases, commitments, today)
    assert (r["committed_units"], r["due_to_date"], r["delivered_units"]) == (400.0, 300.0, 190.0)
    assert r["fill_rate_pct"] == 63.3 and r["term_fill_pct"] == 47.5
    assert (r["late_deliveries"], r["late_units"], r["max_days_late"]) == (1, 90.0, 4)
    assert (r["overdue_open"], r["overdue_open_units"]) == (1, 100.0)
    assert r["fulfilled_undated"] == 0 and r["shortfall_to_date"] == 110.0
    # no fulfilment date: counted, never on time and never late
    commitments[1]["fulfilled_on"] = None
    r2 = cr.commitment_comparison(releases, commitments, today)
    assert r2["fulfilled_undated"] == 1 and r2["late_deliveries"] == 0
    # nothing due: no invented 100%
    r3 = cr.commitment_comparison(releases, [], d("2026-01-01"))
    assert r3["fill_rate_pct"] is None and r3["term_fill_pct"] == 0.0


def test_comparison_agrees_with_contract_progress_on_the_shared_totals():
    """The new comparison and the existing progress screen must not disagree
    about what was delivered or due."""
    today = d("2027-03-15")
    contract = {"schedule_kind": "monthly", "period_start": d("2027-01-01"),
                "period_end": d("2027-06-30"),
                "lines": [{"sku": "A", "total_quantity": 600.0}], "releases": None}
    releases = svc.expand_releases(contract)
    commitments = [
        {"sku": "A", "contract_release_date": d("2027-01-01"), "quantity": 100.0,
         "status": "fulfilled", "fulfilled_on": d("2027-01-02")},
        {"sku": "A", "contract_release_date": d("2027-02-01"), "quantity": 80.0,
         "status": "fulfilled", "fulfilled_on": d("2027-02-01")},
        {"sku": "A", "contract_release_date": d("2027-03-01"), "quantity": 100.0,
         "status": "open", "fulfilled_on": None},
    ]
    progress = svc.contract_progress(releases, commitments, today, 0.0, 180, "active")
    cmp_ = cr.commitment_comparison(releases, commitments, today)
    assert cmp_["committed_units"] == progress["scheduled_total"]
    assert cmp_["due_to_date"] == progress["due_to_date"]
    assert cmp_["delivered_units"] == progress["delivered"]
    assert cmp_["overdue_open"] == progress["overdue_count"]
    assert cmp_["overdue_open_units"] == progress["overdue_units"]


# ── The differential fixture ─────────────────────────────────────────────────

def _iso(x):
    return x.isoformat() if isinstance(x, date) else x


def _rel_json(releases):
    return [{"sku": r["sku"], "date": _iso(r["date"]), "quantity": r["quantity"]} for r in releases]


def _rand_date(rng: random.Random, lo=date(2024, 1, 1), span=1800) -> date:
    return lo + timedelta(days=rng.randrange(span))


def _rand_qty(rng: random.Random) -> float:
    r = rng.random()
    if r < 0.5:
        return float(rng.randrange(1, 5000))
    if r < 0.85:
        return round(rng.uniform(0.5, 800), 2)
    return round(rng.uniform(0.01, 9), 4)


def _rand_term(rng: random.Random):
    start = _rand_date(rng)
    r = rng.random()
    if r < 0.5:                                    # whole months
        months = rng.choice([1, 2, 3, 6, 12, 12, 24])
        end = svc._add_months(start, months) - timedelta(days=1)
    elif r < 0.8:                                  # arbitrary day count
        end = start + timedelta(days=rng.randrange(20, 500))
    else:                                          # month-end starts
        start = date(start.year, start.month, 1)
        start = svc._add_months(start, 1) - timedelta(days=1)
        end = svc._add_months(start, rng.choice([1, 3, 12]))
    return start, end


def _case_expand(rng):
    start, end = _rand_term(rng)
    kind = rng.choice(["monthly", "monthly", "weekly", "explicit"])
    skus = rng.sample(["A", "B", "C-1", "sku z", "Ñ"], rng.randrange(1, 4))
    lines = [{"sku": s, "total_quantity": _rand_qty(rng)} for s in skus]
    releases = None
    if kind == "explicit":
        releases = []
        span = (end - start).days
        for s in skus:
            for _ in range(rng.randrange(1, 6)):
                releases.append({"sku": s, "date": start + timedelta(days=rng.randrange(span + 1)),
                                 "quantity": _rand_qty(rng)})
    contract = {"schedule_kind": kind, "period_start": start, "period_end": end,
                "lines": lines, "releases": releases}
    out = svc.expand_releases(contract)
    return {"fn": "expand_releases",
            "input": {"schedule_kind": kind, "period_start": start.isoformat(),
                      "period_end": end.isoformat(), "lines": lines,
                      "releases": None if releases is None else _rel_json(releases)},
            "output": _rel_json(out)}, contract, out


def _leads(rng):
    return rng.choice([[60, 30, 7], [90, 30], [14], [180, 60, 30, 14, 7, 1], [30, 7]])


def _case_view(rng):
    today = _rand_date(rng, span=400)
    end = today + timedelta(days=rng.randrange(-90, 260))
    notice = rng.choice([None, None, 0, 7, 30, 60, 90])
    auto = rng.random() < 0.5
    leads = _leads(rng)
    return {"fn": "renewal_view",
            "input": {"period_end": end.isoformat(), "notice_days": notice, "auto_renew": auto,
                      "lead_days": leads, "today": today.isoformat()},
            "output": cr.renewal_view(end, notice, auto, leads, today)}


def _case_alert(rng):
    today = _rand_date(rng, span=400)
    end = today + timedelta(days=rng.randrange(-40, 130))
    notice = rng.choice([None, None, 0, 7, 30, 60])
    leads = _leads(rng)
    sent = []
    if rng.random() < 0.5:
        for t in rng.sample(leads, rng.randrange(0, len(leads) + 1)):
            sent.append([end.isoformat(), t])
        if rng.random() < 0.3:
            sent.append([end.isoformat(), None])
        if rng.random() < 0.3:
            sent.append([(end - timedelta(days=365)).isoformat(), leads[0]])
    out = cr.due_alert(end, notice, leads, today, {(a, b) for a, b in sent})
    return {"fn": "due_alert",
            "input": {"period_end": end.isoformat(), "notice_days": notice, "lead_days": leads,
                      "today": today.isoformat(), "sent": sent},
            "output": out}


def _case_terms(rng):
    start, end = _rand_term(rng)
    releases = None
    if rng.random() < 0.6:
        span = (end - start).days
        releases = []
        for s in rng.sample(["A", "B", "C"], rng.randrange(1, 3)):
            for _ in range(rng.randrange(1, 5)):
                off = rng.randrange(span + 1)
                if rng.random() < 0.05:
                    off = span + rng.randrange(1, 40)       # outside its own term
                releases.append({"sku": s, "date": start + timedelta(days=off),
                                 "quantity": _rand_qty(rng)})
        releases.sort(key=lambda r: (r["date"], r["sku"]))
    inp = {"period_start": start.isoformat(), "period_end": end.isoformat(),
           "releases": None if releases is None else _rel_json(releases)}
    try:
        ns, ne, shifted = cr.renewal_terms(start, end, releases)
        out = {"period_start": ns.isoformat(), "period_end": ne.isoformat(),
               "releases": None if shifted is None else _rel_json(shifted)}
    except AppError as e:
        out = {"error": e.code}
        if e.code == "supply_contract_renewal_release_out_of_period":
            out.update({"sku": e.params["sku"], "date": e.params["date"],
                        "shifted": e.params["shifted"]})
    return {"fn": "renewal_terms", "input": inp, "output": out}


def _case_comparison(rng):
    _, contract, releases = _case_expand(rng)
    if contract["schedule_kind"] == "explicit" and not releases:
        releases = []
    start, end = contract["period_start"], contract["period_end"]
    today = start + timedelta(days=rng.randrange(-30, (end - start).days + 60))
    commitments = []
    for r in releases:
        roll = rng.random()
        if roll < 0.2:
            continue                                    # never materialised
        status = rng.choice(["open", "fulfilled", "fulfilled", "fulfilled", "cancelled"])
        qty = r["quantity"] if rng.random() < 0.6 else round(r["quantity"] * rng.uniform(0.2, 1.3), 2)
        done = None
        if status == "fulfilled" and rng.random() > 0.1:
            done = r["date"] + timedelta(days=rng.randrange(-5, 25))
        commitments.append({"sku": r["sku"], "contract_release_date": r["date"],
                            "quantity": qty, "status": status, "fulfilled_on": done})
    if rng.random() < 0.3:                              # a SKU no longer on the schedule
        commitments.append({"sku": "GONE", "contract_release_date": start, "quantity": 7.0,
                            "status": "fulfilled", "fulfilled_on": start})
    rng.shuffle(commitments)
    out = cr.commitment_comparison(releases, commitments, today)
    return {"fn": "commitment_comparison",
            "input": {"today": today.isoformat(), "releases": _rel_json(releases),
                      "commitments": [{**c, "contract_release_date": _iso(c["contract_release_date"]),
                                       "fulfilled_on": _iso(c["fulfilled_on"])}
                                      for c in commitments]},
            "output": out}


def generate() -> dict:
    rng = random.Random(SEED)
    cases = []
    for _ in range(90):
        cases.append(_case_expand(rng)[0])
    for _ in range(90):
        cases.append(_case_view(rng))
    for _ in range(120):
        cases.append(_case_alert(rng))
    for _ in range(90):
        cases.append(_case_terms(rng))
    for _ in range(140):
        cases.append(_case_comparison(rng))
    return {"seed": SEED, "reference": "backend/inventory/contract_renewal.py", "cases": cases}


def _dump(doc: dict) -> str:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False, default=str) + "\n"


def test_the_fixture_is_current():
    doc = generate()
    fresh = _dump(doc)
    if os.environ.get("REGEN_RENEWAL_FIXTURE"):
        FIXTURE.write_text(fresh, encoding="utf-8", newline="\n")
    assert FIXTURE.exists(), "run with REGEN_RENEWAL_FIXTURE=1 to create the differential fixture"
    stored = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert stored == fresh, (
        "tests/contract/renewal_diff_cases.json is stale: the Python reference changed. "
        "Regenerate it (REGEN_RENEWAL_FIXTURE=1) and run `cargo test` so Rust is checked "
        "against the new answers.")


def test_the_fixture_exercises_every_branch():
    kinds = {}
    for c in generate()["cases"]:
        kinds.setdefault(c["fn"], []).append(c)
    assert len(kinds) == 5
    assert any(c["output"].get("error") for c in kinds["renewal_terms"])
    assert any(c["output"] and c["output"]["reason"] == "contract_expired" for c in kinds["due_alert"])
    assert any(c["output"] and c["output"]["reason"] == "contract_notice_deadline" for c in kinds["due_alert"])
    assert any(c["output"] is None for c in kinds["due_alert"])
    buckets = {c["output"]["bucket"] for c in kinds["renewal_view"]}
    assert buckets == {"expired", "notice_passed", "due_soon", "upcoming"}
    cmp_ = [c["output"] for c in kinds["commitment_comparison"]]
    assert any(o["late_deliveries"] for o in cmp_) and any(o["fulfilled_undated"] for o in cmp_)
    assert any(o["overdue_open"] for o in cmp_) and any(o["fill_rate_pct"] is None for o in cmp_)
