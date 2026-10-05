"""Known-value tests for the benchmark metrics and determinism of the adapters.

Run from ForecastingCore/:  python -m pytest benchmarks/tests -q
"""

import math

import numpy as np
import pandas as pd
import pytest

from benchmarks import baselines, metrics
from benchmarks.datasets import synthetic_retail, csv_dataset
from benchmarks.run import origin_cuts


def test_mae_wape_bias_known_values():
    y = [10, 20, 30]
    f = [12, 18, 36]
    assert metrics.mae(y, f) == pytest.approx((2 + 2 + 6) / 3)
    assert metrics.wape(y, f) == pytest.approx(10 / 60)
    assert metrics.bias(y, f) == pytest.approx(6 / 60)      # (2 - 2 + 6) / 60


def test_asymmetric_cost_known_value_and_pooled_ratio():
    y, f = [10, 20, 30], [12, 18, 36]          # over 2, under 2, over 6
    assert metrics.asymmetric_cost_sum(y, f) == pytest.approx(2 + 3 * 2 + 6)
    from benchmarks.report import _agg_method
    rec = {"methods": {"m": metrics.score_forecast(y, f, [1, 2, 3, 4], 1),
                       "naive": metrics.score_forecast(y, [30, 30, 30], [1, 2, 3, 4], 1)}}
    assert _agg_method([rec, rec], "m")["cost_ratio"] == pytest.approx(14 / 60)


def test_wape_and_bias_undefined_on_zero_actuals():
    assert math.isnan(metrics.wape([0, 0], [1, 1]))
    assert math.isnan(metrics.bias([0, 0], [1, 1]))


def test_smape_known_value_and_zero_zero_step():
    # |10-20| / (10+20) * 200 = 66.67 ; second step 0/0 counts as perfect
    assert metrics.smape([10, 0], [20, 0]) == pytest.approx((200 * 10 / 30 + 0) / 2)


def test_mase_uses_insample_seasonal_naive_scale():
    insample = [1, 3, 2, 4, 3, 5]       # m=1 diffs: 2,1,2,1,2 -> mean 1.6
    scale = metrics.mase_scale(insample, 1)
    assert scale == pytest.approx(1.6)
    assert metrics.mase([10, 10], [11, 13], scale) == pytest.approx(2.0 / 1.6)
    # m=2 diffs: |2-1|,|4-3|,|3-2|,|5-4| = 1 each
    assert metrics.mase_scale(insample, 2) == pytest.approx(1.0)


def test_mase_scale_nan_on_flat_series():
    assert math.isnan(metrics.mase_scale([5, 5, 5, 5], 1))
    assert math.isnan(metrics.mase([1, 1], [2, 2], float("nan")))


def test_pinball_known_value():
    # tau=0.9, y=10, q=8 -> 0.9*2=1.8 ; y=10, q=12 -> 0.1*2=0.2 ; mean 1.0
    assert metrics.pinball([10, 10], {0.9: [8, 12]}) == pytest.approx(1.0)
    # median pinball equals MAE / 2
    assert metrics.pinball([10, 20], {0.5: [12, 17]}) == pytest.approx(metrics.mae([10, 20], [12, 17]) / 2)


def test_coverage_known_value():
    assert metrics.coverage([1, 5, 9, 20], [0, 0, 0, 0], [10, 10, 10, 10]) == pytest.approx(0.75)


def test_fva_sign_and_undefined():
    assert metrics.fva(8, 10) == pytest.approx(0.2)       # better than naive
    assert metrics.fva(12, 10) == pytest.approx(-0.2)     # worse than naive
    assert math.isnan(metrics.fva(1, 0))


def test_segment_quadrants_syntetos_boylan():
    smooth = [100, 102, 98, 101, 99, 100, 103, 97]
    assert metrics.classify_segment(smooth) == "smooth"
    erratic = [10, 200, 5, 150, 20, 300, 8, 90]
    assert metrics.classify_segment(erratic) == "erratic"
    intermittent = [0, 5, 0, 0, 5, 0, 0, 5, 0, 0, 6, 0]            # ADI 3, equal sizes
    assert metrics.classify_segment(intermittent) == "intermittent"
    lumpy = [0, 1, 0, 0, 50, 0, 0, 0, 3, 0, 0, 200]               # ADI 3, CV2 large
    assert metrics.classify_segment(lumpy) == "lumpy"


def test_adi_cv2_values():
    adi, cv2 = metrics.adi_cv2([0, 4, 0, 4, 0, 0, 4, 0])
    assert adi == pytest.approx(8 / 3)
    assert cv2 == pytest.approx(0.0)


def test_baselines_known_values():
    x = [1, 2, 3, 4, 5, 6, 7, 8]
    assert list(baselines.naive(x, 3)) == [8, 8, 8]
    assert list(baselines.seasonal_naive(x, 5, 4)) == [5, 6, 7, 8, 5]
    assert list(baselines.moving_average(x, 2, window=4)) == [6.5, 6.5]


def test_synthetic_is_deterministic_and_seed_sensitive():
    a = synthetic_retail(max_series=10, seed=7)
    b = synthetic_retail(max_series=10, seed=7)
    c = synthetic_retail(max_series=10, seed=8)
    pd.testing.assert_frame_equal(a.df, b.df)
    pd.testing.assert_frame_equal(a.truth, b.truth)
    assert not a.df["demand"].equals(c.df["demand"])
    assert (a.df["demand"] >= 0).all()


def test_synthetic_prefix_stable_when_series_count_grows():
    small = synthetic_retail(max_series=5, seed=1).df
    large = synthetic_retail(max_series=10, seed=1).df
    pd.testing.assert_frame_equal(small, large[large["sku"].isin(small["sku"].unique())].reset_index(drop=True))


def test_synthetic_covers_all_four_segments():
    ds = synthetic_retail(max_series=100, seed=42)
    segs = set()
    for _, g in ds.df.groupby("sku"):
        segs.add(metrics.classify_segment(g["demand"].to_numpy()[:-16]))
    assert segs == set(metrics.SEGMENTS)


def test_origin_cuts_leave_a_full_holdout():
    ds = synthetic_retail(max_series=2, seed=1, horizon=8)
    cuts = origin_cuts(ds, 2)
    grid = np.sort(ds.df["date"].unique())
    assert cuts[1] == pd.Timestamp(grid[-1 - 8])
    assert cuts[0] == pd.Timestamp(grid[-1 - 16])


def test_csv_adapter_roundtrip_is_deterministic(tmp_path):
    ds = synthetic_retail(max_series=6, seed=3)
    path = tmp_path / "canon.csv"
    ds.df.to_csv(path, index=False)
    a = csv_dataset(str(path), max_series=4, seed=5)
    b = csv_dataset(str(path), max_series=4, seed=5)
    pd.testing.assert_frame_equal(a.df, b.df)
    assert a.n_series == 4 and a.freq.startswith("W")


def test_csv_adapter_rejects_non_canonical(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"a": [1], "b": [2]}).to_csv(path, index=False)
    with pytest.raises(ValueError):
        csv_dataset(str(path))
