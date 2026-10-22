"""
Launcher for Ultimate Accountant.

Starts the local server on a free port - both modules, Reconciliation and
Exports at / and the Invoice Bridge at /bridge/ (app/main.py) - and shows the
interface in the app's own window - drawn by WebView2, the browser engine
built into Windows - so it opens and closes like any other program. Where
WebView2 is missing, the interface opens in the browser instead. Started a
second time, it brings the open window to the front rather than starting
another copy. Nothing leaves this machine except the update check
(app/backend/updates.py).

The whole app - the server, the OCR and the AI runtime alike - is held to half
of the computer (limit_cpu below), as Invoice Bridge measured it.

This is also the entry point of the packaged build, which runs with no console
attached: anything it needs to say before the window opens has to go to a
dialog box instead of stdout, or the program would appear to do nothing at all.
"""

import json
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from app.backend.paths import APP_DIR, CACHE_DIR, FROZEN, RESOURCE_DIR, DataFolderMissing, data_dir

sys.path.insert(0, str(RESOURCE_DIR))

# A windowed build has no standard streams at all, and uvicorn's logger writes
# to one. Without somewhere for those writes to go, the first log line takes
# the whole program down - so give them a sink before anything starts.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

HOST = "127.0.0.1"
# How the server is run: changed by office_server_mode() on the office server.
SERVE = {"host": HOST}
PINNED = ""                                   # the one certificate the window accepts (office modes)
# Not Assurance Console's 8000 or Invoice Bridge's 8765: either may still be installed.
PREFERRED_PORT = 8770
TITLE = "Ultimate Accountant"
# Invoice Bridge on its own, whose books this app carries on with (bridge_data_dir).
OLD_BRIDGE_PORT = 8765
ICON = RESOURCE_DIR / "app" / "icon.ico"            # brand/ultimate-accountant: the app tile, 16-256 px

# In the window for the second or two the server takes to start: the page the
# choice of module (app/home.html) is drawn on, so nothing flashes.
BACKGROUND = "#f4f2ed"
STARTING = f"""<!doctype html><html><body style="margin:0;height:100vh;font:15px 'Segoe UI',sans-serif;color:#5d5f66;
background:{BACKGROUND}">
<p style="margin:0;padding-top:46vh;text-align:center;opacity:0">Starting Ultimate Accountant&hellip;</p></body></html>"""
FAILED = STARTING.replace("opacity:0", "opacity:1").replace(
    "Starting Ultimate Accountant&hellip;", "The app could not start. Close this window and open it again.")

REQUIRED = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("openpyxl", "openpyxl"),
    ("reportlab", "reportlab"),
    ("multipart", "python-multipart"),
    ("sqlalchemy", "SQLAlchemy"),
    ("pydantic_settings", "pydantic-settings"),
    ("httpx", "httpx"),
    ("pymupdf", "PyMuPDF"),
]

# The app runs without these; only document intake needs them, so a missing one
# is reported rather than treated as a failure to start.
OPTIONAL = [
    ("pdfplumber", "pdfplumber", "PDF text layer"),
    ("pypdfium2", "pypdfium2", "page rasterising"),
    ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "OCR for scans"),
    ("rapidfuzz", "rapidfuzz", "fuzzy narration matching"),
]

# Packaged, the app has no console, whatever its stand-in output says about being a terminal.
HAS_CONSOLE = not FROZEN and sys.stdout is not None and sys.stdout.isatty()

# The most of this computer the app may use: CPU, and GPU when there is one.
SHARE = 0.5
# Windows counts the cap in processor cycles, slice by slice. Measured in
# time, second by second, the app overshoots it: up to 56% at a 45% cap and
# 50.4% at 40% while reading bills (slow cores give more time per cycle). The
# margin keeps every second below SHARE. tools/check_limits.py measures the cap.
CPU_CAP = SHARE - 0.12
# Threads to match the cap: asking for no more CPU than allowed keeps use
# steady, instead of bursting to the cap and being throttled back.
THREADS = max(1, int((os.cpu_count() or 2) * CPU_CAP))
# The model worker fits the model with a margin, then needs a little more
# while reading an image (3.1 GB, 50.6% of a 6 GB card, at an exact 50%).
GPU_FREE = 1 - SHARE + 0.05


def say(message):
    """Print when there is a console, and otherwise keep quiet."""
    if sys.stdout is not None:
        print(message)


def fail(message):
    """Report something the user has to act on, wherever they can see it.

    A packaged build has no console, so a printed error is an error nobody
    reads. Win32 has a message box built in and needs no dependency for it.
    """
    say(message)
    if HAS_CONSOLE:
        try:
            input("\nPress Enter to close. ")
        except (EOFError, OSError):
            pass
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    except Exception:                                     # noqa: BLE001
        pass                                              # not Windows; the print stands


def optional_report():
    """One line saying whether scanned documents can be read on this machine."""
    import importlib.util
    missing = [(pip_name, what) for mod, pip_name, what in OPTIONAL
               if importlib.util.find_spec(mod) is None]
    if not missing:
        return "ready (scans and photographs can be read)"
    names = ", ".join(pip_name for pip_name, _ in missing)
    return f"limited - install {names} to read scanned documents"


def check_dependencies():
    """Report everything that is missing at once, not one import error at a time.

    A packaged build carries its own, so there is nothing to check there.
    """
    if FROZEN:
        return True
    import importlib.util
    missing = [pip_name for mod, pip_name in REQUIRED
               if importlib.util.find_spec(mod) is None]
    if missing:
        fail("Missing packages: " + ", ".join(missing)
             + "\n\nInstall them with:\n\n"
             + f"    {Path(sys.executable).name} -m pip install -r requirements.txt")
        return False
    return True


def bridge_data_dir():
    """Where the Invoice Bridge keeps its books. Not inside the data folder above, which
    Settings can move: the bridge's database holds each bill's full path."""
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"])
    if os.environ.get("ASSURANCE_DATA_DIR"):           # a trial run: all of it in the one folder
        return Path(os.environ["ASSURANCE_DATA_DIR"]) / "bridge"
    if not FROZEN:
        return RESOURCE_DIR / "data" / "bridge"
    roaming = Path(os.environ.get("APPDATA") or Path.home())
    old = roaming / "TallyInvoiceBridge" / "data"       # Invoice Bridge's books carry straight over
    return old if (old / "tally.db").exists() else roaming / "Ultimate Accountant" / "bridge"


def limit_cpu(share):
    """Hard-cap this process, and every process it starts, at `share` of the
    computer's total CPU. Windows enforces it (a job object with a CPU rate
    hard cap), so it holds for the app, the OCR and the AI runtime alike,
    whatever they try to use. The job also ends every one of them if this
    process ends, however it ends. Returns the job handle, or None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class CpuRateControl(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("Basic", BasicLimits), ("Io", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k.CreateJobObjectW(None, None)  # stays open for the life of the process
    # ENABLE | HARD_CAP; CpuRate counts hundredths of a percent of all processors.
    rate = CpuRateControl(0x1 | 0x4, int(share * 10000))
    ends = ExtendedLimits()
    # KILL_ON_JOB_CLOSE: nothing outlives the app. BREAKAWAY_OK: except the
    # updater, which must outlive the copy it replaces (app/backend/updates.py).
    ends.Basic.LimitFlags = 0x2000 | 0x800
    ok = job and k.SetInformationJobObject(job, 15, ctypes.byref(rate), ctypes.sizeof(rate)) \
        and k.SetInformationJobObject(job, 9, ctypes.byref(ends), ctypes.sizeof(ends)) \
        and k.AssignProcessToJobObject(job, k.GetCurrentProcess())
    return job if ok else None


_startfile = getattr(os, "startfile", None)


def open_outside_the_job(path, *_args, **_kwargs):
    """os.startfile, from outside limit_cpu's job: what the app opens in another program -
    a link in the browser (pywebview hands those to webbrowser, which calls os.startfile),
    a folder in File Explorer - is neither held to the app's share of the CPU nor closed
    with the app. ponytail: takes the default action only, which is all the app asks for."""
    try:
        subprocess.Popen(["explorer.exe", os.fspath(path)], creationflags=0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:                                    # a job above ours that allows no breakaway
        _startfile(path)


def gpu_memory():
    """Dedicated memory of the biggest graphics card, in bytes; 0 if there is
    none worth using. Read from the display driver's registry entry, which
    needs no admin rights and works for NVIDIA, AMD and Intel cards alike."""
    if os.name != "nt":
        return 0
    import winreg

    best = 0
    cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls) as adapters:
            for i in range(winreg.QueryInfoKey(adapters)[0]):
                try:
                    with winreg.OpenKey(adapters, winreg.EnumKey(adapters, i)) as a:
                        best = max(best, int(winreg.QueryValueEx(a, "HardwareInformation.qwMemorySize")[0]))
                except (OSError, ValueError):
                    continue  # no such value, or a key we may not read
    except OSError:
        return 0
    return best if best >= 2 << 30 else 0  # below 2 GB it is shared graphics


def share_of_the_computer():
    """What the modules read before they start: the threads the AI and the OCR may
    run on, the bridge's data folder, and how much of the graphics card the AI
    runtime may fill (app/backend/ai.py passes these on to it)."""
    os.environ["DATA_DIR"] = str(bridge_data_dir())
    os.environ["CPU_THREADS"] = str(THREADS)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "LLAMA_ARG_THREADS"):
        os.environ[var] = str(THREADS)
    vram = gpu_memory()
    if vram:
        os.environ["GPU_SHARE"] = str(SHARE)
        # The worker fits the model around a margin it must leave free: leaving GPU_FREE
        # of the card free holds the app under SHARE of it; the layers that do not fit
        # run on the CPU, inside the CPU cap. (OLLAMA_GPU_OVERHEAD does not reach the worker.)
        os.environ["LLAMA_ARG_FIT_TARGET"] = str(int(vram * GPU_FREE) >> 20)   # MiB


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def free_port(start=PREFERRED_PORT, tries=12):
    """First port at or after `start` that nothing is listening on."""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return None


def open_browser(url, delay=1.3):
    def run():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as exc:                      # noqa: BLE001
            fail(f"Could not open a browser automatically: {exc}\n\n"
                 f"Open this address yourself:\n\n    {url}")
    threading.Thread(target=run, daemon=True).start()


def running_copy(ports=None):
    """The address of a copy of the app already running here, if there is one.

    Only a port something listens on is asked. Asking a closed port costs two
    seconds on Windows before the refusal comes - about five seconds on every
    start with nothing running - while binding it, which only works when
    nothing listens there, is instant."""
    for port in ports or range(PREFERRED_PORT, PREFERRED_PORT + 12):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                continue                      # free: no copy of anything there
            except OSError:
                pass
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=0.4) as r:
                if json.load(r).get("app") == TITLE:
                    return f"http://{HOST}:{port}"
        except (OSError, ValueError, AttributeError):
            continue
    return None


def bring_to_front():
    """Raise the open app window; False when there is none (a browser copy)."""
    import ctypes
    user32 = ctypes.windll.user32
    window = user32.FindWindowW(None, TITLE)
    if not window:
        return False
    if user32.IsIconic(window):
        user32.ShowWindow(window, 9)                  # SW_RESTORE
    user32.SetForegroundWindow(window)
    return True


def webview2_installed():
    """Whether Windows has the WebView2 runtime the window is drawn with.

    Without it pywebview quietly falls back to Internet Explorer's engine, which
    cannot run this interface at all - so the browser is used instead. The key
    is the one pywebview itself reads.
    """
    import winreg
    key = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{key}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{key}"),
                       (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{key}")):
        try:
            with winreg.OpenKey(root, path) as k:
                build = winreg.QueryValueEx(k, "pv")[0]
            if build and build != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def _browser_habits(edge):
    """Put back two things pywebview does differently from a browser.

    It asks where to save every download, where the app's own messages say a
    download goes to Downloads; and outside its debug mode it switches off the
    right-click menu (copy, paste) and the browser's keys (Ctrl+F, Ctrl+P).
    Both patch pywebview 6.2's EdgeChrome, the version requirements.txt pins.
    """
    ready = edge.on_webview_ready

    def on_webview_ready(self, sender, args):
        ready(self, sender, args)
        if args.IsSuccess:
            settings = sender.CoreWebView2.Settings
            settings.AreDefaultContextMenusEnabled = True
            settings.AreBrowserAcceleratorKeysEnabled = True

    edge.on_webview_ready = on_webview_ready
    edge.on_download_starting = lambda self, sender, args: None     # WebView2's own: to Downloads


def _pin_certificate(edge, pinned):
    """Accept exactly the office server's own certificate, and no other that Windows doesn't
    already trust (spike N1, PLAN-office.md 3.2). Nothing is added to Windows' trusted roots."""
    ready = edge.on_webview_ready

    def on_webview_ready(self, sender, args):
        ready(self, sender, args)
        if not args.IsSuccess:
            return
        import ssl

        from Microsoft.Web.WebView2.Core import CoreWebView2ServerCertificateErrorAction as Action

        from app.office.pairing import matches

        def on_cert_error(_s, e):
            der = ssl.PEM_cert_to_DER_cert(e.ServerCertificate.ToPemEncoding())
            e.Action = Action.AlwaysAllow if matches(der, pinned) else Action.Cancel

        sender.CoreWebView2.ServerCertificateErrorDetected += on_cert_error

    edge.on_webview_ready = on_webview_ready


def _local_https():
    """For the launcher's own calls to this PC's office server over 127.0.0.1."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


def run_in_window(url, port):
    """Serve in the background and show the interface in the app's own window,
    until the window is closed. False when no window can be had."""
    if not webview2_installed():
        return False
    try:
        import webview
        from webview.platforms import edgechromium
    except Exception:                                 # noqa: BLE001 - .NET or WebView2 unusable here
        return False
    _browser_habits(edgechromium.EdgeChrome)
    if PINNED:
        _pin_certificate(edgechromium.EdgeChrome, PINNED)

    import uvicorn
    from app.backend.server import app as console
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning", **SERVE))
    serving = threading.Thread(target=server.run, daemon=True, name="server")
    serving.start()

    window = webview.create_window(TITLE, html=STARTING, maximized=True, min_size=(960, 620),
                                   background_color=BACKGROUND,  # between pages: the choice's page, not white
                                  
                                   text_select=True)
    console.state.close_window = window.destroy     # how Close the app, and an update, end it

    def show_when_ready():
        while serving.is_alive():
            try:
                urllib.request.urlopen(f"{url}/api/health", timeout=1,
                                       context=_local_https() if url.startswith("https") else None).close()
                if PINNED:                            # the office server: signed in as this PC's person
                    from app.office.setup import window_url
                    window.load_url(window_url(url))
                else:
                    window.load_url(url)
                return
            except OSError:
                time.sleep(0.1)
        window.load_html(FAILED)

    try:
        webview.start(show_when_ready, icon=str(ICON), storage_path=str(CACHE_DIR / "WebView2"))
    except Exception:                                 # noqa: BLE001 - no window after all
        console.state.close_window = None
        open_browser(url, delay=0)
        serving.join()                                # served in the browser until closed
        return True
    server.should_exit = True
    serving.join(timeout=5)
    return True


def window_on_background(office):
    """The office server already runs here without a window (the sign-in task): open the app's
    own window onto it, trusting only this PC's own certificate. Closing it leaves the server."""
    from app.office import pairing
    from app.office.setup import window_url
    pinned = pairing.own_certificate()
    if not pinned or not webview2_installed():
        fail("The office server is running here, but its window can't open: this PC needs its "
             "certificate (--office server) and Microsoft WebView2.")
        return 1
    import webview
    from webview.platforms import edgechromium
    _browser_habits(edgechromium.EdgeChrome)
    _pin_certificate(edgechromium.EdgeChrome, pinned)
    base = f"https://{HOST}:{office.PORT}"
    window = webview.create_window(TITLE, html=STARTING, maximized=True, min_size=(960, 620),
                                   background_color=BACKGROUND, text_select=True)
    webview.start(lambda: window.load_url(window_url(base)), icon=str(ICON),
                  storage_path=str(CACHE_DIR / "WebView2"))
    return 0


def serve_only(port):
    """`--serve --port N`: the server alone - no window, no browser, no looking
    for a copy already open. For checking a build from a script."""
    share_of_the_computer()
    import uvicorn
    from app.main import app
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
    return 0


def main():
    line = "=" * 68
    say(line)
    say("   Ultimate Accountant  ---  bills into Tally, books that tie")
    say(line)

    if not check_dependencies():
        return 1
    try:
        database = data_dir() / "reconcile.db"
    except DataFolderMissing as exc:              # a drive that is not plugged in, most often
        fail(str(exc))
        return 1

    args = sys.argv[1:]
    if "--office" in args:
        return office_command(args[args.index("--office") + 1:])
    from app.office import settings as office
    mode = office.mode()
    if "--keeper-sync" in args:                   # Windows' two-hourly task (app/keeper/sync.py)
        if mode != "server" or port_open(office.PORT):
            return 0                              # the open app syncs on its own timer
        from app.keeper import sync
        return sync.once()
    if "--background" in args:                    # Windows' sign-in task: the office server, no window
        if mode != "server" or port_open(office.PORT) or office_server_mode() is None:
            return 0
        share_of_the_computer()
        import uvicorn
        from app.main import app
        uvicorn.run(app, port=office.PORT, log_level="warning", **SERVE)
        return 0
    if mode == "client":
        return run_client(office.load())
    if mode == "server" and port_open(office.PORT):
        return 0 if bring_to_front() else window_on_background(office)
    if "--serve" in args:
        at = args.index("--port") + 1 if "--port" in args else 0
        return serve_only(int(args[at]) if at and at < len(args) else PREFERRED_PORT)

    existing = running_copy()
    if existing:                                  # double-clicked again: show the one that is open
        if not bring_to_front():
            webbrowser.open(existing)
        return 0
    if FROZEN and bridge_data_dir().parent.name == "TallyInvoiceBridge" and port_open(OLD_BRIDGE_PORT):
        fail("Invoice Bridge is open, and Ultimate Accountant carries on with its books.\n\n"
             "Close Invoice Bridge, then open Ultimate Accountant again.")
        return 1

    if mode == "server":
        port = office_server_mode()
        if port is None:
            return 1
        if FROZEN:
            from app.office import setup
            threading.Thread(target=setup.schedule, args=(sys.executable,), daemon=True).start()
    else:
        port = free_port()
    if port is None:
        fail(f"Ports {PREFERRED_PORT} to {PREFERRED_PORT + 11} are all in use.\n\n"
             "Another copy of Ultimate Accountant may already be running - look "
             "for it in your browser before starting a second one. Otherwise close "
             "whatever is using those ports and try again.")
        return 1

    url = f"{'https' if PINNED else 'http'}://{HOST}:{port}"
    if port != PREFERRED_PORT:
        say(f"Port {PREFERRED_PORT} was busy, using {port} instead.")

    share_of_the_computer()
    if limit_cpu(CPU_CAP):
        os.startfile = open_outside_the_job
        say(f"  Share         at most {SHARE:.0%} of the CPU" +
            (f" and {SHARE:.0%} of the graphics card" if os.environ.get("GPU_SHARE") else ""))
    else:
        say(f"  Share         Windows did not accept the {SHARE:.0%} CPU limit - the app may use more")

    say(f"\n  Interface     {url}   (Invoice Bridge: {url}/bridge/)")
    say(f"  Database      {database}")
    say(f"  Invoice Bridge  {os.environ['DATA_DIR']}")
    say(f"  Sample data   "
        f"{'found' if (RESOURCE_DIR / 'SampleData' / 'Purchases').exists() else 'not present'}")
    say(f"  Document OCR  {optional_report()}")
    if FROZEN:
        say(f"  Program       {APP_DIR}")
    say("\n  Every run is saved locally, so exports and history survive a restart.")
    say("  Close it from the interface, or press CTRL+C here.\n")

    if run_in_window(url, port):
        return 0
    open_browser(url)

    import uvicorn
    from app.main import app
    uvicorn.run(app, port=port, log_level="warning", **SERVE)
    return 0


def inform(message):
    """A message the user reads and dismisses: a dialog in the packaged app."""
    say(message)
    if HAS_CONSOLE:
        input("\nPress Enter to go on. ")
        return
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x40)


def confirm(message):
    """A yes/no question to the user."""
    if HAS_CONSOLE:
        return input(f"{message}\n\nYes or no? ").strip().lower().startswith("y")
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x24) == 6     # MB_YESNO|ICONQUESTION, IDYES


def office_command(args):
    """--office server | client HOST | alone | status | pin-admin CODE (app/office/setup.py)."""
    from app.backend.version import VERSION
    from app.office import setup
    what = args[0] if args else "status"
    try:
        if what == "server":
            return 0 if setup.setup_server(inform, confirm, VERSION) else 1
        if what == "client" and len(args) > 1:
            return 0 if setup.setup_client(args[1], inform, confirm, VERSION) else 1
        if what == "alone":
            setup.set_alone(inform)
            return 0
        if what == "pin-admin" and len(args) > 1:
            return 0 if setup.pin_admin(inform, confirm, args[1]) else 1
        if what == "status":
            inform(setup.status())
            return 0
        fail("Use --office server, --office client HOST, --office alone, --office status or --office pin-admin CODE.")
        return 1
    except Exception as exc:                          # noqa: BLE001 - said, not a crash with no window
        fail(f"Setting up the office didn't finish: {exc}")
        return 1


def office_server_mode():
    """The office server: every address on the office network, over HTTPS with its own
    certificate, on the one port clients know. Its port, or None after saying why not."""
    global SERVE, PINNED
    from app.office import pairing, settings as office
    if port_open(office.PORT):
        if not bring_to_front():
            fail("Ultimate Accountant is already running as the office server.")
        return None
    pinned = pairing.own_certificate()
    if not pinned:
        fail("This PC is set as the office server but has no certificate. Run --office server again.")
        return None
    cert, key = pairing.tls_paths()
    SERVE = {"host": "0.0.0.0", "ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    PINNED = pinned
    return office.PORT


def client_updates():
    """A client has no server of its own to show the update notice, so it checks here and
    asks in a dialog once the new version is downloaded and checked."""
    from app.backend import updates

    def loop():
        time.sleep(updates.FIRST_CHECK)
        while True:
            if updates.check().get("status") == "ready":
                text = f"Ultimate Accountant {updates.status().get('latest')} is ready. Install it now?\n\nThe window closes and opens again."
                if confirm(text):
                    updates.install()
                    return
            time.sleep(updates.EVERY)

    if updates.AUTOMATIC:
        threading.Thread(target=loop, daemon=True, name="updates").start()


def run_client(settings):
    """An office client: no server of its own, only the window onto the office server."""
    if bring_to_front():
        return 0
    if not webview2_installed():
        fail("This PC opens the office server in Ultimate Accountant's own window, which needs "
             "Microsoft WebView2. Install it from microsoft.com/edge/webview2, then try again.")
        return 1
    import webview
    from webview.platforms import edgechromium

    from app.office.setup import window_url
    _browser_habits(edgechromium.EdgeChrome)
    _pin_certificate(edgechromium.EdgeChrome, settings["server_cert"])
    window = webview.create_window(TITLE, html=STARTING, maximized=True, min_size=(960, 620),
                                   background_color=BACKGROUND, text_select=True)
    client_updates()
    webview.start(lambda: window.load_url(window_url(settings["server_url"])), icon=str(ICON),
                  storage_path=str(CACHE_DIR / "WebView2-office"))
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()   # a bundled child process must not re-run main
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("\nStopped.")
