"""SQLite backups.

`VACUUM INTO` makes a consistent, compacted copy while the app keeps running --
no stopping the server, no partial WAL, no pg_dump. Restoring is: stop the app,
copy the file over data/tally.db, start it.

On PostgreSQL this is a no-op and says so; a Postgres deployment should use
that server's own backup tooling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from ..config import settings
from ..db import engine


def run() -> dict[str, Any]:
    if not settings.is_sqlite:
        return {"ok": False, "skipped": True,
                "message": "DATABASE_URL points at PostgreSQL - use pg_dump or your server's backup schedule."}

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.backup_dir / f"tally-{datetime.now():%Y%m%d-%H%M%S}.db"
    with engine.connect() as conn:
        # Parameters are not allowed in VACUUM INTO; the path is ours, not
        # user input.
        conn.execute(text(f"VACUUM INTO '{dest.as_posix()}'"))

    pruned = _prune()
    return {
        "ok": True,
        "file": str(dest),
        "size_bytes": dest.stat().st_size,
        "pruned": pruned,
        "message": f"Backed up to {dest.name}. Restore by copying it over data/tally.db with the app stopped.",
    }


def _prune() -> list[str]:
    files = sorted(settings.backup_dir.glob("tally-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in files[settings.backup_keep:]:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            pass
    return removed


def list_backups() -> list[dict[str, Any]]:
    out = []
    for p in sorted(settings.backup_dir.glob("tally-*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({
            "file": p.name, "path": str(p), "size_bytes": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return out
