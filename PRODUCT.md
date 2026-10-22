# Product context

## What this is

A local desktop tool for the reconciliations a small Indian business owes
itself every month: GSTR-2B against the Tally purchase register and the input
tax credit that comes out of it, the bank statement against the bank book, and
a party's statement of account against the ledger kept for them. Source
documents may arrive as workbooks, PDFs, scans or photographs, and are read on
the same machine.

It runs as a local web app on the user's own machine: a Python server, a
SQLite file, a browser tab. Nothing is uploaded anywhere.

The console has two modules, switched from the cover band. Reconciliation is
the above. Exports is a register per exporter: shipping bills, bills of lading,
GSTR-1, bank advices and the DGFT and ICEGATE downloads are added as they
arrive, each is filed against its export invoice, and every invoice says what
is still missing from it.

## Who uses it

Two people, and every screen has to work for both:

- The owner of a small or mid-size Indian business, who knows the trade but not
  reconciliation vocabulary. "In Tally only" or "Add: cheques not presented"
  means nothing to them; "you paid ₹2,09,160 on 25 Feb and it has not gone
  through the bank yet" does.
- The person who prepares the monthly GST return: an in-house accountant, or a
  practitioner handling several clients. Fluent in GST and in Tally, not in
  software, and will not read documentation.

- The exporter, or whoever keeps their export papers, who today maintains a
  spreadsheet of "did the shipping bill and the bill of lading come for this
  invoice, is it in GSTR-1, has the buyer paid, is the eBRC done". Knows the
  documents by sight, not the ICEGATE error codes.

All of them work at a desk monitor, in daylight, for long stretches, and print
things for the file. Plain words must never cost the accountant a figure: the working,
the exports and every number stay complete.

The same person does the bank reconciliation and answers the statements
suppliers send at the year end. Those jobs are the same shape as the GST one —
two documents that should agree and do not — so they belong in the same tool
rather than in three spreadsheets.

A typical client year runs to about 1,300 GSTR-2B documents against 1,250
Tally vouchers from some 130 suppliers.

## The job

Once a month, and again before the annual return, they need to answer:

1. Which GSTR-2B documents are not in my books, and what credit is riding on them?
2. Which purchases in my books has the supplier not filed?
3. How much credit can I claim this period without inviting a query?
4. Which suppliers are costing me money, and what do I write to them?
5. Did the things I flagged last month actually get fixed?

And, on the same cycle:

6. Does the bank statement agree with the bank book, and if not, exactly why?
7. Which cheques are stale, and what has the bank charged that nobody posted?
8. Does this party's statement agree with our ledger, and what do I write back?

And, for an exporter, whenever a document arrives:

9. Which invoices are still missing a shipping bill or a bill of lading?
10. Will the IGST refund go through, and if not, which side is wrong and what
    does the fix cost?
11. How much is still to come from buyers, and by when, under the rule that
    applies to each shipment?
12. Which payments have no eBRC yet, and is the bulk sheet for DGFT right?
13. Which IGST refunds and drawback have not come back, and what does ICEGATE
    say is holding each one?

Before this tool the answer came from a manual VLOOKUP exercise that took a day
and missed anything where the invoice number was keyed differently.

## Non-negotiables

- **The seven-sheet Excel report keeps its shape.** That file is what gets
  filed and circulated for review. New analysis is appended after it, never
  inside it.
- **Every figure must be defensible line by line.** This is working-paper
  software. If a number cannot be traced to a source row, it does not belong.
- **Offline and light.** The interface uses the system font and ships no web
  fonts, no chart library and no framework; there is no CDN, no telemetry, no
  account.
- **Every open entry says why it is open.** Not the category: the reason for that
  entry, what was checked, the nearest thing on the other side and exactly why
  it was not paired, what it costs and what to do. An entry whose description is
  only "." says so and still gets its reason.
- **Nothing files a return.** The tool reads what was downloaded and reports
  what it finds. It never touches the GST portal, ICEGATE or DGFT, and does not
  generate eBRCs (DGFT does that for free); it fills in DGFT's bulk sheet.
- **Money is never matched silently.** A payment counts against an export
  invoice only on evidence that names the invoice (its eBRC, the bank's advice)
  or on the user's confirmation. Everything else is a suggestion.
- **Decisions outlive runs.** A manual match or a write-off recorded once must
  apply automatically the next time the same period is reconciled, or the tool
  makes more work than it saves.
- **A reconciliation statement always ties.** The bank statement and the party
  bridge are derived from an arithmetic identity, not assembled from a
  checklist. A residual that is not zero means a document was misread, and is
  reported as exactly that rather than rounded away.
- **OCR is read, not trusted.** Anything recognised from a scan carries its
  confidence, and a scanned statement is checked against its own running
  balance chain before its figures are used.

## Domain facts the software encodes

- Invoice numbers are keyed inconsistently. Normalisation must strip case,
  separators and the leading zeros of *each group*, so `25-26/0376` and
  `25-26/376` meet.
- Tally exports carry no GSTIN column, so supplier identity rests on the ledger
  name. Every supplier-level figure needs a human check before it is acted on.
- Bank-charge GST appears in GSTR-2B but never in a purchase register; it is
  posted through bank or journal vouchers. In the sample year one bank accounts
  for 86 of the 98 "2B only" rows.
- A blended tax rate on a multi-item invoice is normal, not a defect. Only a
  rate outside the statutory range, or an impossible IGST/CGST/SGST split, is
  an error.
- Credit must be in the books before it is claimed (s.16(2)(a)), and claimed
  before 30 November of the following year (s.16(4)).
- Amendment rows supersede the documents they amend and must drop out of every
  total, or the credit is double-counted.
- A bank receipt is never the same event as a payment, however close the
  amounts. Matching across directions produces a statement that balances and a
  reconciliation that is wrong.
- Both sides of a bank reconciliation are held as *effect on balance* — bank
  credit less debit, book debit less credit — so the two compare directly and
  the identity holds without a sign convention to remember.
- On a party statement their debit is our credit. Both sides are normalised to
  "amount owed to the party", so a customer and a supplier reconcile through
  the same arithmetic.
- A cheque unpresented for more than 90 days is stale and cannot be presented;
  it is a book correction, not a timing difference.
- Cash movements near ₹2,00,000 matter on their own (s.269ST), whether or not
  they reconcile.
- The deadline for export proceeds follows the date the goods shipped, and four
  rules have applied since November 2025: 9 months, then 15, then 9, then 15
  (18 if invoiced in rupees) from 1 October 2026. The periods and limits live
  in `app/backend/exim_rules.json` so a new circular is an edit, not a release.
- The shipping bill's own Part I boxes (IGST AMT, DBK CLAIM) are the totals to
  track; summing the item lines undercounted 12 of 19 bills tested. The boxes
  agreed with the exporter's own records on all of them.
- A refund is missing only on evidence - the exporter's register lists it with
  nothing against it, or ICEGATE shows a code - never because nothing is on
  file. ICEGATE has no exporter API, so its status comes from the downloads.
- ICEGATE compares Table 6A with the shipping bill character for character: a
  shipping bill that says `KF/` for `KE/` stops the refund as surely as a wrong
  number (SB005). Which fix applies depends on which document is wrong; only
  the port route costs ₹1,000 per shipping bill.

## Scope

In: purchase reconciliation, the credit working, outward reconciliation against
a filed GSTR-1, the GSTR-3B working, supplier follow-up letters, bank
reconciliation with a balancing statement, party statement reconciliation with a
confirmation letter, document intake including OCR of scans and photographs,
runs kept separate by client and period, Excel / PDF / CSV exports; the export
register with its refund checks, payment deadlines, payment matching, eBRC
tracking and DGFT bulk sheet, refund and drawback tracking with ICEGATE's
codes, the bank's EDPMS statement, and the drafted documents (the SB005
Annexure A pack, the Table 9A worksheet, the SB003 undertaking, the EDPMS
declaration, the extension request).

Later: a Rule 96B schedule, and the optional DGFT API connection. The analytics charts, supplier scorecard
screen, document reader screen and run comparison left the interface in the
plain-language redesign; their endpoints remain.

Out: e-invoicing, e-way bills, portal filing, portal scraping, eBRC generation,
bank API or feed connections, TDS, income tax, multi-user access, anything that
needs a server.
