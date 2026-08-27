"""BIZHI - Lightweight dynamic wallpaper & desktop widgets for Windows 11.

Usage:
    python main.py
    python main.py --wallpaper path/to/image.png
    python main.py --no-wallpaper
"""

import sys
import logging
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.wallpaper import WallpaperEngine
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
_tk_pump = {"timer": None}  # WinForms Timer driving WallpaperEngine.tick()
_ui_queue = []  # callables executed on the UI thread by the pump timer

wallpaper_engine = None  # module-level so tray callbacks can reach it
_config = None  # module-level config dict, loaded in main()


def parse_args():
    p = argparse.ArgumentParser(description="BIZHI - Windows 11 Dynamic Wallpaper & Widgets")
    p.add_argument("--wallpaper", "-w", type=str, default=None, help="Path to wallpaper image or GIF")
    p.add_argument("--no-wallpaper", action="store_true", help="Skip wallpaper engine, widgets only")
    p.add_argument("--gradient", action="store_true", help="Use gradient wallpaper")
    p.add_argument("--color", type=str, default=None, help="Solid color wallpaper (hex)")
    return p.parse_args()


def main():
    global wallpaper_engine, _config
    args = parse_args()
    logger.info("BIZHI starting...")
    _config = load_config()
    config = _config

    if not args.no_wallpaper:
        wallpaper_engine = WallpaperEngine()
        wallpaper_engine.start()

        # CLI args take priority; otherwise restore the last wallpaper.
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
            _restore_wallpaper(wallpaper_engine, config)

        if not wallpaper_engine.injected:
            logger.warning("Wallpaper disabled this session (injection failed). "
                           "Widgets and tray still work; use --no-wallpaper to skip silently.")

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
        on_change_wallpaper=_change_wallpaper,
        on_show_all=_show_all,
        on_quit=lambda: _quit_now(wallpaper_engine, tray, taskbar_lyrics, music_api),
        music_api=music_api,
    )
    tray.start()

    logger.info("All components ready. Entering main loop...")

    try:
        start_widgets(func=_setup_ui_thread)
    except KeyboardInterrupt:
        pass
    finally:
        _quit_now(wallpaper_engine, tray, taskbar_lyrics, music_api)


def _setup_ui_thread():
    """Run on the UI thread once the webview loop is up.

    Creates a WinForms Timer that pumps tk events for the wallpaper engine.
    tk's own mainloop can never run (pywebview owns the main thread), so
    without this pump, tk after() callbacks — GIF animation etc. — never fire.
    """
    if wallpaper_engine is None:
        return
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Timer

        timer = Timer()
        timer.Interval = 30
        timer.Tick += _pump_tk
        timer.Start()
        _tk_pump["timer"] = timer
        logger.info("tk pump timer started")
    except Exception as e:
        logger.warning(f"tk pump timer unavailable: {e}")


def _restore_wallpaper(engine, config):
    """Restore the last wallpaper from config (CLI args take priority)."""
    wp = config.get("wallpaper") or {}
    wtype = wp.get("type")
    path = wp.get("path", "")
    color = wp.get("color", "")
    if wtype in ("image", "gif") and path and Path(path).exists():
        if wtype == "gif":
            engine.set_gif(path)
        else:
            engine.set_image(path)
        logger.info(f"Restored wallpaper from config: {path}")
    elif wtype == "solid" and color:
        engine.set_solid_color(color)
    else:
        engine.set_gradient()


def _change_wallpaper():
    """Tray menu action: pick an image/GIF and apply it as wallpaper.

    Runs on the tray thread; the actual tk callbacks are queued to the UI
    thread (tkinter is not thread-safe).
    """
    from core.desktop import pick_image_file
    if wallpaper_engine is None:
        logger.warning("Wallpaper engine disabled (--no-wallpaper), cannot change wallpaper")
        return
    path = pick_image_file()
    if not path:
        logger.info("Wallpaper selection cancelled")
        return
    _ui_queue.append(lambda: _apply_wallpaper(path))


def _apply_wallpaper(path):
    """Apply a wallpaper file. Must run on the UI thread."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".gif":
            wallpaper_engine.set_gif(str(p))
            wtype = "gif"
        else:
            wallpaper_engine.set_image(str(p))
            wtype = "image"
        # Remember for next launch.
        if _config is not None:
            _config["wallpaper"] = {"type": wtype, "path": str(p), "color": ""}
            save_config(_config)
        logger.info(f"Wallpaper changed via tray: {path}")
    except Exception as e:
        logger.error(f"Failed to apply wallpaper {path}: {e}")


def _pump_tk(sender, e):
    """UI-thread pump: run queued UI tasks, then pump tk events."""
    while _ui_queue:
        fn = _ui_queue.pop(0)
        try:
            fn()
        except Exception as ex:
            logger.error(f"UI task failed: {ex}")
    if wallpaper_engine:
        wallpaper_engine.tick()


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


def _quit_now(engine, tray, taskbar_lyrics=None, music_api=None):
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
    if engine:
        # tk destroy() marshals to the main thread, which is still pumping
        # tk events inside the webview loop — so stop the engine BEFORE
        # destroying the webview windows (which ends that loop).
        engine.stop()
    timer = _tk_pump.get("timer")
    if timer is not None:
        try:
            timer.Stop()
        except Exception:
            pass
        _tk_pump["timer"] = None
    quit_all()
    if tray:
        tray.stop()
    # No os._exit() here: destroying all widgets makes webview.start()
    # return, then main() finishes and the process exits cleanly.


if __name__ == "__main__":
    main()
