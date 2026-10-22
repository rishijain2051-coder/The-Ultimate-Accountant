from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

# timeout: how long a save waits for another write to finish before giving up
# with "database is locked". Writes here take well under a second; the margin
# is for a slow PC reading bills at its CPU cap.
_connect_args = {"check_same_thread": False, "timeout": 30} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        # WAL lets the UI read while an import batch writes, and makes
        # VACUUM INTO backups consistent without stopping the app.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers mappers)

    models.Base.metadata.create_all(engine)
    _add_new_columns(models.Base)


def _add_new_columns(base) -> None:
    """create_all makes missing tables but never changes one that exists: a
    column added in a later version is added here, empty, to the books kept
    by an earlier one. ponytail: adds only; a renamed or retyped column
    needs a real migration."""
    have = inspect(engine)
    with engine.begin() as con:
        for table in base.metadata.sorted_tables:
            cols = {c["name"] for c in have.get_columns(table.name)}
            for col in table.columns:
                if col.name not in cols:
                    con.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                                     f'{col.type.compile(dialect=engine.dialect)}'))
