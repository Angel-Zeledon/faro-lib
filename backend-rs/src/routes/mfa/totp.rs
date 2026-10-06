//! TOTP (RFC 6238 over RFC 4226, HMAC-SHA1), recovery codes and the secret's
//! encryption: the Rust twin of `backend/auth/mfa.py`.
//!
//! Python verifies the codes at sign-in and Rust enrols and manages them, so
//! the two MUST agree bit for bit. `backend-rs/test-vectors/mfa.json` is read
//! by this module's tests and by `backend/tests/test_mfa.py`: the RFC 6238
//! appendix B vectors, the acceptance window, the recovery-code HMAC and a
//! Fernet token written by Python's `cryptography`.

use hmac::{Hmac, Mac};
use sha1::Sha1;
use sha2::Sha256;

pub const PERIOD_SECONDS: i64 = 30;
pub const DIGITS: u32 = 6;
/// Steps accepted either side of "now" (one: a phone clock up to a period off).
pub const WINDOW_STEPS: i64 = 1;

const B32: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
/// 32 symbols without lookalikes would need 5 bits too; this alphabet drops
/// 0/O/1/I so a code read off a printout is not ambiguous. Exactly 32 long, so
/// `byte & 31` is uniform.
const RECOVERY_ALPHABET: &[u8; 32] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
pub const RECOVERY_CODE_COUNT: usize = 10;

/// RFC 4648 base32, no padding.
pub fn base32_encode(data: &[u8]) -> String {
    let mut out = String::new();
    let (mut buf, mut bits) = (0u32, 0u32);
    for &b in data {
        buf = (buf << 8) | u32::from(b);
        bits += 8;
        while bits >= 5 {
            out.push(B32[((buf >> (bits - 5)) & 31) as usize] as char);
            bits -= 5;
        }
    }
    if bits > 0 {
        out.push(B32[((buf << (5 - bits)) & 31) as usize] as char);
    }
    out
}

/// Base32 decode as `backend/auth/mfa.py::_decode_secret`: spaces ignored,
/// case-insensitive, padding optional.
pub fn base32_decode(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let (mut buf, mut bits) = (0u32, 0u32);
    for c in s.chars().filter(|c| *c != ' ' && *c != '=') {
        let c = c.to_ascii_uppercase();
        let v = B32.iter().position(|&x| x as char == c)? as u32;
        buf = (buf << 5) | v;
        bits += 5;
        if bits >= 8 {
            out.push((buf >> (bits - 8)) as u8);
            bits -= 8;
            buf &= (1 << bits) - 1;
        }
    }
    Some(out)
}

/// RFC 4226 HOTP with dynamic truncation.
pub fn hotp(secret_b32: &str, counter: u64, digits: u32) -> Option<String> {
    let key = base32_decode(secret_b32)?;
    let mut mac = <Hmac<Sha1> as Mac>::new_from_slice(&key).ok()?;
    mac.update(&counter.to_be_bytes());
    let digest = mac.finalize().into_bytes();
    let offset = (digest[digest.len() - 1] & 0x0f) as usize;
    let value = u32::from_be_bytes([digest[offset], digest[offset + 1], digest[offset + 2], digest[offset + 3]])
        & 0x7fff_ffff;
    let modulus = 10u64.pow(digits);
    Some(format!("{:0width$}", u64::from(value) % modulus, width = digits as usize))
}

pub fn time_step(now_secs: i64) -> i64 {
    now_secs.div_euclid(PERIOD_SECONDS)
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// The time-step `code` is valid for, or None: `mfa.matching_step`. A step at
/// or before `last_used_step` never matches (one code opens one login), and
/// every candidate is compared in constant time even after a match.
pub fn matching_step(secret_b32: &str, code: &str, now_secs: i64, last_used_step: Option<i64>, digits: u32) -> Option<i64> {
    let code: String = code.trim().chars().filter(|c| *c != ' ').collect();
    if code.len() != digits as usize || !code.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let current = time_step(now_secs);
    let mut found = None;
    for step in (current - WINDOW_STEPS)..=(current + WINDOW_STEPS) {
        if step < 0 {
            continue;
        }
        let expected = hotp(secret_b32, step as u64, digits)?;
        let ok = ct_eq(expected.as_bytes(), code.as_bytes());
        if ok && last_used_step.map_or(true, |l| step > l) && found.is_none() {
            found = Some(step);
        }
    }
    found
}

pub fn looks_like_totp(code: &str) -> bool {
    let c: String = code.trim().chars().filter(|c| *c != ' ').collect();
    c.len() == DIGITS as usize && c.bytes().all(|b| b.is_ascii_digit())
}

/// `mfa.normalize_recovery_code`.
pub fn normalize_recovery_code(code: &str) -> String {
    code.to_uppercase().chars().filter(|c| !" -\t\r\n".contains(*c)).collect()
}

/// `mfa.recovery_code_hash`: HMAC-SHA256 keyed with SECRET_KEY over
/// `"mfa-recovery:" + normalised code`, hex.
pub fn recovery_code_hash(secret_key: &str, code: &str) -> String {
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(secret_key.as_bytes()).expect("hmac takes any key length");
    mac.update(format!("mfa-recovery:{}", normalize_recovery_code(code)).as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

pub fn os_random(buf: &mut [u8]) -> Result<(), getrandom::Error> {
    getrandom::getrandom(buf)
}

/// A 160-bit secret, base32 without padding.
pub fn generate_secret() -> Result<String, getrandom::Error> {
    let mut b = [0u8; 20];
    os_random(&mut b)?;
    Ok(base32_encode(&b))
}

/// `XXXXX-XXXXX`, 50 bits each.
pub fn generate_recovery_codes() -> Result<Vec<String>, getrandom::Error> {
    let mut codes = Vec::with_capacity(RECOVERY_CODE_COUNT);
    for _ in 0..RECOVERY_CODE_COUNT {
        let mut b = [0u8; 10];
        os_random(&mut b)?;
        let chars: Vec<char> = b.iter().map(|x| RECOVERY_ALPHABET[(x & 31) as usize] as char).collect();
        codes.push(format!("{}-{}", chars[..5].iter().collect::<String>(), chars[5..].iter().collect::<String>()));
    }
    Ok(codes)
}

/// `otpauth://totp/<issuer>:<account>?...` for an authenticator app.
pub fn otpauth_uri(issuer: &str, account: &str, secret_b32: &str) -> String {
    const KEEP: &percent_encoding::AsciiSet =
        &percent_encoding::NON_ALPHANUMERIC.remove(b'.').remove(b'-').remove(b'_');
    let enc = |s: &str| percent_encoding::utf8_percent_encode(s, KEEP).to_string();
    format!(
        "otpauth://totp/{}:{}?secret={}&issuer={}&algorithm=SHA1&digits={}&period={}",
        enc(issuer), enc(account), secret_b32, enc(issuer), DIGITS, PERIOD_SECONDS
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn vectors() -> Value {
        serde_json::from_str(include_str!("../../../test-vectors/mfa.json")).unwrap()
    }

    #[test]
    fn rfc6238_appendix_b_sha1_vectors() {
        let v = vectors();
        let secret = v["rfc6238"]["secret_base32"].as_str().unwrap();
        // The base32 secret is the RFC's ASCII secret.
        assert_eq!(base32_decode(secret).unwrap(), b"12345678901234567890");
        assert_eq!(base32_encode(b"12345678901234567890"), secret);
        for case in v["rfc6238"]["cases"].as_array().unwrap() {
            let t = case["time"].as_i64().unwrap();
            let code8 = case["code8"].as_str().unwrap();
            let step = time_step(t) as u64;
            assert_eq!(hotp(secret, step, 8).unwrap(), code8, "T={t}");
            // The 6-digit code is the last six digits of the 8-digit one.
            assert_eq!(hotp(secret, step, 6).unwrap(), code8[2..], "T={t} (6 digits)");
        }
    }

    #[test]
    fn rfc4226_appendix_d_counter_vectors() {
        let secret = base32_encode(b"12345678901234567890");
        let expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"];
        for (counter, code) in expected.iter().enumerate() {
            assert_eq!(hotp(&secret, counter as u64, 6).unwrap(), *code);
        }
    }

    #[test]
    fn window_accepts_one_step_either_side_and_no_more() {
        let v = vectors();
        let w = &v["window"];
        let secret = w["secret_base32"].as_str().unwrap();
        let now = w["now"].as_i64().unwrap();
        for off in w["accepted_offsets"].as_array().unwrap() {
            let off = off.as_i64().unwrap();
            let code = hotp(secret, (time_step(now) + off) as u64, 6).unwrap();
            assert_eq!(matching_step(secret, &code, now, None, 6), Some(time_step(now) + off), "offset {off}");
        }
        for off in w["rejected_offsets"].as_array().unwrap() {
            let off = off.as_i64().unwrap();
            let code = hotp(secret, (time_step(now) + off) as u64, 6).unwrap();
            assert_eq!(matching_step(secret, &code, now, None, 6), None, "offset {off}");
        }
    }

    #[test]
    fn a_used_step_is_never_accepted_again() {
        let secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
        let now = 1_111_111_111;
        let step = time_step(now);
        let code = hotp(secret, step as u64, 6).unwrap();
        assert_eq!(matching_step(secret, &code, now, None, 6), Some(step));
        assert_eq!(matching_step(secret, &code, now, Some(step), 6), None);
        // An earlier code of the window is also dead once a later step was used.
        let earlier = hotp(secret, (step - 1) as u64, 6).unwrap();
        assert_eq!(matching_step(secret, &earlier, now, Some(step), 6), None);
        // The next step still works.
        let later = hotp(secret, (step + 1) as u64, 6).unwrap();
        assert_eq!(matching_step(secret, &later, now, Some(step), 6), Some(step + 1));
    }

    #[test]
    fn malformed_codes_never_match() {
        let secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
        for bad in ["", "12345", "1234567", "12345a", "١٢٣٤٥٦", "      "] {
            assert_eq!(matching_step(secret, bad, 1_111_111_111, None, 6), None, "{bad:?}");
        }
        // Spaces inside are tolerated, like authenticator apps print them.
        let code = hotp(secret, time_step(1_111_111_111) as u64, 6).unwrap();
        let spaced = format!("{} {}", &code[..3], &code[3..]);
        assert!(matching_step(secret, &spaced, 1_111_111_111, None, 6).is_some());
    }

    #[test]
    fn recovery_hash_matches_python_and_ignores_formatting() {
        let v = vectors();
        let key = v["recovery"]["secret_key"].as_str().unwrap();
        for case in v["recovery"]["cases"].as_array().unwrap() {
            assert_eq!(
                recovery_code_hash(key, case["code"].as_str().unwrap()),
                case["hash"].as_str().unwrap()
            );
        }
        assert_eq!(recovery_code_hash(key, "abcde-fghjk"), recovery_code_hash(key, "ABCDE FGHJK"));
    }

    #[test]
    fn python_fernet_token_decrypts_here() {
        let v = vectors();
        let f = fernet::Fernet::new(v["fernet"]["key"].as_str().unwrap()).unwrap();
        let plain = f.decrypt(v["fernet"]["token"].as_str().unwrap()).unwrap();
        assert_eq!(String::from_utf8(plain).unwrap(), v["fernet"]["plaintext"].as_str().unwrap());
    }

    #[test]
    fn generated_material_has_the_documented_shape() {
        let s = generate_secret().unwrap();
        assert_eq!(s.len(), 32);
        assert_eq!(base32_decode(&s).unwrap().len(), 20);
        let codes = generate_recovery_codes().unwrap();
        assert_eq!(codes.len(), 10);
        let unique: std::collections::BTreeSet<_> = codes.iter().collect();
        assert_eq!(unique.len(), 10);
        for c in &codes {
            assert_eq!(c.len(), 11);
            assert_eq!(&c[5..6], "-");
            assert!(c.chars().filter(|x| *x != '-').all(|x| RECOVERY_ALPHABET.contains(&(x as u8))));
        }
    }

    #[test]
    fn otpauth_uri_is_what_authenticators_parse() {
        let uri = otpauth_uri("StockAI", "ana+x@example.com", "JBSWY3DPEHPK3PXP");
        assert_eq!(
            uri,
            "otpauth://totp/StockAI:ana%2Bx%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=StockAI&algorithm=SHA1&digits=6&period=30"
        );
    }
}
