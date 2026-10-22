"""Make the sample invoice the Invoice Bridge's tests read.

    python tools/synth_invoice.py

Writes app/bridge/tests/fixtures/kestrel_audit_igst.pdf: a two-page, inter-state
(IGST) service invoice with a text layer, laid out the way a real one is (the
reader works from where each piece of text sits). Every name, address, number
and GSTIN in it is invented; the GSTINs carry a correct check digit.
"""

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "app" / "bridge" / "tests" / "fixtures" / "kestrel_audit_igst.pdf"

# (page, x, y, size, bold, text)
SPANS = [
    (0, 31.2, 44.6, 14.0, True, "TAX INVOICE 5500100-26004852"),
    (0, 537.2, 39.4, 8.0, False, "Page no 1/2"),
    (0, 38.0, 62.0, 9.0, True, "KESTREL AUDIT (INDIA) PRIVATE LIMITED"),
    (0, 493.1, 62.0, 9.0, True, "SAFFRON IMPEX"),
    (0, 38.0, 74.3, 9.0, False, "12 Sample Business Park (Ground Floor),"),
    (0, 38.0, 86.4, 9.0, False, "Example Estate, MIDC,"),
    (0, 38.0, 98.4, 9.0, False, "Cross Road A, Andheri (East),"),
    (0, 38.0, 110.5, 9.0, False, "400093 Mumbai"),
    (0, 38.0, 122.5, 9.0, False, "CIN No: U00000MH2000PTC000000"),
    (0, 38.0, 146.8, 10.0, True, "Issuing Office"),
    (0, 38.0, 158.7, 9.0, False, "NOIDA - KAIL"),
    (0, 38.0, 170.8, 9.0, False, "4TH FLOOR"),
    (0, 38.0, 182.9, 9.0, False, "EXAMPLE BUSINESS PARK"),
    (0, 38.0, 194.9, 9.0, False, "D-1 & 2"),
    (0, 38.0, 207.0, 9.0, False, "SECTOR 3"),
    (0, 38.0, 219.0, 9.0, False, "201301 NOIDA"),
    (0, 38.0, 231.1, 9.0, False, "Place of Issue: 09 Uttar Pradesh"),
    (0, 38.0, 243.2, 9.0, False, "GSTIN/Unique ID: 09XXXCX3333X1ZV"),
    (0, 38.0, 255.2, 9.0, False, "PAN: XXXCX3333X"),
    (0, 38.0, 267.3, 9.0, False, "Tel: .120 0000000"),
    (0, 38.0, 290.0, 9.0, False, "Direct Contact: SAMPLE "),
    (0, 38.0, 302.1, 9.0, False, "CONTACT"),
    (0, 38.0, 324.9, 9.0, False, "Contract No: 10000001"),
    (0, 38.0, 336.9, 9.0, False, "SAFFRON IMPEX - Wood Mgt"),
    (0, 38.0, 349.0, 9.0, False, "Systems"),
    (0, 38.0, 361.0, 9.0, False, "Proposal No: 000000000000000AAA"),
    (0, 38.0, 373.1, 9.0, False, "Signed On: 04/05/2026"),
    (0, 38.0, 385.2, 9.0, False, "BU: 00000A0"),
    (0, 438.5, 74.3, 9.0, False, "1 SAMPLE ROAD, PHASE 1, EXAMPLE "),
    (0, 522.5, 86.4, 9.0, False, "INDUSTRIAL"),
    (0, 551.5, 98.4, 9.0, False, "AREA"),
    (0, 500.0, 110.5, 9.0, False, "302022 JAIPUR"),
    (0, 510.0, 122.5, 9.0, False, "08 RAJASTHAN"),
    (0, 556.5, 134.6, 9.0, False, "India"),
    (0, 426.0, 146.7, 9.0, False, "GSTIN/Unique ID: 08ZZZPZ1111Z1Z4"),
    (0, 503.5, 158.7, 9.0, False, "PAN: ZZZPZ1111Z"),
    (0, 537.3, 181.7, 10.0, True, "Ship To:"),
    (0, 489.0, 193.6, 9.0, False, "SAFFRON IMPEX  "),
    (0, 438.5, 205.6, 9.0, False, "1 SAMPLE ROAD, PHASE 1, EXAMPLE "),
    (0, 522.5, 217.7, 9.0, False, "INDUSTRIAL"),
    (0, 551.5, 229.8, 9.0, False, "AREA"),
    (0, 500.0, 241.8, 9.0, False, "302022 JAIPUR"),
    (0, 510.0, 253.9, 9.0, False, "08 RAJASTHAN"),
    (0, 556.5, 265.9, 9.0, False, "India"),
    (0, 426.0, 278.0, 9.0, False, "GSTIN/Unique ID: 08ZZZPZ1111Z1Z4"),
    (0, 498.7, 299.2, 10.0, True, "Date: "),
    (0, 526.0, 299.2, 10.0, True, "18/08/2026"),
    (0, 478.4, 311.3, 10.0, True, "Due date: "),
    (0, 526.0, 311.3, 10.0, True, "17/09/2026"),
    (0, 479.5, 332.2, 9.0, False, "Customer no: 10000002"),
    (0, 442.5, 344.3, 9.0, False, "Place of Supply: 08 RAJASTHAN"),
    (0, 504.0, 356.3, 9.0, False, "Supply Type: B2B"),
    (0, 38.0, 425.6, 9.0, False, "Service description"),
    (0, 314.5, 424.5, 9.0, False, "SAC/HSN "),
    (0, 404.8, 425.6, 9.0, False, "Quantity"),
    (0, 483.1, 425.6, 9.0, False, "Rate"),
    (0, 513.5, 424.5, 9.0, False, "Total Amt (INR)"),
    (0, 38.0, 447.7, 9.0, False, "KAMAIN Stage 2 "),
    (0, 43.7, 483.9, 9.0, False, "AUDIT DATE:19.05-20.05.2026"),
    (0, 43.7, 495.9, 9.0, False, "AUDITOR:SAMPLE AUDITOR"),
    (0, 314.5, 446.3, 9.0, False, "998349"),
    (0, 420.8, 447.7, 9.0, False, "2.00"),
    (0, 462.1, 447.7, 9.0, False, "39,375.00"),
    (0, 536.0, 446.3, 9.0, False, "78,750.00"),
    (0, 38.0, 519.8, 9.0, False, "KAFEES Registration & Certification F ee"),
    (0, 314.5, 518.4, 9.0, False, "998349"),
    (0, 420.8, 519.8, 9.0, False, "1.00"),
    (0, 467.1, 519.8, 9.0, False, "5,000.00"),
    (0, 541.0, 518.4, 9.0, False, "5,000.00"),
    (0, 38.0, 567.8, 9.0, False, "KAFEES Registration & Certification F ee"),
    (0, 314.5, 566.4, 9.0, False, "998349"),
    (0, 420.8, 567.8, 9.0, False, "1.00"),
    (0, 462.1, 567.8, 9.0, False, "35,000.00"),
    (0, 536.0, 566.4, 9.0, False, "35,000.00"),
    (0, 38.0, 615.7, 9.0, False, "KAFEES FSC AAF FEES "),
    (0, 43.7, 651.9, 9.0, False, "258 USD@95.596= 24,664INR DATED 18.08.2026"),
    (0, 314.5, 614.3, 9.0, False, "998349"),
    (0, 420.8, 615.7, 9.0, False, "1.00"),
    (0, 462.1, 615.7, 9.0, False, "24,664.00"),
    (0, 536.0, 614.3, 9.0, False, "24,664.00"),
    (1, 31.2, 44.6, 14.0, True, "TAX INVOICE 5500100-26004852"),
    (1, 537.2, 39.4, 8.0, False, "Page no 2/2"),
    (1, 38.0, 60.2, 9.0, False, "GST Desc"),
    (1, 185.0, 60.2, 9.0, False, "GST%"),
    (1, 247.8, 60.2, 9.0, False, "Taxable "),
    (1, 251.3, 72.2, 9.0, False, "Amount"),
    (1, 333.3, 60.2, 9.0, False, "GST"),
    (1, 38.0, 95.3, 9.0, False, "V/IN21 IGST 18%"),
    (1, 189.0, 95.3, 9.0, False, "18.00"),
    (1, 244.8, 93.7, 7.5, False, "143,414.00"),
    (1, 311.8, 95.3, 9.0, False, "25,814.52"),
    (1, 38.0, 119.0, 9.0, False, "Payment Conditions: 30 DAYS NET"),
    (1, 38.0, 131.1, 9.0, False, "Reverse Charge Applicable: NO"),
    (1, 38.0, 143.1, 9.0, False, "Interest on overdue payments: 1.5% per month"),
    (1, 37.8, 164.7, 9.0, False, "Pay to: KESTREL AUDIT (INDIA) PRIVATE LIMITED"),
    (1, 37.8, 176.8, 9.0, False, "IFSC/RTGS/NEFT Code: EXMP0000001"),
    (1, 37.8, 188.8, 9.0, False, "Account number: 000000000001"),
    (1, 37.8, 200.9, 9.0, False, "SWIFT: EXMPINBBXXX"),
    (1, 37.8, 225.0, 9.0, False, "Bank Name: Example Bank"),
    (1, 37.8, 237.0, 9.0, False, "Branch: Mumbai, India"),
    (1, 361.9, 67.0, 9.0, False, "Net Amount"),
    (1, 529.7, 67.0, 9.0, False, "143,414.00"),
    (1, 361.9, 93.0, 9.0, False, "GST Total"),
    (1, 534.7, 93.0, 9.0, False, "25,814.52"),
    (1, 361.9, 118.3, 9.0, True, "Total"),
    (1, 529.7, 118.2, 9.0, True, "169,228.52"),
    (1, 361.9, 143.5, 9.0, False, "Currency"),
    (1, 557.5, 144.5, 10.0, False, "INR"),
    (1, 236.5, 277.1, 9.0, False, "Website : www.example.com"),
    (1, 33.6, 301.2, 9.0, False, "Note: 1) This is electronically generated document and does not require physical "
                                 "signatures 2) This invoice is issued in accordance with "),
    (1, 235.7, 313.2, 9.0, False, "the General Terms and Conditions"),
]


def main() -> None:
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page(width=612.1, height=792.1)
        for pg, x, y, size, bold, text in SPANS:
            if pg == n:
                page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT, garbage=4, deflate=True)
    print(OUT)


if __name__ == "__main__":
    main()
