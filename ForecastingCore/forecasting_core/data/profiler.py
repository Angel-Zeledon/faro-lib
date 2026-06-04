"""
DataProfiler — inspects a DataFrame to expose metadata to the engine and frontend.

This is the core of the "interactive column selection" flow:
  1. User uploads file → frontend calls GET /datasets/{id}/columns
  2. API calls profiler.profile(df)
  3. Frontend renders column pickers, type badges, cardinality info
  4. User selects target/date/group → frontend calls POST /datasets/{id}/choose_columns

Example:
    profiler = DataProfiler()
    meta = profiler.profile(df)
    # → {columns: [...], recommended: {...}, stats: {...}}
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, List, Optional


class DataProfiler:
    """
    Profiles a DataFrame to detect column types, distributions,
    and recommend target / date / group columns automatically.
    """

    MAX_SAMPLE = 10_000

    def profile(self, df: pd.DataFrame) -> dict:
        """
        Full profile of the DataFrame.

        Returns:
            {
              columns:     [ {name, dtype, role_hint, n_unique, null_pct, sample} ],
              recommended: {date, target, group, freq},
              stats:       {n_rows, n_cols, n_skus, date_range, ...},
              warnings:    [...],
              data_quality:{issues: [...], gap_fill_needed: bool},
            }
        """
        sample = df.head(self.MAX_SAMPLE)
        warnings: List[str] = []

        # ── Empty / near-empty checks ──────────────────────────────────────
        if len(df) == 0:
            warnings.append("Dataset is empty (header only) — no rows to analyze")
        elif len(df) == 1:
            warnings.append("Dataset has only 1 row — minimum ~30 rows required for training")

        cols = [self._profile_column(col, sample[col]) for col in df.columns]
        recommended = self._recommend(sample, warnings)

        stats = {
            "n_rows": len(df),
            "n_cols": len(df.columns),
        }
        if recommended["group"]:
            stats["n_skus"] = df[recommended["group"]].nunique()
        if recommended["date"]:
            try:
                dates = pd.to_datetime(df[recommended["date"]], errors="coerce").dropna()
                stats["date_min"] = str(dates.min().date())
                stats["date_max"] = str(dates.max().date())
            except Exception:
                pass

        # ── Comprehensive data-quality checks ────────────────────────────
        data_quality: dict = {"issues": [], "gap_fill_needed": False}
        if len(df) > 0 and recommended["date"] and recommended["target"]:
            self._check_quality(
                df,
                recommended["date"],
                recommended["target"],
                recommended["group"],
                warnings,
                data_quality,
                stats,
            )

        return {
            "columns":     cols,
            "recommended": recommended,
            "stats":       stats,
            "warnings":    warnings,
            "data_quality": data_quality,
        }

    def get_column_options(self, df: pd.DataFrame) -> dict:
        """
        Returns lists of candidate columns for each role — used by frontend dropdowns.

        Returns:
            {
              date_candidates:   ["date", "week", ...],
              target_candidates: ["sales", "units", ...],
              group_candidates:  ["sku", "product_id", ...],
              exog_candidates:   ["price", "promo", ...],
            }
        """
        sample = df.head(self.MAX_SAMPLE)
        date_cands, target_cands, group_cands, exog_cands = [], [], [], []

        for col in df.columns:
            s = sample[col]
            if self._is_date(s):
                date_cands.append(col)
            elif pd.api.types.is_numeric_dtype(s):
                # All numeric non-date columns are valid target candidates;
                # low-cardinality ones are also offered as exog suggestions.
                target_cands.append(col)
                ratio = s.nunique() / (len(s) + 1)
                if ratio <= 0.5:
                    exog_cands.append(col)
            else:
                n_u = s.nunique()
                if 2 <= n_u <= len(s) * 0.5:
                    group_cands.append(col)

        return {
            "date_candidates":   date_cands,
            "target_candidates": target_cands,
            "group_candidates":  group_cands,
            "exog_candidates":   exog_cands,
        }

    # ------------------------------------------------------------------
    # Data-quality checks
    # ------------------------------------------------------------------

    def _check_quality(
        self,
        df: pd.DataFrame,
        date_col: str,
        target_col: str,
        group_col: Optional[str],
        warnings: list,
        data_quality: dict,
        stats: dict,
    ) -> None:
        """Run comprehensive data-quality checks and populate warnings + data_quality."""
        today = pd.Timestamp.now().normalize()

        # ── Parse dates ───────────────────────────────────────────────────
        df = df.copy()
        try:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        except Exception:
            warnings.append(f"Could not parse date column '{date_col}'")
            return

        null_dates = int(df[date_col].isna().sum())
        if null_dates > 0:
            warnings.append(
                f"{null_dates} row{'s' if null_dates != 1 else ''} have unparseable or "
                f"missing dates in '{date_col}'"
            )
            data_quality["issues"].append({
                "type": "invalid_dates",
                "severity": "warning",
                "message": f"{null_dates} rows with invalid/missing dates",
            })

        # ── Future dates ──────────────────────────────────────────────────
        future_count = int((df[date_col] > today).sum())
        if future_count > 0:
            warnings.append(
                f"{future_count} row{'s' if future_count != 1 else ''} contain future dates — "
                f"historical data should only include past observations"
            )
            data_quality["issues"].append({
                "type": "future_dates",
                "severity": "info",
                "message": f"{future_count} rows with future dates",
            })

        # ── Target column quality ─────────────────────────────────────────
        try:
            target = pd.to_numeric(df[target_col], errors="coerce")
            null_target = int(target.isna().sum())

            if null_target > 0:
                pct = round(null_target / len(df) * 100, 1)
                warnings.append(
                    f"{null_target} null/non-numeric values ({pct}%) in target '{target_col}'"
                )
                data_quality["issues"].append({
                    "type": "null_target",
                    "severity": "warning",
                    "message": f"{null_target} null values ({pct}%) in target column",
                    "null_count": null_target,
                    "null_pct": pct,
                })

            target_clean = target.dropna()

            if len(target_clean) > 0:
                # Negative values
                neg_count = int((target_clean < 0).sum())
                if neg_count > 0:
                    pct = round(neg_count / len(target_clean) * 100, 1)
                    warnings.append(
                        f"{neg_count} negative value{'s' if neg_count != 1 else ''} ({pct}%) "
                        f"in target '{target_col}' — most demand models expect non-negative values"
                    )
                    data_quality["issues"].append({
                        "type": "negative_target",
                        "severity": "warning",
                        "message": f"{neg_count} negative values ({pct}%) in target",
                        "neg_count": neg_count,
                        "neg_pct": pct,
                    })

                # All zeros
                zero_pct = float((target_clean == 0).mean())
                if zero_pct == 1.0:
                    warnings.append(
                        f"All target values are zero in '{target_col}' — "
                        f"no demand signal available for forecasting"
                    )
                    data_quality["issues"].append({
                        "type": "all_zeros",
                        "severity": "error",
                        "message": "All target values are zero — no demand signal",
                    })
                elif zero_pct > 0.7:
                    pct_str = round(zero_pct * 100, 1)
                    warnings.append(
                        f"{pct_str}% of target values are zero — "
                        f"highly intermittent demand detected; consider using the Croston model"
                    )
                    data_quality["issues"].append({
                        "type": "intermittent",
                        "severity": "warning",
                        "message": f"{pct_str}% zeros — intermittent demand",
                        "zero_pct": pct_str,
                    })

                # Constant (zero variance)
                target_std = float(target_clean.std())
                if target_std < 1e-10 and float(target_clean.mean()) != 0:
                    val = round(float(target_clean.iloc[0]), 4)
                    warnings.append(
                        f"Target '{target_col}' is constant ({val}) — "
                        f"zero variance makes forecasting trivial but may indicate a data issue"
                    )
                    data_quality["issues"].append({
                        "type": "constant_target",
                        "severity": "warning",
                        "message": f"Target is constant ({val}) — zero variance",
                    })

                # Extreme outliers (IQR × 3)
                Q1 = float(target_clean.quantile(0.25))
                Q3 = float(target_clean.quantile(0.75))
                IQR = Q3 - Q1
                if IQR > 0:
                    lo, hi = Q1 - 3 * IQR, Q3 + 3 * IQR
                    n_out = int(((target_clean < lo) | (target_clean > hi)).sum())
                    if n_out > 0:
                        pct_out = round(n_out / len(target_clean) * 100, 1)
                        warnings.append(
                            f"{n_out} extreme outlier{'s' if n_out != 1 else ''} in "
                            f"'{target_col}' (beyond 3× IQR) — may distort model training"
                        )
                        data_quality["issues"].append({
                            "type": "outliers",
                            "severity": "info",
                            "message": f"{n_out} extreme outliers (3× IQR rule)",
                            "outlier_count": n_out,
                        })
                        data_quality["outliers"] = {
                            "total_count": n_out,
                            "total_pct": pct_out,
                            "global_iqr_lo": round(lo, 4),
                            "global_iqr_hi": round(hi, 4),
                            "per_sku": {},
                        }
        except Exception:
            pass

        # ── Duplicate rows ────────────────────────────────────────────────
        key_cols = [date_col] + ([group_col] if group_col else [])
        dupe_count = int(df.duplicated(subset=key_cols, keep=False).sum())
        if dupe_count > 0:
            warnings.append(
                f"{dupe_count} duplicate date+SKU row{'s' if dupe_count != 1 else ''} — "
                f"each date should appear only once per SKU"
            )
            data_quality["issues"].append({
                "type": "duplicates",
                "severity": "warning",
                "message": f"{dupe_count} duplicate date/SKU rows",
                "dupe_count": dupe_count,
            })

        # ── Per-SKU checks (short history, gaps, unequal lengths, outliers) ─
        groups = df[group_col].unique().tolist() if group_col else [None]
        short_count = 0
        sku_lengths: List[int] = []
        total_gap_count = 0
        gap_skus: List[str] = []
        outlier_per_sku: dict = {}

        # Detect native frequency from first non-trivial group
        freq_days: Optional[int] = None
        for g in groups[:5]:
            sub = df if g is None else df[df[group_col] == g]
            d = sub[date_col].dropna().sort_values().drop_duplicates()
            if len(d) >= 3:
                md = int(d.diff().dropna().dt.days.median())
                if md >= 1:
                    freq_days = md
                    break

        warned_dupe = dupe_count > 0  # already warned globally
        for g in groups[:50]:  # cap for performance
            sub = df if g is None else df[df[group_col] == g]
            sku_dates = sub[date_col].dropna().sort_values().drop_duplicates()
            n = len(sku_dates)
            sku_lengths.append(n)

            if n < 20:
                short_count += 1

            # Per-SKU outlier detection (IQR with std fallback when IQR=0)
            if group_col and g is not None:
                try:
                    sku_tgt = pd.to_numeric(sub[target_col], errors="coerce").dropna()
                    if len(sku_tgt) >= 4:
                        _Q1 = float(sku_tgt.quantile(0.25))
                        _Q3 = float(sku_tgt.quantile(0.75))
                        _IQR = _Q3 - _Q1
                        if _IQR > 0:
                            _lo, _hi = _Q1 - 3 * _IQR, _Q3 + 3 * _IQR
                        else:
                            # All values identical or near-identical — fall back to 3×std
                            _mu, _sig = float(sku_tgt.mean()), float(sku_tgt.std())
                            if _sig > 1e-10:
                                _lo, _hi = _mu - 3 * _sig, _mu + 3 * _sig
                            else:
                                _lo, _hi = float(sku_tgt.min()) - 1, float(sku_tgt.max()) + 1
                        _n = int(((sku_tgt < _lo) | (sku_tgt > _hi)).sum())
                        outlier_per_sku[str(g)] = {
                            "count": _n,
                            "pct": round(_n / len(sku_tgt) * 100, 1),
                            "iqr_lo": round(_lo, 4),
                            "iqr_hi": round(_hi, 4),
                            "n_rows": len(sku_tgt),
                        }
                except Exception:
                    pass

            # Temporal gap detection
            if freq_days and freq_days > 0 and n >= 3:
                try:
                    expected = pd.date_range(
                        sku_dates.min(), sku_dates.max(),
                        freq=f"{freq_days}D"
                    )
                    missing = expected.difference(sku_dates)
                    if len(missing) > 0:
                        total_gap_count += len(missing)
                        label = str(g) if g is not None else "__all__"
                        gap_skus.append(label)
                except Exception:
                    pass

        if short_count > 0:
            label = f"{short_count} SKU{'s' if short_count != 1 else ''}"
            warnings.append(
                f"{label} with fewer than 20 time periods — "
                f"insufficient history for reliable training"
            )
            data_quality["issues"].append({
                "type": "short_history",
                "severity": "warning",
                "message": f"{short_count} short-history series (< 20 periods)",
                "short_count": short_count,
            })

        if total_gap_count > 0:
            freq_label = (
                f"{freq_days}-day" if freq_days and freq_days > 1
                else "daily" if freq_days == 1
                else "periodic"
            )
            n_skus_affected = len(gap_skus)
            warnings.append(
                f"{total_gap_count} missing date{'s' if total_gap_count != 1 else ''} "
                f"in {n_skus_affected} series ({freq_label} frequency) — "
                f"gaps should be filled before forecasting"
            )
            data_quality["issues"].append({
                "type": "temporal_gaps",
                "severity": "warning",
                "message": (
                    f"{total_gap_count} missing dates across "
                    f"{n_skus_affected} series ({freq_label} frequency)"
                ),
                "gap_count": int(total_gap_count),
                "n_skus_affected": n_skus_affected,
                "affected_skus": gap_skus[:5],
                "freq_days": freq_days,
            })
            data_quality["gap_fill_needed"] = True

        # Merge per-SKU outlier info
        if outlier_per_sku:
            if "outliers" not in data_quality:
                total = sum(v["count"] for v in outlier_per_sku.values())
                data_quality["outliers"] = {
                    "total_count": total,
                    "total_pct": round(total / max(sum(v["n_rows"] for v in outlier_per_sku.values()), 1) * 100, 1),
                    "per_sku": {},
                }
            data_quality["outliers"]["per_sku"] = outlier_per_sku

        # Unequal history lengths across SKUs
        if len(sku_lengths) > 1:
            max_len = max(sku_lengths)
            min_len = min(sku_lengths)
            if max_len > 0 and min_len / max_len < 0.3:
                warnings.append(
                    f"Unequal history lengths across SKUs "
                    f"(min={min_len}, max={max_len} periods) — "
                    f"short-history SKUs may produce unreliable forecasts"
                )
                data_quality["issues"].append({
                    "type": "unequal_history",
                    "severity": "info",
                    "message": f"History varies from {min_len} to {max_len} periods across SKUs",
                    "min_len": int(min_len),
                    "max_len": int(max_len),
                })

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _profile_column(self, name: str, series: pd.Series) -> dict:
        null_pct = round(series.isna().mean() * 100, 2)
        n_unique = series.nunique()
        sample_vals = series.dropna().head(3).tolist()

        role_hint = "unknown"
        if self._is_date(series):
            role_hint = "date"
        elif pd.api.types.is_numeric_dtype(series):
            cv = series.std() / (abs(series.mean()) + 1e-8) if len(series) > 1 else 0
            role_hint = "target" if cv > 0.1 else "numeric"
        elif n_unique / (len(series) + 1) < 0.5:
            role_hint = "categorical"

        return {
            "name":      name,
            "dtype":     str(series.dtype),
            "role_hint": role_hint,
            "n_unique":  int(n_unique),
            "null_pct":  null_pct,
            "sample":    [str(v) for v in sample_vals],
        }

    def _recommend(self, df: pd.DataFrame, warnings: list) -> dict:
        date_col    = self._detect_date(df, warnings)
        group_col   = self._detect_group(df, date_col, warnings)
        target_col  = self._detect_target(df, date_col, group_col, warnings)
        freq        = self._detect_freq(df, date_col, group_col)
        return {
            "date":   date_col,
            "target": target_col,
            "group":  group_col,
            "freq":   freq,
        }

    def _is_date(self, s: pd.Series) -> bool:
        if pd.api.types.is_datetime64_any_dtype(s):
            return True
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            try:
                parsed = pd.to_datetime(s, format="mixed", errors="coerce")
                return parsed.notna().mean() >= 0.8 and parsed.nunique() > 1
            except Exception:
                pass
        return False

    def _detect_date(self, df, warnings) -> Optional[str]:
        for col in df.columns:
            if self._is_date(df[col]):
                return col
        warnings.append("No date column detected automatically")
        return None

    def _detect_group(self, df, dt_col, warnings) -> Optional[str]:
        best, best_score = None, -1
        for col in df.columns:
            if col == dt_col:
                continue
            # Skip purely numeric columns — group/SKU columns are almost always categorical.
            # Numeric integer-ID columns may still qualify if they look like IDs (high cardinality).
            is_numeric = pd.api.types.is_numeric_dtype(df[col])
            n_u = df[col].nunique()
            n = len(df)
            if n_u < 2 or n_u == n:
                continue
            ratio = n_u / n
            if 0.001 <= ratio <= 0.5:
                is_cat = not is_numeric
                # Numeric columns need higher cardinality to be considered as group IDs
                if is_numeric and n_u < 10:
                    continue
                score = (1 - ratio) * (2 if is_cat else 1)
                if score > best_score:
                    best, best_score = col, score
        if best is None:
            warnings.append("No group/SKU column detected — treating as single series")
        return best

    def _detect_target(self, df, dt_col, group_col, warnings) -> Optional[str]:
        exclude = {dt_col, group_col}
        numeric = [c for c in df.select_dtypes(include=np.number).columns if c not in exclude]
        if not numeric:
            warnings.append("No numeric target column detected")
            return None
        scores = {}
        for col in numeric:
            s = df[col].dropna()
            if len(s) < 2:
                continue
            cv = s.std() / (abs(s.mean()) + 1e-8)
            scores[col] = cv * (s >= 0).mean()
        return max(scores, key=scores.get) if scores else numeric[0]

    def _detect_freq(self, df, dt_col, group_col) -> Optional[str]:
        if not dt_col:
            return None
        try:
            src = df
            if group_col:
                first = df[group_col].iloc[0]
                src = df[df[group_col] == first]
            dates = pd.to_datetime(src[dt_col], errors="coerce").dropna().sort_values().drop_duplicates()
            if len(dates) < 3:
                return None
            md = dates.diff().dropna().median()
            if md.days == 1:   return "D"
            if 5 <= md.days <= 8:  return "W"
            if 25 <= md.days <= 35: return "MS"
        except Exception:
            pass
        return None
