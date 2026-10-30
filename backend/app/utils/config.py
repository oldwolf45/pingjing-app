"""应用配置管理。

统一管理所有环境变量和默认配置。
算法参数（池阈值、仓位上限等）在 algo/config.py 里，不归这里管。

环境变量一览：
  ZISUYE_MODE          运行模式：production / development / test
  ZISUYE_DATA_DIR      数据目录（覆盖默认路径）
  ZISUYE_LOG_LEVEL     日志级别：DEBUG / INFO / WARNING / ERROR
  ZISUYE_DEBUG         非空则开启 pywebview 调试模式
  ZISUYE_REMOTE_DEBUG  远程调试端口
  ZISUYE_HTTP_PROXY    HTTP 代理（抓数据源用）
  ZISUYE_SINGLE_INSTANCE  非空则启用单实例检测

用法：
    from app.utils.config import settings
    print(settings.log_level)
    print(settings.data_dir)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app import paths


@dataclass
class AppSettings:
    """应用配置。

    所有配置都有默认值，环境变量可以覆盖。
    不需要外部依赖（pydantic-settings 太重，我们自己读环境变量）。
    """

    # 运行模式
    mode: str = "production"  # production / development / test

    # 日志级别
    log_level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR

    # 调试模式
    debug: bool = False
    remote_debug_port: Optional[int] = None

    # HTTP 代理（抓数据用）
    http_proxy: Optional[str] = None

    # 单实例检测
    single_instance: bool = True

    # 窗口默认大小
    window_width: int = 1280
    window_height: int = 800
    window_min_width: int = 960
    window_min_height: int = 640

    # 更新超时（秒）
    update_timeout_sec: int = 600

    # 备份保留策略
    backup_daily_keep: int = 7
    backup_weekly_keep: int = 8
    backup_premigration_keep: int = 5

    @property
    def data_dir(self) -> Path:
        """数据目录路径。"""
        return paths.data_dir()

    @property
    def db_path(self) -> Path:
        """数据库文件路径。"""
        return paths.db_path()

    @property
    def is_development(self) -> bool:
        return self.mode == "development"

    @property
    def is_test(self) -> bool:
        return self.mode == "test"

    @property
    def is_production(self) -> bool:
        return self.mode == "production"


def _read_bool(name: str, default: bool = False) -> bool:
    """从环境变量读布尔值。非空字符串即视为 True。"""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _read_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _read_str(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val.strip() if val else default


def _load_settings() -> AppSettings:
    """从环境变量加载配置。"""
    return AppSettings(
        mode=_read_str("ZISUYE_MODE", "production").lower() or "production",
        log_level=_read_str("ZISUYE_LOG_LEVEL", "INFO").upper() or "INFO",
        debug=_read_bool("ZISUYE_DEBUG"),
        remote_debug_port=_read_int("ZISUYE_REMOTE_DEBUG", 0) or None,
        http_proxy=_read_str("ZISUYE_HTTP_PROXY") or None,
        single_instance=_read_bool("ZISUYE_SINGLE_INSTANCE", True),
        window_width=_read_int("ZISUYE_WINDOW_WIDTH", 1280),
        window_height=_read_int("ZISUYE_WINDOW_HEIGHT", 800),
        window_min_width=_read_int("ZISUYE_WINDOW_MIN_WIDTH", 960),
        window_min_height=_read_int("ZISUYE_WINDOW_MIN_HEIGHT", 640),
        update_timeout_sec=_read_int("ZISUYE_UPDATE_TIMEOUT", 600),
        backup_daily_keep=_read_int("ZISUYE_BACKUP_DAILY_KEEP", 7),
        backup_weekly_keep=_read_int("ZISUYE_BACKUP_WEEKLY_KEEP", 8),
        backup_premigration_keep=_read_int("ZISUYE_BACKUP_PREMIGRATION_KEEP", 5),
    )


# 全局单例
settings = _load_settings()


def reload_settings() -> AppSettings:
    """重新加载配置（测试用）。"""
    global settings
    settings = _load_settings()
    return settings
