"""Roadmap 1.17: weekly idle-footprint soak -- WEEKLY CI ONLY.

Holds the real dashboard server idle for ``FOCUSCORE_SOAK_SECONDS``
(3600 in the weekly CI job; override exists so the harness itself can
be smoke-proved in seconds) and fails like a test if the idle CPU or
RSS ceilings from tests/test_performance_footprint.py are breached.

Normal suite runs can never pick this up: the module carries the
``soak`` mark and pyproject's addopts deselects it (``-m "not
soak"``); the weekly CI job selects it explicitly with ``-m soak``.
Sampling reads /proc, so the harness is Linux-shaped by design --
other platforms skip honestly (roadmap 1.17, decision 8).
"""

import os
import sys
import time
from pathlib import Path

import pytest

from tests.test_performance_footprint import (
    IDLE_CPU_CEILING_PCT,
    RSS_CEILING_BYTES,
    assert_port_free,
    copy_app_tree,
    free_port,
    spawn_server,
    stop_server,
    wait_until_ready,
)

pytestmark = [
    pytest.mark.soak,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="soak harness samples /proc (Linux weekly CI only)"),
]

SOAK_SECONDS = int(os.environ.get("FOCUSCORE_SOAK_SECONDS", "3600"))


def _proc_cpu_seconds(pid):
    """utime+stime of the server process, in seconds."""
    stat = Path(f"/proc/{pid}/stat").read_text(
        encoding="utf-8", errors="replace")
    fields = stat.rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[11]) + int(fields[12])) / ticks


def _proc_rss_bytes(pid):
    for line in Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8", errors="replace").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise AssertionError(f"VmRSS missing for pid {pid}")


def test_idle_footprint_soak(tmp_path):
    app_root = tmp_path / "app"
    copy_app_tree(app_root)
    port = free_port()
    log_path = tmp_path / "server.log"
    proc = spawn_server(app_root, port, log_path)
    rss_max = 0
    cpu_pct = 0.0
    try:
        wait_until_ready(proc, port, timeout_s=60, log_path=log_path)
        interval = 5.0 if SOAK_SECONDS >= 30 else max(
            0.2, SOAK_SECONDS / 5)
        wall_start = time.monotonic()
        cpu_start = _proc_cpu_seconds(proc.pid)
        deadline = wall_start + SOAK_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
            assert proc.poll() is None, "server died during the soak"
            rss_max = max(rss_max, _proc_rss_bytes(proc.pid))
        elapsed = time.monotonic() - wall_start
        cpu_pct = (_proc_cpu_seconds(proc.pid) - cpu_start) / elapsed * 100
    finally:
        stop_server(proc)
    assert_port_free(port)
    assert rss_max < RSS_CEILING_BYTES, (
        f"idle RSS peaked at {rss_max / 1048576:.1f} MB "
        f"(ceiling {RSS_CEILING_BYTES / 1048576:.0f} MB)")
    assert cpu_pct < IDLE_CPU_CEILING_PCT, (
        f"idle CPU averaged {cpu_pct:.3f}% over {SOAK_SECONDS} s "
        f"(ceiling {IDLE_CPU_CEILING_PCT:.0f}%)")
