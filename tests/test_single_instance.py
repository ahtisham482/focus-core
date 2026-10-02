"""Roadmap 1.11: single-instance guard + automatic port fallback.

The mutex decision logic is exercised through an injected fake win32
backend so the already-exists / fresh / error branches run on any
platform; real-mutex tests skip off Windows (the authenticode
pattern). Port tests use real localhost sockets on an ephemeral port
block patched in as launcher.PORT -- no test ever binds the real
port 5000, and the repo dev DB is never touched (the only dashboard
touch is /healthz through a Flask test client, which reads no data).
"""

import http.server
import socket
import subprocess
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

from focuscore import launcher, single_instance, tray

REPO_ROOT = Path(__file__).resolve().parent.parent

needs_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="real named-mutex behavior is Windows-only")


@pytest.fixture(autouse=True)
def _reset_single_instance_and_port():
    yield
    single_instance.reset_for_tests()
    launcher._active_port = None


# ------------------------------------------------------------- helpers ---

class _FakeBackend:
    """Injectable stand-in for the CreateMutexW/CloseHandle pair."""

    def __init__(self, handle=111, error=0):
        self._handle = handle
        self._error = error
        self.created = []
        self.closed = []

    def create_mutex(self, name):
        self.created.append(name)
        return self._handle, self._error

    def close_handle(self, handle):
        self.closed.append(handle)


def _bindable(port):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _free_block(count=10):
    """First port of a run of `count` consecutive bindable ports."""
    for _ in range(200):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            base = sock.getsockname()[1]
        if base + count - 1 > 65535:
            continue
        if all(_bindable(p) for p in range(base, base + count)):
            return base
    raise RuntimeError("no free consecutive port block found")


def _use_port_block(monkeypatch):
    """Point launcher.PORT at an ephemeral block; returns its base."""
    base = _free_block(launcher.PORT_SCAN_COUNT)
    monkeypatch.setattr(launcher, "PORT", base)
    return base


@pytest.fixture()
def occupy():
    """Bind+listen a raw (non-HTTP) socket: a foreign occupant."""
    socks = []

    def _occupy(port):
        sock = socket.socket()
        sock.bind(("127.0.0.1", port))
        sock.listen(5)
        socks.append(sock)
        return sock

    yield _occupy
    for sock in socks:
        sock.close()


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    """Fake dashboard (or fake foreign app) for the /healthz probe."""

    def do_GET(self):  # BaseHTTPRequestHandler API name
        mode = self.server.probe_mode
        if mode == "slow":
            time.sleep(2)
        if self.path != "/healthz" or mode == "notfound":
            body = b"nope"
            self.send_response(404)
        elif mode == "wrong":
            body = b'{"app": "some-other-app", "status": "ok"}'
            self.send_response(200)
        elif mode == "text":
            body = b"hello, definitely not json"
            self.send_response(200)
        else:
            body = b'{"app": "focus-core", "status": "ok"}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def serve():
    """Start a fake HTTP responder; returns (server, actual_port)."""
    servers = []

    def _serve(port, mode="ok"):
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), _ProbeHandler)
        server.probe_mode = mode
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server, server.server_address[1]

    yield _serve
    for server in servers:
        server.shutdown()
        server.server_close()


class _FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _record_start_server(monkeypatch, proc=None):
    """Replace launcher.start_server with a recorder; returns calls."""
    calls = []

    def fake_start_server(port=launcher.PORT, _tray_spawned=True):
        calls.append(port)
        return proc if proc is not None else _FakeProc()

    monkeypatch.setattr(launcher, "start_server", fake_start_server)
    return calls


# ---------------------------------------------------------------- mutex ---

def test_mutex_name_is_per_session_not_global():
    assert single_instance.DEFAULT_MUTEX_NAME.startswith("Local\\")
    assert not single_instance.DEFAULT_MUTEX_NAME.startswith("Global\\")


def test_mutex_non_windows_always_acquired():
    handle = single_instance.acquire(platform="linux")
    assert handle
    # Same process asks again: the held handle comes back, not a
    # false "second instance".
    assert single_instance.acquire(platform="linux") is handle


def test_mutex_fresh_acquire_returns_handle_and_is_idempotent():
    backend = _FakeBackend(handle=4321, error=0)
    handle = single_instance.acquire(
        name="Local\\Test.Fresh", backend=backend, platform="win32")
    assert handle == 4321
    again = single_instance.acquire(
        name="Local\\Test.Fresh", backend=backend, platform="win32")
    assert again is handle
    assert backend.created == ["Local\\Test.Fresh"]  # asked Windows once


def test_mutex_already_exists_returns_none_and_closes():
    backend = _FakeBackend(
        handle=99, error=single_instance.ERROR_ALREADY_EXISTS)
    result = single_instance.acquire(
        name="Local\\Test.Taken", backend=backend, platform="win32")
    assert result is None
    assert backend.closed == [99]  # duplicate handle handed back
    # Nothing is held: a later acquire asks Windows again.
    single_instance.acquire(
        name="Local\\Test.Taken", backend=backend, platform="win32")
    assert len(backend.created) == 2


def test_mutex_create_failure_returns_none():
    backend = _FakeBackend(handle=0, error=0)
    assert single_instance.acquire(
        name="Local\\Test.Broken", backend=backend,
        platform="win32") is None


def test_mutex_release_clears_the_hold():
    backend = _FakeBackend(handle=7, error=0)
    handle = single_instance.acquire(
        name="Local\\Test.Release", backend=backend, platform="win32")
    single_instance.release(handle)
    assert backend.closed == [7]
    single_instance.acquire(
        name="Local\\Test.Release", backend=backend, platform="win32")
    assert len(backend.created) == 2


@needs_windows
def test_mutex_real_same_process_reentry_returns_same_handle():
    name = f"Local\\FocusCore.Test.{uuid.uuid4().hex}"
    first = single_instance.acquire(name=name)
    assert first is not None
    assert single_instance.acquire(name=name) is first


@needs_windows
def test_mutex_real_second_process_is_refused(tmp_path):
    name = f"Local\\FocusCore.Test.{uuid.uuid4().hex}"
    ready = tmp_path / "child-ready.txt"
    child_code = (
        "import sys, time\n"
        "from focuscore import single_instance\n"
        "handle = single_instance.acquire(name=sys.argv[1])\n"
        "assert handle is not None\n"
        "open(sys.argv[2], 'w').write('ready')\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, name, str(ready)],
        cwd=str(REPO_ROOT))
    try:
        deadline = time.time() + 30
        while not ready.exists():
            assert time.time() < deadline, "child never took the mutex"
            time.sleep(0.05)
        # The child holds it: this process must be refused.
        assert single_instance.acquire(name=name) is None
    finally:
        child.terminate()
        child.wait(timeout=10)
    # The kernel reclaims the mutex when the owner dies: free again.
    assert single_instance.acquire(name=name) is not None


# ---------------------------------------------------------- probe matrix ---

def test_probe_accepts_focus_core(serve):
    _, port = serve(0, "ok")
    assert launcher.is_focus_core(port) is True


def test_probe_rejects_wrong_app(serve):
    _, port = serve(0, "wrong")
    assert launcher.is_focus_core(port) is False


def test_probe_rejects_non_json_body(serve):
    _, port = serve(0, "text")
    assert launcher.is_focus_core(port) is False


def test_probe_rejects_404(serve):
    _, port = serve(0, "notfound")
    assert launcher.is_focus_core(port) is False


def test_probe_rejects_refused_connection():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert launcher.is_focus_core(port) is False


def test_probe_slow_server_is_bounded_by_timeout(serve):
    _, port = serve(0, "slow")
    started = time.monotonic()
    assert launcher.is_focus_core(port, timeout=0.25) is False
    assert time.monotonic() - started < 1.5


# --------------------------------------------------------- port fallback ---

def test_ensure_server_free_first_port_starts_there(monkeypatch):
    base = _use_port_block(monkeypatch)
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: True)
    starts = _record_start_server(monkeypatch)
    real_probe = launcher.is_focus_core

    def _probe(port, **kwargs):
        if starts:
            return True
        return real_probe(port, **kwargs)

    monkeypatch.setattr(launcher, "is_focus_core", _probe)
    proc, already = launcher.ensure_server()
    assert already is False
    assert proc is not None
    assert starts == [base]
    assert launcher.app_url() == f"http://127.0.0.1:{base}/"


def test_ensure_server_foreign_occupant_falls_back(monkeypatch, occupy):
    base = _use_port_block(monkeypatch)
    monkeypatch.setattr(launcher, "PROBE_TIMEOUT", 0.3)
    occupy(base)  # a foreign app squatting on the first candidate
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: True)
    starts = _record_start_server(monkeypatch)
    real_probe = launcher.is_focus_core

    def _probe(port, **kwargs):
        if starts:
            return True
        return real_probe(port, **kwargs)

    monkeypatch.setattr(launcher, "is_focus_core", _probe)
    _proc, already = launcher.ensure_server()
    assert already is False
    assert starts == [base + 1]


def test_ensure_server_attaches_to_running_focus_core(
        monkeypatch, occupy, serve):
    base = _use_port_block(monkeypatch)
    monkeypatch.setattr(launcher, "PROBE_TIMEOUT", 0.3)
    occupy(base)
    serve(base + 2, "ok")  # Focus Core already running on base+2
    starts = _record_start_server(monkeypatch)
    proc, already = launcher.ensure_server()
    assert proc is None
    assert already is True
    assert starts == []  # attach: never start a duplicate
    assert launcher.active_port() == base + 2
    assert launcher.app_url() == f"http://127.0.0.1:{base + 2}/"


def test_ensure_server_running_instance_beats_free_earlier_port(
        monkeypatch, serve):
    base = _use_port_block(monkeypatch)
    serve(base + 1, "ok")
    starts = _record_start_server(monkeypatch)
    proc, already = launcher.ensure_server()
    assert (proc, already) == (None, True)
    assert starts == []
    assert launcher.active_port() == base + 1


def test_ensure_server_all_foreign_raises_plain_english(
        monkeypatch, occupy):
    base = _use_port_block(monkeypatch)
    monkeypatch.setattr(launcher, "PROBE_TIMEOUT", 0.2)
    for port in range(base, base + launcher.PORT_SCAN_COUNT):
        occupy(port)
    starts = _record_start_server(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        launcher.ensure_server()
    message = str(excinfo.value)
    assert starts == []
    assert "other programs" in message
    assert "setup.bat" not in message  # not the old dead end


def test_start_server_passes_port_flag(monkeypatch):
    commands = []

    def fake_popen(args, **kwargs):
        commands.append(args)
        return _FakeProc()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    launcher.start_server(5051)
    assert commands == [
        [sys.executable, "-m", "dashboard.app", "--port", "5051"]]


def test_ensure_server_integration_with_popen_and_probe(monkeypatch):
    """Decision 7: no real dashboard start -- Popen and the probe are
    faked, the real selection logic runs, and the chosen port must
    land in the spawned command."""
    base = _use_port_block(monkeypatch)
    commands = []

    def fake_popen(args, **kwargs):
        commands.append(args)
        return _FakeProc()

    def _probe(port, **kwargs):
        # Scan phase (before spawn): not Focus Core. Post-start
        # identity re-probe: the spawned server is Focus Core.
        return bool(commands)

    monkeypatch.setattr(launcher, "is_focus_core", _probe)
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: True)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    proc, already = launcher.ensure_server()
    assert already is False
    assert proc is not None
    assert commands[0][-2:] == ["--port", str(base)]


def test_ensure_server_start_failure_keeps_the_old_message(monkeypatch):
    _use_port_block(monkeypatch)
    monkeypatch.setattr(
        launcher, "is_focus_core", lambda port, **kw: False)
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: False)
    proc = _FakeProc()
    _record_start_server(monkeypatch, proc=proc)
    with pytest.raises(RuntimeError) as excinfo:
        launcher.ensure_server()
    assert proc.terminated is True
    assert "The Focus Core server did not start. Please double-click "\
        "setup.bat again" in str(excinfo.value)


def test_ensure_server_post_start_probe_false_raises_and_terminates(
        monkeypatch):
    """MINOR-1 repair: foreign app wins the probe->bind race.

    The port answers TCP (wait_for_port succeeds) but the post-start
    identity probe says "not Focus Core" -- ensure_server must treat
    it as a hard start failure: terminate the spawned process and
    raise the preserved start-failure RuntimeError, not scan on.
    """
    base = _use_port_block(monkeypatch)
    monkeypatch.setattr(
        launcher, "is_focus_core", lambda port, **kw: False)
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: True)
    proc = _FakeProc()
    commands = []

    def fake_popen(args, **kwargs):
        commands.append(args)
        return proc

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError) as excinfo:
        launcher.ensure_server()
    assert proc.terminated is True
    assert commands[0][-2:] == ["--port", str(base)]
    assert "The Focus Core server did not start. Please double-click "\
        "setup.bat again" in str(excinfo.value)


def test_ensure_server_post_start_probe_true_succeeds(monkeypatch):
    """MINOR-1 repair: post-start identity confirms Focus Core."""
    base = _use_port_block(monkeypatch)
    commands = []
    proc = _FakeProc()

    def fake_popen(args, **kwargs):
        commands.append(args)
        return proc

    def _probe(port, **kwargs):
        # Scan: no Focus Core yet. After spawn: identity confirmed.
        return bool(commands)

    monkeypatch.setattr(launcher, "is_focus_core", _probe)
    monkeypatch.setattr(launcher, "wait_for_port", lambda **kw: True)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    result_proc, already = launcher.ensure_server()
    assert result_proc is proc
    assert already is False
    assert proc.terminated is False
    assert commands[0][-2:] == ["--port", str(base)]
    assert launcher.active_port() == base


# ------------------------------------------------------ focus the first ---

def test_focus_existing_window_foregrounds_found_window():
    calls = []
    fake_user32 = types.SimpleNamespace(
        FindWindowW=lambda hwnd_parent, title: 4242,
        ShowWindow=lambda hwnd, cmd: calls.append(("show", hwnd, cmd)),
        SetForegroundWindow=lambda hwnd: calls.append(("fg", hwnd)) or True,
    )
    assert launcher.focus_existing_window(_user32=fake_user32) is True
    assert ("fg", 4242) in calls


def test_focus_existing_window_no_window_is_quiet():
    fake_user32 = types.SimpleNamespace(
        FindWindowW=lambda hwnd_parent, title: 0,
        ShowWindow=lambda hwnd, cmd: True,
        SetForegroundWindow=lambda hwnd: True,
    )
    assert launcher.focus_existing_window(_user32=fake_user32) is False


def test_focus_existing_window_never_raises():
    def explode(*args):
        raise OSError("no user32 here")

    fake_user32 = types.SimpleNamespace(
        FindWindowW=explode, ShowWindow=explode,
        SetForegroundWindow=explode)
    assert launcher.focus_existing_window(_user32=fake_user32) is False


def test_focus_existing_window_off_windows_without_injection():
    if sys.platform == "win32":
        pytest.skip("the no-injection path is the real Windows API")
    assert launcher.focus_existing_window() is False


# ------------------------------------------------------------- main/tray ---

def test_app_url_defaults_to_5000_and_follows_the_active_port():
    assert launcher.app_url() == launcher.APP_URL
    assert launcher.app_url() == "http://127.0.0.1:5000/"
    assert launcher.active_port() == 5000
    launcher._set_active_port(5007)
    assert launcher.active_port() == 5007
    assert launcher.app_url() == "http://127.0.0.1:5007/"


def test_launcher_main_second_instance_starts_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(single_instance, "acquire", lambda **kw: None)
    monkeypatch.setattr(
        launcher, "focus_existing_window",
        lambda: calls.append("focus") or True)
    monkeypatch.setattr(
        launcher, "set_windows_app_identity",
        lambda: calls.append("identity"))
    monkeypatch.setattr(
        launcher, "ensure_server",
        lambda: calls.append("server"))
    assert launcher.main() is None
    assert calls == ["focus"]  # no identity, no server, nothing started


def test_launcher_main_first_instance_simple_mode(monkeypatch):
    order = []

    def fake_acquire(**kwargs):
        order.append("mutex")
        return object()

    monkeypatch.setattr(single_instance, "acquire", fake_acquire)
    from focuscore import logging_config, paths
    monkeypatch.setattr(logging_config, "setup_logging", lambda **kw: None)
    monkeypatch.setattr(paths, "ensure_data_dir", lambda: None)

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            order.append("update-thread")

    import threading
    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(tray, "available", lambda: False)
    monkeypatch.setattr(
        launcher, "ensure_server",
        lambda _tray_spawned=True: order.append(("server", _tray_spawned))
        or (None, True))
    monkeypatch.setattr(launcher, "maybe_backup", lambda: None)
    monkeypatch.setattr(
        launcher, "open_app_window",
        lambda: order.append("window") or "browser")
    assert launcher.main() is None
    assert order[0] == "mutex"  # the guard runs before everything
    assert ("server", False) in order  # simple mode: unflagged child owns 2.5
    assert "window" in order


def test_tray_run_second_instance_starts_nothing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(single_instance, "acquire", lambda **kw: None)
    monkeypatch.setattr(
        launcher, "focus_existing_window",
        lambda: calls.append("focus") or True)
    from focuscore import store as store_mod
    monkeypatch.setattr(
        store_mod, "init_db", lambda path=None: calls.append("init_db"))
    assert tray.run(db_path=str(tmp_path / "t.db")) is None
    assert calls == ["focus"]


def test_tray_run_first_instance_delegates_to_tray_app(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        single_instance, "acquire", lambda **kw: object())
    from focuscore import logging_config
    monkeypatch.setattr(logging_config, "setup_logging", lambda **kw: None)
    from focuscore import store as store_mod
    monkeypatch.setattr(store_mod, "init_db", lambda path=None: None)
    monkeypatch.setattr(tray, "available", lambda: True)
    ran = []

    class FakeTrayApp:
        def __init__(self, db_path=None):
            self.db_path = db_path

        def run(self):
            ran.append(self.db_path)

    monkeypatch.setattr(tray, "TrayApp", FakeTrayApp)
    tray.run(db_path=str(tmp_path / "t.db"))
    assert ran == [str(tmp_path / "t.db")]


def test_tray_app_run_second_instance_starts_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(single_instance, "acquire", lambda **kw: None)
    monkeypatch.setattr(
        launcher, "focus_existing_window",
        lambda: calls.append("focus") or True)
    app = tray.TrayApp(db_path=None)
    assert app.run() is None
    assert calls == ["focus"]
    assert app.server_proc is None


# ---------------------------------------------------------------- healthz ---

def test_healthz_exact_json_and_nothing_else():
    from dashboard.app import create_app

    client = create_app({"TESTING": True}).test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"app": "focus-core", "status": "ok"}
    assert set(response.get_json()) == {"app", "status"}
