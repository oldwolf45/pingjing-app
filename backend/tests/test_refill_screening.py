"""补票闸门：PRD 验收第 17 条。

「市值 300 亿的票标 blocked，block_reason 写明市值300亿>250亿上限，
与「分数不够」分开计数。」

分开计数不是排版洁癖：市值超限说明这只票已经不是紫苏叶要找的东西
（小而冷门的上游），分数不够说明标的质量还不到线。两种原因的处置完全不同，
混在一个数里，看报告的人会误判成选股标准松了。
"""

from __future__ import annotations

import pytest

from app.services import manual_entry, pool_rules


def cand(code: str, *, score: float | None, mv: float | None = 50.0,
         intel: bool = True, finance: bool = True) -> dict:
    return {"code": code, "name": f"票{code}", "total_score": score,
            "total_mv_yi": mv, "intel_ok": intel, "finance_ok": finance}


def test_market_cap_blocked_with_exact_reason():
    v = pool_rules.gate_verdict(cand("600001", score=7.2, mv=300))
    assert v["eligible"] is False
    assert v["blocked"] is True
    assert v["blocked_by"] == "market_cap"
    assert v["block_reason"] == "市值300亿>250亿上限"


def test_market_cap_and_score_are_counted_separately():
    rows = [
        cand("600001", score=7.2, mv=300),   # 市值超限
        cand("600002", score=4.0, mv=40),    # 分数不够
        cand("600003", score=6.6, mv=45),    # 合格
    ]
    out = pool_rules.screen_candidates(rows, slots_left=5)
    c = out["counts"]

    assert c["blocked_market_cap"] == 1
    assert c["score_short"] == 1
    assert c["picked"] == 1
    assert c["slots_left_after"] == 4

    blocked = [v for v in out["verdicts"] if v["blocked"]]
    assert [b["code"] for b in blocked] == ["600001"]
    assert "250" in blocked[0]["block_reason"]


def test_score_short_is_not_marked_blocked():
    """分数不够不算硬闸门——它是标准内的正常淘汰，别和服务有问题混为一谈。"""
    v = pool_rules.gate_verdict(cand("600002", score=4.0))
    assert v["eligible"] is False
    assert v["blocked"] is False
    assert v["blocked_by"] == "score"
    assert "4.5" in v["block_reason"]


def test_intel_and_finance_gaps_are_their_own_buckets():
    rows = [cand("600004", score=5.0, intel=False), cand("600005", score=5.0, finance=False)]
    out = pool_rules.screen_candidates(rows, slots_left=3)
    assert out["counts"]["blocked_intel"] == 1
    assert out["counts"]["blocked_finance"] == 1
    assert out["counts"]["blocked_market_cap"] == 0
    assert out["counts"]["score_short"] == 0


def test_blocked_never_picked_even_with_empty_slots():
    rows = [cand("600001", score=8.5, mv=320), cand("600002", score=8.0, mv=None)]
    out = pool_rules.screen_candidates(rows, slots_left=10)
    assert out["picked"] == []
    assert out["counts"]["picked"] == 0
    assert out["counts"]["blocked_market_cap"] == 2
    assert out["counts"]["slots_left_after"] == 10, "名额就应该空着，不降标准"


def test_picked_goes_by_score_and_tier():
    rows = [cand("600010", score=5.6), cand("600011", score=6.6), cand("600012", score=4.6)]
    out = pool_rules.screen_candidates(rows, slots_left=2)
    assert [p["code"] for p in out["picked"]] == ["600011", "600010"]
    assert out["picked"][0]["pool"] == "candidate"
    assert out["picked"][1]["pool"] == "observation"


def test_boundary_values():
    """正好 250 亿不算超限；正好 4.5 分算过线。"""
    assert pool_rules.gate_verdict(cand("600020", score=4.5, mv=250))["eligible"] is True
    assert pool_rules.gate_verdict(cand("600021", score=4.5, mv=250.1))["blocked"] is True
    assert pool_rules.gate_verdict(cand("600022", score=4.49, mv=100))["blocked_by"] == "score"


def test_refill_reports_screening(conn):
    """补票要能回答「为什么没补上」，所以报告得跟着结果一起回来。"""
    rows = [
        cand("600030", score=7.0, mv=300),
        cand("600031", score=3.9, mv=30),
        cand("600032", score=6.8, mv=40),
    ]
    out = manual_entry.refill_from_candidates(conn, rows)

    assert [p["code"] for p in out["picked"]] == ["600032"]
    counts = out["screening"]["counts"]
    assert counts["blocked_market_cap"] == 1
    assert counts["score_short"] == 1

    row = conn.execute("SELECT pool FROM pool_states WHERE code='600032'").fetchone()
    assert row["pool"] == "candidate"
    # 被挡下的票一张都不许进库
    assert conn.execute("SELECT COUNT(*) FROM stocks WHERE code IN ('600030','600031')"
                        ).fetchone()[0] == 0
