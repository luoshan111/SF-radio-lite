"""Taskbar lyrics display -- embedded directly on the Windows taskbar."""

import ctypes
import ctypes.wintypes as wt
import copy
import tkinter as tk
import threading
import logging
from pathlib import Path

from core.desktop import set_dpi_aware

logger = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
LWA_COLORKEY = 0x01
LWA_ALPHA = 0x02

TASKBAR_THEME_DEFAULTS = {
    "enabled": True,
    "show_title": True,
    "preset": "clear",
    "font_size": 16,
    "font_weight": "bold",
    "text_color": "#f5f7fa",
    "title_color": "#f0b35a",
    "effect": "outline",
    "background_mode": "transparent",
    "background_color": "#171a20",
}

user32 = ctypes.windll.user32
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icons" / "bizhi.ico"

set_dpi_aware()


def _find_taskbar():
    return user32.FindWindowW("Shell_TrayWnd", None)


def _valid_hex(value, fallback):
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if len(value) != 7 or not value.startswith("#"):
        return fallback
    try:
        int(value[1:], 16)
    except ValueError:
        return fallback
    return value.lower()


def normalize_theme(theme=None):
    """Return a validated taskbar lyric theme dictionary."""
    result = copy.deepcopy(TASKBAR_THEME_DEFAULTS)
    if isinstance(theme, dict):
        result.update(theme)

    result["enabled"] = bool(result.get("enabled", True))
    result["show_title"] = bool(result.get("show_title", True))
    result["preset"] = str(result.get("preset", "custom"))[:24]
    try:
        result["font_size"] = max(11, min(24, int(result.get("font_size", 16))))
    except (TypeError, ValueError):
        result["font_size"] = TASKBAR_THEME_DEFAULTS["font_size"]
    if result.get("font_weight") not in {"normal", "bold"}:
        result["font_weight"] = TASKBAR_THEME_DEFAULTS["font_weight"]
    if result.get("effect") not in {"none", "shadow", "outline"}:
        result["effect"] = TASKBAR_THEME_DEFAULTS["effect"]
    if result.get("background_mode") not in {"transparent", "solid"}:
        result["background_mode"] = TASKBAR_THEME_DEFAULTS["background_mode"]

    for key in ("text_color", "title_color", "background_color"):
        result[key] = _valid_hex(result.get(key), TASKBAR_THEME_DEFAULTS[key])
    return result


def _hex_to_colorref(value):
    value = _valid_hex(value, "#010203")
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    return red | (green << 8) | (blue << 16)


class TaskbarLyrics:

    def __init__(self, theme=None):
        self._root = None
        self._canvas = None
        self._ready = threading.Event()
        self._pending = None
        self._last_content = ("", "")
        self._theme = normalize_theme(theme)
        self._bg_color = "#010203"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update(self, title, lyric):
        self._last_content = (title or "", lyric or "")
        if self._ready.is_set() and self._root:
            try:
                self._root.after(0, self._draw, title, lyric)
            except Exception:
                pass
        else:
            self._pending = (title, lyric)

    def get_theme(self):
        return copy.deepcopy(self._theme)

    def apply_theme(self, theme):
        self._theme = normalize_theme(theme)
        if self._ready.is_set() and self._root:
            try:
                self._root.after(0, self._apply_theme_ui)
            except Exception:
                pass
        return self.get_theme()

    def destroy(self):
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def _run(self):
        try:
            self._root = tk.Tk()
            self._root.withdraw()
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            if ICON_PATH.exists():
                try:
                    self._root.iconbitmap(str(ICON_PATH))
                except tk.TclError:
                    logger.warning("Unable to apply BIZHI icon to taskbar lyrics window")
            self._root.configure(bg=self._bg_color)
            try:
                self._root.wm_attributes("-transparentcolor", self._bg_color)
            except tk.TclError:
                logger.warning("Tk transparent color is unavailable; using layered color key")

            bar = wt.RECT()
            taskbar = _find_taskbar()
            if taskbar and user32.GetWindowRect(taskbar, ctypes.byref(bar)):
                w = bar.right - bar.left
                h = bar.bottom - bar.top
                logger.info("Taskbar rect: (%d,%d)-(%d,%d)  h=%d", bar.left, bar.top, bar.right, bar.bottom, h)
            else:
                sw = self._root.winfo_screenwidth()
                sh = self._root.winfo_screenheight()
                logger.warning("Taskbar not found, using screen fallback")
                bar.left, bar.top = 0, sh - 48
                bar.right, bar.bottom = sw, sh
                w, h = sw, 48

            lyric_w = min(520, w // 3)
            lyric_h = h - 4
            lx = bar.left + 80
            ly = bar.top + 2

            self._canvas = tk.Canvas(
                self._root, width=lyric_w, height=lyric_h,
                bg=self._bg_color, highlightthickness=0,
            )
            self._canvas.pack(fill="both", expand=True)

            self._root.geometry(str(lyric_w) + "x" + str(lyric_h) + "+" + str(lx) + "+" + str(ly))
            self._root.update_idletasks()

            hwnd = self._root.winfo_id()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
            )
            user32.SetLayeredWindowAttributes(
                hwnd,
                _hex_to_colorref(self._bg_color),
                255,
                LWA_COLORKEY | LWA_ALPHA,
            )

            if self._theme["enabled"]:
                self._root.deiconify()
            self._root.update()

            vis = user32.IsWindowVisible(hwnd)
            logger.info("Lyrics HWND=%d visible=%d exstyle=0x%X", hwnd, vis, user32.GetWindowLongW(hwnd, GWL_EXSTYLE))

            self._ready.set()
            self._apply_theme_ui()

            if self._pending:
                self._draw(*self._pending)
                self._pending = None

            self._root.mainloop()
        except Exception as e:
            logger.error("TaskbarLyrics error: %s", e, exc_info=True)

    def _apply_theme_ui(self):
        if not self._root:
            return
        if self._theme["enabled"]:
            self._root.deiconify()
            self._root.lift()
            self._draw(*self._last_content)
        else:
            if self._canvas:
                self._canvas.delete("all")
            self._root.withdraw()

    def _draw(self, title, lyric):
        if not self._canvas:
            return
        if not self._theme["enabled"]:
            return
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        c.delete("all")

        if self._theme["background_mode"] == "solid":
            c.create_rectangle(
                0, 2, max(1, w - 1), max(2, h - 2),
                fill=self._theme["background_color"], outline="",
            )

        if title:
            lyric_x = 8
            if self._theme["show_title"]:
                title_width = min(124, max(82, int(w * 0.28)))
                title_size = max(10, self._theme["font_size"] - 3)
                title_text = self._truncate(title, max(5, title_width // max(8, title_size)))
                self._create_text(
                    8, h // 2 - 1, title_text,
                    ("Microsoft YaHei UI", title_size, "bold"),
                    self._theme["title_color"],
                )
                lyric_x = title_width

            if lyric:
                available = max(80, w - lyric_x - 8)
                max_chars = max(5, int(available / max(9, self._theme["font_size"] * 0.92)))
                display = self._truncate(lyric, max_chars)
                self._create_text(
                    lyric_x, h // 2 - 1, display,
                    ("Microsoft YaHei UI", self._theme["font_size"], self._theme["font_weight"]),
                    self._theme["text_color"],
                )
            else:
                self._create_text(
                    lyric_x, h // 2 - 1, "...",
                    ("Microsoft YaHei UI", self._theme["font_size"], self._theme["font_weight"]),
                    self._theme["text_color"],
                )
        else:
            self._draw_no_song()

    def _create_text(self, x, y, text, font, fill, anchor="w"):
        effect = self._theme["effect"]
        if effect == "shadow":
            self._canvas.create_text(
                x + 2, y + 2, anchor=anchor, text=text,
                font=font, fill="#000000",
            )
        elif effect == "outline":
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
                self._canvas.create_text(
                    x + dx, y + dy, anchor=anchor, text=text,
                    font=font, fill="#000000",
                )
        self._canvas.create_text(x, y, anchor=anchor, text=text, font=font, fill=fill)

    @staticmethod
    def _truncate(text, max_chars):
        text = str(text or "")
        if len(text) <= max_chars:
            return text
        return text[:max(1, max_chars - 1)] + "..."

    def _draw_no_song(self):
        if not self._canvas:
            return
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        c.delete("all")
        if self._theme["background_mode"] == "solid":
            c.create_rectangle(
                0, 2, max(1, w - 1), max(2, h - 2),
                fill=self._theme["background_color"], outline="",
            )
        self._create_text(
            w // 2, h // 2, "BIZHI",
            ("Microsoft YaHei UI", max(11, self._theme["font_size"] - 2), "bold"),
            self._theme["title_color"], anchor="center",
        )
