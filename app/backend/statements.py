"""
Party statement reconciliation.

A supplier or customer sends a statement of account; you hold your own ledger
for them; the two closing balances disagree. This module reads their statement
(spreadsheet, PDF or scan), reads your Tally ledger for the same party, matches
document by document, and produces the bridge that explains the gap.

The sign conventions are mirrored and getting them wrong is the classic error
in this exercise. A supplier's statement shows your purchases as *their* debit
and your payments as *their* credit. Your ledger for that supplier shows the
same purchases as *your* credit and the same payments as *your* debit. Both
sides are therefore normalised here to one number - "amount owed to the party" -
before anything is compared, and the bridge is derived from an identity so it
cannot quietly fail to tie.
"""

import re
from datetime import datetime

from rapidfuzz import fuzz
from rapidfuzz import utils as fuzz_utils

from app.backend.bank import find_header, split_leading_date
from app.backend.common import (
    AGING_BUCKETS, clean_inv, fingerprint, inr, iso, month_key, parse_period,
    party_core, r2, tails_match, to_date, to_float,
)

# ---------------------------------------------------------------------------
# What kind of entry is this?
# ---------------------------------------------------------------------------

ENTRY_KINDS = [
    ("credit_note", (r"credit\s*note", r"\bcr\.?\s*note\b", r"\bcn\b", r"rate\s*diff",
                     r"\bdiscount\b", r"\brebate\b", r"\ball[ou]wance\b")),
    ("debit_note",  (r"debit\s*note", r"\bdr\.?\s*note\b", r"\bdn\b")),
    ("payment",     (r"\breceipt\b", r"\bpayment\b", r"\bpaid\b", r"\bneft\b",
                     r"\brtgs\b", r"\bimps\b", r"\bupi\b", r"\bchq\b", r"\bcheque\b",
                     r"\bon\s*account\b", r"\badvance\b", r"\bbank\b", r"\bcash\b")),
    ("invoice",     (r"\binvoice\b", r"\bsales?\b", r"\bpurchase\b", r"\bbill\b",
                     r"\bsupply\b", r"\bgoods\b", r"\btax\s*inv\b")),
    ("tds",         (r"\btds\b", r"\btcs\b", r"withhold")),
    ("interest",    (r"\binterest\b", r"\blate\s*fee\b", r"\bdelay\b")),
    ("opening",     (r"opening\s*balance", r"balance\s*b/?f", r"brought\s*forward")),
]

_KIND_COMPILED = [(k, tuple(re.compile(p, re.I) for p in pats)) for k, pats in ENTRY_KINDS]

STATEMENT_FIELDS = [
    # A Dr/Cr column says which way a figure went. It is tried first, or the
    # "dr" in its heading takes it for the Debit column and every amount beside
    # it reads as a debit.
    ("drcr",        (r"^(dr\s*/?\s*cr|cr\s*/?\s*dr|type|indicator|txn\s*type)$",)),
    ("date",        (r"^date$", r"^dt$", r"\bdate\b", r"^txn\s*date$")),
    ("particulars", (r"particular", r"narration", r"description", r"^desc\b",
                     r"^details$", r"remark", r"^nature")),
    ("vch_type",    (r"^(vch|voucher)\s*type",)),
    ("ref",         (r"(invoice|voucher|document|bill|doc|ref)\s*(no|number|#)",
                     r"^vch\.?\s*no", r"^inv\.?\s*no", r"^ref$", r"^no$")),
    ("debit",       (r"^debit", r"\bdr\b", r"^dr\.?$", r"debit\s*amount")),
    ("credit",      (r"^credit", r"\bcr\b", r"^cr\.?$", r"credit\s*amount")),
    ("balance",     (r"balance", r"^bal$", r"outstanding", r"running")),
    # A columnar Tally ledger has no Debit/Credit, only the voucher's gross total.
    ("amount",      (r"^amount$", r"^amt$", r"^gross\s*total$")),
    ("days",        (r"^days?$",)),
    ("product",     (r"^products?$",)),
    ("due_date",    (r"due\s*date",)),
]

_STMT_COMPILED = [(f, tuple(re.compile(p) for p in pats)) for f, pats in STATEMENT_FIELDS]

_SKIP = re.compile(
    r"^(total|sub\s*total|grand\s*total|closing\s*balance|"
    r"(carried|brought)\s*(over|forward|down)|"
    r"\*+|page\s*\d+|continued|b/?f|c/?f)\b", re.I)

# Which side of a party's ledger each Tally voucher type lands on, for the
# columnar layout that prints one gross amount and no Debit/Credit columns.
_VCH_SIDE = {"purchase": "credit", "receipt": "credit", "credit note": "credit",
             "sales": "debit", "sale": "debit", "payment": "debit",
             "debit note": "debit"}

# Tally prints To/By - or Dr/Cr - in the Particulars column and the ledger name
# in the unlabelled column beside it. On a closing-balance line the word also
# says what kind of balance it is.
_PREFIX_NATURE = {"to": "credit", "cr": "credit", "by": "debit", "dr": "debit"}

_NUMERIC = re.compile(r"^\(?-?(?:rs\.?|₹)?\s*[\d,]+(?:\.\d+)?\)?\s*(?:dr|cr)?\.?$", re.I)

# The first line of an interest statement: the balance carried in, with no
# narration or just a mark.
_BROUGHT_FORWARD = re.compile(r"^(#|b\s*/?\s*f|by\s*balance|to\s*balance|balance|)$", re.I)

TWO_SIDED_HEADER = ["Date", "Particulars", "Debit", "Credit", "Days", "Product"]
_TWO_SIDED_COLS = {"date": 0, "particulars": 1, "debit": 2, "credit": 3,
                   "days": 4, "product": 5}


def classify_entry(particulars, ref=""):
    """Name the kind of ledger entry from its narration."""
    text = f"{particulars} {ref}".strip()
    for kind, patterns in _KIND_COMPILED:
        if any(p.search(text) for p in patterns):
            return kind
    return "unknown"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _label(cell):
    return re.sub(r"\s+", " ", str(cell or "")).strip().lower().replace(".", " ").strip()


def _field_of(label):
    for field, patterns in _STMT_COMPILED:
        if any(p.search(label) for p in patterns):
            return field
    return None


def _num(cell):
    """A number, but only when the cell is one.

    `to_float` fishes digits out of anything, which turns the label "TDS @ 10%"
    into ten. Figures read off a statement's summary lines have to come from
    cells that are actually numbers.
    """
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    s = str(cell or "").strip()
    return to_float(s, None) if s and _NUMERIC.match(s) else None


def _map_columns(header_row):
    colmap, flags = {}, []
    for i, cell in enumerate(header_row):
        label = _label(cell)
        if not label:
            continue
        if _field_of(label) == "drcr":
            flags.append(i)             # a second one is never Debit or Credit
            continue
        for field, patterns in _STMT_COMPILED:
            if field in colmap:
                continue
            if any(p.search(label) for p in patterns):
                colmap[field] = i
                break
    # A flag right of the balance says what kind of balance it is; the other
    # says which way the amount went.
    for i in flags:
        colmap.setdefault("balance_drcr" if colmap.get("balance") == i - 1 else "drcr", i)
    return colmap


def _flag_side(cell):
    """Which way a Dr/Cr (or Type) cell says a figure went, if it says."""
    text = str(cell or "").strip().lower().rstrip(".")
    if re.match(r"(dr?|debit)\b", text):
        return "debit"
    if re.match(r"(cr?|credit)\b", text):
        return "credit"
    return _VCH_SIDE.get(text)


def _find_statement_header(grid, max_scan=30):
    best, best_score, best_map = None, 0, {}
    for i, row in enumerate(grid[:max_scan]):
        colmap = _map_columns(row)
        score = 0
        if "date" in colmap:
            score += 2
        if "particulars" in colmap:
            score += 2
        if "debit" in colmap and "credit" in colmap:
            score += 3
        elif "amount" in colmap:
            score += 2
        if "ref" in colmap:
            score += 1
        if "balance" in colmap:
            score += 1
        if score > best_score:
            best, best_score, best_map = i, score, colmap
    return (best, best_map) if best_score >= 4 else (None, {})


def parse_statement(grid, side="theirs", filename=None, party_name=None):
    """
    Read a statement or ledger grid into entries.

    `side` is "theirs" for a statement received from the party, or "ours" for a
    Tally ledger. It only affects how debit and credit are normalised into the
    single "owed to the party" figure both sides are compared on.
    """
    header_idx, colmap = _find_statement_header(grid)
    if header_idx is None:
        header_idx, colmap2 = find_header(grid)
        if header_idx is not None:
            colmap = _map_columns(grid[header_idx]) or colmap2
    if header_idx is None:
        raise ValueError(
            "Could not find the columns of that statement. It needs a header row "
            "with a date, a particulars or description column, and debit and "
            "credit columns - or one amount column with a Dr/Cr column."
        )

    head_rows = grid[:header_idx]
    head_text = " ".join(str(c).strip() for r in head_rows for c in r
                         if str(c if c is not None else "").strip())
    period_from, period_to = parse_period(head_text)
    ledger_name, company = _ledger_heading(head_rows)

    two_sided = _unfold_two_sided(grid, header_idx)
    if two_sided:
        grid, colmap = two_sided["grid"], dict(_TWO_SIDED_COLS)
    else:
        _read_name_beside_prefix(grid, header_idx, colmap)

    def cell(row, field, default=""):
        idx = colmap.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx] if row[idx] is not None else default

    def balance(row):
        """The running balance, with its Dr/Cr when that has a column of its own."""
        nature = _flag_side(cell(row, "balance_drcr"))
        value = cell(row, "balance")
        return f"{value} {'Dr' if nature == 'debit' else 'Cr'}" if nature else value

    gstin = None
    m = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", head_text.upper())
    if m:
        gstin = m.group(0)
    # Who the other side is. On our own ledger that is the account's name. On
    # a ledger they sent us the account's name is *us*, and the party is the
    # business whose books it came out of - naming it the other way round puts
    # our own name at the top of their statement.
    name = party_name or ((company or ledger_name) if side == "theirs" else ledger_name) \
        or _guess_party(head_rows)

    opening, stated_close, last_row, unsigned = None, None, header_idx, 0
    entries, carried = [], None
    for r in range(header_idx + 1, len(grid)):
        row = grid[r]
        joined = " ".join(str(c) for c in row if c is not None).strip()
        if not joined:
            continue
        if re.search(r"closing\s*balance", joined, re.I):
            stated_close = _closing_owed(row, colmap, side)
            continue
        if _SKIP.match(joined) or re.match(r"^\s*amount\s*$", joined, re.I):
            continue
        if _label(cell(row, "date")) == "date":
            continue                    # the header, repeated on a later page

        date = to_date(cell(row, "date"))
        particulars = str(cell(row, "particulars")).strip()
        if date is None:
            d2, rest = split_leading_date(cell(row, "date") or particulars)
            if d2 is not None:
                # What trailed the date joins the narration rather than
                # replacing it: a "To" that drifted into the date column must
                # not throw away the "Opening Balance" beside it.
                date = d2
                particulars = f"{rest} {particulars}".strip() if rest else particulars

        # Only a cell that is a number is money. Pulling the digits out of
        # whatever is there turns the "Page 2" footer of a ledger into a
        # two-rupee credit, and the account then misses balancing by ₹2.
        debit = _num(cell(row, "debit")) or 0.0
        credit = _num(cell(row, "credit")) or 0.0
        vch_type = str(cell(row, "vch_type")).strip()
        if not debit and not credit and "amount" in colmap:
            amt = _num(cell(row, "amount")) or 0.0
            says = _flag_side(cell(row, "drcr")) or _VCH_SIDE.get(vch_type.lower())
            if says == "credit" or amt < 0:
                credit = abs(amt)
            elif says == "debit" or "vch_type" not in colmap:
                debit = abs(amt)
            elif amt and date is not None:
                unsigned += 1        # a journal with nothing to say which way it went
                continue

        ref_text = str(cell(row, "ref")).strip()
        # A totals line carries figures and nothing else. Tally prints the
        # debit total in the date column, and where that number falls inside
        # the Excel serial range it reads as a plausible date, so the absence
        # of any narration is what identifies it rather than the date.
        if not particulars and not ref_text and not vch_type and (debit or credit):
            if date is None or _num(cell(row, "date")) is not None:
                continue

        kind = classify_entry(f"{particulars} {vch_type}".strip(), ref_text)
        if (kind == "unknown" and two_sided and date is not None
                and date == period_from and _BROUGHT_FORWARD.match(particulars)):
            kind = "opening"
        if kind == "opening":
            if debit or credit:
                raw = debit - credit
                opening = r2(raw if side == "theirs" else -raw)
            else:
                opening = _owed_from_balance(balance(row), side)
            continue
        if not debit and not credit:
            continue
        if date is None:
            # A ledger leaves the date blank when several vouchers share a
            # day, and a totals line has no date at all. Two things mark a
            # totals line: it carries both a debit and a credit, which a real
            # entry never does, and Tally prints the debit total in the date
            # column itself. Dropping every undated row instead would lose a
            # genuine receipt that only omitted its repeated date.
            if carried is None or (debit and credit):
                continue
            if _num(cell(row, "date")) is not None:
                continue
            date = carried
        carried = date

        last_row = r
        ref = ref_text
        if not ref:
            found = re.search(r"\b([A-Z0-9][A-Z0-9/\-]{3,})\b", particulars.upper())
            ref = found.group(1) if found else ""
        entries.append(_entry(
            len(entries), side, date, particulars, ref, kind, debit, credit,
            due_date=to_date(cell(row, "due_date")),
            balance=_owed_from_balance(balance(row), side),
            vch_type=vch_type, days=_num(cell(row, "days")),
            product=_num(cell(row, "product")), source_row=r + 1))

    if not entries:
        raise ValueError("The statement header was found but no entries could be "
                         "read below it.")

    issues = []
    if unsigned:
        issues.append(f"{unsigned} voucher(s) carry an amount but no Debit/Credit "
                      "column or voucher type that says which way they went, so "
                      "they were left out. Export the ledger with Debit and Credit "
                      "columns to include them.")

    # An interest statement prints what it computed beneath the table. Those
    # figures belong in the comparison - our books carry the interest and the
    # TDS as entries - so they are turned into entries dated at the period end.
    interest = None
    if two_sided or re.search(r"\binterest\b", head_text, re.I):
        tail = two_sided["tail"] if two_sided else grid[last_row + 1:]
        interest = _interest_terms(head_text, tail)
    if interest:
        principal_close = r2((opening or 0.0) + sum(e["owed"] for e in entries))
        if interest.get("ledger_balance") is not None:
            debit_nature = interest.get("ledger_side", "debit") == "debit"
            lb = interest["ledger_balance"]
            interest["ledger_balance_owed"] = r2(lb if debit_nature == (side == "theirs")
                                                 else -lb)
        if two_sided:
            interest["check"] = _verify_interest(
                two_sided["lines"], two_sided["totals"], interest, period_to)
        owed_lb = interest.get("ledger_balance_owed")
        if owed_lb is not None and abs(owed_lb - principal_close) > 1.0:
            interest.setdefault("check", {"problems": [], "ok": True})
            interest["check"]["problems"].append(
                f"The entries add up to a balance of {inr(principal_close)}, but the "
                f"statement prints the ledger balance as {inr(owed_lb)}.")
            interest["check"]["ok"] = False
        interest["principal_balance"] = principal_close
        _add_interest_entries(entries, interest, side, period_to)

    closing = r2((opening or 0.0) + sum(e["owed"] for e in entries))
    stated = stated_close if stated_close is not None else entries[-1]["balance"]
    if interest:
        stated = None               # its ledger balance is principal only
    if side == "theirs" and stated is not None and abs(stated - closing) > 1.0:
        # Their own running balance disagrees with their own movements.
        for e in entries:
            e["flags"].append("balance_note")

    return {
        "entries": entries,
        "meta": {
            "side": side,
            "party_name": name,
            "ledger_name": ledger_name,
            "company": company,
            "gstin": gstin,
            "document": ("interest_statement" if interest
                         else "ledger" if ledger_name else "statement"),
            "two_sided": bool(two_sided),
            "count": len(entries),
            "opening_balance": opening,
            "closing_balance": closing,
            "stated_closing": r2(stated) if stated is not None else None,
            "closing_agrees": (abs(stated - closing) <= 1.0) if stated is not None else None,
            "total_debits": r2(sum(e["debit"] for e in entries)),
            "total_credits": r2(sum(e["credit"] for e in entries)),
            "first_date": iso(min(e["date"] for e in entries)),
            "last_date": iso(max(e["date"] for e in entries)),
            "period_from": iso(period_from),
            "period_to": iso(period_to),
            "interest": interest,
            "issues": issues,
            "source_file": filename or "",
            "header_row": header_idx + 1,
            "kinds": _kind_summary(entries),
        },
    }


def _entry(i, side, date, particulars, ref, kind, debit, credit, *, due_date=None,
           balance=None, vch_type="", days=None, product=None, source_row=None,
           flags=None):
    # One number for both sides: what the party is owed.
    owed = (debit - credit) if side == "theirs" else (credit - debit)
    return {
        "id": i,
        "side": side,
        "date": date,
        "due_date": due_date,
        "particulars": particulars,
        "ref": ref,
        "kind": kind,
        "vch_type": vch_type,
        "debit": r2(debit),
        "credit": r2(credit),
        "owed": r2(owed),
        "balance": balance,
        "days": days,
        "product": product,
        "month_key": month_key(date),
        "fp": fingerprint("ST", side, iso(date), clean_inv(ref) or kind, round(owed, 2)),
        "source_row": source_row,
        "flags": list(flags or []),
    }


def _ledger_heading(head_rows):
    """(ledger name, company) from the heading of a Tally ledger export.

    Tally prints the company first and the ledger's own name directly above
    the words "Ledger Account". Taking the first line instead names the wrong
    business - our own - on every ledger we are handed.
    """
    lines = [" ".join(str(c).strip() for c in row if str(c if c is not None else "").strip())
             for row in head_rows]
    lines = [ln for ln in lines if ln]
    for i, ln in enumerate(lines):
        if re.match(r"^ledger\s*(account|a/?c)\b", ln, re.I) and i > 0:
            return lines[i - 1], (lines[0] if i > 1 else "")
    return None, None


def _read_name_beside_prefix(grid, header_idx, colmap):
    """Point Particulars at the ledger names when Tally put To/By there instead."""
    p = colmap.get("particulars")
    if p is None:
        return
    values = [str(row[p]).strip().lower() for row in grid[header_idx + 1:header_idx + 400]
              if p < len(row) and row[p] is not None and str(row[p]).strip()]
    if not values or sum(v in _PREFIX_NATURE for v in values) < 0.6 * len(values):
        return
    head = grid[header_idx]
    beside = p + 1
    if (beside < len(head) and not str(head[beside] or "").strip()
            and beside not in colmap.values()):
        colmap["prefix"] = p
        colmap["particulars"] = beside


def _owed_from_balance(cell, side):
    """A printed balance as an amount owed, honouring a Dr/Cr suffix."""
    value = _num(cell)
    if value is None:
        return None
    m = re.search(r"(dr|cr)\.?\s*$", str(cell).strip(), re.I)
    if not m:
        return r2(value)
    debit_nature = m.group(1).lower() == "dr"
    return r2(abs(value) if debit_nature == (side == "theirs") else -abs(value))


def _closing_owed(row, colmap, side):
    """The closing balance a ledger prints for itself, as an amount owed.

    Tally puts the figure in whichever column it likes on this line, so the
    amount is the first number on it and its nature comes from the To/By or
    Dr/Cr word, a Dr/Cr suffix, or a Dr/Cr column. With none of those there is
    no telling which way it runs, and None is honest.
    """
    cells = [c for c in row if c is not None and str(c).strip()]
    value = next((v for v in (_num(c) for c in cells) if v), None)
    if value is None:
        return None
    nature = None
    for c in cells:
        m = re.search(r"\d\s*(dr|cr)\.?\s*$", str(c).strip(), re.I)
        if m:
            nature = "debit" if m.group(1).lower() == "dr" else "credit"
    p = colmap.get("prefix")
    if nature is None and p is not None and p < len(row):
        nature = _PREFIX_NATURE.get(str(row[p] or "").strip().lower())
    for f in ("balance_drcr", "drcr"):
        i = colmap.get(f)
        if nature is None and i is not None and i < len(row):
            nature = _flag_side(row[i])
    if nature is None:
        return None
    return r2(abs(value) if (nature == "debit") == (side == "theirs") else -abs(value))


def _unfold_two_sided(grid, header_idx):
    """
    Rebuild a two-sided table as one entry per line.

    Interest statements, and ledgers printed in the traditional form, put the
    debits on the left of the page and the credits on the right, each half with
    its own date and amount. Read as an ordinary table only the left half
    survives - which is how a statement showing two repayments came through as
    three advances and nothing else. Returns None for an ordinary table.
    """
    head = grid[header_idx]
    starts = [i for i, c in enumerate(head) if _field_of(_label(c)) == "date"]
    if len(starts) != 2:
        return None
    blocks = []
    for lo, hi in ((starts[0], starts[1]), (starts[1], len(head))):
        cols = {}
        for i in range(lo, hi):
            f = _field_of(_label(head[i]))
            if f and f not in cols:
                cols[f] = i
        amount = next((cols[f] for f in ("amount", "debit", "credit") if f in cols), None)
        if amount is None:
            return None
        if "particulars" not in cols and lo + 1 < amount:
            cols["particulars"] = lo + 1
        blocks.append({"cols": cols, "amount": amount, "lo": lo, "hi": hi})

    # A banner above the header names each half; debits on the left otherwise.
    sides = ["debit", "credit"]
    for row in grid[max(0, header_idx - 2):header_idx]:
        named = {}
        for i, c in enumerate(row):
            m = re.match(r"^(debit|dr|credit|cr)\b", _label(c))
            if not m:
                continue
            for k, b in enumerate(blocks):
                if b["lo"] <= i < b["hi"]:
                    named[k] = "debit" if m.group(1) in ("debit", "dr") else "credit"
        if len(named) == 2 and named[0] != named[1]:
            sides = [named[0], named[1]]

    def at(row, i):
        return row[i] if i is not None and i < len(row) and row[i] is not None else ""

    rows, lines, last = [], [], None
    for r in range(header_idx + 1, len(grid)):
        row = grid[r]
        for k, b in enumerate(blocks):
            raw = at(row, b["cols"].get("date"))
            text = str(at(row, b["cols"].get("particulars"))).strip()
            when = to_date(raw)
            if when is None:
                when, rest = split_leading_date(raw)
                if when is not None and rest:
                    text = f"{rest} {text}".strip()
            amount = _num(at(row, b["amount"]))
            if when is None or not amount:
                continue
            last = r
            days = _num(at(row, b["cols"].get("days")))
            product = _num(at(row, b["cols"].get("product")))
            lines.append({"side": sides[k], "date": when, "text": text,
                          "amount": abs(amount), "days": days, "product": product})
            rows.append([when, text,
                         abs(amount) if sides[k] == "debit" else "",
                         abs(amount) if sides[k] == "credit" else "",
                         "" if days is None else days,
                         "" if product is None else product])
    if last is None:
        return None

    # The totals line sits directly under the last entry, amounts and no dates.
    totals, tail_from = {}, last + 1
    if tail_from < len(grid):
        row = grid[tail_from]
        if not any(to_date(at(row, b["cols"].get("date"))) for b in blocks):
            for k, b in enumerate(blocks):
                amount = _num(at(row, b["amount"]))
                if amount is not None:
                    totals[sides[k]] = {"amount": abs(amount),
                                        "product": _num(at(row, b["cols"].get("product")))}
            if totals:
                tail_from += 1
    return {"grid": grid[:header_idx] + [TWO_SIDED_HEADER] + rows,
            "lines": lines, "totals": totals, "tail": grid[tail_from:],
            "sides": sides}


def _interest_terms(head_text, tail_rows):
    """The rate from the heading, and the figures printed under the table."""
    terms = {}
    m = (re.search(r"@\s*([\d.]+)\s*%\s*p\.?\s*a", head_text, re.I)
         or re.search(r"interest\D{0,25}?([\d.]+)\s*%", head_text, re.I))
    if m:
        terms["rate"] = float(m.group(1))
    m = re.search(r"credit\s*days?\s*:?\s*([\d.]+)", head_text, re.I)
    if m:
        terms["credit_days"] = float(m.group(1))
    for row in tail_rows:
        cells = [str(c if c is not None else "").strip() for c in row]
        for i, text in enumerate(cells):
            if not re.search(r"[A-Za-z]{3}", text):
                continue
            value = next((v for v in (_num(c) for c in cells[i + 1:]) if v is not None), None)
            # recognition drops spaces, so the labels are compared without them
            key = re.sub(r"[^a-z%0-9.]", "", text.lower())
            if value is None:
                break
            if key.startswith("interest"):
                terms["interest"] = abs(value)
                terms["direction"] = (
                    "payable" if re.search(r"payable|paid|allowed", key)
                    else "receivable" if re.search(r"receivable|recoverable|due|charged", key)
                    else None)
            elif key.startswith("tds"):
                terms["tds"] = abs(value)
                m = re.search(r"([\d.]+)%", key)
                if m:
                    terms["tds_rate"] = float(m.group(1))
            elif re.match(r"(dr|cr)\.?cur", key) or "balinlgr" in key or "ledgerbal" in key:
                terms["ledger_balance"] = abs(value)
                terms["ledger_side"] = "credit" if key.startswith("cr") else "debit"
            elif key.startswith(("debitproduct", "creditproduct", "netproduct")):
                terms["net_product"] = abs(value)
            break
    return terms if "interest" in terms else None


def _verify_interest(lines, totals, terms, period_to):
    """
    Redo their interest arithmetic from the figures they printed.

    Each line's product is its amount times its days over a year basis, and
    the interest is the net product at their rate. A figure misread from a scan
    breaks one of those identities, which is how a reading error is told apart
    from a genuine difference - the counterpart of the running balance on a
    bank statement.
    """
    usable = [ln for ln in lines if ln["amount"] and ln["days"] and ln["product"]]
    if not usable:
        return None

    def fits(ln, basis):
        want = ln["amount"] * ln["days"] / basis
        return abs(want - ln["product"]) <= max(1.0, ln["product"] * 1e-6)

    basis = max((360, 365, 1), key=lambda b: sum(fits(ln, b) for ln in usable))
    problems = [
        f"{iso(ln['date'])}: {inr(ln['amount'])} for {ln['days']:g} days gives a "
        f"product of {inr(ln['amount'] * ln['days'] / basis)}, but the statement "
        f"prints {inr(ln['product'])}."
        for ln in usable if not fits(ln, basis)]

    inclusive = None
    if period_to:
        offsets = [ln["days"] - (period_to - ln["date"]).days for ln in usable]
        inclusive = offsets.count(1) >= offsets.count(0)
        want = 1 if inclusive else 0
        problems += [
            f"{iso(ln['date'])}: {ln['days']:g} days, but {(period_to - ln['date']).days + want} "
            f"run from then to {iso(period_to)}."
            for ln, off in zip(usable, offsets) if off != want]

    sums = {}
    for s in ("debit", "credit"):
        sums[s] = {"amount": r2(sum(ln["amount"] for ln in lines if ln["side"] == s)),
                   "product": r2(sum(ln["product"] or 0 for ln in lines if ln["side"] == s))}
        printed = totals.get(s) or {}
        for k in ("amount", "product"):
            if printed.get(k) is not None and abs(printed[k] - sums[s][k]) > 1.0:
                problems.append(f"The {s} {k}s add up to {inr(sums[s][k])}, but the "
                                f"statement's total says {inr(printed[k])}.")
    net = abs(sums["debit"]["product"] - sums["credit"]["product"])
    if terms.get("net_product") is not None and abs(terms["net_product"] - net) > 1.0:
        problems.append(f"The net product works out to {inr(net)}, but the statement "
                        f"prints {inr(terms['net_product'])}.")

    year_days = 365 if basis == 365 else 360
    expected = None
    stated = terms.get("interest")
    if terms.get("rate"):
        if basis == 1:
            # products in plain amount-days: the year basis only shows in the interest
            options = {d: net * terms["rate"] / 100 / d for d in (365, 360)}
            year_days = min(options, key=lambda d: abs(options[d] - (stated or options[365])))
            expected = options[year_days]
        else:
            expected = net * terms["rate"] / 100
        if stated is not None and abs(expected - stated) > 1.0:
            problems.append(f"{inr(net)} of product at {terms['rate']:g}% gives interest "
                            f"of {inr(expected)}, but the statement says {inr(stated)}.")
    tds_expected = None
    if terms.get("tds_rate") and stated:
        tds_expected = stated * terms["tds_rate"] / 100
        if terms.get("tds") is not None and abs(tds_expected - terms["tds"]) > 10:
            problems.append(f"TDS at {terms['tds_rate']:g}% of {inr(stated)} is "
                            f"{inr(tds_expected)}, but the statement says {inr(terms['tds'])}.")
    return {
        "basis": basis, "year_days": year_days, "days_inclusive": inclusive,
        "lines": len(usable), "net_product": r2(net),
        "interest_expected": r2(expected), "tds_expected": r2(tds_expected),
        "totals": sums, "problems": problems, "ok": not problems,
    }


def _add_interest_entries(entries, terms, side, period_to):
    """Turn the interest and TDS an interest statement computed into entries."""
    when = period_to or max(e["date"] for e in entries)
    receivable = (terms.get("direction")
                  or ("receivable" if terms.get("ledger_side", "debit") == "debit"
                      else "payable")) == "receivable"
    rate = f" @ {terms['rate']:g}% p.a." if terms.get("rate") else ""
    tds_rate = f" @ {terms['tds_rate']:g}%" if terms.get("tds_rate") else ""
    # In the lender's books the interest is a debit to the borrower and the TDS
    # deducted from it a credit; interest they owe us runs the other way.
    for kind, amount, is_debit, text in (
        ("interest", terms.get("interest"), receivable,
         f"Interest{rate} as computed on their statement"),
        ("tds", terms.get("tds"), not receivable, f"TDS{tds_rate} on that interest"),
    ):
        if not amount:
            continue
        entries.append(_entry(
            len(entries), side, when, text, "", kind,
            amount if is_debit else 0.0, 0.0 if is_debit else amount,
            flags=["computed_on_statement"]))


def _guess_party(head_rows):
    for row in head_rows:
        for cell in row:
            text = str(cell or "").strip()
            if len(text) < 4 or len(text) > 70:
                continue
            low = text.lower()
            if any(w in low for w in ("statement", "ledger", "account", "gstin",
                                      "period", "from", "to ", "page")):
                continue
            if re.search(r"[A-Za-z]{3}", text):
                return text
    return ""


def _kind_summary(entries):
    out = {}
    for e in entries:
        k = out.setdefault(e["kind"], {"kind": e["kind"], "count": 0, "owed": 0.0})
        k["count"] += 1
        k["owed"] += e["owed"]
    for k in out.values():
        k["owed"] = r2(k["owed"])
    return sorted(out.values(), key=lambda k: -abs(k["owed"]))


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

DEFAULTS = {
    "amount_tolerance": 1.0,
    "date_window_days": 10,
    "wide_date_window_days": 60,
    "ref_min_len": 3,
    "dispute_max_pct": 12.0,
}

OPEN_STATUSES = {"Timing difference", "Amount disputed",
                 "In their statement only", "In our ledger only"}


def reconcile_statement(theirs, ours, settings=None, today=None,
                        their_meta=None, our_meta=None, gst_records=None,
                        relation="supplier"):
    """
    Match a received statement against our ledger and bridge the two balances.

    `gst_records` is optional: when the party is a supplier and the GSTR-2B
    documents for them are supplied, every invoice on their statement is also
    checked against what they actually filed, which is the three-way test that
    catches an invoice billed but never returned.
    """
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    # Differences are aged as at the statement's own date, not the day the
    # reconciliation happens to be run: a year-end statement worked through
    # in September would otherwise call a payment made a week before the year
    # end eighteen months stale.
    today = today or (to_date((their_meta or {}).get("period_to"))
                      or to_date((their_meta or {}).get("last_date"))
                      or datetime.now())
    tol = cfg["amount_tolerance"]
    their_meta = their_meta or {}
    our_meta = our_meta or {}

    used_t, used_o = set(), set()
    pairs = []

    by_amount = {}
    for o in ours:
        by_amount.setdefault(round(abs(o["owed"]), 2), []).append(o)

    def same_direction(a, b):
        return a["owed"] * b["owed"] > 0

    # --- pass 1: document number and amount agree -------------------------
    for t in theirs:
        ct = clean_inv(t["ref"])
        if len(ct) < cfg["ref_min_len"]:
            continue
        for o in by_amount.get(round(abs(t["owed"]), 2), []):
            if o["id"] in used_o or t["id"] in used_t or not same_direction(t, o):
                continue
            if abs(abs(t["owed"]) - abs(o["owed"])) > tol:
                continue
            co = clean_inv(o["ref"])
            if ct == co or tails_match(t["ref"], o["ref"]):
                pairs.append(_pair(t, o, "Agreed", 1.0, "document no. + amount"))
                used_t.add(t["id"])
                used_o.add(o["id"])
                break

    # --- pass 2: amount and date agree ------------------------------------
    win = cfg["date_window_days"]
    for t in theirs:
        if t["id"] in used_t:
            continue
        best, best_gap = None, 10 ** 6
        for o in by_amount.get(round(abs(t["owed"]), 2), []):
            if o["id"] in used_o or not same_direction(t, o):
                continue
            if abs(abs(t["owed"]) - abs(o["owed"])) > tol:
                continue
            gap = abs((t["date"] - o["date"]).days)
            if gap <= win and gap < best_gap:
                best, best_gap = o, gap
        if best is not None:
            pairs.append(_pair(t, best, "Agreed", 0.95,
                               f"amount + date ({best_gap}d apart)",
                               ["date_gap"] if best_gap else []))
            used_t.add(t["id"])
            used_o.add(best["id"])

    # --- pass 2b: interest and TDS, compared as totals for the period -------
    # Each side books interest in its own instalments - they compute it once
    # for the year, we may have booked it twice - so matching entry by entry
    # finds nothing and leaves both sides looking one-sided. The totals for
    # the period are what should agree.
    for kind in ("interest", "tds"):
        t_rows = [t for t in theirs if t["kind"] == kind and t["id"] not in used_t]
        o_rows = [o for o in ours if o["kind"] == kind and o["id"] not in used_o]
        if not t_rows or not o_rows:
            continue
        pairs.append(_period_total_pair(kind, t_rows, o_rows, tol))
        used_t.update(t["id"] for t in t_rows)
        used_o.update(o["id"] for o in o_rows)

    # --- pass 3: same amount, recorded in different periods ---------------
    wide = cfg["wide_date_window_days"]
    for t in theirs:
        if t["id"] in used_t:
            continue
        best, best_gap = None, 10 ** 6
        for o in by_amount.get(round(abs(t["owed"]), 2), []):
            if o["id"] in used_o or not same_direction(t, o):
                continue
            if abs(abs(t["owed"]) - abs(o["owed"])) > tol:
                continue
            gap = abs((t["date"] - o["date"]).days)
            if win < gap <= wide and gap < best_gap:
                best, best_gap = o, gap
        if best is not None:
            pairs.append(_pair(t, best, "Timing difference", 0.8,
                               f"same amount, {best_gap} days apart", ["timing"]))
            used_t.add(t["id"])
            used_o.add(best["id"])

    # --- pass 4: same document, different amount -> a dispute -------------
    for t in theirs:
        if t["id"] in used_t:
            continue
        ct = clean_inv(t["ref"])
        best, best_diff = None, None
        for o in ours:
            if o["id"] in used_o or not same_direction(t, o):
                continue
            if "interest" in (t["kind"], o["kind"]) or "tds" in (t["kind"], o["kind"]):
                if t["kind"] != o["kind"]:
                    continue        # a charge is never a dispute over a payment
            co = clean_inv(o["ref"])
            ref_ok = (len(ct) >= cfg["ref_min_len"] and
                      (ct == co or tails_match(t["ref"], o["ref"])))
            near_date = abs((t["date"] - o["date"]).days) <= win
            if not ref_ok and not near_date:
                continue
            diff = abs(abs(t["owed"]) - abs(o["owed"]))
            if diff <= tol:
                continue
            pct = diff / max(abs(t["owed"]), 1) * 100
            if not ref_ok and pct > cfg["dispute_max_pct"]:
                continue
            if best_diff is None or diff < best_diff:
                best, best_diff = o, diff
        if best is not None:
            pairs.append(_pair(t, best, "Amount disputed", 0.7,
                               "same document, different amount", ["dispute"]))
            used_t.add(t["id"])
            used_o.add(best["id"])

    # --- pass 5: one-sided -------------------------------------------------
    their_only = [t for t in theirs if t["id"] not in used_t]
    our_only = [o for o in ours if o["id"] not in used_o]

    for t in their_only:
        rec = _pair(t, None, "In their statement only", None,
                    "not in our ledger")
        rec["category"] = _their_only_category(t, relation)
        rec["age_days"] = (today - t["date"]).days
        rec["aging_bucket"] = _bucket(rec["age_days"])
        pairs.append(rec)

    for o in our_only:
        rec = _pair(None, o, "In our ledger only", None,
                    "not in their statement")
        rec["category"] = _our_only_category(o, relation)
        rec["age_days"] = (today - o["date"]).days
        rec["aging_bucket"] = _bucket(rec["age_days"])
        pairs.append(rec)

    terms = their_meta.get("interest") or {}
    for p in pairs:
        if "period_total" in p["flags"] and p["kind"] == "interest" and p["difference"]:
            p["explain"] = _explain_interest_gap(p, theirs, their_only, our_only,
                                                 their_meta, terms)

    # --- optional three-way check against what they filed -----------------
    gst_check = None
    if gst_records:
        gst_check = _three_way(theirs, gst_records, tol)

    return _assemble(pairs, theirs, ours, their_only, our_only, cfg,
                     their_meta, our_meta, today, relation, gst_check)


def _bucket(days):
    if days is None:
        return None
    for label, lo, hi in AGING_BUCKETS:
        if lo <= days <= hi:
            return label
    return AGING_BUCKETS[-1][0]


def _their_only_category(t, relation):
    if t["owed"] > 0:
        return ("Billed by them, not in our books" if relation == "supplier"
                else "Charged to us, not in our books")
    if t["kind"] == "credit_note":
        return "Credit note issued by them, no copy with us"
    return "Credited by them, not in our books"


def _our_only_category(o, relation):
    if o["owed"] > 0:
        return ("Booked by us, not in their statement" if relation == "supplier"
                else "Billed by us, not in their statement")
    if o["kind"] == "payment":
        return "Paid by us, not yet credited by them"
    if o["kind"] == "debit_note":
        return "Debit note raised by us, not accepted by them"
    return "Debited by us, not in their statement"


def _pair(t, o, status, score, basis, flags=None):
    flags = list(flags or [])
    if t:
        flags += [f for f in t["flags"] if f not in flags]
    if o:
        flags += [f for f in o["flags"] if f not in flags]
    return {
        "status": status,
        "category": status,
        "fp": (t["fp"] if t else o["fp"]),
        "their_fp": t["fp"] if t else None,
        "our_fp": o["fp"] if o else None,
        "date_theirs": t["date"] if t else None,
        "date_ours": o["date"] if o else None,
        "ref_theirs": t["ref"] if t else None,
        "ref_ours": o["ref"] if o else None,
        "particulars_theirs": t["particulars"] if t else None,
        "particulars_ours": o["particulars"] if o else None,
        "kind": (t["kind"] if t else o["kind"]),
        "owed_theirs": r2(t["owed"]) if t else None,
        "owed_ours": r2(o["owed"]) if o else None,
        "difference": r2(t["owed"] - o["owed"]) if (t and o) else None,
        "their_net": r2(t["owed"]) if t else 0.0,
        "our_net": r2(o["owed"]) if o else 0.0,
        "month_key": (t["month_key"] if t else o["month_key"]),
        "date_gap_days": (abs((t["date"] - o["date"]).days) if (t and o) else None),
        "match_score": score,
        "match_basis": basis,
        "age_days": None,
        "aging_bucket": None,
        "flags": sorted(set(flags)),
        "decision": None,
    }


def _period_total_pair(kind, t_rows, o_rows, tol):
    """One comparison line for a period's interest (or TDS) on each side."""
    label = "Interest" if kind == "interest" else "TDS"
    t_sum = r2(sum(t["owed"] for t in t_rows))
    o_sum = r2(sum(o["owed"] for o in o_rows))
    agreed = abs(t_sum - o_sum) <= tol
    last_t = max(t_rows, key=lambda t: t["date"])
    last_o = max(o_rows, key=lambda o: o["date"])
    rec = _pair(last_t, last_o, "Agreed" if agreed else "Amount disputed",
                0.9 if agreed else 0.7,
                f"{label.lower()} for the period, {len(t_rows)} entr"
                f"{'y' if len(t_rows) == 1 else 'ies'} against {len(o_rows)}",
                ["period_total"] + ([] if agreed else ["dispute"]))

    def listing(rows):
        return "; ".join(f"{iso(x['date'])} {inr(abs(x['owed']))}" for x in
                         sorted(rows, key=lambda x: x["date"]))

    rec.update({
        "category": ("Agreed" if agreed else
                     "Interest worked out differently" if kind == "interest"
                     else "TDS differs"),
        "ref_theirs": label, "ref_ours": label,
        "particulars_theirs": (last_t["particulars"] if len(t_rows) == 1
                               else listing(t_rows)),
        "particulars_ours": listing(o_rows),
        "owed_theirs": t_sum, "owed_ours": o_sum,
        "difference": r2(t_sum - o_sum),
        "their_net": t_sum, "our_net": o_sum,
        "date_gap_days": None,
    })
    return rec


def _explain_interest_gap(p, theirs, their_only, our_only, their_meta, terms):
    """
    Put the interest difference down to the principal the two books disagree on.

    A payment we made that they have not recorded leaves their balance higher,
    so they charge interest on it up to the period end. That is estimated per
    item as the principal times the days after it at their rate - capped at
    their own balance on the day, because a payment that also clears interest
    can only stop interest on the principal still outstanding.
    """
    rate = (terms.get("rate") or 0) / 100
    end = to_date(their_meta.get("period_to")) or max(t["date"] for t in theirs)
    if not rate or end is None:
        return None
    year = ((terms.get("check") or {}).get("year_days")) or 365
    moves = [t for t in theirs if t["kind"] not in ("interest", "tds")]

    def their_balance(d):
        return (their_meta.get("opening_balance") or 0.0) + sum(
            t["owed"] for t in moves if t["date"] <= d)

    items = []
    for rows, side in ((our_only, "ours"), (their_only, "theirs")):
        for x in rows:
            if x["kind"] in ("interest", "tds") or x["date"] > end:
                continue
            # what their books carry extra because of this item
            extra = -x["owed"] if side == "ours" else x["owed"]
            if extra > 0 and side == "ours":
                extra = min(extra, max(their_balance(x["date"]), 0.0))
            days = (end - x["date"]).days
            if not extra or days <= 0:
                continue
            items.append({
                "side": side, "date": iso(x["date"]), "ref": x["ref"],
                "particulars": x["particulars"], "amount": r2(abs(x["owed"])),
                "principal": r2(extra), "days": days,
                "effect": r2(extra * days * rate / year),
            })
    explained = r2(sum(i["effect"] for i in items))
    gap = p["difference"]
    return {"gap": gap, "explained": explained, "items": items,
            "rate": terms.get("rate"), "year_days": year,
            "whole": bool(items) and abs(gap - explained) <= max(1.0, abs(gap) * 0.005)}


def _three_way(theirs, gst_records, tol):
    """Do the invoices on their statement appear in what they filed?"""
    filed = {}
    for g in gst_records:
        filed.setdefault(clean_inv(g.get("inv_no")), []).append(g)

    billed = [t for t in theirs if t["owed"] > 0 and t["kind"] in ("invoice", "unknown")]
    missing, mismatched, ok = [], [], 0
    for t in billed:
        cands = filed.get(clean_inv(t["ref"]), [])
        hit = next((g for g in cands
                    if abs(abs(g.get("inv_val", 0)) - abs(t["owed"])) <= max(tol, 2.0)),
                   None)
        if hit:
            ok += 1
            continue
        if cands:
            mismatched.append({
                "ref": t["ref"], "date": iso(t["date"]),
                "statement_amount": r2(t["owed"]),
                "filed_amount": r2(cands[0].get("inv_val")),
                "difference": r2(t["owed"] - (cands[0].get("inv_val") or 0)),
            })
        else:
            missing.append({"ref": t["ref"], "date": iso(t["date"]),
                            "statement_amount": r2(t["owed"])})

    return {
        "checked": len(billed),
        "agreed": ok,
        "not_filed": missing[:60],
        "not_filed_count": len(missing),
        "not_filed_amount": r2(sum(m["statement_amount"] for m in missing)),
        "value_differs": mismatched[:60],
        "value_differs_count": len(mismatched),
    }


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------

# `side` says which set of books the group's entries are missing from, which
# is what fixes their sign: an item only they have moves their balance by its
# own signed amount, an item only we have moves ours. A group's Add/Less
# wording is then read off the computed effect, never assumed - a credit note
# and an invoice can sit in the same group and pull in opposite directions.
BRIDGE_GROUPS = [
    ("their_invoices", "Invoices in their statement, not in our books", "theirs",
     ("Billed by them, not in our books", "Charged to us, not in our books")),
    ("their_credits", "Credits in their statement, not in our books", "theirs",
     ("Credited by them, not in our books",
      "Credit note issued by them, no copy with us")),
    ("our_invoices", "Entries in our books, not in their statement", "ours",
     ("Booked by us, not in their statement",
      "Billed by us, not in their statement")),
    ("our_payments", "Payments we made, not yet credited by them", "ours",
     ("Paid by us, not yet credited by them",
      "Debit note raised by us, not accepted by them",
      "Debited by us, not in their statement")),
]


def _assemble(pairs, theirs, ours, their_only, our_only, cfg, their_meta,
              our_meta, today, relation, gst_check):
    their_close = their_meta.get("closing_balance")
    our_close = our_meta.get("closing_balance")
    if their_close is None:
        their_close = r2((their_meta.get("opening_balance") or 0.0)
                         + sum(t["owed"] for t in theirs))
    if our_close is None:
        our_close = r2((our_meta.get("opening_balance") or 0.0)
                       + sum(o["owed"] for o in ours))

    opening_gap = r2((their_meta.get("opening_balance") or 0.0)
                     - (our_meta.get("opening_balance") or 0.0))
    matched = [p for p in pairs if p["status"] in
               ("Agreed", "Timing difference", "Amount disputed")]
    period_totals = [p for p in matched if "period_total" in p["flags"]]
    matched_gap = r2(sum(p["their_net"] - p["our_net"] for p in matched
                         if "period_total" not in p["flags"]))

    groups = []
    for key, label, side, cats in BRIDGE_GROUPS:
        rows = [p for p in pairs if p["category"] in cats]
        if not rows:
            continue
        # Signed, from the movement itself.
        effect = r2(sum(r["their_net"] for r in rows) if side == "theirs"
                    else -sum(r["our_net"] for r in rows))
        groups.append({
            "key": key, "label": label, "side": side, "count": len(rows),
            "amount": r2(sum(abs(r["owed_theirs"] if r["owed_theirs"] is not None
                                 else r["owed_ours"]) for r in rows)),
            "effect": effect,
            "rows": [_bridge_row(r) for r in rows],
        })

    lines = [{"key": "ours", "label": "Balance as per our ledger",
              "amount": our_close, "kind": "opening"}]
    for g in groups:
        lines.append({"key": g["key"],
                      "label": ("Add: " if g["effect"] >= 0 else "Less: ") + g["label"],
                      "amount": g["effect"], "count": g["count"], "kind": "adjust"})
    if abs(matched_gap) > 0.5:
        lines.append({"key": "matched_diff",
                      "label": "Adjust: differences on matched documents",
                      "amount": matched_gap, "kind": "adjust"})
    for p in period_totals:
        gap = r2(p["their_net"] - p["our_net"])
        if abs(gap) > 0.5:
            what = "interest as they worked it out" if p["kind"] == "interest" \
                else "TDS as they show it"
            lines.append({"key": f"{p['kind']}_diff", "label": f"Adjust: {what}, against ours",
                          "amount": gap, "kind": "adjust"})
    if abs(opening_gap) > 0.5:
        lines.append({"key": "opening_diff",
                      "label": "Adjust: difference in opening balances",
                      "amount": opening_gap, "kind": "adjust"})

    derived = r2(our_close + sum(l["amount"] for l in lines[1:]))
    residual = r2(their_close - derived)
    if abs(residual) > 0.5:
        lines.append({"key": "residual", "label": "Unexplained difference",
                      "amount": residual, "kind": "adjust", "tone": "risk"})
    lines.append({"key": "theirs", "label": "Balance as per their statement",
                  "amount": their_close, "kind": "closing"})

    counts = {
        "their_entries": len(theirs),
        "our_entries": len(ours),
        "agreed": sum(1 for p in pairs if p["status"] == "Agreed"),
        "timing": sum(1 for p in pairs if p["status"] == "Timing difference"),
        "disputed": sum(1 for p in pairs if p["status"] == "Amount disputed"),
        "their_only": len(their_only),
        "our_only": len(our_only),
        "open": len(their_only) + len(our_only)
                + sum(1 for p in pairs if p["status"] in
                      ("Timing difference", "Amount disputed")),
    }
    agreed_total = counts["agreed"] + counts["timing"] + counts["disputed"]
    match_rate = round(agreed_total / len(theirs) * 100, 1) if theirs else 0.0

    aging = _aging(pairs)
    pairs.sort(key=lambda p: (
        0 if p["status"] in OPEN_STATUSES else 1,
        -(abs(p["owed_theirs"] or p["owed_ours"] or 0)),
    ))

    return {
        "kind": "statement",
        "relation": relation,
        "config": cfg,
        "their_meta": their_meta,
        "our_meta": our_meta,
        "balances": {
            "theirs": their_close,
            "ours": our_close,
            "difference": r2(their_close - our_close),
            "their_stated": their_meta.get("stated_closing"),
            "ties": abs(residual) <= 0.5,
            "residual": residual,
        },
        "bridge": {"lines": lines, "groups": groups,
                   "ties": abs(residual) <= 0.5, "residual": residual},
        "counts": counts,
        "rates": {"match_rate": match_rate},
        "all_records": pairs,
        "aging": aging,
        "gst_check": gst_check,
        "findings": _findings(pairs, gst_check, relation, today, their_meta, our_meta),
        "generated_at": iso(datetime.now()),
        "as_at": iso(today),
    }


def _bridge_row(r):
    return {
        "fp": r["fp"],
        "date": r["date_theirs"] or r["date_ours"],
        "ref": r["ref_theirs"] or r["ref_ours"],
        "particulars": r["particulars_theirs"] or r["particulars_ours"],
        "amount": r["owed_theirs"] if r["owed_theirs"] is not None else r["owed_ours"],
        "kind": r["kind"],
        "age_days": r.get("age_days"),
        "aging_bucket": r.get("aging_bucket"),
        "flags": r["flags"],
    }


def _aging(pairs):
    buckets = {label: {"bucket": label, "count": 0, "amount": 0.0}
               for label, _, _ in AGING_BUCKETS}
    for p in pairs:
        if p["status"] not in OPEN_STATUSES:
            continue
        b = p.get("aging_bucket")
        if b not in buckets:
            continue
        buckets[b]["count"] += 1
        buckets[b]["amount"] += abs(p["owed_theirs"] or p["owed_ours"] or 0)
    out = []
    for label, _, _ in AGING_BUCKETS:
        row = buckets[label]
        row["amount"] = r2(row["amount"])
        out.append(row)
    return out


def _findings(pairs, gst_check, relation, today, their_meta=None, our_meta=None):
    out = []
    their_meta, our_meta = their_meta or {}, our_meta or {}
    terms = their_meta.get("interest") or {}

    check = terms.get("check") or {}
    if check and not check.get("ok"):
        out.append({
            "key": "their_arithmetic", "title": "Their interest figures do not add up",
            "tone": "risk", "count": len(check.get("problems") or []), "amount": 0.0,
            "detail": " ".join((check.get("problems") or [])[:3]) + (
                " If the statement was read from a scan or photo, one of these "
                "figures was probably misread - check it against the original "
                "before relying on anything below."),
        })

    for p in pairs:
        if "period_total" not in p["flags"] or p["status"] == "Agreed":
            continue
        diff = p["difference"] or 0.0
        if p["kind"] == "interest":
            story = p.get("explain") or {}
            detail = (f"They work out {inr(abs(p['owed_theirs']))} for the period; "
                      f"we booked {inr(abs(p['owed_ours']))}.")
            for item in (story.get("items") or [])[:3]:
                if item["side"] == "ours":
                    detail += (f" Our payment of {inr(item['amount'])} on {item['date']} is "
                               f"not on their statement, so they charge interest on "
                               f"{inr(item['principal'])} for the {item['days']} days after "
                               f"it: {inr(item['effect'])}.")
                else:
                    detail += (f" Their entry of {inr(item['amount'])} on {item['date']} "
                               f"is not in our books: {inr(item['effect'])} of interest.")
            if story.get("whole"):
                detail += " That accounts for the whole difference."
            elif story.get("items"):
                detail += (f" That explains {inr(story['explained'])} of it; the rest is "
                           "down to how each side counts the days.")
            out.append({"key": "interest_diff", "title": "Interest worked out differently",
                        "tone": "warn", "count": 1, "amount": r2(abs(diff)),
                        "detail": detail})
        else:
            interest = next((q for q in pairs if "period_total" in q["flags"]
                             and q["kind"] == "interest"), None)
            tds_rate = (terms.get("tds_rate") or 10) / 100
            follows = interest and interest["difference"] and \
                abs(abs(diff) - abs(interest["difference"]) * tds_rate) <= 10
            out.append({
                "key": "tds_diff", "title": "TDS differs", "tone": "warn", "count": 1,
                "amount": r2(abs(diff)),
                "detail": (f"They show TDS of {inr(abs(p['owed_theirs']))}; we deducted "
                           f"{inr(abs(p['owed_ours']))}."
                           + (" It follows the interest difference, and settles when "
                              "that does." if follows else
                              " Check the rate and the interest it was worked out on.")),
            })

    gap = r2((their_meta.get("opening_balance") or 0.0)
             - (our_meta.get("opening_balance") or 0.0))
    if abs(gap) > 0.5:
        detail = (f"Their statement opens at {inr(their_meta.get('opening_balance') or 0)}; "
                  f"our ledger at {inr(our_meta.get('opening_balance') or 0)}. Agree last "
                  "year's closing balance first - every figure after it inherits the "
                  "difference.")
        if their_meta.get("document") == "interest_statement":
            detail += (" An interest statement usually carries principal only, so the "
                       "gap may be last year's interest, net of TDS, still sitting in "
                       "one of the two accounts.")
        out.append({"key": "opening_diff", "title": "Opening balances differ",
                    "tone": "warn", "count": 1, "amount": abs(gap), "detail": detail})

    def add(key, title, tone, detail, rows):
        if not rows:
            return
        out.append({
            "key": key, "title": title, "tone": tone, "detail": detail,
            "count": len(rows),
            "amount": r2(sum(abs(r.get("owed_theirs") or r.get("owed_ours") or 0)
                             for r in rows)),
        })

    add("disputed", "Amounts disputed", "risk",
        "The same document carries a different figure on each side. Get a copy of "
        "the invoice and settle the figure before it ages.",
        [p for p in pairs if p["status"] == "Amount disputed"
         and "period_total" not in p["flags"]])

    add("unbilled", "Billed by them, absent from our books", "risk",
        ("These are purchases the supplier says they made to us and we have no "
         "record of. Each one is a liability we are not carrying, and the input "
         "tax credit on it is unclaimed."
         if relation == "supplier" else
         "Charges the party says they raised on us that our books do not show."),
        [p for p in pairs if p["category"] in
         ("Billed by them, not in our books", "Charged to us, not in our books")])

    add("in_transit", "Payments not yet credited by them", "warn",
        "We have paid, they have not recorded it. Usually in transit; if it is more "
        "than a few weeks old, send the payment advice.",
        [p for p in pairs if p["category"] == "Paid by us, not yet credited by them"])

    add("our_only", "In our books, absent from their statement", "warn",
        "Either they have missed it or we have booked something they never raised. "
        "Worth resolving before the balance is confirmed.",
        [p for p in pairs if p["category"] in
         ("Booked by us, not in their statement",
          "Billed by us, not in their statement")])

    add("credit_notes", "Credit notes we have no copy of", "warn",
        "They have credited us and we have not recorded it. Ask for the credit note; "
        "if it carries tax, it also changes the credit already claimed.",
        [p for p in pairs if p["category"] ==
         "Credit note issued by them, no copy with us"])

    stale = [p for p in pairs
             if p["status"] in OPEN_STATUSES and (p.get("age_days") or 0) > 180]
    add("stale", "Differences older than six months", "risk",
        "An unresolved difference this old rarely resolves itself, and it will be "
        "questioned in the audit.", stale)

    if gst_check and gst_check.get("not_filed_count"):
        out.append({
            "key": "not_filed",
            "title": "On their statement but never filed in GSTR-1",
            "tone": "risk",
            "count": gst_check["not_filed_count"],
            "amount": gst_check["not_filed_amount"],
            "detail": "They have billed us for these and they do not appear in our "
                      "GSTR-2B, which means the supplier has not returned them. No "
                      "credit is available until they do.",
        })
    if gst_check and gst_check.get("value_differs_count"):
        out.append({
            "key": "filed_differs",
            "title": "Filed at a different value from their statement",
            "tone": "warn",
            "count": gst_check["value_differs_count"],
            "amount": r2(sum(abs(v["difference"]) for v in gst_check["value_differs"])),
            "detail": "The figure the supplier filed on the portal differs from the "
                      "figure on the statement they sent. One of the two is wrong.",
        })

    rank = {"risk": 0, "warn": 1, "info": 2}
    out.sort(key=lambda f: (rank.get(f["tone"], 3), -f["count"]))
    del today
    return out


# ---------------------------------------------------------------------------
# Confirmation letter
# ---------------------------------------------------------------------------

def confirmation_letter(result, entity=None, contact=None, as_at=None):
    """
    A balance-confirmation request with the reconciling items set out, which is
    what makes it answerable rather than just a number to argue with.
    """
    entity = entity or {}
    contact = contact or {}
    tm = result["their_meta"]
    party = tm.get("party_name") or "Sir / Madam"
    as_at = _day(as_at or tm.get("period_to") or tm.get("last_date") or datetime.now())
    ours = result["balances"]["ours"]
    theirs = result["balances"]["theirs"]
    relation = result["relation"]
    side_word = "payable to you" if relation == "supplier" else "receivable from you"

    shows = f"The statement you sent shows {inr(abs(theirs))}."
    terms = tm.get("interest") or {}
    if terms.get("principal_balance") is not None and terms.get("interest"):
        shows = (f"The statement you sent shows a ledger balance of "
                 f"{inr(abs(terms['principal_balance']))}, and with the interest of "
                 f"{inr(terms['interest'])}"
                 + (f" less TDS of {inr(terms['tds'])}" if terms.get("tds") else "")
                 + f" worked out on it, {inr(abs(theirs))}.")
    lines = [
        f"Subject: Confirmation of balance as at {as_at}",
        "",
        f"Dear {contact.get('contact') or 'Sir / Madam'},",
        "",
        f"We are closing our books for the period ended {as_at} and are confirming "
        f"balances with all parties.",
        "",
        f"As per our ledger, the amount {side_word} as at that date is "
        f"{inr(abs(ours))}"
        + (f" ({'Cr' if ours > 0 else 'Dr'})." if ours else "."),
        shows,
        "",
    ]

    groups = result["bridge"]["groups"]
    if groups:
        lines.append("The difference is made up of the following items. Please "
                     "confirm each one or send us the document concerned.")
        lines.append("")
        n = 0
        for g in groups:
            lines.append(f"{g['label']} - {len(g['rows'])} item(s), {inr(g['amount'])}")
            for row in g["rows"][:12]:
                n += 1
                lines.append(
                    f"    {n}. {row['ref'] or '(no reference)'}"
                    f"   dated {_day(row['date']) or 'n/a'}"
                    f"   {inr(abs(row['amount'] or 0))}"
                )
            if len(g["rows"]) > 12:
                lines.append(f"    ... and {len(g['rows']) - 12} more")
            lines.append("")

    records = result["all_records"]
    totals = [p for p in records if "period_total" in (p.get("flags") or [])
              and p["status"] == "Amount disputed"]
    if totals:
        lines.append("Interest and TDS for the period do not agree:")
        lines.append("")
        for p in totals:
            lines.append(
                f"    {p['ref_theirs']}: your statement {inr(abs(p['owed_theirs'] or 0))}"
                f"   our books {inr(abs(p['owed_ours'] or 0))}"
                f"   difference {inr(abs(p['difference'] or 0))}")
            for item in ((p.get("explain") or {}).get("items") or [])[:3]:
                if item["side"] == "ours":
                    lines.append(
                        f"      Our payment of {inr(item['amount'])} on {_day(item['date'])} does "
                        f"not appear on your statement; interest on "
                        f"{inr(item['principal'])} for the {item['days']} days after it "
                        f"comes to {inr(item['effect'])}.")
        lines.append("")

    disputed = [p for p in records if p["status"] == "Amount disputed"
                and "period_total" not in (p.get("flags") or [])]
    if disputed:
        lines.append("The following documents carry a different amount in each set "
                     "of books. Please send a copy of each.")
        lines.append("")
        for i, p in enumerate(disputed[:12], start=1):
            lines.append(
                f"    {i}. {p['ref_theirs'] or p['ref_ours'] or '(no reference)'}"
                f"   your books {inr(abs(p['owed_theirs'] or 0))}"
                f"   our books {inr(abs(p['owed_ours'] or 0))}"
                f"   difference {inr(abs(p['difference'] or 0))}"
            )
        lines.append("")

    if result.get("gst_check") and result["gst_check"].get("not_filed_count"):
        lines.append(
            f"Separately, {result['gst_check']['not_filed_count']} invoice(s) on your "
            f"statement totalling {inr(result['gst_check']['not_filed_amount'])} do not "
            f"appear in our GSTR-2B. Please confirm the return period in which they "
            f"were reported, or file them, so that we can take the credit."
        )
        lines.append("")

    lines += [
        "A reply by email is sufficient. If you agree with our figure, please "
        "confirm it as it stands.",
        "",
        "Regards,",
        entity.get("preparer") or entity.get("entity_name") or "",
        entity.get("entity_name") or "",
    ]
    body = "\n".join(lines)
    subject = f"Confirmation of balance as at {as_at}"
    return {
        "party": party,
        "subject": subject,
        "body": body,
        "email": contact.get("email") or "",
        "mailto": _mailto(contact.get("email"), subject, body),
    }


def _day(value):
    """A date as YYYY-MM-DD for the letter, whatever shape it arrives in."""
    if isinstance(value, str):
        return value[:10]
    return iso(value) or ""


def _mailto(email, subject, body):
    from urllib.parse import quote
    if not email:
        return ""
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


def _name_score(a, b):
    if not a or not b:
        return 0
    return fuzz.token_set_ratio(a, b, processor=fuzz_utils.default_process)


def _sheet_score(party, ledger):
    """How well a ledger's name matches the party on a statement, 0-100.

    Token sort rather than token set: "Trading Company" must not score 100
    against every ledger that ends in those two words. The squashed comparison
    catches a name recognised with its spaces missing.
    """
    if not party or not ledger:
        return 0
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())      # noqa: E731
    return max(fuzz.token_sort_ratio(party, ledger, processor=fuzz_utils.default_process),
               fuzz.ratio(squash(party), squash(ledger)))


def parse_statement_workbook(doc, side, filename=None, party_name=None, sheet=None,
                             match=None):
    """
    Parse the right sheet of a workbook as a statement or ledger.

    A Tally export often holds several ledgers, one per sheet. Taking the
    biggest sheet picked a purchase ledger for some other supplier and failed
    outright, so every sheet is read and the one whose ledger name matches
    `match` - the party named on their statement - wins. Returns
    (parsed, info); info is None for a single-sheet document.
    """
    # Only a workbook holds genuinely alternative ledgers. A PDF's "grids" are
    # its pages, and one ledger runs across all of them, so choosing between
    # them would silently read page one and drop the rest.
    grids = (doc.get("grids") or {}) if doc.get("kind") == "excel" else {}
    if len(grids) <= 1 and not sheet:
        return parse_statement(doc["grid"], side=side, filename=filename,
                               party_name=party_name), None

    candidates = []
    for name, grid in grids.items():
        c = {"sheet": name, "ledger": None, "entries": 0, "score": 0, "error": None}
        try:
            parsed = parse_statement(grid, side=side, filename=filename,
                                     party_name=party_name)
            c.update(parsed=parsed, entries=parsed["meta"]["count"],
                     ledger=parsed["meta"].get("ledger_name") or parsed["meta"]["party_name"])
        except ValueError as exc:
            c["error"] = str(exc)
        if match:
            c["score"] = max(_sheet_score(match, c["ledger"]), _sheet_score(match, name))
        candidates.append(c)

    readable = [c for c in candidates if c.get("parsed")]
    if sheet:
        chosen = next((c for c in candidates if c["sheet"] == sheet), None)
        if chosen is None:
            raise ValueError(f"There is no sheet called '{sheet}' in {filename or 'that workbook'}.")
        if chosen["error"]:
            raise ValueError(f"Sheet '{sheet}': {chosen['error']}")
        reason = "chosen"
    elif not readable:
        raise ValueError(
            "None of the sheets in that workbook reads as a ledger or statement: "
            + "; ".join(f"{c['sheet']}: {c['error']}" for c in candidates))
    else:
        named = [c for c in readable if c["score"] >= 75]
        if named:
            chosen, reason = max(named, key=lambda c: c["score"]), "matched"
        elif len(readable) == 1:
            chosen, reason = readable[0], "only"
        else:
            chosen, reason = max(readable, key=lambda c: c["entries"]), "largest"
    info = {"sheet": chosen["sheet"], "reason": reason, "match": match,
            "ledger": chosen["ledger"],
            "candidates": [{k: v for k, v in c.items() if k != "parsed"}
                           for c in candidates]}
    return chosen["parsed"], info


def match_party_to_ledger(name, candidates, threshold=70):
    """Best ledger name for a party, used when wiring a statement to a ledger."""
    best, best_score = None, 0
    for c in candidates:
        score = max(_name_score(name, c), _name_score(party_core(name), party_core(c)))
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= threshold else (None, best_score)
