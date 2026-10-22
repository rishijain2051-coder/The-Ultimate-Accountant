"""Sign-in with Google, against the allow-list of people; sessions; who is asking.

The flow is Google's server-side authorization code flow with PKCE, a state and a nonce.
The ID token comes straight from Google's token endpoint over TLS, using our client
secret, so (OpenID Connect Core 3.1.3.7) TLS stands in for its signature. Its issuer,
audience, expiry, nonce and verified email are still checked.

A session is a random cookie (HttpOnly, Secure, SameSite=Lax); only its SHA-256 is
stored. A session belongs to one person and, once the device has proved it holds its
key (cp/devices.py), to one device.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from cp import db

router = APIRouter()

SESSION_COOKIE = "ua_session"
OAUTH_COOKIE = "ua_oauth"
SESSION_DAYS = 30
BROAD = {"console": "con.", "tib": "tib.", "books": "books."}   # the old broad keys: each implies its area's detailed keys
DETAILED = ("con.recon", "con.exports", "con.reports", "con.settings", "con.ai",
            "tib.upload", "tib.review", "tib.post", "tib.bank", "tib.sync",
            "books.view", "books.alerts", "books.balances", "books.sales", "admin")
CAPS = DETAILED + tuple(BROAD)


def holders(cap: str) -> list[str]:
    """Every stored key that gives `cap`: itself and its broad key."""
    return [cap] + [b for b, prefix in BROAD.items() if cap.startswith(prefix)]


def holds(caps, cap: str) -> bool:
    return any(k in caps for k in holders(cap))


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def origin() -> str:
    return db.env("PUBLIC_ORIGIN").rstrip("/")


def redirect_uri() -> str:
    return origin() + "/api/auth/google/callback"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64]


def _safe_return(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


@router.get("/api/auth/google/start")
def google_start(return_to: str = "/"):
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": db.env("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
    })
    r = RedirectResponse(url, status_code=302)
    saved = b64url(json.dumps([state, nonce, verifier, _safe_return(return_to)]).encode())
    r.set_cookie(OAUTH_COOKIE, saved, max_age=600, httponly=True, secure=True, samesite="lax", path="/api/auth")
    return r


def exchange_code(code: str, verifier: str) -> dict:
    """Trade the code for Google's ID token and return its checked claims."""
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=15, data={
        "code": code, "client_id": db.env("GOOGLE_CLIENT_ID"), "client_secret": db.env("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": verifier})
    if r.status_code != 200:
        raise ValueError(f"Google refused the code: {r.json().get('error', r.status_code)}")
    return json.loads(unb64url(r.json()["id_token"].split(".")[1]))


def check_claims(claims: dict, nonce: str) -> str:
    """The verified, lower-cased email, or ValueError saying what is wrong."""
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("wrong issuer")
    if claims.get("aud") != db.env("GOOGLE_CLIENT_ID"):
        raise ValueError("wrong audience")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < time.time():
        raise ValueError("expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ValueError("wrong nonce")
    if claims.get("email_verified") is not True or not claims.get("email") or not claims.get("sub"):
        raise ValueError("no verified email")
    return claims["email"].strip().lower()


def _page(where: str) -> RedirectResponse:
    r = RedirectResponse(where, status_code=302)
    r.delete_cookie(OAUTH_COOKIE, path="/api/auth")
    return r


@router.get("/api/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = "",
                    c: psycopg.Connection = Depends(db.conn)):
    ip = client_ip(request)
    try:
        st, nonce, verifier, back = json.loads(unb64url(request.cookies.get(OAUTH_COOKIE, "")))
    except Exception:  # noqa: BLE001 - no cookie, or not ours
        return _page("/?signin=expired")
    if not state or not secrets.compare_digest(state, st):
        return _page("/?signin=expired")
    if error or not code:
        return _page("/?signin=cancelled")
    try:
        claims = exchange_code(code, verifier)
        email = check_claims(claims, nonce)
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        db.audit(c, "signin.failed", detail={"why": str(exc)[:200]}, ip=ip)
        c.commit()
        return _page("/?signin=failed")

    u = c.execute("select * from users where lower(email) = %s", (email,)).fetchone()
    if not u or u["status"] != "active" or (u["google_sub"] and u["google_sub"] != claims["sub"]):
        why = "not on the list" if not u else ("disabled" if u["status"] != "active" else "a different Google account")
        db.audit(c, "signin.refused", user_id=u["id"] if u else None, detail={"email": email, "why": why}, ip=ip)
        c.commit()
        return _page("/?signin=refused")
    if not u["google_sub"]:
        c.execute("update users set google_sub = %s, name = coalesce(nullif(name, ''), %s) where id = %s",
                  (claims["sub"], claims.get("name", "")[:120], u["id"]))

    token = secrets.token_urlsafe(32)
    c.execute("insert into sessions (token_hash, user_id, challenge, expires_at, ip, user_agent) "
              "values (%s, %s, %s, now() + make_interval(days => %s), %s, %s)",
              (hashlib.sha256(token.encode()).digest(), u["id"], secrets.token_urlsafe(24), SESSION_DAYS, ip,
               request.headers.get("user-agent", "")[:300]))
    db.audit(c, "signin.ok", user_id=u["id"], ip=ip)
    c.commit()
    r = _page(back)
    r.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=True,
                 samesite="lax", path="/")
    return r


@dataclass
class Who:
    session_id: int
    user: dict
    device: dict | None
    challenge: str
    ip: str

    def has(self, cap: str) -> bool:
        return holds(self.user["caps"], cap)


def _who(request: Request, c: psycopg.Connection) -> Who | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    s = c.execute(
        "select s.id, s.device_id, s.challenge, s.last_used < now() - interval '5 minutes' as stale, "
        "       u.id as uid, u.email, u.name, u.caps, u.status "
        "from sessions s join users u on u.id = s.user_id "
        "where s.token_hash = %s and s.revoked_at is null and s.expires_at > now()",
        (hashlib.sha256(token.encode()).digest(),)).fetchone()
    if not s or s["status"] != "active":
        return None
    device = None
    if s["device_id"]:
        device = c.execute("select * from devices where id = %s", (s["device_id"],)).fetchone()
        if device["status"] == "revoked":
            return None
    ip = client_ip(request)
    if s["stale"]:  # a sliding expiry and a last-seen, written at most every 5 minutes
        c.execute("update sessions set last_used = now(), expires_at = now() + make_interval(days => %s) "
                  "where id = %s", (SESSION_DAYS, s["id"]))
        if device:
            c.execute("update devices set last_seen = now(), last_ip = %s where id = %s", (ip, device["id"]))
        c.commit()
        c.execute(f"set local search_path = {db.SCHEMA}")
    user = {"id": s["uid"], "email": s["email"], "name": s["name"], "caps": list(s["caps"])}
    return Who(s["id"], user, device, s["challenge"], ip)


def signed_in(request: Request, c: psycopg.Connection = Depends(db.conn)) -> Who:
    """Anyone signed in, approved device or not."""
    who = _who(request, c)
    if not who:
        raise HTTPException(401, "Sign in first.")
    return who


def approved(who: Who = Depends(signed_in)) -> Who:
    """Signed in on a device that has been approved."""
    if not who.device or who.device["status"] != "approved":
        raise HTTPException(403, "This device is waiting to be approved.")
    return who


def admin(who: Who = Depends(approved)) -> Who:
    if not who.has("admin"):
        raise HTTPException(403, "Only an admin can do that.")
    return who


@router.post("/api/auth/sign-out")
def sign_out(who: Who = Depends(signed_in), c: psycopg.Connection = Depends(db.conn)):
    c.execute("update sessions set revoked_at = now() where id = %s", (who.session_id,))
    db.audit(c, "signout", user_id=who.user["id"], device_id=who.device["id"] if who.device else None, ip=who.ip)
    c.commit()
    r = JSONResponse({"ok": True})
    r.delete_cookie(SESSION_COOKIE, path="/")
    return r
