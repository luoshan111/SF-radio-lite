"""Unit tests for the QQ Music client (search / lyrics / LRC parsing)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from widgets.music import qq_music as q
from widgets.music.api import _merge_lyrics


class TestParseLrc(unittest.TestCase):

    def test_basic_lines_and_sorting(self):
        offset, lines = q.parse_lrc(
            "[00:05.00]Line two\n[00:01.00]Line one\n[00:03.50]Line mid"
        )
        self.assertEqual(offset, 0)
        self.assertEqual([l["time_ms"] for l in lines], [1000, 3500, 5000])
        self.assertEqual(lines[0]["text"], "Line one")

    def test_offset_tag(self):
        offset, lines = q.parse_lrc("[offset:+500]\n[00:01.00]Line one")
        self.assertEqual(offset, 500)
        self.assertEqual(len(lines), 1)

    def test_negative_offset(self):
        offset, _ = q.parse_lrc("[offset:-250]\n[00:01.00]Line")
        self.assertEqual(offset, -250)

    def test_colon_and_dot_milliseconds(self):
        # [mm:ss:xx] — xx is centiseconds, same as [mm:ss.xx]
        _, lines = q.parse_lrc("[00:01:23]Colon\n[00:01.45]Dot")
        self.assertEqual(lines[0]["time_ms"], 1230)
        self.assertEqual(lines[1]["time_ms"], 1450)

    def test_three_digit_ms(self):
        _, lines = q.parse_lrc("[00:01.123]Three")
        self.assertEqual(lines[0]["time_ms"], 1123)

    def test_empty_and_garbage(self):
        offset, lines = q.parse_lrc("")
        self.assertEqual((offset, lines), (0, []))
        _, lines = q.parse_lrc("not a lyric line\n[00:01.00]")
        self.assertEqual(lines, [])


class TestMergeLyrics(unittest.TestCase):

    def test_exact_match(self):
        original = [{"time_ms": 1000, "text": "Hello"}, {"time_ms": 5000, "text": "Bye"}]
        translation = [{"time_ms": 1000, "text": "你好"}, {"time_ms": 5000, "text": "再见"}]
        merged = _merge_lyrics(original, translation)
        self.assertEqual(merged[0]["trans"], "你好")
        self.assertEqual(merged[1]["trans"], "再见")

    def test_tolerance_match(self):
        original = [{"time_ms": 1000, "text": "Hello"}]
        translation = [{"time_ms": 1200, "text": "你好"}]  # within 500ms tolerance
        merged = _merge_lyrics(original, translation)
        self.assertEqual(merged[0]["trans"], "你好")

    def test_no_translation(self):
        original = [{"time_ms": 1000, "text": "Hello"}]
        merged = _merge_lyrics(original, [])
        self.assertEqual(merged[0]["trans"], "")


class TestSearchSong(unittest.TestCase):

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def test_success(self):
        payload = {"req_1": {"data": {"body": {"song": {"list": [{
            "mid": "M1", "name": "Song A",
            "singer": [{"name": "S1"}, {"name": "S2"}],
            "album": {"name": "Album"}, "interval": 210,
        }]}}}}}
        with mock.patch.object(q.requests, "post", return_value=self._FakeResp(payload)), \
             mock.patch.object(q.requests, "get",
                               side_effect=AssertionError("legacy path used first")):
            results = q.search_song("keyword")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["songmid"], "M1")
        self.assertEqual(results[0]["singer"], "S1/S2")
        self.assertEqual(results[0]["albummid"], "")
        self.assertEqual(results[0]["cover_url"], "")

    def test_album_cover_url(self):
        self.assertIn("T002R300x300M000ALBUM1", q.album_cover_url("ALBUM1", 300))

    def test_album_mid_maps_to_cover_url(self):
        payload = {"req_1": {"data": {"body": {"song": {"list": [{
            "mid": "M1", "name": "Song A", "singer": [],
            "album": {"mid": "A1", "name": "Album"},
        }]}}}}}
        with mock.patch.object(q.requests, "post", return_value=self._FakeResp(payload)):
            result = q.search_song("keyword")[0]
        self.assertEqual(result["albummid"], "A1")
        self.assertIn("T002R500x500M000A1", result["cover_url"])

    def test_legacy_soso_shape_still_parses(self):
        payload = {"data": {"song": {"list": [{
            "mid": "M1", "name": "Song A", "singer": [{"name": "S1"}],
            "album": {"mid": "A1", "name": "Album"},
        }]}}}
        with mock.patch.object(q.requests, "post",
                               side_effect=q.requests.ConnectionError("down")), \
             mock.patch.object(q.requests, "get", return_value=self._FakeResp(payload)):
            result = q.search_song("keyword")[0]
        self.assertEqual(result["songmid"], "M1")
        self.assertEqual(result["albummid"], "A1")

    def test_no_results_returns_empty_list(self):
        empty = {"req_1": {"data": {"body": {"song": {"list": []}}}}}
        legacy_empty = {"data": {"song": {"list": []}}}
        with mock.patch.object(q.requests, "post", return_value=self._FakeResp(empty)), \
             mock.patch.object(q.requests, "get", return_value=self._FakeResp(legacy_empty)):
            self.assertEqual(q.search_song("nothing"), [])

    def test_network_error_returns_none(self):
        with mock.patch.object(q.requests, "post",
                               side_effect=q.requests.ConnectionError("down")), \
             mock.patch.object(q.requests, "get",
                               side_effect=q.requests.ConnectionError("down")):
            self.assertIsNone(q.search_song("keyword"))


class TestAccountPlaylists(unittest.TestCase):

    def test_get_client_uin_reads_ini(self):
        data = "[Account]\nUin=3564017664\nOther=x\n"
        with mock.patch.object(q, "CLIENT_CONFIG", "fake.ini"), \
             mock.patch("builtins.open", mock.mock_open(read_data=data)):
            self.assertEqual(q.get_client_uin(), "3564017664")

    def test_get_client_uin_missing_file(self):
        with mock.patch.object(q, "CLIENT_CONFIG", "missing.ini"), \
             mock.patch("builtins.open", side_effect=OSError):
            self.assertIsNone(q.get_client_uin())

    def test_get_user_playlists_parses(self):
        data = {"v_playlist": [{
            "tid": 9632451793, "dirId": 7, "dirName": "qq", "songNum": 404,
            "bigpicUrl": "http://y.gtimg.cn/x.jpg",
        }]}
        with mock.patch.object(q, "_musicu", return_value=data):
            playlists = q.get_user_playlists("3564017664")
        self.assertEqual(playlists[0]["tid"], 9632451793)
        self.assertEqual(playlists[0]["name"], "qq")
        self.assertTrue(playlists[0]["cover_url"].startswith("https://"))

    def test_get_playlist_songs_parses(self):
        data = {"hasmore": 1, "songlist": [{"songinfo": {
            "mid": "M1", "name": "Song", "interval": 200,
            "singer": [{"name": "A"}, {"name": "B"}],
            "album": {"mid": "AL", "name": "Album"},
        }}]}
        with mock.patch.object(q, "_musicu", return_value=data) as musicu:
            page = q.get_playlist_songs(9632451793, begin=30, num=30)
        self.assertEqual(page["has_more"], True)
        song = page["songs"][0]
        self.assertEqual(song["songmid"], "M1")
        self.assertEqual(song["singer"], "A/B")
        self.assertEqual(song["cover_url"], q.album_cover_url("AL"))
        musicu.assert_called_once_with(
            "music.srfDissInfo.DissInfo", "CgiGetDiss",
            {"disstid": 9632451793, "song_begin": 30, "song_num": 30,
             "tag": False, "userinfo": False, "orderlist": False})


class TestRunAsync(unittest.TestCase):

    def test_plain_coroutine(self):
        async def coro():
            return 42
        self.assertEqual(q._run_async(coro()), 42)


if __name__ == "__main__":
    unittest.main()
