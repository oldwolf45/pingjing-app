"""API 中间件与全局异常处理。

统一错误响应格式：
{
    "ok": false,
    "error": {
        "code": 40000,
        "message": "错误信息",
        "type": "ValidationError"
    }
}
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from app.utils.logger import get_logger

logger = get_logger("api")


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """应用层业务异常。"""
        # 4xx 级别打 warning，5xx 级别打 error
        if exc.code >= 50000:
            logger.error(f"{exc.code} {exc.detail}")
        else:
            logger.warning(f"{exc.code} {exc.detail}")

        http_status = exc.code // 100
        return JSONResponse(
            status_code=min(max(http_status, 400), 599),
            content={
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "type": exc.__class__.__name__,
                },
            },
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        logger.warning(f"参数错误: {exc.detail}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "type": "ValidationError",
                },
            },
        )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        logger.warning(f"资源不存在: {exc.detail}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "type": "NotFoundError",
                },
            },
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
        logger.warning(f"状态冲突: {exc.detail}")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "type": "ConflictError",
                },
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """未捕获的异常：打错误日志，返回 500。"""
        logger.exception(f"未处理的异常: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "ok": False,
                "error": {
                    "code": 50000,
                    "message": "服务器内部错误",
                    "type": "InternalServerError",
                },
            },
        )


def register_middleware(app: FastAPI) -> None:
    """注册中间件。"""

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        """记录每次请求的基本信息和耗时。"""
        import time
        start = time.time()
        try:
            response = await call_next(request)
            elapsed = (time.time() - start) * 1000
            logger.debug(
                f"{request.method} {request.url.path} "
                f"{response.status_code} {elapsed:.0f}ms"
            )
            return response
        except Exception:
            elapsed = (time.time() - start) * 1000
            logger.error(f"{request.method} {request.url.path} 异常 {elapsed:.0f}ms")
            raise
