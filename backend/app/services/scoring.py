"""评分适配器：调原算法算分，把结果写进 score_snapshots。

原则：算法只调用、不修改。这里只做
  ① 参数拼装（把库里的字段转成算法要的形状）
  ② 结果落库（6 维分 + 总分 + 原始明细）
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from app import legacy_bridge

# 6 个维度的可读名（前端和报告共用一套，别各处自己起名）
DIMENSIONS = (
    ("track_barrier", "赛道壁垒", 2.5),
    ("earnings_growth", "业绩增长", 2.5),
    ("profitability", "盈利能力", 1.5),
    ("prosperity_check", "景气验证", 0.5),
    ("heat_position", "热度估值", 1.0),
    ("market_cap_elasticity", "市值弹性", 1.0),
)


def algo_version() -> str:
    return legacy_bridge.algo_version()


def make_financial(**kwargs) -> Any:
    """按 FinancialSnapshot 的字段构造，忽略多余键。"""
    mods = legacy_bridge.load()
    fields = set(mods["scoring"].FinancialSnapshot.__dataclass_fields__)
    clean = {k: v for k, v in kwargs.items() if k in fields}
    return mods["scoring"].FinancialSnapshot(**clean)


def score(code: str, name: str, sector: str, track_barrier_score: float,
          financial: Any, sector_heat_change: float | None = None,
          price_change_5d: float | None = None, peg: float | None = None,
          sector_heat_level: str | None = None, popularity_score: float | None = None,
          tuna_severity: int | None = None) -> Any:
    """调原算法，返回 StockScore。"""
    mods = legacy_bridge.load()
    return mods["scoring"].calculate_full_score(
        code=code, name=name, sector=sector,
        track_barrier_score=track_barrier_score,
        fin=financial,
        sector_heat_change=sector_heat_change,
        price_change_5d=price_change_5d,
        peg=peg,
        sector_heat_level=sector_heat_level,
        popularity_score=popularity_score,
        tuna_severity=tuna_severity,
    )


def score_dict(result: Any) -> dict:
    """把 StockScore 拍平成 {维度: 分} + total。"""
    sb = result.score
    out = {k: getattr(sb, k, None) for k, _, _ in DIMENSIONS}
    out["total"] = sb.total()
    return out


def score_from_cache_entry(entry: dict) -> Any:
    """用评分缓存（含完整输入）复现金样本，供对拍使用。"""
    fin = make_financial(**(entry.get("financial") or {}))
    quote = entry.get("quote") or {}
    pop = entry.get("popularity") or {}
    barrier = entry.get("barrier") or {}
    return score(
        code=entry.get("code") or "",
        name=entry.get("name") or "",
        sector=entry.get("sector") or "",
        track_barrier_score=barrier.get("score", 0.0),
        financial=fin,
        sector_heat_change=entry.get("sector_heat_change"),
        price_change_5d=quote.get("chg_pct"),
        peg=entry.get("peg"),
        sector_heat_level=entry.get("sector_heat_level"),
        popularity_score=pop.get("score"),
        tuna_severity=pop.get("severity"),
    )


def persist(conn: sqlite3.Connection, result: Any, as_of_date: str,
            source: str = "live", run_id: str | None = None,
            total_mv_yi: float | None = None, pool: str | None = None) -> int:
    """写一条评分快照。只追加表，绝不更新。返回新行 id。"""
    sb = result.score
    fin = result.financial
    fin_dict = {}
    for f in ("revenue_yoy", "net_profit_yoy", "deduct_profit_yoy", "order_growth",
              "gross_margin", "gross_margin_trend", "roe", "net_margin",
              "ocf_to_ni", "total_mv"):
        fin_dict[f] = getattr(fin, f, None)

    mv = total_mv_yi if total_mv_yi is not None else fin_dict.get("total_mv")

    cur = conn.execute(
        """INSERT INTO score_snapshots(
                code, as_of_date, track_barrier, earnings_growth, profitability,
                prosperity_check, heat_position, market_cap_elasticity, total, pool,
                total_mv_yi, financial, barrier_detail, notes, source, algo_version, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            result.code, as_of_date,
            sb.track_barrier, sb.earnings_growth, sb.profitability,
            sb.prosperity_check, sb.heat_position, sb.market_cap_elasticity,
            sb.total(), pool or result.pool, mv,
            json.dumps(fin_dict, ensure_ascii=False),
            json.dumps(getattr(result, "barrier_detail", None) or {}, ensure_ascii=False),
            json.dumps(result.notes or [], ensure_ascii=False),
            source, algo_version(), run_id,
        ),
    )
    return int(cur.lastrowid)


def latest(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute("SELECT * FROM v_score_current WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def history(conn: sqlite3.Connection, code: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM score_snapshots WHERE code=? ORDER BY as_of_date DESC, id DESC LIMIT ?",
        (code, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def breakdown(row: dict) -> list[dict]:
    """把一行快照拆成前端要的"六维拆解条"：得分 / 满分 / 占比。"""
    out = []
    for key, label, full in DIMENSIONS:
        v = row.get(key)
        out.append({
            "key": key,
            "label": label,
            "score": v,
            "full": full,
            "missing": v is None,
            "ratio": (v / full) if (v is not None and full) else None,
        })
    return out


def ensure_today_snapshot(conn: sqlite3.Connection, code: str,
                          as_of_date: str | None = None) -> dict | None:
    """确认某票在指定日期有快照；没有就返回 None（不假造成 0 分）。"""
    d = as_of_date or datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT * FROM v_score_daily WHERE code=? AND as_of_date=?", (code, d)
    ).fetchone()
    return dict(row) if row else None
