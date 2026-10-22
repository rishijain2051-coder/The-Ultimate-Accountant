# Reading your files

Everything the app knows comes from files you give it. This chapter says which files each place accepts, how they are read, how scans and photos are read, and what is kept afterwards.

## What each place accepts

| Where | Accepts | Refuses |
|---|---|---|
| GST: purchase register, GSTR-2B, sales register, GSTR-1 | Excel `.xlsx` only | `.xls` (the picker lists it, but it is refused: save it as .xlsx), `.csv`, `.json`, PDF, pictures |
| Bank: the statement | `.xlsx`, `.csv` / `.txt` / `.tsv`, PDF (with text or scanned), pictures: `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp` | old `.xls`, `.gif`, `.heic`, Word files |
| Bank: your bank book | The same as the statement | The same |
| Party: their statement and your ledger | The same as the bank statement | The same |
| Exports | PDF, pictures, `.xlsx`, `.csv`, the GSTR-1 `.json` | old `.xls` (save as .xlsx), anything else |

A file is recognised by what is inside it, not only by its name: a PDF renamed `.xlsx` is still read as a PDF.

## Spreadsheets

- Every sheet is read, formulas giving their last saved values. Column order never matters: columns are found by their headings.
- The heading row is searched for near the top: within the first 25 rows (GSTR-2B), 30 (Tally registers, bank and party files) or 20 (GSTR-1). The heading must sit on one row, although a GSTR-2B two-row heading is understood.
- For a bank statement, the sheet with the most rows is used. For a party's workbook with a sheet per ledger, the sheet whose ledger name matches the party on their statement is used.
- CSV files are read in UTF-8, Windows or Latin encodings, with the separator (comma, semicolon, tab or bar) detected.
x At most 20,000 rows per sheet are read for bank, party and export files; any more are dropped without a warning.
x Only the first 60 columns of a GSTR-2B sheet are read.
x There is no way to tell the app which column is which when it cannot find a heading - the file must carry recognisable headings.

## PDFs

Each page is looked at on its own. A page with at least 90 characters of real text is read as text, which is exact and fast. A page with less - a scan - is turned into a picture at 200 dots per inch and read by text recognition (below). One PDF can mix both.

On a text page the app uses a ruled table if it finds one; otherwise it cuts the page into columns from where the words sit under the headings, placing each word by its centre so right-aligned figures land in the right column. The pages of one statement are stacked into one table, with repeated headings dropped.

x Only the **first 40 pages** of a PDF are read. Pages after the 40th are ignored, and nothing says so.
x Password-protected PDFs cannot be opened.
x On a text page only the first ruled table is used.

## Scans and photos

Pictures, and PDF pages without text, are read by a text-recognition model that ships inside the program (RapidOCR, PP-OCRv3). It works offline, on the computer's processor, using between two and six processor threads.

**How fast.** On a current laptop a scanned page takes about 1 to 1.5 seconds; a photo of a page about a second. The engine loads in the background when the app starts, so the first scan does not wait for it. Version 2.1.2 made recognition about a fifth faster than before (words are read three at a time, which against the text layers of 24 real PDF pages was both quicker and slightly more accurate than the library's default), and it no longer re-reads, by recognition, PDFs whose text layer is perfectly good.

**How it rebuilds a table.** Recognised words are grouped into lines by their height on the page, and into columns by where their left edges cluster; the pages of one scanned statement share one set of columns so figures do not shift sideways from page to page. A small screenshot (under 1,500 pixels) is enlarged up to three times first, because at screen size letters run together.

**How you know it read correctly.** Every recognised word carries a confidence; the page and document confidence are averages, and the source line under a check says, for example, *"the statement was read by recognition at 93% confidence"*. Every reading by recognition adds the note *"Some pages were read by recognising the image, so figures should be spot-checked against the original."* For bank statements the running balance is checked row by row; for interest statements every product, total and the interest itself are recomputed. A figure misread shows up as a residual the working cannot explain.

+ Reads scanned PDFs, phone photos and screenshots of printed statements, bills of lading and invoices, with no internet and nothing to install.
+ Very large pictures are handled safely: words are found on a copy no larger than 4,000 pixels (then read from the full picture), a PDF page is never drawn larger than 5,000 pixels, and memory is handed back after every page - one poster-sized scan can no longer take gigabytes.
x It reads **printed** text. Handwriting is not read.
x Pages must be **upright**. Sideways or upside-down photos are not turned; turn them before adding them. (The part that guesses whether a line is upside down is switched off on purpose - it once read "6,063.00" as "00'E90'9".)
x It is tuned for English letters and digits. Hindi or other scripts are not read.
x A multi-page TIFF gives only its first page. Several photos of one statement cannot be combined into one statement.
x Only the statement's (or their statement's) reading is reported on screen. If your bank book or ledger was itself a scan, that is not flagged.
> Recognition is careful, not perfect. Always spot-check figures read from a scan against the original, especially when the app reports a residual or a broken running balance.

## Every file is kept

Every file you give the app is kept in the data folder, in a folder per run or register, and a folder per kind inside it. Reconciliation files keep their own names, with what they are in front (*Tally - PurchaseRegister.xlsx*, *Bank statement - …*, *Their statement - …*). Export documents are named for what was read from them - *SB 3183667 of 20-Aug-2024 (INNSA1) - KE-24-25-088.pdf* rather than ICEGATE's download number - and filed under *Shipping bills*, *Bills of lading*, *Invoices*, *GSTR-1*, *Bank advices*, *DGFT*, *ICEGATE*, *Your register* or *Not recognised*.

!shot 14-files | The files a check was made from, with Save a copy, Share and Show in folder, and the folder they are kept in.

Each kept file has:

- **Open** - for PDFs and pictures, which open in your browser;
- **Save a copy** - under its kept name, to your downloads;
- **Share** - Windows' share window (mail, nearby sharing, and apps that accept files), for the kinds of file a browser can share: PDFs and pictures. Anything else is saved as a copy instead, and the app says so;
- **Show in folder** - File Explorer, with the file picked out, ready to drag into an email or a chat.

Under each list: *Kept on this computer in …* with **Show the folder** and **Save them all as one zip**. In Exports, each invoice also has **Save them as one zip** for its own papers (shipping bill, bill of lading, invoice and bank advice).

+ The same file added twice to the same run or register is kept once.
+ Deleting a run or register deletes its folder; removing one export document deletes its kept copy.
x A check made with *Keep every check* switched off keeps nothing.
x Checks made before version 2.1.1 kept no files; they say *"Not kept: this check was made before the app kept its files."*
x Excel files cannot go through the share window - that is the browser's rule, not the app's.
