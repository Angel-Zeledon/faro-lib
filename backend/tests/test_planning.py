"""Multi-period Phase B: tenant settings, planning service, resolver, API."""

from backend.tenants import service as tenant_svc


class TestTenantSettings:
    def test_settings_default_empty(self, client, test_tenant):
        assert tenant_svc.get_settings(test_tenant["id"]) == {}

    def test_update_settings_merges_and_persists(self, client, test_tenant):
        tid = test_tenant["id"]
        tenant_svc.update_settings(tid, {"planning": {"period": "weekly", "horizon": 6}})
        tenant_svc.update_settings(tid, {"other": 1})
        got = tenant_svc.get_settings(tid)
        assert got["planning"] == {"period": "weekly", "horizon": 6}  # not clobbered
        assert got["other"] == 1

    def test_get_settings_unknown_tenant_is_empty(self, client):
        assert tenant_svc.get_settings("ten_does_not_exist") == {}
