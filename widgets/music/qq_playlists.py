"""Read QQ Music's library page (the 我喜欢 song list) through Windows UIA.

The client renders pages with CEF offscreen surfaces and only bridges the
accessibility tree of the surface created at process start, so the client must
be launched with --force-renderer-accessibility and the readable page is the
one shown at boot (in practice the 喜欢 library page). Navigating inside the
client kills the tree until the next restart; :meth:`restart_client` handles
that from the widget.

Rows are anchored on the right-column 时长：MM:SS labels: a real row carries a
title/artist pair in the left column and its duration in the right one. Column
ranges are window-relative so the parser survives moving the window.
"""

import ctypes
from ctypes import wintypes as wt
import hashlib
import re
import subprocess
import time
import uuid

from widgets.music.qq_queue import BADGES, QQQueueBridge, QueueError

QQMUSIC_EXE = "D:\\Program Files\\Tencent\\QQMusic\\QQMusic.exe"
ACCESSIBILITY_FLAG = "--force-renderer-accessibility"
# Wheel turns are paced so the virtualized list can load its chunks.
WHEEL_NOTCHES = 4
WHEEL_PACE = 0.12
WHEEL_SETTLE = 0.8
# Column zones as fractions of the window size, so the parser survives
# the client running maximized, restored, or on another monitor/DPI.
TITLE_X = (0.12, 0.55)
TITLE_W = 0.28
DURATION_X = 0.70
HEADER_X = (0.08, 0.35)
HEADER_Y = (0.03, 0.30)
SIDEBAR_X = 0.09
# Column-header and toolbar fragments that can drift into a row band while
# the list scrolls; they must never become a title or artist.
NON_SONG = {"歌名/歌手", "歌名", "歌手", "专辑", "时长", "播放", "下载",
            "批量", "批量操作", "搜索", "播放全部", "评论"}


class QQPageBridge(QQQueueBridge):
    """Browse and play the songs of the client's readable library page."""

    def __init__(self):
        super().__init__()
        self._page_token = ""   # separate from the queue bridge's token
        self._page_items = {}

    # ── page structure ──

    def _content_rows(self, show=True):
        """Named on-screen elements as (name, screen_rect, element) tuples.

        Returns (elements, hwnd, rect) with the main window's screen rect so
        callers can compute window-relative column zones.
        """
        main_el, hwnd = self._main_window(show=show)
        rect = main_el.Current.BoundingRectangle
        window_rect = (int(rect.X), int(rect.Y), int(rect.Width), int(rect.Height))
        out = []
        for child in self._all(main_el, children=True)[1:]:
            for el in self._all(child):
                try:
                    c = el.Current
                    r = c.BoundingRectangle
                    if not c.Name or len(c.Name) > 60 or r.IsEmpty or c.IsOffscreen:
                        continue
                    out.append((str(c.Name), (int(r.X), int(r.Y), int(r.Width), int(r.Height)), el))
                except Exception:
                    continue
        return out, hwnd, window_rect

    def _anchor_rows(self, elements, window_rect):
        """Pair title/artist per 时长 anchor; only fully formed rows survive."""
        wx, wy, ww, wh = window_rect
        durations = [(n, r) for n, r, _ in elements
                     if n.startswith("时长：") and r[0] - wx >= DURATION_X * ww]
        texts = sorted(
            [(n, r) for n, r, _ in elements
             if TITLE_X[0] * ww <= r[0] - wx <= TITLE_X[1] * ww
             and r[2] <= TITLE_W * ww and len(n) >= 2
             and not n.startswith("时长") and n not in ("MV", " - ") and n not in BADGES
             and n not in NON_SONG],
            key=lambda x: (x[1][1], x[1][0]),
        )
        rows = []
        for name, rect in durations:
            # The title starts slightly above the duration label and the
            # artist ends below it; a band around the anchor covers exactly
            # one row of the list layout.
            top, bottom = rect[1] - 25, rect[1] + rect[3] + 20
            pair = [t for t in texts if top <= t[1][1] and t[1][1] + t[1][3] <= bottom]
            if len(pair) < 2:
                continue
            title, artist = pair[0], pair[1]
            # Title and artist stack vertically inside the row block.
            if not 0 <= artist[1][1] - (title[1][1] + title[1][3]) <= 24:
                continue
            rows.append({"title": title[0], "artist": artist[0],
                         "element": None, "top": float(title[1][1]),
                         "rect": title[1]})
        rows.sort(key=lambda row: row["top"])
        seen, unique = set(), []
        for row in rows:
            key = (row["title"], row["artist"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    def _attach_elements(self, rows, elements):
        by_rect = {}
        for n, r, el in elements:
            by_rect.setdefault(r[:2], el)
        for row in rows:
            row["element"] = by_rect.get(row["rect"][:2])
        return rows

    def _page_ready(self, elements, window_rect):
        return bool(self._anchor_rows(elements, window_rect))

    def _header_name(self, elements, window_rect):
        wx, wy, ww, wh = window_rect
        for n, r, _ in elements:
            if (n in ("喜欢", "最近播放", "本地和下载", "已购音乐", "试听列表")
                    and HEADER_X[0] * ww <= r[0] - wx <= HEADER_X[1] * ww
                    and HEADER_Y[0] * wh <= r[1] - wy <= HEADER_Y[1] * wh):
                return n
        return ""

    def _total(self, elements, window_rect):
        wx, wy, ww, wh = window_rect
        for n, r, _ in elements:
            match = re.search(r"[·•](\d+)$", n)
            if match and r[0] - wx <= SIDEBAR_X * ww:
                return int(match.group(1))
        return 0

    # ── paging ──

    def _wheel_page(self, hwnd, window_rect, direction, multiplier=1):
        elements, _, _ = self._content_rows(show=False)
        rows = self._anchor_rows(elements, window_rect)
        if not rows:
            raise QueueError("页面上没有可定位的歌曲行，请确认 QQ 音乐停留在歌曲列表页。")
        anchor = rows[len(rows) // 2]["rect"]
        x, y = anchor[0] + anchor[2] // 2, anchor[1] + anchor[3] // 2
        # The client window fades in on activation; the hit test can lag, so
        # retry before giving up.
        for attempt in range(3):
            self._user32.SetCursorPos(x, y)
            time.sleep(0.1)
            if self._user32.WindowFromPoint(wt.POINT(x, y)) == hwnd:
                break
            if attempt == 2:
                raise QueueError("QQ 音乐窗口未显示在最前，请移开遮挡的窗口后重试。")
            self._bring_to_front(hwnd)
            time.sleep(0.6)
        delta = -120 * WHEEL_NOTCHES if direction == "next" else 120 * WHEEL_NOTCHES
        for _ in range(max(1, (WHEEL_NOTCHES * multiplier) // 2)):
            self._user32.mouse_event(0x800, 0, 0, delta & 0xFFFFFFFF, 0)
            time.sleep(WHEEL_PACE)
        time.sleep(WHEEL_SETTLE)

    def _ensure_page(self):
        elements, hwnd, window_rect = self._content_rows()
        if not self._page_ready(elements, window_rect):
            raise QueueError(
                "读不到 QQ 音乐页面内容：请点「重启客户端」带无障碍参数重启 QQ 音乐，"
                "并让窗口停留在歌曲列表页。"
            )
        return elements, hwnd, window_rect

    # ── public API ──

    def status(self):
        """Cheap probe for the frontend: is the page readable right now?"""
        with self._lock:
            try:
                self._initialize()
                elements, _, window_rect = self._content_rows()
                return {"ok": self._page_ready(elements, window_rect),
                        "page": self._header_name(elements, window_rect),
                        "total": self._total(elements, window_rect)}
            except QueueError as error:
                return {"ok": False, "error": str(error)}
            except Exception:
                return {"ok": False, "error": "检测 QQ 音乐页面状态失败。"}

    def restart_client(self):
        """Restart QQ Music with the accessibility flag; wait for the page.

        The client sometimes self-relaunches right after boot (with
        /background, dropping the flag), so a success only counts once the
        page has stayed readable — otherwise the restart is retried.
        """
        with self._lock:
            last_error = "客户端启动后页面仍不可读"
            try:
                self._initialize()
                for _ in range(3):
                    subprocess.run(["taskkill", "/IM", "QQMusic.exe", "/F"],
                                   capture_output=True, timeout=15)
                    time.sleep(2)
                    subprocess.Popen([QQMUSIC_EXE, ACCESSIBILITY_FLAG], close_fds=True)
                    deadline = time.monotonic() + 60
                    ready = False
                    while time.monotonic() < deadline:
                        time.sleep(3)
                        try:
                            elements, _, window_rect = self._content_rows()
                            if self._page_ready(elements, window_rect):
                                ready = True
                                break
                        except QueueError:
                            continue
                    if not ready:
                        continue
                    time.sleep(6)
                    try:
                        elements, _, window_rect = self._content_rows()
                        if self._page_ready(elements, window_rect):
                            return {"ok": True, "page": self._header_name(elements, window_rect)}
                    except QueueError:
                        pass
                    last_error = "客户端启动后自行重启并丢失了无障碍参数，已自动重试仍失败"
                return {"error": last_error + "。请手动检查 QQ 音乐是否正常启动。"}
            except Exception:
                return {"error": "重启 QQ 音乐失败，请手动以无障碍参数启动客户端。"}

    def read_page(self, direction="refresh"):
        if direction not in {"refresh", "next", "previous", "first"}:
            return {"error": "不支持的歌单翻页操作"}
        with self._lock:
            previous, cursor = None, None
            try:
                self._initialize()
                previous = self._user32.GetForegroundWindow()
                point = wt.POINT()
                self._user32.GetCursorPos(ctypes.byref(point))
                cursor = (point.x, point.y)
                elements, hwnd, window_rect = self._ensure_page()
                before = [(r["title"], r["artist"]) for r in self._anchor_rows(elements, window_rect)]
                if direction != "refresh":
                    if direction == "first":
                        for _ in range(60):
                            self._wheel_page(hwnd, window_rect, "previous", multiplier=3)
                            current = [(r["title"], r["artist"]) for r in self._anchor_rows(
                                self._content_rows(show=False)[0], window_rect)]
                            if current == before:
                                break
                            before = current
                    else:
                        self._wheel_page(hwnd, window_rect, direction)
                elements, _, _ = self._content_rows(show=False)
                rows = self._anchor_rows(elements, window_rect)
                if not rows:
                    raise QueueError("无法读取当前页面歌曲，请刷新后重试。")
                token = uuid.uuid4().hex
                self._page_token = token
                self._page_items = {}
                items = []
                for index, row in enumerate(rows):
                    item_id = hashlib.sha256(
                        f"{row['title']}|{row['artist']}|{token}|{index}".encode()
                    ).hexdigest()[:24]
                    self._page_items[item_id] = (row["title"], row["artist"])
                    items.append({"id": item_id, "title": row["title"], "artist": row["artist"]})
                unchanged = [(r["title"], r["artist"]) for r in rows] == before
                return {"items": items, "page": self._header_name(elements, window_rect),
                        "total": self._total(elements, window_rect), "page_token": token,
                        "at_start": direction in ("first", "refresh") or
                                    (direction == "previous" and unchanged),
                        "at_end": direction == "next" and unchanged}
            except QueueError as error:
                self._page_token, self._page_items = "", {}
                return {"error": str(error)}
            except Exception:
                self._page_token, self._page_items = "", {}
                return {"error": "读取 QQ 音乐歌单页面失败，请重试。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)

    def play_song_by_name(self, title, artist):
        """Play a song that is visible on the current client page.

        Returns ok only when exactly one matching row is on screen; the
        caller falls back to queue location otherwise.
        """
        with self._lock:
            previous, cursor = None, None
            try:
                self._initialize()
                previous = self._user32.GetForegroundWindow()
                point = wt.POINT()
                self._user32.GetCursorPos(ctypes.byref(point))
                cursor = (point.x, point.y)
                elements, hwnd, window_rect = self._ensure_page()
                rows = self._attach_elements(self._anchor_rows(elements, window_rect), elements)
                norm = QQQueueBridge._norm
                matches = [r for r in rows if r["element"] is not None
                           and norm(r["title"]) == norm(title)
                           and norm(r["artist"]) == norm(artist)]
                if len(matches) != 1:
                    return {"error": "该歌曲不在客户端当前可见页面。"}
                self._click(matches[0]["element"], hwnd, double=True)
                return {"ok": True, "title": title, "artist": artist}
            except QueueError as error:
                return {"error": str(error)}
            except Exception:
                return {"error": "点歌失败，请重试。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)

    def play_item(self, item_id, page_token):
        with self._lock:
            if not page_token or page_token != self._page_token or item_id not in self._page_items:
                return {"error": "歌单页面已更新，请刷新后重新选择歌曲。"}
            previous, cursor = None, None
            try:
                self._initialize()
                previous = self._user32.GetForegroundWindow()
                point = wt.POINT()
                self._user32.GetCursorPos(ctypes.byref(point))
                cursor = (point.x, point.y)
                elements, hwnd, window_rect = self._ensure_page()
                rows = self._attach_elements(self._anchor_rows(elements, window_rect), elements)
                title, artist = self._page_items[item_id]
                matches = [r for r in rows if (r["title"], r["artist"]) == (title, artist)
                           and r["element"] is not None]
                if len(matches) != 1:
                    raise QueueError("客户端页面已滚动或改变，请刷新歌单后再点歌。")
                self._click(matches[0]["element"], hwnd, double=True)
                return {"ok": True, "title": title, "artist": artist}
            except QueueError as error:
                return {"error": str(error)}
            except Exception:
                return {"error": "点歌失败，请刷新歌单后重试。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)
