"""
Owner rule: the product never says the LatAm slang word for money; it says "dinero".

This scans every source a user can end up reading (frontend, backend, engine,
docs, help center, manuals, README) for the WHOLE WORD, case- and accent-
insensitive, including its plural. Words that merely contain it ("plataforma")
are not matches by construction: the pattern is anchored on word boundaries, so
no allow-list is needed. No DB, no server.

The word itself is assembled from two halves below so this file does not trip its
own scan (the LLM prompts use the same trick, see `backend/ai/style_rules.py`).
"""

import pathlib
import re
import unicodedata

_ROOT = pathlib.Path(__file__).resolve().parents[2]

_WORD = "pla" + "ta"
_PATTERN = re.compile(r"\b" + _WORD + r"s?\b", re.IGNORECASE)

_SCANNED = ("Frontend/src", "backend", "ForecastingCore", "docs", "README.md")
_SUFFIXES = {".ts", ".tsx", ".js", ".json", ".py", ".md", ".html", ".txt", ".csv", ".css"}
_SKIPPED_PARTS = {"node_modules", ".next", ".venv", "__pycache__", "storage", ".git"}


def _fold(text: str) -> str:
    """Lowercase and strip accents so accented or upper-case spellings cannot slip by."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _files():
    for entry in _SCANNED:
        base = _ROOT / entry
        paths = [base] if base.is_file() else base.rglob("*")
        for path in paths:
            if (path.is_file() and path.suffix.lower() in _SUFFIXES
                    and not _SKIPPED_PARTS & set(path.parts)):
                yield path


def test_the_slang_word_for_money_appears_nowhere():
    offenders = []
    for path in _files():
        text = _fold(path.read_text(encoding="utf-8", errors="ignore"))
        for number, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                offenders.append(f"{path.relative_to(_ROOT)}:{number}")
    assert not offenders, "say 'dinero', never the slang word:\n" + "\n".join(offenders[:50])


def test_the_scan_really_sees_the_word_and_ignores_platform():
    # The guard cannot be vacuous: it must match the word in every inflection and
    # case, and must not match words that merely contain it.
    assert _PATTERN.search(_fold("la " + _WORD.upper() + " parada"))
    assert _PATTERN.search(_fold("tus " + _WORD + "s"))
    assert not _PATTERN.search(_fold("la " + _WORD + "forma"))
    assert not _PATTERN.search(_fold("un plátano"))
    assert _files() is not None and any(_files())
