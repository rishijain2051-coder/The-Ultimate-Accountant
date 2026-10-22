"""The control plane's secrets file: what is filled in, and the keys we make ourselves.

    python scripts/office_secrets.py              which lines are filled in, and whether they look right
    python scripts/office_secrets.py --generate   fill in the (generated) lines that are still empty
    python scripts/office_secrets.py --push-vercel [NAME ...]
                                                   put them (all, or those named) into Vercel's Production
                                                   environment as Sensitive; run it yourself

The file is %USERPROFILE%\\.ultimate-accountant\\office.env, outside the repository.
This script never prints a value: only names, and "filled in", "missing" or what looks wrong.
"""

from __future__ import annotations

import base64
import re
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

PATH = Path.home() / ".ultimate-accountant" / "office.env"

_b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731


def _jwt_key() -> dict[str, str]:
    der = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return {"JWT_SIGNING_KEY": _b64(der)}


def _vapid() -> dict[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"VAPID_PRIVATE_KEY": _b64(raw), "VAPID_PUBLIC_KEY": _b64(pub)}


GENERATED = ("JWT_SIGNING_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "CRON_SECRET")

# What each value must look like; a wrong paste is caught here instead of at deploy.
SHAPES = {
    "PUBLIC_ORIGIN": (r"https://[a-z0-9.-]+(:\d+)?", "an https:// address with no path, e.g. https://x.vercel.app"),
    "GOOGLE_CLIENT_ID": (r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", "ends in .apps.googleusercontent.com"),
    "GOOGLE_CLIENT_SECRET": (r"GOCSPX-[A-Za-z0-9_-]+", "starts with GOCSPX-"),
    "DATABASE_URL": (r"postgres(ql)?://[^\s\[\]]+:6543/[^\s]+",
                     "postgresql://…:6543/…, the transaction pooler, with [YOUR-PASSWORD] replaced"),
    "R2_ENDPOINT": (r"https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com", "https://<32-character account id>.r2.cloudflarestorage.com"),
    "R2_ACCESS_KEY_ID": (r"[0-9a-f]{32}", "32 hex characters"),
    "R2_SECRET_ACCESS_KEY": (r"[0-9a-f]{64}", "64 hex characters"),
    "R2_BUCKET": (r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", "a bucket name"),
}


def read(path: Path = PATH) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {}
    for ln in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=(.*)$", ln)
        if m:
            values[m.group(1)] = m.group(2).strip()
    return lines, values


def generate(path: Path = PATH) -> list[str]:
    """Fill the empty (generated) lines and return their names. A filled one is kept,
    except that the VAPID pair is made again together if either half is empty."""
    lines, values = read(path)
    made: dict[str, str] = {}
    if not values.get("JWT_SIGNING_KEY"):
        made.update(_jwt_key())
    if not (values.get("VAPID_PRIVATE_KEY") and values.get("VAPID_PUBLIC_KEY")):
        made.update(_vapid())
    if not values.get("CRON_SECRET"):
        made["CRON_SECRET"] = _b64(secrets.token_bytes(32))
    out = []
    for ln in lines:
        m = re.match(r"(\s*([A-Z0-9_]+)\s*=)", ln)
        out.append(m.group(1) + made[m.group(2)] if m and m.group(2) in made else ln)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)
    return sorted(made)


def report(path: Path = PATH) -> int:
    _lines, values = read(path)
    bad = 0
    for name in [*SHAPES, *GENERATED]:
        v = values.get(name, "")
        if not v:
            state, bad = "missing", bad + 1
        elif name in SHAPES and not re.fullmatch(SHAPES[name][0], v):
            state, bad = f"filled in, but should be {SHAPES[name][1]}", bad + 1
        else:
            state = "filled in"
        print(f"  {name:22} {state}")
    print(f"\n{'Everything is filled in.' if not bad else f'{bad} to fill in or fix.'}  ({path})")
    return 1 if bad else 0


def push_vercel(names: list[str], path: Path = PATH) -> int:
    """Put each named value (all of them if none are named) into the Vercel project's
    Production environment, as Sensitive, through this repository's own CLI. The value
    goes in on stdin; only the name and whether it worked are printed."""
    import subprocess
    _lines, values = read(path)
    cp = Path(__file__).resolve().parents[1] / "control-plane"
    cli = cp / "node_modules" / ".bin" / "vercel.cmd"
    if not cli.exists():
        print("The Vercel CLI is not installed: run .\\scripts\\vercel.ps1 once first.")
        return 1
    failed = 0
    for name in names or [*SHAPES, *GENERATED]:
        value = values.get(name, "")
        if not value:
            print(f"  {name:22} skipped: not filled in")
            failed += 1
            continue
        r = subprocess.run([str(cli), "--global-config", str(cp / ".vercel-cli"), "--cwd", str(cp),
                            "env", "add", name, "production", "--sensitive", "--force", "--yes"],
                           input=value, capture_output=True, text=True, encoding="utf-8")
        said = (r.stdout + r.stderr).replace(value, "***")
        lines = [ln.strip() for ln in said.splitlines() if ln.strip()]
        reason = next((ln for ln in lines if "Error" in ln), lines[-1] if lines else "")[:160]
        print(f"  {name:22} {'sent to Vercel (Production, Sensitive)' if r.returncode == 0 else 'FAILED ' + reason}")
        failed += r.returncode != 0
    return 1 if failed else 0


def _selfcheck() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "office.env"
        p.write_text("# c\nPUBLIC_ORIGIN=https://x.vercel.app\nJWT_SIGNING_KEY=\nVAPID_PRIVATE_KEY=\n"
                     "VAPID_PUBLIC_KEY=\nCRON_SECRET=keep\n", encoding="utf-8")
        assert generate(p) == ["JWT_SIGNING_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY"]
        lines, v = read(p)
        assert lines[0] == "# c" and v["CRON_SECRET"] == "keep" and v["PUBLIC_ORIGIN"] == "https://x.vercel.app"
        serialization.load_der_private_key(base64.urlsafe_b64decode(v["JWT_SIGNING_KEY"] + "=="), None)
        pub = base64.urlsafe_b64decode(v["VAPID_PUBLIC_KEY"] + "==")
        priv = int.from_bytes(base64.urlsafe_b64decode(v["VAPID_PRIVATE_KEY"] + "=="), "big")
        assert ec.derive_private_key(priv, ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint) == pub
        assert generate(p) == []  # nothing empty: nothing changes


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
        print("selfcheck passed")
    elif not PATH.exists():
        print(f"No secrets file at {PATH}.")
        sys.exit(1)
    elif "--push-vercel" in sys.argv:
        sys.exit(push_vercel(sys.argv[sys.argv.index("--push-vercel") + 1:]))
    elif "--generate" in sys.argv:
        print("Made:", ", ".join(generate()) or "nothing (all already filled in)")
    else:
        sys.exit(report())
