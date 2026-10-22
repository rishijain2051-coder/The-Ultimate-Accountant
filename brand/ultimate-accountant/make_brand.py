"""
The Ultimate Accountant mark and lockups, drawn as paths: `python brand/ultimate-accountant/make_brand.py`.

The mark ("Nothing More"): "/-", what is written after an amount in India so that
nothing can be added to it - the last word on a figure. It also half-reads as an A.
A 60 degree slash and a bar, flat ends, the bar's end cut on the slash's angle: the
family's vocabulary (brand/README.md). The name
is lettered in Assurance Console's square-cut capitals (with a T, I and M drawn for
it), so every file is outlines and no font licence is involved. Variants, icons and
the board come from the logo-design skill's scripts (see README.md here).

Needs shapely (installed with the app's OCR): its straight-edged unions come out exact.
"""
import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parent
INK, GOLD = "#1C1E23", "#E9C46A"
TILE_RX = 57.6                                     # the family's app tile (brand/symbol/*-app-icon.svg)
CENTRE = (128, 126)                                # where the mark's mass sits: optically a touch high


def nothing_more(bar=34, dash=32, gap=20, top=40, bottom=216, length=54, at=0.56):
    """The slash, `bar` thick across its 60 degree run; the dash, `dash` thick (a horizontal
    looks heavier than a diagonal of the same width, so it is drawn a little thinner),
    `gap` clear of the slash and cut parallel to it, a little below half height, as written."""
    tan = math.tan(math.radians(60))
    run, w = (bottom - top) / tan, bar / math.sin(math.radians(60))
    x0 = 0
    slash = Polygon([(x0, bottom), (x0 + w, bottom), (x0 + w + run, top), (x0 + run, top)])

    def edge(y):                                   # the slash's right edge at height y
        return x0 + w + (bottom - y) / tan

    mid = top + (bottom - top) * at
    y1, y2 = mid - dash / 2, mid + dash / 2
    tail = edge(y1) + gap / math.sin(math.radians(60)) + length
    d = Polygon([(edge(y1) + gap / math.sin(math.radians(60)), y1), (tail, y1), (tail, y2),
                 (edge(y2) + gap / math.sin(math.radians(60)), y2)])
    shape = unary_union([slash, d])
    c = shape.centroid
    return affinity.translate(shape, CENTRE[0] - c.x, CENTRE[1] - c.y)


MARK = nothing_more()
# Gold on ink spreads (irradiation): the reversed mark is drawn a touch lighter.
REVERSED = nothing_more(bar=32, dash=30, gap=22)
# The small cut, for 16-48 px: heavier strokes and a wider gap, so the dash stays apart from the slash.
SMALL = nothing_more(bar=46, dash=42, gap=30, top=30, bottom=226, length=60)


def d_of(geom):
    parts = []
    for p in getattr(geom, "geoms", [geom]):
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            parts.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z")
    return " ".join(parts)


# Square-cut capitals: cap height 100, bars 18 - Assurance Console's (brand/make_brand.py), plus T, I and M.
LETTERS = {
    "A": (72, "M0 30 L30 0 H42 L72 30 V100 H54 V70 H18 V100 H0 Z M18 37.5 L36 19.5 L54 37.5 V52 H18 Z"),
    "U": (72, "M0 0 H18 V82 H54 V0 H72 V100 H0 Z"),
    "N": (72, "M0 0 H18 V100 H0 Z M54 0 H72 V100 H54 Z M0 0 H22 L72 100 H50 Z"),
    "C": (68, "M0 0 H68 V18 H18 V82 H68 V100 H0 Z"),
    "E": (60, "M0 0 H60 V18 H18 V41 H52 V59 H18 V82 H60 V100 H0 Z"),
    "O": (76, "M0 0 H76 V100 H0 Z M18 18 H58 V82 H18 Z"),
    "L": (60, "M0 0 H18 V82 H60 V100 H0 Z"),
    "T": (68, "M0 0 H68 V18 H43 V100 H25 V18 H0 Z"),
    "I": (18, "M0 0 H18 V100 H0 Z"),
    "M": (90, "M0 0 H18 L45 27 L72 0 H90 V100 H72 V25.5 L45 52.5 L18 25.5 V100 H0 Z"),
}
TRACK, SPACE = 26, 56
# The T's open lower sides, and the L's and A's open shoulders, leave holes in the even
# tracking: these pairs close up so every gap looks the same.
KERN = {"LT": -14, "TI": -8, "TE": -8, "AT": -10, "TA": -10, "NT": -8}


def word(text, x0, y0, scale=1.0):
    """The text as one path per letter, top-left at (x0, y0); returns (svg, width)."""
    out, x = [], 0.0
    for i, ch in enumerate(text):
        if ch == " ":
            x += SPACE
            continue
        w, d = LETTERS[ch]
        rule = "nonzero" if ch in "NM" else "evenodd"     # their diagonals overlap their stems
        out.append(f'<path fill-rule="{rule}" transform="translate({x0 + x * scale:.1f} {y0:.1f}) scale({scale})" d="{d}"/>')
        x += w + TRACK + KERN.get(text[i:i + 2], 0)
    return "".join(out), (x - TRACK) * scale


def svg(w, h, body, title="Ultimate Accountant"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}">'
            f"<title>{title}</title>{body}</svg>\n")


def mark(fill, geom=MARK, dx=0, dy=0, s=1.0):
    return f'<path fill="{fill}" fill-rule="evenodd" transform="translate({dx} {dy}) scale({s})" d="{d_of(geom)}"/>'


def build(on_dark=False):
    sym, txt, geom = (GOLD, GOLD, REVERSED) if on_dark else (INK, INK, MARK)
    tag = "-reversed" if on_dark else ""
    files = {f"ultimate-accountant-symbol{tag}.svg": svg(256, 256, mark(sym, geom))}
    right = geom.bounds[2]
    # Horizontal: the mark at full height, the name's cap height a little under half of it, centred on the mark.
    name, w = word("ULTIMATE ACCOUNTANT", 0, 0, 0.62)
    x = right + 44
    body = mark(sym, geom) + f'<g fill="{txt}" transform="translate({x:.1f} {CENTRE[1] - 31:.1f})">{name}</g>'
    files[f"ultimate-accountant-horizontal{tag}.svg"] = svg(x + w + 24, 256, body)
    # Stacked: the mark over the name on two lines, all centred.
    l1, w1 = word("ULTIMATE", 0, 0, 0.62)
    l2, w2 = word("ACCOUNTANT", 0, 0, 0.62)
    W = max(w1, w2, 256) + 48
    body = (mark(sym, geom, (W - 256) / 2, 0) + f'<g fill="{txt}" transform="translate({(W - w1) / 2:.1f} 250)">{l1}</g>'
            f'<g fill="{txt}" transform="translate({(W - w2) / 2:.1f} 330)">{l2}</g>')
    files[f"ultimate-accountant-stacked{tag}.svg"] = svg(W, 418, body)
    name, w = word("ULTIMATE ACCOUNTANT", 0, 0, 1.0)
    files[f"ultimate-accountant-wordmark{tag}.svg"] = svg(w + 48, 148, f'<g fill="{txt}" transform="translate(24 24)">{name}</g>')
    return files


def app_tile():
    """The app tile: the family's rounded square in the gold the two apps share, the ink small cut on it."""
    s = 0.794                                      # the mark's share of the tile, as Assurance Console's
    body = (f'<rect width="256" height="256" rx="{TILE_RX}" fill="{GOLD}"/>'
            + mark(INK, SMALL, round((256 - 256 * s) / 2, 2), round((256 - 256 * s) / 2 - 2, 2), s))
    return svg(256, 256, body)


if __name__ == "__main__":
    files = {**build(), **build(on_dark=True),
             "ultimate-accountant-symbol-small.svg": svg(256, 256, mark(INK, SMALL)),
             "ultimate-accountant-app-icon.svg": app_tile()}
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print("\n".join(sorted(files)))
    print("mark", [round(v, 1) for v in MARK.bounds], "small", [round(v, 1) for v in SMALL.bounds])
