"""The control plane's one function on Vercel (PLAN-office.md, section 8).

Pages are plain files in public/, served by Vercel's CDN; this answers /api/* only.
"""

import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from cp import admin, auth, blobs, devices, office, push, tokens

app = FastAPI(title="Ultimate Accountant control plane", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    # The cause goes to Vercel's logs; the person gets the sign-in page or a plain sentence.
    print(f"Unexpected error on {request.method} {request.url.path}:\n{traceback.format_exc()}", flush=True)
    if request.url.path.startswith("/api/auth/"):
        return RedirectResponse("/?signin=failed", status_code=302)
    return JSONResponse({"detail": "Something went wrong on our side. Try again in a moment."}, status_code=500)


@app.middleware("http")
async def guard(request: Request, call_next):
    # A cross-site page can't add a custom header without a CORS preflight, which is never
    # granted, so this header on every change stops cross-site requests riding the cookie.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-ua-request") != "1":
        return JSONResponse({"detail": "Refused: not from this site's pages."}, status_code=403)
    r = await call_next(request)
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(admin.router)
app.include_router(office.router)
app.include_router(tokens.router)
app.include_router(blobs.router)
app.include_router(push.router)
