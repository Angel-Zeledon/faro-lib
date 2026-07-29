from backend.db.connection import query_one, execute

_DEFAULTS = {"language": "es", "theme": "dark", "dm_sms_enabled": False}


def ensure_table() -> None:
    execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id        TEXT PRIMARY KEY,
            tenant_id      TEXT NOT NULL,
            language       TEXT NOT NULL DEFAULT 'es',
            theme          TEXT NOT NULL DEFAULT 'dark',
            dm_sms_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at     TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def get_preferences(tenant_id: str, user_id: str) -> dict:
    row = query_one(
        "SELECT language, theme, dm_sms_enabled FROM user_preferences "
        "WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    return dict(row) if row else {**_DEFAULTS}


def update_preferences(
    tenant_id: str,
    user_id: str,
    language: str | None = None,
    theme: str | None = None,
    dm_sms_enabled: bool | None = None,
) -> dict:
    current = get_preferences(tenant_id, user_id)
    new_lang = language if language is not None else current["language"]
    new_theme = theme if theme is not None else current["theme"]
    new_dm_sms = dm_sms_enabled if dm_sms_enabled is not None else current["dm_sms_enabled"]
    execute(
        """INSERT INTO user_preferences (user_id, tenant_id, language, theme, dm_sms_enabled, updated_at)
           VALUES (%s, %s, %s, %s, %s, NOW())
           ON CONFLICT (user_id) DO UPDATE
           SET language = EXCLUDED.language, theme = EXCLUDED.theme,
               dm_sms_enabled = EXCLUDED.dm_sms_enabled, updated_at = NOW()""",
        (user_id, tenant_id, new_lang, new_theme, new_dm_sms),
    )
    return {"language": new_lang, "theme": new_theme, "dm_sms_enabled": new_dm_sms}
