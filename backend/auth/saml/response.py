"""The SAML 2.0 rules for a Response, on top of `xmlsig`.

`validate_response` is the whole trust decision. It returns claims only when:

 1. the document parses under the hardened parser and has exactly ONE
    Assertion, a direct child of the Response, and no encrypted element;
 2. a configured certificate verified a signature over the Response, the
    Assertion, or both, and the data below is read from the verified element's
    own subtree (see `xmlsig` for why that closes signature wrapping);
 3. it answers THIS sign-in: `InResponseTo` equals the request id we issued, on
    the Response and on the bearer confirmation;
 4. it was meant for THIS service provider and THIS endpoint: `Audience` is the
    tenant's SP entity id, `Destination` and `Recipient` are the ACS URL;
 5. it comes from THIS identity provider (`Issuer`), is within its validity
    window (`NotBefore`, `NotOnOrAfter`, `SessionNotOnOrAfter`) allowing a
    small clock skew, and the status is Success.

Unsolicited (IdP-initiated) responses are refused by rule 3: no request id, no
sign-in. That is a decision, not a gap: an IdP-initiated response is one an
attacker can try to deliver to a victim's browser, and the product only needs
the SP-initiated flow the login page starts.

Every refusal is a `SocialAuthError` with a stable `saml_*` code the frontend
translates; details never include assertion content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from xml.dom import minidom

from backend.auth.saml import xmlsig
from backend.auth.social.providers import SocialAuthError
from backend.auth.saml.xmlsig import DS, XmlSecurityError

SAML_P = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML_A = "urn:oasis:names:tc:SAML:2.0:assertion"
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NAMEID_TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"
NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"

CLOCK_SKEW = timedelta(seconds=120)

# Attribute names IdPs commonly use for the e-mail, tried in order when the
# tenant has not named one.
EMAIL_ATTRIBUTES = (
    "email", "mail", "emailAddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
NAME_ATTRIBUTES = (
    "displayName", "name", "cn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "urn:oid:2.16.840.1.113730.3.1.241",
)

_INSTANT = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d{1,9})?Z$")

MAX_VALUE_LEN = 512
MAX_ATTRIBUTES = 200
MAX_VALUES_PER_ATTRIBUTE = 200


@dataclass(frozen=True)
class Expected:
    """What this particular sign-in must have been issued for."""

    sp_entity_id: str
    acs_url: str
    idp_entity_id: str
    request_id: str
    certificates: list[str]          # base64 DER, from `saml_providers`
    email_attribute: str | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class SamlClaims:
    subject: str
    email: str | None
    full_name: str | None
    raw: dict                        # attribute name -> list[str]
    name_id_format: str | None = None


def _fail(code: str, detail: str = "") -> SocialAuthError:
    return SocialAuthError(code, detail)


def parse_instant(value: str | None, what: str) -> datetime:
    """xs:dateTime as SAML requires it: UTC, with the `Z`."""
    m = _INSTANT.match((value or "").strip())
    if not m:
        raise _fail("saml_time_invalid", what)
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    frac = m.group(7)
    try:
        dt = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        raise _fail("saml_time_invalid", what) from None
    if frac:
        dt += timedelta(microseconds=int((frac[1:] + "000000")[:6]))
    return dt


def _kids(el, ns, name):
    return xmlsig.child_elements(el, ns, name)


def _single(el, ns, name, code="saml_response_invalid"):
    found = _kids(el, ns, name)
    if len(found) != 1:
        raise _fail(code, f"expected exactly one {name}")
    return found[0]


def _text(el) -> str:
    try:
        return xmlsig.text_of(el).strip()
    except XmlSecurityError:
        raise _fail("saml_response_invalid", "unexpected structure") from None


# ── The document ─────────────────────────────────────────────────────────────

def decode_form_value(raw: str | None) -> bytes:
    """The `SAMLResponse` form field: base64, the only thing allowed in it."""
    if not raw:
        raise _fail("saml_response_missing", "no SAMLResponse")
    if len(raw) > (xmlsig.MAX_XML_BYTES * 4) // 3 + 16:
        raise _fail("saml_response_too_large", "SAMLResponse too large")
    import base64
    import binascii
    try:
        return base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError):
        raise _fail("saml_response_invalid", "SAMLResponse is not base64") from None


def validate_response(raw_b64: str | None, expected: Expected) -> SamlClaims:
    data = decode_form_value(raw_b64)
    now = expected.now or datetime.now(timezone.utc)
    try:
        doc = xmlsig.parse_xml(data)
        return _validate(doc, expected, now)
    except XmlSecurityError as exc:
        raise _fail(exc.code, exc.detail) from None


def _validate(doc: minidom.Document, exp: Expected, now: datetime) -> SamlClaims:
    root = doc.documentElement
    if (root.namespaceURI, root.localName) != (SAML_P, "Response"):
        raise _fail("saml_response_invalid", "root is not a Response")
    xmlsig.unique_ids(root)

    everything = list(xmlsig.walk(root))
    for el in everything:
        if el.namespaceURI == SAML_A and el.localName in (
                "EncryptedAssertion", "EncryptedID", "EncryptedAttribute"):
            raise _fail("saml_encryption_unsupported", "encrypted content is not supported")

    assertions = [e for e in everything if e.namespaceURI == SAML_A and e.localName == "Assertion"]
    if len(assertions) != 1 or assertions[0].parentNode is not root:
        # Zero is a failed sign-in; more than one, or one hidden elsewhere, is
        # the shape of a signature-wrapping attempt.
        raise _fail("saml_assertion_invalid", "expected exactly one Assertion under the Response")
    assertion = assertions[0]

    # Every signature in the document must be one we understand, in a place we
    # understand: a direct child of the Response or of the Assertion.
    signatures = [e for e in everything if e.namespaceURI == DS and e.localName == "Signature"]
    resp_sigs = _kids(root, DS, "Signature")
    asrt_sigs = _kids(assertion, DS, "Signature")
    if len(signatures) != len(resp_sigs) + len(asrt_sigs) or len(resp_sigs) > 1 or len(asrt_sigs) > 1:
        raise _fail("saml_signature_invalid", "unexpected signature placement")
    if not signatures:
        raise _fail("saml_unsigned", "neither the Response nor the Assertion is signed")

    certs = xmlsig.load_certificates(exp.certificates)
    for sig in signatures:  # all of them must verify, not just one
        xmlsig.verify_enveloped(sig, certs)

    _check_response(root, exp)
    return _check_assertion(assertion, exp, now)


# ── Response level ───────────────────────────────────────────────────────────

def _check_response(root, exp: Expected) -> None:
    if root.getAttribute("Version") != "2.0":
        raise _fail("saml_response_invalid", "version")
    # Required: a Response not addressed to our endpoint is not ours, and an
    # absent Destination cannot be told from one stripped on the way.
    if root.getAttribute("Destination") != exp.acs_url:
        raise _fail("saml_destination_mismatch", "Destination")
    if root.getAttribute("InResponseTo") != exp.request_id:
        raise _fail("saml_in_response_to_mismatch", "InResponseTo")

    status = _single(root, SAML_P, "Status")
    code = _single(status, SAML_P, "StatusCode")
    if code.getAttribute("Value") != STATUS_SUCCESS:
        top = code.getAttribute("Value")
        # A person who declined or failed at the IdP is not an attack.
        if top.endswith(":Responder") or top.endswith(":AuthnFailed") or top.endswith(":NoPassive"):
            raise _fail("saml_idp_refused", "status")
        raise _fail("saml_status_not_success", "status")

    for issuer in _kids(root, SAML_A, "Issuer"):
        if _text(issuer) != exp.idp_entity_id:
            raise _fail("saml_issuer_mismatch", "Response Issuer")


# ── Assertion level ──────────────────────────────────────────────────────────

def _check_assertion(assertion, exp: Expected, now: datetime) -> SamlClaims:
    if assertion.getAttribute("Version") != "2.0":
        raise _fail("saml_assertion_invalid", "version")
    issuer = _single(assertion, SAML_A, "Issuer", "saml_issuer_mismatch")
    if _text(issuer) != exp.idp_entity_id:
        raise _fail("saml_issuer_mismatch", "Assertion Issuer")
    instant = parse_instant(assertion.getAttribute("IssueInstant"), "IssueInstant")
    if instant > now + CLOCK_SKEW:
        raise _fail("saml_not_yet_valid", "IssueInstant in the future")

    subject = _single(assertion, SAML_A, "Subject", "saml_subject_invalid")
    name_id_el = _single(subject, SAML_A, "NameID", "saml_subject_invalid")
    name_id = _text(name_id_el)
    name_id_format = name_id_el.getAttribute("Format") or None
    if not name_id or len(name_id) > MAX_VALUE_LEN:
        raise _fail("saml_subject_invalid", "NameID")
    if name_id_format == NAMEID_TRANSIENT:
        # A transient id changes on every sign-in: it cannot identify a person.
        raise _fail("saml_nameid_transient", "NameID format")
    sp_qualifier = name_id_el.getAttribute("SPNameQualifier")
    if sp_qualifier and sp_qualifier != exp.sp_entity_id:
        raise _fail("saml_audience_mismatch", "SPNameQualifier")

    _check_bearer(subject, exp, now)
    _check_conditions(assertion, exp, now)

    statements = _kids(assertion, SAML_A, "AuthnStatement")
    if not statements:
        raise _fail("saml_assertion_invalid", "no AuthnStatement")
    for st in statements:
        parse_instant(st.getAttribute("AuthnInstant"), "AuthnInstant")
        if st.hasAttribute("SessionNotOnOrAfter"):
            end = parse_instant(st.getAttribute("SessionNotOnOrAfter"), "SessionNotOnOrAfter")
            if end <= now - CLOCK_SKEW:
                raise _fail("saml_expired", "SessionNotOnOrAfter")

    attributes = _attributes(assertion)
    email = _email(exp, attributes, name_id, name_id_format)
    name = None
    for n in NAME_ATTRIBUTES:
        if attributes.get(n):
            name = attributes[n][0][:200]
            break
    return SamlClaims(subject=name_id, email=email, full_name=name, raw=attributes,
                      name_id_format=name_id_format)


def _check_bearer(subject, exp: Expected, now: datetime) -> None:
    confirmations = _kids(subject, SAML_A, "SubjectConfirmation")
    last: SocialAuthError | None = None
    for conf in confirmations:
        if conf.getAttribute("Method") != BEARER:
            continue
        data = _kids(conf, SAML_A, "SubjectConfirmationData")
        try:
            if len(data) != 1:
                raise _fail("saml_recipient_mismatch", "SubjectConfirmationData")
            d = data[0]
            if d.getAttribute("Recipient") != exp.acs_url:
                raise _fail("saml_recipient_mismatch", "Recipient")
            if d.getAttribute("InResponseTo") != exp.request_id:
                raise _fail("saml_in_response_to_mismatch", "InResponseTo")
            if not d.hasAttribute("NotOnOrAfter"):
                raise _fail("saml_time_invalid", "NotOnOrAfter missing")
            end = parse_instant(d.getAttribute("NotOnOrAfter"), "NotOnOrAfter")
            if end <= now - CLOCK_SKEW:
                raise _fail("saml_expired", "NotOnOrAfter")
            if d.hasAttribute("NotBefore"):
                start = parse_instant(d.getAttribute("NotBefore"), "NotBefore")
                if start > now + CLOCK_SKEW:
                    raise _fail("saml_not_yet_valid", "NotBefore")
            return
        except SocialAuthError as exc:
            last = exc
    raise last or _fail("saml_recipient_mismatch", "no bearer confirmation")


_KNOWN_CONDITIONS = {"AudienceRestriction", "OneTimeUse", "ProxyRestriction"}


def _check_conditions(assertion, exp: Expected, now: datetime) -> None:
    conditions = _kids(assertion, SAML_A, "Conditions")
    if len(conditions) != 1:
        raise _fail("saml_conditions_invalid", "expected one Conditions")
    cond = conditions[0]
    if cond.hasAttribute("NotBefore"):
        if parse_instant(cond.getAttribute("NotBefore"), "NotBefore") > now + CLOCK_SKEW:
            raise _fail("saml_not_yet_valid", "Conditions NotBefore")
    if not cond.hasAttribute("NotOnOrAfter"):
        raise _fail("saml_time_invalid", "Conditions NotOnOrAfter missing")
    if parse_instant(cond.getAttribute("NotOnOrAfter"), "NotOnOrAfter") <= now - CLOCK_SKEW:
        raise _fail("saml_expired", "Conditions NotOnOrAfter")

    restrictions = []
    for c in xmlsig.child_elements(cond):
        # A condition we do not understand must fail the assertion (SAML core).
        if c.namespaceURI != SAML_A or c.localName not in _KNOWN_CONDITIONS:
            raise _fail("saml_conditions_invalid", "unknown condition")
        if c.localName == "AudienceRestriction":
            restrictions.append(c)
    if not restrictions:
        raise _fail("saml_audience_mismatch", "no AudienceRestriction")
    for r in restrictions:  # every restriction must be satisfied
        audiences = [_text(a) for a in _kids(r, SAML_A, "Audience")]
        if exp.sp_entity_id not in audiences:
            raise _fail("saml_audience_mismatch", "Audience")


def _attributes(assertion) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    count = 0
    for st in _kids(assertion, SAML_A, "AttributeStatement"):
        for attr in _kids(st, SAML_A, "Attribute"):
            count += 1
            if count > MAX_ATTRIBUTES:
                raise _fail("saml_response_invalid", "too many attributes")
            name = attr.getAttribute("Name")
            if not name or len(name) > 300:
                continue
            values = out.setdefault(name, [])
            for v in _kids(attr, SAML_A, "AttributeValue"):
                if len(values) >= MAX_VALUES_PER_ATTRIBUTE:
                    break
                try:
                    text = xmlsig.text_of(v).strip()
                except XmlSecurityError:
                    continue  # a structured value is not a string we can map
                if text and len(text) <= 1000:
                    values.append(text)
    return out


def _email(exp: Expected, attributes: dict, name_id: str, fmt: str | None) -> str | None:
    if exp.email_attribute:
        values = attributes.get(exp.email_attribute) or []
        return values[0].strip().lower() if values else None
    if fmt == NAMEID_EMAIL or (fmt in (None, "urn:oasis:names:tc:SAML:2.0:nameid-format:unspecified")
                               and "@" in name_id):
        return name_id.strip().lower()
    for n in EMAIL_ATTRIBUTES:
        if attributes.get(n):
            return attributes[n][0].strip().lower()
    return None
