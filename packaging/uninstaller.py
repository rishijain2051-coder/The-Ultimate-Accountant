"""
Uninstall.exe - removes Ultimate Accountant.

It lives inside the folder it has to delete, and Windows will not let a running
executable be removed, so it copies itself to a temporary directory and hands
over to that copy. The temporary copy does the actual deleting.

Saved reconciliations are kept unless the user explicitly types YES. They are
the only thing here that cannot be downloaded again.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

NAME = "Ultimate Accountant"
APP_EXE = f"{NAME}.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\UltimateAccountant"

TARGET = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / NAME
MENU = (Path(os.environ.get("APPDATA", Path.home()))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs")
DESKTOP = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"

RELAY_FLAG = "--from-temp"
# Where the user chose to keep their data (written by the installer or by the
# app's Settings) and everything the app writes there - the same names as
# app/backend/paths.py. Erasing takes these and nothing else.
POINTER = TARGET / "data-location.txt"
DATA_ITEMS = ("reconcile.db", "reconcile.db-wal", "reconcile.db-shm", "reconcile.pre-v2.db", "documents",
              "log.txt", ".write-probe")
# The app window's browser cache and downloaded updates (CACHE_DIR in paths.py):
# the app's own, so always removed. The data's last-resort folder can sit
# beside them and is left alone here - erasing data goes through erase_data.
CACHE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / NAME
CACHE_ITEMS = ("WebView2", "updates", "ai")          # "ai": the AI checker, when it was fetched
# Where the AI says the apps sharing it may run its copy (app/backend/ai.py).
SHARED_NOTE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Shared AI" / f"{NAME}.json"
# The Invoice Bridge's books (launch.bridge_data_dir): never erased here, as Tally
# Invoice Bridge never erased them - its database is the books it posted from.
_ROAMING = Path(os.environ.get("APPDATA", Path.home()))
_OLD_BRIDGE = _ROAMING / "TallyInvoiceBridge" / "data"
BRIDGE_DATA = _OLD_BRIDGE if (_OLD_BRIDGE / "tally.db").exists() else _ROAMING / NAME / "bridge"


def rule(char="="):
    print(char * 68)


def ask(prompt, default=""):
    """Prompts are for a person at a console; without one, take the default.

    A double-clicked installer always has a console, but one driven from a
    script or a deployment tool does not, and crashing on the last line after
    a successful install would be an absurd way to fail.
    """
    try:
        return input(prompt)
    except (EOFError, OSError):
        print(f"{prompt}{default}  (no console - assumed)")
        return default


def relocate_and_rerun():
    """Re-launch from a temporary copy so the install folder can be deleted."""
    here = Path(sys.executable).resolve()
    if RELAY_FLAG in sys.argv or TARGET not in here.parents:
        return False
    relay = Path(tempfile.gettempdir()) / f"uninstall-{NAME.replace(' ', '-')}.exe"
    try:
        shutil.copy2(here, relay)
        subprocess.Popen([str(relay), RELAY_FLAG], cwd=tempfile.gettempdir())
        return True
    except OSError:
        return False          # carry on in place; everything but the exe will go


def remove_shortcuts():
    for link in (MENU / f"{NAME}.lnk",
                 MENU / f"Uninstall {NAME}.lnk",
                 DESKTOP / f"{NAME}.lnk"):
        try:
            link.unlink(missing_ok=True)
        except OSError:
            pass


def unregister():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except OSError:
        pass
    # the Keeper's two-hourly sync (app/office/setup.py schedule)
    for task in ("Ultimate Accountant Keeper", "Ultimate Accountant Server"):
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", task], capture_output=True, check=False)
    # and the office server's start at sign-in
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "Ultimate Accountant Server")
    except OSError:
        pass


def data_folder():
    try:
        chosen = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        chosen = ""
    return Path(chosen) if chosen else TARGET / "data"


def erase_data(folder):
    """Remove what the app wrote into its data folder, then the folder if that
    leaves it empty - never anything else kept there."""
    failed = []
    for name in DATA_ITEMS:
        item = folder / name
        try:
            if item.is_dir():
                shutil.rmtree(item)
            elif item.exists():
                item.unlink()
        except OSError:
            failed.append(str(item))
    try:
        folder.rmdir()
    except OSError:
        pass
    return failed


def stop_running_from(folder):
    """Stop every program running from `folder` - the bridge may be running the AI checker's copy."""
    if not folder.exists():
        return
    where = (str(folder.resolve()).rstrip("\\") + "\\").replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "$p = Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith("
                    f"'{where}', [StringComparison]::OrdinalIgnoreCase) }}; "
                    "if ($p) { $p | Stop-Process -Force; $p | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue }"],
                   capture_output=True, check=False)


def remove_cache():
    # The note goes first, so the bridge stops counting on the AI checker's copy; then
    # whatever still runs from it, or its files in use would survive as a broken copy.
    try:
        SHARED_NOTE.unlink(missing_ok=True)
    except OSError:
        pass
    stop_running_from(CACHE / "ai")
    for name in CACHE_ITEMS:
        shutil.rmtree(CACHE / name, ignore_errors=True)
    try:
        CACHE.rmdir()                     # only if nothing else is left in it
    except OSError:
        pass


def remove_program(keep_data):
    """Delete everything except, when the data is kept, the data folder and the
    note of where it is - so installing again picks it up."""
    failed = []
    for item in TARGET.iterdir():
        if keep_data and item.name.lower() in ("data", POINTER.name):
            continue
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        except OSError:
            failed.append(item.name)
    if not keep_data:
        try:
            TARGET.rmdir()
        except OSError:
            pass
    return failed


def main():
    rule()
    print(f"   Removing {NAME}")
    rule()
    print()

    if not TARGET.exists():
        print("It does not look installed - nothing found at:")
        print(f"  {TARGET}")
        ask("\nPress Enter to close. ")
        return 0

    data = data_folder()
    keep_data = True
    if data.exists():
        size = sum(p.stat().st_size for p in data.rglob("*") if p.is_file())
        print("Your saved runs and export registers, and every file you gave")
        print("the app, are stored in:")
        print(f"  {data}   ({size / 1048576:.0f} MB)")
        print()
        print("They are kept by default, and a later reinstall will pick them")
        print("up again. There is no way to get them back once erased.")
        print()
        answer = ask('Type YES to erase them, or press Enter to keep: ').strip()
        keep_data = answer.upper() != "YES"

    print("\nClosing the program if it is running")
    subprocess.run(["taskkill", "/f", "/im", APP_EXE],
                   capture_output=True, check=False)
    time.sleep(0.8)

    print("Removing shortcuts")
    remove_shortcuts()
    unregister()

    print("Removing the program")
    failed = []
    if not keep_data and data.exists():
        failed += erase_data(data)
    failed += remove_program(keep_data)
    remove_cache()

    print()
    rule()
    if failed:
        print("   Removed, apart from a few files still in use.")
        rule()
        print("\n  Left behind:")
        for name in failed[:6]:
            print(f"    {name}")
        print(f"\n  Restart the machine and delete this folder by hand:")
        print(f"    {TARGET}")
    else:
        print("   Removed.")
        rule()
        if keep_data and data.exists():
            print(f"\n  Your saved work and files are still in:\n    {data}")
            print("  Installing again picks them up.")
        if BRIDGE_DATA.exists():
            print(f"\n  The Invoice Bridge's books, bills and backups are kept in:\n    {BRIDGE_DATA}")
    print()
    ask("Press Enter to close. ")
    return 0


if __name__ == "__main__":
    if relocate_and_rerun():
        sys.exit(0)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was removed.")
        sys.exit(1)
