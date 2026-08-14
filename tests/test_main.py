"""Unit tests for main.py wiring (quit idempotency, wallpaper restore, tray flows)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from core import desktop


class TestQuitNow(unittest.TestCase):

    def test_idempotent(self):
        with mock.patch.object(main, "quit_all") as qa:
            fake_engine, fake_tray = mock.Mock(), mock.Mock()
            fake_music, fake_tb = mock.Mock(), mock.Mock()
            main._quitting = False
            main._quit_now(fake_engine, fake_tray, fake_tb, fake_music)
            main._quit_now(fake_engine, fake_tray, fake_tb, fake_music)  # no-op
            qa.assert_called_once()
            fake_engine.stop.assert_called_once()
            fake_tray.stop.assert_called_once()
            fake_music.stop_background_sync.assert_called_once()
            fake_tb.destroy.assert_called_once()
            main._quitting = False


class TestRestoreWallpaper(unittest.TestCase):

    def test_restore_types(self):
        engine = mock.Mock()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            gif = td / "a.gif"
            gif.write_bytes(b"GIF89a")
            main._restore_wallpaper(engine, {"wallpaper": {"type": "gif", "path": str(gif), "color": ""}})
            engine.set_gif.assert_called_once_with(str(gif))
            main._restore_wallpaper(engine, {"wallpaper": {"type": "solid", "path": "", "color": "#112233"}})
            engine.set_solid_color.assert_called_once_with("#112233")
            main._restore_wallpaper(engine, {"wallpaper": {"type": "image", "path": str(td / "missing.png"), "color": ""}})
            engine.set_gradient.assert_called_once()  # missing file -> gradient

    def test_no_wallpaper_config_uses_gradient(self):
        engine = mock.Mock()
        main._restore_wallpaper(engine, {"wallpaper": {"type": None, "path": "", "color": ""}})
        engine.set_gradient.assert_called_once()


class TestTrayWallpaperFlow(unittest.TestCase):

    def setUp(self):
        self._old_engine = main.wallpaper_engine
        self._old_queue = main._ui_queue
        self._old_config = main._config
        main._ui_queue = []
        main._config = None  # keep _apply_wallpaper from writing real config
        main.wallpaper_engine = mock.Mock()

    def tearDown(self):
        main.wallpaper_engine = self._old_engine
        main._ui_queue = self._old_queue
        main._config = self._old_config

    def test_gif_flow(self):
        with mock.patch.object(desktop, "pick_image_file", return_value=r"D:\x\wall.gif"):
            main._change_wallpaper()
            self.assertEqual(len(main._ui_queue), 1)
            main._pump_tk(None, None)
            self.assertEqual(main._ui_queue, [])
            main.wallpaper_engine.set_gif.assert_called_once_with(r"D:\x\wall.gif")

    def test_image_flow(self):
        with mock.patch.object(desktop, "pick_image_file", return_value=r"D:\x\wall.png"):
            main._change_wallpaper()
            main._pump_tk(None, None)
            main.wallpaper_engine.set_image.assert_called_once_with(r"D:\x\wall.png")

    def test_cancel_flow(self):
        with mock.patch.object(desktop, "pick_image_file", return_value=None):
            main._change_wallpaper()
            self.assertEqual(main._ui_queue, [])

    def test_no_engine_flow(self):
        main.wallpaper_engine = None
        with mock.patch.object(desktop, "pick_image_file") as pk:
            main._change_wallpaper()
            pk.assert_not_called()


class TestSaveWindowPositions(unittest.TestCase):

    def setUp(self):
        self._old_config = main._config
        main._config = {"widgets": {}}

    def tearDown(self):
        main._config = self._old_config

    def test_saves_positions(self):
        w_music = mock.Mock()
        w_music.x, w_music.y = 30, 40
        # _save_window_positions imports get_widget from widgets.manager
        import widgets.manager as mgr
        with mock.patch.object(mgr, "get_widget", return_value=w_music):
            main._save_window_positions()
        self.assertEqual(main._config["widgets"]["music"], {"x": 30, "y": 40})


if __name__ == "__main__":
    unittest.main()
