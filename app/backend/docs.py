"""
Document intake.

Everything downstream wants the same thing: a rectangular grid of strings. This
module produces one from whatever the user actually has — a spreadsheet, a CSV,
a text PDF, a scanned PDF, or a photograph of a statement.

The routing is deliberate. A PDF that already carries text is read as text,
because that is exact and fast; OCR only runs when a page turns out to be an
image. Nothing here needs a system binary: pages are rasterised with pypdfium2
and recognised with an ONNX model that ships inside the Python package, so the
whole path works on a machine with no internet and no admin rights.
"""

import csv
import io
import re
import sys
import threading
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy, process-wide OCR engine
# ---------------------------------------------------------------------------

_ocr_lock = threading.Lock()
_ocr_engine = None
_ocr_error = None

# A page with less real text than this is almost certainly a scan.
TEXT_PAGE_MIN_CHARS = 90
OCR_DPI = 200
OCR_MAX_PAGES = 40
# Past these sizes a picture costs gigabytes and minutes for nothing: an A4
# page at 200 dpi is 2339 px, a 12-megapixel photo 4000.
OCR_DET_MAX_SIDE = 4000
OCR_RENDER_MAX_SIDE = 5000


def ocr_available():
    """Whether recognition can run, without paying the load cost to find out."""
    global _ocr_error
    if _ocr_engine is not None:
        return True
    if _ocr_error is not None:
        return False
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception as exc:                          # noqa: BLE001
        _ocr_error = str(exc)
        return False


_BARE_MODULES = ("ch_ppocr_v3_det", "ch_ppocr_v3_rec", "ch_ppocr_v2_cls")
_EXPECTED = {"ch_ppocr_v3_det": "TextDetector",
             "ch_ppocr_v3_rec": "TextRecognizer",
             "ch_ppocr_v2_cls": "TextClassifier"}
# What the aliasing actually managed, reported through /api/ocr/status. A
# packaged build has no console, so this is the only way to see it.
_alias_report = {}


def _alias_bare_modules():
    """Let rapidocr find its own sub-packages inside a packaged build.

    rapidocr appends its directory to sys.path and then imports these three by
    bare name. That works from a normal install, where the .py files are on
    disk, but a frozen build serves modules from an archive and PyInstaller
    strips .py files out of anything collected as data - so the bare import
    finds a directory holding only a config.yaml, binds it as an empty
    namespace package, and OCR fails on the first scan.

    This has to run before anything imports rapidocr at all, and it has to
    overwrite rather than skip. Importing the top-level package is itself what
    triggers the bare imports, so by the time the engine is constructed the
    broken namespace packages are already cached - leaving them in place was
    the whole bug the first time round.
    """
    import importlib
    for name in _BARE_MODULES:
        try:
            mod = importlib.import_module(f"rapidocr_onnxruntime.{name}")
            sys.modules[name] = mod
            _alias_report[name] = "ok" if hasattr(mod, _EXPECTED[name]) else (
                f"imported but no {_EXPECTED[name]}; "
                f"file={getattr(mod, '__file__', None)!r} "
                f"path={list(getattr(mod, '__path__', []))!r}")
        except Exception as exc:                      # noqa: BLE001
            _alias_report[name] = f"{type(exc).__name__}: {exc}"


def _ocr_threads():
    """Threads for the recogniser. OCR_THREADS in the environment overrides.

    rapidocr leaves the count to onnxruntime, which spreads a small model over
    every core. On a CPU with slow efficiency cores beside the fast ones the
    fast threads then sit waiting for the slow ones: on a 20-thread laptop a
    receipt photo took 2 s at the default and 0.74 s at six threads. Past six
    it gets slower again.
    """
    import os
    try:
        return max(1, int(os.environ["OCR_THREADS"]))
    except (KeyError, ValueError):
        return max(2, min(6, (os.cpu_count() or 4) // 2))


def _tune_sessions(engine):
    """Rebuild the three model sessions with settings that are not slow.

    rapidocr also turns the memory arena off, so every run re-allocates its
    buffers; that alone was the rest of the difference, 13 s down to 2 s on
    the same photo. It is done after construction because the library offers
    no way to pass session options in. If its internals ever move, this leaves
    the engine as it was - slower, never broken.
    """
    import onnxruntime as ort
    parts = [getattr(getattr(engine, "text_detector", None), "infer", None),
             getattr(getattr(engine, "text_recognizer", None), "session", None),
             getattr(getattr(engine, "text_cls", None), "infer", None)]
    for wrapper in parts:
        try:
            path = wrapper.session._model_path
            opts = ort.SessionOptions()
            opts.log_severity_level = 4
            opts.enable_cpu_mem_arena = True
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = _ocr_threads()
            opts.inter_op_num_threads = 1
            wrapper.session = ort.InferenceSession(
                path, sess_options=opts, providers=["CPUExecutionProvider"])
        except Exception:                             # noqa: BLE001
            continue


class _DetSize:
    """The detector's own resize below OCR_DET_MAX_SIDE; above it, words are
    found on a copy no larger than that. The boxes are mapped back, so each
    word is still read from the full picture. Shrinking every page instead
    was tried: it halved the time on clean samples and ran neighbouring
    columns together on real scans."""

    def __init__(self, op, side):
        self.usual, self.side = op, side
        self.capped = type(op)(limit_side_len=side, limit_type="max")

    def __call__(self, data):
        return (self.capped if max(data["image"].shape[:2]) > self.side else self.usual)(data)


class _GivesMemoryBack:
    """An inference session that hands its working memory back after each run.

    The detector's buffers grow with the page: 1.8 GB after one 300 dpi page,
    kept for good, and one poster-sized scan took a test run past 11 GB.
    Words are found once a page, so letting that go costs little; the
    recogniser runs dozens of times a page on small crops and keeps its pool.
    """

    def __init__(self, session):
        import onnxruntime as ort
        self._session = session
        self._opts = ort.RunOptions()
        self._opts.add_run_config_entry("memory.enable_memory_arena_shrinkage", "cpu:0")

    def run(self, output_names, input_feed, run_options=None):
        return self._session.run(output_names, input_feed, self._opts)

    def __getattr__(self, name):
        return getattr(self._session, name)


def _fit_engine(engine):
    """Bound the detector's time and memory, and read words three at a time.

    The recogniser pads every crop in a batch to the widest one. Against the
    text layers of 24 real PDF pages, read as pictures, batches of 3 took
    45 s where the library's 6 took 53-55 s, and found slightly more of the
    figures (91.0% against 90.8%) and words (79.1% against 77.5%). If the
    library's internals ever move, the engine is left as it was.
    """
    try:
        det = engine.text_detector
        det.preprocess_op[0] = _DetSize(det.preprocess_op[0], OCR_DET_MAX_SIDE)
        det.infer.session = _GivesMemoryBack(det.infer.session)
        engine.text_recognizer.rec_batch_num = 3
    except Exception:                                 # noqa: BLE001
        pass


def get_ocr():
    """Load the recogniser once. First call costs a second or two."""
    global _ocr_engine, _ocr_error
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            try:
                _alias_bare_modules()
                from rapidocr_onnxruntime import RapidOCR
                engine = RapidOCR()
                _tune_sessions(engine)
                _fit_engine(engine)
                # The per-line angle classifier guesses whether each crop is
                # upside down and flips it if so. On a statement it guessed
                # wrong on a short figure and "6,063.00" came out as
                # "00'E90'9". Statements are printed upright; the flip only
                # ever did harm.
                engine.use_angle_cls = False
                _ocr_engine = engine
            except Exception as exc:                  # noqa: BLE001
                _ocr_error = str(exc)
                raise RuntimeError(
                    "Optical character recognition is not available on this "
                    f"machine ({exc}). Install it with: python -m pip install "
                    "rapidocr-onnxruntime"
                ) from exc
    return _ocr_engine


def ocr_status(probe=False):
    """`probe` aliases and reports the sub-module state without loading models.

    Cheap enough to call from a diagnostic, and the only window into a build
    that has no console to print to.
    """
    if probe:
        _alias_bare_modules()
    return {
        "available": ocr_available(),
        "loaded": _ocr_engine is not None,
        "error": _ocr_error,
        "dpi": OCR_DPI,
        "max_pages": OCR_MAX_PAGES,
        "frozen": bool(getattr(sys, "frozen", False)),
        "submodules": dict(_alias_report),
    }


# ---------------------------------------------------------------------------
# Format sniffing
# ---------------------------------------------------------------------------

EXCEL_EXT = {".xlsx", ".xlsm", ".xltx"}
CSV_EXT = {".csv", ".txt", ".tsv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXT = {".pdf"}


def sniff(data, filename=""):
    """Identify a payload by magic bytes first, extension second."""
    ext = Path(filename or "").suffix.lower()
    head = data[:8] if isinstance(data, (bytes, bytearray)) else b""

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        # Both .xlsx and .docx are zips; look inside.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
            if any(n.startswith("xl/") for n in names):
                return "excel"
        except zipfile.BadZipFile:
            pass
        return "zip"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"BM", b"II*\x00", b"MM\x00*")):
        return "image"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls_legacy"

    if ext in PDF_EXT:
        return "pdf"
    if ext in EXCEL_EXT:
        return "excel"
    if ext in IMAGE_EXT:
        return "image"
    if ext in CSV_EXT:
        return "csv"
    if ext == ".xls":
        return "xls_legacy"
    return "csv" if _looks_textual(data) else "unknown"


def _looks_textual(data, sample=2048):
    if not data:
        return False
    chunk = data[:sample]
    if b"\x00" in chunk:
        return False
    printable = sum(1 for b in chunk if 9 <= b <= 13 or 32 <= b <= 126 or b >= 160)
    return printable / len(chunk) > 0.88


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_csv_grid(data):
    """Parse delimited text, sniffing the delimiter and tolerating BOMs."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ",;\t|"}
        delim = max(counts, key=counts.get) if max(counts.values()) else ","

    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append([(c or "").strip() for c in row])
    return _trim_grid(rows)


def read_excel_grid(data, sheet=None, max_rows=20000):
    """Every sheet of a workbook as grids, or one named sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = {}
    names = [sheet] if sheet else wb.sheetnames
    for name in names:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        rows = []
        for i, row in enumerate(ws.iter_rows(max_col=min(ws.max_column or 0, 200), values_only=True)):
            if i >= max_rows:
                break
            rows.append(["" if v is None else (v if not isinstance(v, str) else v.strip())
                         for v in row])
        out[name] = _trim_grid(rows)
    wb.close()
    return out


def read_pdf(data, force_ocr=False, dpi=OCR_DPI, max_pages=OCR_MAX_PAGES,
             progress=None):
    """
    Read a PDF, page by page, choosing text extraction or OCR per page.

    Returns {'pages': [...], 'method': 'text'|'ocr'|'mixed', 'grid': [...],
             'tables': [...], 'text': str, 'confidence': float|None}
    """
    pages = []
    used_text = used_ocr = 0
    tables = []

    plumber_pages = []
    if not force_ocr:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for idx, page in enumerate(pdf.pages):
                    if idx >= max_pages:
                        break
                    text = page.extract_text() or ""
                    found, words = [], []
                    if len(text) >= TEXT_PAGE_MIN_CHARS:
                        for tbl in (page.extract_tables() or []):
                            cleaned = _trim_grid([[(c or "").strip() for c in row]
                                                  for row in tbl])
                            if len(cleaned) > 1:
                                found.append(cleaned)
                        words = _word_boxes(page)
                    plumber_pages.append({"text": text, "tables": found,
                                          "words": words})
        except Exception:                             # noqa: BLE001
            plumber_pages = []

    page_count = _pdf_page_count(data)
    total = min(page_count or len(plumber_pages) or 1, max_pages)

    for idx in range(total):
        pg = (plumber_pages[idx] if idx < len(plumber_pages)
              else {"text": "", "tables": [], "words": []})
        if not force_ocr and len(pg["text"]) >= TEXT_PAGE_MIN_CHARS:
            lines = group_lines(pg.get("words") or [])
            grid = (pg["tables"][0] if pg["tables"]
                    else grid_from_lines(lines) if lines
                    else _grid_from_lines(pg["text"]))
            pages.append({"page": idx + 1, "method": "text", "text": pg["text"],
                          "grid": grid, "tables": pg["tables"], "confidence": None,
                          "_lines": lines})
            tables.extend(pg["tables"])
            used_text += 1
        else:
            if progress:
                progress(idx + 1, total)
            ocr = ocr_page_image(_render_pdf_page(data, idx, dpi))
            pages.append({"page": idx + 1, "method": "ocr", **ocr})
            if ocr["grid"]:
                tables.append(ocr["grid"])
            used_ocr += 1

    # Every page of one statement has the same columns, so cut them all on the
    # same edges. Learned page by page, a header block that yields one extra
    # candidate edge gives that page an extra column, and stacking the pages
    # then shifts every figure sideways from there on.
    if used_ocr > 1 or _unstackable(pages):
        # A text page carries its own heading, and the columns can sit a few
        # points apart on each page, so each is cut on its own heading. A
        # recognised page has no dependable heading, so those share one layout
        # learned from every page at once.
        shared = learn_edges([ln for p in pages if p.get("_lines")
                              and p["method"] != "text" for ln in p["_lines"]])
        rebuilt = []
        for p in pages:
            if not p.get("_lines"):
                continue
            own = header_spans(p["_lines"]) if p["method"] == "text" else None
            if own and len(own) >= 2:
                grid = grid_from_lines(p["_lines"], spans=own, assign="center")
            elif len(shared) >= 2 and p["method"] != "text":
                grid = grid_from_lines(p["_lines"], edges=shared)
            else:
                continue
            if grid:
                p["grid"] = grid
                rebuilt.append(grid)
        if rebuilt:
            tables = rebuilt
    for p in pages:
        p.pop("_lines", None)             # boxes are not part of the result

    method = "text" if used_ocr == 0 else ("ocr" if used_text == 0 else "mixed")
    confs = [p["confidence"] for p in pages if p.get("confidence") is not None]
    return {
        "pages": pages,
        "page_count": page_count,
        "method": method,
        "text": "\n".join(p["text"] for p in pages),
        "tables": tables,
        "grid": _merge_grids([p["grid"] for p in pages if p.get("grid")]),
        "confidence": round(sum(confs) / len(confs), 3) if confs else None,
    }


def _word_boxes(page):
    """Word positions on a text page, in the shape the OCR path produces.

    This lets a text PDF be cut into columns by the same code that cuts a
    scanned one, which is the only reliable way to read a ledger printed
    without ruling lines.
    """
    boxes = []
    for w in page.extract_words(use_text_flow=False, keep_blank_chars=False):
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        x0, x1, y0, y1 = (float(w["x0"]), float(w["x1"]),
                          float(w["top"]), float(w["bottom"]))
        boxes.append({"text": text, "conf": 1.0, "x0": x0, "x1": x1,
                      "y0": y0, "y1": y1, "cy": (y0 + y1) / 2.0, "h": y1 - y0})
    return boxes


def _unstackable(pages):
    """Can the per-page tables be stacked into one grid as they are?

    Two defects say no, and both corrupt every figure below them. A cell
    holding a newline means two visual rows were crammed into one, so a date
    and the row beneath it end up in the same cell. Pages of differing width
    mean each page was cut on its own columns, so stacking them slides the
    figures sideways - a Tally ledger whose first page splits the date into
    "1-Apr-" and "21" reads one column wider than the page that follows it.
    """
    widths = set()
    for p in pages:
        grid = p.get("grid") or []
        if not grid or not p.get("_lines"):
            continue
        if any("\n" in str(c) for row in grid for c in row):
            return True
        widths.add(max(len(r) for r in grid))
    return len(widths) > 1


def _pdf_page_count(data):
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:                                 # noqa: BLE001
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(data)
            n = len(doc)
            doc.close()
            return n
        except Exception:                             # noqa: BLE001
            return 0


def _render_pdf_page(data, index, dpi=OCR_DPI):
    """Rasterise one page to a PIL image, with no external binary."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        page = doc[index]
        # A drawing-sized page at 200 dpi would be tens of megapixels.
        bitmap = page.render(scale=min(dpi / 72.0, OCR_RENDER_MAX_SIDE / max(max(page.get_size()), 1)))
        return bitmap.to_pil()
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# OCR and table reconstruction
# ---------------------------------------------------------------------------

def ocr_page_image(image, edges=None):
    """Recognise one page image and rebuild its rows and columns.

    `edges` lets the caller impose one column layout on every page of a
    multi-page document; see grid_from_lines.
    """
    import numpy as np
    engine = get_ocr()
    arr = np.array(image.convert("RGB"))
    result, _ = engine(arr)

    boxes = []
    for item in (result or []):
        if len(item) < 2:
            continue
        box, text = item[0], item[1]
        conf = item[2] if len(item) > 2 else 1.0
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 1.0
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        boxes.append({
            "text": str(text).strip(),
            "conf": conf,
            "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
            "cy": (min(ys) + max(ys)) / 2.0,
            "h": max(ys) - min(ys),
        })

    lines = group_lines(boxes)
    grid = grid_from_lines(lines, edges=edges)
    text = "\n".join(" ".join(b["text"] for b in line) for line in lines)
    confs = [b["conf"] for b in boxes]
    return {
        "text": text,
        "grid": grid,
        "boxes": boxes,
        "lines": [[b["text"] for b in line] for line in lines],
        "_lines": lines,          # boxes, for document-wide edge learning
        "confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "low_confidence": [b["text"] for b in boxes if b["conf"] < 0.62][:20],
    }


def group_lines(boxes, tol_ratio=0.62):
    """Cluster recognised boxes into visual rows by vertical overlap."""
    if not boxes:
        return []
    heights = sorted(b["h"] for b in boxes if b["h"] > 0)
    median_h = heights[len(heights) // 2] if heights else 10.0
    tol = max(median_h * tol_ratio, 4.0)

    ordered = sorted(boxes, key=lambda b: (b["cy"], b["x0"]))
    lines, current, anchor = [], [], None
    for b in ordered:
        if anchor is None or abs(b["cy"] - anchor) <= tol:
            current.append(b)
            anchor = anchor if anchor is not None else b["cy"]
            anchor = sum(x["cy"] for x in current) / len(current)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current, anchor = [b], b["cy"]
    if current:
        lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


def learn_edges(lines, min_cols=3):
    """Where the columns of a page start.

    Every box start is a candidate; starts within a merge gap of each other
    are one column. Returns the cluster medians rather than the first start in
    each run, so one ragged row cannot drag a column edge sideways.
    """
    if not lines:
        return []
    wide = [ln for ln in lines if len(ln) >= min_cols]
    source = wide if len(wide) >= 2 else lines
    starts = sorted(b["x0"] for ln in source for b in ln)
    if not starts:
        return []

    widths = sorted(b["x1"] - b["x0"] for ln in source for b in ln)
    median_w = widths[len(widths) // 2] if widths else 40.0
    merge_gap = max(median_w * 0.45, 12.0)

    clusters = [[starts[0]]]
    for s in starts[1:]:
        if s - clusters[-1][-1] > merge_gap:
            clusters.append([s])
        else:
            clusters[-1].append(s)
    return [c[len(c) // 2] for c in clusters]


def grid_from_lines(lines, min_cols=3, edges=None, assign="start", trim_cols=None,
                    spans=None):
    """
    Turn visual rows into a rectangular grid.

    `edges` may be supplied so that every page of one document is cut on the
    same columns. Without that, a page whose header block happens to produce
    one extra candidate edge ends up with a different number of columns from
    its neighbours, and stacking the pages shifts every figure one column
    sideways - a withdrawal becomes a reference and a balance becomes a
    deposit, which is how a statement silently stops tying.

    `assign="center"` places a word by its middle rather than its left edge,
    which is what a right-aligned money column needs: a long figure starts
    far to the left of its own heading and would otherwise be filed under the
    column before it. Columns are kept when the edges were handed in, since
    dropping the ones that happen to be empty on this page would undo the
    shared layout the caller asked for.
    """
    if not lines:
        return []
    if spans:
        edges = [s for s, _ in spans]
    if trim_cols is None:
        trim_cols = edges is None
    if edges is None:
        edges = learn_edges(lines, min_cols)
    if len(edges) < 2:
        return [[" ".join(b["text"] for b in ln)] for ln in lines]

    if spans:
        # Halfway across the white space between one heading and the next.
        # Halving the distance between their starts instead puts the boundary
        # inside the wider heading's own text, and a long word in that column
        # tips over it.
        bounds = [(spans[i][1] + spans[i + 1][0]) / 2.0 for i in range(len(spans) - 1)]
    else:
        bounds = [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(edges) - 1)]

    grid = []
    for ln in lines:
        row = [""] * len(edges)
        for b in ln:
            if assign == "center":
                mid = (b["x0"] + b["x1"]) / 2.0
                col = sum(1 for x in bounds if mid >= x)
            else:
                col = min(range(len(edges)), key=lambda i: abs(edges[i] - b["x0"]))
            row[col] = (row[col] + " " + b["text"]).strip() if row[col] else b["text"]
        grid.append(row)
    return _trim_grid(grid, cols=trim_cols)


_HEADING_WORD = re.compile(
    r"^(date|dt|particulars?|description|narration|details|remarks?|nature|"
    r"vch|voucher|type|no|number|#|ref|reference|inv|invoice|bill|doc|document|"
    r"debit|credit|dr|cr|amount|amt|balance|bal|days|product|gross|total|"
    r"withdrawal|withdrawals|deposit|deposits|chq|cheque|value|instrument|"
    r"gstin|uin|desc)$", re.I)


def header_spans(lines, max_scan=40, min_hits=3):
    """Where each column starts and ends, read off the table's own heading row.

    Learning the columns from the body instead splits a two-word cell into
    two columns - "SALES GST" becomes "SALES" and "GST" - because nothing in
    the body says where one column ends. The heading row does say, and a
    ledger always has one.
    """
    for ln in lines[:max_scan]:
        words = [b for b in ln if b["text"].strip()]
        if len(words) < min_hits:
            continue
        hits = sum(1 for b in words
                   if _HEADING_WORD.match(b["text"].strip().strip(".:")))
        if hits >= min_hits and hits >= len(words) * 0.7:
            return _phrase_spans(words)
    return None


def _phrase_spans(words, gap_ratio=0.9):
    """Extent of each heading phrase: "Vch" and "Type" are one column."""
    heights = sorted(b["h"] for b in words if b["h"] > 0)
    median_h = heights[len(heights) // 2] if heights else 10.0
    gap = max(median_h * gap_ratio, 6.0)
    spans = [[words[0]["x0"], words[0]["x1"]]]
    for prev, b in zip(words, words[1:]):
        if b["x0"] - prev["x1"] > gap:
            spans.append([b["x0"], b["x1"]])
        else:
            spans[-1][1] = b["x1"]
    return [tuple(s) for s in spans]


# ---------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------

def _trim_grid(rows, cols=True):
    """Drop fully empty leading/trailing rows, and empty columns when asked."""
    rows = [[("" if c is None else c) for c in r] for r in rows]
    while rows and not any(str(c).strip() for c in rows[0]):
        rows.pop(0)
    while rows and not any(str(c).strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if not cols:
        return rows
    keep = [i for i in range(width) if any(str(r[i]).strip() for r in rows)]
    if not keep:
        return []
    return [[r[i] for i in keep] for r in rows]


def _merge_grids(grids):
    """Stack page grids, dropping a repeated header on later pages."""
    grids = [g for g in grids if g]
    if not grids:
        return []
    out = list(grids[0])
    first_header = _norm_row(grids[0][0]) if grids[0] else None
    for g in grids[1:]:
        rows = g
        if first_header and rows and _norm_row(rows[0]) == first_header:
            rows = rows[1:]
        out.extend(rows)
    return out


def _norm_row(row):
    return tuple(re.sub(r"\s+", " ", str(c)).strip().lower() for c in row)


def _grid_from_lines(text):
    """Split plain text into columns on runs of two or more spaces."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in re.split(r"\s{2,}|\t", line.strip())]
        rows.append(cells)
    return _trim_grid(rows)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _enlarge_for_ocr(img, target=1500):
    """Bring a screenshot up to a size the recogniser reads properly.

    A screenshot of a statement arrives at screen resolution, where a line of
    text is about ten pixels tall. At that size the recogniser runs words
    together and drops letters - "OMEGATRADNGCOMPANY" for "OMEGA TRADING
    COMPANY", "16/04/24HDFCRTGS" for a date and a narration. Doubling it fixed
    both; tripling started misreading again, hence the cap.
    """
    from PIL import Image
    long_side = max(img.size)
    if long_side >= target:
        return img
    scale = min(3.0, target / long_side)
    return img.convert("RGB").resize(
        (round(img.width * scale), round(img.height * scale)), Image.LANCZOS)


def read_document(data, filename="", force_ocr=False, sheet=None, progress=None,
                  max_pages=OCR_MAX_PAGES):
    """
    Read anything into a grid.

    Returns {'kind', 'grid', 'grids', 'text', 'method', 'confidence',
             'pages', 'notes'}. `grids` is populated for workbooks, which can
    carry several candidate sheets; `grid` is always the best single guess.
    """
    if isinstance(data, str):
        data = Path(data).read_bytes()
    kind = sniff(data, filename)
    notes = []

    if kind == "excel":
        grids = read_excel_grid(data, sheet=sheet)
        best = max(grids.items(), key=lambda kv: len(kv[1]), default=(None, []))
        return {"kind": "excel", "grid": best[1], "grids": grids,
                "sheet": best[0], "text": "", "method": "spreadsheet",
                "confidence": None, "pages": 1, "notes": notes}

    if kind == "csv":
        grid = read_csv_grid(data)
        return {"kind": "csv", "grid": grid, "grids": {"csv": grid}, "sheet": "csv",
                "text": "", "method": "delimited", "confidence": None,
                "pages": 1, "notes": notes}

    if kind == "pdf":
        if force_ocr and not ocr_available():
            raise ValueError("OCR was requested but is not installed on this machine.")
        out = read_pdf(data, force_ocr=force_ocr, progress=progress, max_pages=max_pages)
        if out["method"] in ("ocr", "mixed"):
            notes.append("Some pages were read by recognising the image, so figures "
                         "should be spot-checked against the original.")
        if out["method"] == "text" and not out["tables"]:
            notes.append("No ruled table was found in the PDF; columns were inferred "
                         "from the spacing of the text.")
        return {"kind": "pdf", "grid": out["grid"],
                "grids": {f"page {p['page']}": p["grid"] for p in out["pages"] if p["grid"]},
                "sheet": None, "text": out["text"], "method": out["method"],
                "confidence": out["confidence"], "pages": out["page_count"],
                "notes": notes, "page_detail": [
                    {"page": p["page"], "method": p["method"],
                     "confidence": p.get("confidence"), "rows": len(p.get("grid") or [])}
                    for p in out["pages"]]}

    if kind == "image":
        if not ocr_available():
            raise ValueError(
                "That is an image, so it has to be read by recognition, and OCR is "
                "not installed. Run: python -m pip install rapidocr-onnxruntime"
            )
        from PIL import Image
        img = _enlarge_for_ocr(Image.open(io.BytesIO(data)))
        out = ocr_page_image(img)
        notes.append("Read by recognising the image. Spot-check the figures.")
        if out.get("low_confidence"):
            notes.append("Low confidence on: " + ", ".join(out["low_confidence"][:6]))
        return {"kind": "image", "grid": out["grid"], "grids": {"page 1": out["grid"]},
                "sheet": None, "text": out["text"], "method": "ocr",
                "confidence": out["confidence"], "pages": 1, "notes": notes}

    if kind == "xls_legacy":
        raise ValueError(
            "That is an old .xls file. Open it and use Save As to write a .xlsx, "
            "then upload that."
        )

    raise ValueError(
        "Could not tell what that file is. Supported: .xlsx, .csv, .pdf, and "
        "images (.png, .jpg, .tiff)."
    )
