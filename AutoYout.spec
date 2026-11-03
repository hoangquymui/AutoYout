# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for AutoYout desktop application."""
from pathlib import Path
import os
import sys

ROOT = Path.cwd().resolve()

# Bundled data files: (source_path, target_relative_dir)
datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "config" / "default_settings.json"), "config"),
    (str(ROOT / "config" / "language_prompt.json"), "config"),
    (str(ROOT / "bin"), "bin"),
]

hiddenimports = [
    "PySide6.QtSvg",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "huggingface_hub",
    "huggingface_hub.hf_api",
    "huggingface_hub.file_download",
    "huggingface_hub._snapshot_download",
    "huggingface_hub.utils",
    "huggingface_hub.utils.tqdm",
    "filelock",
    "tqdm",
    "certifi",
    "requests",
    "urllib3",
    "edge_tts",
    "pydub",
    "soundfile",
    "PIL",
    "psutil",
    "yt_dlp",
    "youtube_transcript_api",
    "googleapiclient",
    "googleapiclient.discovery",
    "google_auth_oauthlib",
    "google.auth.transport.requests",
    "shutil",
    "subprocess",
    "tempfile",
]

# Exclude unnecessary heavy packages to keep the build lean and fast
excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "ipykernel",
    "jupyter_client",
    "jupyter_core",
    "notebook",
    "pytest",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoYout",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "logo" / "logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AutoYout",
)

