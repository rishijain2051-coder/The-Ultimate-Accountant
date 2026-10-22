"""
Updates, from the app's release page on GitHub.

This is the one thing the app sends over the internet: a request for the newest
release on that page, made a little after the app starts and every few hours
while it stays open - only in the installed app, and only while the setting is
on. Nothing about anyone's data goes with it.

The release page is a public GitHub repository of the deployer's choosing
(UA_UPDATE_REPO, docs/configuration.md); nothing is needed to read it. A copy
built without one, or without the deployer's public key, never checks.

When the release names a newer version, its installer is downloaded, checked
against the fingerprint the release file gives for it, and kept until the user
presses Restart and update. A copy whose base - Python, the libraries, the OCR
models, what build.spec writes to _internal/BASE - is the one the release was
built on fetches Update.exe instead: the app's own code, a few MB. Any other
copy fetches the whole installer. The release file is signed with a key that never
leaves the publisher's own computer; one that fails the signature is ignored,
and a download that fails the fingerprint is thrown away - so neither someone
who can write to the release page nor a tampered page can put a program on
anybody's machine.
"""

import base64
import hashlib
import http.client
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from app.backend import deploy
from app.backend.paths import CACHE_DIR, FROZEN, RESOURCE_DIR
from app.backend.version import VERSION

REPO = deploy.get("update_repo")              # "owner/name" of the public release repository
# A test points this at a stand-in of its own; the signature is checked all the same.
API = os.environ.get("ASSURANCE_UPDATE_API") or "https://api.github.com"
# The publisher's Ed25519 public key, base64. Only a release file signed with the
# matching private key - kept in the publisher's profile, never in the program -
# is believed (packaging/publish_release.py --make-key makes the pair).
PUBLIC_KEY = deploy.get("update_public_key")
PRODUCT = "ultimate-accountant"      # what a signed release file must name
FOLDER = CACHE_DIR / "updates"
CONFIGURED = bool(REPO and PUBLIC_KEY)
# The installed app checks by itself; a copy run from the source only when told where.
AUTOMATIC = CONFIGURED and (FROZEN or bool(os.environ.get("ASSURANCE_UPDATE_API")))
FIRST_CHECK = 20                  # seconds after start, clear of the start-up work
EVERY = 6 * 3600                  # then every six hours while it stays open
TIMEOUT = 20                      # for an answer once connected
CONNECT_TIMEOUT = 4               # for each of a host's addresses to pick up
RELEASE_LIMIT = 256 * 1024
INSTALLER_LIMIT = 1024 ** 3
NEEDED = ("latest.json", "latest.json.sig")
BASE_FILE = RESOURCE_DIR / "BASE"        # what this copy's base is (build.spec)
BREAKAWAY = 0x01000000                   # CREATE_BREAKAWAY_FROM_JOB

_lock = threading.Lock()
_busy = threading.Lock()          # one check at a time
_state = {"status": "idle", "latest": None, "notes": "", "message": "", "checked_at": None,
          "path": None, "sha256": None, "size": None, "light": False}


class UpdateError(Exception):
    """Something to tell the user in plain words."""


def version_tuple(text):
    parts = str(text).strip().lstrip("v").split(".")
    if not all(p.isdigit() for p in parts):
        raise UpdateError(f"{text!r} is not a version number.")
    return tuple(int(p) for p in parts)


def verify(manifest, signature, public_key=PUBLIC_KEY):
    """What the release file says, if and only if its signature is good."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or ""))
    except ValueError:
        raise UpdateError("No valid signing key is set (UA_UPDATE_PUBLIC_KEY), so no release is believed.") from None
    try:
        key.verify(base64.b64decode(signature.strip()), manifest)
    except (InvalidSignature, ValueError):
        raise UpdateError("The release file failed its signature check, so it was ignored.") from None
    try:
        info = json.loads(manifest)
        version_tuple(info["version"])
        product = info.get("product")
        info["sha256"] = str(info["sha256"]).lower()
        info["size"] = int(info["size"])
        if info.get("update") is not None:
            u = info["update"]
            u["sha256"], u["size"] = str(u["sha256"]).lower(), int(u["size"])
            u["modules"] = [str(m) for m in u.get("modules") or []]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise UpdateError("The release file could not be read.") from None
    # One signing key may sign more than one product: another's releases must not pass as ours.
    if product != PRODUCT:
        raise UpdateError("The release file is not for Ultimate Accountant, so it was ignored.")
    return info


def installed_base(path=None):
    """This copy's base, as build.spec wrote it; None when run from the source."""
    try:
        return json.loads(Path(path or BASE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fits(base, update):
    """Whether the app's code alone can go over this copy: the same core, and
    every module the new code needs already in it (packaging/installer.fits)."""
    return bool(base and update and base.get("core") == update.get("core")
                and set(update.get("modules") or []) <= set(base.get("modules") or []))


def status():
    with _lock:
        out = {k: v for k, v in _state.items() if k not in ("path", "sha256")}
    out.update(current=VERSION, automatic=AUTOMATIC, can_install=FROZEN)
    return out


def check(api=None, public_key=None, folder=FOLDER, current=VERSION, base=None, repo=None):
    """Ask for the newest release and, when it is newer, fetch and check it:
    Update.exe when this copy stands on the base it was built on, else Install.exe."""
    repo = REPO if repo is None else repo
    public_key = PUBLIC_KEY if public_key is None else public_key
    if not (repo and public_key):
        return _set(status="failed", checked_at=_now(), message=(
            "This copy was built without an update repository and signing key, so it never "
            "updates itself (docs/configuration.md)."))
    if not _busy.acquire(blocking=False):
        return status()                                   # one is already running
    try:
        _set(status="checking", message="")
        assets = _release(api or API, repo)
        missing = [name for name in NEEDED if name not in assets]
        if missing:
            raise UpdateError("The newest release is missing " + ", ".join(missing) + ".")
        manifest = _fetch(assets["latest.json"], RELEASE_LIMIT)
        signature = _fetch(assets["latest.json.sig"], 4096).decode("ascii", "replace")
        info = verify(manifest, signature, public_key)
        if version_tuple(info["version"]) <= version_tuple(current):
            _clear(folder)
            return _set(status="current", latest=info["version"], notes="", path=None,
                        sha256=None, checked_at=_now())
        light = info.get("update") if "Update.exe" in assets and fits(
            installed_base() if base is None else base, info.get("update")) else None
        name = "Update.exe" if light else "Install.exe"
        if name not in assets:
            raise UpdateError(f"The newest release is missing {name}.")
        want = {**info, **({"sha256": light["sha256"], "size": light["size"]} if light else {})}
        path = _download(want, assets[name], folder, "Update" if light else "Install")
        return _set(status="ready", latest=info["version"], notes=info.get("notes", ""), path=str(path),
                    sha256=want["sha256"], size=want["size"], light=bool(light), checked_at=_now())
    except UpdateError as exc:
        return _set(status="failed", message=str(exc), checked_at=_now())
    except urllib.error.HTTPError as exc:
        return _set(status="failed", checked_at=_now(), message=_refusal(exc.code))
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return _set(status="offline", checked_at=_now(),
                    message="No internet connection, or GitHub could not be reached.")
    except Exception as exc:                              # noqa: BLE001 - the checks must keep running
        return _set(status="failed", checked_at=_now(),
                    message=f"The check went wrong ({type(exc).__name__}); the next one will try again.")
    finally:
        _busy.release()


def check_soon():
    """Check now, without keeping the caller waiting."""
    threading.Thread(target=check, daemon=True, name="update-check").start()


def start(enabled):
    """Check a little after start and every few hours after, while `enabled()` says so."""
    if not AUTOMATIC:
        return

    def loop():
        time.sleep(FIRST_CHECK)
        while True:
            if enabled():
                check()
            time.sleep(EVERY)

    threading.Thread(target=loop, daemon=True, name="updates").start()


def install():
    """Start the downloaded installer, which closes this copy, replaces it and
    opens the new one. It gets a console of its own, so its progress shows."""
    if not FROZEN:
        raise UpdateError("Only the installed app can update itself.")
    with _lock:
        ready, path, expected = _state["status"] == "ready", _state["path"], _state["sha256"]
    if not ready or not path or not Path(path).exists():
        raise UpdateError("There is no update ready to install.")
    if _sha256(Path(path)) != expected:                   # changed on disk since it was checked
        Path(path).unlink(missing_ok=True)
        _set(status="idle", path=None, sha256=None)
        raise UpdateError("The downloaded update changed after it was checked, so it was thrown away.")
    # Out of launch.py's job, which ends everything the app started when the app ends:
    # the installer has to outlive the copy it replaces.
    subprocess.Popen([path, "--reopen"], cwd=str(Path(path).parent),
                     creationflags=subprocess.CREATE_NEW_CONSOLE | BREAKAWAY, close_fds=True)


# ---------------------------------------------------------------------------

class _QuickConnect(http.client.HTTPSConnection):
    """Give each of a host's addresses a few seconds to pick up, not the whole
    timeout. GitHub's file storage answers on four, and from some networks one
    of them never does: tried in turn with 20 s each, that added 20 s to every
    file fetched. Once connected, the full timeout applies again."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self._connect

    @staticmethod
    def _connect(address, timeout, source_address=None):
        wait = timeout if isinstance(timeout, (int, float)) else None
        sock = socket.create_connection(address, min(CONNECT_TIMEOUT, wait or CONNECT_TIMEOUT), source_address)
        sock.settimeout(wait)
        return sock


class _Https(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_QuickConnect, req, context=self._context)


_opener = urllib.request.build_opener(_Https)


def _open(url, accept):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"UltimateAccountant/{VERSION}", "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28"})
    return _opener.open(req, timeout=TIMEOUT)


def _release(api, repo):
    """The newest release's files, by name, as the addresses to fetch them from."""
    with _open(f"{api}/repos/{repo}/releases/latest", "application/vnd.github+json") as r:
        data = r.read(RELEASE_LIMIT + 1)
    if len(data) > RELEASE_LIMIT:
        raise UpdateError("GitHub's description of the release is larger than it can be.")
    return {a["name"]: a["url"] for a in json.loads(data).get("assets", [])}


def _fetch(url, limit):
    with _open(url, "application/octet-stream") as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("The release file is larger than a release file can be.")
    return data


def _refusal(code):
    if code == 404:
        return "No release has been published yet."
    return f"GitHub answered {code}; the next check will try again."


def _set(**changes):
    with _lock:
        _state.update(changes)
    return status()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _download(info, url, folder, prefix="Install"):
    """The installer (or Update.exe) the release file describes, fetched once and kept."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{prefix}-{info['version']}.exe"
    if final.exists() and _sha256(final) == info["sha256"]:
        return final
    _set(status="downloading", latest=info["version"])
    part = final.with_suffix(".part")
    digest, size = hashlib.sha256(), 0
    limit = min(info["size"], INSTALLER_LIMIT)
    try:
        with _open(url, "application/octet-stream") as resp, open(part, "wb") as out:
            while chunk := resp.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise UpdateError("The download was larger than the release said, so it was thrown away.")
                digest.update(chunk)
                out.write(chunk)
        if size != info["size"] or digest.hexdigest() != info["sha256"]:
            raise UpdateError("The download did not match the release's fingerprint, so it was thrown away.")
        os.replace(part, final)
    finally:
        part.unlink(missing_ok=True)
    _clear(folder, keep=final)
    return final


def _clear(folder, keep=None):
    """Installers and updates from earlier checks: no longer wanted once superseded."""
    for old in [*folder.glob("Install-*"), *folder.glob("Update-*")]:
        if old != keep:
            old.unlink(missing_ok=True)
