"""腾讯行情源。返回当周市值、价格等，用于市值弹性和金枪鱼判定。

接口是 GBK 文本：`v_sh600519="1~贵州茅台~600519~1700.00~1680.00~..."`，
字段顺序固定但没有版本号，所以按索引取、逐字段容错，取不到就留空，
绝不因为一个字段缺失把整条行情废掉。
"""

from __future__ import annotations

from typing import Any

from app.services.sources.base import HttpClient, SourceResult

QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"
SOURCE = "腾讯行情"

# 字段索引（腾讯行情文本协议）
_IDX = {
    "name": 1,
    "code": 2,
    "price": 3,
    "prev_close": 4,
    "open": 5,
    "volume_lot": 6,
    "time": 30,
    "change": 31,
    "pct": 32,
    "high": 33,
    "low": 34,
    "amount_wan": 37,
    "turnover_pct": 38,
    "pe": 39,
    "float_mv_yi": 44,
    "total_mv_yi": 45,
    "pb": 46,
}

_NUMERIC = {
    "price", "prev_close", "open", "volume_lot", "change", "pct", "high", "low",
    "amount_wan", "turnover_pct", "pe", "float_mv_yi", "total_mv_yi", "pb",
}


def market_prefix(code: str) -> str:
    c = str(code)
    if c.startswith(("60", "68", "9")):
        return "sh"
    if c.startswith(("0", "3", "2")):
        return "sz"
    if c.startswith(("4", "8")):
        return "bj"
    return ""


def symbol(code: str) -> str:
    return f"{market_prefix(code)}{code}"


def parse_quote(text: str, code: str) -> dict[str, Any] | None:
    """解析腾讯行情文本。拿不到有效价格就返回 None（不编数）。"""
    if not text or "=" not in text:
        return None
    payload = text.split('="', 1)[-1].strip().rstrip('";')
    parts = payload.split("~")
    if len(parts) < 6:
        return None

    out: dict[str, Any] = {"code": str(code), "symbol": symbol(code)}
    for key, idx in _IDX.items():
        if idx >= len(parts):
            continue
        raw = parts[idx].strip()
        if not raw:
            continue
        if key in _NUMERIC:
            try:
                out[key] = float(raw)
            except ValueError:
                continue
        else:
            out[key] = raw

    if not out.get("price"):
        return None
    return out


def fetch_quote(client: HttpClient, code: str) -> SourceResult:
    url = QUOTE_URL.format(symbol=symbol(code))
    res = client.get(url, source=SOURCE, encoding="gbk", capability="quote", code=code)
    if res.ok:
        parsed = parse_quote(res.raw or "", code)
        if parsed is None:
            res.ok = False
            res.error = f"行情解析失败：{(res.raw or '')[:120]}"
        else:
            res.data = parsed
    return res
