"""T4.1 / T4.2：数据源适配器 + 限流熔断。

全部离线跑：transport 换成假函数，clock/sleep 换成假的，
这样"限流间隔"和"熔断 10 分钟"能被确定性地验证，而不是靠 sleep 猜。
"""

from __future__ import annotations

import threading
import time

import pytest

from app.services.sources import (
    FAIL_THRESHOLD,
    OPEN_SECONDS,
    CircuitBreaker,
    HttpClient,
    LiveSources,
    RateLimiter,
    SourceResult,
    change_pct,
    db_recorder,
    parse_kline,
    parse_quote,
    tuna_info_from_popularity,
)

def _quote_frame(**over) -> str:
    """按腾讯行情的真实字段顺序拼一条返回。

    协议没有版本号，只能按索引取。索引 9~28 是买卖五档，
    所以 30 才是时间、38 换手率、44 流通市值、45 总市值。
    手写整行容易数错，这里用列表拼，索引一目了然。
    """
    f = [""] * 49
    f[0] = "1"
    f[1] = over.get("name", "贵州茅台")
    f[2] = over.get("code", "600519")
    f[3] = str(over.get("price", 1700.00))
    f[4] = str(over.get("prev_close", 1680.00))
    f[5] = str(over.get("open", 1690.00))
    f[6] = "23456"
    f[30] = "20261017150000"
    f[31] = str(round(float(over.get("price", 1700.0)) - float(over.get("prev_close", 1680.0)), 2))
    f[32] = str(over.get("pct", 1.19))
    f[33] = "1712.00"
    f[34] = "1675.00"
    f[38] = str(over.get("turnover_pct", 0.19))
    f[39] = "28.50"
    f[44] = str(over.get("float_mv_yi", 21350.00))
    f[45] = str(over.get("total_mv_yi", 21350.00))
    f[46] = "7.80"
    return 'v_sh600519="' + "~".join(f) + '";'


QUOTE_TEXT = _quote_frame()

KLINE_TEXT = (
    '{"code":0,"msg":"","data":{"sh600519":{"qfqday":['
    '["2026-10-13","1650.00","1660.00","1670.00","1640.00","10000"],'
    '["2026-10-14","1660.00","1670.00","1680.00","1655.00","11000"],'
    '["2026-10-15","1670.00","1690.00","1700.00","1668.00","12000"],'
    '["2026-10-16","1690.00","1695.00","1705.00","1690.00","9000"],'
    '["2026-10-17","1695.00","1700.00","1712.00","1675.00","23456"]'
    ']}}}'
)


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def test_parse_quote_extracts_core_fields():
    q = parse_quote(QUOTE_TEXT, "600519")
    assert q is not None
    assert q["symbol"] == "sh600519"
    assert q["name"] == "贵州茅台"
    assert q["price"] == pytest.approx(1700.0)
    assert q["prev_close"] == pytest.approx(1680.0)
    assert q["pct"] == pytest.approx(1.19)
    assert q["total_mv_yi"] == pytest.approx(21350.0)
    assert q["turnover_pct"] == pytest.approx(0.19)


def test_parse_quote_rejects_garbage():
    assert parse_quote("", "600519") is None
    assert parse_quote("v_sh600519=\"\";", "600519") is None
    assert parse_quote("not a quote at all", "600519") is None


def test_parse_quote_tolerates_missing_tail_fields():
    # 只有前 6 个字段，市值等缺失 → 不该编数，也不该整条废掉
    q = parse_quote('v_sz000001="1~平安银行~000001~11.20~11.00~11.05";', "000001")
    assert q is not None
    assert q["price"] == pytest.approx(11.20)
    assert "total_mv_yi" not in q

def test_parse_kline_sorts_and_computes_5d_change():
    rows = parse_kline(KLINE_TEXT, "600519")
    assert len(rows) == 5
    assert rows[0]["date"] == "2026-10-13"
    assert rows[-1]["date"] == "2026-10-17"
    # 首日收盘 1660 → 末日 1700
    assert change_pct(rows, 5) == pytest.approx(round((1700 - 1660) / 1660 * 100, 2))


def test_change_pct_needs_at_least_two_rows():
    assert change_pct([]) is None
    assert change_pct([{"date": "2026-10-17", "close": 10.0}]) is None


def test_parse_kline_bad_json_returns_empty():
    assert parse_kline("<html>502 Bad Gateway</html>", "600519") == []


# ----------------------------------------------------------------------
# HttpClient
# ----------------------------------------------------------------------
def test_http_client_success_records_latency_and_raw():
    seen = []

    def transport(url, headers, timeout):
        return 200, QUOTE_TEXT.encode("gbk")

    client = HttpClient(transport=transport, recorder=seen.append)
    res = client.get("https://qt.gtimg.cn/q=sh600519", source="腾讯行情", encoding="gbk")
    assert res.ok is True
    assert res.status == 200
    assert res.latency_ms >= 0
    assert "贵州茅台" in (res.raw or "")
    assert len(seen) == 1 and seen[0].ok is True


def test_http_client_wraps_transport_exception_with_original_text():
    def boom(url, headers, timeout):
        raise ConnectionError("ConnectionRefusedError: [WinError 10061] 目标计算机积极拒绝")

    client = HttpClient(transport=boom)
    res = client.get("https://qt.gtimg.cn/q=sh600519", source="行情")
    assert res.ok is False
    assert "ConnectionError" in res.error
    assert "10061" in res.error  # 原文必须带回来，不许只写"失败"


def test_http_client_marks_http_error_status():
    def transport(url, headers, timeout):
        return 502, b"<html>bad gateway</html>"

    client = HttpClient(transport=transport)
    res = client.get("https://x.example.com/a", source="行情")
    assert res.ok is False
    assert res.status == 502
    assert "502" in res.error


# ----------------------------------------------------------------------
# 熔断
# ----------------------------------------------------------------------
def test_breaker_opens_after_five_consecutive_failures():
    now = [0.0]

    def boom(url, headers, timeout):
        raise ConnectionError("refused")

    client = HttpClient(
        transport=boom,
        breaker=CircuitBreaker(threshold=FAIL_THRESHOLD, open_seconds=OPEN_SECONDS,
                               clock=lambda: now[0]),
        clock=lambda: now[0],
    )
    url = "https://qt.gtimg.cn/q=sh600519"
    for _ in range(FAIL_THRESHOLD):
        assert client.get(url, source="行情").ok is False

    skipped = client.get(url, source="行情")
    assert skipped.ok is False
    assert skipped.skipped and "熔断" in skipped.skipped
    # 熔断期间不再真的发请求（transport 没被调用），所以没有 latency
    assert skipped.latency_ms == 0


def test_breaker_half_opens_after_ten_minutes():
    now = [0.0]
    calls = []

    def boom(url, headers, timeout):
        calls.append(1)
        raise ConnectionError("refused")

    breaker = CircuitBreaker(threshold=2, open_seconds=OPEN_SECONDS, clock=lambda: now[0])
    client = HttpClient(transport=boom, breaker=breaker, clock=lambda: now[0])
    url = "https://qt.gtimg.cn/q=sh600519"
    client.get(url, source="行情")
    client.get(url, source="行情")
    assert client.get(url, source="行情").skipped
    assert len(calls) == 2

    now[0] = OPEN_SECONDS - 1
    assert client.get(url, source="行情").skipped
    assert len(calls) == 2

    now[0] = OPEN_SECONDS + 1  # 到点，半开试一次
    res = client.get(url, source="行情")
    assert res.skipped is None
    assert len(calls) == 3


def test_breaker_resets_on_success():
    now = [0.0]
    state = {"fail": True}

    def transport(url, headers, timeout):
        if state["fail"]:
            raise ConnectionError("refused")
        return 200, b"ok"

    client = HttpClient(
        transport=transport,
        breaker=CircuitBreaker(threshold=3, open_seconds=600, clock=lambda: now[0]),
        clock=lambda: now[0],
    )
    url = "https://qt.gtimg.cn/q=sh600519"
    client.get(url, source="行情")
    client.get(url, source="行情")
    state["fail"] = False
    assert client.get(url, source="行情").ok is True
    state["fail"] = True
    # 成功一次就把连败清零，所以要重新攒 3 次才熔断
    assert client.get(url, source="行情").ok is False   # 1
    assert client.get(url, source="行情").ok is False   # 2
    assert client.get(url, source="行情").ok is False   # 3 → 熔断开启
    assert client.get(url, source="行情").skipped


def test_one_source_failing_does_not_affect_another_domain():
    def transport(url, headers, timeout):
        if "eastmoney" in url:
            raise ConnectionError("eastmoney down")
        return 200, b"fine"

    client = HttpClient(transport=transport)
    bad = "https://datacenter-web.eastmoney.com/api"
    good = "https://qt.gtimg.cn/q=sh600519"
    for _ in range(FAIL_THRESHOLD):
        client.get(bad, source="财务")
    assert client.get(bad, source="财务").skipped
    assert client.get(good, source="行情").ok is True


# ----------------------------------------------------------------------
# 限流
# ----------------------------------------------------------------------
def test_rate_limiter_enforces_interval_between_same_domain_calls():
    clock = [0.0]
    slept = []

    def sleep(sec):
        slept.append(sec)
        clock[0] += sec

    limiter = RateLimiter(min_interval=0.2, max_interval=0.2,
                          clock=lambda: clock[0], sleep=sleep)
    for _ in range(3):
        with limiter.slot("qt.gtimg.cn"):
            clock[0] += 0.01
    # 第二次、第三次各等一次，每次都在同一域名上补足 0.2 的间隔
    assert len(slept) == 2
    assert all(s > 0.1 for s in slept)
    assert sum(slept) >= 0.25


def test_rate_limiter_caps_concurrency_per_domain():
    in_flight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def transport(url, headers, timeout):
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.05)
        with lock:
            in_flight["now"] -= 1
        return 200, b"ok"

    client = HttpClient(
        transport=transport,
        limiter=RateLimiter(concurrency=4, min_interval=0, max_interval=0),
    )
    threads = [
        threading.Thread(target=client.get, args=("https://qt.gtimg.cn/q=sh600519", "行情"))
        for _ in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert in_flight["max"] <= 4


# ----------------------------------------------------------------------
# wrap：给算法层 fetcher 套壳
# ----------------------------------------------------------------------
def test_wrap_returns_original_error_text():
    def fetcher(code):
        raise RuntimeError("eastmoney 返回 302 跳登录页")

    client = HttpClient(transport=lambda *a: (200, b""))
    res = client.wrap("财务", "finance.eastmoney", fetcher, "600519")
    assert res.ok is False
    assert "302 跳登录页" in res.error


def test_wrap_treats_none_as_failure():
    client = HttpClient(transport=lambda *a: (200, b""))
    res = client.wrap("财务", "finance.eastmoney", lambda code: None, "600519")
    assert res.ok is False
    assert res.error == "返回空"


def test_wrap_success_passes_data_through():
    client = HttpClient(transport=lambda *a: (200, b""))
    res = client.wrap("财务", "finance.eastmoney", lambda code: {"roe": 12.3}, "600519")
    assert res.ok is True
    assert res.data == {"roe": 12.3}


# ----------------------------------------------------------------------
# 留痕落库
# ----------------------------------------------------------------------
def test_source_calls_are_persisted(conn):
    client = HttpClient(transport=lambda *a: (_ for _ in ()).throw(ConnectionError("refused")),
                        recorder=db_recorder(conn))
    client.get("https://qt.gtimg.cn/q=sh600519", source="腾讯行情",
               capability="quote", code="600519")

    row = conn.execute("SELECT * FROM source_calls").fetchone()
    assert row["source"] == "腾讯行情"
    assert row["capability"] == "quote"
    assert row["code"] == "600519"
    assert row["ok"] == 0
    assert "refused" in row["error"]


def test_source_calls_records_breaker_skip(conn):
    client = HttpClient(
        transport=lambda *a: (_ for _ in ()).throw(ConnectionError("refused")),
        breaker=CircuitBreaker(threshold=1, open_seconds=600),
        recorder=db_recorder(conn),
    )
    url = "https://qt.gtimg.cn/q=sh600519"
    client.get(url, source="腾讯行情", capability="quote", code="600519")
    client.get(url, source="腾讯行情", capability="quote", code="600519")

    rows = conn.execute("SELECT ok, error FROM source_calls ORDER BY id").fetchall()
    assert len(rows) == 2  # 熔断跳过也要留痕，否则事后以为没调过
    assert rows[1]["ok"] == 0
    assert "熔断" in rows[1]["error"]


def test_source_calls_is_append_only(conn):
    import sqlite3

    conn.execute(
        "INSERT INTO source_calls(source, capability, ok) VALUES('行情','quote',1)")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE source_calls SET ok=0 WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM source_calls WHERE id=1")


# ----------------------------------------------------------------------
# LiveSources 组合层
# ----------------------------------------------------------------------
def test_live_sources_quote_uses_fake_transport():
    client = HttpClient(transport=lambda url, h, t: (200, QUOTE_TEXT.encode("gbk")))
    src = LiveSources(client)
    res = src.quote("600519")
    assert res.ok is True
    assert res.data["total_mv_yi"] == pytest.approx(21350.0)


def test_tuna_info_extracts_pool_fields():
    payload = {
        "tuna_flag": True, "tuna_reason": "hot", "tuna_reasons": ["研报25篇>20"],
        "tuna_severity": 1, "details": {"reports_12m": 25, "active_ratio_pct": 9.1,
                                        "total_mv_yi": 180.0},
        "score": 0.4,
    }
    info = tuna_info_from_popularity(payload)
    assert info["tuna_flag"] is True
    assert info["details"]["reports_12m"] == 25
    assert info["popularity_score"] == 0.4
    assert tuna_info_from_popularity(None) is None
