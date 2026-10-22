"""Checks that are arithmetic or law, never opinion.

Each returns a dict the UI renders as a row: what was expected, what was found,
and how much it matters. 'error' blocks one-click approval; 'warn' does not.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from . import gst
from .parse import ParsedInvoice

OK, WARN, ERROR = "ok", "warn", "error"


def _v(code: str, severity: str, message: str, expected=None, actual=None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "expected": None if expected is None else str(expected),
        "actual": None if actual is None else str(actual),
    }


LABELS = {"invoice_no": "Bill number", "invoice_date": "Bill date", "party_name": "Party",
          "party_gstin": "Party GSTIN", "taxable_total": "Taxable value", "cgst": "CGST",
          "sgst": "SGST", "igst": "IGST", "grand_total": "Total", "lines": "Line items"}


def run(p: ParsedInvoice, own_state: Optional[str] = None, invoice_like: bool = True) -> list[dict[str, Any]]:
    """invoice_like=False for Payment/Receipt/Contra/Journal: those carry no
    GST, so tax-split and taxable-value checks would only produce noise."""
    # Two readers of the same scan disagreeing is exactly where a misread
    # handwritten digit hides, so say so whatever the voucher type.
    # note: what the bill's arithmetic settled it as (pipeline.settle).
    differs = [_v("reading_differs", WARN,
                  c.get("note") or f"{LABELS.get(c['field'], c['field'])}: the scanned text reads {c['text']}, "
                                   f"the page image reads {c['image']}. Check it on the bill.", c["image"], c["text"])
               for c in p.conflicts]
    return differs + (_run_invoice(p, own_state) if invoice_like else _run_settlement(p))


def _run_invoice(p: ParsedInvoice, own_state: Optional[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    D = Decimal

    taxable = p.get("taxable_total")
    total = p.get("grand_total")
    roundoff = p.get("round_off") or D(0)
    cgst, sgst = p.get("cgst") or D(0), p.get("sgst") or D(0)
    igst, cess = p.get("igst") or D(0), p.get("cess") or D(0)
    tax = cgst + sgst + igst + cess

    # --- GSTIN validity ---------------------------------------------------
    for key, who in (("party_gstin", "Supplier"), ("own_gstin", "Your")):
        g = p.get(key)
        if g:
            if gst.gstin_checksum_ok(g):
                out.append(_v(f"{key}_valid", OK, f"{who} GSTIN {g} passes the checksum "
                                                 f"({gst.state_name(gst.gstin_state(g))})"))
            else:
                out.append(_v(f"{key}_valid", ERROR,
                              f"{who} GSTIN {g} fails the GST checksum - likely misread or invalid", "valid GSTIN", g))

    # --- line items sum to the taxable value ------------------------------
    if p.lines and taxable is not None:
        line_sum = sum((r.get("amount") or D(0)) for r in p.lines)
        if gst.close(line_sum, taxable, "1.00"):
            out.append(_v("line_sum", OK, f"{len(p.lines)} line items add up to the taxable value", taxable, line_sum))
        else:
            out.append(_v("line_sum", ERROR,
                          "Line items do not add up to the taxable value - a line was misread or missed",
                          taxable, line_sum))
    elif p.lines and total is not None and not tax:
        # A bill with no tax at all, like most kachcha bills: its lines add up
        # to the total. The one check that catches a misread handwritten figure.
        line_sum = sum((r.get("amount") or D(0)) for r in p.lines)
        if gst.close(line_sum, total - roundoff, "1.00"):
            out.append(_v("line_sum", OK, f"{len(p.lines)} line items add up to the total", total, line_sum))
        else:
            out.append(_v("line_sum", ERROR,
                          "Line items do not add up to the total - a figure was misread or a line missed",
                          total - roundoff, line_sum))

    bad = [r for r in p.lines if r.get("arith_ok") is False and r.get("quantity") and r.get("rate")]
    for r in bad:
        out.append(_v("line_arith", WARN,
                      f"Line {r['line_no'] + 1}: quantity x rate does not equal the amount",
                      (r.get("quantity") or D(0)) * (r.get("rate") or D(0)), r.get("amount")))

    # --- tax split matches the place of supply ----------------------------
    seller_state = gst.gstin_state(p.get("party_gstin"))
    pos = p.get("place_of_supply") or own_state
    want = gst.expected_split(seller_state, pos)
    if want and tax:
        got = "igst" if igst and not (cgst or sgst) else ("cgst_sgst" if (cgst or sgst) and not igst else "mixed")
        s_name, p_name = gst.state_name(seller_state), gst.state_name(pos)
        if got == want:
            label = "IGST" if want == "igst" else "CGST + SGST"
            kind = "inter-state" if want == "igst" else "intra-state"
            out.append(_v("tax_split", OK,
                          f"{label} is correct: supplier in {s_name}, place of supply {p_name} ({kind})"))
        else:
            out.append(_v("tax_split", ERROR,
                          f"Supplier is in {s_name} and place of supply is {p_name}, so this should be "
                          f"{'IGST' if want == 'igst' else 'CGST + SGST'}",
                          want.upper().replace("_", " + "), got.upper().replace("_", " + ")))

    if cgst and sgst and not gst.close(cgst, sgst, "1.00"):
        out.append(_v("cgst_sgst_equal", ERROR, "CGST and SGST must be equal", cgst, sgst))

    # --- the tax arithmetic itself ----------------------------------------
    for key, amt in (("cgst", cgst), ("sgst", sgst), ("igst", igst)):
        rate = p.get(f"{key}_rate")
        if not (rate and amt and taxable):
            continue
        base = taxable / 2 if key in ("cgst", "sgst") else taxable
        want_amt = (base * D(str(rate)) / D(100)).quantize(D("0.01"))
        if gst.close(want_amt, amt, "2.00"):
            out.append(_v(f"{key}_math", OK, f"{key.upper()} {rate}% of {taxable} = {amt}"))
        else:
            out.append(_v(f"{key}_math", WARN,
                          f"{key.upper()} at {rate}% should be {want_amt} on a taxable value of {taxable}",
                          want_amt, amt))
        if float(rate) not in gst.VALID_GST_RATES:
            out.append(_v(f"{key}_rate_valid", ERROR, f"{rate}% is not a GST rate that exists", "0/5/12/18/28", rate))

    # --- grand total ------------------------------------------------------
    if taxable is not None and total is not None:
        want_total = taxable + tax + roundoff
        if gst.close(want_total, total, "1.00"):
            out.append(_v("total", OK, f"Taxable {taxable} + tax {tax} = {total}"))
        else:
            out.append(_v("total", ERROR, "Taxable value plus tax does not equal the invoice total",
                          want_total, total))

    if not tax and taxable and total and abs(total - taxable) > D("1"):
        out.append(_v("tax_missing", ERROR,
                      "No tax rows were read, but the total exceeds the taxable value - "
                      "the tax table was probably missed", taxable, total))

    # --- dates ------------------------------------------------------------
    inv_date, due = p.get("invoice_date"), p.get("due_date")
    if inv_date:
        from datetime import date as _date

        if inv_date > _date.today():
            out.append(_v("date_future", WARN, f"Invoice date {inv_date} is in the future", "<= today", inv_date))
        if due and due < inv_date:
            out.append(_v("due_before_invoice", WARN, "Due date falls before the invoice date", f">= {inv_date}", due))
    else:
        out.append(_v("date_missing", ERROR, "No invoice date was found - Tally needs one to post"))

    if not p.get("invoice_no"):
        out.append(_v("invoice_no_missing", ERROR, "No invoice number was found - needed for duplicate checks and GSTR"))
    if not p.lines:
        out.append(_v("lines_missing", WARN, "No line items were read; the entry will post as a single amount"))

    return out


def _run_settlement(p: ParsedInvoice) -> list[dict[str, Any]]:
    """Payment, Receipt, Contra, Journal: an amount, a date, a counterparty."""
    out: list[dict[str, Any]] = []
    total = p.get("grand_total")
    if total is None or total == 0:
        out.append(_v("amount_missing", ERROR, "No amount was found - enter the amount to post"))
    else:
        out.append(_v("amount", OK, f"Amount {total}"))
    g = p.get("party_gstin")
    if g and not gst.gstin_checksum_ok(g):
        out.append(_v("party_gstin_valid", WARN, f"GSTIN {g} fails the checksum", "valid GSTIN", g))
    if not p.get("invoice_date"):
        out.append(_v("date_missing", ERROR, "No date was found - Tally needs one to post"))
    return out


def worst(validations: list[dict[str, Any]]) -> str:
    if any(v["severity"] == ERROR for v in validations):
        return ERROR
    if any(v["severity"] == WARN for v in validations):
        return WARN
    return OK
