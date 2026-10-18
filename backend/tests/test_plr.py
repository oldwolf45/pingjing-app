"""V5.0 PLR（hermes 口径）算法测试。

算法：近 250 根日K收盘价+成交量 → 成交量加权价格分布找支撑 s1 / 阻力 w1,w2
→ PLR = (w2 - price) / (price - s1)。与 hermes pool_scan / backtest_v5 一致。
"""
import math

from app.services.plr import compute_plr, find_peaks, MIN_BARS


def _bars(closes, vols=None):
    """构造日K列表，close 必填，volume 缺省 1。"""
    vols = vols or [1.0] * len(closes)
    return [{"date": f"2026-01-{i+1:02d}", "close": c, "volume": v}
            for i, (c, v) in enumerate(zip(closes, vols))]


def _trend(start, n, step):
    return [round(start + i * step, 2) for i in range(n)]


# ---------- find_peaks ----------

def test_find_peaks_too_few_bars():
    s1, w1, w2 = find_peaks([1, 2, 3], [1, 1, 1], 2)
    assert (s1, w1, w2) == (None, None, None)


def test_find_peaks_flat_price_no_range():
    closes = [10.0] * 100
    s1, w1, w2 = find_peaks(closes, [1] * 100, 10.0)
    assert (s1, w1, w2) == (None, None, None)


def test_find_peaks_support_below_current():
    # 成交量集中在 8-10 区（支撑），当前价在 20，分布峰值应在下方
    closes = _trend(5, 100, 0.15)          # 5 → 19.85
    vols = [100.0 if c <= 10 else 1.0 for c in closes]   # 量在低位
    s1, w1, w2 = find_peaks(closes, vols, cur=20.0)
    assert s1 is not None and s1 <= 10.5   # 支撑落在放量低位区
    # 当前价上方无峰 → w1/w2 取区间顶 pmax
    assert w1 == max(closes) and w2 == max(closes)


def test_find_peaks_resistance_above_current():
    # 量在 25-28 区（阻力），当前价 12
    closes = _trend(10, 100, 0.18)         # 10 → 27.82
    vols = [100.0 if c >= 25 else 1.0 for c in closes]
    s1, w1, w2 = find_peaks(closes, vols, cur=12.0)
    assert s1 == min(closes)               # 下方无峰 → s1=区间底
    assert w1 is not None and w1 >= 24     # 阻力落在放量高位区


# ---------- compute_plr ----------

def test_plr_none_when_no_data():
    out = compute_plr([], price=10)
    assert out["plr"] is None and out["bars"] == 0


def test_plr_none_when_price_invalid():
    out = compute_plr(_bars(_trend(5, 100, 0.1)), price=0)
    assert out["plr"] is None


def test_plr_none_when_bars_below_min():
    out = compute_plr(_bars(_trend(5, MIN_BARS - 5, 0.1)), price=10)
    assert out["plr"] is None
    assert out["bars"] == MIN_BARS - 5


def test_plr_none_when_price_too_close_to_support():
    # 当前价贴近支撑（<0.1%），分母非法 → None
    closes = _trend(5, 100, 0.15)
    vols = [100.0 if c <= 8 else 1.0 for c in closes]
    s1, _, _ = find_peaks(closes, vols, cur=20)
    out = compute_plr(_bars(closes, vols), price=s1 * 1.0005)  # 贴脸
    assert out["plr"] is None


def test_plr_positive_when_room_above_and_below():
    # 构造：下方 8-10 有支撑峰（放量），上方 18-20 有阻力峰（放量），当前价 14
    closes = _trend(5, 200, 0.08)          # 5 → 20.92
    vols = []
    for c in closes:
        if c <= 10 or c >= 18:
            vols.append(200.0)             # 支撑与阻力都放量
        else:
            vols.append(1.0)
    out = compute_plr(_bars(closes, vols), price=14.0)
    assert out["plr"] is not None
    assert out["s1"] is not None and out["s1"] <= 10.5
    assert out["w2"] is not None and out["w2"] >= 17
    # 手算复核：plr = (w2-14)/(14-s1)，应为正
    expect = (out["w2"] - 14.0) / (14.0 - out["s1"])
    assert out["plr"] == round(expect, 2)
    assert out["plr"] > 0


def test_plr_uses_only_last_window_bars():
    # 250 根窗口：前面塞 300 根垃圾低量，真正起作用的是尾部
    closes = [1.0] * 300 + _trend(10, 250, 0.05)
    vols = [0.001] * 300 + [50.0 if c <= 12 else 1.0 for c in closes[300:]]
    out = compute_plr(_bars(closes, vols), price=20.0)
    assert out["bars"] == 250              # 只取窗口内


def test_plr_rounds_two_decimals():
    closes = _trend(5, 200, 0.08)
    vols = [200.0 if (c <= 10 or c >= 18) else 1.0 for c in closes]
    out = compute_plr(_bars(closes, vols), price=14.0)
    if out["plr"] is not None:
        assert abs(out["plr"] - round(out["plr"], 2)) < 1e-9
