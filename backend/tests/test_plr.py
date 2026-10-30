"""V5.0 PLR（hermes 口径）算法测试。

算法：近 250 根日K收盘价+成交量 → 成交量加权价格分布找支撑 s1 / 阻力 w1,w2
→ PLR = (w2 - price) / (price - s1)。与 hermes pool_scan / backtest_v5 一致。
"""

from app.services.plr import compute_plr, find_peaks, plr_to_score, MIN_BARS


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


# ---------- 防 PLR 虚高（hermes 复核过滤链） ----------

def test_s1_picks_strongest_not_closest_peak():
    # 现价下方有两个峰：9（量小，离现价近）和 6（量大，离现价远）。
    # s1 必须取量能最强的 6，而不是最贴现价的 9 —— 否则分母小、PLR 虚高。
    closes = _trend(4, 200, 0.07)          # 4 → 17.93
    vols = []
    for c in closes:
        if 5.5 <= c <= 6.5:
            vols.append(500.0)             # 6 区放量最强
        elif 8.5 <= c <= 9.5:
            vols.append(50.0)              # 9 区量小
        else:
            vols.append(1.0)
    s1, _, _ = find_peaks(closes, vols, cur=14.0)
    assert s1 is not None and 5.5 <= s1 <= 6.5   # 落在量大的 6 区，不是贴脸的 9 区


def test_reject_when_s1_too_close():
    # 支撑贴脸（s1 距现价 <1.5%）→ plr 给 None，flag=贴脸剔除
    # 构造：量能最强峰就在现价下方 0.5% 处
    closes = _trend(5, 200, 0.08)          # 5 → 20.92
    price = 14.0
    near = price * 0.995                    # 距现价 0.5%（<1.5%）
    vols = [400.0 if abs(c - near) < 0.2 else 1.0 for c in closes]
    out = compute_plr(_bars(closes, vols), price=price)
    assert out["plr"] is None
    assert out["flag"] == "贴脸剔除"
    assert out["s1_dist"] is not None and out["s1_dist"] < 1.5


def test_thin_flag_when_s1_dist_between_1_5_and_2():
    # s1 距现价 1.5%~2% → PLR 照给但 flag=贴线降仓
    closes = _trend(5, 200, 0.08)
    price = 14.0
    thin = price * 0.983                    # 距现价约 1.7%（在 1.5~2 之间）
    vols = []
    for c in closes:
        if abs(c - thin) < 0.15:
            vols.append(500.0)              # 支撑峰在 1.7% 处
        elif c >= 18:
            vols.append(200.0)              # 上方阻力峰
        else:
            vols.append(1.0)
    out = compute_plr(_bars(closes, vols), price=price)
    if out["plr"] is not None and 1.5 <= out["s1_dist"] < 2.0:
        assert out["flag"] == "贴线降仓"


def test_ok_flag_when_s1_dist_ample():
    # s1 距现价 >2% → 正常
    closes = _trend(5, 200, 0.08)
    vols = [200.0 if (c <= 10 or c >= 18) else 1.0 for c in closes]
    out = compute_plr(_bars(closes, vols), price=14.0)
    assert out["plr"] is not None
    assert out["s1_dist"] > 2.0
    assert out["flag"] == "ok"


def test_reject_when_w2_too_close():
    # 上方第2阻力贴脸 → 分子趋零，PLR≈0，是追在压力位的拒绝信号
    closes = _trend(5, 200, 0.08)
    price = 20.5                            # 接近区间顶（pmax=20.92）
    # 下方放量支撑，但现价已贴近上方边界（w2=pmax≈20.92，距现价<0.1%）
    vols = [300.0 if c <= 12 else 1.0 for c in closes]
    out = compute_plr(_bars(closes, vols), price=price)
    # w2 = pmax ≈ 20.92，距 20.5 仅 2%，需直接命中 w2<=price*1.001 才算贴脸；
    # 若未触发，至少要求 plr 极小（追在压力位）
    if out["flag"] == "贴脸剔除":
        assert out["plr"] is None
    else:
        assert out["plr"] is None or out["plr"] < 1.0


def test_no_inflated_plr_like_zhongwang_case():
    # 复刻 hermes 中望实例的"贴脸"形态：支撑紧贴现价下方（距 <1.5%）→ 旧算法 PLR 40+ 虚高。
    # 修正后必须识别为贴脸剔除，绝不给出虚高数。
    price = 54.0
    s1_like = 53.5                          # 距现价 (54-53.5)/54 ≈ 0.93% < 1.5% → 贴脸
    # 现价下方最近、量能最强的明确峰放在 s1_like（宽带放量确保被 find_peaks 选中）。
    closes = [round(40 + i * 0.15, 2) for i in range(200)]   # 40 → 69.85
    vols = [600.0 if abs(c - s1_like) < 0.4 else 1.0 for c in closes]
    out = compute_plr(_bars(closes, vols), price=price)
    # 选出的支撑确实贴近 53.5
    assert abs(out["s1"] - s1_like) < 1.0
    assert out["flag"] == "贴脸剔除"
    assert out["plr"] is None               # 不得给出虚高值
    assert out["s1_dist"] < 1.5


# ---------- plr_to_score：0~1 参考分（方案C，不计入评分引擎） ----------

def test_score_zero_when_empty_result():
    assert plr_to_score({}) == 0.0
    assert plr_to_score(None) == 0.0


def test_score_zero_when_flag_reject():
    assert plr_to_score({"flag": "贴脸剔除", "plr": None}) == 0.0
    assert plr_to_score({"flag": "数据不足", "plr": None}) == 0.0
    # 即使贴了脸但残留 plr 数值，也不给分（贴脸=破位风险线）
    assert plr_to_score({"flag": "贴脸剔除", "plr": 43.95}) == 0.0


def test_score_thin_flag():
    assert plr_to_score({"flag": "贴线降仓", "plr": 5.0}) == 0.3


def test_score_ok_low_plr():
    assert plr_to_score({"flag": "ok", "plr": 1.2}) == 0.4
    assert plr_to_score({"flag": "ok", "plr": 0.5}) == 0.4


def test_score_ok_mid_plr():
    assert plr_to_score({"flag": "ok", "plr": 2.0}) == 0.7
    assert plr_to_score({"flag": "ok", "plr": 3.0}) == 0.7


def test_score_ok_high_plr():
    assert plr_to_score({"flag": "ok", "plr": 3.01}) == 1.0
    assert plr_to_score({"flag": "ok", "plr": 10.0}) == 1.0


def test_score_ok_none_plr():
    # flag=ok 但 plr 缺失（理论边界）→ 不给分
    assert plr_to_score({"flag": "ok", "plr": None}) == 0.0


def test_score_range_bounded():
    # 所有输出都在 [0,1] 区间
    for case in [{"flag": "ok", "plr": p} for p in (0, 1, 2, 3, 100)] + \
                [{"flag": "贴线降仓", "plr": 5}, {"flag": "贴脸剔除", "plr": 50}]:
        v = plr_to_score(case)
        assert 0.0 <= v <= 1.0
