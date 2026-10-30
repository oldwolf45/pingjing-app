"""T4.3：7 阶段编排的测试。

不碰网络：FakeSources 是同一套鸭子类型，返回固定数据。
重点钉四件事：
  1. 7 个阶段按顺序跑，每个阶段都报「当前/总数」
  2. 单只票的数据源失败不拖垮整轮，也不编分
  3. 取消在阶段边界生效，库里不留半截数据
  4. 连续两次更新结果可复现（同一天不重复计周）
"""

from __future__ import annotations

import json

import pytest

from app.services import pool_rules, updater
from app.services.sources import SourceResult
from app.services.updater import (
    STAGE_KEYS,
    UpdateOptions,
    run_update,
    sector_heat_proxy,
)


# ---------------------------------------------------------------- 假数据源

def ok(data, source="fake", capability="") -> SourceResult:
    return SourceResult(ok=True, source=source, capability=capability, data=data)


def bad(error="模拟失败", capability="") -> SourceResult:
    return SourceResult(ok=False, source="fake", capability=capability, error=error)


def quote_payload(code, *, price=30.0, mv=150.0, pe=40.0, name=None, pct=1.5) -> dict:
    return {
        "code": code, "name": name or f"股票{code}", "price": price,
        "pct": pct, "pe": pe, "turnover_pct": 2.0,
        "total_mv_yi": mv, "float_mv_yi": mv * 0.8,
    }


def kline_rows(n=30, step=0.5) -> list[dict]:
    """一路小涨的 30 根日 K，20 日涨幅约 +10%。"""
    rows = []
    close = 20.0
    for i in range(n):
        close = round(close * (1 + step / 100), 3)
        rows.append({"date": f"2026-09-{i + 1:02d}", "open": close - 0.1,
                     "close": close, "high": close + 0.2,
                     "low": close - 0.2, "volume": 1000.0})
    return rows


def fin_payload(code, **over) -> dict:
    base = {
        "code": code, "date": "2026-10-17", "source": "eastmoney",
        "report_date": "2026-09-30",
        "revenue_yoy": 28.0, "net_profit_yoy": 55.0, "deduct_profit_yoy": 48.0,
        "gross_margin": 52.0, "roe": 14.0, "ocf_to_ni": 1.1,
        "revenue": 6.0e8, "parent_netprofit": 1.2e8,
        "deduct_eps": 0.62,
    }
    base.update(over)
    return base


def pop_payload(code, *, tuna_flag=False, mv=150.0, reports=5.0, ratio=2.0) -> dict:
    return {
        "code": code, "score": 0.72, "tuna_flag": tuna_flag,
        "tuna_reason": "big" if tuna_flag else None,
        "tuna_reasons": [f"总市值{mv}亿>250"] if tuna_flag else [],
        "tuna_severity": 1 if tuna_flag else 0,
        "details": {"reports_12m": reports, "active_ratio_pct": ratio,
                    "total_mv_yi": mv},
    }


class FakeSources:
    """完全离线的数据源。任何一只都能单独注入失败。"""

    def __init__(self, *, quotes=None, klines=None, fins=None, pops=None,
                 fail=None) -> None:
        self.quotes = quotes or {}
        self.klines = klines or {}
        self.fins = fins or {}
        self.pops = pops or {}
        self.fail = fail or set()     # {"quote:301095", "finance:301095", ...}

    def _failed(self, kind, code) -> bool:
        return f"{kind}:{code}" in self.fail

    def quote(self, code):
        if self._failed("quote", code):
            return bad("行情接口超时")
        return ok(self.quotes.get(code) or quote_payload(code))

    def kline(self, code, count=30):
        if self._failed("kline", code):
            return bad("日K接口超时")
        return ok(self.klines.get(code) or kline_rows())

    def finance(self, code, refresh=False):
        if self._failed("finance", code):
            return bad("财务接口 502")
        return ok(self.fins.get(code) or fin_payload(code))

    def popularity(self, code, force=False):
        if self._failed("popularity", code):
            return bad("冷门度接口超时")
        return ok(self.pops.get(code) or pop_payload(code))


# ---------------------------------------------------------------- 数据库夹具

def seed(conn, code, name="测试股", sector="EDA", pool="reserve", **kw):
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES (?,?,?,?)",
                 (code, name, "sz", "active"))
    conn.execute("INSERT INTO stock_profiles(code, sector) VALUES (?,?)", (code, sector))
    conn.execute(
        """INSERT INTO pool_states(code, pool, weeks_in_pool, price_ready,
               consecutive_weeks_above, consecutive_weeks_below, tuna_weeks,
               back_weeks) VALUES (?,?,?,?,0,0,0,0)""",
        (code, pool, kw.get("weeks_in_pool", 3), kw.get("price_ready", 1)),
    )
    conn.commit()
    return code


def set_override(conn, code, score):
    """壁垒人工覆盖分：让测试能控制总分，不靠算法硬算。"""
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE "
        "SET value=excluded.value",
        (f"barrier.override.{code}", str(score)),
    )
    conn.commit()


def snapshots(conn, code=None):
    sql = "SELECT * FROM score_snapshots"
    args = ()
    if code:
        sql += " WHERE code=?"
        args = (code,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


def events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM pool_events ORDER BY id")]


def state_of(conn, code) -> dict:
    return dict(conn.execute("SELECT * FROM pool_states WHERE code=?", (code,)).fetchone())


# ---------------------------------------------------------------- 1. 阶段顺序与进度

def test_stages_run_in_order_and_report_progress(conn):
    seed(conn, "301095")
    seen: list[updater.Progress] = []
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                     progress=seen.append)

    assert res.status == "ok", res.error
    assert [s.key for s in res.stages] == list(STAGE_KEYS)
    assert len(res.stages) == 7

    # 进度里出现的阶段顺序必须与定义一致，且每条都带「当前/总数」
    order = []
    for p in seen:
        if not order or order[-1] != p.stage:
            order.append(p.stage)
    assert order == list(STAGE_KEYS)
    assert all(p.stage_count == 7 for p in seen)
    assert all(p.total == 1 for p in seen)

    # 每个阶段都有 0/total 的起点和 total/total 的终点
    for key in STAGE_KEYS:
        cur = [p.current for p in seen if p.stage == key]
        assert cur[0] == 0
        assert cur[-1] == 1
    assert [p.index for p in seen][:1] == [1]
    assert max(p.index for p in seen) == 7


def test_progress_callback_failure_does_not_break_run(conn):
    seed(conn, "301095")

    def boom(_p):
        raise RuntimeError("UI 画崩了")

    res = run_update(conn, FakeSources(), as_of_date="2026-10-17", progress=boom)
    assert res.status == "ok"


def test_stage_reports_carry_counts(conn):
    seed(conn, "301095")
    seed(conn, "301269")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert len(res.stages) == 7
    for s in res.stages:
        assert s.total == 2
        assert s.done == 2, f"{s.key} 只完成 {s.done}"
        assert s.failed == 0
        assert s.label


# ---------------------------------------------------------------- 2. 落库正确性

def test_persist_writes_snapshot_barrier_and_pool(conn):
    seed(conn, "301095")
    set_override(conn, "301095", 2.3)
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert res.status == "ok"

    snaps = snapshots(conn, "301095")
    assert len(snaps) == 1
    row = snaps[0]
    assert row["as_of_date"] == "2026-10-17"
    assert row["run_id"] == res.run_id
    assert row["source"] == "live"
    assert row["algo_version"]
    assert row["total"] == pytest.approx(res.works[0].scores["total"])
    # 六维都在，且不是全 None
    for k in ("track_barrier", "earnings_growth", "profitability",
              "prosperity_check", "heat_position", "market_cap_elasticity"):
        assert row[k] is not None
    assert row["track_barrier"] == pytest.approx(2.3)  # 壁垒人工覆盖分原样带来
    assert json.loads(row["financial"])["revenue_yoy"] == pytest.approx(28.0)

    bs = [dict(r) for r in conn.execute("SELECT * FROM barrier_snapshots")]
    assert len(bs) == 1
    assert bs[0]["source"] == "manual_override"
    assert bs[0]["score"] == pytest.approx(2.3)


def test_run_row_written_with_counts(conn):
    seed(conn, "301095")
    seed(conn, "301269")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    row = dict(conn.execute("SELECT * FROM score_runs WHERE run_id=?",
                            (res.run_id,)).fetchone())
    assert row["status"] == "ok"
    assert row["total"] == 2
    assert row["ok_count"] == 2
    assert row["fail_count"] == 0
    assert row["finished_at"]
    assert json.loads(row["summary"])["asOfDate"] == "2026-10-17"


def test_audit_log_appended(conn):
    seed(conn, "301095")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log")]
    assert "update.run" in actions
    hit = conn.execute("SELECT * FROM audit_log WHERE action='update.run'").fetchone()
    assert hit["target"] == res.run_id


# ---------------------------------------------------------------- 3. 失败隔离

def test_quote_failure_keeps_scoring_but_leaves_mv_empty(conn):
    seed(conn, "301095")
    res = run_update(conn, FakeSources(fail={"quote:301095"}),
                     as_of_date="2026-10-17")
    w = res.works[0]
    assert res.status == "ok"
    assert w.scored is True
    assert any("行情获取失败" in x for x in w.warnings)
    # 不编市值：快照里 total_mv_yi 为 None，而不是 0
    row = snapshots(conn, "301095")[0]
    assert row["total_mv_yi"] is None


def test_finance_empty_skips_scoring_and_marks_partial(conn):
    seed(conn, "301095")
    seed(conn, "301269")
    empty = {"revenue_yoy": None, "net_profit_yoy": None, "deduct_profit_yoy": None,
             "gross_margin": None, "roe": None, "ocf_to_ni": None,
             "revenue": None, "parent_netprofit": None, "deduct_eps": None}
    src = FakeSources(fins={"301095": empty})
    res = run_update(conn, src, as_of_date="2026-10-17")

    assert res.status == "partial"
    assert res.ok_count == 1
    assert res.fail_count == 1
    failed = [w for w in res.works if not w.scored][0]
    assert failed.code == "301095"
    assert any("财务数据全缺" in e for e in failed.errors)
    # 没分的票绝不写快照（写 0 分会把它推进已移除）
    assert snapshots(conn, "301095") == []
    row = dict(conn.execute("SELECT * FROM score_runs WHERE run_id=?",
                            (res.run_id,)).fetchone())
    assert row["status"] == "partial"
    assert row["fail_count"] == 1


def test_finance_endpoint_error_falls_back_to_manual_data(conn):
    """接口挂了但有手工录入 → 仍能评分，且手工值真的用上了。"""
    seed(conn, "301095")
    conn.execute(
        "INSERT INTO financial_manual(code, period, field, value) VALUES (?,?,?,?)",
        ("301095", "latest", "revenue_yoy", 33.3),
    )
    conn.execute(
        "INSERT INTO financial_manual(code, period, field, value) VALUES (?,?,?,?)",
        ("301095", "latest", "roe", 16.0),
    )
    conn.commit()

    res = run_update(conn, FakeSources(fail={"finance:301095"}),
                     as_of_date="2026-10-17")
    assert res.status == "ok"
    assert res.works[0].scored is True
    fin = json.loads(snapshots(conn, "301095")[0]["financial"])
    assert fin["revenue_yoy"] == pytest.approx(33.3)
    assert fin["roe"] == pytest.approx(16.0)


def test_live_finance_wins_over_manual(conn):
    """真值优先：手工数据只补空缺，不许拿旧记忆盖掉刚查到的数。"""
    seed(conn, "301095")
    conn.execute(
        "INSERT INTO financial_manual(code, period, field, value) VALUES (?,?,?,?)",
        ("301095", "latest", "revenue_yoy", 999.0),
    )
    conn.execute(
        "INSERT INTO financial_manual(code, period, field, value) VALUES (?,?,?,?)",
        ("301095", "latest", "order_growth", 42.0),
    )
    conn.commit()

    run_update(conn, FakeSources(), as_of_date="2026-10-17")
    fin = json.loads(snapshots(conn, "301095")[0]["financial"])
    assert fin["revenue_yoy"] == pytest.approx(28.0)   # 真值
    assert fin["order_growth"] == pytest.approx(42.0)  # 接口没有的字段由手工补


def test_popularity_failure_keeps_pool_frozen(conn):
    """冷门度取不到 → 不算断档也不算满足，池子计数保持原样。"""
    seed(conn, "301095", pool="tuna", week_in=0)
    conn.execute("UPDATE pool_states SET back_weeks=2 WHERE code='301095'")
    conn.commit()

    res = run_update(conn, FakeSources(fail={"popularity:301095"}),
                     as_of_date="2026-10-17")
    assert res.status == "ok"
    st = state_of(conn, "301095")
    assert st["pool"] == "tuna"
    assert st["back_weeks"] == 2


def test_barrier_missing_intel_uses_last_snapshot_as_fallback(conn):
    """情报没录入时沿用上次的壁垒分，不因为查不到情报就把分数打回 0。"""
    seed(conn, "301095")
    conn.execute(
        """INSERT INTO score_snapshots(code, as_of_date, total, track_barrier, source)
           VALUES (?,?,?,?,'migration')""",
        ("301095", "2026-10-10", 6.8, 2.1),
    )
    conn.commit()

    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert res.works[0].barrier.score == pytest.approx(2.1)
    assert res.works[0].barrier.source == "fallback_manual"


def test_barrier_uses_sector_intel_when_present(conn):
    seed(conn, "301095", sector="EDA")
    conn.execute(
        """INSERT INTO sector_intel(sector, cr3, players_count, substitution_rate,
               substitution_urgency, cert_cycle_months, cert_level, market_size_bn,
               market_growth, policy_level)
           VALUES ('EDA', 85, 3, 15, 'high', 24, 'high', 100, 20, 'high')""",
    )
    conn.commit()
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert res.works[0].barrier is not None
    assert res.works[0].barrier.source == "auto"
    assert res.works[0].barrier.score > 0


# ---------------------------------------------------------------- 4. 池子接线

def test_pool_move_writes_event_and_snapshot_pool(conn):
    seed(conn, "301095", pool="reserve")
    # 市值 300 亿 → 命中出池线，连续 2 周进金枪鱼池
    src = FakeSources(
        quotes={"301095": quote_payload("301095", mv=300.0)},
        pops={"301095": pop_payload("301095", tuna_flag=True, mv=300.0)},
    )
    r1 = run_update(conn, src, as_of_date="2026-10-10")
    assert r1.moved == []
    assert state_of(conn, "301095")["tuna_weeks"] == 1

    r2 = run_update(conn, src, as_of_date="2026-10-17")
    assert len(r2.moved) == 1
    ev = events(conn)
    assert len(ev) == 1
    assert ev[0]["from_pool"] == "reserve"
    assert ev[0]["to_pool"] == "tuna"

    st = state_of(conn, "301095")
    assert st["pool"] == "tuna"
    assert st["exit_snapshot"]
    assert json.loads(st["exit_snapshot"])["exit_mv"] == pytest.approx(300.0)

    # 快照里的 pool 是判定之后的池，不是判定之前的
    assert snapshots(conn, "301095")[-1]["pool"] == "tuna"


def test_score_downgrade_needs_two_weeks(conn):
    seed(conn, "301095", pool="candidate")
    set_override(conn, "301095", 0.2)      # 壁垒几乎为 0 → 总分很低
    src = FakeSources(quotes={"301095": quote_payload("301095", mv=900.0)})

    r1 = run_update(conn, src, as_of_date="2026-10-10")
    st1 = state_of(conn, "301095")
    assert st1["pool"] == "candidate"          # 第 1 周只计数，不动
    assert st1["consecutive_weeks_below"] == 1

    r2 = run_update(conn, src, as_of_date="2026-10-17")
    total = r2.works[0].scores["total"]
    assert total == pytest.approx(r1.works[0].scores["total"])

    target = pool_rules.tier_for_score(total)
    st2 = state_of(conn, "301095")
    if target == "removed":
        # 掉到移除线要走 REMOVE_WEEKS=4 周，2 周还不够
        assert st2["pool"] == "candidate"
        assert st2["consecutive_weeks_below"] == 2
        assert events(conn) == []
    else:
        # 掉到相邻池：DOWNGRADE_WEEKS=2 周即降，计数清零
        assert target != "candidate"
        assert st2["pool"] == target
        assert st2["consecutive_weeks_below"] == 0
        assert [e["to_pool"] for e in events(conn)] == [target]


def test_same_day_rerun_does_not_double_count_weeks(conn):
    seed(conn, "301095")
    src = FakeSources()
    run_update(conn, src, as_of_date="2026-10-17")
    first = state_of(conn, "301095")

    res2 = run_update(conn, src, as_of_date="2026-10-17")
    second = state_of(conn, "301095")

    assert second["weeks_in_pool"] == first["weeks_in_pool"]
    assert second["last_judged_at"] == first["last_judged_at"]
    assert res2.works[0].decision.kind == "frozen"
    assert "不重复计周" in res2.works[0].decision.reason
    assert res2.stages[5].skipped == 1


def test_stock_without_pool_state_is_reported_not_crashed(conn):
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES (?,?,?,?)",
                 ("301095", "孤儿股", "sz", "active"))
    conn.commit()
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    w = res.works[0]
    assert any("池子状态缺失" in e for e in w.errors)
    assert res.status == "ok"          # 有评分，只是没判定池子
    assert snapshots(conn, "301095")   # 快照照写


# ---------------------------------------------------------------- 5. 可复现

def test_two_consecutive_runs_are_reproducible(conn):
    seed(conn, "301095", sector="EDA")
    seed(conn, "301269", sector="EDA")
    set_override(conn, "301095", 2.3)
    set_override(conn, "301269", 1.8)
    src = FakeSources()

    r1 = run_update(conn, src, as_of_date="2026-10-17")
    r2 = run_update(conn, src, as_of_date="2026-10-17")

    s1 = {w.code: w.scores for w in r1.works}
    s2 = {w.code: w.scores for w in r2.works}
    assert s1 == s2                                  # 分数一模一样
    assert r1.works[0].barrier.score == r2.works[0].barrier.score
    assert events(conn) == []                        # 第二次不多出池子事件

    # 快照只追加，同一天两条；读的时候取最后一条
    assert len(snapshots(conn, "301095")) == 2
    daily = dict(conn.execute(
        "SELECT * FROM v_score_daily WHERE code='301095'").fetchone())
    assert daily["run_id"] == r2.run_id
    assert daily["total"] == pytest.approx(s1["301095"]["total"])


# ---------------------------------------------------------------- 6. 取消

def test_cancel_before_first_stage_writes_nothing(conn):
    seed(conn, "301095")

    class AlreadyCancelled:
        def is_set(self):
            return True

    res = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                     cancel=AlreadyCancelled())
    assert res.status == "cancelled"
    assert res.cancelled_at == "quote"
    assert snapshots(conn) == []
    assert events(conn) == []
    row = dict(conn.execute("SELECT * FROM score_runs WHERE run_id=?",
                            (res.run_id,)).fetchone())
    assert row["status"] == "cancelled"
    assert row["finished_at"]


class TripWire:
    """取消开关。由进度回调拨动，避免依赖"第几次被检查"这种脆断言。"""

    def __init__(self) -> None:
        self.flag = False

    def is_set(self) -> bool:      # 与 threading.Event 同形
        return self.flag

    def trip_when(self, pred):
        def _report(p):
            if pred(p):
                self.flag = True
        return _report


def test_cancel_mid_stage_leaves_no_partial_data(conn):
    seed(conn, "301095")
    seed(conn, "301269")
    seed(conn, "300124")

    wire = TripWire()
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                     progress=wire.trip_when(
                         lambda p: p.stage == "finance" and p.current >= 1),
                     cancel=wire)
    assert res.status == "cancelled"
    assert res.cancelled_at == "finance"
    # 财务阶段只跑到一半，但库里一条业务数据都没有
    assert snapshots(conn) == []
    assert events(conn) == []
    assert all(state_of(conn, c)["last_judged_at"] is None
               for c in ("301095", "301269", "300124"))


def test_cancel_is_not_checked_inside_persist(conn):
    """一旦进了落库阶段就写完——半截落库比推迟一轮更糟。"""
    seed(conn, "301095")
    seed(conn, "301269")

    wire = TripWire()
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                     progress=wire.trip_when(
                         lambda p: p.stage == "persist" and p.current >= 1),
                     cancel=wire)
    assert res.status == "ok"
    assert len(snapshots(conn)) == 2


# ---------------------------------------------------------------- 7. 标的筛选

def test_removed_pool_excluded_by_default(conn):
    seed(conn, "301095", pool="removed")
    seed(conn, "301269", pool="reserve")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert [w.code for w in res.works] == ["301269"]

    res2 = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                      include_removed=True)
    assert {w.code for w in res2.works} == {"301095", "301269"}


def test_codes_filter(conn):
    seed(conn, "301095")
    seed(conn, "301269")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17",
                     codes=["301095"])
    assert [w.code for w in res.works] == ["301095"]


def test_empty_pool_runs_cleanly(conn):
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert res.total == 0
    assert res.status == "ok"
    assert len(res.stages) == 7


def test_new_stock_first_week_has_no_price(conn):
    """新进池当周不出买入价——price_ready 的翻转让在池子里发生，不由编排层代劳。"""
    seed(conn, "301095", price_ready=0, weeks_in_pool=0)
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    assert res.status == "ok"
    st = state_of(conn, "301095")
    assert st["weeks_in_pool"] == 1
    assert st["price_ready"] == 1     # NEW_ENTRY_NO_PRICE_WEEKS=1，下周确认


# ---------------------------------------------------------------- 8. 板块温度代理

def test_sector_heat_proxy_uses_same_sector_mean():
    proxy, pool = sector_heat_proxy({
        "EDA": [10.0, 20.0],
        "机器人": [-5.0, -1.0, -3.0],
        "独苗": [99.0],
    })
    assert proxy["EDA"] == pytest.approx(15.0)
    assert proxy["机器人"] == pytest.approx(-3.0)
    assert "独苗" not in proxy          # 样本 < 2 只，不用它
    assert pool == pytest.approx((10 + 20 - 5 - 1 - 3 + 99) / 6, abs=0.01)


def test_sector_heat_proxy_skips_missing_values():
    proxy, pool = sector_heat_proxy({"EDA": [None, 10.0], "机器人": [None, None]})
    assert "EDA" not in proxy           # 只剩 1 个有效值
    assert "机器人" not in proxy
    assert pool == pytest.approx(10.0)


def test_single_stock_sector_falls_back_to_pool_mean(conn):
    seed(conn, "301095", sector="独苗赛道")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    w = res.works[0]
    assert w.scored is True
    assert any("整池均值代理" in x for x in w.warnings)


# ---------------------------------------------------------------- 9. 参数兜底

def test_options_default_as_of_today():
    opts = UpdateOptions(as_of_date="2026-10-17")
    assert opts.kind == "manual"
    assert opts.kline_count >= 21      # 要算 20 日涨跌


def test_result_serializable(conn):
    seed(conn, "301095")
    res = run_update(conn, FakeSources(), as_of_date="2026-10-17")
    payload = json.dumps(res.as_dict(), ensure_ascii=False)
    data = json.loads(payload)
    assert data["status"] == "ok"
    assert data["stages"][0]["key"] == "quote"
    assert data["works"][0]["code"] == "301095"
