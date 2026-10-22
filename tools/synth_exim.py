"""
Synthetic export paperwork for the fictional Kesari Textile Mills.

    python tools/synth_exim.py

Writes SampleData/Exports/: ICEGATE-style shipping bills, forwarders' bills of
lading (one of them a scan), GSTR-1 Table 6A as the portal's JSON and as the
offline tool's sheet, the bank's inward remittance advices, the DGFT IRM and
eBRC downloads, ICEGATE's shipping bill status sheet and a few of the
exporter's own invoices.

Every problem the Exports module looks for is planted once, on purpose - a typo
on a shipping bill (SB005), a second invoice series, a wrong port in 6A,
another branch's GSTIN, short IGST, a payment waiting to be matched, an
advance, a missing bill of lading - and verify() reads everything back and
fails if any of them is missed. The layouts copy real documents; every name,
number and figure here is invented.
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from generate_sample_data import ENTITY_NAME, make_gstin, real_client_words  # noqa: E402

OUT = ROOT / "SampleData" / "Exports"
AS_OF = date(2026, 9, 26)           # the dates below are laid out around this day
SEED = 20260926
GSTIN = "08AMHCK7032W1ZX"
PAN = GSTIN[2:12]
IEC = PAN                           # since 2021 the IEC is the PAN
BRANCH_GSTIN = make_gstin("06", PAN)
ADDRESS = ["PLOT 41, SITAPURA INDUSTRIAL AREA", "JAIPUR 302022, RAJASTHAN"]
AD_CODE = "6390417"
IFSC = "RJCB0006390"
BANK = "RAJPUTANA COMMERCIAL BANK LTD"
BROKER = "PINKCITY CUSTOMS CLEARING AGENCY"

PORTS = {
    "INJAI6": ("ICD KANAKPURA, JAIPUR", "LAND", "Jaipur ICD"),
    "INMUN1": ("MUNDRA PORT, KUTCH, GUJARAT", "SEA", "Mundra"),
    "INNSA1": ("JNCH, NHAVA SHEVA, TAL:URAN, DIST-RAIGAD", "SEA", "Nhava Sheva"),
}
BUYERS = {
    "NORD": ("NORDLICHT HEIMTEXTIL GMBH", "GROSSE ELBSTRASSE 212, 22767 HAMBURG", "GERMANY", "DEHAM",
             "Hamburg", "EUR", "HANSEATISCHE HANDELSBANK AG, HAMBURG"),
    "MAPLE": ("MAPLE & THREAD HOME INC", "1840 COMMERCE PARKWAY, SAVANNAH GA 31408", "UNITED STATES",
              "USSAV", "Savannah", "USD", "FIRST COASTAL BANK NA, SAVANNAH"),
    "BRAM": ("BRAMLEY LINEN CO LTD", "UNIT 4 CALDER PARK, WAKEFIELD WF2 7AW", "UNITED KINGDOM", "GBFXT",
             "Felixstowe", "GBP", "PENNINE MUTUAL BANK PLC, LEEDS"),
    "SUND": ("SUNDARBAN TRADING PTE LTD", "21 TOH GUAN ROAD EAST, SINGAPORE 608609", "SINGAPORE", "SGSIN",
             "Singapore", "USD", "STRAITS UNION BANK LTD, SINGAPORE"),
    "OLIVA": ("CASA OLIVA SRL", "VIA DEI TESSITORI 18, 59100 PRATO", "ITALY", "ITGOA", "Genova", "EUR",
              "BANCA DEL CHIANTI SPA, FIRENZE"),
    "NOOR": ("AL NOOR TEXTILES TRADING LLC", "WAREHOUSE 7, AL QUOZ 3, DUBAI", "UNITED ARAB EMIRATES", "AEJEA",
             "Jebel Ali", "AED", "GULF CRESCENT BANK PJSC, DUBAI"),
}
PRODUCTS = [
    ("63023100", "COTTON BED LINEN, PRINTED", "PCS", 5),
    ("63041990", "COTTON BEDSPREADS, QUILTED", "PCS", 5),
    ("63049260", "COTTON CUSHION COVERS, BLOCK PRINTED", "PCS", 5),
    ("52083290", "WOVEN COTTON FABRIC, DYED", "MTR", 5),
    ("63029100", "COTTON TABLE LINEN", "PCS", 12),
]

# Every invoice in the sample year, and what is wrong with it on purpose.
#   sb:  None = no shipping bill yet; else (port, days from invoice to SB)
#   plant: the one problem this invoice carries, or "" for none
INVOICES = [
    dict(no="KE/24-25/088", day=date(2024, 8, 16), buyer="MAPLE", value=9850, sb=("INNSA1", 4), bl=True,
         plant="unpaid a year past its deadline"),
    dict(no="KE/25-26/004", day=date(2025, 5, 8), buyer="NORD", value=18450, sb=("INMUN1", 4), bl=True,
         plant=""),
    dict(no="KE/25-26/009", day=date(2025, 6, 14), buyer="MAPLE", value=22800, sb=("INNSA1", 4), bl=True,
         plant="paid, but the payment is not matched yet"),
    dict(no="KE/25-26/014", day=date(2025, 7, 18), buyer="BRAM", value=9600, sb=("INJAI6", 4), bl=True,
         plant="part paid, overdue; no eBRC for the part received"),
    dict(no="KE/25-26/021", day=date(2025, 8, 11), buyer="SUND", value=31250, sb=("INMUN1", 3), bl=True,
         plant="paid short by bank charges"),
    dict(no="KE/25-26/026", day=date(2025, 9, 2), buyer="MAPLE", value=12600, sb=("INNSA1", 3), bl=True,
         plant="shipping bill filed under the Haryana branch GSTIN (SB003)"),
    dict(no="KE/25-26/030", day=date(2025, 10, 6), buyer="OLIVA", value=12300, sb=("INMUN1", 3), bl=True,
         plant="6A quotes the shipping bill number with two digits swapped (SB001)"),
    dict(no="KE/25-26/033", day=date(2025, 11, 17), buyer="MAPLE", value=14700, sb=("INJAI6", 3), bl=True,
         plant="the shipping bill has KF/ for KE/ - a typing slip (SB005)", invoice_file=True),
    dict(no="KE/25-26/037", day=date(2025, 11, 29), buyer="NORD", value=26900, sb=("INNSA1", 4), bl=True,
         plant="6A carries less IGST than the shipping bill"),
    dict(no="KE/25-26/041", day=date(2025, 12, 16), buyer="NOOR", value=88000, sb=("INMUN1", 3), bl=True,
         plant="6A says WPAY but the goods went under LUT"),
    dict(no="KE/25-26/047", day=date(2026, 1, 12), buyer="SUND", value=19400, sb=("INJAI6", 2), bl=True,
         plant="the shipping bill carries commercial invoice CI/2611 (SB005, second series)"),
    dict(no="KE/25-26/052", day=date(2026, 1, 26), buyer="BRAM", value=7850, sb=("INNSA1", 2), bl=True,
         plant="left out of January's GSTR-1"),
    dict(no="KE/25-26/055", day=date(2026, 2, 7), buyer="MAPLE", value=11200, sb=("INMUN1", 3), bl=True,
         plant="twice in February's 6A; its eBRC quotes the invoice as KE/25-26/0055"),
    dict(no="KE/25-26/058", day=date(2026, 2, 23), buyer="OLIVA", value=15600, sb=("INJAI6", 2), bl=False,
         plant="6A gives the port as INMUN1; no bill of lading"),
    dict(no="KE/25-26/062", day=date(2026, 3, 4), buyer="MAPLE", value=24000, sb=("INNSA1", 2), bl="scan",
         plant="6A leaves the shipping bill details blank; its bill of lading is a scan"),
    dict(no="KE/25-26/066", day=date(2026, 3, 18), buyer="NORD", value=9900, sb=("INMUN1", 2), bl=True,
         plant="6A dates the invoice a day late (SB005 on the date)"),
    dict(no="KE/26-27/003", day=date(2026, 4, 13), buyer="SUND", value=16300, sb=("INJAI6", 2), bl=True,
         plant="", invoice_file=True),
    dict(no="KE/26-27/008", day=date(2026, 5, 19), buyer="BRAM", value=12100, sb=("INNSA1", 2), bl=True,
         plant="part paid; no eBRC for the part received"),
    dict(no="KE/26-27/012", day=date(2026, 6, 8), buyer="OLIVA", value=21000, sb=("INMUN1", 2), bl=True,
         plant=""),
    dict(no="KE/26-27/017", day=date(2026, 7, 14), buyer="MAPLE", value=13500, sb=("INJAI6", 2), bl=False,
         plant="no bill of lading, and July's GSTR-1 is not added"),
    dict(no="KE/26-27/021", day=date(2026, 9, 5), buyer="NORD", value=17800, sb=("INNSA1", 3), bl=True,
         plant=""),
    dict(no="KE/26-27/025", day=date(2026, 9, 2), buyer="OLIVA", value=14200, sb=None, bl=False,
         plant="invoiced 24 days ago; no shipping bill on file", invoice_file=True),
    dict(no="KE/26-27/027", day=date(2026, 9, 20), buyer="SUND", value=8600, sb=None, bl=False,
         plant="", invoice_file=True),
]

RATES = {"USD": (83.4, 0.21), "EUR": (91.8, 0.38), "GBP": (106.2, 0.42), "AED": (22.7, 0.057)}


def rate_on(cur, day):
    base, slope = RATES[cur]
    months = (day.year - 2024) * 12 + day.month - 4
    return round(base + slope * months, 2)


def container(rng):
    """An ISO 6346 container number with a valid check digit."""
    owner = rng.choice(["TGHU", "MSKU", "CMAU", "TCNU", "SEGU"])
    serial = f"{rng.randint(100000, 999999)}"
    values = {c: v for v, c in enumerate("0123456789", 0)}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    val, v = {}, 10
    for c in letters:
        if v % 11 == 0:
            v += 1
        val[c] = v
        v += 1
    total = sum((val.get(c) if c.isalpha() else values[c]) * 2 ** i for i, c in enumerate(owner + serial))
    return owner + serial + str(total % 11 % 10)


def build(rng):
    """Lay out every invoice: its shipping bill, items, rate and IGST."""
    for inv in INVOICES:
        buyer = BUYERS[inv["buyer"]]
        inv["currency"] = buyer[5]
        inv["rate"] = rate_on(inv["currency"], inv["day"])
        n = rng.randint(1, 3)
        picks = rng.sample(PRODUCTS, n)
        shares = [rng.uniform(0.6, 1.4) for _ in picks]
        items, left = [], inv["value"]
        for i, (prod, share) in enumerate(zip(picks, shares)):
            val = left if i == n - 1 else round(inv["value"] * share / sum(shares), 2)
            left = round(left - val, 2)
            price = round(rng.uniform(2.2, 19.5), 2)
            qty = max(1, round(val / price))
            items.append({"hs": prod[0], "desc": prod[1], "uqc": prod[2], "gst": prod[3],
                          "qty": qty, "price": round(val / qty, 4), "value": val})
        inv["items"] = items
        if inv["sb"]:
            port, lag = inv["sb"]
            inv["port"] = port
            inv["sb_date"] = inv["day"] + timedelta(days=lag)
            # ICEGATE numbers run up through the year, all ports together
            inv["sb_no"] = str(3148000 + (inv["sb_date"] - date(2024, 8, 1)).days * 1850 + rng.randint(0, 900))
            inv["leo_date"] = inv["sb_date"] + timedelta(days=1)
            inv["sob_date"] = inv["leo_date"] + timedelta(days=rng.randint(2, 6))
            inv["containers"] = [container(rng)]
        lut = "WPAY but the goods went under LUT" in inv["plant"]
        for it in inv["items"]:
            it["fob_inr"] = round(it["value"] * inv["rate"], 2)
            it["igst_value"] = 0.0 if lut else it["fob_inr"]
            it["igst_amount"] = 0.0 if lut else round(it["fob_inr"] * it["gst"] / 100, 2)
        inv["taxable_inr"] = round(sum(it["fob_inr"] for it in inv["items"]), 2)
        inv["igst_inr"] = round(sum(round(it["fob_inr"] * it["gst"] / 100, 2) for it in inv["items"]), 2)
        inv["sb_igst"] = round(sum(it["igst_amount"] for it in inv["items"]), 2)
        inv["dbk"] = round(inv["taxable_inr"] * 0.015, 2) if inv["sb"] else 0.0     # all-industry rate, roughly
        inv["rodtep"] = round(inv["taxable_inr"] * 0.009, 2) if inv["sb"] else 0.0
        inv["sb_inv_no"] = {"KE/25-26/033": "KF/25-26/033",
                            "KE/25-26/047": "CI/2611"}.get(inv["no"], inv["no"])


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _pdf(path, pages, font="Helvetica", size=7.6):
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    for lines in pages:
        y = h - 36
        for line in lines:
            if isinstance(line, list):           # [(x, text), ...]: boxes laid out across the page
                c.setFont(font, size)
                for x, text in line:
                    c.drawString(x, y, text)
                y -= size + 3.4
                continue
            bold = line.startswith("**")
            c.setFont(font + ("-Bold" if bold else ""), size)
            c.drawString(34, y, line.strip("*"))
            y -= size + 3.4
        c.showPage()
    c.save()


def sb_pdf(inv, path):
    port = PORTS[inv["port"]]
    buyer = BUYERS[inv["buyer"]]
    gstin = BRANCH_GSTIN if "Haryana branch" in inv["plant"] else GSTIN
    d = lambda x: x.strftime("%d-%b-%y").upper()
    lut = inv["sb_igst"] == 0
    head = ["Port Code SB No SB Date",
            f"INDIAN CUSTOMS EDI SYSTEM {inv['port']} {inv['sb_no']} {d(inv['sb_date'])}",
            f"IEC/Br {IEC} 0", "CENTRAL BOARD OF INDIRECT TAXES AND CUSTOMS",
            f"GSTIN/TYPE {gstin} GSN", "DEPARTMENT OF REVENUE - MINISTRY OF FINANCE",
            "CB CODE AAHCP4417QCH001", "GOVERNMENT OF INDIA", "TYPE INV ITEM CONT",
            f"Nos 1 {len(inv['items'])} 1",
            f"{port[0]} PKG {sum(it['qty'] for it in inv['items']) // 40 + 1} G.WT KGS "
            f"{sum(it['qty'] for it in inv['items']) * 0.8:.0f} *SB{inv['port']}{inv['sb_no']}"]
    part1 = head + [
        "**PART - I - SHIPPING BILL SUMMARY",
        "1.MODE 2.ASSESS 3.EXMN 4.JOBBING 5.MEIS 6.DBK 7.RODTP 8.LICENCE 9.DFRC 10.RE-EXP 11.LUT",
        f"{port[1]} N N N N Y Y N N N {'Y' if lut else 'N'}",
        f"13.PORT OF LOADING {inv['port']} ({port[2]}) 15.COUNTRY OF FINALDESTINATION {buyer[2]}",
        f"16.STATE OF ORIGIN Rajasthan 17.PORT OF FINAL DESTINATION {buyer[3]} ({buyer[4]})",
        "1.EXPORTER'S NAME & ADDRESS", ENTITY_NAME, *ADDRESS,
        "2.CONSIGNEE NAME & ADDRESS", buyer[0], buyer[1], buyer[2],
        f"4. AD CODE: {AD_CODE} 5. GSTIN / TYPE", "6.FOREX BANK A/C NO. 63XXXXX417",
        f"7.CB NAME {BROKER} 8.DBK BANK A/C NO. 63XXXXX417",
        # Part I's summary boxes, each value under its label as ICEGATE prints them
        [(34, "1.FOB VALUE"), (110, "2.FREIGHT"), (165, "3.INSURANC"), (225, "4.DISCOU"), (280, "5.COM"),
         (330, "6. IGST AMT")],
        [(34, f"{inv['taxable_inr']:.2f}"), (110, "0"), (165, "0"), (225, "0"), (280, "0"),
         (334, f"{inv['sb_igst']:.2f}")],
        [(300, "7.DBK CLAIM"), (380, "8.CESS AMT")],
        [(304, f"{inv['dbk']:.2f}"), (384, "0")],
        f"9.RODTEP AMT 10.ROSCTL AMT {inv['rodtep']:.2f} 0",
        "**PART - II - INVOICE DETAILS",
        "1.S.No 2.INVOICE No. & Dt. 3.P.O.No. & Dt. 4.LoC No. & Dt 5.Contract No.&Dt 6.AD code 7.INVTERM",
        f"1 {inv['sb_inv_no']} {inv['day']:%d/%m/%Y} FOB",
        "8.EXPORTER'S NAME & ADDRESS 9.BUYER'S NAME & ADDRESS", f"{ENTITY_NAME} SAME AS CONSIGNEE",
        "1.INVOICE VALUE 2.FOB VALUE 3.FREIGHT 4.INSURANCE 5.DISCOUNT 6.COMMISON 7.DEDUCT 8.P/C 9.EXCHANGE RATE",
        f"{inv['value']:.2f} {inv['value']:.2f} 0 {inv['currency']} INR {inv['rate']}",
        f"{inv['currency']} {inv['currency']}",
    ]
    part3 = head + ["**PART - III - ITEM DETAILS"]
    for i, it in enumerate(inv["items"], 1):
        part3 += [
            "1INVSN 2ITEMSN 3.HS CD 4.DESCRIPTION 5.QUANTITY 6UQC 7.RATE 8VALUE(F/C) 9.FOB (INR) 10.PMV",
            f"1 {i} {it['hs']} {it['desc']} {it['qty']} {it['uqc']} {it['price']:.2f} {it['value']:.2f} "
            f"{it['fob_inr']:.2f} {it['fob_inr']:.2f}",
            "11.DUTYAMT 12.CESS RT 13CESAMT 14.DBKCLMD 15.IGSTSTAT 16. IGST VALUE 17. IGST AMOUNT 18SCHCOD",
            (f"Y LUT {it['igst_value']:.2f} {it['igst_amount']:.2f} 60" if lut else
             f"Y P {it['igst_value']:.2f} {it['igst_amount']:.2f} 60"),
            "19. SCHEME DESCRIPTION 20. SQC MSR 21. SQC UQC 22. STATE OF ORIGIN 23. DISTRICT OF ORIGIN",
            f"Drawback {it['qty']} {it['uqc']} Rajasthan JAIPUR",
        ]
    part4 = head + [
        "**PART - IV - EXPORT SCHEME DETAILS", "A. DRAWBACK & ROSL CLAIM",
        "H.INVOICE DETAILS", "1.SNO 2.INVOICE NO 3.INVOICE AMOUNT 4.CURRENCY",
        f"1 {inv['sb_inv_no']} {inv['value']:.2f} {inv['currency']}",
        "I.CONTAINER DETAILS", "1.SNO 2.CONTAINER 3.SEAL 4.DATE",
        *[f"{i} {c} RJS{int(inv['sb_no']) % 999983:06d} {d(inv['sb_date'])}"
          for i, c in enumerate(inv["containers"], 1)],
        "**PART - V - DECLARATION", "1.EVENT 2.DATE 3.TIME 4.LEO NO.",
        f"1.Submission {d(inv['sb_date'])} 11:05 5.LEO Date.",
        "2.Assessment 6.BRC Realisation Date", "3.Examination",
        f"4.LEO {d(inv['leo_date'])} 16:40 {int(inv['sb_no']) % 7777777:07d}",
        "Scan QR Code using ICETRAK Mobile App for authentication.",
    ]
    _pdf(path, [part1, part3, part4])


def bl_lines(inv, glued=False):
    buyer = BUYERS[inv["buyer"]]
    port = PORTS[inv["port"]]
    ref = f"INV NO:{inv['sb_inv_no']}-{inv['day']:%d/%m/%Y}" if glued else \
        f"INV NO: {inv['sb_inv_no']} DT. {inv['day']:%d/%m/%Y}"
    sb = f"SB NO:{inv['sb_no']}-{inv['sb_date']:%d/%m/%Y}" if glued else \
        f"SB NO: {inv['sb_no']} DT. {inv['sb_date']:%d/%m/%Y}"
    load = "MUNDRA, INDIA" if inv["port"] != "INNSA1" else "NHAVA SHEVA, INDIA"
    return [
        "**SAFFRON LINE LOGISTICS PVT LTD", "BILL OF LADING FOR COMBINED TRANSPORT SHIPMENT",
        f"H/BL No. SLL{inv['sob_date']:%y%m}{int(inv['sb_no']) % 99991:05d}",
        f"Job No. SLJ{int(inv['sb_no']) % 88883:05d}",
        "Shipper:", ENTITY_NAME, *ADDRESS,
        "Consignee:", buyer[0], buyer[1], buyer[2],
        "Notify Party: SAME AS CONSIGNEE",
        f"Place of Receipt: {port[2].upper()}   Port of Loading",
        load,
        f"Port of Discharge: {buyer[4].upper()}   Place of Delivery: {buyer[4].upper()}",
        f"Vessel: SAFFRON {['PEARL', 'CREST', 'DAWN', 'TIDE'][int(inv['sb_no']) % 4]} {inv['sob_date']:%y}{int(inv['sb_no']) % 90 + 10}W",
        f"Container No. {inv['containers'][0]} / 1X40'HC FCL",
        f"{sum(it['qty'] for it in inv['items'])} PCS COTTON MADE-UPS",
        ref, sb, f"IEC NO: {IEC}", "FREIGHT COLLECT",
        f"SHIPPED ON BOARD {inv['sob_date']:%d.%m.%Y}",
        f"Place and date of issue: JAIPUR {inv['sob_date']:%d/%m/%Y}",
    ]


def bl_pdf(inv, path):
    _pdf(path, [bl_lines(inv)], size=9)


def bl_scan(inv, path):
    """The same bill of lading, printed and scanned."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    dpi = 150
    img = Image.new("L", (int(8.27 * dpi), int(11.69 * dpi)), 248)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 25)
        bold = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 27)
    except OSError:
        font = bold = ImageFont.load_default()
    y = 70
    for line in bl_lines(inv, glued=True):
        draw.text((70, y), line.strip("*"), font=bold if line.startswith("**") else font, fill=24)
        y += 42
    arr = np.asarray(img).astype(np.float32)
    arr += np.random.default_rng(SEED).normal(0, 2.0, arr.shape)
    arr -= np.linspace(0, 8, arr.shape[1])[None, :]
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), mode="L")
    img = img.filter(ImageFilter.GaussianBlur(0.3)).rotate(0.3, resample=Image.BICUBIC, fillcolor=246)
    img.convert("RGB").save(path, resolution=float(dpi))


def fira_pdf(rem, path):
    buyer_name, country, bank = rem["remitter"], rem["country"], rem["bank"]
    lines = [
        f"**{BANK}", "JAIPUR - SITAPURA",
        f"Ref No :{rem['ref']} Date : {rem['date']:%d-%m-%Y}",
        "**FOREIGN INWARD REMITTANCE ADVICE",
        "We certify that we have received the following remittance and proceeds thereof",
        "were paid to :", f"(a)To the beneficiary: {ENTITY_NAME}", *ADDRESS,
        "By Credit to Current/Saving/Cash Credit Account with us: YES",
        f"DATE OF CREDIT: {rem['date']:%d-%b-%Y}",
        f"Name and Place of Residence of Remitter: {buyer_name}", country,
        f"Name and Place of Remitting bank: {bank}",
        f"DD/TT/MT No: {rem['date']:%Y%m%d}TT{rem['n']:05d} Dated: {rem['date'] - timedelta(days=1):%d-%b-%Y}",
        f"Foreign Currency Amount {rem['currency']} {rem['amount']:,.2f} and EEFC Amount 0.00",
        f"Equivalent to: INR {rem['amount'] * rem['rate']:,.2f}",
        "Currency Conversion Details are as below:",
        "From Currency /Amount Rate To Currency / Amount",
        f"{rem['amount']:,.2f} {rem['currency']} {rem['rate']} {rem['amount'] * rem['rate']:,.2f} INR",
        f"Purpose of remittance as per Beneficiary / Remitter: {rem['purpose_text']}",
        f"RBI Purpose Code: {rem['purpose']}",
        "Additional Purpose code description :",
        f"Invoice Number : {rem.get('invoice') or ''}",
        "Payment details :",
        "This is system generated advice and no signature is required.",
    ]
    _pdf(path, [lines], size=9)


def _sheet(path, title, header, rows, top=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for line in top or []:
        ws.append(line)
    ws.append(header)
    for r in rows:
        ws.append(r)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(12, min(40, max(len(str(c.value or "")) for c in col) + 2))
    wb.save(path)


def invoice_xlsx(inv, path):
    buyer = BUYERS[inv["buyer"]]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["TAX INVOICE CUM PACKING LIST"])
    ws.append(["Exporter:-", "", "Invoice No:-", inv["no"], "GSTIN No:-", GSTIN])
    ws.append([ENTITY_NAME, "", "Date:-", inv["day"], "IEC Code:-", IEC])
    ws.append([ADDRESS[0], "", "PO No. & Date:-", f"{inv['buyer']}/PO/{inv['day']:%y%m}", "PAN No.:-", PAN])
    ws.append(["EXPORT ON PAYMENT OF IGST UNDER REBATE"])
    ws.append(["Consignee:-", "", "Buyer (If Other Than Consignee)"])
    ws.append([buyer[0], "", "SAME AS CONSIGNEE"])
    ws.append([buyer[2]])
    ws.append(["S.No.", "HSN Code", "Description of Goods", "Qty", "Unit", f"Rate ({inv['currency']})",
               f"Amount ({inv['currency']})", "Taxable Value (INR)", "IGST %", "IGST (INR)"])
    for i, it in enumerate(inv["items"], 1):
        ws.append([i, it["hs"], it["desc"], it["qty"], it["uqc"], round(it["price"], 2), it["value"],
                   it["fob_inr"], it["gst"], round(it["fob_inr"] * it["gst"] / 100, 2)])
    ws.append(["", "", "Total Invoice Value", "", "", "", inv["value"], inv["taxable_inr"] + inv["igst_inr"]])
    ws.append(["", "", "Total Taxable Value", "", "", "", "", inv["taxable_inr"]])
    ws.append(["", "", "Total IGST", "", "", "", "", "", "", inv["igst_inr"]])
    ws.append([f"Amount In Words: Indian Rupees {inv['taxable_inr'] + inv['igst_inr']:,.2f} only"])
    wb.save(path)


# ---------------------------------------------------------------------------
# The return, the money and the certificates
# ---------------------------------------------------------------------------

def gst_line(inv):
    """This invoice's Table 6A line, with its planted mistake if it has one."""
    typ = "WPAY"
    iamt = inv["igst_inr"]
    idt = inv["day"]
    sbnum, sbpcode, sbdt = inv.get("sb_no"), inv.get("port"), inv.get("sb_date")
    if "two digits swapped" in inv["plant"]:
        s = inv["sb_no"]
        sbnum = s[:-2] + s[-1] + s[-2]
    if "port as INMUN1" in inv["plant"]:
        sbpcode = "INMUN1"
    if "details blank" in inv["plant"]:
        sbnum = sbpcode = sbdt = None
    if "less IGST" in inv["plant"]:
        iamt = round(iamt * 0.9, 2)
    if "a day late" in inv["plant"]:
        idt = idt + timedelta(days=1)
    return {"inum": inv["no"], "idt": idt, "val": round(inv["taxable_inr"] + (iamt if typ == "WPAY" else 0), 2),
            "sbpcode": sbpcode, "sbnum": sbnum, "sbdt": sbdt, "typ": typ,
            "txval": inv["taxable_inr"], "rt": max(it["gst"] for it in inv["items"]), "iamt": iamt}


def write_gstr1(out):
    by_month = {}
    for inv in INVOICES:
        if not inv.get("sb") or inv["day"] < date(2025, 4, 1) or "left out of January" in inv["plant"]:
            continue
        by_month.setdefault((inv["day"].year, inv["day"].month), []).append(inv)
    files = 0
    for (y, m), invs in sorted(by_month.items()):
        if (y, m) >= (2026, 7):
            continue                     # July to September 2026 not filed yet
        lines = []
        for inv in invs:
            line = gst_line(inv)
            lines.append(line)
            if "twice in February" in inv["plant"]:
                lines.append(dict(line))
        if (y, m) >= (2026, 4):
            continue                     # April to June 2026 go in the offline sheet
        blocks = {}
        for l in lines:
            blocks.setdefault(l["typ"], []).append({
                "inum": l["inum"], "idt": l["idt"].strftime("%d-%m-%Y"), "val": l["val"],
                **({"sbpcode": l["sbpcode"], "sbnum": l["sbnum"], "sbdt": l["sbdt"].strftime("%d-%m-%Y")}
                   if l["sbnum"] else {}),
                "itms": [{"txval": l["txval"], "rt": l["rt"], "iamt": l["iamt"], "csamt": 0}]})
        doc = {"gstin": GSTIN, "fp": f"{m:02d}{y}", "gt": 0, "cur_gt": 0,
               "exp": [{"exp_typ": t, "inv": v} for t, v in blocks.items()]}
        (out / f"returns_R1_{GSTIN}_{m:02d}{y}.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        files += 1
    rows = []
    for (y, m), invs in sorted(by_month.items()):
        if (2026, 4) <= (y, m) < (2026, 7):
            for inv in invs:
                l = gst_line(inv)
                by_rate = {}                    # the offline tool takes one row per tax rate
                for it in inv["items"]:
                    by_rate[it["gst"]] = round(by_rate.get(it["gst"], 0) + it["fob_inr"], 2)
                for rt, txval in sorted(by_rate.items()):
                    rows.append([l["typ"], l["inum"], l["idt"], l["val"], l["sbpcode"], l["sbnum"],
                                 l["sbdt"], rt, txval, 0])
    _sheet(out / "GSTR1_offline_exp_Apr-Jun2026.xlsx", "exp",
           ["Export Type", "Invoice Number", "Invoice date", "Invoice Value", "Port Code",
            "Shipping Bill Number", "Shipping Bill Date", "Rate", "Taxable Value", "Cess Amount"], rows,
           top=[["Summary For EXP(6)"], ["No. of Invoices", len(rows)], []])
    return files + 1


def remittances(invs):
    """The money that came in, and the certificates for some of it."""
    k = {i["no"]: i for i in invs}
    rem = [
        # remitter key, currency, amount, date, invoice named on the advice, purpose, in IRM list, eBRC
        ("NORD", 18450.00, date(2025, 7, 2), "KE/25-26/004", "P0102", True, "E1"),
        ("MAPLE", 22800.00, date(2025, 9, 10), None, "P0102", True, None),
        ("BRAM", 6000.00, date(2025, 10, 15), "KE/25-26/014", "P0102", True, None),
        ("SUND", 31215.00, date(2025, 10, 20), "KE/25-26/021", "P0102", True, "E2"),
        ("NORD", 26900.00, date(2026, 2, 12), "KE/25-26/037", "P0102", True, None),
        ("MAPLE", 11200.00, date(2026, 4, 2), "KE/25-26/055", "P0102", True, "E3"),
        ("BRAM", 5000.00, date(2026, 7, 20), "KE/26-27/008", "P0102", True, None),
        ("HOLM", 4000.00, date(2026, 9, 1), None, "P0103", True, None),
        ("GLOBAL", 2450.00, date(2026, 6, 18), None, "P0102", False, None),
        ("MAPLE", 5000.00, date(2026, 5, 5), None, "P0102", "cancelled", None),
    ]
    extra = {"HOLM": ("HOLMFIRTH INTERIORS LTD", "UNITED KINGDOM", "GBP", "PENNINE MUTUAL BANK PLC, LEEDS"),
             "GLOBAL": ("GLOBAL SOURCING SERVICES FZE", "UNITED ARAB EMIRATES", "USD",
                        "GULF CRESCENT BANK PJSC, DUBAI")}
    out = []
    for n, (who, amount, day, invoice, purpose, irm, ebrc) in enumerate(rem, 1):
        if who in BUYERS:
            b = BUYERS[who]
            name, country, cur, bank = b[0], b[2], b[5], b[6]
        else:
            name, country, cur, bank = extra[who]
        out.append({"n": n, "remitter": name, "country": country, "currency": cur, "bank": bank,
                    "amount": amount, "date": day, "invoice": invoice, "purpose": purpose,
                    "purpose_text": "Advance receipts against export contracts" if purpose == "P0103"
                    else "Realisation of export bills", "rate": rate_on(cur, day),
                    "ref": f"6390XIR{day:%y%m%d}{n:04d}", "irm": irm,
                    "irm_no": f"{IFSC}{day:%y%m%d}{n:06d}", "ebrc": ebrc,
                    "inv": k.get(invoice)})
    return out


def extras(out):
    """Two documents that are not in the sample, for the guide's pictures of
    checking what was read and placing a payment: a bill of lading for
    KE/25-26/058 with its invoice number misread as KE/25-26/085 and no
    shipping bill number, so only a correction files it; and a new advice from
    Casa Oliva paying EUR 30,000 that names no invoice."""
    if "sb_igst" not in INVOICES[0]:
        build(random.Random(SEED))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    inv = next(i for i in INVOICES if i["no"] == "KE/25-26/058")
    buyer, sob = BUYERS[inv["buyer"]], inv.get("sob_date") or inv["sb_date"]
    bl = out / "SLL-house-BL-scan.pdf"
    _pdf(bl, [["**SAFFRON LINE LOGISTICS PVT LTD", "BILL OF LADING FOR COMBINED TRANSPORT SHIPMENT",
               f"H/BL No. SLL{sob:%y%m}77001", "Shipper:", ENTITY_NAME, *ADDRESS,
               "Consignee:", buyer[0], buyer[1], buyer[2], "Notify Party: SAME AS CONSIGNEE",
               "Port of Loading", "MUNDRA, INDIA", f"Vessel: SAFFRON TIDE {sob:%y}77W",
               f"{sum(it['qty'] for it in inv['items'])} PCS COTTON MADE-UPS",
               f"INV NO: KE/25-26/085 DT. {inv['day']:%d/%m/%Y}", f"IEC NO: {IEC}", "FREIGHT COLLECT",
               f"SHIPPED ON BOARD {sob:%d.%m.%Y}"]], size=9)
    b, day = BUYERS["OLIVA"], date(2026, 9, 24)
    advice = out / f"6390XIR{day:%y%m%d}0077-FIRA.pdf"
    fira_pdf({"n": 77, "remitter": b[0], "country": b[2], "currency": b[5], "bank": b[6], "amount": 30000.0,
              "date": day, "invoice": None, "purpose": "P0102", "purpose_text": "Realisation of export bills",
              "rate": rate_on("EUR", day), "ref": f"6390XIR{day:%y%m%d}0077", "irm": True,
              "irm_no": f"{IFSC}{day:%y%m%d}000077", "ebrc": None, "inv": None}, advice)
    return bl, advice


def status_download(folder):
    """An ICEGATE IGST refund status sheet as a browser saves it to Downloads,
    for the guide's picture of the app offering to add it: two of the sample's
    shipping bills, one validated and one held on SB005."""
    if "sb_igst" not in INVOICES[0]:
        build(random.Random(SEED))
    k = {i["no"]: i for i in INVOICES}
    rows = [[k[n]["sb_no"], k[n]["sb_date"], k[n]["port"], code, words, k[n]["sb_igst"]]
            for n, code, words in (("KE/26-27/021", "SB000", "Validated - sent to scroll"),
                                   ("KE/26-27/017", "SB005", "Invoice number mismatch"))]
    path = Path(folder) / "IGST_Refund_Status_27092026.xlsx"
    _sheet(path, "IGST Refund Status", ["Shipping Bill No", "Shipping Bill Date", "Port Code", "Response Code",
                                        "Status", "IGST Amount"], rows)
    return path


def write_money(out, rems):
    fira = out / "Bank advices"
    fira.mkdir(parents=True, exist_ok=True)
    for r in rems:
        if r["irm"] != "cancelled":
            fira_pdf(r, fira / f"{r['ref']}-FIRA.pdf")
    dgft = out / "DGFT"
    dgft.mkdir(parents=True, exist_ok=True)
    irm_rows = []
    for r in rems:
        if not r["irm"]:
            continue
        status = "Cancelled" if r["irm"] == "cancelled" else "Fresh"
        blank_ifsc = r["invoice"] == "KE/25-26/037"     # one row DGFT's own data leaves blank
        irm_rows.append([r["irm_no"], r["date"], "" if blank_ifsc else IFSC, AD_CODE, r["date"], r["currency"],
                         r["amount"], r["amount"], 0, status, r["remitter"], r["country"], r["purpose"], r["ref"]])
    _sheet(dgft / "IRM_repository_download.xlsx", "IRM",
           ["IRM Number", "IRM Issue Date", "IFSC Code", "AD Code", "Remittance Date", "Currency",
            "Remittance Amount", "Available Amount", "Utilised Amount", "IRM Status", "Remitter Name",
            "Remitter Country", "Purpose Code", "Bank Reference No"], irm_rows)
    ebrc_rows = []
    for r in rems:
        if not r["ebrc"]:
            continue
        inv = r["inv"]
        gst_no = "KE/25-26/0055" if inv["no"] == "KE/25-26/055" else inv["no"]
        ebrc_rows.append([f"RJ26EB{r['n']:06d}", r["date"] + timedelta(days=9), "Active", r["irm_no"],
                          inv["sb_no"], inv["sb_date"], inv["port"], inv["no"], r["currency"], r["amount"],
                          r["amount"], r["date"], gst_no, inv["day"], "Y"])
    ebrc_rows.append(["RJ26EB000099", date(2026, 3, 30), "Active", f"{IFSC}260318000099", "7999999",
                      date(2025, 3, 12), "INNSA1", "KE/24-25/070", "USD", 6400.0, 6400.0, date(2026, 3, 18),
                      "KE/24-25/070", date(2025, 3, 9), "Y"])
    _sheet(dgft / "eBRC_bulk_download.xlsx", "eBRC",
           ["eBRC Number", "eBRC Date", "Status", "IRM Number", "Shipping Bill No", "Shipping Bill Date",
            "Port Code", "Bill No", "Currency", "Realised Value", "Net Realised Value", "Realisation Date",
            "GST Invoice No", "GST Invoice Date", "GST Benefit Availed"], ebrc_rows)


# What came back against each shipping bill, as the exporter's own sheet keeps
# it: (IGST refund - True, False, None for none paid, or the amount short),
# drawback received, and any code or note typed beside it.
RECEIVED = {
    "KE/24-25/088": (False, True, ""),            # refund never came; nothing says why
    "KE/25-26/014": (False, True, ""),            # ICEGATE shows it paid; never entered
    "KE/25-26/021": (True, False, ""),            # drawback held on a query
    "KE/25-26/026": (False, True, ""), "KE/25-26/030": (False, True, "SB001"),
    "KE/25-26/033": (False, True, "SB005"), "KE/25-26/037": (0.1, True, ""),
    "KE/25-26/041": (None, True, ""), "KE/25-26/047": (False, True, ""), "KE/25-26/052": (False, True, ""),
    "KE/25-26/055": (2000.0, False, ""),          # short by Rs 2,000; drawback not in
    "KE/25-26/058": (False, True, ""), "KE/25-26/062": (False, True, ""), "KE/25-26/066": (False, True, ""),
    "KE/26-27/008": (False, True, "SB002 EGM pending"),
    "KE/26-27/017": (False, True, ""),            # July's GSTR-1 is not filed, so no refund yet
    "KE/26-27/021": (False, False, ""),           # shipped 18 days ago: nothing due yet
}
# Two older shipping bills the register holds and nothing else does.
REGISTER_ONLY = [
    dict(no="KE/24-25/061", day=date(2025, 1, 17), sb_no="3290417", sb_date=date(2025, 1, 20), port="INNSA1",
         value=11800, igst=41250.0, dbk=5100.0, igst_back=True, container="TGHU-418822-6", note=""),
    dict(no="KE/24-25/079", day=date(2025, 3, 7), sb_no="3342870", sb_date=date(2025, 3, 10), port="INJAI6",
         value=10400, igst=38600.0, dbk=4700.0, igst_back=False, container="MSKU-730215-4",
         note="SB006 gate way egm pending"),
]
OWN_HEAD = ["INV.NO.", "INV.DATE", "INV. AMOUNT", "CONTAINER NO.", "ICD", "S.B.NO.", "S.B.DATE", "GST AMOUNT",
            "GST AMO.RECD", "DIFF", "DATE OF RECD", "D.BACK AMOUNT", "DATE OF RECD", "PAYMENT", "BANK",
            "DEL. TO BUYER"]


def own_register(out):
    """The exporter's own sheet, in the layout exporters keep: a tab per year,
    dates typed as they come, and ICEGATE's drawback list pasted on a tab."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    tabs = {}

    def tab(fy):
        if fy not in tabs:
            ws = wb.create_sheet(fy)
            head = list(OWN_HEAD)
            if fy == "2025-26":
                head[13] = "ERROR CODE"          # one year kept the code in place of the payment
            ws.append(head)
            tabs[fy] = ws
        return tabs[fy]

    typed = lambda d, n: d.strftime("%d.%m.%y") if n % 2 else d        # half typed, half real dates
    lines = [(i, inv) for i, inv in enumerate(INVOICES) if inv.get("sb")]
    for n, inv in lines:
        igst_back, dbk_back, note = RECEIVED.get(inv["no"], (True, True, ""))
        refund_on = inv["sb_date"] + timedelta(days=24 + n % 9)
        paid = inv["sb_igst"] if igst_back is True else (round(inv["sb_igst"] * (1 - igst_back), 2)
                                                         if isinstance(igst_back, float) and igst_back < 1
                                                         else round(inv["sb_igst"] - igst_back, 2)
                                                         if isinstance(igst_back, float) else None)
        fy = f"{inv['sb_date'].year if inv['sb_date'].month >= 4 else inv['sb_date'].year - 1}-" \
             f"{str((inv['sb_date'].year if inv['sb_date'].month >= 4 else inv['sb_date'].year - 1) + 1)[2:]}"
        code = note if fy == "2025-26" and note.startswith("SB") else ""
        tab(fy).append([
            inv["no"], typed(inv["day"], n), inv["value"], f"{inv['containers'][0][:4]}-{inv['containers'][0][4:10]}-"
            f"{inv['containers'][0][10]}", PORTS[inv["port"]][2].upper(), int(inv["sb_no"]), typed(inv["sb_date"], n + 1),
            inv["sb_igst"] or 0, paid, round(inv["sb_igst"] - paid, 2) if paid is not None else None,
            typed(refund_on, n) if paid is not None else None, inv["dbk"],
            typed(inv["sb_date"] + timedelta(days=12 + n % 6), n + 1) if dbk_back else None,
            code, typed(inv["sob_date"] + timedelta(days=3), n) if inv["bl"] else None,
            note if not code else ""])
    for r in REGISTER_ONLY:
        tab("2024-25").append([
            r["no"], r["day"].strftime("%d.%m.%y"), r["value"], r["container"], PORTS[r["port"]][2].upper(),
            int(r["sb_no"]), r["sb_date"].strftime("%d.%m.%y"), r["igst"], r["igst"] if r["igst_back"] else None,
            0 if r["igst_back"] else None, (r["sb_date"] + timedelta(days=29)).strftime("%d.%m.%y")
            if r["igst_back"] else None, r["dbk"], (r["sb_date"] + timedelta(days=14)).strftime("%d.%m.%y"),
            None, None, r["note"]])
    for fy in sorted(tabs):                       # tabs in year order, as a person keeps them
        wb.move_sheet(fy, offset=sorted(tabs).index(fy) - wb.sheetnames.index(fy))
    # ICEGATE's list of drawback waiting on proof of payment, pasted from the
    # web page - the headings keep its non-breaking spaces.
    ws = wb.create_sheet("Sheet1")
    ws.append([h + "\xa0" for h in ("Serial No.", "STATUS", "BILL NO.", "SB No.", "SB Date", "LEO Date",
                                    "AD Code", "FOB", "DBK", "Expected Date of Submission")])
    k = {i["no"]: i for i in INVOICES}
    for n, no in enumerate(("KE/24-25/088", "KE/25-26/037"), 1):
        inv = k[no]
        ws.append([n, "", "", int(inv["sb_no"]), inv["sb_date"], inv["leo_date"], int(AD_CODE), inv["taxable_inr"],
                   inv["dbk"], inv["sb_date"] + timedelta(days=365 if no.startswith("KE/24") else 550)])
    (out / "Your register").mkdir(parents=True, exist_ok=True)
    wb.save(out / "Your register" / "S. BILL DETAIL (KESARI).xlsx")


def icegate_status(out):
    """ICEGATE's IGST refund and drawback status, as downloads."""
    k = {i["no"]: i for i in INVOICES}
    codes = {"KE/25-26/026": "SB003", "KE/25-26/030": "SB001", "KE/25-26/058": "SB001", "KE/25-26/062": "SB001",
             "KE/25-26/033": "SB005", "KE/25-26/047": "SB005", "KE/25-26/066": "SB005"}
    words = {"SB000": "Successfully validated", "SB001": "Invalid SB details", "SB003": "GSTIN mismatch",
             "SB005": "Invalid invoice number"}
    paid = ["KE/25-26/004", "KE/25-26/009", "KE/25-26/014", "KE/25-26/021", "KE/25-26/037", "KE/25-26/055",
            "KE/26-27/003", "KE/26-27/012"]
    rows = []
    for no in paid + list(codes):
        inv = k[no]
        code = codes.get(no, "SB000")
        scrolled = code == "SB000"
        rows.append([int(inv["sb_no"]), inv["sb_date"], inv["port"], inv["sb_inv_no"], code, words[code],
                     f"{inv['port']}/{inv['sb_date']:%y}/{int(inv['sb_no']) % 99991:05d}" if scrolled else "",
                     inv["sb_date"] + timedelta(days=21) if scrolled else None,
                     round(inv["sb_igst"] * (0.9 if no == "KE/25-26/037" else 1), 2)])
    _sheet(out / "ICEGATE" / "IGST_refund_status.xlsx", "IGST Refund Status",
           ["SB No", "SB Date", "Port Code", "Invoice No", "Response Code", "Response Description", "Scroll No",
            "Scroll Date", "IGST Amount"], rows)
    dbk_rows = []
    for no in ("KE/25-26/004", "KE/25-26/009", "KE/25-26/021", "KE/26-27/003", "KE/26-27/012"):
        inv = k[no]
        held = no == "KE/25-26/021"
        dbk_rows.append([int(inv["sb_no"]), inv["sb_date"], inv["port"], inv["dbk"],
                         "" if held else f"D{int(inv['sb_no']) % 99991:05d}",
                         None if held else inv["sb_date"] + timedelta(days=12),
                         "Query raised: drawback rate claimed differs" if held else "Scroll generated"])
    _sheet(out / "ICEGATE" / "Drawback_status.xlsx", "Drawback Status",
           ["SB No", "SB Date", "Port Code", "DBK Amount", "Scroll No", "Scroll Date", "Status"], dbk_rows)


def edpms_statement(out):
    """The AD bank's EDPMS statement of bills it still holds as unpaid."""
    k = {i["no"]: i for i in INVOICES}
    unpaid = ["KE/24-25/088", "KE/25-26/009", "KE/25-26/014", "KE/25-26/026", "KE/25-26/030", "KE/25-26/033",
              "KE/25-26/037", "KE/25-26/041", "KE/25-26/052", "KE/25-26/058", "KE/25-26/062", "KE/25-26/066",
              "KE/26-27/003", "KE/26-27/008", "KE/26-27/012", "KE/26-27/017", "KE/26-27/021"]
    # KE/25-26/047 is left out: the bank cannot see it.
    rows = []
    for no in unpaid:
        inv = k[no]
        months = 9 if inv["sb_date"] < date(2025, 11, 14) or inv["sb_date"] >= date(2026, 6, 5) else 15
        y, m = divmod(inv["sb_date"].month - 1 + months, 12)
        due = inv["sb_date"].replace(year=inv["sb_date"].year + y, month=m + 1,
                                     day=min(inv["sb_date"].day, 28))
        realised = {"KE/25-26/014": 6000.0, "KE/26-27/008": 5000.0, "KE/25-26/030": 12300.0}.get(no, 0.0)
        if no == "KE/25-26/026":
            due = due + timedelta(days=183)        # the bank granted six months more
        status = "Realised" if no == "KE/25-26/030" else "Extension granted" if no == "KE/25-26/026" else "Outstanding"
        rows.append([int(inv["sb_no"]), inv["sb_date"], inv["port"], inv["sb_inv_no"], inv["currency"],
                     inv["value"], realised, round(inv["value"] - realised, 2), due, status])
    _sheet(out / "Bank advices" / "EDPMS_outstanding_statement.xlsx", "EDPMS",
           ["Shipping Bill No", "Shipping Bill Date", "Port Code", "Invoice No", "Currency", "Invoice Value",
            "Realised Amount", "Outstanding Amount", "Due Date", "Status"], rows,
           top=[[f"EDPMS outstanding statement - {BANK}"], [f"AD code {AD_CODE}  as on 25-Sep-2026"], []])


def generate(out=OUT):
    rng = random.Random(SEED)
    build(rng)
    for sub in ("Shipping bills", "Bills of lading", "GSTR-1", "Invoices", "ICEGATE"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for old in out.rglob("*"):
        if old.is_file():
            old.unlink()
    counts = {"sb": 0, "bl": 0}
    for inv in INVOICES:
        if inv.get("sb"):
            name = f"{inv['sb_no']}{inv['sb_date']:%d%m%Y}{inv['port']}SB2{inv['sb_date']:%d%m%Y}1105.pdf"
            sb_pdf(inv, out / "Shipping bills" / name)
            counts["sb"] += 1
            if inv["bl"] == "scan":
                bl_scan(inv, out / "Bills of lading" / f"BL scan - {inv['no'].replace('/', '-')}.pdf")
                counts["bl"] += 1
            elif inv["bl"]:
                bl_pdf(inv, out / "Bills of lading" / f"BL - {inv['no'].replace('/', '-')}.pdf")
                counts["bl"] += 1
        if inv.get("invoice_file"):
            invoice_xlsx(inv, out / "Invoices" / f"{inv['no'].replace('/', '-')}.xlsx")
    stray = dict(INVOICES[1])            # a bill of lading for an export nobody has on file
    stray.update(no="KE/25-26/999", sb_inv_no="KE/25-26/999", sb_no="8888888", day=date(2026, 3, 1),
                 sb_date=date(2026, 3, 3), sob_date=date(2026, 3, 7), containers=["TGHU8765432"])
    bl_pdf(stray, out / "Bills of lading" / "BL - unknown shipment.pdf")
    gstr1 = write_gstr1(out / "GSTR-1")
    rems = remittances(INVOICES)
    write_money(out, rems)
    status = [[inv["port"], IEC, inv["sb_no"], inv["sb_date"], "Sent to EDPMS", 900000 + i, inv["sb_date"],
               f"Job is Submitted Shipping Bill No/Date is {inv['sb_no']}/{inv['sb_date']:%d-%b-%y}".upper(), ""]
              for i, inv in enumerate(INVOICES) if inv.get("sb")]
    _sheet(out / "ICEGATE" / "View_Access_Data.xlsx", "View Access Data",
           ["Location Code", "IEC", "SB No", "SB date", "Status", "Job No", "Job Date", "Error",
            "Control Number"], status)
    own_register(out)
    icegate_status(out)
    edpms_statement(out)
    print(f"Wrote {counts['sb']} shipping bills, {counts['bl'] + 1} bills of lading, {gstr1} GSTR-1 files, "
          f"{sum(1 for r in rems if r['irm'] != 'cancelled')} bank advices, the DGFT IRM and eBRC downloads, "
          f"ICEGATE's status, refund and drawback sheets, the bank's EDPMS statement and the exporter's own "
          f"register to {out}")


# ---------------------------------------------------------------------------
# Read it all back and check every planted problem is found
# ---------------------------------------------------------------------------

EXPECTED = {
    ("overdue", "KE/24-25/088"), ("pay_confirm", "KE/25-26/009"), ("overdue", "KE/25-26/009"),
    ("overdue", "KE/25-26/014"), ("ebrc_due", "KE/25-26/014"), ("sb003", "KE/25-26/026"),
    ("sb001", "KE/25-26/030"), ("sb005", "KE/25-26/033"), ("igst_differs", "KE/25-26/037"),
    ("ebrc_due", "KE/25-26/037"), ("type_differs", "KE/25-26/041"), ("sb005", "KE/25-26/047"),
    ("not_in_gst", "KE/25-26/052"), ("gst_twice", "KE/25-26/055"), ("ebrc_differs", "KE/25-26/055"),
    ("sb001", "KE/25-26/058"), ("no_bl", "KE/25-26/058"), ("sb001", "KE/25-26/062"),
    ("sb005", "KE/25-26/066"), ("ebrc_due", "KE/26-27/008"), ("no_bl", "KE/26-27/017"),
    ("no_sb", "KE/26-27/025"), ("advance", None), ("pay_unplaced", None), ("bl_unplaced", None),
    ("ebrc_unplaced", None),
    # refunds and drawback, from the register and ICEGATE
    ("refund_unentered", "KE/25-26/014"), ("igst_pending", "KE/26-27/008"), ("igst_pending", "KE/24-25/088"),
    ("igst_pending", "KE/24-25/079"), ("igst_pending", "KE/26-27/017"), ("dbk_pending", "KE/25-26/021"),
    ("dbk_pending", "KE/25-26/055"), ("igst_short", "KE/25-26/055"), ("dbk_brc", "KE/24-25/088"),
    ("dbk_brc", "KE/25-26/037"),
    # the bank's EDPMS statement
    ("edpms_open", "KE/25-26/037"), ("edpms_closed", "KE/25-26/030"), ("edpms_missing", "KE/25-26/047"),
}


def verify(out=OUT, quiet=False):
    from app.backend import exim, exim_read
    if "sb_igst" not in INVOICES[0]:
        build(random.Random(SEED))          # the figures the files were written from, same seed
    docs = []
    for i, path in enumerate(sorted(p for p in out.rglob("*") if p.is_file())):
        raw = path.read_bytes()
        if path.suffix in (".json",) and real_client_words(raw.decode("utf-8", "ignore")):
            raise AssertionError(f"a real name is in {path}")
        doc = exim_read.read(raw, path.name)
        doc.update(id=f"d{i}", name=path.name)
        assert doc["type"] != "unknown", f"{path.name} was not recognised: {doc.get('notes')}"
        docs.append(doc)
    got = exim.build(docs, today=AS_OF)
    found = {(r["status"], r["inv_no"]) for r in got["all_records"]}
    missing = EXPECTED - found
    assert not missing, f"planted problems not found: {sorted(missing, key=str)}"
    rows = {r["inv_no"]: r for r in got["invoices"]}
    clean = [r["status"] for r in got["all_records"]
             if r["inv_no"] in ("KE/25-26/004", "KE/26-27/003", "KE/26-27/012", "KE/24-25/061")]
    assert not clean, f"a clean invoice was flagged: {clean}"
    k = {i["no"]: i for i in INVOICES}
    for r in got["invoices"]:                       # Part I's boxes, read by position
        if r["inv_no"] in k and r["sb"] and not r["sb"]["from_register"]:
            assert abs((r["sb"]["igst_amount"] or 0) - k[r["inv_no"]]["sb_igst"]) < 0.01, r["inv_no"]
            assert abs((r["sb"]["dbk_amount"] or 0) - k[r["inv_no"]]["dbk"]) < 0.01, r["inv_no"]
    assert rows["KE/25-26/021"]["due"]["state"] == "paid" and rows["KE/25-26/021"]["due"]["short_by_charges"] == 35
    assert rows["KE/24-25/088"]["due"]["state"] == "overdue_year"
    assert rows["KE/25-26/033"]["invoice_doc"], "the invoice file did not find its SB005 invoice"
    assert rows["KE/25-26/062"]["bls"], "the scanned bill of lading was not placed"
    assert not any(r["status"] == "no_sb" and r["inv_no"] == "KE/26-27/027" for r in got["all_records"])
    assert all(m["how"] != "you confirmed" for r in got["invoices"] for m in r["receipts"])
    assert len(got["bulk_rows"]) == 3 and any(b["problems"] for b in got["bulk_rows"])
    by = {(r["status"], r["inv_no"]): r for r in got["all_records"]}
    assert "SB006" in by[("igst_pending", "KE/24-25/079")]["why"]["title"], "the register's code was not used"
    assert "SB002" in by[("igst_pending", "KE/26-27/008")]["why"]["title"]
    assert any("ICEGATE shows SB003" in n for n in by[("sb003", "KE/25-26/026")]["why"]["notes"])
    assert by[("dbk_brc", "KE/24-25/088")]["tone"] == "serious" and by[("dbk_brc", "KE/25-26/037")]["tone"] == "look"
    assert rows["KE/25-26/026"]["due"]["date"] > "2026-12-01", "the bank's extension was not used"
    from app.backend import exim_drafts
    drafts = exim_drafts.summary(got)
    assert drafts["sb005"]["count"] == 2 and drafts["sb005"]["check"] == 1, drafts["sb005"]
    assert drafts["table9a"]["add"] == 1 and drafts["sb003"]["count"] == 1, drafts
    if not quiet:
        print(f"verify: {len(docs)} documents read, {len(got['invoices'])} invoices, "
              f"{len(got['all_records'])} open entries, all {len(EXPECTED)} planted problems found")
    return got


if __name__ == "__main__":
    generate()
    verify()
