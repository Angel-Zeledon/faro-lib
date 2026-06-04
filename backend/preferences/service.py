from backend.db.connection import query_one, execute

_DEFAULTS = {"language": "es", "theme": "dark"}


def ensure_table() -> None:
    execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id    TEXT PRIMARY KEY,
            tenant_id  TEXT NOT NULL,
            language   TEXT NOT NULL DEFAULT 'es',
            theme      TEXT NOT NULL DEFAULT 'dark',
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def get_preferences(tenant_id: str, user_id: str) -> dict:
    row = query_one(
        "SELECT language, theme FROM user_preferences WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    return dict(row) if row else {**_DEFAULTS}


def update_preferences(tenant_id: str, user_id: str, language: str | None = None, theme: str | None = None) -> dict:
    current  = get_preferences(tenant_id, user_id)
    new_lang  = language if language is not None else current["language"]
    new_theme = theme    if theme    is not None else current["theme"]
    execute(
        """INSERT INTO user_preferences (user_id, tenant_id, language, theme, updated_at)
           VALUES (%s, %s, %s, %s, NOW())
           ON CONFLICT (user_id) DO UPDATE
           SET language = EXCLUDED.language, theme = EXCLUDED.theme, updated_at = NOW()""",
        (user_id, tenant_id, new_lang, new_theme),
    )
    return {"language": new_lang, "theme": new_theme}
