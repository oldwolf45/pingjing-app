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

# hermes 复核过滤链（2026-08-20 实战定稿）防 PLR 虚高：
#   s1 支撑带距现价 < 1.5% → 贴脸剔除（这是破位风险线，不是高赔率）
#   s1 距 1.5% ~ 2%       → 标注"贴线"，应降仓
S1_DIST_REJECT = 1.5    # %，低于此贴脸，不给 PLR
S1_DIST_THIN = 2.0      # %，低于此贴线降仓


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
    peaks: list[tuple[float, float]] = []   # (峰值价, 平滑后高度)
    for i in range(1, n_bins - 1):
        if sm[i] > sm[i - 1] and sm[i] > sm[i + 1] and sm[i] > mean_sm * 0.3:
            peaks.append((pmin + (i + 0.5) * bin_w, sm[i]))
    if not peaks:
        return None, None, None

    # s1 = 现价下方"量能最强"的峰（不是最贴现价的峰）。
    # 取最贴现价的峰会让支撑贴脸、分母趋零、PLR 虚高（hermes 中望实例 PLR 43.95）。
    below = [(p, h) for p, h in peaks if p < cur]
    above = sorted(((p, h) for p, h in peaks if p > cur), key=lambda x: x[0])
    s1 = max(below, key=lambda x: x[1])[0] if below else pmin
    w1 = above[0][0] if above else pmax
    w2 = above[1][0] if len(above) >= 2 else pmax
    return s1, w1, w2


def compute_plr(klines: list[dict[str, Any]], price: float,
                window: int = WINDOW) -> dict[str, Any]:
    """从日K算 PLR。

    klines: [{date, close, volume, ...}] 升序；price: 当前价。
    返回 {plr, s1, w1, w2, s1_dist, flag, bars}；无法计算时 plr 为 None，bars 记实际根数。

    flag 取值：
      "ok"        正常
      "贴线降仓"  s1 距现价 1.5%~2%，PLR 仍给出但应降仓
      "贴脸剔除"  s1 距 <1.5%，破位风险线不是高赔率，plr 给 None
      "数据不足"  日K根数不够或信号算不出
    """
    out: dict[str, Any] = {"plr": None, "s1": None, "w1": None, "w2": None,
                           "s1_dist": None, "flag": "数据不足",
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
    if s1 is None or w2 is None:
        return out

    out["s1_dist"] = round((price - s1) / price * 100, 2)

    # 防虚高第一道：支撑贴脸（s1 距 <1.5%）→ 分母趋零，PLR 必虚高，直接剔除不给数
    if price <= s1 * 1.001 or out["s1_dist"] < S1_DIST_REJECT:
        out["flag"] = "贴脸剔除"
        return out
    # w2 贴脸（上方阻力太近）→ 分子趋零 PLR≈0，是追在压力位的拒绝信号
    if w2 <= price * 1.001:
        out["flag"] = "贴脸剔除"
        return out

    out["plr"] = round((w2 - price) / (price - s1), 2)
    # 防虚高第二道：s1 距 1.5%~2% 标"贴线"，PLR 照给但提示降仓
    out["flag"] = "贴线降仓" if out["s1_dist"] < S1_DIST_THIN else "ok"
    return out


def plr_to_score(result: dict[str, Any]) -> float:
    """把 PLR 结果折算成 0~1 分的"参考分"（方案C：独立展示，不计入评分引擎总分）。

    紫苏叶 6 维基本面评分已是 10 分制且金样本锁定，PLR 作为技术信号参考项
    单独折算成 1 分制展示，不进 scoring_engine、不改池子判定。

    档位（沿用三态语义，技术信号越可靠分越高）：
      贴脸剔除 / 数据不足 / 算不出 → 0.0   破位风险线或无信号，不给分
      贴线降仓                      → 0.3   信号偏险，轻仓
      ok 且 PLR < 2                 → 0.4   赔率不足
      ok 且 2 ≤ PLR ≤ 3             → 0.7   临界可接受
      ok 且 PLR > 3                 → 1.0   达标（hermes 门槛）
    """
    if not result:
        return 0.0
    flag = result.get("flag")
    if flag != "ok" and flag != "贴线降仓":
        return 0.0
    if flag == "贴线降仓":
        return 0.3
    plr = result.get("plr")
    if plr is None:
        return 0.0
    if plr > 3:
        return 1.0
    if plr >= 2:
        return 0.7
    return 0.4
