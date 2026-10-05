"""What a connection to a customer's database IS, before anything connects.

Every field a person types into the connection form, or pastes as one
connection string, is normalised and validated here — pure Python, no network,
no driver import — so the same rules hold for the API, the scheduled refresh
and the tests:

* the engine is one of the four the product supports;
* the host is a hostname (unicode allowed, stored as its IDNA/punycode form),
  an IPv4 address or an IPv6 address (brackets accepted and dropped); SQL
  Server also takes a named instance (``HOST\\INSTANCE``);
* the port is 1..65535;
* the TLS mode is one the engine can honour, and a CA certificate is a real PEM
  certificate (the certificate is stored encrypted, like the password);
* the timeouts are inside a range that can neither hang a worker nor make
  every query fail.

Whether the host may be REACHED (private ranges, cloud metadata) is a separate
question answered at connect time by `network.py`, because a hostname only has
an address when it is resolved.

Every refusal is an `AppError` with a stable code and params; the frontend
renders the sentence (`errors.<code>`).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote, urlsplit

from backend.errors import AppError

ENGINES: tuple[str, ...] = ("postgresql", "mysql", "mssql", "oracle")

DEFAULT_PORTS: dict[str, int] = {
    "postgresql": 5432, "mysql": 3306, "mssql": 1433, "oracle": 1521,
}

SSL_MODES: tuple[str, ...] = ("disable", "prefer", "require", "verify-ca", "verify-full")

# What a stored configuration WITHOUT an `ssl_mode` did before TLS modes existed.
# Kept so an existing connection behaves exactly as it did: psycopg2 and PyMySQL
# both default to "use TLS when the server offers it"; the SQL Server URL forced
# encryption and trusted any certificate; Oracle connected in plain TCP.
LEGACY_SSL_MODE: dict[str, str] = {
    "postgresql": "prefer", "mysql": "prefer", "mssql": "require", "oracle": "disable",
}

# The default a NEW connection form proposes. Same as legacy on purpose: a form
# that defaulted to verify-full would fail against every self-signed on-prem
# server, which is most of them.
DEFAULT_SSL_MODE: dict[str, str] = dict(LEGACY_SSL_MODE)

# What each driver can actually do. A mode a driver cannot honour is refused at
# save time instead of being silently weakened at connect time.
#   * mssql (ODBC): no in-memory CA and no "verify the chain but not the name".
#   * oracle (python-oracledb thin): the protocol is chosen up front (tcp or
#     tcps), so there is no "prefer".
SUPPORTED_SSL_MODES: dict[str, tuple[str, ...]] = {
    "postgresql": SSL_MODES,
    "mysql": SSL_MODES,
    "mssql": ("disable", "prefer", "require", "verify-full"),
    "oracle": ("disable", "require", "verify-ca", "verify-full"),
}
CA_CERT_ENGINES = frozenset({"postgresql", "mysql", "oracle"})
MAX_CA_PEM_BYTES = 64 * 1024

CONNECT_TIMEOUT_RANGE = (1, 60)
DEFAULT_CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_RANGE = (1, 600)
DEFAULT_STATEMENT_TIMEOUT_S = 30
# Materialize, export, analysis and the scheduled refresh read whole results:
# they get at least this long, or four times the interactive limit.
BULK_STATEMENT_TIMEOUT_FLOOR_S = 120
BULK_STATEMENT_TIMEOUT_CAP_S = 900

MAX_NAME_CHARS = 128          # database and user names
_REJECTED = 400

_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_INSTANCE = re.compile(r"^[A-Za-z0-9_$#-]{1,32}$")


def _reject(code: str, message: str, **params: Any) -> AppError:
    return AppError(code, message, status_code=_REJECTED, params=params)


# ── Single fields ─────────────────────────────────────────────────────────────

def normalize_engine(engine: Any) -> str:
    value = str(engine or "").strip().lower()
    if value not in ENGINES:
        raise _reject(
            "data_source_engine_unsupported",
            f"Unsupported engine {value!r}. Options: {', '.join(ENGINES)}.",
            engine=value, allowed=", ".join(ENGINES),
        )
    return value


def split_mssql_instance(host: str) -> tuple[str, Optional[str]]:
    """``SRV01\\SQLEXPRESS`` -> (``SRV01``, ``SQLEXPRESS``)."""
    if "\\" not in host:
        return host, None
    base, instance = host.split("\\", 1)
    return base, instance


def normalize_host(raw: Any, engine: str = "postgresql") -> str:
    """The host as it is stored and connected to.

    IP literals are returned in canonical form (IPv6 without brackets);
    hostnames lower-cased and IDNA-encoded (``bücher.example`` ->
    ``xn--bcher-kva.example``). Anything that could smuggle a second meaning
    into a DSN or URL — ``/``, ``@``, ``?``, ``#``, whitespace, ``:`` outside
    an IPv6 literal, ``;`` or ``=`` — is refused.
    """
    host = str(raw or "").strip()
    if not host or len(host) > 260:
        raise _reject("data_source_host_invalid", "The host is empty or too long.", host=host[:80])

    instance: Optional[str] = None
    if engine == "mssql":
        host, instance = split_mssql_instance(host)
        if instance is not None and not _INSTANCE.match(instance):
            raise _reject("data_source_host_invalid",
                          "The SQL Server instance name is not valid.", host=str(raw)[:80])
    elif "\\" in host:
        raise _reject("data_source_host_invalid",
                      "A backslash in the host is only valid for a SQL Server named instance.",
                      host=host[:80])

    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    if "%" in host:
        # A zone id (fe80::1%eth0) names an interface of OUR server, which is
        # never what a customer means and is link-local anyway.
        raise _reject("data_source_host_invalid",
                      "An IPv6 zone identifier is not accepted.", host=host[:80])
    canonical: Optional[str] = None
    try:
        canonical = ipaddress.ip_address(host).compressed
    except ValueError:  # not an IP literal: a host name, checked below
        canonical = None

    if canonical is None:
        if re.search(r"[\s/@?#:;=,'\"<>\\{}|^`]", host):
            raise _reject("data_source_host_invalid",
                          "The host contains characters a host name cannot have.",
                          host=host[:80])
        name = host.rstrip(".").lower()
        try:
            import idna
            ascii_name = idna.encode(name, uts46=True, transitional=False).decode("ascii")
        except Exception:  # idna.IDNAError, UnicodeError
            # Underscores are common in internal DNS names and are not valid
            # IDNA; accept a pure-ASCII name and let the label check decide.
            if not name.isascii():
                raise _reject("data_source_host_invalid",
                              "The host name is not a valid international domain name.",
                              host=host[:80])
            ascii_name = name
        labels = ascii_name.split(".")
        if (not ascii_name or len(ascii_name) > 253
                or any(not _LABEL.match(label) for label in labels)):
            raise _reject("data_source_host_invalid",
                          "The host name is not valid.", host=host[:80])
        canonical = ascii_name

    if instance:
        return f"{canonical}\\{instance}"
    return canonical


def normalize_port(raw: Any) -> int:
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _reject("data_source_port_invalid", "The port must be a number.", port=str(raw)[:20])
    if not 1 <= port <= 65535:
        raise _reject("data_source_port_invalid", "The port must be between 1 and 65535.", port=port)
    return port


def _normalize_name(raw: Any, code: str, label: str, *, required: bool) -> str:
    value = str(raw or "").strip()
    if not value:
        if required:
            raise _reject(code, f"The {label} is required.")
        return ""
    if len(value) > MAX_NAME_CHARS or "\x00" in value:
        raise _reject(code, f"The {label} is not valid.")
    return value


def normalize_ssl_mode(raw: Any, engine: str) -> str:
    mode = str(raw or "").strip().lower().replace("_", "-")
    aliases = {
        # what other tools call the same modes
        "disabled": "disable", "off": "disable", "false": "disable", "no": "disable",
        "preferred": "prefer", "optional": "prefer",
        "required": "require", "true": "require", "yes": "require", "mandatory": "require",
        "verify-identity": "verify-full", "strict": "verify-full",
    }
    mode = aliases.get(mode, mode)
    if not mode:
        return DEFAULT_SSL_MODE[engine]
    if mode not in SSL_MODES:
        raise _reject("data_source_ssl_mode_invalid", f"Unknown TLS mode {mode!r}.",
                      mode=mode, allowed=", ".join(SSL_MODES))
    supported = SUPPORTED_SSL_MODES[engine]
    if mode not in supported:
        raise _reject("data_source_ssl_mode_unsupported",
                      f"The {engine} driver cannot use TLS mode {mode!r}.",
                      engine=engine, mode=mode, allowed=", ".join(supported))
    return mode


def normalize_timeout(raw: Any, field_name: str, bounds: tuple[int, int], default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = -1
    lo, hi = bounds
    if not lo <= value <= hi:
        raise _reject("data_source_timeout_invalid",
                      f"{field_name} must be between {lo} and {hi} seconds.",
                      field=field_name, min=lo, max=hi)
    return value


def inspect_ca_pem(pem: str) -> dict:
    """Validate a PEM CA bundle and return what is safe to display about it
    (subject, expiry, fingerprint, count). Raises on anything that is not one
    or more X.509 certificates."""
    data = (pem or "").strip()
    if not data:
        raise _reject("data_source_ssl_ca_invalid", "The CA certificate is empty.")
    if len(data.encode("utf-8", "replace")) > MAX_CA_PEM_BYTES:
        raise _reject("data_source_ssl_ca_invalid", "The CA certificate file is too large.",
                      max_kb=MAX_CA_PEM_BYTES // 1024)
    if "PRIVATE KEY" in data:
        # Somebody pasted a key. Never store it, never echo it.
        raise _reject("data_source_ssl_ca_invalid",
                      "That file contains a private key, not a CA certificate.")
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        certs = x509.load_pem_x509_certificates(data.encode("ascii"))
    except Exception:
        raise _reject("data_source_ssl_ca_invalid",
                      "The CA certificate is not a valid PEM certificate.")
    first = certs[0]
    try:
        expires = first.not_valid_after_utc.date().isoformat()
    except AttributeError:  # pragma: no cover - older cryptography
        expires = first.not_valid_after.date().isoformat()
    return {
        "subject": first.subject.rfc4514_string()[:200],
        "expires": expires,
        "fingerprint_sha256": first.fingerprint(hashes.SHA256()).hex()[:32],
        "count": len(certs),
    }


# ── A whole connection ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConnectionSpec:
    """Everything needed to open one connection. Secrets are excluded from
    `repr`, so a spec that ends up in a log line or a traceback carries none."""

    engine: str
    host: str
    port: int
    database: str
    username: str
    password: str = field(default="", repr=False)
    ssl_mode: str = "prefer"
    ssl_ca: Optional[str] = field(default=None, repr=False)
    connect_timeout_s: int = DEFAULT_CONNECT_TIMEOUT_S
    statement_timeout_s: int = DEFAULT_STATEMENT_TIMEOUT_S

    @property
    def host_name(self) -> str:
        """The host without a SQL Server instance suffix."""
        return split_mssql_instance(self.host)[0] if self.engine == "mssql" else self.host

    @property
    def instance(self) -> Optional[str]:
        return split_mssql_instance(self.host)[1] if self.engine == "mssql" else None

    @property
    def bulk_timeout_s(self) -> int:
        return min(BULK_STATEMENT_TIMEOUT_CAP_S,
                   max(BULK_STATEMENT_TIMEOUT_FLOOR_S, self.statement_timeout_s * 4))

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in an error, a log or a response."""
        return tuple(s for s in (self.password,) if s)


def normalize_fields(fields: dict) -> dict:
    """Validate the non-secret connection fields and return them normalised.

    Input keys: engine, host, port, database, username, ssl_mode,
    connect_timeout_s, statement_timeout_s. Port defaults to the engine's
    standard port when empty.
    """
    engine = normalize_engine(fields.get("engine"))
    port_raw = fields.get("port")
    port = DEFAULT_PORTS[engine] if port_raw in (None, "") else normalize_port(port_raw)
    return {
        "engine": engine,
        "host": normalize_host(fields.get("host"), engine),
        "port": port,
        "database": _normalize_name(fields.get("database"), "data_source_database_required",
                                    "database", required=True),
        "username": _normalize_name(fields.get("username"), "data_source_username_required",
                                    "user name", required=True),
        "ssl_mode": normalize_ssl_mode(fields.get("ssl_mode"), engine),
        "connect_timeout_s": normalize_timeout(
            fields.get("connect_timeout_s"), "connect_timeout_s",
            CONNECT_TIMEOUT_RANGE, DEFAULT_CONNECT_TIMEOUT_S),
        "statement_timeout_s": normalize_timeout(
            fields.get("statement_timeout_s"), "statement_timeout_s",
            STATEMENT_TIMEOUT_RANGE, DEFAULT_STATEMENT_TIMEOUT_S),
    }


def check_ca_compatible(engine: str, ssl_mode: str, has_ca: bool) -> None:
    """A CA certificate only means something to an engine that can load one,
    and verify-ca without a CA has nothing to verify against."""
    if has_ca and engine not in CA_CERT_ENGINES:
        raise _reject("data_source_ssl_ca_unsupported",
                      f"The {engine} driver cannot use an uploaded CA certificate.",
                      engine=engine)
    if ssl_mode == "verify-ca" and not has_ca:
        raise _reject("data_source_ssl_ca_required",
                      "TLS mode verify-ca needs the server's CA certificate.")


# ── Connection strings ────────────────────────────────────────────────────────

_SCHEMES = {
    "postgresql": "postgresql", "postgres": "postgresql", "pgsql": "postgresql",
    "postgresql+psycopg2": "postgresql", "postgresql+psycopg": "postgresql",
    "mysql": "mysql", "mariadb": "mysql", "mysql+pymysql": "mysql",
    "mysql+mysqldb": "mysql", "mariadb+pymysql": "mysql",
    "mssql": "mssql", "sqlserver": "mssql", "mssql+pyodbc": "mssql",
    "mssql+pymssql": "mssql",
    "oracle": "oracle", "oracle+oracledb": "oracle", "oracle+cx_oracle": "oracle",
}

_SSL_QUERY_KEYS = ("sslmode", "ssl-mode", "ssl_mode", "ssl", "encrypt", "useSSL")


def _ssl_from_params(params: dict[str, str], engine: str) -> Optional[str]:
    low = {k.lower(): v for k, v in params.items()}
    if engine == "mssql":
        enc = low.get("encrypt")
        trust = (low.get("trustservercertificate") or "").lower()
        if enc is None:
            return None
        enc = enc.lower()
        if enc in ("false", "no", "optional"):
            return "prefer" if enc == "optional" else "disable"
        if enc == "strict":
            return "verify-full"
        return "require" if trust in ("true", "yes") else "verify-full"
    for key in ("sslmode", "ssl-mode", "ssl_mode"):
        if key in low:
            return low[key]
    for key in ("ssl", "usessl"):
        if key in low:
            return "require" if low[key].lower() in ("true", "1", "yes", "require") else "disable"
    return None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "")}


def _parse_key_value(text: str, sep: str) -> dict[str, str]:
    """``k=v<sep>k=v`` with ADO.NET-style {braced} or quoted values."""
    out: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in f"{sep} \t\r\n":
            i += 1
        eq = text.find("=", i)
        if eq == -1:
            break
        key = text[i:eq].strip().lower()
        j = eq + 1
        while j < n and text[j] in " \t":
            j += 1
        if j < n and text[j] in "{'\"":
            close = {"{": "}", "'": "'", '"': '"'}[text[j]]
            k = j + 1
            buf = []
            while k < n:
                if text[k] == close:
                    if k + 1 < n and text[k + 1] == close:
                        buf.append(close)
                        k += 2
                        continue
                    break
                buf.append(text[k])
                k += 1
            value = "".join(buf)
            i = k + 1
        else:
            end = text.find(sep, j) if sep != " " else -1
            if sep == " ":
                m = re.search(r"\s", text[j:])
                end = j + m.start() if m else -1
            end = n if end == -1 else end
            value = text[j:end].strip()
            i = end
        if key:
            out[key] = value
    return out


def _first(d: dict, *keys: str) -> Optional[str]:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _host_port(server: str) -> tuple[str, Optional[str]]:
    """``tcp:host,1433`` / ``host:1433`` / ``[::1]:5432`` -> (host, port)."""
    s = server.strip()
    if s.lower().startswith("tcp:"):
        s = s[4:]
    if "," in s:
        h, p = s.rsplit(",", 1)
        return h.strip(), p.strip()
    if s.startswith("["):
        close = s.find("]")
        rest = s[close + 1:]
        return s[1:close], (rest[1:] if rest.startswith(":") else None)
    if s.count(":") == 1:
        h, p = s.split(":")
        return h, p
    return s, None


def parse_connection_string(text: str) -> dict:
    """Read a connection string a DBA would hand over, in any common shape:

    * URLs — ``postgresql://u:p@host:5432/db?sslmode=require``,
      ``mysql://…``, ``mariadb://…``, ``sqlserver://…``, ``oracle://…`` and
      the SQLAlchemy ``dialect+driver://`` spellings, with or without ``jdbc:``;
    * JDBC SQL Server — ``jdbc:sqlserver://host:1433;databaseName=x;user=u``;
    * JDBC Oracle thin — ``jdbc:oracle:thin:@//host:1521/service`` or
      ``…@host:1521:SID``; EZConnect ``user/pass@//host:1521/service``;
    * ADO.NET — ``Server=tcp:host,1433;Database=x;User Id=u;Password=p``;
    * libpq key/value — ``host=x port=5432 dbname=y user=z sslmode=require``.

    Returns the fields it found (engine, host, port, database, username,
    password, ssl_mode), un-normalised; `normalize_fields` validates them.
    Raises `data_source_connection_string_invalid` with a `reason` code.
    """
    raw = (text or "").strip()
    if not raw:
        raise _reject("data_source_connection_string_invalid", "The connection string is empty.",
                      reason="empty")
    if len(raw) > 4096 or "\x00" in raw:
        raise _reject("data_source_connection_string_invalid", "The connection string is too long.",
                      reason="too_long")
    s = raw[5:] if raw.lower().startswith("jdbc:") else raw

    # Oracle thin / EZConnect
    m = re.match(r"(?i)^oracle:thin:(?:(?P<cred>[^@]*))?@(?P<rest>.+)$", s)
    if m:
        out: dict = {"engine": "oracle"}
        cred = m.group("cred") or ""
        if "/" in cred:
            out["username"], out["password"] = cred.split("/", 1)
        rest = m.group("rest")
        if rest.startswith("//"):
            hp, _, svc = rest[2:].partition("/")
            host, port = _host_port(hp)
            out.update(host=host, port=port, database=svc.split("?")[0])
        else:
            parts = rest.split(":")
            if len(parts) == 3:
                out.update(host=parts[0], port=parts[1], database=parts[2])
            else:
                raise _reject("data_source_connection_string_invalid",
                              "The Oracle connection string is not in a known form.",
                              reason="unrecognized_format")
        return _clean(out)
    m = re.match(r"^(?P<user>[^/@\s]+)/(?P<pw>[^@\s]*)@//(?P<hp>[^/\s]+)/(?P<svc>\S+)$", s)
    if m:
        host, port = _host_port(m.group("hp"))
        return _clean({"engine": "oracle", "username": m.group("user"), "password": m.group("pw"),
                       "host": host, "port": port, "database": m.group("svc")})

    # JDBC SQL Server
    m = re.match(r"(?i)^sqlserver://(?P<hp>[^;]*)(?P<props>;.*)?$", s)
    if m and (m.group("props") or "").strip(";"):
        props = _parse_key_value((m.group("props") or "").lstrip(";"), ";")
        host, port = _host_port(m.group("hp")) if m.group("hp") else (None, None)
        host = host or _first(props, "servername", "server")
        if props.get("instancename") and host:
            host = f"{host}\\{props['instancename']}"
        return _clean({
            "engine": "mssql", "host": host,
            "port": port or _first(props, "portnumber", "port"),
            "database": _first(props, "databasename", "database"),
            "username": _first(props, "user", "username", "userid"),
            "password": _first(props, "password"),
            "ssl_mode": _ssl_from_params(props, "mssql"),
        })

    # URLs
    m = re.match(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+._-]*)://", s)
    if m:
        scheme = m.group("scheme").lower()
        engine = _SCHEMES.get(scheme)
        if not engine:
            raise _reject("data_source_connection_string_invalid",
                          f"Unsupported scheme {scheme!r}.", reason="unsupported_scheme",
                          scheme=scheme[:30])
        try:
            parts = urlsplit(s)
            port = parts.port
        except ValueError:
            raise _reject("data_source_connection_string_invalid",
                          "The port in the connection string is not valid.", reason="bad_port")
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        database = unquote(parts.path.lstrip("/")) if parts.path else None
        if engine == "oracle":
            database = params.get("service_name") or database
        if engine == "mssql":
            database = database or params.get("database")
        return _clean({
            "engine": engine,
            "host": parts.hostname,
            "port": port,
            "database": database,
            "username": unquote(parts.username) if parts.username else None,
            "password": unquote(parts.password) if parts.password else None,
            "ssl_mode": _ssl_from_params(params, engine),
        })

    # ADO.NET (semicolon key=value)
    if ";" in s or re.match(r"(?i)^\s*(server|data source|address|addr)\s*=", s):
        props = _parse_key_value(s, ";")
        server = _first(props, "server", "data source", "address", "addr", "network address")
        if not server:
            raise _reject("data_source_connection_string_invalid",
                          "The connection string names no server.", reason="no_host")
        host, port = _host_port(server)
        engine_hint = _first(props, "engine")
        return _clean({
            "engine": engine_hint or "mssql",
            "host": host,
            "port": port or _first(props, "port"),
            "database": _first(props, "database", "initial catalog"),
            "username": _first(props, "user id", "uid", "user", "username"),
            "password": _first(props, "password", "pwd"),
            "ssl_mode": _ssl_from_params(props, "mssql"),
        })

    # libpq key=value
    if re.search(r"(?i)\b(host|hostaddr|dbname)\s*=", s):
        props = _parse_key_value(s, " ")
        return _clean({
            "engine": "postgresql",
            "host": _first(props, "host", "hostaddr"),
            "port": _first(props, "port"),
            "database": _first(props, "dbname"),
            "username": _first(props, "user"),
            "password": _first(props, "password"),
            "ssl_mode": _first(props, "sslmode"),
        })

    raise _reject("data_source_connection_string_invalid",
                  "The connection string is not in a recognised form.",
                  reason="unrecognized_format")
