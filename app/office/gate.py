"""The office server's sign-in gate: every request, from every PC, by someone signed in and
allowed that module (PLAN-office.md, section 3.3). Only in "server" mode.

A PC signs in by opening /office/sign-in?token=<its 15-minute access token from the
control plane>. The token is used once; the office session that replaces it is a random
HttpOnly cookie, 12 hours, renewed while its person and device stay on the roster. Each
request is checked against the newest roster, so revoking reaches the office in a minute.

What each path needs:
    /api/health, /office/sign-in                 nobody
    /, /office/me                                anyone signed in
    /bridge/...                                  a tib.* key by route (any tib.* for its screens)
    /keeper/..., /office/...                     admin
    /api/shutdown, /api/update/..., /api/data-folder/...   admin (they stop or move the server)
    everything else (/console/..., /api/...)      a con.* key by route (any con.* for the shell);
                                                 unlisted routes need the area's strictest key
    (docs/permissions.md; the old console/tib/books keys imply every key of their area)
"""

from __future__ import annotations

import hashlib
import html
import re
import secrets
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.office import caps, roster, settings
from app.office.cloud import _unb64, check_token

COOKIE = "ua_office"
HOURS = 12
_sessions: dict[str, dict] = {}
_used: dict[str, float] = {}          # access tokens already turned into sessions, until they expire
_lock = threading.Lock()
_loaded = False


# Console routes, by the permission each needs. Anything under /api not listed here needs
# the strictest of the area (STRICT), never "any".
_CON = (
    ("con.recon", ("/api/reconcile", "/api/runs", "/api/bank", "/api/statement", "/api/compare", "/api/items",
                   "/api/open-items", "/api/decisions", "/api/suppliers", "/api/workspaces", "/api/probe",
                   "/api/docs/read", "/api/load-sample", "/api/ocr/status")),
    ("con.exports", ("/api/exports",)),
    ("con.reports", ("/api/download", "/api/files")),
    ("con.ai", ("/api/ai",)),
)
STRICT = {"con": "con.settings", "tib": "tib.post"}


def _under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _bridge(path: str, method: str) -> str:
    if not path.startswith("/api") or path in ("/api", "/api/health"):
        return "any:tib"                                    # the Bridge's own screens, health
    if path == "/api/settings" and method == "GET":
        return "any:tib"
    if _under(path, ("/api/phone", "/api/documents/upload")):
        return "tib.upload"
    if path == "/api/documents/status":
        return "any:tib"
    if _under(path, ("/api/documents",)) or path in ("/api/entries", "/api/entries/stats"):
        return "tib.review"
    if _under(path, ("/api/entries",)):
        last = path.rsplit("/", 1)[-1]
        if last in ("approve", "approve-batch", "create-master"):
            return "tib.post"
        if method == "GET" or last in ("check-duplicates", "rematch", "reject", "restore") or last.isdigit():
            return "tib.review"
        return STRICT["tib"]
    if _under(path, ("/api/bank",)):
        return "tib.post" if path.endswith(("/post", "/ledgers")) else "tib.bank"
    if _under(path, ("/api/tally",)):
        # reading the ledger and voucher-type lists is what every Bridge screen does; reading
        # them again from Tally (discover, sync) is the permission
        return "any:tib" if method == "GET" else "tib.sync"
    return STRICT["tib"]                                    # settings writes, backup(s), ai/*: admin checks sit in the handlers


def need(path: str, method: str = "GET") -> str | None:
    """None: public. 'any': anyone signed in. 'any:<area>': any key of that area. Else one key."""
    if path in ("/api/health", "/office/sign-in"):
        return None
    if path in ("/", "/office/me"):
        return "any"
    if path == "/bridge" or path.startswith("/bridge/"):
        return _bridge(path[7:], method)
    if path.startswith(("/keeper", "/office/")):
        return "admin"
    if path == "/api/shutdown" or path.startswith(("/api/update/", "/api/data-folder/")):
        return "admin"
    if path == "/console" or path.startswith(("/console/", "/favicon.ico")):
        return "any:con"
    if path in ("/api/bootstrap", "/api/update") or (path == "/api/settings" and method == "GET"):
        return "any:con"
    if path == "/api/settings":
        return "con.settings"
    for key, prefixes in _CON:
        if _under(path, prefixes):
            return key
    return STRICT["con"]


def _load() -> None:
    global _loaded
    if not _loaded:
        _sessions.update(settings.read_json("sessions.json", {}) or {})
        _loaded = True


def _save() -> None:
    now = time.time()
    for k in [k for k, s in _sessions.items() if s["expires"] < now]:
        del _sessions[k]
    settings.write_json("sessions.json", _sessions)


def _page(status: int, title: str, text: str) -> HTMLResponse:
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><body style=\"font:15px 'Segoe UI',sans-serif;margin:0;background:#f5f6f8;"
            f"color:#1c1e23\"><div style='background:#74191f;color:#e9c46a;padding:14px 16px;font-weight:700;"
            f"letter-spacing:.16em;font-size:13px'>ULTIMATE ACCOUNTANT</div><div style='max-width:560px;margin:24px auto;"
            f"background:#fff;border:1px solid #dfe5ec;padding:24px'><h1 style='font-size:1.6rem;margin:0 0 8px'>"
            f"{html.escape(title)}</h1><p style='color:#454a54'>{html.escape(text)}</p></div>")
    return HTMLResponse(body, status_code=status)


def _refuse(request: Request, status: int, title: str, text: str):
    if "text/html" in request.headers.get("accept", ""):
        return _page(status, title, text)
    return JSONResponse({"detail": f"{title} {text}"}, status_code=status)


def who(request: Request) -> dict | None:
    """The signed-in person on this request, checked against the newest roster."""
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    with _lock:
        _load()
        s = _sessions.get(key)
        if not s or s["expires"] < time.time():
            return None
        r = roster.current()
        person = r.person(s["user_id"], s["device"]) if r else None
        if not person:
            return None
        if s["expires"] - time.time() < HOURS * 3600 - 300:      # sliding, written at most every 5 minutes
            s["expires"] = time.time() + HOURS * 3600
            _save()
    return {**person, "device": s["device"]}


def sign_in(request: Request):
    token, back = request.query_params.get("token", ""), request.query_params.get("next", "/")
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    key = settings.load().get("token_key")
    try:
        claims = check_token(token, "ua-access", _unb64(key)) if key else None
    except ValueError:
        claims = None
    if not claims or claims.get("kind") not in ("office_client", "office_server"):
        return _page(401, "This sign-in didn't work.", "Close Ultimate Accountant and open it again.")
    r = roster.current()
    if not r or not r.person(claims["sub"], claims["device"]):
        return _page(403, "This PC may not sign in here.",
                     "It isn't approved on the office server's list yet, or it was revoked. Ask your admin.")
    used = hashlib.sha256(token.encode()).hexdigest()
    with _lock:
        _load()
        now = time.time()
        for k in [k for k, t in _used.items() if t < now]:
            del _used[k]
        if used in _used:
            return _page(401, "This sign-in was already used.", "Close Ultimate Accountant and open it again.")
        _used[used] = claims["exp"]
        cookie = secrets.token_urlsafe(32)
        _sessions[hashlib.sha256(cookie.encode()).hexdigest()] = {
            "user_id": claims["sub"], "device": claims["device"], "email": claims["email"],
            "created": now, "expires": now + HOURS * 3600}
        _save()
    resp = RedirectResponse(back, status_code=302)
    resp.set_cookie(COOKIE, cookie, max_age=HOURS * 3600, httponly=True, secure=True, samesite="strict", path="/")
    return resp


def install(app: FastAPI) -> None:
    """In server mode, put the gate in front of everything in app, and its own pages."""
    if settings.mode() != "server":
        return

    @app.middleware("http")
    async def gate(request: Request, call_next):
        need_ = need(request.url.path, request.method)
        if need_ is None:
            return await call_next(request)
        person = who(request)
        if not person:
            return _refuse(request, 401, "Sign in from Ultimate Accountant.",
                           "Open Ultimate Accountant on your PC: it signs you in to the office server.")
        if not caps.allows(person["caps"], need_):
            return _refuse(request, 403, "That isn't part of your access.",
                           "Your admin decides which parts of Ultimate Accountant you can use.")
        request.state.office_person = person
        return await call_next(request)

    app.add_api_route("/office/sign-in", sign_in, methods=["GET"], include_in_schema=False)

    @app.get("/office/me", include_in_schema=False)
    def me(request: Request):
        p = request.state.office_person
        return {"email": p["email"], "name": p["name"], "caps": p["caps"], "mode": "server"}


_LOCAL = re.compile(r"(?:http://)?(?:127\.0\.0\.1|localhost):(\d+)", re.I)


async def local_only(request: Request, call_next):
    """Outside server mode this app is for this PC's own browser. A Host that isn't ours is
    DNS rebinding (400); a write from another site, by Origin or Sec-Fetch-Site, is a
    cross-site post (403). No Origin and no Sec-Fetch-Site (curl, our own tools) passes."""
    host = _LOCAL.fullmatch(request.headers.get("host", ""))
    if not host or host[0].lower().startswith("http"):
        return JSONResponse({"detail": "Not this computer's address."}, status_code=400)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        ours = origin is None or (origin.lower().startswith("http://") and (m := _LOCAL.fullmatch(origin)) and m[1] == host[1])
        if not ours or request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return JSONResponse({"detail": "That request came from another site."}, status_code=403)
    return await call_next(request)


MAX_UPLOAD = 200 * 1024 * 1024


class _TooBig(Exception):
    pass


class upload_limit:
    """Outer ASGI middleware: a write under /api/ or /bridge/api/ over 200 MB is refused (413),
    by Content-Length up front, or by counting the bytes of a chunked body as they arrive."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH")
                or not scope["path"].startswith(("/api/", "/bridge/api/"))):
            return await self.app(scope, receive, send)
        size = dict(scope["headers"]).get(b"content-length")
        started, seen = False, 0

        async def refuse():
            await JSONResponse({"detail": "That file is too big (over 200 MB)."}, status_code=413)(scope, receive, send)

        if size is not None:
            if size.isdigit() and int(size) > MAX_UPLOAD:
                return await refuse()
            return await self.app(scope, receive, send)

        async def count():
            nonlocal seen
            msg = await receive()
            seen += len(msg.get("body", b""))
            if seen > MAX_UPLOAD:
                raise _TooBig
            return msg

        async def watch(msg):
            nonlocal started
            started = True
            await send(msg)

        try:
            await self.app(scope, count, watch)
        except Exception as e:
            if seen <= MAX_UPLOAD or started:
                raise
            await refuse()
