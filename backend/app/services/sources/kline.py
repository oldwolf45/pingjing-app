"""腾讯日 K 源。用来算近 5 日涨跌（热度/位置维度要用）。"""

from __future__ import annotations

import json
from typing import Any

from app.services.sources.base import HttpClient, SourceResult
from app.services.sources.quote import symbol

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
SOURCE = "腾讯日K"


def parse_kline(text: str, code: str) -> list[dict[str, Any]]:
    """解析成 [{date, open, close, high, low, volume}]，按日期升序。"""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return []
    node = (payload.get("data") or {}).get(symbol(code)) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            out.append({
                "date": str(row[0]),
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else None,
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["date"])
    return out


def change_pct(rows: list[dict], days: int = 5) -> float | None:
    """近 N 个交易日涨跌幅（%）。数据不足就返回 None，不硬凑。"""
    if len(rows) < 2:
        return None
    tail = rows[-min(days + 1, len(rows)):]
    first = tail[0].get("close")
    last = tail[-1].get("close")
    if not first or last is None:
        return None
    return round((last - first) / first * 100, 2)


def fetch_kline(client: HttpClient, code: str, count: int = 20) -> SourceResult:
    url = KLINE_URL.format(symbol=symbol(code), count=count)
    res = client.get(url, source=SOURCE, capability="kline", code=code)
    if res.ok:
        rows = parse_kline(res.raw or "", code)
        if not rows:
            res.ok = False
            res.error = f"日K解析为空：{(res.raw or '')[:120]}"
        else:
            res.data = rows
    return res
