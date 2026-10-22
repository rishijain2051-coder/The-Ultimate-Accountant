"""Devices: each holds its own keys, proves it, and waits for an admin's signed approval.

A device makes two P-256 keys when first opened: ECDSA to sign, ECDH to receive the
data key later (Phase D). In a browser both private halves are non-extractable
WebCrypto keys in IndexedDB. The public halves (SPKI, base64) are its identity, and
the fingerprint, 24 digits from their SHA-256, is what you compare on both screens.

Texts signed, exactly (lines joined by "\\n"):
  attach:   ua-attach:v1, the session's challenge, sign_pub, wrap_pub
  approve:  ua-approve:v1, device id, owner's email, owner's caps (sorted, comma), sign_pub, wrap_pub, time
Signatures are WebCrypto's raw r||s (64 bytes), base64.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cp import db
from cp.auth import Who, signed_in

router = APIRouter()


def public_key(b64: str) -> ec.EllipticCurvePublicKey:
    try:
        key = load_der_public_key(base64.b64decode(b64, validate=True))
    except Exception as exc:  # noqa: BLE001 - anything unreadable is simply not a key
        raise HTTPException(400, "That is not a public key.") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise HTTPException(400, "Keys must be P-256.")
    return key


def verify(pub_b64: str, text: str, sig_b64: str) -> bool:
    try:
        raw = base64.b64decode(sig_b64, validate=True)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        public_key(pub_b64).verify(der, text.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, HTTPException):
        return False


def fingerprint(sign_pub: str, wrap_pub: str) -> str:
    h = hashlib.sha256(base64.b64decode(sign_pub) + base64.b64decode(wrap_pub)).digest()
    # The first 12 digits are the old 12-digit code; the next 12 come from the next 8 bytes of the hash.
    d = f"{int.from_bytes(h[:8], 'big') % 10**12:012d}{int.from_bytes(h[8:16], 'big') % 10**12:012d}"
    return " ".join(d[i:i + 6] for i in range(0, 24, 6))


def attach_text(challenge: str, sign_pub: str, wrap_pub: str) -> str:
    return "\n".join(["ua-attach:v1", challenge, sign_pub, wrap_pub])


def approve_text(device_id: str, email: str, caps: list[str], sign_pub: str, wrap_pub: str, at: int) -> str:
    return "\n".join(["ua-approve:v1", str(device_id), email, ",".join(sorted(caps)), sign_pub, wrap_pub, str(at)])


def public(d: dict | None) -> dict | None:
    if not d:
        return None
    return {k: (str(v) if k in ("id", "approved_by_device") and v else v)
            for k, v in d.items() if k not in ("approval", "approval_sig")}


@router.get("/api/session")
def session(who: Who = Depends(signed_in)):
    return {"user": who.user, "device": public(who.device), "challenge": who.challenge}


class Attach(BaseModel):
    sign_pub: str = Field(max_length=400)
    wrap_pub: str = Field(max_length=400)
    signature: str = Field(max_length=200)
    name: str = Field("", max_length=80)
    os: str = Field("", max_length=80)
    app_version: str = Field("", max_length=40)


@router.post("/api/devices/attach")
def attach(body: Attach, who: Who = Depends(signed_in), c: psycopg.Connection = Depends(db.conn)):
    """This browser proves it holds its key, and the session becomes its own: the device
    it registered before, or a new one, pending until an admin approves it."""
    public_key(body.wrap_pub)
    if not verify(body.sign_pub, attach_text(who.challenge, body.sign_pub, body.wrap_pub), body.signature):
        raise HTTPException(400, "The device's signature does not check out.")
    uid = who.user["id"]
    d = c.execute("select * from devices where sign_pub = %s", (body.sign_pub,)).fetchone()
    if d and (d["user_id"] != uid or d["wrap_pub"] != body.wrap_pub):
        db.audit(c, "device.attach-refused", user_id=uid, device_id=d["id"], detail={"why": "belongs elsewhere"},
                 ip=who.ip)
        c.commit()
        raise HTTPException(403, "These keys belong to another device.")
    if d and d["status"] == "revoked":
        raise HTTPException(410, "This device was revoked. Forget it here and register again.")
    if not d:
        fp = fingerprint(body.sign_pub, body.wrap_pub)
        d = c.execute("insert into devices (user_id, name, os, app_version, sign_pub, wrap_pub, fingerprint, last_ip) "
                      "values (%s, %s, %s, %s, %s, %s, %s, %s) returning *",
                      (uid, body.name.strip(), body.os.strip(), body.app_version.strip(), body.sign_pub,
                       body.wrap_pub, fp, who.ip)).fetchone()
        db.audit(c, "device.registered", user_id=uid, device_id=d["id"], detail={"name": d["name"], "fingerprint": fp},
                 ip=who.ip)
        # The very first admin device can't be approved by another one: it approves itself,
        # once, while no admin device is approved anywhere. Its own attach signature is the record.
        nobody = not c.execute("select 1 from devices d join users u on u.id = d.user_id "
                               "where d.status = 'approved' and 'admin' = any(u.caps) limit 1").fetchone()
        if who.has("admin") and nobody:
            text = attach_text(who.challenge, body.sign_pub, body.wrap_pub)
            d = c.execute("update devices set status = 'approved', approval = %s, approval_sig = %s, "
                          "approved_by_device = id, approved_at = now() where id = %s returning *",
                          (text, body.signature, d["id"])).fetchone()
            db.audit(c, "device.approved-first-admin", user_id=uid, device_id=d["id"], ip=who.ip)
        else:
            from cp import push
            push.admins_told(c, "A device is waiting for approval",
                             f"{who.user['email']}: {d['name'] or 'a new device'}. Check its 24 digits before approving.")
    else:
        c.execute("update devices set last_seen = now(), last_ip = %s, app_version = %s where id = %s",
                  (who.ip, body.app_version.strip() or d["app_version"], d["id"]))
    c.execute("update sessions set device_id = %s, challenge = %s where id = %s",
              (d["id"], secrets.token_urlsafe(24), who.session_id))
    c.commit()
    return {"device": public(d)}
