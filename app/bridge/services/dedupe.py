"""Duplicate detection, against this app's own records and against Tally.

The same bill arrives twice as a re-send, a re-scan, or a reminder copy with a
different filename -- only the first is caught by a file hash. And the most
common duplicate of all is one somebody already keyed into Tally by hand,
which no local database can see. So there are two sources and several nets.

Nothing here blocks. An exact match needs one deliberate acknowledgement
before posting; a similar bill is shown and nothing more. Two genuinely
different invoices for the same amount from the same supplier are normal.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, Entry
from . import matching

# How far either side of the invoice date to look inside Tally. Wide enough to
# catch a bill keyed in against the wrong month, narrow enough to stay fast.
TALLY_WINDOW_DAYS = 45

# Suppliers reprint the same number as INV/001, INV-001, inv 001.
_NOISE = re.compile(r"[^A-Z0-9]")


def norm_invoice_no(s: str | None) -> str:
    if not s:
        return ""
    n = _NOISE.sub("", str(s).upper())
    # Drop leading zeros inside numeric runs: 'INV0042' == 'INV42'.
    return re.sub(r"(?<![0-9])0+(?=\d)", "", n)


def _money(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _same_money(a, b, tol: str = "1") -> bool:
    x, y = _money(a), _money(b)
    return x is not None and y is not None and abs(x - y) <= Decimal(tol)


def check_file(db: Session, sha256: str, exclude_document_id: int | None = None) -> Optional[Document]:
    q = select(Document).where(Document.sha256 == sha256)
    if exclude_document_id:
        q = q.where(Document.id != exclude_document_id)
    return db.scalars(q.order_by(Document.id)).first()


def _finding(source: str, kind: str, confidence: float, reason: str, **detail) -> dict[str, Any]:
    return {"source": source, "kind": kind, "confidence": confidence, "reason": reason, **detail}


def check_local(db: Session, entry: Entry) -> list[dict[str, Any]]:
    """Other entries in this app that look like the same bill."""
    if not entry.company_id:
        return []
    others = list(db.scalars(select(Entry).where(
        Entry.company_id == entry.company_id,
        Entry.id != entry.id,
        Entry.status.in_(("pending", "approved", "posted", "drafted")),
    )))
    if not others:
        return []

    inv = norm_invoice_no(entry.invoice_no)
    party = (entry.party_gstin or "").upper()
    out: list[dict[str, Any]] = []

    for o in others:
        same_no = bool(inv) and norm_invoice_no(o.invoice_no) == inv
        same_party = bool(party) and (o.party_gstin or "").upper() == party
        same_amt = _same_money(o.grand_total, entry.grand_total)
        where = "posted to Tally" if o.status == "posted" else f"{o.status} here"
        detail = dict(entry_id=o.id, invoice_no=o.invoice_no,
                      date=str(o.invoice_date) if o.invoice_date else None,
                      amount=str(o.grand_total) if o.grand_total is not None else None,
                      party=o.party_name_raw, status=o.status)

        # GST does not allow a supplier to reuse a number within a year.
        if same_no and same_party:
            out.append(_finding("local", "exact", 1.0,
                                f"invoice {o.invoice_no} from this supplier is already {where} (entry #{o.id})",
                                **detail))
        elif same_no and same_amt:
            out.append(_finding("local", "exact", 0.9,
                                f"same invoice number and same total as entry #{o.id}, {where}", **detail))
        elif same_party and same_amt and entry.invoice_date and o.invoice_date \
                and abs((o.invoice_date - entry.invoice_date).days) <= 7:
            out.append(_finding("local", "similar", 0.6,
                                f"same supplier and same amount as entry #{o.id} dated {o.invoice_date}",
                                **detail))
    return sorted(out, key=lambda f: -f["confidence"])


# When Tally is closed, each attempt to reach it costs ~2s on Windows before
# the connection is refused. A batch of 50 would spend minutes waiting, so the
# background check remembers a failure briefly and skips ahead. Checks a user
# asked for, and the check made just before posting, always go to Tally live.
_TALLY_DOWN_FOR = 20.0
_tally_down_until = 0.0


def check_tally(entry: Entry, company_name: str, party_ledger_name: str | None = None,
                fresh: bool = False) -> dict[str, Any]:
    """Look inside Tally itself.

    -> {"checked": bool, "findings": [...], "error": str|None, "scanned": int}
    Never raises: Tally being closed means "not checked", which the UI says
    plainly, rather than "no duplicates", which would be a lie.
    """
    import time

    from . import tally

    global _tally_down_until

    if not entry.invoice_date:
        return {"checked": False, "findings": [], "scanned": 0,
                "error": "No invoice date, so there is no period to search in Tally."}
    if not fresh and time.monotonic() < _tally_down_until:
        return {"checked": False, "findings": [], "scanned": 0,
                "error": "Tally was not reachable a moment ago"}

    start = entry.invoice_date - timedelta(days=TALLY_WINDOW_DAYS)
    end = entry.invoice_date + timedelta(days=TALLY_WINDOW_DAYS)
    try:
        vouchers = tally.vouchers_in_period(company_name, start, end)
    except tally.TallyUnavailable as exc:
        _tally_down_until = time.monotonic() + _TALLY_DOWN_FOR
        return {"checked": False, "findings": [], "scanned": 0, "error": str(exc)}
    except tally.TallyError as exc:
        return {"checked": False, "findings": [], "scanned": 0, "error": str(exc)}
    _tally_down_until = 0.0

    inv = norm_invoice_no(entry.invoice_no)
    party_norm = matching.normalize(party_ledger_name) if party_ledger_name else ""
    party_gstin = (entry.party_gstin or "").upper()
    out: list[dict[str, Any]] = []

    for v in vouchers:
        refs = [v.get("reference"), v.get("voucher_number"), *v.get("bill_refs", [])]
        same_no = bool(inv) and any(norm_invoice_no(r) == inv for r in refs if r)
        same_party = bool(
            (party_norm and matching.normalize(v.get("party")) == party_norm)
            or (party_gstin and v.get("party_gstin") == party_gstin)
        )
        same_amt = _same_money(v.get("amount"), entry.grand_total)
        near = bool(v.get("date") and abs((v["date"] - entry.invoice_date).days) <= 7)

        label = f"{v.get('voucher_type') or 'voucher'} {v.get('voucher_number') or ''}".strip()
        when = f" on {v['date']:%d-%b-%Y}" if v.get("date") else ""
        draft = " (optional/draft)" if v.get("is_optional") else ""
        detail = dict(voucher_number=v.get("voucher_number"), voucher_type=v.get("voucher_type"),
                      date=str(v["date"]) if v.get("date") else None,
                      amount=str(v["amount"]) if v.get("amount") is not None else None,
                      party=v.get("party"), reference=v.get("reference"),
                      master_id=v.get("master_id"))

        if same_no and (same_party or same_amt):
            out.append(_finding("tally", "exact", 1.0,
                                f"already entered as {label}{when}{draft}, bill no. "
                                f"{v.get('reference') or v.get('voucher_number') or '-'}", **detail))
        elif same_no and party_norm and v.get("party"):
            # Another supplier's bill that happens to carry the same number
            # ("320"), for another amount: common, and not this bill.
            continue
        elif same_no:
            out.append(_finding("tally", "reference", 0.8,
                                f"{label}{when} has the same bill number but a different party or amount - "
                                f"check which is right", **detail))
        elif same_party and same_amt:
            out.append(_finding("tally", "similar", 0.65 if near else 0.5,
                                f"{label}{when}: same party and same amount, but bill no. "
                                f"{v.get('reference') or '(none)'}", **detail))

    return {"checked": True, "findings": sorted(out, key=lambda f: -f["confidence"]),
            "scanned": len(vouchers), "error": None,
            "window": [str(start), str(end)]}


def as_validations(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render findings as review-screen checks.

    Severity is 'warn' for everything similar, 'error' only for an exact match
    -- and even that one is acknowledged, not blocked. See entries._blockers.
    """
    out = []
    for f in findings:
        exact = f["kind"] == "exact"
        where = "in Tally" if f["source"] == "tally" else "in this app"
        out.append({
            "code": "duplicate" if exact else "similar_bill",
            "severity": "error" if exact else "warn",
            "message": (f"Possible duplicate {where}: " if exact else f"Similar bill {where}: ") + f["reason"],
            "expected": None,
            "actual": f.get("voucher_number") or (f"entry #{f['entry_id']}" if f.get("entry_id") else None),
        })
    return out
