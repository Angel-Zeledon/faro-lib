"""
Build the Faro forecasting-engine paper as a PDF.

Run:  python docs/paper/build_paper.py
Out:  docs/faro-forecasting-engine.pdf

The document is authored as structured blocks rather than a LaTeX source
because this repository has no TeX toolchain; equations are typeset with
matplotlib's mathtext and embedded as vector-quality raster at 400 dpi.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Real mathematical typography. The default mathtext fontset is DejaVu Sans,
# which renders equations in the same upright sans face as ordinary UI text —
# variables stop looking like variables. STIX is the family designed for
# scientific publishing; `_register_stix` below puts the PROSE in the same
# family, so an inline variable and a displayed one are the same letter.
matplotlib.rcParams["mathtext.fontset"] = "stix"
matplotlib.rcParams["font.family"] = "STIXGeneral"
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, ListFlowable, ListItem,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

OUT = Path(__file__).resolve().parents[1] / "faro-forecasting-engine.pdf"
EQ_DPI = 400


def _register_stix() -> tuple[str, str, str]:
    """
    Use STIX for the PROSE as well as the equations.

    Setting the mathtext fontset alone is only half the job: the body text was
    still Times, so a variable written inline in a sentence and the same
    variable inside a displayed equation came out in two different typefaces.
    In a document that is mostly about the relationship between the two, that
    reads as an error on every page. Registering the STIX text faces with
    reportlab puts the whole document in one family — which is exactly how
    scientific journals set STIX in the first place.

    Falls back to Times if the fonts are missing, rather than failing the build.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    ttf = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    faces = {
        "STIX": "STIXGeneral.ttf",
        "STIX-Bold": "STIXGeneralBol.ttf",
        "STIX-Italic": "STIXGeneralItalic.ttf",
        "STIX-BoldItalic": "STIXGeneralBolIta.ttf",
    }
    try:
        for name, filename in faces.items():
            path = ttf / filename
            if not path.exists():
                raise FileNotFoundError(path)
            pdfmetrics.registerFont(TTFont(name, str(path)))
        pdfmetrics.registerFontFamily(
            "STIX", normal="STIX", bold="STIX-Bold",
            italic="STIX-Italic", boldItalic="STIX-BoldItalic",
        )
        return "STIX", "STIX-Bold", "STIX-Italic"
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"STIX text faces unavailable ({exc}); falling back to Times")
        return "Times-Roman", "Times-Bold", "Times-Italic"


SERIF, SERIF_BOLD, SERIF_ITALIC = _register_stix()

INK = colors.HexColor("#101418")
MUTED = colors.HexColor("#5A6570")
RULE = colors.HexColor("#C9D1D9")
ACCENT = colors.HexColor("#0C3A40")      # the product's Petróleo badge colour

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

BODY = ParagraphStyle(
    "body", fontName=SERIF, fontSize=9.6, leading=13.4,
    alignment=TA_JUSTIFY, textColor=INK, spaceAfter=6,
)
TITLE = ParagraphStyle(
    "title", fontName=SERIF_BOLD, fontSize=19, leading=23,
    alignment=TA_CENTER, textColor=INK, spaceAfter=4,
)
SUBTITLE = ParagraphStyle(
    "subtitle", fontName=SERIF_ITALIC, fontSize=11, leading=15,
    alignment=TA_CENTER, textColor=MUTED, spaceAfter=14,
)
AUTHOR = ParagraphStyle(
    "author", fontName=SERIF, fontSize=9.5, leading=13,
    alignment=TA_CENTER, textColor=MUTED, spaceAfter=16,
)
H1 = ParagraphStyle(
    "h1", fontName=SERIF_BOLD, fontSize=12.4, leading=16,
    textColor=ACCENT, spaceBefore=15, spaceAfter=6,
)
H2 = ParagraphStyle(
    "h2", fontName=SERIF_BOLD, fontSize=10.4, leading=14,
    textColor=INK, spaceBefore=10, spaceAfter=4,
)
ABSTRACT_HEAD = ParagraphStyle(
    "abshead", fontName=SERIF_BOLD, fontSize=9.6, leading=13,
    alignment=TA_CENTER, textColor=INK, spaceAfter=4,
)
ABSTRACT = ParagraphStyle(
    "abstract", fontName=SERIF, fontSize=9.2, leading=12.8,
    alignment=TA_JUSTIFY, textColor=INK,
    leftIndent=22, rightIndent=22, spaceAfter=10,
)
CAPTION = ParagraphStyle(
    "caption", fontName=SERIF_ITALIC, fontSize=8.4, leading=11.4,
    alignment=TA_CENTER, textColor=MUTED, spaceBefore=3, spaceAfter=10,
)
CELL = ParagraphStyle(
    "cell", fontName=SERIF, fontSize=8.3, leading=11, textColor=INK,
)
CELL_H = ParagraphStyle(
    "cellh", fontName=SERIF_BOLD, fontSize=8.3, leading=11, textColor=colors.white,
)
CODE = ParagraphStyle(
    "code", fontName="Courier", fontSize=8.0, leading=11, textColor=MUTED,
    spaceAfter=6,
)

# ---------------------------------------------------------------------------
# Equation rendering
# ---------------------------------------------------------------------------

_eq_cache: dict[tuple[str, float], tuple[bytes, int, int]] = {}

# matplotlib's mathtext implements a subset of TeX: it has \left/\right but not
# the \big family, and no \! negative thin space. The source below is written in
# ordinary TeX and normalised here, so the equations stay readable as TeX.
_OPENERS = {"(", "[", r"\{", r"\lceil", r"\lfloor", r"\langle"}
_CLOSERS = {")", "]", r"\}", r"\rceil", r"\rfloor", r"\rangle"}
_BIG = re.compile(r"\\(?:bigg?|Bigg?)(\\\{|\\\}|\\[a-zA-Z]+|[()\[\]|])")


def _sanitize_math(latex: str) -> str:
    out = latex.replace(r"\!", "")
    out = out.replace(r"\tfrac", r"\frac").replace(r"\dfrac", r"\frac")

    def _repl(match: "re.Match[str]") -> str:
        delim = match.group(1)
        if delim == "|":
            return "|"                       # no sized variant needed
        if delim in _OPENERS:
            return r"\left" + delim
        if delim in _CLOSERS:
            return r"\right" + delim
        return delim
    return _BIG.sub(_repl, out)


def _render_math(latex: str, fontsize: float) -> tuple[bytes, int, int]:
    latex = _sanitize_math(latex)
    key = (latex, fontsize)
    if key in _eq_cache:
        return _eq_cache[key]
    fig = plt.figure(figsize=(0.02, 0.02))
    fig.text(0, 0, f"${latex}$", fontsize=fontsize, color="#101418")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=EQ_DPI, bbox_inches="tight",
                pad_inches=0.02, transparent=True)
    plt.close(fig)
    buf.seek(0)
    from PIL import Image as PILImage
    with PILImage.open(buf) as im:
        w, h = im.size
    data = buf.getvalue()
    _eq_cache[key] = (data, w, h)
    return data, w, h


def eq(latex: str, fontsize: float = 11.0, number: str | None = None):
    """A display equation, optionally numbered on the right margin."""
    data, w, h = _render_math(latex, fontsize)
    width_in = w / EQ_DPI
    height_in = h / EQ_DPI
    max_w = 14.0 * cm
    scale = min(1.0, max_w / (width_in * inch))
    img = Image(io.BytesIO(data), width=width_in * inch * scale,
                height=height_in * inch * scale)
    img.hAlign = "CENTER"
    if number is None:
        return [Spacer(1, 4), img, Spacer(1, 6)]
    tbl = Table(
        [[img, Paragraph(f"({number})", CELL)]],
        colWidths=[14.6 * cm, 1.4 * cm],
    )
    tbl.setStyle(TableStyle([
        # reportlab's table default is Helvetica; without this the PDF carries a
        # sans face it never means to show.
        ("FONTNAME", (0, 0), (-1, -1), SERIF),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [Spacer(1, 4), tbl, Spacer(1, 6)]


def inline(latex: str, fontsize: float = 9.0):
    """Small inline math, for use inside a table cell."""
    data, w, h = _render_math(latex, fontsize)
    return Image(io.BytesIO(data), width=w / EQ_DPI * inch, height=h / EQ_DPI * inch)


def p(text: str):
    return Paragraph(text, BODY)


def h1(text: str):
    return Paragraph(text, H1)


def h2(text: str):
    return Paragraph(text, H2)


def bullets(items: list[str]):
    return ListFlowable(
        [ListItem(Paragraph(t, BODY), leftIndent=14, value="•") for t in items],
        bulletType="bullet", start="•", leftIndent=14,
        bulletFontName=SERIF, bulletFontSize=8, spaceAfter=6,
    )


def table(rows: list[list], widths: list[float], caption: str | None = None):
    data = []
    for r_i, row in enumerate(rows):
        out = []
        for cell in row:
            if isinstance(cell, str):
                out.append(Paragraph(cell, CELL_H if r_i == 0 else CELL))
            else:
                out.append(cell)
        data.append(out)
    tbl = Table(data, colWidths=widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        # reportlab's table default is Helvetica; without this the PDF carries a
        # sans face it never means to show.
        ("FONTNAME", (0, 0), (-1, -1), SERIF),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F5F7F8")]),
    ]))
    flow = [Spacer(1, 4), tbl]
    if caption:
        flow.append(Paragraph(caption, CAPTION))
    else:
        flow.append(Spacer(1, 8))
    return flow


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

def _decorate(canvas, doc):
    canvas.saveState()
    canvas.setFont(SERIF_ITALIC, 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(2.2 * cm, 1.5 * cm,
                      "Faro — Demand forecasting and inventory decision engine")
    canvas.drawRightString(A4[0] - 2.2 * cm, 1.5 * cm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(2.2 * cm, 1.85 * cm, A4[0] - 2.2 * cm, 1.85 * cm)
    canvas.restoreState()


def build(story):
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        topMargin=2.0 * cm, bottomMargin=2.3 * cm,
        title="A Global Cross-Learning Demand Forecasting Engine for SMB Distribution",
        author="Angel Zeledon Fernandez",
        subject="Forecasting methodology",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_decorate)])
    doc.build(story)


def story():
    S: list = []
    A = S.append
    E = S.extend

    # ── Front matter ───────────────────────────────────────────────────────
    A(Paragraph("A Global Cross-Learning Demand Forecasting Engine "
                "for SMB Distribution", TITLE))
    A(Paragraph("Direct multi-horizon estimation, conformal lead-time "
                "uncertainty, and cost-asymmetric model selection", SUBTITLE))
    A(Paragraph("Angel Zeledon Fernandez", ParagraphStyle(
        "byline", parent=AUTHOR, fontName=SERIF, fontSize=11,
        textColor=INK, spaceAfter=2)))
    A(Paragraph("Faro &mdash; <i>ForecastingCore</i> technical report", AUTHOR))

    A(Paragraph("ABSTRACT", ABSTRACT_HEAD))
    A(Paragraph(
        "We describe the forecasting engine behind Faro, an inventory purchasing "
        "system for small and mid-sized distributors in Latin America. The "
        "operating regime is adversarial to classical per-series forecasting: "
        "catalogues of thousands of SKUs, median histories measured in months "
        "rather than years, intermittent and censored demand, and a decision "
        "whose loss function is sharply asymmetric. We set out the engine's "
        "construction in full: the canonical data contract and the exact feature "
        "algebra; recovery of stockout-censored demand; a global cross-learning "
        "gradient-boosted model that shares structure across the catalogue "
        "through per-series normalisation and series-identity conditioning; a "
        "direct multi-horizon formulation that removes recursive error "
        "compounding; split-conformal prediction bands calibrated per horizon "
        "and per lead time; and a selection rule that ranks candidate models by "
        "asymmetric cost rather than symmetric error. We close with the mapping "
        "from a predictive distribution to a purchase order, and with an "
        "explicit account of what the design does not solve.", ABSTRACT))

    # ── 1. Problem ─────────────────────────────────────────────────────────
    A(h1("1. Problem setting and notation"))
    A(p(
        "Let <i>S</i> denote the set of series in a tenant's catalogue. A series "
        "is a (SKU, location) pair; when a dataset carries no location dimension "
        "the engine assigns a single synthetic location so that the series key is "
        "uniform across the system. Time is discretised into buckets indexed by "
        "<i>t</i>, where a bucket is a day, a week, a fortnight or a month "
        "according to the granularity the session was trained at."))
    E(eq(r"y_{s,t} \in \mathbb{R}_{\geq 0}, \qquad s \in S,\; t = 1,\dots,n_s", number="1"))
    A(p(
        "The quantity of interest is not <i>y</i> itself. Purchasing decisions "
        "are exposed to the demand that accumulates while an order is in transit, "
        "so the target functional is the distribution of the sum over the lead "
        "time <i>L</i>, evaluated at a service level &beta;:"))
    E(eq(r"D_{s,T}(L) \;=\; \sum_{k=1}^{L} y_{s,T+k}, \qquad "
         r"\mathrm{ROP}_{s} \;=\; Q_{\beta}\!\left[D_{s,T}(L)\right]", number="2"))
    A(p(
        "This framing matters more than any modelling choice that follows. A "
        "system that optimises the conditional mean of <i>y</i> and then bolts a "
        "Gaussian safety-stock formula onto it is answering a different question "
        "from the one the buyer asked. Sections 6 and 9 return to this point."))

    A(h2("1.1 Information sets and the leakage boundary"))
    A(p(
        "Write &#8496;<sub>s,t</sub> for everything observable about series "
        "<i>s</i> up to and including bucket <i>t</i>. Every feature the engine "
        "constructs for a prediction of <i>y</i><sub>s,t+h</sub> is a measurable "
        "function of &#8496;<sub>s,t</sub> together with deterministic calendar "
        "information about the target date. The engine enforces this by "
        "construction rather than by review: all autoregressive features are "
        "built from a shifted series, and the validation protocol withholds a gap "
        "of buckets before every evaluation window."))

    # ── 2. Data contract ───────────────────────────────────────────────────
    A(h1("2. The canonical data contract"))
    A(p(
        "Uploads arrive with arbitrary column names in arbitrary languages. A "
        "mapping step resolves the user's columns onto a fixed canonical schema; "
        "unmapped optional fields are filled with declared defaults. Three fields "
        "are required and the rest are optional, but the optional ones are not "
        "decorative &mdash; each unlocks a specific capability, and the engine "
        "degrades explicitly rather than silently when they are absent."))
    E(table(
        [["Canonical field", "Required", "Role in the engine"],
         ["sku", "yes", "Series identity; categorical conditioning variable "
                        "<i>c<sub>s</sub></i> in the global model."],
         ["date", "yes", "Bucket index; source of all calendar features."],
         ["demand", "yes", "Target <i>y</i>."],
         ["store", "no", "Second identity dimension; makes the series key "
                         "(SKU, location)."],
         ["inventory", "no", "End-of-bucket stock. The <i>only</i> field that can "
                             "distinguish &ldquo;sold none&rdquo; from &ldquo;had "
                             "none to sell&rdquo;; gates Section 4."],
         ["price, regular_price, promo_price, discount", "no",
          "Exogenous regressors; also the basis of promotional indicators."],
         ["promo, promo_type", "no", "Event indicators."],
         ["lead_time", "no", "Per-SKU replenishment delay <i>L</i> used by "
                             "Section 9."],
         ["cost", "no", "Unit cost; enters the holding/stockout cost ratio."]],
        widths=[4.6 * cm, 1.9 * cm, 9.5 * cm],
        caption="Table 1. The canonical schema. Defaults for unmapped optional "
                "fields are broadcast constants, which is itself a hazard: see "
                "Section 4.3.",
    ))

    # ── 3. Preprocessing ───────────────────────────────────────────────────
    A(h1("3. Preprocessing"))
    A(h2("3.1 Transaction collapse"))
    A(p(
        "ERP exports commonly emit one row per sale rather than one row per "
        "bucket. Left unaggregated, three transactions of 4, 3 and 3 units on one "
        "day become three observations of a series whose level is then estimated "
        "near 3 instead of 10. The engine sums the target within each "
        "(date, series) key and carries non-additive columns forward by first "
        "value:"))
    E(eq(r"y_{s,t} \;=\; \sum_{r \in \mathcal{R}_{s,t}} q_r", number="3"))
    A(p("where &#8475;<sub>s,t</sub> is the set of raw rows sharing that key."))

    A(h2("3.2 Gap filling and outlier treatment"))
    A(p(
        "Missing buckets are reindexed onto a regular grid at the detected native "
        "frequency, with the fill policy chosen by the user "
        "(zero, mean, forward, linear interpolation, or leave). Reindexing is "
        "abandoned for a series whose implied span exceeds a hard cap, since a "
        "single mistyped date would otherwise materialise millions of rows. "
        "Outliers are treated per series by a configurable rule: &sigma;-clipping, "
        "percentile winsorisation, an IQR fence, removal with interpolation, or a "
        "log1p variance-stabilising transform."))
    E(eq(r"y^{\mathrm{clip}}_{s,t} \;=\; \min\!\Big(\max\big(y_{s,t},\; "
         r"Q_1 - k\,\mathrm{IQR}\big),\; Q_3 + k\,\mathrm{IQR}\Big)", number="4"))

    # ── 4. Censoring ───────────────────────────────────────────────────────
    A(h1("4. Censored demand recovery"))
    A(p(
        "A sales file records what was <i>sold</i>. On a bucket in which the SKU "
        "was out of stock, what was sold is a lower bound on what was demanded, "
        "and on a bucket that was stocked out from open to close the recorded zero "
        "means &ldquo;could not sell&rdquo;, not &ldquo;nobody wanted it&rdquo;. "
        "Formally the engine observes"))
    E(eq(r"y^{\mathrm{obs}}_{s,t} \;=\; \min\big(y_{s,t},\; A_{s,t}\big)", number="5"))
    A(p(
        "where <i>A</i><sub>s,t</sub> is the units available. Training on "
        "<i>y</i><sup>obs</sup> without correction is not merely biased &mdash; it "
        "is a closed feedback loop. The stockout depresses the forecast, the "
        "depressed forecast depresses the reorder quantity, the smaller order "
        "produces another stockout, and every symptom of the loop presents as an "
        "ordinary decline."))

    A(h2("4.1 Detection"))
    A(p("A bucket is flagged when end-of-bucket inventory is non-positive:"))
    E(eq(r"Z_{s,t} \;=\; \mathbf{1}\{\, I_{s,t} \leq 0 \,\}", number="6"))

    A(h2("4.2 Recovery"))
    A(p(
        "Unconstrained demand is estimated from the series' own uncensored "
        "behaviour: a trailing level times a day-of-week shape. Both estimators "
        "use only uncensored buckets, and the level is strictly backward-looking "
        "so that the estimate for a bucket never uses that bucket or any later "
        "one:"))
    E(eq(r"\lambda_{s,t} \;=\; \mathrm{median}\Big\{\, y_{s,u} \;:\; "
         r"t-W \leq u < t,\; Z_{s,u}=0 \,\Big\}", number="7"))
    E(eq(r"\varphi_{s,d} \;=\; \frac{\mathrm{median}\{\, y_{s,u} : "
         r"\mathrm{dow}(u)=d,\; Z_{s,u}=0 \,\}}"
         r"{\mathrm{median}\{\, y_{s,u} : Z_{s,u}=0 \,\}}", number="8"))
    A(p("The recovered observation is then clamped from both sides:"))
    E(eq(r"\hat{y}_{s,t} \;=\; \min\Big(\max\big(y^{\mathrm{obs}}_{s,t},\; "
         r"\lambda_{s,t}\,\varphi_{s,\mathrm{dow}(t)}\big),\; "
         r"\kappa_{\max}\,\lambda_{s,t}\Big)", number="9"))
    A(p(
        "The lower clamp encodes that units which did sell are a fact and only "
        "the unsold remainder is being inferred. The upper clamp, with "
        "&kappa;<sub>max</sub> = 3, prevents a single mis-flagged bucket in a "
        "spiky series from being lifted into an outlier that then dominates the "
        "level estimate of everything downstream. Recovery is suppressed entirely "
        "for series with fewer than fourteen uncensored buckets, which is the "
        "point at which (8) ceases to be estimable &mdash; two observations per "
        "weekday are needed for the shape to exist at all, and below that the "
        "estimator silently degenerates to a bare median while still publishing "
        "its output as recovered demand."))

    A(h2("4.3 The guard that matters more than the method"))
    A(p(
        "The canonical schema broadcasts <i>inventory</i> = 0 into every session "
        "in which the user did not map an inventory column. A naive reading of "
        "(6) would therefore flag every row of every such dataset as censored and "
        "inflate the entire tenant's forecast. The engine treats an inventory "
        "column that is never positive as absent evidence and returns the frame "
        "untouched. Declining to act is the correct failure mode here: the bias "
        "being corrected is real, but fabricating demand from evidence one does "
        "not have is worse than the bias."))

    # ── 5. Features ────────────────────────────────────────────────────────
    A(h1("5. Feature construction"))
    A(p(
        "Features fall into two classes with different leakage properties. "
        "Autoregressive features are functions of the target's own past and must "
        "be shifted; calendar features are deterministic functions of the date and "
        "are available for any bucket, past or future. Conflating the two is the "
        "origin of a large fraction of silent forecasting defects."))

    A(h2("5.1 Autoregressive features"))
    A(p(
        "All of the following are computed within series and are functions of "
        "&#8496;<sub>s,t-1</sub> only. The shift is applied <i>before</i> the "
        "window operator, not after:"))
    E(table(
        [["Feature", "Definition", "Note"],
         ["lag_&#8467;", inline(r"y_{s,t-\ell}"),
          "Direct autoregression."],
         ["roll_mean_w", inline(r"\frac{1}{w}\sum_{j=1}^{w} y_{s,t-j}"),
          "Level over the trailing window."],
         ["roll_std_w", inline(r"\mathrm{sd}\left(y_{s,t-1},\dots,y_{s,t-w}\right)"),
          "Volatility."],
         ["roll_min_w / roll_max_w", inline(r"\min / \max_{j=1..w} \; y_{s,t-j}"),
          "Range."],
         ["cv_w", inline(r"\mathrm{roll\_std}_w \,/\, (|\mathrm{roll\_mean}_w| + \epsilon)"),
          "Scale-free dispersion."],
         ["ewm_&alpha;", inline(r"\sum_{j\geq 1}(1-\alpha)^{\,j-1}\alpha\, y_{s,t-j}"),
          "Exponentially weighted level."],
         ["diff_d", inline(r"y_{s,t-1} - y_{s,t-1-d}"),
          "Differenced on the shifted series."],
         ["pct_change_d", inline(r"(y_{s,t-1}-y_{s,t-1-d})/(|y_{s,t-1-d}|+\epsilon)"),
          "Relative change."]],
        widths=[3.4 * cm, 7.6 * cm, 5.0 * cm],
        caption="Table 2. Autoregressive features. Every definition is indexed "
                "from t-1, never t.",
    ))
    A(p(
        "The differencing entries deserve comment. An unshifted difference "
        "<i>y</i><sub>t</sub> &minus; <i>y</i><sub>t-d</sub> satisfies "
        "<i>y</i><sub>t</sub> = lag<sub>d</sub> + diff<sub>d</sub> and therefore "
        "hands the model an exact algebraic reconstruction of its own target. The "
        "resulting validation error is near zero and the resulting forecast "
        "extrapolates the last observed slope indefinitely. Shifting first "
        "removes the identity."))

    A(h2("5.2 Calendar and holiday features"))
    A(p(
        "Calendar features are generated by a single function used by both "
        "training and inference. This is an architectural constraint, not a "
        "convenience: any divergence between the two produces a feature that "
        "carries signal during fitting and a constant at serving time, which is "
        "strictly worse than omitting the feature. Alongside the usual cyclical "
        "encodings"))
    E(eq(r"\sin\!\left(\tfrac{2\pi m}{12}\right),\;"
         r"\cos\!\left(\tfrac{2\pi m}{12}\right),\;"
         r"\sin\!\left(\tfrac{2\pi \mathrm{dow}}{7}\right),\;"
         r"\cos\!\left(\tfrac{2\pi \mathrm{dow}}{7}\right)", number="10"))
    A(p(
        "the engine emits holiday indicators and signed proximity. Let &#8461; be "
        "the national holiday set for the tenant's country, resolved from "
        "calendar rules and therefore defined for future years as readily as past "
        "ones. Easter is computed in closed form by the anonymous Gregorian "
        "algorithm, guaranteeing a non-empty holiday set even where a national "
        "calendar is unavailable:"))
    E(eq(r"\delta_t \;=\; \min_{\,\tau \in \mathbb{H}} \; \left| t - \tau \right|", number="11"))
    A(p(
        "Proximity is evaluated by binary search over the sorted holiday ordinals, "
        "giving <i>O</i>(<i>n</i> log |&#8461;|) rather than the "
        "<i>O</i>(<i>n</i>&middot;|&#8461;|) of a naive scan. A composite "
        "intensity score weights the classes of holiday that move retail demand "
        "by materially different amounts."))

    A(h2("5.3 Fourier seasonality on a fixed epoch"))
    A(p(
        "Fourier terms of period <i>P</i> and order <i>K</i> supply smooth "
        "seasonality that survives sparse data:"))
    E(eq(r"\left\{ \sin\!\left(\tfrac{2\pi k\, \tau_t}{P}\right),\; "
         r"\cos\!\left(\tfrac{2\pi k\, \tau_t}{P}\right) \right\}_{k=1}^{K}, "
         r"\qquad \tau_t = t - t_0", number="12"))
    A(p(
        "The choice of origin <i>t</i><sub>0</sub> is not free. If "
        "<i>t</i><sub>0</sub> is the first date of the uploaded file, the phase of "
        "every term depends on the customer's export window, two uploads covering "
        "the same calendar day disagree about that day's features, and inference "
        "cannot reproduce a value it never saw the origin for. The engine fixes "
        "<i>t</i><sub>0</sub> at the Unix epoch so that the map from date to "
        "feature is global and reproducible."))

    A(PageBreak())

    # ── 6. Models ──────────────────────────────────────────────────────────
    A(h1("6. Model families"))
    A(p(
        "The engine trains a portfolio and selects per series. Classical local "
        "models remain valuable on long, well-behaved series; the global model "
        "exists for the majority of a real catalogue, which is neither."))

    A(h2("6.1 Local per-series models"))
    E(table(
        [["Family", "Form", "Suited to"],
         ["ARIMA / SARIMAX",
          inline(r"\phi(B)(1-B)^d y_t = \theta(B)\varepsilon_t \; (+\, \beta^{\top} x_t)"),
          "Long, stationary-after-differencing series; exogenous regressors."],
         ["ETS",
          inline(r"\ell_t = \alpha y_t + (1-\alpha)(\ell_{t-1}+b_{t-1})"),
          "Smooth trend and stable seasonality."],
         ["Croston / TSB",
          inline(r"\hat{y} = \hat{z}/\hat{p}"),
          "Intermittent demand: size and interval estimated separately."],
         ["Prophet",
          inline(r"y_t = g(t) + s(t) + h(t) + \varepsilon_t"),
          "Strong multi-scale seasonality with changepoints."],
         ["GBDT (LightGBM, XGBoost)",
          inline(r"F_M(x) = \sum_{m=1}^{M}\nu\, f_m(x)"),
          "Nonlinear interaction of engineered features."],
         ["LSTM",
          inline(r"h_t = \mathrm{LSTM}(x_t, h_{t-1})"),
          "Long nonlinear dependence; requires substantial history."]],
        widths=[3.1 * cm, 7.2 * cm, 5.7 * cm],
        caption="Table 3. Local model families. Each is fitted independently per "
                "series.",
    ))
    A(p(
        "A router assigns each series a candidate subset from a classification of "
        "its own statistics &mdash; zero ratio, coefficient of variation, and STL "
        "seasonal strength. Routing only ever <i>narrows</i> the user's selection; "
        "it can never introduce a model the user did not choose."))

    A(h2("6.2 The global cross-learning model"))
    A(p(
        "Fitting an independent model per series is the wrong estimator for this "
        "regime. A catalogue of three thousand SKUs with fourteen months of "
        "history yields three thousand estimation problems, most of them with a "
        "few dozen effective observations, each blind to the fact that the SKU "
        "beside it is the same product in another size and peaks in the same week. "
        "Series below a minimum history are routed to naive baselines and a "
        "newly-launched SKU receives nothing at all."))
    A(p(
        "The global model replaces this with a single estimation problem over the "
        "pooled catalogue. Two devices make pooling informative rather than merely "
        "averaging."))

    A(h2("6.2.1 Per-series normalisation"))
    A(p(
        "Raw pooling lets a SKU selling 5000 units a day dominate the squared "
        "loss of one selling 5. The target and every feature denominated in units "
        "are divided by a series-specific scale:"))
    E(eq(r"\mu_s \;=\; \max\Big( \big| \mathrm{mean}\{ y_{s,t} : t \in W_0 \} "
         r"\big|,\; \varepsilon \Big), \qquad "
         r"\tilde{y}_{s,t} = y_{s,t}/\mu_s", number="13"))
    A(p(
        "The calibration window <i>W</i><sub>0</sub> ends at the first "
        "cross-validation cutoff, so the scale never sees a bucket it will later "
        "be evaluated on. Features are partitioned by dimensional analysis: "
        "quantities in units of the target (lags, rolling statistics, exponentially "
        "weighted means, differences) are divided by &mu;<sub>s</sub>; "
        "dimensionless ones (coefficients of variation, relative changes, calendar "
        "terms) are left alone. Dividing a ratio by a scale would be a unit error, "
        "and it would destroy the feature."))
    A(p(
        "Normalisation alone would erase the information that a series is large or "
        "small, which is itself predictive. The scale is therefore reintroduced as "
        "an explicit covariate together with a dispersion summary:"))
    E(eq(r"\ell_s = \log(1+\mu_s), \qquad "
         r"v_s = \mathrm{sd}\{y_{s,t} : t \in W_0\} \,/\, \mu_s", number="14"))

    A(h2("6.2.2 Series identity"))
    A(p(
        "Each identity dimension is encoded as an integer code and declared "
        "categorical to the booster, which splits on subsets of levels rather than "
        "on an arbitrary ordinal ordering. The model can therefore specialise "
        "toward a particular series where the evidence supports it and fall back "
        "on the pooled structure where it does not &mdash; the shrinkage is "
        "learned rather than imposed."))

    A(h2("6.2.3 Direct multi-horizon formulation"))
    A(p(
        "The engine does not forecast recursively. Recursion feeds a prediction "
        "back as though it were an observation, so a fourteen-step forecast is "
        "built on thirteen guesses, errors compound multiplicatively through the "
        "lag features, and the intervals &mdash; derived from one-step residuals "
        "&mdash; fail to widen to match. Instead the horizon is a feature. Each "
        "training row pairs the features known at an origin with the realised "
        "value <i>h</i> buckets later:"))
    E(eq(r"\mathcal{D} \;=\; \Big\{ \big( \tilde{x}_{s,i},\; c_s,\; \ell_s,\; v_s,\; h "
         r"\big) \;\longmapsto\; \tilde{y}_{s,\,i+h-1} \Big\}"
         r"_{\,s \in S,\; i,\; h=1..H+1}", number="15"))
    A(p("The model is the gradient-boosted regressor minimising squared error on "
        "the normalised target,"))
    E(eq(r"\hat{\theta} \;=\; \arg\min_{\theta} \sum_{(\,\cdot\,) \in \mathcal{D}} "
         r"\Big( \tilde{y}_{s,i+h-1} - f_{\theta}\big(\tilde{x}_{s,i}, c_s, "
         r"\ell_s, v_s, h\big) \Big)^{2}", number="16"))
    A(p("and the forecast for future step <i>k</i> is a single direct evaluation, "
        "rescaled and truncated at zero:"))
    E(eq(r"\hat{y}_{s,T+k} \;=\; \mu_s \cdot \max\Big(0,\; "
         r"f_{\hat\theta}\big(\tilde{x}_{s,\,\mathrm{origin}},\, c_s,\, \ell_s,\, "
         r"v_s,\; h = g_s + k\big)\Big)", number="17"))
    A(p(
        "The offset <i>g<sub>s</sub></i> in (17) is not cosmetic. Because every "
        "autoregressive feature is indexed from <i>t</i>&minus;1, the freshest "
        "constructible feature row conditions on the <i>second</i>-to-last "
        "observation, so the first genuinely future bucket lies "
        "<i>g<sub>s</sub></i> + 1 steps from the origin with "
        "<i>g<sub>s</sub></i> = 1. Omitting the offset publishes a forecast of the "
        "last observed bucket as though it were the first future one: aggregate "
        "error statistics look entirely normal and every dated value is wrong by "
        "one bucket. The stacked design in (15) is built to <i>H</i>+1 precisely "
        "so that the final step remains inside the horizon range the model was "
        "fitted on, rather than extrapolating the horizon covariate."))
    A(p(
        "Cost. The stacked design has |&#119967;| = <i>O</i>(<i>N</i>&middot;<i>H</i>) rows for "
        "<i>N</i> origins. Above a fixed ceiling the engine thins <i>origins</i> by "
        "a stride and never <i>horizons</i>: dropping origins costs sample size, "
        "whereas dropping horizons would leave entire steps of the published "
        "forecast with no training signal at all."))

    # ── 7. Validation ──────────────────────────────────────────────────────
    A(h1("7. Validation"))
    A(h2("7.1 Walk-forward with a withheld gap"))
    A(p(
        "Local models are validated by expanding-window cross-validation. For "
        "fold <i>i</i> with test window opening at &tau;<sub>i</sub>:"))
    E(eq(r"\mathcal{T}^{\mathrm{train}}_i = \{1,\dots,\tau_i - g\}, \qquad "
         r"\mathcal{T}^{\mathrm{test}}_i = \{\tau_i,\dots,\tau_i + m\}", number="18"))
    A(p(
        "with <i>g</i> set to the forecast horizon. Without the gap the model is "
        "fitted on data ending the bucket before the one it is graded on, while "
        "the freshest observation any production forecast can hold is <i>h</i> "
        "buckets old; for an autocorrelated series that is the most informative "
        "data there is, and the resulting score describes an easier problem than "
        "the product solves. When a short series cannot fund the full gap it is "
        "reduced rather than abandoned, subject to preserving both the fold count "
        "and a minimum training width &mdash; a fold of two rows is not a fold, "
        "merely a shape that does not raise."))
    A(p(
        "It is worth stating what the gap does <i>not</i> fix. Test rows still "
        "carry their own true lag features, so each row is still graded as a "
        "one-step problem. Only a model conditioning on a fixed origin, as in "
        "(17), is scored honestly across the whole horizon."))

    A(h2("7.2 Rolling-origin backtest"))
    A(p(
        "The global model is validated by simulating the production act. At each "
        "cutoff <i>T</i><sub>i</sub> the model is refitted on rows whose "
        "<i>target</i> date precedes the cutoff &mdash; not merely whose feature "
        "date does, which is the subtler of the two leaks &mdash; and is then "
        "asked to forecast forward from the last observation:"))
    E(eq(r"r^{(i)}_{s,h} \;=\; \tilde{y}_{s,\,T_i+h} - "
         r"\hat{\tilde{y}}_{s,\,T_i+h \mid T_i}", number="19"))
    A(p(
        "Folds run over the <i>date</i> axis rather than over row positions. With "
        "thousands of series of differing lengths concatenated, a row index is "
        "meaningless and splitting on it would place one SKU's future inside "
        "another SKU's past."))

    # ── 8. Uncertainty ─────────────────────────────────────────────────────
    A(h1("8. Uncertainty quantification"))
    A(h2("8.1 What the Gaussian band assumed"))
    A(p("The conventional construction is"))
    E(eq(r"\hat{y}_{T+h} \;\pm\; z_{\alpha}\,\hat{\sigma}, \qquad "
         r"\hat{\sigma} = \mathrm{sd}\big(\{r_{t}\}\big)", number="20"))
    A(p(
        "with &sigma; estimated from one-step residuals and reused unchanged for "
        "every step. Three assumptions are compounded there, and retail demand "
        "violates all three: that the error is Gaussian, when demand is "
        "non-negative, discrete and frequently zero; that it is symmetric, which a "
        "floor at zero precludes; and that a fourteen-step error is the size of a "
        "one-step error, which it is not. The band is therefore narrowest in "
        "exactly the region where the buyer most needs it wide."))

    A(h2("8.2 Split conformal calibration per horizon"))
    A(p(
        "The engine replaces (20) with empirical quantiles of the realised "
        "backtest residuals, computed separately for each horizon. Pooling the "
        "residuals of (19) across the catalogue in <i>normalised</i> units gives"))
    E(eq(r"\hat{q}_h(\alpha) \;=\; \mathrm{Quantile}_{\alpha}\Big( "
         r"\big\{ r^{(i)}_{s,h} \big\}_{i,\,s} \Big)", number="21"))
    E(eq(r"\mathcal{C}_{\alpha}\big(\hat{y}_{s,T+h}\big) \;=\; "
         r"\hat{y}_{s,T+h} \;+\; \mu_s\, \hat{q}_h(\alpha)", number="22"))
    A(p(
        "No distributional assumption is made, the band widens with <i>h</i> "
        "because the residuals do, and asymmetry is preserved rather than "
        "averaged away. Normalisation is what makes the pooling legitimate and is "
        "also what makes it valuable: a SKU with eight weeks of history inherits "
        "the shape of the catalogue's error distribution instead of estimating its "
        "own from a handful of points. Where a horizon has too few residuals to "
        "support a quantile, the engine falls back to the pooled bank across "
        "horizons and says so, rather than reporting a confident number computed "
        "from four observations. Independently estimated quantiles can cross; a "
        "running maximum restores monotonicity while preserving every quantile "
        "that was already consistent."))
    A(p(
        "Coverage is finite-sample valid under exchangeability of the residuals. "
        "Demand residuals are not perfectly exchangeable across time, so the "
        "guarantee should be read as strong-in-practice rather than exact &mdash; "
        "which is still a materially stronger claim than (20) can make."))

    A(h2("8.3 Cumulative bands for the lead time"))
    A(p(
        "Section 1 established that the decision-relevant variable is a sum. The "
        "same backtest yields its error directly:"))
    E(eq(r"R^{\mathrm{cum}}_{s,L} \;=\; \sum_{h=1}^{L} "
         r"\Big( \tilde{y}_{s,T+h} - \hat{\tilde{y}}_{s,T+h} \Big)", number="23"))
    E(eq(r"\widehat{Q}_{\beta}\big[D_{s,T}(L)\big] \;=\; "
         r"\sum_{h=1}^{L} \hat{y}_{s,T+h} \;+\; \mu_s\,\hat{q}^{\mathrm{cum}}_{L}(\beta)", number="24"))
    A(p(
        "Equation (24) is the quantity the reorder point needs, obtained without "
        "assuming independence across buckets. The classical alternative, "
        "&sigma;&radic;<i>L</i>, is exactly the independence assumption in "
        "disguise, and it understates risk precisely on the SKUs whose forecast "
        "bias is persistent &mdash; a forecast that runs high today usually runs "
        "high tomorrow."))

    # ── 9. Selection ───────────────────────────────────────────────────────
    A(h1("9. Model selection under an asymmetric loss"))
    A(p(
        "Candidate models were previously ranked by MAE, and the displayed "
        "accuracy by WAPE. Both are symmetric: they score a forecast ten units low "
        "exactly as well as one ten units high. For a distributor the two are not "
        "equivalent &mdash; the surplus costs warehouse space and working capital, "
        "the shortfall costs the sale and sometimes the customer. The engine ranks "
        "by an asymmetric per-unit cost,"))
    E(eq(r"\mathcal{L}_{\kappa}(y,\hat{y}) \;=\; \frac{1}{n}\sum_{t=1}^{n} "
         r"\Big[ (\hat{y}_t - y_t)_{+} \;+\; \kappa\,(y_t - \hat{y}_t)_{+} \Big]", number="25"))
    A(p(
        "with &kappa; the stockout-to-holding ratio. Where a candidate produces a "
        "quantile forecast rather than a point, the proper scoring rule is the "
        "pinball loss, which is the loss the reorder point is actually optimal "
        "for:"))
    E(eq(r"\mathcal{P}_{q}(y,\hat{y}) \;=\; \frac{1}{n}\sum_{t=1}^{n} "
         r"\max\Big( q\,(y_t-\hat{y}_t),\; (q-1)(y_t-\hat{y}_t) \Big)", number="26"))
    A(p(
        "Note that scoring a <i>q</i> = 0.95 forecast with MAE rewards it for "
        "being close to the middle of the distribution, which is precisely what a "
        "reorder point must not be."))
    A(p(
        "Two honesty constraints follow. First, the accuracy reported to the user "
        "is the WAPE <i>of the model actually selected</i>, not the best WAPE "
        "available for that series; those coincided while selection was by WAPE "
        "and diverge once it is by cost, and reporting the better figure would "
        "advertise a forecast nobody is using. Second, baselines are scored and "
        "displayed but excluded from selection: they exist to be beaten, and "
        "purchasing from a naive forecast because it happened to win a fold is a "
        "defect, not a fallback."))

    # ── 10. Decision ───────────────────────────────────────────────────────
    A(h1("10. From predictive distribution to purchase order"))
    A(h2("10.1 Reorder point and safety stock"))
    A(p(
        "Given (24), the reorder point and the implied cushion are read directly "
        "off the measured distribution:"))
    E(eq(r"\mathrm{SS}_{s} \;=\; \mu_s\,\hat{q}^{\mathrm{cum}}_{L}(\beta), \qquad "
         r"\mathrm{ROP}_{s} \;=\; \sum_{h=1}^{L}\hat{y}_{s,T+h} \;+\; \mathrm{SS}_{s}", number="27"))
    A(p("Where the measurement is unavailable the engine falls back explicitly to "
        "the classical form,"))
    E(eq(r"\mathrm{SS}^{\mathrm{classical}}_{s} \;=\; z_{\beta}\,\hat{\sigma}_s\,\sqrt{L}", number="28"))
    A(p(
        "and the two must never be composed. A recurring class of defect is to "
        "read the top of an existing band as though it were a standard deviation "
        "and then multiply by <i>z</i> again; since the band was itself "
        "approximately a 90th percentile, the result is a configured 95% service "
        "level being served at roughly 98% &mdash; more capital tied up than the "
        "buyer asked for, with nothing in the product disclosing the discrepancy. "
        "Both branches of the engine's estimator return the same quantity, a "
        "standard deviation, by dividing any band by the <i>z</i> that produced it."))
    A(p("The order quantity applies on-hand stock and the supplier's minimum:"))
    E(eq(r"Q_s \;=\; \Big\lceil \frac{\max\big(0,\; \mathrm{ROP}_s - "
         r"\mathrm{stock}_s\big)}{\mathrm{MOQ}_s} \Big\rceil \cdot \mathrm{MOQ}_s", number="29"))
    A(p(
        "The service level itself need not be a free parameter: under linear "
        "holding and shortage costs the newsvendor optimum is "
        "&beta;* = <i>c</i><sub>u</sub> / (<i>c</i><sub>u</sub> + <i>c</i><sub>o</sub>), "
        "so a tenant who can state its cost ratio can derive the level rather than "
        "guess it."))

    A(h2("10.2 Multi-warehouse allocation"))
    A(p(
        "With several locations, purchasing and inter-warehouse transfer are "
        "solved jointly as a mixed-integer program over buckets "
        "<i>t</i> = 1..<i>H</i>. Writing <i>o</i>, <i>x</i>, <i>v</i>, <i>u</i> for "
        "order, transfer, ending inventory and shortage, the balance constraint is"))
    E(eq(r"v_{i,w,t} - v_{i,w,t-1} - o_{i,w,t} - \!\!\sum_{a \neq w}\! x_{i,a,w,t} "
         r"+ \!\!\sum_{b \neq w}\! x_{i,w,b,t} - u_{i,w,t} \;=\; -d_{i,w,t}", number="30"))
    A(p("and the objective minimises total cost,"))
    E(eq(r"\min \sum_{i,w,t} \Big( c^{h}_i v_{i,w,t} + c^{u}_i u_{i,w,t} + "
         r"c^{o}_i o_{i,w,t} \Big) + \sum_{a,b,t}\Big( c^{x}_{ab} x_{\cdot,a,b,t} + "
         r"c^{f}_{ab}\, \zeta_{a,b,t} \Big)", number="31"))
    A(p(
        "where &zeta; is a binary indicating that lane (<i>a</i>,<i>b</i>) "
        "dispatches in bucket <i>t</i>, linked by "
        "<i>x</i> &le; <i>M</i><sub>i</sub>&zeta;. The big-M is derived per SKU "
        "from that SKU's own stock plus horizon demand rather than from a global "
        "constant: a single M dominated by the largest SKU in the catalogue drives "
        "the ratio <i>x</i>/<i>M</i> for every other SKU below the solver's "
        "integrality tolerance, at which point the fixed cost is satisfied at "
        "&zeta; &asymp; 0 and shipments ride for free. Per-SKU scaling also "
        "tightens the linear relaxation. Lead times enter as upper bounds of zero "
        "on arrivals earlier than the transit time allows."))

    # ── 11. Hierarchy ──────────────────────────────────────────────────────
    A(h1("11. Hierarchical reconciliation"))
    A(p(
        "Forecasts produced independently at SKU, category and total level are "
        "mutually inconsistent. With <i>S</i> the summing matrix mapping bottom-level "
        "series to all levels, reconciliation projects the base forecasts onto the "
        "coherent subspace:"))
    E(eq(r"\tilde{y} \;=\; S\big(S^{\top}W^{-1}S\big)^{-1}S^{\top}W^{-1}\hat{y}", number="32"))
    A(p(
        "Bottom-up and top-down are the special cases in which <i>W</i> is chosen "
        "to select a single level. The minimum-trace solution instead sets "
        "<i>W</i> to the covariance of the base forecast errors, usually via a "
        "shrinkage estimator toward a diagonal target. The practical consequence "
        "is that MinT improves the bottom level as well as the aggregate, whereas "
        "bottom-up leaves the SKU forecasts exactly as they were and top-down "
        "discards their individual information entirely."))

    # ── 12. Limitations ────────────────────────────────────────────────────
    A(h1("12. Limitations"))
    A(bullets([
        "<b>Cross-family comparability.</b> Local ML models are graded one step "
        "ahead, statistical models across their whole test window, and the global "
        "model on a rolling origin. These are different questions. Selection is "
        "fairer than ranking by symmetric error, but it is not yet apples to "
        "apples across families; unifying the protocol is the single largest "
        "remaining correctness gain.",
        "<b>Censoring evidence.</b> Recovery requires historical inventory. "
        "Tenants who upload sales alone keep the downward bias of Section 4, and "
        "the engine cannot detect that they have it.",
        "<b>Exchangeability.</b> Conformal coverage assumes exchangeable "
        "residuals. A structural break &mdash; a new distribution channel, a price "
        "reposition &mdash; violates it precisely when the bands matter most.",
        "<b>Substitution and cannibalisation.</b> Series are modelled as "
        "conditionally independent given their features. Demand transfer between "
        "substitutable SKUs, and the demand a stockout displaces onto a "
        "neighbour, are outside the current formulation.",
        "<b>Fixed cost asymmetry in ranking.</b> Selection uses a standard 3:1 "
        "shortfall-to-surplus ratio for every tenant; the tenant's configured "
        "ratio drives the order quantity but not the ranking.",
        "<b>Price and promotion.</b> The schema carries them and SARIMAX consumes "
        "them as exogenous regressors, but the global model does not yet treat a "
        "planned future price as a known future covariate, which is where most of "
        "the remaining promotional signal lives.",
    ]))

    # ── 13. Summary ────────────────────────────────────────────────────────
    A(h1("13. Summary of the estimator"))
    E(table(
        [["Stage", "Choice", "Rejected alternative"],
         ["Pooling", "One model across the catalogue, per-series normalised",
          "Independent model per series"],
         ["Horizon", "Direct, horizon as a covariate",
          "Recursive substitution of predictions"],
         ["Identity", "Categorical codes + level + dispersion covariates",
          "Series-agnostic pooling"],
         ["Validation", "Rolling origin on the date axis, gap = horizon",
          "Row-index folds with adjacent train/test"],
         ["Intervals", "Split conformal, per horizon, pooled in scaled units",
          "Gaussian band from one-step residuals"],
         ["Lead-time risk", "Measured cumulative quantile",
          inline(r"z_{\beta}\,\sigma\sqrt{L}")],
         ["Selection", "Asymmetric cost / pinball loss", "MAE or WAPE"],
         ["Censoring", "Detect from inventory, lift with clamps",
          "Train on observed sales"]],
        widths=[3.0 * cm, 7.0 * cm, 6.0 * cm],
        caption="Table 4. The engine's design choices, each against the "
                "convention it replaces.",
    ))

    A(h1("References"))
    for ref in [
        "Hyndman, R. J., &amp; Athanasopoulos, G. <i>Forecasting: Principles and "
        "Practice</i>, 3rd ed. OTexts.",
        "Wickramasuriya, S. L., Athanasopoulos, G., &amp; Hyndman, R. J. (2019). "
        "Optimal forecast reconciliation for hierarchical and grouped time series "
        "through trace minimization. <i>JASA</i>, 114(526).",
        "Vovk, V., Gammerman, A., &amp; Shafer, G. (2005). <i>Algorithmic Learning "
        "in a Random World</i>. Springer.",
        "Lei, J., G'Sell, M., Rinaldo, A., Tibshirani, R. J., &amp; Wasserman, L. "
        "(2018). Distribution-free predictive inference for regression. "
        "<i>JASA</i>, 113(523).",
        "Ke, G., et al. (2017). LightGBM: A highly efficient gradient boosting "
        "decision tree. <i>NeurIPS</i>.",
        "Januschowski, T., et al. (2020). Criteria for classifying forecasting "
        "methods. <i>International Journal of Forecasting</i>, 36(1).",
        "Makridakis, S., Spiliotis, E., &amp; Assimakopoulos, V. (2022). M5 "
        "accuracy competition: Results, findings and conclusions. "
        "<i>International Journal of Forecasting</i>, 38(4).",
        "Syntetos, A. A., &amp; Boylan, J. E. (2005). The accuracy of intermittent "
        "demand estimates. <i>International Journal of Forecasting</i>, 21(2).",
        "Nahmias, S., &amp; Olsen, T. L. <i>Production and Operations Analysis</i>, "
        "7th ed. Waveland Press.",
    ]:
        A(Paragraph(ref, ParagraphStyle(
            "ref", parent=BODY, fontSize=8.6, leading=11.6,
            leftIndent=14, firstLineIndent=-14, spaceAfter=4)))

    return S


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build(story())
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
