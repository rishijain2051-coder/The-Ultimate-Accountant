"""
Shared primitives for the GST reconciliation engine.

Normalisation, fingerprinting, financial-year arithmetic, GST rate tables and
money helpers used by the parser, the reconciler, the insight engine and the
exporters. Kept in one place so every module agrees on what "the same invoice"
means.
"""

import hashlib
import re
from datetime import datetime, date, timedelta

# ---------------------------------------------------------------------------
# GST domain constants
# ---------------------------------------------------------------------------

# Statutory ad-valorem slabs in force for goods and services.
GST_RATES = [0.0, 0.1, 0.25, 1.0, 1.5, 3.0, 5.0, 6.0, 7.5, 12.0, 18.0, 28.0]

MAX_GST_RATE = 28.0

RATE_MATCH_TOLERANCE = 0.30  # percentage points

# Invoice-type strings used by the GST portal in 2B exports.
CREDIT_NOTE_TYPES = {"credit note", "c", "cn"}
DEBIT_NOTE_TYPES = {"debit note", "d", "dn"}

# 2B "Reason" strings the portal emits when ITC is blocked.
INELIGIBLE_REASON_HINTS = {
    "pos and supplier state are same but recipient state is different":
        "Place of supply is the supplier state - CGST/SGST cannot be credited to another state.",
    "return filed post annual cut-off":
        "Supplier filed after the section 16(4) cut-off - the credit has lapsed.",
    "invoice / debit note date is before merger effective date":
        "Document predates the merger effective date recorded on the portal.",
    "invoice / debit note date is after cancellation date":
        "Supplier registration was cancelled before this document date.",
}

AGING_BUCKETS = [
    ("0-30 days", 0, 30),
    ("31-60 days", 31, 60),
    ("61-90 days", 61, 90),
    ("91-180 days", 91, 180),
    ("181-365 days", 181, 365),
    ("Over 1 year", 366, 10 ** 6),
]

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman & Diu", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

# Tally voucher types that never carry input tax credit.
NON_GST_VCH_HINTS = ("purchase", "journal", "payment", "receipt", "contra")

# Corporate suffixes stripped before comparing ledger names to trade names.
_PARTY_NOISE = (
    "PRIVATELIMITED", "PVTLTD", "PRIVATELTD", "LIMITED", "LTD", "LLP", "LLC",
    "ANDCOMPANY", "ANDCO", "COMPANY", "CO", "ENTERPRISES", "ENTERPRISE",
    "TRADERS", "TRADING", "INDIA", "CORPORATION", "CORP", "INC", "SONS", "BROS",
    "BROTHERS", "AGENCIES", "AGENCY", "INDUSTRIES", "INDUSTRY",
)


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def to_float(val, default=0.0):
    """Coerce an Excel cell value to float, tolerating currency noise."""
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return default
    negative = s.startswith("(") and s.endswith(")")
    cleaned = re.sub(r"[^\d.\-]", "", s)
    if cleaned in ("", "-", ".", "-."):
        return default
    try:
        out = float(cleaned)
    except ValueError:
        return default
    return -out if negative and out > 0 else out


_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d",
    "%d.%m.%Y", "%d.%m.%y", "%d %B %Y", "%d-%B-%Y", "%d %b %y",
)


def to_date(val):
    """Coerce an Excel cell value to a naive datetime, or None."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, (int, float)):
        # Excel serial dates, guarded to a plausible 1987-2118 window.
        if 32000 < float(val) < 80000:
            return datetime(1899, 12, 30) + timedelta(days=float(val))
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Tally sometimes exports "2025-04-01 00:00:00".
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def iso(dt):
    """Render a date as YYYY-MM-DD, or None."""
    if isinstance(dt, (datetime, date)):
        return dt.strftime("%Y-%m-%d")
    return None


def r2(val):
    """Round to paise, treating None as None rather than zero."""
    if val is None:
        return None
    return round(float(val) + 0.0, 2)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def clean_inv(inv):
    """Reduce an invoice or voucher number to its comparable core.

    Separators are dropped and each group is stripped of its own leading zeros,
    so 25-26/0376 and 25-26/376 are recognised as the same document. Stripping
    zeros only from the front of the whole string would leave those apart.
    """
    if not inv:
        return ""
    parts = re.split(r"[^A-Z0-9]+", str(inv).strip().upper())
    return "".join(p.lstrip("0") or ("0" if p else "") for p in parts if p)


def inv_tail(inv, length=4):
    """Trailing digits of an invoice number - the part humans actually type."""
    digits = re.sub(r"[^0-9]", "", str(inv or "")).lstrip("0")
    return digits[-length:] if len(digits) >= length else digits


def tails_match(a, b, length=4, minimum=3):
    """True when two document numbers end in the same run of digits.

    Compared at the shorter of the two lengths, because a voucher keyed as
    "455" is the same document as invoice "BW/25-26/0455". Runs shorter than
    `minimum` digits are coincidence rather than evidence.
    """
    da = re.sub(r"[^0-9]", "", str(a or "")).lstrip("0")
    db = re.sub(r"[^0-9]", "", str(b or "")).lstrip("0")
    if not da or not db:
        return False
    n = min(length, len(da), len(db))
    if n < minimum:
        return False
    return da[-n:] == db[-n:]


def clean_party(name):
    """Reduce a party name to a comparable core."""
    if not name:
        return ""
    s = str(name).strip().upper()
    s = re.sub(r"\(.*?\)", "", s)               # drop bracketed branch/GSTIN notes
    s = re.sub(r"^\s*M/?S\.?\s*", "", s)        # drop the M/s honorific
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def party_core(name):
    """Party name with corporate suffixes removed, for stricter comparison."""
    s = clean_party(name)
    for noise in _PARTY_NOISE:
        if len(s) > len(noise) + 3 and s.endswith(noise):
            s = s[: -len(noise)]
    return s


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def party_sim(a, b):
    """Similarity of two party names in 0.0-1.0, tuned for Indian trade names."""
    ca, cb = clean_party(a), clean_party(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ka, kb = party_core(a), party_core(b)
    if ka and kb and ka == kb:
        return 0.97
    if len(ca) >= 5 and (ca in cb or cb in ca):
        return 0.94
    if ka and kb and len(ka) >= 5 and (ka in kb or kb in ka):
        return 0.90
    # Dice coefficient over character bigrams: order-tolerant and fast.
    ba, bb = _bigrams(ka or ca), _bigrams(kb or cb)
    inter = len(ba & bb)
    if not inter:
        return 0.0
    return round((2.0 * inter) / (len(ba) + len(bb)), 4)


def fingerprint(*parts):
    """Stable short id for an invoice-level record, used to carry user
    decisions across re-runs of the same period."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fp_2b(gstin, inv_no, value):
    return fingerprint("2B", (gstin or "").upper(), clean_inv(inv_no), round(to_float(value), 2))


def fp_tally(party, vch_no, amount):
    return fingerprint("TL", party_core(party), clean_inv(vch_no), round(to_float(amount), 2))


# ---------------------------------------------------------------------------
# Financial year arithmetic
# ---------------------------------------------------------------------------

def fy_bounds(fy_start_year):
    """First and last day of an Indian financial year."""
    return datetime(fy_start_year, 4, 1), datetime(fy_start_year + 1, 3, 31)


def fy_label(fy_start_year):
    return f"FY {fy_start_year}-{str(fy_start_year + 1)[2:]}"


def fy_of(dt):
    """Financial year start-year containing a date."""
    if not isinstance(dt, (datetime, date)):
        return None
    return dt.year if dt.month >= 4 else dt.year - 1


_PERIOD_DATE = r"(\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ,]{0,2}\d{2,4})"
_PERIOD = re.compile(
    _PERIOD_DATE + r"\s*(?:to|upto|up\s*to|till|until|-|–)\s*" + _PERIOD_DATE, re.I)


def parse_period(text):
    """The period a document says it covers, from its own header.

    Tally prints "1-Apr-24 to 31-Mar-25", statements say "FROM 01/04/2024 UPTO
    31/03/2025", and recognition often drops the spaces in between, so the
    pattern tolerates all of that. Returns (from, to) datetimes or (None, None).
    """
    for m in _PERIOD.finditer(str(text or "")):
        start, end = (to_date(re.sub(r"\s+", " ", g).strip(" ,")) for g in m.groups())
        if start and end and start <= end and (end - start).days <= 800:
            return start, end
    return None, None


def sec_164_deadline(fy_start_year):
    """Section 16(4) outer date for claiming ITC of a financial year:
    30 November of the following financial year."""
    return datetime(fy_start_year + 1, 11, 30)


def month_key(dt):
    """Sortable month key, e.g. 2025-04."""
    if not isinstance(dt, (datetime, date)):
        return None
    return f"{dt.year:04d}-{dt.month:02d}"


def month_label(key):
    if not key:
        return "Unknown"
    try:
        y, m = str(key).split("-")
        return datetime(int(y), int(m), 1).strftime("%b %y")
    except (ValueError, TypeError):
        return str(key)


def fy_month_order(fy_start_year):
    """The twelve month keys of a financial year, April first."""
    out = []
    y, m = fy_start_year, 4
    for _ in range(12):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def state_of_gstin(gstin):
    code = str(gstin or "")[:2]
    return STATE_CODES.get(code, "")


def pan_of_gstin(gstin):
    g = str(gstin or "")
    return g[2:12] if len(g) >= 12 else ""


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

def inr(value, decimals=2, symbol=True):
    """Format a number in the Indian grouping system (lakh / crore)."""
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    neg = num < 0
    num = abs(num)
    whole = int(num)
    frac = num - whole
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    if decimals:
        s = s + "." + f"{frac:.{decimals}f}"[2:]
    if symbol:
        s = "₹" + s
    return ("−" + s) if neg else s


def compact_inr(value):
    """Short crore/lakh rendering for headline figures."""
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "−" if num < 0 else ""
    a = abs(num)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.2f} L"
    if a >= 1e3:
        return f"{sign}₹{a / 1e3:,.1f} K"
    return f"{sign}₹{a:,.0f}"


def effective_rate(taxable, tax):
    """Ad-valorem rate implied by a taxable value and a tax amount."""
    t = to_float(taxable)
    if t <= 0.5:
        return None
    return round((to_float(tax) / t) * 100.0, 2)


def nearest_slab(rate):
    """Closest statutory slab to an observed rate, and whether it sits on it.

    A document whose implied rate lands between two slabs is usually a
    mixed-rate invoice rather than an error, so callers treat a False here as
    "mixed", not "wrong". Only `rate_is_impossible` marks a genuine defect.
    """
    if rate is None:
        return None, False
    best = min(GST_RATES, key=lambda r: abs(r - rate))
    return best, abs(best - rate) <= RATE_MATCH_TOLERANCE


def rate_is_impossible(rate):
    """True when no combination of statutory slabs could produce this rate."""
    if rate is None:
        return False
    return rate < -RATE_MATCH_TOLERANCE or rate > MAX_GST_RATE + 0.5


def tax_split_problem(igst, cgst, sgst):
    """Describe an internally inconsistent tax split, or None when it is sound.

    A supply is either inter-state (IGST only) or intra-state (CGST and SGST in
    equal halves). Anything else means the supplier mis-keyed the invoice.
    """
    i, c, s = to_float(igst), to_float(cgst), to_float(sgst)
    if abs(i) > 0.5 and (abs(c) > 0.5 or abs(s) > 0.5):
        return "Invoice carries IGST together with CGST/SGST - a supply cannot be both inter-state and intra-state."
    if abs(c - s) > 1.0:
        return "CGST and SGST are unequal - the intra-state split must be two equal halves."
    return None


def serialize(obj):
    """Recursively make a structure JSON-safe."""
    if isinstance(obj, (datetime, date)):
        return iso(obj)
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(v) for v in obj]
    if isinstance(obj, set):
        return sorted(serialize(v) for v in obj)
    return obj
