"""Tests for taskbar lyric theme validation and API persistence."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.taskbar_lyrics import TASKBAR_THEME_DEFAULTS, normalize_theme
from widgets.music import api as api_mod


class FakeTaskbarLyrics:

    def __init__(self):
        self.theme = normalize_theme()

    def get_theme(self):
        return dict(self.theme)

    def apply_theme(self, theme):
        self.theme = normalize_theme(theme)
        return dict(self.theme)


def _make_api(taskbar):
    api = api_mod.MusicApi.__new__(api_mod.MusicApi)
    api._taskbar_lyrics = taskbar
    return api


class TestTaskbarTheme(unittest.TestCase):

    def test_invalid_theme_values_use_safe_bounds(self):
        theme = normalize_theme({
            "font_size": 999,
            "font_weight": "heavy",
            "text_color": "not-a-color",
            "effect": "glow",
            "background_mode": "blur",
        })
        self.assertEqual(theme["font_size"], 24)
        self.assertEqual(theme["font_weight"], "bold")
        self.assertEqual(theme["text_color"], TASKBAR_THEME_DEFAULTS["text_color"])
        self.assertEqual(theme["effect"], "outline")
        self.assertEqual(theme["background_mode"], "transparent")

    def test_api_applies_and_persists_theme(self):
        taskbar = FakeTaskbarLyrics()
        api = _make_api(taskbar)
        with mock.patch.object(api_mod, "normalize_theme", wraps=normalize_theme), \
             mock.patch("core.config.load_config", return_value={"music_offset_ms": 0}) as load_config, \
             mock.patch("core.config.save_config") as save_config:
            result = api.set_taskbar_theme({"font_size": 22, "effect": "shadow"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["theme"]["font_size"], 22)
        self.assertEqual(taskbar.theme["effect"], "shadow")
        self.assertEqual(save_config.call_args[0][0]["taskbar_lyrics"]["font_size"], 22)
        load_config.assert_called_once()

    def test_reset_returns_default_theme(self):
        taskbar = FakeTaskbarLyrics()
        api = _make_api(taskbar)
        with mock.patch("core.config.load_config", return_value={}), \
             mock.patch("core.config.save_config"):
            result = api.reset_taskbar_theme()
        self.assertEqual(result["theme"], normalize_theme(TASKBAR_THEME_DEFAULTS))


if __name__ == "__main__":
    unittest.main()
