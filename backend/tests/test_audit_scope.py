"""The audit trail is company-wide: an admin limited to some warehouses is
refused it, instead of reading other warehouses' receptions and transfers
through it (`record_event` names the warehouse in each row's details)."""

import json
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query
from backend.users import service as user_svc


@pytest.fixture
def scoped_admin_headers(test_tenant):
    tid = test_tenant["id"]
    execute("INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, 'Norte', false)", (tid,))
    wh = query("SELECT id FROM warehouses WHERE tenant_id = %s AND name = 'Norte'", (tid,))[0]["id"]
    email = f"admin-{uuid4().hex[:8]}@example.com"
    u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role="admin")
    user_svc.mark_verified(tid, u["id"])
    execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s", (json.dumps([wh]), u["id"]))
    tok = create_access_token(u["id"], tid, "admin", email_verified=True)
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.parametrize("path", ["/api/v1/audit", "/api/v1/audit/export", "/api/v1/audit/filters"])
def test_scoped_admin_is_refused_the_trail(client, scoped_admin_headers, path):
    r = client.get(path, headers=scoped_admin_headers)
    assert r.status_code == 403, r.text
    assert r.json()["error_code"] == "warehouse_scope_company_totals"


@pytest.mark.parametrize("path", ["/api/v1/audit", "/api/v1/audit/export", "/api/v1/audit/filters"])
def test_company_wide_admin_still_reads_it(client, auth_headers, path):
    r = client.get(path, headers=auth_headers)
    assert r.status_code == 200, r.text
