from __future__ import annotations

import os
import re
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import UI_DIR, settings
from app.backend.version import VERSION
from .db import init_db
from .routers import bank, documents, entries, system, tally_api
from .services import ai_check, backup, bill_links, llm, phone, watcher

DESCRIPTION = """Reads an invoice, shows you what it understood, posts it to Tally once you agree.

Runs entirely on this machine. Tally and the local model are optional; when
either is absent the app says so and keeps working with what is there.
"""


def _backup_loop() -> None:
    while True:
        time.sleep(6 * 3600)
        try:
            backup.run()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from .db import SessionLocal

    with SessionLocal() as db:
        # Settings changed from the UI must survive a restart.
        system.apply_stored(db)
    # Bills the app was closed in the middle of: read them now. Listed before
    # the watcher starts, so nothing it adds is read twice.
    waiting = documents.unfinished()
    statements = bank.unfinished()
    if settings.watch_enabled:
        watcher.start()
    bill_links.start()  # only when links are on
    threading.Thread(target=_backup_loop, name="backup", daemon=True).start()
    threading.Thread(target=llm.warm_up, name="warm-up", daemon=True).start()
    if waiting:
        threading.Thread(target=documents.read_in_order, args=(waiting,), name="resume", daemon=True).start()
    if statements:
        threading.Thread(target=bank.read_in_order, args=(statements,), name="resume-bank", daemon=True).start()
    ai_check.resume()  # and the AI's checks that were waiting
    yield
    watcher.stop()
    bill_links.stop()
    phone.stop()


app = FastAPI(title="Ultimate Accountant: Invoice Bridge", version=VERSION,
              description=DESCRIPTION, lifespan=lifespan)

# The UI runs on 3000 in dev (bridge-ui, `next dev`) and is served from the
# same origin, under /bridge, once built.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(tally_api.router)
app.include_router(documents.router)
app.include_router(entries.router)
app.include_router(bank.router)


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
    # Surface the reason instead of a bare 500 -- most failures here are
    # environmental (Tally shut, file locked) and the text is the fix.
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.get("/api")
def root() -> dict[str, str]:
    return {"name": "Ultimate Accountant: Invoice Bridge", "version": VERSION, "docs": "/bridge/docs",
            "data_dir": str(settings.data_dir)}


_PAGE_DATA = re.compile(r"(.*?)(__next\.[^/\\]+)\.(__PAGE__\.txt)")


def ui_path(path: str) -> str:
    """Next.js asks for review/__next.review.__PAGE__.txt, and its export
    writes review/__next.review/__PAGE__.txt. Unanswered, every switch to
    another page fell back to reloading the whole page."""
    m = _PAGE_DATA.fullmatch(path)
    return f"{m[1]}{m[2]}{os.sep}{m[3]}" if m else path


# Serve the built Next.js export (bridge-ui, basePath /bridge) when it exists,
# so the whole app is one process on one port.
_ui = UI_DIR
if _ui.exists():
    from fastapi.staticfiles import StaticFiles

    class _UI(StaticFiles):
        def lookup_path(self, path: str):  # type: ignore[override]
            found = super().lookup_path(path)
            return found if found[1] else super().lookup_path(ui_path(path))

    app.mount("/", _UI(directory=str(_ui), html=True), name="ui")
