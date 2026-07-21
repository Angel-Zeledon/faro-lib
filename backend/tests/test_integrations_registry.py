"""Test the accounting-integrations provider registry."""
import pytest

from backend.integrations.alegra import AlegraProvider
from backend.integrations.registry import SUPPORTED_PROVIDERS, get_provider
from backend.integrations.siigo import SiigoProvider


def test_supported_providers_lists_credential_fields():
    assert SUPPORTED_PROVIDERS["alegra"]["fields"] == ["email", "token"]
    assert SUPPORTED_PROVIDERS["siigo"]["fields"] == ["partner_id", "username", "access_key"]


def test_get_provider_returns_alegra_instance():
    provider = get_provider("alegra", {"email": "a@b.com", "token": "T"})
    assert isinstance(provider, AlegraProvider)
    assert provider.credentials == {"email": "a@b.com", "token": "T"}


def test_get_provider_returns_siigo_instance():
    provider = get_provider("siigo", {"partner_id": "p", "username": "u", "access_key": "k"})
    assert isinstance(provider, SiigoProvider)
    assert provider.credentials == {"partner_id": "p", "username": "u", "access_key": "k"}


def test_get_provider_raises_for_unknown_provider():
    with pytest.raises(ValueError):
        get_provider("quickbooks", {})
