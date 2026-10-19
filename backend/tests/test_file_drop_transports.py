"""The two transports against real protocol peers: a paramiko SFTP server running
in this process, and a minimal S3-compatible HTTP server. Nothing here touches the
database; it pins what only a real peer can: list/read/move, the host-key pin, the
refused credentials, the SSRF refusal, and the S3 request signing and parsing."""

import hashlib
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import paramiko
import pytest

from backend.datasources import network
from backend.errors import AppError
from backend.file_drop.transports import S3Transport, SftpTransport, fingerprint


# ── An SFTP server rooted in a temp directory ───────────────────────────────

class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    def chattr(self, attr):
        return paramiko.SFTP_OK


class _Sftp(paramiko.SFTPServerInterface):
    ROOT = ""

    def _p(self, path):
        return self.ROOT + self.canonicalize(path)

    def list_folder(self, path):
        real = self._p(path)
        out = []
        try:
            names = os.listdir(real)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        for name in names:
            attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(real, name)))
            attr.filename = name
            out.append(attr)
        return out

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._p(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    lstat = stat

    def open(self, path, flags, attr):
        real = self._p(path)
        try:
            fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o666)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        mode = "wb" if flags & os.O_WRONLY else "rb"
        handle = _Handle(flags)
        handle.filename = real
        f = os.fdopen(fd, mode)
        handle.readfile = f
        handle.writefile = f if mode == "wb" else None
        return handle

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._p(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._p(oldpath), self._p(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK


class _Auth(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        ok = (username, password) == ("drop", "pw")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED


@pytest.fixture()
def sftp_server(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "allow_private_hosts", lambda: True)
    host_key = paramiko.ECDSAKey.generate()
    _Sftp.ROOT = str(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            t = paramiko.Transport(conn)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _Sftp)
            t.start_server(server=_Auth())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield {"port": listener.getsockname()[1], "root": tmp_path,
           "fingerprint": fingerprint(host_key)}
    stop.set()
    listener.close()


def _cfg(server, **over):
    return {"host": "127.0.0.1", "port": server["port"], "username": "drop", "path": ".", **over}


class TestSftp:
    def test_list_read_and_move_round_trip(self, sftp_server):
        (sftp_server["root"] / "sales.csv").write_bytes(b"date,sku,qty\n2026-01-01,A,1\n")
        (sftp_server["root"] / "sub").mkdir()
        t = SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=None)
        try:
            files = t.list_files()
            assert [f.name for f in files] == ["sales.csv"]          # the directory is not a file
            assert files[0].size == 28 and files[0].mtime > 0
            assert t.read("sales.csv", 1000).startswith(b"date,sku")
            t.move("sales.csv", "processed")
            t.put_text("rejected", "x.csv.reason.txt", "why")
        finally:
            t.close()
        assert (sftp_server["root"] / "processed" / "sales.csv").exists()
        assert not (sftp_server["root"] / "sales.csv").exists()
        assert (sftp_server["root"] / "rejected" / "x.csv.reason.txt").read_text() == "why"

    def test_moving_onto_an_existing_name_keeps_both(self, sftp_server):
        root = sftp_server["root"]
        (root / "processed").mkdir()
        (root / "processed" / "a.csv").write_bytes(b"old")
        (root / "a.csv").write_bytes(b"new")
        t = SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=None)
        try:
            t.move("a.csv", "processed")
        finally:
            t.close()
        assert (root / "processed" / "a.csv").read_bytes() == b"old"
        assert sorted(p.name for p in (root / "processed").iterdir()) != ["a.csv"]

    def test_first_connection_reports_the_key_to_pin(self, sftp_server):
        t = SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=None)
        try:
            assert t.observed_fingerprint == sftp_server["fingerprint"]
        finally:
            t.close()

    def test_a_different_host_key_is_refused(self, sftp_server):
        with pytest.raises(AppError) as exc:
            SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned="SHA256:" + "A" * 43)
        assert exc.value.code == "file_drop_host_key_changed"

    def test_the_pinned_key_connects(self, sftp_server):
        t = SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=sftp_server["fingerprint"])
        t.close()

    def test_wrong_password_is_a_coded_refusal_not_a_stack_trace(self, sftp_server):
        with pytest.raises(AppError) as exc:
            SftpTransport(_cfg(sftp_server), {"password": "nope"}, pinned=None)
        assert exc.value.code == "file_drop_auth_failed"

    def test_a_file_over_the_ceiling_is_not_read(self, sftp_server):
        (sftp_server["root"] / "big.csv").write_bytes(b"x" * 500)
        t = SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=None)
        try:
            with pytest.raises(AppError) as exc:
                t.read("big.csv", 100)
        finally:
            t.close()
        assert exc.value.code == "file_drop_file_too_large"

    def test_a_missing_directory_is_named(self, sftp_server):
        t = SftpTransport(_cfg(sftp_server, path="nope"), {"password": "pw"}, pinned=None)
        try:
            with pytest.raises(AppError) as exc:
                t.list_files()
        finally:
            t.close()
        assert exc.value.code == "file_drop_path_not_found"

    def test_private_addresses_are_refused_unless_the_installation_allows_them(
            self, sftp_server, monkeypatch):
        monkeypatch.setattr(network, "allow_private_hosts", lambda: False)
        with pytest.raises(AppError):
            SftpTransport(_cfg(sftp_server), {"password": "pw"}, pinned=None)


# ── A minimal S3-compatible server ───────────────────────────────────────────

class _Bucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str, dict]] = []


def _make_s3(bucket: _Bucket, name="drop"):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _key(self):
            parts = urlsplit(self.path)
            segs = unquote(parts.path).lstrip("/").split("/", 1)
            return (segs[1] if len(segs) > 1 else ""), parse_qs(parts.query)

        def _send(self, code, body=b"", ctype="application/xml"):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def _check(self):
            bucket.requests.append((self.command, self.path, dict(self.headers)))
            auth = self.headers.get("Authorization", "")
            return auth.startswith("AWS4-HMAC-SHA256 Credential=AKTEST/") and "Signature=" in auth

        def do_GET(self):
            if not self._check():
                return self._send(403)
            key, q = self._key()
            if q.get("list-type") == ["2"]:
                prefix = q.get("prefix", [""])[0]
                items = "".join(
                    f"<Contents><Key>{k}</Key><LastModified>2026-10-19T10:00:00.000Z</LastModified>"
                    f"<Size>{len(v)}</Size></Contents>"
                    for k, v in sorted(bucket.objects.items())
                    if k.startswith(prefix) and "/" not in k[len(prefix):])
                return self._send(200, (
                    '<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"{items}</ListBucketResult>").encode())
            if key in bucket.objects:
                return self._send(200, bucket.objects[key], "application/octet-stream")
            self._send(404)

        def do_PUT(self):
            if not self._check():
                return self._send(403)
            key, _ = self._key()
            src = self.headers.get("x-amz-copy-source")
            if src:
                bucket.objects[key] = bucket.objects[unquote(src).split("/", 2)[2]]
                return self._send(200, b"<CopyObjectResult/>")
            length = int(self.headers.get("Content-Length", 0))
            bucket.objects[key] = self.rfile.read(length)
            self._send(200)

        def do_DELETE(self):
            if not self._check():
                return self._send(403)
            key, _ = self._key()
            bucket.objects.pop(key, None)
            self._send(204)

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def s3(monkeypatch):
    monkeypatch.setattr(network, "allow_private_hosts", lambda: True)
    bucket = _Bucket()
    server = _make_s3(bucket)
    yield bucket, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _transport(endpoint, prefix="in", secret="SECRET"):
    return S3Transport({"bucket": "drop", "endpoint": endpoint, "region": "us-east-1",
                        "prefix": prefix}, {"access_key": "AKTEST", "secret_key": secret})


class TestS3:
    def test_list_read_move_and_note(self, s3):
        bucket, endpoint = s3
        bucket.objects["in/sales.csv"] = b"date,sku\n2026-01-01,A\n"
        bucket.objects["in/processed/old.csv"] = b"x"          # below the prefix folder: not listed
        bucket.objects["other/elsewhere.csv"] = b"x"           # another prefix: not listed
        t = _transport(endpoint)
        try:
            files = t.list_files()
            assert [(f.name, f.size) for f in files] == [("sales.csv", 22)]
            assert t.read("sales.csv", 1000) == b"date,sku\n2026-01-01,A\n"
            t.move("sales.csv", "processed")
            t.put_text("rejected", "b.csv.reason.txt", "why")
        finally:
            t.close()
        assert "in/sales.csv" not in bucket.objects
        assert bucket.objects["in/processed/sales.csv"] == b"date,sku\n2026-01-01,A\n"
        assert bucket.objects["in/rejected/b.csv.reason.txt"] == b"why"

    def test_every_request_is_signed_and_never_carries_the_secret(self, s3):
        bucket, endpoint = s3
        t = _transport(endpoint)
        try:
            t.list_files()
        finally:
            t.close()
        method, path, headers = bucket.requests[0]
        lowered = {k.lower(): v for k, v in headers.items()}
        assert lowered["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKTEST/")
        assert "SECRET" not in repr(bucket.requests)
        assert lowered["x-amz-content-sha256"] == hashlib.sha256(b"").hexdigest()

    def test_a_file_over_the_ceiling_is_refused(self, s3):
        bucket, endpoint = s3
        bucket.objects["in/big.csv"] = b"x" * 500
        t = _transport(endpoint)
        try:
            with pytest.raises(AppError) as exc:
                t.read("big.csv", 100)
        finally:
            t.close()
        assert exc.value.code == "file_drop_file_too_large"

    def test_rejected_credentials_are_a_coded_refusal(self, s3):
        bucket, endpoint = s3
        t = S3Transport({"bucket": "drop", "endpoint": endpoint, "prefix": "in"}, {})
        try:
            with pytest.raises(AppError) as exc:
                t.list_files()
        finally:
            t.close()
        assert exc.value.code == "file_drop_auth_failed"

    def test_a_private_endpoint_is_refused_unless_allowed(self, s3, monkeypatch):
        _, endpoint = s3
        monkeypatch.setattr(network, "allow_private_hosts", lambda: False)
        with pytest.raises(AppError):
            _transport(endpoint)
