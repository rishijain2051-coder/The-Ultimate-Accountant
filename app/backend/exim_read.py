"""
Reading export documents: what each file is, and what it says.

Every reader returns a plain dict - {"type": ..., fields..., "confidence",
"notes"} - and the engine in exim.py only ever sees those, never a file.

The layouts were learnt from real documents: ICEGATE shipping bill copies (a
text PDF, one header block repeated on every page, Part II for invoices, Part
III for items and IGST, Part IV H for the invoice summary), a forwarder's house
bill of lading (a scan that carries "INV NO" and "SB NO" in its description),
and an AD bank's foreign inward remittance advice. An exporter's own
shipping-bill sheet (one tab per year: invoice, container, shipping bill, GST
amount and when it came back, drawback and when it came back, the ICEGATE
error code) and a table copied from ICEGATE's drawback pages taught the last
two readers. Nothing from those files is kept; only the shape of them is
encoded here.
"""

import io
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from app.backend import docs
from app.backend.common import clean_inv, iso, r2, to_date, to_float

CURRENCIES = ("USD EUR GBP AED JPY AUD CAD CHF SGD CNY SAR HKD NZD SEK DKK NOK ZAR "
              "QAR KWD OMR BHD THB MYR RUB INR").split()
_CUR = "|".join(CURRENCIES)
_DATE = r"\d{1,2}[./-](?:\d{1,2}|[A-Za-z]{3})[./-]\d{2,4}"
# A real calendar date only. Invoice numbers carry financial years -
# KE/25-26/014 - and "25-26/014" must not be read as the 25th of month 26.
_DATE_STRICT = (r"(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2]|[A-Za-z]{3})[./-]"
                r"(?:\d{4}|\d{2})(?!\d)")
TERMS = {"FOB", "CIF", "CI", "CF", "CFR", "CNF", "CIP", "DAP", "DDP", "EXW", "FCA", "CPT", "DPU"}
GST_RATES = (0.25, 1.5, 3, 5, 12, 18, 28)


def _num(s):
    return to_float(str(s).replace(",", "")) if s not in (None, "") else None


def _amt(s):
    """A money amount as a scan gives it: 50,834.00, or 42.643.00 with a dot
    where the comma was. The last separator before one or two digits is the
    decimal point; every other one groups thousands."""
    m = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,](\d{1,2}))?", str(s or "").strip())
    return float(re.sub(r"\D", "", m.group(1)) + "." + (m.group(2) or "0")) if m else _num(s)


def _date(s):
    if isinstance(s, str):
        s = s.strip().rstrip(".'")                 # hand-typed dates end in stray marks
        if re.fullmatch(r"\d{1,2}[.-]\d{1,2}[.-]\d{2,4}", s):
            s = re.sub(r"[.-]", "/", s)
    return iso(to_date(s))


def _first(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def _line_after(label, text, skip=r"^\W*$"):
    """The first meaningful line after a label line - names and addresses sit
    one line below their heading on every document seen so far."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(label, line, re.I):
            for nxt in lines[i + 1:i + 4]:
                cand = re.sub(r"\s+", " ", nxt).strip(" :-")
                # A lone letter is a stray mark from the text layer: real
                # ICEGATE bills put one between Part II's heading and the names.
                if cand and not re.match(skip, cand) and len(re.sub(r"\W", "", cand)) > 1:
                    return cand
    return None


def ocr_key(inv):
    """An invoice number reduced for linking documents to each other.

    Recognition confuses O with 0 and I or l with 1, and a scanned bill of
    lading says "Vl-0605" for VI-0605. Folding those is safe for linking,
    which always has other evidence behind it; it is never used to decide
    whether GSTR-1 and the shipping bill agree, which must be exact.
    """
    k = clean_inv(inv)
    return k.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


# ---------------------------------------------------------------------------
# What is this file?
# ---------------------------------------------------------------------------

def read(data, filename=""):
    """Recognise one file and read it. Never raises for an unfamiliar file:
    it comes back as type "unknown" with a note saying why."""
    try:
        if data.lstrip()[:1] in (b"{", b"["):
            try:
                return read_gstr1_json(json.loads(data.decode("utf-8-sig")))
            except (ValueError, UnicodeDecodeError):
                pass
        kind = docs.sniff(data, filename)
        if kind in ("pdf", "image"):
            got = docs.read_document(data, filename)
            out = from_text(got.get("text") or "")
            if out["type"] == "unknown" and got.get("grid"):
                # A bank statement or an ICEGATE page printed to PDF is a table
                # (its pages stacked into one).
                tabled = from_grids({"pdf": got["grid"]})
                out = tabled if tabled["type"] != "unknown" else out
            if out["type"] == "unknown" and got.get("method") == "text" and docs.ocr_available() \
                    and _text_layer_unreliable(data, got.get("text") or ""):
                # The page image still reads. Two pages say what a document
                # is; a whole 40-page read of every PDF that simply is not an
                # export document cost 6 s a file on real downloads, 28 s at
                # worst, to end "not recognised" all the same.
                got = docs.read_document(data, filename, force_ocr=True, max_pages=2)
                out = from_text(got.get("text") or "")
            if out["type"] == "sb" and got.get("method") == "text" and kind == "pdf":
                _apply_boxes(out, _sb_boxes(data))
            out["read_by"] = "recognition" if got.get("method") in ("ocr", "mixed") else "text"
            if got.get("confidence") is not None:
                out["ocr_confidence"] = round(got["confidence"], 3)
            return out
        if kind in ("excel", "csv", "xls_legacy"):
            return from_grids(_grids(data, kind))
        return _unknown(f"Not a document type this module reads ({kind}).")
    except ValueError as exc:
        return _unknown(str(exc))


def _unknown(why):
    return {"type": "unknown", "confidence": 0.0, "notes": [why]}


def _text_layer_unreliable(data, text):
    """Whether a PDF's text is worth reading again from the picture: the page is
    a scan with a scanner's text laid over it, or the text is not words at all
    (a font with no character map gives "(cid:12)" codes). On real downloads
    the scanned bills of lading were whole-page pictures with 7% real words;
    PDFs that were simply not export documents had half their tokens as real
    words and pictures on a seventh of the page at most."""
    tokens = text.split()
    words = sum(1 for w in tokens if re.fullmatch(r"[A-Za-z]{3,}", w) and re.search(r"[AEIOUaeiou]", w))
    if "(cid:" in text or words < 0.2 * max(len(tokens), 1):
        return True
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page = pdf.pages[0]
            area = float(page.width * page.height) or 1.0
            return sum(abs((i["x1"] - i["x0"]) * (i["bottom"] - i["top"])) for i in page.images) >= 0.5 * area
    except Exception:                                 # noqa: BLE001 - unsure: read the picture
        return True


def _grids(data, kind):
    if kind == "excel":
        return docs.read_excel_grid(data)
    if kind == "csv":
        return {"csv": docs.read_csv_grid(data)}
    try:
        import xlrd
    except ImportError as exc:
        raise ValueError("That is an old .xls file; save it as .xlsx and add it again.") from exc
    book = xlrd.open_workbook(file_contents=data)
    return {s.name: [s.row_values(i) for i in range(s.nrows)] for s in book.sheets()}


def from_text(text):
    # Scans give full-width colons and digits (："), and run words together:
    # a Kotak advice's title comes back as "RemittanceTransactionAdvice".
    t = unicodedata.normalize("NFKC", text or "")
    squeezed = re.sub(r"[^A-Z]", "", t.upper())
    if re.search(r"INDIAN\s+CUSTOMS\s+EDI\s+SYSTEM", t, re.I) and re.search(
            r"SHIPPING\s+BILL|PART\s*-\s*I", t, re.I):
        return read_sb(t)
    if re.search(r"\bFIR[AC]\b", t, re.I) or re.search(r"INWARDREMITTANCE|REMITTANCE(?:TRANSACTION)?ADVICE", squeezed) \
            or ("PURPOSEOFREMITTANCE" in squeezed and "REMITTER" in squeezed):
        return read_fira(t)
    if re.search(r"BILL\s*OF\s*LADING|\bB\s*/?\s*L\s*NO|H\s*/\s*BL\s*NO|SHIPPED\s*ON\s*BOARD|"
                 r"AIR\s*WAY\s*BILL|\bH?AWB\s*NO|MULTIMODAL\s*TRANSPORT", t, re.I):
        return read_bl(t)
    if re.search(r"INVOICE", t, re.I) and re.search(r"EXPORT|\bIEC\b|CONSIGNEE", t, re.I):
        return read_invoice(t)
    return _unknown("Could not tell what this document is from its text.")


def from_grids(grids):
    found = [g for g in (_grid_kind(name, grid) for name, grid in grids.items()) if g]
    own = [g for g in found if g["type"] == "own_register"]
    if own:
        # One tab per year: every tab's rows, and ICEGATE's drawback list if a
        # tab holds one.
        doc = {"type": "own_register", "rows": [r for g in own for r in g["rows"]],
               "sheets": [g["sheet"] for g in own], "notes": [], "confidence": max(g["confidence"] for g in own)}
        pending = [r for g in found if g["type"] == "dbk_pending" for r in g["rows"]]
        if pending:
            doc["dbk_pending"] = pending
        return doc
    best = max(found, key=lambda g: g.get("confidence", 0), default=None)
    return best or _unknown("A spreadsheet, but not one this module recognises.")


# ---------------------------------------------------------------------------
# Shipping bill (ICEGATE copy)
# ---------------------------------------------------------------------------

_SB_HEAD = re.compile(r"(IN[A-Z0-9]{4})\s+(\d{6,8})\s+(\d{1,2}-[A-Z]{3}-\d{2,4})")
_INV_ROW = re.compile(r"^\s*(\d{1,2})\s+([A-Z0-9][A-Z0-9/\-\.]*[A-Z0-9])\s+(\d{2}/\d{2}/\d{4})(.*)$",
                      re.M)
_VALUE_ROW = re.compile(r"^\s*([\d.]+)\s+([\d.]+)\s+(?:[\d.]+\s+)*?(" + _CUR + r")\s+INR\s+([\d.]+)",
                        re.M)
_ITEM_ROW = re.compile(r"^\s*(\d{1,2})\s+(\d{1,3})\s+(\d{8})")
_DECIMAL = re.compile(r"\d+\.\d+")


def _igst_pair(line):
    """IGST value and amount from the line under Part III's IGST headings.

    Duty and cess columns are usually blank on exports, but when one is filled
    it shifts the numbers along, so the pair is chosen where amount / value is
    a real GST rate rather than by position alone.
    """
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", line)]
    for i in range(len(nums) - 1):
        v, a = nums[i], nums[i + 1]
        if v > 0 and a > 0 and any(abs(a / v * 100 - g) < 0.06 for g in GST_RATES):
            return v, a
    if len(nums) >= 2 and re.search(r"\b(LUT|NP|NA)\b", line):
        return nums[0] if nums[0] > 1 else 0.0, 0.0
    return (nums[0], nums[1]) if len(nums) >= 3 else (None, None)


def read_sb(text):
    t = text
    out = {"type": "sb", "notes": [], "invoices": [], "containers": []}
    head = re.search(r"INDIAN\s+CUSTOMS\s+EDI\s+SYSTEM\s+" + _SB_HEAD.pattern, t) or _SB_HEAD.search(t)
    if head:
        out["port"], out["sb_no"], out["sb_date"] = head.group(1), head.group(2), _date(head.group(3))
    out["iec"] = _first(r"IEC\s*(?:/\s*Br)?\s*[:.]?\s*([0-9A-Z]{10})\b", t)
    out["gstin"] = _first(r"GSTIN\s*/\s*TYPE\s*[:.]?\s*(\d{2}[A-Z0-9]{13})", t)
    out["mode"] = _first(r"\b(SEA|AIR|LAND|RAIL|POSTAL|COURIER)\b", t)
    out["ad_code"] = _first(r"AD\s*CODE\s*:?\s*(\d{7})", t)
    out["country"] = _first(r"COUNTRY\s*OF\s*FINAL\s*DESTINATION\s*([A-Z][A-Z .]*[A-Z])", t)
    out["discharge_port"] = _first(r"PORT\s*OF\s*FINAL\s*DESTINATION\s+([A-Z]{5})", t)
    out["exporter"] = _line_after(r"EXPORTER'?S\s*NAME\s*&\s*ADDRESS", t)
    consignee = _line_after(r"CONSIGNEE'?S?\s*NAME\s*&\s*ADDRESS", t)
    buyer = _line_after(r"BUYER'?S\s*NAME\s*&\s*ADDRESS", t)
    # Part II prints the exporter's and the buyer's names side by side under
    # one heading line, so the line below starts with the exporter - cut off
    # word by word, as the exporter's line in Part I can carry a stray letter.
    if buyer and re.search(r"SAME\s+AS\s+CONSIGNEE", buyer, re.I):
        buyer = None
    elif buyer and out["exporter"] and buyer.upper().startswith(out["exporter"].upper()[:12]):
        words, ex = buyer.split(), out["exporter"].upper().split()
        n = next((i for i, (a, b) in enumerate(zip(words, ex)) if a.upper() != b), min(len(words), len(ex)))
        buyer = " ".join(words[n:]) or None
    out["buyer"] = (buyer or consignee or "").split("  ")[0].strip() or None
    out["submitted"] = _date(_first(r"Submission\s+(\d{1,2}-[A-Z]{3}-\d{2,4})", t, flags=0) or "")
    out["leo_date"] = _date(_first(r"\bLEO\s+(\d{1,2}-[A-Z]{3}-\d{2,4})", t, flags=0) or "")
    out["containers"] = sorted(set(re.findall(r"([A-Z]{3}U\d{7})", t)))

    # Part II: invoice rows, each followed by its value line.
    rows = [m for m in _INV_ROW.finditer(t) if not re.fullmatch(r"\d+", m.group(2))]
    values = list(_VALUE_ROW.finditer(t))
    for i, m in enumerate(rows):
        tail = m.group(4).split()
        inv = {"sno": int(m.group(1)), "inv_no": m.group(2), "inv_date": _date(m.group(3)),
               "terms": next((w for w in reversed(tail) if w.upper() in TERMS), None)}
        if i < len(values):
            v = values[i]
            inv.update(value_fc=_num(v.group(1)), fob_fc=_num(v.group(2)),
                       currency=v.group(3), rate=_num(v.group(4)))
        out["invoices"].append(inv)

    # Part IV H: a clean invoice summary - confirms or fills Part II.
    h = re.search(r"H\.?\s*INVOICE\s+DETAILS(.*?)(?:\n\s*[I-K]\.|\Z)", t, re.S)
    if h:
        for m in re.finditer(r"^\s*(\d{1,2})\s+(\S+)\s+([\d.]+)\s+(" + _CUR + r")\b", h.group(1), re.M):
            sno, inv_no = int(m.group(1)), m.group(2)
            hit = next((x for x in out["invoices"] if x["sno"] == sno), None)
            if hit is None:
                out["invoices"].append({"sno": sno, "inv_no": inv_no, "value_fc": _num(m.group(3)),
                                        "currency": m.group(4)})
            else:
                hit.setdefault("value_fc", _num(m.group(3)))
                hit.setdefault("currency", m.group(4))

    # Part III: items, with FOB in rupees and the IGST on each.
    per = {}
    lines = t.splitlines()
    for i, line in enumerate(lines):
        m = _ITEM_ROW.match(line)
        if not m:
            continue
        decs = _DECIMAL.findall(line)
        item = {"fob_inr": float(decs[-2]) if len(decs) >= 2 else None,
                "igst_value": None, "igst_amount": None}
        for j in range(i + 1, min(i + 8, len(lines))):
            if re.search(r"IGST\s*VALUE", lines[j], re.I) and j + 1 < len(lines):
                item["igst_value"], item["igst_amount"] = _igst_pair(lines[j + 1])
                break
            if _ITEM_ROW.match(lines[j]):
                break
        agg = per.setdefault(int(m.group(1)), {"items": 0, "fob_inr": 0.0, "igst_value": 0.0,
                                               "igst_amount": 0.0, "igst_seen": False})
        agg["items"] += 1
        agg["fob_inr"] += item["fob_inr"] or 0.0
        if item["igst_amount"] is not None:
            agg["igst_seen"] = True
            agg["igst_value"] += item["igst_value"] or 0.0
            agg["igst_amount"] += item["igst_amount"] or 0.0

    # No FOB-in-rupees against FOB x rate check: on real shipping bills the
    # item-level rupee FOB runs 8-20% under the product with every figure read
    # correctly, so the check would cry wolf on sound documents.
    for inv in out["invoices"]:
        agg = per.get(inv["sno"])
        if not agg:
            continue
        inv.update(items=agg["items"], fob_inr=r2(agg["fob_inr"]),
                   igst_value=r2(agg["igst_value"]) if agg["igst_seen"] else None,
                   igst_amount=r2(agg["igst_amount"]) if agg["igst_seen"] else None)

    # Part I's summary: the drawback claimed sits under its label, RoDTEP beside
    # its own. The position-read boxes replace these when the PDF has text.
    m = re.search(r"DBK\s*CLAIM[^\n]*\n\s*([\d.]+)", t)
    out["dbk_amount"] = _num(m.group(1)) if m else None
    out["rodtep_amount"] = _num(_first(r"RODTEP\s*AMT\s*\d*\.?\s*ROSCTL\s*AMT\s+([\d.]+)", t) or "")

    igst = sum(x.get("igst_amount") or 0 for x in out["invoices"])
    seen = any(x.get("igst_amount") is not None for x in out["invoices"])
    out["scheme"] = ("WPAY" if igst > 0 else "LUT") if seen else None
    out["igst_amount"] = r2(igst) if seen else None
    out["fob_inr"] = r2(sum(x.get("fob_inr") or 0 for x in out["invoices"])) or None

    missing = [k for k in ("port", "sb_no", "sb_date") if not out.get(k)]
    if missing:
        out["notes"].append("Could not find " + ", ".join(missing) + " on this shipping bill.")
    if not out["invoices"]:
        out["notes"].append("No invoice row was found in Part II.")
    out["confidence"] = 1.0 if not missing and out["invoices"] and per else (
        0.6 if not missing else 0.2)
    return out


def _under(words, label):
    """The number printed directly under a Part I label (a run of words)."""
    for i in range(len(words) - len(label) + 1):
        seg = words[i:i + len(label)]
        if all(re.sub(r"^\d+\.", "", w["text"]).upper() == t for w, t in zip(seg, label)):
            x0, x1, top = seg[0]["x0"], seg[-1]["x1"], seg[0]["top"]
            cands = [w for w in words if top + 4 < w["top"] < top + 20 and w["x1"] > x0 - 25
                     and w["x0"] < x1 + 25 and re.fullmatch(r"\d+(?:\.\d+)?", w["text"])]
            if cands:
                return float(min(cands, key=lambda w: (w["top"], abs(w["x0"] - x0)))["text"])
    return None


def _sb_boxes(data):
    """Part I's totals, read by where they sit on the page.

    Summing IGST item by item undercounted 12 of 19 real bills - item lines the
    text layer runs together drop out - while the IGST AMT and DBK CLAIM boxes
    agreed with the exporter's own records on every one.
    """
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            words = pdf.pages[0].extract_words() if pdf.pages else []
    except Exception:                                  # noqa: BLE001 - a bonus over the text
        return {}
    got = {"igst_total": _under(words, ("IGST", "AMT")), "dbk_amount": _under(words, ("DBK", "CLAIM"))}
    return {k: v for k, v in got.items() if v is not None}


def _apply_boxes(sb, boxes):
    if boxes.get("dbk_amount") is not None:
        sb["dbk_amount"] = boxes["dbk_amount"]
    total = boxes.get("igst_total")
    if total is None or not sb["invoices"]:
        return
    items = sum(i.get("igst_amount") or 0 for i in sb["invoices"])
    for inv in sb["invoices"]:
        share = (inv.get("igst_amount") or 0) / items if items else 1 / len(sb["invoices"])
        inv["igst_amount"] = r2(total * share)
    sb["igst_amount"] = r2(total)
    sb["scheme"] = "WPAY" if total > 0 else "LUT"


# ---------------------------------------------------------------------------
# Bill of lading / airway bill
# ---------------------------------------------------------------------------

def read_bl(text):
    t = text
    up = t.upper()
    out = {"type": "bl", "notes": [],
           "transport": "air" if re.search(r"AIR\s*WAY\s*BILL|\bH?AWB\b", up) else "sea"}
    # Recognition reads "H/BL" as "HI BL" or "H1BL", so the slash is optional
    # and may come back as a letter.
    out["bl_no"] = _first(r"(?:\bH\s*[/I1L|]?\s*B\s*/?\s*L|\bB\s*/\s*L|\bBL|BILL\s*OF\s*LADING|MTD|H?AWB|MAWB)"
                          r"\s*(?:NO|NUMBER)\.?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9/\-]{5,})", up)
    out["invoice_refs"] = []
    # Recognition often glues "DT." to the number: KE/25-26/062DT.06/03/2026.
    for m in re.finditer(r"INV(?:OICE)?\s*(?:NO|NUMBER)\.?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9/\-]*?)"
                         r"\s*(?:[-/ ,]\s*|(?=DT))(?:DT\.?|DATED)?\s*[:.]?\s*(" + _DATE_STRICT + r")", up):
        out["invoice_refs"].append({"inv_no": m.group(1), "inv_date": _date(m.group(2))})
    if not out["invoice_refs"]:
        for m in re.finditer(r"INV(?:OICE)?\s*(?:NO|NUMBER)\.?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,})", up):
            out["invoice_refs"].append({"inv_no": m.group(1), "inv_date": None})
    out["sb_refs"] = []
    for m in re.finditer(r"S\s*\.?\s*B\s*\.?\s*(?:NO|NUMBER)\.?\s*[:.\-]?\s*(\d{6,8})"
                         r"(?:\s*[-/ ,]?\s*(?:DT\.?\s*)?[:.]?\s*(" + _DATE_STRICT + r"))?", up):
        out["sb_refs"].append({"sb_no": m.group(1), "sb_date": _date(m.group(2)) if m.group(2) else None})
    sob = re.search(r"(?:SHIPPED\s*ON\s*BOARD|ON\s*BOARD\s*DATE|SOB\s*DATE|LADEN\s*ON\s*BOARD)"
                    r"[\s\S]{0,80}?(" + _DATE + r")", up)
    out["sob_date"] = _date(sob.group(1)) if sob else None
    out["vessel"] = _first(r"VES+EL\s*[:.]\s*([A-Z0-9][A-Z0-9 .\-]{2,30}?)\s*(?:\n|$|VOY)", up)
    out["containers"] = sorted(set(re.findall(r"([A-Z]{3}U\d{7})", up)))
    out["loading_port"] = _line_after(r"PORT\s*OF\s*LOADING", up)
    out["consignee"] = _line_after(r"^\s*CONSIGNEE\b", up, skip=r"^(TEL|FAX|EMAIL|ADDRESS)\b")
    if not (out["invoice_refs"] or out["sb_refs"] or out["containers"]):
        out["notes"].append("No invoice number, shipping bill number or container was found on it, "
                            "so it cannot be placed against an invoice by itself.")
    out["confidence"] = 0.9 if out["bl_no"] and (out["invoice_refs"] or out["sb_refs"]) else 0.5
    return out


# ---------------------------------------------------------------------------
# Foreign inward remittance advice (FIRA / FIRC) from the AD bank
# ---------------------------------------------------------------------------

_FC = "|".join(c for c in CURRENCIES if c != "INR")
_MONEY = r"(\d[\d,.]*\d)"


def read_fira(text):
    """A bank's advice of money received from abroad. Two layouts so far: the
    certificate kind (FIRA - "Foreign Currency Amount USD 18,450.00") and
    Kotak's Remittance Transaction Advice, a scan whose amount comes after the
    currency ("50,834.00EUR") and whose money can go to repay packing credit
    before the rest reaches the account."""
    t = text
    out = {"type": "fira", "notes": []}
    out["ref"] = _first(r"REF(?:ERENCE)?\s*(?:NO|NUMBER)\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{5,})", t) or \
        _first(r"REMITTANCE\s*(?:NO|NUMBER)\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{5,})", t)
    out["bank"] = next((ln.strip() for ln in t.splitlines()
                        if re.search(r"\bBANK\b", ln, re.I) and len(ln.strip()) < 60), None)
    credit = _first(r"(?:DATE\s*OF\s*CREDIT|VALUE\s*DATE)\s*[:.]?\s*(" + _DATE + r")", t)
    out["credit_date"] = _date(credit) if credit else _date(_first(r"\bDATE\s*[:.]\s*(" + _DATE + r")", t) or "")
    out["remitter"] = _first(r"REMITTER\s*(?:NAME)?\s*[:.]\s*([^\n]+)", t)
    out["remitting_bank"] = _first(r"(?:REMITTING|RECEIVED\s*FROM)\s*BANK\s*[:.]\s*([^\n]+)", t)
    # The amount received: labelled, else the left side of the conversion line
    # ("From Currency / Amount  Rate  To Currency / Amount"), either way round.
    m = re.search(r"FOREIGN\s*CURRENCY\s*AMOUNT\s*[:.]?\s*(" + _CUR + r")\s*" + _MONEY, t, re.I)
    conv = re.search(_MONEY + r"\s*(" + _CUR + r")\s*(\d+(?:\.\d+)?)\s*" + _MONEY + r"\s*(" + _CUR + r")\b", t)
    if m:
        out["currency"], out["amount_fc"] = m.group(1).upper(), _amt(m.group(2))
    elif conv and conv.group(2) != "INR":
        out["currency"], out["amount_fc"] = conv.group(2), _amt(conv.group(1))
    # Where the money went, when the bank says: packing credit repaid out of
    # it first, the rest credited to the account.
    got = re.search(r"CREDIT\w*\s*(?:TO\s*)?(?:THE\s*)?BENEFICIARY\D{0,12}?(" + _FC + r")\s*(?:CR\.?\s*)?" + _MONEY, t, re.I)
    if got:
        out["credited_fc"] = _amt(got.group(2))
    lines = t.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"PRE\s*-?\s*SHIPMENT|PACKING\s*CREDIT|\bPCFC\b", line, re.I) and re.search(r"REPAY|AMOUNT|AMT", line, re.I):
            out["repaid"] = []
            for nxt in lines[i + 1:i + 12]:
                r = re.match(r"\s*([A-Z0-9/\-]{6,}?)\s*(" + _FC + r")\s*(?:(\d[\d,.]*?[.,]\d{2})\s*(" + _DATE
                             + r")|" + _MONEY + r")", nxt)
                if not r:
                    break
                out["repaid"].append({"ref": r.group(1), "currency": r.group(2),
                                      "amount_fc": _amt(r.group(3) or r.group(5)), "date": _date(r.group(4) or "")})
            break
    parts = ([(got.group(1), out["credited_fc"])] if got else []) + [(r["currency"], r["amount_fc"] or 0)
                                                                      for r in out.get("repaid") or []]
    split = r2(sum(a for _, a in parts))
    if not out.get("amount_fc") and split:
        out["currency"], out["amount_fc"] = parts[0][0], split          # the parts are the whole
    elif not out.get("amount_fc"):
        pairs = [(c, _amt(n)) for c, n in re.findall(r"\b(" + _FC + r")\s*" + _MONEY, t)] + \
                [(c, _amt(n)) for n, c in re.findall(_MONEY + r"\s*(" + _FC + r")\b", t)]
        if pairs:                                       # nothing labelled: the largest sum named
            out["currency"], out["amount_fc"] = max(pairs, key=lambda p: p[1] or 0)
    elif got and out.get("repaid") and abs(split - out["amount_fc"]) > 0.01:
        out["notes"].append(f"The packing credit repaid and the amount credited come to {split:,.2f}, not the "
                            f"{out['amount_fc']:,.2f} received; check the amount.")
    out["amount_inr"] = _num(_first(r"(?:EQUIVALENT\s*TO|INR\s*EQUIVALENT|RUPEE\s*EQUIVALENT)\s*[:.]?"
                                    r"\s*(?:INR|RS\.?)\s*([\d,]+(?:\.\d+)?)", t) or "")
    if conv and conv.group(5) == "INR" and conv.group(2) != "INR":
        out["rate"], out["amount_inr"] = _num(conv.group(3)), out["amount_inr"] or _amt(conv.group(4))
    else:
        out["rate"] = r2(out["amount_inr"] / out["amount_fc"]) if out.get("amount_inr") and out.get("amount_fc") else None
    code = _first(r"PURPOSE[^\n]{0,40}?\b(P[0O][0-9O]{3})\b", t)     # a scan reads the zeros as O
    out["purpose_code"] = code.upper().replace("O", "0") if code else None
    purpose = _first(r"PURPOSE[^\n:]*DESCRIPTION[^\n:]*:[ \t]*([^\n]+)", t) or \
        _first(r"PURPOSE\s*OF\s*REMITTANCE[^:]*:\s*([^\n]+)", t) or ""
    out["purpose"] = purpose.strip() or None
    out["advance"] = out["purpose_code"] == "P0103" or bool(re.search(r"ADVANCE", purpose, re.I))
    inv = _first(r"INVOICE\s*(?:NUMBER|NO)\.?\s*[:.]\s*([A-Z0-9][A-Z0-9/\-]{2,})", t)
    out["invoice_ref"] = inv if inv and not re.fullmatch(r"(?i)payment|details?", inv) else None
    need = [k for k in ("currency", "amount_fc", "credit_date") if not out.get(k)]
    if need:
        out["notes"].append("Could not find " + ", ".join(need) + " on this advice.")
    out["confidence"] = 0.95 if not need and out.get("remitter") else (0.6 if not need else 0.2)
    return out


# ---------------------------------------------------------------------------
# The exporter's own invoice (PDF, scan or workbook) - best effort
# ---------------------------------------------------------------------------

def read_invoice(text):
    t = text
    out = {"type": "invoice", "notes": []}
    out["inv_no"] = _first(r"INVOICE\s*NO\.?\s*[:.\-~]*\s*([A-Z0-9][A-Z0-9/\-\.]{2,})", t)
    out["inv_date"] = _date(_first(r"\bDATE\s*[:.\-~]*\s*(" + _DATE + r")", t) or "")
    if re.search(r"PAYMENT\s+OF\s+IGST|WITH\s+PAYMENT", t, re.I):
        out["scheme"] = "WPAY"
    elif re.search(r"\bLUT\b|WITHOUT\s+PAYMENT|UNDER\s+BOND", t, re.I):
        out["scheme"] = "LUT"
    found = re.findall(r"\b(" + "|".join(c for c in CURRENCIES if c != "INR") + r")\b", t)
    found += ["EUR"] * len(re.findall(r"\bEUROS?\b", t, re.I))
    out["currency"] = max(set(found), key=found.count) if found else None
    # A GST invoice for an export is often in rupees throughout: its taxable
    # value and IGST are what GSTR-1 Table 6A has to agree with.
    out["taxable_inr"] = _num(_first(r"TOTAL\s*TAXABLE\s*VALUE\D{0,12}([\d,]+(?:\.\d+)?)", t) or "")
    out["igst_inr"] = _num(_first(r"TOTAL\s*IGST\D{0,12}([\d,]+(?:\.\d+)?)", t) or "")
    # The total line carries the foreign total, the rupee total, or both - in
    # that order when both, as the item columns run currency then rupees.
    line = _first(r"TOTAL\s*INVOICE\s*VALUE([^\n]*)", t) or ""
    vals = [v for v in (_num(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", line)) if v]
    cur, rupees = out["currency"], bool(re.search(r"RUPEES|\bINR\b|₹", t, re.I))
    out["value_fc"] = out["value_inr"] = None
    if cur and len(vals) >= 2:
        out["value_fc"], out["value_inr"] = vals[0], vals[-1]
    elif vals and rupees and not (cur and re.search(rf"\b{cur}\b", line)):
        out["value_inr"] = vals[0]
    elif vals and cur:
        out["value_fc"] = vals[0]
    if out["value_fc"] is None and cur and out["value_inr"] is None:
        totals = re.findall(r"(?:GRAND\s*TOTAL|TOTAL\s*(?:AMOUNT|VALUE))\D{0,25}([\d,]+\.\d{2})", t, re.I)
        out["value_fc"] = _num(totals[-1]) if totals else None
    buyer = re.sub(r"\s*SAME\s+AS\s+CONSIGNEE.*$", "", _line_after(r"CONSIGNEE|BUYER", t) or "", flags=re.I)
    out["buyer"] = buyer or None
    if not out["inv_no"]:
        out["notes"].append("No invoice number could be read.")
    out["confidence"] = 0.7 if out["inv_no"] and out["inv_date"] else 0.3
    return out


# ---------------------------------------------------------------------------
# GSTR-1 (Table 6A exports)
# ---------------------------------------------------------------------------

def read_gstr1_json(obj):
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict) or not any(k in obj for k in ("exp", "expa", "fp", "gstin")):
        return _unknown("A JSON file, but not a GSTR-1 return.")
    lines = []
    for section, amended in (("exp", False), ("expa", True)):
        for block in obj.get(section) or []:
            typ = (block.get("exp_typ") or "").upper() or None
            for inv in block.get("inv") or []:
                items = [i.get("itm_det", i) for i in inv.get("itms") or []]
                lines.append({
                    "exp_typ": typ, "inum": str(inv.get("inum") or ""), "idt": _date(inv.get("idt") or ""),
                    "val": _num(inv.get("val")), "sbpcode": (inv.get("sbpcode") or "").upper() or None,
                    "sbnum": str(inv.get("sbnum") or "") or None, "sbdt": _date(inv.get("sbdt") or ""),
                    "txval": r2(sum(_num(i.get("txval")) or 0 for i in items)),
                    "iamt": r2(sum(_num(i.get("iamt")) or 0 for i in items)),
                    "csamt": r2(sum(_num(i.get("csamt")) or 0 for i in items)),
                    "rt": max((_num(i.get("rt")) or 0 for i in items), default=0),
                    "amends": {"inum": str(inv.get("oinum") or ""), "idt": _date(inv.get("oidt") or "")}
                    if amended else None,
                })
    fp = str(obj.get("fp") or "")
    return {"type": "gstr1", "source": "json", "gstin": obj.get("gstin"),
            "period": f"{fp[2:]}-{fp[:2]}" if len(fp) == 6 else None,
            "lines": lines, "notes": [] if lines else ["No export invoices (Table 6A) in this return."],
            "confidence": 1.0}


# ---------------------------------------------------------------------------
# Spreadsheets: GSTR-1 export sheet, DGFT IRM and eBRC downloads, ICEGATE status
# ---------------------------------------------------------------------------

def _label(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _header(grid, fields, need, scan=25):
    """Header row and field -> column map, found by synonyms; the row with the
    most hits wins, and at least `need` fields must be present."""
    best = (None, {})
    for r, row in enumerate(grid[:scan]):
        labels = [_label(c) for c in row]
        got = {}
        for field, pats in fields.items():
            for i, lab in enumerate(labels):
                if lab and i not in got.values() and any(re.search(p, lab) for p in pats):
                    got[field] = i
                    break
        if len(got) > len(best[1]):
            best = (r, got)
    return best if len(best[1]) >= need else (None, {})


def _rows(grid, head, cols):
    for row in grid[head + 1:]:
        vals = {f: (row[i] if i < len(row) else "") for f, i in cols.items()}
        if any(str(v).strip() for v in vals.values()):
            yield vals


_GSTR1_EXP = {
    "exp_typ": [r"export type", r"exp.*typ"], "inum": [r"invoice (number|no)"],
    "idt": [r"invoice date"], "val": [r"invoice value"], "sbpcode": [r"port code"],
    "sbnum": [r"shipping bill (number|no)"], "sbdt": [r"shipping bill date"],
    "rt": [r"^rate$", r"^rate\b(?!.*applicable)"], "txval": [r"taxable value"],
    "iamt": [r"integrated tax", r"\bigst\b"], "csamt": [r"cess amount", r"^cess"],
}
_IRM = {
    "irm_no": [r"irm\s*(no|number|ref)"], "irm_date": [r"irm\s*(issue\s*)?date"],
    "ifsc": [r"ifsc"], "ad_code": [r"ad\s*code"], "remit_date": [r"remittance\s*date"],
    "currency": [r"currency", r"\bfcc\b"], "amount": [r"remittance\s*(fc\s*)?amount", r"^amount", r"fc\s*amount"],
    "available": [r"available", r"balance"], "utilised": [r"utili[sz]ed"], "status": [r"status"],
    "remitter": [r"remitter\s*name", r"^remitter$"], "country": [r"country"],
    "purpose": [r"purpose"], "bank_ref": [r"bank\s*ref"],
}
_EBRC = {
    "ebrc_no": [r"e-?brc\s*(no|number)"], "ebrc_date": [r"e-?brc\s*date"], "status": [r"status"],
    "irm_no": [r"irm\s*(no|number)"], "sb_no": [r"(shipping\s*bill|softex)\s*(no|number)", r"^sb\s*no"],
    "sb_date": [r"(shipping\s*bill|softex)\s*date", r"^sb\s*date"], "port": [r"port\s*code"],
    "bill_no": [r"^bill\s*(no|number)"], "currency": [r"currency"],
    "realised_fc": [r"realis(ed|ation)\s*(value|amount)", r"^realised"],
    "net_fc": [r"net\s*realis"], "realised_date": [r"realis(ed|ation)\s*date"],
    "gst_inv_no": [r"gst\s*invoice\s*(no|number)"], "gst_inv_date": [r"gst\s*invoice\s*date"],
    "gst_availed": [r"avail"],
}
_SB_STATUS = {
    "port": [r"location\s*code", r"port\s*code"], "iec": [r"^iec"], "sb_no": [r"sb\s*no", r"shipping\s*bill\s*no"],
    "sb_date": [r"sb\s*date", r"shipping\s*bill\s*date"], "status": [r"status"], "error": [r"error"],
}


def _grid_kind(name, grid):
    if not grid:
        return None
    # eBRC and IRM downloads first: an eBRC sheet also has "GST invoice no"
    # and "shipping bill no" columns and would otherwise pass for GSTR-1.
    head, cols = _header(grid, _EBRC, need=3)
    if head is not None and "ebrc_no" in cols:
        return _ebrc_grid(grid, head, cols)
    head, cols = _header(grid, _IRM, need=4)
    if head is not None and "irm_no" in cols:
        return _irm_grid(grid, head, cols)
    own = _own_grid(name, grid)
    if own:
        return own
    head, cols = _header(grid, _DBK_PENDING, need=3)
    if head is not None and {"sb_no", "expected"} <= set(cols):
        return _dbk_pending_grid(grid, head, cols)
    head, cols = _header(grid, _EDPMS, need=3)
    if head is not None and "sb_no" in cols and ({"outstanding", "realised"} & set(cols)):
        return _edpms_grid(grid, head, cols)
    head, cols = _header(grid, _REFUND, need=3)
    if head is not None and "sb_no" in cols and ({"code", "scroll_no", "status"} & set(cols)) \
            and ({"igst", "dbk"} & set(cols)):
        return _refund_grid(grid, head, cols)
    head, cols = _header(grid, _GSTR1_EXP, need=4)
    if head is not None and "sbnum" in cols and "inum" in cols and ("txval" in cols or "exp_typ" in cols):
        return _gstr1_grid(grid, head, cols)
    head, cols = _header(grid, _SB_STATUS, need=4)
    if head is not None and "sb_no" in cols and "status" in cols:
        return _sb_status_grid(grid, head, cols)
    text = "\n".join(" ".join(_cell_text(row, i) for i in range(len(row)) if str(row[i]).strip())
                     for row in grid)
    if re.search(r"INVOICE", text, re.I) and re.search(r"EXPORT|\bIEC\b|CONSIGNEE", text, re.I):
        out = read_invoice(text)
        out["confidence"] = min(out["confidence"], 0.6)
        return out
    return None


def _cell_text(row, i):
    """A cell as text; a number beside a "date" label is an Excel date serial."""
    v = row[i]
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, (int, float)) and 32000 < v < 80000:
        label = next((str(c) for c in reversed(row[:i]) if str(c).strip()), "")
        if re.search(r"date", label, re.I):
            return to_date(v).strftime("%d/%m/%Y")
    return str(v)


def _gstr1_grid(grid, head, cols):
    """The offline tool's EXP sheet: one row per invoice and tax rate, so an
    invoice with 5% and 12% items spans two rows that belong together."""
    lines = {}
    for v in _rows(grid, head, cols):
        inum = str(v.get("inum") or "").strip()
        if not inum or re.match(r"(?i)total", inum):
            continue
        txval, rt = _num(v.get("txval")) or 0, _num(v.get("rt")) or 0
        typ = str(v.get("exp_typ") or "").upper().replace(" ", "") or None
        iamt = _num(v.get("iamt"))
        if iamt is None:                 # the offline template has no IGST column
            iamt = r2(txval * rt / 100) if typ == "WPAY" else 0.0
        idt = iso(to_date(v.get("idt"))) if not isinstance(v.get("idt"), str) else _date(v.get("idt"))
        sbnum = re.sub(r"\.0$", "", str(v.get("sbnum") or "").strip()) or None
        key = (inum, idt, sbnum)
        line = lines.setdefault(key, {
            "exp_typ": typ, "inum": inum, "idt": idt, "val": _num(v.get("val")),
            "sbpcode": str(v.get("sbpcode") or "").strip().upper() or None, "sbnum": sbnum,
            "sbdt": iso(to_date(v.get("sbdt"))), "txval": 0.0, "iamt": 0.0, "csamt": 0.0,
            "rt": 0, "amends": None})
        line["txval"] = r2(line["txval"] + txval)
        line["iamt"] = r2(line["iamt"] + iamt)
        line["csamt"] = r2(line["csamt"] + (_num(v.get("csamt")) or 0))
        line["rt"] = max(line["rt"], rt)
    return {"type": "gstr1", "source": "sheet", "gstin": None, "period": None,
            "lines": list(lines.values()), "notes": [], "confidence": 0.95 if lines else 0.3}


def _irm_grid(grid, head, cols):
    rows = []
    for v in _rows(grid, head, cols):
        if not str(v.get("irm_no") or "").strip():
            continue
        rows.append({"irm_no": str(v["irm_no"]).strip(), "irm_date": iso(to_date(v.get("irm_date"))),
                     "remit_date": iso(to_date(v.get("remit_date"))) or iso(to_date(v.get("irm_date"))),
                     "ifsc": str(v.get("ifsc") or "").strip() or None,
                     "ad_code": re.sub(r"\.0$", "", str(v.get("ad_code") or "").strip()) or None,
                     "currency": str(v.get("currency") or "").strip().upper() or None,
                     "amount_fc": _num(v.get("amount")), "available_fc": _num(v.get("available")),
                     "utilised_fc": _num(v.get("utilised")),
                     "status": str(v.get("status") or "").strip() or None,
                     "remitter": str(v.get("remitter") or "").strip() or None,
                     "country": str(v.get("country") or "").strip() or None,
                     "purpose_code": (str(v.get("purpose") or "").strip().upper()[:5] or None),
                     "bank_ref": str(v.get("bank_ref") or "").strip() or None})
    return {"type": "irm_list", "rows": rows, "notes": [], "confidence": 0.95 if rows else 0.3}


def _ebrc_grid(grid, head, cols):
    rows = []
    for v in _rows(grid, head, cols):
        if not str(v.get("ebrc_no") or "").strip():
            continue
        rows.append({"ebrc_no": str(v["ebrc_no"]).strip(), "ebrc_date": iso(to_date(v.get("ebrc_date"))),
                     "status": str(v.get("status") or "").strip() or None,
                     "irm_no": str(v.get("irm_no") or "").strip() or None,
                     "sb_no": re.sub(r"\.0$", "", str(v.get("sb_no") or "").strip()) or None,
                     "sb_date": iso(to_date(v.get("sb_date"))),
                     "port": str(v.get("port") or "").strip().upper() or None,
                     "bill_no": str(v.get("bill_no") or "").strip() or None,
                     "currency": str(v.get("currency") or "").strip().upper() or None,
                     "realised_fc": _num(v.get("realised_fc")), "net_fc": _num(v.get("net_fc")),
                     "realised_date": iso(to_date(v.get("realised_date"))),
                     "gst_inv_no": str(v.get("gst_inv_no") or "").strip() or None,
                     "gst_inv_date": iso(to_date(v.get("gst_inv_date"))),
                     "gst_availed": str(v.get("gst_availed") or "").strip().upper()[:1] or None})
    return {"type": "ebrc_list", "rows": rows, "notes": [], "confidence": 0.95 if rows else 0.3}


def _sb_status_grid(grid, head, cols):
    rows = []
    for v in _rows(grid, head, cols):
        sb = re.sub(r"\.0$", "", str(v.get("sb_no") or "").strip())
        if not sb:
            continue
        rows.append({"port": str(v.get("port") or "").strip().upper() or None, "sb_no": sb,
                     "sb_date": iso(to_date(v.get("sb_date"))),
                     "status": str(v.get("status") or "").strip() or None,
                     "error": str(v.get("error") or "").strip() or None})
    return {"type": "sb_status", "rows": rows, "notes": [], "confidence": 0.9 if rows else 0.3}


# ---------------------------------------------------------------------------
# The exporter's own register, and what ICEGATE and the bank say about it
# ---------------------------------------------------------------------------

_SB_NO = [r"^s\.?\s*b\.?\s*no", r"shipping\s*bill\s*(no|number)", r"^sb\s*(no|number)"]
_SB_DT = [r"^s\.?\s*b\.?\s*date", r"shipping\s*bill\s*date", r"^sb\s*date", r"^s\.?\s*date$"]

# An exporter's own sheet, one tab per year. "DATE OF RECD" appears twice and
# is told apart by what stands to its left: GST's, or drawback's.
_OWN = {
    "inv_no": [r"^inv(oice)?\.?\s*no"], "inv_date": [r"^inv(oice)?\.?\s*date$", r"^date$"],
    "inv_amount": [r"^inv(oice)?\.?\s*(amount|value)"], "container": [r"container"], "icd": [r"^(icd|cfs)$"],
    "port": [r"^port\s*code$"], "sb_no": _SB_NO, "sb_date": _SB_DT,
    "igst": [r"^i?gst\s*(amount|amt)$", r"^igst\s*paid"],
    "igst_recd": [r"^i?gst\s*amo?(unt)?\.?\s*rec", r"refund\s*rec"], "diff": [r"^diff"],
    "dbk": [r"^d\.?\s*back", r"^draw\s*back\s*(amount|amt)", r"^dbk\s*(amount|amt)"],
    "error_code": [r"error\s*code"], "payment": [r"^payment"], "bank": [r"^bank$"],
    "docs": [r"del.*buyer", r"docs?\s*to\s*buyer"],
}


def _own_columns(row):
    cols, last = {}, None
    for i, lab in enumerate(_label(c) for c in row):
        if not lab:
            continue
        if re.search(r"date\s*of\s*rec", lab):
            key = "igst_date" if last in ("igst", "igst_recd", "diff") else "dbk_date" if last == "dbk" else None
            if key and key not in cols:
                cols[key] = i
            continue
        for field, pats in _OWN.items():
            if field not in cols and any(re.search(p, lab) for p in pats):
                cols[field], last = i, field
                break
    return cols


def _looks_like_header(row):
    labels = [_label(c) for c in row]
    return sum(1 for lab in labels if lab and any(re.search(p, lab) for ps in _OWN.values() for p in ps)) >= 3


def _sb_no(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(int(v))
    v = re.sub(r"\.0$", "", str(v or "").strip())
    return v if re.fullmatch(r"\d{6,8}", v) else None


def _cell_date(v):
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return _date(v) if isinstance(v, str) and re.search(r"\d", v) else None


def _amount(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    return _num(m.group(0)) if m else None


def _own_grid(name, grid):
    """Every row of one year's tab. A second heading row part-way down starts
    a new table; one that is not a register stops the reading until the next."""
    cols, rows = None, []
    for r, row in enumerate(grid):
        if _looks_like_header(row):
            found = _own_columns(row)
            # ...and a column only a register keeps: what came back and when,
            # or where the goods went - ICEGATE's own sheets have neither.
            ok = "sb_no" in found and ({"igst", "dbk"} & set(found)) and ({"inv_no", "sb_date"} & set(found)) \
                and ({"igst_recd", "igst_date", "dbk_date", "container", "icd", "diff"} & set(found))
            cols = found if ok else None
            continue
        if cols is None:
            continue
        cell = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None
        sb, inv = _sb_no(cell("sb_no")), str(cell("inv_no") or "").strip()
        if not sb and not re.search(r"\d", inv):
            continue
        texts = [str(c) for c in row if isinstance(c, str) and c.strip()]
        codes = sorted(set(re.findall(r"\bSB\s*0*(\d{3})\b", " ".join(texts), re.I)))
        igst_date_raw, dbk_date_raw, pay = cell("igst_date"), cell("dbk_date"), cell("payment")
        igst_recd = _amount(cell("igst_recd")) if not isinstance(cell("igst_recd"), str) \
            or re.search(r"\d", cell("igst_recd")) else None
        rows.append({
            "sheet": name, "row": r + 1, "inv_no": inv or None, "inv_date": _cell_date(cell("inv_date")),
            "inv_amount": _amount(cell("inv_amount")), "container": str(cell("container") or "").strip(" '") or None,
            "icd": str(cell("icd") or "").strip() or None, "port": str(cell("port") or "").strip().upper() or None,
            "sb_no": sb, "sb_date": _cell_date(cell("sb_date")),
            "igst": _amount(cell("igst")), "igst_recd": igst_recd, "igst_date": _cell_date(igst_date_raw),
            "dbk": _amount(cell("dbk")), "dbk_date": _cell_date(dbk_date_raw),
            "codes": [f"SB{c}" for c in codes],
            "payment_recd": bool(pay) and bool(re.search(r"\d|rec", str(pay), re.I)),
            "payment_date": _cell_date(pay), "bank": _cell_date(cell("bank")) or (str(cell("bank") or "").strip() or None),
            "docs": str(cell("docs") or "").strip() or None,
            "notes": [t for t in texts if re.search(r"[a-z]{3}", t, re.I) and not re.fullmatch(r"(?i)recd|done", t.strip())
                      and t.strip() not in (inv, str(cell("icd") or "").strip(), str(cell("container") or "").strip())],
        })
    if not rows:
        return None
    return {"type": "own_register", "sheet": name, "rows": rows, "notes": [], "confidence": 0.9}


_DBK_PENDING = {
    "sb_no": _SB_NO, "sb_date": _SB_DT, "leo_date": [r"leo\s*date"], "ad_code": [r"ad\s*code"],
    "fob": [r"^fob"], "dbk": [r"^dbk", r"draw\s*back"], "expected": [r"expected\s*date", r"date\s*of\s*submission"],
    "status": [r"^status"],
}


def _dbk_pending_grid(grid, head, cols):
    """ICEGATE's list of shipping bills whose drawback waits on proof that the
    buyer paid, each with the date that proof is expected by."""
    rows = []
    for v in _rows(grid, head, cols):
        sb = _sb_no(v.get("sb_no"))
        if sb:
            rows.append({"sb_no": sb, "sb_date": _cell_date(v.get("sb_date")), "leo_date": _cell_date(v.get("leo_date")),
                         "ad_code": re.sub(r"\.0$", "", str(v.get("ad_code") or "").strip()) or None,
                         "fob": _amount(v.get("fob")), "dbk": _amount(v.get("dbk")),
                         "expected": _cell_date(v.get("expected")), "status": str(v.get("status") or "").strip() or None})
    return {"type": "dbk_pending", "rows": rows, "notes": [], "confidence": 0.9 if rows else 0.3}


_EDPMS = {
    "sb_no": _SB_NO, "sb_date": _SB_DT, "port": [r"^port(\s*code)?$"], "inv_no": [r"invoice\s*(no|number)"],
    "currency": [r"curr"], "value": [r"(invoice|fob|bill|export|shipping bill)\s*(value|amount)", r"^fob$"],
    "realised": [r"reali[sz](ed|ation)\s*(amount|value)?$", r"^reali[sz]ed"],
    "outstanding": [r"outstanding", r"unreali[sz]ed", r"^balance"], "due": [r"due\s*date"],
    "status": [r"^status"], "ad_code": [r"ad\s*code"],
}


def _edpms_grid(grid, head, cols):
    """The AD bank's EDPMS statement: what the bank thinks is still unpaid."""
    rows = []
    for v in _rows(grid, head, cols):
        sb = _sb_no(v.get("sb_no"))
        if sb:
            rows.append({"sb_no": sb, "sb_date": _cell_date(v.get("sb_date")),
                         "port": str(v.get("port") or "").strip().upper() or None,
                         "inv_no": str(v.get("inv_no") or "").strip() or None,
                         "currency": str(v.get("currency") or "").strip().upper()[:3] or None,
                         "value_fc": _amount(v.get("value")), "realised_fc": _amount(v.get("realised")),
                         "outstanding_fc": _amount(v.get("outstanding")), "due": _cell_date(v.get("due")),
                         "status": str(v.get("status") or "").strip() or None,
                         "ad_code": re.sub(r"\.0$", "", str(v.get("ad_code") or "").strip()) or None})
    return {"type": "edpms", "rows": rows, "notes": [], "confidence": 0.9 if rows else 0.3}


_REFUND = {
    "sb_no": _SB_NO, "sb_date": _SB_DT, "port": [r"port\s*code", r"location"], "inv_no": [r"invoice\s*(no|number)"],
    "code": [r"(response|error|status|resp|result)\.?\s*code", r"^code$"],
    "status": [r"^status$", r"^response(?!\s*code)", r"remark", r"description", r"reason"],
    "scroll_no": [r"scroll\s*(no|number)"], "scroll_date": [r"scroll\s*date"],
    "igst": [r"igst\s*(amount|amt|paid|refund)", r"refund\s*(amount|amt)"],
    "dbk": [r"^(dbk|draw\s*back)(\s*(amount|amt))?$"], "paid_date": [r"(credit|disburs|payment|paid)\w*\s*date"],
}


def _refund_grid(grid, head, cols):
    """ICEGATE's IGST refund or drawback status: the response code on each
    shipping bill, and the scroll once the money is sent."""
    rows = []
    for v in _rows(grid, head, cols):
        sb = _sb_no(v.get("sb_no"))
        if not sb:
            continue
        status = str(v.get("status") or "").strip() or None
        code = re.search(r"\bSB\s*0*(\d{3})\b", f"{v.get('code') or ''} {status or ''}", re.I)
        base = {"sb_no": sb, "sb_date": _cell_date(v.get("sb_date")), "port": str(v.get("port") or "").strip().upper() or None,
                "inv_no": str(v.get("inv_no") or "").strip() or None, "code": f"SB{code.group(1)}" if code else
                (str(v.get("code") or "").strip() or None), "status": status,
                "scroll_no": re.sub(r"\.0$", "", str(v.get("scroll_no") or "").strip()) or None,
                "scroll_date": _cell_date(v.get("scroll_date")), "paid_date": _cell_date(v.get("paid_date"))}
        for kind in ("igst", "dbk"):
            if kind in cols:
                rows.append({**base, "kind": kind, "amount": _amount(v.get(kind))})
    return {"type": "refund_status", "rows": rows, "notes": [], "confidence": 0.9 if rows else 0.3}


TYPE_WORDS = {
    "sb": "Shipping bill", "bl": "Bill of lading", "fira": "Bank remittance advice",
    "invoice": "Invoice", "gstr1": "GSTR-1", "irm_list": "DGFT remittances (IRM)",
    "ebrc_list": "DGFT eBRCs", "sb_status": "ICEGATE shipping bill status",
    "own_register": "Your own shipping bill register", "edpms": "Bank's EDPMS statement",
    "refund_status": "ICEGATE refund status", "dbk_pending": "ICEGATE drawback: proof of payment due",
    "unknown": "Not recognised",
}

# Where each kind of document is kept inside a register's folder.
FOLDERS = {
    "sb": "Shipping bills", "bl": "Bills of lading", "invoice": "Invoices", "gstr1": "GSTR-1",
    "fira": "Bank advices", "edpms": "Bank advices", "irm_list": "DGFT", "ebrc_list": "DGFT",
    "sb_status": "ICEGATE", "refund_status": "ICEGATE", "dbk_pending": "ICEGATE",
    "own_register": "Your register", "unknown": "Not recognised",
}


def _day(iso_date):
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")
    except (TypeError, ValueError):
        return None


def kept_name(meta, filename):
    """The name a document is kept under: what it is, not what the download
    happened to be called. A spreadsheet keeps its own name."""
    t, g = meta.get("type"), meta.get
    first_inv = next((i.get("inv_no") for i in (g("invoices") or g("invoice_refs") or []) if i.get("inv_no")), None)
    if t == "sb" and g("sb_no"):
        name = f"SB {g('sb_no')}" + (f" of {_day(g('sb_date'))}" if _day(g("sb_date")) else "") \
            + (f" ({g('port')})" if g("port") else "") + (f" - {first_inv}" if first_inv else "")
    elif t == "bl" and (g("bl_no") or first_inv):
        name = "BL" + (f" {g('bl_no')}" if g("bl_no") else "") + (f" for {first_inv}" if first_inv else "")
    elif t == "invoice" and g("inv_no"):
        name = f"Invoice {g('inv_no')}" + (f" of {_day(g('inv_date'))}" if _day(g("inv_date")) else "")
    elif t == "fira" and (g("ref") or g("credit_date")):
        name = "Bank advice" + (f" {g('ref')}" if g("ref") else "") \
            + (f" of {_day(g('credit_date'))}" if _day(g("credit_date")) else "")
    elif t == "gstr1" and g("period"):
        name = "GSTR-1 " + (_day(g("period") + "-01") or g("period"))[3:]
    else:
        return filename
    return name + (Path(filename or "").suffix.lower() or "")
