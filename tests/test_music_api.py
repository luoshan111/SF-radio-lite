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
    api._current_album = ""
    api._current_cover_url = ""
    api._lrc_offset = 0
    api._user_offset = 0
    api._last_keyword = ""
    api._cached_lyric_text = ""
    api._last_js_sync = 0.0
    api._lyrics_missing_since = None
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
        with mock.patch.object(api_mod, "_smtc_status", side_effect=fake_status), \
             mock.patch.object(api_mod, "search_song", return_value=[]):
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


class TestAutostart(unittest.TestCase):

    def test_get_autostart_reads_registry_state(self):
        api = _make_api()
        with mock.patch.object(api_mod, "is_autostart_enabled", return_value=True):
            self.assertEqual(api.get_autostart(), {"enabled": True})

    def test_set_autostart_returns_effective_state(self):
        api = _make_api()
        with mock.patch.object(api_mod, "set_autostart", return_value=True) as setter, \
             mock.patch.object(api_mod, "is_autostart_enabled", return_value=True):
            result = api.set_autostart(True)
        setter.assert_called_once_with(True)
        self.assertEqual(result, {"ok": True, "enabled": True})

    def test_set_autostart_reports_failure_and_restores_state(self):
        api = _make_api()
        with mock.patch.object(api_mod, "set_autostart", return_value=False), \
             mock.patch.object(api_mod, "is_autostart_enabled", return_value=False):
            result = api.set_autostart(True)
        self.assertEqual(result, {"error": "开机自启动设置失败", "enabled": False})


class TestPlaybackControls(unittest.TestCase):

    def test_control_playback_passes_action(self):
        api = _make_api()
        with mock.patch.object(api_mod, "_control_playback", return_value=True) as control:
            result = api.control_playback("next")
        control.assert_called_once_with("next", None)
        self.assertEqual(result, {"ok": True, "action": "next"})

    def test_control_playback_rejects_unknown_action(self):
        api = _make_api()
        self.assertIn("error", api.control_playback("shuffle"))

    def test_control_playback_seek_requires_position(self):
        api = _make_api()
        self.assertIn("error", api.control_playback("seek"))

    def test_queue_click_waits_for_real_playback(self):
        api = _make_api()
        api._qq_queue = mock.Mock()
        api._qq_queue.play_item.return_value = {"ok": True, "title": "Song", "artist": "A / B"}
        with mock.patch.object(api_mod, "_smtc_status", side_effect=[
            {"title": "Other", "artist": "A/B", "is_playing": True},
            {"title": "Song", "artist": "A/B", "is_playing": False},
            {"title": "Song", "artist": "A/B", "is_playing": True},
        ]), mock.patch.object(api_mod, "_control_playback", return_value=True) as play, \
             mock.patch.object(api_mod.time, "sleep"):
            result = api.play_qq_playlist_item("item", "token")
        self.assertTrue(result["verified"])
        play.assert_called_once_with("play")

    def test_queue_click_reports_error_when_song_never_confirmed(self):
        api = _make_api()
        api._qq_queue = mock.Mock()
        api._qq_queue.play_item.return_value = {"ok": True, "title": "Song", "artist": "A/B"}
        with mock.patch.object(api_mod, "_smtc_status", return_value=None), \
             mock.patch.object(api_mod.time, "monotonic", side_effect=[0, 100]), \
             mock.patch.object(api_mod.time, "sleep"):
            result = api.play_qq_playlist_item("item", "token")
        self.assertIn("error", result)

    def test_song_change_clears_stale_lyrics_when_search_fails(self):
        api = _make_api()
        api._current_lyrics = [{"time_ms": 0, "text": "old song"}]
        api._current_cover_url = "old-cover"
        with mock.patch.object(api_mod, "_smtc_status", return_value={
            "title": "New", "artist": "Singer", "is_playing": True,
        }), mock.patch.object(api_mod, "search_song", return_value=None):
            result = api.auto_sync()
        self.assertEqual(result["lyrics"], [])
        self.assertEqual(result["media_title"], "New")
        self.assertEqual(result["cover_url"], "")

    def test_ui_motion_roundtrip_and_default(self):
        api = _make_api()
        with mock.patch.object(cfg, "load_config", return_value={}), \
             mock.patch.object(cfg, "save_config") as save:
            self.assertTrue(api.get_ui_motion()["enabled"])   # 无配置默认开
            api.set_ui_motion(False)
        self.assertEqual(save.call_args[0][0]["ui_motion"], False)

        with mock.patch.object(cfg, "load_config", return_value={"ui_motion": False}), \
             mock.patch.object(cfg, "save_config") as save:
            self.assertFalse(api.get_ui_motion()["enabled"])
            self.assertTrue(api.set_ui_motion(True)["ok"])
        self.assertEqual(save.call_args[0][0]["ui_motion"], True)

    def test_failed_search_is_retried_on_later_polls(self):
        api = _make_api()
        song = {"songmid": "M1", "songname": "New", "singer": "Singer",
                "albumname": "Album", "cover_url": "c", "albummid": "A"}
        responses = iter([None, None, [song]])
        with mock.patch.object(api_mod, "_smtc_status", return_value={
            "title": "New", "artist": "Singer", "is_playing": True,
            "position_ms": 1000, "duration_ms": 5000,
        }), mock.patch.object(api_mod, "search_song",
                              side_effect=lambda *a, **k: next(responses)), \
             mock.patch.object(api_mod.time, "monotonic", side_effect=[0, 100, 200, 300, 400, 500]), \
             mock.patch.object(api_mod, "get_lyrics", return_value={"lrc": "[00:01.00]hi", "trans": ""}):
            first = api.auto_sync()
            self.assertEqual(first["lyrics"], [])          # search failed
            second = api.auto_sync()                        # before retry window
            self.assertIsNone(second["lyrics"])            # omitted: song unchanged
            third = api.auto_sync()                         # window passed: retry loads
        self.assertEqual(len(third["lyrics"]), 1)
        self.assertTrue(third["song_changed"])
        self.assertEqual(third["title"], "New")

    def test_power_saving_persisted(self):
        api = _make_api()
        saved = {}
        with mock.patch.object(cfg, "load_config", return_value={}),              mock.patch.object(cfg, "save_config", side_effect=lambda c: saved.update(c)):
            result = api.set_power_saving(True)
            self.assertTrue(result["ok"])
            self.assertTrue(saved.get("power_saving"))
        with mock.patch.object(cfg, "load_config", return_value={"power_saving": True}):
            self.assertTrue(api.get_power_saving()["enabled"])

    def test_lyrics_only_sent_when_song_changed(self):
        api = _make_api()
        song = {"songmid": "M1", "songname": "New", "singer": "Singer",
                "albumname": "Album", "cover_url": "c", "albummid": "A"}
        with mock.patch.object(api_mod, "_smtc_status", return_value={
            "title": "New", "artist": "Singer", "is_playing": True,
            "position_ms": 1000, "duration_ms": 5000,
        }), mock.patch.object(api_mod, "search_song", return_value=[song]), \
             mock.patch.object(api_mod, "get_lyrics",
                               return_value={"lrc": "[00:01.00]hi", "trans": ""}):
            first = api.auto_sync()      # song change: full payload with lyrics
            self.assertEqual(len(first["lyrics"]), 1)
            second = api.auto_sync()     # same song: lyrics omitted
            self.assertIsNone(second["lyrics"])
            third = api.auto_sync(fresh=True)   # frontend boot: lyrics again
            self.assertEqual(len(third["lyrics"]), 1)


if __name__ == "__main__":
    unittest.main()
