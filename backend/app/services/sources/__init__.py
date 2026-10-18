"""数据源统一入口。

对外只暴露一个 `LiveSources`：编排层拿到它就能取行情、日K、财务、
冷门度、资金流，不用知道底层是腾讯还是东财，也不用管限流和熔断。

测试里换成 FakeSources（同一个鸭子类型）就能完全离线跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.sources.adapters import AlgoSources, tuna_info_from_popularity
from app.services.sources.base import (
    CONCURRENCY_PER_DOMAIN,
    FAIL_THRESHOLD,
    MAX_INTERVAL,
    MIN_INTERVAL,
    OPEN_SECONDS,
    CircuitBreaker,
    HttpClient,
    RateLimiter,
    SourceResult,
    db_recorder,
    domain_of,
)
from app.services.sources.kline import change_pct, fetch_kline, parse_kline
from app.services.sources.quote import fetch_quote, market_prefix, parse_quote, symbol

__all__ = [
    "LiveSources", "SourceResult", "CircuitBreaker", "RateLimiter", "HttpClient",
    "CONCURRENCY_PER_DOMAIN", "MIN_INTERVAL", "MAX_INTERVAL",
    "FAIL_THRESHOLD", "OPEN_SECONDS",
    "parse_quote", "parse_kline", "change_pct", "tuna_info_from_popularity",
    "market_prefix", "symbol", "domain_of", "make_client",
]


def make_client(conn=None, run_id: str | None = None,
                transport: Callable[..., tuple[int, bytes]] | None = None) -> HttpClient:
    recorder = db_recorder(conn, run_id) if conn is not None else None
    return HttpClient(transport=transport, recorder=recorder)


@dataclass
class LiveSources:
    """真实数据源。构造时给一个 HttpClient，测试时换 transport 即可离线。"""

    client: HttpClient

    def __post_init__(self) -> None:
        self._algo = AlgoSources(self.client)

    # 行情
    def quote(self, code: str) -> SourceResult:
        return fetch_quote(self.client, code)

    # 日K
    def kline(self, code: str, count: int = 20) -> SourceResult:
        return fetch_kline(self.client, code, count)

    # 财务
    def finance(self, code: str, refresh: bool = False) -> SourceResult:
        return self._algo.finance(code, refresh=refresh)

    # 冷门度（含金枪鱼判定）
    def popularity(self, code: str, force: bool = False) -> SourceResult:
        return self._algo.popularity(code, force=force)

    # 资金流
    def flow(self, code: str, days: int = 22) -> SourceResult:
        return self._algo.flow(code, days=days)


def sources_status(client: HttpClient) -> dict[str, Any]:
    """数据源健康页要的状态：熔断器快照 + 限流参数。"""
    return {
        "breaker": client.breaker.snapshot(),
        "limits": {
            "concurrency_per_domain": client.limiter.concurrency,
            "min_interval_ms": int(client.limiter.min_interval * 1000),
            "max_interval_ms": int(client.limiter.max_interval * 1000),
            "fail_threshold": client.breaker.threshold,
            "open_seconds": int(client.breaker.open_seconds),
        },
    }
