"""
Self-check for the bank, party-statement and document-intake engines.

The two bridges here are arithmetic identities, so the tests that matter are
the ones that assert they tie to zero. Both of the sign bugs found while
building this - a doubled matched-items term and a credit note pushed the wrong
way - showed up as a non-zero residual and nothing else, which is exactly why
the residual is computed and asserted rather than assumed.

Imported into selftest.py, so `python -m app.backend.selftest` runs it too.
"""

from app.backend.common import iso, to_date

D = to_date


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _bt(i, date, narration, dr=0.0, cr=0.0, ref="", balance=None):
    """One bank statement line."""
    from app.backend.bank import classify_narration
    info = classify_narration(narration)
    return {
        "id": i, "date": D(date), "value_date": D(date), "narration": narration,
        "ref": ref or (info["refs"][0] if info["refs"] else ""),
        "debit": round(dr, 2), "credit": round(cr, 2), "net": round(cr - dr, 2),
        "balance": balance, "channel": info["channel"],
        "counterparty": info["counterparty"], "refs": info["refs"],
        "month_key": D(date).strftime("%Y-%m"),
        "fp": f"bk{i}", "source_row": i + 8, "flags": [],
    }


def _bb(i, date, party, dr=0.0, cr=0.0, vch="", ref="", vch_type="Payment"):
    """One bank book voucher."""
    from app.backend.common import party_core
    return {
        "id": i, "date": D(date), "party": party, "party_core": party_core(party),
        "vch_no": vch, "vch_type": vch_type, "ref": ref,
        "debit": round(dr, 2), "credit": round(cr, 2), "net": round(dr - cr, 2),
        "month_key": D(date).strftime("%Y-%m"),
        "fp": f"bb{i}", "source_row": i + 7, "flags": [],
    }


def _se(i, side, date, particulars, dr=0.0, cr=0.0, ref=""):
    """One party-statement or ledger entry."""
    from app.backend.statements import classify_entry
    owed = (dr - cr) if side == "theirs" else (cr - dr)
    return {
        "id": i, "side": side, "date": D(date), "due_date": None,
        "particulars": particulars, "ref": ref,
        "kind": classify_entry(particulars, ref),
        "debit": round(dr, 2), "credit": round(cr, 2), "owed": round(owed, 2),
        "balance": None, "month_key": D(date).strftime("%Y-%m"),
        "fp": f"{side}{i}", "source_row": i + 7, "flags": [],
    }


# ---------------------------------------------------------------------------
# bank: narration reading
# ---------------------------------------------------------------------------

def test_bank_narration_channels():
    from app.backend.bank import classify_narration
    cases = [
        ("NEFT DR-HDFC0000123-SHREE KRISHNA-N70012", "NEFT"),
        ("NEFTCR-RAJPUTEXPORTS-N810000", "NEFT"),           # recognition glues these
        ("CHQ PAID - 445128 - OMEGA", "CHEQUE"),
        ("GST ON BANK CHARGES @18PCT", "TAX"),              # a tax, not a charge
        ("BANK CHARGES-ACCT MAINTENANCE", "CHARGES"),
        ("NEFT RETURN CHARGES-INSUFFICIENT", "CHARGES"),    # nature beats the rail
        ("INT.PD:01-01-2026 TO 31-03-2026", "INTEREST"),
        ("ACH D- HDFC LOAN EMI-0012845", "LOAN"),
        ("ATM WDL-MI ROAD-4471", "ATM"),
        ("UPI-DELTA CRAFTS-982944@ybl-700445", "UPI"),
    ]
    for narration, expected in cases:
        got = classify_narration(narration)["channel"]
        assert got == expected, f"{narration!r} read as {got}, expected {expected}"
    # The opening word boundary is load-bearing: "pos" must not match inside
    # "DEPOSIT", or every deposit reads as a card payment.
    assert classify_narration("NEFT CR-DEPOSIT FROM ABC")["channel"] == "NEFT"
    assert classify_narration("NEFT DR-ALPHA-N1")["counterparty"] == "ALPHA"
    # An IFSC is not a reference number.
    assert "HDFC0000123" not in classify_narration(
        "NEFT DR-HDFC0000123-SHREE KRISHNA-N700123")["refs"]


def test_split_leading_date_handles_merged_cells():
    from app.backend.bank import split_leading_date
    d, rest = split_leading_date("01/04/2025NEFT-SHREE KRISHNA")
    assert iso(d) == "2025-04-01" and rest == "NEFT-SHREE KRISHNA"
    d2, rest2 = split_leading_date("NEFT ONLY")
    assert d2 is None and rest2 == "NEFT ONLY"


# ---------------------------------------------------------------------------
# bank: matching and the BRS
# ---------------------------------------------------------------------------

def test_bank_reference_match():
    from app.backend.bank import reconcile_bank
    bank = [_bt(0, "2026-01-05", "CHQ PAID - 445128 - ALPHA TRADERS", dr=11800)]
    book = [_bb(0, "2026-01-02", "ALPHA TRADERS", cr=11800, vch="BP/1",
                ref="CHQ 445128")]
    res = reconcile_bank(bank, book, today=D("2026-02-01"),
                         bank_meta={"opening_balance": 0.0, "closing_balance": -11800.0},
                         book_meta={"opening_balance": 0.0, "closing_balance": -11800.0})
    assert res["counts"]["matched"] == 1
    assert res["all_records"][0]["match_basis"] == "reference + amount"
    assert res["brs"]["ties"] is True


def test_bank_brs_ties_with_every_category():
    from app.backend.bank import reconcile_bank
    bank = [
        _bt(0, "2026-01-05", "NEFT DR-ALPHA TRADERS-N1", dr=10000),    # agrees
        _bt(1, "2026-01-31", "BANK CHARGES-AMC", dr=500),              # bank only
        _bt(2, "2026-01-31", "GST ON BANK CHARGES", dr=90),            # bank only
        _bt(3, "2026-01-31", "INT.PD:QUARTER", cr=1200),               # bank only
        _bt(4, "2026-01-20", "ACH D- LOAN EMI-99", dr=5000),           # bank only
    ]
    book = [
        _bb(0, "2026-01-05", "ALPHA TRADERS", cr=10000, vch="BP/1"),
        _bb(1, "2026-01-28", "BETA WOODS", cr=7000, vch="BP/2"),       # unpresented
        _bb(2, "2026-01-29", "GAMMA SALES", dr=3000, vch="BR/1",
            vch_type="Receipt"),                                       # uncredited
    ]
    bank_close = round(sum(t["net"] for t in bank), 2)
    book_close = round(sum(e["net"] for e in book), 2)
    res = reconcile_bank(bank, book, today=D("2026-02-05"),
                         bank_meta={"opening_balance": 0.0, "closing_balance": bank_close},
                         book_meta={"opening_balance": 0.0, "closing_balance": book_close})

    assert res["brs"]["ties"] is True, res["brs"]["residual"]
    assert res["brs"]["residual"] == 0.0
    keys = {g["key"]: g for g in res["brs"]["groups"]}
    assert keys["unpresented"]["effect"] == 7000.0       # bank balance is higher
    assert keys["uncredited"]["effect"] == -3000.0       # bank balance is lower
    assert keys["bank_charges"]["effect"] == -590.0      # the charge and its tax
    assert keys["bank_interest"]["effect"] == 1200.0
    assert keys["bank_debits"]["effect"] == -5000.0
    # The charge and the tax on it are reported apart: the tax is creditable.
    assert res["charges"] == {"bank_charges": 500.0, "gst_on_charges": 90.0,
                              "total": 590.0}


def test_bank_batch_and_amount_difference():
    from app.backend.bank import reconcile_bank
    bank = [
        _bt(0, "2026-01-10", "CLEARING DEPOSIT - 3 INSTR", cr=60000),
        _bt(1, "2026-01-12", "NEFT DR-DELTA TIMBER-N2", dr=20090),
    ]
    book = [
        _bb(0, "2026-01-09", "CUST ONE", dr=30000, vch="BR/1", vch_type="Receipt"),
        _bb(1, "2026-01-09", "CUST TWO", dr=20000, vch="BR/2", vch_type="Receipt"),
        _bb(2, "2026-01-10", "CUST THREE", dr=10000, vch="BR/3", vch_type="Receipt"),
        _bb(3, "2026-01-12", "DELTA TIMBER", cr=20000, vch="BP/9"),
    ]
    bank_close = round(sum(t["net"] for t in bank), 2)
    book_close = round(sum(e["net"] for e in book), 2)
    res = reconcile_bank(bank, book, today=D("2026-02-01"),
                         bank_meta={"opening_balance": 0.0, "closing_balance": bank_close},
                         book_meta={"opening_balance": 0.0, "closing_balance": book_close})
    batched = [r for r in res["all_records"] if r["status"] == "Batched"]
    assert len(batched) == 1
    assert len(batched[0]["parts"]) == 3
    # The pair has to carry the whole batch, or the rest of it reads as a
    # difference and the BRS is out by the remainder.
    assert batched[0]["book_net"] == 60000.0
    diff = [r for r in res["all_records"] if r["status"] == "Amount differs"]
    assert len(diff) == 1 and diff[0]["difference"] == 90.0
    assert res["brs"]["ties"] is True, res["brs"]["residual"]


def test_bank_no_cross_direction_match():
    """A payment must never match a receipt, however well the amounts agree."""
    from app.backend.bank import reconcile_bank
    bank = [_bt(0, "2026-01-05", "NEFT CR-ALPHA TRADERS-N1", cr=10000)]
    book = [_bb(0, "2026-01-05", "ALPHA TRADERS", cr=10000, vch="BP/1")]
    res = reconcile_bank(bank, book, today=D("2026-02-01"),
                         bank_meta={"opening_balance": 0.0, "closing_balance": 10000.0},
                         book_meta={"opening_balance": 0.0, "closing_balance": -10000.0})
    assert res["counts"]["matched"] == 0
    assert res["counts"]["bank_only"] == 1 and res["counts"]["book_only"] == 1
    assert res["brs"]["ties"] is True


def test_bank_stale_cheque_flagged():
    from app.backend.bank import reconcile_bank
    book = [_bb(0, "2026-01-02", "OLD PAYEE", cr=5000, vch="BP/1")]
    res = reconcile_bank([], book, today=D("2026-06-30"),
                         bank_meta={"opening_balance": 0.0, "closing_balance": 0.0},
                         book_meta={"opening_balance": 0.0, "closing_balance": -5000.0})
    rec = res["all_records"][0]
    assert rec["category"] == "Cheque issued, not presented"
    assert "stale_cheque" in rec["flags"]
    assert any(f["key"] == "stale" for f in res["findings"])


def test_bank_continuity_break_is_reported():
    from app.backend.bank import parse_bank_statement
    grid = [
        ["HDFC BANK", "", "", "", ""],
        ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
        ["01/01/2026", "OPENING BALANCE", "", "", "1000.00"],
        ["02/01/2026", "NEFT DR-ALPHA-N1", "100.00", "", "900.00"],
        ["03/01/2026", "NEFT DR-BETA-N2", "100.00", "", "700.00"],    # should be 800
    ]
    out = parse_bank_statement(grid)
    assert out["meta"]["continuity_ok"] is False
    assert out["issues"] and "running balance" in out["issues"][0]
    assert any("balance_break" in t["flags"] for t in out["transactions"])


def test_bank_statement_parses_single_amount_column():
    """Some banks ship one amount column with a Dr/Cr flag instead of two."""
    from app.backend.bank import parse_bank_statement
    grid = [
        ["ICICI BANK", "", "", ""],
        ["Txn Date", "Description", "Amount", "Dr/Cr"],
        ["05/01/2026", "NEFT DR-ALPHA TRADERS-N1", "11800.00", "DR"],
        ["06/01/2026", "NEFT CR-BETA CUSTOMER-N2", "5000.00", "CR"],
    ]
    out = parse_bank_statement(grid)
    assert out["meta"]["count"] == 2
    assert out["transactions"][0]["debit"] == 11800.0
    assert out["transactions"][0]["credit"] == 0.0
    assert out["transactions"][1]["credit"] == 5000.0
    assert out["meta"]["bank"] == "ICICI"


def test_bank_opening_balance_row_anywhere_in_the_row():
    """Tally puts the date first and the label second."""
    from app.backend.bank import parse_bank_book
    grid = [
        ["KESARI TEXTILE MILLS", "", "", "", "", "", ""],
        ["Date", "Particulars", "Vch Type", "Vch No.", "Ref No.", "Debit", "Credit"],
        ["1-Jan-26", "Opening Balance", "", "", "", "418650", ""],
        ["5-Jan-26", "ALPHA TRADERS", "Payment", "BP/1", "CHQ 1", "", "10000"],
    ]
    out = parse_bank_book(grid)
    assert out["meta"]["opening_balance"] == 418650.0
    assert out["meta"]["count"] == 1
    assert out["meta"]["closing_balance"] == 408650.0


# ---------------------------------------------------------------------------
# party statements
# ---------------------------------------------------------------------------

def test_statement_sign_convention_flips():
    # Their debit and our credit on the same invoice both mean "the supplier is
    # owed this", so both normalise to the same positive figure.
    t = _se(0, "theirs", "2026-01-05", "Sales - INV/1", dr=11800, ref="INV/1")
    o = _se(0, "ours", "2026-01-05", "Purchase - INV/1", cr=11800, ref="INV/1")
    assert t["owed"] == 11800.0 and o["owed"] == 11800.0
    # A payment reduces it on both sides.
    tp = _se(1, "theirs", "2026-01-20", "Receipt - NEFT", cr=11800, ref="RCT/1")
    op = _se(1, "ours", "2026-01-20", "HDFC Bank", dr=11800, ref="BP/1")
    assert tp["owed"] == -11800.0 and op["owed"] == -11800.0


def test_statement_bridge_ties_and_credit_note_sign():
    from app.backend.statements import reconcile_statement
    theirs = [
        _se(0, "theirs", "2026-01-05", "Sales - INV/1", dr=11800, ref="INV/1"),
        _se(1, "theirs", "2026-01-20", "Receipt - NEFT", cr=5000, ref="RCT/1"),
        _se(2, "theirs", "2026-03-28", "Sales - INV/9", dr=23600, ref="INV/9"),
        _se(3, "theirs", "2026-03-30", "Credit Note - rate diff", cr=1180, ref="CN/1"),
    ]
    ours = [
        _se(0, "ours", "2026-01-05", "Purchase - INV/1", cr=11800, ref="INV/1"),
        _se(1, "ours", "2026-01-20", "HDFC Bank", dr=5000, ref="RCT/1"),
        _se(2, "ours", "2026-03-31", "HDFC Bank", dr=2000, ref="BP/77"),
    ]
    their_close = round(sum(e["owed"] for e in theirs), 2)
    our_close = round(sum(e["owed"] for e in ours), 2)
    res = reconcile_statement(
        theirs, ours, today=D("2026-04-10"),
        their_meta={"opening_balance": 0.0, "closing_balance": their_close},
        our_meta={"opening_balance": 0.0, "closing_balance": our_close})

    assert res["bridge"]["ties"] is True, res["bridge"]["residual"]
    assert res["bridge"]["residual"] == 0.0
    keys = {g["key"]: g for g in res["bridge"]["groups"]}
    # An invoice only they have raises their balance.
    assert keys["their_invoices"]["effect"] == 23600.0
    # A credit note only they have LOWERS it. Applying a fixed "Add" sign to an
    # absolute amount got this backwards and left the bridge out by twice it.
    assert keys["their_credits"]["effect"] == -1180.0
    # A payment only we have lowers their balance relative to ours.
    assert keys["our_payments"]["effect"] == 2000.0
    assert res["counts"]["agreed"] == 2


def test_statement_dispute_detected():
    from app.backend.statements import reconcile_statement
    theirs = [_se(0, "theirs", "2026-01-05", "Sales - INV/5", dr=36285, ref="INV/5")]
    ours = [_se(0, "ours", "2026-01-05", "Purchase - INV/5", cr=31565, ref="INV/5")]
    res = reconcile_statement(
        theirs, ours, today=D("2026-02-01"),
        their_meta={"opening_balance": 0.0, "closing_balance": 36285.0},
        our_meta={"opening_balance": 0.0, "closing_balance": 31565.0})
    rec = res["all_records"][0]
    assert rec["status"] == "Amount disputed"
    assert rec["difference"] == 4720.0
    assert res["bridge"]["ties"] is True
    assert any(f["key"] == "disputed" for f in res["findings"])


def test_statement_timing_difference():
    from app.backend.statements import reconcile_statement
    theirs = [_se(0, "theirs", "2026-03-30", "Sales - INV/7", dr=9000, ref="INV/7")]
    ours = [_se(0, "ours", "2026-04-25", "Purchase - INV/7A", cr=9000, ref="INV/7A")]
    res = reconcile_statement(
        theirs, ours, today=D("2026-05-01"),
        their_meta={"opening_balance": 0.0, "closing_balance": 9000.0},
        our_meta={"opening_balance": 0.0, "closing_balance": 9000.0})
    assert res["counts"]["timing"] == 1
    assert res["all_records"][0]["date_gap_days"] == 26
    assert res["bridge"]["ties"] is True


def test_statement_three_way_against_what_they_filed():
    from app.backend.statements import reconcile_statement
    theirs = [
        _se(0, "theirs", "2026-01-05", "Sales - INV/1", dr=11800, ref="INV/1"),
        _se(1, "theirs", "2026-03-28", "Sales - INV/9", dr=23600, ref="INV/9"),
    ]
    ours = [_se(0, "ours", "2026-01-05", "Purchase - INV/1", cr=11800, ref="INV/1")]
    filed = [{"inv_no": "INV/1", "inv_val": 11800.0}]       # INV/9 never returned
    res = reconcile_statement(
        theirs, ours, today=D("2026-04-01"),
        their_meta={"opening_balance": 0.0, "closing_balance": 35400.0},
        our_meta={"opening_balance": 0.0, "closing_balance": 11800.0},
        gst_records=filed)
    chk = res["gst_check"]
    assert chk["checked"] == 2 and chk["agreed"] == 1
    assert chk["not_filed_count"] == 1
    assert chk["not_filed"][0]["ref"] == "INV/9"
    assert chk["not_filed_amount"] == 23600.0
    assert any(f["key"] == "not_filed" for f in res["findings"])


def test_statement_entry_classifier():
    from app.backend.statements import classify_entry
    cases = [
        ("Sales - MH/25-26/0441", "invoice"),
        ("Purchase - MH/25-26/0441", "invoice"),
        ("Receipt - NEFT", "payment"),
        ("Credit Note - rate difference", "credit_note"),
        ("Debit note for shortage", "debit_note"),
        ("Opening Balance", "opening"),
        ("TDS deducted u/s 194Q", "tds"),
    ]
    for text, expected in cases:
        got = classify_entry(text)
        assert got == expected, f"{text!r} read as {got}, expected {expected}"


def test_confirmation_letter_sets_out_the_items():
    from app.backend.statements import confirmation_letter, reconcile_statement
    theirs = [_se(0, "theirs", "2026-03-28", "Sales - INV/9", dr=23600, ref="INV/9")]
    res = reconcile_statement(
        theirs, [], today=D("2026-04-01"),
        their_meta={"opening_balance": 0.0, "closing_balance": 23600.0,
                    "party_name": "MU EXPORTS", "last_date": "2026-03-31"},
        our_meta={"opening_balance": 0.0, "closing_balance": 0.0})
    letter = confirmation_letter(res, entity={"entity_name": "TEST CO",
                                              "preparer": "A. Auditor"},
                                 contact={"email": "ap@mu.example"})
    assert "Confirmation of balance" in letter["subject"]
    assert "INV/9" in letter["body"]
    assert "TEST CO" in letter["body"]
    assert letter["mailto"].startswith("mailto:ap@mu.example?")


# ---------------------------------------------------------------------------
# document intake
# ---------------------------------------------------------------------------

def test_sniff_formats():
    from app.backend.docs import sniff
    assert sniff(b"%PDF-1.7 rest", "x.pdf") == "pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n", "x.png") == "image"
    assert sniff(b"\xff\xd8\xff\xe0", "x.jpg") == "image"
    assert sniff(b"Date,Narration,Amount\n01/01/2026,A,1", "x.csv") == "csv"
    assert sniff(b"\xd0\xcf\x11\xe0", "x.xls") == "xls_legacy"


def test_csv_grid_sniffs_delimiter():
    from app.backend.docs import read_csv_grid
    grid = read_csv_grid("Date;Narration;Amount\n01/01/2026;NEFT A;100\n".encode())
    assert grid[0] == ["Date", "Narration", "Amount"]
    assert grid[1][2] == "100"
    tab = read_csv_grid(b"a\tb\n1\t2\n")
    assert tab[0] == ["a", "b"]


def test_ocr_row_and_column_reconstruction():
    """Rebuild a table from recognised boxes, which arrive unordered."""
    from app.backend.docs import grid_from_lines, group_lines

    def box(text, x, y, w=70, h=14):
        return {"text": text, "conf": 0.9, "x0": x, "x1": x + w,
                "y0": y, "y1": y + h, "cy": y + h / 2, "h": h}

    boxes = [
        box("Balance", 600, 10), box("Date", 20, 10), box("Withdrawal", 400, 11),
        box("Narration", 150, 10),
        box("100.00", 400, 40), box("01/01/26", 20, 40),
        box("NEFT DR-ALPHA", 150, 41), box("900.00", 600, 40),
        box("02/01/26", 20, 70), box("NEFT DR-BETA", 150, 70),
        box("50.00", 400, 71), box("850.00", 600, 70),
    ]
    lines = group_lines(boxes)
    assert len(lines) == 3, [[b["text"] for b in ln] for ln in lines]
    # Within a row, boxes come back left to right whatever order they arrived.
    assert [b["text"] for b in lines[0]] == ["Date", "Narration", "Withdrawal", "Balance"]
    grid = grid_from_lines(lines)
    assert len(grid) == 3 and len(grid[0]) == 4
    assert grid[1] == ["01/01/26", "NEFT DR-ALPHA", "100.00", "900.00"]
    assert grid[2] == ["02/01/26", "NEFT DR-BETA", "50.00", "850.00"]


def test_grid_trimming_and_page_merge():
    from app.backend.docs import _merge_grids, _trim_grid
    grid = _trim_grid([["", "", ""], ["", "a", ""], ["", "b", ""], ["", "", ""]])
    assert grid == [["a"], ["b"]]
    merged = _merge_grids([[["Date", "Amt"], ["1", "10"]],
                           [["Date", "Amt"], ["2", "20"]]])
    assert merged == [["Date", "Amt"], ["1", "10"], ["2", "20"]]


def test_pages_share_one_column_layout():
    """Every page of a statement must be cut on the same columns.

    A header block on a later page can throw up one extra candidate edge. If
    each page learns its own edges, that page gains a column and stacking the
    pages shifts every figure sideways from there on - the reference lands in
    the withdrawal column and the deposit lands in the balance. The statement
    then stops tying, which is how this was found in the first place.
    """
    from app.backend.docs import grid_from_lines, learn_edges

    def box(x, text):
        return {"text": text, "x0": float(x), "x1": float(x + 90),
                "y0": 0.0, "y1": 20.0, "cy": 10.0, "h": 20.0, "conf": 0.9}

    # four real columns, at the same x on both pages
    page_one = [
        [box(100, "05/01/26"), box(400, "NEFT CR-ACME"), box(1200, "4471315"),
         box(1800, "396311.00")],
        [box(100, "06/01/26"), box(400, "CHQ PAID"), box(1200, "445044"),
         box(1800, "15880.00")],
    ]
    # page two carries a banner that starts at an x nothing else uses
    page_two = [
        [box(100, "Account 3810"), box(650, "IFSC SBIN0031456")],
        [box(100, "07/01/26"), box(400, "RTGS DR-ACME"), box(1200, "4472108"),
         box(1800, "471272.00")],
    ]

    alone_one = grid_from_lines(page_one)
    alone_two = grid_from_lines(page_two)
    assert len(alone_one[0]) != len(alone_two[1]), (
        "the banner should give page two a different column count on its own, "
        "otherwise this test is not exercising the bug")

    shared = learn_edges([ln for page in (page_one, page_two) for ln in page])
    grid_one = grid_from_lines(page_one, edges=shared)
    grid_two = grid_from_lines(page_two, edges=shared)

    # the amount must sit in the same column on both pages
    col_one = grid_one[0].index("396311.00")
    col_two = grid_two[1].index("471272.00")
    assert col_one == col_two, (
        f"amount moved from column {col_one} to {col_two} across pages")
    ref_one = grid_one[0].index("4471315")
    ref_two = grid_two[1].index("4472108")
    assert ref_one == ref_two, "the reference column moved across pages"


# ---------------------------------------------------------------------------
# reading real ledgers: layouts that turned up in the field
# ---------------------------------------------------------------------------

def test_period_read_from_the_heading():
    """Every heading style these documents actually use."""
    from app.backend.common import parse_period
    cases = [
        ("1-Apr-24 to 31-Mar-25", "2024-04-01", "2025-03-31"),
        ("FROM01/04/2024UPTO31/03/2025", "2024-04-01", "2025-03-31"),   # scan, spaces lost
        ("Period: 01.04.2024 - 31.03.2025", "2024-04-01", "2025-03-31"),
        ("1 April 2024 to 31 March 2025", "2024-04-01", "2025-03-31"),
    ]
    for text, want_from, want_to in cases:
        start, end = parse_period(text)
        assert iso(start) == want_from and iso(end) == want_to, f"{text!r} -> {start}, {end}"
    assert parse_period("F-315 SAMPLE ROAD 302022") == (None, None), \
        "an address is not a period"
    assert parse_period("Bal 12.5 - 13.5") == (None, None), "two numbers are not a period"


def test_tally_ledger_prefix_column_and_totals_row():
    """The layout Tally actually exports, which broke on three counts.

    To/By sits in the Particulars column and the ledger name in the unnamed
    one beside it; a voucher repeating the date above leaves it blank; and the
    grand total line puts the debit total in the date column. Read naively
    that loses the party on every row, drops a payment, and then invents an
    entry of sixty-one thousand.
    """
    from app.backend.statements import parse_statement

    grid = [
        ["KESARI TEXTILE MILLS"],
        ["SIGMA DYEING WORKS"],
        ["Ledger Account"],
        ["1-Apr-21 to 31-Mar-22"],
        ["Date", "Particulars", "", "Vch Type", "Vch No.", "Debit", "Credit"],
        ["1-Apr-21", "Dr", "Opening Balance", "", "", "", 240000],
        ["6-Apr-21", "Dr", "PURCHASE @18% GST", "PURCHASE (GST)", "7", "", 18500],
        ["14-Jun-21", "Cr", "IDBI BANK A/C", "Payment", "259", 200000, ""],
        ["", "Cr", "IDBI BANK A/C", "Payment", "260", 75000, ""],
        [58500, "", "", "", "", "", 58500],
    ]
    out = parse_statement(grid, side="ours")
    meta, entries = out["meta"], out["entries"]

    assert meta["ledger_name"] == "SIGMA DYEING WORKS", \
        f"the ledger is the party, not the company: {meta['ledger_name']!r}"
    assert meta["company"] == "KESARI TEXTILE MILLS"
    assert (meta["period_from"], meta["period_to"]) == ("2021-04-01", "2022-03-31")
    assert meta["opening_balance"] == 240000.0, meta["opening_balance"]

    assert len(entries) == 3, [e["particulars"] for e in entries]
    assert entries[0]["particulars"] == "PURCHASE @18% GST", \
        "the name beside the To/By prefix is the particulars"
    undated = entries[2]
    assert iso(undated["date"]) == "2021-06-14", \
        "a voucher with no date repeats the one above it"
    assert undated["owed"] == -75000.0
    assert all(abs(e["owed"]) != 58500 for e in entries), \
        "the grand total line is not an entry"
    # 240000 owed at the start + 18500 purchased - 275000 paid = 16500 overpaid
    assert meta["closing_balance"] == -16500.0, meta["closing_balance"]


def test_money_column_ignores_text():
    """A "Page 2" footer in a money column is not two rupees."""
    from app.backend.statements import parse_statement

    grid = [
        ["Date", "Particulars", "Debit", "Credit"],
        ["1-Apr-21", "Opening Balance", "1,000.00", ""],
        ["2-Apr-21", "SALES GST", "500.00", ""],
        ["KESARI", "TEXTILE MILLS Ledger", "", "Page 2"],
    ]
    out = parse_statement(grid, side="theirs")
    assert out["meta"]["count"] == 1, [e["particulars"] for e in out["entries"]]
    assert out["meta"]["closing_balance"] == 1500.0, out["meta"]["closing_balance"]


def test_statement_single_amount_with_drcr_column():
    """One Amount column and a Dr/Cr flag beside it, as many packages print.

    The "dr" in the heading "Dr/Cr" took that column for Debit, so every
    amount - the payment and the credit note too - read as a debit, and the
    balance came out 82,360 wrong.
    """
    from app.backend.statements import parse_statement

    grid = [
        ["DUNGARPUR FABRICATORS"],
        ["Statement of account: KESARI TEXTILE MILLS"],
        ["Date", "Particulars", "Ref No", "Amount", "Dr/Cr", "Balance", "Dr/Cr"],
        ["01/04/2025", "Opening Balance", "", "25000.00", "Dr", "25000.00", "Dr"],
        ["10/04/2025", "Sales - DF/101", "DF/101", "11800.00", "Dr", "36800.00", "Dr"],
        ["25/04/2025", "Receipt - NEFT", "UTR88", "40000.00", "Cr", "3200.00", "Cr"],
        ["30/04/2025", "Credit Note - rate diff", "CN/7", "1180.00", "Cr", "4380.00", "Cr"],
        ["", "Closing Balance", "", "4380.00", "Cr", "", ""],
    ]
    out = parse_statement(grid, side="theirs")
    meta, entries = out["meta"], out["entries"]
    assert [e["owed"] for e in entries] == [11800.0, -40000.0, -1180.0], \
        [(e["debit"], e["credit"]) for e in entries]
    assert meta["opening_balance"] == 25000.0, meta["opening_balance"]
    # 25000 owed + 11800 billed - 40000 received - 1180 credited = 4380 overpaid
    assert meta["closing_balance"] == -4380.0, meta["closing_balance"]
    assert entries[-1]["balance"] == -4380.0, "the balance's own Dr/Cr is its nature"
    assert meta["stated_closing"] == -4380.0 and meta["closing_agrees"] is True
    assert not any("balance_note" in e["flags"] for e in entries)

    # "Cr/Dr" with one-letter flags, from our side of the same account
    ours = parse_statement([
        ["Date", "Particulars", "Amount", "Cr/Dr"],
        ["10/04/2025", "Purchase - DF/101", "11800", "C"],
        ["25/04/2025", "HDFC Bank - NEFT", "40000", "D"],
    ], side="ours")
    assert [e["owed"] for e in ours["entries"]] == [11800.0, -40000.0]


def test_two_sided_interest_statement():
    """
    Debits on the left of the page, credits on the right.

    Read as one flat table only the left half survives, so a statement showing
    two repayments came through as three advances and nothing else - the
    balance then moved the wrong way by the whole of both. The interest and
    the TDS printed underneath are entries too, and their arithmetic is
    checkable: product = amount x days / basis, interest = product x rate.
    """
    from app.backend.statements import parse_statement

    grid = [
        ["OMEGA TRADING COMPANY"],
        ["STATEMENT FOR INTEREST @13.80% P.A. Credit Days :", "0.00"],
        ["LOAN ACCOUNT", "", "", "", "", "FROM 01/04/2024 UPTO 31/03/2025"],
        ["", "", "DEBIT", "", "", "", "", "CREDIT", "", ""],
        [".DATE", "DESCRIPTION", "AMOUNT", "DAYS", "PRODUCT",
         "DATE", "DESC.", "AMOUNT", "DAYS", "PRODUCT"],
        ["01/04/24", "#", "1500000.00", "365", "1520833.33",
         "16/04/24", "HDFC RTGS", "1500000.00", "350", "1458333.33"],
        ["28/10/24", "RTGS", "1000000.00", "155", "430555.56",
         "20/11/24", "RTGS", "1000000.00", "132", "366666.67"],
        ["11/12/24", "RTGS", "1000000.00", "111", "308333.33", "", "", "", "", ""],
        ["", "", "3500000.00", "", "2259722.22", "", "", "2500000.00", "", "1825000.00"],
        ["Debit Product", "", "434722.22"],
        ["Interest Receivable", "", "", "59992.00"],
        ["TDS @ 10%", "", "", "6000.00"],
        ["DR Cur. Bal in Lgr", "", "1000000.00"],
    ]
    out = parse_statement(grid, side="theirs")
    meta, entries = out["meta"], out["entries"]

    assert meta["document"] == "interest_statement"
    assert meta["two_sided"] is True
    assert meta["opening_balance"] == 1500000.0, "the first line is the balance carried in"

    moves = [e for e in entries if e["kind"] == "payment"]
    assert len(moves) == 4, [(iso(e["date"]), e["owed"]) for e in moves]
    assert sum(1 for e in moves if e["owed"] < 0) == 2, \
        "both repayments on the credit side must survive"
    assert sum(e["owed"] for e in moves) == -500000.0

    terms = meta["interest"]
    assert terms["rate"] == 13.8 and terms["tds_rate"] == 10.0
    assert terms["interest"] == 59992.0 and terms["tds"] == 6000.0
    assert terms["principal_balance"] == 1000000.0, \
        "the entries must reach the ledger balance the statement prints"

    check = terms["check"]
    assert check["ok"], check["problems"]
    assert check["basis"] == 360, check["basis"]
    assert abs(check["interest_expected"] - 59992.0) <= 1.0, check["interest_expected"]

    charges = {e["kind"]: e["owed"] for e in entries if e["kind"] in ("interest", "tds")}
    assert charges == {"interest": 59992.0, "tds": -6000.0}, charges


def test_misread_interest_figure_is_caught():
    """A figure misread from a scan breaks the statement's own arithmetic."""
    from app.backend.statements import parse_statement

    def build(product):
        # advanced 05/04/24 for 360 days, part repaid 31/12/24 for 90.
        # net product 1200000 - 50000 = 1150000, at 12% = 138000.
        return [
            ["LENDER LTD"],
            ["STATEMENT FOR INTEREST @12.00% P.A."],
            ["", "", "", "", "", "FROM 01/04/2024 UPTO 31/03/2025"],
            ["", "", "DEBIT", "", "", "", "", "CREDIT", "", ""],
            ["DATE", "DESC.", "AMOUNT", "DAYS", "PRODUCT",
             "DATE", "DESC.", "AMOUNT", "DAYS", "PRODUCT"],
            ["05/04/24", "RTGS", "1200000.00", "360", product,
             "31/12/24", "RTGS", "200000.00", "90", "50000.00"],
            ["Interest Receivable", "", "", "138000.00"],
        ]

    good = parse_statement(build("1200000.00"), side="theirs")["meta"]["interest"]
    assert good["check"]["ok"], good["check"]["problems"]

    bad = parse_statement(build("1100000.00"), side="theirs")["meta"]["interest"]
    assert not bad["check"]["ok"], "a product that does not follow must be reported"
    assert any("product" in p for p in bad["check"]["problems"]), bad["check"]["problems"]


def test_interest_compared_as_a_period_total():
    """
    Each side books interest on its own schedule, so only the totals compare.

    Matching entry by entry leaves both sides looking one-sided, and the
    difference then has no explanation attached to it.
    """
    from app.backend.statements import reconcile_statement

    theirs = [_se(0, "theirs", "2025-03-31", "Interest @ 13.8%", dr=59992.0)]
    theirs[0]["kind"] = "interest"
    ours = [_se(0, "ours", "2024-04-30", "Interest Expenses", cr=8625.0),
            _se(1, "ours", "2025-03-24", "Interest Expenses", cr=48684.0)]
    for o in ours:
        o["kind"] = "interest"

    res = reconcile_statement(theirs, ours, their_meta={"interest": {"rate": 13.8}})
    rows = [r for r in res["all_records"] if "period_total" in r["flags"]]
    assert len(rows) == 1, res["all_records"]
    row = rows[0]
    assert row["owed_theirs"] == 59992.0 and row["owed_ours"] == 57309.0
    assert row["difference"] == 2683.0
    assert row["status"] == "Amount disputed"
    assert res["counts"]["their_only"] == 0 and res["counts"]["our_only"] == 0, \
        "the two sides must not both be reported as one-sided"


def test_pdf_ledger_columns_follow_the_heading():
    """
    A ledger printed without ruling lines is cut on its heading row.

    Learning the columns from the body splits "SALES GST" into two columns,
    and a right-aligned money column starts far left of its own heading, so
    placing a figure by its left edge files it under the column before.
    """
    from app.backend.docs import grid_from_lines, header_spans

    def box(x0, x1, text, y):
        return {"text": text, "x0": float(x0), "x1": float(x1), "y0": float(y),
                "y1": float(y + 10), "cy": float(y + 5), "h": 10.0, "conf": 1.0}

    lines = [
        [box(62, 82, "Date", 0), box(112, 160, "Particulars", 0),
         box(237, 253, "Vch", 0), box(256, 276, "Type", 0),
         box(370, 386, "Vch", 0), box(389, 403, "No.", 0),
         box(466, 489, "Debit", 0), box(546, 573, "Credit", 0)],
        [box(63, 96, "1-Apr-21", 20), box(102, 113, "To", 20),
         box(119, 150, "SALES", 20), box(153, 172, "GST", 20),
         box(237, 259, "Sales", 20), box(402, 407, "2", 20),
         box(443, 484, "32,450.00", 20)],
        [box(44, 80, "1-Apr-21", 40), box(88, 99, "To", 40),
         box(117, 155, "Opening", 40), box(157, 193, "Balance", 40),
         box(434, 483, "3,88,146.00", 40)],
        [box(129, 162, "Brought", 60), box(164, 198, "Forward", 60),
         box(428, 482, "16,41,500.00", 60), box(512, 566, "16,41,500.00", 60)],
    ]
    spans = header_spans(lines)
    assert spans and len(spans) == 6, spans
    grid = grid_from_lines(lines, spans=spans, assign="center")

    assert grid[0] == ["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"]
    assert grid[1][1] == "To SALES GST", f"two words, one column: {grid[1][1]!r}"
    assert grid[1][2] == "Sales" and grid[1][3] == "2"
    assert grid[1][4] == "32,450.00", f"a right-aligned figure belongs to Debit: {grid[1]}"
    assert grid[2][1] == "Opening Balance", grid[2]
    assert grid[3][4] == "16,41,500.00" and grid[3][5] == "16,41,500.00", grid[3]


def test_ocr_reads_a_short_figure_the_right_way_up():
    """A short amount must not come back rotated.

    The per-line angle classifier once flipped "6,063.00" to "00'E90'9", which
    broke the running balance of a scanned statement. It is switched off; this
    renders the same figure and reads it back.
    """
    from app.backend.docs import get_ocr, ocr_available, ocr_page_image
    if not ocr_available():
        return
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (700, 140), "white")       # a page has margins
    ImageDraw.Draw(img).text((80, 40), "6,063.00", fill="black",
                             font=ImageFont.load_default(size=44))
    assert get_ocr().use_angle_cls is False, "the angle classifier is back on"
    text = ocr_page_image(img)["text"].replace(" ", "")
    assert "6,063.00" in text or "6063.00" in text, f"read as {text!r}"


def test_pages_keep_their_columns_when_one_is_empty():
    """Imposed columns are kept even where a page never fills them.

    Dropping the columns that happen to be empty on one page undoes the
    shared layout, and stacking the pages shifts every figure again.
    """
    from app.backend.docs import grid_from_lines

    def box(x, text):
        return {"text": text, "x0": float(x), "x1": float(x + 40), "y0": 0.0,
                "y1": 10.0, "cy": 5.0, "h": 10.0, "conf": 1.0}

    edges = [50.0, 200.0, 350.0, 500.0]
    only_credits = [[box(50, "05/01/26"), box(200, "NEFT"), box(500, "900.00")]]
    grid = grid_from_lines(only_credits, edges=edges)
    assert len(grid[0]) == 4, f"an empty column was dropped: {grid[0]}"
    assert grid[0][3] == "900.00"


# ---------------------------------------------------------------------------
# the PDF working papers
# ---------------------------------------------------------------------------

def test_pdf_money_keeps_its_rupee_sign():
    """Every ₹ in the working papers printed as a black box.

    They were set in Helvetica, reportlab's own face, which has neither the
    rupee sign nor the minus sign inr() writes. The GST, bank and party PDFs
    all take their font, styles and tables from exports.py.
    """
    import io

    import pdfplumber
    from reportlab.platypus import SimpleDocTemplate

    from app.backend.common import inr
    from app.backend.exports import FONT, Paragraph, _styles, _table

    buf = io.BytesIO()
    SimpleDocTemplate(buf).build([
        Paragraph(f"<b>Short</b> by {inr(-1234.5)}", _styles()["body"]),
        _table([["Line", "Amount"], ["Interest", inr(2500)]], [200, 100]),
    ])
    with pdfplumber.open(io.BytesIO(buf.getvalue())) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        fonts = {c["fontname"].split("+")[-1] for c in page.chars}
    if FONT == "Helvetica":             # no Segoe UI here: the sign is spelt out
        assert "-Rs.1,234.50" in text and "Rs.2,500.00" in text, text
    else:
        assert "−₹1,234.50" in text and "₹2,500.00" in text, text
        assert fonts == {"SegoeUI", "SegoeUI-Bold"}, fonts
