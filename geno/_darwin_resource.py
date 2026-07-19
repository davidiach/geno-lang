"""Darwin-specific support for secure process resource limits."""

import ctypes
import errno
import os
import sys
from collections.abc import Callable
from typing import Any

_PROC_PIDTASKINFO = 4
_PROC_PIDREGIONINFO = 7
_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_DARWIN_PROBE_GUARD_HEADROOM_BYTES = 64 * 1024 * 1024


class _ProcTaskInfo(ctypes.Structure):
    """Public proc_taskinfo ABI from Darwin's proc_info.h."""

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


class _ProcRegionInfo(ctypes.Structure):
    """Public proc_regioninfo ABI from Darwin's proc_info.h."""

    _fields_ = [
        ("pri_protection", ctypes.c_uint32),
        ("pri_max_protection", ctypes.c_uint32),
        ("pri_inheritance", ctypes.c_uint32),
        ("pri_flags", ctypes.c_uint32),
        ("pri_offset", ctypes.c_uint64),
        ("pri_behavior", ctypes.c_uint32),
        ("pri_user_wired_count", ctypes.c_uint32),
        ("pri_user_tag", ctypes.c_uint32),
        ("pri_pages_resident", ctypes.c_uint32),
        ("pri_pages_shared_now_private", ctypes.c_uint32),
        ("pri_pages_swapped_out", ctypes.c_uint32),
        ("pri_pages_dirtied", ctypes.c_uint32),
        ("pri_ref_count", ctypes.c_uint32),
        ("pri_shadow_depth", ctypes.c_uint32),
        ("pri_share_mode", ctypes.c_uint32),
        ("pri_private_pages_resident", ctypes.c_uint32),
        ("pri_shared_pages_resident", ctypes.c_uint32),
        ("pri_obj_id", ctypes.c_uint32),
        ("pri_depth", ctypes.c_uint32),
        ("pri_address", ctypes.c_uint64),
        ("pri_size", ctypes.c_uint64),
    ]


def _proc_pidinfo() -> Any:
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
    return proc_pidinfo


def _read_darwin_adjusted_virtual_size_bytes() -> int:
    """Return XNU's userspace-adjusted VM size through the public proc ABI."""
    proc_pidinfo = _proc_pidinfo()
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

    adjusted_size = int(info.pti_virtual_size)
    if adjusted_size <= 0:
        raise OSError(errno.EIO, "proc_pidinfo returned an invalid virtual size")
    return adjusted_size


def _read_darwin_map_size_bytes() -> int:
    """Sum public top-level VM entries as a probe lower-bound hint."""
    proc_pidinfo = _proc_pidinfo()
    info = _ProcRegionInfo()
    expected_size = ctypes.sizeof(info)
    cursor = 0
    total = 0

    while True:
        ctypes.memset(ctypes.byref(info), 0, expected_size)
        ctypes.set_errno(0)
        written = proc_pidinfo(
            os.getpid(),
            _PROC_PIDREGIONINFO,
            cursor,
            ctypes.byref(info),
            expected_size,
        )
        error_number = ctypes.get_errno()
        if written <= 0:
            if total > 0 and error_number == errno.EINVAL:
                break
            raise OSError(
                error_number or errno.EIO,
                "proc_pidinfo failed while enumerating the Darwin VM map",
            )
        if written != expected_size:
            raise OSError(
                error_number or errno.EIO,
                "proc_pidinfo returned an incomplete PROC_PIDREGIONINFO record "
                f"({written} of {expected_size} bytes)",
            )

        region_address = int(info.pri_address)
        if info.pri_user_tag == _UINT32_MAX:
            if total <= 0 or region_address < cursor:
                raise OSError(
                    errno.EIO,
                    "proc_pidinfo returned an invalid footprint-only VM region",
                )
            break

        region_size = int(info.pri_size)
        if region_address < cursor or region_size <= 0:
            raise OSError(errno.EIO, "proc_pidinfo returned an invalid VM-map entry")
        if region_address > _UINT64_MAX - region_size:
            raise OSError(errno.EOVERFLOW, "Darwin VM-map address would overflow")
        if region_size > sys.maxsize - total:
            raise OSError(errno.EOVERFLOW, "Darwin VM-map size would overflow")

        end = region_address + region_size
        if end <= cursor:
            raise OSError(errno.EIO, "proc_pidinfo VM-map enumeration did not advance")
        total += region_size
        cursor = end

    if total <= 0:
        raise OSError(errno.EIO, "Darwin returned an empty VM map")
    return total


def _set_darwin_soft_limit(
    candidate: int,
    hard_limit: int,
    resource_module: Any,
) -> bool:
    """Try one trusted pre-input RLIMIT_AS soft limit without changing hard."""
    try:
        resource_module.setrlimit(
            resource_module.RLIMIT_AS,
            (candidate, hard_limit),
        )
    except ValueError:
        # CPython maps Darwin's EINVAL from vm_map_set_size_limit() to
        # ValueError when candidate is below the kernel's raw map->size.
        return False
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            return False
        raise
    return True


def _probe_darwin_raw_map_size_upper_bound(
    requested_bytes: int,
    resource_module: Any,
    *,
    adjusted_size_reader: Callable[[], int] | None = None,
    region_size_reader: Callable[[], int] | None = None,
) -> tuple[int, int]:
    """Find a tight accepted RLIMIT_AS floor before reading untrusted input.

    Darwin exposes only an adjusted VM size on some map modes, while
    vm_map_set_size_limit() compares against raw map->size. Keep the inherited
    hard limit unchanged, find an accepted soft ceiling, then binary-search the
    kernel's exact acceptance boundary. A trusted-only probe guard is replaced
    by the caller's exact soft budget before any untrusted input is read.
    """
    adjusted_reader = adjusted_size_reader or _read_darwin_adjusted_virtual_size_bytes
    region_reader = region_size_reader or _read_darwin_map_size_bytes
    hints = (adjusted_reader(), region_reader())
    for hint in hints:
        if isinstance(hint, bool) or not isinstance(hint, int) or hint <= 0:
            raise OSError(errno.EIO, "invalid Darwin VM-size probe hint")
    lower_hint = max(hints)

    _soft_limit, inherited_hard = resource_module.getrlimit(resource_module.RLIMIT_AS)
    hard_limit = int(inherited_hard)
    max_candidate = (
        sys.maxsize
        if inherited_hard == resource_module.RLIM_INFINITY
        else min(hard_limit, sys.maxsize)
    )
    if max_candidate <= 0 or lower_hint > max_candidate:
        raise OSError(errno.ENOMEM, "inherited RLIMIT_AS is below the VM-size hint")

    if max_candidate - lower_hint < _DARWIN_PROBE_GUARD_HEADROOM_BYTES:
        candidate = max_candidate
    else:
        candidate = lower_hint + _DARWIN_PROBE_GUARD_HEADROOM_BYTES
    failed_lower_bound = 0

    while not _set_darwin_soft_limit(candidate, hard_limit, resource_module):
        failed_lower_bound = candidate
        if candidate >= max_candidate:
            raise OSError(
                errno.ENOMEM,
                "inherited RLIMIT_AS leaves no accepted Darwin VM-map ceiling",
            )
        candidate = min(max_candidate, candidate * 2)

    guard = min(
        max_candidate,
        candidate + _DARWIN_PROBE_GUARD_HEADROOM_BYTES,
    )
    if not _set_darwin_soft_limit(guard, hard_limit, resource_module):
        raise OSError(errno.ENOMEM, "failed to install trusted Darwin probe guard")

    accepted_upper_bound = candidate
    while accepted_upper_bound - failed_lower_bound > 1:
        midpoint = failed_lower_bound + (
            (accepted_upper_bound - failed_lower_bound) // 2
        )
        if _set_darwin_soft_limit(midpoint, hard_limit, resource_module):
            accepted_upper_bound = midpoint
            if not _set_darwin_soft_limit(guard, hard_limit, resource_module):
                raise OSError(
                    errno.ENOMEM,
                    "Darwin VM map grew beyond its trusted probe guard",
                )
        else:
            failed_lower_bound = midpoint

    if requested_bytes > max_candidate - accepted_upper_bound:
        final_soft_limit = max_candidate
    else:
        final_soft_limit = accepted_upper_bound + requested_bytes
    if final_soft_limit <= accepted_upper_bound:
        raise OSError(
            errno.ENOMEM,
            "inherited RLIMIT_AS leaves no address-space growth budget",
        )
    if not _set_darwin_soft_limit(final_soft_limit, hard_limit, resource_module):
        raise OSError(errno.ENOMEM, "failed to install final Darwin soft limit")

    return accepted_upper_bound, hard_limit


def rlimit_as_ceiling(
    requested_bytes: int,
    resource_module: Any,
    *,
    map_size_reader: Callable[[], int] | None = None,
    adjusted_size_reader: Callable[[], int] | None = None,
    region_size_reader: Callable[[], int] | None = None,
) -> int:
    """Return a secure RLIMIT_AS ceiling for the current platform.

    XNU rejects an address-space limit below the process's existing raw VM map.
    On Darwin, treat the configured value as a growth budget above a pre-input
    runtime probe of the kernel's actual acceptance boundary. Any stricter
    inherited hard limit remains authoritative.
    """
    if (
        isinstance(requested_bytes, bool)
        or not isinstance(requested_bytes, int)
        or requested_bytes <= 0
    ):
        raise OSError(errno.EINVAL, "RLIMIT_AS budget must be a positive integer")
    if sys.platform != "darwin":
        return requested_bytes

    if map_size_reader is not None:
        current_map_size = map_size_reader()
        if (
            isinstance(current_map_size, bool)
            or not isinstance(current_map_size, int)
            or current_map_size <= 0
        ):
            raise OSError(errno.EIO, "invalid Darwin raw VM-map baseline")
        _soft_limit, inherited_hard = resource_module.getrlimit(
            resource_module.RLIMIT_AS
        )
        hard_limit = int(inherited_hard)
    else:
        current_map_size, hard_limit = _probe_darwin_raw_map_size_upper_bound(
            requested_bytes,
            resource_module,
            adjusted_size_reader=adjusted_size_reader,
            region_size_reader=region_size_reader,
        )

    if requested_bytes > sys.maxsize - current_map_size:
        raise OSError(errno.EOVERFLOW, "Darwin RLIMIT_AS ceiling would overflow")
    ceiling = current_map_size + requested_bytes
    if hard_limit != resource_module.RLIM_INFINITY:
        if hard_limit <= current_map_size:
            raise OSError(
                errno.ENOMEM,
                "inherited RLIMIT_AS leaves no address-space growth budget",
            )
        ceiling = min(ceiling, hard_limit)
    return ceiling
