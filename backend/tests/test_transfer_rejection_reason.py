"""Why a transfer did NOT happen, and in units somebody can actually move.

Both defects were found by driving the app as a Professional tenant with two
warehouses (feature 5.4):

1. `_network_transfer_pass` only recorded a reason when a donor reached the LANE
   rules and lost there. Every earlier filter did a bare `continue`, so a row
   read "Pedir 174 al proveedor" while the sister warehouse visibly held 575
   units and `transfer_rejected_reason` came back null. The buyer could see the
   stock and not the reason — the one thing this feature owes them.

2. The suggested quantity skipped the MOQ rounding the purchase side gets for
   free, so the same table offered "Pedir 174" next to "Transferir 132.95".

These exercise `_network_transfer_pass` directly: it is pure, and the rows it
takes are the ones `_compute_inventory_status` builds.
"""

from backend.inventory.service import (
    TRANSFER_MIN_DONOR_COVERAGE_DAYS, _network_transfer_pass,
)


def _row(warehouse, *, stock, daily, signal, qty, reorder=0.0, moq=1.0):
    """One (sku, warehouse) row in the shape the network pass consumes."""
    return {
        "sku": "SKU-1", "warehouse": warehouse,
        "current_stock": stock, "daily_demand": daily,
        "reorder_point": reorder, "signal": signal,
        "recommended_qty": qty, "moq": moq,
        "recommended_action": None,
        "transfer_suggestion": None,
        "transfer_rejected_reason": None,
    }


def _lane(frm, to, days=1):
    return {(frm, to): {"lead_time_days": days, "cost_per_unit": 0.0, "fixed_cost": 0.0}}


class TestTheBuyerIsToldWhyNot:
    def test_a_donor_that_cannot_spare_stock_says_so(self):
        """575 units next door, and buying is still right — but say it.

        principal sells 51.2/day, so its 575 units are ~11 days of its own
        demand: under the 30-day floor, lending is refused. Measured on the real
        tenant, this row used to carry no reason at all.
        """
        needy = _row("Cartago", stock=25, daily=27.6, signal="PEDIR_YA", qty=174)
        donor = _row("principal", stock=575, daily=51.2, signal="OK", qty=0)
        _network_transfer_pass([needy, donor], "daily", lanes=_lane("principal", "Cartago"))

        assert needy["recommended_action"] == "order"
        assert needy["transfer_suggestion"] is None
        reason = needy["transfer_rejected_reason"]
        assert reason is not None, "the row said 'buy' and explained nothing"
        assert reason["reason_code"] == "transfer_donor_would_run_short"
        # The params carry what the buyer would otherwise have to work out by
        # opening the other warehouse: how much is there, and the floor.
        assert reason["params"]["from_warehouse"] == "principal"
        assert reason["params"]["donor_stock"] == 575
        assert reason["params"]["min_coverage_days"] == TRANSFER_MIN_DONOR_COVERAGE_DAYS

    def test_a_donation_that_would_not_close_the_gap_says_so(self):
        """A donor can spare some, but under 80% of the need — say how much.

        Moving part of it is a decision the buyer may still want to take by
        hand, and they cannot take it if the option is invisible.
        """
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=400)
        # 100 units, no demand of its own → it can lend all 100, which is 25%
        # of the 400 needed.
        donor = _row("principal", stock=100, daily=0, signal="SOBRESTOCK", qty=0)
        _network_transfer_pass([needy, donor], "daily", lanes=_lane("principal", "Cartago"))

        assert needy["recommended_action"] == "order"
        reason = needy["transfer_rejected_reason"]
        assert reason["reason_code"] == "transfer_donation_too_small"
        assert reason["params"]["qty"] == 100
        assert reason["params"]["need"] == 400

    def test_a_lane_verdict_outranks_a_pre_lane_filter(self):
        """Two donors: one refused early, one refused BY the lane.

        The lane verdict describes a warehouse that could actually have lent, so
        it is the more useful sentence and must win.
        """
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=100)
        cannot_spare = _row("Chepe", stock=90, daily=50, signal="OK", qty=0)
        could_lend = _row("principal", stock=5000, daily=1, signal="SOBRESTOCK", qty=0)
        # 40 transit days against a 5-day purchase: the lane loses on speed.
        needy["lead_time_days"] = 5
        _network_transfer_pass([needy, cannot_spare, could_lend], "daily", lanes=_lane("principal", "Cartago", days=40))
        assert needy["recommended_action"] == "order"
        assert needy["transfer_rejected_reason"]["reason_code"] == "transfer_too_slow"

    def test_an_accepted_transfer_carries_no_rejection(self):
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=100)
        donor = _row("principal", stock=5000, daily=1, signal="SOBRESTOCK", qty=0)
        needy["lead_time_days"] = 30
        _network_transfer_pass([needy, donor], "daily", lanes=_lane("principal", "Cartago", days=2))
        assert needy["recommended_action"] == "transfer"
        assert needy["transfer_rejected_reason"] is None


class TestTheQuantityIsMovable:
    def test_whole_units_by_default(self):
        """No fractional bottles. MOQ 1 → the transfer is an integer.

        The donor is deliberately clear of the coverage floor: this test is about
        the SHAPE of the quantity, and a donor sitting exactly on 30 days lands
        on a floating-point knife edge that has nothing to do with rounding.
        """
        # Numbers chosen so the raw donatable IS fractional (2400.5 − 78.9×30 =
        # 33.5): a fixture that lands on a round number cannot catch this.
        needy = _row("Cartago", stock=0, daily=10, signal="PEDIR_YA", qty=40)
        donor = _row("principal", stock=2400.5, daily=78.9, signal="SOBRESTOCK", qty=0)
        needy["lead_time_days"] = 5
        _network_transfer_pass([needy, donor], "daily", lanes=_lane("principal", "Cartago", days=2))
        qty = needy["transfer_suggestion"]["qty"]
        assert qty == int(qty), f"transfer of {qty} cannot be moved off a shelf"
        assert qty == 33, f"expected the 33.5 floored to 33, got {qty}"

    def test_the_sku_granularity_is_respected(self):
        """A SKU that moves in boxes of 12 gets a multiple of 12, rounded DOWN.

        Down, never up: the donor cannot lend more than it can spare.
        """
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=100, moq=12)
        donor = _row("principal", stock=5000, daily=1, signal="SOBRESTOCK", qty=0)
        needy["lead_time_days"] = 30
        _network_transfer_pass([needy, donor], "daily", lanes=_lane("principal", "Cartago", days=2))
        qty = needy["transfer_suggestion"]["qty"]
        assert qty % 12 == 0, f"{qty} is not a whole number of boxes"
        assert qty <= 100
