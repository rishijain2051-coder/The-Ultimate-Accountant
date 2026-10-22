"""Run the control plane on this PC, pages and all, as Vercel would serve it:

    .venv\\Scripts\\python.exe -m uvicorn dev:app --port 8000

It reads %USERPROFILE%\\.ultimate-accountant\\office.env (the real Supabase and Google
client), with PUBLIC_ORIGIN set to http://localhost:8000, the redirect registered for this,
and the schema office_dev instead of office.
Vercel never sees this file (.vercelignore): there, public/ is served by the CDN.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".ultimate-accountant" / "office.env")
os.environ["PUBLIC_ORIGIN"] = "http://localhost:8000"
# A schema of its own on this PC, so trying things here never touches the live "office" one.
# Set it up once with:  set CP_SCHEMA=office_dev && .venv\Scripts\python.exe -m cp.migrate --admin YOU@example.com
os.environ.setdefault("CP_SCHEMA", "office_dev")

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from main import app  # noqa: E402

PUBLIC = Path(__file__).with_name("public")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(PUBLIC / "admin.html")


@app.get("/books", include_in_schema=False)
def books_page():
    return FileResponse(PUBLIC / "books.html")


@app.get("/link", include_in_schema=False)
def link_page():
    return FileResponse(PUBLIC / "link.html")


app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
