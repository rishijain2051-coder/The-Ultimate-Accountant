"""Bills photographed on a phone, straight into the app.

The upload screen shows a QR code. The phone's camera opens it: a page,
served from here to the office network, where each bill is photographed,
said what it is and sent, and then the next. It is read here exactly as an
upload from this computer is (routers/documents.accept).

Only that page, its fonts and its sending are served on this port, and only
to the current code: each code carries a fresh random key, and a new code,
Stop, a restart or twelve hours ends the one before.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import secrets
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from ..config import UI_DIR, settings
from ..db import SessionLocal
from ..models import Document, Entry

HOURS = 12
MAX_PAGES = 10
MAX_BODY = 60 * 1024 * 1024  # ten phone photos, as base64
PAGE = Path(__file__).resolve().parent.parent / "phone.html"
FONTS = UI_DIR / "fonts"
ENDED = "This code has ended. Show a new one on the computer and scan it again."
GONE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Code ended</title><body style='font:16px/1.5 system-ui,sans-serif;margin:48px 24px;"
        "color:#2f3437;background:#f7f6f3'><h1 style='font:500 26px/1.2 Georgia,serif'>This code has ended.</h1>"
        "<p>On the computer, open Upload, show a new code for your phone and scan it again.</p>")

_lock = threading.Lock()
_decoding = threading.Lock()  # one upload decodes at a time: photos are big in memory
MAX_PIXELS = 40_000_000
_server: Optional[ThreadingHTTPServer] = None
_session: Optional[dict[str, Any]] = None  # key, until, url, qr, sent: document ids
_problem: Optional[str] = None


def address() -> str:
    """This PC as a phone on the same Wi-Fi reaches it: where the route out
    of it leaves from (a UDP connect sends nothing), else a private address."""
    # ponytail: one address; a PC whose route out is a VPN would show the VPN's.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        ips = []
    private = [ip for ip in ips if re.match(r"(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.", ip)]
    return (private or ips or ["127.0.0.1"])[0]


def qr(text: str) -> dict[str, Any]:
    """The code as one SVG path over a square of modules. cv2 is here for
    the OCR already, so nothing new is installed for it."""
    import cv2

    m = cv2.QRCodeEncoder.create().encode(text)  # a module a pixel, 0 dark
    d = []
    for y, row in enumerate(m):
        x = 0
        while x < len(row):
            if row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and not row[x]:
                x += 1
            d.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
    return {"size": int(m.shape[1]), "path": "".join(d)}


def _live() -> Optional[dict[str, Any]]:
    s = _session
    return s if s and s["until"] > time.time() else None


def _valid(key: str) -> bool:
    s = _live()
    return bool(s) and secrets.compare_digest(key.encode(), s["key"].encode())


def start() -> dict[str, Any]:
    """A new code, with a fresh key: the one before ends."""
    global _server, _session, _problem
    with _lock:
        _session, _problem = None, None
        if _server is None:
            try:
                _server = ThreadingHTTPServer(("0.0.0.0", settings.phone_port), _Phone)
            except OSError as exc:
                _problem = f"Port {settings.phone_port} is in use on this computer, so the phone cannot reach it ({exc})."
            else:
                threading.Thread(target=_server.serve_forever, daemon=True, name="phone").start()
        if _server is not None:
            key = secrets.token_urlsafe(18)
            url = f"http://{address()}:{_server.server_address[1]}/p/{key}"
            _session = {"key": key, "until": time.time() + HOURS * 3600, "url": url, "qr": qr(url), "sent": []}
    return status()


def stop() -> dict[str, Any]:
    global _server, _session
    with _lock:
        _session = None
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None
    return status()


def status() -> dict[str, Any]:
    s = _live()
    if _session and not s:  # twelve hours on: it has ended
        return stop()
    return {"on": bool(s), "url": s["url"] if s else None,
            "until": datetime.fromtimestamp(s["until"]).isoformat(timespec="minutes") if s else None,
            "qr": s["qr"] if s else None, "problem": _problem, "sent": sent(s["sent"] if s else [])}


def sent(ids: list[int]) -> list[dict[str, Any]]:
    """What came from the phone with this code, newest first, and how far each has got."""
    if not ids:
        return []
    out = []
    with SessionLocal() as db:
        docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(ids)))}
        entries = {e.document_id: e.id for e in db.scalars(select(Entry).where(Entry.document_id.in_(ids)))}
        for i in reversed(ids):
            d = docs.get(i)
            if d is None:
                continue
            state = ("failed" if d.status == "failed" else "set aside" if d.status == "dismissed"
                     else "ready" if i in entries else "reading")
            at = re.search(r"(\d\d)\.(\d\d)\.\d\d\.\w+$", d.filename or "")
            out.append({"id": i, "name": d.filename, "type": d.requested_voucher_type, "state": state,
                        "at": f"{at[1]}:{at[2]}" if at else None, "entry_id": entries.get(i),
                        "error": d.error if state == "failed" else None})
    return out


def _types() -> list[dict[str, Any]]:
    from ..routers.tally_api import voucher_types

    with SessionLocal() as db:
        return voucher_types(None, db)["types"]


def receive(body: Any) -> dict[str, Any]:
    with _decoding:
        return _receive(body)


def _receive(body: Any) -> dict[str, Any]:
    """One bill from the phone: its pages, JPEG in base64, and what it is.
    One page is kept as the photo; more become one PDF, a page each."""
    from PIL import Image

    from ..routers import documents

    kinds = {t["name"]: t["parent"] for t in _types()}
    name = body.get("type") if isinstance(body, dict) else None
    pages = body.get("pages") if isinstance(body, dict) else None
    if name not in kinds:
        raise ValueError("Choose what the bill is.")
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError(f"Send one to {MAX_PAGES} photos of the bill.")
    try:
        raw = [base64.b64decode(p, validate=True) for p in pages]
        images = [Image.open(io.BytesIO(b)) for b in raw]
        for im in images:
            if im.size[0] * im.size[1] > MAX_PIXELS:  # before it is decoded into memory
                raise ValueError("too large")
            im.load()
    except (binascii.Error, TypeError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("A photo could not be read. Take it again.") from exc

    stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
    if len(raw) == 1 and images[0].format in ("JPEG", "PNG"):
        file, data = f"Phone {stamp}.{'jpg' if images[0].format == 'JPEG' else 'png'}", raw[0]
    else:
        buf = io.BytesIO()
        rgb = [im.convert("RGB") for im in images]
        rgb[0].save(buf, "PDF", save_all=True, append_images=rgb[1:], resolution=200)
        file, data = f"Phone {stamp}.pdf", buf.getvalue()

    with SessionLocal() as db:
        r = documents.accept(db, file, data, None, name, kinds[name], source="phone")
    if r.get("status") == "queued":
        threading.Thread(target=documents._process, args=(r["document_id"], None, True),
                         daemon=True, name="phone-read").start()
    with _lock:
        if _session:
            _session["sent"].append(r["document_id"])
    return r


class _Phone(BaseHTTPRequestHandler):
    timeout = 30  # a stalled connection lets go

    def do_GET(self):  # noqa: N802 - http.server naming
        path = self.path.split("?", 1)[0]
        if m := re.fullmatch(r"/fonts/((?:Geist|GeistMono|Newsreader)-Variable\.woff2)", path):
            f = FONTS / m[1]
            return self._send(200, f.read_bytes(), "font/woff2", cache=True) if f.is_file() else self._send(404, b"", "text/plain")
        m = re.fullmatch(r"/p/([\w-]{16,64})(/sent)?", path)
        if not m or not _valid(m[1]):
            return self._send(410 if m else 404, GONE.encode(), "text/html; charset=utf-8")
        if m[2]:
            s = _live()
            return self._json(sent(list(s["sent"]) if s else []))
        # The types written in, so the page needs nothing more to start.
        types = json.dumps(_types()).replace("</", "<\\/")
        return self._send(200, PAGE.read_text(encoding="utf-8").replace("__TYPES__", types).encode(),
                          "text/html; charset=utf-8")

    def do_POST(self):  # noqa: N802
        m = re.fullmatch(r"/p/([\w-]{16,64})/send", self.path)
        if not m or not _valid(m[1]):
            return self._json({"error": ENDED}, 410)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BODY:
            return self._json({"error": "That is too much to send at once: send fewer pages."}, 413)
        try:
            r = receive(json.loads(self.rfile.read(n)))
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # the phone should hear why, not see the line drop
            return self._json({"error": f"The computer could not keep it: {exc}"}, 500)
        return self._json({"ok": True, "document_id": r["document_id"]})

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _send(self, code: int, data: bytes, kind: str, cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=604800" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):  # quiet
        pass
