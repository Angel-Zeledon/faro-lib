"""The pure half of SCIM: no database, no HTTP, nothing but data in and out.

Everything here is exercised by `backend/tests/test_scim_protocol.py` without a
database. The service layer reads the current state of a user, hands it to the
functions here together with what the identity provider sent, and gets back the
state the provider wants - which it then diffs, guards and writes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ── Schema URNs and media type ───────────────────────────────────────────────

SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_ENTERPRISE_USER = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_SPC = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"

MEDIA_TYPE = "application/scim+json"

# ── Roles ────────────────────────────────────────────────────────────────────

# Highest first. When a provider sends several roles for one person, the
# highest one wins: a person holds exactly one role in this product.
ROLES: tuple[str, ...] = ("admin", "analyst", "viewer")
ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}
# What a provisioned person gets when the provider names no role, and what
# leaving the admin or analyst group drops a person to. Least privilege.
DEFAULT_ROLE = "viewer"

GROUP_DISPLAY = {
    "admin": "StockAI Admin",
    "analyst": "StockAI Analyst",
    "viewer": "StockAI Viewer",
}

# Paging. `count` above this is clamped, as RFC 7644 3.4.2.4 allows.
MAX_PAGE = 200
DEFAULT_PAGE = 100

MAX_OPERATIONS = 100
MAX_MEMBERS_PER_REQUEST = 1000
MAX_FILTER_LENGTH = 512
MAX_FILTER_CLAUSES = 5
MAX_NAME_LENGTH = 200
MAX_EXTERNAL_ID_LENGTH = 255


# ── Errors ───────────────────────────────────────────────────────────────────

class ScimError(Exception):
    """A refusal, in the shape RFC 7644 section 3.12 defines.

    `code` is StockAI's own stable identifier (the same vocabulary as
    `AppError`); it travels as `errorCode` next to the standard fields so the
    provisioning log and an IdP administrator reading the raw response both
    see the precise reason. `detail` is English: a provider shows it to an IT
    administrator, never to an end user of this product.
    """

    def __init__(self, status: int, detail: str, *, code: str,
                 scim_type: Optional[str] = None,
                 params: Optional[dict[str, Any]] = None,
                 headers: Optional[dict[str, str]] = None):
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail
        self.code = code
        self.scim_type = scim_type
        self.params = dict(params or {})
        self.headers = dict(headers or {})

    def body(self) -> dict:
        out: dict[str, Any] = {
            "schemas": [SCHEMA_ERROR],
            "status": str(self.status),
            "detail": self.detail,
            "errorCode": self.code,
        }
        if self.scim_type:
            out["scimType"] = self.scim_type
        if self.params:
            out["errorParams"] = self.params
        return out


def invalid_syntax(detail: str, code: str = "scim_invalid_syntax") -> ScimError:
    return ScimError(400, detail, code=code, scim_type="invalidSyntax")


def invalid_value(detail: str, code: str = "scim_invalid_value",
                  params: Optional[dict] = None) -> ScimError:
    return ScimError(400, detail, code=code, scim_type="invalidValue", params=params)


def invalid_filter(detail: str, code: str = "scim_invalid_filter") -> ScimError:
    return ScimError(400, detail, code=code, scim_type="invalidFilter")


# ── Small coercions ──────────────────────────────────────────────────────────

def coerce_bool(value: Any, attribute: str = "active") -> bool:
    """`true`/`false`, or the strings Entra ID sends ("True", "False")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise invalid_value(f"'{attribute}' must be a boolean.", "scim_invalid_boolean",
                        params={"attribute": attribute})


_EMAIL_RE = re.compile(r"^[^@\s\"<>()\[\],;:\\]{1,64}@[a-z0-9.-]{3,253}$")


def normalize_email(value: Any) -> str:
    """The `userName` as this product stores it: a lower-cased e-mail address."""
    if not isinstance(value, str):
        raise invalid_value("'userName' must be a string.", "scim_username_not_email")
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL_RE.match(email) or email.count("@") != 1:
        raise invalid_value("'userName' must be an e-mail address.",
                            "scim_username_not_email")
    return email


def _clean_text(value: Any, attribute: str, max_len: int = MAX_NAME_LENGTH) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid_value(f"'{attribute}' must be a string.", "scim_invalid_value",
                            params={"attribute": attribute})
    text = value.strip()
    if "\x00" in text:
        raise invalid_value(f"'{attribute}' contains a NUL byte.", "scim_invalid_value",
                            params={"attribute": attribute})
    if len(text) > max_len:
        raise invalid_value(f"'{attribute}' is too long.", "scim_value_too_long",
                            params={"attribute": attribute, "max": max_len})
    return text or None


def role_from_value(value: Any) -> str:
    """One role name from whatever shape a provider uses for it."""
    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str) or value.strip().lower() not in ROLE_RANK:
        raise invalid_value(
            "Roles are 'admin', 'analyst' or 'viewer'.", "scim_role_unknown",
            params={"role": str(value)[:40] if value is not None else ""},
        )
    return value.strip().lower()


def highest_role(values: Any) -> Optional[str]:
    """The highest role in a `roles` value (a list, one dict, or one string).
    None for an empty list: the caller decides what "no role" means."""
    if values is None:
        return None
    if not isinstance(values, list):
        values = [values]
    roles = [role_from_value(v) for v in values]
    if not roles:
        return None
    return max(roles, key=lambda r: ROLE_RANK[r])


# ── Filters ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Comparison:
    """`attribute eq value`. `attribute` is lower-cased, URN prefix removed."""
    attribute: str
    value: Any


_TOKEN_RE = re.compile(
    r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<word>[A-Za-z][A-Za-z0-9_.:$\-]*)|(?P<other>\S))'
)


def _strip_schema(path: str) -> str:
    for urn in (SCHEMA_USER, SCHEMA_GROUP):
        if path.lower().startswith(urn.lower() + ":"):
            return path[len(urn) + 1:]
    return path


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        for kind in ("str", "word", "other"):
            if m.group(kind) is not None:
                out.append((kind, m.group(kind)))
                break
    if text[pos:].strip():
        raise invalid_filter("The filter could not be read.")
    return out


def parse_filter(text: Optional[str], allowed: frozenset[str] | set[str]) -> list[Comparison]:
    """`attr eq "value" [and attr eq "value" ...]`, nothing more.

    That is the whole grammar identity providers use against `/Users` and
    `/Groups`. Every other operator, `or`, `not`, grouping and value paths are
    refused with `invalidFilter` rather than half-understood: a filter read
    wrongly answers "no such user", and the provider then CREATES one.

    The values never reach SQL as text: the service binds them as parameters.
    """
    if text is None or not text.strip():
        return []
    if len(text) > MAX_FILTER_LENGTH:
        raise invalid_filter("The filter is too long.")
    tokens = _tokens(text)
    allowed_lower = {a.lower() for a in allowed}
    out: list[Comparison] = []
    i = 0
    while True:
        if i + 3 > len(tokens):
            raise invalid_filter("Expected: attribute eq value.")
        (k_attr, attr), (k_op, op), (k_val, raw) = tokens[i], tokens[i + 1], tokens[i + 2]
        if k_attr != "word":
            raise invalid_filter("Expected an attribute name.")
        if k_op != "word" or op.lower() != "eq":
            raise invalid_filter("Only the 'eq' operator is supported.",
                                 "scim_filter_operator_unsupported")
        name = _strip_schema(attr).lower()
        if name not in allowed_lower:
            raise invalid_filter(f"Filtering on '{attr[:60]}' is not supported.",
                                 "scim_filter_attribute_unsupported")
        if k_val == "str":
            try:
                value: Any = json.loads(raw)
            except ValueError:
                raise invalid_filter("A quoted value could not be read.")
        elif k_val == "word" and raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            raise invalid_filter("Values must be quoted strings or true/false.")
        out.append(Comparison(name, value))
        i += 3
        if len(out) > MAX_FILTER_CLAUSES:
            raise invalid_filter("Too many conditions in the filter.")
        if i == len(tokens):
            return out
        kind, word = tokens[i]
        if kind != "word" or word.lower() != "and":
            raise invalid_filter("Only 'and' may join conditions.",
                                 "scim_filter_operator_unsupported")
        i += 1


def parse_paging(start_index: Any, count: Any) -> tuple[int, int]:
    """(offset, limit) from SCIM's 1-based `startIndex` and `count`.

    RFC 7644 3.4.2.4: a startIndex below 1 is read as 1, a negative count as 0,
    and a count above the server's maximum is clamped. Unreadable numbers fall
    back to the defaults instead of failing a sync over a query string.
    """
    def _int(value: Any, default: int) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default
    start = max(1, _int(start_index, 1)) if start_index is not None else 1
    limit = _int(count, DEFAULT_PAGE) if count is not None else DEFAULT_PAGE
    limit = min(max(0, limit), MAX_PAGE)
    return start - 1, limit


# ── The state of a user, as SCIM sees it ─────────────────────────────────────

@dataclass
class UserState:
    """The attributes SCIM can change. Everything else on the user row is
    the product's own and never touched by provisioning."""
    email: str
    full_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    external_id: Optional[str] = None
    active: bool = True
    role: str = DEFAULT_ROLE
    # Attributes the provider sent that this product does not model (title,
    # phone numbers, the enterprise extension...). Recorded, never an error:
    # refusing them would break every real provider's default mapping.
    ignored: list[str] = field(default_factory=list)

    def copy(self) -> "UserState":
        return UserState(self.email, self.full_name, self.given_name, self.family_name,
                         self.external_id, self.active, self.role, list(self.ignored))


# Attributes accepted and then dropped on purpose.
_ALWAYS_IGNORED = {"schemas", "id", "meta", "password", "groups"}


class _NameTracker:
    """Which name attributes a request touched, to decide `full_name`.

    The product stores one display name. A request that sets displayName sets
    it; otherwise `name.formatted`; otherwise given + family name. A request
    that touches none of them leaves the stored name alone.
    """

    def __init__(self):
        self.display: Optional[str] = None
        self.display_set = False
        self.formatted: Optional[str] = None
        self.formatted_set = False
        self.parts_set = False

    def resolve(self, state: UserState) -> None:
        if self.display_set and self.display:
            state.full_name = self.display
        elif self.formatted_set and self.formatted:
            state.full_name = self.formatted
        elif self.parts_set or self.display_set or self.formatted_set:
            joined = " ".join(p for p in (state.given_name, state.family_name) if p)
            state.full_name = joined or None


def _apply_name_dict(state: UserState, names: _NameTracker, value: Any, *, replace_all: bool) -> None:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise invalid_value("'name' must be an object.", "scim_invalid_value",
                            params={"attribute": "name"})
    lowered = {str(k).lower(): v for k, v in value.items()}
    if replace_all or "givenname" in lowered:
        state.given_name = _clean_text(lowered.get("givenname"), "name.givenName")
        names.parts_set = True
    if replace_all or "familyname" in lowered:
        state.family_name = _clean_text(lowered.get("familyname"), "name.familyName")
        names.parts_set = True
    if replace_all or "formatted" in lowered:
        names.formatted = _clean_text(lowered.get("formatted"), "name.formatted")
        names.formatted_set = True


def _set_attribute(state: UserState, names: _NameTracker, path: str, value: Any,
                   op: str) -> None:
    """Apply one `add`/`replace` of `path` (already lower-cased, URN-stripped)."""
    if path == "active":
        state.active = coerce_bool(value)
    elif path == "username":
        state.email = normalize_email(value)
    elif path == "externalid":
        state.external_id = _clean_text(value, "externalId", MAX_EXTERNAL_ID_LENGTH)
    elif path == "displayname":
        names.display = _clean_text(value, "displayName")
        names.display_set = True
    elif path == "name":
        _apply_name_dict(state, names, value, replace_all=False)
    elif path == "name.givenname":
        state.given_name = _clean_text(value, "name.givenName")
        names.parts_set = True
    elif path == "name.familyname":
        state.family_name = _clean_text(value, "name.familyName")
        names.parts_set = True
    elif path == "name.formatted":
        names.formatted = _clean_text(value, "name.formatted")
        names.formatted_set = True
    elif path == "roles":
        role = highest_role(value)
        if op == "add":
            # `add` to a single-valued role: the highest of what is there
            # now and what was added. Adding "viewer" to an analyst does not
            # demote them.
            if role and ROLE_RANK[role] > ROLE_RANK[state.role]:
                state.role = role
        else:
            state.role = role or DEFAULT_ROLE
    elif _ROLE_VALUE_PATH.match(path):
        # Entra ID's usual mapping: `roles[primary eq "True"].value`.
        state.role = role_from_value(value)
    else:
        # Includes `emails`: it mirrors userName for every provider we target,
        # and this product keeps ONE address - the userName.
        state.ignored.append(path)


_ROLE_VALUE_PATH = re.compile(r'^roles\[[^\]]*\]\.value$')
_ROLE_FILTER_PATH = re.compile(r'^roles\[\s*value\s+eq\s+"((?:[^"\\]|\\.)*)"\s*\]$', re.I)
_MEMBER_FILTER_PATH = re.compile(r'^members\[\s*value\s+eq\s+"((?:[^"\\]|\\.)*)"\s*\]$', re.I)


def _unquote(inner: str) -> str:
    """The value inside a `[value eq "..."]` path, JSON escapes decoded."""
    try:
        out = json.loads(f'"{inner}"')
    except ValueError:
        raise ScimError(400, "The path could not be read.", code="scim_invalid_path",
                        scim_type="invalidPath")
    if not isinstance(out, str):
        raise ScimError(400, "The path could not be read.", code="scim_invalid_path",
                        scim_type="invalidPath")
    return out


def _remove_attribute(state: UserState, names: _NameTracker, path: str, value: Any) -> None:
    if path == "roles":
        if value is None:
            state.role = DEFAULT_ROLE
        else:
            removed = {role_from_value(v) for v in (value if isinstance(value, list) else [value])}
            if state.role in removed:
                state.role = DEFAULT_ROLE
        return
    m = _ROLE_FILTER_PATH.match(path)
    if m:
        if state.role == role_from_value(_unquote(m.group(1))):
            state.role = DEFAULT_ROLE
        return
    if path in ("username", "active"):
        raise ScimError(400, f"'{path}' cannot be removed.", code="scim_attribute_required",
                        scim_type="mutability", params={"attribute": path})
    if path == "externalid":
        state.external_id = None
    elif path == "displayname":
        names.display, names.display_set = None, True
    elif path == "name":
        _apply_name_dict(state, names, {}, replace_all=True)
    elif path == "name.givenname":
        state.given_name, names.parts_set = None, True
    elif path == "name.familyname":
        state.family_name, names.parts_set = None, True
    elif path == "name.formatted":
        names.formatted, names.formatted_set = None, True
    else:
        state.ignored.append(path)


def _patch_operations(body: Any) -> list[dict]:
    if not isinstance(body, dict):
        raise invalid_syntax("The request body must be a JSON object.")
    ops = body.get("Operations", body.get("operations"))
    if not isinstance(ops, list) or not ops:
        raise invalid_syntax("A PATCH needs a non-empty 'Operations' list.")
    if len(ops) > MAX_OPERATIONS:
        raise ScimError(400, "Too many operations in one request.", code="scim_too_many_operations",
                        scim_type="tooMany", params={"max": MAX_OPERATIONS})
    for op in ops:
        if not isinstance(op, dict):
            raise invalid_syntax("Each operation must be a JSON object.")
        name = str(op.get("op", "")).strip().lower()
        if name not in ("add", "replace", "remove"):
            raise invalid_syntax("'op' must be add, replace or remove.", "scim_invalid_patch_op")
        path = op.get("path")
        if path is not None and (not isinstance(path, str) or len(path) > 300):
            raise ScimError(400, "'path' must be a string.", code="scim_invalid_path",
                            scim_type="invalidPath")
    return ops


def _norm_path(path: str) -> str:
    path = path.strip()
    if path.lower().startswith(SCHEMA_ENTERPRISE_USER.lower()):
        return "enterprise:" + path[len(SCHEMA_ENTERPRISE_USER):].lstrip(":").lower()
    return _strip_schema(path).lower()


def apply_user_patch(current: UserState, body: Any) -> UserState:
    """The state a PatchOp asks for, computed from `current`. Pure.

    Covers what Okta and Entra ID actually send:
      * Okta: `{"op": "replace", "value": {"active": false}}` (no path)
      * Entra: `{"op": "Replace", "path": "active", "value": "False"}`
      * names by path (`name.givenName`) or as a `name` object
      * `userName`, `displayName`, `externalId`
      * roles: `add`/`replace` with `[{"value": "analyst"}]`, `remove` with or
        without a value, `roles[value eq "admin"]`,
        `roles[primary eq "True"].value`
    """
    ops = _patch_operations(body)
    state = current.copy()
    state.ignored = []
    names = _NameTracker()
    for op in ops:
        name = str(op["op"]).strip().lower()
        path = op.get("path")
        value = op.get("value")
        if path is None or not str(path).strip():
            if name == "remove":
                raise ScimError(400, "'remove' needs a path.", code="scim_remove_needs_path",
                                scim_type="noTarget")
            if not isinstance(value, dict):
                raise invalid_syntax("Without a path, 'value' must be an object.")
            for key, v in value.items():
                key_norm = _norm_path(str(key))
                if key_norm.startswith("enterprise:") or str(key).lower() == SCHEMA_ENTERPRISE_USER.lower():
                    state.ignored.append(SCHEMA_ENTERPRISE_USER)
                    continue
                _set_attribute(state, names, key_norm, v, name)
            continue
        norm = _norm_path(str(path))
        if norm.startswith("enterprise:"):
            state.ignored.append(norm)
            continue
        if name == "remove":
            _remove_attribute(state, names, norm, value)
        else:
            _set_attribute(state, names, norm, value, name)
    names.resolve(state)
    return state


def user_from_resource(body: Any, current: Optional[UserState] = None) -> UserState:
    """The state a full User resource (POST, PUT) asks for.

    On PUT, `active` and `roles` that are ABSENT keep their current values
    instead of being cleared: Okta's PUT carries the whole profile but often no
    roles, and reading "no roles" as "demote to viewer" would demote people on
    every routine sync. The names and externalId ARE replaced, as RFC 7644
    3.5.1 says.
    """
    if not isinstance(body, dict):
        raise invalid_syntax("The request body must be a JSON object.")
    lowered = {str(k).lower(): v for k, v in body.items()}
    if "username" not in lowered:
        raise invalid_value("'userName' is required.", "scim_username_required")
    state = UserState(
        email=normalize_email(lowered["username"]),
        active=current.active if current else True,
        role=current.role if current else DEFAULT_ROLE,
    )
    names = _NameTracker()
    _apply_name_dict(state, names, lowered.get("name"), replace_all=True)
    names.display = _clean_text(lowered.get("displayname"), "displayName")
    names.display_set = True
    state.external_id = _clean_text(lowered.get("externalid"), "externalId",
                                    MAX_EXTERNAL_ID_LENGTH)
    if "active" in lowered and lowered["active"] is not None:
        state.active = coerce_bool(lowered["active"])
    if "roles" in lowered and lowered["roles"] is not None:
        role = highest_role(lowered["roles"])
        state.role = role or DEFAULT_ROLE
    known = {"username", "name", "displayname", "externalid", "active", "roles", "emails"}
    for key in lowered:
        if key not in known and key not in _ALWAYS_IGNORED:
            state.ignored.append(key)
    names.resolve(state)
    return state


# ── Groups ───────────────────────────────────────────────────────────────────

def group_id_for(display_or_id: Any) -> Optional[str]:
    """The role a group name or id refers to, or None. Case-insensitive, and
    accepts both the display name and the bare role."""
    if not isinstance(display_or_id, str):
        return None
    v = display_or_id.strip().lower()
    for role, display in GROUP_DISPLAY.items():
        if v in (role, display.lower()):
            return role
    return None


@dataclass
class MembershipChange:
    """What a group request asks for. `replace` is the full new member list
    (None when the request did not replace membership)."""
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    replace: Optional[list[str]] = None
    ignored: list[str] = field(default_factory=list)


def _member_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    out: list[str] = []
    for item in value:
        uid = item.get("value") if isinstance(item, dict) else item
        if not isinstance(uid, str) or not uid.strip() or len(uid) > 64 or "\x00" in uid:
            raise invalid_value("Each member needs a 'value' with a user id.",
                                "scim_member_invalid")
        out.append(uid.strip())
    if len(out) > MAX_MEMBERS_PER_REQUEST:
        raise ScimError(400, "Too many members in one request.", code="scim_too_many_members",
                        scim_type="tooMany", params={"max": MAX_MEMBERS_PER_REQUEST})
    return out


def _check_group_rename(role: str, value: Any) -> None:
    if value is None or group_id_for(value) == role:
        return
    raise ScimError(400, "Role groups cannot be renamed.", code="scim_group_immutable",
                    scim_type="mutability")


def apply_group_patch(role: str, body: Any) -> MembershipChange:
    """Okta: `add members`, `remove` with `members[value eq "id"]`, `replace`
    of the whole list. Entra: `Add`/`Remove` with `path: members` and a value
    list. A rename to anything but the group's own name is refused."""
    ops = _patch_operations(body)
    change = MembershipChange()
    for op in ops:
        name = str(op["op"]).strip().lower()
        path = (op.get("path") or "").strip()
        value = op.get("value")
        low = _strip_schema(path).lower() if path else ""
        if not low:
            if name == "remove":
                raise ScimError(400, "'remove' needs a path.", code="scim_remove_needs_path",
                                scim_type="noTarget")
            if not isinstance(value, dict):
                raise invalid_syntax("Without a path, 'value' must be an object.")
            for key, v in value.items():
                k = str(key).lower()
                if k == "members":
                    ids = _member_ids(v)
                    if name == "replace":
                        change.replace, change.add, change.remove = ids, [], []
                    else:
                        change.add += ids
                elif k == "displayname":
                    _check_group_rename(role, v)
                elif k in ("id", "schemas", "meta"):
                    continue
                else:
                    change.ignored.append(k)
            continue
        m = _MEMBER_FILTER_PATH.match(path)
        if m:
            if name != "remove":
                raise ScimError(400, "Only 'remove' may target one member.",
                                code="scim_invalid_path", scim_type="invalidPath")
            change.remove += _member_ids([_unquote(m.group(1))])
            continue
        if low == "members":
            ids = _member_ids(value)
            if name == "add":
                change.add += ids
            elif name == "replace":
                change.replace, change.add, change.remove = ids, [], []
            elif value is None:
                change.replace, change.add, change.remove = [], [], []
            else:
                change.remove += ids
        elif low == "displayname":
            if name == "remove":
                raise ScimError(400, "Role groups cannot be renamed.",
                                code="scim_group_immutable", scim_type="mutability")
            _check_group_rename(role, value)
        else:
            change.ignored.append(low)
    return change


def group_from_resource(role: str, body: Any) -> list[str]:
    """PUT /Groups/{id}: the full member list it asks for."""
    if not isinstance(body, dict):
        raise invalid_syntax("The request body must be a JSON object.")
    lowered = {str(k).lower(): v for k, v in body.items()}
    _check_group_rename(role, lowered.get("displayname"))
    return _member_ids(lowered.get("members"))


def role_changes_for_group(role: str, change: MembershipChange,
                           current_members: set[str]) -> dict[str, str]:
    """user_id -> new role, for one group request.

    Leaving the admin or analyst group drops a person to the default role.
    Leaving the viewer group changes nothing: viewer is the floor, and taking
    access away is `active: false`, not a group.
    """
    out: dict[str, str] = {}
    if change.replace is not None:
        wanted = list(dict.fromkeys(change.replace))
        for uid in current_members - set(wanted):
            if role != DEFAULT_ROLE:
                out[uid] = DEFAULT_ROLE
        for uid in wanted:
            out[uid] = role
    for uid in change.remove:
        if uid in current_members and role != DEFAULT_ROLE:
            out[uid] = DEFAULT_ROLE
    for uid in change.add:
        out[uid] = role
    return out


# ── Rendering ────────────────────────────────────────────────────────────────

def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def is_active(status: Optional[str]) -> bool:
    return (status or "active") == "active"


def user_version(row: dict) -> str:
    """A weak ETag over every attribute the resource shows."""
    parts = [str(row.get(k)) for k in ("id", "email", "full_name", "role", "status",
                                        "updated_at", "external_id", "given_name",
                                        "family_name")]
    return 'W/"' + hashlib.sha256("|".join(parts).encode()).hexdigest()[:20] + '"'


def user_resource(row: dict, base_url: str) -> dict:
    """A users row (joined with its `scim_user_links` row) as a SCIM User."""
    full = row.get("full_name") or None
    name: dict[str, Any] = {}
    if row.get("given_name"):
        name["givenName"] = row["given_name"]
    if row.get("family_name"):
        name["familyName"] = row["family_name"]
    if full:
        name["formatted"] = full
    out: dict[str, Any] = {
        "schemas": [SCHEMA_USER],
        "id": row["id"],
        "userName": row["email"],
        "active": is_active(row.get("status")),
        "emails": [{"value": row["email"], "type": "work", "primary": True}],
        "roles": [{"value": row["role"], "display": GROUP_DISPLAY.get(row["role"], row["role"]),
                   "primary": True}],
        "groups": [{"value": row["role"], "display": GROUP_DISPLAY.get(row["role"], row["role"]),
                    "$ref": f"{base_url}/Groups/{row['role']}"}],
        "meta": {
            "resourceType": "User",
            "created": _iso(row.get("created_at")),
            "lastModified": _iso(row.get("updated_at") or row.get("created_at")),
            "location": f"{base_url}/Users/{row['id']}",
            "version": user_version(row),
        },
    }
    if name:
        out["name"] = name
    if full:
        out["displayName"] = full
    if row.get("external_id"):
        out["externalId"] = row["external_id"]
    return out


def group_version(role: str, members: list[dict]) -> str:
    ids = ",".join(sorted(m["id"] for m in members))
    return 'W/"' + hashlib.sha256(f"{role}|{ids}".encode()).hexdigest()[:20] + '"'


def group_resource(role: str, members: list[dict], base_url: str, *,
                   include_members: bool = True) -> dict:
    out: dict[str, Any] = {
        "schemas": [SCHEMA_GROUP],
        "id": role,
        "displayName": GROUP_DISPLAY[role],
        "meta": {
            "resourceType": "Group",
            "location": f"{base_url}/Groups/{role}",
            "version": group_version(role, members),
        },
    }
    if include_members:
        out["members"] = [
            {"value": m["id"], "display": m["email"], "$ref": f"{base_url}/Users/{m['id']}"}
            for m in members
        ]
    return out


def list_response(resources: list[dict], total: int, start_index: int) -> dict:
    return {
        "schemas": [SCHEMA_LIST],
        "totalResults": int(total),
        "startIndex": int(start_index),
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def excluded(attrs: Optional[str]) -> set[str]:
    return {a.strip().lower() for a in (attrs or "").split(",") if a.strip()}


# ── Discovery documents ──────────────────────────────────────────────────────

def service_provider_config(base_url: str) -> dict:
    return {
        "schemas": [SCHEMA_SPC],
        "documentationUri": None,
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_PAGE},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": True},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "Bearer token",
            "description": "The SCIM token a tenant administrator creates in StockAI's "
                           "company sign-in settings.",
            "primary": True,
        }],
        "meta": {"resourceType": "ServiceProviderConfig",
                 "location": f"{base_url}/ServiceProviderConfig"},
    }


def resource_types(base_url: str) -> list[dict]:
    return [
        {"schemas": [SCHEMA_RESOURCE_TYPE], "id": "User", "name": "User",
         "endpoint": "/Users", "description": "A person in this StockAI account",
         "schema": SCHEMA_USER, "schemaExtensions": [],
         "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/User"}},
        {"schemas": [SCHEMA_RESOURCE_TYPE], "id": "Group", "name": "Group",
         "endpoint": "/Groups", "description": "One of the three StockAI roles",
         "schema": SCHEMA_GROUP, "schemaExtensions": [],
         "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/Group"}},
    ]


def _attr(name: str, type_: str = "string", *, required: bool = False,
          multi: bool = False, mutability: str = "readWrite", uniqueness: str = "none",
          sub: Optional[list[dict]] = None, case_exact: bool = False) -> dict:
    out = {"name": name, "type": type_, "multiValued": multi, "required": required,
           "caseExact": case_exact, "mutability": mutability, "returned": "default",
           "uniqueness": uniqueness, "description": ""}
    if sub:
        out["subAttributes"] = sub
    return out


def schemas(base_url: str) -> list[dict]:
    user_attrs = [
        _attr("userName", required=True, uniqueness="server"),
        _attr("name", "complex", sub=[_attr("givenName"), _attr("familyName"), _attr("formatted")]),
        _attr("displayName"),
        _attr("externalId", case_exact=True),
        _attr("active", "boolean"),
        _attr("emails", "complex", multi=True, mutability="readOnly",
              sub=[_attr("value"), _attr("type"), _attr("primary", "boolean")]),
        _attr("roles", "complex", multi=True,
              sub=[_attr("value"), _attr("display"), _attr("primary", "boolean")]),
        _attr("groups", "complex", multi=True, mutability="readOnly",
              sub=[_attr("value"), _attr("display")]),
    ]
    group_attrs = [
        _attr("displayName", required=True, mutability="readOnly", uniqueness="server"),
        _attr("members", "complex", multi=True,
              sub=[_attr("value"), _attr("display", mutability="readOnly")]),
    ]
    return [
        {"schemas": [SCHEMA_SCHEMA], "id": SCHEMA_USER, "name": "User",
         "description": "StockAI user", "attributes": user_attrs,
         "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{SCHEMA_USER}"}},
        {"schemas": [SCHEMA_SCHEMA], "id": SCHEMA_GROUP, "name": "Group",
         "description": "StockAI role group", "attributes": group_attrs,
         "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{SCHEMA_GROUP}"}},
    ]
