"""How StockAI reads a company's file drop: SFTP and S3-compatible storage.

Both speak the same four verbs, so the poller never knows which it is talking to:
list the files, read one, move one into `processed/` or `rejected/`, and write a
small note beside a rejected file. Nothing else is ever done to the company's
server: no delete of a file we have not first copied, no overwrite, no listing of
anything outside the watched directory or prefix.

Every address a company types goes through the same SSRF check as a SQL data
source (`datasources.network`): the host is resolved, every answer is judged, and
the connection is made to an address that was checked.

SFTP pins the server's host key (trust on first use). A key that differs from the
pinned one is refused until an admin accepts the new one. Paramiko 4.0.0 accepts
RSA signatures made with SHA-1 (CVE-2026-44405, no release with the fix yet), so
`ssh-rsa` is disabled here; `rsa-sha2-256/512`, ed25519 and ecdsa remain.

S3 is signed with SigV4 over httpx (no SDK). It is written from the published
protocol and exercised against a local S3-compatible server in the tests; it has
not been run against AWS itself, and the screen says so.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import socket
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

from backend.datasources import network
from backend.errors import AppError

CONNECT_TIMEOUT_S = 15
IO_TIMEOUT_S = 60
MAX_LISTED = 1000
_ANY = 400


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    mtime: float            # seconds since the epoch


class Transport(Protocol):
    def list_files(self) -> list[RemoteFile]: ...
    def read(self, name: str, max_bytes: int) -> bytes: ...
    def move(self, name: str, folder: str) -> None: ...
    def put_text(self, folder: str, name: str, text: str) -> None: ...
    def close(self) -> None: ...


def _err(code: str, message: str, status: int = _ANY, **params) -> AppError:
    return AppError(code, message, status_code=status, params=params)


def _too_large(max_bytes: int) -> AppError:
    return _err("file_drop_file_too_large", "The file is larger than the plan allows",
                max_mb=round(max_bytes / 1024 / 1024, 1))


# ── SFTP ─────────────────────────────────────────────────────────────────────

def fingerprint(key) -> str:
    """OpenSSH-style SHA256 fingerprint of a paramiko host key."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class SftpTransport:
    """`pinned` is the stored fingerprint, or None on the first connection, in
    which case `observed_fingerprint` is what the caller shows an admin to pin."""

    def __init__(self, cfg: dict, secrets: dict, pinned: Optional[str]):
        import paramiko

        host, port = cfg["host"], int(cfg.get("port") or 22)
        addresses = network.resolve_allowed(
            host, port, allow_private=network.allow_private_hosts())
        sock = None
        last: Optional[Exception] = None
        for ip in addresses:
            try:
                sock = socket.create_connection((ip.compressed, port), timeout=CONNECT_TIMEOUT_S)
                break
            except OSError as exc:
                last = exc
        if sock is None:
            raise _err("file_drop_connection_failed", "Could not reach the SFTP server",
                       host=host, port=port, detail=type(last).__name__ if last else None)
        sock.settimeout(IO_TIMEOUT_S)
        self.observed_fingerprint: Optional[str] = None
        self._sock = sock
        try:
            self._t = paramiko.Transport(
                sock, disabled_algorithms={"keys": ["ssh-rsa"], "pubkeys": ["ssh-rsa"]})
            self._t.start_client(timeout=CONNECT_TIMEOUT_S)
            self.observed_fingerprint = fingerprint(self._t.get_remote_server_key())
            if pinned and self.observed_fingerprint != pinned:
                raise _err("file_drop_host_key_changed",
                           "The server's host key is not the one that was pinned",
                           pinned=pinned, observed=self.observed_fingerprint)
            self._authenticate(paramiko, cfg, secrets)
            self._sftp = paramiko.SFTPClient.from_transport(self._t)
            self._sftp.get_channel().settimeout(IO_TIMEOUT_S)
        except AppError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise _err("file_drop_connection_failed", "The SFTP handshake failed",
                       host=host, port=port, detail=type(exc).__name__) from exc
        self._base = (cfg.get("path") or ".").rstrip("/") or "."

    def _authenticate(self, paramiko, cfg: dict, secrets: dict) -> None:
        user = cfg["username"]
        try:
            pem = secrets.get("private_key")
            if pem:
                key = None
                for loader in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                    try:
                        key = loader.from_private_key(io.StringIO(pem),
                                                     password=secrets.get("passphrase") or None)
                        break
                    except Exception:
                        continue
                if key is None:
                    raise _err("file_drop_key_unreadable", "The private key cannot be read")
                self._t.auth_publickey(user, key)
            else:
                self._t.auth_password(user, secrets.get("password") or "")
        except AppError:
            raise
        except paramiko.AuthenticationException as exc:
            raise _err("file_drop_auth_failed", "The SFTP server refused the credentials",
                       status=401) from exc

    def _path(self, *parts: str) -> str:
        return "/".join([self._base, *parts]) if self._base != "." else "/".join(parts)

    def list_files(self) -> list[RemoteFile]:
        try:
            entries = self._sftp.listdir_attr(self._base)
        except FileNotFoundError as exc:
            raise _err("file_drop_path_not_found", "The directory does not exist",
                       path=self._base) from exc
        except PermissionError as exc:
            raise _err("file_drop_permission_denied", "The account cannot list this directory",
                       path=self._base) from exc
        out = [RemoteFile(e.filename, int(e.st_size or 0), float(e.st_mtime or 0))
               for e in entries if e.st_mode is not None and stat.S_ISREG(e.st_mode)]
        return out[:MAX_LISTED]

    def read(self, name: str, max_bytes: int) -> bytes:
        with self._sftp.open(self._path(name), "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise _too_large(max_bytes)
        return data

    def _ensure_dir(self, folder: str) -> None:
        try:
            self._sftp.stat(self._path(folder))
        except FileNotFoundError:
            self._sftp.mkdir(self._path(folder))

    def move(self, name: str, folder: str) -> None:
        self._ensure_dir(folder)
        target = self._path(folder, name)
        try:
            self._sftp.stat(target)
            target = self._path(folder, f"{int(datetime.now(timezone.utc).timestamp())}_{name}")
        except FileNotFoundError:
            pass
        self._sftp.rename(self._path(name), target)

    def put_text(self, folder: str, name: str, text: str) -> None:
        self._ensure_dir(folder)
        with self._sftp.open(self._path(folder, name), "wb") as fh:
            fh.write(text.encode("utf-8"))

    def close(self) -> None:
        for attr in ("_sftp", "_t", "_sock"):
            obj = getattr(self, attr, None)
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


# ── S3-compatible ────────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(method: str, url: str, *, region: str, access_key: str, secret_key: str,
            payload: bytes = b"", headers: Optional[dict] = None,
            now: Optional[datetime] = None) -> dict:
    """SigV4 headers for one S3 request. Pure: the clock is a parameter, so the
    published AWS test vector can pin it."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    parts = urlsplit(url)
    host = parts.netloc
    payload_hash = _sha256(payload)
    hdrs = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    hdrs.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    names = sorted(hdrs)
    canonical_headers = "".join(f"{n}:{hdrs[n]}\n" for n in names)
    pairs = sorted((quote(unquote(k), safe="-_.~"), quote(unquote(v), safe="-_.~"))
                   for k, _, v in (p.partition("=") for p in parts.query.split("&") if p))
    canonical_query = "&".join(f"{k}={v}" for k, v in pairs)
    canonical = "\n".join([method, parts.path or "/", canonical_query, canonical_headers,
                           ";".join(names), payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _sha256(canonical.encode())])
    k = _hmac(("AWS4" + secret_key).encode(), day)
    for piece in (region, "s3", "aws4_request"):
        k = _hmac(k, piece)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                             f"SignedHeaders={';'.join(names)}, Signature={signature}")
    hdrs.pop("host")
    return hdrs


def _reject_entities(body: bytes) -> None:
    """A listing is XML from a server we do not control. The standard parser
    expands entities, so a document that declares any is refused outright."""
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise _err("file_drop_bad_response", "The storage server sent an unexpected document")


class S3Transport:
    def __init__(self, cfg: dict, secrets: dict, client: Optional[httpx.Client] = None):
        self._bucket = cfg["bucket"]
        self._prefix = (cfg.get("prefix") or "").strip("/")
        self._prefix = f"{self._prefix}/" if self._prefix else ""
        self._region = cfg.get("region") or "us-east-1"
        self._ak, self._sk = secrets.get("access_key", ""), secrets.get("secret_key", "")
        endpoint = (cfg.get("endpoint") or "").strip().rstrip("/")
        if endpoint:
            parts = urlsplit(endpoint)
            self._origin = f"{parts.scheme}://{parts.netloc}"
            self._path_style = True
            host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
        else:
            host = f"{self._bucket}.s3.{self._region}.amazonaws.com"
            self._origin = f"https://{host}"
            self._path_style = False
            port = 443
        network.resolve_allowed(host, port, allow_private=network.allow_private_hosts())
        self._http = client or httpx.Client(timeout=IO_TIMEOUT_S, follow_redirects=False)

    def _url(self, key: str = "", query: str = "") -> str:
        path = f"/{self._bucket}/{quote(key, safe='/-_.~')}" if self._path_style \
            else f"/{quote(key, safe='/-_.~')}"
        return f"{self._origin}{path}{'?' + query if query else ''}"

    def _call(self, method: str, key: str = "", query: str = "", body: bytes = b"",
              headers: Optional[dict] = None) -> httpx.Response:
        url = self._url(key, query)
        signed = sign_v4(method, url, region=self._region, access_key=self._ak,
                         secret_key=self._sk, payload=body, headers=headers)
        try:
            resp = self._http.request(method, url, content=body or None, headers=signed)
        except httpx.HTTPError as exc:
            raise _err("file_drop_connection_failed", "Could not reach the storage server",
                       detail=type(exc).__name__) from exc
        if resp.status_code in (401, 403):
            raise _err("file_drop_auth_failed", "The storage server refused the credentials",
                       status=resp.status_code)
        if resp.status_code == 404:
            raise _err("file_drop_path_not_found", "The bucket or file does not exist",
                       bucket=self._bucket)
        if resp.status_code >= 300:
            raise _err("file_drop_connection_failed", "The storage server answered an error",
                       status=resp.status_code)
        return resp

    def list_files(self) -> list[RemoteFile]:
        q = f"delimiter=%2F&list-type=2&prefix={quote(self._prefix, safe='')}"
        resp = self._call("GET", query=q)
        _reject_entities(resp.content)
        root = ElementTree.fromstring(resp.content)
        ns = root.tag.partition("}")[0] + "}" if root.tag.startswith("{") else ""
        out: list[RemoteFile] = []
        for c in root.iter(f"{ns}Contents"):
            key = c.findtext(f"{ns}Key") or ""
            name = key[len(self._prefix):]
            if not name or "/" in name:
                continue
            stamp = (c.findtext(f"{ns}LastModified") or "").replace("Z", "+00:00")
            try:
                mtime = datetime.fromisoformat(stamp).timestamp()
            except ValueError:
                mtime = 0.0
            out.append(RemoteFile(name, int(c.findtext(f"{ns}Size") or 0), mtime))
        return out[:MAX_LISTED]

    def read(self, name: str, max_bytes: int) -> bytes:
        resp = self._call("GET", self._prefix + name)
        if len(resp.content) > max_bytes:
            raise _too_large(max_bytes)
        return resp.content

    def move(self, name: str, folder: str) -> None:
        src, dst = self._prefix + name, f"{self._prefix}{folder}/{name}"
        source_header = f"/{self._bucket}/{quote(src, safe='/-_.~')}"
        self._call("PUT", dst, headers={"x-amz-copy-source": source_header})
        self._call("DELETE", src)

    def put_text(self, folder: str, name: str, text: str) -> None:
        self._call("PUT", f"{self._prefix}{folder}/{name}", body=text.encode("utf-8"),
                   headers={"content-type": "text/plain"})

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass
