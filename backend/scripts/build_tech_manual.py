"""Build the TECHNICAL manual — one PDF per language.

    backend/.venv/Scripts/python.exe -m backend.scripts.build_tech_manual

Two manuals exist and they answer to two different readers:

  * `build_manual.py` → the USER manual. Screen by screen, what each one is for,
    what to click, what the numbers mean. Its reader is a purchasing manager.
  * this one → the TECHNICAL manual. Its reader is the engineer who just bought
    the source and has to decide whether they can own it: what the pipeline
    does, which formula produces the recommended quantity, where the thresholds
    live, what the optimizer optimises, and what the code deliberately does NOT
    do.

Everything here is written against the code with `path:line` references, and
the rule for the content is the same rule the rest of this repo lives by: if a
number is assumed, say it is assumed. An engineer will check these claims
against the source within the hour, and a flattering description costs more
than no description.

Layout, fonts and the table of contents are borrowed from `build_manual.py` so
the two documents look like one family and only one of them owns that code.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

from backend.scripts.build_manual import (
    CONTENT_W, INK, RULE, Manual, esc, register_fonts, styles,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TECH_DIR = ROOT / "docs" / "tech"
OUT_DIR = ROOT / "Frontend" / "public"

# Order is the reading order for somebody who has never seen the repo: what the
# thing is, then how data gets in, then how a number is produced, then how it
# becomes a decision, then the platform it runs on.
CHAPTER_ORDER = ["overview", "ingestion", "forecasting", "inventory", "platform"]

COVER = {
    "es": {
        "title": "Faro — Manual técnico",
        "sub": "Cómo funciona por dentro: arquitectura, algoritmos, fórmulas y decisiones de diseño.",
        "meta": (
            "Escrito para quien va a mantener este código. Cada afirmación apunta al "
            "archivo y la línea donde vive, y lo que es un supuesto se dice que lo es."
        ),
        "toc": "Contenido",
    },
    "en": {
        "title": "Faro — Technical manual",
        "sub": "How it works inside: architecture, algorithms, formulas and design decisions.",
        "meta": (
            "Written for whoever will maintain this code. Every claim points at the "
            "file and line where it lives, and what is an assumption is called one."
        ),
        "toc": "Contents",
    },
}


def load_chapters() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(TECH_DIR.glob("chapter_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out[mod.CHAPTER["id"]] = mod.CHAPTER
    missing = set(CHAPTER_ORDER) - set(out)
    if missing:
        sys.exit(f"Missing technical chapters: {sorted(missing)}")
    return out


def _mono(st: dict, text: str, uni: bool) -> Paragraph:
    """A code or formula line. Courier so an expression reads as an expression."""
    return Paragraph(
        f'<font face="Courier">{esc(text, uni)}</font>', st["Code"]
    )


def _topic(topic: dict, st: dict, uni: bool, labels: dict) -> list:
    """One subject: what it is, how it works, the formula, and what it is not."""
    flow: list = [Paragraph(esc(topic["name"], uni), st["ScreenTitle"])]

    if topic.get("where"):
        flow.append(Paragraph(esc(topic["where"], uni), st["Route"]))

    flow.append(Paragraph(esc(topic["what"], uni), st["Body"]))

    if topic.get("how"):
        flow.append(Paragraph(labels["how"], st["Label"]))
        for step in topic["how"]:
            flow.append(Paragraph("• " + esc(step, uni), st["Item"]))

    for label, expr, note in topic.get("formulas", []):
        flow.append(Paragraph(esc(label, uni), st["FormulaLabel"]))
        flow.append(_mono(st, expr, uni))
        if note:
            flow.append(Paragraph(esc(note, uni), st["Gotcha"]))

    if topic.get("table"):
        rows = [[Paragraph(f"<b>{esc(k, uni)}</b>", st["CellKey"]),
                 Paragraph(esc(v, uni), st["Cell"])] for k, v in topic["table"]]
        t = Table(rows, colWidths=[CONTENT_W * 0.32, CONTENT_W * 0.68], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        flow.append(Spacer(1, 4))
        flow.append(t)

    if topic.get("caveats"):
        flow.append(Paragraph(labels["caveats"], st["Label"]))
        for c in topic["caveats"]:
            flow.append(Paragraph("— " + esc(c, uni), st["Gotcha"]))

    flow.append(Spacer(1, 12))
    return flow


def build(lang: str, chapters: dict[str, dict], font: str, bold: str, uni: bool) -> Path:
    st = styles(font, bold)
    # Two styles the user manual has no use for.
    st["Code"] = st["Body"].clone("Code")
    st["Code"].fontName = "Courier"
    st["Code"].fontSize = 8.6
    st["Code"].leading = 12.6
    st["Code"].leftIndent = 10
    st["Code"].spaceAfter = 6
    st["Code"].textColor = INK
    st["FormulaLabel"] = st["Label"].clone("FormulaLabel")

    labels = {
        "es": {"how": "CÓMO FUNCIONA", "caveats": "LO QUE NO SIGNIFICA"},
        "en": {"how": "HOW IT WORKS", "caveats": "WHAT IT DOES NOT MEAN"},
    }[lang]

    cover = COVER[lang]
    flow: list = [
        Spacer(1, 52 * mm),
        Paragraph(esc(cover["title"], uni), st["CoverTitle"]),
        Paragraph(esc(cover["sub"], uni), st["CoverSub"]),
        Spacer(1, 10 * mm),
        Paragraph(esc(cover["meta"], uni), st["CoverMeta"]),
        PageBreak(),
    ]

    for cid in CHAPTER_ORDER:
        chapter = chapters[cid][lang]
        flow.append(Paragraph(esc(chapter["title"], uni), st["Chapter"]))
        flow.append(Paragraph(esc(chapter["intro"], uni), st["Intro"]))
        for topic in chapter["topics"]:
            flow.extend(_topic(topic, st, uni, labels))
        flow.append(PageBreak())

    out = OUT_DIR / f"faro-tecnico-{lang}.pdf"
    doc = Manual(str(out), lang, font, bold, title=cover["title"], author="Faro")
    doc.multiBuild(flow)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    font, bold, uni = register_fonts()
    chapters = load_chapters()
    for lang in ("es", "en"):
        out = build(lang, chapters, font, bold, uni)
        log.info("%s  (%.1f MB)", out, out.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
