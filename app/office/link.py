"""Linking this PC to a person: its keys go to the control plane through the person's browser.

The PC listens once on 127.0.0.1, then opens <control plane>/link with its public keys and a
signature in the #fragment. The person signs in there if they need to, sees the PC's 12
digits, and confirms; their browser comes back to the PC's loopback address with the new
device's id. The PC then waits for an admin to approve it (cloud.Waiting until then).
"""

from __future__ import annotations

import http.server
import platform
import secrets
import threading
import time
import urllib.parse
import webbrowser

from app.office import settings
from app.office.identity import Identity

DONE = (b"<!doctype html><meta charset=utf-8><title>Linked</title><body style=\"font:15px 'Segoe UI',sans-serif;"
        b"padding:40px\"><h1>This PC is linked.</h1><p>Go back to Ultimate Accountant. An admin approves the PC next."
        b"</p></body>")


def link(kind: str, name: str, app_version: str = "", timeout: float = 900, open_page=webbrowser.open) -> Identity:
    """A new identity for this PC as `kind`, linked to whoever confirms in the browser."""
    ident = Identity.new(kind)
    nonce = secrets.token_urlsafe(24)
    got: dict = {}

    class Back(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            ok = self.path.startswith("/done") and q.get("nonce", [""])[0] == nonce and q.get("device")
            if ok:
                got["device"] = q["device"][0]
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DONE if ok else b"Not this.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Back)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        frag = urllib.parse.urlencode({
            "kind": kind, "name": name, "os": f"Windows {platform.release()}", "app_version": app_version,
            "sign_pub": ident.sign_pub, "wrap_pub": ident.wrap_pub, "nonce": nonce, "port": srv.server_address[1],
            "sig": ident.sign("\n".join(["ua-link:v1", kind, nonce, ident.sign_pub, ident.wrap_pub]))})
        open_page(f"{settings.load()['control_plane'].rstrip('/')}/link#{frag}")
        end = time.time() + timeout
        while "device" not in got and time.time() < end:
            time.sleep(0.2)
    finally:
        srv.shutdown()
    if "device" not in got:
        raise TimeoutError("The PC wasn't linked: the browser didn't come back in time.")
    ident.device_id = got["device"]
    ident.save()
    return ident
