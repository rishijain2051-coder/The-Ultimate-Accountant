"""
The AI checker's engine: a local AI model, fetched onto this computer only
when the user asks for it, and run in the background.

It is an add-on, never part of the app's installer or its updates. From
Settings > AI checker (or the Invoice Bridge's Settings > Local AI model) the app
downloads, into %LOCALAPPDATA%\\Ultimate Accountant\\ai:

  - the Ollama runtime, from Ollama's own GitHub releases, checked against the
    checksum published with it (the graphics libraries for old NVIDIA drivers
    are dropped: CUDA 13 and Vulkan cover the rest), and
  - the model, through Ollama's own download.

About 5 GB comes down, once; about 4 GB stays. A download cut off carries on
where it stopped. The one runtime serves the whole app: this module's AI checker
and the Invoice Bridge's reader (app/bridge/services/llm.py) alike.

One copy serves this app and the two it was made from, Assurance Console and
Invoice Bridge, where they are still installed: an app with a complete
copy says where it is in %LOCALAPPDATA%\\Shared AI\\<its name>.json, and an app
without one runs the other's instead of downloading it again - as a process
of its own, on its own port, from files it only reads: it never downloads
into, writes to or deletes from the other's copy. An Ollama already running
on the machine - another app's included - is left alone.

While the app runs, the runtime runs beside it: at idle priority, held to at
most half the processor by a Windows job object, and gone the moment the app
is (the job closes with it). The model is loaded only while it is reading.

Nothing leaves the computer: the runtime listens on 127.0.0.1 only, and the
only things fetched are the runtime and the model. What the model is asked to
do is fixed in app/backend/ai_check.py.
"""

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from app.backend.paths import CACHE_DIR

OLLAMA_VERSION = "v0.34.4"           # pinned, as the bridge pins it: tested with the model below
MODEL = "qwen3.5:4b"                 # reads printed text and page images, English and Hindi
RELEASES = os.environ.get("ASSURANCE_AI_RELEASES") or "https://github.com/ollama/ollama/releases/download"
FOLDER = Path(os.environ.get("ASSURANCE_AI_DIR") or CACHE_DIR / "ai")
# Where the apps that run this same runtime and model say where their complete copy is,
# one file each. A copy kept elsewhere for a test or a trial run is never offered there.
SHARED = (Path(os.environ["ASSURANCE_SHARED_AI"]) if os.environ.get("ASSURANCE_SHARED_AI")
          else None if os.environ.get("ASSURANCE_AI_DIR") else CACHE_DIR.parent / "Shared AI")
APP = "Ultimate Accountant"          # this app's file there is "Ultimate Accountant.json"
# A runtime already running somewhere to use instead of our own: for tests and trial runs only.
GIVEN = os.environ.get("ASSURANCE_AI_URL") or ""
NEED_FREE = 8 * 1024 ** 3            # room for the download and what it unpacks to
PORTS = range(11500, 11600)          # clear of Ollama's own 11434 and the bridge's 11437-11499
CPU_SHARE = 50                       # percent of the processor the runtime may ever use
NUM_CTX = 16384                      # one size for every call: a page image needs the room

_lock = threading.Lock()
_state = {"status": "absent", "done": 0, "total": 0, "step": "", "message": ""}
_proc = {"p": None, "port": None, "job": None, "caps": None, "copy": None}


def runtime_dir():
    return FOLDER / "ollama"


def models_dir():
    return FOLDER / "models"


def _own():
    return {"app": None, "runtime": runtime_dir() / "ollama.exe", "models": models_dir()}


def _complete(c):
    """Both halves on disk: the runtime, and the model's marker written after its download."""
    try:
        return c["runtime"].is_file() and (c["models"] / "MODEL").read_text(encoding="utf-8-sig").strip() == MODEL
    except OSError:
        return False


def others():
    """Complete copies of this same runtime and model that other apps list in the shared
    folder, as {"app", "runtime", "models"}."""
    try:
        notes = sorted(SHARED.glob("*.json")) if SHARED else []
    except OSError:
        notes = []
    for note in notes:
        if note.stem.lower() == APP.lower():
            continue
        try:
            d = json.loads(note.read_text(encoding="utf-8-sig"))
            c = {"app": note.stem, "runtime": Path(d["runtime"]), "models": Path(d["models"])}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if ((d.get("model"), d.get("ollama")) == (MODEL, OLLAMA_VERSION) and c["runtime"].name.lower() == "ollama.exe"
                and c["runtime"].is_absolute() and c["models"].is_absolute() and _complete(c)):
            yield c


def to_run():
    """The copy to run: this app's own when it is complete, else another app's, else None."""
    own = _own()
    return own if _complete(own) else next(others(), None)


def installed():
    return bool(GIVEN) or to_run() is not None


def status():
    with _lock:
        out = dict(_state)
    c = to_run()
    if out["status"] in ("absent", "ready") and not _busy.locked():
        out["status"] = "ready" if c or GIVEN else "absent"
    theirs = c if c and c["app"] else None
    out.update(model=MODEL, runtime=OLLAMA_VERSION, folder=str(FOLDER), running=bool(_url()),
               # our folder: our copy, or - running another app's - a download of ours never finished
               size=_folder_size() if out["status"] == "ready" else None,
               shared=_where(theirs),                                         # runs on another app's copy
               also=_where(next(others(), None)) if c and not theirs else None)   # has one of its own too
    return out


def _where(c):
    """Whose copy, and the folder that app keeps it in (the one holding the runtime's folder)."""
    return {"app": c["app"], "folder": str(c["runtime"].parent.parent)} if c else None


def announce():
    """Say in the shared folder where this app's complete copy is, so the other app runs
    it rather than download its own; take the note away when there is none. At every
    start, and when a download finishes."""
    if not SHARED:
        return
    note, own = SHARED / f"{APP}.json", _own()
    try:
        if not _complete(own):
            note.unlink(missing_ok=True)
            return
        SHARED.mkdir(parents=True, exist_ok=True)
        part = SHARED / f"{APP}.part"                 # only *.json is read, so never half-written
        part.write_text(json.dumps({"runtime": str(own["runtime"]), "models": str(own["models"]),
                                    "model": MODEL, "ollama": OLLAMA_VERSION}), encoding="ascii")
        part.replace(note)
    except OSError:
        pass                                          # the other app downloads its own, as before


def _set(**changes):
    with _lock:
        _state.update(changes)


def _folder_size():
    try:
        return sum(p.stat().st_size for p in FOLDER.rglob("*") if p.is_file())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Getting it: the runtime, then the model
# ---------------------------------------------------------------------------

_busy = threading.Lock()


def get_soon():
    """Fetch and set up the AI checker in the background; the state says how far it has got."""
    if _busy.locked():
        return status()
    _set(status="downloading", done=0, total=0, step="the AI runtime", message="")
    threading.Thread(target=get, daemon=True, name="ai-get").start()
    return status()


def get():
    if not _busy.acquire(blocking=False):
        return status()
    try:
        FOLDER.mkdir(parents=True, exist_ok=True)
        if not (runtime_dir() / "ollama.exe").exists():
            free = shutil.disk_usage(FOLDER).free
            if free < NEED_FREE:
                raise AiError(f"The AI checker needs about 8 GB free on {FOLDER.anchor}, and there is "
                              f"{free / 1024 ** 3:.1f} GB.")
            _get_runtime()
        if not _complete(_own()):
            _set(status="downloading", step="the AI model", done=0, total=0)
            _pull()
        _set(status="ready", step="", message="", done=0, total=0)
        announce()
    except AiError as exc:
        _set(status="failed", message=str(exc))
    except (urllib.error.URLError, OSError) as exc:
        _set(status="failed", message=f"The download did not get through ({exc.__class__.__name__}) - is the "
                                      "internet on? Try again: it carries on where it stopped.")
    except Exception as exc:                                   # noqa: BLE001 - say so, never crash the app
        _set(status="failed", message=f"Setting up the AI checker went wrong ({exc.__class__.__name__}).")
    finally:
        _busy.release()
    return status()


class AiError(Exception):
    """Something to tell the user in plain words."""


def _get_runtime():
    downloads = FOLDER / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    base = f"{RELEASES}/{OLLAMA_VERSION}"
    sums = _fetch(f"{base}/sha256sum.txt", downloads / f"ollama-{OLLAMA_VERSION}-sha256sum.txt", quiet=True)
    want = next((ln.split()[0].lower() for ln in sums.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if ln.strip().endswith("ollama-windows-amd64.zip")), None)
    if not want:
        raise AiError("Ollama's published checksums do not list the Windows runtime.")
    zipped = _fetch(f"{base}/ollama-windows-amd64.zip", downloads / f"ollama-{OLLAMA_VERSION}-windows-amd64.zip")
    _set(status="unpacking", step="the AI runtime")
    if _sha256(zipped) != want:
        zipped.unlink(missing_ok=True)
        raise AiError("The runtime did not match the checksum Ollama published for it, so it was deleted. "
                      "Try again.")
    part = FOLDER / "ollama.part"
    shutil.rmtree(part, ignore_errors=True)
    with zipfile.ZipFile(zipped) as z:            # extractall keeps every name inside `part`
        z.extractall(part)
    # CUDA 13 covers NVIDIA drivers from 580 on and older cards use Vulkan, so
    # CUDA 12 would only take 1.1 GB.
    for old in (part / "lib" / "ollama").glob("cuda_v12*"):
        shutil.rmtree(old, ignore_errors=True)
    if not (part / "ollama.exe").exists():
        shutil.rmtree(part, ignore_errors=True)
        raise AiError("ollama.exe was not in the runtime's archive.")
    part.rename(runtime_dir())                    # only a complete unpack is ever called ollama
    shutil.rmtree(downloads, ignore_errors=True)


def _fetch(url, dest, quiet=False):
    """Download to `dest`, carrying on from a part left by an earlier try."""
    if dest.exists():
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "UltimateAccountant-AI",
                                               **({"Range": f"bytes={have}-"} if have else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:              # the server would not resume: start again
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        with open(part, "ab" if have else "wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
                have += len(chunk)
                if not quiet:
                    _set(done=have, total=total)
    part.replace(dest)
    return dest


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            digest.update(chunk)
    return digest.hexdigest()


def _pull():
    """The model, through the runtime's own download, into the app's model folder."""
    url = start(_own())
    if not url:
        raise AiError("The AI runtime did not start.")
    req = urllib.request.Request(f"{url}/api/pull", data=json.dumps({"model": MODEL, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    layers = {}
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("error"):
                raise AiError(f"The model could not be downloaded: {msg['error']}")
            if msg.get("digest") and msg.get("total"):
                layers[msg["digest"]] = (int(msg.get("completed") or 0), int(msg["total"]))
                _set(done=sum(d for d, _ in layers.values()), total=sum(t for _, t in layers.values()))
            if msg.get("status") == "success":
                models_dir().mkdir(parents=True, exist_ok=True)
                (models_dir() / "MODEL").write_text(MODEL, encoding="ascii")
                return
    raise AiError("The model download stopped before it finished. Try again: it carries on where it stopped.")


def remove():
    """Take this app's copy off the computer. Its note in the shared folder goes first,
    so the other app stops counting on it; then whatever runs from the copy - the other
    app may be running it too, and a file in use would survive as a broken copy."""
    if _busy.locked():
        raise AiError("The AI checker is still being set up; wait for it, then remove it.")
    try:
        if SHARED:
            (SHARED / f"{APP}.json").unlink(missing_ok=True)
    except OSError:
        pass
    stop()
    _stop_all_in(FOLDER)
    shutil.rmtree(FOLDER, ignore_errors=True)
    _set(status="absent", done=0, total=0, step="", message="")
    return status()


# ---------------------------------------------------------------------------
# Running it: a runtime of our own, in the background
# ---------------------------------------------------------------------------

def _url():
    p = _proc["p"]
    return f"http://127.0.0.1:{_proc['port']}" if p is not None and p.poll() is None else None


def _free_port():
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def _answers(url, timeout=2):
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=timeout):
            return True
    except OSError:
        return False


def start(c=None):
    """The runtime's address, starting it if it is not running: from copy `c`, or the
    copy there is to run (to_run()). None when it cannot be."""
    if GIVEN:
        return GIVEN.rstrip("/")
    c = c or to_run()
    if not c:
        return None
    with _lock:
        url = _url()
        if url and _proc["copy"] == c:
            return url
        stop()                                         # one from another copy, or one that has gone
        port = _free_port()
        if not c["runtime"].exists() or port is None:
            return None
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(c["models"]),
               # Ollama otherwise deletes download pieces and unused files from the model folder
               # at every start: a download cut off would start again, and the other app's copy,
               # when it is theirs, would lose files.
               "OLLAMA_NOPRUNE": "1",
               # Kept loaded between bills: the Invoice Bridge reads them one by one as they come.
               "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_KEEP_ALIVE": "30m"}
        flags = 0x08000000 | 0x00000040                # no window; idle priority, so the user's work comes first
        p = subprocess.Popen([str(c["runtime"]), "serve"], env=env, creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _proc.update(p=p, port=port, job=_contain(p), copy=c)
        url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if _answers(url):
            return url
        if p.poll() is not None:
            return None
        time.sleep(0.5)
    stop()                                             # never answered: it is started again next time
    return None


def address():
    """The runtime's address, started if need be; None when there is none to run, or while
    it is being set up. What the AI checker and the Invoice Bridge's reader (app/bridge) ask."""
    return start() if installed() and not _busy.locked() else None


def stop():
    """Stop the runtime and the model runner it started: the whole job, not its first process alone."""
    p, job = _proc["p"], _proc["job"]
    if job:
        try:
            import ctypes
            from ctypes import wintypes
            ctypes.windll.kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(job))
        except Exception:                            # noqa: BLE001 - not Windows: the process alone
            pass
    if p is not None and p.poll() is None:
        p.kill()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    _proc.update(p=None, port=None, job=None, caps=None, copy=None)


def _stop_all_in(folder):
    """Stop every program running from `folder`: ours, and one the other app started from it."""
    if os.name != "nt" or not Path(folder).exists():
        return
    where = (str(Path(folder).resolve()).rstrip("\\") + "\\").replace("'", "''")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "$p = Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith("
                        f"'{where}', [StringComparison]::OrdinalIgnoreCase) }}; "
                        "if ($p) { $p | Stop-Process -Force; $p | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue }"],
                       capture_output=True, creationflags=0x08000000, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _contain(p):
    """Put the runtime in a Windows job object: it and the readers it starts die
    with the app (or stop()), run at idle priority, and together never take more
    than CPU_SHARE of the processor."""
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class CpuRate(ctypes.Structure):
            _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000               # kill on job close
        k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        # One priority for all of it, idle: the model runner it starts asks for above normal.
        limits.BasicLimitInformation.LimitFlags |= 0x20
        limits.BasicLimitInformation.PriorityClass = 0x40
        k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        cpu = CpuRate(0x1 | 0x4, CPU_SHARE * 100)                     # enabled, hard cap; in 1/100 of a percent
        k32.SetInformationJobObject(job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu))
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(p._handle)))
        return job                                   # kept open for as long as the app runs
    except Exception:                                # noqa: BLE001 - not Windows: idle priority alone
        return None


# ---------------------------------------------------------------------------
# Asking it
# ---------------------------------------------------------------------------

def _capabilities(url):
    if _proc["caps"] is None:
        try:
            req = urllib.request.Request(f"{url}/api/show", data=json.dumps({"model": MODEL}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                _proc["caps"] = frozenset(json.load(r).get("capabilities") or ())
        except (OSError, ValueError):
            return frozenset()
    return _proc["caps"]


class Stopped(AiError):
    """The runtime is not there to ask, or stopped while it was answering - the other
    app's installer stops a runtime run from its copy. Nothing is wrong with what was
    asked: ask again later."""


def _gone():
    """The runtime has stopped - given a moment, as its connection can drop just before it goes."""
    p = _proc["p"]
    try:
        return p is None or p.wait(timeout=2) is not None
    except subprocess.TimeoutExpired:
        return False


def chat(system, prompt, schema, images=(), timeout=600):
    """The model's answer, held by Ollama to `schema` while it writes, as a dict;
    None when it is too slow or answers out of shape. Raises Stopped when there is
    no runtime to ask (none, being set up, or stopped part-way)."""
    url = address()
    if not url:
        raise Stopped("The AI runtime is not running.")
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [base64.b64encode(b).decode("ascii") for b in images]
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, msg], "stream": False,
            "format": schema,
            # Deterministic: the same page must read the same way twice.
            # The same threads as the bridge's reader (CPU_THREADS, from launch.py): Ollama
            # loads the model again whenever the thread count changes.
            "options": {"temperature": 0, "num_ctx": NUM_CTX,
                        "num_thread": int(os.environ.get("CPU_THREADS") or 0)
                        or max(1, (os.cpu_count() or 2) * CPU_SHARE // 100)}}
    if "thinking" in _capabilities(url):
        body["think"] = False                     # copying off a page needs no reasoning pass
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = (json.load(r).get("message") or {}).get("content")
        data = json.loads(content or "")
    except (OSError, ValueError):
        if not GIVEN and _gone():
            raise Stopped("The AI runtime stopped while it was reading.") from None
        return None
    return data if isinstance(data, dict) else None
