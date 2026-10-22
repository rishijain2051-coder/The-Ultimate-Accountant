"""
Self-check for the matching engine.

Real GSTR-2B data matches so cleanly that the later passes never fire, so the
interesting paths - split bookings, probable matches, amendment supersession,
credit notes and carried-forward manual decisions - are exercised here against
hand-built records instead.

Run it directly:  python -m app.backend.selftest
"""

from app.backend.common import (
    clean_inv, fp_2b, fp_tally, inr, party_sim, rate_is_impossible,
    tails_match, tax_split_problem, to_date, to_float,
)
from app.backend.insights import build_insights
from app.backend.reconciler import reconcile_data

# The bank, party-statement, document-intake and Exports checks live in their
# own modules and are pulled in here so one command runs everything.
from app.backend.selftest_exim import *           # noqa: F401,F403
from app.backend.selftest_recon import *          # noqa: F401,F403

D = to_date


def _g(i, gstin, party, inv_no, date, value, taxable=None, tax=None, **kw):
    taxable = value / 1.18 if taxable is None else taxable
    tax = value - taxable if tax is None else tax
    rec = {
        "id": i, "section": "B2B", "sheet": "B2B", "gstin": gstin, "party": party,
        "inv_no": inv_no, "inv_type": "Regular", "supply_type": "",
        "inv_date": D(date), "inv_val": round(value, 2), "abs_val": abs(value),
        "pos": "Rajasthan", "pos_state": "Rajasthan", "rev_charge": "No",
        "taxable": round(taxable, 2), "igst": 0.0,
        "cgst": round(tax / 2, 2), "sgst": round(tax / 2, 2), "cess": 0.0,
        "tax": round(tax, 2), "rate": 18.0, "rate_slab": 18.0,
        "pct_applicable": 100.0, "period": "Apr'25", "filing_date": D("2025-05-11"),
        "itc_avail": "Yes", "itc_eligible": True, "reason": "", "irn": "",
        "irn_date": None, "e_invoice": False, "source": "", "source_file": "t.xlsx",
        "orig_inv_no": "", "orig_inv_date": None, "month_key": D(date).strftime("%Y-%m"),
        "fy": 2025, "flags": [], "note": "", "source_row": i + 3,
    }
    rec.update(kw)
    return rec


def _t(i, party, vch_no, date, amt, vch_type="PURCHASE (GST)", **kw):
    rec = {
        "id": i, "date": D(date), "party": party, "party_core": party.upper(),
        "gstin": "", "vch_type": vch_type, "vch_no": vch_no, "amt": round(amt, 2),
        "debit": 0.0, "credit": round(amt, 2), "month_key": D(date).strftime("%Y-%m"),
        "fy": 2025, "narration": "", "flags": [], "note": "", "source_row": i + 6,
        "source_file": "t.xlsx",
    }
    rec.update(kw)
    return rec


ENTITY = {"entity_name": "TEST CO", "gstin": "08AMHCK7032W1ZX", "fy_start": 2025,
          "date_range": "1-Apr-25 to 31-Mar-26"}
TODAY = D("2025-12-31")


def _status_of(res, inv_no):
    for r in res["all_records"]:
        if r.get("inv_no_2b") == inv_no:
            return r
    return None


def test_normalisation():
    assert clean_inv("25-26/0376") == "2526376"
    assert clean_inv("25-26/376") == clean_inv("25-26/0376")
    assert clean_inv("SRV/1868/25-26") == "SRV18682526"
    assert tails_match("BW/25-26/0455", "455")
    assert tails_match("INV-1234", "1234")
    assert not tails_match("INV-12", "12")        # too short to be evidence
    assert not tails_match("INV-1234", "5678")
    assert party_sim("M/S PRIME SUPPLIERS", "PRIME SUPPLIERS (JAIPUR)") == 1.0
    assert party_sim("R K HANDICRAFT", "RK HANDICRAFTS") > 0.9
    assert party_sim("ABC STEEL", "XYZ TEXTILE") < 0.4
    assert to_float("(1,234.50)") == -1234.5
    assert to_float("₹ 12,345.67") == 12345.67


def test_tax_validation():
    assert tax_split_problem(1000, 0, 0) is None
    assert tax_split_problem(0, 90, 90) is None
    assert tax_split_problem(500, 90, 90) is not None      # both inter and intra
    assert tax_split_problem(0, 90, 40) is not None        # unequal halves
    assert rate_is_impossible(31.0) is True
    assert rate_is_impossible(18.0) is False


def test_exact_and_value_mismatch():
    gst = [
        _g(0, "08AAAAA0000A1Z5", "ALPHA TRADERS", "A/25-26/101", "2025-04-10", 11800),
        _g(1, "08AAAAA0000A1Z5", "ALPHA TRADERS", "A/25-26/102", "2025-04-12", 23600),
    ]
    tally = [
        _t(0, "ALPHA TRADERS", "A-25-26-101", "2025-04-10", 11800),      # same number, punctuation differs
        _t(1, "ALPHA TRADERS", "A/25-26/102", "2025-04-12", 23000),      # value differs
    ]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    assert _status_of(res, "A/25-26/101")["status"] == "Matched"
    vm = _status_of(res, "A/25-26/102")
    assert vm["status"] == "Value mismatch"
    assert vm["difference"] == 600.0
    assert res["counts"]["matched"] == 1 and res["counts"]["val_mismatch"] == 1


def test_invoice_number_mismatch_uses_tail():
    gst = [_g(0, "08BBBBB0000B1Z5", "BETA WOODS", "BW/25-26/0455", "2025-05-02", 59000)]
    tally = [_t(0, "Beta Woods", "455", "2025-05-02", 59000)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    rec = _status_of(res, "BW/25-26/0455")
    assert rec["status"] == "Invoice no. mismatch"
    assert "tail_match" in rec["flags"]
    assert "invoice tail" in rec["match_basis"]


def test_probable_match_needs_review():
    # Value off by 40 on 47,200 (0.085%), 9 days apart, party spelled differently.
    gst = [_g(0, "08CCCCC0000C1Z5", "GAMMA ART AND CRAFT", "G/778", "2025-06-01", 47200)]
    tally = [_t(0, "Gamma Arts & Crafts", "GA-778-A", "2025-06-10", 47160)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY,
                         settings={"value_pct_tolerance": 0.25, "date_window_days": 45})
    rec = _status_of(res, "G/778")
    assert rec["status"] == "Probable match", rec["status"]
    assert "needs_review" in rec["flags"]
    assert 0.72 <= rec["match_score"] <= 1.0
    assert "Confirm before posting" in rec["note_2b"]


def test_split_booking():
    gst = [_g(0, "08DDDDD0000D1Z5", "DELTA TIMBER", "DT/900", "2025-07-05", 100000)]
    tally = [
        _t(0, "DELTA TIMBER", "DT-900-A", "2025-07-05", 60000),
        _t(1, "DELTA TIMBER", "DT-900-B", "2025-07-06", 40000),
        _t(2, "DELTA TIMBER", "DT-901", "2025-07-20", 15000),
    ]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    rec = _status_of(res, "DT/900")
    assert rec["status"] == "Split booking", rec["status"]
    assert rec["amt_tally"] == 100000.0
    assert len(rec["parts"]) == 2
    assert rec["difference"] == 0.0
    # The unrelated third voucher must stay one-sided, not be swallowed.
    assert res["counts"]["tally_only"] == 1


def test_amendment_supersedes_original():
    gst = [
        _g(0, "08EEEEE0000E1Z5", "EPSILON MILLS", "EM/55", "2025-08-01", 11800),
        _g(1, "08EEEEE0000E1Z5", "EPSILON MILLS", "EM/55", "2025-08-01", 23600,
           section="B2BA", orig_inv_no="EM/55", orig_inv_date=D("2025-08-01"),
           flags=["amended"]),
    ]
    tally = [_t(0, "EPSILON MILLS", "EM/55", "2025-08-01", 23600)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    assert res["control_totals"]["superseded_rows"] == 1
    assert res["control_totals"]["invoices_in_2b"] == 1
    assert res["control_totals"]["invoice_value_2b"] == 23600.0
    assert res["counts"]["matched"] == 1


def test_credit_note_reduces_credit():
    gst = [
        _g(0, "08FFFFF0000F1Z5", "ZETA GOODS", "Z/1", "2025-09-01", 118000),
        _g(1, "08FFFFF0000F1Z5", "ZETA GOODS", "CN/1", "2025-09-20", -11800,
           taxable=-10000, tax=-1800, section="CDNR", inv_type="Credit Note",
           flags=["credit_note"]),
    ]
    tally = [
        _t(0, "ZETA GOODS", "Z/1", "2025-09-01", 118000),
        _t(1, "ZETA GOODS", "CN/1", "2025-09-20", -11800, vch_type="Credit Note"),
    ]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    assert res["counts"]["matched"] == 2
    assert res["control_totals"]["invoice_value_2b"] == 106200.0
    ins = build_insights(res, gst, tally, today=TODAY)
    assert ins["itc_position"]["credit_notes"] == -1800.0
    assert ins["itc_position"]["gross"] == 16200.0


def test_pos_outside_home_state_blocks_credit():
    gst = [_g(0, "27GGGGG0000G1Z5", "ETA MAHARASHTRA", "EM/7", "2025-10-01", 11800,
              pos="Maharashtra")]
    tally = [_t(0, "ETA MAHARASHTRA", "EM/7", "2025-10-01", 11800)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    rec = _status_of(res, "EM/7")
    assert "pos_mismatch" in rec["flags"]
    ins = build_insights(res, gst, tally, today=TODAY)
    assert ins["itc_position"]["pos_blocked"] == 1800.0
    assert ins["itc_position"]["statutory"] == 0.0


def test_blocked_itc_is_excluded():
    gst = [_g(0, "08HHHHH0000H1Z5", "THETA SUPPLY", "TS/9", "2025-11-01", 11800,
              itc_avail="No", itc_eligible=False, reason="Return filed post annual cut-off",
              flags=["itc_blocked"])]
    tally = []
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    ins = build_insights(res, gst, tally, today=TODAY)
    assert ins["itc_position"]["blocked"] == 1800.0
    assert ins["itc_position"]["statutory"] == 0.0
    rec = _status_of(res, "TS/9")
    assert rec["eligible_itc"] == 0.0
    assert ins["itc_position"]["blocked_reasons"][0]["count"] == 1


def test_partial_itc():
    gst = [_g(0, "08IIIII0000I1Z5", "IOTA CABS", "IC/3", "2025-11-05", 11800,
              pct_applicable=65.0, flags=["partial_itc"])]
    res = reconcile_data(gst, [], tolerance=2.0, entity_info=ENTITY, today=TODAY)
    ins = build_insights(res, gst, [], today=TODAY)
    assert ins["itc_position"]["partial_lost"] == 630.0
    assert ins["itc_position"]["statutory"] == 1170.0


def test_itc_working_takes_each_bill_off_once():
    """A bill under two lines of the credit working comes off once.

    Summed line by line, a reverse-charge bill missing from Tally was taken
    off as unbooked and again as reverse charge, an amended bill counted with
    its original, and "safe to claim" came to 1,170 where it is 5,400. The
    GSTR-3B set-off used the gross credit while saying it used what the law
    allows. Every bill here carries 1,800 of tax, the amendment 3,600.
    """
    from app.backend.insights import gstr3b_working

    gst = [
        _g(0, "08AAAAA0000A1Z5", "ALPHA TRANSPORT", "AT/1", "2025-06-01", 11800,
           rev_charge="Yes", flags=["rcm"]),                    # reverse charge, not booked
        _g(1, "27GGGGG0000G1Z5", "ETA MAHARASHTRA", "EM/7", "2025-06-02", 11800,
           pos="Maharashtra"),                                  # other state, not booked
        _g(2, "08HHHHH0000H1Z5", "THETA SUPPLY", "TS/9", "2025-06-03", 11800,
           itc_avail="No", itc_eligible=False, flags=["itc_blocked", "rcm"]),
        _g(3, "27KKKKK0000K1Z5", "ETA TEXTILES", "ET/8", "2025-06-04", 11800,
           pos="Maharashtra", pct_applicable=65.0, flags=["partial_itc"]),
        _g(4, "08BBBBB0000B1Z5", "BETA LOGISTICS", "BL/2", "2025-06-05", 11800,
           rev_charge="Yes", flags=["rcm"]),                    # reverse charge, booked
        _g(5, "08CCCCC0000C1Z5", "GAMMA MILLS", "GM/3", "2025-06-06", 11800),
        _g(6, "08EEEEE0000E1Z5", "EPSILON MILLS", "EM/55", "2025-06-07", 11800),
        _g(7, "08EEEEE0000E1Z5", "EPSILON MILLS", "EM/55", "2025-06-07", 23600,
           section="B2BA", orig_inv_no="EM/55", flags=["amended"]),
    ]
    tally = [_t(0, "THETA SUPPLY", "TS/9", "2025-06-03", 11800),
             _t(1, "ETA TEXTILES", "ET/8", "2025-06-04", 11800),
             _t(2, "BETA LOGISTICS", "BL/2", "2025-06-05", 11800),
             _t(3, "GAMMA MILLS", "GM/3", "2025-06-06", 11800),
             _t(4, "EPSILON MILLS", "EM/55", "2025-06-07", 23600)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    itc = build_insights(res, gst, tally, today=TODAY)["itc_position"]

    assert itc["gross"] == res["control_totals"]["total_itc_2b"] == 14400.0, \
        "an amended bill counts once, not with its original"
    assert itc["blocked"] == 1800.0, "blocked and reverse charge: off as blocked only"
    assert (itc["partial_lost"], itc["pos_blocked"]) == (630.0, 2970.0), \
        "65% creditable in another state: 630 then the other 1,170, not 1,800 more"
    assert itc["statutory"] == 9000.0
    assert itc["unbooked_eligible"] == 1800.0, "the other-state bill is off already"
    assert itc["rcm"] == 1800.0, "the unbooked reverse-charge bill is off already"
    assert itc["claimable_now"] == 5400.0, itc["claimable_now"]
    assert itc["recoverable_by_booking"] == 0.0, \
        "booking the reverse-charge bill still leaves its tax to be paid"
    assert itc["heads_allowed"] == {"igst": 0.0, "cgst": 4500.0, "sgst": 4500.0,
                                    "cess": 0.0}

    tb = gstr3b_working(res, itc, {"unreported_sales_value": 0.0},
                        [{"igst": 0.0, "cgst": 6000.0, "sgst": 6000.0, "cess": 0.0,
                          "taxable": 66666.67}])
    cgst = tb["set_off"][1]
    assert (cgst["credit_used"], cgst["cash"]) == (4500.0, 1500.0), \
        f"set off against the credit the law allows, not the gross 2B figure: {cgst}"
    assert tb["cash_payable"] == 3000.0 and tb["credit"]["total"] == 9000.0


def test_update_needs_the_publishers_signature():
    """An update is fetched only with the publisher's signature and fingerprint.

    Another key, a file changed after signing, or a version no newer than this
    one is refused, and nothing is left behind to be run. The stand-in for
    GitHub serves a public page, handing each file on to separate storage, and
    no request carries a key; a copy built without a release page or signing
    key never asks at all. Signed with a key made here, never the real one.
    """
    import base64
    import hashlib
    import http.server
    import json
    import shutil
    import tempfile
    import threading
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.backend import updates

    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    installer = b"MZ stand-in installer " * 4000
    files, seen = {}, []                          # what is published; who brought which key

    class GitHub(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization")
            seen.append((self.path, auth))
            if self.path == "/repos/someone/releases/releases/latest":
                if not files:
                    return self.answer(404, b"{}")
                assets = [{"name": n, "url": f"{api}/assets/{n}"} for n in files]
                return self.answer(200, json.dumps({"assets": assets}).encode())
            if self.path.startswith("/assets/"):
                self.send_response(302)
                self.send_header("Location", f"{storage}/{self.path.rsplit('/', 1)[1]}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if auth:                              # the storage: one way of signing in only
                return self.answer(400, b"only one way of signing in is allowed")
            body = files.get(self.path.lstrip("/"))
            self.answer(404 if body is None else 200, body or b"")

        def answer(self, code, body):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    api, storage = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
    folder = Path(tempfile.mkdtemp(prefix="updates-"))

    def publish(version, signer=key, served=installer):
        manifest = json.dumps({"product": "ultimate-accountant", "version": version, "sha256": hashlib.sha256(installer).hexdigest(),
                               "size": len(installer), "notes": "Faster reading."}).encode()
        files.clear()
        files.update({"latest.json": manifest, "Install.exe": served,
                      "latest.json.sig": base64.b64encode(signer.sign(manifest))})

    def check(repo="someone/releases", key=public):
        return updates.check(api=api, public_key=key, folder=folder, current="2.2.0", repo=repo)

    try:
        publish("2.2.1")
        s = check()
        assert (s["status"], s["latest"], s["notes"]) == ("ready", "2.2.1", "Faster reading."), s
        assert (folder / "Install-2.2.1.exe").read_bytes() == installer
        assert seen and not any(auth for _path, auth in seen), "a public release page is read with no key"

        publish("2.2.0")
        assert check()["status"] == "current", "the same version is not an update"
        assert not list(folder.glob("Install-*")), "a superseded download is not kept"

        publish("2.3.0", signer=Ed25519PrivateKey.generate())
        s = check()
        assert s["status"] == "failed" and "signature" in s["message"], s

        publish("2.3.0", served=b"X" + installer[1:])           # changed after it was signed
        s = check()
        assert s["status"] == "failed" and "fingerprint" in s["message"], s
        assert not list(folder.glob("Install-*")), "a download that fails is not kept"

        asked = len(seen)
        for repo, k in (("", public), ("someone/releases", "")):
            s = check(repo, k)
            assert s["status"] == "failed" and "never updates itself" in s["message"], s
        assert len(seen) == asked, "a copy built without a release page or key asked anyway"
        files.clear()
        assert check()["message"] == "No release has been published yet."
    finally:
        server.shutdown()
        shutil.rmtree(folder, ignore_errors=True)
    assert updates.version_tuple("2.10.0") > updates.version_tuple("2.9.9")


def test_update_takes_the_code_alone_on_the_same_base():
    """A copy standing on the base a release was built on fetches Update.exe -
    the app's own code, a few MB; one on another base, or missing a module the
    new code needs, or run from the source, fetches the whole installer.
    Signed with a key made here, never the real one."""
    import base64
    import hashlib
    import http.server
    import json
    import shutil
    import tempfile
    import threading
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.backend import updates

    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    full, small = b"MZ the whole program " * 3000, b"MZ the code " * 200
    sha = lambda b: hashlib.sha256(b).hexdigest()
    files = {}

    class GitHub(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/repos/someone/releases/releases/latest":
                body = json.dumps({"assets": [{"name": n, "url": f"{api}/f/{n}"} for n in files]}).encode()
            else:
                body = files.get(self.path.rsplit("/", 1)[-1], b"")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{server.server_address[1]}"
    folder = Path(tempfile.mkdtemp(prefix="updates-"))
    here = {"core": "c0ffee", "modules": ["fastapi", "json", "sqlite3"]}

    def publish(needs):
        manifest = json.dumps({"product": "ultimate-accountant", "version": "9.1.0", "sha256": sha(full), "size": len(full),
                               "update": {"sha256": sha(small), "size": len(small), "core": "c0ffee",
                                          "modules": needs}}).encode()
        files.clear()
        files.update({"latest.json": manifest, "latest.json.sig": base64.b64encode(key.sign(manifest)),
                      "Install.exe": full, "Update.exe": small})

    def check(base):
        return updates.check(api=api, public_key=public, folder=folder, current="9.0.0", base=base,
                             repo="someone/releases")

    try:
        publish(["fastapi", "json"])
        s = check(here)
        assert (s["status"], s["light"], s["size"]) == ("ready", True, len(small)), s
        assert (folder / "Update-9.1.0.exe").read_bytes() == small
        s = check({**here, "core": "another"})
        assert (s["status"], s["light"]) == ("ready", False) and (folder / "Install-9.1.0.exe").read_bytes() == full
        assert not (folder / "Update-9.1.0.exe").exists(), "the update for another base was kept"
        publish(["fastapi", "json", "tomllib"])                  # the new code needs a module not installed
        assert check(here)["light"] is False
        assert check(None)["light"] is False, "a copy run from the source has no base to update"
    finally:
        server.shutdown()
        shutil.rmtree(folder, ignore_errors=True)


def test_update_gives_each_address_a_few_seconds():
    """Each of GitHub's addresses gets CONNECT_TIMEOUT to pick up, so one that
    never answers costs seconds, not the 20 s a download may wait for data."""
    import socket
    import urllib.request

    from app.backend import updates
    assert any(isinstance(h, updates._Https) for h in updates._opener.handlers)
    assert not any(type(h) is urllib.request.HTTPSHandler for h in updates._opener.handlers)
    listener = socket.create_server(("127.0.0.1", 0))
    asked, real = [], socket.create_connection

    def spy(address, timeout=None, source_address=None, **kw):
        asked.append(timeout)
        return real(address, timeout, source_address)

    socket.create_connection = spy
    try:
        conn = updates._QuickConnect("127.0.0.1", listener.getsockname()[1], timeout=updates.TIMEOUT)
        sock = conn._create_connection(("127.0.0.1", listener.getsockname()[1]), updates.TIMEOUT)
        assert asked == [updates.CONNECT_TIMEOUT] and sock.gettimeout() == updates.TIMEOUT, (asked, sock.gettimeout())
        sock.close()
    finally:
        socket.create_connection = real
        listener.close()


def test_start_asks_only_ports_that_listen():
    """Starting with no copy open must not wait on every closed port (two
    seconds each on Windows); an open copy must still be found - and Assurance
    Console, still installed on its own, is not taken for one."""
    import http.server
    import json
    import socket
    import threading
    import time

    import launch

    free = []
    for _ in range(12):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free.append(s.getsockname()[1])
    started = time.perf_counter()
    assert launch.running_copy(free) is None
    assert time.perf_counter() - started < 1.0, "closed ports were asked"

    said = {"app": "GST Reconcile"}                       # what Assurance Console answers

    class App(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({**said, "db": {"db_path": "reconcile.db"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert launch.running_copy(free[:3] + [port]) is None, "the old app was taken for this one"
        said["app"] = launch.TITLE
        assert launch.running_copy(free[:3] + [port]) == f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_what_the_app_opens_breaks_out_of_its_job():
    """A link or folder the app opens goes to Explorer outside launch.py's job (held to
    the app's share of the CPU, and ended with it); where breakaway is refused, it is
    opened the usual way."""
    import launch

    calls, fell = [], []
    real = launch.subprocess.Popen, launch._startfile
    try:
        launch.subprocess.Popen = lambda args, **kw: calls.append((args, kw))
        launch.open_outside_the_job(r"C:\some folder")
        assert calls == [(["explorer.exe", r"C:\some folder"], {"creationflags": 0x01000000})], calls

        def refused(args, **kw):
            raise PermissionError("no breakaway")
        launch.subprocess.Popen, launch._startfile = refused, fell.append
        launch.open_outside_the_job("https://example.com")
        assert fell == ["https://example.com"], fell
    finally:
        launch.subprocess.Popen, launch._startfile = real


def test_manual_decision_carries_forward():
    gst = [_g(0, "08JJJJJ0000J1Z5", "KAPPA STORES", "KS/12", "2025-04-20", 50000)]
    tally = [_t(0, "Totally Different Ledger", "XYZ-99", "2025-04-22", 50000)]

    plain = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    assert _status_of(plain, "KS/12")["status"] == "In GSTR-2B only"

    decisions = {
        fp_2b("08JJJJJ0000J1Z5", "KS/12", 50000): {
            "action": "match",
            "counterpart": fp_tally("Totally Different Ledger", "XYZ-99", 50000),
            "note": "Ledger renamed mid-year", "updated_at": "2025-12-01T10:00:00",
        }
    }
    with_dec = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY,
                              today=TODAY, decisions=decisions)
    rec = _status_of(with_dec, "KS/12")
    assert rec["status"] == "Matched"
    assert "manual_match" in rec["flags"]
    assert "Ledger renamed mid-year" in rec["note_2b"]
    assert with_dec["control_totals"]["manual_matches"] == 1


def test_ignore_decision_is_written_off():
    gst = [_g(0, "08KKKKK0000K1Z5", "LAMBDA BANK", "BC/1", "2025-04-30", 118)]
    decisions = {fp_2b("08KKKKK0000K1Z5", "BC/1", 118): {
        "action": "ignore", "note": "Bank charge, posted via journal",
        "updated_at": "2025-12-01T10:00:00", "counterpart": None}}
    res = reconcile_data(gst, [], tolerance=2.0, entity_info=ENTITY, today=TODAY,
                         decisions=decisions)
    rec = _status_of(res, "BC/1")
    assert "ignored" in rec["flags"] and "resolved" in rec["flags"]
    assert rec["decision"]["action"] == "ignore"


def test_duplicate_detection():
    gst = [
        _g(0, "08LLLLL0000L1Z5", "MU EXPORTS", "ME/70", "2025-05-05", 23600),
        _g(1, "08LLLLL0000L1Z5", "MU EXPORTS", "ME/70", "2025-05-05", 23600, period="Jun'25"),
    ]
    res = reconcile_data(gst, [], tolerance=2.0, entity_info=ENTITY, today=TODAY)
    ins = build_insights(res, gst, [], today=TODAY)
    dupes = [c for c in ins["quality"]["checks"] if c["key"] == "duplicates"]
    assert dupes and dupes[0]["count"] == 1
    assert dupes[0]["rows"][0]["times"] == 2


def test_sec164_lapse_flagging():
    gst = [_g(0, "08MMMMM0000M1Z5", "NU METALS", "NM/1", "2025-04-01", 11800)]
    # Well past 30-Nov-2026, the cut-off for FY 2025-26.
    late = D("2027-01-15")
    res = reconcile_data(gst, [], tolerance=2.0, entity_info=ENTITY, today=late)
    rec = _status_of(res, "NM/1")
    assert "sec164_lapsed" in rec["flags"]
    ins = build_insights(res, gst, [], today=late)
    assert ins["sec164"]["lapsed"] is True
    assert any(i["priority"] == 0 for i in ins["checklist"])


def test_aging_and_health():
    gst = [_g(i, "08NNNNN0000N1Z5", "XI TRADERS", f"XT/{i}", "2025-04-01", 11800)
           for i in range(10)]
    res = reconcile_data(gst, [], tolerance=2.0, entity_info=ENTITY, today=TODAY)
    ins = build_insights(res, gst, [], today=TODAY)
    buckets = {a["bucket"]: a["count"] for a in ins["aging"]}
    assert buckets["181-365 days"] == 10
    assert res["health"]["score"] == 0.0            # nothing matched at all
    assert res["health"]["tone"] == "risk"
    assert res["rates"]["match_rate"] == 0.0


def test_no_double_use_of_one_voucher():
    gst = [
        _g(0, "08OOOOO0000O1Z5", "OMICRON CO", "OC/1", "2025-04-01", 5000),
        _g(1, "08OOOOO0000O1Z5", "OMICRON CO", "OC/2", "2025-04-01", 5000),
    ]
    tally = [_t(0, "OMICRON CO", "OC/1", "2025-04-01", 5000)]
    res = reconcile_data(gst, tally, tolerance=2.0, entity_info=ENTITY, today=TODAY)
    used = [r for r in res["all_records"] if r.get("amt_tally")]
    assert len(used) == 1, "a Tally voucher was consumed twice"
    assert res["counts"]["gst_only"] == 1


def test_checklist_is_ordered_by_recovery():
    gst = [_g(i, "08PPPPP0000P1Z5", "PI GOODS", f"PG/{i}", "2025-04-01", 11800)
           for i in range(5)]
    res = reconcile_data(gst, [_t(0, "PI GOODS", "PG/0", "2025-04-01", 11800)],
                         tolerance=2.0, entity_info=ENTITY, today=TODAY)
    ins = build_insights(res, gst, [], today=TODAY)
    prios = [i["priority"] for i in ins["checklist"]]
    assert prios == sorted(prios)
    assert ins["checklist"][0]["view"] == "purchase"


def test_every_open_item_is_explained():
    # Each unresolved entry carries its own reason - even one whose only
    # description is "." - and the nearest thing on the other side is named.
    from app.backend.bank import reconcile_bank
    from app.backend.common import serialize
    from app.backend.explain import explain_run
    from app.backend.selftest_recon import _bb, _bt

    def open_why(payload):
        explain_run(payload)
        rows = [r for r in payload["all_records"]
                if r["status"] not in ("Matched", "Agreed", "Batched", "Superseded")]
        assert rows and all(r["why"]["title"] and r["why"]["what"] and r["why"]["todo"]
                            for r in rows), [r["status"] for r in rows if not r.get("why")]
        return rows

    k = "08AAAAA0000A1Z5"
    gst = [_g(0, k, "KESARI TEXTILE MILLS", "KT/101", "2025-06-01", 11800),
           _g(1, k, "KESARI TEXTILE MILLS", "KT/150", "2025-06-20", 11000)]
    books = [_t(0, "KESARI TEXTILE MILLS", "KT/101", "2025-06-01", 11800),
             _t(1, "SIGMA DYEING", "999", "2025-06-05", 4000, narration=".")]
    rows = open_why(serialize(reconcile_data(gst, books, tolerance=2.0,
                                             entity_info=ENTITY, today=TODAY)))
    only_2b = next(r for r in rows if r["status"] == "In GSTR-2B only")
    assert "KT/101" in only_2b["why"]["nearest"] and "already matched" in only_2b["why"]["nearest"]
    only_books = next(r for r in rows if r["status"] == "In Tally only")
    assert 'only "."' in only_books["why"]["what"], only_books["why"]["what"]
    assert "SIGMA DYEING's bills" in only_books["why"]["title"]

    bank = [_bt(0, "2026-01-10", "NEFT CR-KESARI TEXTILE MILLS", cr=50000),
            _bt(1, "2026-01-12", "SMS CHARGES", dr=59),
            _bt(2, "2026-01-25", "NEFT DR-NAVRANG HANDLOOMS", dr=7500)]
    book = [_bb(0, "2026-01-10", "KESARI TEXTILE MILLS", dr=50000, vch="BR/1"),
            _bb(1, "2026-01-15", ".", cr=12000, vch="BP/2"),
            _bb(2, "2026-01-25", "NAVRANG HANDLOOMS", dr=7500, vch="BR/3")]
    rows = open_why(serialize(reconcile_bank(bank, book, bank_meta={"last_date": "2026-01-31"})))
    by_amt = {r["bank_amount"] or r["book_amount"]: r["why"] for r in rows
              if not (r["bank_amount"] and r["book_amount"])}
    assert 'only "."' in by_amt[12000.0]["what"]
    assert by_amt[12000.0]["checks"][0].startswith("Description:")   # nothing to match on
    assert by_amt[59.0]["checks"][0].startswith("Kind:")           # the bank made it
    assert any(c.startswith("Wrong side?") for c in by_amt[7500.0]["checks"])


def test_money_formatting():
    assert inr(71962994.5) == "₹7,19,62,994.50"
    assert inr(-546323) == "−₹5,46,323.00"
    assert inr(None) == "—"
    assert inr(0) == "₹0.00"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:                      # noqa: BLE001 - report and continue
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
