"""Bills sent from a phone: the code, its page and the sending, over real
HTTP on a free port. Run it with:  python tests/test_phone.py

Same style as test_pipeline.py: no pytest, a check(name, condition) helper.
The reading itself is not run here (documents._process is swapped for a
recorder): only what the phone's server keeps, refuses and serves.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository: app.bridge is a package in it

_tmp = Path(tempfile.mkdtemp(prefix="tallytest-phone-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LLM_ENABLED"] = "false"
os.environ["ASSURANCE_AI_DIR"] = str(_tmp / "ai")   # the app's AI: a folder of its own, never the one on this computer
os.environ["WATCH_ENABLED"] = "false"
os.environ["TALLY_PORT"] = "9"
os.environ["PHONE_PORT"] = "0"  # any free port

import pymupdf                                    # noqa: E402
from PIL import Image                             # noqa: E402

from app.bridge.db import SessionLocal, init_db          # noqa: E402
from app.bridge.models import Document                   # noqa: E402
from app.bridge.routers import documents                 # noqa: E402
from app.bridge.services import phone                    # noqa: E402

PASSED: list[str] = []
READ: list[int] = []
documents._process = lambda doc_id, _company, _llm: READ.append(doc_id)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)


def jpeg(w: int = 400, h: int = 560, shade: int = 250) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (shade, shade, shade)).save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url: str, body) -> tuple[int, dict]:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def read_started(n: int) -> None:  # the reading starts on a thread of its own
    end = time.time() + 5
    while len(READ) < n and time.time() < end:
        time.sleep(0.05)


def main() -> None:
    init_db()
    s = phone.status()
    check("off until asked", not s["on"] and s["url"] is None and s["qr"] is None)

    s = phone.start()
    check("a code", s["on"] and "/p/" in (s["url"] or ""), str(s))
    check("its QR", s["qr"]["size"] >= 21 and s["qr"]["path"].startswith("M"))
    url = s["url"]
    here = url.replace(url.split("/")[2].split(":")[0], "127.0.0.1", 1)
    key = url.rsplit("/", 1)[1]
    root = here.rsplit("/p/", 1)[0]

    code, page = get(here)
    check("the page", code == 200 and b"Photograph a bill" in page and b'"Purchase"' in page, str(code))
    check("the types written in", b"__TYPES__" not in page)
    check("a wrong key gets nothing", get(f"{root}/p/{'x' * len(key)}")[0] == 410)
    check("nothing else is served", get(f"{root}/api/health")[0] == 404 and get(f"{root}/bill/{'0' * 64}")[0] == 404)
    check("fonts only by name", get(f"{root}/fonts/..%2Fphone.html")[0] == 404)

    code, r = post(f"{here}/send", {"type": "Purchase", "pages": [jpeg()]})
    check("one photo sent", code == 200 and r.get("ok"), f"{code} {r}")
    read_started(1)
    with SessionLocal() as db:
        d = db.get(Document, r["document_id"])
        check("kept as the photo", d.filename.startswith("Phone ") and d.filename.endswith(".jpg"), d.filename)
        check("from the phone, as a Purchase", d.source == "phone" and d.requested_voucher_type == "Purchase"
              and d.requested_voucher_parent == "Purchase")
        check("queued for reading", READ == [d.id], str(READ))

    code, r = post(f"{here}/send", {"type": "Payment", "pages": [jpeg(), jpeg(shade=235)]})
    check("two pages sent", code == 200, f"{code} {r}")
    read_started(2)
    with SessionLocal() as db:
        d = db.get(Document, r["document_id"])
        check("two pages, one PDF", d.filename.endswith(".pdf"), d.filename)
        with pymupdf.open(d.stored_path) as pdf:
            check("a page a photo", pdf.page_count == 2)

    check("the type must be one it offers", post(f"{here}/send", {"type": "Stock Journal", "pages": [jpeg()]})[0] == 400)
    check("no pages", post(f"{here}/send", {"type": "Purchase", "pages": []})[0] == 400)
    check("too many pages", post(f"{here}/send", {"type": "Purchase", "pages": [jpeg(20, 20)] * 11})[0] == 400)
    check("not a photo", post(f"{here}/send", {"type": "Purchase", "pages": [base64.b64encode(b"hello").decode()]})[0] == 400)
    check("not JSON", post(f"{here}/send", b"{nope")[0] == 400)
    check("not an object", post(f"{here}/send", [1, 2])[0] == 400)
    check("nothing kept from those", len(READ) == 2)

    code, body = get(f"{here}/sent")
    sent = json.loads(body)
    check("what was sent, newest first", code == 200 and [x["type"] for x in sent] == ["Payment", "Purchase"]
          and all(x["state"] == "reading" and x["at"] for x in sent), str(sent))
    check("the computer sees the same", [x["id"] for x in phone.status()["sent"]] == [x["id"] for x in sent])

    new = phone.start()
    check("a new code", new["url"] != url)
    check("the old one has ended", get(here)[0] == 410
          and post(f"{here}/send", {"type": "Purchase", "pages": [jpeg(shade=200)]})[0] == 410)

    phone._session["until"] = time.time() - 1
    check("twelve hours on, it ends", not phone.status()["on"] and phone._server is None)

    phone.start()
    phone.stop()
    check("Stop", not phone.status()["on"] and phone._server is None)
    try:
        urllib.request.urlopen(here, timeout=3)
        listening = True
    except urllib.error.URLError:
        listening = False
    check("nothing listens after Stop", not listening)

    print(f"phone: {len(PASSED)} checks passed")


if __name__ == "__main__":
    try:
        main()
    finally:
        phone.stop()
        shutil.rmtree(_tmp, ignore_errors=True)
