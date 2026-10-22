# Every number the app uses

| What | Value | Where it matters |
|---|---|---|
| GST: rounding allowed | ₹2 (Settings) | Matched, bill number differs, entered in parts |
| GST: date window | 45 days (Settings) | Probably the same bill, entered in parts |
| GST: names alike enough | 50% (40% for a same-number pair, 35% for a probable match) | Bill number differs, amount differs |
| GST: probable match | amount within 0.1% or ₹2, score at least 0.72 | Probably the same bill |
| GST: entered in parts | up to 3 vouchers, from the 14 largest candidates | Entered in parts |
| GST: rounding-only difference | up to ₹10 | Amount differs |
| GST: dates far apart | more than 31 days | A note on matched pairs |
| GST: Section 16(4) warning | 60 days or fewer before 30 November after the year | Red entries, the deadline figure |
| GST: value does not tie | more than ₹2 or 0.5% | A note on the bill |
| GST: tax slabs | 0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28% (0.3 points either way) | Mixed or impossible rate |
| Bank: difference allowed | ₹1 (Settings) | Amount differs, several-in-one |
| Bank: same transaction | 7 days (Settings) | Matched |
| Bank: timing difference | up to 45 days (Settings) | Timing difference |
| Bank: amount differs | up to ₹500 or 5% | Amount differs |
| Bank: several in one | 2 to 4 entries, from 14 candidates | Several entries in one |
| Bank: names alike | 62 out of 100 | Amount differs, notes |
| Bank: stale cheque | over 90 days | Stale cheques |
| Bank: large cash | ₹2,00,000 | Large cash movements |
| Bank: running balance | ₹1 a row | Running-balance check |
| Bank and party: working adds up | within ₹0.50 | "It adds up" |
| Party: difference allowed | ₹1 (Settings) | Amounts differ, interest and TDS totals |
| Party: same document | 10 days | Agrees |
| Party: timing | 11 to 60 days | Same amount, different dates |
| Party: dispute | up to 12% | Amounts differ |
| Party: sheet name match | 75% alike | Choosing your ledger's sheet |
| Party: long open | over 180 days | Red entries |
| Exports: due soon | 90 days (Settings) | Payment due soon |
| Exports: bank charges | up to 1%, at most 150 currency units | Paid, short by charges |
| Exports: the buyer's own invoices | remitter's name 60% alike | Which invoices a payment is for |
| Exports: no shipping bill | 15 days after the invoice | No shipping bill |
| Exports: no bill of lading | 10 days after the shipping bill | No bill of lading |
| Exports: refund due back | 45 days (drawback 30) | *Not due back yet* |
| Exports: refund short | more than ₹100 | IGST short |
| Exports: drawback serious | after 180 days | Drawback not received |
| Exports: SB005 port fee | ₹1,000 a shipping bill | SB005 pack |
| Exports: declaration limit | ₹10,00,000, from 1 October 2026 | EDPMS declaration |
| Exports: extension asked | 180 days | Extension request |
| Exports: over a year overdue | 365 days | Advance or letter of credit needed |
| Reading: PDF pages | 40 | All PDFs |
| Reading: text page | 90 characters | Text or recognition |
| Reading: scan resolution | 200 dpi | Recognition |
| Reading: rows per sheet | 20,000 (bank, party, exports) | Spreadsheets |
| Reading: GSTR-2B columns | 60 | GSTR-2B |
| Reading: small pictures enlarged | to 1,500 pixels, at most 3 times | Screenshots |
| Reading: large pictures | words found on a copy of at most 4,000 pixels; pages drawn at most 5,000 pixels | Recognition |
| Reading: recognition threads | 2 to 6 | Speed |
| Reading: low-confidence word | below 0.62 | Notes |
| Lists: open entries shown | 150 at a time | Needs a look |
| Lists: all entries | 100 a page | All entries |
| Lists: runs | 8 on the start page, 10 in the run menu, 200 in all | Runs |
| App address | 127.0.0.1, port 8000 to 8011 | Starting |
| Update checks | 20 seconds after start, then every 6 hours (installed app, setting on) | Updating |
| Update downloads | up to 1 GB; 4 seconds for each of GitHub's addresses to connect, then 20 seconds without data before giving up | Updating |

# Glossary

| Word | Meaning |
|---|---|
| AD code | The code of the bank branch that handles your export proceeds |
| BRS | Bank reconciliation statement: the working from your book balance to the bank's |
| Bill of lading (BL) / airway bill | The shipping line's or airline's receipt for the goods; its shipped-on-board date is the day they left |
| Bridge | The working from your ledger balance to a party's |
| Drawback | A refund of customs duty on the inputs of exported goods, claimed on the shipping bill |
| eBRC | Electronic bank realisation certificate, generated on the DGFT portal: proof that an export was paid for |
| EDPMS | The RBI's Export Data Processing and Monitoring System, in which your bank tracks every shipping bill until it is paid |
| EGM | Export general manifest, filed by the shipping line; without it the IGST refund does not validate (SB002) |
| FEMA | The Foreign Exchange Management Act, whose regulations set the time allowed to bring export proceeds in |
| FIRA / FIRC | The bank's advice or certificate of an inward remittance |
| GSTR-1 | The return of your sales. Table 6A holds exports; Table 9A amends earlier returns |
| GSTR-2B | The statement, on the GST portal, of the bills your suppliers reported - the basis of your input tax credit |
| GSTR-3B | The monthly summary return in which tax is paid and credit taken |
| ICEGATE | Customs' portal, where shipping bills, refund and drawback status are shown |
| IEC | Importer-exporter code |
| IGST refund | The refund of IGST paid on goods exported with payment of tax (WPAY), paid by customs once the shipping bill matches GSTR-1 |
| IRM | Inward remittance message: a bank's record of a payment from abroad, listed on the DGFT portal |
| ITC | Input tax credit |
| LEO | Let export order: customs' permission for the goods to leave |
| LUT | Letter of undertaking: exporting without paying IGST (WOPAY) |
| OCR / recognition | Reading text from a picture of a page |
| Place of supply | The state where a supply is treated as made; decides whether CGST/SGST or IGST applies |
| Realisation | Receiving the export proceeds |
| Reverse charge (RCM) | Tax the buyer pays instead of the supplier, then claims as credit |
| RoDTEP | Remission of duties and taxes on exported products, credited as an e-scrip |
| Rule 96A / 96B | GST rules: an export under LUT not paid for in time becomes liable to IGST (96A); a refund taken on an export not paid for in time must be repaid (96B) |
| Section 16(4) | The time limit for claiming input tax credit: 30 November after the end of the financial year |
| Section 17(5) | Credits the law blocks whatever the bill says (motor vehicles, food, personal use…) |
| Shipping bill (SB) | The customs declaration for exported goods |
| UTR | Unique transaction reference of an NEFT or RTGS payment |
| WebView2 | The browser engine built into Windows that draws the app's own window |
| WPAY / WOPAY | Export with payment of IGST / without payment of IGST (under LUT) |
