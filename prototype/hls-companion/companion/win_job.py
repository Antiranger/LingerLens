"""Own a child process tree so Stop can end it in one synchronous call.

Why this exists
---------------
yt-dlp delegates a live HLS download to its *external* ffmpeg downloader, so
the process this app holds is a parent, not the process that owns the media
pipe:

    python (yt-dlp)  ->  ffmpeg -i <live m3u8> -f mpegts -

On Windows ``Popen.terminate()`` is ``TerminateProcess`` on that one PID.
The ffmpeg grandchild survives, keeps the inherited stdout handle open, and
therefore keeps the reader (``_TcpPump``) blocked. ``process.wait()`` cannot
observe EOF until the grandchild dies, so a Stop built from terminate+wait
spends its whole timeout budget (8s, see ``YtDlpLiveIngest.stop``) before
falling through to ``kill()`` -- and even then the grandchild is still there
holding the pipe for the pump's own 3s join.

A Job Object moves the ownership of "the tree" from us to the kernel: closing
or terminating the job kills every descendant, including grandchildren that
were spawned after assignment. Measured on this machine: 12.6s -> 0.02s.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

_JOB_OBJECT_EXTENDED_LIMITS_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


if sys.platform == "win32":  # pragma: no cover - platform branch
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


class ProcessTreeJob:
    """Create a kill-on-close Job Object and spawn owned children into it.

    Children may be nested inside another job: since Windows 8 a process can
    belong to more than one job, so this composes with the desktop shell's own
    job (``desktop/windows_job.py``) instead of conflicting with it.

    On non-Windows platforms this degrades to a plain ``Popen`` (no job), which
    keeps the module importable in CI on any OS.
    """

    def __init__(self, *, unsupported: bool = False) -> None:
        self._handle: Any = None
        self._children: list[subprocess.Popen[bytes]] = []
        if unsupported or sys.platform != "win32":
            return
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMITS_INFORMATION,
            ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            code = ctypes.get_last_error()
            _kernel32.CloseHandle(handle)
            raise ctypes.WinError(code)
        self._handle = handle

    @property
    def owns_tree(self) -> bool:
        return self._handle is not None

    def spawn(self, command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        """Start ``command`` owned by this job.

        Never pass ``CREATE_SUSPENDED``/``creationflags`` that conflict with a
        synchronous assignment; the child is assigned immediately after spawn,
        which is what also reaps it when it dies before any later Stop.
        """
        process = subprocess.Popen(command, **kwargs)
        self.adopt(process)
        return process

    def adopt(self, process: subprocess.Popen[bytes]) -> bool:
        """Assign an already-running process to this job.

        A process that already exited, or one the OS refuses to move, must not
        break the caller: the explicit terminate path still covers it.
        """
        self._children.append(process)
        if self._handle is None:
            return False
        try:
            assigned = _kernel32.AssignProcessToJobObject(self._handle, int(process._handle))  # noqa: SLF001
        except Exception:
            return False
        return bool(assigned)

    def terminate(self) -> None:
        """Kill every process in the job, then wait briefly for the direct children.

        This is the one call that closes the whole tree in a single kernel
        operation, so a Stop no longer has to guess a timeout for a grandchild
        it cannot see.
        """
        handle, self._handle = self._handle, None
        if handle is not None:
            if not _kernel32.TerminateJobObject(handle, 1):
                _kernel32.CloseHandle(handle)  # KILL_ON_JOB_CLOSE still ends it
            else:
                _kernel32.CloseHandle(handle)
        for process in self._children:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                # TerminateJobObject already ended it; this only reaps the
                # handle, so it must never become a place where Stop waits.
                process.wait(timeout=0.5)
            except Exception:
                pass
        self._children.clear()

    def close(self) -> None:
        """Release the job without killing anything that is still healthy."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _kernel32.CloseHandle(handle)

    def __enter__(self) -> "ProcessTreeJob":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive
        try:
            self.close()
        except Exception:
            pass


def terminate_tree(process: subprocess.Popen[bytes], timeout: float = 2.0) -> float:
    """Fallback for a process that is NOT job-owned: kill it and its tree.

    Uses ``taskkill /T`` because the Windows API offers no other way to reach a
    grandchild by parent PID. Returns the seconds spent.
    """
    if os.name != "nt" or process.poll() is not None:
        return 0.0
    import time

    started = time.perf_counter()
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=timeout)
    except Exception:
        pass
    return time.perf_counter() - started
