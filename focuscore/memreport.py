"""Roadmap 1.17: the tray process reports its own memory footprint.

Once a night the tray records a snapshot of its own process (pid,
uptime, resident memory) into a small bounded JSON file in the data
dir; the 0.4 diagnostics bundle then surfaces the latest snapshot
plus the 24 h growth when two comparable snapshots exist (same
process, at least ~20 h apart).

Observational only: nothing here reads or writes an enforcement
path (Invariant I-1), no database table is touched, and every
failure is soft -- callers log and move on. Stdlib only: RSS comes
from /proc/self/status on Linux and psapi on Windows; process
uptime comes from /proc on Linux and GetProcessTimes on Windows.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "memory-snapshots.json"
MAX_SNAPSHOTS = 8
GROWTH_MIN_GAP_SECONDS = 20 * 3600  # "~24 h" with slack for sleep/wake

_FILETIME_EPOCH_OFFSET_S = 11644473600  # 1601-01-01 -> 1970-01-01


def _linux_start_epoch():
    uptime_s = float(Path("/proc/uptime").read_text().split()[0])
    stat = Path("/proc/self/stat").read_text()
    # comm (field 2) may contain spaces/parens; fields resume after
    # the last ')'. starttime is field 22 -> index 19 of the rest.
    fields = stat.rsplit(")", 1)[1].split()
    start_ticks = int(fields[19])
    ticks = os.sysconf("SC_CLK_TCK")
    return time.time() - (uptime_s - start_ticks / ticks)


def _windows_start_epoch():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    creation = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel_t = wintypes.FILETIME()
    user_t = wintypes.FILETIME()
    ok = kernel32.GetProcessTimes(
        kernel32.GetCurrentProcess(), ctypes.byref(creation),
        ctypes.byref(exited), ctypes.byref(kernel_t),
        ctypes.byref(user_t))
    if not ok:
        return None
    filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    return filetime / 10_000_000 - _FILETIME_EPOCH_OFFSET_S


def _process_start_epoch():
    try:
        if sys.platform == "win32":
            return _windows_start_epoch()
        if sys.platform.startswith("linux"):
            return _linux_start_epoch()
    except Exception:  # uptime is best-effort
        logger.debug("memory snapshot: process start time unavailable",
                     exc_info=True)
    return None


def _linux_rss_bytes():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return None


def _windows_rss_bytes():
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set", ctypes.c_size_t),
            ("working_set", ctypes.c_size_t),
            ("quota_peak_paged", ctypes.c_size_t),
            ("quota_paged", ctypes.c_size_t),
            ("quota_peak_nonpaged", ctypes.c_size_t),
            ("quota_nonpaged", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                    ctypes.byref(counters),
                                    counters.cb)
    return int(counters.working_set) if ok else None


def _rss_bytes():
    try:
        if sys.platform == "win32":
            return _windows_rss_bytes()
        if sys.platform.startswith("linux"):
            return _linux_rss_bytes()
    except Exception:  # memory size is best-effort
        logger.debug("memory snapshot: RSS unavailable", exc_info=True)
    return None


def snapshot_now():
    """One snapshot of this process, as a plain JSON-ready dict."""
    start = _process_start_epoch()
    now = time.time()
    return {
        "pid": os.getpid(),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(now)),
        "recorded_epoch": now,
        "uptime_s": (now - start) if start else None,
        "rss_bytes": _rss_bytes(),
    }


def snapshot_path(data_dir):
    return Path(data_dir) / SNAPSHOT_FILENAME


def load_snapshots(data_dir):
    """All recorded snapshots, oldest first. A missing or corrupt
    file reads as "nothing recorded yet" -- never an error."""
    try:
        data = json.loads(snapshot_path(data_dir).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [snap for snap in data if isinstance(snap, dict)]


def record_snapshot(data_dir, snapshot=None):
    """Append one snapshot, keeping only the newest MAX_SNAPSHOTS.
    Written via a temp file + rename so a crash mid-write cannot
    corrupt the file. Returns the snapshot recorded."""
    snap = snapshot if snapshot is not None else snapshot_now()
    snapshots = load_snapshots(data_dir)
    snapshots.append(snap)
    snapshots = snapshots[-MAX_SNAPSHOTS:]
    path = snapshot_path(data_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snapshots, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return snap


def summarize(snapshots):
    """Latest snapshot + 24 h growth when a comparable one exists:
    same pid, at least GROWTH_MIN_GAP_SECONDS older, RSS known on
    both ends. Growth is None until then."""
    summary = {"latest": None, "growth_bytes": None, "compared_to": None}
    if not snapshots:
        return summary
    latest = snapshots[-1]
    summary["latest"] = latest
    for snap in reversed(snapshots[:-1]):
        if snap.get("pid") != latest.get("pid"):
            continue
        gap = (latest.get("recorded_epoch") or 0) - (
            snap.get("recorded_epoch") or 0)
        if gap < GROWTH_MIN_GAP_SECONDS:
            continue
        if latest.get("rss_bytes") is None or snap.get("rss_bytes") is None:
            continue
        summary["growth_bytes"] = latest["rss_bytes"] - snap["rss_bytes"]
        summary["compared_to"] = snap
        break
    return summary
