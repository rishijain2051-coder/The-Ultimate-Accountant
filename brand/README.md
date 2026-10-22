# Assurance Console - the logo

**Agreed:** an equals sign whose lower bar ticks itself. The two sides of a
reconciliation agree, and the auditor's tick says so. It is built on a 256
grid from 30-unit bars, with flat ends and 45° angles only. The name is lettered
in the same vocabulary, as square-cut capitals made of 18-unit bars. Every file
is outlines: no font is needed and no licence is involved.

`python brand/make_brand.py` draws the masters. The variants, icons and board
come from the logo-design skill (github.com/kaankiziltug/logo-design-skill):
`export_variants.py` and `presentation_board.py board-spec.json`.

## Files

| File | Use |
|---|---|
| `assurance-console-symbol.svg` | The mark, maroon, for light backgrounds |
| `assurance-console-symbol-reversed.svg` | The mark, gold, on maroon |
| `assurance-console-symbol-small.svg` | The small cut for 16-48 px: heavier bars and a wider gap |
| `assurance-console-horizontal(-reversed).svg` | Mark and name on one line: headers, documents |
| `assurance-console-stacked(-reversed).svg` | Mark over the name: covers, square spaces |
| `assurance-console-wordmark(-reversed).svg` | The name alone |
| `symbol/`, `horizontal/`, `stacked/`, `wordmark/` | Black, white and one-colour versions, as SVG and PNG |
| `symbol/*-app-icon.svg`, `favicon.*`, `icon-*.png`, `site.webmanifest`, `head-snippet.html` | App icon and web icons |
| `board/` | The presentation board (HTML) and its slides (PNG) |

In the app: `app/frontend/assets/favicon.svg` is the app icon's tile, and
`app/frontend/assets/icon.ico` (16-256 px) is the Windows icon. The exe carries
that icon, so it arrives with the full installer, not with a light update. The cover
band draws the mark in gold before the wordmark.

## Colour

| | HEX | RGB | Where |
|---|---|---|---|
| Cloth-cover maroon | `#74191F` | 116 25 31 | The mark on light; the tile and cover behind the gold |
| Turmeric gold | `#E9C46A` | 233 196 106 | The mark and name on maroon |
| Ink | `#1C1E23` | 28 30 35 | The name on light |

Contrast: maroon on white 11.0:1, gold on maroon 6.6:1, ink on white 16.7:1.
Gold on white is only 1.7:1, so never put the gold mark on white or light grey.
In one colour, use black or white.

## Rules

- **Clear space:** keep a margin of at least the upper bar's height (30 of the
  256 units) on every side, free of text and edges.
- **Minimum sizes:** below 48 px, use the small cut. It works down to 16 px. Keep the horizontal
  lockup at least 140 px wide, or 32 mm in print, and use the mark alone below that.
- **Backgrounds:** maroon on white or the page wash (`#F5F6F8`); gold on maroon; black or white on
  photographs, in one colour.
- **Don't:** recolour the bars separately, add a shadow or gradient, round the ends, rotate or
  mirror the mark (mirrored, the tick reads backwards), set the name in another typeface, or
  put the tick inside a circle or a shield.

- **Family:** Invoice Bridge (Counterfoil, in its own `brand/`) is ink and gold; the
  Console is maroon and gold. Both share the tile and the gold. Neither mark goes on the other's
  ground.

## Notes

- The board's business card and social profile use placeholder names and addresses, for the mockup only.
- Search the trademark registers before using this outside the app. The logo library was only
  used to check that the mark is not like any logo in it.
