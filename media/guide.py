"""
Render the guide: media/guide/*.md -> media/Assurance Console - Guide.pdf.

The guide replaces the tutorial video. Its words live in media/guide/, one file
per part, read in name order, in a small
markdown: `#`, `##`, `###` headings, paragraphs, `- ` bullets, `| a | b |`
tables (first row is the heading), `+ ` and `x ` for what the app can and
cannot do, `> ` for a limitation worth stopping on, `!shot name | caption`
for a screenshot from media/shots (taken by media/capture.py), and
`**bold**`, `*italic*`, `` `code` `` inside a line. The look follows the app:
the maroon cover band, gold, Segoe UI, and the pink margin rule of a ledger.

    python media/capture.py     # the screenshots, from the sample data
    python media/guide.py
"""

import re
import sys
from datetime import date
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "media" / "guide"
SHOTS = ROOT / "media" / "shots"
OUT = ROOT / "media" / "Assurance Console - Guide.pdf"
sys.path.insert(0, str(ROOT))
from app.backend.version import VERSION  # noqa: E402 - the app's own number, in one place

COVER, DEEP, GOLD, GOLD_INK = (colors.HexColor(c) for c in ("#74191f", "#58121a", "#e9c46a", "#9a6a00"))
INK, INK2, INK3 = (colors.HexColor(c) for c in ("#1c1e23", "#454a54", "#626773"))
RULE, WASH, MARGIN = (colors.HexColor(c) for c in ("#dfe5ec", "#f5f6f8", "#e6aeb3"))
OK, OK_WASH, LOOK_INK, LOOK_WASH, SERIOUS, SERIOUS_WASH = (colors.HexColor(c) for c in (
    "#1f7a4d", "#e8f3ec", "#875800", "#fcf3e0", "#b42318", "#fbebe9"))

FONTS = Path("C:/Windows/Fonts")
for name, file in (("Sans", "segoeui.ttf"), ("Sans-Bold", "segoeuib.ttf"), ("Sans-Italic", "segoeuii.ttf"),
                   ("Sans-BoldItalic", "segoeuiz.ttf"), ("Mono", "consola.ttf"), ("Sym", "seguisym.ttf")):
    if not (FONTS / file).exists():
        sys.exit(f"{file} is not in {FONTS}; the guide is set in Segoe UI, which has the rupee sign.")
    pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic",
                              boldItalic="Sans-BoldItalic")

PAGE_W, PAGE_H = A4
LEFT, RIGHT, TOP, BOTTOM = 24 * mm, 18 * mm, 22 * mm, 20 * mm
WIDTH = PAGE_W - LEFT - RIGHT


def style(name, **kw):
    base = dict(fontName="Sans", fontSize=9.6, leading=14, textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "h1": style("h1", fontName="Sans-Bold", fontSize=21, leading=26, textColor=COVER, spaceBefore=2, spaceAfter=8),
    "h2": style("h2", fontName="Sans-Bold", fontSize=13.5, leading=18, textColor=COVER, spaceBefore=12, spaceAfter=4),
    "h3": style("h3", fontName="Sans-Bold", fontSize=10.8, leading=15, textColor=INK, spaceBefore=8, spaceAfter=2),
    "p": style("p", spaceAfter=5),
    "li": style("li", leftIndent=12, bulletIndent=2, spaceAfter=2.2),
    "cell": style("cell", fontSize=8.6, leading=11.6),
    "head": style("head", fontName="Sans-Bold", fontSize=8.6, leading=11.6, textColor=INK2),
    "note": style("note", fontSize=9.2, leading=13.4),
    "cap": style("cap", fontName="Sans-Italic", fontSize=8.4, leading=11, textColor=INK3, spaceBefore=3, spaceAfter=9),
    "toc1": style("toc1", fontName="Sans-Bold", fontSize=10.2, leading=15, textColor=INK),
    "toc2": style("toc2", fontSize=9, leading=12.5, leftIndent=14, textColor=INK2),
}


def inline(text):
    """**bold**, *italic*, `code` and escaping, for one line of guide.md."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", lambda m: f'<font name="Mono" size="8.6">{m.group(1)}</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    return text


class Heading(Paragraph):
    """A heading that files itself in the contents."""

    def __init__(self, text, level):
        self.level = level
        super().__init__(inline(text), S[f"h{level}"])


class Guide(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(str(path), pagesize=A4, title="Assurance Console - the complete guide",
                         author="Assurance Console", subject=f"Version {VERSION}")
        body = Frame(LEFT, BOTTOM, WIDTH, PAGE_H - TOP - BOTTOM, id="body", leftPadding=0, rightPadding=0,
                     topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("cover", [body], onPage=cover_page),
                               PageTemplate("body", [body], onPageEnd=body_page)])
        self.section = ""
        self._n = 0

    def beforeDocument(self):
        self.section, self._n = "", 0                     # each pass must file the same keys, or it never settles

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading) and flowable.level <= 2:
            text = flowable.getPlainText()
            if flowable.level == 1:
                self.section = text
            self._n += 1
            key = f"h{self._n}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=flowable.level - 1)
            self.notify("TOCEntry", (flowable.level - 1, text, self.page, key))


def cover_page(canv, doc):
    canv.saveState()
    canv.setFillColor(COVER)
    canv.rect(0, PAGE_H - 118 * mm, PAGE_W, 118 * mm, stroke=0, fill=1)
    canv.setFillColor(DEEP)
    canv.rect(0, PAGE_H - 121 * mm, PAGE_W, 3 * mm, stroke=0, fill=1)
    canv.setFillColor(GOLD)
    canv.setFont("Sans-Bold", 11)
    canv.drawString(LEFT, PAGE_H - 30 * mm, "  ".join("ASSURANCE CONSOLE"))
    canv.setFillColor(colors.white)
    canv.setFont("Sans-Bold", 30)
    canv.drawString(LEFT, PAGE_H - 62 * mm, "The complete guide")
    canv.setFont("Sans", 15)
    canv.drawString(LEFT, PAGE_H - 74 * mm, "Everything it does, how to use it,")
    canv.drawString(LEFT, PAGE_H - 82 * mm, "and everything it cannot do")
    canv.setFillColor(GOLD)
    canv.setFont("Sans-Bold", 10.5)
    canv.drawString(LEFT, PAGE_H - 104 * mm, f"Version {VERSION}  ·  {date.today():%d %B %Y}")
    canv.setStrokeColor(MARGIN)
    canv.setLineWidth(0.8)
    canv.line(LEFT - 7 * mm, BOTTOM, LEFT - 7 * mm, PAGE_H - 125 * mm)
    canv.restoreState()


def body_page(canv, doc):
    canv.saveState()
    canv.setStrokeColor(MARGIN)                           # the ledger's margin rule
    canv.setLineWidth(0.8)
    canv.line(LEFT - 7 * mm, BOTTOM - 6 * mm, LEFT - 7 * mm, PAGE_H - 12 * mm)
    canv.setStrokeColor(COVER)
    canv.setLineWidth(1.4)
    canv.line(LEFT, PAGE_H - 13 * mm, PAGE_W - RIGHT, PAGE_H - 13 * mm)
    canv.setFont("Sans-Bold", 7.6)
    canv.setFillColor(COVER)
    canv.drawString(LEFT, PAGE_H - 11 * mm, "  ".join("ASSURANCE CONSOLE"))
    canv.setFont("Sans", 7.6)
    canv.setFillColor(INK3)
    canv.drawRightString(PAGE_W - RIGHT, PAGE_H - 11 * mm, doc.section)
    canv.drawRightString(PAGE_W - RIGHT, 11 * mm, str(doc.page))
    canv.drawString(LEFT, 11 * mm, f"The complete guide · version {VERSION}")
    canv.restoreState()


def table(rows, widths=None):
    head, *body = rows
    n = len(head)
    widths = widths or [WIDTH / n] * n
    data = [[Paragraph(inline(c), S["head"]) for c in head]] + [[Paragraph(inline(c), S["cell"]) for c in r]
                                                                  for r in body]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.1, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def widths_for(rows):
    """Columns sized by how much they hold, within sensible limits."""
    n = len(rows[0])
    load = [max(12, min(60, max(len(r[i]) if i < len(r) else 0 for r in rows))) for i in range(n)]
    return [WIDTH * x / sum(load) for x in load]


def can_list(items):
    """What it can (+) and cannot (x) do, each with its pen mark."""
    rows = []
    for mark, text in items:
        sign = '<font name="Sym" color="#1f7a4d">✓</font>' if mark == "+" else '<font name="Sym" color="#b42318">✗</font>'
        rows.append([Paragraph(sign, S["cell"]), Paragraph(inline(text), S["cell"])])
    t = Table(rows, colWidths=[6 * mm, WIDTH - 6 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("TOPPADDING", (0, 0), (-1, -1), 1.5)]))
    return t


def note(text, lead=()):
    """A limitation worth stopping on: the look colour of the app's key. `lead`
    is the heading above it, kept on the same page (see heading_above)."""
    t = Table([[Paragraph(inline(text), S["note"])]], colWidths=[WIDTH], hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LOOK_WASH),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.4, colors.HexColor("#c98a1c")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return KeepTogether([*lead, Spacer(1, 2), t, Spacer(1, 6)])


def shot(name, caption, lead=()):
    """A screenshot at the page's width, cut short if it would run past a page."""
    path = SHOTS / f"{name}.png"
    if not path.exists():
        return [*lead, Paragraph(f"<i>[screenshot {inline(name)} missing - run media/capture.py]</i>", S["cap"])]
    img = PILImage.open(path)
    w, h = img.size
    max_h = (PAGE_H - TOP - BOTTOM) * 0.62
    width = min(WIDTH, w / 1.5 * 0.62)                   # shots are 1.5x; never blown up past their pixels
    height = width * h / w
    if height > max_h:                                   # a tall section: keep its top
        keep = int(h * max_h / height)
        crop = SHOTS / f".{name}-top.png"
        img.crop((0, 0, w, keep)).save(crop)
        path, height = crop, max_h
    pic = Image(str(path), width=width, height=height)
    frame = Table([[pic]], colWidths=[width], hAlign="LEFT")
    frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return [KeepTogether([*lead, Spacer(1, 3), frame, Paragraph(inline(caption), S["cap"])])]


def parse(text):
    """guide.md, block by block, into flowables."""
    story, para, rows, cans = [], [], [], []

    def flush():
        nonlocal para, rows, cans
        if para:
            story.append(Paragraph(inline(" ".join(para)), S["p"]))
        if rows:
            story.append(table(rows, widths_for(rows)))
            story.append(Spacer(1, 7))
        if cans:
            story.append(can_list(cans))
            story.append(Spacer(1, 6))
        para, rows, cans = [], [], []

    def heading_above():
        # reportlab's keepWithNext will not join a heading to a KeepTogether,
        # so a heading directly above a picture or note goes inside it instead
        # - or it can end a page on its own while the picture starts the next.
        return [story.pop()] if story and isinstance(story[-1], Heading) else []

    first_h1 = True
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("<!--"):
            continue
        if not line.strip():
            flush()
            continue
        m = re.match(r"(#{1,3}) (.+)", line)
        if m:
            flush()
            level = len(m.group(1))
            if level == 1:
                if not first_h1:
                    story.append(PageBreak())
                first_h1 = False
            elif level == 2:
                story.append(CondPageBreak(40 * mm))
            story.append(Heading(m.group(2), level))
            continue
        if line.startswith("- "):
            flush_para = para
            if flush_para:
                story.append(Paragraph(inline(" ".join(para)), S["p"]))
                para.clear()
            story.append(Paragraph(inline(line[2:]), S["li"], bulletText="•"))
            continue
        if line.startswith("  ") and story and isinstance(story[-1], Paragraph) and story[-1].style is S["li"]:
            prev = story.pop()                            # a bullet carried onto the next line
            story.append(Paragraph(prev.text + " " + inline(line.strip()), S["li"], bulletText="•"))
            continue
        if line.startswith("|"):
            if para:
                flush()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                rows.append(cells)
            continue
        if line[:2] in ("+ ", "x "):
            if para or rows:
                flush()
            cans.append((line[0], line[2:]))
            continue
        if line.startswith("> "):
            flush()
            story.append(note(line[2:], heading_above()))
            continue
        if line.startswith("!shot "):
            flush()
            name, _, caption = line[6:].partition("|")
            story.extend(shot(name.strip(), caption.strip(), heading_above()))
            continue
        para.append(line.strip())
    flush()
    return story


def build():
    doc = Guide(OUT)
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story = [NextPageTemplate("body"), Spacer(1, 128 * mm),
             Paragraph(inline("A plain account of the whole app - both modules, every screen, every file it "
                              "reads and writes - and, as carefully, of what it does **not** do. Where a "
                              "limit exists it is said where you would meet it, and gathered again at the end."),
                       style("lede", fontSize=11.5, leading=17, textColor=INK2)),
             PageBreak(), Paragraph("Contents", S["h1"]), toc, PageBreak()]
    story += parse("\n\n".join(p.read_text(encoding="utf-8") for p in sorted(SOURCE.glob("*.md"))))
    doc.multiBuild(story)
    for tmp in SHOTS.glob(".*-top.png"):
        tmp.unlink()
    print(f"{OUT}  ({OUT.stat().st_size / 1024:,.0f} KB, {doc.page} pages)")


if __name__ == "__main__":
    build()
