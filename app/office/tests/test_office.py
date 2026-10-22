"""The office's self-check. Run it with:  python app/office/tests/test_office.py

Same style as the app's other tests: no pytest, a check(name, condition) helper. Everything
here runs on this PC in a throwaway office folder, with a stand-in control plane:
  - DPAPI and the PC's own keys
  - the control plane's signed tokens, as the office server checks them
  - the sign-in gate: who may open what, sessions, a token used once, revoking
  - the data keys: whom to trust, who may hold the key, a new key when someone may not
  - .ukeep: round trips, and every way a file can be tampered with
  - the browser's keeper-crypto.js, under Node, reading what Python wrapped and wrote
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="officetest-"))
os.environ["ASSURANCE_OFFICE_DIR"] = str(_tmp / "office")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app.keeper import ukeep  # noqa: E402
from app.office import dpapi, gate, keys, roster, settings  # noqa: E402
from app.office.cloud import check_token  # noqa: E402
from app.office.identity import Identity, fingerprint  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


# --- a stand-in control plane: its token key, its roster ---------------------

CP_KEY = Ed25519PrivateKey.generate()
CP_PUB = CP_KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731


def jwt(claims: dict, typ: str, ttl: int = 900, key=CP_KEY) -> str:
    head = b64u(json.dumps({"alg": "EdDSA", "typ": typ}).encode())
    body = b64u(json.dumps({**claims, "exp": int(time.time()) + ttl}).encode())
    return f"{head}.{body}.{b64u(key.sign(f'{head}.{body}'.encode()))}"


class Office:
    """People and devices as the control plane would hold them."""

    def __init__(self):
        self.users, self.devices, self.wrapped, self.key_ring = {}, {}, [], []
        self.ids = {}

    def person(self, email, caps):
        uid = len(self.users) + 1
        self.users[uid] = {"id": uid, "email": email, "name": email.split("@")[0], "caps": caps, "status": "active"}
        return uid

    def device(self, uid, kind="viewer", approver: "Identity | None" = None, approver_id=None, signed_caps=None,
               email=None, status="approved"):
        ident = Identity.new("office_client")
        did = f"dev-{len(self.devices) + 1}"
        d = {"id": did, "user_id": uid, "kind": kind, "name": did, "status": status, "sign_pub": ident.sign_pub,
             "wrap_pub": ident.wrap_pub, "approval": None, "approval_sig": None, "approved_by_device": approver_id}
        if approver:
            text = "\n".join(["ua-approve:v1", did, email or self.users[uid]["email"],
                              ",".join(sorted(signed_caps or self.users[uid]["caps"])), ident.sign_pub, ident.wrap_pub,
                              str(int(time.time()))])
            d["approval"], d["approval_sig"] = text, approver.sign(text)
        self.devices[did] = d
        self.ids[did] = ident
        return did, ident

    def roster(self) -> roster.Roster:
        return roster.Roster({"users": list(self.users.values()), "devices": list(self.devices.values()),
                              "wrapped": self.wrapped, "key_ring": self.key_ring}, time.time())


class FakeCloud:
    def __init__(self, office: Office):
        self.office, self.puts = office, []

    def put(self, path, body):
        assert path == "/api/office/wrapped-keys"
        self.puts.append(body)
        for k in body["keys"]:
            self.office.wrapped.append({"device": k["device_id"], "kid": body["kid"], **k})
        if body["current"]:
            self.office.key_ring = [{"kid": body["kid"], "is_current": True}]
        return {"ok": True}


# --- tests -------------------------------------------------------------------

def test_keys_on_this_pc():
    print("\n[this PC's keys]")
    secret = os.urandom(40)
    check("DPAPI gives back what it was given", dpapi.unprotect(dpapi.protect(secret)) == secret)
    check("... and the protected form isn't the secret", secret not in dpapi.protect(secret))
    i = Identity.new("office_client")
    i.device_id = "d-1"
    i.save()
    j = Identity.load("office_client")
    check("a PC's identity survives being saved and loaded",
          j.device_id == "d-1" and j.sign_pub == i.sign_pub and j.wrap_pub == i.wrap_pub)
    check("... its file holds no private key in the clear",
          b"PRIVATE" not in (settings.office_dir() / "client.json").read_bytes())
    check("a PC's signature checks out against its public key",
          __import__("app.office.identity", fromlist=["verify"]).verify(i.sign_pub, "x", j.sign("x")))


def test_tokens():
    print("\n[the control plane's tokens]")
    t = jwt({"sub": 1}, "ua-access")
    check("a token the control plane signed is read", check_token(t, "ua-access", CP_PUB)["sub"] == 1)
    for name, bad in (("a token of another type", jwt({"sub": 1}, "ua-roster")),
                      ("an expired token", jwt({"sub": 1}, "ua-access", ttl=-5)),
                      ("a token signed by another key", jwt({"sub": 1}, "ua-access", key=Ed25519PrivateKey.generate())),
                      ("a token with its claims changed", t.split(".")[0] + "." + b64u(b'{"sub":2,"exp":9999999999}')
                       + "." + t.split(".")[2])):
        try:
            check_token(bad, "ua-access", CP_PUB)
            check(f"{name} is refused", False)
        except ValueError:
            check(f"{name} is refused", True)


def gate_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    s = settings.load()
    s.update({"mode": "server", "token_key": b64u(CP_PUB)})
    settings.save(s)
    app = FastAPI()
    for p in ("/", "/bridge/x", "/console/x", "/api/runs", "/api/shutdown", "/api/update/install", "/keeper/x",
              "/api/health"):
        app.add_api_route(p, (lambda p=p: {"at": p}), methods=["GET"])
    gate.install(app)
    return TestClient(app, base_url="https://office")


def test_gate():
    print("\n[the office server's sign-in gate]")
    o = Office()
    acct, mgr, boss = o.person("acct@x.in", ["tib", "console"]), o.person("mgr@x.in", ["console"]), o.person("boss@x.in", ["books"])
    acct_pc, _ = o.device(acct, "office_client")
    mgr_pc, _ = o.device(mgr, "office_client")
    phone, _ = o.device(boss, "viewer")
    roster.set_current(o.roster())
    c = gate_app()

    def signed_in(uid, did, kind="office_client"):
        from fastapi.testclient import TestClient
        cl = TestClient(c.app, base_url="https://office")
        tok = jwt({"sub": uid, "email": o.users[uid]["email"], "device": did, "kind": kind}, "ua-access")
        return cl, cl.get(f"/office/sign-in?token={tok}&next=/", follow_redirects=False), tok

    check("health needs no one", c.get("/api/health").status_code == 200)
    check("nobody signed in is turned away", c.get("/console/x").status_code == 401)
    check("... with a page, when a window asks", "Sign in from Ultimate Accountant" in
          c.get("/console/x", headers={"accept": "text/html"}).text)
    a, r, tok = signed_in(acct, acct_pc)
    check("the accountant's PC signs in with its token", r.status_code == 302 and "ua_office" in a.cookies)
    check("... and opens TIB and the Console", a.get("/bridge/x").status_code == 200 and a.get("/console/x").status_code == 200)
    check("... but can't stop or update the office server", a.get("/api/shutdown").status_code == 403
          and a.get("/api/update/install").status_code == 403)
    check("... nor open the Keeper's screen", a.get("/keeper/x").status_code == 403)
    me = a.get("/office/me").json()
    check("... and /office/me says who and what", me["email"] == "acct@x.in" and me["caps"] == ["tib", "console"])
    again = c.get(f"/office/sign-in?token={tok}&next=/", follow_redirects=False)
    check("a sign-in token works once only", again.status_code == 401)
    m, _r, _t = signed_in(mgr, mgr_pc)
    check("the manager opens the Console, not TIB", m.get("/console/x").status_code == 200 and m.get("/bridge/x").status_code == 403)
    b, r, _t = signed_in(boss, phone, kind="viewer")
    check("a phone's token can't sign in to the office server", r.status_code == 401 and "ua_office" not in b.cookies)
    _x, r, _t = signed_in(acct, mgr_pc)
    check("a token for someone else's PC is refused", r.status_code == 403)

    o.devices[acct_pc]["status"] = "revoked"
    roster.set_current(o.roster())
    check("revoking the accountant's PC turns it away at the next roster", a.get("/console/x").status_code == 401)
    o.devices[acct_pc]["status"] = "approved"
    o.users[mgr]["status"] = "disabled"
    roster.set_current(o.roster())
    check("disabling the manager turns them away too", m.get("/console/x").status_code == 401)
    o.users[mgr].update(status="active", caps=["console", "tib"])
    roster.set_current(o.roster())
    check("access given in the admin console applies at the next roster", m.get("/bridge/x").status_code == 200)
    check("sessions are kept by the SHA-256 of their cookie only",
          a.cookies["ua_office"] not in (settings.office_dir() / "sessions.json").read_text())


def test_detailed_permissions():
    print("\n[detailed permissions: route to key]")
    from app.office import caps
    can = lambda held, path, method="GET": caps.allows(held, gate.need(path, method))  # noqa: E731
    up = ["tib.upload"]
    check("tib.upload uploads bills", can(up, "/bridge/api/documents/upload", "POST"))
    check("... and uses the phone code and the Bridge screens", can(up, "/bridge/api/phone") and can(up, "/bridge/review/") and can(up, "/bridge/api/health"))
    check("... but can't review, approve or post", not can(up, "/bridge/api/entries/3", "PUT")
          and not can(up, "/bridge/api/entries/3/approve", "POST") and not can(up, "/bridge/api/bank/rows/2/post", "POST"))
    check("review edits but doesn't approve", can(["tib.review"], "/bridge/api/entries/3/reject", "POST")
          and not can(["tib.review"], "/bridge/api/entries/approve-batch", "POST"))
    check("tib.bank uploads statements, tib.post posts them", can(["tib.bank"], "/bridge/api/bank/statements", "POST")
          and not can(["tib.bank"], "/bridge/api/bank/statements/1/post", "POST")
          and can(["tib.post"], "/bridge/api/bank/statements/1/post", "POST"))
    check("tib.sync reads Tally only", can(["tib.sync"], "/bridge/api/tally/companies") and not can(["tib.sync"], "/bridge/api/entries"))
    rep = ["con.reports"]
    check("con.reports downloads", can(rep, "/api/download/excel/5") and can(rep, "/api/files/1/zip"))
    check("... but can't reconcile, export, or change settings", not can(rep, "/api/reconcile", "POST")
          and not can(rep, "/api/exports/1/post", "POST") and not can(rep, "/api/settings", "POST"))
    check("... and still gets the shell and read-only basics", can(rep, "/console/") and can(rep, "/api/bootstrap") and can(rep, "/api/settings"))
    check("con.recon covers runs, bank, statements; con.ai covers /api/ai", can(["con.recon"], "/api/runs/4")
          and can(["con.recon"], "/api/statement/reconcile", "POST") and can(["con.ai"], "/api/ai/get", "POST")
          and not can(["con.recon"], "/api/ai/get", "POST"))
    check("the old 'console' opens everything in the console", all(can(["console"], x, m) for x, m in (
        ("/api/reconcile", "POST"), ("/api/exports/1", "GET"), ("/api/download/pdf/1", "GET"), ("/api/settings", "POST"),
        ("/api/ai/get", "POST"), ("/console/", "GET"), ("/api/never-heard-of-it", "GET"))))
    check("... but not the Bridge, and the old 'tib' opens all of it", not can(["console"], "/bridge/api/entries")
          and can(["tib"], "/bridge/api/entries/3/approve", "POST"))
    check("an unmapped Bridge route needs the strictest key", can(["tib.post"], "/bridge/api/new-thing", "POST")
          and not can(["tib.upload", "tib.review", "tib.bank", "tib.sync"], "/bridge/api/new-thing", "POST"))
    check("an unmapped console route needs the strictest key", can(["con.settings"], "/api/new-thing", "POST")
          and not can(["con.recon", "con.exports", "con.reports", "con.ai"], "/api/new-thing", "POST"))
    check("admin implies nothing else, and the server controls stay admin", not can(["admin"], "/api/reconcile", "POST")
          and not can(["con.settings"], "/api/shutdown", "POST") and can(["admin"], "/api/shutdown", "POST")
          and not caps.has(["admin"], "books.view"))
    check("health and sign-in stay public", gate.need("/api/health") is None and gate.need("/office/sign-in") is None)
    check("books.view (or old books) qualifies a device for the data key", caps.has(["books"], "books.view")
          and caps.has(["books.view"], "books.view") and not caps.has(["books.alerts"], "books.view"))


def test_data_keys():
    print("\n[the Keeper's data keys]")
    (settings.office_dir() / "keyring.json").unlink(missing_ok=True)
    o = Office()
    me = o.person("alex@x.in", ["admin", "books", "tib", "console"])
    boss, buyer = o.person("boss@x.in", ["books"]), o.person("buyer@x.in", ["books"])
    laptop_id, laptop = o.device(me)                                      # the first admin device
    phone_id, phone = o.device(me, approver=laptop, approver_id=laptop_id)  # admin's phone, approved by it
    boss_id, boss_dev = o.device(boss, approver=phone, approver_id=phone_id)  # approved from the phone
    buyer_id, _ = o.device(buyer, approver=laptop, approver_id=laptop_id)
    forged_id, _ = o.device(buyer, approver=laptop, approver_id=laptop_id, signed_caps=["books"], email="someone@else")
    rogue_admin = Identity.new("office_client")
    rogue_id, _ = o.device(buyer, approver=rogue_admin, approver_id=None)
    o.devices[rogue_id]["approved_by_device"] = laptop_id                 # claims the laptop, signed by another key
    keys.pin_admin(o.devices[laptop_id]["sign_pub"], o.devices[laptop_id]["wrap_pub"])

    r = o.roster()
    trusted = keys.trusted_admin_keys(r, keys.ring()["pinned_admins"])
    check("the pinned admin device is trusted", o.devices[laptop_id]["sign_pub"] in trusted)
    check("... and an admin device it approved, as an admin", o.devices[phone_id]["sign_pub"] in trusted)
    pins = keys.ring()["pinned_admins"]
    ok = keys.eligible(r, trusted, pins)
    check("a device approved by a trusted admin device may hold the key", boss_id in ok and buyer_id in ok)
    check("... and so may the admin's own devices", laptop_id in ok and phone_id in ok)
    check("an approval for a different email than the owner's is refused", forged_id not in ok)
    check("an approval signed by a key nobody trusts is refused", rogue_id not in ok)
    o.devices[boss_id]["approval"] = o.devices[boss_id]["approval"].replace("books", "books,tib")
    check("an approval changed after it was signed is refused", boss_id not in keys.eligible(o.roster(), trusted, pins))
    o.devices[boss_id]["approval"] = o.devices[boss_id]["approval"].replace("books,tib", "books")

    cloud = FakeCloud(o)
    res = keys.sync(cloud, o.roster())
    check("the first sync makes key k1 and gives it to every eligible device",
          res["current"] == "k1" and set(res["sent"]["k1"]) == {laptop_id, phone_id, boss_id, buyer_id}, str(res))
    w = next(x for x in o.wrapped if x["device"] == boss_id and x["kid"] == "k1")
    got = keys.unwrap(w, "k1", boss_id, boss_dev.wrap_key)
    check("... and the boss's phone can unwrap it", got == keys.dek("k1"))
    try:
        keys.unwrap(w, "k1", buyer_id, boss_dev.wrap_key)
        check("... but not as if it were wrapped for another device", False)
    except Exception:  # noqa: BLE001
        check("... but not as if it were wrapped for another device", True)
    check("a second sync sends nothing new", keys.sync(cloud, o.roster())["sent"] == {})

    # a control-plane writer swaps the pinned admin device's wrap_pub and drops its wrapped rows
    real_wrap = o.devices[laptop_id]["wrap_pub"]
    o.devices[laptop_id]["wrap_pub"] = Identity.new("office_client").wrap_pub
    o.wrapped[:] = [x for x in o.wrapped if x["device"] != laptop_id]
    res = keys.sync(cloud, o.roster())
    check("a pinned admin device whose wrap_pub changed gets nothing",
          laptop_id not in res["eligible"] and not any(laptop_id in v for v in res["sent"].values())
          and not any(x["device"] == laptop_id for x in o.wrapped), str(res))
    o.devices[laptop_id]["wrap_pub"] = real_wrap
    keys.sync(cloud, o.roster())
    check("... and gets keys again once it shows the pinned one", any(x["device"] == laptop_id for x in o.wrapped))
    old = keys.ring()
    old["pinned_admins"] = [o.devices[laptop_id]["sign_pub"]]            # a key ring from before the pair was pinned
    keys._save(old)
    keys.sync(cloud, o.roster())
    check("a bare pinned key is bound to its device's wrap_pub once, on upgrade",
          keys.ring()["pinned_admins"] == [{"sign_pub": o.devices[laptop_id]["sign_pub"], "wrap_pub": real_wrap}])

    o.devices[buyer_id]["status"] = "revoked"
    res = keys.sync(cloud, o.roster())
    check("revoking a device that held the key makes a new key, k2", res["rotated"] and res["current"] == "k2", str(res))
    check("... given to the others and not to the revoked device",
          set(res["sent"]["k2"]) == {laptop_id, phone_id, boss_id} and buyer_id not in res["eligible"], str(res))
    o.users[boss]["caps"] = ["console"]
    res = keys.sync(cloud, o.roster())
    check("taking `books` away from the boss makes another new key, k3", res["current"] == "k3" and boss_id not in res["eligible"])
    o.devices[boss_id]["status"] = "revoked"

    new_id, new_dev = o.device(me, approver=laptop, approver_id=laptop_id)
    res = keys.sync(cloud, o.roster())
    check("a device approved later gets every key, old files' too", set(res["sent"]) == {"k1", "k2", "k3"}
          and all(new_id in v for v in res["sent"].values()), str(res))

    sheet = keys.recovery_sheet("Test office").read_text(encoding="utf-8")
    check("the recovery sheet lists every data key", all(k in sheet for k in ("k1", "k2", "k3")))
    code = sheet.split('font-size:20px">')[1].split("<")[0]
    other = Identity.new("office_client")
    check("its code pins a new admin device", keys.pin_with_code(code, other.sign_pub, other.wrap_pub))
    check("... once only", not keys.pin_with_code(code, other.sign_pub, other.wrap_pub))
    check("a wrong code pins nothing", not keys.pin_with_code("0000-0000", other.sign_pub, other.wrap_pub))
    got_new = [x for x in o.wrapped if x["device"] == new_id]

    # the control plane revokes the pinned admin device: what it approved is no longer eligible
    v_id, _ = o.device(buyer, approver=laptop, approver_id=laptop_id)
    o.devices[laptop_id]["status"] = "revoked"
    res = keys.sync(cloud, o.roster())
    check("a revoked pinned admin device is no longer trusted, so a viewer it approved gets nothing",
          o.devices[laptop_id]["sign_pub"] not in keys.trusted_admin_keys(o.roster(), keys.ring()["pinned_admins"])
          and v_id not in res.get("eligible", {}) and not any(v_id in v for v in res.get("sent", {}).values())
          and not any(x["device"] == v_id for x in o.wrapped), str(res))
    return {"dev": new_dev, "dev_id": new_id, "wrapped": got_new}


def test_ukeep():
    print("\n[.ukeep files]")
    dek, other = os.urandom(32), os.urandom(32)
    for n in (0, 1, ukeep.CHUNK, 2 * ukeep.CHUNK + 5):
        data = os.urandom(n)
        f = ukeep.encrypt(data, dek, "k1", "snapshot", company="c-1", taken_at="2026-10-21T10:00:00+05:30")
        h, back = ukeep.decrypt(f, {"k1": dek}.__getitem__)
        check(f"{n} bytes come back exactly", back == data and h["company"] == "c-1")
    data = os.urandom(2 * ukeep.CHUNK + 5)
    f = bytearray(ukeep.encrypt(data, dek, "k1", "snapshot", company="c-1"))
    hdr_end = 11 + int.from_bytes(f[7:11], "big")
    size = ukeep.CHUNK + 16

    def refused(blob, key=dek, kid="k1"):
        try:
            ukeep.decrypt(bytes(blob), {kid: key}.__getitem__)
            return False
        except ukeep.UkeepError:
            return True

    changed_hdr = bytearray(f)
    changed_hdr[hdr_end - 3] ^= 1
    check("a changed header is refused", refused(changed_hdr))
    changed = bytearray(f)
    changed[hdr_end + 100] ^= 1
    check("a changed byte in a chunk is refused", refused(changed))
    check("a file cut short at a chunk's edge is refused", refused(f[:hdr_end + 2 * size]))
    check("a file cut short inside a chunk is refused", refused(f[:-7]))
    swapped = f[:hdr_end] + f[hdr_end + size:hdr_end + 2 * size] + f[hdr_end:hdr_end + size] + f[hdr_end + 2 * size:]
    check("chunks swapped round are refused", refused(swapped))
    check("the wrong key is refused", refused(f, key=other))
    check("a key this device lacks is said so", refused(f, kid="k9"))
    check("something that isn't a .ukeep file is refused", refused(b"PK\x03\x04 not ukeep"))
    big = io.BytesIO()
    ukeep.write(big, io.BytesIO(data), dek, "k1", "tally-backup", name="TDBK1800_10000.001")
    h, parts = ukeep.chunks(io.BytesIO(big.getvalue()), {"k1": dek}.__getitem__)
    check("a big file is read chunk by chunk", b"".join(parts) == data and h["name"] == "TDBK1800_10000.001")


def test_browser(d: dict):
    print("\n[the browser's keeper-crypto.js agrees]")
    node = shutil.which("node")
    check("node is here to run it", bool(node))
    pkcs8 = base64.b64encode(d["dev"].wrap_key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()
    w = next(x for x in d["wrapped"] if x["kid"] == "k2")
    plain = os.urandom(ukeep.CHUNK + 1234)
    file = ukeep.encrypt(plain, keys.dek("k2"), "k2", "snapshot", company="c-1")
    js = (ROOT / "control-plane" / "public" / "keeper-crypto.js").as_uri()
    script = f"""
      const m = await import({json.dumps(js)});
      const b = (s) => Uint8Array.from(Buffer.from(s, "base64"));
      const priv = await crypto.subtle.importKey("pkcs8", b({json.dumps(pkcs8)}), {{name: "ECDH", namedCurve: "P-256"}}, false, ["deriveBits"]);
      const key = await m.unwrapKey({json.dumps(w)}, "k2", {json.dumps(d["dev_id"])}, priv);
      const file = b(process.argv[1]);
      const out = await m.openUkeep(file, async (kid) => (kid === "k2" ? key : null));
      const hash = Buffer.from(await crypto.subtle.digest("SHA-256", out.plain)).toString("hex");
      let tampered = "accepted";
      file[file.length - 3] ^= 1;
      try {{ await m.openUkeep(file, async () => key); }} catch {{ tampered = "refused"; }}
      let extractable = key.extractable;
      console.log(JSON.stringify({{hash, company: out.header.company, tampered, extractable}}));
    """
    f = _tmp / "file.b64"
    f.write_text(base64.b64encode(file).decode())
    run = subprocess.run([node, "--input-type=module", "-e", script.replace("process.argv[1]",
                          f"require('fs').readFileSync({json.dumps(str(f))}, 'utf8')").replace(
                          "const m =", "const {createRequire} = await import('module'); const require = createRequire(import.meta.url); const m =")],
                         capture_output=True, text=True)
    check("node ran it", run.returncode == 0, run.stderr[-600:])
    out = json.loads(run.stdout)
    import hashlib
    check("the browser unwraps the key the office server wrapped, and opens the file",
          out["hash"] == hashlib.sha256(plain).hexdigest() and out["company"] == "c-1")
    check("... refuses the file once a byte is changed", out["tampered"] == "refused")
    check("... and the unwrapped key can't be read out", out["extractable"] is False)


def test_setup_dialogs():
    print("\n[the packaged app's set-up asks in dialogs]")
    from app.office import setup
    shown = []
    real_box, setup._box = setup._box, lambda text, flags: (shown.append((text, flags)), 6)[1]
    sys.frozen = True
    try:
        def crashing(*_a):
            raise RuntimeError("lost sys.stdin")

        @setup.in_dialogs
        def asks(host, tell, ask, app_version=""):
            tell(f"hello {host} {app_version}")
            return ask("go on?")

        check("given the launcher's console functions, it asks in dialogs instead",
              asks("pc", crashing, crashing, "1.1.1") is True and shown[0][0] == "hello pc 1.1.1" and shown[1][1] == 0x24)

        @setup.in_dialogs
        def breaks(tell, ask):
            raise ValueError("no network")

        check("anything that goes wrong is said in a dialog, not left to the launcher",
              breaks(crashing, crashing) is False and "no network" in shown[-1][0] and shown[-1][1] == 0x10)
        try:
            setup.status()
            check("--office status shows its dialog and ends the program", False)
        except SystemExit as e:
            check("--office status shows its dialog and ends the program", e.code == 0 and "mode" in shown[-1][0])
    finally:
        del sys.frozen
        setup._box = real_box


def test_local_only():
    print("\n[outside server mode, only this PC's own pages may talk to the app]")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.middleware("http")(gate.local_only)
    app.add_api_route("/x", lambda: {"ok": 1}, methods=["GET", "POST"])
    c, me = TestClient(app), {"host": "127.0.0.1:8000"}
    check("a plain GET passes", c.get("/x", headers=me).status_code == 200)
    check("a rebound Host is refused (400)", c.get("/x", headers={"host": "evil.com:8000"}).status_code == 400)
    check("a Host with no port is refused", c.get("/x", headers={"host": "evil.com"}).status_code == 400)
    check("localhost passes", c.get("/x", headers={"host": "localhost:8000"}).status_code == 200)
    check("a POST with no Origin (curl, our tools) passes", c.post("/x", headers=me).status_code == 200)
    check("a same-origin POST passes", c.post("/x", headers={**me, "origin": "http://127.0.0.1:8000"}).status_code == 200)
    check("... and localhost on the same port", c.post("/x", headers={**me, "origin": "http://localhost:8000"}).status_code == 200)
    check("a cross-site Origin POST is refused (403)", c.post("/x", headers={**me, "origin": "http://evil.com"}).status_code == 403)
    check("another port's Origin is refused", c.post("/x", headers={**me, "origin": "http://127.0.0.1:9"}).status_code == 403)
    check("Origin null is refused", c.post("/x", headers={**me, "origin": "null"}).status_code == 403)
    check("Sec-Fetch-Site cross-site is refused", c.post("/x", headers={**me, "sec-fetch-site": "cross-site"}).status_code == 403)
    check("Sec-Fetch-Site same-origin passes", c.post("/x", headers={**me, "sec-fetch-site": "same-origin"}).status_code == 200)


def test_upload_limit():
    print("\n[no upload over 200 MB]")
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.add_middleware(gate.upload_limit)
    async def x(request):
        await request.body()
        return JSONResponse({"ok": 1})
    app.add_route("/api/x", x, methods=["POST"])
    app.add_api_route("/other", lambda: {"ok": 1}, methods=["POST"])
    c, big = TestClient(app), {"content-length": str(gate.MAX_UPLOAD + 1)}
    check("a small upload passes", c.post("/api/x", content=b"hi").status_code == 200)
    check("a Content-Length over 200 MB is refused (413)", c.post("/api/x", headers=big).status_code == 413)
    check("... with a plain message", "too big" in c.post("/api/x", headers=big).json()["detail"])
    check("other paths are not limited", c.post("/other", headers={"content-length": "0"}).status_code == 200)
    gate.MAX_UPLOAD, was = 10, gate.MAX_UPLOAD
    try:
        check("a chunked body over the limit is refused (413)", c.post("/api/x", content=iter([b"x" * 8, b"x" * 8])).status_code == 413)
    finally:
        gate.MAX_UPLOAD = was


def main() -> int:
    try:
        test_setup_dialogs()
        test_keys_on_this_pc()
        test_tokens()
        test_gate()
        test_detailed_permissions()
        test_local_only()
        test_upload_limit()
        d = test_data_keys()
        test_ukeep()
        test_browser(d)
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)
    print(f"\n{len(PASSED)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
