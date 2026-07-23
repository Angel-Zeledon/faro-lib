"""Enforce the pandas boundary: pandas/numpy live only in dataframes/,
utils/temporal_agg.py, and workers/runner.py (pandas-boundary refactor)."""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent  # backend/
_ALLOWED = {
    ("dataframes",),          # any file under backend/dataframes/
    ("utils", "temporal_agg.py"),
    ("workers", "runner.py"),
}
_PATTERN = re.compile(r"^\s*(import|from)\s+(pandas|numpy)\b", re.MULTILINE)


# Non-application directories that live under backend/ but are not backend code
# (the local virtualenv and caches). They are third-party / generated, never the
# subject of the pandas-boundary rule.
_SKIP_DIRS = {".venv", "venv", "site-packages", "__pycache__", ".pytest_cache", ".mypy_cache"}


def _is_allowed(rel: pathlib.PurePath) -> bool:
    parts = rel.parts
    if any(p in _SKIP_DIRS for p in parts):
        return True
    if parts and parts[0] == "tests":
        return True
    if parts and parts[0] == "dataframes":
        return True
    return parts in _ALLOWED


def test_pandas_only_in_boundary_modules():
    offenders = []
    for py in _ROOT.rglob("*.py"):
        rel = py.relative_to(_ROOT)
        if _is_allowed(rel):
            continue
        if _PATTERN.search(py.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(rel))
    assert offenders == [], (
        "pandas/numpy imported outside the boundary: " + ", ".join(sorted(offenders)))
