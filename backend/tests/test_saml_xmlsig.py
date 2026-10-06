"""SAML assertion validation: the trust decision, attacked from every side.

No database and no HTTP here: `validate_response` is a pure function of (the
posted document, what this sign-in expected, the trusted certificates). Every
test either shows a valid document being accepted, or one specific attack being
refused WITH THE CODE THAT NAMES IT, never merely "some error": an attack that
is refused for the wrong reason is an attack one refactor away from working.

Documents are hand-written canonical text (see `saml_fixtures.py`), so the
signatures were not produced by the code under test.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import pytest

from backend.auth.saml import response as R
from backend.auth.saml import xmlsig
from backend.auth.social.providers import SocialAuthError
from backend.tests import saml_fixtures as fx
from backend.tests.saml_fixtures import A, DS, EXC, P, b64

SP = "https://app.example.test/api/v1/auth/saml/sp/ten_victim"
ACS = "https://app.example.test/api/v1/auth/saml/acs"
REQ = "_req-1234"


@pytest.fixture(scope="module")
def idp():
    return fx.FakeIdp()


@pytest.fixture(scope="module")
def attacker():
    return fx.FakeIdp()


def expected(idp, **over):
    kw = dict(sp_entity_id=SP, acs_url=ACS, idp_entity_id=idp.entity_id, request_id=REQ,
              certificates=[idp.cert_b64])
    kw.update(over)
    return R.Expected(**kw)


def signed(idp, email="ana@victim.example.com", sign="assertion", **kw):
    return idp.signed_response(request_id=REQ, sp_entity=SP, acs=ACS, email=email, sign=sign, **kw)


def code_of(idp, xml, **over) -> str:
    with pytest.raises(SocialAuthError) as exc:
        R.validate_response(b64(xml), expected(idp, **over))
    return exc.value.code


def accept(idp, xml, **over) -> R.SamlClaims:
    return R.validate_response(b64(xml), expected(idp, **over))


# ── What a good response looks like ──────────────────────────────────────────

class TestAccepted:
    @pytest.mark.parametrize("sign", ["assertion", "response", "both"])
    def test_a_signed_response_is_accepted_whichever_element_carries_the_signature(self, idp, sign):
        claims = accept(idp, signed(idp, sign=sign))
        assert claims.email == "ana@victim.example.com"
        assert claims.subject == "ana@victim.example.com"

    def test_attributes_name_and_groups_are_read(self, idp):
        xml = signed(idp, assertion_kw=dict(attributes={
            "email": ["ana@victim.example.com"], "displayName": ["Ana Pérez"],
            "groups": ["Ops", "Finance"]}))
        claims = accept(idp, xml)
        assert claims.full_name == "Ana Pérez"
        assert claims.raw["groups"] == ["Ops", "Finance"]

    def test_email_comes_from_the_named_attribute_when_the_tenant_names_one(self, idp):
        xml = signed(idp, assertion_kw=dict(
            name_id="opaque-7f3a", name_id_format=fx.PERSISTENT,
            attributes={"corp_mail": ["Ana@Victim.Example.com"]}))
        assert accept(idp, xml, email_attribute="corp_mail").email == "ana@victim.example.com"
        assert accept(idp, xml, email_attribute="corp_mail").subject == "opaque-7f3a"
        # Not naming it, with an opaque NameID and no well-known attribute: no e-mail.
        assert accept(idp, xml).email is None

    def test_an_opaque_name_id_with_a_well_known_email_attribute(self, idp):
        xml = signed(idp, assertion_kw=dict(name_id="u-1", name_id_format=fx.PERSISTENT,
                                            attributes={"mail": ["ana@victim.example.com"]}))
        assert accept(idp, xml).email == "ana@victim.example.com"

    def test_a_rolled_over_certificate_set_verifies_with_either_key(self, idp, attacker):
        xml = signed(idp)
        both = [attacker.cert_b64, idp.cert_b64]
        assert accept(idp, xml, certificates=both).email

    def test_the_signing_certificate_in_keyinfo_is_ignored_the_stored_one_decides(self, idp, attacker):
        # Signed by the attacker, who helpfully embeds THEIR certificate.
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="evil@victim.example.com")
        sig = attacker.signature_block(
            a_text, aid, extra_children="")
        sig = sig.replace("</ds:Signature>",
                          f"<ds:KeyInfo><ds:X509Data><ds:X509Certificate>{attacker.cert_b64}"
                          f"</ds:X509Certificate></ds:X509Data></ds:KeyInfo></ds:Signature>")
        a_text = idp.put_signature(a_text, sig)
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        assert code_of(idp, r_text) == "saml_signature_invalid"


# ── Signature failures ───────────────────────────────────────────────────────

class TestSignatures:
    def test_unsigned_is_refused(self, idp):
        assert code_of(idp, signed(idp, sign="none")) == "saml_unsigned"

    def test_signed_by_another_key_is_refused(self, idp, attacker):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        a_text = idp.put_signature(a_text, attacker.signature_block(a_text, aid))
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_content_changed_after_signing_is_refused(self, idp):
        xml = signed(idp, email="ana@victim.example.com")
        evil = xml.replace("ana@victim.example.com", "boss@victim.example.com")
        assert evil != xml
        assert code_of(idp, evil) == "saml_signature_invalid"

    def test_a_changed_signature_value_is_refused(self, idp):
        xml = signed(idp)
        i = xml.index("<ds:SignatureValue>") + len("<ds:SignatureValue>")
        flipped = xml[:i] + ("A" if xml[i] != "A" else "B") + xml[i + 1:]
        assert code_of(idp, flipped) == "saml_signature_invalid"

    def test_a_change_inside_a_signed_response_that_hides_in_the_other_signed_element(self, idp):
        # Both signed; edit only what the Response signature covers outside the assertion.
        xml = signed(idp, sign="both")
        evil = xml.replace("Success", "Responder", 1)
        assert code_of(idp, evil) == "saml_signature_invalid"

    def test_sha1_signature_and_digest_are_refused(self, idp):
        xml = signed(idp, sig_kw=dict(alg=fx.RSA1))
        assert code_of(idp, xml) == "saml_signature_invalid"
        xml = signed(idp, sig_kw=dict(digest_alg=fx.DIG1))
        assert code_of(idp, xml) == "saml_signature_invalid"

    @pytest.mark.parametrize("transforms", [
        [fx.ENV],                                                   # no canonicalization: inclusive c14n
        [fx.EXC],                                                   # not enveloped
        [fx.EXC, fx.ENV],                                           # wrong order
        [fx.ENV, "http://www.w3.org/TR/1999/REC-xpath-19991116", fx.EXC],
        [fx.ENV, "http://www.w3.org/TR/1999/REC-xslt-19991116"],
        [fx.ENV, "http://www.w3.org/2001/10/xml-exc-c14n#WithComments"],
        [fx.ENV, "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"],
        [],
    ])
    def test_only_enveloped_then_exclusive_c14n_is_accepted(self, idp, transforms):
        xml = signed(idp, sig_kw=dict(transforms=transforms))
        assert code_of(idp, xml) == "saml_signature_invalid"

    def test_the_reference_must_be_the_signatures_own_parent(self, idp):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        # Reference points at a different (made up) id; the digest is right for the assertion.
        a_text = idp.put_signature(a_text, idp.signature_block(a_text, aid, uri="#_other"))
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        assert code_of(idp, r_text) == "saml_signature_invalid"
        # Empty URI means "the whole document": not supported, not guessed.
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        a_text = idp.put_signature(a_text, idp.signature_block(a_text, aid, uri=""))
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_a_response_signature_that_references_the_assertion_is_refused(self, idp):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        r_text, rid = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        sig = idp.signature_block(a_text, aid)  # digest of the assertion, URI of the assertion
        assert code_of(idp, idp.put_signature(r_text, sig)) == "saml_signature_invalid"

    def test_a_signature_object_element_is_refused(self, idp):
        evil, _ = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="boss@victim.example.com")
        xml = signed(idp, sig_kw=dict(extra_children=f"<ds:Object>{evil}</ds:Object>"))
        # Refused for the structure (and, independently, because it is a second Assertion).
        assert code_of(idp, xml) in ("saml_signature_invalid", "saml_assertion_invalid")

    def test_a_benign_object_element_inside_the_signature_is_refused(self, idp):
        xml = signed(idp, sig_kw=dict(extra_children="<ds:Object>harmless</ds:Object>"))
        assert code_of(idp, xml) == "saml_signature_invalid"

    def test_two_references_are_refused(self, idp):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        sig = idp.signature_block(a_text, aid)
        extra = ('<ds:Reference URI="#_evil"><ds:Transforms></ds:Transforms>'
                 f'<ds:DigestMethod Algorithm="{fx.DIG256}"></ds:DigestMethod>'
                 '<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference>')
        sig = sig.replace("</ds:Reference></ds:SignedInfo>", f"</ds:Reference>{extra}</ds:SignedInfo>")
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=idp.put_signature(a_text, sig))
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_a_certificate_that_cannot_be_read_fails_closed(self, idp):
        assert code_of(idp, signed(idp), certificates=["not-a-cert"]) == "saml_certificate_invalid"
        assert code_of(idp, signed(idp), certificates=[]) == "saml_certificate_invalid"

    def test_a_weak_stored_key_is_not_trusted(self):
        weak = fx.FakeIdp(*fx.make_identity(bits=1024))
        xml = weak.signed_response(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        assert code_of(weak, xml) == "saml_certificate_invalid"


# ── Signature wrapping (XSW) ─────────────────────────────────────────────────

class TestSignatureWrapping:
    """The classic shapes: a validly signed assertion is kept in the document
    and an attacker's assertion is put where the application will read it."""

    def _pair(self, idp):
        good, gid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="ana@victim.example.com")
        good_signed = idp.put_signature(good, idp.signature_block(good, gid))
        evil, eid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="boss@victim.example.com")
        return good_signed, gid, evil, eid

    def test_evil_unsigned_assertion_first_signed_one_in_extensions(self, idp):
        good, gid, evil, _ = self._pair(idp)
        wrapped = f'<samlp:Extensions xmlns:samlp="{P}">{good}</samlp:Extensions>{evil}'
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=wrapped)
        assert code_of(idp, r_text) == "saml_assertion_invalid"

    def test_signed_assertion_nested_in_the_evil_ones_advice(self, idp):
        good, gid, evil, _ = self._pair(idp)
        evil = evil.replace("</saml:Assertion>", f"<saml:Advice>{good}</saml:Advice></saml:Assertion>")
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil)
        assert code_of(idp, r_text) == "saml_assertion_invalid"

    def test_two_sibling_assertions_one_signed(self, idp):
        good, _, evil, _ = self._pair(idp)
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil + good)
        assert code_of(idp, r_text) == "saml_assertion_invalid"
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=good + evil)
        assert code_of(idp, r_text) == "saml_assertion_invalid"

    def test_the_evil_assertion_reuses_the_signed_assertions_id(self, idp):
        good, gid, _, _ = self._pair(idp)
        evil, _ = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="boss@victim.example.com",
                                assertion_id=gid)
        # Same ID on two elements: refused before any signature is even looked at.
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil + good)
        assert code_of(idp, r_text) in ("saml_duplicate_id", "saml_assertion_invalid")
        xml = f'<samlp:Extensions xmlns:samlp="{P}">{good}</samlp:Extensions>'
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil + xml)
        assert code_of(idp, r_text) in ("saml_duplicate_id", "saml_assertion_invalid")

    def test_an_evil_assertion_carrying_a_copied_signature(self, idp):
        good, gid, evil, eid = self._pair(idp)
        sig = good[good.index("<ds:Signature"):good.index("</ds:Signature>") + len("</ds:Signature>")]
        evil = idp.put_signature(evil.replace(eid, gid), sig)
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil)
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_a_signature_somewhere_other_than_response_or_assertion_is_refused(self, idp):
        good, gid, evil, _ = self._pair(idp)
        sig = good[good.index("<ds:Signature"):good.index("</ds:Signature>") + len("</ds:Signature>")]
        # Signature hidden in Subject (a place only an attack puts one).
        evil = evil.replace("</saml:Subject>", f"{sig}</saml:Subject>")
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=evil)
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_an_unsigned_assertion_inside_a_signed_response_is_covered_and_accepted(self, idp):
        # The legitimate shape that XSW defenses must not break: Response-level signing.
        assert accept(idp, signed(idp, sign="response")).email == "ana@victim.example.com"

    def test_a_signed_response_cannot_be_extended_with_a_second_assertion(self, idp):
        xml = signed(idp, sign="response")
        evil, _ = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="boss@victim.example.com")
        tampered = xml.replace("</samlp:Response>", evil + "</samlp:Response>")
        assert code_of(idp, tampered) in ("saml_signature_invalid", "saml_assertion_invalid")

    def test_the_only_assertion_hidden_inside_extensions_is_refused(self, idp):
        # One assertion, validly covered by a signed Response, but not where the
        # protocol puts it: it must be a direct child of the Response.
        a_text, _ = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        wrapped = f'<samlp:Extensions xmlns:samlp="{P}">{a_text}</samlp:Extensions>'
        r_text, rid = idp.response(request_id=REQ, acs=ACS, assertion_text=wrapped)
        r_text = idp.put_signature(r_text, idp.signature_block(r_text, rid))
        assert code_of(idp, r_text) == "saml_assertion_invalid"

    def test_an_id_shared_with_any_other_element_is_refused_even_with_one_assertion(self, idp):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        a_text = idp.put_signature(a_text, idp.signature_block(a_text, aid))
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        r_text = r_text.replace("<samlp:Status>", f'<samlp:Status ID="{aid}">', 1)
        assert code_of(idp, r_text) == "saml_duplicate_id"

    def test_a_validly_signed_signature_in_an_unexpected_place_is_refused(self, idp):
        advice_core = f'<saml:Advice xmlns:saml="{A}" ID="_adv1"></saml:Advice>'
        sig = idp.signature_block(advice_core, "_adv1")
        advice = f'<saml:Advice ID="_adv1">{sig}</saml:Advice>'
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        a_text = a_text.replace("</saml:Assertion>", advice + "</saml:Assertion>")
        a_text = idp.put_signature(a_text, idp.signature_block(a_text, aid))
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_every_signature_must_verify_not_just_the_first(self, idp, attacker):
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        a_text = idp.put_signature(a_text, attacker.signature_block(a_text, aid))   # forged, inner
        r_text, rid = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text)
        r_text = idp.put_signature(r_text, idp.signature_block(r_text, rid))        # genuine, outer
        assert code_of(idp, r_text) == "saml_signature_invalid"

    def test_comment_injection_cannot_truncate_the_identity(self, idp):
        # The IdP signs "ana@victim.example.com.evil.test". A parser that read only the
        # first text node would see "ana@victim.example.com". Comments are not part of the
        # canonical form, so the signature stays valid; the claim must be the WHOLE text.
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS,
                                    email="ana@victim.example.com.evil.test", attributes={})
        signature = idp.signature_block(a_text, aid)
        in_doc = a_text.replace("ana@victim.example.com.evil.test</saml:NameID>",
                                "ana@victim.example.com<!-- x -->.evil.test</saml:NameID>")
        in_doc = idp.put_signature(in_doc, signature)
        r_text, _ = idp.response(request_id=REQ, acs=ACS, assertion_text=in_doc)
        claims = accept(idp, r_text)
        assert claims.email == "ana@victim.example.com.evil.test"


# ── The SAML rules ───────────────────────────────────────────────────────────

def refused(idp, code, *, assertion_kw=None, response_kw=None, sign="assertion", **over):
    xml = signed(idp, sign=sign, assertion_kw=assertion_kw, response_kw=response_kw)
    assert code_of(idp, xml, **over) == code


class TestRules:
    def test_the_wrong_audience_is_refused(self, idp):
        refused(idp, "saml_audience_mismatch", assertion_kw=dict(audience="https://other.example/sp"))

    def test_another_tenants_audience_is_refused(self, idp):
        other = "https://app.example.test/api/v1/auth/saml/sp/ten_other"
        xml = signed(idp, assertion_kw=dict(audience=other))
        assert code_of(idp, xml) == "saml_audience_mismatch"

    def test_no_audience_restriction_is_refused(self, idp):
        refused(idp, "saml_audience_mismatch", assertion_kw=dict(with_audience=False))

    def test_every_audience_restriction_must_include_us(self, idp):
        extra = f'<saml:AudienceRestriction><saml:Audience>https://x.test/sp</saml:Audience></saml:AudienceRestriction>'
        refused(idp, "saml_audience_mismatch", assertion_kw=dict(conditions_extra=extra))

    def test_an_audience_list_that_contains_us_is_accepted(self, idp):
        xml = signed(idp, assertion_kw=dict(with_audience=False, conditions_extra=(
            f'<saml:AudienceRestriction><saml:Audience>https://x.test/sp</saml:Audience>'
            f'<saml:Audience>{SP}</saml:Audience></saml:AudienceRestriction>')))
        assert accept(idp, xml).email

    def test_the_wrong_recipient_is_refused(self, idp):
        refused(idp, "saml_recipient_mismatch", assertion_kw=dict(recipient="https://evil.test/acs"))

    def test_the_wrong_destination_is_refused(self, idp):
        refused(idp, "saml_destination_mismatch", response_kw=dict(destination="https://evil.test/acs"))
        refused(idp, "saml_destination_mismatch", response_kw=dict(destination=""))

    def test_in_response_to_must_match_on_the_response(self, idp):
        refused(idp, "saml_in_response_to_mismatch", response_kw=dict(in_response_to="_somebody-elses"))
        refused(idp, "saml_in_response_to_mismatch", response_kw=dict(in_response_to=""))

    def test_in_response_to_must_match_on_the_bearer_confirmation(self, idp):
        refused(idp, "saml_in_response_to_mismatch", assertion_kw=dict(scd_in_response_to="_other"))
        refused(idp, "saml_in_response_to_mismatch", assertion_kw=dict(scd_in_response_to=""))

    def test_an_idp_initiated_response_has_no_request_to_answer(self, idp):
        # A flow whose request id the response does not mention: the same refusal.
        assert code_of(idp, signed(idp), request_id="_a-request-we-never-sent") == "saml_in_response_to_mismatch"

    def test_the_wrong_issuer_is_refused(self, idp):
        refused(idp, "saml_issuer_mismatch", assertion_kw=dict(issuer="https://evil-idp.test/meta"))
        refused(idp, "saml_issuer_mismatch", response_kw=dict(issuer="https://evil-idp.test/meta"))
        assert code_of(idp, signed(idp), idp_entity_id="https://another-idp.test/") == "saml_issuer_mismatch"

    def test_an_expired_assertion_is_refused_beyond_the_clock_skew(self, idp):
        refused(idp, "saml_expired", assertion_kw=dict(not_on_or_after=-300, cond_not_on_or_after=300))
        refused(idp, "saml_expired", assertion_kw=dict(cond_not_on_or_after=-300))
        assert accept(idp, signed(idp, assertion_kw=dict(not_on_or_after=-60, cond_not_on_or_after=-60))).email

    def test_an_assertion_not_yet_valid_is_refused_beyond_the_clock_skew(self, idp):
        refused(idp, "saml_not_yet_valid", assertion_kw=dict(cond_not_before=600))
        refused(idp, "saml_not_yet_valid", assertion_kw=dict(scd_not_before=600))
        refused(idp, "saml_not_yet_valid", assertion_kw=dict(issue_instant=600))
        assert accept(idp, signed(idp, assertion_kw=dict(cond_not_before=60))).email

    def test_a_bearer_confirmation_without_an_end_time_is_refused(self, idp):
        refused(idp, "saml_time_invalid", assertion_kw=dict(with_scd_not_on_or_after=False))
        refused(idp, "saml_time_invalid", assertion_kw=dict(cond_not_on_or_after=None))

    def test_conditions_are_required(self, idp):
        refused(idp, "saml_conditions_invalid", assertion_kw=dict(with_conditions=False))

    def test_an_unknown_condition_fails_the_assertion(self, idp):
        refused(idp, "saml_conditions_invalid", assertion_kw=dict(conditions_extra=(
            '<saml:Condition xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="Weird"></saml:Condition>')))

    def test_a_known_harmless_condition_is_accepted(self, idp):
        assert accept(idp, signed(idp, assertion_kw=dict(conditions_extra="<saml:OneTimeUse></saml:OneTimeUse>"))).email

    def test_only_a_bearer_confirmation_counts(self, idp):
        refused(idp, "saml_recipient_mismatch", assertion_kw=dict(bearer=False))

    def test_a_session_that_already_ended_is_refused(self, idp):
        refused(idp, "saml_expired", assertion_kw=dict(session_not_on_or_after=-600))

    def test_an_authn_statement_is_required(self, idp):
        refused(idp, "saml_assertion_invalid", assertion_kw=dict(with_authn=False))

    def test_failure_status_is_refused_and_a_user_decline_is_told_apart(self, idp):
        refused(idp, "saml_status_not_success",
                response_kw=dict(status="urn:oasis:names:tc:SAML:2.0:status:Requester"))
        refused(idp, "saml_idp_refused",
                response_kw=dict(status="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed"))

    def test_a_transient_name_id_cannot_identify_anyone(self, idp):
        refused(idp, "saml_nameid_transient",
                assertion_kw=dict(name_id="_transient-1", name_id_format=fx.TRANSIENT))

    def test_an_sp_name_qualifier_for_another_service_is_refused(self, idp):
        refused(idp, "saml_audience_mismatch", assertion_kw=dict(sp_name_qualifier="https://other.test/sp"))
        assert accept(idp, signed(idp, assertion_kw=dict(sp_name_qualifier=SP))).email

    def test_a_missing_or_empty_name_id_is_refused(self, idp):
        refused(idp, "saml_subject_invalid", assertion_kw=dict(name_id=" ", attributes={}))

    def test_versions_other_than_2_0_are_refused(self, idp):
        refused(idp, "saml_assertion_invalid", assertion_kw=dict(version="1.1"))
        refused(idp, "saml_response_invalid", response_kw=dict(version="1.1"))

    def test_encrypted_content_is_not_supported_and_says_so(self, idp):
        xml = signed(idp, sign="response")
        # An EncryptedAssertion beside the signed assertion changes the signed
        # content, so sign the final text: wrap an encrypted assertion inside.
        a_text, aid = idp.assertion(request_id=REQ, sp_entity=SP, acs=ACS, email="a@victim.example.com")
        enc = f'<saml:EncryptedAssertion xmlns:saml="{A}"></saml:EncryptedAssertion>'
        r_text, rid = idp.response(request_id=REQ, acs=ACS, assertion_text=a_text + enc)
        r_text = idp.put_signature(r_text, idp.signature_block(r_text, rid))
        assert code_of(idp, r_text) == "saml_encryption_unsupported"
        assert xml  # the unmodified one is fine
        assert accept(idp, xml).email

    def test_a_response_with_no_assertion_is_refused(self, idp):
        r_text, rid = idp.response(request_id=REQ, acs=ACS, assertion_text="")
        r_text = idp.put_signature(r_text, idp.signature_block(r_text, rid))
        assert code_of(idp, r_text) == "saml_assertion_invalid"

    def test_the_wrong_root_is_refused(self, idp):
        xml = (f'<samlp:LogoutResponse xmlns:samlp="{P}" ID="_x" Version="2.0"></samlp:LogoutResponse>')
        assert code_of(idp, xml) == "saml_response_invalid"


# ── Parsing hardening ────────────────────────────────────────────────────────

class TestParsing:
    def test_a_doctype_is_refused_before_it_can_expand_or_fetch(self, idp):
        xml = signed(idp)
        for dtd in (
            '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
            '<!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>',
            '<!DOCTYPE r SYSTEM "http://evil.test/x.dtd">',
        ):
            assert code_of(idp, dtd + xml) == "saml_xml_invalid"
        assert code_of(idp, "<?xml version='1.0'?>\n<!doctype r>" + xml) == "saml_xml_invalid"

    def test_utf16_and_other_encodings_are_refused(self, idp):
        xml = signed(idp)
        with pytest.raises(SocialAuthError) as e:
            R.validate_response(base64.b64encode(("﻿" + xml).encode("utf-16")).decode(), expected(idp))
        assert e.value.code == "saml_xml_invalid"
        latin = "<?xml version='1.0' encoding='ISO-8859-1'?>" + xml
        assert code_of(idp, latin) == "saml_xml_invalid"

    def test_not_base64_not_xml_empty_and_oversized(self, idp):
        for raw, code in [("!!!not base64!!!", "saml_response_invalid"), (None, "saml_response_missing"),
                          ("", "saml_response_missing"),
                          (base64.b64encode(b"<a><b></a>").decode(), "saml_xml_invalid"),
                          (base64.b64encode(b"just text").decode(), "saml_xml_invalid"),
                          (base64.b64encode(b"x" * (xmlsig.MAX_XML_BYTES + 1)).decode(), "saml_response_too_large")]:
            with pytest.raises(SocialAuthError) as e:
                R.validate_response(raw, expected(idp))
            assert e.value.code == code, raw and raw[:20]

    def test_a_deeply_nested_document_is_refused_without_recursing(self, idp):
        deep = "<a>" * 5000 + "</a>" * 5000
        assert code_of(idp, deep) == "saml_xml_invalid"

    def test_whitespace_in_the_base64_is_tolerated(self, idp):
        raw = b64(signed(idp))
        wrapped = "\n".join(raw[i:i + 76] for i in range(0, len(raw), 76))
        assert R.validate_response(wrapped, expected(idp)).email

    def test_duplicate_attributes_are_refused_by_the_parser(self, idp):
        xml = signed(idp).replace('Version="2.0">', 'Version="2.0" Version="2.0">', 1)
        assert code_of(idp, xml) == "saml_xml_invalid"


# ── Canonicalization against an independent implementation ───────────────────

def exc_c14n(xml: str, selector=None, **kw) -> str:
    doc = xmlsig.parse_xml(xml.encode())
    el = doc.documentElement
    if selector:
        el = next(e for e in xmlsig.walk(el) if e.tagName == selector)
    return xmlsig.canonicalize(el, **kw).decode()


class TestCanonicalization:
    """Python's `ElementTree.canonicalize` is C14N 2.0, an independent
    implementation that agrees with exclusive C14N when no namespace is
    declared that its element does not use. Where they agree we compare;
    where exclusive differs, the expected text is written out by hand."""

    CASES = [
        '<a xmlns="urn:x" b="2" a="1"><c>text &amp; more</c><d/></a>',
        '<p:a xmlns:p="urn:p"><p:b q="1" p="2"> spaced </p:b></p:a>',
        '<a><b>&lt;tag&gt; "quoted" \'single\'</b><c x="a&quot;b&#9;c&#10;d"/></a>',
        '<a xmlns="urn:one"><b xmlns="urn:two"><c xmlns="urn:one"/></b></a>',
        '<x:a xmlns:x="urn:x" xmlns:y="urn:y"><y:b y:attr="1" x:attr="2"/></x:a>',
        '<a><![CDATA[<raw & text>]]></a>',
        '<a><?pi some data?><b/></a>',
        '<a>line1&#13;line2</a>',
    ]

    @pytest.mark.parametrize("xml", CASES)
    def test_agrees_with_elementtree_c14n(self, xml):
        assert exc_c14n(xml) == ET.canonicalize(xml)

    def test_comments_are_dropped(self):
        assert exc_c14n("<a>x<!-- gone -->y</a>") == "<a>xy</a>"

    def test_unused_namespace_declarations_are_not_output(self):
        xml = '<a xmlns:unused="urn:u" xmlns:p="urn:p"><p:b/></a>'
        assert exc_c14n(xml) == '<a><p:b xmlns:p="urn:p"></p:b></a>'

    def test_a_subtree_gets_the_namespaces_it_uses_from_its_ancestors(self):
        xml = '<r xmlns:p="urn:p" xmlns:q="urn:q"><s><p:leaf q:a="1"/></s></r>'
        assert exc_c14n(xml, "p:leaf") == '<p:leaf xmlns:p="urn:p" xmlns:q="urn:q" q:a="1"></p:leaf>'
        # ...and does not drag in what it does not use.
        assert exc_c14n(xml, "s") == '<s><p:leaf xmlns:p="urn:p" xmlns:q="urn:q" q:a="1"></p:leaf></s>'

    def test_default_namespace_undeclaration_is_output_only_when_needed(self):
        xml = '<a xmlns="urn:d"><b xmlns=""><c/></b></a>'
        assert exc_c14n(xml) == '<a xmlns="urn:d"><b xmlns=""><c></c></b></a>'
        # An apex with no default namespace at all outputs no xmlns="".
        assert exc_c14n('<a xmlns=""><b/></a>') == "<a><b></b></a>"

    def test_the_inclusive_prefix_list_forces_declarations_out(self):
        xml = '<a xmlns:keep="urn:k" xmlns:p="urn:p"><p:b/></a>'
        assert exc_c14n(xml, inclusive_prefixes=("keep",)) == (
            '<a xmlns:keep="urn:k"><p:b xmlns:p="urn:p"></p:b></a>')
        assert exc_c14n('<a xmlns="urn:d"><b xmlns="urn:d"/></a>', inclusive_prefixes=("#default",)) == (
            '<a xmlns="urn:d"><b></b></a>')

    def test_xml_namespace_attributes_are_not_inherited_by_an_exclusive_subtree(self):
        xml = '<r xml:lang="en"><s a="1"/></r>'
        assert exc_c14n(xml, "s") == '<s a="1"></s>'

    def test_attribute_order_is_by_namespace_uri_then_local_name(self):
        xml = '<a xmlns:z="urn:a" xmlns:b="urn:z" b:x="1" z:y="2" plain="3"/>'
        # No-namespace attributes first, then by URI: urn:a (z:y) before urn:z (b:x).
        assert exc_c14n(xml) == '<a xmlns:b="urn:z" xmlns:z="urn:a" plain="3" z:y="2" b:x="1"></a>'

    def test_the_enveloped_signature_is_left_out_and_nothing_else(self):
        xml = f'<a xmlns:ds="{DS}">x<ds:Signature>sig</ds:Signature>y</a>'
        doc = xmlsig.parse_xml(xml.encode())
        root = doc.documentElement
        sig = next(e for e in xmlsig.walk(root) if e.localName == "Signature")
        assert xmlsig.canonicalize(root, exclude=sig).decode() == "<a>xy</a>"
