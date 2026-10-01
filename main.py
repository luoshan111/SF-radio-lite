"""BIZHI - QQ音乐桌面歌词组件 for Windows 11.

Usage:
    python main.py            控制进程（常驻、轻量）：任务栏歌词 + 托盘
    python main.py --widget   歌词挂件窗口（WebView2）：按需由托盘拉起，
                              关闭窗口即退出进程并释放全部内存

The WebView2 renderer is the heavyweight part of this app (hundreds of MB),
so it runs as an on-demand subprocess instead of a permanent window. The
controller keeps the native taskbar lyrics and the tray alive; the tray's
"歌词挂件" entry launches the UI when wanted and closing it frees everything.
"""

import sys
import logging
import ctypes
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_config, save_config, _data_dir
from core.taskbar_lyrics import TaskbarLyrics
from core.tray import TrayManager
from widgets.music.api import MusicApi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bizhi")

WIDGETS_DIR = ROOT / "widgets"
MUSIC_WIDGET_WIDTH = 380
MUSIC_WIDGET_HEIGHT = 720
FIXED_MUSIC_POSITION = {"x": 1480, "y": 100}
WIDGET_HWND_FILE = _data_dir() / "widget_hwnd"

_quitting = False
_config = None
_widget_proc = None            # controller only: the on-demand widget subprocess
_quit_event = threading.Event()
_mutexes = {}                  # mutex name -> handle (per process)


def _acquire_single_instance(name):
    """Prevent duplicates of either process (controller / widget)."""
    if sys.platform != "win32":
        return True
    if name in _mutexes:
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if not handle:
        logger.error("Unable to create single-instance mutex %s", name)
        return False
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        logger.info("Another BIZHI instance (%s) is already running", name)
        return False
    _mutexes[name] = handle
    return True


def _set_windows_app_id():
    """Give the taskbar a stable application identity separate from pythonw.exe."""
    if sys.platform != "win32":
        return
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BIZHI.DesktopLyrics"
        )
        if result:
            logger.warning("Unable to set Windows taskbar application identity: HRESULT=%s", result)
    except Exception:
        logger.warning("Unable to set Windows taskbar application identity", exc_info=True)


def _safe_music_position(position):
    """Reject the sentinel coordinates used by hidden/off-screen windows."""
    default = dict(FIXED_MUSIC_POSITION)
    if not isinstance(position, dict):
        return default
    try:
        x, y = int(position.get("x", default["x"])), int(position.get("y", default["y"]))
    except (TypeError, ValueError):
        return default
    if x <= -10000 or y <= -10000:
        return default
    return {"x": x, "y": y}


def run_widget():
    """--widget: the native tkinter lyrics window as its own throwaway process.

    Closing the window ends this process and frees the renderer memory; the
    controller keeps taskbar lyrics and the tray alive independently.
    """
    global _config
    if not _acquire_single_instance("BIZHI.DesktopLyrics.Widget"):
        return
    _set_windows_app_id()
    logger.info("BIZHI widget process starting (Qt UI)...")
    _config = load_config()

    from widgets.music.qt_widget import run as run_qt_widget
    run_qt_widget(_config)
    logger.info("BIZHI widget process exited")


def run_widget_webui():
    """--webui: the previous WebView2 UI, kept as a fallback."""
    global _config
    if not _acquire_single_instance("BIZHI.DesktopLyrics.Widget"):
        return
    _set_windows_app_id()
    logger.info("BIZHI widget process starting...")
    _config = load_config()

    music_pos = _safe_music_position(_config.get("widgets", {}).get("music"))
    power_saving = bool(_config.get("power_saving", False))
    music_api = MusicApi(initial_offset_ms=_config.get("music_offset_ms", 0))

    # Deferred import: webview is only ever loaded inside the widget process.
    from widgets.manager import create_widget, start_widgets

    window = create_widget(
        name="music",
        title="QQ 音乐歌词",
        html_path=str(WIDGETS_DIR / "music" / "index.html"),
        js_api=music_api,
        width=MUSIC_WIDGET_WIDTH,
        height=MUSIC_WIDGET_HEIGHT,
        x=music_pos.get("x", 1480),
        y=music_pos.get("y", 100),
        transparent=not power_saving,
        close_exits=True,   # closing the window exits this process on purpose
    )
    music_api._window = window

    window.events.shown += _publish_hwnd_callback(window)
    _install_restore_repaint_hook(window)

    try:
        start_widgets()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            WIDGET_HWND_FILE.unlink()
        except OSError:
            pass
    logger.info("BIZHI widget process exited")


def _widget_running() -> bool:
    return _widget_proc is not None and _widget_proc.poll() is None


def _read_widget_hwnd():
    try:
        return int(WIDGET_HWND_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _focus_widget():
    """Bring a running widget window to the front (background-process safe)."""
    user32 = ctypes.windll.user32
    hwnd = _read_widget_hwnd()
    if not hwnd or not user32.IsWindow(hwnd):
        return
    if user32.GetForegroundWindow() == hwnd:
        return
    user32.keybd_event(0x12, 0, 0, 0)   # Alt tap lifts the foreground lock
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    if user32.GetForegroundWindow() != hwnd:
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(this_thread, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(this_thread, fg_thread, False)


def _toggle_widget():
    """Tray entry: launch the widget when closed, focus it when running."""
    global _widget_proc
    if _widget_running():
        _focus_widget()
        return
    _widget_proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--widget"],
        cwd=str(ROOT),
    )
    logger.info("Widget process launched (pid %s)", _widget_proc.pid)


def _stop_widget_process():
    """Close the widget window gracefully (WM_CLOSE), then force if needed.

    The venv python is a launcher shim: the window belongs to its child
    process, so the close target is resolved from the published hwnd.
    """
    global _widget_proc
    proc = _widget_proc
    if proc is None or proc.poll() is not None:
        return
    user32 = ctypes.windll.user32
    hwnd = _read_widget_hwnd()
    target_pid = None
    if hwnd and user32.IsWindow(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        target_pid = pid.value or None
    if target_pid:
        subprocess.run(["taskkill", "/PID", str(target_pid)],
                       capture_output=True, timeout=10)
    else:
        subprocess.run(["taskkill", "/PID", str(proc.pid)],
                       capture_output=True, timeout=10)
    # Wait for the window to disappear (the shim exits with its child).
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if not hwnd or not user32.IsWindow(hwnd):
            break
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
    _widget_proc = None
    logger.info("Widget process stopped")


def run_controller():
    """Default mode: taskbar lyrics + tray only; the UI widget is on demand."""
    global _config
    if not _acquire_single_instance("BIZHI.DesktopLyrics.SingleInstance"):
        return
    _set_windows_app_id()
    logger.info("BIZHI controller starting...")
    _config = load_config()

    taskbar_lyrics = TaskbarLyrics(_config.get("taskbar_lyrics", {}))
    music_api = MusicApi(taskbar_lyrics=taskbar_lyrics,
                         initial_offset_ms=_config.get("music_offset_ms", 0))

    tray = TrayManager(
        on_quit=lambda: _quit_now(tray, taskbar_lyrics, music_api),
        music_api=music_api,
        on_toggle_widget=_toggle_widget,
        widget_running=_widget_running,
    )
    tray.start()

    logger.info("Controller ready: taskbar lyrics + tray running; "
                "the lyrics widget launches on demand from the tray.")
    _quit_event.wait()
    logger.info("Controller exiting")


def _save_window_positions():
    """Keep the configured startup position stable across restarts."""
    if _config is None:
        return
    _config.setdefault("widgets", {})["music"] = dict(FIXED_MUSIC_POSITION)


def _quit_now(tray=None, taskbar_lyrics=None, music_api=None):
    """Shut the controller down (idempotent across threads)."""
    global _quitting
    if _quitting:
        logger.info("Quit already in progress, skipping")
        return
    _quitting = True

    logger.info("Shutting down BIZHI controller...")
    _save_window_positions()
    if _config is not None:
        save_config(_config)
    _stop_widget_process()
    if music_api:
        music_api.stop_background_sync()
    if taskbar_lyrics:
        taskbar_lyrics.destroy()
    if tray:
        tray.stop()
    _quit_event.set()


if __name__ == "__main__":
    if "--webui" in sys.argv:
        run_widget_webui()
    elif "--widget" in sys.argv:
        run_widget()
    else:
        run_controller()
