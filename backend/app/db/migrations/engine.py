"""数据库迁移引擎。

设计原则：
- 版本化迁移：每个迁移一个版本号，顺序执行
- 单事务：每个迁移在独立事务中执行，失败回滚
- 升级前备份：有数据的库先备份再迁移，失败自动恢复
- 断电保护：迁移开始前标记 state=running，下次启动发现则提示恢复
- 自检：迁移完成后检查关键表存在、行数不减少、外键完整

迁移脚本位置：app/db/migrations/versions/
- 每个迁移一个 SQL 文件（纯 SQL 迁移）或一个 Python 函数（复杂迁移）
- 版本号 3 位零填充，如 001_init.sql
- schema_migrations 表记录已执行的迁移

用法：
    from app.db.migrations.engine import run_migrations
    run_migrations(conn)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db import session
from app.services import backup

# 当前目标版本。新增迁移时加 1。
TARGET_VERSION = 4

MIGRATION_STATE_KEY = "migration_state"

# 迁移时用于自检行数的关键表（迁移后行数不能减少）
CRITICAL_TABLES = (
    "stocks", "stock_profiles", "pool_states", "pool_events",
    "score_snapshots", "recommendations", "trades", "sector_intel",
)

# 迁移脚本目录
MIGRATIONS_DIR = Path(__file__).resolve().parent / "versions"


class MigrationInterrupted(RuntimeError):
    """上次迁移没跑完。需要用户选择：恢复备份 or 重试。"""

    def __init__(self, message: str, state: str = "running"):
        super().__init__(message)
        self.state = state


@dataclass
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _apply_sql_file(conn: sqlite3.Connection, filename: str) -> None:
    """从 migrations/versions/ 目录读取 SQL 文件并执行。"""
    sql_path = MIGRATIONS_DIR / filename
    sql = sql_path.read_text(encoding="utf-8")
    conn.executescript(sql)


def _m001(conn: sqlite3.Connection) -> None:
    """初始建表：完整 schema（24 表 + 视图 + 触发器 + 初始数据）。"""
    _apply_sql_file(conn, "001_init.sql")


def _m002(conn: sqlite3.Connection) -> None:
    """评分来源索引 + 数据源初始登记。"""
    _apply_sql_file(conn, "002_index_and_sources.sql")


def _m003(conn: sqlite3.Connection) -> None:
    """新增最新行情表 quotes。"""
    _apply_sql_file(conn, "003_quotes.sql")


def _m004(conn: sqlite3.Connection) -> None:
    """pool_states 补 last_judged_at 列。

    老库升级时缺这一列，schema.sql 里直接加了所以新库不缺。
    SQLite 没有 ADD COLUMN IF NOT EXISTS，用 PRAGMA 判断一下。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pool_states)").fetchall()}
    if "last_judged_at" not in cols:
        conn.execute("ALTER TABLE pool_states ADD COLUMN last_judged_at TEXT")


# 全部迁移列表，按版本号升序排列
# 注意：这是模块级变量，测试时可通过赋值替换（如 migrator.MIGRATIONS = [...]）
# run_migrations 内部通过 get_migrations() 动态读取，确保替换生效
MIGRATIONS: list[Migration] = [
    Migration(1, "001_init", _m001),
    Migration(2, "002_index_and_sources", _m002),
    Migration(3, "003_quotes", _m003),
    Migration(4, "004_pool_last_judged", _m004),
]


def get_migrations() -> list[Migration]:
    """获取当前迁移列表。

    测试时可能替换 MIGRATIONS 变量，每次调用都重新读模块全局值。
    """
    return MIGRATIONS


def _table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in CRITICAL_TABLES:
        try:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[t] = -1  # 表不存在
    return counts


def _has_any_data(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False


def needs_migration(conn: sqlite3.Connection) -> bool:
    """检查是否需要迁移。"""
    state = session.meta_get(MIGRATION_STATE_KEY, conn=conn)
    if state == "running":
        return True
    return session.get_schema_version(conn) < TARGET_VERSION


def run_migrations(conn: sqlite3.Connection, allow_backup: bool = True) -> dict:
    """把数据库升到 TARGET_VERSION。返回执行报告。

    Args:
        conn: 数据库连接
        allow_backup: 是否允许升级前备份（空库不需要）

    Returns:
        {"from": int, "to": int, "applied": list[str], "backup": dict|None}

    Raises:
        MigrationInterrupted: 上次迁移未完成，需要先恢复
        RuntimeError: 迁移失败，已回滚
    """
    state = session.meta_get(MIGRATION_STATE_KEY, conn=conn)
    if state == "running":
        raise MigrationInterrupted(
            "上次升级未完成，为避免半成品数据被继续写入，已停止启动。"
            "请选择「恢复到升级前」或「重试」。",
            state="running",
        )

    current = session.get_schema_version(conn)
    if current >= TARGET_VERSION:
        return {"from": current, "to": current, "applied": [], "backup": None}

    # 有数据的库先备份
    pre_backup = None
    if allow_backup and _has_any_data(conn):
        pre_backup = backup.create_backup(
            "pre_migration", reason=f"升级 {current} → {TARGET_VERSION}", conn=conn
        )

    before = _table_row_counts(conn)
    session.meta_set(MIGRATION_STATE_KEY, "running", conn=conn)

    applied: list[str] = []
    try:
        for mig in get_migrations():
            if mig.version <= current:
                continue
            # 每个迁移单独一个事务：一个失败不影响已完成的
            conn.execute("BEGIN IMMEDIATE")
            try:
                mig.apply(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (?,?)",
                    (mig.version, mig.name),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied.append(mig.name)

        # 自检
        problems = self_check(conn, before)
        if problems:
            raise RuntimeError("迁移自检未通过：" + "；".join(problems))

    except Exception as exc:
        # 顺序不能反：先回滚、再标失败。
        # 回滚是用「升级前」的库盖回来，那个库里的 migration_state 还是老值，
        # 先标 failed 会被这一盖直接抹掉。
        if pre_backup is not None:
            backup.restore_backup(pre_backup["id"])
        session.meta_set(MIGRATION_STATE_KEY, "failed", conn=conn)
        if pre_backup is not None:
            raise RuntimeError(
                f"迁移失败，已回滚到升级前（备份 {pre_backup['id']}）：{exc}"
            ) from exc
        raise

    session.meta_set(MIGRATION_STATE_KEY, "done", conn=conn)
    return {
        "from": current,
        "to": session.get_schema_version(conn),
        "applied": applied,
        "backup": pre_backup,
    }


def self_check(
    conn: sqlite3.Connection, before: dict[str, int] | None = None
) -> list[str]:
    """迁移自检：关键表存在 + 行数不减少 + 外键完整。

    Returns:
        问题列表，空列表表示通过。
    """
    problems: list[str] = []

    tables = set(session.table_names(conn))
    for t in CRITICAL_TABLES:
        if t not in tables:
            problems.append(f"关键表缺失：{t}")

    if before:
        after = _table_row_counts(conn)
        for t, n in before.items():
            if n > 0 and after.get(t, -1) < n:
                problems.append(f"表 {t} 行数减少：{n} → {after.get(t, -1)}")

    try:
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            problems.append(f"外键不完整：{len(fk)} 条违规")
    except sqlite3.OperationalError:
        pass

    return problems


def recover_from_interruption(conn: sqlite3.Connection) -> dict:
    """用最近一次 pre_migration 备份覆盖当前库，坏库改名留证。"""
    items = backup.list_backups("pre_migration")
    item = next((i for i in items if i.get("verified")), None)
    if item is None:
        session.meta_set(MIGRATION_STATE_KEY, "idle", conn=conn)
        raise RuntimeError("没有可用的升级前备份，无法恢复。请联系维护者。")

    result = backup.restore_backup(item["id"])
    session.meta_set(MIGRATION_STATE_KEY, "idle")
    return result


def reset_state(conn: sqlite3.Connection) -> None:
    """重置迁移状态标记（一般不需要手动调用）。"""
    session.meta_set(MIGRATION_STATE_KEY, "idle", conn=conn)


def list_migrations(conn: sqlite3.Connection) -> list[dict]:
    """列出所有迁移及其状态。用于调试和展示。"""
    applied = {}
    try:
        rows = conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        applied = {r["version"]: r for r in rows}
    except sqlite3.OperationalError:
        pass

    result = []
    for mig in get_migrations():
        a = applied.get(mig.version)
        result.append({
            "version": mig.version,
            "name": mig.name,
            "status": "applied" if a else "pending",
            "applied_at": a["applied_at"] if a else None,
        })
    return result
