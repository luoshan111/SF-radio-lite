"""BIZHI - QQ音乐桌面歌词组件 for Windows 11.

Usage:
    python main.py
"""

import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.taskbar_lyrics import TaskbarLyrics
from core.tray import TrayManager
from core.config import load_config, save_config
from widgets.manager import create_widget, start_widgets, show_all_widgets, quit_all
from widgets.music.api import MusicApi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bizhi")

WIDGETS_DIR = ROOT / "widgets"
MUSIC_WIDGET_WIDTH = 380
MUSIC_WIDGET_HEIGHT = 720

_quitting = False  # idempotency guard for _quit_now

_config = None  # module-level config dict, loaded in main()


def main():
    global _config
    logger.info("BIZHI starting...")
    _config = load_config()
    config = _config

    taskbar_lyrics = TaskbarLyrics(config.get("taskbar_lyrics", {}))
    music_api = MusicApi(taskbar_lyrics=taskbar_lyrics,
                         initial_offset_ms=config.get("music_offset_ms", 0))

    wpos = config.get("widgets", {})
    music_pos = wpos.get("music", {})

    music_win = create_widget(
        name="music",
        title="QQ 音乐歌词",
        html_path=str(WIDGETS_DIR / "music" / "index.html"),
        js_api=music_api,
        width=MUSIC_WIDGET_WIDTH,
        height=MUSIC_WIDGET_HEIGHT,
        x=music_pos.get("x", 1480),
        y=music_pos.get("y", 100),
    )
    music_api._window = music_win

    def _show_all():
        show_all_widgets()
        logger.info("All widgets restored from tray")

    tray = TrayManager(
        on_show_all=_show_all,
        on_quit=lambda: _quit_now(tray, taskbar_lyrics, music_api),
        music_api=music_api,
    )
    tray.start()

    logger.info("All components ready. Entering main loop...")

    try:
        start_widgets()
    except KeyboardInterrupt:
        pass
    finally:
        _quit_now(tray, taskbar_lyrics, music_api)


def _save_window_positions():
    """Persist current widget positions before destroying windows."""
    if _config is None:
        return
    from widgets.manager import get_widget
    for name in ("music",):
        w = get_widget(name)
        if w is None:
            continue
        try:
            _config.setdefault("widgets", {}).setdefault(name, {})["x"] = int(w.x)
            _config.setdefault("widgets", {}).setdefault(name, {})["y"] = int(w.y)
        except Exception:
            pass  # pywebview may not expose live position on all backends


def _quit_now(tray, taskbar_lyrics=None, music_api=None):
    """Destroy all windows and let the process exit naturally.

    Idempotent: may be called from the tray thread AND from the main thread
    (finally block) — the second call is a no-op.
    """
    global _quitting
    if _quitting:
        logger.info("Quit already in progress, skipping")
        return
    _quitting = True

    logger.info("Shutting down BIZHI...")
    _save_window_positions()
    if _config is not None:
        save_config(_config)
    if music_api:
        music_api.stop_background_sync()
    if taskbar_lyrics:
        taskbar_lyrics.destroy()
    quit_all()
    if tray:
        tray.stop()
    # No os._exit() here: destroying all widgets makes webview.start()
    # return, then main() finishes and the process exits cleanly.


if __name__ == "__main__":
    main()
