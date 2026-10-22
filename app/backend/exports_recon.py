"""
Excel, PDF and CSV output for the bank and party-statement reconciliations.

Both produce a working paper whose first sheet is the reconciliation statement
itself, because that is the page that gets signed. The supporting schedules sit
behind it in the order a reviewer walks them: what agreed, what did not, and
what only one side has.
"""

import csv
import io
from datetime import datetime

import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, TableStyle

from app.backend.common import inr, iso
from app.backend.excel_generator import (
    ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BORDER_THIN, BORDER_TOTAL, CURRENCY,
    DATE_FMT, FILL_HEADER, FONT_DATA, FONT_NOTE, FONT_SECTION_HDR,
    FONT_SECTION_TITLE, FONT_SHEET_TITLE, FONT_SUBTITLE, FONT_TBL_HDR,
    FONT_TOTAL, INTEGER, PERCENT, STATUS_FILL, TONE_FILL, _as_date,
    auto_fit_columns, format_cell_value,
)
from app.backend.exports import BOLD, Paragraph, _footer, _styles, _table
from app.backend.explain import why_line

# Status tints for the two new vocabularies, reusing the same four fills.
RECON_FILL = {
    "Matched": STATUS_FILL["Matched"],
    "Agreed": STATUS_FILL["Matched"],
    "Timing difference": STATUS_FILL["Invoice no. mismatch"],
    "Batched": STATUS_FILL["Invoice no. mismatch"],
    "Amount differs": STATUS_FILL["Value mismatch"],
    "Amount disputed": STATUS_FILL["Value mismatch"],
}


def _sheet(wb, title, heading, headers, rows, formats, sum_cols=None,
           status_col=None, tone_col=None, wrap_cols=(), autofilter=True):
    """The same data-sheet shape the GST workbook uses."""
    ws = wb.create_sheet(title=title[:31])
    ws.cell(1, 1, heading).font = FONT_SECTION_TITLE
    ws.row_dimensions[3].height = 24.0
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(3, c, h)
        cell.font = FONT_TBL_HDR
        cell.fill = FILL_HEADER
        low = h.lower()
        if "date" in low or "status" in low or "period" in low:
            cell.alignment = ALIGN_CENTER
        elif formats.get(c) in (CURRENCY, INTEGER, PERCENT):
            cell.alignment = ALIGN_RIGHT
        else:
            cell.alignment = ALIGN_LEFT

    r = 4
    for row in rows:
        fill = None
        if status_col and status_col <= len(row):
            fill = RECON_FILL.get(row[status_col - 1]) or STATUS_FILL.get(row[status_col - 1])
        elif tone_col and tone_col <= len(row):
            fill = TONE_FILL.get(row[tone_col - 1])
        for c, val in enumerate(row, start=1):
            cell = ws.cell(r, c, format_cell_value(_as_date(val)))
            cell.font = FONT_DATA
            cell.border = BORDER_THIN
            if fill and c == (status_col or tone_col):
                cell.fill = fill
            nfmt = formats.get(c)
            if nfmt:
                cell.number_format = nfmt
                if nfmt in (CURRENCY, INTEGER, PERCENT):
                    cell.alignment = ALIGN_RIGHT
                elif nfmt == DATE_FMT:
                    cell.alignment = ALIGN_CENTER
            elif isinstance(cell.value, (int, float)):
                cell.number_format = CURRENCY
                cell.alignment = ALIGN_RIGHT
            else:
                cell.alignment = ALIGN_LEFT
        r += 1

    if sum_cols and rows:
        from openpyxl.utils import get_column_letter
        tot = r
        first = min(sum_cols)
        ws.cell(tot, max(1, first - 1) if first > 1 else 1, "Total").font = FONT_TOTAL
        for c in range(1, len(headers) + 1):
            cell = ws.cell(tot, c)
            cell.border = BORDER_TOTAL
            if c in sum_cols:
                letter = get_column_letter(c)
                cell.value = f"=SUM({letter}4:{letter}{tot - 1})"
                cell.font = FONT_TOTAL
                cell.number_format = formats.get(c, CURRENCY)
                cell.alignment = ALIGN_RIGHT

    if autofilter and rows:
        from openpyxl.utils import get_column_letter
        ws.auto_filter.ref = f"A3:{get_column_letter(len(headers))}{3 + len(rows)}"
    ws.freeze_panes = "A4"
    auto_fit_columns(ws)
    del wrap_cols
    return ws


def _statement_sheet(wb, title, heading, subtitle, lines, ties, residual):
    """The reconciliation statement itself, laid out as a working paper."""
    ws = wb.create_sheet(title=title[:31])
    ws["A1"] = heading
    ws["A1"].font = FONT_SHEET_TITLE
    ws["A2"] = subtitle
    ws["A2"].font = FONT_SUBTITLE
    ws["A3"] = f"Generated {datetime.now().strftime('%d %b %Y at %H:%M')}"
    ws["A3"].font = FONT_NOTE

    row = 5
    for line in lines:
        label = ws.cell(row, 1, line["label"])
        amount = ws.cell(row, 3, line["amount"])
        amount.number_format = CURRENCY
        amount.alignment = ALIGN_RIGHT
        if line.get("count"):
            c = ws.cell(row, 2, line["count"])
            c.number_format = INTEGER
            c.alignment = ALIGN_RIGHT
            c.font = FONT_NOTE
        if line["kind"] in ("opening", "closing"):
            label.font = FONT_SECTION_HDR
            amount.font = FONT_TOTAL
            for col in (1, 2, 3):
                ws.cell(row, col).border = BORDER_TOTAL
        else:
            label.font = FONT_DATA
            amount.font = FONT_DATA
            if line.get("tone") == "risk":
                label.fill = amount.fill = TONE_FILL["risk"]
        row += 1

    row += 1
    ws.cell(row, 1, "Ties" if ties else "DOES NOT TIE").font = FONT_SECTION_HDR
    c = ws.cell(row, 3, residual)
    c.number_format = CURRENCY
    c.alignment = ALIGN_RIGHT
    c.font = FONT_TOTAL
    if not ties:
        ws.cell(row, 1).fill = c.fill = TONE_FILL["risk"]
    row += 2
    ws.cell(row, 1, "Every line above is derived from the movements themselves, "
                    "not from a checklist, so a residual here means a document "
                    "was read wrongly rather than that an item was forgotten.")
    ws.cell(row, 1).font = FONT_NOTE

    for letter, width in (("A", 62.0), ("B", 10.0), ("C", 20.0)):
        ws.column_dimensions[letter].width = width
    return ws


# ---------------------------------------------------------------------------
# Bank workbook
# ---------------------------------------------------------------------------

def build_bank_excel(result, output=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    bm = result.get("bank_meta") or {}
    bk = result.get("book_meta") or {}
    subtitle = "  |  ".join(p for p in [
        bm.get("bank") or "Bank",
        f"A/c {bm['account']}" if bm.get("account") else "",
        f"{bm.get('first_date', '')} to {bm.get('last_date', '')}",
    ] if p)

    _statement_sheet(wb, "BRS", "Bank reconciliation statement", subtitle,
                     result["brs"]["lines"], result["brs"]["ties"],
                     result["brs"]["residual"])

    recs = result["all_records"]
    headers = ["Status", "Category", "Direction", "Date (bank)", "Date (book)",
               "Narration", "Channel", "Counterparty", "Bank ref",
               "Ledger (Tally)", "Vch No.", "Vch Type",
               "Amount (bank)", "Amount (book)", "Difference", "Days apart",
               "Age (days)", "Flags", "Basis", "Why it is open"]
    formats = {4: DATE_FMT, 5: DATE_FMT, 13: CURRENCY, 14: CURRENCY,
               15: CURRENCY, 16: INTEGER, 17: INTEGER}
    rows = [[
        r["status"], r["category"], r["direction"], r["date_bank"], r["date_book"],
        r["narration"], r["channel"], r["counterparty"], r["bank_ref"],
        r["book_party"], r["book_vch_no"], r["book_vch_type"],
        r["bank_amount"], r["book_amount"], r["difference"],
        r["date_gap_days"], r.get("age_days"),
        ", ".join(r["flags"]), r["match_basis"], why_line(r),
    ] for r in recs]
    _sheet(wb, "All items", "Every bank line and book voucher, matched and unmatched",
           headers, rows, formats, sum_cols=[13, 14, 15], status_col=1, wrap_cols=(20,))

    for key, title, heading in (
        ("unpresented", "Unpresented cheques",
         "Cheques issued but not yet presented at the bank"),
        ("uncredited", "Uncredited deposits",
         "Deposits recorded in the books but not yet credited by the bank"),
        ("bank_charges", "Charges not booked",
         "Bank charges and the tax on them, debited by the bank and not in the books"),
        ("bank_debits", "Bank debits not booked",
         "Standing instructions, loan repayments and withdrawals absent from the books"),
        ("bank_credits", "Bank credits not booked",
         "Amounts credited by the bank with no entry in the books"),
        ("bank_interest", "Interest not booked",
         "Interest credited by the bank and not in the books"),
    ):
        group = next((g for g in result["brs"]["groups"] if g["key"] == key), None)
        if not group:
            continue
        _sheet(wb, title, heading,
               ["Date", "Particulars", "Reference", "Direction", "Channel",
                "Amount", "Age (days)", "Flags"],
               [[r["date"], r["particulars"], r["ref"], r["direction"],
                 r["channel"], r["amount"], r.get("age_days"),
                 ", ".join(r["flags"])] for r in group["rows"]],
               {1: DATE_FMT, 6: CURRENCY, 7: INTEGER}, sum_cols=[6])

    if result.get("findings"):
        _sheet(wb, "Findings", "What needs attention, most serious first",
               ["Severity", "Finding", "Items", "Amount", "What to do"],
               [[f["tone"], f["title"], f["count"], f["amount"], f["detail"]]
                for f in result["findings"]],
               {3: INTEGER, 4: CURRENCY}, tone_col=1, autofilter=False)

    if result.get("channels"):
        _sheet(wb, "By channel", "How the money moved",
               ["Channel", "Transactions", "Withdrawn", "Deposited"],
               [[c["channel"], c["count"], c["debit"], c["credit"]]
                for c in result["channels"]],
               {2: INTEGER, 3: CURRENCY, 4: CURRENCY}, sum_cols=[3, 4],
               autofilter=False)

    if result.get("monthly"):
        _sheet(wb, "Monthly", "Month by month, bank against books",
               ["Month", "Bank in", "Bank out", "Bank net", "Books in",
                "Books out", "Books net", "Bank lines", "Book entries"],
               [[m["month"], m["bank_in"], m["bank_out"], m["net_bank"],
                 m["book_in"], m["book_out"], m["net_book"],
                 m["bank_n"], m["book_n"]] for m in result["monthly"]],
               {2: CURRENCY, 3: CURRENCY, 4: CURRENCY, 5: CURRENCY,
                6: CURRENCY, 7: CURRENCY, 8: INTEGER, 9: INTEGER},
               sum_cols=[2, 3, 4, 5, 6, 7], autofilter=False)

    _sheet(wb, "Sources", "What was read",
           ["Side", "Detail", "Value"],
           [["Bank statement", "File", bm.get("source_file", "")],
            ["Bank statement", "Bank", bm.get("bank", "")],
            ["Bank statement", "Account", bm.get("account", "")],
            ["Bank statement", "IFSC", bm.get("ifsc", "")],
            ["Bank statement", "Transactions", bm.get("count")],
            ["Bank statement", "Opening balance", bm.get("opening_balance")],
            ["Bank statement", "Closing balance", bm.get("closing_balance")],
            ["Bank statement", "Running balance ties", bm.get("continuity_ok")],
            ["Bank book", "File", bk.get("source_file", "")],
            ["Bank book", "Vouchers", bk.get("count")],
            ["Bank book", "Opening balance", bk.get("opening_balance")],
            ["Bank book", "Closing balance", bk.get("closing_balance")]],
           {}, autofilter=False)

    if output is None:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
    wb.save(output)
    return output


# ---------------------------------------------------------------------------
# Party statement workbook
# ---------------------------------------------------------------------------

def build_statement_excel(result, output=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    tm = result.get("their_meta") or {}
    om = result.get("our_meta") or {}
    subtitle = "  |  ".join(p for p in [
        tm.get("party_name") or "Party",
        f"GSTIN {tm['gstin']}" if tm.get("gstin") else "",
        f"{tm.get('first_date', '')} to {tm.get('last_date', '')}",
    ] if p)

    _statement_sheet(wb, "Bridge", "Statement reconciliation", subtitle,
                     result["bridge"]["lines"], result["bridge"]["ties"],
                     result["bridge"]["residual"])

    headers = ["Status", "Category", "Kind", "Date (theirs)", "Date (ours)",
               "Reference (theirs)", "Reference (ours)",
               "Particulars (theirs)", "Particulars (ours)",
               "Amount (theirs)", "Amount (ours)", "Difference",
               "Days apart", "Age (days)", "Ageing", "Flags", "Why it is open"]
    formats = {4: DATE_FMT, 5: DATE_FMT, 10: CURRENCY, 11: CURRENCY,
               12: CURRENCY, 13: INTEGER, 14: INTEGER}
    rows = [[
        r["status"], r["category"], r["kind"], r["date_theirs"], r["date_ours"],
        r["ref_theirs"], r["ref_ours"], r["particulars_theirs"],
        r["particulars_ours"], r["owed_theirs"], r["owed_ours"], r["difference"],
        r["date_gap_days"], r.get("age_days"), r.get("aging_bucket"),
        ", ".join(r["flags"]), why_line(r),
    ] for r in result["all_records"]]
    _sheet(wb, "All items", "Every entry on both sides, matched and unmatched",
           headers, rows, formats, sum_cols=[10, 11, 12], status_col=1, wrap_cols=(17,))

    for group in result["bridge"]["groups"]:
        _sheet(wb, group["label"][:31], group["label"],
               ["Date", "Reference", "Particulars", "Kind", "Amount",
                "Age (days)", "Ageing"],
               [[r["date"], r["ref"], r["particulars"], r["kind"], r["amount"],
                 r.get("age_days"), r.get("aging_bucket")] for r in group["rows"]],
               {1: DATE_FMT, 5: CURRENCY, 6: INTEGER}, sum_cols=[5])

    if result.get("findings"):
        _sheet(wb, "Findings", "What needs attention, most serious first",
               ["Severity", "Finding", "Items", "Amount", "What to do"],
               [[f["tone"], f["title"], f["count"], f["amount"], f["detail"]]
                for f in result["findings"]],
               {3: INTEGER, 4: CURRENCY}, tone_col=1, autofilter=False)

    chk = result.get("gst_check")
    if chk:
        _sheet(wb, "Against GSTR-2B",
               "Every invoice on their statement, checked against what they filed",
               ["Check", "Count", "Amount"],
               [["Invoices checked", chk["checked"], None],
                ["Agreed with the return", chk["agreed"], None],
                ["Billed but never filed", chk["not_filed_count"],
                 chk["not_filed_amount"]],
                ["Filed at a different value", chk["value_differs_count"], None]],
               {2: INTEGER, 3: CURRENCY}, autofilter=False)
        if chk.get("not_filed"):
            _sheet(wb, "Billed not filed",
                   "On their statement, absent from GSTR-2B - no credit until they file",
                   ["Reference", "Date", "Amount on statement"],
                   [[x["ref"], x["date"], x["statement_amount"]]
                    for x in chk["not_filed"]],
                   {2: DATE_FMT, 3: CURRENCY}, sum_cols=[3])
        if chk.get("value_differs"):
            _sheet(wb, "Filed differently",
                   "Filed at a different value from the statement they sent",
                   ["Reference", "Date", "On statement", "As filed", "Difference"],
                   [[x["ref"], x["date"], x["statement_amount"],
                     x["filed_amount"], x["difference"]]
                    for x in chk["value_differs"]],
                   {2: DATE_FMT, 3: CURRENCY, 4: CURRENCY, 5: CURRENCY},
                   sum_cols=[3, 4, 5])

    if result.get("aging"):
        _sheet(wb, "Ageing", "How long each open difference has been open",
               ["Bucket", "Items", "Amount"],
               [[a["bucket"], a["count"], a["amount"]] for a in result["aging"]],
               {2: INTEGER, 3: CURRENCY}, sum_cols=[3], autofilter=False)

    _sheet(wb, "Sources", "What was read",
           ["Side", "Detail", "Value"],
           [["Their statement", "File", tm.get("source_file", "")],
            ["Their statement", "Party", tm.get("party_name", "")],
            ["Their statement", "GSTIN", tm.get("gstin") or ""],
            ["Their statement", "Entries", tm.get("count")],
            ["Their statement", "Closing balance", tm.get("closing_balance")],
            ["Their statement", "Balance they state", tm.get("stated_closing")],
            ["Our ledger", "File", om.get("source_file", "")],
            ["Our ledger", "Entries", om.get("count")],
            ["Our ledger", "Closing balance", om.get("closing_balance")]],
           {}, autofilter=False)

    if output is None:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
    wb.save(output)
    return output


# ---------------------------------------------------------------------------
# PDF working papers
# ---------------------------------------------------------------------------

def _brs_pdf_lines(S, W, lines, ties, residual):
    rows = [["", "Amount"]]
    style_extra = []
    for line in lines:
        label = line["label"] + (f"   ({line['count']})" if line.get("count") else "")
        rows.append([Paragraph(label, S["cell"]), inr(line["amount"])])
        if line["kind"] in ("opening", "closing"):
            idx = len(rows) - 1
            style_extra += [
                ("FONTNAME", (0, idx), (-1, idx), BOLD),
                ("LINEABOVE", (0, idx), (-1, idx), 0.7, colors.black),
            ]
        elif line.get("tone") == "risk":
            idx = len(rows) - 1
            style_extra.append(("TEXTCOLOR", (0, idx), (-1, idx),
                                colors.HexColor("#A8342A")))
    t = _table(rows, [W * 0.66, W * 0.34], align_right=(1,), font_size=8.8, band=False)
    t.setStyle(TableStyle(style_extra))
    out = [t, Spacer(1, 3 * mm)]
    out.append(Paragraph(
        ("<b>The statement ties.</b> Every rupee of the difference is named above."
         if ties else
         f"<b>The statement does not tie.</b> {inr(residual)} is unexplained, which "
         f"means a document was read wrongly rather than that an item was forgotten."),
        S["body"]))
    return out


def build_bank_pdf(result, entity=None):
    S = _styles()
    entity = entity or {}
    bm = result.get("bank_meta") or {}
    bk = result.get("book_meta") or {}
    name = entity.get("entity_name") or "Entity"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title=f"Bank reconciliation - {name}", author="Ultimate Accountant")
    W = A4[0] - 30 * mm
    flow = [Paragraph("Bank reconciliation statement", S["title"])]
    flow.append(Paragraph(" &nbsp;·&nbsp; ".join(p for p in [
        name, bm.get("bank") or "",
        f"A/c {bm['account']}" if bm.get("account") else "",
        f"{bm.get('first_date', '')} to {bm.get('last_date', '')}"] if p), S["sub"]))
    flow.append(Paragraph(
        f"Prepared {datetime.now().strftime('%d %b %Y at %H:%M')}"
        + (f" by {entity['preparer']}" if entity.get("preparer") else ""), S["small"]))
    flow.append(Spacer(1, 6 * mm))

    c = result["counts"]
    flow.append(Paragraph(
        f"{c['bank_txns']:,} statement lines were compared with {c['book_entries']:,} "
        f"bank book vouchers. {c['matched']:,} agree outright"
        + (f", {c['batched']:,} as a batched deposit" if c["batched"] else "")
        + (f", and {c['amount_diff']:,} carry a different amount" if c["amount_diff"] else "")
        + f". {c['bank_only']:,} lines are on the statement only and {c['book_only']:,} "
        f"vouchers are in the books only.", S["lead"]))

    flow.append(Paragraph("The reconciliation", S["h2"]))
    flow += _brs_pdf_lines(S, W, result["brs"]["lines"], result["brs"]["ties"],
                           result["brs"]["residual"])

    ch = result.get("charges") or {}
    if ch.get("total"):
        flow.append(Paragraph("Charges the books do not carry", S["h2"]))
        flow.append(Paragraph(
            f"The bank took {inr(ch['bank_charges'])} in charges and "
            f"{inr(ch['gst_on_charges'])} of tax on them. The charge is an expense to "
            f"book; the tax is input credit that will be sitting in GSTR-2B against "
            f"the bank's GSTIN, and it cannot be claimed until the entry exists.",
            S["body"]))

    if result.get("findings"):
        flow.append(Paragraph("What needs attention", S["h2"]))
        rows = [["Severity", "Finding", "Items", "Amount"]]
        for f in result["findings"]:
            rows.append([f["tone"].title(),
                         Paragraph(f"<b>{f['title']}</b><br/>"
                                   f"<font color='#5A6672'>{f['detail']}</font>", S["cell"]),
                         f"{f['count']:,}", inr(f["amount"])])
        flow.append(_table(rows, [W * 0.10, W * 0.62, W * 0.12, W * 0.16],
                           align_right=(2, 3)))

    flow.append(PageBreak())
    for key, heading in (("unpresented", "Cheques issued but not yet presented"),
                         ("uncredited", "Deposits made but not yet credited"),
                         ("bank_charges", "Charges and tax debited, not in the books"),
                         ("bank_debits", "Other bank debits not in the books"),
                         ("bank_credits", "Bank credits with no entry"),
                         ("bank_interest", "Interest credited, not in the books")):
        group = next((g for g in result["brs"]["groups"] if g["key"] == key), None)
        if not group:
            continue
        flow.append(Paragraph(heading, S["h2"]))
        rows = [["Date", "Particulars", "Reference", "Amount", "Age"]]
        for r in group["rows"][:40]:
            rows.append([_d(r["date"]),
                         Paragraph(str(r["particulars"] or "")[:64], S["cell"]),
                         str(r["ref"] or ""), inr(r["amount"]),
                         f"{r['age_days']}d" if r.get("age_days") is not None else ""])
        rows.append(["", "Total", "", inr(group["amount"]), ""])
        t = _table(rows, [W * 0.12, W * 0.46, W * 0.16, W * 0.18, W * 0.08],
                   align_right=(3,))
        t.setStyle(TableStyle([
            ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), BOLD),
            ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.7, colors.black),
        ]))
        flow.append(t)
        if len(group["rows"]) > 40:
            flow.append(Paragraph(f"and {len(group['rows']) - 40} more - see the "
                                  f"workbook", S["small"]))

    flow.append(Paragraph("How the matching was done", S["h2"]))
    cfg = result.get("config") or {}
    for i, text in enumerate([
        "Reference numbers are compared first: a cheque number or a UTR appearing "
        "on both sides, with the amount agreeing, is a match with no further test.",
        f"Then amount and date, inside {cfg.get('date_window_days', 7)} days, with the "
        f"name pulled out of the narration used to rank the candidates.",
        f"Then the same amount up to {cfg.get('wide_date_window_days', 45)} days apart, "
        f"reported as a timing difference rather than a match.",
        "Then the same party and date with a different amount, reported as an "
        "amount difference so the figure can be corrected.",
        "Then one statement line against several book vouchers, for a batched "
        "deposit or a split payment.",
        "Whatever is left is one-sided and appears in the schedules above.",
        "A payment is never matched against a receipt, however well the amounts "
        "agree.",
    ], start=1):
        flow.append(Paragraph(f"{i}. {text}", S["body"]))
        flow.append(Spacer(1, 1.2 * mm))

    flow.append(Paragraph("Sources", S["h2"]))
    flow.append(Paragraph(
        f"Statement: {bm.get('source_file') or 'n/a'} - {bm.get('count', 0):,} lines, "
        f"opening {inr(bm.get('opening_balance'))}, closing "
        f"{inr(bm.get('closing_balance'))}. The statement's own running balance "
        + ("ties throughout." if bm.get("continuity_ok")
           else "does NOT tie, so the figures should be checked against the original.")
        + f"<br/>Bank book: {bk.get('source_file') or 'n/a'} - {bk.get('count', 0):,} "
        f"vouchers, opening {inr(bk.get('opening_balance'))}, closing "
        f"{inr(bk.get('closing_balance'))}.", S["small"]))

    footer = _footer(name, "Bank reconciliation working paper")
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf


def build_statement_pdf(result, entity=None):
    S = _styles()
    entity = entity or {}
    tm = result.get("their_meta") or {}
    om = result.get("our_meta") or {}
    name = entity.get("entity_name") or "Entity"
    party = tm.get("party_name") or "Party"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title=f"Statement reconciliation - {party}", author="Ultimate Accountant")
    W = A4[0] - 30 * mm
    rel = result.get("relation", "supplier")

    flow = [Paragraph(f"Statement reconciliation - {party}", S["title"])]
    flow.append(Paragraph(" &nbsp;·&nbsp; ".join(p for p in [
        name, f"GSTIN {tm['gstin']}" if tm.get("gstin") else "",
        f"{tm.get('first_date', '')} to {tm.get('last_date', '')}",
        f"treated as a {rel}"] if p), S["sub"]))
    flow.append(Paragraph(
        f"Prepared {datetime.now().strftime('%d %b %Y at %H:%M')}"
        + (f" by {entity['preparer']}" if entity.get("preparer") else ""), S["small"]))
    flow.append(Spacer(1, 6 * mm))

    c = result["counts"]
    b = result["balances"]
    flow.append(Paragraph(
        f"Their statement carries {c['their_entries']:,} entries and closes at "
        f"{inr(b['theirs'])}. Our ledger carries {c['our_entries']:,} and closes at "
        f"{inr(b['ours'])}, a difference of {inr(b['difference'])}. "
        f"{c['agreed']:,} documents agree outright"
        + (f", {c['disputed']:,} carry a different amount" if c["disputed"] else "")
        + (f", {c['timing']:,} sit in different periods" if c["timing"] else "")
        + f", {c['their_only']:,} appear only on their statement and "
        f"{c['our_only']:,} only in our books.", S["lead"]))

    flow.append(Paragraph("The bridge", S["h2"]))
    flow += _brs_pdf_lines(S, W, result["bridge"]["lines"], result["bridge"]["ties"],
                           result["bridge"]["residual"])

    if result.get("findings"):
        flow.append(Paragraph("What needs attention", S["h2"]))
        rows = [["Severity", "Finding", "Items", "Amount"]]
        for f in result["findings"]:
            rows.append([f["tone"].title(),
                         Paragraph(f"<b>{f['title']}</b><br/>"
                                   f"<font color='#5A6672'>{f['detail']}</font>", S["cell"]),
                         f"{f['count']:,}", inr(f["amount"])])
        flow.append(_table(rows, [W * 0.10, W * 0.62, W * 0.12, W * 0.16],
                           align_right=(2, 3)))

    chk = result.get("gst_check")
    if chk and chk.get("checked"):
        flow.append(Paragraph("Checked against what they filed", S["h2"]))
        flow.append(Paragraph(
            f"{chk['checked']:,} invoices on their statement were looked for in our "
            f"GSTR-2B. {chk['agreed']:,} agree. {chk['not_filed_count']:,} totalling "
            f"{inr(chk['not_filed_amount'])} do not appear at all, which means they "
            f"have not been returned and no credit is available on them yet. "
            f"{chk['value_differs_count']:,} were filed at a different value from the "
            f"statement.", S["body"]))

    flow.append(PageBreak())
    for group in result["bridge"]["groups"]:
        flow.append(Paragraph(group["label"], S["h2"]))
        rows = [["Date", "Reference", "Particulars", "Amount", "Age"]]
        for r in group["rows"][:40]:
            rows.append([_d(r["date"]), str(r["ref"] or ""),
                         Paragraph(str(r["particulars"] or "")[:58], S["cell"]),
                         inr(r["amount"]),
                         f"{r['age_days']}d" if r.get("age_days") is not None else ""])
        rows.append(["", "Total", "", inr(group["amount"]), ""])
        t = _table(rows, [W * 0.12, W * 0.20, W * 0.42, W * 0.18, W * 0.08],
                   align_right=(3,))
        t.setStyle(TableStyle([
            ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), BOLD),
            ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.7, colors.black),
        ]))
        flow.append(t)
        if len(group["rows"]) > 40:
            flow.append(Paragraph(f"and {len(group['rows']) - 40} more - see the "
                                  f"workbook", S["small"]))

    disputed = [r for r in result["all_records"] if r["status"] == "Amount disputed"]
    if disputed:
        flow.append(Paragraph("Documents where the amounts disagree", S["h2"]))
        rows = [["Reference", "Date", "Their figure", "Our figure", "Difference"]]
        for r in disputed[:30]:
            rows.append([str(r["ref_theirs"] or r["ref_ours"] or ""),
                         _d(r["date_theirs"] or r["date_ours"]),
                         inr(r["owed_theirs"]), inr(r["owed_ours"]),
                         inr(r["difference"])])
        flow.append(_table(rows, [W * 0.22, W * 0.14, W * 0.21, W * 0.21, W * 0.22],
                           align_right=(2, 3, 4)))

    flow.append(Paragraph("Sources", S["h2"]))
    flow.append(Paragraph(
        f"Their statement: {tm.get('source_file') or 'n/a'} - {tm.get('count', 0):,} "
        f"entries, closing {inr(tm.get('closing_balance'))}"
        + (f" against {inr(tm.get('stated_closing'))} stated on the document"
           if tm.get("stated_closing") is not None else "")
        + f".<br/>Our ledger: {om.get('source_file') or 'n/a'} - "
        f"{om.get('count', 0):,} entries, closing {inr(om.get('closing_balance'))}.",
        S["small"]))

    footer = _footer(name, f"Statement reconciliation with {party}")
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf


def _d(val):
    if not val:
        return ""
    if isinstance(val, str):
        try:
            val = datetime.strptime(val[:10], "%Y-%m-%d")
        except ValueError:
            return val
    return val.strftime("%d-%b-%y")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

BANK_CSV = [
    ("status", "Status"), ("category", "Category"), ("direction", "Direction"),
    ("date_bank", "Date (bank)"), ("date_book", "Date (book)"),
    ("narration", "Narration"), ("channel", "Channel"),
    ("counterparty", "Counterparty"), ("bank_ref", "Bank reference"),
    ("book_party", "Ledger (Tally)"), ("book_vch_no", "Vch No."),
    ("book_vch_type", "Vch Type"), ("book_ref", "Book reference"),
    ("bank_amount", "Amount (bank)"), ("book_amount", "Amount (book)"),
    ("difference", "Difference"), ("balance", "Balance"),
    ("date_gap_days", "Days apart"), ("age_days", "Age (days)"),
    ("match_score", "Score"), ("match_basis", "Basis"), ("flags", "Flags"),
    ("source_row_bank", "Row in statement"), ("source_row_book", "Row in book"),
    ("why", "Why it is open"), ("fp", "Item id"),
]

STATEMENT_CSV = [
    ("status", "Status"), ("category", "Category"), ("kind", "Kind"),
    ("date_theirs", "Date (theirs)"), ("date_ours", "Date (ours)"),
    ("ref_theirs", "Reference (theirs)"), ("ref_ours", "Reference (ours)"),
    ("particulars_theirs", "Particulars (theirs)"),
    ("particulars_ours", "Particulars (ours)"),
    ("owed_theirs", "Amount (theirs)"), ("owed_ours", "Amount (ours)"),
    ("difference", "Difference"), ("date_gap_days", "Days apart"),
    ("age_days", "Age (days)"), ("aging_bucket", "Ageing"),
    ("match_score", "Score"), ("match_basis", "Basis"), ("flags", "Flags"),
    ("why", "Why it is open"), ("fp", "Item id"),
]


def build_recon_csv(result, kind):
    cols = BANK_CSV if kind == "bank" else STATEMENT_CSV
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([label for _, label in cols])
    for r in result.get("all_records", []):
        line = []
        for field, _ in cols:
            val = r.get(field)
            if isinstance(val, list):
                val = " ".join(str(x) for x in val)
            elif field == "why":
                val = why_line(r)
            elif hasattr(val, "strftime"):
                val = iso(val)
            line.append("" if val is None else val)
        w.writerow(line)
    return out.getvalue()


def build_brs_csv(result):
    """The reconciliation statement itself, for pasting into a working paper."""
    lines = (result.get("brs") or result.get("bridge"))["lines"]
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["Line", "Items", "Amount"])
    for line in lines:
        w.writerow([line["label"], line.get("count") or "", line["amount"]])
    return out.getvalue()
