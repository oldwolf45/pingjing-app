"""应用异常基类。

所有业务异常都继承 AppError，这样全局异常处理器能统一捕获。

错误码约定：
  400xx - 参数错误 / 业务校验失败
  404xx - 资源不存在
  409xx - 状态冲突 / 重复操作
  500xx - 内部错误
"""

from __future__ import annotations


class AppError(RuntimeError):
    """应用层异常基类。

    Attributes:
        code: 业务错误码，5 位数字（HTTP 状态码 + 3 位细分）
        detail: 人类可读的错误信息
    """

    code: int = 50000

    def __init__(self, detail: str = "", code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code

    def __str__(self) -> str:
        return self.detail or self.__class__.__name__


class NotFoundError(AppError):
    """资源不存在。"""
    code = 40400


class ValidationError(AppError):
    """参数校验失败 / 业务规则不满足。"""
    code = 40000


class ConflictError(AppError):
    """状态冲突（重复操作、前置条件不满足等）。"""
    code = 40900


class OperationError(AppError):
    """操作失败（通用业务错误）。"""
    code = 40001
