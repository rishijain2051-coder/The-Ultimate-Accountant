"""Local OCR via RapidOCR (ONNX).

Chosen over Tesseract because it installs from pip with no separate .exe, which
is the difference between a one-click Windows installer and a support ticket.

Known ceiling: this is a print-text engine. Cursive handwriting will come back
poor or empty and the entry will land at low confidence for manual keying --
that is the honest behaviour, not a bug.
"""

from __future__ import annotations

import threading

_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    # Loading the ONNX models costs ~2s and ~300MB; do it once, on first scan,
    # never at import -- most invoices are digital PDFs and never touch this.
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                _limit_threads()
                _engine = RapidOCR()
    return _engine


def _limit_threads() -> None:
    """Keep the OCR to the app's thread budget. RapidOCR offers no setting,
    and ONNX Runtime by default takes a thread per core and spins them while
    waiting, which under the app's CPU cap only burns the budget."""
    from ..config import settings

    n = settings.cpu_threads
    if not n:
        return
    import cv2
    from rapidocr_onnxruntime import utils as rapid

    cv2.setNumThreads(n)
    make = rapid.SessionOptions

    def limited():
        opts = make()
        opts.intra_op_num_threads, opts.inter_op_num_threads = n, 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        return opts

    rapid.SessionOptions = limited


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def ocr_images(image_bytes: bytes) -> tuple[str, float]:
    """Return (text, min_confidence). Never raises -- a failed OCR is a
    low-confidence entry, not a crashed import."""
    if not image_bytes:
        return "", 0.0
    try:
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        result, _elapsed = _get_engine()(np.array(img))
    except Exception:  # pragma: no cover - engine/model failures
        return "", 0.0

    if not result:
        return "", 0.0

    # RapidOCR returns [box, text, score]; reading order is already top-to-bottom.
    lines = [str(r[1]) for r in result if len(r) > 1 and r[1]]
    scores = [float(r[2]) for r in result if len(r) > 2 and r[2] is not None]
    return "\n".join(lines), (min(scores) if scores else 0.0)
