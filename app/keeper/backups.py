"""Tally's own backups, sealed and sent to R2; and a restore of one, into an empty folder.

The Keeper sends the files Tally's Backup writes (TallyPrime 7 can schedule them daily,
even while Tally is closed); it never copies Tally's live data folder, which can be caught
half-written. Each sync sends any backup file that is new or changed since it was last sent:
    office/backups/<file's time>/<file name>.ukeep
and office/backups.ukeep, the list of every backup sent, with each one's SHA-256.

Restore takes one back: downloaded, opened, checked against its SHA-256, into a folder the
admin names, which must be empty, so it can never land on top of Tally's own data. Tally's
Data > Restore then reads it from there.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import zlib
from pathlib import Path

import httpx

from app.keeper import ukeep
from app.office import keys, settings

# TallyPrime 7: TDBK1800_<company>.001 (one-off), TSDBK1800_... (scheduled); 6.2 and earlier
# TBK1800_...; Tally.ERP 9 TBK900.001. A clash adds _1, _2; big ones go on in .002, .003.
NAME = re.compile(r"T(S?D)?BK\d+(_\d+)*\.\d{3}", re.I)
OLD_AFTER_DAYS = 7
MAX_LIST_BYTES, MAX_LIST_PLAIN = 8 << 20, 64 << 20   # the backups list as downloaded, and unzipped


def _dir() -> Path:
    return settings.office_dir() / "keeper"


def folder() -> Path | None:
    f = settings.load().get("tally_backup_dir")
    return Path(f) if f else None


def guess() -> str | None:
    """Where Tally keeps its backups, from tally.ini, when it says."""
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        for ini in Path(root or ".").glob("Tally*/tally.ini"):
            try:
                for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
                    k, _, v = line.partition("=")
                    if "backup" in k.lower() and v.strip() and Path(v.strip()).is_dir():
                        return v.strip()
            except OSError:
                continue
    return None


def found(where: Path | None = None) -> list[Path]:
    where = where or folder()
    if not where or not where.is_dir():
        return []
    return sorted((p for p in where.iterdir() if p.is_file() and NAME.fullmatch(p.name)), key=lambda p: p.stat().st_mtime)


def manifest() -> list[dict]:
    return settings.read_json(_dir() / "backups.json") or []


def seal_new(kid: str, dek: bytes, step=lambda text: None) -> list[dict]:
    """Seal into the outbox every backup file not sent as it is now. Returns the new rows."""
    sent = manifest()
    have = {(b["name"], b["mtime"], b["size"]) for b in sent}
    new = []
    for f in found():
        st = f.stat()
        if (f.name, int(st.st_mtime), st.st_size) in have:
            continue
        step(f"Tally backup {f.name}")
        stamp = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y%m%d-%H%M%S")
        path = f"office/backups/{stamp}/{f.name}.ukeep"
        out = _dir() / "outbox" / path
        out.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()

        class Hashing:                                 # the plain bytes' SHA-256, on the way through
            def __init__(self, src):
                self.src = src

            def read(self, n=-1):
                b = self.src.read(n)
                h.update(b)
                return b

        with f.open("rb") as src, out.with_suffix(".tmp").open("wb") as dst:
            ukeep.write(dst, Hashing(src), dek, kid, "backup", name=f.name)
        out.with_suffix(".tmp").replace(out)
        new.append({"name": f.name, "path": path, "mtime": int(st.st_mtime), "size": st.st_size,
                    "sha256": h.hexdigest(), "taken": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    if new:
        sent += new
        settings.write_json(_dir() / "backups.json", sent)
    if sent:
        data = gzip.compress(json.dumps({"v": 1, "backups": sent}, separators=(",", ":")).encode())
        tmp = _dir() / "outbox" / "office" / "backups.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(ukeep.encrypt(data, dek, kid, "backups", gz=True))
        tmp.replace(tmp.with_suffix(".ukeep"))
    return new


def state() -> dict:
    """What the Keeper page and the books say about backups."""
    f, sent = folder(), manifest()
    newest = max((b["taken"] for b in sent), default=None)
    old = newest is None or dt.datetime.fromisoformat(newest) < dt.datetime.now() - dt.timedelta(days=OLD_AFTER_DAYS)
    return {"folder": str(f) if f else None, "folder_ok": bool(f and f.is_dir()), "guess": None if f else guess(),
            "sent": len(sent), "newest": newest, "old": old}


def restore(cloud, path: str, into: str) -> Path:
    """One sent backup, back as Tally wrote it, into an empty folder. Returns the file."""
    row = next((b for b in manifest() + remote_manifest(cloud) if b["path"] == path), None)
    if not row:
        raise ValueError("That backup isn't in the list of those sent.")
    name = row["name"]
    if not isinstance(name, str) or not NAME.fullmatch(name) or Path(name).name != name:
        raise ValueError("That backup has a file name that isn't a Tally backup's, so it was not restored.")
    dest = Path(into)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} isn't empty. Restore goes only into an empty folder, never over Tally's own data.")
    dest.mkdir(parents=True, exist_ok=True)
    url = cloud.post("/api/office/download-url", {"path": path})["url"]
    sealed = dest / (name + ".ukeep.part")
    with httpx.stream("GET", url, timeout=300) as r, sealed.open("wb") as out:
        r.raise_for_status()
        for b in r.iter_bytes(1 << 20):
            out.write(b)
    target, h = dest / name, hashlib.sha256()
    part_out = dest / (name + ".part")        # only renamed to the real name once the SHA-256 matches
    try:
        with sealed.open("rb") as src, part_out.open("wb") as out:
            _, parts = ukeep.chunks(src, keys.dek)
            for part in parts:
                h.update(part)
                out.write(part)
    except BaseException:
        part_out.unlink(missing_ok=True)
        raise
    finally:
        sealed.unlink(missing_ok=True)
    if h.hexdigest() != row["sha256"]:
        part_out.unlink(missing_ok=True)
        raise ValueError("The restored file doesn't match what was sent (SHA-256), so it was removed.")
    part_out.replace(target)
    return target


def remote_manifest(cloud) -> list[dict]:
    """The list kept in R2: what a new office server, after losing this one, restores from."""
    try:
        url = cloud.post("/api/office/download-url", {"path": "office/backups.ukeep"})["url"]
        body = bytearray()
        with httpx.stream("GET", url, timeout=60) as r:
            r.raise_for_status()
            for b in r.iter_bytes(1 << 16):
                body += b
                if len(body) > MAX_LIST_BYTES:
                    raise ValueError("The backups list is too big.")
        d = zlib.decompressobj(31)                 # gzip; stops at the cap instead of filling memory
        plain = d.decompress(ukeep.decrypt(bytes(body), keys.dek)[1], MAX_LIST_PLAIN + 1)
        if len(plain) > MAX_LIST_PLAIN:
            raise ValueError("The backups list is too big.")
        return json.loads(plain)["backups"]
    except Exception:  # noqa: BLE001 - none sent yet, or offline: the local list stands
        return []
