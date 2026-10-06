//! Reading an X.509 certificate far enough to decide whether to TRUST it as an
//! identity provider's signing key: it is RSA, the key is at least 2048 bits,
//! it has not expired, and what its fingerprint is.
//!
//! This is not a general certificate parser and it verifies nothing (a signing
//! certificate is trusted because an administrator pasted it, not because a CA
//! signed it). The Python side re-parses the stored certificate with the
//! `cryptography` package on every sign-in and fails closed if it disagrees,
//! so a certificate this accepts that Python cannot read costs a failed
//! sign-in, never a wrongly accepted one.

use base64::Engine;
use chrono::{DateTime, NaiveDate, Utc};
use sha2::{Digest, Sha256};

pub const MIN_RSA_BITS: usize = 2048;
const MAX_CERT_BYTES: usize = 16 * 1024;
const RSA_ENCRYPTION_OID: &[u8] = &[0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01];

#[derive(Debug, PartialEq)]
pub enum CertError {
    Unreadable,
    NotRsa,
    Weak(usize),
    Expired,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CertInfo {
    /// Standard base64 of the DER (what is stored, what Python loads).
    pub der_b64: String,
    pub fingerprint_sha256: String,
    pub not_after: DateTime<Utc>,
    pub rsa_bits: usize,
}

/// (tag, content, rest) of the first TLV in `data`.
fn tlv(data: &[u8]) -> Option<(u8, &[u8], &[u8])> {
    let tag = *data.first()?;
    let first = *data.get(1)?;
    let (len, header) = if first < 0x80 {
        (first as usize, 2)
    } else {
        let n = (first & 0x7f) as usize;
        if n == 0 || n > 3 {
            return None;
        }
        let mut len = 0usize;
        for i in 0..n {
            len = (len << 8) | *data.get(2 + i)? as usize;
        }
        (len, 2 + n)
    };
    let end = header.checked_add(len)?;
    Some((tag, data.get(header..end)?, data.get(end..)?))
}

fn expect(data: &[u8], tag: u8) -> Option<(&[u8], &[u8])> {
    let (t, content, rest) = tlv(data)?;
    (t == tag).then_some((content, rest))
}

fn parse_time(tag: u8, content: &[u8]) -> Option<DateTime<Utc>> {
    let s = std::str::from_utf8(content).ok()?;
    let s = s.strip_suffix('Z')?;
    let (year, rest) = match tag {
        0x17 => {
            // UTCTime: YYMMDDHHMMSS, 00-49 is 20xx, 50-99 is 19xx.
            let yy: i32 = s.get(0..2)?.parse().ok()?;
            (if yy < 50 { 2000 + yy } else { 1900 + yy }, s.get(2..)?)
        }
        0x18 => (s.get(0..4)?.parse().ok()?, s.get(4..)?),
        _ => return None,
    };
    if rest.len() != 10 || !rest.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let p = |a: usize| -> Option<u32> { rest.get(a..a + 2)?.parse().ok() };
    let d = NaiveDate::from_ymd_opt(year, p(0)?, p(2)?)?.and_hms_opt(p(4)?, p(6)?, p(8)?)?;
    Some(DateTime::from_naive_utc_and_offset(d, Utc))
}

/// Accept a PEM block, or bare base64, with any whitespace inside.
pub fn decode_der(input: &str) -> Option<Vec<u8>> {
    let body: String = input
        .lines()
        .map(str::trim)
        .filter(|l| !l.starts_with("-----"))
        .collect::<Vec<_>>()
        .concat();
    let body: String = body.chars().filter(|c| !c.is_whitespace()).collect();
    let der = base64::engine::general_purpose::STANDARD.decode(body).ok()?;
    (!der.is_empty() && der.len() <= MAX_CERT_BYTES).then_some(der)
}

/// Full check for a certificate being SAVED: readable, RSA, strong, unexpired.
pub fn inspect(input: &str, now: DateTime<Utc>) -> Result<CertInfo, CertError> {
    let info = describe(input)?;
    if info.rsa_bits < MIN_RSA_BITS {
        return Err(CertError::Weak(info.rsa_bits));
    }
    if info.not_after <= now {
        return Err(CertError::Expired);
    }
    Ok(info)
}

/// What a certificate says, without judging it: used to SHOW stored ones
/// (an expired certificate must still be displayable so it can be replaced).
pub fn describe(input: &str) -> Result<CertInfo, CertError> {
    let der = decode_der(input).ok_or(CertError::Unreadable)?;
    let u = || CertError::Unreadable;
    let (cert, trailing) = expect(&der, 0x30).ok_or_else(u)?;
    if !trailing.is_empty() {
        return Err(CertError::Unreadable);
    }
    let (tbs, _) = expect(cert, 0x30).ok_or_else(u)?;
    let mut rest = tbs;
    // [0] EXPLICIT version, optional.
    if rest.first() == Some(&0xa0) {
        rest = tlv(rest).ok_or_else(u)?.2;
    }
    let (_, r) = expect(rest, 0x02).ok_or_else(u)?; // serialNumber
    let (_, r) = expect(r, 0x30).ok_or_else(u)?; // signature algorithm
    let (_, r) = expect(r, 0x30).ok_or_else(u)?; // issuer
    let (validity, r) = expect(r, 0x30).ok_or_else(u)?;
    let (_, r) = expect(r, 0x30).ok_or_else(u)?; // subject
    let (spki, _) = expect(r, 0x30).ok_or_else(u)?;

    // validity ::= SEQUENCE { notBefore, notAfter }
    let (_, _, after_not_before) = tlv(validity).ok_or_else(u)?;
    let (t, c, _) = tlv(after_not_before).ok_or_else(u)?;
    let not_after = parse_time(t, c).ok_or_else(u)?;

    let (alg, r) = expect(spki, 0x30).ok_or_else(u)?;
    let (oid, _) = expect(alg, 0x06).ok_or_else(u)?;
    if oid != RSA_ENCRYPTION_OID {
        return Err(CertError::NotRsa);
    }
    let (bits, _) = expect(r, 0x03).ok_or_else(u)?;
    // BIT STRING: the first byte counts unused bits (0 for a key).
    let key = bits.get(1..).ok_or_else(u)?;
    let (rsa_key, _) = expect(key, 0x30).ok_or_else(u)?;
    let (modulus, _) = expect(rsa_key, 0x02).ok_or_else(u)?;
    let skip = modulus.iter().take_while(|b| **b == 0).count();
    let significant = &modulus[skip..];
    let rsa_bits = match significant.first() {
        Some(first) => (significant.len() - 1) * 8 + (8 - first.leading_zeros() as usize),
        None => 0,
    };
    Ok(CertInfo {
        der_b64: base64::engine::general_purpose::STANDARD.encode(&der),
        fingerprint_sha256: hex::encode(Sha256::digest(&der)),
        not_after,
        rsa_bits,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::saml::test_certs as fx;

    #[test]
    fn a_good_rsa_certificate_is_read() {
        let info = inspect(fx::RSA2048, Utc::now()).unwrap();
        assert_eq!(info.rsa_bits, 2048);
        assert_eq!(info.fingerprint_sha256.len(), 64);
        assert!(info.not_after > Utc::now());
        assert_eq!(info.der_b64, fx::RSA2048);
    }

    #[test]
    fn pem_and_wrapped_base64_are_accepted_and_normalised() {
        let wrapped: String = fx::RSA2048
            .as_bytes()
            .chunks(64)
            .map(|c| std::str::from_utf8(c).unwrap())
            .collect::<Vec<_>>()
            .join("\n");
        let pem = format!("-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n");
        assert_eq!(inspect(&pem, Utc::now()).unwrap().der_b64, fx::RSA2048);
        assert_eq!(inspect(&wrapped, Utc::now()).unwrap().der_b64, fx::RSA2048);
    }

    #[test]
    fn weak_ec_expired_and_garbage_are_refused() {
        assert_eq!(inspect(fx::RSA1024, Utc::now()), Err(CertError::Weak(1024)));
        assert_eq!(inspect(fx::EC, Utc::now()), Err(CertError::NotRsa));
        assert_eq!(inspect(fx::EXPIRED, Utc::now()), Err(CertError::Expired));
        for bad in ["", "not base64!!", "AAAA", "MIIB"] {
            assert_eq!(inspect(bad, Utc::now()), Err(CertError::Unreadable), "{bad}");
        }
        // Trailing bytes after the certificate are not a certificate.
        let mut der = base64::engine::general_purpose::STANDARD.decode(fx::RSA2048).unwrap();
        der.push(0);
        let padded = base64::engine::general_purpose::STANDARD.encode(der);
        assert_eq!(inspect(&padded, Utc::now()), Err(CertError::Unreadable));
    }

    #[test]
    fn expiry_is_judged_against_the_clock_given() {
        let far = Utc::now() + chrono::Duration::days(365 * 30);
        assert_eq!(inspect(fx::RSA2048, far), Err(CertError::Expired));
    }
}
