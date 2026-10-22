"""
Build the three executables, in the order they depend on each other.

    python packaging/build_release.py

  0. the Invoice Bridge's interface -> bridge-ui/out  (npm run build)
  1. the app         -> dist/Ultimate Accountant/Ultimate Accountant.exe  (folder)
  2. the uninstaller -> copied into that folder as Uninstall.exe
  3. the installer   -> dist/release/Install.exe, carrying the folder inside it
  4. the update      -> dist/release/Update.exe, carrying only the app's own code

Install.exe is what a new machine gets. Update.exe is what installed copies
built on the same base fetch instead - a few MB rather than the whole program.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Ultimate Accountant"
DIST = ROOT / "dist"
PROGRAM = DIST / NAME
RELEASE = DIST / "release"


# The project's own Python, with every package in requirements.txt: a build from another one
# leaves libraries out, and the installed app then can't start (1.3.4 lacked pydantic_settings).
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run(spec, label):
    print(f"\n=== {label} " + "=" * (58 - len(label)))
    result = subprocess.run(
        [str(PYTHON), "-m", "PyInstaller", spec, "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")],
        cwd=ROOT,
    )
    if result.returncode != 0:
        sys.exit(f"\n{label} failed - the output above says why.")


def megabytes(path):
    if path.is_file():
        return path.stat().st_size / 1048576
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1048576


def main():
    # The app has to be able to start: every top-level module it imports must be in that Python.
    import os
    import tempfile
    scratch = tempfile.mkdtemp(prefix="ua-build-check-")    # never the real books on this PC
    env = {**os.environ, **{k: scratch for k in ("ASSURANCE_DATA_DIR", "ASSURANCE_OFFICE_DIR",
                                                 "ASSURANCE_AI_DIR", "ASSURANCE_DOWNLOADS_DIR")}}
    check = subprocess.run([str(PYTHON), "-c", "import app.main, launch"], cwd=ROOT,
                           capture_output=True, text=True, env=env)
    if check.returncode != 0:
        sys.exit(f"The project's Python ({PYTHON}) can't load the app:\n{check.stderr[-800:]}")
    # 0. The installer writes its version into Add/Remove Programs and the app
    #    compares updates against its own; they have to be the same number.
    sys.path.insert(0, str(ROOT))
    from app.backend.version import VERSION
    said = re.search(r'^VERSION = "([^"]+)"',
                     (ROOT / "packaging" / "installer.py").read_text(encoding="utf-8"), re.M)
    if not said or said.group(1) != VERSION:
        sys.exit(f"packaging/installer.py says {said and said.group(1)}, "
                 f"app/backend/version.py says {VERSION} - make them the same.")
    #    And where updates come from and the key they are signed with, or its
    #    copies could never fetch one (build.spec packs deploy.env; docs/configuration.md).
    from app.backend import deploy
    if "--without-updates" not in sys.argv and not (deploy.get("update_repo") and deploy.get("update_public_key")):
        sys.exit("UA_UPDATE_REPO and UA_UPDATE_PUBLIC_KEY are not set (deploy.env or the environment), "
                 "so the copies built now could not update themselves.\nSet them (docs/configuration.md), "
                 "or build with --without-updates for a copy that never will.")

    # 0. the Invoice Bridge's interface, which build.spec copies in as built.
    ui = ROOT / "bridge-ui"
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm is not on this computer: the Invoice Bridge's interface (bridge-ui) is built with "
                 "Node.js. Install it from https://nodejs.org and build again.")
    print("\n=== the Invoice Bridge's interface " + "=" * 35)
    if not (ui / "node_modules").exists() and subprocess.run([npm, "ci"], cwd=ui).returncode:
        sys.exit("npm ci failed in bridge-ui - the output above says why.")
    if subprocess.run([npm, "run", "build"], cwd=ui).returncode:
        sys.exit("The Invoice Bridge's interface did not build - the output above says why.")

    # 1. the app itself. This recreates the folder from scratch, which is why
    #    the uninstaller is built after it and not before - it would be wiped.
    run("build.spec", "the app")
    app_exe = PROGRAM / f"{NAME}.exe"
    if not app_exe.exists():
        sys.exit(f"{app_exe} is not where it should be.")

    # 2. the uninstaller, which ships inside the program folder so that it is
    #    installed alongside the app rather than handed over separately
    run("packaging/uninstall.spec", "the uninstaller")
    built = DIST / "Uninstall.exe"
    if not built.exists():
        sys.exit("Uninstall.exe is not where it should be.")
    shutil.move(str(built), str(PROGRAM / "Uninstall.exe"))

    # 3. the installer, which now has both to carry.
    #    Running the app out of dist to test it leaves a database behind, and
    #    that folder would otherwise be swept up into the payload and shipped
    #    - handing every user a copy of whatever was reconciled on this
    #    machine. It is not test residue to shrug at; it is someone else's
    #    ledger.
    stray = PROGRAM / "data"
    if stray.exists():
        print(f"\n  removing {stray.relative_to(ROOT)} from the payload")
        shutil.rmtree(stray)

    # 3b. Fail closed on anyone else's ledger.
    #
    #     Stripping one named folder is not a policy, it is a memory. This walks
    #     the finished payload and refuses to build if a spreadsheet, PDF or
    #     database sits anywhere except SampleData/ - the synthetic corpus that
    #     tools/generate_sample_data.py produces from a fixed seed.
    #
    #     This exists because a real engagement's folder was once wired in as the
    #     "Load sample data" source and reached a shipped payload: a year of
    #     GSTR-2B filings and three bank statements under a live GSTIN. For a
    #     product whose promise is that nothing leaves your machine, that is the
    #     one defect that ends it. An error here is the cheap version of it.
    LEDGER_SUFFIXES = {".xlsx", ".xls", ".csv", ".pdf", ".db", ".sqlite", ".sqlite3"}
    allowed = PROGRAM / "_internal" / "SampleData"
    intruders = [
        f for f in PROGRAM.rglob("*")
        if f.is_file()
        and f.suffix.lower() in LEDGER_SUFFIXES
        and allowed not in f.parents
    ]
    if intruders:
        print("\n  REFUSING TO BUILD - data outside SampleData/ is in the payload:")
        for f in intruders[:20]:
            print(f"    {f.relative_to(PROGRAM)}")
        if len(intruders) > 20:
            print(f"    ... and {len(intruders) - 20} more")
        sys.exit(
            "\n  Every one of these would ship to every buyer. Move real books out of "
            "the payload,\n  regenerate with tools/generate_sample_data.py, and build again."
        )
    print("  payload carries no ledger data outside SampleData/")

    run("packaging/install.spec", "the installer")
    installer = DIST / "Install.exe"
    if not installer.exists():
        sys.exit("Install.exe is not where it should be.")

    RELEASE.mkdir(exist_ok=True)
    final = RELEASE / "Install.exe"
    if final.exists():
        final.unlink()
    shutil.move(str(installer), str(final))

    # 4. the update: the app's own code and the base it was built on.
    internal = PROGRAM / "_internal"
    payload = DIST / "update-payload"
    shutil.rmtree(payload, ignore_errors=True)
    shutil.copytree(internal / "app", payload / "app", ignore=shutil.ignore_patterns("app.previous"))
    shutil.copy2(internal / "BASE", payload / "BASE")
    run("packaging/update.spec", "the update")
    update = RELEASE / "Update.exe"
    if update.exists():
        update.unlink()
    shutil.move(str(DIST / "Update.exe"), str(update))

    print("\n" + "=" * 68)
    print("  Send this one file:")
    print(f"    {final}   ({megabytes(final):.0f} MB)")
    print(f"  Installed copies on the same base update with {update.name} ({megabytes(update):.1f} MB).")
    print()
    print("  It installs:")
    print(f"    {NAME}.exe    the app        ({megabytes(app_exe):.0f} MB)")
    print(f"    Uninstall.exe  removes it     "
          f"({megabytes(PROGRAM / 'Uninstall.exe'):.0f} MB)")
    print(f"    ...into %LOCALAPPDATA%\\Programs\\{NAME}"
          f"   ({megabytes(PROGRAM):.0f} MB unpacked)")
    print("=" * 68)


if __name__ == "__main__":
    main()
