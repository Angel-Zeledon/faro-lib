import pytest
from forecasting_core.business.optimizer import VariableIndex


class TestVariableIndex:
    def test_all_indices_unique_and_in_range(self):
        idx = VariableIndex(skus=["A", "B"], warehouses=["W1", "W2"], horizon=2)
        seen = set()
        for i in ["A", "B"]:
            for w in ["W1", "W2"]:
                for t in [1, 2]:
                    for method, args in [
                        (idx.order_idx, (i, w, t)),
                        (idx.inv_idx, (i, w, t)),
                        (idx.short_idx, (i, w, t)),
                    ]:
                        v = method(*args)
                        assert 0 <= v < idx.n_vars
                        assert v not in seen
                        seen.add(v)
            for a in ["W1", "W2"]:
                for b in ["W1", "W2"]:
                    if a == b:
                        continue
                    for t in [1, 2]:
                        v = idx.transfer_idx(i, a, b, t)
                        assert 0 <= v < idx.n_vars
                        assert v not in seen
                        seen.add(v)
        assert len(seen) == idx.n_vars  # every slot used exactly once

    def test_n_vars_matches_expected_count(self):
        # 2 SKUs, 3 warehouses, 4 buckets:
        #   order + inv + short: 2*3*4 each = 24 each -> 72
        #   transfer: 2 SKUs * (3*2 ordered pairs) * 4 buckets = 2*6*4 = 48
        idx = VariableIndex(skus=["A", "B"], warehouses=["W1", "W2", "W3"], horizon=4)
        assert idx.n_vars == 72 + 48

    def test_transfer_idx_rejects_same_warehouse(self):
        idx = VariableIndex(skus=["A"], warehouses=["W1", "W2"], horizon=1)
        with pytest.raises(ValueError):
            idx.transfer_idx("A", "W1", "W1", 1)

    def test_order_and_inv_and_short_indices_are_distinct_blocks(self):
        idx = VariableIndex(skus=["A"], warehouses=["W1"], horizon=1)
        o = idx.order_idx("A", "W1", 1)
        v = idx.inv_idx("A", "W1", 1)
        s = idx.short_idx("A", "W1", 1)
        assert len({o, v, s}) == 3
