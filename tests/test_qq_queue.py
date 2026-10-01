"""Queue snapshot safety tests; native UI is covered by client smoke testing."""
import unittest
from unittest import mock

from widgets.music.qq_queue import QQQueueBridge, QueueError


class TestQueueSnapshots(unittest.TestCase):
    def bridge(self):
        bridge = QQQueueBridge()
        bridge._initialize = mock.Mock()
        bridge._user32 = mock.Mock()
        bridge._user32.GetForegroundWindow.return_value = 1
        bridge._ensure_queue = mock.Mock(return_value="queue")
        bridge._all = mock.Mock(return_value=[])
        bridge._rows = mock.Mock(return_value=[{
            "title": "Song", "artist": "Singer", "runtime_id": (1, 2),
            "element": "fresh-element",
        }])
        bridge._click = mock.Mock()
        return bridge

    def test_refresh_invalidates_previous_page_before_input(self):
        bridge = self.bridge()
        old = bridge.read_page()
        bridge.read_page()
        result = bridge.play_item(old["items"][0]["id"], old["page_token"])
        self.assertIn("error", result)
        bridge._click.assert_not_called()

    def test_client_changed_or_duplicate_rows_cannot_be_clicked(self):
        for rows in ([], [{"title": "Different", "artist": "Singer"}],
                     [{"title": "Song", "artist": "Singer"}] * 2):
            with self.subTest(rows=rows):
                bridge = self.bridge()
                page = bridge.read_page()
                bridge._rows.return_value = rows
                result = bridge.play_item(page["items"][0]["id"], page["page_token"])
                self.assertIn("error", result)
                bridge._click.assert_not_called()

    def test_failed_refresh_invalidates_selection(self):
        bridge = self.bridge()
        page = bridge.read_page()
        bridge._ensure_queue.side_effect = QueueError("client closed")
        self.assertIn("error", bridge.read_page())
        self.assertIn("error", bridge.play_item(page["items"][0]["id"], page["page_token"]))
        bridge._click.assert_not_called()

    def test_selection_uses_newly_resolved_element(self):
        bridge = self.bridge()
        page = bridge.read_page()
        bridge._rows.return_value = [{"title": "Song", "artist": "Singer", "element": "new-element"}]
        self.assertTrue(bridge.play_item(page["items"][0]["id"], page["page_token"])["ok"])
        bridge._click.assert_called_once_with("new-element", None, double=True)
