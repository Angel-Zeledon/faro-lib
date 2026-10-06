"""The pure rules of approval chains (`cost_center_chain_core`). The Rust copy
(`backend-rs/src/chain.rs`) is held to the same answers by
`tests/contract/chain_fixtures.json`, written from this module."""

import pytest

from backend.inventory import cost_center_chain_core as core

ADMIN = {"kind": "role", "role": "admin"}
ANALYST = {"kind": "role", "role": "analyst"}


def _centers():
    return [
        {"id": "root", "parent_id": None, "active": True},
        {"id": "ops", "parent_id": "root", "active": True},
        {"id": "ops-north", "parent_id": "ops", "active": True},
        {"id": "retired", "parent_id": "root", "active": False},
    ]


def _chain(cid, center, bands, active=True):
    return {"id": cid, "cost_center_id": center, "active": active,
            "bands": [{"min_amount": m, "levels": lv} for m, lv in bands]}


DEFAULT = _chain("c-default", None, [(1000, [ANALYST]), (10000, [ANALYST, ADMIN])])
OPS = _chain("c-ops", "ops", [(500, [ADMIN])])


def test_no_chain_at_all_changes_nothing():
    r = core.resolve([], _centers(), "ops", 99999.0)
    assert r["state"] == core.NOT_REQUIRED and r["reason"] is None
    # an inactive chain counts as none
    r = core.resolve([_chain("x", None, [(1, [ADMIN])], active=False)], _centers(), None, None)
    assert r["state"] == core.NOT_REQUIRED


def test_band_selection_is_inclusive_with_the_shared_tolerance():
    chains = [DEFAULT]
    assert core.resolve(chains, _centers(), None, 999.0)["state"] == core.NOT_REQUIRED
    assert core.resolve(chains, _centers(), None, 999.996)["levels"] == [ANALYST]   # within EPS
    assert core.resolve(chains, _centers(), None, 1000.0)["levels"] == [ANALYST]
    assert core.resolve(chains, _centers(), None, 9999.0)["levels"] == [ANALYST]
    assert core.resolve(chains, _centers(), None, 10000.0)["levels"] == [ANALYST, ADMIN]


def test_a_center_uses_its_own_chain_then_the_nearest_ancestors_then_the_default():
    chains = [DEFAULT, OPS]
    assert core.resolve(chains, _centers(), "ops", 600.0)["chain_id"] == "c-ops"
    assert core.resolve(chains, _centers(), "ops-north", 600.0)["chain_id"] == "c-ops"
    # root has no chain of its own: the default covers it
    assert core.resolve(chains, _centers(), "root", 2000.0)["chain_id"] == "c-default"


def test_fail_closed_reasons():
    centers = _centers()
    # no center and no default chain
    r = core.resolve([OPS], centers, None, 5000.0)
    assert (r["state"], r["reason"]) == (core.UNRESOLVED, "no_cost_center")
    # a center nobody covers and no default
    r = core.resolve([OPS], centers, "root", 5000.0)
    assert (r["state"], r["reason"]) == (core.UNRESOLVED, "no_chain")
    # unknown and inactive centers
    for cid in ("ghost", "retired"):
        r = core.resolve([DEFAULT], centers, cid, 5000.0)
        assert (r["state"], r["reason"]) == (core.UNRESOLVED, "cost_center_invalid")
    # unknown value
    r = core.resolve([DEFAULT], centers, "ops", None)
    assert (r["state"], r["reason"]) == (core.UNRESOLVED, "amount_unknown")
    # even a value below every band is unresolved when the center is
    r = core.resolve([OPS], centers, None, 1.0)
    assert r["state"] == core.UNRESOLVED


@pytest.mark.parametrize("bands", [
    [],                                            # no bands
    [(100, [])],                                   # a band with no levels
    [(100, [{"kind": "role", "role": "viewer"}])],  # a role that cannot approve
    [(100, [{"kind": "users", "user_ids": []}])],
    [(100, [{"kind": "users", "user_ids": [1]}])],
    [(100, [{"kind": "other"}])],
    [(100, [ADMIN] * 6)],                          # too many levels
    [(100, [ADMIN]), (100, [ANALYST])],            # duplicate band
    [(-1, [ADMIN])],
    [(float("nan"), [ADMIN])],
])
def test_a_malformed_chain_is_never_trusted(bands):
    r = core.resolve([_chain("bad", None, bands)], _centers(), None, 5000.0)
    assert (r["state"], r["reason"]) == (core.UNRESOLVED, "chain_invalid")


def test_one_broken_band_breaks_the_whole_chain():
    chain = _chain("c", None, [(100, [ADMIN]), (200, [{"kind": "role", "role": "root"}])])
    r = core.resolve([chain], _centers(), None, 150.0)
    assert r["reason"] == "chain_invalid"


def test_a_cycle_in_the_center_tree_fails_closed():
    centers = [{"id": "a", "parent_id": "b", "active": True},
               {"id": "b", "parent_id": "a", "active": True}]
    r = core.resolve([_chain("c", "zzz", [(1, [ADMIN])])], centers, "a", 5.0)
    assert (r["state"], r["reason"]) == (core.UNRESOLVED, "cost_center_invalid")


def test_escalation_takes_the_top_band_even_below_every_band():
    r = core.resolve([DEFAULT], _centers(), None, 5.0, escalate=True)
    assert r["state"] == core.REQUIRED and r["levels"] == [ANALYST, ADMIN] and r["escalated"]
    # but never rescues an unresolved order
    r = core.resolve([OPS], _centers(), None, 5.0, escalate=True)
    assert r["state"] == core.UNRESOLVED


def test_fingerprint_changes_with_chain_band_and_levels():
    a = core.resolve([DEFAULT], _centers(), None, 2000.0)["fingerprint"]
    b = core.resolve([DEFAULT], _centers(), None, 20000.0)["fingerprint"]
    other = _chain("c-default", None, [(1000, [ADMIN]), (10000, [ANALYST, ADMIN])])
    c = core.resolve([other], _centers(), None, 2000.0)["fingerprint"]
    assert len({a, b, c}) == 3
    assert a == "c-default|1000.00|role:analyst"
    # named people are order-insensitive
    lv = lambda ids: {"kind": "users", "user_ids": ids}
    f1 = core.resolve([_chain("n", None, [(1, [lv(["b", "a"])])])], [], None, 5.0)["fingerprint"]
    f2 = core.resolve([_chain("n", None, [(1, [lv(["a", "b", "a"])])])], [], None, 5.0)["fingerprint"]
    assert f1 == f2 == "n|1.00|users:a,b"


def test_level_eligibility():
    admin = {"id": "u1", "role": "admin", "status": "active"}
    analyst = {"id": "u2", "role": "analyst", "status": "active"}
    viewer = {"id": "u3", "role": "viewer", "status": "active"}
    gone = {"id": "u4", "role": "admin", "status": "inactive"}
    assert core.level_eligible(ANALYST, admin) and core.level_eligible(ANALYST, analyst)
    assert core.level_eligible(ADMIN, admin) and not core.level_eligible(ADMIN, analyst)
    assert not core.level_eligible(ANALYST, viewer) and not core.level_eligible(ADMIN, gone)
    named = {"kind": "users", "user_ids": ["u2", "u3", "u4"]}
    assert core.level_eligible(named, analyst)
    assert not core.level_eligible(named, viewer)          # named, but cannot act
    assert not core.level_eligible(named, gone)
    assert not core.level_eligible(named, admin)           # not named


def test_descendants_include_self_and_are_cycle_safe():
    assert core.descendants(_centers(), "ops") == ["ops", "ops-north"]
    assert core.descendants(_centers(), "root") == ["ops", "ops-north", "retired", "root"]
    cyc = [{"id": "a", "parent_id": "b", "active": True}, {"id": "b", "parent_id": "a", "active": True}]
    assert core.descendants(cyc, "a") == ["a", "b"]


def test_staffing_needs_a_different_person_per_level_and_never_the_requester():
    users = [{"id": "r", "role": "admin", "status": "active"},
             {"id": "x", "role": "admin", "status": "active"}]
    # two admin levels, only ONE admin besides the requester: level 2 cannot be filled
    assert core.staffing([ADMIN, ADMIN], users, "r") == 2
    users.append({"id": "y", "role": "admin", "status": "active"})
    assert core.staffing([ADMIN, ADMIN], users, "r") is None
    # matching, not greedy: a named level that only x fits must get x
    named_x = {"kind": "users", "user_ids": ["x"]}
    assert core.staffing([ANALYST, named_x], users, "r") is None
    assert core.staffing([ADMIN], [{"id": "r", "role": "admin", "status": "active"}], "r") == 1
