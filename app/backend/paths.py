"""
Where things live, whether the app runs from source or from a frozen build.

PyInstaller unpacks the bundled files into a temporary directory and leaves the
executable somewhere else entirely, so read-only resources and the writable
database have to be resolved separately. Every other module asks here rather
than working it out from `__file__`, which is wrong inside a bundle.
"""

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent.parent
    APP_DIR = RESOURCE_DIR


def _writable(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# The app window's browser cache and downloaded updates: the app's own files,
# never the user's, so the uninstaller always takes them. (The data's last-resort
# folder, below, sits here too - and is never taken without asking.)
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Ultimate Accountant"

# Where the user chose to keep their data, if not beside the program: one line,
# written by the installer or by Settings, and kept by every update. The
# installer and uninstaller read it too, so its name must not change.
POINTER = APP_DIR / "data-location.txt"
# Everything the app writes into its data folder: what a move takes and what
# an uninstall erases - never anything else that shares the folder.
DATA_ITEMS = ("reconcile.db", "reconcile.db-wal", "reconcile.db-shm", "reconcile.pre-v2.db", "documents", "log.txt")


class DataFolderMissing(RuntimeError):
    """The chosen data folder is not there - most often a drive not plugged in."""


def chosen_data_dir():
    try:
        text = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def remember_data_dir(folder):
    """Keep `folder` as the data folder from now on; the usual place needs no note."""
    if Path(folder) == APP_DIR / "data":
        POINTER.unlink(missing_ok=True)
    else:
        POINTER.write_text(str(folder), encoding="utf-8")


def dedicated(folder):
    """The folder the data goes in when `folder` is picked: the folder itself if
    it is new, empty or already holds this app's data, otherwise a new folder
    inside it - so erasing the data can never take anything else with it."""
    folder = Path(folder)
    if not folder.exists() or (folder / "reconcile.db").exists() or not any(folder.iterdir()):
        return folder
    return folder / "Ultimate Accountant data"


def data_dir():
    """The folder chosen at install or in Settings; otherwise beside the
    program when that is writable.

    An install under Program Files is not, and neither is the temporary
    directory a one-file build unpacks into, so the fallback is the user's own
    AppData. Without this the app would appear to work and then lose every run
    on the next launch.
    """
    override = os.environ.get("ASSURANCE_DATA_DIR")      # tests and trial runs
    if override:
        Path(override).mkdir(parents=True, exist_ok=True)
        return Path(override)
    chosen = chosen_data_dir()
    if chosen:
        # Starting empty in its place would look like every run had gone, and
        # split the data in two once the drive came back.
        if chosen.is_dir() and _writable(chosen):
            return chosen
        raise DataFolderMissing(
            f"Your data is kept in\n\n    {chosen}\n\nand that folder cannot be reached. If it is on a drive "
            "that is not connected, connect it and start the app again. To choose another folder, "
            "run Install.exe again.")
    beside = APP_DIR / "data"
    if _writable(beside):
        return beside
    fallback = CACHE_DIR / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def downloads_dir():
    """The user's Downloads folder, where a browser saves what is fetched from
    ICEGATE, DGFT or the bank - wherever Windows keeps it, which can be another
    drive. The app only ever reads it. ASSURANCE_DOWNLOADS_DIR stands in for it
    in tests and trial runs, so they never look at the real one."""
    override = os.environ.get("ASSURANCE_DOWNLOADS_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        import ctypes
        import uuid
        guid = ctypes.create_string_buffer(uuid.UUID("374de290-123f-4565-9164-39c4925e467b").bytes_le, 16)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    return Path.home() / "Downloads"
