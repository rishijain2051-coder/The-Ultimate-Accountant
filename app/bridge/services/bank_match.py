"""What a bank line posts to in Tally, and whether it is there already.

A line's words carry keys to what it was posted to before: an account (the
masked UBINX1446 of an IMPS), a biller's number (0987654321), a phone, the
payee's name, or the kind of line (interest, charges, GST on charges, cash).
Each key remembers the ledgers it went to (BankRule), learnt from the
vouchers already in Tally and from what you approve. A line with no key
learnt yet is matched to your ledgers by name, or by an account number in a
ledger's name, and bank charges, interest and cash by their kind.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BankRule, Company, Ledger

# The kind of line, from the bank's own words. GST on charges before charges.
KINDS = [
    ("return", r"RTN|RETURN|REMIFAILED|REVERS"),
    ("interest", r"INT\.?COLL|INTEREST|INTCOLL"),
    ("gst_charges", r"CMSGST|GSTONCHG|GSTPAY"),
    ("charges", r"CMSCHARGES|CHARGES|CHRG|SMSCHG|COMMISSION"),
    ("cash", r"CASHWITHDRAWAL|ATMWDL|CASHWDL"),
    ("gst_paid", r"ETAXGSTN|GSTCHALLAN"),
    ("mutual_fund", r"NACH.*(?:MUT|MF)"),
    ("electricity", r"VIDY|ELECTRICITY|DISCOM|VVNL|BSES|MSEDCL"),
    ("telephone", r"AIRTE|BSNL|JIOFIBER|RELIANCEJIO|VODAFONE"),
]
# Kinds whose ledgers are the bank's, whoever the line names.
BANK_KINDS = {"interest", "gst_charges", "charges", "cash", "gst_paid"}


_GSTIN = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")


@lru_cache(maxsize=8192)  # every row is scored against every ledger's name
def _squash(s: str) -> str:
    """Letters only, as OCR runs words together ("RAKESHMEENA"); an O read as
    0 is an O, & is AND, and a GSTIN in a ledger's name is no part of it."""
    s = _GSTIN.sub("", (s or "").upper()).replace("&", "AND")
    return re.sub(r"[^A-Z]", "", re.sub(r"(?<=[A-Z])0|0(?=[A-Z])", "O", s))


def kind(details: str) -> Optional[str]:
    flat = re.sub(r"[\s:]", "", (details or "").upper())
    return next((k for k, pat in KINDS if re.search(pat, flat)), None)


_NAME = [
    # SentIMPS600000000101RAVINDRA P/IDIBX7193/, as OCR reads it too (Sent/MPS, SentlMPS)
    r"SENT\s*[I1L|/]?\s*MPS\s*(?:\d{12}|\d{6,})\s*(.+?)/",
    r"RECD:?\s*IMPS/\d+/(.+?)/",                          # Recd:IMPS/600000000103/VIJAY ANAND/KKBK/
    r"^UPI/(.+?)/",                                       # UPI/RAKESH MEENA/600000000107/
    r"IB:?\s*(?:RECEIVED\s*MONEY\s*FROM|SENT\s*MONEY\s*TO)\s*(.+?)\s*\d{10}",
    r"MB:?\s*SENT\s*NEFT/\s*(.+?)/",
    r"MB:?\s*SENT\s*TO\s*(.+)",
    r"^(?:IMPS|NEFT|RTGS|IFT)\s*-\s*(.+?)(?:-FCM|\s+FCM|-[A-Z]{4}[A-Z]?\d|\s+[A-Z]{3,4}-[A-Z]{4}|$)",
    r"^(.+?)\s*\d{9,}",                                   # a biller and its number: CITY POWER 0987654321
]


def counterparty(details: str) -> str:
    """Who the line is with, as the bank wrote it."""
    d = (details or "").strip()
    for pat in _NAME:
        m = re.search(pat, d, re.I)
        if m and _squash(m.group(1)):
            return m.group(1).strip(" /-")
    return d


# What a transfer's remark says when the sender typed none.
_NO_REMARK = re.compile(r"^(?:KKBK|IBIMPS|PAIDVIA|PAYMENT|PAYTOPERSON|UPI|NA$|TRANSFER|SENT|MB)", re.I)


def remark(details: str) -> str:
    """The sender's own note on a transfer (SentIMPS.../UBINX1446/rentjune,
    UPI/.../.../Bagaram): often who it was for."""
    d = re.sub(r"\s+", "", details or "")
    m = re.search(r"(?:SENT[I1L|/]?MPS|^UPI/).+?/[^/]+/([A-Za-z][^/]{3,})$", d, re.I)
    return m.group(1) if m and not _NO_REMARK.search(m.group(1)) else ""


def _fix_id(s: str) -> str:
    """A masked account read by OCR: its bank code is letters, its end digits."""
    s = s.upper()
    code, rest = s[:-5], s[-5:]
    code = code.translate(str.maketrans("0158", "OISB"))
    return code + "X" + rest[1:].translate(str.maketrans("OILSB", "01158"))


def identifiers(details: str, ref: str = "") -> list[str]:
    """Accounts, biller numbers and phones in the line: the surest keys."""
    d = (details or "").upper()
    out: list[str] = []
    for m in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{4}X[0-9OILSB]{4})(?![A-Z0-9])", d):
        out.append("acct:" + _fix_id(m.group(1)))
    for m in re.finditer(r"/X(\d{4})/", d):
        out.append("acct:X" + m.group(1))
    # A biller's customer number: 10+ digits after letters (VIDY0987654321), not a reference.
    for m in re.finditer(r"(?<=[A-Z\s])(\d{10,12})(?!\d)", d):
        n = m.group(1)
        if re.fullmatch(r"[6-9]\d{9}", n):
            out.append("phone:" + n)
        elif not re.search(r"(?:IMPS|UPI|NEFT|RTGS|COLL)[^/]*" + n, d):
            out.append("num:" + n)
    return list(dict.fromkeys(out))


def keys(details: str, ref: str = "") -> list[str]:
    """Every key the line can be known by, surest first. A return has none:
    it goes back to what its payment went to."""
    k = kind(details)
    if k == "return":
        return []
    out = identifiers(details, ref)
    if k in BANK_KINDS:
        out.append("kind:" + k)
        return out
    # A biller's name is shared by all its numbers (the factory's power and
    # the house's), and so is a kind of bill: the number is the key.
    name = _squash(counterparty(details))
    if len(name) >= 3 and not any(x.startswith("num:") for x in out):
        out.append("name:" + name)
    return out


# --------------------------------------------------------------------------
# learning
# --------------------------------------------------------------------------

def learn(db: Session, company_id: int, details: str, ref: str, ledgers: list[tuple[str, Decimal]],
          when: Optional[date] = None) -> None:
    """Remember what this line went to, by each of its keys."""
    total = sum((abs(a) for _n, a in ledgers), Decimal(0))
    if not ledgers or not total:
        return
    shares = [{"name": n, "share": round(float(abs(a) / total), 4)} for n, a in ledgers]
    names = [s["name"] for s in shares]
    for key in keys(details, ref):
        rule = db.scalar(select(BankRule).where(BankRule.company_id == company_id, BankRule.key == key))
        if rule is None:
            db.add(BankRule(company_id=company_id, key=key, ledgers=shares, times=1, last_date=when))
            db.flush()
        elif [s["name"] for s in rule.ledgers] == names:
            rule.times += 1
            rule.ledgers, rule.last_date = shares, when or rule.last_date
        else:  # a new habit: the latest one counts
            rule.ledgers, rule.times, rule.last_date = shares, 1, when


# --------------------------------------------------------------------------
# suggesting
# --------------------------------------------------------------------------

def split(amount: Decimal, shares: list[dict[str, Any]]) -> list[Decimal]:
    """The amount in the given shares, to the paisa; the last takes the rest."""
    a = abs(amount)
    parts = [(a * Decimal(str(s["share"]))).quantize(Decimal("0.01")) for s in shares[:-1]]
    return parts + [a - sum(parts, Decimal(0))]


_WHAT = {"acct": "the account", "num": "the number", "phone": "the phone", "name": "the name",
         "kind": "lines like this"}
_EXPENSES = ("Indirect Expenses", "Direct Expenses")


def _bank(l: Ledger) -> bool:
    return (l.parent or "").lower() in ("bank accounts", "bank od a/c", "bank occ a/c")


def like(name: str, ledger: str) -> float:
    """How like a ledger's name the bank's name is, 0-100. Banks cut names
    short ("SAILESH CH"), so a ledger that starts like it scores as it; and
    all of it inside a ledger's name ("... (GAHLOT WATCH CO)") is as good."""
    from rapidfuzz import fuzz

    a, b = _squash(name), _squash(ledger)
    if not a or not b:
        return 0.0
    s = fuzz.ratio(a, b)
    if len(a) >= 5 and len(b) > len(a):
        s = max(s, *(fuzz.ratio(a, b[:len(a) + k]) for k in range(3)))
        if len(a) >= 8 and a in b:
            s = max(s, 95.0)
    elif len(b) >= 8 and a.startswith(b):  # the ledger's whole name, and more: "YOGESH THANVI ACCOUNTS"
        s = max(s, 90.0)
    return s


def suggest(db: Session, company_id: int, details: str, ref: str, amount: Decimal,
            bank_ledger: Optional[str] = None, leds: Optional[list[Ledger]] = None) -> dict[str, Any]:
    """The ledgers a line posts to: [{ledger_id, name, amount}], how sure, why,
    and the likeliest ledgers when it cannot say. leds: the company's ledgers,
    read once for a whole statement."""
    if leds is None:
        leds = list(db.scalars(select(Ledger).where(Ledger.company_id == company_id)))
    by_name = {l.tally_name: l for l in leds}
    others = [l for l in leds if l.tally_name != bank_ledger]

    def out(pairs: list[tuple[Ledger, Decimal]], conf: float, why: str,
            cands: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        return {"ledgers": [{"ledger_id": l.id, "name": l.tally_name, "amount": str(a)} for l, a in pairs],
                "confidence": conf, "reason": why, "candidates": cands or []}

    def none(why: str, cands: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        return {"ledgers": [], "confidence": 0.0, "reason": why, "candidates": cands or []}

    def rule(key: str) -> Optional[BankRule]:
        r = db.scalar(select(BankRule).where(BankRule.company_id == company_id, BankRule.key == key))
        return r if r and all(x["name"] in by_name for x in r.ledgers) else None

    def went(r: BankRule, conf: float, why: str) -> dict[str, Any]:
        return out([(by_name[x["name"]], a) for x, a in zip(r.ledgers, split(amount, r.ledgers))], conf,
                   why + (f" ({r.times} times)" if r.times > 1 else ""))

    ks = keys(details, ref)
    ids = [k for k in ks if k.split(":")[0] in ("acct", "num", "phone")]

    # 1. The same account, number or phone as before.
    for key in ids:
        r = rule(key)
        if r:
            tag, val = key.split(":", 1)
            return went(r, 0.97, f"{_WHAT[tag]} {val} went to this before")

    # 2. The account in a ledger's name ("SHARWAN KUMAR (WAGES)(UBINX1446)"),
    # or a bank account of yours ending in its digits.
    for key in ids:
        tag, val = key.split(":", 1)
        if tag != "acct":
            continue
        hit = next((l for l in others if val in re.sub(r"[^A-Z0-9]", "", l.tally_name.upper())), None)
        if hit:
            return out([(hit, abs(amount))], 0.9, f"the account {val} is in its name")
        tail = val[-4:]
        banks = [l for l in others if _bank(l) and re.search(r"\d{4,}", l.tally_name)
                 and re.sub(r"\D", "", l.tally_name).endswith(tail)]
        if len(banks) == 1:
            return out([(banks[0], abs(amount))], 0.85, f"your bank account ending {tail}")

    # 3. The same name, or kind of line, as before.
    for key in ks:
        r = None if key in ids else rule(key)
        if r:
            tag, val = key.split(":", 1)
            new = bool(ids) and tag == "name"  # but to an account not seen before
            return went(r, (0.92 if r.times > 1 else 0.85) - (0.1 if new else 0),
                        f"{_WHAT[tag]} {val} went to this before" + (", to another account" if new else ""))

    k = kind(details)

    def named(*words: str, groups: tuple[str, ...] = ()) -> Optional[Ledger]:
        hits = [l for l in others if all(w in l.tally_name.upper() for w in words)
                and (not groups or (l.parent or "") in groups)]
        return min(hits, key=lambda l: len(l.tally_name)) if hits else None

    def one(led: Optional[Ledger], why: str, conf: float = 0.8) -> Optional[dict[str, Any]]:
        return out([(led, abs(amount))], conf, why) if led else None

    # 4. The bank's own lines, and bills, by their kind.
    flat = re.sub(r"[\s:]", "", (details or "").upper())
    found: Optional[dict[str, Any]] = None
    if k == "interest":
        found = one(named("INTEREST", "O/D") or named("INTEREST", "OD") or named("INTEREST", "CC")
                    or named("BANK", "INTEREST") or named("INTEREST", groups=("Indirect Expenses",)),
                    "the bank's interest")
    elif k == "charges":
        found = one(named("BANK", "CHARGES") or named("BANK", "CHG"), "the bank's charges")
    elif k in ("gst_charges", "gst_paid"):
        portal = k == "gst_paid"

        def exact(n: str) -> Optional[Ledger]:
            return next((l for l in others if l.tally_name.upper() == n), None)

        c = (named("CGST", "PORTAL") if portal else None) or exact("CGST") or named("INPUT", "CGST") \
            or named("CGST", groups=("Duties & Taxes",))
        g = (named("SGST", "PORTAL") if portal else None) or exact("SGST") or named("INPUT", "SGST") \
            or named("SGST", groups=("Duties & Taxes",))
        if c and g:
            half = split(amount, [{"share": 0.5}, {"share": 0.5}])
            found = out([(c, half[0]), (g, half[1])], 0.75,
                        "GST paid" if portal else "the GST on the bank's charges, half CGST and half SGST")
    elif k == "cash":
        found = one(next((l for l in others if (l.parent or "").lower() == "cash-in-hand"), None), "cash withdrawn")
    elif k == "mutual_fund":
        m = re.search(r"([A-Z]{2,8})MF", flat)
        found = one((named(m.group(1), "MUTUAL") if m else None) or named("MUTUAL", "FUND"), "a mutual fund", 0.75)
    elif k == "electricity":
        found = one(named("ELECTRIC", "POWER", groups=_EXPENSES) or named("ELECTRICITY", groups=_EXPENSES),
                    "an electricity bill", 0.7)
    elif k == "telephone":
        prov = next((p for p in ("AIRTEL", "BSNL", "JIO", "VODAFONE") if p[:5] in flat), None)
        found = one((named(prov) if prov else None) or named("TELEPHONE", groups=_EXPENSES), "a telephone bill", 0.7)
    if found:
        return found
    if k in BANK_KINDS:
        return none("pick the ledger")

    # 5. The name (or the sender's remark) against your ledgers.
    who = counterparty(details)
    company = db.get(Company, company_id)
    if company and like(who, company.tally_name) >= 88:
        banks = sorted((l for l in others if _bank(l)),
                       key=lambda l: -sum(w in flat for w in re.findall(r"[A-Z]{3,}", l.tally_name.upper())))
        return none("a transfer to your own account - pick which",
                    [{"ledger_id": l.id, "name": l.tally_name, "score": 0.9} for l in banks[:6]])
    cands: list[dict[str, Any]] = []
    for name in (who, remark(details)):
        if len(_squash(name)) < 4:
            continue
        scored = sorted(((like(name, l.tally_name), l) for l in others), key=lambda t: -t[0])
        if not cands:
            cands = [{"ledger_id": l.id, "name": l.tally_name, "score": round(sc / 100, 2)}
                     for sc, l in scored[:6] if sc >= 60]
        if scored and scored[0][0] >= 88:
            top = scored[0][0]
            near = [l for sc, l in scored if sc >= top - 5]
            base = min(near, key=lambda l: len(_squash(l.tally_name)))
            exact = next((l for l in near if _squash(l.tally_name) == _squash(name)), None)
            # One ledger, or one with its variants ("... (ADVANCE)", "... (TDS APPLICABLE)"):
            # the plain one. Two people as like it: you say which.
            pick = exact or (base if all(_squash(l.tally_name).startswith(_squash(base.tally_name)) for l in near)
                             else None)
            if pick is None:
                return none(f"{name} is like more than one ledger - pick one", cands)
            # A name alike is never sure: check it the first time; after that its account is known.
            return out([(pick, abs(amount))], round(0.6 + 0.25 * (top - 88) / 12, 2),
                       f"the name {name} is like it", cands)
    return none(f"no ledger like {who} - pick one" if len(_squash(who)) >= 4 else "pick the ledger", cands)


def returns(rows: list[dict[str, Any]]) -> dict[int, int]:
    """The payment each returned line gives back: {row i: its payment's i}.
    Its transfer number says which (IMPS-RTN-ACCOUNTCLOSED 622314656376),
    else the last payment of the same amount before it.
    rows: {i, details, ref, amount}."""
    def nums(r: dict[str, Any]) -> set[str]:
        return set(re.findall(r"\d{12}", (r["details"] or "") + " " + (r["ref"] or "")))

    out: dict[int, int] = {}
    for n, r in enumerate(rows):
        if kind(r["details"]) != "return" or r["amount"] is None:
            continue
        before = [p for p in rows[:n][::-1] if kind(p["details"]) != "return"]
        hit = next((p for p in before if nums(r) & nums(p)), None) \
            or next((p for p in before if p["amount"] == -r["amount"]), None)
        if hit:
            out[r["i"]] = hit["i"]
    return out


# --------------------------------------------------------------------------
# already in Tally?
# --------------------------------------------------------------------------

def reconcile(rows: list[dict[str, Any]], vouchers: list[dict[str, Any]], days: int = 3) -> dict[int, dict[str, Any]]:
    """Which rows a voucher in Tally is for already.

    rows: {i, date, amount (+ in, - out)}; vouchers: {date, type, number,
    amount (+ in, - out, the bank's side), others: [(ledger, amount)]}.
    The same day first, then up to `days` apart. A voucher joining lines
    (a payment with its bank charges and their GST) is matched line by line:
    each of its ledgers, or a pair of them (CGST and SGST), is a row."""
    found: dict[int, dict[str, Any]] = {}
    used: set[int] = set()
    pieces: dict[int, list[tuple[str, Decimal]]] = {j: list(v["others"]) for j, v in enumerate(vouchers)}

    def gap(r: dict[str, Any], v: dict[str, Any]) -> int:
        return abs((r["date"] - v["date"]).days) if r["date"] and v["date"] else 99

    def whole(limit: int) -> None:
        for r in rows:
            if r["i"] in found:
                continue
            best = min((j for j, v in enumerate(vouchers) if j not in used and v["amount"] == r["amount"]
                        and gap(r, v) <= limit and len(pieces[j]) == len(v["others"])),
                       key=lambda j: gap(r, vouchers[j]), default=None)
            if best is not None:
                used.add(best)
                v = vouchers[best]
                found[r["i"]] = {**_about(v), "ledgers": [[n, str(a)] for n, a in v["others"]], "part": False}

    def parts(limit: int) -> None:
        for j, v in enumerate(vouchers):
            if j in used and len(pieces[j]) == len(v["others"]):
                continue
            for r in rows:
                if r["i"] in found or gap(r, v) > limit or not pieces[j]:
                    continue
                want, lines = abs(r["amount"]), pieces[j]
                pick = next(([l] for l in lines if abs(l[1]) == want), None) or next(
                    ([a, b] for k, a in enumerate(lines) for b in lines[k + 1:] if abs(a[1]) + abs(b[1]) == want), None)
                if pick and (r["amount"] < 0) == (v["amount"] < 0):
                    for l in pick:
                        lines.remove(l)
                    used.add(j)
                    found[r["i"]] = {**_about(v), "ledgers": [[n, str(a)] for n, a in pick], "part": True}

    whole(0)
    parts(0)
    whole(days)
    parts(1)
    return found


def tally_vouchers(company: str, bank: str, start: date, end: date) -> list[dict[str, Any]]:
    """The vouchers in Tally touching a bank ledger in a window, as reconcile
    takes them: the bank's side (+ in, - out) and the other lines (+ Dr)."""
    from . import tally

    extra = (f'<SVFROMDATE TYPE="Date">{start:%Y%m%d}</SVFROMDATE>'
             f'<SVTODATE TYPE="Date">{end:%Y%m%d}</SVTODATE>')
    root = tally._request(tally._collection(
        "BankVchList", "Voucher", ["DATE", "VOUCHERNUMBER", "VOUCHERTYPENAME", "NARRATION", "MASTERID",
                                   "ISCANCELLED", "ISOPTIONAL", "ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"],
        company=company, extra_vars=extra), timeout=180.0)
    out = []
    for v in root.iter("VOUCHER"):
        if (tally._txt(v, "ISCANCELLED") or "No").lower() == "yes":
            continue
        # Dr is negative in Tally's XML: the bank's Dr is money in.
        lines = [(tally._txt(e, "LEDGERNAME") or "", tally._decimal(tally._txt(e, "AMOUNT")) or Decimal(0))
                 for tag in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST") for e in v.iter(tag)]
        if not any(n == bank for n, _a in lines):
            continue
        out.append({"date": tally._tally_date(tally._txt(v, "DATE")), "type": tally._txt(v, "VOUCHERTYPENAME"),
                    "number": tally._txt(v, "VOUCHERNUMBER"), "narration": tally._txt(v, "NARRATION"),
                    "master_id": tally._txt(v, "MASTERID") or v.get("REMOTEID"),
                    "optional": (tally._txt(v, "ISOPTIONAL") or "No").lower() == "yes",
                    "amount": -sum((a for n, a in lines if n == bank), Decimal(0)),
                    "others": [(n, -a) for n, a in lines if n != bank]})
    return out


def _about(v: dict[str, Any]) -> dict[str, Any]:
    return {"type": v.get("type"), "number": v.get("number"), "date": str(v["date"]) if v.get("date") else None,
            "narration": v.get("narration") or None, "master_id": v.get("master_id")}


def window(rows_dates: list[Optional[date]], days: int = 3) -> tuple[date, date]:
    ds = [d for d in rows_dates if d]
    return min(ds) - timedelta(days=days), max(ds) + timedelta(days=days)
