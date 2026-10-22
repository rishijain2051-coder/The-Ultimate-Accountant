"""Get text out of a document.

Order matters for both speed and accuracy: a born-digital PDF already contains
perfect text, and running OCR over it would only introduce errors. OCR is the
fallback for scans, never the default path.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_PIXELS = 40_000_000  # one rendered page; a huge page size must not eat the memory
MAX_OCR_PAGES = 60  # scanned pages read per file; the rest are left unread


def fit_zoom(page, zoom: float) -> float:
    """The zoom, lowered if the page would render past MAX_PIXELS."""
    area = page.rect.width * page.rect.height * zoom * zoom
    return zoom if area <= MAX_PIXELS else zoom * (MAX_PIXELS / area) ** 0.5


@dataclass
class PageText:
    number: int
    text: str
    ocr: bool = False
    ocr_confidence: float = 1.0


@dataclass
class ExtractResult:
    pages: list[PageText] = field(default_factory=list)
    has_text_layer: bool = False
    ocr_used: bool = False

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    @property
    def ocr_confidence(self) -> float:
        scanned = [p.ocr_confidence for p in self.pages if p.ocr]
        return min(scanned) if scanned else 1.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(path: Path) -> ExtractResult:
    path = Path(path)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return _extract_image(path)
    return _extract_pdf(path)


def _extract_pdf(path: Path) -> ExtractResult:
    import pdfplumber

    res = ExtractResult()
    scan_pages: list[int] = []

    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            # layout=True keeps column alignment, which is what makes the
            # "SAC qty rate amount" tail of a line item recognisable at all.
            try:
                txt = page.extract_text(layout=True, x_tolerance=1.5) or ""
            except Exception:
                txt = page.extract_text() or ""
            res.pages.append(PageText(number=i, text=txt))
            if len(txt.strip()) < settings.ocr_char_threshold:
                scan_pages.append(i)

    res.has_text_layer = len(scan_pages) < len(res.pages)

    if scan_pages:
        from .ocr import ocr_images

        if len(scan_pages) > MAX_OCR_PAGES:
            log.warning("%s: %d scanned pages, reading the first %d", path.name, len(scan_pages), MAX_OCR_PAGES)
            scan_pages = scan_pages[:MAX_OCR_PAGES]
        for page_no, img_bytes in _render_pages(path, scan_pages):
            text, conf = ocr_images(img_bytes)
            for p in res.pages:
                if p.number == page_no:
                    # Keep whichever is richer; a near-empty text layer on a
                    # scan is usually just a stamp or a footer.
                    if len(text.strip()) > len(p.text.strip()):
                        p.text, p.ocr, p.ocr_confidence = text, True, conf
                    break
        res.ocr_used = any(p.ocr for p in res.pages)

    return res


def page_images(path: Path, max_pages: int = 3, long_side: int = 2000) -> list[bytes]:
    """The first pages as PNGs, for a model that reads the picture itself.

    Phone photos are turned upright (EXIF) and scaled down: past ~2000 px a
    vision model sees no more detail, it only gets slower."""
    p = Path(path)
    if p.suffix.lower() not in IMAGE_SUFFIXES:
        n = min(page_count(p), max_pages)
        return [png for _no, png in _render_pages(p, list(range(1, n + 1)), dpi=150)]

    import io

    from PIL import Image, ImageOps

    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((long_side, long_side))
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return [buf.getvalue()]


def _render_pages(path: Path, page_numbers: list[int], dpi: int | None = None):
    """(page number, PNG) one page at a time."""
    import pymupdf

    zoom = (dpi or settings.ocr_dpi) / 72.0
    with pymupdf.open(str(path)) as doc:
        for n in page_numbers:
            page = doc[n - 1]
            z = fit_zoom(page, zoom)
            yield n, page.get_pixmap(matrix=pymupdf.Matrix(z, z)).tobytes("png")


def _extract_image(path: Path) -> ExtractResult:
    from .ocr import ocr_images

    text, conf = ocr_images(path.read_bytes())
    return ExtractResult(
        pages=[PageText(number=1, text=text, ocr=True, ocr_confidence=conf)],
        has_text_layer=False,
        ocr_used=True,
    )


def page_count(path: Path) -> int:
    p = Path(path)
    if p.suffix.lower() in IMAGE_SUFFIXES:
        return 1
    try:
        import pymupdf

        with pymupdf.open(str(p)) as doc:
            return doc.page_count
    except Exception:
        return 0
