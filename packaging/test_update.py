"""
Check the installer's update path against a throwaway folder.

    python packaging/test_update.py

Nothing real is touched: no registry, no shortcuts, and only processes started
here are closed. Three cases - a clean update, an update that fails part-way
and must put the old version back, and an update while the app is open.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import installer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PING = Path(r"C:\Windows\System32\PING.EXE")      # a harmless program to stand in for the app


def lay_out(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_text()
            for p in root.rglob("*") if p.is_file()}


def start_fake_app(folder):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / installer.APP_EXE
    shutil.copy2(PING, exe)
    return subprocess.Popen([str(exe), "-n", "120", "127.0.0.1"], stdout=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    installer.TARGET = tmp / "Programs" / installer.NAME
    lay_out(installer.TARGET, {"Ultimate Accountant.exe": "old", "_internal/old.dll": "old",
                               "data/reconcile.db": "saved runs"})
    new = {"Ultimate Accountant.exe": "new", "Uninstall.exe": "new",
           "_internal/app/index.html": "new"}
    lay_out(tmp / "payload", new)

    # 1. a clean update: the new program in, the old one gone, the data untouched
    installer.replace_program(tmp / "payload")
    assert contents(installer.TARGET) == {**new, "data/reconcile.db": "saved runs"}
    assert not installer.aside_dir().exists(), "the old copy was left behind"
    print("  clean update                  new program in, old gone, data untouched")

    # 2. a file held open stops the swap: the version that was there comes back whole
    before = contents(installer.TARGET)
    with open(installer.TARGET / "_internal" / "app" / "index.html"):
        try:
            installer.replace_program(tmp / "payload")
            raise AssertionError("the update went ahead with a file held open")
        except OSError:
            pass
    assert contents(installer.TARGET) == before, "the old version was not put back"
    assert not installer.aside_dir().exists()
    print("  update blocked part-way       the version before was put back intact")

    # 3. the installed app is open: it is closed and reported; a copy elsewhere is not
    ours = start_fake_app(installer.TARGET)
    theirs = start_fake_app(tmp / "elsewhere")
    try:
        assert installer.close_running_copy() is True, "the open app was not reported"
        assert ours.poll() is not None, "the installed copy is still running"
        assert theirs.poll() is None, "a copy outside the install folder was closed"
        assert installer.close_running_copy() is False, "reported open when it was not"
        print("  update while the app is open  that copy closed, others left alone")
    finally:
        for proc in (ours, theirs):
            proc.kill()
            proc.wait()

    # 4. a data folder chosen elsewhere: every update keeps the note of where it is
    installer.POINTER = installer.TARGET / "data-location.txt"
    custom = tmp / "My data"
    lay_out(custom, {"reconcile.db": "saved runs", "documents/x.pdf": "kept file"})
    installer.POINTER.write_text(str(custom))
    installer.replace_program(tmp / "payload")
    assert installer.POINTER.read_text() == str(custom), "the update lost the chosen data folder"
    assert installer.data_folder() == (custom, True)
    assert installer.dedicated(custom) == custom                    # already holds the data
    assert installer.dedicated(tmp / "payload") == tmp / "payload" / "Ultimate Accountant data"
    print("  chosen data folder            kept by the update, read back, never mixed in")

    # 4b. a first install copies Assurance Console's runs and registers over, once; its own copy stays
    installer.OLD_CONSOLE = tmp / "Programs" / "Assurance Console"
    lay_out(installer.OLD_CONSOLE / "data", {"reconcile.db": "old runs", "documents/a.pdf": "old file"})
    ask, installer.ask = installer.ask, lambda prompt, default="": "y"
    fresh = tmp / "fresh data"
    installer.copy_old_console_data(fresh)
    assert contents(fresh) == {"reconcile.db": "old runs", "documents/a.pdf": "old file"}, contents(fresh)
    assert contents(installer.OLD_CONSOLE / "data") == contents(fresh), "Assurance Console's copy changed"
    (fresh / "reconcile.db").write_text("new runs")
    installer.copy_old_console_data(fresh)
    assert (fresh / "reconcile.db").read_text() == "new runs", "data of its own was copied over"
    installer.ask = ask
    print("  first install                 copies Assurance Console's data once; its own stays")

    # 5. uninstalling: kept data stays with its note; erased data takes only the app's own files
    import uninstaller
    uninstaller.TARGET, uninstaller.POINTER = installer.TARGET, installer.POINTER
    (custom / "someone else's.xlsx").write_text("not ours")
    assert uninstaller.data_folder() == custom
    uninstaller.remove_program(keep_data=True)
    assert installer.POINTER.exists() and (custom / "reconcile.db").exists()
    assert uninstaller.erase_data(custom) == []
    assert contents(custom) == {"someone else's.xlsx": "not ours"}, contents(custom)
    print("  uninstall                     keeps data and its note, or erases only the app's files")

    # 6. an update the app started (--reopen): the app closed itself on the way
    #    out, and is opened again all the same. Nothing real is registered or linked.
    lay_out(installer.TARGET, {"Ultimate Accountant.exe": "old"})
    opened = []
    installer.payload_dir = lambda: tmp / "payload"
    installer.make_shortcut = lambda *args: None
    installer.register = lambda: None
    installer.refresh_icons = lambda: None
    installer.close_running_copy = lambda: False              # already gone
    installer.os.startfile = opened.append
    installer.time.sleep = lambda seconds: None
    sys.argv[1:] = ["--reopen"]
    assert installer.main() == 0
    assert opened == [installer.TARGET / installer.APP_EXE], opened
    assert (installer.TARGET / installer.APP_EXE).read_text() == "new"
    print("  update started by the app     installed, then opened again")

    # 7. the window's cache and downloaded updates go with the program; data beside them stays
    uninstaller.CACHE = tmp / "cache"
    lay_out(uninstaller.CACHE, {"WebView2/Default/Cache": "cache", "updates/Install-9.9.9.exe": "update",
                                "ai/ollama/ollama.exe": "runtime", "ai/models/MODEL": "model",
                                "data/reconcile.db": "saved runs"})
    uninstaller.SHARED_NOTE = tmp / "Shared AI" / "Ultimate Accountant.json"
    lay_out(tmp / "Shared AI", {"Ultimate Accountant.json": "ours", "Invoice Bridge.json": "theirs"})
    stopped = []
    uninstaller.stop_running_from = lambda folder: stopped.append((folder, uninstaller.SHARED_NOTE.exists()))
    uninstaller.remove_cache()
    assert contents(uninstaller.CACHE) == {"data/reconcile.db": "saved runs"}, contents(uninstaller.CACHE)
    # the bridge is told first, then whatever runs from the copy is stopped, then it goes
    assert stopped == [(uninstaller.CACHE / "ai", False)], stopped
    assert contents(tmp / "Shared AI") == {"Invoice Bridge.json": "theirs"}
    print("  uninstall, the cache          window cache, updates and the AI checker gone, data kept")

    # 8. Update.exe: the app's own code swapped over the same base, nothing else
    #    touched; over another base it refuses and changes nothing.
    installer.ask = lambda prompt, default="": default
    lay_out(installer.TARGET, {"Ultimate Accountant.exe": "exe", "_internal/python314.dll": "base",
                               "_internal/BASE": json.dumps({"core": "c1", "modules": ["fastapi", "json"]}),
                               "_internal/app/backend/server.py": "old code", "data/reconcile.db": "saved runs"})
    code = tmp / "update-payload"
    lay_out(code, {"BASE": json.dumps({"core": "c1", "modules": ["json"]}),
                   "app/backend/server.py": "new code", "app/frontend/index.html": "new page"})
    assert installer.payload_kind(code) == "app" and installer.payload_kind(tmp / "payload") == "program"
    not_code = lambda files: {k: v for k, v in files.items() if not k.startswith("_internal/app/")}
    before = contents(installer.TARGET)
    assert installer.update_app(code) == 0
    after = contents(installer.TARGET)
    assert (after["_internal/app/backend/server.py"], after["_internal/app/frontend/index.html"]) == ("new code", "new page")
    assert not_code(after) == not_code(before), "an update of the code touched something else"
    assert not (installer.TARGET / "_internal" / "app.previous").exists()
    lay_out(code, {"BASE": json.dumps({"core": "c2", "modules": ["json"]})})
    before = contents(installer.TARGET)
    assert installer.update_app(code) == 1 and contents(installer.TARGET) == before, "went over another base"
    print("  update of the code alone      swapped over the same base; refused over another")

# 9. the release file must name this product: Assurance Console's, signed by the same key, is refused
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.backend import updates
key = Ed25519PrivateKey.generate()
public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
for product, accepted in (("ultimate-accountant", True), ("assurance-console", False), (None, False)):
    body = {"version": "9.0.0", "sha256": "ab", "size": 1, **({"product": product} if product else {})}
    manifest = json.dumps(body).encode()
    try:
        updates.verify(manifest, base64.b64encode(key.sign(manifest)).decode(), public)
        assert accepted, f"a release for {product!r} was accepted"
    except updates.UpdateError:
        assert not accepted, "a release for this product was refused"
print("  release file                  refused unless it names ultimate-accountant")

print("update checks passed")
