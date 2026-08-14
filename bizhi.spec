# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BIZHI.

Build (one-dir, recommended for this project since it keeps user data
next to the executable):
    pyinstaller bizhi.spec

Output: dist/BIZHI/BIZHI.exe
"""

import os

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ("assets", "assets"),
        ("widgets/music/index.html", "widgets/music"),
    ],
    hiddenimports=[
        "winsdk.windows.media.control",
        "webview.platforms.winforms",
        "clr",  # pythonnet (WinForms backend)
        "pystray._win32",
        "PIL._tkinter_finder",  # ImageTk support
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BIZHI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icons/bizhi.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BIZHI",
)
