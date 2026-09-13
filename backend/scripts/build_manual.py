"""Build the Faro user manual as a PDF, one per language.

    backend/.venv/Scripts/python.exe -m backend.scripts.build_manual

Writes `Frontend/public/faro-manual-es.pdf` and `faro-manual-en.pdf`, which the
landing page offers for download in whichever language the visitor is reading.

The CONTENT is not here. It lives in `docs/manual/section_*.py` — plain data,
one module per chapter, each holding a Spanish and an English half with the same
shape. This file only lays it out. Keeping the two apart means the manual can be
corrected by someone who does not want to think about reportlab, and that a
layout change cannot quietly reword the product.

Every screenshot is the same file the landing tour uses (`Frontend/public/
shot-*.png`, `-en.png` for English), so the manual and the site can never drift
into showing different products.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import logging
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

log = logging.getLogger("build_manual")

ROOT = Path(__file__).resolve().parents[2]
MANUAL_DIR = ROOT / "docs" / "manual"
SHOTS_DIR = ROOT / "Frontend" / "public"
OUT_DIR = ROOT / "Frontend" / "public"

# Brand — the same Petróleo the product uses.
INK = colors.HexColor("#0C3A40")
ACCENT = colors.HexColor("#0E7C7B")
TEXT = colors.HexColor("#1B2325")
MUTED = colors.HexColor("#5C6B6E")
RULE = colors.HexColor("#DCE4E5")
TINT = colors.HexColor("#F2F7F7")
WARN_BG = colors.HexColor("#FFF7EC")
WARN_RULE = colors.HexColor("#E8A33D")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# The chapter order of the manual, which is NOT the order the files happen to
# sit in: a reader starts by getting the product running, then works the daily
# loop, then feeds it data, then analyses, and only then touches the settings.
# `section_sistema` carries both ends of that arc, so it is split in two.
CHAPTER_ORDER = ["sistema:first", "operacion", "datos", "analisis", "sistema:rest"]

STRINGS = {
    "es": {
        "manual": "Manual de usuario",
        "subtitle": "Todo lo que hace Faro, pantalla por pantalla.",
        "toc": "Contenido",
        "screen": "Pantalla",
        "purpose": "Para qué sirve",
        "walkthrough": "Recorrido de la pantalla",
        "fields": "Qué significa cada dato",
        "tasks": "Cómo hacer",
        "gotchas": "Ten en cuenta",
        "field": "Campo",
        "meaning": "Qué es",
        "system_title": "Tu cuenta y el sistema",
        "system_intro": (
            "Lo que configuras una vez y casi no vuelves a tocar: quién entra, "
            "con qué permisos, en qué moneda ves las cifras y qué corre solo."
        ),
        "how_to_read": "Cómo leer este manual",
        "how_to_read_body": (
            "Cada pantalla ocupa su propia sección y siempre en el mismo orden: "
            "una captura real de la aplicación, para qué sirve, un recorrido de "
            "arriba hacia abajo, qué significa cada dato, cómo hacer las cosas "
            "concretas y, al final, lo que suele confundir. Puedes leerlo "
            "seguido o abrirlo solo en la pantalla que tienes enfrente."
        ),
        "signals_title": "Las cuatro señales",
        "signals_body": (
            "Toda la aplicación gira alrededor de una sola cuenta: cuántos días "
            "aguantas con lo que tienes, comparado con lo que tarda tu proveedor "
            "en entregarte. De ahí salen cuatro señales, y las verás en casi "
            "todas las pantallas."
        ),
        "signals": [
            ("PEDIR_YA", "Te quedas sin producto antes de que llegue el próximo pedido."),
            ("PEDIR_PRONTO", "Todavía no es urgente, pero si esperas a la semana que viene ya lo será."),
            ("OK", "Tienes cobertura suficiente. No hay nada que decidir hoy."),
            ("SOBRESTOCK", "Tienes mucho más de lo que vas a vender: es dinero detenido."),
        ],
        "page": "Página",
        "generated": "Generado el",
    },
    "en": {
        "manual": "User manual",
        "subtitle": "Everything Faro does, screen by screen.",
        "toc": "Contents",
        "screen": "Screen",
        "purpose": "What it is for",
        "walkthrough": "Walking the screen",
        "fields": "What each figure means",
        "tasks": "How to",
        "gotchas": "Worth knowing",
        "field": "Field",
        "meaning": "What it is",
        "system_title": "Your account and the system",
        "system_intro": (
            "What you set up once and rarely touch again: who gets in, with "
            "which permissions, the currency your figures are shown in, and "
            "what runs on its own."
        ),
        "how_to_read": "How to read this manual",
        "how_to_read_body": (
            "Every screen gets its own section, always in the same order: a real "
            "capture of the application, what it is for, a walk down the screen, "
            "what each figure means, how to do the concrete things, and finally "
            "what tends to confuse people. Read it through, or open it only at "
            "the screen in front of you."
        ),
        "signals_title": "The four signals",
        "signals_body": (
            "The whole application turns on a single piece of arithmetic: how "
            "many days you last with what you hold, against how long your "
            "supplier takes to deliver. Four signals come out of it, and you "
            "will see them on almost every screen."
        ),
        "signals": [
            ("PEDIR_YA", "You run out before the next order arrives."),
            ("PEDIR_PRONTO", "Not urgent yet, but it will be if you wait until next week."),
            ("OK", "You have enough coverage. Nothing to decide today."),
            ("SOBRESTOCK", "You hold far more than you will sell: that is money standing still."),
        ],
        "page": "Page",
        "generated": "Generated on",
    },
}

# Not in WinAnsi. Only used if DejaVu is unavailable and we fall back to Arial.
FALLBACK_SUBS = {"\u2192": "->", "\u2212": "-", "\u2265": ">=", "\u25b6": ">", "\u2605": "*"}


# ── Fonts ─────────────────────────────────────────────────────────────────────
def register_fonts() -> tuple[str, str, bool]:
    """Return (regular, bold, full_unicode).

    DejaVu covers the arrows, ≥ and ★ the content uses; Arial does not, and a
    missing glyph in reportlab is a silent black box, not an error. So when we
    fall back the caller substitutes those characters instead of shipping boxes.
    """
    try:
        import matplotlib

        ttf = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("Manual", ttf / "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("Manual-Bold", ttf / "DejaVuSans-Bold.ttf"))
        return "Manual", "Manual-Bold", True
    except Exception as exc:  # noqa: BLE001 - any failure means "use the fallback"
        log.warning("DejaVu unavailable (%s); falling back to Arial", exc)

    win = Path("C:/Windows/Fonts")
    if (win / "arial.ttf").exists():
        pdfmetrics.registerFont(TTFont("Manual", win / "arial.ttf"))
        pdfmetrics.registerFont(TTFont("Manual-Bold", win / "arialbd.ttf"))
        return "Manual", "Manual-Bold", False

    return "Helvetica", "Helvetica-Bold", False


# ── Content loading ───────────────────────────────────────────────────────────
def load_sections() -> dict[str, dict]:
    sections: dict[str, dict] = {}
    for path in sorted(MANUAL_DIR.glob("section_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sections[mod.SECTION["id"]] = mod.SECTION
    missing = {c.split(":")[0] for c in CHAPTER_ORDER} - set(sections)
    if missing:
        sys.exit(f"Missing manual sections: {sorted(missing)}")
    return sections


def chapters_for(sections: dict[str, dict], lang: str) -> list[dict]:
    """The ordered chapters, with `sistema` split around the rest."""
    S = STRINGS[lang]
    out = []
    for key in CHAPTER_ORDER:
        sid, _, part = key.partition(":")
        half = sections[sid][lang]
        if part == "first":
            out.append({"title": half["title"], "intro": half["intro"],
                        "screens": half["screens"][:1]})
        elif part == "rest":
            rest = half["screens"][1:]
            if rest:
                out.append({"title": S["system_title"], "intro": S["system_intro"],
                            "screens": rest})
        else:
            out.append({"title": half["title"], "intro": half["intro"],
                        "screens": half["screens"]})
    return out


# ── Document ──────────────────────────────────────────────────────────────────
class Manual(BaseDocTemplate):
    """Two page templates: a bare cover, and the body with a running footer."""

    def __init__(self, path: str, lang: str, font: str, bold: str, **kw):
        super().__init__(path, pagesize=A4,
                         leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN, bottomMargin=MARGIN, **kw)
        self.lang = lang
        self.font = font
        self.bold = bold
        self.front_label = STRINGS[lang]["manual"]
        self.chapter = self.front_label
        frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame]),
            # onPageEnd, not onPage: the running chapter name is set by
            # `afterFlowable`, which has not seen this page's heading yet
            # when onPage fires — the footer named the previous chapter on
            # every page a new one started.
            PageTemplate(id="body", frames=[frame], onPageEnd=self._footer),
        ])

    def beforeDocument(self):
        # multiBuild lays the story out twice; without this the second
        # pass starts with the last chapter of the first still set.
        self.chapter = self.front_label

    def afterFlowable(self, flowable):
        """Remember the current chapter for the footer, and feed the outline."""
        style = getattr(flowable, "style", None)
        name = getattr(style, "name", "")
        if name == "Chapter":
            self.chapter = flowable.getPlainText()
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))
        elif name == "ScreenTitle":
            self.notify("TOCEntry", (1, flowable.getPlainText(), self.page))

    def _footer(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, MARGIN - 5 * mm, PAGE_W - MARGIN, MARGIN - 5 * mm)
        canvas.setFont(self.font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, MARGIN - 9.5 * mm, f"Faro · {self.chapter}")
        canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 9.5 * mm, str(doc.page))
        canvas.restoreState()


def styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    base = dict(fontName=font, textColor=TEXT, alignment=TA_LEFT)
    return {
        "CoverTitle": ParagraphStyle("CoverTitle", fontName=bold, fontSize=34,
                                     leading=38, textColor=INK, spaceAfter=8),
        "CoverSub": ParagraphStyle("CoverSub", fontName=font, fontSize=13,
                                   leading=19, textColor=MUTED),
        "CoverMeta": ParagraphStyle("CoverMeta", fontName=font, fontSize=8.5,
                                    leading=13, textColor=MUTED),
        "Chapter": ParagraphStyle("Chapter", fontName=bold, fontSize=21,
                                  leading=25, textColor=INK, spaceAfter=6),
        # Same look, different name — front matter must stay out of the
        # table of contents, and `afterFlowable` recognises headings by name.
        "FrontH1": ParagraphStyle("FrontH1", fontName=bold, fontSize=21,
                                  leading=25, textColor=INK, spaceAfter=6),
        "FrontH2": ParagraphStyle("FrontH2", fontName=bold, fontSize=15,
                                  leading=19, textColor=INK, spaceBefore=4,
                                  spaceAfter=2),
        "Intro": ParagraphStyle("Intro", fontName=font, fontSize=10.5,
                                leading=16.5, textColor=MUTED,
                                spaceAfter=14, alignment=TA_LEFT),
        "ScreenTitle": ParagraphStyle("ScreenTitle", fontName=bold, fontSize=15,
                                      leading=19, textColor=INK, spaceBefore=4,
                                      spaceAfter=2),
        "Route": ParagraphStyle("Route", fontName=font, fontSize=8.5, leading=12,
                                textColor=ACCENT, spaceAfter=9),
        "Label": ParagraphStyle("Label", fontName=bold, fontSize=8, leading=11,
                                textColor=ACCENT, spaceBefore=11, spaceAfter=4),
        "Body": ParagraphStyle("Body", fontSize=9.5, leading=15,
                               spaceAfter=4, **base),
        "Item": ParagraphStyle("Item", fontSize=9.5, leading=14.5,
                               leftIndent=13, spaceAfter=3.5, **base),
        "Cell": ParagraphStyle("Cell", fontSize=8.6, leading=12.6, **base),
        "CellKey": ParagraphStyle("CellKey", fontName=bold, fontSize=8.6,
                                  leading=12.6, textColor=INK),
        "TaskName": ParagraphStyle("TaskName", fontName=bold, fontSize=9.5,
                                   leading=14, textColor=INK, spaceBefore=6,
                                   spaceAfter=2),
        "Gotcha": ParagraphStyle("Gotcha", fontSize=8.8, leading=13.5,
                                 leftIndent=11, spaceAfter=4, **base),
        "TocL0": ParagraphStyle("TocL0", fontName=bold, fontSize=10.5, leading=19,
                                textColor=INK, spaceBefore=9),
        "TocL1": ParagraphStyle("TocL1", fontName=font, fontSize=9.5, leading=15,
                                textColor=TEXT, leftIndent=13),
    }


def esc(text: str, full_unicode: bool) -> str:
    """Paragraph markup is XML, so the content's own <, > and & must be escaped."""
    if not full_unicode:
        for bad, good in FALLBACK_SUBS.items():
            text = text.replace(bad, good)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def screenshot(image_key: str, lang: str):
    """The tour capture for this screen, scaled to the text column."""
    if not image_key:
        return None
    suffix = "-en" if lang == "en" else ""
    path = SHOTS_DIR / f"shot-{image_key}{suffix}.png"
    if not path.exists():
        log.warning("missing screenshot: %s", path.name)
        return None
    iw, ih = ImageReader(str(path)).getSize()
    return Image(str(path), width=CONTENT_W, height=CONTENT_W * ih / iw)


def build(lang: str, sections: dict[str, dict], font: str, bold: str, uni: bool) -> Path:
    S = STRINGS[lang]
    st = styles(font, bold)
    out = OUT_DIR / f"faro-manual-{lang}.pdf"
    doc = Manual(str(out), lang, font, bold,
                 title=f"Faro — {S['manual']}", author="Faro")

    def E(text: str) -> str:
        return esc(text, uni)

    story: list = []

    # ── Cover ────────────────────────────────────────────────────────────────
    story += [
        Spacer(1, 58 * mm),
        Paragraph("Faro", st["CoverTitle"]),
        Paragraph(E(S["manual"]), ParagraphStyle(
            "CoverKicker", parent=st["CoverTitle"], fontSize=21, leading=25,
            textColor=ACCENT, spaceAfter=16)),
        Paragraph(E(S["subtitle"]), st["CoverSub"]),
        Spacer(1, 10 * mm),
        Table([[""]], colWidths=[46 * mm], rowHeights=[2.2],
              style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)])),
        Spacer(1, 10 * mm),
        Paragraph(
            f"{E(S['generated'])} {_dt.date.today().isoformat()}",
            st["CoverMeta"]),
        NextPageTemplate("body"),
        PageBreak(),
    ]

    # ── How to read it, and the signals every screen leans on ────────────────
    story += [
        Paragraph(E(S["how_to_read"]), st["FrontH1"]),
        Paragraph(E(S["how_to_read_body"]), st["Body"]),
        Spacer(1, 6),
        Paragraph(E(S["signals_title"]), st["FrontH2"]),
        Paragraph(E(S["signals_body"]), st["Body"]),
        Spacer(1, 5),
        Table(
            [[Paragraph(E(sig), st["CellKey"]), Paragraph(E(desc), st["Cell"])]
             for sig, desc in S["signals"]],
            colWidths=[38 * mm, CONTENT_W - 38 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), TINT),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]),
        ),
        PageBreak(),
    ]

    # ── Table of contents ────────────────────────────────────────────────────
    from reportlab.platypus.tableofcontents import TableOfContents

    toc = TableOfContents()
    toc.levelStyles = [st["TocL0"], st["TocL1"]]
    story += [Paragraph(E(S["toc"]), st["FrontH1"]), Spacer(1, 4), toc, PageBreak()]

    # ── Chapters ─────────────────────────────────────────────────────────────
    for chapter in chapters_for(sections, lang):
        story += [
            Paragraph(E(chapter["title"]), st["Chapter"]),
            Paragraph(E(chapter["intro"]), st["Intro"]),
        ]

        for n, screen in enumerate(chapter["screens"]):
            if n:
                story.append(PageBreak())

            story.append(Paragraph(E(screen["name"]), st["ScreenTitle"]))
            if screen.get("route"):
                story.append(Paragraph(E(screen["route"]), st["Route"]))

            shot = screenshot(screen.get("image", ""), lang)
            if shot is not None:
                story += [shot, Spacer(1, 11)]

            story += [
                Paragraph(E(S["purpose"]).upper(), st["Label"]),
                Paragraph(E(screen["purpose"]), st["Body"]),
                Paragraph(E(S["walkthrough"]).upper(), st["Label"]),
            ]
            for i, step in enumerate(screen["walkthrough"], 1):
                story.append(Paragraph(f"<b>{i}.</b>  {E(step)}", st["Item"]))

            if screen.get("fields"):
                story += [
                    Paragraph(E(S["fields"]).upper(), st["Label"]),
                    Table(
                        [[Paragraph(E(S["field"]), st["CellKey"]),
                          Paragraph(E(S["meaning"]), st["CellKey"])]] +
                        [[Paragraph(E(k), st["CellKey"]), Paragraph(E(v), st["Cell"])]
                         for k, v in screen["fields"]],
                        colWidths=[52 * mm, CONTENT_W - 52 * mm],
                        repeatRows=1,
                        style=TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, 0), TINT),
                            ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]),
                    ),
                ]

            if screen.get("tasks"):
                story.append(Paragraph(E(S["tasks"]).upper(), st["Label"]))
                for name, steps in screen["tasks"]:
                    story.append(KeepTogether([
                        Paragraph(E(name), st["TaskName"]),
                        Paragraph(E(steps.strip()), st["Item"]),
                    ]))

            if screen.get("gotchas"):
                rows = [[Paragraph(E(S["gotchas"]).upper(), ParagraphStyle(
                    "GotchaHead", fontName=bold, fontSize=8, leading=11,
                    textColor=WARN_RULE))]]
                rows += [[Paragraph(f"·  {E(g)}", st["Gotcha"])]
                         for g in screen["gotchas"]]
                story += [
                    Spacer(1, 9),
                    Table(rows, colWidths=[CONTENT_W], style=TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), WARN_BG),
                        ("LINEBEFORE", (0, 0), (0, -1), 2, WARN_RULE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 11),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                        ("TOPPADDING", (0, 0), (-1, 0), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
                    ])),
                ]

        story.append(PageBreak())

    # multiBuild, twice over: the table of contents needs the page numbers that
    # only exist once the pages have been laid out.
    doc.multiBuild(story)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    font, bold, uni = register_fonts()
    if not uni:
        log.warning("running without full Unicode: arrows and ≥ are substituted")
    sections = load_sections()
    for lang in ("es", "en"):
        out = build(lang, sections, font, bold, uni)
        log.info("%s  (%.1f MB)", out, out.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
