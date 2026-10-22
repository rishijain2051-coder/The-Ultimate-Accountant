"""
Why each open item is open, in words anyone can follow.

A status such as "In Tally only" tells an accountant the category; it tells
nobody why *this* entry landed there. For every unresolved entry this works
that out from the finished result: it describes the entry (saying so when the
description is blank or only a "."), re-asks each question the matcher asked,
names the nearest thing on the other side and exactly why that was not paired,
then says what it costs and what to do.

Only the saved payload is read, so a run saved before this existed is
explained the next time it is opened.
"""

import re
from functools import lru_cache

from app.backend.bank import _name_score
from app.backend.common import clean_inv, inr, party_sim, tails_match, to_date
from app.backend.reconciler import OPEN_STATUSES as GST_OPEN
from app.backend.statements import OPEN_STATUSES as STATEMENT_OPEN

BANK_SETTLED = {"Matched", "Batched"}
SALES_OPEN = {"Value mismatch", "In GSTR-1 only", "In Tally only"}


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def _rs(v):
    """₹1,18,000 - paise only when there are some."""
    if v is None:
        return "an unknown amount"
    v = abs(float(v))
    return inr(v, 0 if round(v, 2) == int(v) else 2)


def _day(v):
    d = to_date(v)
    return f"{d.day} {d:%b %Y}" if d else "an unknown date"


def _gap(a, b):
    da, db = to_date(a), to_date(b)
    return abs((da - db).days) if da and db else None


def _days(n):
    return f"{n} day{'' if n == 1 else 's'}"


def _pct(sim):
    return f"{round(sim * 100)}%"


def _no(ref):
    return f"no. {ref}" if ref else "with no number"


def _paren(v, before=" (", after=")"):
    return f"{before}{v}{after}" if v else ""


def _text(v):
    s = str(v or "").strip()
    return s


def _described(text, blank="no description was written against it"):
    """How an entry's own description reads: quoted, blank, or junk."""
    s = _text(text)
    if not s:
        return blank
    if not re.search(r"[A-Za-z0-9]", s):
        return f'no real description was written against it - only "{s}"'
    return f'it is described as "{s[:120]}"'


def _quote(text):
    """A description to drop into a sentence, junk called out as junk."""
    s = _text(text)
    if not s:
        return "no description"
    if not re.search(r"[A-Za-z0-9]", s):
        return f'only "{s}" as its description'
    return f'"{s[:120]}"'


def _poss(name):
    return f"{name}'" if name.lower().endswith("s") else f"{name}'s"


def _near_enough(diff, amt, share):
    """A nearest candidate is worth naming only if it is actually near."""
    return abs(diff) <= max(abs(amt or 0) * share, 1.0)


def _no_words(text, whose):
    """The check line for an entry whose description gives nothing to match on."""
    s = _text(text)
    if re.search(r"[A-Za-z0-9]", s):
        return None
    shown = f'only "{s}"' if s else "nothing"
    return (f"Description: {whose} {shown} here, so there was no name or reference to "
            f"match on - only the amount and the date could be compared.")


def _name(text, fallback):
    """A party name fit to put in a sentence."""
    s = _text(text)
    return s if re.search(r"[A-Za-z0-9]", s) else fallback


@lru_cache(maxsize=65536)
def _sim(a, b):
    return party_sim(a or "", b or "")


def _dash(s):
    """Proper dashes in our own words; anything quoted from a file stays as it is."""
    if not s:
        return s
    parts = s.split('"')
    return '"'.join(p.replace(" - ", " – ") if i % 2 == 0 else p for i, p in enumerate(parts))


def _why(title, what, checks=(), nearest=None, impact=None, todo=None, notes=()):
    return {"title": _dash(title), "what": _dash(what),
            "checks": [_dash(c) for c in checks if c],
            "nearest": _dash(nearest), "impact": _dash(impact), "todo": _dash(todo),
            "notes": [_dash(n) for n in notes if n]}


def _entry(ref, party, date, amt, rec, **extra):
    return {"ref": _text(ref), "party": _text(party), "date": date,
            "amt": amt or 0.0, "rec": rec, **extra}


# ---------------------------------------------------------------------------
# GST purchases: GSTR-2B against the purchase register
# ---------------------------------------------------------------------------

GST_FLAG_NOTES = {
    "pos_mismatch": "Place of supply is another state but CGST/SGST was charged - "
                    "that tax is not creditable to you.",
    "rcm": "Reverse charge: you pay this GST yourself, then claim it back.",
    "partial_itc": "Only part of the GST on this bill can be claimed.",
    "prior_fy": "The bill is dated before this financial year.",
    "later_fy": "The bill is dated after this financial year.",
    "rate_invalid": "The GST works out to a rate that does not exist - the supplier's "
                    "figures look wrong.",
    "tax_split_error": "The IGST / CGST / SGST split cannot be right for this bill.",
    "value_split_odd": "Bill value is not equal to taxable value plus GST.",
    "import": "This is an import.",
    "isd": "This credit was passed on by an input service distributor.",
    "amended": "This row amends an earlier bill.",
    "non_gst_vch": "It is entered as a plain Purchase voucher with no GST on it, so it "
                   "may not be a GST bill at all (for example, from an unregistered seller).",
    "no_vch_no": "It has no voucher number, so it could only be matched on supplier "
                 "and amount.",
}


def _gst_pools(records):
    books, portal = [], []
    for r in records:
        if r.get("status") == "Superseded":
            continue
        if r.get("parts"):
            for p in r["parts"]:
                books.append(_entry(p.get("vch_no"), p.get("party"), p.get("date"),
                                    p.get("amt"), r))
        elif r.get("amt_tally") is not None:
            books.append(_entry(r.get("vch_no_tally"), r.get("party_tally"),
                                r.get("vch_date_tally"), r.get("amt_tally"), r))
        if r.get("inv_val_2b") is not None:
            portal.append(_entry(r.get("inv_no_2b"), r.get("party_2b"),
                                 r.get("inv_date_2b"), r.get("inv_val_2b"), r,
                                 gstin=r.get("gstin")))
    return books, portal


def _gst_paired(rec):
    return rec.get("inv_val_2b") is not None and rec.get("amt_tally") is not None


def _gst_partner(c, side):
    """What an other-side entry is already paired with, in words."""
    r = c["rec"]
    if side == "books":      # c is in the books; its partner is a portal bill
        return (f"bill {_no(r.get('inv_no_2b'))} from "
                f"{_name(r.get('party_2b'), 'another supplier')} ({_rs(r.get('inv_val_2b'))})")
    return (f"your entry {_no(r.get('vch_no_tally'))} "
            f"({_name(r.get('party_tally'), 'no name')}, {_rs(r.get('amt_tally'))})")


def _gst_search(x, pool, side, tol, thr):
    """The matcher's questions, re-asked for one one-sided entry.

    `side` names where the pool lives: "books" or "portal".
    """
    where = "your books" if side == "books" else "the GST portal"
    on = "in your books" if side == "books" else "on the GST portal"
    ref_hits, amt_hits, party_hits = [], [], []
    x_ref = clean_inv(x["ref"])
    for c in pool:
        if c["rec"] is x["rec"]:
            continue
        sim = _sim(x["party"], c["party"])
        same_gstin = bool(x.get("gstin") and c.get("gstin") and x["gstin"] == c["gstin"])
        if x_ref and clean_inv(c["ref"]) == x_ref:
            ref_hits.append((sim, c))
        if abs((c["amt"] or 0) - (x["amt"] or 0)) <= tol:
            amt_hits.append((sim, c))
        if sim >= thr or same_gstin:
            party_hits.append(c)

    checks, nearest = [], None
    ref_c = max(ref_hits, key=lambda h: h[0]) if ref_hits else None
    if not x_ref:
        checks.append("Number: this entry has no number, so it could only be matched "
                      "on supplier and amount.")
    elif not ref_c:
        checks.append(f"Number: nothing {on} is numbered {x['ref']}.")
    elif _gst_paired(ref_c[1]["rec"]):
        checks.append(f"Number: there is an entry numbered {ref_c[1]['ref']} {on}, but it "
                      f"is already matched to {_gst_partner(ref_c[1], side)}.")
    else:
        c = ref_c[1]
        checks.append(
            f"Number: there is an entry numbered {c['ref']} {on}, but it is from "
            f"{_name(c['party'], 'an unnamed party')} ({_pct(ref_c[0])} name match) for "
            f"{_rs(c['amt'])}, {_rs(abs(c['amt'] - x['amt']))} different - a different bill "
            f"that happens to share the number.")

    amt_c = max(amt_hits, key=lambda h: h[0]) if amt_hits else None
    if not amt_c:
        checks.append(f"Amount: nothing {on} is for {_rs(x['amt'])} "
                      f"(₹{tol:g} either way is allowed for rounding).")
    else:
        sim, c = amt_c
        more = f" ({len(amt_hits)} entries have this amount; this is the closest by name.)" \
            if len(amt_hits) > 1 else ""
        if _gst_paired(c["rec"]):
            checks.append(f"Amount: {_poss(_name(c['party'], 'another party'))} {_no(c['ref'])} is "
                          f"also {_rs(c['amt'])}, but it is already matched to "
                          f"{_gst_partner(c, side)}.{more}")
        elif "ignored" in (c["rec"].get("flags") or []):
            checks.append(f"Amount: bill {_no(c['ref'])} for the same amount was written "
                          f"off by hand, so it was kept out of matching.{more}")
        else:
            checks.append(f"Amount: {_no(c['ref'])} for the same amount is from "
                          f"{_name(c['party'], 'an unnamed party')}, which does not look like "
                          f"the same supplier ({_pct(sim)} name match).{more}")

    if not party_hits:
        checks.append(f"Supplier: there is nothing from "
                      f"{_name(x['party'], 'this supplier')} {on} for this period.")
    else:
        n = len(party_hits)
        checks.append(f"Supplier: {_name(x['party'], 'this supplier')} has {n} "
                      f"entr{'ies' if n != 1 else 'y'} {on}, but none of them is this one.")
        c = min(party_hits, key=lambda e: abs((e["amt"] or 0) - (x["amt"] or 0)))
        d = (c["amt"] or 0) - (x["amt"] or 0)
        g = _gap(c["date"], x["date"])
        taken = (f" It is already matched to {_gst_partner(c, side)}."
                 if _gst_paired(c["rec"]) else "")
        if not _near_enough(d, x["amt"], 0.25):
            return checks, None, ref_c, amt_c, party_hits
        nearest = (f"The closest is {_no(c['ref'])} dated {_day(c['date'])} for "
                   f"{_rs(c['amt'])} - {_rs(abs(d))} {'more' if d > 0 else 'less'}"
                   f"{f', {_days(g)} apart' if g else ''}.{taken}")
        if abs(d) <= tol:
            nearest = (f"The closest is {_no(c['ref'])} dated {_day(c['date'])} for the "
                       f"same {_rs(c['amt'])}.{taken}")
    return checks, nearest, ref_c, amt_c, party_hits


def _explain_gst(payload):
    cfg = payload.get("config") or {}
    tol = float(cfg.get("tolerance", 2.0))
    thr = float(cfg.get("party_threshold", 0.5))
    records = payload.get("all_records") or []
    books, portal = _gst_pools(records)
    for r in records:
        if r.get("status") in GST_OPEN:
            r["why"] = _gst_why(r, books, portal, tol, thr)
    # A saved payload keeps separate copies of the records per status.
    why = {(r["fp"], r["status"]): r["why"] for r in records if r.get("why")}
    for rows in (payload.get("subsets") or {}).values():
        for r in rows:
            if (r.get("fp"), r.get("status")) in why:
                r["why"] = why[(r["fp"], r["status"])]


def _gst_notes(r):
    notes = [GST_FLAG_NOTES[f] for f in r.get("flags") or [] if f in GST_FLAG_NOTES]
    if "pos_mismatch" in (r.get("flags") or []) and r.get("pos"):
        notes[notes.index(GST_FLAG_NOTES["pos_mismatch"])] = (
            f"Place of supply is {r['pos']}, another state, but CGST/SGST was charged - "
            f"that tax is not creditable to you.")
    return notes


def _gst_why(r, books, portal, tol, thr):
    st = r["status"]
    flags = r.get("flags") or []
    p2b = _name(r.get("party_2b"), "the supplier")
    pt = _name(r.get("party_tally"), "the party")
    kind = ("credit note" if "credit_note" in flags else
            "debit note" if "debit_note" in flags else "bill")
    notes = _gst_notes(r)

    if st == "In GSTR-2B only":
        x = _entry(r.get("inv_no_2b"), r.get("party_2b"), r.get("inv_date_2b"),
                   r.get("inv_val_2b"), r, gstin=r.get("gstin"))
        checks, nearest, ref_c, amt_c, same = _gst_search(x, books, "books", tol, thr)
        what = (f"{kind.capitalize()} {_no(x['ref'])} from {p2b}"
                f"{_paren(r.get('gstin'), ' (GSTIN ')}, dated "
                f"{_day(x['date'])}, for {_rs(x['amt'])} including {_rs(r.get('tax_2b'))} "
                f"GST. The supplier has reported it on the GST portal (your GSTR-2B).")
        if "ignored" in flags:
            title = "You wrote this off by hand, so it was not matched."
        elif not same and not ref_c and not amt_c:
            title = (f"Your books have nothing from {p2b} this period - this {kind} "
                     f"does not appear to have been entered.")
        elif ref_c and _gst_paired(ref_c[1]["rec"]):
            title = (f"Your entry numbered {ref_c[1]['ref']} is already matched to another "
                     f"bill, and nothing else in your books fits this one.")
        elif ref_c:
            title = (f"Your books have an entry numbered {ref_c[1]['ref']}, but from "
                     f"{_name(ref_c[1]['party'], 'another party')} for "
                     f"{_rs(ref_c[1]['amt'])} - a different bill.")
        elif same:
            title = (f"{p2b} has {len(same)} entr{'ies' if len(same) != 1 else 'y'} in "
                     f"your books, but none for {_rs(x['amt'])} or numbered "
                     f"{x['ref'] or '(none)'}.")
        else:
            title = (f"No entry in your books is numbered {x['ref'] or '(none)'}, and every "
                     f"{_rs(x['amt'])} entry is from another supplier.")
        tax = r.get("eligible_itc") if r.get("eligible_itc") is not None else r.get("tax_2b")
        if "credit_note" in flags:
            impact = (f"This credit note reduces your GST credit by {_rs(r.get('tax_2b'))}. "
                      f"Until it is in your books you may be claiming that much too much.")
        elif r.get("itc_avail") == "No" or "itc_blocked" in flags:
            impact = ("The portal marks this credit as not available"
                      f"{_paren(r.get('reason'))}, so there "
                      "is no credit to claim - but the purchase still belongs in your books.")
        else:
            impact = (f"{_rs(tax)} of GST credit is waiting on the portal. You can claim it "
                      f"only once this purchase is in your books.")
        left = r.get("days_to_deadline")
        if left is not None and r.get("itc_deadline"):
            if left < 0:
                impact += f" The last date to claim it ({_day(r['itc_deadline'])}) has passed."
            elif left <= 60:
                impact += (f" The last date to claim it is {_day(r['itc_deadline'])}, "
                           f"{_days(left)} away.")
        todo = (f"Find {_poss(p2b)} {kind} {_no(x['ref'])} dated {_day(x['date'])}. If it is "
                f"yours, enter it in your books with the number exactly as "
                f"{x['ref'] or 'printed'}. If you do not recognise it, ask {p2b} - they may "
                f"have put your GSTIN on someone else's bill.")
        return _why(title, what, checks, nearest, impact, todo, notes)

    if st == "In Tally only":
        x = _entry(r.get("vch_no_tally"), r.get("party_tally"), r.get("vch_date_tally"),
                   r.get("amt_tally"), r, gstin=r.get("gstin"))
        checks, nearest, ref_c, amt_c, same = _gst_search(x, portal, "portal", tol, thr)
        what = (f"{r.get('vch_type') or 'Purchase'} entry {_no(x['ref'])} in your books, "
                f"from {pt}, dated {_day(x['date'])}, for {_rs(x['amt'])}; "
                f"{_described(r.get('narration_tally'), 'no description')}.")
        what = what.replace("; no description.", ".")
        if not same and not ref_c and not amt_c:
            title = (f"None of {_poss(pt)} bills are on the GST portal for this period - they may "
                     f"not have filed their return yet.")
        elif ref_c and _gst_paired(ref_c[1]["rec"]):
            title = (f"The portal's bill {ref_c[1]['ref']} is already matched to another "
                     f"entry in your books, and nothing else there fits this one.")
        elif ref_c:
            title = (f"The portal has a bill {ref_c[1]['ref']}, but from "
                     f"{_name(ref_c[1]['party'], 'another supplier')} for "
                     f"{_rs(ref_c[1]['amt'])} - a different bill.")
        elif same:
            title = (f"{pt} has reported {len(same)} bill{'s' if len(same) != 1 else ''}, "
                     f"but none for {_rs(x['amt'])} or numbered {x['ref'] or '(none)'}.")
        else:
            title = (f"The portal has no bill numbered {x['ref'] or '(none)'}, and every "
                     f"{_rs(x['amt'])} bill there is from another supplier.")
        impact = ("You cannot claim GST credit on this purchase until the supplier reports "
                  "it and it shows in your GSTR-2B. If you have claimed it already, that "
                  "claim is at risk.")
        todo = (f"Ask {pt} to report bill {_no(x['ref'])} in their GSTR-1. If this purchase "
                f"carried no GST (an unregistered seller, exempt goods, an expense), mark it "
                f"resolved.")
        return _why(title, what, checks, nearest, impact, todo, notes)

    both = (f"Bill {_no(r.get('inv_no_2b'))} from {p2b} dated {_day(r.get('inv_date_2b'))} "
            f"is on the portal for {_rs(r.get('inv_val_2b'))}; your books have entry "
            f"{_no(r.get('vch_no_tally'))} ({pt}, {_day(r.get('vch_date_tally'))}) for "
            f"{_rs(r.get('amt_tally'))}.")
    diff = r.get("difference") or 0.0
    sim = _sim(r.get("party_2b"), r.get("party_tally"))

    if st == "Value mismatch":
        pct = abs(diff) / abs(r["inv_val_2b"]) * 100 if r.get("inv_val_2b") else 0
        if "rounding_only" in flags:
            title = f"Same bill on both sides, only {_rs(diff)} apart - a rounding difference."
            impact = "No real effect."
            todo = "Mark it resolved, or correct the paise in your entry."
        else:
            title = (f"Same bill number on both sides, but the amounts differ by {_rs(diff)} "
                     f"({pct:.1f}% of the bill).")
            impact = (f"Your GST credit follows the portal figure ({_rs(r.get('tax_2b'))} GST). "
                      f"If your entry is wrong, your purchases and what you owe {p2b} are off "
                      f"by {_rs(diff)}.")
            todo = (f"Check the bill copy. If your entry is wrong, correct "
                    f"{_no(r.get('vch_no_tally'))} to {_rs(r.get('inv_val_2b'))}. If the "
                    f"supplier reported it wrongly, ask them to amend it in their next GSTR-1.")
        checks = [f"Number: {r.get('inv_no_2b')} matches your entry {r.get('vch_no_tally')}.",
                  f"Amount: {_rs(r.get('inv_val_2b'))} on the portal against "
                  f"{_rs(r.get('amt_tally'))} in your books - more than the ₹{tol:g} allowed "
                  f"for rounding."]
        if sim < thr:
            checks.append(f"Name: \"{p2b}\" on the portal, \"{pt}\" in your books "
                          f"({_pct(sim)} alike).")
        return _why(title, both, checks, None, impact, todo, notes)

    if st == "Invoice no. mismatch":
        tail = "tail_match" in flags
        title = (f"Same supplier and amount; the numbers {r.get('vch_no_tally') or '(none)'} "
                 f"and {r.get('inv_no_2b')} end in the same digits - almost certainly the same "
                 f"bill typed differently." if tail else
                 f"Same supplier and amount, but your entry is numbered "
                 f"{r.get('vch_no_tally') or '(nothing)'} while the bill is "
                 f"{r.get('inv_no_2b')}.")
        checks = [f"Supplier: the names match ({_pct(sim)} alike).",
                  f"Amount: agrees within ₹{tol:g}.",
                  f"Number: {r.get('vch_no_tally') or '(none)'} in your books against "
                  f"{r.get('inv_no_2b')} on the portal."]
        impact = ("No effect on your credit today, but a mismatched number makes the two "
                  "harder to tie up and can draw a notice.")
        todo = (f"In your books, change the supplier bill number on entry "
                f"{r.get('vch_no_tally') or '(unnumbered)'} to {r.get('inv_no_2b')}.")
        return _why(title, both, checks, None, impact, todo, notes)

    if st == "Probable match":
        g = r.get("date_gap_days")
        odd = [f"the number differs ({r.get('vch_no_tally') or 'none'} against "
               f"{r.get('inv_no_2b')})"]
        if abs(diff) > 0.005:
            odd.append(f"the amount is {_rs(diff)} off")
        if g:
            odd.append(f"the dates are {_days(g)} apart")
        if sim < 0.97:
            odd.append(f"the names are only {_pct(sim)} alike")
        title = (f"Probably the same bill, but {', '.join(odd[:-1])}"
                 f"{' and ' if len(odd) > 1 else ''}{odd[-1]} - it needs your eye.")
        checks = [f"Number: {r.get('inv_no_2b')} on the portal, "
                  f"{r.get('vch_no_tally') or '(none)'} in your books - they do not match.",
                  f"Supplier: {_pct(sim)} name match.",
                  f"Amount: {_rs(diff)} apart." if abs(diff) > 0.005 else "Amount: agrees.",
                  f"Date: {_days(g)} apart." if g else "Date: same day."]
        impact = "Until you confirm it, both sides count as unmatched."
        todo = ("Open both. If they are the same bill, press Yes, it is the same bill; if not, "
                "leave it - each side then stays unmatched.")
        return _why(title, both, checks, None, impact, todo, notes)

    if st == "Split booking":
        parts = r.get("parts") or []
        listing = ", ".join(f"{_no(p.get('vch_no'))} ({_rs(p.get('amt'))})" for p in parts)
        title = (f"One bill on the portal, entered as {len(parts)} separate entries in your "
                 f"books that add up to the same {_rs(r.get('inv_val_2b'))}.")
        what = (f"Bill {_no(r.get('inv_no_2b'))} from {p2b} dated "
                f"{_day(r.get('inv_date_2b'))} for {_rs(r.get('inv_val_2b'))}; your books "
                f"have {listing}.")
        impact = "No effect on your credit if these are parts of the same bill."
        todo = ("Confirm the entries are the same purchase and mark it resolved. Next time, "
                "enter one bill as one voucher, with its number.")
        return _why(title, what, [], None, impact, todo, notes)

    return None


# ---------------------------------------------------------------------------
# Bank: statement against the bank book
# ---------------------------------------------------------------------------

BANK_TITLES = {
    "Bank charges not in books":
        "The bank deducted {amt} as charges on {day}; your books do not have it yet.",
    "GST on bank charges not in books":
        "The bank charged {amt} GST on its fees on {day}; your books do not have it yet.",
    "Interest credited, not in books":
        "The bank paid you {amt} interest on {day}; your books do not have it yet.",
    "Interest debited, not in books":
        "The bank charged you {amt} interest on {day}; your books do not have it yet.",
    "Loan or EMI debit not in books":
        "A loan instalment (EMI) of {amt} went out on {day}; your books do not have it.",
    "Standing instruction not in books":
        "An automatic debit (a standing mandate) of {amt} went out on {day}; your books "
        "do not have it.",
    "ATM withdrawal not in books":
        "{amt} was withdrawn in cash at an ATM on {day}; your books do not have it.",
    "Reversal or return not in books":
        "{amt} on {day} is a returned or reversed payment; your books do not show the "
        "reversal.",
    "Credit in bank, not in books":
        "{amt} came into the bank on {day}{who}, but your books have no matching receipt.",
    "Debit in bank, not in books":
        "{amt} went out of the bank on {day}{who}, but your books have no matching payment.",
}

# Entries the bank makes by itself. Nobody writes these into the books until the
# statement arrives, so they are expected here - and a reference number or a
# name on them means nothing to the books.
BANK_MADE = {
    "Bank charges not in books": "bank charges",
    "GST on bank charges not in books": "GST on bank charges",
    "Interest credited, not in books": "interest paid by the bank",
    "Interest debited, not in books": "interest charged by the bank",
    "Loan or EMI debit not in books": "a loan instalment",
    "Standing instruction not in books": "an automatic debit set up with the bank",
    "ATM withdrawal not in books": "a cash withdrawal",
}

BANK_TODO = {
    "Bank charges not in books": "Enter it in your books as bank charges dated {day}.",
    "GST on bank charges not in books":
        "Enter it with the bank charges; the GST can be claimed as credit.",
    "Interest credited, not in books": "Enter it in your books as interest received.",
    "Interest debited, not in books": "Enter it in your books as interest paid.",
    "Loan or EMI debit not in books":
        "Enter it against the loan account - split principal and interest if you have "
        "the repayment schedule.",
    "Standing instruction not in books":
        "Find what the mandate pays for (insurance, SIP, loan, subscription) and enter it.",
    "ATM withdrawal not in books": "Enter it as a cash withdrawal (bank to cash).",
    "Reversal or return not in books":
        "Find the entry that bounced or was reversed and reverse it in your books too.",
    "Credit in bank, not in books":
        "Find out who paid it and why, then enter it as a receipt from that party.",
    "Debit in bank, not in books":
        "Find out what it paid for, then enter it as a payment.",
}


def _bank_pools(records):
    bank, book = [], []
    for r in records:
        if r.get("bank_fp"):
            bank.append(_entry(r.get("bank_ref"), r.get("counterparty") or r.get("narration"),
                               r.get("date_bank"), r.get("bank_amount"), r,
                               net=r.get("bank_net") or 0.0, refs=[r.get("bank_ref")]))
        if not r.get("book_fp"):
            continue
        if r.get("parts"):
            sign = 1 if (r.get("bank_net") or 0) >= 0 else -1
            for p in r["parts"]:
                book.append(_entry(p.get("vch_no"), p.get("party"), p.get("date"),
                                   p.get("amount"), r, net=sign * (p.get("amount") or 0),
                                   refs=[p.get("vch_no")]))
        else:
            book.append(_entry(r.get("book_vch_no") or r.get("book_ref"), r.get("book_party"),
                               r.get("date_book"), r.get("book_amount"), r,
                               net=r.get("book_net") or 0.0,
                               refs=[r.get("book_vch_no"), r.get("book_ref")]))
    return bank, book


def _refs_meet(a_refs, b_refs):
    a = {clean_inv(x) for x in a_refs if x and len(clean_inv(x)) >= 4}
    b = {clean_inv(x) for x in b_refs if x and len(clean_inv(x)) >= 4}
    if a & b:
        return True
    return any(len(x) >= 5 and len(y) >= 5 and (x.endswith(y) or y.endswith(x))
               for x in a for y in b)


def _bank_paired(rec):
    return bool(rec.get("bank_fp") and rec.get("book_fp"))


def _bank_partner(c, side):
    r = c["rec"]
    if side == "book":
        return f"the bank's {_rs(r.get('bank_amount'))} on {_day(r.get('date_bank'))}"
    return (f"your book's {_rs(r.get('book_amount'))} on {_day(r.get('date_book'))}"
            f" ({_name(r.get('book_party'), 'no name')})")


def _bank_search(x, pool, side, cfg, bank_made=False):
    """Re-ask the bank matcher's questions for one entry.

    `side` names where the pool lives: "book" or "bank".
    """
    where = "your bank book" if side == "book" else "the bank statement"
    tol = float(cfg.get("amount_tolerance", 1.0))
    win = int(cfg.get("date_window_days", 7))
    wide = int(cfg.get("wide_date_window_days", 45))
    name_ok = int(cfg.get("name_threshold", 62))
    way = "receipt" if x["net"] > 0 else "payment"
    other_way = "payment" if way == "receipt" else "receipt"

    ref_hit, same, flipped, near, pool_win = None, [], [], [], []
    for c in pool:
        if c["rec"] is x["rec"]:
            continue
        g = _gap(c["date"], x["date"])
        g = 10 ** 6 if g is None else g
        same_amt = abs(abs(c["amt"] or 0) - abs(x["amt"] or 0)) <= tol
        if c["net"] * x["net"] < 0:
            if same_amt:
                flipped.append((g, c))
            continue
        if ref_hit is None and _refs_meet(x["refs"], c["refs"]):
            ref_hit = c
        if same_amt:
            same.append((g, c))
        elif g <= wide:
            near.append((abs(abs(c["amt"] or 0) - abs(x["amt"] or 0)), g, c))
        if g <= win and not _bank_paired(c["rec"]) and abs(c["amt"] or 0) < abs(x["amt"] or 0):
            pool_win.append(c)

    checks, nearest = [], None
    refs = [r for r in x["refs"] if r and len(clean_inv(r)) >= 4]
    if bank_made:
        pass
    elif refs:
        if not ref_hit:
            checks.append(f"Reference: {refs[0]} does not appear anywhere in {where}.")
        elif _bank_paired(ref_hit["rec"]):
            checks.append(f"Reference: {refs[0]} appears in {where}, but that entry is "
                          f"already matched to {_bank_partner(ref_hit, side)}.")
        else:
            checks.append(f"Reference: {refs[0]} appears in {where} on "
                          f"{_day(ref_hit['date'])} for {_rs(ref_hit['amt'])} - a "
                          f"different amount.")
    else:
        checks.append("Reference: there is no cheque or transfer number to match on.")

    if not same:
        checks.append(f"Same amount: {where} has no {way} of {_rs(x['amt'])} "
                      f"(₹{tol:g} either way is allowed).")
    else:
        g, c = min(same, key=lambda h: h[0])
        if _bank_paired(c["rec"]):
            checks.append(f"Same amount: {where} has {_rs(c['amt'])} on {_day(c['date'])}, "
                          f"but it is already matched to {_bank_partner(c, side)}.")
        elif g > wide:
            checks.append(f"Same amount: the nearest {_rs(c['amt'])} {way} in {where} is on "
                          f"{_day(c['date'])}, {_days(g)} away - further apart than the "
                          f"{wide} days we allow.")
        else:
            checks.append(f"Same amount: {where} has {_rs(c['amt'])} on {_day(c['date'])} "
                          f"({_days(g)} away), but the names do not look alike "
                          f"(\"{_name(c['party'], 'blank')}\"), so it was not assumed to be "
                          f"the same.")
    if flipped:
        g, c = min(flipped, key=lambda h: h[0])
        checks.append(f"Wrong side? {where.capitalize()} has {_rs(c['amt'])} on "
                      f"{_day(c['date'])}, but as a {other_way}, while this is a {way}. It "
                      f"may have been entered on the wrong side.")

    if x["party"] and near and not bank_made:
        close = [n for n in near if n[1] <= win
                 and n[0] <= max(500.0, abs(x["amt"] or 0) * 0.05)
                 and _name_score(x["party"], n[2]["party"]) >= name_ok]
        if not close:
            checks.append(f"Close amount: nothing from a similar name within {win} days is "
                          f"within 5% of it.")
    if len(pool_win) >= 2:
        checks.append(f"Adding up: no two to four {way}s within {win} days add up to "
                      f"{_rs(x['amt'])}.")

    # A nearest amount is only worth naming when it could be the same party.
    related = [n for n in near if not bank_made and (
        not x["party"] or _name_score(x["party"], n[2]["party"]) >= 45)]
    d, g, c = min(related, key=lambda n: (n[0], n[1])) if related else (None, None, None)
    if c and _near_enough(d, x["amt"], 0.10):
        taken = (f" It is already matched to {_bank_partner(c, side)}."
                 if _bank_paired(c["rec"]) else "")
        nearest = (f"The closest {way} within {wide} days is {_rs(c['amt'])} on "
                   f"{_day(c['date'])} ({_name(c['party'], 'no description')}) - "
                   f"{_rs(d)} different.{taken}")
    return checks, nearest, same, flipped


def _explain_bank(payload):
    cfg = payload.get("config") or {}
    records = payload.get("all_records") or []
    bank, book = _bank_pools(records)
    last = (payload.get("bank_meta") or {}).get("last_date")
    for r in records:
        if r.get("status") not in BANK_SETTLED:
            r["why"] = _bank_why(r, bank, book, cfg, last)


def _bank_why(r, bank, book, cfg, last):
    st = r["status"]
    notes = []
    wide = int(cfg.get("wide_date_window_days", 45))
    win = int(cfg.get("date_window_days", 7))

    if r.get("bank_fp") and not r.get("book_fp"):
        x = _entry(r.get("bank_ref"), r.get("counterparty"), r.get("date_bank"),
                   r.get("bank_amount"), r, net=r.get("bank_net") or 0.0,
                   refs=[r.get("bank_ref")])
        money = "in" if x["net"] > 0 else "out"
        what = (f"On {_day(x['date'])} the bank statement shows {_rs(x['amt'])} going "
                f"{money}; {_described(r.get('narration'))}.")
        made = BANK_MADE.get(r.get("category"))
        checks, nearest, same, flipped = _bank_search(x, book, "book", cfg, bool(made))
        blank = _no_words(r.get("narration"), "the bank printed")
        if blank:
            checks.insert(0, blank)
        if made:
            checks.insert(0, f"Kind: the bank's description marks it as {made}. The bank makes "
                             f"entries like this itself, so they reach your books only when "
                             f"someone enters them from the statement.")
        who = _name(r.get("counterparty"), "")
        title = BANK_TITLES.get(r.get("category"), BANK_TITLES["Debit in bank, not in books"]
                                if x["net"] < 0 else BANK_TITLES["Credit in bank, not in books"])
        title = title.format(amt=_rs(x["amt"]), day=_day(x["date"]),
                             who=f" ({'from' if x['net'] > 0 else 'to'} {who})" if who else "")
        if flipped:
            title += " A matching amount is in your books on the other side - see below."
        impact = (f"Until it is entered, your books show {_rs(x['amt'])} "
                  f"{'less' if x['net'] > 0 else 'more'} in the bank than the bank does.")
        todo = BANK_TODO.get(r.get("category"), "Find out what it was and enter it.")
        return _why(title, what, checks, nearest, impact,
                    todo.format(day=_day(x["date"])), notes)

    if r.get("book_fp") and not r.get("bank_fp"):
        x = _entry(r.get("book_vch_no"), r.get("book_party"), r.get("date_book"),
                   r.get("book_amount"), r, net=r.get("book_net") or 0.0,
                   refs=[r.get("book_vch_no"), r.get("book_ref")])
        pay = x["net"] < 0
        party = _name(r.get("book_party"), "")
        what = (f"Your bank book has a {'payment' if pay else 'receipt'} of {_rs(x['amt'])} "
                f"on {_day(x['date'])} (entry {_no(r.get('book_vch_no'))}"
                f"{_paren(r.get('book_ref'), ', ref ', '')}); "
                f"{_described(r.get('book_party'))}.")
        checks, nearest, same, flipped = _bank_search(x, bank, "bank", cfg)
        blank = _no_words(r.get("book_party"), "your bank book has")
        if blank:
            checks.insert(0, blank)
        after = to_date(x["date"]) and to_date(last) and to_date(x["date"]) > to_date(last)
        age = r.get("age_days")
        if after:
            title = (f"It is dated {_day(x['date'])}, after the bank statement ends "
                     f"({_day(last)}), so the bank could not show it yet.")
        elif pay:
            title = (f"You paid {_rs(x['amt'])}{f' to {party}' if party else ''} on "
                     f"{_day(x['date'])}, but it has not gone through the bank"
                     f"{f' by {_day(last)}' if last else ''}.")
        else:
            title = (f"You recorded {_rs(x['amt'])} received{f' from {party}' if party else ''} "
                     f"on {_day(x['date'])}, but the bank has not credited it"
                     f"{f' by {_day(last)}' if last else ''}.")
        if "stale_cheque" in (r.get("flags") or []):
            notes.insert(0, f"It is {_days(age)} old. A cheque is valid for three months, so "
                            f"this one can no longer be cashed.")
        impact = (f"Your books show {_rs(x['amt'])} {'less' if pay else 'more'} in the bank "
                  f"than the bank does, until it clears.")
        if pay:
            todo = ("Usually nothing to do - it clears when the cheque is presented. If it "
                    "still has not appeared next month, check with the payee."
                    if "stale_cheque" not in (r.get("flags") or []) else
                    "Reverse this entry in your books, and issue a fresh cheque if the "
                    "amount is still owed.")
        else:
            todo = ("If the cheque was deposited, ask the bank about it; if it bounced, "
                    "reverse the entry; if it was never deposited, deposit it.")
        return _why(title, what, checks, nearest, impact, todo, notes)

    what = (f"The bank shows {_rs(r.get('bank_amount'))} on {_day(r.get('date_bank'))} "
            f"({_quote(r.get('narration'))}); your bank book shows "
            f"{_rs(r.get('book_amount'))} on {_day(r.get('date_book'))} "
            f"({_quote(r.get('book_party'))}).")
    if st == "Timing difference":
        g = r.get("date_gap_days")
        title = (f"The same {_rs(r.get('bank_amount'))} is on both sides, but {_days(g)} apart.")
        checks = [f"Amount: agrees.",
                  f"Date: {_days(g)} apart - automatic pairing only covers dates within "
                  f"{win} days; up to {wide} days is listed for you to confirm."]
        impact = "No effect on the balance once both sides have it."
        todo = ("If it is the same transaction, mark it resolved. If the date in your book "
                "is wrong, correct it.")
        return _why(title, what, checks, None, impact, todo, notes)

    if st == "Amount differs":
        d = abs(r.get("difference") or 0)
        title = (f"Same party and date, but the bank shows {_rs(r.get('bank_amount'))} and "
                 f"your book {_rs(r.get('book_amount'))} - {_rs(d)} apart.")
        checks = ["Name or reference: agrees.",
                  f"Date: {_days(r['date_gap_days'])} apart." if r.get("date_gap_days")
                  else "Date: same day.",
                  f"Amount: {_rs(d)} apart."]
        impact = f"Your book balance is off by {_rs(d)} because of this entry."
        todo = (f"Check the bank advice. The {_rs(d)} may be bank charges or a short "
                f"payment - enter the difference, or correct the entry.")
        return _why(title, what, checks, None, impact, todo, notes)
    return None


# ---------------------------------------------------------------------------
# Party statements: their statement against our ledger
# ---------------------------------------------------------------------------

KIND_WORDS = {"invoice": "a bill", "payment": "a payment", "credit_note": "a credit note",
              "debit_note": "a debit note", "interest": "an interest charge",
              "tds": "a TDS entry", "opening": "an opening balance"}

STATEMENT_TITLES = {
    "Billed by them, not in our books":
        "{p} billed {amt} on {day}{ref}, but your ledger has no such bill.",
    "Charged to us, not in our books":
        "{p} charged {amt} on {day}{ref}, but your ledger does not have it.",
    "Credit note issued by them, no copy with us":
        "{p} gave you a credit note of {amt} on {day}{ref}, but it is not in your ledger.",
    "Credited by them, not in our books":
        "{p} shows {amt} credited on {day}{ref}, but your ledger does not have it.",
    "Booked by us, not in their statement":
        "Your ledger has a bill of {amt} on {day}{ref} that {ps} statement does not show.",
    "Billed by us, not in their statement":
        "You billed {amt} on {day}{ref}, but {ps} statement does not show it.",
    "Paid by us, not yet credited by them":
        "You paid {amt} on {day}{ref}, but {ps} statement does not show it received.",
    "Debit note raised by us, not accepted by them":
        "Your debit note of {amt} on {day}{ref} is not in {ps} statement - they may not "
        "have accepted it.",
    "Debited by us, not in their statement":
        "Your ledger has {amt} on {day}{ref} that {ps} statement does not show.",
}

STATEMENT_TODO = {
    "Billed by them, not in our books":
        "Ask {p} for a copy of bill {ref}. If it is genuine, enter it; if it is not yours, "
        "ask them to reverse it.",
    "Charged to us, not in our books":
        "Ask {p} what the charge is for. Enter it if you accept it; dispute it if not.",
    "Credit note issued by them, no copy with us":
        "Ask {p} for the credit note and enter it - it reduces what you owe.",
    "Credited by them, not in our books":
        "Check whether this payment was made; if it was, enter it in your ledger.",
    "Booked by us, not in their statement":
        "Check the bill in your file. If it is genuine, ask {p} why it is missing from "
        "their statement.",
    "Billed by us, not in their statement":
        "Send {p} a copy of the bill and ask them to enter it.",
    "Paid by us, not yet credited by them":
        "Send {p} the payment details (date, amount, UTR or cheque number) and ask them "
        "to credit it.",
    "Debit note raised by us, not accepted by them":
        "Send {p} the debit note and ask them to accept it, or agree the amount.",
    "Debited by us, not in their statement":
        "Check what the entry is and send {p} the details.",
}


def _stmt_pools(records):
    theirs, ours = [], []
    for r in records:
        if "period_total" in (r.get("flags") or []):
            continue
        if r.get("their_fp"):
            theirs.append(_entry(r.get("ref_theirs"), r.get("particulars_theirs"),
                                 r.get("date_theirs"), r.get("owed_theirs"), r,
                                 kind=r.get("kind")))
        if r.get("our_fp"):
            ours.append(_entry(r.get("ref_ours"), r.get("particulars_ours"),
                               r.get("date_ours"), r.get("owed_ours"), r, kind=r.get("kind")))
    return theirs, ours


def _stmt_paired(rec):
    return bool(rec.get("their_fp") and rec.get("our_fp"))


def _stmt_partner(c, side):
    r = c["rec"]
    if side == "ours":
        return (f"their {_rs(r.get('owed_theirs'))} on {_day(r.get('date_theirs'))}"
                f"{_paren(r.get('ref_theirs'))}")
    return (f"your {_rs(r.get('owed_ours'))} on {_day(r.get('date_ours'))}"
            f"{_paren(r.get('ref_ours'))}")


def _stmt_search(x, pool, side, cfg, party):
    where = "your ledger" if side == "ours" else f"{_poss(party)} statement"
    tol = float(cfg.get("amount_tolerance", 1.0))
    win = int(cfg.get("date_window_days", 10))
    wide = int(cfg.get("wide_date_window_days", 60))
    min_ref = int(cfg.get("ref_min_len", 3))
    pct = float(cfg.get("dispute_max_pct", 12.0))

    ref_hit, same, flipped, near = None, [], [], []
    x_ref = clean_inv(x["ref"])
    for c in pool:
        if c["rec"] is x["rec"]:
            continue
        g = _gap(c["date"], x["date"])
        g = 10 ** 6 if g is None else g
        same_amt = abs(abs(c["amt"] or 0) - abs(x["amt"] or 0)) <= tol
        ref_ok = len(x_ref) >= min_ref and (clean_inv(c["ref"]) == x_ref
                                            or tails_match(x["ref"], c["ref"]))
        if ref_ok and (ref_hit is None or _stmt_paired(ref_hit["rec"])):
            ref_hit = c
        if (c["amt"] or 0) * (x["amt"] or 0) < 0:
            if same_amt:
                flipped.append((g, c))
            continue
        if same_amt:
            same.append((g, c))
        elif g <= wide and c.get("kind") == x.get("kind"):
            near.append((abs(abs(c["amt"] or 0) - abs(x["amt"] or 0)), g, c))

    checks, nearest = [], None
    if len(x_ref) < min_ref:
        checks.append("Document number: none given, so it could only be matched on amount "
                      "and date.")
    elif not ref_hit:
        checks.append(f"Document number: nothing in {where} carries {x['ref']}.")
    elif _stmt_paired(ref_hit["rec"]):
        checks.append(f"Document number: {where} has {ref_hit['ref']}, but it is already "
                      f"matched to {_stmt_partner(ref_hit, side)}.")
    elif (ref_hit["amt"] or 0) * (x["amt"] or 0) < 0:
        checks.append(f"Document number: {where} has {ref_hit['ref']} for "
                      f"{_rs(ref_hit['amt'])}, but on the opposite side of the account, so "
                      f"it cannot be the same entry.")
    else:
        checks.append(f"Document number: {where} has {ref_hit['ref']} for "
                      f"{_rs(ref_hit['amt'])}, but it is {KIND_WORDS.get(ref_hit['kind'], 'an entry')} "
                      f"and this is {KIND_WORDS.get(x['kind'], 'an entry')}, so they were "
                      f"kept apart.")

    if not same:
        checks.append(f"Same amount: nothing in {where} is {_rs(x['amt'])} on the same side "
                      f"(₹{tol:g} either way is allowed).")
    else:
        g, c = min(same, key=lambda h: h[0])
        if _stmt_paired(c["rec"]):
            checks.append(f"Same amount: {where} has {_rs(c['amt'])} on {_day(c['date'])}, "
                          f"but it is already matched to {_stmt_partner(c, side)}.")
        else:
            checks.append(f"Same amount: the nearest {_rs(c['amt'])} in {where} is on "
                          f"{_day(c['date'])}, {_days(g)} away - further apart than the "
                          f"{wide} days we allow.")
    if flipped:
        g, c = min(flipped, key=lambda h: h[0])
        checks.append(f"Wrong side? {where[0].upper() + where[1:]} has {_rs(c['amt'])} on "
                      f"{_day(c['date'])}, but on the opposite side of the account. One of "
                      f"the two may have been entered the wrong way round.")
    close = [n for n in near if n[1] <= win and n[0] / max(abs(x["amt"] or 0), 1) * 100 <= pct]
    if not close:
        checks.append(f"Near amount: nothing in {where} within {win} days of it is within "
                      f"{pct:g}% of the amount.")
    d, g, c = min(near, key=lambda n: (n[0], n[1])) if near else (None, None, None)
    if c and _near_enough(d, x["amt"], 0.10):
        taken = (f" It is already matched to {_stmt_partner(c, side)}."
                 if _stmt_paired(c["rec"]) else "")
        nearest = (f"The closest within {wide} days is {_rs(c['amt'])} on {_day(c['date'])}"
                   f"{_paren(c.get('ref'))} - "
                   f"{_rs(d)} different.{taken}")
    return checks, nearest


def _explain_statement(payload):
    cfg = payload.get("config") or {}
    records = payload.get("all_records") or []
    theirs, ours = _stmt_pools(records)
    tm, om = payload.get("their_meta") or {}, payload.get("our_meta") or {}
    party = _name(tm.get("party_name"), "The party")
    ends = {"theirs": tm.get("period_to") or tm.get("last_date"),
            "ours": om.get("period_to") or om.get("last_date")}
    for r in records:
        if r.get("status") in STATEMENT_OPEN:
            r["why"] = _stmt_why(r, theirs, ours, cfg, party, ends)


def _stmt_why(r, theirs, ours, cfg, party, ends):
    st = r["status"]
    notes = []
    kind = r.get("kind")

    if "period_total" in (r.get("flags") or []):
        label = "interest" if kind == "interest" else "TDS"
        t, o, d = r.get("owed_theirs") or 0, r.get("owed_ours") or 0, r.get("difference") or 0
        what = (f"{label.capitalize()} for the period: {party} shows {_rs(t)} "
                f"({r.get('particulars_theirs') or 'one entry'}); your ledger shows {_rs(o)} "
                f"({r.get('particulars_ours') or 'one entry'}).")
        story = r.get("explain") or {}
        checks = []
        for i in story.get("items") or []:
            if i["side"] == "ours":
                checks.append(
                    f"Your {_rs(i['amount'])} on {_day(i['date'])}"
                    f"{_paren(i.get('ref'))} is not in "
                    f"their statement, so they charged interest on {_rs(i['principal'])} for "
                    f"{_days(i['days'])} more: about {_rs(i['effect'])}.")
            else:
                checks.append(
                    f"Their {_rs(i['amount'])} on {_day(i['date'])} is not in your ledger: "
                    f"about {_rs(i['effect'])} of interest over {_days(i['days'])}.")
        if kind == "interest":
            title = (f"{party} worked out {_rs(d)} {'more' if d > 0 else 'less'} interest "
                     f"than your ledger shows.")
            if story.get("whole"):
                nearest = "Together, these explain the whole difference."
            elif story.get("items"):
                nearest = (f"These explain about {_rs(story.get('explained'))}; the other "
                           f"{_rs((story.get('gap') or 0) - (story.get('explained') or 0))} "
                           f"needs their interest working.")
            else:
                nearest = ("No missing entry explains it - the rate, the days counted or the "
                           "balance they charged on must differ.")
            todo = ("Get the missing entries recorded on both sides, then ask for a revised "
                    "interest statement and their working (rate, days, balance).")
        else:
            title = f"TDS for the period: {party} shows {_rs(t)}, your ledger {_rs(o)}."
            nearest = None
            todo = ("Check Form 26AS and your TDS return - one side has booked TDS the other "
                    "has not, or at a different rate.")
        impact = f"This accounts for {_rs(d)} of the difference between the two balances."
        return _why(title, what, checks, nearest, impact, todo, notes)

    one_sided = bool(r.get("their_fp")) != bool(r.get("our_fp"))
    if one_sided:
        mine = bool(r.get("our_fp"))
        x = (_entry(r.get("ref_ours"), r.get("particulars_ours"), r.get("date_ours"),
                    r.get("owed_ours"), r, kind=kind) if mine else
             _entry(r.get("ref_theirs"), r.get("particulars_theirs"), r.get("date_theirs"),
                    r.get("owed_theirs"), r, kind=kind))
        whose = "Your ledger" if mine else f"{_poss(party)} statement"
        what = (f"{whose} has {KIND_WORDS.get(kind, 'an entry')} of {_rs(x['amt'])} on "
                f"{_day(x['date'])}{_paren(x.get('ref'))}; "
                f"{_described(x['party'])}.")
        checks, nearest = _stmt_search(x, theirs if mine else ours,
                                       "theirs" if mine else "ours", cfg, party)
        blank = _no_words(x["party"], "your ledger has" if mine else "their statement has")
        if blank:
            checks.insert(0, blank)
        cat = r.get("category")
        ref = f" ({x['ref']})" if x["ref"] else ""
        if kind in ("interest", "tds") and not mine:
            title = (f"{party} charged {_rs(x['amt'])} {'interest' if kind == 'interest' else 'TDS'} "
                     f"on {_day(x['date'])}; your ledger has none for them in this period.")
            todo = f"If you accept it, enter it; if not, dispute it with {party}."
        else:
            title = STATEMENT_TITLES.get(cat, "{p}: {amt} on {day}{ref} has no match.").format(
                p=party, ps=_poss(party), amt=_rs(x["amt"]), day=_day(x["date"]), ref=ref)
            todo = STATEMENT_TODO.get(cat, "Check the entry with {p}.").format(
                p=party, ref=x["ref"] or "(no number)")
        # Their statement (or our ledger) may simply stop before this entry.
        end = ends["theirs" if mine else "ours"]
        if to_date(end) and to_date(x["date"]) and to_date(x["date"]) > to_date(end):
            title = (f"It is dated {_day(x['date'])}, after "
                     f"{_poss(party) + ' statement' if mine else 'your ledger'} ends "
                     f"({_day(end)}), so it could not appear there yet.")
            todo = "Nothing to fix now - check that it appears in the next statement."
        # The same document entered twice on one side.
        x_ref = clean_inv(x["ref"])
        twice = [c for c in (ours if mine else theirs) if c["rec"] is not r and x_ref
                 and len(x_ref) >= int(cfg.get("ref_min_len", 3))
                 and clean_inv(c["ref"]) == x_ref]
        if twice:
            c = twice[0]
            if _stmt_paired(c["rec"]):
                title = (f"{whose} shows {x['ref']} twice - {_rs(c['amt'])} and "
                         f"{_rs(x['amt'])}. The first is matched; this second one has "
                         f"nothing to match on the other side.")
            checks.insert(0, f"Twice: {whose} carries {x['ref']} more than once - also "
                             f"{_rs(c['amt'])} on {_day(c['date'])}. The same document may "
                             f"have been entered twice.")
        impact = f"This accounts for {_rs(x['amt'])} of the difference between the two balances."
        if r.get("age_days") and r["age_days"] > 90:
            notes.insert(0, f"It has been open for {_days(r['age_days'])} as at the "
                            f"statement date.")
        return _why(title, what, checks, nearest, impact, todo, notes)

    what = (f"{party} shows {_rs(r.get('owed_theirs'))} on {_day(r.get('date_theirs'))}"
            f"{_paren(r.get('ref_theirs'))}; "
            f"your ledger shows {_rs(r.get('owed_ours'))} on {_day(r.get('date_ours'))}"
            f"{_paren(r.get('ref_ours'))}.")
    if st == "Timing difference":
        g = r.get("date_gap_days")
        title = (f"The same {_rs(r.get('owed_theirs'))} is on both sides, but {_days(g)} "
                 f"apart - probably recorded in different periods.")
        checks = ["Amount: agrees.",
                  f"Date: {_days(g)} apart; automatic pairing only covers "
                  f"{int(cfg.get('date_window_days', 10))} days."]
        impact = "No difference in the balance once both are in the same period."
        todo = "If it is the same entry, mark it resolved."
        return _why(title, what, checks, None, impact, todo, notes)

    d = abs(r.get("difference") or 0)
    ref = r.get("ref_theirs") or r.get("ref_ours")
    title = (f"Same document{f' {ref}' if ref else ''}: {party} shows "
             f"{_rs(r.get('owed_theirs'))}, your ledger {_rs(r.get('owed_ours'))} - "
             f"{_rs(d)} apart.")
    checks = ["Document number or date: agrees.", f"Amount: {_rs(d)} apart."]
    impact = f"This accounts for {_rs(d)} of the difference between the two balances."
    todo = (f"Compare both copies of {ref or 'the document'} line by line - rate, quantity, "
            f"discount, GST.")
    return _why(title, what, checks, None, impact, todo, notes)


# ---------------------------------------------------------------------------
# Sales: GSTR-1 against the sales register
# ---------------------------------------------------------------------------

def _explain_sales(sales):
    records = sales.get("all_records") or []
    tol = float((sales.get("config") or {}).get("tolerance", 2.0))
    filed = [r for r in records if r.get("val_gstr1") is not None]
    booked = [r for r in records if r.get("amt_tally") is not None]
    for r in records:
        st = r.get("status")
        if st not in SALES_OPEN:
            continue
        if st == "Value mismatch":
            d = abs(r.get("difference") or 0)
            r["why"] = _why(
                f"Sale {r.get('inv_no_gstr1')}: your GSTR-1 shows {_rs(r.get('val_gstr1'))}, "
                f"your books {_rs(r.get('amt_tally'))} - {_rs(d)} apart.",
                f"Invoice {_no(r.get('inv_no_gstr1'))} to "
                f"{_name(r.get('party_gstr1'), 'a customer')} dated "
                f"{_day(r.get('inv_date_gstr1'))}.",
                [f"Number: agrees.", f"Amount: {_rs(d)} apart - more than ₹{tol:g}."],
                None, f"GST on {_rs(d)} of sales is reported differently from your books.",
                "Correct whichever side is wrong; amend GSTR-1 if the return is wrong.")
            continue
        filed_side = st == "In GSTR-1 only"
        ref = r.get("inv_no_gstr1") if filed_side else r.get("vch_no_tally")
        amt = r.get("val_gstr1") if filed_side else r.get("amt_tally")
        pool = booked if filed_side else filed
        key = "vch_no_tally" if filed_side else "inv_no_gstr1"
        val = "amt_tally" if filed_side else "val_gstr1"
        same_no = [c for c in pool if ref and clean_inv(c.get(key)) == clean_inv(ref)]
        same_amt = [c for c in pool if c is not r and abs((c.get(val) or 0) - (amt or 0)) <= tol]
        where = "your books" if filed_side else "your GSTR-1"
        checks = [
            f"Number: nothing in {where} is numbered {ref}." if not same_no else
            f"Number: {where} has {ref}, but it is already matched to another invoice.",
            f"Amount: nothing in {where} is for {_rs(amt)}." if not same_amt else
            f"Amount: {len(same_amt)} entr{'y' if len(same_amt) == 1 else 'ies'} in {where} "
            f"are {_rs(amt)}, but under other numbers.",
        ]
        party = _name(r.get("party_gstr1") or r.get("party_tally"), "a customer")
        if filed_side:
            r["why"] = _why(
                f"You reported sale {ref} ({_rs(amt)}) in GSTR-1, but your books have no sale "
                f"numbered {ref}.",
                f"Invoice {_no(ref)} to {party} dated {_day(r.get('inv_date_gstr1'))} for "
                f"{_rs(amt)}, reported in your GSTR-1.",
                checks, None,
                f"If the sale is real, your books are short by {_rs(amt)} of sales.",
                "Enter the sale in your books with the same number, or amend GSTR-1 if it "
                "was reported by mistake.")
        else:
            r["why"] = _why(
                f"Sale {ref} to {party} ({_rs(amt)}) is in your books but not in your GSTR-1.",
                f"Sales entry {_no(ref)} to {party} dated {_day(r.get('vch_date_tally'))} for "
                f"{_rs(amt)}.",
                checks, None,
                "GST on this sale may not have been reported or paid - that can draw a notice.",
                "Report it in your next GSTR-1, or check whether it went in under another "
                "number.")


# ---------------------------------------------------------------------------

def why_line(rec):
    """The reason and the next step in one cell, for exports."""
    w = rec.get("why") or {}
    return f"{w['title']} What to do: {w['todo']}" if w.get("title") else ""


def explain_run(payload):
    """Attach `why` to every open record of a run payload, in place."""
    if not payload:
        return payload
    records = payload.get("all_records") or []
    kind = payload.get("kind") or "purchase"
    if records and all("why" in r for r in records
                       if r.get("status") not in ("Matched", "Agreed", "Batched", "Superseded")):
        return payload
    if kind == "bank":
        _explain_bank(payload)
    elif kind == "statement":
        _explain_statement(payload)
    else:
        _explain_gst(payload)
        if payload.get("sales"):
            _explain_sales(payload["sales"])
    return payload
