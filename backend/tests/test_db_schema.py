"""M1 数据层：结构 / 触发器 / 路径 / 备份 / 迁移。"""

from __future__ import annotations

import sqlite3

import pytest

from app import paths
from app.db import session

EXPECTED_TABLES = {
    "app_meta", "schema_migrations", "app_settings", "audit_log",
    "stocks", "stock_profiles",
    "pool_states", "pool_events",
    "score_runs", "score_snapshots", "barrier_snapshots",
    "financial_periods", "financial_manual", "sector_intel", "trading_calendar",
    "quotes",
    "recommendations", "recommendation_tracks", "recommendation_evaluations",
    "portfolios", "trades", "decision_notes",
    "data_sources", "source_calls", "jobs",
}

APPEND_ONLY = {
    "score_snapshots", "pool_events", "recommendations",
    "recommendation_tracks", "trades", "source_calls",
}


# ---------- 路径 ----------

def test_paths_dev_mode_points_into_project(data_home):
    assert paths.data_dir() == data_home.resolve()
    assert paths.db_path().parent == data_home.resolve()
    assert paths.backup_dir().is_dir()
    assert paths.export_dir().is_dir()
    assert paths.log_dir().is_dir()


def test_paths_describe_has_all_keys(data_home):
    d = paths.describe()
    for k in ("frozen", "install_dir", "resource_dir", "data_dir", "backup_dir", "db_path"):
        assert k in d


# ---------- 结构与 PRAGMA ----------

def test_exactly_25_tables(conn):
    # 25 = 24 张原始表 + quotes（M5 补的最新行情表，持仓市值和价格时间戳要用）
    tables = set(session.table_names(conn))
    assert len(tables) == 25, f"表数量不对：{len(tables)}"
    assert tables == EXPECTED_TABLES


def test_views_exist(conn):
    views = set(session.view_names(conn))
    assert {"v_score_current", "v_score_daily"} <= views


def test_journal_mode_is_wal(conn):
    assert session.journal_mode(conn).lower() == "wal"


def test_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_quick_check_ok(conn):
    assert session.quick_check(conn) == "ok"


# ---------- 只追加表 ----------

def _insert_stock(conn, code="301095", name="广立微"):
    conn.execute("INSERT OR IGNORE INTO stocks(code, name) VALUES (?,?)", (code, name))
    conn.commit()


def _seed_append_only(conn, table: str):
    _insert_stock(conn)
    if table == "score_snapshots":
        conn.execute(
            "INSERT INTO score_snapshots(code, as_of_date, total) VALUES ('301095','2026-10-17',7.1)"
        )
    elif table == "pool_events":
        conn.execute(
            "INSERT INTO pool_events(code, from_pool, to_pool, change_date) "
            "VALUES ('301095','observation','reserve','2026-10-17')"
        )
    elif table == "recommendations":
        conn.execute(
            "INSERT INTO recommendations(code, as_of_date) VALUES ('301095','2026-10-17')"
        )
    elif table == "recommendation_tracks":
        conn.execute("INSERT INTO recommendations(code, as_of_date) VALUES ('301095','2026-10-17')")
        rid = conn.execute("SELECT id FROM recommendations").fetchone()[0]
        conn.execute(
            "INSERT INTO recommendation_tracks(rec_id, as_of_date) VALUES (?, '2026-10-18')", (rid,)
        )
    elif table == "trades":
        conn.execute("INSERT OR IGNORE INTO portfolios(id, name) VALUES (1,'默认')")
        conn.execute(
            "INSERT INTO trades(portfolio_id, code, side, shares, price_cents, trade_date) "
            "VALUES (1,'301095','BUY',1000,7200,'2026-10-17')"
        )
    elif table == "source_calls":
        conn.execute(
            "INSERT INTO source_calls(source, capability, ok) VALUES ('tencent','行情',1)"
        )
    conn.commit()


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_append_only_rejects_update(conn, table):
    _seed_append_only(conn, table)
    first_col = conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    assert first_col is not None
    with pytest.raises(sqlite3.DatabaseError) as ei:
        conn.execute(f"UPDATE {table} SET rowid = rowid + 1000")
        conn.commit()
    assert "只追加" in str(ei.value)


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_append_only_rejects_delete(conn, table):
    _seed_append_only(conn, table)
    with pytest.raises(sqlite3.DatabaseError) as ei:
        conn.execute(f"DELETE FROM {table}")
        conn.commit()
    assert "只追加" in str(ei.value)


def test_pool_states_is_mutable(conn):
    _insert_stock(conn)
    conn.execute("INSERT INTO pool_states(code, pool) VALUES ('301095','candidate')")
    conn.execute("UPDATE pool_states SET pool='reserve' WHERE code='301095'")
    conn.commit()
    assert conn.execute("SELECT pool FROM pool_states").fetchone()[0] == "reserve"


# ---------- 视图语义 ----------

def test_v_score_current_takes_latest_date_and_max_id(conn):
    _insert_stock(conn)
    rows = [
        ("2026-09-25", 8.4), ("2026-10-02", 7.1), ("2026-10-02", 7.3),
    ]
    for d, total in rows:
        conn.execute(
            "INSERT INTO score_snapshots(code, as_of_date, total) VALUES ('301095',?,?)", (d, total)
        )
    conn.commit()
    cur = conn.execute("SELECT total, as_of_date FROM v_score_current").fetchall()
    assert len(cur) == 1
    assert cur[0]["as_of_date"] == "2026-10-02"
    assert cur[0]["total"] == 7.3, "同日重跑应取 id 最大者"


def test_v_score_daily_dedups_same_day(conn):
    _insert_stock(conn)
    for total in (7.0, 7.5):
        conn.execute(
            "INSERT INTO score_snapshots(code, as_of_date, total) VALUES ('301095','2026-10-17',?)",
            (total,),
        )
    conn.commit()
    rows = conn.execute("SELECT total FROM v_score_daily").fetchall()
    assert [r["total"] for r in rows] == [7.5]
    assert conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 2


# ---------- 审计 ----------

def test_audit_log_written(conn):
    session.audit("test_action", "301095", '{"k":1}', conn=conn)
    row = conn.execute("SELECT action, target FROM audit_log").fetchone()
    assert row["action"] == "test_action"
    assert row["target"] == "301095"
