# Exports: a register per exporter

The Exports part replaces the spreadsheet an exporter keeps by hand. You drop documents on a register as they arrive - the shipping bill at clearance, the bill of lading when the ship sails, the GSTR-1 when it is filed, the bank's advice when the money comes in, the ICEGATE and DGFT downloads when you fetch them - and every invoice's line shows what has arrived, what is missing, when the money is due, whether the IGST refund and drawback came back, and whether an eBRC exists.

!shot 20-ex-start | The Exports start page: drop any number of documents, in any order, or try the sample.

## Registers

A **register** is one exporter's documents. Documents dropped on the Exports start page, and the sample, always start a new register; documents dropped while a register is open go into it. A register is named from the exporter on its shipping bills (*Kesari Textile Mills Private Limited · exports*). **The whole register is worked out again every time it is opened**, from every document posted to it and today's date, so deadlines move day by day and a document added today can settle a question an older one left open.

## The documents it reads

| Document | How it is recognised | What is read |
|---|---|---|
| **Shipping bill** (ICEGATE PDF) | "INDIAN CUSTOMS EDI SYSTEM" with "SHIPPING BILL" | Port, SB number and date, IEC, GSTIN, mode, AD code, destination, exporter, buyer, LEO date, containers; each invoice (number, date, INCOTERM, value, FOB, currency, rate); IGST per item; the IGST and drawback totals read from the Part I boxes by position; RoDTEP |
| **Bill of lading / airway bill** (text or scan) | "BILL OF LADING", "B/L NO", "SHIPPED ON BOARD", "AIR WAY BILL", "AWB NO"… | BL number (tolerating OCR's "HI BL"), the invoice and SB numbers it names, shipped-on-board date, vessel, containers, port of loading, consignee |
| **Invoice** (PDF, scan or Excel) | "INVOICE" with "EXPORT", "IEC" or "CONSIGNEE" | Number, date, WPAY or LUT, currency, totals, buyer - used to settle which side is wrong in an SB005 mismatch |
| **GSTR-1** (portal JSON, or the offline tool's EXP sheet) | The JSON's structure; the sheet's headings | Every Table 6A export invoice and every Table 9A amendment: type, number, date, value, port, SB number and date, taxable value, IGST, rate |
| **Bank remittance advice** (FIRA/FIRC, or a Remittance Transaction Advice like Kotak's) | "INWARD REMITTANCE", "FIRA", "REMITTANCE ADVICE", "REMITTANCE TRANSACTION ADVICE" - even run together in a scan - or "Purpose of Remittance" with a remitter | Reference, bank, credit date, remitter, currency and amount (written before or after the currency), rupee amount, rate, purpose code, whether an advance, invoice named; and when the bank shows it, the packing credit repaid out of the money and what reached your account |
| **DGFT IRM list** (Excel) | Its headings | Every inward remittance: IRM number and date, IFSC, AD code, currency, amount, available, status, remitter, purpose |
| **DGFT eBRC list** (Excel) | Its headings | Every eBRC: number, date, status, IRM, SB, invoice, realised value |
| **ICEGATE shipping bill status** | Its headings | Status and error code per SB |
| **ICEGATE IGST refund and drawback status** (download or a pasted web table) | Its headings | Per SB: the response code (SB000-SB006), status, scroll number and date, paid date, amount |
| **ICEGATE drawback waiting on proof of payment** | Its headings | SB, LEO date, drawback, expected date |
| **Your bank's EDPMS statement** (Excel, or PDF read as a table) | Its headings | Per SB: value, realised, outstanding, due date (including the bank's extension), status |
| **Your own shipping bill register** (for example "S. BILL DETAIL") | An SB number column plus GST or drawback plus register-only columns | Every row of every year tab: invoice, container, depot, port, SB, GST paid and received with its date, drawback and its date, error codes and notes, payment, bank |

+ Files you give it that it does not recognise are still kept, under *Not recognised*, and wait to be checked like any other: say what each is and fill in its numbers, or remove it.
+ The same file added twice is kept once; a second copy of the same shipping bill is ignored.
x Only Table 6A and 9A are read from a GSTR-1, and only a GSTR-1 JSON carries the GSTIN the SB003 check needs.
x A workbook is read as one kind of document (the sheet it recognises best), except your own register, whose year tabs are combined.
x Old `.xls` files are refused; save them as `.xlsx`.
x Reading notes on recognised but incomplete documents (for example "No invoice row was found in Part II") are not shown on the page; the document's one-line summary shows what was found.
> The same month's GSTR-1 added twice (the portal JSON and the offline Excel, say) puts each invoice in Table 6A twice and flags it; the same IRM or eBRC list added twice lists each entry twice. Add each download once.

## Checking what was read

Nothing you add goes into the register until you have seen what was read from it. Each document you drop waits at the top of the register under **Check what was read before it goes in**, and *To check* in the margin counts them. For each you see:

- what kind of document it was read as, the name it is kept under, and *read from a scan* if it was;
- the numbers that decide where it goes and the money on it - for a bill of lading its number, shipped-on-board date, the invoice and shipping bill numbers printed on it and its containers; for a shipping bill its number, date, port, invoices with their values, IGST and drawback; for an advice the amount, remitter, date of credit, reference, purpose code and any invoice it names - and, when the bank shows it, the packing credit repaid out of it and what reached your account; for a list, how many rows and the first few;
- **where it will be filed** - *Will be filed against KE/25-26/058*, worked out with the document in - or that it matches nothing on file yet. An advice that names no invoice says you will choose its invoices when you post it;
- with the AI checker, what it made of the document (see *The AI checker*).

!shot 31-ex-check | Check what was read: a bill of lading whose invoice number matches nothing on file, and an advice that names no invoice - neither counts until it is posted.

Then: **Post it** puts it in; **Correct what was read** opens it to put right; **Open the original** shows the file beside it; **Remove** drops it. **Post all** posts every one as shown. A bank advice posted whose money nothing places opens *Which invoices is this payment for?* straight away.

+ Every document can be checked before it counts, and corrected at any time after.
x The sample is posted without a check - it is there to look around, not to verify.
x A file added twice is kept once; adding it again does not bring it back to be checked.

## Correcting what was read

**Correct what was read** - on each document waiting, on every document in **Documents**, and beside *Open* in an invoice's file - opens a window with everything the reader filled in:

- **Fields**, each in its own box: numbers, dates (a calendar), amounts, codes, and lists like containers or the invoice numbers on a bill of lading (separate them with commas). A box you have corrected turns green and says what it was *read as*.
- **Tables** - the invoices on a shipping bill, a GSTR-1's Table 6A lines, the rows of an IRM, eBRC, status or EDPMS list, your register's rows. Each row has **Change** (its boxes open) and **Remove** (struck through, with *Keep it* to take that back); **Add a row** adds one; *Find a row* narrows a long table.
- **What this document is** - for a document read as the wrong kind, or not recognised at all, turn it into a shipping bill, bill of lading, advice or invoice; its boxes change to that kind's.

!shot 32-ex-correct | Correcting what was read: the bill of lading's invoice number put right - the box turns green and says what it was read as.

**Save the corrections** checks every value first - a date that does not exist, a word where an amount goes, a row with no number - and saves nothing until all of them pass, saying which box is wrong. Then the register is worked out again, the document is filed where its corrected numbers say, and the file it is kept under takes the name its corrected reading gives it.

+ What the reader found is never overwritten: your corrections are kept beside it. Put a value back and it is no longer a correction.
**Read the file again**, at the foot of the same window, reads the kept copy once more with this version's reader - for a document read before the reader knew its layout, such as a bank advice taken for an invoice. The new reading replaces the old; a correction of yours that still says something different stays; and the document goes back under *Check what was read*, to be posted again. If it reads the same as before, nothing changes.

+ A shipping bill's IGST total and WPAY or LUT follow its invoices once you correct them.
x Rows cannot be added to your own register here - add them to the workbook and add it again. They can be corrected or removed.
x A list (GSTR-1, IRM, eBRC, status, EDPMS, your register) cannot be turned into another kind of document.
> A correction belongs to the document it was made on. A newer download of the same list comes in as it was read, and is checked on its own.

## History

The last section of every register, **History**, is its audit trail, newest first: each document added, checked and posted (and where it went), corrected - each change as *field: before → after* - and removed; each payment you set against invoices or cleared; each yes or no you gave on GSTR-1, a refund or drawback; and each button pressed on an open entry, with the entry's title. Every line has the date and time and who did it: the name in **Settings > Your name**, or the Windows account when that is blank. **Show more** reaches further back. The register's Excel download carries the whole history on a sheet of its own.

!shot 34-ex-history | History: each document added, corrected (before and after) and posted, and each payment placed, with when and who.

x The history starts with this version: what was done before it is not in it.
x It is kept with the register: deleting the register deletes its history too.

## The register

!shot 21-ex-register | A register: the summary figures, then a line per invoice with a mark and a few words for each stage.

The summary opens with how many invoices need something from you, then the figures: **IGST refund held up**, **Overdue from buyers**, **Due in the next 90 days** (the number follows Settings), **Still to come from buyers**, **eBRCs to generate**, **Bills of lading missing**, and - when your register or ICEGATE's refund status is on file - **Refunds and drawback not received**. A second drop zone takes new documents at any time.

!shot 22-ex-summary | The register's summary: what is held up, overdue, due, and still to come.

Each invoice has one line with six columns - **Shipping bill**, **Bill of lading**, **GSTR-1**, **Payment**, **eBRC**, **Refunds** - each a mark and a few words: *4569456 · 8 Sep 2026*, *Not received*, *In 6A · Sep 2026*, *Overdue since 22 Apr 2026*, *To generate*, *IGST held · SB005*. Above the table: **Show** (every invoice, needs something from you, no shipping bill yet, no bill of lading yet, GSTR-1 not right, money still to come, payment overdue, eBRC to generate, refund or drawback not in), a year filter (when invoices span more than one year), and a search.

Click an invoice to open its **file**: every shipping bill with its IGST and *Open*; the bill of lading; its Table 6A line, with **Filed in GSTR-1?**; your invoice; the payment deadline with the rule behind it and every receipt counted, with why and a **Change**; eBRCs; the IGST refund and drawback with the ICEGATE code and scroll, with **Refund received?** and **Drawback received?**; the bank's EDPMS line; ICEGATE's drawback list; your register's row and notes; **Papers** - its own documents, which **Save them as one zip** hands over together; and what is still open.

!shot 23-ex-invoice | An invoice's file: every stage, the deadline and why, and its papers in one zip.

## Payment deadlines

The time allowed to bring the money in depends on when the goods were shipped, under the FEMA regulations as they stood on 26 September 2026:

| Shipped | Time allowed |
|---|---|
| Up to 13 Nov 2025 | 9 months |
| 14 Nov 2025 to 4 Jun 2026 | 15 months |
| 5 Jun 2026 to 30 Sep 2026 | 9 months |
| From 1 Oct 2026 | 15 months, or 18 if invoiced in rupees |

The deadline counts from the shipping bill date by default - or the let export order date, or the shipped-on-board date on the bill of lading (**Settings > Exports**, since your bank decides which). Months are calendar months held to the month end. If the bank's EDPMS statement shows a **later** due date (an extension), that date is used. An unpaid invoice is **due soon** when 90 days or fewer are left (a setting), **overdue** after the date, and **over a year overdue** after 365 days. A shortfall of up to 1% (at most 150 units of the currency) is taken as the banks' charges and the invoice counts as paid.

x The deadline tracks the invoice value in foreign currency; rupee figures use the shipping bill's exchange rate.
x A line known only from your own register, with no invoice value, gets no deadline.
x The switch that gives 15 months to bills shipped before 14 November 2025 and still unpaid on that day does not check that they were in fact unpaid; it is off by default, the safer choice.
> The rules changed three times in twelve months. They are kept in a rules file (`exim_rules.json`) so a change means editing a value, not a new program - but they are only as current as that file.

## Money in

Payments from abroad come from the DGFT IRM list and the bank's advices (an advice and an IRM for the same payment become one entry). **A payment is only counted against an invoice on evidence**: an eBRC that names both the invoice and the IRM; a bank advice that names the invoice; or your own say.

### Which invoices a payment is for

When you add a bank advice and nothing names the invoice it pays - or it pays more than the invoice it names - a window opens: **Which invoices is this payment for?** It lists every invoice in the payment's currency with money still to come: first the one the advice names, then the buyer's own (the remitter's name at least 60% alike), each oldest first, then the rest. Beside each is how much is still to come and a box for how much of this payment goes to it.

!shot 33-ex-settle | Which invoices is this payment for? EUR 30,000 from Casa Oliva spread over its invoices, oldest first - a suggestion to change as you like.

The boxes start filled in with a suggestion: the payment spread over the likeliest invoices, oldest first. Three invoices of EUR 10,000 and an advice for EUR 25,000 start as 10,000, 10,000 and 5,000. Change any amount; **Fill from the top** fills the list again in order. The line under the boxes keeps the total: all of it placed, how much stays on the payment, or how much too much. An amount larger than what an invoice has left, or a total larger than the payment, cannot be saved. What you leave over stays on the payment as *not yet placed* - an advance, or for an invoice not added yet. **Not now** closes the window and changes nothing; the payment waits under Money received, where *Choose invoices* opens the window again.

Every payment in **Money received** has **Choose invoices** (nothing placed yet) or **Change**, and so does every receipt in an invoice's file. Change opens the window with the payment as it stands, and **Clear what you chose** hands it back to what its documents say.

+ Your split counts over the invoice the advice names; nothing else is added to a payment you have split.
x What an eBRC sets against an invoice is proof and stays: the window shows it and offers only the rest.
x The window opens by itself only for a bank advice just added, not for the payments in a DGFT IRM list or the sample; those have *Choose invoices*.

When you do not choose, the app **suggests** the best candidate - same currency, money still due, and a mix of how alike the remitter is to the buyer, whether the amount fits (within 1% or 150 units) and whether the money arrived after shipment. The suggestion is a *Payment to confirm* entry with **Yes, it is for invoice X**, **No, it is for another invoice** and **Split it over several invoices**. Yes counts it (with an Undo in the invoice's file); No means that pairing is never suggested again, and the next best is offered.

When no single invoice fits the amount but a run of one buyer's invoices does - oldest first, together coming to the payment or up to the banks' charges more - the suggestion is for all of them: *EUR 36,420 received on 8 Jul 2026 looks like payment for invoices KE/26-27/061, KE/26-27/062 and KE/26-27/063*, with what goes to each. **Yes - check the amounts and save** opens the window filled in that way; **No, it is for other invoices** stops it being suggested again. Two runs that would both fit are a guess, so then none is offered.

!shot 26-ex-money | Money received: each payment, what it was counted against and why, and what is not yet placed.

+ Advances (purpose code P0103, or "ADVANCE" on the advice) are never matched automatically; they wait until the shipment they pay for is added, or until you choose the invoices.
+ An advance set against goods that had already gone when the money came is flagged under *Bank (EDPMS)*: the bank has the payment on the wrong code, and until it realises it against those shipping bills (purpose code P0102) EDPMS can go on showing them unpaid.
+ Cancelled IRMs and eBRCs are shown struck through and never used.
x Payments in another currency from the invoice are never matched, suggested or offered in the window.
x An invoice can take no more than its value: a buyer who paid more leaves the extra on the payment.
x Your own register's PAYMENT column does not settle an invoice whose value is known; only payments on file do.

## eBRCs

!shot 27-ex-ebrc | eBRC: payments counted against an invoice that no eBRC covers yet, each checked the way DGFT checks it.

Every payment counted against an invoice that no eBRC covers yet becomes an *eBRC to generate* entry and a row of the **DGFT bulk eBRC sheet** - DGFT's own columns, with a second tab checking each row the way DGFT does (IRM number and date, IFSC, AD code, purpose code valid for goods - P0102, P0103, P0104 or P0109 - SB details, currency code, and not mapping more of an IRM than is left on it). eBRCs on file are listed; an eBRC whose GST invoice or SB differs from the return is flagged.

x The app does not generate eBRCs - DGFT does that, free. It fills in DGFT's bulk upload sheet for you.

## Refunds and drawback

From your own register and ICEGATE's status downloads, each shipping bill's **IGST refund** is *received*, *short*, *held* (ICEGATE shows a code), *not received*, *not due back yet* (within 45 days of shipment) or *unknown*; the **drawback** likewise (serious after 180 days). A refund is flagged only **on evidence** - your register lists it as not received, or ICEGATE shows a code. With no evidence it is *unknown*: no alarm, but the shipping bill goes on the **ICEGATE look-up list**.

Every shipping bill to look up - in its open entry, in its invoice's file and in the look-up list - shows **Look it up on ICEGATE**: the port, the SB number and the date the way ICEGATE's form asks for them (a click copies each one), and **Open the shipping bill enquiry**, which opens ICEGATE's public enquiry in your browser. That page needs no login, but you type its captcha. The IGST refund status needs your ICEGATE login: *Services > Enquiries > ICEGATE Enquiry*. Download or copy what ICEGATE shows, add it here, and the codes and scrolls fill in.

x The app itself never goes to ICEGATE: ICEGATE offers no way for other software to ask it, and its pages sit behind a login and a captcha that the app does not fill in for you.

| Code | Means | What to do |
|---|---|---|
| SB000 | Validated: matched with GSTR-1; the refund goes into a scroll and is paid to your bank | Nothing on ICEGATE; if no money arrives within a month of the scroll, check your bank account is validated for refunds |
| SB001 | Shipping bill details wrong in GSTR-1 | Correct Table 6A through Table 9A; it costs nothing |
| SB002 | EGM not filed by the shipping line | Ask your forwarder or the line to file or correct it; the refund validates by itself |
| SB003 | GSTIN on the shipping bill is not the return's | Same business: an undertaking to customs. Otherwise correct the return |
| SB004 | Sent twice; the first record counts | Nothing, unless the first was the wrong one |
| SB005 | Invoice number on the shipping bill and in 6A differ | Table 6A wrong: amend through 9A. Shipping bill wrong: the port route - a letter, the Annexure A table and a ₹1,000 challan per shipping bill |
| SB006 | Gateway EGM not available (goods from an inland depot) | Ask the line to file the gateway EGM at the port |

!shot 24-ex-entry | A refund entry: what was checked, and the ICEGATE code with what to do about it.

ICEGATE's list of drawback waiting for proof that the buyer paid is compared with your payments: *paid here - make sure the eBRC carries this shipping bill*; *not tracked here*; or *unpaid - proof due by …*, serious once the date passes (Rule 18 of the Drawback Rules, 2017, allows recovery).

## Your own yes or no

Three questions in every invoice's file take your answer when the documents cannot give it: **Filed in GSTR-1?** under its Table 6A line, **Refund received?** under the IGST refund and **Drawback received?** under the drawback (the last two only where something was paid or claimed). Each has **Yes** and **No**; the one that counts is pressed - green for yes, amber for no - and the line beside it says where the answer came from:

- *From the documents* - the return, your register or ICEGATE's download says so. Press the other button if it is wrong.
- *No document says yet* - nothing on file answers it. Press Yes or No.
- *You marked it … on (date)* - your answer, with what the documents say if they disagree, and **Undo** to go back to them.

Your answer counts everywhere the documents' would: the register's columns, the open entries, the figures, the ICEGATE look-up list and the downloads. A GSTR-1 you mark **filed** clears *not in your GSTR-1* even when that month's return is not added (and a return downloaded before it was filed can be marked **not filed**). One you mark **not filed** becomes an open entry, serious when IGST was paid, even with no return on file. A refund or drawback you mark **received** stops being chased; one you mark **not received** is chased like any other - held if ICEGATE gave a code, not due back yet within 45 days of shipment (30 for drawback), late after.

x A refund marked received by hand has no date or amount of its own: the file shows the amount paid on the shipping bill as having come back.
> Your answer stands even when a document added later says otherwise - the file shows both. Undo it once the document is right.

## The bank's EDPMS statement

Three checks, when the statement is on file: the bank shows a bill **open** that is paid here (ask the bank to close it, giving the IRM or eBRC); **closed** with no receipt on file here (add the advice or IRM); or **missing** - a shipping bill inside the statement's dates, unpaid here, that the bank does not list (ask the bank to link it to your AD code). A later due date on the statement is taken as the bank's extension.

## Everything it checks

| Check | When it fires | Serious when |
|---|---|---|
| Not in GSTR-1 | A shipping bill exists, the month's GSTR-1 is on file, and the invoice is not in Table 6A - or you marked it *not filed* (unless you marked it *filed*) | IGST was paid (WPAY) |
| In Table 6A twice | Two 6A lines for one invoice | - |
| SB001 | Table 6A's shipping bill number is blank, or its number, port or date differs from the shipping bill | WPAY |
| SB005 | The 6A invoice number differs from the shipping bill's, character for character, or the dates differ | WPAY |
| IGST differs | Both WPAY, IGST more than ₹1 apart | The shipping bill's is higher |
| WPAY/LUT differs | 6A's export type is not the shipping bill's | Always |
| SB003 | The shipping bill's GSTIN is not the GSTR-1's (says whether the PAN is the same) | WPAY |
| No shipping bill | A 6A line or invoice more than 15 days old with no shipping bill | - |
| No bill of lading | A shipping bill more than 10 days old with no BL | - |
| Payment overdue / due soon | The deadline passed / is within 90 days (notes Rule 96B or 96A, and the ₹10 lakh declaration from 1 Oct 2026) | Overdue |
| Payment to confirm | A suggested payment (Yes, No, or split it over several invoices) | - |
| Advance / money not placed | A payment with money left and nothing to suggest, or left over from your split (*Choose the invoices it is for*) | - |
| eBRC to generate / to correct | See eBRCs | - |
| Documents not placed | An eBRC, BL or file that belongs to nothing on file | - |
| IGST refund not received / short / not entered | See Refunds (short means more than ₹100 less came back; not entered means ICEGATE shows it paid but your register does not) | Not received |
| Drawback not received / proof of payment | See Refunds | After 180 days / once proof is overdue |
| EDPMS open / closed / missing | See EDPMS | - |

!shot 25-ex-sb005 | An SB005 mismatch: which side looks wrong, and the route to fix it.

For an **SB005** mismatch the app works out the fix route: if your invoice agrees with the shipping bill, the slip is in Table 6A (**Table 9A**, free); if your invoice agrees with 6A, or the numbers are entirely different (a second invoice series), the shipping bill is wrong (**the port route**, ₹1,000 a shipping bill); if only a digit or the date differs and there is no invoice to settle it, it asks you to add the invoice (**check first**).

x Links between documents treat O and 0, and I, L and 1 as the same (scans confuse them); comparisons of 6A with the shipping bill do not.
x If both the invoice number and the shipping bill number in 6A are wrong, the 6A line cannot be joined to its invoice: it appears as a separate invoice with no shipping bill, and the real invoice as not in GSTR-1.
x A difference only in leading zeros (033 against 0033) is sent to the port route, even with your invoice added.
x Ticking an entry off does not change the GSTR-1 words in the register, the summary figures, the drafts or the Excel's *Needs a look* sheet.

## Drafts

!shot 28-ex-drafts | Drafts: the papers that fix what is listed, filled in from the register.

Only drafts with something in them are listed. Each is a draft: read it, fill in what is in square brackets, and sign it. The sender is your business from Settings, or the exporter on the documents.

- **SB005 pack** (zip) - for each port, a request letter to the customs IGST refund section, the **Annexure A** table (SB number and date, invoice number and date as on the SB and as in GSTR-1, IGST) as Word and Excel, and a challan schedule for ₹1,000 a shipping bill. Only cases where the shipping bill is the wrong side go in; *check first* cases go to the 9A worksheet instead.
- **Table 9A worksheet** (Excel) - every 6A line to amend, with the original and revised details, what changes and the date to amend by; and a sheet of exports missing from 6A to report in the current month.
- **SB003 undertaking** (Word) - for shipping bills filed under another GSTIN of the same PAN.
- **EDPMS declaration** (zip) - to the bank, for bills up to ₹10,00,000: bank-charge reductions, and overdue bills if they will not come.
- **Extension request** (zip) - to the bank, for bills overdue or falling due, asking for 180 days more.
- **ICEGATE look-up list** (Excel) - shipping bills whose refund or drawback is held, not received, or has no status on file, each with a link to ICEGATE's shipping bill enquiry and its date the way that page asks for it.

x Port names are known for Nhava Sheva (JNPT), Mundra, Mumbai and Chennai; other ports are named by their code.
x The extension request always asks for 180 days; the declaration draft does not check the 1 October 2026 start date.
x There is no PDF or CSV for Exports - Excel, Word and zip files.

## Downloads

**The register** (Excel: the register with five stage columns, every open entry, money in, eBRCs), **the register in your own sheet's layout** (Excel: a tab per year with your columns - GST and drawback received and the date, the error code, payment), and **the DGFT bulk eBRC sheet** (when there are rows).

## Picked up from your Downloads folder

Save an ICEGATE status download - or a DGFT IRM or eBRC list, or your bank's EDPMS statement - and when you come back to the app, the register offers it at the top: **In your Downloads folder**, what it is, its name and when it was saved, with **Add it** and **Not this one**. Add it and it waits under *Check what was read*, like anything you drop in; Not this one stops it being offered.

!shot 35-ex-downloads | In your Downloads folder: an ICEGATE refund status just saved, offered to the register.

+ Only Excel and CSV files saved in the last week, of a kind the register reads and not already in it, are offered.
+ The app only reads the Downloads folder: nothing in it is moved, changed or deleted, and the file stays there after it is added.
x A PDF saved from ICEGATE is not offered; drop it in yourself.

## Documents

!shot 29-ex-docs | Documents: each named for what it is, filed by kind, with Open, Save a copy, Share, Show in folder and Remove.

Every document posted, newest first, with a **Show** filter by kind: what was read (*Shipping bill 4569456 · INNSA1 · 1 invoice*), where it is kept (*Shipping bills › SB 4569456 of 08-Sep-2026 (INNSA1) - KE-26-27-021.pdf*), the name it came in with, *corrected by you* if it was, what the AI checker made of it if you have it, the invoices it was filed against, and **Open**, **Save a copy**, **Share**, **Show in folder**, **Correct** (the correcting window) and **Remove** (which deletes the kept copy, works the register out again, and is written into the history).

## The AI checker

An add-on, for a second opinion on what was read. Once you get it (*Settings > AI checker*), a small AI model on your own computer reads each shipping bill, bill of lading, bank advice and invoice again on its own - those waiting under *Check what was read* and those already posted - and compares what it reads with what the app read, your corrections included.

- **Where it reads the same**, the document says *the AI reads it the same* - in *Check what was read* and in *Documents*.
- **Where it reads differently**, a document waiting to be checked shows both readings (*Amount: the app read EUR 2,050.00; the AI reads EUR 30,050.00*), and a posted one gets an open entry under **Read differently by the AI**, saying which fields. Open the original and see which is right, then: **Use the AI's reading** (kept as your corrections, in the history, undone like any other), **Correct it yourself**, or **The app read it right - take this off the list**.
- A document you correct, or read again, is checked again.

+ It works only on your computer: nothing you add is sent anywhere. It starts after the app has been left alone for a minute, reads one document at a time, and never takes more than half the processor; with a graphics card a document takes a few seconds, on the processor alone longer.
+ Nothing it reads goes into the register by itself; it only points at what to look at.
+ With Invoice Bridge on the same computer, one copy serves both: the checker uses the AI the bridge downloaded, and the bridge uses this one. Nothing is downloaded twice.
+ A number it copies with its date after it (*KE/25-26/062-04/03/2026*) is compared without the date; a field it cannot read is left out, not counted against the app.
x It reads only PDFs and pictures - not spreadsheets, whose cells are read exactly already - and only the first two pages of each; lists (GSTR-1, IRM, eBRC, ICEGATE and EDPMS downloads) are not checked.
x It can be wrong too. Where the two readings differ, the original decides.
x It needs about 8 GB free to get and keeps about 4 GB. It is not part of the app's updates.

## What Exports cannot do

x Log in to ICEGATE, the GST portal, DGFT or a bank, or fetch anything from them. ICEGATE has no interface for exporters; the app reads the files you download. It never stores portal passwords or gets past captchas.
x Generate eBRCs, or file anything.
x Read the GST portal's failed-invoice list, or an export ledger CSV; draw up a Rule 96B schedule; track RoDTEP (it is shown, not tracked); handle service exports (SOFTEX).
x Use foreign credits from a Bank reconciliation - export registers are separate from runs.
