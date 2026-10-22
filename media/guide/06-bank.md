# Bank reconciliation

The bank check compares the statement your bank sent with the bank book in your books (Tally), names every difference, and proves that the two balances reconcile - a bank reconciliation statement that adds up to the rupee.

## What you give it

- **The bank statement** - Excel, CSV or PDF from the bank; a scan or a photo works too.
- **Your bank book** - the bank ledger from Tally for the same period.

Under **Matching settings**: **Allow a difference of (₹)** (default ₹1), **Same transaction if within (days)** (default 7), **Timing difference up to (days)** (default 45), and **Read the statement as a scan, even if it is a PDF with text** - useful when a PDF's text layer is garbled.

!shot 07-add-bank | Adding a bank check to the same run: the statement and the bank book.

## How the statement is read

The heading row is found within the first 30 rows by scoring each row for a date, a narration, withdrawal and deposit columns (or one amount column with a Dr/Cr flag), a balance and a reference. Headings are recognised in the many ways banks write them: *Txn Date*, *Value Dt*, *Narration*, *Particulars*, *Chq./Ref.No.*, *UTR*, *Withdrawal Amt.*, *Deposit Amt.*, *Debit*, *Credit*, *Closing Balance*, *Dr/Cr* and so on.

For each line:

- The **date** - day first (dd/mm/yyyy and some twenty other ways of writing a date, and Excel dates). A date glued to the front of the narration by a scan is split off.
- The **amount** - withdrawal and deposit as printed; or, with one amount column, a withdrawal if the Dr/Cr column says D or the amount is negative.
- The **channel**, read from the narration: tax, interest, charges, loan/EMI, salary, cheque, NEFT, RTGS, IMPS, UPI, standing instruction (ACH/ECS/NACH), ATM, card (POS), cash, transfer or reversal - the nature (a charge) is tested before the rail (NEFT), so *NEFT RETURN CHARGES* is a charge.
- **Reference numbers** - UTRs, 6-digit cheque numbers, 12-digit UPI numbers - with branch IFSC codes removed first so a branch code is not taken for a UTR.
- The **counterparty** - the longest run of letters left once the codes, handles, account numbers and dates are taken out.

The bank name (21 banks are recognised by name), the account number and the IFSC are read from the lines above the heading. Opening and closing balances, total lines and page footers are recognised and skipped.

**The running balance is checked on every row**: the previous balance plus this line's movement must equal the printed balance within ₹1. Rows where it does not are flagged, a note says *"The running balance does not tie on N row(s)… The statement may have been edited, or a column was read wrongly."*, and the reconciliation still runs.

## How your bank book is read

The same heading rules, with Tally's columns: Date, Particulars (taken as the party), Vch Type, Vch No., a reference, Debit (money into the bank) and Credit (money out). The opening-balance line is read; the closing balance is worked out from the entries.

x A bank-book line with no date is dropped (Tally leaves a repeated date blank on some exports) - the party-statement reader carries the date down, the bank-book reader does not.
x A bank book with one Amount column instead of Debit and Credit is not supported.
x An overdrawn (credit) opening balance loses its minus sign; without an opening-balance line the book is taken to start at zero, and a *difference in opening balances* line appears.

## How lines are matched

| Pass | Result | What must agree |
|---|---|---|
| 1 | **Matched** | The same amount to the paisa, and a reference on both sides (a UTR or cheque number on the statement found in the voucher number or reference) |
| 2 | **Matched** | The same amount to the paisa and dates within 7 days; the nearest date and most similar name win. Pairs whose names differ are marked |
| 3 | **Timing difference** | The same amount, dates 8 to 45 days apart, and similar names or matching references |
| 4 | **Amount differs** | Amounts differing by more than the tolerance but by no more than ₹500 or 5%, dates within 7 days, and similar names or references |
| 5 | **Several entries in one** | One bank line equal (within the tolerance) to 2, 3 or 4 book entries in the same direction within 7 days |
| 6 | One-sided | Whatever is left, sorted into what it most likely is (below) |

A payment is never matched with a receipt.

Left in the bank only: *Bank charges not in books*, *GST on bank charges not in books*, *Interest credited / debited, not in books*, *Loan or EMI debit not in books*, *Standing instruction not in books*, *ATM withdrawal not in books*, *Reversal or return not in books*, *Credit in bank, not in books*, *Debit in bank, not in books*. Left in your books only: *Cheque issued, not presented* (every payment) and *Deposit made, not credited* (every receipt); a payment more than 90 days old is marked stale.

## The reconciliation statement

!shot 08-bank | A bank check: the two balances, every difference on its own line, and the proof that it adds up.

The working starts from **your bank book's balance**, adds or takes away each group - cheques you issued that the bank has not paid out, deposits not yet credited, bank charges and interest you have not entered, other money the bank took out or received - then any differences on matched lines and in the opening balances, and ends at **the bank statement's balance**. When every rupee is accounted for it says *"It adds up."*

If it does not add up, the leftover is shown as *Not explained – a figure was probably read wrongly*, and the headline becomes *"₹X of the difference cannot be explained"*. Because every group is worked out from the movements themselves, a leftover means a line was dropped or misread, or the opening balance was picked up wrongly - not that something was forgotten.

> The leftover can only catch a misread when the statement has a balance column. Without one, the closing balance is worked out from the lines read, so the working always adds up and cannot catch a misread figure.

!shot 09-dot | Even a bank line described only as "." explains itself.

## What else it finds (in the downloads)

Stale cheques (more than three months old - reverse and reissue); amounts that differ between bank and books; bank charges never booked (with the GST on them); direct debits never booked; credits in the bank with no entry; possible duplicate payments (same party and amount within 7 days); large cash movements of ₹2,00,000 or more (Section 269ST); a running balance that does not tie; and narrations too unclear to classify. Also a table by channel and a month-by-month table.

## Downloads

- **Excel workbook** (up to 12 sheets): the BRS; every pair and one-sided line with its reason; unpresented cheques, uncredited deposits, charges, bank debits, bank credits and interest not booked (each when there are any); findings; by channel; monthly; and the sources (files, bank, account, IFSC, balances, and whether the running balance tied).
- **PDF working paper**: the statement, the charges, what needs attention, each schedule (40 rows each), how the matching was done, and the sources.
- **Every entry as CSV** and **The bank reconciliation statement (CSV)**.

## What the bank check cannot do

x Pair two lines one-to-one whose amounts differ by a few rupees: a one-to-one pair needs the same amount to the paisa; *Allow a difference* applies to the *Amount differs* test (more than the tolerance) and to several-in-one groups.
x Read "Dr" or "Cr" written after a balance or amount (an overdrawn balance printed as "1,000.00 Dr" breaks the running-balance check), or a trailing minus sign.
x Reverse a statement listed newest-first; it is read in the order printed.
x Match several bank lines to one book entry (only one bank line to several book entries).
x Re-match with different settings without adding the files again, or pair two lines by hand.
x Show the findings, charges and channel tables on screen - they are in the downloads.
x Filter either file by date: book entries after the statement ends appear as unpresented or uncredited (the explanation says so). Give it two files that cover the same period.
x Treat TDS or TCS debits other than as *GST on bank charges*, or check bank-charge GST against your GSTR-2B.
