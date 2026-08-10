path = r"D:\code\BIZHI\widgets\music\index.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

if "win-btn" not in html:
    css = """  .win-btn {
    -webkit-app-region: no-drag;
    background: rgba(255,255,255,0.06);
    border: none;
    border-radius: 6px;
    width: 28px; height: 28px;
    color: var(--text-dim);
    font-size: 14px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all var(--transition);
    flex-shrink: 0;
    line-height: 1;
    margin-left: 6px;
  }
  .win-btn:hover { background: rgba(255,255,255,0.12); color: var(--text); }

"""
    html = html.replace("  .header {\n", css + "  .header {\n")

old2 = '<div class="status-dot" id="statusDot" title="\u64AD\u653E\u72B6\u6001"></div>\n  </div>'
new2 = '<div class="status-dot" id="statusDot" title="\u64AD\u653E\u72B6\u6001"></div>\n    <button class="win-btn" onclick="minimizeWin()" title="\u6700\u5C0F\u5316">\u2013</button>\n  </div>'
html = html.replace(old2, new2)

if "minimizeWin" not in html:
    fn = '  async function minimizeWin() {\n    await waitForApi();\n    window.pywebview.api.minimize();\n  }\n\n  startSync();'
    html = html.replace("  startSync();", fn)

with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print("done")
