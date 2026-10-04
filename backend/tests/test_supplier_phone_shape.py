"""A supplier's phone / WhatsApp number is normalized, and junk is refused.

The web form now sends E.164 from a country picker, but API clients and rows
saved before the picker existed send `+506 8888-7777`, `00506 8888 7777` or a
bare local number. Normalizing makes them deliverable; refusing letters keeps a
note typed into the wrong box from being filed as a number.
"""

import uuid

import pytest

from backend.db.connection import query_one


def _name():
    return f"Proveedor-{uuid.uuid4().hex[:8]}"


def _stored(tenant_id, name):
    return query_one(
        "SELECT phone, whatsapp FROM suppliers WHERE tenant_id = %s AND name = %s",
        (tenant_id, name),
    )


@pytest.mark.parametrize("sent,stored", [
    ("+50688887777", "+50688887777"),
    ("+506 8888-7777", "+50688887777"),
    ("00506 (8888) 7777", "+50688887777"),
    ("8888-7777", "88887777"),          # legacy local number: kept, not rejected
])
def test_numbers_are_normalized_before_they_are_stored(
        client, auth_headers, test_tenant, sent, stored):
    name = _name()
    resp = client.post("/api/v1/inventory/suppliers",
                       json={"name": name, "phone": sent, "whatsapp": sent},
                       headers=auth_headers)
    assert resp.status_code in (200, 201), resp.text
    row = _stored(test_tenant["id"], name)
    assert row["phone"] == stored
    assert row["whatsapp"] == stored


@pytest.mark.parametrize("bad", ["llamar a Juan", "+0123456789", "+5068", "12ab34"])
def test_junk_is_rejected_and_nothing_is_stored(client, auth_headers, test_tenant, bad):
    name = _name()
    resp = client.post("/api/v1/inventory/suppliers",
                       json={"name": name, "whatsapp": bad}, headers=auth_headers)
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert errors[0]["type"] == "supplier_phone_shape"
    assert errors[0]["loc"][-1] == "whatsapp"
    assert _stored(test_tenant["id"], name) is None


def test_blank_stays_empty(client, auth_headers, test_tenant):
    name = _name()
    resp = client.post("/api/v1/inventory/suppliers",
                       json={"name": name, "phone": "  ", "whatsapp": ""},
                       headers=auth_headers)
    assert resp.status_code in (200, 201), resp.text
    row = _stored(test_tenant["id"], name)
    assert row["phone"] is None and row["whatsapp"] is None


def test_a_viewer_cannot_create_a_supplier(client, viewer_headers, test_tenant):
    name = _name()
    resp = client.post("/api/v1/inventory/suppliers",
                       json={"name": name, "whatsapp": "+50688887777"},
                       headers=viewer_headers)
    assert resp.status_code == 403
    assert _stored(test_tenant["id"], name) is None
