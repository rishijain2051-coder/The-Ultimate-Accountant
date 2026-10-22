"""The deployer's own settings: where updates come from, the key they are signed
with, the office's control plane. docs/configuration.md lists each one.

Each is read from the environment (UA_<NAME>), else from deploy.env - KEY=value
lines, as in deploy.env.example - beside the program: the repository's root when
run from the source; build.spec packs it into the installed app. Nothing has a
real default: a copy built without them never updates itself and has no office.
"""

import os

from app.backend.paths import RESOURCE_DIR


def _file():
    try:
        lines = (RESOURCE_DIR / "deploy.env").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    pairs = (ln.split("=", 1) for ln in lines if "=" in ln and not ln.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip('"') for k, v in pairs}


_FILE = _file()


def get(name, default=""):
    """UA_<NAME> from the environment, then deploy.env, then `default`."""
    key = f"UA_{name.upper()}"
    return os.environ.get(key) or _FILE.get(key) or default
