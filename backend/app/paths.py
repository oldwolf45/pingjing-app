"""路径解析：安装目录 / 数据目录 / 备份目录 / 静态资源目录。

三种运行模式：
  1. 开发模式（python -m app.main）      → 项目目录
  2. PyInstaller onedir 打包（sys.frozen）→ exe 同级的 _internal
  3. 测试模式（ZISUYE_DATA_DIR 覆盖）     → 指向临时目录

数据目录绝不能放在安装目录下：Program Files 普通用户没有写权限，
而且 SQLite 的 WAL 模式在 OneDrive 同步盘上会损坏。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ZiSuYe"

# 环境变量覆盖（测试与便携模式用）
ENV_DATA_DIR = "ZISUYE_DATA_DIR"
ENV_MODE = "ZISUYE_MODE"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """安装目录：只读的程序文件所在位置。"""
    if is_frozen():
        # onedir 模式：sys.executable 在根目录，_internal 在 sys._MEIPASS
        return Path(sys.executable).resolve().parent
    # 开发模式：backend/ 的上一级就是项目根
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """打包进去的静态资源目录（frontend/dist）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", install_dir())) / "frontend" / "dist"
    # 开发模式：backend/ 的兄弟目录 frontend/dist
    return install_dir().parent / "frontend" / "dist"


def data_dir() -> Path:
    """可写数据目录。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        p = Path(override).expanduser().resolve()
    elif is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / APP_NAME
    else:
        # 开发模式：项目根下的 data_local，避免污染旧项目的 data/
        p = install_dir() / "data_local"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_dir() -> Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_dir() -> Path:
    p = data_dir() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "zisuye.db"


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def describe() -> dict:
    """给设置页和启动日志看的路径快照。"""
    return {
        "frozen": is_frozen(),
        "install_dir": str(install_dir()),
        "resource_dir": str(resource_dir()),
        "data_dir": str(data_dir()),
        "backup_dir": str(backup_dir()),
        "db_path": str(db_path()),
    }
