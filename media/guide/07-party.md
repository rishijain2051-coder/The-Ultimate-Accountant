# A party's statement

The party check compares a supplier's or customer's statement of account with your ledger for them, and builds a bridge from your balance to theirs in which every rupee is named. For a supplier it can also check their bills against what they filed in GSTR-2B, and it drafts a balance confirmation letter.

## What you give it

- **Their statement** - whatever they sent: Excel, PDF, or a photo of a printout.
- **Your ledger for them** - their ledger account from Tally, for the same period.
- **They are your** - *Supplier (you owe them)* or *Customer (they owe you)*.
- **Their name** - read from their statement unless you type it.
- Under **Matching settings**: **Allow a difference of (₹)** (default ₹1) and **Also check their bills against GSTR-2B** (on by default).

## How the statements are read

The heading row is found as for bank statements, with the columns a ledger uses: Date, Particulars, Vch Type, a document number (*Invoice No.*, *Vch No.*, *Bill No.*, *Ref*), Debit, Credit, Balance, and Tally's *Gross Total* for columnar ledgers. The party's name, your company's name, their GSTIN and the period are read from the lines above.

- **One Amount column with a Dr/Cr column** (headed *Dr/Cr*, *Cr/Dr*, *Type* or *Indicator*): each amount is a debit or a credit as its flag says - *Dr*, *D* or *Debit*; *Cr*, *C* or *Credit*; or a voucher type such as *Sales* or *Receipt*. A second Dr/Cr column right of the balance says whether that balance is a debit or a credit, and a Dr/Cr on the closing-balance line is read the same way.
- **A workbook with a sheet per ledger**: your ledger's sheet is the one whose ledger name matches the party named on their statement (75% alike or more), otherwise the only readable sheet, otherwise the largest.
- **Tally quirks handled**: *To/By* in the Particulars column with the ledger name in the unnamed column beside it; a blank date meaning "same as the line above"; the grand-total line that puts the debit total in the date column; a "Page 2" footer not taken for ₹2.
- **Both sides on one sign**: every entry becomes "owed to the party" - their debit or your credit - so a purchase is plus on both sides and a payment minus.
- **Entry kinds** are read from the words: invoice, payment, credit note, debit note, TDS, interest, opening balance.
- **Their printed closing balance** is checked against their own entries; a difference of more than ₹1 marks every entry on their side (in the downloads).

### Two-sided and interest statements

A statement printed with debits on the left of the page and credits on the right (two date columns) is unfolded into one list. When the statement is an **interest statement**, the rate, credit days, interest and TDS are read, and the arithmetic is checked line by line: each product (amount × days ÷ the year basis, which it works out: 360, 365 or days), the days from each date to the period end, the column totals, the net product, the interest at the stated rate, TDS at its rate, and the ledger balance printed against the entries. Each problem is reported in words - *"12 Jan: ₹50,000 for 79 days gives a product of ₹39,50,000, but the statement prints ₹3,95,000."*

## How entries are matched

| Pass | Result | What must agree |
|---|---|---|
| 1 | **Agrees** | The same document number (or the same last 3-4 digits) and the same amount to the paisa |
| 2 | **Agrees** | The same amount to the paisa and dates within 10 days |
| 2b | Interest and TDS for the period | All interest entries on each side compared as one total (and TDS likewise): agreeing within the tolerance, or *interest worked out differently* / *TDS differs* |
| 3 | **Same amount, different dates** | The same amount, 11 to 60 days apart |
| 4 | **Amounts differ** | The same direction, and the same document number (any difference) or dates within 10 days with a difference of at most 12% |
| 5 | One-sided | Their bills, their credits or credit notes missing from your ledger; your entries, payments or debit notes missing from their statement |

Names are not compared - both files are about the same party. Disputed amounts, and anything open for more than 180 days, are red.

## The bridge

!shot 10-party | A party's statement: who owes whom on each side, and the bridge that ties the two.

From **your ledger's balance**, the bridge adds or takes away: their bills not in your ledger; their credits not in your ledger; your entries not in their statement; your payments they have not recorded; differences on documents that otherwise agree; interest and TDS as they worked them out against yours; and any difference in opening balances - ending at **their statement's balance**, with *"It adds up."*

> The party bridge always adds up, because both closing balances are worked out from the entries that were read. It shows where the difference lies; it cannot prove that their statement was read correctly. That is what the interest-statement checks and your own spot-check are for.

When interest differs, the app estimates how much of the difference comes from payments one side has not recorded (the principal times the days to the period end at their rate) and says whether that explains all of it.

## Against GSTR-2B (suppliers)

If their statement shows a GSTIN, and a GST check holding that supplier's GSTR-2B rows has been saved (the newest of the last twelve GST checks, in any run), every bill on their statement is looked up by its number: *agreed with the return*, *filed at a different value*, or *never filed in GSTR-1* - the last being credit you cannot take until they file. The results are in the findings, the Excel, the PDF and the letter.

## The balance confirmation letter

**Balance confirmation letter** opens a draft on the page: the date, your balance as per your ledger, their figure, every difference grouped and listed (up to 12 per group), interest and TDS disagreements, disputed documents, bills not in your GSTR-2B, and a request to confirm. Fill in **Write to** and **Their email**; **Copy the letter**, **Open it in your email** (when an email is known), or **Save these details and redo the letter** (saves the contact to your supplier book, when their GSTIN was read).

## Downloads

- **Excel workbook** (up to 12 sheets): the bridge; every entry with its reason; a sheet per bridge group; findings; against GSTR-2B, bills not filed and filed differently (when checked); ageing; sources.
- **PDF working paper**: the balances, the bridge, findings, what they filed, each group and the disputed documents.
- **Every entry as CSV** and **The balance working (CSV)**.

## What the party check cannot do

x Tell which way an amount went when one Amount column has no Dr/Cr column, no voucher type and no minus sign: it is taken as a debit. A single Dr/Cr column placed right of the balance is read as the balance's, so it cannot direct the amounts.
x Read a document number when the file has no number column: the first word of four or more letters in the particulars is used instead (for "Sales - MH/25-26/0441" that is SALES), which weakens number matching and the GSTR-2B check.
x Include journal vouchers from a columnar ledger that has no Debit/Credit column (they are left out, with a warning).
x Show on screen that their printed closing disagrees with their own entries, or which sheet of a workbook was used.
x Use the customer wording exactly: for a customer, some one-sided lines are labelled as if from a supplier's point of view. The figures are right; only the labels are.
x Check customers against GSTR-1, re-match without adding the files again, or pair entries by hand.
x Keep the reading notes (an OCR notice, left-out vouchers) after the first showing - they are not shown when the run is opened again.
x Put the confirmation letter in a download - it is copied or opened in your email.
