"""The Keeper reads every company's books from a stand-in Tally.  python -m app.keeper.tests.test_books"""

from __future__ import annotations

import datetime as dt

from app.keeper import books
from app.keeper.tests import stand_in_tally as tally

TODAY = dt.date(2025, 10, 21)


def main() -> None:
    srv, port = tally.start()
    try:
        r = books.Reader(port=port)
        cos = r.companies()
        assert [c["name"] for c in cos] == ["Saffron Impex", "Rao & Sons"], cos
        snaps = {c["name"]: r.snapshot(c, TODAY) for c in cos}
    finally:
        srv.shutdown()

    v = snaps["Saffron Impex"]
    assert v["company"]["fy_from"] == "2025-04-01" and v["company"]["fy_to"] == "2026-03-31"
    nature = {g["name"]: g["nature"] for g in v["groups"]}
    assert nature == {"Sundry Debtors": "assets", "Sundry Creditors": "liabilities", "Sales Accounts": "income",
                      "Indirect Expenses": "expenses", "Bank Accounts": "assets"}, nature
    assert {g["parent"] for g in v["groups"]} == {"Primary", "Current Assets"}     # '&#4;' gone
    led = {x["name"]: x for x in v["ledgers"]}
    assert led["Kiran Traders"]["opening"] == 500000 and led["Sales"]["closing"] == -1000000
    assert led["Output GST"]["group"] == "Duties & Taxes"

    by = {x["number"]: x for x in v["vouchers"]}
    assert set(by) == {"S-1", "R-1"}, by                                           # cancelled S-2 left out
    s1 = by["S-1"]
    assert sum(x["amount"] for x in s1["lines"]) == 0, s1["lines"]                 # invoice mode balances
    assert {x["ledger"]: x["amount"] for x in s1["lines"]} == {"Kiran Traders": 1180000, "Output GST": -180000,
                                                               "Sales": -1000000}
    assert s1["items"] == [{"item": "Steel Rod", "qty": "12 Nos", "rate": "833.33/Nos", "amount": -1000000}]
    assert sum(x["amount"] for x in by["R-1"]["lines"]) == 0

    # OB/1 paid by R-1; S-1 open, due 45 days after 5 Sep
    assert v["bills"] == [{"ledger": "Kiran Traders", "name": "S-1", "date": "2025-09-05", "due": "2025-10-20",
                           "amount": 1180000}], v["bills"]

    s = snaps["Rao & Sons"]
    assert [x["number"] for x in s["vouchers"]] == ["1"] and s["vouchers"][0]["optional"]
    assert s["vouchers"][0]["narration"] == "Rent & lights"
    assert s["company"]["books_from"] == "2025-04-01"
    assert {x["name"] for x in s["ledgers"]} == {"Rent", "Cash"} and not s["bills"]

    # every request was a read, each named its company, voucher reads went month by month
    assert all("<TALLYREQUEST>Export</TALLYREQUEST>" in q for q in tally.REQUESTS)
    vreqs = [q for q in tally.REQUESTS if "<ID>KeeperVouchers</ID>" in q]
    assert len(vreqs) == 19 + 7, len(vreqs)          # Saffron: Apr 2024 - Oct 2025; Rao: Apr - Oct 2025
    assert any("<SVCURRENTCOMPANY>Rao &amp; Sons</SVCURRENTCOMPANY>" in q for q in vreqs)
    assert all('<SVFROMDATE TYPE="Date">' in q for q in vreqs)           # a period Tally takes as one
    sync_seals_every_company(port_again())
    people_route()
    print("test_books: all checks passed")


def people_route() -> None:
    from app.keeper import sync
    from app.office import roster
    roster.set_current(roster.Roster({"users": [{"id": 1, "email": "a@x.in", "name": "Asha", "caps": ["books"], "status": "active"},
                                                {"id": 2, "email": "b@x.in", "name": "Bo", "caps": [], "status": "active"}],
                                      "devices": [{"id": "d1", "user_id": 1, "kind": "viewer", "name": "Phone", "status": "approved",
                                                   "sign_pub": "SECRET", "wrap_pub": "SECRET"}]}, 0.0))
    try:
        got = sync.people()
    finally:
        roster.set_current(None)
    assert got["known"] and [p["name"] for p in got["people"]] == ["Asha", "Bo"], got
    dev = got["people"][0]["devices"]
    assert [{k: d[k] for k in ("name", "kind", "status")} for d in dev] == [
        {"name": "Phone", "kind": "viewer", "status": "approved"}], got
    assert dev[0]["key"].startswith("No key yet"), got           # its books key, in words
    assert "SECRET" not in str(got)
    from app.keeper.page import HTML
    assert "Who has access" in HTML and "Can sign in, nothing else." in HTML


def port_again() -> int:
    srv, port = tally.start()
    return port


def sync_seals_every_company(port: int) -> None:
    import gzip, json, os, tempfile
    from pathlib import Path
    os.environ["ASSURANCE_OFFICE_DIR"] = tempfile.mkdtemp(prefix="keeper-test-")
    from app.keeper import sync, ukeep
    from app.office import keys
    st = sync.run(books.Reader(port=port), None, TODAY)
    assert st["error"] is None and st["step"] is None, st
    box = Path(os.environ["ASSURANCE_OFFICE_DIR"]) / "keeper" / "outbox" / "office"
    files = sorted(p.relative_to(box).as_posix() for p in box.rglob("*.ukeep"))
    ids = json.loads((box.parents[1] / "companies.json").read_text())
    assert len(files) == 5 and "index.ukeep" in files, files
    assert not any("Saffron" in f or "Rao" in f for f in files)              # names stay out of paths
    hdr, data = ukeep.decrypt((box / "index.ukeep").read_bytes(), keys.dek)
    index = json.loads(gzip.decompress(data))
    assert [c["name"] for c in index["companies"]] == ["Rao & Sons", "Saffron Impex"]      # by name
    hdr, data = ukeep.decrypt((box / f"current/{ids['Rao & Sons']}.ukeep").read_bytes(), keys.dek)
    assert hdr["kind"] == "books" and json.loads(gzip.decompress(data))["company"]["name"] == "Rao & Sons"
    again = sync.run(books.Reader(port=port), None, TODAY)                     # same ids next time
    assert [c["cid"] for c in again["companies"]] == [c["cid"] for c in st["companies"]]
    # A company closed in Tally stays listed with its last read; the one still open is read again
    del tally.COMPANIES["Rao & Sons"]
    try:
        later = sync.run(books.Reader(port=port), None, TODAY)
    finally:
        tally.COMPANIES["Rao & Sons"] = "20250401"
    rows = {c["name"]: c for c in later["companies"]}
    assert set(rows) == {"Saffron Impex", "Rao & Sons"}, rows
    assert rows["Rao & Sons"]["read_at"] == {c["name"]: c for c in again["companies"]}["Rao & Sons"]["read_at"]
    hdr, data = ukeep.decrypt((box / "index.ukeep").read_bytes(), keys.dek)
    assert [c["name"] for c in json.loads(gzip.decompress(data))["companies"]] == ["Rao & Sons", "Saffron Impex"]
    quick_reads(port)
    unchanged_and_outbox(port)
    backups_round_trip(port)
    notices_told()
    no_double_entries()
    exporters_outstanding()
    gone = sync.run(books.Reader(port=1), None, TODAY)                          # Tally closed: said, with the step
    assert gone["error"].startswith("TallyUnavailable") and gone["step"] == "the list of companies", gone


def quick_reads(port: int) -> None:
    from app.keeper import sync
    vreq = lambda: [q for q in tally.REQUESTS if "<ID>KeeperVouchers</ID>" in q]      # noqa: E731
    ids = sync.company_ids([])
    vi = ids["Saffron Impex"]

    n = len(vreq())                                           # nothing saved in Tally since: no voucher reads
    st = sync.run(books.Reader(port=port), None, TODAY)
    assert st["error"] is None and st["kind"] == "quick" and len(vreq()) == n, st

    # a new voucher, back-dated into last year: one request, for just the altered ones
    tally.VOUCHERS[("Saffron Impex", "202406")] = (
        '<VOUCHER><DATE>20240610</DATE><GUID>g-j1</GUID><ALTERID>4</ALTERID><VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>'
        '<VOUCHERNUMBER>J-1</VOUCHERNUMBER><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>'
        '<ALLLEDGERENTRIES.LIST><LEDGERNAME>HDFC Bank</LEDGERNAME><AMOUNT>-100.00</AMOUNT></ALLLEDGERENTRIES.LIST>'
        '<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>100.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>')
    tally.ALTER["Saffron Impex"] = 4
    st = sync.run(books.Reader(port=port), None, TODAY)
    new = vreq()[n:]
    assert st["error"] is None and len(new) == 2, len(new)                     # one per financial year
    assert all("KeeperAltered" in q and "&gt; 3<" in q for q in new)
    assert '<SVFROMDATE TYPE="Date">20240401<' in new[0] and '<SVTODATE TYPE="Date">20250331<' in new[0]
    row = {c["name"]: c for c in st["companies"]}
    assert row["Saffron Impex"]["how"] == "1 changed voucher" and row["Rao & Sons"]["how"] == "nothing changed in Tally", row
    snap = sync.kept_snapshot(vi)
    assert [v["number"] for v in snap["vouchers"]] == ["J-1", "S-1", "R-1"], snap["vouchers"]   # by date

    # deleted in Tally: the number moves but nothing is altered, so a full read finds it gone
    del tally.VOUCHERS[("Saffron Impex", "202406")]
    tally.ALTER["Saffron Impex"] = 5
    m = len(vreq())
    st = sync.run(books.Reader(port=port), None, TODAY)
    assert st["error"] is None and len(vreq()) - m == 2 + 19, len(vreq()) - m       # the filtered two, then every month
    assert [v["number"] for v in sync.kept_snapshot(vi)["vouchers"]] == ["S-1", "R-1"]
    assert {c["name"]: c for c in st["companies"]}["Saffron Impex"]["how"].startswith("read everything: something was deleted")

    # Read everything now: every month, whatever changed
    m = len(vreq())
    st = sync.run(books.Reader(port=port), None, TODAY, full=True)
    assert st["kind"] == "full" and len(vreq()) - m == 19 + 7

    # A Tally that won't take the filter: the long way, and said
    tally.FILTER, tally.ALTER["Saffron Impex"] = "refuse", 6
    try:
        st = sync.run(books.Reader(port=port), None, TODAY)
    finally:
        tally.FILTER = "ok"
    how = {c["name"]: c for c in st["companies"]}["Saffron Impex"]["how"]
    assert st["error"] is None and how.startswith("read everything: Tally didn't take the change filter"), how
    assert [v["number"] for v in sync.kept_snapshot(vi)["vouchers"]] == ["S-1", "R-1"]

    # One that ignores it: every voucher of each year comes back, the books stay right, and it is said
    tally.FILTER, tally.ALTER["Saffron Impex"] = "ignore", 7
    try:
        st = sync.run(books.Reader(port=port), None, TODAY)
    finally:
        tally.FILTER = "ok"
    how = {c["name"]: c for c in st["companies"]}["Saffron Impex"]["how"]
    assert "Tally ignored the change filter" in how, how
    assert [v["number"] for v in sync.kept_snapshot(vi)["vouchers"]] == ["S-1", "R-1"]

    # An error reply in the middle (a company closed mid-sync): the sync stops, nothing empty is sent
    from app.office import settings
    current = settings.office_dir() / "keeper" / "outbox" / "office" / "current" / f"{vi}.ukeep"
    before = current.read_bytes()
    tally.LEDGERS_FAIL, tally.ALTER["Saffron Impex"] = True, 8
    try:
        st = sync.run(books.Reader(port=port), None, TODAY)
    finally:
        tally.LEDGERS_FAIL = False
    assert st["error"] and "Company not open" in st["error"] and "ledgers" in st["step"], st
    assert current.read_bytes() == before and len(sync.kept_snapshot(vi)["ledgers"]) == 4


def unchanged_and_outbox(port: int) -> None:
    import httpx
    from app.keeper import sync
    from app.office import settings
    box = settings.office_dir() / "keeper" / "outbox"
    sync.run(books.Reader(port=port), None, TODAY)
    for f in box.rglob("*.ukeep"):
        f.unlink()
    st = sync.run(books.Reader(port=port), None, TODAY)               # nothing changed in Tally since
    assert st["error"] is None, st
    waiting = sorted(f.relative_to(box).as_posix() for f in box.rglob("*.ukeep"))
    assert waiting == ["office/index.ukeep"], waiting                 # the books already sent today aren't sent again

    # the outbox: everything goes, the index last
    for n in range(6):
        (box / "office" / "daily" / f"x{n}.ukeep").parent.mkdir(parents=True, exist_ok=True)
        (box / "office" / "daily" / f"x{n}.ukeep").write_bytes(b"x")
    order = []

    class Fake:
        def post(self, path, body):
            return {"url": body["path"]}

    class Ok:
        def raise_for_status(self):
            pass

    real = httpx.put
    httpx.put = lambda url, **kw: (order.append(url), Ok())[1]
    try:
        assert sync.send_outbox(Fake()) == 7
    finally:
        httpx.put = real
    assert order[-1] == "office/index.ukeep" and len(order) == 7 and not list(box.rglob("*.ukeep")), order


def backups_round_trip(port: int) -> None:
    import functools, http.server, os, tempfile, threading
    from pathlib import Path
    from app.keeper import backups, sync
    from app.office import settings
    tally_dir = Path(tempfile.mkdtemp(prefix="tally-backup-"))
    original = os.urandom(3 * (1 << 20) + 12345)                      # several 1 MiB chunks
    (tally_dir / "TSDBK1800_10000.001").write_bytes(original)
    (tally_dir / "notes.txt").write_text("not a backup")
    s = settings.load()
    s["tally_backup_dir"] = str(tally_dir)
    settings.save(s)

    st = sync.run(books.Reader(port=port), None, TODAY)
    assert st["error"] is None and st["backups_sent"] == 1 and not st["backup"]["old"], st
    again = sync.run(books.Reader(port=port), None, TODAY)
    assert again["backups_sent"] == 0                                 # unchanged: not sent twice
    box = settings.office_dir() / "keeper" / "outbox"
    row = backups.manifest()[0]
    assert (box / row["path"]).exists() and (box / "office" / "backups.ukeep").exists()
    assert original not in (box / row["path"]).read_bytes()[:1 << 20]  # sealed

    # restore through a stand-in R2 serving the outbox
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(box)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cloud = type("C", (), {"post": lambda self, path, body: {"url": f"http://127.0.0.1:{srv.server_address[1]}/{body['path']}"}})()
    try:
        into = Path(tempfile.mkdtemp(prefix="restore-")) / "here"
        out = backups.restore(cloud, row["path"], str(into))
        assert out.read_bytes() == original and out.name == "TSDBK1800_10000.001"
        assert [p.name for p in into.iterdir()] == ["TSDBK1800_10000.001"]          # nothing left behind
        try:
            backups.restore(cloud, row["path"], str(into))                 # not empty now: refused
            raise AssertionError("restored over files")
        except ValueError as exc:
            assert "isn't empty" in str(exc)
        assert len(backups.remote_manifest(cloud)) == 1
    finally:
        srv.shutdown()


def notices_told() -> None:
    import base64, copy, json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.keeper import notices
    posts = []
    cloud = type("C", (), {"post": lambda self, path, body: posts.append(body) or {"sent": 1}})()
    dek = AESGCM.generate_key(256)
    V = lambda id, amt: {"id": id, "optional": False, "type": "Sales", "number": id, "party": "Kiran",   # noqa: E731
                         "lines": [{"ledger": "Kiran", "amount": amt}, {"ledger": "Sales", "amount": -amt}]}
    snap = {"company": {"cid": "c000000000001", "name": "Rao & Sons"},
            "bills": [{"ledger": "Kiran", "name": "S-1", "due": "2025-10-01", "amount": 1180000}],
            "vouchers": [V("old", 50_00_000_00)]}
    ok = {"error": None, "backup": {"folder": None}}
    day = dt.date(2025, 10, 21)
    assert notices.after_sync(cloud, dict(ok), [snap], "k1", dek, day) == 0 and not posts     # first sync: remembered only

    later = copy.deepcopy(snap)
    later["bills"].append({"ledger": "Steel Co", "name": "P-9", "due": "2025-10-20", "amount": -250000})
    later["vouchers"] += [V("big", 2_00_000_00), V("small", 500_00)]
    assert notices.after_sync(cloud, dict(ok), [later], "k1", dek, day) == 2
    kinds = [p["kind"] for p in posts]
    assert kinds == ["overdue", "big"], kinds
    opened = [json.loads(AESGCM(dek).decrypt(base64.b64decode(p["nonce"]), base64.b64decode(p["ct"]), b"ua-push:v1:k1"))
              for p in posts]
    assert opened[0]["body"] == "Steel Co: ₹2,500 to pay (1 bill)", opened[0]
    assert "Sales big" in opened[1]["body"] and "₹2,00,000" in opened[1]["body"], opened[1]
    assert all("Steel" not in json.dumps(p) for p in posts)                  # sealed: nothing readable on the way

    posts.clear()
    assert notices.after_sync(cloud, dict(ok), [later], "k1", dek, day) == 0                  # told once
    assert notices.after_sync(cloud, {"error": "TallyUnavailable: closed"}, [], None, None, day) == 1
    assert posts[-1]["kind"] == "alert" and "Tally" not in posts[-1]["body"]
    assert notices.after_sync(cloud, {"error": "again"}, [], None, None, day) == 1 - 1        # once per failure


def no_double_entries() -> None:
    import xml.etree.ElementTree as ET
    L = lambda tag, led, amt: f"<{tag}><LEDGERNAME>{led}</LEDGERNAME><AMOUNT>{amt}</AMOUNT></{tag}>"   # noqa: E731
    item = ("<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Rod</STOCKITEMNAME><AMOUNT>100.00</AMOUNT>"
            + L("ACCOUNTINGALLOCATIONS.LIST", "Sales", "100.00") + "</ALLINVENTORYENTRIES.LIST>")
    cases = {
        # an export that fills both lists with the same lines, the sales line in both and in the item
        "both lists": L("ALLLEDGERENTRIES.LIST", "Kiran", "-118.00") + L("ALLLEDGERENTRIES.LIST", "GST", "18.00")
        + L("ALLLEDGERENTRIES.LIST", "Sales", "100.00")
        + L("LEDGERENTRIES.LIST", "Kiran", "-118.00") + L("LEDGERENTRIES.LIST", "GST", "18.00") + item,
        # an invoice: party and tax in LEDGERENTRIES, the sales ledger inside the item line
        "invoice": L("LEDGERENTRIES.LIST", "Kiran", "-118.00") + L("LEDGERENTRIES.LIST", "GST", "18.00") + item,
        # an accounting voucher
        "accounting": L("ALLLEDGERENTRIES.LIST", "Kiran", "-118.00") + L("ALLLEDGERENTRIES.LIST", "Bank", "118.00"),
    }
    for name, body in cases.items():
        v = books.Reader._voucher(ET.fromstring(f"<VOUCHER><GUID>g</GUID>{body}</VOUCHER>"))
        ledgers = sorted(x["ledger"] for x in v["lines"])
        assert sum(x["amount"] for x in v["lines"]) == 0 and len(ledgers) == len(set(ledgers)), (name, v["lines"])
        assert {x["ledger"]: x["amount"] for x in v["lines"]}["Kiran"] == 11800, (name, v["lines"])

    # the same voucher sent twice (two months' reads, an alteration): listed once, the latest
    import datetime as dt
    srv, port = tally.start()
    dup = tally.VOUCHERS[("Saffron Impex", "202509")]
    tally.VOUCHERS[("Saffron Impex", "202508")] = dup.replace("<ALTERID>1</ALTERID>", "<ALTERID>0</ALTERID>")
    try:
        snap = books.Reader(port=port).snapshot({"name": "Saffron Impex", "books_from": "2024-04-01"}, TODAY)
    finally:
        del tally.VOUCHERS[("Saffron Impex", "202508")]
        srv.shutdown()
    ids = [v["id"] for v in snap["vouchers"]]
    assert len(ids) == len(set(ids)) and ids.count("g-s1") == 1, ids
    assert next(v for v in snap["vouchers"] if v["id"] == "g-s1")["alter"] == 1


def exporters_outstanding() -> None:
    # Tally's foreign-currency amounts: the rupees after the "="
    assert books.paise("-$1,200.00 @ ₹ 82.50/$ = -₹ 99,000.00") == 9900000
    assert books.paise("-1200.00 USD @ 82.50 INR/USD = -99000.00 INR") == 9900000
    assert books.paise("1,234.50") == -123450 and books.paise("") == 0

    # A buyer whose receipts don't pair with its invoices: two invoices, a receipt against a
    # reference of its own. Tally's balance is what's owed; one side only; the newest unpaid.
    V = lambda id, date, amt, ref, kind="New Ref", days=60: {   # noqa: E731
        "id": id, "date": date, "type": "Sales" if amt > 0 else "Receipt", "number": id, "optional": False,
        "lines": [{"ledger": "Lumen SRL", "amount": amt, "bills": [[ref, kind, days, amt]]},
                  {"ledger": "Export Sales" if amt > 0 else "Bank", "amount": -amt}]}
    snap = {"company": {"books_from": "2023-04-01"},
            "groups": [{"name": "Sundry Debtors", "parent": "Current Assets"}, {"name": "Current Assets", "parent": "Primary"}],
            "ledgers": [{"name": "Lumen SRL", "group": "Sundry Debtors", "closing": 600000},
                        {"name": "Bank", "group": "Bank Accounts", "closing": 0}],
            "opening_bills": [],
            "vouchers": [V("E1", "2024-05-01", 1000000, "E1"), V("E2", "2025-01-10", 400000, "E2"),
                         V("R1", "2025-02-01", -800000, "PAY-1", "Agst Ref", 0)]}
    out = books.open_bills(snap)
    assert sum(b["amount"] for b in out) == 600000 and all(b["amount"] > 0 for b in out), out   # one side, Tally's figure
    assert [(b["name"], b["amount"], b["due"]) for b in out] == [("Sales E1", 200000, "2024-06-30"), ("Sales E2", 400000, "2025-03-11")], out

    # When the references do pair up, Tally's own bills are used as they are
    snap["vouchers"][2] = V("R1", "2025-02-01", -800000, "E1", "Agst Ref", 0)
    out = books.open_bills(snap)
    assert [(b["name"], b["amount"]) for b in out] == [("E1", 200000), ("E2", 400000)], out

    # The two-hourly task sends the data keys too, even before the first sync went through
    from app.keeper import sync
    from app.office import roster
    sent, saved = [], (sync.settings.mode, sync.settings.load, sync.Identity.load, roster.refresh, sync.keys.sync)
    sync.settings.mode, sync.settings.load = (lambda: "server"), (lambda: {"control_plane": "http://cp.test"})
    sync.Identity.load, roster.refresh = (lambda kind: object()), (lambda cloud: "roster")
    sync.keys.sync = lambda cloud, r: sent.append(r)
    try:
        assert sync.once() == 0 and sent == ["roster"], sent
    finally:
        sync.settings.mode, sync.settings.load, sync.Identity.load, roster.refresh, sync.keys.sync = saved


if __name__ == "__main__":
    main()
