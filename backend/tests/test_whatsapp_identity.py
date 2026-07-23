"""Identity resolution + number linking/verification for the WhatsApp bot."""
import pytest

from backend.db.connection import query_one, execute
from backend.whatsapp import identity


def test_normalize_phone_strips_whatsapp_prefix():
    assert identity.normalize_phone("whatsapp:+573001234567") == "+573001234567"
    assert identity.normalize_phone("  +573001234567 ") == "+573001234567"


def test_is_e164():
    assert identity.is_e164("+573001234567")
    assert not identity.is_e164("3001234567")
    assert not identity.is_e164("+0123")


def test_resolve_sender_unknown_returns_none(client):
    assert identity.resolve_sender("+59990000000") is None


def test_resolve_sender_unverified_returns_none(registered_user):
    uid = registered_user["user"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL WHERE id = %s",
        ("+573001112222", uid),
    )
    assert identity.resolve_sender("+573001112222") is None


def test_resolve_sender_verified_returns_context(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
        ("+573002223333", uid),
    )
    ctx = identity.resolve_sender("+573002223333")
    assert ctx is not None
    assert ctx["user_id"] == uid
    assert ctx["tenant_id"] == tid
    assert ctx["role"] == "admin"


def test_start_and_confirm_verification_sets_verified_at(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    code = identity.start_verification(tid, uid, "+573004445555")
    # Before confirmation the number is present but NOT usable.
    assert identity.resolve_sender("+573004445555") is None
    assert identity.confirm_verification(tid, uid, code) is True
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is not None
    assert identity.resolve_sender("+573004445555") is not None


def test_confirm_verification_wrong_code_fails(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    identity.start_verification(tid, uid, "+573005556666")
    assert identity.confirm_verification(tid, uid, "000000") is False
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is None


def test_start_verification_rejects_number_verified_on_another_user(registered_user, analyst_user):
    tid = registered_user["tenant"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
        ("+573007778888", registered_user["user"]["id"]),
    )
    with pytest.raises(ValueError):
        identity.start_verification(tid, analyst_user["user"]["id"], "+573007778888")


def test_start_verification_rejects_bad_format(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    with pytest.raises(ValueError):
        identity.start_verification(tid, uid, "3001234567")
