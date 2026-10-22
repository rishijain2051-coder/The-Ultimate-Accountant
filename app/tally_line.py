"""The one line to Tally, shared by every part of the app that talks to it.

Tally answers one connection at a time and drops a second one that arrives
while it works. The Invoice Bridge (which posts) and the Keeper (which only
reads) must therefore take turns, also when the Keeper runs as its own
process from the Task Scheduler. A Windows named mutex is that turn: held for
one request at a time, by whichever process and thread is talking to Tally.

This is the only piece the two modules share. It knows nothing about Tally.

Within one process it behaves exactly as the threading.Lock it replaced in the
Invoice Bridge: nobody, not even the thread holding it, can take it twice. That
is a threading.Lock taken first; the named mutex, taken second, carries the
turn to other processes. A process that died holding it does not keep it.
The thread that takes it must be the one that releases it.
"""

from __future__ import annotations

import sys
import threading
import time

NAME = "Local\\UltimateAccountant.TallyLine"

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    _k32.ReleaseMutex.restype = wintypes.BOOL

    _WAIT_OBJECT_0, _WAIT_ABANDONED, _WAIT_TIMEOUT = 0x0, 0x80, 0x102
    _INFINITE = 0xFFFFFFFF

    class NamedLock:
        def __init__(self, name: str):
            self._here = threading.Lock()
            self._handle = _k32.CreateMutexW(None, False, name)
            if not self._handle:
                raise OSError(ctypes.get_last_error(), f"Could not create the mutex {name}")

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            forever = blocking and (timeout is None or timeout < 0)
            end = None if forever else time.monotonic() + (max(timeout, 0) if blocking else 0)
            if not (self._here.acquire() if forever else self._here.acquire(timeout=max(end - time.monotonic(), 0))):
                return False
            ms = _INFINITE if forever else min(int(max(end - time.monotonic(), 0) * 1000), _INFINITE - 1)
            r = _k32.WaitForSingleObject(self._handle, ms)
            if r in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                return True
            self._here.release()
            if r == _WAIT_TIMEOUT:
                return False
            raise OSError(ctypes.get_last_error(), "Waiting for the line to Tally failed")

        def release(self) -> None:
            if not _k32.ReleaseMutex(self._handle):
                raise RuntimeError("Released the line to Tally from a thread that does not hold it")
            self._here.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *exc):
            self.release()

    LINE = NamedLock(NAME)
else:
    # ponytail: off Windows the line holds within one process only; the app ships for Windows.
    LINE = threading.Lock()
