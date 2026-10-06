"""XML parsing and enveloped-signature verification for SAML responses.

Nothing here knows about SAML semantics (audiences, times); it answers two
questions only: "is this document safe to parse" and "did a trusted key sign
THIS element". `response.py` builds the SAML rules on top.

Why it is written by hand: no XML-signature library is installed with the
backend (no lxml, no xmlsec, no signxml), and adding a native dependency to the
distribution for one feature is not worth it. What is written is the smallest
subset a SAML identity provider actually uses, and **everything outside that
subset is refused, not guessed**:

* parsing: UTF-8 only, no DTD or entity declaration at all (so no XXE and no
  entity-expansion attack), a size cap and a depth cap;
* one `ds:Signature` per signed element, as its DIRECT child (enveloped);
* canonicalization: Exclusive XML Canonicalization 1.0 without comments, with
  `InclusiveNamespaces PrefixList` honoured;
* exactly one `Reference`, whose URI is `#<ID of the Signature's own parent>`;
* transforms: enveloped-signature, then exclusive c14n, nothing else (no XPath,
  no XSLT: those are how signature-wrapping and transform attacks work);
* RSA PKCS#1 v1.5 with SHA-256/384/512 (SHA-1 is refused);
* the key is NEVER read from the document's `KeyInfo`: the only keys trusted
  are the certificates the tenant's administrator stored.

Signature wrapping is closed by construction rather than by a check at the end:
the caller verifies a signature against its parent element and then reads data
ONLY from that parent's subtree, after `unique_ids` proved no other element
shares the ID the signature points to.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import re
from dataclasses import dataclass
from xml.dom import Node, minidom
from xml.parsers import expat

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DS = "http://www.w3.org/2000/09/xmldsig#"
XMLNS = "http://www.w3.org/2000/xmlns/"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
EXC_NS = "http://www.w3.org/2001/10/xml-exc-c14n#"

MAX_XML_BYTES = 512 * 1024
MAX_DEPTH = 64
MIN_RSA_BITS = 2048

SIGNATURE_METHODS = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": hashes.SHA384,
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": hashes.SHA512,
}
DIGEST_METHODS = {
    "http://www.w3.org/2001/04/xmlenc#sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#sha384": hashes.SHA384,
    "http://www.w3.org/2001/04/xmlenc#sha512": hashes.SHA512,
}


class XmlSecurityError(Exception):
    """A document or signature refused. `code` is a stable `saml_*` error code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# ── Parsing ──────────────────────────────────────────────────────────────────

_XML_DECL = re.compile(r"^\s*<\?xml\s[^>]*?encoding\s*=\s*['\"]([^'\"]+)['\"]", re.I)


def parse_xml(data: bytes) -> minidom.Document:
    """Parse untrusted XML, or raise `XmlSecurityError("saml_xml_invalid")`."""
    if len(data) > MAX_XML_BYTES:
        raise XmlSecurityError("saml_response_too_large", "document too large")
    # Only UTF-8 is accepted, and it is decoded HERE so the DOCTYPE scan below
    # sees the same characters the parser will. A UTF-16 document would
    # otherwise hide `<!DOCTYPE` from a byte-level scan.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        raise XmlSecurityError("saml_xml_invalid", "encoding not allowed")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise XmlSecurityError("saml_xml_invalid", "not UTF-8") from None
    if text.startswith("﻿"):
        text = text[1:]
    decl = _XML_DECL.match(text)
    if decl and decl.group(1).strip().lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise XmlSecurityError("saml_xml_invalid", "encoding not allowed")
    if "\x00" in text or "<!DOCTYPE" in text or "<!ENTITY" in text or "<!doctype" in text.lower():
        raise XmlSecurityError("saml_xml_invalid", "DTD not allowed")
    try:
        doc = minidom.parseString(text.encode("utf-8"))
    except (expat.ExpatError, ValueError, RecursionError):
        raise XmlSecurityError("saml_xml_invalid", "not well-formed XML") from None
    root = doc.documentElement
    if root is None:
        raise XmlSecurityError("saml_xml_invalid", "no root element")
    _check_shape(root)
    return doc


def _check_shape(root: minidom.Element) -> None:
    """Depth cap, iteratively (a recursive walk is itself an attack surface)."""
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise XmlSecurityError("saml_xml_invalid", "document nested too deeply")
        for child in node.childNodes:
            if child.nodeType == Node.ELEMENT_NODE:
                stack.append((child, depth + 1))
            elif child.nodeType in (Node.DOCUMENT_TYPE_NODE, Node.ENTITY_REFERENCE_NODE):
                raise XmlSecurityError("saml_xml_invalid", "DTD not allowed")


def walk(root: minidom.Element):
    """Every element under (and including) `root`, in document order."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed([c for c in node.childNodes if c.nodeType == Node.ELEMENT_NODE]))


def child_elements(el: minidom.Element, ns: str | None = None, name: str | None = None):
    out = []
    for c in el.childNodes:
        if c.nodeType != Node.ELEMENT_NODE:
            continue
        if ns is not None and c.namespaceURI != ns:
            continue
        if name is not None and c.localName != name:
            continue
        out.append(c)
    return out


def text_of(el: minidom.Element) -> str:
    """The element's text, only if it has no element children."""
    if child_elements(el):
        raise XmlSecurityError("saml_xml_invalid", "unexpected nested elements")
    return "".join(c.data for c in el.childNodes
                   if c.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


_ID_ATTRS = ("ID", "Id", "id")


def unique_ids(root: minidom.Element) -> None:
    """No two elements may share an ID, whatever the attribute is called.

    A signature points at `#<id>`. If two elements answered to that id, which
    one is "the signed one" would be a matter of lookup order, and that is the
    entire signature-wrapping attack.
    """
    seen: set[str] = set()
    for el in walk(root):
        for attr in _ID_ATTRS:
            if el.hasAttribute(attr):
                value = el.getAttribute(attr)
                if value in seen:
                    raise XmlSecurityError("saml_duplicate_id", "duplicate element ID")
                seen.add(value)


# ── Exclusive canonicalization 1.0 (no comments) ─────────────────────────────

def _escape_text(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("\r", "&#xD;"))


def _escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
             .replace("\t", "&#x9;").replace("\n", "&#xA;").replace("\r", "&#xD;"))


def _declared(el: minidom.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(el.attributes.length):
        a = el.attributes.item(i)
        if a.namespaceURI == XMLNS:
            out["" if a.name == "xmlns" else a.localName] = a.value or ""
    return out


def _in_scope(el: minidom.Element) -> dict[str, str]:
    chain = []
    node = el
    while node is not None and node.nodeType == Node.ELEMENT_NODE:
        chain.append(node)
        node = node.parentNode
    scope: dict[str, str] = {}
    for n in reversed(chain):
        scope.update(_declared(n))
    return scope


def canonicalize(apex: minidom.Element, *, inclusive_prefixes: tuple[str, ...] = (),
                 exclude: minidom.Element | None = None) -> bytes:
    """Exclusive C14N of the subtree at `apex`, leaving out `exclude` (the
    enveloped signature). The apex's namespace context comes from its ancestors
    in the document, as the specification requires for a document subset."""
    out: list[str] = []
    _emit(apex, _in_scope(apex), {}, tuple(inclusive_prefixes), exclude, out)
    return "".join(out).encode("utf-8")


def _emit(el, scope, rendered_above, inclusive, exclude, out) -> None:
    # Iterative would be cleaner, but depth is capped at MAX_DEPTH by parse_xml.
    prefix = el.prefix or ""
    used = {prefix}
    attrs = []
    for i in range(el.attributes.length):
        a = el.attributes.item(i)
        if a.namespaceURI == XMLNS:
            continue
        attrs.append(a)
        if a.prefix and a.prefix != "xml":
            used.add(a.prefix)
    want = set(used)
    for p in inclusive:
        want.add("" if p == "#default" else p)

    rendered = dict(rendered_above)
    decls: list[tuple[str, str]] = []
    for p in sorted(want):
        if p == "xml":
            continue
        uri = scope.get(p)
        if p == "":
            current = rendered.get("", "")
            if (uri or "") != current:
                decls.append(("", uri or ""))
                rendered[""] = uri or ""
        elif uri is not None and rendered.get(p) != uri:
            decls.append((p, uri))
            rendered[p] = uri

    out.append("<" + el.tagName)
    for p, uri in decls:
        out.append(f' xmlns="{_escape_attr(uri)}"' if p == "" else f' xmlns:{p}="{_escape_attr(uri)}"')
    for a in sorted(attrs, key=lambda x: (x.namespaceURI or "", x.localName)):
        out.append(f' {a.name}="{_escape_attr(a.value)}"')
    out.append(">")
    for c in el.childNodes:
        t = c.nodeType
        if t == Node.ELEMENT_NODE:
            if c is exclude:
                continue
            child_scope = dict(scope)
            child_scope.update(_declared(c))
            _emit(c, child_scope, rendered, inclusive, exclude, out)
        elif t in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
            out.append(_escape_text(c.data))
        elif t == Node.PROCESSING_INSTRUCTION_NODE:
            out.append(f"<?{c.target}" + (f" {c.data}" if c.data else "") + "?>")
        # comments are dropped: the algorithm is the one WITHOUT comments
    out.append("</" + el.tagName + ">")


# ── Certificates ─────────────────────────────────────────────────────────────

def load_certificates(ders: list[str]) -> list[x509.Certificate]:
    """The stored certificates (base64 DER), or `saml_certificate_invalid`."""
    certs = []
    for b64 in ders:
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(b64, validate=True))
        except (ValueError, binascii.Error):
            raise XmlSecurityError("saml_certificate_invalid", "stored certificate unreadable") from None
        key = cert.public_key()
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < MIN_RSA_BITS:
            raise XmlSecurityError("saml_certificate_invalid", "stored certificate key not allowed")
        certs.append(cert)
    if not certs:
        raise XmlSecurityError("saml_certificate_invalid", "no certificate configured")
    return certs


# ── Signature verification ───────────────────────────────────────────────────

def _one(parent, name: str) -> minidom.Element:
    found = child_elements(parent, DS, name)
    if len(found) != 1:
        raise XmlSecurityError("saml_signature_invalid", f"expected one {name}")
    return found[0]


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        raise XmlSecurityError("saml_signature_invalid", "bad base64 in signature") from None


def _prefix_list(el: minidom.Element) -> tuple[str, ...]:
    found = child_elements(el, EXC_NS, "InclusiveNamespaces")
    if not found:
        return ()
    if len(found) != 1:
        raise XmlSecurityError("saml_signature_invalid", "bad InclusiveNamespaces")
    raw = found[0].getAttribute("PrefixList")
    return tuple(raw.split())


@dataclass(frozen=True)
class Verified:
    element: minidom.Element
    id: str


def verify_enveloped(signature: minidom.Element, certificates: list[x509.Certificate]) -> Verified:
    """Verify `signature` (a direct child of the element it protects).

    Returns the element that is now known to be signed. Raises
    `XmlSecurityError` with `saml_signature_invalid` for every failure: the
    caller's next step is identical and a precise reason helps only an attacker.
    """
    signed = signature.parentNode
    if signed is None or signed.nodeType != Node.ELEMENT_NODE:
        raise XmlSecurityError("saml_signature_invalid", "signature has no parent")
    kids = [c for c in signature.childNodes if c.nodeType == Node.ELEMENT_NODE]
    names = [(c.namespaceURI, c.localName) for c in kids]
    allowed = [[(DS, "SignedInfo"), (DS, "SignatureValue")],
               [(DS, "SignedInfo"), (DS, "SignatureValue"), (DS, "KeyInfo")]]
    if names not in allowed:
        # Includes `ds:Object`, which can smuggle a second, "signed" document.
        raise XmlSecurityError("saml_signature_invalid", "unexpected signature structure")
    signed_info = kids[0]
    sig_value = _b64(text_of(kids[1]))

    sinfo_kids = [c for c in signed_info.childNodes if c.nodeType == Node.ELEMENT_NODE]
    if [(c.namespaceURI, c.localName) for c in sinfo_kids] != [
            (DS, "CanonicalizationMethod"), (DS, "SignatureMethod"), (DS, "Reference")]:
        raise XmlSecurityError("saml_signature_invalid", "unexpected SignedInfo structure")
    c14n_el, method_el, ref = sinfo_kids
    if c14n_el.getAttribute("Algorithm") != EXC_C14N:
        raise XmlSecurityError("saml_signature_invalid", "canonicalization not supported")
    hash_cls = SIGNATURE_METHODS.get(method_el.getAttribute("Algorithm"))
    if hash_cls is None:
        raise XmlSecurityError("saml_signature_invalid", "signature algorithm not supported")

    # The one Reference must point at the Signature's own parent, by its ID.
    uri = ref.getAttribute("URI")
    signed_id = signed.getAttribute("ID") if signed.hasAttribute("ID") else ""
    if not signed_id or uri != "#" + signed_id:
        raise XmlSecurityError("saml_signature_invalid", "reference does not match the signed element")

    ref_kids = [c for c in ref.childNodes if c.nodeType == Node.ELEMENT_NODE]
    if [(c.namespaceURI, c.localName) for c in ref_kids] != [
            (DS, "Transforms"), (DS, "DigestMethod"), (DS, "DigestValue")]:
        raise XmlSecurityError("saml_signature_invalid", "unexpected Reference structure")
    transforms = child_elements(ref_kids[0], DS, "Transform")
    if len(transforms) != len(child_elements(ref_kids[0])):
        raise XmlSecurityError("saml_signature_invalid", "unexpected transform")
    algs = [t.getAttribute("Algorithm") for t in transforms]
    # A reference with no canonicalization transform means inclusive C14N 1.0,
    # which is not implemented, so the chain must be exactly these two.
    if algs != [ENVELOPED, EXC_C14N]:
        raise XmlSecurityError("saml_signature_invalid", "transforms not supported")
    ref_prefixes = _prefix_list(transforms[1])
    digest_cls = DIGEST_METHODS.get(ref_kids[1].getAttribute("Algorithm"))
    if digest_cls is None:
        raise XmlSecurityError("saml_signature_invalid", "digest algorithm not supported")
    expected_digest = _b64(text_of(ref_kids[2]))

    h = hashes.Hash(digest_cls())
    h.update(canonicalize(signed, inclusive_prefixes=ref_prefixes, exclude=signature))
    if not hmac.compare_digest(h.finalize(), expected_digest):
        raise XmlSecurityError("saml_signature_invalid", "digest mismatch")

    signed_bytes = canonicalize(signed_info, inclusive_prefixes=_prefix_list(c14n_el))
    for cert in certificates:
        try:
            cert.public_key().verify(sig_value, signed_bytes, padding.PKCS1v15(), hash_cls())
            return Verified(signed, signed_id)
        except InvalidSignature:
            continue
    raise XmlSecurityError("saml_signature_invalid", "no trusted key verifies the signature")
