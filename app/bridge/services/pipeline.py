"""Document in, open entry out.

The rules read the bill's text or OCR and the arithmetic settles it: the
entry is on the review screen in seconds. The AI reads the bill afterwards,
in the background (services/ai_check), and the same arithmetic runs over
both readings (read_bill), so a wrong value from either gets caught in the
same place. Duplicates are checked last, against both this app's records
and the vouchers already inside Tally.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import shutil
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditLog, Company, Document, Entry, EntryLine, Ledger
from . import confidence, dedupe, gst, llm, matching, pdf_text, validate
from .parse import parse
from .vouchers import behaviour

# Tally's own group names. Line items land in an expense or income group,
# never in Sundry Creditors, which is where the party goes.
EXPENSE_GROUPS = ("Purchase Accounts", "Direct Expenses", "Indirect Expenses", "Fixed Assets")
INCOME_GROUPS = ("Sales Accounts", "Direct Incomes", "Indirect Incomes")
CASH_GROUPS = ("Bank Accounts", "Cash-in-Hand", "Bank OD A/c", "Bank OCC A/c")
TAX_GROUP = "Duties & Taxes"


def store_upload(src_name: str, data: bytes) -> tuple[Path, str]:
    """Write the upload under data/uploads/YYYY-MM/ and return (path, sha256)."""
    digest = hashlib.sha256(data).hexdigest()
    folder = settings.upload_dir / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in Path(src_name).name if c.isalnum() or c in "._- ")[:120] or "upload.pdf"
    # Hash prefix keeps two invoices with the same filename apart on disk.
    dest = folder / f"{digest[:12]}_{safe}"
    if not dest.exists():
        dest.write_bytes(data)
    return dest, digest


def adopt_file(path: Path) -> tuple[Path, str]:
    """Same, for a file the watcher found rather than an HTTP upload."""
    digest = pdf_text.sha256_file(path)
    folder = settings.upload_dir / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{digest[:12]}_{path.name}"
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest, digest


def own_gstin_map(db: Session) -> dict[str, list[Company]]:
    """Your companies by GSTIN. A list: a firm split into one Tally company
    per year has the same GSTIN in each."""
    out: dict[str, list[Company]] = {}
    for c in db.scalars(select(Company).where(Company.gstin.is_not(None)).order_by(Company.id)):
        if c.gstin:
            out.setdefault(c.gstin.upper(), []).append(c)
    return out


def choose_company(db: Session, own_gstin: Optional[str], picked_id: Optional[int],
                   by_gstin: dict[str, list[Company]]) -> tuple[Optional[Company], str]:
    """Whose books a bill goes in, and why, in words for the review screen.

    Your GSTIN on the bill decides it. Then the company chosen at upload.
    Never a guess between several companies: a bill that names none of them
    (a kachcha bill) waits for the reviewer to choose.
    """
    picked = db.get(Company, picked_id) if picked_id else None
    named = by_gstin.get((own_gstin or "").upper(), [])
    if len(named) == 1:
        c = named[0]
        if picked and picked.id != c.id:
            return c, (f"The bill is made out to {c.tally_name} (GSTIN {c.gstin}), so it went into the "
                       f"books of {c.tally_name}, not {picked.tally_name} as chosen at upload.")
        return c, f"The bill is made out to {c.tally_name} (GSTIN {c.gstin})."
    if named:
        if picked and any(c.id == picked.id for c in named):
            return picked, f"Chosen at upload. {len(named)} of your companies share the GSTIN on the bill."
        return None, f"{', '.join(c.tally_name for c in named)} all have the GSTIN on this bill ({own_gstin})."
    if picked:
        return picked, "Chosen at upload."
    active = list(db.scalars(select(Company).where(Company.is_active.is_(True)).limit(2)))
    if len(active) == 1:
        return active[0], "Your only company in Tally."
    if active:
        return None, "Nothing on the bill says which of your companies it is for."
    return None, "No Tally company is set up yet: connect Tally and discover companies in Settings."


def line_groups(voucher_type: str, parent: str | None) -> tuple[str, ...]:
    """Where the non-party side of this voucher normally posts."""
    b = behaviour(voucher_type, parent)
    if b.shape in ("settlement", "transfer"):
        return CASH_GROUPS
    if b.parent in ("Sales", "Credit Note", "Sales Order", "Delivery Note"):
        return INCOME_GROUPS
    return EXPENSE_GROUPS


# One bill read at a time, app-wide: uploads, retries and the watched folder
# all wait here, before they hold a database connection (routers.documents).
ONE_BILL = threading.Lock()

# Below these a match is a suggestion in the list, not the answer. Suppliers
# and stock items are named alike on bills and in Tally, so a low score there
# is a different thing; an expense ledger ("Certification & Audit Fees") is
# an account head, never worded like the bill's "Registration & Certification Fee".
ASSIGN_FLOOR = 0.75
LEDGER_FLOOR = 0.6
_PAISA = Decimal("0.01")
_SAY = {"taxable_total": "Taxable value", "cgst": "CGST", "sgst": "SGST", "igst": "IGST", "cess": "Cess",
        "round_off": "Round-off", "grand_total": "Total", "total_in_words": "Total in words"}


def _rates(parsed) -> set[float]:
    """The whole GST rates the bill states: beside CGST, SGST or IGST, or on
    its lines. A line's 9% on a CGST+SGST bill may be the half of 18%: both
    stay possible, and the arithmetic decides."""
    whole = {float(v) * (1 if k == "igst_rate" else 2)
             for k in ("cgst_rate", "sgst_rate", "igst_rate") if (v := parsed.get(k))}
    for r in parsed.lines:
        if f := r.get("gst_rate"):
            whole |= {x for x in (f, f * 2) if x in gst.VALID_GST_RATES}
    return whole


def settle(parsed) -> None:
    """Make the bill's amounts agree, the way a person checks one:

    1. Where the scanned text and the page image read an amount differently,
       keep the reading that makes taxable + tax + round-off equal the total,
       with CGST equal to SGST.
    2. A tax within a rupee of its rate on the taxable value is that exact
       figure: its paise were missed ("6,683 | 76" read as 6,683).
    3. What is left over, under a rupee, is the round-off.
    4. The items plus charges (packing, cartage) are the taxable value when
       the tax was worked out on both.

    5. The total itself has a second reading: the amount in words.

    Nothing changes unless the bill then adds up to the paisa, and every
    change is listed on the review screen as a reading to check."""
    image = {c["field"]: c["value"] for c in parsed.conflicts if c.get("value") is not None and c["field"] in _SAY}

    def read(k: str) -> list:
        return list(dict.fromkeys([parsed.get(k), image.get(k)] if k in image else [parsed.get(k)]))

    totals = [t for t in dict.fromkeys([*read("grand_total"), *read("total_in_words")]) if t is not None]
    for total in totals:
        if _settle_on(parsed, image, read, total):
            return
    words = parsed.get("total_in_words")
    if words is not None and parsed.get("grand_total") not in (None, words):
        parsed.conflicts.append({"field": "grand_total", "text": str(parsed.get("grand_total")), "image": str(words),
                                 "note": f"Total: the figures read {parsed.get('grand_total')}, but the amount in "
                                         f"words is {words}. Check it on the bill."})


def _settle_on(parsed, image: dict, read, total: Decimal) -> bool:
    lines = sum((r.get("amount") or Decimal(0)) for r in parsed.lines) if parsed.lines else None
    whole = _rates(parsed)
    for taxable in dict.fromkeys([*read("taxable_total"), lines]):
        if taxable is None:
            continue
        heads = {}
        for k in ("cgst", "sgst", "igst"):
            got = read(k)
            for pct in sorted(whole):
                exact = (taxable * Decimal(str(pct / (1 if k == "igst" else 2))) / 100).quantize(_PAISA)
                if exact not in got and any(v is not None and abs(v - exact) <= 1 for v in got):
                    got.append(exact)
            heads[k] = got
        for c, s, i, ce in itertools.product(heads["cgst"], heads["sgst"], heads["igst"], read("cess")):
            if c is not None and s is not None and c != s:
                continue
            base = taxable + sum(v or Decimal(0) for v in (c, s, i, ce))
            left = total - base
            for ro in dict.fromkeys([*read("round_off"), left if abs(left) < 1 else None]):
                if abs(base + (ro or 0) - total) <= _PAISA:
                    _apply(parsed, image, {"taxable_total": taxable, "cgst": c, "sgst": s, "igst": i,
                                           "cess": ce, "round_off": ro or None, "grand_total": total})
                    return True
    return False


def _apply(parsed, image: dict, chosen: dict) -> None:
    for k, v in chosen.items():
        was = parsed.get(k)
        if v == was or (not v and not was):
            continue
        if k == "grand_total" and v in (parsed.get("total_in_words"), image.get("total_in_words")):
            parsed.put(k, v, 0.8, "computed", "the amount in words, which makes the bill add up")
            note = (f"Total: the figures read {was}, but the amount in words is {v}, and it makes the bill "
                    f"add up, so it is used. Check it on the bill.")
        elif v is not None and v == image.get(k):
            parsed.put(k, v, llm.LLM_CEILING, "llm", "read off the page image: the reading that makes the bill add up")
            note = (f"{_SAY[k]}: the scanned text reads {was}, the page image {v}. {v} makes the bill add up, "
                    f"so it is used. Check it on the bill.")
        elif v is None:
            parsed.fields.pop(k, None)
            note = f"{_SAY[k]}: read as {was}, but the bill adds up without it, so it is left out. Check it on the bill."
        else:
            parsed.put(k, v, 0.8, "computed", f"worked out: read as {was}")
            why = ""
            if was is not None and v == -was:
                why = " (its sign was misread)"
            elif was is not None and k == "round_off" and v * 100 == was:
                why = " (paise, read as rupees)"
            elif was is not None and abs(v - was) <= 1:
                why = " (the paise were missed)"
            note = f"{_SAY[k]}: read as {was}, but {v} is what makes the bill add up{why}, so it is used. Check it on the bill."
        c = next((c for c in parsed.conflicts if c["field"] == k), None)
        if c is None:
            parsed.conflicts.append({"field": k, "text": str(was), "image": str(v), "note": note})
        else:
            c["note"] = note


def line_rates(parsed) -> None:
    """A line's GST % is the whole rate. On a CGST+SGST bill whose tax is 18%
    of its value, a line's 9% was the half; a line with no % of its own
    takes the bill's, when the bill has one rate."""
    taxable = parsed.get("taxable_total")
    taxes = [parsed.get(k) for k in ("cgst", "sgst", "igst")]
    bill = None
    if taxable and any(t is not None for t in taxes):
        pct = float(sum(t or 0 for t in taxes) / taxable * 100)
        near = min(gst.VALID_GST_RATES, key=lambda r: abs(r - pct))
        bill = near if abs(near - pct) < 0.6 else None  # several rates: none for the whole bill
    for r in parsed.lines:
        f = r.get("gst_rate")
        if f is None:
            r["gst_rate"] = bill
        elif bill is not None and parsed.get("cgst") and abs(f * 2 - bill) < 0.01:
            r["gst_rate"] = bill
        elif f not in gst.VALID_GST_RATES and f * 2 in gst.VALID_GST_RATES:
            r["gst_rate"] = f * 2


def line_quantities(parsed) -> None:
    """A quantity one digit off what its amount and rate give was misread:
    21.48 CFT at 560 is the 12,029 on the bill, not the 91.48 read. The
    amounts add up with the bill and the rate is as printed, so the
    quantity is the one digit that slipped. Used, and noted for the reviewer."""
    for n, r in enumerate(parsed.lines, start=1):
        qty, rate, amount = r.get("quantity"), r.get("rate"), r.get("amount")
        if not (qty and rate and amount) or abs(qty * rate - amount) <= 1:
            continue
        exact = (amount / rate).quantize(_PAISA)
        was, now = f"{qty:.2f}", f"{exact:.2f}"
        if abs(exact * rate - amount) <= 1 and len(was) == len(now) and sum(x != y for x, y in zip(was, now)) == 1:
            r["quantity"], r["arith_ok"] = exact, True
            parsed.conflicts.append({"field": "lines", "text": was, "image": now,
                                     "note": f"Line {n}: the quantity reads {was}, but {amount} at {rate} is {now}, "
                                             f"one digit apart, so {now} is used. Check it on the bill."})


def match_party(db: Session, company_id: int, name: str | None, gstin: str | None,
                group: str | None) -> dict[str, Any]:
    """The party's ledger: under its usual group first, then anywhere - the
    party may sit under a non-standard group, and declaring it new would
    offer to create a duplicate. A guess below ASSIGN_FLOOR stays a
    suggestion: posting a bill to the wrong supplier is worse than asking."""
    party = matching.match_ledger(db, company_id, name, gstin, parent_like=group or None)
    if not party["ledger_id"]:
        party = matching.match_ledger(db, company_id, name, gstin)
    if party["ledger_id"] and party["confidence"] < ASSIGN_FLOOR:
        party = {**party, "ledger_id": None, "confidence": 0.0,
                 "reason": f"{party['reason']} - not sure enough to pick it: choose the ledger, or create it"}
    return party


def read_bill(text: str, own_gstins: set[str], ai: bool = False,
              images: list[bytes] | None = None) -> tuple[Any, Optional[list[str]]]:
    """The bill as the rules read its text, with the AI's reading merged in
    when asked, and the arithmetic settled over both. Returns the reading
    and the fields the AI filled: None when it gave no answer."""
    parsed = parse(text, own_gstins=own_gstins)
    filled: Optional[list[str]] = []
    if ai:
        try:
            filled = llm.merge_into(parsed, text, images=images, own_gstins=own_gstins)
        except Exception:
            filled = None  # the model is a convenience, never a dependency
    settle(parsed)
    line_rates(parsed)
    line_quantities(parsed)
    return parsed, filled


def ai_mode(parsed, ocr_used: bool) -> Optional[str]:
    """How the AI is to check a bill, if at all. "image": it reads the page
    itself when the text is unreliable - a scan (handwriting and Hindi defeat
    the OCR), or a PDF whose text the rules could not use at all (old Hindi
    fonts such as Kruti Dev extract as Latin gibberish). "text": it fills in
    what the rules did not find in the text."""
    caps = llm.capabilities() if settings.llm_enabled else frozenset()
    if not caps:
        return None  # off, or not running: the rules' reading is the answer
    key_gaps = sum(parsed.get(k) is None for k in ("invoice_no", "invoice_date", "party_name", "grand_total"))
    if (ocr_used or key_gaps >= 2) and "vision" in caps:
        return "image"
    return "text" if llm.has_gaps(parsed) else None


# The entry's columns read off the bill, and the reading's name for one
# where it differs (field_meta is keyed by the reading's names).
READ = ("invoice_no", "invoice_date", "due_date", "party_name_raw", "party_gstin", "place_of_supply",
        "supply_type", "taxable_total", "cgst", "sgst", "igst", "cess", "round_off", "grand_total")
READ_AS = {"party_name_raw": "party_name"}


def header(parsed, company: Optional[Company], b) -> dict[str, Any]:
    """An entry's own values from a reading of its bill."""
    h = {k: parsed.get(READ_AS.get(k, k)) for k in READ}
    h["own_gstin"] = parsed.get("own_gstin") or (company.gstin if company else None)
    h["currency"] = parsed.get("currency") or "INR"
    if b.shape != "invoice":
        # A payment or receipt posts the gross; there is no tax to split out.
        h.update(taxable_total=None, cgst=None, sgst=None, igst=None, cess=None)
    return h


def line_rows(parsed, b, grand_total: Optional[Decimal]) -> list[dict[str, Any]]:
    if b.shape in ("settlement", "transfer"):
        # One line: the bank or cash account the money moved through.
        return [{"line_no": 0, "description_raw": "Bank / cash account", "amount": grand_total,
                 "quantity": None, "rate": None, "hsn_sac": None, "uom": None, "arith_ok": True}]
    return parsed.lines


def set_lines(db: Session, entry: Entry, rows: list[dict[str, Any]], company: Optional[Company]) -> None:
    """The entry's lines become these rows, each matched to a stock item or
    ledger. A line it has already is rewritten in place and keeps its id:
    unsaved edits on the review screen point at it."""
    b = behaviour(entry.voucher_type, entry.voucher_parent)
    groups = line_groups(entry.voucher_type, entry.voucher_parent)
    supplier = db.get(Ledger, entry.party_ledger_id).tally_name if entry.party_ledger_id else None
    have = list(entry.lines)
    for i, row in enumerate(rows):
        line = have[i] if i < len(have) else EntryLine()
        if i >= len(have):
            entry.lines.append(line)
        line.line_no = row.get("line_no", 0)
        for k in ("description_raw", "hsn_sac", "quantity", "uom", "rate", "amount", "gst_rate"):
            setattr(line, k, row.get(k))
        line.stock_item_id = line.ledger_id = None
        line.match_confidence, line.match_reason, line.candidates = 0.0, None, None
        if company:
            # A charge (packing, cartage) posts to a ledger, never to a stock item.
            _match_line(db, company.id, line, groups, allow_stock=(b.shape == "invoice" and not row.get("charge")),
                        party=supplier)
    for line in have[len(rows):]:
        entry.lines.remove(line)  # delete-orphan: gone at the flush


# One change to an entry at a time: yours on the review screen, and the AI's
# check saving into the same entry in the background (services/ai_check),
# would each save over the other's field_meta.
_entry_locks: dict[int, threading.Lock] = {}


def entry_lock(entry_id: int) -> threading.Lock:
    return _entry_locks.setdefault(entry_id, threading.Lock())


def stored_conflicts(parsed) -> dict[str, Any]:
    """Where the readings of a bill differ, kept for the checks run again
    after an edit (entries._recheck); without the values, which are for
    settle() alone."""
    return {"value": [{k: v for k, v in c.items() if k != "value"} for c in parsed.conflicts],
            "confidence": 1.0, "source": "computed", "evidence": None}


def process_document(db: Session, doc: Document, company_id: int | None = None,
                     use_llm: bool = True, check_tally: bool = True) -> Entry:
    doc.status = "processing"
    db.commit()

    if not Path(doc.stored_path).exists():
        doc.status = "failed"
        doc.error = (f"The file is gone from the app's data folder: it was deleted or moved. "
                     f"Upload {doc.filename} again to read it.")
        db.commit()
        raise FileNotFoundError(doc.error)
    try:
        result = pdf_text.extract(Path(doc.stored_path))
    except Exception as exc:
        doc.status, doc.error = "failed", f"Could not read the file: {exc}"
        db.commit()
        raise

    doc.pages = len(result.pages)
    doc.has_text_layer = result.has_text_layer
    doc.ocr_used = result.ocr_used
    doc.extracted_text = result.text[:400_000]

    companies = own_gstin_map(db)
    # The rules' reading of the text or the OCR, now. The AI reads the bill
    # afterwards, in the background (services/ai_check.py), and the bill can
    # be reviewed meanwhile: a scan used to wait here a minute for it.
    parsed, _ = read_bill(result.text, set(companies))
    ai = ai_mode(parsed, result.ocr_used) if use_llm else None

    company, company_reason = choose_company(db, parsed.get("own_gstin"), company_id, companies)

    # The user's choice at upload is authoritative; inference is the fallback.
    vtype = doc.requested_voucher_type or parsed.get("voucher_type") or "Purchase"
    vparent = doc.requested_voucher_parent or _parent_from_company(company, vtype)
    b = behaviour(vtype, vparent)

    doc.company_id = company.id if company else None
    doc.status = "extracted"

    entry = Entry(document_id=doc.id, company_id=company.id if company else None, voucher_type=vtype,
                  voucher_parent=vparent or b.parent, **header(parsed, company, b))

    # --- party ledger -----------------------------------------------------
    party = {"ledger_id": None, "confidence": 0.0, "reason": "choose the company first", "candidates": []}
    if company:
        party = match_party(db, company.id, entry.party_name_raw, entry.party_gstin, b.party_group)

    entry.party_ledger_id = party["ledger_id"]
    entry.party_confidence = party["confidence"]
    entry.party_match_reason = party["reason"]
    entry.party_candidates = party["candidates"]

    # --- inside Tally: before this entry takes the database's write lock ----
    # The search runs over the network and can take a while on real books;
    # holding the lock meanwhile made every save in the app fail with
    # "database is locked".
    tally_check: dict[str, Any] = {
        "checked": False, "findings": [], "scanned": 0,
        "error": "no company is chosen for this bill yet" if company is None else "skipped",
    }
    if check_tally and company:
        led = db.get(Ledger, entry.party_ledger_id) if entry.party_ledger_id else None
        tally_check = dedupe.check_tally(entry, company.tally_name, led.tally_name if led else None)

    db.add(entry)
    db.flush()

    # --- lines ------------------------------------------------------------
    rows = line_rows(parsed, b, entry.grand_total)
    set_lines(db, entry, rows, company)

    # --- checks -----------------------------------------------------------
    own_state = (company.state_code if company else None) or ((entry.own_gstin or "")[:2] or None)
    checks = validate.run(parsed, own_state=own_state, invoice_like=(b.shape == "invoice"))

    field_meta = {k: v.as_dict() for k, v in parsed.fields.items()}
    # Below 1: the review screen asks you to check it (your GSTIN on the bill
    # overrode the company chosen at upload).
    overrode = bool(company and company_id and company.id != company_id)
    field_meta["_company"] = {"value": company_reason, "confidence": 0.5 if overrode else 1.0,
                              "source": "computed", "evidence": None}
    field_meta["_conflicts"] = stored_conflicts(parsed)
    if ai:
        # The callers queue the AI's check (services/ai_check).
        field_meta["_ai"] = {"value": {"state": "waiting", "mode": ai}, "confidence": 1.0,
                             "source": "computed", "evidence": None}
    entry.field_meta = field_meta
    db.flush()

    # --- duplicates: here, and inside Tally (searched above) ----------------
    findings = dedupe.check_local(db, entry) + tally_check["findings"]
    apply_duplicates(entry, findings, tally_check)
    checks += dedupe.as_validations(findings)

    entry.validations = checks
    score, breakdown = confidence.score(entry.field_meta, party["confidence"], checks, rows,
                                        result.ocr_confidence)
    entry.confidence = score
    entry.field_meta = {**entry.field_meta,
                        "_score": {"value": breakdown, "confidence": score, "source": "computed",
                                   "evidence": None}}

    # No narration of its own: the books leave it blank (19 of 19 purchases,
    # Sept 2026). One typed on the review screen is posted.
    db.add(AuditLog(entry_id=entry.id, action="extracted", detail={
        "document_id": doc.id, "confidence": entry.confidence, "voucher_type": vtype,
        "ocr": doc.ocr_used, "ai": ai,
        "tally_duplicate_check": tally_check.get("checked"),
        "errors": [c["code"] for c in checks if c["severity"] == "error"],
    }))
    db.commit()
    db.refresh(entry)
    return entry


def apply_duplicates(entry: Entry, findings: list[dict[str, Any]], tally_check: dict[str, Any]) -> None:
    """Record duplicate findings on the entry. Never changes its status to a
    blocked state -- the user decides."""
    exact = next((f for f in findings if f["kind"] == "exact"), None)
    entry.duplicate_of_id = exact.get("entry_id") if exact and exact["source"] == "local" else None
    entry.duplicate_reason = exact["reason"] if exact else None
    meta = dict(entry.field_meta or {})
    meta["_duplicates"] = {
        "value": {
            "findings": findings,
            "tally_checked": tally_check.get("checked", False),
            "tally_error": tally_check.get("error"),
            "tally_scanned": tally_check.get("scanned", 0),
            "tally_window": tally_check.get("window"),
        },
        "confidence": 1.0, "source": "computed", "evidence": None,
    }
    entry.field_meta = meta


def _parent_from_company(company: Optional[Company], vtype: str) -> Optional[str]:
    for vt in (company.voucher_types or []) if company else []:
        if vt.get("name") == vtype:
            return vt.get("parent")
    return None


def _match_line(db: Session, company_id: int, line: EntryLine, groups: tuple[str, ...],
                allow_stock: bool, party: str | None = None) -> None:
    stock = (matching.match_stock_item(db, company_id, line.description_raw, line.hsn_sac, uom=line.uom,
                                       party=party)
             if allow_stock else {"stock_item_id": None, "confidence": 0.0, "candidates": [], "reason": ""})
    led = match_group_ledger(db, company_id, line.description_raw, groups)
    # Prefer whichever is more sure; a service bill has no stock item and a
    # goods bill usually has no matching expense ledger. Under ASSIGN_FLOOR
    # both are only suggestions: cow feed once matched a stock item "JAR" at 63%.
    if stock["confidence"] >= max(led["confidence"], ASSIGN_FLOOR):
        line.stock_item_id, line.ledger_id = stock["stock_item_id"], None
        line.match_confidence, line.match_reason = stock["confidence"], stock["reason"]
        line.candidates = stock["candidates"]
    elif led["ledger_id"] and led["confidence"] >= LEDGER_FLOOR:
        line.ledger_id, line.stock_item_id = led["ledger_id"], None
        line.match_confidence, line.match_reason = led["confidence"], led["reason"]
        line.candidates = led["candidates"]
    else:
        line.match_reason = "no matching ledger - pick one"
        line.candidates = (stock["candidates"] or []) + (led["candidates"] or [])


def match_group_ledger(db: Session, company_id: int, text: str | None,
                       groups: tuple[str, ...]) -> dict[str, Any]:
    best = {"ledger_id": None, "confidence": 0.0, "reason": "no ledger matched", "candidates": []}
    if not text:
        return best
    # An alias beats every group guess: the reviewer already answered this.
    # For a bank line that means the bank you picked last time becomes the
    # default, because the placeholder description never changes.
    al = matching.match_ledger(db, company_id, text, None)
    if al["confidence"] >= 0.99:
        return al

    if groups == CASH_GROUPS:
        # The description on a bank line is our own placeholder, so fuzzy
        # matching it means nothing. One bank: use it. Several: ask.
        banks = list(db.scalars(select(Ledger).where(
            Ledger.company_id == company_id, Ledger.parent.in_(CASH_GROUPS))))
        if len(banks) == 1:
            return {"ledger_id": banks[0].id, "confidence": 0.8,
                    "reason": "your only bank/cash ledger", "candidates": []}
        best["reason"] = f"{len(banks)} bank/cash ledgers - pick the one used" if banks \
            else "no bank or cash ledger in this company"
        best["candidates"] = [{"ledger_id": b.id, "name": b.tally_name, "score": 0.5} for b in banks[:8]]
        return best

    for grp in groups:
        r = matching.match_ledger(db, company_id, text, None, parent_like=grp)
        if r["confidence"] > best["confidence"]:
            best = r
    return best


def find_tax_ledger(db: Session, company_id: int, kind: str, rate: float | None = None) -> Optional[Ledger]:
    """Locate 'Input IGST' / 'IGST @ 18%' / 'Output CGST' under Duties & Taxes,
    and the round-off ledger ('Round Off', 'Rounded Off') wherever it is:
    usually under Indirect Expenses, never under Duties & Taxes."""
    everything = select(Ledger).where(Ledger.company_id == company_id)
    cands = [] if kind == "round" else list(db.scalars(everything.where(Ledger.parent.ilike(f"%{TAX_GROUP}%"))))
    if not cands:
        cands = list(db.scalars(everything))
    k = kind.lower()
    scored: list[tuple[float, Ledger]] = []
    for led in cands:
        n = led.tally_name.lower()
        # A word start for round-off, so "Ground Rent" is not taken for it.
        if not (re.search(r"\bround", n) if k == "round" else k in n):
            continue
        s = 1.0
        if rate and f"{rate:g}" in n:
            s += 0.5
        # Prefer the plainest name when several match.
        s -= len(n) / 500.0
        scored.append((s, led))
    if not scored:
        return None
    return max(scored, key=lambda t: t[0])[1]
