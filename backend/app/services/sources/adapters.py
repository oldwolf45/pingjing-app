"""把算法层现成的取数函数包成统一数据源。

algo/ 下的 fetcher 不许改（DEV_PLAN 4.1），但它们内部自己发请求、
没有限流也没有失败留痕。所以这里在外面套一层：串行、有间隔、失败可查。

domain 用的是逻辑名（finance / popularity / flow），不是真实域名。
限流器和熔断器只把它当 key 用，按"这一类数据"整体限速，
比按真实域名更保守——一个里子源挂了不会连带另一个。
"""

from __future__ import annotations

from typing import Any

from app import legacy_bridge
from app.services.sources.base import HttpClient, SourceResult

FINANCE_DOMAIN = "finance.eastmoney"
POPULARITY_DOMAIN = "popularity.eastmoney"
FLOW_DOMAIN = "flow.sina"


class AlgoSources:
    """算法层取数的统一出口。"""

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    # ---------------- 财务 ----------------
    def finance(self, code: str, refresh: bool = False) -> SourceResult:
        mods = legacy_bridge.load()
        return self.client.wrap(
            "财务", FINANCE_DOMAIN, mods["fin_fetcher"].fetch_financial,
            str(code), use_cache=not refresh, refresh=refresh,
            capability="finance", code=code,
        )

    # ---------------- 冷门度（含金枪鱼判定） ----------------
    def popularity(self, code: str, force: bool = False) -> SourceResult:
        mods = legacy_bridge.load()
        return self.client.wrap(
            "冷门度", POPULARITY_DOMAIN, mods["popularity"].evaluate,
            str(code), use_cache=not force, force=force,
            capability="popularity", code=code,
        )

    # ---------------- 资金流 ----------------
    def flow(self, code: str, days: int = 22) -> SourceResult:
        mods = legacy_bridge.load()
        return self.client.wrap(
            "资金流", FLOW_DOMAIN, mods["flow_fetcher"].fetch_flow, str(code), days=days,
            capability="flow", code=code,
        )

    def hot_rank(self) -> SourceResult:
        mods = legacy_bridge.load()
        return self.client.wrap("资金流排名", FLOW_DOMAIN, mods["flow_fetcher"].fetch_rank_top100,
                                capability="flow_rank")


def tuna_info_from_popularity(payload: Any) -> dict | None:
    """从冷门度结果里挑出池子判定要用的字段。

    字段口径以算法层为准（popularity_engine.evaluate 的返回）：
        tuna_flag / tuna_reason / tuna_reasons / tuna_severity / details
    details 里要有 reports_12m（研报篇数）、active_ratio_pct（主动资金占比）、
    total_mv_yi（总市值），回归判定和出池判定都读这三个。
    """
    if not isinstance(payload, dict):
        return None
    out = {
        "tuna_flag": bool(payload.get("tuna_flag")),
        "tuna_reason": payload.get("tuna_reason"),
        "tuna_reasons": list(payload.get("tuna_reasons") or []),
        "tuna_severity": payload.get("tuna_severity"),
        "details": dict(payload.get("details") or {}),
        "popularity_score": payload.get("score"),
    }
    return out
