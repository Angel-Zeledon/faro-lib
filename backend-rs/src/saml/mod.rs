//! SAML 2.0 single sign-on, configuration side (docs/rust-migration.md, "SAML").
//!
//! * `cert.rs`     deciding whether a pasted certificate may be trusted
//! * `metadata.rs` importing IdP metadata, producing SP metadata
//! * `rules.rs`    the tenant rules shared with OIDC (domains, group mapping)
//!
//! The routes are in `routes/saml.rs`. The sign-in itself (AuthnRequest, ACS,
//! assertion validation, session issuance) stays in Python
//! (`backend/auth/saml/`): see that package's docstring for why.

pub mod cert;
pub mod metadata;
pub mod rules;
#[cfg(test)]
pub mod test_certs;
