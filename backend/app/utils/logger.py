"""统一日志配置。

统一输出格式、级别、文件轮转。所有模块通过 get_logger(name) 获取日志实例，
禁止直接使用 print() 输出运行信息。

用法：
    from app.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("启动成功")
    logger.debug("详细调试信息")  # 默认不显示
    logger.warning("异常但可继续")
    logger.error("失败，需要关注")
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

from app import paths

# 日志格式：[时间] [级别] [模块名] 消息
_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 全局初始化标记
_initialized = False
_root_level = logging.INFO


def _ensure_dir(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)


def setup_logging(
    level: int | str = logging.INFO,
    log_file: Optional[str | Path] = None,
    console: bool = True,
    rotate_days: int = 30,
) -> None:
    """初始化全局日志配置。

    多次调用安全，只有第一次生效。

    Args:
        level: 日志级别，默认 INFO。可通过环境变量 ZISUYE_LOG_LEVEL 覆盖。
        log_file: 日志文件路径，默认 {data_dir}/logs/zisuye.log
        console: 是否输出到控制台
        rotate_days: 文件日志保留天数
    """
    global _initialized, _root_level

    if _initialized:
        return

    # 环境变量覆盖
    import os
    env_level = os.environ.get("ZISUYE_LOG_LEVEL", "").upper()
    if env_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = env_level

    _root_level = logging.getLevelName(level) if isinstance(level, str) else level

    root = logging.getLogger("app")
    root.setLevel(_root_level)
    root.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    # 控制台输出
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(_root_level)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    # 文件输出（按天轮转）
    if log_file is None:
        log_dir = paths.log_dir()
        _ensure_dir(log_dir)
        log_file = log_dir / "zisuye.log"
    else:
        log_file = Path(log_file)
        _ensure_dir(log_file.parent)

    try:
        fh = TimedRotatingFileHandler(
            str(log_file),
            when="midnight",
            interval=1,
            backupCount=rotate_days,
            encoding="utf-8",
        )
        fh.setLevel(_root_level)
        fh.setFormatter(formatter)
        fh.suffix = "%Y-%m-%d"
        root.addHandler(fh)
    except Exception:
        # 文件日志失败不影响程序运行，至少控制台能看到
        pass

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取命名日志实例。

    推荐传入 __name__ 作为模块名，这样日志里能看到是哪个模块输出的。
    """
    # 确保已初始化（测试或 CLI 直接调用时可能没走 setup_logging）
    if not _initialized:
        setup_logging()

    # 统一以 "app." 开头，和根 logger 的 handlers 对接
    if not name.startswith("app."):
        name = f"app.{name}"

    return logging.getLogger(name)


def set_level(level: int | str) -> None:
    """动态调整全局日志级别。"""
    global _root_level
    _root_level = logging.getLevelName(level) if isinstance(level, str) else level

    root = logging.getLogger("app")
    root.setLevel(_root_level)
    for handler in root.handlers:
        handler.setLevel(_root_level)
