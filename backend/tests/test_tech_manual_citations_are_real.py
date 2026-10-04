"""The technical manual promises a file and a line. This checks it kept it.

`docs/tech/` says, on its own cover: "written against the code, with file and
line". That promise is the whole reason the document is worth more than a wiki
page — and it is the first thing to rot, silently, in a way no reader can
detect. It already had: five citations into `inventory/service.py` drifted by
55 lines when a helper was inserted above them, and one pointed 18 lines past
the end of `auth/guards.py`. Both survived a full review of the prose, because
prose review does not open the file.

So the citations are checked mechanically:

- every `path:line` must resolve to a file that exists, at a line that exists;
- where the citation names a symbol after a `·`, that symbol must actually be
  on the line the manual sends the reader to.

The anchor is what makes drift *detectable* rather than merely *wrong*: a bare
line number is still a valid line number after an edit moves the code, and
nothing can tell. A failure here is never a code defect — it means the manual
now lies, and the fix is to re-point the citation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TECH = _REPO / "docs" / "tech"

# Citations are written from whichever root reads naturally in prose — some
# from the repo, some from inside `ForecastingCore/`. Both are resolved.
_ROOTS = (_REPO, _REPO / "ForecastingCore", _REPO / "backend")

_CITED_LINE = re.compile(r"^(?P<path>[\w./\\-]+\.py):(?P<line>\d+)(?:\s*·\s*(?P<anchor>.+))?$")


def _citations() -> list[tuple[str, str]]:
    """Every `where` value in the manual, paired with the chapter it came from.

    Parsed with `ast` rather than imported: these modules are pure data, and a
    test that imports them to read them would be running the document.
    """
    found: list[tuple[str, str]] = []
    for chapter in sorted(_TECH.glob("chapter_*.py")):
        tree = ast.parse(chapter.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "where"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.append((chapter.name, value.value))
    return found


def _line_citations() -> list[tuple[str, str]]:
    """Only the ones that name a line — a bare directory or module is a
    pointer, not a citation, and there is nothing to verify about it."""
    out = []
    for chapter, where in _citations():
        # "a.py:10 → b.py:20" cites two places; both are checked.
        for part in where.split("→"):
            part = part.strip()
            if _CITED_LINE.match(part):
                out.append((chapter, part))
    return out


def test_the_manual_actually_cites_something():
    """A guard on the guard: if the parse silently returns nothing, every
    assertion below passes and the file becomes decoration."""
    cites = _line_citations()
    assert len(cites) >= 20, f"only found {len(cites)} line citations — did the parse break?"


@pytest.mark.parametrize("chapter,citation", _line_citations(), ids=lambda v: str(v))
def test_citation_points_at_real_code(chapter: str, citation: str):
    m = _CITED_LINE.match(citation)
    assert m, citation
    rel, lineno, anchor = m["path"], int(m["line"]), m["anchor"]

    target = next((root / rel for root in _ROOTS if (root / rel).is_file()), None)
    assert target is not None, (
        f"{chapter} cites {rel}, which does not exist under any of "
        f"{[str(r.name) for r in _ROOTS]}"
    )

    lines = target.read_text(encoding="utf-8").splitlines()
    assert lineno <= len(lines), (
        f"{chapter} cites {citation}, but {rel} has only {len(lines)} lines"
    )

    if anchor:
        actual = lines[lineno - 1]
        assert anchor in actual, (
            f"{chapter} sends the reader to {citation}, but line {lineno} of "
            f"{rel} now reads:\n    {actual.strip()}\n"
            f"The code moved. Re-point the citation — do not delete the anchor."
        )


def test_every_line_citation_carries_an_anchor():
    """A line number alone cannot be verified, so new ones must not be added.

    The existing anchorless citations are listed by name: they are the ones
    whose target is a bare expression nobody would search for. The list is
    allowed to shrink and never to grow.
    """
    anchorless = {
        c for _, c in _line_citations() if not _CITED_LINE.match(c)["anchor"]
    }
    known = {
        "backend/api/public_surface.py:26",
        "backend/inventory/roi_service.py:516",
        "backend/sessions/state_machine.py:26",
        "backend/tests/test_no_pandas_in_backend.py:33",
        "backend/training/queue.py:50",
        "forecasting_core/data/canonical.py:49",
        "forecasting_core/data/loader.py:109",
        "forecasting_core/data/quality.py:80",
        "forecasting_core/data/resampler.py:19",
        "forecasting_core/engine.py:68",
        "forecasting_core/features/engineer.py:47",
        "forecasting_core/inference/predictor.py:29",
        "forecasting_core/training/router.py:30",
        "forecasting_core/validation/auto_correct.py:100",
    }
    added = anchorless - known
    assert not added, (
        "new citations without a ` · symbol` anchor: "
        f"{sorted(added)}. An anchor is what lets this test notice when the "
        "code moves; without one the citation rots invisibly."
    )
