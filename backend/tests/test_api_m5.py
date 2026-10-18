"""M5 接口层测试：路由能通、错误码对、口径不回退。

服务层已经逐条钉过数值，这里只验"接口把服务正确暴露出来"——
参数名对不上、异常没转成 400、只追加表多出改删路由，都算失败。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(data_home):
    return TestClient(create_app())


def seed(conn, code="301095", name="广立微", pool="candidate"):
    conn.execute("INSERT OR IGNORE INTO stocks(code, name, market, status) VALUES (?,?,?,?)",
                 (code, name, "sz", "active"))
    conn.execute("INSERT OR IGNORE INTO stock_profiles(code, sector) VALUES (?,?)",
                 (code, "EDA"))
    conn.execute("INSERT OR IGNORE INTO pool_states(code, pool, weeks_in_pool, price_ready) "
                 "VALUES (?,?,1,1)", (code, pool))
    conn.commit()


# ---------------------------------------------------------------- 决策

def test_decisions_roundtrip(client, conn):
    seed(conn)
    r = client.get("/api/decisions?week=2026-W42")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.post("/api/decisions", json={
        "code": "301095", "decision": "add", "note": "回调", "week": "2026-W42"})
    assert r.status_code == 200, r.text
    assert r.json()["decisionLabel"] == "加仓"

    r = client.get("/api/decisions?week=2026-W42")
    assert r.json()["decided"] == 1


def test_decisions_rejects_bad_value(client, conn):
    seed(conn)
    r = client.post("/api/decisions", json={"code": "301095", "decision": "buy"})
    assert r.status_code == 400
    assert "只能是" in r.json()["detail"]


def test_decision_options(client):
    r = client.get("/api/decisions/options")
    assert [i["value"] for i in r.json()["items"]] == ["add", "hold", "trim", "watch"]


# ---------------------------------------------------------------- 持仓

def test_portfolio_trade_flow(client, conn):
    seed(conn)
    client.post("/api/portfolio/cash", json={"cashCents": 10_000_000})

    r = client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "BUY", "shares": 1000, "priceCents": 1000,
        "feeCents": 500, "tradeDate": "2026-10-10"})
    assert r.status_code == 200, r.text
    assert r.json()["valuation"]["positions"][0]["costMilli"] == 1_000_500

    r = client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "BUY", "shares": 1000, "priceCents": 1200,
        "feeCents": 500, "tradeDate": "2026-10-11"})
    pos = r.json()["valuation"]["positions"][0]
    assert pos["costMilli"] == 1_100_500
    assert pos["costPrice"] == pytest.approx(11.005)

    r = client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "SELL", "shares": 500, "priceCents": 1300,
        "feeCents": 500, "tradeDate": "2026-10-12"})
    pos = r.json()["valuation"]["positions"][0]
    assert pos["costMilli"] == 1_100_500, "卖出后成本价不许变"


def test_portfolio_rejects_oversell(client, conn):
    seed(conn)
    client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "BUY", "shares": 100, "priceCents": 1000})
    r = client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "SELL", "shares": 200, "priceCents": 1000})
    assert r.status_code == 400
    assert "超过持仓" in r.json()["detail"]


def test_portfolio_void(client, conn):
    seed(conn)
    r = client.post("/api/portfolio/trades", json={
        "code": "301095", "side": "BUY", "shares": 100, "priceCents": 1000})
    tid = r.json()["trade"]["id"]
    r = client.post("/api/portfolio/void", json={"tradeId": tid})
    assert r.status_code == 200
    assert r.json()["valuation"]["positions"] == []


def test_portfolio_overview_shape(client, conn):
    seed(conn)
    d = client.get("/api/portfolio").json()
    for k in ("positions", "trades", "cashCents", "totalAssetsCents",
              "alertPct", "overLimit", "incomplete"):
        assert k in d
    assert d["alertPct"] == 10.0


# ---------------------------------------------------------------- 推荐

def test_recommendations_append_only_routes(client, conn):
    seed(conn)
    r = client.post("/api/recommendations", json={"code": "301095",
                                                  "asOfDate": "2026-10-17"})
    assert r.status_code == 200
    rid = r.json()["id"]

    # 没有改和删的路由：404 而不是 200
    assert client.put(f"/api/recommendations/{rid}", json={}).status_code in (404, 405)
    assert client.delete(f"/api/recommendations/{rid}").status_code in (404, 405)


def test_recommendation_stats_sample_note(client, conn):
    seed(conn)
    client.post("/api/recommendations", json={"code": "301095", "asOfDate": "2026-10-17"})
    s = client.get("/api/recommendations/stats").json()
    assert s["total"] == 1
    assert s["tiers"]["hard_miss"]["rate"] is None
    assert s["tiers"]["hard_miss"]["sampleNote"] == "样本不足 0 / 需要 10"
    assert s["byScoreBand"]["available"] is False


# ---------------------------------------------------------------- 数据源

def test_sources_page(client, conn):
    conn.execute("INSERT INTO source_calls(source, capability, ok, latency_ms) "
                 "VALUES ('腾讯行情','行情',1,88)")
    conn.commit()
    d = client.get("/api/ops/sources").json()
    assert d["rules"]
    assert d["limits"]["concurrencyPerDomain"] == 4
    item = next(i for i in d["items"] if i["name"] == "腾讯行情")
    assert item["status"] == "ok"


def test_source_toggle(client, conn):
    conn.execute("INSERT INTO source_calls(source, capability, ok) VALUES ('新浪行情','行情',1)")
    conn.commit()
    r = client.post("/api/ops/sources/toggle", json={"name": "新浪行情", "enabled": False})
    assert r.status_code == 200
    item = next(i for i in client.get("/api/ops/sources").json()["items"]
                if i["name"] == "新浪行情")
    assert item["status"] == "disabled"


# ---------------------------------------------------------------- 设置 / 导出

def test_settings_and_privacy_promise(client):
    d = client.get("/api/ops/settings").json()
    assert d["privacy"]["localOnly"] is True
    assert "不上传" in d["privacy"]["text"]
    assert d["versions"]["algo"]


def test_backup_and_restore_roundtrip(client, conn):
    seed(conn)
    r = client.post("/api/ops/backup")
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    d = client.get("/api/ops/settings").json()
    assert any(b["id"] == bid for b in d["backups"])

    r = client.post("/api/ops/restore", json={"id": bid})
    assert r.status_code == 200


def test_export_json_and_csv(client, conn, tmp_path, monkeypatch):
    seed(conn)
    r = client.post("/api/ops/export?fmt=json")
    assert r.status_code == 200
    assert r.json()["format"] == "json"

    r = client.post("/api/ops/export?fmt=csv")
    assert r.status_code == 200
    assert r.json()["format"] == "csv"

    r = client.post("/api/ops/export?fmt=xlsx")
    assert r.status_code == 400


# ---------------------------------------------------------------- 添加标的

def test_add_stock_forces_reserve(client, conn):
    r = client.post("/api/ops/stocks", json={
        "code": "300124", "name": "汇川技术", "sector": "工控",
        "totalScore": 9.0, "forcePool": "candidate"})
    assert r.status_code == 200, r.text
    st = dict(conn.execute("SELECT * FROM pool_states WHERE code='300124'").fetchone())
    assert st["pool"] == "reserve"
    assert st["price_ready"] == 0


def test_add_stock_duplicate_400(client, conn):
    seed(conn)
    r = client.post("/api/ops/stocks", json={"code": "301095", "name": "广立微"})
    assert r.status_code == 400
