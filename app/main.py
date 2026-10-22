"""
Ultimate Accountant: one server, one port, both modules.

    /          the choice of module (app/home.html)
    /console/  Assurance Console: reconciliation and exports (app/backend, app/frontend; its API at /api)
    /bridge/   Invoice Bridge: bills and bank statements into Tally (app/bridge, bridge-ui)

Each module keeps its own API, database and settings; this only mounts them
side by side and runs their start-up and shut-down work. A mounted app's own
start-up never runs by itself, so it is entered here, the bridge's inside the
console's.
"""

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.backend import ai_check
from app.backend.server import app as console
from app.bridge.main import app as bridge
from app.keeper import sync as keeper_sync
from app.office import gate, settings as office_settings, setup as office_setup


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    if office_settings.mode() == "server":      # the roster and the Keeper's data keys, every minute
        threading.Thread(target=office_setup.background, args=(stop,), daemon=True, name="office").start()
        threading.Thread(target=keeper_sync.background, args=(stop,), daemon=True, name="keeper").start()
    async with console.router.lifespan_context(console), bridge.router.lifespan_context(bridge):
        yield
    stop.set()


app = FastAPI(title="Ultimate Accountant", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _bills_hold_the_checker_back(request, call_next):
    """Work in the bridge holds the AI checker back too, as the console's own requests do
    (app/backend/server.py): the bills being read use the same AI."""
    if request.method != "GET" and request.url.path.startswith("/bridge/"):
        ai_check.busy()
    return await call_next(request)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(Path(__file__).with_name("home.html"), headers={"Cache-Control": "no-store"})


gate.install(app)        # the office server's sign-in gate; nothing at all in "alone" mode
if office_settings.mode() != "server":          # outermost: only this PC's own pages may talk to it
    app.middleware("http")(gate.local_only)
app.add_middleware(gate.upload_limit)           # outermost, every mode: no upload over 200 MB
if office_settings.mode() == "server":
    app.include_router(keeper_sync.router)      # the Keeper's page: admins only, by the gate
app.mount("/bridge", bridge)
app.mount("/", console)
