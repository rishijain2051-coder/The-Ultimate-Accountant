"""Bank statements, end to end. Run it with:  python tests/test_bank.py

No pytest, like test_pipeline.py. A statement is made here as a PDF with a
text layer, laid out as Kotak prints one; every name, account and reference
in it is invented. Scanned statements are checked by hand, outside the tests.
A fake Tally stands in for the gateway.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
from datetime import date
from decimal import Decimal as D
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository: app.bridge is a package in it
sys.path.insert(0, str(HERE))

_tmp = Path(tempfile.mkdtemp(prefix="tallybank-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LLM_ENABLED"] = "false"
os.environ["ASSURANCE_AI_DIR"] = str(_tmp / "ai")   # the app's AI: a folder of its own, never the one on this computer
os.environ["WATCH_ENABLED"] = "false"
os.environ["TALLY_PORT"] = "9"

from app.bridge.config import settings                                  # noqa: E402
from app.bridge.db import SessionLocal, init_db                         # noqa: E402
from app.bridge.models import Company, Document, Ledger                 # noqa: E402
from app.bridge.services import bank_match as M, statement as S, voucher_xml  # noqa: E402

PASSED: list[str] = []
BANK = "KOTAK MAHIND BANK OD A/C (1234567890)"


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


# The statement: (date, details, second line, ref, amount, balance), from an
# opening balance of 10,000.00.
ROWS = [
    ("01 Aug 2026", "SentIMPS600000000101RAVINDRA P/IDIBX7193/", "KKBKTrans", "IMPS-600000000102", "-5,000.00", "5,000.00"),
    ("02 Aug 2026", "CMS CHARGES", "", "", "-80.00", "4,920.00"),
    ("02 Aug 2026", "CMS GST", "", "", "-14.40", "4,905.60"),
    ("03 Aug 2026", "Recd:IMPS/600000000103/VIJAY ANAND/KKBK/", "X1234/", "IMPS-600000000103", "2,000.00", "6,905.60"),
    ("04 Aug 2026", "IMPS-RTN-ACCOUNTCLOSED 600000000101", "", "", "5,000.00", "11,905.60"),
]


def statement_pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str) -> None:
        if text:
            pg.insert_text((x, y), text, fontsize=7, fontname="helv")

    put(30, 40, "Kotak Mahindra Bank")
    put(30, 55, "Account No. 1234567890")
    put(30, 70, "01 Aug 2026 - 31 Aug 2026")
    put(30, 85, "Opening Balance 10,000.00")
    for x, h in ((30, "#"), (45, "TRANSACTION DATE"), (115, "VALUE DATE"), (185, "TRANSACTION DETAILS"),
                 (380, "CHQ / REF NO."), (455, "DEBIT/CREDIT"), (525, "BALANCE")):
        put(x, 110, h)
    y = 130
    for n, (d, det, det2, ref, amt, bal) in enumerate(ROWS, start=1):
        put(30, y, str(n)); put(45, y, d); put(115, y, d); put(185, y, det); put(380, y, ref)
        put(455, y, amt); put(525, y, bal)
        put(45, y + 9, "08:55 PM"); put(185, y + 9, det2)
        y += 26
    put(30, 800, "Page 1 of 1")
    return doc.tobytes()


# --------------------------------------------------------------------------
def test_reader() -> None:
    print("\n[reading a statement]")
    path = _tmp / "statement.pdf"
    path.write_bytes(statement_pdf())
    st = S.read_file(path)
    check("every line of the statement is a row, in its order", len(st.rows) == 5, str(len(st.rows)))
    check("the account, bank, period and opening balance are read",
          (st.account_no, st.bank, st.period, st.opening)
          == ("1234567890", "Kotak Mahindra Bank", (date(2026, 8, 1), date(2026, 8, 31)), D("10000.00")),
          f"{st.account_no} {st.bank} {st.period} {st.opening}")
    r = st.rows[0]
    check("a row's dates, number, reference, amount and balance land in their columns",
          (r.serial, r.date, r.value_date, r.ref, r.amount, r.balance)
          == ("1", date(2026, 8, 1), date(2026, 8, 1), "IMPS-600000000102", D("-5000.00"), D("5000.00")),
          f"{r.serial} {r.date} {r.ref} {r.amount} {r.balance}")
    check("details wrapped over two lines read as one", r.details == "SentIMPS600000000101RAVINDRA P/IDIBX7193/KKBKTrans",
          r.details)
    check("money in is plus, money out minus", [x.amount for x in st.rows]
          == [D("-5000.00"), D("-80.00"), D("-14.40"), D("2000.00"), D("5000.00")])
    check("the running balance agrees on every row", all(x.check == "ok" for x in st.rows),
          str([x.check for x in st.rows]))
    check("each row knows where it is on its page", all(0 < x.y0 < x.y1 < 1 for x in st.rows))

    # What a scan gets wrong, the balances put right, and say so.
    def row(amount, balance, signed=True):
        return S.Row(date=date(2026, 8, 1), amount=None if amount is None else D(amount),
                     balance=D(balance), signed=signed)
    st = S.Statement(rows=[row("-100.00", "900.00"), row("50.00", "850.00"), row(None, "800.00"),
                           row("-10.00", "790.00"), row("-99.00", "700.00"), row("-5.00", "600.00")],
                     opening=D("1000.00"))
    S.settle(st)
    r = st.rows
    check("a sign read wrong is set by the balance, with a note",
          r[1].amount == D("-50.00") and r[1].check == "fixed" and r[1].notes, str(r[1]))
    check("an amount not read is worked out from the balances",
          r[2].amount == D("-50.00") and r[2].check == "fixed", str(r[2]))
    check("a row the balances do not agree with is marked to check", r[4].check == "bad" or r[5].check == "bad",
          str([x.check for x in r]))
    check("a sign not printed is simply taken from the balance",
          (lambda s: (S.settle(s), s.rows[0].check)[1])(S.Statement(rows=[row("100.00", "900.00", signed=False)],
                                                                   opening=D("1000.00"))) == "ok")


def test_words() -> None:
    print("\n[a line's words: who, which account, which kind]")
    check("the payee is read however the OCR spelt Sent IMPS",
          [M.counterparty(x) for x in ("SentIMPS600000000104Hari Prasa/SBINX5240/groceri",
                                        "Sent/MPS600000000104Hari Prasa/SBINX5240/groceri",
                                        "SentlMPS600000000105Tarun Ghos/SBINX0386/KKBKTra")]
          == ["Hari Prasa", "Hari Prasa", "Tarun Ghos"])
    check("a masked account is a key, its OCR slips put right",
          M.identifiers("SentIMPS600000000101RAVINDRA P/IDIBX7I93/KKBKTrans") == ["acct:IDIBX7193"])
    check("a biller's number is a key, its name is not (it has many numbers)",
          M.keys("J41PURVIDY0987654321") == ["num:0987654321"])
    check("the sender's own remark is kept, the bank's default one is not",
          (M.remark("SentIMPS600000000106NIKHIL VER/BARBX3125/rentjune"),
           M.remark("UPI/RAKESHMEENA/600000000107/PaidViaKotakApp")) == ("rentjune", ""))
    check("the bank's charges, their GST and a return are told apart",
          [M.kind(x) for x in ("CMS CHARGES", "CMS GST", "IMPS-RTN-ACCOUNTCLOSED 1", "NACH-MUT-DR-SBIMF20082026")]
          == ["charges", "gst_charges", "return", "mutual_fund"])
    check("a name the bank cut short is like the ledger it starts",
          M.like("SAILESH CH", "SHAILESH CHANDRA VERMA (CONTRACTOR)") >= 88)
    check("but a first name alone is not taken for a longer one", M.like("MAN0J SETH", "MANOJ") < 88)
    check("a GSTIN and & in a ledger's name do not get in the way",
          M.like("KESHAVENTERPRISESMOTILA", "Keshav Enterprises (08TTTPT7777T1ZA) (MOTILAL)") >= 95
          and M.like("SURYAARTANDCRAFT", "Surya Art & Craft") >= 95)
    rows = [{"i": n, "details": d, "ref": ref, "amount": D(a.replace(",", ""))}
            for n, (_d, d, _x, ref, a, _b) in enumerate(ROWS, start=1)]
    check("a returned transfer is known by its number: the payment it gives back", M.returns(rows) == {5: 1},
          str(M.returns(rows)))


def test_reconcile() -> None:
    print("\n[which lines are in Tally already]")
    rows = [{"i": 1, "date": date(2026, 8, 11), "amount": D("-75000.00")},
            {"i": 2, "date": date(2026, 8, 11), "amount": D("-80.00")},
            {"i": 3, "date": date(2026, 8, 11), "amount": D("-14.40")},
            {"i": 4, "date": date(2026, 8, 11), "amount": D("-500.00")},
            {"i": 5, "date": date(2026, 8, 11), "amount": D("-500.00")}]
    joined = {"date": date(2026, 8, 11), "type": "Payment", "number": "469", "amount": D("-75094.40"),
              "others": [("ORION INTERNATIONAL", D("75000.00")), ("Bank Charges", D("80.00")),
                         ("CGST", D("7.20")), ("SGST", D("7.20"))]}
    one = {"date": date(2026, 8, 12), "type": "Payment", "number": "470", "amount": D("-500.00"),
           "others": [("Cash", D("500.00"))]}
    found = M.reconcile(rows, [joined, one])
    check("a voucher joining a payment, its charges and their GST covers the three lines",
          {i: found[i]["number"] for i in (1, 2, 3)} == {1: "469", 2: "469", 3: "469"} and found[3]["part"],
          str(found))
    check("of two lines of the same amount, one voucher is for one of them only",
          sum(1 for i in (4, 5) if i in found) == 1)


def test_voucher() -> None:
    print("\n[the voucher, as the books keep a bank line]")
    xml = voucher_xml.bank_voucher(
        "INDIGO HANDICRAFTS", "Payment", date(2026, 8, 11), BANK, D("-75094.40"),
        [("ORION INTERNATIONAL", D("75000.00"), True), ("Bank Charges", D("80.00"), False),
         ("CGST", D("7.20"), False), ("SGST", D("7.20"), False)],
        "SentIMPS… IMPS-622…", gst_registration="Rajasthan Registration", company_gstin="08YYYPY2222Y1Z5",
        company_state="Rajasthan")
    rows = re.findall(r"<ALLLEDGERENTRIES.LIST>(.*?)</ALLLEDGERENTRIES.LIST>", xml, re.S)
    got = [(re.search(r"<LEDGERNAME>(.*?)<", r).group(1), re.search(r"<AMOUNT>(.*?)<", r).group(1)) for r in rows]
    check("a payment: what it went to Dr, the bank Cr, the bank last",
          got == [("ORION INTERNATIONAL", "-75000.00"), ("Bank Charges", "-80.00"), ("CGST", "-7.20"),
                  ("SGST", "-7.20"), (BANK, "75094.40")], str(got))
    check("a bill-wise party's amount goes on account, other ledgers carry no bills",
          "On Account" in rows[0] and not any("BILLALLOCATIONS" in r for r in rows[1:]))
    check("the bank's row carries the bank details Tally keeps, in favour of the party",
          "<BANKALLOCATIONS.LIST>" in rows[-1] and "<PAYMENTFAVOURING>ORION INTERNATIONAL<" in rows[-1]
          and "<TRANSACTIONTYPE>Cheque<" in rows[-1] and "<AMOUNT>75094.40</AMOUNT>" in rows[-1])
    check("numbered by Tally, the party the first ledger, the GST registration named",
          "<VOUCHERNUMBER>" not in xml and "<PARTYLEDGERNAME>ORION INTERNATIONAL<" in xml
          and "<GSTREGISTRATION>Rajasthan Registration<" in xml and 'VCHTYPE="Payment"' in xml)
    xml = voucher_xml.bank_voucher("C", "Receipt", date(2026, 8, 3), BANK, D("990.00"),
                                   [("VIJAY ANAND", D("1000.00"), False), ("Bank Charges", D("-10.00"), False)])
    got = [(re.search(r"<LEDGERNAME>(.*?)<", r).group(1), re.search(r"<AMOUNT>(.*?)<", r).group(1))
           for r in re.findall(r"<ALLLEDGERENTRIES.LIST>(.*?)</ALLLEDGERENTRIES.LIST>", xml, re.S)]
    check("a receipt net of charges: the party Cr, the charges Dr, the bank Dr what came in",
          got == [("VIJAY ANAND", "1000.00"), ("Bank Charges", "-10.00"), (BANK, "-990.00")], str(got))
    try:
        voucher_xml.bank_voucher("C", "Payment", date(2026, 8, 3), BANK, D("-100.00"), [("Cash", D("90.00"), False)])
        refused = False
    except voucher_xml.VoucherImbalance:
        refused = True
    check("ledgers that do not add up to the line are never sent", refused)


def test_screen(db) -> None:
    print("\n[a statement through the app, posted to a fake Tally]")
    import fake_tally
    from fastapi.testclient import TestClient

    from app.bridge.main import app as api
    from app.bridge.routers import bank, documents
    from app.bridge.routers.tally_api import discover, sync_masters

    impex = fake_tally.IMPEX
    fake_tally.LEDGERS[impex] += [(BANK, "Bank OD A/c", ""), ("RAVINDRA PAL (CONTRACTER)", "Sundry Creditors", ""),
                                  ("RAVINDRA PAL (CONTRACTER) (TDS APPLICABLE)", "Sundry Creditors", ""),
                                  ("Bank Charges", "Indirect Expenses", ""), ("CGST", "Duties & Taxes", ""),
                                  ("SGST", "Duties & Taxes", ""), ("VIJAY ANAND", "Sundry Creditors", "")]
    srv = fake_tally.serve(9089)
    real = settings.tally_port, settings.tally_allow_post
    settings.tally_port = 9089
    try:
        discover(db=db)
        company = db.query(Company).filter_by(tally_name=impex).one()
        sync_masters(company.id, db=db)
        ravindra = db.query(Ledger).filter_by(company_id=company.id, tally_name="RAVINDRA PAL (CONTRACTER)").one()
        ravindra.bill_wise = True  # the fake does not say; a real sync does
        db.commit()

        client = TestClient(api)
        up = client.post("/api/bank/statements", files={"file": ("aug.pdf", statement_pdf(), "application/pdf")}).json()
        for _ in range(100):
            listed = client.get("/api/bank/statements").json()
            if listed and listed[0]["status"] in ("extracted", "failed"):
                break
            time.sleep(0.1)
        check("a statement is read in the background", listed[0]["status"] == "extracted", str(listed[0]))
        sid = listed[0]["id"]
        st = client.get(f"/api/bank/statements/{sid}").json()
        check("its account finds its company and bank ledger by number",
              (st["company"], st["bank_ledger"]) == (impex, BANK), f"{st['company']} / {st['bank_ledger']}")
        check("the totals are the statement's", (st["debits"], st["credits"], st["closing"])
              == ("5094.40", "7000.00", "11905.60"), f"{st['debits']} {st['credits']} {st['closing']}")
        rows = st["rows"]
        names = [[x["name"] for x in r["ledgers"]] for r in rows]
        check("each row is suggested its ledgers: the payee (not its TDS variant), the bank's charges, "
              "their GST in halves, the payer", names[:4] == [["RAVINDRA PAL (CONTRACTER)"], ["Bank Charges"],
                                                             ["CGST", "SGST"], ["VIJAY ANAND"]], str(names))
        check("the GST on the charges is split to the paisa",
              [x["amount"] for x in rows[2]["ledgers"]] == ["7.20", "7.20"], str(rows[2]["ledgers"]))
        check("a returned transfer goes back to what its payment went to",
              names[4] == ["RAVINDRA PAL (CONTRACTER)"] and "return of row 1" in rows[4]["reason"], rows[4]["reason"])
        check("a row the app is not sure of says so; nothing is posted without you",
              all(r["status"] == "pending" for r in rows) and rows[0]["confidence"] < 0.9)
        check("a statement is not a bill: not in the bills' list, nor read as one after a restart",
              all(d["id"] != up["document_id"] for d in client.get("/api/documents").json())
              and up["document_id"] not in [i for i, _c in documents.unfinished()])

        settings.tally_allow_post = False
        r = client.post(f"/api/bank/rows/{rows[0]['id']}/post")
        check("posting needs 'Allow this app to write to Tally'", r.status_code == 403)
        settings.tally_allow_post = True

        before = len(fake_tally.POSTED[impex])
        r = client.post(f"/api/bank/rows/{rows[0]['id']}/post").json()
        sent = fake_tally.POSTED[impex][-1]
        check("an approved row is one voucher in Tally: the payee Dr, the bank Cr",
              r["result"]["ok"] and len(fake_tally.POSTED[impex]) == before + 1 and sent["type"] == "Payment"
              and sent["rows"] == [("RAVINDRA PAL (CONTRACTER)", "-5000.00"), (BANK, "5000.00")], str(r["result"]))
        r = client.post(f"/api/bank/rows/{rows[0]['id']}/post").json()
        check("a posted row is never posted twice", not r["result"]["ok"] and len(fake_tally.POSTED[impex]) == before + 1)

        # Row 4 keyed in by hand meanwhile: found, not posted again.
        fake_tally.POSTED[impex].append({"date": "20260803", "number": "88", "type": "Receipt", "party": "VIJAY ANAND",
                                         "reference": "", "amount": "2000.00", "optional": "No",
                                         "rows": [("VIJAY ANAND", "2000.00"), (BANK, "-2000.00")]})
        r = client.post(f"/api/bank/rows/{rows[3]['id']}/post").json()
        check("a line keyed in Tally by hand since is found at posting, and not posted again",
              not r["result"]["ok"] and r["statement"]["rows"][3]["status"] == "in_tally", r["result"]["message"])
        st = client.post(f"/api/bank/statements/{sid}/check").json()
        check("looking in Tally again keeps what the app posted as posted",
              [x["status"] for x in st["rows"]][:4] == ["posted", "pending", "pending", "in_tally"],
              str([x["status"] for x in st["rows"]]))

        # You choose a ledger: the row is yours, and what is like it follows.
        charges = db.query(Ledger).filter_by(company_id=company.id, tally_name="Bank Charges").one()
        st = client.patch(f"/api/bank/rows/{rows[1]['id']}", json={"ledgers": [
            {"ledger_id": charges.id, "amount": "80.00"}], "narration": "Charges for August"}).json()
        check("a ledger you choose is kept as yours", st["rows"][1]["reason"] == "you chose it"
              and st["rows"][1]["narration"] == "Charges for August")
        r = client.patch(f"/api/bank/rows/{rows[1]['id']}", json={"ledgers": [
            {"ledger_id": charges.id, "amount": "70.00"}]})
        r = client.post(f"/api/bank/statements/{sid}/post", json={"row_ids": [rows[1]["id"], rows[2]["id"]]}).json()
        check("ticked rows post one voucher each; one whose ledgers do not add up is refused, the rest go",
              [x["ok"] for x in r["results"]] == [False, True] and r["posted"] == 1, str(r["results"]))
        st = client.patch(f"/api/bank/rows/{rows[4]['id']}", json={"skip": True}).json()
        check("a row can be left out", st["rows"][4]["status"] == "skipped" and st["by_status"]["skipped"] == 1)
        made = client.post(f"/api/bank/statements/{sid}/ledgers",
                           json={"name": "RAKESH MEENA (WAGES)", "parent": "Sundry Creditors"}).json()
        again = client.post(f"/api/bank/statements/{sid}/ledgers", json={"name": "Rakesh Meena (Wages)"}).json()
        check("a payee with no ledger gets one made in Tally from the row, and asking twice makes one",
              made["id"] == again["id"] and ("RAKESH MEENA (WAGES)", "Sundry Creditors", "")
              in fake_tally.LEDGERS[impex] and db.get(Ledger, made["id"]).is_party, str(made))
        check("what you post teaches the next statement: the payee's account is known now",
              M.suggest(db, company.id, "SentIMPS699999999999SOMEONE/IDIBX7193/KKBKTrans", "", D("-10.00"), BANK)
              ["ledgers"][0]["name"] == "RAVINDRA PAL (CONTRACTER)")
        waiting = Document(sha256="x", filename="s.pdf", stored_path="s.pdf", kind="statement", status="queued")
        db.add(waiting)
        db.commit()
        check("a statement left half-read is read again after a restart, by the bank's queue",
              waiting.id in bank.unfinished() and waiting.id not in [i for i, _c in documents.unfinished()])
    finally:
        settings.tally_port, settings.tally_allow_post = real
        srv.shutdown()


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        test_reader()
        test_words()
        test_reconcile()
        test_voucher()
        test_screen(db)
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
