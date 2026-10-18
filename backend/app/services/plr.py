"""V5.0 盈亏比（PLR）技术信号参考项。

算法口径与 hermes 池 backtest_v5 / pool_scan 完全一致：
  - 取近 250 根日K（收盘价 + 成交量），做成交量加权价格分布（60 桶 + [1,2,4,2,1] 平滑）。
  - 分布峰值里，当前价之下最近的峰 = 支撑 s1，之上的第 1、2 个峰 = 阻力 w1、w2。
  - PLR = (w2 - price) / (price - s1)：以当前价为准，"上行到下一阻力空间" / "下跌到支撑空间"。
  - 数据不足、价格贴脸（price<=s1*1.001 或 w2<=price*1.001）时返回 None，宁缺毋假。

注意：这是纯技术信号参考，与紫苏叶基本面的"买点/止损/目标价"文本点位是两套体系，
仅作为个股详情页的一个参考项展示，不参与评分与池子判定。
"""

from __future__ import annotations

from typing import Any

WINDOW = 250          # 日K窗口
N_BINS = 60           # 价格分布桶数
MIN_BARS = 30         # 最少根数，不足返回 None
_KERNEL = (1, 2, 4, 2, 1)


def find_peaks(closes: list[float], vols: list[float], cur: float,
               n_bins: int = N_BINS) -> tuple[float | None, float | None, float | None]:
    """成交量加权价格分布找支撑/阻力峰。返回 (s1, w1, w2)，找不到给 (None, None, None)。"""
    n = len(closes)
    if n < MIN_BARS:
        return None, None, None
    pmin, pmax = float(min(closes)), float(max(closes))
    if pmax <= pmin * 1.001:
        return None, None, None

    bin_w = (pmax - pmin) / n_bins
    heights = [0.0] * n_bins
    for i in range(n):
        c = closes[i]
        v = vols[i] if i < len(vols) and vols[i] else 0.0
        idx = min(int((c - pmin) / bin_w), n_bins - 1)
        heights[idx] += v

    # [1,2,4,2,1] 卷积平滑（mode=same，边缘补零）
    ks = sum(_KERNEL)
    sm = [0.0] * n_bins
    half = len(_KERNEL) // 2
    for i in range(n_bins):
        acc = 0.0
        for k in range(len(_KERNEL)):
            j = i + k - half
            if 0 <= j < n_bins:
                acc += heights[j] * _KERNEL[k]
        sm[i] = acc / ks

    mean_sm = sum(sm) / n_bins
    peaks: list[float] = []
    for i in range(1, n_bins - 1):
        if sm[i] > sm[i - 1] and sm[i] > sm[i + 1] and sm[i] > mean_sm * 0.3:
            peaks.append(pmin + (i + 0.5) * bin_w)
    if not peaks:
        return None, None, None

    below = [p for p in peaks if p < cur]
    above = sorted(p for p in peaks if p > cur)
    s1 = max(below) if below else pmin
    w1 = above[0] if above else pmax
    w2 = above[1] if len(above) >= 2 else pmax
    return s1, w1, w2


def compute_plr(klines: list[dict[str, Any]], price: float,
                window: int = WINDOW) -> dict[str, Any]:
    """从日K算 PLR。

    klines: [{date, close, volume, ...}] 升序；price: 当前价。
    返回 {plr, s1, w1, w2, bars}；无法计算时 plr 等数值为 None，bars 记实际根数。
    """
    out: dict[str, Any] = {"plr": None, "s1": None, "w1": None, "w2": None,
                           "bars": 0, "window": window}
    if not klines or price is None or price <= 0:
        return out

    tail = klines[-window:]
    closes = [float(r["close"]) for r in tail if r.get("close") is not None]
    vols = [float(r["volume"] or 0.0) for r in tail if r.get("close") is not None]
    out["bars"] = len(closes)
    if len(closes) < MIN_BARS:
        return out

    s1, w1, w2 = find_peaks(closes, vols, price)
    out["s1"], out["w1"], out["w2"] = s1, w1, w2
    if s1 is not None and w2 is not None and price > s1 * 1.001 and w2 > price * 1.001:
        out["plr"] = round((w2 - price) / (price - s1), 2)
    return out
