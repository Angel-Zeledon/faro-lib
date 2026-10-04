"""A service level the customer chose must be the one the cushion is built for.

`_safety_stock` looked its z up in a four-entry dict with a default:

    z = _Z.get(service_level, 1.645)

Every level that was not 0.90 / 0.95 / 0.97 / 0.99 therefore got the z of 95%,
silently. That is not a rounding error, it is the wrong answer to the question
the user asked — and the product invites the question: the API accepts any
level in [0.5, 0.999] (`inventory.py` Query bounds) and the defaults cascade
lets a tenant store one per SKU, per supplier or per category.

The shape of the failure is the reason it survived: a buyer who raises a
critical SKU to 98% gets a cushion sized for 95%, the number on screen looks
perfectly reasonable, and nothing anywhere says otherwise. It would surface as
"we stock out more than our service level says", months later, against the one
setting they deliberately changed.
"""

import math

import pytest

from backend.inventory.service import _safety_stock, _z_for

# Published values of the standard normal quantile. Independent of the
# implementation on purpose: a test that computed them the same way the code
# does would pass no matter what either of them said.
KNOWN_Z = {
    0.50: 0.0000,
    0.75: 0.6745,
    0.90: 1.2816,
    0.95: 1.6449,
    0.96: 1.7507,
    0.97: 1.8808,
    0.98: 2.0537,
    0.99: 2.3263,
    0.995: 2.5758,
    0.999: 3.0902,
}


@pytest.mark.parametrize("level,expected", sorted(KNOWN_Z.items()))
def test_z_matches_the_normal_quantile(level, expected):
    """Within 5e-4 — the four legacy points are kept at their historical
    3-decimal values so a tenant's numbers do not move under them, and
    everything else is computed."""
    assert abs(_z_for(level) - expected) < 5e-4, (
        f"service level {level} resolved to z={_z_for(level):.4f}, "
        f"expected ≈{expected:.4f}"
    )


def test_the_four_historical_values_are_unchanged():
    """The product shipped these exact numbers. Recomputing them would move
    every existing tenant's safety stock by a hair for no reason."""
    assert _z_for(0.90) == 1.282
    assert _z_for(0.95) == 1.645
    assert _z_for(0.97) == 1.881
    assert _z_for(0.99) == 2.326


def test_an_unlisted_level_is_no_longer_the_z_of_95_percent():
    """The defect itself. 0.98 used to be 1.645."""
    assert _z_for(0.98) != 1.645
    assert _z_for(0.98) > _z_for(0.95), "a higher service level must cushion more"
    assert _z_for(0.96) > _z_for(0.95)
    assert _z_for(0.995) > _z_for(0.99)


def test_z_is_monotonic_across_the_range_the_api_accepts():
    """Whatever the approximation does, a customer asking for more protection
    must never receive less."""
    levels = [0.5 + i * 0.001 for i in range(500)]  # 0.500 … 0.999
    zs = [_z_for(x) for x in levels]
    assert all(b >= a for a, b in zip(zs, zs[1:])), "z is not monotonic"


def test_safety_stock_grows_with_the_service_level():
    """End to end through the function the recommendation actually calls."""
    common = dict(avg_std=10.0, lead_time=9.0)
    at_95 = _safety_stock(service_level=0.95, **common)
    at_98 = _safety_stock(service_level=0.98, **common)
    assert at_98 > at_95
    # z(0.98)·σ·√L with σ=10, L=9 → 3·10·2.0537 ≈ 61.6
    assert math.isclose(at_98, 3 * 10.0 * 2.0537, rel_tol=1e-3)


def test_an_out_of_range_level_is_clamped_rather_than_raising():
    """This runs inside the 08:00 alert loop. An exception there costs a tenant
    their whole digest, so a nonsensical level is clamped and the cushion is
    still a number."""
    assert _z_for(0.0) == pytest.approx(_z_for(0.5), abs=1e-9)
    assert _z_for(-1.0) == pytest.approx(_z_for(0.5), abs=1e-9)
    assert _z_for(1.0) > 4.0  # clamped just below 1, a very large but finite z
    assert math.isfinite(_z_for(1.5))
