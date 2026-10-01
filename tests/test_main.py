"""Unit tests for main.py wiring (quit idempotency, widget subprocess, positions)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main


class TestQuitNow(unittest.TestCase):

    def setUp(self):
        main._quitting = False
        main._widget_proc = None

    def tearDown(self):
        main._quitting = False
        main._widget_proc = None

    def test_idempotent_without_widget(self):
        fake_tray = mock.Mock()
        fake_music, fake_tb = mock.Mock(), mock.Mock()
        with mock.patch.object(main.subprocess, "run") as run:
            main._quit_now(fake_tray, fake_tb, fake_music)
            main._quit_now(fake_tray, fake_tb, fake_music)  # no-op
        run.assert_not_called()               # no widget subprocess running
        fake_tray.stop.assert_called_once()
        fake_music.stop_background_sync.assert_called_once()
        fake_tb.destroy.assert_called_once()

    def test_quit_stops_running_widget_process(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        main._widget_proc = proc
        with mock.patch.object(main.subprocess, "run") as run:
            main._quit_now(None, None, None)
        run.assert_called_once()              # graceful WM_CLOSE attempted first
        run.call_args[0][0][:2] == ["taskkill", "/PID"]


class TestToggleWidget(unittest.TestCase):

    def tearDown(self):
        main._widget_proc = None

    def test_launches_when_not_running(self):
        main._widget_proc = None
        with mock.patch.object(main.subprocess, "Popen") as popen, \
             mock.patch.object(main, "_focus_widget") as focus:
            main._toggle_widget()
        popen.assert_called_once()
        focus.assert_not_called()

    def test_focuses_instead_of_double_launch(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        main._widget_proc = proc
        with mock.patch.object(main.subprocess, "Popen") as popen, \
             mock.patch.object(main, "_focus_widget") as focus:
            main._toggle_widget()
        popen.assert_not_called()
        focus.assert_called_once_with()


class TestSaveWindowPositions(unittest.TestCase):

    def setUp(self):
        self._old_config = main._config
        main._config = {"widgets": {}}

    def tearDown(self):
        main._config = self._old_config

    def test_keeps_fixed_startup_position(self):
        w_music = mock.Mock()
        w_music.x, w_music.y = 30, 40
        # _save_window_positions imports get_widget from widgets.manager
        import widgets.manager as mgr
        with mock.patch.object(mgr, "get_widget", return_value=w_music):
            main._save_window_positions()
        self.assertEqual(main._config["widgets"]["music"], main.FIXED_MUSIC_POSITION)


if __name__ == "__main__":
    unittest.main()
