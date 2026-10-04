"""Every error code the backend can emit reads in Spanish AND English.

A code with no `errors.<code>` entry falls through to a generic sentence, so the
user loses the specific advice ("the file is 40 MB, the max is 25"). This is the
wall that keeps a new `AppError("some_code", ...)` from shipping untranslated.
"""

import pytest

from backend.error_codes import all_bridge_codes, describe_http_error
from backend.scripts.check_error_codes import backend_codes, missing_translations


def test_every_backend_error_code_has_es_and_en_copy():
    gaps = missing_translations()
    assert not gaps, "untranslated error codes: " + ", ".join(
        f"{code}[{lang}] ({where})" for code, lang, where in gaps
    )


def test_the_scan_actually_finds_codes():
    # Guards against the regex silently matching nothing and the test above
    # passing vacuously.
    codes = backend_codes()
    assert "session_not_found" in codes
    assert "server_busy" in codes
    assert len(codes) > 100


def test_check_fails_when_a_translation_is_missing(monkeypatch):
    import backend.scripts.check_error_codes as chk

    monkeypatch.setattr(
        chk, "backend_codes", lambda: {"definitely_not_translated_code": "x.py"},
    )
    assert chk.missing_translations() == [
        ("definitely_not_translated_code", "es", "x.py"),
        ("definitely_not_translated_code", "en", "x.py"),
    ]


@pytest.mark.parametrize("detail,code,params", [
    ("Invalid state transition: DRAFT → COMPLETED. Allowed from DRAFT: ['QUEUED']",
     "session_invalid_transition", {"current": "DRAFT", "target": "COMPLETED"}),
    ("File too large (60 MB). Max 50 MB.", "file_too_large",
     {"size_mb": "60", "max_mb": "50"}),
    ("File size 31.2 MB exceeds limit of 25 MB", "file_too_large",
     {"size_mb": "31.2", "max_mb": "25"}),
    ("Session not found", "session_not_found", {}),
    ("Rate limit exceeded: max 20 messages per 60s per organization. Please wait.",
     "chat_rate_limited", {"max": "20", "window": "60"}),
])
def test_bridge_maps_known_sentences(detail, code, params):
    assert describe_http_error(detail) == (code, params)
    assert code in all_bridge_codes()


def test_bridge_ignores_unknown_sentences_and_non_strings():
    assert describe_http_error("Something nobody has coded yet") is None
    assert describe_http_error({"code": "PLAN_LIMIT_REACHED"}) is None
    assert describe_http_error(None) is None


def test_http_exception_envelope_carries_code_and_keeps_detail(client, auth_headers):
    """The wire shape: `detail` stays the English sentence, `error_code` is added."""
    r = client.get("/api/v1/chats/does-not-exist", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "Chat not found"
    assert body["error_code"] == "chat_not_found"
    assert body["error_params"] == {}
