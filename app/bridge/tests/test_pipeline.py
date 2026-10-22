"""End-to-end self-check. Run it with:  python tests/test_pipeline.py

No pytest, no fixtures. Every assert here fails loudly if the logic that
decides what gets posted to someone's books changes behaviour.

Runs against the synthetic sample invoice in tests/fixtures (tools/synth_invoice.py), with Tally and
Ollama both absent -- which is also the worst case the app has to survive.
Where a test needs Tally to answer, a fake stands in for the gateway.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository: app.bridge is a package in it

_tmp = Path(tempfile.mkdtemp(prefix="tallytest-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LLM_ENABLED"] = "false"       # rules only: the model must not be needed
os.environ["ASSURANCE_AI_DIR"] = str(_tmp / "ai")   # the app's AI: a folder of its own, never the one on this computer
os.environ["WATCH_ENABLED"] = "false"
os.environ["TALLY_PORT"] = "9"            # nothing listens here: Tally is "closed"

from app.bridge.db import SessionLocal, init_db                              # noqa: E402
from app.bridge.models import Company, Document, Entry, Ledger               # noqa: E402
from app.bridge.schemas import ApproveRequest                                # noqa: E402
from app.bridge.config import settings                                       # noqa: E402
from app.bridge.services import ai_check, dedupe, llm, matching, pipeline, tally, validate, voucher_xml  # noqa: E402
from app.bridge.services.gst import gstin_checksum_ok                        # noqa: E402
from app.bridge.services.parse import parse, to_date, to_decimal             # noqa: E402
from app.bridge.services.pdf_text import extract                             # noqa: E402

FIXTURE = HERE / "fixtures" / "kestrel_audit_igst.pdf"
PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


def sides(xml: str) -> list[tuple[str, str, Decimal]]:
    out = []
    # An invoice's rows are in LEDGERENTRIES, an accounting voucher's in ALLLEDGERENTRIES.
    for blk in re.findall(r"<(?:ALL)?LEDGERENTRIES.LIST>(.*?)</(?:ALL)?LEDGERENTRIES.LIST>", xml, re.S):
        led = re.search(r"<LEDGERNAME>(.*?)</LEDGERNAME>", blk).group(1)
        amt = Decimal(re.search(r"<AMOUNT>(-?[\d.]+)</AMOUNT>", blk).group(1))
        out.append(("Dr" if amt < 0 else "Cr", led, abs(amt)))
    return out


# --------------------------------------------------------------------------
def test_primitives() -> None:
    print("\n[primitives]")
    check("GSTIN checksum accepts real GSTINs",
          gstin_checksum_ok("09XXXCX3333X1ZV") and gstin_checksum_ok("08ZZZPZ1111Z1Z4"))
    check("GSTIN checksum rejects a tampered check digit", not gstin_checksum_ok("08ZZZPZ1111Z1ZZ"))
    check("dates are read day-first", to_date("04/05/2026").isoformat() == "2026-05-04",
          f"got {to_date('04/05/2026')}")
    check("unambiguous month-first still parses", to_date("05/18/2026").isoformat() == "2026-05-18")
    check("Indian digit grouping parses", to_decimal("1,43,414.00") == Decimal("143414.00"))
    check("bracketed negatives parse", to_decimal("(1,234.00)") == Decimal("-1234.00"))
    check("invoice numbers normalise across punctuation",
          dedupe.norm_invoice_no("5500100/26004852") == dedupe.norm_invoice_no("5500100-26004852"))
    check("leading zeros are ignored in invoice numbers",
          dedupe.norm_invoice_no("INV-0042") == dedupe.norm_invoice_no("inv 42"))


def test_limits() -> None:
    from types import SimpleNamespace
    from app.bridge.services import pdf_text, statement
    t = time.time()
    check("money skips a huge digit run", statement.money("9" * 100_000 + " 1,234.50") == [(Decimal("1234.50"), False)]
          and time.time() - t < 2)
    big = SimpleNamespace(rect=SimpleNamespace(width=50_000, height=50_000))
    z = pdf_text.fit_zoom(big, 2.0)
    check("huge page zoom is capped", 50_000 * 50_000 * z * z <= pdf_text.MAX_PIXELS * 1.001)
    check("normal page zoom is kept", pdf_text.fit_zoom(SimpleNamespace(rect=SimpleNamespace(width=595, height=842)), 2.0) == 2.0)


def test_parse():
    print("\n[parsing the sample invoice]")
    res = extract(FIXTURE)
    check("clear PDF uses its text layer, not OCR", res.has_text_layer and not res.ocr_used)

    p = parse(res.text, own_gstins={"08ZZZPZ1111Z1Z4"})
    check("invoice number", p.get("invoice_no") == "5500100-26004852", str(p.get("invoice_no")))
    check("invoice date", str(p.get("invoice_date")) == "2026-08-18", str(p.get("invoice_date")))
    check("due date", str(p.get("due_date")) == "2026-09-17")
    check("supplier GSTIN", p.get("party_gstin") == "09XXXCX3333X1ZV")
    check("own GSTIN identified", p.get("own_gstin") == "08ZZZPZ1111Z1Z4")
    check("supplier name", "KESTREL AUDIT" in (p.get("party_name") or "").upper(), str(p.get("party_name")))
    check("direction inferred as Purchase", p.get("voucher_type") == "Purchase")
    check("place of supply", p.get("place_of_supply") == "08")
    check("taxable value", p.get("taxable_total") == Decimal("143414.00"))
    check("IGST amount", p.get("igst") == Decimal("25814.52"), str(p.get("igst")))
    check("IGST rate", p.get("igst_rate") == 18.0)
    check("grand total", p.get("grand_total") == Decimal("169228.52"))
    check("no CGST/SGST on an inter-state invoice", not p.get("cgst") and not p.get("sgst"))
    check("four line items", len(p.lines) == 4, f"got {len(p.lines)}")
    check("every line's qty x rate equals its amount", all(l["arith_ok"] for l in p.lines))
    check("lines sum to the taxable value", sum(l["amount"] for l in p.lines) == Decimal("143414.00"))
    check("page-2 header did not leak into a description",
          not any("Page no" in (l["description_raw"] or "") for l in p.lines))
    check("every field records where it came from",
          all(f.evidence for k, f in p.fields.items() if k in ("invoice_no", "igst", "grand_total")))
    return p


def test_validations(p) -> None:
    print("\n[validation]")
    checks = validate.run(p, own_state="08")
    codes = {c["code"]: c for c in checks}
    check("tax split verified as IGST", codes["tax_split"]["severity"] == "ok", str(codes.get("tax_split")))
    check("IGST arithmetic verified", codes["igst_math"]["severity"] == "ok")
    check("line items reconcile to the taxable value", codes["line_sum"]["severity"] == "ok")
    check("grand total reconciles", codes["total"]["severity"] == "ok")
    check("a correct invoice raises no errors", validate.worst(checks) == "ok",
          str([c for c in checks if c["severity"] != "ok"]))

    # Break it on purpose: same supplier state as the buyer means the tax
    # should have been CGST+SGST, and the app has to say so.
    bad = copy.deepcopy(p)
    bad.put("party_gstin", "08ZZZPZ1111Z1Z4", 1.0, "test")
    split = next(c for c in validate.run(bad, own_state="08") if c["code"] == "tax_split")
    check("intra-state invoice charging IGST is flagged as an error", split["severity"] == "error",
          split["message"])

    pay = validate.run(p, own_state="08", invoice_like=False)
    check("a payment is not held to GST rules",
          not any(c["code"].startswith(("tax_", "igst", "cgst", "line_sum")) for c in pay),
          str([c["code"] for c in pay]))


def seed(db) -> Company:
    c = Company(tally_name="Saffron Impex", gstin="08ZZZPZ1111Z1Z4", state_code="08",
                voucher_types=[{"name": "Purchase", "parent": "Purchase"},
                               {"name": "GST Purchase - Services", "parent": "Purchase"},
                               {"name": "Payment", "parent": "Payment"}])
    db.add(c)
    db.flush()
    # Deliberately awkward: the ledger name does not equal the invoice name.
    for name, parent in [
        ("Kestrel Audit (FSC)", "Sundry Creditors"),
        ("Kestrel Audit Certification", "Sundry Creditors"),
        ("Saffron Traders", "Sundry Creditors"),
        ("Input IGST", "Duties & Taxes"),
        ("Input CGST", "Duties & Taxes"),
        ("Certification & Audit Fees", "Indirect Expenses"),
        ("Purchase - Services", "Purchase Accounts"),
        ("Round Off", "Indirect Expenses"),
        ("HDFC Bank", "Bank Accounts"),
    ]:
        db.add(Ledger(company_id=c.id, tally_name=name, parent=parent,
                      is_party="Creditors" in parent, norm_name=matching.normalize(name)))
    db.commit()
    return c


def test_matching(db, company) -> None:
    print("\n[ledger matching]")
    m = matching.match_ledger(db, company.id, "KESTREL AUDIT (INDIA) PRIVATE LIMITED", None,
                              parent_like="Creditors")
    check("invoice name matches a differently-named ledger",
          m["ledger_id"] is not None and "Kestrel Audit" in m["name"], str(m))
    check("a fuzzy-only match never reads as certain", m["confidence"] < 0.95, str(m["confidence"]))
    check("the near-tie is surfaced, not hidden",
          len(m["candidates"]) >= 2 and "almost the same" in m["reason"], str(m["reason"]))
    print(f"      -> matched '{m['name']}' at {m['confidence']:.0%}: {m['reason']}")

    led = db.query(Ledger).filter_by(tally_name="Kestrel Audit (FSC)").one()
    led.gstin = "09XXXCX3333X1ZV"
    db.commit()
    m2 = matching.match_ledger(db, company.id, "SOMETHING ELSE ENTIRELY", "09XXXCX3333X1ZV")
    check("a GSTIN match wins outright", m2["confidence"] == 1.0 and m2["name"] == "Kestrel Audit (FSC)",
          str(m2))

    matching.remember(db, company.id, "ledger", "ACME WIDGETS LLP", "Saffron Traders")
    db.commit()
    m3 = matching.match_ledger(db, company.id, "acme widgets llp", None)
    check("a correction you made is applied next time",
          m3["name"] == "Saffron Traders" and m3["confidence"] >= 0.99, str(m3))


def _ingest(db, voucher_type=None, parent=None) -> Entry:
    stored, digest = pipeline.adopt_file(FIXTURE)
    doc = Document(filename=FIXTURE.name, stored_path=str(stored), sha256=digest,
                   size_bytes=FIXTURE.stat().st_size, source="upload",
                   requested_voucher_type=voucher_type, requested_voucher_parent=parent)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return pipeline.process_document(db, doc, use_llm=False)


def test_ingest(db, company) -> Entry:
    print("\n[full pipeline, voucher type chosen at upload]")
    e = _ingest(db, "GST Purchase - Services")
    check("the chosen voucher type is kept", e.voucher_type == "GST Purchase - Services")
    check("a custom type resolves to its Tally parent", e.voucher_parent == "Purchase", str(e.voucher_parent))
    check("entry is attached to the right company", e.company_id == company.id)
    check("entry totals carried through", e.grand_total == Decimal("169228.52"))
    check("party matched by GSTIN", e.party_ledger_id is not None and e.party_confidence == 1.0)
    check("four lines stored", len(e.lines) == 4)
    check("status is pending, not auto-posted", e.status == "pending")

    dup = (e.field_meta.get("_duplicates") or {}).get("value") or {}
    check("with Tally closed, the Tally check says 'not checked' instead of 'clean'",
          dup.get("tally_checked") is False and dup.get("tally_error"), str(dup))

    unmatched = [l for l in e.lines if not l.ledger_id and not l.stock_item_id]
    check("descriptions that match nothing are left unmatched, not guessed",
          len(unmatched) == 2, str([(l.description_raw[:28], l.ledger_id) for l in e.lines]))
    check("an unmatched line says so in plain words",
          all("no matching" in (l.match_reason or "") for l in unmatched))

    from app.bridge.routers.entries import _blockers
    check("unmatched lines block approval",
          any("line(s) have no Tally ledger" in b for b in _blockers(db, e, ApproveRequest())))

    target = db.query(Ledger).filter_by(tally_name="Certification & Audit Fees").one()
    matching.remember(db, company.id, "ledger", unmatched[0].description_raw, target.tally_name)
    db.commit()
    again = matching.match_ledger(db, company.id, unmatched[0].description_raw, None)
    check("mapping a line once teaches it permanently",
          again["ledger_id"] == target.id and again["confidence"] >= 0.99, str(again))

    for l in e.lines:
        l.ledger_id, l.stock_item_id = target.id, None
    db.commit()
    check("a clean, fully-mapped entry has nothing blocking it",
          _blockers(db, e, ApproveRequest()) == [], str(_blockers(db, e, ApproveRequest())))
    print(f"      -> confidence {e.confidence:.0%}, party '{e.party_ledger.tally_name}'")
    return e


def test_payment_shape(db, company) -> None:
    print("\n[the same bill uploaded as a Payment]")
    e = _ingest(db, "Payment")
    check("a payment carries no GST lines", not e.igst and not e.cgst and not e.taxable_total)
    check("a payment has one line: the bank", len(e.lines) == 1)
    check("the only bank ledger is offered automatically",
          e.lines[0].ledger_id is not None and "bank" in (e.lines[0].match_reason or "").lower(),
          str(e.lines[0].match_reason))
    check("no GST checks on a payment",
          not any(v["code"] in ("tax_split", "igst_math", "line_sum") for v in e.validations))


def test_local_duplicates(db, company, first: Entry) -> None:
    print("\n[duplicates in this app]")
    check("the identical file is caught by hash", dedupe.check_file(db, first.document.sha256) is not None)

    clone = Entry(document_id=first.document_id, company_id=company.id, voucher_type="Purchase",
                  invoice_no="5500100/26004852", party_gstin="09XXXCX3333X1ZV",
                  invoice_date=first.invoice_date, grand_total=first.grand_total)
    db.add(clone)
    db.flush()
    hits = dedupe.check_local(db, clone)
    check("same supplier + same invoice number is caught despite punctuation",
          any(h["entry_id"] == first.id and h["kind"] == "exact" for h in hits), str(hits))

    other = Entry(document_id=first.document_id, company_id=company.id, voucher_type="Purchase",
                  invoice_no="ENTIRELY-DIFFERENT-9", party_gstin="09XXXCX3333X1ZV",
                  invoice_date=first.invoice_date, grand_total=Decimal("11.00"))
    db.add(other)
    db.flush()
    check("an unrelated invoice is not flagged", dedupe.check_local(db, other) == [])
    db.rollback()


def test_tally_duplicates(db, entry: Entry) -> None:
    print("\n[duplicates inside Tally]")
    real = tally.vouchers_in_period
    party = "Kestrel Audit (FSC)"

    def fake(vouchers):
        return lambda company, start, end: vouchers

    try:
        tally.vouchers_in_period = fake([{
            "date": date(2026, 8, 20), "voucher_number": "P-118", "voucher_type": "Journal",
            "party": party, "party_gstin": None, "reference": "5500100/26004852", "bill_refs": [],
            "amount": Decimal("169228.52"), "narration": "", "master_id": "991", "is_optional": False,
        }])
        t = dedupe.check_tally(entry, "Saffron Impex", party, fresh=True)
        check("a bill keyed into Tally by hand is found", t["checked"] and t["findings"], str(t))
        check("... even entered as a different voucher type",
              t["findings"][0]["kind"] == "exact" and "Journal" in t["findings"][0]["reason"],
              t["findings"][0]["reason"])

        tally.vouchers_in_period = fake([{
            "date": date(2026, 8, 19), "voucher_number": "P-120", "voucher_type": "Purchase",
            "party": party, "party_gstin": None, "reference": "BV-OTHER-77", "bill_refs": [],
            "amount": Decimal("169228.52"), "narration": "", "master_id": "992", "is_optional": False,
        }])
        t2 = dedupe.check_tally(entry, "Saffron Impex", party, fresh=True)
        check("same party + same amount + different number is only 'similar'",
              t2["findings"] and t2["findings"][0]["kind"] == "similar", str(t2["findings"]))
        v = dedupe.as_validations(t2["findings"])
        check("a similar bill is a warning, not an error", v[0]["severity"] == "warn")

        # Approval: similar needs nothing; exact needs one acknowledgement.
        from app.bridge.routers.entries import _blockers
        pipeline.apply_duplicates(entry, t2["findings"], t2)
        check("a similar bill does not block approval", _blockers(db, entry, ApproveRequest()) == [],
              str(_blockers(db, entry, ApproveRequest())))
        pipeline.apply_duplicates(entry, t["findings"], t)
        check("an exact duplicate asks for acknowledgement",
              any("duplicate" in b.lower() for b in _blockers(db, entry, ApproveRequest())))
        check("... and posting anyway is allowed once acknowledged",
              _blockers(db, entry, ApproveRequest(override_duplicate=True)) == [])

        tally.vouchers_in_period = fake([{
            "date": date(2026, 8, 18), "voucher_number": "S-9", "voucher_type": "Sales",
            "party": "Someone Else", "party_gstin": None, "reference": "XYZ", "bill_refs": [],
            "amount": Decimal("500.00"), "narration": "", "master_id": "3", "is_optional": False,
        }])
        check("unrelated vouchers in Tally are ignored",
              dedupe.check_tally(entry, "Saffron Impex", party, fresh=True)["findings"] == [])

        # A batch with Tally closed must not wait ~2s per document.
        dedupe._tally_down_until = __import__("time").monotonic() + 30
        skipped = dedupe.check_tally(entry, "Saffron Impex", party)
        check("background checks skip ahead while Tally was just unreachable",
              not skipped["checked"] and "moment ago" in skipped["error"])
        check("... but a check you asked for always goes to Tally",
              dedupe.check_tally(entry, "Saffron Impex", party, fresh=True)["checked"])
        dedupe._tally_down_until = 0.0
    finally:
        tally.vouchers_in_period = real
        pipeline.apply_duplicates(entry, [], {"checked": False})
        db.rollback()


def test_voucher_types() -> None:
    print("\n[every voucher type posts to the right side]")
    base = dict(company="X", date=date(2026, 8, 18), invoice_no="INV-1")
    cases = [
        ("Purchase", "Supplier", "Expense", ("Input IGST", Decimal("18")),
         {"Supplier": "Cr", "Expense": "Dr", "Input IGST": "Dr"}),
        ("Sales", "Customer", "Sales", ("Output IGST", Decimal("18")),
         {"Customer": "Dr", "Sales": "Cr", "Output IGST": "Cr"}),
        ("Debit Note", "Supplier", "Purchase Return", ("Input IGST", Decimal("18")),
         {"Supplier": "Dr", "Purchase Return": "Cr", "Input IGST": "Cr"}),
        ("Credit Note", "Customer", "Sales Return", ("Output IGST", Decimal("18")),
         {"Customer": "Cr", "Sales Return": "Dr", "Output IGST": "Dr"}),
        ("Payment", "Supplier", "HDFC Bank", None, {"Supplier": "Dr", "HDFC Bank": "Cr"}),
        ("Receipt", "Customer", "HDFC Bank", None, {"Customer": "Cr", "HDFC Bank": "Dr"}),
    ]
    for vt, party, line, tax, want in cases:
        spec = voucher_xml.VoucherSpec(**base, voucher_type=vt, party_ledger=party,
                                       lines=[voucher_xml.VoucherLine(line, Decimal("100"))],
                                       taxes={"igst": tax} if tax else {})
        got = {led: side for side, led, _ in sides(voucher_xml.build(spec))}
        check(f"{vt}: {', '.join(f'{s} {l}' for l, s in want.items())}", got == want, str(got))

    custom = voucher_xml.VoucherSpec(**base, voucher_type="GST Purchase - Imports", voucher_parent="Purchase",
                                     party_ledger="Supplier", lines=[voucher_xml.VoucherLine("E", Decimal("1"))])
    check("a custom type posts like its parent",
          {l: s for s, l, _ in sides(voucher_xml.build(custom))} == {"Supplier": "Cr", "E": "Dr"})

    j = voucher_xml.VoucherSpec(**base, voucher_type="Journal", party_ledger="Suspense", lines=[
        voucher_xml.VoucherLine("Depreciation", Decimal("500"), is_debit=True),
        voucher_xml.VoucherLine("Plant", Decimal("500"), is_debit=False)])
    check("a journal posts each line on its own side",
          {l: s for s, l, _ in sides(voucher_xml.build(j))} == {"Depreciation": "Dr", "Plant": "Cr"})

    try:
        voucher_xml.build(voucher_xml.VoucherSpec(**base, voucher_type="Journal", party_ledger="S",
                                                  lines=[voucher_xml.VoucherLine("A", Decimal("5"))]))
        raise AssertionError("a journal line with no side was accepted")
    except voucher_xml.VoucherImbalance:
        check("a journal refuses a line with no debit/credit side", True)


def test_voucher_xml(db, entry: Entry) -> None:
    print("\n[the voucher for the sample invoice]")
    from app.bridge.routers.entries import _build_spec

    xml = voucher_xml.build(_build_spec(db, entry))
    check("supplier is credited the gross", "<AMOUNT>169228.52</AMOUNT>" in xml)
    check("four allocations to one ledger merge into a single debit",
          xml.count("Certification &amp; Audit Fees") == 1)
    check("expense is debited the taxable value", "<AMOUNT>-143414.00</AMOUNT>" in xml)
    check("input IGST is debited", "<AMOUNT>-25814.52</AMOUNT>" in xml and "Input IGST" in xml)
    check("Supplier Invoice No. is left blank, as the books are kept", "<REFERENCE>" not in xml)
    check("bill-wise allocation is created, under the bill's number",
          "<NAME>5500100-26004852</NAME><BILLTYPE>New Ref</BILLTYPE>" in xml)
    entry.reference = "SI-77"
    check("... and a Supplier Invoice No. typed in goes in", "<REFERENCE>SI-77</REFERENCE>" in
          voucher_xml.build(_build_spec(db, entry)))
    entry.reference = None
    check("the chosen voucher type is what Tally receives", 'VCHTYPE="GST Purchase - Services"' in xml)
    check("a normal post is not optional", "ISOPTIONAL" not in xml)

    draft = voucher_xml.build(_build_spec(db, entry, optional=True))
    check("draft mode posts an optional voucher", "<ISOPTIONAL>Yes</ISOPTIONAL>" in draft)

    try:
        voucher_xml._assert_balanced(["<AMOUNT>-100.00</AMOUNT>", "<AMOUNT>60.00</AMOUNT>"])
        raise AssertionError("an unbalanced voucher was allowed through")
    except voucher_xml.VoucherImbalance:
        check("an unbalanced voucher is refused before it reaches Tally", True)

    spec = _build_spec(db, entry)
    spec.party_ledger = "A & B Traders"
    check("ampersands in names are escaped", "A &amp; B Traders" in voucher_xml.build(spec))


def test_approval_gates(db, entry: Entry) -> None:
    print("\n[approval gates]")
    from app.bridge.routers.entries import _blockers

    held = entry.party_ledger_id
    entry.party_ledger_id = None
    check("no party ledger blocks approval",
          any("ledger" in b for b in _blockers(db, entry, ApproveRequest())))
    entry.party_ledger_id = held

    from app.bridge.config import settings
    from app.bridge.routers.entries import approve
    settings.tally_allow_post = False
    r = approve(entry.id, ApproveRequest(mode="post"), db)
    check("posting is refused while 'write to Tally' is off", not r.ok and "switched off" in r.message,
          r.message)
    r = approve(entry.id, ApproveRequest(mode="export", dry_run=True), db)
    check("a dry run returns the XML and changes nothing", r.ok and r.xml and entry.status == "pending")

    # Writing allowed, but Tally is closed: the pre-post duplicate search
    # cannot run, and the app must say so rather than post blind.
    settings.tally_allow_post = True
    r = approve(entry.id, ApproveRequest(mode="post"), db)
    check("posting never skips the Tally duplicate search silently",
          not r.ok and r.needs_ack and "duplicates" in r.message, r.message)
    r = approve(entry.id, ApproveRequest(mode="post", override_duplicate=True), db)
    check("once acknowledged, a closed Tally gives a clear connection error",
          not r.ok and "Could not reach Tally" in r.message and entry.status == "pending", r.message)
    settings.tally_allow_post = False
    db.rollback()


def test_hindi_and_vision(db, company) -> None:
    print("\n[the AI reader: Hindi, scans, and what it is not allowed to do]")
    check("Devanagari vowel signs survive normalisation",
          matching.normalize("श्री गणेश ट्रेडर्स") == "श्री गणेश ट्रेडर्स", matching.normalize("श्री गणेश ट्रेडर्स"))
    check("Devanagari digits parse as amounts", to_decimal("१,२५०.००") == Decimal("1250.00"))
    check("Devanagari digits parse as dates", str(to_date("१२/०९/२०२६")) == "2026-09-12")
    check("'Rs. 4,105/-' parses as an amount", to_decimal("Rs. 4,105/-") == Decimal("4105"))
    k = parse("")
    k.put("grand_total", Decimal("4105"), 0.9, "regex")
    k.lines = [{"line_no": 0, "amount": Decimal("2400")}, {"line_no": 1, "amount": Decimal("880")},
               {"line_no": 2, "amount": Decimal("852")}]  # 825 misread as 852
    check("on a bill with no tax, a misread figure fails the lines-vs-total check",
          any(c["code"] == "line_sum" and c["severity"] == "error" for c in validate.run(k)))

    from app.bridge.main import app as api
    from app.bridge.routers import entries as entries_router

    check("there is no chat or question-answering endpoint",
          not hasattr(llm, "ask") and not any(getattr(r, "path", "") == "/api/ask" for r in api.routes))

    # What the model copies off a handwritten Hindi bill: text, as written.
    reply = {"invoice_no": "४७", "invoice_date": "१२/०९/२०२६", "supplier_name": "श्री गणेश ट्रेडर्स",
             "supplier_gstin": "08ZZZPZ1111Z1ZZ", "grand_total": "११३०/-",
             "lines": [{"description": "चावल", "quantity": "10", "rate": "113", "amount": "1,130"}],
             # Keys outside the reading schema must be ignored, whatever they say.
             "voucher_type": "Sales", "advice": "post this as a sale"}
    calls: list[tuple] = []
    real_chat, real_caps = llm._chat, llm.capabilities

    def fake_chat(*a, **k):
        calls.append((a, k))
        return json.dumps(reply, ensure_ascii=False)

    llm._chat, llm.capabilities = fake_chat, lambda: frozenset({"completion", "vision"})
    try:
        p = parse("")  # the OCR read nothing useful
        p.put("grand_total", Decimal("1180"), 0.9, "regex", "Total 1180")
        filled = llm.merge_into(p, "", images=[b"page"])
        schema = calls[0][0][2]
        lists = {"lines": llm.LINE_FIELDS, "charges": llm.CHARGE_FIELDS}
        check("the model can only answer in reading fields, all plain text",
              set(schema["properties"]) <= set(llm.FIELDS) | set(lists)
              and all(v.get("type") == "string" for k, v in schema["properties"].items() if k not in lists)
              and all(set(schema["properties"][k]["items"]["properties"]) == set(f)
                      and all(x["type"] == "string" for x in schema["properties"][k]["items"]["properties"].values())
                      for k, f in lists.items() if k in schema["properties"]),
              str(schema["properties"].keys()))
        check("a Hindi name stays as written: no translation", p.get("party_name") == "श्री गणेश ट्रेडर्स")
        check("the app, not the model, turns Devanagari digits into numbers",
              p.get("invoice_no") == "47" and str(p.get("invoice_date")) == "2026-09-12")
        check("a GSTIN failing its check digit is dropped, not trusted", p.get("party_gstin") is None)
        check("the rules' total is kept over the model's", p.get("grand_total") == Decimal("1180"))
        check("... but the two readings disagreeing is flagged",
              p.fields["grand_total"].confidence <= 0.6
              and any(c["code"] == "reading_differs" for c in validate.run(p)), str(p.conflicts))
        check("item rows are copied as written", p.lines[0]["description_raw"] == "चावल"
              and p.lines[0]["amount"] == Decimal("1130"))
        check("anything outside the reading schema is ignored",
              "advice" not in p.fields and p.get("voucher_type") is None)
        check("model-read values are marked as the model's",
              p.fields["invoice_no"].source == "llm" and "invoice_no" in filled)

        # Through the whole pipeline: a photo goes to the model as a picture.
        db.add(Ledger(company_id=company.id, tally_name="Shri Ganesh Traders", parent="Sundry Creditors",
                      is_party=True, norm_name=matching.normalize("Shri Ganesh Traders")))
        db.commit()
        from PIL import Image

        def photo_doc(name: str, size: tuple[int, int]) -> Document:
            photo = _tmp / name
            Image.new("RGB", size, "white").save(photo)
            stored, digest = pipeline.adopt_file(photo)
            d = Document(filename=photo.name, stored_path=str(stored), sha256=digest,
                         size_bytes=photo.stat().st_size, source="upload", requested_voucher_type="Purchase")
            db.add(d)
            db.commit()
            return d

        calls.clear()
        settings.llm_enabled = True
        e = pipeline.process_document(db, photo_doc("kachcha-1.jpg", (900, 1200)), company_id=company.id,
                                      check_tally=False)
        check("a scan is up for review before the AI reads it: the OCR first, the AI after, in the background",
              not calls and ai_check.pending(e) and entries_router._summary(e).ai_state == "waiting"
              and entries_router.stats(db=db)["ai_checking"] == 1)
        ai_check.check(e.id)
        db.expire(e)
        check("a photo is sent to the model as an image", bool(calls and calls[0][1].get("images")))
        check("its total and line came from the picture",
              e.grand_total == Decimal("1130") and e.lines[0].description_raw == "चावल")
        check("... and the check is marked done, saying what it changed",
              ai_check.state(e).get("state") == "done" and "Total" in ai_check.state(e).get("changed", [])
              and entries_router.stats(db=db)["ai_checking"] == 0, str(ai_check.state(e)))
        check("a Hindi name is not guessed onto an English ledger", e.party_ledger_id is None,
              f"{e.party_name_raw} -> {e.party_match_reason}")

        # You map it once; the next bill from them matches by itself.
        matching.remember(db, company.id, "ledger", "श्री गणेश ट्रेडर्स", "Shri Ganesh Traders")
        db.commit()
        e2 = pipeline.process_document(db, photo_doc("kachcha-2.jpg", (900, 1201)), company_id=company.id,
                                       check_tally=False)
        ai_check.check(e2.id)
        db.expire(e2)
        check("after one mapping by you, the same Hindi name matches",
              e2.party_ledger is not None and e2.party_ledger.tally_name == "Shri Ganesh Traders"
              and e2.party_confidence >= 0.99, str(e2.party_match_reason))

        e2.confidence, e2.validations = 0.99, []
        db.commit()
        check("an entry the AI read is never 'clean'",
              e2.id not in entries_router.stats(db=db)["clean_ids"])
        batch = entries_router.approve_batch([e2.id], ApproveRequest(mode="export"), db)
        check("... and bulk approval refuses it", not batch["results"][0]["ok"], str(batch["results"][0]))

        # A scan where the OCR ran the name together and dropped a digit.
        reply.clear()
        reply.update({"supplier_name": "KESTREL AUDIT (INDIA) PRIVATE LIMITED", "taxable_total": "1,43,414.00",
                      "lines": [{"description": "Audit fee", "amount": "78,750.00"},
                                {"description": "Travel", "amount": "64,664.00"}]})
        p2 = parse("")
        p2.put("party_name", "KESTRELAUDIT(INDIA)PRIVATELIMITED", 0.85, "regex", "OCR line")
        p2.put("taxable_total", Decimal("143414.00"), 0.9, "regex", "Taxable 143414.00")
        p2.lines = [{"line_no": 0, "description_raw": "Audit fee", "amount": Decimal("7875.00")},
                    {"line_no": 1, "description_raw": "Travel", "amount": Decimal("64664.00")}]
        llm.merge_into(p2, "", images=[b"page"])
        check("a name the OCR ran together takes the image's spacing, unflagged",
              p2.get("party_name") == "KESTREL AUDIT (INDIA) PRIVATE LIMITED"
              and not any(c["field"] == "party_name" for c in p2.conflicts), str(p2.conflicts))
        check("lines that only add up as the image read them come from the image, flagged",
              p2.lines[0]["amount"] == Decimal("78750") and any(c["field"] == "lines" for c in p2.conflicts))

        # Whose books: the OCR misread the buyer's GSTIN on a scan.
        yours = {"08ZZZPZ1111Z1Z4"}
        reply.clear()
        reply.update({"supplier_name": "KESTREL AUDIT", "supplier_gstin": "09XXXCX3333X1ZV",
                      "buyer_gstin": "08ZZZPZ1111Z1Z4"})
        p3 = parse("")
        llm.merge_into(p3, "", images=[b"page"], own_gstins=yours)
        check("the buyer's GSTIN, read off the scan, says whose books the bill belongs in",
              p3.get("own_gstin") == "08ZZZPZ1111Z1Z4" and p3.get("party_gstin") == "09XXXCX3333X1ZV")
        reply.update({"supplier_name": "SAFFRON IMPEX", "supplier_gstin": "08ZZZPZ1111Z1Z4",
                      "buyer_gstin": "09XXXCX3333X1ZV"})
        p4 = parse("")
        llm.merge_into(p4, "", images=[b"page"], own_gstins=yours)
        check("on a sale the party is the buyer, never your own firm",
              p4.get("own_gstin") == "08ZZZPZ1111Z1Z4" and p4.get("party_gstin") == "09XXXCX3333X1ZV"
              and p4.get("party_name") is None, f"{p4.get('party_gstin')} {p4.get('party_name')}")
        reply.update({"supplier_name": "X", "supplier_gstin": "09XXXCX3333X1ZV", "buyer_gstin": "27WWWFW4444W1ZS"})
        p5 = parse("")
        llm.merge_into(p5, "", images=[b"page"], own_gstins=yours)
        check("a GSTIN that is not one of your companies' is never taken as yours", p5.get("own_gstin") is None)
    finally:
        llm._chat, llm.capabilities = real_chat, real_caps
        settings.llm_enabled = False


def test_background_ai(db, company) -> None:
    print("\n[the AI's check in the background: it fills in and flags, and never overwrites you]")
    from fastapi import HTTPException
    from PIL import Image

    from app.bridge.routers import entries as er
    from app.bridge.schemas import EntryUpdate, LineUpdate

    reply: dict = {}
    real_chat, real_caps = llm._chat, llm.capabilities
    llm._chat = lambda *a, **k: json.dumps(reply) if reply else None  # no reply: the AI did not answer
    llm.capabilities = lambda: frozenset({"completion", "vision"})
    settings.llm_enabled = True

    def scan(n: int) -> Entry:
        photo = _tmp / f"bg-{n}.png"
        Image.new("RGB", (900, 1300 + n), "white").save(photo)
        stored, digest = pipeline.adopt_file(photo)
        d = Document(filename=photo.name, stored_path=str(stored), sha256=digest, size_bytes=photo.stat().st_size,
                     source="upload", requested_voucher_type="Purchase")
        db.add(d)
        db.commit()
        return pipeline.process_document(db, d, company_id=company.id, check_tally=False)

    def checked(e: Entry) -> Entry:
        ai_check.check(e.id)
        db.expire(e)
        return e

    def warnings(e: Entry) -> list[str]:
        return [v["message"] for v in e.validations if v["code"] == "reading_differs"]

    try:
        reply.update({"invoice_no": "B-77", "invoice_date": "12/09/2026", "grand_total": "1,130",
                      "lines": [{"description": "Rice", "quantity": "10", "rate": "113", "amount": "1130"}]})
        e = scan(1)
        er.update_entry(e.id, EntryUpdate(invoice_no="B-71", learn=False), db)
        checked(e)
        check("a field you changed before the check ended is never overwritten", e.invoice_no == "B-71", e.invoice_no)
        check("... the AI's reading of it is a warning to check on the bill",
              any("you entered B-71" in w and "B-77" in w for w in warnings(e)), str(warnings(e)))
        check("... and what you had not touched, it fills in",
              e.grand_total == Decimal("1130") and e.invoice_date == date(2026, 9, 12) and len(e.lines) == 1)
        check("... in the AI's colour", e.field_meta["grand_total"]["source"] == "llm"
              and "grand_total" in e.field_meta["_llm_filled"]["value"]
              and "invoice_no" not in e.field_meta["_llm_filled"]["value"])

        e = scan(2)
        er.update_entry(e.id, EntryUpdate(lines=[LineUpdate(description_raw="Wheat", amount=Decimal("500"))],
                                          learn=False), db)
        checked(e)
        check("lines you changed are kept, and the AI's are a warning",
              [l.description_raw for l in e.lines] == ["Wheat"] and any("Line items" in w for w in warnings(e)),
              str(warnings(e)))

        e = scan(3)
        er.reject(e.id, "", db)
        checked(e)
        check("a bill rejected (or put in Tally) before the check ends only gets warnings",
              e.grand_total is None and not e.lines and len(warnings(e)) >= 3, str(warnings(e)))

        e = scan(4)
        was = dict(reply)
        reply.clear()
        checked(e)
        check("when the AI gives no answer the check says so, and the bill stays as the OCR read it",
              ai_check.state(e).get("state") == "failed" and e.grand_total is None, str(ai_check.state(e)))
        reply.update(was)

        try:
            er.update_entry(e.id, EntryUpdate(lines=[LineUpdate(id=987654, amount=Decimal("1"))], learn=False), db)
            raise AssertionError("an edit to a line that is gone made a new line")
        except HTTPException as exc:
            db.rollback()
            check("an edit to a line the AI's check took away asks for a reload, never makes a stray line",
                  exc.status_code == 409, str(exc.detail))

        e = scan(5)
        ai_check.resume()
        for _ in range(200):
            db.expire(e)
            if not ai_check.pending(e):
                break
            time.sleep(0.05)
        check("checks still waiting when the app was closed run when it starts again",
              ai_check.state(e).get("state") == "done" and e.grand_total == Decimal("1130"), str(ai_check.state(e)))
    finally:
        llm._chat, llm.capabilities = real_chat, real_caps
        settings.llm_enabled = False


def test_share_limits() -> None:
    print("\n[the app's share of the computer]")
    settings.cpu_threads, settings.gpu_share = 10, 0.5
    try:
        check("the AI runtime is given only the app's threads", llm._options()["num_thread"] == 10)
        llm._rest_after(0.3)
        t = time.monotonic()
        llm._wait_turn()
        check("after a reading the GPU rests as long as it worked (busy at most half the time)",
              0.25 <= time.monotonic() - t <= 0.6, f"{time.monotonic() - t:.2f}s")
    finally:
        settings.cpu_threads, settings.gpu_share, llm._next_turn = 0, 1.0, 0.0
    check("without a limit set (development), nothing is held back",
          "num_thread" not in llm._options() and (llm._rest_after(5.0) or llm._next_turn == 0.0))


def test_bill_details() -> None:
    print("\n[names and numbers as real bills print them]")
    from app.bridge.services.parse import is_invocation

    p = parse("Shree Ganeshaya Namah\nGSTIN:08UUUPU6666U2Z8\n9000000001\nSURYA GLASS HOUSE\nInvoice No. 320\n")
    check("the blessing at the top is not the supplier; the name under the GSTIN is",
          p.get("party_name") == "SURYA GLASS HOUSE", str(p.get("party_name")))
    p = parse("ShreeGneshay\nOrigin./ Dupli. / Tripl.\nGopal Hitkari\nGSTIN:-08VVVPV5555V1Z8\n")
    check("... nor is 'Original / Duplicate / Triplicate'", p.get("party_name") == "Gopal Hitkari",
          str(p.get("party_name")))
    check("a firm named after a blessing keeps its name",
          is_invocation("Jai Mata Di") and not is_invocation("Jai Mata Di Traders")
          and not is_invocation("श्री गणेश ट्रेडर्स") and is_invocation("श्री गणेशाय नमः"))
    check("'e-Invoice' under the title is not the bill number",
          parse("Tax Invoice\ne-lnvoice\nIRN : 7a6b9e04\n").get("invoice_no") is None)
    check("a number with its financial year is a bill number",
          parse("Invoice No.: 93/2026-27\n").get("invoice_no") == "93/2026-27",
          str(parse("Invoice No.: 93/2026-27\n").get("invoice_no")))
    check("... and a date beside the label is not", parse("Invoice No. 12/09/2026\n").get("invoice_no") is None)


def test_companies(db, company) -> None:
    print("\n[one Tally, several companies]")
    from fastapi import HTTPException

    from app.bridge.routers import entries as er
    from app.bridge.schemas import EntryUpdate

    other = Company(tally_name="Saffron Exports", gstin="08ZZZPZ1111Z2Z3", state_code="08",
                    voucher_types=[{"name": "Purchase", "parent": "Purchase"}, {"name": "Payment", "parent": "Payment"}])
    db.add(other)
    db.flush()
    for name, parent in [("Kestrel Audit India Pvt Ltd", "Sundry Creditors"), ("Input IGST", "Duties & Taxes"),
                         ("Testing Charges", "Indirect Expenses")]:
        db.add(Ledger(company_id=other.id, tally_name=name, parent=parent, is_party="Creditors" in parent,
                      norm_name=matching.normalize(name)))
    db.commit()

    by = pipeline.own_gstin_map(db)
    pick = lambda gstin, chosen: pipeline.choose_company(db, gstin, chosen, by)  # noqa: E731
    check("a bill made out to one company's GSTIN goes to that company",
          pick("08ZZZPZ1111Z2Z3", None)[0].id == other.id)
    c, why = pick("08ZZZPZ1111Z2Z3", company.id)
    check("... even if another was chosen at upload, and the screen says why",
          c.id == other.id and "not Saffron Impex as chosen" in why, why)
    check("a kachcha bill, with no GSTIN of yours, goes where you chose", pick(None, other.id)[0].id == other.id)
    c, why = pick(None, None)
    check("with several companies and nothing to go on, the company is not guessed",
          c is None and "which of your companies" in why, why)

    twin = Company(tally_name="Saffron Impex 2025-26", gstin="08ZZZPZ1111Z1Z4", state_code="08")
    db.add(twin)
    db.commit()
    by = pipeline.own_gstin_map(db)
    check("two companies with one GSTIN: your choice at upload decides",
          pick("08ZZZPZ1111Z1Z4", twin.id)[0].id == twin.id)
    c, why = pick("08ZZZPZ1111Z1Z4", None)
    check("... and with no choice it asks, naming both", c is None and "Saffron Impex 2025-26" in why, why)
    db.delete(twin)
    db.commit()

    e = _ingest(db, "GST Purchase - Services")
    check("the sample invoice goes to Saffron Impex by its GSTIN", e.company_id == company.id)
    moved = er.update_entry(e.id, EntryUpdate(company_id=other.id), db)
    led = db.get(Ledger, moved.party_ledger_id) if moved.party_ledger_id else None
    check("moved to another company, the party is matched in that company's books",
          moved.company_id == other.id and led is not None and led.company_id == other.id,
          f"{moved.party_ledger_id} {moved.party_match_reason}")
    check("... every line too, nothing left from the old books",
          all(db.get(Ledger, l.ledger_id).company_id == other.id for l in moved.lines if l.ledger_id)
          and not any(l.stock_item_id for l in moved.lines))
    check("a custom type the new company lacks becomes the type it works like",
          moved.voucher_type == "Purchase", moved.voucher_type)
    note = (moved.field_meta.get("_company") or {}).get("value", "")
    check("the screen says it was your choice, and that the bill names another GSTIN",
          note.startswith("Chosen by you") and "08ZZZPZ1111Z1Z4" in note, note)
    theirs = db.query(Ledger).filter_by(company_id=company.id, tally_name="HDFC Bank").one()
    try:
        er.update_entry(e.id, EntryUpdate(party_ledger_id=theirs.id), db)
        raise AssertionError("a ledger from another company's books was accepted")
    except HTTPException as exc:
        check("a ledger from another company's books is refused", exc.status_code == 400, str(exc.detail))
    db.rollback()


def test_batches(db) -> None:
    print("\n[a batch of bills: review what is ready while the rest is read]")
    import hashlib

    import pymupdf
    from fastapi.testclient import TestClient

    from app.bridge.main import app as api
    from app.bridge.routers import documents as docs_router

    def pdf(*pages: str) -> bytes:
        d = pymupdf.open()
        for text in pages:
            d.new_page().insert_text((72, 72), text)
        return d.tobytes()

    client = TestClient(api)
    form = {"voucher_type": "Purchase", "use_llm": "false"}
    scan = pdf("Invoice No: SPLIT-1", "Invoice No: SPLIT-2", "Invoice No: SPLIT-3")
    r = client.post("/api/documents/upload", files=[("files", ("scan.pdf", scan, "application/pdf"))],
                    data={**form, "each_page": "true"}).json()
    ids = [x["document_id"] for x in r["results"]]
    check("each page of a PDF can be a bill of its own",
          len(ids) == 3 and r["results"][1]["filename"] == "scan - page 2.pdf", str(r))
    st = client.get(f"/api/documents/status?ids={','.join(map(str, ids))}").json()
    check("one request reports on the whole batch", len(st) == 3 and all(s["entry_id"] for s in st), str(st))
    again = client.post("/api/documents/upload", files=[("files", ("scan.pdf", scan, "application/pdf"))],
                        data={**form, "each_page": "true"}).json()
    check("the same PDF split again is caught, page by page",
          all(x.get("duplicate_file") for x in again["results"]), str(again))

    blob = pdf("Invoice No: LOST-1")
    lost = Document(filename="lost.pdf", stored_path=str(_tmp / "gone.pdf"), size_bytes=len(blob),
                    sha256=hashlib.sha256(blob).hexdigest(), source="upload")
    db.add(lost)
    db.commit()
    docs_router._process(lost.id, None, False)
    db.expire_all()
    check("a file deleted before it was read fails, saying what to do",
          lost.status == "failed" and "Upload lost.pdf again" in (lost.error or ""), str(lost.error))
    check("the review screen is told what could not be read",
          any(f["id"] == lost.id for f in client.get("/api/entries/stats").json()["failed"]))
    r = client.post("/api/documents/upload", files=[("files", ("lost.pdf", blob, "application/pdf"))],
                    data=form).json()["results"][0]
    db.expire_all()
    check("uploading it again reads it again, instead of 'already uploaded'",
          r["document_id"] == lost.id and not r.get("duplicate_file") and lost.status == "extracted", str(r))

    bad = Document(filename="bad.pdf", stored_path=str(_tmp / "gone2.pdf"), sha256="0" * 64, source="upload",
                   status="failed", error="lost")
    db.add(bad)
    db.commit()
    client.post(f"/api/documents/{bad.id}/dismiss")
    check("a failed file can be set aside",
          not any(f["id"] == bad.id for f in client.get("/api/entries/stats").json()["failed"]))

    path = _tmp / "waiting.pdf"
    path.write_bytes(pdf("Invoice No: WAIT-1"))
    waiting = Document(filename=path.name, stored_path=str(path), sha256="1" * 64, source="upload")
    db.add(waiting)
    db.commit()
    check("a bill left waiting when the app closed is found at the next start",
          (waiting.id, None) in docs_router.unfinished())
    docs_router.read_in_order([(waiting.id, None)])
    docs_router.read_in_order([(waiting.id, None)])
    db.expire_all()
    check("... read then, and only once",
          waiting.status == "extracted" and db.query(Entry).filter_by(document_id=waiting.id).count() == 1)


def test_fake_tally_books(db) -> None:
    print("\n[the simulated Tally keeps each company's books apart]")
    import fake_tally

    from app.bridge.routers.tally_api import discover

    srv = fake_tally.serve(9097)
    real = settings.tally_port
    settings.tally_port = 9097
    try:
        exports = db.query(Company).filter_by(tally_name="Saffron Exports").one()
        exports.gstin = exports.state_code = None  # as if never discovered
        db.commit()
        discover(db=db)
        db.refresh(exports)
        check("a GSTIN kept on the company's GST registration (TallyPrime 3+) is found",
              exports.gstin == "08ZZZPZ1111Z2Z3" and exports.state_code == "08", str(exports.gstin))
        names = [c["name"] for c in tally.companies()]
        impex, exports = tally.ledgers(names[0]), tally.ledgers(names[1])
        check("both companies are open, each with its own ledgers",
              len(names) == 2 and {l["name"] for l in impex} != {l["name"] for l in exports})
        msg = ("<TALLYMESSAGE><VOUCHER VCHTYPE=\"Purchase\"><DATE>20260901</DATE>"
               "<ALLLEDGERENTRIES.LIST><LEDGERNAME>HDFC Bank</LEDGERNAME><AMOUNT>5</AMOUNT></ALLLEDGERENTRIES.LIST>"
               "</VOUCHER></TALLYMESSAGE>")
        check("a ledger of one company does not exist in the other",
              tally.import_xml(names[1], msg).errors == 1 and tally.import_xml(names[0], msg).created == 1)
        check("Tally's '&#4; Primary' marker is read as plain 'Primary', not refused as bad XML",
              tally.stock_items(names[0])[0]["parent"] == "Primary")
        from app.bridge.routers.tally_api import sync_masters
        exports_co = db.query(Company).filter_by(tally_name="Saffron Exports").one()
        sync_masters(exports_co.id, db=db)
        db.refresh(exports_co)
        check("a sync keeps the GST registration by name and the godown, as the vouchers keyed in Tally name them",
              (exports_co.gst_registration, exports_co.godown) == ("Rajasthan Registration", "Main Location"),
              f"{exports_co.gst_registration} / {exports_co.godown}")
        good = voucher_xml.build(voucher_xml.VoucherSpec(
            company=names[0], voucher_type="Purchase", date=date(2026, 9, 1), party_ledger="Jaipur Timber Mart",
            invoice_no="T-9", use_inventory=True, godown="Main Location",
            lines=[voucher_xml.VoucherLine("Purchase - Services", Decimal("1000"), stock_item="Teak Wood Log",
                                           quantity=Decimal("2"), rate=Decimal("500"), uom="Cft", gst_rate=18.0)],
            taxes={"cgst": ("Input CGST", Decimal("90")), "sgst": ("Input SGST", Decimal("90"))},
            round_off=("Round Off", Decimal("-0.40"))))
        old = good.replace("LEDGERENTRIES.LIST>", "ALLLEDGERENTRIES.LIST>").replace("ALLALL", "ALL")
        new_r, old_r = tally.import_xml(names[0], good), tally.import_xml(names[0], old)
        check("an item invoice in the layout Tally keeps is taken; the old layout is an import exception, as 6748 was",
              new_r.ok and old_r.exceptions == 1 and not old_r.ok, f"{new_r} / {old_r}")
        from app.bridge.models import Bought
        from app.bridge.routers.tally_api import _learn_purchases

        impex_co = db.query(Company).filter_by(tally_name=names[0]).one()
        learnt = _learn_purchases(db, impex_co)
        db.commit()
        timber = db.query(Bought).filter_by(company_id=impex_co.id, party="Jaipur Timber Mart").all()
        check("a sync learns from your own vouchers what you bought from whom, and to which ledger",
              learnt == 3 and [(b.item, b.ledger, b.times) for b in timber] == [("Teak Wood Log", "Purchase - Services", 3)],
              str([(b.item, b.ledger, b.times) for b in timber]))
        check("... while a real character written the same way (&#8377;, the rupee sign) is kept",
              tally._clean("<A>&#8377; &#4;x</A>") == "<A>&#8377; x</A>")
        check("the party's balance is read from Tally for the voucher screen; a ledger Tally does not have "
              "has none, not the 0 in the reply's master counts",
              tally.balance(names[0], "Jaipur Timber Mart", date(2026, 9, 1)) == Decimal("1234.50")
              and tally.balance(names[0], "No Such Ledger", date(2026, 9, 1)) is None)
    finally:
        settings.tally_port = real
        srv.shutdown()


class Flaky:
    """Drops the first `drops` connections with a reset, the way a busy Tally
    does (WinError 10054), then answers each request with `body`."""

    def __init__(self, port: int, drops: int, body: bytes = b"<ENVELOPE/>"):
        import socket
        import threading

        self.seen, self.drops, self.body = 0, drops, body
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import socket
        import struct

        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.seen += 1
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = c.recv(65536)
                if not chunk:
                    break
                data += chunk
            if self.seen <= self.drops:
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close = reset
                c.close()
                continue
            head, _, rest = data.partition(b"\r\n\r\n")
            n = int(re.search(rb"content-length: (\d+)", head, re.I).group(1))
            while len(rest) < n:
                rest += c.recv(65536)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/xml\r\nConnection: close\r\n"
                      b"Content-Length: %d\r\n\r\n" % len(self.body) + self.body)
            c.close()

    def close(self) -> None:
        self.sock.close()


def test_tally_line(db, company) -> None:
    print("\n[Tally answers one request at a time]")
    import sqlite3

    one = b'<ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME="X"><NAME>X</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>'
    real = settings.tally_port
    try:
        settings.tally_port = 9095
        srv = Flaky(9095, drops=1, body=one)
        check("a read Tally drops half way is asked again, once",
              tally.companies()[0]["name"] == "X" and srv.seen == 2, str(srv.seen))
        srv.close()

        settings.tally_port = 9094
        srv = Flaky(9094, drops=99)
        p = tally.ping()
        check("a dropped line never crashes the status check: it says what happened",
              p["connected"] is False and "dropped the connection" in p.get("error", ""), str(p))
        before = srv.seen
        try:
            tally.import_xml("X", "<TALLYMESSAGE/>")
            raise AssertionError("a dropped import was taken as done")
        except tally.TallyUnavailable as exc:
            check("a dropped post is never sent twice, and says to check the Day Book",
                  srv.seen - before == 1 and "Day Book" in str(exc), f"{srv.seen - before} {exc}")
        srv.close()

        tally._last_ping = {"connected": True, "url": tally._url(), "companies": 1, "company_names": ["X"]}
        with tally._line:  # another request to Tally is under way
            t = time.monotonic()
            p = tally.ping()
            took = time.monotonic() - t
        check("meanwhile the status check does not wait: the last answer stands",
              p.get("busy") and p["connected"] and took < 0.5, f"{p} {took:.2f}s")
    finally:
        settings.tally_port, tally._last_ping = real, {}

    # Your save while a bill is searched for in Tally: it must not wait on Tally.
    free: list[bool] = []

    def searching(*_a):
        other = sqlite3.connect(str(_tmp / "test.db"), timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")  # what a save needs: the write lock
            free.append(True)
        except sqlite3.OperationalError:
            free.append(False)
        finally:
            other.rollback()
            other.close()
        return []

    real_search = tally.vouchers_in_period
    tally.vouchers_in_period, dedupe._tally_down_until = searching, 0.0
    try:
        stored, digest = pipeline.adopt_file(FIXTURE)
        doc = Document(filename="while-searching.pdf", stored_path=str(stored), sha256="2" * 64,
                       size_bytes=FIXTURE.stat().st_size, source="upload")
        db.add(doc)
        db.commit()
        e = pipeline.process_document(db, doc, company_id=company.id, use_llm=False)
    finally:
        tally.vouchers_in_period = real_search
    check("while a bill is searched for in Tally, a save elsewhere in the app goes through",
          free == [True] and (e.field_meta["_duplicates"]["value"]["tally_checked"] is True), str(free))


def test_tally_replies(db, company) -> None:
    print("\n[what Tally's replies mean]")
    from app.bridge.routers import entries as er

    db.add(Ledger(company_id=company.id, tally_name="Discount", parent="Indirect Expenses",
                  norm_name=matching.normalize("Discount")))
    db.commit()
    bill = Entry(company_id=company.id, voucher_type="Purchase", voucher_parent="Purchase",
                 invoice_date=date(2026, 9, 20))
    check("a stock line with no ledger posts to Purchase Accounts, not the shortest name about",
          er._default_line_ledger(db, bill) == "Purchase - Services", er._default_line_ledger(db, bill))

    real = settings.tally_port
    try:
        settings.tally_port = 9093
        srv = Flaky(9093, drops=0, body=b'<ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME="X"><NAME>X</NAME>'
                                        b'<EDU>Yes</EDU></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>')
        check("a Tally in Educational Mode is recognised", tally.educational() is True)
        asked = srv.seen
        check("... and asked only once", tally.educational() is True and srv.seen == asked)
        srv.close()

        settings.tally_port = 9092
        srv = Flaky(9092, drops=0, body=b"<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><IGNORED>0</IGNORED>"
                                        b"<ERRORS>0</ERRORS><EXCEPTIONS>1</EXCEPTIONS><LINEERROR>Voucher date is "
                                        b"missing for: 'Purchase' voucher 1.</LINEERROR></RESPONSE>")
        tally._educational[tally._url()] = True
        res = tally.import_xml("X", "<TALLYMESSAGE/>")
        text = er._rejected(res, bill)
        check("a voucher Tally keeps as an import exception is not taken as posted", not res.ok and res.exceptions == 1)
        check("... and the message says where it is and what to do with it",
              "Alt+O Import > Exceptions" in text and "posting again" in text, text)
        check("... and why: Educational Mode takes only the 1st, 2nd and 31st", "Educational Mode" in text, text)
        srv.close()
    finally:
        settings.tally_port = real
        tally._educational.clear()


def test_books_like_yours(db) -> None:
    print("\n[posted the way the books are kept: ledger per GST rate, Rounded Off, the bill no. as voucher no.]")
    from fastapi import HTTPException

    from app.bridge.models import EntryLine, StockItem
    from app.bridge.routers import entries as er
    from app.bridge.schemas import EntryUpdate, LineUpdate

    # Named the way a real set of books names them.
    c = Company(tally_name="Indigo Handicrafts Unit 1", gstin="08YYYPY2222Y1Z5", state_code="08",
                voucher_types=[{"name": "Purchase", "parent": "Purchase", "numbering": "Manual"},
                               {"name": "Sales", "parent": "Sales", "numbering": "Automatic"}])
    db.add(c)
    db.flush()
    led = {}
    for name, parent in [("PRIME SUPPLIERS", "Sundry Creditors"), ("Purchase 5%", "Purchase Accounts"),
                         ("PURCHASE @5% GST", "Purchase Accounts"), ("PURCHASE @18% GST", "Purchase Accounts"),
                         ("PURCHASE @ IGST 18%", "Purchase Accounts"), ("STORE & SPARES @18%", "Purchase Accounts"),
                         ("PURCHASE EXTEMPTED (GST)", "Purchase Accounts"), ("CGST", "Duties & Taxes"),
                         ("SGST", "Duties & Taxes"), ("IGST", "Duties & Taxes"), ("Ground Rent", "Indirect Expenses"),
                         ("Rounded Off", "Indirect Expenses")]:
        led[name] = Ledger(company_id=c.id, tally_name=name, parent=parent, is_party="Creditors" in parent,
                           norm_name=matching.normalize(name))
        db.add(led[name])
    pad = StockItem(company_id=c.id, tally_name="XTRA POWER VELCRO PAD SOLID TYPE M 10X125", uom="pcs", gst_rate=18.0)
    magnet = StockItem(company_id=c.id, tally_name="Magnet Catcher M-3 Brown", uom="pcs", gst_rate=18.0)
    db.add_all([pad, magnet])
    doc = Document(filename="prime.pdf", stored_path=str(_tmp / "prime.pdf"), sha256="m" * 64, status="done")
    db.add(doc)
    db.flush()

    def bill(**kw) -> Entry:
        e = Entry(document_id=doc.id, company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase",
                  invoice_no="26-27/SB1/0042", invoice_date=date(2026, 9, 14), party_ledger_id=led["PRIME SUPPLIERS"].id,
                  taxable_total=Decimal("1457.40"), cgst=Decimal("131.17"), sgst=Decimal("131.17"),
                  round_off=Decimal("0.26"), grand_total=Decimal("1720.00"), **kw)
        e.lines = [EntryLine(line_no=0, description_raw="Xtra Power Velcro Pad solid type m10*125", quantity=10,
                             uom="Pcs", rate=Decimal("47.74"), amount=Decimal("477.40"), gst_rate=18.0,
                             stock_item_id=pad.id),
                   EntryLine(line_no=1, description_raw="Magnet catcher no 3 brown", quantity=100, uom="Pcs",
                             rate=Decimal("9.80"), amount=Decimal("980.00"), gst_rate=18.0, stock_item_id=magnet.id)]
        db.add(e)
        db.commit()
        return e

    e = bill()
    xml = voucher_xml.build(er._build_spec(db, e))
    items = re.findall(r"<ALLINVENTORYENTRIES.LIST>.*?<LEDGERNAME>(.*?)</LEDGERNAME>", xml, re.S)
    check("an 18% bill's items post to PURCHASE @18% GST, not the shortest name (Purchase 5%)",
          items == ["PURCHASE @18% GST"] * 2, str(items))
    check("the round-off posts to Rounded Off, found outside Duties & Taxes (and not Ground Rent)",
          ("Dr", "Rounded Off", Decimal("0.26")) in sides(xml), str(sides(xml)))
    check("with Manual numbering the bill's number is the voucher number, as keyed by hand",
          "<VOUCHERNUMBER>26-27/SB1/0042</VOUCHERNUMBER>" in xml)
    check("the quantity is in the item's own unit (pcs), not the bill's spelling",
          "<BILLEDQTY>10.00 pcs</BILLEDQTY>" in xml and "<RATE>47.74/pcs</RATE>" in xml)
    check("an item has no description in Tally unless one is typed: the bill's words are not it",
          "BASICUSERDESCRIPTION" not in xml)
    e.lines[0].description = "SMALL"
    xml = voucher_xml.build(er._build_spec(db, e))
    first = re.search(r"<ALLINVENTORYENTRIES.LIST>\s*(<BASICUSERDESCRIPTION.LIST[^>]*>)\s*"
                      r"<BASICUSERDESCRIPTION>(.*?)</BASICUSERDESCRIPTION>\s*</BASICUSERDESCRIPTION.LIST>\s*<STOCKITEMNAME>", xml)
    check("a description typed under an item goes to Tally as the item's description, first in the line as "
          "your vouchers keep it (SMALL, MID, BIG)",
          first is not None and first.groups() == ('<BASICUSERDESCRIPTION.LIST TYPE="String">', "SMALL")
          and xml.count("<BASICUSERDESCRIPTION>") == 1, xml[:600])
    e.lines[0].description = None

    # The shape of a voucher keyed by hand in Tally.
    c.gst_registration, c.godown, led["PRIME SUPPLIERS"].state = "Rajasthan Registration", "Main Location", "Rajasthan"
    db.commit()
    xml = voucher_xml.build(er._build_spec(db, e))
    rows = re.findall(r"<(LEDGERENTRIES|ALLLEDGERENTRIES)\.LIST>\s*<LEDGERNAME>(.*?)</LEDGERNAME>", xml)
    check("an item invoice keeps the supplier, taxes and round-off in LEDGERENTRIES, as 6674 keyed in Tally does "
          "(sent in ALLLEDGERENTRIES, Tally dropped them and made bill 6748 an import exception)",
          sorted(rows) == sorted(("LEDGERENTRIES", n) for n in ("PRIME SUPPLIERS", "CGST", "SGST", "Rounded Off")),
          str(rows))
    want = ["<VCHENTRYMODE>Item Invoice</VCHENTRYMODE>", "<GSTREGISTRATION>Rajasthan Registration</GSTREGISTRATION>",
            "<CMPGSTIN>08YYYPY2222Y1Z5</CMPGSTIN>", "<STATENAME>Rajasthan</STATENAME>",
            "<PLACEOFSUPPLY>Rajasthan</PLACEOFSUPPLY>", "<NUMBERINGSTYLE>Manual</NUMBERINGSTYLE>",
            "<GODOWNNAME>Main Location</GODOWNNAME>", "<BATCHNAME>Primary Batch</BATCHNAME>",
            "<GSTOVRDNSTOREDNATURE>Local Purchase - Taxable</GSTOVRDNSTOREDNATURE>",
            "<GSTLEDGERSOURCE>PURCHASE @18% GST</GSTLEDGERSOURCE>", "<GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>",
            "<GSTRATE>9</GSTRATE>", "<GSTRATE>18</GSTRATE>"]
    check("... with the GST registration, godown, GST nature and rates that voucher carries",
          all(w in xml for w in want), str([w for w in want if w not in xml]))
    check("... and no Supplier Invoice No. or narration of the app's own: the books leave them blank",
          "<REFERENCE>" not in xml and "<NARRATION></NARRATION>" in xml)
    round_row = (r"<LEDGERENTRIES.LIST>\s*<LEDGERNAME>Rounded Off</LEDGERNAME>\s*<ISDEEMEDPOSITIVE>(\w+)</ISDEEMEDPOSITIVE>"
                 r"\s*<ISPARTYLEDGER>No</ISPARTYLEDGER>\s*<AMOUNT>(-?[\d.]+)</AMOUNT>")
    up = re.search(round_row, xml).groups()
    e.round_off = Decimal("-0.40")
    down = re.search(round_row, voucher_xml.build(er._build_spec(db, e))).groups()
    check("a round-off stays a Dr-nature row with a signed amount, as Tally keeps it: +0.26 is -0.26, 0.40 off is +0.40",
          up == ("Yes", "-0.26") and down == ("Yes", "0.40"), f"{up} {down}")
    db.rollback()

    check("a 5% line goes to the GST ledger for 5%, not the old VAT one",
          er._default_line_ledger(db, e, 5.0) == "PURCHASE @5% GST")
    check("an inter-state 18% line goes to the IGST purchase ledger",
          er._default_line_ledger(db, Entry(company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase",
                                            igst=Decimal("262.33")), 18.0) == "PURCHASE @ IGST 18%")
    check("a nil-rated line goes to the exempted purchase ledger",
          er._default_line_ledger(db, e, 0.0) == "PURCHASE EXTEMPTED (GST)")
    check("with no rate on the line, the bill's tax over its taxable value decides",
          er._bill_rate(Entry(taxable_total=Decimal("1457.40"), cgst=Decimal("131.17"), sgst=Decimal("131.17"))) == 18.0)

    rows = er.preview(e.id, db)["rows"]
    check("each row of the voucher says what it is, so it can be changed",
          [r["role"] for r in rows] == ["party", "line", "line", "tax", "tax", "round"]
          and [r.get("kind") for r in rows[3:5]] == ["cgst", "sgst"]
          and rows[1]["line_ids"] == [e.lines[0].id] and rows[1]["ledger_id"] == led["PURCHASE @18% GST"].id,
          str(rows))
    check("... an item its rate and unit (the item's own, as posted), and a tax its rate, as Tally's voucher screen shows them",
          (rows[2].get("rate"), rows[2].get("unit")) == ("9.80", "pcs") and [r.get("pct") for r in rows[3:5]] == ["9", "9"],
          str([(r.get("rate"), r.get("unit"), r.get("pct")) for r in rows]))

    spares = led["STORE & SPARES @18%"]
    er.update_entry(e.id, EntryUpdate(lines=[LineUpdate(id=e.lines[0].id, ledger_id=spares.id)]), db)
    db.refresh(e)
    check("picking a ledger on an item's row keeps the item and posts it to that ledger",
          e.lines[0].stock_item_id == pad.id and e.lines[0].ledger_id == spares.id)
    again = bill()
    check("... and that item's next bill posts to it too",
          voucher_xml.build(er._build_spec(db, again)).count("<LEDGERNAME>STORE &amp; SPARES @18%</LEDGERNAME>") == 1)
    er.update_entry(e.id, EntryUpdate(lines=[LineUpdate(id=e.lines[1].id, ledger_id=led["PURCHASE @18% GST"].id,
                                                        stock_item_id=None)], learn=False), db)
    db.refresh(e)
    check("the lines table can still post a line to a ledger instead of an item",
          e.lines[1].stock_item_id is None and e.lines[1].ledger_id == led["PURCHASE @18% GST"].id)

    er.update_entry(e.id, EntryUpdate(tax_ledgers={"round": led["Ground Rent"].id, "cgst": led["IGST"].id}), db)
    db.refresh(e)
    xml = voucher_xml.build(er._build_spec(db, e))
    check("the tax and round-off rows post to the ledgers picked for them",
          ("Dr", "Ground Rent", Decimal("0.26")) in sides(xml) and ("Dr", "IGST", Decimal("131.17")) in sides(xml),
          str(sides(xml)))
    er.update_entry(e.id, EntryUpdate(tax_ledgers={"round": None}), db)
    db.refresh(e)
    check("... and a row goes back to the app's own pick when cleared",
          ("Dr", "Rounded Off", Decimal("0.26")) in sides(voucher_xml.build(er._build_spec(db, e))))
    try:
        er.update_entry(e.id, EntryUpdate(tax_ledgers={"freight": led["CGST"].id}), db)
        raise AssertionError("a row that does not exist was accepted")
    except HTTPException as exc:
        check("a row that is not a tax or the round-off is refused", exc.status_code == 400, str(exc.detail))
    db.rollback()

    db.delete(led["Rounded Off"])
    db.commit()
    fresh = bill()
    try:
        er._build_spec(db, fresh)
        raise AssertionError("a round-off posted to a ledger the books do not have")
    except HTTPException as exc:
        check("no round-off ledger in the books: posting says so instead of sending a made-up one",
              exc.status_code == 422 and "round-off" in exc.detail, str(exc.detail))
    check("... and the preview marks the gap", "no round-off ledger in Tally" in er.preview(fresh.id, db)["missing"])

    fresh.round_off = None
    c.voucher_types = [{"name": "Purchase", "parent": "Purchase", "numbering": "Automatic"}]
    db.commit()
    check("an automatically numbered type is left for Tally to number",
          "VOUCHERNUMBER" not in voucher_xml.build(er._build_spec(db, fresh)))


def test_voucher_editing(db) -> None:
    print("\n[the entry to Tally, changed like a voucher: amounts, Dr/Cr, rows added and removed, Undo]")
    from fastapi import HTTPException

    from app.bridge.models import EntryLine
    from app.bridge.routers import entries as er
    from app.bridge.schemas import EntryUpdate, LineUpdate

    base = dict(company="X", date=date(2026, 9, 28), invoice_no="B-1", voucher_type="Purchase")
    spec = voucher_xml.VoucherSpec(**base, party_ledger="Supplier", taxes={"igst": ("IGST", Decimal("162"))}, lines=[
        voucher_xml.VoucherLine("Purchase", Decimal("1000")), voucher_xml.VoucherLine("Discount", Decimal("-100"))])
    got = sides(voucher_xml.build(spec))
    check("a negative line posts on the other side, and the supplier takes the rest",
          ("Cr", "Discount", Decimal("100")) in got and ("Dr", "Purchase", Decimal("1000")) in got
          and ("Cr", "Supplier", Decimal("1062")) in got, str(got))
    spec.lines = [voucher_xml.VoucherLine("Purchase", Decimal("500")), voucher_xml.VoucherLine("Purchase", Decimal("-50")),
                  voucher_xml.VoucherLine("Purchase", Decimal("200"))]
    spec.taxes = {"cgst": ("CGST", Decimal("-5"))}
    got = sides(voucher_xml.build(spec))
    check("a Dr and a Cr row of one ledger stay two rows, and a negative tax sits with the supplier",
          ("Dr", "Purchase", Decimal("700")) in got and ("Cr", "Purchase", Decimal("50")) in got
          and ("Cr", "CGST", Decimal("5")) in got and ("Cr", "Supplier", Decimal("645")) in got, str(got))
    j = voucher_xml.VoucherSpec(**{**base, "voucher_type": "Journal"}, party_ledger="Suspense", lines=[
        voucher_xml.VoucherLine("Depreciation", Decimal("500"), is_debit=True),
        voucher_xml.VoucherLine("Plant", Decimal("-500"), is_debit=True)])
    check("in a journal a line's sign is its side",
          {l: s for s, l, _ in sides(voucher_xml.build(j))} == {"Depreciation": "Dr", "Plant": "Cr"})

    c = Company(tally_name="Edit Test Co", gstin="08AAAAA0000A1Z5", state_code="08",
                voucher_types=[{"name": "Purchase", "parent": "Purchase", "numbering": "Manual"},
                               {"name": "Journal", "parent": "Journal", "numbering": "Automatic"}])
    db.add(c)
    db.flush()
    led = {}
    for name, parent in [("SUPPLIER ONE", "Sundry Creditors"), ("PURCHASE @18% GST", "Purchase Accounts"),
                         ("TDS PAYABLE", "Duties & Taxes"), ("CGST", "Duties & Taxes"), ("SGST", "Duties & Taxes")]:
        led[name] = Ledger(company_id=c.id, tally_name=name, parent=parent, is_party="Creditors" in parent,
                           norm_name=matching.normalize(name))
        db.add(led[name])
    doc = Document(filename="edit.pdf", stored_path=str(_tmp / "edit.pdf"), sha256="e" * 64, status="done")
    db.add(doc)
    db.flush()
    buy = led["PURCHASE @18% GST"].id
    e = Entry(document_id=doc.id, company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase",
              invoice_no="E-1", invoice_date=date(2026, 9, 1), party_ledger_id=led["SUPPLIER ONE"].id,
              taxable_total=Decimal("1000.00"), cgst=Decimal("90.00"), sgst=Decimal("90.00"),
              grand_total=Decimal("1180.00"))
    e.lines = [EntryLine(line_no=0, description_raw="Goods A", amount=Decimal("600.00"), ledger_id=buy),
               EntryLine(line_no=1, description_raw="Goods B", amount=Decimal("400.00"), ledger_id=buy)]
    db.add(e)
    db.commit()

    p = er.preview(e.id, db)
    check("each row says the side a positive amount of it sits on, so Dr/Cr can be switched",
          p["line_side"] == "Dr" and [r["natural"] for r in p["rows"]] == ["Cr", "Dr", "Dr", "Dr"], str(p["rows"]))

    gone = e.lines[1].id
    er.update_entry(e.id, EntryUpdate(lines=[LineUpdate(id=gone, delete=True),
                                             LineUpdate(ledger_id=led["TDS PAYABLE"].id, amount=Decimal("-10"))],
                                      learn=False), db)
    db.refresh(e)
    got = sides(voucher_xml.build(er._build_spec(db, e)))
    check("a row removed on the screen takes its line off the entry",
          all(l.id != gone for l in e.lines) and db.get(EntryLine, gone) is None)
    check("a row added as Cr posts as a credit, and the supplier's row balances it",
          ("Cr", "TDS PAYABLE", Decimal("10")) in got and ("Dr", "PURCHASE @18% GST", Decimal("600")) in got
          and ("Cr", "SUPPLIER ONE", Decimal("770")) in got, str(got))
    ls = next(v for v in e.validations if v["code"] == "line_sum")
    check("... and the checks after that save count the lines as they are now",
          ls["severity"] == "error" and Decimal(ls["actual"]) == Decimal("590"), str(ls))

    er.reject(e.id, "wrong bill", db)
    er.reject(e.id, "", db)  # twice: still one reject to undo
    er.restore(e.id, db)
    check("Undo puts a rejected bill back to review", e.status == "pending", e.status)
    e.status = "approved"
    db.commit()
    er.reject(e.id, "", db)
    er.restore(e.id, db)
    check("... and an exported one back to exported", e.status == "approved", e.status)
    try:
        er.restore(e.id, db)
        raise AssertionError("a bill that was not rejected was put back")
    except HTTPException as exc:
        check("only a rejected bill can be put back", exc.status_code == 409, str(exc.detail))

    from app.bridge.main import ui_path
    from app.bridge.services.parse import ParsedInvoice, iso_currency
    p = ParsedInvoice()
    p.put("currency", "₹", 0.9, "llm")
    check("a currency the AI copies as written (₹) is stored as its code, INR",
          p.get("currency") == "INR" and iso_currency("Rs.") == "INR" and iso_currency("rupees") == "INR"
          and iso_currency(None) == "INR" and iso_currency("usd") == "USD", str(p.get("currency")))
    e.currency = "₹"  # as bills read by 1.0.4 were stored
    check("... and a bill already stored with the symbol reads back as INR, so it can't break the screen",
          er._summary(e).currency == "INR")
    db.rollback()
    check("the page data Next.js asks for is served from where its export writes it",
          ui_path(os.path.join("review", "__next.review.__PAGE__.txt")) == os.path.join("review", "__next.review", "__PAGE__.txt")
          and ui_path("__next.__PAGE__.txt") == "__next.__PAGE__.txt" and ui_path("index.html") == "index.html")
    order = [x.document_id for x in er.list_entries(status=None, company_id=None, q=None, sort="upload", limit=1000,
                                                    offset=0, db=db)]
    check("while a batch is read the list keeps the order the bills were given in", order == sorted(order), str(order))

    jr = Entry(document_id=doc.id, company_id=c.id, voucher_type="Journal", voucher_parent="Journal",
               invoice_no="J-1", invoice_date=date(2026, 9, 1), party_ledger_id=led["SUPPLIER ONE"].id,
               grand_total=Decimal("50"))
    jr.lines = [EntryLine(line_no=0, amount=Decimal("50"), ledger_id=led["TDS PAYABLE"].id)]
    db.add(jr)
    db.commit()
    got = sides(voucher_xml.build(er._build_spec(db, jr)))
    check("a journal from a bill posts without asking each line's side: its sign is the side",
          got == [("Dr", "TDS PAYABLE", Decimal("50")), ("Cr", "SUPPLIER ONE", Decimal("50"))], str(got))


def test_real_bills(db) -> None:
    print("\n[ten kinds of real-world bill: each thing they got wrong, fixed where it starts]")
    from app.bridge.models import EntryLine, StockItem
    from app.bridge.routers import entries as er
    from app.bridge.services.parse import ParsedInvoice

    D = Decimal

    def bill(lines=(), **amounts) -> ParsedInvoice:
        p = ParsedInvoice()
        for k, v in amounts.items():
            p.put(k, D(v), 0.72, "llm")
        p.lines = [dict(r) for r in lines]
        return p

    # Surya Glass House 320: the OCR ran "SGST@9% 1354.32" into 971354.3.
    p = bill(taxable_total="15048", cgst="1354.32", sgst="971354.30", round_off="0.36", grand_total="17757")
    p.conflicts.append({"field": "sgst", "text": "971354.30", "image": "1354.32", "value": D("1354.32")})
    pipeline.settle(p)
    check("two readings of an amount: the one that makes the bill add up is kept (SGST 1354.32, not 971354.30)",
          p.get("sgst") == D("1354.32") and "makes the bill add up" in p.conflicts[0]["note"], str(p.conflicts))

    # Shree Krishna 487: CGST read as its rate, the paise columns dropped ("6,683 | 76", "+ | 48").
    p = bill([{"amount": D(x), "gst_rate": 9.0} for x in ("12029", "24572", "16523", "21140")],
             taxable_total="74264", cgst="9", sgst="6683", round_off="48", grand_total="87632")
    p.conflicts.append({"field": "cgst", "text": "9", "image": "6683", "value": D("6683")})
    pipeline.settle(p)
    check("a tax a rupee off its rate on the taxable value is that figure, and what is left under a rupee is the round-off",
          (p.get("cgst"), p.get("sgst"), p.get("round_off")) == (D("6683.76"), D("6683.76"), D("0.48")),
          str([(k, p.get(k)) for k in ("cgst", "sgst", "round_off")]))
    pipeline.line_rates(p)
    check("a line's 9% on a CGST+SGST bill taxed at 18% was the half: the line is 18%",
          {r["gst_rate"] for r in p.lines} == {18.0}, str([r["gst_rate"] for r in p.lines]))

    # Orbit GST/26-27/1534: packing & cartage 50, taxed with the goods; the 50 was read as the round-off.
    p = bill([{"amount": D("3300")}, {"amount": D("450")}, {"amount": D("512")},
              {"amount": D("50"), "charge": True, "description_raw": "PACKING & CARTAGE"}],
             taxable_total="4262", cgst="388.08", sgst="388.08", round_off="50", grand_total="5088")
    pipeline.settle(p)
    check("a charge beside the items is in the taxable value when the tax is on it, and the round-off is -0.16",
          (p.get("taxable_total"), p.get("round_off")) == (D("4312"), D("-0.16")),
          str([(k, p.get(k)) for k in ("taxable_total", "round_off")]))
    p = bill(taxable_total="100", cgst="9", sgst="9", grand_total="150")
    pipeline.settle(p)
    check("a bill that adds up no way is left as read, for the checks to flag", p.get("cgst") == D("9") and not p.conflicts)
    p = bill([{"amount": D("24000")}], taxable_total="24000", cgst="0", sgst="0", grand_total="24000")
    pipeline.line_rates(p)
    check("tax written as 0.00: the line is nil-rated (Gopal Hitkari's cow feed)", p.lines[0]["gst_rate"] == 0.0)

    c = Company(tally_name="Indigo Test Books", gstin="08YYYPY2222Y1Z5", state_code="08",
                voucher_types=[{"name": "Purchase", "parent": "Purchase", "numbering": "Manual"}])
    db.add(c)
    db.flush()
    for name, parent in [("Desert Heritage & Crafts", "Sundry Creditors"), ("Lotus Traders", "Sundry Creditors"),
                         ("ORBIT TRADELINKS", "Sundry Creditors"), ("PURCHASE @18% GST", "Purchase Accounts"),
                         ("Freight & Cartage Charges 18% Gst", "Indirect Expenses"), ("CGST", "Duties & Taxes"),
                         ("SGST", "Duties & Taxes"), ("Rounded Off", "Indirect Expenses")]:
        db.add(Ledger(company_id=c.id, tally_name=name, parent=parent, is_party="Creditors" in parent,
                      norm_name=matching.normalize(name)))
    for name, uom in [("JAR", "pcs"), ("4mm Glass", "sqf"), ("4 MM MIRROR GLASS FT", "FT"), ("MCI TP-1", "KG"),
                      ("Bond 20 GM", "pcs")]:
        db.add(StockItem(company_id=c.id, tally_name=name, uom=uom, norm_name=matching.normalize(name)))
    db.commit()

    feed = matching.match_stock_item(db, c.id, "Dhenu Paushtik Aahar (Cow Feed)", uom="Bag")
    check("cow feed is not the stock item JAR: a 3-letter name is not 'contained' in AAHAR",
          feed["confidence"] < pipeline.ASSIGN_FLOOR, str(feed))
    glass = matching.match_stock_item(db, c.id, "4mm Mirror glass")
    check("4mm Mirror glass is 4 MM MIRROR GLASS FT, not the shorter 4mm Glass",
          glass["name"] == "4 MM MIRROR GLASS FT", str(glass))
    honey = matching.match_stock_item(db, c.id, "MCI NC Honey 05", uom="Ltr")
    check("NC Honey in litres is not MCI TP-1, an item counted in kg", honey["confidence"] < pipeline.ASSIGN_FLOOR,
          str(honey))
    from app.bridge.models import Bought

    for name, uom in [("4 MM MIRROR GLASS", "pcs"), ("Timber Wood", "CFT")]:
        db.add(StockItem(company_id=c.id, tally_name=name, uom=uom, norm_name=matching.normalize(name)))
    db.add(Ledger(company_id=c.id, tally_name="STORE & SPARES @18%", parent="Purchase Accounts",
                  norm_name=matching.normalize("STORE & SPARES @18%")))
    db.add_all([Bought(company_id=c.id, party="SURYA GLASS HOUSE", item="4 MM MIRROR GLASS FT",
                       ledger="PURCHASE @18% GST", times=6),
                Bought(company_id=c.id, party="SHREE KRISHNA ENTERPRISES", item="Timber Wood",
                       ledger="PURCHASE @18% GST", times=41),
                Bought(company_id=c.id, party="ORBIT TRADELINKS", item="Bond 20 GM",
                       ledger="STORE & SPARES @18%", times=4)])
    db.commit()
    glass = matching.match_stock_item(db, c.id, "4mm Mirror glass", party="SURYA GLASS HOUSE")
    check("of three near-alike glass items, the one you always buy from this supplier (the FT one)",
          glass["name"] == "4 MM MIRROR GLASS FT" and "bought from this supplier" in glass["reason"], str(glass))
    for name, times in (("Magnet Catcher M-2 Brown", 3), ("Magnet Catcher M-3 Brown", 1)):
        db.add(StockItem(company_id=c.id, tally_name=name, uom="pcs", norm_name=matching.normalize(name)))
        db.add(Bought(company_id=c.id, party="PRIME SUPPLIERS", item=name, ledger="PURCHASE @18% GST", times=times))
    db.commit()
    magnet = matching.match_stock_item(db, c.id, "Magnet catcher no 3 brown", party="PRIME SUPPLIERS")
    check("history breaks ties, never names: 'no 3' is M-3, though M-2 was bought more often",
          magnet["name"] == "Magnet Catcher M-3 Brown", str(magnet))
    for name in ("VELCRO PAD 5''", "Velcro Pad 7''"):
        db.add(StockItem(company_id=c.id, tally_name=name, uom="pcs", norm_name=matching.normalize(name)))
    db.add(Bought(company_id=c.id, party="ORBIT TRADELINKS", item="Velcro Pad 7''", ledger="PURCHASE @18% GST",
                  times=2))
    db.commit()
    pad = matching.match_stock_item(db, c.id, 'VELCRO PAD 5" THIN M10 (ACMEX)', party="ORBIT TRADELINKS")
    check("a size on the bill (5\") is not overruled by an item bought before in another size (7'')",
          pad["name"] == "VELCRO PAD 5''", str(pad))
    p = bill([{"quantity": D("91.48"), "rate": D("560"), "amount": D("12029")},
              {"quantity": D("37.73"), "rate": D("660"), "amount": D("24572")},
              {"quantity": D("10"), "rate": D("47.74"), "amount": D("477.40")}])
    pipeline.line_quantities(p)
    check("a quantity one digit off what its amount and rate give is that figure (91.48 -> 21.48 CFT)",
          [r["quantity"] for r in p.lines] == [D("21.48"), D("37.23"), D("10")] and len(p.conflicts) == 2,
          str([r["quantity"] for r in p.lines]))
    p = bill([{"quantity": D("50"), "rate": D("560"), "amount": D("12029")}])
    pipeline.line_quantities(p)
    check("... but not one that is more than a digit off: that is left for you to check",
          p.lines[0]["quantity"] == D("50") and not p.conflicts)
    rows = matching.match_stock_item(db, c.id, "Small Medium Big Large", party="SHREE KRISHNA ENTERPRISES")
    check("rows named only by size, from a supplier who sells you one thing, are that thing (Timber Wood)",
          rows["name"] == "Timber Wood" and rows["confidence"] >= pipeline.ASSIGN_FLOOR, str(rows))
    bond_item = db.query(StockItem).filter_by(company_id=c.id, tally_name="Bond 20 GM").one()
    probe = Entry(company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase")
    check("an item posts to the ledger it goes to in your own vouchers (STORE & SPARES @18%)",
          er._default_line_ledger(db, probe, 18.0, bond_item) == "STORE & SPARES @18%")
    check("... but not when this bill charges it at another rate",
          er._default_line_ledger(db, probe, 5.0, bond_item) != "STORE & SPARES @18%")

    new = pipeline.match_party(db, c.id, "Gopal Hitkari", "08VVVPV5555V1Z8", "Creditors")
    check("a supplier the books do not have is left to you, not guessed from a 58% name",
          new["ledger_id"] is None and new["confidence"] == 0.0, str(new))

    real = tally.vouchers_in_period
    other = {"date": date(2026, 5, 9), "voucher_number": "320", "voucher_type": "Purchase", "party": "Nav Shakti Art",
             "party_gstin": None, "reference": "320", "bill_refs": [], "amount": D("94105"), "is_optional": False,
             "master_id": "1"}
    tally.vouchers_in_period = lambda *a, **k: [other]
    try:
        seen = dedupe.check_tally(Entry(invoice_no="320", invoice_date=date(2026, 9, 21), grand_total=D("17757")),
                                  "Indigo Test Books", "SURYA GLASS HOUSE", fresh=True)
    finally:
        tally.vouchers_in_period = real
    check("another supplier's bill with the same number (320) and another amount is not flagged",
          seen["checked"] and not seen["findings"], str(seen))

    doc = Document(filename="orbit.pdf", stored_path=str(_tmp / "orbit.pdf"), sha256="a" * 64, status="done")
    db.add(doc)
    db.flush()
    bond = db.query(StockItem).filter_by(company_id=c.id, tally_name="Bond 20 GM").one()
    e = Entry(document_id=doc.id, company_id=c.id, voucher_type="Purchase", voucher_parent="Purchase",
              invoice_no="GST/26-27/1534", invoice_date=date(2026, 9, 25),
              party_ledger_id=db.query(Ledger).filter_by(company_id=c.id, tally_name="ORBIT TRADELINKS").one().id,
              taxable_total=D("3350"), cgst=D("301.50"), sgst=D("301.50"), grand_total=D("3953"),
              round_off=D("0"))
    e.lines = [EntryLine(line_no=0, description_raw="KWIK BOND 20GM", quantity=300, uom="PCS", rate=D("11"),
                         amount=D("3300"), gst_rate=18.0, stock_item_id=bond.id),
               EntryLine(line_no=1, description_raw="PACKING & CARTAGE", amount=D("50"), gst_rate=18.0,
                         ledger_id=db.query(Ledger).filter_by(company_id=c.id,
                                                              tally_name="Freight & Cartage Charges 18% Gst").one().id)]
    db.add(e)
    db.commit()
    xml = voucher_xml.build(er._build_spec(db, e))
    check("items post as items and a charge as its ledger beside them, as keyed by hand in Tally",
          xml.count("<ALLINVENTORYENTRIES.LIST>") == 1 and "<STOCKITEMNAME>Bond 20 GM</STOCKITEMNAME>" in xml
          and ("Dr", "Freight &amp; Cartage Charges 18% Gst", D("50.00")) in sides(xml), str(sides(xml)))

    import threading

    from app.bridge.routers import documents as docs_router

    queued = []
    for i in range(4):
        d = Document(filename=f"q{i}.pdf", stored_path=str(_tmp / f"q{i}.pdf"), sha256=f"{i}" * 64, status="queued")
        db.add(d)
        db.flush()
        queued.append(d.id)
    db.commit()
    busy, peak, real_read = [0], [0], pipeline.process_document

    def slow_read(db_, doc, **_kw):
        busy[0] += 1
        peak[0] = max(peak[0], busy[0])
        time.sleep(0.15)
        busy[0] -= 1
        doc.status = "extracted"
        db_.commit()

    pipeline.process_document = slow_read
    try:
        threads = [threading.Thread(target=docs_router._process, args=(i, None, False)) for i in queued]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        pipeline.process_document = real_read
    check("bills uploaded one by one are read one at a time, waiting before they hold a database connection",
          peak[0] == 1 and all(db.get(Document, i).status == "extracted" for i in queued if not db.expire_all()),
          f"at most {peak[0]} at once")


def test_bill_links(db) -> None:
    print("\n[each bill a keypress away from its voucher in Tally]")
    import hashlib
    import urllib.error
    import urllib.request

    from app.bridge.services import bill_links

    blob = b"%PDF-1.4 a bill"
    digest = hashlib.sha256(blob).hexdigest()
    path = _tmp / f"{digest}.pdf"
    path.write_bytes(blob)
    doc = Document(filename="Prime 6783.pdf", stored_path=str(path), sha256=digest, status="done")
    db.add(doc)
    db.commit()

    check("links are off until the Tally add-on is loaded", bill_links.url_for(doc) is None)
    real = settings.bill_links, settings.bill_link_port
    settings.bill_links, settings.bill_link_port = True, 9098
    try:
        url = bill_links.url_for(doc)
        check("a bill's link names it by its fingerprint, on this PC when Tally runs here",
              url == f"http://127.0.0.1:9098/bill/{digest}.pdf", str(url))
        spec = voucher_xml.VoucherSpec(company="X", voucher_type="Purchase", date=date(2026, 9, 14),
                                       party_ledger="PRIME SUPPLIERS", lines=[voucher_xml.VoucherLine("P", Decimal("100"))],
                                       bill_link=url)
        xml = voucher_xml.build(spec)
        check("the voucher carries the link in its own field, as Tally exports a UDF",
              f'<UDF:TIBBILLLINK.LIST DESC="`TIBBillLink`" ISLIST="YES" TYPE="String" INDEX="45001">'
              f'<UDF:TIBBILLLINK DESC="`TIBBillLink`">{url}</UDF:TIBBILLLINK></UDF:TIBBILLLINK.LIST>' in xml)
        check("... and no link field at all with links off",
              "UDF:" not in voucher_xml.build(voucher_xml.VoucherSpec(**{**spec.__dict__, "bill_link": None})))
        check("the bill server starts", bill_links.start() is None)
        with urllib.request.urlopen(url, timeout=5) as r:
            got, kind = r.read(), r.headers["Content-Type"]
        check("the link opens that bill, and only that bill", got == blob and kind == "application/pdf", kind)
        refused = []
        for bad in (f"/bill/{'0' * 64}.pdf", "/bill/../tally.db", f"/bill/{digest[:40]}", "/api/entries", "/"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:9098{bad}", timeout=5)
            except urllib.error.HTTPError as exc:
                refused.append(exc.code)
        check("nothing else is served: no other file, no listing, no way out of the bills",
              refused == [404] * 5, str(refused))
        check("the Tally add-on declares the same field and opens it with Ctrl+Alt+O",
              "TIBBillLink : String : 45001" in bill_links.ADDON and "Ctrl + Alt + O" in bill_links.ADDON)
    finally:
        bill_links.stop()
        settings.bill_links, settings.bill_link_port = real


def test_settings(db) -> None:
    print("\n[settings]")
    from app.bridge.models import Setting
    from app.bridge.routers import system as system_router

    system_router.put_settings({"ollama_model": settings.ollama_model, "auto_clean_confidence": 0.95}, db=db)
    keys = {s.key for s in db.query(Setting)}
    check("saving Settings stores only what you changed, so upgrades still reach the rest",
          "auto_clean_confidence" in keys and "ollama_model" not in keys, str(keys))
    settings.auto_clean_confidence = 0.90
    db.query(Setting).delete()
    db.commit()


def main() -> int:
    if not FIXTURE.exists():
        print(f"missing fixture: {FIXTURE}")
        return 2
    init_db()
    db = SessionLocal()
    try:
        test_primitives()
        parsed = test_parse()
        test_validations(parsed)
        test_voucher_types()
        company = seed(db)
        test_matching(db, company)
        entry = test_ingest(db, company)
        test_payment_shape(db, company)
        test_local_duplicates(db, company, entry)
        test_tally_duplicates(db, entry)
        test_voucher_xml(db, entry)
        test_approval_gates(db, entry)
        test_hindi_and_vision(db, company)
        test_background_ai(db, company)
        test_bill_details()
        test_companies(db, company)
        test_batches(db)
        test_fake_tally_books(db)
        test_tally_line(db, company)
        test_tally_replies(db, company)
        test_books_like_yours(db)
        test_voucher_editing(db)
        test_real_bills(db)
        test_bill_links(db)
        test_settings(db)
        test_share_limits()
        test_limits()
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
