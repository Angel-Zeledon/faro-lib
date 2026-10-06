"""Supplier confirmation link: the rules that need no database.

Token strength, expiry, locking, status derivation, payload validation and the
promise that drives the overdue date. Each test names the way the feature could
hurt a customer if the rule were wrong (a forgeable link, a "confirmed" that
hides a changed date, a promise that moves purchasing without a person).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.inventory import po_confirmation_core as core

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 5)
REQUESTED = date(2026, 10, 20)

LINES = {"a": {"ordered_qty": 100.0}, "b": {"ordered_qty": 40.0}}


# ── Token ────────────────────────────────────────────────────────────────────

def test_token_is_long_random_and_url_safe():
    tokens = {core.new_token() for _ in range(200)}
    assert len(tokens) == 200
    for t in tokens:
        assert len(t) >= 43                     # 32 random bytes, base64url
        assert core.token_is_wellformed(t)


def test_hash_is_not_the_token_and_verifies_only_the_same_token():
    t = core.new_token()
    h = core.hash_token(t)
    assert h != t and len(h) == 64
    assert core.verify_token(t, h)
    assert not core.verify_token(core.new_token(), h)


@pytest.mark.parametrize("bad", [
    None, "", "short", "a" * 42, "a" * 129, "../../etc/passwd" + "a" * 40,
    "a" * 43 + "%", "a" * 43 + " ", 12345, b"bytes",
])
def test_malformed_tokens_never_verify(bad):
    assert not core.token_is_wellformed(bad)
    assert not core.verify_token(bad, core.hash_token("x" * 43))


def test_missing_stored_hash_never_verifies():
    assert not core.verify_token(core.new_token(), None)
    assert not core.verify_token(core.new_token(), "")


def test_client_hash_is_keyed_and_not_the_address():
    a = core.hash_client_value("203.0.113.9", "secret-1")
    assert a != "203.0.113.9" and "203" not in a
    assert a == core.hash_client_value("203.0.113.9", "secret-1")
    assert a != core.hash_client_value("203.0.113.9", "secret-2")


# ── Expiry ───────────────────────────────────────────────────────────────────

def test_expiry_is_21_days_when_arrival_is_soon():
    assert core.compute_expiry(NOW, date(2026, 10, 10)) == NOW + timedelta(days=21)
    assert core.compute_expiry(NOW, None) == NOW + timedelta(days=21)


def test_expiry_is_seven_days_after_a_far_arrival():
    arrival = date(2026, 12, 25)
    expiry = core.compute_expiry(NOW, arrival)
    assert expiry.date() == date(2027, 1, 1)
    assert expiry > NOW + timedelta(days=21)


def test_link_is_unusable_when_expired_or_revoked_at_the_boundary():
    expiry = NOW + timedelta(days=21)
    assert core.link_is_usable(expires_at=expiry, revoked_at=None, now=expiry - timedelta(seconds=1))
    assert not core.link_is_usable(expires_at=expiry, revoked_at=None, now=expiry)
    assert not core.link_is_usable(expires_at=expiry, revoked_at=NOW, now=NOW)


# ── Locking ──────────────────────────────────────────────────────────────────

def test_lock_follows_submission_and_reopening():
    assert not core.is_locked(None, None)
    assert core.is_locked(NOW, None)
    assert not core.is_locked(NOW, NOW + timedelta(hours=1))       # reopened after
    assert core.is_locked(NOW + timedelta(hours=2), NOW + timedelta(hours=1))  # answered again
    assert not core.is_locked(None, NOW)


# ── Status derivation ────────────────────────────────────────────────────────

def _status(**kw):
    base = dict(decision="confirm", ordered_qty=100.0, confirmed_qty=100.0,
                requested_date=REQUESTED, promised_date=REQUESTED)
    base.update(kw)
    return core.derive_line_status(**base)


def test_same_quantity_and_date_is_confirmed():
    assert _status() == "confirmed"


def test_another_date_or_quantity_is_a_change_never_a_confirmation():
    assert _status(promised_date=REQUESTED + timedelta(days=3)) == "changed"
    assert _status(confirmed_qty=90.0) == "changed"
    assert _status(confirmed_qty=100.5) == "changed"


def test_declined_wins_over_everything():
    assert _status(decision="decline", confirmed_qty=None, promised_date=None) == "declined"


def test_without_a_requested_date_only_the_quantity_decides():
    assert _status(requested_date=None, promised_date=date(2026, 11, 1)) == "confirmed"
    assert _status(requested_date=None, confirmed_qty=1.0) == "changed"


def test_overall_status():
    assert core.derive_overall([]) == "pending"
    assert core.derive_overall(["confirmed", "confirmed"]) == "confirmed"
    assert core.derive_overall(["confirmed", "changed"]) == "changed"
    assert core.derive_overall(["confirmed", "declined"]) == "changed"
    assert core.derive_overall(["declined", "declined"]) == "declined"


# ── Payload validation ───────────────────────────────────────────────────────

def _ok_line(line_id="a", **kw):
    row = {"line_id": line_id, "decision": "confirm",
           "confirmed_qty": LINES[line_id]["ordered_qty"],
           "promised_date": REQUESTED.isoformat()}
    row.update(kw)
    return row


def _validate(raw):
    return core.validate_submission(raw, LINES, today=TODAY, requested_date=REQUESTED)


def test_a_complete_valid_answer_is_cleaned_and_statused():
    out = _validate([_ok_line("a"), _ok_line("b", confirmed_qty=30, note="  two trucks ")])
    assert [r["po_item_id"] for r in out] == ["a", "b"]
    assert out[0]["status"] == "confirmed"
    assert out[1]["status"] == "changed" and out[1]["note"] == "two trucks"
    assert out[0]["promised_date"] == REQUESTED


def test_client_cannot_choose_the_status():
    out = _validate([_ok_line("a", status="confirmed", promised_date="2026-11-30"), _ok_line("b")])
    assert out[0]["status"] == "changed"


def test_decline_needs_no_numbers():
    out = _validate([_ok_line("a"), {"line_id": "b", "decision": "decline", "note": "out of stock"}])
    assert out[1]["status"] == "declined"
    assert out[1]["confirmed_qty"] is None and out[1]["promised_date"] is None


@pytest.mark.parametrize("raw,reason", [
    (None, "lines_missing"),
    ([], "lines_missing"),
    ("nope", "lines_missing"),
    ([_ok_line("a")], "lines_incomplete"),                                   # b unanswered
    ([_ok_line("a"), _ok_line("b"), _ok_line("a")], "line_duplicated"),
    ([_ok_line("a"), {**_ok_line("b"), "line_id": "other-po-line"}], "line_unknown"),
    (["a"], "line_invalid"),
])
def test_incomplete_or_foreign_answers_are_refused(raw, reason):
    with pytest.raises(core.SubmissionInvalid) as exc:
        _validate(raw)
    assert exc.value.code == reason


@pytest.mark.parametrize("patch,reason", [
    ({"confirmed_qty": 0}, "quantity_invalid"),
    ({"confirmed_qty": -5}, "quantity_invalid"),
    ({"confirmed_qty": float("nan")}, "quantity_invalid"),
    ({"confirmed_qty": float("inf")}, "quantity_invalid"),
    ({"confirmed_qty": 1e12}, "quantity_invalid"),
    ({"confirmed_qty": "100"}, "quantity_invalid"),
    ({"confirmed_qty": True}, "quantity_invalid"),
    ({"confirmed_qty": None}, "quantity_invalid"),
    ({"promised_date": "not-a-date"}, "date_invalid"),
    ({"promised_date": None}, "date_invalid"),
    ({"promised_date": "2020-01-01"}, "date_out_of_range"),
    ({"promised_date": "2206-01-01"}, "date_out_of_range"),
    ({"decision": "maybe"}, "decision_invalid"),
    ({"note": "x" * 501}, "note_too_long"),
    ({"note": 123}, "note_invalid"),
])
def test_bad_numbers_dates_and_notes_are_refused(patch, reason):
    with pytest.raises(core.SubmissionInvalid) as exc:
        _validate([_ok_line("a", **patch), _ok_line("b")])
    assert exc.value.code == reason


def test_too_many_lines_is_refused_before_anything_else():
    with pytest.raises(core.SubmissionInvalid) as exc:
        _validate([_ok_line("a")] * (core.MAX_LINES + 1))
    assert exc.value.code == "too_many_lines"


def test_note_keeps_markup_as_typed_and_drops_control_characters():
    # Stored verbatim: every surface that prints it escapes it. Rewriting what
    # the supplier typed would be a silent edit of their words.
    out = _validate([_ok_line("a", note="<script>alert(1)</script>\x00\x07ok"), _ok_line("b")])
    assert out[0]["note"] == "<script>alert(1)</script>ok"


def test_yesterday_is_tolerated_for_a_supplier_a_timezone_behind():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    out = _validate([_ok_line("a", promised_date=yesterday), _ok_line("b")])
    assert out[0]["promised_date"] == TODAY - timedelta(days=1)


# ── The promise that drives the overdue date ─────────────────────────────────

def test_no_accepted_promise_leaves_the_model_date_alone():
    model = date(2026, 10, 12)
    assert core.expected_arrival_with_promises(model, [None, None]) == (model, "model")
    assert core.expected_arrival_with_promises(model, []) == (model, "model")


def test_an_accepted_promise_replaces_the_model_date():
    model = date(2026, 10, 12)
    assert core.expected_arrival_with_promises(model, [date(2026, 11, 2)]) == (
        date(2026, 11, 2), "supplier_promise")


def test_a_late_accepted_promise_never_hides_an_unaccepted_line():
    model = date(2026, 10, 12)
    # One line promised for November, one never accepted: the order is expected
    # when its EARLIEST line is, so the unaccepted line (still on the model's
    # date) keeps the "did it arrive?" nudge alive. The date is the model's, and
    # the source says so.
    assert core.expected_arrival_with_promises(model, [date(2026, 11, 2), None]) == (model, "model")
    # Once every line has an accepted promise the order follows the promises.
    assert core.expected_arrival_with_promises(
        model, [date(2026, 11, 2), date(2026, 11, 9)]) == (date(2026, 11, 2), "supplier_promise")
    # An accepted EARLIER promise does pull the date forward.
    assert core.expected_arrival_with_promises(
        model, [date(2026, 10, 5), None]) == (date(2026, 10, 5), "supplier_promise")


def test_promise_slip_days():
    assert core.promise_slip_days(date(2026, 11, 2), date(2026, 11, 5)) == 3
    assert core.promise_slip_days(date(2026, 11, 2), date(2026, 11, 1)) == -1
