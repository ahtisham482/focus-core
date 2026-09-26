"""focuscore/win32.py - Windows-only OS primitives via ctypes (stdlib only).

Everything here is a thin, fail-silent wrapper around a Win32 API call.
On non-Windows platforms every function returns a safe default
(None / False / []) so the shield degrades to notifications-only.

Qwen audit (2026-09-27) remediation notes, implemented here:
- R1: foreground detection is EVENT-DRIVEN via SetWinEventHook
  (EVENT_SYSTEM_FOREGROUND); polling is only a 2 s safety net.
- R2: when OpenProcess fails with ERROR_ACCESS_DENIED the process
  name is "__ELEVATED_UNKNOWN__" -- the shield then fails OPEN.
- R3: single instance via a named kernel mutex (no stale file locks).
- R4: fullscreen-exclusive windows never get an overlay; overlays span
  all monitors; a WH_KEYBOARD_LL hook swallows Alt+Tab / Win key only
  during the hardcore lock window (Ctrl+Alt+Del is uninterpretable by
  design and always works).
"""

import os
import threading

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHIELD_MUTEX_NAME = "FocusCore_Shield_Singleton_Mutex_v1"
ELEVATED_UNKNOWN = "__ELEVATED_UNKNOWN__"

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0x00000000

WH_KEYBOARD_LL = 13
HC_ACTION = 0
VK_TAB = 0x09
VK_LWIN = 0x5B
VK_RWIN = 0x5C
LLKHF_ALTDOWN = 0x20

SW_MINIMIZE = 6
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
MONITOR_DEFAULTTONEAREST = 2

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

FALLBACK_POLL_SECONDS = 2

# Keep ctypes callbacks alive for the life of the process; a collected
# callback pointer is a crash.
_KEEPALIVE = []
_KEEPALIVE_LOCK = threading.Lock()


def _remember(obj):
    with _KEEPALIVE_LOCK:
        _KEEPALIVE.append(obj)
    return obj


def is_windows():
    """True only on real Windows."""
    return os.name == "nt"


# ---------------------------------------------------------------------------
# Lazy ctypes bindings (built once, only on Windows)
# ---------------------------------------------------------------------------

_BINDINGS = None
_BINDINGS_LOCK = threading.Lock()


def _bindings():
    """Module-level ctypes bindings; None on non-Windows."""
    global _BINDINGS
    if _BINDINGS is not None:
        return _BINDINGS
    with _BINDINGS_LOCK:
        if _BINDINGS is not None:
            return _BINDINGS
        if not is_windows():
            return None
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception:
            return None

        HWND = wintypes.HWND
        HANDLE = wintypes.HANDLE
        DWORD = wintypes.DWORD
        BOOL = wintypes.BOOL
        UINT = wintypes.UINT
        LPARAM = wintypes.LPARAM
        WPARAM = wintypes.WPARAM
        LRESULT = LPARAM

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", DWORD),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", DWORD)]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", DWORD),
                        ("scanCode", DWORD),
                        ("flags", DWORD),
                        ("time", DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", HWND),
                        ("message", UINT),
                        ("wParam", WPARAM),
                        ("lParam", LPARAM),
                        ("time", DWORD),
                        ("pt", wintypes.POINT)]

        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None, HANDLE, DWORD, HWND, wintypes.LONG, wintypes.LONG,
            DWORD, DWORD)
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            BOOL, HANDLE, HANDLE, ctypes.POINTER(RECT), LPARAM)
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM,
                                      LPARAM)

        # -- signatures (64-bit correctness matters: HWND/HANDLE are
        # -- pointer-sized, so restypes must be set explicitly) --
        user32.GetForegroundWindow.restype = HWND
        user32.GetWindowThreadProcessId.argtypes = [HWND,
                                                    ctypes.POINTER(DWORD)]
        user32.GetWindowThreadProcessId.restype = DWORD
        user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
        user32.ShowWindow.restype = BOOL
        user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = BOOL
        user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.MonitorFromWindow.argtypes = [HWND, DWORD]
        user32.MonitorFromWindow.restype = HANDLE
        user32.GetMonitorInfoW.argtypes = [HANDLE,
                                           ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = BOOL
        user32.EnumDisplayMonitors.argtypes = [HANDLE, ctypes.c_void_p,
                                               MONITORENUMPROC, LPARAM]
        user32.EnumDisplayMonitors.restype = BOOL
        user32.SetWinEventHook.argtypes = [DWORD, DWORD, HANDLE,
                                           WINEVENTPROC, DWORD, DWORD,
                                           DWORD]
        user32.SetWinEventHook.restype = HANDLE
        user32.UnhookWinEvent.argtypes = [HANDLE]
        user32.UnhookWinEvent.restype = BOOL
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                             HANDLE, DWORD]
        user32.SetWindowsHookExW.restype = HANDLE
        user32.UnhookWindowsHookEx.argtypes = [HANDLE]
        user32.UnhookWindowsHookEx.restype = BOOL
        user32.CallNextHookEx.argtypes = [HANDLE, ctypes.c_int, WPARAM,
                                          LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT,
                                       UINT]
        user32.GetMessageW.restype = BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT,
                                        UINT, UINT]
        user32.PeekMessageW.restype = BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, UINT]
        user32.SetWindowPos.restype = BOOL

        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL,
                                          wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = HANDLE
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
        kernel32.OpenProcess.restype = HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = HANDLE

        _BINDINGS = {
            "ctypes": ctypes, "wintypes": wintypes,
            "user32": user32, "kernel32": kernel32,
            "RECT": RECT, "MONITORINFO": MONITORINFO,
            "KBDLLHOOKSTRUCT": KBDLLHOOKSTRUCT, "MSG": MSG,
            "WINEVENTPROC": WINEVENTPROC,
            "MONITORENUMPROC": MONITORENUMPROC, "HOOKPROC": HOOKPROC,
            "HWND": HWND, "HANDLE": HANDLE, "DWORD": DWORD,
            "BOOL": BOOL, "LPARAM": LPARAM, "WPARAM": WPARAM,
        }
        return _BINDINGS


# ---------------------------------------------------------------------------
# Single instance: named kernel mutex (R3)
# ---------------------------------------------------------------------------

def acquire_singleton_mutex(name=SHIELD_MUTEX_NAME):
    """Take the shield singleton mutex.

    Returns the mutex handle, or None when another instance already
    holds it. On non-Windows returns a dummy truthy object (the daemon
    is Windows-only in practice; tests use the dummy).
    The kernel destroys the mutex when the owning process dies --
    a stale lock is impossible.
    """
    b = _bindings()
    if b is None:
        return object()
    try:
        kernel32 = b["kernel32"]
        ctypes = b["ctypes"]
        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            return None
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return None


def release_singleton_mutex(handle):
    """Release a mutex from acquire_singleton_mutex(); never raises."""
    try:
        b = _bindings()
        if b is not None and handle is not None \
                and not isinstance(handle, object):
            b["kernel32"].CloseHandle(handle)
    except Exception:
        pass


def mutex_held(name=SHIELD_MUTEX_NAME):
    """True when another live process holds the named mutex.

    Used for "daemon running?" status checks: we create the mutex
    without taking ownership; ERROR_ALREADY_EXISTS means someone else
    is alive. Our own handle is closed immediately. Never raises.
    """
    b = _bindings()
    if b is None:
        return False
    try:
        kernel32, ctypes = b["kernel32"], b["ctypes"]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return False
        try:
            return ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Foreground window identity (R2)
# ---------------------------------------------------------------------------

def _process_name(b, pid):
    """Lowercased exe basename for pid, or ELEVATED_UNKNOWN / ''."""
    kernel32, ctypes = b["kernel32"], b["ctypes"]
    try:
        from ctypes import byref
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
                # Elevated / protected process: we cannot identify it,
                # so the shield must fail OPEN (treat as protected).
                return ELEVATED_UNKNOWN
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = b["DWORD"](512)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def get_foreground_info():
    """Identify the current foreground window.

    Returns {"hwnd", "pid", "process_name"} or None. process_name may
    be ELEVATED_UNKNOWN (R2) -- callers must treat that as protected.
    Never raises; None means "unknown, do nothing".
    """
    b = _bindings()
    if b is None:
        return None
    try:
        from ctypes import byref
        user32 = b["user32"]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = b["DWORD"]()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        return {"hwnd": int(hwnd), "pid": int(pid.value),
                "process_name": _process_name(b, int(pid.value))}
    except Exception:
        return None


def window_still_foreground(hwnd):
    """True when hwnd is still the foreground window."""
    b = _bindings()
    if b is None or not hwnd:
        return False
    try:
        return int(b["user32"].GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def minimize_window(hwnd):
    """Minimize hwnd; refuses 0/None. Returns True on success.

    The protected-process double-check lives in shield.py; this is the
    dumb primitive. Never raises.
    """
    b = _bindings()
    if b is None or not hwnd:
        return False
    try:
        return bool(b["user32"].ShowWindow(int(hwnd), SW_MINIMIZE))
    except Exception:
        return False


def make_topmost(hwnd):
    """Pin hwnd above all other windows; never raises."""
    b = _bindings()
    if b is None or not hwnd:
        return False
    try:
        return bool(b["user32"].SetWindowPos(
            int(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fullscreen detection + monitor enumeration (R4)
# ---------------------------------------------------------------------------

def is_fullscreen_window(hwnd):
    """True when hwnd covers its monitor AND is topmost (exclusive).

    The overlay is suspended for such windows (no tearing of game /
    video rendering contexts); minimizing still applies. Never raises.
    """
    b = _bindings()
    if b is None or not hwnd:
        return False
    try:
        from ctypes import byref
        user32 = b["user32"]
        rect = b["RECT"]()
        if not user32.GetWindowRect(int(hwnd), byref(rect)):
            return False
        exstyle = user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE)
        if not (exstyle & WS_EX_TOPMOST):
            return False
        monitor = user32.MonitorFromWindow(int(hwnd),
                                           MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False
        mi = b["MONITORINFO"]()
        mi.cbSize = b["DWORD"](b["ctypes"].sizeof(mi))
        if not user32.GetMonitorInfoW(monitor, byref(mi)):
            return False
        rc = mi.rcMonitor
        return (rect.left <= rc.left and rect.top <= rc.top
                and rect.right >= rc.right and rect.bottom >= rc.bottom)
    except Exception:
        return False


def iter_monitors():
    """[(left, top, width, height)] for every display; [] on failure."""
    b = _bindings()
    if b is None:
        return []
    try:
        out = []

        def _cb(hmon, hdc, lprc, data):
            r = lprc.contents
            out.append((r.left, r.top, r.right - r.left,
                        r.bottom - r.top))
            return True

        cb = b["MONITORENUMPROC"](_cb)
        _remember(cb)  # keep alive past this call (hook-style safety)
        if not b["user32"].EnumDisplayMonitors(None, None, cb, 0):
            return []
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Foreground-change event hook (R1)
# ---------------------------------------------------------------------------

def install_foreground_hook(on_foreground):
    """Hook EVENT_SYSTEM_FOREGROUND; on_foreground(hwnd) per change.

    The callback MUST be fast (<1 ms): it only enqueues the hwnd.
    Returns the hook handle, or None on failure / non-Windows.
    The installing thread must pump messages (pump_messages).
    """
    b = _bindings()
    if b is None:
        return None
    try:
        def _proc(hhook, event, hwnd, id_object, id_child,
                  event_thread, event_time):
            try:
                if event == EVENT_SYSTEM_FOREGROUND and hwnd \
                        and id_object == OBJID_WINDOW:
                    on_foreground(int(hwnd))
            except Exception:
                pass

        proc = b["WINEVENTPROC"](_proc)
        _remember(proc)
        handle = b["user32"].SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
            proc, 0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        return int(handle) if handle else None
    except Exception:
        return None


def uninstall_foreground_hook(handle):
    """Remove a hook from install_foreground_hook(); never raises."""
    try:
        b = _bindings()
        if b is not None and handle:
            b["user32"].UnhookWinEvent(int(handle))
    except Exception:
        pass


def pump_messages_once():
    """Drain pending Win32 messages once; returns messages handled.

    The worker thread calls this every loop iteration so the
    foreground hook and keyboard hook stay alive without blocking.
    Never raises.
    """
    b = _bindings()
    if b is None:
        return 0
    try:
        from ctypes import byref
        user32 = b["user32"]
        msg = b["MSG"]()
        PM_REMOVE = 1
        n = 0
        while user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
            n += 1
        return n
    except Exception:
        return 0


def pump_messages(stop_event, timeout_ms=500):
    """Win32 message pump for the hook thread.

    Non-blocking drain via PeekMessageW(PM_REMOVE), then an
    interruptible sleep -- the loop exits promptly when stop_event is
    set and never blocks forever inside GetMessage. Never raises.
    """
    b = _bindings()
    if b is None:
        return
    try:
        from ctypes import byref
        user32 = b["user32"]
        msg = b["MSG"]()
        PM_REMOVE = 1
        while not stop_event.is_set():
            try:
                while user32.PeekMessageW(byref(msg), None, 0, 0,
                                          PM_REMOVE):
                    user32.TranslateMessage(byref(msg))
                    user32.DispatchMessageW(byref(msg))
            except Exception:
                pass
            stop_event.wait(timeout_ms / 1000.0)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Low-level keyboard swallow for the hardcore lock (R4)
# ---------------------------------------------------------------------------

def install_keyboard_swallow():
    """Swallow Alt+Tab and Win keys until uninstalled.

    Scoped to the hardcore LOCK_SECONDS window; installed with
    try/finally discipline by the caller. Returns the hook handle or
    None when unavailable -- the lock proceeds WITHOUT the swallow
    (fail-safe). Ctrl+Alt+Del cannot be intercepted (secure attention
    sequence) and always remains an exit. The installing thread must
    pump messages (it does: the shield worker thread).
    """
    b = _bindings()
    if b is None:
        return None
    try:
        from ctypes import cast, POINTER
        user32, kernel32 = b["user32"], b["kernel32"]

        def _proc(n_code, w_param, l_param):
            try:
                if n_code == HC_ACTION:
                    kbd = cast(l_param,
                               POINTER(b["KBDLLHOOKSTRUCT"])).contents
                    vk = kbd.vkCode
                    if vk in (VK_LWIN, VK_RWIN):
                        return 1  # swallow
                    if vk == VK_TAB and (kbd.flags & LLKHF_ALTDOWN):
                        return 1  # swallow Alt+Tab
            except Exception:
                pass
            return user32.CallNextHookEx(None, n_code, w_param,
                                         l_param)

        proc = b["HOOKPROC"](_proc)
        _remember(proc)
        hmod = kernel32.GetModuleHandleW(None)
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, hmod, 0)
        return int(handle) if handle else None
    except Exception:
        return None


def uninstall_keyboard_swallow(handle):
    """Remove the swallow hook; never raises."""
    try:
        b = _bindings()
        if b is not None and handle:
            b["user32"].UnhookWindowsHookEx(int(handle))
    except Exception:
        pass
