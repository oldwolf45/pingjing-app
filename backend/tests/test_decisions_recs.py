"""T5.1 决策清单、T5.5 推荐快照与三层保护、T5.6 三档统计。

这里最要紧的两条：
  · 推荐快照写进去就改不了（触发器 + 仓储没 update 方法）
  · 三档统计分开算，样本不足一律不给数字
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.services import decisions as dec
from app.services import recommendations as rec


def seed(conn, code="301095", name="广立微", pool="candidate"):
    conn.execute("INSERT OR IGNORE INTO stocks(code, name, market, status) "
                 "VALUES (?,?,?,?)", (code, name, "sz", "active"))
    conn.execute("INSERT OR IGNORE INTO stock_profiles(code, sector) VALUES (?,?)",
                 (code, "EDA"))
    conn.execute(
        """INSERT OR IGNORE INTO pool_states(code, pool, weeks_in_pool, price_ready)
           VALUES (?,?,1,1)""", (code, pool))
    conn.commit()
    return code


def quote(conn, code, price, quote_time="20261017150000"):
    conn.execute(
        """INSERT INTO quotes(code, price, quote_time, quoted_at)
           VALUES (?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET price=excluded.price,
               quote_time=excluded.quote_time, quoted_at=excluded.quoted_at""",
        (code, price, quote_time, "2026-10-17T15:00:05+08:00"))
    conn.commit()


# ---------------------------------------------------------------- T5.1 决策清单

def test_week_key_is_iso_week():
    assert dec.week_key("2026-10-17") == "2026-W42"
    assert dec.week_key("2026-01-01") == "2026-W01"
    # 同一周的周三和周五必须是同一周
    assert dec.week_key("2026-10-14") == dec.week_key("2026-10-16")


def test_set_decision_saves_immediately(conn):
    seed(conn)
    w = "2026-W42"
    row = dec.set_decision(conn, code="301095", week=w, decision="add", note="回调到位")
    assert row["decision"] == "add"
    assert row["decisionLabel"] == "加仓"
    assert row["note"] == "回调到位"
    # 没有「提交」这一步：set 之后直接查得到
    assert dec.get_decision(conn, "301095", w)["decision"] == "add"


def test_same_week_overwrite(conn):
    seed(conn)
    w = "2026-W42"
    dec.set_decision(conn, code="301095", week=w, decision="add")
    dec.set_decision(conn, code="301095", week=w, decision="trim")
    assert dec.get_decision(conn, "301095", w)["decision"] == "trim"
    assert conn.execute("SELECT COUNT(*) FROM decision_notes").fetchone()[0] == 1


def test_rejects_invalid_decision_and_code(conn):
    seed(conn)
    with pytest.raises(dec.DecisionError, match="只能是"):
        dec.set_decision(conn, code="301095", week="2026-W42", decision="buy")
    with pytest.raises(dec.DecisionError, match="不在标的库"):
        dec.set_decision(conn, code="999999", week="2026-W42", decision="add")


def test_previous_week_still_visible(conn):
    """下周打开还在：本周没点的时候，能看到上周怎么决定的。"""
    seed(conn)
    dec.set_decision(conn, code="301095", week="2026-W41", decision="hold", note="等财报")
    lst = dec.week_list(conn, "2026-W42")
    item = lst["items"][0]
    assert item["weekDecision"] is None
    assert item["lastDecision"] == "hold"
    assert item["lastDecisionLabel"] == "持有"
    assert item["lastDecisionWeek"] == "2026-W41"
    assert lst["decided"] == 0
    assert lst["undecided"] == 1


def test_week_list_scope_and_counts(conn):
    seed(conn, "301095", "广立微", "candidate")
    seed(conn, "301269", "华大九天", "observation")
    seed(conn, "300124", "汇川技术", "reserve")
    seed(conn, "601100", "恒立液压", "tuna")
    seed(conn, "603650", "彤程新材", "removed")

    w = "2026-W42"
    dec.set_decision(conn, code="301095", week=w, decision="add")
    lst = dec.week_list(conn, w)

    codes = [i["code"] for i in lst["items"]]
    assert codes == ["301095", "301269", "300124"]   # 金枪鱼和已移除不进来
    assert lst["total"] == 3
    assert lst["decided"] == 1
    assert lst["counts"]["add"] == 1
    assert lst["counts"]["hold"] == 0


def test_audit_written(conn):
    seed(conn)
    dec.set_decision(conn, code="301095", week="2026-W42", decision="watch")
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='decision.set'").fetchone()[0] == 1


# ---------------------------------------------------------------- T5.5 推荐快照

def test_create_fixes_price_and_score(conn):
    seed(conn)
    conn.execute(
        """INSERT INTO score_snapshots(code, as_of_date, total, pool, total_mv_yi,
               track_barrier, earnings_growth, profitability, prosperity_check,
               heat_position, market_cap_elasticity, source)
           VALUES ('301095','2026-10-17',7.1,'candidate',150,2.42,1.9,1.2,0.5,0.35,0.7,'live')""")
    conn.commit()
    quote(conn, "301095", 45.67)

    r = rec.create(conn, code="301095", as_of_date="2026-10-17", thesis="壁垒硬")
    assert r["price_cents"] == 4567
    assert r["total_score"] == pytest.approx(7.1)
    assert r["pool"] == "candidate"
    snap = json.loads(r["snapshot"])
    assert snap["total"] == pytest.approx(7.1)
    assert snap["dims"]["track_barrier"] == pytest.approx(2.42)


def test_create_is_idempotent_per_day(conn):
    seed(conn)
    a = rec.create(conn, code="301095", as_of_date="2026-10-17")
    b = rec.create(conn, code="301095", as_of_date="2026-10-17")
    assert a["id"] == b["id"]
    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 1


def test_snapshot_cannot_be_updated_or_deleted(conn):
    """第一层保护：数据库触发器。"""
    seed(conn)
    r = rec.create(conn, code="301095", as_of_date="2026-10-17")
    with pytest.raises(sqlite3.IntegrityError, match="只追加"):
        conn.execute("UPDATE recommendations SET thesis='改了' WHERE id=?", (r["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="只追加"):
        conn.execute("DELETE FROM recommendations WHERE id=?", (r["id"],))


def test_module_exposes_no_mutation_api(conn):
    """第二层保护：仓储层不提供 update/delete。"""
    banned = ("update", "delete", "remove", "modify", "set_thesis")
    for name in dir(rec):
        if name.startswith("_"):
            continue
        low = name.lower()
        assert not any(low.startswith(b) for b in banned), \
            f"推荐模块不该暴露 {name}"


def test_rec_tracks_also_append_only(conn):
    seed(conn)
    r = rec.create(conn, code="301095", as_of_date="2026-10-17")
    t = rec.track(conn, rec_id=r["id"], as_of_date="2026-10-20", price_cents=4500)
    with pytest.raises(sqlite3.IntegrityError, match="只追加"):
        conn.execute("UPDATE recommendation_tracks SET price_cents=1 WHERE id=?",
                     (t["id"],))


def test_track_is_idempotent_per_date(conn):
    seed(conn)
    r = rec.create(conn, code="301095", as_of_date="2026-10-17")
    a = rec.track(conn, rec_id=r["id"], as_of_date="2026-10-20", price_cents=4500)
    b = rec.track(conn, rec_id=r["id"], as_of_date="2026-10-20", price_cents=9999)
    assert a["id"] == b["id"]
    assert conn.execute("SELECT COUNT(*) FROM recommendation_tracks").fetchone()[0] == 1


def test_track_computes_pct(conn):
    seed(conn)
    quote(conn, "301095", 100.0)
    r = rec.create(conn, code="301095", as_of_date="2026-10-17")
    t = rec.track(conn, rec_id=r["id"], as_of_date="2026-10-20", price_cents=11000)
    assert t["pct_change"] == pytest.approx(10.0)


# ---------------------------------------------------------------- T5.6 三档统计

def _rec_with_tracks(conn, entry_cents, prices, days, bench=None, date="2026-10-17"):
    seed(conn)
    quote(conn, "301095", entry_cents / 100)
    r = rec.create(conn, code="301095", as_of_date=date)
    for i, p in enumerate(prices, 1):
        rec.track(conn, rec_id=r["id"], as_of_date=f"2026-11-{i:02d}",
                  price_cents=p, trading_days=days, benchmark_pct=bench)
    return r


def test_hard_miss_hits_on_deep_drawdown(conn):
    r = _rec_with_tracks(conn, 10000, [9500, 8400, 9800], days=10)
    res = rec.evaluate_one(conn, r)
    assert res["hard_miss"]["verdict"] == "hit"
    assert "-16.0%" in res["hard_miss"]["detail"]


def test_hard_miss_misses_when_shallow(conn):
    r = _rec_with_tracks(conn, 10000, [9500, 9200], days=10)
    assert rec.evaluate_one(conn, r)["hard_miss"]["verdict"] == "miss"


def test_flat_is_pending_before_maturity(conn):
    r = _rec_with_tracks(conn, 10000, [10200], days=20)
    res = rec.evaluate_one(conn, r)
    assert res["flat"]["verdict"] == "pending"
    assert "20/45" in res["flat"]["detail"]
    assert res["lag_benchmark"]["verdict"] == "pending"


def test_flat_hits_when_matured_and_not_profitable(conn):
    r = _rec_with_tracks(conn, 10000, [9800], days=45)
    res = rec.evaluate_one(conn, r)
    assert res["flat"]["verdict"] == "hit"
    assert res["flat"]["detail"] == "到期收益 -2.0%"


def test_lag_benchmark_compares_to_index(conn):
    r = _rec_with_tracks(conn, 10000, [10300], days=45, bench=8.0)
    res = rec.evaluate_one(conn, r)
    assert res["flat"]["verdict"] == "miss"           # 赚钱了，不算到期不赚
    assert res["lag_benchmark"]["verdict"] == "hit"   # 但跑输大盘
    assert "3.0%" in res["lag_benchmark"]["detail"]


def test_tiers_are_independent_not_merged(conn):
    """一只票可以同时是硬失误和跑输大盘——必须两档各记一次。"""
    r = _rec_with_tracks(conn, 10000, [8000, 9000], days=45, bench=50.0)
    rec.persist_evaluation(conn, r)
    rows = {e["tier"]: e["verdict"] for e in conn.execute(
        "SELECT * FROM recommendation_evaluations WHERE rec_id=?", (r["id"],))}
    assert rows["hard_miss"] == "hit"
    assert rows["flat"] == "hit"        # 到期 -10%
    assert rows["lag_benchmark"] == "hit"
    assert len(rows) == 3


def test_stats_refuse_rate_under_10_samples(conn):
    for i in range(3):
        r = _rec_with_tracks(conn, 10000, [8000], days=45, date=f"2026-0{i + 1}-10")
        rec.persist_evaluation(conn, r)
    s = rec.stats(conn)
    assert s["total"] == 3
    hm = s["tiers"]["hard_miss"]
    assert hm["hit"] == 3
    assert hm["rate"] is None, "样本不足就不能给成功率"
    assert "样本不足 3 / 需要 10" == hm["sampleNote"]
    assert s["byScoreBand"]["available"] is False
    assert s["byDimension"]["available"] is False


def test_stats_give_rate_at_10_samples(conn):
    for i in range(10):
        date = f"2026-{i // 5 + 1:02d}-{i % 5 * 5 + 1:02d}"
        r = _rec_with_tracks(conn, 10000, [20000 if i < 6 else 8000], days=45, date=date)
        rec.persist_evaluation(conn, r)
    s = rec.stats(conn)
    hm = s["tiers"]["hard_miss"]
    assert hm["evaluated"] == 10
    assert hm["sampleEnough"] is True
    assert hm["hit"] == 4          # 4 条跌到 8000
    assert hm["rate"] == pytest.approx(40.0)
    assert s["byScoreBand"]["available"] is True


def test_persist_evaluation_is_idempotent(conn):
    r = _rec_with_tracks(conn, 10000, [9000], days=45)
    rec.persist_evaluation(conn, r)
    rec.persist_evaluation(conn, r)
    assert conn.execute(
        "SELECT COUNT(*) FROM recommendation_evaluations").fetchone()[0] == 3


def test_list_recs_includes_tracks_and_evals(conn):
    r = _rec_with_tracks(conn, 10000, [9800], days=45, bench=1.0)
    rec.persist_evaluation(conn, r)
    rows = rec.list_recs(conn)
    assert len(rows) == 1
    assert rows[0]["tracks"]
    assert rows[0]["evaluations"]["hard_miss"]["verdict"] in ("hit", "miss")
    assert rows[0]["snapshotDecoded"] is not None or rows[0]["snapshot"] is None


def test_track_all_uses_latest_quote(conn):
    seed(conn)
    quote(conn, "301095", 50.0)
    r = rec.create(conn, code="301095", as_of_date="2026-10-17")
    assert rec.track_all(conn, "2026-10-24", benchmark_pct=1.2, trading_days=5) == 1
    row = conn.execute("SELECT * FROM recommendation_tracks WHERE rec_id=?",
                       (r["id"],)).fetchone()
    assert row["price_cents"] == 5000
    assert row["benchmark_pct"] == pytest.approx(1.2)
    # 同一天再跑一遍不重复记
    assert rec.track_all(conn, "2026-10-24") == 0
