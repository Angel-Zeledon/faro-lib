"""The pre-training gate: what is wrong with this file, and what can be done about it.

The product rule this implements, in the owner's words: *the standard we state is
met, or there is no forecast and we say why*. A soft warning under a button that
reads "this looks good" is not a standard — it is a suggestion, and the file that
warned "there is nothing to forecast" still trained.

Every problem lands in exactly one of three outcomes:

``blocking_fatal``
    No forecast is possible and no fix we can apply changes that. The run must
    not start. There are no options, because offering one would be a lie.

``blocking_fixable``
    The data would produce a WRONG forecast — plausible, unflagged and wrong —
    but a fix exists. The run must not start until the user picks one. Each
    option states what it does AND what it costs, because the cost is the whole
    point: "fill the gaps with zero" and "interpolate the gaps" are not two
    flavours of the same button, they are two different claims about what
    happened, and only the user knows which is true.

``advisory``
    Genuinely normal distributor data. Reported, never gated. Over-blocking is
    the failure mode that makes the product unusable — an SMB does not sell
    every SKU every day, and intermittent demand is the norm, not a defect.

A fourth shape exists and is deliberately NOT a gate: something we can detect,
cannot fix, and must still say out loud (see ``censored_demand_no_inventory``).
It carries ``remediable: False`` so the UI can render a statement rather than a
choice.

Contract notes
--------------
* Everything here is DATA: stable English codes plus params. The Spanish comes
  from the frontend catalogue, per CLAUDE.md.
* Issues ride the existing ``data_quality["issues"]`` channel — same list the
  mapping screen already renders. ``type`` / ``severity`` / ``blocking`` keep
  their old meaning; ``classification``, ``params`` and ``remediations`` are
  added.
* ``OPTION_EFFECTS`` names, for every option offered, the prep step that
  actually applies it. An option with no implementation is worse than no
  option, so ``validate_catalogue()`` refuses to let one exist.
"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd

# ── Outcome classes ────────────────────────────────────────────────────────

BLOCKING_FATAL = "blocking_fatal"
BLOCKING_FIXABLE = "blocking_fixable"
ADVISORY = "advisory"

# Periods a single SKU needs before the engine will train anything on it.
# Mirrors `min_history` (ValidationConfigRequest / sessions/defaults.py), the
# threshold DataQualityChecker.filter_valid_skus applies to drop a series.
MIN_TRAINABLE_PERIODS = 20

# A date before this is a typo, not history. Faro forecasts consumer goods for
# SMB distributors; nobody's ERP holds real 1900 sales.
MIN_PLAUSIBLE_YEAR = 2000
# Beyond this many days ahead a date is a typo, not a pre-order.
MAX_PLAUSIBLE_FUTURE_DAYS = 365

# A contiguous stretch with NO row for ANY series this long is a missing export,
# not a quiet month. Below it, absence is ordinary retail sparsity.
DATASET_WIDE_GAP_DAYS = 30

# Excel stores dates as days since 1899-12-30. This window is 1954-2064 — wide
# enough for any real history, narrow enough that a quantity column of 3-digit
# unit counts cannot be mistaken for it.
EXCEL_SERIAL_MIN = 20_000
EXCEL_SERIAL_MAX = 60_000
EXCEL_EPOCH = "1899-12-30"


# ── Option catalogue ───────────────────────────────────────────────────────
#
# `prep` names the step in backend/workers/runner.py that applies the option.
# `issue_type` ties the option back to the finding it answers, so a stored
# choice can be validated against the issue it claims to resolve.

OPTION_EFFECTS: dict = {
    # 1 — ambiguous date format
    "date_format_day_first":        {"issue_type": "ambiguous_date_format",  "prep": "date_format"},
    "date_format_month_first":      {"issue_type": "ambiguous_date_format",  "prep": "date_format"},
    # 2 — ambiguous thousands separator
    "separator_comma_is_thousands": {"issue_type": "ambiguous_number_format", "prep": "number_format"},
    "separator_comma_is_decimal":   {"issue_type": "ambiguous_number_format", "prep": "number_format"},
    # 3 — cumulative demand
    "cumulative_to_periodic":       {"issue_type": "cumulative_demand",      "prep": "cumulative"},
    "cumulative_keep":              {"issue_type": "cumulative_demand",      "prep": "no_change"},
    # 4 — one row per sale vs per day
    "duplicates_sum":               {"issue_type": "duplicates",             "prep": "duplicates"},
    "duplicates_keep_last":         {"issue_type": "duplicates",             "prep": "duplicates"},
    # 5 — negative demand
    "negatives_net_into_period":    {"issue_type": "negative_target",        "prep": "negatives"},
    "negatives_as_zero":            {"issue_type": "negative_target",        "prep": "negatives"},
    "negatives_drop_rows":          {"issue_type": "negative_target",        "prep": "negatives"},
    # 6 — impossible dates
    "out_of_range_dates_drop":      {"issue_type": "out_of_range_dates",     "prep": "out_of_range_dates"},
    "out_of_range_dates_keep":      {"issue_type": "out_of_range_dates",     "prep": "no_change"},
    # 7 — calendar gaps
    "gaps_fill_zero":               {"issue_type": "dataset_wide_gap",       "prep": "gap_fill"},
    "gaps_forward_fill":            {"issue_type": "dataset_wide_gap",       "prep": "gap_fill"},
    "gaps_interpolate":             {"issue_type": "dataset_wide_gap",       "prep": "gap_fill"},
    "gaps_leave":                   {"issue_type": "dataset_wide_gap",       "prep": "gap_fill"},
    # 8 — non-numeric values in a minority of rows
    "non_numeric_strip_symbols":    {"issue_type": "non_numeric_target",     "prep": "non_numeric"},
    "non_numeric_as_zero":          {"issue_type": "non_numeric_target",     "prep": "non_numeric"},
    "non_numeric_drop_rows":        {"issue_type": "non_numeric_target",     "prep": "non_numeric"},
    # empty quantities (distinct from unparseable text: nothing to strip)
    "null_target_as_zero":          {"issue_type": "null_target",            "prep": "null_target"},
    "null_target_drop_rows":        {"issue_type": "null_target",            "prep": "null_target"},
    # 9 — extreme outliers (advisory, but the options carry their cost)
    "outliers_leave":               {"issue_type": "outliers",               "prep": "outliers"},
    "outliers_clip_iqr":            {"issue_type": "outliers",               "prep": "outliers"},
    "outliers_winsorize":           {"issue_type": "outliers",               "prep": "outliers"},
    # 10 — Excel serial dates
    "excel_serial_as_date":         {"issue_type": "excel_serial_dates",     "prep": "excel_serial"},
    "excel_serial_as_number":       {"issue_type": "excel_serial_dates",     "prep": "no_change"},
    # 11 — inconsistent SKU identity
    "sku_identity_unify":           {"issue_type": "inconsistent_sku_identity", "prep": "sku_identity"},
    "sku_identity_keep_separate":   {"issue_type": "inconsistent_sku_identity", "prep": "no_change"},
    # 12 — quantity column that is money
    "target_is_units":              {"issue_type": "target_looks_like_money", "prep": "confirm_only"},
    "target_is_money_remap":        {"issue_type": "target_looks_like_money", "prep": "remap_required"},
}

# Every prep key above must be honoured by the runner, EXCEPT the three that
# are deliberately not transformations — and they are named rather than
# inferred, so "this option does nothing on purpose" can never be confused with
# "somebody forgot to wire it up":
#   no_change      the user chose to leave the data as it is, having been told
#                  what that costs. Recording the decision IS the effect.
#   confirm_only   the user affirmed our reading of a column was right.
#   remap_required the gate stays closed until the session maps a different
#                  column, which the gate enforces by re-detecting.
NON_TRANSFORMING_PREPS = frozenset({"no_change", "confirm_only", "remap_required"})
PREP_STEPS = frozenset(v["prep"] for v in OPTION_EFFECTS.values())

# Issue types that can never be argued with.
FATAL_TYPES = frozenset({
    "empty_dataset",
    "insufficient_columns",
    "no_date_column",
    "no_numeric_column",
    "single_date_value",
    "identifier_not_product",
    "no_trainable_history",
    "all_zeros",
})


def _opt(code: str, action: str, consequence: str,
         params: Optional[dict] = None, recommended: bool = False) -> dict:
    if code not in OPTION_EFFECTS:
        raise KeyError(f"remediation option {code!r} has no entry in OPTION_EFFECTS")
    return {
        "code": code,
        "action": action,
        "consequence": consequence,
        "params": params or {},
        "recommended": recommended,
        "applied_by": OPTION_EFFECTS[code]["prep"],
    }


def validate_catalogue() -> None:
    """Every option answers an issue and names the step that applies it.

    Called by the test suite. The rule it protects is the one the owner stated:
    an option that does nothing is worse than no option.
    """
    for code, meta in OPTION_EFFECTS.items():
        if not meta.get("issue_type") or not meta.get("prep"):
            raise ValueError(f"{code}: incomplete OPTION_EFFECTS entry")


# ── Issue construction ─────────────────────────────────────────────────────

def _issue(type_: str, severity: str, message: str, classification: str,
           params: Optional[dict] = None,
           remediations: Optional[list] = None,
           remediable: bool = True,
           **legacy) -> dict:
    """Build one issue in the shape the mapping screen already consumes.

    `blocking` stays a plain bool because consumers already read it and must not
    have to infer a dead end from `severity` — `all_zeros` was "error" and still
    overrulable before this gate existed.
    """
    issue = {
        "type": type_,
        "severity": severity,
        "message": message,
        "classification": classification,
        "blocking": classification in (BLOCKING_FATAL, BLOCKING_FIXABLE),
        "remediable": remediable,
        "params": params or {},
        "remediations": remediations or [],
    }
    issue.update(legacy)
    return issue


# ── Detectors ──────────────────────────────────────────────────────────────
#
# Each returns a list of issues. They are deliberately independent: a file can
# be wrong in several ways at once and the user deserves to see all of them
# before deciding, not one per round-trip.

_DATE_PARTS = re.compile(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$")


def detect_ambiguous_date_format(df: pd.DataFrame, date_col: Optional[str]) -> list:
    """`03/04/2026` is the 3rd of April or the 4th of March, and both parse.

    The most expensive defect on this list, because it is the quietest: reading
    day-first data as month-first shuffles every observation into the wrong
    week. Weekly seasonality — the single strongest signal in distributor
    demand — is destroyed, the model still trains, the metrics still look
    ordinary, and nothing anywhere says the calendar was wrong.

    Ambiguous means exactly: every row's first AND second component is <= 12, so
    neither reading can be ruled out, AND at least one row has the two
    components different (11/11/2026 is the same date either way).
    """
    if not date_col or date_col not in df.columns:
        return []
    col = df[date_col]
    if pd.api.types.is_datetime64_any_dtype(col) or pd.api.types.is_numeric_dtype(col):
        return []   # already a real date, or a serial number (own check)

    text = col.dropna().astype(str)
    if text.empty:
        return []

    parts = text.str.extract(_DATE_PARTS)
    matched = parts.dropna()
    # Require the whole column to use this shape; a mixed column has a different
    # problem (unparseable rows) that `invalid_dates` already reports.
    if len(matched) < len(text):
        return []
    if matched.empty:
        return []

    first = pd.to_numeric(matched[0], errors="coerce")
    second = pd.to_numeric(matched[1], errors="coerce")
    if first.isna().any() or second.isna().any():
        return []
    if (first > 12).any() or (second > 12).any():
        return []   # one reading is impossible — the format is determined
    if not (first != second).any():
        return []   # every row is a palindrome; the choice cannot change a date

    n_ambiguous = int((first != second).sum())
    sample = matched.apply(lambda r: f"{r[0]}/{r[1]}/{r[2]}", axis=1).head(3).tolist()
    return [_issue(
        "ambiguous_date_format", "error",
        f"The date column '{date_col}' is written as {sample[0] if sample else 'dd/mm/yyyy'} "
        f"and both readings are possible: {n_ambiguous} rows change date depending on "
        f"whether the first number is the day or the month.",
        BLOCKING_FIXABLE,
        params={"column": date_col, "n_ambiguous": n_ambiguous, "examples": sample},
        remediations=[
            _opt("date_format_day_first",
                 "Read the first number as the day (dd/mm/yyyy) — the Latin American convention.",
                 f"If the file was actually exported month-first, {n_ambiguous} rows move to a "
                 f"different month and the weekly pattern the models rely on is destroyed. "
                 f"Nothing will look broken afterwards.",
                 params={"n_rows": n_ambiguous}, recommended=True),
            _opt("date_format_month_first",
                 "Read the first number as the month (mm/dd/yyyy) — the US convention.",
                 f"If the file was actually exported day-first, {n_ambiguous} rows move to a "
                 f"different month and the weekly pattern is destroyed. "
                 f"Nothing will look broken afterwards.",
                 params={"n_rows": n_ambiguous}),
        ],
    )]


_AMBIGUOUS_THOUSANDS = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")   # 1,234
_EUROPEAN_DECIMAL = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$")  # 1.234,56
_SIMPLE_DECIMAL = re.compile(r"^-?\d+,\d+$")                    # 10,5


def detect_ambiguous_number_format(df: pd.DataFrame, target_col: Optional[str]) -> list:
    """`1,234` is 1234 in English and 1.234 in Spanish — a factor of a thousand.

    `_coerce_decimal_comma` in the runner already refuses to guess here, which
    was the right call and the wrong ending: it logged a line nobody reads and
    left the column as text, so the run died deep inside training with
    `could not convert string to float`. Refusing to guess is only honest if it
    ASKS.
    """
    if not target_col or target_col not in df.columns:
        return []
    col = df[target_col]
    if pd.api.types.is_numeric_dtype(col):
        return []
    text = col.dropna().astype(str).str.strip()
    if text.empty:
        return []
    hits = text[text.str.match(_AMBIGUOUS_THOUSANDS)]
    if hits.empty:
        return []
    # A column that ALSO contains an unambiguous European decimal (1.234,56)
    # has already declared its convention — no question to ask.
    if text.str.match(_EUROPEAN_DECIMAL).any():
        return []

    sample = hits.head(3).tolist()
    n = int(len(hits))
    example = sample[0] if sample else "1,234"
    plain = example.replace(",", "")
    spanish = example.replace(",", ".")
    return [_issue(
        "ambiguous_number_format", "error",
        f"{n} quantities in '{target_col}' are written like '{example}', where the comma "
        f"is either a thousands separator ({plain}) or a decimal point ({spanish}).",
        BLOCKING_FIXABLE,
        params={"column": target_col, "n_rows": n, "examples": sample,
                "as_thousands": plain, "as_decimal": spanish},
        remediations=[
            _opt("separator_comma_is_thousands",
                 f"Read the comma as a thousands separator: '{example}' means {plain}.",
                 f"If it was really a decimal point, every one of those {n} quantities is "
                 f"multiplied by a thousand and the purchase orders come out a thousand times "
                 f"too large.",
                 params={"n_rows": n, "example": example, "result": plain}),
            _opt("separator_comma_is_decimal",
                 f"Read the comma as a decimal point: '{example}' means {spanish}.",
                 f"If it was really a thousands separator, every one of those {n} quantities is "
                 f"divided by a thousand and you will under-order by the same factor.",
                 params={"n_rows": n, "example": example, "result": spanish}),
        ],
    )]


def detect_cumulative_demand(df: pd.DataFrame, date_col: Optional[str],
                             target_col: Optional[str],
                             group_col: Optional[str]) -> list:
    """A month-to-date column trained as demand forecasts a number that only grows.

    Plenty of ERPs export a running total. Read as per-period demand it produces
    a series with a perfect upward trend, an excellent-looking fit, and a
    forecast that says next month you will sell more than you have ever sold in
    your life — every month, forever.

    Detected as: within a series, every step is non-decreasing, the series is
    not flat, and it has enough points that this is not a coincidence.
    """
    if not target_col or target_col not in df.columns or not date_col:
        return []
    if date_col not in df.columns:
        return []
    try:
        work = df[[c for c in {date_col, target_col, group_col} if c]].copy()
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
        work = work.dropna(subset=[date_col, target_col])
    except Exception:
        return []
    if work.empty:
        return []

    groups = ([(None, work)] if not group_col or group_col not in work.columns
              else list(work.groupby(group_col, sort=False)))

    monotone, considered = [], 0
    for key, sub in groups:
        s = sub.sort_values(date_col)[target_col]
        if len(s) < 10:
            continue
        considered += 1
        if float(s.std()) <= 0:
            continue                       # flat, not cumulative
        if float(s.iloc[-1]) <= float(s.iloc[0]):
            continue                       # does not end higher than it started
        if bool((s.diff().dropna() >= 0).all()):
            monotone.append(str(key) if key is not None else "__all__")

    if not considered or not monotone:
        return []
    share = len(monotone) / considered
    # One monotone SKU in a catalogue is a product that only ever grew. Half of
    # them are an export format.
    if share < 0.5:
        return []

    return [_issue(
        "cumulative_demand", "error",
        f"{len(monotone)} of {considered} series never go down — '{target_col}' looks like a "
        f"running (month-to-date or year-to-date) total, not the quantity sold in each period.",
        BLOCKING_FIXABLE,
        params={"column": target_col, "n_series": len(monotone),
                "n_considered": considered, "examples": monotone[:5]},
        remediations=[
            _opt("cumulative_to_periodic",
                 "Convert to per-period demand by taking the difference between consecutive "
                 "periods of each series.",
                 "Each series loses its first observation (there is nothing to subtract from), "
                 "and any period where the running total went DOWN — a return, or a counter "
                 "reset at the start of a month — becomes 0.",
                 params={"n_series": len(monotone)}, recommended=True),
            _opt("cumulative_keep",
                 "Treat the values as the quantity sold in each period after all.",
                 "If they really are running totals the forecast grows without bound: every "
                 "period is predicted higher than the last, and the reorder quantities grow "
                 "with it until someone notices.",
                 params={"n_series": len(monotone)}),
        ],
    )]


def detect_out_of_range_dates(df: pd.DataFrame, date_col: Optional[str]) -> list:
    """A 1900 or 2099 row stretches the calendar the whole run is built on.

    Measured, not assumed: one 2099 row moves the forecast calendar into the
    24th century, and gap filling for that series is abandoned entirely because
    the span blows past the bucket cap — silently, in a log line.
    """
    if not date_col or date_col not in df.columns:
        return []
    try:
        dates = pd.to_datetime(df[date_col], errors="coerce")
    except Exception:
        return []
    valid = dates.dropna()
    if valid.empty:
        return []

    today = pd.Timestamp.now().normalize()
    lo = pd.Timestamp(year=MIN_PLAUSIBLE_YEAR, month=1, day=1)
    hi = today + pd.Timedelta(days=MAX_PLAUSIBLE_FUTURE_DAYS)
    bad = valid[(valid < lo) | (valid > hi)]
    if bad.empty:
        return []

    kept = valid[(valid >= lo) & (valid <= hi)]
    span_before = int((valid.max() - valid.min()).days)
    span_after = int((kept.max() - kept.min()).days) if len(kept) > 1 else 0
    n = int(len(bad))
    examples = sorted({str(d.date()) for d in bad})[:3]

    return [_issue(
        "out_of_range_dates", "error",
        f"{n} row{'s' if n != 1 else ''} carry a date outside {MIN_PLAUSIBLE_YEAR}–"
        f"{hi.year} (for example {', '.join(examples)}).",
        BLOCKING_FIXABLE,
        params={"column": date_col, "n_rows": n, "examples": examples,
                "span_days_before": span_before, "span_days_after": span_after},
        remediations=[
            _opt("out_of_range_dates_drop",
                 f"Remove those {n} rows.",
                 f"You lose {n} observation{'s' if n != 1 else ''} and the history span goes "
                 f"from {span_before} days to {span_after} days. A series left below "
                 f"{MIN_TRAINABLE_PERIODS} periods is skipped by training.",
                 params={"n_rows": n, "span_days_before": span_before,
                         "span_days_after": span_after},
                 recommended=True),
            _opt("out_of_range_dates_keep",
                 "Keep them and fix the file yourself later.",
                 "Gap filling is abandoned for every series they touch — the date range is too "
                 "wide to reindex — so those series train on irregular history, and the "
                 "forecast calendar starts after the impossible date.",
                 params={"n_rows": n}),
        ],
    )]


def detect_excel_serial_dates(df: pd.DataFrame, date_col: Optional[str],
                              exclude: Optional[set] = None) -> list:
    """`45000` in a date column is 2023-03-15 — or forty-five thousand of something.

    Only asked when no real date column was detected: if the file has a parseable
    date, a numeric column in this range is just a number.
    """
    if date_col:
        return []
    exclude = exclude or set()
    for col in df.columns:
        if col in exclude:
            continue
        s = df[col].dropna()
        if s.empty or not pd.api.types.is_numeric_dtype(s):
            continue
        if not bool(((s >= EXCEL_SERIAL_MIN) & (s <= EXCEL_SERIAL_MAX)).all()):
            continue
        if s.nunique() < 3:
            continue
        as_dates = pd.to_datetime(s, unit="D", origin=EXCEL_EPOCH, errors="coerce")
        if as_dates.isna().any():
            continue
        lo, hi = str(as_dates.min().date()), str(as_dates.max().date())
        n = int(len(s))
        return [_issue(
            "excel_serial_dates", "error",
            f"No date column could be read. Column '{col}' holds whole numbers between "
            f"{int(s.min())} and {int(s.max())}, which is how Excel stores dates — "
            f"{lo} to {hi}.",
            BLOCKING_FIXABLE,
            params={"column": col, "n_rows": n, "date_min": lo, "date_max": hi},
            remediations=[
                _opt("excel_serial_as_date",
                     f"Read '{col}' as dates: {lo} to {hi}.",
                     "If those numbers were a quantity, a code or a price, the whole file is "
                     "re-dated onto a calendar that has nothing to do with the sales.",
                     params={"column": col, "date_min": lo, "date_max": hi},
                     recommended=True),
                _opt("excel_serial_as_number",
                     f"Treat '{col}' as an ordinary number.",
                     "The file still has no date column, so there is nothing to forecast "
                     "against. You will have to add one and upload again.",
                     params={"column": col}),
            ],
        )]
    return []


def detect_inconsistent_sku_identity(df: pd.DataFrame, group_col: Optional[str]) -> list:
    """`SKU-1`, `sku-1` and `SKU-1 ` are one product spread across three series.

    Each fragment gets a fraction of the history, several fall under the
    trainable minimum and are dropped, and the stock the buyer entered for the
    real code matches whichever fragment happens to win. Nothing errors.
    """
    if not group_col or group_col not in df.columns:
        return []
    values = df[group_col].dropna().astype(str)
    if values.empty:
        return []
    normalized = values.str.strip().str.casefold().str.replace(r"\s+", " ", regex=True)

    pairs = pd.DataFrame({"raw": values, "norm": normalized}).drop_duplicates()
    collisions = pairs.groupby("norm")["raw"].nunique()
    colliding = collisions[collisions > 1]
    if colliding.empty:
        return []

    n_groups = int(len(colliding))
    n_variants = int(pairs[pairs["norm"].isin(colliding.index)].shape[0])
    examples = [
        sorted(pairs.loc[pairs["norm"] == key, "raw"].tolist())
        for key in colliding.index[:3]
    ]
    return [_issue(
        "inconsistent_sku_identity", "error",
        f"{n_variants} product codes differ only in capitalisation or spacing and describe "
        f"{n_groups} product{'s' if n_groups != 1 else ''} — for example {examples[0]}.",
        BLOCKING_FIXABLE,
        params={"column": group_col, "n_groups": n_groups,
                "n_variants": n_variants, "examples": examples},
        remediations=[
            _opt("sku_identity_unify",
                 "Treat the variants as the same product (trim spaces, ignore capitalisation).",
                 f"{n_variants} series become {n_groups}. If two of those codes really are "
                 f"different products, their sales are added together and both get one "
                 f"forecast — and one purchase order.",
                 params={"n_groups": n_groups, "n_variants": n_variants}, recommended=True),
            _opt("sku_identity_keep_separate",
                 "Keep every code exactly as written.",
                 f"The same product stays split across {n_variants} series with a fraction of "
                 f"the history each; any fragment under {MIN_TRAINABLE_PERIODS} periods is "
                 f"skipped, and the stock you enter matches only one of them.",
                 params={"n_variants": n_variants}),
        ],
    )]


def detect_target_looks_like_money(df: pd.DataFrame, target_col: Optional[str]) -> list:
    """A revenue column mapped as demand orders money, not units.

    Deliberately narrow. Distributors do sell by weight and volume, and a
    fractional quantity is not suspicious on its own — so this only fires on the
    signature of currency: almost every value has EXACTLY two decimal places and
    the typical magnitude is large.
    """
    if not target_col or target_col not in df.columns:
        return []
    try:
        vals = pd.to_numeric(df[target_col], errors="coerce").dropna()
    except Exception:
        return []
    if len(vals) < 20:
        return []
    if float(vals.median()) < 1000:
        return []

    text = vals.abs().map(lambda v: f"{v:.10f}".rstrip("0"))
    decimals = text.str.split(".").str[1].fillna("").str.len()
    two_dp = float((decimals == 2).mean())
    if two_dp < 0.9:
        return []

    return [_issue(
        "target_looks_like_money", "error",
        f"'{target_col}' reads like an amount of money, not a count of units: "
        f"{round(two_dp * 100)}% of the values have exactly two decimals and the typical "
        f"value is {round(float(vals.median()))}.",
        BLOCKING_FIXABLE,
        params={"column": target_col, "two_decimal_pct": round(two_dp * 100, 1),
                "median": round(float(vals.median()), 2)},
        remediations=[
            _opt("target_is_units",
                 f"Confirm that '{target_col}' really is the quantity sold.",
                 "Nothing changes. If it is revenue, every purchase suggestion is a quantity "
                 "of money — you would order thousands of units of a product you sell tens of.",
                 params={"column": target_col}),
            _opt("target_is_money_remap",
                 "It is money — go back and map the units column instead.",
                 "Training stays blocked until the demand column points somewhere else. "
                 "If the file has no unit column, this data cannot drive purchasing.",
                 params={"column": target_col}, recommended=True),
        ],
    )]


def detect_identifier_not_product(df: pd.DataFrame, group_col: Optional[str],
                                  n_rows: int) -> list:
    """A column with one value per row is an invoice number, not a product.

    Every "series" would have exactly one observation, so every series is
    dropped and the run ends in `no_models_trained` — after the wait.
    """
    if not group_col or group_col not in df.columns or n_rows < MIN_TRAINABLE_PERIODS:
        return []
    n_unique = int(df[group_col].nunique())
    if n_rows <= 0 or n_unique / n_rows < 0.95:
        return []
    return [_issue(
        "identifier_not_product", "error",
        f"Column '{group_col}' has {n_unique} different values across {n_rows} rows — "
        f"about one per row. That is an invoice or line number, not a product code: "
        f"every product would have a single sale and nothing can be forecast.",
        BLOCKING_FATAL,
        params={"column": group_col, "n_unique": n_unique, "n_rows": n_rows},
    )]


def detect_dataset_wide_gap(df: pd.DataFrame, date_col: Optional[str],
                            group_col: Optional[str] = None) -> list:
    """A month where the whole catalogue sold nothing is a missing export.

    This is the only gap the gate blocks on, and the restraint is deliberate.
    Per-SKU sparsity is what distributor data LOOKS like — a shop that sells one
    SKU three days a week has 57% of its daily buckets empty and is perfectly
    healthy. But a stretch where NO product in the file has a single row is not
    a quiet month: it is an export that lost a chunk, and filling it with zeros
    teaches every model in the catalogue that demand collapsed.

    One more condition, and it is the one that keeps a real catalogue out of the
    gate: at least one product must have sales on BOTH sides of the gap. A
    catalogue where some products were discontinued in April and others launched
    in June has a two-month hole in the file's date axis and nothing whatsoever
    wrong with it — no series crosses that hole, so no series lost anything.
    A lost export looks the opposite way round: the same products resume
    afterwards.
    """
    if not date_col or date_col not in df.columns:
        return []
    try:
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    except Exception:
        return []
    if len(dates) < 3:
        return []
    # Ignore implausible dates here — `out_of_range_dates` owns them, and a
    # single 1900 row would otherwise report a 125-year "gap".
    today = pd.Timestamp.now().normalize()
    lo = pd.Timestamp(year=MIN_PLAUSIBLE_YEAR, month=1, day=1)
    hi = today + pd.Timedelta(days=MAX_PLAUSIBLE_FUTURE_DAYS)
    dates = dates[(dates >= lo) & (dates <= hi)]
    if len(dates) < 3:
        return []

    unique = pd.Series(sorted(dates.unique()))
    diffs = unique.diff().dropna().dt.days
    if diffs.empty:
        return []
    worst = int(diffs.max())
    if worst < DATASET_WIDE_GAP_DAYS:
        return []
    # The native cadence: a monthly file legitimately has 30-day steps.
    cadence = int(diffs.median()) or 1
    if worst <= cadence * 2:
        return []

    idx = int(diffs.idxmax())
    gap_start_ts = unique.iloc[idx - 1]
    gap_end_ts = unique.iloc[idx]
    start = str(gap_start_ts.date())
    end = str(gap_end_ts.date())
    missing = worst - cadence

    # Does any product actually straddle the hole? If none does, the file simply
    # changed catalogue — nothing was lost and nothing must be gated.
    if group_col and group_col in df.columns:
        try:
            frame = pd.DataFrame({
                "g": df[group_col].astype(str),
                "d": pd.to_datetime(df[date_col], errors="coerce"),
            }).dropna()
            before = set(frame.loc[frame["d"] <= gap_start_ts, "g"])
            after = set(frame.loc[frame["d"] >= gap_end_ts, "g"])
            if not (before & after):
                return []
        except Exception:
            pass

    return [_issue(
        "dataset_wide_gap", "error",
        f"No product in the file has a single row between {start} and {end} — a gap of "
        f"{worst} days in data that otherwise moves every {cadence} day(s).",
        BLOCKING_FIXABLE,
        params={"gap_days": worst, "gap_start": start, "gap_end": end,
                "cadence_days": cadence, "missing_periods": missing},
        remediations=[
            _opt("gaps_fill_zero",
                 "Insert the missing periods with demand 0.",
                 f"You are asserting that the whole catalogue sold nothing for {worst} days. "
                 f"If the rows are merely missing, every model learns a collapse that never "
                 f"happened and the suggested orders come out low.",
                 params={"gap_days": worst}),
            _opt("gaps_interpolate",
                 "Insert the missing periods and draw a straight line between the observations "
                 "on either side.",
                 f"We invent {missing} periods of demand that was never observed. Validation "
                 f"error comes out lower than the model really achieves, because part of what "
                 f"it is scored against is our own straight line.",
                 params={"missing_periods": missing}),
            _opt("gaps_forward_fill",
                 "Insert the missing periods and repeat the last observed value.",
                 f"Whatever happened to sell on {start} is repeated {missing} times. If that "
                 f"was a promotion day, the promotion becomes a month of baseline demand.",
                 params={"missing_periods": missing, "gap_start": start}),
            _opt("gaps_leave",
                 "Leave the gap exactly as it is.",
                 f"The series stay irregular across the gap: a lag of 1 means yesterday on one "
                 f"side and {worst} days ago across it, so the model's idea of 'last period' is "
                 f"inconsistent for every product.",
                 params={"gap_days": worst}, recommended=True),
        ],
    )]


_CURRENCY_STRIP = re.compile(r"[^0-9,.\-]")


def detect_non_numeric_target(df: pd.DataFrame, target_col: Optional[str]) -> list:
    """`N/D`, `-`, `$10`, `10 kg` — text where a quantity belongs.

    Coerced to NaN and dropped further down without a word. Split from
    `null_target` (a genuinely empty cell) because the remediations differ:
    there is nothing to strip out of an empty cell.
    """
    if not target_col or target_col not in df.columns:
        return []
    col = df[target_col]
    if pd.api.types.is_numeric_dtype(col):
        return []
    text = col.dropna().astype(str).str.strip()
    text = text[text != ""]
    if text.empty:
        return []

    parsed = pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce")
    bad = text[parsed.isna()]
    if bad.empty:
        return []
    n_bad = int(len(bad))
    if n_bad == len(text):
        return []   # nothing numeric at all — `no_numeric_column` owns that

    stripped = pd.to_numeric(
        bad.str.replace(_CURRENCY_STRIP, "", regex=True).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    n_recoverable = int(stripped.notna().sum())
    n_rows = int(len(df))
    pct = round(n_bad / max(n_rows, 1) * 100, 1)
    examples = sorted(set(bad.tolist()))[:4]

    # A handful of stray cells in a large file is ordinary and already handled
    # (they become NaN and the row is dropped). A real share of the file is a
    # different column than the user thinks they mapped.
    blocking = n_bad >= max(5, n_rows * 0.005)

    options = [
        _opt("non_numeric_strip_symbols",
             "Strip currency symbols, spaces and unit suffixes and read what is left as a number.",
             f"{n_recoverable} of the {n_bad} values become numbers "
             f"({', '.join(examples[:2])} and the like). The remaining "
             f"{n_bad - n_recoverable} stay unreadable and their rows are dropped.",
             params={"n_rows": n_bad, "n_recoverable": n_recoverable},
             recommended=n_recoverable > n_bad / 2),
        _opt("non_numeric_as_zero",
             "Read every unreadable value as 0.",
             f"You assert that {n_bad} periods sold nothing. If '{examples[0] if examples else 'N/D'}' "
             f"meant 'not reported' rather than 'none sold', demand is understated and the "
             f"orders come out low.",
             params={"n_rows": n_bad}),
        _opt("non_numeric_drop_rows",
             f"Remove the {n_bad} rows.",
             f"You lose {n_bad} observations ({pct}% of the file). Series already close to "
             f"{MIN_TRAINABLE_PERIODS} periods can fall under it and be skipped entirely.",
             params={"n_rows": n_bad, "pct": pct},
             recommended=n_recoverable <= n_bad / 2),
    ]
    return [_issue(
        "non_numeric_target", "error" if blocking else "warning",
        f"{n_bad} value{'s' if n_bad != 1 else ''} ({pct}%) in '{target_col}' are text, not "
        f"numbers — for example {', '.join(repr(e) for e in examples)}.",
        BLOCKING_FIXABLE if blocking else ADVISORY,
        params={"column": target_col, "n_rows": n_bad, "pct": pct,
                "examples": examples, "n_recoverable": n_recoverable},
        remediations=options,
    )]


def detect_censored_demand(canonical_mapping: Optional[dict]) -> list:
    """Detectable, unfixable, and it must still be said.

    Without stock history there is no way to tell "sold none" from "had none to
    sell". Every stockout is read as zero demand, the forecast is biased down,
    the reorder point follows it down, and the next stockout is more likely than
    the last. There is no remediation — only a column the user does not have —
    so this is a statement, not a gate. Saying nothing would leave the bias in
    place AND unmentioned, which is how it survived this long.
    """
    if canonical_mapping is None:
        # No mapping decided yet (the profile runs before the mapping screen).
        # Announcing a missing column the user has not had the chance to choose
        # yet is noise, and noise is what taught everyone to skip this panel.
        return []
    if canonical_mapping.get("inventory"):
        return []
    return [_issue(
        "censored_demand_no_inventory", "info",
        "No stock column is mapped, so a day with no sales cannot be told apart from a day "
        "with nothing on the shelf. Demand is measured low wherever the product was out of "
        "stock, and the forecast will run conservative.",
        ADVISORY,
        params={"missing_field": "inventory"},
        remediable=False,
    )]


def detect_structural_faults(df: pd.DataFrame, date_col: Optional[str],
                             target_col: Optional[str]) -> list:
    """The file cannot be a sales history at all. Nothing here has a fix.

    Runs before every other detector and short-circuits: a header-only file
    does not also need to be told its dates are ambiguous.
    """
    n_rows, n_cols = int(len(df)), int(len(df.columns))

    if n_rows == 0:
        return [_issue(
            "empty_dataset", "error",
            "The file has a header but no rows. There is nothing to forecast.",
            BLOCKING_FATAL, params={"n_rows": 0, "n_cols": n_cols}, remediable=False,
        )]

    if n_cols < 3:
        return [_issue(
            "insufficient_columns", "error",
            f"The file has {n_cols} column{'s' if n_cols != 1 else ''}. A sales history needs "
            f"at least three: when, which product, how many. With fewer, a forecast cannot be "
            f"tied back to a product, so it cannot drive a purchase order.",
            BLOCKING_FATAL,
            params={"n_cols": n_cols, "columns": [str(c) for c in df.columns]},
            remediable=False,
        )]

    faults = []
    # Below the trainable minimum the column detectors cannot honestly claim
    # anything: `_is_date` needs more than one distinct value before it will
    # call a column a date, so a three-row file "has no date column" as an
    # artifact of the detector, not as a fact about the file. That file is
    # already fatally short — `no_trainable_history` says so — and piling a
    # second, shakier verdict on top only makes the panel less believable.
    if n_rows < MIN_TRAINABLE_PERIODS:
        return faults

    if not target_col:
        faults.append(_issue(
            "no_numeric_column", "error",
            "No column in this file holds numbers that could be a quantity sold.",
            BLOCKING_FATAL,
            params={"columns": [str(c) for c in df.columns]}, remediable=False,
        ))

    if not date_col:
        # Only fatal when there is not even an Excel serial column to offer.
        if not detect_excel_serial_dates(df, None, exclude={target_col} if target_col else None):
            faults.append(_issue(
                "no_date_column", "error",
                "No column in this file can be read as a date, so there is no timeline to "
                "forecast along.",
                BLOCKING_FATAL,
                params={"columns": [str(c) for c in df.columns]}, remediable=False,
            ))
    else:
        try:
            parsed = pd.to_datetime(df[date_col], errors="coerce").dropna()
        except Exception:
            parsed = pd.Series([], dtype="datetime64[ns]")
        if len(parsed) and int(parsed.nunique()) == 1:
            faults.append(_issue(
                "single_date_value", "error",
                f"Every row carries the same date ({parsed.iloc[0].date()}). A single snapshot "
                f"is not a history — there is no change over time to learn from.",
                BLOCKING_FATAL,
                params={"column": date_col, "date": str(parsed.iloc[0].date())},
                remediable=False,
            ))
    return faults


def evaluate(df: pd.DataFrame, date_col: Optional[str], target_col: Optional[str],
             group_col: Optional[str], data_quality: Optional[dict] = None,
             canonical_mapping: Optional[dict] = None,
             series_col: Optional[str] = None) -> dict:
    """Run the whole gate over one file and return the enriched data_quality dict.

    `data_quality` carries whatever DataProfiler's own checks already found; the
    detectors below add what it could not see, and `classify` sorts the lot into
    the three outcomes.

    `group_col` is the PRODUCT column — what the user sees named in an issue.
    `series_col` is what actually identifies a time series, which on a
    multi-warehouse session is (sku, store) folded into one key. Detectors that
    reason about a timeline use `series_col`; detectors that talk ABOUT the
    product column use `group_col`.
    """
    data_quality = data_quality if data_quality is not None else {"issues": []}
    data_quality.setdefault("issues", [])
    n_rows = int(len(df))
    series_col = series_col or group_col

    structural = detect_structural_faults(df, date_col, target_col)
    if any(i["type"] in ("empty_dataset", "insufficient_columns") for i in structural):
        # Nothing else can be meaningfully said about this file.
        data_quality["issues"] = structural
        return classify(data_quality)

    enrich_existing_issues(data_quality["issues"], n_rows)

    found = list(structural)
    found += detect_identifier_not_product(df, group_col, n_rows)
    found += detect_ambiguous_date_format(df, date_col)
    found += detect_excel_serial_dates(df, date_col,
                                       exclude={target_col} if target_col else None)
    found += detect_out_of_range_dates(df, date_col)
    found += detect_ambiguous_number_format(df, target_col)
    found += detect_non_numeric_target(df, target_col)
    found += detect_inconsistent_sku_identity(df, group_col)
    found += detect_target_looks_like_money(df, target_col)
    found += detect_cumulative_demand(df, date_col, target_col, series_col)
    found += detect_dataset_wide_gap(df, date_col, series_col)
    found += detect_censored_demand(canonical_mapping)

    existing_types = {i.get("type") for i in data_quality["issues"]}
    data_quality["issues"].extend(i for i in found if i["type"] not in existing_types)

    # A fatal finding makes every fixable one moot: there is no run left to
    # protect, and asking a user to choose how to net their returns on a file
    # that can never train is a worse insult than the original soft warning.
    # Checked across BOTH sources — `all_zeros` reaches its fatal verdict via
    # FATAL_TYPES in classify(), not with a classification already stamped on.
    has_fatal = any(
        i.get("classification") == BLOCKING_FATAL or i.get("type") in FATAL_TYPES
        for i in data_quality["issues"]
    )
    if has_fatal:
        for i in data_quality["issues"]:
            if i.get("type") in FATAL_TYPES:
                continue
            if i.get("classification") == BLOCKING_FIXABLE:
                i["classification"] = ADVISORY
                i["blocking"] = False
                i["severity"] = "warning"

    return classify(data_quality)


# ── Enrichment of the checks that already existed ──────────────────────────
#
# `negative_target`, `duplicates`, `null_target` and `outliers` were already
# detected by DataProfiler._check_quality and already carried their counts —
# what they never carried was a decision. These builders attach it, using the
# numbers the check itself measured, so there is one detector per problem.

def enrich_negative_target(issue: dict, n_rows: int) -> dict:
    n = int(issue.get("neg_count", 0))
    pct = issue.get("neg_pct", 0)
    issue["classification"] = BLOCKING_FIXABLE
    issue["params"] = {"n_rows": n, "pct": pct}
    issue["remediations"] = [
        _opt("negatives_net_into_period",
             "Subtract them from the sales of the same day and product — treat them as returns.",
             f"That day's net demand drops by the returned quantity, and the rows of that day "
             f"are merged into one. A day whose returns exceed its sales becomes 0. Correct "
             f"only if these {n} rows really are returns booked against the same day.",
             params={"n_rows": n}, recommended=True),
        _opt("negatives_as_zero",
             "Read every negative quantity as 0.",
             f"Returns are ignored: demand for those {n} periods reads higher than what was "
             f"actually sold, and the purchase suggestions come out high by the returned amount.",
             params={"n_rows": n}),
        _opt("negatives_drop_rows",
             f"Remove those {n} rows.",
             f"You lose {n} observations ({pct}% of the file). A product left under "
             f"{MIN_TRAINABLE_PERIODS} periods is skipped by training and gets no forecast "
             f"and no reorder point at all.",
             params={"n_rows": n, "pct": pct}),
    ]
    return issue


def enrich_duplicates(issue: dict) -> dict:
    n = int(issue.get("dupe_count", 0))
    issue["classification"] = BLOCKING_FIXABLE
    issue["params"] = {"n_rows": n}
    issue["remediations"] = [
        _opt("duplicates_sum",
             "Add the rows of the same day and product into one period total.",
             f"Correct when the ERP exports one row per SALE. If those {n} rows are instead "
             f"corrections of the same sale, demand is counted twice and you order double.",
             params={"n_rows": n}, recommended=True),
        _opt("duplicates_keep_last",
             "Keep only the last row for each day and product.",
             f"Correct when a later row CORRECTS an earlier one. If they are separate sales, "
             f"the units on the discarded rows vanish from demand — three transactions of 4, 3 "
             f"and 3 become 3 instead of 10, and you order a third of what you need.",
             params={"n_rows": n}),
    ]
    return issue


def enrich_null_target(issue: dict, n_rows: int) -> dict:
    """Empty quantity cells. Blocking only once they are a real share of the file.

    A handful of blanks in a year of history are already handled — the row is
    dropped and the series survives. A tenth of the file is a different story:
    the series trains with holes in it and nobody said so.
    """
    n = int(issue.get("null_count", 0))
    pct = float(issue.get("null_pct", 0) or 0)
    blocking = pct >= 10.0
    issue["classification"] = BLOCKING_FIXABLE if blocking else ADVISORY
    issue["severity"] = "error" if blocking else "warning"
    issue["params"] = {"n_rows": n, "pct": pct}
    issue["remediations"] = [
        _opt("null_target_as_zero",
             "Read the empty cells as 0.",
             f"You assert that {n} periods ({pct}%) sold nothing. If the quantity was simply "
             f"not exported, demand is understated everywhere those blanks fall.",
             params={"n_rows": n, "pct": pct}),
        _opt("null_target_drop_rows",
             f"Remove the {n} rows with an empty quantity.",
             f"You lose {pct}% of the file. Products already close to "
             f"{MIN_TRAINABLE_PERIODS} periods can fall under it and be skipped.",
             params={"n_rows": n, "pct": pct}, recommended=True),
    ]
    return issue


def enrich_outliers(issue: dict) -> dict:
    """Advisory on purpose — and that is the judgement call worth stating.

    Extreme values are what real retail looks like: a wholesale order, a
    promotion, the week before a holiday. Gating on them would stop nearly every
    genuine file, which is the failure mode the owner ranked worst after silent
    wrongness. What was missing was never the block — it was the COST of
    clipping, which the wizard offered as a bare dropdown of statistics terms.
    """
    n = int(issue.get("outlier_count", 0))
    issue["classification"] = ADVISORY
    issue["params"] = {"n_rows": n}
    issue["remediations"] = [
        _opt("outliers_leave",
             f"Leave the {n} extreme values in.",
             "If they are real sales — a wholesale order, a promotion — this is the right "
             "answer. If they are typos, they pull the whole series up and you over-order.",
             params={"n_rows": n}, recommended=True),
        _opt("outliers_clip_iqr",
             "Clip them back to the normal range of each product (IQR fence).",
             f"The {n} peaks are flattened to the edge of ordinary demand. A genuine peak "
             f"season is erased from the history, so next year the model does not expect it "
             f"and you under-order exactly when it matters.",
             params={"n_rows": n}),
        _opt("outliers_winsorize",
             "Pull them in to a few standard deviations of each product's mean.",
             f"Same trade as clipping, applied more gently: the {n} peaks survive as smaller "
             f"peaks. Still lowers what the model expects at the next peak.",
             params={"n_rows": n}),
    ]
    return issue


_ENRICHERS = {
    "negative_target": lambda issue, ctx: enrich_negative_target(issue, ctx["n_rows"]),
    "duplicates":      lambda issue, ctx: enrich_duplicates(issue),
    "null_target":     lambda issue, ctx: enrich_null_target(issue, ctx["n_rows"]),
    "outliers":        lambda issue, ctx: enrich_outliers(issue),
}


def enrich_existing_issues(issues: list, n_rows: int) -> list:
    ctx = {"n_rows": n_rows}
    for issue in issues:
        enricher = _ENRICHERS.get(issue.get("type"))
        if enricher:
            enricher(issue, ctx)
    return issues


# ── Classification ─────────────────────────────────────────────────────────

def classify(data_quality: dict) -> dict:
    """Stamp every issue with its outcome and summarise the gate.

    Issues that arrived from the older profiler checks without a
    `classification` are treated as advisory unless their type is in
    `FATAL_TYPES` — so a check added later defaults to NOT blocking, which is
    the safe direction: an over-blocked file is a product nobody can use.
    """
    issues = data_quality.get("issues", []) or []
    for issue in issues:
        type_ = issue.get("type")
        if type_ in FATAL_TYPES:
            issue["classification"] = BLOCKING_FATAL
            issue["blocking"] = True
            issue["remediations"] = []
        elif not issue.get("classification"):
            issue["classification"] = (
                BLOCKING_FIXABLE if issue.get("blocking") else ADVISORY
            )
        issue.setdefault("params", {})
        issue.setdefault("remediations", [])
        issue.setdefault("remediable", True)
        issue["blocking"] = issue["classification"] in (BLOCKING_FATAL, BLOCKING_FIXABLE)

        # The rule, enforced rather than documented: a fixable issue without a
        # single applicable option is a fatal one wearing a friendlier label.
        if issue["classification"] == BLOCKING_FIXABLE and not issue["remediations"]:
            issue["classification"] = BLOCKING_FATAL
            issue["remediable"] = False

    fatal = [i["type"] for i in issues if i["classification"] == BLOCKING_FATAL]
    fixable = [i["type"] for i in issues if i["classification"] == BLOCKING_FIXABLE]
    advisory = [i["type"] for i in issues if i["classification"] == ADVISORY]

    data_quality["blocking_fatal"] = fatal
    data_quality["blocking_fixable"] = fixable
    data_quality["advisory"] = advisory
    data_quality["blocking"] = bool(fatal or fixable)
    data_quality["outcome"] = (
        "blocked_fatal" if fatal else "blocked_fixable" if fixable else "clear"
    )
    data_quality["gate_version"] = 1
    return data_quality


def required_choices(data_quality: dict) -> list:
    """The issue types the user must answer before a run may start."""
    return list(data_quality.get("blocking_fixable", []))


def unresolved(data_quality: dict, chosen: Optional[dict]) -> list:
    """Blocking-fixable types with no valid option recorded against them.

    A choice only counts when the option code belongs to that issue type — a
    stale `gaps_fill_zero` left over from a previous file does not resolve a
    negative-demand finding.
    """
    chosen = chosen or {}
    missing = []
    for issue in data_quality.get("issues", []) or []:
        if issue.get("classification") != BLOCKING_FIXABLE:
            continue
        type_ = issue.get("type")
        code = chosen.get(type_)
        offered = {o["code"] for o in issue.get("remediations", [])}
        if not code or code not in offered:
            missing.append(type_)
    return missing
