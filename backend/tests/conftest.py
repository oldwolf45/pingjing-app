from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db import session  # noqa: E402


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    """每个测试一个独立数据目录，互不干扰。"""
    monkeypatch.setenv("ZISUYE_DATA_DIR", str(tmp_path))
    session.reset_connections()
    # legacy_bridge.load() 有 lru_cache，数据目录变了必须重新重绑路径
    from app import legacy_bridge
    legacy_bridge.reset()
    from app.services import intel
    intel._patched = False
    yield tmp_path
    session.reset_connections()
    legacy_bridge.reset()


@pytest.fixture()
def conn(data_home):
    c = session.connect()
    session.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def source_dir() -> Path:
    """旧项目的源数据目录。"""
    return Path("D:/云吞下载/紫苏叶跟踪")
