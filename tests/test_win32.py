"""Phase 7: win32 layer tests.

On non-Windows every function must degrade to a safe default (no
crash, no exception). Windows-only behavior is verified on the PC;
here we pin the fail-silent contract plus the pure helpers.
"""

import sys

import pytest

from focuscore import win32


def test_is_windows_matches_platform():
    assert win32.is_windows() == (sys.platform == "win32")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="graceful-degradation checks are for non-Windows")
def test_graceful_degradation_off_windows():
    assert win32.get_foreground_info() is None
    assert win32.iter_monitors() == []
    assert win32.minimize_window(1234) is False
    assert win32.minimize_window(0) is False
    assert win32.window_still_foreground(1234) is False
    assert win32.is_fullscreen_window(1234) is False
    assert win32.install_foreground_hook(lambda hwnd: None) is None
    assert win32.install_keyboard_swallow() is None
    assert win32.pump_messages_once() == 0
    assert win32.mutex_held() is False
    # uninstallers must never raise, even with junk handles
    win32.uninstall_foreground_hook(None)
    win32.uninstall_foreground_hook(1234)
    win32.uninstall_keyboard_swallow(None)
    win32.release_singleton_mutex(None)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="graceful-degradation checks are for non-Windows")
def test_singleton_dummy_is_truthy_but_not_a_handle():
    mutex = win32.acquire_singleton_mutex()
    assert mutex  # run_shield treats truthy as "we hold it"
    win32.release_singleton_mutex(mutex)  # must not raise


def test_constants_sane():
    assert win32.SHIELD_MUTEX_NAME.startswith("FocusCore_Shield")
    assert win32.ELEVATED_UNKNOWN == "__ELEVATED_UNKNOWN__"
    assert win32.EVENT_SYSTEM_FOREGROUND == 0x0003
    assert win32.FALLBACK_POLL_SECONDS == 2
    assert win32.WH_KEYBOARD_LL == 13


def test_pump_messages_respects_stop_event():
    import threading
    stop = threading.Event()
    stop.set()
    # Returns quickly (does not block) when already stopped.
    win32.pump_messages(stop, timeout_ms=50)
