"""Unit tests for core.config (JSON persistence with defaults)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfg


class ConfigTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_file = cfg.CONFIG_FILE
        cfg.CONFIG_FILE = Path(self._tmp.name) / "config.json"

    def tearDown(self):
        cfg.CONFIG_FILE = self._orig_file
        self._tmp.cleanup()

    def test_defaults_when_missing(self):
        c = cfg.load_config()
        self.assertEqual(c["music_offset_ms"], 0)
        self.assertEqual(c["widgets"]["music"]["x"], 1480)
        self.assertEqual(c["widgets"]["music"]["y"], 100)
        self.assertTrue(c["taskbar_lyrics"]["enabled"])

    def test_deep_copy_of_defaults(self):
        c1 = cfg.load_config()
        c1["widgets"]["music"]["x"] = 999
        c2 = cfg.load_config()
        self.assertEqual(c2["widgets"]["music"]["x"], 1480)

    def test_round_trip(self):
        c = cfg.load_config()
        c["music_offset_ms"] = -500
        c["taskbar_lyrics"]["preset"] = "ice"
        c["widgets"]["music"] = {"x": 11, "y": 22}
        cfg.save_config(c)
        c2 = cfg.load_config()
        self.assertEqual(c2["music_offset_ms"], -500)
        self.assertEqual(c2["taskbar_lyrics"]["preset"], "ice")
        self.assertEqual(c2["widgets"]["music"]["x"], 11)

    def test_corrupt_file_falls_back_to_defaults(self):
        cfg.CONFIG_FILE.write_text("{not json", encoding="utf-8")
        c = cfg.load_config()
        self.assertEqual(c["music_offset_ms"], 0)

    def test_partial_config_merges_with_defaults(self):
        cfg.CONFIG_FILE.write_text('{"music_offset_ms": 300}', encoding="utf-8")
        c = cfg.load_config()
        self.assertEqual(c["music_offset_ms"], 300)
        self.assertEqual(c["widgets"]["music"]["y"], 100)


if __name__ == "__main__":
    unittest.main()
