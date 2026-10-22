"""
Publish a release to GitHub, where installed copies look for their updates.

    python packaging/publish_release.py --make-key        # once: your own signing key
    python packaging/build_release.py                     # first: dist/release/Install.exe
    python packaging/publish_release.py "What is new, in a sentence or two."

Writes latest.json - the version, where its installer is, the installer's
SHA-256 fingerprint, and the same for Update.exe with the base it was built on
- signs it with the release key, and uploads the four files as a GitHub
release on UA_UPDATE_REPO (docs/configuration.md) with the GitHub CLI (`gh`,
signed in). Installed copies read
latest.json from the newest release, check its signature against the public
key built into them (app/backend/updates.py), and only then fetch Update.exe
(a few MB) when they stand on that base, or the whole installer when they do
not; either must match its fingerprint to be kept.

The release key lives in your profile, never in this folder or the program:

    %USERPROFILE%\\.ultimate-accountant\\release-signing-key.pem   (or ASSURANCE_SIGNING_KEY)

`--make-key` writes a new one there and prints the public half to set as
UA_UPDATE_PUBLIC_KEY. Keep a copy of it somewhere safe and offline. Without it no update can be
signed, and every installed copy would have to be updated by hand once more,
with an installer carrying a new key.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.backend.updates import PUBLIC_KEY, REPO, verify   # noqa: E402
from app.backend.version import VERSION                    # noqa: E402

INSTALLER = ROOT / "dist" / "release" / "Install.exe"
UPDATE = ROOT / "dist" / "release" / "Update.exe"
UPDATE_BASE = ROOT / "dist" / "update-payload" / "BASE"
KEY = Path(os.environ.get("ASSURANCE_SIGNING_KEY")
           or Path.home() / ".ultimate-accountant" / "release-signing-key.pem")


def make_key():
    """A new Ed25519 signing key, never over an old one; prints the public half."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if KEY.exists():
        sys.exit(f"{KEY} already exists; move it away first if you really mean to replace it.")
    key = Ed25519PrivateKey.generate()
    KEY.parent.mkdir(parents=True, exist_ok=True)
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    print(f"Wrote {KEY}. Keep a copy offline.\nSet this in deploy.env:\n\nUA_UPDATE_PUBLIC_KEY={public}")


def main():
    if sys.argv[1:] == ["--make-key"]:
        return make_key()
    if not (REPO and PUBLIC_KEY):
        sys.exit("Set UA_UPDATE_REPO and UA_UPDATE_PUBLIC_KEY first (deploy.env; docs/configuration.md).")
    notes = " ".join(sys.argv[1:]).strip()
    if not notes:
        sys.exit('Say what is new: python packaging/publish_release.py "..."')
    if not INSTALLER.exists() or not UPDATE.exists() or not UPDATE_BASE.exists():
        sys.exit(f"{INSTALLER.parent} does not have both Install.exe and Update.exe - "
                 "run packaging/build_release.py first.")
    built = re.search(r'^VERSION = "([^"]+)"',
                      (ROOT / "packaging" / "installer.py").read_text(encoding="utf-8"), re.M)
    if not built or built.group(1) != VERSION:
        sys.exit(f"packaging/installer.py says {built and built.group(1)}, the app says {VERSION}.")
    if not KEY.exists():
        sys.exit(f"The release key is not at {KEY}.")

    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    data, small = INSTALLER.read_bytes(), UPDATE.read_bytes()
    base = json.loads(UPDATE_BASE.read_text(encoding="utf-8"))
    tag = f"v{VERSION}"
    manifest = json.dumps({
        "product": "ultimate-accountant",
        "version": VERSION,
        "url": f"https://github.com/{REPO}/releases/download/{tag}/Install.exe",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        # Copies built on this base take the app's code alone.
        "update": {"sha256": hashlib.sha256(small).hexdigest(), "size": len(small),
                   "core": base["core"], "modules": base["modules"]},
        "notes": notes,
        "published": date.today().isoformat(),
    }, indent=1).encode("utf-8")
    signature = base64.b64encode(key.sign(manifest)).decode("ascii")
    verify(manifest, signature, PUBLIC_KEY)     # the key is the one the app trusts, or stop here

    out = Path(tempfile.mkdtemp(prefix="release-"))
    (out / "latest.json").write_bytes(manifest)
    (out / "latest.json.sig").write_text(signature, encoding="ascii")
    subprocess.run(["gh", "release", "create", tag, str(INSTALLER), str(UPDATE), str(out / "latest.json"),
                    str(out / "latest.json.sig"), "--repo", REPO,
                    "--title", f"Ultimate Accountant {VERSION}", "--notes", notes], check=True)
    print(f"\nPublished {tag}: https://github.com/{REPO}/releases/tag/{tag}")
    print("Installed copies pick it up at their next check (a little after they start).")


if __name__ == "__main__":
    main()
