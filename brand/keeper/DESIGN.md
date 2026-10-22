---
name: Keeper
description: The office's books from Tally, on your phone. Black, white and grey, with Tally's yellow bar on the row you are on.
colors:
  ink: "#111111"
  ink-2: "#3f3f46"
  ink-3: "#71717a"
  ground: "#f4f4f5"
  card: "#ffffff"
  line: "#e4e4e7"
  line-2: "#d4d4d8"
  tally-bar: "#ffe27a"
  bar-ink: "#111111"
  bar-ink-quiet: "#3f3a1e"
  overdue: "#c62828"
  overdue-soft: "#fdecea"
  overdue-on-ink: "#ffb4ab"
  ok: "#2e7d32"
  stale: "#f59e0b"
typography:
  verdict:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  figure:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    letterSpacing: "-0.015em"
    fontFeature: "tnum"
  headline:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
  small:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
  key:
    fontFamily: "ui-monospace, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1
rounded:
  card: "16px"
  control: "12px"
  nav: "10px"
  key: "8px"
  kbd: "5px"
  pill: "999px"
  round: "50%"
spacing:
  hair: "6px"
  gap: "10px"
  gutter: "12px"
  row: "14px"
  page: "16px"
  page-desk: "28px"
components:
  button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 18px"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    rounded: "{rounded.control}"
    padding: "11px 18px"
  balance-block:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    rounded: "{rounded.card}"
    padding: "18px"
  tile:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "14px 16px"
  tile-on:
    backgroundColor: "{colors.tally-bar}"
    textColor: "{colors.bar-ink}"
  entry-row:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    padding: "12px 14px"
  entry-row-on:
    backgroundColor: "{colors.tally-bar}"
    textColor: "{colors.bar-ink}"
  avatar:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.round}"
    size: "36px"
  avatar-overdue:
    backgroundColor: "{colors.overdue-soft}"
    textColor: "{colors.overdue}"
    rounded: "{rounded.round}"
    size: "36px"
  chip:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "7px 14px"
  chip-current:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    rounded: "{rounded.pill}"
    padding: "7px 14px"
  field:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 14px"
  count-badge:
    backgroundColor: "{colors.overdue}"
    textColor: "{colors.card}"
    height: "18px"
  key-button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.key}"
    padding: "7px 10px"
---

# Design System: Keeper

Keeper is the read-only books viewer (control-plane/public/books.html, .css, .js) and the Keeper page on the office server (app/keeper/page.py). It is a separate world from Assurance Console: the root DESIGN.md is the Console's and does not apply here.

## Overview

**Creative North Star: "Your Tally books, in your pocket"**

A payments-app account view of Tally's books, done properly. It is black, white and zinc grey, and has one colour of its own: Tally's yellow bar, which sits on the row you are on and moves with the arrow keys. Each screen shows the answer first (who owes what, who is late) and the detail only when you open it.

It is dense but calm: white rounded cards on a light grey ground, hairline dividers, bold tabular figures, and initials in grey circles. There are no gradients, no textures and no decoration. It does not use the Console's ledger paper and maroon cloth.

**Key Characteristics:**
- Black, white and grey; one yellow, one red, each with a single job.
- System sans with tabular figures everywhere, so amounts line up.
- Rounded white cards with hairlines, pill chips, a black balance block.
- Phone first: one column and a bottom tab bar. Desktop: sidebar, content and Tally's key bar.
- Keyboard first on desktop: the yellow bar moves with the arrows.

## Colors

Zinc greys do almost all the work. Yellow and red are both rare, and that rarity is how they get noticed.

### Primary
- **Tally Bar Yellow** (tally-bar): the row, tile, balance half or key-bar button you are on (keyboard `.on`, pointer press, finger press), and text selection. Text on it is ink, and quiet text on it is bar-ink-quiet.

### Secondary
- **Overdue Red** (overdue): overdue only. Used for the red ring and initials on a late party's avatar, the count on the Dues tab, late amounts (`t-look`, `t-serious`), and serious notices (on overdue-soft). On the black balance block it becomes overdue-on-ink.

### Neutral
- **Ink** (ink): text, the balance block, primary buttons, the current chip, focus rings and checkbox accent.
- **Ink 2 / Ink 3** (ink-2, ink-3): secondary text, then quiet text (labels, dates, details, inactive tabs).
- **Ground** (ground): the page, and also the hover tint, avatar fill, total rows and the picker pill.
- **Card** (card): every container surface.
- **Hairline / Hairline 2** (line, line-2): card borders and row dividers, then chevrons, kbd borders and the scrollbar.
- **Status greens and ambers** (ok, stale): only the freshness dot ("read N min ago") and the office page's status dot.

### Named Rules
**The One Bar Rule.** Tally yellow means "you are here" and nothing else. A plain hover on a list row is ground grey, not yellow. Yellow appears on the row only when it becomes current (arrow keys, press). It is never used for a status, a badge or decoration.

**The Red Means Late Rule.** Red is for overdue only. Errors on the office page use the same red (`--bad`) because they are the same kind of alarm. Red is never used for emphasis, brand or negative amounts in general.

## Typography

**Body Font:** system-ui (with -apple-system, Segoe UI, Roboto, Noto Sans, Nirmala UI, sans-serif)
**Key Font:** ui-monospace (with Consolas), used only inside key caps.

**Character:** The phone's own face, like the payments apps it sits beside. Hierarchy comes from weight (400 / 600 / 700) and size, never from a second family. `font-variant-numeric: tabular-nums` is set on the body.

### Hierarchy
- **Verdict** (700, 1.5rem, 1.75rem at 1000px+, line-height 1.2, -0.015em): one per screen. It is the company name or the answer ("Sales 12: ₹4,200.").
- **Figure** (700, 1.5rem, -0.015em): the two balance amounts. Tile figures are 1.125rem 700 and row amounts are 1rem 700.
- **Headline** (700, 1rem): section heads such as "Latest" and "Items". They are sentence case and are not set in small caps or uppercase.
- **Body** (400, 1rem, 1.45): row titles are set at 600.
- **Small** (0.8125rem, ink-3): row details, dates, tile and balance labels, field labels. Sub-notes are 0.75rem.
- **Tab label** (600, 0.6875rem on the phone tab bar, 0.9375rem in the sidebar).
- **Key cap** (700, 0.75rem mono).

**The Weight Not Family Rule.** Use one sans family. Make things more important with weight, and quieter with ink-3.

## Layout

- **Phone (default):** one column. The content has 16px padding and children are capped at 760px. A sticky top bar sits above and a fixed bottom tab bar below, with the body padded `72px + safe-area` underneath it. Safe-area insets are respected at the top and bottom.
- **700px and up:** the three Summary tiles sit on one row (on phones, cash and bank spans the full width above the other two).
- **1000px and up:** a three-column frame (220px sidebar, flexible content, 190px key bar), capped at 1280px. The sidebar and key bar are sticky under the 61px top bar and full height. Content padding becomes 22px 28px 48px.
- **Office page:** one 760px column with 28px 20px padding, and 20px 16px under 700px.
- **Rhythm:** 6 / 10 / 12 / 14 / 16px. Cards stack with 10–12px gaps. Section heads sit 26px above their list.
- **Print:** the top bar, tabs, key bar, filters, search and chevrons are hidden. The page prints white.

## Elevation & Depth

The system is flat. Depth comes from white cards with 1px hairlines on a grey ground. The only blur is on the top bar, which is 94% white with `saturate(180%) blur(12px)` and turns solid white under `prefers-reduced-transparency`. Inset rings are used as outlines, not shadows: the 2px red ring on an overdue avatar and the 1px hairline ring on the current sidebar item. No drop shadows are used.

**The Hairline Not Shadow Rule.** Separate things with a 1px line (line), not a shadow.

## Shapes

Corners are soft, and they get smaller as the thing gets smaller: cards and lists 16px, tiles, fields, buttons and notices 12px, sidebar items 10px, key buttons 8px, key caps 5px. Chips, the company picker and the count badge are pills. Avatars and status dots are circles. Row dividers start 14px in from the left and run to the right edge.

## Components

### Top bar
It is sticky, frosted white and has a bottom hairline. On the left is the brand (the 30px favicon-variant mark, radius 8px, then "Keeper" at 700, 1.0625rem). On the right is the company picker, a ground-grey pill holding a native `<select>` laid invisibly over it, a chevron, and the name, which truncates at 58vw.

### Balance block
A black card (balance-block) split in two halves, "You'll get" and "You'll give", with a 16% white rule between them. Each half is a link and shows a label (66% white), the figure, and an overdue line underneath ("₹X overdue" in overdue-on-ink, otherwise "nothing overdue").

### Tiles
White cards with a hairline. Each has a small ink-3 label and a 1.125rem bold figure. They are links and take the yellow bar when current.

### Entries and ledger cards
A white 16px card holding rows. An entry row is a grid of avatar 36px / what (bold title, small detail) / amount (bold, right, optional small note) / chevron. Rows without a party drop the avatar column. Ledger lines have two columns (text, money). Sub lines are indented 28px in ink-2, and total rows are ground grey and bold. States: hover is ground, current or pressed is yellow with ink text. Background changes take 80ms, with none under reduced motion. The empty state is a single card with ink-3 text.

### Initials avatar
A 36px circle in ground grey holding up to two initials in ink-2 (700, 0.8125rem, +0.02em). An overdue party gets an inset 2px red ring, red initials and an overdue-soft fill.

### Chips
Filters in a horizontally scrolling row with the scrollbar hidden. A chip is a white pill with a hairline in ink-2 at 500. The current chip is black with white text at 600. "Other dates" (`details.more`) uses the same pill shape and opens a fields card.

### Buttons
The default is white with a hairline, 600, 11px 18px, radius 12px. Primary is black with white text. Pressed scales to 0.98 (off under reduced motion). Disabled is at 45% opacity. Fields match them: a 12px radius, a hairline, and a 2px ink outline on focus.

### Notices
12px radius and 12px 14px padding. A plain notice uses a one-off pale yellow (#fff8db), which is not a token, and a serious one uses overdue-soft.

### Bottom tab bar (phone)
Fixed, white, with a top hairline. It has five equal tabs: Summary, Dues, Transactions, Accounts, Alerts. Each tab has a 24px stroked line icon (1.8 stroke, round caps, drawn inline in books.js) over an 11px label. The inactive tab is ink-3 and the current one is ink. Dues carries a red count pill (18px, white figure) showing the number of overdue parties.

### Sidebar (1000px+)
The same tabs become a vertical list with 20px icons, 0.9375rem labels in ink-2, 10px 12px padding and a 10px radius. Hover is white. The current item is white with a hairline ring and ink text. The count sits at the right end.

### Key bar (1000px+)
A right-hand column headed "Keys" (0.75rem 700 ink-3), listing Tally's own keys one per row: a white button with a hairline and an 8px radius holding a key cap (mono, ground fill, line-2 border) and the action. Hover is yellow. The footnote reads "↑ ↓ move the yellow bar, Enter opens."

### Office Keeper page (app/keeper/page.py)
Same tokens. It has a white top bar with a 28px mark and "Keeper", and a "Back to Ultimate Accountant" pill. The h1 is led by an 11px status dot: green when up to date, amber when not checked, red when not up to date, and a pulsing dot while busy. Below are rows cards (Companies, Backups), primary and plain buttons, `details` with a mono `pre`, and fields capped at 480px.

## Keyboard grammar

This applies on the desktop. Shortcuts are ignored while Alt, Ctrl or Meta is held.
- **↑ / ↓** move the yellow bar through the rows, tiles and balance halves on the screen, stopping at the ends. It scrolls into view.
- **Enter** opens the current row. **Esc** leaves a field, or goes back (it does nothing on Summary).
- **S D T A L** go to Summary, Dues, Transactions, Accounts and Alerts. **/** goes to Find. **F2** sets the period (opens Other dates or goes to the chips). **F3** goes to the company picker.
- While you are typing in a field, only Esc, F2 and F3 act.
- Every key is also a button in the key bar. A new key goes into `KEYS` and the bar together.

## Logo

The mark is a K whose arm is a key: the stem and leg are drawn as thick round-capped strokes, and the upper arm is the key's shank with a round head.

| File | Use | Key grey | Key hole |
|---|---|---|---|
| keeper-mark.svg | on white or light, no tile; K in #111 | #8C8C8C | white |
| keeper-mark-reversed.svg | on #111 or dark; K in white | #A6A6A6 | #111 |
| keeper-icon.svg | rounded #111 tile (rx 58/256), mark at 80% | #A6A6A6 | #111 |
| keeper-icon-square.svg | full-bleed square tile, for platforms that round their own corners | #A6A6A6 | #111 |
| keeper-icon-maskable.svg | full-bleed, mark at 70% inside the maskable safe zone | #A6A6A6 | #111 |
| keeper-favicon.svg | rounded tile, mark at 90%, for 32px and below | #B3B3B3 | none |
| keeper-lockup.svg / -reversed.svg | icon plus "Keeper" (700, system-ui, -3 tracking), gap 48/256 of the icon | #A6A6A6 | #111 |

- **Clear space:** keep at least one key-head diameter (about a quarter of the mark's height) free on every side. In the lockup, the gap between icon and word is fixed. Do not tighten it.
- **Minimum sizes:** the favicon variant covers 16–32px, including the 28–30px top-bar use, and has no key hole because the hole fills in at 16px. Its key grey is lighter (#B3B3B3) so it still reads. The icon and the mark with the hole are used from 40px up.
- **Greys per variant:** #8C8C8C on light, #A6A6A6 on dark, #B3B3B3 for the favicon. Do not swap them between variants.
- **Don't:** recolour the key yellow or red, add a key hole to the favicon, use the light mark on dark or the reversed mark on mid-grey, mirror, rotate, stretch, outline or shadow it, set the wordmark in another face or weight, or put the mark on a tile of any colour except #111.

**Raster provenance.** Every PNG and the ICO were rendered from the SVGs in brand/keeper with headless Chrome. Do not redraw or edit them by hand: re-render from the SVG instead. The files are:
- keeper-*-1024.png and keeper-lockup-860.png, rendered from their namesake SVGs.
- apple-touch-icon.png (180), icon-192.png and icon-512.png, rendered from keeper-icon-square.svg (opaque corners).
- icon-maskable-512.png, rendered from keeper-icon-maskable.svg.
- favicon.ico (16/32/48), rendered from keeper-favicon.svg.

The copies in control-plane/public are byte-identical to brand/keeper. control-plane/public/favicon.svg is keeper-favicon.svg, and app/keeper/page.py embeds the same SVG as base64. test-sheet.html/.png is the check sheet showing the mark at sizes, on grey, mirrored and squinted.

## Do's and Don'ts

### Do:
- **Do** put Tally yellow (tally-bar) only on the thing you are on, and on text selection.
- **Do** keep red (overdue) for overdue and errors, and show lateness on the avatar ring and the Dues count.
- **Do** set every amount in bold tabular figures, aligned right in rows.
- **Do** put lists in white 16px cards with hairline dividers inset 14px.
- **Do** give every new navigable row the `.on` state and add it to the rows `moveBar` walks.
- **Do** list every new desktop shortcut in the key bar.

### Don't:
- **Don't** use yellow for hover on rows, for statuses, badges or decoration.
- **Don't** add drop shadows, gradients or a second typeface.
- **Don't** bring in the Console's ledger paper, maroon or serif type.
- **Don't** put a small label above a heading. The verdict stands alone and says the answer.
- **Don't** use emoji or text glyphs as icons. Icons are inline stroked SVG at 1.8 weight.
