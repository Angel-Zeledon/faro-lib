//! Importing an identity provider's SAML metadata, and producing ours.
//!
//! Import reads what an administrator PASTES (no URL is ever fetched: a
//! metadata URL would be a server-side request to an address a customer chose,
//! which is an SSRF surface this feature does not need). It is namespace-aware,
//! rejects any DOCTYPE outright (no entities, no XXE), accepts exactly one
//! `EntityDescriptor` holding exactly one `IDPSSODescriptor`, and takes from it
//! only three things: the entityID, the HTTP-Redirect sign-on URL, and the
//! signing certificates. The metadata's own signature is not checked: the
//! administrator is the trust anchor, and the certificates are re-validated
//! (`cert.rs`) like any pasted certificate.

use chrono::{DateTime, Utc};
use quick_xml::events::{BytesStart, Event};
use quick_xml::name::ResolveResult;
use quick_xml::NsReader;

pub const MD: &str = "urn:oasis:names:tc:SAML:2.0:metadata";
pub const DS: &str = "http://www.w3.org/2000/09/xmldsig#";
pub const SAML2_PROTOCOL: &str = "urn:oasis:names:tc:SAML:2.0:protocol";
pub const BINDING_REDIRECT: &str = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect";
pub const BINDING_POST: &str = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST";
pub const MAX_METADATA_BYTES: usize = 256 * 1024;
const MAX_CERTS_IN_METADATA: usize = 20;

#[derive(Debug, PartialEq)]
pub enum MetadataError {
    /// Not well-formed, a DOCTYPE, or not an EntityDescriptor.
    Invalid,
    /// No IDPSSODescriptor for SAML 2.0, or more than one.
    NoIdp,
    /// The IdP offers only POST (this product speaks the Redirect binding).
    NoRedirectBinding,
    NoCertificate,
    Expired,
}

#[derive(Debug, PartialEq)]
pub struct IdpMetadata {
    pub entity_id: String,
    pub sso_url: String,
    /// Certificate bodies exactly as found (base64, whitespace stripped).
    pub certificates: Vec<String>,
}

fn attr(e: &BytesStart<'_>, name: &str) -> Option<String> {
    for a in e.attributes().with_checks(true).flatten() {
        if a.key.local_name().as_ref() == name.as_bytes() && a.key.prefix().is_none() {
            return a.unescape_value().ok().map(|v| v.into_owned());
        }
    }
    None
}

fn is_ns(r: &ResolveResult<'_>, ns: &str) -> bool {
    matches!(r, ResolveResult::Bound(n) if n.as_ref() == ns.as_bytes())
}

pub fn parse_idp_metadata(xml: &str, now: DateTime<Utc>) -> Result<IdpMetadata, MetadataError> {
    if xml.len() > MAX_METADATA_BYTES || xml.contains('\0') {
        return Err(MetadataError::Invalid);
    }
    let mut reader = NsReader::from_str(xml);
    reader.config_mut().expand_empty_elements = true;

    let mut path: Vec<(bool, String)> = Vec::new(); // (in md or ds namespace, local name)
    let mut entity_id: Option<String> = None;
    let mut idp_count = 0usize;
    let mut in_idp = false;
    let mut redirect: Option<String> = None;
    let mut post_seen = false;
    let mut certs: Vec<String> = Vec::new();
    let mut key_use_ok = false;
    let mut cert_text: Option<String> = None;

    loop {
        let (resolved, event) = reader.read_resolved_event().map_err(|_| MetadataError::Invalid)?;
        match event {
            Event::DocType(_) => return Err(MetadataError::Invalid),
            Event::Start(e) => {
                let local = String::from_utf8_lossy(e.local_name().as_ref()).into_owned();
                let md = is_ns(&resolved, MD);
                let ds = is_ns(&resolved, DS);
                if path.is_empty() {
                    if !(md && local == "EntityDescriptor") {
                        return Err(MetadataError::Invalid);
                    }
                    let id = attr(&e, "entityID").map(|s| s.trim().to_string()).unwrap_or_default();
                    if id.is_empty() {
                        return Err(MetadataError::Invalid);
                    }
                    if let Some(until) = attr(&e, "validUntil") {
                        let parsed = DateTime::parse_from_rfc3339(until.trim()).map_err(|_| MetadataError::Invalid)?;
                        if parsed.with_timezone(&Utc) <= now {
                            return Err(MetadataError::Expired);
                        }
                    }
                    entity_id = Some(id);
                } else if path.len() == 1 && md && local == "IDPSSODescriptor" {
                    idp_count += 1;
                    let protocols = attr(&e, "protocolSupportEnumeration").unwrap_or_default();
                    in_idp = protocols.split_whitespace().any(|p| p == SAML2_PROTOCOL);
                    if !in_idp {
                        return Err(MetadataError::NoIdp);
                    }
                } else if in_idp && path.len() == 2 && md && local == "SingleSignOnService" {
                    let binding = attr(&e, "Binding").unwrap_or_default();
                    let location = attr(&e, "Location").unwrap_or_default();
                    if binding == BINDING_REDIRECT && redirect.is_none() && !location.trim().is_empty() {
                        redirect = Some(location.trim().to_string());
                    } else if binding == BINDING_POST {
                        post_seen = true;
                    }
                } else if in_idp && path.len() == 2 && md && local == "KeyDescriptor" {
                    key_use_ok = !matches!(attr(&e, "use").as_deref(), Some("encryption"));
                } else if in_idp && ds && local == "X509Certificate" && key_use_ok
                    && path.len() == 5
                    && path[2].1 == "KeyDescriptor" && path[3].1 == "KeyInfo" && path[4].1 == "X509Data"
                {
                    cert_text = Some(String::new());
                }
                path.push((md || ds, local));
            }
            Event::Text(t) => {
                if let Some(buf) = cert_text.as_mut() {
                    let raw = t.decode().map_err(|_| MetadataError::Invalid)?;
                    buf.push_str(&quick_xml::escape::unescape(&raw).map_err(|_| MetadataError::Invalid)?);
                }
            }
            Event::CData(t) => {
                if let Some(buf) = cert_text.as_mut() {
                    buf.push_str(&t.decode().map_err(|_| MetadataError::Invalid)?);
                }
            }
            Event::End(_) => {
                let (_, local) = path.pop().ok_or(MetadataError::Invalid)?;
                if local == "X509Certificate" {
                    if let Some(text) = cert_text.take() {
                        let compact: String = text.chars().filter(|c| !c.is_whitespace()).collect();
                        if !compact.is_empty() {
                            if certs.len() >= MAX_CERTS_IN_METADATA {
                                return Err(MetadataError::Invalid);
                            }
                            certs.push(compact);
                        }
                    }
                }
                if path.len() == 1 && local == "IDPSSODescriptor" {
                    in_idp = false;
                }
                if path.len() == 2 && local == "KeyDescriptor" {
                    key_use_ok = false;
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if !path.is_empty() {
        return Err(MetadataError::Invalid);
    }
    let entity_id = entity_id.ok_or(MetadataError::Invalid)?;
    if idp_count != 1 {
        return Err(MetadataError::NoIdp);
    }
    let Some(sso_url) = redirect else {
        let _ = post_seen;
        return Err(MetadataError::NoRedirectBinding);
    };
    if certs.is_empty() {
        return Err(MetadataError::NoCertificate);
    }
    Ok(IdpMetadata { entity_id, sso_url, certificates: certs })
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// The service provider metadata an administrator uploads to the IdP.
pub fn sp_metadata(entity_id: &str, acs_url: &str) -> String {
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
<md:EntityDescriptor xmlns:md=\"{MD}\" entityID=\"{}\">\n\
  <md:SPSSODescriptor AuthnRequestsSigned=\"false\" WantAssertionsSigned=\"true\" protocolSupportEnumeration=\"{SAML2_PROTOCOL}\">\n\
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>\n\
    <md:AssertionConsumerService Binding=\"{BINDING_POST}\" Location=\"{}\" index=\"0\" isDefault=\"true\"/>\n\
  </md:SPSSODescriptor>\n\
</md:EntityDescriptor>\n",
        esc(entity_id),
        esc(acs_url),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-10-06T00:00:00Z").unwrap().with_timezone(&Utc)
    }

    fn doc(extra_root_attrs: &str, body: &str) -> String {
        format!(
            r#"<?xml version="1.0"?><md:EntityDescriptor xmlns:md="{MD}" xmlns:ds="{DS}" entityID=" https://idp.example.test/meta " {extra_root_attrs}>{body}</md:EntityDescriptor>"#
        )
    }

    const GOOD_BODY: &str = r#"<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
  <md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>
    AAAA
    BBBB
  </ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
  <md:KeyDescriptor use="encryption"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>ENCR</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
  <md:KeyDescriptor><ds:KeyInfo><ds:X509Data><ds:X509Certificate>CCCC</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
  <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="https://idp.example.test/post"/>
  <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.test/redirect"/>
</md:IDPSSODescriptor>"#;

    #[test]
    fn reads_entity_redirect_url_and_signing_certificates_only() {
        let m = parse_idp_metadata(&doc("", GOOD_BODY), now()).unwrap();
        assert_eq!(m.entity_id, "https://idp.example.test/meta");
        assert_eq!(m.sso_url, "https://idp.example.test/redirect");
        // The encryption-only key is not a signing key.
        assert_eq!(m.certificates, vec!["AAAABBBB".to_string(), "CCCC".to_string()]);
    }

    #[test]
    fn prefixes_do_not_matter_namespaces_do() {
        let xml = r#"<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="e">
          <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
            <KeyDescriptor><KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#"><X509Data><X509Certificate>ZZ</X509Certificate></X509Data></KeyInfo></KeyDescriptor>
            <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://i/s"/>
          </IDPSSODescriptor></EntityDescriptor>"#;
        assert_eq!(parse_idp_metadata(xml, now()).unwrap().certificates, vec!["ZZ"]);
        // Same element names in the wrong namespace are not metadata.
        let wrong = xml.replace("urn:oasis:names:tc:SAML:2.0:metadata", "urn:evil");
        assert_eq!(parse_idp_metadata(&wrong, now()), Err(MetadataError::Invalid));
    }

    #[test]
    fn doctype_is_refused_outright() {
        let xml = format!(
            r#"<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]>{}"#,
            doc("", GOOD_BODY)
        );
        assert_eq!(parse_idp_metadata(&xml, now()), Err(MetadataError::Invalid));
    }

    #[test]
    fn structural_refusals() {
        assert_eq!(parse_idp_metadata("", now()), Err(MetadataError::Invalid));
        assert_eq!(parse_idp_metadata("<a/>", now()), Err(MetadataError::Invalid));
        assert_eq!(parse_idp_metadata("<a><b></a>", now()), Err(MetadataError::Invalid));
        // Two IdPs in one document: ambiguous, refused.
        let two = doc("", &format!("{GOOD_BODY}{GOOD_BODY}"));
        assert_eq!(parse_idp_metadata(&two, now()), Err(MetadataError::NoIdp));
        // An SP-only document.
        let sp = doc("", r#"<md:SPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"/>"#);
        assert_eq!(parse_idp_metadata(&sp, now()), Err(MetadataError::NoIdp));
        // SAML 1.1 only.
        let old = doc("", &GOOD_BODY.replace("SAML:2.0:protocol", "SAML:1.1:protocol"));
        assert_eq!(parse_idp_metadata(&old, now()), Err(MetadataError::NoIdp));
        // POST binding only.
        let post_only = doc("", &GOOD_BODY.replace("HTTP-Redirect", "SOAP"));
        assert_eq!(parse_idp_metadata(&post_only, now()), Err(MetadataError::NoRedirectBinding));
        // No signing certificate.
        let no_cert = doc("", &GOOD_BODY.replace("use=\"signing\"", "use=\"encryption\"").replace("<md:KeyDescriptor><ds:KeyInfo><ds:X509Data><ds:X509Certificate>CCCC</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>", ""));
        assert_eq!(parse_idp_metadata(&no_cert, now()), Err(MetadataError::NoCertificate));
        // Empty entityID.
        let noid = doc("", GOOD_BODY).replace(" https://idp.example.test/meta ", "  ");
        assert_eq!(parse_idp_metadata(&noid, now()), Err(MetadataError::Invalid));
    }

    #[test]
    fn valid_until_in_the_past_is_expired() {
        let xml = doc(r#"validUntil="2020-01-01T00:00:00Z""#, GOOD_BODY);
        assert_eq!(parse_idp_metadata(&xml, now()), Err(MetadataError::Expired));
        let xml = doc(r#"validUntil="2030-01-01T00:00:00Z""#, GOOD_BODY);
        assert!(parse_idp_metadata(&xml, now()).is_ok());
        let xml = doc(r#"validUntil="soon""#, GOOD_BODY);
        assert_eq!(parse_idp_metadata(&xml, now()), Err(MetadataError::Invalid));
    }

    #[test]
    fn oversized_input_is_refused() {
        let big = "a".repeat(MAX_METADATA_BYTES + 1);
        assert_eq!(parse_idp_metadata(&big, now()), Err(MetadataError::Invalid));
    }

    #[test]
    fn sp_metadata_escapes_and_names_the_endpoint() {
        let xml = sp_metadata("https://x/sp/t&1", "https://x/acs");
        assert!(xml.contains(r#"entityID="https://x/sp/t&amp;1""#));
        assert!(xml.contains(r#"Location="https://x/acs""#));
        assert!(xml.contains("HTTP-POST"));
        // What we publish, we can read back as well-formed XML.
        let mut r = NsReader::from_str(&xml);
        loop {
            match r.read_resolved_event().unwrap().1 {
                Event::Eof => break,
                _ => {}
            }
        }
    }
}
