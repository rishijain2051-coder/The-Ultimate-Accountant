# Exporters module and separate runs: research and build plan

Prepared 25 September 2026. The new RBI export rules start in 6 days (1 October 2026), and
GSTR-1 amendments for FY 2025-26 (Table 9A, which is how most Table 6A mistakes get fixed)
close on 30 November 2026. Both dates make this a good time to build it.

## Where it stands (26 September 2026)

Exports is built as the console's second module, beside Reconciliation, switched from the
cover band. Two things changed from the plan below while building it:

- **It is a register, not a one-off check.** An exporter keeps one register and drops
  documents on it as they arrive — the shipping bill at clearance, the bill of lading when the
  vessel sails, GSTR-1 at the month end, the bank's advice when the buyer pays — instead of
  keeping a spreadsheet of what has come for which invoice. Each document is filed against its
  invoice and the originals are kept, so every invoice has its papers in one place.
- **eBRCs follow eBRC.in's lead, without generating them:** a ledger of every remittance and
  what it is counted against, suggestions confirmed with one click (or refused, which moves
  the suggestion on), an eBRC register, and DGFT's bulk sheet filled in and checked row by row.

| Phase | State |
|---|---|
| 1. Separate runs | Done earlier. Registers are runs with `module = 'exports'` |
| 2. Import, pre-filing checks, deadline tracker | Done: `exim_read.py`, `exim.py`, `exim_rules.json` (with the Settings choices from 5.6), the bank's EDPMS statement, ICEGATE's refund and drawback status with the SB000-SB006 codes, and the exporter's own register (26 Sep). Not yet read: the GST failed-invoice list, the export ledger CSV |
| 3. Drafted documents | Done (26 Sep), in `exim_drafts.py`: SB005 pack per port, Table 9A worksheet, SB003 undertaking, EDPMS declaration, extension request, ICEGATE look-up list, the register in the exporter's own layout. Rule 96B schedule still to do |
| 4. Sample, tests, tutorial, exe | Sample (`tools/synth_exim.py`, 25 invoices, 39 planted problems) and self-tests done; 2.1.3 built and installed (27 Sep: party Dr/Cr column, the credit working deducting a bill once, ₹ in the PDFs); the tutorial is now a 60-page PDF guide (media/guide), Exports included |
| 5. Optional connections | Not started |
| Kept files (both modules) | Done (26 Sep): every uploaded file kept under `data/documents/<run or register>/<kind>/`, named for what it is; open, save a copy, share, show in folder, a zip of an invoice's papers or of everything. In 2.1.1 |

The screens in 5.5 became one register page (verdict and figures, the invoice register, what
needs a look, money received, eBRCs, documents, downloads) in the existing design system, with
figures instead of the chips and timeline sketched there.

---

## 0. The short version

- **The problem is real, and it's been confirmed.** SB005 (the invoice number on the shipping
  bill doesn't match GSTR-1 Table 6A) stops the IGST refund. For shipping bills already
  filed, the fix is a request letter, a concordance table (Annexure A) and an original TR
  challan of ₹1,000 per shipping bill. That route has been available for every shipping
  bill, whatever its date, since Circular 05/2021-Customs.
- **The deadline rule in the pitch is incomplete.** The payment deadline depends on the
  date the goods shipped, and four different rules have applied in the last year: 9 months,
  then 15, then back to 9, then 15 (or 18 for INR invoices) from 1 October 2026. A tool that
  applies "15 months" to every bill gets bills shipped between June and September 2026 wrong
  by six months.
- **"No cheap self-serve tool" is not right.** At least five exist, all cloud-based:
  NIRYAT (the closest match), eBRC.in (free), Shipzy, BharathExim and Seasaw. None of them
  runs locally, none is built for a CA handling many clients, and none drafts the SB005
  paperwork. That is where we fit.
- **ICEGATE has no API for exporters.** Everything we get from ICEGATE comes from files the
  user downloads: shipping bill PDFs and the IGST validation and scroll status pages. The
  GST portal also works through downloads (GSTR-1 JSON, the failed-invoice list, the export
  ledger CSV). The bank side comes from the AD bank's EDPMS outstanding statement. DGFT is
  the only one with a real API (IRMs and eBRCs), but it needs a fixed public IP address and
  consent signed with a DSC, so it goes in last and is optional. We won't scrape portals.
- **Runs become separate by default.** A run is one client and one period. Inside it sit the
  reconciliations that belong to it: GST, bank, party statements, and now exports. Starting
  a reconciliation creates a new run unless the user chooses "Add to this run". The app
  opens the last run you had open and nothing else. Decisions belong to their run. Each
  existing stored result moves into a run of its own.
- **Build order:** (1) separate runs, (2) exports: file import, pre-filing checks and the
  payment deadline tracker, (3) drafted documents, (4) sample data, tests, tutorial and a
  new exe, (5) optional DGFT API connection.

---

## 1. What the research confirmed, corrected and added

### 1.1 Claims in the pitch

| Claim | Verdict | Evidence |
|---|---|---|
| An invoice number mismatch between the shipping bill and GSTR-1 6A freezes the IGST refund (SB005) | **Confirmed.** ICES matches the two at invoice level, on GSTIN, shipping bill number and invoice number | ICES IGST refund guide [3]; Chennai Customs [2] |
| The fix needs a matching table and a ₹1,000 challan per shipping bill | **Confirmed.** Request letter to the AC (IGST Refunds), a shipping-bill-wise concordance table (Annexure A), and the original TR challan of ₹1,000 per shipping bill. Available for all past shipping bills since Circular 05/2021-Customs (17 Feb 2021) | [2], [4], [5] |
| Consultants sell the cleanup as a service | **Confirmed** (VJM Global, EXIMINQ and others advertise it) | [22], [23] |
| New RBI rules from 1 Oct 2026, with 15 months (18 if invoiced in rupees) | **Confirmed.** FEMA 23(R)/2026-RB, notified 13 Jan 2026 | [6], [7], [8] |
| Bills up to ₹10 lakh can be closed in EDPMS on the exporter's declaration | **Confirmed, and it goes further:** reduction or non-realisation up to ₹10 lakh is also allowed on declaration, and declarations can be sent quarterly in bulk | [6] |
| No cheap self-serve tool exists | **Not right.** See section 2 | — |

### 1.2 Corrections that change the product

**1. The payment deadline follows the shipment date, and four rules have applied.**

| Shipped on | Payment must arrive within | Rule | Example |
|---|---|---|---|
| before 14 Nov 2025 | 9 months | FEMA 2015 regulations | shipped 10 Oct 2025, due 10 Jul 2026 |
| 14 Nov 2025 to 4 Jun 2026 | 15 months | FEMA 23(R)/(7)/2025-RB, 13 Nov 2025 | shipped 1 Dec 2025, due 1 Mar 2027 |
| 5 Jun 2026 to 30 Sep 2026 | **9 months** | FEMA 23(R)/(8)/2026-RB, 5 Jun 2026 | shipped 15 Jul 2026, due **15 Apr 2027** (15 months would say 15 Oct 2027) |
| from 1 Oct 2026 | 15 months; 18 months if invoiced or settled in INR | FEMA 23(R)/2026-RB | shipped 5 Oct 2026, due 5 Jan 2028 (INR: 5 Apr 2028) |

Sources: [9], [10], [11], [6]. Bills shipped under the 15-month window keep it after the June
reversal. That reading comes from secondary commentary [10], so the app treats it as a
setting that can be changed (section 5.6). The AD bank can extend any bill, so every bill
also gets an override for its due date.

**2. SB005 has two fixes, and which one applies depends on which document is wrong.** The
officer-interface route assumes GSTR-1 is correct and the shipping bill is not [1]. ICES
also says an exporter who put a separate commercial invoice on the shipping bill cannot put
that number into GSTR-1 in place of the GST invoice [3]. So:

- **Typo in 6A:** amend it. Use GSTR-1A in the same month before GSTR-3B is filed, or
  Table 9A later, up to 30 November after the end of the financial year. This costs nothing.
- **The shipping bill carries a different invoice number (two invoice series):** use the
  officer interface. That means a request letter, Annexure A, and ₹1,000 per shipping bill.

Catching these before GSTR-1 is filed is worth the most: typos cost nothing to fix, and
genuine two-series cases can go to the port immediately.

**3. SB000 does not guarantee payment.** A bill can validate and still not be paid if: the
goods went under LUT or bond, the IGST is under ₹1,000, the bank account isn't validated in
PFMS, the IEC has an alert or suspension, one invoice on a multi-invoice bill failed, or a
higher drawback rate was claimed [2], [12].

**4. GSTN won't even send the data to ICEGATE if** GSTR-1 or GSTR-3B isn't filed, the IGST
in 6A is more than the IGST paid in GSTR-3B Table 3.1(b), or Aadhaar authentication or e-KYC
is pending [13], [14].

### 1.3 Things the pitch didn't mention that belong in the product

- **eBRCs now name the GST invoice.** Every eBRC generated since 13 January 2026 must carry
  the GSTIN, GST invoice number and GST invoice date [15], [16]. So the bank leg now points
  to the GST invoice directly, and the three-way match gets an official link.
- **Rule 96B makes a missed deadline a GST problem too.** If payment doesn't arrive within
  the FEMA period, the IGST refund (or unused ITC refund) must be repaid within 30 days, with
  interest, to the extent the money wasn't received [17]. The deadline tracker therefore
  also tracks GST exposure.
- **One bill more than a year overdue affects every future export.** Under the 2026 rules,
  once a bill is unrealised more than a year past its due date, further exports are allowed
  only against full advance payment or an irrevocable letter of credit [7].
- **DGFT has an official eBRC API.** Trade Notice 12/2024-25 (bulk upload and API) and
  technical spec v1.3 of January 2026 [18], [19]. Details in section 3.3.
- **ICEGATE Public Enquiries 2.0 went live in February 2026.** Every lookup is now one
  shipping bill at a time, and each needs the customs location picked from a list [20].

---

## 2. Competitors (searched harder) [25]

| Product | What it does | How it gets data | Price | Runs where | Gap we can use |
|---|---|---|---|---|---|
| **NIRYAT** (theniryat.com) | Compliance system for SME exporters: reconciles DGFT, ICEGATE, EDPMS and the bank; batch eBRCs in the January 2026 format; refund tracking (RoDTEP, drawback, IGST); EPCG/AA | Claims DGFT API, ICEGATE, EDPMS, a GSP (IRIS) and AD bank APIs (HDFC, ICICI); access granted by DSC or Aadhaar e-sign | Suggested ₹60K–2.4L a year per exporter; 90 days free for pilots | Cloud | Early stage (onboarding 10 design partners in Tirupur). Sells to exporters, not CAs. Doesn't mention SB005 concordance drafting. I found no public exporter API for ICEGATE or EDPMS, so its "live" claims for those two can't be checked |
| **eBRC.in** (Eximfiles) | Free eBRC generation from IRMs, an IRM ledger, bulk Excel upload, and a free extractor for shipping bill PDFs | DGFT connection | App free at any volume; API free up to 250 certificates a month | Cloud and Android | No 6A matching, no EDPMS deadlines, no drafting. It makes eBRC generation free, so we shouldn't compete on that |
| **Shipzy** | Export ERP: documents, eBRC automation, IRM view, a worklist of exceptions | DGFT API | Not published | Cloud | A full ERP, heavy to adopt. No shipping bill to 6A matching |
| **BharathExim** | AI eBRC platform that matches IRMs to shipping bills | DGFT APIs | Not published | Cloud | Focused on eBRCs |
| **Seasaw.ai** | E-commerce (courier, CSB-V) export compliance: shipping bill to EDPMS, settlement to eBRC, GST refund | Not stated | Not published | Cloud | Serves courier and e-commerce exporters only |
| Payment platforms (Skydo, Karbon/Infinity, Razorpay, PayGlocal, EximPe, Briskpe, Xflow) | FIRA and EDPMS help for money received through them | Their own payment rails | Bundled | Cloud | Only see their own receipts |
| GST suites (ClearTax, Masters India, IRIS/Sovos, Suvit) | Detailed GSTR reconciliation | GSP | Subscription | Cloud | I found no shipping bill to 6A matching. One search result said ClearTax does it; ClearTax's own page doesn't say so |
| Consultants (VJM Global, EXIMINQ, …) | SB005 cleanup as a service | Manual | Per case | — | This is the work we automate |

**Where we win:**
- **Local.** Client data never leaves the office. A CA holding 40 exporters' shipping
  bills and bank data has good reason not to upload them to a young startup.
- **Built for many clients.** One install covers every client of a CA practice.
- **Prevention, not cleanup.** We check before filing, where fixes are free.
- **Drafting.** Annexure A, the covering letter, the challan schedule and the ₹10 lakh
  declarations.
- **Correct deadlines** across all four rules.
- **One engine** that already handles GST, bank and party reconciliation.

**Where we don't compete:** generating eBRCs. DGFT does it for free, and so does eBRC.in. We
produce DGFT's bulk upload sheet and stop there.

---

## 3. Getting the data (ICEGATE, GST portal, DGFT, bank)

### 3.1 The honest picture

ICEGATE offers exporters no API. Its public enquiry checks one shipping bill at a time, and
the useful screens (IGST validation and scroll status, document download) sit behind the
exporter's login. EDPMS is a system for banks, and exporters see it only through their AD
bank's statements. The GST portal's APIs need a licensed GSP in between. **DGFT is the only
government system with a real API that an exporter's software can call.** So the design is
built on files: the user downloads from each portal as they already do, drops everything
into the app at once, and the app works out what each file is.

### 3.2 Sources, data points and what we build

| Source | Data points | How the user gets it | What we build |
|---|---|---|---|
| **ICEGATE: shipping bill copy** | SB number, date, port code, IEC, GSTIN, AD code, LEO date; invoice table (invoice no./date, value, currency, FOB, terms); item lines (HS, quantity, value); IGST taxable value and IGST paid, or LUT/bond; drawback/RoDTEP; exchange rate; EGM | ICEGATE login, then Document Download Utility (PDF), or from the customs broker (CHA) | Reader built on the existing text-PDF path, with the existing OCR as a fallback for scans. Consistency checks (do the invoice values add up to the FOB?), in the same spirit as the running-balance check on bank statements |
| **ICEGATE: IGST validation and scroll status** | For each shipping bill and invoice: response code SB000–SB006, scroll number, date and amount | Login 2.0 → Dashboard → Services → Enquiries → ICEGATE Enquiry Service → *IGST Validation Enquiry* / *IGST Scroll Sanctioned Status* [12] | A flexible table importer (saved page, Excel or PDF). **The exact format needs a real downloaded file to confirm** |
| ICEGATE: public enquiry | Status of one shipping bill; its EDPMS status | Services → Enquiry → Public Enquiries 2.0 → Document Status → Shipping Bill (number, date and location) [20] | Nothing. It's one lookup at a time. We show the user what to type |
| **GST portal: GSTR-1** | Table 6A: invoice number and date, value, port code, SB number and date, WPAY/WOPAY, rate, taxable value, IGST, cess; Table 9A amendments | JSON of the filed return (GSTR-1 → Prepare offline → Download), or the JSON/Excel that Tally or the offline tool produced **before filing** | A parser for GSTR-1 JSON (`exp` and `expa`: `inum`, `idt`, `val`, `sbpcode`, `sbnum`, `sbdt`, `exp_typ`, `itms[txval, rt, iamt, csamt]`) [21]. We also extend the existing Excel parser: it already recognises the EXP sheet but drops the port and SB columns, and the offline-tool template has no IGST column, so IGST has to be worked out from the rate |
| **GST portal: transmission status** | For each invoice: sent to ICEGATE or failed, plus the error | Services → Refunds → Track status of invoice data to be shared with ICEGATE → *Download failed invoices* [13] | Importer. The portal's actual status replaces our prediction |
| **GST portal: export ledger** | Running total of GSTR-3B IGST against 6A IGST by period | *View Export Ledger* → CSV [13] | Importer. Powers the "won't be sent to ICEGATE" check |
| GSTR-3B | IGST in Table 3.1(b) | 3B PDF or JSON | Small importer |
| **DGFT: eBRCs** | eBRC number, date and status; bill number (the invoice); SB number, date and port; realised amount in foreign currency; currency; realisation date; utilisation | My Dashboard → Repositories → Bills Repository → e-BRC → bulk download [19] | Importer |
| **DGFT: IRMs** | IRM number, date and status; IFSC; AD code; remittance date; currency; amount; outward (ORM) amount; amount available and used; remitter name and country; purpose code | DGFT eBRC module (view and export), or the API | Importer now, API connector later (section 3.3) |
| **AD bank: EDPMS outstanding statement** | SB number, date and port; value; amount realised; outstanding; due date; extension | Monthly from the bank, as Excel or PDF in each bank's own layout | Flexible importer, plus a preset for each bank's layout as we see real ones |
| FIRA / FIRC | Remitter, amount, currency, date, purpose code, IRM reference | PDFs from the bank or payment platform | Existing reader (text or OCR) |
| Bank statement | Foreign inward credits | The run's bank reconciliation has already read it | Reused within the same run |
| Books (Tally) | Export sales vouchers, buyer receivables | Existing sales register import | Reused from the outward reconciliation |

### 3.3 The DGFT API, and why it comes last

Base `https://apiservices.dgft.gov.in/genebrc/`: `getAccessToken`, `fetchIRMDetails`,
`fetchORMDetails`, `pushIRMToGenEBRC` (a sandbox exists), `getRequestStatus`,
`fetchEBRCDetails` [19].

What it needs:
- Registration on DGFT as an API consumer, linked to the exporter's IEC. The IEC holder
  approves access with a **DSC or Aadhaar e-sign**.
- **Calls only from a whitelisted, fixed public IP address.** A new IP takes 24 hours to
  activate.
- A downloaded credential file holding a client ID and secret, an API key and RSA keys,
  valid for one year.
- Encryption and signing on every call: PBKDF2-salted secret, AES-256-GCM payload, RSA-OAEP
  key wrap, RSA-SHA256 signatures.
- Tokens that last 5 minutes, queries of at most 3 months at a time, and a 2-hour wait
  before the status of a submission can be read.

Most CA offices and small exporters don't have a fixed public IP. Our app runs on their own
PC, so the file route has to be the default. The API becomes an optional connection for
users who do have one, and it only reads IRMs and eBRCs; the app never submits anything to
DGFT. The encryption library it needs (`cryptography`) is already installed, so it adds no
new dependency.

### 3.4 What we won't do

- Scrape ICEGATE or the GST portal, or automate their captchas. It breaks their terms and
  would stop working every time the portals change; the February 2026 change would already
  have broken it.
- Store portal passwords. The user logs into each portal themselves.
- Sign up as a GSP ourselves. If direct GST API access is ever worth it, we partner with an
  existing GSP.

### 3.5 Importing files

One drop area that takes a whole folder. For each file, the app shows what it thinks the
file is, how many rows it found, how confident it is, and any problems, all before
reconciling. A file it doesn't recognise opens a column mapper, and the mapping is saved as
a preset for next time. This is the step a CA with 200 files a month cares about most.

---

## 4. Separate runs

### 4.1 What's mixed today, and where in the code

- `restoreLatestRuns()` (`app/frontend/js/app.js:437`) loads **the newest result of each
  kind** at start-up. That can put client A's GST, client B's bank and client C's party
  statement on screen together.
- `st.result`, `st.bank` and `st.stmt` (`app.js:18-20`) hold three results that have
  nothing to do with each other. The strip at the top changes whose data it shows depending
  on the section (`renderStrip`, `app.js:295`).
- The rail always shows all 12 sections, whatever is loaded (`NAV`, `app.js:63`).
- Decisions (manual matches, notes) are stored against the fingerprint alone
  (`store.py:68`, `fp PRIMARY KEY`), so a decision made in one run applies to every run.
- `open_items_across_runs()` (`store.py:379`) mixes every client together, and the
  bootstrap sends that list on start-up.
- The client name and GSTIN are global settings copied into every new run
  (`prefillLanding`, `app.js:482`).
- History shows GST columns (documents in 2B, ITC at risk) for bank and party results too
  (`views.js:1132`).

### 4.2 The model

**A run is one client and one period** (name, client name, GSTIN, IEC, period, created and
updated dates, archived flag, notes). Inside it sit its **parts**: GST (purchase and
outward), Bank (one per account), Party statements (one per party), Exports, and documents
read while the run was open.

Rules:

1. **Every new reconciliation starts a new run**, unless the user chose to add to the one
   that's open.
2. **"Add to this run"** is always available inside a run. It opens the start screen with
   the client and period filled in and locked, and shows what the run already contains.
3. **Running a part again replaces it** inside the run. The old version stays in that run's
   history.
4. **At start-up the app opens the last run you had open.** Nothing else is loaded.
5. **Decisions and notes belong to their run.** Bringing them forward is a deliberate
   choice ("Start from the last run's decisions") when creating the next run for the same
   client.
6. **Views across runs appear only when asked for:** Compare (two runs of the same kind,
   normally the same client) and Follow-ups across runs (filtered by client).
7. **Parts inside one run can use each other.** Exports reads the bank part's foreign
   credits, and the party statement's GST check reads the GST part. That's the benefit of a
   run holding several reconciliations.

### 4.3 Data changes

- New table `workspaces` (id, name, client_name, gstin, iec, period_from, period_to,
  created_at, updated_at, archived, notes). The interface calls these **runs**. In the code
  the word `runs` already means one engine's result, and renaming it would touch every
  export route for no visible gain.
- `runs` gets `workspace_id` and `part_label` (for example "SBI 3810…" or "Supplier:
  Dungarpur Fabricators"). Existing IDs, download routes and exports stay as they are.
- `decisions` is keyed by `(workspace_id, fp)`.
- The migration runs once, controlled by `PRAGMA user_version`:
  1. Copy `reconcile.db` to `reconcile.pre-runs.db` first.
  2. Give each existing result a run of its own (separate by default).
  3. Copy each old decision into every run that contains its fingerprint, and keep the old
     table as `decisions_legacy`.
- The client name and GSTIN stop being global defaults. Settings keep only app preferences
  and default tolerances.

### 4.4 API and screen changes

- **API:** `POST/GET/PATCH/DELETE /api/workspaces`. Every reconcile endpoint takes
  `workspace_id`; without one, it creates a new run. `/api/bootstrap` returns the list of
  runs and `last_workspace_id` instead of loose results. Decisions and open items are
  limited to one run.
- **Screens:**
  - The strip shows the run (client and period) and stays the same in every section.
  - The run picker lists runs grouped by client.
  - The rail shows only the parts this run has, plus "Add to this run".
  - The start screen has two modes: *New run* and *Add to <run>*.
  - History lists runs by client; each opens to show its parts, and comparison works within
    a client.
- **Sample data:** one "Sample run: Kesari Textile Mills" holding GST, Bank, Party and
  Exports together. It shows adding to a run without anyone having to try it.

### 4.5 Done when

- Opening the app shows the last run and nothing from any other.
- A new GST reconciliation creates a new run. Adding a bank statement inside it keeps the
  same run, and the GST part doesn't change.
- Another client's data never appears in the rail or strip.
- A decision made in run A doesn't show in run B.
- An old database comes through the migration intact, with one run per old result.

---

## 5. The Exports part

### 5.1 Matching keys

- **ICEGATE's natural key:** port code + SB number + SB date.
- **The GST side:** GSTIN + invoice number + invoice date, plus the shipping bill details on
  each 6A line.
- **The bank side:** each eBRC carries the shipping bill details, the bill number, and
  (since January 2026) the GST invoice number and date, which gives an official link to the
  GST side.

### 5.2 Before filing: checking 6A against the shipping bills

| Predicted outcome | What we check | Fix | Cost / time limit |
|---|---|---|---|
| **SB001** Invalid SB details | The SB number, date and port on the 6A line match no shipping bill. We suggest the closest one (same invoice number, SB number one digit off, date within 3 days, port code typo) | Amend 6A (GSTR-1A this month, Table 9A later) | Free; 9A closes 30 Nov after the financial year ends |
| **SB003** GSTIN mismatch | The shipping bill's GSTIN isn't the one on the return; same PAN means another branch | Amend 6A if the return is wrong. If the shipping bill is wrong and the PAN is the same, use the undertaking route (Circular 15/2018) [24] | Free; we draft the undertaking |
| **SB005** Invalid invoice number | The shipping bill matches but the invoice number or date differs. We classify it as a typo or a separate invoice series (every invoice on the shipping bill follows a different numbering pattern) | Typo: amend 6A. Separate series: officer interface (letter + Annexure A + TR challan) | ₹1,000 per shipping bill |
| **Won't be sent to ICEGATE** | 6A IGST (plus 6B) is more than GSTR-3B Table 3.1(b) IGST for the period; GSTR-3B not filed; SB fields blank | Pay the difference in the next GSTR-3B; fill in the SB details | Interest if tax was short |
| **Refund short** | IGST on the shipping bill differs from IGST in 6A. We show both; the difference is the refund at risk | Correct whichever side is wrong. The officer can change IGST on the shipping bill for short shipment or a calculation error [4] | — |
| **SB000 but no refund** | WPAY in 6A but the shipping bill says LUT/bond; IGST under ₹1,000; higher drawback claimed; one invoice failing on a multi-invoice shipping bill | Fix the failing invoice; pick the right scheme next time | — |
| **Duplicate** | The same invoice appears twice in 6A, or one shipping bill invoice matches two 6A lines | Remove the duplicate | — |
| **Dates that don't make sense** | Shipping bill dated before the invoice; shipping bill in a later month than the 6A return; LEO date missing | Review | — |
| SB002 / SB006 | Only known once the ICEGATE status file is imported | The shipping line or airline files the EGM or gateway EGM | — |

Any character-level difference counts as a predicted SB005, because we don't know how ICES
compares text and should assume it's exact. The typo vs separate-series label only decides
which fix we recommend.

### 5.3 Payment deadline tracker

For each shipping bill:
- **Value:** FOB in foreign currency, and in INR at the shipping bill's exchange rate.
- **Received so far:** the sum of eBRCs and IRMs mapped to it, with any shortfall from bank
  charges shown separately.
- **Deadline:** the amount still outstanding, the due date from the rule table (with its
  label shown on every bill) or the AD bank's extension, and the days left.

Each bill falls into one of: **Paid, Part-paid, Due within 30 days, Due within 90 days,
Overdue, Overdue by more than a year** (the last one means future exports need advance
payment or an LC).

Flags on each bill:
- **Up to ₹10 lakh:** eligible for closure (or reduction) on the exporter's declaration,
  from 1 October 2026.
- **Rule 96B exposure:** the refund received times the unpaid share, due 30 days after the
  deadline.

Matching receipts to shipping bills:
1. **Exact:** an eBRC already exists for the bill.
2. **Suggested:** the remitter name resembles the buyer, the currency matches, the amount is
   within tolerance after bank charges, and the money arrived after the shipping bill date
   (money that arrived earlier is marked as an advance).
3. **Split cases:** several IRMs paying one shipping bill (part payments), or one IRM split
   across several bills, including payments in a different currency from the bill.

**Every suggestion needs a click to accept.** Money is never matched silently.

### 5.4 Documents we draft

Each one says it is a draft for the user to check. The port or bank's own format takes
precedence.

1. **Annexure A concordance table**, one per port. Columns: SB number/date, port, GST
   invoice number/date, the matching shipping bill invoice number/date, taxable value per GST
   and per shipping bill, IGST per GST and per shipping bill, final IGST [4]. It comes with
   a certification block, a **covering letter to the AC (IGST Refunds)**, and a **challan
   schedule** (number of shipping bills × ₹1,000).
2. **Table 9A amendment worksheet:** original invoice to corrected invoice, with shipping bill
   details. For mistakes on the GST side, which cost nothing to fix.
3. **SB003 undertaking** for branches with the same PAN (Circular 15/2018).
4. **EDPMS closure declaration** for bills up to ₹10 lakh, one bill at a time or as the
   quarterly bulk schedule.
5. **Reduction / non-realisation declaration** for bills up to ₹10 lakh.
6. **Extension request letter to the AD bank** for bills close to their deadline.
7. **DGFT eBRC bulk upload sheet** mapping IRMs to shipping bills to GST invoices, including
   the GSTIN and invoice fields required since January 2026. The column list follows DGFT's
   published API fields; it gets checked against the portal's own template before release.
8. **Rule 96B exposure schedule.**

### 5.5 Screens (in the existing design system)

- **Exports overview:** refund at risk (₹), bills due within 90 days, total outstanding, and
  the Rule 96B exposure.
- **Before you file:** a grid grouped by predicted code, each row showing its fix and cost.
- **Payments:** a timeline by days left, plus a table with regime labels and ₹10 lakh chips.
- **Receipts:** IRMs and eBRCs mapped to shipping bills, with suggestions waiting for a
  click.
- **Drafts:** a document pack builder showing counts and the challan total.
- **Findings.**

As elsewhere in the app, colour marks status only.

### 5.6 Rules kept as data, not code

The rule table, the ₹10 lakh threshold, the ₹1,000 fee, the 9A cut-off and the transition
choices live in one rules file. Settings shows them with an "as of" date and lets the user
edit them. These rules changed three times in 12 months, so a change should mean editing a
value, not shipping a new release. The two uncertain transition cases are switches:

- Bills shipped 14 Nov 2025 to 4 Jun 2026 keep 15 months. On by default.
- The November 2025 extension also applies to bills shipped before 14 November that were
  still open then. Off by default, because the earlier deadline is the safer assumption.

---

## 6. Build plan

**Phase 1: separate runs** (medium; comes first because Exports is a new kind of part)
- **Store:** the `workspaces` table; `workspace_id` and `part_label` on runs; decisions keyed
  by run; the migration with its backup.
- **Server:** run (workspace) endpoints; `workspace_id` on every reconcile; bootstrap
  returns runs and the last-opened run; decisions and open items limited to one run.
- **Screens:** the run in the strip, a rail showing only that run's parts, Add to this run,
  the two start-screen modes, history grouped by client, and the combined sample run.
- **Tests:** migration, keeping runs apart, what opens at start-up, adding a part, deleting a
  run removes its parts.
- **Done when:** section 4.5 holds.

**Phase 2: Exports: file import, pre-filing checks, deadline tracker** (large)
- **Parsers:** GSTR-1 JSON; the existing Excel parser extended with SB columns and IGST
  worked out from the rate; ICEGATE shipping bill PDFs; the IGST status file; DGFT eBRC and
  IRM downloads; the EDPMS outstanding statement; the GST failed-invoice download; the export
  ledger CSV.
- **Folder import** that recognises each file, plus the column mapper for unknown files.
- **Engine** (`app/backend/exim.py`): the 6A vs shipping bill checks in 5.2, the deadline
  rules in 5.3, receipt matching, and the rules file from 5.6.
- **Done when:** on the sample data, every planted mistake is found with the right predicted
  code and fix, every deadline matches a table computed by hand, and no receipt is matched
  without a click.

**Phase 3: drafted documents** (medium)
- The eight documents in 5.4, as Excel and PDF, with the Annexure A pack grouped by port.
- **Done when:** each document's totals agree with the grid it came from, and the challan
  total equals ₹1,000 × the number of shipping bills in the pack.

**Phase 4: sample data, tests, tutorial, exe** (medium)
- `tools/synth_exim.py`, built on the existing generator, for the same fictional Kesari
  Textile Mills:
  - About 70 shipping bills across ports (Nhava Sheva, Mundra, Mumbai and Delhi air cargo,
    Chennai, and one inland container depot for SB006).
  - Bills with several invoices; USD, EUR, GBP and AED, plus some INR invoices (the 18-month
    rule).
  - Shipment dates spanning all four rules.
  - Text shipping bill PDFs and one scan.
  - A GSTR-1 JSON with planted mistakes of each kind (typos, a second invoice series, wrong
    port or SB number, another branch's GSTIN, IGST mismatches, WPAY vs LUT, a GSTR-3B
    shortfall).
  - IRM and eBRC files, and an EDPMS outstanding statement with part payments, bank-charge
    shortfalls, advances, one IRM paying several bills, bills under ₹10 lakh, and one bill
    more than a year overdue.
  - A `verify()` step that asserts minimum counts and checks that no real client names
    appear.
- **Self-tests:** the rule boundaries (13 Nov / 14 Nov 2025, 4 Jun / 5 Jun 2026, 30 Sep /
  1 Oct 2026), month-end dates (30 Nov + 15 months = 28 Feb), cleaning up invoice numbers,
  the ₹10 lakh test, Annexure A totals.
- A new Exports chapter in the tutorial video, then a rebuild of Install.exe.

**Phase 5: optional connections** (medium, each only when needed)
- The DGFT API, read-only (IRMs and eBRCs), for users with a fixed IP. Credentials are
  stored encrypted with Windows' built-in protection and can be revoked from Settings.
- Presets for each bank's EDPMS statement layout, and adapters for the files customs broker
  software produces.
- Later: service exporters (invoice ↔ FIRA/IRM ↔ EDF/SOFTEX), since the 2026 rules bring an
  EDF requirement for services.

---

## 7. Risks and unknowns

- **Real file layouts are unconfirmed:** ICEGATE's status downloads, each bank's EDPMS
  statement, DGFT's IRM export. Synthetic data tests the logic, not the layouts. We need two
  or three real, anonymised files from a pilot CA before these importers are final. Until
  then, the column mapper covers any file the app doesn't recognise.
- **The rules keep changing:** four regimes in a year. Keeping them as data (5.6) makes each
  change an edit to a value.
- **Transition treatment** for bills shipped 14 Nov 2025 to 4 Jun 2026 comes from secondary
  sources. Confirm against the RBI notification text or with an AD bank. It's a switch
  either way.
- **Formats vary.** AD banks may use their own declaration formats, and ports their own
  Annexure A layouts (Chennai's is P.N. 10/2018). Our drafts are designed to be easy to put
  on letterhead and adjust.
- **The DGFT API needs a fixed IP**, which is why it's optional and comes last.
- **Legal position:** everything the app writes is a draft for a professional to check, not
  advice. The drafts say so.

---

## 8. Defaults I chose (say if you want any changed)

- "Run" means one client and one period, with reconciliations inside it.
- A new run by default; adding to a run is a deliberate choice.
- Decisions carry forward only when the user asks.
- No portal scraping. The DGFT API is optional and comes last.
- The sample becomes one combined run for Kesari Textile Mills.
- No eBRC generation. We produce the bulk upload sheet only.

---

## Sources

1. Circular 05/2018-Customs (officer interface for SB005) — https://taxguru.in/goods-and-service-tax/alternative-mechanism-issued-igst-refunds-case-refund-stuck-due-invoice-mismatch-sb005.html
2. Chennai Customs, IGST Refund Response/Error Codes and Rectification procedure (Aug 2025) — https://chennaicustoms.gov.in/wp-content/uploads/2025/08/IGST-Refund-Response.pdf
3. CBIC DG Systems, Guide on IGST refunds in ICES — https://jawaharcustoms.gov.in/pdf/GST_REFUND.pdf
4. SB005 procedure, Annexure A columns and officer checks — https://taxguru.in/goods-and-service-tax/alternative-mechanism-issued-igst-refunds-case-refund-stuck-due-invoice-mismatch-sb005.html
5. Circular 05/2021-Customs (all shipping bills, ₹1,000 fee) — https://www.a2ztaxcorp.com/cbic-issued-circular-for-extension-of-alternate-mechanism-for-resolving-invoice-mis-match-errors-sb-005-in-respect-of-all-shipping-bills and https://thetaxtalk.com/2021/02/alternate-mechanism-for-resolving-sb005-error-for-igst-refund-extended-on-permanent-basis-with-fee/
6. FEMA (Export and Import of Goods and Services) Regulations, 2026 — https://taxguru.in/rbi/foreign-exchange-management-export-import-goods-services-regulations-2026.html
7. KPMG Flash News on the 2026 Regulations — https://www.in.kpmg.com/taxflashnews/KPMG-Flash-News-Foreign-Exchange-Management-Export-and-Import-of-Goods-and-Services-Regulations-2026.pdf
8. EY alert, March 2026 — https://www.ey.com/en_in/technical/alerts-hub/2026/03/rbi-issues-exim-guidelines
9. EY alert, June 2026 reversal — https://www.ey.com/en_in/technical/alerts-hub/2026/06/rbi-revises-export-of-goods-and-services-framework
10. Transition table for the June 2026 amendment — https://www.corplawupdates.in/updates/fema-export-realisation-period-9-months-first-amendment-2026
11. November 2025 extension — https://taxguru.in/rbi/rbi-extends-export-realisation-15-months-edpms-softex-fema-impact.html
12. ICEGATE IGST Refund FAQ (April 2026) — https://www.icegate.gov.in/sites/default/files/2026-04/IGST%20REFUND%20FAQ1%20updated.pdf
13. GST portal: Track refund status for IGST on exports — https://tutorial.gst.gov.in/userguide/refund/Track_Refund_Status_for_IGST_paid_on_account_of_Export_of_Goods.htm
14. Why invoices don't get sent to ICEGATE — https://ebizfiling.com/blog/non-transmission-of-invoices-to-icegate-reasons-and-solution/
15. DGFT: GSTIN and GST invoice required in eBRCs from 13 Jan 2026 — https://taxguru.in/dgft/dfgt-amends-ebrc-format-add-gst-details-13th-january-2026.html
16. — https://www.taxscan.in/top-stories/dgft-mandates-inclusion-of-gstin-and-gst-invoice-details-in-amended-e-brc-for-exporters-1442019
17. Rule 96B, CGST Rules — https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter10/rule96b_v1.00.html
18. DGFT Trade Notice 12/2024-25 (bulk upload and API) — https://taxguru.in/dgft/dgft-introduces-ebrc-bulk-upload-api-integration.html
19. DGFT eBRC IEC Integration technical spec v1.3 (Jan 2026) and eBRC FAQs v1.1 — https://content.dgft.gov.in/Website/eBRC%20Technical%20Specs%20Bulk%20Generation.pdf and https://content.dgft.gov.in/Website/DGFT-FAQs-on-Self-Certification-of-eBRC-dated-1.1.pdf
20. ICEGATE Public Enquiries 2.0 — https://www.skydo.com/blog/check-shipping-bill-status and https://www.skydo.com/blog/how-to-download-shipping-bill-from-icegate
21. GSTR-1 export JSON fields — https://groups.google.com/g/gst-suvidha-provider-gsp-discussion-group/c/_IzPV0w2-gw
22. VJM Global — https://www.vjmglobal.com/blog/refund-for-invoice-mismatch-cases
23. EXIMINQ — https://eximinq.in/services/igst-refund/
24. Circular 15/2018-Customs (SB003 undertaking) — https://leatherindia.org/refund-of-igst-on-export-of-goods-extension-of-date-in-sb005-alternate-mechanism-cases-and-clarification-in-other-cases-reg/
25. Competitors — https://theniryat.com/ · https://ebrc.in · https://goshipzy.com/import-export-software/ebrc-closure-in-india-what-it-is-why-it-matters-and-how-exporters-can-save-serious-time · https://www.bharathexim.com/eBRC-Blog.html · https://seasaw.ai/
