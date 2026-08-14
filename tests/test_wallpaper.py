"""Unit tests for the wallpaper engine (GIF frames, cancel, injection flag)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import wallpaper as wp


class TestGifDurations(unittest.TestCase):

    def test_per_frame_durations(self):
        engine = wp.WallpaperEngine.__new__(wp.WallpaperEngine)
        engine._screen_w = engine._screen_h = 64
        engine._root = mock.Mock()
        engine._canvas = mock.Mock()
        engine._running = True
        engine._gif_frames = []
        engine._gif_durations = []
        engine._gif_index = 0
        engine._anim_after_id = None
        engine._photo_ref = None

        with tempfile.TemporaryDirectory() as td:
            gif_path = make_test_gif(td)
            with mock.patch("core.wallpaper.ImageTk.PhotoImage") as photo:
                engine.set_gif(str(gif_path))
        self.assertEqual(len(engine._gif_frames), 3)
        self.assertEqual(engine._gif_durations, [200, 400, 600])
        # first animation frame scheduled with its own delay
        engine._root.after.assert_called_once()
        delay = engine._root.after.call_args[0][0]
        self.assertEqual(delay, 200)


class TestStopGif(unittest.TestCase):

    def test_cancels_pending_after(self):
        engine = wp.WallpaperEngine.__new__(wp.WallpaperEngine)
        engine._root = mock.Mock()
        engine._anim_after_id = 12345
        engine._gif_frames = [mock.Mock()]
        engine._gif_durations = [100]
        engine._gif_index = 2
        engine._stop_gif()
        engine._root.after_cancel.assert_called_once_with(12345)
        self.assertIsNone(engine._anim_after_id)
        self.assertEqual(engine._gif_frames, [])
        self.assertEqual(engine._gif_durations, [])
        self.assertEqual(engine._gif_index, 0)

    def test_no_root_is_safe(self):
        engine = wp.WallpaperEngine.__new__(wp.WallpaperEngine)
        engine._root = None
        engine._anim_after_id = None
        engine._gif_frames = [1]
        engine._gif_durations = [2]
        engine._gif_index = 1
        engine._stop_gif()  # must not raise


class TestInjectFlag(unittest.TestCase):

    def _make_engine(self):
        engine = wp.WallpaperEngine.__new__(wp.WallpaperEngine)
        engine._root = mock.Mock()
        engine._screen_w = engine._screen_h = 100
        return engine

    def test_failure_hides_window(self):
        engine = self._make_engine()
        with mock.patch.object(wp, "inject_window", return_value=False):
            engine._do_inject()
        self.assertFalse(engine.injected)
        engine._root.withdraw.assert_called_once()

    def test_success_keeps_window(self):
        engine = self._make_engine()
        with mock.patch.object(wp, "inject_window", return_value=True):
            engine._do_inject()
        self.assertTrue(engine.injected)
        engine._root.withdraw.assert_not_called()


def make_test_gif(td):
    from PIL import Image, ImageDraw
    frames = []
    for color in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]:
        im = Image.new("RGB", (64, 64), color)
        ImageDraw.Draw(im)
        frames.append(im)
    path = Path(td) / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=[200, 400, 600], loop=0)
    return path


if __name__ == "__main__":
    unittest.main()
