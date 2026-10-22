# Getting around

## The cover band

The maroon band at the top of every page holds, from left to right:

- **ASSURANCE CONSOLE** - goes back to the start page (and clears any files you had chosen but not checked).
- **Reconciliation** and **Exports** - the two parts. Each remembers where you left it.
- **The open run's name**, in a bordered button (hidden when nothing is open). Click it for *Switch to another run* - the ten most recent - and *See every run*. In Exports it reads *Switch to another register*.
- **Update ready**, with a gold dot, only when a newer version has been downloaded and is waiting (it opens Settings, where **Restart and update** installs it).
- **Settings**, and the gold button: **Start a new check** in Reconciliation (*New check* on a narrow screen), **Add documents** in Exports.

## Runs, checks and registers

A **run** is one client and one period. It holds up to three **checks**: GST, Bank, and a party's statement. **Each check starts a run of its own** unless you add it to an existing run - this keeps one client's GST from ending up beside another client's bank statement. To add a check to a run, use **Add another check to this run** in the run's left margin, or **Add a check to this run** in the list of runs. The start page then says *"This check will be added to …"*, with the link *Keep it as a separate run instead*.

A run is named from the client and the period the documents cover (for example *Kesari Textile Mills · 1 Apr 2025 – 31 Mar 2026*). A check added later fills in only what is blank; it never overwrites what is there.

In Exports, a **register** plays the same part: one exporter's documents, kept as long as you keep adding to them.

!shot 12-runs | Your runs on the start page: when each was last used, its name, what it holds, and Open, Add a check to this run, Delete.

+ The start page lists your runs, newest first: *Open*, *Add a check to this run*, *Delete* (with a warning - it cannot be undone). Eight are shown, then *Show all*.
x A run shows only the **newest** check of each kind. Add a second bank check to the same run and the first is still stored, but no screen opens it again. Two bank accounts belong in two runs.
x Renaming, archiving and pinning runs, deleting a single check, and comparing two checks exist in the program's internal interface only - there are no buttons for them.
x At most 200 runs and registers are listed.

## Starting a check

**Start a new check** asks one question: *What do you want to check?*

- **GST purchases** - your purchase bills against what your suppliers filed on the GST portal (GSTR-2B).
- **Bank** - your bank book against the statement the bank sent.
- **A party's statement** - a supplier's or customer's statement of account against your ledger for them.

!shot 01-start | The start page: one question, three choices, and your runs below.

Pick one and add the two files. Each file goes in its own dashed box: click it, or drop the file on it. A filled box turns solid green and shows the file's name. Under **Matching settings** you can change how closely entries must agree for this check (the defaults come from Settings). **Check these files** stays greyed out until both files are in; **Try it with sample files** runs the made-up sample instead.

!shot 02-files | Both files added: the boxes turn green and Check these files is ready.

While a check runs, a card shows three stages - *Reading your files*, *Matching the entries*, *Writing down why each open entry is open*. The stages move on a timer to show it is working; they are not a measure of progress.

## The run page

A finished check opens as one long page with a margin on the left.

**The margin** (*In this run*) lists the checks in the run, each with how many entries are still open, and *Add another check to this run*. Under *On this page* it lists the sections - Summary, Needs a look (with each kind of problem and its count - click one to show only those), Sales (if loaded), All entries, Downloads and files - and the colour key.

**The summary** opens with one sentence saying whether the two sides agree, then a line of counts - so many settled, so many need a look, so many serious - and the key figures. For bank and party checks it then shows the **working**: your balance, each difference on its own line in plain words, and their balance, closing with *"It adds up. Every rupee between the two balances is on a line above."* Each line of the working that has entries behind it is a link that shows only those entries.

!shot 03-verdict | A GST check: the verdict, the counts, and the four figures that matter.

**Needs a look** lists every open entry as one line: the date, the reason in bold, who and which document, and the amount (with *off by ₹…* when two amounts differ). Serious lines come first, then the largest amounts. Above the list:

- **Show** - every entry, or one kind;
- a search box - it matches names, numbers, GSTINs, the reason, and amounts typed as plain digits (type 142800, not 1,42,800);
- **Hide what I have ticked off** (once something is ticked) and **Open every line**.

150 lines are shown at a time, then *Show more*.

!shot 04-list | Needs a look: each line says why it is still open.

## Colours and marks

| Mark | Meaning |
|---|---|
| Green tick | Matches, or you have ticked it off |
| Amber dot | Needs a look |
| Red dot | Serious: money at risk |
| Grey ring (Exports only) | Not due yet, or a document is still to come |

What counts as serious: in GST, a bill the supplier filed that is not in your books, or credit about to lapse under Section 16(4); in Bank, money that left or arrived with no entry in your books, and cheques more than three months old; for a party, disputed amounts and anything open for more than 180 days; in Exports, whatever puts a refund or a deadline at risk.

## Opening an entry: the explanation

Click a line to open it. The explanation has up to six parts, each only when it has something to say:

- **What this is** - the document in plain words.
- **What was checked** - the questions the matcher asked, answered against every entry on the other side: is there one with this number? this amount? from this supplier? on a nearby date? already paired with something else?
- **Closest match found** - the nearest thing on the other side, and exactly why it was not taken.
- **What it means for you** - the effect in rupees: credit you cannot claim yet, a deadline, a charge not in your books.
- **What to do** - one concrete step.
- **Also note** - anything else about the document (reverse charge, place of supply, a date outside the year…).

!shot 05-entry | An open entry explains itself: what it is, what was checked, the closest match, what it means and what to do.

## Ticking entries off

Under every open explanation:

- **Mark as done** - you have dealt with it.
- **I am chasing this** - it stays on the list, marked as being followed up.
- **Not a problem – take it off the list** - treated as settled from now on.
- On a GST *Probably the same bill* entry, **Yes, it is the same bill** - the pair counts as matched the next time the run is checked.
- **Copy a link to this entry** - an address that opens the run on this entry, already open.

A ticked entry is struck through, drops to the bottom, and stops counting as open. The line says what you decided and when, with **Undo**.

!shot 06-ticked | A ticked entry: struck through, moved to the end, with what you decided and an Undo.

+ Decisions are kept with the run, and come back when you open it again or add another check to it.
x A decision belongs to its run. A new, separate run of the same client starts with none.
x You cannot type a note on a decision, or tick many entries at once.
x Ticking changes the page, not the reports: ticked entries still appear in the Excel, PDF and CSV downloads, in the supplier letters and in the drafted export papers, and still count in the credit and summary figures.
x A decision is tied to the document's number and amount. If either changes in a later file, the old decision no longer applies to it.
x *Copy a link* only works on this computer, while the app is running on the same port.

## All entries, and downloads

**All entries** (at the foot of each check) lists everything, matched pairs too, 100 per page, with its own search; click an open row to jump to its explanation. **Downloads and files** holds the reports for the check and the files it was made from (see each part for what each report contains, and *Reading your files* for the kept files).

## Messages you may see

Small messages appear at the bottom right and fade (serious ones stay until you dismiss them): *Checked - N entries need a look. Each one says why.*; *Saved to your downloads*; *That file could not be made*; *Those files could not be checked*, with the reason. The chapter **When something goes wrong** lists the common ones and what to do.

## Keyboard, screen readers, small screens and printing

+ Every control is a real button or link with words on it; everything can be reached with the keyboard; a *Skip to the page* link comes first; the working card and messages are announced to screen readers; animation stops if you ask Windows to reduce motion.
+ In a narrow window the margin folds into a row of short tabs and each entry reflows onto two lines; tables scroll sideways.
+ Printing leaves out the band, the margin, the buttons and the downloads, and keeps entries whole on the page.
x There are no keyboard shortcuts.
x Only what is on screen prints: 150 entries at a time, and explanations only for lines you have opened.
x There is one light theme; no dark mode.
x Phones and other computers cannot reach the app - it listens only on this computer.
