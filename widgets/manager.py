"""Widget manager: coordinates all desktop widgets (pywebview windows)."""

import webview
import logging
import threading
import ctypes
from pathlib import Path

logger = logging.getLogger(__name__)

_widgets = {}  # name -> window ref
_quitting = False

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icons" / "bizhi.ico"
_WINDOW_ICON_HANDLES = []


def _apply_windows_window_icon(window=None):
    """Set the native window icon handles used by the Windows taskbar."""
    if not window or not ICON_PATH.exists():
        return
    try:
        native = window.native
        if native is None:
            return

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        load_image = user32.LoadImageW
        load_image.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        load_image.restype = ctypes.c_void_p
        send_message = user32.SendMessageW
        send_message.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        send_message.restype = ctypes.c_ssize_t

        hwnd = ctypes.c_void_p(int(native.Handle.ToInt64()))
        flags = 0x10 | 0x40  # LR_LOADFROMFILE | LR_DEFAULTSIZE
        for size, icon_type in ((16, 0), (32, 1)):  # ICON_SMALL, ICON_BIG
            handle = load_image(None, str(ICON_PATH), 1, size, size, flags)
            if handle:
                _WINDOW_ICON_HANDLES.append(handle)
                send_message(hwnd, 0x80, ctypes.c_void_p(icon_type), handle)  # WM_SETICON
    except Exception:
        logger.warning("Unable to set native BIZHI taskbar icon", exc_info=True)


def create_widget(name: str, title: str, html_path: str, js_api=None,
                  width: int = 360, height: int = 500, x: int = 100, y: int = 100,
                  frameless: bool = True, transparent: bool = True,
                  on_top: bool = True, easy_drag: bool = True,
                  close_exits: bool = False):
    """Create and show a frameless widget window.

    Args:
        name: Unique widget identifier.
        title: Window title.
        html_path: Path to the HTML file.
        js_api: Python object exposed to JS via window.pywebview.api.
        width/height: Window dimensions.
        x/y: Screen position.
        frameless: Remove window chrome.
        transparent: Transparent background (WebView2 on Win11).
        on_top: Always on top.
        easy_drag: Allow dragging from HTML elements with -webkit-app-region: drag.
        close_exits: When True, closing the window really closes it (letting
            webview.start return so the process can exit). When False, the
            close is intercepted and the window hides instead — for widgets
            that live inside the long-running controller process.

    Returns:
        The webview window object.
    """
    uri = Path(html_path).resolve().as_uri()

    window = webview.create_window(
        title=title,
        url=uri,
        width=width,
        height=height,
        x=x,
        y=y,
        frameless=frameless,
        transparent=transparent,
        on_top=on_top,
        easy_drag=easy_drag,
        js_api=js_api,
    )

    _widgets[name] = window
    logger.info(f"Widget '{name}' created at ({x},{y}) size {width}x{height}")
    window.events.shown += _apply_windows_window_icon

    # In the shared-process mode a close must not end the webview loop: hide
    # instead. With close_exits the default close behavior is exactly wanted.
    if not close_exits:
        def _on_closing(w=window, n=name):
            if _quitting:
                return True  # Allow real close during quit
            logger.info(f"Widget '{n}' closing — hiding instead")
            try:
                w.hide()
            except Exception:
                pass
            return False  # Prevent actual close, keep webview loop alive

        window.events.closing += _on_closing
    return window


def get_widget(name: str):
    """Get a widget window by name."""
    return _widgets.get(name)


def show_widget(name: str):
    """Show a previously hidden widget."""
    w = _widgets.get(name)
    if w:
        try:
            w.show()
            logger.info(f"Widget '{name}' restored")
        except Exception:
            pass


def show_all_widgets():
    """Show all hidden widgets."""
    for name, w in _widgets.items():
        try:
            w.show()
        except Exception:
            pass


def hide_all_widgets():
    """Hide all visible widgets."""
    for name, w in _widgets.items():
        try:
            w.hide()
        except Exception:
            pass


def all_hidden() -> bool:
    """Check if all widget windows are hidden."""
    for w in _widgets.values():
        try:
            if w.visible:
                return False
        except Exception:
            pass
    return True


def quit_all():
    """Force-destroy all widget windows so webview.start() can return."""
    global _quitting
    _quitting = True
    for name in list(_widgets):
        w = _widgets.pop(name, None)
        if w:
            try:
                w.destroy()
            except Exception:
                pass
    logger.info("All widgets destroyed for quit")


def remove_widget(name: str):
    """Close and remove a widget."""
    w = _widgets.pop(name, None)
    if w:
        try:
            w.destroy()
        except Exception:
            pass
        logger.info(f"Widget '{name}' removed")


def start_widgets(func=None):
    """Start the webview event loop (blocks until all windows close).

    Must be called ONCE after all widgets are created. Runs in the
    calling thread (typically a dedicated thread).
    Note: when all windows are hidden (not destroyed), this loop stays alive.
    func, if given, is invoked on the UI thread once the loop is running.
    """
    logger.info("Starting webview event loop")
    icon = str(ICON_PATH) if ICON_PATH.exists() else None
    webview.start(debug=False, func=func, icon=icon)
