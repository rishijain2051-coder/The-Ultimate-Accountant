# Assurance Console

Two modules that run on one machine, in a window of their own — no account, no
upload, no internet needed; the only thing it ever fetches is its own updates
(and, if you ask for it, the AI checker, once).
**Reconciliation** ties two documents that should agree;
**Exports** keeps every export invoice's papers together and says what is still
missing from each.

| | What it ties together |
|---|---|
| **GST** | GSTR-2B against the Tally purchase register, and the input tax credit working that comes out of it |
| **Bank** | A bank statement against the Tally bank book, ending in a reconciliation statement that balances |
| **Parties** | A customer's or supplier's statement of account against your ledger for them, ending in a confirmation letter |
| **Exports** | Each export invoice's shipping bill, bill of lading, GSTR-1 line, payments and eBRC, filed together: is the paperwork in, will the IGST refund go through, how much is still to come and by when, is the eBRC done |
| **Documents** | Any of the above arriving as a PDF, a scan or a photograph — read on this machine, no service involved |

```bash
python -m pip install -r requirements.txt
```

Then double-click `run_app.bat`, or:

```bash
python launch.py
```

It opens in its own window (WebView2, the browser engine built into Windows;
without it, in the browser), served from <http://127.0.0.1:8000>. Every check has a **Try it with sample files**
button (Exports: *Try it with sample documents*), so you can see a finished
reconciliation before touching your own files. The sample is a fictional company, generated from a fixed seed by
`tools/generate_sample_data.py` — no real business's books ship with this.

---

## What you give it

### GST

| File | Needed | Where it comes from |
|---|---|---|
| Tally purchase register | yes | Tally → Display → Account Books → Purchase Register → export as `.xlsx` |
| GSTR-2B | yes | GST portal → Returns → GSTR-2B → download Excel. One merged workbook, or all twelve monthly files at once. |
| Tally sales register | optional | Same as above, Sales Register. Adds the outward side. |
| Filed GSTR-1 | optional | GST portal, the return you actually filed. Completes the GSTR-3B working. |

### Bank

| File | Needed | Where it comes from |
|---|---|---|
| Bank statement | yes | Net banking → statement, as `.xlsx`, `.csv`, or a PDF (including a scanned one) |
| Tally bank book | yes | Tally → Display → Account Books → Ledger → your bank ledger → export as `.xlsx` |

### Parties

| File | Needed | Where it comes from |
|---|---|---|
| Their statement of account | yes | Whatever the party sent you — workbook, PDF or a photograph of a printout |
| Your ledger for them | yes | Tally → Display → Account Books → Ledger → that party |

### Exports

Add any of these, in any order, whenever they arrive. Each is recognised from
its contents, so nothing needs renaming or sorting first.

| Document | Where it comes from |
|---|---|
| Shipping bill | ICEGATE → document download, or the copy your customs broker sends |
| Bill of lading or airway bill | Your forwarder. A scan or a photograph is fine |
| GSTR-1 | GST portal → the return's JSON, or the offline tool's Excel |
| Bank advice (FIRA) | Your bank, when a buyer pays |
| DGFT IRM and eBRC lists | The Excel downloads on DGFT's eBRC pages |
| Shipping bill status | ICEGATE's *View Access Data* sheet, as Excel |
| Your export invoice | PDF or Excel; optional, but it settles which side is wrong when numbers differ |
| Your own shipping bill register | The Excel sheet you keep today, a tab per year: GST amount and when it came back, drawback and when it came back, the ICEGATE error code. Years of records carry over |
| ICEGATE refund and drawback status | The IGST refund status and drawback status downloads, with the response code on each shipping bill; a table copied from ICEGATE's drawback pages works too |
| The bank's EDPMS statement | Your AD bank's list of shipping bills it still holds as unpaid, Excel or PDF |

Column order never matters. Every sheet is read by matching header labels, so
portal exports, merged workbooks, re-saved files, bank formats you have never
seen before, and the `B2BA`, `CDNR`, `CDNRA`, `ISD` and `IMPG` sections all
work.

## What it does

### GST matching

Six passes, each stricter than the last, each looking only at what the earlier
passes left behind:

1. Invoice number and value agree. Numbers are normalised first — case,
   spaces, slashes and the leading zeros of each group are stripped, so
   `25-26/0376` and `25-26/376` meet.
2. Same invoice number, different value → **value mismatch**.
3. Value and supplier name agree, number does not → **invoice no. mismatch**.
   A shared run of trailing digits strengthens it.
4. Party, value and date close enough to be worth a look → **probable match**,
   offered for a human to confirm or reject.
5. One invoice whose value equals the sum of two or three vouchers for the same
   supplier → **split booking**.
6. Anything left is reported one-sided.

Amendment rows supersede the documents they amend and drop out of every total.
Credit notes carry negative tax. A decision you record by hand is keyed to the
document itself, so it is applied again automatically the next time the same
period is reconciled.

**The credit working** starts at the figure the portal shows and strips, in
order: rows the portal blocks, partially-exempt rows, CGST/SGST charged by
another state, invoices not yet in your books, and reverse-charge tax. Each
row's credit comes off once, at the first of those that applies to it. What is
left is what you can defend today. The GSTR-3B working sets off the credit
allowed by law, head by head.

**It also checks** for impossible tax splits (IGST alongside CGST/SGST, unequal
halves), duplicated invoices, documents dated outside the year, place of supply
outside your state, invoice values that do not tie to taxable plus tax, and
credit approaching the section 16(4) cut-off.

### Bank reconciliation

Statement lines are matched to book entries on amount and date, never across
directions — a receipt is never matched to a payment, however close the figures
are. Cheque and UTR numbers are read out of the narration, with IFSC codes
removed first so a branch code is not mistaken for a reference. One statement
credit answering several book entries is recognised as a batch. Each line is
classified by channel (NEFT, RTGS, IMPS, UPI, ACH, cheque, cash, charges, tax,
interest, salary, loan) so a month can be read by payment rail.

What comes out is a **reconciliation statement**, not a list: book balance,
then each reconciling group as an Add or a Less, ending at the statement
balance. It is derived from the arithmetic, so it always ties — and if it ever
did not, that would mean a document had been misread, which is the point.

It also names the things worth acting on: cheques stale past 90 days, bank
charges and interest never posted, GST on bank charges you could be claiming,
cash movements near the section 269ST limit, and any break in the statement's
own running-balance chain.

### Party statements

Their statement and your ledger are normalised to the same question — *how
much is owed to the party* — so the two sides compare directly whichever way
round the relationship runs. Invoices, receipts, credit notes and adjustments
are matched on reference and amount, then the difference is explained as a
**bridge**: your closing balance, each reconciling group, their closing
balance.

Where the party is a supplier and a GST run is already stored, invoices in
their statement are also checked against GSTR-2B — so an invoice they have
billed but never filed is caught here rather than at the year end.

The output includes a **balance confirmation letter** setting out every
reconciling item, ready to send back.

### Document intake

GST files must be `.xlsx`. Bank and party files can be `.xlsx`, `.csv`, PDFs
or pictures; an old `.xls` has to be saved as `.xlsx` first. A PDF page with no text
layer, and any image, goes through OCR: the page is rasterised at 200 dpi and
read by a model that ships inside the installed wheel — nothing is uploaded,
and there is no Tesseract or Poppler to install.

Recognition is tuned for what it reads: words are read three at a time
(against the text layers of 24 real PDF pages read as pictures, that was 18%
faster than the library's default and found slightly more of the figures), the
engine loads in the background when the app starts, very large pictures are
capped so one poster-sized scan cannot take gigabytes of memory, and memory
is handed back after every page. A text PDF that simply is not an export
document is no longer read a second time by OCR - only a scan with a text
layer on top, or text that is not words, is, and then only its first two pages.

Rows and columns are rebuilt from where the words sit on the page, so a
scanned statement comes back as a table rather than a paragraph. Two
independent checks tell you whether the digits were read correctly: the
per-word confidence, and whether the statement's own running balance chains
from one line to the next. A photo of a statement is read in about a second:
the recogniser runs with a few fast threads rather than every core, and without
the per-line rotation guess that used to turn figures upside down.

### The export register

**Nothing goes in unchecked.** A document added to a register waits under
*Check what was read* until you post it: its kind, the numbers that decide
where it goes (invoice, bill of lading, shipping bill), its dates and money,
whether it was read from a scan, and where it will be filed - worked out with
it in, but kept out of the register until you press *Post it* (or *Post all*).
*Correct what was read* opens a window with every field and table the reader
filled in: type over any value, change, remove or add a table's rows, or turn
a document it could not recognise into the shipping bill, bill of lading,
advice or invoice it is and fill in its numbers. The same window corrects any
document at any time after, from the Documents list or the invoice's file. The
reader's own output is never changed: corrections are kept beside it and laid
over it each time the register is worked out, a value put back is no longer a
correction, a bad value refuses the whole edit with the reason, and the kept
file takes the name its corrected reading gives it. The sample is posted
without a check. *Read the file again*, in the same window, reads the kept
copy with this version's reader - for a document read before the reader knew
its layout: corrections that still say something else stay, and it waits to
be checked again.

**History.** Each register keeps an audit trail, newest first: every document
added, checked and posted, corrected (field by field, with what it said
before), and removed; every payment you set against invoices; every yes or no
you gave; and every button pressed on an open entry - each with the time and
who (the name in Settings, or the Windows account). It is the last section of
the register and a sheet of its own in the register's Excel download.

**The AI checker - an add-on, only if you want it.** *Settings > AI checker >
Get the AI checker* downloads a local AI model once: the Ollama runtime
(v0.34.4, from Ollama's own GitHub release, checked against the SHA-256 it
publishes, with the CUDA 12 libraries dropped) and then qwen3.5:4b through the
runtime's own download, into `%LOCALAPPDATA%\Assurance Console\ai` - about
5 GB down, about 4 GB kept; a download cut off carries on where it stopped.
It is never part of the installer or an update, so those stay small. One copy
serves this app and Invoice Bridge, which runs the same runtime and
model: an app with a complete copy says where it is in
`%LOCALAPPDATA%\Shared AI\<its name>.json` (at every start, and when its
download finishes), and one without runs the other's - as a process of its
own, with Ollama's pruning off (`OLLAMA_NOPRUNE`), writing nothing into it -
so nothing more is downloaded. Removing a copy takes its note away first, then
stops whatever runs from it. While
the app runs, the runtime runs beside it: on 127.0.0.1 only, on a port of its
own (11500-11599, clear of any other Ollama, the bridge's included), at idle
priority, held by a Windows job object to half the processor, and gone the
moment the app is. In the background - once the app has been left alone for a
minute, one document at a time, resting as long as each took - it reads every
shipping bill, bill of lading, bank advice and invoice kept as a PDF or a
picture again, posted or waiting to be checked (a scan as a picture of its
first two pages, good text as text), and compares its reading with the app's,
corrections included, field by field through the app's own readers (dates day
first, amounts digit by digit, a number copied with its date after it taken
without the date). What it is asked is fixed in `ai_check.py`: copy a fixed
set of fields, its answer held to a JSON schema while it writes, nothing
calculated, nothing on the page taken as an instruction. Where the two agree,
the document says so; where they differ, a posted document gets an open entry
under *Read differently by the AI*, and one waiting to be checked shows both
readings. *Use the AI's reading* keeps it as your corrections, in the history,
undone like any other; a correction, or the file read again, has it checked
again. Nothing it reads goes in by itself.

Every document is filed against the invoice it belongs to: a bill of lading by
the shipping bill number printed on it, then the invoice number, then the
container; a GSTR-1 line by its shipping bill before its invoice number,
because a wrong invoice number is one of the things to catch. From that, each
invoice gets one line with five columns — shipping bill, bill of lading,
GSTR-1, payment, eBRC — and open entries that say why, as everywhere else:

- **The IGST refund:** not in Table 6A, in it twice, shipping bill details
  blank or wrong (SB001), a different GSTIN on the shipping bill (SB003), the
  invoice number or date written differently (SB005, with which of the two
  fixes applies and whether it costs the ₹1,000 port fee), IGST or WPAY/LUT
  that does not agree.
- **The buyer's money:** the deadline follows the date the goods shipped, and
  four rules have applied since November 2025 (9, 15, 9, then 15 or 18 months).
  The periods, the ₹10 lakh declaration limit and the rest are data in
  `app/backend/exim_rules.json`, not code. Overdue bills say what else is at
  stake: Rule 96B or 96A, and advance-only exports once a bill is a year late.
- **Money is never matched silently.** A payment counts against an invoice when
  its eBRC or the bank's advice names the invoice, or when you say which: confirm
  a suggestion (*No, it is for another invoice* moves it on), or split the
  payment yourself. Adding a bank advice that no document places opens a
  window asking which invoices it is for - every invoice in its currency with
  money still to come, the advice's own and the buyer's first, oldest first,
  with a split filled in to start from (EUR 25,000 over three invoices of
  10,000: the two oldest in full, 5,000 on the third). Any amount can be
  changed; what is left stays on the payment as not yet placed. Every payment
  has *Choose invoices* or *Change* under Money received, and in each invoice's
  file. Your split counts over the advice's own invoice; an eBRC's does not
  move. When no single invoice fits a payment but a run of one buyer's
  invoices does (oldest first, allowing for the banks' charges), the
  suggestion is for all of them at once, answered in that window. An advance
  (P0103) set against goods already shipped is flagged: the bank has it on the
  wrong code, and EDPMS will not close those bills until it is changed.
- **Bank advices** are read in both layouts seen so far: the FIRA certificate,
  and Kotak's Remittance Transaction Advice - a scan, its title run together
  (or every space lost), the amount written after its currency, and packing
  credit repaid out of the
  money before the rest reaches the account (both shown in the check).
- **eBRCs:** DGFT makes them for free, so the app fills in DGFT's bulk upload
  sheet instead, each row checked the way DGFT checks it (purpose code, IRM
  details, amount left on the IRM).
- **The IGST refund and the drawback on every shipping bill:** what was paid
  (read from the shipping bill's own Part I boxes), what came back and when
  (from your register, or ICEGATE's scroll), and — when it has not — the ICEGATE
  code with what it means and what fixes it (SB000 to SB006). A refund is only
  called missing on evidence: your register lists the bill with nothing
  against it, or ICEGATE shows a code. With neither, the bill goes on a list to
  look up on ICEGATE instead. The app never logs in to ICEGATE — it has no API
  for exporters — so it reads the status sheets you download.
  Each bill to look up shows its port, number and date the way ICEGATE's
  enquiry asks for them (a click copies each), with a link that opens
  ICEGATE's public shipping bill enquiry in the browser - no login, a captcha
  you type; the IGST refund status needs your login. A status sheet, DGFT
  list or EDPMS statement saved to the Downloads folder in the last week is
  offered at the top of the register (*Add it* / *Not this one*); the folder
  is only read, never changed.
- **Your own yes or no:** each invoice's file asks *Filed in GSTR-1?*,
  *Refund received?* and *Drawback received?*. The documents answer first;
  where none does, or one is wrong (a GSTR-1 downloaded before it was filed, a
  refund that reached the bank with no download to show it), press Yes or No.
  Your answer counts over the documents - in the register, the open entries,
  the figures and the downloads - until you press Undo, and the file says
  what the documents said.
- **The bank's EDPMS statement** against what is on file: a bill the bank still
  holds as unpaid that is paid here, one it has closed that is unpaid here, one
  it cannot see at all, and any deadline the bank has extended.
- **Drafts:** the SB005 pack for each port (request letter, Annexure A,
  challan schedule), the Table 9A worksheet, the SB003 undertaking, the
  declaration to the bank for bills of up to ₹10 lakh, and the request for more
  time — Word letters and Excel schedules, filled in from the register, with
  the reasons, challan number and signature left in brackets. An SB005 goes to
  the port letter only when the documents show the shipping bill is the wrong
  side; when they cannot tell, it waits in the 9A worksheet marked to check.

The originals are kept beside the database, named for what they are
(`SB 3183667 of 20-Aug-2024 (INNSA1) - KE-24-25-088.pdf`, not the download's
own number) and filed by kind, so any of them opens again from the invoice it
was filed against — and an invoice's own papers (shipping bill, bill of
lading, invoice, bank advice) save as one zip to hand over.

## Every file is kept, and can be handed over

Every file given to the app — a Tally export, a GSTR-2B, a bank statement, a
party's statement, an export document — is kept in the data folder (beside the
program, or wherever you chose), in a folder per run or register and a folder
per kind inside it:

```
data\documents\
  KESARI TEXTILE MILLS - 2025-04-01 to 2026-03-31 (5d57d7)\
    GST purchases\    Tally - PurchaseRegister.xlsx, GSTR-2B - MERGED_GSTR2B.xlsx
    Bank\             Bank statement - ....xlsx, Bank book - ....xlsx
    Party statements\ Their statement - ....xlsx, Our ledger - ....xlsx
  KESARI TEXTILE MILLS PRIVATE LIMITED - exports (653c83)\
    Shipping bills\  Bills of lading\  Invoices\  GSTR-1\  Bank advices\
    DGFT\  ICEGATE\  Your register\  Not recognised\
```

Each file has *Open* (PDFs and pictures), *Save a copy* (under its kept name),
*Share* (Windows' share window, for the kinds of file a browser can share —
PDFs and pictures; anything else is saved as a copy instead) and *Show in
folder* (File Explorer with the file picked out, ready to drag into an email).
*Save them all as one zip* takes a run's or register's whole folder. The same
file added twice is kept once. Deleting a run or register deletes its folder;
uninstalling keeps the data folder unless told otherwise. A check made with
saving switched off in Settings keeps nothing.

## Where your data is kept

The first install asks: press Enter to keep the data beside the program, or
choose another folder in Windows' own folder picker (a folder that already has
other things in it gets an "Assurance Console data" folder of its own inside
it, so erasing the data can never take anything else). The choice is one line
in `data-location.txt` beside the program:

- every update keeps it, and says where the data is;
- Settings shows the folder and its size, with *Show the folder* and *Move it
  to another folder…* - everything is copied, the copy is checked (file count
  and bytes), and only then is the old copy removed; the app carries on from
  the new folder without a restart;
- the uninstaller asks about that folder, keeps it by default (with the note,
  so installing again picks it up), and when told to erase it removes only
  what the app wrote there;
- if the folder cannot be reached - a drive that is not plugged in - the app
  says so and stops, rather than starting empty and splitting the data in two.

## What you get back

- The reason every open entry is open, in plain words: what it is (and when
  its description is only "."), what was checked, the closest thing on the
  other side and exactly why it was not paired, what it costs, and what to do
- An input tax credit working, line by line, and a GSTR-3B working when a sales
  source is loaded
- A bank reconciliation statement that balances, and a party bridge that ties
- Ready-to-send follow-up letters, one per supplier, and balance confirmation
  letters for parties
- Excel workbooks — 17 to 27 sheets for GST, up to 12 for bank and for a party
  (a sheet appears only when there is something to put on it),
  and the export register with its open entries, money received and eBRCs
- DGFT's bulk eBRC upload sheet, filled in and checked row by row
- The export drafts: SB005 pack, Table 9A worksheet, SB003 undertaking, EDPMS
  declaration, extension request, and the list of shipping bills to look up
- The register again in your own sheet's layout, a tab per year
- Printable PDF working papers for all three
- CSV extracts of any view
- Every run kept, so next month shows what actually got fixed

## Around the interface

It is written for the business owner as much as the accountant, so it has
very little of it:

- **Start a new check** asks one question: GST purchases, the bank, or a party's
  statement. Add the two files and press *Check these files*, or try the
  sample files first.
- **A run** is one client and one period. Each check starts its own run unless
  you add it to one (*Add another check to this run*, or *Add a check to this
  run* in the list of runs). A run holds up to one GST, one bank and one party
  check, shown as the tabs down the left.
- **Each check** opens with one sentence saying whether the two sides agree,
  then the working that ties them, then **Needs a look**: every open entry as a
  line whose words say why it is open. Open a line for the full explanation.
  Serious lines (money at risk) come first, largest amount first.
- **Tick it off.** *Mark as done*, *I am chasing this* or *Not a problem – take it off the list* on any
  entry; *Undo* reverses it. Decisions are kept per run and apply the next time
  the same run is checked. *Copy a link to this entry* gives an address that
  opens the run on that entry, already open.
- **All entries** (matched ones too) and **Downloads** sit at the foot of each
  check. GST checks can be matched again at different settings without
  re-uploading, because the parsed records were saved with the run.

**Exports** is the second tab in the cover band. A register is one exporter's
documents, kept as long as you keep adding to it: drop new documents on it as
they arrive and everything is worked out again. Each tab returns to where it
was left.

**Closing it.** Closing the window closes the app, and so does *Close the app*
in Settings. Every run is already on disk, so nothing is lost. Started again
while it is open, it brings the window to the front instead of starting a second
copy. In the browser (no WebView2) the tab can be closed without stopping it, so
*Close the app* is the way; `Ctrl+C` in the terminal works when you started it
from one.

**Updates.** The installed app asks its GitHub release page (the repository
named in `UA_UPDATE_REPO`) for the newest version 20 seconds after it starts and every six hours after, while *Check for
updates* is on in Settings — the only request it ever makes to the internet,
carrying nothing about anyone's data, besides the AI checker's one download
when asked for. A newer release is downloaded in the
background, shown as *Update ready*, and installed only when the user presses
*Restart and update*. See *Publishing an update* below.

## Where things live

```
app/backend/
  common.py            normalisation, fingerprints, FY arithmetic, money
  parser.py            every workbook reader, header-mapped
  reconciler.py        the six GST matching passes, purchase and sales
  insights.py          credit working, GSTR-3B, ageing, follow-ups
  explain.py           why each open entry is open, in plain words
  bank.py              bank matching, channel classification, the BRS
  statements.py        party matching, the bridge, confirmation letters
  docs.py              PDF text, OCR, row and column reconstruction
  excel_generator.py   the GST workbook (17 to 27 sheets)
  exports.py           GST PDF, CSV, follow-up pack
  exports_recon.py     bank and party workbooks, PDFs and CSVs
  exim_read.py         recognises and reads each export document
  exim_fields.py       checking and correcting what was read: forms, checks, corrections
  exim.py              the export register: filing, checks, deadlines, money
  exim_rules.json      realisation periods and limits, kept as data
  exim_out.py          the register workbook and DGFT's bulk eBRC sheet
  exim_drafts.py       the drafted letters, worksheets and schedules
  ai.py                the AI checker's engine: fetched on request, run in the background
  ai_check.py          what the AI checker reads again, and how the two readings are compared
  store.py             SQLite: runs, decisions, documents, suppliers, settings
  server.py            the HTTP API
  updates.py           the update check: the one thing sent online, signed
  version.py           the version, in one place
  selftest.py          asserts for the paths real data never hits
  selftest_exim.py     the Exports checks, pulled into selftest.py
app/frontend/
  index.html  styles.css  assets/
  js/{core,views,exports,app}.js   no framework, no chart library, system font
  paths.py             where things are, running from source or frozen
data/reconcile.db      created on first run
data/documents/        every file given to the app, a folder per run or register
SampleData/            the fictional corpus, generated, never real books
tools/
  generate_sample_data.py   invoices, suppliers, sales, GSTINs
  synth_bank.py             the bank quarter and a party statement
  synth_exim.py             the export register's documents, problems planted
media/
  capture.py           runs its own copy of the app and screenshots it
  guide.py  guide/     renders the PDF guide from its parts and the screens
  script.py  build_video.py  music.py   the old tutorial video
build.spec             the app build
launch.py              starts the server and the app's window
packaging/
  installer.py  uninstaller.py         Install.exe and Uninstall.exe
  install.spec  uninstall.spec
  build_release.py     builds all three, in dependency order
  publish_release.py   signs a release and puts it on GitHub for the updater
```

Run the engine's self-check with:

```bash
python -m app.backend.selftest
```

Seventy-nine checks, covering the matching passes, the credit ladder (a bill under
two of its lines comes off once), both reconciliation bridges, the party
statement laid out as one Amount column with a Dr/Cr column, the OCR table reconstruction, the
rupee sign in the PDF working papers, the update check refusing anything not
signed or not matching its fingerprint, taking the app's own code alone only over the
base it was built on and giving each of GitHub's addresses
only a few seconds to pick up, a start with no copy open not waiting on closed
ports, the explanation every open entry carries, and for Exports the deadline
rules at each boundary, a payment waiting for its confirmation, one split over
three invoices (and refused when it asks too much), one payment offered for a
buyer's three invoices at once, a scanned bank advice read with its whole
amount (and with every space lost), a shipping bill's buyer read past a stray letter, a document read
again from its kept copy, the ICEGATE look-up links, a list offered from a stand-in
Downloads folder (which is left unchanged), your own yes or no
standing in for a document, a document waiting unposted until it is checked, a
misread bill of lading filed right once corrected (and a bad correction
refused whole), the history keeping each change, a database from before the
check opening with everything posted, the whole export sample and every draft
made from it (the SB005 challans must come to ₹1,000 a shipping bill), the
names documents are kept under, and the AI checker - fetched from stand-in
servers (a cut-off download carried on, a runtime failing its checksum deleted
unopened, CUDA 12 dropped, removed without a trace) and reading documents
again through a stand-in model (good text sent as text and a scan as a
picture, a difference listed, its reading used as a correction and checked
again, a field it left blank not counted against the app, a check the
runtime was stopped in asked again) and sharing one copy with the bridge (the
bridge's run with pruning off and nothing written to it, a stopped runtime
started again, its own copy listed in ASCII JSON, removing it taking the note
away first and stopping only what runs from it).

## Packaging it as a Windows program

For anyone who should not have to think about Python:

```bash
python -m pip install pyinstaller
python packaging/build_release.py
```

That produces **one file** to send — `dist/release/Install.exe` — and that is
the whole distribution. The program is carried inside it, so there is nothing to
unzip. Beside it, `dist/release/Update.exe` carries only the app's own code
(a few MB) for copies already installed; see *Publishing an update*.

The person receiving it runs Install.exe. It installs into
`%LOCALAPPDATA%\Programs\Assurance Console`, which needs no administrator
rights, makes a desktop and Start-menu shortcut, registers in Add/Remove
Programs, and offers to start the app straight away.

Run on a machine that already has it, Install.exe finds that copy and updates it
with no questions. It closes the app if it is open (and opens it again when it
is done), moves the old program aside while the new one is copied in, puts the
old one back if the copy fails part-way, and never touches the saved
reconciliations. `python packaging/test_update.py` checks that path against a
throwaway folder.

Three executables, each doing one job:

| | |
|---|---|
| `Install.exe` | the single download; carries the other two inside it |
| `Update.exe` | the app's own code alone, for installed copies on the same base |
| `Assurance Console.exe` | the app |
| `Uninstall.exe` | installed alongside; also in Add/Remove Programs |

Uninstall keeps your saved reconciliations unless you type YES to erase them,
wherever you chose to keep them, and copies itself to a temporary folder
first, because Windows will not delete a running program.

Notes on the build:

- The **app** is a one-folder build and the **installer** is one-file, which
  looks inconsistent but is the same reasoning applied twice: a one-file app
  would unpack 13 MB of OCR models to a temporary folder on *every* launch,
  turning a two-second start into a twenty-second one, whereas the installer
  pays that cost once.
- The app has **no console window**; its window is pywebview over WebView2,
  and the launcher reports a failed start in a dialog box rather than to a
  terminal nobody can see. The installer and uninstaller *do* show a console,
  because copying a few hundred megabytes in silence looks like a hang.
- `launch.py` checks for the WebView2 runtime itself before opening a window:
  without it pywebview falls back to Internet Explorer's engine, which cannot
  run this interface, so the browser is used instead. It also puts back two
  browser habits pywebview changes (downloads straight to Downloads; the
  right-click menu and Ctrl+F / Ctrl+P), which is why `requirements.txt` pins
  pywebview. `build.spec` collects all of pywebview's files: the standard hook
  misses the scripts it injects into every page. To find a copy already open
  it asks only the ports something listens on: asking a closed port takes two
  seconds on Windows, which cost about five seconds on every start.
- The database goes where the first install was told to put it - beside the
  program by default, or under `%LOCALAPPDATA%` if that turns out to be
  read-only. Settings shows the folder and can move it.
- `pandas`, `scipy` and `pyarrow` are excluded — nothing in the app imports
  them. Do **not** also exclude the OpenBLAS libraries to chase the last of
  scipy: numpy ships its own copy under a `libscipy_openblas` name, and
  filtering it takes numpy's C extension down with it. The exe still starts and
  serves, and then every OCR read fails on a missing DLL. That mistake cost a
  build cycle; the comment in `build.spec` says so, so it is not repeated.

## Publishing an update

Installed copies look for updates on the GitHub release page of the repository
named in `UA_UPDATE_REPO` (`owner/name`, in `deploy.env` or the environment; see
[configuration](configuration.md)): a **public** repository of your choosing that
holds nothing but the installers, so nothing is needed to read it and no key
ships inside the program. A copy built without `UA_UPDATE_REPO` and
`UA_UPDATE_PUBLIC_KEY` never checks: `build_release.py` refuses to build without
them, and `--without-updates` builds such a copy on purpose. GitHub hands
release files to its own storage by a redirect. That storage answers on four
addresses, and from some networks one never picks up, so each gets four seconds
to connect before the next is tried (the full 20 s applies once connected).

To send an update out:

0. Once: `python packaging/publish_release.py --make-key` makes your signing
   key (below) and prints its public half; set that as `UA_UPDATE_PUBLIC_KEY`.
1. Raise the version in `app/backend/version.py` and `packaging/installer.py`
   (the build refuses to go ahead if they differ), and on the guide's first
   page (`media/guide/01-start.md`; its footer reads `version.py`).
2. `python packaging/build_release.py`
3. `python packaging/publish_release.py "What is new, in a sentence or two."`

The last step writes `latest.json` (the version, the installer's address and its
SHA-256 fingerprint, and the same for `Update.exe` with the base it was built
on), signs it with the release key, checks the signature against the public key
the app is built with (`UA_UPDATE_PUBLIC_KEY`), and creates the release on
`UA_UPDATE_REPO` with the four files (`gh` must be signed in). Each installed copy finds it at its next check, verifies the
signature, downloads what it needs, checks the fingerprint, and offers
*Restart and update*.

**Light updates, like Invoice Bridge's.** The app's own code is not frozen
into the exe: `build.spec` copies it into `_internal/app` as plain files (with
`.pyc` checked by the source's hash), and writes `_internal/BASE` - the *core*
(Python, PyInstaller, the bundled packages and their versions, the launcher,
the build recipe, the sample data) and every module the base holds. A copy
whose core matches the release's, and which holds every module the new code
needs, fetches `Update.exe`: the same `installer.py`, carrying only
`_internal/app`, which closes the app, swaps that folder (the old one waits
aside and comes back if the copy stops), and opens it again - a few MB instead
of the whole program. Any other copy fetches `Install.exe`, and so does every
copy when a release changes a package, the launcher or the recipe. Update.exe
refuses, and changes nothing, over a base it was not built for.

**The release key** is `%USERPROFILE%\.ultimate-accountant\release-signing-key.pem`
(or the path in `ASSURANCE_SIGNING_KEY`), an Ed25519 key of your own made by
`--make-key`, outside this folder and never in the program; only its public half
goes into the app, as `UA_UPDATE_PUBLIC_KEY`. Keep a copy somewhere safe and offline. Losing it
means no further update can be signed, and every installed copy would need one
update by hand with an installer carrying a new public key. A key that leaks
would let someone else sign updates: make a new one, publish a release carrying
its public half at once, and treat copies that have not taken it as suspect.

## The guide

`media/Assurance Console - Guide.pdf` (also beside the installer in
`dist/release/`) replaces the tutorial video: sixty pages covering every
screen, every file the app reads and writes, both modules, every check, every
download, the settings, privacy, and - as carefully - everything it cannot do,
gathered again at the end with a table of every number it uses and a glossary.
It is built from the app itself rather than mocked up:

```bash
python media/capture.py      # 30 screens, from runs and a register it makes itself
python media/guide.py        # media/guide/*.md + the screens -> the PDF
```

`media/capture.py` starts its own copy of the app on an empty data folder
(`D:\Assurance Console data`, or a temporary one if that name is taken - so
the pictures show no one's user name, and your own data is never touched),
makes the clicks a first-time user would, and shoots each screen or section.
The words live in `media/guide/`, one file per part, in a small markdown
(headings, paragraphs, bullets, tables, `+`/`x` for can and cannot, `>` for a
limit worth stopping on, `!shot name | caption`); edit them and run
`guide.py` again. The PDF uses the app's colours and its font, Segoe UI.

The old video pipeline (`media/script.py`, `build_video.py`, `music.py`) is
still there and still works from the same screens, but is no longer the
tutorial.

## The sample data

Everything under `SampleData/` is invented:

```bash
python tools/generate_sample_data.py
```

One fictional company — Kesari Textile Mills, Jaipur — with 52 suppliers, a
year of GSTR-2B downloads, a Tally purchase register, a sales register and a
filed GSTR-1, a bank quarter with its statement and bank book, a scanned copy
of that statement, and a supplier's statement of account. Roughly a thousand
documents.

It is built by writing one set of *true* transactions and then deriving both
sides from them, applying a named divergence to some — a value keyed wrong, an
invoice renumbered, a purchase never filed, one invoice booked as three
vouchers. So the defects the app should find are known before it runs, and the
generator's own `verify()` re-runs the real parser and reconciler over the
output and fails if a category has drifted to nothing.

Two details worth keeping:

- **GSTINs carry a correct check digit.** A demo that ships invalid ones
  teaches you to ignore the one field that can be validated for free.
- **`verify()` also refuses known real-world names and GSTINs**, so a careless
  edit cannot quietly reintroduce a client's books into a shipped build.

The export sample is its own generator, `tools/synth_exim.py`: the same
company's 23 export invoices as ICEGATE-style shipping bill PDFs, bills of
lading (one a noisy scan), a year of GSTR-1 as JSON and offline-tool Excel,
bank advices, the DGFT and ICEGATE downloads, the bank's EDPMS statement, and
the exporter's own register in the layout exporters keep. Most invoices carry
one planted problem — a digit swapped in Table 6A, an invoice number the
shipping bill spells differently, a payment short by bank charges, a refund
ICEGATE paid that nobody entered, a drawback held on a query, a bill the bank
cannot see — and its `verify()` fails unless all 39 are found and the clean
invoices stay clean.

Regenerating changes nothing unless you change the seed — the corpus is a
fixture, and a fixture that moves under you is worse than no fixture.

## When something goes wrong

The packaged build has no console, so errors go to a file instead:

```
%LOCALAPPDATA%\Programs\Assurance Console\data\log.txt
```

Any unhandled error is written there with its full traceback, and the API
returns the error class and that path rather than a bare 500. Two diagnostics
are worth knowing:

| | |
|---|---|
| `/api/health` | is it up |
| `/api/ocr/status?probe=true` | whether recognition is wired up, and if not, which sub-module failed and why |

## Notes worth knowing

- Tally exports carry no GSTIN column, so supplier identity rests on the ledger
  name. Check the supplier tables before acting on them.
- Bank-charge GST appears in GSTR-2B but never in a purchase register; it is
  normally posted through a bank or journal voucher. Those rows show up as
  "in GSTR-2B only" and are usually correct — write them off once and the
  decision carries forward. The bank module finds the same tax from the other
  direction, which is a useful cross-check.
- A blended tax rate on a multi-item invoice is flagged for information, not as
  an error. Only a rate outside the statutory range is a defect.
- OCR is read carefully, not trusted. A scanned statement whose balance chain
  does not follow is still reconciled, but with a note, a finding and each
  broken row flagged — check those pages by eye before relying on the figures.
- Nothing in this app files a return, talks to the GST portal, or connects to a
  bank. It reads what you downloaded and tells you what it finds. Its one
  connection is the update check, to its own GitHub release page - and, only
  when you press *Get the AI checker*, that one download from Ollama's (none
  when Invoice Bridge has already downloaded the same AI).
