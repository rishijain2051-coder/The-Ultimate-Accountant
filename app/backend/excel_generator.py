"""
Excel report writer.

The first seven sheets keep the layout, fonts, number formats and formulas of
the original reference workbook, because that file is what gets filed and
circulated. Everything new is appended after them, so an existing review
process keeps working and the extra analysis is there for whoever wants it.
"""

from datetime import datetime, date
from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Formatting constants, matching the reference workbook
# ---------------------------------------------------------------------------

FONT_NAME = "Arial"
COLOR_NAVY = "1F3864"
COLOR_GRAY = "595959"
COLOR_WHITE = "FFFFFF"

FILL_HEADER = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
FILL_SUBTLE = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

# Status tints, light enough to print and to read black text on.
FILL_CLEAR = PatternFill(start_color="E7F3EC", end_color="E7F3EC", fill_type="solid")
FILL_WARN = PatternFill(start_color="FDF2DC", end_color="FDF2DC", fill_type="solid")
FILL_RISK = PatternFill(start_color="FBE6E2", end_color="FBE6E2", fill_type="solid")
FILL_INFO = PatternFill(start_color="E8F0F9", end_color="E8F0F9", fill_type="solid")

STATUS_FILL = {
    "Matched": FILL_CLEAR,
    "Value mismatch": FILL_WARN,
    "Invoice no. mismatch": FILL_INFO,
    "Probable match": FILL_INFO,
    "Split booking": FILL_INFO,
    "In GSTR-2B only": FILL_RISK,
    "In Tally only": FILL_WARN,
    "Superseded": FILL_SUBTLE,
    "In GSTR-1 only": FILL_RISK,
}

TONE_FILL = {"clear": FILL_CLEAR, "warn": FILL_WARN, "risk": FILL_RISK,
             "info": FILL_INFO, "total": FILL_SUBTLE, "neutral": None}

FONT_SHEET_TITLE = Font(name=FONT_NAME, size=14, bold=True, color=COLOR_NAVY)
FONT_SECTION_TITLE = Font(name=FONT_NAME, size=12, bold=True, color=COLOR_NAVY)
FONT_SUBTITLE = Font(name=FONT_NAME, size=10, bold=False, color="000000")
FONT_NOTE = Font(name=FONT_NAME, size=9, bold=False, color=COLOR_GRAY)
FONT_SECTION_HDR = Font(name=FONT_NAME, size=11, bold=True, color="000000")

FONT_TBL_HDR = Font(name=FONT_NAME, size=10, bold=True, color=COLOR_WHITE)
FONT_DATA = Font(name=FONT_NAME, size=10, bold=False, color="000000")
FONT_TOTAL = Font(name=FONT_NAME, size=10, bold=True, color="000000")

BORDER_THIN = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)
BORDER_TOTAL = Border(top=Side(style="thin", color="000000"),
                      bottom=Side(style="double", color="000000"))

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)

CURRENCY = '#,##0.00;(#,##0.00);"-"'
INTEGER = '#,##0'
DATE_FMT = 'dd-mmm-yy'
PERCENT = '0.0"%"'


def auto_fit_columns(ws, min_widths=None, max_widths=None, skip_title_rows=True):
    """Size columns to their content, ignoring the wide title rows."""
    min_widths = min_widths or {}
    max_widths = max_widths or {}
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            if skip_title_rows and cell.row in (1, 2, 3) and ws.title != "Summary":
                continue
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        w = max(max_len + 4, min_widths.get(col_letter, 12))
        if col_letter in max_widths:
            w = min(w, max_widths[col_letter])
        ws.column_dimensions[col_letter].width = float(min(w, 60))


def format_cell_value(val):
    if isinstance(val, (datetime, date)):
        return val
    if isinstance(val, (list, tuple)):
        return ", ".join(str(v) for v in val)
    if isinstance(val, bool):
        return "Yes" if val else "No"
    return val


def _as_date(val):
    """Accept both datetimes and the ISO strings a saved run round-trips as."""
    if isinstance(val, (datetime, date)):
        return val
    if isinstance(val, str) and len(val) == 10 and val[4] == "-":
        try:
            return datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return val
    return val


def create_reconciliation_excel(reconciliation_data, output_path_or_buffer=None,
                                insights=None, sales=None):
    """Build the workbook. `insights` and `sales` are optional; the first seven
    sheets are written identically with or without them."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    entity_info = reconciliation_data.get("entity_info", {}) or {}
    entity_name = entity_info.get("entity_name", "ENTITY")
    entity_gstin = entity_info.get("gstin", "")
    date_range = entity_info.get("date_range") or entity_info.get("fy_label") or "FY 2025-26"

    control_totals = reconciliation_data["control_totals"]
    status_summary = reconciliation_data["status_summary"]
    itc_impact = reconciliation_data["itc_impact"]
    all_records = reconciliation_data["all_records"]
    subsets = reconciliation_data["subsets"]
    party_wise = reconciliation_data["party_wise"]

    # =====================================================================
    # SHEET 1: Summary
    # =====================================================================
    ws_sum = wb.create_sheet(title="Summary")
    ws_sum.views.sheetView[0].showGridLines = True

    ws_sum["A1"] = "GSTR-2B vs Tally Purchase Register — Reconciliation Summary"
    ws_sum["A1"].font = FONT_SHEET_TITLE

    subtitle_parts = [p for p in [entity_name,
                                  f"GSTIN {entity_gstin}" if entity_gstin else "",
                                  date_range] if p]
    ws_sum["A2"] = "  |  ".join(subtitle_parts)
    ws_sum["A2"].font = FONT_SUBTITLE

    ws_sum["A3"] = "Source: GSTR-2B B2B and Tally Purchase Register (Auto-Reconciled)"
    ws_sum["A3"].font = FONT_NOTE

    ws_sum["A5"] = "Control totals"
    ws_sum["A5"].font = FONT_SECTION_HDR

    ctrl_rows = [
        ("Invoices in GSTR-2B (B2B)", control_totals["invoices_in_2b"], INTEGER,
         "Excludes repeated header rows from the merged monthly files"),
        ("Invoice value as per GSTR-2B", control_totals["invoice_value_2b"], CURRENCY, ""),
        ("Total ITC as per GSTR-2B", control_totals["total_itc_2b"], CURRENCY,
         "IGST + CGST + SGST + Cess"),
        ("Vouchers in Tally purchase register", control_totals["vouchers_in_tally"], INTEGER, ""),
        ("Value as per Tally", control_totals["value_in_tally"], CURRENCY, ""),
    ]
    for idx, (label, val, nfmt, note) in enumerate(ctrl_rows, start=6):
        c1 = ws_sum.cell(idx, 1, label)
        c1.font = FONT_DATA
        c2 = ws_sum.cell(idx, 2, val)
        c2.font = FONT_DATA
        c2.number_format = nfmt
        c2.alignment = ALIGN_RIGHT
        if note:
            ws_sum.cell(idx, 5, note).font = FONT_NOTE

    ws_sum["A12"] = "Reconciliation status"
    ws_sum["B12"] = "Count"
    ws_sum["C12"] = "Value (2B)"
    ws_sum["D12"] = "Value (Tally)"
    for name in ("A12", "B12", "C12", "D12"):
        ws_sum[name].font = FONT_TBL_HDR
        ws_sum[name].fill = FILL_HEADER
        ws_sum[name].alignment = ALIGN_CENTER if name == "B12" else ALIGN_RIGHT
    ws_sum["A12"].alignment = ALIGN_LEFT

    curr_row = 13
    for s in status_summary[:-1]:
        ws_sum.cell(curr_row, 1, s["status"]).font = FONT_DATA
        c2 = ws_sum.cell(curr_row, 2, s["count"])
        c2.font, c2.number_format, c2.alignment = FONT_DATA, INTEGER, ALIGN_RIGHT
        c3 = ws_sum.cell(curr_row, 3, s["val_2b"])
        c3.font, c3.number_format, c3.alignment = FONT_DATA, CURRENCY, ALIGN_RIGHT
        c4 = ws_sum.cell(curr_row, 4, s["val_tally"])
        c4.font, c4.number_format, c4.alignment = FONT_DATA, CURRENCY, ALIGN_RIGHT
        for col_i in range(1, 5):
            ws_sum.cell(curr_row, col_i).border = BORDER_THIN
        curr_row += 1

    tot_row = curr_row
    ws_sum.cell(tot_row, 1, "Total").font = FONT_TOTAL
    for col_i, nfmt in ((2, INTEGER), (3, CURRENCY), (4, CURRENCY)):
        letter = get_column_letter(col_i)
        cell = ws_sum.cell(tot_row, col_i, f"=SUM({letter}13:{letter}{tot_row - 1})")
        cell.font, cell.number_format, cell.alignment = FONT_TOTAL, nfmt, ALIGN_RIGHT
    for col_i in range(1, 5):
        ws_sum.cell(tot_row, col_i).border = BORDER_TOTAL

    curr_row += 2
    ws_sum.cell(curr_row, 1, "ITC impact").font = FONT_SECTION_HDR
    curr_row += 1
    for item in itc_impact:
        ws_sum.cell(curr_row, 1, item["title"]).font = FONT_DATA
        c2 = ws_sum.cell(curr_row, 2, item["amount"])
        c2.font, c2.number_format, c2.alignment = FONT_DATA, CURRENCY, ALIGN_RIGHT
        ws_sum.cell(curr_row, 5, item["action"]).font = FONT_NOTE
        curr_row += 1

    if insights:
        curr_row += 1
        ws_sum.cell(curr_row, 1, "Input tax credit working").font = FONT_SECTION_HDR
        curr_row += 1
        for row in insights["itc_position"]["ladder"]:
            font = FONT_TOTAL if row["tone"] in ("total", "clear") else FONT_DATA
            c1 = ws_sum.cell(curr_row, 1, row["label"])
            c1.font = font
            c2 = ws_sum.cell(curr_row, 2, row["amount"])
            c2.font = font
            c2.number_format, c2.alignment = CURRENCY, ALIGN_RIGHT
            fill = TONE_FILL.get(row["tone"])
            if fill:
                c1.fill = c2.fill = fill
            ws_sum.cell(curr_row, 5, row["note"]).font = FONT_NOTE
            curr_row += 1

        health = reconciliation_data.get("health") or {}
        if health:
            curr_row += 1
            ws_sum.cell(curr_row, 1, "Reconciliation health").font = FONT_SECTION_HDR
            curr_row += 1
            ws_sum.cell(curr_row, 1, f"{health['verdict']} — score "
                                     f"{health['score']} / 100").font = FONT_DATA
            curr_row += 1
            for d in health.get("drivers", []):
                ws_sum.cell(curr_row, 1, f"   {d['label']}").font = FONT_DATA
                c2 = ws_sum.cell(curr_row, 2, d["value"])
                c2.font, c2.number_format, c2.alignment = FONT_DATA, PERCENT, ALIGN_RIGHT
                curr_row += 1

    curr_row += 1
    ws_sum.cell(curr_row, 1, "How the matching was done").font = FONT_SECTION_HDR
    curr_row += 1
    notes = [
        "1. Invoice / voucher numbers normalised (case, spaces, slashes and the leading zeros of each group removed), then matched on number + value within tolerance.",
        "2. Remaining: same invoice number, different value -> \"Value mismatch\".",
        "3. Remaining: value equal within tolerance and supplier name similar -> \"Invoice no. mismatch\".",
        "4. Remaining: party, value and date close enough to suggest a pair -> \"Probable match\", for a human to confirm.",
        "5. Remaining: one invoice whose value equals the sum of several vouchers -> \"Split booking\".",
        "6. Anything still unmatched is reported one-sided.",
        "Amendment rows (B2BA / CDNRA) supersede the documents they amend; the superseded rows are listed separately and excluded from the totals.",
        "Tally has no GSTIN column, so supplier identity is matched on ledger name — verify the party-wise sheet before acting.",
        "Bank-charge GST appears in 2B but not in a purchase register; it is normally posted through bank/journal vouchers.",
    ]
    for n in notes:
        ws_sum.cell(curr_row, 1, n).font = FONT_NOTE
        curr_row += 1

    for letter, width in (("A", 58.0), ("B", 18.0), ("C", 18.0), ("D", 18.0), ("E", 78.0)):
        ws_sum.column_dimensions[letter].width = width

    # =====================================================================
    # Shared data-sheet builder
    # =====================================================================
    def build_sheet(sheet_title, section_heading, headers, data_rows, col_formats,
                    sum_cols=None, status_col=None, tone_col=None, freeze="A4",
                    autofilter=True, wrap_cols=()):
        ws = wb.create_sheet(title=sheet_title[:31])
        ws.views.sheetView[0].showGridLines = True
        ws.cell(1, 1, section_heading).font = FONT_SECTION_TITLE

        ws.row_dimensions[3].height = 24.0
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(3, col_idx, h)
            cell.font = FONT_TBL_HDR
            cell.fill = FILL_HEADER
            low = h.lower()
            if "date" in low or "period" in low or "status" in low:
                cell.alignment = ALIGN_CENTER
            elif col_formats.get(col_idx) in (CURRENCY, INTEGER, PERCENT):
                cell.alignment = ALIGN_RIGHT
            else:
                cell.alignment = ALIGN_LEFT

        r_idx = 4
        for row in data_rows:
            ws.row_dimensions[r_idx].height = 20.0
            row_fill = None
            if status_col is not None and status_col <= len(row):
                row_fill = STATUS_FILL.get(row[status_col - 1])
            elif tone_col is not None and tone_col <= len(row):
                row_fill = TONE_FILL.get(row[tone_col - 1])
            for col_idx, val in enumerate(row, start=1):
                cell = ws.cell(r_idx, col_idx, format_cell_value(_as_date(val)))
                cell.font = FONT_DATA
                cell.border = BORDER_THIN
                if row_fill and col_idx == (status_col or tone_col):
                    cell.fill = row_fill
                nfmt = col_formats.get(col_idx)
                if nfmt:
                    cell.number_format = nfmt
                    if nfmt in (CURRENCY, INTEGER, PERCENT):
                        cell.alignment = ALIGN_RIGHT
                    elif nfmt == DATE_FMT:
                        cell.alignment = ALIGN_CENTER
                elif isinstance(cell.value, (datetime, date)):
                    cell.number_format = DATE_FMT
                    cell.alignment = ALIGN_CENTER
                elif isinstance(cell.value, (int, float)):
                    cell.number_format = CURRENCY
                    cell.alignment = ALIGN_RIGHT
                else:
                    cell.alignment = ALIGN_WRAP if col_idx in wrap_cols else ALIGN_LEFT
            r_idx += 1

        if sum_cols and data_rows:
            tot_row_idx = r_idx
            ws.row_dimensions[tot_row_idx].height = 22.0
            first_sum_col = min(sum_cols)
            label_col = max(1, first_sum_col - 1) if first_sum_col > 1 else 1
            ws.cell(tot_row_idx, label_col, "Total").font = FONT_TOTAL
            for c in range(1, len(headers) + 1):
                cell = ws.cell(tot_row_idx, c)
                cell.border = BORDER_TOTAL
                if c in sum_cols:
                    letter = get_column_letter(c)
                    cell.value = f"=SUM({letter}4:{letter}{tot_row_idx - 1})"
                    cell.font = FONT_TOTAL
                    cell.number_format = col_formats.get(c, CURRENCY)
                    cell.alignment = ALIGN_RIGHT

        if autofilter and data_rows:
            ws.auto_filter.ref = f"A3:{get_column_letter(len(headers))}{3 + len(data_rows)}"
        if freeze:
            ws.freeze_panes = freeze
        auto_fit_columns(ws)
        return ws

    # =====================================================================
    # SHEET 2: Reconciliation
    # =====================================================================
    rec_headers = [
        "Status", "Supplier GSTIN", "Supplier (GSTR-2B)", "Ledger (Tally)",
        "Invoice No. (2B)", "Vch No. (Tally)", "Invoice Date (2B)", "Vch Date (Tally)",
        "Invoice Value 2B", "Amount Tally", "Difference", "Taxable Value 2B",
        "Tax (ITC) 2B", "2B Period", "ITC Available", "Vch Type", "Note (2B)", "Note (Tally)",
    ]
    rec_formats = {7: DATE_FMT, 8: DATE_FMT, 9: CURRENCY, 10: CURRENCY,
                   11: CURRENCY, 12: CURRENCY, 13: CURRENCY}
    rec_rows = [[
        r["status"], r["gstin"], r["party_2b"], r["party_tally"],
        r["inv_no_2b"], r["vch_no_tally"], r["inv_date_2b"], r["vch_date_tally"],
        r["inv_val_2b"], r["amt_tally"], r["difference"], r["taxable_2b"],
        r["tax_2b"], r["period_2b"], r["itc_avail"], r["vch_type"],
        r["note_2b"], r["note_tally"],
    ] for r in all_records]
    build_sheet("Reconciliation",
                "Invoice-level reconciliation — GSTR-2B (B2B) vs Tally Purchase Register",
                rec_headers, rec_rows, rec_formats, sum_cols=[9, 10, 11, 12, 13],
                status_col=1, wrap_cols=(17, 18))

    # =====================================================================
    # SHEET 3: 2B not in Tally
    # =====================================================================
    gst_only_headers = ["Supplier GSTIN", "Supplier (GSTR-2B)", "Invoice No. (2B)",
                        "Invoice Date (2B)", "Invoice Value 2B", "Taxable Value 2B",
                        "Tax (ITC) 2B", "2B Period", "ITC Available", "Note (2B)"]
    build_sheet("2B not in Tally",
                "In GSTR-2B but not booked in Tally — ITC available, purchase possibly unrecorded",
                gst_only_headers,
                [[r["gstin"], r["party_2b"], r["inv_no_2b"], r["inv_date_2b"],
                  r["inv_val_2b"], r["taxable_2b"], r["tax_2b"], r["period_2b"],
                  r["itc_avail"], r["note_2b"]] for r in subsets["gst_only"]],
                {4: DATE_FMT, 5: CURRENCY, 6: CURRENCY, 7: CURRENCY},
                sum_cols=[5, 6, 7], wrap_cols=(10,))

    # =====================================================================
    # SHEET 4: Tally not in 2B
    # =====================================================================
    build_sheet("Tally not in 2B",
                "Booked in Tally but not appearing in GSTR-2B — supplier may not have filed; ITC to be held back",
                ["Ledger (Tally)", "Vch No. (Tally)", "Vch Date (Tally)",
                 "Amount Tally", "Vch Type", "Note (Tally)"],
                [[r["party_tally"], r["vch_no_tally"], r["vch_date_tally"],
                  r["amt_tally"], r["vch_type"], r["note_tally"]]
                 for r in subsets["tally_only"]],
                {3: DATE_FMT, 4: CURRENCY}, sum_cols=[4], wrap_cols=(6,))

    # =====================================================================
    # SHEET 5: Value mismatch
    # =====================================================================
    val_mis_headers = ["Supplier GSTIN", "Supplier (GSTR-2B)", "Ledger (Tally)",
                       "Invoice No. (2B)", "Vch No. (Tally)", "Invoice Date (2B)",
                       "Vch Date (Tally)", "Invoice Value 2B", "Amount Tally",
                       "Difference", "Tax (ITC) 2B"]
    build_sheet("Value mismatch", "Same invoice number, different value",
                val_mis_headers,
                [[r["gstin"], r["party_2b"], r["party_tally"], r["inv_no_2b"],
                  r["vch_no_tally"], r["inv_date_2b"], r["vch_date_tally"],
                  r["inv_val_2b"], r["amt_tally"], r["difference"], r["tax_2b"]]
                 for r in subsets["value_mismatch"]],
                {6: DATE_FMT, 7: DATE_FMT, 8: CURRENCY, 9: CURRENCY,
                 10: CURRENCY, 11: CURRENCY},
                sum_cols=[8, 9, 10, 11])

    # =====================================================================
    # SHEET 6: Invoice no mismatch
    # =====================================================================
    build_sheet("Invoice no mismatch",
                "Matched on party and value — invoice number differs between 2B and Tally",
                ["Supplier GSTIN", "Supplier (GSTR-2B)", "Ledger (Tally)",
                 "Invoice No. (2B)", "Vch No. (Tally)", "Invoice Date (2B)",
                 "Vch Date (Tally)", "Invoice Value 2B", "Amount Tally", "Tax (ITC) 2B"],
                [[r["gstin"], r["party_2b"], r["party_tally"], r["inv_no_2b"],
                  r["vch_no_tally"], r["inv_date_2b"], r["vch_date_tally"],
                  r["inv_val_2b"], r["amt_tally"], r["tax_2b"]]
                 for r in subsets["invoice_no_mismatch"]],
                {6: DATE_FMT, 7: DATE_FMT, 8: CURRENCY, 9: CURRENCY, 10: CURRENCY},
                sum_cols=[8, 9, 10])

    # =====================================================================
    # SHEET 7: Party-wise
    # =====================================================================
    build_sheet("Party-wise", "Supplier-wise summary (GSTR-2B basis)",
                ["Supplier GSTIN", "Supplier (GSTR-2B)", "Invoices in 2B", "Value in 2B",
                 "Taxable in 2B", "Total ITC in 2B", "Matched in Tally (nos.)",
                 "Matched value (Tally)", "Unmatched invoices", "Unmatched value",
                 "ITC at risk / unclaimed"],
                [[p["gstin"], p["party_name"], p["count_2b"], p["val_2b"],
                  p["taxable_2b"], p["itc_2b"], p["matched_count"], p["matched_val_tally"],
                  p["unmatched_count"], p["unmatched_val"], p["itc_at_risk"]]
                 for p in party_wise],
                {3: INTEGER, 4: CURRENCY, 5: CURRENCY, 6: CURRENCY, 7: INTEGER,
                 8: CURRENCY, 9: INTEGER, 10: CURRENCY, 11: CURRENCY},
                sum_cols=[4, 5, 6, 8, 10, 11])

    # =====================================================================
    # Sheets 8+ : the extended analysis
    # =====================================================================
    open_rows = [r for r in all_records if r.get("why")]
    if open_rows:
        build_sheet("Why each is open",
                    "Every open entry, with the reason it is open and what to do about it",
                    ["Status", "Supplier / ledger", "Invoice / Vch No.", "Date",
                     "Value 2B", "Amount Tally", "Why it is open", "Closest match found",
                     "What to do"],
                    [[r["status"], r["party_2b"] or r["party_tally"],
                      r["inv_no_2b"] or r["vch_no_tally"],
                      r["inv_date_2b"] or r["vch_date_tally"], r["inv_val_2b"], r["amt_tally"],
                      r["why"].get("title"), r["why"].get("nearest"), r["why"].get("todo")]
                     for r in open_rows],
                    {4: DATE_FMT, 5: CURRENCY, 6: CURRENCY},
                    status_col=1, wrap_cols=(7, 8, 9))

    if subsets.get("probable"):
        build_sheet("Probable matches",
                    "Suggested pairs — confirm or reject each one before posting",
                    ["Score", "Basis", "Supplier GSTIN", "Supplier (GSTR-2B)",
                     "Ledger (Tally)", "Invoice No. (2B)", "Vch No. (Tally)",
                     "Invoice Date (2B)", "Vch Date (Tally)", "Days apart",
                     "Invoice Value 2B", "Amount Tally", "Difference", "Tax (ITC) 2B"],
                    [[r["match_score"], r["match_basis"], r["gstin"], r["party_2b"],
                      r["party_tally"], r["inv_no_2b"], r["vch_no_tally"],
                      r["inv_date_2b"], r["vch_date_tally"], r["date_gap_days"],
                      r["inv_val_2b"], r["amt_tally"], r["difference"], r["tax_2b"]]
                     for r in subsets["probable"]],
                    {1: '0.000', 8: DATE_FMT, 9: DATE_FMT, 10: INTEGER,
                     11: CURRENCY, 12: CURRENCY, 13: CURRENCY, 14: CURRENCY},
                    sum_cols=[11, 12, 13, 14], wrap_cols=(2,))

    if subsets.get("split"):
        split_rows = []
        for r in subsets["split"]:
            for i, part in enumerate(r["parts"] or []):
                split_rows.append([
                    r["gstin"], r["party_2b"], r["inv_no_2b"], r["inv_date_2b"],
                    r["inv_val_2b"] if i == 0 else None,
                    part["vch_no"], part["party"], _as_date(part["date"]), part["amt"],
                    part["vch_type"], r["difference"] if i == 0 else None,
                ])
        build_sheet("Split bookings",
                    "One supplier invoice booked across several Tally vouchers",
                    ["Supplier GSTIN", "Supplier (GSTR-2B)", "Invoice No. (2B)",
                     "Invoice Date (2B)", "Invoice Value 2B", "Vch No. (Tally)",
                     "Ledger (Tally)", "Vch Date (Tally)", "Amount Tally",
                     "Vch Type", "Residual"],
                    split_rows,
                    {4: DATE_FMT, 5: CURRENCY, 8: DATE_FMT, 9: CURRENCY, 11: CURRENCY},
                    sum_cols=[5, 9, 11])

    if subsets.get("superseded"):
        build_sheet("Superseded rows",
                    "Documents replaced by an amendment row — excluded from every total above",
                    ["Supplier GSTIN", "Supplier (GSTR-2B)", "Invoice No. (2B)",
                     "Invoice Date (2B)", "Invoice Value 2B", "Tax (ITC) 2B",
                     "2B Period", "Note"],
                    [[r["gstin"], r["party_2b"], r["inv_no_2b"], r["inv_date_2b"],
                      r["inv_val_2b"], r["tax_2b"], r["period_2b"], r["note_2b"]]
                     for r in subsets["superseded"]],
                    {4: DATE_FMT, 5: CURRENCY, 6: CURRENCY}, sum_cols=[5, 6],
                    wrap_cols=(8,))

    if insights:
        _write_insight_sheets(wb, build_sheet, insights, reconciliation_data,
                              entity_name, entity_gstin, date_range)

    if sales:
        _write_sales_sheets(wb, build_sheet, sales)

    # Cover the workbook with a table of contents so a 15-sheet file is navigable.
    _write_index(wb, entity_name, entity_gstin, date_range, reconciliation_data)

    if output_path_or_buffer is None:
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
    wb.save(output_path_or_buffer)
    return output_path_or_buffer


def _write_insight_sheets(wb, build_sheet, insights, result, entity_name,
                          entity_gstin, date_range):
    itc = insights["itc_position"]

    build_sheet("ITC working",
                "Input tax credit working — from the portal figure down to what is safe to claim",
                ["Step", "Amount", "Basis"],
                [[row["label"], row["amount"], row["note"]] for row in itc["ladder"]],
                {2: CURRENCY}, wrap_cols=(3,), autofilter=False)

    if itc["blocked_reasons"]:
        build_sheet("ITC blocked reasons",
                    "Rows where the GST portal itself blocks the credit",
                    ["Reason as filed", "Documents", "Tax blocked", "What it means"],
                    [[b["reason"], b["count"], b["tax"], b["explanation"]]
                     for b in itc["blocked_reasons"]],
                    {2: INTEGER, 3: CURRENCY}, sum_cols=[3], wrap_cols=(1, 4))

    tb = insights["gstr3b"]
    rows = [[r["head"], r["liability"], r["credit_used"], r["cash"], r["credit_left"]]
            for r in tb["set_off"]]
    rows.append(["Total", tb["outward"]["total"], None, tb["cash_payable"],
                 tb["credit_carried_forward"]])
    build_sheet("GSTR-3B working",
                "GSTR-3B working — outward liability set off against the credit allowed by law"
                + ("" if tb["liability_known"] else " (no sales source loaded)"),
                ["Head", "Output liability", "Credit set off", "Payable in cash",
                 "Credit carried forward"],
                rows, {2: CURRENCY, 3: CURRENCY, 4: CURRENCY, 5: CURRENCY},
                autofilter=False)

    build_sheet("Action checklist",
                "What to do next, ordered by how much credit it recovers",
                ["Priority", "Action", "Items", "Amount involved", "Why it matters"],
                [[i["priority"], i["title"], i["count"], i["amount"], i["detail"]]
                 for i in insights["checklist"]],
                {1: INTEGER, 3: INTEGER, 4: CURRENCY}, wrap_cols=(5,), autofilter=False)

    build_sheet("Data quality",
                "Defects in the source data — each one is something a person can go and fix",
                ["Severity", "Finding", "Rows", "Amount", "What to do"],
                [[c["tone"], c["title"], c["count"], c["amount"], c["detail"]]
                 for c in insights["quality"]["checks"]],
                {3: INTEGER, 4: CURRENCY}, tone_col=1, wrap_cols=(5,), autofilter=False)

    dupes = next((c for c in insights["quality"]["checks"] if c["key"] == "duplicates"), None)
    if dupes and dupes.get("rows"):
        build_sheet("Possible duplicates",
                    "The same supplier, number and value more than once in the return",
                    ["Supplier GSTIN", "Supplier", "Invoice No.", "Value", "Times",
                     "Excess tax", "Periods", "Sections"],
                    [[d["gstin"], d["party"], d["inv_no"], d["value"], d["times"],
                      d["tax"], ", ".join(d["periods"]), ", ".join(d["sections"])]
                     for d in dupes["rows"]],
                    {4: CURRENCY, 5: INTEGER, 6: CURRENCY}, sum_cols=[6])

    build_sheet("Ageing",
                "Open items by how long they have been sitting unresolved",
                ["Bucket", "Items", "Value", "Tax involved", "Breakdown by status"],
                [[a["bucket"], a["count"], a["value"], a["tax"],
                  "; ".join(f"{k}: {v}" for k, v in a["by_status"].items())]
                 for a in insights["aging"]],
                {2: INTEGER, 3: CURRENCY, 4: CURRENCY}, sum_cols=[3, 4],
                wrap_cols=(5,), autofilter=False)

    build_sheet("Rate analysis", "Tax-slab distribution across the GSTR-2B documents",
                ["Slab", "Documents", "Taxable value", "Tax", "Invoice value",
                 "Share of credit"],
                [[r["slab"], r["count"], r["taxable"], r["tax"], r["value"], r["share"]]
                 for r in insights["rate_analysis"]],
                {2: INTEGER, 3: CURRENCY, 4: CURRENCY, 5: CURRENCY, 6: PERCENT},
                sum_cols=[3, 4, 5], autofilter=False)

    build_sheet("Monthly trend", "Month-by-month comparison and match quality",
                ["Month", "Value (2B)", "Value (Tally)", "Variance", "Tax (2B)",
                 "Documents", "Matched", "Open", "Match rate", "ITC at risk"],
                [[m["label"], m["val_2b"], m["val_tally"], m["variance"], m["tax"],
                  m["total"], m["matched"], m["open"], m["match_rate"], m["at_risk"]]
                 for m in insights["monthly"]],
                {2: CURRENCY, 3: CURRENCY, 4: CURRENCY, 5: CURRENCY, 6: INTEGER,
                 7: INTEGER, 8: INTEGER, 9: PERCENT, 10: CURRENCY},
                sum_cols=[2, 3, 4, 5, 10], autofilter=False)

    build_sheet("Supplier scorecard",
                "Suppliers ranked by the risk they carry into your credit",
                ["Grade", "Risk score", "Supplier GSTIN", "Supplier", "State",
                 "Documents", "Value (2B)", "Total ITC", "Match rate",
                 "Unmatched", "ITC at risk", "Avg filing lag (days)",
                 "Late filings", "e-Invoices", "First document", "Last document"],
                [[p["grade"], p["risk_score"], p["gstin"], p["party_name"], p["state"],
                  p["count_2b"], p["val_2b"], p["itc_2b"], p["match_rate"],
                  p["unmatched_count"], p["itc_at_risk"], p["avg_filing_lag"],
                  p["late_filings"], p["e_invoices"], p["first_date"], p["last_date"]]
                 for p in sorted(result["party_wise"], key=lambda x: -x["risk_score"])],
                {2: '0.0', 6: INTEGER, 7: CURRENCY, 8: CURRENCY, 9: PERCENT,
                 10: INTEGER, 11: CURRENCY, 12: '0.0', 13: INTEGER, 14: INTEGER},
                sum_cols=[7, 8, 11])

    cc = insights["concentration"]
    build_sheet("Credit concentration",
                f"{cc['suppliers_for_80pct']} of {cc['supplier_count']} suppliers carry 80% of the credit",
                ["Rank", "Supplier GSTIN", "Supplier", "ITC", "Share",
                 "Cumulative share", "Grade", "Match rate"],
                [[r["rank"], r["gstin"], r["party_name"], r["itc"], r["share"],
                  r["cumulative"], r["grade"], r["match_rate"]] for r in cc["top"]],
                {1: INTEGER, 4: CURRENCY, 5: PERCENT, 6: PERCENT, 8: PERCENT},
                autofilter=False)

    watch = insights["sec164"]
    if watch.get("applicable") and watch.get("top"):
        build_sheet("Section 16(4) watch",
                    f"Credit that lapses after {watch['deadline']} "
                    f"({watch['days_left']} days from the report date)",
                    ["Supplier GSTIN", "Supplier", "Invoice No.", "Invoice Date",
                     "Invoice Value", "Creditable tax", "Age (days)"],
                    [[t["gstin"], t["party"], t["inv_no"], _as_date(t["inv_date"]),
                      t["value"], t["itc"], t["age_days"]] for t in watch["top"]],
                    {4: DATE_FMT, 5: CURRENCY, 6: CURRENCY, 7: INTEGER},
                    sum_cols=[5, 6])

    if insights.get("followups"):
        build_sheet("Follow-up list",
                    "One line per supplier to contact, with the letter body ready to send",
                    ["Supplier", "GSTIN", "Reason", "Documents", "Value", "Tax involved",
                     "Grade", "Email", "Phone", "Subject"],
                    [[p["name"], p["gstin"],
                      "Not filed in 2B" if p["kind"] == "not_filed" else "Does not agree with books",
                      p["count"], p["value"], p["tax"], p["grade"], p["email"],
                      p["phone"], p["subject"]]
                     for p in insights["followups"]],
                    {4: INTEGER, 5: CURRENCY, 6: CURRENCY}, sum_cols=[5, 6],
                    wrap_cols=(10,))


def _write_sales_sheets(wb, build_sheet, sales):
    build_sheet("Sales summary",
                "Tally sales register vs filed GSTR-1",
                ["Status", "Count", "Value (GSTR-1)", "Value (Tally)"],
                [[s["status"], s["count"], s["val_2b"], s["val_tally"]]
                 for s in sales["status_summary"]],
                {2: INTEGER, 3: CURRENCY, 4: CURRENCY}, autofilter=False)

    build_sheet("Sales reconciliation",
                "Invoice-level outward reconciliation — GSTR-1 vs Tally Sales Register",
                ["Status", "Section", "Recipient GSTIN", "Recipient (GSTR-1)",
                 "Ledger (Tally)", "Invoice No. (GSTR-1)", "Vch No. (Tally)",
                 "Invoice Date", "Vch Date", "Value (GSTR-1)", "Amount (Tally)",
                 "Difference", "Taxable", "Tax", "Place of supply"],
                [[r["status"], r["section"], r["gstin"], r["party_gstr1"],
                  r["party_tally"], r["inv_no_gstr1"], r["vch_no_tally"],
                  r["inv_date_gstr1"], r["vch_date_tally"], r["val_gstr1"],
                  r["amt_tally"], r["difference"], r["taxable_gstr1"],
                  r["tax_gstr1"], r["pos"]] for r in sales["all_records"]],
                {8: DATE_FMT, 9: DATE_FMT, 10: CURRENCY, 11: CURRENCY,
                 12: CURRENCY, 13: CURRENCY, 14: CURRENCY},
                sum_cols=[10, 11, 12, 13, 14], status_col=1)


def _write_index(wb, entity_name, entity_gstin, date_range, result):
    """A contents sheet, moved to the front."""
    ws = wb.create_sheet(title="Contents")
    ws.views.sheetView[0].showGridLines = False
    ws["A1"] = "GST reconciliation report"
    ws["A1"].font = FONT_SHEET_TITLE
    ws["A2"] = "  |  ".join(p for p in [entity_name,
                                        f"GSTIN {entity_gstin}" if entity_gstin else "",
                                        date_range] if p)
    ws["A2"].font = FONT_SUBTITLE
    ws["A3"] = f"Generated {datetime.now().strftime('%d %b %Y at %H:%M')}"
    ws["A3"].font = FONT_NOTE

    health = result.get("health") or {}
    if health:
        ws["A5"] = f"{health.get('verdict', '')} — score {health.get('score', '')} / 100"
        ws["A5"].font = FONT_SECTION_HDR

    ws["A7"] = "Sheet"
    ws["B7"] = "What it contains"
    for name in ("A7", "B7"):
        ws[name].font = FONT_TBL_HDR
        ws[name].fill = FILL_HEADER
        ws[name].alignment = ALIGN_LEFT

    blurbs = {
        "Summary": "Control totals, status breakdown, ITC impact and the credit working.",
        "Reconciliation": "Every document, matched and unmatched, in one table.",
        "2B not in Tally": "Credit the supplier filed that your books do not carry.",
        "Tally not in 2B": "Purchases in your books that have not appeared in 2B.",
        "Value mismatch": "Same invoice number, different amount.",
        "Invoice no mismatch": "Party and value agree, the number does not.",
        "Party-wise": "Supplier rollup on a GSTR-2B basis.",
        "Why each is open": "Every open entry, the reason it is open, and what to do.",
        "Probable matches": "Suggested pairs awaiting a human decision.",
        "Split bookings": "One invoice booked across several vouchers.",
        "Superseded rows": "Documents replaced by an amendment row.",
        "ITC working": "From the portal figure down to what is safe to claim.",
        "ITC blocked reasons": "Where the portal blocks the credit, and why.",
        "GSTR-3B working": "Liability set off against available credit.",
        "Action checklist": "What to do next, ordered by recovery.",
        "Data quality": "Defects in the source data.",
        "Possible duplicates": "The same document counted twice.",
        "Ageing": "How long open items have been sitting there.",
        "Rate analysis": "Tax-slab distribution.",
        "Monthly trend": "Month-by-month comparison and match quality.",
        "Supplier scorecard": "Suppliers ranked by the risk they carry.",
        "Credit concentration": "How few suppliers carry most of the credit.",
        "Section 16(4) watch": "Credit that lapses after the statutory cut-off.",
        "Follow-up list": "Suppliers to contact, with letters ready.",
        "Sales summary": "Outward reconciliation headline.",
        "Sales reconciliation": "Invoice-level GSTR-1 vs Tally sales.",
    }

    row = 8
    for sheet in wb.sheetnames:
        if sheet == "Contents":
            continue
        cell = ws.cell(row, 1, sheet)
        cell.font = Font(name=FONT_NAME, size=10, color="1155CC", underline="single")
        cell.hyperlink = f"#'{sheet}'!A1"
        cell.border = BORDER_THIN
        note = ws.cell(row, 2, blurbs.get(sheet, ""))
        note.font = FONT_DATA
        note.border = BORDER_THIN
        note.alignment = ALIGN_LEFT
        row += 1

    ws.column_dimensions["A"].width = 26.0
    ws.column_dimensions["B"].width = 82.0
    wb.move_sheet("Contents", offset=-(len(wb.sheetnames) - 1))
