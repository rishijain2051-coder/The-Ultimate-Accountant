from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.office import settings as office_settings

from ..config import settings
from ..db import get_db
from ..models import Company, Document, Entry, Ledger, Setting
from ..schemas import HealthOut
from ..services import ai_setup, backup, bill_links, llm, ocr, phone, tally

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> Any:
    from concurrent.futures import ThreadPoolExecutor

    # Both probes spend most of their time waiting on a socket; run them side
    # by side so a closed Tally and a stopped Ollama cost one wait, not two.
    with ThreadPoolExecutor(max_workers=2) as pool:
        tally_f = pool.submit(tally.ping)
        ollama_f = pool.submit(llm.status)
        tally_status, ollama_status = tally_f.result(), ollama_f.result()

    # Not installed, and a button can install it: say so, not a command.
    setup = ai_setup.status()
    if setup["can_install"] and not ollama_status.get("available"):
        ollama_status = {**ollama_status, "reason": "The AI reader is not installed on this computer: "
                                                    "Settings > Local AI model installs it."}

    counts = {
        "companies": db.scalar(select(func.count(Company.id))) or 0,
        "ledgers": db.scalar(select(func.count(Ledger.id))) or 0,
        "documents": db.scalar(select(func.count(Document.id))) or 0,
        "entries": db.scalar(select(func.count(Entry.id))) or 0,
        "pending": db.scalar(select(func.count(Entry.id)).where(Entry.status == "pending")) or 0,
        "posted": db.scalar(select(func.count(Entry.id)).where(Entry.status == "posted")) or 0,
    }
    return HealthOut(
        database={
            "engine": "sqlite" if settings.is_sqlite else "postgresql",
            "backups": backup.list_backups()[:5],
        },
        tally={**tally_status, "allow_post": settings.tally_allow_post},
        ollama={**ollama_status, "setup": setup},
        ocr={"engine": "rapidocr-onnxruntime", "available": ocr.available()},
        watch={"enabled": settings.watch_enabled, "folder": str(settings.watch_dir)},
        counts=counts,
    )


def _stored(db: Session) -> dict[str, str]:
    return {s.key: s.value for s in db.scalars(select(Setting)) if s.value is not None}


def _bool(v: Any) -> bool:
    return str(v).lower() in ("true", "1", "yes", "on")


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> Any:
    return {
        "tally_host": settings.tally_host,
        "tally_port": settings.tally_port,
        "tally_allow_post": settings.tally_allow_post,
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "llm_enabled": settings.llm_enabled,
        "auto_clean_confidence": settings.auto_clean_confidence,
        "watch_enabled": settings.watch_enabled,
        "watch_dir": str(settings.watch_dir),
        "data_dir": str(settings.data_dir),
        "bill_links": settings.bill_links,
        "bill_link_base": f"http://{bill_links.address()}:{settings.bill_link_port}/bill/",
        "bill_links_problem": bill_links.start() if settings.bill_links else None,
    }


_LIVE: dict[str, type] = {
    "tally_host": str, "tally_port": int, "tally_allow_post": bool,
    "ollama_host": str, "ollama_model": str, "llm_enabled": bool,
    "auto_clean_confidence": float, "watch_enabled": bool, "bill_links": bool,
}


@router.get("/bill-links/addon")
def bill_link_addon() -> Any:
    """The Tally add-on (TDL) that shows each bill's link and opens it."""
    from fastapi.responses import Response

    return Response(bill_links.ADDON, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="InvoiceBridge-BillLinks.tdl"'})


def apply_stored(db: Session) -> None:
    """Called at startup: settings saved from the UI override .env defaults."""
    for key, value in _stored(db).items():
        caster = _LIVE.get(key)
        if caster is None:
            continue
        try:
            setattr(settings, key, _bool(value) if caster is bool else caster(value))
        except (TypeError, ValueError):
            pass


# Where Tally and the AI are, and the bill links: on the office server only an admin moves them.
_WHERE = ("tally_host", "tally_port", "tally_allow_post", "ollama_host", "ollama_model", "bill_links")


_ADMIN_DO = "Only an admin can do this on the office server."


def _admin_only(request: Request, msg: str = "Only an admin can change where Tally and the AI are, on the office server.") -> None:
    if office_settings.mode() != "server":
        return
    person = getattr(getattr(request, "state", None), "office_person", None)
    if not person or "admin" not in person["caps"]:
        raise HTTPException(403, msg)


@router.put("/settings")
def put_settings(payload: dict[str, Any], request: Request = None, db: Session = Depends(get_db)) -> Any:
    if any(k in payload for k in _WHERE):
        _admin_only(request)
    stored = _stored(db)
    for key, value in payload.items():
        caster = _LIVE.get(key)
        if caster is None:
            continue
        try:
            cast = _bool(value) if caster is bool else caster(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} is not a valid {caster.__name__}")
        # Store only what was changed. The page sends every field; storing an
        # untouched one would pin it forever -- e.g. the model a newer version
        # of the app ships with would never be picked up.
        if key not in stored and cast == getattr(settings, key):
            continue
        db.merge(Setting(key=key, value=str(cast)))
        # Apply immediately -- a Tally port you have to restart the app to
        # change is a setting nobody finds.
        setattr(settings, key, cast)
    db.commit()
    if not settings.bill_links:
        bill_links.stop()  # on again: get_settings starts it
    return get_settings(db)


@router.post("/backup")
def make_backup(request: Request) -> Any:
    _admin_only(request, _ADMIN_DO)
    try:
        return backup.run()
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/backups")
def list_backups(request: Request) -> Any:
    _admin_only(request, _ADMIN_DO)
    return backup.list_backups()


# Bills sent from a phone on the same Wi-Fi (services/phone.py). Asked by the
# upload screen on this computer only: this app listens on 127.0.0.1.
@router.get("/phone")
def phone_status() -> Any:
    return phone.status()


@router.post("/phone/start")
def phone_start(request: Request) -> Any:
    """A new code for the phone; the one before stops working."""
    _admin_only(request)
    return phone.start()


@router.post("/phone/stop")
def phone_stop() -> Any:
    return phone.stop()


# The AI reader: the app's one AI (app/backend/ai.py), fetched when asked for (services/ai_setup.py).
@router.get("/ai/setup")
def ai_setup_status() -> Any:
    return ai_setup.status()


@router.post("/ai/install")
def ai_install(request: Request) -> Any:
    """Download the AI reader, once, for the whole app."""
    _admin_only(request, _ADMIN_DO)
    return ai_setup.start()
