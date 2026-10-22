# Ultimate Accountant - the logo

**Nothing More:** `/-`, what is written after an amount in India so that nothing can be added to it
(`₹4,105/-`). The last word on a figure, as the books should be when they tie. It also half-reads as an A.

It is a 60° slash and a bar, with flat ends. The bar's end is cut parallel to the slash, and the bar is
drawn a little thinner than the slash, since a horizontal looks heavier than a diagonal of the same width. It
sits a little below half height, as it is written, and the mark is centred on its weight, slightly above the
canvas's middle. The name is lettered in Assurance Console's square-cut capitals, with a T, an I and an M drawn
for it and the T's open sides kerned. Every file is outlines: no font is needed and no licence is involved.

It is the parent of a family, so it keeps the family's rules: the same app tile as Assurance Console's
"Agreed" (maroon and gold) and Invoice Bridge's "Counterfoil" (ink and gold). The tile is in the turmeric gold
both of them share, with the mark in ink on it.

`python brand/ultimate-accountant/make_brand.py` draws the masters. The variants, icons and board come from
the logo-design skill (github.com/kaankiziltug/logo-design-skill): `export_variants.py`, and
`presentation_board.py board-spec.json`. The three round-1 concepts are in `round-1/`.

## Files

| File | Use |
|---|---|
| `ultimate-accountant-symbol.svg` | The mark, in ink, for light backgrounds |
| `ultimate-accountant-symbol-reversed.svg` | The mark in gold, for ink or dark backgrounds (drawn a touch lighter) |
| `ultimate-accountant-symbol-small.svg` | The small cut for 16-48 px: heavier strokes and a wider gap, so the bar stays clear of the slash |
| `ultimate-accountant-app-icon.svg` | The app tile: the ink small cut on gold |
| `ultimate-accountant-horizontal(-reversed).svg` | Mark and name on one line: headers, documents, the start screen |
| `ultimate-accountant-stacked(-reversed).svg` | Mark over the name: covers, square spaces |
| `ultimate-accountant-wordmark(-reversed).svg` | The name alone |
| `symbol/`, `horizontal/`, `stacked/`, `wordmark/` | Black, white and one-colour (ink, and gold for the symbol) versions, as SVG and PNG; `symbol/` also holds the app icon and the web icons (`favicon.ico`, `favicon.svg`, `icon-*.png`, `maskable-512.png`, `site.webmanifest`, `head-snippet.html`) |
| `board/` | The presentation board (HTML) and its slides (PNG) |

**In the app:**
- `app/icon.ico` (16-256 px, drawn from the tile at each size) is the Windows icon: the app window and the
  taskbar (`launch.py`), the exe, `Install.exe`, `Update.exe` and `Uninstall.exe` (`build.spec`,
  `packaging/*.spec`). It arrives with the full installer, not with a light update.
- The start screen (`app/home.html`) shows the horizontal lockup in its header (ink on light, gold in dark
  mode) and has the tile as its favicon.
- Assurance Console and Invoice Bridge keep their own marks on their own screens.

## Colour

| | HEX | RGB | Where |
|---|---|---|---|
| Turmeric gold | `#E9C46A` | 233 196 106 | The tile; the mark and name on dark. Shared with Assurance Console and Invoice Bridge |
| Ink | `#1C1E23` | 28 30 35 | The mark and name on light and on the gold tile. Invoice Bridge's ink, and Assurance Console's name ink |
| Paper | `#F4F2ED` | 244 242 237 | The start screen's page |

Contrast: ink on gold is 10.0:1, ink on white 16.7:1, ink on paper 14.9:1, and gold on ink 10.0:1. Gold on
white is only 1.7:1, so never put the gold mark on white or paper. In one colour, use black or white.

## Rules

- **Clear space:** keep a margin of at least the bar's height (32 of the 256 units, an eighth of the canvas)
  on every side, free of text and edges.
- **Minimum sizes:** below 48 px, use the small cut. It holds down to 16 px. Keep the horizontal lockup at
  least 180 px wide, or 40 mm in print, and use the mark alone below that.
- **Backgrounds:** the ink mark on white, paper or the gold tile; the gold mark on ink or near-black; black or
  white on photographs, in one colour.
- **Family:** the parent is gold and ink. Don't put its mark on Assurance Console's maroon, and don't put the
  two siblings' marks on its gold.
- **Don't:**
  - colour the slash and the bar differently, or close the gap between them;
  - put the bar level with the slash's middle, or on its left: it is written low and after;
  - add a shadow, outline or gradient;
  - rotate or mirror the mark (mirrored, it reads `-\`);
  - set the name in another typeface, or type it instead of using the outlines.

## Notes

- The board's business card, social profile and website use placeholder names and an address, for the
  mockup only.
- Search the trademark registers before using this outside the app. The logo library was only used to
  check that the mark is not like any logo in it.
