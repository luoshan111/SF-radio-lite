"""Simple JSON config persistence for BIZHI (data/config.json)."""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    """Return the data directory.

    Normal runs: <project>/data. Frozen (PyInstaller) runs: _MEIPASS is a
    read-only temp dir, so fall back to %APPDATA%/BIZHI for persistence.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(os.environ.get("APPDATA") or Path.home()) / "BIZHI"
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data"


CONFIG_FILE = _data_dir() / "config.json"

DEFAULTS = {
    "wallpaper": {"type": None, "path": "", "color": ""},
    "widgets": {
        "music": {"x": 1480, "y": 100},
    },
    "music_offset_ms": 0,
}


def load_config() -> dict:
    """Load config, merging over DEFAULTS (missing keys keep defaults)."""
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if not CONFIG_FILE.exists():
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        _deep_merge(cfg, saved)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load config: {e}")
    return cfg


def save_config(cfg: dict):
    """Persist config to disk."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"Failed to save config: {e}")


def _deep_merge(base: dict, extra: dict):
    """Recursively merge `extra` into `base` in place."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
