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
import os
import threading
import time
import requests
from typing import Optional

logger = logging.getLogger(__name__)

_smtc_watcher: Optional["SmtcWatcher"] = None

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
# The desktop client keeps the logged-in uin in plain text here.
CLIENT_CONFIG = os.path.expandvars(r"%APPDATA%\Tencent\QQMusic\QQMusicServiceConfig.ini")

HEADERS = {
    "Referer": "https://y.qq.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def album_cover_url(albummid: str, size: int = 500) -> str:
    """Build QQ Music's stable album artwork URL for an album mid."""
    if not albummid:
        return ""
    safe_size = max(100, min(500, int(size)))
    return f"https://y.gtimg.cn/music/photo_new/T002R{safe_size}x{safe_size}M000{albummid}.jpg?max_age=2592000"


_async_loop = None
_async_loop_lock = threading.Lock()


def _run_async(coro, timeout: float = 5.0):
    """Run a coroutine on one persistent background event loop.

    Creating a loop per call (asyncio.run) cost a thread + WinRT teardown on
    every SMTC poll; a shared loop makes repeated calls nearly free.
    """
    global _async_loop
    if _async_loop is None or _async_loop.is_closed():
        with _async_loop_lock:
            if _async_loop is None or _async_loop.is_closed():
                _async_loop = asyncio.new_event_loop()
                threading.Thread(
                    target=_async_loop.run_forever, name="smtc-event-loop", daemon=True
                ).start()
    future = asyncio.run_coroutine_threadsafe(coro, _async_loop)
    return future.result(timeout=timeout)


def _parse_song(s: dict) -> dict:
    """Map a song entry from either the musicu or legacy soso response."""
    singers = "/".join(
        si.get("name", "") for si in s.get("singer", []) if isinstance(si, dict)
    )
    album = s.get("album") if isinstance(s.get("album"), dict) else {}
    albummid = album.get("mid", s.get("albummid", ""))
    return {
        "songmid": s.get("mid", s.get("songmid", "")),
        "songname": s.get("name", s.get("songname", s.get("title", ""))),
        "singer": singers,
        "albumname": album.get("name", s.get("albumname", "")),
        "albummid": albummid,
        "cover_url": album_cover_url(albummid),
        "interval": s.get("interval", 0),
    }


def _search_via_musicu(keyword: str, limit: int, attempts: int = 3) -> list:
    """The desktop search behind y.qq.com; client_search_cp died in 2026-09.

    The endpoint intermittently throttles to an HTTP-200 empty song list, so
    an empty body is retried a few times before it is believed.
    """
    payload = {
        "req_1": {
            "method": "DoSearchForQQMusicDesktop",
            "module": "music.search.SearchCgiService",
            "param": {
                "search_type": 0,
                "query": keyword,
                "page_num": 1,
                "num_per_page": limit,
            },
        }
    }
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.6)
        resp = requests.post(MUSICU_URL, json=payload, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        body = resp.json().get("req_1", {}).get("data", {}).get("body", {})
        songs = (body.get("song") or {}).get("list") or []
        if songs:
            return [_parse_song(s) for s in songs]
    return []


def _search_via_soso(keyword: str, limit: int) -> list:
    """Legacy soso endpoint, kept as a fallback while it still answers."""
    params = {
        "w": keyword,
        "format": "json",
        "p": 1,
        "n": limit,
        "cr": 1,
        "new_json": 1,
    }
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=8)
    resp.raise_for_status()
    songs = resp.json().get("data", {}).get("song", {}).get("list", [])
    return [_parse_song(s) for s in songs]


def search_song(keyword: str, limit: int = 5):
    """Search QQ Music for songs by keyword.

    Returns a list of {songmid, songname, singer, albumname, interval},
    or None when the request itself failed (network / API error) —
    callers can then distinguish "no results" ([]) from "failed" (None).
    """
    try:
        results = _search_via_musicu(keyword, limit)
        if results:
            return results
    except Exception as e:
        logger.warning(f"QQ Music musicu search failed: {e}")
    try:
        results = _search_via_soso(keyword, limit)
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


def get_client_uin() -> Optional[str]:
    """Read the logged-in uin from the desktop client's plain-text config."""
    try:
        with open(CLIENT_CONFIG, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip().lower().startswith("uin="):
                    uin = line.split("=", 1)[1].strip()
                    if uin.isdigit():
                        return uin
    except OSError:
        pass
    return None


def _musicu(module: str, method: str, param: dict, attempts: int = 3):
    """One musicu.fcg call; the gateway intermittently throttles to empty
    bodies, so results are retried before they are believed."""
    payload = {"req_1": {"module": module, "method": method, "param": param}}
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.6)
        try:
            resp = requests.post(
                MUSICU_URL, json=payload,
                headers={**HEADERS, "Referer": "https://i.y.qq.com/"}, timeout=8,
            )
            resp.raise_for_status()
            inner = resp.json().get("req_1", {})
            if inner.get("code") not in (0, None):
                last = RuntimeError(f"code {inner.get('code')}")
                continue
            data = inner.get("data")
            if data is not None and data.get("code") not in (0, None):
                last = RuntimeError(data.get("msg") or f"code {data.get('code')}")
                continue
            return data
        except Exception as e:
            last = e
    raise last if last else RuntimeError("musicu call failed")


def get_user_playlists(uin: str):
    """The logged-in user's own playlists (我喜欢 + created), via public API."""
    data = _musicu("music.musicasset.PlaylistBaseRead", "GetPlaylistByUin",
                   {"uin": str(uin)})
    playlists = []
    for item in data.get("v_playlist", []):
        cover = item.get("bigpicUrl") or item.get("picUrl") or ""
        playlists.append({
            "tid": item.get("tid"),
            "dirid": item.get("dirId"),
            "name": item.get("dirName", ""),
            "song_num": item.get("songNum", 0),
            "cover_url": cover.replace("http://", "https://"),
        })
    return playlists


def get_playlist_songs(tid: int, begin: int = 0, num: int = 30):
    """One page of a playlist's songs, with full real song identity."""
    data = _musicu("music.srfDissInfo.DissInfo", "CgiGetDiss", {
        "disstid": tid, "song_begin": begin, "song_num": num,
        "tag": False, "userinfo": False, "orderlist": False,
    })
    songs = []
    for item in data.get("songlist", []):
        info = item.get("songinfo") or item
        singers = "/".join(s.get("name", "") for s in info.get("singer", []) if isinstance(s, dict))
        album = info.get("album") if isinstance(info.get("album"), dict) else {}
        albummid = album.get("mid", "")
        songs.append({
            "songmid": info.get("mid", ""),
            "songname": info.get("name", ""),
            "singer": singers,
            "albumname": album.get("name", ""),
            "cover_url": album_cover_url(albummid) if albummid else "",
            "interval": info.get("interval", 0),
        })
    return {"songs": songs, "has_more": bool(data.get("hasmore"))}


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


def _select_media_session(manager):
    """Prefer QQ Music so a browser video cannot take over queue controls."""
    for session in manager.get_sessions():
        if "qqmusic" in (session.source_app_user_model_id or "").casefold():
            return session
    return manager.get_current_session()


class SmtcWatcher:
    """Subscribes to SMTC events and keeps a fresh playback snapshot.

    Polling used to recreate the session manager and an event loop on every
    query (5 times per second while playing); the watcher listens to
    MediaProperties / PlaybackInfo / TimelineProperties / SessionsChanged
    events instead and get_playback_status() just reads the snapshot.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.snapshot = {}
        self.position_at = 0.0     # time.monotonic() when position was cached
        self.ready = False
        self.failed = False
        self._manager = None
        self._session = None
        self._tokens = []
        self._starting = False

    def ensure_started(self):
        if self.ready or self.failed or self._starting:
            return
        self._starting = True
        try:
            _run_async(self._start(), timeout=15)
        except Exception as e:
            logger.warning(f"SMTC watcher failed to start, falling back to polling: {e}")
            self.failed = True
        finally:
            self._starting = False

    async def _start(self):
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as G,
        )
        self._manager = await G.request_async()
        self._tokens.append(self._manager.add_sessions_changed(self._on_sessions_changed))
        self._tokens.append(self._manager.add_current_session_changed(self._on_current_changed))
        await self._bind(self._select_session())
        self.ready = bool(self._session) or True   # ready even with no session yet

    def _select_session(self):
        manager = self._manager
        if not manager:
            return None
        return _select_media_session(manager)

    def _on_sessions_changed(self, sender, args):
        try:
            _async_loop.call_soon_threadsafe(
                lambda: _run_coro(self._rebind(), "sessions changed"))
        except Exception:
            pass

    def _on_current_changed(self, sender, args):
        try:
            _async_loop.call_soon_threadsafe(
                lambda: _run_coro(self._rebind(), "current changed"))
        except Exception:
            pass

    def _on_session_event(self, sender, args):
        try:
            _async_loop.call_soon_threadsafe(
                lambda: _run_coro(self._refresh(sender), "session event"))
        except Exception:
            pass

    async def _rebind(self):
        session = self._select_session()
        if session is not self._session:
            await self._bind(session)
        else:
            await self._refresh(self._session)

    async def _bind(self, session):
        for source, token in self._tokens[2:]:
            try:
                source.remove_media_properties_changed(token)
                source.remove_playback_info_changed(token)
                source.remove_timeline_properties_changed(token)
            except Exception:
                pass
        self._tokens = self._tokens[:2]
        self._session = session
        if session is None:
            with self.lock:
                self.snapshot = {}
            return
        self._tokens.append((session, session.add_media_properties_changed(self._on_session_event)))
        self._tokens.append((session, session.add_playback_info_changed(self._on_session_event)))
        self._tokens.append((session, session.add_timeline_properties_changed(self._on_session_event)))
        await self._refresh(session)

    async def _refresh(self, session):
        if session is None:
            return
        try:
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
            with self.lock:
                self.snapshot = {
                    "title": props.title or "",
                    "artist": props.artist or "",
                    "album": props.album_title or "",
                    "app_id": session.source_app_user_model_id or "",
                    "position_ms": int(timeline.position.total_seconds() * 1000),
                    "duration_ms": int(timeline.end_time.total_seconds() * 1000),
                    "is_playing": int(playback.playback_status) == 4,
                }
                self.position_at = time.monotonic()
        except Exception as e:
            logger.debug(f"SMTC refresh failed: {e}")

    def get_status(self):
        with self.lock:
            status = dict(self.snapshot)
            at = self.position_at
        if not status:
            return None
        if status.get("is_playing"):
            # Interpolate the position from the last timeline event.
            status["position_ms"] = status.get("position_ms", 0) + int(
                (time.monotonic() - at) * 1000)
        return status


def _run_coro(coro, label):
    """Fire-and-forget a refresh coroutine on the shared loop."""
    async def _wrap():
        try:
            await coro
        except Exception as e:
            logger.debug(f"SMTC watcher {label} failed: {e}")
    future = asyncio.run_coroutine_threadsafe(_wrap(), _async_loop)


def get_playback_status() -> Optional[dict]:
    """Read the current SMTC media session (single source of truth).

    Returns {title, artist, album, app_id, position_ms, duration_ms, is_playing}
    or None when no session is available / SMTC is unsupported.
    """
    global _smtc_watcher
    try:
        if _smtc_watcher is None:
            _smtc_watcher = SmtcWatcher()
        _smtc_watcher.ensure_started()
        if _smtc_watcher.ready:
            return _smtc_watcher.get_status()
    except ImportError:
        logger.debug("winsdk not available, SMTC disabled")
        return None
    except Exception as e:
        logger.debug(f"SMTC watcher path failed: {e}")

    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager
        )

        async def _get():
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = _select_media_session(manager)
            if not session:
                return None
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
            return {
                "title": props.title or "",
                "artist": props.artist or "",
                "album": props.album_title or "",
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


def control_playback(action: str, position_ms: Optional[int] = None) -> bool:
    """Send a playback command to the current Windows media session."""
    actions = {
        "toggle": "try_toggle_play_pause_async",
        "play": "try_play_async",
        "pause": "try_pause_async",
        "previous": "try_skip_previous_async",
        "next": "try_skip_next_async",
    }
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager
        )

        async def _control():
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = _select_media_session(manager)
            if not session:
                return False
            if action == "seek":
                if position_ms is None:
                    return False
                result = await session.try_change_playback_position_async(
                    max(0, int(position_ms)) * 10000
                )
            else:
                method_name = actions.get(action)
                if not method_name:
                    return False
                result = await getattr(session, method_name)()
            return bool(result)

        return bool(_run_async(_control(), timeout=5))
    except ImportError:
        logger.debug("winsdk not available, playback controls disabled")
        return False
    except Exception as e:
        logger.debug(f"Playback control failed ({action}): {e}")
        return False


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
