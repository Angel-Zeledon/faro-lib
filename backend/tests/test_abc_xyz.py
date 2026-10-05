"""
ABC / XYZ classification — the pure math (no database, no server).

Pinned here:
  * the 80% / 95% cumulative boundaries, decided on the share BEFORE a SKU is
    added (a dominant SKU is an A, never a C);
  * ties, zero demand everywhere, a single SKU, an empty catalogue;
  * the XYZ cut-offs at exactly 0.5 and 1.0 and a missing CV;
  * the semáforo's `_classify_abc` / `_classify_xyz` still return what the
    shipped implementation returned (a frozen copy lives below), so moving the
    math into `abc_xyz.py` changed no letter;
  * the per-class summary: always A, B, C; owned SKUs are not counted as
    changeable; a class already at its suggestion changes nothing.
"""
from __future__ import annotations

import random

import pytest

from backend.inventory import abc_xyz
from backend.inventory.abc_xyz import SUGGESTED_SERVICE_LEVEL, class_summary, classify_abc, classify_xyz


def _shipped_classify_abc(items):
    scored = []
    for item in items:
        demand = item.get("daily_demand") or 0.0
        cost = item.get("unit_cost") or 1.0
        scored.append((item["sku"], demand * cost))
    scored.sort(key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in scored)
    if total == 0:
        return {sku: "C" for sku, _ in scored}
    result = {}
    cumulative = 0.0
    for sku, val in scored:
        if cumulative < 0.80:
            result[sku] = "A"
        elif cumulative < 0.95:
            result[sku] = "B"
        else:
            result[sku] = "C"
        cumulative += val / total
    return result


class TestAbcBoundaries:
    def test_cumulative_cutoffs_80_and_95(self):
        # Ten SKUs, values 50, 30, 10, 5, 3, 1, 0.5, 0.3, 0.1, 0.1 of 100.
        values = [50, 30, 10, 5, 3, 1, 0.5, 0.3, 0.1, 0.1]
        got = classify_abc((f"s{i}", v) for i, v in enumerate(values))
        # Before s0: 0 -> A. Before s1: 0.50 -> A. Before s2: 0.80 -> NOT < 0.80 -> B.
        assert [got[f"s{i}"] for i in range(3)] == ["A", "A", "B"]
        # Before s3: 0.90 -> B. Before s4: 0.95 -> NOT < 0.95 -> C.
        assert got["s3"] == "B"
        assert got["s4"] == "C"
        assert all(got[f"s{i}"] == "C" for i in range(4, 10))

    def test_a_dominant_sku_is_an_A_not_a_C(self):
        got = classify_abc([("big", 990.0), ("tiny", 10.0)])
        assert got == {"big": "A", "tiny": "C"}

    def test_a_sku_that_crosses_the_line_keeps_the_class_it_started_in(self):
        # s0 alone is 79% -> A; s1 starts at 79% (< 80%) so it is still an A even
        # though it pushes the running share to 100%.
        got = classify_abc([("s0", 79.0), ("s1", 21.0)])
        assert got == {"s0": "A", "s1": "A"}

    def test_single_sku_is_A(self):
        assert classify_abc([("only", 5.0)]) == {"only": "A"}

    def test_single_sku_without_demand_is_C(self):
        assert classify_abc([("only", 0.0)]) == {"only": "C"}

    def test_empty_catalogue(self):
        assert classify_abc([]) == {}

    def test_zero_demand_everywhere_is_all_C(self):
        got = classify_abc([("a", 0.0), ("b", 0.0), ("c", 0.0)])
        assert set(got.values()) == {"C"}
        assert set(got) == {"a", "b", "c"}

    def test_zero_demand_sku_among_active_ones_is_C(self):
        got = classify_abc([("dead", 0.0), ("a", 60.0), ("b", 40.0)])
        assert got["dead"] == "C"
        assert got["a"] == "A"

    def test_ties_keep_input_order_and_are_deterministic(self):
        # Four equal SKUs: shares 25% each. Running share before each: 0, .25,
        # .50, .75 -> all under 80% -> all A. Order of input never changes that.
        values = [("a", 1.0), ("b", 1.0), ("c", 1.0), ("d", 1.0)]
        assert set(classify_abc(values).values()) == {"A"}
        # Five equal SKUs: the fifth starts at exactly 80% -> B (stable: last in).
        five = [(f"s{i}", 1.0) for i in range(5)]
        got = classify_abc(five)
        assert [got[f"s{i}"] for i in range(5)] == ["A", "A", "A", "A", "B"]
        assert classify_abc(five) == got

    def test_every_sku_gets_a_class(self):
        rng = random.Random(7)
        items = [(f"s{i}", rng.random() * 100) for i in range(200)]
        got = classify_abc(items)
        assert set(got) == {sku for sku, _ in items}
        assert set(got.values()) <= {"A", "B", "C"}

    def test_value_never_makes_a_lower_value_sku_a_better_class(self):
        rng = random.Random(11)
        items = [(f"s{i}", rng.random() * 100) for i in range(300)]
        got = classify_abc(items)
        rank = {"A": 0, "B": 1, "C": 2}
        by_value = sorted(items, key=lambda p: p[1], reverse=True)
        classes = [rank[got[sku]] for sku, _ in by_value]
        assert classes == sorted(classes)


class TestMatchesTheShippedClassification:
    @pytest.mark.parametrize("seed", range(8))
    def test_same_letters_as_the_implementation_it_replaced(self, seed):
        from backend.inventory import service as svc

        rng = random.Random(seed)
        items = []
        for i in range(rng.randint(1, 120)):
            items.append({
                "sku": f"s{i}",
                "daily_demand": rng.choice([None, 0.0, rng.random() * 50]),
                "unit_cost": rng.choice([None, 0.0, rng.random() * 20]),
            })
        assert svc._classify_abc(items) == _shipped_classify_abc(items)

    def test_service_xyz_is_the_pure_one(self):
        from backend.inventory import service as svc

        for cv in (None, 0.0, 0.49, 0.5, 0.99, 1.0, 3.0):
            assert svc._classify_xyz(cv) == classify_xyz(cv)


class TestXyz:
    @pytest.mark.parametrize("cv,expected", [
        (None, "?"), (0.0, "X"), (0.4999, "X"), (0.5, "Y"), (0.9999, "Y"),
        (1.0, "Z"), (7.5, "Z"),
    ])
    def test_cutoffs(self, cv, expected):
        assert classify_xyz(cv) == expected


class TestSummary:
    def _rows(self):
        return [
            {"abc": "A", "value": 70.0, "service_level": 0.95, "owned": False},
            {"abc": "A", "value": 10.0, "service_level": 0.98, "owned": False},   # already there
            {"abc": "B", "value": 15.0, "service_level": 0.95, "owned": True},    # owned
            {"abc": "B", "value": 5.0, "service_level": 0.90, "owned": False},
        ]

    def test_always_three_classes_in_order(self):
        out = class_summary(self._rows())
        assert [c["abc"] for c in out] == ["A", "B", "C"]
        assert out[2]["skus"] == 0 and out[2]["current_service_level"] is None

    def test_value_share_and_suggestion(self):
        a, b, c = class_summary(self._rows())
        assert a["value_share"] == 0.8 and b["value_share"] == 0.2 and c["value_share"] == 0.0
        assert [a["suggested_service_level"], b["suggested_service_level"], c["suggested_service_level"]] == [
            SUGGESTED_SERVICE_LEVEL["A"], SUGGESTED_SERVICE_LEVEL["B"], SUGGESTED_SERVICE_LEVEL["C"]]

    def test_would_change_skips_owned_and_already_at_suggestion(self):
        a, b, _ = class_summary(self._rows())
        assert a["would_change"] == 1 and a["owned"] == 0     # the 0.98 one is already there
        assert b["would_change"] == 1 and b["owned"] == 1     # the owned one is left alone

    def test_no_rows_gives_zeros_not_a_crash(self):
        out = class_summary([])
        assert all(c["skus"] == 0 and c["value_share"] == 0.0 and c["would_change"] == 0 for c in out)

    def test_suggestions_are_valid_service_levels_and_ordered(self):
        s = SUGGESTED_SERVICE_LEVEL
        assert set(s) == set(abc_xyz.ABC_CLASSES)
        assert all(0.5 < v < 1.0 for v in s.values())
        assert s["A"] > s["B"] > s["C"]
