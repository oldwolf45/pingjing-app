"""配置模块测试。"""

import os
import pytest

from app.utils.config import AppSettings, _load_settings, reload_settings, settings


def test_settings_singleton():
    """settings 是全局单例。"""
    from app.utils.config import settings as s2
    assert settings is s2


def test_default_values():
    """默认值正确。"""
    s = AppSettings()
    assert s.mode == "production"
    assert s.log_level == "INFO"
    assert s.debug is False
    assert s.single_instance is True
    assert s.window_width == 1280
    assert s.window_height == 800
    assert s.is_production is True
    assert s.is_development is False
    assert s.is_test is False


def test_read_env_mode(monkeypatch):
    """从环境变量读取 mode。"""
    monkeypatch.setenv("ZISUYE_MODE", "development")
    s = _load_settings()
    assert s.mode == "development"
    assert s.is_development is True
    assert s.is_production is False


def test_read_env_debug(monkeypatch):
    """从环境变量读取 debug。"""
    monkeypatch.setenv("ZISUYE_DEBUG", "1")
    s = _load_settings()
    assert s.debug is True

    monkeypatch.setenv("ZISUYE_DEBUG", "true")
    s = _load_settings()
    assert s.debug is True

    monkeypatch.setenv("ZISUYE_DEBUG", "0")
    s = _load_settings()
    assert s.debug is False


def test_read_env_log_level(monkeypatch):
    """从环境变量读取日志级别。"""
    monkeypatch.setenv("ZISUYE_LOG_LEVEL", "DEBUG")
    s = _load_settings()
    assert s.log_level == "DEBUG"


def test_read_int_env(monkeypatch):
    """从环境变量读取整数。"""
    monkeypatch.setenv("ZISUYE_WINDOW_WIDTH", "1920")
    monkeypatch.setenv("ZISUYE_WINDOW_HEIGHT", "1080")
    s = _load_settings()
    assert s.window_width == 1920
    assert s.window_height == 1080


def test_read_invalid_int(monkeypatch):
    """无效整数回退到默认值。"""
    monkeypatch.setenv("ZISUYE_WINDOW_WIDTH", "abc")
    s = _load_settings()
    assert s.window_width == 1280  # 回退默认


def test_http_proxy(monkeypatch):
    """HTTP 代理配置。"""
    monkeypatch.setenv("ZISUYE_HTTP_PROXY", "http://proxy:8080")
    s = _load_settings()
    assert s.http_proxy == "http://proxy:8080"


def test_backup_keep_settings(monkeypatch):
    """备份保留配置。"""
    monkeypatch.setenv("ZISUYE_BACKUP_DAILY_KEEP", "14")
    s = _load_settings()
    assert s.backup_daily_keep == 14


def test_data_dir_property():
    """data_dir 属性返回 Path 对象。"""
    from pathlib import Path
    assert isinstance(settings.data_dir, Path)
