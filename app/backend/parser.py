"""
Workbook readers for every source this app reconciles.

Nothing here is positional. The GST portal ships a different column order for
B2B, B2BA, CDNR, ISD and IMPG, and a merged workbook loses the two-row header
block altogether, so each sheet is read by building a label for every column
(parent header + sub header, forward-filled across merged cells) and mapping
those labels onto canonical field names. Add a section by adding its keywords.
"""

import re
import zipfile
from collections import Counter
from datetime import datetime
from io import BytesIO

import openpyxl

from app.backend.common import (
    GSTIN_RE, clean_inv, effective_rate, fy_bounds, fy_of, iso, month_key,
    nearest_slab, parse_period, party_core, r2, rate_is_impossible,
    state_of_gstin, tax_split_problem, to_date, to_float,
)

# ---------------------------------------------------------------------------
# Header mapping
# ---------------------------------------------------------------------------

# Ordered most-specific first; the first pattern that matches a column label wins.
FIELD_RULES = [
    ("orig_doc_no",    (r"original\s+(invoice|note|document)\s*(number|no)",)),
    ("orig_doc_date",  (r"original\s+(invoice|note|document)\s*date",)),
    ("gstin",          (r"gstin\s+of", r"^gstin$", r"gstin\s*/\s*uin", r"ctin")),
    ("party",          (r"trade\s*/?\s*legal\s*name", r"legal\s*name", r"receiver\s*name",
                        r"supplier\s*name", r"trade\s*name")),
    ("supply_type",    (r"note\s+supply\s+type", r"document\s+supply\s+type")),
    ("doc_type",       (r"(invoice|note|document)\s*type", r"^type$")),
    ("doc_no",         (r"(invoice|note|document)\s*(number|no\.?)",
                        r"bill\s+of\s+entry\s*(number|no)", r"^(number|no\.?)$",
                        r"vch\.?\s*no", r"voucher\s*(number|no)")),
    ("doc_date",       (r"(invoice|note|document)\s*date", r"bill\s+of\s+entry\s*date",
                        r"icegate\s+reference\s+date", r"vch\.?\s*date",
                        r"voucher\s*date", r"^date$")),
    ("doc_value",      (r"(invoice|note|document)\s*value", r"bill\s+of\s+entry\s*value",
                        r"^value$", r"total\s+invoice\s+value")),
    ("pos",            (r"place\s+of\s+supply", r"^pos$", r"port\s+code")),
    ("rev_charge",     (r"reverse\s+charge",)),
    ("rate",           (r"applicable\s*%\s*of\s*tax\s*rate", r"applicable\s*%",
                        r"rate\s*\(\s*%\s*\)", r"^rate$", r"tax\s*rate")),
    ("taxable",        (r"taxable\s+value", r"taxable\s+amount")),
    ("igst",           (r"integrated\s+tax", r"^igst", r"integrated\s+gst")),
    ("cgst",           (r"central\s+tax", r"^cgst", r"central\s+gst")),
    ("sgst",           (r"state\s*/?\s*ut\s+tax", r"state\s+tax", r"^sgst", r"state\s+gst")),
    ("cess",           (r"cess",)),
    ("filing_date",    (r"filing\s+date", r"date\s+of\s+filing")),
    ("period",         (r"period", r"return\s+period", r"tax\s+period")),
    ("itc_avail",      (r"itc\s+availability", r"itc\s+eligibility", r"eligibility\s+for\s+itc")),
    ("reason",         (r"reason",)),
    ("amended",        (r"amended\s*\(?\s*yes", r"^amended$", r"is\s+amended")),
    ("irn_date",       (r"irn\s*date",)),
    ("irn",            (r"^irn$", r"irn\s+number")),
    ("source_file",    (r"source_?file", r"source\s+file")),
    ("source",         (r"^source$",)),
    ("hsn",            (r"^hsn", r"hsn\s*/?\s*sac", r"sac\s*code")),
    ("description",    (r"description", r"narration", r"particulars")),
    ("uqc",            (r"^uqc$", r"unit\s+of\s+measure")),
    ("qty",            (r"total\s+quantity", r"^quantity$", r"^qty$")),
    ("vch_type",       (r"vch\s*type", r"voucher\s*type")),
    ("debit",          (r"^debit", r"debit\s+amount")),
    ("credit",         (r"^credit", r"credit\s+amount")),
]

_COMPILED = [(field, tuple(re.compile(p) for p in pats)) for field, pats in FIELD_RULES]

# Sheet-name keywords to the 2B section they represent.
SECTION_PATTERNS = [
    ("B2BA", (r"^b2ba", r"b2b.*amend")),
    ("CDNRA", (r"cdnra", r"cdnr.*amend")),
    ("CDNR", (r"cdnr", r"credit.*debit.*note")),
    ("ISDA", (r"isda", r"isd.*amend")),
    ("ISD", (r"^isd",)),
    ("IMPGSEZ", (r"impgsez", r"impg.*sez")),
    ("IMPG", (r"^impg", r"import.*goods")),
    ("IMPS", (r"^imps", r"import.*service")),
    ("B2B", (r"^b2b", r"^sheet1$")),
]

SECTION_LABELS = {
    "B2B": "B2B invoices",
    "B2BA": "B2B amendments",
    "CDNR": "Credit / debit notes",
    "CDNRA": "Credit / debit note amendments",
    "ISD": "ISD credit",
    "ISDA": "ISD amendments",
    "IMPG": "Import of goods",
    "IMPGSEZ": "Import from SEZ",
    "IMPS": "Import of services",
}

# Sections whose tax reduces credit rather than adding to it.
NEGATIVE_DOC_TYPES = ("credit note",)


def _norm_label(val):
    """Clean a header cell into a comparable label fragment."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.lower().startswith("unnamed:"):
        return ""
    s = s.replace("₹", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _load_workbook(file_input):
    """Open a workbook from a path, bytes or file-like object."""
    if isinstance(file_input, bytes):
        src = BytesIO(file_input)
    elif isinstance(file_input, BytesIO):
        file_input.seek(0)
        src = file_input
    else:
        src = str(file_input)
    try:
        return openpyxl.load_workbook(src, data_only=True, read_only=False)
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "That file is not a readable .xlsx workbook. Re-download it from the "
            "portal or re-export it from Tally as Excel, not as .xls or .csv."
        ) from exc


def _row_values(ws, row, width):
    return [ws.cell(row, c).value for c in range(1, width + 1)]


def _find_header(ws, anchors, max_scan=25):
    """Locate the header row by looking for an anchor keyword, then build a
    column label map from that row plus the row beneath it."""
    width = min(ws.max_column or 1, 60)
    scan = min(ws.max_row or 1, max_scan)
    for r in range(1, scan + 1):
        labels = [_norm_label(v).lower() for v in _row_values(ws, r, width)]
        joined = " | ".join(labels)
        if any(a in joined for a in anchors):
            return r, width
    return None, width


def _column_map(ws, header_row, width):
    """Canonical field name -> 1-based column index."""
    parents = [_norm_label(v) for v in _row_values(ws, header_row, width)]
    children = [_norm_label(v) for v in _row_values(ws, header_row + 1, width)] \
        if header_row + 1 <= (ws.max_row or 1) else [""] * width

    # A sub-header row only exists if it holds labels where the parent is blank.
    has_children = any(children[i] and not parents[i] for i in range(width))

    # Forward-fill parent labels across merged cells.
    filled, last = [], ""
    for p in parents:
        if p:
            last = p
        filled.append(last if has_children else p)

    colmap, labels = {}, []
    for i in range(width):
        child = children[i] if has_children else ""
        label = f"{filled[i]} {child}".strip() if child else (parents[i] or "")
        label = re.sub(r"\s+", " ", label).strip().lower()
        labels.append(label)
        if not label:
            continue
        for field, patterns in _COMPILED:
            if field in colmap:
                continue
            if any(p.search(label) for p in patterns):
                colmap[field] = i + 1
                break
    return colmap, labels, (header_row + 1 if has_children else header_row)


def _data_rows(ws, start, cols):
    """Row numbers from start, up to row 200,000. A longer sheet is refused, never cut short."""
    if (ws.max_row or 0) > 200_000:
        raise ValueError("This sheet has more than 200,000 rows. "
                         "Split it into smaller files and try again.")
    yield from range(start, (ws.max_row or 0) + 1)


def _cell(ws, row, colmap, field, default=None):
    col = colmap.get(field)
    if not col:
        return default
    val = ws.cell(row, col).value
    return default if val is None else val


def _text(ws, row, colmap, field, default=""):
    val = _cell(ws, row, colmap, field)
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


# ---------------------------------------------------------------------------
# GSTR-2B
# ---------------------------------------------------------------------------

def _section_of(sheet_name):
    low = str(sheet_name).strip().lower()
    for section, patterns in SECTION_PATTERNS:
        if any(re.search(p, low) for p in patterns):
            return section
    return None


def parse_gstr2b_file(file_input, fy_start_year=None, filename=None, sections=None):
    """
    Read every recognised section of a GSTR-2B workbook.

    Returns {'records': [...], 'metadata': {...}, 'sections': {...}, 'issues': [...]}
    where records are invoice-level dicts with a uniform shape regardless of the
    section they came from.
    """
    wb = _load_workbook(file_input)
    fy_from, fy_to = fy_bounds(fy_start_year) if fy_start_year else (None, None)

    records, issues = [], []
    section_stats = {}
    wanted = set(sections) if sections else None

    for sheet_name in wb.sheetnames:
        section = _section_of(sheet_name)
        if section is None:
            continue
        if wanted and section not in wanted:
            continue
        ws = wb[sheet_name]
        header_row, width = _find_header(ws, ("gstin of", "gstin/uin", "bill of entry", "ctin"))
        if header_row is None:
            issues.append(f"{sheet_name}: no recognisable header row, sheet skipped.")
            continue
        colmap, labels, last_header = _column_map(ws, header_row, width)
        if "gstin" not in colmap and section not in ("IMPG", "IMPGSEZ"):
            issues.append(f"{sheet_name}: no supplier GSTIN column found, sheet skipped.")
            continue

        start = last_header + 1
        count_before = len(records)
        for r in _data_rows(ws, start, colmap.values()):
            rec = _read_2b_row(ws, r, colmap, section, fy_from, fy_to,
                               fy_start_year, filename, sheet_name)
            if rec is not None:
                rec["id"] = len(records)
                records.append(rec)

        added = len(records) - count_before
        if added:
            block = records[count_before:]
            section_stats[section] = {
                "section": section,
                "label": SECTION_LABELS.get(section, section),
                "sheet": sheet_name,
                "count": added,
                "value": r2(sum(x["inv_val"] for x in block)),
                "taxable": r2(sum(x["taxable"] for x in block)),
                "tax": r2(sum(x["tax"] for x in block)),
            }

    wb.close()

    if not records and not issues:
        issues.append("No GSTR-2B rows found. Check that the workbook still has its B2B sheet.")

    return {
        "records": records,
        "sections": section_stats,
        "issues": issues,
        "metadata": _gst_metadata(records),
    }


def _read_2b_row(ws, r, colmap, section, fy_from, fy_to, fy_start_year, filename, sheet_name):
    """One GSTR-2B data row, or None when the row is padding or a repeated header."""
    gstin = str(_text(ws, r, colmap, "gstin")).strip().upper()
    doc_no = _text(ws, r, colmap, "doc_no")

    if section in ("IMPG", "IMPGSEZ"):
        if not doc_no:
            return None
    else:
        if not gstin or not GSTIN_RE.match(gstin):
            return None

    taxable = to_float(_cell(ws, r, colmap, "taxable"))
    igst = to_float(_cell(ws, r, colmap, "igst"))
    cgst = to_float(_cell(ws, r, colmap, "cgst"))
    sgst = to_float(_cell(ws, r, colmap, "sgst"))
    cess = to_float(_cell(ws, r, colmap, "cess"))
    inv_val = to_float(_cell(ws, r, colmap, "doc_value"))

    doc_type = _text(ws, r, colmap, "doc_type", "Regular")
    is_credit_note = any(k in doc_type.lower() for k in NEGATIVE_DOC_TYPES) or section in ("CDNR", "CDNRA")
    supply_type = _text(ws, r, colmap, "supply_type")
    if section in ("CDNR", "CDNRA") and "debit" in doc_type.lower():
        is_credit_note = False

    sign = -1 if is_credit_note else 1
    tax = igst + cgst + sgst + cess
    doc_date = to_date(_cell(ws, r, colmap, "doc_date"))

    if inv_val == 0 and taxable == 0 and tax == 0:
        return None  # spacer row

    itc_raw = _text(ws, r, colmap, "itc_avail", "Yes")
    reason = _text(ws, r, colmap, "reason")
    rate_txt = _text(ws, r, colmap, "rate")
    pct_applicable = 100.0
    m = re.search(r"(\d+(?:\.\d+)?)", rate_txt or "")
    if m:
        pct_applicable = to_float(m.group(1), 100.0)

    eff_rate = effective_rate(taxable, tax)
    slab, rate_ok = nearest_slab(eff_rate)

    notes = []
    flags = []
    if section in ("B2BA", "CDNRA", "ISDA"):
        flags.append("amended")
        notes.append("Amendment row - supersedes the original document.")
    if is_credit_note:
        flags.append("credit_note")
    if section == "ISD":
        flags.append("isd")
    if section in ("IMPG", "IMPGSEZ", "IMPS"):
        flags.append("import")

    rev_charge = _text(ws, r, colmap, "rev_charge", "No")
    if rev_charge.strip().lower().startswith("y"):
        flags.append("rcm")
        notes.append("Reverse charge - tax is payable by the recipient, then claimed.")

    itc_eligible = not itc_raw.strip().lower().startswith("n")
    if not itc_eligible:
        flags.append("itc_blocked")
        hint = reason.strip().lower()
        notes.append(f"Portal marks ITC unavailable: {reason}" if reason
                     else "Portal marks ITC unavailable.")
        del hint
    if pct_applicable and pct_applicable < 100:
        flags.append("partial_itc")
        notes.append(f"Only {pct_applicable:g}% of the tax is creditable.")

    if fy_from and doc_date and doc_date < fy_from:
        flags.append("prior_fy")
        notes.append(f"Document dated before {iso(fy_from)} - belongs to an earlier year.")
    if fy_to and doc_date and doc_date > fy_to:
        flags.append("later_fy")
        notes.append(f"Document dated after {iso(fy_to)} - belongs to a later year.")

    if rate_is_impossible(eff_rate):
        flags.append("rate_invalid")
        notes.append(f"Implied rate {eff_rate:g}% is outside the statutory range "
                     f"- the tax figures on this document do not add up.")
    elif eff_rate is not None and not rate_ok:
        flags.append("mixed_rate")
        notes.append(f"Blended rate {eff_rate:g}% - this document mixes tax slabs. "
                     f"Check the line items if the credit looks wrong.")

    split_issue = tax_split_problem(igst, cgst, sgst)
    if split_issue:
        flags.append("tax_split_error")
        notes.append(split_issue)

    header_diff = r2(inv_val - (taxable + tax))
    if inv_val and abs(header_diff) > max(2.0, abs(inv_val) * 0.005):
        flags.append("value_split_odd")
        notes.append(f"Invoice value is {r2(header_diff):+,.2f} away from taxable value plus tax.")

    src_file = _text(ws, r, colmap, "source_file") or (filename or "")

    return {
        "id": 0,
        "section": section,
        "sheet": sheet_name,
        "gstin": gstin,
        "party": _text(ws, r, colmap, "party"),
        "inv_no": doc_no,
        "inv_type": doc_type,
        "supply_type": supply_type,
        "inv_date": doc_date,
        "inv_val": r2(inv_val * sign),
        "abs_val": r2(abs(inv_val)),
        "pos": _text(ws, r, colmap, "pos"),
        "pos_state": _text(ws, r, colmap, "pos") or state_of_gstin(gstin),
        "rev_charge": rev_charge,
        "taxable": r2(taxable * sign),
        "igst": r2(igst * sign),
        "cgst": r2(cgst * sign),
        "sgst": r2(sgst * sign),
        "cess": r2(cess * sign),
        "tax": r2(tax * sign),
        "rate": eff_rate,
        "rate_slab": slab,
        "pct_applicable": pct_applicable,
        "period": _text(ws, r, colmap, "period"),
        "filing_date": to_date(_cell(ws, r, colmap, "filing_date")),
        "itc_avail": "Yes" if itc_eligible else "No",
        "itc_eligible": itc_eligible,
        "reason": reason,
        "irn": _text(ws, r, colmap, "irn"),
        "irn_date": to_date(_cell(ws, r, colmap, "irn_date")),
        "e_invoice": bool(_text(ws, r, colmap, "source")) or bool(_text(ws, r, colmap, "irn")),
        "source": _text(ws, r, colmap, "source"),
        "source_file": src_file,
        "orig_inv_no": _text(ws, r, colmap, "orig_doc_no"),
        "orig_inv_date": to_date(_cell(ws, r, colmap, "orig_doc_date")),
        "month_key": month_key(doc_date),
        "fy": fy_of(doc_date),
        "flags": flags,
        "note": " ".join(notes),
        "source_row": r,
    }


def _gst_metadata(records):
    live = [r for r in records if "amended" not in r["flags"]]
    return {
        "total_count": len(records),
        "total_invoice_value": r2(sum(r["inv_val"] for r in live)),
        "total_taxable_value": r2(sum(r["taxable"] for r in live)),
        "total_itc": r2(sum(r["tax"] for r in live)),
        "eligible_itc": r2(sum(r["tax"] * (r["pct_applicable"] / 100.0)
                               for r in live if r["itc_eligible"])),
        "blocked_itc": r2(sum(r["tax"] for r in live if not r["itc_eligible"])),
        "rcm_tax": r2(sum(r["tax"] for r in live if "rcm" in r["flags"])),
        "credit_note_tax": r2(sum(r["tax"] for r in records if "credit_note" in r["flags"])),
        "suppliers": len({r["gstin"] for r in records if r["gstin"]}),
        "periods": sorted({r["period"] for r in records if r["period"]}),
    }


def merge_multiple_gstr2b(file_list, fy_start_year=None, filenames=None):
    """Consolidate several monthly 2B workbooks, dropping exact duplicates that
    appear when the same month is downloaded twice."""
    out, seen, issues, sections = [], set(), [], {}
    names = filenames or []

    for idx, f in enumerate(file_list):
        name = names[idx] if idx < len(names) else None
        parsed = parse_gstr2b_file(f, fy_start_year=fy_start_year, filename=name)
        issues.extend(f"{name or 'file ' + str(idx + 1)}: {m}" for m in parsed["issues"])
        for section, stat in parsed["sections"].items():
            agg = sections.setdefault(section, {**stat, "count": 0, "value": 0.0,
                                                "taxable": 0.0, "tax": 0.0})
            agg["count"] += stat["count"]
            agg["value"] = r2(agg["value"] + stat["value"])
            agg["taxable"] = r2(agg["taxable"] + stat["taxable"])
            agg["tax"] = r2(agg["tax"] + stat["tax"])
        for rec in parsed["records"]:
            key = (rec["section"], rec["gstin"], clean_inv(rec["inv_no"]),
                   round(rec["inv_val"], 2), rec["period"])
            if key in seen:
                continue
            seen.add(key)
            rec["id"] = len(out)
            out.append(rec)

    out.sort(key=lambda r: (r["inv_date"] or datetime(1900, 1, 1), r["gstin"]))
    for i, rec in enumerate(out):
        rec["id"] = i

    return {"records": out, "sections": sections, "issues": issues,
            "metadata": _gst_metadata(out)}


# ---------------------------------------------------------------------------
# Tally registers
# ---------------------------------------------------------------------------

TALLY_ANCHORS = ("particulars", "vch no", "voucher no", "vch type")

_TALLY_SKIP = re.compile(
    r"^(amount|opening balance|closing balance|grand total|total|carried over|"
    r"brought forward|by |to )", re.I)


def parse_tally_file(file_input, kind="purchase", filename=None):
    """
    Read a Tally Purchase Register, Sales Register or DayBook export.

    `kind` only affects which of Debit/Credit is treated as the primary amount
    when both carry a figure; single-sided rows are read the same either way.
    """
    wb = _load_workbook(file_input)

    preferred = {
        "purchase": ("purchase register", "purchase", "daybook", "day book"),
        "sales": ("sales register", "sales", "daybook", "day book"),
    }.get(kind, ("daybook",))

    ws = None
    for hint in preferred:
        for name in wb.sheetnames:
            if hint in name.strip().lower():
                ws = wb[name]
                break
        if ws is not None:
            break
    if ws is None:
        ws = wb[wb.sheetnames[0]]

    header_row, width = _find_header(ws, TALLY_ANCHORS, max_scan=30)
    entity_name, date_range, extra_lines = "", "", []
    scan_to = (header_row - 1) if header_row else min(6, ws.max_row or 1)
    for r in range(1, max(1, scan_to) + 1):
        first = _norm_label(ws.cell(r, 1).value)
        if not first:
            continue
        low = first.lower()
        if re.search(r"\d.*\bto\b.*\d", low) or re.search(r"(apr|jan|mar|dec)", low) and " to " in low:
            date_range = first
        elif not entity_name and not low.startswith(("purchase register", "sales register", "day book")):
            entity_name = first
        else:
            extra_lines.append(first)

    if header_row is None:
        wb.close()
        raise ValueError(
            "Could not find the column headers in this Tally export. Export the "
            "Purchase Register with columns Date, Particulars, Vch Type, Vch No., "
            "Debit and Credit."
        )

    colmap, labels, last_header = _column_map(ws, header_row, width)
    # Tally repeats the word "Amount" under Debit/Credit; that is a sub-header,
    # not data, and _column_map already consumed it when it forward-filled.
    date_col = colmap.get("doc_date") or 1
    party_col = colmap.get("description") or 2

    records, skipped = [], 0
    for r in _data_rows(ws, last_header + 1, [date_col, party_col, *colmap.values()]):
        party =_norm_label(ws.cell(r, party_col).value)
        date_raw = ws.cell(r, date_col).value
        dt = to_date(date_raw)

        if not party and dt is None:
            continue
        if party and _TALLY_SKIP.match(party):
            if party.lower().startswith(("grand total", "total")):
                break
            skipped += 1
            continue
        if not party:
            skipped += 1
            continue

        debit = to_float(_cell(ws, r, colmap, "debit"))
        credit = to_float(_cell(ws, r, colmap, "credit"))
        if kind == "sales":
            amt = debit if debit else credit
        else:
            amt = credit if credit else debit
        if not amt:
            amt = to_float(_cell(ws, r, colmap, "doc_value"))
        if not amt:
            skipped += 1
            continue

        vch_type = _text(ws, r, colmap, "vch_type")
        vch_no = _text(ws, r, colmap, "doc_no")
        gstin = str(_text(ws, r, colmap, "gstin")).upper()

        flags, notes = [], []
        vt_low = vch_type.lower()
        if vt_low == "purchase":
            flags.append("non_gst_vch")
            notes.append("Booked under a plain Purchase voucher - no GST ledger attached.")
        if "credit note" in vt_low:
            flags.append("credit_note")
        if "debit note" in vt_low:
            flags.append("debit_note")
        if not vch_no:
            flags.append("no_vch_no")
            notes.append("Voucher has no number - it can only be matched on party and value.")
        if gstin and not GSTIN_RE.match(gstin):
            gstin = ""

        records.append({
            "id": len(records),
            "date": dt,
            "party": party,
            "party_core": party_core(party),
            "gstin": gstin,
            "vch_type": vch_type,
            "vch_no": vch_no,
            "amt": r2(amt),
            "debit": r2(debit),
            "credit": r2(credit),
            "month_key": month_key(dt),
            "fy": fy_of(dt),
            "narration": _text(ws, r, colmap, "description") if party_col != colmap.get("description") else "",
            "flags": flags,
            "note": " ".join(notes),
            "source_row": r,
            "source_file": filename or "",
        })

    wb.close()

    dates = [x["date"] for x in records if x["date"]]
    stated_from, stated_to = parse_period(date_range or " ".join(extra_lines))
    fy_counts = Counter(fy_of(d) for d in dates if fy_of(d) is not None)
    return {
        "metadata": {
            "entity_name": entity_name or "Tally Entity",
            "date_range": date_range,
            "period_from": iso(stated_from),
            "period_to": iso(stated_to),
            # The financial year most of the vouchers fall in, so a stray
            # voucher dated outside the period cannot decide it.
            "fy_detected": fy_counts.most_common(1)[0][0] if fy_counts else None,
            "extra_lines": extra_lines[:4],
            "sheet": ws.title,
            "kind": kind,
            "total_count": len(records),
            "total_amount": r2(sum(x["amt"] for x in records)),
            "rows_skipped": skipped,
            "first_date": iso(min(dates)) if dates else None,
            "last_date": iso(max(dates)) if dates else None,
            "vch_types": sorted({x["vch_type"] for x in records if x["vch_type"]}),
            "columns_found": sorted(colmap.keys()),
        },
        "records": records,
    }


def parse_tally_sales_file(file_input, filename=None):
    return parse_tally_file(file_input, kind="sales", filename=filename)


# ---------------------------------------------------------------------------
# GSTR-1 (filed outward supplies)
# ---------------------------------------------------------------------------

GSTR1_SECTIONS = [
    ("B2B", (r"b2b", r"b2b.*sez.*de")),
    ("B2CL", (r"b2cl",)),
    ("B2CS", (r"b2cs",)),
    ("CDNR", (r"^cdnr",)),
    ("CDNUR", (r"cdnur",)),
    ("EXP", (r"^exp",)),
    ("HSN", (r"^hsn",)),
    ("NIL", (r"exemp", r"^nil",)),
]


def parse_gstr1_file(file_input, fy_start_year=None, filename=None):
    """Read a filed GSTR-1 workbook and return outward supply records plus a
    per-section rollup used for the GSTR-3B working."""
    wb = _load_workbook(file_input)
    records, issues, sections = [], [], {}

    for sheet_name in wb.sheetnames:
        low = sheet_name.strip().lower()
        section = None
        for sec, pats in GSTR1_SECTIONS:
            if any(re.search(p, low) for p in pats):
                section = sec
                break
        if section is None:
            continue
        ws = wb[sheet_name]
        header_row, width = _find_header(
            ws, ("gstin/uin of recipient", "gstin of", "invoice number", "hsn",
                 "place of supply", "taxable value"), max_scan=20)
        if header_row is None:
            issues.append(f"{sheet_name}: no header row found, skipped.")
            continue
        colmap, labels, last_header = _column_map(ws, header_row, width)

        block = []
        for r in _data_rows(ws, last_header + 1, colmap.values()):
            taxable = to_float(_cell(ws, r, colmap, "taxable"))
            igst = to_float(_cell(ws, r, colmap, "igst"))
            cgst = to_float(_cell(ws, r, colmap, "cgst"))
            sgst = to_float(_cell(ws, r, colmap, "sgst"))
            cess = to_float(_cell(ws, r, colmap, "cess"))
            doc_val = to_float(_cell(ws, r, colmap, "doc_value"))
            if taxable == 0 and igst == 0 and cgst == 0 and sgst == 0 and doc_val == 0:
                continue
            doc_no = _text(ws, r, colmap, "doc_no")
            dt = to_date(_cell(ws, r, colmap, "doc_date"))
            sign = -1 if section in ("CDNR", "CDNUR") and \
                "credit" in _text(ws, r, colmap, "doc_type").lower() else 1
            rec = {
                "id": len(records),
                "section": section,
                "gstin": str(_text(ws, r, colmap, "gstin")).upper(),
                "party": _text(ws, r, colmap, "party"),
                "inv_no": doc_no,
                "inv_date": dt,
                "inv_val": r2(doc_val * sign),
                "taxable": r2(taxable * sign),
                "igst": r2(igst * sign), "cgst": r2(cgst * sign),
                "sgst": r2(sgst * sign), "cess": r2(cess * sign),
                "tax": r2((igst + cgst + sgst + cess) * sign),
                "rate": to_float(_cell(ws, r, colmap, "rate")) or effective_rate(taxable, igst + cgst + sgst),
                "pos": _text(ws, r, colmap, "pos"),
                "hsn": _text(ws, r, colmap, "hsn"),
                "month_key": month_key(dt),
                "source_file": filename or "",
                "source_row": r,
            }
            records.append(rec)
            block.append(rec)

        if block:
            sections[section] = {
                "section": section, "sheet": sheet_name, "count": len(block),
                "value": r2(sum(x["inv_val"] for x in block)),
                "taxable": r2(sum(x["taxable"] for x in block)),
                "tax": r2(sum(x["tax"] for x in block)),
            }

    wb.close()
    if not records and not issues:
        issues.append("No GSTR-1 rows recognised in this workbook.")

    return {
        "records": records,
        "sections": sections,
        "issues": issues,
        "metadata": {
            "total_count": len(records),
            "total_value": r2(sum(x["inv_val"] for x in records)),
            "total_taxable": r2(sum(x["taxable"] for x in records)),
            "total_tax": r2(sum(x["tax"] for x in records)),
            "igst": r2(sum(x["igst"] for x in records)),
            "cgst": r2(sum(x["cgst"] for x in records)),
            "sgst": r2(sum(x["sgst"] for x in records)),
            "cess": r2(sum(x["cess"] for x in records)),
        },
    }


def probe_workbook(file_input):
    """Describe a workbook without committing to a parser - used by the upload
    UI to tell the user what it thinks they handed it."""
    wb = _load_workbook(file_input)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        gst_section = _section_of(name)
        out.append({
            "sheet": name,
            "rows": ws.max_row,
            "cols": ws.max_column,
            "gstr2b_section": gst_section,
            "looks_like_tally": _find_header(ws, TALLY_ANCHORS, max_scan=30)[0] is not None,
        })
    wb.close()
    kinds = set()
    for s in out:
        if s["gstr2b_section"]:
            kinds.add("gstr2b")
        if s["looks_like_tally"]:
            kinds.add("tally")
    return {"sheets": out, "detected": sorted(kinds)}
