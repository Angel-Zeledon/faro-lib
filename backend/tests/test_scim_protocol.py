"""SCIM's pure half (backend/scim/protocol.py, backend/scim/tokens.py).

No database: these run with `--noconftest`. The payloads are the shapes Okta
and Microsoft Entra ID actually send, because a parser that is right about the
RFC and wrong about the providers provisions nobody.
"""

from __future__ import annotations

import pytest

from backend.scim import protocol, tokens
from backend.scim.protocol import ScimError, UserState

PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _ops(*ops):
    return {"schemas": [PATCH], "Operations": list(ops)}


def _state(**over):
    base = dict(email="ana@acme.test", full_name="Ana Diaz", given_name="Ana",
                family_name="Diaz", external_id="00u1", active=True, role="analyst")
    base.update(over)
    return UserState(**base)


# ── Filter parser ────────────────────────────────────────────────────────────

USER_ATTRS = frozenset({"username", "emails.value", "externalid", "id"})


class TestFilter:
    def test_okta_and_entra_lookup(self):
        [c] = protocol.parse_filter('userName eq "ana@acme.test"', USER_ATTRS)
        assert (c.attribute, c.value) == ("username", "ana@acme.test")

    def test_operator_and_attribute_are_case_insensitive(self):
        [c] = protocol.parse_filter('USERNAME EQ "x@acme.test"', USER_ATTRS)
        assert c.attribute == "username"

    def test_schema_urn_prefix_is_accepted(self):
        [c] = protocol.parse_filter(
            'urn:ietf:params:scim:schemas:core:2.0:User:userName eq "a@acme.test"', USER_ATTRS)
        assert c.attribute == "username"

    def test_and_joins_clauses(self):
        comps = protocol.parse_filter('externalId eq "00u1" and userName eq "a@acme.test"',
                                      USER_ATTRS)
        assert [(c.attribute, c.value) for c in comps] == [
            ("externalid", "00u1"), ("username", "a@acme.test")]

    def test_escaped_quotes_are_decoded_not_ended(self):
        [c] = protocol.parse_filter(r'userName eq "a\"b@acme.test"', USER_ATTRS)
        assert c.value == 'a"b@acme.test'

    def test_sql_in_a_value_stays_a_literal_value(self):
        [c] = protocol.parse_filter('''userName eq "x'; DROP TABLE users;--"''', USER_ATTRS)
        assert c.value == "x'; DROP TABLE users;--"

    @pytest.mark.parametrize("text", [
        'userName eq "x" or userName eq "y"',          # `or` is not supported
        'userName eq "x" or 1 eq 1',                   # injection attempt
        'not (userName eq "x")',
        '(userName eq "x")',
        'userName eq x',                               # unquoted value
        'userName eq "x',                              # unterminated quote
        'userName eq "x" )',                           # trailing garbage
        'userName eq "x" and',                         # dangling and
        'emails[type eq "work"].value eq "x"',         # value path
        'userName',
        'eq "x"',
        'userName eq 5',
    ])
    def test_malformed_or_unsupported_is_invalid_filter(self, text):
        with pytest.raises(ScimError) as exc:
            protocol.parse_filter(text, USER_ATTRS)
        assert exc.value.status == 400
        assert exc.value.scim_type == "invalidFilter"

    @pytest.mark.parametrize("op", ["ne", "co", "sw", "ew", "gt", "lt", "ge", "le", "pr"])
    def test_other_operators_are_refused_by_name(self, op):
        with pytest.raises(ScimError) as exc:
            protocol.parse_filter(f'userName {op} "x"', USER_ATTRS)
        assert exc.value.code == "scim_filter_operator_unsupported"

    def test_unknown_attribute_is_refused(self):
        with pytest.raises(ScimError) as exc:
            protocol.parse_filter('password eq "x"', USER_ATTRS)
        assert exc.value.code == "scim_filter_attribute_unsupported"

    def test_overlong_filter_is_refused(self):
        with pytest.raises(ScimError):
            protocol.parse_filter('userName eq "' + "a" * 600 + '"', USER_ATTRS)

    def test_too_many_clauses_is_refused(self):
        text = " and ".join(['userName eq "x"'] * (protocol.MAX_FILTER_CLAUSES + 1))
        with pytest.raises(ScimError):
            protocol.parse_filter(text, USER_ATTRS)

    def test_empty_filter_is_no_condition(self):
        assert protocol.parse_filter(None, USER_ATTRS) == []
        assert protocol.parse_filter("  ", USER_ATTRS) == []

    def test_boolean_value(self):
        [c] = protocol.parse_filter('id eq true', USER_ATTRS)
        assert c.value is True


class TestPaging:
    def test_defaults(self):
        assert protocol.parse_paging(None, None) == (0, protocol.DEFAULT_PAGE)

    def test_start_index_is_one_based_and_floored(self):
        assert protocol.parse_paging("11", "5") == (10, 5)
        assert protocol.parse_paging("0", "5") == (0, 5)
        assert protocol.parse_paging("-3", "5") == (0, 5)

    def test_count_is_clamped(self):
        assert protocol.parse_paging("1", "100000") == (0, protocol.MAX_PAGE)
        assert protocol.parse_paging("1", "-1") == (0, 0)

    def test_garbage_falls_back_to_defaults(self):
        assert protocol.parse_paging("abc", "xyz") == (0, protocol.DEFAULT_PAGE)


# ── PATCH on users ───────────────────────────────────────────────────────────

class TestUserPatchOkta:
    def test_deactivate_without_path(self):
        out = protocol.apply_user_patch(_state(), _ops({"op": "replace", "value": {"active": False}}))
        assert out.active is False
        assert out.role == "analyst" and out.email == "ana@acme.test"

    def test_reactivate(self):
        out = protocol.apply_user_patch(_state(active=False),
                                        _ops({"op": "replace", "value": {"active": True}}))
        assert out.active is True

    def test_profile_replace_without_path(self):
        out = protocol.apply_user_patch(_state(), _ops({"op": "replace", "value": {
            "userName": "Ana.New@ACME.test",
            "name": {"givenName": "Anabel", "familyName": "Diaz"},
        }}))
        assert out.email == "ana.new@acme.test"
        assert (out.given_name, out.family_name) == ("Anabel", "Diaz")
        assert out.full_name == "Anabel Diaz"

    def test_does_not_mutate_the_input(self):
        current = _state()
        protocol.apply_user_patch(current, _ops({"op": "replace", "value": {"active": False}}))
        assert current.active is True


class TestUserPatchEntra:
    def test_active_as_a_string(self):
        out = protocol.apply_user_patch(_state(), _ops(
            {"op": "Replace", "path": "active", "value": "False"}))
        assert out.active is False

    def test_paths_for_names_and_ignored_attributes(self):
        out = protocol.apply_user_patch(_state(), _ops(
            {"op": "Add", "path": "name.givenName", "value": "Juana"},
            {"op": "Replace", "path": 'emails[type eq "work"].value', "value": "ana@acme.test"},
            {"op": "Replace", "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department",
             "value": "Sales"},
            {"op": "Replace", "path": "title", "value": "Buyer"},
        ))
        assert out.given_name == "Juana"
        assert out.full_name == "Juana Diaz"
        assert "title" in out.ignored
        assert any(i.startswith("enterprise:") for i in out.ignored)

    def test_display_name_wins_over_parts(self):
        out = protocol.apply_user_patch(_state(), _ops(
            {"op": "Replace", "path": "displayName", "value": "Ana D."},
            {"op": "Replace", "path": "name.familyName", "value": "Duarte"},
        ))
        assert out.full_name == "Ana D."
        assert out.family_name == "Duarte"

    def test_external_id_and_username(self):
        out = protocol.apply_user_patch(_state(), _ops(
            {"op": "Replace", "path": "externalId", "value": "ext-9"},
            {"op": "Replace", "path": "userName", "value": "other@acme.test"},
        ))
        assert (out.external_id, out.email) == ("ext-9", "other@acme.test")

    def test_bad_boolean_is_invalid_value(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops({"op": "Replace", "path": "active", "value": "maybe"}))
        assert exc.value.scim_type == "invalidValue"

    def test_username_must_be_an_email(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops({"op": "Replace", "path": "userName", "value": "jdoe"}))
        assert exc.value.code == "scim_username_not_email"


class TestRoles:
    def test_add_roles(self):
        out = protocol.apply_user_patch(_state(role="viewer"), _ops(
            {"op": "add", "path": "roles", "value": [{"value": "analyst"}]}))
        assert out.role == "analyst"

    def test_add_a_lower_role_does_not_demote(self):
        out = protocol.apply_user_patch(_state(role="analyst"), _ops(
            {"op": "add", "path": "roles", "value": [{"value": "viewer"}]}))
        assert out.role == "analyst"

    def test_replace_roles_takes_the_highest(self):
        out = protocol.apply_user_patch(_state(role="viewer"), _ops(
            {"op": "replace", "path": "roles", "value": [{"value": "viewer"}, {"value": "Admin"}]}))
        assert out.role == "admin"

    def test_replace_with_empty_list_is_the_default_role(self):
        out = protocol.apply_user_patch(_state(role="analyst"), _ops(
            {"op": "replace", "path": "roles", "value": []}))
        assert out.role == protocol.DEFAULT_ROLE

    def test_entra_primary_value_path(self):
        out = protocol.apply_user_patch(_state(role="viewer"), _ops(
            {"op": "Replace", "path": 'roles[primary eq "True"].value', "value": "analyst"}))
        assert out.role == "analyst"

    def test_remove_by_value_filter(self):
        out = protocol.apply_user_patch(_state(role="admin"), _ops(
            {"op": "remove", "path": 'roles[value eq "admin"]'}))
        assert out.role == "viewer"

    def test_remove_another_role_changes_nothing(self):
        out = protocol.apply_user_patch(_state(role="analyst"), _ops(
            {"op": "remove", "path": "roles", "value": [{"value": "admin"}]}))
        assert out.role == "analyst"

    def test_unknown_role_is_refused(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops(
                {"op": "add", "path": "roles", "value": [{"value": "superuser"}]}))
        assert exc.value.code == "scim_role_unknown"

    def test_highest_role(self):
        assert protocol.highest_role([{"value": "viewer"}, "analyst"]) == "analyst"
        assert protocol.highest_role([]) is None
        assert protocol.highest_role(None) is None


class TestPatchSyntax:
    @pytest.mark.parametrize("body", [
        {}, {"Operations": []}, {"Operations": "x"}, [1, 2],
        {"Operations": [{"op": "move", "path": "active", "value": True}]},
        {"Operations": ["replace"]},
    ])
    def test_malformed_bodies(self, body):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), body)
        assert exc.value.status == 400

    def test_remove_without_path(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops({"op": "remove"}))
        assert exc.value.scim_type == "noTarget"

    def test_username_cannot_be_removed(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops({"op": "remove", "path": "userName"}))
        assert exc.value.scim_type == "mutability"

    def test_too_many_operations(self):
        body = _ops(*[{"op": "replace", "path": "active", "value": True}] * (protocol.MAX_OPERATIONS + 1))
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), body)
        assert exc.value.scim_type == "tooMany"

    def test_bad_escape_in_a_value_path_is_a_400_not_a_crash(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_user_patch(_state(), _ops({"op": "remove", "path": r'roles[value eq "\x"]'}))
        assert exc.value.status == 400


# ── Full resources (POST / PUT) ──────────────────────────────────────────────

OKTA_CREATE = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "jdoe@acme.test",
    "name": {"givenName": "John", "familyName": "Doe"},
    "emails": [{"primary": True, "value": "jdoe@acme.test", "type": "work"}],
    "displayName": "John Doe",
    "locale": "en-US",
    "externalId": "00u1abcd",
    "groups": [],
    "password": "never-stored-1",
    "active": True,
}


class TestResources:
    def test_okta_create(self):
        s = protocol.user_from_resource(OKTA_CREATE)
        assert s.email == "jdoe@acme.test"
        assert s.full_name == "John Doe"
        assert s.external_id == "00u1abcd"
        assert s.active is True
        assert s.role == protocol.DEFAULT_ROLE == "viewer"
        assert "locale" in s.ignored
        assert "password" not in s.ignored and not hasattr(s, "password")

    def test_create_with_roles(self):
        s = protocol.user_from_resource({**OKTA_CREATE, "roles": [{"value": "analyst"}]})
        assert s.role == "analyst"

    def test_put_without_roles_or_active_keeps_them(self):
        current = _state(role="analyst", active=False)
        body = {k: v for k, v in OKTA_CREATE.items() if k != "active"}
        s = protocol.user_from_resource(body, current)
        assert s.role == "analyst" and s.active is False

    def test_put_replaces_names(self):
        s = protocol.user_from_resource({"userName": "ana@acme.test"}, _state())
        assert s.full_name is None and s.given_name is None and s.external_id is None

    def test_username_required(self):
        with pytest.raises(ScimError) as exc:
            protocol.user_from_resource({"name": {"givenName": "x"}})
        assert exc.value.code == "scim_username_required"

    def test_nul_byte_refused(self):
        with pytest.raises(ScimError):
            protocol.user_from_resource({"userName": "a@acme.test", "displayName": "a\x00b"})

    def test_rendering(self):
        row = {"id": "usr_1", "email": "a@acme.test", "full_name": "A B", "role": "analyst",
               "status": "suspended", "created_at": None, "updated_at": None,
               "external_id": "e1", "given_name": "A", "family_name": "B"}
        r = protocol.user_resource(row, "https://x/api/v1/scim/v2")
        assert r["active"] is False, "a suspended user must not read as active"
        assert r["userName"] == "a@acme.test"
        assert r["roles"][0]["value"] == "analyst"
        assert r["meta"]["location"] == "https://x/api/v1/scim/v2/Users/usr_1"
        assert r["meta"]["version"].startswith('W/"')
        assert "password" not in r and "hashed_password" not in r

    def test_version_changes_with_the_role(self):
        row = {"id": "u", "email": "a@acme.test", "role": "analyst", "status": "active"}
        assert protocol.user_version(row) != protocol.user_version({**row, "role": "admin"})


# ── Groups ───────────────────────────────────────────────────────────────────

class TestGroups:
    def test_group_names(self):
        assert protocol.group_id_for("StockAI Analyst") == "analyst"
        assert protocol.group_id_for("admin") == "admin"
        assert protocol.group_id_for("Finance") is None

    def test_okta_remove_one_member(self):
        c = protocol.apply_group_patch("analyst", _ops(
            {"op": "remove", "path": 'members[value eq "usr_1"]'}))
        assert c.remove == ["usr_1"]

    def test_okta_replace_members_and_same_name(self):
        c = protocol.apply_group_patch("analyst", _ops(
            {"op": "replace", "value": {"id": "analyst", "displayName": "StockAI Analyst"}},
            {"op": "replace", "path": "members", "value": [{"value": "usr_1"}, {"value": "usr_2"}]}))
        assert c.replace == ["usr_1", "usr_2"]

    def test_entra_add_and_remove(self):
        c = protocol.apply_group_patch("admin", _ops(
            {"op": "Add", "path": "members", "value": [{"value": "usr_1"}]},
            {"op": "Remove", "path": "members", "value": [{"value": "usr_2"}]}))
        assert c.add == ["usr_1"] and c.remove == ["usr_2"]

    def test_rename_is_refused(self):
        with pytest.raises(ScimError) as exc:
            protocol.apply_group_patch("analyst", _ops(
                {"op": "replace", "path": "displayName", "value": "Finance"}))
        assert exc.value.scim_type == "mutability"

    def test_bad_member(self):
        with pytest.raises(ScimError):
            protocol.apply_group_patch("analyst", _ops(
                {"op": "add", "path": "members", "value": [{"display": "no value"}]}))

    def test_role_changes_for_group(self):
        change = protocol.MembershipChange(add=["u3"], remove=["u1", "u9"])
        out = protocol.role_changes_for_group("analyst", change, {"u1", "u2"})
        # u9 was never a member: removing it changes nothing.
        assert out == {"u1": "viewer", "u3": "analyst"}

    def test_replace_demotes_those_left_out(self):
        change = protocol.MembershipChange(replace=["u2"])
        out = protocol.role_changes_for_group("admin", change, {"u1", "u2"})
        assert out == {"u1": "viewer", "u2": "admin"}

    def test_viewer_is_the_floor(self):
        change = protocol.MembershipChange(replace=[])
        assert protocol.role_changes_for_group("viewer", change, {"u1"}) == {}

    def test_put_group(self):
        ids = protocol.group_from_resource("viewer", {"displayName": "StockAI Viewer",
                                                      "members": [{"value": "u1"}]})
        assert ids == ["u1"]


# ── Error shape ──────────────────────────────────────────────────────────────

class TestErrorShape:
    def test_rfc_7644_error_body(self):
        err = ScimError(409, "taken", code="scim_user_exists", scim_type="uniqueness")
        body = err.body()
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert body["status"] == "409", "RFC 7644 3.12: status is a string"
        assert body["scimType"] == "uniqueness"
        assert body["detail"] == "taken"
        assert body["errorCode"] == "scim_user_exists"

    def test_no_scim_type_when_none_fits(self):
        body = ScimError(403, "limit", code="scim_user_limit_reached",
                         params={"max": 2}).body()
        assert "scimType" not in body
        assert body["errorParams"] == {"max": 2}


# ── Tokens ───────────────────────────────────────────────────────────────────

class TestTokens:
    def test_mint_parse_verify(self):
        token_id, raw, secret_hash = tokens.mint()
        parsed = tokens.parse(raw)
        assert parsed is not None and parsed[0] == token_id
        assert tokens.verify(parsed[1], secret_hash) is True

    def test_the_hash_is_not_the_secret(self):
        _, raw, secret_hash = tokens.mint()
        assert tokens.parse(raw)[1] not in secret_hash
        assert raw not in secret_hash

    def test_wrong_secret(self):
        _, raw, secret_hash = tokens.mint()
        _, other, _ = tokens.mint()
        assert tokens.verify(tokens.parse(other)[1], secret_hash) is False

    def test_unknown_token_never_verifies(self):
        assert tokens.verify("x" * 43, None) is False

    @pytest.mark.parametrize("raw", [
        None, "", "sk_live_abcdef", "eyJhbGciOiJIUzI1NiJ9.e30.x",
        "scim_ZZZZZZZZZZZZZZZZ_" + "a" * 43, "scim_0123456789abcdef_short",
        "scim_0123456789abcdef_" + "a" * 44,
    ])
    def test_not_a_scim_token(self, raw):
        assert tokens.parse(raw) is None

    def test_tokens_are_unique(self):
        assert len({tokens.mint()[1] for _ in range(50)}) == 50

    def test_hint_hides_the_secret(self):
        token_id, raw, _ = tokens.mint()
        assert tokens.hint(token_id) != raw
        assert tokens.parse(raw)[1] not in tokens.hint(token_id)

    def test_a_scim_token_is_not_an_api_key(self):
        from backend.auth.api_key_auth import looks_like_api_key
        _, raw, _ = tokens.mint()
        assert looks_like_api_key(raw) is False


def test_scim_is_an_internal_tag():
    from backend.api.public_surface import EXPOSED_TAGS, INTERNAL_TAGS
    assert "scim" in INTERNAL_TAGS and "scim" not in EXPOSED_TAGS
