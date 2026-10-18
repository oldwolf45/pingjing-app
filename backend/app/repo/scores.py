"""评分查询：排行、拆解、趋势可用性判断。"""

from __future__ import annotations

import json
import sqlite3

from app.db import session
from app.services import scoring

# 趋势至少要这么多个不同日期才有意义（PRD 3.3：不足就不画线）
TREND_MIN_POINTS = 8


def top(conn: sqlite3.Connection | None = None, limit: int = 7) -> list[dict]:
    c = conn or session.get_conn()
    rows = c.execute(
        """SELECT s.code, s.name, p.sector, ps.pool, v.total, v.as_of_date, v.source
             FROM stocks s
             JOIN pool_states ps ON ps.code=s.code
             LEFT JOIN stock_profiles p ON p.code=s.code
             JOIN v_score_current v ON v.code=s.code
            ORDER BY v.total DESC, s.code
            LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_fallback"] = (d.get("source") == "fallback_manual")
        out.append(d)
    return out


def detail(conn: sqlite3.Connection | None, code: str) -> dict | None:
    """详情页要的全套：当前分、六维拆解、壁垒明细、财务、历史。"""
    c = conn or session.get_conn()
    row = c.execute("SELECT * FROM v_score_current WHERE code=?", (code,)).fetchone()
    if row is None:
        return {
            "code": code,
            "has_score": False,
            "dimensions": scoring.breakdown({}),
            "history": [],
            "trend": {"available": False, "points": 0, "needed": TREND_MIN_POINTS},
            "financial": None,
            "barrier": None,
            "notes": [],
        }

    d = dict(row)
    fin = _loads(row["financial"])
    barrier = _loads(row["barrier_detail"])
    notes = _loads(row["notes"]) or []

    hist = c.execute(
        """SELECT as_of_date, total, track_barrier, earnings_growth, profitability,
                  prosperity_check, heat_position, market_cap_elasticity, source
             FROM score_snapshots WHERE code=?
            ORDER BY as_of_date ASC, id ASC""",
        (code,),
    ).fetchall()
    history = [dict(h) for h in hist]
    dates = sorted({h["as_of_date"] for h in history})

    return {
        "code": code,
        "has_score": True,
        "as_of_date": d["as_of_date"],
        "total": d["total"],
        "pool": d.get("pool"),
        "score_source": d.get("source"),
        "is_fallback": d.get("source") == "fallback_manual",
        "total_mv_yi": d.get("total_mv_yi"),
        "algo_version": d.get("algo_version"),
        "dimensions": scoring.breakdown(d),
        "financial": fin,
        "barrier": barrier,
        "notes": notes,
        "history": history,
        "trend": {
            "available": len(dates) >= TREND_MIN_POINTS,
            "points": len(dates),
            "needed": TREND_MIN_POINTS,
            "dates": dates,
        },
    }


def _loads(v):
    if not v:
        return None
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return None


def latest_date(conn: sqlite3.Connection | None = None) -> str | None:
    c = conn or session.get_conn()
    row = c.execute("SELECT MAX(as_of_date) AS d FROM score_snapshots").fetchone()
    return row["d"] if row else None


def count_snapshots(conn: sqlite3.Connection | None = None) -> int:
    c = conn or session.get_conn()
    return c.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0]
