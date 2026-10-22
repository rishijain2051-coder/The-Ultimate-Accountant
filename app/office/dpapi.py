"""Windows DPAPI: bytes only this Windows user, on this PC, can turn back into what they were.

The PC's private keys and the Keeper's data keys are kept this way. A copy of the office
folder on another PC, or under another Windows user, is of no use to anyone.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("DPAPI is Windows only")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32")
_kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
_ENTROPY = b"Ultimate Accountant office"
_UI_FORBIDDEN = 0x1


def _call(fn, data: bytes) -> bytes:
    src = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_in, blob_ent, blob_out = (_Blob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char))),
                                   _Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char))), _Blob())
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "Windows refused to protect or unprotect the data")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def protect(data: bytes) -> bytes:
    return _call(_crypt32.CryptProtectData, data)


def unprotect(data: bytes) -> bytes:
    return _call(_crypt32.CryptUnprotectData, data)
