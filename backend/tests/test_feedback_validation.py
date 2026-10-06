"""Feedback validation: pure logic, no database and no server.

Every refusal is an AppError with a stable code, because the frontend renders
`errors.<code>` in the person's language. The screenshot's type is decided from
its bytes, never from what the client claims.
"""

import base64
import struct
import zlib

import pytest

from backend.errors import AppError
from backend.feedback import validation as v


def _png(extra: bytes = b"") -> bytes:
    """A real 1x1 PNG (so a decoder would accept it), optionally padded."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"") + extra


def _jpeg(extra: bytes = b"") -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20 + extra + b"\xff\xd9"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


class TestScreenshotMagicBytes:

    def test_png_is_accepted_and_typed_from_its_bytes(self):
        shot = v.decode_screenshot(_b64(_png()))
        assert shot is not None
        assert (shot.ext, shot.media_type) == ("png", "image/png")

    def test_jpeg_is_accepted(self):
        shot = v.decode_screenshot(_b64(_jpeg()))
        assert (shot.ext, shot.media_type) == ("jpg", "image/jpeg")

    def test_data_url_prefix_is_accepted_and_ignored_for_typing(self):
        # The declared type says PNG; the bytes say JPEG. The bytes win.
        shot = v.decode_screenshot("data:image/png;base64," + _b64(_jpeg()))
        assert shot.ext == "jpg"

    @pytest.mark.parametrize("payload", [
        b"GIF89a" + b"\x00" * 40,
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"%PDF-1.7\n" + b"0" * 40,
        b"MZ" + b"\x00" * 60,
        b"<html><body>not an image</body></html>",
    ])
    def test_other_file_types_are_refused(self, payload):
        with pytest.raises(AppError) as exc:
            v.decode_screenshot(_b64(payload))
        assert _code(exc) == "feedback_screenshot_invalid"
        assert exc.value.status_code == 422

    def test_text_that_is_not_base64_is_refused(self):
        with pytest.raises(AppError) as exc:
            v.decode_screenshot("this is not base64 !!!")
        assert _code(exc) == "feedback_screenshot_invalid"

    def test_a_png_signature_with_nothing_after_it_is_refused(self):
        with pytest.raises(AppError) as exc:
            v.decode_screenshot(_b64(b"\x89PNG\r\n\x1a\n"))
        assert _code(exc) == "feedback_screenshot_invalid"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_no_screenshot_is_valid(self, empty):
        assert v.decode_screenshot(empty) is None


class TestScreenshotSize:

    def test_exactly_at_the_cap_is_accepted(self):
        raw = _png(b"\x00" * (v.MAX_SCREENSHOT_BYTES - len(_png())))
        assert len(raw) == v.MAX_SCREENSHOT_BYTES
        assert v.decode_screenshot(_b64(raw)) is not None

    def test_one_byte_over_the_cap_is_refused_with_413(self):
        raw = _png(b"\x00" * (v.MAX_SCREENSHOT_BYTES - len(_png()) + 1))
        with pytest.raises(AppError) as exc:
            v.decode_screenshot(_b64(raw))
        assert _code(exc) == "feedback_screenshot_too_large"
        assert exc.value.status_code == 413
        assert exc.value.params == {"max_mb": "2.5"}

    def test_a_huge_payload_is_refused_before_it_is_decoded(self, monkeypatch):
        called = []
        monkeypatch.setattr(v.base64, "b64decode", lambda *a, **k: called.append(1) or b"")
        with pytest.raises(AppError) as exc:
            v.decode_screenshot("A" * (v.MAX_SCREENSHOT_BYTES * 2))
        assert _code(exc) == "feedback_screenshot_too_large"
        assert called == [], "the oversize check must run before any decoding allocates memory"

    def test_the_body_cap_is_larger_than_the_cap_of_a_maximal_screenshot(self):
        # The endpoint refuses a body bigger than MAX_BODY_BYTES; it must never
        # refuse a legal screenshot because of the JSON around it.
        assert v.MAX_BODY_BYTES > len(_b64(b"\x00" * v.MAX_SCREENSHOT_BYTES)) + 1024


class TestMessage:

    def test_trimmed_and_returned(self):
        assert v.clean_message("  it broke \n") == "it broke"

    @pytest.mark.parametrize("bad", [None, "", "  ", "ab", " a "])
    def test_too_short_is_refused(self, bad):
        with pytest.raises(AppError) as exc:
            v.clean_message(bad)
        assert _code(exc) == "feedback_message_required"

    def test_too_long_is_refused_with_the_limit_as_a_param(self):
        with pytest.raises(AppError) as exc:
            v.clean_message("x" * (v.MAX_MESSAGE_CHARS + 1))
        assert _code(exc) == "feedback_message_too_long"
        assert exc.value.params == {"max": v.MAX_MESSAGE_CHARS}

    def test_a_nul_character_is_removed_not_passed_to_the_database(self):
        assert v.clean_message("it\x00 broke") == "it broke"
        assert v.clip("a\x00b", 10) == "ab"
        assert v.clean_page_path("/pe\x00didos") == "/pedidos"

    def test_exactly_the_limit_is_accepted(self):
        assert len(v.clean_message("x" * v.MAX_MESSAGE_CHARS)) == v.MAX_MESSAGE_CHARS


class TestMetadata:

    def test_page_path_loses_its_query_string_and_fragment(self):
        assert v.clean_page_path("/pedidos?token=abc&sku=9#top") == "/pedidos"

    def test_fields_are_cut_not_refused(self):
        assert len(v.clip("a" * 1000, v.MAX_USER_AGENT_CHARS)) == v.MAX_USER_AGENT_CHARS

    def test_whitespace_is_collapsed_and_empty_becomes_none(self):
        assert v.clip("  a \n  b ", 20) == "a b"
        assert v.clip("   ", 20) is None
        assert v.clip(None, 20) is None


class TestRateLimitDecision:

    def test_below_the_maximum_is_allowed(self):
        assert v.is_rate_limited(v.RATE_MAX_PER_HOUR - 1) is False

    def test_at_the_maximum_is_refused(self):
        assert v.is_rate_limited(v.RATE_MAX_PER_HOUR) is True

    def test_the_documented_numbers(self):
        # 10 per person per rolling hour (owner's brief).
        assert (v.RATE_MAX_PER_HOUR, v.RATE_WINDOW_SECONDS) == (10, 3600)
