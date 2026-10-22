"""
The Exports module: one file per export invoice, and what is missing from it.

Documents arrive over months - the shipping bill at clearance, the bill of
lading when the vessel sails, GSTR-1 at the month end, the bank's advice when
the buyer pays, the eBRC after that - and each is filed against the invoice it
belongs to. From those the module answers, invoice by invoice: is the paperwork
complete, will the IGST refund go through, how much is still to come and by
when, and is the eBRC done.

Inputs are the dicts exim_read produces, each with an "id" and a "name" from
the store; nothing here opens a file. Rules that change - the realisation
periods, the ₹10 lakh limit, the ₹1,000 fee - come from exim_rules.json.

Money is never matched silently. A payment is set against an invoice only on
evidence that names the invoice (an eBRC, the bank advice's invoice field) or
on the user's say - one suggested invoice confirmed, or the payment split over
several (settle_options, check_splits); everything else is a suggestion
waiting for that click.

Where no document says whether an invoice is in GSTR-1, or whether its IGST
refund or drawback came back, the user's own yes or no stands in - and wins
over a document the user knows to be wrong, until it is undone.
"""

import calendar
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from app.backend import paths
from app.backend.common import clean_inv, fingerprint, inr, iso, party_sim, r2, to_date
from app.backend.exim_read import TYPE_WORDS, ocr_key
from app.backend.explain import _day, _days, _paren, _rs

RULES_PATH = paths.RESOURCE_DIR / "app" / "backend" / "exim_rules.json"
SERIOUS, LOOK = "serious", "look"
MARKS = ("gstr1", "igst", "dbk")          # what the user can answer yes or no to, per invoice


def load_rules(path=RULES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Dates and the realisation rules
# ---------------------------------------------------------------------------

def _d(v):
    x = to_date(v)
    return x.date() if x else None


def add_months(d, months):
    """Calendar months forward, held to the month's end: 30 Nov + 15 = 28 Feb."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def regime(shipped, rules):
    for r in rules["realisation"]:
        lo = date.fromisoformat(r["shipped_from"]) if r["shipped_from"] else date.min
        hi = date.fromisoformat(r["shipped_to"]) if r["shipped_to"] else date.max
        if lo <= shipped <= hi:
            return r
    return rules["realisation"][-1]


def due_date(shipped, currency, rules):
    """(due date, months, rule text) for a bill shipped on `shipped`."""
    r = regime(shipped, rules)
    months = r.get("months_inr") if currency == "INR" and r.get("months_inr") else r["months"]
    rule = r["rule"] + (" (invoiced in rupees)" if months == r.get("months_inr") else "")
    cut = date(2025, 11, 14)
    if rules.get("extend_open_before_2025_11_14") and shipped < cut <= add_months(shipped, months):
        months, rule = 15, "FEMA 23(R)/(7)/2025-RB, extended to bills still open on 14 Nov 2025"
    return add_months(shipped, months), months, rule


def _fc(v, cur):
    if v is None:
        return "an unknown amount"
    return f"{cur or ''} {abs(v):,.2f}".strip()


def _pattern(inv):
    """The shape of an invoice number: VI-0484 -> 'VI-9999'."""
    return re.sub(r"\d", "9", str(inv or "").upper())


# ---------------------------------------------------------------------------
# Building the invoice files
# ---------------------------------------------------------------------------

def _row(inv_no, key=None):
    return {"key": key or ocr_key(inv_no), "inv_no": inv_no, "inv_date": None, "buyer": None,
            "currency": None, "value_fc": None, "fob_fc": None, "rate": None, "terms": None,
            "sb": None, "sbs": [], "bls": [], "gst": None, "gst_lines": [], "invoice_doc": None,
            "sb_status": None, "receipts": [], "ebrcs": [], "scheme": None,
            "own": [], "refund_status": [], "dbk_brc": None, "bank_edpms": None}


def _sb_entry(sb, inv):
    return {"doc_id": sb.get("id"), "doc_name": sb.get("name"), "port": sb.get("port"),
            "sb_no": sb.get("sb_no"), "sb_date": sb.get("sb_date"), "leo_date": sb.get("leo_date"),
            "gstin": sb.get("gstin"), "iec": sb.get("iec"), "mode": sb.get("mode"), "ad_code": sb.get("ad_code"),
            "containers": sb.get("containers") or [], "country": sb.get("country"),
            "inv_no": inv.get("inv_no"), "inv_date": inv.get("inv_date"),
            "igst_amount": inv.get("igst_amount"), "igst_value": inv.get("igst_value"),
            "scheme": ("WPAY" if (inv.get("igst_amount") or 0) > 0 else "LUT")
            if inv.get("igst_amount") is not None else sb.get("scheme"),
            # Drawback and RoDTEP are claimed on the whole bill: counted once,
            # against its first invoice.
            "dbk_amount": sb.get("dbk_amount") if inv is (sb.get("invoices") or [inv])[0] else None,
            "rodtep_amount": sb.get("rodtep_amount") if inv is (sb.get("invoices") or [inv])[0] else None,
            "from_register": False}


def _container(text):
    m = re.match(r"([A-Z]{4})\W*(\d{6})\W*(\d)", str(text or "").upper())
    return "".join(m.groups()) if m else None


def _register_sb(o):
    """A shipping bill as the exporter's own register records it: enough to
    track its refund and drawback, not enough to check it against GSTR-1."""
    igst = o.get("igst")
    return {"doc_id": o.get("doc_id"), "doc_name": o.get("doc_name"), "port": o.get("port"),
            "sb_no": o["sb_no"], "sb_date": o.get("sb_date"), "leo_date": None, "gstin": None, "iec": None,
            "mode": None, "containers": [c for c in [_container(o.get("container"))] if c], "country": None,
            "inv_no": o.get("inv_no"), "inv_date": o.get("inv_date"), "igst_amount": igst, "igst_value": None,
            "scheme": ("WPAY" if igst else "LUT") if igst is not None else None,
            "dbk_amount": o.get("dbk"), "rodtep_amount": None, "from_register": True}


def _apply_amendments(lines):
    """Table 9A rows replace the rows they amend, or the invoice counts twice."""
    amended = {(ocr_key(l["amends"]["inum"]), l["amends"]["idt"]) for l in lines if l.get("amends")}
    keep = [l for l in lines if l.get("amends") or (ocr_key(l["inum"]), l["idt"]) not in amended]
    return keep


def build(documents, decisions=None, today=None, rules=None):
    rules = rules or load_rules()
    today = today or date.today()
    decisions = decisions or {}
    marks = _marks(decisions)
    by = defaultdict(list)
    for d in documents:
        by[d.get("type", "unknown")].append(d)

    rows = {}
    notes = []

    # 1. Shipping bills: every invoice on one starts an invoice file.
    for sb in by["sb"]:
        for inv in sb.get("invoices") or []:
            if not inv.get("inv_no"):
                continue
            row = rows.setdefault(ocr_key(inv["inv_no"]), _row(inv["inv_no"]))
            entry = _sb_entry(sb, inv)
            if any(s["sb_no"] == entry["sb_no"] and s["port"] == entry["port"] for s in row["sbs"]):
                continue                                   # the same bill added twice
            row["sbs"].append(entry)
            if row["sb"] is None or (entry["sb_date"] or "") > (row["sb"]["sb_date"] or ""):
                row["sb"] = entry
            row.update({k: row[k] or inv.get(k) for k in ("inv_date", "currency", "value_fc",
                                                          "fob_fc", "rate", "terms")})
            row["buyer"] = row["buyer"] or sb.get("buyer")
            row["scheme"] = row["sb"]["scheme"]

    # 1b. The exporter's own register: what came back against each shipping
    #     bill and the code ICEGATE gave. A bill known only from it still gets
    #     a line, so years of records carry over.
    reg_index = {s["sb_no"]: key for key, r in rows.items() for s in r["sbs"]}
    for doc in by["own_register"]:
        for o in doc.get("rows") or []:
            o = {**o, "doc_id": doc.get("id"), "doc_name": doc.get("name")}
            key = reg_index.get(o.get("sb_no"))
            if key is None and o.get("inv_no"):
                k = ocr_key(o["inv_no"])
                # Numbering that restarts each year repeats; a different
                # shipping bill means a different invoice.
                if k in rows and not (o.get("sb_no") and rows[k]["sbs"]):
                    key = k
                elif k not in rows:
                    key = k
                else:
                    key = f"{k}~{o['sb_no']}"
            if key is None:
                key = f"SB{o['sb_no']}"
            row = rows.setdefault(key, _row(o.get("inv_no") or f"SB {o['sb_no']}", key))
            row["own"].append(o)
            if o.get("sb_no") and not any(x["sb_no"] == o["sb_no"] for x in row["sbs"]):
                entry = _register_sb(o)
                row["sbs"].append(entry)
                row["sb"] = row["sb"] or entry
                reg_index[o["sb_no"]] = key
            row["inv_date"] = row["inv_date"] or o.get("inv_date")
            row["scheme"] = row["scheme"] or (row["sb"] or {}).get("scheme")

    sb_index = {(s["sb_no"]): (key, s) for key, r in rows.items() for s in r["sbs"]}
    # When a shipping bill calls the invoice something else (SB005), the GST
    # invoice number still has to find its file - from the invoice document,
    # the bill of lading, the bank advice or the eBRC.
    alias = {}

    def find(ref):
        k = ocr_key(ref) if ref else None
        return k if k in rows else alias.get(k)

    # 2. GSTR-1 Table 6A: joined by shipping bill number first, invoice second,
    #    because a wrong invoice number on either side is the thing to catch.
    gst_gstin = next((g.get("gstin") for g in by["gstr1"] if g.get("gstin")), None)
    covered = set()
    lines = []
    for g in by["gstr1"]:
        for line in g.get("lines") or []:
            lines.append({**line, "doc_id": g.get("id"), "doc_name": g.get("name")})
        if g.get("period"):
            covered.add(g["period"])
        else:
            covered.update(l["idt"][:7] for l in g.get("lines") or [] if l.get("idt"))
    lines = _apply_amendments(lines)
    for line in lines:
        key = None
        if line.get("sbnum") and line["sbnum"] in sb_index:
            key = sb_index[line["sbnum"]][0]
        elif find(line["inum"]):
            key = find(line["inum"])
        if key is None:
            key = ocr_key(line["inum"])
            rows[key] = _row(line["inum"])
        row = rows[key]
        if ocr_key(line["inum"]) != key:
            alias[ocr_key(line["inum"])] = key
            row["inv_no"] = line["inum"]          # the GST invoice is the legal one
        row["gst_lines"].append(line)
        row["gst"] = row["gst"] or line
        row["inv_date"] = row["inv_date"] or line.get("idt")
        row["scheme"] = row["scheme"] or line.get("exp_typ")

    # 3. The exporter's own invoices.
    for doc in by["invoice"]:
        if not doc.get("inv_no"):
            continue
        key = find(doc["inv_no"]) or ocr_key(doc["inv_no"])
        row = rows.setdefault(key, _row(doc["inv_no"]))
        row["invoice_doc"] = row["invoice_doc"] or doc
        row.update({k: row[k] or doc.get(k) for k in ("inv_date", "buyer", "scheme", "currency", "value_fc")})

    # 4. Bills of lading: by shipping bill number, then invoice, then container.
    unplaced_bl = []
    for bl in by["bl"]:
        hit = None
        for ref in bl.get("sb_refs") or []:
            if ref["sb_no"] in sb_index:
                hit = sb_index[ref["sb_no"]][0]
                break
        if hit is None:
            for ref in bl.get("invoice_refs") or []:
                if find(ref["inv_no"]):
                    hit = find(ref["inv_no"])
                    break
        if hit is None and bl.get("containers"):
            hit = next((k for k, r in rows.items() if r["sb"] and
                        set(bl["containers"]) & set(r["sb"]["containers"])), None)
        if hit is None:
            unplaced_bl.append(bl)
        else:
            rows[hit]["bls"].append(bl)
            rows[hit]["buyer"] = rows[hit]["buyer"] or bl.get("consignee")

    # 5. ICEGATE's shipping bill status sheet.
    for st in by["sb_status"]:
        for r in st.get("rows") or []:
            if r["sb_no"] in sb_index:
                rows[sb_index[r["sb_no"]][0]]["sb_status"] = r

    # 6. ICEGATE on each refund and drawback, and its list of drawback waiting
    #    on proof that the buyer paid (a download, or a tab of the register).
    #    Documents come oldest first, so a later download wins.
    for doc in by["refund_status"]:
        for r in doc.get("rows") or []:
            if r["sb_no"] in sb_index:
                rows[sb_index[r["sb_no"]][0]]["refund_status"].append(
                    {**r, "doc_id": doc.get("id"), "doc_name": doc.get("name")})
    for doc in by["dbk_pending"] + by["own_register"]:
        for r in doc.get("rows" if doc["type"] == "dbk_pending" else "dbk_pending") or []:
            if r["sb_no"] in sb_index:
                rows[sb_index[r["sb_no"]][0]]["dbk_brc"] = {**r, "doc_id": doc.get("id"), "doc_name": doc.get("name")}

    # 7. The bank's EDPMS statement: what the bank still holds as unpaid.
    edpms_span = None
    for doc in by["edpms"]:
        dates = [r["sb_date"] for r in doc.get("rows") or [] if r.get("sb_date")]
        if dates:
            edpms_span = (min(dates + ([edpms_span[0]] if edpms_span else [])),
                          max(dates + ([edpms_span[1]] if edpms_span else [])))
        for r in doc.get("rows") or []:
            if r["sb_no"] in sb_index:
                rows[sb_index[r["sb_no"]][0]]["bank_edpms"] = {**r, "doc_id": doc.get("id"),
                                                              "doc_name": doc.get("name")}

    rems = _remittances(by, notes)
    ebrcs = _ebrcs(by, rows, sb_index, rems, find)
    _map_money(rows, rems, ebrcs, decisions, find)
    suggestions = _suggest(rows, rems, rules, decisions)

    has_register = bool(by["own_register"])
    for row in rows.values():
        _deadline(row, rules, today, by["bl"])
        _refund(row, rules, today, marks)
        row["gst_mark"] = marks.get((row["key"], "gstr1"), (None, None))[0]
        # A refund or drawback not known to be in: where to look it up on ICEGATE.
        need = {row["refund"]["igst"]["state"], row["refund"]["dbk"]["state"]} & {"held", "pending", "unknown"}
        row["icegate"] = _lookup(row, rules) if row["sb"] and need else None

    issues = []
    for row in rows.values():
        mine = _issues(row, rules, today, covered, gst_gstin, decisions)
        mine += _refund_issues(row, rules, today, has_register, mine, covered)
        if by["edpms"]:
            mine += _edpms_issues(row, edpms_span)
        issues += mine
    issues += _money_issues(rows, rems, suggestions, rules)
    issues += _ebrc_issues(rows, rems, ebrcs)
    for bl in unplaced_bl:
        issues.append(_bl_unplaced(bl))
    for doc in by["unknown"]:
        issues.append(_unread(doc))
    for doc in documents:                        # the AI checker read it differently (ai_check.py)
        if (doc.get("ai") or {}).get("state") == "differs":
            issues.append(_ai_differs(doc))

    for row in rows.values():
        row["chips"] = _chips(row, issues, decisions, covered, rules, today)
        row["marks"] = _controls(row, marks, covered)
    bulk = _bulk_rows(rows, rems, ebrcs)
    checkable = [r for r in rows.values() if r["sb"] and not r["sb"]["from_register"]]
    uncovered = sorted({r["inv_date"][:7] for r in checkable if r["inv_date"] and r["inv_date"][:7] not in covered})
    if by["gstr1"] and uncovered:
        notes.append(f"GSTR-1 for {', '.join(_month(m) for m in uncovered)} has not been added, so "
                     f"{sum(1 for r in checkable if (r['inv_date'] or '')[:7] in uncovered)} "
                     "invoices could not be checked against Table 6A.")
    if has_register:
        only = sum(1 for r in rows.values() if r["sb"] and r["sb"]["from_register"])
        if only:
            notes.append(f"{only} shipping bills are known only from your own register. Their refund and "
                         "drawback are tracked; add the shipping bill PDFs to check them against GSTR-1 too.")
    if not by["gstr1"] and rows:
        notes.append("No GSTR-1 has been added, so no invoice has been checked against Table 6A yet.")

    invoices = sorted(rows.values(), key=lambda r: ((r["sb"] or {}).get("sb_date") or r["inv_date"] or "",
                                                    r["inv_no"] or ""), reverse=True)
    return {
        "kind": "exports",
        "as_at": today.isoformat(),
        "entity": _entity(by, gst_gstin),
        "invoices": invoices,
        "remittances": rems,
        "ebrcs": ebrcs,
        "bulk_rows": bulk,
        "all_records": sorted(issues, key=lambda x: (x["tone"] != SERIOUS, -(x.get("inr") or 0))),
        "documents": [_doc_summary(d, rows, rems, ebrcs) for d in documents],
        "figures": _figures(invoices, issues, bulk, rems),
        "notes": notes,
        "has": {k: bool(by[k]) for k in ("own_register", "refund_status", "edpms", "dbk_pending", "gstr1")},
        "rules": {"as_of": rules.get("as_of"), "counted_from": rules["counted_from"],
                  "icegate": {k: v for k, v in (rules.get("icegate_pages") or {}).items() if k != "about"},
                  "due_soon_days": rules["due_soon_days"],
                  "counted_from_words": rules["counted_from_choices"][rules["counted_from"]]},
    }


def _month(ym):
    d = _d(ym + "-01")
    return f"{d:%b %Y}" if d else ym


def _entity(by, gst_gstin):
    names = Counter(s.get("exporter") for s in by["sb"] if s.get("exporter"))
    gstins = Counter(s.get("gstin") for s in by["sb"] if s.get("gstin"))
    iecs = Counter(s.get("iec") for s in by["sb"] if s.get("iec"))
    return {"name": names.most_common(1)[0][0] if names else None,
            "gstin": gst_gstin or (gstins.most_common(1)[0][0] if gstins else None),
            "iec": iecs.most_common(1)[0][0] if iecs else None}


# ---------------------------------------------------------------------------
# Money in: remittances, eBRCs, and setting one against the other
# ---------------------------------------------------------------------------

def _remittances(by, notes):
    """One entry per inward remittance, from the DGFT IRM list and the bank's
    advices together. An advice and an IRM for the same money become one."""
    out = []
    for doc in by["irm_list"]:
        for r in doc.get("rows") or []:
            cancelled = str(r.get("status") or "").upper().startswith("C")
            amount = r.get("amount_fc") or 0
            out.append({"id": fingerprint("IRM", r["irm_no"]), "irm_no": r["irm_no"],
                        "ref": r.get("bank_ref"), "date": r.get("remit_date") or r.get("irm_date"),
                        "irm_date": r.get("irm_date"), "ifsc": r.get("ifsc"), "ad_code": r.get("ad_code"),
                        "remitter": r.get("remitter"), "country": r.get("country"),
                        "currency": r.get("currency"), "amount_fc": amount,
                        "available_fc": r.get("available_fc") if r.get("available_fc") is not None else amount,
                        "amount_inr": None, "purpose_code": r.get("purpose_code"),
                        "advance": r.get("purpose_code") == "P0103", "invoice_ref": None,
                        "status": r.get("status"), "cancelled": cancelled, "chosen": False,
                        "sources": ["DGFT IRM list"], "doc_ids": [doc.get("id")], "mapped": []})
    for doc in by["fira"]:
        if not (doc.get("currency") and doc.get("amount_fc")):
            continue
        twin = next((r for r in out if r["currency"] == doc["currency"]
                     and abs((r["amount_fc"] or 0) - doc["amount_fc"]) < 0.01
                     and (doc.get("ref") and doc["ref"] in (r["irm_no"] or "") + (r["ref"] or "")
                          or _gap(r["date"], doc.get("credit_date"), 5))), None)
        if twin:
            twin["sources"].append("bank advice")
            twin["doc_ids"].append(doc.get("id"))
            twin.update({k: twin.get(k) or doc.get(v) for k, v in (
                ("ref", "ref"), ("remitter", "remitter"), ("amount_inr", "amount_inr"),
                ("purpose_code", "purpose_code"), ("invoice_ref", "invoice_ref"))})
            twin["advance"] = twin["advance"] or doc.get("advance")
            continue
        out.append({"id": fingerprint("FIRA", doc.get("ref"), doc["currency"], doc["amount_fc"],
                                      doc.get("credit_date")),
                    "irm_no": None, "ref": doc.get("ref"), "date": doc.get("credit_date"), "irm_date": None,
                    "ifsc": None, "ad_code": None, "remitter": doc.get("remitter"), "country": None,
                    "currency": doc["currency"], "amount_fc": doc["amount_fc"],
                    "available_fc": doc["amount_fc"], "amount_inr": doc.get("amount_inr"),
                    "purpose_code": doc.get("purpose_code"), "advance": doc.get("advance"),
                    "invoice_ref": doc.get("invoice_ref"), "status": None, "cancelled": False,
                    "chosen": False, "sources": ["bank advice"], "doc_ids": [doc.get("id")], "mapped": []})
    return sorted(out, key=lambda r: r["date"] or "")


def _gap(a, b, days):
    da, db = _d(a), _d(b)
    return da is not None and db is not None and abs((da - db).days) <= days


def _ebrcs(by, rows, sb_index, rems, find):
    out = []
    for doc in by["ebrc_list"]:
        for e in doc.get("rows") or []:
            key = None
            for ref in (e.get("gst_inv_no"), e.get("bill_no")):
                if ref and find(ref):
                    key = find(ref)
                    break
            if key is None and e.get("sb_no") in sb_index:
                key = sb_index[e["sb_no"]][0]
            rem = next((r for r in rems if e.get("irm_no") and r["irm_no"] == e["irm_no"]), None)
            item = {**e, "invoice_key": key, "remittance_id": rem["id"] if rem else None,
                    "doc_id": doc.get("id"),
                    "cancelled": str(e.get("status") or "").upper().startswith("CANCEL")}
            out.append(item)
            if key and not item["cancelled"]:
                rows[key]["ebrcs"].append(item)
    return out


def _outstanding(row):
    got = sum(m["amount_fc"] for m in row["receipts"])
    return r2((row["value_fc"] or 0) - got), r2(got)


def _map(row, rem, amount, how):
    amount = r2(min(amount, rem["available_fc"] or 0))
    if amount <= 0:
        return
    rem["available_fc"] = r2(rem["available_fc"] - amount)
    rem["mapped"].append({"invoice_key": row["key"], "inv_no": row["inv_no"], "amount_fc": amount,
                          "how": how})
    row["receipts"].append({"remittance_id": rem["id"], "amount_fc": amount, "how": how,
                            "date": rem["date"], "remitter": rem["remitter"], "irm_no": rem["irm_no"],
                            "ref": rem["ref"]})


def _map_money(rows, rems, ebrcs, decisions, find):
    live = {r["id"]: r for r in rems if not r["cancelled"]}
    # (a) an eBRC names the invoice and the remittance: that is proof.
    for e in ebrcs:
        rem = live.get(e["remittance_id"])
        if e["invoice_key"] and rem and not e["cancelled"] and rem["currency"] == rows[e["invoice_key"]]["currency"]:
            _map(rows[e["invoice_key"]], rem, e.get("realised_fc") or rem["available_fc"], "eBRC")
    # (b) the user said so: a payment split over the invoices chosen for it,
    #     or one suggested invoice confirmed. A split is the user's whole
    #     answer for that payment, so nothing below adds to it.
    for fp, dec in decisions.items():
        ctx = dec.get("context") or {}
        rem = live.get(ctx.get("remittance"))
        if dec.get("action") != "match" or ctx.get("kind") != "settle" or not rem:
            continue
        rem["chosen"] = True
        for s in ctx.get("splits") or []:
            row = rows.get(s.get("invoice"))
            if row and rem["currency"] == row["currency"]:
                out, _ = _outstanding(row)
                _map(row, rem, min(s.get("amount") or 0, max(out, 0)), "you confirmed")
    for fp, dec in decisions.items():
        ctx = dec.get("context") or {}
        if dec.get("action") != "match" or ctx.get("kind") != "payment":
            continue
        rem, row = live.get(ctx.get("remittance")), rows.get(ctx.get("invoice"))
        if rem and row and rem["currency"] == row["currency"]:
            out, _ = _outstanding(row)
            _map(row, rem, ctx.get("amount") or min(out, rem["available_fc"]), "you confirmed")
    # (c) the bank advice names the invoice.
    for rem in live.values():
        if rem["chosen"]:
            continue
        key = find(rem["invoice_ref"]) if rem["invoice_ref"] else None
        if key and rem["currency"] == rows[key]["currency"]:
            out, _ = _outstanding(rows[key])
            _map(rows[key], rem, min(out, rem["available_fc"]), "named on the bank advice")


def _suggest(rows, rems, rules, decisions):
    """The likeliest invoice for each remittance with money left on it - or,
    when no one invoice fits its amount, the run of one buyer's invoices that
    together do (a buyer paying three bills at once).

    Only an invoice in the same currency with money still to come qualifies;
    the buyer's name, the amount and the order of events rank the candidates.
    Nothing is applied - each becomes a question for the user, and a pairing
    the user turned down is not offered again.
    """
    out = []
    tol = rules["bank_charge_tolerance"]
    for rem in rems:
        if rem["cancelled"] or rem["chosen"] or (rem["available_fc"] or 0) <= 0.005:
            continue
        cands, open_ = [], []
        for row in rows.values():
            if not row["sb"] or row["currency"] != rem["currency"]:
                continue
            refused = decisions.get(fingerprint("EX", "pay_confirm", row["key"], rem["id"])) or {}
            if refused.get("action") == "ignore":
                continue
            left, _ = _outstanding(row)
            if left <= 0.005:
                continue
            name = max(party_sim(rem["remitter"], row["buyer"]),
                       max((party_sim(rem["remitter"], b.get("consignee")) for b in row["bls"]), default=0))
            fits = abs(left - rem["available_fc"]) <= max(left * tol["pct"] / 100, 0)
            near = abs(left - rem["available_fc"]) <= tol["max_fc"]
            after = _d(rem["date"]) and _d(row["sb"]["sb_date"]) and _d(rem["date"]) >= _d(row["sb"]["sb_date"])
            score = name * 3 + (2 if fits else 1 if near else 0) + (0.5 if after else 0)
            open_.append({"key": row["key"], "inv_no": row["inv_no"], "buyer": row["buyer"],
                          "shipped": row["sb"]["sb_date"], "room": left, "likely": name >= 0.6, "name": name})
            if name >= 0.6 or fits or near:
                cands.append((score, name, left, row))
        cands.sort(key=lambda c: -c[0])
        refused = decisions.get(fingerprint("EX", "pay_confirm", "", rem["id"], "set")) or {}
        group = None if refused.get("action") == "ignore" else _set_for(rem["available_fc"], open_, tol)
        if group:
            mine = [c for c in open_ if c["key"] in dict(group)]
            out.append({"remittance": rem, "rows": [rows[k] for k, _ in group], "splits": group,
                        "name_sim": max(c["name"] for c in mine), "left": r2(sum(c["room"] for c in mine))})
            continue
        if not cands:
            continue
        score, name, left, row = cands[0]
        amount = r2(min(left, rem["available_fc"]))
        out.append({"remittance": rem, "rows": [row], "splits": [(row["key"], amount)], "name_sim": name,
                    "left": left, "others": len(cands) - 1})
    return out


def _set_for(amount, cands, tol):
    """Two or more of one buyer's invoices that together make up a payment: a
    run of them, oldest first, coming to the payment or a little more - the
    banks' charges. The buyer the remitter's name matches is looked at first;
    two runs that both fit are a guess, so then nothing is offered. Returns
    [(invoice key, amount)] filled oldest first, or None.

    Each candidate is {"key", "inv_no", "buyer", "shipped", "room", "likely"}.
    """
    # ponytail: runs only, not every combination - a buyer paying bills 1 and
    # 3 but not 2 is left to the settle window.
    if any(abs(c["room"] - amount) <= max(c["room"] * tol["pct"] / 100, tol["max_fc"]) for c in cands):
        return None                                    # one invoice fits by itself
    groups = defaultdict(list)
    for c in cands:
        groups[re.sub(r"[^A-Z0-9]", "", (c["buyer"] or "").upper())].append(c)
    runs = []
    for group in groups.values():
        group.sort(key=lambda c: (c["shipped"] or c.get("inv_date") or "", c["inv_no"] or ""))
        for i in range(len(group)):
            total = 0.0
            for j in range(i, len(group)):
                total = r2(total + group[j]["room"])
                short = r2(total - amount)
                if short > min(total * tol["pct"] / 100, tol["max_fc"]):
                    break
                if j > i and short >= -0.005:
                    runs.append(group[i:j + 1])
    liked = [r for r in runs if any(c["likely"] for c in r)]
    pick = liked or runs
    if len(pick) != 1:
        return None
    out, left = [], amount
    for c in pick[0]:
        take = r2(min(c["room"], left))
        out.append((c["key"], take))
        left = r2(left - take)
    return out


def settle_options(view, rem_id, rules=None):
    """What the settle window offers for one payment: every invoice in its
    currency that can still take money, the likeliest first (the invoice the
    advice names, then the buyer's own, each oldest first), with what the
    payment puts on each now and a split to start from - a run of one buyer's
    invoices that makes up the payment when there is one (_set_for), else the
    likeliest filled oldest first.

    `room` is what an invoice can take from this payment: its value less what
    other payments and this payment's eBRCs already cover. What the user or
    the advice put there before does not count against it - that is what the
    window is for changing.
    """
    rules = rules or load_rules()
    rem = next((r for r in view["remittances"] if r["id"] == rem_id), None)
    if rem is None:
        return None
    fixed, now = defaultdict(float), defaultdict(float)
    for m in rem["mapped"]:
        (fixed if m["how"] == "eBRC" else now)[m["invoice_key"]] += m["amount_fc"]
    free = r2((rem["amount_fc"] or 0) - sum(fixed.values()))
    named = ocr_key(rem["invoice_ref"]) if rem["invoice_ref"] else None
    cands = []
    for row in view["invoices"]:
        if rem["cancelled"] or row["currency"] != rem["currency"] or not row["value_fc"]:
            continue
        others = sum(m["amount_fc"] for m in row["receipts"] if m["remittance_id"] != rem_id)
        room = max(r2(row["value_fc"] - others - fixed.get(row["key"], 0)), 0.0)
        if room <= 0.005 and not now.get(row["key"]):
            continue
        name = max([party_sim(rem["remitter"], row["buyer"])]
                   + [party_sim(rem["remitter"], b.get("consignee")) for b in row["bls"]])
        is_named = named is not None and named in (row["key"], ocr_key(row["inv_no"]))
        cands.append({"key": row["key"], "inv_no": row["inv_no"], "inv_date": row["inv_date"],
                      "buyer": row["buyer"], "shipped": (row["sb"] or {}).get("sb_date"),
                      "value_fc": row["value_fc"], "room": room, "now": r2(now.get(row["key"], 0)),
                      "named": is_named, "likely": is_named or name >= 0.6, "suggest": 0.0})
    tol = rules["bank_charge_tolerance"]
    # A run of one buyer's invoices that makes up the payment is the best
    # guess there is - it goes to the top, filled in.
    group = None if any(c["now"] for c in cands) else dict(
        _set_for(free, [c for c in cands if c["room"] > 0.005], tol) or [])
    for c in cands:
        c["likely"] = c["likely"] or c["key"] in (group or {})
    cands.sort(key=lambda c: (not c["likely"], not c["named"], c["shipped"] or c["inv_date"] or "9999",
                              c["inv_no"] or ""))
    if group is None:
        for c in cands:
            c["suggest"] = c["now"]
    elif group:
        for c in cands:
            c["suggest"] = group.get(c["key"], 0.0)
    else:
        pool = [c for c in cands if c["likely"]]
        if not pool:        # no name to go on: only an amount that fits exactly, allowing for bank charges
            fits = [c for c in cands if -0.005 <= c["room"] - free <= tol["max_fc"]]
            pool = fits if len(fits) == 1 else []
        left = free
        for c in pool:
            c["suggest"] = r2(min(c["room"], left))
            left = r2(left - c["suggest"])
            if left <= 0.005:
                break
    keep = ("id", "date", "remitter", "currency", "amount_fc", "ref", "irm_no", "invoice_ref",
            "advance", "cancelled", "chosen")
    return {"remittance": {k: rem[k] for k in keep}, "free": free,
            "fixed": [{"inv_no": next((m["inv_no"] for m in rem["mapped"] if m["invoice_key"] == k), k),
                       "amount_fc": r2(v)} for k, v in fixed.items()],
            "candidates": cands}


def check_splits(options, splits):
    """The user's split, checked before it is kept: only invoices the window
    offered, none given more than it can still take, and no more in all than
    the payment has to place. Returns the split cleaned up, or raises
    ValueError saying what is wrong."""
    if options["remittance"]["cancelled"]:
        raise ValueError("That payment has been cancelled, so it cannot be set against an invoice.")
    cur, offered = options["remittance"]["currency"], {c["key"]: c for c in options["candidates"]}
    out, total = [], 0.0
    for s in splits:
        amount = s.get("amount") if isinstance(s, dict) else None
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount < 1e12:
            raise ValueError("Each amount has to be a number, zero or more.")
        amount = r2(amount)
        if amount <= 0:
            continue
        c = offered.get(s.get("invoice"))
        if c is None:
            raise ValueError(f"One of those invoices cannot take this payment: it is not in {cur}, "
                             "or nothing is left to come on it.")
        if amount > c["room"] + 0.005:
            raise ValueError(f"Invoice {c['inv_no']} has only {_fc(c['room'], cur)} left to come.")
        if any(x["invoice"] == c["key"] for x in out):
            raise ValueError(f"Invoice {c['inv_no']} is listed twice.")
        out.append({"invoice": c["key"], "amount": amount})
        total = r2(total + amount)
    if total > options["free"] + 0.005:
        raise ValueError(f"That sets {_fc(total, cur)} against invoices, but the payment has only "
                         f"{_fc(options['free'], cur)} to place.")
    return out


# ---------------------------------------------------------------------------
# The payment deadline on each invoice
# ---------------------------------------------------------------------------

def _deadline(row, rules, today, bls):
    row["due"] = None
    if not row["sb"] or not row["value_fc"] or not row["currency"]:
        return                            # a register line without its value cannot be tracked
    basis = rules["counted_from"]
    anchor = {"sb_date": row["sb"]["sb_date"], "leo_date": row["sb"].get("leo_date"),
              "bl_date": next((b.get("sob_date") for b in row["bls"] if b.get("sob_date")), None)}.get(basis)
    used = basis if anchor else "sb_date"
    anchor = anchor or row["sb"]["sb_date"]
    shipped = _d(anchor)
    if not shipped:
        return
    due, months, rule = due_date(shipped, row["currency"], rules)
    bank_due = _d((row.get("bank_edpms") or {}).get("due"))
    if bank_due and bank_due > due:
        due, rule = bank_due, f"{rule}, extended by your bank to {_day(bank_due)} (EDPMS)"
    left, got = _outstanding(row)
    tol = rules["bank_charge_tolerance"]
    small = 0 < left <= min((row["value_fc"] or 0) * tol["pct"] / 100, tol["max_fc"])
    settled = got > 0 and (left <= 0.005 or small)
    days = (due - today).days
    rate = row["rate"] or 0
    state = ("paid" if settled or (row["value_fc"] and left <= 0.005) else
             "overdue_year" if days < -rules["advance_or_lc_after_days_overdue"] else
             "overdue" if days < 0 else "due_soon" if days <= rules["due_soon_days"] else "open")
    row["due"] = {
        "date": due.isoformat(), "months": months, "rule": rule, "anchor": anchor,
        "anchor_basis": used, "anchor_words": rules["counted_from_choices"][used],
        "days_left": days, "state": state, "received_fc": got,
        "outstanding_fc": 0.0 if settled else max(left, 0.0),
        "outstanding_inr": 0.0 if settled else r2(max(left, 0.0) * rate),
        "short_by_charges": r2(left) if settled and small else 0.0,
        "declaration_ok": (not settled and 0 < max(left, 0.0) * rate <= rules["declaration_limit_inr"]
                           and today >= date.fromisoformat(rules["declaration_from"])),
        "part_paid": got > 0 and not settled,
    }


# ---------------------------------------------------------------------------
# The IGST refund and the drawback on each shipping bill
# ---------------------------------------------------------------------------

def _pick(statuses, kind):
    """ICEGATE's latest word on one refund: a scroll beats a code, and a later
    download beats an earlier one."""
    got = [x for x in statuses if x.get("kind") == kind]
    paid = [x for x in got if x.get("scroll_no") or x.get("paid_date")]
    return (paid or got or [None])[-1]


def _refund(row, rules, today, marks=None):
    sb, own = row["sb"] or {}, row["own"]
    started = _d(sb.get("leo_date") or sb.get("sb_date") or row["inv_date"])
    age = (today - started).days if started else None

    # IGST: paid on the shipping bill, to come back from customs.
    due = sb.get("igst_amount") or max((o.get("igst") or 0 for o in own), default=0)
    st = _pick(row["refund_status"], "igst")
    reg = next((o for o in own if o.get("igst_recd") or o.get("igst_date")), None)
    code, code_from = None, None
    if st and st.get("code"):
        code, code_from = st["code"], f"ICEGATE's refund status ({st['doc_name']})"
    elif any(o.get("codes") for o in own):
        o = next(o for o in own if o.get("codes"))
        code, code_from = o["codes"][-1], f"your register ({o['sheet']}, row {o['row']})"
    elif re.search(r"SB\d{3}", (row.get("sb_status") or {}).get("error") or ""):
        code = re.search(r"SB\d{3}", row["sb_status"]["error"]).group(0)
        code_from = "ICEGATE's shipping bill status sheet"
    igst = {"due": r2(due), "code": code, "code_from": code_from, "recd": None, "recd_date": None,
            "source": None, "scroll_no": (st or {}).get("scroll_no"), "scroll_date": (st or {}).get("scroll_date"),
            "status_text": (st or {}).get("status"), "age": age}
    if not due:
        igst["state"] = "na"
    elif reg:
        recd = reg.get("igst_recd")
        igst.update(recd=recd if recd is not None else due, recd_date=reg.get("igst_date"),
                    source=f"your register ({reg['sheet']}, row {reg['row']})")
        # Refunds are rounded item by item; only a real gap is a finding.
        short = recd is not None and due - recd > rules.get("refund_short_tolerance_inr", 100)
        igst["state"] = "short" if short else "received"
    elif st and (st.get("scroll_no") or st.get("paid_date")):
        igst.update(recd=st.get("amount") or due, recd_date=st.get("paid_date") or st.get("scroll_date"),
                    source=f"ICEGATE ({st['doc_name']})")
        igst["state"] = "received"
    elif code and code not in ("SB000", "SB004"):
        igst["state"] = "held"
    elif age is not None and age <= rules["refund_grace_days"]:
        igst["state"] = "waiting"
    elif not (own or st or code):
        igst["state"] = "unknown"         # nothing says either way: a look-up, not a finding
    else:
        igst["state"] = "pending"

    # Drawback: claimed on the shipping bill, paid into the drawback account.
    due = sb.get("dbk_amount") or max((o.get("dbk") or 0 for o in own), default=0)
    st = _pick(row["refund_status"], "dbk")
    reg = next((o for o in own if o.get("dbk_date")), None)
    dbk = {"due": r2(due), "recd_date": None, "source": None, "status_text": (st or {}).get("status"),
           "status_from": f"ICEGATE's drawback status ({st['doc_name']})" if st else None,
           "scroll_no": (st or {}).get("scroll_no"), "scroll_date": (st or {}).get("scroll_date"), "age": age,
           "serious": age is not None and age > rules["dbk_serious_after_days"]}
    if not due:
        dbk["state"] = "na"
    elif reg:
        dbk.update(recd_date=reg["dbk_date"], source=f"your register ({reg['sheet']}, row {reg['row']})",
                   state="received")
    elif st and (st.get("scroll_no") or st.get("paid_date")):
        dbk.update(recd_date=st.get("paid_date") or st.get("scroll_date"), source=f"ICEGATE ({st['doc_name']})",
                   state="received")
    elif st and st.get("status"):
        dbk["state"] = "held"
    elif age is not None and age <= rules["dbk_grace_days"]:
        dbk["state"] = "waiting"
    elif not (own or st):
        dbk["state"] = "unknown"
    else:
        dbk["state"] = "pending"

    # The user's own yes or no counts over the documents: a refund they say
    # came back is received, one they say did not is still owed - held if
    # ICEGATE gave a reason, late or not yet due by its age otherwise.
    for what, x, grace in (("igst", igst, rules["refund_grace_days"]), ("dbk", dbk, rules["dbk_grace_days"])):
        said = (marks or {}).get((row["key"], what), (None, None))[0]
        if not said or x["state"] == "na":
            continue
        x["manual"], x["doc_state"] = said, x["state"]
        if said == "yes" and x["state"] not in ("received", "short"):
            x.update(state="received", recd_date=None, source="your own mark")
            if what == "igst":
                x["recd"] = x["due"]
        elif said == "no" and x["state"] in ("received", "short", "unknown"):
            held = x["code"] not in (None, "SB000", "SB004") if what == "igst" else bool(x["status_text"])
            x.update(state="held" if held else "waiting" if age is not None and age <= grace else "pending",
                     recd_date=None, source=None)
            if what == "igst":
                x["recd"] = None
    row["refund"] = {"igst": igst, "dbk": dbk}


# ---------------------------------------------------------------------------
# What needs a look, and why
# ---------------------------------------------------------------------------

def _rec(kind, tone, row, why, amount=None, currency="INR", date_=None, who=None, inr_value=None,
         extra=None):
    fp = fingerprint("EX", kind, row["key"] if row else "", *(extra or ()))
    return {"fp": fp, "status": kind, "tone": tone, "inv_no": row["inv_no"] if row else None,
            "key": row["key"] if row else None, "date": date_ or (row and (row["sb"] and row["sb"]["sb_date"]
                                                                         or row["inv_date"])),
            "amount": amount, "currency": currency, "inr": inr_value if inr_value is not None else (
                amount if currency == "INR" else None),
            "amount_text": _rs(amount) if currency == "INR" and amount is not None else _fc(amount, currency)
            if amount is not None else "",
            "who": who or _who(row), "why": why}


def _who(row):
    if not row:
        return ""
    bits = [row.get("buyer"), f"invoice {row['inv_no']}"]
    if row["sb"]:
        bits.append(f"SB {row['sb']['sb_no']} · {row['sb']['port']}")
    return " · ".join(b for b in bits if b)


def _why(title, what, checks=(), nearest=None, impact=None, todo=None, notes=()):
    return {"title": title, "what": what, "checks": list(checks), "nearest": nearest,
            "impact": impact, "todo": todo, "notes": list(notes)}


def _igst(row):
    return (row["sb"] or {}).get("igst_amount") or 0


def _issues(row, rules, today, covered, gst_gstin, decisions):
    out = []
    sb, gst, inv = row["sb"], row["gst"], row["inv_no"]
    real_sb = sb if sb and not sb["from_register"] else None      # the bill itself, not a record of it
    fee, amend_by = rules["sb005_fee_inr"], rules["amend_by"]
    wpay = (sb or {}).get("scheme") == "WPAY"
    refund = _igst(row)

    # --- the GST side: will the refund go through? ---------------------------
    # The user's yes stands in for a return not added here; their no stands
    # in for one that is not filed yet.
    mark = row.get("gst_mark")
    in_month = bool(sb and sb.get("inv_date") and sb["inv_date"][:7] in covered)
    if sb and not gst and mark != "yes" and (in_month or mark == "no"):
        out.append(_rec("not_in_gst", SERIOUS if wpay else LOOK, row, _why(
            f"Invoice {inv} went out on shipping bill {sb['sb_no']} but is not in your GSTR-1"
            + (f" for {_month(sb['inv_date'][:7])}." if sb.get("inv_date") else "."),
            f"Shipping bill {sb['sb_no']} of {_day(sb['sb_date'])} ({sb['port']}) carries invoice {inv}"
            + (f" dated {_day(sb['inv_date'])}" if sb.get("inv_date") else "") + ". "
            + ("The GSTR-1 you added for that month has no Table 6A line for it." if in_month
               else "You marked it as not filed in GSTR-1 yet."),
            [f"Invoice number: no 6A line has {inv}, or anything close to it.",
             f"Shipping bill: no 6A line quotes shipping bill {sb['sb_no']}."] if in_month
            else [f"GSTR-1: you marked invoice {inv} as not filed."],
            None,
            (f"The IGST refund of {_rs(refund)} cannot start: customs only pays against invoices "
             "GSTN has sent to ICEGATE from Table 6A.") if wpay else
            "Every export has to be reported in Table 6A, including exports under LUT.",
            f"Add it to Table 6A with shipping bill {sb['sb_no']}, {_day(sb['sb_date'])}, port "
            f"{sb['port']} - in this month's GSTR-1, or through Table 9A by {amend_by}."),
            refund if wpay else None, inr_value=refund))
    elif not sb and not gst and mark == "no":
        out.append(_rec("not_in_gst", LOOK, row, _why(
            f"Invoice {inv} is not in your GSTR-1 yet.",
            f"You marked invoice {inv}" + (f" of {_day(row['inv_date'])}" if row["inv_date"] else "")
            + " as not filed in GSTR-1.",
            [f"GSTR-1: you marked invoice {inv} as not filed."], None,
            "Every export has to be reported in Table 6A, and an IGST refund only starts once it is.",
            f"Report it in Table 6A of the GSTR-1 for its month, or through Table 9A by {amend_by}. Once it is "
            "filed, mark it filed here or add the return."), date_=row["inv_date"]))

    for line in row["gst_lines"][1:]:
        if clean_inv(line["inum"]) == clean_inv(row["gst_lines"][0]["inum"]):
            out.append(_rec("gst_twice", LOOK, row, _why(
                f"Invoice {line['inum']} is in Table 6A twice.",
                f"Two 6A lines carry invoice {line['inum']}"
                f"{_paren(line.get('doc_name'), ' (in ', ')')}.",
                ["Invoice number: the same on both lines."], None,
                "The export is counted twice and the second line can never match a shipping bill.",
                "Remove the duplicate through Table 9A."), extra=(line["inum"], line.get("idt"))))
            break

    if real_sb and gst:
        exact_no = str(gst["inum"]).strip() == str(sb["inv_no"]).strip()
        same_date = not gst.get("idt") or not sb.get("inv_date") or gst["idt"] == sb["inv_date"]
        sb_match = (gst.get("sbnum") == sb["sb_no"] and (gst.get("sbpcode") or "") == sb["port"]
                    and (not gst.get("sbdt") or gst["sbdt"] == sb["sb_date"]))

        if not gst.get("sbnum"):
            out.append(_rec("sb001", SERIOUS if wpay else LOOK, row, _why(
                f"Table 6A has no shipping bill details for invoice {gst['inum']}.",
                f"The 6A line for invoice {gst['inum']} leaves the shipping bill number, date and port blank.",
                [f"Shipping bill: {sb['sb_no']} of {_day(sb['sb_date'])} at {sb['port']} carries this invoice."],
                None, "GSTN does not send an invoice to ICEGATE without shipping bill details, so no refund starts."
                if wpay else "The export is reported without the shipping bill that proves it.",
                f"Fill in shipping bill {sb['sb_no']}, {_day(sb['sb_date'])}, port {sb['port']} on the 6A line "
                f"(Table 9A by {amend_by}). It costs nothing."), refund if wpay else None, inr_value=refund))
        elif not sb_match:
            diffs = []
            if gst.get("sbnum") != sb["sb_no"]:
                diffs.append(f"Shipping bill number: 6A says {gst['sbnum']}, the shipping bill is {sb['sb_no']}.")
            if (gst.get("sbpcode") or "") != sb["port"]:
                diffs.append(f"Port: 6A says {gst.get('sbpcode') or 'nothing'}, the shipping bill is {sb['port']}.")
            if gst.get("sbdt") and gst["sbdt"] != sb["sb_date"]:
                diffs.append(f"Date: 6A says {_day(gst['sbdt'])}, the shipping bill is {_day(sb['sb_date'])}.")
            out.append(_rec("sb001", SERIOUS if wpay else LOOK, row, _why(
                f"Table 6A gives the wrong shipping bill for invoice {gst['inum']}.",
                f"Invoice {gst['inum']} went out on shipping bill {sb['sb_no']} of {_day(sb['sb_date'])} at "
                f"{sb['port']}, but its 6A line points somewhere else.", diffs,
                f"The shipping bill for this invoice is {sb['sb_no']} ({sb['port']}, {_day(sb['sb_date'])}).",
                "ICEGATE matches on port, number and date together, so this will come back as SB001 "
                "(invalid shipping bill details) and the refund will not be paid." if wpay else
                "The export is reported against a shipping bill that does not carry it.",
                f"Correct the shipping bill details on the 6A line through Table 9A by {amend_by}. "
                "It costs nothing."), refund if wpay else None, inr_value=refund))

        if not exact_no or not same_date:
            checks = []
            if not exact_no:
                checks.append(f"Invoice number: 6A says {gst['inum']}, the shipping bill says {sb['inv_no']}.")
            if not same_date:
                checks.append(f"Invoice date: 6A says {_day(gst['idt'])}, the shipping bill says {_day(sb['inv_date'])}.")
            route, fix = _sb005_route(row, gst, sb)
            out.append(_rec("sb005", SERIOUS if wpay else LOOK, row, _why(
                f"The shipping bill calls invoice {gst['inum']} \"{sb['inv_no']}\"."
                if not exact_no else f"The shipping bill dates invoice {gst['inum']} differently.",
                f"Shipping bill {sb['sb_no']} of {_day(sb['sb_date'])} ({sb['port']}) and Table 6A describe the "
                f"same export, but not in the same words. ICEGATE compares them character for character.",
                checks, None,
                (f"This comes back as SB005 (invalid invoice number) and the refund of {_rs(refund)} stops "
                 "until it is fixed.") if wpay else "The export and its return do not agree, which draws a query.",
                route, [f"Fixing it through the port costs {inr(fee, 0)} per shipping bill."] if fix == "port" else
                [f"If the shipping bill is the wrong side, fixing it through the port costs {inr(fee, 0)} per "
                 "shipping bill."] if fix == "check" else []),
                refund if wpay else None, inr_value=refund))
            out[-1]["fix"] = fix

        if wpay and gst.get("exp_typ") == "WPAY" and sb.get("igst_amount") is not None and \
                abs((gst.get("iamt") or 0) - sb["igst_amount"]) > 1:
            short = r2(sb["igst_amount"] - (gst.get("iamt") or 0))
            out.append(_rec("igst_differs", SERIOUS if short > 0 else LOOK, row, _why(
                f"IGST on invoice {gst['inum']} is {_rs(gst.get('iamt'))} in 6A but {_rs(sb['igst_amount'])} "
                "on the shipping bill.",
                "Customs refunds the lower of the IGST in Table 6A and the IGST on the shipping bill.",
                [f"IGST in 6A: {_rs(gst.get('iamt'))}.", f"IGST on the shipping bill: {_rs(sb['igst_amount'])}."],
                None,
                f"The refund will be {_rs(min(sb['igst_amount'], gst.get('iamt') or 0))}, "
                f"{_rs(abs(short))} {'less than the tax you paid' if short > 0 else 'less than 6A shows'}.",
                "Correct whichever side is wrong: 6A through Table 9A, or the shipping bill through the "
                "customs officer (short shipment or a calculation error)."),
                abs(short), inr_value=abs(short)))

        if gst.get("exp_typ") and sb.get("scheme") and gst["exp_typ"].replace("WOPAY", "LUT") != sb["scheme"]:
            says = "with payment of IGST (WPAY)" if gst["exp_typ"] == "WPAY" else "under LUT, without IGST (WOPAY)"
            other = "under LUT, with no IGST on it" if sb["scheme"] == "LUT" else f"with IGST of {_rs(refund)} paid"
            out.append(_rec("type_differs", SERIOUS, row, _why(
                f"6A reports invoice {gst['inum']} {says}, but the shipping bill went out {other}.",
                "Table 6A and the shipping bill disagree on whether IGST was paid on this export.",
                [f"6A export type: {gst['exp_typ']}.", f"Shipping bill: {sb['scheme']}."], None,
                "A refund is only paid when both say IGST was paid; otherwise the bill validates and "
                "still pays nothing.", f"Correct the export type on the 6A line through Table 9A by {amend_by}."),
                max(refund, gst.get("iamt") or 0) or None, inr_value=max(refund, gst.get("iamt") or 0)))

    if sb and gst_gstin and sb.get("gstin") and sb["gstin"] != gst_gstin:
        same_pan = sb["gstin"][2:12] == gst_gstin[2:12]
        out.append(_rec("sb003", SERIOUS if wpay else LOOK, row, _why(
            f"Shipping bill {sb['sb_no']} carries GSTIN {sb['gstin']}, not the {gst_gstin} on your GSTR-1.",
            "ICEGATE matches the GSTIN first; a different one means the invoice is never found (SB003).",
            [f"GSTIN on the shipping bill: {sb['gstin']}.", f"GSTIN on the return: {gst_gstin}.",
             "PAN: the same, so this is another branch of the same business." if same_pan else
             "PAN: different - this is not the same business."], None,
            f"The refund of {_rs(refund)} is held." if wpay else None,
            "Give customs an undertaking, through the officer interface, that the shipping bill was filed "
            "under another registration of the same PAN - the draft is under Drafts." if same_pan else
            "Check which registration made this export; correct the return or approach customs."),
            refund if wpay else None, inr_value=refund))

    # --- the paperwork: shipping bill and bill of lading ---------------------
    started = _d(row["inv_date"])
    if not sb and (gst or row["invoice_doc"]) and started and (today - started).days > rules["sb_grace_days"]:
        out.append(_rec("no_sb", LOOK, row, _why(
            f"No shipping bill on file for invoice {inv}.",
            f"Invoice {inv} of {_day(row['inv_date'])} is "
            + ("in Table 6A" if gst else "on file") + " but no shipping bill carrying it has been added.",
            [f"Invoice number: no shipping bill here lists {inv}."]
            + ([f"Shipping bill number: 6A quotes {gst['sbnum']}, which is not among the shipping bills added."]
               if gst and gst.get("sbnum") else []), None,
            "Without the shipping bill the export cannot be proved, and no payment deadline can be worked out.",
            "Download the shipping bill from ICEGATE (or ask your customs broker) and add it here. If the "
            "goods have not gone yet, nothing needs doing."), date_=row["inv_date"]))

    if real_sb and not row["bls"]:
        age = (today - _d(sb["sb_date"])).days if _d(sb["sb_date"]) else 0
        if age > rules["bl_grace_days"]:
            word = "airway bill" if sb.get("mode") == "AIR" else "bill of lading"
            out.append(_rec("no_bl", LOOK, row, _why(
                f"No {word} yet for invoice {inv}, which went out on {_day(sb['sb_date'])}.",
                f"Shipping bill {sb['sb_no']} ({sb['port']}) cleared invoice {inv} {_days(age)} ago; no "
                f"{word} naming it has been added.",
                [f"Shipping bill number: no {word} here quotes {sb['sb_no']}.",
                 f"Invoice number: none quotes {inv}."]
                + ([f"Container: none carries {', '.join(sb['containers'])}."] if sb["containers"] else []),
                None, f"The buyer's bank needs the {word} to release payment, and the shipped-on-board date "
                "on it is the proof of export.",
                f"Ask your forwarder for the {word} and add it here.")))
    return out


def _sb005_route(row, gst, sb):
    """Which fix an SB005 needs: "9a" when Table 6A is the wrong side, "port"
    when the shipping bill is, "check" when the documents on file cannot say."""
    doc = ocr_key((row["invoice_doc"] or {}).get("inv_no"))
    if doc and doc == ocr_key(sb["inv_no"]) and doc != ocr_key(gst["inum"]):
        return ("Your invoice agrees with the shipping bill, so the slip is in Table 6A: correct it "
                "through Table 9A. It costs nothing."), "9a"
    if doc and doc == ocr_key(gst["inum"]) and doc != ocr_key(sb["inv_no"]):
        return ("Your invoice agrees with Table 6A, so the shipping bill is the one that is wrong: ask the port "
                "to correct it through the officer interface (a letter, the Annexure A table and the challan)."), "port"
    if str(gst["inum"]).strip() == str(sb["inv_no"]).strip():
        return ("The invoice numbers agree and only the date differs. If Table 6A has the wrong date, correct it "
                "through Table 9A for nothing; if the shipping bill has, the port route applies. Your invoice "
                "settles which - add it here."), "check"
    digits = lambda s: re.sub(r"\D", "", str(s)).lstrip("0")
    if digits(gst["inum"]) == digits(sb["inv_no"]):
        return ("The digits agree and only the letters or separators differ, which is a typing slip. If "
                "Table 6A matches your GST invoice, the shipping bill is the one that is wrong: ask the port "
                "to correct it through the officer interface (a letter, the Annexure A table and the "
                "challan). If 6A is the one that is wrong, correct it through Table 9A for nothing. Your "
                "invoice settles which - add it here."), "check"
    return ("The numbers are different, which usually means the shipping bill carries a commercial invoice "
            "number from a second series. GSTR-1 cannot carry that number in place of the GST invoice, so the "
            "fix is through the port: a request letter, the Annexure A concordance table and the challan."), "port"


def _and(items):
    items = list(items)
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


def _money_issues(rows, rems, suggestions, rules):
    out = []
    waiting = {k: (s, a) for s in suggestions for k, a in s["splits"]}
    for row in rows.values():
        due = row.get("due")
        if not due or due["state"] in ("paid", "open"):
            continue
        cur = row["currency"]
        left = due["outstanding_fc"]
        got = due["received_fc"]
        paid_words = (f"{_fc(got, cur)} of {_fc(row['value_fc'], cur)} has come in; {_fc(left, cur)} has not."
                      if got else f"None of the {_fc(row['value_fc'], cur)} has come in yet.")
        checks = [f"Deadline: {_day(due['date'])} - {due['months']} months from {due['anchor_words']} "
                  f"({_day(due['anchor'])}), under {due['rule']}.",
                  f"Received: {_fc(got, cur)}" + (f" from {len(row['receipts'])} remittance"
                                                  f"{'s' if len(row['receipts']) != 1 else ''}." if got else ".")]
        notes = []
        if due["declaration_ok"]:
            notes.append(f"At {_rs(due['outstanding_inr'])} it is within the {_rs(rules['declaration_limit_inr'])} "
                         "limit, so the bank can close or reduce it on your declaration.")
        if row["scheme"] == "WPAY" and _igst(row):
            share = left / row["value_fc"] if row["value_fc"] else 0
            notes.append(f"Rule 96B: if it is not received by the deadline, {_rs(_igst(row) * share)} of the "
                         f"IGST refund has to be paid back within {rules['rule_96b_days']} days, with interest.")
        elif row["scheme"] in ("LUT", "WOPAY"):
            notes.append("Rule 96A: an export under LUT that is not paid for in time becomes liable to IGST, "
                         "with interest.")
        if row["key"] in waiting:
            s, amount = waiting[row["key"]]
            rem = s["remittance"]
            notes.insert(0, (f"A payment of {_fc(amount, cur)} received on {_day(rem['date'])} looks like it is "
                             "for this invoice" if len(s["rows"]) == 1 else
                             f"{_fc(amount, cur)} of the {_fc(rem['available_fc'], cur)} received on {_day(rem['date'])} "
                             f"looks like it is for this invoice, with {_and(r['inv_no'] for r in s['rows'] if r is not row)}")
                         + " - confirm it under Payment to confirm and this will clear.")
        if due["state"] in ("overdue", "overdue_year"):
            late = -due["days_left"]
            year = due["state"] == "overdue_year"
            out.append(_rec("overdue", SERIOUS, row, _why(
                f"Payment for invoice {row['inv_no']} was due on {_day(due['date'])}, {_days(late)} ago.",
                paid_words, checks, None,
                ("It is more than a year overdue, so further exports need full advance payment or an "
                 "irrevocable letter of credit until it is settled.") if year else
                "The bank will report it as overdue in EDPMS.",
                "Chase the buyer. If more time is needed, ask your bank for an extension before anything else; "
                "if part of it will never come, the bank can write it off on your declaration.", notes),
                left, cur, date_=due["date"], inr_value=due["outstanding_inr"]))
        else:
            out.append(_rec("due_soon", LOOK, row, _why(
                f"Payment for invoice {row['inv_no']} is due by {_day(due['date'])} - {_days(due['days_left'])} left.",
                paid_words, checks, None,
                f"If it has not arrived by then, the bank reports the bill as overdue.",
                "Remind the buyer now. If it will be late, ask your bank for an extension before the date.",
                notes), left, cur, date_=due["date"], inr_value=due["outstanding_inr"]))

    offered = {s["remittance"]["id"] for s in suggestions}
    for s in suggestions:
        rem, row = s["remittance"], s["rows"][0]
        if len(s["rows"]) > 1:
            out.append(_set_record(rem, s))
            continue
        checks = [f"Currency: both {rem['currency']}.",
                  f"Amount: {_fc(rem['available_fc'], rem['currency'])} is free on the remittance; "
                  f"{_fc(s['left'], row['currency'])} is still due on the invoice.",
                  f"Buyer: {rem['remitter'] or 'unnamed remitter'} against {row['buyer'] or 'an unnamed buyer'} "
                  f"({round(s['name_sim'] * 100)}% alike).",
                  f"Order: the money arrived on {_day(rem['date'])}; the goods went on {_day(row['sb']['sb_date'])}."]
        if rem["advance"] and (row["sb"]["sb_date"] or "") < (rem["date"] or ""):
            checks.append(f"Purpose code: {rem['purpose_code'] or 'an advance'} - the bank booked it as an advance, "
                          "though the goods went first.")
        rec = _rec("pay_confirm", LOOK, row, _why(
            f"{_fc(rem['available_fc'], rem['currency'])} received on {_day(rem['date'])} looks like payment "
            f"for invoice {row['inv_no']}.",
            f"A remittance from {rem['remitter'] or 'an unnamed remitter'}"
            f"{_paren(rem['irm_no'] or rem['ref'], ' (', ')')} has not been set against any invoice yet.",
            checks,
            f"{s['others']} other invoice{'s' if s['others'] != 1 else ''} could also fit." if s["others"] else None,
            "Until it is set against an invoice, the invoice still shows as unpaid and no eBRC can be made for it.",
            f"If it is for invoice {row['inv_no']}, confirm it. Nothing is matched until you do."),
            s["splits"][0][1], rem["currency"], date_=rem["date"], who=_payer(rem), extra=(rem["id"],))
        rec["counterpart"] = {"kind": "payment", "remittance": rem["id"], "invoice": row["key"],
                              "amount": s["splits"][0][1]}
        out.append(rec)

    for rem in rems:
        if rem["cancelled"] or rem["id"] in offered or (rem["available_fc"] or 0) <= 0.005:
            continue
        adv = rem["advance"]
        out.append(_rec("advance" if adv else "pay_unplaced", LOOK, None, _why(
            (f"An advance of {_fc(rem['available_fc'], rem['currency'])} from {rem['remitter'] or 'a buyer'} "
             "has no shipment against it yet.") if adv else
            f"{_fc(rem['available_fc'], rem['currency'])} received on {_day(rem['date'])} is not set against any invoice.",
            f"Received on {_day(rem['date'])}{_paren(rem['irm_no'] or rem['ref'], ' (', ')')}"
            f"{', purpose code ' + rem['purpose_code'] if rem['purpose_code'] else ''}.",
            [f"Currency: no invoice in {rem['currency']} with money still due fits it."
             if rem["currency"] else "Currency: not stated on the advice.",
             f"Remitter: {rem['remitter'] or 'not named'} matches no buyer on file."], None,
            "Advance money has to be set against the shipment it pays for before the bank can close it."
            if adv else "Money that is not set against an invoice leaves that invoice looking unpaid.",
            "When the goods for it go, add the shipping bill; the advance can then be set against it."
            if adv else "Add the shipping bill for the export it pays for, or choose the invoices it is for."),
            rem["available_fc"], rem["currency"], date_=rem["date"], who=_payer(rem), extra=(rem["id"],)))
        out[-1]["remittance"] = rem["id"]

    # An advance that pays for goods already gone: the bank has it on the
    # wrong code, and EDPMS will not close those shipping bills by itself.
    for rem in rems:
        early = [(m, rows[m["invoice_key"]]) for m in rem["mapped"] if rem["advance"] and not rem["cancelled"]
                 and m["how"] != "eBRC" and (rows[m["invoice_key"]]["sb"] or {}).get("sb_date")
                 and rows[m["invoice_key"]]["sb"]["sb_date"] < (rem["date"] or "")]
        if not early:
            continue
        cur, amount = rem["currency"], r2(sum(m["amount_fc"] for m, _ in early))
        sbs = [f"{r['sb']['sb_no']} of {_day(r['sb']['sb_date'])}" for _, r in early]
        out.append(_rec("advance_shipped", LOOK, None, _why(
            f"The bank booked the {_fc(amount, cur)} received on {_day(rem['date'])} as an advance, but it pays for "
            "goods already shipped.",
            f"The advice gives purpose code {rem['purpose_code'] or 'for an advance'}"
            f"{' (advance receipts against export contracts)' if rem['purpose_code'] == 'P0103' else ''}, "
            f"yet the goods for {_and(r['inv_no'] for _, r in early)} went before the money came.",
            [f"Purpose code: {rem['purpose_code'] or 'not given'} - for money received before the goods go.",
             "Shipped: " + "; ".join(f"{r['inv_no']} on {_day(r['sb']['sb_date'])}" for _, r in early) + ".",
             f"Received: {_day(rem['date'])}{_paren(rem['irm_no'] or rem['ref'], ' (', ')')}."], None,
            "Booked as an advance, the money is not set against these shipping bills in EDPMS, so the bank can go on "
            "showing them as unpaid and report them overdue.",
            f"Ask the bank to realise it against shipping bill{'s' if len(sbs) > 1 else ''} {_and(sbs)} in EDPMS, "
            "under purpose code P0102 (realisation of export bills). If the bank changes the code, correct the "
            "advice here too."), amount, cur, date_=rem["date"], who=_payer(rem), extra=(rem["id"],)))
    return out


def _payer(rem):
    return f"{rem['remitter'] or 'Remitter not named'} · {rem['irm_no'] or rem['ref'] or 'no reference'}"


def _set_record(rem, s):
    """The question for a payment that looks like it covers several of one
    buyer's invoices: answered in the settle window, where the split can be
    changed before it is kept."""
    cur, rows, avail = rem["currency"], s["rows"], rem["available_fc"]
    put = dict(s["splits"])
    short = r2(s["left"] - avail)
    buyers = _and(sorted({r["buyer"] or "an unnamed buyer" for r in rows}))
    checks = [f"Currency: all {cur}.",
              f"Amount: the {len(rows)} invoices have {_fc(s['left'], cur)} still to come; the payment is "
              f"{_fc(avail, cur)}" + (f", {_fc(short, cur)} less - taken as the banks' charges." if short > 0.005
                                      else ", the same."),
              f"Buyer: {rem['remitter'] or 'unnamed remitter'} against {buyers}"
              + (f" ({round(s['name_sim'] * 100)}% alike)." if s["name_sim"] else "."),
              f"Order: the money arrived on {_day(rem['date'])}; the goods went between "
              f"{_day(rows[0]['sb']['sb_date'])} and {_day(rows[-1]['sb']['sb_date'])}."]
    if rem["advance"]:
        checks.append(f"Purpose code: {rem['purpose_code'] or 'an advance'} - the bank booked it as an advance, "
                      "though the goods went first.")
    rec = _rec("pay_confirm", LOOK, None, _why(
        f"{_fc(avail, cur)} received on {_day(rem['date'])} looks like payment for invoices "
        f"{_and(r['inv_no'] for r in rows)}.",
        f"A remittance from {rem['remitter'] or 'an unnamed remitter'}{_paren(rem['irm_no'] or rem['ref'], ' (', ')')} "
        "has not been set against any invoice yet. Oldest first, it pays "
        + "; ".join(f"{r['inv_no']} {_fc(put[r['key']], cur)}" for r in rows) + ".",
        checks, None,
        "Until it is set against the invoices, they still show as unpaid and no eBRC can be made for them.",
        "If it is for these invoices, check the amounts and save them. Nothing is matched until you do."),
        avail, cur, date_=rem["date"], who=_payer(rem), extra=(rem["id"], "set"))
    rec["counterpart"] = {"kind": "set", "remittance": rem["id"],
                          "splits": [{"invoice": k, "amount": a} for k, a in s["splits"]]}
    return rec


def _ebrc_issues(rows, rems, ebrcs):
    out = []
    by_id = {r["id"]: r for r in rems}
    for row in rows.values():
        covered = defaultdict(float)
        for e in row["ebrcs"]:
            covered[e.get("remittance_id")] += e.get("realised_fc") or 0
        for m in row["receipts"]:
            if m["how"] == "eBRC" or covered.get(m["remittance_id"], 0) + 0.005 >= m["amount_fc"]:
                continue
            rem = by_id[m["remittance_id"]]
            out.append(_rec("ebrc_due", LOOK, row, _why(
                f"No eBRC yet for the {_fc(m['amount_fc'], row['currency'])} received against invoice {row['inv_no']}.",
                f"The money came in on {_day(rem['date'])}{_paren(rem['irm_no'] or rem['ref'], ' (', ')')} "
                f"and is set against invoice {row['inv_no']} ({m['how']}), but no eBRC for it has been added.",
                [f"eBRCs added for this invoice: {len(row['ebrcs'])}.",
                 "IRM number: " + (rem["irm_no"] or "not known yet - add the DGFT IRM list to get it.")], None,
                "The eBRC is the proof of realisation that RoDTEP, drawback and GST refunds ask for.",
                "Generate it on the DGFT portal. It is in the bulk sheet under Downloads, with every row checked."),
                m["amount_fc"], row["currency"], date_=rem["date"], extra=(m["remittance_id"],)))
        for e in row["ebrcs"]:
            diffs = []
            gst = row["gst"]
            if e.get("gst_inv_no") and gst and str(e["gst_inv_no"]).strip() != str(gst["inum"]).strip():
                diffs.append(f"GST invoice: the eBRC says {e['gst_inv_no']}, Table 6A says {gst['inum']}.")
            if e.get("sb_no") and row["sb"] and e["sb_no"] != row["sb"]["sb_no"]:
                diffs.append(f"Shipping bill: the eBRC says {e['sb_no']}, the invoice went on {row['sb']['sb_no']}.")
            if diffs:
                out.append(_rec("ebrc_differs", LOOK, row, _why(
                    f"eBRC {e['ebrc_no']} describes invoice {row['inv_no']} differently.",
                    "Since 13 January 2026 an eBRC carries the GST invoice; it has to match the return.", diffs, None,
                    "An eBRC that does not match can be rejected as proof of realisation.",
                    "Ask DGFT to amend the eBRC, or correct the return if that is the one that is wrong."),
                    e.get("realised_fc"), e.get("currency") or row["currency"], date_=e.get("ebrc_date"),
                    extra=(e["ebrc_no"],)))
    for e in ebrcs:
        if not e["invoice_key"] and not e["cancelled"]:
            out.append(_rec("ebrc_unplaced", LOOK, None, _why(
                f"eBRC {e['ebrc_no']} names an export that is not on file.",
                f"It refers to shipping bill {e.get('sb_no') or 'none'} and invoice "
                f"{e.get('gst_inv_no') or e.get('bill_no') or 'none'}.",
                ["Invoice: none on file matches.", "Shipping bill: none on file matches."], None,
                "Its realisation cannot be credited to any invoice here.",
                "Add the shipping bill it refers to."), e.get("realised_fc"), e.get("currency") or "",
                date_=e.get("ebrc_date"), who=f"eBRC {e['ebrc_no']}", extra=(e["ebrc_no"],)))
    return out


GST_REFUND_KINDS = {"not_in_gst", "sb001", "sb005", "sb003", "type_differs", "igst_differs"}


def _sb_words(row):
    sb = row["sb"] or {}
    return (f"shipping bill {sb.get('sb_no')}" + (f" of {_day(sb['sb_date'])}" if sb.get("sb_date") else "")
            + (f" ({sb['port']})" if sb.get("port") else ""))


def _lookup(row, rules):
    """What ICEGATE's shipping bill enquiry asks for, in the form it asks for it
    (the date as DD-MM-YYYY), and where its pages are."""
    sb, d = row["sb"] or {}, _d((row["sb"] or {}).get("sb_date"))
    return {**{k: v for k, v in (rules.get("icegate_pages") or {}).items() if k != "about"},
            "port": sb.get("port"), "sb_no": sb.get("sb_no"), "sb_date": f"{d:%d-%m-%Y}" if d else None}


def _refund_issues(row, rules, today, has_register, gst_issues, covered=()):
    out = _refund_records(row, rules, today, has_register, gst_issues, covered)
    for rec in out:
        if rec["status"] in ("igst_pending", "dbk_pending", "dbk_brc"):
            rec["icegate"] = _lookup(row, rules)
    return out


def _refund_records(row, rules, today, has_register, gst_issues, covered=()):
    rf = row.get("refund")
    if not rf or not row["sb"]:
        return []
    out, sb, codes = [], row["sb"], rules.get("icegate_codes", {})
    ig, db = rf["igst"], rf["dbk"]
    reg = f"{row['own'][0]['sheet']}, row {row['own'][0]['row']}" if row["own"] else None
    known = codes.get(ig["code"] or "", {})

    if ig["state"] in ("held", "pending"):
        stuck = [i for i in gst_issues if i["status"] in GST_REFUND_KINDS]
        if stuck and ig["code"]:
            # The return is already known to be the cause; ICEGATE's code confirms it.
            for i in stuck:
                i["why"]["notes"].insert(0, f"ICEGATE shows {ig['code']} ({known.get('words', 'see ICEGATE')}) for "
                                            f"this shipping bill, from {ig['code_from']}.")
        elif not stuck:
            checks = [f"ICEGATE: {ig['code']} - {known.get('meaning', 'a code this app does not know; read the text beside it on ICEGATE.')} (from {ig['code_from']})"
                      if ig["code"] else "ICEGATE: no status for this shipping bill has been added here."]
            if reg:
                checks.append(f"Your register: {reg} has no refund amount or date against it.")
            checks.append("Scroll: none on file.")
            month = (row["inv_date"] or row["sb"].get("sb_date") or "")[:7]
            if row.get("gst_mark") == "no":
                checks.append("GSTR-1: you marked this invoice as not filed yet. A refund only starts once it is in "
                              "GSTR-1 and GSTR-3B for the month is filed.")
            elif month and month not in covered and not row["gst"] and row.get("gst_mark") != "yes":
                checks.append(f"GSTR-1: the return for {_month(month)} is not on file here. A refund only starts once "
                              "GSTR-1 and GSTR-3B for the month are filed.")
            validated = ig["code"] == "SB000"
            out.append(_rec("igst_pending", SERIOUS, row, _why(
                f"The IGST refund of {_rs(ig['due'])} on {_sb_words(row)} has not come in"
                + (f": ICEGATE shows {ig['code']}, {known.get('words', 'an error')}." if ig["code"] and not validated
                   else " although ICEGATE has validated it." if validated else "."),
                f"The shipping bill paid {_rs(ig['due'])} of IGST {_days(ig['age'] or 0)} ago. "
                + ("You marked it as not received." if ig.get("manual") == "no" else
                   "Your register records no refund against it." if reg else "No refund against it is on file."),
                checks, None,
                known.get("meaning") if ig["code"] and not validated else
                "Refunds normally arrive within a few weeks of the export; this one is late.",
                known.get("todo") or "Look up this shipping bill on ICEGATE (IGST refund status), then add the "
                "status download here - or note the code in your register's ERROR CODE column - so the reason shows."),
                ig["due"], inr_value=ig["due"], extra=("igst",)))
    differs = [i for i in gst_issues if i["status"] == "igst_differs"]
    if ig["state"] == "short" and differs:
        differs[0]["why"]["notes"].insert(0, f"Refunded so far: {_rs(ig['recd'])}, from {ig['source']}.")
    elif ig["state"] == "short":
        gap = r2(ig["due"] - ig["recd"])
        out.append(_rec("igst_short", LOOK, row, _why(
            f"{_rs(gap)} less IGST came back than was paid on {_sb_words(row)}.",
            f"The shipping bill paid {_rs(ig['due'])}; {_rs(ig['recd'])} was refunded"
            + (f" on {_day(ig['recd_date'])}" if ig["recd_date"] else "") + ".",
            [f"Paid on the shipping bill: {_rs(ig['due'])}.", f"Refunded: {_rs(ig['recd'])}, from {ig['source']}."], None,
            "Customs refunds the lower of the IGST on the shipping bill and the IGST in Table 6A, so a lower figure "
            "in 6A (or a part shipment) leaves the rest behind.",
            "Compare Table 6A's IGST with the shipping bill; if 6A was lower, amend it through Table 9A to claim the rest."),
            gap, inr_value=gap, extra=("igst_short",)))

    for kind, x, name, col in (("igst", ig, "IGST refund", "GST AMO.RECD and DATE OF RECD"),
                               ("dbk", db, "drawback", "the drawback DATE OF RECD")):
        if has_register and row["own"] and x["state"] == "received" and (x["source"] or "").startswith("ICEGATE"):
            amount = x.get("recd") if kind == "igst" else x["due"]
            out.append(_rec("refund_unentered", LOOK, row, _why(
                f"ICEGATE shows the {name} on {_sb_words(row)} paid, but your register does not have it.",
                f"Scroll {x['scroll_no'] or '(number not given)'}"
                + (f" of {_day(x['scroll_date'])}" if x.get("scroll_date") else "")
                + f" for {_rs(amount)}, from {x['source']}.",
                [f"Your register: {reg} leaves {col} blank."], None,
                "Your register and your bank book will not agree with what customs paid until it is entered.",
                f"Enter it in your register ({col}), and check the money reached the bank account it was sent to."),
                amount, inr_value=amount, extra=("unentered", kind)))

    if db["state"] in ("held", "pending"):
        checks = [f"ICEGATE: {db['status_text']} (from {db['status_from']})" if db["status_text"]
                  else "ICEGATE: no drawback status for this shipping bill has been added here."]
        if reg:
            checks.append(f"Your register: {reg} has no drawback received date.")
        out.append(_rec("dbk_pending", SERIOUS if db["serious"] else LOOK, row, _why(
            f"The drawback of {_rs(db['due'])} on {_sb_words(row)} has not come in"
            + (f": ICEGATE says \"{db['status_text']}\"." if db["status_text"] else "."),
            f"Drawback of {_rs(db['due'])} was claimed on the shipping bill {_days(db['age'] or 0)} ago. "
            + ("You marked it as not received." if db.get("manual") == "no" else
               "Your register records no receipt." if reg else "No receipt is on file."),
            checks, None,
            "Drawback is paid into your drawback bank account once the export manifest is in and the claim is "
            "processed; one this late usually has a query or a missing manifest behind it.",
            "Look it up on ICEGATE (drawback status) and add the status download here. The usual holds are a "
            "missing EGM, a query on the claim, or a drawback bank account that is not validated."),
            db["due"], inr_value=db["due"], extra=("dbk",)))

    brc = row.get("dbk_brc")
    if brc:
        exp, dbk_amt = _d(brc.get("expected")), brc.get("dbk") or db["due"]
        due = row.get("due") or {}
        paid = due.get("state") == "paid" or bool(row["ebrcs"])
        days = (exp - today).days if exp else None
        common = f"ICEGATE lists {_sb_words(row)}, with drawback of {_rs(dbk_amt)}, as waiting for proof that the buyer paid"
        if not paid and not due:
            # The payment is not tracked here (a line from the register without
            # its value): say what customs wants, not what will happen.
            out.append(_rec("dbk_brc", LOOK, row, _why(
                f"Customs lists {_sb_words(row)} as waiting for proof that the buyer paid.",
                common + (f", expected by {_day(exp)}." if exp else "."),
                ["Payment: not tracked here - the invoice's value and currency are not on file."], None,
                "Customs recovers drawback when it sees no proof that the export was paid for (Rule 18 of the "
                "Drawback Rules, 2017).",
                "If the buyer has paid, make sure the eBRC carries this shipping bill, or submit the proof of "
                "realisation on ICEGATE. If not, chase the payment."),
                dbk_amt, inr_value=dbk_amt, date_=brc.get("expected"), extra=("brc",)))
        elif paid:
            out.append(_rec("dbk_brc", LOOK, row, _why(
                f"Customs still wants proof of payment for {_sb_words(row)}, which is paid.",
                common + (f", expected by {_day(exp)}." if exp else "."),
                [f"Payment: on file here ({'an eBRC' if row['ebrcs'] else 'counted against the invoice'})."], None,
                "Until customs sees the realisation, the drawback can be recovered.",
                "Check that the eBRC for this payment carries this shipping bill; if customs still lists it, submit "
                "the proof of realisation on ICEGATE."), dbk_amt, inr_value=dbk_amt, extra=("brc",)))
        else:
            late = days is not None and days < 0
            out.append(_rec("dbk_brc", SERIOUS if late else LOOK, row, _why(
                (f"Drawback of {_rs(dbk_amt)} on {_sb_words(row)} can be recovered: proof of payment was due "
                 f"{_day(exp)}." if late else
                 f"Proof that the buyer paid for {_sb_words(row)} is due to customs by {_day(exp)}"
                 + (f" - {_days(days)} left." if days is not None else ".")),
                common + ".", ["Payment: none on file for this invoice."], None,
                "Customs recovers drawback when the export proceeds are not realised in time (Rule 18 of the "
                "Drawback Rules, 2017).",
                "Chase the payment. If the bank has extended the deadline, give customs the extension so the "
                "recovery waits."), dbk_amt, inr_value=dbk_amt, date_=brc.get("expected"), extra=("brc",)))
    return out


def _edpms_issues(row, span):
    """The bank's EDPMS statement against what is on file here."""
    sb, e, due = row["sb"], row.get("bank_edpms"), row.get("due")
    if not sb or not due:
        return []
    if e:
        status = e.get("status") or ""
        open_ = (e.get("outstanding_fc") or 0) > 0.005 or bool(re.search(r"pending|outstanding|open|unreali", status, re.I))
        closed = not open_ and bool((e.get("realised_fc") or 0) > 0 or re.search(r"reali[sz]ed|closed", status, re.I))
        cur = e.get("currency") or row["currency"]
        if open_ and due["state"] == "paid":
            return [_rec("edpms_open", LOOK, row, _why(
                f"The bank still shows {_sb_words(row)} as unpaid in EDPMS, but it is paid.",
                f"Your bank's EDPMS statement ({e['doc_name']}) shows {_fc(e.get('outstanding_fc'), cur)} outstanding; "
                f"here the invoice is paid ({_fc(due['received_fc'], row['currency'])} received).",
                [f"Receipts on file: {len(row['receipts'])}.", f"eBRCs on file: {len(row['ebrcs'])}."], None,
                "An open bill in EDPMS is reported as overdue at its due date, whatever happened to the money.",
                "Ask the bank to close it in EDPMS against the remittance (give them the IRM or eBRC details)."),
                e.get("outstanding_fc"), cur, extra=("edpms",))]
        if closed and due["state"] != "paid" and not row["receipts"]:
            return [_rec("edpms_closed", LOOK, row, _why(
                f"The bank has closed {_sb_words(row)} in EDPMS, but no payment for it is on file here.",
                f"Your bank's EDPMS statement ({e['doc_name']}) shows it realised"
                + (f" ({_fc(e.get('realised_fc'), cur)})" if e.get("realised_fc") else "") + ".",
                ["Payments on file: none counted against this invoice."], None,
                "The register shows money still to come that the bank says has arrived.",
                "Add the bank's advice or the DGFT IRM list for that payment, or confirm the suggested one."),
                e.get("realised_fc"), cur, extra=("edpms",))]
        return []
    if span and not sb["from_register"] and due["state"] != "paid" and span[0] <= (sb.get("sb_date") or "") <= span[1]:
        return [_rec("edpms_missing", LOOK, row, _why(
            f"{_sb_words(row)[0].upper()}{_sb_words(row)[1:]} is not on your bank's EDPMS statement.",
            f"The statement covers shipping bills from {_day(span[0])} to {_day(span[1])}; this one falls inside "
            "that and is still unpaid here, but the bank does not list it.",
            ["Shipping bill number: not on the statement.", f"AD code on the shipping bill: {sb.get('ad_code') or 'not read'}."],
            None, "A bill the bank cannot see cannot be closed when the money arrives.",
            "Ask the bank to check that the shipping bill is linked to your AD code in EDPMS."), extra=("edpms",))]
    return []


def _bl_unplaced(bl):
    refs = [r["inv_no"] for r in bl.get("invoice_refs") or []] + [r["sb_no"] for r in bl.get("sb_refs") or []]
    return _rec("bl_unplaced", LOOK, None, _why(
        f"Bill of lading {bl.get('bl_no') or bl.get('name')} cannot be placed against an invoice.",
        "It was read, but nothing on it points to an invoice or shipping bill on file.",
        [f"References on it: {', '.join(refs)}." if refs else "References on it: none could be read.",
         f"Containers: {', '.join(bl.get('containers') or []) or 'none read'}."], None,
        "Its invoice will keep showing the bill of lading as missing.",
        "Add the shipping bill it belongs to, or check the invoice number printed on it."),
        date_=bl.get("sob_date"), who=bl.get("name") or "", extra=(bl.get("id"),))


def _ai_differs(doc):
    """The AI checker read a document on its own and does not agree with what the app read."""
    a, name = doc["ai"], doc.get("kept_as") or doc.get("name") or "a document"
    what = TYPE_WORDS.get(doc.get("type"), "document").lower()
    wrong = a.get("differences") or []
    checks = [f"{d['label']}: the app has {d['app']}; the AI reads {d['ai']}." for d in wrong]
    if a.get("kind_differs"):
        checks.insert(0, f"Kind: the app filed it as a {what}; the AI takes it for {a.get('kind_words') or 'another kind'}.")
    if a.get("unread"):
        checks.append(f"Not read by the AI: {', '.join(a['unread']).lower()}.")
    labels = [d["label"].lower() for d in wrong] + (["what it is"] if a.get("kind_differs") else [])
    rec = _rec("ai_differs", LOOK, None, _why(
        f"The AI checker reads {name} differently: {_and(labels)}.",
        "The local AI model read the document again on its own, and does not agree with what the app read. "
        "One of the two readings is wrong.", checks, None,
        "Until you settle it, the register counts what the app read.",
        "Open the original and compare. If the AI is right, use its reading; if the app is right, take this off "
        "the list."), date_=(doc.get("added_at") or "")[:10] or None, who=name, extra=(doc.get("id"), a.get("reading")))
    rec["doc_id"] = doc.get("id")
    return rec


def _unread(doc):
    return _rec("unread", LOOK, None, _why(
        f"{doc.get('name') or 'A document'} could not be read.",
        "It is not a document the Exports module recognises, or the page could not be read.",
        list(doc.get("notes") or []), None,
        "Whatever it holds is not counted anywhere.",
        "If it is a shipping bill, bill of lading, bank advice, GSTR-1 or a DGFT download, add a clearer copy."),
        who=doc.get("name") or "", extra=(doc.get("id"),))


# ---------------------------------------------------------------------------
# The ticks on each invoice, the figures, and the eBRC bulk sheet
# ---------------------------------------------------------------------------

def _marks(decisions):
    """What the user has said about each invoice's GSTR-1, IGST refund and
    drawback: {(invoice key, what): ("yes" or "no", when it was said)}."""
    out = {}
    for dec in decisions.values():
        ctx = dec.get("context") or {}
        if (dec.get("action") == "mark" and ctx.get("kind") == "mark" and ctx.get("what") in MARKS
                and ctx.get("value") in ("yes", "no")):
            out[(ctx.get("invoice"), ctx["what"])] = (ctx["value"], dec.get("updated_at"))
    return out


def _controls(row, marks, covered):
    """The yes-or-no questions an invoice's file asks: whether each applies,
    what the documents say (None when none does), what the user said, and the
    answer that counts."""
    rf, out = row.get("refund") or {}, {}
    for what in MARKS:
        said, at = marks.get((row["key"], what), (None, None))
        if what == "gstr1":
            applies = True
            doc = "yes" if row["gst"] else "no" if (row["inv_date"] or "")[:7] in covered else None
        else:
            x = rf.get(what) or {}
            applies = x.get("state") not in (None, "na")
            doc = {"received": "yes", "short": "yes", "held": "no", "pending": "no", "waiting": "no"}.get(
                x.get("doc_state", x.get("state")))
        out[what] = {"applies": applies, "doc": doc, "said": said, "at": at, "value": said or doc}
    return out


def _chips(row, issues, decisions, covered, rules, today):
    mine = {i["status"]: i for i in issues if i["key"] == row["key"]}

    def settled(kind):
        return kind in mine and (decisions.get(mine[kind]["fp"]) or {}).get("action") in ("resolved", "ignore")

    sb, gst, due = row["sb"], row["gst"], row.get("due")
    chips = {}
    if sb and sb["from_register"]:
        chips["sb"] = ("ok", f"{sb['sb_no']} · {_day(sb['sb_date'])} · your register")
    else:
        chips["sb"] = ("ok", f"{sb['sb_no']} · {_day(sb['sb_date'])}") if sb else (
        ("marked", "Marked as not needed") if settled("no_sb") else ("missing", "Not on file")
        if "no_sb" in mine else ("pending", "Not expected yet"))
    if not sb:
        chips["bl"] = ("na", "Waits for the shipping bill")
    elif sb["from_register"] and not row["bls"]:
        chips["bl"] = ("na", "Not kept in your register")
    elif row["bls"]:
        b = row["bls"][0]
        chips["bl"] = ("ok", f"{b.get('bl_no') or 'On file'}" + (f" · {_day(b['sob_date'])}" if b.get("sob_date") else ""))
    else:
        chips["bl"] = ("marked", "Marked as received") if settled("no_bl") else ("missing", "Not received")
    short = {"sb001": "Shipping bill wrong in 6A" if (gst or {}).get("sbnum") else "No shipping bill in 6A",
             "sb005": "Invoice number differs", "sb003": "GSTIN differs", "igst_differs": "IGST differs",
             "type_differs": "WPAY or LUT differs"}
    gst_bad = [k for k in short if k in mine]
    mark = row.get("gst_mark")
    if gst and mark == "no":
        chips["gst"] = ("look", "Not filed yet · marked by you")
    elif gst:
        chips["gst"] = (("problem" if mine[gst_bad[0]]["tone"] == SERIOUS else "look", short[gst_bad[0]])
                        if gst_bad else ("ok", f"In 6A · {_month((gst.get('idt') or '')[:7])}"))
    elif mark == "yes":
        chips["gst"] = ("ok", "Filed · marked by you")
    elif mark == "no":
        chips["gst"] = ("problem" if (mine.get("not_in_gst") or {}).get("tone") == SERIOUS else "look",
                        "Not filed · marked by you")
    elif (row["inv_date"] or "")[:7] in covered:
        chips["gst"] = ("problem" if sb else "pending", "Not in Table 6A")
    else:
        chips["gst"] = ("pending", "GSTR-1 not added yet")
    if due:
        cur = row["currency"]
        state = due["state"]
        chips["paid"] = {
            "paid": ("ok", f"Received {_fc(due['received_fc'], cur)}"),
            "open": ("pending", f"Due {_day(due['date'])}" + (" · part paid" if due["part_paid"] else "")),
            "due_soon": ("look", f"Due {_day(due['date'])} · {_days(due['days_left'])} left"),
            "overdue": ("problem", f"Overdue since {_day(due['date'])}"),
            "overdue_year": ("problem", f"Over a year overdue"),
        }[state]
    elif any(o.get("payment_recd") for o in row["own"]):
        chips["paid"] = ("ok", "Received · your register")
    else:
        chips["paid"] = ("na", "No invoice value on file" if sb else "Waits for the shipping bill")
    rf = row.get("refund") or {}
    parts = []
    for name, x in (("IGST", rf.get("igst") or {}), ("DBK", rf.get("dbk") or {})):
        state = x.get("state")
        if state in (None, "na", "unknown"):
            continue
        tone = {"received": "ok", "short": "look", "waiting": "pending", "held": "problem" if name == "IGST" else "look",
                "pending": "problem" if name == "IGST" or x.get("serious") else "look"}[state]
        words = {"received": "received", "short": "short", "waiting": "due", "held": "held", "pending": "not in"}[state]
        parts.append((tone, f"{name} {words}" + (f" · {x['code']}" if name == "IGST" and state == "held" and x.get("code") else "")))
    order = ["problem", "look", "pending", "ok"]
    unknown = any((x or {}).get("state") == "unknown" for x in rf.values())
    chips["refund"] = (min((t for t, _ in parts), key=order.index), " · ".join(w for _, w in parts)) if parts \
        else ("pending", "No status added yet") if unknown else ("na", "Nothing claimed")
    if due and due["received_fc"]:
        chips["ebrc"] = ("look", "To generate") if "ebrc_due" in mine else ("ok", f"{len(row['ebrcs'])} on file")
    else:
        chips["ebrc"] = ("na", "After payment")
    return {k: {"state": v[0], "text": v[1]} for k, v in chips.items()}


def _figures(invoices, issues, bulk, rems):
    gst_serious = {"not_in_gst", "sb001", "sb005", "sb003", "type_differs"}
    at_risk = {}
    for i in issues:
        if i["status"] in gst_serious and i["tone"] == SERIOUS and i["key"]:
            at_risk[i["key"]] = i["inr"] or 0
    dues = [r["due"] for r in invoices if r.get("due")]
    return {
        "invoices": len(invoices),
        "shipped": sum(1 for r in invoices if r["sb"]),
        "refund_at_risk": r2(sum(at_risk.values())),
        "refund_at_risk_count": len(at_risk),
        "to_receive_inr": r2(sum(d["outstanding_inr"] for d in dues)),
        "due_90_inr": r2(sum(d["outstanding_inr"] for d in dues if d["state"] == "due_soon")),
        "due_90_count": sum(1 for d in dues if d["state"] == "due_soon"),
        "overdue_inr": r2(sum(d["outstanding_inr"] for d in dues if d["state"].startswith("overdue"))),
        "overdue_count": sum(1 for d in dues if d["state"].startswith("overdue")),
        "paid_count": sum(1 for d in dues if d["state"] == "paid"),
        "ebrc_to_generate": len(bulk),
        "bl_missing": sum(1 for i in issues if i["status"] == "no_bl"),
        "not_in_gst": sum(1 for i in issues if i["status"] == "not_in_gst"),
        "unplaced_money": sum(1 for r in rems if not r["cancelled"] and (r["available_fc"] or 0) > 0.005),
        "refund_pending_inr": r2(sum((r["refund"]["igst"]["due"] if r["refund"]["igst"]["state"] in ("held", "pending") else 0)
                                     + (r["refund"]["dbk"]["due"] if r["refund"]["dbk"]["state"] in ("held", "pending") else 0)
                                     for r in invoices if r.get("refund"))),
        "refund_pending_count": sum(1 for r in invoices if r.get("refund") and (
            r["refund"]["igst"]["state"] in ("held", "pending") or r["refund"]["dbk"]["state"] in ("held", "pending"))),
    }


def _bulk_rows(rows, rems, ebrcs):
    """Rows for DGFT's bulk eBRC sheet: every receipt set against an invoice
    that no eBRC covers yet, each checked the way DGFT checks it."""
    by_id = {r["id"]: r for r in rems}
    out = []
    for row in rows.values():
        covered = defaultdict(float)
        for e in row["ebrcs"]:
            covered[e.get("remittance_id")] += e.get("realised_fc") or 0
        for m in row["receipts"]:
            if m["how"] == "eBRC" or covered.get(m["remittance_id"], 0) + 0.005 >= m["amount_fc"]:
                continue
            rem, sb, gst = by_id[m["remittance_id"]], row["sb"] or {}, row["gst"] or {}
            problems = []
            from_irm = ("IRM number", "IRM date", "IFSC", "AD code")
            for field, value in (("IRM number", rem["irm_no"]), ("IRM date", rem["irm_date"]),
                                 ("IFSC", rem["ifsc"]), ("AD code", rem["ad_code"]),
                                 ("purpose code", rem["purpose_code"]), ("shipping bill number", sb.get("sb_no")),
                                 ("shipping bill date", sb.get("sb_date")), ("port code", sb.get("port"))):
                if not value:
                    problems.append(f"{field} is missing" + (
                        "" if field not in from_irm else " - add the DGFT IRM list" if not rem["irm_no"]
                        else " - it is blank in the DGFT IRM list; copy it from the IRM on the portal"))
            if rem["purpose_code"] and rem["purpose_code"] not in ("P0102", "P0103", "P0104", "P0109"):
                problems.append(f"purpose code {rem['purpose_code']} cannot be used for goods "
                                "(P0102, P0103, P0104 or P0109 only)")
            if rem["currency"] and not re.fullmatch(r"[A-Z]{3}", rem["currency"]):
                problems.append("currency must be the ISO code, e.g. USD")
            used = sum(e.get("realised_fc") or 0 for e in ebrcs
                       if e.get("remittance_id") == rem["id"] and not e["cancelled"])
            out.append({
                "irm_no": rem["irm_no"], "irm_date": rem["irm_date"], "ifsc": rem["ifsc"],
                "ad_code": rem["ad_code"], "currency": rem["currency"], "purpose_code": rem["purpose_code"],
                "available_fc": r2((rem["amount_fc"] or 0) - used), "sb_no": sb.get("sb_no"),
                "sb_date": sb.get("sb_date"), "port": sb.get("port"), "bill_no": row["inv_no"],
                "mapped_fc": m["amount_fc"], "gst_availed": "Y" if gst else "N",
                "gst_inv_no": gst.get("inum") or row["inv_no"], "gst_inv_date": gst.get("idt") or row["inv_date"],
                "remitter": rem["remitter"], "problems": problems})
    # DGFT refuses a sheet that maps more of one IRM than is left on it.
    for irm in {b["irm_no"] for b in out if b["irm_no"]}:
        rows_ = [b for b in out if b["irm_no"] == irm]
        if sum(b["mapped_fc"] for b in rows_) > rows_[0]["available_fc"] + 0.005:
            for b in rows_:
                b["problems"].append(f"these rows map more of IRM {irm} than the "
                                     f"{rows_[0]['available_fc']:,.2f} left on it")
    return out


def _doc_summary(doc, rows, rems, ebrcs):
    t = doc.get("type", "unknown")
    placed = []
    for r in rows.values():
        if any(s.get("doc_id") == doc.get("id") for s in r["sbs"]) or any(
                b.get("id") == doc.get("id") for b in r["bls"]) or (
                r["invoice_doc"] or {}).get("id") == doc.get("id") or any(
                l.get("doc_id") == doc.get("id") for l in r["gst_lines"]):
            placed.append(r["inv_no"])
    placed += [r["inv_no"] for r in rows.values() if any(o.get("doc_id") == doc.get("id") for o in r["own"])
               or any(x.get("doc_id") == doc.get("id") for x in r["refund_status"])
               or (r.get("dbk_brc") or {}).get("doc_id") == doc.get("id")
               or (r.get("bank_edpms") or {}).get("doc_id") == doc.get("id")]
    # Money documents are placed through what their money was counted against.
    placed += [m["inv_no"] for r in rems if doc.get("id") in r["doc_ids"] for m in r["mapped"]]
    placed += [rows[e["invoice_key"]]["inv_no"] for e in ebrcs
               if e.get("doc_id") == doc.get("id") and e["invoice_key"] and not e["cancelled"]]
    what = {
        "sb": lambda: f"Shipping bill {doc.get('sb_no')} · {doc.get('port')} · "
                      f"{len(doc.get('invoices') or [])} invoice(s)",
        "bl": lambda: f"Bill of lading {doc.get('bl_no') or ''}".strip(),
        "fira": lambda: f"{doc.get('currency') or ''} {doc.get('amount_fc') or 0:,.2f} from "
                        f"{doc.get('remitter') or 'an unnamed remitter'}",
        "invoice": lambda: f"Invoice {doc.get('inv_no') or '(number not read)'}",
        "gstr1": lambda: f"{len(doc.get('lines') or [])} export invoice(s) in Table 6A"
                         + (f" · {_month(doc['period'])}" if doc.get("period") else ""),
        "irm_list": lambda: f"{len(doc.get('rows') or [])} remittance(s)",
        "ebrc_list": lambda: f"{len(doc.get('rows') or [])} eBRC(s)",
        "sb_status": lambda: f"{len(doc.get('rows') or [])} shipping bill status line(s)",
        "own_register": lambda: f"{len(doc.get('rows') or [])} shipping bills over {len(doc.get('sheets') or [])} "
                                "year tab(s)" + (f", and ICEGATE's drawback list ({len(doc['dbk_pending'])})"
                                                 if doc.get("dbk_pending") else ""),
        "edpms": lambda: f"{len(doc.get('rows') or [])} shipping bills on the bank's EDPMS statement",
        "refund_status": lambda: f"{len(doc.get('rows') or [])} refund and drawback statuses",
        "dbk_pending": lambda: f"{len(doc.get('rows') or [])} shipping bills waiting on proof of payment",
    }.get(t, lambda: "Not recognised")()
    return {"id": doc.get("id"), "name": doc.get("name"), "type": t, "type_words": TYPE_WORDS.get(t, t),
            "what": what, "placed": sorted(set(placed)), "notes": doc.get("notes") or [],
            "read_by": doc.get("read_by"), "confidence": doc.get("confidence"),
            "added_at": doc.get("added_at"), "kept_as": doc.get("kept_as"), "folder": doc.get("folder"),
            "corrected": bool(doc.get("corrected")), "ai": doc.get("ai")}
