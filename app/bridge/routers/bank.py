"""Bank statements: read one, show it as the bank printed it, and post each
line to Tally as a Payment or Receipt once you approve it.

A statement is a Document of kind "statement". Reading it is a queued job
like a bill (the same one-at-a-time lock), but its rows are its own: they
never become bill entries. Each row carries the ledgers it posts to - what
the same account or name went to before, or a guess you confirm - and
whether a voucher for it is in Tally already.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..models import Company, Document, Ledger, Statement, StatementRow, utcnow
from ..services import bank_match, dedupe, gst, matching, pipeline, statement as reader, tally, voucher_xml

router = APIRouter(prefix="/api/bank", tags=["bank"])

MAX_BYTES = 40 * 1024 * 1024
# The type a line becomes: money out is a Payment, money in a Receipt.
TYPES = ("Payment", "Receipt", "Contra", "Journal")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

@router.post("/statements")
async def upload(background: BackgroundTasks, file: UploadFile = File(...),
                 company_id: Optional[int] = Form(None), db: Session = Depends(get_db)) -> Any:
    name = file.filename or "statement.pdf"
    if Path(name).suffix.lower() != ".pdf":
        raise HTTPException(400, "A bank statement is read from its PDF (downloaded, or scanned).")
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "The file is empty or larger than 40 MB.")
    path, digest = pipeline.store_upload(name, data)
    prior = dedupe.check_file(db, digest)
    if prior and prior.kind == "statement" and prior.status != "failed":
        st = db.scalars(select(Statement).where(Statement.document_id == prior.id)).first()
        return {"document_id": prior.id, "statement_id": st.id if st else None, "status": prior.status,
                "duplicate_file": True, "message": f"This statement was uploaded already, as '{prior.filename}'."}
    if prior and prior.kind != "statement":
        raise HTTPException(409, f"This exact file was uploaded as a bill ('{prior.filename}').")
    doc = prior or Document(sha256=digest, size_bytes=len(data), source="upload", kind="statement")
    doc.filename, doc.stored_path, doc.company_id = name, str(path), company_id
    doc.status, doc.error = "queued", None
    db.add(doc)
    db.commit()
    background.add_task(_read, doc.id)
    return {"document_id": doc.id, "statement_id": None, "status": "queued", "message": "Reading the statement."}


def _read(document_id: int) -> None:
    with pipeline.ONE_BILL:  # one reading at a time, bills and statements alike
        db = SessionLocal()
        try:
            doc = db.get(Document, document_id)
            if not doc or doc.status not in ("queued", "processing"):
                return
            doc.status = "processing"
            db.commit()
            read = reader.read_file(Path(doc.stored_path))
            st = _save(db, doc, read)
            doc.status, doc.pages, doc.ocr_used = "extracted", read.pages, read.ocr
            doc.has_text_layer = not read.ocr
            db.commit()
            if st.company_id and st.bank_ledger_id:
                _check_tally(db, st)  # what is posted already; quietly, if Tally is not there
        except Exception as exc:
            db.rollback()
            doc = db.get(Document, document_id)
            if doc:
                doc.status, doc.error = "failed", str(exc)[:2000]
                db.commit()
        finally:
            db.close()


def _save(db: Session, doc: Document, read: reader.Statement) -> Statement:
    for old in db.scalars(select(Statement).where(Statement.document_id == doc.id)):
        db.delete(old)
    st = Statement(document_id=doc.id, bank=read.bank, account_no=read.account_no, holder=read.holder,
                   period_from=read.period[0] if read.period else None,
                   period_to=read.period[1] if read.period else None, opening=read.opening)
    st.bank_ledger = _find_account(db, read.account_no, doc.company_id)
    st.company_id = st.bank_ledger.company_id if st.bank_ledger else doc.company_id
    for n, r in enumerate(read.rows, start=1):
        st.rows.append(StatementRow(
            row_no=n, serial=r.serial, txn_date=r.date, value_date=r.value_date, details=r.details, ref=r.ref,
            note=r.note or None, amount=r.amount, balance=r.balance, page=r.page, top=r.y0, bottom=r.y1,
            check=r.check, read_notes=r.notes or None,
            voucher_type="Receipt" if (r.amount or 0) > 0 else "Payment",
            # What the bank wrote, so the voucher can be found from the statement.
            narration=" ".join(x for x in (r.details, r.ref) if x) or None))
    db.add(st)
    db.flush()
    _suggest(db, st)
    return st


def _find_account(db: Session, account_no: Optional[str], company_id: Optional[int]) -> Optional[Ledger]:
    """The bank ledger whose name carries the account's number:
    "KOTAK MAHIND BANK OD A/C (1234567890)"."""
    if not account_no:
        return None
    q = select(Ledger)
    if company_id:
        q = q.where(Ledger.company_id == company_id)
    banks = [l for l in db.scalars(q) if bank_match._bank(l)]
    hit = [l for l in banks if account_no in re.sub(r"\D", "", l.tally_name)] or \
        [l for l in banks if len(re.sub(r"\D", "", l.tally_name)) >= 4
         and account_no.endswith(re.sub(r"\D", "", l.tally_name)[-4:])]
    return hit[0] if len(hit) == 1 else None


def _suggest(db: Session, st: Statement) -> None:
    """What each open row posts to, unless you chose it yourself."""
    if not st.company_id:
        return
    bank = st.bank_ledger.tally_name if st.bank_ledger else None
    leds = list(db.scalars(select(Ledger).where(Ledger.company_id == st.company_id)))
    for r in st.rows:
        if r.status != "pending" or r.match_reason == CHOSEN or r.amount is None:
            continue
        res = bank_match.suggest(db, st.company_id, r.details or "", r.ref or "", r.amount, bank, leds)
        r.ledgers, r.match_confidence = res["ledgers"], res["confidence"]
        r.match_reason, r.candidates = res["reason"][:255], res["candidates"]
    # A returned transfer goes back to what its payment went to.
    by_no = {r.row_no: r for r in st.rows}
    for i, j in bank_match.returns([_plain(r) for r in st.rows]).items():
        r, paid = by_no[i], by_no[j]
        if r.status == "pending" and r.match_reason != CHOSEN and paid.ledgers:
            r.ledgers = [{**x, "amount": str(a)} for x, a in
                         zip(paid.ledgers, bank_match.split(r.amount, _shares(paid.ledgers)))]
            r.match_confidence, r.match_reason = paid.match_confidence, f"the return of row {j}"


CHOSEN = "you chose it"


def _shares(ledgers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum((abs(Decimal(x["amount"])) for x in ledgers), Decimal(0)) or Decimal(1)
    return [{"share": float(abs(Decimal(x["amount"])) / total)} for x in ledgers]


def _plain(r: StatementRow) -> dict[str, Any]:
    return {"i": r.row_no, "date": r.txn_date, "amount": r.amount, "details": r.details or "", "ref": r.ref or ""}


def unfinished() -> list[int]:
    """Statements the app was closed in the middle of reading."""
    with SessionLocal() as db:
        return list(db.scalars(select(Document.id).where(
            Document.kind == "statement", Document.status.in_(("queued", "processing"))).order_by(Document.id)))


def read_in_order(waiting: list[int]) -> None:
    for doc_id in waiting:
        _read(doc_id)


# --------------------------------------------------------------------------
# Tally: what is there already, and posting
# --------------------------------------------------------------------------

def _check_tally(db: Session, st: Statement) -> None:
    """Mark the rows a voucher in Tally is for already, and learn from each
    what its words go to. A row whose voucher is gone from Tally is open again."""
    dates = [r.txn_date for r in st.rows if r.txn_date]
    if not dates or not st.bank_ledger:
        return
    start, end = bank_match.window(dates)
    try:
        vouchers = bank_match.tally_vouchers(st.bank_ledger.company.tally_name, st.bank_ledger.tally_name, start, end)
    except tally.TallyError as exc:
        st.tally_error = str(exc)[:1000]
        db.commit()
        return
    found = bank_match.reconcile([_plain(r) for r in st.rows if r.amount is not None], vouchers)
    for r in st.rows:
        f = found.get(r.row_no)
        if f:
            # Learnt once per voucher: looking again does not count it again.
            new = r.status != "posted" and (r.in_tally or {}).get("master_id") != f.get("master_id")
            r.in_tally = f
            if r.status == "pending":
                r.status = "in_tally"
            if new:
                bank_match.learn(db, st.company_id, r.details or "", r.ref or "",
                                 [(n, Decimal(a)) for n, a in f["ledgers"]], r.txn_date)
        elif r.status == "in_tally":
            r.status, r.in_tally = "pending", None
    db.flush()
    st.tally_checked_at, st.tally_error = utcnow(), None
    _suggest(db, st)
    db.commit()


def _post(db: Session, st: Statement, r: StatementRow) -> dict[str, Any]:
    if r.status != "pending":
        return {"ok": False, "row": r.row_no, "message": f"Row {r.row_no} is {r.status.replace('_', ' ')}."}
    if not st.bank_ledger or not st.company_id:
        return {"ok": False, "row": r.row_no, "message": "Pick the company and the bank's ledger first."}
    if not r.ledgers or r.amount is None or not r.txn_date:
        return {"ok": False, "row": r.row_no, "message": f"Row {r.row_no}: pick the ledger it goes to."}
    leds = {l.id: l for l in db.scalars(select(Ledger).where(
        Ledger.id.in_([x.get("ledger_id") for x in r.ledgers])))}
    if any(x.get("ledger_id") not in leds for x in r.ledgers):
        return {"ok": False, "row": r.row_no, "message": f"Row {r.row_no}: a ledger is not in {st.bank_ledger.company.tally_name}."}
    company = st.bank_ledger.company
    lines = [(leds[x["ledger_id"]].tally_name, Decimal(x["amount"]),
              bool(leds[x["ledger_id"]].bill_wise if leds[x["ledger_id"]].bill_wise is not None
                   else leds[x["ledger_id"]].is_party))
             for x in r.ledgers]
    try:
        xml = voucher_xml.bank_voucher(
            company.tally_name, r.voucher_type or ("Receipt" if r.amount > 0 else "Payment"), r.txn_date,
            st.bank_ledger.tally_name, r.amount, lines, r.narration, gst_registration=company.gst_registration,
            company_gstin=company.gstin, company_state=gst.state_name(company.state_code or (company.gstin or "")[:2]))
    except voucher_xml.VoucherImbalance as exc:
        return {"ok": False, "row": r.row_no, "message": f"Row {r.row_no}: {exc}"}
    # A last look in Tally: someone may have keyed this line in since.
    try:
        near = bank_match.tally_vouchers(company.tally_name, st.bank_ledger.tally_name,
                                         r.txn_date - timedelta(days=3), r.txn_date + timedelta(days=3))
    except tally.TallyError as exc:
        return {"ok": False, "row": r.row_no, "message": str(exc)}
    # Against all the statement's rows around it: two payments of the same
    # amount that day are two rows, and the voucher is for one of them.
    around = [_plain(x) for x in st.rows if x.amount is not None and x.txn_date
              and abs((x.txn_date - r.txn_date).days) <= 6]
    there = bank_match.reconcile(around, near).get(r.row_no)
    if there:
        r.status, r.in_tally = "in_tally", there
        db.commit()
        return {"ok": False, "row": r.row_no, "in_tally": there,
                "message": f"Row {r.row_no} is in Tally already: {there['type']} {there['number'] or ''} "
                           f"of {there['date']}. Not posted again."}
    try:
        res = tally.import_xml(company.tally_name, xml)
    except tally.TallyError as exc:
        return {"ok": False, "row": r.row_no, "message": str(exc)}
    if not res.ok:
        return {"ok": False, "row": r.row_no, "xml": xml,
                "message": f"Tally did not take row {r.row_no}: " + ("; ".join(res.messages) or res.raw[:300])}
    r.status, r.posted_at = "posted", utcnow()
    r.tally_voucher_id = res.master_id or res.last_vch_id
    if r.match_reason != CHOSEN:  # what you chose was learnt when you chose it
        bank_match.learn(db, st.company_id, r.details or "", r.ref or "",
                         [(leds[x["ledger_id"]].tally_name, Decimal(x["amount"])) for x in r.ledgers], r.txn_date)
    db.commit()
    return {"ok": True, "row": r.row_no, "message": f"Row {r.row_no} is in {company.tally_name}."}


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------

def _m(v: Optional[Decimal]) -> Optional[str]:
    return None if v is None else str(v)  # to the paisa, not a float


def _row_out(r: StatementRow) -> dict[str, Any]:
    return {"id": r.id, "row_no": r.row_no, "serial": r.serial, "date": r.txn_date, "value_date": r.value_date,
            "details": r.details, "ref": r.ref, "note": r.note, "amount": _m(r.amount), "balance": _m(r.balance),
            "page": r.page, "top": r.top, "bottom": r.bottom, "check": r.check, "read_notes": r.read_notes or [],
            "voucher_type": r.voucher_type, "ledgers": r.ledgers or [], "narration": r.narration,
            "confidence": r.match_confidence, "reason": r.match_reason, "candidates": r.candidates or [],
            "status": r.status, "in_tally": r.in_tally, "posted_at": r.posted_at}


def _statement_out(st: Statement, rows: bool = True) -> dict[str, Any]:
    doc = st.document
    money = [r.amount for r in st.rows if r.amount is not None]
    out = {"id": st.id, "document_id": st.document_id, "filename": doc.filename, "status": doc.status,
           "error": doc.error, "bank": st.bank, "account_no": st.account_no, "holder": st.holder,
           "period_from": st.period_from, "period_to": st.period_to, "opening": _m(st.opening),
           "company_id": st.company_id, "company": st.company.tally_name if st.company else None,
           "bank_ledger_id": st.bank_ledger_id, "bank_ledger": st.bank_ledger.tally_name if st.bank_ledger else None,
           "pages": doc.pages, "tally_checked_at": st.tally_checked_at, "tally_error": st.tally_error,
           "count": len(st.rows), "debits": _m(-sum((a for a in money if a < 0), Decimal(0))),
           "credits": _m(sum((a for a in money if a > 0), Decimal(0))),
           "closing": _m(next((r.balance for r in reversed(st.rows) if r.balance is not None), None)),
           "by_status": {s: sum(1 for r in st.rows if r.status == s) for s in ("pending", "in_tally", "posted", "skipped")},
           "unsure": sum(1 for r in st.rows if r.check == "bad")}
    if rows:
        out["rows"] = [_row_out(r) for r in st.rows]
    return out


@router.get("/statements")
def list_statements(db: Session = Depends(get_db)) -> Any:
    docs = list(db.scalars(select(Document).where(Document.kind == "statement").order_by(Document.id.desc()).limit(200)))
    sts = {s.document_id: s for s in db.scalars(select(Statement).where(Statement.document_id.in_([d.id for d in docs])))}
    return [_statement_out(sts[d.id], rows=False) if d.id in sts else
            {"id": None, "document_id": d.id, "filename": d.filename, "status": d.status, "error": d.error}
            for d in docs]


def _statement(db: Session, statement_id: int) -> Statement:
    st = db.get(Statement, statement_id)
    if not st:
        raise HTTPException(404, "Statement not found")
    return st


@router.get("/statements/{statement_id}")
def get_statement(statement_id: int, db: Session = Depends(get_db)) -> Any:
    return _statement_out(_statement(db, statement_id))


class StatementUpdate(BaseModel):
    company_id: Optional[int] = None
    bank_ledger_id: Optional[int] = None


@router.patch("/statements/{statement_id}")
def update_statement(statement_id: int, req: StatementUpdate, db: Session = Depends(get_db)) -> Any:
    """Whose statement it is, when its number was not found in a ledger's name."""
    st = _statement(db, statement_id)
    if req.bank_ledger_id is not None:
        led = db.get(Ledger, req.bank_ledger_id)
        if not led:
            raise HTTPException(404, "Ledger not found")
        st.bank_ledger, st.company_id = led, led.company_id
    elif req.company_id is not None:
        if not db.get(Company, req.company_id):
            raise HTTPException(404, "Company not found")
        st.company_id, st.bank_ledger = req.company_id, None
    db.flush()
    db.refresh(st)
    _suggest(db, st)
    db.commit()
    return _statement_out(st)


@router.post("/statements/{statement_id}/check")
def check_tally(statement_id: int, db: Session = Depends(get_db)) -> Any:
    """Look in Tally for the rows posted already (by you or by hand)."""
    st = _statement(db, statement_id)
    if not st.bank_ledger:
        raise HTTPException(400, "Pick the bank's ledger first.")
    _check_tally(db, st)
    if st.tally_error:
        raise HTTPException(503, st.tally_error)
    return _statement_out(st)


class LedgerShare(BaseModel):
    ledger_id: int
    amount: Decimal


class RowUpdate(BaseModel):
    voucher_type: Optional[str] = None
    ledgers: Optional[list[LedgerShare]] = None
    narration: Optional[str] = None
    skip: Optional[bool] = None


@router.patch("/rows/{row_id}")
def update_row(row_id: int, req: RowUpdate, db: Session = Depends(get_db)) -> Any:
    r = db.get(StatementRow, row_id)
    if not r:
        raise HTTPException(404, "Row not found")
    st = r.statement
    if r.status in ("posted", "in_tally") and (req.ledgers is not None or req.voucher_type or req.skip):
        raise HTTPException(409, f"Row {r.row_no} is in Tally already; change it there.")
    if req.skip is not None:
        r.status = "skipped" if req.skip else "pending"
    if req.voucher_type is not None:
        if not req.voucher_type.strip():
            raise HTTPException(400, "A voucher type is needed.")
        r.voucher_type = req.voucher_type.strip()
    if req.narration is not None:
        r.narration = req.narration
    if req.ledgers is not None:
        leds = {l.id: l for l in db.scalars(select(Ledger).where(
            Ledger.id.in_([x.ledger_id for x in req.ledgers]), Ledger.company_id == st.company_id))}
        if any(x.ledger_id not in leds for x in req.ledgers):
            raise HTTPException(400, "A ledger is not in this statement's company.")
        r.ledgers = [{"ledger_id": x.ledger_id, "name": leds[x.ledger_id].tally_name, "amount": str(x.amount)}
                     for x in req.ledgers]
        r.match_confidence, r.match_reason = (1.0, CHOSEN) if req.ledgers else (0.0, "pick the ledger")
        if req.ledgers and r.amount is not None:
            # What you choose is what lines like it go to: the rest of the statement follows.
            bank_match.learn(db, st.company_id, r.details or "", r.ref or "",
                             [(leds[x.ledger_id].tally_name, x.amount) for x in req.ledgers], r.txn_date)
            db.flush()
            _suggest(db, st)
    db.commit()
    return _statement_out(st)


class NewLedger(BaseModel):
    name: str
    parent: str = "Sundry Creditors"
    duty_head: Optional[str] = None


@router.post("/statements/{statement_id}/ledgers")
def create_ledger(statement_id: int, req: NewLedger, db: Session = Depends(get_db)) -> Any:
    """A ledger a row goes to that Tally does not have yet: made there, then
    picked. One of that name already kept is picked instead."""
    from .entries import _existing_master, _master_rejected

    _allowed()
    st = _statement(db, statement_id)
    name = req.name.strip()
    if not st.company or not name:
        raise HTTPException(400, "Pick the statement's company, and give the ledger a name.")
    row = _existing_master(db, Ledger, st.company.id, name)
    if row is None:
        try:
            res = tally.create_ledger(st.company.tally_name, name, req.parent,
                                      duty_head=req.duty_head if req.parent == "Duties & Taxes" else None)
        except tally.TallyError as exc:
            raise HTTPException(503, str(exc))
        if not res.ok:
            raise HTTPException(422, _master_rejected(res, "ledger"))
        row = Ledger(company_id=st.company.id, tally_name=name, parent=req.parent,
                     is_party=any(k in req.parent.lower() for k in ("creditor", "debtor")),
                     norm_name=matching.normalize(name))
        db.add(row)
        db.commit()
    return {"ok": True, "kind": "ledger", "id": row.id, "name": row.tally_name,
            "message": f"'{row.tally_name}' is in {st.company.tally_name} under {row.parent}."}


def _allowed() -> None:
    if not settings.tally_allow_post:
        raise HTTPException(403, "Writing to Tally is switched off. Turn on 'Allow this app to write to Tally' "
                                 "in Settings.")


@router.post("/rows/{row_id}/post")
def post_row(row_id: int, db: Session = Depends(get_db)) -> Any:
    _allowed()
    r = db.get(StatementRow, row_id)
    if not r:
        raise HTTPException(404, "Row not found")
    return {"result": _post(db, r.statement, r), "statement": _statement_out(r.statement)}


class PostMany(BaseModel):
    row_ids: list[int]


@router.post("/statements/{statement_id}/post")
def post_rows(statement_id: int, req: PostMany, db: Session = Depends(get_db)) -> Any:
    """The rows you ticked, one voucher each, in the statement's order."""
    _allowed()
    st = _statement(db, statement_id)
    want = set(req.row_ids)
    results = [_post(db, st, r) for r in st.rows if r.id in want]
    return {"results": results, "posted": sum(1 for x in results if x["ok"]), "statement": _statement_out(st)}
