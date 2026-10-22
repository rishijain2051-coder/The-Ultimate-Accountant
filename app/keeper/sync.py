"""The Keeper's sync, on the office server: every company in Tally, read, sealed and sent.

Each run, for every company Tally has loaded:
    office/current/<cid>.ukeep          the newest snapshot
    office/daily/<cid>/<date>.ukeep     one a day; R2 keeps them 400 days
and office/index.ukeep, the list of companies. Files are sealed (.ukeep, the current
data key) into the outbox first, then sent; anything not sent stays for the next run.

Companies get opaque ids (companies.json), so their names are never in R2 paths.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse

from app.keeper import backups, books, notices, ukeep
from app.office import keys, settings
from app.office.cloud import Cloud
from app.office.identity import Identity

EVERY = 2 * 3600
_now = threading.Event()          # set by Sync now: the quick read
_full = threading.Event()         # set by Read everything now
_busy = threading.Lock()


def _dir() -> Path:
    d = settings.office_dir() / "keeper"
    (d / "outbox").mkdir(parents=True, exist_ok=True)
    return d


def company_ids(names: list[str]) -> dict[str, str]:
    path = _dir() / "companies.json"
    ids = settings.read_json(path) or {}
    for n in names:
        ids.setdefault(n, "c" + secrets.token_hex(6))
    settings.write_json(path, ids)
    return ids


def _seal(path: str, obj: dict, kind: str, kid: str, dek: bytes, **meta) -> None:
    out = _dir() / "outbox" / path
    out.parent.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(), 6)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(ukeep.encrypt(data, dek, kid, kind, gz=True, **meta))
    tmp.replace(out)


def send_outbox(cloud: Cloud) -> int:
    """Everything waiting, four at a time; the index last, once what it lists is there."""
    box = _dir() / "outbox"

    def send(f: Path) -> None:
        path = f.relative_to(box).as_posix()
        url = cloud.post("/api/office/upload-url", {"path": path})["url"]
        with f.open("rb") as body:                    # streamed: a Tally backup can be big
            r = httpx.put(url, content=body, headers={"Content-Length": str(f.stat().st_size)}, timeout=600)
        r.raise_for_status()
        f.unlink()

    files = list(box.rglob("*.ukeep"))
    rest = [f for f in files if f.name != "index.ukeep"]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(send, rest))                    # the first failure is raised here; the rest stay for next time
    for f in files:
        if f.name == "index.ukeep":
            send(f)
    return len(files)


def _kept(cid: str) -> Path:
    return _dir() / "kept" / f"{cid}.ukeep"


def kept_snapshot(cid: str) -> dict | None:
    """The last snapshot of a company, kept sealed on this PC for the next quick read."""
    try:
        return json.loads(gzip.decompress(ukeep.decrypt(_kept(cid).read_bytes(), keys.dek)[1]))
    except (OSError, ValueError, KeyError):
        return None


def run(reader: books.Reader, cloud: Cloud | None, today: dt.date | None = None, full: bool = False) -> dict:
    """One sync: quick (only what changed) unless full. Returns what it did, also kept in
    status.json for the Keeper page."""
    today = today or dt.date.today()
    status = {"started": time.time(), "companies": [], "error": None, "step": "starting",
              "kind": "full" if full else "quick"}

    def step(text: str) -> None:              # kept on disk before each read: a crash still leaves it
        status["step"] = text
        try:
            settings.write_json(_dir() / "status.json", status)
        except OSError:
            pass                              # only the progress line; never a reason to stop reading Tally

    reader.step = step
    snaps, kid, dek = [], None, None
    try:
        cos = reader.companies()
        ids = company_ids([c["name"] for c in cos])
        kid, dek = keys.current()
        # Every company ever sent stays listed, each with when it was last read: one closed
        # in Tally today keeps its last copy in the books.
        known = settings.read_json(_dir() / "index.json") or {}
        for c in cos:
            cid = ids[c["name"]]
            base = None if full else kept_snapshot(cid)
            snap = reader.snapshot(c, today, base)
            snap["company"]["cid"] = cid
            snaps.append(snap)
            same = base is not None and snap["alter"] == base.get("alter") and snap["vouchers"] is base["vouchers"]
            sent_today = (known.get(cid) or {}).get("sent_on") == today.isoformat()
            if not (same and sent_today):
                _kept(cid).parent.mkdir(exist_ok=True)
                data = gzip.compress(json.dumps(snap, separators=(",", ":"), ensure_ascii=False).encode(), 6)
                _kept(cid).with_suffix(".tmp").write_bytes(ukeep.encrypt(data, dek, kid, "kept", gz=True, cid=cid))
                _kept(cid).with_suffix(".tmp").replace(_kept(cid))
                for path in (f"office/current/{cid}.ukeep", f"office/daily/{cid}/{today}.ukeep"):
                    _seal(path, snap, "books", kid, dek, cid=cid)
            row = {"cid": cid, "name": c["name"], "read_at": snap["read_at"], "full_read_at": snap["full_read_at"],
                   "how": reader.how,
                   "books_from": snap["company"]["books_from"], "fy_from": snap["company"]["fy_from"],
                   "vouchers": len(snap["vouchers"]), "ledgers": len(snap["ledgers"]), "bills": len(snap["bills"]),
                   "sent_on": today.isoformat()}
            known[cid] = row
            settings.write_json(_dir() / "index.json", known)
        index = sorted(known.values(), key=lambda r: r["name"].lower())
        status["companies"] = index
        status["backups_sent"] = len(backups.seal_new(kid, dek, step))
        status["backup"] = backups.state()
        _seal("office/index.ukeep", {"v": 1, "companies": index, "at": status["started"],
                                     "backup": {k: status["backup"][k] for k in ("newest", "old", "sent")}},
              "index", kid, dek)
        status["sent"] = send_outbox(cloud) if cloud else 0
    except Exception as exc:  # noqa: BLE001 - said on the Keeper page; the next run tries again
        status["error"] = f"{type(exc).__name__}: {exc}"
    else:
        status["step"] = None
    notices.after_sync(cloud, status, snaps, kid, dek, today)
    status["finished"] = time.time()
    settings.write_json(_dir() / "status.json", status)
    return status


def background(stop: threading.Event) -> None:
    """On the office server. Sync now: the quick read. Read everything now, and every two hours
    once an admin's sync has gone through cleanly: the full read. Tally is the one on this PC."""
    ident = Identity.load("office_server")
    if not ident:
        return
    cloud = Cloud(ident)
    timer = False
    # At start (the sign-in task): a quick read in 2 minutes, again every 10 until one goes through.
    first = time.time() + 120 if settings.load().get("keeper_auto") else None
    while not stop.is_set():
        start = first is not None and time.time() >= first
        quick, full = _now.is_set() or start, _full.is_set() or timer
        if quick or full:
            _now.clear()
            _full.clear()
            with _busy:
                done = run(books.Reader(pause=0.0 if not full else 0.2), cloud, full=full)
            if not done["error"] and not settings.load().get("keeper_auto"):
                s = settings.load()
                s["keeper_auto"] = True
                settings.save(s)
            if start:
                first = time.time() + 600 if done["error"] else None
        timer = False
        for _ in range(EVERY):
            if stop.is_set() or _now.is_set() or _full.is_set() or (first is not None and time.time() >= first):
                break
            stop.wait(1)
        else:
            timer = bool(settings.load().get("keeper_auto"))


def once() -> int:
    """--keeper-sync, from the task: the full read, the timer's, once an admin's sync went
    through cleanly. The launcher has already left it alone while the app itself is open."""
    if settings.mode() != "server" or not settings.load().get("keeper_auto"):
        return 0
    ident = Identity.load("office_server")
    if not ident:
        return 0
    with _busy:
        done = run(books.Reader(pause=0.2), Cloud(ident), full=True)
    return 1 if done["error"] else 0


router = APIRouter()


@router.post("/keeper/sync")
def sync_now(full: bool = False):
    (_full if full else _now).set()
    return {"ok": True}


class Folder(BaseModel):
    folder: str


class Restore(BaseModel):
    path: str
    folder: str


@router.post("/keeper/backup-folder")
def backup_folder(body: Folder):
    f = Path(body.folder.strip().strip('"'))
    if not f.is_dir():
        raise HTTPException(400, f"There's no folder {f} on this PC.")
    s = settings.load()
    s["tally_backup_dir"] = str(f)
    settings.save(s)
    return {"ok": True, "found": len(backups.found(f))}


@router.get("/keeper/backups")
def backup_list():
    return {"state": backups.state(), "sent": sorted(backups.manifest(), key=lambda b: b["taken"], reverse=True)}


@router.post("/keeper/restore")
def restore(body: Restore):
    ident = Identity.load("office_server")
    if not ident:
        raise HTTPException(400, "This PC isn't the office server.")
    try:
        return {"file": str(backups.restore(Cloud(ident), body.path, body.folder.strip().strip('"')))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/keeper/status")
def status():
    return {"status": settings.read_json(_dir() / "status.json"), "busy": _busy.locked(),
            "waiting": sum(1 for _ in (_dir() / "outbox").rglob("*.ukeep")),
            "auto": bool(settings.load().get("keeper_auto"))}


@router.get("/keeper/people")
def people():
    """Who has access, from the newest roster. Names, status, access and device kinds only:
    never keys, fingerprints or approvals."""
    from app.office import roster
    r = roster.current()
    if r is None:
        return {"people": [], "known": False}
    out = []
    for u in r.users.values():
        devs = [{"name": d.get("name") or "", "kind": d.get("kind"), "status": d.get("status")}
                for d in r.devices.values() if d.get("user_id") == u["id"]]
        out.append({"name": u.get("name") or "", "email": u.get("email") or "", "status": u.get("status"),
                    "caps": [str(c) for c in u.get("caps") or []], "devices": devs})
    return {"people": out, "known": True}


@router.get("/keeper", response_class=HTMLResponse)
def page():
    from app.keeper.page import HTML
    return HTML
