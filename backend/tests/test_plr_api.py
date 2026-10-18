"""PLR API 测试：/api/stocks/{code}/plr。

算法本身已在 test_plr.py 钉死，这里只验"接口把数据源和算法正确接起来"——
mock 掉行情/日K源（不打真实网络），验路由、404、数据缺失时的降级。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.sources.base import SourceResult
from app.api import stocks as stocks_api


@pytest.fixture()
def client(data_home):
    return TestClient(create_app())


def seed(conn, code="301095"):
    conn.execute("INSERT OR IGNORE INTO stocks(code, name, market, status) VALUES (?,?,?,?)",
                 (code, "广立微", "sz", "active"))
    conn.execute("INSERT OR IGNORE INTO pool_states(code, pool, weeks_in_pool, price_ready) "
                 "VALUES (?,?,1,1)", (code, "candidate"))
    conn.commit()


def _ok(source, data):
    return SourceResult(ok=True, source=source, data=data)


def _fail(source, err):
    return SourceResult(ok=False, source=source, error=err)


def _trend_klines(start, n, step, low_zone, high_zone):
    """构造 200 根日K，量在 low_zone 以下和 high_zone 以上放大。"""
    rows = []
    for i in range(n):
        c = round(start + i * step, 2)
        v = 200.0 if (c <= low_zone or c >= high_zone) else 1.0
        rows.append({"date": f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}",
                     "open": c, "close": c, "high": c, "low": c, "volume": v})
    return rows


def test_plr_unknown_code_404(client, conn):
    r = client.get("/api/stocks/999999/plr")
    assert r.status_code == 404


def test_plr_happy_path(client, conn, monkeypatch):
    seed(conn)
    klines = _trend_klines(5, 200, 0.08, low_zone=10, high_zone=18)
    monkeypatch.setattr(stocks_api, "make_client", lambda conn: object())
    monkeypatch.setattr(stocks_api, "fetch_quote",
                        lambda c, code: _ok("行情", {"price": 14.0}))
    monkeypatch.setattr(stocks_api, "fetch_kline",
                        lambda c, code, count=0: _ok("日K", klines))

    r = client.get("/api/stocks/301095/plr")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "301095"
    assert body["price"] == 14.0
    assert body["data_ok"] is True
    assert body["plr"] is not None and body["plr"] > 0
    assert body["s1"] is not None and body["s1"] <= 10.5
    assert body["w2"] is not None and body["w2"] >= 17
    # 复核公式
    assert body["plr"] == round((body["w2"] - 14.0) / (14.0 - body["s1"]), 2)


def test_plr_quote_source_fails(client, conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(stocks_api, "make_client", lambda conn: object())
    monkeypatch.setattr(stocks_api, "fetch_quote",
                        lambda c, code: _fail("行情", "连接超时"))
    monkeypatch.setattr(stocks_api, "fetch_kline",
                        lambda c, code, count=0: _ok("日K", _trend_klines(5, 200, 0.08, 10, 18)))

    r = client.get("/api/stocks/301095/plr")
    body = r.json()
    assert body["data_ok"] is False
    assert body["plr"] is None
    assert "行情源失败" in body["error"]


def test_plr_kline_source_fails(client, conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(stocks_api, "make_client", lambda conn: object())
    monkeypatch.setattr(stocks_api, "fetch_quote",
                        lambda c, code: _ok("行情", {"price": 14.0}))
    monkeypatch.setattr(stocks_api, "fetch_kline",
                        lambda c, code, count=0: _fail("日K", "解析为空"))

    r = client.get("/api/stocks/301095/plr")
    body = r.json()
    assert body["data_ok"] is False
    assert body["plr"] is None
    assert "日K源失败" in body["error"]


def test_plr_insufficient_kline_bars(client, conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(stocks_api, "make_client", lambda conn: object())
    monkeypatch.setattr(stocks_api, "fetch_quote",
                        lambda c, code: _ok("行情", {"price": 14.0}))
    # 只给 20 根，低于 MIN_BARS=30
    monkeypatch.setattr(stocks_api, "fetch_kline",
                        lambda c, code, count=0: _ok("日K", _trend_klines(5, 20, 0.1, 0, 99)))

    r = client.get("/api/stocks/301095/plr")
    body = r.json()
    assert body["plr"] is None
    assert body["bars"] == 20
