"""Library-page bridge tests: row parsing and click-snapshot safety."""
import unittest
from unittest import mock

from widgets.music.qq_playlists import QQPageBridge


def element(name, x, y, w, h):
    return [mock.Mock(Name=name, BoundingRectangle=mock.Mock(X=x, Y=y, Width=w, Height=h),
                      IsOffscreen=False, Current=None)]


def page_dump():
    """One page of the 喜欢 list: headers, rows (title/artist/时长), junk.

    Coordinates are real screen values with the client window at (117, 434).
    """
    return [
        ("喜欢", (309, 565, 96, 64)),               # page header
        ("喜欢·463", (117, 1016, 128, 78)),         # sidebar total
        ("歌名/歌手", (312, 828, 76, 23)),           # column header
        ("专辑", (1122, 828, 34, 23)),
        ("时长", (1473, 828, 34, 23)),
        ("鲜花", (389, 878, 40, 27)),
        ("回春丹乐队", (389, 910, 90, 24)),
        ("鲜花", (1113, 891, 52, 30)),              # album column
        ("时长：05:41", (1473, 894, 47, 24)),
        ("野草", (389, 965, 40, 27)),
        ("椿乐队", (389, 997, 54, 24)),
        ("时长：04:31", (1473, 981, 47, 24)),
        ("劥", (435, 1128, 32, 18)),                # badge glyph, no pair
        ("MV", (454, 879, 48, 24)),
    ]


WINDOW_RECT = (117, 434, 1574, 1035)  # maximized client at (117, 434)


def with_elements(dump):
    return [(n, r, mock.Mock(Current=None)) for n, r in dump]


class TestAnchorRows(unittest.TestCase):
    def setUp(self):
        self.bridge = QQPageBridge()

    def test_pairs_title_artist_by_duration_anchor(self):
        rows = self.bridge._anchor_rows(with_elements(page_dump()), WINDOW_RECT)
        self.assertEqual([(r["title"], r["artist"]) for r in rows],
                         [("鲜花", "回春丹乐队"), ("野草", "椿乐队")])

    def test_junk_and_badges_never_become_rows(self):
        rows = self.bridge._anchor_rows(with_elements(page_dump()), WINDOW_RECT)
        for row in rows:
            self.assertNotIn(row["title"], ("劥", "MV", "歌名/歌手", "时长：05:41"))
            self.assertNotEqual(row["artist"], "鲜花")

    def test_duration_without_pair_is_skipped(self):
        specs = with_elements([("时长：03:00", (1473, 500, 47, 24))])
        self.assertEqual(self.bridge._anchor_rows(specs, WINDOW_RECT), [])

    def test_header_fragment_never_becomes_artist(self):
        specs = with_elements([
            ("离心力", (389, 878, 60, 27)),
            ("歌名/歌手", (312, 910, 76, 23)),
            ("时长：05:01", (1473, 894, 47, 24)),
        ])
        self.assertEqual(self.bridge._anchor_rows(specs, WINDOW_RECT), [])

    def test_columns_follow_window_origin(self):
        shifted = [(n, (x + 500, y, w, h)) for n, (x, y, w, h) in page_dump()]
        rows = self.bridge._anchor_rows(with_elements(shifted), (617, 434, 1574, 1035))
        self.assertEqual([r["title"] for r in rows], ["鲜花", "野草"])


class TestPageSnapshots(unittest.TestCase):
    def bridge(self):
        bridge = QQPageBridge()
        bridge._initialize = mock.Mock()
        bridge._user32 = mock.Mock()
        bridge._user32.GetForegroundWindow.return_value = 1
        bridge._content_rows = mock.Mock(
            return_value=(with_elements(page_dump()), 99, WINDOW_RECT))
        bridge._click = mock.Mock()
        bridge._wheel_page = mock.Mock()
        return bridge

    def test_refresh_returns_rows_and_total(self):
        bridge = self.bridge()
        data = bridge.read_page()
        self.assertEqual([i["title"] for i in data["items"]], ["鲜花", "野草"])
        self.assertEqual(data["total"], 463)
        self.assertEqual(data["page"], "喜欢")
        self.assertTrue(data["at_start"])

    def test_stale_page_cannot_play_after_refresh(self):
        bridge = self.bridge()
        old = bridge.read_page()
        bridge.read_page()
        result = bridge.play_item(old["items"][0]["id"], old["page_token"])
        self.assertIn("error", result)
        bridge._click.assert_not_called()

    def test_play_uses_newly_resolved_element(self):
        bridge = self.bridge()
        page = bridge.read_page()
        fresh = with_elements(page_dump())
        bridge._content_rows.return_value = (fresh, 99, WINDOW_RECT)
        flower = next(el for n, r, el in fresh if (n, r[:2]) == ("鲜花", (389, 878)))
        self.assertTrue(bridge.play_item(page["items"][0]["id"], page["page_token"])["ok"])
        bridge._click.assert_called_once()
        self.assertIs(bridge._click.call_args[0][0], flower)
        self.assertEqual(bridge._click.call_args[0][1], 99)

    def test_unreadable_page_reports_error(self):
        bridge = self.bridge()
        bridge._content_rows.return_value = ([
            ("Yonder·Journal", (261, 469, 151, 28), mock.Mock(Current=None)),
            ("后退", (299, 471, 51, 41), mock.Mock(Current=None)),
        ], 99, WINDOW_RECT)
        self.assertIn("error", bridge.read_page())
        self.assertFalse(bridge.status()["ok"])

    def test_restart_client_relaunches_and_confirms(self):
        bridge = self.bridge()
        with mock.patch("widgets.music.qq_playlists.subprocess.run"), \
             mock.patch("widgets.music.qq_playlists.subprocess.Popen"), \
             mock.patch("widgets.music.qq_playlists.time.sleep"):
            result = bridge.restart_client()
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
