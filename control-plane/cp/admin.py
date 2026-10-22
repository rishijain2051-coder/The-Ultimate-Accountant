"""The admin console's API: people and their access, devices, the audit log.

Everything here needs an approved device whose owner has the `admin` tick. Approving a
device needs that device's own signature over exactly what is being approved.
"""

from __future__ import annotations

import re
import time

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cp import db
from cp.auth import CAPS, Who, admin
from cp.devices import approve_text, public, verify

router = APIRouter(prefix="/api/admin")

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _caps(caps: list[str]) -> list[str]:
    bad = [x for x in caps if x not in CAPS]
    if bad:
        raise HTTPException(400, f"Unknown access: {', '.join(bad)}.")
    return sorted(set(caps))


@router.get("/users")
def users(who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    rows = c.execute(
        "select u.id, u.email, u.name, u.caps, u.status, u.created_at, u.google_sub is not null as signed_in_before, "
        "  (select count(*) from devices d where d.user_id = u.id and d.status = 'approved') as devices, "
        "  (select max(last_seen) from devices d where d.user_id = u.id) as last_seen "
        "from users u order by u.status, lower(u.email)").fetchall()
    return {"users": rows}


class NewUser(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field("", max_length=120)
    caps: list[str] = []


@router.post("/users")
def add_user(body: NewUser, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    email = body.email.strip().lower()
    if not EMAIL.fullmatch(email):
        raise HTTPException(400, "That is not an email address.")
    if c.execute("select 1 from users where lower(email) = %s", (email,)).fetchone():
        raise HTTPException(409, "That person is already on the list.")
    u = c.execute("insert into users (email, name, caps, created_by) values (%s, %s, %s, %s) returning id",
                  (email, body.name.strip(), _caps(body.caps), who.user["id"])).fetchone()
    db.audit(c, "user.added", user_id=who.user["id"], device_id=who.device["id"],
             detail={"email": email, "caps": _caps(body.caps)}, ip=who.ip)
    c.commit()
    return {"id": u["id"]}


class UserChange(BaseModel):
    name: str | None = Field(None, max_length=120)
    caps: list[str] | None = None
    status: str | None = None


@router.patch("/users/{uid}")
def change_user(uid: int, body: UserChange, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    u = c.execute("select * from users where id = %s", (uid,)).fetchone()
    if not u:
        raise HTTPException(404, "No such person.")
    caps = _caps(body.caps) if body.caps is not None else list(u["caps"])
    status = body.status if body.status is not None else u["status"]
    if status not in ("active", "disabled"):
        raise HTTPException(400, "Status is active or disabled.")
    if uid == who.user["id"] and (status != "active" or "admin" not in caps):
        raise HTTPException(400, "You can't take away your own admin access.")
    c.execute("update users set name = %s, caps = %s, status = %s where id = %s",
              (u["name"] if body.name is None else body.name.strip(), caps, status, uid))
    if status == "disabled":
        c.execute("update sessions set revoked_at = now() where user_id = %s and revoked_at is null", (uid,))
    db.audit(c, "user.changed", user_id=who.user["id"], device_id=who.device["id"],
             detail={"email": u["email"], "caps": caps, "status": status}, ip=who.ip)
    c.commit()
    return {"ok": True}


@router.get("/devices")
def devices(who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    rows = c.execute("select d.*, u.email, u.name as owner, u.caps from devices d join users u on u.id = d.user_id "
                     "order by (d.status = 'pending') desc, d.last_seen desc").fetchall()
    return {"devices": [{**public(r), "email": r["email"], "owner": r["owner"], "caps": r["caps"]} for r in rows],
            "this_device": str(who.device["id"])}


class Approval(BaseModel):
    at: int
    signature: str = Field(max_length=200)


@router.post("/devices/{did}/approve")
def approve(did: str, body: Approval, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    d = c.execute("select d.*, u.email, u.caps from devices d join users u on u.id = d.user_id "
                  "where d.id::text = %s", (did,)).fetchone()
    if not d:
        raise HTTPException(404, "No such device.")
    if d["status"] != "pending":
        raise HTTPException(409, f"That device is {d['status']}, not waiting.")
    if abs(time.time() - body.at) > 600:
        raise HTTPException(400, "That approval is too old, or this device's clock is wrong.")
    text = approve_text(d["id"], d["email"], list(d["caps"]), d["sign_pub"], d["wrap_pub"], body.at)
    if not verify(who.device["sign_pub"], text, body.signature):
        db.audit(c, "device.approve-refused", user_id=who.user["id"], device_id=who.device["id"],
                 detail={"device": str(d["id"]), "why": "bad signature"}, ip=who.ip)
        c.commit()
        raise HTTPException(400, "Your device's signature does not match this device. Nothing was approved.")
    c.execute("update devices set status = 'approved', approval = %s, approval_sig = %s, approved_by_device = %s, "
              "approved_at = now() where id = %s", (text, body.signature, who.device["id"], d["id"]))
    db.audit(c, "device.approved", user_id=who.user["id"], device_id=who.device["id"],
             detail={"device": str(d["id"]), "owner": d["email"], "fingerprint": d["fingerprint"]}, ip=who.ip)
    c.commit()
    return {"ok": True}


@router.post("/devices/{did}/revoke")
def revoke(did: str, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    if did == str(who.device["id"]):
        raise HTTPException(400, "That's the device you are using. Revoke it from another one.")
    d = c.execute("update devices set status = 'revoked', revoked_at = now() where id::text = %s "
                  "and status <> 'revoked' returning id, fingerprint", (did,)).fetchone()
    if not d:
        raise HTTPException(404, "No such device, or it is already revoked.")
    c.execute("update sessions set revoked_at = now() where device_id = %s and revoked_at is null", (d["id"],))
    db.audit(c, "device.revoked", user_id=who.user["id"], device_id=who.device["id"],
             detail={"device": did, "fingerprint": d["fingerprint"]}, ip=who.ip)
    c.commit()
    return {"ok": True}


class Rename(BaseModel):
    name: str = Field(max_length=80)


@router.patch("/devices/{did}")
def rename(did: str, body: Rename, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    if not c.execute("update devices set name = %s where id::text = %s returning id", (body.name.strip(), did)).fetchone():
        raise HTTPException(404, "No such device.")
    db.audit(c, "device.renamed", user_id=who.user["id"], device_id=who.device["id"],
             detail={"device": did, "name": body.name.strip()}, ip=who.ip)
    c.commit()
    return {"ok": True}


@router.get("/audit")
def audit(before: int | None = None, who: Who = Depends(admin), c: psycopg.Connection = Depends(db.conn)):
    rows = c.execute("select a.id, a.at, a.action, a.detail, a.ip, u.email, d.name as device "
                     "from audit a left join users u on u.id = a.user_id left join devices d on d.id = a.device_id "
                     "where (%s::bigint is null or a.id < %s) order by a.id desc limit 100", (before, before)).fetchall()
    return {"audit": rows}
