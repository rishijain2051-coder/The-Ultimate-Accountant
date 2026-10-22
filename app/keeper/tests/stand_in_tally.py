"""A stand-in Tally for the Keeper's tests: two companies, answering reads the way the real
one does (TIB's lessons): debit as a negative AMOUNT, invoice-mode vouchers with the sales
ledger inside the item lines, bill allocations, an unescaped '&' and '&#4;' before built-in
names, cancelled and optional vouchers. It refuses anything that is not an Export.
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUESTS: list[str] = []

COMPANIES = {
    "Saffron Impex": "20240401",
    "Rao & Sons": "20250401",            # '&' goes out unescaped, as Tally sends it
}

GROUPS = """
<GROUP NAME="Sundry Debtors"><PARENT>&#4; Primary</PARENT><ISREVENUE>No</ISREVENUE><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE></GROUP>
<GROUP NAME="Sundry Creditors"><PARENT>&#4; Primary</PARENT><ISREVENUE>No</ISREVENUE><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE></GROUP>
<GROUP NAME="Sales Accounts"><PARENT>&#4; Primary</PARENT><ISREVENUE>Yes</ISREVENUE><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE></GROUP>
<GROUP NAME="Indirect Expenses"><PARENT>&#4; Primary</PARENT><ISREVENUE>Yes</ISREVENUE><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE></GROUP>
<GROUP NAME="Bank Accounts"><PARENT>Current Assets</PARENT><ISREVENUE>No</ISREVENUE><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE></GROUP>
"""

LEDGERS = {
    "Saffron Impex": """
<LEDGER NAME="Kiran Traders"><PARENT>Sundry Debtors</PARENT><OPENINGBALANCE>-5000.00</OPENINGBALANCE><CLOSINGBALANCE>-11800.00</CLOSINGBALANCE>
 <BILLALLOCATIONS.LIST><NAME>OB/1</NAME><BILLDATE>20250310</BILLDATE><BILLCREDITPERIOD>30 Days</BILLCREDITPERIOD><OPENINGBALANCE>-5000.00</OPENINGBALANCE></BILLALLOCATIONS.LIST>
</LEDGER>
<LEDGER NAME="Sales"><PARENT>Sales Accounts</PARENT><OPENINGBALANCE></OPENINGBALANCE><CLOSINGBALANCE>10000.00</CLOSINGBALANCE></LEDGER>
<LEDGER NAME="Output GST"><PARENT>Duties &amp; Taxes</PARENT><OPENINGBALANCE></OPENINGBALANCE><CLOSINGBALANCE>1800.00</CLOSINGBALANCE></LEDGER>
<LEDGER NAME="HDFC Bank"><PARENT>Bank Accounts</PARENT><OPENINGBALANCE>-20000.00</OPENINGBALANCE><CLOSINGBALANCE>-25000.00</CLOSINGBALANCE></LEDGER>
""",
    "Rao & Sons": """
<LEDGER NAME="Rent"><PARENT>Indirect Expenses</PARENT><OPENINGBALANCE></OPENINGBALANCE><CLOSINGBALANCE>-3000.00</CLOSINGBALANCE></LEDGER>
<LEDGER NAME="Cash"><PARENT>&#4; Cash-in-Hand</PARENT><OPENINGBALANCE>-9000.00</OPENINGBALANCE><CLOSINGBALANCE>-6000.00</CLOSINGBALANCE></LEDGER>
""",
}

STOCK = {
    "Saffron Impex": '<STOCKITEM NAME="Steel Rod"><PARENT>&#4; Primary</PARENT><BASEUNITS>Nos</BASEUNITS>'
                      '<CLOSINGBALANCE> 38 Nos</CLOSINGBALANCE><CLOSINGVALUE>-38000.00</CLOSINGVALUE></STOCKITEM>',
    "Rao & Sons": "",
}

# (company, YYYYMM) -> vouchers that month
VOUCHERS = {
    ("Saffron Impex", "202509"): """
<VOUCHER VCHTYPE="Sales"><DATE>20250905</DATE><GUID>g-s1</GUID><ALTERID>1</ALTERID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>S-1</VOUCHERNUMBER>
 <PARTYLEDGERNAME>Kiran Traders</PARTYLEDGERNAME><NARRATION>12 rods</NARRATION><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>
 <LEDGERENTRIES.LIST><LEDGERNAME>Kiran Traders</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-11800.00</AMOUNT>
  <BILLALLOCATIONS.LIST><NAME>S-1</NAME><BILLTYPE>New Ref</BILLTYPE><BILLCREDITPERIOD>45 Days</BILLCREDITPERIOD><AMOUNT>-11800.00</AMOUNT></BILLALLOCATIONS.LIST>
 </LEDGERENTRIES.LIST>
 <LEDGERENTRIES.LIST><LEDGERNAME>Output GST</LEDGERNAME><AMOUNT>1800.00</AMOUNT></LEDGERENTRIES.LIST>
 <ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Steel Rod</STOCKITEMNAME><RATE>833.33/Nos</RATE><ACTUALQTY> 12 Nos</ACTUALQTY><BILLEDQTY> 12 Nos</BILLEDQTY><AMOUNT>10000.00</AMOUNT>
  <ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>10000.00</AMOUNT></ACCOUNTINGALLOCATIONS.LIST>
 </ALLINVENTORYENTRIES.LIST>
</VOUCHER>
<VOUCHER><DATE>20250906</DATE><GUID>g-x</GUID><ALTERID>2</ALTERID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>S-2</VOUCHERNUMBER><ISCANCELLED>Yes</ISCANCELLED></VOUCHER>
""",
    ("Saffron Impex", "202510"): """
<VOUCHER><DATE>20251002</DATE><GUID>g-r1</GUID><ALTERID>3</ALTERID><UDF:VCHNOTE.LIST DESC="`VchNote`" UDF:ISLIST="Yes"><UDF:VCHNOTE>a:b</UDF:VCHNOTE></UDF:VCHNOTE.LIST><VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME><VOUCHERNUMBER>R-1</VOUCHERNUMBER><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Kiran Traders</LEDGERNAME><AMOUNT>5000.00</AMOUNT>
  <BILLALLOCATIONS.LIST><NAME>OB/1</NAME><BILLTYPE>Agst Ref</BILLTYPE><AMOUNT>5000.00</AMOUNT></BILLALLOCATIONS.LIST>
 </ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>HDFC Bank</LEDGERNAME><AMOUNT>-5000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER>
""",
    ("Rao & Sons", "202510"): """
<VOUCHER><DATE>20251001</DATE><GUID>g-p1</GUID><ALTERID>1</ALTERID><VOUCHERTYPENAME>Payment</VOUCHERTYPENAME><VOUCHERNUMBER>1</VOUCHERNUMBER><NARRATION>Rent & lights</NARRATION><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>Yes</ISOPTIONAL>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Rent</LEDGERNAME><AMOUNT>-3000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><AMOUNT>3000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER>
""",
}


ALTER = {"Saffron Impex": 3, "Rao & Sons": 1}
FILTER = "ok"           # "refuse": an error reply, as a Tally that won't take the formula; "ignore": all of the period
LEDGERS_FAIL = False    # the ledgers read answered with a LINEERROR, as for a company closed mid-sync     # the companies' AltVchID: their highest voucher AlterID


def _var(body: str, tag: str) -> str:
    m = re.search(rf"<{tag}(?: [^>]*)?>(.*?)</{tag}>", body, re.S)
    return (m.group(1) if m else "").replace("&amp;", "&")


def answer(body: str) -> str:
    if "<TALLYREQUEST>Export</TALLYREQUEST>" not in body:
        return "<RESPONSE><LINEERROR>Only reads here</LINEERROR></RESPONSE>"
    rid, company = _var(body, "ID"), _var(body, "SVCURRENTCOMPANY")
    if rid == "KeeperCompanies":
        rows = "".join(f'<COMPANY NAME="{n}"><BOOKSFROM>{d}</BOOKSFROM><ALTVCHID>{ALTER[n]}</ALTVCHID>'
                       f'<ALTMSTID>{ALTER[n]}</ALTMSTID></COMPANY>' for n, d in COMPANIES.items())
    elif company not in COMPANIES:
        return "<RESPONSE><LINEERROR>Company not loaded</LINEERROR></RESPONSE>"
    elif rid == "KeeperGroups":
        rows = GROUPS
    elif rid == "KeeperLedgers":
        if LEDGERS_FAIL:
            return "<RESPONSE><LINEERROR>Company not open</LINEERROR></RESPONSE>"
        rows = LEDGERS[company]
    elif rid == "KeeperStock":
        rows = STOCK[company]
    elif rid == "KeeperVouchers":
        # Without TYPE="Date" the period isn't taken as one: every voucher, as is feared of the real one
        altered = re.search(r'NAME="KeeperAltered">\$AlterID &gt; (\d+)<', body)
        in_period = "".join(v for (co, m), v in VOUCHERS.items() if co == company
                            and _var(body, "SVFROMDATE")[:6] <= m <= _var(body, "SVTODATE")[:6])
        if altered and FILTER == "refuse":
            return "<RESPONSE><LINEERROR>Could not find Formula 'KeeperAltered'</LINEERROR></RESPONSE>"
        if altered and FILTER == "ignore":
            rows = in_period
        elif altered:                     # only vouchers altered since, in the period asked
            rows = "".join(m for m in re.findall(r"<VOUCHER\b.*?</VOUCHER>", in_period, re.S)
                           if int(re.search(r"<ALTERID>(\d+)", m).group(1)) > int(altered.group(1)))
        elif '<SVFROMDATE TYPE="Date">' not in body:
            rows = "".join(v for (co, _), v in VOUCHERS.items() if co == company)
        else:
            rows = VOUCHERS.get((company, _var(body, "SVFROMDATE")[:6]), "")
    else:
        rows = ""
    return f"<ENVELOPE><BODY><DATA><COLLECTION>{rows}</COLLECTION></DATA></BODY></ENVELOPE>"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        REQUESTS.append(body)
        out = answer(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def start() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
