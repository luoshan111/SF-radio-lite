"""Native tkinter lyrics widget — the WebView2-free UI (option C).

Runs as its own process (main.py --widget) so closing the window releases
every byte it uses. Data comes from MusicApi directly (in-process, no
serialization); all blocking calls (network, UIA bridge) run on worker
threads and post results back through root.after.
"""

import ctypes
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont
from io import BytesIO

import requests
from PIL import Image, ImageTk

from core.config import _data_dir
from core.desktop import set_dpi_aware
from widgets.music.api import MusicApi

import logging
logger = logging.getLogger("bizhi.widget")

HWND_FILE = _data_dir() / "widget_hwnd"

BG = "#14161c"
PANEL = "#1b1e26"
LINE = "#262a33"
ACCENT = "#f0b35a"
TEXT = "#e8eaf0"
MUTED = "#8a8f9c"
DANGER = "#e06c75"

FONT = "Microsoft YaHei UI"


def run(config: dict):
    set_dpi_aware()
    widget = NativeWidget(config)
    try:
        widget.root.mainloop()
    finally:
        try:
            HWND_FILE.unlink()
        except OSError:
            pass
    # mainloop returns when the window is closed: the process exits and the
    # renderer memory is released. The controller keeps everything else alive.


class NativeWidget:
    def __init__(self, config: dict):
        self.config = config
        self.power_saving = bool(config.get("power_saving", False))
        self.api = MusicApi(initial_offset_ms=config.get("music_offset_ms", 0))

        self.root = tk.Tk()
        self.root.title("QQ 音乐歌词")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        # With per-monitor DPI awareness Tk geometry is physical pixels: scale
        # the fixed layout by the display factor so the card keeps its size.
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        w, h = int(380 * self.scale), int(720 * self.scale)
        x = int((config.get("widgets", {}).get("music") or {}).get("x", 1480))
        y = int((config.get("widgets", {}).get("music") or {}).get("y", 100))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.report_callback_exception = self._on_tk_error

        self._build()
        self._setup_native_window()
        self._make_draggable(self.header)
        self._make_draggable(self.info_frame)

        self._cover_cache = {}
        self._sync_point = None          # {position_ms, at, duration_ms, is_playing}
        self._lyrics = []
        self._line_map = []              # lyric index -> Text line number
        self._current_index = -1
        self._seeking = False
        self._queue_page = None          # last queue read_page result
        self._mine_playlist = None       # selected account playlist
        self._mine_begin = 0
        self._mine_has_more = False
        self._search_results = []
        self._mine_songs_cache = []
        self._mine_playlists_cache = []
        self._busy = False

        self._show_tab("lyrics")
        self.root.after(60, self._setup_native_window)
        self.root.after(self._sync_interval(), self._sync_tick)
        self.root.after(200, self._position_tick)

    # ── window chrome ──

    def _build(self):
        f = tkfont.Font(family=FONT, size=10)

        self.header = tk.Frame(self.root, bg=BG, height=42)
        self.header.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(self.header, text="♪ QQ 音乐歌词", bg=BG, fg=MUTED,
                 font=(FONT, 10, "bold")).pack(side="left")
        self.status_label = tk.Label(self.header, text="待机", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status_label.pack(side="left", padx=(10, 0))
        tk.Button(self.header, text="⚙", command=self._open_settings, bg=BG, fg=MUTED,
                  relief="flat", bd=0, font=(FONT, 11), cursor="hand2",
                  activebackground=BG, activeforeground=TEXT
                  ).pack(side="right")
        tk.Button(self.header, text="✕", command=self.root.destroy, bg=BG, fg=MUTED,
                  relief="flat", bd=0, font=(FONT, 11), cursor="hand2",
                  activebackground=BG, activeforeground=TEXT
                  ).pack(side="right", padx=(0, 8))

        self.info_frame = tk.Frame(self.root, bg=BG)
        self.info_frame.pack(fill="x", padx=14, pady=(10, 0))
        self.cover_label = tk.Label(self.info_frame, bg=PANEL, width=10, height=5)
        self.cover_label.pack(side="left", padx=(0, 12))
        info_text = tk.Frame(self.info_frame, bg=BG)
        info_text.pack(side="left", fill="x", expand=True)
        self.playing_label = tk.Label(info_text, text="", bg=BG, fg=ACCENT, font=(FONT, 9))
        self.playing_label.pack(anchor="w")
        self.title_label = tk.Label(info_text, text="等待播放", bg=BG, fg=TEXT,
                                    font=(FONT, 15, "bold"),
                                    wraplength=int(230 * self.scale), justify="left")
        self.title_label.pack(anchor="w")
        self.artist_label = tk.Label(info_text, text="", bg=BG, fg=MUTED, font=(FONT, 10))
        self.artist_label.pack(anchor="w")
        self.album_label = tk.Label(info_text, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.album_label.pack(anchor="w")

        self.tab_bar = tk.Frame(self.root, bg=BG)
        self.tab_bar.pack(fill="x", padx=14, pady=(12, 0))
        self._tab_buttons = {}
        for name, label in (("lyrics", "歌词"), ("queue", "播放队列"),
                            ("mine", "我的歌单"), ("search", "搜索")):
            btn = tk.Button(self.tab_bar, text=label, command=lambda n=name: self._show_tab(n),
                            bg=BG, fg=MUTED, relief="flat", bd=0, font=(FONT, 10, "bold"),
                            cursor="hand2", activebackground=BG, activeforeground=TEXT,
                            padx=2)
            btn.pack(side="left", padx=(0, 14))
            self._tab_buttons[name] = btn

        self.busy_label = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.busy_label.pack(fill="x", padx=14, pady=(4, 0))

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(fill="both", expand=True, padx=14, pady=(6, 0))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self._build_lyrics_view()
        self._build_queue_view()
        self._build_mine_view()
        self._build_search_view()

        # progress + transport
        progress_row = tk.Frame(self.root, bg=BG)
        progress_row.pack(fill="x", padx=14, pady=(10, 0))
        self.pos_label = tk.Label(progress_row, text="0:00", bg=BG, fg=MUTED, font=(FONT, 9))
        self.pos_label.pack(side="left")
        self.progress = tk.Canvas(progress_row, height=8, bg=LINE, highlightthickness=0)
        self.progress.pack(side="left", fill="x", expand=True, padx=8, pady=(4, 0))
        self.dur_label = tk.Label(progress_row, text="0:00", bg=BG, fg=MUTED, font=(FONT, 9))
        self.dur_label.pack(side="left")
        self.progress.bind("<Button-1>", self._seek_start)
        self.progress.bind("<B1-Motion>", self._seek_drag)
        self.progress.bind("<ButtonRelease-1>", self._seek_commit)

        transport = tk.Frame(self.root, bg=BG)
        transport.pack(fill="x", pady=(6, 0))
        for glyph, cmd in (("⏮", lambda: self._control("previous")),
                           ("⏯", self._toggle_play),
                           ("⏭", lambda: self._control("next"))):
            tk.Button(transport, text=glyph, command=cmd, bg=PANEL, fg=TEXT,
                      relief="flat", bd=0, font=(FONT, 13), cursor="hand2",
                      activebackground=LINE, activeforeground=ACCENT, width=4,
                      height=1).pack(side="left", expand=True)

        offset_row = tk.Frame(self.root, bg=BG)
        offset_row.pack(fill="x", padx=14, pady=(6, 12))
        tk.Label(offset_row, text="歌词偏移", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        for text, ms in (("-0.5", -500), ("-0.1", -100), ("0", 0), ("+0.1", 100), ("+0.5", 500)):
            tk.Button(offset_row, text=text, bg=BG, fg=MUTED, relief="flat", bd=0,
                      font=(FONT, 9), cursor="hand2", activebackground=BG,
                      activeforeground=ACCENT,
                      command=lambda m=ms: self._set_offset(m)).pack(side="left", padx=3)

    def _build_lyrics_view(self):
        self.lyrics_frame = tk.Frame(self.content, bg=BG)
        self.lyrics_frame.grid(row=0, column=0, sticky="nsew")
        bar = tk.Scrollbar(self.lyrics_frame, bg=BG, troughcolor=PANEL)
        bar.pack(side="right", fill="y")
        self.lyrics_text = tk.Text(self.lyrics_frame, bg=BG, fg=TEXT, bd=0,
                                   wrap="word", font=(FONT, 11), spacing1=4,
                                   spacing3=4, state="disabled",
                                   yscrollcommand=bar.set, cursor="arrow",
                                   selectbackground=LINE)
        bar.config(command=self.lyrics_text.yview)
        self.lyrics_text.pack(side="left", fill="both", expand=True)
        self.lyrics_text.tag_config("cur", foreground=ACCENT,
                                    font=(FONT, 13, "bold"))
        self.lyrics_text.tag_config("line", foreground=TEXT)
        self.lyrics_text.tag_config("tr", foreground=MUTED, font=(FONT, 9))
        self.lyrics_text.tag_config("empty", foreground=MUTED, justify="center")

    def _build_queue_view(self):
        self.queue_frame = tk.Frame(self.content, bg=BG)
        self.queue_frame.grid(row=0, column=0, sticky="nsew")
        bar = tk.Scrollbar(self.queue_frame, bg=BG, troughcolor=PANEL)
        bar.pack(side="right", fill="y")
        self.queue_list = tk.Listbox(self.queue_frame, bg=BG, fg=TEXT, bd=0,
                                     font=(FONT, 10), activestyle="none",
                                     yscrollcommand=bar.set, selectbackground=LINE,
                                     selectforeground=ACCENT)
        bar.config(command=self.queue_list.yview)
        self.queue_list.pack(fill="both", expand=True)
        self.queue_list.bind("<Double-Button-1>", self._queue_play)
        btns = tk.Frame(self.queue_frame, bg=BG)
        btns.pack(fill="x", pady=(6, 0))
        for text, direction in (("上一页", "previous"), ("回到开头", "first"),
                                ("下一页", "next")):
            tk.Button(btns, text=text, bg=PANEL, fg=TEXT, relief="flat", bd=0,
                      font=(FONT, 9), cursor="hand2", activebackground=LINE,
                      command=lambda d=direction: self._load_queue(d)
                      ).pack(side="left", expand=True, padx=2)

    def _build_mine_view(self):
        self.mine_frame = tk.Frame(self.content, bg=BG)
        self.mine_frame.grid(row=0, column=0, sticky="nsew")
        bar = tk.Scrollbar(self.mine_frame, bg=BG, troughcolor=PANEL)
        bar.pack(side="right", fill="y")
        self.mine_list = tk.Listbox(self.mine_frame, bg=BG, fg=TEXT, bd=0,
                                    font=(FONT, 10), activestyle="none",
                                    yscrollcommand=bar.set, selectbackground=LINE,
                                    selectforeground=ACCENT)
        bar.config(command=self.mine_list.yview)
        self.mine_list.pack(fill="both", expand=True)
        self.mine_list.bind("<Double-Button-1>", self._mine_activate)
        btns = tk.Frame(self.mine_frame, bg=BG)
        btns.pack(fill="x", pady=(6, 0))
        self.mine_back_btn = tk.Button(btns, text="‹ 歌单列表", bg=PANEL, fg=TEXT,
                                       relief="flat", bd=0, font=(FONT, 9),
                                       cursor="hand2", activebackground=LINE,
                                       command=self._mine_back)
        self.mine_back_btn.pack(side="left", expand=True)
        self.mine_prev_btn = tk.Button(btns, text="上一页", bg=PANEL, fg=TEXT,
                                       relief="flat", bd=0, font=(FONT, 9),
                                       cursor="hand2", activebackground=LINE,
                                       state="disabled",
                                       command=lambda: self._mine_page(-1))
        self.mine_prev_btn.pack(side="left", expand=True)
        self.mine_next_btn = tk.Button(btns, text="下一页", bg=PANEL, fg=TEXT,
                                       relief="flat", bd=0, font=(FONT, 9),
                                       cursor="hand2", activebackground=LINE,
                                       state="disabled",
                                       command=lambda: self._mine_page(1))
        self.mine_next_btn.pack(side="left", expand=True)

    def _build_search_view(self):
        self.search_frame = tk.Frame(self.content, bg=BG)
        self.search_frame.grid(row=0, column=0, sticky="nsew")
        row = tk.Frame(self.search_frame, bg=BG)
        row.pack(fill="x")
        self.search_entry = tk.Entry(row, bg=PANEL, fg=TEXT, bd=0,
                                     font=(FONT, 10), insertbackground=TEXT)
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.search_entry.bind("<Return>", lambda e: self._do_search())
        tk.Button(row, text="搜索", command=self._do_search, bg=ACCENT, fg="#20242c",
                  relief="flat", bd=0, font=(FONT, 10, "bold"), cursor="hand2",
                  activebackground=ACCENT).pack(side="left", padx=(8, 0))
        bar = tk.Scrollbar(self.search_frame, bg=BG, troughcolor=PANEL)
        bar.pack(side="right", fill="y")
        self.search_list = tk.Listbox(self.search_frame, bg=BG, fg=TEXT, bd=0,
                                      font=(FONT, 10), activestyle="none",
                                      yscrollcommand=bar.set, selectbackground=LINE,
                                      selectforeground=ACCENT)
        bar.config(command=self.search_list.yview)
        self.search_list.pack(fill="both", expand=True, pady=(8, 0))
        self.search_list.bind("<Double-Button-1>", self._search_pick)

    # ── tabs / busy ──

    def _show_tab(self, name):
        for key, frame in (("lyrics", self.lyrics_frame), ("queue", self.queue_frame),
                           ("mine", self.mine_frame), ("search", self.search_frame)):
            if key == name:
                frame.lift()
            else:
                frame.lower()
        for key, btn in self._tab_buttons.items():
            btn.config(fg=ACCENT if key == name else MUTED)
        if name == "queue" and not self._queue_page:
            self._load_queue("refresh")
        if name == "mine" and not self.mine_list.size():
            self._load_mine_playlists()

    def _set_busy(self, text=""):
        self._busy = bool(text and not text.startswith(("共 ", "双击", "「", "已", "1-", "读取的是")))
        self.busy_label.config(text=text)

    def _set_topmost(self, on):
        """Queue-panel input (wheel/click) needs the widget off the top."""
        if not self.hwnd:
            return
        SWP_NOMOVE, SWP_NOSIZE = 0x2, 0x1
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(self.hwnd),
            wintypes.HWND(-1 if on else -2), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE)

    def _worker(self, fn, on_done):
        """Run a blocking call off the UI thread; deliver via after()."""
        def run():
            try:
                result = fn()
            except Exception as e:
                result = {"error": str(e)}
            self.root.after(0, lambda: on_done(result))
        threading.Thread(target=run, daemon=True).start()

    def _on_tk_error(self, exc_type, exc_value, tb):
        import logging
        logging.getLogger("bizhi").error("Widget UI error", exc_info=(exc_type, exc_value, tb))

    # ── sync / rendering ──

    def _sync_interval(self):
        return 1000 if self.power_saving else 500

    def _position_interval(self):
        return 500 if self.power_saving else 200

    def _sync_tick(self):
        try:
            snap = self.api.snapshot()
        except Exception:
            snap = {}
        playing = snap.get("is_playing")
        self.status_label.config(text="播放中" if playing else
                                 ("已暂停" if snap.get("title") else "待机"))
        self.playing_label.config(text="正在播放" if playing else "")
        if snap.get("title") != self.title_label.cget("text"):
            self.title_label.config(text=snap.get("title") or "等待播放")
            self.artist_label.config(text=snap.get("artist") or "")
            self.album_label.config(text=snap.get("album") or "")
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
        self.root.after(self._sync_interval(), self._sync_tick)

    def _render_lyrics(self):
        self.lyrics_text.config(state="normal")
        self.lyrics_text.delete("1.0", "end")
        self._line_map = []
        self._current_index = -1
        if not self._lyrics:
            self.lyrics_text.insert("end", "当前歌曲暂无可用歌词\n", "empty")
            self.lyrics_text.insert("end", "播放控制和歌单仍可使用。", "empty")
        else:
            line_no = 1
            for i, entry in enumerate(self._lyrics):
                self.lyrics_text.insert("end", entry.get("text", "") + "\n", f"main_{i}")
                self._line_map.append(line_no)
                line_no += 1
                trans = entry.get("trans", "")
                if trans:
                    self.lyrics_text.insert("end", trans + "\n", f"tr_{i}")
                    line_no += 1
        self.lyrics_text.config(state="disabled")

    def _position_tick(self):
        sync = self._sync_point
        if sync:
            pos = sync.get("position_ms", 0)
            if sync.get("is_playing"):
                pos += int((time.monotonic() - sync.get("at", time.monotonic())) * 1000)
            pos = max(0, pos)
            duration = sync.get("duration_ms", 0)
            self._draw_progress(pos, duration)
            self.pos_label.config(text=self._fmt(pos))
            self.dur_label.config(text=self._fmt(duration))
            offset = self.api._lrc_offset + self.api._user_offset
            self._highlight_lyrics(pos - offset)
        self.root.after(self._position_interval(), self._position_tick)

    def _highlight_lyrics(self, adjusted_ms):
        idx = -1
        for i, entry in enumerate(self._lyrics):
            if adjusted_ms >= entry.get("time_ms", 0):
                idx = i
            else:
                break
        if idx == self._current_index:
            return
        if 0 <= self._current_index < len(self._line_map):
            line = self._line_map[self._current_index]
            self.lyrics_text.tag_remove("cur", f"{line}.0", f"{line}.end+1c")
        self._current_index = idx
        if 0 <= idx < len(self._line_map):
            line = self._line_map[idx]
            self.lyrics_text.tag_add("cur", f"{line}.0", f"{line}.end+1c")
            if not self._seeking:
                self.lyrics_text.see(f"{line}.0")

    def _draw_progress(self, pos, duration):
        self.progress.delete("all")
        width = self.progress.winfo_width() or 350
        if not duration:
            return
        ratio = max(0.0, min(1.0, pos / duration))
        self.progress.create_rectangle(0, 3, width, 5, fill=LINE, width=0)
        self.progress.create_rectangle(0, 3, int(width * ratio), 5, fill=ACCENT, width=0)
        self.progress.create_oval(int(width * ratio) - 4, 0,
                                  int(width * ratio) + 4, 8, fill=ACCENT, width=0)

    @staticmethod
    def _fmt(ms):
        if not ms or ms < 0:
            ms = 0
        return f"{int(ms) // 60000}:{int(ms) // 1000 % 60:02d}"

    # ── cover ──

    def _fetch_cover(self, url):
        if not url:
            self.cover_label.config(image="", text="")
            return
        cached = self._cover_cache.get(url)
        if cached:
            self.cover_label.config(image=cached, text="", width=96, height=96)
            return
        size = int(96 * self.scale)

        def run():
            try:
                data = requests.get(url, timeout=8).content
                img = Image.open(BytesIO(data)).resize((size, size))
                photo = ImageTk.PhotoImage(img)
                self._cover_cache[url] = photo
                self.root.after(0, lambda: self.cover_label.config(
                    image=photo, text="", width=96, height=96))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    # ── transport ──

    def _control(self, action):
        self._worker(lambda: self.api.control_playback(action), lambda r: None)

    def _toggle_play(self):
        self._control("toggle")

    def _set_offset(self, ms):
        def done(result):
            if not result.get("error"):
                self.api._user_offset = result.get("user_offset", self.api._user_offset)
        self._worker(lambda: self.api.set_user_offset(ms), done)

    def _seek_ratio(self, event):
        width = max(1, self.progress.winfo_width())
        return max(0.0, min(1.0, event.x / width))

    def _seek_start(self, event):
        self._seeking = True
        self._seek_drag(event)

    def _seek_drag(self, event):
        sync = self._sync_point or {}
        duration = sync.get("duration_ms", 0)
        if not duration:
            return
        ratio = self._seek_ratio(event)
        self._draw_progress(ratio * duration, duration)
        self.pos_label.config(text=self._fmt(ratio * duration))

    def _seek_commit(self, event):
        sync = self._sync_point or {}
        duration = sync.get("duration_ms", 0)
        self._seeking = False
        if not duration:
            return
        target = int(self._seek_ratio(event) * duration)
        self._worker(lambda: self.api.control_playback("seek", target), lambda r: None)

    # ── queue tab ──

    def _load_queue(self, direction):
        def run():
            self.root.after(0, lambda: self._set_busy("正在读取 QQ 音乐播放队列…"))
            self._set_topmost(False)
            try:
                return self.api.get_qq_playlist(direction)
            finally:
                self._set_topmost(True)
        def done(page):
            if page.get("error"):
                self._set_busy(page["error"])
                return
            self._queue_page = page
            self.queue_list.delete(0, "end")
            for item in page.get("items", []):
                self.queue_list.insert("end", f"{item['title']} — {item['artist']}")
            self._set_busy(f"共 {page.get('total', '?')} 首 · 本页 {len(page.get('items', []))} 首 · 双击播放")
        self._worker(run, done)

    def _queue_play(self, event):
        page = self._queue_page
        if not page or self._busy:
            return
        selection = self.queue_list.curselection()
        if not selection:
            return
        item = page["items"][selection[0]]
        self._set_busy(f"正在切换到「{item['title']}」…")
        def run():
            self._set_topmost(False)
            try:
                return self.api.play_qq_playlist_item(item["id"], page["page_token"])
            finally:
                self._set_topmost(True)
        def done(result):
            if result.get("error"):
                self._set_busy(result["error"])
            else:
                self._set_busy(f"已切换到「{result['title']}」。")
        self._worker(run, done)

    # ── mine tab (real account playlists) ──

    def _load_mine_playlists(self):
        def run():
            self.root.after(0, lambda: self._set_busy("正在读取账号歌单…"))
            return self.api.get_qq_account_playlists()
        def done(data):
            if data.get("error"):
                self._set_busy(data["error"])
                return
            self._mine_playlist = None
            self._mine_playlists_cache = data.get("playlists", [])
            self.mine_back_btn.config(state="disabled")
            self.mine_list.delete(0, "end")
            for pl in self._mine_playlists_cache:
                self.mine_list.insert("end", f"{pl['name']} ({pl['song_num']} 首)")
            self._set_busy("双击歌单浏览歌曲。")
        self._worker(run, done)

    def _mine_activate(self, event):
        selection = self.mine_list.curselection()
        if not selection or self._busy:
            return
        if self._mine_playlist is None:
            playlists = self._mine_playlists_cache
            if selection[0] < len(playlists):
                self._open_mine_playlist(playlists[selection[0]])
        else:
            songs = self._mine_songs_cache
            if selection[0] < len(songs):
                self._play_mine_song(songs[selection[0]])

    def _open_mine_playlist(self, playlist, begin=0):
        self._mine_playlist = playlist
        self._mine_begin = begin
        def run():
            self.root.after(0, lambda: self._set_busy(f"正在读取「{playlist['name']}」…"))
            return self.api.get_qq_account_playlist_songs(playlist["tid"], begin, 30)
        def done(data):
            if data.get("error"):
                self._set_busy(data["error"])
                return
            self._mine_songs_cache = data.get("songs", [])
            self._mine_has_more = data.get("has_more", False)
            self.mine_back_btn.config(state="normal")
            self.mine_list.delete(0, "end")
            for song in self._mine_songs_cache:
                self.mine_list.insert("end", f"{song['songname']} — {song['singer']}")
            self._set_busy(f"「{playlist['name']}」双击歌曲播放。")
            self.mine_prev_btn.config(state="normal" if begin > 0 else "disabled")
            self.mine_next_btn.config(state="normal" if self._mine_has_more else "disabled")
        self._worker(run, done)

    def _mine_page(self, direction):
        if self._mine_playlist:
            self._open_mine_playlist(self._mine_playlist,
                                     max(0, self._mine_begin + direction * 30))

    def _mine_back(self):
        self._mine_playlist = None
        self._mine_songs_cache = []
        self._load_mine_playlists()

    def _play_mine_song(self, song):
        self._set_busy(f"正在让 QQ 音乐播放「{song['songname']}」…")
        def run():
            self._set_topmost(False)
            try:
                return self.api.play_qq_account_song(song["songname"], song["singer"])
            finally:
                self._set_topmost(True)
        def done(result):
            if result.get("error"):
                self._set_busy(result["error"])
            else:
                self._set_busy(f"已在 QQ 音乐播放「{result['title']}」。")
        self._worker(run, done)

    # ── search tab ──

    def _do_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword or self._busy:
            return
        self._set_busy("正在搜索…")
        def done(results):
            if isinstance(results, dict):
                self._set_busy(results.get("error", "搜索失败"))
                return
            self._search_results = results
            self.search_list.delete(0, "end")
            for song in self._search_results:
                self.search_list.insert("end", f"{song['songname']} — {song['singer']}")
            self._set_busy("双击结果加载歌词。")
        self._worker(lambda: self.api.search(keyword), done)

    def _search_pick(self, event):
        selection = self.search_list.curselection()
        if not selection or self._busy:
            return
        song = self._search_results[selection[0]]
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

    # ── settings dialog ──

    def _open_settings(self):
        if getattr(self, "_settings_win", None) is not None:
            try:
                self._settings_win.lift()
                return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("设置")
        win.geometry("300x150")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text="设置", bg=BG, fg=TEXT, font=(FONT, 12, "bold")).pack(
            anchor="w", padx=14, pady=(12, 6))

        power = tk.BooleanVar(value=self.power_saving)
        def on_power():
            self.power_saving = power.get()
            self._worker(lambda: self.api.set_power_saving(power.get()), lambda r: None)
        tk.Checkbutton(win, text="省电模式（降低刷新频率）", variable=power,
                       command=on_power, bg=BG, fg=TEXT, font=(FONT, 10),
                       activebackground=BG, selectcolor=PANEL).pack(anchor="w", padx=14, pady=4)

        autostart = tk.BooleanVar(value=self.api.get_autostart().get("enabled", False))
        def on_autostart():
            self._worker(lambda: self.api.set_autostart(autostart.get()), lambda r: None)
        tk.Checkbutton(win, text="开机自启动", variable=autostart,
                       command=on_autostart, bg=BG, fg=TEXT, font=(FONT, 10),
                       activebackground=BG, selectcolor=PANEL).pack(anchor="w", padx=14, pady=4)

        tk.Label(win, text="任务栏歌词样式请在 data/config.json 调整。",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", padx=14, pady=(8, 12))

    # ── window behaviours ──

    def _make_draggable(self, widget):
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_motion)

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_motion(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _setup_native_window(self):
        """Win11 DWM rounded corners + publish the top-level hwnd for the tray.

        Runs via after(): the top-level HWND only exists once the window has
        actually been mapped (update_idletasks alone is not enough).
        """
        try:
            self.hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            if not self.hwnd:
                self.hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            dwm = ctypes.windll.dwmapi
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(wintypes.HWND(self.hwnd), 33,
                                      ctypes.byref(preference), ctypes.sizeof(preference))
            dark = ctypes.c_int(1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
            dwm.DwmSetWindowAttribute(wintypes.HWND(self.hwnd), 20,
                                      ctypes.byref(dark), ctypes.sizeof(dark))
            HWND_FILE.parent.mkdir(parents=True, exist_ok=True)
            HWND_FILE.write_text(str(self.hwnd), encoding="utf-8")
            logger.info(
                "widget geometry: tk=%s screen=%sx%s fpixels_per_inch=%.1f",
                self.root.geometry(), self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(), self.root.winfo_fpixels("1i"))
        except Exception:
            import traceback
            logger.error("native window setup failed:\n%s", traceback.format_exc())
            self.hwnd = 0
