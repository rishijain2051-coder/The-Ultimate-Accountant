from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Document, Entry
from ..schemas import DocumentOut
from ..services import ai_check, dedupe, pipeline
from ..services.pdf_text import fit_zoom

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_BYTES = 40 * 1024 * 1024
# A file that could not be read, or was set aside, is read again if uploaded again.
READ_AGAIN = ("failed", "dismissed")
# Bank statements are documents too, read and listed by routers/bank.py.
BILLS = or_(Document.kind.is_(None), Document.kind != "statement")


def split_pages(name: str, data: bytes) -> list[tuple[str, bytes]]:
    """One PDF per page: a scanner's batch of separate bills in one file.
    The same file always splits into the same bytes, so uploading it twice
    is still caught as a duplicate, page by page."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as src:
        if src.page_count < 2:
            return [(name, data)]
        out = []
        for i in range(src.page_count):
            with pymupdf.open() as one:
                one.insert_pdf(src, from_page=i, to_page=i)
                out.append((f"{Path(name).stem} - page {i + 1}.pdf",
                            one.tobytes(garbage=3, deflate=True, no_new_id=True)))
        return out


@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    company_id: int | None = Form(None),
    voucher_type: str | None = Form(None),
    voucher_parent: str | None = Form(None),
    use_llm: bool = Form(True),
    each_page: bool = Form(False),
    db: Session = Depends(get_db),
) -> Any:
    """Accepts one file or fifty, all as the same voucher type. Each is queued
    and extracted in the background. each_page: every page of a PDF is a bill
    of its own."""
    results: list[dict[str, Any]] = []

    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in ALLOWED:
            results.append({"filename": f.filename, "ok": False,
                            "error": f"{suffix or 'file'} is not a supported type"})
            continue

        data = await f.read()
        if not data:
            results.append({"filename": f.filename, "ok": False, "error": "file is empty"})
            continue
        if len(data) > MAX_BYTES:
            results.append({"filename": f.filename, "ok": False,
                            "error": f"file is larger than {MAX_BYTES // 1024 // 1024} MB"})
            continue

        name = f.filename or "upload.pdf"
        try:
            parts = split_pages(name, data) if each_page and suffix == ".pdf" else [(name, data)]
        except Exception as exc:  # not a PDF pymupdf can open: say so, keep the batch going
            results.append({"filename": name, "ok": False, "error": f"could not split into pages: {exc}"})
            continue

        for part_name, blob in parts:
            r = accept(db, part_name, blob, company_id, voucher_type, voucher_parent)
            if r.get("status") == "queued":
                background.add_task(_process, r["document_id"], company_id, use_llm)
            results.append(r)

    return {"results": results,
            "queued": sum(1 for r in results if r.get("status") == "queued")}


def accept(db: Session, name: str, blob: bytes, company_id: int | None, voucher_type: str | None,
           voucher_parent: str | None, source: str = "upload") -> dict[str, Any]:
    """Keep one file and queue it for reading, unless this exact file is here
    already. A "queued" result is for _process to read; the upload screen and
    the phone (services/phone.py) both come through here."""
    path, digest = pipeline.store_upload(name, blob)

    prior = dedupe.check_file(db, digest)
    if prior and prior.status not in READ_AGAIN:
        entry = db.scalars(select(Entry).where(Entry.document_id == prior.id)).first()
        return {"filename": name, "ok": True, "duplicate_file": True,
                "document_id": prior.id, "entry_id": entry.id if entry else None,
                "message": f"This exact file was already uploaded as '{prior.filename}'."}

    doc = prior or Document(sha256=digest, size_bytes=len(blob), source=source)
    doc.filename, doc.stored_path = name, str(path)  # the file may have gone missing
    doc.company_id, doc.status, doc.error = company_id, "queued", None
    doc.requested_voucher_type = voucher_type or None
    doc.requested_voucher_parent = voucher_parent or None
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"filename": name, "ok": True, "document_id": doc.id,
            "status": "queued", "message": "Queued for extraction."}


def _process(document_id: int, company_id: int | None, use_llm: bool) -> None:
    """Runs off the request. Its own session: the request's is already closed.

    One bill at a time, and the turn is waited for before the session opens:
    ten uploads waiting their turn each holding a database connection ran
    the pool dry, until even the status check failed. The AI's slower
    reading is not in this queue: it follows in its own (services/ai_check)."""
    with pipeline.ONE_BILL:
        _process_now(document_id, company_id, use_llm)


def _process_now(document_id: int, company_id: int | None, use_llm: bool) -> None:
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if doc and doc.kind != "statement" and doc.status in ("queued", "processing"):  # never read one twice
            ai_check.enqueue(pipeline.process_document(db, doc, company_id=company_id, use_llm=use_llm))
    except Exception as exc:
        db.rollback()
        try:
            doc = db.get(Document, document_id)
            if doc:
                doc.status, doc.error = "failed", str(exc)[:2000]
                db.commit()
        except Exception:
            # Not even that could be saved: it stays waiting, and the next start
            # reads it again. The rest of the batch carries on either way.
            db.rollback()
    finally:
        db.close()


@router.get("", response_model=list[DocumentOut])
def list_documents(status: str | None = None, limit: int = Query(100, le=1000),
                   db: Session = Depends(get_db)) -> Any:
    stmt = select(Document).where(BILLS).order_by(desc(Document.id)).limit(limit)
    if status:
        stmt = stmt.where(Document.status == status)
    return list(db.scalars(stmt))


@router.get("/status")
def batch_status(ids: str, db: Session = Depends(get_db)) -> Any:
    """Where each document of an upload has got to, in one request: a batch
    of fifty polls once, not fifty times. (Declared before /{document_id}.)"""
    want = [int(i) for i in ids.split(",") if i.strip().isdigit()][:1000]
    entry_of = dict(db.execute(select(Entry.document_id, Entry.id).where(Entry.document_id.in_(want))).all())
    return [{"id": d.id, "status": d.status, "error": d.error, "filename": d.filename,
             "entry_id": entry_of.get(d.id)}
            for d in db.scalars(select(Document).where(Document.id.in_(want)))]


@router.get("/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db)) -> Any:
    """Status poll used right after upload: returns the entry id once ready."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    entry = db.scalars(select(Entry).where(Entry.document_id == doc.id)).first()
    return {"id": doc.id, "status": doc.status, "error": doc.error, "filename": doc.filename,
            "entry_id": entry.id if entry else None}


@router.get("/{document_id}/file")
def get_file(document_id: int, db: Session = Depends(get_db)) -> Any:
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(410, "The stored file is missing from data/uploads.")
    media = "application/pdf" if path.suffix.lower() == ".pdf" else None
    # inline, so the review screen can show it beside the extracted fields
    return FileResponse(path, media_type=media, filename=doc.filename,
                        content_disposition_type="inline")


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
PAGE_DPI = 144  # sharp on a normal screen, ~150 KB a page


def _stored(doc: Document) -> Path:
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(410, "The stored file is missing from data/uploads.")
    return path


@router.get("/{document_id}/pages")
def page_count(document_id: int, db: Session = Depends(get_db)) -> Any:
    """How many page images the review screen should ask for."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    path = _stored(doc)
    if path.suffix.lower() in IMAGE_TYPES:
        return {"count": 1, "kind": "image"}
    import pymupdf

    with pymupdf.open(str(path)) as pdf:
        return {"count": pdf.page_count, "kind": "pdf"}


@router.get("/{document_id}/page/{page_no}")
def page_image(document_id: int, page_no: int, db: Session = Depends(get_db)) -> Any:
    """One page as a PNG.

    Rendered here rather than handed to the browser's PDF plugin, which some
    browsers and managed office machines disable or replace with a download --
    and the side-by-side view is the whole point of the review screen.
    Cached beside the upload, so each page is rendered once.
    """
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    path = _stored(doc)
    if path.suffix.lower() in IMAGE_TYPES:
        return FileResponse(path, media_type=IMAGE_TYPES[path.suffix.lower()])

    cached = path.with_name(f"{path.stem}.p{page_no}.png")
    if not cached.exists():
        import pymupdf

        with pymupdf.open(str(path)) as pdf:
            if not 1 <= page_no <= pdf.page_count:
                raise HTTPException(404, f"This document has {pdf.page_count} page(s).")
            page = pdf[page_no - 1]
            zoom = fit_zoom(page, PAGE_DPI / 72.0)
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(str(cached))
    return FileResponse(cached, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{document_id}/text")
def get_text(document_id: int, db: Session = Depends(get_db)) -> Any:
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return {"document_id": doc.id, "has_text_layer": doc.has_text_layer,
            "ocr_used": doc.ocr_used, "text": doc.extracted_text or ""}


@router.post("/{document_id}/reprocess")
def reprocess(document_id: int, background: BackgroundTasks, use_llm: bool = True,
              voucher_type: str | None = None, db: Session = Depends(get_db)) -> Any:
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    posted = db.scalars(
        select(Entry).where(Entry.document_id == doc.id, Entry.status.in_(("posted", "drafted")))
    ).first()
    if posted:
        raise HTTPException(409, f"Entry #{posted.id} from this document is already in Tally. "
                                 f"Delete that voucher in Tally before re-extracting.")
    if voucher_type:
        doc.requested_voucher_type = voucher_type
    for e in db.scalars(select(Entry).where(Entry.document_id == doc.id)):
        db.delete(e)
    doc.status, doc.error = "queued", None
    db.commit()
    background.add_task(_process, doc.id, doc.company_id, use_llm)
    return {"document_id": doc.id, "status": "queued", "message": "Re-extracting."}


@router.post("/{document_id}/dismiss")
def dismiss(document_id: int, db: Session = Depends(get_db)) -> Any:
    """Take a file that could not be read off the review screen. Uploading
    it again reads it again."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status != "failed":
        raise HTTPException(409, "Only a file that could not be read can be set aside.")
    doc.status = "dismissed"
    db.commit()
    return {"document_id": doc.id, "status": doc.status}


def unfinished() -> list[tuple[int, int | None]]:
    """Bills still waiting when the app was last closed, in upload order."""
    with SessionLocal() as db:
        return [(d.id, d.company_id) for d in db.scalars(
            select(Document).where(BILLS, Document.status.in_(("queued", "processing"))).order_by(Document.id))]


def read_in_order(waiting: list[tuple[int, int | None]]) -> None:
    for doc_id, company_id in waiting:
        _process(doc_id, company_id, True)
