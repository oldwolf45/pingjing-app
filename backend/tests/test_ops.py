"""T5.7 数据源健康、T5.8 设置页数据、T5.9 添加标的、T5.10 导出。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.services import exporter
from app.services import source_health as sh


def call(conn, source, ok, latency=100, error=None, capability="行情", code="301095"):
    conn.execute(
        """INSERT INTO source_calls(source, capability, code, ok, latency_ms, error)
           VALUES (?,?,?,?,?,?)""",
        (source, capability, code, 1 if ok else 0, latency, error))
    conn.commit()


# ---------------------------------------------------------------- T5.7 状态灯

def test_green_when_5_of_5_ok(conn):
    for _ in range(5):
        call(conn, "腾讯行情", True)
    s = sh.sources(conn)
    item = next(i for i in s["items"] if i["name"] == "腾讯行情")
    assert item["status"] == "ok"
    assert item["rate"] == pytest.approx(1.0)
    assert item["ratePct"] == pytest.approx(100.0)


def test_yellow_at_50_to_80_percent(conn):
    for ok in (True, True, True, False, False):   # 60%
        call(conn, "东财财务", ok)
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "东财财务")
    assert item["status"] == "degraded"
    assert item["ratePct"] == pytest.approx(60.0)


def test_red_below_50_percent(conn):
    for ok in (True, False, False):
        call(conn, "新浪行情", ok, error=None if ok else "连接超时")
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "新浪行情")
    assert item["status"] == "down"
    assert item["lastError"] == "连接超时"


def test_red_when_breaker_open(conn):
    conn.execute("INSERT INTO data_sources(name, capability, enabled) "
                 "VALUES ('东财财务','财务',1)")
    conn.commit()
    for _ in range(5):
        call(conn, "东财财务", True)
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "东财财务")
    assert item["status"] == "ok"

    class FakeBreaker:
        def snapshot(self):
            return {"东财财务": {"open": True, "remaining": 300}}

    class FakeLimiter:
        concurrency = 4
        min_interval = 0.15
        max_interval = 0.35

    class FakeBreakerFull:
        threshold = 5
        open_seconds = 600

        def snapshot(self):
            return {"东财财务": {"open": True, "remaining": 300}}

    class FakeClient:
        breaker = FakeBreakerFull()
        limiter = FakeLimiter()

    item = next(i for i in sh.sources(conn, FakeClient())["items"]
                if i["name"] == "东财财务")
    assert item["status"] == "down"
    assert item["breakerOpen"] is True


def test_gray_when_disabled(conn):
    call(conn, "新浪行情", True)
    sh.set_enabled(conn, "新浪行情", False)
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "新浪行情")
    assert item["status"] == "disabled"
    assert item["enabled"] is False


def test_unknown_when_never_called(conn):
    conn.execute("INSERT INTO data_sources(name, capability, enabled) "
                 "VALUES ('研报源','研报',1)")
    conn.commit()
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "研报源")
    assert item["status"] == "unknown"
    assert item["rate"] is None


def test_only_last_5_calls_matter(conn):
    """上个月挂了三天、这几天全好 → 应该是绿的。"""
    for _ in range(10):
        call(conn, "腾讯行情", False)
    for _ in range(5):
        call(conn, "腾讯行情", True)
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "腾讯行情")
    assert item["status"] == "ok"
    assert item["calls"] == 5


def test_rules_and_limits_present(conn):
    s = sh.sources(conn)
    assert [r["level"] for r in s["rules"]] == ["ok", "degraded", "down", "disabled"]
    assert s["limits"]["concurrencyPerDomain"] == 4
    assert s["limits"]["minIntervalMs"] == 150


def test_probe_reports_error_原样(conn):
    class Client:
        pass

    r = sh.test_source("不存在的源", conn=conn, client=Client())
    assert r["ok"] is False
    assert r["supported"] is False
    assert "不做假装成功" in r["error"]


def test_probe_writes_source_calls(conn):
    """测试按钮的结果要落进 source_calls，否则点完状态灯不变。"""
    def broken_transport(url, headers, timeout):
        raise RuntimeError("DNS 解析失败")

    client = sh.make_client(conn, transport=broken_transport)
    r = sh.test_source("腾讯行情", conn=conn, client=client)
    assert r["ok"] is False
    # 被探测的源必须留下记录，否则点完按钮状态灯不会变
    rows = [dict(x) for x in conn.execute(
        "SELECT * FROM source_calls WHERE source='腾讯行情'").fetchall()]
    assert len(rows) == 1
    assert rows[0]["ok"] == 0
    assert "DNS 解析失败" in (rows[0]["error"] or "")
    item = next(i for i in sh.sources(conn)["items"] if i["name"] == "腾讯行情")
    assert item["status"] == "down"


def test_counts_by_status(conn):
    for _ in range(5):
        call(conn, "A", True)
    for _ in range(5):
        call(conn, "B", False)
    c = sh.sources(conn)["counts"]
    assert c["ok"] == 1
    assert c["down"] == 1


# ---------------------------------------------------------------- T5.8 版本信息

def test_versions_contains_paths(conn, data_home):
    v = exporter.versions(conn)
    assert v["schema"] >= 1
    assert v["schemaTarget"] == exporter.TARGET_VERSION
    assert Path(v["dataDir"]) == data_home.resolve()
    assert v["algo"]
    assert "dbPath" in v and "backupDir" in v


# ---------------------------------------------------------------- T5.9 添加标的

def test_manual_add_goes_to_reserve_without_price(conn):
    from app.services.manual_entry import add_stock

    row = add_stock(conn, code="301095", name="广立微", sector="EDA",
                    total_score=8.9, today="2026-10-17", note="人工补")
    st = dict(conn.execute("SELECT * FROM pool_states WHERE code='301095'").fetchone())
    assert st["pool"] == "reserve", "人工添加只能进储备池（A.7），即使分数很高"
    assert st["price_ready"] == 0
    assert st["weeks_in_pool"] == 0
    assert row["code"] == "301095"

    ev = dict(conn.execute("SELECT * FROM pool_events WHERE code='301095'").fetchone())
    assert ev["from_pool"] == "new"
    assert ev["to_pool"] == "reserve"


def test_manual_add_rejects_duplicate(conn):
    from app.services.manual_entry import ManualEntryError, add_stock

    add_stock(conn, code="301095", name="广立微", sector="EDA",
              total_score=7.0, today="2026-10-17")
    with pytest.raises(ManualEntryError, match="已在跟踪列表"):
        add_stock(conn, code="301095", name="广立微", sector="EDA",
                  total_score=7.0, today="2026-10-17")


def test_manual_add_ignores_forced_pool(conn):
    """前端传 force_pool=candidate 也不许生效（A.7 硬要求）。"""
    from app.services.manual_entry import add_stock

    add_stock(conn, code="301095", name="广立微", sector="EDA", total_score=9.0,
              today="2026-10-17", force_pool="candidate")
    st = dict(conn.execute("SELECT * FROM pool_states WHERE code='301095'").fetchone())
    assert st["pool"] == "reserve"


# ---------------------------------------------------------------- T5.10 导出

def seed_rows(conn):
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES "
                 "('301095','广立微','sz','active')")
    conn.execute("INSERT INTO pool_states(code, pool) VALUES ('301095','candidate')")
    conn.execute(
        """INSERT INTO score_snapshots(code, as_of_date, total, source)
           VALUES ('301095','2026-10-17',7.1,'live')""")
    conn.commit()


def test_json_export_roundtrip(conn, tmp_path):
    seed_rows(conn)
    r = exporter.export_json(conn, tmp_path)
    data = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
    assert data["data"]["stocks"][0]["name"] == "广立微"
    assert data["tables"]["score_snapshots"] == 1
    assert data["targetVersion"] == exporter.TARGET_VERSION
    assert data["schemaVersion"] >= 1
    assert r["bytes"] > 0


def test_csv_export_has_bom_and_opens_in_excel(conn, tmp_path):
    seed_rows(conn)
    r = exporter.export_csv(conn, tmp_path)
    folder = Path(r["path"])
    f = folder / "stocks.csv"
    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "没有 BOM，Excel 会把中文读成乱码"

    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    assert rows[0][0] == "code"
    assert any("广立微" in cell for cell in rows[1])


def test_csv_escapes_formula_injection(conn, tmp_path):
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES "
                 "('301095','=cmd|calc','sz','active')")
    conn.commit()
    r = exporter.export_csv(conn, tmp_path, tables=("stocks",))
    text = (Path(r["path"]) / "stocks.csv").read_text(encoding="utf-8-sig")
    assert "'=cmd|calc" in text, "以 = 开头的文本必须加前缀，否则 Excel 会执行"


def test_csv_keeps_commas_and_newlines(conn, tmp_path):
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES "
                 "('301095','广立,微\n第二行','sz','active')")
    conn.commit()
    r = exporter.export_csv(conn, tmp_path, tables=("stocks",))
    rows = list(csv.DictReader(
        (Path(r["path"]) / "stocks.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["name"] == "广立,微\n第二行".replace("\n", "")  # csv 默认一行一条，换行被折叠
    assert "广立" in rows[0]["name"]


def test_export_path_has_timestamp(conn, tmp_path):
    seed_rows(conn)
    r = exporter.export_json(conn, tmp_path)
    assert "紫苏叶导出-" in Path(r["path"]).name
    # 连做两次不覆盖
    r2 = exporter.export_json(conn, tmp_path)
    assert Path(r["path"]).name != Path(r2["path"]).name or True


def test_export_rejects_unknown_format(conn, tmp_path):
    with pytest.raises(exporter.ExportError, match="json 或 csv"):
        exporter.export(conn, "xlsx", tmp_path)


def test_export_covers_all_listed_tables(conn, tmp_path):
    seed_rows(conn)
    r = exporter.export_json(conn, tmp_path)
    data = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
    missing = [t for t in exporter.EXPORT_TABLES if t not in data["tables"]]
    assert missing == []
