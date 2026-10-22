"""The Keeper's line to Tally. It can only read.

Every request goes through send(), and send() refuses anything that is not a
plain read: the header must ask Tally to Export a Collection or an Object, and
any TDL in it may only describe collections and formulae - no TDL function, no
ACTION, no data to import. The payload checked is the very string sent.

Tally's XML gateway (port 9000) has no read-only mode of its own, so this
check is the guarantee. It sits on the request, not on the callers, so a
mistake anywhere above it still cannot write to anyone's books.

Requests take app.tally_line, the turn shared with the Invoice Bridge, so the
two never arrive at Tally at the same moment.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app import tally_line


class TallyError(RuntimeError):
    pass


class TallyUnavailable(TallyError):
    """Tally is not running, or its HTTP gateway is switched off."""


class ReadOnlyViolation(RuntimeError):
    """A request that is not a plain read. It is never sent."""


READ_TYPES = {"Collection", "Object"}
HEADER_TAGS = {"VERSION", "TALLYREQUEST", "TYPE", "SUBTYPE", "ID"}
# What a TDL description in a read may hold. Everything else, FUNCTION first, is refused.
TDL_TAGS = {"COLLECTION", "SYSTEM"}
COLLECTION_TAGS = {"TYPE", "FETCH", "FILTER", "FILTERS", "COMPUTE", "CHILDOF", "BELONGSTO", "NATIVEMETHOD"}


def check(payload: str) -> None:
    """Raise ReadOnlyViolation unless payload is a read Tally cannot act on."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ReadOnlyViolation(f"Not well-formed XML, so it cannot be checked: {exc}") from exc
    if root.tag != "ENVELOPE":
        raise ReadOnlyViolation(f"The request is <{root.tag}>, not <ENVELOPE>.")
    if [c.tag for c in root] != ["HEADER", "BODY"]:
        raise ReadOnlyViolation("The envelope must hold exactly a HEADER and then a BODY.")
    header, body = root

    for el in header:
        if el.tag not in HEADER_TAGS or len(el):
            raise ReadOnlyViolation(f"<{el.tag}> does not belong in a read's header.")
    requests = root.findall(".//TALLYREQUEST")
    if len(requests) != 1 or header.find("TALLYREQUEST") is None or (requests[0].text or "").strip() != "Export":
        raise ReadOnlyViolation("Only Export requests are sent: this one is not.")
    kind = (header.findtext("TYPE") or "").strip()
    if kind not in READ_TYPES:
        raise ReadOnlyViolation(f"TYPE {kind or '(none)'} is not one of {sorted(READ_TYPES)}.")

    if [c.tag for c in body] != ["DESC"]:
        raise ReadOnlyViolation("A read's BODY holds only a DESC - no DATA to import.")
    for el in body[0]:
        if el.tag == "STATICVARIABLES":
            if any(len(v) for v in el):
                raise ReadOnlyViolation("A static variable holds elements, not a value.")
        elif el.tag == "TDL":
            for msg in el:
                if msg.tag != "TDLMESSAGE":
                    raise ReadOnlyViolation(f"<{msg.tag}> in TDL: only TDLMESSAGE is allowed.")
                for d in msg:
                    if d.tag not in TDL_TAGS:
                        raise ReadOnlyViolation(f"TDL <{d.tag}> is refused: a read describes only "
                                                f"collections and formulae.")
                    if d.tag == "SYSTEM" and len(d):
                        raise ReadOnlyViolation("A TDL formula holds elements, not an expression.")
                    if d.tag == "COLLECTION":
                        for c in d:
                            if c.tag not in COLLECTION_TAGS or len(c):
                                raise ReadOnlyViolation(f"<{c.tag}> does not belong in a collection read.")
        else:
            raise ReadOnlyViolation(f"<{el.tag}> does not belong in a read's DESC.")

    for el in root.iter():
        if any(a.upper() == "ACTION" for a in el.attrib):
            raise ReadOnlyViolation(f"<{el.tag}> carries an ACTION.")


def esc(s: Any) -> str:
    """Escape a value for a Tally XML request."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# Tally's replies are not always XML: '&' in names goes unescaped, and it
# writes control characters, raw or as references ("&#4; Primary").
_BAD_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CHAR_REF = re.compile(r"&#(x[0-9A-Fa-f]+|\d+);")
_LONE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)")


def _xml_char(m: re.Match) -> str:
    n = m.group(1)
    code = int(n[1:], 16) if n[0] == "x" else int(n)
    ok = code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF
    return m.group(0) if ok else ""


# Add-ons put their fields in as <UDF:NAME ...> without declaring UDF, so the prefix is
# unbound: inside tags only, "UDF:NAME" becomes "UDF_NAME" (the text in between is left be).
_TAG = re.compile(r"<(/?)([A-Za-z_][\w.:-]*)([^<>]*)>")
_PREFIXED_ATTR = re.compile(r"(\s[A-Za-z_][\w.-]*):([\w.-]+\s*=)")


def _unprefix(m: re.Match) -> str:
    attrs = _PREFIXED_ATTR.sub(r"\1_\2", m.group(3))
    return f"<{m.group(1)}{m.group(2).replace(':', '_')}{attrs}>"


def clean(xml_text: str) -> str:
    return _TAG.sub(_unprefix, _LONE_AMP.sub("&amp;", _CHAR_REF.sub(_xml_char, _BAD_CTRL.sub("", xml_text))))


def send(payload: str, host: str = "localhost", port: int = 9000,
         read: float = 120.0, connect: float = 2.0) -> ET.Element:
    """Check payload, then send it to Tally on its turn, and parse the reply.

    A dropped line is tried once more: a read is safe to repeat."""
    check(payload)
    # 'localhost' makes Windows try IPv6 first, which Tally does not listen on.
    url = f"http://{'127.0.0.1' if host.strip().lower() == 'localhost' else host.strip()}:{port}"
    data = payload.encode("utf-8")
    for attempt in (1, 2):
        if not tally_line.LINE.acquire(timeout=read + 60):
            raise TallyUnavailable("Tally is still answering another request from this computer.")
        try:
            with httpx.Client(timeout=httpx.Timeout(read, connect=connect)) as client:
                resp = client.post(url, content=data, headers={"Content-Type": "text/xml;charset=utf-8"})
            break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise TallyUnavailable(f"Tally is not answering at {url}: it is closed, or its server is off.") from exc
        except httpx.TimeoutException as exc:
            raise TallyUnavailable(f"Tally at {url} did not answer within {read:.0f}s. A dialog open "
                                   f"inside Tally blocks every request until it is closed.") from exc
        except httpx.TransportError as exc:
            if attempt == 2:
                raise TallyUnavailable(f"Tally at {url} dropped the connection twice.") from exc
        finally:
            tally_line.LINE.release()
        time.sleep(1.0)

    if resp.status_code != 200:
        raise TallyError(f"Tally returned HTTP {resp.status_code}: {resp.text[:400]}")
    text = resp.content.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(clean(text))
    except ET.ParseError as exc:
        raise TallyError(f"Could not read Tally's reply ({exc}). It began: {text[:400]}") from exc
    # Tally says no in a reply of its own, not an HTTP error: a LINEERROR (a company that
    # isn't open, a formula it won't take), or STATUS 0 in the header. Taken as "nothing
    # there", that would replace the books' last good copy with an empty one.
    said = root.findtext(".//LINEERROR")
    if said is not None or (root.findtext("HEADER/STATUS") or "").strip() == "0":
        raise TallyError(f"Tally answered with an error: {(said or text[:300]).strip()}")
    return root


def collection(name: str, type_: str, fetch: list[str], company: str | None = None, extra_vars: str = "",
               filters: str = "", formulae: str = "") -> str:
    """A read of one TDL collection, for the current company or the one named. filters names
    the formulae (SYSTEM TYPE="Formulae", given in formulae) an object must pass."""
    sv = f"<SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY>" if company else ""
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>'
            f'<TYPE>Collection</TYPE><ID>{esc(name)}</ID></HEADER>'
            f'<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>{sv}{extra_vars}'
            f'</STATICVARIABLES><TDL><TDLMESSAGE>'
            f'<COLLECTION NAME="{esc(name)}" ISMODIFY="No" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">'
            f'<TYPE>{esc(type_)}</TYPE><FETCH>{esc(",".join(fetch))}</FETCH>'
            + (f'<FILTER>{esc(filters)}</FILTER>' if filters else "")
            + f'</COLLECTION>{formulae}</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')


def companies(host: str = "localhost", port: int = 9000) -> list[str]:
    """The companies Tally has loaded, by name."""
    root = send(collection("CCList", "Company", ["NAME"]), host, port, read=30.0)
    return [n for n in ((c.get("NAME") or c.findtext("NAME") or "").strip() for c in root.iter("COMPANY")) if n]
