"""Darwin-specific support for secure process resource limits."""

import ctypes
import errno
import os
import sys
from collections.abc import Callable
from typing import Any

_PROC_PIDTASKINFO = 4


class _ProcTaskInfo(ctypes.Structure):
    """Public proc_taskinfo ABI from Darwin proc_info.h."""

    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


def _read_darwin_virtual_size_bytes() -> int:
    """Return the current process virtual size through Darwin's public ABI."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int

    info = _ProcTaskInfo()
    expected_size = ctypes.sizeof(info)
    ctypes.set_errno(0)
    written = proc_pidinfo(
        os.getpid(),
        _PROC_PIDTASKINFO,
        0,
        ctypes.byref(info),
        expected_size,
    )
    if written != expected_size:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(
            error_number,
            "proc_pidinfo returned an incomplete PROC_PIDTASKINFO record "
            f"({written} of {expected_size} bytes)",
        )

    virtual_size = int(info.pti_virtual_size)
    if virtual_size <= 0:
        raise OSError(errno.EIO, "proc_pidinfo returned an invalid virtual size")
    return virtual_size


def rlimit_as_ceiling(
    requested_bytes: int,
    resource_module: Any,
    *,
    virtual_size_reader: Callable[[], int] | None = None,
) -> int:
    """Return a secure RLIMIT_AS ceiling for the current platform.

    XNU rejects an address-space limit below the process's existing VM map.
    On Darwin, treat the configured value as a growth budget above the caller's
    trusted bootstrap baseline. Any stricter inherited hard limit
    remains authoritative.
    """
    if (
        isinstance(requested_bytes, bool)
        or not isinstance(requested_bytes, int)
        or requested_bytes <= 0
    ):
        raise OSError(errno.EINVAL, "RLIMIT_AS budget must be a positive integer")
    if sys.platform != "darwin":
        return requested_bytes

    reader = virtual_size_reader or _read_darwin_virtual_size_bytes
    current_virtual_size = reader()
    if (
        isinstance(current_virtual_size, bool)
        or not isinstance(current_virtual_size, int)
        or current_virtual_size <= 0
    ):
        raise OSError(errno.EIO, "invalid Darwin virtual-size baseline")
    if requested_bytes > sys.maxsize - current_virtual_size:
        raise OSError(errno.EOVERFLOW, "Darwin RLIMIT_AS ceiling would overflow")

    ceiling = current_virtual_size + requested_bytes
    _soft_limit, hard_limit = resource_module.getrlimit(resource_module.RLIMIT_AS)
    if hard_limit != resource_module.RLIM_INFINITY:
        hard_limit = int(hard_limit)
        if hard_limit <= current_virtual_size:
            raise OSError(
                errno.ENOMEM,
                "inherited RLIMIT_AS leaves no address-space growth budget",
            )
        ceiling = min(ceiling, hard_limit)
    return ceiling
