"""
Everything derived from a finished reconciliation.

The reconciler answers "which invoice is which". This module answers the
questions the person running it actually has: how much credit can I claim this
month, what do I owe, which supplier is costing me money, what is about to
lapse, and what should I do first.
"""

from collections import Counter, defaultdict
from datetime import datetime

from app.backend.common import (
    AGING_BUCKETS, INELIGIBLE_REASON_HINTS, compact_inr, fp_2b, fy_month_order,
    inr, iso, month_label, r2, sec_164_deadline, to_float,
)

OPEN_STATUSES = {"Value mismatch", "Invoice no. mismatch", "Probable match",
                 "Split booking", "In GSTR-2B only", "In Tally only"}

HEADS = ("igst", "cgst", "sgst", "cess")


# ---------------------------------------------------------------------------
# ITC position
# ---------------------------------------------------------------------------

def itc_position(result, gst_records):
    """
    The credit working, built the way it is defended in an assessment: start
    from what the portal shows, strip what the law blocks, then strip what your
    own books cannot yet support.

    A row's credit comes off once, at the first line of the ladder that applies
    to it: blocked, the part not creditable, place of supply, not yet booked,
    reverse charge. Summing each line over every row took a reverse-charge
    bill missing from Tally off twice, and "safe to claim" fell below nothing.
    """
    live = [g for g in gst_records if "superseded" not in g.get("flags", [])]
    not_booked = Counter(r["fp"] for r in result["subsets"]["gst_only"])

    gross = blocked = partial_lost = pos_blocked = unbooked_eligible = rcm = 0.0
    recoverable = 0.0
    allowed = dict.fromkeys(HEADS, 0.0)
    for g in live:
        flags = g.get("flags", [])
        fp = fp_2b(g["gstin"], g["inv_no"], g["inv_val"])
        unbooked_row = not_booked[fp] > 0
        if unbooked_row:
            not_booked[fp] -= 1
        tax = g["tax"]
        gross += tax
        if not g.get("itc_eligible", True):
            blocked += tax
            continue
        share = (g.get("pct_applicable") or 100) / 100.0
        partial_lost += tax * (1 - share)
        if "pos_mismatch" in flags:
            pos_blocked += tax * share
            continue
        for head in HEADS:                      # for the GSTR-3B set-off
            allowed[head] += g[head] * share
        if unbooked_row:
            unbooked_eligible += tax * share
            if "rcm" not in flags:              # booked, it still waits for the tax
                recoverable += tax * share
        elif "rcm" in flags:
            rcm += tax * share

    gross, blocked, partial_lost, pos_blocked, unbooked_eligible, rcm, recoverable = (
        r2(x) for x in (gross, blocked, partial_lost, pos_blocked, unbooked_eligible,
                        rcm, recoverable))
    credit_notes = r2(sum(g["tax"] for g in live if "credit_note" in g.get("flags", [])))
    prior_fy = r2(sum(g["tax"] for g in live if "prior_fy" in g.get("flags", [])))
    unbooked = r2(sum(r.get("tax_2b") or 0 for r in result["subsets"]["gst_only"]))

    statutory = r2(gross - blocked - partial_lost - pos_blocked)
    claimable_now = r2(statutory - unbooked_eligible - rcm)

    igst = r2(sum(g["igst"] for g in live))
    cgst = r2(sum(g["cgst"] for g in live))
    sgst = r2(sum(g["sgst"] for g in live))
    cess = r2(sum(g["cess"] for g in live))

    ladder = [
        {"label": "Credit shown in GSTR-2B", "amount": gross, "tone": "neutral",
         "note": "IGST + CGST + SGST + Cess across every section of the return."},
        {"label": "Less: portal marks ITC unavailable", "amount": -blocked, "tone": "risk",
         "note": "Rows where the GST portal itself blocks the credit."},
        {"label": "Less: partially creditable rows", "amount": -partial_lost, "tone": "warn",
         "note": "Documents where only part of the tax is creditable."},
        {"label": "Less: place-of-supply outside your state", "amount": -pos_blocked,
         "tone": "risk",
         "note": "CGST/SGST of another state cannot be credited against your liability."},
        {"label": "Credit allowed by law", "amount": statutory, "tone": "total",
         "note": "The ceiling. You cannot claim above this line."},
        {"label": "Less: invoices not yet booked in Tally", "amount": -unbooked_eligible,
         "tone": "warn",
         "note": "Section 16(2)(a) needs the invoice in your books before you claim it. "
                 "Reverse-charge bills among them also wait for the tax to be paid."},
        {"label": "Less: reverse-charge credit", "amount": -rcm, "tone": "info",
         "note": "Booked reverse-charge bills: claim these only after the tax is "
                 "actually paid in cash."},
        {"label": "Safe to claim this period", "amount": claimable_now, "tone": "clear",
         "note": "What you can defend today, on the books as they stand."},
    ]

    reasons = Counter()
    for g in live:
        if not g.get("itc_eligible", True) and g.get("reason"):
            reasons[g["reason"].strip()] += 1
    blocked_reasons = [{
        "reason": k, "count": v,
        "tax": r2(sum(g["tax"] for g in live
                      if (g.get("reason") or "").strip() == k and not g.get("itc_eligible", True))),
        "explanation": INELIGIBLE_REASON_HINTS.get(k.lower(), ""),
    } for k, v in reasons.most_common()]

    return {
        "ladder": ladder,
        "heads": {"igst": igst, "cgst": cgst, "sgst": sgst, "cess": cess},
        "heads_allowed": {h: r2(v) for h, v in allowed.items()},
        "gross": gross, "blocked": blocked, "partial_lost": partial_lost,
        "pos_blocked": pos_blocked, "statutory": statutory,
        "unbooked": unbooked, "unbooked_eligible": unbooked_eligible,
        "rcm": rcm, "credit_notes": credit_notes, "prior_fy": prior_fy,
        "claimable_now": claimable_now,
        "recoverable_by_booking": recoverable,
        "blocked_reasons": blocked_reasons,
    }


def gstr3b_working(purchase_result, purchase_itc, sales_result=None, sales_records=None):
    """
    A GSTR-3B working paper. The outward side needs a sales source; without one
    the liability block is reported as unavailable rather than guessed at.
    """
    itc = purchase_itc
    heads = itc["heads_allowed"]

    if sales_result and sales_records:
        out_igst = r2(sum(s["igst"] for s in sales_records))
        out_cgst = r2(sum(s["cgst"] for s in sales_records))
        out_sgst = r2(sum(s["sgst"] for s in sales_records))
        out_cess = r2(sum(s["cess"] for s in sales_records))
        out_taxable = r2(sum(s["taxable"] for s in sales_records))
        liability_known = True
        unreported = sales_result.get("unreported_sales_value") or 0.0
    else:
        out_igst = out_cgst = out_sgst = out_cess = out_taxable = 0.0
        liability_known = False
        unreported = 0.0

    # Credit is set off head-wise: IGST first against IGST, then across heads.
    def offset(liab, credit):
        used = min(liab, credit)
        return r2(used), r2(liab - used), r2(credit - used)

    igst_used, igst_bal, igst_credit_left = offset(out_igst, heads["igst"])
    cgst_used, cgst_bal, cgst_credit_left = offset(out_cgst, heads["cgst"])
    sgst_used, sgst_bal, sgst_credit_left = offset(out_sgst, heads["sgst"])
    cess_used, cess_bal, cess_credit_left = offset(out_cess, heads["cess"])

    # Surplus IGST credit may be applied to the CGST/SGST balance.
    cross = min(igst_credit_left, cgst_bal + sgst_bal)
    to_cgst = min(cgst_bal, cross / 2 if cross else 0)
    to_sgst = min(sgst_bal, cross - to_cgst)
    cgst_bal = r2(cgst_bal - to_cgst)
    sgst_bal = r2(sgst_bal - to_sgst)
    igst_credit_left = r2(igst_credit_left - to_cgst - to_sgst)

    cash = r2(igst_bal + cgst_bal + sgst_bal + cess_bal)
    carry = r2(igst_credit_left + cgst_credit_left + sgst_credit_left + cess_credit_left)

    return {
        "liability_known": liability_known,
        "outward": {
            "taxable": out_taxable, "igst": out_igst, "cgst": out_cgst,
            "sgst": out_sgst, "cess": out_cess,
            "total": r2(out_igst + out_cgst + out_sgst + out_cess),
        },
        "credit": {
            "igst": heads["igst"], "cgst": heads["cgst"], "sgst": heads["sgst"],
            "cess": heads["cess"], "total": itc["statutory"],
            "claimable": itc["claimable_now"],
        },
        "set_off": [
            {"head": "IGST", "liability": out_igst, "credit_used": igst_used,
             "cash": igst_bal, "credit_left": igst_credit_left},
            {"head": "CGST", "liability": out_cgst, "credit_used": r2(cgst_used + to_cgst),
             "cash": cgst_bal, "credit_left": cgst_credit_left},
            {"head": "SGST / UTGST", "liability": out_sgst, "credit_used": r2(sgst_used + to_sgst),
             "cash": sgst_bal, "credit_left": sgst_credit_left},
            {"head": "Cess", "liability": out_cess, "credit_used": cess_used,
             "cash": cess_bal, "credit_left": cess_credit_left},
        ],
        "cash_payable": cash,
        "credit_carried_forward": carry,
        "unreported_sales_value": unreported,
        "notes": [
            "Credit is set off head-wise, then surplus IGST is applied against the "
            "CGST and SGST balance in equal halves, as rule 88A requires.",
            "This working sets off the credit allowed by law, head by head. That "
            "includes credit on bills not yet in your books and reverse-charge "
            "credit not yet paid for: claim those only once they are.",
        ] + ([] if liability_known else [
            "No sales source was loaded, so the outward liability block is empty. "
            "Upload a Tally sales register and the filed GSTR-1 to complete it."
        ]),
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def rate_analysis(gst_records):
    """Tax-slab distribution across the 2B documents."""
    live = [g for g in gst_records if "superseded" not in g.get("flags", [])]
    groups = defaultdict(lambda: {"count": 0, "taxable": 0.0, "tax": 0.0, "value": 0.0})
    for g in live:
        slab = g.get("rate_slab")
        key = f"{slab:g}%" if slab is not None else "Not derivable"
        if "mixed_rate" in g.get("flags", []):
            key = "Mixed slabs"
        row = groups[key]
        row["count"] += 1
        row["taxable"] += g["taxable"]
        row["tax"] += g["tax"]
        row["value"] += g["inv_val"]

    def sort_key(k):
        if k == "Mixed slabs":
            return 1e5
        if k == "Not derivable":
            return 1e6
        try:
            return float(k.rstrip("%"))
        except ValueError:
            return 1e7

    total_tax = sum(v["tax"] for v in groups.values()) or 1.0
    out = []
    for key in sorted(groups, key=sort_key):
        v = groups[key]
        out.append({
            "slab": key, "count": v["count"], "taxable": r2(v["taxable"]),
            "tax": r2(v["tax"]), "value": r2(v["value"]),
            "share": round(v["tax"] / total_tax * 100, 1),
        })
    return out


def monthly_series(result, fy_start=None):
    """Per-month value, credit and match quality - the input for the charts."""
    rows = result["all_records"]
    order = fy_month_order(fy_start) if fy_start else None
    seen = defaultdict(lambda: {
        "val_2b": 0.0, "val_tally": 0.0, "tax": 0.0, "matched": 0,
        "open": 0, "total": 0, "at_risk": 0.0,
    })
    for r in rows:
        if r["status"] == "Superseded":
            continue
        mk = r.get("month_key")
        if not mk:
            continue
        m = seen[mk]
        m["val_2b"] += r.get("inv_val_2b") or 0.0
        m["val_tally"] += r.get("amt_tally") or 0.0
        m["tax"] += r.get("tax_2b") or 0.0
        m["total"] += 1
        if r["status"] == "Matched":
            m["matched"] += 1
        else:
            m["open"] += 1
        if r["status"] == "In GSTR-2B only":
            m["at_risk"] += r.get("tax_2b") or 0.0

    keys = order if order else sorted(seen)
    out = []
    for k in keys:
        if order and k not in seen and not any(seen):
            continue
        m = seen.get(k)
        if m is None:
            if not order:
                continue
            m = {"val_2b": 0.0, "val_tally": 0.0, "tax": 0.0, "matched": 0,
                 "open": 0, "total": 0, "at_risk": 0.0}
        out.append({
            "month": k, "label": month_label(k),
            "val_2b": r2(m["val_2b"]), "val_tally": r2(m["val_tally"]),
            "tax": r2(m["tax"]), "matched": m["matched"], "open": m["open"],
            "total": m["total"], "at_risk": r2(m["at_risk"]),
            "match_rate": round(m["matched"] / m["total"] * 100, 1) if m["total"] else 0.0,
            "variance": r2(m["val_2b"] - m["val_tally"]),
        })
    return out


def aging(result):
    """Open items bucketed by how long they have been sitting there."""
    buckets = {label: {"bucket": label, "count": 0, "value": 0.0, "tax": 0.0,
                       "by_status": Counter()} for label, _, _ in AGING_BUCKETS}
    for r in result["all_records"]:
        if r["status"] not in OPEN_STATUSES:
            continue
        b = r.get("aging_bucket")
        if b not in buckets:
            continue
        row = buckets[b]
        row["count"] += 1
        row["value"] += abs(r.get("inv_val_2b") or r.get("amt_tally") or 0.0)
        row["tax"] += abs(r.get("tax_2b") or 0.0)
        row["by_status"][r["status"]] += 1

    out = []
    for label, _, _ in AGING_BUCKETS:
        row = buckets[label]
        out.append({"bucket": label, "count": row["count"], "value": r2(row["value"]),
                    "tax": r2(row["tax"]), "by_status": dict(row["by_status"])})
    return out


def concentration(party_wise, top=10):
    """How much of the credit sits with how few suppliers."""
    ranked = sorted(party_wise, key=lambda p: -abs(p["itc_2b"]))
    total = sum(abs(p["itc_2b"]) for p in ranked) or 1.0
    running, out = 0.0, []
    for i, p in enumerate(ranked[:top], start=1):
        running += abs(p["itc_2b"])
        out.append({
            "rank": i, "gstin": p["gstin"], "party_name": p["party_name"],
            "itc": p["itc_2b"], "share": round(abs(p["itc_2b"]) / total * 100, 1),
            "cumulative": round(running / total * 100, 1),
            "grade": p["grade"], "match_rate": p["match_rate"],
        })
    n80 = 0
    running = 0.0
    for p in ranked:
        running += abs(p["itc_2b"])
        n80 += 1
        if running / total >= 0.80:
            break
    return {
        "top": out, "supplier_count": len(ranked),
        "suppliers_for_80pct": n80,
        "top_10_share": round(sum(abs(p["itc_2b"]) for p in ranked[:10]) / total * 100, 1),
    }


def sec164_watch(result, gst_records, fy_start, today=None):
    """Credit that lapses if it is not claimed before the statutory cut-off."""
    today = today or datetime.now()
    # Counted in whole calendar days: at 8 pm on 25 Sep the 30 Nov deadline is
    # 66 days away, which a timestamp difference rounds down to 65.
    today = datetime(today.year, today.month, today.day)
    if not fy_start:
        return {"applicable": False}
    deadline = sec_164_deadline(fy_start)
    days = (deadline - today).days
    at_stake = [r for r in result["subsets"]["gst_only"] if (r.get("eligible_itc") or 0) > 0]
    at_stake.sort(key=lambda r: -(r.get("eligible_itc") or 0))
    return {
        "applicable": True,
        "deadline": iso(deadline),
        "days_left": days,
        "lapsed": days < 0,
        "urgent": 0 <= days <= 60,
        "count": len(at_stake),
        "amount": r2(sum(r["eligible_itc"] for r in at_stake)),
        "top": [{
            "gstin": r["gstin"], "party": r["party_2b"], "inv_no": r["inv_no_2b"],
            "inv_date": r["inv_date_2b"], "value": r["inv_val_2b"],
            "itc": r["eligible_itc"], "age_days": r.get("age_days"),
            "fp": r["fp"],
        } for r in at_stake[:25]],
    }


def top_exposures(result, limit=25):
    """The open items worth the most credit, ranked."""
    rows = [r for r in result["all_records"] if r["status"] in OPEN_STATUSES]
    rows.sort(key=lambda r: (-abs(r.get("tax_2b") or 0),
                             -abs(r.get("inv_val_2b") or r.get("amt_tally") or 0)))
    out = []
    for r in rows[:limit]:
        out.append({
            "fp": r["fp"], "status": r["status"], "gstin": r.get("gstin"),
            "party": r.get("party_2b") or r.get("party_tally"),
            "doc_no": r.get("inv_no_2b") or r.get("vch_no_tally"),
            "date": r.get("inv_date_2b") or r.get("vch_date_tally"),
            "value": r.get("inv_val_2b") if r.get("inv_val_2b") is not None else r.get("amt_tally"),
            "tax": r.get("tax_2b"), "age_days": r.get("age_days"),
            "flags": r.get("flags"),
        })
    return out


def data_quality(result, gst_records, tally_records):
    """Defects in the source data, each one something a human can go and fix."""
    live = [g for g in gst_records if "superseded" not in g.get("flags", [])]

    def count_flag(records, flag):
        return [r for r in records if flag in r.get("flags", [])]

    checks = []

    def add(key, title, rows, tone, detail, amount=None):
        if not rows:
            return
        checks.append({
            "key": key, "title": title, "count": len(rows), "tone": tone,
            "detail": detail,
            "amount": amount if amount is not None
            else r2(sum(abs(to_float(r.get("tax") or r.get("tax_2b") or 0)) for r in rows)),
        })

    add("tax_split", "Impossible tax split", count_flag(live, "tax_split_error"), "risk",
        "IGST alongside CGST/SGST, or an unequal CGST/SGST split. Ask the supplier "
        "to amend; the credit is not safe as filed.")
    add("rate_invalid", "Tax does not match any slab", count_flag(live, "rate_invalid"),
        "risk", "The implied rate falls outside the statutory range.")
    add("value_split", "Invoice value does not tie to taxable plus tax",
        count_flag(live, "value_split_odd"), "warn",
        "Usually a discount or rounding entered only on one line of the invoice.")
    add("mixed_rate", "Mixed-slab invoices", count_flag(live, "mixed_rate"), "info",
        "Legitimate on multi-item bills. Verify only if the credit looks wrong.")
    add("prior_fy", "Documents dated before this financial year",
        count_flag(live, "prior_fy"), "warn",
        "These belong to the earlier year. Claiming them here invites a query.")
    add("later_fy", "Documents dated after this financial year",
        count_flag(live, "later_fy"), "warn",
        "Filed early by the supplier, or the year selection is wrong.")
    add("pos_mismatch", "Place of supply outside your state",
        count_flag(live, "pos_mismatch"), "risk",
        "CGST/SGST charged by another state is a dead credit.")
    add("itc_blocked", "Portal blocks the credit", count_flag(live, "itc_blocked"), "risk",
        "The 2B row itself says ITC is unavailable. Read the reason column.")
    add("rcm", "Reverse-charge documents", count_flag(live, "rcm"), "info",
        "Pay the tax in cash first, then take the credit.")
    add("no_vch_no", "Tally vouchers with no number", count_flag(tally_records, "no_vch_no"),
        "warn", "These can only be matched on party and value. Number them.",
        amount=r2(sum(abs(t["amt"]) for t in count_flag(tally_records, "no_vch_no"))))
    add("non_gst_vch", "Booked on a non-GST voucher type",
        count_flag(tally_records, "non_gst_vch"), "warn",
        "A plain Purchase voucher carries no tax ledger, so no credit flows from it.",
        amount=r2(sum(abs(t["amt"]) for t in count_flag(tally_records, "non_gst_vch"))))

    dupes = _duplicates(live)
    if dupes:
        checks.append({
            "key": "duplicates", "title": "Possible duplicate invoices",
            "count": len(dupes), "tone": "risk",
            "amount": r2(sum(d["tax"] for d in dupes)),
            "detail": "The same supplier, number and value appears more than once "
                      "in the return. Claiming both doubles the credit.",
            "rows": dupes[:20],
        })

    order = {"risk": 0, "warn": 1, "info": 2}
    checks.sort(key=lambda c: (order.get(c["tone"], 3), -c["count"]))
    return {
        "checks": checks,
        "clean": not any(c["tone"] == "risk" for c in checks),
        "defect_rows": sum(c["count"] for c in checks if c["tone"] in ("risk", "warn")),
    }


def _duplicates(records):
    from app.backend.common import clean_inv
    seen = defaultdict(list)
    for g in records:
        seen[(g["gstin"], clean_inv(g["inv_no"]), round(abs(g["inv_val"]), 2))].append(g)
    out = []
    for key, group in seen.items():
        if len(group) < 2:
            continue
        out.append({
            "gstin": key[0], "party": group[0]["party"], "inv_no": group[0]["inv_no"],
            "value": group[0]["inv_val"], "times": len(group),
            "tax": r2(sum(g["tax"] for g in group[1:])),
            "periods": sorted({g.get("period") or "" for g in group}),
            "sections": sorted({g.get("section") or "" for g in group}),
        })
    out.sort(key=lambda d: -d["tax"])
    return out


# ---------------------------------------------------------------------------
# Supplier follow-up
# ---------------------------------------------------------------------------

def followup_packets(result, party_wise, suppliers=None, entity=None, limit=None):
    """
    One packet per supplier with something outstanding: the list of documents,
    what to ask for, and a ready letter body.
    """
    suppliers = suppliers or {}
    entity = entity or {}
    by_gstin = defaultdict(list)
    for r in result["all_records"]:
        if r["status"] in ("In GSTR-2B only", "Value mismatch", "Invoice no. mismatch"):
            if r.get("gstin"):
                by_gstin[r["gstin"]].append(r)

    # Tally-only rows have no GSTIN, so they group by ledger name instead.
    by_name = defaultdict(list)
    for r in result["subsets"]["tally_only"]:
        by_name[r["party_tally"] or "(unnamed ledger)"].append(r)

    packets = []
    pw_index = {p["gstin"]: p for p in party_wise}

    for gstin, rows in by_gstin.items():
        pw = pw_index.get(gstin, {})
        contact = suppliers.get(gstin, {})
        tax = r2(sum(abs(r.get("tax_2b") or 0) for r in rows))
        packets.append(_packet(
            gstin=gstin,
            name=contact.get("name") or pw.get("party_name") or rows[0].get("party_2b"),
            contact=contact, rows=rows, tax=tax, pw=pw, entity=entity,
            kind="from_2b",
        ))

    for name, rows in by_name.items():
        value = r2(sum(abs(r.get("amt_tally") or 0) for r in rows))
        packets.append(_packet(
            gstin="", name=name, contact={}, rows=rows, tax=0.0,
            pw={}, entity=entity, kind="not_filed", value=value,
        ))

    packets.sort(key=lambda p: (-p["tax"], -p["value"]))
    return packets[:limit] if limit else packets


def _packet(gstin, name, contact, rows, tax, pw, entity, kind, value=None):
    value = value if value is not None else r2(sum(abs(r.get("inv_val_2b") or 0) for r in rows))
    ent_name = entity.get("entity_name") or "our company"
    ent_gstin = entity.get("gstin") or ""
    period = entity.get("fy_label") or entity.get("date_range") or "the period under review"

    lines = []
    for r in sorted(rows, key=lambda x: -(abs(x.get("tax_2b") or 0))):
        lines.append({
            "doc_no": r.get("inv_no_2b") or r.get("vch_no_tally"),
            "date": iso(r.get("inv_date_2b") or r.get("vch_date_tally"))
            if not isinstance(r.get("inv_date_2b") or r.get("vch_date_tally"), str)
            else (r.get("inv_date_2b") or r.get("vch_date_tally")),
            "status": r["status"],
            "value_2b": r.get("inv_val_2b"),
            "value_books": r.get("amt_tally"),
            "tax": r.get("tax_2b"),
            "fp": r.get("fp"),
        })

    if kind == "not_filed":
        subject = f"GSTR-1 not filed for {len(rows)} purchase(s) - {ent_name}"
        ask = ("These purchases are recorded in our books but do not appear in our "
               "GSTR-2B. Please confirm the return period in which they were reported, "
               "or file them so the credit becomes available to us.")
    else:
        subject = f"GSTR-2B reconciliation - {len(rows)} document(s) pending - {ent_name}"
        ask = ("The documents below appear in our GSTR-2B but do not agree with our "
               "books. Please send a copy of each invoice, and confirm the taxable "
               "value and tax charged, so that we can record them correctly.")

    body = "\n".join([
        f"Subject: {subject}",
        "",
        f"Dear {contact.get('contact') or 'Sir / Madam'},",
        "",
        f"We are reconciling our input tax credit for {period}"
        + (f" against GSTIN {ent_gstin}" if ent_gstin else "") + ".",
        "",
        ask,
        "",
        "Documents concerned:",
    ] + [
        f"  {i + 1}. {l['doc_no'] or '(no number)'}"
        f"   dated {l['date'] or 'n/a'}"
        f"   value {inr(l['value_2b'] if l['value_2b'] is not None else l['value_books'])}"
        + (f"   tax {inr(l['tax'])}" if l["tax"] else "")
        for i, l in enumerate(lines)
    ] + [
        "",
        f"Total value: {inr(value)}" + (f"    Tax involved: {inr(tax)}" if tax else ""),
        "",
        "An early reply will let us claim the credit within the statutory time limit.",
        "",
        "Regards,",
        entity.get("preparer") or ent_name,
    ])

    return {
        "gstin": gstin, "name": name, "kind": kind,
        "email": contact.get("email") or "", "phone": contact.get("phone") or "",
        "contact": contact.get("contact") or "",
        "count": len(rows), "tax": tax, "value": value,
        "grade": pw.get("grade"), "risk_score": pw.get("risk_score"),
        "subject": subject, "body": body, "lines": lines,
        "mailto": _mailto(contact.get("email"), subject, body),
    }


def _mailto(email, subject, body):
    from urllib.parse import quote
    if not email:
        return ""
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


# ---------------------------------------------------------------------------
# Action checklist
# ---------------------------------------------------------------------------

def checklist(result, itc, quality, sec164, sales_result=None):
    """
    What to do, in the order that recovers the most money for the least work.
    Every entry carries the view it opens so the UI can link straight to it.
    """
    items = []
    c = result["counts"]
    subsets = result["subsets"]

    def add(priority, title, detail, amount, count, view, filters=None, tone="info"):
        if not count:
            return
        items.append({
            "priority": priority, "title": title, "detail": detail,
            "amount": r2(amount), "count": count, "tone": tone,
            "view": view, "filters": filters or {},
        })

    add(1, f"Book {c['gst_only']} invoices your supplier already filed",
        "The credit is sitting in your 2B but the purchase is not in your books. "
        "Booking them is the single largest recovery available.",
        itc["recoverable_by_booking"], c["gst_only"], "purchase",
        {"status": "In GSTR-2B only"}, "risk")

    add(2, f"Chase {c['tally_only']} purchases that never reached GSTR-2B",
        "Your books carry these but the supplier has not filed them. Hold the "
        "credit and send a follow-up.",
        sum(abs(r.get("amt_tally") or 0) for r in subsets["tally_only"]),
        c["tally_only"], "purchase", {"status": "In Tally only"}, "warn")

    add(3, f"Correct {c['val_mismatch']} value mismatches",
        "Same invoice number, different amount. Fix the Tally value, or get a "
        "revised invoice, then re-run.",
        sum(abs(r.get("difference") or 0) for r in subsets["value_mismatch"]),
        c["val_mismatch"], "purchase", {"status": "Value mismatch"}, "warn")

    add(4, f"Review {c['probable']} probable matches",
        "Close on party, value and date. Confirm each one and it moves to matched.",
        sum(abs(r.get("tax_2b") or 0) for r in subsets["probable"]),
        c["probable"], "purchase", {"status": "Probable match"}, "info")

    add(5, f"Confirm {c['split']} split bookings",
        "One supplier invoice spread across several vouchers. Verify the parts add up.",
        sum(abs(r.get("inv_val_2b") or 0) for r in subsets["split"]),
        c["split"], "purchase", {"status": "Split booking"}, "info")

    add(6, f"Renumber {c['inv_mismatch']} vouchers to match the invoice",
        "Party and value agree, only the number differs. Aligning them makes next "
        "month reconcile itself.",
        sum(abs(r.get("tax_2b") or 0) for r in subsets["invoice_no_mismatch"]),
        c["inv_mismatch"], "purchase", {"status": "Invoice no. mismatch"}, "info")

    if sec164.get("applicable") and sec164.get("count"):
        if sec164.get("lapsed"):
            add(0, f"{sec164['count']} items are past the section 16(4) cut-off",
                f"The claim window closed on {sec164['deadline']}. This credit is lost "
                "unless the documents belong to a later year.",
                sec164["amount"], sec164["count"], "itc", {}, "risk")
        elif sec164.get("urgent"):
            add(0, f"{sec164['days_left']} days left to claim {compact_inr(sec164['amount'])}",
                f"Section 16(4) closes the window on {sec164['deadline']}. Book these "
                "purchases before then or the credit lapses permanently.",
                sec164["amount"], sec164["count"], "itc", {}, "risk")

    for check in quality["checks"]:
        if check["tone"] != "risk":
            continue
        add(7, check["title"], check["detail"], check["amount"], check["count"],
            "quality", {"check": check["key"]}, "risk")

    if sales_result:
        so = sales_result["counts"].get("tally_only") or 0
        add(2, f"{so} sales invoices are in Tally but not in GSTR-1",
            "Unreported outward supply. File an amendment before the annual return.",
            sales_result.get("unreported_sales_value") or 0, so, "sales",
            {"status": "In Tally only"}, "risk")

    items.sort(key=lambda x: (x["priority"], -abs(x["amount"])))
    return items


def build_insights(result, gst_records, tally_records, sales_result=None,
                   sales_records=None, suppliers=None, today=None):
    """Everything the dashboard needs, assembled once per run."""
    fy_start = (result.get("entity_info") or {}).get("fy_start")
    itc = itc_position(result, gst_records)
    quality = data_quality(result, gst_records, tally_records)
    watch = sec164_watch(result, gst_records, fy_start, today=today)
    return {
        "itc_position": itc,
        "gstr3b": gstr3b_working(result, itc, sales_result, sales_records),
        "rate_analysis": rate_analysis(gst_records),
        "monthly": monthly_series(result, fy_start),
        "aging": aging(result),
        "concentration": concentration(result["party_wise"]),
        "sec164": watch,
        "top_exposures": top_exposures(result),
        "quality": quality,
        "checklist": checklist(result, itc, quality, watch, sales_result),
        "followups": followup_packets(result, result["party_wise"], suppliers,
                                      result.get("entity_info")),
    }
