"""Test the integration_connections table and integration storage layer."""


def test_integration_connections_table_exists(client):
    """Verify integration_connections table with credentials column exists."""
    from backend.db.connection import query_one

    row = query_one(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name='integration_connections' AND column_name='credentials'"""
    )
    assert row is not None
