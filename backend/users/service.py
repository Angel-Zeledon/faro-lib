from datetime import datetime, timedelta
from typing import Optional

from backend.auth.password import hash_password, verify_password
from backend.db.connection import query_one, query, execute
from backend.utils.ids import generate_id


# ── CRUD ───────────────────────────────────────────────────────────────────

def create_user(
    tenant_id: str,
    email: str,
    password: str,
    role: str = "analyst",
    full_name: Optional[str] = None,
    status: str = "active",
) -> dict:
    user_id = generate_id("usr")
    execute(
        """INSERT INTO users
           (id, tenant_id, email, full_name, role, hashed_password,
            email_verified, status, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, NOW(), NOW())""",
        (user_id, tenant_id, email.lower().strip(), full_name, role, hash_password(password), status),
    )
    return _public(get_user(tenant_id, user_id))


def create_user_admin(
    tenant_id: str,
    email: str,
    temp_password: str,
    role: str = "analyst",
    full_name: Optional[str] = None,
) -> dict:
    """Admin-created user starts as pending_confirmation."""
    return create_user(tenant_id, email, temp_password, role, full_name, status="pending_confirmation")


def get_user(tenant_id: str, user_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM users WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )


def get_user_by_email(tenant_id: str, email: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM users WHERE email = %s AND tenant_id = %s",
        (email.lower().strip(), tenant_id),
    )


def verify_credentials(tenant_id: str, email: str, password: str) -> Optional[dict]:
    u = get_user_by_email(tenant_id, email)
    if u and verify_password(password, u["hashed_password"]):
        return u
    return None


def mark_verified(tenant_id: str, user_id: str) -> None:
    execute(
        """UPDATE users SET email_verified = TRUE, updated_at = NOW(),
           status = CASE WHEN status = 'pending_confirmation' THEN 'active' ELSE status END
           WHERE id = %s AND tenant_id = %s""",
        (user_id, tenant_id),
    )


def update_password(tenant_id: str, user_id: str, new_password: str) -> None:
    execute(
        "UPDATE users SET hashed_password = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (hash_password(new_password), user_id, tenant_id),
    )
    execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


def update_profile(
    tenant_id: str,
    user_id: str,
    full_name: Optional[str] = None,
    whatsapp_number: Optional[str] = None,
) -> Optional[dict]:
    if full_name is not None:
        execute(
            "UPDATE users SET full_name = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
            (full_name.strip(), user_id, tenant_id),
        )
    if whatsapp_number is not None:
        # Empty string clears the number (opt out of WhatsApp alerts)
        execute(
            "UPDATE users SET whatsapp_number = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
            (whatsapp_number.strip() or None, user_id, tenant_id),
        )
    return _public(get_user(tenant_id, user_id))


def update_last_login(tenant_id: str, user_id: str) -> None:
    execute(
        "UPDATE users SET last_login_at = NOW() WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )


def list_users(tenant_id: str) -> list[dict]:
    rows = query(
        "SELECT * FROM users WHERE tenant_id = %s ORDER BY created_at",
        (tenant_id,),
    )
    return [_public(u) for u in rows]


def list_users_admin(
    tenant_id: str,
    search: Optional[str] = None,
    status: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    conditions = ["tenant_id = %s"]
    params: list = [tenant_id]

    if search:
        conditions.append("(email ILIKE %s OR full_name ILIKE %s)")
        like = f"%{search}%"
        params += [like, like]
    if status:
        conditions.append("status = %s")
        params.append(status)
    if role:
        conditions.append("role = %s")
        params.append(role)

    where = " AND ".join(conditions)
    total_row = query_one(f"SELECT COUNT(*) AS n FROM users WHERE {where}", tuple(params))
    total = total_row["n"] if total_row else 0

    rows = query(
        f"SELECT * FROM users WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset),
    )
    return {"items": [_public(u) for u in rows], "total": total}


def update_user_admin(
    tenant_id: str,
    user_id: str,
    full_name: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[dict]:
    sets = []
    params = []
    if full_name is not None:
        sets.append("full_name = %s")
        params.append(full_name.strip())
    if role is not None:
        sets.append("role = %s")
        params.append(role)
    email_changed = False
    if email is not None:
        new_email = email.lower().strip()
        sets.append("email = %s")
        sets.append("email_verified = FALSE")
        sets.append("status = 'pending_confirmation'")
        params.append(new_email)
        email_changed = True
    if not sets:
        return _public(get_user(tenant_id, user_id))

    sets.append("updated_at = NOW()")
    params += [user_id, tenant_id]
    execute(
        f"UPDATE users SET {', '.join(sets)} WHERE id = %s AND tenant_id = %s",
        tuple(params),
    )
    if email_changed:
        revoke_all_tokens(tenant_id, user_id)
    return _public(get_user(tenant_id, user_id))


def update_status(tenant_id: str, user_id: str, new_status: str) -> None:
    execute(
        "UPDATE users SET status = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (new_status, user_id, tenant_id),
    )
    if new_status in ("inactive", "suspended"):
        execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


def delete_user(tenant_id: str, user_id: str) -> None:
    execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))
    execute("DELETE FROM user_permissions WHERE user_id = %s", (user_id,))
    execute("DELETE FROM pw_change_codes WHERE user_id = %s", (user_id,))
    execute(
        "DELETE FROM users WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )


# ── Permissions ────────────────────────────────────────────────────────────

ALL_PERMISSIONS = [
    "view_forecasts", "run_training", "manage_sessions", "export_data",
    "view_inventory", "manage_inventory",
    "view_analysts", "run_analysts",
    "view_data_sources", "manage_data_sources",
    "view_users", "manage_users",
]


def get_permissions(tenant_id: str, user_id: str) -> list[str]:
    rows = query(
        "SELECT permission FROM user_permissions WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    return [r["permission"] for r in rows]


def set_permissions(tenant_id: str, user_id: str, permissions: list[str]) -> None:
    valid = set(ALL_PERMISSIONS)
    perms = [p for p in permissions if p in valid]
    execute("DELETE FROM user_permissions WHERE user_id = %s AND tenant_id = %s", (user_id, tenant_id))
    for perm in perms:
        execute(
            """INSERT INTO user_permissions (user_id, tenant_id, permission)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (user_id, tenant_id, perm),
        )


# ── Refresh tokens ─────────────────────────────────────────────────────────

def add_refresh_token(tenant_id: str, user_id: str, token_hash: str) -> None:
    execute(
        """DELETE FROM refresh_tokens WHERE user_id = %s AND id IN (
               SELECT id FROM refresh_tokens
               WHERE user_id = %s ORDER BY created_at DESC OFFSET 4
           )""",
        (user_id, user_id),
    )
    from backend.auth.jwt_handler import get_refresh_expire_days
    from datetime import timezone
    expires_at = datetime.now(timezone.utc) + timedelta(days=get_refresh_expire_days())
    execute(
        """INSERT INTO refresh_tokens (user_id, tenant_id, hash, expires_at, created_at)
           VALUES (%s, %s, %s, %s, NOW())""",
        (user_id, tenant_id, token_hash, expires_at),
    )


def validate_refresh_token(token_hash: str) -> Optional[dict]:
    return query_one(
        """SELECT u.* FROM refresh_tokens rt
           JOIN users u ON u.id = rt.user_id
           WHERE rt.hash = %s AND rt.expires_at > NOW()""",
        (token_hash,),
    )


def revoke_all_tokens(tenant_id: str, user_id: str) -> None:
    execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


# ── Private ────────────────────────────────────────────────────────────────

def _public(user: Optional[dict]) -> Optional[dict]:
    if not user:
        return user
    return {k: v for k, v in user.items() if k != "hashed_password"}
