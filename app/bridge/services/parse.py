"""Rule-based invoice field extraction.

This runs before the LLM and wins ties. A GSTIN, a date, a total and a tax
split are all things a regex gets exactly right or not at all -- there is no
reason to let a 3B model guess at them and then have to audit the guess.
The local AI reader (llm.py) copies off the page what these rules could not
find; turning its text into dates and amounts is done here, by the same code.

Every field carries its confidence AND the line it came from, so the reviewer
can see *why* the app believes something without opening the PDF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from . import gst

NUM = r"\(?-?[\d,]+(?:\.\d{1,4})?\)?"

DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b"),
    re.compile(r"\b(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-,]+(\d{2,4})\b"),
    re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"),
]

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}

# Labels are matched longest-first so "Due date" never gets eaten by "date".
INVOICE_NO_LABELS = [
    r"invoice\s*(?:no|number|#)\.?\s*[:\-]?",
    r"bill\s*(?:no|number)\.?\s*[:\-]?",
    r"tax\s+invoice\s*(?:no)?\.?\s*[:\-]?",
    r"document\s*(?:no|number)\.?\s*[:\-]?",
    r"voucher\s*(?:no|number)\.?\s*[:\-]?",
]
INVOICE_DATE_LABELS = [
    r"invoice\s*date", r"bill\s*date", r"dated", r"date\s*of\s*(?:invoice|issue|supply)", r"\bdate\b",
]
DUE_DATE_LABELS = [r"due\s*date", r"payment\s*due", r"due\s*on"]

TOTAL_LABELS = [
    r"grand\s*total", r"invoice\s*total", r"total\s*amount\s*(?:payable|due)?",
    r"amount\s*payable", r"net\s*payable", r"bill\s*amount", r"\btotal\b",
]
TAXABLE_LABELS = [
    r"taxable\s*(?:value|amount)", r"net\s*amount", r"sub\s*[\-\s]?total",
    r"total\s*before\s*tax", r"amount\s*before\s*tax", r"basic\s*amount",
]
ROUNDOFF_LABELS = [r"round(?:ing)?\s*[\-\s]?off", r"\brounded\b"]

ITEM_HEADER_HINTS = (("hsn", "sac"), ("qty", "quantity", "nos"), ("rate", "price", "amount", "value"))
TOTALS_STOP = re.compile(
    r"\b(sub\s*total|taxable\s*value|net\s*amount|total\s*amount|grand\s*total|cgst|sgst|igst|"
    r"terms|declaration|bank|e\s*&\s*o\.?e|amount\s*in\s*words|gst\s*desc|page\s*no|"
    r"continued|carried\s*forward)\b",
    re.I,
)

# <description> <HSN/SAC> <qty> <rate> <amount>
RE_LINE_FULL = re.compile(
    rf"^\s*(?P<desc>.*?\S)\s+(?P<hsn>\d{{4,8}})\s+(?P<qty>{NUM})\s+(?P<rate>{NUM})\s+(?P<amt>{NUM})\s*$"
)
# <description> <HSN/SAC> <amount>   (service bills with no quantity column)
RE_LINE_NOQTY = re.compile(rf"^\s*(?P<desc>.*?\S)\s+(?P<hsn>\d{{4,8}})\s+(?P<amt>{NUM})\s*$")
# <sl> <description> <qty> <unit> <rate> <amount>  (no HSN column at all)
RE_LINE_NOHSN = re.compile(
    rf"^\s*\d{{1,3}}[.)]?\s+(?P<desc>.*?\S)\s+(?P<qty>{NUM})\s*(?P<uom>[A-Za-z]{{1,8}})?\s+"
    rf"(?P<rate>{NUM})\s+(?P<amt>{NUM})\s*$"
)

RE_TAX_ROW = re.compile(r"\b(?P<kind>CGST|SGST|UTGST|IGST|CESS)\b", re.I)
RE_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
RE_NUMS = re.compile(NUM)


@dataclass
class Field:
    value: Any = None
    confidence: float = 0.0
    source: str = "none"  # regex | llm | ocr | inferred | user
    evidence: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        v = self.value
        return {
            "value": str(v) if isinstance(v, (Decimal, date)) else v,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "evidence": re.sub(r"\s+", " ", (self.evidence or "")).strip()[:240] or None,
        }


@dataclass
class ParsedInvoice:
    fields: dict[str, Field] = dc_field(default_factory=dict)
    lines: list[dict[str, Any]] = dc_field(default_factory=list)
    gstins: list[str] = dc_field(default_factory=list)
    # Fields where the scanned text and the model's reading of the page image
    # disagree: {"field", "text", "image"}. validate.py turns them into warnings.
    conflicts: list[dict[str, str]] = dc_field(default_factory=list)

    def get(self, key: str, default=None):
        f = self.fields.get(key)
        return f.value if f and f.value is not None else default

    def put(self, key: str, value, confidence: float, source: str, evidence: str | None = None):
        if value is None:
            return
        if key == "currency":
            value = iso_currency(value)
        self.fields[key] = Field(value, confidence, source, evidence)


_RUPEE = re.compile(r"(₹|₨|rs\.?|inr|(indian )?rupees?)", re.I)


def iso_currency(value: Any) -> str:
    """The currency as its code. The AI copies it as written ("₹", "Rs."),
    and the screen formats amounts with it: a symbol there broke the page."""
    s = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z]{3}", s) and not _RUPEE.fullmatch(s):
        return s.upper()
    return "INR"


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def ascii_digits(s: str) -> str:
    """१२/०९/२०२६ -> 12/09/2026. Bill numbers and GSTINs are compared and sent
    to Tally in 0-9, whichever digits the bill was written in."""
    return s.translate(_DEVANAGARI_DIGITS)


def to_decimal(raw: str | None) -> Optional[Decimal]:
    if raw is None:
        return None
    s = ascii_digits(str(raw)).strip()
    # "Rs. 4,105/-" and "रु 4105/-": the Indian ways of opening and closing an amount.
    s = re.sub(r"/-\s*$", "", s)
    s = re.sub(r"^(?:rs\.?|inr|रु\.?|रुपये)\s*", "", s, flags=re.I)
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("₹", "").replace("INR", "").strip()
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in {"-", "."}:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def to_date(raw: str) -> Optional[date]:
    """Day-first. In India 04/05/2026 is 4 May; guessing month-first here
    silently books invoices into the wrong period."""
    s = raw.strip()
    m = DATE_PATTERNS[0].search(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        day, month = (a, b) if a > 12 >= b or a <= 31 else (b, a)
        if a <= 12 < b:  # unambiguous the other way (e.g. 05/18/2026)
            day, month = b, a
        try:
            return date(y, month, day)
        except ValueError:
            try:
                return date(y, day, month)
            except ValueError:
                return None
    m = DATE_PATTERNS[1].search(s)
    if m:
        d_, mon, y = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
        y = y + 2000 if y < 100 else y
        if mon in MONTHS:
            try:
                return date(y, MONTHS[mon], d_)
            except ValueError:
                return None
    m = DATE_PATTERNS[2].search(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _drop_repeated(lines: list[str]) -> list[str]:
    """Remove page headers/footers.

    On a multi-page invoice the header repeats above every page's item rows,
    and anything left in the item block gets glued onto the previous line item
    as if it were part of the description. A line that appears more than once
    with its digits masked is a header, not data.
    """
    if len(lines) < 12:
        return lines
    keys = [re.sub(r"\d+", "#", re.sub(r"\s+", " ", ln.strip().lower())) for ln in lines]
    seen: set[str] = set()
    out: list[str] = []
    for ln, k in zip(lines, keys):
        # Keep the first copy -- on page 1 the header is where the invoice
        # number lives. Only the repeats further down are page furniture.
        # Money-bearing lines are never dropped: two identical item rows on
        # one invoice are real, and losing one changes the total.
        if k in seen and len(k) >= 8 and not re.search(r"\d[\d,]*\.\d{2}", ln):
            continue
        seen.add(k)
        out.append(ln)
    return out


def _labelled(lines: list[str], labels: list[str]) -> list[tuple[str, str]]:
    """(tail_after_label, whole_line) for every line carrying one of the labels."""
    out = []
    for ln in lines:
        for lab in labels:
            m = re.search(lab, ln, re.I)
            if m:
                out.append((ln[m.end():], ln))
                break
    return out


def _last_number(s: str) -> Optional[Decimal]:
    nums = re.findall(NUM, s)
    for raw in reversed(nums):
        d = to_decimal(raw)
        if d is not None:
            return d
    return None


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

def parse(text: str, own_gstins: set[str] | None = None) -> ParsedInvoice:
    own_gstins = {g.upper() for g in (own_gstins or set())}
    raw_lines = [ln for ln in text.splitlines()]
    lines = [ln.rstrip() for ln in raw_lines if ln.strip()]

    lines = _drop_repeated(lines)
    flat = "\n".join(lines)
    up = flat.upper()

    p = ParsedInvoice()
    p.gstins = gst.find_gstins(flat)

    _parse_parties(p, lines, flat, up, own_gstins)
    _parse_invoice_no(p, lines, flat)
    _parse_dates(p, lines)
    _parse_place_of_supply(p, lines, flat)
    _parse_lines(p, lines)
    # Totals first: the taxable value is what lets _parse_taxes tell a tax
    # amount apart from the other three numbers sharing its row.
    _parse_totals(p, lines)
    _parse_taxes(p, lines)
    _reconcile(p)
    words = words_amount(flat)
    if words is not None:
        p.put("total_in_words", words, 0.9, "regex", "the total in words")
    return p


# Amounts in words, as bills print them ("Rupees One Thousand Seven Hundred
# Twenty Only") and as the OCR returns them, run together
# ("INRFiftyThreeThousandNineHundredNinetySevenOnly"): a second reading of
# the total, in another hand, for pipeline.settle to weigh.
_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_NUM_WORDS |= {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_NUM_WORDS |= {"fourty": 40, "ninty": 90, "tewenty": 20}  # as bills misspell them
_SCALE_WORDS = {"hundred": 100, "hundered": 100, "thousand": 1000, "thausand": 1000, "thousend": 1000,
                "lakh": 10 ** 5, "lakhs": 10 ** 5, "lac": 10 ** 5, "lacs": 10 ** 5, "crore": 10 ** 7, "crores": 10 ** 7}
_FILL_WORDS = {"and", "rupees", "rupee", "indian", "inr", "rs", "only", "paise", "paisa"}
_VOCAB = sorted({*_NUM_WORDS, *_SCALE_WORDS, *_FILL_WORDS}, key=len, reverse=True)


def _count_words(words: list[str]) -> int:
    total = current = 0
    for w in words:
        if w in _NUM_WORDS:
            current += _NUM_WORDS[w]
        elif w in _SCALE_WORDS:
            if _SCALE_WORDS[w] == 100:
                current = (current or 1) * 100
            else:
                total, current = total + (current or 1) * _SCALE_WORDS[w], 0
    return total + current


def words_amount(text: str | None) -> Optional[Decimal]:
    """The largest amount written in words that ends in "only" (the tax in
    words is always the smaller one), or None."""
    found: list[Decimal] = []
    for chunk in re.split(r"[^a-z]+", re.sub(r"[\s.,:;\-]+", "", str(text or "").lower()) + "|"):
        words: list[str] = []
        i = 0
        while i <= len(chunk):
            hit = next((w for w in _VOCAB if chunk.startswith(w, i)), None) if i < len(chunk) else None
            if hit:
                words.append(hit)
                i += len(hit)
                continue
            if "only" in words:
                seq = words[:len(words) - words[::-1].index("only")]
                if sum(w in _NUM_WORDS or w in _SCALE_WORDS for w in seq) >= 2:
                    paise = 0
                    if seq.count("paise") or seq.count("paisa"):
                        k = max(i for i, w in enumerate(seq) if w in ("paise", "paisa"))
                        j = max((i for i, w in enumerate(seq[:k]) if w == "and"), default=None)
                        if j is not None:
                            paise, seq = _count_words(seq[j + 1:k]), seq[:j]
                    if paise < 100:
                        found.append(Decimal(_count_words(seq)) + Decimal(paise) / 100)
            words = []
            i += 1
    return max(found) if found else None


def _parse_parties(p: ParsedInvoice, lines, flat, up, own_gstins) -> None:
    valid = [g for g in p.gstins if gst.gstin_checksum_ok(g)]
    unknown = [g for g in valid if g not in own_gstins]

    mine = next((g for g in valid if g in own_gstins), None)
    if mine:
        p.put("own_gstin", mine, 1.0, "regex", f"matches a company GSTIN in Tally: {mine}")

    counterparty: Optional[str] = None
    reason = ""
    if mine and unknown:
        counterparty = unknown[0]
        reason = "only GSTIN on the invoice that is not one of your own companies"
    elif len(valid) >= 2:
        # No Tally company GSTIN known yet: the issuer's state is on
        # "Place of Issue", the recipient's on "Place of Supply".
        issue = re.search(r"place\s*of\s*issue\s*[:\-]?\s*(\d{2})", flat, re.I)
        supply = re.search(r"place\s*of\s*supply\s*[:\-]?\s*(\d{2})", flat, re.I)
        if issue:
            counterparty = next((g for g in valid if g.startswith(issue.group(1))), None)
            reason = f"state {issue.group(1)} matches Place of Issue"
        if not counterparty and supply:
            buyer = next((g for g in valid if g.startswith(supply.group(1))), None)
            counterparty = next((g for g in valid if g != buyer), None)
            reason = "the GSTIN that is not the Place of Supply state"
    elif len(valid) == 1:
        counterparty = valid[0]
        reason = "only GSTIN found on the document"

    if counterparty:
        conf = 0.97 if mine else (0.80 if len(valid) >= 2 else 0.70)
        p.put("party_gstin", counterparty, conf, "regex", reason)
        name = _party_name_near(lines, counterparty)
        if name:
            p.put("party_name", name, 0.80, "regex", f"text block above GSTIN {counterparty}")

    # Purchase vs sale: if my GSTIN is the Place of Supply, I am the recipient.
    if mine:
        pos = re.search(r"place\s*of\s*supply\s*[:\-]?\s*(\d{2})", flat, re.I)
        is_purchase = bool(pos and mine.startswith(pos.group(1))) or bool(counterparty)
        p.put(
            "voucher_type",
            "Purchase" if is_purchase else "Sales",
            0.90 if pos else 0.70,
            "inferred",
            "your GSTIN is the recipient on this invoice" if is_purchase else "your GSTIN issued this invoice",
        )

    m = re.search(r"supply\s*type\s*[:\-]?\s*(B2B|B2C[A-Z]*|SEZ[A-Z]*|EXPORT\w*)", flat, re.I)
    if m:
        p.put("supply_type", m.group(1).upper(), 0.95, "regex", m.group(0))


LEGAL_SUFFIX = re.compile(
    r"\b(private\s+limited|pvt\.?\s*ltd|limited|ltd\.?|llp|l\.?l\.?p|inc\.?|corp\.?|"
    r"company|co\.|&\s*co|enterprises?|industries|traders?|trading|exports?|imports?|"
    r"impex|solutions?|services?|technologies|associates|agencies|sons|brothers)\b",
    re.I,
)
NAME_NOISE = re.compile(
    r"gstin|unique\s*id|\bpan\b|\bcin\b|\btel\b|phone|e-?mail|\bstate\b|place\s*of|"
    r"ship\s*to|bill\s*to|address|invoice|\bdate\b|customer|contract|proposal|"
    r"issuing|office|floor|sector|\bbu\b|signed|direct\s*contact|website|"
    r"account|ifsc|swift|branch|bank\s*name|supply\s*type|page\s*no|description|"
    # "Original / Duplicate / Triplicate", "(ORIGINAL FOR RECIPIENT)"
    r"\borigin|\bdupli|\btripl|recipient|transporter",
    re.I,
)
# The blessing printed above a firm's name - "Shree Ganeshaya Namah", "Shubh
# Labh", "Jai Mata Di" - is not its name. A firm named after one ("Jai Mata
# Di Traders") has a business word in the name as well, and still counts.
INVOCATION = re.compile(
    r"ga?nesha?y|\bnamah?a?\b|shubh\s*-?\s*labh|\bjai\s+(?:shri|shree|sri|mata|maa?)\b|"
    r"गणेशाय|नमः|नम:|शुभ\s*लाभ|जय\s+(?:श्री|माता)",
    re.I,
)


def is_invocation(s: str) -> bool:
    return bool(INVOCATION.search(s)) and not LEGAL_SUFFIX.search(s)
# Labels that name the counterparty outright -- always better than geometry.
PARTY_LABELS = [
    r"pay\s*to\s*[:\-]", r"sold\s*by\s*[:\-]?", r"supplier\s*(?:name)?\s*[:\-]",
    r"seller\s*(?:name)?\s*[:\-]", r"vendor\s*(?:name)?\s*[:\-]",
    r"billed\s*by\s*[:\-]?", r"from\s*[:\-]",
]


def _looks_like_name(seg: str) -> bool:
    if len(seg) < 4 or len(seg) > 200:
        return False
    if NAME_NOISE.search(seg) or seg[0].isdigit() or is_invocation(seg):
        return False
    letters = sum(c.isalpha() for c in seg)
    return letters >= 4 and letters / len(seg) > 0.55


def _party_name_near(lines: list[str], gstin: str) -> Optional[str]:
    """Find the counterparty's legal name.

    A labelled name ("Pay to:", "Supplier:") beats geometry every time. Only
    when there is no label do we fall back to the address block above the
    GSTIN -- and there the name is the *top* of the block, not the line
    nearest the GSTIN, which is usually the city or the state.
    """
    for tail, _whole in _labelled(lines, PARTY_LABELS):
        seg = re.sub(r"\s{2,}.*$", "", tail).strip(" .,:-")
        if _looks_like_name(seg):
            return seg[:200]

    idx = next((i for i, ln in enumerate(lines) if gstin in ln.upper()), None)
    if idx is None:
        return None
    col = lines[idx].upper().find(gstin)

    def names(rows: range) -> list[tuple[int, int, str]]:
        found = []
        for i in rows:
            # Stay in the GSTIN's own column: the other half of the page is the
            # other party, and grabbing its name inverts buyer and seller.
            seg = lines[i][max(0, col - 20):] if col > 24 else lines[i][: col + 60 if col else 70]
            seg = re.sub(r"\s{2,}.*$", "", seg.strip()).strip(" .,:-*★☆•|")
            if _looks_like_name(seg):
                score = (3 if LEGAL_SUFFIX.search(seg) else 0) + (1 if seg.isupper() else 0)
                found.append((score, -i, seg))  # -i: the top of the block above, the nearest below
        return found

    # Some bills print the GSTIN in a strip across the top, the name under it.
    candidates = names(range(max(0, idx - 24), idx)) or names(range(idx + 1, min(len(lines), idx + 7)))
    if not candidates:
        return None
    return max(candidates)[2][:200]


def bill_number_like(s: str) -> bool:
    """A bill number has a digit in it ("e-Invoice" beside the title is not
    one) and is not a date. "93/2026-27", number and financial year, is a
    bill number."""
    return bool(re.search(r"\d", s)) and not re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", s)


def _parse_invoice_no(p: ParsedInvoice, lines: list[str], flat: str) -> None:
    for tail, whole in _labelled(lines, INVOICE_NO_LABELS):
        m = re.search(r"([A-Za-z0-9][A-Za-z0-9\-/_\\]{2,40})", tail)
        if m and bill_number_like(m.group(1)):
            p.put("invoice_no", m.group(1).strip(" -/"), 0.95, "regex", whole)
            return
    # "TAX INVOICE 5500100-26004852" -- the number sits on the title line.
    m = re.search(r"tax\s+invoice[ \t]+([A-Z0-9][A-Z0-9\-/]{4,40})", flat, re.I)
    if m and bill_number_like(m.group(1)):
        p.put("invoice_no", m.group(1).strip(" -/"), 0.85, "regex", m.group(0))


def _parse_dates(p: ParsedInvoice, lines: list[str]) -> None:
    for tail, whole in _labelled(lines, DUE_DATE_LABELS):
        d = to_date(tail)
        if d:
            p.put("due_date", d, 0.95, "regex", whole)
            break
    for tail, whole in _labelled(lines, INVOICE_DATE_LABELS):
        if re.search(r"due\s*date", whole, re.I) and not re.search(r"(?<!due )\bdate\b", tail, re.I):
            continue
        d = to_date(tail)
        if d:
            p.put("invoice_date", d, 0.95, "regex", whole)
            break


def _parse_place_of_supply(p: ParsedInvoice, lines: list[str], flat: str) -> None:
    m = re.search(r"place\s*of\s*supply\s*[:\-]?\s*(\d{2})?\s*([A-Za-z &]+)?", flat, re.I)
    if not m:
        return
    code = m.group(1)
    if not code and m.group(2):
        want = m.group(2).strip().lower()
        code = next((c for c, n in gst.STATE_CODES.items() if n.lower() == want), None)
    if code and code in gst.STATE_CODES:
        p.put("place_of_supply", code, 0.95, "regex", m.group(0).strip())


def _parse_lines(p: ParsedInvoice, lines: list[str]) -> None:
    start, end = _item_block(lines)
    block = lines[start:end]
    out: list[dict[str, Any]] = []

    for ln in block:
        if TOTALS_STOP.search(ln) or RE_TAX_ROW.search(ln):
            continue
        row = _match_line(ln)
        if row:
            out.append(row)
        elif out and len(ln.strip()) > 3 and not re.fullmatch(r"[\s\d.,%-]+", ln):
            # Continuation text ("AUDITOR: ...") belongs to the line above and
            # is worth keeping -- it is often the only thing distinguishing
            # two otherwise identical fee lines.
            extra = ln.strip()
            if len(extra) > 2:
                out[-1]["description_raw"] = f"{out[-1]['description_raw']} | {extra}"[:800]

    for i, row in enumerate(out):
        row["line_no"] = i
        q, r, a = row.get("quantity"), row.get("rate"), row.get("amount")
        # qty x rate == amount is a free integrity check on our own parse.
        row["arith_ok"] = bool(q and r and a and gst.close(q * r, a, "1.00"))
    p.lines = out


def _item_block(lines: list[str]) -> tuple[int, int]:
    start = 0
    for i, ln in enumerate(lines):
        low = ln.lower()
        if sum(any(k in low for k in group) for group in ITEM_HEADER_HINTS) >= 2:
            start = i + 1
            break
    end = len(lines)
    for j in range(start, len(lines)):
        if TOTALS_STOP.search(lines[j]) and not RE_LINE_FULL.match(lines[j]):
            end = j
            break
    return start, end


def _match_line(ln: str) -> Optional[dict[str, Any]]:
    m = RE_LINE_FULL.match(ln)
    if m:
        return {
            "description_raw": m.group("desc").strip(" .:-"),
            "hsn_sac": m.group("hsn"),
            "quantity": to_decimal(m.group("qty")),
            "rate": to_decimal(m.group("rate")),
            "amount": to_decimal(m.group("amt")),
            "uom": None,
        }
    m = RE_LINE_NOHSN.match(ln)
    if m:
        return {
            "description_raw": m.group("desc").strip(" .:-"),
            "hsn_sac": None,
            "quantity": to_decimal(m.group("qty")),
            "uom": (m.group("uom") or None),
            "rate": to_decimal(m.group("rate")),
            "amount": to_decimal(m.group("amt")),
        }
    m = RE_LINE_NOQTY.match(ln)
    if m and to_decimal(m.group("amt")):
        return {
            "description_raw": m.group("desc").strip(" .:-"),
            "hsn_sac": m.group("hsn"),
            "quantity": None,
            "rate": None,
            "amount": to_decimal(m.group("amt")),
            "uom": None,
        }
    return None


def _parse_taxes(p: ParsedInvoice, lines: list[str]) -> None:
    """Tax rows are laid out too many ways to pin with one regex. Find the row
    by keyword, then pick the number that the rate actually predicts -- a
    summary row often repeats the taxable value and the total on the same line,
    and 'the last number' picks the wrong one about as often as it picks right.
    """
    taxable = p.get("taxable_total")
    buckets: dict[str, tuple[Decimal, float, str, bool]] = {}

    for ln in lines:
        m = RE_TAX_ROW.search(ln)
        if not m:
            continue
        kind = m.group("kind").lower()
        kind = "sgst" if kind == "utgst" else kind

        nums = [d for d in (to_decimal(x) for x in RE_NUMS.findall(ln)) if d is not None]
        if not nums:
            continue

        pm = RE_PCT.search(ln)
        rate = float(pm.group(1)) if pm else None
        if rate is None:
            # Unlabelled rate column: a bare number that is a legal GST rate.
            rate = next((float(n) for n in nums if float(n) in gst.VALID_GST_RATES and n > 0), None)

        amt: Optional[Decimal] = None
        exact = False
        if rate and taxable:
            base = taxable / 2 if kind in ("cgst", "sgst") else taxable
            want = base * Decimal(str(rate)) / Decimal(100)
            best = min(nums, key=lambda n: abs(n - want))
            if gst.close(best, want, "2.00"):
                amt, exact = best, True
        if amt is None:
            # Fall back to the last number, excluding the rate and the taxable
            # value themselves so a summary row cannot pass those off as tax.
            drop = {Decimal(str(rate))} if rate else set()
            if taxable is not None:
                drop.add(taxable)
            cand = [n for n in nums if n not in drop]
            amt = cand[-1] if cand else nums[-1]

        prev = buckets.get(kind)
        # Prefer a rate-confirmed figure; otherwise the largest (summary beats
        # a per-line fragment).
        if prev is None or (exact and not prev[3]) or (exact == prev[3] and amt > prev[0]):
            buckets[kind] = (amt, rate or 0.0, ln.strip(), exact)

    for kind, (amt, rate, ev, exact) in buckets.items():
        p.put(kind, amt, 0.96 if exact else 0.75, "regex", ev)
        if rate:
            p.put(f"{kind}_rate", rate, 0.92, "regex", ev)


def _parse_totals(p: ParsedInvoice, lines: list[str]) -> None:
    for key, labels, conf in (
        ("grand_total", TOTAL_LABELS, 0.92),
        ("taxable_total", TAXABLE_LABELS, 0.92),
        ("round_off", ROUNDOFF_LABELS, 0.90),
    ):
        best: Optional[Decimal] = None
        ev = None
        for tail, whole in _labelled(lines, labels):
            v = _last_number(tail)
            if v is None:
                continue
            # "Total" also appears as "GST Total"; the grand total is the
            # biggest number wearing a total-ish label.
            if best is None or (key == "grand_total" and v > best):
                best, ev = v, whole
            if key != "grand_total":
                break
        if best is not None:
            p.put(key, best, conf, "regex", ev)

    m = re.search(r"\b(?:currency|curr\.?)\s*[:\-]?\s*([A-Z]{3})\b", "\n".join(lines), re.I)
    if m:
        p.put("currency", m.group(1).upper(), 0.95, "regex", m.group(0))


def _reconcile(p: ParsedInvoice) -> None:
    """Fill holes by arithmetic before ever asking a model to guess."""
    tax = sum((p.get(k) or Decimal(0)) for k in ("cgst", "sgst", "igst", "cess"))
    taxable, total = p.get("taxable_total"), p.get("grand_total")
    line_sum = sum((r.get("amount") or Decimal(0)) for r in p.lines) if p.lines else None

    if taxable is None and line_sum:
        p.put("taxable_total", line_sum, 0.85, "inferred", "sum of parsed line amounts")
        taxable = line_sum
    if taxable is None and total is not None and tax:
        p.put("taxable_total", total - tax, 0.80, "inferred", "grand total minus tax")
        taxable = total - tax
    if total is None and taxable is not None:
        p.put("grand_total", taxable + tax, 0.80, "inferred", "taxable value plus tax")

    # No tax row found but the gap equals a standard rate? Then it is IGST/CGST+SGST
    # that the layout hid, not a zero-rated invoice.
    if not tax and taxable and total and total > taxable:
        diff = total - taxable
        pct = float(diff / taxable * 100) if taxable else 0.0
        near = min(gst.VALID_GST_RATES, key=lambda r: abs(r - pct), default=None)
        if near and abs(near - pct) < 0.15 and near > 0:
            p.put("implied_tax_rate", near, 0.60, "inferred", f"total exceeds taxable by {pct:.2f}%")
            p.put("implied_tax_amount", diff, 0.60, "inferred", "grand total minus taxable value")
