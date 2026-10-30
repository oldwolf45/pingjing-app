"""算法模块的 print 输出捕获到日志系统。

算法模块（app/algo/）是原样拷贝的，有指纹锁死，不能直接改代码替换 print。
方案：在调用算法核心函数前，把 sys.stdout 临时替换为 logger 包装器，
把 print 输出重定向到 logging.INFO 级别。

用法：
    from app.utils.algo_logger import capture_algo_prints

    with capture_algo_prints():
        # 这里的所有 print 都会变成 logger.info
        algo.some_function()
"""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager
from typing import Iterator

from app.utils.logger import get_logger

_logger = get_logger("algo")


class _PrintToLogger(io.StringIO):
    """把 print 输出转发到 logger。

    print("xxx") 会调用 sys.stdout.write("xxx\n")，
    我们在 write() 里按行切分，每行一条日志。
    """

    def __init__(self, logger, level: int):
        super().__init__()
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        # 按行处理
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():  # 跳过空行
                self._logger.log(self._level, line)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.log(self._level, self._buf.rstrip())
            self._buf = ""


@contextmanager
def capture_algo_prints(level: int | None = None) -> Iterator[None]:
    """上下文管理器：把当前线程的 print 输出捕获到日志。

    Args:
        level: 日志级别，默认 INFO。算法模块的输出主要是进度信息。

    示例：
        with capture_algo_prints():
            manager.update_all_scores()  # 里面的 print 全变 logger.info
    """
    import logging as _logging
    log_level = level if level is not None else _logging.INFO

    old_stdout = sys.stdout
    sys.stdout = _PrintToLogger(_logger, log_level)
    try:
        yield
    finally:
        sys.stdout.flush() if hasattr(sys.stdout, "flush") else None
        sys.stdout = old_stdout


def get_algo_logger():
    """获取算法模块专用 logger（name=app.algo）。"""
    return _logger
