"""Office PCs: linking one to a person, its tokens, the roster, and the wrapped data keys.

Linking (from the PC's own browser, signed in as the PC's person):
  The PC makes its keys and opens /link with them; the page asks the person to confirm and
  posts them here with the PC's signature over
      ua-link:v1, kind, nonce, sign_pub, wrap_pub
  The PC is then a device like any other: pending until an admin's device approves it.
Tokens: the PC signs  ua-token:v1, device id, unix time  (later than its last), and gets a
  15-minute access token while it is approved and its person active.
Roster: the office server's token alone fetches everyone and every device, signed.
Keys: the office server uploads the DEK wrapped for each device; a device fetches its own.
"""

from __future__ import annotations

import time

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cp import db, tokens
from cp.auth import Who, approved, client_ip, signed_in
from cp.devices import fingerprint, public, public_key, verify

router = APIRouter()

PC_KINDS = ("office_server", "office_client")


def link_text(kind: str, nonce: str, sign_pub: str, wrap_pub: str) -> str:
    return "\n".join(["ua-link:v1", kind, nonce, sign_pub, wrap_pub])


def token_text(device_id: str, ts: int) -> str:
    return "\n".join(["ua-token:v1", str(device_id), str(ts)])


class Link(BaseModel):
    kind: str
    name: str = Field("", max_length=80)
    os: str = Field("", max_length=80)
    app_version: str = Field("", max_length=40)
    sign_pub: str = Field(max_length=400)
    wrap_pub: str = Field(max_length=400)
    nonce: str = Field(min_length=16, max_length=80)
    signature: str = Field(max_length=200)


@router.post("/api/devices/link")
def link(body: Link, who: Who = Depends(signed_in), c: psycopg.Connection = Depends(db.conn)):
    if body.kind not in PC_KINDS:
        raise HTTPException(400, "Only office PCs are linked this way.")
    if body.kind == "office_server" and not who.has("admin"):
        raise HTTPException(403, "Only an admin can link the office server.")
    public_key(body.wrap_pub)
    if not verify(body.sign_pub, link_text(body.kind, body.nonce, body.sign_pub, body.wrap_pub), body.signature):
        raise HTTPException(400, "The PC's signature does not check out.")
    if c.execute("select 1 from devices where sign_pub = %s", (body.sign_pub,)).fetchone():
        raise HTTPException(409, "That PC is already linked.")
    fp = fingerprint(body.sign_pub, body.wrap_pub)
    d = c.execute("insert into devices (user_id, kind, name, os, app_version, sign_pub, wrap_pub, fingerprint, last_ip) "
                  "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
                  (who.user["id"], body.kind, body.name.strip(), body.os.strip(), body.app_version.strip(),
                   body.sign_pub, body.wrap_pub, fp, who.ip)).fetchone()
    db.audit(c, "device.linked", user_id=who.user["id"], device_id=d["id"],
             detail={"kind": body.kind, "name": d["name"], "fingerprint": fp}, ip=who.ip)
    c.commit()
    from cp import push
    push.admins_told(c, "A PC is waiting for approval",
                     f"{who.user['email']}: {d['name']}. Check its 24 digits before approving.")
    return {"device": public(d)}


class TokenRequest(BaseModel):
    device_id: str = Field(max_length=40)
    ts: int
    signature: str = Field(max_length=200)


@router.post("/api/devices/token")
def token(body: TokenRequest, request: Request, c: psycopg.Connection = Depends(db.conn)):
    d = c.execute("select d.*, u.email, u.name as owner, u.caps, u.status as user_status "
                  "from devices d join users u on u.id = d.user_id where d.id::text = %s", (body.device_id,)).fetchone()
    if not d or d["kind"] not in PC_KINDS:
        raise HTTPException(404, "No such PC.")
    if abs(time.time() - body.ts) > 120 or body.ts <= d["last_token_ts"]:
        raise HTTPException(400, "That request is stale or was sent before. Check this PC's clock.")
    if not verify(d["sign_pub"], token_text(d["id"], body.ts), body.signature):
        raise HTTPException(401, "The PC's signature does not check out.")
    if d["status"] == "revoked":
        raise HTTPException(410, "This PC was revoked.")
    if d["user_status"] != "active":
        raise HTTPException(403, "This PC's person is disabled.")
    ip = client_ip(request)
    c.execute("update devices set last_token_ts = %s, last_seen = now(), last_ip = %s where id = %s",
              (body.ts, ip, d["id"]))
    c.commit()
    if d["status"] != "approved":
        raise HTTPException(403, "This PC is waiting to be approved.")
    claims = {"sub": d["user_id"], "email": d["email"], "name": d["owner"], "caps": list(d["caps"]),
              "device": str(d["id"]), "kind": d["kind"]}
    return {"token": tokens.issue(claims, 900, "ua-access"), "expires_in": 900, "claims": claims}


def office_server(claims: dict = Depends(tokens.bearer), c: psycopg.Connection = Depends(db.conn)) -> dict:
    if claims.get("kind") != "office_server":
        raise HTTPException(403, "Only the office server can do that.")
    # The token lasts 15 minutes; a revoked server or disabled owner stops at once.
    d = c.execute("select d.status, u.status as user_status from devices d join users u on u.id = d.user_id "
                  "where d.id::text = %s and d.kind = 'office_server'", (str(claims.get("device")),)).fetchone()
    if not d or d["status"] != "approved":
        raise HTTPException(401, "This office server isn't approved.")
    if d["user_status"] != "active":
        raise HTTPException(403, "This PC's person is disabled.")
    return claims


@router.get("/api/office/roster")
def roster(claims: dict = Depends(office_server), c: psycopg.Connection = Depends(db.conn)):
    users = c.execute("select id, email, name, caps, status from users order by id").fetchall()
    devices = c.execute("select id, user_id, kind, name, status, sign_pub, wrap_pub, fingerprint, approval, "
                        "approval_sig, approved_by_device from devices order by first_seen").fetchall()
    keys = c.execute("select device_id, kid from wrapped_keys").fetchall()
    ring = c.execute("select kid, is_current from key_ring order by created_at").fetchall()
    body = {"users": [{**u, "caps": list(u["caps"])} for u in users],
            "devices": [{**d, "id": str(d["id"]), "approved_by_device": str(d["approved_by_device"] or "") or None}
                        for d in devices],
            "wrapped": [{"device": str(k["device_id"]), "kid": k["kid"]} for k in keys],
            "key_ring": ring,
            "settings": {r["key"]: r["value"] for r in c.execute("select key, value from office_settings").fetchall()}}
    return {"roster": tokens.issue(body, 600, "ua-roster")}


class Wrapped(BaseModel):
    device_id: str = Field(max_length=40)
    epk: str = Field(max_length=400)
    nonce: str = Field(max_length=40)
    ct: str = Field(max_length=200)


class Keys(BaseModel):
    kid: str = Field(pattern=r"^k[0-9]{1,6}$")
    current: bool = False
    keys: list[Wrapped] = Field(max_length=500)


@router.put("/api/office/wrapped-keys")
def put_keys(body: Keys, claims: dict = Depends(office_server), c: psycopg.Connection = Depends(db.conn)):
    c.execute("insert into key_ring (kid) values (%s) on conflict (kid) do nothing", (body.kid,))
    for k in body.keys:
        c.execute("insert into wrapped_keys (device_id, kid, epk, nonce, ct) values (%s, %s, %s, %s, %s) "
                  "on conflict (device_id, kid) do update set epk = excluded.epk, nonce = excluded.nonce, "
                  "ct = excluded.ct, created_at = now()", (k.device_id, body.kid, k.epk, k.nonce, k.ct))
    if body.current:
        c.execute("update key_ring set is_current = (kid = %s)", (body.kid,))
    db.audit(c, "keys.wrapped", user_id=claims["sub"], device_id=claims["device"],
             detail={"kid": body.kid, "devices": len(body.keys), "current": body.current})
    c.commit()
    return {"ok": True}


@router.get("/api/keys/me")
def my_keys(who: Who = Depends(approved), c: psycopg.Connection = Depends(db.conn)):
    rows = c.execute("select w.kid, w.epk, w.nonce, w.ct from wrapped_keys w where w.device_id = %s order by w.kid",
                     (who.device["id"],)).fetchall()
    cur = c.execute("select kid from key_ring where is_current").fetchone()
    return {"device": str(who.device["id"]), "current": cur["kid"] if cur else None, "keys": rows}
