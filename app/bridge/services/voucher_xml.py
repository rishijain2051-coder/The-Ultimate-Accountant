"""Build the Tally voucher XML for an approved entry.

Sign convention, which is the part that is easy to get wrong and expensive to
get wrong: in Tally's XML a DEBIT is a negative AMOUNT with
ISDEEMEDPOSITIVE=Yes, and a CREDIT is a positive AMOUNT with
ISDEEMEDPOSITIVE=No. Every voucher must net to exactly zero or Tally rejects
it -- build() asserts that before anything is sent.

Which side the party sits on comes from the voucher type's behaviour
(vouchers.py), so the same builder handles Purchase, Sales, both notes,
Payment, Receipt, Contra and Journal:

  Purchase     Dr expense + Dr input tax          Cr supplier
  Sales        Dr customer                        Cr income + Cr output tax
  Debit Note   Dr supplier                        Cr expense + Cr input tax
  Credit Note  Dr income + Dr output tax          Cr customer
  Payment      Dr party/expense                   Cr bank/cash
  Receipt      Dr bank/cash                       Cr party/income
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .tally import esc
from .vouchers import behaviour

TWOPLACES = Decimal("0.01")
# The voucher field that links the bill (services/bill_links.py): name, UDF index.
# 20001-65536 is free for add-ons; 10000-20000 is Tally's own.
BILL_LINK_UDF = ("TIBBillLink", 45001)


def q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(TWOPLACES)


@dataclass
class VoucherLine:
    """One allocation on the non-party side: which ledger, how much, stock item if any."""
    ledger_name: str
    amount: Decimal
    stock_item: Optional[str] = None
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    uom: Optional[str] = None
    # An item's description in Tally, under its name: a line of it per line.
    description: Optional[str] = None
    # Journal / free-form only: which side this line sits on.
    is_debit: Optional[bool] = None
    # The entry line it came from, so the review screen can change its row.
    line_id: Optional[int] = None
    # Its GST rate, for the rate details Tally keeps on an item line.
    gst_rate: Optional[float] = None


@dataclass
class VoucherSpec:
    company: str
    voucher_type: str
    date: date
    party_ledger: str
    lines: list[VoucherLine]
    voucher_parent: Optional[str] = None
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None
    taxes: dict[str, tuple[str, Decimal]] = field(default_factory=dict)  # kind -> (ledger, amount)
    round_off: Optional[tuple[str, Decimal]] = None
    narration: Optional[str] = None
    place_of_supply: Optional[str] = None
    party_gstin: Optional[str] = None
    use_inventory: bool = False
    voucher_number: Optional[str] = None
    # Optional voucher: visible in Tally's daybook, excluded from the books
    # until someone marks it regular. The local equivalent of a "draft".
    optional: bool = False
    # Where the bill can be opened from, for the voucher's link field.
    bill_link: Optional[str] = None
    # Tally's "Supplier Invoice No.": blank unless typed in, as the books are kept.
    reference: Optional[str] = None
    # What a voucher keyed in Tally carries besides its rows (read off the
    # books of a working business): the company's GST registration and GSTIN, the
    # party's state, the godown the items go into, the numbering style.
    gst_registration: Optional[str] = None
    company_gstin: Optional[str] = None
    company_state: Optional[str] = None
    party_state: Optional[str] = None
    godown: Optional[str] = None
    numbering_manual: bool = False
    inter_state: bool = False


class VoucherImbalance(ValueError):
    """Debits and credits do not net to zero. Never send one of these."""


def _entry(ledger: str, amount: Decimal, is_debit: bool, bill: str | None = None, invoice: bool = False) -> str:
    """One ledger row. An invoice keeps its ledger rows in LEDGERENTRIES.LIST,
    each on its nature's side with a signed amount, as Tally stores them: a
    round-off that takes 0.40 off is a Dr-nature row of +0.40. Tally drops
    an invoice's rows from ALLLEDGERENTRIES.LIST - the supplier, the taxes -
    and takes what is left as an import exception (bill 6748, 2026-09-28).
    An accounting voucher's rows (ALLLEDGERENTRIES.LIST) sit on the side
    their sign says: a negative amount is on the other side."""
    amount = q(amount)
    if not invoice and amount < 0:
        amount, is_debit = -amount, not is_debit
    signed = -amount if is_debit else amount
    bill_block = ""
    if bill:
        bill_block = f"""
   <BILLALLOCATIONS.LIST>
    <NAME>{esc(bill)}</NAME><BILLTYPE>New Ref</BILLTYPE>
    <AMOUNT>{signed}</AMOUNT>
   </BILLALLOCATIONS.LIST>"""
    tag = "LEDGERENTRIES.LIST" if invoice else "ALLLEDGERENTRIES.LIST"
    return f"""
  <{tag}>
   <LEDGERNAME>{esc(ledger)}</LEDGERNAME>
   <ISDEEMEDPOSITIVE>{"Yes" if is_debit else "No"}</ISDEEMEDPOSITIVE>
   <ISPARTYLEDGER>{"Yes" if bill else "No"}</ISPARTYLEDGER>
   <AMOUNT>{signed}</AMOUNT>{bill_block}
  </{tag}>"""


# GST rate heads as Tally names them on an item's rate details.
_HEADS = (("CGST", 0.5), ("SGST/UTGST", 0.5), ("IGST", 1.0))


# The GST nature Tally stores on an item line, by (purchase or sale,
# inter-state, taxable): only the names seen in the books, so Tally is never
# sent one it does not have. Any other case is left to Tally, which works it
# out from the ledger and the item as it does for a voucher keyed there.
_NATURES = {("Purchase", False, True): "Local Purchase - Taxable"}


def _gst_nature(spec: "VoucherSpec", rate: Optional[float]) -> Optional[str]:
    if rate is None:
        return None
    b = behaviour(spec.voucher_type, spec.voucher_parent)
    return _NATURES.get((b.parent, spec.inter_state, bool(rate)))


def _inventory_entry(ln: VoucherLine, is_debit: bool, spec: Optional["VoucherSpec"] = None) -> str:
    if q(ln.amount) < 0:
        is_debit = not is_debit
    signed = -abs(q(ln.amount)) if is_debit else abs(q(ln.amount))
    qty = q(ln.quantity) if ln.quantity is not None else Decimal("1.00")
    uom = f" {esc(ln.uom)}" if ln.uom else ""
    rate = f"{q(ln.rate)}/{esc(ln.uom) if ln.uom else 'Nos'}" if ln.rate is not None else None
    rate_block = f"<RATE>{rate}</RATE>" if rate else ""
    yn = "Yes" if is_debit else "No"
    gst = batch = details = ""
    nature = _gst_nature(spec, ln.gst_rate) if spec else None
    if nature:
        gst = f"""
   <GSTOVRDNTAXABILITY>{"Taxable" if ln.gst_rate else "Exempt"}</GSTOVRDNTAXABILITY>
   <GSTSOURCETYPE>Ledger</GSTSOURCETYPE>
   <GSTLEDGERSOURCE>{esc(ln.ledger_name)}</GSTLEDGERSOURCE>
   <GSTOVRDNSTOREDNATURE>{nature}</GSTOVRDNSTOREDNATURE>
   <GSTOVRDNTYPEOFSUPPLY>Goods</GSTOVRDNTYPEOFSUPPLY>
   <GSTRATEINFERAPPLICABILITY>As per Masters/Company</GSTRATEINFERAPPLICABILITY>
   <GSTHSNINFERAPPLICABILITY>As per Masters/Company</GSTHSNINFERAPPLICABILITY>"""
        details = "".join(f"""
   <RATEDETAILS.LIST>
    <GSTRATEDUTYHEAD>{head}</GSTRATEDUTYHEAD>
    <GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
    <GSTRATE>{ln.gst_rate * part:g}</GSTRATE>
   </RATEDETAILS.LIST>""" for head, part in _HEADS if ln.gst_rate)
    if spec and spec.godown:
        batch = f"""
   <BATCHALLOCATIONS.LIST>
    <GODOWNNAME>{esc(spec.godown)}</GODOWNNAME>
    <BATCHNAME>Primary Batch</BATCHNAME>
    <AMOUNT>{signed}</AMOUNT>
    <ACTUALQTY>{qty}{uom}</ACTUALQTY>
    <BILLEDQTY>{qty}{uom}</BILLEDQTY>{f"{chr(10)}    <BATCHRATE>{rate}</BATCHRATE>" if rate else ""}
   </BATCHALLOCATIONS.LIST>"""
    # First in the line, as Tally keeps it.
    lines = [d.strip() for d in (ln.description or "").splitlines() if d.strip()]
    desc = "".join(f"\n    <BASICUSERDESCRIPTION>{esc(d)}</BASICUSERDESCRIPTION>" for d in lines)
    desc = f'\n   <BASICUSERDESCRIPTION.LIST TYPE="String">{desc}\n   </BASICUSERDESCRIPTION.LIST>' if lines else ""
    return f"""
  <ALLINVENTORYENTRIES.LIST>{desc}
   <STOCKITEMNAME>{esc(ln.stock_item)}</STOCKITEMNAME>{gst}
   <ISDEEMEDPOSITIVE>{yn}</ISDEEMEDPOSITIVE>
   <ISLASTDEEMEDPOSITIVE>{yn}</ISLASTDEEMEDPOSITIVE>
   {rate_block}
   <AMOUNT>{signed}</AMOUNT>
   <ACTUALQTY>{qty}{uom}</ACTUALQTY>
   <BILLEDQTY>{qty}{uom}</BILLEDQTY>{batch}
   <ACCOUNTINGALLOCATIONS.LIST>
    <LEDGERNAME>{esc(ln.ledger_name)}</LEDGERNAME>
    <ISDEEMEDPOSITIVE>{yn}</ISDEEMEDPOSITIVE>
    <AMOUNT>{signed}</AMOUNT>
   </ACCOUNTINGALLOCATIONS.LIST>{details}
  </ALLINVENTORYENTRIES.LIST>"""


def parts(spec: VoucherSpec) -> list[tuple[dict, str]]:
    """The voucher's rows in order, each as (what it is, its XML): the party,
    lines (with the entry lines behind them), a tax, the round-off. The review
    screen changes a row by what it is; build() joins this same list, so what
    the screen shows cannot drift from what Tally receives."""
    b = behaviour(spec.voucher_type, spec.voucher_parent)

    goods = sum(q(l.amount) for l in spec.lines)
    tax_total = sum(q(a) for _led, a in spec.taxes.values())
    ro = q(spec.round_off[1]) if spec.round_off else Decimal("0.00")
    party_total = q(goods + tax_total + ro)

    # A journal's own lines can net to nothing (Dr 500, Cr 500); a party can't.
    if party_total == 0 and (b.party_is_debit is not None or not any(q(l.amount) for l in spec.lines)):
        raise VoucherImbalance("Voucher total is zero - nothing to post.")

    def line(*ls: VoucherLine) -> dict:
        return {"role": "line", "line_ids": [l.line_id for l in ls if l.line_id is not None]}

    out: list[tuple[dict, str]] = []

    if b.party_is_debit is None:
        # Journal / Contra / unknown custom type: every line carries its own
        # side, and the "party" is simply the balancing ledger.
        dr = sum(q(l.amount) for l in spec.lines if l.is_debit)
        cr = sum(q(l.amount) for l in spec.lines if l.is_debit is False)
        unassigned = [l for l in spec.lines if l.is_debit is None]
        if unassigned:
            raise VoucherImbalance(
                f"{spec.voucher_type} is free-form: mark each line as debit or credit "
                f"({len(unassigned)} line(s) are unassigned).")
        balance = q(dr - cr)
        for l in spec.lines:
            out.append((line(l), _entry(l.ledger_name, l.amount, is_debit=bool(l.is_debit))))
        if balance:
            # The party ledger takes whatever balances the voucher.
            out.append(({"role": "party"}, _entry(spec.party_ledger, abs(balance), is_debit=balance < 0,
                                                  bill=spec.invoice_no)))
    else:
        party_debit = b.party_is_debit
        line_debit = not party_debit
        inv = b.is_invoice

        out.append(({"role": "party"}, _entry(spec.party_ledger, party_total, is_debit=party_debit,
                                              bill=spec.invoice_no, invoice=inv)))

        items = [l for l in spec.lines if spec.use_inventory and l.stock_item]
        out.extend((line(l), _inventory_entry(l, is_debit=line_debit, spec=spec)) for l in items)
        # The rest - charges beside the items, or every line of a bill with
        # no items - as ledgers. Repeated ledgers merge: Tally accepts
        # duplicates, but the voucher reads badly in the daybook and
        # reconciles worse. A Dr and a Cr row of one ledger stay two rows.
        merged: dict[tuple[str, bool], list[VoucherLine]] = {}
        for l in spec.lines:
            if l not in items:
                merged.setdefault((l.ledger_name, q(l.amount) < 0), []).append(l)
        out.extend((line(*ls), _entry(name, sum(q(l.amount) for l in ls), is_debit=line_debit, invoice=inv))
                   for (name, _cr), ls in merged.items())

        for kind, (led, amt) in spec.taxes.items():
            if q(amt):
                out.append(({"role": "tax", "kind": kind}, _entry(led, amt, is_debit=line_debit, invoice=inv)))

        if ro:
            # A positive round-off adds to the gross; a negative one takes off
            # it: on an invoice a Dr-nature row of a plus amount, elsewhere a
            # row on the party's side.
            out.append(({"role": "round"}, _entry(spec.round_off[0], ro, is_debit=line_debit, invoice=inv)))

    _assert_balanced([x for _, x in out])
    return out


def build(spec: VoucherSpec) -> str:
    """-> a <TALLYMESSAGE> block ready for tally.import_xml()."""
    b = behaviour(spec.voucher_type, spec.voucher_parent)
    body = "".join(x for _, x in parts(spec))

    ref_date = spec.invoice_date or spec.date
    vch_no = f"<VOUCHERNUMBER>{esc(spec.voucher_number)}</VOUCHERNUMBER>" if spec.voucher_number else ""
    # Supplier Invoice No. and date: only when typed in; the books leave them blank.
    ref = (f"<REFERENCE>{esc(spec.reference)}</REFERENCE>\n  <REFERENCEDATE>{ref_date:%Y%m%d}</REFERENCEDATE>"
           if spec.reference else "")
    pos = f"<PLACEOFSUPPLY>{esc(spec.place_of_supply)}</PLACEOFSUPPLY>" if spec.place_of_supply else ""
    gstin = f"<PARTYGSTIN>{esc(spec.party_gstin)}</PARTYGSTIN>" if spec.party_gstin else ""
    view = "Invoice Voucher View" if b.is_invoice else "Accounting Voucher View"
    optional = "<ISOPTIONAL>Yes</ISOPTIONAL>" if spec.optional else ""
    # As a voucher keyed in Tally carries them (a purchase keyed by hand).
    keyed = "".join(f"\n  <{tag}>{esc(v)}</{tag}>" for tag, v in (
        ("VCHENTRYMODE", ("Item Invoice" if spec.use_inventory else "Accounting Invoice") if b.is_invoice else None),
        ("STATENAME", spec.party_state), ("COUNTRYOFRESIDENCE", "India" if spec.party_state else None),
        ("GSTREGISTRATION", spec.gst_registration), ("CMPGSTIN", spec.company_gstin),
        ("CMPGSTSTATE", spec.company_state), ("BASICBUYERNAME", spec.company if spec.gst_registration else None),
        ("PARTYMAILINGNAME", spec.party_ledger), ("BASICBASEPARTYNAME", spec.party_ledger),
        ("NUMBERINGSTYLE", "Manual" if spec.numbering_manual else None),
    ) if v)
    name, index = BILL_LINK_UDF
    link = (f'\n  <UDF:{name.upper()}.LIST DESC="`{name}`" ISLIST="YES" TYPE="String" INDEX="{index}">'
            f'<UDF:{name.upper()} DESC="`{name}`">{esc(spec.bill_link)}</UDF:{name.upper()}>'
            f'</UDF:{name.upper()}.LIST>') if spec.bill_link else ""

    return f"""<TALLYMESSAGE xmlns:UDF="TallyUDF">
 <VOUCHER VCHTYPE="{esc(spec.voucher_type)}" ACTION="Create" OBJVIEW="{view}">
  <DATE>{spec.date:%Y%m%d}</DATE>
  <EFFECTIVEDATE>{spec.date:%Y%m%d}</EFFECTIVEDATE>
  <VOUCHERTYPENAME>{esc(spec.voucher_type)}</VOUCHERTYPENAME>
  {vch_no}
  {ref}
  <PARTYLEDGERNAME>{esc(spec.party_ledger)}</PARTYLEDGERNAME>
  <PARTYNAME>{esc(spec.party_ledger)}</PARTYNAME>
  {gstin}{pos}{keyed}
  <PERSISTEDVIEW>{view}</PERSISTEDVIEW>
  <ISINVOICE>{"Yes" if b.is_invoice else "No"}</ISINVOICE>
  {optional}
  <NARRATION>{esc(spec.narration or "")}</NARRATION>{link}{body}
 </VOUCHER>
</TALLYMESSAGE>"""


def _row(ledger: str, amount: Decimal, is_debit: bool, party: bool = False, inner: str = "") -> str:
    """An accounting row whose inner lists (bill, bank) are made by the caller
    with the signed amount this returns alongside."""
    signed = -q(amount) if is_debit else q(amount)
    return f"""
  <ALLLEDGERENTRIES.LIST>
   <LEDGERNAME>{esc(ledger)}</LEDGERNAME>
   <ISDEEMEDPOSITIVE>{"Yes" if is_debit else "No"}</ISDEEMEDPOSITIVE>
   <ISPARTYLEDGER>{"Yes" if party else "No"}</ISPARTYLEDGER>
   <AMOUNT>{signed}</AMOUNT>{inner}
  </ALLLEDGERENTRIES.LIST>"""


def bank_voucher(company: str, voucher_type: str, day: date, bank_ledger: str, amount: Decimal,
                 ledgers: list[tuple[str, Decimal, bool]], narration: Optional[str] = None,
                 gst_registration: Optional[str] = None, company_gstin: Optional[str] = None,
                 company_state: Optional[str] = None) -> str:
    """A Payment or Receipt for one line of a bank statement, as the books keep
    them in a working set of books: the ledgers it went to, then the
    bank with the bank details Tally keeps on it; numbered by Tally.

    amount: the statement's, + in, - out. ledgers: (name, amount, bill-wise),
    adding up to it without its sign: Cr when money came in, Dr when it went
    out, and a minus amount on the other side (charges taken off a receipt).
    A bill-wise ledger's amount goes on account."""
    amount = q(amount)
    if not amount or q(sum((q(a) for _n, a, _b in ledgers), Decimal(0))) != abs(amount):
        raise VoucherImbalance(f"The ledgers add up to {q(sum((q(a) for _n, a, _b in ledgers), Decimal(0)))}, "
                               f"not the statement's {abs(amount)}.")
    money_in = amount > 0
    rows: list[str] = []
    for name, a, bill_wise in ledgers:
        is_debit = not money_in if q(a) > 0 else money_in
        signed = -abs(q(a)) if is_debit else abs(q(a))
        bill = f"""
   <BILLALLOCATIONS.LIST>
    <BILLTYPE>On Account</BILLTYPE>
    <AMOUNT>{signed}</AMOUNT>
   </BILLALLOCATIONS.LIST>""" if bill_wise else ""
        rows.append(_row(name, abs(q(a)), is_debit, party=bill_wise, inner=bill))
    party = ledgers[0][0]
    signed = -abs(amount) if money_in else abs(amount)
    rows.append(_row(bank_ledger, abs(amount), money_in, party=True, inner=f"""
   <BANKALLOCATIONS.LIST>
    <DATE>{day:%Y%m%d}</DATE>
    <INSTRUMENTDATE>{day:%Y%m%d}</INSTRUMENTDATE>
    <TRANSACTIONTYPE>Cheque</TRANSACTIONTYPE>
    <PAYMENTFAVOURING>{esc(party)}</PAYMENTFAVOURING>
    <CHEQUECROSSCOMMENT>A/c Payee</CHEQUECROSSCOMMENT>
    <PAYMENTMODE>Transacted</PAYMENTMODE>
    <BANKPARTYNAME>{esc(party)}</BANKPARTYNAME>
    <AMOUNT>{signed}</AMOUNT>
   </BANKALLOCATIONS.LIST>"""))
    _assert_balanced(rows)
    view = "Accounting Voucher View"
    keyed = "".join(f"\n  <{tag}>{esc(v)}</{tag}>" for tag, v in (
        ("GSTREGISTRATION", gst_registration), ("CMPGSTIN", company_gstin), ("CMPGSTSTATE", company_state)) if v)
    return f"""<TALLYMESSAGE xmlns:UDF="TallyUDF">
 <VOUCHER VCHTYPE="{esc(voucher_type)}" ACTION="Create" OBJVIEW="{view}">
  <DATE>{day:%Y%m%d}</DATE>
  <EFFECTIVEDATE>{day:%Y%m%d}</EFFECTIVEDATE>
  <VOUCHERTYPENAME>{esc(voucher_type)}</VOUCHERTYPENAME>
  <PARTYLEDGERNAME>{esc(party)}</PARTYLEDGERNAME>{keyed}
  <PERSISTEDVIEW>{view}</PERSISTEDVIEW>
  <ISINVOICE>No</ISINVOICE>
  <NARRATION>{esc(narration or "")}</NARRATION>{"".join(rows)}
 </VOUCHER>
</TALLYMESSAGE>"""


def _assert_balanced(parts: list[str]) -> None:
    total = Decimal("0")
    # Only the top-level entry amounts, not the nested accounting-allocation
    # or bill-allocation copies of the same figure.
    for block in parts:
        m = re.search(r"<AMOUNT>(-?[\d.]+)</AMOUNT>", block)
        if m:
            total += Decimal(m.group(1))
    if total.quantize(TWOPLACES) != Decimal("0.00"):
        raise VoucherImbalance(
            f"Debits and credits differ by {total.quantize(TWOPLACES)}. "
            f"Tally would reject this voucher; fix the amounts before posting."
        )


def standalone_xml(company: str, message: str) -> str:
    """Wrap a TALLYMESSAGE as a file Tally can import via Gateway > Import Data."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE><ID>Vouchers</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
   <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
   <SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY>
  </STATICVARIABLES></DESC>
  <DATA>{message}</DATA>
 </BODY>
</ENVELOPE>"""
