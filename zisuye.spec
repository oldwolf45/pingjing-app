# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

要点：
  1. onedir（不是 onefile）：启动快、杀软误报少、升级只换文件
  2. --noupx：压缩过的 exe 被杀软误报的概率高很多
  3. 前端 dist 打进 _internal/frontend/dist，app/paths.py 用 sys._MEIPASS 找
  4. pywebview 的 winforms 后端是动态导入，必须显式 hiddenimport
"""

import os
import sys
from pathlib import Path

ROOT = Path(os.getcwd())
APP_NAME = "紫苏叶"

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "backend" / "app" / "db" / "schema.sql"), "app/db"),
    # 算法文件当数据打：既要能 import（sys.path 加进去），
    # 又要能读源码算 sha256 做指纹校验。编译进 PYZ 就没有源文件可校验了。
    (str(ROOT / "backend" / "app" / "algo"), "app/algo"),
]

hiddenimports = [
    "webview.platforms.winforms",
    "clr_loader",
    "pythonnet",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

excludes = [
    "tkinter", "matplotlib", "numpy", "pandas", "scipy", "PIL", "IPython",
    "jupyter", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest", "_pytest", "setuptools", "pip", "wheel",
    "sqlalchemy", "alembic", "boto3", "botocore",
]

a = Analysis(
    [str(ROOT / "backend" / "app" / "main.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
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
    icon=str(ROOT / "installer" / "zisuye.ico"),
    version=str(ROOT / "installer" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
