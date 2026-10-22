"""What each Tally voucher type means in debits and credits.

Tally lets you invent voucher types, but every one of them declares a parent
that is one of the built-in types. So the accounting behaviour is driven off
the parent, and a custom "GST Purchase - Imports" behaves exactly like
Purchase without anyone having to configure it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VoucherBehaviour:
    parent: str
    # True  -> the party/counterparty ledger is DEBITED (we are owed / we paid)
    # False -> the party ledger is CREDITED (we owe)
    # None  -> free-form; the user assigns each side (Journal, Stock Journal)
    party_is_debit: Optional[bool]
    # "invoice"  -> taxable lines + tax ledgers + party   (Purchase, Sales, notes)
    # "settlement" -> party on one side, bank/cash on the other (Payment, Receipt)
    # "transfer" -> bank/cash both sides (Contra)
    # "free"     -> anything (Journal)
    shape: str
    # What the counterparty ledger normally is, used to scope the picker.
    party_group: str
    label: str
    # Does Tally treat it as an invoice (item/accounting invoice view)?
    is_invoice: bool = False


BEHAVIOURS: dict[str, VoucherBehaviour] = {
    "Purchase": VoucherBehaviour(
        "Purchase", party_is_debit=False, shape="invoice", party_group="Creditors",
        label="Purchase - a bill from a supplier", is_invoice=True),
    "Sales": VoucherBehaviour(
        "Sales", party_is_debit=True, shape="invoice", party_group="Debtors",
        label="Sales - a bill you raised", is_invoice=True),
    "Debit Note": VoucherBehaviour(
        "Debit Note", party_is_debit=True, shape="invoice", party_group="Creditors",
        label="Debit Note - purchase return or supplier undercharge", is_invoice=True),
    "Credit Note": VoucherBehaviour(
        "Credit Note", party_is_debit=False, shape="invoice", party_group="Debtors",
        label="Credit Note - sales return or customer overcharge", is_invoice=True),
    "Payment": VoucherBehaviour(
        "Payment", party_is_debit=True, shape="settlement", party_group="Creditors",
        label="Payment - money you paid out"),
    "Receipt": VoucherBehaviour(
        "Receipt", party_is_debit=False, shape="settlement", party_group="Debtors",
        label="Receipt - money you received"),
    "Contra": VoucherBehaviour(
        "Contra", party_is_debit=None, shape="transfer", party_group="Bank Accounts",
        label="Contra - cash/bank transfer"),
    "Journal": VoucherBehaviour(
        "Journal", party_is_debit=None, shape="free", party_group="",
        label="Journal - adjustment entry"),
    "Purchase Order": VoucherBehaviour(
        "Purchase Order", party_is_debit=False, shape="invoice", party_group="Creditors",
        label="Purchase Order", is_invoice=True),
    "Sales Order": VoucherBehaviour(
        "Sales Order", party_is_debit=True, shape="invoice", party_group="Debtors",
        label="Sales Order", is_invoice=True),
    "Receipt Note": VoucherBehaviour(
        "Receipt Note", party_is_debit=False, shape="invoice", party_group="Creditors",
        label="Receipt Note - goods received", is_invoice=True),
    "Delivery Note": VoucherBehaviour(
        "Delivery Note", party_is_debit=True, shape="invoice", party_group="Debtors",
        label="Delivery Note - goods sent", is_invoice=True),
}

# Offered before Tally has ever been connected, so the upload screen is never
# empty. Replaced by the real list (including your custom types) after a sync.
DEFAULT_TYPES = [
    "Purchase", "Sales", "Payment", "Receipt", "Contra",
    "Journal", "Debit Note", "Credit Note",
]


def behaviour(voucher_type: str, parent: str | None = None) -> VoucherBehaviour:
    """Resolve a voucher type -- custom or built-in -- to its accounting shape."""
    if voucher_type in BEHAVIOURS:
        return BEHAVIOURS[voucher_type]
    if parent and parent in BEHAVIOURS:
        return BEHAVIOURS[parent]
    # An unknown custom type with an unknown parent: treat it as a journal so
    # the user assigns both sides explicitly rather than us guessing a sign.
    return VoucherBehaviour(parent or voucher_type, None, "free", "", f"{voucher_type} (custom)")


def is_invoice_like(voucher_type: str, parent: str | None = None) -> bool:
    return behaviour(voucher_type, parent).shape == "invoice"


def needs_tax_rows(voucher_type: str, parent: str | None = None) -> bool:
    """Only invoice-shaped vouchers carry GST. A Payment does not."""
    return behaviour(voucher_type, parent).shape == "invoice"
