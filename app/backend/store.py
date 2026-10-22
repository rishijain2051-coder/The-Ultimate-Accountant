"""
Local SQLite store.

Holds everything that must outlive a process restart: completed reconciliation
runs, the user's per-invoice decisions, the supplier contact book, app
settings, and the documents filed in each export register. One file, no server, no migration tooling - the schema is created on
demand and every column added later is guarded by an ALTER probe.

Decisions are keyed on the invoice fingerprint rather than a row id, so a
re-run of the same period inherits every manual match and resolution note that
was recorded against the previous run.
"""

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import uuid
import zlib
from datetime import datetime
from pathlib import Path

from app.backend.paths import data_dir

DATA_DIR = data_dir()
DB_PATH = DATA_DIR / "reconcile.db"
DOCS_DIR = DATA_DIR / "documents"

_lock = threading.Lock()  # ponytail: one global lock; per-table locks if the UI ever goes multi-user
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    name         TEXT,
    client_name  TEXT,
    gstin        TEXT,
    period_from  TEXT,
    period_to    TEXT,
    notes        TEXT,
    archived     INTEGER NOT NULL DEFAULT 0,
    module       TEXT NOT NULL DEFAULT 'reconcile'
);

CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'purchase',
    label         TEXT,
    entity_name   TEXT,
    gstin         TEXT,
    fy_start      INTEGER,
    period_from   TEXT,
    period_to     TEXT,
    tolerance     REAL,
    source_json   TEXT,
    settings_json TEXT,
    summary_json  TEXT,
    payload_json  TEXT,
    pinned        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_ws ON runs (workspace_id, kind, created_at);

CREATE TABLE IF NOT EXISTS items (
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    fp          TEXT,
    status      TEXT,
    gstin       TEXT,
    party       TEXT,
    doc_no      TEXT,
    doc_date    TEXT,
    month_key   TEXT,
    value       REAL,
    tax         REAL,
    tally_value REAL,
    flags       TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_items_fp     ON items (fp);
CREATE INDEX IF NOT EXISTS idx_items_status ON items (run_id, status);

-- Keyed on the workspace as well as the fingerprint: a write-off decided for
-- one client's run must not reappear in another client's.
CREATE TABLE IF NOT EXISTS decisions (
    workspace_id TEXT NOT NULL DEFAULT '',
    fp          TEXT NOT NULL,
    action      TEXT NOT NULL,
    counterpart TEXT,
    note        TEXT,
    author      TEXT,
    context     TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (workspace_id, fp)
);

-- Every file given to the app, with what was read from it. The original is
-- kept on disk beside the database, under a name a person can find:
-- documents/<run or register>/<kind>/<name>, the path held in stored_path.
-- An export document waits as 'pending' until the user has checked what was
-- read and posted it; corrections hold what the user put right, laid over
-- meta_json, which stays as the reader left it.
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    added_at     TEXT NOT NULL,
    filename     TEXT,
    sha256       TEXT,
    type         TEXT,
    size         INTEGER,
    meta_json    TEXT,
    stored_path  TEXT,
    status       TEXT NOT NULL DEFAULT 'posted',
    corrections  TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_ws ON documents (workspace_id, sha256);

-- What was done to a register, in the words the screen uses: each document
-- added, checked, corrected (with what it was before), posted or removed, and
-- each payment split, yes-or-no answer and decision.
CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    at           TEXT NOT NULL,
    by           TEXT,
    what         TEXT NOT NULL,
    detail       TEXT NOT NULL,
    doc_id       TEXT,
    changes      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ws ON audit (workspace_id, id);

-- The AI checker's second reading of each export document (app/backend/ai_check.py):
-- of which reading of it, whether it could read it, and what it read.
CREATE TABLE IF NOT EXISTS ai_checks (
    doc_id       TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    reading      TEXT NOT NULL,
    state        TEXT NOT NULL,
    result       TEXT,
    at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    gstin      TEXT PRIMARY KEY,
    name       TEXT,
    contact    TEXT,
    email      TEXT,
    phone      TEXT,
    notes      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT
);
"""

DEFAULT_SETTINGS = {
    "tolerance": 2.0,
    "fy_start": 2025,
    "date_window_days": 45,
    "party_threshold": 0.50,
    "probable_threshold": 0.72,
    "value_pct_tolerance": 0.10,
    "entity_name": "",
    "gstin": "",
    "entity_address": "",
    "preparer": "",
    "theme": "ink",
    "density": "comfortable",
    "auto_save_runs": True,
    "rounding": 2,
    "check_updates": True,
}


SCHEMA_VERSION = 5


def connect():
    """Process-wide connection, created on first use."""
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fresh = not DB_PATH.exists()
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        if not fresh:
            _migrate(_conn)
        _conn.executescript(SCHEMA)
        _conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        _conn.commit()
    return _conn


def _columns(conn, table):
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _migrate(conn):
    """Bring an older database up to the current schema."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if not _columns(conn, "runs"):
        return                                   # nothing stored yet
    if version < 2:
        _split_runs(conn)
    # 3: a run belongs to a module; everything before it was a reconciliation.
    if "module" not in _columns(conn, "workspaces"):
        conn.execute("ALTER TABLE workspaces ADD COLUMN module TEXT NOT NULL DEFAULT 'reconcile'")
    # 4: files are kept under readable names, the path stored with each. Files
    # kept before then stay where they are and are still found.
    if _columns(conn, "documents") and "stored_path" not in _columns(conn, "documents"):
        conn.execute("ALTER TABLE documents ADD COLUMN stored_path TEXT")
    # 5: a document waits to be checked before it counts, and keeps the user's
    # corrections beside what was read. Everything already on file was filed
    # the moment it was added, so it stands as posted.
    cols = _columns(conn, "documents")
    if cols and "status" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'posted'")
    if cols and "corrections" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN corrections TEXT")
    conn.commit()


def _split_runs(conn):
    """2: everything already stored was reconciled before runs were kept
    apart, so each stored result becomes a run of its own - separate by
    default, which is the safe reading. Decisions were held against the
    fingerprint alone, so each one is copied into every run it actually
    applies to and the original table is kept under another name rather than
    dropped.
    """
    backup = DB_PATH.with_suffix(".pre-v2.db")
    if not backup.exists():
        try:
            conn.commit()
            with sqlite3.connect(backup) as dest:
                conn.backup(dest)
        except sqlite3.Error:
            pass                                 # a backup is a courtesy, not a gate

    # The column has to exist before the schema script runs: it indexes it.
    if "workspace_id" not in _columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN workspace_id TEXT")
    conn.executescript(SCHEMA)

    now = _now()
    orphans = conn.execute(
        "SELECT id, created_at, kind, label, entity_name, gstin, period_from, "
        "period_to FROM runs WHERE workspace_id IS NULL OR workspace_id = ''"
    ).fetchall()
    for row in orphans:
        ws = uuid.uuid4().hex[:12]
        name = " · ".join(x for x in (row["entity_name"], row["label"]) if x) \
            or f"{row['kind']} run"
        conn.execute(
            "INSERT INTO workspaces (id, created_at, updated_at, name, client_name, "
            "gstin, period_from, period_to, notes, archived) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (ws, row["created_at"] or now, row["created_at"] or now, name[:120],
             row["entity_name"] or "", row["gstin"] or "", row["period_from"],
             row["period_to"], "Separated when runs were split apart."))
        conn.execute("UPDATE runs SET workspace_id = ? WHERE id = ?", (ws, row["id"]))

    if "workspace_id" not in _columns(conn, "decisions"):
        old = conn.execute("SELECT * FROM decisions").fetchall()
        conn.execute("ALTER TABLE decisions RENAME TO decisions_legacy")
        conn.executescript(SCHEMA)
        for d in old:
            for ws in conn.execute(
                    "SELECT DISTINCT r.workspace_id FROM items i "
                    "JOIN runs r ON r.id = i.run_id WHERE i.fp = ?", (d["fp"],)):
                if not ws["workspace_id"]:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO decisions (workspace_id, fp, action, "
                    "counterpart, note, author, context, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (ws["workspace_id"], d["fp"], d["action"], d["counterpart"],
                     d["note"], d["author"], d["context"], d["updated_at"]))
    conn.commit()


def close():
    """Checkpoint the write-ahead log and close.

    Called on the way out so the next start opens a settled database rather
    than recovering a 4MB journal, and so nothing is left half-written if the
    machine is shut down straight after.
    """
    global _conn
    with _lock:
        if _conn is None:
            return
        try:
            _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _conn.commit()
        except sqlite3.Error:
            pass                      # closing matters more than tidying
        finally:
            _conn.close()
            _conn = None


def data_folder_size():
    return sum(p.stat().st_size for p in DATA_DIR.rglob("*") if p.is_file())


def move_data(target):
    """Move the database and every kept file to `target` and work from there.

    Everything is copied and the copy checked, byte count and file count,
    before the originals go - a failure part-way leaves the data where it was.
    Only the app's own items move; anything else in the old folder stays.
    """
    global DATA_DIR, DB_PATH, DOCS_DIR, _conn
    from app.backend import paths
    source, target = DATA_DIR, Path(target)
    if target == source or target.is_relative_to(source):
        raise ValueError("Choose a folder outside the one your data is in now.")
    if (target / "reconcile.db").exists():
        raise ValueError(f"{target} already holds Ultimate Accountant data. Choose an empty folder.")

    def tally(root, names):
        files = [p for n in names for p in ([root / n] if (root / n).is_file() else (root / n).rglob("*"))
                 if p.is_file()]
        return len(files), sum(p.stat().st_size for p in files)

    close()                                             # settles the journal into the file
    with _lock:
        names = [n for n in paths.DATA_ITEMS if (source / n).exists()]
        try:
            target.mkdir(parents=True, exist_ok=True)
            for n in names:
                if (source / n).is_dir():
                    shutil.copytree(source / n, target / n, dirs_exist_ok=True)
                else:
                    shutil.copy2(source / n, target / n)
            if tally(target, names) != tally(source, names):
                raise OSError("the copy did not come out the same size")
        except OSError as exc:
            for n in names:                             # the half copy goes; the originals never moved
                shutil.rmtree(target / n, ignore_errors=True) if (target / n).is_dir() \
                    else (target / n).unlink(missing_ok=True)
            raise ValueError(f"Your data could not be copied to {target} ({exc}). It is still where it was.") from exc
        paths.remember_data_dir(target)
        DATA_DIR, DB_PATH, DOCS_DIR = target, target / "reconcile.db", target / "documents"
        for n in names:
            shutil.rmtree(source / n, ignore_errors=True) if (source / n).is_dir() \
                else (source / n).unlink(missing_ok=True)
        try:
            source.rmdir()                              # only if nothing else lives there
        except OSError:
            pass
    return target


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _pack(obj):
    """Store a run payload compressed - a year of monthly runs otherwise grows
    the database by tens of megabytes of very repetitive JSON."""
    return zlib.compress(json.dumps(obj, default=str).encode("utf-8"), 6)


def _unpack(blob):
    if blob is None:
        return {}
    if isinstance(blob, (bytes, bytearray, memoryview)):
        raw = bytes(blob)
        try:
            return json.loads(zlib.decompress(raw).decode("utf-8"))
        except zlib.error:
            return json.loads(raw.decode("utf-8"))   # written before compression
    return json.loads(blob or "{}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_settings():
    conn = connect()
    with _lock:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    out = dict(DEFAULT_SETTINGS)
    for k, v in stored.items():
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            out[k] = v
    return out


def save_settings(patch):
    conn = connect()
    with _lock:
        for k, v in (patch or {}).items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v)),
            )
        conn.commit()
    return get_settings()


# ---------------------------------------------------------------------------
# Workspaces - one client, one period. The interface calls these "runs"; the
# `runs` table below holds the individual reconciliations inside one.
# ---------------------------------------------------------------------------

def create_workspace(name=None, client_name="", gstin="", period_from=None,
                     period_to=None, notes="", module="reconcile"):
    conn = connect()
    ws = uuid.uuid4().hex[:12]
    now = _now()
    with _lock:
        conn.execute(
            "INSERT INTO workspaces (id, created_at, updated_at, name, client_name, "
            "gstin, period_from, period_to, notes, archived, module) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?)",
            (ws, now, now, (name or client_name or "New run")[:120], client_name or "",
             (gstin or "").upper(), period_from, period_to, notes or "", module))
        conn.execute("INSERT INTO activity (at, kind, detail) VALUES (?,?,?)",
                     (now, "workspace", json.dumps({"id": ws, "name": name})))
        conn.commit()
    return ws


def update_workspace(ws_id, **fields):
    conn = connect()
    allowed = ("name", "client_name", "gstin", "period_from", "period_to",
               "notes", "archived")
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        return get_workspace(ws_id)
    with _lock:
        sets = ", ".join(f"{k} = ?" for k in patch)
        conn.execute(f"UPDATE workspaces SET {sets}, updated_at = ? WHERE id = ?",
                     (*patch.values(), _now(), ws_id))
        conn.commit()
    return get_workspace(ws_id)


def touch_workspace(ws_id, **fields):
    """Keep a run's client and period in step with what its parts turned out
    to hold, without overwriting anything the user typed in."""
    ws = get_workspace(ws_id)
    if not ws:
        return None
    patch = {k: v for k, v in fields.items() if v and not ws.get(k)}
    if ws.get("name") in (None, "", "New run") and fields.get("client_name"):
        patch["name"] = " · ".join(
            x for x in (fields.get("client_name"), fields.get("period_label")) if x)[:120]
    patch.pop("period_label", None)
    return update_workspace(ws_id, **patch) if patch else ws


def get_workspace(ws_id):
    conn = connect()
    with _lock:
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
    return dict(row) if row else None


def list_workspaces(limit=200, include_archived=False):
    """Every run, newest first, each with a note of what it holds."""
    conn = connect()
    sql = ("SELECT w.*, "
           "(SELECT COUNT(*) FROM runs r WHERE r.workspace_id = w.id) AS part_count, "
           "(SELECT MAX(r.created_at) FROM runs r WHERE r.workspace_id = w.id) AS last_run_at, "
           "(SELECT COUNT(*) FROM documents d WHERE d.workspace_id = w.id) AS doc_count "
           "FROM workspaces w")
    if not include_archived:
        sql += " WHERE w.archived = 0"
    sql += " ORDER BY COALESCE(last_run_at, w.updated_at) DESC, w.rowid DESC LIMIT ?"
    with _lock:
        rows = _rows(conn.execute(sql, (limit,)))
        kinds = {}
        for r in conn.execute(
                "SELECT workspace_id, kind, COUNT(*) AS n FROM runs GROUP BY 1, 2"):
            kinds.setdefault(r["workspace_id"], {})[r["kind"]] = r["n"]
    for r in rows:
        r["parts"] = kinds.get(r["id"], {})
    return rows


def delete_workspace(ws_id):
    """Remove a run and everything inside it, its kept files too."""
    folder = group_folder(ws_id)
    conn = connect()
    with _lock:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM runs WHERE workspace_id = ?", (ws_id,))]
        for rid in ids:
            conn.execute("DELETE FROM items WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM runs WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM decisions WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM documents WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM audit WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM ai_checks WHERE workspace_id = ?", (ws_id,))
        cur = conn.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
        conn.commit()
    if folder:
        shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(DOCS_DIR / ws_id, ignore_errors=True)       # kept before readable names
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Documents - the Exports module files each one against its register
# ---------------------------------------------------------------------------

def _doc_path(ws_id, doc_id, filename, stored=None):
    if stored:
        return DOCS_DIR / stored
    return DOCS_DIR / ws_id / (doc_id + Path(filename or "").suffix.lower()[:8])    # kept before 4


def safe_name(text, limit=100):
    """A name Windows takes for a file or a folder, still readable."""
    s = re.sub(r"[\\/]", "-", str(text or ""))
    s = re.sub(r'[<>:"|?*\x00-\x1f]', " ", s).replace("\u00b7", "-")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].rstrip(" .") or "untitled"


def file_name(filename, name=None):
    """What a file is kept as: `name` if given (what it is), else the name it
    came with - cleaned for Windows, keeping the file's own extension. The
    slashes in "KE/24-25/088" are part of a name, not a path."""
    ext = Path(filename or "").suffix.lower()[:8]
    given = name or filename or "document"
    stem = given[:-len(ext)] if ext and given.lower().endswith(ext) else given
    return safe_name(stem, 100 - len(ext)) + ext


def _group_dir(conn, ws_id, hint=None):
    """The folder one run's or register's files live in: named from what it
    was called when its first file arrived, and kept after that."""
    row = conn.execute("SELECT stored_path FROM documents WHERE workspace_id = ? AND stored_path IS NOT NULL "
                       "LIMIT 1", (ws_id,)).fetchone()
    if row:
        return row["stored_path"].split("/")[0]
    if not hint:
        w = conn.execute("SELECT name FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        hint = w["name"] if w else ""
    return f"{safe_name(hint, 60)} ({ws_id[:6]})"


def add_document(ws_id, filename, data, meta, folder="", name=None, group=None, status="posted"):
    """Keep one file and what was read from it, as
    documents/<group>/<folder>/<name>. The same file added twice is kept
    once: returns (id, False) for the copy already on file. An export
    document that is to be checked first is added as status "pending"."""
    sha = hashlib.sha256(data).hexdigest()
    conn = connect()
    with _lock:
        hit = conn.execute("SELECT id FROM documents WHERE workspace_id = ? AND sha256 = ?",
                           (ws_id, sha)).fetchone()
        if hit:
            return hit["id"], False
        doc_id = uuid.uuid4().hex[:12]
        base = Path(_group_dir(conn, ws_id, group))
        if folder:
            base = base / safe_name(folder, 40)
        kept = file_name(filename, name)
        ext = Path(filename or "").suffix.lower()[:8]
        stem = kept[:len(kept) - len(ext)]
        rel, n = base / kept, 2
        while (DOCS_DIR / rel).exists():                  # two files that would share a name
            rel, n = base / f"{stem} ({n}){ext}", n + 1
        path = DOCS_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        now = _now()
        conn.execute(
            "INSERT INTO documents (id, workspace_id, added_at, filename, sha256, type, size, "
            "meta_json, stored_path, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, ws_id, now, filename, sha, meta.get("type"), len(data),
             json.dumps(meta, default=str), rel.as_posix(), status))
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (now, ws_id))
        conn.commit()
    return doc_id, True


def document_hashes(ws_id):
    """The fingerprints of the files one run or register holds."""
    conn = connect()
    with _lock:
        return {r["sha256"] for r in conn.execute("SELECT sha256 FROM documents WHERE workspace_id = ?", (ws_id,))}


def group_folder(ws_id):
    """Where one run's or register's files are, on this computer."""
    conn = connect()
    with _lock:
        row = conn.execute("SELECT stored_path FROM documents WHERE workspace_id = ? AND stored_path IS NOT NULL "
                           "LIMIT 1", (ws_id,)).fetchone()
    return DOCS_DIR / row["stored_path"].split("/")[0] if row else None


def list_documents(ws_id, status=None):
    """The documents in a run or register as the reader left them, oldest
    first, each carrying the id, name and date the Exports engine files it
    under, its status, and the user's corrections (laid over it by
    exim_fields.apply). `status` keeps one kind: "posted" or "pending"."""
    conn = connect()
    with _lock:
        rows = _rows(conn.execute(
            "SELECT id, added_at, filename, meta_json, stored_path, size, status, corrections FROM documents "
            "WHERE workspace_id = ? AND (? IS NULL OR status = ?) ORDER BY added_at, rowid",
            (ws_id, status, status)))
    out = []
    for r in rows:
        meta = json.loads(r["meta_json"] or "{}")
        parts = (r["stored_path"] or "").split("/")
        meta.update(id=r["id"], name=r["filename"], added_at=r["added_at"], size=r["size"],
                    kept_as=parts[-1] if r["stored_path"] else r["filename"],
                    folder=parts[1] if len(parts) > 2 else "", status=r["status"],
                    corrections=json.loads(r["corrections"] or "{}"))
        out.append(meta)
    return out


def save_corrections(ws_id, doc_id, corrections, kind=None):
    """Keep the user's corrections to one document; the reader's own output stays as it was."""
    conn = connect()
    with _lock:
        conn.execute("UPDATE documents SET corrections = ?, type = COALESCE(?, type) WHERE workspace_id = ? AND id = ?",
                     (json.dumps(corrections, default=str) if corrections else None, kind, ws_id, doc_id))
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (_now(), ws_id))
        conn.commit()


def replace_reading(ws_id, doc_id, meta, corrections):
    """What a kept file says, read again: the new reading replaces the old,
    the user's corrections that still differ from it stay, and it waits to be
    checked again before it counts."""
    conn = connect()
    with _lock:
        conn.execute("UPDATE documents SET meta_json = ?, type = ?, corrections = ?, status = 'pending' "
                     "WHERE workspace_id = ? AND id = ?",
                     (json.dumps(meta, default=str), (corrections or {}).get("type") or meta.get("type"),
                      json.dumps(corrections, default=str) if corrections else None, ws_id, doc_id))
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (_now(), ws_id))
        conn.commit()


def post_documents(ws_id, ids):
    """Put checked documents into the register. Returns the ids that were
    waiting and are now posted."""
    conn = connect()
    with _lock:
        waiting = [r["id"] for r in conn.execute(
            f"SELECT id FROM documents WHERE workspace_id = ? AND status = 'pending' AND id IN "
            f"({','.join('?' * len(ids))})", (ws_id, *ids))] if ids else []
        conn.executemany("UPDATE documents SET status = 'posted' WHERE workspace_id = ? AND id = ?",
                         [(ws_id, i) for i in waiting])
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (_now(), ws_id))
        conn.commit()
    return waiting


def refile_document(ws_id, doc_id, folder, name):
    """Move a kept file to the name and folder its corrected reading gives it
    - a bill of lading misread as something else, or kept under a misread
    number. A file Windows will not let go of keeps its old name."""
    doc = get_document(ws_id, doc_id)
    if not doc or not doc.get("stored_path"):
        return None
    old = doc["path"]
    group = doc["stored_path"].split("/")[0]
    kept = file_name(doc["filename"], name)
    ext = Path(doc["filename"] or "").suffix.lower()[:8]
    stem = kept[:len(kept) - len(ext)]
    base = Path(group) / safe_name(folder, 40) if folder else Path(group)
    rel, n = base / kept, 2
    if (DOCS_DIR / rel) == old:
        return rel.as_posix()
    while (DOCS_DIR / rel).exists():
        rel, n = base / f"{stem} ({n}){ext}", n + 1
    try:
        (DOCS_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
        old.rename(DOCS_DIR / rel)
    except OSError:
        return None
    conn = connect()
    with _lock:
        conn.execute("UPDATE documents SET stored_path = ? WHERE workspace_id = ? AND id = ?",
                     (rel.as_posix(), ws_id, doc_id))
        conn.commit()
    try:
        old.parent.rmdir()                                  # the folder it left, if that was its last file
    except OSError:
        pass
    return rel.as_posix()


def save_ai_check(ws_id, doc_id, reading, state, result):
    """Keep the AI checker's reading of one document: of which reading of it
    ("reading", ai_check.reading_key), "done" or "failed", and what it read."""
    conn = connect()
    with _lock:
        conn.execute("INSERT INTO ai_checks (doc_id, workspace_id, reading, state, result, at) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(doc_id) DO UPDATE SET reading = excluded.reading, state = excluded.state, "
                     "result = excluded.result, at = excluded.at",
                     (doc_id, ws_id, reading, state, json.dumps(result, default=str), _now()))
        conn.commit()


def ai_checks(ws_id):
    """The AI checker's readings of a register's documents: {document id: row}."""
    conn = connect()
    with _lock:
        rows = _rows(conn.execute("SELECT doc_id, reading, state, result, at FROM ai_checks WHERE workspace_id = ?",
                                  (ws_id,)))
    for r in rows:
        r["result"] = json.loads(r["result"]) if r["result"] else None
    return {r["doc_id"]: r for r in rows}


def audit(ws_id, what, detail, doc_id=None, changes=None, by=None):
    """Write one line of a register's history."""
    conn = connect()
    with _lock:
        conn.execute("INSERT INTO audit (workspace_id, at, by, what, detail, doc_id, changes) VALUES (?,?,?,?,?,?,?)",
                     (ws_id, _now(), by, what, detail, doc_id, json.dumps(changes) if changes else None))
        conn.commit()


def list_audit(ws_id, limit=None):
    """A register's history, newest first."""
    conn = connect()
    with _lock:
        rows = _rows(conn.execute("SELECT at, by, what, detail, doc_id, changes FROM audit WHERE workspace_id = ? "
                                  "ORDER BY id DESC LIMIT ?", (ws_id, limit or -1)))
    for r in rows:
        r["changes"] = json.loads(r["changes"]) if r["changes"] else []
    return rows


def get_document(ws_id, doc_id):
    conn = connect()
    with _lock:
        row = conn.execute("SELECT id, filename, size, stored_path FROM documents WHERE workspace_id = ? "
                           "AND id = ?", (ws_id, doc_id)).fetchone()
    if not row:
        return None
    path = _doc_path(ws_id, doc_id, row["filename"], row["stored_path"])
    return {**dict(row), "path": path, "kept_as": path.name if row["stored_path"] else row["filename"]}


def delete_document(ws_id, doc_id):
    doc = get_document(ws_id, doc_id)
    if not doc:
        return False
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM documents WHERE workspace_id = ? AND id = ?", (ws_id, doc_id))
        conn.execute("DELETE FROM ai_checks WHERE doc_id = ?", (doc_id,))
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (_now(), ws_id))
        conn.commit()
    doc["path"].unlink(missing_ok=True)
    for folder in doc["path"].parents:                   # empty folders go with their last file
        if folder == DOCS_DIR or not folder.is_relative_to(DOCS_DIR):
            break
        try:
            folder.rmdir()
        except OSError:
            break
    return True


def latest_parts(ws_id):
    """The newest run of each kind inside one run, which is what it shows."""
    conn = connect()
    with _lock:
        rows = _rows(conn.execute(
            "SELECT id, kind, created_at, label, summary_json, source_json FROM runs "
            "WHERE workspace_id = ? ORDER BY created_at DESC, rowid DESC", (ws_id,)))
    out = {}
    for r in rows:
        if r["kind"] in out:
            continue
        r["summary"] = json.loads(r.pop("summary_json") or "{}")
        r["source"] = json.loads(r.pop("source_json") or "{}")
        out[r["kind"]] = r
    return out


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def _index_row(rec, kind):
    """
    Flatten one reconciliation record into the searchable `items` columns.

    The three engines produce different record shapes, so each one is mapped
    here rather than teaching every caller about the table. The columns are
    only an index - history, comparison and the open-items list read them, and
    the full record always comes back from the run payload.
    """
    flags = ",".join(rec.get("flags") or [])
    if kind == "bank":
        return (
            rec.get("fp"), rec.get("status"), "",
            rec.get("counterparty") or rec.get("book_party") or "",
            rec.get("bank_ref") or rec.get("book_vch_no") or "",
            rec.get("date_bank") or rec.get("date_book"),
            rec.get("month_key"),
            rec.get("bank_amount") if rec.get("bank_amount") is not None
            else rec.get("book_amount"),
            0.0, rec.get("book_amount"), flags,
        )
    if kind == "statement":
        return (
            rec.get("fp"), rec.get("status"), "",
            rec.get("particulars_theirs") or rec.get("particulars_ours") or "",
            rec.get("ref_theirs") or rec.get("ref_ours") or "",
            rec.get("date_theirs") or rec.get("date_ours"),
            rec.get("month_key"),
            rec.get("owed_theirs") if rec.get("owed_theirs") is not None
            else rec.get("owed_ours"),
            0.0, rec.get("owed_ours"), flags,
        )
    return (
        rec.get("fp"), rec.get("status"), rec.get("gstin") or "",
        rec.get("party_2b") or rec.get("party_tally") or "",
        rec.get("inv_no_2b") or rec.get("vch_no_tally") or "",
        rec.get("inv_date_2b") or rec.get("vch_date_tally"),
        rec.get("month_key"),
        rec.get("inv_val_2b") if rec.get("inv_val_2b") is not None
        else rec.get("amt_tally"),
        rec.get("tax_2b") or 0.0, rec.get("amt_tally"), flags,
    )


def save_run(result, kind="purchase", source=None, settings=None, label=None,
             workspace_id=None):
    """Persist a completed reconciliation and return its run id."""
    conn = connect()
    run_id = uuid.uuid4().hex[:12]
    entity = result.get("entity_info") or {}
    # The run list and the history view read only this summary, never the full
    # payload, so each engine contributes the handful of figures it headlines
    # with. Absent keys stay absent rather than being faked to zero.
    summary = {
        "control_totals": result.get("control_totals"),
        "counts": result.get("counts"),
        "status_summary": result.get("status_summary"),
        "itc_impact": result.get("itc_impact"),
        "itc_position": result.get("itc_position"),
        "health": result.get("health"),
        "rates": result.get("rates"),
        "balances": result.get("balances"),
        "brs_ties": (result.get("brs") or {}).get("ties"),
        "bridge_ties": (result.get("bridge") or {}).get("bridge_ties")
        if result.get("bridge") else None,
        "charges": result.get("charges"),
        "findings_count": len(result.get("findings") or []),
    }
    if result.get("bridge"):
        summary["bridge_ties"] = result["bridge"].get("ties")
    rows = [(run_id, i, *cols)
            for i, cols in enumerate(_index_row(rec, kind)
                                     for rec in (result.get("all_records") or []))]

    with _lock:
        conn.execute(
            "INSERT INTO runs (id, workspace_id, created_at, kind, label, entity_name, "
            "gstin, fy_start, period_from, period_to, tolerance, source_json, "
            "settings_json, summary_json, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, workspace_id, _now(), kind,
                label or entity.get("date_range") or "",
                entity.get("entity_name") or "", entity.get("gstin") or "",
                entity.get("fy_start"), entity.get("period_from"), entity.get("period_to"),
                (settings or {}).get("tolerance"),
                json.dumps(source or {}), json.dumps(settings or {}),
                json.dumps(summary), _pack(result),
            ),
        )
        if workspace_id:
            conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?",
                         (_now(), workspace_id))
        conn.executemany(
            "INSERT INTO items (run_id, seq, fp, status, gstin, party, doc_no, doc_date, "
            "month_key, value, tax, tally_value, flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute("INSERT INTO activity (at, kind, detail) VALUES (?,?,?)",
                     (_now(), "run", json.dumps({"run_id": run_id, "kind": kind,
                                                 "records": len(rows)})))
        conn.commit()
    return run_id


def list_runs(limit=60, kind=None, workspace_id=None):
    conn = connect()
    sql = ("SELECT id, workspace_id, created_at, kind, label, entity_name, gstin, "
           "fy_start, tolerance, source_json, summary_json, pinned FROM runs")
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if workspace_id:
        where.append("workspace_id = ?")
        args.append(workspace_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # rowid breaks the tie. Timestamps are only second-accurate, so two runs
    # started in the same second would otherwise come back in whatever order
    # SQLite felt like - and "the newest run" is what the app restores on
    # start and what the exports act on.
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _rows(conn.execute(sql, args))
    for r in rows:
        r["source"] = json.loads(r.pop("source_json") or "{}")
        r["summary"] = json.loads(r.pop("summary_json") or "{}")
    return rows


def get_run(run_id):
    conn = connect()
    with _lock:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    payload = _unpack(row["payload_json"])
    payload["run_id"] = row["id"]
    payload["created_at"] = row["created_at"]
    payload["kind"] = row["kind"]
    payload["source_info"] = json.loads(row["source_json"] or "{}")
    return payload


def delete_run(run_id):
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM items WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
    return cur.rowcount > 0


def set_run_pinned(run_id, pinned):
    conn = connect()
    with _lock:
        conn.execute("UPDATE runs SET pinned = ? WHERE id = ?", (1 if pinned else 0, run_id))
        conn.commit()


def run_month_series(run_id):
    """Value and tax totals per month for one run, for the trend charts."""
    conn = connect()
    with _lock:
        return _rows(conn.execute(
            "SELECT month_key, status, COUNT(*) AS n, "
            "COALESCE(SUM(value),0) AS value, COALESCE(SUM(tax),0) AS tax, "
            "COALESCE(SUM(tally_value),0) AS tally_value "
            "FROM items WHERE run_id = ? AND month_key IS NOT NULL "
            "GROUP BY month_key, status ORDER BY month_key",
            (run_id,),
        ))


def open_items_across_runs(limit=400, workspace_id=None):
    """Unmatched items that appear in the most recent run for their fingerprint,
    so an item fixed in a later run drops out of the list.

    Confined to one run unless asked otherwise: pooling every client's open
    items into one list was the clearest way the old behaviour mixed them up.
    """
    conn = connect()
    scope = "AND r0.workspace_id = ?" if workspace_id else ""
    args = ([workspace_id] if workspace_id else []) + [limit]
    with _lock:
        return _rows(conn.execute(
            f"""
            WITH latest AS (
                SELECT i.*, r0.created_at,
                       ROW_NUMBER() OVER (PARTITION BY i.fp ORDER BY r0.created_at DESC) AS rn
                FROM items i JOIN runs r0 ON r0.id = i.run_id
                WHERE i.fp IS NOT NULL AND i.fp <> '' {scope}
            )
            SELECT l.fp, l.status, l.gstin, l.party, l.doc_no, l.doc_date,
                   l.value, l.tax, l.flags, l.created_at, l.run_id, r.kind,
                   r.workspace_id
            FROM latest l JOIN runs r ON r.id = l.run_id
            WHERE l.rn = 1 AND l.status NOT IN ('Matched', 'Agreed', 'Superseded')
            ORDER BY l.tax DESC, ABS(l.value) DESC
            LIMIT ?
            """,
            args,
        ))


def resolution_history(fp):
    """Every run in which a fingerprint appeared, oldest first."""
    conn = connect()
    with _lock:
        return _rows(conn.execute(
            "SELECT i.run_id, r.created_at, i.status, i.value, i.tally_value, i.flags "
            "FROM items i JOIN runs r ON r.id = i.run_id "
            "WHERE i.fp = ? ORDER BY r.created_at ASC",
            (fp,),
        ))


def compare_runs(run_a, run_b):
    """Fingerprint-level diff between two runs."""
    conn = connect()
    with _lock:
        a = {r["fp"]: dict(r) for r in conn.execute(
            "SELECT fp, status, party, doc_no, value, tax FROM items "
            "WHERE run_id = ? AND fp IS NOT NULL AND fp <> ''", (run_a,))}
        b = {r["fp"]: dict(r) for r in conn.execute(
            "SELECT fp, status, party, doc_no, value, tax FROM items "
            "WHERE run_id = ? AND fp IS NOT NULL AND fp <> ''", (run_b,))}

    # Anything that is not a clean match counts as open, whichever engine
    # produced it, so a run comparison works across all three.
    SETTLED = {"Matched", "Agreed", "Superseded"}
    resolved, appeared, changed = [], [], []
    for fp, ra in a.items():
        rb = b.get(fp)
        if rb is None:
            if ra["status"] not in SETTLED:
                resolved.append({**ra, "from": ra["status"], "to": "absent"})
            continue
        if rb["status"] != ra["status"]:
            entry = {**rb, "from": ra["status"], "to": rb["status"]}
            was_open = ra["status"] not in SETTLED
            is_open = rb["status"] not in SETTLED
            if was_open and not is_open:
                resolved.append(entry)
            elif not was_open and is_open:
                appeared.append(entry)
            else:
                changed.append(entry)
    for fp, rb in b.items():
        if fp not in a and rb["status"] not in SETTLED:
            appeared.append({**rb, "from": "absent", "to": rb["status"]})

    for lst in (resolved, appeared, changed):
        lst.sort(key=lambda x: -(x.get("tax") or 0))
    return {
        "resolved": resolved, "appeared": appeared, "changed": changed,
        "totals": {
            "resolved_count": len(resolved),
            "resolved_tax": round(sum(x.get("tax") or 0 for x in resolved), 2),
            "appeared_count": len(appeared),
            "appeared_tax": round(sum(x.get("tax") or 0 for x in appeared), 2),
            "changed_count": len(changed),
        },
    }


# ---------------------------------------------------------------------------
# Decisions (manual matches, write-offs, resolution notes)
# ---------------------------------------------------------------------------

VALID_ACTIONS = {"match", "unmatch", "resolved", "ignore", "flag", "follow_up", "mark", "clear"}


def set_decision(fp, action, counterpart=None, note=None, author=None, context=None,
                 workspace_id=""):
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    ws = workspace_id or ""
    conn = connect()
    with _lock:
        if action == "clear":
            conn.execute("DELETE FROM decisions WHERE workspace_id = ? AND fp = ?", (ws, fp))
        else:
            conn.execute(
                "INSERT INTO decisions (workspace_id, fp, action, counterpart, note, "
                "author, context, updated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id, fp) DO UPDATE SET "
                "action=excluded.action, counterpart=excluded.counterpart, note=excluded.note, "
                "author=excluded.author, context=excluded.context, updated_at=excluded.updated_at",
                (ws, fp, action, counterpart, note, author, json.dumps(context or {}), _now()),
            )
        conn.execute("INSERT INTO activity (at, kind, detail) VALUES (?,?,?)",
                     (_now(), "decision",
                      json.dumps({"fp": fp, "action": action, "workspace": ws})))
        conn.commit()
    return get_decisions(ws)


def get_decisions(workspace_id=""):
    conn = connect()
    with _lock:
        rows = _rows(conn.execute("SELECT * FROM decisions WHERE workspace_id = ?",
                                  (workspace_id or "",)))
    out = {}
    for r in rows:
        try:
            r["context"] = json.loads(r.get("context") or "{}")
        except json.JSONDecodeError:
            r["context"] = {}
        out[r["fp"]] = r
    return out


def copy_decisions(from_ws, to_ws):
    """Carry a run's decisions into the next one, when the user asks for it."""
    conn = connect()
    with _lock:
        cur = conn.execute(
            "INSERT OR REPLACE INTO decisions (workspace_id, fp, action, counterpart, "
            "note, author, context, updated_at) "
            "SELECT ?, fp, action, counterpart, note, author, context, updated_at "
            "FROM decisions WHERE workspace_id = ?", (to_ws, from_ws))
        conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Supplier contact book
# ---------------------------------------------------------------------------

def upsert_supplier(gstin, **fields):
    conn = connect()
    allowed = ("name", "contact", "email", "phone", "notes")
    patch = {k: v for k, v in fields.items() if k in allowed}
    with _lock:
        conn.execute("INSERT INTO suppliers (gstin, updated_at) VALUES (?, ?) "
                     "ON CONFLICT(gstin) DO NOTHING", (gstin, _now()))
        if patch:
            sets = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(f"UPDATE suppliers SET {sets}, updated_at = ? WHERE gstin = ?",
                         (*patch.values(), _now(), gstin))
        conn.commit()
    return get_suppliers()


def get_suppliers():
    conn = connect()
    with _lock:
        return {r["gstin"]: dict(r) for r in conn.execute("SELECT * FROM suppliers")}


def recent_activity(limit=40):
    conn = connect()
    with _lock:
        rows = _rows(conn.execute(
            "SELECT at, kind, detail FROM activity ORDER BY id DESC LIMIT ?", (limit,)))
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"] or "{}")
        except json.JSONDecodeError:
            r["detail"] = {}
    return rows


def stats():
    conn = connect()
    with _lock:
        runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        items = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        decisions = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
        suppliers = conn.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
    # In WAL mode most recent data sits in the -wal file until a checkpoint,
    # so reporting only the main file understates the size on disk.
    size = sum(p.stat().st_size for p in (
        DB_PATH, DB_PATH.with_suffix(".db-wal"), DB_PATH.with_suffix(".db-shm"))
        if p.exists())
    return {"runs": runs, "items": items, "decisions": decisions,
            "suppliers": suppliers, "db_bytes": size, "db_path": str(DB_PATH)}
