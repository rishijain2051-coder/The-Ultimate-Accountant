"""
Bank reconciliation.

Two halves. The first reads a bank statement from whatever the bank gave you —
a spreadsheet, a CSV, a generated PDF, or a scan — and turns each line into a
transaction with its channel and counterparty pulled out of the narration. The
second reconciles those against the Tally bank book and produces a bank
reconciliation statement.

The BRS is not assembled from rules of thumb. It is derived from an identity
that always holds:

    bank_closing - book_closing = (bank_opening - book_opening)
                                + sum(bank-only movements)
                                - sum(book-only movements)
                                + sum(matched: bank movement - book movement)

Each term is then given its conventional name — unpresented cheques,
uncredited deposits, bank charges not in books, and so on. Because the
statement is computed from an identity rather than from a checklist, it ties
out every time, and when it does not the arithmetic says so instead of quietly
hiding a parsing error.
"""

import re
from datetime import datetime, timedelta

from rapidfuzz import fuzz
from rapidfuzz import utils as fuzz_utils

from app.backend.common import (
    clean_inv, fingerprint, iso, month_key, party_core, party_sim, r2, to_date,
    to_float,
)

# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

BANK_FIELD_RULES = [
    ("value_date", (r"value\s*(date|dt)", r"^val\s*dt")),
    ("date",       (r"^(txn|tran|transaction|posting|post|book)\s*(date|dt)",
                    r"^date$", r"^dt$", r"\bdate\b")),
    ("narration",  (r"narration", r"description", r"particular", r"remark",
                    r"transaction\s*details", r"^details$", r"^narrative$")),
    ("ref",        (r"chq.*(no|ref)", r"cheque.*(no|ref)", r"ref(erence)?\s*(no|number)?",
                    r"instrument", r"utr", r"^chq", r"^ref$")),
    ("debit",      (r"withdraw", r"^debit", r"\bdr\b.*amount", r"^dr$",
                    r"paid\s*out", r"amount\s*\(dr\)")),
    ("credit",     (r"deposit", r"^credit", r"\bcr\b.*amount", r"^cr$",
                    r"paid\s*in", r"amount\s*\(cr\)")),
    ("balance",    (r"balance", r"^bal$", r"running\s*total")),
    ("amount",     (r"^amount$", r"^amt$", r"transaction\s*amount")),
    ("drcr",       (r"^(dr\s*/\s*cr|cr\s*/\s*dr|type|indicator|txn\s*type)$",)),
    ("branch",     (r"branch",)),
]

_BANK_COMPILED = [(f, tuple(re.compile(p) for p in pats)) for f, pats in BANK_FIELD_RULES]

# ---------------------------------------------------------------------------
# Narration intelligence
# ---------------------------------------------------------------------------

CHANNELS = [
    # Order matters, and it is ordered by what a reconciliation needs to know.
    # A line reading "NEFT RETURN CHARGES" is a charge first and a transfer
    # second, so the nature of the entry is tested before the rail it used.
    #
    # Each pattern opens on a word boundary but deliberately does not close on
    # one: recognition glues the rail to the next token, producing NEFTCR and
    # NEFTDR. The opening boundary has to stay, or "pos" would match inside
    # "DEPOSIT" and every deposit would read as a card payment.
    ("TAX",      (r"\bgst\b", r"\bgst\s*on", r"\btds\b", r"\btcs\b",
                  r"\bigst", r"\bcgst", r"\bsgst")),
    ("INTEREST", (r"\bint\.?\s*(pd|paid|cr)", r"\binterest", r"\bint\.pd")),
    ("CHARGES",  (r"\bcharges?\b", r"\bfee\b", r"\bcommission\b", r"\bpenal",
                  r"\bsms\s*chg", r"\bamc\b", r"\bfolio\b", r"\bincidental")),
    ("LOAN",     (r"\bemi\b", r"\bloan\b", r"\brepayment", r"\bprincipal\b")),
    ("SALARY",   (r"\bsalary", r"\bwages\b", r"\bpayroll")),
    ("CHEQUE",   (r"\bchq", r"\bcheque", r"\bclg\b", r"\bclearing\b")),
    ("NEFT",     (r"\bneft",)),
    ("RTGS",     (r"\brtgs",)),
    ("IMPS",     (r"\bimps",)),
    ("UPI",      (r"\bupi", r"@[a-z]{2,}\b", r"\bbhim")),
    ("ACH",      (r"\bach\b", r"\bach\s*[dc]\b", r"\becs\b", r"\bnach",
                  r"\bsi\s*-", r"standing\s*instr")),
    ("ATM",      (r"\batm", r"\bawb\b", r"\bcash\s*wdl", r"\bnwd\b")),
    ("POS",      (r"\bpos\b", r"\becom\b", r"\bcard\s*payment", r"\bvisa\b",
                  r"\bmastercard\b", r"\brupay\b")),
    ("CASH",     (r"\bcash\s*(dep|deposit|withdrawal|wdl)", r"\bby\s*cash\b",
                  r"\bcdm\b", r"\bcash\s*/\s*chq")),
    ("TRANSFER", (r"\btransfer", r"\bft\b", r"\bself\b", r"\bsweep")),
    ("REVERSAL", (r"\breversal", r"\breturn(ed)?\b", r"\bfailed\b", r"\brefund")),
]

_CHAN_COMPILED = [(c, tuple(re.compile(p, re.I) for p in pats)) for c, pats in CHANNELS]

# Tokens that are never part of a counterparty name.
_NOISE = re.compile(
    r"\b(neft|rtgs|imps|upi|ach|nach|ecs|chq|cheque|clg|clearing|paid|dr|cr|"
    r"pos|atm|cdm|ft|tfr|transfer|by|to|from|ref|no|inf|inft|bil|billdesk|"
    r"razorpay|payu|bharatpe|paytm|phonepe|gpay|instr|instrument|deposit|"
    r"withdrawal|wdl|charges?|gst|tds|int|interest|emi|loan|sal|salary|"
    r"payment|paymt|pymt|towards|against|acct|account|limited|ltd)\b",
    re.I)

# Recognition glues the rail onto the name: NEFTCR-RAJPUTEXPORTS. These have to
# come off before the noise pass, which can only remove standalone words.
_GLUED_RAIL = re.compile(r"\b(NEFT|RTGS|IMPS|UPI|ACH|NACH|ECS|CHQ)(CR|DR)?", re.I)
# A truncated reference number leaves a stray initial hanging off the name.
_TRAILING_STUB = re.compile(r"(?:\s+[A-Z])+$")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_UPI_HANDLE = re.compile(r"\b[\w.\-]+@[a-z]{2,}\b", re.I)
_LONG_NUM = re.compile(r"\b[A-Z]{0,4}\d{6,}\b", re.I)
_DATE_IN_TEXT = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
_ACCOUNT = re.compile(r"\bX{2,}\d{2,}\b|\bxx+\d+\b", re.I)

REF_PATTERNS = [
    ("utr",    re.compile(r"\b([A-Z]{4}[A-Z0-9]\d{6,}|\d{12,22})\b")),
    ("cheque", re.compile(r"\b(\d{6})\b")),
    ("upi",    re.compile(r"\b(\d{12})\b")),
]

LEADING_DATE = re.compile(
    r"^\s*(\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ]\d{2,4})\s*(.*)$", re.S)

# Cheques go stale three months after their date.
STALE_CHEQUE_DAYS = 90
# Section 269ST and cash-reporting thresholds worth surfacing.
CASH_ALERT = 200000.0


def classify_narration(narration):
    """Channel, counterparty and reference numbers out of a bank narration."""
    text = re.sub(r"\s+", " ", str(narration or "")).strip()
    if not text:
        return {"channel": "UNKNOWN", "counterparty": "", "refs": [], "raw": ""}

    channel = "UNKNOWN"
    for name, patterns in _CHAN_COMPILED:
        if any(p.search(text) for p in patterns):
            channel = name
            break

    # Strip the IFSC before hunting for references: HDFC0000123 otherwise
    # reads as a UTR and can create a false reference match.
    ref_source = _IFSC.sub(" ", text.upper())
    refs = []
    for _, pat in REF_PATTERNS:
        for m in pat.finditer(ref_source):
            if m.group(1) not in refs:
                refs.append(m.group(1))

    # Strip everything that is structurally not a name, then take the longest
    # surviving alphabetic run.
    cleaned = text.upper()
    for pat in (_IFSC, _UPI_HANDLE, _ACCOUNT, _DATE_IN_TEXT, _LONG_NUM):
        cleaned = pat.sub(" ", cleaned)
    cleaned = _GLUED_RAIL.sub(" ", cleaned)
    cleaned = _NOISE.sub(" ", cleaned)
    cleaned = re.sub(r"[^A-Z& ]+", " ", cleaned)
    parts = [p.strip() for p in re.split(r"\s{2,}|(?<=\s)&(?=\s)", cleaned) if p.strip()]
    candidates = [p for p in parts if len(p) >= 4 and len(p.split()) <= 8]
    counterparty = max(candidates, key=len).strip() if candidates else ""
    counterparty = re.sub(r"\s+", " ", counterparty)
    # A truncated reference leaves a stray initial hanging off the name.
    counterparty = _TRAILING_STUB.sub("", counterparty).strip()

    return {"channel": channel, "counterparty": counterparty, "refs": refs, "raw": text}


def split_leading_date(cell):
    """Pull a date off the front of a cell.

    Recognition often merges the date column into the narration when the gap
    between them is narrow, so a row can arrive as one string beginning with a
    date. Splitting it here keeps that out of every caller.
    """
    m = LEADING_DATE.match(str(cell or ""))
    if not m:
        return None, str(cell or "").strip()
    return to_date(m.group(1)), m.group(2).strip()


# ---------------------------------------------------------------------------
# Statement parsing
# ---------------------------------------------------------------------------

_OPENING_LABEL = re.compile(
    r"^(opening\s*balance|balance\s*b/?f|balance\s*brought\s*forward)$", re.I)


def _is_opening_row(row, joined):
    """An opening-balance row, wherever the label sits.

    Tally puts the date in the first cell and the label in the second, so
    anchoring the match to the start of the row misses it entirely.
    """
    if any(_OPENING_LABEL.match(str(c).strip()) for c in row):
        return True
    return bool(re.match(r"^\s*(opening\s*balance|balance\s*b/?f)", joined, re.I))


_SKIP_ROW = re.compile(
    r"^(opening\s*balance|closing\s*balance|b/?f|brought\s*forward|carried\s*forward|"
    r"total|sub\s*total|statement\s*summary|\*+|end\s*of\s*statement|page\s*\d+)",
    re.I)


def _map_columns(header_row):
    colmap, labels = {}, []
    for i, cell in enumerate(header_row):
        label = re.sub(r"\s+", " ", str(cell or "")).strip().lower()
        label = label.replace(".", " ").strip()
        labels.append(label)
        if not label:
            continue
        for field, patterns in _BANK_COMPILED:
            if field in colmap:
                continue
            if any(p.search(label) for p in patterns):
                colmap[field] = i
                break
    return colmap, labels


def find_header(grid, max_scan=30):
    """Locate the header row of a statement grid and map its columns."""
    best, best_score, best_map = None, 0, {}
    for i, row in enumerate(grid[:max_scan]):
        colmap, _ = _map_columns(row)
        score = 0
        if "date" in colmap or "value_date" in colmap:
            score += 2
        if "narration" in colmap:
            score += 2
        if "debit" in colmap and "credit" in colmap:
            score += 3
        elif "amount" in colmap:
            score += 2
        if "balance" in colmap:
            score += 1
        if "ref" in colmap:
            score += 1
        if score > best_score:
            best, best_score, best_map = i, score, colmap
    return (best, best_map) if best_score >= 4 else (None, {})


def parse_bank_statement(grid, account=None, filename=None):
    """
    Read a bank statement grid into transactions.

    Returns {'transactions': [...], 'meta': {...}, 'issues': [...]}
    """
    header_idx, colmap = find_header(grid)
    issues = []
    if header_idx is None:
        raise ValueError(
            "Could not find the columns of that bank statement. It needs a header "
            "row with a date, a narration or description, and either separate "
            "withdrawal and deposit columns or one amount column with a Dr/Cr flag."
        )

    def cell(row, field, default=""):
        idx = colmap.get(field)
        if idx is None or idx >= len(row):
            return default
        val = row[idx]
        return default if val is None else val

    header_text = " ".join(str(c) for r in grid[:header_idx] for c in r)
    meta = {
        "bank": _guess_bank(header_text),
        "account": account or _guess_account(header_text),
        "ifsc": (_IFSC.search(header_text.upper()).group(0)
                 if _IFSC.search(header_text.upper()) else ""),
        "source_file": filename or "",
        "header_row": header_idx + 1,
        "columns_found": sorted(colmap.keys()),
    }

    opening = None
    for row in grid[:header_idx + 4]:
        joined = " ".join(str(c) for c in row).lower()
        if _is_opening_row(row, joined) or "opening balance" in joined:
            nums = [to_float(c) for c in row if to_float(c, None) is not None]
            nums = [n for n in nums if n]
            if nums:
                opening = r2(nums[-1])
                break

    txns = []
    for r in range(header_idx + 1, len(grid)):
        row = grid[r]
        if not any(str(c).strip() for c in row):
            continue
        first = str(cell(row, "date") or (row[0] if row else "")).strip()
        joined = " ".join(str(c) for c in row).strip()
        if _SKIP_ROW.match(first) or _SKIP_ROW.match(joined):
            if opening is None and _is_opening_row(row, joined):
                nums = [to_float(c) for c in row if to_float(c, None) is not None]
                nums = [n for n in nums if n]
                if nums:
                    opening = r2(nums[-1])
            continue

        date = to_date(cell(row, "date"))
        narration = str(cell(row, "narration")).strip()
        if date is None:
            # A merged date+narration cell, which recognition produces often.
            d2, rest = split_leading_date(cell(row, "date") or narration)
            if d2 is not None:
                date, narration = d2, (rest or narration)
        if date is None:
            date = to_date(cell(row, "value_date"))
        if date is None:
            continue

        debit = to_float(cell(row, "debit"))
        credit = to_float(cell(row, "credit"))
        if not debit and not credit and "amount" in colmap:
            amt = to_float(cell(row, "amount"))
            flag = str(cell(row, "drcr")).strip().upper()
            if flag.startswith("D") or amt < 0:
                debit = abs(amt)
            else:
                credit = abs(amt)
        if not debit and not credit:
            continue

        balance = to_float(cell(row, "balance"), None)
        info = classify_narration(narration)
        ref = str(cell(row, "ref")).strip()
        if not ref and info["refs"]:
            ref = info["refs"][0]

        txns.append({
            "id": len(txns),
            "date": date,
            "value_date": to_date(cell(row, "value_date")) or date,
            "narration": narration,
            "ref": ref,
            "debit": r2(debit),
            "credit": r2(credit),
            "net": r2(credit - debit),
            "balance": r2(balance) if balance is not None else None,
            "channel": info["channel"],
            "counterparty": info["counterparty"],
            "refs": info["refs"],
            "month_key": month_key(date),
            "fp": fingerprint("BK", iso(date), clean_inv(ref),
                              round(credit - debit, 2), info["counterparty"][:18]),
            "source_row": r + 1,
            "flags": [],
        })

    if not txns:
        raise ValueError("The statement header was found but no transaction rows "
                         "could be read below it.")

    # A statement whose own running balance does not add up has been misread or
    # edited; either way the reconciliation cannot be trusted until it is fixed.
    continuity = _check_continuity(txns, opening)
    if continuity["breaks"]:
        issues.append(
            f"The running balance does not tie on {len(continuity['breaks'])} row(s) "
            f"(first at row {continuity['breaks'][0]['row']}). The statement may have "
            f"been edited, or a column was read wrongly."
        )
        for b in continuity["breaks"]:
            for t in txns:
                if t["source_row"] == b["row"]:
                    t["flags"].append("balance_break")

    closing = txns[-1]["balance"]
    if closing is None and opening is not None:
        closing = r2(opening + sum(t["net"] for t in txns))
    if opening is None and txns[0]["balance"] is not None:
        opening = r2(txns[0]["balance"] - txns[0]["net"])

    meta.update({
        "count": len(txns),
        "opening_balance": opening,
        "closing_balance": closing,
        "total_debits": r2(sum(t["debit"] for t in txns)),
        "total_credits": r2(sum(t["credit"] for t in txns)),
        "first_date": iso(min(t["date"] for t in txns)),
        "last_date": iso(max(t["date"] for t in txns)),
        "continuity_ok": not continuity["breaks"],
        "channels": _channel_summary(txns),
    })
    return {"transactions": txns, "meta": meta, "issues": issues}


def _check_continuity(txns, opening):
    """Walk the statement's own balance column and report where it stops adding up."""
    breaks = []
    prev = opening
    for t in txns:
        if t["balance"] is None:
            prev = None
            continue
        if prev is not None and abs(r2(prev + t["net"]) - t["balance"]) > 1.0:
            breaks.append({"row": t["source_row"], "expected": r2(prev + t["net"]),
                           "found": t["balance"],
                           "gap": r2(t["balance"] - (prev + t["net"]))})
        prev = t["balance"]
    return {"breaks": breaks}


def _channel_summary(txns):
    out = {}
    for t in txns:
        c = out.setdefault(t["channel"], {"channel": t["channel"], "count": 0,
                                          "debit": 0.0, "credit": 0.0})
        c["count"] += 1
        c["debit"] += t["debit"]
        c["credit"] += t["credit"]
    for c in out.values():
        c["debit"] = r2(c["debit"])
        c["credit"] = r2(c["credit"])
    return sorted(out.values(), key=lambda c: -(c["debit"] + c["credit"]))


# Matched against the statement header, longest first so "State Bank of India"
# wins over "SBI". The display name is spelled out rather than derived: any
# length-based rule turns ICICI into "Icici".
BANK_NAMES = [
    ("STATE BANK OF INDIA", "State Bank of India"),
    ("PUNJAB NATIONAL", "Punjab National Bank"),
    ("BANK OF BARODA", "Bank of Baroda"),
    ("AU SMALL FINANCE", "AU Small Finance Bank"),
    ("CENTRAL BANK", "Central Bank of India"),
    ("INDIAN BANK", "Indian Bank"),
    ("UNION BANK", "Union Bank of India"),
    ("INDUSIND", "IndusInd Bank"),
    ("FEDERAL", "Federal Bank"),
    ("BANDHAN", "Bandhan Bank"),
    ("CANARA", "Canara Bank"),
    ("YES BANK", "YES Bank"),
    ("KOTAK", "Kotak Mahindra Bank"),
    ("ICICI", "ICICI"),
    ("HDFC", "HDFC"),
    ("IDFC", "IDFC FIRST"),
    ("IDBI", "IDBI"),
    ("AXIS", "AXIS"),
    ("RBL", "RBL"),
    ("UCO", "UCO"),
    ("SBI", "SBI"),
]


def _guess_bank(text):
    up = text.upper()
    for needle, display in BANK_NAMES:
        if needle in up:
            return display
    return ""


def _guess_account(text):
    # A recognised header glues the label to the digits, so there is no word
    # boundary in front of the account number.
    m = re.search(r"(?<!\d)(\d{9,18})(?!\d)", text)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Tally bank book
# ---------------------------------------------------------------------------

def parse_bank_book(grid, filename=None):
    """Read a Tally bank book / ledger grid into book movements."""
    header_idx, colmap = find_header(grid)
    if header_idx is None:
        # Tally ledgers use Particulars / Vch No. / Debit / Credit.
        for i, row in enumerate(grid[:30]):
            joined = " ".join(str(c) for c in row).lower()
            if "particular" in joined and ("debit" in joined or "credit" in joined):
                header_idx = i
                colmap, _ = _map_columns(row)
                break
    if header_idx is None:
        raise ValueError(
            "Could not find the columns of that bank book. Export the bank ledger "
            "from Tally with Date, Particulars, Vch Type, Vch No., Debit and Credit."
        )

    vch_no_idx = vch_type_idx = None
    for i, cell in enumerate(grid[header_idx]):
        label = str(cell or "").strip().lower()
        if re.search(r"vch\.?\s*no|voucher\s*no", label):
            vch_no_idx = i
        elif re.search(r"vch\.?\s*type|voucher\s*type", label):
            vch_type_idx = i

    def cell(row, field, default=""):
        idx = colmap.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx] if row[idx] is not None else default

    opening = None
    entries = []
    for r in range(header_idx + 1, len(grid)):
        row = grid[r]
        joined = " ".join(str(c) for c in row).strip()
        if not joined:
            continue
        if _is_opening_row(row, joined):
            nums = [to_float(c) for c in row if to_float(c, None) is not None]
            nums = [n for n in nums if n]
            if nums:
                opening = r2(nums[0])
            continue
        if re.match(r"^\s*(closing\s*balance|total|grand\s*total|by |to )", joined, re.I):
            continue
        if re.match(r"^\s*amount\s*$", joined, re.I):
            continue

        date = to_date(cell(row, "date"))
        if date is None:
            continue
        party = str(cell(row, "narration")).strip()
        debit = to_float(cell(row, "debit"))
        credit = to_float(cell(row, "credit"))
        if not debit and not credit:
            continue

        vch_no = str(row[vch_no_idx]).strip() if vch_no_idx is not None and \
            vch_no_idx < len(row) and row[vch_no_idx] is not None else ""
        vch_type = str(row[vch_type_idx]).strip() if vch_type_idx is not None and \
            vch_type_idx < len(row) and row[vch_type_idx] is not None else ""
        ref = str(cell(row, "ref")).strip()

        entries.append({
            "id": len(entries),
            "date": date,
            "party": party,
            "party_core": party_core(party),
            "vch_no": vch_no,
            "vch_type": vch_type,
            "ref": ref,
            "debit": r2(debit),
            "credit": r2(credit),
            "net": r2(debit - credit),
            "month_key": month_key(date),
            "fp": fingerprint("BB", iso(date), clean_inv(vch_no or ref),
                              round(debit - credit, 2), party_core(party)[:18]),
            "source_row": r + 1,
            "flags": [],
        })

    if not entries:
        raise ValueError("No voucher rows could be read from that bank book.")

    return {
        "entries": entries,
        "meta": {
            "count": len(entries),
            "opening_balance": opening,
            "closing_balance": r2((opening or 0.0) + sum(e["net"] for e in entries))
            if opening is not None else None,
            "total_receipts": r2(sum(e["debit"] for e in entries)),
            "total_payments": r2(sum(e["credit"] for e in entries)),
            "first_date": iso(min(e["date"] for e in entries)),
            "last_date": iso(max(e["date"] for e in entries)),
            "source_file": filename or "",
            "header_row": header_idx + 1,
        },
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

DEFAULTS = {
    "amount_tolerance": 1.0,
    "date_window_days": 7,
    "wide_date_window_days": 45,
    "name_threshold": 62,      # rapidfuzz score, 0-100
    "probable_threshold": 0.70,
    "batch_max_parts": 4,
    "batch_candidate_cap": 14,
}


def _name_score(a, b):
    """0-100 similarity between a narration counterparty and a ledger name."""
    if not a or not b:
        return 0
    return max(
        fuzz.token_set_ratio(a, b, processor=fuzz_utils.default_process),
        fuzz.partial_ratio(a, b, processor=fuzz_utils.default_process),
    )


def _refs_agree(bank_txn, book_entry):
    """True when a reference number appears on both sides."""
    bank_refs = {clean_inv(x) for x in ([bank_txn["ref"]] + bank_txn["refs"]) if x}
    book_refs = {clean_inv(x) for x in (book_entry["ref"], book_entry["vch_no"]) if x}
    bank_refs = {x for x in bank_refs if len(x) >= 4}
    book_refs = {x for x in book_refs if len(x) >= 4}
    if not bank_refs or not book_refs:
        return False
    if bank_refs & book_refs:
        return True
    # A cheque number written as 445128 in the book and CHQ 445128 in the bank.
    for a in bank_refs:
        for b in book_refs:
            if len(a) >= 5 and len(b) >= 5 and (a.endswith(b) or b.endswith(a)):
                return True
    return False


def reconcile_bank(bank_txns, book_entries, settings=None, today=None,
                   bank_meta=None, book_meta=None):
    """Match a bank statement against the bank book and build the BRS."""
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    # Aged as at the statement's last day, as party statements are: a cheque
    # written a month before a March statement is not stale because the
    # reconciliation happens to be done in September.
    today = today or to_date((bank_meta or {}).get("last_date")) or datetime.now()
    tol = cfg["amount_tolerance"]

    used_bank, used_book = set(), set()
    pairs = []

    by_amount = {}
    for e in book_entries:
        by_amount.setdefault(round(abs(e["net"]), 2), []).append(e)

    def candidates(t):
        return by_amount.get(round(abs(t["net"]), 2), [])

    # --- pass 1: reference number and amount both agree -------------------
    for t in bank_txns:
        for e in candidates(t):
            if e["id"] in used_book or t["id"] in used_bank:
                continue
            if e["net"] * t["net"] < 0:
                continue                             # a payment cannot match a receipt
            if abs(abs(e["net"]) - abs(t["net"])) > tol:
                continue
            if _refs_agree(t, e):
                pairs.append(_pair(t, e, "Matched", 1.0, "reference + amount"))
                used_bank.add(t["id"])
                used_book.add(e["id"])
                break

    # --- pass 2: amount and date agree, name supports it ------------------
    win = cfg["date_window_days"]
    for t in bank_txns:
        if t["id"] in used_bank:
            continue
        best, best_score = None, -1
        for e in candidates(t):
            if e["id"] in used_book or e["net"] * t["net"] < 0:
                continue
            if abs(abs(e["net"]) - abs(t["net"])) > tol:
                continue
            gap = abs((t["date"] - e["date"]).days)
            if gap > win:
                continue
            name = _name_score(t["counterparty"], e["party"])
            score = (100 - gap * 4) + name
            if score > best_score:
                best, best_score = e, score
        if best is not None:
            gap = abs((t["date"] - best["date"]).days)
            name = _name_score(t["counterparty"], best["party"])
            flags = ["date_gap"] if gap else []
            if name < cfg["name_threshold"]:
                flags.append("name_differs")
            pairs.append(_pair(t, best, "Matched", round(min(1.0, 0.7 + name / 400), 3),
                               f"amount + date ({gap}d apart)", flags))
            used_bank.add(t["id"])
            used_book.add(best["id"])

    # --- pass 3: same amount, far apart in time -> timing difference ------
    wide = cfg["wide_date_window_days"]
    for t in bank_txns:
        if t["id"] in used_bank:
            continue
        best, best_gap = None, 10 ** 6
        for e in candidates(t):
            if e["id"] in used_book or e["net"] * t["net"] < 0:
                continue
            if abs(abs(e["net"]) - abs(t["net"])) > tol:
                continue
            gap = abs((t["date"] - e["date"]).days)
            if gap > wide or gap <= win:
                continue
            if _name_score(t["counterparty"], e["party"]) < 45 and not _refs_agree(t, e):
                continue
            if gap < best_gap:
                best, best_gap = e, gap
        if best is not None:
            pairs.append(_pair(t, best, "Timing difference", 0.8,
                               f"same amount, {best_gap} days apart",
                               ["timing"]))
            used_bank.add(t["id"])
            used_book.add(best["id"])

    # --- pass 4: amount differs slightly on an otherwise clear match ------
    for t in bank_txns:
        if t["id"] in used_bank:
            continue
        best, best_score = None, -1
        for e in book_entries:
            if e["id"] in used_book or e["net"] * t["net"] < 0:
                continue
            diff = abs(abs(e["net"]) - abs(t["net"]))
            if diff <= tol or diff > max(500.0, abs(t["net"]) * 0.05):
                continue
            gap = abs((t["date"] - e["date"]).days)
            if gap > win:
                continue
            name = _name_score(t["counterparty"], e["party"])
            if name < cfg["name_threshold"] and not _refs_agree(t, e):
                continue
            score = name - gap * 3 - diff / 10
            if score > best_score:
                best, best_score = e, score
        if best is not None:
            pairs.append(_pair(t, best, "Amount differs", 0.75,
                               "same party and date, different amount",
                               ["amount_diff"]))
            used_bank.add(t["id"])
            used_book.add(best["id"])

    # --- pass 5: one bank line against several book entries --------------
    open_book = [e for e in book_entries if e["id"] not in used_book]
    for t in bank_txns:
        if t["id"] in used_bank:
            continue
        target = abs(t["net"])
        if target <= 0:
            continue
        pool = [e for e in open_book
                if e["id"] not in used_book
                and e["net"] * t["net"] > 0
                and abs(e["net"]) < target
                and abs((t["date"] - e["date"]).days) <= win]
        if len(pool) < 2:
            continue
        pool.sort(key=lambda e: -abs(e["net"]))
        pool = pool[: cfg["batch_candidate_cap"]]
        combo = _subset_sum(pool, target, tol, cfg["batch_max_parts"])
        if not combo:
            continue
        lead = combo[0]
        rec = _pair(t, lead, "Batched", 0.85,
                    f"one bank line covering {len(combo)} book entries", ["batch"])
        rec["book_amount"] = r2(sum(abs(e["net"]) for e in combo))
        # The pair carries the whole group's movement, not just the lead
        # entry's, or the BRS counts the rest of the batch as a difference.
        rec["book_net"] = r2(sum(e["net"] for e in combo))
        rec["difference"] = r2(abs(t["net"]) - rec["book_amount"])
        rec["book_party"] = ", ".join(dict.fromkeys(e["party"] for e in combo))
        rec["book_vch_no"] = " + ".join(e["vch_no"] or "(no number)" for e in combo)
        rec["parts"] = [{"vch_no": e["vch_no"], "party": e["party"],
                         "date": e["date"], "amount": r2(abs(e["net"])),
                         "vch_type": e["vch_type"], "fp": e["fp"]} for e in combo]
        pairs.append(rec)
        used_bank.add(t["id"])
        for e in combo:
            used_book.add(e["id"])

    # --- pass 6: one-sided ------------------------------------------------
    bank_only = [t for t in bank_txns if t["id"] not in used_bank]
    book_only = [e for e in book_entries if e["id"] not in used_book]

    for t in bank_only:
        label, tone = _classify_bank_only(t)
        rec = _pair(t, None, label, None, "not in the bank book")
        rec["category"] = label
        rec["tone"] = tone
        pairs.append(rec)

    for e in book_only:
        label = ("Cheque issued, not presented" if e["net"] < 0
                 else "Deposit made, not credited")
        rec = _pair(None, e, label, None, "not on the bank statement")
        rec["category"] = label
        age = (today - e["date"]).days
        rec["age_days"] = age
        if e["net"] < 0 and age > STALE_CHEQUE_DAYS:
            rec["flags"] = sorted(set(rec["flags"] + ["stale_cheque"]))
        pairs.append(rec)

    return _assemble_brs(pairs, bank_txns, book_entries, bank_only, book_only,
                         cfg, bank_meta or {}, book_meta or {}, today)


def _subset_sum(pool, target, tol, max_parts):
    """Smallest group of entries summing to the target, up to max_parts."""
    from itertools import combinations
    for size in range(2, max_parts + 1):
        for combo in combinations(pool, size):
            if abs(sum(abs(e["net"]) for e in combo) - target) <= tol:
                return list(combo)
    return None


def _classify_bank_only(t):
    """Name a bank-only movement the way a reconciliation would."""
    ch = t["channel"]
    if ch == "CHARGES":
        return "Bank charges not in books", "warn"
    if ch == "TAX":
        return "GST on bank charges not in books", "warn"
    if ch == "INTEREST":
        return ("Interest credited, not in books" if t["credit"]
                else "Interest debited, not in books"), "info"
    if ch == "LOAN":
        return "Loan or EMI debit not in books", "risk"
    if ch in ("ACH",):
        return "Standing instruction not in books", "risk"
    if ch == "ATM":
        return "ATM withdrawal not in books", "risk"
    if ch == "REVERSAL":
        return "Reversal or return not in books", "warn"
    if t["credit"]:
        return "Credit in bank, not in books", "risk"
    return "Debit in bank, not in books", "risk"


def _pair(t, e, status, score, basis, flags=None):
    flags = list(flags or [])
    if t:
        flags += [f for f in t["flags"] if f not in flags]
    if e:
        flags += [f for f in e["flags"] if f not in flags]
    bank_amt = r2(abs(t["net"])) if t else None
    book_amt = r2(abs(e["net"])) if e else None
    return {
        "status": status,
        "category": status,
        "fp": (t["fp"] if t else e["fp"]),
        "bank_fp": t["fp"] if t else None,
        "book_fp": e["fp"] if e else None,
        "date_bank": t["date"] if t else None,
        "date_book": e["date"] if e else None,
        "narration": t["narration"] if t else None,
        "channel": t["channel"] if t else None,
        "counterparty": t["counterparty"] if t else None,
        "bank_ref": t["ref"] if t else None,
        "book_party": e["party"] if e else None,
        "book_vch_no": e["vch_no"] if e else None,
        "book_vch_type": e["vch_type"] if e else None,
        "book_ref": e["ref"] if e else None,
        "direction": ("Payment" if ((t and t["net"] < 0) or (e and e["net"] < 0))
                      else "Receipt"),
        "bank_amount": bank_amt,
        "book_amount": book_amt,
        "difference": r2(bank_amt - book_amt) if (bank_amt is not None
                                                 and book_amt is not None) else None,
        "bank_net": r2(t["net"]) if t else 0.0,
        "book_net": r2(e["net"]) if e else 0.0,
        "balance": t["balance"] if t else None,
        "month_key": (t["month_key"] if t else e["month_key"]),
        "match_score": score,
        "match_basis": basis,
        "date_gap_days": (abs((t["date"] - e["date"]).days) if (t and e) else None),
        "age_days": None,
        "flags": sorted(set(flags)),
        "parts": None,
        "decision": None,
        "source_row_bank": t["source_row"] if t else None,
        "source_row_book": e["source_row"] if e else None,
    }


# ---------------------------------------------------------------------------
# The bank reconciliation statement
# ---------------------------------------------------------------------------

MATCHED_STATUSES = {"Matched", "Timing difference", "Amount differs", "Batched"}

# `side` names the set of books the group's items are missing from, which is
# what determines the sign: a movement only the bank has shifts the bank
# balance by its own signed amount, one only the book has shifts the book's.
# The Add/Less wording is then read off the computed effect. A reversal can be
# a debit or a credit, so assuming a sign per group would eventually be wrong.
BRS_GROUPS = [
    ("unpresented",   "Cheques issued but not yet presented", "book",
     ("Cheque issued, not presented",)),
    ("uncredited",    "Deposits made but not yet credited", "book",
     ("Deposit made, not credited",)),
    ("bank_charges",  "Bank charges debited, not in books", "bank",
     ("Bank charges not in books", "GST on bank charges not in books")),
    ("bank_interest", "Interest credited, not in books", "bank",
     ("Interest credited, not in books",)),
    ("bank_debits",   "Other bank debits not in books", "bank",
     ("Loan or EMI debit not in books", "Standing instruction not in books",
      "ATM withdrawal not in books", "Debit in bank, not in books",
      "Interest debited, not in books")),
    ("bank_credits",  "Other bank credits not in books", "bank",
     ("Credit in bank, not in books", "Reversal or return not in books")),
]


def _assemble_brs(pairs, bank_txns, book_entries, bank_only, book_only, cfg,
                  bank_meta, book_meta, today):
    bank_open = bank_meta.get("opening_balance")
    book_open = book_meta.get("opening_balance")
    bank_close = bank_meta.get("closing_balance")
    book_close = book_meta.get("closing_balance")

    if bank_close is None:
        bank_close = r2((bank_open or 0.0) + sum(t["net"] for t in bank_txns))
    if book_close is None:
        book_close = r2((book_open or 0.0) + sum(e["net"] for e in book_entries))

    # The identity the statement is built from.
    opening_gap = r2((bank_open or 0.0) - (book_open or 0.0))
    matched = [p for p in pairs if p["status"] in MATCHED_STATUSES]
    # Both nets are already expressed as the effect on the balance - the bank's
    # as credit minus debit, the book's as debit minus credit - so they compare
    # directly. Only a genuine difference on a matched pair survives here.
    matched_gap = r2(sum(p["bank_net"] - p["book_net"] for p in matched))

    groups = []
    for key, label, side, statuses in BRS_GROUPS:
        rows = [p for p in pairs if p["category"] in statuses]
        if not rows:
            continue
        effect = r2(sum(r["bank_net"] for r in rows) if side == "bank"
                    else -sum(r["book_net"] for r in rows))
        groups.append({
            "key": key, "label": label, "side": side,
            "count": len(rows),
            "amount": r2(sum(abs(r["bank_amount"] if r["bank_amount"] is not None
                                 else r["book_amount"]) for r in rows)),
            "effect": effect,
            "rows": [_brs_row(r) for r in rows],
        })

    lines = [{"key": "book", "label": "Balance as per the bank book",
              "amount": book_close, "kind": "opening"}]
    for g in groups:
        lines.append({
            "key": g["key"],
            "label": ("Add: " if g["effect"] >= 0 else "Less: ") + g["label"],
            "amount": g["effect"], "count": g["count"], "kind": "adjust",
        })
    if abs(matched_gap) > 0.5:
        lines.append({"key": "matched_diff",
                      "label": "Adjust: differences on matched items",
                      "amount": matched_gap, "kind": "adjust"})
    if abs(opening_gap) > 0.5:
        lines.append({"key": "opening_diff",
                      "label": "Adjust: difference in opening balances",
                      "amount": opening_gap, "kind": "adjust"})

    derived = r2(book_close + sum(l["amount"] for l in lines[1:]))
    residual = r2(bank_close - derived)
    if abs(residual) > 0.5:
        lines.append({"key": "residual", "label": "Unexplained difference",
                      "amount": residual, "kind": "adjust", "tone": "risk"})
    lines.append({"key": "bank", "label": "Balance as per the bank statement",
                  "amount": bank_close, "kind": "closing"})

    counts = {
        "bank_txns": len(bank_txns),
        "book_entries": len(book_entries),
        "matched": sum(1 for p in pairs if p["status"] == "Matched"),
        "timing": sum(1 for p in pairs if p["status"] == "Timing difference"),
        "amount_diff": sum(1 for p in pairs if p["status"] == "Amount differs"),
        "batched": sum(1 for p in pairs if p["status"] == "Batched"),
        "bank_only": len(bank_only),
        "book_only": len(book_only),
        "open": len(bank_only) + len(book_only)
                + sum(1 for p in pairs if p["status"] in
                      ("Timing difference", "Amount differs")),
    }
    matched_total = counts["matched"] + counts["timing"] + counts["amount_diff"] \
        + counts["batched"]
    match_rate = round(matched_total / len(bank_txns) * 100, 1) if bank_txns else 0.0

    charges = r2(sum(abs(p["bank_amount"] or 0) for p in pairs
                     if p["category"] == "Bank charges not in books"))
    charge_gst = r2(sum(abs(p["bank_amount"] or 0) for p in pairs
                        if p["category"] == "GST on bank charges not in books"))

    order = {"Amount differs": 0, "Timing difference": 1}
    pairs.sort(key=lambda p: (
        0 if p["status"] not in MATCHED_STATUSES else 2,
        order.get(p["status"], 3),
        -(abs(p["bank_amount"] or p["book_amount"] or 0)),
    ))

    return {
        "kind": "bank",
        "config": cfg,
        "bank_meta": bank_meta,
        "book_meta": book_meta,
        "balances": {
            "bank_opening": bank_open, "book_opening": book_open,
            "bank_closing": bank_close, "book_closing": book_close,
            "difference": r2(book_close - bank_close),
            "ties": abs(residual) <= 0.5,
            "residual": residual,
        },
        "brs": {"lines": lines, "groups": groups, "ties": abs(residual) <= 0.5,
                "residual": residual},
        "counts": counts,
        "rates": {"match_rate": match_rate},
        "all_records": pairs,
        "charges": {"bank_charges": charges, "gst_on_charges": charge_gst,
                    "total": r2(charges + charge_gst)},
        "channels": bank_meta.get("channels") or _channel_summary(bank_txns),
        "findings": _bank_findings(pairs, bank_txns, book_entries, today),
        "monthly": _bank_monthly(bank_txns, book_entries),
        "generated_at": iso(datetime.now()),
        "as_at": iso(today),
    }


def _brs_row(r):
    return {
        "fp": r["fp"],
        "date": r["date_bank"] or r["date_book"],
        "particulars": r["narration"] or r["book_party"],
        "ref": r["bank_ref"] or r["book_vch_no"],
        "amount": r["bank_amount"] if r["bank_amount"] is not None else r["book_amount"],
        "direction": r["direction"],
        "channel": r["channel"],
        "age_days": r.get("age_days"),
        "flags": r["flags"],
    }


def _bank_findings(pairs, bank_txns, book_entries, today):
    """Things worth a person's attention, each with the money attached."""
    out = []

    def add(key, title, tone, detail, rows, amount=None):
        if not rows:
            return
        out.append({
            "key": key, "title": title, "tone": tone, "detail": detail,
            "count": len(rows),
            "amount": amount if amount is not None
            else r2(sum(abs(x.get("bank_amount") or x.get("book_amount") or 0)
                        for x in rows)),
        })

    stale = [p for p in pairs if "stale_cheque" in p["flags"]]
    add("stale", "Cheques stale-dated", "risk",
        "Issued more than three months ago and still not presented. A cheque older "
        "than three months is no longer a valid instrument - reverse it in the books "
        "and issue a fresh one.", stale)

    add("amount_diff", "Amount differs between bank and books", "risk",
        "Same payment, different figure. Usually a bank charge added to the transfer, "
        "or a keying error. Correct the book value.",
        [p for p in pairs if p["status"] == "Amount differs"],
        amount=r2(sum(abs(p["difference"] or 0) for p in pairs
                      if p["status"] == "Amount differs")))

    add("charges", "Bank charges never booked", "warn",
        "These reduce the bank balance but are not in the books. Book them as an "
        "expense; the GST on them is creditable and will be sitting in your GSTR-2B.",
        [p for p in pairs if p["category"] in
         ("Bank charges not in books", "GST on bank charges not in books")])

    add("direct_debits", "Direct debits never booked", "risk",
        "Standing instructions, EMIs and ATM withdrawals the bank took but the books "
        "do not show. Each one is a real payment that needs a voucher.",
        [p for p in pairs if p["category"] in
         ("Loan or EMI debit not in books", "Standing instruction not in books",
          "ATM withdrawal not in books")])

    add("unexplained_credits", "Credits in the bank with no entry", "risk",
        "Money arrived and nothing records where from. Identify each one before the "
        "period closes.",
        [p for p in pairs if p["category"] == "Credit in bank, not in books"])

    # Duplicate payments: same counterparty and amount inside a week.
    dupes = []
    seen = {}
    for e in book_entries:
        if e["net"] >= 0:
            continue
        key = (party_core(e["party"]), round(abs(e["net"]), 2))
        prev = seen.get(key)
        if prev and abs((e["date"] - prev["date"]).days) <= 7:
            dupes.append({"book_amount": abs(e["net"]), "bank_amount": None,
                          "party": e["party"], "first": iso(prev["date"]),
                          "second": iso(e["date"]),
                          "vch": f"{prev['vch_no']} / {e['vch_no']}"})
        seen[key] = e
    if dupes:
        out.append({
            "key": "dup_payments", "title": "Possible duplicate payments",
            "tone": "risk", "count": len(dupes),
            "amount": r2(sum(d["book_amount"] for d in dupes)),
            "detail": "The same party paid the same amount twice inside a week. "
                      "Check before it becomes an advance nobody tracks.",
            "rows": dupes[:20],
        })

    big_cash = [t for t in bank_txns
                if t["channel"] == "CASH" and max(t["debit"], t["credit"]) >= CASH_ALERT]
    if big_cash:
        out.append({
            "key": "cash", "title": "Large cash movements", "tone": "warn",
            "count": len(big_cash),
            "amount": r2(sum(max(t["debit"], t["credit"]) for t in big_cash)),
            "detail": f"Cash transactions of {int(CASH_ALERT):,} or more. Section 269ST "
                      f"restricts cash receipts at this size; confirm each one is on the "
                      f"right side of it.",
        })

    breaks = [t for t in bank_txns if "balance_break" in t["flags"]]
    if breaks:
        out.append({
            "key": "continuity", "title": "Running balance does not tie", "tone": "risk",
            "count": len(breaks), "amount": 0.0,
            "detail": "The statement's own balance column does not follow from the "
                      "debits and credits. Either a column was misread or the file "
                      "has been edited. Re-download it from the bank.",
        })

    unknown = [t for t in bank_txns if t["channel"] == "UNKNOWN"]
    if len(unknown) > max(3, len(bank_txns) * 0.15):
        out.append({
            "key": "unclassified", "title": "Narrations that could not be read",
            "tone": "info", "count": len(unknown),
            "amount": r2(sum(abs(t["net"]) for t in unknown)),
            "detail": "The channel could not be identified for these lines. Matching "
                      "still works on amount and date, but the categories will be thin.",
        })

    tone_rank = {"risk": 0, "warn": 1, "info": 2}
    out.sort(key=lambda f: (tone_rank.get(f["tone"], 3), -f["count"]))
    del today
    return out


def _bank_monthly(bank_txns, book_entries):
    months = {}
    for t in bank_txns:
        m = months.setdefault(t["month_key"], {"month": t["month_key"],
                                               "bank_in": 0.0, "bank_out": 0.0,
                                               "book_in": 0.0, "book_out": 0.0,
                                               "bank_n": 0, "book_n": 0})
        m["bank_in"] += t["credit"]
        m["bank_out"] += t["debit"]
        m["bank_n"] += 1
    for e in book_entries:
        m = months.setdefault(e["month_key"], {"month": e["month_key"],
                                               "bank_in": 0.0, "bank_out": 0.0,
                                               "book_in": 0.0, "book_out": 0.0,
                                               "bank_n": 0, "book_n": 0})
        m["book_in"] += e["debit"]
        m["book_out"] += e["credit"]
        m["book_n"] += 1
    out = []
    for key in sorted(k for k in months if k):
        m = months[key]
        for f in ("bank_in", "bank_out", "book_in", "book_out"):
            m[f] = r2(m[f])
        m["net_bank"] = r2(m["bank_in"] - m["bank_out"])
        m["net_book"] = r2(m["book_in"] - m["book_out"])
        out.append(m)
    return out
