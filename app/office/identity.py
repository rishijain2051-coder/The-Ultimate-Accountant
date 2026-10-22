"""This PC's own keys: P-256 to sign (ECDSA) and to receive the data key (ECDH), as a
browser's, so the control plane treats a PC and a phone the same way.

Signatures are raw r||s, base64, as WebCrypto makes them. The private keys are kept with
DPAPI in server.json or client.json (app/office/settings.py).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from app.office import dpapi, settings

FILES = {"office_server": "server.json", "office_client": "client.json"}


def spki(pub: ec.EllipticCurvePublicKey) -> str:
    return base64.b64encode(pub.public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)).decode()


def load_pub(b64: str) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(base64.b64decode(b64))
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("not a P-256 key")
    return key


def verify(pub_b64: str, text: str, sig_b64: str) -> bool:
    """A WebCrypto-style ECDSA P-256 signature over text, by that key."""
    try:
        raw = base64.b64decode(sig_b64, validate=True)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        load_pub(pub_b64).verify(der, text.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:  # noqa: BLE001 - any failure is simply "not signed by that key"
        return False


def fingerprint(sign_pub: str, wrap_pub: str) -> str:
    """The 24 digits, as the control plane and the browser work them out."""
    h = hashlib.sha256(base64.b64decode(sign_pub) + base64.b64decode(wrap_pub)).digest()
    # The first 12 digits are the old 12-digit code; the next 12 come from the next 8 bytes of the hash.
    d = f"{int.from_bytes(h[:8], 'big') % 10**12:012d}{int.from_bytes(h[8:16], 'big') % 10**12:012d}"
    return " ".join(d[i:i + 6] for i in range(0, 24, 6))


@dataclass
class Identity:
    kind: str
    sign_key: ec.EllipticCurvePrivateKey
    wrap_key: ec.EllipticCurvePrivateKey
    device_id: str | None = None

    @property
    def sign_pub(self) -> str:
        return spki(self.sign_key.public_key())

    @property
    def wrap_pub(self) -> str:
        return spki(self.wrap_key.public_key())

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.sign_pub, self.wrap_pub)

    def sign(self, text: str) -> str:
        r, s = decode_dss_signature(self.sign_key.sign(text.encode(), ec.ECDSA(hashes.SHA256())))
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()

    @classmethod
    def new(cls, kind: str) -> "Identity":
        return cls(kind, ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1()))

    def save(self) -> None:
        def held(k):
            der = k.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
            return base64.b64encode(dpapi.protect(der)).decode()
        settings.write_json(FILES[self.kind], {"kind": self.kind, "device_id": self.device_id,
                                               "sign_key": held(self.sign_key), "wrap_key": held(self.wrap_key),
                                               "sign_pub": self.sign_pub, "wrap_pub": self.wrap_pub})

    @classmethod
    def load(cls, kind: str) -> "Identity | None":
        d = settings.read_json(FILES[kind])
        if not d:
            return None
        key = lambda b: serialization.load_der_private_key(dpapi.unprotect(base64.b64decode(b)), None)  # noqa: E731
        return cls(kind, key(d["sign_key"]), key(d["wrap_key"]), d.get("device_id"))

    @staticmethod
    def forget(kind: str) -> None:
        (settings.office_dir() / FILES[kind]).unlink(missing_ok=True)
