# Invoice Bridge

Upload a bill, say what kind of entry it is, and check what the app understood
next to the document itself: what it read, and exactly what it will post to
Tally, debit by debit. Correct anything wrong and post it with one click. It
remembers your corrections for the next bill.

Everything runs on this one computer: the app, the database, the OCR and the
AI model. No invoice or ledger ever leaves the machine.

---

## The flow

1. **Upload.** Drop a PDF or photo, or a batch of them, or drop them into the
   watched folder. A scan of several bills in one PDF can be read as one bill
   per page: tick *Each page is a separate bill*.
2. **Say what it is.** Purchase, Sales, Payment, Receipt, Contra, Journal,
   Debit Note, Credit Note, or any custom voucher type in your Tally. Custom
   types post like the built-in type they derive from.
3. **Review side by side,** each bill as soon as the OCR has read it: the rest
   of a batch keeps being read and joins the list as it finishes. The AI then
   reads each bill too, in the background (*AI checking…*), and the open bill
   updates by itself when it is done: what the AI filled in or corrected
   turns amber, and what you have changed stays yours. A bill that could not
   be read says why, with *Try again*. If the app is closed in the middle of
   a batch, it carries on at the next start. The document on the left; on
   the right, the voucher as Tally's own voucher screen shows it, edited the
   same way:
   * **Header:** the voucher type, *No.*, the date with its day, *Supplier
     Invoice No.* (blank unless you type it, as your books keep it), *Party
     A/c name* with its current balance in Tally, and the status.
   * **Items:** *Name of Item*, *Quantity*, *Rate*, *per*, *Amount*, worked out
     as in Tally: a quantity or rate changes the amount, an amount the rate.
     Under each item, what the bill calls it, the ledger behind it and its GST
     rate. A ledger picked for an item is used for its next bill.
   * **Ledgers under the items:** charges, CGST and SGST (or IGST) with their
     rate, and *Rounded Off*. Any amount can be typed over, a reduction as
     (-)0.40; × takes a row off, *+ Add ledger* adds one, and *+ CGST & SGST*,
     *+ IGST* and *+ Rounded Off* add those at the items' rate.
   * **Bottom:** the narration, the total quantity and the total, and whether
     that is the bill's total. Under it, what the bill itself says (supplier,
     GSTIN, taxable value, total) to compare. Hover a label for the line on the
     bill it came from.
   * **Keys, as in Tally:** Enter moves to the next field, Ctrl+A accepts
     (saves), F2 is the date, Ctrl+H changes the mode (*Item Invoice* or
     *Accounting Invoice* to *As Voucher*, the voucher as Dr/Cr rows; payments,
     receipts, contras and journals open that way), Ctrl+I shows more details
     (place of supply, due date, the GST registration), Ctrl+L saves it in
     Tally as optional.
   * **One source:** each amount is a field of the bill or one of its lines,
     so a change here changes them, and the checks, with it. What is shown is
     built by the same code that posts, so it cannot differ from what Tally
     receives.
   * **Duplicate check**: against this app's entries *and* against the
     vouchers already inside Tally, including bills someone keyed in by hand.
   * **Checks**: arithmetic and GST: lines sum to the taxable value,
     qty × rate = amount, IGST vs CGST+SGST from the supplier's state and the
     place of supply, GSTIN check digits, totals.
4. **Post.** You choose: post to Tally, save as a **draft** (a Tally optional
   voucher that shows in the Day Book without touching your books until you
   mark it regular), or export XML to import yourself. Or **Reject** it.
   Posting before the AI has finished checking a bill is allowed; it asks
   first.
   A rejected bill stays in the list for 15 seconds, with *Undo*, then
   moves to the Rejected tab. There, *Put it back* returns it to where it
   was.

### Posted the way your books are kept

Measured on ten real scanned bills (Sept 2026) against the entries keyed by
hand in Tally: 4 of 10 came out right before, 10 of 10 now.

* **Ledgers:** the purchase ledger for the line's GST rate
  (*PURCHASE @18% GST*, the IGST one on an inter-state bill, the exempt one
  at 0%); the round-off ledger wherever it is (*Rounded Off*); and, before
  either, the ledger the item goes to in your own vouchers.
* **Numbers:** with Manual numbering the bill's number is the voucher
  number, as you key it; quantities go in the stock item's own unit.
* **Items:** *Sync* also reads a year of your vouchers: who you bought which
  item from. That decides between close names (one item kept by the foot and
  by the square foot, say) and names a one-item supplier's lines, but never overrules a size
  or grade written on the bill. A supplier or item scoring under 75% is
  offered, never picked.
* **Amounts:** the bill's own arithmetic settles a misread. Of two readings,
  the one that adds up wins. A tax a rupee off its rate is that figure (a
  paise column missed). What is left under a rupee is the round-off. The
  amount in words is a second reading of the total. A quantity one digit off
  amount ÷ rate is that figure. Packing and cartage are charges, posted to
  a ledger beside the items. Every such change shows as a warning to check
  on the bill.

### What the AI does, and what it never does

The AI is a reader and nothing else.

**In scope**
* **Reading.** It copies text and values off the page exactly as written,
  like an OCR engine that can also read handwriting and Hindi. It reads only
  what the rules could not find.
* **Verifying.** It reads each scan a second time, independently of the OCR,
  in the background once the bill is up for review. Where the two readings
  differ, you see both, and the value is marked uncertain.

**Out of scope, enforced in code**
* No chat, no answers, no explanations, no advice.
* No translating or transliterating. A Hindi name stays as written. After
  you map it to your ledger once, it is matched by itself from then on.
* No calculating, inferring, guessing, rounding or completing a value. Digits
  and dates are converted by the app's own parser, not by the AI.
* No choosing voucher types, ledgers or tax treatment. No duplicate judgement,
  no approving, no posting, no memory between bills.

**Guardrails**
* Its answer is restricted to a fixed set of text fields. Ollama enforces that
  while the model writes, so it cannot reply in any other form.
* The app parses every value it returns. A GSTIN that fails its check digit is
  dropped.
* It never overwrites a value the rules read, and never touches line items
  that add up. Nor anything you changed, or a bill in Tally or rejected: there
  what it reads differently is only a warning.
* Everything it read shows in amber. An entry with anything the AI read never
  counts as clean, and bulk approval refuses it on the server.
* Text on a bill is treated as content to copy, never as an instruction.
* It runs on this computer, and the app works without it.

**Duplicates warn, they never block.** A similar bill (same party, same
amount, different number) is shown as a warning. A likely duplicate (same bill
number already in Tally) needs one tick, "this is a different bill", and then
posts. Tally is always checked again at the moment of posting, and if that
check cannot run, the app says so rather than posting blind. Every override
is recorded in the audit log.

---

## Install

The release folder, `installer\dist\release`, holds:

| | |
|---|---|
| `Install.exe` | the installer: one file of about 150 MB (zip it for mail and chat apps that refuse `.exe` files) |
| `Invoice Bridge - Guide.pdf` | the guide |

Run `Install.exe`. No admin rights are needed.
* **Where it goes:** choose any folder you can write to, for example
  `D:\Apps` on another drive. The default is
  `%LOCALAPPDATA%\Programs\Invoice Bridge`. A folder that needs admin
  rights, such as `C:\Program Files`, is refused with a message rather than
  failing half way through.
* **What it installs:** its own Python and every dependency, and the OCR
  models. Then `get-ai.ps1` downloads the AI reader, meaning the Ollama
  runtime (v0.34.4, checked against its published checksum) and the model,
  about 5 GB in all. It goes into the same folder, and a window shows the
  progress. The AI reader is not in the installer and not in git.
  Setup needs about 8 GB free, and the internet once. If the download is cut
  off, or setup ran without the internet, **Settings > Local AI model** has an
  *Install the AI* button: the same download, into the same folder, shown as
  it goes, and it carries on where it stopped (so does running `Install.exe`
  again). The AI switches on when it is done, without a restart. Until then,
  the app reads bills without the AI. This Ollama runs only on the app's own port, is
  not added to Windows start-up, and is removed on uninstall. An Ollama you
  installed yourself is left alone.
* **Shared with Assurance Console:** that app runs the same AI reader. Each
  app notes where its own copy is in `%LOCALAPPDATA%\Shared AI`, and an app
  without a copy runs the other's instead of downloading the same 5 GB again:
  as its own process, on its own port and inside its own CPU cap, reading the
  files only. Setup then downloads nothing, and Settings says whose copy it
  is. Uninstalling the app that has the copy takes it away from the other
  too, which then offers its own download.
* **Shortcuts:** desktop and Start menu. The app is also listed in Add/Remove
  Programs.
* **Updates:** running a newer `Install.exe` over an existing copy closes it
  if it is open and updates it in place.

Start it from its shortcut. It opens in a window of its own, drawn by
WebView2, the browser engine built into Windows. There is no console: logs are
in the data folder under `logs`, and a failed start is explained in a dialog
box. The AI model is loaded as the app starts, so the first bill does not wait
for it. Closing the window stops everything the app started. Opening it again
while it is running brings the open window to the front. If WebView2 is
missing, it opens in your browser instead, with a small box whose OK button
stops it.

### It uses at most half of the computer

The launcher holds the whole app to 50% of the machine: the app, the OCR and
the AI runtime alike. Measured every second on an i7-13650HX with an RTX 4050,
while reading bills:

| | Limit | How it is held | Measured |
|---|---|---|---|
| CPU | 50% | Windows enforces a hard cap (a job object, set to 38%), and the AI and OCR use no more threads than that | highest second 46.5%, average 33% |
| GPU memory | 50% | the model is fitted to leave 55% of the card free; the rest of it runs on the CPU | highest 45.5% |
| GPU busy time | 50% | after each reading, the GPU rests as long as it worked | average 19% |

The GPU still reaches 100% in bursts of a fraction of a second while reading;
that was 5% of the readings. A consumer graphics card offers no way to cap one
program's GPU use from moment to moment. The only way to rule the bursts out is
not to use the GPU at all.

Without a GPU, the CPU stayed at or under 35% every second. The price is speed:
* with the GPU, a one-page bill took about 40 s, against 12 s unlimited;
* without a GPU, it took about 60 s.

`python installer\check_limits.py` checks the CPU cap on any PC.

Your data lives in `%APPDATA%\Ultimate Accountant\bridge` (a data folder left
by an older stand-alone install, `%APPDATA%\TallyInvoiceBridge\data`, is used as
it is): the database, the original documents, exported XML and backups.
Uninstalling never removes it.

The installer is not code-signed, so Windows SmartScreen will warn the first
time: *More info → Run anyway*. Signing needs a paid certificate.

### Connecting Tally

1. Open TallyPrime with your company loaded.
2. **F1 (Help) → Settings → Connectivity → Client/Server configuration**: set
   *TallyPrime acts as* to **Both**, port **9000**.
   (Tally.ERP 9: F12 → Advanced Configuration.)
3. In the app: **Settings → Discover companies**, then **Sync** each company.
4. To post, tick **Allow this app to write to Tally** in Settings. Until you
   do, the app only reads from Tally and exports XML.

Sync copies ledgers, stock items and voucher types to this computer, so
reviewing and matching work with Tally closed. Tally only needs to be open to
sync, to check for duplicates, and to post.

**Tally on another computer** on the same network: in the app's Settings, put
that computer's address in **Host** (its IPv4 address from `ipconfig`, such as
`192.168.1.6`, or its computer name). On the Tally computer, Windows Firewall
has to let port 9000 in. In an administrator PowerShell there, with your own
computer's address as the remote one:

```powershell
New-NetFirewallRule -DisplayName "TallyPrime 9000" -Direction Inbound `
    -Protocol TCP -LocalPort 9000 -RemoteAddress 192.168.1.15
Get-NetFirewallRule -Direction Inbound -Action Block |
    Where-Object { $_.DisplayName -match 'tally' } | Remove-NetFirewallRule
```

The second command removes the rule Windows makes if its firewall prompt for
TallyPrime was ever cancelled; a block rule wins over any allow rule. An
antivirus with a firewall of its own needs port 9000 allowed as well. Check
from this computer by opening `http://<address>:9000` in a browser: it says
*TallyPrime Server is Running*. Tally's port has no password, which is why the
rule lets in only one computer.

**Several companies.** Discover finds every company open in Tally, and each
keeps its own ledgers, remembered names and duplicate checks. A bill goes to
the company whose GSTIN is on it; failing that, to the one chosen at upload.
A bill that names none of them, a kachcha bill for one, is never guessed: it
waits with *Choose company* until you pick one on the review screen. Moving a
bill to another company there matches its ledgers again from that company's
books. Two companies with one GSTIN (one firm split by year) take the one
chosen at upload, or ask.

### Bills in Tally

TallyPrime cannot keep a file inside a voucher, so the voucher gets a link to
its bill instead, in a field of its own. Turn it on in **Settings > Bills in
Tally**, after loading the Tally add-on it offers there (on the PC where you
work in Tally: F1 > TDL & Add-On > F4 Manage Local TDLs). The link shows under
the narration and Ctrl+Alt+O opens the bill. This computer serves the bills to
the network on port 8766: read-only, only bill files, each under its SHA-256.
It must be on when a bill is opened from Tally. Off by default.

### From your phone

**Upload > Send bills from your phone** shows a QR code. Scanned with the
phone's camera, it opens a page on the phone: photograph a bill (more pages
if it has them), say what it is, send it, and on to the next. Each bill is
read here exactly as an upload from this computer is, and waits in Review;
the card on the upload screen and the phone both show how far each has got.
The phone must be on the same Wi-Fi. This computer serves that page on port
8767, and only to the current code: each code carries a fresh random key,
and a new code, *Stop*, a restart of the app or twelve hours ends it. No app
is installed on the phone, and nothing but the page, its fonts and the
sending is served on that port. Off until you show a code.

### Bank statements

**Bank** reads a statement's PDF, downloaded or scanned, and shows it as the
bank printed it: date, value date, details, reference, debit, credit and
balance. Opening a row shows that part of the page beside what was read. The
running balance checks every row to the paisa. Where a scan misread a sign or
a digit, the balances put it right and say so; a row they cannot square is
marked for you to check.

The statement finds its company and bank ledger by the account number in the
ledger's name. Each row gets the ledgers it goes to, from what the same
account, number or name went to before, or else from your ledgers' names.
Bank charges, their GST (in CGST and SGST halves), interest and cash go by
their kind, and a returned transfer goes back to its payment's ledger.
*Look in Tally* marks the rows that are posted already, whether one voucher
each or joined into one (a payment with its charges), and learns from them.
So a month already keyed teaches the app how you post the next one.

Nothing is posted until you approve it: a row's *Post to Tally*, or the rows
you tick, each asking first. Money out is a Payment and money in a Receipt,
one voucher per row, numbered by Tally, with the ledgers Dr and the bank Cr
(or the other way). Just before posting, the app looks in Tally again, so a
line keyed in by hand meanwhile is not posted twice.

### Updates

The bridge is part of Ultimate Accountant and is updated with it. The
installed app asks the release page of the public GitHub repository named in
`UA_UPDATE_REPO` (see [configuration](configuration.md)) for a newer version,
shortly after start and every six hours, and offers **Restart and update** in
Settings. Nothing is needed to read that page. An update is usually
`Update.exe`, the app's own code, a few MB; a release built on another base
(`_internal/BASE` in the install folder) asks for the full installer instead.
The release file is signed with the deployer's own Ed25519 key,
`%USERPROFILE%\.ultimate-accountant\release-signing-key.pem` (made once with
`python packaging/publish_release.py --make-key`), which never leaves the
publisher's PC; the app checks it against the public half, set as
`UA_UPDATE_PUBLIC_KEY`. A release that fails the signature, or a download that
fails its fingerprint, is thrown away.

To publish an update:

```powershell
python packaging/build_release.py
python packaging/publish_release.py "What is new"
```

The details are under *Publishing an update* in
[reconciliation-and-exports.md](reconciliation-and-exports.md).

---

## Development

```powershell
.\scripts\dev.ps1            # API on :8765 (auto-reload) + UI on :3000
.\scripts\dev.ps1 -Test      # the self-check: 259 assertions, starting from a sample invoice
.\scripts\dev.ps1 -FakeTally # also start a simulated Tally on :9000
```

`backend/tests/fake_tally.py` answers the same XML requests TallyPrime does,
with two companies whose books are kept apart, and Tally's habits: unescaped
`&` in ledger names, and `&#4;` (a control character XML does not allow) in
front of built-in names such as Primary. So the whole flow can be exercised
without Tally. It models Tally; it is not Tally.

### Building the installer

```powershell
.\installer\build.ps1                  # the installer, ~150 MB; setup downloads the AI
.\installer\build.ps1 -CpuOnly         # setup keeps no NVIDIA GPU libraries
.\installer\build.ps1 -Model qwen3.5:9b  # setup downloads another model
.\installer\build.ps1 -SkipOllama      # no AI step: text and OCR only
.\installer\build.ps1 -SkipInstaller   # stop at the portable folder, installer\stage
.\installer\build.ps1 -UpdateOnly      # just Update.exe (see Updates)
```

Needs [Inno Setup 6](https://jrsoftware.org/isdl.php) for the final `.exe`.
Without it the build still produces `installer\stage`, a working portable
copy: run `python\pythonw.exe launcher.py` inside it. The build copies the
guide into the release folder. To rebuild the guide itself, run
`python installer\guide\build_guide.py`.

`installer\get-ai.ps1` is the step setup runs. It keeps CUDA 13 (NVIDIA
drivers from 580 on) and drops CUDA 12. Older NVIDIA cards use Ollama's
Vulkan backend, and PCs without a GPU use the CPU. To put the AI into the
portable folder, run
`powershell -File installer\get-ai.ps1 -Dest installer\stage -Downloads installer\.cache`,
which takes the Ollama zip from the build's cache.

`backend/requirements.txt` is a freeze of the environment the self-check
passes in, and the build installs exactly that. To upgrade a package: upgrade
it in `backend\.venv`, run the self-check, re-freeze.

---

## Layout

```
backend/app/services/
  parse.py         rule-based extraction; runs first and wins ties
  llm.py           the AI reader: copies off the page what the rules miss, and
                   re-reads scans to check the OCR; its scope is written at the top
  gst.py           GSTIN checksum, state codes, valid GST rates
  validate.py      arithmetic and GST checks
  matching.py      party/item matching; GSTIN > your past corrections > name similarity
  vouchers.py      what each voucher type means in debits and credits
  voucher_xml.py   builds the Tally voucher; refuses one that does not balance
  dedupe.py        duplicates here and inside Tally
  tally.py         TallyPrime XML-over-HTTP client
  pipeline.py      document in, open entry out; settles misread amounts
  ai_check.py      the AI's reading in the background, reconciled with the entry
  bill_links.py    serves bills to Tally, and the Tally add-on that opens them
  phone.py         the QR code and the page a phone sends bills from (app/phone.html)
  ai_setup.py      installs the AI reader from Settings when setup could not
  shared_ai.py     one copy of the AI reader for this app and Assurance Console
  statement.py     a bank statement's rows, as printed; the running balance settles misreads
  bank_match.py    what a bank line posts to, learnt by account/number/name; already in Tally?
  updates.py       the update check, from the signed release page
backend/app/routers/   the HTTP API the UI uses
backend/tests/         self-check, fake Tally, the sample invoice,
                       vision_eval.py: scores a model reading bills off the page image
frontend/              Next.js UI, exported static and served by the backend;
                       components/TallyVoucher.tsx is the review screen's voucher
installer/             launcher, build script, Inno Setup script,
                       get-ai.ps1: the step of setup that downloads Ollama and the model
```

---

## Known limits

* **Handwriting and Hindi are read by the AI, not the OCR.** On test bills
  (`tests/vision_eval.py`, qwen3.5:4b on an RTX 4050), the scores were:
  * a printed Hindi kachcha bill with handwritten figures: 10/10 fields
    right, in 8 s;
  * a typed supplier's invoice as a 2-page scan: 10/10, in 17 s;
  * a printed Hindi invoice written in **Devanagari digits**: 6/10. The
    digits ४ ८ ६ ९ ५ get confused, so check every number on such bills.

  None of this has been measured on real handwritten Hindi yet. Run
  `python tests/vision_eval.py qwen3.5:4b --dir <folder of bills>` on yours.
* **Hindi names are not matched to English ledgers until you map them once.**
  The AI never translates, so the first bill from a supplier needs you to
  pick the ledger. After that it is remembered.
* **New layouts.** The rules cover common GST invoice layouts. The AI reads
  what they miss on unseen ones, and each correction you make is remembered.
* **After posting**, change the voucher in Tally; the app does not sync edits back.
* **A bank payment to a bill-wise ledger goes On Account.** Setting it against
  the supplier's bills (Agst Ref) is still done in Tally. Handwriting on a
  scanned statement is kept as the row's note, as the OCR read it.
* **The bill-link add-on has not been tried in a real Tally yet.** It is
  written from Tally's TDL reference. Tally keeps the link even without it,
  but you only see the link, and the Ctrl+Alt+O key, once it is loaded.
* **Reading from a real TallyPrime is tested; writing to it is not yet.** On a
  real company's TallyPrime, over Wi-Fi from another computer, the app read
  the company, its ledgers (hundreds), stock items (thousands) and voucher
  types. Posting
  has so far been tested against the simulated Tally only: start with *Show
  XML*, then *Save as draft in Tally*, before posting for real.
