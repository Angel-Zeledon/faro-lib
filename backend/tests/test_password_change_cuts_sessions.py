"""A password change must end the sessions that were already open.

Named after the failure. Walking /forgot-password on 2026-08-10, the endpoint
answered "Password updated. All sessions have been revoked." and it was not
true: `update_password` deleted refresh tokens only, so the access token issued
BEFORE the reset kept answering 200 — writes included — for the rest of its 15
minutes. Nothing errored. The one situation that makes somebody reset a password
("I think someone got in") is precisely the one where those minutes matter.

/logout could always revoke its own token because it is authenticated and holds
the `jti`. The reset flow never sees the intruder's token, so the cut has to be
expressed per user and per time: `users.sessions_invalid_before` versus the
token's `iat`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query_one


def _access_token_for(user: dict) -> str:
    return create_access_token(
        user_id=user["id"], tenant_id=user["tenant_id"], role=user.get("role", "admin"),
    )


class TestAPasswordChangeEndsOpenSessions:
    def test_a_token_issued_before_the_change_stops_working(
        self, client, registered_user
    ):
        from backend.users import service as user_svc

        user = registered_user["user"]
        token = _access_token_for(user)
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/me/preferences", headers=headers).status_code == 200, (
            "fixture broken: the token must work before the password changes"
        )

        user_svc.update_password(user["tenant_id"], user["id"], "AnotherPass123!")

        r = client.get("/api/v1/me/preferences", headers=headers)
        assert r.status_code == 401, (
            f"the pre-change token still answered {r.status_code}; an intruder "
            f"keeps full access for the rest of its lifetime"
        )

    def test_a_token_issued_after_the_change_works(self, client, registered_user):
        """The other half, and the one that would lock a user out of the account
        they just recovered if the comparison were off by a second."""
        from backend.users import service as user_svc

        user = registered_user["user"]
        user_svc.update_password(user["tenant_id"], user["id"], "AnotherPass123!")

        fresh = _access_token_for(user)
        r = client.get("/api/v1/me/preferences",
                       headers={"Authorization": f"Bearer {fresh}"})
        assert r.status_code == 200, (
            f"a token minted after the change was refused ({r.status_code}) — "
            f"this is the lockout the flooring exists to prevent"
        )

    def test_an_account_that_never_changed_its_password_is_untouched(
        self, client, registered_user
    ):
        """NULL means 'never cut anything'. Tokens minted before `iat` existed
        must keep working for those accounts, so the check may not run at all."""
        user = registered_user["user"]
        assert query_one(
            "SELECT sessions_invalid_before FROM users WHERE id = %s", (user["id"],),
        )["sessions_invalid_before"] is None

        token = _access_token_for(user)
        assert client.get("/api/v1/me/preferences",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200

    def test_a_token_with_no_iat_is_refused_once_the_account_has_cut(
        self, client, registered_user
    ):
        """Tokens minted before this feature carry no `iat`. For an account that
        HAS cut its sessions, such a token cannot prove it is new, so it must be
        refused rather than trusted."""
        import jwt as pyjwt
        from backend.config import settings
        from backend.users import service as user_svc

        user = registered_user["user"]
        legacy = pyjwt.encode(
            {
                "sub": user["id"], "tenant_id": user["tenant_id"],
                "role": user.get("role", "admin"), "email_verified": True,
                "jti": "legacy-no-iat", "type": "access",
                "exp": (datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp(),
            },
            settings.secret_key, algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {legacy}"}
        assert client.get("/api/v1/me/preferences", headers=headers).status_code == 200

        user_svc.update_password(user["tenant_id"], user["id"], "AnotherPass123!")
        assert client.get("/api/v1/me/preferences", headers=headers).status_code == 401


class TestAResetLinkWorksExactlyOnce:
    """Walked 2026-08-10: replaying the same reset token after a completed reset
    returned 200 and changed the password again. The OTP that buys the token is
    burned; the token was not. It travels in the URL of /reset-password, so it
    outlives the reset in browser history."""

    def _reset_token(self, user: dict) -> str:
        from backend.auth.jwt_handler import create_signed_token
        return create_signed_token({
            "sub": user["id"], "tenant_id": user["tenant_id"],
            "purpose": "password_reset",
        }, expires_minutes=15)

    def test_the_same_link_cannot_change_the_password_twice(
        self, client, registered_user
    ):
        user = registered_user["user"]
        token = self._reset_token(user)

        first = client.post("/api/v1/auth/reset-password",
                            json={"token": token, "new_password": "OwnerPass123!"})
        assert first.status_code == 200, first.text

        second = client.post("/api/v1/auth/reset-password",
                             json={"token": token, "new_password": "Intruder123!"})
        assert second.status_code == 400, (
            f"the same reset link was accepted twice ({second.status_code}); "
            f"whoever finds it in the URL history owns the account"
        )

        # The owner's password, not the replayer's, is the one that stands.
        assert client.post("/api/v1/auth/login", json={
            "email": user["email"], "password": "OwnerPass123!",
        }).status_code == 200
        assert client.post("/api/v1/auth/login", json={
            "email": user["email"], "password": "Intruder123!",
        }).status_code == 401

    def test_a_weak_password_does_not_burn_the_link(self, client, registered_user):
        """A first typo must not cost the user their only way back in."""
        user = registered_user["user"]
        token = self._reset_token(user)

        weak = client.post("/api/v1/auth/reset-password",
                           json={"token": token, "new_password": "abc"})
        assert weak.status_code >= 400

        ok_try = client.post("/api/v1/auth/reset-password",
                             json={"token": token, "new_password": "OwnerPass123!"})
        assert ok_try.status_code == 200, (
            f"the link was spent by a rejected password ({ok_try.status_code})"
        )


class TestTheEndpointStillTellsTheTruth:
    def test_reset_password_reports_the_sign_out_it_now_performs(
        self, client, registered_user
    ):
        """The message was the original defect. It may only claim the sign-out
        while the sign-out actually happens."""
        from backend.auth.jwt_handler import create_signed_token

        user = registered_user["user"]
        token = create_signed_token({
            "sub": user["id"], "tenant_id": user["tenant_id"],
            "purpose": "password_reset",
        }, expires_minutes=15)

        r = client.post("/api/v1/auth/reset-password",
                        json={"token": token, "new_password": "AnotherPass123!"})
        assert r.status_code == 200, r.text
        assert query_one(
            "SELECT sessions_invalid_before FROM users WHERE id = %s", (user["id"],),
        )["sessions_invalid_before"] is not None, (
            "the response claims every session was signed out; the cut must exist"
        )
