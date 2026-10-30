"""导入器：真实源数据导入、幂等、报告完整性、口径核对。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import session
from app.services import backup, importer, migrator

SRC = Path("D:/云吞下载/紫苏叶跟踪")

# 整个文件依赖外部源数据目录，不存在时全部 skip
pytestmark = pytest.mark.skipif(
    not SRC.exists(),
    reason=f"缺少金样本源目录：{SRC}"
)


@pytest.fixture()
def imported(data_home):
    assert SRC.is_dir(), f"源数据目录不存在：{SRC}"
    c = session.connect()
    session.init_schema(c)
    migrator.run_migrations(c)
    report = importer.run_full_import(c, SRC)
    return c, report


# ---------- 纯函数 ----------

@pytest.mark.parametrize("text,expect", [
    ("72元", 72.0),
    ("31.7元", 31.7),
    ("120", 120.0),
    ("-", None),
    ("", None),
    (None, None),
    ("暂无", None),
])
def test_parse_price(text, expect):
    assert importer.parse_price(text) == expect


@pytest.mark.parametrize("code,market", [
    ("600000", "sh"), ("688981", "sh"), ("000001", "sz"),
    ("300750", "sz"), ("830799", "bj"), ("430047", "bj"),
])
def test_guess_market(code, market):
    assert importer.guess_market(code) == market


# ---------- 导入结果 ----------

def test_stock_counts(imported):
    conn, _ = imported
    v = importer.verify_import(conn)
    assert v["stocks_active"] == 36, "跟踪池应有 36 条记录"
    assert v["stocks_watch"] == 21, "sector_map 里不在池内的 21 只应为 watch"
    assert v["stocks"] == 57


def test_pool_distribution(imported):
    conn, _ = imported
    v = importer.verify_import(conn)
    assert v["pool_dist"] == {
        "candidate": 2, "observation": 11, "reserve": 15, "tuna": 5, "removed": 3,
    }
    assert v["in_pool"] == 28


def test_pool_events_19(imported):
    conn, _ = imported
    assert importer.verify_import(conn)["pool_events"] == 19


def test_sector_intel_31(imported):
    conn, _ = imported
    assert importer.verify_import(conn)["sector_intel"] == 31


def test_sector_map_57(imported):
    conn, _ = imported
    assert importer.verify_import(conn)["profiles_with_sector"] == 57


def test_every_active_stock_has_snapshot(imported):
    conn, _ = imported
    v = importer.verify_import(conn)
    assert v["stocks_without_snapshot"] == 0, "有票拿不到最新分，详情页会空白"


def test_snapshot_dates_cover_multiple_days(imported):
    conn, _ = imported
    v = importer.verify_import(conn)
    assert v["snapshot_dates"] >= 3, f"只覆盖 {v['snapshot_dates']} 个日期"
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of_date FROM score_snapshots ORDER BY 1").fetchall()]
    assert "2026-09-25" in dates
    assert "2026-10-02" in dates


def test_snapshot_scores_complete(imported):
    conn, _ = imported
    row = conn.execute(
        "SELECT * FROM v_score_current WHERE code='301095'").fetchone()
    assert row is not None
    assert row["track_barrier"] == pytest.approx(2.42)
    assert row["total"] == pytest.approx(7.1)
    fin = json.loads(row["financial"])
    assert fin["revenue_yoy"] == pytest.approx(38.68)
    assert row["total_mv_yi"] == pytest.approx(136.41)
    notes = json.loads(row["notes"])
    assert len(notes) >= 10


# ---------- 双存与裁剪 ----------

def test_buy_price_dual_stored(imported):
    conn, _ = imported
    row = conn.execute(
        "SELECT ideal_buy_text, ideal_buy_price FROM stock_profiles WHERE code='301095'"
    ).fetchone()
    assert row["ideal_buy_text"] == "72元", "原文必须原样保留"
    assert row["ideal_buy_price"] == pytest.approx(72.0), "同时要有解析值"


def test_position_capped_to_10(imported):
    conn, _ = imported
    row = conn.execute(
        "SELECT position_suggest_raw, position_suggest_cap, position_capped "
        "FROM stock_profiles WHERE code='301095'").fetchone()
    assert row["position_suggest_raw"] == pytest.approx(15.0)
    assert row["position_suggest_cap"] == pytest.approx(10.0)
    assert row["position_capped"] == 1


def test_pool_state_fields(imported):
    conn, _ = imported
    row = conn.execute("SELECT * FROM pool_states WHERE code='301095'").fetchone()
    assert row["pool"] == "candidate"
    assert row["weeks_in_pool"] == 3
    assert row["tuna_weeks"] == 1
    assert json.loads(row["tuna_hits"])[0].startswith("2026-10-17")


def test_price_ready_flag(imported):
    conn, _ = imported
    row = conn.execute("SELECT price_ready FROM pool_states WHERE code='301210'").fetchone()
    assert row["price_ready"] == 0, "新进池的票当周不给买入价"


# ---------- 报告 ----------

def test_report_has_no_silent_drop(imported):
    _, report = imported
    assert report.skipped, "应当有被跳过的文件（2 个 backup + 1 个旧格式）"
    names = {s["文件"] for s in report.skipped}
    assert "跟踪表_2026-09-19.json" in names
    for s in report.skipped:
        assert s["原因"], f"{s['文件']} 被跳过但没写原因"


def test_report_file_written(imported):
    _, report = imported
    f = report.write()
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert "导入后各表实际条数" in text
    assert "跳过的文件" in text
    assert "跟踪表_2026-09-19.json" in text


def test_report_lists_parse_failures_when_present():
    r = importer.ImportReport()
    r.fail("x:y", "ideal_buy", "很便宜", "价格文本无法解析成数字")
    md = r.to_markdown()
    assert "价格文本无法解析成数字" in md
    assert "很便宜" in md


def test_archive_originals(imported, data_home):
    _, report = imported
    legacy = data_home / "legacy"
    assert legacy.is_dir()
    assert any(legacy.iterdir()), "原始文件应归档到 legacy/"
    assert report.archived


# ---------- 幂等 ----------

def test_import_twice_is_idempotent(imported):
    conn, _ = imported
    before = importer.verify_import(conn)
    importer.run_full_import(conn, SRC)
    after = importer.verify_import(conn)
    assert after == before, f"重复导入改变了数据：{before} → {after}"


def test_cache_not_polluting_stocks(imported):
    """缓存里 100+ 只非池内票不许进库。"""
    conn, _ = imported
    v = importer.verify_import(conn)
    assert v["stocks"] == 57, "评分缓存把非池内票带进来了"


def test_legacy_skip_does_not_create_tables_rows(imported):
    conn, _ = imported
    # 旧格式文件里有"板块跟踪"，不应产生任何 sector_intel 之外的记录
    n = conn.execute("SELECT COUNT(*) FROM sector_intel").fetchone()[0]
    assert n == 31
