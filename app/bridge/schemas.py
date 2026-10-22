from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyOut(ORM):
    id: int
    tally_name: str
    gstin: Optional[str] = None
    state_code: Optional[str] = None
    gst_registration: Optional[str] = None
    godown: Optional[str] = None
    is_active: bool = True
    last_synced_at: Optional[datetime] = None
    ledger_count: int = 0
    stock_count: int = 0


class LedgerOut(ORM):
    id: int
    tally_name: str
    parent: Optional[str] = None
    gstin: Optional[str] = None
    state: Optional[str] = None


class StockItemOut(ORM):
    id: int
    tally_name: str
    parent: Optional[str] = None
    uom: Optional[str] = None
    hsn: Optional[str] = None
    gst_rate: Optional[float] = None


class DocumentOut(ORM):
    id: int
    filename: str
    sha256: str
    pages: int = 0
    source: str = "upload"
    has_text_layer: bool = False
    ocr_used: bool = False
    status: str
    error: Optional[str] = None
    created_at: datetime


class EntryLineOut(ORM):
    id: int
    line_no: int
    description_raw: Optional[str] = None
    description: Optional[str] = None
    hsn_sac: Optional[str] = None
    quantity: Optional[Decimal] = None
    uom: Optional[str] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    gst_rate: Optional[float] = None
    stock_item_id: Optional[int] = None
    ledger_id: Optional[int] = None
    match_confidence: float = 0.0
    match_reason: Optional[str] = None
    candidates: Optional[list[dict[str, Any]]] = None
    matched_name: Optional[str] = None


class EntrySummary(ORM):
    id: int
    document_id: int
    company_id: Optional[int] = None
    voucher_type: str
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None
    party_name_raw: Optional[str] = None
    party_gstin: Optional[str] = None
    party_ledger_id: Optional[int] = None
    party_ledger_name: Optional[str] = None
    party_confidence: float = 0.0
    grand_total: Optional[Decimal] = None
    currency: str = "INR"
    confidence: float = 0.0
    confidence_label: str = "low"
    status: str
    error_count: int = 0
    warning_count: int = 0
    duplicate_of_id: Optional[int] = None
    filename: Optional[str] = None
    created_at: datetime
    # The AI's check in the background: waiting, reading, done, failed; None: none.
    ai_state: Optional[str] = None


class EntryDetail(EntrySummary):
    reference: Optional[str] = None
    due_date: Optional[date] = None
    own_gstin: Optional[str] = None
    place_of_supply: Optional[str] = None
    place_of_supply_name: Optional[str] = None
    supply_type: Optional[str] = None
    taxable_total: Optional[Decimal] = None
    cgst: Optional[Decimal] = None
    sgst: Optional[Decimal] = None
    igst: Optional[Decimal] = None
    cess: Optional[Decimal] = None
    round_off: Optional[Decimal] = None
    narration: Optional[str] = None
    party_match_reason: Optional[str] = None
    party_candidates: Optional[list[dict[str, Any]]] = None
    field_meta: Optional[dict[str, Any]] = None
    validations: Optional[list[dict[str, Any]]] = None
    duplicate_reason: Optional[str] = None
    tally_voucher_id: Optional[str] = None
    export_path: Optional[str] = None
    posted_at: Optional[datetime] = None
    lines: list[EntryLineOut] = Field(default_factory=list)
    document: Optional[DocumentOut] = None


class LineUpdate(BaseModel):
    id: Optional[int] = None
    line_no: Optional[int] = None
    description_raw: Optional[str] = None
    description: Optional[str] = None
    hsn_sac: Optional[str] = None
    quantity: Optional[Decimal] = None
    uom: Optional[str] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    gst_rate: Optional[float] = None
    stock_item_id: Optional[int] = None
    ledger_id: Optional[int] = None
    # Take this line off the entry (a row removed on the review screen).
    delete: bool = False


class EntryUpdate(BaseModel):
    """Every field a reviewer may correct before approving."""
    company_id: Optional[int] = None
    voucher_type: Optional[str] = None
    invoice_no: Optional[str] = None
    # Tally's Supplier Invoice No.: blank unless you type it in.
    reference: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    party_name_raw: Optional[str] = None
    party_gstin: Optional[str] = None
    party_ledger_id: Optional[int] = None
    place_of_supply: Optional[str] = None
    supply_type: Optional[str] = None
    taxable_total: Optional[Decimal] = None
    cgst: Optional[Decimal] = None
    sgst: Optional[Decimal] = None
    igst: Optional[Decimal] = None
    cess: Optional[Decimal] = None
    round_off: Optional[Decimal] = None
    grand_total: Optional[Decimal] = None
    narration: Optional[str] = None
    lines: Optional[list[LineUpdate]] = None
    # The ledger for a tax or the round-off row: {"cgst": 12, "round": 40};
    # None puts that row back to the ledger the app picks itself.
    tax_ledgers: Optional[dict[str, Optional[int]]] = None
    # Off by default so a typo does not teach the matcher something wrong.
    learn: bool = True


class ApproveRequest(BaseModel):
    # "export" writes an XML file; "draft" posts an optional voucher that shows
    # in Tally's daybook without affecting the books; "post" posts it for real.
    mode: str = "export"
    dry_run: bool = False
    override_duplicate: bool = False
    override_errors: bool = False


class ApproveResult(BaseModel):
    ok: bool
    entry_id: int
    mode: str
    status: str
    message: str
    export_path: Optional[str] = None
    tally_voucher_id: Optional[str] = None
    xml: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    # True when the only thing in the way is a duplicate question the user can
    # answer with "post anyway".
    needs_ack: bool = False


class CreateMasterRequest(BaseModel):
    entry_id: int
    kind: str = "ledger"          # ledger | stock
    name: Optional[str] = None
    parent: Optional[str] = None
    # A ledger under Duties & Taxes: CGST | SGST/UTGST | IGST | Cess.
    duty_head: Optional[str] = None
    uom: str = "Nos"
    hsn: Optional[str] = None
    gst_rate: Optional[float] = None
    line_id: Optional[int] = None


class HealthOut(BaseModel):
    app: str = "ok"
    database: dict[str, Any]
    tally: dict[str, Any]
    ollama: dict[str, Any]
    ocr: dict[str, Any]
    watch: dict[str, Any]
    counts: dict[str, int]
