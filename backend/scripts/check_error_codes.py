"""Fail when a backend error code has no Spanish or English translation.

Every code the backend can put on the wire (`AppError("code", ...)`, plus the
legacy-detail bridge in `backend/error_codes.py`) must exist as
`errors.<code>` in BOTH the `es` and `en` blocks of
`Frontend/src/i18n/translations.ts`. Otherwise the user reads a generic
fallback, or worse, the raw English sentence.

Usage: python -m backend.scripts.check_error_codes
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRANSLATIONS = ROOT / "Frontend" / "src" / "i18n" / "translations.ts"

_APP_ERROR = re.compile(r"""AppError\(\s*(?:code\s*=\s*)?(["'])([a-z][a-z0-9_]*)\1""")


# Handlers in main.py build the envelope by hand: `"error_code": "server_busy"`.
_ENVELOPE = re.compile(r"""["']error_code["']\s*:\s*(["'])([a-z][a-z0-9_]*)\1""")

# Deliberately NOT in the catalogue: a 422 `validation_error` carries per-field
# failures rendered from `errors.validation.<type>` / `errors.field.*`; a
# code-level sentence would hide them behind one vague line.
FIELD_LEVEL_CODES = {"validation_error"}


def backend_codes() -> dict[str, str]:
    """Map every literal code in the backend to one file that raises it."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "backend").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/.venv/" in rel or rel.endswith("scripts/check_error_codes.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for m in list(_APP_ERROR.finditer(text)) + list(_ENVELOPE.finditer(text)):
            if m.group(2) not in FIELD_LEVEL_CODES:
                found.setdefault(m.group(2), rel)
    from backend.error_codes import all_bridge_codes
    for code in all_bridge_codes():
        found.setdefault(code, "backend/error_codes.py")
    return found


def catalogue_blocks() -> tuple[str, str]:
    text = TRANSLATIONS.read_text(encoding="utf-8")
    split = text.index("\n  en: {")
    return text[:split], text[split:]


def missing_translations() -> list[tuple[str, str, str]]:
    """(code, language, raised-in) for each absent `errors.<code>` entry."""
    es, en = catalogue_blocks()
    out = []
    for code, where in sorted(backend_codes().items()):
        key = f"'errors.{code}'"
        if key not in es:
            out.append((code, "es", where))
        if key not in en:
            out.append((code, "en", where))
    return out


def main() -> int:
    missing = missing_translations()
    for code, lang, where in missing:
        print(f"MISSING errors.{code} [{lang}]  (raised in {where})")
    print(f"{len(backend_codes())} codes checked, {len(missing)} gaps")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
