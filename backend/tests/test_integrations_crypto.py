import pytest


def test_encrypt_roundtrip_and_ciphertext(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    from backend.integrations import crypto
    creds = {"email": "a@b.com", "token": "SECRET-123"}
    enc = crypto.encrypt_credentials(creds)
    assert "SECRET-123" not in enc          # not plaintext
    assert crypto.decrypt_credentials(enc) == creds


def test_an_empty_variable_provisions_a_key_instead_of_disabling(monkeypatch, tmp_path):
    """A fresh install has no key, and used to be unable to store ANY secret
    because of it — so the configuration panel refused every field a buyer
    wanted to fill on day one. An empty variable now means "make me one",
    written beside the datasets in storage/."""
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    monkeypatch.setattr("backend.config.settings.storage_path", tmp_path)
    from backend.integrations import crypto
    crypto.reset_cache()

    assert crypto.integrations_enabled() is True
    assert crypto.key_source() == "generated"
    assert (tmp_path / crypto.KEY_FILENAME).exists()

    # It round-trips, and it is stable across calls — a key that changed per
    # call would encrypt credentials nothing could ever read back.
    enc = crypto.encrypt_credentials({"x": "y"})
    assert crypto.decrypt_credentials(enc) == {"x": "y"}
    assert crypto.active_key() == (tmp_path / crypto.KEY_FILENAME).read_text().strip()


def test_the_environment_key_always_wins(monkeypatch, tmp_path):
    """A deployment that named its key does not get a second one, and the file
    is not even consulted."""
    from cryptography.fernet import Fernet
    from backend.integrations import crypto

    named = Fernet.generate_key().decode()
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", named)
    monkeypatch.setattr("backend.config.settings.storage_path", tmp_path)
    crypto.reset_cache()

    assert crypto.active_key() == named
    assert crypto.key_source() == "env"
    assert not (tmp_path / crypto.KEY_FILENAME).exists()


def test_a_key_that_cannot_be_written_turns_the_feature_off(monkeypatch, tmp_path):
    """The one case that still disables it: nowhere to put the key. A read-only
    disk must report the feature off, not raise into whatever asked."""
    from backend.integrations import crypto

    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    monkeypatch.setattr("backend.config.settings.storage_path", tmp_path)
    crypto.reset_cache()
    monkeypatch.setattr(
        crypto.Path, "write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only file system")),
    )

    assert crypto.integrations_enabled() is False
    assert crypto.key_source() == "none"
    with pytest.raises(RuntimeError):
        crypto.encrypt_credentials({"x": "y"})
