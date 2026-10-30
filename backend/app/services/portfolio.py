"""持仓与交易（DEV_PLAN T5.2 / T5.3 / T5.4）。

三条必须算准的东西：

1. **移动加权成本，含手续费**
   买 1000@10 费 5 + 买 1000@12 费 5 → 成本 11.005 元/股（不是 11.00）
   手续费摊进成本，卖出时才算得清真实盈亏。

2. **卖出不改变成本价**
   卖 500 股之后单位成本还是 11.005。卖出的成本用当时的单位成本扣减，
   剩下的成本总额自然等比例减少，单位成本一分不动。

3. **作废要从第一条流水重算**
   移动加权和顺序强相关：作废第 2 笔会让第 3 笔的持仓基数变。
   所以作废不是"把那条删掉扣回来"，而是整条链重新跑一遍，
   顺便验证重算后中间任何一步都不会出现负持仓。作废是插一条 side='VOID'
   的反向记录，原记录永不修改（trades 是只追加表）。

**金额单位**：价格和金额一律整数分；成本价用 `cost_milli`（分 × 1000）。
10.005 元 = 1000.5 分 → cost_milli = 1000500。浮点存成本价，几百次加减后会漂。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from app.db import session

MILLI = 1000            # 成本价的小数位：分 × 1000
DEFAULT_ALERT_PCT = 10.0  # 单票占总资产超过这个百分比就变红（PRD 5.4）
SIDES = ("BUY", "SELL", "VOID")


from app.exceptions import ValidationError  # noqa: E402


class PortfolioError(ValidationError):
    """交易不合规。带一句给用户看的中文。"""


# ---------------------------------------------------------------- 数据结构

@dataclass
class Position:
    """一只票的持仓。cost_milli 是移动加权单位成本（分×1000，含手续费）。"""

    code: str
    shares: int = 0
    cost_milli: int = 0
    realized_cents: int = 0
    last_trade_date: str | None = None

    @property
    def cost_price_milli(self) -> int:
        """单位成本（分×1000）。空仓时保持最后一次的值，便于回看。"""
        return self.cost_milli

    @property
    def cost_cents(self) -> int:
        """持仓成本总额（分）。"""
        return self.cost_milli * self.shares // MILLI

    @property
    def cost_yuan(self) -> float:
        return round(self.cost_milli / MILLI / 100, 4)


@dataclass
class Ledger:
    """一条流水的重算结果。"""

    positions: dict[str, Position] = field(default_factory=dict)
    cash_delta_cents: int = 0
    applied: int = 0
    voided: list[int] = field(default_factory=list)

    def get(self, code: str) -> Position:
        return self.positions.setdefault(code, Position(code=code))


# ---------------------------------------------------------------- 纯计算

def _round_div(numerator: int, denominator: int) -> int:
    """四舍五入的整数除法（half up），避免 Python round 的银行家舍入。"""
    if denominator == 0:
        raise ZeroDivisionError("除数不能为 0")
    sign = -1 if (numerator < 0) != (denominator < 0) else 1
    n, d = abs(numerator), abs(denominator)
    return sign * ((n + d // 2) // d)


def replay(trades: Iterable[dict]) -> Ledger:
    """按时间顺序重算持仓和现金变动。纯函数，不碰数据库。

    参数每项至少要有：id / code / side / shares / price_cents / fee_cents。
    side='VOID' 的行用来标记"它指向的那条流水不算数"。
    """
    rows = list(trades)
    voided_ids = {r.get("voids_trade_id") for r in rows if r.get("side") == "VOID"}
    voided_ids.discard(None)

    led = Ledger(voided=sorted(int(x) for x in voided_ids))
    for r in rows:
        side = r.get("side")
        if side == "VOID" or r.get("id") in voided_ids:
            continue
        pos = led.get(r["code"])
        shares = int(r["shares"])
        price = int(r["price_cents"])
        fee = int(r.get("fee_cents") or 0)
        amount = shares * price

        if side == "BUY":
            # 成本总额（分×1000）加上本笔金额和费用，再摊到新的总股数上
            total_milli = pos.cost_milli * pos.shares + (amount + fee) * MILLI
            pos.shares += shares
            pos.cost_milli = _round_div(total_milli, pos.shares) if pos.shares else 0
            led.cash_delta_cents -= amount + fee
        elif side == "SELL":
            if shares > pos.shares:
                raise PortfolioError(
                    f"{r['code']} 卖出 {shares} 股超过持仓 {pos.shares} 股")
            cost_out = pos.cost_milli * shares // MILLI
            pos.realized_cents += amount - fee - cost_out
            pos.shares -= shares
            # 单位成本不动（T5.2 的核心要求）
            led.cash_delta_cents += amount - fee
        else:
            raise PortfolioError(f"未知的交易方向：{side}")

        if pos.shares == 0 and side == "SELL":
            pos.cost_milli = 0
        pos.last_trade_date = r.get("trade_date") or pos.last_trade_date
        led.applied += 1

    return led


def _validate_chain(trades: list[dict]) -> Ledger:
    """整链重算一遍，任何一步非法就抛错。作废之后必须重新验证。"""
    return replay(trades)


# ---------------------------------------------------------------- 组合

def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_default(conn: sqlite3.Connection) -> dict:
    """默认组合：没有就建一个。第一版只用一个组合，但表结构留了多组合的余地。"""
    row = conn.execute(
        "SELECT * FROM portfolios WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    if row:
        return dict(row)
    row = conn.execute("SELECT * FROM portfolios ORDER BY id LIMIT 1").fetchone()
    if row:
        conn.execute("UPDATE portfolios SET is_default=1 WHERE id=?", (row["id"],))
        conn.commit()
        return dict(conn.execute("SELECT * FROM portfolios WHERE id=?",
                                 (row["id"],)).fetchone())
    cur = conn.execute(
        "INSERT INTO portfolios(name, cash_cents, is_default) VALUES ('主账户', 0, 1)")
    conn.commit()
    return dict(conn.execute("SELECT * FROM portfolios WHERE id=?",
                             (cur.lastrowid,)).fetchone())


def list_portfolios(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM portfolios ORDER BY is_default DESC, id").fetchall()]


def set_cash(conn: sqlite3.Connection, portfolio_id: int, cash_cents: int) -> dict:
    if cash_cents < 0:
        raise PortfolioError("现金不能为负")
    with session.transaction(conn):
        conn.execute(
            "UPDATE portfolios SET cash_cents=?, updated_at=datetime('now','localtime') "
            "WHERE id=?", (int(cash_cents), portfolio_id))
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','portfolio.cash',?,?)",
            (str(portfolio_id), f"现金改为 {cash_cents} 分"),
        )
    return dict(conn.execute("SELECT * FROM portfolios WHERE id=?",
                             (portfolio_id,)).fetchone())


# ---------------------------------------------------------------- 流水

def _trade_rows(conn: sqlite3.Connection, portfolio_id: int) -> list[dict]:
    """按时间顺序取全部流水。同一交易日按 id 排，保证重算可复现。"""
    return [dict(r) for r in conn.execute(
        """SELECT * FROM trades WHERE portfolio_id=?
            ORDER BY trade_date ASC, id ASC""", (portfolio_id,)).fetchall()]


def list_trades(conn: sqlite3.Connection, portfolio_id: int,
                limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT t.*, s.name FROM trades t
             LEFT JOIN stocks s ON s.code = t.code
            WHERE t.portfolio_id=?
            ORDER BY t.trade_date DESC, t.id DESC LIMIT ?""",
        (portfolio_id, limit)).fetchall()]


def add_trade(conn: sqlite3.Connection, *, portfolio_id: int, code: str,
              side: str, shares: int, price_cents: int, fee_cents: int = 0,
              trade_date: str | None = None, note: str | None = None) -> dict:
    """记一笔交易。先整链重算验证，合法才落库。"""
    if side not in ("BUY", "SELL"):
        raise PortfolioError("只支持买入或卖出，作废请用 void_trade")
    if shares <= 0:
        raise PortfolioError("股数必须大于 0")
    if price_cents < 0 or fee_cents < 0:
        raise PortfolioError("价格和费用不能为负")
    if not conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone():
        raise PortfolioError(f"{code} 不在标的库里，请先添加标的")

    date = trade_date or _now_date()
    draft = {
        "id": -1, "code": code, "side": side, "shares": int(shares),
        "price_cents": int(price_cents), "fee_cents": int(fee_cents),
        "trade_date": date,
    }
    _validate_chain(_trade_rows(conn, portfolio_id) + [draft])

    with session.transaction(conn):
        cur = conn.execute(
            """INSERT INTO trades(portfolio_id, code, side, shares, price_cents,
                   fee_cents, trade_date, note) VALUES (?,?,?,?,?,?,?,?)""",
            (portfolio_id, code, side, int(shares), int(price_cents),
             int(fee_cents), date, note),
        )
        # 买入：现金减少「成交额 + 费用」；卖出：现金增加「成交额 − 费用」
        if side == "BUY":
            delta = -(shares * price_cents + fee_cents)
        else:
            delta = shares * price_cents - fee_cents
        conn.execute(
            "UPDATE portfolios SET cash_cents=cash_cents+?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (delta, portfolio_id))
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','trade.add',?,?)",
            (code, f"{side} {shares}@{price_cents} 费{fee_cents} {date}"),
        )
    return dict(conn.execute("SELECT * FROM trades WHERE id=?",
                             (cur.lastrowid,)).fetchone())


def void_trade(conn: sqlite3.Connection, *, portfolio_id: int, trade_id: int,
               trade_date: str | None = None, note: str | None = None) -> dict:
    """作废一笔交易：插一条 VOID 反向记录，然后整链重算验证。

    为什么不是把原记录改掉：trades 是只追加表（触发器禁止 UPDATE/DELETE）。
    作废要留痕，谁在什么时候撤了哪一笔，事后要能查。
    """
    target = conn.execute(
        "SELECT * FROM trades WHERE id=? AND portfolio_id=?",
        (trade_id, portfolio_id)).fetchone()
    if not target:
        raise PortfolioError("找不到这笔交易")
    if target["side"] == "VOID":
        raise PortfolioError("作废记录本身不能再作废")
    already = conn.execute(
        "SELECT 1 FROM trades WHERE side='VOID' AND voids_trade_id=?",
        (trade_id,)).fetchone()
    if already:
        raise PortfolioError("这笔交易已经作废过了")

    date = trade_date or _now_date()
    draft = {"id": -2, "code": target["code"], "side": "VOID",
             "shares": target["shares"], "price_cents": target["price_cents"],
             "fee_cents": target["fee_cents"], "trade_date": date,
             "voids_trade_id": trade_id}
    rows = _trade_rows(conn, portfolio_id) + [draft]
    # 作废会让后面的流水基数变化，可能直接把某个卖出顶成负持仓 → 必须重算验证
    _validate_chain(rows)

    # 作废就是把原交易对现金的影响反过来
    if target["side"] == "BUY":
        delta = target["shares"] * target["price_cents"] + target["fee_cents"]
    else:
        delta = -(target["shares"] * target["price_cents"] - target["fee_cents"])
    with session.transaction(conn):
        cur = conn.execute(
            """INSERT INTO trades(portfolio_id, code, side, shares, price_cents,
                   fee_cents, trade_date, note, voids_trade_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (portfolio_id, target["code"], "VOID", target["shares"],
             target["price_cents"], target["fee_cents"], date,
             note or f"作废 #{trade_id}", trade_id),
        )
        conn.execute(
            "UPDATE portfolios SET cash_cents=cash_cents+?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (delta, portfolio_id))
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','trade.void',?,?)",
            (target["code"], f"作废 #{trade_id}（{target['side']} {target['shares']}股）"),
        )
    return dict(conn.execute("SELECT * FROM trades WHERE id=?",
                             (cur.lastrowid,)).fetchone())


# ---------------------------------------------------------------- 持仓与估值

def build_positions(conn: sqlite3.Connection, portfolio_id: int) -> Ledger:
    return replay(_trade_rows(conn, portfolio_id))


def positions(conn: sqlite3.Connection, portfolio_id: int) -> list[dict]:
    """当前持仓（含 0 股的已清仓记录不列出）。"""
    led = build_positions(conn, portfolio_id)
    quotes = _quotes(conn, list(led.positions))
    out = []
    for code, pos in led.positions.items():
        if pos.shares <= 0:
            continue
        q = quotes.get(code) or {}
        out.append(_position_row(pos, q))
    out.sort(key=lambda r: (-(r["marketValueCents"] or 0), r["code"]))
    return out


def _quotes(conn: sqlite3.Connection, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    marks = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT * FROM quotes WHERE code IN ({marks})", codes).fetchall()
    return {r["code"]: dict(r) for r in rows}


def _position_row(pos: Position, quote: dict) -> dict:
    price = quote.get("price")
    price_cents = int(round(price * 100)) if price else None
    mv = price_cents * pos.shares if price_cents is not None else None
    cost = pos.cost_cents
    pnl = (mv - cost) if mv is not None else None
    return {
        "code": pos.code,
        "shares": pos.shares,
        "costMilli": pos.cost_milli,
        "costPrice": pos.cost_yuan,
        "costCents": cost,
        "price": price,
        "priceCents": price_cents,
        "marketValueCents": mv,
        "pnlCents": pnl,
        "pnlPct": round(pnl / cost * 100, 2) if (pnl is not None and cost) else None,
        "realizedCents": pos.realized_cents,
        "lastTradeDate": pos.last_trade_date,
        "quotedAt": quote.get("quoted_at"),
        "quoteTime": quote.get("quote_time"),
        "hasPrice": price_cents is not None,
    }


def valuation(conn: sqlite3.Connection, portfolio_id: int,
              alert_pct: float = DEFAULT_ALERT_PCT) -> dict:
    """持仓市值 + 仓位占比。

    占比 = 个股市值 ÷ 总资产（**含现金**）。不含现金的话，半仓的人每只都超 10%，
    告警就没意义了。总资产按有报价的持仓 + 现金算；有票没报价时标记
    incomplete 并把缺价的票列出来——宁可不给结论，也不给个偏小的分母。
    """
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?",
                      (portfolio_id,)).fetchone()
    if not pf:
        raise PortfolioError("组合不存在")
    cash = int(pf["cash_cents"] or 0)

    led = build_positions(conn, portfolio_id)
    held = {c: p for c, p in led.positions.items() if p.shares > 0}
    quotes = _quotes(conn, list(held))
    rows = [_position_row(p, quotes.get(c) or {}) for c, p in held.items()]

    missing = [r["code"] for r in rows if not r["hasPrice"]]
    mv_total = sum(r["marketValueCents"] or 0 for r in rows)
    total_assets = mv_total + cash

    for r in rows:
        r["weightPct"] = (round(r["marketValueCents"] / total_assets * 100, 2)
                          if r["marketValueCents"] is not None and total_assets > 0
                          else None)
        r["overLimit"] = bool(r["weightPct"] is not None and r["weightPct"] > alert_pct)

    rows.sort(key=lambda r: (-(r["marketValueCents"] or 0), r["code"]))
    cost_total = sum(r["costCents"] for r in rows)
    pnl_total = sum(r["pnlCents"] or 0 for r in rows)
    return {
        "portfolio": {"id": pf["id"], "name": pf["name"], "cashCents": cash},
        "positions": rows,
        "cashCents": cash,
        "marketValueCents": mv_total,
        "totalAssetsCents": total_assets,
        "totalPnlCents": pnl_total if not missing else None,
        "totalPnlPct": (round(pnl_total / cost_total * 100, 2)
                        if (cost_total and not missing) else None),
        "cashWeightPct": round(cash / total_assets * 100, 2) if total_assets > 0 else None,
        "alertPct": alert_pct,
        "overLimit": [r["code"] for r in rows if r["overLimit"]],
        "incomplete": bool(missing),
        "missingPrice": missing,
        "realizedCents": sum(p.realized_cents for p in led.positions.values()),
    }


def summary(conn: sqlite3.Connection, portfolio_id: int | None = None) -> dict:
    """给仪表盘/顶栏用的一句话口径。"""
    pid = portfolio_id or ensure_default(conn)["id"]
    v = valuation(conn, pid)
    return {
        "portfolioId": pid,
        "name": v["portfolio"]["name"],
        "positionCount": len(v["positions"]),
        "totalAssetsCents": v["totalAssetsCents"],
        "marketValueCents": v["marketValueCents"],
        "cashCents": v["cashCents"],
        "cashWeightPct": v["cashWeightPct"],
        "overLimit": v["overLimit"],
        "incomplete": v["incomplete"],
    }
