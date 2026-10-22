from __future__ import annotations

import functools
import html
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditLog, Bought, Company, Document, Entry, EntryLine, Ledger, StockItem, utcnow
from ..schemas import (
    ApproveRequest, ApproveResult, CreateMasterRequest, EntryDetail, EntryLineOut, EntrySummary,
    EntryUpdate,
)
from ..services import ai_check, bill_links, confidence, dedupe, gst, matching, pipeline, tally, validate, voucher_xml
from ..services.parse import Field, iso_currency
from ..services.vouchers import behaviour
from .documents import BILLS

router = APIRouter(prefix="/api/entries", tags=["entries"])

EXPORT_DIR_NAME = "exports"
IN_TALLY = ("posted", "drafted")
NO_COMPANY = ("Choose which company's books this bill goes in (Company, at the top of the entry). "
              "No company in the list? Connect Tally and discover companies in Settings.")


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def _counts(e: Entry) -> tuple[int, int]:
    v = e.validations or []
    return (sum(1 for x in v if x.get("severity") == "error"),
            sum(1 for x in v if x.get("severity") == "warn"))


def _ai_read(e: Entry) -> bool:
    """Anything the AI reader supplied, or read differently from the OCR,
    needs a person to look at this entry by itself: never bulk-approved.
    Nor does one the AI is still checking."""
    return bool((e.field_meta or {}).get("_llm_filled")) or ai_check.pending(e) or \
        any(v.get("code") == "reading_differs" for v in (e.validations or []))


def _summary(e: Entry) -> EntrySummary:
    err, warn = _counts(e)
    return EntrySummary(
        id=e.id, document_id=e.document_id, company_id=e.company_id, voucher_type=e.voucher_type,
        invoice_no=e.invoice_no, invoice_date=e.invoice_date, party_name_raw=e.party_name_raw,
        party_gstin=e.party_gstin, party_ledger_id=e.party_ledger_id,
        party_ledger_name=e.party_ledger.tally_name if e.party_ledger else None,
        party_confidence=e.party_confidence, grand_total=e.grand_total, currency=iso_currency(e.currency),
        confidence=e.confidence, confidence_label=confidence.label(e.confidence), status=e.status,
        error_count=err, warning_count=warn, duplicate_of_id=e.duplicate_of_id,
        filename=e.document.filename if e.document else None, created_at=e.created_at,
        ai_state=ai_check.state(e).get("state"),
    )


def _line_out(db: Session, l: EntryLine) -> EntryLineOut:
    name = None
    if l.stock_item_id:
        it = db.get(StockItem, l.stock_item_id)
        name = it.tally_name if it else None
    elif l.ledger_id:
        led = db.get(Ledger, l.ledger_id)
        name = led.tally_name if led else None
    return EntryLineOut(
        id=l.id, line_no=l.line_no, description_raw=l.description_raw, description=l.description, hsn_sac=l.hsn_sac,
        quantity=l.quantity, uom=l.uom, rate=l.rate, amount=l.amount, gst_rate=l.gst_rate,
        stock_item_id=l.stock_item_id, ledger_id=l.ledger_id,
        match_confidence=l.match_confidence, match_reason=l.match_reason,
        candidates=l.candidates, matched_name=name,
    )


def _detail(db: Session, e: Entry) -> EntryDetail:
    return EntryDetail(
        **_summary(e).model_dump(),
        reference=e.reference, due_date=e.due_date, own_gstin=e.own_gstin, place_of_supply=e.place_of_supply,
        place_of_supply_name=gst.state_name(e.place_of_supply), supply_type=e.supply_type,
        taxable_total=e.taxable_total, cgst=e.cgst, sgst=e.sgst, igst=e.igst, cess=e.cess,
        round_off=e.round_off, narration=e.narration, party_match_reason=e.party_match_reason,
        party_candidates=e.party_candidates,
        field_meta={**(e.field_meta or {}),
                    "_voucher": {"value": {"type": e.voucher_type, "parent": e.voucher_parent,
                                           "shape": behaviour(e.voucher_type, e.voucher_parent).shape},
                                 "confidence": 1.0, "source": "user", "evidence": None}},
        validations=e.validations, duplicate_reason=e.duplicate_reason,
        tally_voucher_id=e.tally_voucher_id, export_path=e.export_path, posted_at=e.posted_at,
        lines=[_line_out(db, l) for l in e.lines],
        document=e.document,
    )


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

@router.get("", response_model=list[EntrySummary])
def list_entries(
    status: str | None = None,
    company_id: int | None = None,
    q: str | None = None,
    sort: str = Query("confidence", pattern="^(confidence|date|created|upload)$"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> Any:
    """Default sort is confidence ascending: the entries that need a human
    come first, and the clean ones fall to the bottom where they can be
    approved in a batch. "upload" keeps a batch in the order it was given
    (page order), so the list stays put while the rest is still being read."""
    stmt = select(Entry)
    if status:
        stmt = stmt.where(Entry.status.in_(status.split(",")))
    if company_id:
        stmt = stmt.where(Entry.company_id == company_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Entry.invoice_no.ilike(like) | Entry.party_name_raw.ilike(like) | Entry.party_gstin.ilike(like)
        )
    order = {"confidence": Entry.confidence.asc(), "date": desc(Entry.invoice_date),
             "created": desc(Entry.created_at), "upload": Entry.document_id.asc()}[sort]
    rows = list(db.scalars(stmt.order_by(order, desc(Entry.id)).offset(offset).limit(limit)))
    return [_summary(e) for e in rows]


@router.get("/stats")
def stats(company_id: int | None = None, db: Session = Depends(get_db)) -> Any:
    def count(*statuses: str) -> int:
        stmt = select(func.count(Entry.id)).where(Entry.status.in_(statuses))
        if company_id:
            stmt = stmt.where(Entry.company_id == company_id)
        return db.scalar(stmt) or 0

    pending = select(Entry).where(Entry.status == "pending")
    if company_id:
        pending = pending.where(Entry.company_id == company_id)
    rows = list(db.scalars(pending))
    clean = [e for e in rows
             if e.confidence >= settings.auto_clean_confidence and not _counts(e)[0] and not _ai_read(e)]

    return {
        "pending": len(rows), "clean": len(clean), "needs_review": len(rows) - len(clean),
        "approved": count("approved"), "drafted": count("drafted"), "posted": count("posted"),
        "rejected": count("rejected"),
        "clean_ids": [e.id for e in clean],
        "clean_value": str(sum((e.grand_total or Decimal(0)) for e in clean)),
        "threshold": settings.auto_clean_confidence,
        "ai_checking": sum(1 for e in rows if ai_check.pending(e)),
        # Bills still being read, and ones that could not be: the review
        # screen shows both, so a batch can be reviewed while it is read.
        "reading": db.scalar(select(func.count(Document.id))
                             .where(BILLS, Document.status.in_(("queued", "processing")))) or 0,
        "failed": [{"id": d.id, "filename": d.filename, "error": d.error} for d in db.scalars(
            select(Document).where(BILLS, Document.status == "failed").order_by(desc(Document.id)).limit(20))],
    }


@router.get("/{entry_id}", response_model=EntryDetail)
def get_entry(entry_id: int, db: Session = Depends(get_db)) -> Any:
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    return _detail(db, e)


@router.get("/{entry_id}/balance")
def party_balance(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """The party's balance in Tally on the bill's date, shown under the party
    as Tally's voucher screen does. Nothing when Tally does not answer."""
    e = db.get(Entry, entry_id)
    company = db.get(Company, e.company_id) if e and e.company_id else None
    if not company or not e.party_ledger:
        return {"balance": None}
    try:
        b = tally.balance(company.tally_name, e.party_ledger.tally_name, e.invoice_date or utcnow().date())
    except tally.TallyError as exc:
        return {"balance": None, "error": str(exc)}
    return {"balance": None if b is None else str(abs(b)), "side": "Dr" if b is not None and b < 0 else "Cr"}


@router.get("/{entry_id}/audit")
def audit(entry_id: int, db: Session = Depends(get_db)) -> Any:
    return [
        {"action": a.action, "detail": a.detail, "actor": a.actor, "at": a.at}
        for a in db.scalars(select(AuditLog).where(AuditLog.entry_id == entry_id).order_by(AuditLog.id))
    ]


# --------------------------------------------------------------------------
# edit
# --------------------------------------------------------------------------

def _one_hand(fn):
    """One change to an entry at a time: the AI's check saves into the same
    entry in the background (services/ai_check)."""
    @functools.wraps(fn)
    def one(entry_id: int, *args: Any, **kwargs: Any) -> Any:
        with pipeline.entry_lock(entry_id):
            return fn(entry_id, *args, **kwargs)
    return one


@router.put("/{entry_id}", response_model=EntryDetail)
@_one_hand
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)) -> Any:
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.status in IN_TALLY:
        raise HTTPException(409, "This entry is already in Tally and cannot be edited here. "
                                 "Alter or delete the voucher in Tally instead.")

    before = {k: str(getattr(e, k)) for k in
              ("company_id", "voucher_type", "invoice_no", "invoice_date", "party_ledger_id", "grand_total")}
    data = payload.model_dump(exclude_unset=True, exclude={"lines", "learn", "tax_ledgers"})

    # Into another company's books first, so the rest of this save is checked
    # against the ledgers of the company the bill now belongs to.
    new_company = data.pop("company_id", e.company_id)
    if new_company != e.company_id:
        company = db.get(Company, new_company) if new_company else None
        if company is None:
            raise HTTPException(404, "Company not found")
        _move_to(db, e, company)
    _check_owned(db, e, data.get("party_ledger_id"), payload.lines, payload.tax_ledgers)
    if payload.tax_ledgers is not None:
        _choose_tax_ledgers(e, payload.tax_ledgers)

    # Teach the matcher from the correction before overwriting the raw text.
    if payload.learn and e.company_id and data.get("party_ledger_id"):
        led = db.get(Ledger, data["party_ledger_id"])
        raw = data.get("party_name_raw") or e.party_name_raw
        if led and raw and data["party_ledger_id"] != e.party_ledger_id:
            matching.remember(db, e.company_id, "ledger", raw, led.tally_name)

    # What you change is yours: the AI's check never overwrites it, and the
    # warnings about it are dealt with (services/ai_check).
    edited = {pipeline.READ_AS.get(k, k): v for k, v in data.items() if k in pipeline.READ and getattr(e, k) != v}
    meta = dict(e.field_meta or {})
    meta.update({k: Field(v, 1.0, "user").as_dict() for k, v in edited.items()})
    gone = set(edited) | ({"lines"} if payload.lines else set())
    was = (meta.get("_conflicts") or {}).get("value") or []
    meta["_conflicts"] = {"value": [c for c in was if c["field"] not in gone], "confidence": 1.0,
                          "source": "computed", "evidence": None}
    if payload.lines:
        meta["_lines_user"] = {"value": True, "confidence": 1.0, "source": "user", "evidence": None}
    e.field_meta = meta

    type_changed = "voucher_type" in data and data["voucher_type"] != e.voucher_type
    for key, value in data.items():
        setattr(e, key, value)

    if type_changed:
        company = db.get(Company, e.company_id) if e.company_id else None
        e.voucher_parent = pipeline._parent_from_company(company, e.voucher_type) \
            or behaviour(e.voucher_type).parent
        if behaviour(e.voucher_type, e.voucher_parent).shape != "invoice":
            e.cgst = e.sgst = e.igst = e.cess = None

    if "party_ledger_id" in data:
        e.party_confidence = 1.0
        e.party_match_reason = "set by you"

    if payload.lines is not None:
        _apply_lines(db, e, payload.lines, learn=payload.learn)

    _recheck(db, e)
    db.add(AuditLog(entry_id=e.id, action="edited", detail={
        "before": before,
        "after": {k: str(getattr(e, k)) for k in before},
        "fields": list(data.keys()) + (["tax_ledgers"] if payload.tax_ledgers is not None else []),
    }))
    db.commit()
    db.refresh(e)
    return _detail(db, e)


TAX_ROWS = ("cgst", "sgst", "igst", "cess", "round")


def _choose_tax_ledgers(e: Entry, picks: dict[str, int | None]) -> None:
    """Keep the ledgers picked for this bill's tax and round-off rows."""
    unknown = sorted(set(picks) - set(TAX_ROWS))
    if unknown:
        raise HTTPException(400, f"No such row: {', '.join(unknown)}. The rows are {', '.join(TAX_ROWS)}.")
    meta = dict(e.field_meta or {})
    chosen = {**(((meta.get("_ledgers") or {}).get("value")) or {}), **picks}
    meta["_ledgers"] = {"value": {k: v for k, v in chosen.items() if v},
                        "confidence": 1.0, "source": "user", "evidence": None}
    e.field_meta = meta


def _chosen_ledgers(db: Session, e: Entry) -> dict[str, Ledger]:
    """kind -> the ledger picked for that tax or round-off row, if any."""
    ids = ((e.field_meta or {}).get("_ledgers") or {}).get("value") or {}
    leds = {kind: db.get(Ledger, lid) for kind, lid in ids.items() if lid}
    return {kind: led for kind, led in leds.items() if led and led.company_id == e.company_id}


def _check_owned(db: Session, e: Entry, party_ledger_id: int | None, lines: list[Any] | None,
                 tax_ledgers: dict[str, int | None] | None = None) -> None:
    """A ledger from another company's books would post under a name this
    company may not have. The screen never sends one; refuse it anyway."""
    for lid in filter(None, [party_ledger_id, *(l.ledger_id for l in lines or []),
                             *(tax_ledgers or {}).values()]):
        led = db.get(Ledger, lid)
        if led is None or led.company_id != e.company_id:
            raise HTTPException(400, "That ledger is not in this bill's company. Reload the entry and pick again.")
    for sid in filter(None, (l.stock_item_id for l in lines or [])):
        item = db.get(StockItem, sid)
        if item is None or item.company_id != e.company_id:
            raise HTTPException(400, "That stock item is not in this bill's company. Reload the entry and pick again.")


def _move_to(db: Session, e: Entry, company: Company, why: str | None = None) -> None:
    """Put the bill in another company's books. Every ledger it was matched
    to, even by hand, is in the old company's books: match again in the new
    one's. why: the AI's reading moved it, not you."""
    meta = dict(e.field_meta or {})
    read = meta.get("own_gstin") or {}
    on_bill = read.get("value") if read.get("source") in ("regex", "llm") else None  # read off the bill
    note = "Chosen by you."
    other_gstin = bool(on_bill and on_bill.upper() != (company.gstin or "").upper())
    if other_gstin:
        note += (f" Check it: the bill is made out to GSTIN {on_bill}, and the GSTIN of {company.tally_name} "
                 f"is {company.gstin or 'not set in Tally'}.")

    e.company_id = company.id
    if not on_bill:
        e.own_gstin = company.gstin
    kinds = {v.get("name") for v in company.voucher_types or []}
    if kinds and e.voucher_type not in kinds:
        # A custom type of the old company: use the built-in type it works
        # like, which every company has.
        note += f" {company.tally_name} has no '{e.voucher_type}' voucher type, so it is a {e.voucher_parent} now."
        e.voucher_type = e.voucher_parent or e.voucher_type
    e.voucher_parent = pipeline._parent_from_company(company, e.voucher_type) or behaviour(e.voucher_type).parent

    e.party_ledger_id, e.party_confidence, e.party_match_reason, e.party_candidates = None, 0.0, None, None
    for line in e.lines:
        line.ledger_id = line.stock_item_id = None
        line.match_confidence, line.match_reason, line.candidates = 0.0, None, None
    meta.pop("_ledgers", None)  # tax and round-off ledgers picked in the old books
    meta["_company"] = {"value": why or note, "confidence": 0.5 if (why or other_gstin) else 1.0,
                        "source": "computed" if why else "user", "evidence": None}
    e.field_meta = meta
    _rematch(db, e)


def _rematch(db: Session, e: Entry) -> None:
    """Match the party and the lines again, in the entry's company. What you
    set by hand stays."""
    b = behaviour(e.voucher_type, e.voucher_parent)
    party = pipeline.match_party(db, e.company_id, e.party_name_raw, e.party_gstin, b.party_group)
    if e.party_confidence < 1.0 and (party["ledger_id"] or not e.party_ledger_id):
        e.party_ledger_id = party["ledger_id"]
        e.party_confidence = party["confidence"]
        e.party_match_reason = party["reason"]
        e.party_candidates = party["candidates"]

    groups = pipeline.line_groups(e.voucher_type, e.voucher_parent)
    supplier = db.get(Ledger, e.party_ledger_id).tally_name if e.party_ledger_id else None
    for line in e.lines:
        if line.match_confidence >= 1.0:
            continue  # set by hand; leave it alone
        pipeline._match_line(db, e.company_id, line, groups, allow_stock=(b.shape == "invoice"),
                             party=supplier)


def _apply_lines(db: Session, e: Entry, updates: list[Any], learn: bool) -> None:
    by_id = {l.id: l for l in e.lines}
    for up in updates:
        data = up.model_dump(exclude_unset=True)
        line = by_id.get(data.get("id")) if data.get("id") else None
        if data.get("id") and line is None and not data.get("delete"):
            raise HTTPException(409, "The AI changed this bill's lines while you were editing. "
                                     "Reload the bill and make your change again.")
        if data.pop("delete", False):
            if line is not None:
                e.lines.remove(line)  # delete-orphan: gone at the flush
            continue
        if line is None:
            # On the entry at once, so the checks after this save count it.
            line = EntryLine(line_no=data.get("line_no", max((l.line_no for l in e.lines), default=-1) + 1))
            e.lines.append(line)
            db.flush()

        # A ledger picked for a stock item's row is the purchase (or sales)
        # ledger behind the item, and the item stays. To post the line to a
        # ledger instead of an item, stock_item_id: null comes with it.
        item_id = data["stock_item_id"] if "stock_item_id" in data else line.stock_item_id
        if learn and e.company_id:
            raw = data.get("description_raw") or line.description_raw
            if raw and data.get("stock_item_id") and data["stock_item_id"] != line.stock_item_id:
                it = db.get(StockItem, data["stock_item_id"])
                if it:
                    matching.remember(db, e.company_id, "stock", raw, it.tally_name)
            if data.get("ledger_id") and data["ledger_id"] != line.ledger_id:
                led = db.get(Ledger, data["ledger_id"])
                item = db.get(StockItem, item_id) if item_id else None
                if led and item:
                    # The item's next bill posts to the same ledger.
                    matching.remember(db, e.company_id, "item_ledger", item.tally_name, led.tally_name)
                elif led and raw:
                    matching.remember(db, e.company_id, "ledger", raw, led.tally_name)

        for key, value in data.items():
            if key != "id":
                setattr(line, key, value)
        if data.get("stock_item_id") or (data.get("ledger_id") and not item_id):
            line.match_confidence = 1.0
            line.match_reason = "set by you"
        # A newly picked stock item posts to its usual ledger unless one came with it.
        if data.get("stock_item_id") and "ledger_id" not in data:
            line.ledger_id = None
    db.flush()


def _recheck(db: Session, e: Entry) -> None:
    """Re-run validation, local duplicates and scoring after an edit.

    Uses the entry's current values, not the original extraction, so a
    correction that fixes a tax split visibly clears the error that flagged it.
    The Tally duplicate check is not repeated on every keystroke-save; it runs
    on demand and again, always, at the moment of posting.
    """
    from ..services.parse import Field, ParsedInvoice

    p = ParsedInvoice()
    meta = e.field_meta or {}
    p.conflicts = list((meta.get("_conflicts") or {}).get("value") or [])
    for key in ("invoice_no", "invoice_date", "due_date", "party_gstin", "own_gstin",
                "place_of_supply", "taxable_total", "cgst", "sgst", "igst", "cess",
                "round_off", "grand_total"):
        val = getattr(e, key, None)
        if val is not None:
            old = meta.get(key) or {}
            src = old.get("source", "user")
            conf = float(old.get("confidence", 1.0)) if src != "user" else 1.0
            p.fields[key] = Field(val, conf, src, old.get("evidence"))
    for key in ("cgst_rate", "sgst_rate", "igst_rate"):
        if key in meta and meta[key].get("value") is not None:
            p.fields[key] = Field(float(meta[key]["value"]), meta[key].get("confidence", 0.9), "regex")
    p.lines = [{
        "line_no": l.line_no, "description_raw": l.description_raw, "hsn_sac": l.hsn_sac,
        "quantity": l.quantity, "rate": l.rate, "amount": l.amount,
        "arith_ok": bool(l.quantity and l.rate and l.amount
                         and gst.close(l.quantity * l.rate, l.amount, "1.00")),
    } for l in e.lines]

    company = db.get(Company, e.company_id) if e.company_id else None
    own_state = (company.state_code if company else None) or ((e.own_gstin or "")[:2] or None)
    invoice_like = behaviour(e.voucher_type, e.voucher_parent).shape == "invoice"
    checks = validate.run(p, own_state=own_state, invoice_like=invoice_like)

    # Keep the last Tally findings (still true until re-checked); refresh local.
    prev = ((meta.get("_duplicates") or {}).get("value") or {})
    tally_findings = [f for f in prev.get("findings", []) if f.get("source") == "tally"]
    findings = dedupe.check_local(db, e) + tally_findings
    pipeline.apply_duplicates(e, findings, {
        "checked": prev.get("tally_checked", False), "error": prev.get("tally_error"),
        "scanned": prev.get("tally_scanned", 0), "window": prev.get("tally_window"),
    })
    checks += dedupe.as_validations(findings)

    e.validations = checks
    fm = {k: v.as_dict() for k, v in p.fields.items()}
    score, breakdown = confidence.score(fm, e.party_confidence, checks, p.lines, 1.0)
    e.confidence = score
    e.field_meta = {**(e.field_meta or {}), **fm,
                    "_score": {"value": breakdown, "confidence": score, "source": "computed", "evidence": None}}
    e.updated_at = utcnow()


@router.post("/{entry_id}/check-duplicates", response_model=EntryDetail)
@_one_hand
def check_duplicates(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """Ask Tally again, now. Useful after opening Tally or keying something in."""
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    company = db.get(Company, e.company_id) if e.company_id else None
    if not company:
        raise HTTPException(400, NO_COMPANY)

    party = e.party_ledger.tally_name if e.party_ledger else None
    t = dedupe.check_tally(e, company.tally_name, party, fresh=True)
    findings = dedupe.check_local(db, e) + t["findings"]
    pipeline.apply_duplicates(e, findings, t)
    e.validations = [v for v in (e.validations or []) if v["code"] not in ("duplicate", "similar_bill")] \
        + dedupe.as_validations(findings)
    db.add(AuditLog(entry_id=e.id, action="duplicate_check", detail={
        "tally_checked": t["checked"], "scanned": t["scanned"], "found": len(findings), "error": t["error"]}))
    db.commit()
    db.refresh(e)
    return _detail(db, e)


@router.post("/{entry_id}/rematch", response_model=EntryDetail)
@_one_hand
def rematch(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """Re-run matching after a Tally sync or a newly created master."""
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if not e.company_id:
        raise HTTPException(400, NO_COMPANY)

    _rematch(db, e)
    _recheck(db, e)
    db.commit()
    db.refresh(e)
    return _detail(db, e)


@router.post("/{entry_id}/reject", response_model=EntrySummary)
def reject(entry_id: int, reason: str = "", db: Session = Depends(get_db)) -> Any:
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.status in IN_TALLY:
        raise HTTPException(409, "Already in Tally; delete the voucher there instead.")
    if e.status != "rejected":
        db.add(AuditLog(entry_id=e.id, action="rejected", detail={"reason": reason, "was": e.status}))
        e.status = "rejected"
        db.commit()
    return _summary(e)


@router.post("/{entry_id}/restore", response_model=EntrySummary)
def restore(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """Undo a reject: the entry goes back to where it was, to review or exported."""
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.status != "rejected":
        raise HTTPException(409, "This entry is not rejected.")
    last = db.scalar(select(AuditLog).where(AuditLog.entry_id == e.id, AuditLog.action == "rejected")
                     .order_by(desc(AuditLog.id)).limit(1))
    e.status = ((last.detail or {}).get("was") if last else None) or "pending"
    db.add(AuditLog(entry_id=e.id, action="restored", detail={"status": e.status}))
    db.commit()
    return _summary(e)


# --------------------------------------------------------------------------
# approve -> export, draft into Tally, or post into Tally
# --------------------------------------------------------------------------

def _exact_duplicates(e: Entry) -> list[dict[str, Any]]:
    d = ((e.field_meta or {}).get("_duplicates") or {}).get("value") or {}
    return [f for f in d.get("findings", []) if f.get("kind") == "exact"]


def _blockers(db: Session, e: Entry, req: ApproveRequest) -> list[str]:
    """Things that must be fixed or knowingly acknowledged first.

    Similar bills never appear here: they are shown on the review screen and
    that is all. An exact duplicate needs one deliberate acknowledgement.
    """
    out: list[str] = []
    if e.status in IN_TALLY:
        out.append(f"Already in Tally as voucher {e.tally_voucher_id or '(id not returned)'}.")
    if not e.company_id:
        out.append(NO_COMPANY)
    if not e.party_ledger_id:
        out.append(f"No Tally ledger is selected for '{e.party_name_raw or 'the party'}'. "
                   f"Pick one, or create it from this screen.")
    if not e.invoice_date:
        out.append("The date is missing; Tally needs one.")
    if not e.grand_total:
        out.append("The total is missing or zero.")

    if not req.override_duplicate:
        for f in _exact_duplicates(e):
            out.append(f"Possible duplicate: {f['reason']}. Tick 'post anyway' if this is a different bill.")

    errs = [v for v in (e.validations or [])
            if v.get("severity") == "error" and v.get("code") not in ("duplicate", "similar_bill")]
    if errs and not req.override_errors:
        out.extend(f"Check failed: {v['message']}" for v in errs)

    unmapped = [l for l in e.lines if not l.stock_item_id and not l.ledger_id]
    if unmapped and e.lines:
        names = ", ".join((l.description_raw or f"line {l.line_no + 1}")[:40] for l in unmapped[:3])
        more = f" (+{len(unmapped) - 3} more)" if len(unmapped) > 3 else ""
        out.append(f"{len(unmapped)} line(s) have no Tally ledger or stock item: {names}{more}")
    return out


MISSING = "⟨"  # marks a placeholder name in a tolerant (preview) build


def _placeholder(what: str) -> str:
    return f"{MISSING}{what}⟩"


def _build_spec(db: Session, e: Entry, optional: bool = False,
                tolerant: bool = False) -> voucher_xml.VoucherSpec:
    """tolerant=True never raises for a missing ledger: it drops in a visible
    placeholder instead, so the review screen can show the whole voucher with
    the gaps marked. Posting always uses the strict build."""
    company = db.get(Company, e.company_id) if e.company_id else None
    party = db.get(Ledger, e.party_ledger_id) if e.party_ledger_id else None
    b = behaviour(e.voucher_type, e.voucher_parent)
    if not tolerant and (company is None or party is None):
        raise HTTPException(422, "Set a company and a party ledger first.")

    def default_line(rate: float | None = None, item: StockItem | None = None) -> str:
        try:
            return _default_line_ledger(db, e, rate, item)
        except HTTPException:
            if not tolerant:
                raise
            return _placeholder("pick a ledger")

    bill_rate = _bill_rate(e)
    lines: list[voucher_xml.VoucherLine] = []
    # Items as items, and a charge (packing, cartage) or an unmatched line as a
    # ledger beside them, the way an item invoice is keyed in Tally.
    use_inventory = b.shape == "invoice" and any(l.stock_item_id for l in e.lines)
    for l in e.lines:
        if l.stock_item_id:
            it = db.get(StockItem, l.stock_item_id)
            # An inventory line still needs an accounting ledger behind it.
            acct = db.get(Ledger, l.ledger_id) if l.ledger_id else None
            rate = _first(l.gst_rate, it.gst_rate if it else None, bill_rate)
            lines.append(voucher_xml.VoucherLine(
                ledger_name=acct.tally_name if acct else default_line(rate, it),
                amount=l.amount or Decimal(0), stock_item=it.tally_name if it else None,
                # Tally counts the item in its own unit ("pcs"), whatever the bill calls it.
                quantity=l.quantity, rate=l.rate, uom=(it.uom if it else None) or l.uom,
                description=l.description, line_id=l.id, gst_rate=rate,
            ))
        else:
            led = db.get(Ledger, l.ledger_id) if l.ledger_id else None
            # Approval blocks on unmapped lines; this fallback only exists so
            # the XML preview still renders instead of raising at the reviewer.
            lines.append(voucher_xml.VoucherLine(
                ledger_name=led.tally_name if led else (_placeholder("pick a ledger") if tolerant
                                                        else default_line(_first(l.gst_rate, bill_rate))),
                amount=l.amount or Decimal(0), line_id=l.id,
            ))

    if not lines:
        amount = e.taxable_total if b.shape == "invoice" else e.grand_total
        lines = [voucher_xml.VoucherLine(ledger_name=default_line(bill_rate), amount=amount or Decimal(0))]
    if b.party_is_debit is None:
        # Journal, Contra: a line's sign is its side, a positive amount a debit.
        for ln in lines:
            ln.is_debit = True

    chosen = _chosen_ledgers(db, e)
    taxes: dict[str, tuple[str, Decimal]] = {}
    if b.shape == "invoice":
        for kind, amount in (("cgst", e.cgst), ("sgst", e.sgst), ("igst", e.igst), ("cess", e.cess)):
            if not amount:
                continue
            meta = (e.field_meta or {}).get(f"{kind}_rate") or {}
            rate = float(meta["value"]) if meta.get("value") else None
            led = chosen.get(kind) or (pipeline.find_tax_ledger(db, e.company_id, kind, rate)
                                       if e.company_id else None)
            if not led:
                if tolerant:
                    taxes[kind] = (_placeholder(f"no {kind.upper()} ledger in Tally"), Decimal(amount))
                    continue
                raise HTTPException(422, f"No ledger for {kind.upper()} was found under 'Duties & Taxes' "
                                         f"in {company.tally_name}. Create it in Tally and sync, or "
                                         f"clear the {kind.upper()} amount on this entry.")
            taxes[kind] = (led.tally_name, Decimal(amount))

    round_off = None
    if e.round_off:
        ro = chosen.get("round") or (pipeline.find_tax_ledger(db, e.company_id, "round") if e.company_id else None)
        if not ro and not tolerant:
            raise HTTPException(422, f"No round-off ledger was found in {company.tally_name}. Pick one on the "
                                     f"round-off row under What I'll post, or create one in Tally (Round Off, "
                                     f"under Indirect Expenses) and sync.")
        round_off = (ro.tally_name if ro else _placeholder("no round-off ledger in Tally"), Decimal(e.round_off))

    # Manual numbering: you key the bill's own number in as the voucher
    # number; without it the voucher would have none.
    numbering = next((v.get("numbering") for v in (company.voucher_types or [])
                      if v.get("name") == e.voucher_type), None) if company else None

    # The GST details a voucher keyed in Tally carries.
    ours = gst.state_name(company.state_code) if company else None
    theirs = (party.state if party else None) or gst.state_name(gst.gstin_state(e.party_gstin))
    invoice = b.shape == "invoice"
    # Not read off the bill: where a purchase is received, where a sale goes.
    pos = gst.state_name(e.place_of_supply) or (ours if b.party_group == "Creditors" else theirs)

    return voucher_xml.VoucherSpec(
        company=company.tally_name if company else _placeholder("no company"),
        voucher_type=e.voucher_type, voucher_parent=e.voucher_parent,
        date=e.invoice_date or datetime.now().date(),
        party_ledger=party.tally_name if party else _placeholder("pick a party ledger"),
        lines=lines, invoice_no=e.invoice_no, reference=e.reference,
        invoice_date=e.invoice_date, taxes=taxes, round_off=round_off,
        narration=e.narration, party_gstin=e.party_gstin,
        place_of_supply=pos if invoice else None,
        use_inventory=use_inventory, optional=optional,
        voucher_number=e.invoice_no if numbering == "Manual" else None,
        numbering_manual=numbering == "Manual",
        bill_link=bill_links.url_for(e.document),
        gst_registration=company.gst_registration if company and invoice else None,
        company_gstin=company.gstin if company and invoice else None,
        company_state=ours if invoice else None, party_state=theirs if invoice else None,
        godown=company.godown if company else None,
        inter_state=bool(e.igst) or bool(ours and theirs and ours != theirs),
    )


def _rejected(result: tally.PostResult, e: Entry) -> str:
    """What Tally's reply means, in words that say what to do next."""
    said = "; ".join(result.messages)
    if result.exceptions:
        text = ("Tally took this voucher as an import exception: it is in Tally, but kept out of your books "
                "until it is fixed there (in Tally: Alt+O Import > Exceptions). Correct it or delete it there; "
                "posting again from here would add another." + (f" Tally said: {said}" if said else ""))
    elif said:
        text = f"Tally rejected the voucher: {said}"
    else:
        text = (f"Tally took nothing and gave no reason ({result.created} created, {result.altered} altered, "
                f"{result.ignored} ignored, {result.errors} errors). Its reply began: {result.raw[:300]}")
    if e.invoice_date and e.invoice_date.day not in tally.EDUCATIONAL_DAYS and tally.educational():
        text += " " + tally.EDUCATIONAL_NOTE
    return text


def _master_rejected(res: tally.PostResult, what: str) -> str:
    said = "; ".join(res.messages)
    if res.exceptions:
        return (f"Tally took the {what} as an import exception: fix or delete it in Tally (Alt+O Import > "
                f"Exceptions), then Sync." + (f" Tally said: {said}" if said else ""))
    return f"Tally did not create the {what}: " + (said or f"no reason given. Its reply began: {res.raw[:300]}")


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _bill_rate(e: Entry) -> float | None:
    """The bill's GST rate, for lines that carry none of their own: as read
    off it, else its tax over its taxable value when that is a GST rate."""
    meta = e.field_meta or {}
    for key, times in (("igst_rate", 1), ("cgst_rate", 2)):
        v = (meta.get(key) or {}).get("value")
        if v:
            return float(v) * times
    taxes = (e.cgst, e.sgst, e.igst)
    tax = sum(x or 0 for x in taxes)
    if e.taxable_total and (tax or any(x is not None for x in taxes)):  # tax written as 0.00: nil-rated
        pct = float(tax) / float(e.taxable_total) * 100
        near = min(gst.VALID_GST_RATES, key=lambda r: abs(r - pct))
        return near if abs(near - pct) < 0.6 else None  # items at several rates: no one rate
    return None


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NO_GST = ("exempt", "extempt", "nil rated", "non gst", "non-gst")
_INTER = re.compile(r"igst|inter[- ]?state|outside state")


def _rate_fit(name: str, rate: float | None) -> int:
    """0: the ledger's name is for this GST rate; 1: for no rate; 2: for
    another rate. With no rate known, a name for no rate fits best."""
    n = name.lower()
    named = {float(x) for x in _PCT.findall(n)} | ({0.0} if any(w in n for w in _NO_GST) else set())
    if rate is None:
        return 1 if named else 0
    return 0 if rate in named else (2 if named else 1)


def _default_line_ledger(db: Session, e: Entry, rate: float | None = None,
                         item: StockItem | None = None) -> str:
    """Where a line with no ledger of its own posts - a stock item's line, say.
    The ledger you picked for that item before, or the one it goes to in
    your own vouchers; otherwise the first group in
    order (Purchase Accounts before the expense groups on a purchase), a name
    for the line's GST rate ("PURCHASE @18% GST", the IGST one on an
    inter-state bill), a GST name over an old VAT one, one named for
    purchases (or sales), then the plainest name. The shortest name alone
    picked "Discount", then "Purchase 5%" for 18% bills, on real books."""
    if item is not None:
        mine = matching.recall(db, e.company_id, "item_ledger", item.tally_name)
        # ...or the one it goes to in your own vouchers, unless that is named for another GST rate.
        usual = db.scalar(select(Bought.ledger).where(Bought.company_id == e.company_id, Bought.item == item.tally_name,
                                                      Bought.ledger.is_not(None))
                          .group_by(Bought.ledger).order_by(func.sum(Bought.times).desc()))
        for name in (mine, usual if usual and _rate_fit(usual, rate) < 2 else None):
            if name and db.scalar(select(Ledger.id).where(Ledger.company_id == e.company_id,
                                                           Ledger.tally_name == name)):
                return name
    groups = pipeline.line_groups(e.voucher_type, e.voucher_parent)
    word = "sale" if groups == pipeline.INCOME_GROUPS else "purchase"
    leds = list(db.scalars(select(Ledger).where(Ledger.company_id == e.company_id, Ledger.parent.in_(groups))))
    if not leds:
        raise HTTPException(422, f"No ledger exists under {' / '.join(groups)} in this company. "
                                 f"Create one in Tally and sync, or map each line to a ledger.")
    inter = bool(e.igst)

    def fit(l: Ledger) -> tuple:
        n = l.tally_name.lower()
        return (groups.index(l.parent), _rate_fit(n, rate), bool(_INTER.search(n)) != inter,
                "gst" not in n, word not in n, len(n))
    return min(leds, key=fit).tally_name


@router.post("/{entry_id}/approve", response_model=ApproveResult)
@_one_hand
def approve(entry_id: int, req: ApproveRequest, db: Session = Depends(get_db)) -> Any:
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if req.mode not in ("export", "draft", "post"):
        raise HTTPException(400, "mode must be export, draft or post")

    blocked = _blockers(db, e, req)
    if blocked:
        dup_only = all("duplicate" in b.lower() for b in blocked)
        return ApproveResult(ok=False, entry_id=e.id, mode=req.mode, status=e.status, needs_ack=dup_only,
                             message="Not done yet - resolve these first.", blocked_by=blocked)

    try:
        spec = _build_spec(db, e, optional=(req.mode == "draft"))
        message_xml = voucher_xml.build(spec)
    except voucher_xml.VoucherImbalance as exc:
        return ApproveResult(ok=False, entry_id=e.id, mode=req.mode, status=e.status,
                             message=str(exc), blocked_by=[str(exc)])

    full_xml = voucher_xml.standalone_xml(spec.company, message_xml)

    if req.dry_run:
        return ApproveResult(ok=True, entry_id=e.id, mode="dry_run", status=e.status,
                             message="Dry run - nothing was sent to Tally or written to disk.",
                             xml=full_xml)

    if req.mode in ("draft", "post"):
        if not settings.tally_allow_post:
            return ApproveResult(
                ok=False, entry_id=e.id, mode=req.mode, status=e.status,
                message="Writing to Tally is switched off. Turn on 'Allow this app to write to Tally' "
                        "in Settings, or use Export instead.",
                blocked_by=["tally_allow_post is off"])

        # Last look inside Tally before writing: someone may have keyed this
        # bill in by hand since it was extracted.
        t = dedupe.check_tally(e, spec.company, spec.party_ledger, fresh=True)
        fresh = [f for f in t["findings"] if f["kind"] == "exact"]
        if fresh and not req.override_duplicate:
            pipeline.apply_duplicates(e, dedupe.check_local(db, e) + t["findings"], t)
            db.commit()
            return ApproveResult(
                ok=False, entry_id=e.id, mode=req.mode, status=e.status, needs_ack=True,
                message="Tally already has this bill. Tick 'post anyway' if it is a different one.",
                blocked_by=[f["reason"] for f in fresh])
        if not t["checked"] and not req.override_duplicate:
            # Never post blind: if the duplicate search itself failed, say so
            # and let the user decide, rather than quietly skipping it.
            return ApproveResult(
                ok=False, entry_id=e.id, mode=req.mode, status=e.status, needs_ack=True,
                message="Could not search Tally for duplicates before posting. "
                        "Tick 'post anyway' to post without that check.",
                blocked_by=[t["error"] or "duplicate search did not complete"])

        try:
            result = tally.import_xml(spec.company, message_xml)
        except tally.TallyError as exc:
            return ApproveResult(ok=False, entry_id=e.id, mode=req.mode, status=e.status,
                                 message=str(exc), blocked_by=[str(exc)])
        if not result.ok:
            db.add(AuditLog(entry_id=e.id, action=f"{req.mode}_failed", detail={
                "created": result.created, "errors": result.errors, "exceptions": result.exceptions,
                "ignored": result.ignored, "messages": result.messages, "reply": result.raw}))
            db.commit()
            return ApproveResult(ok=False, entry_id=e.id, mode=req.mode, status=e.status,
                                 message=_rejected(result, e), blocked_by=result.messages, xml=full_xml)

        e.status = "drafted" if req.mode == "draft" else "posted"
        e.tally_voucher_id = result.last_vch_id
        e.tally_masterid = result.master_id
        e.posted_at = utcnow()
        db.add(AuditLog(entry_id=e.id, action=e.status, detail={
            "company": spec.company, "voucher_id": result.last_vch_id, "created": result.created,
            "acknowledged_duplicate": bool(req.override_duplicate and (fresh or _exact_duplicates(e))),
            "overrode_errors": req.override_errors,
        }))
        db.commit()
        where = "as an optional (draft) voucher" if req.mode == "draft" else ""
        msg = f"In {spec.company} {where} - voucher id {result.last_vch_id or '(not returned)'}."
        if req.mode == "draft":
            msg += " It shows in the Day Book but does not touch your books until you mark it regular in Tally."
        return ApproveResult(ok=True, entry_id=e.id, mode=req.mode, status=e.status, message=msg,
                             tally_voucher_id=result.last_vch_id, xml=full_xml)

    # --- export ----------------------------------------------------------
    folder = settings.data_dir / EXPORT_DIR_NAME / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    safe_no = "".join(c for c in (e.invoice_no or f"entry{e.id}") if c.isalnum() or c in "-_")[:60]
    dest = folder / f"{e.id:06d}_{safe_no}.xml"
    dest.write_text(full_xml, encoding="utf-8")

    e.status = "approved"
    e.export_path = str(dest)
    db.add(AuditLog(entry_id=e.id, action="exported", detail={"path": str(dest), "company": spec.company}))
    db.commit()
    return ApproveResult(
        ok=True, entry_id=e.id, mode="export", status=e.status, export_path=str(dest),
        message=f"Exported to {dest.name}. In Tally: Gateway > Import > Transactions, "
                f"with {spec.company} open.",
        xml=full_xml)


@router.post("/approve-batch")
def approve_batch(entry_ids: list[int], req: ApproveRequest, db: Session = Depends(get_db)) -> Any:
    """Approve the clean ones in one action. Anything with a blocker -- or an
    unacknowledged duplicate -- is skipped and reported, never forced through."""
    req.override_duplicate = False
    req.override_errors = False
    results = []
    for eid in entry_ids[:500]:
        e = db.get(Entry, eid)
        if e is not None and _ai_read(e):
            msg = ("the AI is still checking this bill" if ai_check.pending(e) else
                   "the AI read part of this bill") + " - review and approve it on its own"
            results.append({"ok": False, "entry_id": eid, "message": msg, "blocked_by": [msg]})
            continue
        try:
            results.append(approve(eid, req, db).model_dump())
        except HTTPException as exc:
            results.append({"ok": False, "entry_id": eid, "message": exc.detail, "blocked_by": [exc.detail]})
    return {"total": len(results), "approved": sum(1 for r in results if r["ok"]), "results": results}


@router.get("/{entry_id}/preview")
def preview(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """The voucher exactly as it would be posted, as Dr/Cr rows.

    Built by the same code that posts, so this cannot drift from what Tally
    receives. Missing ledgers show as marked placeholders rather than an error,
    so the reviewer sees the whole entry and exactly which gaps remain.
    """
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    spec = _build_spec(db, e, tolerant=True)
    b = behaviour(e.voucher_type, e.voucher_parent)
    try:
        found = voucher_xml.parts(spec)
    except voucher_xml.VoucherImbalance as exc:
        return {"ok": False, "balanced": False, "error": str(exc), "rows": [], "missing": [],
                "voucher_type": e.voucher_type, "behaves_as": b.parent, "company": spec.company}

    # Each row says what it is (party, line, tax, round-off), so the screen
    # can offer the right ledgers for it and save the pick in the right place;
    # and its natural side, where a positive amount of it sits, so switching
    # it to the other side saves as a negative amount.
    line_side = "Cr" if b.party_is_debit else "Dr"
    party_side = "Dr" if b.party_is_debit else "Cr"
    goods = sum((Decimal(l.amount) for l in spec.lines), Decimal(0))
    rows = []
    for role, blk in found:
        name = html.unescape(re.search(r"<LEDGERNAME>(.*?)</LEDGERNAME>", blk).group(1))
        amt = Decimal(re.search(r"<AMOUNT>(-?[\d.]+)</AMOUNT>", blk).group(1))
        row = {**role, "side": "Dr" if amt < 0 else "Cr", "ledger": name, "amount": str(abs(amt)),
               "natural": party_side if role["role"] == "party" else line_side,
               "is_party": role["role"] == "party", "missing": name.startswith(MISSING)}
        item = re.search(r"<STOCKITEMNAME>(.*?)</STOCKITEMNAME>", blk)
        if item:
            row["stock_item"] = html.unescape(item.group(1))
            row["quantity"] = (re.search(r"<BILLEDQTY>(.*?)</BILLEDQTY>", blk) or [None, ""])[1]
            rate = re.search(r"<RATE>(-?[\d.]+)/(.*?)</RATE>", blk)
            if rate:
                row["rate"], row["unit"] = rate.group(1), html.unescape(rate.group(2))
        if role["role"] == "tax" and goods:
            # Its rate, as Tally shows it beside the tax: the nearest GST rate
            # to what the tax is of the goods, or the plain figure.
            half = 1 if role.get("kind") in ("igst", "cess") else 2
            pct = float(abs(amt) / abs(goods) * 100)
            near = min((r / half for r in gst.VALID_GST_RATES), key=lambda r: abs(r - pct))
            row["pct"] = f"{near:g}" if abs(near - pct) < 0.05 else f"{pct:.2f}"
        rows.append(row)
    ids = dict(db.execute(select(Ledger.tally_name, Ledger.id).where(
        Ledger.company_id == e.company_id, Ledger.tally_name.in_({r["ledger"] for r in rows}))).all())
    for r in rows:
        r["ledger_id"] = ids.get(r["ledger"])

    dr = sum(Decimal(r["amount"]) for r in rows if r["side"] == "Dr")
    cr = sum(Decimal(r["amount"]) for r in rows if r["side"] == "Cr")
    return {
        "ok": True,
        "company": spec.company,
        "voucher_type": e.voucher_type,
        "behaves_as": b.parent,
        "date": str(spec.date),
        "reference": spec.invoice_no,
        "voucher_number": spec.voucher_number,
        "narration": spec.narration,
        "line_side": line_side,
        "rows": rows,
        "total_dr": str(dr), "total_cr": str(cr), "balanced": dr == cr,
        "missing": [r["ledger"].strip(MISSING + "⟩") for r in rows if r["missing"]],
    }


@router.get("/{entry_id}/xml")
def preview_xml(entry_id: int, db: Session = Depends(get_db)) -> Any:
    """See exactly what would be sent, before anything is sent."""
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    if not e.party_ledger_id or not e.invoice_date:
        raise HTTPException(422, "Set a party ledger and a date before previewing the XML.")
    spec = _build_spec(db, e)
    return {"entry_id": e.id, "company": spec.company,
            "xml": voucher_xml.standalone_xml(spec.company, voucher_xml.build(spec))}


# --------------------------------------------------------------------------
# create masters in Tally
# --------------------------------------------------------------------------

@router.post("/create-master")
def create_master(req: CreateMasterRequest, db: Session = Depends(get_db)) -> Any:
    """Create the missing ledger (of any group) or stock item in Tally, from
    any picker on the review screen."""
    with pipeline.entry_lock(req.entry_id):
        return _create_master(req, db)


def _existing_master(db: Session, model: type, company_id: int, name: str) -> Any:
    """A master already in the cache under this name, punctuation/case aside."""
    norm = matching.normalize(name)
    return db.scalar(select(model).where(model.company_id == company_id, model.norm_name == norm))


def _create_master(req: CreateMasterRequest, db: Session) -> Any:
    e = db.get(Entry, req.entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    company = db.get(Company, e.company_id) if e.company_id else None
    if not company:
        raise HTTPException(400, "This entry has no company set.")
    if not settings.tally_allow_post:
        raise HTTPException(
            403, "Creating masters writes to your Tally company, so it needs "
                 "'Allow this app to write to Tally' switched on in Settings.")

    if req.kind == "ledger":
        name = (req.name or e.party_name_raw or "").strip()
        if not name:
            raise HTTPException(400, "A ledger name is required.")

        # No group given: the party picker's own "+ Create", as before -- the
        # entry's own GSTIN and state, and it becomes this bill's party. Every
        # other picker always sends its own group, and wires the result in itself.
        as_party = req.parent is None
        b = behaviour(e.voucher_type, e.voucher_parent)
        parent = req.parent or ("Sundry Debtors" if b.party_group == "Debtors" else "Sundry Creditors")

        row = _existing_master(db, Ledger, company.id, name)
        created = row is None
        if created:
            gstin = e.party_gstin if as_party else None
            state = gst.state_name(gst.gstin_state(e.party_gstin)) if as_party else None
            try:
                res = tally.create_ledger(company.tally_name, name, parent, gstin=gstin, state=state,
                                          duty_head=req.duty_head if parent == "Duties & Taxes" else None)
            except tally.TallyError as exc:
                raise HTTPException(503, str(exc))
            if not res.ok:
                raise HTTPException(422, _master_rejected(res, "ledger"))
            row = Ledger(company_id=company.id, tally_name=name, parent=parent, gstin=gstin, state=state,
                        is_party=any(k in parent.lower() for k in ("creditor", "debtor")),
                        norm_name=matching.normalize(name))
            db.add(row)
            db.flush()
            db.add(AuditLog(entry_id=e.id, action="ledger_created",
                            detail={"name": name, "parent": parent, "gstin": gstin}))

        if as_party:
            e.party_ledger_id = row.id
            e.party_confidence = 1.0
            e.party_match_reason = "created in Tally from this invoice" if created else "matched an existing Tally ledger"
            _recheck(db, e)
        db.commit()
        verb = "Created" if created else "Using the existing"
        return {"ok": True, "kind": "ledger", "id": row.id, "name": row.tally_name,
                "message": f"{verb} '{row.tally_name}' under {row.parent} in {company.tally_name}."}

    line = db.get(EntryLine, req.line_id) if req.line_id else None
    if not line:
        raise HTTPException(400, "line_id is required when creating a stock item.")
    name = (req.name or line.description_raw or "").strip()[:200]
    if not name:
        raise HTTPException(400, "A stock item name is required.")

    row = _existing_master(db, StockItem, company.id, name)
    created = row is None
    if created:
        try:
            res = tally.create_stock_item(company.tally_name, name, req.parent or "Primary",
                                          uom=req.uom or line.uom or "Nos",
                                          hsn=req.hsn or line.hsn_sac, gst_rate=req.gst_rate or line.gst_rate)
        except tally.TallyError as exc:
            raise HTTPException(503, str(exc))
        if not res.ok:
            raise HTTPException(422, _master_rejected(res, "stock item"))
        row = StockItem(company_id=company.id, tally_name=name, parent=req.parent or "Primary",
                        uom=req.uom or line.uom or "Nos", hsn=req.hsn or line.hsn_sac,
                        gst_rate=req.gst_rate or line.gst_rate, norm_name=matching.normalize(name))
        db.add(row)
        db.flush()
        db.add(AuditLog(entry_id=e.id, action="stock_item_created", detail={"name": name}))

    line.stock_item_id = row.id
    line.ledger_id = None
    line.match_confidence = 1.0
    line.match_reason = "created in Tally from this invoice" if created else "matched an existing Tally stock item"
    db.commit()
    verb = "Created stock item" if created else "Using the existing stock item"
    return {"ok": True, "kind": "stock", "id": row.id, "name": row.tally_name,
            "message": f"{verb} '{row.tally_name}' in {company.tally_name}."}
