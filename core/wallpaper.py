"""Wallpaper rendering engine using tkinter in the WorkerW layer.

Supports static images, animated GIFs, and solid color backgrounds.
"""

import tkinter as tk
import logging
import threading
import time
from pathlib import Path
from PIL import Image, ImageTk, ImageDraw, ImageFilter

from core.desktop import inject_window, get_screen_size

logger = logging.getLogger(__name__)


class WallpaperEngine:
    """Renders wallpaper content in a tkinter window placed behind desktop icons."""

    def __init__(self):
        self._root = None
        self._canvas = None
        self._running = False
        self._current_type = None
        self._gif_frames = []
        self._gif_index = 0
        self._photo_ref = None  # prevent GC
        self._screen_w, self._screen_h = get_screen_size()

    def start(self):
        """Create the wallpaper window and inject into desktop."""
        self._root = tk.Tk()
        self._root.title("BIZHI_Wallpaper")
        # Remove window decorations
        self._root.overrideredirect(True)
        self._root.geometry(f"{self._screen_w}x{self._screen_h}+0+0")
        self._root.configure(bg="black")

        self._canvas = tk.Canvas(
            self._root,
            width=self._screen_w,
            height=self._screen_h,
            highlightthickness=0,
            bg="black",
        )
        self._canvas.pack(fill="both", expand=True)

        # Defer injection to allow window to fully create
        self._root.after(200, self._do_inject)
        self._running = True
        logger.info("Wallpaper engine started")

    def _do_inject(self):
        """Inject the window into the WorkerW desktop layer."""
        self._root.update_idletasks()
        hwnd = self._root.winfo_id()
        success = inject_window(hwnd)
        if success:
            logger.info("Wallpaper window injected into desktop")
        else:
            logger.warning("Failed to inject wallpaper, showing as overlay")

    def set_image(self, image_path: str):
        """Display a static image as wallpaper."""
        self._stop_gif()
        self._current_type = "image"
        try:
            img = Image.open(image_path)
            img = img.resize((self._screen_w, self._screen_h), Image.Resampling.LANCZOS)
            self._photo_ref = ImageTk.PhotoImage(img)
            self._canvas.delete("all")
            self._canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)
            logger.info(f"Set wallpaper image: {image_path}")
        except Exception as e:
            logger.error(f"Failed to set image wallpaper: {e}")

    def set_gif(self, gif_path: str):
        """Display an animated GIF as wallpaper."""
        self._stop_gif()
        self._current_type = "gif"
        try:
            gif = Image.open(gif_path)
            self._gif_frames = []
            self._gif_index = 0

            try:
                while True:
                    frame = gif.copy()
                    frame = frame.resize(
                        (self._screen_w, self._screen_h), Image.Resampling.LANCZOS
                    )
                    self._gif_frames.append(ImageTk.PhotoImage(frame))
                    gif.seek(len(self._gif_frames))
            except EOFError:
                pass

            if self._gif_frames:
                self._animate_gif()
                logger.info(f"Set wallpaper GIF: {gif_path} ({len(self._gif_frames)} frames)")
        except Exception as e:
            logger.error(f"Failed to set GIF wallpaper: {e}")

    def _animate_gif(self):
        """Animate through GIF frames."""
        if not self._running or not self._gif_frames:
            return
        self._canvas.delete("all")
        self._photo_ref = self._gif_frames[self._gif_index]
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)
        self._gif_index = (self._gif_index + 1) % len(self._gif_frames)

        # Get frame duration from original GIF, default 100ms
        delay = 100
        self._root.after(delay, self._animate_gif)

    def set_gradient(self, color1: str = "#0f0c29", color2: str = "#302b63",
                     color3: str = "#24243e"):
        """Display a gradient background."""
        self._stop_gif()
        self._current_type = "gradient"

        img = Image.new("RGB", (self._screen_w, self._screen_h))
        draw = ImageDraw.Draw(img)

        r1, g1, b1 = int(color1[1:3], 16), int(color1[3:5], 16), int(color1[5:7], 16)
        r2, g2, b2 = int(color2[1:3], 16), int(color2[3:5], 16), int(color2[5:7], 16)
        r3, g3, b3 = int(color3[1:3], 16), int(color3[3:5], 16), int(color3[5:7], 16)

        for y in range(self._screen_h):
            ratio = y / self._screen_h
            if ratio < 0.5:
                t = ratio * 2
                r = int(r1 + (r2 - r1) * t)
                g = int(g1 + (g2 - g1) * t)
                b = int(b1 + (b2 - b1) * t)
            else:
                t = (ratio - 0.5) * 2
                r = int(r2 + (r3 - r2) * t)
                g = int(g2 + (g3 - g2) * t)
                b = int(b2 + (b3 - b2) * t)
            draw.line([(0, y), (self._screen_w, y)], fill=(r, g, b))

        self._photo_ref = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)

    def set_solid_color(self, color: str = "#1a1a2e"):
        """Display a solid color background."""
        self._stop_gif()
        self._current_type = "solid"
        self._canvas.configure(bg=color)
        self._canvas.delete("all")

    def _stop_gif(self):
        """Stop GIF animation."""
        self._gif_frames = []
        self._gif_index = 0

    def run(self):
        """Start the tkinter main loop."""
        if self._root:
            self._root.mainloop()

    def stop(self):
        """Stop the wallpaper engine."""
        self._running = False
        self._stop_gif()
        if self._root:
            self._root.destroy()
            self._root = None
