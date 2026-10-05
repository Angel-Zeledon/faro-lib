"""Provider-agnostic reading of an inbound-mail webhook body.

The contract is deliberately tolerant on field NAMES and strict on everything
else (size, content type, authentication happen before this module is reached).
Supported shapes, all producing the same `ParsedEmail`:

* JSON, generic:   {"from", "to", "message_id", "attachments":
                    [{"filename", "content_base64", "content_type"}]}
* JSON, Postmark:  {"From"|"FromFull.Email", "OriginalRecipient"|"To",
                    "MessageID", "Attachments": [{"Name", "Content", "ContentType"}]}
* JSON, Resend:    {"type": "email.received", "data": {...generic shape...}}
* multipart/form-data, Mailgun/SendGrid style: fields `from`|`sender`,
  `recipient`|`to`, `Message-Id`; every file part is an attachment.

An attachment that arrives only as a URL (no inline bytes) cannot be read and is
counted in `skipped_attachments`, never silently dropped.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_ADDR = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


class MalformedPayload(ValueError):
    """The body is not an e-mail in any shape we know."""


@dataclass
class Attachment:
    filename: str
    content: bytes
    content_type: str = ""


@dataclass
class ParsedEmail:
    message_id: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    skipped_attachments: int = 0


def extract_addresses(value: Any) -> list[str]:
    """Every e-mail address inside a string, a dict or a list of either."""
    found: list[str] = []
    if value is None:
        return found
    if isinstance(value, str):
        found += [m.group(0).lower() for m in _ADDR.finditer(value)]
    elif isinstance(value, dict):
        for key in ("Email", "email", "address", "Address"):
            if key in value:
                found += extract_addresses(value[key])
                break
        else:
            for v in value.values():
                found += extract_addresses(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found += extract_addresses(v)
    return found


def _first(obj: dict, *keys: str) -> Any:
    for k in keys:
        if obj.get(k) not in (None, ""):
            return obj[k]
    return None


def parse_json(obj: Any) -> ParsedEmail:
    if not isinstance(obj, dict):
        raise MalformedPayload("JSON body must be an object")
    # Resend wraps the message: {"type": "email.received", "data": {...}}.
    if isinstance(obj.get("data"), dict) and "type" in obj:
        obj = obj["data"]

    out = ParsedEmail()
    out.message_id = str(_first(obj, "message_id", "MessageID", "Message-Id",
                                "messageId", "email_id") or "").strip()
    senders = extract_addresses(_first(obj, "FromFull", "from", "From", "sender", "from_email"))
    out.sender = senders[0] if senders else ""
    rcpt: list[str] = []
    for key in ("OriginalRecipient", "recipient", "recipients", "to", "To", "ToFull"):
        rcpt += extract_addresses(obj.get(key))
    env = obj.get("envelope")
    if isinstance(env, dict):
        rcpt += extract_addresses(env.get("to"))
    out.recipients = list(dict.fromkeys(rcpt))

    raw = _first(obj, "attachments", "Attachments") or []
    if not isinstance(raw, list):
        raise MalformedPayload("attachments must be a list")
    for att in raw:
        if not isinstance(att, dict):
            raise MalformedPayload("each attachment must be an object")
        name = str(_first(att, "filename", "Name", "name", "file_name") or "")
        data = _first(att, "content_base64", "Content", "content", "data")
        ctype = str(_first(att, "content_type", "ContentType", "contentType") or "")
        if not isinstance(data, str):
            out.skipped_attachments += 1      # URL-only or malformed: unreadable
            continue
        try:
            content = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError):
            out.skipped_attachments += 1
            continue
        out.attachments.append(Attachment(name, content, ctype))
    return out


def parse_form(fields: dict[str, Any], files: list[tuple[str, bytes, str]]) -> ParsedEmail:
    """`fields` are the text parts, `files` are (filename, bytes, content_type)."""
    out = ParsedEmail()
    low = {k.lower(): v for k, v in fields.items() if isinstance(v, str)}
    out.message_id = (low.get("message-id") or low.get("message_id") or "").strip()
    senders = extract_addresses(low.get("from") or low.get("sender"))
    out.sender = senders[0] if senders else ""
    rcpt: list[str] = []
    for key in ("recipient", "to", "envelope"):
        rcpt += extract_addresses(low.get(key))
    out.recipients = list(dict.fromkeys(rcpt))
    out.attachments = [Attachment(n, c, t) for n, c, t in files]
    return out


def token_from_recipients(recipients: list[str], domain: str) -> Optional[str]:
    """The `<token>` of the first `sales+<token>@<domain>` recipient."""
    domain = domain.strip().lower()
    for addr in recipients:
        local, _, host = addr.partition("@")
        if host != domain or "+" not in local:
            continue
        token = local.split("+", 1)[1]
        if token:
            return token
    return None
