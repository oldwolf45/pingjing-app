"""决策清单（DEV_PLAN T5.1）。

每周给池子里的每只票拍一个板：加仓 / 持有 / 减仓 / 观察。

**为什么没有提交按钮**：点一下就是决定。周日晚边看边点，中间随时关窗口，
不会因为忘了按提交而丢掉半小时的思考。所以每次点击直接落库（UPSERT）。

**为什么按周存**：`decision_notes` 的唯一键是 (code, week)。这一周怎么想的
要留档——下周复盘时能看到「上周我说减仓，结果它涨了 20%」，
这比任何事后总结都值钱。新的一周打开，看到的是上周的决定（当作参考），
本周没点之前不冒充本周的结论。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from app.db import session

DECISIONS = ("add", "hold", "trim", "watch")
DECISION_LABELS = {
    "add": "加仓", "hold": "持有", "trim": "减仓", "watch": "观察",
}
ACTIVE_POOLS = ("candidate", "observation", "reserve")


class DecisionError(ValueError):
    pass


def week_key(day: str | date | None = None) -> str:
    """ISO 周，形如 2026-W42。周一为一周开始。"""
    if day is None:
        d = date.today()
    elif isinstance(day, str):
        d = datetime.strptime(day[:10], "%Y-%m-%d").date()
    else:
        d = day
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def set_decision(conn: sqlite3.Connection, *, code: str, week: str,
                 decision: str, note: str | None = None) -> dict:
    """点一下即存。同一周重复点就是改主意。"""
    if decision not in DECISIONS:
        raise DecisionError(f"决策只能是 {'/'.join(DECISIONS)}")
    if not conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone():
        raise DecisionError(f"{code} 不在标的库里")

    with session.transaction(conn):
        conn.execute(
            """INSERT INTO decision_notes(code, week, decision, note)
               VALUES (?,?,?,?)
               ON CONFLICT(code, week) DO UPDATE SET
                   decision=excluded.decision, note=excluded.note,
                   updated_at=datetime('now','localtime')""",
            (code, week, decision, note),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','decision.set',?,?)",
            (code, f"{week} {decision}"),
        )
    return get_decision(conn, code, week) or {}


def get_decision(conn: sqlite3.Connection, code: str, week: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM decision_notes WHERE code=? AND week=?", (code, week)).fetchone()
    return _row(row) if row else None


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["decisionLabel"] = DECISION_LABELS.get(d["decision"], d["decision"])
    return d


def previous_decision(conn: sqlite3.Connection, code: str,
                      week: str) -> dict | None:
    """这只票在本周之前最后一次决定。用来「下周打开还在」。"""
    row = conn.execute(
        """SELECT * FROM decision_notes
            WHERE code=? AND week < ?
            ORDER BY week DESC LIMIT 1""", (code, week)).fetchone()
    return _row(row) if row else None


def week_list(conn: sqlite3.Connection, week: str | None = None) -> dict:
    """本周清单：池内所有票 + 本周决定 + 上次决定。

    已移除和金枪鱼池不进来——那两类不需要每周拍板（一个已经出局，
    一个在等回归条件）。跟池子页的「在池」口径保持一致。
    """
    w = week or week_key()
    marks = ",".join("?" * len(ACTIVE_POOLS))
    rows = conn.execute(
        f"""SELECT s.code, s.name, COALESCE(p.sector,'') AS sector,
                   ps.pool, ps.price_ready, ps.weeks_in_pool,
                   cur.decision AS weekDecision, cur.note AS weekNote,
                   cur.updated_at AS decidedAt
              FROM pool_states ps
              JOIN stocks s ON s.code = ps.code
              LEFT JOIN stock_profiles p ON p.code = ps.code
              LEFT JOIN decision_notes cur ON cur.code = ps.code AND cur.week = ?
             WHERE ps.pool IN ({marks}) AND s.status <> 'delisted'
             ORDER BY CASE ps.pool WHEN 'candidate' THEN 0
                                   WHEN 'observation' THEN 1 ELSE 2 END,
                      s.code""",
        (w, *ACTIVE_POOLS),
    ).fetchall()

    # 每只票补上"上次怎么想的"，前端灰色小字展示，不冒充本周结论
    prev = {}
    for r in rows:
        p = previous_decision(conn, r["code"], w)
        if p:
            prev[r["code"]] = p

    items = []
    for r in rows:
        d = dict(r)
        last = prev.get(d["code"])
        d["weekDecisionLabel"] = DECISION_LABELS.get(d["weekDecision"] or "", "")
        d["lastDecision"] = last["decision"] if last else None
        d["lastDecisionLabel"] = last["decisionLabel"] if last else ""
        d["lastDecisionWeek"] = last["week"] if last else None
        d["lastDecisionNote"] = last["note"] if last else None
        items.append(d)

    decided = sum(1 for i in items if i["weekDecision"])
    counts = {k: sum(1 for i in items if i["weekDecision"] == k) for k in DECISIONS}
    return {
        "week": w,
        "items": items,
        "total": len(items),
        "decided": decided,
        "undecided": len(items) - decided,
        "counts": counts,
    }


def weeks_with_notes(conn: sqlite3.Connection, limit: int = 12) -> list[dict]:
    rows = conn.execute(
        """SELECT week, COUNT(*) AS n
             FROM decision_notes GROUP BY week
            ORDER BY week DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
