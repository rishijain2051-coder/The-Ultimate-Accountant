# GST purchases: GSTR-2B against your books

The GST check compares the bills your suppliers reported on the GST portal (your **GSTR-2B**) with the purchases in your **Tally purchase register**, and works out how much input tax credit you can safely claim. If you also add your **sales register** and the **GSTR-1** you filed, it checks your sales too and prepares a GSTR-3B working.

## What you give it

| Box | What to add | Notes |
|---|---|---|
| Your purchase register | The Purchase Register exported from Tally as Excel (.xlsx) | One file. A Day Book also works, but see the limits below |
| GSTR-2B from the GST portal | The GSTR-2B Excel download | One merged file, or every monthly file at once - any number; they are combined |
| Your sales register (optional) | Tally's Sales Register as Excel | One file |
| The GSTR-1 you filed (optional) | The GSTR-1 Excel from the portal | One or more files |

Under **Matching settings**: the **financial year** (*Read from the file* unless you choose), **Allow a difference of (₹)** (default ₹2, for rounding), **your GSTIN** (sets your home state for the place-of-supply check, and prints on reports) and **business name** (otherwise taken from the Tally file).

### How the GSTR-2B is read

- Sheets are found by their names: B2B, B2BA (amendments), CDNR and CDNRA (credit and debit notes), ISD and ISDA, IMPG, IMPGSEZ and IMPS (imports). A sheet called Sheet1 is read as B2B. Other sheets are ignored.
- Columns are found by their headings, in any order, including the portal's two-row headings ("Tax Amount / Integrated Tax"): GSTIN, trade or legal name, invoice number, date, value, place of supply, reverse charge, applicable % of tax, taxable value, IGST, CGST, SGST/UTGST, cess, filing date, period, ITC availability, reason, original number and date for amendments, and more.
- Rows without a correctly formed GSTIN are dropped (this is how repeated headings and padding are removed; imports need only a bill-of-entry number). Rows with nothing in them are dropped.
- Credit notes are made negative. A credit-note amendment is treated the same way.
- Several GSTR-2B files are combined; a row appearing identically in two downloads (same section, GSTIN, number, value and period) is kept once.

### How the Tally register is read

- The sheet named *Purchase Register* is used (then *Purchase*, *Day Book*), otherwise the first sheet.
- The heading row must have Particulars, Vch No., or Vch Type; the lines above it give your company name and the period ("1-Apr-25 to 31-Mar-26").
- Particulars is taken as the supplier. The amount is Credit (or Debit, or Value) for purchases.
- Reading stops at the first *Total* or *Grand Total* line. Opening and closing balance lines, and lines starting "By " or "To ", are skipped.
- A voucher of type exactly "Purchase" is flagged as possibly not a GST bill; a voucher with no number is flagged too.

### Which financial year

The year is the one most of your purchase vouchers fall in. If you choose a different year, the run notes it: *"The register is dated 2025-26, but the run was set to 2024-25. GSTR-2B documents dated outside 2024-25 are still matched, and marked as outside the year."* The year sets the Section 16(4) deadline (30 November after the year ends) and the months of the trend. If no date can be read at all, 2025-26 is assumed.

x Documents dated outside the year are matched and counted like any other; they are only marked. Your Tally vouchers outside the year are not marked.
x The year comes from the purchase register alone, and one Section 16(4) deadline applies to every document in the run.

## How bills are matched

The matcher works in passes. Each pass only sees what the earlier ones left, and a Tally voucher is never used twice.

| Pass | Result | What must agree |
|---|---|---|
| 0 | Matched (by you) | You said "Yes, it is the same bill" in an earlier check of this run, and both sides are still there |
| 1 | **Matched** | The same bill number (after normalising: separators and each group's leading zeros removed, so 25-26/0376 = 25-26/376) and the amount within ₹2. The name need not agree - if it is less than half alike, the pair is still matched but marked *ledger name is different* |
| 2 | **Amount differs** | The same bill number and names at least 40% alike, the closest amount winning. There is no limit on the size of the difference. Up to ₹10 is called a rounding difference |
| 3 | **Bill number differs** | The amount within ₹2 and names at least half alike (dates are not checked). Numbers that end in the same 3 or 4 digits count strongly |
| 4 | **Probably the same bill** | An amount within 0.1% (or ₹2), dates within 45 days, names at least 35% alike, and an overall score of at least 0.72. Never counted as matched until you confirm it |
| 5 | **Entered in parts** | One GSTR-2B bill equal (within ₹2) to 2 or 3 Tally vouchers from the same supplier within 45 days |
| 6 | One-sided | Whatever is left: **Not in your books** (filed by the supplier, not in Tally) or **Not filed by the supplier** (in Tally, not in GSTR-2B) |

Suppliers are compared by name, after taking away M/s, brackets and endings such as Pvt Ltd, Traders, Enterprises or Industries.

+ Amendments replace what they amend: an amendment that names its original bill takes that bill out of matching and out of every total, shown as *Replaced by an amendment*.
+ Dates more than a month apart on a matched pair are marked.
x **Tally's GSTIN column is never used for matching** - a supplier is recognised by the name of its ledger. Two suppliers with similar ledger names can be confused; one supplier under two very different names will not be connected.
x Pass 1 pairs any bill with the same number and amount, even from a different supplier; the only sign is a note in the Excel and CSV (*ledger name is different*).
x Pass 2 pairs bills with the same number and similar names whatever the difference - two different bills that happen to share a number can be paired.
x *Entered in parts* finds one bill booked as up to 3 vouchers (from the 14 largest candidates) - not several bills booked as one voucher.

## What each result means

| Result | On screen as | Colour |
|---|---|---|
| Matched | Settled | Green tick |
| In GSTR-2B only | **Not in your books** | Always red: credit is waiting |
| In Tally only | **Not filed by the supplier** | Amber |
| Value mismatch | **Amount differs** | Amber |
| Invoice no. mismatch | **Bill number differs** | Amber |
| Probable match | **Probably the same bill** | Amber |
| Split booking | **Entered in parts** | Amber |
| Superseded | *Replaced by an amendment* | Settled |

Any open entry whose credit is close to lapsing under Section 16(4) (60 days or fewer) or has lapsed is also red.

## The checks on every bill

As each GSTR-2B row is read it is checked, and anything found appears under **Also note** in the entry and in the downloads:

- **Place of supply**: CGST/SGST charged when the place of supply is another state (needs your GSTIN) - that tax is not creditable to you.
- **Reverse charge**: the tax is yours to pay in cash, then claim.
- **Credit the portal blocks**: ITC availability *No*, with the portal's reason explained where known (four common reasons are explained in plain words).
- **Partly creditable**: an applicable percentage below 100.
- **The tax rate**: the implied rate (tax ÷ taxable value) is compared with the GST slabs (0 to 28%). A rate between slabs is noted as a mixed-rate bill; a rate below 0 or above 28.5% as impossible.
- **The tax split**: IGST together with CGST/SGST on one bill, or CGST and SGST that are not equal halves.
- **The value**: invoice value that differs from taxable value plus tax by more than ₹2 or 0.5%.
- **Duplicates**: the same GSTIN, number and value more than once - claiming both would double the credit.
- **Section 16(4)**: bills not in your books whose credit lapses on 30 November after the year.

x The app does **not** decide for itself which credits are blocked under Section 17(5) - only what the portal marks as unavailable is treated as blocked.
x Cess is counted in the implied rate, so bills with cess can look mixed or impossible; a genuine rate above 28.5% (for example 40%) is reported as impossible.
x The place-of-supply check compares state names as text, and only runs when your GSTIN is known.

## The credit working

The summary shows four figures: **GST credit you can safely claim now** (on your books as they stand), **More credit, once the missing bills are in your books**, **Purchases your suppliers have not filed**, and, when credit is at stake, **Last date to claim this year's credit** with the days left.

The full working - in the Excel and the PDF, not on screen - goes:

| Line | How it is worked out |
|---|---|
| Credit shown in GSTR-2B | IGST + CGST + SGST + cess across every section; a bill replaced by an amendment counts once, as amended |
| Less: portal marks ITC unavailable | The tax on blocked rows |
| Less: partially creditable rows | The part that is not creditable |
| Less: place of supply outside your state | What is left of those rows' tax |
| **Credit allowed by law** | The ceiling - you cannot claim above this |
| Less: invoices not yet booked in Tally | The credit allowed on bills not in your books - Section 16(2)(a): the bill must be in your books |
| Less: reverse-charge credit | The credit allowed on booked reverse-charge bills - claim only after paying the tax in cash |
| **Safe to claim this period** | What you can defend today |

+ Each bill's credit comes off once, at the first line above that applies to it: a reverse-charge bill not yet in Tally comes off as not booked, a blocked reverse-charge bill as blocked, and a partly creditable bill from another state by the part not creditable and then by the rest.
+ **More credit, once the missing bills are in your books** leaves out reverse-charge bills: once booked, their tax still has to be paid before the credit is safe.
x Reverse-charge tax is deducted whether or not you have paid it; credit from before the year is not deducted.
x Ticking entries off does not change these figures; confirming a *same bill* does, at the next check.

### The GSTR-3B working

When both sales files are loaded, the Excel and PDF add a GSTR-3B working: the output tax from your GSTR-1, set off head by head against the **credit allowed by law** (the ceiling above, split into IGST, CGST, SGST and cess, and printed by head under the credit working in the PDF), surplus IGST credit applied to CGST and SGST in equal halves, cess only against cess, giving the cash payable and the credit carried forward. Without the sales files the output side is left empty, not guessed, and the PDF gives the credit allowed by law and how much of it is safe to claim.

x The set-off uses the credit allowed by law, not the smaller figure that is safe to claim: it still includes credit on bills not yet in your books and reverse-charge credit not yet paid for. The note printed under the working says so.
x If your GSTR-1 workbook has an HSN summary sheet, its rows are added to the output tax.

## Your sales (optional)

With a sales register and a GSTR-1, a second list appears: *Sales: your GSTR-1 against your books*, matched on the invoice number only - **Matched**, **Amount differs**, **Reported, not in your books**, **In your books, not reported**.

x Sales are matched on the exact invoice number only: no name, near-number, probable or split matching. B2CS summary rows (which have no invoice number) are always unmatched.
x Decisions do not affect sales matching (ticks show, but the next check matches afresh). There is no sales CSV, sales entries are not in *All entries*, and their explanations are not in the downloads.
x The sample button loads the purchase side only; the sample sales files are in the SampleData folder if you want to add them yourself.

## Downloads

!shot 11-downloads | Downloads and files: the reports for this check, the files it was made from, and Match again.

- **Excel workbook** - 17 to 27 sheets, depending on what there is to show. It opens on a Contents sheet (business, GSTIN, period, a health score with a verdict from *Filing-ready* to *Material gaps*, and a link to every sheet), then: Summary; Reconciliation (every row, both sides, 18 columns); 2B not in Tally; Tally not in 2B; Value mismatch; Invoice no mismatch; Party-wise; Why each is open (the reason and what to do for every open entry); Probable matches; Split bookings; Superseded rows; ITC working; ITC blocked reasons; GSTR-3B working; Action checklist; Data quality; Possible duplicates; Ageing; Rate analysis; Monthly trend; Supplier scorecard (each supplier graded A to E on unmatched and mismatched share, credit at risk and filing delay); Credit concentration; Section 16(4) watch; Follow-up list; and, with sales, Sales summary and Sales reconciliation.
- **PDF working paper** - ready to print and sign: a one-paragraph summary, the control totals, the status table, the credit working and what to do next; then the largest open items, ageing and data findings; then the monthly table, tax slabs, the riskiest suppliers, the Section 16(4) position, the GSTR-3B working, sales, and *How the matching was done* with the settings actually used.
- **Every entry as CSV**, **Bills not in your books (CSV)**, **Bills your suppliers have not filed (CSV)** - 36 columns each, UTF-8 for Excel.
- **Letters to suppliers (zip)** - one ready-to-send letter per supplier with bills not in your books or not agreeing, and one per supplier whose purchases have not reached GSTR-2B: the documents concerned, their total and tax, and a request to send or file them. Plus a list of all letters and all letters in one file. The letters use the contact person and email from the supplier book when known.

### Match again with different settings

At the foot of a saved GST check: change **Allow a difference of (₹)** and **Look this many days either side**, and press **Match again**. It uses the records already read - no re-uploading - applies the run's decisions and today's date, and saves the result as a new check in the run.

x Match again exists only for GST. Bank and party checks have to be added again to be re-matched.
x Letters include suppliers you have ticked off; probable matches and split bookings are not included in letters.
x Ages and days-to-deadline are fixed on the day of the check; Match again refreshes them.

## Everything the GST check cannot do

x Read `.xls`, CSV, PDF, scans or the GSTR-1 JSON for GST (JSON is read by Exports).
x Use Tally's GSTIN column, filter out non-purchase vouchers in a Day Book (payments and journals would come in as purchases), read Tally's narration or tax columns, or understand "Dr"/"Cr" written beside amounts.
x Detect Section 17(5) blocked credit, file anything, or talk to the GST portal.
x Show the credit working, GSTR-3B working, checklist, data findings, ageing, rates, trend or supplier grades on screen - they are in the Excel and the PDF.
x Tell you, on screen, about sheets or rows it skipped while reading - they are recorded but not shown.
