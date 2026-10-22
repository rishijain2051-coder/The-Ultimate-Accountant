"""This PC talking to the control plane: its access token, and the control plane's signed tokens.

A PC never holds a password or a refresh token. To get a 15-minute access token it signs
  ua-token:v1, device id, unix time
with its own key; the control plane refuses a time it has seen. Tokens and the roster are
Ed25519 JWTs, checked against the control plane's key, pinned the first time it is fetched.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.office import settings
from app.office.identity import Identity


class Waiting(RuntimeError):
    """The PC is linked, and an admin has yet to approve it."""


class Revoked(RuntimeError):
    """The PC was revoked, or its person disabled."""


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def check_token(token: str, typ: str, key_raw: bytes, *, allow_expired: bool = False) -> dict:
    """The claims of an EdDSA JWT of this type signed by key_raw; ValueError otherwise."""
    try:
        head, body, sig = token.split(".")
        h = json.loads(_unb64(head))
        if h.get("alg") != "EdDSA" or h.get("typ") != typ:
            raise ValueError("wrong kind of token")
        Ed25519PublicKey.from_public_bytes(key_raw).verify(_unb64(sig), f"{head}.{body}".encode())
        claims = json.loads(_unb64(body))
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError("not signed by the control plane") from exc
    if not allow_expired and claims.get("exp", 0) < time.time():
        raise ValueError("expired")
    return claims


class Cloud:
    def __init__(self, ident: Identity, base: str | None = None):
        self.base = (base or settings.load()["control_plane"]).rstrip("/")
        self.ident = ident
        self._token, self._until, self._last_ts = "", 0.0, 0
        self._lock = threading.Lock()
        self._http = httpx.Client(timeout=30)       # one kept-open connection: no new TLS per call

    def token_key(self) -> bytes:
        """The control plane's token key: pinned in settings.json once, then only ever that one."""
        s = settings.load()
        if not s.get("token_key"):
            r = httpx.get(f"{self.base}/api/keys/token-key", timeout=20)
            r.raise_for_status()
            s["token_key"] = r.json()["public_key"]
            settings.save(s)
        return _unb64(s["token_key"])

    def access(self) -> str:
        with self._lock:
            if self._token and time.time() < self._until - 60:
                return self._token
            ts = max(int(time.time()), self._last_ts + 1)
            self._last_ts = ts
            did = self.ident.device_id
            r = self._http.post(f"{self.base}/api/devices/token", timeout=20, headers={"X-UA-Request": "1"},
                           json={"device_id": did, "ts": ts, "signature": self.ident.sign(f"ua-token:v1\n{did}\n{ts}")})
            if r.status_code == 403 and "waiting" in r.text:
                raise Waiting("This PC is waiting to be approved.")
            if r.status_code in (403, 410):
                raise Revoked(r.json().get("detail", "This PC may no longer sign in."))
            r.raise_for_status()
            tok = r.json()["token"]
            check_token(tok, "ua-access", self.token_key())
            self._token, self._until = tok, time.time() + r.json()["expires_in"]
            return tok

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access()}", "X-UA-Request": "1"}

    def get(self, path: str) -> dict:
        r = self._http.get(self.base + path, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self._http.post(self.base + path, headers=self._headers(), json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: dict) -> dict:
        r = self._http.put(self.base + path, headers=self._headers(), json=body, timeout=30)
        r.raise_for_status()
        return r.json()
