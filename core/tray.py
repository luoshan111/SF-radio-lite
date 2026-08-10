"""System tray integration for BIZHI wallpaper engine."""

import pystray
import time
from PIL import Image, ImageDraw
import logging
import threading

logger = logging.getLogger(__name__)


def _create_icon_image(size=64):
    """Create a simple icon for the system tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw a stylized 'B' shape
    margin = 8
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=12,
        fill=(99, 102, 241),
    )
    draw.text((size // 2 - 8, size // 2 - 14), "B", fill="white")
    return img


class TrayManager:
    """Manages the system tray icon and menu."""

    def __init__(self, on_change_wallpaper=None, on_show_all=None,
                 on_settings=None, on_quit=None, music_api=None):
        self._on_change_wallpaper = on_change_wallpaper
        self._on_show_all = on_show_all
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._icon = None
        self._music_api = music_api
        self._lyric_thread = None
        self._lyric_running = False

    def _build_menu(self):
        """Build the tray context menu."""
        return pystray.Menu(
            pystray.MenuItem(
                "显示窗口",
                self._on_show_all if self._on_show_all else lambda: None,
            ),
            pystray.MenuItem(
                "更换壁纸",
                self._on_change_wallpaper if self._on_change_wallpaper else lambda: None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "退出",
                self._on_quit if self._on_quit else lambda: None,
            ),
        )

    def update_title(self, title: str):
        """Update the tray icon tooltip."""
        if self._icon:
            self._icon.title = title

    def _lyric_poll_loop(self):
        """Periodically update tray tooltip with current lyric line."""
        while self._lyric_running:
            try:
                if self._music_api and self._icon:
                    text = self._music_api.get_current_lyric_text()
                    if text:
                        self._icon.title = text
            except Exception as e:
                logger.debug(f"Lyric poll error: {e}")
            time.sleep(1.5)

    def start(self):
        """Start the system tray icon."""
        icon_image = _create_icon_image()
        self._icon = pystray.Icon(
            name="BIZHI",
            icon=icon_image,
            title="BIZHI - 动态壁纸",
            menu=self._build_menu(),
        )
        # Run in a separate thread so it doesn't block
        thread = threading.Thread(target=self._icon.run, daemon=True)
        thread.start()
        logger.info("System tray started")
        if self._music_api:
            self._lyric_running = True
            self._lyric_thread = threading.Thread(target=self._lyric_poll_loop, daemon=True)
            self._lyric_thread.start()

    def stop(self):
        """Stop the system tray icon."""
        self._lyric_running = False
        if self._icon:
            self._icon.stop()
            self._icon = None
