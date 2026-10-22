---
name: Assurance Console
description: A reconciliation written as a bahi-khata page - one plain sentence per open entry, in the auditor's three colours.
colors:
  cover: "#74191f"
  cover-deep: "#58121a"
  gold: "#e9c46a"
  gold-ink: "#9a6a00"
  paper: "#ffffff"
  wash: "#f5f6f8"
  rule: "#dfe5ec"
  rule-strong: "#c3cdd8"
  margin-rule: "#e6aeb3"
  ink: "#1c1e23"
  ink-2: "#454a54"
  ink-3: "#626773"
  ok: "#1f7a4d"
  ok-wash: "#e8f3ec"
  look: "#c98a1c"
  look-ink: "#875800"
  look-wash: "#fcf3e0"
  serious: "#b42318"
  serious-wash: "#fbebe9"
typography:
  verdict:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "2.125rem"
    fontWeight: 700
    lineHeight: 1.16
    letterSpacing: "-0.012em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 700
    lineHeight: 1.25
    fontFeature: "tnum"
  title:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    fontFeature: "tnum"
  lede:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "tnum"
  body:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "tnum"
  label:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    fontFeature: "tnum"
  wordmark:
    fontFamily: "Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial, Noto Sans, Nirmala UI, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    letterSpacing: "0.16em"
rounded:
  none: "0px"
  letter: "2px"
  tab: "3px"
  control: "4px"
  dot: "50%"
spacing:
  cover-h: "56px"
  margin-col: "216px"
  sheet-x: "56px"
  sheet-top: "44px"
  sheet-measure: "940px"
  block-gap: "64px"
  row-y: "13px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-hover:
    backgroundColor: "{colors.wash}"
  button-primary:
    backgroundColor: "{colors.cover}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.cover-deep}"
  button-small:
    rounded: "{rounded.control}"
    padding: "6px 12px"
    typography: "{typography.label}"
  button-cover-gold:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.cover-deep}"
    rounded: "{rounded.control}"
    padding: "7px 14px"
  link:
    textColor: "{colors.cover}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
  cover-band:
    backgroundColor: "{colors.cover}"
    textColor: "{colors.paper}"
    height: "{spacing.cover-h}"
    padding: "0 24px"
  entry-line:
    textColor: "{colors.ink}"
    padding: "13px 6px"
  toast:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
---

# Design System: Assurance Console

## Overview

**Creative North Star: "The Bahi-Khata Page"**

The app is a red cloth-bound ledger book opened on a desk. A deep maroon cover band with turmeric-gold lettering runs across the top; beneath it lies one white ruled page, split by a double rose margin rule into a narrow margin column and the sheet. Everything on the sheet is written, not charted: a verdict sentence, a lede with the counts inline, ruled ledger tables whose totals carry the accountant's single-over-double rule, and a list of open entries where each line states in one plain sentence why it is still open.

Colour is disciplined. Maroon and gold belong to the binding (cover band, primary action, the "you are here" marker). Everything else on the page is ink on paper, and the only other colours are the auditor's pen: green for settled, amber for needs a look, red for money at risk. One light theme only, chosen for daylight offices and printed pages. The type is the system sans with tabular figures everywhere, because the product commits to shipping no web fonts (PRODUCT.md, "Offline and light").

The page is dense but calm: rows are separated by hairline rules rather than boxed, the sheet has a readable measure (940px), and the page prints cleanly with the chrome removed.

**Key Characteristics:**
- Maroon cover band, gold wordmark, white ruled page, double rose margin rule.
- Verdict first: every part opens with one sentence that states the outcome.
- Three-colour auditor's pen (green, amber, red) with the key always visible.
- Hairline rules, not cards; ledger totals with a rule above and a double rule below.
- The auditor's tick: a drawn green tick and a struck-through line when an entry is marked done.
- Words, not icons: every control is a labelled button or link.

## Colors

A binding of maroon and gold around a white page, with colour on the page reserved for the auditor's pen.

### Primary
- **Cloth Cover Maroon** (`cover`): the sticky cover band, primary buttons, links, the focus ring, form accent colour, the top edge of menus and the working overlay. Hover deepens to **Cover Shadow Maroon** (`cover-deep`), which is also the text colour on gold.

### Secondary
- **Turmeric Gold** (`gold`): the wordmark, the "Start a new check" button in the cover, the current step's circle on the start screen, the pulsing current stage in the working overlay, and the skip link.
- **Ribbon Gold** (`gold-ink`): the darker gold used where gold must read on white: the silk-ribbon bookmark on the current part tab and the bar on the active sub-filter.

### Tertiary: the auditor's pen
- **Settled Green** (`ok`, wash `ok-wash`): the drawn tick, settled counts, figures that tie, a filled upload blank, "done" states.
- **Needs-a-Look Amber** (`look`): the 10px dot marking an open entry. Text in amber always uses **Amber Ink** (`look-ink`) for contrast; `look-wash` backs the "adding to this run" notice.
- **Serious Red** (`serious`, wash `serious-wash`): the dot for money at risk, amounts that do not tie, the serious toast, destructive links.

### Neutral
- **Page White** (`paper`): the page, inputs, buttons, overlays.
- **Blotter Wash** (`wash`): hover and selected row backgrounds, the current part tab.
- **Faint Blue-Grey Ruling** (`rule`): hairlines between rows and lines of the letter; **Strong Ruling** (`rule-strong`) heads a list, borders controls, and draws step circles.
- **Rose Margin Rule** (`margin-rule`): the 3px double rule between margin and sheet, repeated inside the entry list at the date column.
- **Ink** (`ink`), **Second Ink** (`ink-2`), **Pencil Grey** (`ink-3`): primary text, secondary text, and quiet text (dates, counts, captions, struck-through settled entries).

### Named Rules
**The Auditor's Pen Rule.** Green, amber and red mean settled, needs a look and serious, and nothing else. They never decorate.

**The Pencil Ring.** The export register has a fourth state the pen has no colour for: not due yet, or a document still to come. It is a hollow pencil-grey ring, not a colour, and it says nothing is wrong. It appears only where the register's stages do, and the colour key there carries it.

**The Settled-Is-Grey Rule.** An entry the user has ticked off counts as settled: its reason is struck through in pencil grey and its amount turns grey. The verdict counts only what is left to do.

**The Binding Rule.** Maroon and gold belong to the book's binding and to "act here / you are here". They are never used to signal a finding.

## Typography

**Display Font:** System sans ("Segoe UI", system-ui, -apple-system, with "Nirmala UI" for Indic scripts)
**Body Font:** the same stack
**Label/Mono Font:** none; labels are the same face, bold and small

**Character:** One plain, familiar face that looks like the rest of the office computer, with tabular numerals everywhere so rupee columns line up. Hierarchy comes from size and weight, not from a second family.

### Hierarchy
- **Verdict** (700, 2.125rem / 34px, 1.16, -0.012em; 1.625rem under 900px): the one sentence opening each part and each screen, max 24ch.
- **Headline** (700, 1.375rem, 1.25): section heads on the sheet ("Needs a look", "All entries", "Downloads"), with a small green done-count beside them.
- **Title** (700, 1.125rem): sub-sections and the working overlay heading.
- **Lede** (400, 1.0625rem, 1.55, second ink): the sentence under the verdict carrying the figures and the coloured counts, max 66ch.
- **Body** (400, 0.9375rem, 1.55): everything else; explanation text runs to 70ch.
- **Label** (700, 0.8125rem, pencil grey): margin headings, field labels, table headers, the "why" terms in an opened entry. Sentence case, never uppercase.
- **Wordmark** (700, 13px, 0.16em, uppercase, gold): the only uppercase letterspaced text in the system, and only in the cover band.

### Named Rules
**The Tabular Rule.** `font-variant-numeric: tabular-nums` is set on the body; every figure inherits it. Money sits right-aligned in its own column, bold.

**The Sentence Rule.** The reason for an entry is a full sentence in bold ink, never a code or a status chip.

## Layout

A sticky 56px cover band spans the top. Below, a centred grid up to 1280px wide: a 216px sticky margin column (part tabs with counts, the in-page index, the colour key) separated by the 3px double rose rule from the sheet. The sheet pads 44px top and 56px sides, and its children cap at 940px. Sections are 64px apart; the summary block sits flush and the next block follows at 44px.

Lists are ruled, not boxed: a strong rule heads the list and a faint rule closes each row (row padding about 13px). The open-entries list uses a grid of mark (20px), date (74px), reason, amount, and chevron, with the rose double rule repeated at the date column. An opened entry indents its working under the reason column (128px) as a two-column definition list (132px terms).

At 900px and below the cover wraps (run switcher drops to its own full-width row), the margin collapses to a horizontal row of short part tabs above the sheet with the double rule underneath, the index and hints hide, the colour key moves inline under "Needs a look", and each entry line puts the reason on its own row with date and amount beneath. Print removes the cover, margin, tools and actions, keeps entries unbroken.

## Elevation & Depth

Flat. The page is paper; depth comes from rules and washes. Shadows exist only on things that float above the page, and they are soft and downward.

### Shadow Vocabulary
- **Cover edge** (`box-shadow: inset 0 -1px 0 rgba(233,196,106,.6), 0 1px 0 #58121a`): a gold hairline stitched along the bottom of the cover band.
- **Menu lift** (`box-shadow: 0 10px 24px -10px rgba(28,30,35,.45)`): the run switcher menu.
- **Overlay lift** (`box-shadow: 0 12px 28px -12px rgba(28,30,35,.45)`): the working card.
- **Toast lift** (`box-shadow: 0 14px 32px -14px rgba(0,0,0,.5)`): toasts.

### Named Rules
**The Paper-Is-Flat Rule.** Nothing that sits on the page casts a shadow. Only the menu, the working overlay and toasts float.

## Shapes

Mostly square. Controls and inputs carry a slight 4px corner; the current part tab 3px; the letter 2px. Floating panels (menu, working card) are square with a thick maroon top edge (4px and 6px) like a tab of the cover. The only round forms are the pen's dots (10px), the step circles (26px), and the stages in the working overlay. Upload blanks are dashed until filled, then solid green; the Exports drop zone is a large dashed blank on its start page and a slim one-line blank under a register's figures, since documents arrive for months. The current part is marked by a gold silk ribbon clipped to a swallowtail. Ledger totals close with a 1.5px ink rule above and a 3px double ink rule below.

## Components

### Buttons
Labelled, plain and sure; each says exactly what it does ("Check these files", "Mark as done", "I am chasing this").
- **Shape:** slight corner (4px), 1px strong-ruling border.
- **Default:** white with ink text, weight 600, 9px 16px; hover to blotter wash with a pencil-grey border; pressed nudges down 1px.
- **Primary:** cloth-cover maroon with white text; hover to cover shadow maroon. At most one primary per action group.
- **Small:** 6px 12px at label size, used for entry actions and pagination.
- **Cover gold:** the one gold button in the cover band ("Start a new check"), gold with maroon text.
- **Disabled:** 45% opacity, not-allowed cursor.
- **Link button:** maroon underlined text (1px, thickens to 2px on hover) for secondary actions; red for destructive ones.

### Inputs / Fields
- **Style:** white, 1px strong-ruling border, 4px corner, 8px 10px; hover darkens the border to pencil grey. Labels sit above in bold label type; hints below in pencil grey.
- **Focus:** the global 2px maroon outline, offset 2px (gold inside the cover band).
- **Checks and radios:** native controls tinted maroon via `accent-color`.

### Navigation
- **Cover band:** gold wordmark left, then the two module tabs, the open run's name as a bordered button that opens the run menu, plain white links and the gold button right. The gold button names the module's main act: *Start a new check*, or *Add documents* in Exports.
- **Module tabs:** "Reconciliation" and "Exports" in plain white text; the current one bold with a 3px gold bar along the cover's bottom edge (you are here). Each returns to where it was left. On phones they take a full-width row of their own under the wordmark.
- **Margin tabs:** one row per part with its open count right-aligned; the current part gets the blotter wash, bold text and the gold ribbon. The in-page index below uses plain text buttons that underline on hover; active sub-filters get a 3px gold-ink bar.
- **Mobile:** tabs become a wrapping row of short names; the index hides.

### Colour Key
Always visible: at the foot of the margin on desktop, inline under "Needs a look" on phones. Three rows: tick (settled), amber dot (needs a look), red dot (serious).

### Entry Line (signature)
A full-width button row: pen mark, date in the margin, bold reason sentence with the party and document number beneath in pencil grey, bold amount in the money column (with an amber "off by" note), and a chevron. Opening it reveals the working (what was checked, the closest match, what it costs, what to do) as a definition list, followed by labelled action buttons.

### The Auditor's Tick (signature interaction)
Marking an entry done replaces the dot with a green tick drawn stroke by stroke (0.42s, `cubic-bezier(.16,1,.3,1)`) and strikes the reason through in pencil grey; Undo erases it. Reduced motion shows the tick already drawn.

The opening (`js/launch.js`) plays once per launch of the app, never on a reload (a `sessionStorage` flag): on the maroon field a ledger ruling draws in, the two bars of the mark come in from opposite sides on a spring and agree, the flat stroke of the lower bar stands up into the tick, the meeting lines run across the page, a sheen crosses the mark and the name letters in. It then folds into the cover band: the lockup is the cover's wordmark drawn `--s` times larger and scales by 1/`--s` onto it, measured at the moment of the fold, while the field scales down to the band's 56px and the page rises in. Only transform and opacity move, each on its own layer, so the compositor keeps it smooth while the page is built. It waits for the first page, says "Getting your books ready" 1.2s after it has played if the page is still coming, gives up after 12s, and a click or Esc hurries it. Reduced motion shows the lockup still and fades it. The window's background and its pre-server page (`STARTING` in launch.py) are the same maroon, so nothing flashes white.

### Export Register (signature)
A ruled table, one line per export invoice: the invoice number in bold with its date, buyer and value beneath in pencil grey, then one column per stage — shipping bill, bill of lading, GSTR-1, payment, eBRC — each a pen mark followed by a few words ("4569456 · 8 Sep 2026", "Overdue since 22 Apr 2026"). Never a chip or a bar. The invoice cell is the button (with a chevron); opening it unfolds the invoice's file beneath as the same two-column definition list an open entry uses, stage by stage, with *Open* links to the originals and the invoice's open entries as links. On phones each invoice folds into its number over a two-column grid of labelled stages; the open one takes the blotter wash as a whole, not per cell.

Money received, eBRCs and documents are ruled lists in the same key: bold figures right-aligned, the reference in pencil grey under the name, a cancelled line struck through. How much of a payment is still unplaced is a figure with its mark, not a proportion bar.

### Kept Files
Every kept file carries the same plain word links, in this order: *Open* (only for what a browser shows itself: PDFs, pictures), *Save a copy*, *Share*, *Show in folder*; *Remove* last and in red where removing is allowed. The line under a document's name reads `Kind › kept name · added as original name`. Under each list, one pencil-grey line gives the folder's full path in monospace with *Show the folder* and *Save them all as one zip* on their own line below it. Never icons alone: the words are the buttons.

### Ledger Table
Balancing working for bank and party checks: ruled rows, particulars left, bold money right, an operator column in pencil grey, and the closing total under the single-over-double ink rule. A green or red line underneath says whether it adds up.

### Working Overlay and Toasts
The working overlay is a square white card with a 6px maroon top edge over a white scrim, listing stages as hollow circles that fill gold (current, pulsing) then green (done). Toasts are ink-dark with gold links, red for serious, rising in over 0.22s.

## Do's and Don'ts

### Do:
- **Do** open every part and screen with one verdict sentence (34px bold) before any table.
- **Do** give every open entry its own plain-sentence reason in bold ink, with amount right-aligned in tabular figures.
- **Do** keep the colour key visible wherever the pen colours appear: margin on desktop, inline under "Needs a look" on phones.
- **Do** show ticked-off entries as settled: struck through and grey, counted with the settled.
- **Do** write every action as a labelled button that says what it does.
- **Do** separate rows with the faint blue-grey hairline and close totals with a rule above and a double rule below.
- **Do** put counts in the lede as a coloured sentence ("n settled, n need a look, n serious").

### Don't:
- **Don't** use icon-only controls; an icon may accompany a label (the chevron, the tick), never replace it.
- **Don't** use green, amber or red for anything but settled, needs a look and serious.
- **Don't** add charts, proportion bars, KPI cards or status chips; the counts are words.
- **Don't** box content in cards or give on-page elements shadows; only the menu, the working overlay and toasts float.
- **Don't** add a dark theme or web fonts.
