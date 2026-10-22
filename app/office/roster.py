"""The roster: everyone, their access, and every device, as the control plane signed it.

The office server fetches it every minute. Every request to it is checked against the last
good roster, so a revoked device or a disabled person is turned away within a minute. If the
internet is down, the last roster stands; it is kept, signed, in roster.json.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.office import settings
from app.office.cloud import Cloud, _unb64, check_token


@dataclass
class Roster:
    claims: dict
    fetched_at: float
    users: dict = field(init=False)
    devices: dict = field(init=False)

    def __post_init__(self):
        self.users = {u["id"]: u for u in self.claims.get("users", [])}
        self.devices = {d["id"]: d for d in self.claims.get("devices", [])}

    def person(self, user_id: int, device_id: str) -> dict | None:
        """The person, if they are active and this device of theirs is approved."""
        u, d = self.users.get(user_id), self.devices.get(device_id)
        if not u or not d or u["status"] != "active" or d["status"] != "approved" or d["user_id"] != user_id:
            return None
        return u


_current: Roster | None = None
_lock = threading.Lock()


def current() -> Roster | None:
    """The newest good roster: from memory, or else the one kept on disk."""
    global _current
    with _lock:
        if _current is None:
            kept, key = settings.read_json("roster.json"), settings.load().get("token_key")
            if kept and key:
                try:
                    _current = Roster(check_token(kept["token"], "ua-roster", _unb64(key), allow_expired=True),
                                      kept["fetched_at"])
                except ValueError:
                    _current = None
        return _current


def refresh(cloud: Cloud) -> Roster:
    global _current
    token = cloud.get("/api/office/roster")["roster"]
    r = Roster(check_token(token, "ua-roster", cloud.token_key()), time.time())
    settings.write_json("roster.json", {"token": token, "fetched_at": r.fetched_at})
    with _lock:
        _current = r
    return r


def set_current(r: Roster | None) -> None:
    """For tests: the roster the gate checks against."""
    global _current
    with _lock:
        _current = r
