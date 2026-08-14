"""Unit tests for the music widget API (sync dedup, offsets, error paths)."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from widgets.music import api as api_mod
from core import config as cfg


def _make_api():
    """Construct MusicApi without __init__ side effects (threads)."""
    api = api_mod.MusicApi.__new__(api_mod.MusicApi)
    api._current_lyrics = []
    api._current_title = ""
    api._current_artist = ""
    api._lrc_offset = 0
    api._user_offset = 0
    api._last_keyword = ""
    api._cached_lyric_text = ""
    api._last_js_sync = 0.0
    api._taskbar_lyrics = None
    return api


class TestAutoSyncDedup(unittest.TestCase):

    def test_background_poll_skips_when_frontend_fresh(self):
        calls = {"n": 0}

        def fake_status():
            calls["n"] += 1
            return {"title": "x", "artist": "y", "position_ms": 1000,
                    "duration_ms": 5000, "is_playing": True}

        api = _make_api()
        with mock.patch.object(api_mod, "_smtc_status", side_effect=fake_status):
            r = api.auto_sync(from_bg=False)  # frontend poll
            self.assertIsNotNone(r)
            n1 = calls["n"]
            self.assertIsNone(api.auto_sync(from_bg=True))  # bg immediately after
            self.assertEqual(calls["n"], n1)
            # after staleness the bg poll runs again
            api._last_js_sync = 0.0
            self.assertIsNotNone(api.auto_sync(from_bg=True))
            self.assertEqual(calls["n"], n1 + 1)

    def test_search_only_on_song_change(self):
        api = _make_api()
        with mock.patch.object(api_mod, "_smtc_status",
                               return_value={"title": "same", "artist": "a",
                                             "position_ms": 0, "duration_ms": 100,
                                             "is_playing": True}), \
             mock.patch.object(api_mod, "search_song", return_value=[]) as ss:
            api.auto_sync(from_bg=False)
            api.auto_sync(from_bg=False)
            ss.assert_called_once()  # keyword unchanged -> no second search


class TestUserOffset(unittest.TestCase):

    def test_set_persists(self):
        api = _make_api()
        with mock.patch.object(cfg, "load_config",
                               return_value={"music_offset_ms": 0}) as lc, \
             mock.patch.object(cfg, "save_config") as sc:
            r = api.set_user_offset(-300)
            self.assertEqual(r["user_offset"], -300)
            lc.assert_called_once()
            self.assertEqual(sc.call_args[0][0]["music_offset_ms"], -300)

    def test_initial_offset_from_constructor(self):
        api = api_mod.MusicApi.__new__(api_mod.MusicApi)
        api._user_offset = None  # placeholder
        api_mod.MusicApi.__init__(api, initial_offset_ms=777)
        self.assertEqual(api._user_offset, 777)


class TestErrorPaths(unittest.TestCase):

    def test_search_network_error(self):
        api = _make_api()
        with mock.patch.object(api_mod, "search_song", return_value=None):
            self.assertEqual(api.search("x")["error"], "搜索失败，请检查网络连接")

    def test_search_no_results(self):
        api = _make_api()
        with mock.patch.object(api_mod, "search_song", return_value=[]):
            self.assertEqual(api.search("x")["error"], "未找到相关歌曲")

    def test_search_empty_keyword(self):
        api = _make_api()
        self.assertIn("error", api.search("  "))

    def test_load_lyrics_missing(self):
        api = _make_api()
        with mock.patch.object(api_mod, "get_lyrics", return_value=None):
            self.assertIn("error", api.load_lyrics("mid"))

    def test_detect_song_network_error(self):
        api = _make_api()
        with mock.patch.object(api_mod, "detect_qq_music_song", return_value="kw"), \
             mock.patch.object(api_mod, "search_song", return_value=None):
            self.assertEqual(api.detect_song()["error"], "搜索失败，请检查网络连接")


if __name__ == "__main__":
    unittest.main()
