"""QQ Music API client: search songs, fetch lyrics, detect current playback.

Uses Windows SMTC (System Media Transport Controls) for detection,
falls back to window title enumeration.
"""

import re
import json
import logging
import asyncio
import ctypes
import concurrent.futures
import requests
from typing import Optional

logger = logging.getLogger(__name__)

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

HEADERS = {
    "Referer": "https://y.qq.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _run_async(coro, timeout: float = 5.0):
    """Run a coroutine from a possibly-busy thread.

    pywebview owns the main event loop, so if a loop is already running in
    this thread we execute the coroutine in a fresh thread instead.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=timeout)
    return loop.run_until_complete(coro)


def search_song(keyword: str, limit: int = 5):
    """Search QQ Music for songs by keyword.

    Returns a list of {songmid, songname, singer, albumname, interval},
    or None when the request itself failed (network / API error) —
    callers can then distinguish "no results" ([]) from "failed" (None).
    """
    params = {
        "w": keyword,
        "format": "json",
        "p": 1,
        "n": limit,
        "cr": 1,
        "new_json": 1,
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        songs = data.get("data", {}).get("song", {}).get("list", [])
        results = []
        for s in songs:
            singers = "/".join(
                si.get("name", "") for si in s.get("singer", [])
            )
            results.append({
                "songmid": s.get("mid", s.get("songmid", "")),
                "songname": s.get("name", s.get("songname", s.get("title", ""))),
                "singer": singers,
                "albumname": s.get("album", {}).get("name", s.get("albumname", "")),
                "interval": s.get("interval", 0),
            })
        return results
    except Exception as e:
        logger.error(f"QQ Music search failed: {e}")
        return None


def get_lyrics(songmid: str) -> Optional[dict]:
    """Fetch lyrics for a song by its mid.

    Returns {lrc: str, trans: str} or None.
    """
    params = {
        "songmid": songmid,
        "format": "json",
        "nobase64": 1,
    }
    try:
        resp = requests.get(LYRIC_URL, params=params, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        lyric = data.get("lyric", "")
        trans = data.get("trans", "")
        if lyric:
            return {"lrc": lyric, "trans": trans}
        return None
    except Exception as e:
        logger.error(f"Failed to fetch lyrics for {songmid}: {e}")
        return None


def parse_lrc(lrc_text: str) -> tuple:
    """Parse LRC format into (offset_ms, list of {time_ms, text}).

    Supports [mm:ss.xx], [mm:ss:xx], and [offset:+/-xxx] tags.
    """
    lines = []
    offset_ms = 0
    offset_pattern = re.compile(r"\[offset:([+-]?\d+)\]")
    time_pattern = re.compile(r"\[(\d{1,3}):(\d{2})[.:](\d{2,3})\](.*)")

    for raw in lrc_text.splitlines():
        stripped = raw.strip()
        om = offset_pattern.match(stripped)
        if om:
            offset_ms = int(om.group(1))
            continue
        tm = time_pattern.match(stripped)
        if tm:
            mins = int(tm.group(1))
            secs = int(tm.group(2))
            ms_part = tm.group(3)
            ms = int(ms_part.ljust(3, "0")) if len(ms_part) == 2 else int(ms_part)
            total_ms = mins * 60000 + secs * 1000 + ms
            text = tm.group(4).strip()
            if text:
                lines.append({"time_ms": total_ms, "text": text})

    lines.sort(key=lambda x: x["time_ms"])
    return offset_ms, lines


def get_playback_status() -> Optional[dict]:
    """Query the current SMTC media session (single source of truth).

    Returns {title, artist, app_id, position_ms, duration_ms, is_playing}
    or None when no session is available / SMTC is unsupported.
    """
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager
        )

        async def _get():
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = manager.get_current_session()
            if not session:
                return None
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
            return {
                "title": props.title or "",
                "artist": props.artist or "",
                "app_id": session.source_app_user_model_id or "",
                "position_ms": int(timeline.position.total_seconds() * 1000),
                "duration_ms": int(timeline.end_time.total_seconds() * 1000),
                "is_playing": int(playback.playback_status) == 4,
            }

        return _run_async(_get(), timeout=5)
    except ImportError:
        logger.debug("winsdk not available, SMTC disabled")
        return None
    except Exception as e:
        logger.debug(f"SMTC status failed: {e}")
        return None


def _detect_via_smtc() -> Optional[dict]:
    """Detect currently playing media via Windows SMTC API.

    Returns {title, artist, app_id} or None.
    """
    status = get_playback_status()
    if status and status.get("title"):
        return {
            "title": status["title"],
            "artist": status.get("artist", ""),
            "app_id": status.get("app_id", ""),
        }
    return None


def _detect_via_window_title() -> Optional[str]:
    """Fallback: detect from QQ Music window title."""
    try:
        user32 = ctypes.windll.user32
        titles = []

        def enum_callback(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if "QQ音乐" in title and "歌词" not in title and "Dummy" not in title:
                    parts = title.split(" - ", 1)
                    if len(parts) == 2 and parts[0].strip():
                        titles.append(title)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        )
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

        if titles:
            title = titles[0]
            title = re.sub(r"\s*[-–]\s*QQ\s*音乐\s*$", "", title)
            parts = title.split(" - ", 1)
            if len(parts) == 2:
                return f"{parts[0]} {parts[1]}"
            return title.strip()
    except Exception as e:
        logger.debug(f"Window title detection failed: {e}")
    return None


def detect_qq_music_song() -> Optional[str]:
    """Detect the currently playing song.

    Uses Windows SMTC first (reliable), falls back to window title.
    Returns search keyword string or None.
    """
    # Try SMTC first (works with modern QQ Music)
    smtc = _detect_via_smtc()
    if smtc and smtc["title"]:
        keyword = f"{smtc['title']} {smtc['artist']}".strip()
        logger.info(f"SMTC detected: {keyword} (from {smtc['app_id']})")
        return keyword

    # Fallback to window title
    result = _detect_via_window_title()
    if result:
        logger.info(f"Window title detected: {result}")
        return result

    return None