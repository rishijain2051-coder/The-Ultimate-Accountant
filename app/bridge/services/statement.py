"""A bank statement in, its rows out, in the statement's own order and words.

Words come from the PDF's text layer, or from the OCR of a scan, each with
where it sits on the page. The column headings say which column is which. A
row starts at a date in the date column, and the lines under it until the
next date are its details and reference. Its last amount is the balance and
the one before it the amount.

The running balance then checks every row: the balance before it plus its
amount must be its balance, to the paisa. Where the reading got a sign or a
digit wrong, the balances say which, and the row is set by them, with a note
of what was read. What cannot be settled is marked for you to check.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from .parse import MONTHS, to_date

_PAISA = Decimal("0.01")


@dataclass
class Word:
    page: int
    x0: float
    x1: float
    y0: float
    y1: float
    text: str
    conf: float = 1.0

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Row:
    serial: Optional[str] = None
    date: Optional[date] = None
    value_date: Optional[date] = None
    details: str = ""
    ref: str = ""
    # What a person wrote on the printed statement, read apart from the bank's text.
    note: str = ""
    amount: Optional[Decimal] = None     # + money in, - money out
    balance: Optional[Decimal] = None
    page: int = 0
    y0: float = 0.0
    y1: float = 0.0
    # The amount's sign was printed (or its column says it), not taken from the balance.
    signed: bool = True
    # ok: the balance agrees; fixed: set by the balances (see notes);
    # first: the first row, with no balance before it; bad: does not agree.
    check: str = "ok"
    notes: list[str] = field(default_factory=list)


@dataclass
class Statement:
    rows: list[Row]
    bank: Optional[str] = None
    account_no: Optional[str] = None
    holder: Optional[str] = None
    period: Optional[tuple[date, date]] = None
    opening: Optional[Decimal] = None
    pages: int = 0
    ocr: bool = False
    page_order: list[int] = field(default_factory=list)
    heights: dict[int, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# words
# --------------------------------------------------------------------------

def read_file(path: Path, ocr_char_threshold: int = 60, dpi: int = 300) -> Statement:
    ws, heights, scans = words(path, ocr_char_threshold, dpi)
    st = read(ws, heights, looker(path, scans, dpi) if scans else None)
    st.ocr = bool(scans)
    return st


def words(path: Path, ocr_char_threshold: int = 60, dpi: int = 300) -> tuple[list[Word], dict[int, float], list[int]]:
    """Every word of the statement with its place, each page's height, and
    the pages that were scans."""
    import pdfplumber

    out: list[Word] = []
    heights: dict[int, float] = {}
    scans: list[int] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            ws = page.extract_words(x_tolerance=1.5, y_tolerance=2) or []
            if sum(len(w["text"]) for w in ws) < ocr_char_threshold:
                scans.append(i)
                continue
            heights[i] = float(page.height)
            out += [Word(i, w["x0"], w["x1"], w["top"], w["bottom"], w["text"]) for w in ws]
    if scans:
        more, h = _ocr_words(path, scans, dpi)
        out += more
        heights.update(h)
    return out, heights, scans


def _ocr_words(path: Path, pages: list[int], dpi: int) -> tuple[list[Word], dict[int, float]]:
    import numpy as np
    from PIL import Image

    from . import ocr, pdf_text

    out: list[Word] = []
    heights: dict[int, float] = {}
    if len(pages) > pdf_text.MAX_OCR_PAGES:
        pdf_text.log.warning("%s: %d scanned pages, reading the first %d",
                             Path(path).name, len(pages), pdf_text.MAX_OCR_PAGES)
        pages = pages[:pdf_text.MAX_OCR_PAGES]
    for n, png in pdf_text._render_pages(path, pages, dpi=dpi):
        img = Image.open(io.BytesIO(png)).convert("RGB")
        heights[n] = float(img.size[1])
        result, _ = ocr._get_engine()(np.array(img))
        out += [_word(n, *r) for r in result or []]
    return out, heights


def _word(page: int, box: Any, text: Any, conf: Any, dx: float = 0, dy: float = 0) -> Word:
    xs, ys = [p[0] + dx for p in box], [p[1] + dy for p in box]
    return Word(page, min(xs), max(xs), min(ys), max(ys), str(text), float(conf))


Look = Callable[[int, tuple[float, float, float, float]], list[Word]]


def looker(path: Path, pages: list[int], dpi: int = 300) -> Look:
    """Read a part of a scanned page again, on its own: a printed line with
    handwriting over it can be missed in the whole page, and read alone."""
    import numpy as np
    from PIL import Image

    from . import ocr, pdf_text

    shown: dict[int, Any] = {}

    def look(page: int, box: tuple[float, float, float, float]) -> list[Word]:
        if page not in pages:
            return []
        if page not in shown:
            shown.clear()  # one page at a time: the rows come in page order
            ((_n, png),) = pdf_text._render_pages(path, [page], dpi=dpi)
            shown[page] = Image.open(io.BytesIO(png)).convert("RGB")
        x0, y0, x1, y1 = (int(v) for v in box)
        result, _ = ocr._get_engine()(np.array(shown[page].crop((x0, y0, x1, y1))))
        return [_word(page, *r, dx=x0, dy=y0) for r in result or []]

    return look


def ocr_boxes(boxes: dict[Any, Any]) -> tuple[list[Word], dict[int, float]]:
    """Words from OCR boxes kept as {page: {"size": [w, h], "boxes": [{x0, x1, y0, y1, t, c}]}}."""
    return ([Word(int(p), b["x0"], b["x1"], b["y0"], b["y1"], b["t"], b.get("c", 1.0))
             for p, v in boxes.items() for b in v["boxes"]],
            {int(p): float(v["size"][1]) for p, v in boxes.items() if v.get("size")})


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------

# An amount always has paise. Separators are read loosely (an OCR'd comma is
# often a dot); the digits and the last two decimals are what count.
_MONEY = re.compile(r"([+-]?)\s?((?:\d[\d,.]*)?\d[.,]\d{2})(?![\d])(\s?(?:Cr|CR|Dr|DR)\b)?")
_DATE = re.compile(r"(\d{1,2})\s*[-/ ]?\s*([A-Za-z]{3,9})\s*[-/ ,]?\s*(\d{2,4})")


def money(s: str) -> list[tuple[Decimal, bool]]:
    """The amounts in a piece of text: (value, whether its sign was written)."""
    out = []
    if any(len(w) > 40 for w in s.split()):  # never an amount; keeps the pattern from backtracking
        s = " ".join(w for w in s.split() if len(w) <= 40)
    for m in _MONEY.finditer(s):
        sign, body, crdr = m.group(1), m.group(2), (m.group(3) or "").strip().lower()
        digits = re.sub(r"\D", "", body)
        if len(digits) < 3:
            continue
        v = Decimal(digits[:-2] + "." + digits[-2:])
        if sign == "-" or crdr == "dr":
            v = -v
        out.append((v, bool(sign or crdr)))
    return out


def day(s: str) -> Optional[date]:
    m = _DATE.search(s)
    if m and m.group(2)[:3].lower() in MONTHS:
        y = int(m.group(3))
        try:
            return date(y + 2000 if y < 100 else y, MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError:
            return None
    return to_date(s) if re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", s) else None


def _flat(s: str) -> str:
    return re.sub(r"[^A-Z#/]", "", s.upper())


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------

# Column headings as banks print them, run together as the OCR reads them.
_HEADS = [
    ("value_date", ("VALUEDATE", "VALUEDT")),
    ("date", ("TRANSACTIONDATE", "TXNDATE", "TRANDATE", "POSTDATE", "DATE")),
    ("details", ("TRANSACTIONDETAILS", "DETAILS", "NARRATION", "DESCRIPTION", "PARTICULARS", "REMARKS")),
    ("ref", ("CHQ", "CHOQ", "REF", "CHEQUE", "INSTRUMENT")),
    ("signed", ("DEBIT/CREDIT", "DEBITCREDIT", "DR/CR")),
    ("debit", ("WITHDRAWAL", "DEBIT")),
    ("credit", ("DEPOSIT", "CREDIT")),
    ("balance", ("BALANCE",)),
]


def _lines(ws: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines, top to bottom: a word is on a line
    when its middle is within half a word's height of the line's."""
    if not ws:
        return []
    heights = sorted(w.y1 - w.y0 for w in ws)
    tol = heights[len(heights) // 2] * 0.5
    lines: list[tuple[float, list[Word]]] = []
    for w in sorted(ws, key=lambda w: (w.y0 + w.y1) / 2):
        mid = (w.y0 + w.y1) / 2
        if lines and abs(mid - lines[-1][0]) <= tol:
            mids, line = lines[-1]
            line.append(w)
            lines[-1] = ((mids * (len(line) - 1) + mid) / len(line), line)
        else:
            lines.append((mid, [w]))
    return [sorted(l, key=lambda w: w.x0) for _m, l in lines]


def _phrases(ws: list[Word]) -> list[Word]:
    """Words a space apart run together, as a scan's OCR reads them: a text
    PDF gives "01" "Aug" "2026" and "VALUE" "DATE" a word each."""
    if not ws:
        return []
    gap = sorted(w.y1 - w.y0 for w in ws)[len(ws) // 2] * 0.6
    out: list[Word] = []
    for w in sorted(ws, key=lambda w: w.x0):
        p = out[-1] if out else None
        if p and w.x0 - p.x1 < gap and abs((w.y0 + w.y1) - (p.y0 + p.y1)) / 2 < gap:
            out[-1] = Word(p.page, p.x0, max(p.x1, w.x1), min(p.y0, w.y0), max(p.y1, w.y1),
                           p.text + " " + w.text, min(p.conf, w.conf))
        else:
            out.append(w)
    return out


def _header(lines: list[list[Word]]) -> Optional[tuple[int, dict[str, tuple[float, float]]]]:
    """The heading line of the table and each column's span on it."""
    for i, line in enumerate(lines):
        flat = "".join(_flat(w.text) for w in line)
        if "BALANCE" not in flat or "DATE" not in flat:
            continue
        cols: dict[str, tuple[float, float]] = {}
        for w in _phrases(line):
            t = _flat(w.text)
            for kind, keys in _HEADS:
                for k in keys:
                    j = t.find(k)
                    if j < 0 or kind in cols:
                        continue
                    if kind == "date" and "VALUEDATE" in t and t.find("VALUEDATE") <= j < t.find("VALUEDATE") + 9:
                        continue  # the DATE of VALUE DATE
                    if kind in ("debit", "credit") and "signed" in cols:
                        continue
                    span = (w.x1 - w.x0) / max(len(t), 1)
                    # A heading run together with the one before it ("VALUE DATE
                    # TRANSACTION DETAILS") starts part-way along; otherwise
                    # where its word starts ("CHQ / REF NO").
                    earlier = any(t.find(x, 0, j) >= 0 for other, xs in _HEADS if other != kind for x in xs)
                    cols[kind] = (w.x0 + j * span if earlier else w.x0, w.x0 + (j + len(k)) * span)
                    break
        if "balance" in cols and ("date" in cols or "value_date" in cols):
            return i, cols
    return None


def _page_no(lines: list[list[Word]]) -> Optional[int]:
    for line in lines:
        m = re.search(r"PAGE\s*(\d{1,3})\s*OF\s*\d{1,3}", " ".join(w.text for w in line).upper())
        if m:
            return int(m.group(1))
    return None


_END = re.compile(r"STATEMENT\s*GENERATED|PAGE\s*\d+\s*OF|END\s*OF\s*STATEMENT|CLOSING\s*BALANCE|OPENING\s*BALANCE", re.I)
# Printed text reads with a sure hand; what reads below this is usually handwriting.
HANDWRITING = 0.72


def read(ws: list[Word], heights: Optional[dict[int, float]] = None, look: Optional[Look] = None) -> Statement:
    pages = sorted({w.page for w in ws})
    by_page = {p: _lines([w for w in ws if w.page == p]) for p in pages}
    printed = {p: _page_no(ls) for p, ls in by_page.items()}
    # A scan's pages come out in the scanner's order; the footer says the statement's.
    order = sorted(pages, key=lambda p: (printed[p] is None, printed[p] or 0, p)) \
        if len({v for v in printed.values() if v}) == sum(1 for v in printed.values() if v) else pages
    st = Statement(rows=[], pages=len(pages), page_order=order, heights=dict(heights or {}))
    _about(st, [w for ls in by_page.values() for l in ls for w in l])

    cols: Optional[dict[str, tuple[float, float]]] = None
    for p in order:
        lines = by_page[p]
        found = _header(lines)
        start = 0
        if found:
            start, cols = found[0] + 1, found[1]
        if not cols:
            continue
        st.rows += _rows(p, lines[start:], cols, look)
    _serial_order(st)
    settle(st)
    for r in st.rows:  # where on its page, as fractions of its height
        h = st.heights.get(r.page)
        if h:
            r.y0, r.y1 = round(r.y0 / h, 4), round(r.y1 / h, 4)
    return st


def _about(st: Statement, ws: list[Word]) -> None:
    text = " ".join(w.text for w in sorted(ws, key=lambda w: (w.page, w.y0, w.x0)))
    flat = re.sub(r"\s+", "", text)
    # Spaces kept: the number ends where its line does, not in the next line's date.
    m = re.search(r"(?:ACC[O0]UNT|A/C)\s*(?:N[O0]\.?|NUMBER|#)?\s*[:.#]*\s*(\d{9,18})(?!\d)", text, re.I)
    st.account_no = m.group(1) if m else None
    for bank in ("Kotak Mahindra Bank", "State Bank of India", "HDFC Bank", "ICICI Bank", "Axis Bank",
                 "Bank of Baroda", "Punjab National Bank", "Canara Bank", "IDBI Bank", "Union Bank",
                 "Indian Overseas Bank", "UCO Bank", "Yes Bank", "IndusInd Bank", "AU Small Finance Bank"):
        if re.sub(r"\s", "", bank).lower() in flat.lower():
            st.bank = bank
            break
    m = re.search(r"(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})\s*[-–to]+\s*(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})", text)
    if m and day(m.group(1)) and day(m.group(2)):
        st.period = (day(m.group(1)), day(m.group(2)))
    m = re.search(r"OPENING\s*BALANCE[^\d-]*([-+]?[\d,]+\.\d{2})", text, re.I)
    if m:
        st.opening = money(m.group(1))[0][0] if money(m.group(1)) else None


# Printed bits the OCR reads unsurely, which are not handwriting: a wrapped
# "pp", a reference's prefix ("FCM-", "IMPS-", "IM PS-").
_PRINTED = re.compile(r"(?:[A-Z]{2,5}\s?[A-Z]{0,3}-|pp|PP|[A-Za-z]{1,2}\.?)")


def _rows(page: int, lines: list[list[Word]], cols: dict[str, tuple[float, float]],
          look: Optional[Look] = None) -> list[Row]:
    """The rows of one page. Each row owns a band of the page, from just above
    its date to just above the next row's, and every word in it by its middle:
    a word lifted by handwriting over it stays with its row."""
    details_x = cols.get("details", (cols.get("value_date", cols.get("date"))[1] + 10, 0))[0]
    ref_x = cols.get("ref", (None, None))[0]
    amount_x = min(v[0] for k, v in cols.items() if k in ("signed", "debit", "credit", "balance"))
    date_x = cols.get("date", cols.get("value_date"))[0]
    words_ = [w for l in lines for w in l]
    if not words_:
        return []
    heights = sorted(w.y1 - w.y0 for w in words_)
    tall = heights[len(heights) // 2]  # a line of print
    margin = tall * 0.6
    # A reference starts up to half a line left of its heading, as the OCR boxes it.
    ref_x = ref_x - tall if ref_x is not None else None
    cell_x1 = ref_x if ref_x is not None else amount_x - 5
    in_cell = lambda w: details_x - 5 <= w.xc < cell_x1  # noqa: E731

    # Where the table ends on this page: a footer, or the page's end.
    end = max(w.y1 for w in words_) + 1
    starts: list[tuple[float, list[Word]]] = []
    for line in lines:
        text = " ".join(w.text for w in line)
        if _END.search(text):
            end = min(w.y0 for w in line)
            break
        left = _phrases([w for w in line if w.xc < details_x - 5])
        if any(day(w.text) for w in left):
            starts.append((min(w.y0 for w in left if day(w.text)), line))
    rows: list[Row] = []
    for k, (top, line) in enumerate(starts):
        bottom = starts[k + 1][0] - margin if k + 1 < len(starts) else end
        band = [w for w in words_ if top - margin <= (w.y0 + w.y1) / 2 < bottom]
        if look and not any(in_cell(w) for w in line):
            # Nothing read beside the date: read the row's details again, alone.
            again = look(page, (details_x - 10, top - margin, cell_x1, bottom))
            if again:
                band = [w for w in band if not in_cell(w)] + again
        left = _phrases([w for w in line if w.xc < details_x - 5])
        dates = [d for d in (day(w.text) for w in left) if d]
        row = Row(page=page, y0=min(w.y0 for w in band), y1=max(w.y1 for w in band),
                  date=dates[0], value_date=dates[1] if len(dates) > 1 else None)
        # The bank's own number, left of the date column: a word of its own,
        # or run into the date by the OCR ("10 08Aug2026").
        first = left[0]
        if first.x0 < date_x - tall:
            m = re.match(r"\s*(\d{1,5})(?:\s+|$)", first.text)
            if m and (not day(first.text) or day(first.text[m.end():])):
                row.serial = m.group(1)
        # An amount and its Cr or Dr, a word each in a text PDF, are one.
        amounts = _phrases([w for w in band if w.xc >= amount_x - 5])
        band = [w for w in band if w.xc < amount_x - 5] + amounts
        # Top to bottom, then left to right: the details as they read.
        for w in sorted(band, key=lambda w: (round((w.y0 + w.y1) / 2 / (margin * 1.2)), w.x0)):
            if w.xc < details_x - 5:
                continue  # the dates and times
            if w.xc >= amount_x - 5 or w.x1 > amount_x - 5 and money(w.text):
                found = money(w.text)  # under the amounts: an amount, or nothing
                for n, (v, signed) in enumerate(found):
                    # Run together by the OCR ("-4,500.00/-48,68,129.32"): the
                    # amount, then the balance. Alone: its column says which.
                    where = ("amount" if n == 0 else "balance") if len(found) > 1 else None
                    _amount(row, v, signed, w, cols, where)
                continue
            if w.conf < HANDWRITING and not _PRINTED.fullmatch(w.text.strip()):
                row.note = (row.note + " " + w.text).strip()
            elif ref_x is not None and w.x0 >= ref_x:
                row.ref = (row.ref + w.text) if row.ref.endswith("-") else (row.ref + " " + w.text).strip()
            else:
                row.details = _join(row.details, w.text)
        rows.append(row)
    return [r for r in rows if r.amount is not None or r.balance is not None]


def _join(a: str, b: str) -> str:
    # A detail wrapped over two lines is one text: rejoin a word split at the edge.
    if not a:
        return b
    return a + ("" if a.endswith(("/", "-")) or b.startswith(("/", "-")) else " ") + b


def _amount(row: Row, v: Decimal, signed: bool, w: Word, cols: dict[str, tuple[float, float]],
            where: Optional[str] = None) -> None:
    """Place an amount read on the row: in the balance column, or the amount
    column (signed, or debit and credit apart)."""
    if where is None:
        mid = lambda k: sum(cols[k]) / 2  # noqa: E731
        amount_cols = [k for k in ("signed", "debit", "credit") if k in cols]
        nearest = min(amount_cols + ["balance"], key=lambda k: abs(w.xc - mid(k)))
        where = "balance" if nearest == "balance" else "amount"
    if where == "balance" and row.balance is None:
        row.balance = v
        return
    if row.amount is None:
        if "debit" in cols and "credit" in cols and not signed:
            nearer_credit = abs(w.xc - sum(cols["credit"]) / 2) < abs(w.xc - sum(cols["debit"]) / 2)
            v = abs(v) if nearer_credit else -abs(v)
        row.amount = v
        row.signed = signed or "signed" not in cols
    elif row.balance is None:
        row.balance = v


def _serial_order(st: Statement) -> None:
    """Rows in the bank's own numbering, when it was read for most of them."""
    known = [r for r in st.rows if r.serial and r.serial.isdigit()]
    if len(known) >= len(st.rows) * 0.8:
        pos = {id(r): i for i, r in enumerate(st.rows)}
        st.rows.sort(key=lambda r: (int(r.serial) if r.serial and r.serial.isdigit() else 10**9, pos[id(r)]))


def settle(st: Statement) -> None:
    """Check each row against the running balance, and set by it what the
    reading got wrong: the balance before plus the amount is the balance."""
    rows = st.rows

    def agrees(bal: Decimal, nxt: Optional[Row]) -> bool:
        """The next row runs on from this balance (its sign as printed or not)."""
        return nxt is not None and nxt.amount is not None and nxt.balance is not None and (
            abs(bal + nxt.amount - nxt.balance) < _PAISA or abs(bal - nxt.amount - nxt.balance) < _PAISA)

    for i, r in enumerate(rows):
        prev = rows[i - 1].balance if i else st.opening
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if prev is None or r.balance is None:
            if r.amount is None and prev is not None and r.balance is not None:
                r.amount = r.balance - prev
                r.notes.append("The amount was not read; the balances say it.")
                r.check = "fixed"
            else:
                r.check = "first" if i == 0 else "bad"
            continue
        if r.amount is not None and abs(prev + r.amount - r.balance) < _PAISA:
            r.check = "ok"
        elif r.amount is not None and abs(prev - r.amount - r.balance) < _PAISA:
            r.amount = -r.amount  # a sign not printed is taken from the balance
            r.check = "ok" if not r.signed else "fixed"
            if r.signed:
                r.notes.append("The sign was misread; the balance says it.")
        elif agrees(r.balance, nxt):  # the balance is right: the amount was misread
            was, r.amount = r.amount, r.balance - prev
            r.check = "fixed"
            r.notes.append(f"The amount could not be read; the balances say {r.amount}." if was is None
                           else f"The amount was read as {was}; the balances say {r.amount}.")
        elif r.amount is not None and agrees(prev + r.amount, nxt):  # the balance was misread
            was, r.balance = r.balance, prev + r.amount
            r.check = "fixed"
            r.notes.append(f"The balance was read as {was}; the amounts say {r.balance}.")
        else:
            r.check = "bad"
            r.notes.append("The amount and the balances do not agree: check this row on the statement.")
    if rows and rows[0].check == "first" and rows[0].amount is not None and len(rows) > 1 and rows[1].check == "ok":
        rows[0].check = "ok"  # its balance is the one the next row runs on from
