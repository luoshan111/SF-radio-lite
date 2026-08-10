"""Taskbar lyrics display -- embedded directly on the Windows taskbar."""

import ctypes
import ctypes.wintypes as wt
import tkinter as tk
import threading
import logging

logger = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x02

user32 = ctypes.windll.user32


def _set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

_set_dpi_aware()


def _find_taskbar():
    return user32.FindWindowW("Shell_TrayWnd", None)


class TaskbarLyrics:

    def __init__(self):
        self._root = None
        self._canvas = None
        self._ready = threading.Event()
        self._pending = None
        self._bg_color = "#d0d0d0"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update(self, title, lyric):
        if self._ready.is_set() and self._root:
            try:
                self._root.after(0, self._draw, title, lyric)
            except Exception:
                pass
        else:
            self._pending = (title, lyric)

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
            self._root.configure(bg=self._bg_color)

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

            lyric_w = min(400, w // 3)
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
            # 80% transparent = 20% opaque = alpha 51/255
            user32.SetLayeredWindowAttributes(hwnd, 0, 51, LWA_ALPHA)

            self._root.deiconify()
            self._root.update()

            vis = user32.IsWindowVisible(hwnd)
            logger.info("Lyrics HWND=%d visible=%d exstyle=0x%X", hwnd, vis, user32.GetWindowLongW(hwnd, GWL_EXSTYLE))

            self._draw_no_song()
            self._ready.set()

            if self._pending:
                self._draw(*self._pending)
                self._pending = None

            self._root.mainloop()
        except Exception as e:
            logger.error("TaskbarLyrics error: %s", e, exc_info=True)

    def _draw(self, title, lyric):
        if not self._canvas:
            return
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        c.delete("all")
        if title:
            c.create_text(8, h // 2 - 2, anchor="w", text=title,
                          font=("Microsoft YaHei UI", 9, "bold"), fill="#000000")
            if lyric:
                max_chars = max(8, (w - 130) // 13)
                display = lyric if len(lyric) <= max_chars else lyric[:max_chars - 1] + "..."
                c.create_text(130, h // 2 - 2, anchor="w", text=display,
                              font=("Microsoft YaHei UI", 10, "bold"), fill="#000000")
            else:
                c.create_text(130, h // 2 - 2, anchor="w", text="...",
                              font=("Microsoft YaHei UI", 10, "bold"), fill="#000000")
        else:
            self._draw_no_song()

    def _draw_no_song(self):
        if not self._canvas:
            return
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        c.delete("all")
        c.create_text(w // 2, h // 2, anchor="center", text="BIZHI",
                      font=("Microsoft YaHei UI", 10, "bold"), fill="#000000")