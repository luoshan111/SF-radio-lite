# BIZHI

Windows 11 上的 **QQ音乐桌面歌词挂件**：桌面上悬浮一张歌词卡片，同时把歌词画到任务栏左侧。

> 说明：旧版本的「动态壁纸」功能已整体移除，现在只保留歌词挂件与任务栏歌词。

## 功能

- **歌词卡片挂件** — 封面、歌名/歌手、逐行歌词、原文+翻译、播放/暂停、上一首/下一首、进度拖动；原生 tkinter 界面（无 WebView2）：从托盘打开、关闭即释放内存，平时只有轻量的任务栏歌词常驻
- **界面动效** — 面板切换、歌词高亮与滚动、封面淡入等动画，设置里可一键关闭并立即生效
- **自动识别播放** — 通过 Windows 媒体会话（SMTC）自动同步正在播放的歌曲，无需手动搜索
- **QQ 歌单** — 读取 QQ 音乐客户端的当前播放队列，分页浏览、刷新并点击歌曲切换播放
- **我的歌单** — 浏览客户端账号的「喜欢」歌曲列表并点歌播放。需要客户端以 `--force-renderer-accessibility` 启动；挂件检测到不可读时提供一键重启客户端按钮
- **任务栏歌词** — 歌词直接显示在任务栏左侧，字体/颜色/描边/背景可实时调整
- **系统托盘** — 显示窗口、开机自启、退出

## 运行环境

| 项目 | 要求 |
|------|------|
| 系统 | Windows 10/11（Win11 体验最佳） |
| Python | **3.12**（官方解释器，见下方安装步骤） |
| WebView2 | Win11 已内置；Win10 首次运行会提示安装 |

## 安装

### 第 1 步：装 Python 3.12

打开 PowerShell，执行：

```powershell
winget install -e --id Python.Python.3.12 --scope user
```

装完后重开一个 PowerShell，确认：

```powershell
python --version
```

显示 `Python 3.12.x` 即可。若提示找不到 `python`，说明没加入 PATH，用完整路径 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` 代替下文的 `python`。

### 第 2 步：装依赖

```powershell
cd D:\code\SF-radio-lite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

所有依赖只装在项目里的 `.venv` 文件夹，不会动系统 Python。想彻底卸载，删掉 `.venv` 即可。

### 第 3 步：启动

```powershell
.\.venv\Scripts\python.exe main.py
```

或者直接双击项目里的 **`BIZHI.bat`**（它内部就是用 `.venv` 启动，出错时会停住显示报错）。

## 启动后你会看到

1. 桌面右上角出现一张歌词卡片挂件（无边框、置顶、可拖动）
2. 任务栏左侧出现当前歌词（默认开启）
3. 右下角托盘出现 BIZHI 图标（可能在 `^` 折叠区里）

启动 QQ音乐播放任意歌曲，卡片会自动显示歌曲信息与歌词。

## 使用说明

### 卡片挂件

| 操作 | 位置 |
|------|------|
| 拖动挂件 | 按住卡片顶部标题区域拖 |
| 打开设置 | 右上角齿轮 ⚙ |
| 最小化 | 右上角 `−`（仅隐藏，程序仍在托盘运行） |
| 搜索歌曲 | 卡片内搜索框输入歌名后选择 |
| 手动播放控制 | 封面下方的播放/上一首/下一首/进度条 |

### 设置面板（齿轮 ⚙）

- **常规** 页：任务栏歌词开关、开机自启动开关、歌词偏移
- **任务栏样式** 页：预设（清晰浅色 / 暖金高亮 / 冰蓝清透 / 深色高对比）、字号、字重、歌词与歌曲名颜色、无效果/阴影/描边、透明或深色背景

所有设置即时生效，并自动保存到配置文件。

### 托盘菜单（右键 BIZHI 图标）

- **显示窗口** — 把隐藏的挂件重新显示出来
- **开机自启** — 勾选后随 Windows 登录自动启动
- **退出** — 完全退出程序

## 配置文件

| 运行方式 | 配置位置 |
|----------|----------|
| 源码运行 | `D:\code\SF-radio-lite\data\config.json` |
| 打包 exe | `%APPDATA%\BIZHI\data\config.json` |

保存内容：挂件位置、歌词偏移、任务栏歌词主题。删掉该文件即可恢复全部默认值。该文件不会被提交到 git。

## 任务栏歌词位置不对？

任务栏歌词是画在任务栏上的一个透明窗口，位置写死在 `core/taskbar_lyrics.py` 的 `_run()` 里，找到这 4 行：

```python
lyric_w = min(520, w // 3)   # 歌词区域宽度（像素）
lyric_h = h - 4              # 高度（比任务栏矮 4px，留边距）
lx = bar.left + 80           # 左边缘偏移：距任务栏最左侧 80px
ly = bar.top + 2             # 上边缘偏移：比任务栏顶部低 2px
```

常见改法：

| 现象 | 怎么改 |
|------|--------|
| 歌词和左侧应用图标重叠 | 把 `lx` 的 `80` 改大，例如 `200` |
| 歌词太窄 / 太宽 | 改 `min(520, w // 3)` 里的 `520` |
| 歌词偏上 / 偏下 | 调 `ly` 的 `+2` |
| 任务栏在屏幕顶部 | 把 `ly` 改成 `bar.bottom + 偏移` |

改完重新启动程序生效。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

覆盖：LRC 解析、原文/翻译合并、搜索与歌词接口错误路径、SMTC 同步去重、配置持久化、退出幂等、任务栏主题校验。

## 打包成 exe

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller bizhi.spec --noconfirm
```

产物：`dist\BIZHI\BIZHI.exe`（onedir 模式）。打包版的数据目录是 `%APPDATA%\BIZHI\`。

## 目录结构

```
SF-radio-lite/
├── main.py                     # 程序入口：启动歌词组件、托盘、事件循环
├── BIZHI.bat                   # 一键启动脚本（使用 .venv）
├── requirements.txt            # Python 依赖
├── bizhi.spec                  # PyInstaller 打包配置
├── core/
│   ├── config.py               # 配置读写（data/config.json）
│   ├── desktop.py              # Windows DPI 适配
│   ├── taskbar_lyrics.py       # 任务栏歌词窗口
│   └── tray.py                 # 系统托盘 + 开机自启
├── widgets/
│   ├── manager.py              # pywebview 窗口管理
│   └── music/
│       ├── api.py              # 暴露给网页的 Python 接口
│       ├── qq_music.py         # QQ音乐搜索 / 封面 / SMTC 客户端
│       └── index.html          # 卡片界面（HTML/CSS/JS）
├── assets/icons/               # 图标资源
├── tests/                      # 单元测试
└── data/                       # 运行时数据（已 gitignore）
```

## 技术栈

Python 3.12 · pywebview（WebView2）· pystray（托盘）· Pillow（图标）· winsdk（SMTC 媒体检测）· tkinter（任务栏歌词绘制）· ctypes（Win32 API）

## 常见问题

- **双击 `BIZHI.bat` 没反应/闪退** — 脚本会在出错时暂停并显示报错；若仍无输出，改成命令行运行 `.\.venv\Scripts\python.exe main.py` 查看日志。
- **歌词不动** — 需要 QQ音乐/系统媒体会话正在播放；缺少 `winsdk` 时会降级为窗口标题检测，效果较差。
- **任务栏看不到歌词** — 检查齿轮 →「任务栏歌词」是否开启；重启过资源管理器（explorer.exe）后需重启本程序。
- **托盘图标找不到** — Win11 默认折叠到 `^` 里，拖出来固定即可。
- **播放/暂停按钮无效** — 依赖 SMTC 控制通道，个别播放器不支持回控。

## 已知限制

- 仅支持 Windows 系统。
- 歌曲识别依赖 Windows 媒体会话，非 QQ音乐客户端（如浏览器网页播放）可能识别不到。
- 歌词数据来自 QQ音乐公开接口，需要联网。
