"""The guided upload layer: detection, questions, fixes and post-fix correctness.

Every messy file in ``guidance_cases.py`` is walked the way a user would walk
it: read, ask, answer, repeat, then materialize. The tests assert on what the
file BECOMES (the same canonical table its clean equivalent has), not on
status flags, and that clean files produce no questions and no fixes at all.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

from forecasting_core.data.guidance import (
    SHORT_HISTORY_PERIODS, analyze, check_mapping, classify_name, detect_header_row,
    materialize, score_sheet,
)
from forecasting_core.data.guidance_io import (
    GuidanceReadError, read_workbook, sniff_delimiter, workbook_from_grid,
)
from forecasting_core.data.guidance_parse import (
    analyze_date_column, analyze_number_column, read_date_cell, read_number_cell,
    serial_to_date,
)
from forecasting_core.data.loader import DataLoader

_spec = importlib.util.spec_from_file_location(
    "guidance_cases", pathlib.Path(__file__).with_name("guidance_cases.py"))
cases_mod = importlib.util.module_from_spec(_spec)
sys.modules["guidance_cases"] = cases_mod
_spec.loader.exec_module(cases_mod)
CASES = cases_mod.CASES


# ── the walk-through ─────────────────────────────────────────────────────────

def merge_decision(decisions: dict, new: dict) -> dict:
    out = copy.deepcopy(decisions)
    for k, v in new.items():
        if isinstance(v, dict):
            out.setdefault(k, {}).update(v)
        else:
            out[k] = v
    return out


def walk(book, answers: dict, max_steps: int = 12):
    """Answer every question from ``answers``; returns (report, decisions, asked)."""
    decisions, asked = {}, []
    for _ in range(max_steps):
        rep = analyze(book, decisions, today=dt.date(2025, 9, 1))
        q = rep["next_question"]
        if not q:
            return rep, decisions, asked
        asked.append(q["code"])
        option_id = answers.get(q["code"])
        assert option_id is not None, f"unexpected question {q['code']}: {q['params']}"
        option = next(o for o in q["options"] if o["id"] == option_id)
        decisions = merge_decision(decisions, option["decision"])
    raise AssertionError("conversation did not converge")


def canonical(df: pd.DataFrame, mapping: dict) -> list[tuple]:
    out = pd.DataFrame({
        "sku": df[mapping["sku"]].astype(str).str.strip(),
        "date": pd.to_datetime(df[mapping["date"]]).dt.strftime("%Y-%m-%d"),
        "demand": pd.to_numeric(df[mapping["demand"]], errors="coerce").astype(float),
    })
    out = out.dropna()
    return sorted(map(tuple, out.itertuples(index=False, name=None)))


def expected(rows: list) -> list[tuple]:
    return sorted((str(s), str(d), float(v)) for s, d, v in rows)


TODAY = dt.date(2025, 9, 1)


class Walked:
    """One case, built and walked once, shared by every property test."""

    def __init__(self, case, path):
        self.case, self.path = case, path
        self.book = read_workbook(str(path))
        self.first = analyze(self.book, today=TODAY)
        self.report, self.decisions, self.asked = walk(self.book, case.answers)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    cache: dict = {}

    def get(case):
        if case.id not in cache:
            cache[case.id] = Walked(case, case.build(tmp_path_factory.mktemp(case.id)))
        return cache[case.id]
    return get


@pytest.fixture(params=CASES, ids=[c.id for c in CASES])
def w(request, world):
    return world(request.param)


# ── one test per property, over every file ───────────────────────────────────

def test_first_report_names_what_the_file_has(w):
    case = w.case
    codes = {f["code"] for f in w.first["findings"]}
    missing = [c for c in case.findings if c not in codes]
    assert not missing, f"{case.id}: expected findings {missing}, got {sorted(codes)}"
    unexpected = [c for c in case.absent if c in codes]
    assert not unexpected, f"{case.id}: unexpected findings {unexpected}"


def test_questions_are_asked_one_at_a_time_in_order(w):
    case = w.case
    if case.verdict == "unusable":
        assert w.first["next_question"] is None, "an unusable file must not ask questions"
        return
    assert w.asked == case.asks, f"{case.id}: asked {w.asked}, expected {case.asks}"
    if w.first["verdict"] == "ask":
        q = w.first["next_question"]
        assert q is w.first["questions"][0] and q["severity"] == "ask" and q["options"]
    else:
        assert case.asks == []


def test_final_verdict_and_mapping(w):
    case, rep = w.case, w.report
    assert rep["verdict"] == case.verdict, (case.id, [f["code"] for f in rep["findings"]])
    if case.verdict == "ready":
        for slot, col in case.mapping.items():
            assert rep["mapping"].get(slot) == col, (case.id, slot, rep["mapping"])
        assert rep["checks_done"] == rep["checks_total"]


def test_fixes_recorded(w):
    case, rep = w.case, w.report
    if case.verdict != "ready":
        return
    got = {f["code"] for f in rep["fixes"]}
    if case.zero_fixes:
        assert not rep["fixes"], f"{case.id}: a clean file got fixes {sorted(got)}"
        assert rep["needs_apply"] is False
    elif case.fixes:
        assert rep["needs_apply"] is True
    for code in case.fixes:
        assert code in got, f"{case.id}: expected fix {code}, got {sorted(got)}"


def test_post_fix_table_equals_clean_equivalent(w):
    case, rep = w.case, w.report
    if case.verdict != "ready" or case.clean is None:
        return
    if rep["fixes"]:
        df, stats = materialize(w.book, rep["fixes"])
        assert stats["rows_out"] <= stats["rows_in"]
    else:
        # A file the guide leaves alone must be one the engine already reads right.
        df = DataLoader().load(str(w.path))
    assert canonical(df, rep["mapping"]) == expected(case.clean), case.id


def test_materialize_is_deterministic_and_never_mutates_the_source(w):
    case, rep = w.case, w.report
    if case.verdict != "ready" or not rep["fixes"]:
        return
    before = copy.deepcopy(w.book.sheets)
    a, _ = materialize(w.book, rep["fixes"])
    b, _ = materialize(w.book, rep["fixes"])
    pd.testing.assert_frame_equal(a, b)
    assert w.book.sheets == before
    assert w.path.exists()


def test_clean_files_ask_nothing(w):
    if not w.case.zero_fixes:
        return
    rep = w.first
    assert rep["verdict"] == "ready" and rep["questions"] == [] and rep["fixes"] == []
    assert all(f["severity"] == "info" for f in rep["findings"]), \
        [(f["code"], f["severity"]) for f in rep["findings"]]


def test_report_shape_is_complete(w):
    rep = w.first
    for key in ("version", "findings", "fixes", "questions", "verdict", "checks",
                "checks_done", "checks_total", "mapping", "needs_apply"):
        assert key in rep
    assert rep["checks_total"] == 6
    for f in rep["findings"]:
        assert set(f) >= {"code", "severity", "check", "confidence", "params"}
        assert f["severity"] in ("info", "auto", "ask", "block")
        if f["severity"] == "ask":
            assert f["options"] and all("decision" in o for o in f["options"])


# ── measured detection rate over the whole catalogue ─────────────────────────

def test_detection_rate_over_the_catalogue(world):
    """Every expected finding, question and fix, counted across all files."""
    total = hit = 0
    misses = []
    for c in CASES:
        w = world(c)
        codes = {f["code"] for f in w.first["findings"]} | {f["code"] for f in w.report["findings"]}
        fixes = {f["code"] for f in w.report["fixes"]}
        for want, have in ([(x, codes) for x in c.findings] + [(x, set(w.asked)) for x in c.asks]
                           + [(x, fixes) for x in c.fixes]):
            total += 1
            if want in have:
                hit += 1
            else:
                misses.append((c.id, want))
    print(f"\nguidance detection: {hit}/{total} expected findings, questions and fixes")
    assert total >= 80
    assert not misses, f"{hit}/{total} detected; missed {misses}"


def test_there_are_at_least_25_messy_fixtures():
    assert len(CASES) >= 25
    assert len([c for c in CASES if not c.zero_fixes]) >= 25


# ── cell-level rules ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_date", [
    ("2025-01-14", dt.date(2025, 1, 14)),
    ("2025/01/14", dt.date(2025, 1, 14)),
    ("14/01/2025", dt.date(2025, 1, 14)),
    ("01/14/2025", dt.date(2025, 1, 14)),
    ("14-01-25", dt.date(2025, 1, 14)),
    ("14 ene 2025", dt.date(2025, 1, 14)),
    ("14 de enero de 2025", dt.date(2025, 1, 14)),
    ("14-Jan-25", dt.date(2025, 1, 14)),
    ("Jan 14, 2025", dt.date(2025, 1, 14)),
    ("14.01.2025", dt.date(2025, 1, 14)),
    ("2025-01-14T10:30:00Z", dt.date(2025, 1, 14)),
    ("2025-01-14 10:30:00+02:00", dt.date(2025, 1, 14)),
    ("ene-25", dt.date(2025, 1, 1)),
    ("enero 2025", dt.date(2025, 1, 1)),
    ("2025-03", dt.date(2025, 3, 1)),
    ("03/2025", dt.date(2025, 3, 1)),
    ("05/05/2025", dt.date(2025, 5, 5)),
])
def test_date_cells_read_unambiguously(text, expected_date):
    d, flags = read_date_cell(text)
    assert d == expected_date, (text, flags)
    assert "ambiguous" not in flags


def test_ambiguous_date_is_not_guessed():
    d, flags = read_date_cell("01/02/2025")
    assert d is None and "ambiguous" in flags
    assert read_date_cell("01/02/2025", "day_first")[0] == dt.date(2025, 2, 1)
    assert read_date_cell("01/02/2025", "month_first")[0] == dt.date(2025, 1, 2)


def test_impossible_dates_are_unreadable_not_ambiguous():
    for bad in ("31/02/2025", "2025-13-01", "99/99/2025", "hola", "N/A"):
        d, flags = read_date_cell(bad)
        assert d is None and "ambiguous" not in flags, bad


def test_two_digit_year_pivot():
    assert read_date_cell("14/01/69")[0].year == 2069
    assert read_date_cell("14/01/70")[0].year == 1970


def test_excel_serial_only_when_asked():
    assert read_date_cell(45663)[0] is None
    assert read_date_cell(45663, serial_ok=True)[0] == serial_to_date(45663)
    assert read_date_cell(45663, serial_ok=True)[0] == dt.date(2025, 1, 6)
    assert read_date_cell(12, serial_ok=True)[0] is None


def test_column_with_proof_resolves_the_whole_column():
    col = ["01/02/2025", "13/02/2025", "03/04/2025"]
    r = analyze_date_column(col)
    assert r["order"] == "day_first" and r["order_source"] == "data"
    assert r["dates"][0] == dt.date(2025, 2, 1)
    assert not r["unresolved"]
    r = analyze_date_column(["01/02/2025", "02/13/2025"])
    assert r["order"] == "month_first"


def test_column_with_both_proofs_is_a_conflict():
    r = analyze_date_column(["13/02/2025", "02/13/2025", "01/03/2025"])
    assert r["order_conflict"] is True


def test_column_without_proof_is_unresolved_with_both_readings():
    r = analyze_date_column(["01/02/2025", "03/04/2025"])
    assert r["unresolved"] and r["order"] is None
    ex = r["ambiguous_examples"][0]
    assert ex["day_first"] == "2025-02-01" and ex["month_first"] == "2025-01-02"


def test_sequence_can_settle_the_order():
    cells = [f"{d:02d}/{m:02d}/2025" for m in range(1, 7) for d in range(1, 5)]
    r = analyze_date_column(cells)
    assert r["order"] == "day_first" and r["order_source"] == "sequence"
    flipped = [f"{m:02d}/{d:02d}/2025" for m in range(1, 7) for d in range(1, 5)]
    r = analyze_date_column(flipped)
    assert r["order"] == "month_first" and r["order_source"] == "sequence"


def test_future_dates_counted():
    r = analyze_date_column(["2025-01-01", "2031-01-01"], today=dt.date(2025, 9, 1))
    assert r["future"] == 1


@pytest.mark.parametrize("text,value", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("1,234,567", 1234567.0),
    ("1.234.567", 1234567.0), ("12,5", 12.5), ("12.5", 12.5), ("0,123", 0.123),
    ("$1,250.00", 1250.0), ("S/ 1 250,50", 1250.5), ("€ 12,50", 12.5), ("(12)", -12.0),
    ("12-", -12.0), ("-7", -7.0), ("45%", 45.0), ("1 234", 1234.0), ("  8 ", 8.0),
])
def test_number_cells(text, value):
    v, flags = read_number_cell(text)
    assert v == pytest.approx(value), (text, flags)
    assert "ambiguous" not in flags


def test_ambiguous_number_is_not_guessed():
    for text in ("1,234", "1.234", "12,345"):
        v, flags = read_number_cell(text)
        assert v is None and "ambiguous" in flags, text
    assert read_number_cell("1,234", ",")[0] == pytest.approx(1.234)
    assert read_number_cell("1,234", ".")[0] == 1234.0
    assert read_number_cell("1.234", ",")[0] == 1234.0


def test_number_column_decimal_evidence():
    assert analyze_number_column(["1,234", "12,5"])["decimal"] == ","
    assert analyze_number_column(["1,234", "12.5"])["decimal"] == "."
    r = analyze_number_column(["1,234", "2,500"])
    assert r["unresolved"] and r["ambiguous_examples"][0]["decimal_dot"] == 1234.0
    assert analyze_number_column(["12,5", "12.5"])["format_conflict"] is True


def test_number_column_statistics():
    r = analyze_number_column(["5", "-2", "0", "7"])
    assert r["negative_share"] == pytest.approx(0.25)
    assert analyze_number_column(["0", "0", "0"])["constant"] is True
    assert analyze_number_column(["$5", "$6"])["currency_share"] == 1.0
    assert analyze_number_column(["5%", "6%"])["percent_share"] == 1.0
    assert analyze_number_column(["a", "b", "3"])["n_unparsed"] == 2


# ── structure ────────────────────────────────────────────────────────────────

def test_header_detection_skips_titles_and_blanks():
    grid = [["Reporte", None, None], [None] * 3, ["sku", "fecha", "cantidad"],
            ["A", "2025-01-06", "5"], ["B", "2025-01-13", "7"]]
    assert detect_header_row(grid) == 2
    assert detect_header_row([["sku", "fecha"], ["A", "2025-01-06"]]) == 0
    assert detect_header_row([["A", "2025-01-06", "5"], ["B", "2025-01-13", "7"]]) is None


def test_sheet_ranking_prefers_the_sales_sheet():
    sales = [["sku", "fecha", "cantidad"]] + [["A", f"2025-01-{d:02d}", d] for d in range(1, 20)]
    summary = [["Resumen"], ["Productos", 4], ["Semanas", 26]]
    assert score_sheet("Ventas", sales)["score"] > score_sheet("Resumen", summary)["score"]


def test_delimiter_sniffing_survives_a_title_line():
    text = "Ventas 2025\nsku;fecha;cantidad\nA;2025-01-06;5\nB;2025-01-13;7\n"
    assert sniff_delimiter(text) == ";"


def test_cp1252_csv_reads(tmp_path):
    p = tmp_path / "latin.csv"
    p.write_bytes("sku,fecha,cantidad\nCamión,2025-01-06,5\n".encode("cp1252"))
    wb = read_workbook(str(p))
    assert wb.sheets[""][1][0] == "Camión"


def test_unreadable_file_raises_a_named_error(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"not a zip")
    with pytest.raises(GuidanceReadError):
        read_workbook(str(p))


def test_name_hints():
    assert "sku_strong" in classify_name("Código")
    assert "units_strong" in classify_name("Cantidad vendida")
    assert "money" in classify_name("Importe total")
    assert "units_strong" in classify_name("cantidad_total") and "money" not in classify_name("cantidad_total")
    assert "rowid" in classify_name("N° línea") or "rowid" in classify_name("linea")
    assert "stock" in classify_name("Existencia")


# ── verdicts the owner asked for, spelled out ────────────────────────────────

def test_mapping_the_user_chose_is_checked_not_overridden():
    rows = [["sku", "fecha", "cantidad"]] + [["A", f"2025-01-{d:02d}", d] for d in range(1, 25)]
    book = workbook_from_grid(rows)
    rep = check_mapping(book, {"sku": "sku", "date": "cantidad", "demand": "fecha"})
    assert rep["mapping"]["date"] == "cantidad"
    assert rep["verdict"] == "unusable"
    slots = {c["slot"]: c["status"] for c in rep["columns"]}
    assert slots["date"] == "bad" and slots["demand"] == "bad"


def test_summary_in_plain_numbers():
    book = workbook_from_grid(cases_mod._std_rows(cases_mod._SHORT))
    rep = analyze(book)
    s = rep["summary"]
    assert s["products"] == 5 and s["granularity"] == "weekly"
    assert s["short_products"] == 2 and s["short_threshold"] == SHORT_HISTORY_PERIODS
    assert s["date_min"] == "2025-01-06"
    assert rep["checks_done"] == 6


def test_blocked_file_explains_which_check_failed():
    book = workbook_from_grid([["sku", "cantidad"]] + [["A", i] for i in range(1, 30)])
    rep = analyze(book)
    status = {c["id"]: c["status"] for c in rep["checks"]}
    assert rep["verdict"] == "unusable" and status["date"] == "block"
    assert rep["next_question"] is None


def test_clean_file_summary_and_checks():
    rep = analyze(workbook_from_grid(cases_mod._std_rows()))
    assert rep["verdict"] == "ready" and rep["checks_done"] == 6
    assert rep["preview"]["slots"] == {"sku": "sku", "date": "fecha", "demand": "cantidad"}
    first = rep["preview"]["rows"][0]
    assert first["read"] == {"sku": "SKU-001", "date": "2025-01-06", "demand": 31.0} or \
        first["read"]["date"] == "2025-01-06"


# ── the repository's own sample files must not be second-guessed ─────────────

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SAMPLES = sorted((_REPO / "test_csvs").glob("*.csv")) + [_REPO / "demo_ventas.csv"]


@pytest.mark.skipif(not _SAMPLES[0].exists(), reason="sample files not present")
@pytest.mark.parametrize("path", _SAMPLES, ids=[p.name for p in _SAMPLES])
def test_existing_sample_files_never_crash_and_defer_to_the_gate(path):
    rep = analyze(read_workbook(str(path)), today=dt.date(2025, 9, 1))
    # The data gate already owns negatives, duplicates, outliers, nulls, gaps,
    # short history and future dates: the guide must not turn those into questions.
    assert {q["code"] for q in rep["questions"]} <= {"date_order_ambiguous"}, path.name
    assert rep["fixes"] == [] or path.name == "11_bad_text_in_numeric.csv", path.name


def test_good_sample_and_demo_file_are_ready():
    for name in ("test_csvs/01_good_daily_multi_sku.csv", "demo_ventas.csv"):
        rep = analyze(read_workbook(str(_REPO / name)))
        assert rep["verdict"] == "ready" and rep["questions"] == [] and rep["fixes"] == [], name


# ── product codes keep their zeros all the way through the loader ────────────

def test_loader_keeps_leading_zeros_of_code_columns_in_csv(tmp_path):
    p = tmp_path / "z.csv"
    p.write_text("sku,fecha,cantidad\n00123,2025-01-06,5\n00456,2025-01-06,7\n", encoding="utf-8")
    df = DataLoader().load(str(p))
    assert list(df["sku"]) == ["00123", "00456"]
    assert pd.api.types.is_integer_dtype(df["cantidad"])


def test_loader_keeps_leading_zeros_of_code_columns_in_xlsx(tmp_path):
    p = cases_mod.write_xlsx(tmp_path / "z.xlsx", {"S": [["sku", "fecha", "cantidad"],
                                                         ["00123", "2025-01-06", 5],
                                                         ["00456", "2025-01-06", 7]]})
    df = DataLoader().load(str(p))
    assert list(df["sku"]) == ["00123", "00456"]


def test_loader_leaves_ordinary_numeric_codes_numeric(tmp_path):
    p = tmp_path / "n.csv"
    p.write_text("sku,fecha,cantidad\n123,2025-01-06,5\n456,2025-01-06,7\n", encoding="utf-8")
    df = DataLoader().load(str(p))
    assert pd.api.types.is_integer_dtype(df["sku"])


def test_cleaned_file_written_as_xlsx_survives_the_loader(tmp_path):
    """The exact path the backend takes: materialize -> .xlsx -> engine loader."""
    case = next(c for c in CASES if c.id == "sku_leading_zeros_in_csv")
    book = read_workbook(str(case.build(tmp_path)))
    rep = analyze(book)
    df, _ = materialize(book, rep["fixes"])
    out = tmp_path / "clean.xlsx"
    df.to_excel(out, index=False, engine="openpyxl")
    back = DataLoader().load(str(out))
    assert canonical(back, rep["mapping"]) == expected(case.clean)
