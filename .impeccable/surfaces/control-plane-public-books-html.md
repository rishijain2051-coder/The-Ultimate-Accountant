---
version: 1
slug: "control-plane-public-books-html"
primary_target: "control-plane/public/books.html"
related_targets: ["control-plane/public/books.css","control-plane/public/books.js","app/keeper/page.py"]
---

## Scope

Keeper: the read-only books viewer on phones and laptops (control-plane/public/books.*) and the Keeper page on the office server (app/keeper/page.py). Mode: Operate. Its own world, separate from Assurance Console's bahi-khata look (DESIGN.md at the root belongs to the Console).

Audience: the owner and admins checking dues, a party's ledger, and the day's entries, mostly on a phone, sometimes at a laptop next to Tally. Job: see what is owed and by whom, in seconds, without asking for detail. Constraint: show the thing first, detail only when asked. Quality bar: LiveKeeping, Google Pay, PhonePe.

## Direction contract

THESIS: Keeper is the category standard played straight, a payments-app account view of Tally's books, with Tally's own yellow bar as the one thing nobody else has. It refuses the ledger-paper, maroon-cloth Console look.

OWN-WORLD: black, white and zinc greys (#111 ink, #f4f4f5 ground, white cards, #e4e4e7 hairlines); Tally yellow #ffe27a only on the row you are on and on selection; red #c62828 only for overdue. System sans, tabular figures. Rounded white cards, initials avatars, pill chips, a black balance block.

STORY: the visitor sees what they'll get and give, sees who is late, opens a party, sees its bills; nothing more unless they ask.

FIRST VIEWPORT: phone: white top bar (K mark, "Keeper", company picker), the company name, a black block split "You'll get / You'll give" with overdue under each, three tiles (cash and bank full width, sales and purchases), then Latest; bottom tab bar with a red count on Dues. Desktop: left sidebar, content, a right key bar like Tally's.

FORM: canon (the standing exit, taken through the safer re-roll); seed key 91114b21. Signature interaction: the yellow bar moves with the arrow keys, Enter opens, Esc goes back, letters jump, F2 period, F3 company.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
