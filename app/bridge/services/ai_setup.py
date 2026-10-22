"""The AI reader is the app's one AI (app/backend/ai.py): the same runtime and
model the AI checker of Reconciliation and Exports runs, fetched once, when
asked for, and run beside the app. This is how the bridge's Settings card sees
it and asks for it. llm.py finds the runtime by itself as soon as it is there:
nothing to restart.
"""

from __future__ import annotations

from typing import Any

from app.backend import ai


def status() -> dict[str, Any]:
    s = ai.status()
    running = s["status"] in ("downloading", "unpacking")
    return {
        "can_install": s["status"] in ("absent", "failed"),
        "running": running,
        "folder": s["folder"],
        "model": s["model"],
        "step": f"{'Unpacking' if s['status'] == 'unpacking' else 'Downloading'} {s['step']}" if running else None,
        "percent": round(100 * s["done"] / s["total"], 1) if running and s["total"] else None,
        "error": s["message"] if s["status"] == "failed" else None,
        # Another app's copy, when the app runs that one (Invoice Bridge or
        # Assurance Console installed on their own).
        "shared": s["shared"],
    }


def start() -> dict[str, Any]:
    ai.get_soon()
    return status()
