"""How well does a local model read bills off the page image?

    python tests/vision_eval.py qwen3.5:4b qwen3.5:9b
    python tests/vision_eval.py qwen3.5:4b --dir samples\\hindi
    python tests/vision_eval.py qwen3.5:4b --host http://127.0.0.1:11436   # e.g. a CPU-only Ollama

Built-in samples have known answers and are scored field by field: a value
the bill does not have counts as wrong, because a made-up GSTIN is worse than
a blank one. Bills from --dir have no answers, so what each model read is
printed for checking against the paper. Needs Ollama running with the models.
"""

from __future__ import annotations

import argparse
import copy
import io
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository: app.bridge is a package in it
sys.stdout.reconfigure(encoding="utf-8")

import pymupdf  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from app.bridge.config import settings  # noqa: E402
from app.bridge.services import gst, llm, pdf_text  # noqa: E402
from app.bridge.services.matching import normalize  # noqa: E402
from app.bridge.services.parse import ascii_digits, parse, to_date, to_decimal  # noqa: E402

FONTS = pymupdf.Archive("C:/Windows/Fonts")
CSS = "@font-face {font-family: hand; src: url(Inkfree.ttf);} * {font-family: sans-serif;} .h {font-family: hand; font-size: 1.25em;}"


def _gstin(base14: str) -> str:
    return next(base14 + c for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" if gst.gstin_checksum_ok(base14 + c))


def _photo(page: pymupdf.Page, tilt: float) -> bytes:
    """A page as a slightly tilted, soft phone photo."""
    im = Image.open(io.BytesIO(page.get_pixmap(dpi=150).tobytes("png"))).convert("L")
    im = im.rotate(tilt, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def _bill(rows: list[tuple[float, float, str]], lines: list[float], tilt: float) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=500)
    for x, y, html in rows:
        page.insert_htmlbox(pymupdf.Rect(x, y, 410, y + 40), html, css=CSS, archive=FONTS)
    for y in lines:
        page.draw_line((15, y), (405, y), width=0.8)
    return _photo(page, tilt)


def make_samples(folder: Path) -> list[tuple[Path, dict]]:
    out = []

    # 1. Your Kestrel Audit invoice, as a scan: pictures only, no text layer.
    src = pymupdf.open(HERE / "fixtures" / "kestrel_audit_igst.pdf")
    scan = pymupdf.open()
    for page in src:
        jpg = _photo(page, 0.6)
        w, h = Image.open(io.BytesIO(jpg)).size
        scan.new_page(width=w * 72 / 150, height=h * 72 / 150).insert_image(
            pymupdf.Rect(0, 0, w * 72 / 150, h * 72 / 150), stream=jpg)
    p = folder / "kestrel_audit_scan.pdf"
    scan.save(p)
    out.append((p, {"invoice_no": "5500100-26004852", "invoice_date": "2026-08-18",
                    "party_name": "Kestrel Audit India", "party_gstin": "09XXXCX3333X1ZV",
                    "taxable_total": "143414.00", "cgst": None, "sgst": None, "igst": "25814.52",
                    "grand_total": "169228.52",
                    "lines": ["78750.00", "5000.00", "35000.00", "24664.00"]}))

    # 2. A kachcha bill: printed Hindi template, values written in by hand.
    rows = [
        (20, 15, '<p style="text-align:center;font-size:20px"><b>श्री गणेश ट्रेडर्स</b></p>'),
        (20, 42, '<p style="text-align:center;font-size:10px">किराना एवं जनरल मर्चेंट, सोजती गेट, जोधपुर (राज.) &nbsp; मो. 98290 12345</p>'),
        (20, 75, '<p style="font-size:12px">बिल नं. <span class="h">47</span></p>'),
        (250, 75, '<p style="font-size:12px">दिनांक <span class="h">12/09/2026</span></p>'),
        (20, 100, '<p style="font-size:12px">श्रीमान वर्धमान इम्पेक्स, जोधपुर</p>'),
        (20, 130, '<p style="font-size:12px">क्र.</p>'), (50, 130, '<p style="font-size:12px">विवरण</p>'),
        (200, 130, '<p style="font-size:12px">मात्रा</p>'), (275, 130, '<p style="font-size:12px">दर</p>'),
        (340, 130, '<p style="font-size:12px">राशि</p>'),
    ]
    for i, (desc, qty, rate, amt) in enumerate([("चावल बासमती", 25, 96, 2400), ("चना दाल", 10, 88, 880),
                                                ("सरसों तेल", 5, 165, 825)]):
        y = 158 + i * 26
        rows += [(22, y, f'<p style="font-size:12px">{i + 1}</p>'), (50, y, f'<p style="font-size:12px">{desc}</p>'),
                 (200, y, f'<p style="font-size:12px"><span class="h">{qty}</span> किलो</p>'),
                 (275, y, f'<p class="h">{rate}</p>'), (340, y, f'<p class="h">{amt}</p>')]
    rows += [(250, 250, '<p style="font-size:12px"><b>कुल योग</b></p>'), (340, 248, '<p class="h">4105</p>'),
             (20, 292, '<p style="font-size:11px">रुपये (शब्दों में): चार हज़ार एक सौ पाँच मात्र</p>'),
             (300, 340, '<p style="font-size:11px">हस्ताक्षर</p>')]
    p = folder / "hindi_kachcha_bill.jpg"
    p.write_bytes(_bill(rows, [136, 162, 240, 284], tilt=-1.2))
    out.append((p, {"invoice_no": "47", "invoice_date": "2026-09-12", "party_name": "श्री गणेश ट्रेडर्स",
                    "party_gstin": None, "taxable_total": None, "cgst": None, "sgst": None, "igst": None,
                    "grand_total": "4105", "lines": ["2400", "880", "825"]}))

    # 3. A printed Hindi tax invoice, numbers in Devanagari digits.
    g = _gstin("08AAKFR4821M1Z")
    dv = str.maketrans("0123456789", "०१२३४५६७८९")
    rows = [
        (20, 15, '<p style="text-align:center;font-size:18px"><b>मैसर्स राठी ट्रेडिंग कंपनी</b></p>'),
        (20, 40, f'<p style="text-align:center;font-size:10px">थोक विक्रेता, पावटा, जोधपुर &nbsp; जीएसटीआईएन: {g}</p>'),
        (20, 60, '<p style="text-align:center;font-size:13px"><b>कर बीजक</b></p>'),
        (20, 85, '<p style="font-size:12px">बीजक सं. ११२</p>'), (250, 85, '<p style="font-size:12px">दिनांक ०३/०९/२०२६</p>'),
        (20, 105, '<p style="font-size:12px">क्रेता: वर्धमान इम्पेक्स, जोधपुर (08ZZZPZ1111Z1Z4)</p>'),
        (20, 135, '<p style="font-size:12px">विवरण</p>'), (170, 135, '<p style="font-size:12px">मात्रा</p>'),
        (250, 135, '<p style="font-size:12px">दर</p>'), (330, 135, '<p style="font-size:12px">राशि</p>'),
    ]
    for i, (desc, qty, rate, amt) in enumerate([("चीनी", "50 किलो", "44.00", "2200.00"),
                                                ("चाय पत्ती", "4 किलो", "270.00", "1080.00")]):
        y = 160 + i * 24
        rows += [(20, y, f'<p style="font-size:12px">{desc}</p>'),
                 (170, y, f'<p style="font-size:12px">{qty.translate(dv)}</p>'),
                 (250, y, f'<p style="font-size:12px">{rate.translate(dv)}</p>'),
                 (330, y, f'<p style="font-size:12px">{amt.translate(dv)}</p>')]
    for i, (label, amt) in enumerate([("कर योग्य राशि", "3280.00"), ("सीजीएसटी 2.5%", "82.00"),
                                      ("एसजीएसटी 2.5%", "82.00"), ("कुल राशि", "3444.00")]):
        rows += [(200, 222 + i * 22, f'<p style="font-size:12px">{label.translate(dv)}</p>'),
                 (330, 222 + i * 22, f'<p style="font-size:12px">{amt.translate(dv)}</p>')]
    p = folder / "hindi_tax_invoice.jpg"
    p.write_bytes(_bill(rows, [141, 167, 215], tilt=0.8))
    out.append((p, {"invoice_no": "112", "invoice_date": "2026-09-03", "party_name": "मैसर्स राठी ट्रेडिंग कंपनी",
                    "party_gstin": g, "taxable_total": "3280", "cgst": "82", "sgst": "82", "igst": None,
                    "grand_total": "3444", "lines": ["2200", "1080"]}))
    return out


def _right(key: str, got, want) -> bool:
    if want is None:
        return got in (None, "", 0) or to_decimal(str(got)) == 0
    if got in (None, ""):
        return False
    if key == "party_name":
        return fuzz.token_set_ratio(normalize(str(got)), normalize(want)) >= 80
    if key == "invoice_date":
        return str(to_date(ascii_digits(str(got)))) == want
    if key in ("invoice_no", "party_gstin"):
        return ascii_digits(str(got)).replace(" ", "").upper() == want.upper()
    return to_decimal(str(got)) == Decimal(want)


def _model_view(data: dict) -> dict:
    """The model's raw answer, keyed like the truth."""
    return {"invoice_no": data.get("invoice_no"), "invoice_date": data.get("invoice_date"),
            "party_name": data.get("supplier_name"),
            "party_gstin": data.get("supplier_gstin"),
            **{k: data.get(k) for k in ("taxable_total", "cgst", "sgst", "igst", "grand_total")},
            "lines": [r.get("amount") for r in data.get("lines") or [] if isinstance(r, dict)]}


def run(models: list[str], samples: list[tuple[Path, dict | None]]) -> None:
    captured: dict = {}
    real = llm.read_fields

    def spy(*a, **k):
        captured["data"] = real(*a, **k)
        return captured["data"]

    llm.read_fields = spy
    summary = []
    for path, truth in samples:
        t = time.perf_counter()
        res = pdf_text.extract(path)
        base = parse(res.text, own_gstins={"08ZZZPZ1111Z1Z4"})
        images = pdf_text.page_images(path)
        print(f"\n=== {path.name}  (OCR {time.perf_counter() - t:.0f}s, {len(images)} page image(s))")
        for model in models:
            settings.ollama_model = model
            p, captured["data"] = copy.deepcopy(base), None
            t = time.perf_counter()
            llm.merge_into(p, res.text, images=images)
            secs = time.perf_counter() - t
            raw = _model_view(captured["data"] or {})
            final = {k: p.get(k) for k in ("invoice_no", "invoice_date", "party_name", "party_gstin",
                                           "taxable_total", "cgst", "sgst", "igst", "grand_total")}
            final["lines"] = [r.get("amount") for r in p.lines]
            print(f"--- {model}: {secs:.0f}s" + ("" if captured["data"] else "  (NO ANSWER)"))
            if truth is None:
                for k, v in final.items():
                    print(f"    {k:14} {v}")
                continue
            score = {"model": 0, "app": 0}
            for k, want in truth.items():
                if k == "lines":
                    amounts = lambda xs: sorted(to_decimal(str(x)) or Decimal(-1) for x in xs)  # noqa: E731
                    ok_m = amounts(raw["lines"]) == amounts(want)
                    ok_a = amounts(final["lines"]) == amounts(want)
                else:
                    ok_m, ok_a = _right(k, raw.get(k), want), _right(k, final.get(k), want)
                score["model"] += ok_m
                score["app"] += ok_a
                mark = "ok " if ok_a else "BAD"
                note = "" if ok_m == ok_a else ("  (model alone: right)" if ok_m else "  (model alone: wrong)")
                print(f"    {mark} {k:14} got {final.get(k)!s:28} want {want}{note}")
            for c in p.conflicts:
                print(f"    flagged: {c['field']} text={c['text']} image={c['image']}")
            summary.append((path.name, model, score["model"], score["app"], len(truth), secs))
    llm.read_fields = real

    if summary:
        print("\nsample                        model            model  app   seconds")
        for name, model, m, a, n, s in summary:
            print(f"{name:29} {model:16} {m:2}/{n}  {a:2}/{n}  {s:6.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--dir", type=Path, help="your own bills (no answers: prints what was read)")
    ap.add_argument("--host", help="another Ollama, e.g. one started CPU-only")
    a = ap.parse_args()
    settings.llm_enabled = True
    if a.host:
        settings.ollama_host = a.host
    if a.dir:
        files = sorted(f for f in a.dir.iterdir() if f.suffix.lower() in pdf_text.IMAGE_SUFFIXES | {".pdf"})
        run(a.models, [(f, None) for f in files])
    else:
        with tempfile.TemporaryDirectory() as d:
            run(a.models, make_samples(Path(d)))
