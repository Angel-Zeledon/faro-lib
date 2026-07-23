"""The WhatsApp bot migrations must exist on the live schema after startup."""
from backend.db.connection import query_one


def test_users_has_whatsapp_verified_at(client):
    row = query_one(
        """SELECT 1 AS ok FROM information_schema.columns
           WHERE table_name = 'users' AND column_name = 'whatsapp_verified_at'"""
    )
    assert row is not None, "users.whatsapp_verified_at missing"


def test_whatsapp_number_partial_unique_index_exists(client):
    row = query_one(
        "SELECT 1 AS ok FROM pg_class WHERE relname = 'users_whatsapp_number_uniq'"
    )
    assert row is not None, "partial unique index on users.whatsapp_number missing"


def test_whatsapp_conversations_table_exists(client):
    row = query_one(
        """SELECT 1 AS ok FROM information_schema.tables
           WHERE table_name = 'whatsapp_conversations'"""
    )
    assert row is not None, "whatsapp_conversations table missing"


def test_whatsapp_conversations_has_expected_columns(client):
    rows = query_one(
        """SELECT array_agg(column_name::text) AS cols
           FROM information_schema.columns
           WHERE table_name = 'whatsapp_conversations'"""
    )
    cols = set(rows["cols"])
    assert {"id", "tenant_id", "user_id", "phone", "history",
            "pending_action", "last_message_sid", "updated_at"} <= cols
