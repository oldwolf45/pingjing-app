"""人工添加标的（PRD A.7 入口②，DEV_PLAN T5.9 的服务层）。

两条硬约束，写在这里就不许绕开：
    1. 只能进储备池。不接受前端传池参数——传了也不认。
       人工加的票通常没情报（壁垒走 1.2 兜底，总分最高 6.0），
       也没经过一次完整更新验证，储备池是观察位。
    2. 当周不给买入价（price_ready=False），第 2 周数据确认后才给数字。

不按分数跳层，是这条入口和"补票"最重要的区别。
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app.db import session
from app.services import pool_rules


class ManualEntryError(ValueError):
    """人工添加被拒绝（重复、参数不对等）。"""


def add_stock(conn: sqlite3.Connection, *, code: str, name: str,
              sector: str | None = None, total_score: float | None = None,
              today: str | None = None, note: str | None = None,
              force_pool: str | None = None) -> dict:
    """添加一只票到跟踪池。已存在则拒绝，不覆盖。

    force_pool 只是为了让调用方能明确表达意图，但传什么都会被按回 reserve——
    池子由入口决定，不由调用方决定。
    """
    code = str(code).strip()
    if not code or not name:
        raise ManualEntryError("代码和名称都必须填")

    today = today or date.today().isoformat()
    exists = conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone()
    if exists:
        raise ManualEntryError(f"{code} 已在跟踪列表里，不要重复添加")

    # 必须显式提交：接口层是另一条线程/连接，不提交就等于没加
    with session.transaction(conn):
        conn.execute(
            "INSERT INTO stocks(code, name, market, status) VALUES(?,?,?,'active')",
            (code, name, _guess_market(code)),
        )
        conn.execute(
            """INSERT INTO stock_profiles(code, sector, notes) VALUES(?,?,?)""",
            (code, sector, note),
        )
        conn.execute(
            """INSERT INTO pool_states(
                   code, pool, weeks_in_pool, entry_week, entry_score, price_ready)
               VALUES(?, 'reserve', 0, ?, ?, 0)""",
            (code, today, total_score),
        )
        conn.execute(
            """INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date)
               VALUES(?,?, 'new', 'reserve', ?, ?)""",
            (code, name, "人工添加（只进储备池，当周无买入价）", today),
        )
        conn.execute(
            "INSERT INTO audit_log(action, target, detail) VALUES('manual_add', ?, ?)",
            (code, f"人工添加 {name}，进储备池，分数 {total_score}"),
        )
    return {
        "code": code,
        "name": name,
        "pool": "reserve",
        "price_ready": False,
        "weeks_in_pool": 0,
        "entry_week": today,
        "requested_pool": force_pool or None,
        "pool_forced_to": "reserve" if force_pool and force_pool != "reserve" else None,
    }


def _guess_market(code: str) -> str:
    c = str(code)
    if c.startswith(("60", "68", "9")):
        return "sh"
    if c.startswith(("0", "3", "2")):
        return "sz"
    if c.startswith(("4", "8")):
        return "bj"
    return ""


def refill_from_candidates(conn: sqlite3.Connection, candidates: list[dict],
                           capacity: int = pool_rules.POOL_CAPACITY,
                           today: str | None = None) -> dict:
    """出一只补一只。补不上就空着（A.7 入口①）。

    返回 {"picked": 真补进来的票, "screening": 每只候选的判定与按理由的计数}。
    为什么把 screening 一起带出来：名额空着的时候，用户第一个问题就是
    「为什么没补上」——是市值超了还是分数不够，这两件事得分开说清楚。
    """
    today = today or date.today().isoformat()
    left = pool_rules.slots_left(conn, capacity)
    screening = pool_rules.screen_candidates(candidates, left)
    picked = screening["picked"]
    out: list[dict] = []
    with session.transaction(conn):
        for c in picked:
            code = str(c["code"])
            if conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO stocks(code, name, market, status) VALUES(?,?,?,'active')",
                (code, c.get("name") or code, _guess_market(code)),
            )
            conn.execute(
                "INSERT INTO stock_profiles(code, sector) VALUES(?,?)",
                (code, c.get("sector")),
            )
            conn.execute(
                """INSERT INTO pool_states(
                       code, pool, weeks_in_pool, entry_week, entry_score, price_ready)
                   VALUES(?,?,0,?,?,0)""",
                (code, c["pool"], today, c.get("total_score")),
            )
            conn.execute(
                """INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date)
                   VALUES(?,?, 'new', ?, ?, ?)""",
                (code, c.get("name"), c["pool"],
                 f"补票（{c.get('total_score')} 分按分数定层）", today),
            )
            out.append(c)
    return {"picked": out, "screening": screening}
