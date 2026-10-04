"""
A supplier's email is where purchase orders are sent. It was any string at all.

Measured in the browser: `no-es-un-email` saved cleanly, appeared in the EMAIL
column of the suppliers table exactly like a real address, and stayed wrong
until the day an order failed to arrive. `POST /inventory/po/{id}/send` does
report the failure (`skipped: delivery_failed`, and the attempt is recorded), so
nothing is lost in silence — but the user finds out AFTER the moment the order
was supposed to have gone out, which is the wrong moment to learn that the
address was never routable.

This is deliberately a SHAPE check, not a deliverability check. The only thing
knowable at save time is whether an address could ever be routed at all; whether
anybody reads it is the send path's business, and the send path already reports
that honestly.
"""

import uuid

import pytest

from backend.db.connection import query_one


def _name():
    return f"Proveedor-{uuid.uuid4().hex[:8]}"


def _body(resp):
    return resp.json().get("data") or {}


class TestAnUnroutableAddressIsRefused:

    @pytest.mark.parametrize("bad", [
        "no-es-un-email",              # the one measured in the browser
        "ventas arroba empresa.com",
        "a@b",                          # no dot: not routable on the public net
        "ventas@",
        "@empresa.com",
        "ventas @empresa.com",
        "ventas@empresa .com",
        "ventas@empresa.com, otro@x.com",   # two addresses in one field
        "ventas@empresa.com; otro@x.com",
    ])
    def test_it_is_rejected_and_nothing_is_stored(self, client, auth_headers,
                                                  test_tenant, bad):
        name = _name()
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "email": bad}, headers=auth_headers)
        assert resp.status_code == 422, (
            f"{bad!r} cannot receive mail but the API accepted it"
        )
        assert query_one(
            "SELECT id FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        ) is None, "a rejected request must not leave a supplier behind"

    def test_the_error_names_the_field_so_the_form_can_point_at_it(
        self, client, auth_headers,
    ):
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": _name(), "email": "roto"},
                           headers=auth_headers)
        detail = resp.json()["detail"]
        assert any("email" in (d.get("loc") or []) for d in detail), detail

    def test_a_patch_cannot_smuggle_one_in_either(self, client, auth_headers,
                                                  test_tenant):
        """Create is not the only door: the edit form posts a PATCH."""
        name = _name()
        created = _body(client.post("/api/v1/inventory/suppliers",
                                    json={"name": name, "email": "ventas@empresa.com"},
                                    headers=auth_headers))
        resp = client.patch(f"/api/v1/inventory/suppliers/{created['id']}",
                            json={"email": "roto"}, headers=auth_headers)
        assert resp.status_code == 422
        assert query_one(
            "SELECT email FROM suppliers WHERE id = %s", (created["id"],),
        )["email"] == "ventas@empresa.com", "the good address must survive"


class TestARoutableAddressStillWorks:

    @pytest.mark.parametrize("good", [
        "ventas@empresa.com",
        "VENTAS@Empresa.CR",           # case is not our business
        "juan.perez+oc@sub.empresa.co.cr",
        "compras_2026@empresa-latam.com",
    ])
    def test_it_is_accepted(self, client, auth_headers, test_tenant, good):
        name = _name()
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "email": good}, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        assert query_one(
            "SELECT email FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        )["email"] == good.strip()

    def test_surrounding_whitespace_is_trimmed_not_rejected(
        self, client, auth_headers, test_tenant,
    ):
        """Pasting an address out of a contact card brings spaces with it."""
        name = _name()
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "email": "  ventas@empresa.com  "},
                           headers=auth_headers)
        assert resp.status_code == 201
        assert query_one(
            "SELECT email FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        )["email"] == "ventas@empresa.com"

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_no_email_at_all_is_allowed(self, client, auth_headers,
                                        test_tenant, blank):
        """Not every supplier is contacted by email — some are WhatsApp only.
        Blank must stay NULL rather than become an empty string that later
        reads as "has an address"."""
        name = _name()
        payload = {"name": name}
        if blank is not None:
            payload["email"] = blank
        resp = client.post("/api/v1/inventory/suppliers", json=payload,
                           headers=auth_headers)
        assert resp.status_code == 201, resp.text
        assert query_one(
            "SELECT email FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        )["email"] is None


class TestPermissions:
    """Every mutating endpoint needs the pair — see CLAUDE.md."""

    def test_viewer_cannot_create_a_supplier(self, client, viewer_headers,
                                             test_tenant):
        name = _name()
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "email": "ventas@empresa.com"},
                           headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one(
            "SELECT id FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        ) is None

    def test_analyst_can(self, client, analyst_headers, test_tenant):
        name = _name()
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "email": "ventas@empresa.com"},
                           headers=analyst_headers)
        assert resp.status_code == 201
        assert query_one(
            "SELECT id FROM suppliers WHERE tenant_id = %s AND name = %s",
            (test_tenant["id"], name),
        ) is not None
