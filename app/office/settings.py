"""Where the office's state lives, and the few settings it has.

%APPDATA%\\Ultimate Accountant\\office\\ (ASSURANCE_OFFICE_DIR, or the office\\ folder of a trial
run's ASSURANCE_DATA_DIR, instead):
    settings.json   mode, the control plane's address and token key, the office server's address
                    and certificate (on a client)
    server.json     this PC's identity as the office server (keys held with DPAPI)
    client.json     this PC's identity as the person using it
    roster.json     the last signed roster (server)
    sessions.json   office sessions, by the SHA-256 of their cookie (server)
    keyring.json    the Keeper's data keys, held with DPAPI, and the pinned admin keys (server)
    tls/            the office server's certificate and key (server)
"""

from __future__ import annotations

import json
import os
import threading
import time
import sys
from pathlib import Path


def _control_plane() -> str:
    """Your control plane (docs/configuration.md): UA_CONTROL_PLANE, else that line of
    deploy.env beside the program. The same file app/backend/deploy.py reads; read here
    on its own, as the office imports nothing of the rest of the app."""
    if os.environ.get("UA_CONTROL_PLANE"):
        return os.environ["UA_CONTROL_PLANE"]
    frozen = getattr(sys, "frozen", False)
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) if frozen else Path(__file__).resolve().parents[2]
    try:
        for line in (root / "deploy.env").read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "UA_CONTROL_PLANE" and value.strip():
                return value.strip().strip('"')
    except OSError:
        pass
    return "https://your-control-plane.example"


CONTROL_PLANE = _control_plane()
PORT = 8770
MODES = ("alone", "server", "client")


def office_dir() -> Path:
    if os.environ.get("ASSURANCE_OFFICE_DIR"):
        d = Path(os.environ["ASSURANCE_OFFICE_DIR"])
    elif os.environ.get("ASSURANCE_DATA_DIR"):          # a trial run: all of it in the one folder
        d = Path(os.environ["ASSURANCE_DATA_DIR"]) / "office"
    else:
        d = Path(os.environ.get("APPDATA") or Path.home()) / "Ultimate Accountant" / "office"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(name: str, default=None):
    try:
        return json.loads((office_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(name: str, data) -> None:
    """Written whole and then renamed, so a crash never leaves half a file."""
    p = office_dir() / name
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # one per writer
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    for i in range(20):
        try:
            tmp.replace(p)
            return
        except PermissionError:      # Windows: a reader (the Keeper page) or antivirus has it open a moment
            time.sleep(0.05 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"{p} stayed in use")


def load() -> dict:
    s = read_json("settings.json", {}) or {}
    s.setdefault("mode", "alone")
    s.setdefault("control_plane", CONTROL_PLANE)
    if s["mode"] not in MODES:
        s["mode"] = "alone"
    return s


def save(s: dict) -> None:
    write_json("settings.json", s)


def mode() -> str:
    return load()["mode"]
