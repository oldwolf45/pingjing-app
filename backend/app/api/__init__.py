"""API 路由聚合。"""
from fastapi import FastAPI

from app.api import (
    dashboard,
    decisions,
    health,
    ops,
    portfolio,
    recommendations,
    stocks,
    update,
)
from app.api.middleware import register_exception_handlers, register_middleware


def register(app: FastAPI) -> None:
    # 中间件与异常处理
    register_middleware(app)
    register_exception_handlers(app)

    # 路由
    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(stocks.router)
    app.include_router(decisions.router)
    app.include_router(portfolio.router)
    app.include_router(recommendations.router)
    app.include_router(ops.router)
    app.include_router(update.router)
