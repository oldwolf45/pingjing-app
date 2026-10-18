"""T4.4：池子判定的 8+1 条验收（DEV_PLAN M4 表格逐条）。

规则是纯函数，这里直接喂状态和假数据，一周一周地推，不碰网络也不碰数据库。
第 8、9 条涉及落库和补票，单独用 conn 验。
"""

from __future__ import annotations

import pytest

from app.services import pool_rules
from app.services.pool_rules import (
    PoolState,
    decide,
    eligible_for_refill,
    entry_pool,
    plan_refill,
    tier_for_score,
)

C = pool_rules.algo_consts()


def hot(reports=25, mv=180.0, ratio=3.0) -> dict:
    """变热（研报超 20 篇），市值不大。"""
    return {
        "tuna_flag": True, "tuna_reason": "hot",
        "tuna_reasons": [f"研报{reports}篇>20"],
        "tuna_severity": 1,
        "details": {"reports_12m": reports, "active_ratio_pct": ratio, "total_mv_yi": mv},
    }


def big(mv=300.0, reports=5, ratio=2.0) -> dict:
    """变大（市值超 250 亿）。"""
    return {
        "tuna_flag": True, "tuna_reason": "big",
        "tuna_reasons": [f"总市值{mv}亿>250"],
        "tuna_severity": 1,
        "details": {"reports_12m": reports, "active_ratio_pct": ratio, "total_mv_yi": mv},
    }


def calm(reports=5, mv=150.0, ratio=2.0) -> dict:
    return {
        "tuna_flag": False, "tuna_reason": None, "tuna_reasons": [],
        "tuna_severity": 0,
        "details": {"reports_12m": reports, "active_ratio_pct": ratio, "total_mv_yi": mv},
    }


def state(pool="reserve", **kw) -> PoolState:
    base = dict(code="301095", name="广立微", pool=pool, weeks_in_pool=3,
                price_ready=True)
    base.update(kw)
    return PoolState(**base)


# ----------------------------------------------------------------------
# 1. 连续 2 周命中「市值 300 亿」→ 第 1 周待确认，第 2 周进金枪鱼池
# ----------------------------------------------------------------------
def test_acceptance_1_two_weeks_big_moves_to_tuna():
    st = state("reserve")
    d1 = decide(st, total_score=7.0, tuna_info=big(300), today="2026-10-10")
    assert d1.kind == "pending_tuna"
    assert d1.to_pool is None
    assert d1.state.tuna_weeks == 1

    d2 = decide(d1.state, total_score=5.2, tuna_info=big(305), today="2026-10-17")
    assert d2.moved is True
    assert d2.to_pool == "tuna"
    assert d2.state.exit_date == "2026-10-17"
    assert d2.state.exit_snapshot["exit_mv"] == pytest.approx(305.0)
    assert d2.state.exit_snapshot["exit_reason"] == "big"
    # 出池时固化快照，之后不再改
    assert "总市值305亿>250" in d2.state.exit_snapshot["exit_reasons"][0]


# ----------------------------------------------------------------------
# 2. 第 1 周命中、第 2 周不命中 → 计数归零，留原池，不出池
# ----------------------------------------------------------------------
def test_acceptance_2_miss_resets_counter_and_keeps_pool():
    st = state("reserve")
    d1 = decide(st, total_score=7.0, tuna_info=big(300), today="2026-10-10")
    assert d1.state.tuna_weeks == 1

    d2 = decide(d1.state, total_score=7.0, tuna_info=calm(), today="2026-10-17")
    assert d2.to_pool is None
    assert d2.state.pool == "reserve"
    assert d2.state.tuna_weeks == 0
    assert d2.state.tuna_hits == []


# ----------------------------------------------------------------------
# 3. 金枪鱼池连续 4 周满足回归三条 → 回储备池（不是候选池）
# ----------------------------------------------------------------------
def test_acceptance_3_tuna_returns_to_reserve_after_four_weeks():
    st = state("tuna", weeks_in_pool=2, back_weeks=0,
               exit_date="2026-09-01", exit_snapshot={"exit_reason": "big"})
    back = calm(reports=6, mv=150.0, ratio=2.0)  # 三条都满足
    cur = st
    for i in range(3):
        d = decide(cur, total_score=6.9, tuna_info=back, today=f"2026-10-{i + 1:02d}")
        assert d.to_pool is None, "前三周不该动"
        assert d.state.back_weeks == i + 1
        cur = d.state

    d4 = decide(cur, total_score=6.9, tuna_info=back, today="2026-10-04")
    assert d4.moved is True
    assert d4.to_pool == "reserve", "回归只能是储备池，不能直接跳候选池"
    assert d4.state.exit_snapshot is not None, "回归后原出池快照仍保留"


# ----------------------------------------------------------------------
# 4. 连续 3 周满足、第 4 周破一条 → 归零，仍在金枪鱼池
# ----------------------------------------------------------------------
def test_acceptance_4_one_broken_week_resets_back_counter():
    st = state("tuna", weeks_in_pool=2, back_weeks=3)
    broken = calm(reports=12, mv=150.0, ratio=2.0)  # 研报 12 > 10，破一条
    d = decide(st, total_score=6.9, tuna_info=broken, today="2026-10-04")
    assert d.to_pool is None
    assert d.state.pool == "tuna"
    assert d.state.back_weeks == 0
    assert "研报>10篇" in d.reason


# ----------------------------------------------------------------------
# 5. 「变大」的票分数也掉到 4.0 → 先判金枪鱼 → 出金枪鱼池，不进已移除
# ----------------------------------------------------------------------
def test_acceptance_5_tuna_gate_wins_over_score_downgrade():
    st = state("candidate", weeks_in_pool=9, consecutive_weeks_below=3)
    d = decide(st, total_score=4.0, tuna_info=big(310), today="2026-10-17")
    # 第 1 周：待确认；不该因为分数低被降级
    assert d.kind == "pending_tuna"
    assert d.to_pool is None
    assert d.state.pool == "candidate"

    d2 = decide(d.state, total_score=3.9, tuna_info=big(315), today="2026-10-24")
    assert d2.to_pool == "tuna", "出口必须是金枪鱼池，不是已移除"


# ----------------------------------------------------------------------
# 6. 出池后有票可补 → 补 1 只，名额不变
# ----------------------------------------------------------------------
def test_acceptance_6_refill_keeps_capacity():
    candidates = [
        {"code": "688001", "name": "甲", "total_score": 6.30,
         "intel_ok": True, "finance_ok": True, "total_mv_yi": 88.0},
    ]
    picked = plan_refill(1, candidates)
    assert len(picked) == 1
    assert picked[0]["pool"] == "observation", "6.30 分应该补进观察池（≥5.5 <6.5）"


def test_acceptance_6_refill_sorts_by_score():
    candidates = [
        {"code": "000002", "name": "乙", "total_score": 5.0,
         "intel_ok": True, "finance_ok": True, "total_mv_yi": 60.0},
        {"code": "000001", "name": "甲", "total_score": 6.8,
         "intel_ok": True, "finance_ok": True, "total_mv_yi": 70.0},
    ]
    picked = plan_refill(1, candidates)
    assert [c["code"] for c in picked] == ["000001"]
    assert picked[0]["pool"] == "candidate"


# ----------------------------------------------------------------------
# 7. 补不上 → 名额空着，不降标准
# ----------------------------------------------------------------------
def test_acceptance_7_no_qualified_candidate_leaves_slot_empty():
    weak = [
        {"code": "000003", "name": "丙", "total_score": 4.4,
         "intel_ok": True, "finance_ok": True, "total_mv_yi": 50.0},
        {"code": "000004", "name": "丁", "total_score": 7.2,
         "intel_ok": False, "finance_ok": True, "total_mv_yi": 50.0},
        {"code": "000005", "name": "戊", "total_score": 7.2,
         "intel_ok": True, "finance_ok": True, "total_mv_yi": 320.0},
    ]
    assert plan_refill(1, weak) == []
    assert eligible_for_refill(intel_ok=True, finance_ok=True,
                              total_score=4.49, total_mv_yi=50.0) is False
    assert eligible_for_refill(intel_ok=True, finance_ok=True,
                              total_score=5.0, total_mv_yi=250.01) is False
    assert eligible_for_refill(intel_ok=True, finance_ok=True,
                              total_score=5.0, total_mv_yi=250.0) is True


# ----------------------------------------------------------------------
# 8. 新票进池：补票按分数定层；人工添加只能进储备池；当周买入价待确认
# ----------------------------------------------------------------------
def test_acceptance_8_entry_pool_rules():
    assert entry_pool(6.30) == "observation"
    assert entry_pool(6.80) == "candidate"
    assert entry_pool(4.60) == "reserve"
    # 人工添加不管多少分都只能进储备池
    assert entry_pool(7.50, manual=True) == "reserve"
    assert entry_pool(4.60, manual=True) == "reserve"


def test_acceptance_8_new_entry_has_no_price_first_week(conn):
    conn.execute("INSERT INTO stocks(code,name,market) VALUES('301095','广立微','sz')")
    conn.execute(
        """INSERT INTO pool_states(code, pool, weeks_in_pool, price_ready)
           VALUES('301095','observation',0,0)""")
    conn.commit()

    st = pool_rules.load_state(conn, "301095")
    assert st.price_ready is False
    dec = decide(st, total_score=6.30, tuna_info=calm(), today="2026-10-17")
    # 第一周：不计金枪鱼（本来也没命中），但周数要涨、price_ready 翻 True
    assert dec.state.weeks_in_pool == 1
    assert dec.state.price_ready is True


def test_acceptance_8_manual_add_uses_reserve_and_no_price(conn):
    from app.services import manual_entry
    manual_entry.add_stock(conn, code="688999", name="新股", sector="测试",
                           total_score=8.5, today="2026-10-17")
    row = conn.execute("SELECT * FROM pool_states WHERE code='688999'").fetchone()
    assert row["pool"] == "reserve", "人工添加分数再高也只能进储备池"
    assert row["price_ready"] == 0
    assert row["weeks_in_pool"] == 0


# ----------------------------------------------------------------------
# 9. 新票进池第 2 周命中「研报 30 篇」→ 不计周数，等 price_ready 才开始计数
# ----------------------------------------------------------------------
def test_acceptance_9_first_week_hot_does_not_count():
    st = state("observation", weeks_in_pool=0, price_ready=False)
    d = decide(st, total_score=6.6, tuna_info=hot(30), today="2026-10-17")
    assert d.to_pool is None
    assert d.state.pool == "observation"
    assert d.state.tuna_weeks == 0, "进池当周不该累计金枪鱼周数"
    assert d.state.price_ready is True, "第二周开始数据确认"
    assert d.state.weeks_in_pool == 1

    # 第 2 周再命中，这才开始计数
    d2 = decide(d.state, total_score=6.6, tuna_info=hot(31), today="2026-10-24")
    assert d2.state.tuna_weeks == 1
    assert d2.to_pool is None

    d3 = decide(d2.state, total_score=6.6, tuna_info=hot(32), today="2026-10-31")
    assert d3.to_pool == "tuna"


def test_acceptance_9_third_stage_still_runs_in_first_week():
    """⓿ 只短路 ②。如果顺手把 ③ 也短路，weeks_in_pool 会永远不涨。"""
    st = state("candidate", weeks_in_pool=0, price_ready=False,
               consecutive_weeks_below=0)
    d = decide(st, total_score=4.0, tuna_info=hot(30), today="2026-10-17")
    # 进池当周：在池周数和"连续破线"计数都在涨，说明 ③ 真的跑了
    assert d.state.weeks_in_pool == 1
    assert d.state.consecutive_weeks_below == 1
    assert d.to_pool is None
    assert d.state.price_ready is True

    # 攒到第 4 周才移除（REMOVE_WEEKS=4），说明门槛没被放宽
    d2 = decide(d.state, total_score=4.0, tuna_info=calm(), today="2026-10-24")
    d3 = decide(d2.state, total_score=4.0, tuna_info=calm(), today="2026-10-31")
    assert d3.to_pool is None
    d4 = decide(d3.state, total_score=4.0, tuna_info=calm(), today="2026-11-07")
    assert d4.to_pool == "removed"
    assert d4.reason.startswith("连续4周")


# ----------------------------------------------------------------------
# 顺序与滞回
# ----------------------------------------------------------------------
def test_tuna_pool_never_uses_score_downgrade():
    st = state("tuna", weeks_in_pool=5, back_weeks=0)
    d = decide(st, total_score=3.0, tuna_info=calm(mv=150, reports=5, ratio=2.0),
               today="2026-10-17")
    assert d.state.pool == "tuna", "金枪鱼池只走回归，不参与分数升降级"


def test_tuna_back_keeps_counter_when_data_missing():
    st = state("tuna", back_weeks=2)
    d = decide(st, total_score=6.0, tuna_info=None, today="2026-10-17")
    assert d.kind == "frozen"
    assert d.state.back_weeks == 2, "数据缺失不算断档"


def test_hysteresis_band_does_not_flip_flop():
    """251 亿出池、249 亿回来：中间 200~250 亿谁也别动。"""
    in_band = calm(reports=8, mv=230.0, ratio=2.5)
    st = state("tuna", back_weeks=0)
    for i in range(5):
        d = decide(st, total_score=6.0, tuna_info=in_band, today=f"2026-10-{i + 1:02d}")
        assert d.to_pool is None, "滞回区间内不许动"
        st = d.state
    assert st.pool == "tuna"


def test_determine_pool_thresholds_match_config():
    assert tier_for_score(9.0) == "candidate"
    assert tier_for_score(6.5) == "candidate"
    assert tier_for_score(6.49) == "observation"
    assert tier_for_score(5.5) == "observation"
    assert tier_for_score(5.49) == "reserve"
    assert tier_for_score(4.5) == "reserve"
    assert tier_for_score(4.49) == "removed"


def test_pool_events_appended_on_move(conn):
    conn.execute("INSERT INTO stocks(code,name,market) VALUES('301095','广立微','sz')")
    conn.execute(
        """INSERT INTO pool_states(code, pool, weeks_in_pool, price_ready)
           VALUES('301095','reserve',5,1)""")
    conn.commit()

    st = pool_rules.load_state(conn, "301095")
    d1 = decide(st, total_score=7.0, tuna_info=hot(), today="2026-10-10")
    pool_rules.apply_decision(conn, d1, "2026-10-10")
    d2 = decide(pool_rules.load_state(conn, "301095"), total_score=7.0,
                tuna_info=hot(), today="2026-10-17")
    pool_rules.apply_decision(conn, d2, "2026-10-17")

    row = conn.execute("SELECT * FROM pool_events").fetchall()
    assert len(row) == 1
    assert row[0]["from_pool"] == "reserve"
    assert row[0]["to_pool"] == "tuna"
    assert "变热" in row[0]["reason"]
    assert conn.execute(
        "SELECT pool FROM pool_states WHERE code='301095'").fetchone()[0] == "tuna"
