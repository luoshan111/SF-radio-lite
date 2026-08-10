"""Widget manager: coordinates all desktop widgets (pywebview windows)."""

import webview
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_widgets = {}  # name -> window ref
_quitting = False


def create_widget(name: str, title: str, html_path: str, js_api=None,
                  width: int = 360, height: int = 500, x: int = 100, y: int = 100,
                  frameless: bool = True, transparent: bool = True,
                  on_top: bool = True, easy_drag: bool = True):
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

    # Register close handler: hide instead of closing to keep app alive
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


def start_widgets():
    """Start the webview event loop (blocks until all windows close).

    Must be called ONCE after all widgets are created. Runs in the
    calling thread (typically a dedicated thread).
    Note: when all windows are hidden (not destroyed), this loop stays alive.
    """
    logger.info("Starting webview event loop")
    webview.start(debug=False)
