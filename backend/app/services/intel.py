"""情报从数据库读，替代 sector_intel.load_intel() 读文件。

为什么要换：打包后 `赛道数据库/intel/` 在安装目录，用户改不了；情报应该存在库里，
以后在界面上编辑。但**不能改算法文件**，所以在 bridge 层把
`sector_intel._load_intel_single` 换成读库版本——别名匹配、多赛道合并这些逻辑
全部复用原实现，一行不动。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app import legacy_bridge
from app.db import session

# 表里存成列、raw 里也有的字段
COLUMNS = (
    "cr3", "players_count", "substitution_rate", "substitution_urgency",
    "cert_cycle_months", "cert_level", "market_size_bn", "market_growth",
    "policy_level", "note",
)

# 只在 raw JSON 里、没有独立列的字段（壁垒引擎会用到）
RAW_ONLY = ("global_players", "invent_patent_ratio", "patent_year",
            "policy_notes", "sources", "human_reviewed", "updated")


def _row_to_intel(row: sqlite3.Row) -> dict:
    """把表行还原成与 intel/*.json 等价的 dict。"""
    out: dict[str, Any] = {"sector": row["sector"]}

    raw = {}
    if row["raw"]:
        try:
            raw = json.loads(row["raw"])
        except json.JSONDecodeError:
            raw = {}

    # 先铺 raw（保留全部原始字段），再用列值覆盖（列是权威）
    out.update({k: v for k, v in raw.items() if k not in COLUMNS})
    for c in COLUMNS:
        if c == "note":
            continue
        out[c] = row[c]

    if row["note"]:
        # 原文件里备注键叫 _note
        out["_note"] = row["note"]
    elif "_note" in raw:
        out["_note"] = raw["_note"]

    for k in RAW_ONLY:
        if k in raw and k not in out:
            out[k] = raw[k]

    out["source"] = "db"
    return out


def get_intel_by_sector_key(sector: str, conn: sqlite3.Connection | None = None) -> dict | None:
    """按赛道名精确查。"""
    c = conn or session.get_conn()
    row = c.execute("SELECT * FROM sector_intel WHERE sector=?", (sector,)).fetchone()
    return _row_to_intel(row) if row else None


def all_intel(conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    c = conn or session.get_conn()
    rows = c.execute("SELECT * FROM sector_intel ORDER BY sector").fetchall()
    return {r["sector"]: _row_to_intel(r) for r in rows}


def count(conn: sqlite3.Connection | None = None) -> int:
    c = conn or session.get_conn()
    return c.execute("SELECT COUNT(*) FROM sector_intel").fetchone()[0]


# ------------------------------------------------------------------
# 桥接：把算法模块的单文件读取换成读库
# ------------------------------------------------------------------

_patched = False


def install_db_loader(conn: sqlite3.Connection | None = None) -> None:
    """把 sector_intel._load_intel_single 指向库读取。

    幂等：重复调用只生效一次。
    找不到的赛道返回 None（与原实现一致），调用方会走 fallback。
    """
    global _patched
    if _patched:
        return

    mods = legacy_bridge.load()
    si = mods["sector_intel"]

    # 缓存：一次更新里同一赛道会被反复查询
    cache: dict[str, dict | None] = {}

    def _from_db(sector: str):
        if sector in cache:
            return cache[sector]
        v = get_intel_by_sector_key(sector, conn)
        cache[sector] = v
        return v

    si._load_intel_single = _from_db
    si._intel_cache = cache
    _patched = True


def clear_cache() -> None:
    mods = legacy_bridge.load()
    si = mods.get("sector_intel")
    if si is not None and hasattr(si, "_intel_cache"):
        si._intel_cache.clear()


def load_intel(sector: str, conn: sqlite3.Connection | None = None) -> dict | None:
    """对外入口：与 sector_intel.load_intel 行为一致（含别名与多赛道合并）。"""
    install_db_loader(conn)
    mods = legacy_bridge.load()
    try:
        return mods["sector_intel"].load_intel(sector)
    finally:
        clear_cache()   # 不同 conn 的数据源可能不同，避免串味
