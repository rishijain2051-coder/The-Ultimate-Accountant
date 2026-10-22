"""The .ukeep file: what the Keeper writes, encrypted, for the control plane to keep
(PLAN-keeper.md section 5.2, keys as in PLAN-office.md section 4).

    "UKEEP1\\n" | u32 BE header length | header (UTF-8 JSON) | chunks
    header: {"v": 1, "kid", "kind": "snapshot"|"index"|"tally-backup", "company", "taken_at",
             "chunk": 1048576, "np": base64 of 8 random bytes, "name"?}
    chunk i: AES-256-GCM(DEK, nonce = np || u32 BE i, chunk, aad = SHA-256(header) || u32 BE i || u8 last)

The header is bound into every chunk, the index stops reordering, and the last-chunk flag
stops a file being cut short at a chunk's edge without notice. The browser reads it with
control-plane/public/keeper-crypto.js; tests check the two agree.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from collections.abc import Callable, Iterator
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"UKEEP1\n"
CHUNK = 1 << 20


class UkeepError(ValueError):
    pass


def _aad(hdr_hash: bytes, i: int, last: bool) -> bytes:
    return hdr_hash + struct.pack(">IB", i, 1 if last else 0)


def write(out: BinaryIO, source: BinaryIO, dek: bytes, kid: str, kind: str, **meta) -> dict:
    """Encrypt everything read from source into out. Returns the header."""
    np = os.urandom(8)
    header = {"v": 1, "kid": kid, "kind": kind, "chunk": CHUNK, "np": base64.b64encode(np).decode(),
              **{k: v for k, v in meta.items() if v is not None}}
    hdr = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    out.write(MAGIC + struct.pack(">I", len(hdr)) + hdr)
    h, aes, i = hashlib.sha256(hdr).digest(), AESGCM(dek), 0
    chunk = source.read(CHUNK)
    while True:
        nxt = source.read(CHUNK)
        last = not nxt
        out.write(aes.encrypt(np + struct.pack(">I", i), chunk, _aad(h, i, last)))
        if last:
            return header
        chunk, i = nxt, i + 1


def encrypt(data: bytes, dek: bytes, kid: str, kind: str, **meta) -> bytes:
    import io
    out = io.BytesIO()
    write(out, io.BytesIO(data), dek, kid, kind, **meta)
    return out.getvalue()


def read_header(src: BinaryIO) -> tuple[dict, bytes]:
    if src.read(len(MAGIC)) != MAGIC:
        raise UkeepError("Not a .ukeep file.")
    (n,) = struct.unpack(">I", src.read(4))
    if n > 64 * 1024:
        raise UkeepError("The header is too long.")
    hdr = src.read(n)
    try:
        header = json.loads(hdr)
    except ValueError as exc:
        raise UkeepError("The header can't be read.") from exc
    if header.get("v") != 1:
        raise UkeepError("A newer kind of .ukeep file than this program reads.")
    return header, hdr


def chunks(src: BinaryIO, key_for: Callable[[str], bytes]) -> tuple[dict, Iterator[bytes]]:
    """The header, and the plaintext chunk by chunk. A bad key, a changed header or chunk,
    chunks out of order and a file cut short all raise UkeepError."""
    header, hdr = read_header(src)
    try:
        aes = AESGCM(key_for(header["kid"]))
    except KeyError as exc:
        raise UkeepError(f"This device doesn't have key {header.get('kid')}.") from exc
    np, h, size = base64.b64decode(header["np"]), hashlib.sha256(hdr).digest(), header["chunk"] + 16

    def gen():
        i, block = 0, src.read(size)
        while True:
            nxt = src.read(size)
            last = not nxt
            try:
                yield aes.decrypt(np + struct.pack(">I", i), block, _aad(h, i, last))
            except InvalidTag as exc:
                raise UkeepError("This file was changed, cut short, or isn't for this key.") from exc
            if last:
                return
            block, i = nxt, i + 1
    return header, gen()


def decrypt(data: bytes, key_for: Callable[[str], bytes]) -> tuple[dict, bytes]:
    import io
    header, parts = chunks(io.BytesIO(data), key_for)
    return header, b"".join(parts)
