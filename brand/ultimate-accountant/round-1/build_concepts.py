"""
Round-1 concepts for the Ultimate Accountant mark: `python build_concepts.py`.

Black on white, 256 grid, in the family's vocabulary (brand/README.md): bars with
flat ends, 45 and 60 degree angles only, no fonts - the name in Assurance Console's
square-cut capitals. Unions are made with shapely (in requirements.txt via the OCR),
whose straight-edged polygons come out exact: no curve is approximated.
"""
import math
from pathlib import Path

from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parent


def d_of(geom):
    """An SVG path for a (multi)polygon, holes included, evenodd."""
    polys = getattr(geom, "geoms", [geom])
    parts = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            parts.append("M" + " L".join(f"{x:.2f} {y:.2f}".replace(".00", "") for x, y in pts) + " Z")
    return " ".join(parts)


def stroke(points, width, extend_start=0.0, extend_end=0.0):
    """A straight-segment stroke: mitred corners, flat ends (optionally pushed out along the line)."""
    pts = [list(p) for p in points]
    for i, j, e in ((0, 1, extend_start), (-1, -2, extend_end)):
        if e:
            dx, dy = pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]
            n = math.hypot(dx, dy)
            pts[i] = [pts[i][0] + dx / n * e, pts[i][1] + dy / n * e]
    return LineString(pts).buffer(width / 2, cap_style="flat", join_style="mitre", mitre_limit=4)


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}">'
            f"<title>{title}</title>{body}</svg>\n")


# ---------------------------------------------------------------- A. Closing Sum
# A sigma - the sum - built from 28-unit bars and a 45 degree chevron, its foot the
# accountant's double rule: the line drawn under a total that is final.
def closing_sum(bar=28, rule=17, gap=11, L=52, R=204, T=40, B=218):
    top = box(L, T, R, T + bar)
    r2 = box(L, B - rule, R, B)
    r1 = box(L, B - rule - gap - rule, R, B - rule - gap)
    inner_top, inner_bot = T + bar, B - 2 * rule - gap
    s = (inner_bot - inner_top) / 2
    cx = L + bar * math.sqrt(2) / 2
    chevron = stroke([(cx, inner_top), (cx + s, inner_top + s), (cx, inner_bot)], bar, extend_start=30, extend_end=30)
    # The arms end inside the bars they meet: never into the double rule's gap.
    chevron = chevron.intersection(box(L, T, R, B - rule - gap))
    body = unary_union([top, r1, r2, chevron]).intersection(box(L, T, R, B))
    return body


# ---------------------------------------------------------------- B. Nothing More
# "/-": what is written after an amount in India so that nothing can be added to it.
# The last word on a figure. A 60 degree slash and a bar, the bar's end cut on the slash's angle.
def nothing_more(bar=34, top=40, bottom=216):
    h = bottom - top
    run = h / math.tan(math.radians(60))
    w = bar / math.sin(math.radians(60))              # the slash's width along a horizontal
    x0 = 48
    slash = Polygon([(x0, bottom), (x0 + w, bottom), (x0 + w + run, top), (x0 + run, top)])
    mid = top + h * 0.56                              # the dash sits a little below centre, as written
    gap = 20
    # The dash's left end runs parallel to the slash, `gap` clear of it.
    def slash_right_x(y):
        return x0 + w + (bottom - y) / math.tan(math.radians(60))
    y1, y2 = mid - bar / 2, mid + bar / 2
    dash = Polygon([(slash_right_x(y1) + gap, y1), (208, y1), (208, y2), (slash_right_x(y2) + gap, y2)])
    return unary_union([slash, dash])


# ---------------------------------------------------------------- C. Initials
# U and A in Assurance Console's square-cut capitals, set tight: the plain, safe option.
# (Sharing one stem read as "LA", then as "Uh": the letter test failed both ways.)
def initials(track=14, width=200):
    from shapely import affinity
    from shapely.geometry import Polygon
    u = Polygon([(0, 0), (18, 0), (18, 82), (54, 82), (54, 0), (72, 0), (72, 100), (0, 100)])
    a = Polygon([(0, 30), (30, 0), (42, 0), (72, 30), (72, 100), (54, 100), (54, 70), (18, 70), (18, 100), (0, 100)])
    a = a.difference(Polygon([(18, 37.5), (36, 19.5), (54, 37.5), (54, 52), (18, 52)]))
    a = affinity.translate(a, 72 + track, 0)
    pair = unary_union([u, a])
    k = width / (144 + track)
    pair = affinity.scale(pair, k, k, origin=(0, 0))
    minx, miny, maxx, maxy = pair.bounds
    return affinity.translate(pair, (256 - maxx) / 2, (256 - maxy) / 2)


# ---------------------------------------------------------------- the name
T_, LETTERS = 18, {
    "A": (72, "M0 30 L30 0 H42 L72 30 V100 H54 V70 H18 V100 H0 Z M18 37.5 L36 19.5 L54 37.5 V52 H18 Z"),
    "U": (72, "M0 0 H18 V82 H54 V0 H72 V100 H0 Z"),
    "N": (72, "M0 0 H18 V100 H0 Z M54 0 H72 V100 H54 Z M0 0 H22 L72 100 H50 Z"),
    "C": (68, "M0 0 H68 V18 H18 V82 H68 V100 H0 Z"),
    "E": (60, "M0 0 H60 V18 H18 V41 H52 V59 H18 V82 H60 V100 H0 Z"),
    "O": (76, "M0 0 H76 V100 H0 Z M18 18 H58 V82 H18 Z"),
    "L": (60, "M0 0 H18 V82 H60 V100 H0 Z"),
    # New for this name, in the same vocabulary: 18-unit bars, 45 degrees.
    "T": (68, "M0 0 H68 V18 H43 V100 H25 V18 H0 Z"),
    "I": (18, "M0 0 H18 V100 H0 Z"),
    "M": (90, "M0 0 H18 L45 27 L72 0 H90 V100 H72 V25.5 L45 52.5 L18 25.5 V100 H0 Z"),
}
TRACK, SPACE = 26, 56


def word(text, scale):
    out, x = [], 0.0
    for ch in text:
        if ch == " ":
            x += SPACE
            continue
        w, d = LETTERS[ch]
        rule = "nonzero" if ch == "N" else "evenodd"
        out.append(f'<path fill-rule="{rule}" transform="translate({x * scale:.1f} 0) scale({scale})" d="{d}"/>')
        x += w + TRACK
    return "".join(out), (x - TRACK) * scale


def main():
    concepts = {"a-closing-sum": closing_sum(), "b-nothing-more": nothing_more(), "c-initials": initials()}
    name, w = word("ULTIMATE ACCOUNTANT", 0.62)
    for slug, geom in concepts.items():
        d = d_of(geom)
        (OUT / f"{slug}-symbol.svg").write_text(
            svg(256, 256, f'<path fill="#000" fill-rule="evenodd" d="{d}"/>', f"Ultimate Accountant - {slug}"), encoding="utf-8")
        body = f'<path fill="#000" fill-rule="evenodd" d="{d}"/><g fill="#000" transform="translate(268 97)">{name}</g>'
        (OUT / f"{slug}-lockup.svg").write_text(svg(268 + w + 24, 256, body, "Ultimate Accountant"), encoding="utf-8")
        print(slug, f"{len(d)} chars, bounds {tuple(round(v) for v in geom.bounds)}")


if __name__ == "__main__":
    main()
