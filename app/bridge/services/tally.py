"""Tally Prime / ERP 9 gateway client.

Tally exposes an XML-over-HTTP endpoint on port 9000 when
Gateway of Tally > F1 > Advanced Config > Enable ODBC/HTTP is on. There is no
SDK; this is the interface.

Three things make it awkward in practice and are handled here:
  * Tally emits invalid XML. Unescaped '&' in ledger names ("A & B Traders") is
    routine, and so are raw control characters.
  * Every request is implicitly scoped to whichever company is "current" unless
    SVCURRENTCOMPANY says otherwise -- forget it and you silently read, or
    write, the wrong company's books.
  * Tally answers one request at a time. A second connection arriving while it
    works is dropped (WinError 10054), so this app never sends two at once.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from app import tally_line

from ..config import settings


class TallyError(RuntimeError):
    pass


class TallyUnavailable(TallyError):
    """Tally is not running, or the HTTP gateway is switched off."""


class TallyBusy(TallyError):
    """This app is in the middle of another request to Tally."""


# One request to Tally at a time, from anywhere in the app - and from the
# Keeper, which reads Tally from its own process too (app/tally_line.py).
_line = tally_line.LINE


@dataclass
class PostResult:
    created: int = 0
    altered: int = 0
    ignored: int = 0
    errors: int = 0
    # TallyPrime 3 and later import a voucher with a problem as an
    # "exception": inside Tally, but kept out of the books until fixed there.
    exceptions: int = 0
    last_vch_id: Optional[str] = None
    master_id: Optional[str] = None
    messages: list[str] = field(default_factory=list)
    raw: str = ""  # the start of Tally's reply, for the audit log

    @property
    def ok(self) -> bool:
        return self.errors == 0 and self.exceptions == 0 and (self.created > 0 or self.altered > 0)


def _host(h: str) -> str:
    # 'localhost' makes Windows try IPv6 ::1 first; against a closed port that
    # alone costs ~2s. Tally only listens on IPv4.
    h = (h or "").strip()
    return "127.0.0.1" if h.lower() == "localhost" else h


def _url() -> str:
    return f"http://{_host(settings.tally_host)}:{settings.tally_port}"


def esc(s: Any) -> str:
    """Escape a value for inclusion in a Tally XML request."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_BAD_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Tally writes control characters as references too: "&#4; Primary" is how it
# marks its own built-in names. XML allows neither form.
_CHAR_REF = re.compile(r"&#(x[0-9A-Fa-f]+|\d+);")
# '&' that is not already the start of a valid entity.
_LONE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)")


def _xml_char(m: re.Match) -> str:
    n = m.group(1)
    code = int(n[1:], 16) if n[0] == "x" else int(n)
    ok = code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF
    return m.group(0) if ok else ""


def _clean(xml_text: str) -> str:
    return _LONE_AMP.sub("&amp;", _CHAR_REF.sub(_xml_char, _BAD_CTRL.sub("", xml_text)))


def _request(payload: str, timeout: float | None = None, connect: float = 2.0,
             wait: bool = True) -> ET.Element:
    """A listening local server accepts a connection instantly, so a short
    connect cap costs nothing when Tally is up and saves seconds when it is not.
    The read timeout stays long: a big ledger export genuinely takes a while.

    wait=False: if another request to Tally is under way, raise TallyBusy
    at once instead of queueing behind it."""
    read = timeout or settings.tally_timeout
    if not _line.acquire(timeout=read + 60 if wait else 0):
        raise TallyBusy("Tally is still answering another request from this app.")
    try:
        return _send(payload, read, connect)
    finally:
        _line.release()


def _send(payload: str, read: float, connect: float) -> ET.Element:
    # A dropped line on a read is tried once more. On an import it never is:
    # Tally may have taken the voucher before the line went, and sending it
    # again would post it twice.
    importing = "<TALLYREQUEST>Import</TALLYREQUEST>" in payload
    attempts = 1 if importing else 2
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=httpx.Timeout(read, connect=connect)) as client:
                resp = client.post(
                    _url(),
                    content=payload.encode("utf-8"),
                    headers={"Content-Type": "text/xml;charset=utf-8"},
                )
            break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Nothing listening: Tally is closed, or its server mode is off.
            remote = _host(settings.tally_host) not in ("127.0.0.1", "::1")
            raise TallyUnavailable(
                f"Could not reach Tally at {_url()}. Open TallyPrime with your company loaded, then "
                f"F1 (Help) > Settings > Connectivity > Client/Server configuration: set "
                f"'TallyPrime acts as' to Both and the port to {settings.tally_port}. "
                f"(Tally.ERP 9: F12 > Advanced Configuration.)"
                + (f" Tally is on another computer: Windows Firewall there must let port {settings.tally_port} "
                   f"in, and both computers must be on the same network." if remote else "")
            ) from exc
        except httpx.TimeoutException as exc:
            # Connected, but no answer: Tally is up and stuck.
            raise TallyUnavailable(
                f"Tally at {_url()} accepted the connection but did not answer within {read:.0f}s. "
                f"A dialog or an unfinished screen open inside Tally blocks every request until it is closed."
            ) from exc
        except httpx.TransportError as exc:
            # Connected, then dropped: Tally was busy with another program's request.
            if attempt + 1 < attempts:
                time.sleep(1.0)
                continue
            raise TallyUnavailable(
                f"Tally at {_url()} dropped the connection before answering. It answers one request at a "
                f"time: if another program is talking to it, try again in a moment."
                + (" Check Tally's Day Book before posting this bill again: it may have taken the voucher."
                   if importing else "")
            ) from exc

    if resp.status_code != 200:
        raise TallyError(f"Tally returned HTTP {resp.status_code}: {resp.text[:400]}")

    text = resp.content.decode("utf-8", errors="replace")
    try:
        return ET.fromstring(_clean(text))
    except ET.ParseError as exc:
        raise TallyError(f"Could not parse Tally's reply ({exc}). First 400 chars: {text[:400]}") from exc


def _txt(node: Optional[ET.Element], tag: str) -> Optional[str]:
    if node is None:
        return None
    el = node.find(tag)
    if el is None or el.text is None:
        return None
    v = el.text.strip()
    return v or None


def _collection(name: str, type_: str, fetch: list[str], company: str | None = None,
                extra_vars: str = "", filters: str = "", system: str = "",
                compute: list[str] | None = None) -> str:
    sv = f"<SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY>" if company else ""
    # COMPUTE: a value Tally works out per object, e.g. "EDU:$$LicenseInfo:IsEducationalMode".
    filters += "".join(f"<COMPUTE>{c}</COMPUTE>" for c in compute or [])
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>{name}</ID></HEADER>
 <BODY><DESC>
  <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>{sv}{extra_vars}</STATICVARIABLES>
  <TDL><TDLMESSAGE>
   <COLLECTION NAME="{name}" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">
    <TYPE>{type_}</TYPE>
    <FETCH>{",".join(fetch)}</FETCH>
    {filters}
   </COLLECTION>
   {system}
  </TDLMESSAGE></TDL>
 </DESC></BODY>
</ENVELOPE>"""


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

_last_ping: dict[str, Any] = {}
_educational: dict[str, Optional[bool]] = {}  # address -> Educational Mode?

# What Educational Mode means for posting, in the words the reviewer needs.
EDUCATIONAL_DAYS = (1, 2, 31)
EDUCATIONAL_NOTE = ("This Tally is in Educational Mode (not licensed): it takes vouchers dated only the "
                    "1st, 2nd or 31st of a month, and refuses any other date as \"Voucher date is missing\". "
                    "Post to a licensed Tally, or try this one with such a date.")


def educational(wait: bool = True) -> Optional[bool]:
    """Whether the Tally at the configured address is unlicensed, in
    Educational Mode. None: not known (no company open, or an older Tally
    without the function). Asked once per address."""
    url = _url()
    if url not in _educational:
        try:
            root = _request(_collection("LicCheck", "Company", ["NAME"],
                                        compute=["EDU:$$LicenseInfo:IsEducationalMode"]), wait=wait)
        except TallyBusy:
            return None
        except TallyError:
            _educational[url] = None
            return None
        v = next((_txt(n, "EDU") for n in root.iter("COMPANY") if _txt(n, "EDU")), None)
        if v is None:
            return None  # no company open to ask: ask again next time
        _educational[url] = v.lower() == "yes"
    return _educational[url]


def ping() -> dict[str, Any]:
    """Never raises, and never waits: while the app is in the middle of
    another request to Tally, the last answer stands. The whole app works
    offline; this just reports status."""
    global _last_ping
    try:
        comps = companies(connect=1.0, wait=False)
        _last_ping = {"connected": True, "url": _url(), "companies": len(comps),
                      "company_names": [c["name"] for c in comps],
                      "educational": educational(wait=False)}
    except TallyBusy:
        if _last_ping.get("url") == _url():
            return {**_last_ping, "busy": True}
        return {"connected": False, "url": _url(), "busy": True,
                "error": "Tally is busy with another request from this app; checking again shortly."}
    except TallyError as exc:
        _last_ping = {"connected": False, "url": _url(), "error": str(exc)}
    return _last_ping


def companies(connect: float = 2.0, wait: bool = True) -> list[dict[str, Any]]:
    root = _request(
        _collection("CCList", "Company",
                    ["NAME", "STARTINGFROM", "ENDINGAT", "GSTREGNUMBER", "CMPGSTIN", "STATENAME"]),
        connect=connect, wait=wait,
    )
    out: list[dict[str, Any]] = []
    for node in root.iter("COMPANY"):
        name = _txt(node, "NAME") or (node.get("NAME") or "").strip()
        if not name:
            continue
        gstin = _txt(node, "CMPGSTIN") or _txt(node, "GSTREGNUMBER")
        out.append({
            "name": name,
            "gstin": (gstin or "").upper() or None,
            "state": _txt(node, "STATENAME"),
            "books_from": _tally_date(_txt(node, "STARTINGFROM")),
        })
    # Tally repeats company nodes across the reply; keep the first of each.
    seen, uniq = set(), []
    for c in out:
        if c["name"] not in seen:
            seen.add(c["name"])
            uniq.append(c)
    return uniq


def registration(company: str) -> tuple[Optional[str], Optional[str]]:
    """(GSTIN, name) of the company's GST registration. TallyPrime 3 and
    later keep the GSTIN on a "tax unit" ("Rajasthan Registration"), not on
    the company itself, and every voucher keyed there names it.
    ponytail: the first valid one; a company registered in several states is
    matched by that GSTIN only."""
    from .gst import gstin_checksum_ok

    root = _request(_collection("TaxUnits", "TaxUnit", ["NAME", "GSTREGNUMBER", "STATENAME"], company=company))
    for node in root.iter("TAXUNIT"):
        g = (_txt(node, "GSTREGNUMBER") or "").upper()
        if gstin_checksum_ok(g):
            return g, (node.get("NAME") or _txt(node, "NAME") or "").strip() or None
    return None, None


def godowns(company: str) -> list[str]:
    """Where stock is kept. Tally's own is "Main Location"."""
    root = _request(_collection("GodownList", "Godown", ["NAME"], company=company))
    names = ((node.get("NAME") or _txt(node, "NAME") or "").strip() for node in root.iter("GODOWN"))
    return [n for n in names if n]


def balance(company: str, ledger: str, as_of: date) -> Optional[Decimal]:
    """A ledger's balance on a date, as Tally keeps it (negative is Dr): the
    "Current balance" of Tally's voucher screen. None: no such ledger.
    The one ledger is asked for by name, as an object, so no name needs
    escaping into a formula. Never waits behind a sync."""
    start = date(as_of.year - (as_of.month < 4), 4, 1)  # the financial year's first day
    root = _request(f"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Object</TYPE><SUBTYPE>Ledger</SUBTYPE><ID TYPE="Name">{esc(ledger)}</ID></HEADER>
 <BODY><DESC>
  <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT><SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY><SVFROMDATE TYPE="Date">{start:%Y%m%d}</SVFROMDATE><SVTODATE TYPE="Date">{as_of:%Y%m%d}</SVTODATE></STATICVARIABLES>
  <FETCHLIST><FETCH>ClosingBalance</FETCH></FETCHLIST>
 </DESC></BODY>
</ENVELOPE>""", timeout=8.0, wait=False)
    # Under DATA: the reply's header counts masters in a LEDGER tag of its own.
    data = root.find(".//DATA")
    node = data.find(".//LEDGER") if data is not None else None
    return None if node is None else (_decimal(_txt(node, "CLOSINGBALANCE")) or Decimal(0))


def voucher_types(company: str) -> list[dict[str, Any]]:
    """Every voucher type in the company, with the built-in type it derives
    from. The parent is what decides the debit/credit shape."""
    root = _request(
        _collection("VchTypeList", "VoucherType",
                    ["NAME", "PARENT", "ISACTIVE", "NUMBERINGMETHOD", "ISOPTIONAL"], company=company)
    )
    out = []
    for node in root.iter("VOUCHERTYPE"):
        name = (node.get("NAME") or _txt(node, "NAME") or "").strip()
        if not name:
            continue
        active = (_txt(node, "ISACTIVE") or "Yes").lower() != "no"
        out.append({
            "name": name,
            "parent": _txt(node, "PARENT") or name,
            "active": active,
            "numbering": _txt(node, "NUMBERINGMETHOD"),
        })
    return [v for v in out if v["active"]]


def ledgers(company: str) -> list[dict[str, Any]]:
    root = _request(
        _collection("LedList", "Ledger",
                    ["NAME", "PARENT", "PARTYGSTIN", "GSTIN", "LEDSTATENAME", "COUNTRYNAME",
                     "OPENINGBALANCE", "ISBILLWISEON"], company=company),
        timeout=max(settings.tally_timeout, 90.0),
    )
    out = []
    for node in root.iter("LEDGER"):
        name = (node.get("NAME") or _txt(node, "NAME") or "").strip()
        if not name:
            continue
        gstin = (_txt(node, "PARTYGSTIN") or _txt(node, "GSTIN") or "").upper() or None
        out.append({
            "name": name,
            "parent": _txt(node, "PARENT"),
            "gstin": gstin,
            "state": _txt(node, "LEDSTATENAME"),
            "country": _txt(node, "COUNTRYNAME"),
            "opening_balance": _amount(_txt(node, "OPENINGBALANCE")),
            "bill_wise": (_txt(node, "ISBILLWISEON") or "").lower() == "yes",
        })
    return out


def stock_items(company: str) -> list[dict[str, Any]]:
    root = _request(
        _collection("StkList", "StockItem",
                    ["NAME", "PARENT", "BASEUNITS", "GSTAPPLICABLE", "HSNCODE", "GSTDETAILS.LIST"],
                    company=company),
        timeout=max(settings.tally_timeout, 90.0),
    )
    out = []
    for node in root.iter("STOCKITEM"):
        name = (node.get("NAME") or _txt(node, "NAME") or "").strip()
        if not name:
            continue
        hsn = _txt(node, "HSNCODE")
        rate = None
        for det in node.iter("STATEWISEDETAILS.LIST"):
            for r in det.iter("RATEDETAILS.LIST"):
                head = (_txt(r, "GSTRATEDUTYHEAD") or "").lower()
                v = _txt(r, "GSTRATE")
                if v and head == "igst":
                    rate = float(v)
                    break
                if v and head in ("central tax", "cgst") and rate is None:
                    rate = float(v) * 2
        if hsn is None:
            hsn = next((_txt(h, "HSNCODE") for h in node.iter("HSNDETAILS.LIST") if _txt(h, "HSNCODE")), None)
        out.append({
            "name": name, "parent": _txt(node, "PARENT"), "uom": _txt(node, "BASEUNITS"),
            "hsn": hsn, "gst_rate": rate,
        })
    return out


def vouchers_in_period(company: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Every voucher Tally holds in a date window, with what a duplicate check
    needs: the supplier's reference, the party, and the party-side amount.

    Deliberately not filtered by voucher type. The same bill keyed in by hand
    last month as a Journal is still the same bill.
    """
    extra = (f'<SVFROMDATE TYPE="Date">{from_date:%Y%m%d}</SVFROMDATE>'
             f'<SVTODATE TYPE="Date">{to_date:%Y%m%d}</SVTODATE>')
    root = _request(
        _collection(
            "VchDupList", "Voucher",
            ["DATE", "VOUCHERNUMBER", "VOUCHERTYPENAME", "PARTYLEDGERNAME", "PARTYGSTIN",
             "REFERENCE", "REFERENCEDATE", "NARRATION", "MASTERID", "ISOPTIONAL", "ISCANCELLED",
             "ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"],
            company=company, extra_vars=extra),
        timeout=max(settings.tally_timeout, 120.0),
    )

    out = []
    for node in root.iter("VOUCHER"):
        if (_txt(node, "ISCANCELLED") or "No").lower() == "yes":
            continue
        party = _txt(node, "PARTYLEDGERNAME")
        amount: Optional[Decimal] = None
        bills: list[str] = []
        for tag in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"):
            for le in node.iter(tag):
                led = _txt(le, "LEDGERNAME")
                amt = _decimal(_txt(le, "AMOUNT"))
                if party and led == party and amt is not None:
                    amount = abs(amt)
                for ba in le.iter("BILLALLOCATIONS.LIST"):
                    nm = _txt(ba, "NAME")
                    if nm:
                        bills.append(nm)
        if amount is None:
            # No party line (e.g. a Journal): fall back to the largest line.
            amts = [abs(a) for a in (_decimal(_txt(le, "AMOUNT")) for le in node.iter("ALLLEDGERENTRIES.LIST"))
                    if a is not None]
            amount = max(amts) if amts else None
        out.append({
            "date": _tally_date(_txt(node, "DATE")),
            "voucher_number": _txt(node, "VOUCHERNUMBER"),
            "voucher_type": _txt(node, "VOUCHERTYPENAME"),
            "party": party,
            "party_gstin": (_txt(node, "PARTYGSTIN") or "").upper() or None,
            "reference": _txt(node, "REFERENCE"),
            "bill_refs": bills,
            "amount": amount,
            "narration": _txt(node, "NARRATION"),
            "master_id": _txt(node, "MASTERID") or node.get("REMOTEID"),
            "is_optional": (_txt(node, "ISOPTIONAL") or "No").lower() == "yes",
        })
    return out


def purchases(company: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Every item line on a voucher in the window: who it was bought from,
    the item, the ledger it went to. What the matcher learns your habits from."""
    extra = (f'<SVFROMDATE TYPE="Date">{from_date:%Y%m%d}</SVFROMDATE>'
             f'<SVTODATE TYPE="Date">{to_date:%Y%m%d}</SVTODATE>')
    root = _request(_collection("BoughtList", "Voucher", ["DATE", "PARTYLEDGERNAME", "ISCANCELLED",
                                                          "ALLINVENTORYENTRIES.LIST"], company=company, extra_vars=extra),
                    timeout=max(settings.tally_timeout, 180.0))
    out = []
    for v in root.iter("VOUCHER"):
        party = _txt(v, "PARTYLEDGERNAME")
        if not party or (_txt(v, "ISCANCELLED") or "No").lower() == "yes":
            continue
        for ie in v.iter("ALLINVENTORYENTRIES.LIST"):
            item = _txt(ie, "STOCKITEMNAME")
            if item:
                out.append({"party": party, "item": item, "date": _tally_date(_txt(v, "DATE")),
                            "ledger": next((_txt(a, "LEDGERNAME") for a in ie.iter("ACCOUNTINGALLOCATIONS.LIST")
                                            if _txt(a, "LEDGERNAME")), None)})
    return out


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

def import_xml(company: str, tally_messages: str, request_id: str = "Vouchers") -> PostResult:
    payload = f"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE><ID>{request_id}</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
   <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
   <SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY>
  </STATICVARIABLES></DESC>
  <DATA>{tally_messages}</DATA>
 </BODY>
</ENVELOPE>"""
    root = _request(payload, timeout=max(settings.tally_timeout, 120.0))

    def num(tag: str) -> int:
        v = next((e.text for e in root.iter(tag) if e.text), "0")
        try:
            return int(str(v).strip() or 0)
        except ValueError:
            return 0

    msgs = [e.text.strip() for e in root.iter("LINEERROR") if e.text and e.text.strip()]
    res = PostResult(
        created=num("CREATED"), altered=num("ALTERED"), ignored=num("IGNORED"), errors=num("ERRORS"),
        exceptions=num("EXCEPTIONS"),
        last_vch_id=next((e.text for e in root.iter("LASTVCHID") if e.text), None),
        master_id=next((e.text for e in root.iter("LASTMID") if e.text), None),
        messages=msgs, raw=re.sub(r"\s+", " ", ET.tostring(root, encoding="unicode"))[:800],
    )
    if res.errors and not res.messages:
        res.messages = ["Tally rejected the voucher but gave no reason. "
                        "The usual cause is a ledger or stock item name that does not exist."]
    return res


def create_ledger(company: str, name: str, parent: str, gstin: str | None = None,
                  state: str | None = None, address: list[str] | None = None,
                  registration_type: str = "Regular", duty_head: str | None = None) -> PostResult:
    """A ledger under any group, with the fields that group needs: a party
    (bill-wise, GSTIN, state), a GST duty ledger (TAXTYPE GST + its duty head,
    under Duties & Taxes), a purchase/sales ledger (GST on, affects stock), or
    a plain one (an expense, a bank...) which needs nothing beyond its parent.
    Field shapes mirror what TallyPrime exports."""
    extra = ""
    if parent in ("Sundry Debtors", "Sundry Creditors"):
        addr = "".join(f"<ADDRESS>{esc(a)}</ADDRESS>" for a in (address or [])[:4])
        gst_block = ""
        if gstin:
            gst_block = (
                f"<PARTYGSTIN>{esc(gstin)}</PARTYGSTIN>"
                f"<GSTREGISTRATIONTYPE>{esc(registration_type)}</GSTREGISTRATIONTYPE>"
                f"<ISGSTAPPLICABLE>Yes</ISGSTAPPLICABLE>"
            )
        state_block = f"<LEDSTATENAME>{esc(state)}</LEDSTATENAME><COUNTRYNAME>India</COUNTRYNAME>" if state else ""
        addr_block = f"<ADDRESS.LIST TYPE='String'>{addr}</ADDRESS.LIST>" if addr else ""
        extra = f"<ISBILLWISEON>Yes</ISBILLWISEON>{state_block}{gst_block}{addr_block}"
    elif parent == "Duties & Taxes":
        extra = f"<TAXTYPE>GST</TAXTYPE><GSTDUTYHEAD>{esc(duty_head or 'CGST')}</GSTDUTYHEAD>"
    elif parent in ("Purchase Accounts", "Sales Accounts"):
        extra = ("<TAXTYPE>Others</TAXTYPE><GSTAPPLICABLE>&#4; Applicable</GSTAPPLICABLE>"
                 "<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY><AFFECTSSTOCK>Yes</AFFECTSSTOCK>")
    elif "expense" in parent.lower() or "income" in parent.lower():
        # As the books keep Bank Charges and Wages Expenses: no GST on the ledger.
        extra = "<TAXTYPE>Others</TAXTYPE><GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>"
    msg = f"""<TALLYMESSAGE xmlns:UDF="TallyUDF">
 <LEDGER NAME="{esc(name)}" RESERVEDNAME="" ACTION="Create">
  <NAME.LIST><NAME>{esc(name)}</NAME></NAME.LIST>
  <PARENT>{esc(parent)}</PARENT>
  {extra}
 </LEDGER>
</TALLYMESSAGE>"""
    return import_xml(company, msg, request_id="All Masters")


def create_stock_item(company: str, name: str, parent: str = "Primary",
                      uom: str = "Nos", hsn: str | None = None,
                      gst_rate: float | None = None) -> PostResult:
    gst_block = ""
    if gst_rate is not None:
        half = gst_rate / 2
        gst_block = f"""<GSTDETAILS.LIST>
   <APPLICABLEFROM>20170701</APPLICABLEFROM><HSNCODE>{esc(hsn or "")}</HSNCODE>
   <TAXABILITY>Taxable</TAXABILITY>
   <STATEWISEDETAILS.LIST><STATENAME>&#4; Any</STATENAME>
    <RATEDETAILS.LIST><GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD><GSTRATE>{half}</GSTRATE></RATEDETAILS.LIST>
    <RATEDETAILS.LIST><GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD><GSTRATE>{half}</GSTRATE></RATEDETAILS.LIST>
    <RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD><GSTRATE>{gst_rate}</GSTRATE></RATEDETAILS.LIST>
   </STATEWISEDETAILS.LIST>
  </GSTDETAILS.LIST>"""
    msg = f"""<TALLYMESSAGE xmlns:UDF="TallyUDF">
 <STOCKITEM NAME="{esc(name)}" ACTION="Create">
  <NAME.LIST><NAME>{esc(name)}</NAME></NAME.LIST>
  <PARENT>{esc(parent)}</PARENT>
  <BASEUNITS>{esc(uom)}</BASEUNITS>
  <GSTAPPLICABLE>&#4; Applicable</GSTAPPLICABLE>
  {f"<HSNCODE>{esc(hsn)}</HSNCODE>" if hsn else ""}
  {gst_block}
 </STOCKITEM>
</TALLYMESSAGE>"""
    return import_xml(company, msg, request_id="All Masters")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _tally_date(v: str | None) -> Optional[date]:
    if not v:
        return None
    v = v.strip()
    for fmt in ("%Y%m%d", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _decimal(v: str | None) -> Optional[Decimal]:
    if not v:
        return None
    try:
        return Decimal(re.sub(r"[^\d.\-]", "", v) or "0")
    except InvalidOperation:
        return None


def _amount(v: str | None) -> Optional[float]:
    d = _decimal(v)
    return float(d) if d is not None else None
