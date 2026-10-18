"""T5.2 持仓 + 交易、T5.3 作废重算、T5.4 仓位告警。

数字必须一个一个对得上，所以这里的断言全部写成具体数值，
不用「约等于」含糊过去：11.005 就是 11005（分×1000），差 1 都算错。
"""

from __future__ import annotations

import pytest

from app.services import portfolio as pf
from app.services.portfolio import PortfolioError

CODE = "301095"


def money(yuan: float) -> int:
    """元 → 分（整数）。"""
    return int(round(yuan * 100))


@pytest.fixture()
def pid(conn) -> int:
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES (?,?,?,?)",
                 (CODE, "广立微", "sz", "active"))
    conn.commit()
    return pf.ensure_default(conn)["id"]


def buy(conn, pid, shares, price_yuan, fee_yuan=0.0, date="2026-10-10", code=CODE):
    return pf.add_trade(conn, portfolio_id=pid, code=code, side="BUY",
                        shares=shares, price_cents=money(price_yuan),
                        fee_cents=money(fee_yuan), trade_date=date)


def sell(conn, pid, shares, price_yuan, fee_yuan=0.0, date="2026-10-15", code=CODE):
    return pf.add_trade(conn, portfolio_id=pid, code=code, side="SELL",
                        shares=shares, price_cents=money(price_yuan),
                        fee_cents=money(fee_yuan), trade_date=date)


def one(conn, pid, code=CODE) -> dict:
    rows = [r for r in pf.positions(conn, pid) if r["code"] == code]
    return rows[0] if rows else {}


# ---------------------------------------------------------------- T5.2 成本

def test_acceptance_t5_2_cost_includes_fees(conn, pid):
    """买 1000@10 费 5 + 买 1000@12 费 5 → 成本 11.005；卖 500 后成本不变。"""
    buy(conn, pid, 1000, 10.0, 5.0)
    buy(conn, pid, 1000, 12.0, 5.0)

    pos = one(conn, pid)
    assert pos["shares"] == 2000
    # costMilli 单位是"分×1000"：11.005 元 = 1100.5 分 = 1_100_500
    assert pos["costMilli"] == 1_100_500
    assert pos["costPrice"] == pytest.approx(11.005)
    assert pos["costCents"] == money(22010)   # 2000 × 11.005 = 22010 元 = 2_201_000 分

    sell(conn, pid, 500, 13.0, 5.0)
    pos = one(conn, pid)
    assert pos["shares"] == 1500
    assert pos["costMilli"] == 1_100_500, "卖出不许改变单位成本（T5.2 硬要求）"
    assert pos["costPrice"] == pytest.approx(11.005)
    assert pos["costCents"] == 1_650_750      # 1500 × 1100.5 分

    # 已实现盈亏 = 500×13 - 5 - 500×11.005 = 6500 - 5 - 5502.5 = 992.5 元
    assert pos["realizedCents"] == money(992.5)


def test_cost_without_fee(conn, pid):
    buy(conn, pid, 1000, 10.0)
    pos = one(conn, pid)
    assert pos["costMilli"] == 1_000_000      # 10.000 元 = 1000 分
    assert pos["costPrice"] == pytest.approx(10.0)


def test_average_cost_is_exact_for_three_buys(conn, pid):
    """三笔不同价：300@10.00 + 300@10.01 + 400@10.02 → 1001.1 分/股。"""
    buy(conn, pid, 300, 10.0, 0.0, "2026-10-01")
    buy(conn, pid, 300, 10.01, 0.0, "2026-10-02")
    buy(conn, pid, 400, 10.02, 0.0, "2026-10-03")
    # (300*1000 + 300*1001 + 400*1002) 分 = 1_001_100 分 / 1000 股 = 1001.1 分
    assert one(conn, pid)["costMilli"] == 1_001_100
    assert one(conn, pid)["costPrice"] == pytest.approx(10.011)


def test_round_div_is_half_up_not_bankers():
    """Python 的 round(2.5) 是 2（银行家舍入），成本摊分不能用它。"""
    from app.services.portfolio import _round_div
    assert _round_div(5, 2) == 3
    assert _round_div(3, 2) == 2
    assert _round_div(-5, 2) == -3
    assert _round_div(1_100_499, 2) == 550_250


def test_cash_moves_with_trades(conn, pid):
    pf.set_cash(conn, pid, money(100000))
    buy(conn, pid, 1000, 10.0, 5.0)
    row = conn.execute("SELECT cash_cents FROM portfolios WHERE id=?", (pid,)).fetchone()
    assert row[0] == money(100000) - money(10000) - money(5)

    sell(conn, pid, 500, 13.0, 5.0)
    row = conn.execute("SELECT cash_cents FROM portfolios WHERE id=?", (pid,)).fetchone()
    assert row[0] == money(100000) - money(10000) - money(5) + money(6500) - money(5)


# ---------------------------------------------------------------- 校验

def test_cannot_sell_more_than_held(conn, pid):
    buy(conn, pid, 1000, 10.0)
    with pytest.raises(PortfolioError, match="超过持仓"):
        sell(conn, pid, 1001, 11.0)
    assert one(conn, pid)["shares"] == 1000


def test_rejects_bad_input(conn, pid):
    with pytest.raises(PortfolioError, match="股数"):
        buy(conn, pid, 0, 10.0)
    with pytest.raises(PortfolioError, match="不能为负"):
        pf.add_trade(conn, portfolio_id=pid, code=CODE, side="BUY", shares=100,
                     price_cents=-1, fee_cents=0)
    with pytest.raises(PortfolioError, match="买入或卖出"):
        pf.add_trade(conn, portfolio_id=pid, code=CODE, side="VOID", shares=100,
                     price_cents=100, fee_cents=0)


def test_rejects_unknown_code(conn, pid):
    with pytest.raises(PortfolioError, match="不在标的库"):
        pf.add_trade(conn, portfolio_id=pid, code="999999", side="BUY", shares=100,
                     price_cents=100, fee_cents=0)


# ---------------------------------------------------------------- T5.3 作废

def test_acceptance_t5_3_void_replays_from_first_trade(conn, pid):
    """作废第 1 笔后，第 2 笔的持仓基数变了，成本必须按重算结果变。"""
    t1 = buy(conn, pid, 1000, 10.0, 5.0, "2026-10-01")
    buy(conn, pid, 1000, 12.0, 5.0, "2026-10-02")
    assert one(conn, pid)["costMilli"] == 1_100_500

    pf.void_trade(conn, portfolio_id=pid, trade_id=t1["id"], trade_date="2026-10-20")

    pos = one(conn, pid)
    assert pos["shares"] == 1000
    # 只剩「买 1000@12 费 5」→ (1_200_000 + 500) 分 / 1000 股 = 1200.5 分
    assert pos["costMilli"] == 1_200_500
    assert pos["costPrice"] == pytest.approx(12.005)


def test_void_reverses_cash(conn, pid):
    pf.set_cash(conn, pid, money(50000))
    t = buy(conn, pid, 1000, 10.0, 5.0)
    cash_after_buy = conn.execute(
        "SELECT cash_cents FROM portfolios WHERE id=?", (pid,)).fetchone()[0]
    pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])
    cash_now = conn.execute(
        "SELECT cash_cents FROM portfolios WHERE id=?", (pid,)).fetchone()[0]
    assert cash_now == money(50000)
    assert cash_now != cash_after_buy


def test_void_keeps_original_row_and_appends_marker(conn, pid):
    t = buy(conn, pid, 1000, 10.0, 5.0)
    v = pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE portfolio_id=? ORDER BY id", (pid,))]
    assert len(rows) == 2
    assert rows[0]["id"] == t["id"] and rows[0]["side"] == "BUY"  # 原记录还在
    assert rows[1]["side"] == "VOID"
    assert rows[1]["voids_trade_id"] == t["id"]
    assert v["id"] == rows[1]["id"]


def test_void_twice_rejected(conn, pid):
    t = buy(conn, pid, 1000, 10.0)
    pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])
    with pytest.raises(PortfolioError, match="已经作废"):
        pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])


def test_void_marker_cannot_be_voided(conn, pid):
    t = buy(conn, pid, 1000, 10.0)
    v = pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])
    with pytest.raises(PortfolioError, match="作废记录本身"):
        pf.void_trade(conn, portfolio_id=pid, trade_id=v["id"])


def test_void_that_breaks_chain_is_rejected(conn, pid):
    """作废买入会让后面的卖出变成负持仓 → 必须拒绝，且什么都不改。"""
    t = buy(conn, pid, 1000, 10.0, 0.0, "2026-10-01")
    sell(conn, pid, 800, 12.0, 0.0, "2026-10-05")

    before = dict(conn.execute("SELECT cash_cents FROM portfolios WHERE id=?",
                               (pid,)).fetchone())
    with pytest.raises(PortfolioError, match="超过持仓"):
        pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])

    assert one(conn, pid)["shares"] == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM trades WHERE side='VOID'").fetchone()[0] == 0
    after = dict(conn.execute("SELECT cash_cents FROM portfolios WHERE id=?",
                              (pid,)).fetchone())
    assert after == before


def test_void_missing_trade(conn, pid):
    with pytest.raises(PortfolioError, match="找不到"):
        pf.void_trade(conn, portfolio_id=pid, trade_id=9999)


# ---------------------------------------------------------------- T5.4 仓位告警

def test_acceptance_t5_4_weight_uses_total_assets_with_cash(conn, pid):
    """占比 = 个股市值 ÷ 总资产（含现金）。现金算进去，超 10% 才红。"""
    conn.execute("INSERT INTO stocks(code, name, market, status) VALUES "
                 "('301269','华大九天','sz','active')")
    conn.commit()
    buy(conn, pid, 1000, 10.0)                 # 1 万元成本
    buy(conn, pid, 1000, 10.0, code="301269")
    pf.set_cash(conn, pid, money(90000))       # 买完再设现金 9 万

    # 两只票各给一个 15 元的价 → 各 15000 元市值，总资产 30000 + 90000 = 120000
    for code, price in ((CODE, 15.0), ("301269", 15.0)):
        conn.execute(
            """INSERT INTO quotes(code, price, quoted_at) VALUES (?,?,datetime('now'))""",
            (code, price))
    conn.commit()

    v = pf.valuation(conn, pid)
    assert v["totalAssetsCents"] == money(120000)
    assert v["marketValueCents"] == money(30000)
    assert v["cashWeightPct"] == pytest.approx(75.0)
    for row in v["positions"]:
        assert row["weightPct"] == pytest.approx(12.5)
        assert row["overLimit"] is True        # 12.5% > 10%
    assert set(v["overLimit"]) == {CODE, "301269"}


def test_weight_under_limit_not_flagged(conn, pid):
    buy(conn, pid, 1000, 10.0)
    pf.set_cash(conn, pid, money(100000))      # 买完再设现金，否则现金已被扣掉
    conn.execute("INSERT INTO quotes(code, price, quoted_at) VALUES (?,?,datetime('now'))",
                 (CODE, 10.0))
    conn.commit()
    v = pf.valuation(conn, pid)
    # 1 万市值 / (1 万 + 10 万) = 9.09%
    assert v["positions"][0]["weightPct"] == pytest.approx(9.09)
    assert v["positions"][0]["overLimit"] is False
    assert v["overLimit"] == []


def test_missing_quote_is_reported_not_zeroed(conn, pid):
    pf.set_cash(conn, pid, money(1000))
    buy(conn, pid, 1000, 10.0)
    v = pf.valuation(conn, pid)
    assert v["incomplete"] is True
    assert v["missingPrice"] == [CODE]
    assert v["positions"][0]["marketValueCents"] is None
    assert v["positions"][0]["weightPct"] is None
    assert v["totalPnlCents"] is None          # 有票没价就不给总盈亏，别给个偏小的数


def test_price_timestamp_is_exposed(conn, pid):
    buy(conn, pid, 100, 10.0)
    conn.execute(
        "INSERT INTO quotes(code, price, quote_time, quoted_at) VALUES (?,?,?,?)",
        (CODE, 12.0, "20261017150000", "2026-10-17T15:00:05+08:00"))
    conn.commit()
    row = pf.positions(conn, pid)[0]
    assert row["quotedAt"] == "2026-10-17T15:00:05+08:00"
    assert row["quoteTime"] == "20261017150000"


def test_realized_pnl_accumulates_across_sells(conn, pid):
    buy(conn, pid, 1000, 10.0)
    sell(conn, pid, 300, 12.0)
    sell(conn, pid, 200, 14.0)
    pos = one(conn, pid)
    # 第一笔赚 (12-10)*300=600；第二笔赚 (14-10)*200=800；合计 1400
    assert pos["realizedCents"] == money(1400)
    assert pos["shares"] == 500


def test_fully_closed_position_drops_out(conn, pid):
    buy(conn, pid, 1000, 10.0)
    sell(conn, pid, 1000, 11.0)
    assert pf.positions(conn, pid) == []
    led = pf.build_positions(conn, pid)
    assert led.positions[CODE].realized_cents == money(1000)


# ---------------------------------------------------------------- 组合

def test_default_portfolio_created_once(conn):
    a = pf.ensure_default(conn)
    b = pf.ensure_default(conn)
    assert a["id"] == b["id"]
    assert a["is_default"] == 1
    assert len(pf.list_portfolios(conn)) == 1


def test_set_cash_rejects_negative(conn, pid):
    with pytest.raises(PortfolioError, match="不能为负"):
        pf.set_cash(conn, pid, -1)


def test_audit_log_written(conn, pid):
    t = buy(conn, pid, 100, 10.0)
    pf.void_trade(conn, portfolio_id=pid, trade_id=t["id"])
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
    assert "trade.add" in actions
    assert "trade.void" in actions
