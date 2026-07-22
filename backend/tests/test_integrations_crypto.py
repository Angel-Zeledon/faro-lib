import pytest


def test_encrypt_roundtrip_and_ciphertext(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    from backend.integrations import crypto
    creds = {"email": "a@b.com", "token": "SECRET-123"}
    enc = crypto.encrypt_credentials(creds)
    assert "SECRET-123" not in enc          # not plaintext
    assert crypto.decrypt_credentials(enc) == creds


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    from backend.integrations import crypto
    assert crypto.integrations_enabled() is False
    with pytest.raises(RuntimeError):
        crypto.encrypt_credentials({"x": "y"})
