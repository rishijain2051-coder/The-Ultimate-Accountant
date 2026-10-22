"""
Non-Excel outputs: a PDF working paper, CSV extracts and a zipped follow-up
pack of supplier letters.

The PDF is written to be printed and filed - portrait A4, no colour needed for
comprehension, every figure in the Indian grouping system, and a footer that
says where it came from.
"""

import csv
import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus import Paragraph as _Paragraph

from app.backend.common import inr, iso
from app.backend.explain import why_line

INK = colors.HexColor("#14181C")
MUTED = colors.HexColor("#5A6672")
RULE = colors.HexColor("#C9D1D9")
NAVY = colors.HexColor("#1F3864")
CLEAR = colors.HexColor("#1F7A54")
WARN = colors.HexColor("#9A6A10")
RISK = colors.HexColor("#A8342A")
BAND = colors.HexColor("#F4F6F7")

TONE_COLOR = {"clear": CLEAR, "warn": WARN, "risk": RISK, "info": NAVY,
              "total": INK, "neutral": INK}


def _register_font():
    """Segoe UI, from Windows' own fonts folder, for every PDF the app makes.

    Helvetica, reportlab's built-in face, has no rupee sign, and every ₹ in a
    working paper printed as a black box. Without the font files the PDF is
    still made, in Helvetica, with the sign spelt out (see pdf_text).
    """
    folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    faces = {"SegoeUI": "segoeui.ttf", "SegoeUI-Bold": "segoeuib.ttf",
             "SegoeUI-Italic": "segoeuii.ttf", "SegoeUI-BoldItalic": "segoeuiz.ttf"}
    try:
        for name, file in faces.items():
            pdfmetrics.registerFont(TTFont(name, str(folder / file)))
    except (TTFError, OSError):
        return "Helvetica", "Helvetica-Bold"
    pdfmetrics.registerFontFamily("SegoeUI", normal="SegoeUI", bold="SegoeUI-Bold",
                                  italic="SegoeUI-Italic", boldItalic="SegoeUI-BoldItalic")
    return "SegoeUI", "SegoeUI-Bold"


FONT, BOLD = _register_font()


def pdf_text(text):
    """Text as the PDF's font can draw it: Helvetica has no ₹ and no − sign."""
    if FONT != "Helvetica" or not isinstance(text, str):
        return text
    return text.replace("₹", "Rs.").replace("−", "-")


class Paragraph(_Paragraph):
    """reportlab's Paragraph, its text passed through pdf_text."""

    def __init__(self, text, *args, **kwargs):
        super().__init__(pdf_text(text), *args, **kwargs)


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName=BOLD,
                                fontSize=17, leading=21, textColor=INK,
                                alignment=TA_LEFT, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontName=FONT,
                              fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=1),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=BOLD,
                             fontSize=11.5, leading=14, textColor=NAVY,
                             spaceBefore=13, spaceAfter=5),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontName=FONT,
                               fontSize=9, leading=12.5, textColor=INK),
        "small": ParagraphStyle("sm", parent=ss["Normal"], fontName=FONT,
                                fontSize=7.8, leading=10.5, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontName=FONT,
                               fontSize=7.8, leading=10),
        "lead": ParagraphStyle("l", parent=ss["Normal"], fontName=FONT,
                               fontSize=10, leading=14.5, textColor=INK, spaceAfter=4),
    }


def _table(data, widths, align_right=(), header=True, font_size=7.8, band=True):
    data = [[pdf_text(c) for c in row] for row in data]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
    ]
    if header:
        cmds += [
            ("FONTNAME", (0, 0), (-1, 0), BOLD),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ]
        if band:
            for i in range(2, len(data), 2):
                cmds.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for col in align_right:
        cmds.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(cmds))
    return t


def _footer(entity_name, paper="GSTR-2B vs Tally reconciliation working paper"):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(15 * mm, 14 * mm, doc.pagesize[0] - 15 * mm, 14 * mm)
        canvas.setFont(FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 10 * mm, pdf_text(f"{entity_name} · {paper}"))
        canvas.drawRightString(doc.pagesize[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def build_pdf_report(result, insights, sales=None, orientation="portrait"):
    """Assemble the working paper. Returns a BytesIO positioned at zero."""
    S = _styles()
    entity = result.get("entity_info") or {}
    name = entity.get("entity_name") or "Entity"
    pagesize = landscape(A4) if orientation == "landscape" else A4

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=pagesize,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title=f"GST reconciliation - {name}",
        author="GST Reconcile", subject="GSTR-2B vs Tally purchase reconciliation",
    )
    W = pagesize[0] - 30 * mm
    flow = []

    ct = result["control_totals"]
    health = result.get("health") or {}
    itc = insights["itc_position"]

    # --- cover -------------------------------------------------------
    flow.append(Paragraph("GSTR-2B vs Tally purchase reconciliation", S["title"]))
    flow.append(Paragraph(
        " &nbsp;·&nbsp; ".join(p for p in [
            name,
            f"GSTIN {entity['gstin']}" if entity.get("gstin") else "",
            entity.get("date_range") or entity.get("fy_label") or "",
        ] if p), S["sub"]))
    flow.append(Paragraph(
        f"Prepared {datetime.now().strftime('%d %b %Y at %H:%M')}"
        + (f" by {entity['preparer']}" if entity.get("preparer") else ""), S["small"]))
    flow.append(Spacer(1, 7 * mm))

    verdict = health.get("verdict", "")
    flow.append(Paragraph(
        f"<b>{verdict}.</b> {ct['invoices_in_2b']:,} documents in GSTR-2B worth "
        f"{inr(ct['invoice_value_2b'])} were compared with {ct['vouchers_in_tally']:,} "
        f"Tally vouchers worth {inr(ct['value_in_tally'])}. "
        f"{result['counts']['matched']:,} agree on both number and value "
        f"({result['rates']['clean_match_rate']}%). "
        f"{inr(itc['claimable_now'])} of input tax credit is safe to claim as the books "
        f"stand; {inr(itc['recoverable_by_booking'])} more becomes available once the "
        f"{result['counts']['gst_only']} unbooked invoices are recorded.", S["lead"]))
    flow.append(Spacer(1, 3 * mm))

    # --- control totals ---------------------------------------------
    flow.append(Paragraph("Control totals", S["h2"]))
    flow.append(_table([
        ["", "Count", "Value", "Tax / ITC"],
        ["As per GSTR-2B", f"{ct['invoices_in_2b']:,}",
         inr(ct["invoice_value_2b"]), inr(ct["total_itc_2b"])],
        ["As per Tally", f"{ct['vouchers_in_tally']:,}",
         inr(ct["value_in_tally"]), "—"],
        ["Difference", f"{ct['invoices_in_2b'] - ct['vouchers_in_tally']:+,}",
         inr(ct["difference"]), "—"],
    ], [W * 0.40, W * 0.16, W * 0.22, W * 0.22], align_right=(1, 2, 3), font_size=8.6))

    # --- status breakdown -------------------------------------------
    flow.append(Paragraph("Reconciliation status", S["h2"]))
    rows = [["Status", "Count", "Value (2B)", "Value (Tally)", "Tax involved"]]
    for s in result["status_summary"]:
        if s["key"] == "total":
            rows.append(["Total", f"{s['count']:,}", inr(s["val_2b"]),
                         inr(s["val_tally"]), inr(s.get("tax"))])
        elif s["count"]:
            rows.append([s["status"], f"{s['count']:,}", inr(s["val_2b"]),
                         inr(s["val_tally"]), inr(s.get("tax"))])
    t = _table(rows, [W * 0.36, W * 0.11, W * 0.19, W * 0.19, W * 0.15],
               align_right=(1, 2, 3, 4), font_size=8.4)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), BOLD),
        ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.8, INK),
        ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.white),
    ]))
    flow.append(t)

    # --- credit working ---------------------------------------------
    flow.append(Paragraph("Input tax credit working", S["h2"]))
    rows = [["Step", "Amount"]]
    style_extra = []
    for i, row in enumerate(itc["ladder"], start=1):
        if row["amount"] == 0 and row["tone"] not in ("total", "clear", "neutral"):
            continue
        rows.append([row["label"], inr(row["amount"])])
        if row["tone"] in ("total", "clear"):
            idx = len(rows) - 1
            style_extra += [
                ("FONTNAME", (0, idx), (-1, idx), BOLD),
                ("LINEABOVE", (0, idx), (-1, idx), 0.7, INK),
                ("TEXTCOLOR", (0, idx), (-1, idx), TONE_COLOR[row["tone"]]),
            ]
    t = _table(rows, [W * 0.62, W * 0.38], align_right=(1,), font_size=8.6, band=False)
    t.setStyle(TableStyle(style_extra))
    flow.append(t)
    allowed = itc["heads_allowed"]
    flow.append(Paragraph(
        "Credit allowed by law, by head (what the GSTR-3B working sets off): IGST " +
        inr(allowed["igst"]) + " &nbsp;·&nbsp; CGST " + inr(allowed["cgst"]) +
        " &nbsp;·&nbsp; SGST " + inr(allowed["sgst"]) + " &nbsp;·&nbsp; Cess " +
        inr(allowed["cess"]), S["small"]))

    # --- what to do next --------------------------------------------
    if insights["checklist"]:
        flow.append(Paragraph("What to do next", S["h2"]))
        rows = [["#", "Action", "Items", "Amount"]]
        for i, item in enumerate(insights["checklist"], start=1):
            rows.append([
                str(i),
                Paragraph(f"<b>{item['title']}</b><br/>"
                          f"<font color='#5A6672'>{item['detail']}</font>", S["cell"]),
                f"{item['count']:,}", inr(item["amount"]),
            ])
        flow.append(_table(rows, [W * 0.05, W * 0.63, W * 0.12, W * 0.20],
                           align_right=(2, 3)))

    flow.append(PageBreak())

    # --- top exposures ----------------------------------------------
    flow.append(Paragraph("Largest open items", S["h2"]))
    flow.append(Paragraph(
        "Ranked by the input tax credit riding on them. Clear these first.", S["small"]))
    flow.append(Spacer(1, 2 * mm))
    rows = [["Status", "Supplier", "Document", "Date", "Value", "Tax", "Age"]]
    for e in insights["top_exposures"][:22]:
        rows.append([
            Paragraph(e["status"], S["cell"]),
            Paragraph(str(e["party"] or "")[:38], S["cell"]),
            Paragraph(str(e["doc_no"] or "")[:20], S["cell"]),
            _fmt_date(e["date"]), inr(e["value"]), inr(e["tax"]),
            f"{e['age_days']}d" if e["age_days"] is not None else "—",
        ])
    flow.append(_table(rows, [W * 0.15, W * 0.26, W * 0.15, W * 0.10,
                              W * 0.14, W * 0.13, W * 0.07],
                       align_right=(4, 5, 6)))

    # --- ageing ------------------------------------------------------
    flow.append(Paragraph("Ageing of open items", S["h2"]))
    rows = [["Bucket", "Items", "Value", "Tax involved"]]
    for a in insights["aging"]:
        if not a["count"]:
            continue
        rows.append([a["bucket"], f"{a['count']:,}", inr(a["value"]), inr(a["tax"])])
    if len(rows) > 1:
        flow.append(_table(rows, [W * 0.34, W * 0.16, W * 0.25, W * 0.25],
                           align_right=(1, 2, 3), font_size=8.4))

    # --- data quality -------------------------------------------------
    if insights["quality"]["checks"]:
        flow.append(Paragraph("Source data findings", S["h2"]))
        rows = [["Severity", "Finding", "Rows", "Amount"]]
        for c in insights["quality"]["checks"]:
            rows.append([
                c["tone"].title(),
                Paragraph(f"<b>{c['title']}</b><br/>"
                          f"<font color='#5A6672'>{c['detail']}</font>", S["cell"]),
                f"{c['count']:,}", inr(c["amount"]),
            ])
        flow.append(_table(rows, [W * 0.10, W * 0.62, W * 0.12, W * 0.16],
                           align_right=(2, 3)))

    flow.append(PageBreak())

    # --- monthly ------------------------------------------------------
    flow.append(Paragraph("Month by month", S["h2"]))
    rows = [["Month", "Value (2B)", "Value (Tally)", "Variance", "Tax (2B)",
             "Docs", "Matched", "Match rate", "ITC at risk"]]
    for m in insights["monthly"]:
        rows.append([m["label"], inr(m["val_2b"], 0), inr(m["val_tally"], 0),
                     inr(m["variance"], 0), inr(m["tax"], 0), f"{m['total']:,}",
                     f"{m['matched']:,}", f"{m['match_rate']:.1f}%",
                     inr(m["at_risk"], 0)])
    flow.append(_table(rows, [W * 0.09, W * 0.145, W * 0.145, W * 0.12, W * 0.12,
                              W * 0.07, W * 0.08, W * 0.09, W * 0.13],
                       align_right=(1, 2, 3, 4, 5, 6, 7, 8), font_size=7.4))

    # --- rate analysis -------------------------------------------------
    flow.append(Paragraph("Tax slabs", S["h2"]))
    rows = [["Slab", "Documents", "Taxable value", "Tax", "Share of credit"]]
    for r in insights["rate_analysis"]:
        rows.append([r["slab"], f"{r['count']:,}", inr(r["taxable"]), inr(r["tax"]),
                     f"{r['share']:.1f}%"])
    flow.append(_table(rows, [W * 0.18, W * 0.16, W * 0.26, W * 0.24, W * 0.16],
                       align_right=(1, 2, 3, 4), font_size=8.4))

    # --- supplier risk --------------------------------------------------
    flow.append(Paragraph("Suppliers carrying the most risk", S["h2"]))
    rows = [["Grade", "Supplier", "GSTIN", "Docs", "Total ITC",
             "Unmatched", "ITC at risk", "Filing lag"]]
    ranked = sorted(result["party_wise"], key=lambda p: -p["risk_score"])
    for p in ranked[:18]:
        if p["risk_score"] <= 0:
            break
        rows.append([p["grade"], Paragraph(p["party_name"][:34], S["cell"]), p["gstin"],
                     f"{p['count_2b']:,}", inr(p["itc_2b"]),
                     f"{p['unmatched_count']}/{p['count_2b']}",
                     inr(p["itc_at_risk"]),
                     f"{p['avg_filing_lag']:.0f}d" if p["avg_filing_lag"] is not None else "—"])
    if len(rows) > 1:
        flow.append(_table(rows, [W * 0.07, W * 0.24, W * 0.17, W * 0.07,
                                  W * 0.15, W * 0.10, W * 0.13, W * 0.07],
                           align_right=(3, 4, 5, 6, 7), font_size=7.6))

    cc = insights["concentration"]
    flow.append(Paragraph(
        f"{cc['suppliers_for_80pct']} of {cc['supplier_count']} suppliers account for "
        f"80% of the credit; the top ten carry {cc['top_10_share']}%.", S["small"]))

    # --- section 16(4) ----------------------------------------------------
    watch = insights["sec164"]
    if watch.get("applicable") and watch.get("count"):
        flow.append(Paragraph("Section 16(4) time limit", S["h2"]))
        state = ("has already passed" if watch["lapsed"]
                 else f"is {watch['days_left']} days away")
        flow.append(Paragraph(
            f"The outer date for claiming credit of this year is "
            f"<b>{watch['deadline']}</b>, which {state}. "
            f"{inr(watch['amount'])} of creditable tax across {watch['count']} "
            f"documents depends on those purchases being booked first.", S["body"]))

    # --- GSTR-3B -----------------------------------------------------------
    tb = insights["gstr3b"]
    flow.append(Paragraph("GSTR-3B working", S["h2"]))
    if tb["liability_known"]:
        rows = [["Head", "Output liability", "Credit set off", "Payable in cash",
                 "Credit carried forward"]]
        for r in tb["set_off"]:
            rows.append([r["head"], inr(r["liability"]), inr(r["credit_used"]),
                         inr(r["cash"]), inr(r["credit_left"])])
        rows.append(["Total", inr(tb["outward"]["total"]), "",
                     inr(tb["cash_payable"]), inr(tb["credit_carried_forward"])])
        t = _table(rows, [W * 0.20, W * 0.20, W * 0.20, W * 0.20, W * 0.20],
                   align_right=(1, 2, 3, 4), font_size=8.2)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), BOLD),
            ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.8, INK),
        ]))
        flow.append(t)
        flow.append(Paragraph(" ".join(tb["notes"]), S["small"]))
    else:
        flow.append(Paragraph(
            "No sales source was loaded, so the outward liability cannot be computed. "
            "Available credit stands at " + inr(tb["credit"]["total"]) +
            ", of which " + inr(tb["credit"]["claimable"]) + " is safe to claim.",
            S["body"]))

    if sales:
        flow.append(Paragraph("Outward supplies", S["h2"]))
        rows = [["Status", "Count", "Value (GSTR-1)", "Value (Tally)"]]
        for s in sales["status_summary"]:
            rows.append([s["status"], f"{s['count']:,}", inr(s["val_2b"]),
                         inr(s["val_tally"])])
        flow.append(_table(rows, [W * 0.40, W * 0.14, W * 0.23, W * 0.23],
                           align_right=(1, 2, 3), font_size=8.4))

    # --- method ------------------------------------------------------------
    flow.append(Paragraph("How the matching was done", S["h2"]))
    cfg = result.get("config") or {}
    method = [
        f"Invoice and voucher numbers are normalised - case, spaces, slashes and the "
        f"leading zeros of each group are removed - then matched on number and value "
        f"within {inr(cfg.get('tolerance', 2.0))}.",
        "Remaining documents with the same number but a different value are reported "
        "as a value mismatch.",
        "Remaining documents whose value and supplier name agree are reported as an "
        "invoice-number mismatch; a shared run of trailing digits strengthens the match.",
        f"Remaining documents within {cfg.get('value_pct_tolerance', 0.1)}% on value and "
        f"{cfg.get('date_window_days', 45)} days on date, with a similar supplier name, "
        f"are offered as probable matches for a person to confirm.",
        "An invoice whose value equals the sum of two or three vouchers for the same "
        "supplier is reported as a split booking.",
        "Amendment rows supersede the documents they amend and are excluded from every "
        "total; the superseded rows are listed separately.",
        "Anything still unmatched is reported one-sided.",
        "Tally carries no GSTIN column, so supplier identity rests on the ledger name. "
        "Verify the supplier tables before acting on them.",
    ]
    for i, m in enumerate(method, start=1):
        flow.append(Paragraph(f"{i}. {m}", S["body"]))
        flow.append(Spacer(1, 1.2 * mm))

    doc.build(flow, onFirstPage=_footer(name), onLaterPages=_footer(name))
    buf.seek(0)
    return buf


def _fmt_date(val):
    if not val:
        return "—"
    if isinstance(val, str):
        try:
            val = datetime.strptime(val[:10], "%Y-%m-%d")
        except ValueError:
            return val
    return val.strftime("%d-%b-%y")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_VIEWS = {
    "all": ("all_records", None),
    "matched": ("subsets", "matched"),
    "value_mismatch": ("subsets", "value_mismatch"),
    "inv_mismatch": ("subsets", "invoice_no_mismatch"),
    "probable": ("subsets", "probable"),
    "split": ("subsets", "split"),
    "gst_only": ("subsets", "gst_only"),
    "tally_only": ("subsets", "tally_only"),
    "superseded": ("subsets", "superseded"),
}

CSV_COLUMNS = [
    ("status", "Status"), ("section", "2B section"), ("gstin", "Supplier GSTIN"),
    ("party_2b", "Supplier (GSTR-2B)"), ("party_tally", "Ledger (Tally)"),
    ("inv_no_2b", "Invoice No. (2B)"), ("vch_no_tally", "Vch No. (Tally)"),
    ("inv_date_2b", "Invoice Date (2B)"), ("vch_date_tally", "Vch Date (Tally)"),
    ("inv_val_2b", "Invoice Value 2B"), ("amt_tally", "Amount Tally"),
    ("difference", "Difference"), ("taxable_2b", "Taxable Value 2B"),
    ("tax_2b", "Tax (ITC) 2B"), ("eligible_itc", "Creditable tax"),
    ("igst_2b", "IGST"), ("cgst_2b", "CGST"), ("sgst_2b", "SGST"), ("cess_2b", "Cess"),
    ("rate", "Implied rate %"), ("period_2b", "2B Period"),
    ("filing_date", "Supplier filing date"), ("itc_avail", "ITC available"),
    ("reason", "Portal reason"), ("vch_type", "Vch Type"), ("pos", "Place of supply"),
    ("age_days", "Age (days)"), ("aging_bucket", "Ageing bucket"),
    ("match_score", "Match score"), ("match_basis", "Match basis"),
    ("date_gap_days", "Days apart"), ("flags", "Flags"),
    ("note_2b", "Note (2B)"), ("note_tally", "Note (Tally)"),
    ("why", "Why it is open"), ("fp", "Item id"),
]


def build_csv(result, view="all"):
    """One CSV for a named view, UTF-8 with a BOM so Excel opens it cleanly."""
    where, key = CSV_VIEWS.get(view, CSV_VIEWS["all"])
    rows = result[where] if key is None else result["subsets"].get(key, [])

    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([label for _, label in CSV_COLUMNS])
    for r in rows:
        line = []
        for field, _ in CSV_COLUMNS:
            val = r.get(field)
            if isinstance(val, list):
                val = " ".join(val)
            elif field == "why":
                val = why_line(r)
            elif hasattr(val, "strftime"):
                val = iso(val)
            line.append("" if val is None else val)
        w.writerow(line)
    return out.getvalue()


def build_party_csv(result):
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["Grade", "Risk score", "Supplier GSTIN", "Supplier", "State",
                "Documents", "Value (2B)", "Taxable (2B)", "Total ITC",
                "Match rate %", "Matched", "Matched value", "Unmatched",
                "Unmatched value", "ITC at risk", "Avg filing lag",
                "Late filings", "e-Invoices", "Months active",
                "First document", "Last document"])
    for p in result["party_wise"]:
        w.writerow([p["grade"], p["risk_score"], p["gstin"], p["party_name"], p["state"],
                    p["count_2b"], p["val_2b"], p["taxable_2b"], p["itc_2b"],
                    p["match_rate"], p["matched_count"], p["matched_val_tally"],
                    p["unmatched_count"], p["unmatched_val"], p["itc_at_risk"],
                    p["avg_filing_lag"] if p["avg_filing_lag"] is not None else "",
                    p["late_filings"], p["e_invoices"], p["months_active"],
                    p["first_date"] or "", p["last_date"] or ""])
    return out.getvalue()


# ---------------------------------------------------------------------------
# Follow-up pack
# ---------------------------------------------------------------------------

def build_followup_zip(packets, entity=None):
    """A zip with one letter per supplier, plus an index CSV and a merged file
    that can be pasted into a mail-merge."""
    entity = entity or {}
    buf = io.BytesIO()
    stamp = datetime.now().strftime("%Y-%m-%d")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        index = io.StringIO()
        index.write("﻿")
        w = csv.writer(index, lineterminator="\r\n")
        w.writerow(["Supplier", "GSTIN", "Reason", "Documents", "Value",
                    "Tax involved", "Grade", "Email", "Phone", "Subject", "File"])

        merged = []
        used_names = set()
        for p in packets:
            safe = _safe_name(p["name"] or p["gstin"] or "supplier")
            fname = f"letters/{safe}.txt"
            n = 2
            while fname in used_names:
                fname = f"letters/{safe}-{n}.txt"
                n += 1
            used_names.add(fname)
            z.writestr(fname, p["body"])
            merged.append(p["body"])
            w.writerow([p["name"], p["gstin"],
                        "Not filed in GSTR-2B" if p["kind"] == "not_filed"
                        else "Does not agree with books",
                        p["count"], p["value"], p["tax"], p["grade"] or "",
                        p["email"], p["phone"], p["subject"], fname])

        z.writestr("index.csv", index.getvalue())
        z.writestr("all-letters.txt",
                   ("\n\n" + "=" * 78 + "\n\n").join(merged))
        z.writestr("README.txt", "\n".join([
            f"Supplier follow-up pack — {entity.get('entity_name') or 'entity'}",
            f"Generated {stamp}",
            "",
            f"{len(packets)} suppliers have something outstanding.",
            "",
            "  index.csv        one row per supplier, for mail-merge or a tracker",
            "  letters/*.txt    a ready letter per supplier",
            "  all-letters.txt  every letter in one file, for printing",
            "",
            "Letters are drafts. Read them before sending, and add your own",
            "reference numbers and contact details where needed.",
        ]))

    buf.seek(0)
    return buf


def _safe_name(s):
    keep = "".join(c if (c.isalnum() or c in " -_") else "-" for c in str(s))
    return "-".join(keep.split())[:60] or "supplier"
