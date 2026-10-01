"""Roadmap 1.17: performance & resource-footprint ceilings.

The ceilings below are the roadmap's numbers, pinned as test
constants in this one module: a breach fails like a test.

* Cold start (this module, normal suite): spawn the dashboard server
  exactly as users start it -- ``python -m dashboard.app --host
  127.0.0.1 --port <free>`` -- in a throwaway copy of the app tree,
  so the dev database is never touched, and poll to the first
  HTTP 200.
* Idle soak (tests/test_performance_soak.py, weekly CI only): same
  server held idle; CPU and RSS sampled from /proc on Linux.

Tray/shield footprints need a desktop and are deliberately NOT
measured headlessly; they are covered on real machines by the
nightly self-report (focuscore/memreport.py -> diagnostics bundle).
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ------------------------------------------------------- the ceilings ---
# Roadmap 1.17. Changing a number here is changing the product spec;
# do it on purpose, with the roadmap, never to make a red run pass.
IDLE_CPU_CEILING_PCT = 1.0            # idle CPU < 1 %
RSS_CEILING_BYTES = 150 * 1024 * 1024  # private RAM < 150 MB
COLD_START_CEILING_S = 5.0            # double-click -> interactive < 5 s

REPO_ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache",
              "smoke-shot", "backups"}
_SKIP_FILE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".pyc")


def _skip_names(_dirpath, names):
    return [name for name in names
            if name in _SKIP_DIRS or name.endswith(_SKIP_FILE_SUFFIXES)]


def copy_app_tree(dest):
    """A runnable copy of the repo the server subprocess can own:
    the dev database, caches and screenshots stay behind, so the
    dev ``focuscore.db`` remains byte-identical."""
    shutil.copytree(REPO_ROOT, dest, ignore=_skip_names)
    return dest


def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def spawn_server(app_root, port, log_path):
    """Start the dashboard exactly as a user starts it."""
    argv = [sys.executable, "-m", "dashboard.app",
            "--host", "127.0.0.1", "--port", str(port)]
    kwargs = {"cwd": str(app_root)}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log_path, "wb") as log:
        return subprocess.Popen(argv, stdout=log,
                                stderr=subprocess.STDOUT, **kwargs)


def _log_tail(log_path, lines=20):
    try:
        text = Path(log_path).read_text(encoding="utf-8",
                                         errors="replace")
    except OSError:
        return "(no server log captured)"
    return "\n".join(text.splitlines()[-lines:])


def wait_until_ready(proc, port, timeout_s, log_path):
    """Poll until the first HTTP 200; returns the monotonic time of
    that response. Fails with the server log tail attached."""
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"server exited early (code {proc.returncode}); "
                f"log tail:\n{_log_tail(log_path)}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return time.monotonic()
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    raise AssertionError(
        f"server did not answer HTTP 200 within {timeout_s:.0f} s; "
        f"log tail:\n{_log_tail(log_path)}")


def stop_server(proc):
    """Terminate the server (whole process group) and reap it."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=10)


def assert_port_free(port, timeout_s=10):
    """The with_server.py lesson: a terminated server must leave the
    port free -- a lingering listener means an orphan survived."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass  # someone is still listening
        except OSError:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"port {port} still occupied {timeout_s:.0f} s "
                "after server stop")
        time.sleep(0.2)


# ------------------------------------------------------------------ tests

def test_ceilings_are_the_roadmap_numbers():
    assert IDLE_CPU_CEILING_PCT == 1.0
    assert RSS_CEILING_BYTES == 150 * 1024 * 1024
    assert COLD_START_CEILING_S == 5.0


def test_cold_start_is_under_the_ceiling(tmp_path):
    app_root = tmp_path / "app"
    copy_app_tree(app_root)
    port = free_port()
    log_path = tmp_path / "server.log"
    started = time.monotonic()
    proc = spawn_server(app_root, port, log_path)
    try:
        ready = wait_until_ready(proc, port, timeout_s=30,
                                 log_path=log_path)
    finally:
        stop_server(proc)
    elapsed = ready - started
    assert_port_free(port)
    assert elapsed < COLD_START_CEILING_S, (
        f"cold start took {elapsed:.2f} s "
        f"(ceiling {COLD_START_CEILING_S:.0f} s)")
