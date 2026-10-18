"""T6.2 / T6.3 升级演练。

T6.2 造一个旧版本库 → 升级 → 数据一条不少。
T6.3 迁移中途强杀 → 重启提示「上次升级未完成」→ 恢复成功。

两条都拿真数据跑，不用空表糊过去。判据是**内容指纹**不是行数：
行数对得上但某一行的字段串了位，那是更糟的事故。

老库结构用 fixtures/schema_v1.sql（冻结副本），不跟着 schema.sql 走。
冻结的意思是：它停在 v1 的样子，新加的列/表都不在里面——这正是演练要压的东西。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.db import session
from app.services import backup, migrator

FIXTURES = Path(__file__).resolve().parent / "fixtures"
V1_SCHEMA = FIXTURES / "schema_v1.sql"

# 迁移本身会写的表，不参与「一条不少」对比：
#   schema_migrations 记录迁移历史，app_meta 记 migration_state，data_sources 由 002 播种
VOLATILE = {"schema_migrations", "app_meta", "data_sources"}


def _open_v1(tmp_path: Path) -> sqlite3.Connection:
    """建一个真的 v1 库：冻结结构 + 版本号停在 1。"""
    conn = session.connect(tmp_path / "zisuye.db")
    conn.executescript(V1_SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_migrations(version, name) VALUES (1, '001_init')")
    conn.commit()
    return conn


def seed_old_data(conn: sqlite3.Connection) -> dict[str, int]:
    """往里灌一份有分量的旧数据，够覆盖每一张会被碰到的表。"""
    codes = [f"{600000 + i:06d}" for i in range(28)] + [f"{300100 + i:06d}" for i in range(8)]
    for i, code in enumerate(codes):
        conn.execute("INSERT INTO stocks(code, name, market) VALUES (?,?,?)",
                     (code, f"旧票{i:02d}", "sh" if code.startswith("6") else "sz"))
        conn.execute(
            "INSERT INTO stock_profiles(code, sector, tags, position_suggest_raw,"
            " position_suggest_cap, position_capped) VALUES (?,?,?,?,?,?)",
            (code, f"赛道{i % 5}", json.dumps(["上游", "小市值"]), 15, 10, 1),
        )
        conn.execute(
            "INSERT INTO pool_states(code, pool, weeks_in_pool, entry_week, entry_score,"
            " price_ready, consecutive_weeks_above, tuna_weeks) VALUES (?,?,?,?,?,?,?,?)",
            (code, "candidate" if i < 12 else "observation", i % 4, "2026-W30", 6.4 + i * 0.01,
             1, i % 3, 0),
        )

    # ≥99 条评分：28 只核心票 3 周 + 8 只 2 周 = 84 + 16 = 100
    snaps = 0
    for code in codes[:28]:
        for week in ("2026-W30", "2026-W31", "2026-W32"):
            conn.execute(
                "INSERT INTO score_snapshots(code, as_of_date, track_barrier, earnings_growth,"
                " profitability, prosperity_check, heat_position, market_cap_elasticity,"
                " total, pool, source) VALUES (?,?,?,?,?,?,?,?,?,?, 'history')",
                (code, week, 2.0, 2.1, 1.2, 0.4, 0.8, 0.9, 7.4, "candidate"),
            )
            snaps += 1
    for code in codes[28:]:
        for week in ("2026-W31", "2026-W32"):
            conn.execute(
                "INSERT INTO score_snapshots(code, as_of_date, total, pool, source)"
                " VALUES (?,?,?,?, 'history')",
                (code, week, 5.8, "observation"),
            )
            snaps += 1

    # 19 条池变动
    events = 0
    for i in range(19):
        conn.execute(
            "INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date)"
            " VALUES (?,?,?,?,?,?)",
            (codes[i], f"旧票{i:02d}", "reserve", "candidate", f"分数升到 6.6（第{i}条）",
             f"2026-09-{i + 1:02d}"),
        )
        events += 1

    for i in range(10):
        conn.execute(
            "INSERT INTO barrier_snapshots(code, as_of_date, score, confidence, source)"
            " VALUES (?,?,?,?, 'manual_override')",
            (codes[i], "2026-W32", 2.2, 3),
        )

    for code in codes[:5]:
        conn.execute(
            "INSERT INTO financial_periods(code, period, revenue, revenue_yoy, net_profit,"
            " net_profit_yoy, gross_margin, roe, ocf_to_ni, source) VALUES (?,'2026Q2',?,?,?,?,?,?,?,?)",
            (code, 5.2e8, 31.5, 8.1e7, 42.0, 38.4, 15.2, 1.12, "eastmoney"),
        )

    conn.execute(
        "INSERT INTO sector_intel(sector, cr3, players_count, market_size_bn)"
        " VALUES ('光刻胶', 62.0, 14, 88.5)"
    )

    conn.execute("INSERT INTO portfolios(name, cash_cents, is_default) VALUES ('主账户', 5000000, 1)")
    pid = conn.execute("SELECT id FROM portfolios WHERE name='主账户'").fetchone()[0]
    conn.execute(
        "INSERT INTO trades(portfolio_id, code, side, shares, price_cents, trade_date)"
        " VALUES (?,?,'BUY',1000,1000,'2026-09-01')", (pid, codes[0]))
    conn.execute(
        "INSERT INTO trades(portfolio_id, code, side, shares, price_cents, trade_date)"
        " VALUES (?,?,'BUY',1000,1200,'2026-09-02')", (pid, codes[0]))

    conn.execute(
        "INSERT INTO recommendations(code, name, as_of_date, pool, total_score, price_cents,"
        " snapshot) VALUES (?,?,?,?,?,?,?)",
        (codes[0], "旧票00", "2026-09-01", "candidate", 7.1, 1050, json.dumps({"total": 7.1})),
    )
    rec_id = conn.execute("SELECT id FROM recommendations").fetchone()[0]
    conn.execute(
        "INSERT INTO recommendation_tracks(rec_id, as_of_date, price_cents, pct_change)"
        " VALUES (?, '2026-09-20', 1120, 6.67)", (rec_id,))

    conn.execute(
        "INSERT INTO decision_notes(code, week, decision, note) VALUES (?,?,?,?)",
        (codes[0], "2026-W32", "hold", "等三季报"),
    )
    conn.execute(
        "INSERT INTO source_calls(source, capability, code, ok, latency_ms)"
        " VALUES ('tencent_quote','行情',?,1,123)", (codes[0],))
    conn.commit()

    return {"stocks": len(codes), "score_snapshots": snaps, "pool_events": events}


def columns_of(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """当前每张表的列名。升级前拍一份，用来把指纹固定在「老列」上。"""
    return {
        t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        for t in session.table_names(conn)
        if not t.startswith("sqlite_")
    }


def fingerprint(
    conn: sqlite3.Connection,
    tables: list[str] | None = None,
    projection: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """每张表的内容指纹：整表按 rowid 排序后逐行 JSON，取 sha256。

    projection 传「升级前那套列」时，只比这些列——升级新增的列（值都是 NULL）
    不该被算成数据变化，但也正因为只比老列，任何一行的老字段被改动都会被抓出来。
    """
    out: dict[str, str] = {}
    for t in tables or session.table_names(conn):
        if t in VOLATILE:
            continue
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if not cols:
            continue
        keep = [c for c in (projection or {}).get(t, cols) if c in cols]
        if not keep:
            keep = cols
        select = ", ".join(keep)
        try:
            rows = conn.execute(f"SELECT {select} FROM {t} ORDER BY rowid").fetchall()
        except sqlite3.OperationalError:
            continue
        blob = json.dumps([dict(r) for r in rows], ensure_ascii=False, sort_keys=True,
                          default=str)
        out[t] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return out


def assert_rows_kept(before: dict[str, str], after: dict[str, str]) -> None:
    """老的每一张表内容不许变；升级新加的表必须存在且是空的。

    不能直接比两个 dict：升级本来就会多出 quotes 这类新表，
    拿「不相等」当失败会把正常的增量也判死。
    """
    for table, digest in before.items():
        assert after.get(table) == digest, f"表 {table} 的内容在升级后变了"
    added = set(after) - set(before)
    assert added <= {"quotes"}, f"升级后多出非预期表：{sorted(added - {'quotes'})}"


def schema_shape(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """结构快照：表名 → 列名列表。用来断言「升级后和全新安装长得一样」。"""
    shape: dict[str, list[str]] = {}
    for t in session.table_names(conn):
        if t.startswith("sqlite_"):
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        shape[t] = sorted(cols)
    return shape


# ---------------------------------------------------------------- T6.2


def test_v1_upgrade_keeps_every_row_and_field(data_home):
    """老库升上来：每一张表、每一行、每一个字段都不许变。"""
    conn = _open_v1(data_home)
    counts = seed_old_data(conn)
    assert counts["score_snapshots"] == 100
    assert counts["pool_events"] == 19

    before_cols = columns_of(conn)
    before = fingerprint(conn)
    row_counts_before = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in before
    }

    report = migrator.run_migrations(conn)

    assert report["from"] == 1
    assert report["to"] == migrator.TARGET_VERSION
    assert report["backup"] is not None, "有数据的老库升级前必须自动备份"

    after = fingerprint(conn, projection=before_cols)
    for table, digest in before.items():
        assert after.get(table) == digest, f"表 {table} 的内容在升级后变了"

    for table, n in row_counts_before.items():
        now = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert now == n, f"表 {table} 行数变了：{n} → {now}"

    # 明确点名 PRD 里那三个数，别只靠循环糊过去
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 36
    assert conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] >= 99
    assert conn.execute("SELECT COUNT(*) FROM pool_events").fetchone()[0] == 19


def test_upgraded_schema_matches_fresh_install(data_home):
    """升级后的库结构必须和全新安装一模一样。

    这条是防「新加字段只改 schema.sql、忘了写迁移」——那种缺口平时看不出来，
    用户升级完第一次读库才炸。
    """
    old = _open_v1(data_home)
    seed_old_data(old)
    migrator.run_migrations(old)
    upgraded = schema_shape(old)

    fresh_path = data_home / "fresh.db"
    fresh = session.connect(fresh_path)
    fresh.executescript(
        (Path(session.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8"))
    fresh.execute("INSERT INTO schema_migrations(version, name) VALUES (?, '001_init')",
                  (migrator.TARGET_VERSION,))
    fresh.commit()
    expected = schema_shape(fresh)

    missing_tables = set(expected) - set(upgraded)
    assert not missing_tables, f"升级后缺表：{sorted(missing_tables)}"

    for table, cols in expected.items():
        missing_cols = set(cols) - set(upgraded.get(table, []))
        assert not missing_cols, f"表 {table} 升级后缺列：{sorted(missing_cols)}"


def test_v1_upgrade_creates_quotes_and_seeds_sources(data_home):
    """002/003 的增量必须真落地，不是「报告里写了 applied」就算。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    migrator.run_migrations(conn)

    assert "quotes" in session.table_names(conn)
    sources = {r[0] for r in conn.execute("SELECT name FROM data_sources")}
    assert {"tencent_quote", "eastmoney_fin", "sina_quote"} <= sources
    # 升级不该碰用户数据：quotes 是空的，等第一次更新自己填
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0


def test_v2_to_v3_to_v4_chain(data_home):
    """v1 先只升到 2，再升到最新，两次都要保数据。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    before_cols = columns_of(conn)
    before = fingerprint(conn)

    real = migrator.MIGRATIONS
    try:
        migrator.MIGRATIONS = tuple(m for m in real if m.version <= 2)
        migrator.run_migrations(conn)
        assert session.get_schema_version(conn) == 2
    finally:
        migrator.MIGRATIONS = real

    migrator.run_migrations(conn)
    assert session.get_schema_version(conn) == migrator.TARGET_VERSION
    assert_rows_kept(before, fingerprint(conn, projection=before_cols))


def test_upgrade_report_is_auditable(data_home):
    """升级报告要能给人看：从哪到哪、做了哪几条、备份 id 是什么。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    report = migrator.run_migrations(conn)

    assert report["applied"] == [
        "002_index_and_sources", "003_quotes", "004_pool_last_judged"]
    assert report["backup"]["id"]
    assert "升级" in report["backup"]["reason"]

    listed = backup.list_backups("pre_migration")
    assert listed and listed[0]["id"] == report["backup"]["id"]
    assert listed[0]["verified"] is True


# ---------------------------------------------------------------- T6.3


def _simulate_kill_mid_migration(conn: sqlite3.Connection) -> dict:
    """模拟「升级跑到一半进程被杀」。

    真实场景是：备份做完了、版本号标成 running、002 建了索引但还没提交完，
    然后进程没了。这里就把库停在这个状态，连接直接关掉。
    """
    pre = backup.create_backup("pre_migration", reason="升级 1 → 4（演练）", conn=conn)
    session.meta_set(migrator.MIGRATION_STATE_KEY, "running", conn=conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_source ON score_snapshots(source)")
    conn.commit()
    return pre


def test_kill_mid_migration_blocks_next_start(data_home):
    """重启后不许静默继续：必须先提示「上次升级未完成」。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    _simulate_kill_mid_migration(conn)
    conn.close()

    session.reset_connections()
    restarted = session.connect()

    with pytest.raises(migrator.MigrationInterrupted) as ei:
        migrator.run_migrations(restarted)

    msg = str(ei.value)
    assert "上次升级未完成" in msg
    # 提示得能落地：告诉用户有两个选项
    assert "恢复" in msg and "重试" in msg
    # 拒绝启动的同时不许偷偷把库改了
    assert session.get_schema_version(restarted) == 1


def test_recover_after_kill_restores_pre_upgrade_data(data_home):
    """恢复成功后，数据回到升级前那一刻，半成品痕迹不留。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    before_cols = columns_of(conn)
    before = fingerprint(conn)
    row_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}

    _simulate_kill_mid_migration(conn)
    # 半成品阶段又写进来一条脏数据（真实情况下是前台还在点）
    conn.execute("INSERT INTO stocks(code, name) VALUES ('999999','半成品')")
    conn.commit()
    conn.close()

    session.reset_connections()
    restarted = session.connect()
    with pytest.raises(migrator.MigrationInterrupted):
        migrator.run_migrations(restarted)

    result = migrator.recover_from_interruption(restarted)
    assert result["restored"]["id"]
    assert session.meta_get(migrator.MIGRATION_STATE_KEY) == "idle"

    restarted.close()
    session.reset_connections()
    check = session.connect()

    assert_rows_kept(before, fingerprint(check, projection=before_cols))
    for t, n in row_counts.items():
        assert check.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == n
    assert check.execute(
        "SELECT COUNT(*) FROM stocks WHERE code='999999'").fetchone()[0] == 0


def test_recover_then_upgrade_succeeds(data_home):
    """恢复完再升一次，得能升上去——恢复不是终点。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    before_cols = columns_of(conn)
    before = fingerprint(conn)

    _simulate_kill_mid_migration(conn)
    conn.close()
    session.reset_connections()

    restarted = session.connect()
    with pytest.raises(migrator.MigrationInterrupted):
        migrator.run_migrations(restarted)
    migrator.recover_from_interruption(restarted)

    report = migrator.run_migrations(restarted)
    assert report["to"] == migrator.TARGET_VERSION
    assert_rows_kept(before, fingerprint(restarted, projection=before_cols))


def test_failed_migration_rolls_back_automatically(data_home, monkeypatch):
    """迁移代码自己抛错时，自动回到升级前，而不是留个半死库。"""
    conn = _open_v1(data_home)
    seed_old_data(conn)
    before_cols = columns_of(conn)
    before = fingerprint(conn)

    def boom(c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM stocks")
        raise RuntimeError("故意炸")

    real = migrator.MIGRATIONS
    try:
        migrator.MIGRATIONS = real + (migrator.Migration(99, "099_boom", boom),)
        with pytest.raises(RuntimeError) as ei:
            migrator.run_migrations(conn)
        assert "已回滚到升级前" in str(ei.value)
    finally:
        migrator.MIGRATIONS = real

    assert session.meta_get(migrator.MIGRATION_STATE_KEY) == "failed"
    assert_rows_kept(before, fingerprint(conn, projection=before_cols))

    # 回滚完还能继续用：把状态清掉再升，应该一次过
    migrator.reset_state(conn)
    report = migrator.run_migrations(conn)
    assert report["to"] == migrator.TARGET_VERSION
    assert_rows_kept(before, fingerprint(conn, projection=before_cols))
