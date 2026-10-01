// Generic song-list panel used by both the play-queue tab and the
// library-page ("我的歌单") tab. Each panel is an independent instance
// configured with its own data source and wording; the DOM stays in
// index.html and only the behaviour lives here.
//
// config: {
//   root, count, message, items, previousBtn, nextBtn — selectors inside the panel
//   loadApi: async (direction) => page data | {error}
//   playApi: async (itemId, pageToken) => result | {error}
//   loadingText, errorCountText, playLoadingText, playErrorText — static strings
//   successText: (data) => string   — shown when the page has songs
//   emptyText: (data) => string     — shown when the page has no songs
//   countText: (data) => string     — the toolbar count line
//   playSuccessText: (result) => string
//   onReset: () => void             — extra cleanup when a load fails
//   onSuccess: (data) => void       — extra update when a load succeeds
//   onPlayed: () => void            — e.g. reschedule the lyrics sync
// }
function createSongListPanel(config) {
  let data = null;
  let busy = false;
  const nowPlaying = { title: "", artist: "", playing: false };
  const $ = selector => document.querySelector(selector);

  function message(text, error = false) {
    const el = $(config.message);
    el.textContent = text;
    el.classList.toggle("error", Boolean(error));
  }

  function setBusy(value) {
    busy = value;
    $(config.root).setAttribute("aria-busy", String(value));
    document.querySelectorAll(config.root + " button").forEach(button => { button.disabled = value; });
    if (!value && data) {
      $(config.previousBtn).disabled = Boolean(data.at_start);
      $(config.nextBtn).disabled = Boolean(data.at_end);
    }
  }

  function render() {
    const container = $(config.items);
    container.replaceChildren();
    $(config.count).textContent = config.countText(data);
    for (const item of data.items) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "playlist-row";
      row.dataset.itemId = item.id;
      row.setAttribute("aria-label", "播放 " + item.title + "，" + item.artist);
      for (const [className, text] of [["playlist-title", item.title], ["playlist-artist", item.artist], ["playlist-marker", "播放"]]) {
        const span = document.createElement("span");
        span.className = className;
        span.textContent = text;
        row.appendChild(span);
      }
      row.addEventListener("click", () => play(item.id, data.page_token));
      container.appendChild(row);
    }
    highlight();
  }

  function highlight() {
    if (!data) return;
    const normalize = text => String(text || "").toLocaleLowerCase().replace(/\s/g, "");
    for (const item of data.items) {
      const row = document.querySelector(config.root + ' .playlist-row[data-item-id="' + item.id + '"]');
      if (!row) continue;
      const current = normalize(item.title) === normalize(nowPlaying.title) && normalize(item.artist) === normalize(nowPlaying.artist);
      row.setAttribute("aria-current", String(current));
      row.querySelector(".playlist-marker").textContent = current ? (nowPlaying.playing ? "播放中" : "已暂停") : "播放";
    }
  }

  function reset() {
    data = null;
    $(config.items).replaceChildren();
    $(config.count).textContent = config.errorCountText;
    if (config.onReset) config.onReset();
  }

  async function load(direction) {
    if (busy) return;
    setBusy(true);
    message(config.loadingText);
    try {
      if (!await waitForApi()) throw new Error("桌面接口不可用");
      const result = await config.loadApi(direction);
      if (!result || result.error) throw new Error(result?.error || "歌单读取失败");
      data = result;
      render();
      if (config.onSuccess) config.onSuccess(data);
      message(data.items.length ? config.successText(data) : config.emptyText(data));
    } catch (error) {
      reset();
      message(error.message || "歌单读取失败", true);
    } finally {
      setBusy(false);
    }
  }

  async function play(itemId, pageToken) {
    if (busy) return;
    setBusy(true);
    message(config.playLoadingText);
    try {
      const result = await config.playApi(itemId, pageToken);
      if (!result || result.error || !result.verified) throw new Error(result?.error || "尚未确认播放");
      message(config.playSuccessText(result));
      if (config.onPlayed) config.onPlayed();
    } catch (error) {
      message(error.message || config.playErrorText, true);
    } finally {
      setBusy(false);
    }
  }

  function setNowPlaying(title, artist, playing) {
    nowPlaying.title = title || "";
    nowPlaying.artist = artist || "";
    nowPlaying.playing = Boolean(playing);
    highlight();
  }

  return {
    load, play, setNowPlaying, message, setBusy,
    isBusy: () => busy,
    hasData: () => Boolean(data),
  };
}
