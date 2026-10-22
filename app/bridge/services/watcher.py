"""Watched folder: drop invoices in, they queue themselves.

A polling loop, not a filesystem-event library. Polling is a few lines, has no
dependency, and survives the cases that break watchers in practice -- network
shares, OneDrive folders, and scanners that create a file and then write into
it for the next ten seconds.

Files dropped here have no voucher type chosen, so they are inferred
(Purchase or Sales, from which GSTIN is yours) and can be changed on review.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..config import settings
from ..db import SessionLocal
from ..models import Document
from . import ai_check, dedupe, pipeline

SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_stop = threading.Event()
_thread: threading.Thread | None = None
_sizes: dict[str, int] = {}


def _settled(path: Path) -> bool:
    """True once the file stops growing -- a scanner's half-written PDF
    parses as a corrupt file and creates a junk entry."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    key = str(path)
    was = _sizes.get(key)
    _sizes[key] = size
    return was is not None and was == size and size > 0


def scan_once() -> list[int]:
    """Process anything new. Returns the document ids created."""
    created: list[int] = []
    folder = settings.watch_dir
    if not folder.exists():
        return created

    db = SessionLocal()
    try:
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            if not _settled(path):
                continue
            try:
                stored, digest = pipeline.adopt_file(path)
                if dedupe.check_file(db, digest):
                    _archive(path, "duplicate")
                    continue

                doc = Document(filename=path.name, stored_path=str(stored), sha256=digest,
                               size_bytes=path.stat().st_size, source="watch")
                db.add(doc)
                db.commit()
                db.refresh(doc)
                with pipeline.ONE_BILL:  # the same one-at-a-time queue as uploads
                    ai_check.enqueue(pipeline.process_document(db, doc))
                created.append(doc.id)
                _archive(path, "done")
            except Exception as exc:
                db.rollback()
                _archive(path, "failed", note=str(exc))
            finally:
                _sizes.pop(str(path), None)
    finally:
        db.close()
    return created


def _archive(path: Path, outcome: str, note: str | None = None) -> None:
    """Move the original out of the watch folder so it is not seen twice."""
    dest_dir = settings.watch_dir / f"_{outcome}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}_{i}{path.suffix}"
        i += 1
    try:
        path.rename(dest)
    except OSError:
        return
    if note:
        dest.with_suffix(dest.suffix + ".error.txt").write_text(note[:4000], encoding="utf-8")


def _loop() -> None:
    while not _stop.is_set():
        if settings.watch_enabled:
            try:
                scan_once()
            except Exception:
                pass  # a bad file must never kill the watcher
        _stop.wait(settings.watch_interval_seconds)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="watch-folder", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
