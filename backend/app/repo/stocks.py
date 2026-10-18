"""股票查询：列表、详情、搜索。SQL 只写在这一层。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import session

POOLS = ("candidate", "observation", "reserve", "tuna", "removed")

POOL_LABELS = {
    "candidate": "候选池",
    "observation": "观察池",
    "reserve": "储备池",
    "tuna": "金枪鱼池",
    "removed": "已移除",
}

POOL_ICONS = {
    "candidate": "🏆",
    "observation": "👀",
    "reserve": "🔍",
    "tuna": "🐟",
    "removed": "🗑",
}

# 名额只算这三个池，金枪鱼和已移除不占名额
CAPACITY_POOLS = ("candidate", "observation", "reserve")

SORTABLE = {
    "score": "v.total",
    "code": "s.code",
    "weeks": "ps.weeks_in_pool",
    "name": "s.name",
}

_LIST_SQL = """
SELECT
    s.code, s.name, s.market,
    p.sector,
    ps.pool, ps.weeks_in_pool, ps.price_ready, ps.entry_week, ps.entry_score,
    ps.tuna_weeks, ps.tuna_reason, ps.consecutive_weeks_above, ps.remove_reason,
    v.total, v.as_of_date, v.source AS score_source, v.total_mv_yi,
    v.track_barrier, v.earnings_growth, v.profitability,
    v.prosperity_check, v.heat_position, v.market_cap_elasticity,
    p.ideal_buy_text, p.ideal_buy_price, p.stop_loss_text, p.stop_loss_price,
    p.target_text, p.target_price, p.position_suggest_raw, p.position_suggest_cap, p.position_capped
  FROM stocks s
  JOIN pool_states ps ON ps.code = s.code
  LEFT JOIN stock_profiles p ON p.code = s.code
  LEFT JOIN v_score_current v ON v.code = s.code
"""


def _row_to_item(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["pool_label"] = POOL_LABELS.get(d.get("pool"), d.get("pool"))
    d["pool_icon"] = POOL_ICONS.get(d.get("pool"), "")
    # 兜底分：壁垒走 1.2 兜底算出来的，界面必须标出来
    d["is_fallback"] = (d.get("score_source") == "fallback_manual")
    # 仓位必须裁剪到 10 并标注
    raw = d.get("position_suggest_raw")
    cap = d.get("position_suggest_cap")
    d["position_display"] = cap
    d["position_capped"] = bool(d.get("position_capped"))
    d["position_note"] = f"原始 {raw}%，已按 10% 上限裁剪" if d.get("position_capped") else ""
    return d


def list_stocks(conn: sqlite3.Connection | None = None, pool: str | None = None,
                keyword: str = "", sort: str = "score", desc: bool = True) -> list[dict]:
    c = conn or session.get_conn()
    sql = _LIST_SQL
    where = []
    args: list[Any] = []

    if pool and pool != "all":
        if pool not in POOLS:
            raise ValueError(f"未知池子：{pool}")
        where.append("ps.pool = ?")
        args.append(pool)
    if keyword:
        kw = f"%{keyword.strip()}%"
        where.append("(s.code LIKE ? OR s.name LIKE ? OR p.sector LIKE ?)")
        args += [kw, kw, kw]
    if where:
        sql += " WHERE " + " AND ".join(where)

    col = SORTABLE.get(sort, "v.total")
    direction = "DESC" if desc else "ASC"
    # 空分排最后，不要混在中间
    sql += f" ORDER BY (v.total IS NULL), {col} {direction}, s.code ASC"

    rows = c.execute(sql, args).fetchall()
    return [_row_to_item(r) for r in rows]


def get_stock(code: str, conn: sqlite3.Connection | None = None) -> dict | None:
    c = conn or session.get_conn()
    row = c.execute(_LIST_SQL + " WHERE s.code = ?", (code,)).fetchone()
    if row is None:
        return None
    d = _row_to_item(row)

    prof = c.execute("SELECT * FROM stock_profiles WHERE code=?", (code,)).fetchone()
    if prof:
        d["profile"] = dict(prof)
        for k in ("tags", "main_business"):
            try:
                d["profile"][k] = json.loads(prof[k]) if prof[k] else None
            except (json.JSONDecodeError, TypeError):
                pass

    ps = c.execute("SELECT * FROM pool_states WHERE code=?", (code,)).fetchone()
    if ps:
        extra = dict(ps)
        for k in ("tuna_hits", "back_miss", "exit_snapshot"):
            try:
                extra[k] = json.loads(ps[k]) if ps[k] else None
            except (json.JSONDecodeError, TypeError):
                pass
        d["pool_state"] = extra
    return d


def exists(code: str, conn: sqlite3.Connection | None = None) -> bool:
    c = conn or session.get_conn()
    return c.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone() is not None


def search(conn: sqlite3.Connection | None, keyword: str, limit: int = 20) -> list[dict]:
    c = conn or session.get_conn()
    kw = f"%{keyword.strip()}%"
    rows = c.execute(
        """SELECT s.code, s.name, p.sector, ps.pool
             FROM stocks s
             LEFT JOIN stock_profiles p ON p.code=s.code
             LEFT JOIN pool_states ps ON ps.code=s.code
            WHERE s.code LIKE ? OR s.name LIKE ? OR p.sector LIKE ?
            ORDER BY (ps.code IS NULL), s.code
            LIMIT ?""",
        (kw, kw, kw, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pool_label"] = POOL_LABELS.get(d.get("pool"), "") if d.get("pool") else ""
        out.append(d)
    return out


def all_codes(conn: sqlite3.Connection | None = None, pool_in: tuple[str, ...] | None = None) -> list[str]:
    c = conn or session.get_conn()
    if pool_in:
        q = ",".join("?" * len(pool_in))
        rows = c.execute(
            f"""SELECT s.code FROM stocks s JOIN pool_states ps ON ps.code=s.code
                 WHERE ps.pool IN ({q}) ORDER BY s.code""", pool_in).fetchall()
    else:
        rows = c.execute("SELECT code FROM stocks ORDER BY code").fetchall()
    return [r[0] for r in rows]
