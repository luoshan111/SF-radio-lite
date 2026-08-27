"""QQ Music lyrics widget API: exposed to JS via pywebview.

Includes real-time playback sync via Windows SMTC.
"""

import logging
import threading
import time
from typing import Optional

from widgets.music.qq_music import (
    search_song, get_lyrics, parse_lrc, detect_qq_music_song,
    get_playback_status as _smtc_status, control_playback as _control_playback,
)
from core.taskbar_lyrics import TASKBAR_THEME_DEFAULTS, normalize_theme
from core.tray import is_autostart_enabled, set_autostart

logger = logging.getLogger(__name__)


def _merge_lyrics(original, translation):
    """Merge original + translation lyrics by matching timestamps.

    Returns list of {time_ms, text, trans} where trans is "" if no match.
    """
    merged = []
    trans_map = {}
    for t in translation:
        trans_map[t["time_ms"]] = t["text"]

    for line in original:
        ts = line["time_ms"]
        # Find closest translation (within 500ms tolerance)
        matched_trans = trans_map.get(ts, "")
        if not matched_trans:
            for delta in [100, 200, 300, 500]:
                for check in [ts - delta, ts + delta]:
                    if check in trans_map:
                        matched_trans = trans_map[check]
                        break
                if matched_trans:
                    break
        merged.append({
            "time_ms": ts,
            "text": line["text"],
            "trans": matched_trans,
        })
    return merged


class MusicApi:
    """pywebview JS API for the music lyrics widget."""

    def __init__(self, taskbar_lyrics=None, initial_offset_ms: int = 0):
        self._current_song = None
        self._current_lyrics = []
        self._current_title = ""
        self._current_artist = ""
        self._current_album = ""
        self._current_cover_url = ""
        self._auto_detect = False
        self._detect_thread = None
        self._last_keyword = ""
        self._lrc_offset = 0
        self._user_offset = initial_offset_ms
        self._window = None
        self._cached_lyric_text = "BIZHI - 动态壁纸"
        self._taskbar_lyrics = taskbar_lyrics
        self._bg_thread = None
        self._bg_running = False
        self._last_js_sync = 0.0
        self._start_background_sync()

    # ── Search & Load ──

    def search(self, keyword: str):
        if not keyword or not keyword.strip():
            return {"error": "请输入搜索关键词"}
        results = search_song(keyword.strip())
        if results is None:
            return {"error": "搜索失败，请检查网络连接"}
        if not results:
            return {"error": "未找到相关歌曲"}
        return results

    def load_lyrics(self, songmid: str, songname: str = "", singer: str = "",
                    albumname: str = "", cover_url: str = ""):
        lyrics_data = get_lyrics(songmid)
        if not lyrics_data:
            return {"error": "获取歌词失败（该歌曲可能没有歌词或网络异常）"}
        lrc_offset, parsed = parse_lrc(lyrics_data["lrc"])
        _, trans_parsed = parse_lrc(lyrics_data["trans"]) if lyrics_data.get("trans") else (0, [])

        # Merge original + translation
        merged = _merge_lyrics(parsed, trans_parsed)

        self._current_lyrics = merged
        self._current_title = songname
        self._current_artist = singer
        self._current_album = albumname
        self._current_cover_url = cover_url
        self._current_song = songmid
        self._lrc_offset = lrc_offset
        return {
            "title": songname,
            "artist": singer,
            "album": albumname,
            "cover_url": cover_url,
            "lyrics": merged,
            "lrc_offset": lrc_offset,
        }

    def get_current_info(self):
        return {
            "title": self._current_title,
            "artist": self._current_artist,
            "album": self._current_album,
            "cover_url": self._current_cover_url,
            "lyrics": self._current_lyrics,
            "songmid": self._current_song,
        }

    # ── SMTC real-time playback ──

    def get_playback_status(self):
        """Query current playback via SMTC (shared with qq_music module)."""
        status = _smtc_status()
        if status is None:
            return {"error": "无法获取播放状态（无媒体会话或 SMTC 不可用）"}
        return status

    def auto_sync(self, from_bg: bool = False):
        """Sync playback state + lyrics.

        Called by the frontend (from_bg=False) or the background thread
        (from_bg=True). When the visible widget is polling actively, the
        background thread skips its redundant poll.
        """
        if not from_bg:
            self._last_js_sync = time.time()
        else:
            # Frontend polls every 0.2-2s while visible; skip if it's fresh.
            if self._last_js_sync and (time.time() - self._last_js_sync) < 3.0:
                return None

        status = self.get_playback_status()
        if not status or status.get("error"):
            return status or {"error": "无法获取播放状态"}

        title = status.get("title", "")
        artist = status.get("artist", "")
        keyword = f"{title} {artist}".strip()

        song_changed = False
        if keyword and keyword != self._last_keyword:
            self._last_keyword = keyword
            song_changed = True
            results = search_song(keyword, limit=1)
            if results:
                song = results[0]
                self.load_lyrics(
                    song["songmid"], song["songname"], song["singer"],
                    song.get("albumname", ""), song.get("cover_url", ""),
                )
                status["title"] = song["songname"]
                status["artist"] = song["singer"]
                status["album"] = song.get("albumname", "")
                status["cover_url"] = song.get("cover_url", "")

        status["lyrics"] = self._current_lyrics
        status.setdefault("album", getattr(self, "_current_album", ""))
        status.setdefault("cover_url", getattr(self, "_current_cover_url", ""))
        status["song_changed"] = song_changed
        status["lrc_offset"] = self._lrc_offset
        status["user_offset"] = self._user_offset

        # Update cached lyric text for tray tooltip
        self._update_lyric_cache(status)
        return status

    def set_user_offset(self, offset_ms: int):
        self._user_offset = offset_ms
        # Persist so the offset survives restarts.
        try:
            from core.config import load_config, save_config
            cfg = load_config()
            cfg["music_offset_ms"] = offset_ms
            save_config(cfg)
        except Exception as e:
            logger.debug(f"Failed to persist offset: {e}")
        return {"ok": True, "user_offset": offset_ms}

    def control_playback(self, action: str, position_ms: Optional[int] = None):
        """Send a playback action and return a stable JS-friendly result."""
        allowed = {"toggle", "play", "pause", "previous", "next", "seek"}
        if action not in allowed:
            return {"error": "不支持的播放操作"}
        if action == "seek" and position_ms is None:
            return {"error": "缺少定位时间"}
        ok = _control_playback(action, position_ms)
        return {"ok": ok, "action": action} if ok else {"error": "播放控制不可用", "action": action}

    def get_taskbar_theme(self):
        if not self._taskbar_lyrics:
            return normalize_theme()
        return self._taskbar_lyrics.get_theme()

    def set_taskbar_theme(self, theme):
        normalized = normalize_theme(theme)
        if self._taskbar_lyrics:
            normalized = self._taskbar_lyrics.apply_theme(normalized)
        try:
            from core.config import load_config, save_config
            cfg = load_config()
            cfg["taskbar_lyrics"] = normalized
            save_config(cfg)
        except Exception as e:
            logger.debug(f"Failed to persist taskbar theme: {e}")
            return {"error": "任务栏样式保存失败"}
        return {"ok": True, "theme": normalized}

    def reset_taskbar_theme(self):
        return self.set_taskbar_theme(TASKBAR_THEME_DEFAULTS)

    def get_autostart(self):
        """Return whether BIZHI starts with the current Windows user."""
        return {"enabled": is_autostart_enabled()}

    def set_autostart(self, enabled: bool):
        """Toggle the current user's Windows startup registration."""
        requested = bool(enabled)
        if not set_autostart(requested):
            return {
                "error": "开机自启动设置失败",
                "enabled": is_autostart_enabled(),
            }
        return {"ok": True, "enabled": is_autostart_enabled()}

    # ── Legacy detect ──

    def detect_song(self):
        keyword = detect_qq_music_song()
        if not keyword:
            return {"error": "未检测到QQ音乐播放"}
        results = search_song(keyword, limit=1)
        if results is None:
            return {"error": "搜索失败，请检查网络连接"}
        if not results:
            return {"error": f"未找到匹配歌曲: {keyword}"}
        song = results[0]
        return self.load_lyrics(
            song["songmid"], song["songname"], song["singer"],
            song.get("albumname", ""), song.get("cover_url", ""),
        )

    def start_auto_detect(self):
        self._auto_detect = True
        if self._detect_thread and self._detect_thread.is_alive():
            return {"ok": True}
        self._detect_thread = threading.Thread(target=self._auto_detect_loop, daemon=True)
        self._detect_thread.start()
        return {"ok": True}

    def stop_auto_detect(self):
        self._auto_detect = False
        return {"ok": True}

    def _auto_detect_loop(self):
        last_keyword = ""
        while self._auto_detect:
            try:
                keyword = detect_qq_music_song()
                if keyword and keyword != last_keyword:
                    last_keyword = keyword
                    results = search_song(keyword, limit=1)
                    if results:
                        self.load_lyrics(
                            results[0]["songmid"],
                            results[0]["songname"],
                            results[0]["singer"],
                        )
            except Exception as e:
                logger.debug(f"Auto-detect error: {e}")
            time.sleep(5)

    def _start_background_sync(self):
        """Start a background thread that keeps lyrics updated even when window is hidden."""
        self._bg_running = True
        self._bg_thread = threading.Thread(target=self._bg_sync_loop, daemon=True)
        self._bg_thread.start()

    def _bg_sync_loop(self):
        """Poll SMTC every 2s to keep cached lyrics fresh (skips when the
        visible widget is already polling actively)."""
        while self._bg_running:
            try:
                self.auto_sync(from_bg=True)
            except Exception as e:
                logger.debug(f"Background sync error: {e}")
            time.sleep(2)

    def stop_background_sync(self):
        self._bg_running = False

    def get_current_lyric_text(self):
        """Return a tooltip string: song title + current lyric line."""
        return self._cached_lyric_text

    def _update_lyric_cache(self, status):
        """Compute and cache the lyric tooltip from an existing playback status."""
        pos_ms = status.get("position_ms", 0)
        offset = self._lrc_offset + self._user_offset
        adjusted = pos_ms - offset

        current_text = ""
        for line in reversed(self._current_lyrics):
            if adjusted >= line["time_ms"]:
                current_text = line["text"]
                break

        title = status.get("title", "") or self._current_title
        artist = status.get("artist", "") or self._current_artist
        song_part = f"{title} - {artist}".strip(" -")

        if current_text:
            self._cached_lyric_text = f"♫ {song_part}\n{current_text}" if song_part else current_text
        else:
            self._cached_lyric_text = f"♫ {song_part}" if song_part else "BIZHI - 动态壁纸"

        # Push to taskbar lyrics overlay
        if self._taskbar_lyrics:
            self._taskbar_lyrics.update(title, current_text)

    def minimize(self):
        if self._window:
            self._window.minimize()
        return {"ok": True}

    def restore(self):
        if self._window:
            self._window.restore()
        return {"ok": True}

    def hide(self):
        if self._window:
            try:
                self._window.hide()
            except Exception:
                pass

    def show(self):
        if self._window:
            try:
                self._window.show()
            except Exception:
                pass
