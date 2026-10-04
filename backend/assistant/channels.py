"""Channel adapters: the same answer, shaped for where it is read.

A channel contributes two things and nothing else:

  * `style` — one paragraph of prompt text telling the model how to write for
    this surface (English, like every prompt);
  * `format(text)` — a deterministic post-pass over the model's answer, so a
    model that ignores the style instruction still cannot ship a markdown table
    to a phone.

The core never branches on the channel name. Adding a surface (email digest,
MCP `ask` tool, a future integration) is one class here plus one entry in
`CHANNELS`; the data, the tools, the persona and the grounding guard are shared.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The app screens the assistant may point at (core.DEEP_LINKS); app routes are
# deliberately Spanish (CLAUDE.md).
APP_PATHS = ("compras", "pedidos", "inventario", "proveedores", "pronosticos", "ventas", "impacto")


@dataclass(frozen=True)
class Channel:
    name: str
    style: str
    # Characters the formatted reply may take. None: no hard cap.
    max_chars: int | None = None
    # Where relative deep links (`/compras`) point. Empty: keep them relative.
    link_base: str = ""

    def format(self, text: str) -> str:
        return (text or "").strip()


class WebChannel(Channel):
    """The /asistente screen renders a markdown subset (bold, bullets, headings).
    Relative links stay relative: the browser is already inside the app."""

    def format(self, text: str) -> str:
        text = (text or "").strip()
        # Tables are not rendered by the screen's markdown-lite: flatten a
        # table row into a bullet rather than print pipes.
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", s):
                continue
            if s.startswith("|") and s.endswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                line = "- " + " · ".join(c for c in cells if c)
            lines.append(line)
        return "\n".join(lines).strip()


class WhatsAppChannel(Channel):
    """A phone screen: short, plain, WhatsApp's own *bold*, absolute links."""

    def format(self, text: str) -> str:
        text = (text or "").strip()
        # [label](/path) -> "label: https://app/path"
        def _link(m: re.Match) -> str:
            label, href = m.group(1), m.group(2)
            if href.startswith("/") and self.link_base:
                href = self.link_base.rstrip("/") + href
            return f"{label}: {href}"
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, text)
        # A bare app path ("revisa /compras") is not tappable on a phone.
        if self.link_base:
            base = self.link_base.rstrip("/")
            text = re.sub(r"(?<![\w/.:])(/(?:" + "|".join(APP_PATHS) + r"))\b",
                          lambda m: base + m.group(1), text)
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)           # **bold** -> *bold*
        text = re.sub(r"^\s*#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
        text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s and "-" in s and re.fullmatch(r"\|?[\s:|-]+\|?", s):
                continue                                         # table separator row
            if s.startswith("|") and s.endswith("|"):
                line = "• " + " · ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
            out.append(line)
        text = "\n".join(out)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if self.max_chars and len(text) > self.max_chars:
            cut = text[: self.max_chars]
            nl = cut.rfind("\n")
            text = (cut[:nl] if nl > self.max_chars * 0.5 else cut).rstrip() + "\n…"
        return text


def _frontend_url() -> str:
    # Environment-only by design (CLAUDE.md, Configuration): read off settings.
    from backend.config import settings
    return (getattr(settings, "frontend_url", "") or "").strip()


def get_channel(name: str) -> Channel:
    name = (name or "web").lower()
    if name == "whatsapp":
        return WhatsAppChannel(
            name="whatsapp",
            style=("You are writing a WhatsApp message read on a phone. Plain text, at most "
                   "6 short lines, no headings, no tables, no markdown links — write the "
                   "screen path (for example /compras) when you point somewhere. Lists use "
                   "'• '. One emoji at most."),
            # Twilio refuses a WhatsApp body over 1,600 characters.
            max_chars=1200,
            link_base=_frontend_url(),
        )
    return WebChannel(
        name="web",
        style=("You are writing in the StockAI web app's chat panel, which renders a markdown "
               "subset: **bold**, bullet lists and ### headings (no tables). Keep it short: a "
               "direct answer first, then at most 6 bullets. Link screens as markdown, e.g. "
               "[Compras](/compras)."),
    )


CHANNELS = ("web", "whatsapp")
