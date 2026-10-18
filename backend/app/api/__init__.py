"""API 路由聚合。"""
from fastapi import FastAPI

from app.api import (
    dashboard,
    decisions,
    ops,
    portfolio,
    recommendations,
    stocks,
    update,
)


def register(app: FastAPI) -> None:
    app.include_router(dashboard.router)
    app.include_router(stocks.router)
    app.include_router(decisions.router)
    app.include_router(portfolio.router)
    app.include_router(recommendations.router)
    app.include_router(ops.router)
    app.include_router(update.router)
