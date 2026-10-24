"""Setting this PC up, from  Ultimate Accountant.exe --office <what>  (launch.py):

    --office server        this PC is the office server (the one with Tally)
    --office client HOST   this PC opens the office server at HOST, signed in as its person
    --office alone         back to this PC on its own, as before
    --office status        what this PC is set up as
    --office pin-admin     with the recovery sheet's code: trust a new admin device

`tell(text)` shows a message and `ask(text)` a yes/no question, in whatever way the
launcher can (a dialog in the packaged app, the console otherwise).
"""

from __future__ import annotations

import functools
import os
import socket
import subprocess
import sys
import time

from app.office import keys, pairing, roster, settings
from app.office.cloud import Cloud, Revoked, Waiting
from app.office.identity import Identity, fingerprint
from app.office.link import link

HOST = socket.gethostname()
TITLE = "Ultimate Accountant"


def _box(text: str, flags: int) -> int:
    import ctypes
    # MB_SETFOREGROUND | MB_TOPMOST: started from the Run box, nothing else is on screen to hold it
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | 0x10000 | 0x40000)


def in_dialogs(fn):
    """In the packaged app, ask and tell in dialogs here, whatever the launcher passes.

    The 1.1.0 launcher thinks a packaged app has a console (its stand-in output says it is
    a terminal), asks there, and crashes on "lost sys.stdin". The launcher is part of the
    installed base, so the fix lives here and goes out as a light update; anything that goes
    wrong is said in a dialog too, never left to the launcher.
    """
    @functools.wraps(fn)
    def run(*args, **kwargs):
        if not getattr(sys, "frozen", False):
            return fn(*args, **kwargs)
        tell = lambda text: _box(text, 0x40)                    # noqa: E731
        ask = lambda text: _box(text, 0x24) == 6                # noqa: E731 - Yes/No, IDYES
        names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
        bound = dict(zip(names, args), **kwargs)
        bound.update({k: v for k, v in (("tell", tell), ("ask", ask)) if k in names})
        try:
            return fn(**bound)
        except Exception as exc:  # noqa: BLE001 - said here: the launcher's own report would crash
            _box(f"Setting up the office didn't finish:\n\n{type(exc).__name__}: {exc}", 0x10)
            return False
    return run


def _approved(cloud: Cloud, what: str, tell, ask) -> bool:
    tell(f"Now approve {what} in the admin console, on your phone or laptop.\n\n"
         f"It is waiting there with these 24 digits:\n\n{cloud.ident.fingerprint}\n\n"
         "Approve it only if they match. Then click OK here.")
    while True:
        try:
            cloud.access()
            return True
        except Waiting:
            if not ask("It isn't approved yet.\n\nTry again?"):
                return False
        except Revoked as exc:
            tell(f"It can't be approved: {exc}")
            return False


def _person_link(what: str, tell, app_version: str) -> Identity:
    tell(f"Your browser opens next, to link {what}.\n\n"
         "Sign in there with the Google account of the person who uses this PC, check the 24 digits, "
         "and choose Link this PC. Then come back here.")
    return link("office_client", HOST, app_version)


@in_dialogs
def setup_server(tell, ask, app_version: str = "") -> bool:
    tell("Setting up this PC as the office server.\n\nYour browser opens next. Sign in with your admin "
         "Google account, check the 24 digits, and choose Link this PC. Then come back here.")
    ident = link("office_server", f"{HOST} (office server)", app_version)
    cloud = Cloud(ident)
    cloud.token_key()                                    # pinned now, over the internet's own TLS
    if not _approved(cloud, "the office server", tell, ask):
        return False

    r = roster.refresh(cloud)
    me = r.devices.get(ident.device_id, {})
    approver = r.devices.get(me.get("approved_by_device") or "")
    if not approver:
        tell("The control plane doesn't say which device approved this one, so no admin device was trusted. "
             "Set up the office server again.")
        return False
    owner = r.users.get(approver["user_id"], {})
    if not ask(f"It was approved from {owner.get('email', '?')}'s device \"{approver.get('name') or 'unnamed'}\".\n\n"
               f"That device shows its own 24 digits on its first page (an older app shows only the first 12). Are they these?\n\n"
               f"{fingerprint(approver['sign_pub'], approver['wrap_pub'])}"):
        tell("Then that approval can't be trusted, and nothing was set up. Revoke that device in the admin console.")
        return False
    keys.pin_admin(approver["sign_pub"], approver["wrap_pub"])

    cert = pairing.make_certificate()
    if ask("Does someone use Ultimate Accountant on this PC itself, as well?\n\n"
           "Yes, if so: they link this PC to their own Google account next (yours, if it's you)."):
        person = Cloud(_person_link("this PC to its person", tell, app_version))
        _approved(person, "this PC for its person", tell, ask)

    sheet = keys.recovery_sheet(f"Office server {HOST}")
    os.startfile(sheet)
    tell("The recovery sheet has opened. Print it, keep it in a safe, and then delete the file:\n\n"
         f"{sheet}")

    s = settings.load()
    s["mode"] = "server"
    settings.save(s)
    tell(f"Done: this PC is the office server.\n\n"
         f"Office PCs connect to {HOST} (or {', '.join(a for a in pairing.local_addresses() if a != '127.0.0.1')}) "
         f"and check that it shows these 24 digits:\n\n{pairing.digits(cert)}\n\n"
         "Once, as administrator, let it through Windows Firewall on the office network:\n\n"
         'netsh advfirewall firewall add rule name="Ultimate Accountant office server" dir=in action=allow '
         f"protocol=TCP localport={settings.PORT} profile=private\n\nThen close and open Ultimate Accountant.")
    return True


@in_dialogs
def setup_client(host: str, tell, ask, app_version: str = "") -> bool:
    host = host.strip().removeprefix("https://").split("/")[0].split(":")[0]
    try:
        cert = pairing.remote_certificate(host)
    except OSError as exc:
        tell(f"The office server at {host} didn't answer ({exc}).\n\nIs it on, in server mode, and let through "
             "its firewall? Is this PC on the office network?")
        return False
    if not ask(f"The office server at {host} shows these 24 digits:\n\n{pairing.digits(cert)}\n\n"
               "Does the office server show the same? (On the office server: Ultimate Accountant.exe --office status.)"):
        tell("Then this isn't the office server, or something is in between. Nothing was set up.")
        return False
    person = Cloud(_person_link("this PC", tell, app_version))
    person.token_key()
    if not _approved(person, "this PC", tell, ask):
        return False
    s = settings.load()
    s.update({"mode": "client", "server_url": f"https://{host}:{settings.PORT}", "server_cert": cert})
    settings.save(s)
    tell("Done: this PC opens the office server. Close and open Ultimate Accountant.")
    return True


TASK = "Ultimate Accountant Keeper"
SIGN_IN_TASK = "Ultimate Accountant Server"


HOURS = (1, 2, 4, 6, 12)                 # what an admin can pick for the Keeper's check; 2 unless picked


def every_hours() -> int:
    h = settings.load().get("keeper_every_hours")
    return h if h in HOURS else 2


def schedule(exe: str) -> None:
    """Windows starts `exe --keeper-sync` every few hours (every_hours), so the Keeper's books go out while
    the app is closed too. Made again at every start of the office server and when an admin changes the
    hours: /F replaces the old one."""
    subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "HOURLY", "/MO", str(every_hours()),
                    "/TR", f'"{exe}" --keeper-sync'], capture_output=True, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # and at every sign-in, the office server itself with no window. The user's own Run list:
    # a sign-in task would need an administrator to make.
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, SIGN_IN_TASK, 0, winreg.REG_SZ, f'"{exe}" --background')


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def unschedule() -> None:
    for task in (TASK, SIGN_IN_TASK):
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", task], capture_output=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, SIGN_IN_TASK)
    except OSError:
        pass


@in_dialogs
def set_alone(tell) -> None:
    s = settings.load()
    s["mode"] = "alone"
    settings.save(s)
    unschedule()
    tell("This PC is on its own again, as before. Its links stay, for if it is set up again.")


def status() -> str:
    """What this PC is set up as. In the packaged app it is shown here, in a dialog, and
    the program ends: the 1.1.0 launcher would otherwise try to show it at a console."""
    text = _status()
    if getattr(sys, "frozen", False):
        _box(text, 0x40)
        raise SystemExit(0)
    return text


def _status() -> str:
    s = settings.load()
    lines = [f"This PC: {HOST}, mode {s['mode']}", f"Control plane: {s['control_plane']}"]
    for kind in ("office_server", "office_client"):
        try:
            i = Identity.load(kind)
        except OSError:
            i = None
        if i:
            lines.append(f"Linked as {kind}: device {i.device_id}, 24 digits {i.fingerprint}")
    own = pairing.own_certificate()
    if own:
        lines.append(f"Office server certificate: {pairing.digits(own)}")
    if s.get("server_url"):
        lines.append(f"Opens: {s['server_url']}, certificate {pairing.digits(s['server_cert'])}")
    r = roster.current()
    if r:
        lines.append(f"Roster: {len(r.users)} people, {len(r.devices)} devices, "
                     f"{int((time.time() - r.fetched_at) / 60)} minutes old")
    kr = keys.ring()
    if kr["keys"]:
        lines.append(f"Data keys: {', '.join(kr['keys'])} (now {kr['current']}); "
                     f"pinned admin devices: {len(kr['pinned_admins'])}")
    return "\n".join(lines)


@in_dialogs
def pin_admin(tell, ask, code: str) -> bool:
    ident = Identity.load("office_server")
    if not ident:
        tell("This PC isn't the office server.")
        return False
    r = roster.refresh(Cloud(ident))
    for d in r.devices.values():
        owner = r.users.get(d["user_id"], {})
        if d["status"] == "approved" and "admin" in owner.get("caps", []) and d["kind"] == "viewer":
            if ask(f"Trust {owner['email']}'s device \"{d['name']}\" as an admin device?\n\n"
                   f"Only if that device shows these 24 digits:\n\n{fingerprint(d['sign_pub'], d['wrap_pub'])}"):
                if keys.pin_with_code(code, d["sign_pub"], d["wrap_pub"]):
                    tell("Trusted. Print a new recovery sheet: the code is used up.")
                    os.startfile(keys.recovery_sheet(f"Office server {HOST}"))
                    return True
                tell("That code isn't right, or it was used before.")
                return False
    tell("No other admin device was trusted.")
    return False


def window_url(base: str) -> str:
    """Where the window opens: the office server, signed in as this PC's person when the
    control plane can be reached; just the office server (its session cookie) when not."""
    try:
        ident = Identity.load("office_client")
        if ident and ident.device_id:
            return f"{base}/office/sign-in?token={Cloud(ident).access()}&next=/"
    except Exception:  # noqa: BLE001 - offline, or not approved: the office server says which
        pass
    return base


def background(stop) -> None:
    """On the office server, every minute: the newest roster, then the data keys."""
    ident = Identity.load("office_server")
    if not ident:
        return
    cloud = Cloud(ident)
    while not stop.is_set():
        try:
            keys.sync(cloud, roster.refresh(cloud))
        except Exception as exc:  # noqa: BLE001 - the last roster stands; try again in a minute
            print(f"office: {type(exc).__name__}: {exc}", flush=True)
        stop.wait(60)
