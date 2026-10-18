"""池子状态与池变动事件。"""

from __future__ import annotations

import sqlite3

from app.db import session
from app.repo import stocks as repo_stocks

CAPACITY = 28


def distribution(conn: sqlite3.Connection | None = None) -> dict:
    """五池分布 + 名额口径。在池 = 候选 + 观察 + 储备，金枪鱼不占名额。"""
    c = conn or session.get_conn()
    rows = c.execute(
        "SELECT pool, COUNT(*) AS n FROM pool_states GROUP BY pool"
    ).fetchall()
    dist = {p: 0 for p in repo_stocks.POOLS}
    for r in rows:
        if r["pool"] in dist:
            dist[r["pool"]] = r["n"]

    in_pool = sum(dist[p] for p in repo_stocks.CAPACITY_POOLS)
    return {
        "counts": dist,
        "labels": {p: repo_stocks.POOL_LABELS[p] for p in repo_stocks.POOLS},
        "capacity": CAPACITY,
        "in_pool": in_pool,
        "gap": max(0, CAPACITY - in_pool),
        "over": max(0, in_pool - CAPACITY),
    }


def pending_tuna(conn: sqlite3.Connection | None = None) -> list[dict]:
    """已命中出池线但还没满 2 周的票（下周再命中就出池）。"""
    c = conn or session.get_conn()
    rows = c.execute(
        """SELECT s.code, s.name, ps.pool, ps.tuna_weeks, ps.tuna_reason, ps.tuna_hits
             FROM pool_states ps JOIN stocks s ON s.code = ps.code
            WHERE ps.tuna_weeks >= 1
              AND ps.pool IN ('candidate','observation','reserve')
            ORDER BY ps.tuna_weeks DESC, s.code""",
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pool_label"] = repo_stocks.POOL_LABELS.get(d["pool"], "")
        out.append(d)
    return out


def near_upgrade(conn: sqlite3.Connection | None = None, margin: float = 0.2) -> list[dict]:
    """距上一档 0.2 分以内的票。"""
    c = conn or session.get_conn()
    thresholds = {"reserve": 5.5, "observation": 6.5}
    rows = c.execute(
        """SELECT s.code, s.name, ps.pool, ps.consecutive_weeks_above, v.total
             FROM pool_states ps
             JOIN stocks s ON s.code = ps.code
             LEFT JOIN v_score_current v ON v.code = s.code
            WHERE ps.pool IN ('reserve','observation')
              AND v.total IS NOT NULL
            ORDER BY v.total DESC""",
    ).fetchall()
    out = []
    for r in rows:
        target = thresholds.get(r["pool"])
        if target is None:
            continue
        gap = target - r["total"]
        if 0 < gap <= margin + 1e-9:
            d = dict(r)
            d["target"] = target
            d["gap"] = round(gap, 2)
            d["pool_label"] = repo_stocks.POOL_LABELS.get(r["pool"], "")
            out.append(d)
    return out


def recent_events(conn: sqlite3.Connection | None = None, limit: int = 10) -> list[dict]:
    c = conn or session.get_conn()
    rows = c.execute(
        """SELECT id, code, name, from_pool, to_pool, reason, change_date
             FROM pool_events
            ORDER BY change_date DESC, id DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["from_label"] = repo_stocks.POOL_LABELS.get(d["from_pool"], d["from_pool"])
        d["to_label"] = repo_stocks.POOL_LABELS.get(d["to_pool"], d["to_pool"])
        d["direction"] = _direction(d["from_pool"], d["to_pool"])
        out.append(d)
    return out


def _direction(frm: str, to: str) -> str:
    """给界面画方向色点用。"""
    if frm in ("new", "") or frm is None:
        return "in"
    if to == "tuna":
        return "tuna"
    if to == "removed":
        return "out"
    order = ["reserve", "observation", "candidate"]
    if frm in order and to in order:
        return "up" if order.index(to) > order.index(frm) else "down"
    if to == "reserve" and frm == "tuna":
        return "back"
    return "move"


def coverage(conn: sqlite3.Connection | None = None) -> dict:
    """数据新鲜度：最近评分日期、距今天数、有分数的票数比例。"""
    c = conn or session.get_conn()
    row = c.execute("SELECT MAX(as_of_date) AS d FROM score_snapshots").fetchone()
    latest = row["d"] if row else None

    total = c.execute("SELECT COUNT(*) FROM stocks WHERE status='active'").fetchone()[0]
    covered = c.execute(
        "SELECT COUNT(*) FROM stocks s WHERE s.status='active' "
        "AND EXISTS (SELECT 1 FROM v_score_current v WHERE v.code=s.code)"
    ).fetchone()[0]

    days = None
    if latest:
        from datetime import date, datetime
        try:
            days = (date.today() - datetime.strptime(latest, "%Y-%m-%d").date()).days
        except ValueError:
            days = None

    return {
        "latest_score_date": latest,
        "days_since": days,
        "stale": bool(days is not None and days > 7),
        "stocks_total": total,
        "stocks_with_score": covered,
        "coverage_ratio": round(covered / total, 3) if total else None,
    }


def set_pool(conn: sqlite3.Connection, code: str, pool: str, reason: str,
             change_date: str, name: str | None = None) -> None:
    """改池 + 记事件。规则判定由 services/pools.py 负责，这里只落库。"""
    if pool not in repo_stocks.POOLS:
        raise ValueError(f"未知池子：{pool}")
    old = conn.execute("SELECT pool, name FROM pool_states ps "
                       "LEFT JOIN stocks s ON s.code=ps.code WHERE ps.code=?", (code,)).fetchone()
    from_pool = old["pool"] if old else "new"
    conn.execute(
        "UPDATE pool_states SET pool=?, updated_at=datetime('now','localtime') WHERE code=?",
        (pool, code),
    )
    if from_pool != pool:
        conn.execute(
            "INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date) "
            "VALUES (?,?,?,?,?,?)",
            (code, name or (old["name"] if old else None), from_pool, pool, reason, change_date),
        )
