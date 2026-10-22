"""One connection per request, through Supabase's transaction pooler, in our own schema."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

# "office" in production; the tests use a schema of their own and drop it after.
SCHEMA = os.environ.get("CP_SCHEMA", "office")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", SCHEMA):
    raise RuntimeError(f"CP_SCHEMA {SCHEMA!r} is not a plain schema name")


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def connect() -> psycopg.Connection:
    """A connection with a transaction open in our schema. The transaction pooler can't keep
    prepared statements or session settings, hence prepare_threshold=None and SET LOCAL."""
    c = psycopg.connect(env("DATABASE_URL"), prepare_threshold=None, row_factory=dict_row, connect_timeout=10)
    c.execute(f"set local search_path = {SCHEMA}")
    return c


def conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency. Handlers commit what they write themselves, before answering;
    anything left uncommitted is rolled back here."""
    c = connect()
    try:
        yield c
    finally:
        c.close()


def audit(c: psycopg.Connection, action: str, *, user_id: int | None = None, device_id=None,
          detail: dict | None = None, ip: str = "") -> None:
    c.execute("insert into audit (action, user_id, device_id, detail, ip) values (%s, %s, %s, %s, %s)",
              (action, user_id, device_id, json.dumps(detail or {}), ip))
