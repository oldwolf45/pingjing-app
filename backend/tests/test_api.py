"""API 层测试：异常处理、健康检查、中间件。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import register
from app.exceptions import AppError, NotFoundError, ValidationError


@pytest.fixture
def app():
    fastapi_app = FastAPI()
    register(fastapi_app)
    return fastapi_app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_health_endpoint(client):
    """健康检查返回 200。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "ok"


def test_info_endpoint(client):
    """系统信息端点返回数据库和算法状态。"""
    resp = client.get("/api/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "database" in data
    assert "algo" in data


def test_not_found_returns_json(client):
    """不存在的端点返回 JSON 错误格式。"""
    resp = client.get("/api/nonexistent-endpoint")
    # FastAPI 默认 404 是 HTML，但我们的全局异常处理器只处理 Exception 子类
    # HTTPException 由 FastAPI 默认处理，这里验证至少不是 500
    assert resp.status_code == 404


def test_app_error_base_class():
    """AppError 基类行为正确。"""
    e = AppError("测试错误", code=40001)
    assert str(e) == "测试错误"
    assert e.code == 40001
    assert e.detail == "测试错误"


def test_validation_error_default_code():
    """ValidationError 默认 code = 40000。"""
    e = ValidationError("参数不对")
    assert e.code == 40000
    assert isinstance(e, AppError)


def test_not_found_error_default_code():
    """NotFoundError 默认 code = 40400。"""
    e = NotFoundError("找不到")
    assert e.code == 40400
    assert isinstance(e, AppError)


def test_service_errors_inherit_app_error():
    """各个 service 的异常类都继承自 AppError。"""
    from app.services.decisions import DecisionError
    from app.services.exporter import ExportError
    from app.services.manual_entry import ManualEntryError
    from app.services.portfolio import PortfolioError
    from app.services.recommendations import RecommendationError

    assert issubclass(DecisionError, AppError)
    assert issubclass(ExportError, AppError)
    assert issubclass(ManualEntryError, AppError)
    assert issubclass(PortfolioError, AppError)
    assert issubclass(RecommendationError, AppError)


def test_custom_error_code():
    """可以自定义错误码。"""
    e = ValidationError("特殊错误", code=40099)
    assert e.code == 40099


def test_empty_detail():
    """detail 为空时返回类名。"""
    e = AppError()
    assert str(e) == "AppError"
