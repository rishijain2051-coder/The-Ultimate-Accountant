from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Bought, Company, Ledger, StockItem, utcnow
from ..schemas import CompanyOut, LedgerOut, StockItemOut
from ..services import matching, tally
from ..services.vouchers import BEHAVIOURS, DEFAULT_TYPES, behaviour

router = APIRouter(prefix="/api/tally", tags=["tally"])


@router.get("/ping")
def ping() -> Any:
    return tally.ping()


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db)) -> Any:
    out = []
    for c in db.scalars(select(Company).order_by(Company.tally_name)):
        out.append(CompanyOut(
            id=c.id, tally_name=c.tally_name, gstin=c.gstin, state_code=c.state_code,
            is_active=c.is_active, last_synced_at=c.last_synced_at,
            ledger_count=db.scalar(select(func.count(Ledger.id)).where(Ledger.company_id == c.id)) or 0,
            stock_count=db.scalar(select(func.count(StockItem.id)).where(StockItem.company_id == c.id)) or 0,
        ))
    return out


@router.post("/companies/discover")
def discover(db: Session = Depends(get_db)) -> Any:
    """Read the companies currently open in Tally and cache them."""
    try:
        found = tally.companies()
    except tally.TallyError as exc:
        raise HTTPException(503, str(exc))

    added, updated = [], []
    for c in found:
        if not c["gstin"]:
            try:
                c["gstin"] = tally.registration(c["name"])[0]
            except tally.TallyError:
                pass  # an older Tally has no tax units: the company goes without, as before
        row = db.scalar(select(Company).where(Company.tally_name == c["name"]))
        state_code = (c["gstin"] or "")[:2] or None
        if row:
            row.gstin, row.state_code = c["gstin"] or row.gstin, state_code or row.state_code
            row.books_from = c["books_from"] or row.books_from
            updated.append(c["name"])
        else:
            db.add(Company(tally_name=c["name"], gstin=c["gstin"], state_code=state_code,
                           books_from=c["books_from"]))
            added.append(c["name"])
    db.commit()
    return {"found": len(found), "added": added, "updated": updated,
            "message": f"{len(found)} company(s) open in Tally."
                       + (f" Added: {', '.join(added)}." if added else "")}


@router.post("/companies/{company_id}/sync")
def sync_masters(company_id: int, db: Session = Depends(get_db)) -> Any:
    """Pull ledgers, stock items and voucher types into the local cache.

    Everything downstream reads this cache, which is what lets the app match,
    review and queue entries with Tally closed.
    """
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, "Company not found")

    try:
        leds = tally.ledgers(company.tally_name)
        items = tally.stock_items(company.tally_name)
        vtypes = tally.voucher_types(company.tally_name)
    except tally.TallyError as exc:
        raise HTTPException(503, str(exc))

    existing = {l.tally_name: l for l in db.scalars(select(Ledger).where(Ledger.company_id == company_id))}
    for l in leds:
        row = existing.get(l["name"])
        party = bool(l["parent"] and any(
            k in l["parent"].lower() for k in ("creditor", "debtor", "supplier", "customer")
        ))
        if row is None:
            row = Ledger(company_id=company_id, tally_name=l["name"])
            db.add(row)
        row.parent, row.gstin, row.state = l["parent"], l["gstin"], l["state"]
        row.country, row.is_party = l["country"], party
        row.opening_balance, row.bill_wise = l["opening_balance"], l.get("bill_wise")
        row.norm_name = matching.normalize(l["name"])

    existing_items = {s.tally_name: s for s in db.scalars(
        select(StockItem).where(StockItem.company_id == company_id))}
    for s in items:
        row = existing_items.get(s["name"])
        if row is None:
            row = StockItem(company_id=company_id, tally_name=s["name"])
            db.add(row)
        row.parent, row.uom, row.hsn, row.gst_rate = s["parent"], s["uom"], s["hsn"], s["gst_rate"]
        row.norm_name = matching.normalize(s["name"])

    # numbering: a Manual type gets the bill's number as its voucher number.
    company.voucher_types = [{"name": v["name"], "parent": v["parent"], "numbering": v["numbering"]}
                             for v in vtypes]
    _learn_registration(company)
    bought = _learn_purchases(db, company)
    company.last_synced_at = utcnow()
    db.commit()
    return {"company": company.tally_name, "ledgers": len(leds), "stock_items": len(items),
            "voucher_types": len(vtypes), "bought": bought,
            "message": f"Cached {len(leds)} ledgers, {len(items)} stock items and "
                       f"{len(vtypes)} voucher types"
                       + (f", and what you bought from whom over the last year ({bought} item lines)" if bought
                          else "") + ". Matching now works with Tally closed."}


def _learn_registration(company: Company) -> None:
    """The GST registration and the godown every voucher keyed in Tally names.
    Optional, like the purchases: a Tally without them keeps what it had."""
    try:
        gstin, name = tally.registration(company.tally_name)
        company.gst_registration = name or company.gst_registration
        company.gstin = company.gstin or gstin
    except tally.TallyError:
        pass
    try:
        places = tally.godowns(company.tally_name)
        company.godown = "Main Location" if "Main Location" in places else (places[0] if places else company.godown)
    except tally.TallyError:
        pass


def _learn_purchases(db: Session, company: Company) -> int:
    """Who you bought which item from, and the ledger it went to, over the
    last year of your own vouchers. Optional: a slow or failing read leaves
    the rest of the sync done, and the last year's habits as they were."""
    today = date.today()
    try:
        rows = tally.purchases(company.tally_name, today - timedelta(days=365), today)
    except tally.TallyError:
        return 0
    seen: dict[tuple[str, str, str | None], Bought] = {}
    for r in rows:
        key = (r["party"], r["item"], r["ledger"])
        b = seen.get(key)
        if b is None:
            seen[key] = Bought(company_id=company.id, party=r["party"], item=r["item"], ledger=r["ledger"],
                               times=1, last_date=r["date"])
        else:
            b.times += 1
            b.last_date = max(filter(None, (b.last_date, r["date"])), default=None)
    db.execute(delete(Bought).where(Bought.company_id == company.id))
    db.add_all(seen.values())
    return len(rows)


@router.get("/voucher-types")
def voucher_types(company_id: int | None = None, db: Session = Depends(get_db)) -> Any:
    """The types offered at upload: this company's real list once synced,
    Tally's standard ones before that."""
    raw: list[dict[str, Any]] = []
    source = "default"
    if company_id:
        c = db.get(Company, company_id)
        if c and c.voucher_types:
            raw, source = c.voucher_types, "tally"
    else:
        # Company not chosen yet (it is detected from the invoice's GSTIN):
        # offer every type any synced company has.
        seen: set[str] = set()
        for c in db.scalars(select(Company).where(Company.voucher_types.is_not(None))):
            for v in c.voucher_types or []:
                if v["name"] not in seen:
                    seen.add(v["name"])
                    raw.append(v)
        if raw:
            source = "tally"
    if not raw:
        raw = [{"name": n, "parent": n} for n in DEFAULT_TYPES]

    out = []
    for v in raw:
        b = behaviour(v["name"], v.get("parent"))
        # Only offer types this app knows how to post. A Stock Journal or
        # Physical Stock voucher is not something an invoice turns into.
        if b.parent not in BEHAVIOURS:
            continue
        out.append({"name": v["name"], "parent": b.parent, "label": b.label,
                    "shape": b.shape, "custom": v["name"] != b.parent})
    order = {n: i for i, n in enumerate(DEFAULT_TYPES)}
    out.sort(key=lambda v: (order.get(v["parent"], 99), v["custom"], v["name"]))
    return {"source": source, "types": out}


@router.get("/companies/{company_id}/ledgers", response_model=list[LedgerOut])
def ledgers(company_id: int, q: str | None = None, parent: str | None = None,
            limit: int = Query(200, le=2000), db: Session = Depends(get_db)) -> Any:
    """parent may list several groups, comma-separated: an expense line
    searches Purchase Accounts, Direct and Indirect Expenses at once."""
    from sqlalchemy import or_

    stmt = select(Ledger).where(Ledger.company_id == company_id)
    groups = [g.strip() for g in (parent or "").split(",") if g.strip()]
    if groups:
        stmt = stmt.where(or_(*(Ledger.parent.ilike(f"%{g}%") for g in groups)))
    if q:
        stmt = stmt.where(Ledger.tally_name.ilike(f"%{q}%"))
    return list(db.scalars(stmt.order_by(Ledger.tally_name).limit(limit)))


@router.get("/companies/{company_id}/stock-items", response_model=list[StockItemOut])
def stock_items(company_id: int, q: str | None = None, limit: int = Query(200, le=2000),
                db: Session = Depends(get_db)) -> Any:
    stmt = select(StockItem).where(StockItem.company_id == company_id)
    if q:
        stmt = stmt.where(StockItem.tally_name.ilike(f"%{q}%"))
    return list(db.scalars(stmt.order_by(StockItem.tally_name).limit(limit)))
