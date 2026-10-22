"""A stand-in for TallyPrime's XML gateway, for testing and demos without Tally.

    python tests/fake_tally.py            # listens on 127.0.0.1:9000

It answers the same requests the app makes, in the shape Tally uses, including
Tally's bad habits: an unescaped '&' in a ledger name, "&#4; Primary" (a
control character XML does not allow, the way Tally marks its built-in names),
and a LINEERROR when a voucher names a ledger that does not exist. Vouchers you post are remembered,
so uploading the same bill again exercises the duplicate check against Tally.

Two companies are open, each with books of its own: every request is answered
from the company its SVCURRENTCOMPANY names, as Tally does. A ledger of one
company does not exist in the other.

This models Tally's replies; it is not Tally. The first run against a real
TallyPrime is still the test that counts.
"""

from __future__ import annotations

import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMPANIES = [
    ("Saffron Impex", "08ZZZPZ1111Z1Z4", "Rajasthan"),
    # No GSTIN on the company: TallyPrime 3 and later keep it on the
    # company's GST registration, a tax unit (TAX_UNITS below).
    ("Saffron Exports", "", "Rajasthan"),
]

IMPEX, EXPORTS = COMPANIES[0][0], COMPANIES[1][0]
_BUILT_IN = [
    ("Purchase", "Purchase"), ("Sales", "Sales"), ("Payment", "Payment"), ("Receipt", "Receipt"),
    ("Contra", "Contra"), ("Journal", "Journal"), ("Debit Note", "Debit Note"),
    ("Credit Note", "Credit Note"), ("Stock Journal", "Stock Journal"),
]

# (name, parent, gstin). No GSTIN on Kestrel Audit on purpose: the invoice
# says "KESTREL AUDIT (INDIA) PRIVATE LIMITED", the ledger says "(FSC)".
LEDGERS: dict[str, list[tuple[str, str, str]]] = {
    IMPEX: [
        ("Kestrel Audit (FSC)", "Sundry Creditors", ""),
        ("Kestrel Audit Certification", "Sundry Creditors", ""),
        ("A & B Traders", "Sundry Creditors", "27WWWFW4444W1ZS"),
        ("Jaipur Timber Mart", "Sundry Creditors", ""),
        ("Comet Enterprises", "Sundry Debtors", ""),
        ("Input IGST", "Duties & Taxes", ""),
        ("Input CGST", "Duties & Taxes", ""),
        ("Input SGST", "Duties & Taxes", ""),
        ("Output IGST", "Duties & Taxes", ""),
        ("Certification & Audit Fees", "Indirect Expenses", ""),
        ("Purchase - Services", "Purchase Accounts", ""),
        ("Sales - Domestic", "Sales Accounts", ""),
        ("HDFC Bank", "Bank Accounts", ""),
        ("Cash", "Cash-in-Hand", ""),
        ("Round Off", "Indirect Expenses", ""),
    ],
    # Other books: some of the same suppliers, under other ledger names.
    EXPORTS: [
        ("Kestrel Audit India Pvt Ltd", "Sundry Creditors", ""),
        ("Jaipur Timber Mart", "Sundry Creditors", ""),
        ("Shree Krishna Enterprises", "Sundry Creditors", ""),
        ("Input IGST", "Duties & Taxes", ""),
        ("Input CGST", "Duties & Taxes", ""),
        ("Input SGST", "Duties & Taxes", ""),
        ("Output IGST", "Duties & Taxes", ""),
        ("Testing Charges", "Indirect Expenses", ""),
        ("Purchase - Goods", "Purchase Accounts", ""),
        ("Export Sales", "Sales Accounts", ""),
        ("SBI Current Account", "Bank Accounts", ""),
        ("Cash", "Cash-in-Hand", ""),
        ("Round Off", "Indirect Expenses", ""),
    ],
}

# "&#4; Primary": how real Tally writes a built-in name.
STOCK = {IMPEX: [("Teak Wood Log", "&#4; Primary", "Cft", "4403", 18.0)], EXPORTS: []}

TAX_UNITS = {IMPEX: [("Default Tax Unit", "")],
             EXPORTS: [("Default Tax Unit", ""), ("Rajasthan Registration", "08ZZZPZ1111Z2Z3")]}

# A custom type only Saffron Impex has.
VOUCHER_TYPES = {IMPEX: _BUILT_IN + [("GST Purchase - Services", "Purchase")], EXPORTS: _BUILT_IN}

# Already in Saffron Impex's books: a different Kestrel Audit bill for the
# same amount, so a fresh upload shows a "similar bill" warning, not a duplicate.
# And timber keyed by hand as items: what the app learns your habits from.
POSTED: dict[str, list[dict]] = {IMPEX: [{
    "date": "20260812", "number": "P-0107", "type": "Purchase", "party": "Kestrel Audit (FSC)",
    "reference": "5500100-26003990", "amount": "169228.52", "optional": "No",
}, {
    "date": "20260705", "number": "412", "type": "Purchase", "party": "Jaipur Timber Mart",
    "reference": "", "amount": "12980.00", "optional": "No",
    "items": [("Teak Wood Log", "Purchase - Services")] * 3,
}], EXPORTS: []}
_lock = threading.Lock()
# Every request body this fake was sent, in order: the Keeper's tests check
# that none of its requests was anything but a read.
REQUESTS: list[str] = []
_next_id = [500]


def _company(xml: str) -> str | None:
    """The company a request is for. None: one that is not open in Tally."""
    m = re.search(r"<SVCURRENTCOMPANY>(.*?)</SVCURRENTCOMPANY>", xml)
    name = m.group(1).replace("&amp;", "&") if m else IMPEX  # none named: Tally's current company
    return name if name in LEDGERS else None


def _company_block() -> str:
    # Tally emits the name both as an attribute and a child; keep both.
    return "".join(
        f'<COMPANY NAME="{n}"><NAME>{n}</NAME><CMPGSTIN>{g}</CMPGSTIN>'
        f"<STATENAME>{s}</STATENAME><STARTINGFROM>20260401</STARTINGFROM></COMPANY>"
        for n, g, s in COMPANIES)


def _ledger_block(company: str) -> str:
    # Deliberately NOT escaped: this is what real Tally output looks like.
    return "".join(
        f'<LEDGER NAME="{n}"><PARENT>{p}</PARENT>'
        + (f"<PARTYGSTIN>{g}</PARTYGSTIN>" if g else "")
        + "<OPENINGBALANCE>0.00</OPENINGBALANCE></LEDGER>"
        for n, p, g in LEDGERS[company])


def _stock_block(company: str) -> str:
    return "".join(
        f'<STOCKITEM NAME="{n}"><PARENT>{p}</PARENT><BASEUNITS>{u}</BASEUNITS><HSNCODE>{h}</HSNCODE>'
        f"<GSTDETAILS.LIST><STATEWISEDETAILS.LIST><RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>"
        f"<GSTRATE>{r}</GSTRATE></RATEDETAILS.LIST></STATEWISEDETAILS.LIST></GSTDETAILS.LIST></STOCKITEM>"
        for n, p, u, h, r in STOCK[company])


def _tax_unit_block(company: str) -> str:
    return "".join(f'<TAXUNIT NAME="{n}"><GSTREGNUMBER>{g}</GSTREGNUMBER><STATENAME>Rajasthan</STATENAME></TAXUNIT>'
                   for n, g in TAX_UNITS[company])


def _vtype_block(company: str) -> str:
    return "".join(f'<VOUCHERTYPE NAME="{n}"><PARENT>{p}</PARENT><ISACTIVE>Yes</ISACTIVE></VOUCHERTYPE>'
                   for n, p in VOUCHER_TYPES[company])


def _voucher_block(company: str, frm: str, to: str, full: bool = False) -> str:
    """full: every row as posted (the bank's vouchers), not the party's alone."""
    out = []
    with _lock:
        for v in POSTED[company]:
            if frm <= v["date"] <= to:
                out.append(
                    f'<VOUCHER REMOTEID="{v["number"]}" VCHTYPE="{v["type"]}">'
                    f'<DATE>{v["date"]}</DATE><VOUCHERNUMBER>{v["number"]}</VOUCHERNUMBER>'
                    f'<VOUCHERTYPENAME>{v["type"]}</VOUCHERTYPENAME><PARTYLEDGERNAME>{v["party"]}</PARTYLEDGERNAME>'
                    f'<REFERENCE>{v["reference"]}</REFERENCE><ISOPTIONAL>{v["optional"]}</ISOPTIONAL>'
                    + ("".join(f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{n}</LEDGERNAME><AMOUNT>{a}</AMOUNT>"
                               f"</ALLLEDGERENTRIES.LIST>" for n, a in v["rows"]) if full and v.get("rows") else
                       f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{v["party"]}</LEDGERNAME>'
                       f'<AMOUNT>{v["amount"]}</AMOUNT></ALLLEDGERENTRIES.LIST>')
                    + "".join(f"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{i}</STOCKITEMNAME><ACCOUNTINGALLOCATIONS.LIST>"
                              f"<LEDGERNAME>{led}</LEDGERNAME></ACCOUNTINGALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST>"
                              for i, led in v.get("items", []))
                    + "</VOUCHER>")
    return "".join(out)


def _envelope(body: str) -> str:
    return f"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION>{body}</COLLECTION></DATA></BODY></ENVELOPE>"


def _error(text: str) -> str:
    return f"<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><ERRORS>1</ERRORS><LINEERROR>{text}</LINEERROR></RESPONSE>"


def _import(xml: str) -> str:
    company = _company(xml)
    if company is None:
        named = re.search(r"<SVCURRENTCOMPANY>(.*?)</SVCURRENTCOMPANY>", xml).group(1)
        return _error(f"Could not set 'SVCurrentCompany' to '{named}'")
    known = {n for n, _p, _g in LEDGERS[company]}
    names = [re.sub(r"&amp;", "&", n) for n in re.findall(r"<LEDGERNAME>(.*?)</LEDGERNAME>", xml)]
    missing = [n for n in names if n not in known]
    if missing:
        return _error(f"Ledger '{missing[0]}' does not exist!")

    if "<LEDGER " in xml and "ACTION=\"Create\"" in xml:  # master creation, any group
        name = re.search(r'<LEDGER NAME="(.*?)"', xml).group(1).replace("&amp;", "&")
        parent = re.search(r"<PARENT>(.*?)</PARENT>", xml).group(1)
        LEDGERS[company].append((name, parent, ""))
        return "<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>"

    if "<STOCKITEM " in xml and "ACTION=\"Create\"" in xml:  # a stock item, from the item picker
        name = re.search(r'<STOCKITEM NAME="(.*?)"', xml).group(1).replace("&amp;", "&")
        parent = (re.search(r"<PARENT>(.*?)</PARENT>", xml) or [None, "Primary"])[1]
        uom = (re.search(r"<BASEUNITS>(.*?)</BASEUNITS>", xml) or [None, "Nos"])[1]
        hsn = (re.search(r"<HSNCODE>(.*?)</HSNCODE>", xml) or [None, ""])[1]
        STOCK[company].append((name, parent, uom, hsn, 0.0))
        return "<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>"

    # Real Tally, bill 6748 (2026-09-28): an item invoice keeps its supplier,
    # tax and round-off rows in LEDGERENTRIES.LIST. Sent in ALLLEDGERENTRIES
    # they are dropped, and what is left is taken as an import exception.
    if 'OBJVIEW="Invoice Voucher View"' in xml and "<ALLINVENTORYENTRIES.LIST>" in xml \
            and "<ALLLEDGERENTRIES.LIST>" in xml:
        return ("<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS>"
                "<EXCEPTIONS>1</EXCEPTIONS></RESPONSE>")

    with _lock:
        _next_id[0] += 1
        vid = str(_next_id[0])
        party = (re.search(r"<PARTYLEDGERNAME>(.*?)</PARTYLEDGERNAME>", xml) or [None, ""])[1].replace("&amp;", "&")
        rows = re.findall(r"<((?:ALL)?LEDGERENTRIES)\.LIST>(.*?)</\1\.LIST>", xml, re.S)
        amount = next((re.search(r"<AMOUNT>(-?[\d.]+)</AMOUNT>", r).group(1).lstrip("-") for _t, r in rows
                       if (re.search(r"<LEDGERNAME>(.*?)</LEDGERNAME>", r) or [None, ""])[1]
                       .replace("&amp;", "&") == party), "0")
        POSTED[company].append({
            "date": re.search(r"<DATE>(\d{8})</DATE>", xml).group(1),
            "number": f"P-{vid}", "type": re.search(r'VCHTYPE="(.*?)"', xml).group(1),
            "party": party, "reference": (re.search(r"<REFERENCE>(.*?)</REFERENCE>", xml) or [None, ""])[1],
            "amount": amount, "optional": "Yes" if "<ISOPTIONAL>Yes" in xml else "No",
            "rows": [((re.search(r"<LEDGERNAME>(.*?)</LEDGERNAME>", r) or [None, ""])[1].replace("&amp;", "&"),
                      re.search(r"<AMOUNT>(-?[\d.]+)</AMOUNT>", r).group(1)) for _t, r in rows],
        })
    return (f"<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS>"
            f"<LASTVCHID>{vid}</LASTVCHID><LASTMID>{vid}</LASTMID></RESPONSE>")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server naming
        xml = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        REQUESTS.append(xml)
        rid =(re.search(r"<ID>(.*?)</ID>", xml) or [None, ""])[1]
        company = _company(xml)
        if "<TALLYREQUEST>Import</TALLYREQUEST>" in xml:
            body = _import(xml)
        elif "<SUBTYPE>Ledger</SUBTYPE>" in xml:
            # One ledger by name, as Tally answers it: owing 1,234.50 (Cr), with
            # the header's master counts, which are no ledger, before it.
            name = (re.search(r'<ID TYPE="Name">(.*?)</ID>', xml) or [None, ""])[1].replace("&amp;", "&")
            known = company and any(n == name for n, _p, _g in LEDGERS.get(company, []))
            body = ("<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DESC><CMPINFO><LEDGER>0</LEDGER></CMPINFO></DESC>"
                    "<DATA>" + ("<TALLYMESSAGE><LEDGER><CLOSINGBALANCE>1234.50</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>"
                                if known else "") + "</DATA></BODY></ENVELOPE>")
        elif rid == "CCList":
            body = _envelope(_company_block())
        elif company is None:  # a company that is not open: nothing to read
            body = _envelope("")
        elif rid == "LedList":
            body = _envelope(_ledger_block(company))
        elif rid == "StkList":
            body = _envelope(_stock_block(company))
        elif rid == "VchTypeList":
            body = _envelope(_vtype_block(company))
        elif rid == "TaxUnits":
            body = _envelope(_tax_unit_block(company))
        elif rid == "GodownList":  # Tally's own godown, with the mark it puts on built-in names
            body = _envelope('<GODOWN NAME="&#4; Main Location"><NAME>&#4; Main Location</NAME></GODOWN>')
        elif rid in ("VchDupList", "BoughtList", "BankVchList"):
            frm = (re.search(r"<SVFROMDATE[^>]*>(\d{8})</SVFROMDATE>", xml) or [None, "00000000"])[1]
            to = (re.search(r"<SVTODATE[^>]*>(\d{8})</SVTODATE>", xml) or [None, "99999999"])[1]
            body = _envelope(_voucher_block(company, frm, to, full=rid == "BankVchList"))
        else:
            body = "<RESPONSE>Unknown Request</RESPONSE>"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # keep the console quiet
        if "-v" in sys.argv:
            super().log_message(fmt, *args)


def serve(port: int = 9000) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), 9000))
    print(f"Fake TallyPrime gateway on http://127.0.0.1:{port} - Ctrl+C to stop")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
