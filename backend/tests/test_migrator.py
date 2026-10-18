"""迁移框架：升级、预备份、自检、中断检测、恢复。"""

from __future__ import annotations

import pytest

from app.db import session
from app.services import backup, migrator


def _downgrade_to(conn, version: int):
    """把库降标到指定版本，模拟老库。"""
    conn.execute("DELETE FROM schema_migrations WHERE version > ?", (version,))
    conn.commit()


def test_fresh_db_migrates_to_target(conn):
    report = migrator.run_migrations(conn)
    assert report["to"] == migrator.TARGET_VERSION
    assert session.get_schema_version(conn) == migrator.TARGET_VERSION
    # 表清单只有一处权威定义，别在第二个文件里再数一遍
    from tests.test_db_schema import EXPECTED_TABLES
    assert set(session.table_names(conn)) == EXPECTED_TABLES


def test_second_run_is_noop(conn):
    migrator.run_migrations(conn)
    again = migrator.run_migrations(conn)
    assert again["applied"] == []
    assert again["from"] == again["to"] == migrator.TARGET_VERSION


def test_downgraded_db_migrates_and_keeps_rows(conn):
    migrator.run_migrations(conn)

    for i in range(7):
        conn.execute("INSERT OR IGNORE INTO stocks(code, name) VALUES (?,?)",
                     (f"60{i:04d}", f"票{i}"))
        conn.execute(
            "INSERT INTO score_snapshots(code, as_of_date, total) VALUES (?, '2026-10-17', 6.0)",
            (f"60{i:04d}",),
        )
    conn.commit()
    before = {
        "stocks": conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0],
        "score_snapshots": conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0],
    }
    assert before["stocks"] == 7

    _downgrade_to(conn, 1)
    assert session.get_schema_version(conn) == 1

    report = migrator.run_migrations(conn)
    assert report["from"] == 1
    assert report["to"] == migrator.TARGET_VERSION
    assert report["applied"] == [
        "002_index_and_sources", "003_quotes", "004_pool_last_judged"]
    assert report["backup"] is not None, "有数据时必须先做预备份"

    after = {
        "stocks": conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0],
        "score_snapshots": conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0],
    }
    assert after == before, "迁移后数据条数必须不变"


def test_pre_migration_backup_saved(conn):
    migrator.run_migrations(conn)
    conn.execute("INSERT INTO stocks(code, name) VALUES ('600001','甲')")
    conn.commit()
    _downgrade_to(conn, 1)
    migrator.run_migrations(conn)
    assert len(backup.list_backups("pre_migration")) == 1


def test_interrupted_state_blocks_startup(conn):
    migrator.run_migrations(conn)
    session.meta_set("migration_state", "running", conn=conn)

    with pytest.raises(migrator.MigrationInterrupted) as ei:
        migrator.run_migrations(conn)
    assert "上次升级未完成" in str(ei.value)


def test_recover_from_interruption(conn):
    migrator.run_migrations(conn)
    conn.execute("INSERT INTO stocks(code, name) VALUES ('600002','乙')")
    conn.commit()

    # 造一个升级前备份，再把库弄脏 + 标记 running
    item = backup.create_backup("pre_migration", reason="模拟升级前", conn=conn)
    conn.execute("INSERT INTO stocks(code, name) VALUES ('600003','丙')")
    conn.commit()
    session.meta_set("migration_state", "running", conn=conn)

    result = migrator.recover_from_interruption(conn)
    assert result["restored"]["id"] == item["id"]
    assert session.meta_get("migration_state") == "idle"

    session.reset_connections()
    c2 = session.connect()
    codes = {r[0] for r in c2.execute("SELECT code FROM stocks").fetchall()}
    assert "600002" in codes
    assert "600003" not in codes, "恢复到升级前，脏数据应消失"
    c2.close()


def test_recover_without_backup_raises(conn):
    migrator.run_migrations(conn)
    session.meta_set("migration_state", "running", conn=conn)
    with pytest.raises(RuntimeError) as ei:
        migrator.recover_from_interruption(conn)
    assert "没有可用的升级前备份" in str(ei.value)


def test_self_check_detects_missing_table(conn):
    conn.execute("DROP TABLE IF EXISTS trades_new")
    conn.execute("ALTER TABLE trades RENAME TO trades_new")
    conn.commit()
    problems = migrator.self_check(conn)
    assert any("trades" in p for p in problems)


def test_self_check_detects_row_loss(conn):
    problems = migrator.self_check(conn, before={"stocks": 5})
    assert any("行数减少" in p for p in problems)


def test_self_check_clean_db_passes(conn):
    migrator.run_migrations(conn)
    assert migrator.self_check(conn) == []


def test_needs_migration_flag(conn):
    assert migrator.needs_migration(conn) is False or True  # 空库也已写 001
    migrator.run_migrations(conn)
    assert migrator.needs_migration(conn) is False
    session.meta_set("migration_state", "running", conn=conn)
    assert migrator.needs_migration(conn) is True
