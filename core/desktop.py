"""Windows desktop WorkerW injection for rendering behind desktop icons.

Uses the undocumented Windows technique of sending 0x052C to Progman
to spawn a WorkerW window, then parenting our window into it.
"""

import ctypes
import ctypes.wintypes as wintypes
import logging

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# Callback type for EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
)

_workerw_hwnd = None
_original_workerw = None


def _find_shehll_def_view():
    """Find the SHELLDLL_DefView window and its parent WorkerW."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        raise RuntimeError("Cannot find Progman window")

    # Tell Progman to spawn a WorkerW behind icons
    result = ctypes.c_ulong()
    user32.SendMessageTimeoutW(
        progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(result)
    )

    defview = user32.FindWindowW("SHELLDLL_DefView", None)
    if not defview:
        raise RuntimeError("Cannot find SHELLDLL_DefView")

    return defview


def _find_workerw_behind_defview():
    """Find the WorkerW window that is behind SHELLDLL_DefView."""
    defview = _find_shehll_def_view()

    # SHELLDLL_DefView's parent should be a WorkerW
    parent = user32.GetParent(defview)
    if parent:
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(parent, cls, 256)
        if cls.value == "WorkerW":
            return parent

    # Fallback: enumerate all WorkerW windows
    workerw_hwnd = None

    def enum_callback(hwnd, _lparam):
        nonlocal workerw_hwnd
        hwnd = ctypes.cast(hwnd, ctypes.POINTER(ctypes.c_int))
        h = ctypes.cast(hwnd, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if h is None:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        if cls.value == "WorkerW":
            child = user32.FindWindowExW(h, None, "SHELLDLL_DefView", None)
            if child:
                workerw_hwnd = h
        return True

    enum_func = WNDENUMPROC(enum_callback)
    user32.EnumWindows(enum_func, 0)

    if workerw_hwnd:
        return workerw_hwnd

    raise RuntimeError("Cannot find WorkerW behind desktop icons")


def inject_window(child_hwnd: int) -> bool:
    """Parent a window into the WorkerW layer (behind desktop icons)."""
    global _workerw_hwnd
    try:
        _workerw_hwnd = _find_workerw_behind_defview()

        # Get screen dimensions
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)

        # Reparent the child window
        user32.SetParent(child_hwnd, _workerw_hwnd)

        # Position to fill the screen
        user32.MoveWindow(child_hwnd, 0, 0, screen_w, screen_h, True)

        logger.info(f"Injected window {child_hwnd} into WorkerW {_workerw_hwnd}")
        return True
    except Exception as e:
        logger.error(f"Failed to inject window: {e}")
        return False


def restore_desktop():
    """Restore desktop to normal (remove injected window)."""
    global _workerw_hwnd
    if _workerw_hwnd:
        # The children will be destroyed with the process
        _workerw_hwnd = None
        logger.info("Desktop restored")


def get_screen_size() -> tuple:
    """Return (width, height) of the primary monitor."""
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
