"""The compliance pack cites code. This checks the citations are still true.

`docs/compliance/` is handed to customers' security teams, so a citation that
no longer points at the control it names is worse than no citation: it claims
a safeguard that may have been moved, renamed or deleted. Same approach as
`test_tech_manual_citations_are_real.py`, adapted to Markdown:

- a citation is an inline code span `path/file.py:LINE · symbol`;
- the file must exist, the line must exist, and the symbol must be on that line
  (the anchor is what makes drift detectable - a bare line number stays a valid
  line number after the code moves, and nothing can tell);
- a code span that names a repository file without a line must name a file
  that exists;
- fenced code blocks are examples, not claims, and are skipped.

A failure is never a code defect: it means the document now lies. Re-point the
citation (`{{path::symbol}}` placeholders resolve it) - do not delete the
anchor, and do not weaken the statement without checking the control exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DOCS = _REPO / "docs" / "compliance"

_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
_SPAN = re.compile(r"`([^`\n]+)`")
_CITATION = re.compile(r"^(?P<path>[\w./\\-]+\.[A-Za-z]+):(?P<line>\d+)\s*·\s*(?P<anchor>.+)$")
_BARE_LINE = re.compile(r"^[\w./\\-]+\.[A-Za-z]+:\d+$")
_ROOTS = ("backend/", "deploy/", "docs/", "scripts/", "Frontend/", "ForecastingCore/")
_PATH_SPAN = re.compile(r"^[\w./-]+\.(py|md|ts|tsx|sh|mjs|yml|json|txt)(::.*)?$")


def _doc_files() -> list[Path]:
    return sorted(_DOCS.glob("*.md"))


def _spans(path: Path) -> list[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    return _SPAN.findall(text)


def _citations() -> list[tuple[str, str]]:
    out = []
    for doc in _doc_files():
        for span in _spans(doc):
            if _CITATION.match(span):
                out.append((doc.name, span))
    return out


def test_the_pack_exists_and_actually_cites_something():
    """A guard on the guard: an empty parse would make every test below pass."""
    names = {p.name for p in _doc_files()}
    assert {
        "security-overview.md", "data-processing-and-subprocessors.md",
        "soc2-readiness-gap-analysis.md", "incident-response.md",
        "business-continuity.md", "vulnerability-disclosure.md",
        "questionnaire-answers.md",
    } <= names
    assert len(_citations()) >= 40, "the citation parse found almost nothing"


@pytest.mark.parametrize("doc,citation", _citations(), ids=lambda v: str(v))
def test_citation_points_at_real_code(doc: str, citation: str):
    m = _CITATION.match(citation)
    assert m, citation
    rel, lineno, anchor = m["path"], int(m["line"]), m["anchor"].strip()
    target = _REPO / rel
    assert target.is_file(), f"{doc} cites {rel}, which does not exist"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert 1 <= lineno <= len(lines), f"{doc} cites {citation} but {rel} has {len(lines)} lines"
    actual = lines[lineno - 1]
    assert anchor in actual, (
        f"{doc} sends the reader to {citation}, but line {lineno} of {rel} now reads:\n"
        f"    {actual.strip()}\nThe code moved. Re-point the citation."
    )


def test_no_citation_is_a_bare_line_number():
    """A line with no symbol cannot be checked, so none may be added."""
    bare = [(d.name, s) for d in _doc_files() for s in _spans(d) if _BARE_LINE.match(s)]
    assert not bare, f"citations without a ` · symbol` anchor: {bare}"


def test_every_repository_path_named_in_the_pack_exists():
    missing = []
    for doc in _doc_files():
        for span in _spans(doc):
            if _CITATION.match(span) or not _PATH_SPAN.match(span):
                continue
            if not span.startswith(_ROOTS):
                continue
            rel = span.split("::", 1)[0]
            if not (_REPO / rel).exists():
                missing.append((doc.name, rel))
    assert not missing, f"the pack names files that do not exist: {missing}"


def test_the_pack_never_uses_the_forbidden_word():
    """House style: the Spanish word for money slang is not used anywhere."""
    pattern = re.compile(r"\bplata\b", re.I)
    hits = [d.name for d in _doc_files() if pattern.search(d.read_text(encoding="utf-8"))]
    assert not hits, hits


def test_every_gap_the_pack_promises_to_mark_is_marked():
    """The pack's rule is 'never claim a control that is not implemented'. Every
    document that makes claims must also say what is missing."""
    for name in ("security-overview.md", "data-processing-and-subprocessors.md",
                 "incident-response.md", "business-continuity.md"):
        assert "BRECHA" in (_DOCS / name).read_text(encoding="utf-8"), name
    answers = (_DOCS / "questionnaire-answers.md").read_text(encoding="utf-8")
    assert "NO VERIFICADO" in answers
    # The sheet answers each question with one of the four allowed verdicts.
    rows = [r for r in answers.splitlines() if re.match(r"^\| \d+ \|", r)]
    assert len(rows) >= 60
    for row in rows:
        verdict = row.split("|")[3].strip()
        assert verdict in {"Sí", "No", "Parcial", "NO VERIFICADO"}, row
