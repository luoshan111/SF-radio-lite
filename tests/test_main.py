"""Unit tests for main.py wiring (quit idempotency, window position persistence)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main


class TestQuitNow(unittest.TestCase):

    def test_idempotent(self):
        with mock.patch.object(main, "quit_all") as qa:
            fake_tray = mock.Mock()
            fake_music, fake_tb = mock.Mock(), mock.Mock()
            main._quitting = False
            main._quit_now(fake_tray, fake_tb, fake_music)
            main._quit_now(fake_tray, fake_tb, fake_music)  # no-op
            qa.assert_called_once()
            fake_tray.stop.assert_called_once()
            fake_music.stop_background_sync.assert_called_once()
            fake_tb.destroy.assert_called_once()
            main._quitting = False


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
