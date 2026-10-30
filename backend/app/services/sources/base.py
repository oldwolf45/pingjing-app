"""数据源基础设施：限流、熔断、调用留痕。

三条硬约束（DEV_PLAN T4.2，数值写死在常量里，不给随手改的空间）：
    单域名并发 4
    同一域名两次请求间隔 150~350ms（随机，避免整齐撞车）
    连续 5 次失败熔断 10 分钟

所有取数都要落 source_calls 表。目的不是"记日志"，而是事后能回答
"那次更新到底哪个源挂了、原文是什么"——这是整个系统可信度的一部分。

设计上留了两个注入点，方便离线测试：
    transport   —— 真实 HTTP 的替身，(url, headers, timeout) -> (status, body)
    clock/sleep —— 时间源，让限流和熔断能被确定性地验证
"""

from __future__ import annotations

import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

CONCURRENCY_PER_DOMAIN = 4
MIN_INTERVAL = 0.15
MAX_INTERVAL = 0.35
FAIL_THRESHOLD = 5
OPEN_SECONDS = 600
DEFAULT_TIMEOUT = 20


def domain_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or "unknown"


@dataclass
class SourceResult:
    """一次取数的结果。失败也必须带着可读的原文，不许只留一句"失败"。"""

    ok: bool
    source: str
    capability: str = ""          # quote / kline / finance / popularity / flow
    code: str | None = None
    url: str = ""
    data: Any = None
    error: str | None = None
    status: int | None = None
    latency_ms: int = 0
    raw: str | None = None
    skipped: str | None = None  # 被熔断跳过时填原因

    def brief(self) -> str:
        if self.ok:
            return f"{self.source} ok {self.latency_ms}ms"
        if self.skipped:
            return f"{self.source} 跳过：{self.skipped}"
        return f"{self.source} 失败 {self.latency_ms}ms：{self.error}"


class RateLimiter:
    """按域名限并发 + 限间隔。"""

    def __init__(self, concurrency: int = CONCURRENCY_PER_DOMAIN,
                 min_interval: float = MIN_INTERVAL,
                 max_interval: float = MAX_INTERVAL,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[float, float], float] = random.uniform) -> None:
        self.concurrency = concurrency
        self.min_interval = min_interval
        self.max_interval = max_interval
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._sem: dict[str, threading.Semaphore] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _semaphore(self, domain: str) -> threading.Semaphore:
        with self._lock:
            sem = self._sem.get(domain)
            if sem is None:
                sem = threading.Semaphore(self.concurrency)
                self._sem[domain] = sem
            return sem

    @contextmanager
    def slot(self, domain: str):
        sem = self._semaphore(domain)
        sem.acquire()
        try:
            gap = self._rng(self.min_interval, self.max_interval)
            last = self._last.get(domain)
            now = self._clock()
            if last is not None:
                wait = last + gap - now
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
            self._last[domain] = now
            yield
        finally:
            sem.release()


class CircuitBreaker:
    """连续失败阈值熔断，到点自动半开（放一次请求试水）。"""

    def __init__(self, threshold: int = FAIL_THRESHOLD,
                 open_seconds: float = OPEN_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, domain: str) -> bool:
        with self._lock:
            opened = self._opened_at.get(domain)
        if opened is None:
            return False
        if self._clock() - opened >= self.open_seconds:
            # 半开：清掉开启时间，放一次请求进去试
            with self._lock:
                self._opened_at.pop(domain, None)
                self._fails[domain] = 0
            return False
        return True

    def remaining(self, domain: str) -> int:
        with self._lock:
            opened = self._opened_at.get(domain)
        if opened is None:
            return 0
        return max(0, int(self.open_seconds - (self._clock() - opened)))

    def record(self, domain: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._fails[domain] = 0
                self._opened_at.pop(domain, None)
                return
            self._fails[domain] = self._fails.get(domain, 0) + 1
            if self._fails[domain] >= self.threshold and domain not in self._opened_at:
                self._opened_at[domain] = self._clock()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "fails": dict(self._fails),
                "opened_at": dict(self._opened_at),
            }


def default_transport(url: str, headers: dict | None, timeout: float) -> tuple[int, bytes]:
    """真实 HTTP。默认不走系统/环境代理——行情源都是直连。

    需要走代理时设 ZISUYE_HTTP_PROXY（例如公司网络）。
    """
    import os

    proxy = os.environ.get("ZISUYE_HTTP_PROXY")
    handlers: list[Any] = []
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b""
    except urllib.error.URLError as exc:
        raise ConnectionError(f"{type(exc.reason).__name__}: {exc.reason}") from exc


class HttpClient:
    """带限流、熔断、留痕的 GET。请求失败不抛异常，一律包成 SourceResult。"""

    def __init__(self, transport: Callable[..., tuple[int, bytes]] | None = None,
                 limiter: RateLimiter | None = None,
                 breaker: CircuitBreaker | None = None,
                 recorder: Callable[[SourceResult], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._transport = transport or default_transport
        self.limiter = limiter or RateLimiter()
        self.breaker = breaker or CircuitBreaker()
        self._recorder = recorder
        self._clock = clock

    def _record(self, res: SourceResult) -> SourceResult:
        if self._recorder:
            try:
                self._recorder(res)
            except Exception:  # noqa: BLE001 留痕失败不能反过来把取数搞挂
                pass
        return res

    def get(self, url: str, source: str, encoding: str = "utf-8",
            headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
            capability: str = "", code: str | None = None) -> SourceResult:
        domain = domain_of(url)
        if self.breaker.is_open(domain):
            return self._record(SourceResult(
                ok=False, source=source, url=url, capability=capability, code=code,
                skipped=f"{domain} 熔断中，还剩 {self.breaker.remaining(domain)} 秒",
            ))

        started = self._clock()
        try:
            with self.limiter.slot(domain):
                status, body = self._transport(url, headers, timeout)
            latency = int((self._clock() - started) * 1000)
            text = body.decode(encoding, "replace") if isinstance(body, bytes) else str(body)
            ok = 200 <= status < 400
            self.breaker.record(domain, ok)
            return self._record(SourceResult(
                ok=ok, source=source, url=url, status=status, latency_ms=latency,
                raw=text, capability=capability, code=code,
                error=None if ok else f"HTTP {status}: {text[:200]}",
            ))
        except Exception as exc:  # noqa: BLE001
            latency = int((self._clock() - started) * 1000)
            self.breaker.record(domain, False)
            return self._record(SourceResult(
                ok=False, source=source, url=url, latency_ms=latency,
                capability=capability, code=code,
                error=f"{type(exc).__name__}: {exc}",
            ))

    def wrap(self, source: str, domain: str, fn: Callable[..., Any],
             *args: Any, capability: str = "", code: str | None = None,
             **kwargs: Any) -> SourceResult:
        """给不方便改的第三方取数函数套上同样的限流/熔断/留痕。

        算法层（algo/）里的 fetcher 内部自己发请求，不能改；
        在编排层统一套一层，至少保证串行、有间隔、失败可查。
        """
        if self.breaker.is_open(domain):
            return self._record(SourceResult(
                ok=False, source=source, capability=capability, code=code,
                skipped=f"{domain} 熔断中，还剩 {self.breaker.remaining(domain)} 秒",
            ))
        started = self._clock()
        try:
            with self.limiter.slot(domain):
                data = fn(*args, **kwargs)
            latency = int((self._clock() - started) * 1000)
            ok = data is not None
            self.breaker.record(domain, ok)
            return self._record(SourceResult(
                ok=ok, source=source, data=data, latency_ms=latency,
                capability=capability, code=code,
                error=None if ok else "返回空",
            ))
        except Exception as exc:  # noqa: BLE001
            latency = int((self._clock() - started) * 1000)
            self.breaker.record(domain, False)
            return self._record(SourceResult(
                ok=False, source=source, latency_ms=latency,
                capability=capability, code=code,
                error=f"{type(exc).__name__}: {exc}",
            ))


def db_recorder(conn, run_id: str | None = None) -> Callable[[SourceResult], None]:
    """把每次调用写进 source_calls（只追加表）。

    表里没有 run_id / raw 列，一次调用只留能回答问题的那些：
    哪个源、取什么能力、哪只票、成功与否、耗时、错误原文。
    熔断跳过也记一条，错误列写"熔断中"，否则事后会以为压根没调过。
    """
    def record(res: SourceResult) -> None:
        conn.execute(
            """INSERT INTO source_calls(
                   source, capability, code, url, ok, http_code, latency_ms, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (res.source, res.capability or "unknown", res.code, res.url,
             1 if res.ok else 0, res.status, res.latency_ms,
             res.error or (f"跳过：{res.skipped}" if res.skipped else None)),
        )
    return record
