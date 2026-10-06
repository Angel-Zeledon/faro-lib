"""A pretend SAML identity provider for the tests, and the pieces of attacks.

Documents are written DIRECTLY in exclusive-canonical form (attributes sorted,
empty elements expanded, each namespace declared where first used, no
whitespace between elements), so the digest and the signature are computed
from text this file wrote by hand, not from the code under test. If
`backend.auth.saml.xmlsig.canonicalize` and these strings ever disagree, a
signature that "should" verify does not, and the tests fail loudly.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

P = "urn:oasis:names:tc:SAML:2.0:protocol"
A = "urn:oasis:names:tc:SAML:2.0:assertion"
DS = "http://www.w3.org/2000/09/xmldsig#"
EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENV = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
RSA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
RSA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
DIG256 = "http://www.w3.org/2001/04/xmlenc#sha256"
DIG1 = "http://www.w3.org/2000/09/xmldsig#sha1"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
EMAIL_FMT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"


def instant(delta_seconds: float = 0, now: dt.datetime | None = None) -> str:
    t = (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(seconds=delta_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_identity(cn: str = "idp.example-saml.test", bits: int = 2048):
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650)).sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return key, base64.b64encode(der).decode()


def esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


class FakeIdp:
    entity_id = "https://idp.example-saml.test/metadata"
    sso_url = "https://idp.example-saml.test/sso"

    def __init__(self, key=None, cert_b64: str | None = None):
        if key is None:
            key, cert_b64 = make_identity()
        self.key, self.cert_b64 = key, cert_b64

    # ── signing ──────────────────────────────────────────────────────────────

    def sign_bytes(self, data: bytes, alg: str = RSA256, key=None) -> str:
        h = {RSA256: hashes.SHA256, RSA1: hashes.SHA1}[alg]()
        return base64.b64encode((key or self.key).sign(data, padding.PKCS1v15(), h)).decode()

    def signature_block(self, canonical_without_signature: str, element_id: str, *,
                        alg: str = RSA256, digest_alg: str = DIG256, key=None,
                        uri: str | None = None, transforms: list[str] | None = None,
                        digest_of: str | None = None, extra_children: str = "") -> str:
        """`<ds:Signature>` over an element whose canonical text (signature
        removed) is given. Declared as the canonical form of itself, so it can
        be embedded in a larger canonical document unchanged."""
        data = (digest_of or canonical_without_signature).encode()
        digest = {DIG256: hashlib.sha256, DIG1: hashlib.sha1}[digest_alg](data).digest()
        transforms = transforms if transforms is not None else [ENV, EXC]
        tr = "".join(f'<ds:Transform Algorithm="{t}"></ds:Transform>' for t in transforms)
        inner = (
            f'<ds:SignedInfo>'
            f'<ds:CanonicalizationMethod Algorithm="{EXC}"></ds:CanonicalizationMethod>'
            f'<ds:SignatureMethod Algorithm="{alg}"></ds:SignatureMethod>'
            f'<ds:Reference URI="{uri if uri is not None else "#" + element_id}">'
            f'<ds:Transforms>{tr}</ds:Transforms>'
            f'<ds:DigestMethod Algorithm="{digest_alg}"></ds:DigestMethod>'
            f'<ds:DigestValue>{base64.b64encode(digest).decode()}</ds:DigestValue>'
            f'</ds:Reference></ds:SignedInfo>'
        )
        signed_info_canonical = inner.replace("<ds:SignedInfo>", f'<ds:SignedInfo xmlns:ds="{DS}">', 1)
        value = self.sign_bytes(signed_info_canonical.encode(), alg, key)
        return (f'<ds:Signature xmlns:ds="{DS}">{inner}'
                f'<ds:SignatureValue>{value}</ds:SignatureValue>{extra_children}</ds:Signature>')

    # ── the assertion ────────────────────────────────────────────────────────

    def assertion(self, *, request_id: str, sp_entity: str, acs: str, email: str,
                  name_id: str | None = None, name_id_format: str | None = EMAIL_FMT,
                  attributes: dict[str, list[str]] | None = None, assertion_id: str | None = None,
                  issuer: str | None = None, audience: str | None = None,
                  recipient: str | None = None, scd_in_response_to: str | None = None,
                  not_on_or_after: float = 300, not_before: float = -5, cond_not_on_or_after: float = 300,
                  cond_not_before: float | None = -5, scd_not_before: float | None = None,
                  issue_instant: float = 0, session_not_on_or_after: float | None = None,
                  conditions_extra: str = "", with_conditions: bool = True,
                  with_audience: bool = True, with_authn: bool = True,
                  with_scd_not_on_or_after: bool = True, bearer: bool = True,
                  version: str = "2.0", sp_name_qualifier: str | None = None,
                  now: dt.datetime | None = None, subject_extra: str = "") -> tuple[str, str]:
        aid = assertion_id or "_a" + secrets.token_hex(8)
        nid = name_id if name_id is not None else email
        fmt = f' Format="{name_id_format}"' if name_id_format else ""
        spq = f' SPNameQualifier="{esc_attr(sp_name_qualifier)}"' if sp_name_qualifier else ""
        scd_attrs = [("InResponseTo", scd_in_response_to if scd_in_response_to is not None else request_id)]
        if scd_not_before is not None:
            scd_attrs.append(("NotBefore", instant(scd_not_before, now)))
        if with_scd_not_on_or_after:
            scd_attrs.append(("NotOnOrAfter", instant(not_on_or_after, now)))
        scd_attrs.append(("Recipient", recipient if recipient is not None else acs))
        scd = "".join(f' {k}="{esc_attr(v)}"' for k, v in sorted(scd_attrs))
        method = "urn:oasis:names:tc:SAML:2.0:cm:bearer" if bearer else "urn:oasis:names:tc:SAML:2.0:cm:holder-of-key"
        subject = (
            f'<saml:Subject><saml:NameID{fmt}{spq}>{esc_text(nid)}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="{method}">'
            f'<saml:SubjectConfirmationData{scd}></saml:SubjectConfirmationData>'
            f'</saml:SubjectConfirmation>{subject_extra}</saml:Subject>'
        )
        cond = ""
        if with_conditions:
            c_attrs = []
            if cond_not_before is not None:
                c_attrs.append(("NotBefore", instant(cond_not_before, now)))
            if cond_not_on_or_after is not None:
                c_attrs.append(("NotOnOrAfter", instant(cond_not_on_or_after, now)))
            ca = "".join(f' {k}="{v}"' for k, v in sorted(c_attrs))
            aud = (f'<saml:AudienceRestriction><saml:Audience>'
                   f'{esc_text(audience if audience is not None else sp_entity)}'
                   f'</saml:Audience></saml:AudienceRestriction>') if with_audience else ""
            cond = f'<saml:Conditions{ca}>{aud}{conditions_extra}</saml:Conditions>'
        authn_attrs = f' AuthnInstant="{instant(-30, now)}" SessionIndex="_s1"'
        if session_not_on_or_after is not None:  # canonical order: SessionIndex < SessionNotOnOrAfter
            authn_attrs += f' SessionNotOnOrAfter="{instant(session_not_on_or_after, now)}"'
        authn = (f'<saml:AuthnStatement{authn_attrs}><saml:AuthnContext>'
                 f'<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password'
                 f'</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>') if with_authn else ""
        attrs = {"email": [email]} if attributes is None else attributes
        stmt = ""
        if attrs:
            stmt = "<saml:AttributeStatement>" + "".join(
                f'<saml:Attribute Name="{esc_attr(n)}">' + "".join(
                    f'<saml:AttributeValue>{esc_text(v)}</saml:AttributeValue>' for v in vs)
                + "</saml:Attribute>" for n, vs in attrs.items()) + "</saml:AttributeStatement>"
        text = (
            f'<saml:Assertion xmlns:saml="{A}" ID="{aid}" IssueInstant="{instant(issue_instant, now)}" '
            f'Version="{version}">'
            f'<saml:Issuer>{esc_text(issuer if issuer is not None else self.entity_id)}</saml:Issuer>'
            f'{subject}{cond}{authn}{stmt}</saml:Assertion>'
        )
        return text, aid

    @staticmethod
    def put_signature(element_text: str, signature: str) -> str:
        """The signature goes right after the element's Issuer."""
        marker = "</saml:Issuer>"
        i = element_text.index(marker) + len(marker)
        return element_text[:i] + signature + element_text[i:]

    def response(self, *, request_id: str, acs: str, assertion_text: str,
                 destination: str | None = None, in_response_to: str | None = None,
                 status: str = SUCCESS, response_id: str | None = None,
                 issuer: str | None = "self", now: dt.datetime | None = None,
                 version: str = "2.0") -> tuple[str, str]:
        rid = response_id or "_r" + secrets.token_hex(8)
        iss = ""
        if issuer is not None:
            iss = f'<saml:Issuer xmlns:saml="{A}">{esc_text(self.entity_id if issuer == "self" else issuer)}</saml:Issuer>'
        text = (
            f'<samlp:Response xmlns:samlp="{P}" '
            f'Destination="{esc_attr(destination if destination is not None else acs)}" ID="{rid}" '
            f'InResponseTo="{esc_attr(in_response_to if in_response_to is not None else request_id)}" '
            f'IssueInstant="{instant(0, now)}" Version="{version}">{iss}'
            f'<samlp:Status><samlp:StatusCode Value="{status}"></samlp:StatusCode></samlp:Status>'
            f'{assertion_text}</samlp:Response>'
        )
        return text, rid

    # ── whole documents, the usual shapes ────────────────────────────────────

    def signed_response(self, *, request_id: str, sp_entity: str, acs: str, email: str,
                        sign: str = "assertion", assertion_kw: dict | None = None,
                        response_kw: dict | None = None, sig_kw: dict | None = None) -> str:
        """`sign` is "assertion", "response", "both" or "none". Returns the XML."""
        a_text, aid = self.assertion(request_id=request_id, sp_entity=sp_entity, acs=acs,
                                     email=email, **(assertion_kw or {}))
        if sign in ("assertion", "both"):
            a_text = self.put_signature(a_text, self.signature_block(a_text, aid, **(sig_kw or {})))
        r_text, rid = self.response(request_id=request_id, acs=acs, assertion_text=a_text,
                                    **(response_kw or {}))
        if sign in ("response", "both"):
            r_text = self.put_signature(r_text, self.signature_block(r_text, rid, **(sig_kw or {})))
        return r_text


def b64(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()
