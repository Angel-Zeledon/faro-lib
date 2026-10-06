//! The one call this service makes to the Python API: the report preview.
//!
//! `POST {PYTHON_API_URL}/internal/scheduled-reports/render`, signed with an
//! HMAC of `SECRET_KEY` over the timestamp and the SHA-256 of the body (see
//! `backend/scheduled_reports/internal_api.py`). The sections of a report stand
//! on code that stays in Python on purpose, and the preview must say exactly
//! what the mail will say, so it asks the same builder.
//!
//! A deliberately tiny HTTP/1.1 client over a TCP stream: the target is the
//! sibling container on the private network (plain `http://`), the exchange is
//! one request and one JSON answer, and this keeps TLS and a full HTTP client
//! out of the binary. `https://` targets are refused, not silently downgraded.

use std::time::Duration;

use hmac::{Hmac, Mac};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

pub const DEFAULT_URL: &str = "http://127.0.0.1:8010";
const TIMEOUT: Duration = Duration::from_secs(60);
const MAX_BODY: usize = 8 * 1024 * 1024;

#[derive(Debug)]
#[allow(dead_code)] // the payloads are for the error log (`{:?}`)
pub enum CallError {
    /// The URL is not a plain `http://host:port`.
    BadUrl,
    /// Could not connect, timed out, or the answer was not HTTP.
    Unreachable(String),
    /// Python answered, with this status.
    Status(u16, String),
    /// Python answered 200 with something that is not JSON.
    NotJson,
}

/// `http://host:port` -> (host:port for connecting, Host header).
pub fn parse_base(url: &str) -> Option<(String, String)> {
    let rest = url.trim().strip_prefix("http://")?;
    let authority = rest.split('/').next()?;
    if authority.is_empty() || authority.contains('@') {
        return None;
    }
    let with_port = if authority.contains(':') { authority.to_string() } else { format!("{authority}:80") };
    Some((with_port, authority.to_string()))
}

pub fn signature(secret: &str, timestamp: &str, body: &[u8]) -> String {
    let digest = hex::encode(Sha256::digest(body));
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("hmac accepts any key length");
    mac.update(format!("internal-render|{timestamp}|{digest}").as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Decode a chunked transfer-encoded body.
fn dechunk(mut raw: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let eol = raw.windows(2).position(|w| w == b"\r\n")?;
        let size_text = std::str::from_utf8(&raw[..eol]).ok()?.split(';').next()?.trim();
        let size = usize::from_str_radix(size_text, 16).ok()?;
        raw = &raw[eol + 2..];
        if size == 0 {
            return Some(out);
        }
        if raw.len() < size + 2 {
            return None;
        }
        out.extend_from_slice(&raw[..size]);
        raw = &raw[size + 2..];
    }
}

/// Split a raw HTTP/1.x response into (status, body).
pub fn parse_response(raw: &[u8]) -> Option<(u16, Vec<u8>)> {
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = std::str::from_utf8(&raw[..split]).ok()?;
    let mut lines = head.split("\r\n");
    let status: u16 = lines.next()?.split(' ').nth(1)?.parse().ok()?;
    let chunked = lines.any(|l| {
        let l = l.to_ascii_lowercase();
        l.starts_with("transfer-encoding:") && l.contains("chunked")
    });
    let body = &raw[split + 4..];
    Some((status, if chunked { dechunk(body)? } else { body.to_vec() }))
}

pub async fn render(base_url: &str, secret: &str, payload: &Value) -> Result<Value, CallError> {
    let (connect_to, host) = parse_base(base_url).ok_or(CallError::BadUrl)?;
    let body = serde_json::to_vec(payload).map_err(|e| CallError::Unreachable(e.to_string()))?;
    let timestamp = format!("{:.3}", chrono::Utc::now().timestamp_millis() as f64 / 1000.0);
    let request = format!(
        "POST /internal/scheduled-reports/render HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nX-Internal-Timestamp: {timestamp}\r\nX-Internal-Signature: {}\r\n\
         Connection: close\r\n\r\n",
        body.len(),
        signature(secret, &timestamp, &body),
    );
    let exchange = async {
        let mut stream = tokio::net::TcpStream::connect(&connect_to).await.map_err(|e| e.to_string())?;
        stream.write_all(request.as_bytes()).await.map_err(|e| e.to_string())?;
        stream.write_all(&body).await.map_err(|e| e.to_string())?;
        let mut raw = Vec::new();
        let mut limited = stream.take(MAX_BODY as u64);
        limited.read_to_end(&mut raw).await.map_err(|e| e.to_string())?;
        Ok::<Vec<u8>, String>(raw)
    };
    let raw = tokio::time::timeout(TIMEOUT, exchange)
        .await
        .map_err(|_| CallError::Unreachable("timeout".into()))?
        .map_err(CallError::Unreachable)?;
    let (status, body) = parse_response(&raw).ok_or_else(|| CallError::Unreachable("not an HTTP response".into()))?;
    if status != 200 {
        return Err(CallError::Status(status, String::from_utf8_lossy(&body).chars().take(200).collect()));
    }
    serde_json::from_slice(&body).map_err(|_| CallError::NotJson)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urls() {
        assert_eq!(parse_base("http://api:8010"), Some(("api:8010".into(), "api:8010".into())));
        assert_eq!(parse_base("http://127.0.0.1:8011/"), Some(("127.0.0.1:8011".into(), "127.0.0.1:8011".into())));
        assert_eq!(parse_base("http://api"), Some(("api:80".into(), "api".into())));
        assert_eq!(parse_base("https://api:8010"), None);
        assert_eq!(parse_base("api:8010"), None);
        assert_eq!(parse_base("http://user@api:8010"), None);
        assert_eq!(parse_base("http://"), None);
    }

    /// The same vector `internal_api.sign` produces in Python (pinned in
    /// backend/tests/test_scheduled_reports.py::TestInternalRenderAuth).
    #[test]
    fn the_signature_is_the_python_signature() {
        assert_eq!(signature("k", "1800000000.0", b"{\"a\":1}"), PINNED);
    }

    const PINNED: &str = "263db5cf08e56b77b22456e01e8198f84221272de31275eb590d58838b5c34b4";

    #[test]
    fn responses() {
        let plain = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n{}";
        assert_eq!(parse_response(plain), Some((200, b"{}".to_vec())));
        let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{\"a\"\r\n3\r\n:1}\r\n0\r\n\r\n";
        assert_eq!(parse_response(chunked), Some((200, b"{\"a\":1}".to_vec())));
        assert_eq!(parse_response(b"HTTP/1.1 401 Unauthorized\r\n\r\n{\"detail\":\"x\"}").unwrap().0, 401);
        assert_eq!(parse_response(b"garbage"), None);
    }
}
