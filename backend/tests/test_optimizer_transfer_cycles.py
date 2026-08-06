"""The optimizer's transfer lines must be moves a warehouse worker can carry out.

Found by reading the purchasing panel of a three-warehouse Professional tenant.
Lanes the tenant has not configured default to free, and free movement makes
CIRCULATION cost the solver nothing, so it emitted:

    Mover 2126 uds de SKU-001 de Bodega Cartago a Bodega Heredia
    Mover 1518 uds de SKU-001 de Bodega Heredia a Bodega Cartago
    …
    Mover 831 uds de SKU-005 de Bodega Cartago a principal
    Mover 831 uds de SKU-005 de principal a Bodega Heredia
    Mover 831 uds de SKU-005 de Bodega Heredia a Bodega Cartago

The last three are a ring that returns every unit where it started. The first
two summed over buckets also exceeded the stock that exists. Both were printed
next to a "Convertir en OC" button.

`_net_transfer_moves` works from each warehouse's NET change instead, so a cycle
of ANY length collapses to nothing and the remaining moves keep the same net
effect per location.
"""

from backend.inventory.optimizer_service import _net_transfer_moves


def _totals(*rows):
    """(sku, from, to, qty) tuples → the per-pair totals the collapse consumes."""
    return {(sku, a, b): float(qty) for sku, a, b, qty in rows}


def _sends_and_receives(moves) -> set:
    """Warehouses that both ship and receive the same SKU — a cycle's signature."""
    by_sku: dict = {}
    for m in moves:
        entry = by_sku.setdefault(m["sku"], (set(), set()))
        entry[0].add(m["from_warehouse"])
        entry[1].add(m["to_warehouse"])
    return {f"{sku}:{w}" for sku, (out, inn) in by_sku.items() for w in out & inn}


class TestCyclesAreRemoved:
    def test_a_two_way_pair_becomes_one_move_of_the_difference(self):
        moves = _net_transfer_moves(_totals(
            ("SKU-1", "Cartago", "Heredia", 2126),
            ("SKU-1", "Heredia", "Cartago", 1518),
        ))
        assert moves == [{
            "sku": "SKU-1", "from_warehouse": "Cartago",
            "to_warehouse": "Heredia", "qty": 608,
        }]

    def test_a_three_way_ring_collapses_to_nothing(self):
        """Every unit ends where it began, so there is nothing to move."""
        moves = _net_transfer_moves(_totals(
            ("SKU-5", "Cartago", "principal", 831),
            ("SKU-5", "principal", "Heredia", 831),
            ("SKU-5", "Heredia", "Cartago", 831),
        ))
        assert moves == []

    def test_no_warehouse_ships_and_receives_the_same_sku(self):
        """The invariant that proves the output is cycle-free at any length."""
        moves = _net_transfer_moves(_totals(
            ("SKU-1", "A", "B", 500), ("SKU-1", "B", "C", 400),
            ("SKU-1", "C", "A", 300), ("SKU-1", "B", "A", 100),
            ("SKU-2", "A", "B", 50),  ("SKU-2", "B", "A", 50),
        ))
        assert _sends_and_receives(moves) == set()


class TestRealMovesSurvive:
    def test_one_surplus_feeding_two_shortfalls_is_untouched(self):
        moves = _net_transfer_moves(_totals(
            ("SKU-2", "principal", "Cartago", 100),
            ("SKU-2", "principal", "Heredia", 50),
        ))
        assert sorted(m["to_warehouse"] for m in moves) == ["Cartago", "Heredia"]
        assert sum(m["qty"] for m in moves) == 150
        assert {m["from_warehouse"] for m in moves} == {"principal"}

    def test_the_net_effect_per_warehouse_is_preserved(self):
        """What each location ends up with must not change — only the routing."""
        totals = _totals(
            ("SKU-9", "A", "B", 300), ("SKU-9", "B", "C", 120),
            ("SKU-9", "A", "C", 40),
        )
        expected: dict = {}
        for (_sku, a, b), qty in totals.items():
            expected[a] = expected.get(a, 0.0) - qty
            expected[b] = expected.get(b, 0.0) + qty

        actual: dict = {}
        for m in _net_transfer_moves(totals):
            actual[m["from_warehouse"]] = actual.get(m["from_warehouse"], 0) - m["qty"]
            actual[m["to_warehouse"]] = actual.get(m["to_warehouse"], 0) + m["qty"]

        for w, net in expected.items():
            assert abs(actual.get(w, 0) - net) <= 1, (
                f"{w} net changed: planned {net}, reported {actual.get(w, 0)}")

    def test_nothing_is_invented_by_rounding(self):
        """Fractions round DOWN on both sides: the plan can never grow."""
        moves = _net_transfer_moves(_totals(("SKU-3", "A", "B", 10.9)))
        assert [m["qty"] for m in moves] == [10]

    def test_an_empty_plan_stays_empty(self):
        assert _net_transfer_moves({}) == []
