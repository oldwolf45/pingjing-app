# -*- coding: utf-8 -*-
"""
🍃 紫苏叶策略 V2.0 - 配置文件
所有标的池、赛道数据、评分阈值都在这里配置
"""

# ============================================================
# 🏆 候选池（≥7.5分）- 精选中的精选，可考虑建仓
# 👀 观察池（6.5-7.5分）- 持续跟踪，等待升级机会
# 🔍 储备池（5.5-6.5分）- 新挖掘或待验证标的
# ============================================================
# 字段说明：
#   code: 6位代码
#   name: 股票名称
#   sector: 赛道名称
#   track_barrier: 赛道壁垒分（0-2.5，保留字段：无赛道情报时的兜底/初始分）
#   barrier_manual_override: 赛道壁垒人工覆盖分（可选，设置后优先于自动评分）
#   ideal_buy: 理想买点
#   stop_loss: 止损价
#   target: 目标价
#   position_suggest: 建议仓位（%）—— 必须 ≤ 所属池的单票上限
#                     候选池 ≤ RISK_PARAMS["w_cap_candidate"]（10%）
#                     观察/储备池 ≤ RISK_PARAMS["w_cap_observation"]（8%）
#                     写超了也会被 portfolio_risk.py 自动裁剪并告警
#   core_logic: 核心逻辑简述
#   upgrade_conditions: 升级条件（观察→候选）
#   downgrade_warning: 降级预警线
# ============================================================
# 🛡️ 赛道壁垒自动化说明：
#   - 每周更新时由 barrier_engine 自动算分（8维规则卡+赛道情报+财报直算）
#   - 赛道情报放 赛道数据库/intel/<赛道>.json，用 sector_intel.py 维护
#   - 想手动锁死某只的壁垒分 → 加 "barrier_manual_override": 2.4
# ============================================================

# ============================================================
# ⚖️ 组合风控参数（来自 V5.x 2578天回测验证）
# ============================================================
# ⚠️ 单一真源（single source of truth）
#    单票仓位上限、赛道上限、高相关上限一律从本字典读取，
#    任何脚本不得再硬编码数字（历史踩坑：portfolio_risk.py 曾写死 25%，
#    与这里不一致，导致组合模拟的仓位口径与决策包建议冲突）。
# ============================================================
RISK_PARAMS = {
    # 单票权重上限（V5.3 实测：wcap 8% 回撤改善13pp，Sharpe 只损失4.4%）
    # 候选池标的 10%，观察/储备池升级首仓 8%
    "w_cap_candidate": 10,      # 候选池单票上限 %
    "w_cap_observation": 8,     # 观察池单票上限 %
    "w_cap_reserve": 8,         # 储备池单票上限 %（升级首仓同观察池）
    # 赛道集中度上限
    "sector_cap": 35,           # 单一赛道合计不超过 %
    # 高相关性阈值（>此值视为同一风险敞口）
    "high_corr_threshold": 0.7,
    # 高相关组合计上限
    "corr_pair_cap": 25,        # 高相关对合计不超过 %
    # 组合目标最大回撤（超过则按比例降总仓）
    "target_max_drawdown": 25,  # %
    # 组合总仓位上限（留现金是 wcap 口径的一部分，不是满仓）
    "gross_exposure_max": 60,   # 全部持仓合计不超过 %
    # 入场过滤（V5.x 实测：离开好池子+好位置，因子基本失效）
    "entry_volume_ratio_max": 1.45,   # 5日/20日量比上限（缩量低关注）
    "entry_price_percentile_max": 50, # 750日价格分位上限（位置不高）
    # 持有期（V5.x 实测：45天 Sharpe 0.989，优于30/60/75天）
    "holding_period_days": 45,  # 交易日
    # 止损区间（V5.x S40 太宽，决策包原-14%太窄，取中间值待自测）
    "stop_loss_default": -25,   # 默认止损 %（建议自测区间 -25%~-30%）
}


def pool_w_cap(pool, risk=None):
    """按池子返回单票仓位上限（%）

    池子标签用三池体系命名：candidate / observation / reserve。
    兼容传入 None（视为候选池）。
    """
    rp = risk if risk is not None else RISK_PARAMS
    pool = (pool or "candidate").lower()
    if pool == "candidate":
        return rp.get("w_cap_candidate", 10)
    return rp.get("w_cap_observation", 8)


def clamp_position(pos, pool, risk=None):
    """把建议仓位裁剪到该池子的上限内，返回 (裁剪后仓位, 是否被裁剪)"""
    cap = pool_w_cap(pool, risk)
    try:
        p = float(pos or 0)
    except (TypeError, ValueError):
        p = 0.0
    if p > cap:
        return cap, True
    return p, False


# ============================================================================
# ⚠️ 这四个池名单（2026-10-17 起）由 sync_config_pools.py 从
#    data/tracking_state.json **自动生成**，不要手改 —— 手改会在下次同步时被覆盖。
#
#    为什么改成自动生成：手写版长期与 state 脱节。2026-10-17 实测手写 29 只，
#    其中 20 条池归属与 state 冲突，另有 7 只 state 有、这里没有。拿它判池会全错。
#
#    现在的关系：
#      state  = 唯一真源（池子归属、评分、计数）
#      本文件 = state 的一份可读快照，只在 state 加载失败时当兜底种子
#
#    要补充"核心逻辑/降级预警"这类静态字段，**改 state 再跑同步**，
#    改法：python sync_config_pools.py --apply
# ============================================================================
CANDIDATE_POOL = [  # 🏆 候选池 ≥6.5
    {
        "code": "301095", "name": "广立微",
        "sector": "EDA/良率分析+测试设备", "track_barrier": 2.30, "ideal_buy": "72元",
        "stop_loss": "56元", "target": "120元", "position_suggest": 15,
        "core_logic": "扣非净利+572%，在手订单9.55亿+92.5%，EDA国产替代最硬核细分", "upgrade_conditions": None, "downgrade_warning": "订单增速下滑 + 连续2周主力流出",
    },
    {
        "code": "300480", "name": "光力科技",
        "sector": "科学仪器/半导体划片机", "track_barrier": 1.80, "ideal_buy": "31.7元",
        "stop_loss": "24.1元", "target": "54.7元", "position_suggest": 0,
        "core_logic": "半导体划片设备国产替代龙头，毛利率50%+，营收+57%净利+194%高景气，先测后切双壁垒", "upgrade_conditions": "半导体设备订单持续高增 + 国产替代率突破 + 现金流转正", "downgrade_warning": "半导体周期下行 + 竞争加剧毛利率下滑 + 现金流持续为负",
    },
]

OBSERVATION_POOL = [  # 👀 观察池 5.5-6.5
    {
        "code": "603032", "name": "德新科技",
        "sector": "新能源/电池精密模具", "track_barrier": 1.60, "ideal_buy": "16.9元",
        "stop_loss": "12.8元", "target": "29.2元", "position_suggest": 0,
        "core_logic": "锂电池裁切模具龙头（致宏精密），刀片电池核心模具国产替代，42亿小市值，净利+139%/扣非+188%高增", "upgrade_conditions": "新能车需求回升 + 海外客户突破 + 毛利维持40%+", "downgrade_warning": "锂电投资周期下行 + 客户集中度高 + 价格战",
    },
    {
        "code": "002111", "name": "威海广泰",
        "sector": "空港设备+军工无人机/消防装备", "track_barrier": 1.60, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "空港地面设备龙头+军工无人机+消防装备三主业，净现比1.56现金流极佳，50亿市值0研报冷门低估，受益于民航复苏+军工无人机放量", "upgrade_conditions": "无人机订单放量 + 空港设备复苏 + 净利增速回20%+", "downgrade_warning": "民航复苏不及预期 + 军工资质风险 + 毛利率持续下滑",
    },
    {
        "code": "301210", "name": "金杨精密",
        "sector": "电池精密模具", "track_barrier": 2.02, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "营收+59.6%/净利+177.7%/扣非+183.3%，47亿小市值、近12月研报1篇（够冷门）；但毛利率仅14.0%、OCF/NI -0.09，是行业爆发带动，不是定价权。电池精密结构件67%，圆柱/方形电池壳", "upgrade_conditions": None, "downgrade_warning": "毛利率跌破12% + 电池厂资本开支下行 + 主力连续2周净流出",
    },
    {
        "code": "300666", "name": "江丰电子",
        "sector": "溅射靶材/半导体材料", "track_barrier": 2.00, "ideal_buy": "222元",
        "stop_loss": "183元", "target": "340元", "position_suggest": 12,
        "core_logic": "2026H1营收+31%/净利+132%，靶材国产替代龙头，先进封装+存储复苏双驱动", "upgrade_conditions": None, "downgrade_warning": "靶材降价 + 半导体设备投资放缓",
    },
    {
        "code": "603638", "name": "艾迪精密",
        "sector": "高端液压件/机器人丝杠", "track_barrier": 2.00, "ideal_buy": "18元",
        "stop_loss": "14元", "target": "31元", "position_suggest": 15,
        "core_logic": "液压件+60%/海外+59%，公司回购托底（上限28元），行业β非个股雷，PEG 0.69", "upgrade_conditions": None, "downgrade_warning": "业绩不及预期 + 跌破16元",
    },
    {
        "code": "300885", "name": "海昌新材",
        "sector": "机器人核心/粉末冶金精密件", "track_barrier": 1.50, "ideal_buy": "17元",
        "stop_loss": "13.2元", "target": "28.4元", "position_suggest": 0,
        "core_logic": "高端粉末冶金精密零件龙头，营收+99%/毛利37%，切入机器人减速器齿轮+新能源车+卫星通信，47亿小市值真冷门", "upgrade_conditions": "机器人减速器批量出货 + 毛利维持35%+ + 净利增速维持50%+", "downgrade_warning": "营收增速不可持续 + 毛利率下滑 + 并购整合不及预期",
    },
    {
        "code": "002338", "name": "奥普光电",
        "sector": "科学仪器/光栅编码器", "track_barrier": 2.20, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "300797", "name": "钢研纳克",
        "sector": "科学仪器/检测分析", "track_barrier": 2.30, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "002985", "name": "北摩高科",
        "sector": "军工/航空刹车+起落架", "track_barrier": 2.10, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "军机刹车盘+起落架国产绝对龙头，毛利率54%/研发10%，军工电子+民航维修双赛道，91亿市值0研报真冷门，公司回购托底", "upgrade_conditions": "军品订单回暖 + 净利增速回20%+ + 现金流转正", "downgrade_warning": "军工订单不及预期 + 信用减值持续 + 现金流恶化",
    },
    {
        "code": "603025", "name": "大豪科技",
        "sector": "工业自动化/缝制机械电控", "track_barrier": 1.56, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "002222", "name": "福晶科技",
        "sector": "光学晶体/光刻机上游", "track_barrier": 2.30, "ideal_buy": "64元",
        "stop_loss": "51元", "target": "102元", "position_suggest": 0,
        "core_logic": "LBO晶体全球垄断，毛利率72%，光刻机上游核心", "upgrade_conditions": "光刻胶/光刻机板块启动 + 资金回流 + 业绩超预期", "downgrade_warning": "毛利率跌破60% + 需求下滑",
        "tuna_flag": True, "tuna_note": "市值320亿过金枪鱼线。0研报属光刻机题材披露限制，非认知差；机构37家",
    },
]

RESERVE_POOL = [  # 🔍 储备池 4.5-5.5
    {
        "code": "603197", "name": "保隆科技",
        "sector": "汽车电子/智能传感器", "track_barrier": 1.50, "ideal_buy": "28.3元",
        "stop_loss": "21.1元", "target": "49.9元", "position_suggest": 0,
        "core_logic": "汽车TPMS/毫米波雷达/空气悬架龙头，位置10%分位极低，净利+96%/ROE 16%高质量增长，汽车传感器国产替代", "upgrade_conditions": "汽车电子订单加速 + 空气悬架放量 + 净利增速维持50%+", "downgrade_warning": "汽车价格战加剧 + 新业务拓展不及预期",
    },
    {
        "code": "300034", "name": "钢研高纳",
        "sector": "高温合金/航发材料", "track_barrier": 2.20, "ideal_buy": "14.4元",
        "stop_loss": "11.3元", "target": "23.5元", "position_suggest": 0,
        "core_logic": "航发高温合金龙头，赛道壁垒高，国企背景", "upgrade_conditions": "三季报利润增速回升 + 毛利率改善到25%+", "downgrade_warning": "毛利率持续低于20% + 增收不增利",
    },
    {
        "code": "603915", "name": "国茂股份",
        "sector": "高端液压件/工业减速机", "track_barrier": 1.40, "ideal_buy": "12.5元",
        "stop_loss": "10元", "target": "20元", "position_suggest": 0,
        "core_logic": "通用减速机龙头，RV减速器+谐波减速器切入机器人赛道，位置低估，现金流健康", "upgrade_conditions": "机器人减速器量产落地 + 净利增速维持25%+ + 订单加速", "downgrade_warning": "工业需求下行 + 机器人拓展不及预期",
    },
    {
        "code": "300400", "name": "劲拓股份",
        "sector": "半导体封测装备", "track_barrier": 2.13, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "603728", "name": "鸣志电器",
        "sector": "空心杯电机/机器人", "track_barrier": 1.80, "ideal_buy": "46元",
        "stop_loss": "35.8元", "target": "76.5元", "position_suggest": 0,
        "core_logic": "机器人执行器龙头，空心杯电机技术领先", "upgrade_conditions": "机器人板块整体回暖 + 业绩超预期", "downgrade_warning": "机器人量产推迟 + 竞争加剧",
    },
    {
        "code": "300354", "name": "东华测试",
        "sector": "科学仪器/力学测试+PHM", "track_barrier": 2.00, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "国产力学性能测试仪器龙头，毛利率65%/研发14%极高壁垒，PHM故障预测切入军工+新能源，38亿市值0研报极度冷门，国产替代瓶颈标的", "upgrade_conditions": "军工/新能源测试订单放量 + 净利增速30%+ + 现金流转正 + 新领域突破", "downgrade_warning": "研发投入效率低 + 下游需求疲软 + 现金流持续为负",
    },
    {
        "code": "603119", "name": "浙江荣泰",
        "sector": "新能源云母绝缘材料", "track_barrier": 1.64, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "300811", "name": "铂科新材",
        "sector": "金属软磁粉芯/AI电感材料", "track_barrier": 1.90, "ideal_buy": "77元",
        "stop_loss": "62元", "target": "120元", "position_suggest": 12,
        "core_logic": "2026H1营收+37%/净利+18%，AI服务器电感芯片电感上游核心，全球金属软磁龙头", "upgrade_conditions": None, "downgrade_warning": "AI算力需求放缓 + 毛利率下滑",
    },
    {
        "code": "300487", "name": "蓝晓科技",
        "sector": "吸附分离材料/盐湖提锂", "track_barrier": 2.10, "ideal_buy": "60元",
        "stop_loss": "49.5元", "target": "93元", "position_suggest": 0,
        "core_logic": "毛利率53%/净利率30%/净现比1.05，吸附分离全球龙头，业绩高质量但H1微降，等增速重启", "upgrade_conditions": "盐湖提锂订单放量 + 净利重回双位数增长", "downgrade_warning": "锂价低迷订单下滑 + 毛利率跌破45%",
    },
    {
        "code": "300726", "name": "宏达电子",
        "sector": "钽电容/军工电子元器件", "track_barrier": 2.20, "ideal_buy": "54元",
        "stop_loss": "43.8元", "target": "85元", "position_suggest": 0,
        "core_logic": "毛利率58%/净现比1.67/负债仅10%，军工钽电容龙头，质量极高但H1业绩平稳，等军品订单回升", "upgrade_conditions": "军工电子订单回暖 + 净利增速回双位数", "downgrade_warning": "军品降价 + 毛利率持续下滑",
    },
    {
        "code": "002801", "name": "微光股份",
        "sector": "机器人核心/微电机", "track_barrier": 1.40, "ideal_buy": "24.7元",
        "stop_loss": "20.3元", "target": "38元", "position_suggest": 0,
        "core_logic": "微电机/ECM电机龙头，ROE 20%质地极优，切入机器人关节+储能热管理+无人机，62亿市值冷门低估", "upgrade_conditions": "机器人电机批量出货 + 营收增速提升到20%+ + 新业务占比提升", "downgrade_warning": "传统业务下滑 + 新拓展不及预期",
    },
    {
        "code": "002530", "name": "金财互联",
        "sector": "热处理设备与工艺", "track_barrier": 1.34, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "300718", "name": "长盛轴承",
        "sector": "高端轴承/自润滑轴承", "track_barrier": 1.58, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "", "upgrade_conditions": None, "downgrade_warning": None,
    },
    {
        "code": "603267", "name": "鸿远电子",
        "sector": "军工电子/MLCC瓷介电容", "track_barrier": 2.00, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "军用高可靠MLCC龙头，毛利率43%/净利率24%质地优，资产负债率仅13%，117亿市值3研报低覆盖，军工电子国产替代瓶颈", "upgrade_conditions": "军工电子订单加速 + 净利增速回升到20%+ + 毛利率维持40%+", "downgrade_warning": "军品降价 + 民用MLCC价格战 + 需求不及预期",
    },
    {
        "code": "300984", "name": "金沃股份",
        "sector": "高端轴承/轴承套圈", "track_barrier": 1.60, "ideal_buy": "27元",
        "stop_loss": "22.3元", "target": "41.5元", "position_suggest": 0,
        "core_logic": "高端轴承套圈精密毛坯龙头，卡在轴承产业链上游，国产替代瓶颈，小市值高弹性", "upgrade_conditions": "高端轴承客户突破 + 净利增速回20%+ + 毛利率回升", "downgrade_warning": "汽车周期下行 + 高端客户拓展不及预期",
    },
]

REMOVED_POOL = [  # ❌ 已移除 <4.5
    {
        "code": "301583", "name": "托伦斯",
        "sector": "半导体精密阀门", "track_barrier": 2.30, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "国内唯一，技术壁垒极高，但短期游资炒作，等回调", "upgrade_conditions": "回调到合理估值 + 业绩兑现", "downgrade_warning": "技术被证伪 + 竞争格局恶化",
    },
    {
        "code": "300655", "name": "晶瑞电材",
        "sector": "光刻胶", "track_barrier": 1.50, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "KrF光刻胶批量出货，小市值高弹性", "upgrade_conditions": "光刻胶板块持续升温 + 业绩兑现", "downgrade_warning": "技术落后 + 价格战",
    },
    {
        "code": "300203", "name": "聚光科技",
        "sector": "科学仪器/质谱", "track_barrier": 2.00, "ideal_buy": "-",
        "stop_loss": "-", "target": "-", "position_suggest": 0,
        "core_logic": "质谱仪国产龙头，赛道好但业绩差，等拐点", "upgrade_conditions": "业绩扭亏 + 高端仪器突破", "downgrade_warning": "持续亏损 + 应收账款恶化",
    },
]

# ============================================================
# 📊 赛道纯度数据库（紫苏叶纯度评分）
# ============================================================
SECTOR_PURITY = [
    {"sector": "EDA/工业软件", "purity": 8.5, "rep_stocks": "广立微、华大九天", "buyable_count": 2},
    {"sector": "科学仪器", "purity": 8.5, "rep_stocks": "聚光科技（业绩差）", "buyable_count": 1},
    {"sector": "高温合金/航发材料", "purity": 8.0, "rep_stocks": "钢研高纳、抚顺特钢", "buyable_count": 2},
    {"sector": "高端液压件", "purity": 7.8, "rep_stocks": "艾迪精密、恒立液压", "buyable_count": 2},
    {"sector": "光刻胶", "purity": 7.5, "rep_stocks": "彤程新材、晶瑞电材", "buyable_count": 5},
    {"sector": "光学晶体", "purity": 7.5, "rep_stocks": "福晶科技", "buyable_count": 1},
    {"sector": "高端密封件", "purity": 7.5, "rep_stocks": "中密控股", "buyable_count": 1},
    {"sector": "高端轴承", "purity": 7.0, "rep_stocks": "国机精工、新强联", "buyable_count": 2},
    {"sector": "电子特气", "purity": 7.0, "rep_stocks": "和远气体", "buyable_count": 1},
    {"sector": "溅射靶材", "purity": 6.5, "rep_stocks": "江丰电子", "buyable_count": 2},
    {"sector": "机器人核心部件", "purity": 6.5, "rep_stocks": "鸣志电器、双环传动", "buyable_count": 4},
    {"sector": "液冷/散热", "purity": 6.0, "rep_stocks": "英维克、申菱环境", "buyable_count": 3},
]

# ============================================================
# ⚠️ 排除规则（科创板不能买）
# ============================================================
def is_buyable(code):
    """判断能不能买（排除科创板688xxx）"""
    return not code.startswith("688")

# ============================================================
# 🎯 评分体系阈值（满分10分）
# ============================================================
SCORING_WEIGHTS = {
    "track_barrier": 2.5,   # 赛道壁垒
    "earnings_growth": 2.5,  # 业绩增长
    "profitability": 1.5,    # 盈利能力
    "prosperity_check": 0.5,  # 业绩加速度（2026-09-27 校准：原景气度1.5，删3个重复子项后降为0.5）
    "heat_position": 1.0,    # 热度/位置
    "market_cap_elasticity": 1.0,  # 市值弹性
}
# 满分合计 = 2.5+2.5+1.5+0.5+1.0+1.0 = 9.0（原10.0，删重叠冗余1.0分）

POOL_THRESHOLDS = {
    "candidate": 6.5,    # ≥6.5 → 候选池（原7.5，满分降至9后整体-1）
    "observation": 5.5,  # 5.5-6.5 → 观察池
    "reserve": 4.5,      # 4.5-5.5 → 储备池
    # <4.5 → 移除
}

# 连续N周触发升级/降级
UPGRADE_WEEKS = 2
DOWNGRADE_WEEKS = 2
REMOVE_WEEKS = 4

# ============================================================
# 🐟 金枪鱼池 + 定额进出机制（2026-10-17 新增）
# ============================================================
# 设计要点（已与用户逐条确认）：
#   1. 定额制：出一只补一只，池子名额恒定；找不到够格的票就空着，不降标准硬补
#   2. 三种出池去向，互不混用：
#        金枪鱼池 ← 变大/变热（不再冷门），仍跟踪，满足三条可回归
#        移除池   ← 业绩崩 / 逻辑证伪 / 分数长期破线
#   3. 出池要连续 2 周确认；回归要连续 4 周确认（滞回区间，防边界横跳）
#   4. 新进池当周不给买入价，次周数据确认后才给

POOL_CAPACITY = 28              # 紫苏叶池名额上限（按 2026-10-17 实有 28 只定基准）
TUNA_CONFIRM_WEEKS = 2          # 金枪鱼化出池：连续 2 周命中即出
TUNA_BACK_WEEKS = 4             # 金枪鱼回归：连续 4 周满足三条才回（先进储备池）
NEW_ENTRY_NO_PRICE_WEEKS = 1    # 新进池当周不给买入价

# 金枪鱼池初始名单（2026-10-17 首次迁移快照）
# 运行时由 data/tracking_state.json 维护，这里只作人工可读的底稿。
# 字段：code / name / sector / exit_date / exit_reason(hot|big|both)
#       exit_mv(亿) / tuna_status(watching|archived)
TUNA_POOL = [  # 🐟 金枪鱼池（已变大/变热出池，仍跟踪；回归要连续 4 周满足三条）
    {
        "code": "603650", "name": "彤程新材", "sector": "光刻胶/半导体材料",
        "exit_date": "2026-10-17", "exit_reason": "big", "exit_mv": 345.52, "tuna_status": "watching",
    },
    {
        "code": "301269", "name": "华大九天", "sector": "EDA全流程",
        "exit_date": "2026-10-17", "exit_reason": "both", "exit_mv": 538.83, "tuna_status": "watching",
    },
    {
        "code": "601100", "name": "恒立液压", "sector": "高端液压件",
        "exit_date": "2026-10-17", "exit_reason": "both", "exit_mv": 1047.58, "tuna_status": "watching",
    },
    {
        "code": "002472", "name": "双环传动", "sector": "RV减速器/机器人",
        "exit_date": "2026-10-17", "exit_reason": "big", "exit_mv": 282.37, "tuna_status": "watching",
    },
    {
        "code": "300124", "name": "汇川技术", "sector": "工业自动化/伺服",
        "exit_date": "2026-10-17", "exit_reason": "both", "exit_mv": 1346.81, "tuna_status": "watching",
    },
]




# 进池硬门槛：赛道情报已录入 + 财务数据拉全，缺一不给进
ENTRY_REQUIRE_INTEL = True
ENTRY_REQUIRE_FINANCIAL = True
