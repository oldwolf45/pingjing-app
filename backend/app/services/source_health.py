"""数据源健康页（DEV_PLAN T5.7 / PRD 5.7）。

状态灯的四色定义（PRD 写死的，不许自己发明）：
    绿 ok        最近 5 次成功率 ≥ 80%
    黄 degraded  50% ~ 80%
    红 down      < 50% 或熔断中
    灰 disabled  手动关闭

**为什么用最近 5 次而不是全历史**：一个接口上个月挂了三天，这个月全好，
还挂着红灯就没意义。人要看的是"现在能不能用"。

"测试"按钮发的是最小请求：只测一个代码、只要一个字段，不通就是不通。
测试结果本身也写进 source_calls，所以点完按钮状态灯会跟着变。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Callable

from app.services.sources import (
    FAIL_THRESHOLD,
    MAX_INTERVAL,
    MIN_INTERVAL,
    CONCURRENCY_PER_DOMAIN,
    SourceResult,
    fetch_kline,
    fetch_quote,
    make_client,
)

WINDOW = 5                 # 看最近几次
OK_RATE = 0.8
DEGRADED_RATE = 0.5
PROBE_CODE = "301095"      # 测试用的固定代码，广立微

CAPABILITY_LABELS = {
    "行情": "行情", "日K": "日K", "财务": "财务", "研报": "研报",
    "冷门度": "冷门度", "资金流": "资金流", "flow_rank": "资金流排名",
    "quote": "行情", "kline": "日K", "finance": "财务", "popularity": "冷门度",
    "flow": "资金流",
}

DEGRADE_RULES = [
    {"level": "ok", "label": "绿 · 正常", "rule": "最近 5 次成功率 ≥ 80%"},
    {"level": "degraded", "label": "黄 · 降级", "rule": "最近 5 次成功率 50%–80%，数据可能不全"},
    {"level": "down", "label": "红 · 不可用", "rule": "最近 5 次成功率 < 50%，或熔断已触发"},
    {"level": "disabled", "label": "灰 · 已关闭", "rule": "手动关闭，不再请求"},
]


def color_for(rate: float | None, breaker_open: bool, enabled: bool,
              calls: int) -> str:
    """把"成功率 + 熔断 + 开关"翻译成一个颜色。"""
    if not enabled:
        return "disabled"
    if breaker_open:
        return "down"
    if calls == 0 or rate is None:
        return "unknown"       # 还没调过：既不能说好也不能说坏
    if rate >= OK_RATE:
        return "ok"
    if rate >= DEGRADED_RATE:
        return "degraded"
    return "down"


def recent_stats(conn: sqlite3.Connection, source: str,
                 window: int = WINDOW) -> dict:
    rows = conn.execute(
        """SELECT ok, latency_ms, error, called_at FROM source_calls
            WHERE source=? ORDER BY id DESC LIMIT ?""", (source, window)).fetchall()
    n = len(rows)
    ok_n = sum(1 for r in rows if r["ok"])
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    last_err = next((r["error"] for r in rows if not r["ok"] and r["error"]), None)
    return {
        "calls": n,
        "okCount": ok_n,
        "rate": (ok_n / n) if n else None,
        "avgLatencyMs": int(sum(lat) / len(lat)) if lat else None,
        "lastError": last_err,
        "lastCalledAt": rows[0]["called_at"] if rows else None,
    }


def sources(conn: sqlite3.Connection, client=None) -> dict:
    """状态表。data_sources 里没登记但最近调过的源也列出来，不然看不见。"""
    registered = {r["name"]: dict(r) for r in conn.execute(
        "SELECT * FROM data_sources ORDER BY priority, name").fetchall()}
    called = [r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM source_calls").fetchall()]

    breaker = {}
    limits = {
        "concurrencyPerDomain": CONCURRENCY_PER_DOMAIN,
        "minIntervalMs": int(MIN_INTERVAL * 1000),
        "maxIntervalMs": int(MAX_INTERVAL * 1000),
        "failThreshold": FAIL_THRESHOLD,
    }
    if client is not None:
        snap = client.breaker.snapshot()
        breaker = snap
        limits.update({
            "concurrencyPerDomain": client.limiter.concurrency,
            "minIntervalMs": int(client.limiter.min_interval * 1000),
            "maxIntervalMs": int(client.limiter.max_interval * 1000),
            "failThreshold": client.breaker.threshold,
            "openSeconds": int(client.breaker.open_seconds),
        })

    names = list(registered)
    for n in called:
        if n not in registered:
            names.append(n)

    items = []
    for name in names:
        reg = registered.get(name) or {}
        st = recent_stats(conn, name)
        enabled = bool(reg.get("enabled", 1))
        is_open = bool(breaker.get(name, {}).get("open")) if isinstance(breaker, dict) else False
        color = color_for(st["rate"], is_open, enabled, st["calls"])
        items.append({
            "name": name,
            "capability": reg.get("capability") or CAPABILITY_LABELS.get(name, ""),
            "capabilityLabel": CAPABILITY_LABELS.get(reg.get("capability") or name, ""),
            "priority": reg.get("priority"),
            "enabled": enabled,
            "registered": bool(reg),
            "status": color,
            "calls": st["calls"],
            "okCount": st["okCount"],
            "rate": st["rate"],
            "ratePct": round(st["rate"] * 100, 1) if st["rate"] is not None else None,
            "avgLatencyMs": st["avgLatencyMs"],
            "lastError": st["lastError"],
            "lastCalledAt": st["lastCalledAt"],
            "breakerOpen": is_open,
        })

    order = {"down": 0, "degraded": 1, "unknown": 2, "ok": 3, "disabled": 4}
    items.sort(key=lambda i: (order.get(i["status"], 9), i["name"]))
    counts = {k: sum(1 for i in items if i["status"] == k)
              for k in ("ok", "degraded", "down", "disabled", "unknown")}
    return {"items": items, "counts": counts, "rules": DEGRADE_RULES,
            "limits": limits, "window": WINDOW}


def set_enabled(conn: sqlite3.Connection, name: str, enabled: bool) -> dict:
    conn.execute(
        """INSERT INTO data_sources(name, capability, enabled) VALUES (?,?,?)
           ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,
               updated_at=datetime('now','localtime')""",
        (name, CAPABILITY_LABELS.get(name, "其他"), 1 if enabled else 0))
    conn.execute(
        "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','source.toggle',?,?)",
        (name, "启用" if enabled else "关闭"))
    conn.commit()
    return {"name": name, "enabled": enabled}


# ---------------------------------------------------------------- 测试按钮

def _probe_quote(client, code):
    return fetch_quote(client, code)


def _probe_kline(client, code):
    return fetch_kline(client, code, count=5)


PROBES: dict[str, Callable[[Any, str], SourceResult]] = {
    "行情": _probe_quote, "腾讯行情": _probe_quote, "quote": _probe_quote,
    "日K": _probe_kline, "腾讯日K": _probe_kline, "kline": _probe_kline,
}


def test_source(name: str, *, conn: sqlite3.Connection | None = None,
                client=None, code: str = PROBE_CODE) -> dict:
    """发一个最小请求。没有探针的源返回 unsupported，不假装成功。"""
    probe = PROBES.get(name)
    if probe is None:
        return {"source": name, "ok": False, "supported": False,
                "error": "这个源还没有探测实现，不做假装成功的测试"}
    c = client or make_client(conn)
    res = probe(c, code)
    return {
        "source": name,
        "ok": bool(res.ok),
        "supported": True,
        "code": code,
        "latencyMs": res.latency_ms,
        "httpStatus": res.status,
        "error": res.error,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
