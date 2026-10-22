"""
Generate the sample data the app ships with.

    python tools/generate_sample_data.py

Everything under SampleData/ comes out of this file. Nothing in it is real: the
entity, its suppliers, their GSTINs, the bank account and every figure are
invented here from a fixed seed, so the same corpus is produced on every run
and no real business's ledger is ever bundled into a build.

The method is to write one set of *true* transactions and then derive the two
sides from it, applying a named divergence to some of them - a value keyed
wrong, an invoice renumbered, a purchase never filed. That is how real data
goes wrong, and it means the number of defects the app ought to find is known
in advance rather than discovered afterwards. `verify()` at the bottom checks
the corpus actually contains what was planted.

GSTINs carry a correct check digit, because a demo that ships invalid ones
teaches the viewer to ignore the one field that can be validated for free.
"""

import argparse
import datetime as dt
import hashlib
import math
import random
import re
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

import synth_bank

# Your own business's names and tax ids, which must never appear in the invented
# corpus - kept as fingerprints (length, first 20 hex of SHA-256), so the words
# themselves are written down nowhere in the project. Empty here: add your own as
# (len(word), hashlib.sha256(word.upper().encode()).hexdigest()[:20]). Short words
# are easy to guess from their hash, so keep these in a private copy only.
REAL_CLIENT: set[tuple[int, str]] = set()


def real_client_words(text):
    """Fingerprints of any real-client name or tax id found in `text`, anywhere in a word."""
    found = set()
    for run in re.findall(r"[A-Z0-9]+", str(text).upper()):
        for size in {n for n, _ in REAL_CLIENT}:
            for i in range(len(run) - size + 1):
                h = hashlib.sha256(run[i:i + size].encode()).hexdigest()[:20]
                if (size, h) in REAL_CLIENT:
                    found.add(h)
    return sorted(found)


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "SampleData"

SEED = 20260401
FY_START = dt.date(2025, 4, 1)
FY_END = dt.date(2026, 3, 31)

# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------

GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STATES = {
    "08": "Rajasthan", "27": "Maharashtra", "07": "Delhi", "24": "Gujarat",
    "29": "Karnataka", "33": "Tamil Nadu", "09": "Uttar Pradesh",
    "19": "West Bengal", "06": "Haryana", "23": "Madhya Pradesh",
}
HOME_STATE = "08"


def gstin_check_digit(first14):
    """The official mod-36 checksum, so every GSTIN here validates."""
    total = 0
    for i, ch in enumerate(first14):
        value = GSTIN_CHARS.index(ch)
        product = value * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return GSTIN_CHARS[(36 - total % 36) % 36]


def make_gstin(state, pan, entity="1"):
    stem = f"{state}{pan}{entity}Z"
    return stem + gstin_check_digit(stem)


def make_pan(rng, surname_letter, kind="C"):
    """PAN shape: AAAAA9999A. The fourth letter encodes the holder type."""
    head = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXY") for _ in range(3))
    return (head + kind + surname_letter
            + "".join(str(rng.randrange(10)) for _ in range(4))
            + rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ"))


# ---------------------------------------------------------------------------
# The fictional world
# ---------------------------------------------------------------------------

ENTITY_NAME = "KESARI TEXTILE MILLS PRIVATE LIMITED"
ENTITY_SHORT = "KESARI TEXTILE MILLS"
ENTITY_ADDRESS = "Plot 41, Sitapura Industrial Area, Jaipur 302022, Rajasthan"
ENTITY_UDYAM = "UDYAM-RJ-17-0099421"

# Trade names are assembled from these parts, so every supplier is obviously a
# construction rather than a company somebody might recognise.
NAME_HEADS = [
    "SURYODAY", "NEELKANTH", "TRIVENI", "ASHIRWAD", "MARUDHAR", "CHITRAKOOT",
    "SANGAM", "PADMAVATI", "RUPALI", "GIRIRAJ", "AMRAPALI", "BHAIRAV",
    "CHANDANI", "DEVANSHI", "EKLAVYA", "GULMOHAR", "HARIDWAR", "INDRAPRASTHA",
    "JALORE", "KAILASH", "LOTUS VALLEY", "MRIGNAYANI", "NAVRANG", "OMKAR",
    "PUSHKAR", "RANTHAMBORE", "SHEKHAWATI", "TARAGARH", "UDAIGARH", "VINAYAK",
    "YASHODA", "ZORAWAR", "AMBER FORT", "BANSWARA", "CHITTOR", "DUNGARPUR",
]
NAME_TAILS = [
    "TEXTILE TRADERS", "POLYMERS", "PACKAGING WORKS", "DYEING MILLS",
    "ENGINEERING WORKS", "ENTERPRISES", "TRADING COMPANY", "INDUSTRIES",
    "FABRICATORS", "HARDWARE STORES", "PRINTERS", "CHEMICALS",
    "ELECTRICALS", "TRANSPORT SERVICE", "STATIONERS", "AGENCIES",
    "SYNTHETICS", "HANDLOOMS", "CARTONS", "THREAD MILLS",
]

HSN = ["5208", "5407", "5509", "5911", "3923", "4819", "7318", "8448",
       "3208", "8443", "9603", "4802", "7326", "3506"]

RATES = [5.0, 12.0, 18.0, 18.0, 18.0, 28.0, 12.0, 5.0]

BLOCK_REASONS = [
    "POS and supplier state are same but recipient state is different",
    "Return filed after the annual cut-off",
]

INV_STYLES = [
    "{fy}/{n}", "{n}", "INV-{fy}-{n:04d}", "{pfx}/{n}/{fy}",
    "{pfx}{n}", "{fy}-{n:04d}", "{pfx}/{fy}/{n:04d}",
]


def month_range(start=FY_START, end=FY_END):
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    return months


def fy_tag(date):
    year = date.year if date.month >= 4 else date.year - 1
    return f"{year % 100}-{(year + 1) % 100}"


def last_day(date):
    nxt = (date.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def r2(x):
    return round(x + 1e-9, 2)


class Supplier:
    def __init__(self, rng, index):
        head = NAME_HEADS[index % len(NAME_HEADS)]
        tail = NAME_TAILS[(index * 7 + 3) % len(NAME_TAILS)]
        self.name = f"{head} {tail}"
        # a fifth of suppliers sit outside the home state, so the place-of-
        # supply and IGST paths are exercised
        self.state = HOME_STATE if index % 5 else rng.choice(
            [s for s in STATES if s != HOME_STATE])
        kind = "C" if index % 3 else "P"
        self.pan = make_pan(rng, head[0], kind)
        self.gstin = make_gstin(self.state, self.pan)
        self.style = INV_STYLES[index % len(INV_STYLES)]
        self.prefix = "".join(w[0] for w in head.split())[:3] + tail[0]
        self.counter = rng.randrange(120, 900)
        self.size = rng.choice(["small", "small", "mid", "mid", "large"])
        # how late they file, in days after the month end
        self.filing_lag = rng.choice([9, 10, 11, 11, 12, 13, 18, 26, 40])

    def next_invoice(self, date):
        self.counter += random.randrange(1, 7)
        return self.style.format(fy=fy_tag(date), n=self.counter,
                                 pfx=self.prefix)

    def amount(self, rng):
        if self.size == "small":
            return rng.randrange(800, 60_000)
        if self.size == "mid":
            return rng.randrange(20_000, 400_000)
        return rng.randrange(150_000, 1_400_000)


class Doc:
    """One true transaction, plus whatever was done to it."""

    def __init__(self, supplier, date, taxable, rate, inv_no):
        self.supplier = supplier
        self.date = date
        self.rate = rate
        self.taxable = r2(taxable)
        self.inv_no = inv_no
        self.interstate = supplier.state != HOME_STATE
        tax = r2(self.taxable * rate / 100)
        if self.interstate:
            self.igst, self.cgst, self.sgst = tax, 0.0, 0.0
        else:
            self.igst, self.cgst, self.sgst = 0.0, r2(tax / 2), r2(tax / 2)
        self.cess = 0.0
        self.value = r2(self.taxable + self.igst + self.cgst + self.sgst)
        self.divergence = None
        self.rcm = "N"
        self.itc = "Yes"
        self.reason = None
        self.section = "B2B"
        self.note = None

    @property
    def tax(self):
        return r2(self.igst + self.cgst + self.sgst)

    def recompute_value(self):
        self.value = r2(self.taxable + self.igst + self.cgst + self.sgst)


# ---------------------------------------------------------------------------
# Building the purchase corpus
# ---------------------------------------------------------------------------

PLAN = {
    "value": 9,          # booked at a different figure
    "invno": 22,         # renumbered in the books
    "gst_only": 34,      # filed by the supplier, never booked
    "tally_only": 24,    # booked, never filed
    "split": 6,          # one invoice, several vouchers
    "probable": 8,       # close enough to be worth a look
    "amended": 11,       # superseded by a B2BA row
    "credit_note": 26,   # CDNR
    "duplicate": 3,      # keyed twice in the books
}


def build_purchases(rng):
    suppliers = [Supplier(rng, i) for i in range(52)]
    docs = []

    for month in month_range():
        # a working year is not flat: October and March are busier
        base = 78
        if month.month in (10, 11):
            base = 104
        elif month.month == 3:
            base = 96
        elif month.month in (5, 6):
            base = 66
        for _ in range(rng.randrange(base - 12, base + 12)):
            supplier = rng.choice(suppliers)
            day = rng.randrange(1, (last_day(month) - month).days + 2)
            date = month + dt.timedelta(days=day - 1)
            rate = rng.choice(RATES)
            docs.append(Doc(supplier, date, supplier.amount(rng), rate,
                            supplier.next_invoice(date)))

    rng.shuffle(docs)
    cursor = 0

    def take(n):
        nonlocal cursor
        chunk = docs[cursor:cursor + n]
        cursor += n
        return chunk

    for kind, count in PLAN.items():
        for doc in take(count):
            doc.divergence = kind

    # a handful of things that are true of the document rather than a
    # difference between the two sides
    for doc in take(14):
        doc.rcm = "Y"
    for doc in take(9):
        doc.itc = "No"
        doc.reason = rng.choice(BLOCK_REASONS)

    # two documents with a tax split that cannot happen, which is a keying
    # error at the supplier's end and worth flagging loudly
    bad_split = take(2)
    bad_split[0].igst = r2(bad_split[0].taxable * 0.09)
    bad_split[0].cgst = r2(bad_split[0].taxable * 0.045)
    bad_split[0].sgst = r2(bad_split[0].taxable * 0.045)
    bad_split[0].recompute_value()
    bad_split[0].note = "IGST together with CGST/SGST"
    bad_split[1].cgst = r2(bad_split[1].cgst * 1.35)
    bad_split[1].recompute_value()
    bad_split[1].note = "unequal halves"

    # two dated outside the year they were filed in
    for doc in take(2):
        doc.date = FY_START - dt.timedelta(days=rng.randrange(20, 90))
        doc.note = "dated before the financial year"

    # credit notes carry negative tax and belong in their own section
    for doc in docs:
        if doc.divergence == "credit_note":
            doc.section = "CDNR"
            doc.taxable = r2(-abs(doc.taxable) * rng.uniform(0.1, 0.5))
            tax = r2(doc.taxable * doc.rate / 100)
            if doc.interstate:
                doc.igst, doc.cgst, doc.sgst = tax, 0.0, 0.0
            else:
                doc.igst, doc.cgst, doc.sgst = 0.0, r2(tax / 2), r2(tax / 2)
            doc.recompute_value()

    return suppliers, docs


def gst_rows(docs):
    """The supplier's side: what the portal shows."""
    rows = []
    for doc in docs:
        if doc.divergence == "tally_only":
            continue
        rows.append(doc)
        if doc.divergence == "amended":
            amended = Doc(doc.supplier, doc.date, doc.taxable * 1.08, doc.rate,
                          doc.inv_no)
            amended.section = "B2BA"
            amended.note = "amends " + doc.inv_no
            rows.append(amended)
    return rows


def tally_rows(docs, rng):
    """Our side: what actually got keyed, divergences and all."""
    rows = []
    for doc in docs:
        d = doc.divergence
        if d == "gst_only":
            continue
        if d == "split":
            parts = rng.choice([2, 3])
            remaining = doc.value
            for i in range(parts):
                share = (r2(remaining / (parts - i)) if i < parts - 1
                         else r2(remaining))
                remaining = r2(remaining - share)
                rows.append({
                    "date": doc.date + dt.timedelta(days=i),
                    "party": doc.supplier.name,
                    "vch_no": f"{doc.inv_no}/{chr(65 + i)}",
                    "amount": share, "doc": doc})
            continue
        amount, number, date = doc.value, doc.inv_no, doc.date
        if d == "value":
            amount = r2(amount + rng.choice([-1, 1]) * rng.randrange(50, 2600))
        elif d == "invno":
            number = f"{doc.supplier.prefix}{rng.randrange(1000, 9999)}"
        elif d == "probable":
            number = f"{rng.randrange(100, 999)}"
            date = doc.date + dt.timedelta(days=rng.randrange(1, 5))
            amount = r2(amount * rng.uniform(0.998, 1.002))
        rows.append({"date": date, "party": doc.supplier.name,
                     "vch_no": number, "amount": amount, "doc": doc})
        if d == "duplicate":
            rows.append({"date": date + dt.timedelta(days=2),
                         "party": doc.supplier.name, "vch_no": number,
                         "amount": amount, "doc": doc})

    # purchases booked that the supplier never filed already exist as
    # tally_only; add a few cash and unregistered expenses too, which is the
    # ordinary reason a voucher has no counterpart in GSTR-2B
    return rows


# ---------------------------------------------------------------------------
# Workbook writers
# ---------------------------------------------------------------------------

BOLD = Font(bold=True)


def autosize(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


B2B_PARENTS = [
    "GSTIN of supplier", "Trade/Legal name", "Invoice Details", "", "", "",
    "Place of supply", "Supply Attract Reverse Charge", "Taxable Value (₹)",
    "Tax Amount", "", "", "", "GSTR-1/1A/IFF/GSTR-5 Period",
    "GSTR-1/1A/IFF/GSTR-5 Filing Date", "ITC Availability", "Reason",
    "Applicable % of Tax Rate", "Source", "IRN", "IRN Date",
]
B2B_CHILDREN = [
    "", "", "Invoice number", "Invoice type", "Invoice Date",
    "Invoice Value(₹)", "", "", "", "Integrated Tax(₹)",
    "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)",
    "", "", "", "", "", "", "", "",
]
B2B_WIDTHS = [20, 30, 18, 12, 12, 14, 16, 12, 14, 13, 13, 13, 9,
              12, 14, 12, 34, 12, 10, 12, 12]


def write_2b_sheet(ws, docs, period_label):
    ws.append(B2B_PARENTS)
    ws.append(B2B_CHILDREN)
    for cell in ws[1] + ws[2]:
        cell.font = BOLD
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for doc in docs:
        filed = last_day(doc.date) + dt.timedelta(days=doc.supplier.filing_lag)
        ws.append([
            doc.supplier.gstin, doc.supplier.name, doc.inv_no, "Regular",
            doc.date.strftime("%d/%m/%Y"), doc.value,
            STATES[doc.supplier.state if doc.interstate else HOME_STATE],
            "Yes" if doc.rcm == "Y" else "No",
            doc.taxable, doc.igst, doc.cgst, doc.sgst, doc.cess,
            period_label or doc.date.strftime("%b'%y"),
            filed.strftime("%d/%m/%Y"), doc.itc, doc.reason or "",
            "100%", "e-Invoice" if doc.value > 500000 else "",
            "", "",
        ])
    autosize(ws, B2B_WIDTHS)
    ws.freeze_panes = "A3"


def write_gstr2b(rows, out_dir, gstin):
    """Twelve monthly downloads, plus one merged workbook."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for doc in rows:
        key = (doc.date.year, doc.date.month)
        by_month.setdefault(key, []).append(doc)

    written = []
    for month in month_range():
        key = (month.year, month.month)
        docs = sorted(by_month.get(key, []), key=lambda d: (d.supplier.name, d.date))
        if not docs:
            continue
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        label = month.strftime("%b'%y")
        for section in ("B2B", "B2BA", "CDNR"):
            part = [d for d in docs if d.section == section]
            if not part:
                continue
            write_2b_sheet(wb.create_sheet(section), part, label)
        name = f"{month.strftime('%m%Y')}_{gstin}_GSTR2B_{FY_END:%d%m%Y}.xlsx"
        wb.save(out_dir / name)
        written.append(name)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for section in ("B2B", "B2BA", "CDNR"):
        part = sorted([d for d in rows if d.section == section],
                      key=lambda d: (d.date, d.supplier.name))
        if part:
            write_2b_sheet(wb.create_sheet(section), part, None)
    wb.save(out_dir / "MERGED_GSTR2B.xlsx")
    return written


def write_tally_purchase(rows, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    for line in (ENTITY_SHORT, f"UDYAM : {ENTITY_UDYAM}", "Purchase Register",
                 f"{FY_START:%d-%b-%y} to {FY_END:%d-%b-%y}"):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    ws.append(["", "", "", "", "Amount", "Amount"])
    for cell in ws[5] + ws[6]:
        cell.font = BOLD
    for row in sorted(rows, key=lambda r: (r["date"], r["party"])):
        doc = row["doc"]
        vch_type = "EXPENSES (GST)" if doc.supplier.size == "small" else "PURCHASE (GST)"
        if doc.section == "CDNR":
            vch_type = "CREDIT NOTE"
        ws.append([row["date"].strftime("%Y-%m-%d 00:00:00"), row["party"],
                   vch_type, row["vch_no"], None, r2(abs(row["amount"]))])
    autosize(ws, [22, 34, 18, 20, 14, 14])
    ws.freeze_panes = "A7"
    wb.save(path)


def write_sales(docs, path, gstr1_path, rng, suppliers):
    """An outward side, with a few documents the return never picked up."""
    customers = suppliers[:18]
    sales = []
    for month in month_range():
        for _ in range(rng.randrange(28, 46)):
            customer = rng.choice(customers)
            day = rng.randrange(1, (last_day(month) - month).days + 2)
            date = month + dt.timedelta(days=day - 1)
            rate = rng.choice(RATES)
            taxable = r2(rng.randrange(12_000, 900_000))
            interstate = customer.state != HOME_STATE
            tax = r2(taxable * rate / 100)
            sales.append({
                "date": date, "party": customer.name, "gstin": customer.gstin,
                "inv": f"KTM/{fy_tag(date)}/{len(sales) + 1001}",
                "taxable": taxable, "rate": rate,
                "igst": tax if interstate else 0.0,
                "cgst": 0.0 if interstate else r2(tax / 2),
                "sgst": 0.0 if interstate else r2(tax / 2),
                "pos": STATES[customer.state if interstate else HOME_STATE],
            })
            sales[-1]["value"] = r2(taxable + sales[-1]["igst"]
                                    + sales[-1]["cgst"] + sales[-1]["sgst"])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales Register"
    for line in (ENTITY_SHORT, "Sales Register",
                 f"{FY_START:%d-%b-%y} to {FY_END:%d-%b-%y}"):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Particulars", "Vch Type", "Vch No.", "GSTIN",
               "Taxable Value", "IGST", "CGST", "SGST", "Debit", "Credit"])
    for cell in ws[4]:
        cell.font = BOLD
    for s in sorted(sales, key=lambda x: x["date"]):
        ws.append([s["date"].strftime("%Y-%m-%d 00:00:00"), s["party"],
                   "SALES (GST)", s["inv"], s["gstin"], s["taxable"],
                   s["igst"], s["cgst"], s["sgst"], s["value"], None])
    autosize(ws, [22, 32, 16, 20, 20, 14, 12, 12, 12, 14, 12])
    wb.save(path)

    # the filed return: almost the same, minus a few and with one restated
    filed = [s for s in sales if rng.random() > 0.012]
    if filed:
        filed[3] = dict(filed[3], taxable=r2(filed[3]["taxable"] * 1.04))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "b2b"
    ws.append(["GSTIN/UIN of Recipient", "Receiver Name", "Invoice Number",
               "Invoice date", "Invoice Value", "Place Of Supply",
               "Reverse Charge", "Invoice Type", "Rate", "Taxable Value",
               "Integrated Tax", "Central Tax", "State/UT Tax", "Cess"])
    for cell in ws[1]:
        cell.font = BOLD
    for s in sorted(filed, key=lambda x: x["date"]):
        ws.append([s["gstin"], s["party"], s["inv"],
                   s["date"].strftime("%d-%b-%y"), s["value"], s["pos"], "N",
                   "Regular", s["rate"], s["taxable"], s["igst"], s["cgst"],
                   s["sgst"], 0])
    autosize(ws, [20, 30, 20, 13, 14, 16, 10, 12, 8, 14, 13, 12, 13, 8])
    wb.save(gstr1_path)
    return len(sales), len(filed)


# ---------------------------------------------------------------------------
# A scan of the bank statement, for the OCR path
# ---------------------------------------------------------------------------

def _statement_font(size):
    """A font that is not the app's own - this is meant to look like a bank."""
    from PIL import ImageFont
    for name in ("consola.ttf", "cour.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_scanned_pdf(rows, path, rng, dpi=200):
    """Print the statement to an image and then rough it up.

    A clean render would prove nothing: the point of the OCR path is that it
    copes with a page that has been through a printer and a scanner, so the
    page gets sensor noise, a faint gradient and a fraction of a degree of
    rotation - which is what makes the running-balance check worth running.

    Landscape, and at a real point size. The first attempt set body text at
    about 3.7pt, which is not a hard scan but an unreadable one: recognition
    turned 03/01/26 into 03/81/26 and lost the column headings entirely. A
    bank prints statements at 8-9pt, so that is what gets rendered - the page
    is meant to be difficult, not impossible.
    """
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np

    width, height = int(11.69 * dpi), int(8.27 * dpi)        # A4 landscape
    mono = _statement_font(int(dpi * 0.140))                 # ~10pt
    small = _statement_font(int(dpi * 0.120))
    head = _statement_font(int(dpi * 0.20))

    # How many rows fit is geometry, not a guess. Getting this wrong by four
    # rows a page silently dropped ten transactions out of the middle of the
    # statement, and the only symptom was the running-balance check refusing
    # to tie - which is the check doing its job on a corpus that was lying.
    line_h = int(dpi * 0.235)
    body_top = int(dpi * (0.45 + 0.42 + 3 * 0.24 + 0.22 + 0.26 + 0.16))
    per_page = (height - body_top - int(dpi * 0.45)) // line_h

    pages = []
    chunks = [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    assert sum(len(c) for c in chunks) == len(rows), "rows lost while paginating"

    for page_no, chunk in enumerate(chunks, start=1):
        img = Image.new("L", (width, height), 255)
        d = ImageDraw.Draw(img)
        x = int(dpi * 0.65)
        y = int(dpi * 0.45)
        d.text((x, y), synth_bank.BANK_NAME, font=head, fill=15)
        y += int(dpi * 0.42)
        for line in (f"Account No: {synth_bank.BANK_ACCOUNT}    "
                     f"IFSC: {synth_bank.BANK_IFSC}",
                     ENTITY_SHORT,
                     f"Period: {synth_bank.BANK_START:%d/%m/%Y} to "
                     f"{synth_bank.BANK_END:%d/%m/%Y}    "
                     f"Page {page_no} of {len(chunks)}"):
            d.text((x, y), line, font=small, fill=35)
            y += int(dpi * 0.24)

        y += int(dpi * 0.22)
        # The narration column is kept clear of the reference column by a wide
        # margin. Let a long narration run into it and its trailing UTR is
        # recognised as a figure in the withdrawal column - which is exactly
        # the failure the running-balance check exists to catch, but not
        # something the shipped sample should demonstrate.
        columns = [x, x + int(dpi * 1.25), x + int(dpi * 5.55),
                   x + int(dpi * 6.95), x + int(dpi * 8.40), x + int(dpi * 9.80)]
        for label, cx in zip(["Date", "Narration", "Ref", "Withdrawal",
                              "Deposit", "Balance"], columns):
            d.text((cx, y), label, font=mono, fill=15)
        # the rule sits well clear of the labels: run it too close and the
        # line-grouping folds it into the header, which loses the headings
        y += int(dpi * 0.26)
        d.line([x, y, width - x, y], fill=110, width=2)
        y += int(dpi * 0.16)

        printed = 0
        for event, balance in chunk:
            cells = [
                event["date"].strftime("%d/%m/%y"),
                event["narration"][:38],
                str(event["ref"])[:12],
                f"{event['debit']:,.2f}" if event["debit"] else "",
                f"{event['credit']:,.2f}" if event["credit"] else "",
                f"{balance:,.2f}",
            ]
            for text, cx in zip(cells, columns):
                d.text((cx, y), text, font=mono, fill=25)
            y += line_h
            printed += 1
        assert printed == len(chunk), (
            f"page {page_no} printed {printed} of {len(chunk)} rows")

        # through a printer and a scanner
        arr = np.asarray(img).astype(np.float32)
        gauss = np.random.default_rng(SEED + page_no).normal(0, 2.1, arr.shape)
        arr = arr + gauss
        ramp = np.linspace(0, 9, arr.shape[1])[None, :]
        arr = arr - ramp                                    # uneven illumination
        arr = np.clip(arr, 0, 255).astype("uint8")
        img = Image.fromarray(arr, mode="L")
        img = img.filter(ImageFilter.GaussianBlur(0.25))
        img = img.rotate(0.24 if page_no % 2 else -0.19, resample=Image.BICUBIC,
                         fillcolor=246)
        pages.append(img.convert("RGB"))

    pages[0].save(path, save_all=True, append_images=pages[1:],
                  resolution=float(dpi))
    return len(pages)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate(out=OUT):
    rng = random.Random(SEED)
    random.seed(SEED)                       # Supplier.next_invoice uses it

    purchases = out / "Purchases"
    bank_dir = out / "Bank"
    stmt_dir = out / "Statements"
    for d in (purchases / "GST", purchases / "Tally", purchases / "Sales",
              bank_dir, stmt_dir):
        d.mkdir(parents=True, exist_ok=True)

    entity_gstin = make_gstin(HOME_STATE, make_pan(rng, "K", "C"))
    suppliers, docs = build_purchases(rng)

    print(f"Entity      {ENTITY_NAME}")
    print(f"GSTIN       {entity_gstin}")
    print(f"Suppliers   {len(suppliers)}")

    two_b = gst_rows(docs)
    monthly = write_gstr2b(two_b, purchases / "GST", entity_gstin)
    print(f"GSTR-2B     {len(two_b)} rows over {len(monthly)} monthly files "
          f"plus MERGED_GSTR2B.xlsx")

    books = tally_rows(docs, rng)
    write_tally_purchase(books, purchases / "Tally" / "PurchaseRegister.xlsx")
    print(f"Tally       {len(books)} vouchers")

    n_sales, n_filed = write_sales(
        docs, purchases / "Sales" / "SalesRegister.xlsx",
        purchases / "Sales" / "GSTR1_Filed.xlsx", rng, suppliers)
    print(f"Outward     {n_sales} invoices, {n_filed} filed in GSTR-1")

    payees = [s.name for s in suppliers[:26]]
    payers = [s.name for s in suppliers[26:44]]
    events = synth_bank.build_events(rng, payees, payers)
    rows = synth_bank.write_statement(
        events, bank_dir / "Bank_Statement_Jan-Mar2026.xlsx", ENTITY_SHORT)
    synth_bank.write_bank_book(
        events, bank_dir / "Tally_BankBook_Jan-Mar2026.xlsx", ENTITY_SHORT)
    print(f"Bank        {len(rows)} statement lines, "
          f"{sum(1 for e in events if e['sides'] != 'bank')} book entries")

    pages = render_scanned_pdf(rows, bank_dir / "Bank_Statement_scanned.pdf", rng)
    print(f"Scan        {pages} page(s) at 200 dpi, for the OCR path")

    party = max(suppliers, key=lambda s: sum(
        1 for d in docs if d.supplier is s and d.section == "B2B"))
    entries = synth_bank.build_party(rng, party, docs)
    theirs = synth_bank.write_party_statement(
        entries, stmt_dir / f"{party.name.replace(' ', '_')}_SOA_FY2526.xlsx",
        party, ENTITY_SHORT)
    synth_bank.write_our_ledger(
        entries, stmt_dir / f"Tally_Ledger_{party.name.replace(' ', '_')}.xlsx",
        party, ENTITY_SHORT)
    print(f"Party       {party.name}, {len(entries)} entries, "
          f"their balance {theirs:,.2f}")

    # A manifest, so nothing downstream has to hardcode who the sample is.
    # The real client's GSTIN was once the fallback in the sample loader and
    # sat in the shipped payload; a generated file cannot drift like that.
    import json
    (out / "sample.json").write_text(json.dumps({
        "entity_name": ENTITY_NAME,
        "entity_short": ENTITY_SHORT,
        "gstin": entity_gstin,
        "address": ENTITY_ADDRESS,
        "fy_start": FY_START.year,
        "party": party.name,
        "party_gstin": party.gstin,
        "generated_by": "tools/generate_sample_data.py",
        "seed": SEED,
    }, indent=2), encoding="utf-8")
    print(f"Manifest    SampleData/sample.json")

    return {"gstin": entity_gstin, "suppliers": suppliers, "docs": docs,
            "party": party}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(info):
    """Check the corpus contains what was planted.

    Generating data is easy; generating data that still holds the defects you
    meant to plant, after every later edit, is not. This runs the app's own
    parser and reconciler over the output and fails loudly if the numbers have
    drifted - the corpus is a fixture, and a fixture nobody checks is a fixture
    that quietly rots.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from app.backend.parser import merge_multiple_gstr2b, parse_tally_file
    from app.backend.reconciler import reconcile_data

    gst_dir = OUT / "Purchases" / "GST"
    merged = gst_dir / "MERGED_GSTR2B.xlsx"
    gst = merge_multiple_gstr2b([merged.read_bytes()], filenames=[merged.name])
    tally = parse_tally_file(
        (OUT / "Purchases" / "Tally" / "PurchaseRegister.xlsx").read_bytes())
    gst_records = gst["records"] if isinstance(gst, dict) else gst
    tally_records = tally["records"] if isinstance(tally, dict) else tally
    result = reconcile_data(gst_records, tally_records)

    counts = result["counts"]
    statuses = {s["key"]: s["count"] for s in result["status_summary"]}
    print("\nReconciled:")
    for key, value in sorted(statuses.items()):
        if value:
            print(f"  {key:22} {value}")

    problems = []
    total = statuses.get("total", 0)
    if total < 900:
        problems.append(f"only {total} documents; expected 900+")
    # Floors, not exact counts: the passes run in order, so a document planted
    # as a probable match is often caught by an earlier, stricter pass. That is
    # the engine working, not the corpus being wrong - only a category falling
    # to nothing means a planted defect has stopped existing.
    for key, floor in (("value_mismatch", 5), ("inv_mismatch", 10),
                       ("gst_only", 20), ("tally_only", 15),
                       ("split", 3), ("matched", 700)):
        if statuses.get(key, 0) < floor:
            problems.append(f"{key} is {statuses.get(key, 0)}, expected >= {floor}")

    # every GSTIN must survive its own check digit
    bad = [d["supplier_gstin"] for d in gst_records
           if d.get("supplier_gstin")
           and gstin_check_digit(d["supplier_gstin"][:14]) != d["supplier_gstin"][14]]
    if bad:
        problems.append(f"{len(bad)} GSTINs fail the check digit, e.g. {bad[0]}")

    # and nothing from the real client may survive anywhere
    text = " ".join(str(d.get("supplier_name", "")) for d in gst_records)
    for hit in real_client_words(text):
        problems.append(f"real client data leaked into the corpus (fingerprint {hit})")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("\nAll planted defects present, all GSTINs valid, no real data.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()
    info = generate(args.out)
    if not args.skip_verify:
        verify(info)
