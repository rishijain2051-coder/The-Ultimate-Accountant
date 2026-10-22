"""Apply the SQL in migrations/ that hasn't been applied yet, and add the super admin.

    python -m cp.migrate                          apply what is new
    python -m cp.migrate --admin EMAIL [--name N]  also add EMAIL with every access tick

Reads DATABASE_URL (and CP_SCHEMA, default "office") from the environment; run locally it
loads %USERPROFILE%\\.ultimate-accountant\\office.env. Prints file names only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cp import db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def migrate(admin: str | None = None, name: str = "") -> list[str]:
    s = db.SCHEMA
    origin = __import__("os").environ.get("PUBLIC_ORIGIN", "").rstrip("/").replace("'", "")
    c = db.connect()
    try:
        c.execute(f"create schema if not exists {s}")
        # Supabase's API roles get nothing here, now or in tables made later.
        c.execute(f"revoke all on schema {s} from public, anon, authenticated")
        c.execute(f"alter default privileges in schema {s} revoke all on tables from public, anon, authenticated")
        c.execute("create table if not exists schema_migrations (version text primary key, "
                  "applied_at timestamptz not null default now())")
        done = {r["version"] for r in c.execute("select version from schema_migrations")}
        applied = []
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.stem in done:
                continue
            # {{PUBLIC_ORIGIN}} in a migration is this control plane's own address (004 schedules a call to it).
            c.execute(f.read_text(encoding="utf-8").replace("{{PUBLIC_ORIGIN}}", origin))
            c.execute("insert into schema_migrations (version) values (%s)", (f.stem,))
            applied.append(f.stem)
        c.execute("alter table schema_migrations enable row level security")
        if admin:
            c.execute("insert into users (email, name, caps) values (%s, %s, array['admin','books','tib','console']) "
                      "on conflict (lower(email)) do update set caps = array['admin','books','tib','console'], "
                      "status = 'active'", (admin.strip().lower(), name))
            db.audit(c, "user.super-admin", detail={"email": admin.strip().lower()})
        c.commit()
        return applied
    finally:
        c.close()


if __name__ == "__main__":
    if not __import__("os").environ.get("DATABASE_URL"):
        from dotenv import load_dotenv
        load_dotenv(Path.home() / ".ultimate-accountant" / "office.env")
    a = sys.argv[1:]
    admin = a[a.index("--admin") + 1] if "--admin" in a else None
    name = a[a.index("--name") + 1] if "--name" in a else ""
    done = migrate(admin, name)
    print(f"schema {db.SCHEMA}: applied {', '.join(done) or 'nothing new'}"
          + (f"; super admin {admin} added" if admin else ""))
