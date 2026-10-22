"""Push notifications: standard Web Push (RFC 8291 encryption, RFC 8292 VAPID), made here
with `cryptography`, to phones and laptop browsers.

What is sent (PLAN-office section 6):
    a device waiting for approval            plain, to admins            (this control plane)
    a sync failing, an old Tally backup      plain, to admins            (the office server)
    overdue bills, big transactions          sealed with the books' key, to the people with
                                             the books tick who want them (the office server)
Sealed events stay blind here: the office server encrypts them with the data key and this
control plane only passes the ciphertext on; the device's service worker opens it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlparse

import httpx
import psycopg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cp import db
from cp.auth import Who, admin, approved, holders
from cp.office import office_server

router = APIRouter()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """RFC 8291 aes128gcm: one record, for the subscription's keys."""
    ua_pub = unb64u(p256dh)
    ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = eph.exchange(ec.ECDH(), ua)
    ikm = _hkdf(unb64u(auth), shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub + body


def vapid_header(endpoint: str) -> str:
    key = ec.derive_private_key(int.from_bytes(unb64u(db.env("VAPID_PRIVATE_KEY")), "big"), ec.SECP256R1())
    u = urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": db.env("PUBLIC_ORIGIN")}).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{claims}.{sig}, k={db.env('VAPID_PUBLIC_KEY')}"


def send(c: psycopg.Connection, audience: str, message: dict) -> int:
    """Push one message to every subscription that wants it, and is on an approved device
    of an active person allowed to see it. Gone subscriptions are dropped. Returns how many."""
    cap = "admin" if audience == "admin" else "books.alerts"
    subs = c.execute(
        "select s.id, s.endpoint, s.p256dh, s.auth from push_subscriptions s join devices d on d.id = s.device_id "
        "join users u on u.id = d.user_id where d.status = 'approved' and u.status = 'active' "
        "and u.caps && %s::text[] and coalesce((s.wants ->> %s)::boolean, false) "
        "order by s.last_ok_at desc nulls last, s.id", (holders(cap), audience)).fetchall()
    payload = json.dumps(message, separators=(",", ":")).encode()
    sent = 0
    end = time.monotonic() + 20          # one slow push service can't hold the request for long
    with httpx.Client(timeout=8) as client:
        for s in subs:
            if time.monotonic() > end:
                break
            try:
                r = client.post(s["endpoint"], content=encrypt(payload, s["p256dh"], s["auth"]), headers={
                    "Authorization": vapid_header(s["endpoint"]), "Content-Encoding": "aes128gcm",
                    "TTL": "86400", "Urgency": "high" if audience == "admin" else "normal"})
            except httpx.HTTPError:
                continue
            if r.status_code in (404, 410):
                c.execute("delete from push_subscriptions where id = %s", (s["id"],))
            elif r.status_code < 300:
                c.execute("update push_subscriptions set last_ok_at = now() where id = %s", (s["id"],))
                sent += 1
    return sent


def admins_told(_c: psycopg.Connection, title: str, body: str, url: str = "/admin") -> None:
    """A plain alert to admins, on a connection of its own, so the request that caused it
    keeps its own transaction whatever happens here. Never in the way of what caused it."""
    try:
        with db.connect() as c:
            send(c, "admin", {"title": title, "body": body, "url": url})
            c.commit()
    except Exception as exc:  # noqa: BLE001 - logged; the request that caused it goes on
        print(f"push: {type(exc).__name__}: {exc}", flush=True)


def push_host(endpoint: str) -> bool:
    h = (urlparse(endpoint).hostname or "").lower()
    return h in ("fcm.googleapis.com", "updates.push.services.mozilla.com") or h.endswith(
        (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com"))


class Subscription(BaseModel):
    endpoint: str = Field(pattern=r"^https://", max_length=1000)
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)
    wants: dict[str, bool] = {}


@router.get("/api/push/key")
def key():
    return {"key": db.env("VAPID_PUBLIC_KEY")}


@router.post("/api/push/subscribe")
def subscribe(body: Subscription, who: Who = Depends(approved), c: psycopg.Connection = Depends(db.conn)):
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(body.p256dh))     # a real key, or 422 below
    if not push_host(body.endpoint):
        raise HTTPException(400, "That isn't a browser push address.")
    wants = {"overdue": bool(body.wants.get("overdue")) and who.has("books.alerts"),
             "big": bool(body.wants.get("big")) and who.has("books.alerts"),
             "admin": bool(body.wants.get("admin")) and who.has("admin")}
    c.execute("insert into push_subscriptions (device_id, endpoint, p256dh, auth, wants) values (%s, %s, %s, %s, %s) "
              "on conflict (endpoint) do update set device_id = excluded.device_id, p256dh = excluded.p256dh, "
              "auth = excluded.auth, wants = excluded.wants "
              "where push_subscriptions.device_id = excluded.device_id",
              (who.device["id"], body.endpoint, body.p256dh, body.auth, json.dumps(wants)))
    # At most 5 per device: the oldest go.
    c.execute("delete from push_subscriptions where device_id = %s and id not in "
              "(select id from push_subscriptions where device_id = %s order by id desc limit 5)",
              (who.device["id"], who.device["id"]))
    c.commit()
    return {"wants": wants}


@router.post("/api/push/unsubscribe")
def unsubscribe(body: dict, who: Who = Depends(approved), c: psycopg.Connection = Depends(db.conn)):
    c.execute("delete from push_subscriptions where device_id = %s and endpoint = %s",
              (who.device["id"], str(body.get("endpoint", ""))))
    c.commit()
    return {"ok": True}


@router.post("/api/push/test")
def test(who: Who = Depends(approved), c: psycopg.Connection = Depends(db.conn)):
    rows = c.execute("select endpoint, p256dh, auth from push_subscriptions where device_id = %s",
                     (who.device["id"],)).fetchall()
    if not rows:
        raise HTTPException(400, "This device hasn't turned notifications on.")
    with httpx.Client(timeout=8) as client:
        for s in rows:
            try:
                client.post(s["endpoint"], content=encrypt(json.dumps(
                    {"title": "Notifications work", "body": "This device will be told.", "url": "/books"}).encode(),
                    s["p256dh"], s["auth"]), headers={"Authorization": vapid_header(s["endpoint"]),
                                                      "Content-Encoding": "aes128gcm", "TTL": "600"})
            except Exception:  # noqa: BLE001 - one bad row mustn't stop the rest
                continue
    return {"ok": True}


class Notice(BaseModel):
    kind: str = Field(pattern=r"^(overdue|big|alert)$")
    title: str = Field(default="", max_length=120)          # alerts only: plain, no figures
    body: str = Field(default="", max_length=400)
    kid: str = Field(default="", pattern=r"^(k[0-9]{1,6})?$")
    nonce: str = Field(default="", max_length=40)
    ct: str = Field(default="", max_length=3000)            # sealed events: ciphertext only


@router.post("/api/office/notify")
def notify(body: Notice, claims: dict = Depends(office_server), c: psycopg.Connection = Depends(db.conn)):
    if body.kind == "alert":
        n = send(c, "admin", {"title": body.title or "The office server", "body": body.body, "url": "/admin"})
    elif not (body.kid and body.nonce and body.ct):
        raise HTTPException(400, "Overdue and big-transaction notices go sealed.")
    else:
        n = send(c, body.kind, {"sealed": {"kid": body.kid, "nonce": body.nonce, "ct": body.ct}})
    c.commit()
    return {"sent": n}


class Settings(BaseModel):
    big_threshold_paise: int = Field(ge=100, le=10**13)


@router.get("/api/admin/settings")
def get_settings(who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    return {r["key"]: r["value"] for r in c.execute("select key, value from office_settings").fetchall()}


@router.put("/api/admin/settings")
def put_settings(body: Settings, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    c.execute("insert into office_settings (key, value) values ('big_threshold_paise', %s) "
              "on conflict (key) do update set value = excluded.value", (json.dumps(body.big_threshold_paise),))
    db.audit(c, "settings.changed", user_id=who.user["id"], device_id=who.device["id"],
             detail={"big_threshold_paise": body.big_threshold_paise})
    c.commit()
    return {"ok": True}


@router.get("/api/cron/office-check")
def office_check(c: psycopg.Connection = Depends(db.conn)):
    """Every 20 minutes, from Supabase's pg_cron: admins hear once when the office server has
    been silent for 3 hours in office hours (09:00-20:00 IST, Monday to Saturday). Anyone may
    call it; it only ever looks, and tells at most once per silence."""
    ist = time.gmtime(time.time() + 5.5 * 3600)
    if ist.tm_wday == 6 or not 9 <= ist.tm_hour < 20:
        return {"checked": False}
    row = c.execute("select max(last_token_ts) as ts from devices where kind = 'office_server' "
                    "and status = 'approved'").fetchone()
    last = int(row["ts"] or 0)
    if not last or time.time() - last < 3 * 3600:
        return {"checked": True, "silent": False}
    told = c.execute("select value from office_settings where key = 'silent_told_for'").fetchone()
    if told and int(told["value"]) == last:
        return {"checked": True, "silent": True, "told": False}
    c.execute("insert into office_settings (key, value) values ('silent_told_for', %s) "
              "on conflict (key) do update set value = excluded.value", (json.dumps(last),))
    c.commit()
    hours = int((time.time() - last) / 3600)
    admins_told(c, "The office server is silent",
                f"Nothing from it for {hours} hours. Is the PC on, with Ultimate Accountant open?")
    return {"checked": True, "silent": True, "told": True}
