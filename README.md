# BIZHI - 轻量级动态壁纸 & 桌面组件

适用于 Windows 11 的轻量级动态壁纸软件，支持自定义桌面组件。

## ✨ 功能

- **动态壁纸** — 支持静态图片、GIF 动画、渐变色和纯色背景
- **日程待办** — 桌面悬浮待办列表，支持优先级、截止日期、分类
- **QQ音乐歌词** — 实时歌词显示，自动检测QQ音乐播放，滚动高亮
- **任务栏歌词** — 歌词直接嵌入任务栏显示，酷狗风格，鼠标悬停托盘也可查看
- **系统托盘** — 最小化到托盘，快捷切换组件和壁纸

## 📦 安装

```bash
pip install -r requirements.txt
```

## 🚀 使用

```bash
# 默认启动（渐变壁纸 + 两个组件）
python main.py

# 指定壁纸图片
python main.py --wallpaper path/to/image.png

# GIF 动画壁纸
python main.py --wallpaper path/to/animation.gif

# 纯色壁纸
python main.py --color "#1a1a2e"

# 仅启动组件（不更换壁纸）
python main.py --no-wallpaper
```

## 📁 项目结构

```
BIZHI/
├── main.py                    # 主入口
├── requirements.txt           # Python 依赖
├── core/
│   ├── desktop.py             # Windows WorkerW 桌面注入
│   ├── wallpaper.py           # 壁纸渲染引擎
│   ├── taskbar_lyrics.py      # 任务栏歌词组件
│   └── tray.py                # 系统托盘管理
├── widgets/
│   ├── manager.py             # 组件窗口管理器
│   ├── todo/
│   │   ├── api.py             # 待办后端 CRUD
│   │   └── index.html         # 待办前端 UI
│   └── music/
│       ├── api.py             # 歌词后端逻辑
│       ├── qq_music.py        # QQ音乐 API 客户端
│       └── index.html         # 歌词前端 UI
├── assets/
│   ├── wallpapers/            # 壁纸资源
│   └── icons/                 # 图标资源
└── data/
    └── todos.json             # 待办数据存储
```

## 🛠️ 技术栈

- **Python** — 主语言
- **tkinter** — 壁纸渲染层（轻量、内置）
- **pywebview** — 组件 UI（基于 WebView2，Win11 原生）
- **pystray** — 系统托盘
- **Pillow** — 图像处理
- **Windows API (ctypes)** — WorkerW 桌面注入

## 🎵 任务栏歌词配置

歌词默认显示在任务栏右侧，宽 400px。如果你的任务栏较满（固定了很多应用），歌词可能和应用按钮重叠，需要手动调整偏移。

编辑 `core/taskbar_lyrics.py`，找到 `_run()` 方法中的以下三行：

```python
lyric_w = min(400, w // 3)   # 歌词区域宽度（像素）
lyric_h = h - 4              # 歌词区域高度（比任务栏矮 4px，留出边距）
lx = bar.right - lyric_w - 80  # 水平偏移：距屏幕右边缘 80px（托盘区域）
ly = bar.top + 2             # 垂直偏移：比任务栏顶部低 2px
```

### 常用调整场景

| 场景 | 修改 |
|------|------|
| 歌词和应用按钮重叠 | 增大 `lx` 的末尾数字（如 `80` → `200`），向左移动 |
| 歌词太窄/太宽 | 修改 `400`（如 `300` 或 `500`） |
| 歌词位置偏上/偏下 | 调整 `ly` 的 `+2` 偏移量 |
| 任务栏在屏幕左侧 | 将 `lx` 改为 `bar.left + offset` |
| 任务栏在屏幕顶部 | 将 `ly` 改为 `bar.bottom + offset` |

调整后重启 `python main.py` 生效。

## 🚀 快速启动

**桌面快捷方式：** 双击桌面上的 `BIZHI` 图标即可启动

**命令行：** 直接运行 `D:\code\BIZHI\BIZHI.bat`

## 📝 注意事项

- 需要 Windows 10/11 系统
- 首次运行会安装 WebView2 运行时（Win11 已内置）
- QQ音乐歌词功能需要网络连接
- 组件窗口可通过拖拽标题栏移动
- 右键系统托盘图标可退出程序
