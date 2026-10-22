---
version: 1
slug: "app-frontend-index-html"
primary_target: "app/frontend/index.html"
related_targets: ["app/frontend/styles.css","app/frontend/js/app.js"]
---

# Surface: the reconciliation app (every screen)

Mode: Operate. The visitor completes a check: two documents that should agree, and a clear account of every entry that does not.

Audience: the business owner as much as the accountant. Many are not fluent in software or in reconciliation vocabulary. Desk monitor, office daylight, pages printed for the file.

Job: choose a check (GST purchases, bank, a party's statement), add two files, read one sentence that says whether the books agree, then work through each open entry, each carrying its own reason.

Constraints: offline, local, lightweight (system fonts, no charts, no icon fonts, three JS files). Keep the Excel/PDF/CSV exports, runs kept separate by default with explicit "add to this run", decisions that carry over, the balancing statement that must tie.

Memorable moment: an entry whose description is only "." still says, in its own sentence, exactly why it is open; ticking it off draws the auditor's tick in the margin.

Unresolved: dark theme dropped (use scene is daylight and print). Document reader, analytics, supplier scorecards, command palette and run comparison leave the interface; their APIs stay.

## Direction contract

THESIS: The reconciliation is a bahi-khata page. Every open entry is a written line whose particulars are one plain sentence saying why it is still open, and it opens to the full working beneath it. Refuses the category default: KPI cards, charts and status chips only an accountant can decode.

OWN-WORLD: A deep maroon cloth cover band with turmeric-gold lettering across the top; below it a white page with a double rose margin rule and faint blue-grey ruling between lines. Ink-black system type, figures tabular in a right-hand money column. One gold marker means "you are here". Colour otherwise belongs to the auditor's pen only: green tick = matches, amber = needs a look, red = serious. No cards, no charts, no icon-only controls.

STORY: In one sentence the visitor learns whether the books agree and by how much; reads each entry's own reason without decoding a status; opens any line for what was checked, the closest match, what it costs, what to do; ticks it off, and can undo.

FIRST VIEWPORT: Cover band, 56px: wordmark left, the open run's name as a button to switch, "Start a new check" in gold on the right. Below, a 216px margin column holds the part tabs (GST / Bank / party name) with open counts and the gold marker, then the in-page index and the colour key. The sheet opens with the verdict sentence at 34px, a lede with the figures inline, the balancing working (bank and party) or the credit lines (GST), and the first "Needs a look" lines start inside the viewport: date in the margin, reason sentence, amount in the money column.

FORM: the bahi-khata red ledger book, #6 of my grounded list, seed key 3360b9e6. Signature interaction: the auditor's tick, a drawn green tick in the margin and a pencil strike through the line when an entry is marked done; undo erases it. Raises, each named for its donor:
- Night six-pack: each part opens with exactly one verdict that owns one truth, read in a fixed order.
- HyperCard stack: every entry has its own address; opening a link lands on that entry, open.
- Metro tiles: words are the doors; no icon-only control anywhere.
- Orizuru sequence: the start screen is numbered steps with one gold marker on the current step; every decision is recoverable.
- Cassette deck: every action is a labelled button saying what it does.
- Gravity garden: the colour key is always visible, so colour always means one thing.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
