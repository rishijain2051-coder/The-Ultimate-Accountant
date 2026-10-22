"""
The bank and party-statement half of the sample corpus.

Split from generate_sample_data.py because it models a different thing: that
file invents invoices, this one invents the money moving against them and the
two records of it that never quite agree.

Both sides are derived from one list of events, each carrying a `sides` marker
saying which record it reaches - `both`, `bank` only, `book` only, or `batch`
for one receipt answering several invoices. Every reconciling category the app
reports has at least one event planted for it here, so the reconciliation has
something true to find rather than an empty statement that trivially ties.
"""

import datetime as dt
import random
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

BOLD = Font(bold=True)

BANK_NAME = "STATE BANK OF INDIA"
BANK_IFSC = "SBIN0031456"
BANK_ACCOUNT = "38104477219"
BANK_START = dt.date(2026, 1, 1)
BANK_END = dt.date(2026, 3, 31)
OPENING = 8_431_920.60

# what a bank does that books routinely miss
CHARGE_KINDS = [
    ("NEFT CHARGES", 23.60), ("RTGS CHARGES", 29.50),
    ("SMS ALERT CHARGES", 17.70), ("ACCOUNT MAINTENANCE CHARGES", 590.00),
    ("CHEQUE BOOK ISSUE CHARGES", 177.00), ("CASH HANDLING CHARGES", 118.00),
]


def r2(x):
    return round(x + 1e-9, 2)


def autosize(ws, widths):
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def narration(kind, party, ref):
    if kind == "NEFT":
        return f"NEFT DR-{BANK_IFSC}-{party}-N{ref}"
    if kind == "NEFT-CR":
        return f"NEFT CR-HDFC0000512-{party}-N{ref}"
    if kind == "RTGS":
        return f"RTGS DR-{BANK_IFSC}-{party}-R{ref}"
    if kind == "IMPS":
        return f"IMPS-{ref}-{party}-PAYMENT"
    if kind == "UPI":
        return f"UPI-{party.split()[0].lower()}@okaxis-{ref}-PAYMENT"
    if kind == "CHQ":
        return f"CHQ PAID - {ref}"
    if kind == "ACH":
        return f"ACH D- {party} EMI {ref}"
    if kind == "CASH-DR":
        return f"CASH WDL SELF {ref}"
    if kind == "CASH-CR":
        return f"CASH DEP {party} {ref}"
    return f"{kind} {party}".strip()


def build_events(rng, payees, payers):
    events = []
    counter = [4471200]

    def add(date, kind, party, debit=0.0, credit=0.0, *, sides="both",
            note=None, book_amount=None, reference=None):
        counter[0] += rng.randrange(7, 90)
        marker = reference or str(counter[0])
        events.append({
            "date": date, "kind": kind, "party": party,
            "narration": narration(kind, party, marker), "ref": marker,
            "debit": r2(debit), "credit": r2(credit), "sides": sides,
            "note": note, "book_date": date, "book_amount": book_amount,
        })

    # A trading business collects roughly what it pays out, so the receipts
    # are sized against the payments rather than drawn from an independent
    # range. Draw the two sides independently and the balance either drifts
    # overdrawn or piles up into crores over a single quarter - both of which
    # read as broken figures however correctly the reconciliation ties on them.
    day = BANK_START
    while day <= BANK_END:
        if day.weekday() != 6:
            paid = 0.0
            for _ in range(rng.randrange(0, 3)):
                kind = rng.choice(["NEFT", "NEFT", "RTGS", "IMPS", "UPI", "CHQ"])
                amount = (rng.randrange(2_000, 90_000) if kind in ("UPI", "IMPS")
                          else rng.randrange(15_000, 850_000))
                paid += amount
                add(day, kind, rng.choice(payees), debit=amount,
                    reference=str(rng.randrange(445000, 446500))
                    if kind == "CHQ" else None)

            # collections lag and lump together, so the day's receipts only
            # loosely track the day's payments
            target = paid * rng.uniform(0.55, 1.45)
            receipts = rng.randrange(1, 3) if target > 120_000 else 1
            for i in range(receipts):
                share = target / receipts
                amount = int(max(40_000, share * rng.uniform(0.7, 1.3)))
                add(day, "NEFT-CR", rng.choice(payers), credit=amount)
        day += dt.timedelta(days=1)

    # cheques issued but never presented; the first two are stale past 120 days
    for i in range(5):
        issued = (BANK_START + dt.timedelta(days=i * 3) if i < 2
                  else BANK_START + dt.timedelta(days=rng.randrange(20, 80)))
        add(issued, "CHQ", rng.choice(payees),
            debit=rng.randrange(28_000, 240_000), sides="book",
            note="issued, not presented",
            reference=str(rng.randrange(445000, 446500)))

    # deposits entered in the books the bank has not credited yet
    for _ in range(2):
        add(BANK_END - dt.timedelta(days=rng.randrange(0, 4)), "CASH-CR",
            rng.choice(payers), credit=rng.randrange(60_000, 320_000),
            sides="book", note="deposited, not yet credited")

    # charges, and the GST on them, which is creditable and never booked
    for _ in range(7):
        date = BANK_START + dt.timedelta(days=rng.randrange(2, 88))
        label, amount = rng.choice(CHARGE_KINDS)
        add(date, label, "", debit=amount, sides="bank",
            note="charge not in books")
        add(date, "GST ON BANK CHARGES", "", debit=r2(amount * 0.18),
            sides="bank", note="creditable tax sitting in GSTR-2B")

    add(BANK_END, "CREDIT INTEREST", "", credit=8_412.00, sides="bank",
        note="interest not in books")

    for _ in range(2):
        add(BANK_START + dt.timedelta(days=rng.randrange(10, 85)), "ACH",
            "SUNRISE EQUIPMENT FINANCE", debit=rng.randrange(35_000, 95_000),
            sides="bank", note="standing instruction never booked")

    # the same payment recorded at two different figures
    add(BANK_START + dt.timedelta(days=31), "NEFT", payees[4],
        debit=147_500.00, book_amount=147_000.00, note="amount differs")

    # one receipt answering three invoices, booked as three entries
    add(BANK_START + dt.timedelta(days=44), "NEFT-CR", payers[2],
        credit=615_000.00, sides="batch", note="one credit, three entries")

    # cash close enough to the section 269ST line to be worth a look
    add(BANK_START + dt.timedelta(days=58), "CASH-DR", "", debit=198_000.00,
        note="cash near the 269ST limit")

    # a debit the bank printed with no narration at all, only a "." - the
    # explanation has to name why it is open all the same. Appended directly,
    # without drawing from rng, so every other figure in the corpus stays put.
    dot_day = BANK_START + dt.timedelta(days=67)
    events.append({"date": dot_day, "kind": "DOT", "party": "", "narration": ".",
                   "ref": "", "debit": 236.00, "credit": 0.0, "sides": "bank",
                   "note": "no narration, only a dot", "book_date": dot_day,
                   "book_amount": None})

    events.sort(key=lambda e: (e["date"], e["narration"]))
    return events


def statement_rows(events):
    """The bank's side, with its running balance chained exactly."""
    rows, balance = [], OPENING
    low = balance
    for e in events:
        if e["sides"] == "book":
            continue
        balance = r2(balance - e["debit"] + e["credit"])
        low = min(low, balance)
        rows.append((e, balance))
    # The balance has to stay in a band a working current account would sit
    # in: never overdrawn, and not compounding into crores over one quarter.
    # Both failures came from drawing receipts and payments independently.
    assert low > 0, f"the statement goes overdrawn to {low:,.2f}"
    assert balance < OPENING * 5, (
        f"the statement closes at {balance:,.2f}, {balance / OPENING:.1f}x "
        "opening - receipts are outrunning payments")
    return rows


def write_statement(events, path, entity):
    rows = statement_rows(events)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Account Statement"
    for line in (BANK_NAME, "Statement of account",
                 f"Account No: {BANK_ACCOUNT}    IFSC: {BANK_IFSC}", entity,
                 f"Period: {BANK_START:%d/%m/%Y} to {BANK_END:%d/%m/%Y}", ""):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Narration", "Chq./Ref.No.", "Value Dt",
               "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    for cell in ws[7]:
        cell.font = BOLD
    ws.append([BANK_START.strftime("%d/%m/%Y"), "OPENING BALANCE", "", "",
               None, None, OPENING])
    for e, balance in rows:
        ws.append([e["date"].strftime("%d/%m/%Y"), e["narration"], e["ref"],
                   e["date"].strftime("%d/%m/%Y"),
                   e["debit"] or None, e["credit"] or None, balance])
    autosize(ws, [13, 54, 16, 12, 17, 16, 18])
    ws.freeze_panes = "A8"
    wb.save(path)
    return rows


def write_bank_book(events, path, entity):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bank Book"
    for line in (entity, f"{BANK_NAME} - {BANK_ACCOUNT}", "Bank Book",
                 f"{BANK_START:%d-%b-%y} to {BANK_END:%d-%b-%y}"):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Particulars", "Vch Type", "Vch No.", "Ref No.",
               "Debit", "Credit"])
    ws.append(["", "", "", "", "", "Amount", "Amount"])
    for cell in ws[5] + ws[6]:
        cell.font = BOLD
    ws.append([BANK_START.strftime("%d-%b-%y"), "Opening Balance", "", "", "",
               OPENING, None])

    lines = []
    for e in events:
        if e["sides"] == "bank":
            continue
        amount = (e["book_amount"] if e["book_amount"] is not None
                  else (e["debit"] or e["credit"]))
        if e["sides"] == "batch":
            share = r2(amount / 3)
            for i in range(3):
                lines.append((e, share if i < 2 else r2(amount - 2 * share),
                              True, i))
            continue
        lines.append((e, amount, e["credit"] > 0, 0))

    n = 0
    for e, amount, is_receipt, index in sorted(
            lines, key=lambda x: (x[0]["book_date"], x[0]["narration"], x[3])):
        n += 1
        kind, ref = e["kind"], e["ref"]
        if kind == "CHQ":
            ref_text = f"Cheque {ref}"
        elif kind in ("NEFT", "NEFT-CR", "RTGS", "IMPS", "UPI"):
            ref_text = f"{kind.replace('-CR', '')} {ref}"
        else:
            ref_text = ref
        party = e["party"] or kind.title()
        ws.append([
            e["book_date"].strftime("%Y-%m-%d 00:00:00"),
            f"{party} - part {index + 1}" if index else party,
            "Receipt" if is_receipt else "Payment",
            f"{'BR' if is_receipt else 'BP'}/{n}", ref_text,
            amount if is_receipt else None,
            None if is_receipt else amount,
        ])
    autosize(ws, [22, 36, 12, 12, 20, 14, 14])
    ws.freeze_panes = "A7"
    wb.save(path)


# ---------------------------------------------------------------------------
# A party statement of account, and our ledger for the same party
# ---------------------------------------------------------------------------

def build_party(rng, supplier, docs):
    """Their version of the account, and ours, differing in named ways.

    The interesting case is an invoice they have billed and never filed on the
    portal: our ledger does not have it, GSTR-2B does not have it, and only
    their own statement says it exists. That is what the three-way check is
    for, and the corpus has to contain one for it to prove anything.
    """
    mine = [d for d in docs if d.supplier is supplier and d.section == "B2B"]
    mine.sort(key=lambda d: d.date)
    entries = []

    running = 0.0
    for doc in mine:
        entries.append({"date": doc.date, "kind": "invoice",
                        "ref": doc.inv_no, "amount": doc.value,
                        "sides": "both"})
        running += doc.value
        if rng.random() < 0.62:
            pay_date = doc.date + dt.timedelta(days=rng.randrange(18, 70))
            if pay_date <= dt.date(2026, 3, 31):
                entries.append({"date": pay_date, "kind": "payment",
                                "ref": f"PMT/{rng.randrange(600, 999)}",
                                "amount": r2(doc.value * rng.choice(
                                    [1.0, 1.0, 0.5])), "sides": "both"})

    # billed by them, never filed and never booked by us
    entries.append({"date": dt.date(2025, 11, 14), "kind": "invoice",
                    "ref": f"{supplier.prefix}/7741", "amount": 186_500.00,
                    "sides": "theirs", "note": "billed, never filed"})
    # a credit note they raised that we never recorded
    entries.append({"date": dt.date(2025, 12, 27), "kind": "credit",
                    "ref": f"CN/{supplier.prefix}/58", "amount": 41_300.00,
                    "sides": "theirs", "note": "credit note not in our books"})
    # a payment we made in the last days that they have not applied
    entries.append({"date": dt.date(2026, 3, 27), "kind": "payment",
                    "ref": "PMT/1042", "amount": 225_000.00, "sides": "ours",
                    "note": "paid, not yet applied"})
    # the same invoice at two different figures
    if len(mine) > 6:
        entries.append({"date": mine[6].date, "kind": "restate",
                        "ref": mine[6].inv_no, "amount": r2(mine[6].value + 3_150),
                        "sides": "theirs-only-amount",
                        "note": "same invoice, different figure"})

    entries.sort(key=lambda e: (e["date"], e["ref"]))
    return entries


def write_party_statement(entries, path, supplier, entity):
    """Their statement: their debit is what we owe them."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Statement"
    for line in (supplier.name, f"GSTIN: {supplier.gstin}",
                 f"Statement of account - {entity}",
                 "1-Apr-2025 to 31-Mar-2026", ""):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Particulars", "Voucher No", "Debit", "Credit",
               "Balance"])
    for cell in ws[6]:
        cell.font = BOLD
    ws.append(["01-Apr-2025", "Opening Balance", "", None, None, 0])

    balance = 0.0
    for e in entries:
        if e["sides"] == "ours":
            continue
        amount = e["amount"]
        if e["sides"] == "theirs-only-amount":
            label, debit, credit = f"Sales - {e['ref']}", amount, None
        elif e["kind"] == "invoice":
            label, debit, credit = f"Sales - {e['ref']}", amount, None
        elif e["kind"] == "credit":
            label, debit, credit = f"Credit Note - {e['ref']}", None, amount
        else:
            label, debit, credit = f"Receipt - {e['ref']}", None, amount
        balance = r2(balance + (debit or 0) - (credit or 0))
        ws.append([e["date"].strftime("%d-%b-%Y"), label, e["ref"],
                   debit, credit, balance])
    autosize(ws, [14, 34, 18, 14, 14, 16])
    wb.save(path)
    return balance


def write_our_ledger(entries, path, supplier, entity):
    """Our ledger for them: our credit is what we owe."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    for line in (entity, supplier.name, "Ledger Account",
                 "1-Apr-25 to 31-Mar-26"):
        ws.append([line])
    ws["A1"].font = BOLD
    ws.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    for cell in ws[5]:
        cell.font = BOLD

    for e in entries:
        if e["sides"] in ("theirs", "theirs-only-amount"):
            continue
        amount = e["amount"]
        if e["kind"] == "invoice":
            ws.append([e["date"].strftime("%Y-%m-%d 00:00:00"),
                       f"Purchase - {e['ref']}", "PURCHASE (GST)", e["ref"],
                       None, amount])
        elif e["kind"] == "credit":
            ws.append([e["date"].strftime("%Y-%m-%d 00:00:00"),
                       f"Credit Note - {e['ref']}", "CREDIT NOTE", e["ref"],
                       amount, None])
        else:
            ws.append([e["date"].strftime("%Y-%m-%d 00:00:00"),
                       f"Payment - {e['ref']}", "Payment", e["ref"],
                       amount, None])
    # the restated invoice is already in our books at its original figure,
    # written above as an ordinary purchase - that is the whole point of it
    autosize(ws, [22, 34, 18, 18, 14, 14])
    wb.save(path)
