"""SQL data-source connections, pure: no network, no database.

Parsing and validating what a person types, the SSRF policy, the mapping of
every driver's failure to a coded error that never carries a secret, value
conversion, connection-string building for SQL Server and Oracle (no server of
either exists to test against — see test_customer_db_live.py for the three
engines that do), identifier quoting, the per-tenant gate, transient-only
retries, secrets at rest, the schema cache, and the credential-rotation rule.
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import json
import socket
import ssl
import time
import uuid
from decimal import Decimal

import pytest

from backend.config import settings
from backend.datasources import catalog, client, network, schema_cache
from backend.datasources import connection as conn_mod
from backend.datasources import errors as ds_errors
from backend.datasources import secrets as ds_secrets
from backend.datasources import service as svc
from backend.datasources.sql_guard import validate_read_only_sql
from backend.datasources.values import csv_cell, json_cell
from backend.errors import AppError

pytestmark = pytest.mark.offline


def _code(fn, *a, **k) -> str:
    with pytest.raises(AppError) as exc:
        fn(*a, **k)
    return exc.value.code


# ── Hosts, ports, modes ───────────────────────────────────────────────────────

class TestHost:
    @pytest.mark.parametrize("raw, expected", [
        ("DB.Example.COM", "db.example.com"),
        ("db.example.com.", "db.example.com"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("10.0.0.5", "10.0.0.5"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:0db8:0000::0001", "2001:db8::1"),
        ("erp_db.internal", "erp_db.internal"),
        ("  db1  ", "db1"),
    ])
    def test_normalised(self, raw, expected):
        assert conn_mod.normalize_host(raw, "postgresql") == expected

    @pytest.mark.parametrize("raw", [
        "", "db example.com", "user@db.example.com", "db.example.com/extra",
        "db.example.com:5432", "db;drop", "fe80::1%eth0", "a" * 300, "db?x=1",
        "-bad-.example.com", "db\\INST",
    ])
    def test_refused(self, raw):
        assert _code(conn_mod.normalize_host, raw, "postgresql") == "data_source_host_invalid"

    def test_sql_server_named_instance(self):
        assert conn_mod.normalize_host("SRV01\\SQLEXPRESS", "mssql") == "srv01\\SQLEXPRESS"
        assert _code(conn_mod.normalize_host, "srv\\bad name", "mssql") == "data_source_host_invalid"

    @pytest.mark.parametrize("raw", ["0", "65536", "-1", "abc", "1.5"])
    def test_bad_port(self, raw):
        assert _code(conn_mod.normalize_port, raw) == "data_source_port_invalid"

    def test_ports_at_the_edges(self):
        assert conn_mod.normalize_port("1") == 1 and conn_mod.normalize_port(65535) == 65535


class TestModesAndFields:
    @pytest.mark.parametrize("engine, mode", [("mssql", "verify-ca"), ("oracle", "prefer")])
    def test_a_mode_the_driver_cannot_honour_is_refused(self, engine, mode):
        assert _code(conn_mod.normalize_ssl_mode, mode, engine) == "data_source_ssl_mode_unsupported"

    @pytest.mark.parametrize("alias, mode", [
        ("REQUIRED", "require"), ("preferred", "prefer"), ("VERIFY_IDENTITY", "verify-full"),
        ("disabled", "disable"), ("", "prefer"),
    ])
    def test_aliases_from_other_tools(self, alias, mode):
        assert conn_mod.normalize_ssl_mode(alias, "postgresql") == mode

    def test_unknown_mode(self):
        assert _code(conn_mod.normalize_ssl_mode, "maybe", "mysql") == "data_source_ssl_mode_invalid"

    def test_engine_defaults_and_requirements(self):
        out = conn_mod.normalize_fields({"engine": "MySQL", "host": "db", "database": "x", "username": "u"})
        assert out["port"] == 3306 and out["ssl_mode"] == "prefer"
        assert out["connect_timeout_s"] == 10 and out["statement_timeout_s"] == 30
        assert _code(conn_mod.normalize_fields, {"engine": "sqlite", "host": "h"}) == "data_source_engine_unsupported"
        assert _code(conn_mod.normalize_fields, {"engine": "mysql", "host": "h", "username": "u"}) \
            == "data_source_database_required"
        assert _code(conn_mod.normalize_fields, {"engine": "mysql", "host": "h", "database": "d"}) \
            == "data_source_username_required"

    @pytest.mark.parametrize("value", [0, 61, "x"])
    def test_timeouts_bounded(self, value):
        assert _code(conn_mod.normalize_fields, {"engine": "postgresql", "host": "h", "database": "d",
                                                 "username": "u", "connect_timeout_s": value}) \
            == "data_source_timeout_invalid"

    def test_spec_never_shows_secrets_in_repr(self):
        spec = conn_mod.ConnectionSpec("postgresql", "h", 1, "d", "u", password="hunter2",
                                       ssl_ca="-----BEGIN CERTIFICATE-----")
        assert "hunter2" not in repr(spec) and "BEGIN" not in repr(spec)

    def test_bulk_timeout(self):
        spec = conn_mod.ConnectionSpec("postgresql", "h", 1, "d", "u", statement_timeout_s=10)
        assert spec.bulk_timeout_s == 120
        assert conn_mod.ConnectionSpec("postgresql", "h", 1, "d", "u", statement_timeout_s=600).bulk_timeout_s == 900


# ── CA certificates ───────────────────────────────────────────────────────────

def _self_signed(cn="Test CA") -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem, key_pem


class TestCaCertificate:
    def test_a_certificate_is_described_not_echoed(self):
        pem, _ = _self_signed()
        info = conn_mod.inspect_ca_pem(pem)
        assert info["subject"] == "CN=Test CA" and info["count"] == 1
        assert len(info["fingerprint_sha256"]) == 32
        assert "BEGIN" not in json.dumps(info)

    def test_a_private_key_is_refused(self):
        pem, key = _self_signed()
        assert _code(conn_mod.inspect_ca_pem, pem + key) == "data_source_ssl_ca_invalid"

    @pytest.mark.parametrize("junk", ["", "hello", "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"])
    def test_garbage_is_refused(self, junk):
        assert _code(conn_mod.inspect_ca_pem, junk) == "data_source_ssl_ca_invalid"

    def test_engine_and_mode_compatibility(self):
        assert _code(conn_mod.check_ca_compatible, "mssql", "verify-full", True) == "data_source_ssl_ca_unsupported"
        assert _code(conn_mod.check_ca_compatible, "postgresql", "verify-ca", False) == "data_source_ssl_ca_required"
        conn_mod.check_ca_compatible("postgresql", "verify-full", False)   # system trust store

    def test_ssl_context_per_mode(self):
        pem, _ = _self_signed()
        assert client.ssl_context("disable", None) is None and client.ssl_context("prefer", None) is None
        req = client.ssl_context("require", None)
        assert req.verify_mode == ssl.CERT_NONE and req.check_hostname is False
        ca = client.ssl_context("verify-ca", pem)
        assert ca.verify_mode == ssl.CERT_REQUIRED and ca.check_hostname is False
        full = client.ssl_context("verify-full", pem)
        assert full.verify_mode == ssl.CERT_REQUIRED and full.check_hostname is True


# ── Connection strings ────────────────────────────────────────────────────────

class TestConnectionStrings:
    @pytest.mark.parametrize("text, expected", [
        ("postgresql://u:p%40ss@db.example.com:6543/erp?sslmode=verify-full",
         {"engine": "postgresql", "host": "db.example.com", "port": 6543, "database": "erp",
          "username": "u", "password": "p@ss", "ssl_mode": "verify-full"}),
        ("jdbc:postgresql://db:5432/erp?ssl=true",
         {"engine": "postgresql", "host": "db", "port": 5432, "database": "erp", "ssl_mode": "require"}),
        ("mysql+pymysql://reader:x@[2001:db8::5]:3307/shop?ssl-mode=REQUIRED",
         {"engine": "mysql", "host": "2001:db8::5", "port": 3307, "database": "shop",
          "username": "reader", "password": "x", "ssl_mode": "REQUIRED"}),
        ("mariadb://r@maria/shop", {"engine": "mysql", "host": "maria", "database": "shop", "username": "r"}),
        ("jdbc:sqlserver://srv:1433;databaseName=ERP;user=sa;password={a;b}}};encrypt=true;trustServerCertificate=true",
         {"engine": "mssql", "host": "srv", "port": "1433", "database": "ERP", "username": "sa",
          "password": "a;b}", "ssl_mode": "require"}),
        ("Server=tcp:erp.local,1444;Database=Ventas;User Id=lector;Password='p;w';Encrypt=True;"
         "TrustServerCertificate=False",
         {"engine": "mssql", "host": "erp.local", "port": "1444", "database": "Ventas",
          "username": "lector", "password": "p;w", "ssl_mode": "verify-full"}),
        ("jdbc:oracle:thin:scott/tiger@//ora.example.com:1521/ORCLPDB1",
         {"engine": "oracle", "host": "ora.example.com", "port": "1521", "database": "ORCLPDB1",
          "username": "scott", "password": "tiger"}),
        ("jdbc:oracle:thin:@ora:1521:ORCL", {"engine": "oracle", "host": "ora", "port": "1521", "database": "ORCL"}),
        ("scott/tiger@//ora:1522/SVC", {"engine": "oracle", "host": "ora", "port": "1522", "database": "SVC",
                                        "username": "scott", "password": "tiger"}),
        ("host=db port=5433 dbname='my db' user=r password=s sslmode=require",
         {"engine": "postgresql", "host": "db", "port": "5433", "database": "my db",
          "username": "r", "password": "s", "ssl_mode": "require"}),
    ])
    def test_forms(self, text, expected):
        assert conn_mod.parse_connection_string(text) == expected

    @pytest.mark.parametrize("text, reason", [
        ("", "empty"), ("ftp://x", "unsupported_scheme"), ("just words", "unrecognized_format"),
        ("postgresql://h:99999/db", "bad_port"), ("x" * 5000, "too_long"),
    ])
    def test_refused_with_a_reason(self, text, reason):
        with pytest.raises(AppError) as exc:
            conn_mod.parse_connection_string(text)
        assert exc.value.code == "data_source_connection_string_invalid"
        assert exc.value.params["reason"] == reason

    def test_public_parse_never_returns_the_password(self):
        out = svc.parse_connection_string_public("postgresql://u:topsecret@DB.example.com/erp")
        assert out == {"engine": "postgresql", "host": "db.example.com", "port": 5432,
                       "database": "erp", "username": "u", "has_password": True}
        assert "topsecret" not in json.dumps(out)


# ── SSRF policy ───────────────────────────────────────────────────────────────

class TestNetworkPolicy:
    @pytest.mark.parametrize("addr, category", [
        ("169.254.169.254", "metadata" if False else "link_local"),
        ("100.100.100.200", "metadata"), ("168.63.129.16", "metadata"), ("fd00:ec2::254", "metadata"),
        ("fe80::1", "link_local"), ("224.0.0.1", "multicast"), ("0.0.0.0", "unspecified"),
        ("::", "unspecified"), ("255.255.255.255", "reserved"),
        ("::ffff:169.254.169.254", "link_local"),
    ])
    def test_never_allowed_even_with_the_switch(self, addr, category):
        verdict = network.classify(ipaddress.ip_address(addr), allow_private=True)
        assert verdict.allowed is False and verdict.category == category

    @pytest.mark.parametrize("addr", [
        "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.10", "100.64.0.1", "fc00::1", "::1",
        "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:c0a8:0101::1",
    ])
    def test_private_follows_the_switch(self, addr):
        ip = ipaddress.ip_address(addr)
        assert network.classify(ip, allow_private=False).allowed is False
        assert network.classify(ip, allow_private=True).allowed is True

    @pytest.mark.parametrize("addr", ["8.8.8.8", "2606:4700:4700::1111", "64:ff9b::808:808"])
    def test_public(self, addr):
        assert network.classify(ipaddress.ip_address(addr), allow_private=False).allowed is True

    def test_one_bad_record_among_good_ones_refuses(self, monkeypatch):
        def fake(host, port, type=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
        monkeypatch.setattr(network.socket, "getaddrinfo", fake)
        with pytest.raises(AppError) as exc:
            network.resolve_allowed("evil.example", 5432, allow_private=True)
        assert exc.value.code == "data_source_host_forbidden"

    def test_private_refusal_names_the_setting(self):
        with pytest.raises(AppError) as exc:
            network.check_literal("10.0.0.5", allow_private=False)
        assert exc.value.code == "data_source_host_not_allowed"
        assert exc.value.params["setting"] == "SQL_SOURCES_ALLOW_PRIVATE_HOSTS"
        network.check_literal("10.0.0.5", allow_private=True)
        network.check_literal("db.example.com", allow_private=False)  # names: checked at connect

    def test_a_hanging_lookup_times_out(self, monkeypatch):
        monkeypatch.setattr(network.socket, "getaddrinfo", lambda *a, **k: time.sleep(3))
        started = time.monotonic()
        with pytest.raises(AppError) as exc:
            network.resolve("slow.example", 1, timeout_s=0.3)
        assert exc.value.code == "data_source_dns_failed" and exc.value.params["reason"] == "timeout"
        assert time.monotonic() - started < 2

    def test_unresolvable(self, monkeypatch):
        def boom(*a, **k):
            raise socket.gaierror(-2, "Name or service not known")
        monkeypatch.setattr(network.socket, "getaddrinfo", boom)
        assert _code(network.resolve, "nope.invalid", 1) == "data_source_dns_failed"

    def test_save_time_refusal_of_a_literal(self, monkeypatch):
        monkeypatch.setattr(settings, "sql_sources_allow_private_hosts", False, raising=False)
        with pytest.raises(AppError) as exc:
            svc.build_sql_config({"engine": "postgresql", "host": "169.254.169.254", "database": "d",
                                  "username": "u"}, "pw")
        assert exc.value.code == "data_source_host_forbidden"


# ── Driver failures -> codes, without secrets ─────────────────────────────────

class _PgError(Exception):
    def __init__(self, msg, pgcode=None):
        super().__init__(msg)
        self.pgcode = pgcode


class _Wrapped(Exception):
    """What SQLAlchemy raises: the DBAPI error under `.orig`, plus noise."""
    def __init__(self, orig, statement="SELECT 1"):
        super().__init__(f"({type(orig).__module__}.{type(orig).__name__}) {orig}\n[SQL: {statement}]\n"
                         "(Background on this error at: https://sqlalche.me/e/20/e3q8)")
        self.orig = orig


class _OraErr:
    def __init__(self, code):
        self.full_code = code


class TestErrorClassification:
    @pytest.mark.parametrize("exc, engine, code, stage", [
        (_PgError('FATAL:  password authentication failed for user "x"', "28P01"), "postgresql", "data_source_auth_failed", "auth"),
        (_PgError('FATAL:  database "nope" does not exist', "3D000"), "postgresql", "data_source_database_not_found", "auth"),
        (_PgError("canceling statement due to statement timeout", "57014"), "postgresql", "data_source_query_timeout", "query"),
        (_PgError("cannot execute INSERT in a read-only transaction", "25006"), "postgresql", "data_source_read_only_violation", "query"),
        (_PgError("permission denied for table t", "42501"), "postgresql", "data_source_permission_denied", "query"),
        (_PgError("FATAL:  sorry, too many clients already", "53300"), "postgresql", "data_source_server_busy", "auth"),
        (_PgError('FATAL:  no pg_hba.conf entry for host "1.2.3.4"', "28000"), "postgresql", "data_source_host_rejected", "auth"),
        (_PgError("server does not support SSL, but SSL was required"), "postgresql", "data_source_tls_failed", "tls"),
        (_PgError('connection to server at "h" (1.2.3.4), port 5432 failed: Connection refused'), "postgresql", "data_source_host_unreachable", "tcp"),
        (_PgError("connection to server at \"h\" failed: timeout expired"), "postgresql", "data_source_connect_timeout", "tcp"),
        (_PgError('column "x" does not exist', "42703"), "postgresql", "sql_query_failed", "query"),
        (Exception(1045, "Access denied for user 'r'@'1.2.3.4' (using password: YES)"), "mysql", "data_source_auth_failed", "auth"),
        (Exception(1049, "Unknown database 'nope'"), "mysql", "data_source_database_not_found", "auth"),
        (Exception(1130, "Host '1.2.3.4' is not allowed to connect"), "mysql", "data_source_host_rejected", "auth"),
        (Exception(3024, "Query execution was interrupted, maximum statement execution time exceeded"), "mysql", "data_source_query_timeout", "query"),
        (Exception(1969, "Query execution was interrupted (max_statement_time exceeded)"), "mysql", "data_source_query_timeout", "query"),
        (Exception(1792, "Cannot execute statement in a READ ONLY transaction."), "mysql", "data_source_read_only_violation", "query"),
        (Exception(1142, "SELECT command denied to user"), "mysql", "data_source_permission_denied", "query"),
        (Exception(1040, "Too many connections"), "mysql", "data_source_server_busy", "auth"),
        (Exception(2003, "Can't connect to MySQL server on 'h' ([Errno 111] Connection refused)"), "mysql", "data_source_host_unreachable", "tcp"),
        (Exception(2003, "Can't connect to MySQL server on 'h' (timed out)"), "mysql", "data_source_connect_timeout", "tcp"),
        (Exception(2013, "Lost connection to MySQL server during query (timed out)"), "mysql", "data_source_query_timeout", "query"),
        (Exception(2013, "Lost connection to MySQL server during query"), "mysql", "data_source_connection_lost", "query"),
        (Exception(1054, "Unknown column 'x' in 'field list'"), "mysql", "sql_query_failed", "query"),
        (Exception("28000", "[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Login failed for user 'sa'. (18456)"), "mssql", "data_source_auth_failed", "auth"),
        (Exception("42000", "[42000] Cannot open database \"ERP\" requested by the login. (4060)"), "mssql", "data_source_database_not_found", "auth"),
        (Exception("HYT00", "[HYT00] [Microsoft][ODBC Driver 18 for SQL Server]Query timeout expired (0)"), "mssql", "data_source_query_timeout", "query"),
        (Exception("HYT01", "[HYT01] Login timeout expired"), "mssql", "data_source_connect_timeout", "tcp"),
        (Exception("08001", "[08001] TCP Provider: No connection could be made because the target machine actively refused it."), "mssql", "data_source_host_unreachable", "tcp"),
        (Exception("08001", "[08001] SSL Provider: The certificate chain was issued by an authority that is not trusted."), "mssql", "data_source_tls_failed", "tls"),
        (Exception("42000", "[42000] The SELECT permission was denied on the object 't'. (229)"), "mssql", "data_source_permission_denied", "query"),
        (Exception("01000", "[01000] [unixODBC][Driver Manager]Can't open lib 'ODBC Driver 18 for SQL Server'"), "mssql", "data_source_driver_missing", "driver"),
        (Exception("08S01", "[08S01] Communication link failure"), "mssql", "data_source_connection_lost", "query"),
        (Exception(_OraErr("ORA-01017")), "oracle", "data_source_auth_failed", "auth"),
        (Exception("ORA-12514: TNS:listener does not currently know of service"), "oracle", "data_source_database_not_found", "auth"),
        (Exception("DPY-4024: call timeout of 2000 ms exceeded"), "oracle", "data_source_query_timeout", "query"),
        (Exception("ORA-00942: table or view does not exist"), "oracle", "data_source_permission_denied", "query"),
        (Exception("DPY-6005: cannot connect to database. Connection refused"), "oracle", "data_source_host_unreachable", "tcp"),
        (ModuleNotFoundError("No module named 'oracledb'"), "oracle", "data_source_driver_missing", "driver"),
    ])
    def test_codes(self, exc, engine, code, stage):
        during = "query" if stage == "query" else "auth"
        got = ds_errors.classify(exc, engine=engine, during=during)
        assert (got.code, got.stage) == (code, stage)
        wrapped = ds_errors.classify(_Wrapped(exc), engine=engine, during=during)
        assert wrapped.code == code

    def test_transient_only_for_what_can_heal(self):
        assert ds_errors.classify(Exception(1040, "Too many connections"), engine="mysql").transient
        assert not ds_errors.classify(Exception(1045, "Access denied"), engine="mysql", during="auth").transient

    @pytest.mark.parametrize("secret", ["hunter2", "pä$$ wörd/€?&=", "a@b:c"])
    def test_scrub_removes_the_secret_in_every_spelling(self, secret):
        from urllib.parse import quote, quote_plus
        text = (f"failed for {secret} url postgresql://u:{quote(secret, safe='')}@h/db "
                f"pw={quote_plus(secret)} password={secret}; PWD={{{secret}}}")
        out = ds_errors.scrub(text, [secret])
        for spelling in (secret, quote(secret, safe=""), quote_plus(secret)):
            assert spelling not in out
        assert "sqlalche.me" not in ds_errors.scrub(str(_Wrapped(Exception("x"))))

    def test_driver_text_is_capped_and_single_line(self):
        out = ds_errors.scrub("line one\n" + "x" * 1000)
        assert out == "line one"
        assert len(ds_errors.scrub("y" * 1000)) == ds_errors.MAX_REASON_CHARS

    def test_query_error_keeps_the_useful_text_without_the_statement(self):
        exc = _Wrapped(_PgError('column "qty2" does not exist', "42703"), statement="SELECT qty2 FROM secret_table")
        got = ds_errors.classify(exc, engine="postgresql", during="query")
        assert got.code == "sql_query_failed"
        assert 'column "qty2" does not exist' in got.params["reason"]
        assert "secret_table" not in got.params["reason"]


# ── Values ────────────────────────────────────────────────────────────────────

class TestValues:
    @pytest.mark.parametrize("value, expected", [
        (None, None), (True, "true"), (5, 5), (2 ** 70, 2 ** 70), (1.5, 1.5),
        (float("nan"), None), (float("inf"), None),
        (Decimal("1E+3"), "1000"), (Decimal("0.000001"), "0.000001"), (Decimal("-0"), "0"),
        (Decimal("NaN"), None), (Decimal("12345678901234567890.0123456789"), "12345678901234567890.0123456789"),
        (dt.date(2026, 1, 31), "2026-01-31"),
        (dt.datetime(2026, 1, 31, 13, 45, 6, tzinfo=dt.timezone(dt.timedelta(hours=-6))), "2026-01-31T13:45:06-06:00"),
        (dt.time(7, 30), "07:30:00"), (dt.timedelta(minutes=2), "120.0"),
        (b"\x00\xff", "0x00ff"), (memoryview(b"\x01"), "0x01"), (bytearray(b"\x02"), "0x02"),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        ({"a": "ü"}, '{"a": "ü"}'), ("=cmd|' /C calc'!A0", "'=cmd|' /C calc'!A0"), ("-5", "-5"),
        ("a\x00b", "ab"),
    ])
    def test_csv(self, value, expected):
        assert csv_cell(value) == expected

    @pytest.mark.parametrize("value, expected", [
        (2 ** 53 - 1, 2 ** 53 - 1), (2 ** 53, str(2 ** 53)), (-(2 ** 60), str(-(2 ** 60))),
        (Decimal("0.1"), "0.1"), (float("nan"), None), (b"\x00" * 100, "0x" + "00" * 64 + "…"),
        ({"k": [1]}, {"k": [1]}), (dt.date(2026, 1, 1), "2026-01-01"),
    ])
    def test_json(self, value, expected):
        assert json_cell(value) == expected
        json.dumps(json_cell(value))

    def test_xlsx_keeps_types_excel_can_hold_and_text_for_what_it_would_change(self):
        assert svc._xlsx_cell(10 ** 15) == str(10 ** 15)
        assert svc._xlsx_cell(10 ** 14) == 10 ** 14
        assert svc._xlsx_cell(Decimal("12.50")) == Decimal("12.50")
        assert svc._xlsx_cell(Decimal("12345678901234567890.01")) == "12345678901234567890.01"
        assert svc._xlsx_cell(dt.date(2026, 1, 2)) == dt.date(2026, 1, 2)
        aware = dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone.utc)
        assert svc._xlsx_cell(aware) == "2026-01-02T03:04:00+00:00"
        assert svc._xlsx_cell("=1+1") == "'=1+1"
        assert svc._xlsx_cell(b"") == "0x01"


# ── Building connections for engines with no test server ──────────────────────

def _spec(engine, **kw):
    base = dict(engine=engine, host="erp.example.com", port=1433, database="ERP", username="lector",
                password="p;w}x", ssl_mode="require")
    base.update(kw)
    return conn_mod.ConnectionSpec(**base)


class TestSqlServerConnectionString:
    def test_values_are_brace_escaped(self, monkeypatch):
        monkeypatch.setattr(client, "best_mssql_odbc_driver", lambda: "ODBC Driver 18 for SQL Server")
        cs = client.mssql_connection_string(_spec("mssql"), "203.0.113.7")
        assert "PWD={p;w}}x};" in cs and "UID={lector};" in cs and "DATABASE={ERP};" in cs
        assert "SERVER=tcp:203.0.113.7,1433;" in cs
        assert "Encrypt=yes;TrustServerCertificate=yes;" in cs

    def test_verify_full_checks_the_name_while_connecting_to_the_checked_address(self, monkeypatch):
        monkeypatch.setattr(client, "best_mssql_odbc_driver", lambda: "ODBC Driver 18 for SQL Server")
        cs = client.mssql_connection_string(_spec("mssql", ssl_mode="verify-full"), "203.0.113.7")
        assert "TrustServerCertificate=no" in cs and "HostNameInCertificate={erp.example.com}" in cs

    def test_named_instance_and_ipv6(self, monkeypatch):
        monkeypatch.setattr(client, "best_mssql_odbc_driver", lambda: "ODBC Driver 18 for SQL Server")
        assert "SERVER=203.0.113.7\\SQLEXPRESS;" in client.mssql_connection_string(
            _spec("mssql", host="srv\\SQLEXPRESS"), "203.0.113.7")
        assert "SERVER=tcp:[2001:db8::1],1433;" in client.mssql_connection_string(_spec("mssql"), "2001:db8::1")
        assert "Encrypt=no" in client.mssql_connection_string(_spec("mssql", ssl_mode="disable"), None)

    def test_connect_sets_login_and_query_timeouts(self, monkeypatch):
        import sys
        import types
        calls = {}

        class FakeConn:
            timeout = 0

        fake = types.ModuleType("pyodbc")
        fake.connect = lambda cs, **kw: calls.update(cs=cs, **kw) or FakeConn()
        fake.drivers = lambda: ["ODBC Driver 17 for SQL Server"]
        monkeypatch.setitem(sys.modules, "pyodbc", fake)
        conn = client.dbapi_connect(_spec("mssql", connect_timeout_s=7), "203.0.113.7", 25)
        assert calls["timeout"] == 7 and calls["autocommit"] is False and calls["readonly"] is True
        assert conn.timeout == 25
        assert "DRIVER={ODBC Driver 17 for SQL Server}" in calls["cs"]


class TestOracleConnect:
    def _capture(self, monkeypatch):
        import oracledb
        calls = {}

        class FakeConn:
            call_timeout = 0

        monkeypatch.setattr(oracledb, "connect", lambda **kw: calls.update(kw) or FakeConn())
        return calls

    def test_plain_tcp_to_the_checked_address(self, monkeypatch):
        calls = self._capture(monkeypatch)
        conn = client.dbapi_connect(_spec("oracle", ssl_mode="disable", port=1521, database="ORCLPDB1"),
                                    "203.0.113.9", 12)
        assert calls["host"] == "203.0.113.9" and calls["service_name"] == "ORCLPDB1"
        assert "protocol" not in calls and conn.call_timeout == 12_000

    def test_verify_full_uses_tcps_and_the_host_name(self, monkeypatch):
        calls = self._capture(monkeypatch)
        client.dbapi_connect(_spec("oracle", ssl_mode="verify-full"), "203.0.113.9", 5)
        assert calls["protocol"] == "tcps" and calls["ssl_server_dn_match"] is True
        assert calls["host"] == "erp.example.com"
        assert isinstance(calls["ssl_context"], ssl.SSLContext)


class TestStoredSpec:
    def test_legacy_rows_keep_their_old_behaviour(self):
        enc = ds_secrets.encrypt("pw")
        for engine, mode in conn_mod.LEGACY_SSL_MODE.items():
            spec = client.spec_from_stored({"engine": engine, "host": "h", "port": 1, "database": "d",
                                            "username": "u", "password_enc": enc})
            assert spec.ssl_mode == mode and spec.password == "pw"
            assert spec.connect_timeout_s == 10 and spec.statement_timeout_s == 30


# ── Per-tenant gate and retries ───────────────────────────────────────────────

class TestGateAndRetries:
    def test_gate_refuses_beyond_the_limit_per_tenant_only(self, monkeypatch):
        monkeypatch.setattr(settings, "sql_sources_max_concurrent_per_tenant", 1, raising=False)
        with client.tenant_slot("tA"):
            assert _code(lambda: client.tenant_slot("tA", wait_s=0.05).__enter__()) == "data_source_busy"
            with client.tenant_slot("tB", wait_s=0.05):
                pass
        with client.tenant_slot("tA", wait_s=0.05):
            pass

    def test_retries_transient_failures_only(self, monkeypatch):
        monkeypatch.setattr(client.time, "sleep", lambda s: None)
        spec = conn_mod.ConnectionSpec("mysql", "h", 1, "d", "u", password="pw")

        class Engine:
            def __init__(self, errors):
                self.errors = list(errors)
                self.calls = 0

            def connect(self):
                self.calls += 1
                if self.errors:
                    raise self.errors.pop(0)
                return "conn"

        busy = Engine([Exception(1040, "Too many connections")] * 2)
        assert client._connect_with_retry(busy, spec, retries=2) == "conn" and busy.calls == 3
        denied = Engine([Exception(1045, "Access denied for user (using password: YES) pw")])
        with pytest.raises(AppError) as exc:
            client._connect_with_retry(denied, spec, retries=2)
        assert exc.value.code == "data_source_auth_failed" and denied.calls == 1
        assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True
        always = Engine([Exception(1040, "Too many connections")] * 5)
        with pytest.raises(AppError):
            client._connect_with_retry(always, spec, retries=2)
        assert always.calls == 3


# ── Identifiers and privileges ────────────────────────────────────────────────

class TestCatalog:
    @pytest.mark.parametrize("engine, name, quoted", [
        ("postgresql", 'a"b', '"a""b"'), ("oracle", "ORDER", '"ORDER"'),
        ("mysql", "a`b", "`a``b`"), ("mssql", "a]b", "[a]]b]"),
    ])
    def test_quote_ident(self, engine, name, quoted):
        assert catalog.quote_ident(engine, name) == quoted

    @pytest.mark.parametrize("engine", ["postgresql", "mysql", "mssql", "oracle"])
    @pytest.mark.parametrize("name", ["update", "Ventas 2024", 'x"; DROP TABLE t; --', "a]b`c", "ñandú", "$$x$$"])
    def test_preview_sql_always_passes_the_read_only_guard(self, engine, name):
        sql = catalog.select_preview_sql(engine, "dbo", name)
        if sql is not None:
            validate_read_only_sql(sql, engine)
            assert sql.upper().startswith("SELECT")

    def test_a_name_the_guard_cannot_accept_gets_no_preview(self):
        assert catalog.select_preview_sql("mysql", "s", "a\\b") is None

    def test_mysql_grants_parse_without_echoing_hashes(self):
        privs, target = catalog._mysql_grant_privileges(
            "GRANT USAGE ON *.* TO `r`@`%` IDENTIFIED BY PASSWORD '*8954CBD7CF'")
        assert privs == {"USAGE"} and target == "*.*"
        privs, _ = catalog._mysql_grant_privileges(
            "GRANT SELECT, INSERT, UPDATE (`a`, `b`) ON `customer`.* TO `r`@`%` WITH GRANT OPTION")
        assert privs == {"SELECT", "INSERT", "UPDATE", "GRANT OPTION"}

    def test_mssql_and_oracle_privilege_checks_with_fake_catalogues(self):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0]

            def fetchall(self):
                return self.rows

            def scalar(self):
                return self.rows[0][0]

        class Conn:
            def __init__(self, answers):
                self.answers = answers

            def execute(self, stmt, params=None):
                text = str(stmt)
                for needle, rows in self.answers.items():
                    if needle in text:
                        return Result(rows)
                raise AssertionError(text)

        reader = catalog.write_privileges(Conn({"IS_SRVROLEMEMBER": [(0, 0, 0, 0, 0, 0, 0, 0)]}), "mssql")
        assert reader == {"can_write": False, "privileges": [], "superuser": False}
        owner = catalog.write_privileges(Conn({"IS_SRVROLEMEMBER": [(0, 1, 0, 0, 1, 1, 1, 1)]}), "mssql")
        assert owner["can_write"] and owner["superuser"] and "DB_OWNER" in owner["privileges"]
        ora = catalog.write_privileges(Conn({"session_privs": [("SELECT ANY TABLE",), ("CREATE ANY TABLE",)],
                                             "user_tables": [(0,)]}), "oracle")
        assert ora["privileges"] == ["CREATE ANY TABLE"] and ora["superuser"]

    @pytest.mark.parametrize("engine", ["postgresql", "mysql", "mssql", "oracle"])
    def test_least_privilege_statements_grant_only_reads(self, engine):
        sql = "\n".join(catalog.least_privilege_sql(engine, "ERP"))
        assert "<choose-a-password>" in sql
        for word in ("INSERT", "UPDATE", "DELETE", "ALL PRIVILEGES", "db_owner"):
            assert word not in sql


# ── Secrets at rest ───────────────────────────────────────────────────────────

class TestSecrets:
    def test_round_trip_and_ciphertext_is_not_the_password(self):
        token = ds_secrets.encrypt("pä$$")
        assert "pä$$" not in token and ds_secrets.decrypt(token) == "pä$$"

    def test_legacy_base64_still_reads(self):
        assert ds_secrets.decrypt(base64.b64encode("old".encode()).decode()) == "old"

    def test_a_key_change_is_reported_not_sent_as_a_password(self, monkeypatch):
        token = ds_secrets.encrypt("pw")
        monkeypatch.setattr(settings, "secret_key", "another-key")
        assert _code(ds_secrets.decrypt, token) == "data_source_credentials_unreadable"


# ── Schema cache ──────────────────────────────────────────────────────────────

class TestSchemaCache:
    def test_hit_refresh_invalidate_and_config_change(self):
        schema_cache.clear()
        loads = []
        cfg = {"engine": "postgresql", "host": "h", "password_enc": "a"}

        def load():
            loads.append(1)
            return {"n": len(loads)}

        assert schema_cache.get_or_load("t", "s", cfg, ("tables",), load)[0] == {"n": 1}
        assert schema_cache.get_or_load("t", "s", cfg, ("tables",), load)[0] == {"n": 1}
        assert schema_cache.get_or_load("t", "s", cfg, ("tables",), load, refresh=True)[0] == {"n": 2}
        assert schema_cache.get_or_load("t", "s", {**cfg, "password_enc": "b"}, ("tables",), load)[0] == {"n": 3}
        schema_cache.invalidate("t", "s")
        assert schema_cache.get_or_load("t", "s", cfg, ("tables",), load)[0] == {"n": 4}
        assert schema_cache.get_or_load("other", "s", cfg, ("tables",), load)[0] == {"n": 5}


# ── Editing a connection (fake datasets row) ──────────────────────────────────

class _Rows:
    def __init__(self, cfg):
        self.row = {"id": "ds1", "tenant_id": "t", "name": "ERP", "source_type": "sql",
                    "connection_status": "connected", "sql_config": cfg}
        self.updates = []

    def install(self, monkeypatch):
        monkeypatch.setattr(svc, "get_source", lambda t, s: self.row if s == "ds1" else None)

        def execute(sql, params=None):
            self.updates.append((sql, params))
            if sql.lstrip().startswith("UPDATE datasets") and "sql_config=%s" in sql:
                self.row = {**self.row, "sql_config": json.loads(params[0]) if isinstance(params[0], str)
                            else params[0].adapted, "connection_status": "pending"}
        monkeypatch.setattr(svc, "execute", execute)
        return self


@pytest.fixture
def stored(monkeypatch):
    monkeypatch.setattr(settings, "sql_sources_allow_private_hosts", True, raising=False)
    cfg = svc.build_sql_config({"engine": "postgresql", "host": "DB.Example.com", "port": 5432,
                                "database": "erp", "username": "r"}, "old-secret")
    # A legacy row stored the host exactly as typed.
    cfg["host"] = "DB.Example.com"
    return _Rows(cfg).install(monkeypatch)


class TestCredentialRotation:
    def test_unchanged_fields_need_no_retyping(self, stored):
        svc.update_sql_config("t", "ds1", host="db.example.com", port=5432, database="erp2",
                              username="r", engine="postgresql")
        cfg = stored.row["sql_config"]
        assert cfg["database"] == "erp2"
        assert ds_secrets.decrypt(cfg["password_enc"]) == "old-secret"

    @pytest.mark.parametrize("change", [{"host": "evil.example.com"}, {"port": 6543}, {"engine": "mysql"}])
    def test_pointing_elsewhere_requires_the_password_again(self, stored, change):
        before = json.dumps(stored.row["sql_config"], sort_keys=True)
        with pytest.raises(AppError) as exc:
            svc.update_sql_config("t", "ds1", **change)
        assert exc.value.code == "data_source_password_required"
        assert json.dumps(stored.row["sql_config"], sort_keys=True) == before

    def test_with_the_new_password_the_move_is_allowed(self, stored):
        svc.update_sql_config("t", "ds1", host="replica.example.com", password="new-secret")
        cfg = stored.row["sql_config"]
        assert cfg["host"] == "replica.example.com"
        assert ds_secrets.decrypt(cfg["password_enc"]) == "new-secret"

    def test_ca_upload_is_encrypted_and_removable(self, stored):
        pem, _ = _self_signed("Corp Root")
        svc.update_sql_config("t", "ds1", ssl_mode="verify-full", ssl_ca=pem)
        cfg = stored.row["sql_config"]
        assert "BEGIN" not in json.dumps({k: v for k, v in cfg.items() if k != "ssl_ca_enc"})
        assert ds_secrets.decrypt(cfg["ssl_ca_enc"]).startswith("-----BEGIN CERTIFICATE")
        assert cfg["ssl_ca"]["subject"] == "CN=Corp Root"
        public = svc._public(stored.row)["sql_config"]
        assert public["has_ssl_ca"] is True and "ssl_ca_enc" not in public and "password_enc" not in public
        svc.update_sql_config("t", "ds1", clear_ssl_ca=True)
        assert "ssl_ca_enc" not in stored.row["sql_config"]

    def test_verify_ca_without_a_certificate_is_refused(self, stored):
        assert _code(svc.update_sql_config, "t", "ds1", ssl_mode="verify-ca") == "data_source_ssl_ca_required"
