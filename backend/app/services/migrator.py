"""结构迁移框架：编号迁移 + 预备份 + 单事务 + 自检 + 回滚。

断电保护：迁移开始前置 migration_state='running'。下次启动发现 running 说明上次
没跑完，**不自动续跑**（半成品不可信），直接拿 pre_migration 备份覆盖。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db import session
from app.services import backup

TARGET_VERSION = 4

MIGRATION_STATE_KEY = "migration_state"

CRITICAL_TABLES = (
    "stocks", "stock_profiles", "pool_states", "pool_events",
    "score_snapshots", "recommendations", "trades", "sector_intel",
)


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


def _m001(conn: sqlite3.Connection) -> None:
    sql = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)


def _m002(conn: sqlite3.Connection) -> None:
    """增量：评分来源索引 + 数据源初始登记。"""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_source ON score_snapshots(source)")
    seeds = [
        ("tencent_quote", "行情", 10, 1),
        ("tencent_kline", "日K", 10, 1),
        ("eastmoney_fin", "财务", 10, 1),
        ("eastmoney_report", "研报", 10, 1),
        ("eastmoney_flow", "资金流", 20, 1),
        ("sina_quote", "行情", 90, 0),
    ]
    for name, cap, prio, enabled in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO data_sources(name, capability, priority, enabled) VALUES (?,?,?,?)",
            (name, cap, prio, enabled),
        )


def _m003(conn: sqlite3.Connection) -> None:
    """增量：最新行情表 quotes。

    持仓市值和仓位占比要用现价，价格时间戳也是展示的硬要求。
    单独建表而不是塞进 stock_profiles —— 行情是易变的、按日刷新，
    画像是一年改一次的东西，混在一起会让人不敢动画像。
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quotes (
               code          TEXT PRIMARY KEY REFERENCES stocks(code) ON DELETE RESTRICT,
               price         REAL,
               prev_close    REAL,
               pct           REAL,
               open          REAL,
               high          REAL,
               low           REAL,
               turnover_pct  REAL,
               pe            REAL,
               total_mv_yi   REAL,
               float_mv_yi   REAL,
               quote_time    TEXT,
               quoted_at     TEXT NOT NULL,
               source        TEXT NOT NULL DEFAULT 'tencent',
               updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
           )"""
    )


def _m004(conn: sqlite3.Connection) -> None:
    """增量：pool_states.last_judged_at。

    这一列是做到「池子判定」那一步才加的，直接写进了 schema.sql，
    后果是老库升上来以后 load_state() 一查就报 no such column。
    补一条迁移把缺口填上；列已经在了就跳过（全新安装走 schema.sql 时就是这样）。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pool_states)").fetchall()}
    if "last_judged_at" not in cols:
        conn.execute("ALTER TABLE pool_states ADD COLUMN last_judged_at TEXT")


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "001_init", _m001),
    Migration(2, "002_index_and_sources", _m002),
    Migration(3, "003_quotes", _m003),
    Migration(4, "004_pool_last_judged", _m004),
)


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
    state = session.meta_get(MIGRATION_STATE_KEY, conn=conn)
    if state == "running":
        return True
    return session.get_schema_version(conn) < TARGET_VERSION


def run_migrations(conn: sqlite3.Connection, allow_backup: bool = True) -> dict:
    """把库升到 TARGET_VERSION。返回执行报告。"""
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

    pre_backup = None
    if allow_backup and _has_any_data(conn):
        pre_backup = backup.create_backup(
            "pre_migration", reason=f"升级 {current} → {TARGET_VERSION}", conn=conn
        )

    before = _table_row_counts(conn)
    session.meta_set(MIGRATION_STATE_KEY, "running", conn=conn)

    applied: list[str] = []
    try:
        for mig in MIGRATIONS:
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

        problems = self_check(conn, before)
        if problems:
            raise RuntimeError("迁移自检未通过：" + "；".join(problems))

    except Exception as exc:
        # 顺序不能反：先回滚、再标失败。
        # 回滚是用「升级前」的库盖回来，那个库里的 migration_state 还是老值，
        # 先标 failed 会被这一盖直接抹掉，下次启动就看不出「上次升级失败过」。
        if pre_backup is not None:
            backup.restore_backup(pre_backup["id"])
        session.meta_set(MIGRATION_STATE_KEY, "failed", conn=conn)
        if pre_backup is not None:
            raise RuntimeError(f"迁移失败，已回滚到升级前（备份 {pre_backup['id']}）：{exc}") from exc
        raise

    session.meta_set(MIGRATION_STATE_KEY, "done", conn=conn)
    return {
        "from": current,
        "to": session.get_schema_version(conn),
        "applied": applied,
        "backup": pre_backup,
    }


def self_check(conn: sqlite3.Connection, before: dict[str, int] | None = None) -> list[str]:
    """关键表存在 + 行数不减少 + 外键完整。返回问题列表，空表示通过。"""
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
    session.meta_set(MIGRATION_STATE_KEY, "idle", conn=conn)
