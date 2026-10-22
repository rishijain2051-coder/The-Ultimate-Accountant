"""The Keeper's files in R2, by short-lived signed links: the office server gets links to
put sealed files, and approved devices whose person has the books tick get links to read
them. The files are sealed (.ukeep) before they leave the office; R2 and this control plane
only ever hold ciphertext.

Links are AWS SigV4 query-signed (R2 speaks S3), made here with the standard library.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from urllib.parse import quote, urlparse

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cp import db
from cp.auth import Who, approved
from cp.office import office_server

router = APIRouter()

CID = r"c[0-9a-f]{12}"
BOOKS = re.compile(rf"office/(index\.ukeep|current/{CID}\.ukeep|daily/{CID}/\d{{4}}-\d\d-\d\d\.ukeep)")
BACKUPS = re.compile(r"office/(backups\.ukeep|backups/\d{8}-\d{6}/T[A-Z]*BK[\w]{1,60}\.\d{3}\.ukeep)", re.I)


def presign(method: str, key: str, expires: int, now: dt.datetime | None = None) -> str:
    endpoint, bucket = db.env("R2_ENDPOINT").rstrip("/"), db.env("R2_BUCKET")
    access, secret = db.env("R2_ACCESS_KEY_ID"), db.env("R2_SECRET_ACCESS_KEY")
    now = now or dt.datetime.now(dt.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = urlparse(endpoint).netloc
    scope = f"{day}/auto/s3/aws4_request"
    uri = "/" + quote(f"{bucket}/{key}", safe="/-_.~")
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{access}/{scope}", "X-Amz-Date": amz,
         "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(q.items()))
    canonical = f"{method}\n{uri}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    k = ("AWS4" + secret).encode()
    for part in (day, "auto", "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{endpoint}{uri}?{query}&X-Amz-Signature={sig}"


def _path(path: str, books_only: bool = False) -> str:
    if not (BOOKS.fullmatch(path or "") or (not books_only and BACKUPS.fullmatch(path or ""))):
        raise HTTPException(400, "Not a Keeper file.")
    return path


class Upload(BaseModel):
    path: str


@router.post("/api/office/upload-url")
def upload_url(body: Upload, claims: dict = Depends(office_server)):
    return {"url": presign("PUT", _path(body.path), 3600)}


@router.post("/api/office/download-url")
def download_url(body: Upload, claims: dict = Depends(office_server), c: psycopg.Connection = Depends(db.conn)):
    """For a restore, on the office server only: the viewer never fetches a Tally backup."""
    if BACKUPS.fullmatch(body.path or "") and body.path != "office/backups.ukeep":
        db.audit(c, "backup.downloaded", user_id=claims.get("sub"), device_id=claims.get("device"),
                 detail={"path": body.path})
        c.commit()
    return {"url": presign("GET", _path(body.path), 3600)}


@router.get("/api/books/url")
def read_url(path: str, who: Who = Depends(approved), c: psycopg.Connection = Depends(db.conn)):
    if not who.has("books.view"):
        raise HTTPException(403, "This person can't see the books.")
    if who.device["kind"] != "viewer":
        raise HTTPException(403, "The books open on a phone or a browser, not here.")
    _path(path, books_only=True)
    if path != "office/index.ukeep":
        db.audit(c, "books.read", user_id=who.user["id"], device_id=who.device["id"], detail={"path": path})
        c.commit()
    return {"url": presign("GET", path, 300)}
