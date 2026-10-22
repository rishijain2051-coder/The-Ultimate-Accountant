# Everything it cannot do

The limits from every chapter in one place, overall first.

## Overall

x File anything, or connect to the GST portal, ICEGATE, DGFT or any bank. It reads the files you download.
x Work on a Mac, a phone, or more than one computer; share data between computers or people; or keep separate logins.
x Back up your data automatically; restore it from a button; or encrypt it.
x Update itself from anywhere but its GitHub release page, install an update without restarting, or update versions before 2.2.0 (install 2.2.0 by hand once).
x Stop you running an older `Install.exe` over a newer version (updates it fetches itself are always newer).
x Show the log's location on screen.
x Stop when you close the browser tab, when it opens in the browser for want of WebView2 - use Settings > Close the app.
x Replace a professional's judgement: letters and worksheets are drafts, suggestions need your confirmation, and anything read from a scan must be spot-checked.

## Reading files

x GST files other than `.xlsx`; old `.xls` anywhere; password-protected PDFs; more than 40 pages of a PDF (the rest are ignored silently); more than 20,000 rows of a sheet (bank, party, exports); more than 60 columns of a GSTR-2B.
x Handwriting, sideways or upside-down photos, scripts other than English letters and digits, several photos combined into one statement, pages after the first of a multi-page TIFF.
x A column map for files without recognisable headings; a preview of what was read before it is used.

## Decisions and reports

x Carry decisions to a new, separate run; take a note with a decision; tick many at once.
x Leave ticked entries out of the Excel, PDF, CSV, letters, drafts or summary figures.
x Show on screen what is only in the downloads: the GST credit and GSTR-3B workings, checklist, data findings, ageing, rates, trend and supplier grades; the bank findings and channel tables; the party findings, ageing and GSTR-2B results.
x Open older checks of the same kind in a run (only the newest shows); rename, archive or pin runs; compare two checks.

## GST

x Use Tally's GSTIN to identify a supplier (names are used); filter a Day Book to purchases; read Tally narrations, tax columns or "Dr/Cr" text.
x Decide Section 17(5) blocked credit; recognise tax rates above 28.5% as genuine.
x Know whether reverse-charge tax has been paid (that credit is kept out of *safe to claim* either way); take credit from before the year out of the working; set off GSTR-3B against only the credit that is safe to claim (it uses the credit allowed by law).
x Match several bills booked as one voucher; stop a same-number, same-amount pair from different suppliers being matched (it is only noted in the downloads); cap the difference on a same-number pair.
x Match sales other than by exact invoice number; match B2CS summaries; export sales in CSV.

## Bank

x Pair two lines one-to-one whose amounts differ at all (the tolerance applies to the *amount differs* test and to groups); match several bank lines to one book entry.
x Read "Dr"/"Cr" or a trailing minus on amounts; reverse a newest-first statement; read a bank book with one Amount column or with dates left blank on repeated lines.
x Catch a misread figure when the statement has no balance column.
x Re-match without adding the files again; pair lines by hand.

## A party's statement

x Tell which way an amount went when one Amount column has no Dr/Cr column, voucher type or minus sign (it is taken as a debit); find document numbers in files that have no number column.
x Prove that their statement was read correctly - the bridge always adds up; check customers against GSTR-1.
x Keep reading notes after the first showing; put the confirmation letter in a download.

## Exports

x Generate eBRCs; read the GST failed-invoice list or an export ledger CSV; draw up a Rule 96B schedule; track RoDTEP; handle service exports.
x Match payments in a currency other than the invoice's, or set more against an invoice than its value.
x Record the date or amount of a refund or drawback you mark received by hand - only yes or no.
x Add rows to your own shipping bill register from the app (correct or remove them, yes; add them in the workbook), or turn a list into another kind of document.
x Keep a history of anything done before the history was added, or keep a register's history after the register is deleted.
x Join a Table 6A line whose invoice number and shipping bill number are both wrong; treat a leading-zero difference as a typing slip.
x Name ports other than Nhava Sheva, Mundra, Mumbai and Chennai in the drafts; ask for other than 180 days in the extension request.
x Have the AI checker read spreadsheets, lists or pages after the second, run it on another computer, or let it change anything by itself.
x Keep up with a rule change by itself - the realisation rules are as of 26 September 2026, in a file that has to be edited when they change.
