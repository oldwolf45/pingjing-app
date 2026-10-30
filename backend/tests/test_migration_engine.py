"""迁移引擎测试 — 新路径 app.db.migrations。

验证：
1. 从新路径导入可用
2. list_migrations 返回正确的版本列表
3. 空库升级到最新版本后所有表存在
4. schema_migrations 表记录正确

注：实际使用中 init_schema() 会先建初始表再调用 run_migrations()，
    本测试也遵循这个流程。
"""

import sqlite3
import pytest

from app.db import session
from app.db.migrations import (
    list_migrations,
    needs_migration,
    run_migrations,
    self_check,
    TARGET_VERSION,
)


def test_import_from_new_path():
    """从新路径 app.db.migrations 导入可用。"""
    from app.db.migrations import (  # noqa: F401
        run_migrations,
        needs_migration,
        list_migrations,
        MigrationInterrupted,
    )


def test_list_migrations_after_init():
    """init_schema 之后的迁移列表。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)

    m = list_migrations(conn)
    assert len(m) == TARGET_VERSION
    # v001 已通过 init_schema 建表，但 schema_migrations 里也有记录
    assert m[0]["version"] == 1
    assert m[0]["name"] == "001_init"


def test_needs_migration_after_init():
    """init_schema 之后如果版本落后，需要迁移。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)

    # SCHEMA_VERSION 是 init_schema 写入的版本
    current = session.get_schema_version(conn)
    if current < TARGET_VERSION:
        assert needs_migration(conn) is True
    else:
        assert needs_migration(conn) is False


def test_run_migrations_from_init():
    """init_schema 后升级到最新版本。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)

    current = session.get_schema_version(conn)
    result = run_migrations(conn, allow_backup=False)

    assert result["to"] == TARGET_VERSION
    assert result["backup"] is None

    # 验证 schema_migrations 记录
    rows = conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert len(rows) >= TARGET_VERSION
    assert rows[0]["version"] == 1
    assert rows[-1]["version"] == TARGET_VERSION


def test_self_check_after_migration():
    """迁移后自检通过。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)
    run_migrations(conn, allow_backup=False)

    problems = self_check(conn)
    assert problems == [], f"自检发现问题：{problems}"


def test_list_migrations_after_upgrade():
    """升级后所有迁移状态正确。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)
    run_migrations(conn, allow_backup=False)

    m = list_migrations(conn)
    assert len(m) == TARGET_VERSION
    assert all(x["status"] == "applied" for x in m)
    assert all(x["applied_at"] is not None for x in m)


def test_no_migration_needed_after_upgrade():
    """升级后不需要再迁移。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)
    run_migrations(conn, allow_backup=False)

    assert needs_migration(conn) is False

    result = run_migrations(conn, allow_backup=False)
    assert result["applied"] == []
    assert result["from"] == result["to"] == TARGET_VERSION


def test_migration_files_exist():
    """迁移 SQL 文件存在。"""
    from pathlib import Path

    versions_dir = Path(__file__).resolve().parent.parent / "app" / "db" / "migrations" / "versions"
    files = sorted(versions_dir.glob("*.sql"))
    assert len(files) >= TARGET_VERSION
    assert files[0].name == "001_init.sql"


def test_idempotent_rerun():
    """重复执行迁移不会出错（幂等）。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)

    # 第一次迁移
    r1 = run_migrations(conn, allow_backup=False)

    # 第二次，应该什么都不做
    r2 = run_migrations(conn, allow_backup=False)
    assert r2["applied"] == []
    assert r2["from"] == r2["to"] == TARGET_VERSION

    # 第三次
    r3 = run_migrations(conn, allow_backup=False)
    assert r3["applied"] == []


def test_all_tables_exist_after_migration():
    """迁移后所有关键表存在。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)
    run_migrations(conn, allow_backup=False)

    tables = session.table_names(conn)
    expected = [
        "app_meta", "schema_migrations", "app_settings", "audit_log",
        "stocks", "stock_profiles",
        "pool_states", "pool_events",
        "score_runs", "score_snapshots", "barrier_snapshots",
        "financial_periods", "financial_manual", "sector_intel",
        "trading_calendar", "quotes",
        "recommendations", "recommendation_tracks", "recommendation_evaluations",
        "portfolios", "trades", "decision_notes",
        "data_sources", "source_calls", "jobs",
    ]
    for t in expected:
        assert t in tables, f"缺少表：{t}"


def test_views_exist_after_migration():
    """迁移后视图存在。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    session.init_schema(conn)
    run_migrations(conn, allow_backup=False)

    views = session.view_names(conn)
    assert "v_score_current" in views
    assert "v_score_daily" in views
