"""What the office server tells people after a sync (PLAN-office section 6).

    overdue bills       a bill that has crossed its due date since it was last looked at
    big transactions    a voucher, new since the last sync, at or above the threshold set in
                        the admin console (default ₹1,00,000)
Both are sealed with the books' data key, so the control plane passes on only ciphertext;
the device opens it. A company's first sync only remembers what is there: nobody is told
about every old bill at once. Each bill and voucher is told about once.

    a sync failing, the newest Tally backup over a week old
are said plainly to admins, with no figures: once per failure, once a day for the backup.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.office import roster, settings

DEFAULT_THRESHOLD = 100_000 * 100           # paise


def _path():
    return settings.office_dir() / "keeper" / "told.json"


def _rupees(p: int) -> str:
    """₹1,23,456 in the Indian way, whole rupees."""
    s = str(abs(p) // 100)
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    return "₹" + (head + "," + tail if head else tail)


def seal(message: dict, kid: str, dek: bytes) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, json.dumps(message, separators=(",", ":")).encode(), f"ua-push:v1:{kid}".encode())
    return {"kid": kid, "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def _list(lines: list[str], limit: int = 5) -> str:
    return "\n".join(lines[:limit]) + (f"\nand {len(lines) - limit} more" if len(lines) > limit else "")


def threshold() -> int:
    r = roster.current()
    v = (r.claims.get("settings") or {}).get("big_threshold_paise") if r else None
    return int(v) if v else DEFAULT_THRESHOLD


def work_out(snap: dict, told: dict, today: dt.date, big_at: int) -> list[tuple[str, dict]]:
    """The notices one company's new snapshot calls for, as (kind, message); told is updated."""
    cid, name = snap["company"]["cid"], snap["company"]["name"]
    seen = told.setdefault("companies", {}).setdefault(cid, {"bills": [], "vouchers": [], "seeded": False})
    bills_told, vouchers_seen = set(seen["bills"]), set(seen["vouchers"])
    overdue = [b for b in snap["bills"] if b["due"] and b["due"] < today.isoformat()]
    new_overdue = [b for b in overdue if f"{b['ledger']}|{b['name']}" not in bills_told]
    new_big = [v for v in snap["vouchers"] if v["id"] not in vouchers_seen and not v["optional"]
               and sum(x["amount"] for x in v["lines"] if x["amount"] > 0) >= big_at]
    out = []
    if seen["seeded"]:
        if new_overdue:
            parties: dict[str, list] = {}
            for b in new_overdue:
                parties.setdefault(b["ledger"], []).append(b)
            lines = [f"{p}: {_rupees(sum(b['amount'] for b in bs))} {'to receive' if sum(b['amount'] for b in bs) > 0 else 'to pay'}"
                     f" ({len(bs)} bill{'s' * (len(bs) > 1)})" for p, bs in
                     sorted(parties.items(), key=lambda kv: -abs(sum(b["amount"] for b in kv[1])))]
            out.append(("overdue", {"title": f"Overdue in {name}: {len(parties)} part{'ies' if len(parties) > 1 else 'y'}",
                                    "body": _list(lines), "url": "/books#outstanding?late=yes", "cid": cid}))
        if new_big:
            lines = [f"{v['type']} {v['number']}{' · ' + v['party'] if v['party'] else ''}: "
                     f"{_rupees(sum(x['amount'] for x in v['lines'] if x['amount'] > 0))}" for v in new_big]
            one = new_big[0]
            out.append(("big", {"title": f"Big transaction{'s' * (len(new_big) > 1)} in {name}", "body": _list(lines),
                                "url": f"/books#voucher/{one['id']}" if len(new_big) == 1 else "/books#vouchers", "cid": cid}))
    seen["bills"] = sorted({f"{b['ledger']}|{b['name']}" for b in overdue})     # paid ones drop off
    seen["vouchers"] = sorted({v["id"] for v in snap["vouchers"]})
    seen["seeded"] = True
    return out


def after_sync(cloud, status: dict, snaps: list[dict], kid: str, dek: bytes, today: dt.date | None = None) -> int:
    """Tell what this sync calls for. Returns how many notices went out. Never raises: a
    notice not sent must not undo a sync."""
    today = today or dt.date.today()
    told = settings.read_json(_path()) or {}
    sent = 0
    try:
        notices = []
        if status.get("error"):
            if not told.get("sync_failed"):
                notices.append(("alert", {"title": "The Keeper couldn't sync",
                                          "body": "See the Keeper page on the office server for what stopped it."}))
            told["sync_failed"] = True
        else:
            told["sync_failed"] = False
            big_at = threshold()
            for snap in snaps:
                notices += work_out(snap, told, today, big_at)
        b = status.get("backup") or {}
        if b.get("folder") and b.get("old") and told.get("backup_told_on") != today.isoformat():
            notices.append(("alert", {"title": "Tally's backup is old",
                                      "body": "The newest Tally backup is more than a week old. Take one in Tally, "
                                              "or turn on its daily scheduled backup."}))
            told["backup_told_on"] = today.isoformat()
        for kind, message in notices:
            if cloud is None:
                continue
            if kind == "alert":
                cloud.post("/api/office/notify", {"kind": "alert", **message})
            else:
                cloud.post("/api/office/notify", {"kind": kind, **seal(message, kid, dek)})
            sent += 1
        status["notices"] = [k for k, _ in notices]
        settings.write_json(_path(), told)            # only once all went out: else they go again next sync
    except Exception as exc:  # noqa: BLE001 - said on the Keeper page; the books are already sent
        status["notice_error"] = f"{type(exc).__name__}: {exc}"
    return sent
