"""The control plane's self-check. Run it with:  .venv\\Scripts\\python.exe tests\\test_api.py

Same style as the app's tests: no pytest, a check(name, condition) helper and a PASSED list.
Runs the real API against the real Supabase, in a schema of its own (office_test_<random>)
that is dropped at the end, so the live "office" schema is never touched. Google is
stood in for; devices are P-256 keys made here, signing as WebCrypto does. The browser's
own app.js is also run under Node, to check its signatures and fingerprints match ours.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path.home() / ".ultimate-accountant" / "office.env")   # DATABASE_URL only is used from it
os.environ["CP_SCHEMA"] = f"office_test_{secrets.token_hex(4)}"
os.environ["PUBLIC_ORIGIN"] = "https://testserver"
os.environ["GOOGLE_CLIENT_ID"] = "123-test.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "GOCSPX-test"
os.environ["ASSURANCE_OFFICE_DIR"] = str(Path(__import__("tempfile").mkdtemp(prefix="cp-office-test-")))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed  # noqa: E402
from cryptography.hazmat.primitives import serialization as _ser  # noqa: E402
os.environ["JWT_SIGNING_KEY"] = base64.urlsafe_b64encode(_Ed.generate().private_bytes(
    _ser.Encoding.DER, _ser.PrivateFormat.PKCS8, _ser.NoEncryption())).rstrip(b"=").decode()

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cp import auth, db, devices as devmod  # noqa: E402
from cp.migrate import migrate  # noqa: E402
from main import app  # noqa: E402

PASSED: list[str] = []
H = {"x-ua-request": "1"}


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


class Device:
    """A device's two keys, signing as WebCrypto does (raw r||s)."""

    def __init__(self):
        self.sign_key = ec.generate_private_key(ec.SECP256R1())
        self.sign_pub = self._spki(self.sign_key)
        self.wrap_key = ec.generate_private_key(ec.SECP256R1())
        self.wrap_pub = self._spki(self.wrap_key)

    @staticmethod
    def _spki(k) -> str:
        return base64.b64encode(k.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def sign(self, text: str) -> str:
        r, s = decode_dss_signature(self.sign_key.sign(text.encode(), ec.ECDSA(hashes.SHA256())))
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


CLAIMS: dict = {}
auth.exchange_code = lambda code, verifier: CLAIMS   # Google, stood in for


def sign_in(email: str, sub: str | None = None, nonce: str | None = None, state: str | None = None) -> tuple[TestClient, str]:
    c = TestClient(app, base_url="https://testserver")
    c.get("/api/auth/google/start?return_to=/admin", follow_redirects=False)
    st, n, _verifier, _back = json.loads(auth.unb64url(c.cookies.get("ua_oauth")))
    CLAIMS.clear()
    CLAIMS.update({"iss": "https://accounts.google.com", "aud": os.environ["GOOGLE_CLIENT_ID"], "exp": time.time() + 600,
                   "nonce": nonce or n, "email": email, "email_verified": True, "sub": sub or f"sub-{email}",
                   "name": email.split("@")[0].title()})
    r = c.get(f"/api/auth/google/callback?code=x&state={state or st}", follow_redirects=False)
    return c, r.headers.get("location", "")


def attach(c: TestClient, d: Device, signature: str | None = None):
    s = c.get("/api/session").json()
    text = devmod.attach_text(s["challenge"], d.sign_pub, d.wrap_pub)
    return c.post("/api/devices/attach", headers=H, json={
        "sign_pub": d.sign_pub, "wrap_pub": d.wrap_pub, "signature": signature or d.sign(text), "name": "Test device"})


def approval(admin_dev: Device, target: dict, email: str, caps: list[str], at: int | None = None) -> dict:
    at = at or int(time.time())
    return {"at": at, "signature": admin_dev.sign(
        devmod.approve_text(target["id"], email, caps, target["sign_pub"], target["wrap_pub"], at))}


def test_signin():
    print("\n[sign-in]")
    c = TestClient(app, base_url="https://testserver")
    check("the API answers", c.get("/api/health").json() == {"ok": True})
    check("nobody signed in gets 401", c.get("/api/session").status_code == 401)
    _c, where = sign_in("stranger@example.com")
    check("an email not on the list is refused", where == "/?signin=refused", where)
    _c, where = sign_in("admin@test.example", nonce="not-the-nonce")
    check("a token with the wrong nonce is refused", where == "/?signin=failed", where)
    _c, where = sign_in("admin@test.example", state="forged")
    check("a callback whose state isn't ours is refused", where == "/?signin=expired", where)
    c, where = sign_in("admin@test.example")
    check("the super admin signs in and goes back where they started", where == "/admin", where)
    s = c.get("/api/session").json()
    check("... signed in, with no device yet", s["user"]["email"] == "admin@test.example" and s["device"] is None)
    _c, where = sign_in("admin@test.example", sub="another-google-account")
    check("the same email from a different Google account is refused", where == "/?signin=refused", where)

    real, auth.exchange_code = auth.exchange_code, lambda code, verifier: 1 / 0
    try:
        broken = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        broken.get("/api/auth/google/start", follow_redirects=False)
        st = json.loads(auth.unb64url(broken.cookies.get("ua_oauth")))[0]
        r = broken.get(f"/api/auth/google/callback?code=x&state={st}", follow_redirects=False)
        check("an unexpected error in sign-in goes back to the sign-in page, not a bare error",
              r.status_code == 302 and r.headers["location"] == "/?signin=failed", f"{r.status_code} {r.text[:80]}")
    finally:
        auth.exchange_code = real
    return c


def test_devices(admin_c: TestClient):
    print("\n[devices]")
    laptop, phone = Device(), Device()
    r = attach(admin_c, laptop, signature=laptop.sign("not the challenge"))
    check("a device that can't sign the challenge is refused", r.status_code == 400, r.text)
    r = attach(admin_c, laptop)
    check("the first admin device approves itself, once", r.status_code == 200 and r.json()["device"]["status"] == "approved", r.text)
    check("... its fingerprint is the 24 digits", r.json()["device"]["fingerprint"] == devmod.fingerprint(laptop.sign_pub, laptop.wrap_pub))
    phone_c, _ = sign_in("admin@test.example")
    r = attach(phone_c, phone)
    check("the admin's second device waits for approval", r.json()["device"]["status"] == "pending", r.text)
    check("... and can't reach the admin API meanwhile", phone_c.get("/api/admin/users").status_code == 403)

    r = admin_c.post("/api/admin/users", json={"email": "boss@test.example", "caps": ["books"]})
    check("a change without this site's header is refused", r.status_code == 403, r.text)
    r = admin_c.post("/api/admin/users", headers=H, json={"email": "Boss@Test.example", "name": "Boss", "caps": ["books"]})
    check("the admin adds the boss", r.status_code == 200, r.text)
    r = admin_c.post("/api/admin/users", headers=H, json={"email": "x@test.example", "caps": ["everything"]})
    check("access that doesn't exist is refused", r.status_code == 400, r.text)

    boss_c, where = sign_in("boss@test.example")
    check("the boss can sign in once added (email matched without case)", where == "/admin", where)
    boss_dev = Device()
    boss = attach(boss_c, boss_dev).json()["device"]
    check("the boss's phone waits for approval", boss["status"] == "pending")
    check("the boss can't reach the admin API", boss_c.get("/api/admin/devices").status_code == 403)

    stolen = Device()
    stolen.sign_pub, stolen.wrap_pub = boss_dev.sign_pub, boss_dev.wrap_pub
    other_c, _ = sign_in("admin@test.example")
    r = attach(other_c, stolen)
    check("someone else's public keys, without their private key, are refused", r.status_code == 400, r.text)

    r = admin_c.post(f"/api/admin/devices/{boss['id']}/approve", headers=H,
                     json=approval(laptop, boss, "boss@test.example", ["admin"]))
    check("an approval signed for other access than the boss's is refused", r.status_code == 400, r.text)
    r = admin_c.post(f"/api/admin/devices/{boss['id']}/approve", headers=H,
                     json=approval(boss_dev, boss, "boss@test.example", ["books"]))
    check("an approval not signed by the approving device is refused", r.status_code == 400, r.text)
    r = admin_c.post(f"/api/admin/devices/{boss['id']}/approve", headers=H,
                     json=approval(laptop, boss, "boss@test.example", ["books"], at=int(time.time()) - 3600))
    check("an hour-old approval is refused", r.status_code == 400, r.text)
    r = admin_c.post(f"/api/admin/devices/{boss['id']}/approve", headers=H,
                     json=approval(laptop, boss, "boss@test.example", ["books"]))
    check("the admin's device approves the boss's phone", r.status_code == 200, r.text)
    check("... and the boss's session sees it approved", boss_c.get("/api/session").json()["device"]["status"] == "approved")
    r = admin_c.post(f"/api/admin/devices/{boss['id']}/approve", headers=H,
                     json=approval(laptop, boss, "boss@test.example", ["books"]))
    check("approving twice is refused", r.status_code == 409, r.text)

    me = admin_c.get("/api/session").json()["device"]["id"]
    r = admin_c.post(f"/api/admin/devices/{me}/revoke", headers=H)
    check("the admin can't revoke the device they are using", r.status_code == 400, r.text)
    r = admin_c.post(f"/api/admin/devices/{boss['id']}/revoke", headers=H)
    check("the admin revokes the boss's phone", r.status_code == 200, r.text)
    check("... which is signed out at once", boss_c.get("/api/session").status_code == 401)
    again_c, _ = sign_in("boss@test.example")
    r = attach(again_c, boss_dev)
    check("... and can't come back as that device", r.status_code == 410, r.text)
    r = attach(again_c, Device())
    check("... only as a new device, waiting for approval", r.json()["device"]["status"] == "pending", r.text)
    return laptop


def test_people(admin_c: TestClient):
    print("\n[people]")
    users = {u["email"]: u for u in admin_c.get("/api/admin/users").json()["users"]}
    me, boss = users["admin@test.example"], users["boss@test.example"]
    r = admin_c.patch(f"/api/admin/users/{me['id']}", headers=H, json={"status": "disabled"})
    check("the admin can't disable themselves", r.status_code == 400, r.text)
    r = admin_c.patch(f"/api/admin/users/{me['id']}", headers=H, json={"caps": ["books"]})
    check("... nor take away their own admin access", r.status_code == 400, r.text)
    boss_c, _ = sign_in("boss@test.example")
    r = admin_c.patch(f"/api/admin/users/{boss['id']}", headers=H, json={"status": "disabled"})
    check("the admin disables the boss", r.status_code == 200, r.text)
    check("... who is signed out everywhere", boss_c.get("/api/session").status_code == 401)
    _c, where = sign_in("boss@test.example")
    check("... and can't sign in", where == "/?signin=refused", where)
    actions = [a["action"] for a in admin_c.get("/api/admin/audit").json()["audit"]]
    for a in ("signin.refused", "signin.failed", "device.approved-first-admin", "device.approved",
              "device.approve-refused", "device.revoked", "user.added", "user.changed"):
        check(f"the audit log has {a}", a in actions)


def test_locked_down():
    print("\n[Supabase's own roles reach nothing]")
    c = db.connect()
    try:
        s = db.SCHEMA
        for role in ("anon", "authenticated"):
            check(f"{role} has no use of the schema",
                  c.execute("select has_schema_privilege(%s, %s, 'USAGE') as ok", (role, s)).fetchone()["ok"] is False)
        rls = c.execute("select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                        "where n.nspname = %s and c.relkind = 'r'", (s,)).fetchall()
        check("every table has row-level security on", rls and all(r["relrowsecurity"] for r in rls), str(rls))
    finally:
        c.close()


def test_browser_code():
    print("\n[the browser's app.js agrees]")
    node = shutil.which("node")
    if not node:
        check("node is here to run app.js", False)
    js = (ROOT / "public" / "app.js").as_uri()
    script = f"""
      const m = await import({json.dumps(js)});
      const s = await crypto.subtle.generateKey({{name: "ECDSA", namedCurve: "P-256"}}, false, ["sign", "verify"]);
      const w = await crypto.subtle.generateKey({{name: "ECDH", namedCurve: "P-256"}}, false, ["deriveBits"]);
      const b64 = (b) => Buffer.from(b).toString("base64");
      const k = {{sign: s, sign_pub: b64(await crypto.subtle.exportKey("spki", s.publicKey)),
                  wrap_pub: b64(await crypto.subtle.exportKey("spki", w.publicKey))}};
      const text = "ua-approve:v1\\nsome device\\nboss@x.in\\nbooks\\nA\\nB\\n1700000000";
      console.log(JSON.stringify({{...k, sign: undefined, text, sig: await m.sign(k, text),
                                   fp: await m.fingerprint(k.sign_pub, k.wrap_pub)}}));
    """
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                                    check=True).stdout)
    check("a signature made by app.js checks out here", devmod.verify(out["sign_pub"], out["text"], out["sig"]))
    check("... and not over other text", not devmod.verify(out["sign_pub"], out["text"] + "x", out["sig"]))
    check("app.js and the server work out the same 24 digits", out["fp"] == devmod.fingerprint(out["sign_pub"], out["wrap_pub"]))


def test_office_pcs(admin_c: TestClient, laptop: Device):
    """The office server and office PCs, end to end: the real app/office code, over real HTTP,
    against this control plane, running in a thread on a free port."""
    print("\n[office PCs, end to end]")
    import socket
    import threading
    import urllib.parse

    import httpx
    import uvicorn

    sys.path.insert(0, str(ROOT.parent))
    from app.office import keys as okeys, roster as oroster, settings as osettings
    from app.office.cloud import Cloud, Waiting
    from app.office.link import link

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    while not server.started:
        time.sleep(0.05)
    s = osettings.load()
    s["control_plane"] = base
    osettings.save(s)

    def browser_for(client: TestClient):
        """The person's browser: posts the PC's keys as /link would, then goes back to the PC."""
        def open_page(url):
            q = dict(urllib.parse.parse_qsl(url.split("#", 1)[1]))
            r = client.post("/api/devices/link", headers=H, json={k: q.get(k, "") for k in (
                "kind", "name", "os", "app_version", "sign_pub", "wrap_pub", "nonce")} | {"signature": q["sig"]})
            assert r.status_code == 200, r.text
            threading.Thread(target=lambda: httpx.get(
                f"http://127.0.0.1:{q['port']}/done?device={r.json()['device']['id']}&nonce={q['nonce']}")).start()
        return open_page

    def approve(device_id: str):
        d = next(x for x in admin_c.get("/api/admin/devices").json()["devices"] if x["id"] == device_id)
        r = admin_c.post(f"/api/admin/devices/{device_id}/approve", headers=H, json=approval(laptop, d, d["email"], d["caps"]))
        assert r.status_code == 200, r.text

    boss_c, _ = sign_in("boss@test.example")
    try:
        link("office_server", "server", "t", timeout=20, open_page=browser_for(boss_c))
        check("someone without admin access can't link the office server", False)
    except AssertionError as exc:
        check("someone without admin access can't link the office server", "admin" in str(exc), str(exc))

    srv = link("office_server", "OFFICE-PC (office server)", "1.1.0", timeout=20, open_page=browser_for(admin_c))
    check("the admin links this PC as the office server", bool(srv.device_id))
    cloud = Cloud(srv)
    try:
        cloud.access()
        check("before approval it gets no token", False)
    except Waiting:
        check("before approval it gets no token", True)
    approve(srv.device_id)
    tok = cloud.access()
    claims = json.loads(auth.unb64url(tok.split(".")[1]))
    check("approved, it gets a token checked against the pinned token key",
          claims["kind"] == "office_server" and claims["device"] == srv.device_id)
    stale = httpx.post(f"{base}/api/devices/token", headers=H, json={
        "device_id": srv.device_id, "ts": cloud._last_ts, "signature": srv.sign(f"ua-token:v1\n{srv.device_id}\n{cloud._last_ts}")})
    check("a token request sent twice is refused", stale.status_code == 400, stale.text)

    r = oroster.refresh(cloud)
    check("the office server fetches the signed roster", srv.device_id in r.devices and len(r.users) >= 2)
    me = r.devices[srv.device_id]
    approver = r.devices[me["approved_by_device"]]
    check("... which says which device approved it", approver["sign_pub"] == laptop.sign_pub)
    okeys.pin_admin(approver["sign_pub"])

    acct = admin_c.post("/api/admin/users", headers=H, json={"email": "acct@test.example", "caps": ["tib", "console"]})
    check("the admin adds the accountant", acct.status_code == 200, acct.text)
    acct_c, _ = sign_in("acct@test.example")
    pc = link("office_client", "ACCT-PC", "1.1.0", timeout=20, open_page=browser_for(acct_c))
    approve(pc.device_id)
    pc_tok = Cloud(pc).access()
    check("the accountant's PC is linked, approved and gets its token",
          json.loads(auth.unb64url(pc_tok.split(".")[1]))["caps"] == ["console", "tib"])
    r = httpx.get(f"{base}/api/office/roster", headers={"Authorization": f"Bearer {pc_tok}"})
    check("an office PC can't fetch the roster", r.status_code == 403, r.text)

    phone = Device()
    phone_c, _ = sign_in("admin@test.example")
    ph = attach(phone_c, phone).json()["device"]
    approve(ph["id"])
    res = okeys.sync(cloud, oroster.refresh(cloud))
    check("the office server gives the data key to the approved phone", ph["id"] in res["sent"].get(res["current"], []), str(res))
    mine = phone_c.get("/api/keys/me").json()
    w = next(k for k in mine["keys"] if k["kid"] == res["current"])
    check("... the phone fetches it and unwraps it to the office server's key",
          okeys.unwrap(w, w["kid"], ph["id"], phone.wrap_key) == okeys.dek(res["current"]) and mine["current"] == res["current"])
    r = httpx.put(f"{base}/api/office/wrapped-keys", headers={"Authorization": f"Bearer {pc_tok}", **H},
                  json={"kid": "k9", "keys": []})
    check("an office PC can't put keys", r.status_code == 403, r.text)

    admin_c.post(f"/api/admin/devices/{ph['id']}/revoke", headers=H)
    res2 = okeys.sync(cloud, oroster.refresh(cloud))
    check("revoking the phone makes a new key it doesn't get", res2["rotated"] and ph["id"] not in res2["eligible"], str(res2))
    check("... and the revoked phone can't fetch keys any more", phone_c.get("/api/keys/me").status_code == 401)
    admin_c.post(f"/api/admin/devices/{pc.device_id}/revoke", headers=H)
    try:
        Cloud(pc).access()
        check("a revoked PC gets no token", False)
    except Exception as exc:  # noqa: BLE001
        check("a revoked PC gets no token", "revoked" in str(exc).lower(), str(exc))
    server.should_exit = True


def main() -> int:
    migrate(admin="admin@test.example", name="Admin")
    try:
        admin_c = test_signin()
        laptop = test_devices(admin_c)
        test_office_pcs(admin_c, laptop)
        test_people(admin_c)
        test_locked_down()
        test_browser_code()
    finally:
        c = db.connect()
        c.execute(f"drop schema {db.SCHEMA} cascade")
        c.commit()
        c.close()
    print(f"\n{len(PASSED)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
