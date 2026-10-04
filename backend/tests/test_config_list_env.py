"""List-valued settings must accept the form `.env.example` documents.

`INSTANCE_ADMIN_EMAILS=you@example.com` — a bare address, as the template shows
it — made pydantic-settings raise at import, so the API container crashed on
its first production boot. Both the comma-separated and the JSON forms must
parse.
"""
import pytest

from backend.config import Settings


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("owner@example.com", ["owner@example.com"]),
        ("a@x.com, b@y.com", ["a@x.com", "b@y.com"]),
        ('["a@x.com", "b@y.com"]', ["a@x.com", "b@y.com"]),
        ("", []),
    ],
)
def test_instance_admin_emails_parses_every_documented_form(monkeypatch, raw, expected):
    monkeypatch.setenv("INSTANCE_ADMIN_EMAILS", raw)
    assert Settings().instance_admin_emails == expected


def test_allowed_origins_accepts_a_single_url(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    assert Settings().allowed_origins == ["https://app.example.com"]


def test_allowed_origins_still_accepts_json(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", '["https://a.com","https://b.com"]')
    assert Settings().allowed_origins == ["https://a.com", "https://b.com"]
