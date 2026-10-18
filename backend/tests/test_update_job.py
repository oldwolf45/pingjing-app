"""T4.5 后端部分：更新任务（后台线程 + 进度落库 + 取消）。

进度面板能不能用，全看这三个接口靠不靠谱，所以这里把
「起一轮 → 轮询到结束」「重复点更新」「取消」都钉死。
数据源全部离线（FakeSources），不碰网络。
"""

from __future__ import annotations

import threading

import pytest

from app.services import update_job
from app.services.update_job import UpdateJobManager, percent_of
from tests.test_updater import (  # noqa: F401  复用编排层的假数据源
    FakeSources,
    quote_payload,
    seed,
)


@pytest.fixture()
def fresh_manager(data_home):
    """每个测试一个独立的任务管理器，别共享全局单例的状态。"""
    m = UpdateJobManager(sources_factory=lambda conn: FakeSources())
    yield m
    m.wait(10)


# ---------------------------------------------------------------- 进度换算

def test_percent_covers_all_stages():
    assert percent_of("quote", 0, 36) == 0
    assert percent_of("quote", 36, 36) == 14        # 1/7
    assert percent_of("pool", 0, 36) == 71          # 5/7
    assert percent_of("persist", 36, 36) == 100
    assert percent_of("persist", 18, 36) == 93
    assert percent_of(None, 5, 10) == 0
    assert percent_of("quote", 0, 0) == 0


# ---------------------------------------------------------------- 起一轮、跑完

def test_start_and_finish(conn, fresh_manager):
    seed(conn, "301095")
    seed(conn, "301269")

    r = fresh_manager.start(as_of_date="2026-10-17")
    assert r["ok"] is True
    assert r["jobId"] > 0
    assert fresh_manager.wait(15)

    st = fresh_manager.status()
    assert st["status"] == "ok"
    assert st["percent"] == 100
    assert st["total"] == 2
    assert st["current"] == 2
    assert st["stage"] == "persist"
    assert st["stageIndex"] == 7
    assert st["stageCount"] == 7
    assert st["finishedAt"]
    assert st["result"]["okCount"] == 2
    assert "works" not in st["result"]          # 列表接口不带明细，别把响应撑爆

    row = dict(conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone())
    assert row["status"] == "ok"
    assert row["kind"] == "manual"
    assert "完成" in row["message"]


def test_progress_is_visible_while_running(conn):
    """跑的过程中另开一条连接也能看到进度——进度必须提交到库，不能只在内存。

    第一只票跑完就把「行情 1/2」提交掉，然后卡在第二只票上。
    此时用同一条外部连接去读，必须看到 stage=quote、current>=1。
    """
    gate = threading.Event()

    class BlockingSources(FakeSources):
        def quote(self, code):
            if code == "301269":
                gate.wait(5)
            return super().quote(code)

    m = UpdateJobManager(sources_factory=lambda c: BlockingSources())
    seed(conn, "301095")
    seed(conn, "301269")
    m.start(as_of_date="2026-10-17")
    try:
        deadline = threading.Event()
        seen = None
        for _ in range(200):                     # 最多等 4 秒
            seen = dict(conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone())
            if seen["status"] == "running" and (seen["progress"] or 0) >= 1:
                break
            deadline.wait(0.02)
        assert seen["status"] == "running"
        assert seen["stage"] == "quote"
        assert seen["progress"] >= 1, f"另一条连接看不到进度：{seen}"
        assert seen["total"] == 2
    finally:
        gate.set()
        m.wait(10)

    assert m.status()["status"] == "ok"


def test_status_idle_when_no_job(conn):
    m = UpdateJobManager(sources_factory=lambda c: FakeSources())
    st = m.status()
    assert st["status"] == "idle"
    assert st["percent"] == 0
    assert st["stageCount"] == 7
    assert st["result"] is None


# ---------------------------------------------------------------- 重复点

def test_second_start_is_rejected(conn):
    gate = threading.Event()

    class BlockingSources(FakeSources):
        def quote(self, code):
            gate.wait(5)
            return super().quote(code)

    m = UpdateJobManager(sources_factory=lambda c: BlockingSources())
    seed(conn, "301095")
    first = m.start(as_of_date="2026-10-17")
    assert first["ok"] is True

    second = m.start(as_of_date="2026-10-17")
    assert second["ok"] is False
    assert second["reason"] == "already_running"
    assert second["status"]["status"] == "running"

    gate.set()
    assert m.wait(10)
    # 只留一条 running/ok 的任务行，没有第二条任务被创建
    rows = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id")]
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


# ---------------------------------------------------------------- 取消

def test_cancel_stops_run_without_partial_data(conn):
    gate = threading.Event()

    class BlockingSources(FakeSources):
        def quote(self, code):
            gate.wait(5)
            return super().quote(code)

    m = UpdateJobManager(sources_factory=lambda c: BlockingSources())
    seed(conn, "301095")
    m.start(as_of_date="2026-10-17")
    r = m.cancel()
    assert r["ok"] is True
    assert r["status"]["cancelRequested"] is True

    gate.set()
    assert m.wait(10)

    st = m.status()
    assert st["status"] == "cancelled"
    assert st["percent"] == 100
    assert conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pool_events").fetchone()[0] == 0


def test_cancel_when_idle(conn):
    m = UpdateJobManager(sources_factory=lambda c: FakeSources())
    r = m.cancel()
    assert r["ok"] is False
    assert r["reason"] == "not_running"


# ---------------------------------------------------------------- 失败不外逸

def test_source_exception_becomes_failed_job(conn):
    class Exploding(FakeSources):
        def quote(self, code):
            raise RuntimeError("连接被重置")

    m = UpdateJobManager(sources_factory=lambda c: Exploding())
    seed(conn, "301095")
    # 编排层已经把单源异常兜住了，所以这里应该照常跑完、只是该票无行情
    assert m.start(as_of_date="2026-10-17")["ok"] is True
    assert m.wait(10)
    assert m.status()["status"] == "ok"


def test_factory_exception_marks_failed(conn):
    def boom(_conn):
        raise RuntimeError("数据源初始化失败")

    m = UpdateJobManager(sources_factory=boom)
    seed(conn, "301095")
    assert m.start(as_of_date="2026-10-17")["ok"] is True
    assert m.wait(10)
    st = m.status()
    assert st["status"] == "failed"
    assert "数据源初始化失败" in st["message"]


# ---------------------------------------------------------------- API 层

def test_api_endpoints(conn, data_home, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    seed(conn, "301095")
    m = UpdateJobManager(sources_factory=lambda c: FakeSources())
    monkeypatch.setattr(update_job, "manager", m)

    client = TestClient(create_app())

    st = client.get("/api/update/status").json()
    assert st["status"] in ("idle", "running", "ok")

    r = client.post("/api/update/start", json={"asOfDate": "2026-10-17"}).json()
    assert r["ok"] is True
    assert m.wait(15)

    st = client.get("/api/update/status").json()
    assert st["status"] == "ok"
    assert st["stageCount"] == 7

    c = client.post("/api/update/cancel").json()
    assert c["ok"] is False      # 已经跑完了，没什么可取消的


def test_api_start_accepts_empty_body(conn, data_home, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    m = UpdateJobManager(sources_factory=lambda c: FakeSources())
    monkeypatch.setattr(update_job, "manager", m)
    client = TestClient(create_app())
    r = client.post("/api/update/start")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    m.wait(10)
