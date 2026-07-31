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
from typing import List, Optional
from forecasting_core.data.canonical import FIELD_DEFAULTS
from forecasting_core.data import gate as _gate


class DataProfiler:
    """
    Profiles a DataFrame to detect column types, distributions,
    and recommend target / date / group columns automatically.
    """

    MAX_SAMPLE = 10_000

    # Periods a single SKU needs before the engine will train anything on it.
    # This is `min_history` — default 20 in ValidationConfigRequest
    # (backend/schemas/configuration.py) and in backend/sessions/defaults.py —
    # the threshold DataQualityChecker.filter_valid_skus applies to drop a
    # series before routing. When it drops every series, the run ends in
    # `no_models_trained`.
    # Single source of truth lives in the gate module, so the threshold the
    # profiler reports and the threshold the gate enforces cannot drift apart.
    MIN_TRAINABLE_PERIODS = _gate.MIN_TRAINABLE_PERIODS

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
            warnings.append(
                f"Dataset has only 1 row — at least {self.MIN_TRAINABLE_PERIODS} "
                f"periods per product are required for training"
            )

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
        data_quality = self.evaluate_gate(
            df,
            date_col=recommended["date"],
            target_col=recommended["target"],
            group_col=recommended["group"],
            warnings=warnings,
            stats=stats,
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
        single_valued_cands: list = []  # fallback for single-product catalogues

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
                elif n_u == 1:
                    # A single-product file: the SKU/product column has just one
                    # value. Excluding it forces training into the "__all__"
                    # single-series mode, which DROPS the product identity — the
                    # forecast and inventory can then never be matched back to
                    # that SKU (it shows up as "Serie única" and any stock entered
                    # for the real SKU stays SIN_DATOS forever). Keep it as a
                    # fallback so a 1-SKU upload still trains per-SKU.
                    single_valued_cands.append(col)

        # Only fall back to single-valued columns when there is NO real
        # multi-valued grouping column — so multi-SKU files are unaffected.
        if not group_cands and single_valued_cands:
            def _sku_score(name: str) -> int:
                n = str(name).lower()
                return 0 if any(k in n for k in (
                    "sku", "product", "producto", "item", "codigo", "código",
                    "code", "ref", "id",
                )) else 1
            group_cands = sorted(single_valued_cands, key=_sku_score)

        return {
            "date_candidates":   date_cands,
            "target_candidates": target_cands,
            "group_candidates":  group_cands,
            "exog_candidates":   exog_cands,
        }

    # ------------------------------------------------------------------
    # Canonical field mapping
    # ------------------------------------------------------------------

    # Alias lists for each canonical field — ordered by match strength.
    # First alias in each list is the highest-priority match.
    _CANONICAL_ALIASES: dict = {
        "sku":           ["sku", "producto", "product", "articulo", "item",
                          "codigo", "code", "referencia", "ref", "id"],
        "date":          ["fecha", "date", "dt", "timestamp", "periodo", "mes", "semana"],
        "demand":        ["demanda", "demand", "ventas", "sales", "cantidad",
                          "qty", "units", "unidades", "pedidos"],
        "store":         ["tienda", "store", "sucursal", "punto_venta", "pdv",
                          "local", "canal", "negocio"],
        "region":        ["region", "zona", "area", "territory", "ciudad", "city"],
        "inventory":     ["inventario", "inventory", "stock", "existencias",
                          "disponible", "on_hand"],
        "lead_time":     ["lead_time", "leadtime", "tiempo_entrega",
                          "reposicion", "lt", "plazo"],
        "price":         ["precio", "price", "precio_venta", "pvp", "selling_price"],
        "cost":          ["costo", "cost", "precio_costo", "cog", "unit_cost"],
        "regular_price": ["precio_regular", "regular_price", "precio_base",
                          "precio_lista", "list_price"],
        "promo_price":   ["precio_promo", "promo_price", "precio_promocional",
                          "promotional_price"],
        "promo":         ["promocion", "promo", "oferta", "promotion",
                          "is_promo", "en_promo"],
        "promo_type":    ["tipo_promo", "promo_type", "tipo_oferta",
                          "tipo_promocion", "promotion_type"],
        "discount":      ["descuento", "discount", "pct_descuento",
                          "descuento_pct", "disc_pct"],
    }

    _REQUIRED_CANONICAL: frozenset = frozenset({"sku", "date", "demand"})

    def get_canonical_mapping(self, df: pd.DataFrame) -> dict:
        """
        Suggest which source column maps to each of the 14 canonical fields.

        Matching strategy:
          - Exact alias match:    confidence = 1.0 (decays slightly by alias rank)
          - Substring alias match: confidence = 0.6 (decays slightly by alias rank)
          - Type-compatibility bonus applied after name match
          - No match:             confidence = 0.0

        Returns:
            {
              canonical_field: {
                "top":             str | None,  # best candidate column name
                "candidates":      list[str],   # all matches, ranked best-first
                "confidence":      float,       # 0.0–1.0
                "can_use_default": bool,        # True for optional (non-required) fields
              }
            }
        """
        col_names_lower = {c: c.lower().replace(" ", "_") for c in df.columns}
        result: dict = {}

        for field_name, aliases in self._CANONICAL_ALIASES.items():
            candidates: list = []  # list of (score, col)

            for col, col_lower in col_names_lower.items():
                score = 0.0
                for rank, alias in enumerate(aliases):
                    if col_lower == alias:
                        score = 1.0 - rank * 0.03   # exact match; decays by alias rank
                        break
                    if alias in col_lower or col_lower in alias:
                        score = max(score, 0.6 - rank * 0.02)

                # Type-compatibility bonus
                if score > 0:
                    if field_name == "date" and self._is_date(df[col]):
                        score = min(1.0, score + 0.2)
                    elif field_name in (
                        "demand", "inventory", "price", "cost",
                        "regular_price", "promo_price", "discount", "lead_time",
                    ):
                        if pd.api.types.is_numeric_dtype(df[col]):
                            score = min(1.0, score + 0.1)
                    elif field_name == "promo":
                        if df[col].dropna().nunique() <= 2:
                            score = min(1.0, score + 0.15)

                if score > 0:
                    candidates.append((score, col))

            candidates.sort(key=lambda x: -x[0])
            top_score = candidates[0][0] if candidates else 0.0
            top_col   = candidates[0][1] if candidates else None

            result[field_name] = {
                "top":             top_col,
                "candidates":      [c for _, c in candidates],
                "confidence":      round(top_score, 2),
                "can_use_default": FIELD_DEFAULTS.get(field_name) is not None,
            }

        return result

    # ------------------------------------------------------------------
    # Data-quality checks
    # ------------------------------------------------------------------

    # Name of the composite key the gate builds for a multi-key session. Chosen
    # to be something no exported ERP column could collide with.
    _SERIES_KEY = "__series_key__"

    def evaluate_gate(
        self,
        df: pd.DataFrame,
        date_col: Optional[str],
        target_col: Optional[str],
        group_col: Optional[str],
        warnings: Optional[list] = None,
        stats: Optional[dict] = None,
        canonical_mapping: Optional[dict] = None,
        group_cols: Optional[List[str]] = None,
    ) -> dict:
        """The pre-training gate, run against ONE explicit column mapping.

        `profile()` calls this with the columns it guessed, so the wizard shows
        the gate the moment the file is inspected. The backend calls it again at
        launch with the columns the user CONFIRMED — the two must be the same
        computation or the browser and the API would enforce different rules,
        which is how a gate becomes a suggestion.

        `group_cols` is EVERY column that identifies a series, not just the SKU,
        and getting this wrong is an over-block rather than an under-block. A
        distributor with two branches sells the same SKU on the same day in
        both; grouped by SKU alone those two rows look like a duplicate, and the
        gate would demand a decision about a problem that does not exist —
        forever, on every sync. The runner already trains on the full key (see
        `_group_cols`), so the gate must judge the same series it does.
        """
        from forecasting_core.data import gate as gate_mod

        warnings = warnings if warnings is not None else []
        data_quality: dict = {"issues": [], "gap_fill_needed": False, "blocking": False}

        keys = [c for c in (group_cols or []) if c and c in df.columns]
        series_col = group_col
        if len(keys) > 1:
            df = df.copy()
            df[self._SERIES_KEY] = df[keys].astype(str).agg("|".join, axis=1)
            series_col = self._SERIES_KEY

        # Runs FIRST and unconditionally: the file that cannot train at all is
        # exactly the file whose date/target columns often fail to be detected,
        # so it must not sit behind the guard below.
        self._check_trainability(
            df, {"date": date_col, "group": series_col}, warnings, data_quality,
        )

        if len(df) > 0 and date_col and target_col:
            self._check_quality(
                df, date_col, target_col, series_col,
                warnings, data_quality, stats if stats is not None else {},
            )

        return gate_mod.evaluate(
            df, date_col, target_col, group_col,
            data_quality=data_quality,
            canonical_mapping=canonical_mapping,
            series_col=series_col,
        )

    def _check_trainability(
        self,
        df: pd.DataFrame,
        recommended: dict,
        warnings: list,
        data_quality: dict,
    ) -> None:
        """
        Flag the one case the wizard must never wave through: NO product in
        this file can reach `min_history` periods, so filter_valid_skus drops
        every series and the run can only end in `no_models_trained`. Every
        other check here is "this will be worse"; this one is "this cannot
        work", and it is marked `blocking` so the UI can say so.

        Two upper bounds on the longest trainable series, checked in order.
        Both can only UNDER-report — neither can block a file that would have
        trained:

        1. Total row count. A product cannot have more time periods than the
           whole file has rows, so `n_rows < MIN_TRAINABLE_PERIODS` settles it
           with no date column needed at all. This is the branch that catches
           the one-row upload, whose date column is not even detectable — a
           single date value never passes `_is_date` (it requires more than
           one distinct parsed value).
        2. The longest per-SKU history, as distinct dates. That is an upper
           bound on the rows that reach training: aggregating to a coarser
           granularity only merges periods, and duplicate date+SKU rows are
           summed into one. Computed over EVERY group, not the 50-group sample
           `_check_quality` uses — one long series is enough to make the file
           trainable, and missing it would block a good file.

        Deliberately NOT blocked: a catalogue where only SOME products are
        short. One new product with three weeks of history is normal and must
        still train; `short_history` already says so as a soft warning.

        Lag features consume rows on top of `min_history`, so a file that
        clears this bar can still train nothing. That stays a soft warning:
        this check only claims what is arithmetically impossible.
        """
        n_rows = len(df)
        min_needed = self.MIN_TRAINABLE_PERIODS
        # Bound 1 — holds even when nothing else about the file is known.
        longest = n_rows

        if n_rows >= min_needed:
            # Bound 1 is silent; only a per-SKU count can decide, and that
            # needs a usable date column. Without one the file has a different,
            # already-reported problem — don't guess.
            date_col = recommended.get("date")
            if not date_col or date_col not in df.columns:
                return
            try:
                dates = pd.to_datetime(df[date_col], errors="coerce")
            except Exception:
                return
            keep = dates.notna()
            if not bool(keep.any()):
                return  # unparseable dates are reported by _check_quality

            group_col = recommended.get("group")
            if group_col and group_col in df.columns:
                longest = int(dates[keep].groupby(df.loc[keep, group_col]).nunique().max())
            else:
                longest = int(dates[keep].nunique())

            if longest >= min_needed:
                return

        message = (
            f"No product has enough history to train: the longest series has "
            f"{longest} period{'s' if longest != 1 else ''}, and at least "
            f"{min_needed} are required"
        )
        warnings.append(message)
        data_quality["blocking"] = True
        data_quality["issues"].append({
            "type": "no_trainable_history",
            "severity": "error",
            # The flag that separates advice from a dead end. Consumers must
            # not infer it from `severity`: `all_zeros` and the granularity
            # conflict are already "error" and are still overrulable.
            "blocking": True,
            "message": message,
            "longest_history": longest,
            "min_required": min_needed,
            "n_rows": n_rows,
        })

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
        # series.isna().mean() is NaN for a zero-row column (header-only file),
        # and Starlette's JSONResponse rejects NaN floats outright (allow_nan=False),
        # turning an empty-file upload into an opaque 500 instead of a clean response.
        null_pct = round(series.isna().mean() * 100, 2) if len(series) > 0 else 0.0
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

    # Column-name substrings that strongly imply a SKU/group identifier, even when the
    # values are low-cardinality numeric codes that would otherwise fail the cardinality
    # heuristic below (e.g. two SKUs "1001"/"1002" only have n_unique=2).
    _GROUP_NAME_HINTS = (
        "sku", "product", "producto", "item", "articulo", "artículo",
        "group", "grupo", "store", "tienda", "cliente", "customer",
        "codigo", "código", "id",
    )
    # Column-name substrings that strongly imply the demand/target measure — excluded from
    # competing as a group candidate so a target column never wins group detection by default.
    _TARGET_NAME_HINTS = (
        "target", "sales", "venta", "demanda", "demand", "qty",
        "quantity", "cantidad", "units", "unidades",
    )

    def _detect_group(self, df, dt_col, warnings) -> Optional[str]:
        # Name-hint pre-pass: a column whose name clearly identifies it as a SKU/group
        # column should win immediately, before the cardinality heuristic gets a chance
        # to misread it (e.g. numeric SKU codes with very few distinct values).
        for col in df.columns:
            if col == dt_col:
                continue
            name = str(col).strip().lower()
            if any(hint in name for hint in self._GROUP_NAME_HINTS):
                n_u = df[col].nunique()
                n = len(df)
                if 1 <= n_u < n:
                    return col

        best, best_score = None, -1
        for col in df.columns:
            if col == dt_col:
                continue
            name = str(col).strip().lower()
            # Don't let an obvious target/measure column win group detection by default.
            if any(hint in name for hint in self._TARGET_NAME_HINTS):
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

        # Name-hint pre-pass: a numeric column whose name clearly identifies it as the
        # demand/sales measure should win immediately, before the variance heuristic below
        # gets a chance to prefer an unrelated numeric column (e.g. a stock/inventory level,
        # which tends to have higher variance than steady demand) just because of variance.
        for col in numeric:
            name = str(col).strip().lower()
            if any(hint in name for hint in self._TARGET_NAME_HINTS):
                return col

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

    # Canonical frequency bucket order, finest to coarsest. Every consumer of
    # detect_granularity's "detected" list relies on this exact order to know
    # which frequency is coarser without re-deriving it.
    _FREQ_BUCKETS = ["D", "W", "2W", "MS"]

    def _classify_sku_frequency(self, dates: pd.Series) -> Optional[str]:
        """
        Classify one SKU's date series into a frequency bucket by the median
        gap between consecutive (sorted, deduplicated) dates. Returns None for
        insufficient history or an irregular cadence — such SKUs are excluded
        from the granularity conflict determination entirely, not treated as
        a fifth bucket.
        """
        d = pd.to_datetime(dates, errors="coerce").dropna().sort_values().drop_duplicates()
        if len(d) < 3:
            return None
        median_gap = d.diff().dropna().median()
        days = median_gap.days
        if days == 1:
            return "D"
        if 6 <= days <= 8:
            return "W"
        if 13 <= days <= 15:
            return "2W"
        if 28 <= days <= 35:
            return "MS"
        return None

    def detect_granularity(
        self, df: pd.DataFrame, date_col: Optional[str], group_col: Optional[str],
    ) -> dict:
        """
        Detect per-SKU temporal granularity and flag heterogeneous conflicts.

        Returns:
            {
              "status":             "homogeneous" | "conflict" | "unknown",
              "detected":           [...],  # sorted finest→coarsest, only buckets present
              "skus_by_frequency":  {bucket: [sku, ...]},  # full SKU list per bucket
              "suggested_target":   "..." | None,           # coarsest bucket present
            }
        """
        empty = {"status": "unknown", "detected": [], "skus_by_frequency": {}, "suggested_target": None}
        if not date_col or date_col not in df.columns:
            return empty

        if not group_col or group_col not in df.columns:
            # Single series — it can never conflict with itself.
            bucket = self._classify_sku_frequency(df[date_col])
            detected = [bucket] if bucket else []
            return {
                "status": "homogeneous",
                "detected": detected,
                "skus_by_frequency": {bucket: ["__all__"]} if bucket else {},
                "suggested_target": bucket,
            }

        skus_by_frequency: dict = {}
        for sku, g in df.groupby(group_col):
            bucket = self._classify_sku_frequency(g[date_col])
            if bucket is None:
                continue  # irregular/insufficient — excluded from the conflict determination
            skus_by_frequency.setdefault(bucket, []).append(str(sku))

        detected = [b for b in self._FREQ_BUCKETS if b in skus_by_frequency]
        status = "homogeneous" if len(detected) <= 1 else "conflict"
        suggested_target = detected[-1] if detected else None

        return {
            "status": status,
            "detected": detected,
            "skus_by_frequency": skus_by_frequency,
            "suggested_target": suggested_target,
        }
