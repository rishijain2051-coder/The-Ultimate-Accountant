"""Builds the guide: installer/guide/Invoice Bridge - Guide.pdf

    python installer\\guide\\build_guide.py
    python installer\\guide\\build_guide.py --print-only   # after editing guide.html only

Starts a throwaway copy of the app - an empty data folder of its own, the
simulated Tally on port 9099 (never the real one on 9000), and the AI model
from installer\\stage - reads the synthetic sample bills through it, takes
pictures of the screens with Chrome or Edge in headless mode, and prints
guide.html, with those pictures, to PDF. Your own data is never touched.

Needs installer\\stage (run build.ps1 once) and Chrome or Edge.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STAGE = REPO / "installer" / "stage"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
SHOTS = HERE / "shots"
PDF = HERE / "Invoice Bridge - Guide.pdf"
API, TALLY, AI = 8799, 9099, 11441
BASE = f"http://127.0.0.1:{API}"

sys.path.insert(0, str(REPO / "backend"))


def browser() -> str:
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
              r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
              r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"):
        if Path(os.path.expandvars(p)).exists():
            return os.path.expandvars(p)
    sys.exit("Needs Chrome or Edge to take the pictures and print the PDF")


def call(method: str, path: str, body: bytes | None = None, ctype: str | None = None):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def upload(path: Path, voucher_type: str, company_id: int | None = None) -> int:
    """multipart/form-data by hand: one file, the voucher type and, for a bill
    without your GSTIN on it, the company chosen for it."""
    b = "----guide"
    fields = {"voucher_type": voucher_type, **({"company_id": company_id} if company_id else {})}
    parts = [*(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
               for k, v in fields.items()),
             f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
             f'Content-Type: application/octet-stream\r\n\r\n'.encode() + path.read_bytes() + b"\r\n",
             f"--{b}--\r\n".encode()]
    res = call("POST", "/api/documents/upload", b"".join(parts), f"multipart/form-data; boundary={b}")
    doc = res["results"][0]["document_id"]
    for _ in range(600):
        d = call("GET", f"/api/documents/{doc}")
        if d.get("entry_id"):
            return d["entry_id"]
        time.sleep(0.5)
    sys.exit(f"{path.name} was not read")


def shoot(exe: str, profile: str, url: str, name: str, height: int) -> Path:
    out = SHOTS / name
    # Light theme whatever Windows is set to: it prints better.
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--blink-settings=preferredColorScheme=1", f"--user-data-dir={profile}",
                    f"--window-size=1440,{height}", "--virtual-time-budget=20000", f"--screenshot={out}", url],
                   check=True, capture_output=True, timeout=120)
    return out


def tighten(path: Path) -> None:
    """The bill viewer fills the window's height, which leaves a tall empty
    band under the review panels. Cut it out and keep the button bar that is
    fixed to the foot of the window: the picture stays a true one, shorter."""
    from PIL import Image, ImageChops

    img = Image.open(path).convert("RGB")
    w, h = img.size
    bar = 80  # the fixed buttons at the foot
    column = img.crop((int(w * 0.62), 0, w - 10, h - bar))  # the review panels
    empty = Image.new("RGB", column.size, column.getpixel((column.width - 5, column.height - 5)))
    rows = ImageChops.difference(column, empty).convert("L").point(lambda v: 255 if v > 18 else 0)
    bottom = rows.getbbox()[3] + 24
    out = Image.new("RGB", (w, bottom + bar), (255, 255, 255))
    out.paste(img.crop((0, 0, w, bottom)), (0, 0))
    out.paste(img.crop((0, h - bar, w, h)), (0, bottom))
    out.save(path)


def wait_port(url: str, seconds: float = 90) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return
        except OSError:
            time.sleep(0.5)
    sys.exit(f"{url} did not answer")


def print_pdf(exe: str, tmp: Path) -> None:
    PDF.unlink(missing_ok=True)
    subprocess.run([exe, "--headless=new", "--disable-gpu", f"--user-data-dir={tmp / 'print'}",
                    "--no-pdf-header-footer", f"--print-to-pdf={PDF}", (HERE / "guide.html").as_uri()],
                   check=True, capture_output=True, timeout=120)
    if not PDF.exists():
        sys.exit("the PDF was not written")
    print(f"guide written: {PDF} ({PDF.stat().st_size // 1024} KB)")


def main() -> None:
    from tests.vision_eval import make_samples

    exe = browser()
    SHOTS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="tib-guide-"))
    if "--print-only" in sys.argv:  # the words changed, the pictures did not
        return print_pdf(exe, tmp)
    if not (STAGE / "ollama" / "ollama.exe").exists():
        sys.exit("the pictures need the AI in the build: powershell -File installer\\get-ai.ps1 "
                 "-Dest installer\\stage -Downloads installer\\.cache")
    env = {**os.environ, "DATA_DIR": str(tmp / "data"), "TALLY_PORT": str(TALLY), "WATCH_ENABLED": "false",
           "LLM_ENABLED": "true", "OLLAMA_HOST": f"http://127.0.0.1:{AI}"}
    procs = [
        subprocess.Popen([str(PY), str(REPO / "backend" / "tests" / "fake_tally.py"), str(TALLY)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([str(STAGE / "ollama" / "ollama.exe"), "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env={**os.environ, "OLLAMA_HOST": f"127.0.0.1:{AI}",
                                                        "OLLAMA_MODELS": str(STAGE / "ollama-models")}),
        subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--port", str(API), "--no-access-log"],
                         cwd=str(REPO / "backend"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    try:
        wait_port(f"{BASE}/api/health")
        wait_port(f"http://127.0.0.1:{AI}/api/version")
        call("POST", "/api/tally/companies/discover")
        companies = call("GET", "/api/tally/companies")
        for c in companies:
            call("POST", f"/api/tally/companies/{c['id']}/sync")
        impex = next(c["id"] for c in companies if c["tally_name"] == "Saffron Impex")
        bills = {p.name: p for p, _truth in make_samples(tmp)}
        profile = str(tmp / "browser")
        shoot(exe, profile, f"{BASE}/", "upload.png", 760)
        # Review opens on the weakest entry. Headless Chrome's fast-forwarded
        # clock loses the ?id= of a deep link (a real browser keeps it), so
        # each bill is photographed while it is the one Review opens on: the
        # kachcha bill alone, then the tax invoice, which scores lower.
        upload(bills["hindi_kachcha_bill.jpg"], "Purchase", impex)  # a kachcha bill: no GSTIN to go by
        shoot(exe, profile, f"{BASE}/review/", "review.png", 2200)  # the whole screen
        upload(bills["hindi_tax_invoice.jpg"], "Purchase")
        shoot(exe, profile, f"{BASE}/review/", "review-checks.png", 2200)
        shoot(exe, profile, f"{BASE}/settings/", "settings.png", 1500)
        for name in ("review.png", "review-checks.png"):
            tighten(SHOTS / name)
        print("sample bills read, pictures taken")
    finally:
        # The whole tree: Ollama starts a model worker of its own, which
        # terminate() would leave running, holding the graphics memory.
        for p in procs:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)

    print_pdf(exe, tmp)


if __name__ == "__main__":
    main()
