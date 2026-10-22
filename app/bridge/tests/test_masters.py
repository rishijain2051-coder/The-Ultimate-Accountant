"""Creating a ledger of any group, or a stock item, from the review screen's
pickers. Run it with:  python tests/test_masters.py

Same style as test_pipeline.py: no pytest, no fixtures, a check(name, condition)
helper and a PASSED list. Uses the fake Tally gateway (tests/fake_tally.py) so
nothing ever reaches a real TallyPrime.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository: app.bridge is a package in it

_tmp = Path(tempfile.mkdtemp(prefix="tallytest-masters-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LLM_ENABLED"] = "false"
os.environ["ASSURANCE_AI_DIR"] = str(_tmp / "ai")   # the app's AI: a folder of its own, never the one on this computer
os.environ["WATCH_ENABLED"] = "false"
os.environ["TALLY_PORT"] = "9"  # nothing listens here until the fake server is started

from app.bridge.config import settings                                       # noqa: E402
from app.bridge.db import SessionLocal, init_db                              # noqa: E402
from app.bridge.models import Company, Document, Entry, EntryLine, Ledger, StockItem  # noqa: E402
from app.bridge.routers import entries as er                                 # noqa: E402
from app.bridge.schemas import CreateMasterRequest                            # noqa: E402
from app.bridge.services import matching, tally                              # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


def seed(db) -> tuple[Company, Entry]:
    # "Saffron Impex" is one of fake_tally's own companies, so the fake
    # server answers requests for it without any extra setup.
    c = Company(tally_name="Saffron Impex", gstin="08ZZZPZ1111Z1Z4", state_code="08",
                voucher_types=[{"name": "Purchase", "parent": "Purchase"}])
    db.add(c)
    db.flush()
    doc = Document(filename="bill.pdf", stored_path=str(_tmp / "bill.pdf"), sha256="b" * 64, status="done")
    db.add(doc)
    db.flush()
    e = Entry(document_id=doc.id, company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase",
              party_name_raw="New Supplier Pvt Ltd", party_gstin="08ZZZPZ1111Z1Z4",
              grand_total=Decimal("1000.00"))
    e.lines = [EntryLine(line_no=0, description_raw="Widget A", quantity=Decimal("1"), uom="Nos",
                        rate=Decimal("500"), amount=Decimal("500"), gst_rate=18.0)]
    db.add(e)
    db.commit()
    return c, e


def test_masters(db, company: Company, entry: Entry) -> None:
    print("\n[creating a ledger of any group, or a stock item, from a picker]")
    from fastapi import HTTPException

    import fake_tally

    srv = fake_tally.serve(9098)
    real_port = settings.tally_port
    settings.tally_port = 9098
    settings.tally_allow_post = True

    sent: list[str] = []
    real_import = tally.import_xml

    def capture(company_name: str, msg: str, request_id: str = "Vouchers"):
        sent.append(msg)
        return real_import(company_name, msg, request_id)

    tally.import_xml = capture
    try:
        # a GST ledger: TAXTYPE GST + its duty head, under Duties & Taxes
        r = er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="ledger", name="IGST", parent="Duties & Taxes", duty_head="IGST"), db)
        check("a GST ledger's XML carries TAXTYPE GST and its duty head",
              "<TAXTYPE>GST</TAXTYPE>" in sent[-1] and "<GSTDUTYHEAD>IGST</GSTDUTYHEAD>" in sent[-1], sent[-1])
        check("... and lands in the local cache at once",
              db.query(Ledger).filter_by(company_id=company.id, tally_name="IGST").count() == 1)
        check("... returned for the picker to select immediately", r["id"] and r["name"] == "IGST", str(r))

        # a purchase ledger: GST on, for goods, affects stock
        er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="ledger", name="Purchase @18% GST", parent="Purchase Accounts"), db)
        check("a purchase ledger's XML turns GST on for goods and affects stock",
              all(s in sent[-1] for s in ("<TAXTYPE>Others</TAXTYPE>",
                                          "<GSTAPPLICABLE>&#4; Applicable</GSTAPPLICABLE>",
                                          "<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>",
                                          "<AFFECTSSTOCK>Yes</AFFECTSSTOCK>")),
              sent[-1])

        # an expense ledger: as the books keep Bank Charges, GST not applicable, not bill-wise
        er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="ledger", name="Bank Charges", parent="Indirect Expenses"), db)
        check("an expense ledger is GST not applicable, with no duty head and not bill-wise",
              "<GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>" in sent[-1]
              and not any(s in sent[-1] for s in ("GSTDUTYHEAD", "ISBILLWISEON")), sent[-1])

        # a party: the original zero-argument call, unchanged
        r4 = er.create_master(CreateMasterRequest(entry_id=entry.id, kind="ledger"), db)
        check("a party ledger's XML still carries bill-wise-on and the bill's GSTIN, as before",
              "<ISBILLWISEON>Yes</ISBILLWISEON>" in sent[-1]
              and "<PARTYGSTIN>08ZZZPZ1111Z1Z4</PARTYGSTIN>" in sent[-1], sent[-1])
        db.refresh(entry)
        check("... and is picked as this bill's party automatically, as before",
              entry.party_ledger_id == r4["id"], f"{entry.party_ledger_id} vs {r4['id']}")

        # a stock item
        r5 = er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="stock", name="Widget A", uom="Nos", hsn="1234", gst_rate=18.0,
            line_id=entry.lines[0].id), db)
        check("a stock item lands in the cache",
              db.query(StockItem).filter_by(company_id=company.id, tally_name="Widget A").count() == 1)
        db.refresh(entry)
        check("... and is picked onto the line that asked for it",
              entry.lines[0].stock_item_id == r5["id"], str(entry.lines[0].stock_item_id))

        # a duplicate name: picked, never created twice
        before_ledgers = db.query(Ledger).filter_by(company_id=company.id, tally_name="Bank Charges").count()
        before_sent = len(sent)
        r6 = er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="ledger", name="Bank Charges", parent="Indirect Expenses"), db)
        check("a ledger name that already exists in the cache is picked, not created again",
              db.query(Ledger).filter_by(company_id=company.id, tally_name="Bank Charges").count() == before_ledgers
              and len(sent) == before_sent and r6["name"] == "Bank Charges",
              f"ledgers now {db.query(Ledger).filter_by(tally_name='Bank Charges').count()}, "
              f"sent now {len(sent)} (was {before_sent})")

        before_items = db.query(StockItem).filter_by(company_id=company.id, tally_name="Widget A").count()
        er.create_master(CreateMasterRequest(
            entry_id=entry.id, kind="stock", name="widget a", line_id=entry.lines[0].id), db)
        check("a stock item name that already exists (case aside) is picked, not created again",
              db.query(StockItem).filter_by(company_id=company.id, tally_name="Widget A").count() == before_items)

        # the write-permission gate
        settings.tally_allow_post = False
        try:
            er.create_master(CreateMasterRequest(
                entry_id=entry.id, kind="ledger", name="Blocked Ledger", parent="Indirect Expenses"), db)
            raise AssertionError("a master was created while 'write to Tally' was off")
        except HTTPException as exc:
            check("creating any master is refused while 'write to Tally' is off",
                  exc.status_code == 403, str(exc.detail))
        check("... and refusing it did not create anything",
              db.query(Ledger).filter_by(company_id=company.id, tally_name="Blocked Ledger").count() == 0)
    finally:
        tally.import_xml = real_import
        settings.tally_port = real_port
        settings.tally_allow_post = False
        srv.shutdown()


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        company, entry = seed(db)
        test_masters(db, company, entry)
    finally:
        db.close()
    print(f"\n{len(PASSED)} checks passed.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)
    sys.exit(code)
