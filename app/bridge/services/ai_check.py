"""The AI's reading of a bill, in the background.

A bill is on the review screen as soon as the rules have read its text or
OCR (pipeline.process_document). The AI then reads it too, here, one bill at
a time, and the two readings are reconciled: what the AI fills in or
corrects goes into the entry, and the checks run again. A field you changed
already is never overwritten, nor is anything on a bill in Tally or
rejected: there the AI's reading is only a warning to check on the bill.

The AI call holds no database session and no lock: it takes minutes on a
CPU, and sessions held meanwhile once ran the connection pool dry
(routers.documents).
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from ..db import SessionLocal
from ..models import AuditLog, Company, Entry
from . import dedupe, llm, pdf_text, pipeline, validate
from .vouchers import behaviour

WAITING = ("waiting", "reading")
# A changed one means the bill may be one already in Tally: search it again.
_IDENTITY = {"invoice_no", "invoice_date", "grand_total", "party_gstin", "party", "company"}

_queue: queue.Queue[int] = queue.Queue()
_start = threading.Lock()
_worker: Optional[threading.Thread] = None


def state(e: Optional[Entry]) -> dict[str, Any]:
    return (((e.field_meta or {}).get("_ai") or {}).get("value") or {}) if e else {}


def pending(e: Entry) -> bool:
    return state(e).get("state") in WAITING


def enqueue(e: Optional[Entry]) -> None:
    """Check a bill just read, if it is waiting for the AI."""
    global _worker
    if e is None or not pending(e):
        return
    _queue.put(e.id)
    with _start:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="ai-check", daemon=True)
            _worker.start()


def _loop() -> None:
    while True:
        entry_id = _queue.get()
        try:
            check(entry_id)
        except Exception:
            pass  # check() marks the bill itself; nothing may stop the queue


def resume() -> None:
    """Checks that were waiting when the app was last closed."""
    with SessionLocal() as db:
        for e in db.scalars(select(Entry).where(Entry.status.in_(("pending", "rejected"))).order_by(Entry.id)):
            enqueue(e)


def _mark(e: Entry, value: dict[str, Any]) -> None:
    e.field_meta = {**(e.field_meta or {}),
                    "_ai": {"value": value, "confidence": 1.0, "source": "computed", "evidence": None}}


def check(entry_id: int) -> None:
    with pipeline.entry_lock(entry_id), SessionLocal() as db:
        e = db.get(Entry, entry_id)
        if e is None or not pending(e):
            return  # read again or deleted meanwhile, or checked already
        mode = state(e).get("mode")
        path, text = e.document.stored_path, e.document.extracted_text or ""
        own = set(pipeline.own_gstin_map(db))
        _mark(e, {"state": "reading", "mode": mode})
        db.commit()

    try:
        images = pdf_text.page_images(Path(path)) if mode == "image" else None
        parsed, filled = pipeline.read_bill(text, own, ai=True, images=images)
    except Exception:
        parsed, filled = None, None

    with pipeline.entry_lock(entry_id), SessionLocal() as db:
        e = db.get(Entry, entry_id)
        if e is None or not pending(e):
            return
        again = False
        try:
            if filled is None:
                raise RuntimeError("the AI gave no answer")
            again = reconcile(db, e, parsed, filled, mode)
        except Exception:
            if filled is not None:
                traceback.print_exc()  # a fault here, not the AI's: into app.log
            db.rollback()
            _mark(db.get(Entry, entry_id), {"state": "failed", "mode": mode})
        db.commit()
    if again:
        with SessionLocal() as db:
            e = db.get(Entry, entry_id)
            if e is not None:
                _search_tally(db, e)


def _same(a: Any, b: Any) -> bool:
    return (a or None) == (b or None)  # 0 and nothing are the same amount


_LABELS = {"own_gstin": "Your GSTIN", "due_date": "Due date", "place_of_supply": "Place of supply",
           "supply_type": "Supply type", "currency": "Currency", "company": "Company"}


def _label(key: str) -> str:
    return validate.LABELS.get(key) or pipeline._SAY.get(key) or _LABELS.get(key) or key


def _line_keys(rows: list[Any]) -> list[tuple]:
    get = (lambda r, k: r.get(k)) if rows and isinstance(rows[0], dict) else getattr
    return [tuple(get(r, k) for k in ("description_raw", "hsn_sac", "quantity", "uom", "rate", "amount", "gst_rate"))
            for r in rows]


def reconcile(db, e: Entry, parsed, filled: list[str], mode: Optional[str]) -> bool:
    """The AI's reading into the entry. Returns whether what identifies the
    bill changed, so Tally is searched for it again."""
    from ..routers import entries  # the same steps as after an edit

    meta = dict(e.field_meta or {})
    editable = e.status == "pending"
    b = behaviour(e.voucher_type, e.voucher_parent)
    company = db.get(Company, e.company_id) if e.company_id else None
    changed: list[str] = []
    differs: list[dict[str, Any]] = []

    def differ(key: str, have: Any, read: Any) -> None:
        whose = "you entered" if (meta.get(key) or {}).get("source") == "user" else "the entry has"
        differs.append({"field": key, "text": str(have), "image": str(read),
                        "note": f"{_label(key)}: {whose} {have if have is not None else 'nothing'}, the AI reads "
                                f"{read if read is not None else 'nothing'} on the bill. Check it on the bill."})

    for col, read in pipeline.header(parsed, company, b).items():
        key = pipeline.READ_AS.get(col, col)
        have = getattr(e, col)
        if _same(have, read):
            continue
        if not editable or (meta.get(key) or {}).get("source") == "user":
            differ(key, have, read)
            continue
        setattr(e, col, read)
        changed.append(key)
        if key in parsed.fields:
            meta[key] = parsed.fields[key].as_dict()
        else:
            meta.pop(key, None)
    e.field_meta = meta

    # Whose books: the AI may read your GSTIN where the OCR could not.
    moved = False
    if "own_gstin" in changed and (meta.get("_company") or {}).get("source") != "user":
        to, why = pipeline.choose_company(db, e.own_gstin, e.company_id, pipeline.own_gstin_map(db))
        if to is not None and to.id != e.company_id:
            entries._move_to(db, e, to, why=why)
            company, moved = to, True
            changed.append("company")
    party_was = e.party_ledger_id
    if not moved and company and {"party_name", "party_gstin"} & set(changed):
        entries._rematch(db, e)

    rows = pipeline.line_rows(parsed, b, e.grand_total)
    if _line_keys(e.lines) != _line_keys(rows):
        total = sum(r.get("amount") or 0 for r in rows)
        if not editable or meta.get("_lines_user"):
            differs.append({"field": "lines", "text": "the lines as they are", "image": f"lines adding up to {total}",
                            "note": f"Line items: the AI reads them differently on the bill, adding up to {total}. "
                                    f"Check them on the bill."})
        else:
            pipeline.set_lines(db, e, rows, company)
            changed.append("lines")
    if e.party_ledger_id != party_was:
        changed.append("party")

    meta = dict(e.field_meta or {})
    held = {d["field"] for d in differs}
    kept = [c for c in pipeline.stored_conflicts(parsed)["value"] if c["field"] not in held]
    meta["_conflicts"] = {"value": kept + differs, "confidence": 1.0, "source": "computed", "evidence": None}
    ai_filled = [k for k in filled if k not in held and (k != "charges" or "lines" in changed)]
    if ai_filled:
        meta["_llm_filled"] = {"value": ai_filled, "confidence": llm.LLM_CEILING, "source": "llm", "evidence": None}
    shown = [k for k in changed if k != "party"]  # the name is shown when it changed
    meta["_ai"] = {"value": {"state": "done", "mode": mode, "changed": [_label(k) for k in shown],
                             "differs": [_label(k) for k in sorted(held)]},
                   "confidence": 1.0, "source": "computed", "evidence": None}
    e.field_meta = meta
    entries._recheck(db, e)
    db.add(AuditLog(entry_id=e.id, action="ai_checked", detail={
        "mode": mode, "changed": changed, "differs": sorted(held), "filled": ai_filled}))
    return bool(_IDENTITY & set(changed))


def _search_tally(db, e: Entry) -> None:
    """Tally again, for a bill the AI read differently. The search runs over
    the network, with no lock held; the findings are saved under one."""
    company = db.get(Company, e.company_id) if e.company_id else None
    if company is None:
        return
    t = dedupe.check_tally(e, company.tally_name, e.party_ledger.tally_name if e.party_ledger else None)
    with pipeline.entry_lock(e.id):
        db.refresh(e)
        findings = dedupe.check_local(db, e) + t["findings"]
        pipeline.apply_duplicates(e, findings, t)
        e.validations = [v for v in (e.validations or []) if v["code"] not in ("duplicate", "similar_bill")] \
            + dedupe.as_validations(findings)
        db.commit()
