"""Bridge QQ Music's actual, virtualized playback queue through Windows UIA.

Each response is a page from the client, not a search result or a saved history.
Playback targets are resolved again before input; stale pages cannot select a
different song by reusing a former row index or coordinate.

Verified against the real client: the queue is an independent top-level window
titled 播放队列. The client keeps its UIA tree alive while the panel is hidden,
but only real mouse input (button click, row double-click, wheel) reaches the
shown panel, so every hit-tested point must resolve to the queue window itself.
"""

import ctypes
from ctypes import wintypes as wt
import hashlib
import logging
import re
import threading
import time
import uuid

logger = logging.getLogger(__name__)
BADGES = {"力", "劥", "劦", "MV", "VIP", "试听", "臻品母带"}
QUEUE_TITLE = "播放队列"
MAIN_CLASS = "TXGuiFoundation"


class QueueError(RuntimeError):
    pass


# One client, one input stream: every bridge operation on the client's UI
# shares this lock so queue and playlist reads cannot interleave.
_CLIENT_LOCK = threading.Lock()


def _is_main_window(title, cls):
    return (cls == MAIN_CLASS and title and title != QUEUE_TITLE
            and title != "TXMenuWindow"
            and "Dummy" not in title and "歌词" not in title and "Lyric" not in title)


class QQQueueBridge:
    def __init__(self):
        self._lock = _CLIENT_LOCK
        self._page_token = ""
        self._items = {}
        self._uia = None
        self._queue_hwnd = None

    def _initialize(self):
        if self._uia is not None:
            return
        import clr
        # These framework assemblies are in the Windows GAC, not beside the
        # interpreter. A simple name does not resolve them in pythonnet.
        for assembly in ("WindowsBase", "UIAutomationTypes", "UIAutomationClient"):
            clr.AddReference(
                f"{assembly}, Version=4.0.0.0, Culture=neutral, "
                "PublicKeyToken=31bf3856ad364e35"
            )
        from System import IntPtr
        from System.Diagnostics import Process
        from System.Windows.Automation import (
            AutomationElement, AndCondition, Condition, ControlType,
            PropertyCondition, ScrollAmount, ScrollPattern,
            TreeScope, TreeWalker,
        )
        self._uia = {
            "Element": AutomationElement, "And": AndCondition,
            "Condition": Condition, "Type": ControlType,
            "Property": PropertyCondition,
            "Scroll": ScrollPattern, "Amount": ScrollAmount,
            "Scope": TreeScope, "Walker": TreeWalker.ControlViewWalker,
            "IntPtr": IntPtr, "Process": Process,
        }
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.GetForegroundWindow.restype = wt.HWND
        self._user32.SetForegroundWindow.argtypes = [wt.HWND]
        self._user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self._user32.IsWindowVisible.argtypes = [wt.HWND]
        self._user32.IsIconic.argtypes = [wt.HWND]
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        self._user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self._user32.GetWindowTextLengthW.argtypes = [wt.HWND]
        self._user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self._user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self._user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self._user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self._user32.WindowFromPoint.argtypes = [wt.POINT]
        self._user32.WindowFromPoint.restype = wt.HWND
        self._user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
        self._user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
        self._user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _bring_to_front(self, hwnd):
        """Raise the client window from a background process.

        Windows denies SetForegroundWindow to processes that do not own the
        foreground; an Alt tap lifts the lock, and attaching to the current
        foreground thread's input queue is the fallback.
        """
        user32 = self._user32
        if user32.GetForegroundWindow() == hwnd:
            return
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        if user32.GetForegroundWindow() == hwnd:
            return
        fg = user32.GetForegroundWindow()
        pid = wt.DWORD()
        fg_thread = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        this_thread = self._kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != this_thread:
            user32.AttachThreadInput(this_thread, fg_thread, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(this_thread, fg_thread, False)

    def _all(self, element, children=False):
        u = self._uia
        return list(element.FindAll(
            u["Scope"].Children if children else u["Scope"].Descendants,
            u["Condition"].TrueCondition,
        ))

    def _find(self, root, name, kind):
        u = self._uia
        condition = u["And"](
            u["Property"](u["Element"].NameProperty, name),
            u["Property"](u["Element"].ControlTypeProperty, kind),
        )
        # The first QQ root pane hosts a huge hidden web subtree. Native player
        # controls belong to later sibling panes, so search those first.
        children = self._all(root, children=True)
        for child in reversed(children[1:]):
            if child.Current.IsOffscreen:
                continue
            match = child.FindFirst(u["Scope"].Descendants, condition)
            if match is not None:
                return match
        return None

    def _titled_windows(self, match):
        """Top-level windows of the client whose title/class satisfy match."""
        pids = {int(p.Id) for p in self._uia["Process"].GetProcessesByName("QQMusic")}
        if not pids:
            raise QueueError("请先打开 QQ 音乐客户端，再刷新歌单。")
        found = []
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def visit(hwnd, _):
            pid = wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            size = self._user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(size + 1)
            self._user32.GetWindowTextW(hwnd, title, size + 1)
            cls = ctypes.create_unicode_buffer(64)
            self._user32.GetClassNameW(hwnd, cls, 64)
            if match(title.value, cls.value):
                found.append(hwnd)
            return True

        callback = callback_type(visit)
        self._user32.EnumWindows.argtypes = [callback_type, wt.LPARAM]
        self._user32.EnumWindows(callback, 0)
        return found

    def _main_window(self, show=True):
        """Locate the main window; restore/foreground it when show is set.

        A minimized client collapses to a tiny rect, so iconic windows are
        candidates on their own instead of the area threshold.
        """
        sized, minimized = [], []
        for hwnd in self._titled_windows(_is_main_window):
            if self._user32.IsIconic(hwnd):
                minimized.append(hwnd)
                continue
            rect = wt.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area > 100000:
                sized.append((area, hwnd))
        if sized:
            hwnd = max(sized)[1]
        elif minimized:
            hwnd = minimized[0]
        else:
            raise QueueError("找不到 QQ 音乐主窗口，请切回客户端的完整模式。")
        if show:
            self._user32.ShowWindow(hwnd, 9 if self._user32.IsIconic(hwnd) else 5)
            self._bring_to_front(hwnd)
            time.sleep(0.3)
        logger.info("QQ queue: main window %s", hwnd)
        element = self._uia["Element"].FromHandle(self._uia["IntPtr"](hwnd))
        return element, hwnd

    def _queue_window(self):
        hwnds = self._titled_windows(lambda title, cls: title == QUEUE_TITLE)
        return hwnds[0] if hwnds else None

    def _panel_shown(self, queue_hwnd):
        """True when the queue window is the actual hit-test target on screen.

        The client keeps the panel's UIA tree alive while hidden, so readable
        rows prove nothing; only the hit test does.
        """
        element = self._uia["Element"].FromHandle(self._uia["IntPtr"](queue_hwnd))
        rect = element.Current.BoundingRectangle
        if rect.IsEmpty:
            return False
        point = wt.POINT(int(rect.X + rect.Width / 2), int(rect.Y + rect.Height / 2))
        return self._user32.WindowFromPoint(point) == queue_hwnd

    def _ensure_queue(self):
        """Return the queue element with its panel genuinely shown on screen."""
        queue_hwnd = self._queue_window()
        if queue_hwnd and self._panel_shown(queue_hwnd):
            self._queue_hwnd = queue_hwnd
            return self._uia["Element"].FromHandle(self._uia["IntPtr"](queue_hwnd))
        main, main_hwnd = self._main_window(show=True)
        button = self._find(main, QUEUE_TITLE, self._uia["Type"].Button)
        if button is None:
            raise QueueError("此 QQ 音乐版本未提供播放队列入口，请在客户端手动打开播放队列。")
        self._click(button, main_hwnd)
        for _ in range(12):
            time.sleep(0.15)
            queue_hwnd = self._queue_window()
            if queue_hwnd and self._panel_shown(queue_hwnd):
                self._queue_hwnd = queue_hwnd
                return self._uia["Element"].FromHandle(self._uia["IntPtr"](queue_hwnd))
        raise QueueError("无法打开 QQ 音乐播放队列，请移开遮挡的窗口或手动打开后重试。")

    def _pointer(self, element, target_hwnd):
        rect = element.Current.BoundingRectangle
        if rect.IsEmpty or element.Current.IsOffscreen:
            raise QueueError("歌曲项已移出客户端可见区域，请刷新歌单。")
        x, y = int(rect.X + rect.Width / 2), int(rect.Y + rect.Height / 2)
        # The client window fades in on activation; the hit test can lag.
        for attempt in range(3):
            self._user32.SetCursorPos(x, y)
            time.sleep(0.1)
            if self._user32.WindowFromPoint(wt.POINT(x, y)) == target_hwnd:
                break
            if attempt == 2:
                raise QueueError("QQ 音乐窗口未显示在最前，请移开遮挡的窗口后重试。")
            self._bring_to_front(target_hwnd)
            time.sleep(0.5)

    def _click(self, element, target_hwnd, double=False):
        # UIA Invoke reaches this client's controls but does not open the queue
        # panel or start a song; only real input does.
        self._pointer(element, target_hwnd)
        for _ in range(2 if double else 1):
            self._user32.mouse_event(0x2, 0, 0, 0, 0)
            self._user32.mouse_event(0x4, 0, 0, 0, 0)
            time.sleep(0.06)

    def _rows(self, queue):
        u = self._uia
        rows, seen = [], set()
        bounds = queue.Current.BoundingRectangle
        for element in self._all(queue):
            if element.Current.ControlType != u["Type"].Text:
                continue
            # Queue rows expose a title pane followed by a direct artist text.
            metadata = u["Walker"].GetParent(element)
            children = self._all(metadata, children=True)
            artists = [c for c in children if c.Current.ControlType == u["Type"].Text]
            panes = [c for c in children if c.Current.ControlType == u["Type"].Pane]
            if len(artists) != 1 or len(panes) != 1:
                continue
            title_elements = [c for c in self._all(panes[0])
                              if c.Current.ControlType == u["Type"].Text and c.Current.Name
                              and c.Current.Name not in BADGES]
            if not title_elements:
                continue
            title_element = title_elements[0]
            rect = title_element.Current.BoundingRectangle
            if title_element.Current.IsOffscreen or rect.IsEmpty:
                continue
            if rect.Top < bounds.Top + 75 or rect.Bottom > bounds.Bottom - 4:
                continue
            title, artist = str(title_element.Current.Name), str(artists[0].Current.Name)
            identity = tuple(metadata.GetRuntimeId())
            if identity in seen:
                continue
            seen.add(identity)
            rows.append({"title": title, "artist": artist, "element": title_element,
                         "top": float(rect.Top), "runtime_id": identity})
        return sorted(rows, key=lambda row: row["top"])

    def _wheel(self, x, y, notches, queue):
        self._user32.SetCursorPos(x, y)
        time.sleep(0.05)
        if self._user32.WindowFromPoint(wt.POINT(x, y)) != self._queue_hwnd:
            raise QueueError("QQ 音乐队列未显示在最前，请移开遮挡的窗口后重试。")
        self._user32.mouse_event(0x800, 0, 0, (120 * notches) & 0xFFFFFFFF, 0)
        time.sleep(0.3)
        return [(r["title"], r["artist"]) for r in self._rows(queue)]

    def _scroll(self, queue, direction):
        """Turn one page with the wheel; the client exposes no UIA scroll bar."""
        rows = self._rows(queue)
        if not rows:
            return
        anchor = rows[len(rows) // 2]["element"].Current.BoundingRectangle
        x, y = int(anchor.X + anchor.Width / 2), int(anchor.Y + anchor.Height / 2)
        if direction == "first":
            seen = [(r["title"], r["artist"]) for r in rows]
            for _ in range(60):
                current = self._wheel(x, y, 5, queue)
                if current == seen:
                    return
                seen = current
            return
        self._wheel(x, y, -2 if direction == "next" else 2, queue)

    def _count(self, queue):
        for element in self._all(queue):
            match = re.search(r"共\s*(\d+)\s*首", str(element.Current.Name))
            if match:
                return int(match.group(1))
        return 0

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
                queue = self._ensure_queue()
                logger.info("QQ queue: playback queue opened")
                before = [(r["title"], r["artist"]) for r in self._rows(queue)]
                if direction != "refresh":
                    self._scroll(queue, direction)
                rows = self._rows(queue)
                count = self._count(queue)
                if not rows and count:
                    raise QueueError("无法读取当前队列页，QQ 音乐的界面结构可能已变化。")
                self._page_token = uuid.uuid4().hex
                self._items = {}
                items = []
                for row in rows:
                    item_id = hashlib.sha256(repr(row["runtime_id"]).encode()).hexdigest()[:24]
                    self._items[item_id] = (row["title"], row["artist"])
                    items.append({"id": item_id, "title": row["title"], "artist": row["artist"]})
                unchanged = [(r["title"], r["artist"]) for r in rows] == before
                return {"items": items, "total": count, "page_token": self._page_token,
                        "at_start": direction == "first" or (direction == "previous" and unchanged),
                        "at_end": direction == "next" and unchanged}
            except QueueError as error:
                self._page_token, self._items = "", {}
                return {"error": str(error)}
            except Exception:
                self._page_token, self._items = "", {}
                logger.exception("QQ Music queue read failed")
                return {"error": "读取 QQ 音乐歌单失败，请确认客户端已打开且与挂件使用相同权限。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)

    @staticmethod
    def _norm(value):
        return "".join(str(value or "").casefold().split())

    def locate_and_play(self, title, artist, max_turns=75):
        """Scroll the queue from its top until the song shows, then play it.

        The queue holds the client's library, so this is the generic play
        path for a song the widget only knows by name.
        """
        with self._lock:
            previous, cursor = None, None
            try:
                self._initialize()
                previous = self._user32.GetForegroundWindow()
                point = wt.POINT()
                self._user32.GetCursorPos(ctypes.byref(point))
                cursor = (point.x, point.y)
                queue = self._ensure_queue()
                target = (self._norm(title), self._norm(artist))
                self._scroll(queue, "first")
                turns = 0
                while turns < max_turns:
                    rows = self._rows(queue)
                    matches = [r for r in rows
                               if (self._norm(r["title"]), self._norm(r["artist"])) == target]
                    if len(matches) == 1:
                        self._click(matches[0]["element"], self._queue_hwnd, double=True)
                        return {"ok": True, "title": title, "artist": artist}
                    if not rows:
                        break
                    before = [(r["title"], r["artist"]) for r in rows]
                    self._scroll(queue, "next")
                    turns += 1
                    rows = self._rows(queue)
                    if [(r["title"], r["artist"]) for r in rows] == before:
                        break   # queue end reached
                return {"error": "在客户端播放队列中扫描完毕，没有找到这首歌。"
                                 "它可能不在当前队列里，请先在客户端播放一次该歌曲。"}
            except QueueError as error:
                return {"error": str(error)}
            except Exception:
                logger.exception("QQ Music queue locate failed")
                return {"error": "在队列中定位歌曲失败，请重试。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)

    def play_item(self, item_id, page_token):
        with self._lock:
            if not page_token or page_token != self._page_token or item_id not in self._items:
                return {"error": "歌单页面已更新，请刷新后重新选择歌曲。"}
            previous, cursor = None, None
            try:
                self._initialize()
                previous = self._user32.GetForegroundWindow()
                point = wt.POINT()
                self._user32.GetCursorPos(ctypes.byref(point))
                cursor = (point.x, point.y)
                queue = self._ensure_queue()
                title, artist = self._items[item_id]
                matches = [r for r in self._rows(queue) if (r["title"], r["artist"]) == (title, artist)]
                if len(matches) != 1:
                    raise QueueError("客户端队列已滚动或改变，请刷新歌单后再点歌。")
                self._click(matches[0]["element"], self._queue_hwnd, double=True)
                return {"ok": True, "title": title, "artist": artist}
            except QueueError as error:
                return {"error": str(error)}
            except Exception:
                logger.exception("QQ Music queue selection failed")
                return {"error": "点歌失败，请刷新客户端歌单后重试。"}
            finally:
                if cursor:
                    self._user32.SetCursorPos(*cursor)
                if previous:
                    self._user32.SetForegroundWindow(previous)
