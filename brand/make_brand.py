"""
The Assurance Console mark and lockups, drawn as paths: `python brand/make_brand.py`.

The mark ("Agreed"): an equals sign whose lower bar ticks itself - the two
sides of a reconciliation agree. Built on a 256 grid from 30-unit bars, flat
ends and 45° only. The name is lettered in the same vocabulary (square-cut
capitals from 18-unit bars), so no font licence is involved and every file is
outlines. Variants, icons and the board come from the logo-design skill's
scripts (see brand/README.md).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent
MAROON, GOLD, INK = "#74191F", "#E9C46A", "#1C1E23"

# The mark, centred (optically a touch high) on the 256 canvas.
MARK = ("M35.5 60 H159.5 V90 H35.5 Z "
        "M35.5 126 H113.5 L135.5 148 L199.5 84 L220.5 105 L135.5 190 L101.5 156 H35.5 Z")
# The small cut, for 16-48 px: bars 40 thick, the gap opened, the tick shortened.
SMALL = ("M28 50 H160 V92 H28 Z "
         "M28 138 H110 L132 160 L198 94 L228 124 L132 220 L96 184 H28 Z")

# Square-cut capitals: cap height 100, bars 18. (width, path) with the origin top-left.
T, H = 18, 100
LETTERS = {
    "A": (72, "M0 30 L30 0 H42 L72 30 V100 H54 V70 H18 V100 H0 Z M18 37.5 L36 19.5 L54 37.5 V52 H18 Z"),
    "S": (68, "M0 0 H68 V18 H18 V41 H68 V100 H0 V82 H50 V59 H0 Z"),
    "U": (72, "M0 0 H18 V82 H54 V0 H72 V100 H0 Z"),
    "R": (72, "M0 0 H72 V70 H18 V100 H0 Z M18 18 H54 V52 H18 Z M34 70 H58 L72 84 V100 H64 Z"),
    "N": (72, "M0 0 H18 V100 H0 Z M54 0 H72 V100 H54 Z M0 0 H22 L72 100 H50 Z"),
    "C": (68, "M0 0 H68 V18 H18 V82 H68 V100 H0 Z"),
    "E": (60, "M0 0 H60 V18 H18 V41 H52 V59 H18 V82 H60 V100 H0 Z"),
    "O": (76, "M0 0 H76 V100 H0 Z M18 18 H58 V82 H18 Z"),
    "L": (60, "M0 0 H18 V82 H60 V100 H0 Z"),
}
TRACK, SPACE = 26, 56


def word(text, x0, y0, scale=1.0):
    """The text as one evenodd path per letter, top-left at (x0, y0); returns (svg, width)."""
    out, x = [], 0.0
    for ch in text:
        if ch == " ":
            x += SPACE
            continue
        w, d = LETTERS[ch]
        rule = "nonzero" if ch == "N" else "evenodd"       # N's diagonal overlaps its stems
        out.append(f'<path fill-rule="{rule}" transform="translate({x0 + x * scale:.1f} {y0:.1f}) scale({scale})" d="{d}"/>')
        x += w + TRACK
    return "".join(out), (x - TRACK) * scale


def svg(w, h, body, title="Assurance Console"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}">'
            f"<title>{title}</title>{body}</svg>\n")


def mark(fill, dx=0, dy=0, s=1.0, d=MARK):
    return f'<path fill="{fill}" transform="translate({dx} {dy}) scale({s})" d="{d}"/>'


def build(on_dark=False):
    sym, txt = (GOLD, GOLD) if on_dark else (MAROON, INK)
    tag = "-reversed" if on_dark else ""
    files = {}
    files[f"assurance-console-symbol{tag}.svg"] = svg(256, 256, mark(sym))
    # Horizontal: the mark at full height, the name's cap height a little under the mark's inner gap.
    name, w = word("ASSURANCE CONSOLE", 0, 0, 0.62)
    body = mark(sym) + f'<g fill="{txt}" transform="translate(268 97)">{name}</g>'
    files[f"assurance-console-horizontal{tag}.svg"] = svg(268 + w + 24, 256, body)
    # Stacked: the mark over the name on two lines, all centred.
    l1, w1 = word("ASSURANCE", 0, 0, 0.62)
    l2, w2 = word("CONSOLE", 0, 0, 0.62)
    W = max(w1, w2, 256) + 48
    body = (mark(sym, (W - 256) / 2, 0) + f'<g fill="{txt}" transform="translate({(W - w1) / 2:.1f} 262)">{l1}</g>'
            f'<g fill="{txt}" transform="translate({(W - w2) / 2:.1f} 342)">{l2}</g>')
    files[f"assurance-console-stacked{tag}.svg"] = svg(W, 430, body)
    name, w = word("ASSURANCE CONSOLE", 0, 0, 1.0)
    files[f"assurance-console-wordmark{tag}.svg"] = svg(w + 48, 148, f'<g fill="{txt}" transform="translate(24 24)">{name}</g>')
    return files


if __name__ == "__main__":
    files = {**build(), **build(on_dark=True),
             "assurance-console-symbol-small.svg": svg(256, 256, mark(MAROON, d=SMALL))}
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print("\n".join(sorted(files)))
