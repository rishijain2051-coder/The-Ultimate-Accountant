"""The Keeper's data keys, on the office server (PLAN-office.md, section 4).

The data key (DEK) is 32 random bytes, held with DPAPI in keyring.json, and leaves this PC
only wrapped for one device:
    ECDH(fresh P-256 key, device's wrap_pub) -> HKDF-SHA256(salt "", info) -> AES-256-GCM(DEK, aad info)
    info = "keeper-dek:<kid>:<device id>"
A device gets it only if the control plane's roster shows it approved, its person active
with See the books (`books.view`, or the old `books`), and its approval signed by an admin device this office server
trusts: one pinned here when it was set up, or one approved, as an admin, by a trusted one.
The control plane can say anything it likes; it can't forge those signatures.

When a device that holds the current key may hold it no longer (revoked, its person
disabled, `books` taken away), a new key is made, and only the remaining devices get it.
Older keys stay, for older files, and the remaining devices get those too.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.office import caps, dpapi, settings
from app.office.identity import load_pub, spki, verify
from app.office.roster import Roster


def info(kid: str, device_id: str) -> bytes:
    return f"keeper-dek:{kid}:{device_id}".encode()


def _kek(private: ec.EllipticCurvePrivateKey, public: ec.EllipticCurvePublicKey, kid: str, device_id: str) -> bytes:
    shared = private.exchange(ec.ECDH(), public)
    return HKDF(hashes.SHA256(), 32, salt=b"", info=info(kid, device_id)).derive(shared)


def wrap(dek: bytes, kid: str, device_id: str, wrap_pub: str) -> dict:
    eph = ec.generate_private_key(ec.SECP256R1())
    nonce = os.urandom(12)
    ct = AESGCM(_kek(eph, load_pub(wrap_pub), kid, device_id)).encrypt(nonce, dek, info(kid, device_id))
    return {"device_id": device_id, "epk": spki(eph.public_key()),
            "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def unwrap(w: dict, kid: str, device_id: str, wrap_key: ec.EllipticCurvePrivateKey) -> bytes:
    kek = _kek(wrap_key, load_pub(w["epk"]), kid, device_id)
    return AESGCM(kek).decrypt(base64.b64decode(w["nonce"]), base64.b64decode(w["ct"]), info(kid, device_id))


# --- the key ring on this PC ------------------------------------------------

def ring() -> dict:
    r = settings.read_json("keyring.json", {}) or {}
    r.setdefault("current", None)
    r.setdefault("keys", {})
    r.setdefault("pinned_admins", [])
    return r


def _save(r: dict) -> None:
    settings.write_json("keyring.json", r)


def new_key(r: dict) -> str:
    n = max((int(k[1:]) for k in r["keys"]), default=0) + 1
    kid = f"k{n}"
    r["keys"][kid] = base64.b64encode(dpapi.protect(os.urandom(32))).decode()
    r["current"] = kid
    _save(r)
    return kid


def dek(kid: str, r: dict | None = None) -> bytes:
    return dpapi.unprotect(base64.b64decode((r or ring())["keys"][kid]))


def current() -> tuple[str, bytes]:
    """The key the Keeper encrypts with now: (kid, DEK). Made the first time it is asked for."""
    r = ring()
    kid = r["current"] or new_key(r)
    return kid, dek(kid, r)


def _pin(r: dict, sign_pub: str, wrap_pub: str) -> None:
    """Pin the pair: the person confirmed both keys through the 24 digits."""
    if not any(isinstance(p, dict) and p["sign_pub"] == sign_pub for p in r["pinned_admins"]):
        r["pinned_admins"].append({"sign_pub": sign_pub, "wrap_pub": wrap_pub})


def pin_admin(sign_pub: str, wrap_pub: str) -> None:
    r = ring()
    _pin(r, sign_pub, wrap_pub)
    _save(r)


def _upgrade_pins(r: Roster, kr: dict) -> None:
    """Older key rings hold a bare sign_pub. Trust-on-upgrade: bind it, once, to the wrap_pub the
    roster shows for that device now. One the roster doesn't show stays bare, and trusted by nothing."""
    for i, p in enumerate(kr["pinned_admins"]):
        d = next((d for d in r.devices.values() if d["sign_pub"] == p), None) if isinstance(p, str) else None
        if d:
            kr["pinned_admins"][i] = {"sign_pub": p, "wrap_pub": d["wrap_pub"]}
            _save(kr)


# --- whom to trust ----------------------------------------------------------

def approval_parts(text: str | None) -> dict | None:
    lines = (text or "").split("\n")
    if len(lines) != 7 or lines[0] != "ua-approve:v1":
        return None
    return {"device": lines[1], "email": lines[2], "caps": [c for c in lines[3].split(",") if c],
            "sign_pub": lines[4], "wrap_pub": lines[5], "at": lines[6]}


def _signed_for(r: Roster, d: dict, trusted: set[str], cap: str) -> bool:
    """d's approval is a trusted admin's signature over exactly d, its owner, and `cap`."""
    parts = approval_parts(d.get("approval"))
    approver = r.devices.get(d.get("approved_by_device") or "")
    owner = r.users.get(d["user_id"])
    return bool(parts and approver and owner and approver["sign_pub"] in trusted
                and parts["device"] == d["id"] and parts["sign_pub"] == d["sign_pub"]
                and parts["wrap_pub"] == d["wrap_pub"] and parts["email"] == owner["email"]
                and caps.has(parts["caps"], cap) and verify(approver["sign_pub"], d["approval"], d.get("approval_sig") or ""))


def trusted_admin_keys(r: Roster, pinned: list[dict]) -> set[str]:
    """Signing keys of the admin devices this office server trusts: pinned ones, and those a
    trusted one approved as an admin, while they stay approved and their person an admin."""
    # A pin counts only while the roster still shows that device approved under an active admin:
    # the control plane can only take trust away this way, never add it.
    pins = {p["sign_pub"] for p in pinned if isinstance(p, dict)}
    trusted = set()
    for d in r.devices.values():
        owner = r.users.get(d["user_id"])
        if (d["sign_pub"] in pins and d["kind"] == "viewer" and d["status"] == "approved" and owner
                and owner["status"] == "active" and "admin" in owner["caps"]):
            trusted.add(d["sign_pub"])
    grew = True
    while grew:
        grew = False
        for d in r.devices.values():
            owner = r.users.get(d["user_id"])
            if (d["sign_pub"] in trusted or d["status"] != "approved" or not owner
                    or owner["status"] != "active" or "admin" not in owner["caps"]):
                continue
            if _signed_for(r, d, trusted, "admin"):
                trusted.add(d["sign_pub"])
                grew = True
    return trusted


def eligible(r: Roster, trusted: set[str], pinned: list[dict] = ()) -> dict[str, dict]:
    """The devices that may hold the data key now, by id. A pinned device must still have the
    wrap_pub that was pinned with it."""
    pins = {p["sign_pub"]: p["wrap_pub"] for p in pinned if isinstance(p, dict)}
    out = {}
    for d in r.devices.values():
        owner = r.users.get(d["user_id"])
        if (d["kind"] != "viewer" or d["status"] != "approved" or not owner or owner["status"] != "active"
                or not caps.has(owner["caps"], "books.view")):
            continue
        if ((d["sign_pub"] in trusted and pins.get(d["sign_pub"], d["wrap_pub"]) == d["wrap_pub"])
                or _signed_for(r, d, trusted, "books.view")):
            out[d["id"]] = d
    return out


def standing(r: Roster) -> dict[str, str]:
    """Each phone or laptop's key status, in words, for the Keeper page: why it has the key or not."""
    kr = ring()
    if not kr["pinned_admins"]:
        return {d["id"]: "No key yet: no admin device is pinned on this office server." for d in r.devices.values()
                if d["kind"] == "viewer"}
    ok = eligible(r, trusted_admin_keys(r, kr["pinned_admins"]), kr["pinned_admins"])
    held = {(w["device"], w["kid"]) for w in r.claims.get("wrapped", [])}
    out = {}
    for d in r.devices.values():
        owner = r.users.get(d["user_id"]) or {}
        if d["kind"] != "viewer":
            continue
        if d["id"] in ok:
            out[d["id"]] = ("Has the key." if (d["id"], kr["current"]) in held
                            else "Gets the key within a minute, while the app is open here.")
        elif d["status"] != "approved":
            out[d["id"]] = "No key: not approved yet."
        elif owner.get("status") != "active":
            out[d["id"]] = "No key: this person is turned off."
        elif not caps.has(owner.get("caps"), "books.view"):
            out[d["id"]] = "No key: no See the books access."
        else:
            out[d["id"]] = ("No key: approved by a device this office server doesn't trust. "
                            "Approve it again from the first admin device.")
    return out


def sync(cloud, r: Roster) -> dict:
    """Give every eligible device every key it lacks; make a new key when someone who holds
    the current one may hold it no longer. Returns what it did."""
    kr = ring()
    _upgrade_pins(r, kr)
    if not kr["pinned_admins"]:
        return {"skipped": "no admin device is pinned on this office server yet"}
    ok = eligible(r, trusted_admin_keys(r, kr["pinned_admins"]), kr["pinned_admins"])
    held = {(w["device"], w["kid"]) for w in r.claims.get("wrapped", [])}
    rotated = False
    if kr["current"] is None:
        new_key(kr)
    elif {dev for dev, kid in held if kid == kr["current"]} - set(ok):
        new_key(kr)
        rotated = True
    sent = {}
    cloud_current = next((k["kid"] for k in r.claims.get("key_ring", []) if k.get("is_current")), None)
    for kid in sorted(kr["keys"], key=lambda k: int(k[1:])):
        missing = [d for d in ok.values() if (d["id"], kid) not in held]
        is_current = kid == kr["current"]
        if missing or (is_current and cloud_current != kid):
            key = dek(kid, kr)
            cloud.put("/api/office/wrapped-keys", {"kid": kid, "current": is_current,
                                                   "keys": [wrap(key, kid, d["id"], d["wrap_pub"]) for d in missing]})
            sent[kid] = [d["id"] for d in missing]
    return {"current": kr["current"], "rotated": rotated, "eligible": sorted(ok), "sent": sent}


# --- the recovery sheet -----------------------------------------------------

def recovery_sheet(office_name: str = "") -> Path:
    """A page to print and keep in a safe: every data key, in base32, and a one-time code that
    pins a new admin device if every one is lost. Written to the office folder and opened."""
    r = ring()
    if not r["keys"]:
        new_key(r)
        r = ring()
    code = "-".join(secrets.token_hex(2).upper() for _ in range(5))
    r["recovery"] = hashlib.sha256(code.encode()).hexdigest()
    _save(r)
    def groups(kid):
        b32 = base64.b32encode(dek(kid, r)).decode().rstrip("=")
        return " ".join(b32[i:i + 4] for i in range(0, len(b32), 4))
    rows = "".join(f"<tr><td>{kid}</td><td><code>{groups(kid)}</code></td></tr>"
                   for kid in sorted(r["keys"], key=lambda k: int(k[1:])))
    page = f"""<!doctype html><meta charset=utf-8><title>Ultimate Accountant recovery sheet</title>
<style>body{{font:14px 'Segoe UI',sans-serif;max-width:760px;margin:32px auto;color:#1c1e23}}code{{font:15px Consolas,monospace}}
td{{padding:6px 10px;border-bottom:1px solid #ccc;vertical-align:top}}h1{{font-size:22px}}.warn{{border-left:4px solid #b42318;padding:8px 12px;background:#fbebe9}}</style>
<h1>Ultimate Accountant: recovery sheet</h1>
<p>{html.escape(office_name)} · made {time.strftime('%d %b %Y %H:%M')}</p>
<p class=warn><b>Print this and keep it in a safe.</b> Anyone with it can read the Keeper's books. Delete this file after printing.</p>
<h2>Data keys</h2><p>They open every snapshot and backup the Keeper has made, if this office server is lost.</p>
<table>{rows}</table>
<h2>Admin recovery code</h2><p>If every admin device is lost: on the office server run
<code>Ultimate Accountant.exe --office pin-admin</code> and enter</p><p><code style="font-size:20px">{code}</code></p>
<p>It works once. Pinned admin devices now: {len(r['pinned_admins'])}.</p>"""
    out = settings.office_dir() / "recovery-sheet.html"
    out.write_text(page, encoding="utf-8")
    return out


def pin_with_code(code: str, sign_pub: str, wrap_pub: str) -> bool:
    r = ring()
    if not r.get("recovery") or hashlib.sha256(code.strip().upper().encode()).hexdigest() != r["recovery"]:
        return False
    r["recovery"] = None
    _pin(r, sign_pub, wrap_pub)
    _save(r)
    return True
