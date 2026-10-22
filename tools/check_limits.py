"""Checks that the CPU limit the launcher applies really holds.

    python installer\\check_limits.py

Puts itself under the same limit the app runs under, starts one busy
process per processor, and reads from Windows how much CPU the whole group
got while they all ran flat out. Without the limit that is ~100% of the
computer; with it, no more than the limit.
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launcher import CPU_CAP, SHARE, gpu_memory, limit_cpu  # noqa: E402

BUSY = "import time\nt = time.monotonic()\nwhile time.monotonic() - t < 9: pass"


class Accounting(ctypes.Structure):  # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


def job_cpu_seconds(job) -> float:
    a = Accounting()
    k = ctypes.WinDLL("kernel32")
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.c_void_p]
    assert k.QueryInformationJobObject(job, 1, ctypes.byref(a), ctypes.sizeof(a), None)
    return (a.TotalUserTime + a.TotalKernelTime) / 1e7  # 100 ns units


if __name__ == "__main__":
    job = limit_cpu(CPU_CAP)
    assert job, "Windows did not accept the CPU limit"
    n = os.cpu_count() or 2
    procs = [subprocess.Popen([sys.executable, "-c", BUSY]) for _ in range(n)]
    time.sleep(1.5)  # all started and busy
    # One reading per second, the way Task Manager shows it.
    shares = []
    c0, t0 = job_cpu_seconds(job), time.monotonic()
    for _ in range(6):
        time.sleep(1)
        c1, t1 = job_cpu_seconds(job), time.monotonic()
        shares.append((c1 - c0) / ((t1 - t0) * n))
        c0, t0 = c1, t1
    for p in procs:
        p.wait()
    share = max(shares)
    print(f"{n} busy processes, CPU each second: {' '.join(f'{s:.0%}' for s in shares)}"
          f" - highest {share:.1%} (limit {SHARE:.0%})")
    vram = gpu_memory()
    print(f"graphics card: {vram / 2**30:.1f} GB, the AI may use {vram * SHARE / 2**30:.1f} GB" if vram
          else "no graphics card worth using: the AI runs on the CPU, inside the limit")
    assert share <= SHARE, "the CPU limit did not hold"
    print("ok")
