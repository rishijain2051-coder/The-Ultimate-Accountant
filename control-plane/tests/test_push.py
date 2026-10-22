"""Web Push as a browser receives it: RFC 8291 decryption and the VAPID signature.  python tests/test_push.py"""

import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

key = ec.generate_private_key(ec.SECP256R1())
b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
os.environ["PUBLIC_ORIGIN"] = "https://testserver"
os.environ["VAPID_PRIVATE_KEY"] = b64u(key.private_numbers().private_value.to_bytes(32, "big"))
os.environ["VAPID_PUBLIC_KEY"] = b64u(key.public_key().public_bytes(serialization.Encoding.X962,
                                                                     serialization.PublicFormat.UncompressedPoint))
from cp import push  # noqa: E402

# the browser's side: its subscription keys
ua = ec.generate_private_key(ec.SECP256R1())
ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
auth = os.urandom(16)
message = json.dumps({"sealed": {"kid": "k1", "nonce": "x", "ct": "y" * 900}}).encode()
body = push.encrypt(message, b64u(ua_pub), b64u(auth))

salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
assert rs == 4096 and idlen == 65 and len(ct) <= rs
shared = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
kdf = lambda salt, ikm, info, n: hmac.new(hmac.new(salt, ikm, hashlib.sha256).digest(), info + b"\x01", hashlib.sha256).digest()[:n]  # noqa: E731
ikm = kdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
plain = AESGCM(kdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
    kdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), ct, None)
assert plain == message + b"\x02", "decrypted as a browser would"
print("  ok  a browser decrypts the push (RFC 8291 aes128gcm)")

h = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc")
t, k = h.removeprefix("vapid t=").split(", k=")
head, claims, sig = t.split(".")
assert k == os.environ["VAPID_PUBLIC_KEY"]
c = json.loads(push.unb64u(claims))
assert c["aud"] == "https://fcm.googleapis.com" and c["exp"] > 0 and c["sub"].startswith("https://")
raw = push.unb64u(sig)
key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                        f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
print("  ok  the VAPID token is signed by our key, for the push service's origin")
print("test_push: all checks passed")

# The service worker opens what the office server seals (app/keeper/notices.py seal), under Node.
import re as _re  # noqa: E402
import subprocess  # noqa: E402

dek, nonce = AESGCM.generate_key(256), os.urandom(12)
note = {"title": "Overdue in Rao & Sons: 1 party", "body": "Steel Co: ₹2,500 to pay (1 bill)", "url": "/books#outstanding"}
ct = AESGCM(dek).encrypt(nonce, json.dumps(note).encode(), b"ua-push:v1:k3")
sw = (Path(__file__).parents[1] / "public" / "sw.js").read_text(encoding="utf-8")
fn = _re.search(r"const unb64 = .*?\n\nasync function opened\(.*?\n}\n", sw, _re.S).group(0)
js = fn + f"""
const raw = Uint8Array.from(atob("{base64.b64encode(dek).decode()}"), c => c.charCodeAt(0));
const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
const m = await opened({{kid: "k3", nonce: "{base64.b64encode(nonce).decode()}", ct: "{base64.b64encode(ct).decode()}"}}, async (kid) => kid === "k3" ? key : null);
const none = await opened({{kid: "k9", nonce: "", ct: ""}}, async () => null);
console.log(JSON.stringify({{m, none}}));
"""
url = "data:text/javascript;base64," + base64.b64encode(js.encode()).decode()
out = json.loads(subprocess.run(["node", "--input-type=module", "-e", f"await import('{url}')"],
                                capture_output=True, text=True, encoding="utf-8", check=True).stdout)
assert out["m"] == note and out["none"] is None, out
print("  ok  the service worker opens a sealed notice; without the key it shows a plain one")
print("test_push: sealed notices checked too")

# Who gets an alert: books.alerts (or the old broad books), not books.view alone; admin alerts need admin.
from cp import auth  # noqa: E402
assert auth.holders("books.alerts") == ["books.alerts", "books"] and auth.holders("admin") == ["admin"]
assert auth.holds(["books"], "books.alerts") and auth.holds(["books.alerts"], "books.alerts")
assert not auth.holds(["books.view", "books.sales"], "books.alerts") and not auth.holds(["tib"], "books.view")
assert auth.holds(["console"], "con.ai") and not auth.holds(["con.ai"], "console")
sent = []
class FakeConn:
    def execute(self, sql, params):
        sent.append(params)
        return type("R", (), {"fetchall": lambda self: []})()
push.send(FakeConn(), "overdue", {})
push.send(FakeConn(), "admin", {})
assert sent == [(["books.alerts", "books"], "overdue"), (["admin"], "admin")], sent
print("  ok  alerts go to books.alerts holders (and the old books key); admin alerts to admins")
