"""Native Qt lyrics widget — full-fidelity UI without WebView2 (option C).

Runs as its own process (main.py --widget): closing the window ends the
process and releases all memory; the controller keeps taskbar lyrics and
the tray alive. Data comes from MusicApi directly; blocking calls (network,
UIA bridge) run on worker threads and marshal results back via Qt signals.
"""

import sys
import logging
import threading
import time
from io import BytesIO

import requests
from PIL import Image, ImageFilter

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPixmap,
                           QTextCharFormat, QTextCursor, QFontMetrics)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QSlider, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from core.desktop import set_dpi_aware
from core.config import _data_dir
from widgets.music.api import MusicApi

logger = logging.getLogger("bizhi.widget")

BG = "#14161c"
PANEL = "#1b1e26"
LINE = "#2a2e38"
ACCENT = "#f0b35a"
TEXT = "#e8eaf0"
MUTED = "#8a8f9c"
DANGER = "#e06c75"

HWND_FILE = _data_dir() / "widget_hwnd"

QSS = f"""
#root {{
    background: rgba(20, 22, 28, 0.94);
    border-radius: 12px;
}}
#header {{ background: transparent; }}
#appTitle {{ color: {MUTED}; font-size: 10pt; font-weight: bold; }}
#statusDot {{
    background: {MUTED}; border-radius: 4px; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}}
#statusDot[state="playing"] {{ background: #4ade80; }}
#statusDot.error {{ background: {DANGER}; }}
#statusLabel {{ color: {MUTED}; font-size: 9pt; }}
#tabBar {{ background: transparent; }}
QPushButton[tab="true"] {{
    background: transparent; color: {MUTED}; border: 0; border-radius: 5px;
    padding: 5px 9px; font-size: 10pt; font-weight: bold; text-align: left;
}}
QPushButton[tab="true"]:hover {{ color: {TEXT}; }}
QPushButton[tab="true"]:checked {{ color: {ACCENT}; background: rgba(240,179,90,0.12); }}
#busyLabel {{ color: {MUTED}; font-size: 8pt; }}
#playingTag {{ color: {ACCENT}; font-size: 9pt; }}
#songTitle {{ color: {TEXT}; font-size: 14pt; font-weight: bold; }}
#songArtist, #songAlbum {{ color: {MUTED}; font-size: 9pt; }}
#cover {{ border-radius: 8px; background: {PANEL}; }}
QListWidget {{
    background: {BG}; border: 1px solid {LINE}; border-radius: 6px;
    font-size: 10pt; outline: 0;
}}
QListWidget::item {{ padding: 7px 6px; color: {TEXT}; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.05); }}
QListWidget::item:selected {{ background: rgba(240,179,90,0.14); color: {ACCENT}; }}
QTextBrowser {{
    background: {BG}; border: none; color: {TEXT}; font-size: 11pt;
}}
#searchEntry {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px;
    padding: 6px 10px; color: {TEXT}; font-size: 10pt; selection-background-color: {ACCENT};
}}
#searchBtn, .action-btn {{
    background: {ACCENT}; color: #20242c; border: 0; border-radius: 6px;
    padding: 6px 12px; font-size: 10pt; font-weight: bold;
}}
#searchBtn:hover, .action-btn:hover {{ background: #ffd08a; }}
 QPushButton.flat {{
    background: {PANEL}; color: {TEXT}; border: 0; border-radius: 6px;
    padding: 6px 10px; font-size: 9pt;
}}
QPushButton[kind="flat"]:hover {{ color: {ACCENT}; }}
QPushButton[kind="flat"]:disabled {{ color: {MUTED}; background: {PANEL}; }}
#posLabel, #durLabel {{ color: {MUTED}; font-size: 9pt; }}
QSlider::groove:horizontal {{
    height: 4px; background: {LINE}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 12px; height: 12px; margin: -5px 0;
    border-radius: 6px;
}}
#offsetBtn {{
    background: transparent; color: {MUTED}; border: 0; padding: 2px 4px;
    font-size: 9pt;
}}
#offsetBtn:hover {{ color: {ACCENT}; }}
#offsetBtn:checked {{ color: {ACCENT}; font-weight: bold; }}
#closeBtn {{ color: {MUTED}; font-size: 12pt; background: transparent; border: 0; }}
#closeBtn:hover {{ color: {DANGER}; }}
#gearBtn {{ color: {MUTED}; font-size: 12pt; background: transparent; border: 0; }}
#gearBtn:hover {{ color: {TEXT}; }}
#settingsTitle {{ color: {TEXT}; font-size: 12pt; font-weight: bold; }}
#settingsHint {{ color: {MUTED}; font-size: 8pt; }}
QDialog {{ background: {BG}; color: {TEXT}; }}
QCheckBox {{ color: {TEXT}; font-size: 10pt; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {LINE}; border-radius: 4px;
    background: {PANEL};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""


class Signals(QObject):
    done = Signal(object)


def _rounded_pixmap(img: Image.Image, size: int, radius: int) -> QPixmap:
    img = img.convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    img.putalpha(mask)
    return QPixmap.fromImage(img)


def _backdrop_pixmap(img: Image.Image, w: int, h: int) -> QPixmap:
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height) * 1.2
    img = img.resize((int(img.width * scale), int(img.height * scale)))
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h)).filter(ImageFilter.GaussianBlur(24))
    img = Image.blend(img, Image.new("RGB", img.size, (10, 11, 14)), 0.72)
    img = img.convert("RGBA")
    from PIL import ImageDraw
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width, img.height), radius=16, fill=255)
    img.putalpha(mask)
    return QPixmap.fromImage(img)


class QtWidget:
    """The lyrics widget window (QMainWindow)."""

    def __init__(self, config: dict):
        self.config = config
        self.power_saving = bool(config.get("power_saving", False))
        self.api = MusicApi(initial_offset_ms=config.get("music_offset_ms", 0))
        self.signals = Signals()
        self.signals.done.connect(self._on_worker_done)

        self.app = QApplication.instance()
        set_dpi_aware()
        self.win = QMainWindow()
        self.win.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # Transparent top-level windows can briefly expose the unpainted
        # backing store as a white surface on Windows. Power-saving mode is
        # documented as opaque, so apply that choice before the native window
        # is created/shown.
        self.win.setAttribute(Qt.WA_TranslucentBackground, not self.power_saving)
        self.win.resize(380, 720)
        pos = config.get("widgets", {}).get("music") or {}
        self.win.move(int(pos.get("x", 1480)), int(pos.get("y", 100)))

        self._drag = None
        self._sync_point = None
        self._lyrics = []
        self._current_index = -1
        self._seeking = False
        self._queue_page = None
        self._mine_playlist = None
        self._mine_begin = 0
        self._mine_has_more = False
        self._search_results = []
        self._cover_cache = {}
        self._cover_url_now = ""
        self._workers = []

        self._build()
        self._apply_power_saving(self.power_saving)

        self.sync_timer = QTimer(interval=1000, timeout=self._sync_tick)
        self.sync_timer.start()
        self.render_timer = QTimer(interval=200, timeout=self._position_tick)
        self.render_timer.start()
        self._sync_tick()

    # ── UI construction ──

    def _build(self):
        root = QWidget(objectName="root")
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.win.setCentralWidget(root)
        # Blurred/dimmed cover backdrop sits behind the whole layout.
        self.backdrop = QLabel(root)
        self.backdrop.setGeometry(0, 0, self.win.width(), self.win.height())
        self.backdrop.setScaledContents(True)
        self.backdrop.lower()
        column = QVBoxLayout(root)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(6)

        # header
        header = QHBoxLayout(objectName="header")
        header.addWidget(QLabel("♪ QQ 音乐歌词", objectName="appTitle"))
        dot = QLabel(objectName="statusDot")
        dot.setFixedSize(8, 8)
        header.addWidget(dot)
        self.status_label = QLabel("待机", objectName="statusLabel")
        header.addWidget(self.status_label)
        header.addStretch(1)
        gear = QPushButton("⚙", objectName="gearBtn", clicked=self._open_settings)
        gear.setCursor(Qt.PointingHandCursor)
        header.addWidget(gear)
        close = QPushButton("✕", objectName="closeBtn", clicked=self.win.close)
        close.setCursor(Qt.PointingHandCursor)
        header.addWidget(close)
        column.addLayout(header)

        # song info
        info = QHBoxLayout()
        info.setSpacing(12)
        self.cover = QLabel(objectName="cover")
        self.cover.setFixedSize(96, 96)
        self.cover.setAlignment(Qt.AlignCenter)
        info.addWidget(self.cover, 0, Qt.AlignTop)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self.playing_tag = QLabel("", objectName="playingTag")
        self.song_title = QLabel("等待播放", objectName="songTitle")
        self.song_title.setWordWrap(True)
        self.song_artist = QLabel("", objectName="songArtist")
        self.song_album = QLabel("", objectName="songAlbum")
        for w in (self.playing_tag, self.song_title, self.song_artist, self.song_album):
            info_col.addWidget(w)
        info_col.addStretch(1)
        info.addLayout(info_col, 1)
        column.addLayout(info)

        # tabs
        tabs = QHBoxLayout(objectName="tabBar")
        self.tab_group = QButtonGroup(self.win)
        self.tab_buttons = {}
        for i, (key, label) in enumerate((("lyrics", "歌词"), ("queue", "播放队列"),
                                          ("mine", "我的歌单"), ("search", "搜索"))):
            btn = QPushButton(label)
            btn.setProperty("tab", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._show_tab(k))
            self.tab_group.addButton(btn, i)
            self.tab_buttons[key] = btn
            tabs.addWidget(btn)
        tabs.addStretch(1)
        column.addLayout(tabs)

        self.busy_label = QLabel("", objectName="busyLabel")
        self.busy_label.setWordWrap(True)
        column.addWidget(self.busy_label)

        # stacked views
        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)

        self.lyrics_view = QTextBrowser()
        self.lyrics_view.setOpenExternalLinks(False)
        self.lyrics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack.addWidget(self.lyrics_view)

        self.queue_list = QListWidget()
        self.queue_list.itemDoubleClicked.connect(
            lambda item: self._play_queue_row(self.queue_list.row(item)))
        self.stack.addWidget(self.queue_list)
        queue_btns = QHBoxLayout()
        for text, direction in (("上一页", "previous"), ("回到开头", "first"), ("下一页", "next")):
            btn = QPushButton(text)
            btn.setProperty("kind", "flat")
            btn.setProperty("class", "flat")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, d=direction: self._load_queue(d))
            queue_btns.addWidget(btn, 1)
        self.stack.addWidget(self._wrap_bottom(self.queue_list, queue_btns))

        self.mine_list = QListWidget()
        self.mine_list.itemDoubleClicked.connect(
            lambda item: self._mine_activate(self.mine_list.row(item)))
        self.stack.addWidget(self.mine_list)
        self.mine_back = QPushButton("‹ 歌单列表")
        self.mine_back.setProperty("kind", "flat")
        self.mine_back.setCursor(Qt.PointingHandCursor)
        self.mine_back.clicked.connect(self._load_mine_playlists)
        self.mine_prev = QPushButton("上一页")
        self.mine_prev.setProperty("kind", "flat")
        self.mine_prev.setCursor(Qt.PointingHandCursor)
        self.mine_prev.clicked.connect(lambda: self._mine_page(-1))
        self.mine_next = QPushButton("下一页")
        self.mine_next.setProperty("kind", "flat")
        self.mine_next.setCursor(Qt.PointingHandCursor)
        self.mine_next.clicked.connect(lambda: self._mine_page(1))
        mine_btns = QHBoxLayout()
        mine_btns.addWidget(self.mine_back, 1)
        mine_btns.addWidget(self.mine_prev, 1)
        mine_btns.addWidget(self.mine_next, 1)
        self.stack.addWidget(self._wrap_bottom(self.mine_list, mine_btns))

        search_row = QHBoxLayout()
        self.search_entry = _SearchEntry()
        self.search_entry.setPlaceholderText("搜索歌曲或歌手")
        self.search_entry.returnPressed.connect(self._do_search)
        search_row.addWidget(self.search_entry, 1)
        search_btn = QPushButton("搜索", objectName="searchBtn",
                                 clicked=self._do_search)
        search_btn.setCursor(Qt.PointingHandCursor)
        search_row.addWidget(search_btn)
        self.search_page = QWidget()
        search_layout = QVBoxLayout(self.search_page)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.addLayout(search_row)
        self.search_list = QListWidget()
        self.search_list.itemDoubleClicked.connect(
            lambda item: self._search_pick(self.search_list.row(item)))
        search_layout.addWidget(self.search_list, 1)
        self.stack.addWidget(self.search_page)

        # progress + transport
        progress = QHBoxLayout()
        self.pos_label = QLabel("0:00", objectName="posLabel")
        progress.addWidget(self.pos_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._seek_commit)
        self.slider.sliderMoved.connect(self._seek_preview)
        progress.addWidget(self.slider, 1)
        self.dur_label = QLabel("0:00", objectName="durLabel")
        progress.addWidget(self.dur_label)
        column.addLayout(progress)

        transport = QHBoxLayout()
        for glyph, handler in (("⏮", lambda: self._control("previous")),
                               ("⏯", lambda: self._control("toggle")),
                               ("⏭", lambda: self._control("next"))):
            btn = QPushButton(glyph)
            btn.setProperty("kind", "flat")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(64, 34)
            btn.clicked.connect(handler)
            transport.addWidget(btn, 1)
        column.addLayout(transport)

        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("歌词偏移", objectName="statusLabel"))
        self.offset_buttons = []
        for text, ms in (("-0.5", -500), ("-0.1", -100), ("0", 0), ("+0.1", 100), ("+0.5", 500)):
            btn = QPushButton(text, objectName="offsetBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=ms: self._set_offset(m))
            self.offset_buttons.append(btn)
            offset_row.addWidget(btn)
        offset_row.addStretch(1)
        column.addLayout(offset_row)

        self._show_tab("lyrics")
        self.tab_buttons["lyrics"].setChecked(True)

    def _wrap_bottom(self, list_widget, buttons):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(list_widget, 1)
        layout.addLayout(buttons)
        return page

    # ── tabs / busy ──

    def _show_tab(self, name):
        index = {"lyrics": 0, "queue": 1, "mine": 2, "search": 3}[name]
        self.stack.setCurrentIndex(index)
        for key, btn in self.tab_buttons.items():
            btn.setChecked(key == name)
        if name == "queue" and not self._queue_page:
            self._load_queue("refresh")
        if name == "mine" and self.mine_list.count() == 0:
            self._load_mine_playlists()

    def _set_busy(self, text=""):
        self.busy_label.setText(text)

    def _worker(self, fn, on_done):
        """Run a blocking call on a worker thread; deliver via signal."""

        def run():
            try:
                result = fn()
            except Exception as e:
                result = {"error": str(e)}
            self.signals.done.emit((on_done, result))

        thread = threading.Thread(target=run, daemon=True)
        self._workers.append(thread)
        thread.start()

    def _on_worker_done(self, payload):
        on_done, result = payload
        on_done(result)

    # ── sync / rendering ──

    def _sync_tick(self):
        snap = self.api.snapshot()
        playing = snap.get("is_playing")
        dot = self.win.findChild(QLabel, "statusDot")
        if dot:
            dot.setProperty("state", "playing" if playing else "idle")
            dot.style().unpolish(dot)
            dot.style().polish(dot)
        self.status_label.setText("播放中" if playing else
                                  ("已暂停" if snap.get("title") else "待机"))
        self.playing_tag.setText("正在播放" if playing else "")
        if snap.get("title") != self.song_title.text():
            self.song_title.setText(snap.get("title") or "等待播放")
            self.song_artist.setText(snap.get("artist") or "")
            self.song_album.setText(snap.get("album") or "")
            self._fetch_cover(snap.get("cover_url"))
        if snap.get("lyrics") is not self._lyrics:
            self._lyrics = snap.get("lyrics") or []
            self._render_lyrics()
        self._sync_point = {
            "position_ms": snap.get("position_ms", 0),
            "duration_ms": snap.get("duration_ms", 0),
            "is_playing": playing,
            "at": time.monotonic(),
        }
        self._highlight_queue(snap)

    def _render_lyrics(self):
        self.lyrics_view.clear()
        self._current_index = -1
        if not self._lyrics:
            self.lyrics_view.setHtml(
                '<div style="color:#8a8f9c; text-align:center; margin-top:60px">'
                "当前歌曲暂无可用歌词<br><span style='font-size:8pt'>播放控制和歌单仍可使用。</span>"
                "</div>")
            return
        html = []
        for entry in self._lyrics:
            line = entry.get("text", "").replace("<", "&lt;")
            html.append(f"<p style='margin:6px 0'><span class='l'>{line}</span>")
            trans = entry.get("trans", "")
            if trans:
                html.append(f"<br><span style='color:{MUTED}; font-size:8pt'>"
                            f"{trans.replace('<', '&lt;')}</span>")
            html.append("</p>")
        self.lyrics_view.setHtml("".join(html))

    def _highlight_lyrics(self, adjusted_ms):
        idx = -1
        for i, entry in enumerate(self._lyrics):
            if adjusted_ms >= entry.get("time_ms", 0):
                idx = i
            else:
                break
        if idx == self._current_index:
            return
        self._current_index = idx
        cursor = QTextCursor(self.lyrics_view.document().findBlockByNumber(idx))
        if idx >= 0:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(ACCENT))
            f = QFont(self.lyrics_view.font())
            f.setBold(True)
            fmt.setFont(f)
            sel = QTextBrowser.ExtraSelection()
            sel.format = fmt
            sel.cursor = cursor
            self.lyrics_view.setExtraSelections([sel])
            self.lyrics_view.setTextCursor(cursor)
        else:
            self.lyrics_view.setExtraSelections([])

    def _highlight_queue(self, snap):
        playing = (snap.get("media_title", ""), snap.get("media_artist", ""))
        for row in range(self.queue_list.count()):
            item = self.queue_list.item(row)
            data = item.data(Qt.UserRole) or ("", "")
            match = (data[0].casefold().replace(" ", "")
                     == playing[0].casefold().replace(" ", "")
                     and data[1].casefold().replace(" ", "")
                     == playing[1].casefold().replace(" ", ""))
            item.setForeground(QColor(ACCENT) if match else QColor(TEXT))

    def _position_tick(self):
        sync = self._sync_point
        if not sync:
            return
        pos = sync.get("position_ms", 0)
        if sync.get("is_playing") and not self._seeking:
            pos += int((time.monotonic() - sync.get("at", time.monotonic())) * 1000)
        pos = max(0, min(pos, sync.get("duration_ms") or pos))
        if not self._seeking:
            self.slider.blockSignals(True)
            if sync.get("duration_ms"):
                self.slider.setValue(int(pos / sync["duration_ms"] * 1000))
            self.slider.blockSignals(False)
            self.pos_label.setText(self._fmt(pos))
        offset = self.api._lrc_offset + self.api._user_offset
        self._highlight_lyrics(pos - offset)

    def _fmt(self, ms):
        if not ms or ms < 0:
            ms = 0
        return f"{int(ms) // 60000}:{int(ms) // 1000 % 60:02d}"

    # ── cover ──

    def _fetch_cover(self, url):
        if not url:
            self.cover.clear()
            return
        cached = self._cover_cache.get(url)
        if cached:
            self._apply_cover(cached)
            return

        def run():
            data = requests.get(url, timeout=8).content
            img = Image.open(BytesIO(data))
            self._cover_cache[url] = img
            self.signals.done.emit((lambda img=img: self._apply_cover(img), img))
        threading.Thread(target=run, daemon=True).start()

    def _apply_cover(self, img):
        size = int(96 * 1.0)
        pm = _rounded_pixmap(img, size, 10)
        self.cover.setPixmap(pm)
        if not self.power_saving:
            backdrop = _backdrop_pixmap(img, self.win.width(), self.win.height())
            self.backdrop.setPixmap(backdrop)

    # ── transport ──

    def _control(self, action):
        self._worker(lambda: self.api.control_playback(action), lambda r: None)

    def _set_offset(self, ms):
        def done(result):
            if not result.get("error"):
                self.api._user_offset = result.get("user_offset", self.api._user_offset)
        self._worker(lambda: self.api.set_user_offset(ms), done)

    def _seek_preview(self, value):
        sync = self._sync_point or {}
        duration = sync.get("duration_ms", 0)
        if duration:
            self.pos_label.setText(self._fmt(value / 1000 * duration))

    def _seek_commit(self):
        sync = self._sync_point or {}
        duration = sync.get("duration_ms", 0)
        self._seeking = False
        if not duration:
            return
        target = int(self.slider.value() / 1000 * duration)
        self._worker(lambda: self.api.control_playback("seek", target), lambda r: None)

    # ── queue tab ──

    def _load_queue(self, direction):
        def run():
            self.win.lower()
            try:
                return self.api.get_qq_playlist(direction)
            finally:
                self.win.raise_()
                self.win.activateWindow()
        def done(page):
            if page.get("error"):
                self._set_busy(page["error"])
                return
            self._queue_page = page
            self.queue_list.clear()
            for item in page.get("items", []):
                list_item = QListWidgetItem(f"{item['title']} — {item['artist']}")
                list_item.setData(Qt.UserRole, (item["title"], item["artist"]))
                self.queue_list.addItem(list_item)
            self._set_busy(f"共 {page.get('total', '?')} 首 · 本页 "
                           f"{len(page.get('items', []))} 首 · 双击播放")
        self._worker(run, done)

    def _play_queue_row(self, row):
        page = self._queue_page
        if not page or row >= len(page.get("items", [])):
            return
        item = page["items"][row]
        self._set_busy(f"正在切换到「{item['title']}」…")

        def run():
            self.win.lower()
            try:
                return self.api.play_qq_playlist_item(item["id"], page["page_token"])
            finally:
                self.win.raise_()
                self.win.activateWindow()

        def done(result):
            if result.get("error"):
                self._set_busy(result["error"])
            else:
                self._set_busy(f"已切换到「{result['title']}」。")
        self._worker(run, done)

    # ── mine tab ──

    def _load_mine_playlists(self):
        self._mine_playlist = None
        self.mine_back.setEnabled(False)
        self.mine_prev.setEnabled(False)
        self.mine_next.setEnabled(False)
        def done(data):
            if data.get("error"):
                self._set_busy(data["error"])
                return
            self.mine_list.clear()
            for pl in data.get("playlists", []):
                self.mine_list.addItem(f"{pl['name']} ({pl['song_num']} 首)")
            self._set_busy("双击歌单浏览歌曲。")
        self._worker(self.api.get_qq_account_playlists, done)

    def _mine_activate(self, row):
        if self._mine_playlist is None:
            def done(data):
                if data.get("error"):
                    self._set_busy(data["error"])
                    return
                playlists = data.get("playlists", [])
                if row < len(playlists):
                    self._open_mine_playlist(playlists[row], 0)
            self._worker(self.api.get_qq_account_playlists, done)
        else:
            def done(page):
                if page.get("error"):
                    self._set_busy(page["error"])
                    return
                songs = page.get("songs", [])
                if row < len(songs):
                    self._play_mine_song(songs[row])
            self._worker(lambda: self.api.get_qq_account_playlist_songs(
                self._mine_playlist["tid"], self._mine_begin, 30), done)

    def _open_mine_playlist(self, playlist, begin=0):
        self._mine_playlist = playlist
        self._mine_begin = begin
        self.mine_back.setEnabled(True)
        self._set_busy(f"正在读取「{playlist['name']}」…")
        def done(page):
            if page.get("error"):
                self._set_busy(page["error"])
                return
            self._mine_has_more = page.get("has_more", False)
            self.mine_list.clear()
            for song in page.get("songs", []):
                self.mine_list.addItem(f"{song['songname']} — {song['singer']}")
            self.mine_prev.setEnabled(begin > 0)
            self.mine_next.setEnabled(self._mine_has_more)
            self._set_busy(f"「{playlist['name']}」双击歌曲播放。")
        self._worker(lambda: self.api.get_qq_account_playlist_songs(
            playlist["tid"], begin, 30), done)

    def _mine_page(self, direction):
        if self._mine_playlist:
            self._open_mine_playlist(self._mine_playlist,
                                     max(0, self._mine_begin + direction * 30))

    def _play_mine_song(self, song):
        self._set_busy(f"正在让 QQ 音乐播放「{song['songname']}」…")
        def run():
            self.win.lower()
            try:
                return self.api.play_qq_account_song(song["songname"], song["singer"])
            finally:
                self.win.raise_()
                self.win.activateWindow()
        def done(result):
            if result.get("error"):
                self._set_busy(result["error"])
            else:
                self._set_busy(f"已在 QQ 音乐播放「{result['title']}」。")
        self._worker(run, done)

    # ── search tab ──

    def _do_search(self):
        keyword = self.search_entry.text().strip()
        if not keyword:
            return
        self._set_busy("正在搜索…")
        def done(results):
            if isinstance(results, dict):
                self._set_busy(results.get("error", "搜索失败"))
                return
            self._search_results = results
            self.search_list.clear()
            for song in results:
                self.search_list.addItem(f"{song['songname']} — {song['singer']}")
            self._set_busy("双击结果加载歌词。")
        self._worker(lambda: self.api.search(keyword), done)

    def _search_pick(self, row):
        if row >= len(self._search_results):
            return
        song = self._search_results[row]
        self._set_busy("正在加载歌词…")
        def done(data):
            if data.get("error"):
                self._set_busy(data["error"])
                return
            self._set_busy("")
            self._show_tab("lyrics")
        self._worker(lambda: self.api.load_lyrics(
            song["songmid"], song["songname"], song["singer"],
            song.get("albumname", ""), song.get("cover_url", "")), done)

    # ── settings ──

    def _open_settings(self):
        dialog = QDialog(self.win)
        dialog.setWindowTitle("设置")
        dialog.setFixedWidth(300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("设置", objectName="settingsTitle"))
        power = self.power_saving
        power_cb = QCheckBox("省电模式（降低刷新频率）")
        power_cb.setChecked(power)
        layout.addWidget(power_cb)
        hint = QLabel("窗口不透明需重启挂件生效。", objectName="settingsHint")
        layout.addWidget(hint)
        autostart_cb = QCheckBox("开机自启动")
        autostart_cb.setChecked(self.api.get_autostart().get("enabled", False))
        layout.addWidget(autostart_cb)

        def on_power():
            self.power_saving = power_cb.isChecked()
            self._apply_power_saving(self.power_saving)
            self._worker(lambda: self.api.set_power_saving(self.power_saving),
                         lambda r: None)
        power_cb.toggled.connect(on_power)

        def on_autostart():
            self._worker(lambda: self.api.set_autostart(autostart_cb.isChecked()),
                         lambda r: None)
        autostart_cb.toggled.connect(on_autostart)

        dialog.exec()

    def _apply_power_saving(self, enabled):
        self.power_saving = enabled
        if getattr(self, "sync_timer", None):
            self.sync_timer.setInterval(1000 if enabled else 500)
            self.render_timer.setInterval(500 if enabled else 200)

    # ── window behaviours ──

    def closeEvent(self, event):
        event.accept()   # closing the window exits this process on purpose


class _SearchEntry(QLineEdit):
    def __init__(self):
        super().__init__(objectName="searchEntry")


class NativeWidgetApp:
    """Owns the QApplication and the widget window."""

    def __init__(self, config: dict):
        self.config = config

    def run(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        stylesheet = QSS
        if self.config.get("power_saving", False):
            stylesheet = stylesheet.replace(
                "background: rgba(20, 22, 28, 0.94);",
                f"background: {BG};",
                1,
            )
        app.setStyleSheet(stylesheet)
        set_dpi_aware()
        self.widget = QtWidget(self.config)
        self.win = self.widget.win
        # Finish constructing and populating the UI before the first paint.
        # This avoids exposing an empty/white frame during startup.
        self.win.show()
        try:
            HWND_FILE.parent.mkdir(parents=True, exist_ok=True)
            HWND_FILE.write_text(str(int(self.win.winId())), encoding="utf-8")
        except Exception:
            pass
        app.aboutToQuit.connect(lambda: HWND_FILE.unlink(missing_ok=True))
        app.exec()
        logger.info("BIZHI widget process exited")


def run(config: dict):
    set_dpi_aware()
    app = NativeWidgetApp(config)
    app.run()
