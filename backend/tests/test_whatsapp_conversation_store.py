"""Conversation state persistence + idempotency + 24h pruning."""
from backend.db.connection import execute, query_one
from backend.whatsapp import conversation_store as cs


def test_load_empty_when_no_row(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    state = cs.load(tid, uid)
    assert state["history"] == []
    assert state["pending_action"] is None
    assert state["exists"] is False


def test_save_then_load_roundtrip(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+573001234567",
            history=[{"role": "user", "content": "hola"}],
            pending_action={"type": "approve_po", "po_log_id": "po1"},
            last_message_sid="SM1")
    state = cs.load(tid, uid)
    assert state["history"] == [{"role": "user", "content": "hola"}]
    assert state["pending_action"]["type"] == "approve_po"
    assert state["last_message_sid"] == "SM1"
    assert state["exists"] is True


def test_save_upserts_single_row(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM1")
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM2")
    row = query_one(
        "SELECT COUNT(*) AS n FROM whatsapp_conversations WHERE tenant_id = %s AND user_id = %s",
        (tid, uid),
    )
    assert row["n"] == 1


def test_history_trimmed_to_max_turns(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    long_history = [{"role": "user", "content": str(i)} for i in range(50)]
    cs.save(tid, uid, "+57300", history=long_history, pending_action=None, last_message_sid="SMx")
    state = cs.load(tid, uid)
    assert len(state["history"]) == cs.MAX_TURNS
    assert state["history"][-1]["content"] == "49"


def test_is_duplicate_matches_last_sid(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM-DUP")
    assert cs.is_duplicate(tid, uid, "SM-DUP") is True
    assert cs.is_duplicate(tid, uid, "SM-OTHER") is False


def test_stale_row_pruned_on_read(client, registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300",
            history=[{"role": "user", "content": "old"}],
            pending_action={"type": "approve_po"}, last_message_sid="SM-OLD")
    # Age the row past the 24h window.
    execute(
        "UPDATE whatsapp_conversations SET updated_at = NOW() - INTERVAL '25 hours' "
        "WHERE tenant_id = %s AND user_id = %s",
        (tid, uid),
    )
    state = cs.load(tid, uid)
    assert state["history"] == []
    assert state["pending_action"] is None
    # A stale row must not make an old MessageSid look already-processed.
    assert cs.is_duplicate(tid, uid, "SM-OLD") is False
