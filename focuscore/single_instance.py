"""Single-instance guard for Focus Core (Roadmap 1.11).

Only one Focus Core process may run per Windows session. The first
instance takes a named kernel mutex (``Local\\FocusCore.SingleInstance.v1``)
at startup and keeps it for the life of the process. A second instance
sees the mutex already exists, focuses the first instance's window
(best effort, in launcher.py), and exits without starting anything --
which also means two updaters can never race, because the second
instance never gets as far as the updater.

Notes on the mechanics:

- ``Local\\`` scopes the mutex to this logon session. The install is
  per-user, so per-session is exactly right; ``Global\\`` would need
  extra privileges and would wrongly block other users' sessions.
- Raw ``CreateMutexW`` reports ``ERROR_ALREADY_EXISTS`` even when the
  SAME process asks twice (it just hands back another handle to the
  same mutex). The module-level per-process hold below makes
  :func:`acquire` idempotent: re-entry by the same process returns the
  handle it already holds instead of falsely reporting a second
  instance. The launcher and the tray both call :func:`acquire` in
  the same process, so this matters.
- Non-Windows is dev-only: :func:`acquire` always succeeds there
  (there is no installed tray app to protect off Windows).
- The win32 decision logic goes through a small injectable backend so
  tests can exercise the already-exists / fresh / error branches on
  any platform. Real-mutex tests follow the authenticode pattern:
  skip off Windows.
"""

import logging
import sys

logger = logging.getLogger(__name__)

DEFAULT_MUTEX_NAME = "Local\\FocusCore.SingleInstance.v1"
ERROR_ALREADY_EXISTS = 183

_HELD = {}      # mutex name -> handle this process already holds
_BACKENDS = {}  # mutex name -> backend that created the handle


class _NonWindowsHandle:
    """Truthy stand-in handle for non-Windows (dev-only) acquires."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<non-windows mutex handle {self.name!r}>"


def _is_windows(platform):
    return (platform if platform is not None else sys.platform) == "win32"


def _default_win32_backend():
    """Build the real ctypes backend for CreateMutexW/CloseHandle."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    class _Win32Backend:
        def create_mutex(self, name):
            handle = kernel32.CreateMutexW(None, True, name)
            return handle, ctypes.get_last_error()

        def close_handle(self, handle):
            kernel32.CloseHandle(handle)

    return _Win32Backend()


def acquire(name=DEFAULT_MUTEX_NAME, backend=None, platform=None):
    """Take the single-instance mutex. Returns a held handle or None.

    None means another Focus Core instance already holds the mutex:
    the caller should focus that instance's window and exit quietly.
    A returned handle must be kept alive for the life of the process
    (this module keeps it in the per-process hold, so callers may
    simply drop their reference). Calling acquire() again in the same
    process returns the same handle, never a false "second instance".
    """
    if name in _HELD:
        return _HELD[name]
    if not _is_windows(platform):
        handle = _NonWindowsHandle(name)
        _HELD[name] = handle
        return handle
    if backend is None:
        try:
            backend = _default_win32_backend()
        except Exception:  # no mutex API: fail closed
            logger.exception("could not load the mutex API")
            return None
    try:
        handle, last_error = backend.create_mutex(name)
    except Exception:  # mutex failure must not crash launch
        logger.exception("CreateMutexW failed for %s", name)
        return None
    if not handle:
        logger.warning("CreateMutexW returned no handle for %s", name)
        return None
    if last_error == ERROR_ALREADY_EXISTS:
        try:
            backend.close_handle(handle)
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            logger.warning("closing the duplicate mutex handle failed")
        return None
    _HELD[name] = handle
    _BACKENDS[name] = backend
    return handle


def release(handle):
    """Release a handle returned by acquire(); never raises.

    Normal app code never calls this (the mutex is held for process
    life and the kernel reclaims it at exit). It exists so tests and
    tools can hand the mutex back deterministically.
    """
    if handle is None:
        return
    for name, held in list(_HELD.items()):
        if held is handle:
            del _HELD[name]
            backend = _BACKENDS.pop(name, None)
            if backend is not None:
                try:
                    backend.close_handle(handle)
                except Exception:  # noqa: BLE001 -- cleanup only
                    logger.warning("closing the mutex handle failed")
            return


def reset_for_tests():
    """Drop every per-process hold (closing win32 handles). Test hook."""
    for handle in list(_HELD.values()):
        release(handle)
    _HELD.clear()
    _BACKENDS.clear()
