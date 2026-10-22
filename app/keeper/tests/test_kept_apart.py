"""The Keeper is kept apart from the Invoice Bridge, and can only read Tally.
Run it with:  python app/keeper/tests/test_kept_apart.py

Same style as the bridge's tests: no pytest, a check(name, condition) helper
and a PASSED list. Holds the rules in PLAN-keeper.md, section 2:

  G1  no Keeper code imports the Invoice Bridge or Assurance Console
  G2  the Keeper's line to Tally refuses anything but a plain read, before sending
  G3  everything the Keeper sends to a (fake) Tally is a read
  G5  the one shared piece, app.tally_line, takes turns across threads and
      processes, and the Invoice Bridge uses that very lock
"""

from __future__ import annotations

import ast
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="keepertest-"))
# The bridge is imported below only to check it shares the lock: give it a
# folder of its own, never the books on this computer.
os.environ["DATA_DIR"] = str(_tmp / "bridge")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'bridge.db').as_posix()}"
os.environ["ASSURANCE_AI_DIR"] = str(_tmp / "ai")
os.environ["LLM_ENABLED"] = "false"
os.environ["WATCH_ENABLED"] = "false"
os.environ["TALLY_PORT"] = "9"

from app import tally_line                                                   # noqa: E402
from app.keeper import tally_read                                            # noqa: E402
from app.keeper.tally_read import ReadOnlyViolation                          # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"  ok  {name}")


def refused(payload: str) -> bool:
    try:
        tally_read.check(payload)
    except ReadOnlyViolation:
        return True
    return False


# --- G1 -------------------------------------------------------------------

# What each package may import from the app; nothing else, and never app.bridge (which writes
# to Tally) or app.backend. The office code (sign-in, devices, keys) may not import the Keeper.
ALLOWED_APP = {"keeper": ("app.keeper", "app.tally_line", "app.office"), "office": ("app.office",)}


def imports_of(path: Path, root: Path = ROOT) -> list[tuple[int, str]]:
    """(line, module) for every import in a file, relative ones made absolute,
    and 'dynamic' for importlib.import_module / __import__, which this check could not follow."""
    package = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                up = parts[:len(parts) - node.level + 1]
                base = ".".join(up + ([base] if base else []))
            # 'from app import x' imports app.x
            out += [(node.lineno, f"{base}.{a.name}" if base == "app" else base) for a in node.names]
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("import_module", "__import__"):
                out.append((node.lineno, "dynamic"))
    return out


def test_no_imports_of_the_other_modules():
    for package, allowed in ALLOWED_APP.items():
        files = [p for p in (ROOT / "app" / package).rglob("*.py") if "tests" not in p.relative_to(ROOT).parts]
        check(f"app/{package} has code to check", len(files) >= 2, str(files))
        bad = []
        for p in files:
            for line, mod in imports_of(p):
                if mod == "dynamic" or (mod.split(".")[0] == "app"
                                        and not any(mod == a or mod.startswith(a + ".") for a in allowed)):
                    bad.append(f"{p.relative_to(ROOT)}:{line} {mod}")
        check(f"no app/{package} code imports anything of the app but {', '.join(allowed)}", not bad, "; ".join(bad))

    # The checker itself must see what it is meant to catch.
    probe = _tmp / "app" / "keeper" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text("from app.bridge.services import tally\nfrom ..bridge import db\nfrom app import backend\n"
                     "import importlib\nimportlib.import_module('app.bridge')\n", encoding="utf-8")
    found = [m for _l, m in imports_of(probe, root=_tmp)]
    check("... and the check catches direct, relative, 'from app' and dynamic imports",
          found == ["app.bridge.services", "app.bridge", "app.backend", "importlib", "dynamic"], str(found))


# --- G2 -------------------------------------------------------------------

def envelope(header: str, body: str) -> str:
    return f"<ENVELOPE><HEADER>{header}</HEADER><BODY>{body}</BODY></ENVELOPE>"


EXPORT = "<VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>X</ID>"
TDL = "<DESC><TDL><TDLMESSAGE>{}</TDLMESSAGE></TDL></DESC>"


def test_only_reads_pass():
    ok = tally_read.collection("LedList", "Ledger", ["NAME", "PARENT"], company="A & B Traders <Pvt>",
                               extra_vars='<SVFROMDATE TYPE="Date">20260401</SVFROMDATE>')
    check("a collection read passes", not refused(ok))
    check("an object read passes", not refused(envelope(
        '<VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Object</TYPE><SUBTYPE>Ledger</SUBTYPE>'
        '<ID TYPE="Name">Cash</ID>',
        "<DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES></DESC>")))
    check("a formula alongside a collection passes", not refused(envelope(
        EXPORT, TDL.format('<COLLECTION NAME="X"><TYPE>Company</TYPE><FETCH>NAME</FETCH>'
                           '<COMPUTE>EDU:$$LicenseInfo:IsEducationalMode</COMPUTE></COLLECTION>'
                           '<SYSTEM TYPE="Formulae" NAME="IsMine">$Parent = "Sundry Debtors"</SYSTEM>'))))

    # How the Invoice Bridge posts a voucher and makes a ledger (app/bridge/services/tally.py).
    importing = envelope(
        "<VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE><ID>Vouchers</ID>",
        '<DESC><STATICVARIABLES><SVCURRENTCOMPANY>X</SVCURRENTCOMPANY></STATICVARIABLES></DESC>'
        '<DATA><TALLYMESSAGE><VOUCHER VCHTYPE="Purchase" ACTION="Create"><DATE>20260401</DATE></VOUCHER>'
        '</TALLYMESSAGE></DATA>')
    check("a voucher import is refused", refused(importing))
    check("a ledger made by import is refused", refused(importing.replace(
        '<VOUCHER VCHTYPE="Purchase" ACTION="Create"><DATE>20260401</DATE></VOUCHER>',
        '<LEDGER NAME="New" ACTION="Create"><PARENT>Sundry Debtors</PARENT></LEDGER>')))
    check("Export with data to import in the body is refused", refused(envelope(
        EXPORT, "<DESC></DESC><DATA><TALLYMESSAGE><LEDGER NAME='x'/></TALLYMESSAGE></DATA>")))
    check("Export of a report (TYPE Data) is refused - not one the Keeper sends", refused(envelope(
        "<VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID>",
        "<DESC></DESC>")))
    check("a TDL function is refused", refused(envelope(
        EXPORT, TDL.format('<FUNCTION NAME="Wipe"><ACTION>Delete Object</ACTION></FUNCTION>'))))
    check("an ACTION attribute anywhere is refused", refused(envelope(
        EXPORT, TDL.format('<COLLECTION NAME="X" ACTION="Alter"><TYPE>Ledger</TYPE></COLLECTION>'))))
    check("a collection with more than a read in it is refused", refused(envelope(
        EXPORT, TDL.format('<COLLECTION NAME="X"><TYPE>Ledger</TYPE><OBJECT>x</OBJECT></COLLECTION>'))))
    check("a second TALLYREQUEST hidden in the body is refused", refused(envelope(
        EXPORT, "<DESC><STATICVARIABLES><TALLYREQUEST>Import</TALLYREQUEST></STATICVARIABLES></DESC>")))
    check("an Import with an Export also in the header is refused", refused(envelope(
        EXPORT.replace("<TYPE>", "<TALLYREQUEST>Import</TALLYREQUEST><TYPE>"), "<DESC></DESC>")))
    check("anything but Export is refused, whatever its spelling",
          refused(envelope(EXPORT.replace("Export", "IMPORT"), "<DESC></DESC>"))
          and refused(envelope(EXPORT.replace("Export", "export"), "<DESC></DESC>")))
    check("XML that cannot be parsed is refused", refused("<ENVELOPE><HEADER>"))
    check("something other than an envelope is refused", refused("<RESPONSE/>"))


# --- G2 + G3 --------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_against_a_fake_tally():
    sys.path.insert(0, str(ROOT / "app" / "bridge" / "tests"))
    import fake_tally
    port = free_port()
    srv = fake_tally.serve(port)
    try:
        before = len(fake_tally.REQUESTS)
        names = tally_read.companies(port=port)
        check("the Keeper reads the companies a (fake) Tally has open",
              names == [n for n, _g, _s in fake_tally.COMPANIES], str(names))

        try:
            tally_read.send(envelope("<VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE>",
                                     "<DESC></DESC><DATA></DATA>"), port=port)
            sent = True
        except ReadOnlyViolation:
            sent = False
        sent_now = fake_tally.REQUESTS[before:]
        check("an Import given to send() is refused before it reaches Tally",
              not sent and len(sent_now) == 1, f"{len(sent_now)} requests reached the fake")
        bad = [r[:200] for r in sent_now if refused(r) or "<TALLYREQUEST>Export</TALLYREQUEST>" not in r]
        check("everything the Keeper sent was a read", not bad, str(bad))
    finally:
        srv.shutdown()

    try:
        tally_read.companies(port=free_port())
        check("a closed Tally is reported as such", False)
    except tally_read.TallyUnavailable:
        check("a closed Tally is reported as such", True)


# --- G5 -------------------------------------------------------------------

def test_the_shared_line():
    from app.bridge.services import tally as bridge_tally
    check("the Invoice Bridge's lock is the shared line", bridge_tally._line is tally_line.LINE)

    # The bridge takes the line once per request, as it did its threading.Lock.
    class Depth:
        def __init__(self, inner):
            self.inner, self.now, self.most = inner, 0, 0

        def acquire(self, *a, **k):
            ok = self.inner.acquire(*a, **k)
            if ok:
                self.now += 1
                self.most = max(self.most, self.now)
            return ok

        def release(self):
            self.now -= 1
            self.inner.release()

    sys.path.insert(0, str(ROOT / "app" / "bridge" / "tests"))
    import fake_tally
    port = free_port()
    srv = fake_tally.serve(port)
    depth, real_port = Depth(tally_line.LINE), bridge_tally.settings.tally_port
    bridge_tally._line, bridge_tally.settings.tally_port = depth, port
    try:
        company = bridge_tally.companies()[0]["name"]
        bridge_tally.ledgers(company)
        bridge_tally.voucher_types(company)
    finally:
        bridge_tally._line, bridge_tally.settings.tally_port = tally_line.LINE, real_port
        srv.shutdown()
    check("the bridge takes the line once per request, never twice over",
          depth.most == 1 and depth.now == 0, f"most {depth.most}, left {depth.now}")

    line = tally_line.LINE
    check("the line is free to start with", line.acquire(timeout=0))
    check("the thread holding it cannot take it again, as with threading.Lock",
          not line.acquire(timeout=0) and not line.acquire(blocking=False))
    got = []
    t = threading.Thread(target=lambda: got.append(line.acquire(timeout=0)))
    t.start()
    t.join()
    check("another thread cannot take it while it is held", got == [False])
    t = threading.Thread(target=lambda: got.append(line.acquire(timeout=0.3)))
    t.start()
    t.join()
    check("... nor after waiting a while", got == [False, False])
    line.release()
    t = threading.Thread(target=lambda: (got.append(line.acquire(timeout=1)), line.release()))
    t.start()
    t.join()
    check("once released, another thread takes it", got[-1] is True)

    hold = ("import sys, time; sys.path.insert(0, sys.argv[1]); from app import tally_line as t; "
            "t.LINE.acquire(); print('held', flush=True); time.sleep(float(sys.argv[2]));"
            "t.LINE.release() if sys.argv[3] == 'release' else None")
    p = subprocess.Popen([sys.executable, "-c", hold, str(ROOT), "1.5", "release"], stdout=subprocess.PIPE, text=True)
    try:
        check("another process takes the line", p.stdout.readline().strip() == "held")
        check("... and this one cannot while it holds it", not line.acquire(timeout=0))
        check("... and takes it once that process lets go", line.acquire(timeout=10))
        line.release()
    finally:
        p.wait(10)

    p = subprocess.Popen([sys.executable, "-c", hold, str(ROOT), "0", "exit"], stdout=subprocess.PIPE, text=True)
    p.stdout.readline()
    p.wait(10)
    check("a process that died holding the line does not keep it", line.acquire(timeout=2))
    line.release()


def main() -> int:
    test_no_imports_of_the_other_modules()
    test_only_reads_pass()
    test_against_a_fake_tally()
    test_the_shared_line()
    print(f"\n{len(PASSED)} checks passed.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)
    sys.exit(code)
