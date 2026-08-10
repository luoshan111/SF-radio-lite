"""BIZHI - Lightweight dynamic wallpaper & desktop widgets for Windows 11.

Usage:
    python main.py
    python main.py --wallpaper path/to/image.png
    python main.py --no-wallpaper
"""

import sys
import os
import logging
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODO_DIR = ROOT.parent / "todo"
if str(TODO_DIR) not in sys.path:
    sys.path.insert(0, str(TODO_DIR))

from core.wallpaper import WallpaperEngine
from core.taskbar_lyrics import TaskbarLyrics
from core.tray import TrayManager
from widgets.manager import create_widget, start_widgets, show_all_widgets, quit_all
from api import TodoApi
from widgets.music.api import MusicApi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bizhi")

WIDGETS_DIR = ROOT / "widgets"


def parse_args():
    p = argparse.ArgumentParser(description="BIZHI - Windows 11 Dynamic Wallpaper & Widgets")
    p.add_argument("--wallpaper", "-w", type=str, default=None, help="Path to wallpaper image or GIF")
    p.add_argument("--no-wallpaper", action="store_true", help="Skip wallpaper engine, widgets only")
    p.add_argument("--gradient", action="store_true", help="Use gradient wallpaper")
    p.add_argument("--color", type=str, default=None, help="Solid color wallpaper (hex)")
    return p.parse_args()


def main():
    args = parse_args()
    logger.info("BIZHI starting...")

    wallpaper_engine = None

    if not args.no_wallpaper:
        wallpaper_engine = WallpaperEngine()
        wallpaper_engine.start()

        if args.wallpaper:
            wp = Path(args.wallpaper)
            if wp.exists():
                if wp.suffix.lower() == ".gif":
                    wallpaper_engine.set_gif(str(wp))
                else:
                    wallpaper_engine.set_image(str(wp))
            else:
                logger.warning(f"Wallpaper not found: {wp}")
                wallpaper_engine.set_gradient()
        elif args.color:
            wallpaper_engine.set_solid_color(args.color)
        elif args.gradient:
            wallpaper_engine.set_gradient()
        else:
            wallpaper_engine.set_gradient()

    todo_api = TodoApi()
    taskbar_lyrics = TaskbarLyrics()
    music_api = MusicApi(taskbar_lyrics=taskbar_lyrics)

    todo_win = create_widget(
        name="todo",
        title="日程待办",
        html_path=str(TODO_DIR / "index.html"),
        js_api=todo_api,
        width=380,
        height=520,
        x=80,
        y=120,
    )
    todo_api._window = todo_win

    music_win = create_widget(
        name="music",
        title="QQ音乐歌词",
        html_path=str(WIDGETS_DIR / "music" / "index.html"),
        js_api=music_api,
        width=360,
        height=580,
        x=1480,
        y=100,
    )
    music_api._window = music_win

    def _show_all():
        show_all_widgets()
        logger.info("All widgets restored from tray")

    tray = TrayManager(
        on_change_wallpaper=lambda: logger.info("Change wallpaper requested"),
        on_show_all=_show_all,
        on_quit=lambda: _quit_now(wallpaper_engine, tray, taskbar_lyrics, music_api),
        music_api=music_api,
    )
    tray.start()

    logger.info("All components ready. Entering main loop...")

    try:
        start_widgets()
    except KeyboardInterrupt:
        pass
    finally:
        _quit_now(wallpaper_engine, tray, taskbar_lyrics, music_api)


def _quit_now(engine, tray, taskbar_lyrics=None, music_api=None):
    """Actually destroy all windows and exit the process."""
    logger.info("Shutting down BIZHI...")
    if music_api:
        music_api.stop_background_sync()
    if taskbar_lyrics:
        taskbar_lyrics.destroy()
    quit_all()
    if engine:
        engine.stop()
    if tray:
        tray.stop()
    os._exit(0)


if __name__ == "__main__":
    main()
