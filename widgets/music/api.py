"""QQ Music lyrics widget API: exposed to JS via pywebview.

Includes real-time playback sync via Windows SMTC.
"""

import logging
import threading
import time
import asyncio
from typing import Optional

from widgets.music.qq_music import (
    search_song, get_lyrics, parse_lrc, detect_qq_music_song
)

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

    def __init__(self, taskbar_lyrics=None):
        self._current_song = None
        self._current_lyrics = []
        self._current_title = ""
        self._current_artist = ""
        self._auto_detect = False
        self._detect_thread = None
        self._last_keyword = ""
        self._lrc_offset = 0
        self._user_offset = 0
        self._window = None
        self._cached_lyric_text = "BIZHI - 动态壁纸"
        self._taskbar_lyrics = taskbar_lyrics
        self._bg_thread = None
        self._bg_running = False
        self._start_background_sync()

    # ── Search & Load ──

    def search(self, keyword: str):
        if not keyword or not keyword.strip():
            return {"error": "请输入搜索关键词"}
        return search_song(keyword.strip())

    def load_lyrics(self, songmid: str, songname: str = "", singer: str = ""):
        lyrics_data = get_lyrics(songmid)
        if not lyrics_data:
            return {"error": "未找到歌词"}
        lrc_offset, parsed = parse_lrc(lyrics_data["lrc"])
        _, trans_parsed = parse_lrc(lyrics_data["trans"]) if lyrics_data.get("trans") else (0, [])

        # Merge original + translation
        merged = _merge_lyrics(parsed, trans_parsed)

        self._current_lyrics = merged
        self._current_title = songname
        self._current_artist = singer
        self._current_song = songmid
        self._lrc_offset = lrc_offset
        return {
            "title": songname,
            "artist": singer,
            "lyrics": merged,
            "lrc_offset": lrc_offset,
        }

    def get_current_info(self):
        return {
            "title": self._current_title,
            "artist": self._current_artist,
            "lyrics": self._current_lyrics,
            "songmid": self._current_song,
        }

    # ── SMTC real-time playback ──

    def get_playback_status(self):
        try:
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager
            )

            async def _read():
                manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
                session = manager.get_current_session()
                if not session:
                    return None
                props = await session.try_get_media_properties_async()
                timeline = session.get_timeline_properties()
                playback = session.get_playback_info()
                status_val = int(playback.playback_status)
                return {
                    "position_ms": int(timeline.position.total_seconds() * 1000),
                    "duration_ms": int(timeline.end_time.total_seconds() * 1000),
                    "is_playing": status_val == 4,
                    "title": props.title or "",
                    "artist": props.artist or "",
                }

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        return pool.submit(asyncio.run, _read()).result(timeout=3)
                else:
                    return loop.run_until_complete(_read())
            except RuntimeError:
                return asyncio.run(_read())

        except Exception as e:
            logger.debug(f"SMTC status failed: {e}")
            return {"error": str(e)}

    def auto_sync(self):
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
                self.load_lyrics(song["songmid"], song["songname"], song["singer"])
                status["title"] = song["songname"]
                status["artist"] = song["singer"]

        status["lyrics"] = self._current_lyrics
        status["song_changed"] = song_changed
        status["lrc_offset"] = self._lrc_offset
        status["user_offset"] = self._user_offset

        # Update cached lyric text for tray tooltip
        self._update_lyric_cache(status)
        return status

    def set_user_offset(self, offset_ms: int):
        self._user_offset = offset_ms
        return {"ok": True, "user_offset": offset_ms}

    # ── Legacy detect ──

    def detect_song(self):
        keyword = detect_qq_music_song()
        if not keyword:
            return {"error": "未检测到QQ音乐播放"}
        results = search_song(keyword, limit=1)
        if not results:
            return {"error": f"未找到匹配歌曲: {keyword}"}
        song = results[0]
        return self.load_lyrics(song["songmid"], song["songname"], song["singer"])

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
        """Poll SMTC every 2s to keep cached lyrics fresh."""
        while self._bg_running:
            try:
                self.auto_sync()
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
