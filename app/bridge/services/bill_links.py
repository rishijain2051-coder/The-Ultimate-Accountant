"""Each bill one keypress away from its voucher in Tally.

TallyPrime cannot hold a file in a voucher: its "Docs by Ira" needs a TSS
subscription and hides the file once the voucher is in the books. So the
voucher carries a link instead, in a field of its own (a UDF). The add-on
below shows it under the narration, and Ctrl+Alt+O opens the bill.

The files are served from here, read-only, to the network Tally is on,
because Tally usually runs on another PC. Only bill files, and only by their
SHA-256: a link cannot be guessed from another, and nothing can be listed,
changed or uploaded.
"""

from __future__ import annotations

import mimetypes
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Document
from ..routers.documents import BILLS
from .voucher_xml import BILL_LINK_UDF

_server: Optional[ThreadingHTTPServer] = None
_lock = threading.Lock()


def address() -> str:
    """This PC as the Tally PC reaches it: the local address of the route to
    Tally, or 127.0.0.1 when Tally runs here."""
    host = (settings.tally_host or "").strip()
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, settings.tally_port))  # picks the route; sends nothing
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


def url_for(doc: Optional[Document]) -> Optional[str]:
    if not settings.bill_links or doc is None:
        return None
    return f"http://{address()}:{settings.bill_link_port}/bill/{doc.sha256}{Path(doc.stored_path).suffix.lower()}"


class _Bills(BaseHTTPRequestHandler):
    timeout = 30  # a stalled connection lets go

    def do_GET(self):  # noqa: N802 - http.server naming
        m = re.fullmatch(r"/bill/([0-9a-f]{64})(?:\.[a-z0-9]{2,5})?", self.path)
        doc = None
        if m:
            with SessionLocal() as db:
                doc = db.scalar(select(Document).where(Document.sha256 == m.group(1), BILLS))
        path = Path(doc.stored_path) if doc else None
        if path is None or not path.is_file():
            self.send_error(404, "No such bill")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(doc.filename)}")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):  # quiet
        pass


def start() -> Optional[str]:
    """Serve the bills, if links are on. None when serving; else why not."""
    global _server
    if not settings.bill_links:
        return "bill links are off"
    with _lock:
        if _server is None:
            try:
                _server = ThreadingHTTPServer(("0.0.0.0", settings.bill_link_port), _Bills)
            except OSError as exc:
                return f"port {settings.bill_link_port} is taken: {exc}"
            threading.Thread(target=_server.serve_forever, daemon=True, name="bill-links").start()
    return None


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None


NAME, INDEX = BILL_LINK_UDF
# Loaded once in TallyPrime on the PC where you work in Tally. Written from
# Tally's TDL reference; see README for how it was checked.
ADDON = f"""; Invoice Bridge: open the bill a voucher was posted from.
;
; Load it in TallyPrime, on the PC where you work in Tally:
;   F1 (Help) > TDL & Add-On > F4 Manage Local TDLs,
;   "Load TDL files on startup": Yes, and add the full path of this file.
; A voucher the app posted then shows "Bill:" and its link under the
; narration, and Ctrl+Alt+O opens the bill.

[System : UDF]
    {NAME} : String : {INDEX}

[#Part : VCH Narration]
    Add : Line : After : VCH NarrPrompt : TIB Bill Line

[Line : TIB Bill Line]
    Fields    : Short Prompt, TIB Bill Field
    Local     : Field : Short Prompt : Info : "Bill:"
    Invisible : $$IsEmpty:${NAME}

[Field : TIB Bill Field]
    Use     : Name Field
    Storage : {NAME}
    Width   : 70
    Skip    : Yes

[#Form : Voucher]
    Add : Button : TIB Open Bill

[Button : TIB Open Bill]
    Key      : Ctrl + Alt + O
    Title    : "Open Bill"
    Action   : Browse URL : ${NAME}
    Inactive : $$IsEmpty:${NAME}
"""
