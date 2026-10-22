"""Signed tokens for the office PCs: Ed25519 JWTs (alg EdDSA), checked by the office server
with the public key from GET /api/keys/token-key, which it pins when it is set up.

A PC's access token (15 minutes) says who it is for and what they may use; the roster
(10 minutes) is everyone and every device, for the office server's sign-in gate and key loop.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import APIRouter, Header, HTTPException

from cp import db

router = APIRouter()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _private() -> Ed25519PrivateKey:
    key = serialization.load_der_private_key(_unb64(db.env("JWT_SIGNING_KEY")), None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("JWT_SIGNING_KEY is not an Ed25519 key")
    return key


def public_raw() -> bytes:
    return _private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def kid() -> str:
    return hashlib.sha256(public_raw()).hexdigest()[:16]


def issue(claims: dict, ttl: int, typ: str) -> str:
    now = int(time.time())
    head = _b64(json.dumps({"alg": "EdDSA", "typ": typ, "kid": kid()}, separators=(",", ":")).encode())
    body = _b64(json.dumps({**claims, "iss": db.env("PUBLIC_ORIGIN").rstrip("/"), "iat": now, "exp": now + ttl},
                           separators=(",", ":"), default=str).encode())
    return f"{head}.{body}.{_b64(_private().sign(f'{head}.{body}'.encode()))}"


def check(token: str, typ: str) -> dict:
    """The claims of a token this control plane signed, of this type, still in date."""
    try:
        head, body, sig = token.split(".")
        h = json.loads(_unb64(head))
        if h.get("alg") != "EdDSA" or h.get("typ") != typ:
            raise ValueError("wrong kind of token")
        Ed25519PublicKey.from_public_bytes(public_raw()).verify(_unb64(sig), f"{head}.{body}".encode())
        claims = json.loads(_unb64(body))
    except Exception as exc:  # noqa: BLE001 - every failure is the same answer
        raise HTTPException(401, "That token is not valid.") from exc
    if claims.get("exp", 0) < time.time():
        raise HTTPException(401, "That token has expired.")
    return claims


def bearer(authorization: str = Header("")) -> dict:
    """FastAPI dependency: a PC's access token from the Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in first.")
    return check(authorization[7:], "ua-access")


@router.get("/api/keys/token-key")
def token_key():
    return {"kid": kid(), "alg": "EdDSA", "public_key": _b64(public_raw())}
