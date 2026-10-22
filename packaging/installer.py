"""
Install.exe - the single file you send someone.

The whole program is carried inside this executable as a payload, so there is
nothing to unzip and nothing to place by hand. It installs per-user into
%LOCALAPPDATA%\\Programs, which needs no administrator rights, registers itself
in Add/Remove Programs, and leaves an Uninstall.exe behind.

Run on a machine that already has it, it updates that copy in place with no
questions: the app is closed if it is open (and opened again afterwards), the
old program is swapped for the new one, and the saved reconciliations are left
exactly where they are.

Built as Update.exe it carries only the app's own code (_internal/app, a few
MB) and swaps that alone, over an install whose base - Python, the libraries,
the OCR models - is the one it was built on (build.spec writes _internal/BASE).
Over any other it does nothing and says the full installer is needed.

A first install asks where the data should live - beside the program, or a
folder the user picks. The choice is one line in data-location.txt beside the
program, which every update keeps and the uninstaller reads; the app reads it
too (app/backend/paths.py, which shares the file name and the folder rules).

A console window is deliberate here: copying a few hundred megabytes takes long
enough that a silent window would look like nothing was happening.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import winreg
from datetime import datetime
from pathlib import Path

NAME = "Ultimate Accountant"
APP_EXE = f"{NAME}.exe"
UNINSTALL_EXE = "Uninstall.exe"
PUBLISHER = "Ultimate Accountant"
VERSION = "1.3.6"                                 # the same as app/backend/version.py
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\UltimateAccountant"

TARGET = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / NAME
MENU = (Path(os.environ.get("APPDATA", Path.home()))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs")
DESKTOP = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
POINTER = TARGET / "data-location.txt"
KEEP = {"data", POINTER.name}                     # what an update never touches
# Assurance Console, installed on its own: its runs and registers can be copied over.
OLD_CONSOLE = TARGET.with_name("Assurance Console")
DATA_ITEMS = ("reconcile.db", "reconcile.db-wal", "reconcile.db-shm", "reconcile.pre-v2.db", "documents",
              "log.txt")                          # the same as app/backend/paths.py


def payload_dir():
    """Where the bundled copy of the program sits inside this executable."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload"


def payload_kind(source):
    """"program" for the whole program (Install.exe), "app" for the app's own
    code alone (Update.exe), None when neither is there."""
    if (source / APP_EXE).exists():
        return "program"
    if (source / "app").is_dir() and (source / "BASE").exists():
        return "app"
    return None


def base_of(folder):
    """A program folder's base as build.spec wrote it, or None."""
    try:
        return json.loads((folder / "BASE").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fits(installed, update):
    """Whether the app's code alone can go over this install: the same Python
    and libraries, and every module the new code needs already there."""
    return bool(installed and update and installed.get("core") == update.get("core")
                and set(update.get("modules") or []) <= set(installed.get("modules") or []))


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


def ps(text):
    """A PowerShell single-quoted string; a quote in a path must not end it."""
    return "'" + str(text).replace("'", "''") + "'"


def powershell(script):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-Command", script],
                          capture_output=True, text=True, check=False).stdout


def data_folder():
    """Where the data is: the folder chosen at install or in Settings, if any."""
    try:
        chosen = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        chosen = ""
    return (Path(chosen) if chosen else TARGET / "data"), bool(chosen)


def dedicated(folder):
    """The picked folder itself if it is new, empty or already holds the app's
    data; otherwise a folder inside it, so erasing the data never takes
    anything else. The same rule as paths.dedicated in the app."""
    if not folder.exists() or (folder / "reconcile.db").exists() or not any(folder.iterdir()):
        return folder
    return folder / "Ultimate Accountant data"


def pick_folder():
    """Windows' own folder chooser, on top of this window."""
    return powershell(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description = 'Choose the folder Ultimate Accountant keeps your data in'; "
        "$d.ShowNewFolderButton = $true; "
        "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }").strip()


def choose_data_folder():
    """Ask where the data should live. None keeps it beside the program."""
    print("  Where should your data be kept? Your runs, export registers and")
    print("  every file you give the app are stored there.")
    print(f"    Press Enter to keep it with the program, in\n      {TARGET / 'data'}")
    print("    or type C to choose another folder.")
    if ask("  > ", "").strip().lower() != "c":
        print()
        return None
    picked = pick_folder()
    if not picked:
        print("  No folder chosen - keeping it with the program.\n")
        return None
    folder = dedicated(Path(picked))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".write-probe").write_text("", encoding="utf-8")
        (folder / ".write-probe").unlink()
    except OSError as exc:
        print(f"  {folder} cannot be written to ({exc}) - keeping it with the program.\n")
        return None
    print(f"  Your data will be kept in\n    {folder}\n")
    return folder


def old_console_data():
    """Assurance Console's data folder, when it is installed on its own and has some."""
    try:
        chosen = (OLD_CONSOLE / "data-location.txt").read_text(encoding="utf-8").strip()
    except OSError:
        chosen = ""
    for folder in ([Path(chosen)] if chosen else []) + [OLD_CONSOLE / "data"]:
        if (folder / "reconcile.db").exists():
            return folder
    return None


def copy_old_console_data(data):
    """Offer to copy Assurance Console's runs and registers into `data`, once, on a first
    install. Copied, not moved: the old app and its data stay exactly as they were."""
    old = old_console_data()
    if not old or (data / "reconcile.db").exists():
        return
    print("  Assurance Console is installed here too, with its runs and registers in")
    print(f"    {old}")
    if ask("  Copy them into Ultimate Accountant? [Y/n] ", "y").strip().lower() not in ("", "y", "yes"):
        print()
        return
    try:
        data.mkdir(parents=True, exist_ok=True)
        for name in DATA_ITEMS:
            if (old / name).is_dir():
                shutil.copytree(old / name, data / name, dirs_exist_ok=True)
            elif (old / name).exists():
                shutil.copy2(old / name, data / name)
        print("  Copied. Assurance Console keeps its own copy.\n")
    except OSError as exc:
        print(f"  They could not be copied ({exc}). Ultimate Accountant starts empty;")
        print("  Assurance Console's data is untouched.\n")


def close_running_copy():
    """Close the installed app if it is open, and say whether it was.

    A running program's files cannot be replaced. Only the copy in the install
    folder is closed - one started from anywhere else is left alone - and the
    wait is for the process to be gone, not a guess at how long that takes.
    """
    folder = str(TARGET) + "\\"
    out = powershell(
        f"$p = Get-Process -Name {ps(NAME)} -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.Path -and $_.Path.StartsWith({ps(folder)}, "
        "[StringComparison]::OrdinalIgnoreCase) }; "
        "if ($p) { $p | Stop-Process -Force; "
        "$p | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue; 'was running' }")
    return "was running" in out


def retry(action, tries=20):
    """Windows can hold a file for a moment after its program exits - an
    antivirus scan, the search indexer - so a move that fails once is tried
    again a beat later before giving up."""
    for attempt in range(tries):
        try:
            return action()
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(0.25)


def copy_payload(source):
    total = sum(1 for p in source.rglob("*") if p.is_file())
    done = 0
    width = 44
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        dest = TARGET / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        done += 1
        if done % 25 == 0 or done == total:
            filled = int(width * done / max(total, 1))
            print(f"\r  [{'#' * filled}{'.' * (width - filled)}] "
                  f"{done}/{total} files", end="", flush=True)
    print()


def aside_dir():
    """Where the old program waits while the new one is copied in."""
    return TARGET.with_name(f"{NAME}.previous")


def replace_program(source):
    """Swap the old program for the new one; the data folder is never touched.

    The old files are moved aside rather than deleted, so if anything stops the
    copy part-way - a file still held open, a full disk, Ctrl+C - they go
    straight back and the version that was there still runs. Deleting first
    could leave half of each.
    """
    aside = aside_dir()
    shutil.rmtree(aside, ignore_errors=True)          # left by an update cut short
    aside.mkdir(parents=True)
    moved = []

    def put_back():
        for name in moved:
            (aside / name).rename(TARGET / name)
        shutil.rmtree(aside, ignore_errors=True)

    try:
        for item in [p for p in TARGET.iterdir() if p.name.lower() not in KEEP]:
            retry(lambda: item.rename(aside / item.name))
            moved.append(item.name)
    except BaseException:
        put_back()                                    # nothing new was copied yet
        raise
    try:
        copy_payload(source)
    except BaseException:
        for item in TARGET.iterdir():                 # the half-copied new version
            if item.name.lower() not in KEEP:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        put_back()
        raise
    shutil.rmtree(aside, ignore_errors=True)


def replace_app(source):
    """Swap the app's own code, _internal/app, for the update's; nothing else is
    touched. As with the whole program, the old code waits aside and goes
    straight back if the copy stops part-way."""
    internal = TARGET / "_internal"
    live, aside = internal / "app", internal / "app.previous"
    shutil.rmtree(aside, ignore_errors=True)          # left by an update cut short
    if live.exists():
        retry(lambda: live.rename(aside))
    try:
        shutil.copytree(source / "app", live)
    except BaseException:
        shutil.rmtree(live, ignore_errors=True)
        if aside.exists():
            aside.rename(live)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def update_app(source):
    """Update.exe: the new version's code over an install built on the same base."""
    if not (TARGET / APP_EXE).exists():
        print(f"  This is an update. Install {NAME} first, with Install.exe.")
        ask("\nPress Enter to close. ")
        return 1
    if not fits(base_of(TARGET / "_internal"), base_of(source)):
        print("  This update was made for a different Python or set of libraries than")
        print("  the ones installed here, so it cannot go over them. Run Install.exe")
        print("  for this version instead; your data stays exactly where it is.")
        ask("\nPress Enter to close. ")
        return 1
    was_running = close_running_copy()
    if was_running:
        print("  The app was open, so it has been closed for the update.\n")
    print("  Copying the new version's code")
    try:
        replace_app(source)
    except OSError as exc:
        print(f"\n  The copy failed: {exc}")
        print("  The version you had has been put back, so it still works.")
        print(f"  Close {NAME} if it is open, then run this again.")
        ask("\nPress Enter to close. ")
        return 1
    register()
    refresh_icons()                               # an install still showing an older logo
    print()
    rule()
    print(f"   Updated to {VERSION}.")
    rule()
    if was_running or "--reopen" in sys.argv[1:]:
        print("\n  Opening it again.")
        os.startfile(TARGET / APP_EXE)    # noqa: S606 - the program we just updated
        time.sleep(2)
    return 0


def make_shortcut(link, target, description):
    """Windows shortcuts are COM objects; PowerShell is the way in without a
    dependency, and it is already on every machine this runs on."""
    powershell(
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut({ps(link)}); "
        f"$s.TargetPath = {ps(target)}; "
        f"$s.WorkingDirectory = {ps(Path(target).parent)}; "
        f"$s.IconLocation = {ps(f'{target},0')}; "
        f"$s.Description = {ps(description)}; "
        "$s.Save()")


def refresh_icons():
    """Windows keeps the icon it once read from the exe, keyed by its path, which
    an update never changes - so the taskbar, the Start menu and the desktop
    went on showing the old logo. Tell the shell its icons changed, and have it
    rebuild its icon cache."""
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except OSError:
        pass
    ie4uinit = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ie4uinit.exe"
    try:
        subprocess.run([str(ie4uinit), "-show"], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass                                          # the icons catch up at the next sign-in


def register():
    """Appear in Add/Remove Programs, so it can be removed the usual way."""
    try:
        size_kb = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file()) // 1024
        values = {
            "DisplayName": NAME,
            "DisplayVersion": VERSION,
            "Publisher": PUBLISHER,
            "InstallLocation": str(TARGET),
            "DisplayIcon": str(TARGET / APP_EXE),
            "UninstallString": f'"{TARGET / UNINSTALL_EXE}"',
        }
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except OSError as exc:
        print(f"  (could not register in Add/Remove Programs: {exc})")


def main():
    installed = TARGET / APP_EXE
    updating = installed.exists()

    rule()
    print(f"   {'Updating' if updating else 'Installing'} {NAME}")
    rule()
    print()

    source = payload_dir()
    kind = payload_kind(source)
    if kind is None:
        print("This installer is incomplete - the program payload is missing.")
        print("Download it again.")
        ask("\nPress Enter to close. ")
        return 1
    if kind == "app":
        return update_app(source)

    was_running = False
    data, chosen = data_folder()
    new_folder = None
    if updating:
        since = datetime.fromtimestamp(installed.stat().st_mtime).strftime("%d %b %Y")
        print(f"  Found the copy installed on {since}, in")
        print(f"    {TARGET}")
        print("  Replacing it with this version. Your saved runs and export")
        print("  registers stay exactly as they are, in")
        print(f"    {data}\n")
        if chosen and not data.is_dir():
            print("  That folder cannot be found. If it is on a drive that is not")
            print("  connected, connect it and run this again - or choose where the")
            print("  data should be kept from now on.\n")
            new_folder = choose_data_folder()
        was_running = close_running_copy()
        if was_running:
            print("  The app was open, so it has been closed for the update.")
            print("  It will open again when this finishes.\n")
    elif data.exists():
        print("  Saved work from an earlier install was found in")
        print(f"    {data}")
        print("  It will be kept and picked up again.\n")
    else:
        if chosen:
            print(f"  An earlier install kept its data in {data},")
            print("  which cannot be found now.\n")
        new_folder = choose_data_folder()

    print("  Copying files")
    try:
        if updating:
            replace_program(source)
        else:
            shutil.rmtree(aside_dir(), ignore_errors=True)   # from a copy cut short
            TARGET.mkdir(parents=True, exist_ok=True)
            copy_payload(source)
    except OSError as exc:
        print(f"\n  The copy failed: {exc}")
        if updating:
            print("  The version you had has been put back, so it still works.")
            print(f"  Close {NAME} if it is open, and anything else using that")
            print("  folder, then run this again.")
        else:
            print("  Check there is space on the drive and try again.")
        ask("\nPress Enter to close. ")
        return 1

    if new_folder:
        POINTER.write_text(str(new_folder), encoding="utf-8")
        data = new_folder
    elif not updating and chosen and not data.exists():
        POINTER.unlink(missing_ok=True)               # a note of a folder that has gone
        data = TARGET / "data"
    if not updating:
        copy_old_console_data(data)

    print("  Creating shortcuts")
    app = TARGET / APP_EXE
    make_shortcut(MENU / f"{NAME}.lnk", app,
                  "Bills into Tally, reconciliation and the export register")
    make_shortcut(DESKTOP / f"{NAME}.lnk", app,
                  "Bills into Tally, reconciliation and the export register")
    make_shortcut(MENU / f"Uninstall {NAME}.lnk", TARGET / UNINSTALL_EXE,
                  f"Remove {NAME}")

    print("  Registering")
    register()
    refresh_icons()

    print()
    rule()
    print(f"   {'Updated' if updating else 'Done'}.")
    rule()
    print(f"\n  Installed in   {TARGET}")
    print("  Start it from  the desktop shortcut or the Start menu")
    print(f"  Your data      {data}")
    print("  To remove it   Settings > Apps, or Uninstall in the Start menu")
    print()

    # --reopen: started by the app itself for an update, which closes the app
    # on its way out - so it may already be gone, and still wants reopening.
    if was_running or "--reopen" in sys.argv[1:]:
        print("  Opening it again.")
        os.startfile(app)                 # noqa: S606 - a program we just wrote
        time.sleep(2)                     # long enough to read before the window goes
        return 0
    answer = ask("Start it now? [Y/n] ", "n").strip().lower()
    if answer in ("", "y", "yes"):
        os.startfile(app)                 # noqa: S606 - a program we just wrote
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was changed.")
        sys.exit(1)
