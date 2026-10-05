//! HS256 access-token verification, identical to `backend/auth/jwt_handler.py`
//! (`decode_token`), which is PyJWT 2.13 `jwt.decode(token, SECRET_KEY,
//! algorithms=["HS256"], options={"verify_exp": True})`.
//!
//! Hand-rolled on `hmac` + `sha2` instead of the `jsonwebtoken` crate on
//! purpose. The English `detail` of a 401 is part of the wire contract (the
//! error-code bridge in `backend/error_codes.py` matches on it, and API
//! clients read it), and it is PyJWT's own wording: "Token expired",
//! "Invalid token: Signature verification failed", "Invalid token: Not enough
//! segments", ... `jsonwebtoken` has its own error taxonomy, its own claim
//! rules (60 s default leeway, `exp` required, integer-only timestamps) and
//! would need a translation table that is wrong in exactly the corners nobody
//! tests. Replaying PyJWT's steps in the same order is shorter and auditable.
//!
//! Python mints `iat` and `exp` as FLOATS (sub-second, see the comment in
//! `create_access_token`); PyJWT validates them with `int(value)` against a
//! float `now`. Both quirks are reproduced.

use base64::alphabet;
use base64::engine::{DecodePaddingMode, GeneralPurpose, GeneralPurposeConfig};
use base64::Engine;
use hmac::{Hmac, Mac};
use serde_json::{Map, Value};
use sha2::Sha256;

const URL_SAFE_LENIENT: GeneralPurpose = GeneralPurpose::new(
    &alphabet::URL_SAFE,
    GeneralPurposeConfig::new()
        .with_decode_padding_mode(DecodePaddingMode::Indifferent)
        .with_decode_allow_trailing_bits(true),
);

/// Why a token was refused, already worded as Python's `decode_token` words it
/// (the `detail` of the 401).
#[derive(Debug, Clone, PartialEq)]
pub struct JwtRejected(pub String);

/// PyJWT's `base64url_decode`: pad to a multiple of 4, then the non-validating
/// `urlsafe_b64decode`, which silently drops characters outside the alphabet.
fn b64url_decode(segment: &str) -> Option<Vec<u8>> {
    let cleaned: String = segment
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect();
    if cleaned.len() % 4 == 1 {
        return None;
    }
    URL_SAFE_LENIENT.decode(cleaned.as_bytes()).ok()
}

fn invalid(msg: &str) -> JwtRejected {
    JwtRejected(format!("Invalid token: {msg}"))
}

/// `int(value)` as PyJWT applies it to a numeric claim. `None` means Python
/// would have raised (and PyJWT turns that into its "must be an integer").
fn py_int(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => n.as_f64().filter(|f| f.is_finite()).map(f64::trunc),
        Value::String(s) => {
            let t = s.trim().replace('_', "");
            t.parse::<i64>().ok().map(|i| i as f64)
        }
        _ => None,
    }
}

/// Decode and verify; `now` is the current UNIX time as a float.
pub fn decode_token(token: &str, secret: &[u8], now: f64) -> Result<Map<String, Value>, JwtRejected> {
    // api_jws._load
    let (signing_input, crypto_segment) = token
        .rsplit_once('.')
        .ok_or_else(|| invalid("Not enough segments"))?;
    let (header_segment, payload_segment) = signing_input
        .split_once('.')
        .ok_or_else(|| invalid("Not enough segments"))?;
    let header_data = b64url_decode(header_segment).ok_or_else(|| invalid("Invalid header padding"))?;
    let header: Value = serde_json::from_slice(&header_data)
        .map_err(|e| invalid(&format!("Invalid header string: {e}")))?;
    let header = header
        .as_object()
        .cloned()
        .ok_or_else(|| invalid("Invalid header string: must be a json object"))?;
    let payload_bytes = b64url_decode(payload_segment).ok_or_else(|| invalid("Invalid payload padding"))?;
    let signature = b64url_decode(crypto_segment).ok_or_else(|| invalid("Invalid crypto padding"))?;

    // api_jws.decode_complete: RFC 7797 unencoded payloads are never minted by
    // StockAI; refuse them with PyJWT's wording.
    if header.get("b64") == Some(&Value::Bool(false)) {
        let crit_has_b64 = header
            .get("crit")
            .and_then(Value::as_array)
            .map(|a| a.iter().any(|v| v == "b64"))
            .unwrap_or(false);
        if !crit_has_b64 {
            return Err(invalid("The 'b64' header parameter requires 'b64' to be listed in 'crit'."));
        }
        return Err(invalid(
            "It is required that you pass in a value for the \"detached_payload\" argument to decode a message having the b64 header set to false.",
        ));
    }

    // api_jws._verify_signature
    let alg = header.get("alg").ok_or_else(|| invalid("Algorithm not specified"))?;
    if alg.as_str() != Some("HS256") {
        return Err(invalid("The specified alg value is not allowed"));
    }
    let mut mac = Hmac::<Sha256>::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(signing_input.as_bytes());
    if mac.verify_slice(&signature).is_err() {
        return Err(invalid("Signature verification failed"));
    }

    // api_jwt._decode_payload
    let payload: Value = serde_json::from_slice(&payload_bytes)
        .map_err(|e| invalid(&format!("Invalid payload string: {e}")))?;
    let payload = payload
        .as_object()
        .cloned()
        .ok_or_else(|| invalid("Invalid payload string: must be a json object"))?;

    // api_jwt._validate_claims, in PyJWT's order, leeway 0.
    if let Some(iat) = payload.get("iat") {
        let iat = py_int(iat).ok_or_else(|| invalid("Issued At claim (iat) must be an integer."))?;
        if iat > now {
            return Err(invalid("The token is not yet valid (iat)"));
        }
    }
    if let Some(nbf) = payload.get("nbf") {
        let nbf = py_int(nbf).ok_or_else(|| invalid("Not Before claim (nbf) must be an integer."))?;
        if nbf > now {
            return Err(invalid("The token is not yet valid (nbf)"));
        }
    }
    if let Some(exp) = payload.get("exp") {
        let exp = py_int(exp).ok_or_else(|| invalid("Expiration Time claim (exp) must be an integer."))?;
        if exp <= now {
            // ExpiredSignatureError is translated by jwt_handler.decode_token.
            return Err(JwtRejected("Token expired".into()));
        }
    }
    // No audience is passed by StockAI, so any non-empty `aud` is refused.
    if payload.get("aud").map(crate::pycompat::truthy).unwrap_or(false) {
        return Err(invalid("Invalid audience"));
    }
    if let Some(sub) = payload.get("sub") {
        if !sub.is_string() {
            return Err(invalid("Subject must be a string"));
        }
    }
    if let Some(jti) = payload.get("jti") {
        if !jti.is_string() {
            return Err(invalid("JWT ID must be a string"));
        }
    }
    Ok(payload)
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use serde_json::json;

    /// Mint a token the way `create_access_token` does (tests only).
    pub fn mint(payload: &Value, secret: &[u8], alg: &str) -> String {
        let eng = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        let header = json!({"alg": alg, "typ": "JWT"});
        let h = eng.encode(serde_json::to_vec(&header).unwrap());
        let p = eng.encode(serde_json::to_vec(payload).unwrap());
        let signing_input = format!("{h}.{p}");
        let mut mac = Hmac::<Sha256>::new_from_slice(secret).unwrap();
        mac.update(signing_input.as_bytes());
        let sig = eng.encode(mac.finalize().into_bytes());
        format!("{signing_input}.{sig}")
    }

    const NOW: f64 = 1_800_000_000.5;

    fn access(exp_offset: f64) -> Value {
        json!({"sub": "usr_1", "tenant_id": "t_1", "role": "admin", "email_verified": true,
               "jti": "abcd", "type": "access", "iat": NOW - 1.25, "exp": NOW + exp_offset})
    }

    #[test]
    fn a_valid_token_decodes() {
        let t = mint(&access(900.0), b"secret", "HS256");
        let p = decode_token(&t, b"secret", NOW).unwrap();
        assert_eq!(p["tenant_id"], "t_1");
    }

    #[test]
    fn wrong_secret_is_a_signature_failure() {
        let t = mint(&access(900.0), b"secret", "HS256");
        assert_eq!(
            decode_token(&t, b"other", NOW).unwrap_err().0,
            "Invalid token: Signature verification failed"
        );
    }

    #[test]
    fn expiry_uses_int_of_the_float_claim() {
        // exp = NOW + 0.4 -> int() truncates to below `now` -> expired, as in PyJWT.
        let t = mint(&access(0.4), b"secret", "HS256");
        assert_eq!(decode_token(&t, b"secret", NOW).unwrap_err().0, "Token expired");
        let t = mint(&access(2.0), b"secret", "HS256");
        assert!(decode_token(&t, b"secret", NOW).is_ok());
    }

    #[test]
    fn other_algorithms_are_refused() {
        let t = mint(&access(900.0), b"secret", "HS512");
        assert_eq!(
            decode_token(&t, b"secret", NOW).unwrap_err().0,
            "Invalid token: The specified alg value is not allowed"
        );
    }

    #[test]
    fn malformed_tokens_use_pyjwt_wording() {
        assert_eq!(decode_token("abc", b"s", NOW).unwrap_err().0, "Invalid token: Not enough segments");
        assert_eq!(decode_token("a.b", b"s", NOW).unwrap_err().0, "Invalid token: Not enough segments");
        assert_eq!(decode_token("a.b.c", b"s", NOW).unwrap_err().0, "Invalid token: Invalid header padding");
    }

    #[test]
    fn future_iat_is_immature() {
        let mut p = access(900.0);
        p["iat"] = json!(NOW + 30.0);
        let t = mint(&p, b"secret", "HS256");
        assert_eq!(
            decode_token(&t, b"secret", NOW).unwrap_err().0,
            "Invalid token: The token is not yet valid (iat)"
        );
    }
}
