# BIZHI - 轻量级动态壁纸 & 桌面组件

适用于 Windows 11 的轻量级动态壁纸软件，支持自定义桌面组件。

## ✨ 功能

- **动态壁纸** — 支持静态图片、GIF 动画、渐变色和纯色背景（注入桌面图标层 WorkerW）
- **QQ音乐音乐卡片** — 封面、歌曲信息、实时歌词、原词+翻译、播放/暂停、上一首/下一首和进度定位，通过 Windows SMTC 自动同步
- **任务栏歌词** — 歌词直接嵌入任务栏显示，支持字体、颜色、描边、阴影和背景主题实时调整
- **系统托盘** — 最小化到托盘，快捷更换壁纸、开机自启

## 📦 安装

```bash
pip install -r requirements.txt
```

## 🚀 使用

```bash
# 默认启动（恢复上次壁纸或渐变壁纸 + 歌词组件）
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

启动后壁纸类型、组件位置、歌词延迟和任务栏歌词主题都会持久化到 `data/config.json`，下次启动自动恢复。

## 📁 项目结构

```
BIZHI/
├── main.py                    # 主入口
├── requirements.txt           # Python 依赖
├── bizhi.spec                 # PyInstaller 打包配置
├── core/
│   ├── desktop.py             # Windows WorkerW 桌面注入 / 文件对话框 / DPI
│   ├── wallpaper.py           # 壁纸渲染引擎（图片/GIF/渐变/纯色）
│   ├── taskbar_lyrics.py      # 任务栏歌词组件
│   ├── tray.py                # 系统托盘（含开机自启）
│   └── config.py              # 配置持久化
├── widgets/
│   ├── manager.py             # 组件窗口管理器
│   └── music/
│       ├── api.py             # 歌词与播放控制后端（SMTC 同步）
│       ├── qq_music.py        # QQ音乐 API / 封面 / SMTC 客户端
│       └── index.html         # 音乐卡片与歌词前端 UI
├── tests/                     # unittest 测试（python -m unittest discover -s tests）
├── assets/
│   └── icons/                 # 图标资源
└── data/                      # 运行时数据（已 gitignore）
    └── config.json            # 配置（壁纸/窗口位置/歌词延迟）
```

## 🛠️ 技术栈

- **Python** — 主语言
- **tkinter** — 壁纸渲染层（轻量、内置）
- **pywebview** — 组件 UI（基于 WebView2，Win11 原生）
- **pystray** — 系统托盘
- **Pillow** — 图像处理
- **winsdk** — Windows SMTC（媒体播放检测，缺失时降级为窗口标题检测）
- **Windows API (ctypes)** — WorkerW 桌面注入

## 🎵 任务栏歌词配置

歌词默认显示在任务栏左侧，采用高对比描边文字。打开歌词挂件右上角的齿轮，在“任务栏样式”子 Tab 中可以实时调整：

- 任务栏歌词开关、是否显示歌曲名
- 清晰浅色、暖金高亮、冰蓝清透、深色高对比预设
- 字号、字重、歌词颜色、歌曲名颜色
- 无背景或深色背景，以及无效果、阴影、描边

设置会立即应用并保存到 `data/config.json`，无需编辑源码或重启。

任务栏歌词默认宽度为 520px。如果你的任务栏较满（固定了很多应用），歌词可能和应用按钮重叠，需要调整 `_run()` 中的定位值：

```python
lyric_w = min(520, w // 3)   # 歌词区域宽度（像素）
lyric_h = h - 4              # 歌词区域高度（比任务栏矮 4px，留出边距）
lx = bar.left + 80           # 水平偏移：距任务栏左边缘 80px
ly = bar.top + 2             # 垂直偏移：比任务栏顶部低 2px
```

### 常用调整场景

| 场景 | 修改 |
|------|------|
| 歌词和应用按钮重叠 | 增大 `lx` 的末尾数字（如 `80` → `200`），向右移动 |
| 歌词太窄/太宽 | 修改 `400`（如 `300` 或 `500`） |
| 歌词位置偏上/偏下 | 调整 `ly` 的 `+2` 偏移量 |
| 任务栏在屏幕顶部 | 将 `ly` 改为 `bar.bottom + offset` |

调整后重启 `python main.py` 生效。

## 🧪 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：LRC 解析、原词/翻译合并、搜索与歌词 API 错误路径、SMTC 同步去重、配置持久化、退出幂等、托盘换壁纸流程、GIF 动画帧时长等。

## 📦 打包发布

```bash
pip install pyinstaller
pyinstaller bizhi.spec --noconfirm
```

产物在 `dist/BIZHI/`（onedir 模式）。打包版的数据（配置/待办）保存在 `%APPDATA%/BIZHI/`。

## 🚀 快速启动

**桌面快捷方式：** 双击桌面上的 `BIZHI` 图标即可启动。开机自启动可在歌词挂件的“设置 → 常规”中开启，也可通过托盘菜单切换。

**命令行：** 直接运行 `D:\code\BIZHI\BIZHI.bat`

## 📝 注意事项

- 需要 Windows 10/11 系统
- 首次运行会安装 WebView2 运行时（Win11 已内置）
- QQ音乐歌词功能需要网络连接
- 壁纸注入依赖非文档化 Win32 技巧（`core/desktop.py`），个别 Windows 版本可能失败——失败时壁纸窗口会自动隐藏，程序其余功能不受影响
- 组件窗口可通过拖拽标题栏移动
- 右键系统托盘图标可退出程序
