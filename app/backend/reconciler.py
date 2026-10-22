"""
The matching engine.

Purchase reconciliation runs in passes, each one stricter about what it is
willing to call a match than the last, and each one only looking at records the
earlier passes left behind:

  0. decisions the user recorded by hand on a previous run
  1. invoice number and value agree
  2. invoice number agrees, value does not
  3. value and party agree, invoice number does not
  4. value, party and date are close enough to be worth a human look
  5. one 2B invoice booked as several Tally vouchers, or the reverse
  6. whatever is left is reported one-sided

Amendment rows supersede the documents they amend, credit notes carry negative
tax, and every record leaves with a fingerprint so a decision taken today still
applies when the same period is reconciled next month.
"""

from datetime import datetime
from itertools import combinations

from app.backend.common import (
    AGING_BUCKETS, clean_inv, fp_2b, fp_tally, fy_label, iso, party_sim, r2,
    sec_164_deadline, state_of_gstin, tails_match, to_float,
)

STATUS_ORDER = [
    "Value mismatch",
    "Invoice no. mismatch",
    "Probable match",
    "Split booking",
    "In GSTR-2B only",
    "In Tally only",
    "Matched",
]

OPEN_STATUSES = {"Value mismatch", "Invoice no. mismatch", "Probable match",
                 "Split booking", "In GSTR-2B only", "In Tally only"}

STATUS_META = {
    "Matched": {
        "key": "matched", "tone": "clear",
        "blurb": "Invoice number and value agree within tolerance.",
    },
    "Value mismatch": {
        "key": "value_mismatch", "tone": "warn",
        "blurb": "Same invoice number on both sides, different amount.",
    },
    "Invoice no. mismatch": {
        "key": "inv_mismatch", "tone": "info",
        "blurb": "Party and value agree; the voucher number does not match the invoice number.",
    },
    "Probable match": {
        "key": "probable", "tone": "info",
        "blurb": "Close on party, value and date, but not close enough to post without a look.",
    },
    "Split booking": {
        "key": "split", "tone": "info",
        "blurb": "One invoice booked across several vouchers, or several invoices in one voucher.",
    },
    "In GSTR-2B only": {
        "key": "gst_only", "tone": "risk",
        "blurb": "The supplier filed it; your books do not have it.",
    },
    "In Tally only": {
        "key": "tally_only", "tone": "warn",
        "blurb": "Your books have it; it has not appeared in GSTR-2B.",
    },
}

DEFAULTS = {
    "tolerance": 2.0,
    "party_threshold": 0.50,
    "probable_threshold": 0.72,
    "value_pct_tolerance": 0.10,   # percent, for the probable pass
    "date_window_days": 45,
    "split_max_parts": 3,
    "split_candidate_cap": 14,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _age_bucket(days):
    if days is None:
        return None
    for label, lo, hi in AGING_BUCKETS:
        if lo <= days <= hi:
            return label
    return AGING_BUCKETS[-1][0]


def _days_between(a, b):
    if not a or not b:
        return None
    return abs((a - b).days)


def _eligible_itc(rec):
    """Creditable portion of a 2B document's tax."""
    if not rec.get("itc_eligible", True):
        return 0.0
    pct = rec.get("pct_applicable") or 100.0
    return r2(to_float(rec.get("tax")) * (pct / 100.0))


def _pair(status, g, t, *, score=None, basis=None, extra_flags=(), extra_note=""):
    """Build a reconciliation record from a 2B document and a Tally voucher."""
    g_date = g.get("inv_date") if g else None
    t_date = t.get("date") if t else None
    flags = list(g.get("flags", []) if g else []) + \
            [f for f in (t.get("flags", []) if t else []) if f not in ("credit_note", "debit_note")]
    flags += list(extra_flags)

    gap = _days_between(g_date, t_date)
    if gap is not None and gap > 31:
        flags.append("date_gap")

    notes_2b = " ".join(x for x in [g.get("note") if g else "", extra_note] if x).strip()

    return {
        "status": status,
        "fp": fp_2b(g["gstin"], g["inv_no"], g["inv_val"]) if g else fp_tally(
            t["party"], t["vch_no"], t["amt"]),
        "tally_fp": fp_tally(t["party"], t["vch_no"], t["amt"]) if t else None,
        "section": g.get("section") if g else "TALLY",
        "gstin": g.get("gstin") if g else (t.get("gstin") or None),
        "supplier_state": state_of_gstin(g.get("gstin")) if g else "",
        "party_2b": g.get("party") if g else None,
        "party_tally": t.get("party") if t else None,
        "inv_no_2b": g.get("inv_no") if g else None,
        "vch_no_tally": t.get("vch_no") if t else None,
        "inv_date_2b": g_date,
        "vch_date_tally": t_date,
        "inv_val_2b": g.get("inv_val") if g else None,
        "amt_tally": t.get("amt") if t else None,
        "difference": r2(g["inv_val"] - t["amt"]) if (g and t) else None,
        "taxable_2b": g.get("taxable") if g else None,
        "tax_2b": g.get("tax") if g else None,
        "eligible_itc": _eligible_itc(g) if g else None,
        "igst_2b": g.get("igst") if g else None,
        "cgst_2b": g.get("cgst") if g else None,
        "sgst_2b": g.get("sgst") if g else None,
        "cess_2b": g.get("cess") if g else None,
        "rate": g.get("rate") if g else None,
        "rate_slab": g.get("rate_slab") if g else None,
        "inv_type": g.get("inv_type") if g else None,
        "period_2b": g.get("period") if g else None,
        "filing_date": g.get("filing_date") if g else None,
        "itc_avail": g.get("itc_avail") if g else None,
        "reason": g.get("reason") if g else None,
        "e_invoice": g.get("e_invoice") if g else None,
        "pos": g.get("pos") if g else None,
        "vch_type": t.get("vch_type") if t else None,
        "note_2b": notes_2b or None,
        "note_tally": (t.get("note") or None) if t else None,
        "narration_tally": (t.get("narration") or None) if t else None,
        "month_key": (g.get("month_key") if g else None) or (t.get("month_key") if t else None),
        "date_gap_days": gap,
        "match_score": score,
        "match_basis": basis,
        "flags": sorted(set(flags)),
        "parts": None,
    }


def _one_sided_2b(g):
    rec = _pair("In GSTR-2B only", g, None)
    rec["match_basis"] = "no counterpart in Tally"
    return rec


def _one_sided_tally(t):
    rec = _pair("In Tally only", None, t)
    rec["match_basis"] = "no counterpart in GSTR-2B"
    return rec


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def reconcile_data(gst_records, tally_records, tolerance=2.0, entity_info=None,
                   settings=None, decisions=None, today=None):
    """
    Reconcile GSTR-2B documents against Tally vouchers.

    `decisions` is the fingerprint-keyed map from the store; a recorded manual
    match is honoured before any automatic pass runs, and an item marked
    resolved or ignored keeps its status but carries the decision forward.
    """
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    cfg["tolerance"] = float(tolerance if tolerance is not None else cfg["tolerance"])
    decisions = decisions or {}
    today = today or datetime.now()
    today = datetime(today.year, today.month, today.day)     # ages in whole days

    entity_info = dict(entity_info or {})
    fy_start = entity_info.get("fy_start")
    entity_gstin = (entity_info.get("gstin") or "").upper()
    home_state = state_of_gstin(entity_gstin)

    # --- amendments supersede the documents they amend -------------------
    amended_keys = set()
    for g in gst_records:
        if g.get("section") in ("B2BA", "CDNRA", "ISDA") and g.get("orig_inv_no"):
            amended_keys.add((g["gstin"], clean_inv(g["orig_inv_no"])))
    superseded = []
    live_gst = []
    for g in gst_records:
        if g.get("section") in ("B2B", "CDNR", "ISD") and \
                (g["gstin"], clean_inv(g["inv_no"])) in amended_keys:
            # Flagged on the caller's row too: the credit working and the other
            # summaries read these rows, and without it counted both the bill
            # and its amendment.
            g["flags"] = sorted(set(list(g.get("flags", [])) + ["superseded"]))
            g = dict(g)
            g["note"] = (g.get("note", "") + " Superseded by an amendment row.").strip()
            superseded.append(g)
        else:
            live_gst.append(g)

    # --- place-of-supply eligibility, which needs the entity GSTIN -------
    if home_state:
        for g in live_gst:
            pos = (g.get("pos") or "").strip()
            intra = abs(to_float(g.get("cgst"))) > 0.5 or abs(to_float(g.get("sgst"))) > 0.5
            if pos and intra and pos.lower() != home_state.lower():
                g["flags"] = sorted(set(list(g.get("flags", [])) + ["pos_mismatch"]))
                g["note"] = (g.get("note", "") +
                             f" Place of supply is {pos} but you are registered in "
                             f"{home_state} - CGST/SGST of another state is not creditable.").strip()

    used_g, used_t = set(), set()
    tally_by_id = {t["id"]: t for t in tally_records}

    # Indexes, built once.
    tally_by_inv = {}
    for t in tally_records:
        ci = clean_inv(t["vch_no"])
        if ci:
            tally_by_inv.setdefault(ci, []).append(t)

    buckets = {s: [] for s in STATUS_ORDER}

    # ---------------------------------------------------------------
    # Pass 0: decisions taken by hand on an earlier run
    # ---------------------------------------------------------------
    manual_pairs = 0
    if decisions:
        tally_by_fp = {fp_tally(t["party"], t["vch_no"], t["amt"]): t for t in tally_records}
        for g in live_gst:
            fp = fp_2b(g["gstin"], g["inv_no"], g["inv_val"])
            dec = decisions.get(fp)
            if not dec:
                continue
            if dec["action"] == "match" and dec.get("counterpart"):
                t = tally_by_fp.get(dec["counterpart"])
                if t and t["id"] not in used_t:
                    rec = _pair("Matched", g, t, score=1.0, basis="matched by hand",
                                extra_flags=("manual_match",),
                                extra_note="Matched manually" +
                                           (f": {dec['note']}" if dec.get("note") else "."))
                    buckets["Matched"].append(rec)
                    used_g.add(g["id"])
                    used_t.add(t["id"])
                    manual_pairs += 1
            elif dec["action"] == "ignore":
                rec = _one_sided_2b(g)
                rec["flags"] = sorted(set(rec["flags"] + ["ignored"]))
                rec["match_basis"] = "written off by hand"
                buckets["In GSTR-2B only"].append(rec)
                used_g.add(g["id"])

    # ---------------------------------------------------------------
    # Pass 1: invoice number and value agree
    # ---------------------------------------------------------------
    tol = cfg["tolerance"]
    for g in live_gst:
        if g["id"] in used_g:
            continue
        ci = clean_inv(g["inv_no"])
        if not ci or ci not in tally_by_inv:
            continue
        cands = [t for t in tally_by_inv[ci]
                 if t["id"] not in used_t and abs(t["amt"] - g["inv_val"]) <= tol]
        if not cands:
            continue
        cands.sort(key=lambda t: (-party_sim(g["party"], t["party"]),
                                  _days_between(g["inv_date"], t["date"]) or 999))
        best = cands[0]
        sim = party_sim(g["party"], best["party"])
        flags = ("party_differs",) if sim < cfg["party_threshold"] else ()
        note = ("Invoice number and value agree but the ledger name is different - "
                "confirm this is the same supplier.") if flags else ""
        buckets["Matched"].append(_pair(
            "Matched", g, best, score=1.0, basis="invoice no. + value",
            extra_flags=flags, extra_note=note))
        used_g.add(g["id"])
        used_t.add(best["id"])

    # ---------------------------------------------------------------
    # Pass 2: invoice number agrees, value does not
    # ---------------------------------------------------------------
    for g in live_gst:
        if g["id"] in used_g:
            continue
        ci = clean_inv(g["inv_no"])
        if not ci or ci not in tally_by_inv:
            continue
        cands = [t for t in tally_by_inv[ci]
                 if t["id"] not in used_t
                 and party_sim(g["party"], t["party"]) >= 0.40]
        if not cands:
            continue
        cands.sort(key=lambda t: abs(t["amt"] - g["inv_val"]))
        best = cands[0]
        diff = r2(g["inv_val"] - best["amt"])
        pct = abs(diff) / abs(g["inv_val"]) * 100 if g["inv_val"] else 0
        extra = ("rounding_only",) if abs(diff) <= 10 else ()
        note = ("Difference is rounding-level." if extra else
                f"Books differ from the return by {pct:.2f}% of the invoice value.")
        buckets["Value mismatch"].append(_pair(
            "Value mismatch", g, best,
            score=round(party_sim(g["party"], best["party"]), 3),
            basis="invoice no. matched, value differs",
            extra_flags=extra, extra_note=note))
        used_g.add(g["id"])
        used_t.add(best["id"])

    # ---------------------------------------------------------------
    # Pass 3: value and party agree, invoice number does not
    # ---------------------------------------------------------------
    open_tally = [t for t in tally_records if t["id"] not in used_t]
    for g in live_gst:
        if g["id"] in used_g:
            continue
        best, best_sim = None, 0.0
        for t in open_tally:
            if t["id"] in used_t:
                continue
            if abs(t["amt"] - g["inv_val"]) > tol:
                continue
            sim = party_sim(g["party"], t["party"])
            if sim < cfg["party_threshold"]:
                continue
            # A shared numeric tail is strong evidence of the same document.
            if tails_match(g["inv_no"], t["vch_no"]):
                sim = min(1.0, sim + 0.15)
            if sim > best_sim:
                best, best_sim = t, sim
        if best is None:
            continue
        shares_tail = tails_match(g["inv_no"], best["vch_no"])
        buckets["Invoice no. mismatch"].append(_pair(
            "Invoice no. mismatch", g, best, score=round(best_sim, 3),
            basis="party + value" + (" + invoice tail" if shares_tail else ""),
            extra_flags=("tail_match",) if shares_tail else (),
            extra_note="The trailing digits of both numbers agree." if shares_tail else ""))
        used_g.add(g["id"])
        used_t.add(best["id"])

    # ---------------------------------------------------------------
    # Pass 4: close enough to deserve a human look
    # ---------------------------------------------------------------
    window = cfg["date_window_days"]
    pct_tol = cfg["value_pct_tolerance"] / 100.0
    open_tally = [t for t in tally_records if t["id"] not in used_t]
    for g in live_gst:
        if g["id"] in used_g:
            continue
        if not g["inv_val"]:
            continue
        allow = max(tol, abs(g["inv_val"]) * pct_tol)
        best, best_score, best_why = None, 0.0, ""
        for t in open_tally:
            if t["id"] in used_t:
                continue
            vdiff = abs(t["amt"] - g["inv_val"])
            if vdiff > allow:
                continue
            gap = _days_between(g["inv_date"], t["date"])
            if gap is not None and gap > window:
                continue
            sim = party_sim(g["party"], t["party"])
            if sim < 0.35:
                continue
            value_score = 1.0 - (vdiff / allow if allow else 0)
            date_score = 1.0 - ((gap or 0) / window if window else 0)
            score = 0.55 * sim + 0.30 * value_score + 0.15 * date_score
            if score > best_score:
                best, best_score = t, score
                best_why = (f"party {sim * 100:.0f}%, value off by "
                            f"{vdiff:,.2f}, {gap if gap is not None else '?'} days apart")
        if best is not None and best_score >= cfg["probable_threshold"]:
            buckets["Probable match"].append(_pair(
                "Probable match", g, best, score=round(best_score, 3),
                basis="fuzzy party + near value + date window",
                extra_flags=("needs_review",),
                extra_note=f"Suggested match on {best_why}. Confirm before posting."))
            used_g.add(g["id"])
            used_t.add(best["id"])

    # ---------------------------------------------------------------
    # Pass 5: split and consolidated bookings
    # ---------------------------------------------------------------
    open_tally = [t for t in tally_records if t["id"] not in used_t]
    for g in live_gst:
        if g["id"] in used_g or not g["inv_val"]:
            continue
        cands = [t for t in open_tally
                 if t["id"] not in used_t
                 and party_sim(g["party"], t["party"]) >= cfg["party_threshold"]
                 and abs(t["amt"]) < abs(g["inv_val"])
                 and (_days_between(g["inv_date"], t["date"]) or 0) <= window]
        if len(cands) < 2:
            continue
        cands.sort(key=lambda t: -abs(t["amt"]))
        cands = cands[: cfg["split_candidate_cap"]]
        found = None
        for size in range(2, cfg["split_max_parts"] + 1):
            for combo in combinations(cands, size):
                if abs(sum(t["amt"] for t in combo) - g["inv_val"]) <= tol:
                    found = combo
                    break
            if found:
                break
        if not found:
            continue
        total = r2(sum(t["amt"] for t in found))
        lead = found[0]
        rec = _pair("Split booking", g, lead,
                    score=round(party_sim(g["party"], lead["party"]), 3),
                    basis=f"one invoice booked as {len(found)} vouchers",
                    extra_flags=("split_booking",),
                    extra_note=f"Invoice value equals the sum of {len(found)} Tally "
                               f"vouchers ({', '.join(t['vch_no'] or '(no number)' for t in found)}).")
        rec["amt_tally"] = total
        rec["difference"] = r2(g["inv_val"] - total)
        rec["vch_no_tally"] = " + ".join(t["vch_no"] or "(no number)" for t in found)
        rec["parts"] = [{
            "vch_no": t["vch_no"], "party": t["party"], "date": t["date"],
            "amt": t["amt"], "vch_type": t["vch_type"],
            "fp": fp_tally(t["party"], t["vch_no"], t["amt"]),
        } for t in found]
        buckets["Split booking"].append(rec)
        used_g.add(g["id"])
        for t in found:
            used_t.add(t["id"])

    # ---------------------------------------------------------------
    # Pass 6: one-sided
    # ---------------------------------------------------------------
    for g in live_gst:
        if g["id"] not in used_g:
            buckets["In GSTR-2B only"].append(_one_sided_2b(g))
    for t in tally_records:
        if t["id"] not in used_t:
            buckets["In Tally only"].append(_one_sided_tally(t))

    # ---------------------------------------------------------------
    # Enrichment: ageing, section 16(4), recorded decisions
    # ---------------------------------------------------------------
    deadline = sec_164_deadline(fy_start) if fy_start else None
    for status, rows in buckets.items():
        for rec in rows:
            ref = rec["inv_date_2b"] or rec["vch_date_tally"]
            age = (today - ref).days if ref else None
            rec["age_days"] = age
            rec["aging_bucket"] = _age_bucket(age)
            if deadline and status in OPEN_STATUSES and rec["tax_2b"]:
                rec["itc_deadline"] = iso(deadline)
                left = (deadline - today).days
                rec["days_to_deadline"] = left
                if left < 0:
                    rec["flags"] = sorted(set(rec["flags"] + ["sec164_lapsed"]))
                elif left <= 60:
                    rec["flags"] = sorted(set(rec["flags"] + ["sec164_due"]))
            dec = decisions.get(rec["fp"])
            if dec:
                rec["decision"] = {"action": dec["action"], "note": dec.get("note"),
                                   "updated_at": dec.get("updated_at")}
                if dec["action"] in ("resolved", "ignore"):
                    rec["flags"] = sorted(set(rec["flags"] + ["resolved"]))
                elif dec["action"] == "follow_up":
                    rec["flags"] = sorted(set(rec["flags"] + ["follow_up"]))
            else:
                rec["decision"] = None

    for status in buckets:
        buckets[status].sort(key=lambda x: (
            -(abs(x.get("tax_2b") or 0)), (x.get("party_2b") or x.get("party_tally") or ""),
            str(x.get("inv_no_2b") or x.get("vch_no_tally") or "")))

    all_records = []
    for status in STATUS_ORDER:
        all_records.extend(buckets[status])
    for rec in superseded:
        item = _one_sided_2b(rec)
        item["status"] = "Superseded"
        item["age_days"] = None
        item["aging_bucket"] = None
        item["decision"] = None
        all_records.append(item)

    return _assemble(all_records, buckets, superseded, live_gst, tally_records,
                     gst_records, cfg, entity_info, manual_pairs, today, deadline)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def _assemble(all_records, buckets, superseded, live_gst, tally_records, gst_records,
              cfg, entity_info, manual_pairs, today, deadline):
    matched = buckets["Matched"]
    val_mis = buckets["Value mismatch"]
    inv_mis = buckets["Invoice no. mismatch"]
    probable = buckets["Probable match"]
    split = buckets["Split booking"]
    gst_only = buckets["In GSTR-2B only"]
    tally_only = buckets["In Tally only"]

    def s(rows, key):
        return r2(sum(r.get(key) or 0 for r in rows))

    tot_2b_val = s(live_gst, "inv_val")
    tot_2b_tax = s(live_gst, "tax")
    tot_2b_taxable = s(live_gst, "taxable")
    tot_tally_val = s(tally_records, "amt")

    linked = matched + val_mis + inv_mis + probable + split
    status_summary = [
        {"status": "Matched (invoice no. + value agree)", "key": "matched",
         "count": len(matched), "val_2b": s(matched, "inv_val_2b"),
         "val_tally": s(matched, "amt_tally"), "tax": s(matched, "tax_2b")},
        {"status": "Value mismatch (same invoice no.)", "key": "value_mismatch",
         "count": len(val_mis), "val_2b": s(val_mis, "inv_val_2b"),
         "val_tally": s(val_mis, "amt_tally"), "tax": s(val_mis, "tax_2b")},
        {"status": "Invoice no. mismatch (party + value agree)", "key": "inv_mismatch",
         "count": len(inv_mis), "val_2b": s(inv_mis, "inv_val_2b"),
         "val_tally": s(inv_mis, "amt_tally"), "tax": s(inv_mis, "tax_2b")},
        {"status": "Probable match (needs review)", "key": "probable",
         "count": len(probable), "val_2b": s(probable, "inv_val_2b"),
         "val_tally": s(probable, "amt_tally"), "tax": s(probable, "tax_2b")},
        {"status": "Split booking (one invoice, many vouchers)", "key": "split",
         "count": len(split), "val_2b": s(split, "inv_val_2b"),
         "val_tally": s(split, "amt_tally"), "tax": s(split, "tax_2b")},
        {"status": "In GSTR-2B, not in Tally", "key": "gst_only",
         "count": len(gst_only), "val_2b": s(gst_only, "inv_val_2b"),
         "val_tally": 0.0, "tax": s(gst_only, "tax_2b")},
        {"status": "In Tally, not in GSTR-2B", "key": "tally_only",
         "count": len(tally_only), "val_2b": 0.0,
         "val_tally": s(tally_only, "amt_tally"), "tax": 0.0},
        {"status": "Total", "key": "total",
         "count": len(matched) + len(val_mis) + len(inv_mis) + len(probable) +
                  len(split) + len(gst_only) + len(tally_only),
         "val_2b": tot_2b_val, "val_tally": tot_tally_val, "tax": tot_2b_tax},
    ]

    itc_unbooked = s(gst_only, "tax_2b")
    itc_unbooked_eligible = s(gst_only, "eligible_itc")
    itc_val_mis = s(val_mis, "tax_2b")
    itc_inv_mis = s(inv_mis, "tax_2b")
    itc_probable = s(probable, "tax_2b")
    tally_only_val = s(tally_only, "amt_tally")

    itc_impact = [
        {"title": "ITC in 2B on invoices not booked in Tally",
         "amount": itc_unbooked, "count": len(gst_only), "tone": "risk",
         "action": "Purchase may be unrecorded - book it or forgo the credit"},
        {"title": "Value of Tally purchases absent from 2B",
         "amount": tally_only_val, "count": len(tally_only), "tone": "warn",
         "action": "Supplier has not filed or filed under a different GSTIN - "
                   "hold the credit until it appears"},
        {"title": "ITC on value-mismatch invoices",
         "amount": itc_val_mis, "count": len(val_mis), "tone": "warn",
         "action": "Correct the Tally value, then re-run"},
        {"title": "ITC on invoice-no. mismatch invoices",
         "amount": itc_inv_mis, "count": len(inv_mis), "tone": "info",
         "action": "Align the voucher number with the supplier invoice number"},
        {"title": "ITC on probable matches awaiting review",
         "amount": itc_probable, "count": len(probable), "tone": "info",
         "action": "Confirm or reject each suggestion in the Purchase view"},
    ]

    # --- party-wise rollup, GSTR-2B basis ---------------------------
    groups = {}
    for g in live_gst:
        key = g["gstin"] or "(no GSTIN)"
        grp = groups.setdefault(key, {
            "gstin": key, "party_name": g["party"], "state": state_of_gstin(key),
            "count_2b": 0, "val_2b": 0.0, "taxable_2b": 0.0, "itc_2b": 0.0,
            "eligible_itc": 0.0, "matched_count": 0, "matched_val_tally": 0.0,
            "unmatched_count": 0, "unmatched_val": 0.0, "itc_at_risk": 0.0,
            "mismatch_count": 0, "credit_notes": 0, "e_invoices": 0,
            "filing_lags": [], "months": set(), "first_date": None, "last_date": None,
        })
        grp["count_2b"] += 1
        grp["val_2b"] += g["inv_val"]
        grp["taxable_2b"] += g["taxable"]
        grp["itc_2b"] += g["tax"]
        grp["eligible_itc"] += _eligible_itc(g)
        if "credit_note" in g.get("flags", []):
            grp["credit_notes"] += 1
        if g.get("e_invoice"):
            grp["e_invoices"] += 1
        if g.get("month_key"):
            grp["months"].add(g["month_key"])
        if g.get("inv_date") and g.get("filing_date"):
            grp["filing_lags"].append((g["filing_date"] - g["inv_date"]).days)
        for field, val in (("first_date", g.get("inv_date")), ("last_date", g.get("inv_date"))):
            if not val:
                continue
            cur = grp[field]
            if cur is None or (field == "first_date" and val < cur) or \
                    (field == "last_date" and val > cur):
                grp[field] = val

    for rec in all_records:
        gstin = rec.get("gstin")
        if not gstin or gstin not in groups:
            continue
        grp = groups[gstin]
        st = rec["status"]
        if st in ("Matched", "Value mismatch", "Invoice no. mismatch",
                  "Probable match", "Split booking"):
            grp["matched_count"] += 1
            grp["matched_val_tally"] += (rec["amt_tally"] or 0.0)
            if st != "Matched":
                grp["mismatch_count"] += 1
        elif st == "In GSTR-2B only":
            grp["unmatched_count"] += 1
            grp["unmatched_val"] += (rec["inv_val_2b"] or 0.0)
            grp["itc_at_risk"] += (rec["tax_2b"] or 0.0)

    party_wise = []
    for grp in groups.values():
        lags = grp.pop("filing_lags")
        months = grp.pop("months")
        grp["months_active"] = len(months)
        grp["avg_filing_lag"] = round(sum(lags) / len(lags), 1) if lags else None
        grp["late_filings"] = sum(1 for l in lags if l > 45)
        grp["first_date"] = iso(grp["first_date"])
        grp["last_date"] = iso(grp["last_date"])
        for f in ("val_2b", "taxable_2b", "itc_2b", "eligible_itc",
                  "matched_val_tally", "unmatched_val", "itc_at_risk"):
            grp[f] = r2(grp[f])
        grp["match_rate"] = round(grp["matched_count"] / grp["count_2b"] * 100, 1) \
            if grp["count_2b"] else 0.0
        grp["risk_score"], grp["grade"] = _supplier_grade(grp)
        party_wise.append(grp)

    party_wise.sort(key=lambda p: (-p["itc_at_risk"], -p["unmatched_val"], p["party_name"]))

    linked_count = len(linked)
    total_2b = len(live_gst)
    match_rate = round(linked_count / total_2b * 100, 1) if total_2b else 0.0
    clean_rate = round(len(matched) / total_2b * 100, 1) if total_2b else 0.0

    health = _health(clean_rate, itc_unbooked, tot_2b_tax, len(gst_only),
                     len(tally_only), total_2b)

    return {
        "entity_info": {
            **entity_info,
            "fy_label": fy_label(entity_info["fy_start"]) if entity_info.get("fy_start") else "",
            "generated_at": iso(today),
        },
        "config": cfg,
        "control_totals": {
            "invoices_in_2b": len(live_gst),
            "invoice_value_2b": tot_2b_val,
            "taxable_value_2b": tot_2b_taxable,
            "total_itc_2b": tot_2b_tax,
            "eligible_itc_2b": r2(sum(_eligible_itc(g) for g in live_gst)),
            "vouchers_in_tally": len(tally_records),
            "value_in_tally": tot_tally_val,
            "difference": r2(tot_2b_val - tot_tally_val),
            "superseded_rows": len(superseded),
            "rows_in_source_2b": len(gst_records),
            "manual_matches": manual_pairs,
        },
        "status_summary": status_summary,
        "itc_impact": itc_impact,
        "counts": {
            "matched": len(matched), "val_mismatch": len(val_mis),
            "inv_mismatch": len(inv_mis), "probable": len(probable),
            "split": len(split), "gst_only": len(gst_only),
            "tally_only": len(tally_only),
            "total_reconciled": len(all_records) - len(superseded),
            "superseded": len(superseded),
            "linked": linked_count,
            "open": len(val_mis) + len(inv_mis) + len(probable) + len(split) +
                    len(gst_only) + len(tally_only),
            "resolved": sum(1 for r in all_records if "resolved" in r.get("flags", [])),
        },
        "rates": {
            "match_rate": match_rate,
            "clean_match_rate": clean_rate,
            "itc_secured_pct": round((tot_2b_tax - itc_unbooked) / tot_2b_tax * 100, 1)
            if tot_2b_tax else 0.0,
        },
        "health": health,
        "all_records": all_records,
        "subsets": {
            "matched": matched, "value_mismatch": val_mis,
            "invoice_no_mismatch": inv_mis, "probable": probable,
            "split": split, "gst_only": gst_only, "tally_only": tally_only,
            "superseded": [r for r in all_records if r["status"] == "Superseded"],
        },
        "party_wise": party_wise,
        "itc_unbooked_eligible": itc_unbooked_eligible,
        "sec164": {
            "deadline": iso(deadline) if deadline else None,
            "days_left": (deadline - today).days if deadline else None,
            "itc_at_stake": itc_unbooked_eligible,
        },
    }


def _supplier_grade(grp):
    """Composite risk score 0-100 for one supplier, plus a letter grade.

    Weighted on what actually costs money: unclaimed credit first, then how
    reliably the supplier files, then how messy their paperwork is.
    """
    score = 0.0
    if grp["count_2b"]:
        score += (grp["unmatched_count"] / grp["count_2b"]) * 45
        score += (grp["mismatch_count"] / grp["count_2b"]) * 15
    if grp["itc_2b"]:
        score += min(abs(grp["itc_at_risk"]) / abs(grp["itc_2b"]), 1.0) * 25
    lag = grp.get("avg_filing_lag")
    if lag is not None:
        score += min(max(lag - 30, 0) / 60.0, 1.0) * 10
    if grp["count_2b"]:
        score += (grp["late_filings"] / grp["count_2b"]) * 5
    score = round(min(score, 100.0), 1)
    grade = "A" if score < 8 else "B" if score < 20 else "C" if score < 40 else \
            "D" if score < 65 else "E"
    return score, grade


def _health(clean_rate, itc_unbooked, tot_tax, gst_only_n, tally_only_n, total_2b):
    """One headline number the user can act on, with the reasons behind it."""
    exposure = (abs(itc_unbooked) / abs(tot_tax) * 100) if tot_tax else 0.0
    score = clean_rate - exposure * 1.5
    one_sided = ((gst_only_n + tally_only_n) / total_2b * 100) if total_2b else 0
    score -= max(one_sided - 5, 0) * 0.4
    score = round(max(min(score, 100.0), 0.0), 1)

    if score >= 92:
        verdict, tone = "Filing-ready", "clear"
    elif score >= 80:
        verdict, tone = "Minor cleanup", "clear"
    elif score >= 60:
        verdict, tone = "Needs attention", "warn"
    else:
        verdict, tone = "Material gaps", "risk"

    return {
        "score": score, "verdict": verdict, "tone": tone,
        "clean_match_rate": clean_rate,
        "itc_exposure_pct": round(exposure, 2),
        "one_sided_pct": round(one_sided, 2),
        "drivers": [
            {"label": "Clean matches", "value": clean_rate, "unit": "%", "good": clean_rate >= 90},
            {"label": "ITC exposed", "value": round(exposure, 2), "unit": "%", "good": exposure <= 1},
            {"label": "One-sided rows", "value": round(one_sided, 2), "unit": "%", "good": one_sided <= 5},
        ],
    }


# ---------------------------------------------------------------------------
# Sales: Tally sales register vs filed GSTR-1
# ---------------------------------------------------------------------------

def reconcile_sales(gstr1_records, tally_records, tolerance=2.0, entity_info=None,
                    settings=None, today=None):
    """Compare the outward supplies actually filed in GSTR-1 against the sales
    booked in Tally. Shape mirrors the purchase result so the UI can reuse it."""
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    cfg["tolerance"] = float(tolerance if tolerance is not None else cfg["tolerance"])
    today = today or datetime.now()
    tol = cfg["tolerance"]

    used_t = set()
    tally_by_inv = {}
    for t in tally_records:
        ci = clean_inv(t["vch_no"])
        if ci:
            tally_by_inv.setdefault(ci, []).append(t)

    matched, val_mis, r1_only = [], [], []

    for g in gstr1_records:
        ci = clean_inv(g["inv_no"])
        cands = [t for t in tally_by_inv.get(ci, []) if t["id"] not in used_t]
        exact = [t for t in cands if abs(t["amt"] - g["inv_val"]) <= tol]
        if exact:
            t = exact[0]
            matched.append(_sales_row("Matched", g, t))
            used_t.add(t["id"])
        elif cands:
            t = min(cands, key=lambda x: abs(x["amt"] - g["inv_val"]))
            val_mis.append(_sales_row("Value mismatch", g, t))
            used_t.add(t["id"])
        else:
            r1_only.append(_sales_row("In GSTR-1 only", g, None))

    tally_only = [_sales_row("In Tally only", None, t)
                  for t in tally_records if t["id"] not in used_t]

    all_rows = val_mis + r1_only + tally_only + matched
    tot_r1 = r2(sum(g["inv_val"] for g in gstr1_records))
    tot_tally = r2(sum(t["amt"] for t in tally_records))
    tot_tax = r2(sum(g["tax"] for g in gstr1_records))

    return {
        "entity_info": {**(entity_info or {}), "generated_at": iso(today)},
        "config": cfg,
        "control_totals": {
            "invoices_in_gstr1": len(gstr1_records),
            "value_in_gstr1": tot_r1,
            "tax_in_gstr1": tot_tax,
            "taxable_in_gstr1": r2(sum(g["taxable"] for g in gstr1_records)),
            "vouchers_in_tally": len(tally_records),
            "value_in_tally": tot_tally,
            "difference": r2(tot_r1 - tot_tally),
        },
        "counts": {
            "matched": len(matched), "val_mismatch": len(val_mis),
            "gstr1_only": len(r1_only), "tally_only": len(tally_only),
            "total_reconciled": len(all_rows),
        },
        "status_summary": [
            {"status": "Matched", "key": "matched", "count": len(matched),
             "val_2b": r2(sum(r["val_gstr1"] or 0 for r in matched)),
             "val_tally": r2(sum(r["amt_tally"] or 0 for r in matched))},
            {"status": "Value mismatch", "key": "value_mismatch", "count": len(val_mis),
             "val_2b": r2(sum(r["val_gstr1"] or 0 for r in val_mis)),
             "val_tally": r2(sum(r["amt_tally"] or 0 for r in val_mis))},
            {"status": "Filed in GSTR-1, not in Tally", "key": "gstr1_only",
             "count": len(r1_only),
             "val_2b": r2(sum(r["val_gstr1"] or 0 for r in r1_only)), "val_tally": 0.0},
            {"status": "In Tally, not filed in GSTR-1", "key": "tally_only",
             "count": len(tally_only), "val_2b": 0.0,
             "val_tally": r2(sum(r["amt_tally"] or 0 for r in tally_only))},
            {"status": "Total", "key": "total", "count": len(all_rows),
             "val_2b": tot_r1, "val_tally": tot_tally},
        ],
        "all_records": all_rows,
        "subsets": {
            "matched": matched, "value_mismatch": val_mis,
            "gstr1_only": r1_only, "tally_only": tally_only,
        },
        "unreported_sales_value": r2(sum(r["amt_tally"] or 0 for r in tally_only)),
    }


def _sales_row(status, g, t):
    gd = g.get("inv_date") if g else None
    td = t.get("date") if t else None
    return {
        "status": status,
        "fp": fp_2b(g.get("gstin") or "", g["inv_no"], g["inv_val"]) if g
        else fp_tally(t["party"], t["vch_no"], t["amt"]),
        "section": g.get("section") if g else "TALLY",
        "gstin": g.get("gstin") if g else (t.get("gstin") or None),
        "party_gstr1": g.get("party") if g else None,
        "party_tally": t.get("party") if t else None,
        "inv_no_gstr1": g.get("inv_no") if g else None,
        "vch_no_tally": t.get("vch_no") if t else None,
        "inv_date_gstr1": gd,
        "vch_date_tally": td,
        "val_gstr1": g.get("inv_val") if g else None,
        "amt_tally": t.get("amt") if t else None,
        "difference": r2(g["inv_val"] - t["amt"]) if (g and t) else None,
        "taxable_gstr1": g.get("taxable") if g else None,
        "tax_gstr1": g.get("tax") if g else None,
        "igst": g.get("igst") if g else None,
        "cgst": g.get("cgst") if g else None,
        "sgst": g.get("sgst") if g else None,
        "rate": g.get("rate") if g else None,
        "pos": g.get("pos") if g else None,
        "vch_type": t.get("vch_type") if t else None,
        "month_key": (g.get("month_key") if g else None) or (t.get("month_key") if t else None),
        "flags": [],
    }
