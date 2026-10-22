"""Match invoice text to Tally masters, with a confidence you can act on.

The ranking, strongest first:
  1. GSTIN on the ledger == GSTIN on the invoice          -> certainty
  2. A correction you made before (Alias)                 -> near-certainty
  3. Fuzzy name similarity                                -> a score, shown as one

This is what answers "the invoice says KESTREL AUDIT (INDIA) PRIVATE LIMITED,
my ledger is Kestrel Audit (FSC) -- how sure are you?".
"""

from __future__ import annotations

import re
from typing import Any, Optional

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Alias, Bought, Ledger, StockItem

# Stripped before comparing: they are noise for matching, not for display.
_SUFFIXES = re.compile(
    r"\b(private\s+limited|pvt\.?\s*ltd\.?|p\s*ltd|limited|ltd\.?|llp|inc\.?|corp\.?|"
    r"corporation|company|co\.?|&\s*co\.?|and\s+co\.?|india|\(india\)|"
    r"gmbh|s\.?a\.?|b\.?v\.?|plc)\b",
    re.I,
)
# Indic vowel signs and viramas are combining marks, which \w does not cover:
# without the Indic blocks here, "गणेश" would normalise to "गण श".
_PUNCT = re.compile(r"[^\w\sऀ-෿]")
_WS = re.compile(r"\s+")


# "4MM" and "4 MM", "M10X125" and "M 10 X 125": one way of writing each.
_DIGIT_EDGE = re.compile(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)")


def normalize(name: str | None) -> str:
    if not name:
        return ""
    s = _SUFFIXES.sub(" ", str(name).upper())
    s = _PUNCT.sub(" ", s)
    s = _DIGIT_EDGE.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _scorer(a: str, b: str, *, processor=None, score_cutoff=None, **_kw) -> float:
    """token_set handles reordering and extra words ('(FSC)', 'A/c'); partial
    handles one name being contained in the other. Take the better of the two,
    then damp it slightly so a fuzzy hit never outranks a GSTIN or an alias.

    partial only for names of 6 letters or more: a 3-letter item "JAR" is
    "contained" in almost anything ("...AAHAR..."). And a name that leaves out
    words of the bill ("4 MM GLASS" for "4 MM MIRROR GLASS") scores a shade
    under one that has them all, which token_set alone scores the same.

    rapidfuzz passes processor/score_cutoff to custom scorers, hence the
    keyword-only tail.
    """
    part = fuzz.partial_ratio(a, b) * 0.95 if min(len(a), len(b)) >= 6 else 0.0
    words = set(a.split())
    has = len(words & set(b.split())) / len(words) if words else 1.0
    score = max(fuzz.token_set_ratio(a, b), part) * (0.95 + 0.05 * has)
    # A number in the name that the bill does not have is another size or
    # grade: VELCRO PAD 7'' for a bill's 5", Magnet Catcher M-2 for its "no 3".
    bill, name = _numbers(a), _numbers(b)
    if bill and name - bill:
        score *= 0.85
    return 0.0 if score_cutoff is not None and score < score_cutoff else score


def _numbers(s: str) -> set[str]:
    return {str(int(w)) for w in s.split() if w.isdigit()}  # 009 is 9


# Units by what they measure: a bill in litres is not an item counted in kg.
_UNITS = {
    "count": "PCS PC PCE NOS NO PIECE PIECES UNIT UNITS SET SETS PKT PKTS PACKET PACKETS BAG BAGS BOX BOXES "
             "ROLL ROLLS PAIR PAIRS DOZ DOZEN BTL BOTTLE BOTTLES JAR TIN CAN",
    "weight": "KG KGS KILO KILOS KILOGRAM KILOGRAMS G GM GMS GRAM GRAMS QTL QUINTAL TON TONNE TONNES MT",
    "volume": "L LT LTR LTRS LITRE LITRES LITER LITERS ML",
    "area": "FT FEET SQF SQFT SFT SQM SQMT RFT MTR MTRS METER METERS METRE METRES INCH",
    "wood": "CFT CUFT CBM",
}
_UNIT_KIND = {u: kind for kind, us in _UNITS.items() for u in us.split()}


def unit_kind(uom: str | None) -> Optional[str]:
    return _UNIT_KIND.get(re.sub(r"[^A-Z]", "", str(uom or "").upper()))


def _alias(db: Session, company_id: int, kind: str, raw_norm: str) -> Optional[Alias]:
    return db.scalar(
        select(Alias).where(
            Alias.company_id == company_id, Alias.kind == kind, Alias.raw_norm == raw_norm
        )
    )


def match_ledger(
    db: Session,
    company_id: int,
    raw_name: str | None,
    gstin: str | None = None,
    parent_like: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """-> {ledger_id, name, confidence, reason, candidates[]}"""
    q = select(Ledger).where(Ledger.company_id == company_id)
    if parent_like:
        q = q.where(Ledger.parent.ilike(f"%{parent_like}%"))
    ledgers = list(db.scalars(q))
    empty = {"ledger_id": None, "name": None, "confidence": 0.0, "reason": "no ledgers cached", "candidates": []}
    if not ledgers:
        return empty

    if gstin:
        g = gstin.upper()
        for led in ledgers:
            if (led.gstin or "").upper() == g:
                return {
                    "ledger_id": led.id,
                    "name": led.tally_name,
                    "confidence": 1.0,
                    "reason": f"GSTIN {g} matches this ledger exactly",
                    "candidates": [],
                }

    raw_norm = normalize(raw_name)
    if not raw_norm:
        return {**empty, "reason": "no party name extracted"}

    al = _alias(db, company_id, "ledger", raw_norm)
    if al:
        led = next((x for x in ledgers if x.tally_name == al.target_name), None)
        if led:
            return {
                "ledger_id": led.id,
                "name": led.tally_name,
                "confidence": 0.99,
                "reason": f"you mapped this name to this ledger before ({al.hits}x)",
                "candidates": [],
            }

    choices = {led.id: normalize(led.tally_name) for led in ledgers}
    hits = process.extract(raw_norm, choices, scorer=_scorer, limit=limit)
    by_id = {led.id: led for led in ledgers}

    candidates = [
        {
            "ledger_id": lid,
            "name": by_id[lid].tally_name,
            "gstin": by_id[lid].gstin,
            "score": round(score / 100.0, 3),
        }
        for _norm, score, lid in hits
        if score / 100.0 >= settings.match_floor
    ]
    if not candidates:
        return {**empty, "reason": f"no ledger scored above {settings.match_floor:.0%}"}

    best = candidates[0]
    # A near-tie is not a match -- it is a question for the reviewer.
    gap = best["score"] - (candidates[1]["score"] if len(candidates) > 1 else 0.0)
    conf = best["score"] * (0.88 if gap < 0.05 and len(candidates) > 1 else 1.0)
    # Fuzzy alone never reads as certain; only GSTIN or an alias earns that.
    conf = min(conf, 0.94)
    reason = f"name similarity {best['score']:.0%}"
    if gap < 0.05 and len(candidates) > 1:
        reason += f" -- but '{candidates[1]['name']}' scores almost the same"
    if gstin and best.get("gstin") and best["gstin"].upper() != gstin.upper():
        conf *= 0.6
        reason += f"; ledger GSTIN {best['gstin']} differs from the invoice's {gstin}"

    return {
        "ledger_id": best["ledger_id"],
        "name": best["name"],
        "confidence": round(conf, 3),
        "reason": reason,
        "candidates": candidates,
    }


def match_stock_item(
    db: Session, company_id: int, description: str | None, hsn: str | None = None, limit: int = 5,
    uom: str | None = None, party: str | None = None,
) -> dict[str, Any]:
    """party: the supplier's ledger name. What you bought from them before
    (Bought, read from your vouchers) decides between close names - your
    books have 4 MM MIRROR GLASS, ... FT and ... SQF, and you always key the
    FT one for this supplier - and names the item for a supplier who only
    ever sells you one ("Small / Medium / Big" rows are Timber Wood)."""
    bought: dict[str, int] = {}
    if party:
        for b in db.scalars(select(Bought).where(Bought.company_id == company_id, Bought.party == party)):
            bought[b.item] = bought.get(b.item, 0) + b.times
    found = _match_stock(db, company_id, description, hsn, limit, uom, bought)
    if bought and found["confidence"] < 0.75:
        top, n = max(bought.items(), key=lambda kv: kv[1])
        it = db.scalar(select(StockItem).where(StockItem.company_id == company_id, StockItem.tally_name == top))
        if it and n >= 3 and n >= 0.9 * sum(bought.values()):
            return {**found, "stock_item_id": it.id, "name": top, "confidence": 0.8,
                    "reason": f"the only item you bought from {party} in the last year ({n} lines)"}
    return found


def _match_stock(db: Session, company_id: int, description: str | None, hsn: str | None, limit: int,
                 uom: str | None, bought: dict[str, int]) -> dict[str, Any]:
    items = list(db.scalars(select(StockItem).where(StockItem.company_id == company_id)))
    empty = {"stock_item_id": None, "name": None, "confidence": 0.0, "reason": "no stock items cached", "candidates": []}
    if not items or not description:
        return empty

    raw_norm = normalize(description)
    al = _alias(db, company_id, "stock", raw_norm)
    if al:
        it = next((x for x in items if x.tally_name == al.target_name), None)
        if it:
            return {
                "stock_item_id": it.id,
                "name": it.tally_name,
                "confidence": 0.99,
                "reason": f"you mapped this description to this item before ({al.hits}x)",
                "candidates": [],
            }

    # Normalised here, not taken from the cache: the rules for it change.
    choices = {it.id: normalize(it.tally_name) for it in items}
    hits = process.extract(raw_norm, choices, scorer=_scorer, limit=limit * 2)
    by_id = {it.id: it for it in items}
    # What you bought from this supplier is in the running, however its name scores.
    listed = {iid for _n, _s, iid in hits}
    hits += [(choices[it.id], _scorer(raw_norm, choices[it.id]), it.id)
             for it in items if it.tally_name in bought and it.id not in listed]
    bill_unit = unit_kind(uom)

    candidates = []
    for _n, score, iid in hits:
        s = score / 100.0
        times = bought.get(by_id[iid].tally_name, 0)
        if times:
            s += 0.15  # not capped yet: M-2 and M-3 Brown must stay apart by name
        # A matching HSN is real corroboration; a clashing one is a warning.
        if hsn and by_id[iid].hsn:
            s = s + 0.08 if by_id[iid].hsn == hsn else s * 0.85
        # So is a unit that measures something else: litres on the bill, kg in Tally.
        item_unit = unit_kind(by_id[iid].uom)
        if bill_unit and item_unit and bill_unit != item_unit:
            s *= 0.85
        if s >= settings.match_floor:
            candidates.append({"stock_item_id": iid, "name": by_id[iid].tally_name, "hsn": by_id[iid].hsn,
                               "score": round(min(s, 1.0), 3), "rank": s, "similarity": round(score / 100.0, 3),
                               "bought": times})
    if not candidates:
        return {**empty, "reason": f"no stock item scored above {settings.match_floor:.0%}"}

    # Equal scores: the item bought from this supplier before.
    candidates.sort(key=lambda c: (-c["rank"], -c["bought"]))
    candidates = [{k: v for k, v in c.items() if k != "rank"} for c in candidates[:limit]]
    best = candidates[0]
    return {
        "stock_item_id": best["stock_item_id"],
        "name": best["name"],
        "confidence": round(min(best["score"], 0.94), 3),
        "reason": f"description similarity {best['similarity']:.0%}"
        + (" (HSN matches)" if hsn and best.get("hsn") == hsn else "")
        + (f", bought from this supplier {best['bought']} times" if best["bought"] else ""),
        "candidates": candidates,
    }


def recall(db: Session, company_id: int, kind: str, raw_text: str) -> Optional[str]:
    """What a reviewer chose for this text before, if anything."""
    al = _alias(db, company_id, kind, normalize(raw_text))
    return al.target_name if al else None


def remember(db: Session, company_id: int, kind: str, raw_text: str, target_name: str) -> None:
    """Record a reviewer's correction so the same text matches next time."""
    raw_norm = normalize(raw_text)
    if not raw_norm or not target_name:
        return
    from ..models import utcnow

    al = _alias(db, company_id, kind, raw_norm)
    if al:
        if al.target_name != target_name:
            al.target_name, al.hits = target_name, 1  # re-taught: start the count over
        else:
            al.hits += 1
        al.last_used_at = utcnow()
    else:
        db.add(
            Alias(
                company_id=company_id,
                kind=kind,
                raw_norm=raw_norm,
                raw_sample=str(raw_text)[:512],
                target_name=target_name,
            )
        )
