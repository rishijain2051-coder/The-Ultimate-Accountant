from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# Money is Numeric everywhere. Never float -- a 1-paisa drift on a tax line is
# a mismatch the user has to chase by hand.
Money = Numeric(18, 2)
Qty = Numeric(18, 4)


class Company(Base):
    """A company as it exists in Tally. Everything else is scoped to one."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tally_name: Mapped[str] = mapped_column(String(255), unique=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(15), index=True)
    state_code: Mapped[Optional[str]] = mapped_column(String(2))
    books_from: Mapped[Optional[date]] = mapped_column(Date())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # [{name, parent}] as read from Tally, including custom types.
    voucher_types: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)
    # Its GST registration ("Rajasthan Registration") and the godown items go
    # to, as the vouchers keyed in Tally carry them.
    gst_registration: Mapped[Optional[str]] = mapped_column(String(128))
    godown: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ledgers: Mapped[list["Ledger"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    stock_items: Mapped[list["StockItem"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Ledger(Base):
    """Cached Tally ledger master. Refreshed by a sync, read offline."""

    __tablename__ = "ledgers"
    __table_args__ = (UniqueConstraint("company_id", "tally_name", name="uq_ledger_company_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    tally_name: Mapped[str] = mapped_column(String(512), index=True)
    parent: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(15), index=True)
    state: Mapped[Optional[str]] = mapped_column(String(128))
    country: Mapped[Optional[str]] = mapped_column(String(128))
    is_party: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Its amounts are kept against bills. Not known until the next sync.
    bill_wise: Mapped[Optional[bool]] = mapped_column(Boolean)
    opening_balance: Mapped[Optional[Decimal]] = mapped_column(Money)
    # Name with punctuation/case/legal-suffix noise stripped, for fuzzy matching.
    norm_name: Mapped[str] = mapped_column(String(512), index=True, default="")

    company: Mapped[Company] = relationship(back_populates="ledgers")


class StockItem(Base):
    __tablename__ = "stock_items"
    __table_args__ = (UniqueConstraint("company_id", "tally_name", name="uq_stock_company_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    tally_name: Mapped[str] = mapped_column(String(512), index=True)
    parent: Mapped[Optional[str]] = mapped_column(String(255))
    uom: Mapped[Optional[str]] = mapped_column(String(64))
    hsn: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    gst_rate: Mapped[Optional[float]] = mapped_column(Float)
    norm_name: Mapped[str] = mapped_column(String(512), index=True, default="")

    company: Mapped[Company] = relationship(back_populates="stock_items")


class Bought(Base):
    """What the books say was bought from whom, and which ledger each item
    went to: read from the vouchers in Tally at every sync. How you keyed
    your own bills is the best hint for the next one - better than a name."""
    __tablename__ = "bought"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    party: Mapped[str] = mapped_column(String(512), index=True)
    item: Mapped[str] = mapped_column(String(512))
    ledger: Mapped[Optional[str]] = mapped_column(String(512))
    times: Mapped[int] = mapped_column(Integer, default=1)
    last_date: Mapped[Optional[date]] = mapped_column(Date())


class Document(Base):
    """One uploaded/watched file. sha256 is the cheap exact-duplicate gate."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload | watch
    # bill, or statement (a bank statement: services/statement.py). Rows kept
    # before there was a kind are bills.
    kind: Mapped[Optional[str]] = mapped_column(String(16), default="bill")
    # What the user said this document is, at upload. Survives a re-extract.
    requested_voucher_type: Mapped[Optional[str]] = mapped_column(String(64))
    requested_voucher_parent: Mapped[Optional[str]] = mapped_column(String(64))
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    # queued | processing | extracted | failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    entries: Mapped[list["Entry"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Entry(Base):
    """The open entry: what the app understood, and what it intends to post."""

    __tablename__ = "entries"
    __table_args__ = (
        Index("ix_entry_dupe", "company_id", "party_gstin", "invoice_no"),
        Index("ix_entry_status_conf", "status", "confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)

    voucher_type: Mapped[str] = mapped_column(String(64), default="Purchase")
    # The built-in type a custom voucher type derives from; drives Dr/Cr.
    voucher_parent: Mapped[Optional[str]] = mapped_column(String(64))
    invoice_no: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    # Tally's "Supplier Invoice No.": left blank, as the books are kept (the
    # voucher number is already the bill's number), unless typed in.
    reference: Mapped[Optional[str]] = mapped_column(String(128))
    invoice_date: Mapped[Optional[date]] = mapped_column(Date(), index=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date())

    party_name_raw: Mapped[Optional[str]] = mapped_column(String(512))
    party_gstin: Mapped[Optional[str]] = mapped_column(String(15), index=True)
    party_ledger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledgers.id", ondelete="SET NULL"))
    party_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    party_match_reason: Mapped[Optional[str]] = mapped_column(String(255))
    # Runner-up ledger matches, so the reviewer picks instead of typing.
    party_candidates: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)

    own_gstin: Mapped[Optional[str]] = mapped_column(String(15))
    place_of_supply: Mapped[Optional[str]] = mapped_column(String(2))
    supply_type: Mapped[Optional[str]] = mapped_column(String(16))

    taxable_total: Mapped[Optional[Decimal]] = mapped_column(Money)
    cgst: Mapped[Optional[Decimal]] = mapped_column(Money)
    sgst: Mapped[Optional[Decimal]] = mapped_column(Money)
    igst: Mapped[Optional[Decimal]] = mapped_column(Money)
    cess: Mapped[Optional[Decimal]] = mapped_column(Money)
    round_off: Mapped[Optional[Decimal]] = mapped_column(Money)
    grand_total: Mapped[Optional[Decimal]] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    narration: Mapped[Optional[str]] = mapped_column(Text)

    confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    # Per-field confidence + where each value came from (regex | llm | ocr | user).
    field_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    validations: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # pending | approved | posted | rejected | duplicate
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("entries.id", ondelete="SET NULL"))
    duplicate_reason: Mapped[Optional[str]] = mapped_column(String(255))

    tally_voucher_id: Mapped[Optional[str]] = mapped_column(String(128))
    tally_masterid: Mapped[Optional[str]] = mapped_column(String(64))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    export_path: Mapped[Optional[str]] = mapped_column(String(1024))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="entries")
    lines: Mapped[list["EntryLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="EntryLine.line_no"
    )
    party_ledger: Mapped[Optional[Ledger]] = relationship()


class EntryLine(Base):
    __tablename__ = "entry_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)

    description_raw: Mapped[Optional[str]] = mapped_column(Text)
    # Tally's own description of an item line ("SMALL", "5.5 FT"), as you
    # type it; the bill's words above are for matching and stay here.
    description: Mapped[Optional[str]] = mapped_column(Text)
    hsn_sac: Mapped[Optional[str]] = mapped_column(String(16))
    quantity: Mapped[Optional[Decimal]] = mapped_column(Qty)
    uom: Mapped[Optional[str]] = mapped_column(String(64))
    rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    amount: Mapped[Optional[Decimal]] = mapped_column(Money)
    gst_rate: Mapped[Optional[float]] = mapped_column(Float)

    stock_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stock_items.id", ondelete="SET NULL"))
    # Service invoices post to a ledger, not a stock item; both are supported.
    ledger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledgers.id", ondelete="SET NULL"))
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    match_reason: Mapped[Optional[str]] = mapped_column(String(255))
    candidates: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)

    entry: Mapped[Entry] = relationship(back_populates="lines")
    stock_item: Mapped[Optional[StockItem]] = relationship()
    ledger: Mapped[Optional[Ledger]] = relationship()


class Statement(Base):
    """A bank statement as uploaded: whose account, which books, its rows."""

    __tablename__ = "statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    # The account's ledger in Tally, found by its number, or picked.
    bank_ledger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledgers.id", ondelete="SET NULL"))
    bank: Mapped[Optional[str]] = mapped_column(String(128))
    account_no: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    holder: Mapped[Optional[str]] = mapped_column(String(255))
    period_from: Mapped[Optional[date]] = mapped_column(Date)
    period_to: Mapped[Optional[date]] = mapped_column(Date)
    opening: Mapped[Optional[Decimal]] = mapped_column(Money)
    # The last look in Tally for rows already posted: when, and why it failed.
    tally_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    tally_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship()
    company: Mapped[Optional["Company"]] = relationship()
    bank_ledger: Mapped[Optional["Ledger"]] = relationship()
    rows: Mapped[list["StatementRow"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="StatementRow.row_no")


class StatementRow(Base):
    """One line of a statement, as the bank printed it, and the voucher it
    becomes: posted only when you approve it."""

    __tablename__ = "statement_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)          # its place in the statement
    serial: Mapped[Optional[str]] = mapped_column(String(16))  # the bank's own number, when read
    txn_date: Mapped[Optional[date]] = mapped_column(Date)
    value_date: Mapped[Optional[date]] = mapped_column(Date)
    details: Mapped[Optional[str]] = mapped_column(Text)
    ref: Mapped[Optional[str]] = mapped_column(String(128))
    note: Mapped[Optional[str]] = mapped_column(Text)     # written by hand on the printed statement
    amount: Mapped[Optional[Decimal]] = mapped_column(Money)   # + money in, - money out
    balance: Mapped[Optional[Decimal]] = mapped_column(Money)
    page: Mapped[int] = mapped_column(Integer, default=0)
    # Where on its page (fractions of the page's height), to show it on the scan.
    top: Mapped[float] = mapped_column(Float, default=0.0)
    bottom: Mapped[float] = mapped_column(Float, default=0.0)
    check: Mapped[str] = mapped_column(String(8), default="ok")  # ok | fixed | first | bad
    read_notes: Mapped[Optional[list[str]]] = mapped_column(JSON)

    voucher_type: Mapped[Optional[str]] = mapped_column(String(64))
    # The other side of the voucher: [{"ledger_id", "name", "amount"}], the
    # amounts adding up to the row's.
    ledgers: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)
    narration: Mapped[Optional[str]] = mapped_column(Text)
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    match_reason: Mapped[Optional[str]] = mapped_column(String(255))
    candidates: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)
    # pending | in_tally (a voucher for it is there already) | posted | skipped
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    in_tally: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    tally_voucher_id: Mapped[Optional[str]] = mapped_column(String(64))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    statement: Mapped[Statement] = relationship(back_populates="rows")


class BankRule(Base):
    """What a bank line's words were posted to before, by key: an account
    (UBINX1446), a biller's number, a name, the kind of line. Learnt from
    the vouchers already in Tally and from what you approve."""

    __tablename__ = "bank_rules"
    __table_args__ = (UniqueConstraint("company_id", "key", name="uq_bank_rule"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(160))
    ledgers: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # [{"name", "share"}]
    times: Mapped[int] = mapped_column(Integer, default=1)
    last_date: Mapped[Optional[date]] = mapped_column(Date)


class Alias(Base):
    """Learned correction: 'this raw text really means this Tally master'.

    Written every time a reviewer overrides a match. Next identical invoice
    matches at ~0.99 instead of asking again.
    """

    __tablename__ = "aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", "raw_norm", name="uq_alias_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # ledger | stock | item_ledger
    raw_norm: Mapped[str] = mapped_column(String(512), index=True)
    raw_sample: Mapped[Optional[str]] = mapped_column(String(512))
    target_name: Mapped[str] = mapped_column(String(512))
    hits: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Every state change on an entry. An accounting tool that writes to books
    without a trail is not one you can defend in an audit."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(128), default="local")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Setting(Base):
    """Runtime-editable settings (Tally host, allow-post, model name...)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
