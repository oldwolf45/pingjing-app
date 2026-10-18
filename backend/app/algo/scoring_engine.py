# -*- coding: utf-8 -*-
"""
🍃 紫苏叶策略 V2.0 - 核心评分引擎
6维10分制：赛道壁垒 + 业绩增长 + 盈利能力 + 资金面 + 热度/位置 + 市值弹性
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Tuple
from datetime import datetime
import json
import os


@dataclass
class ScoreBreakdown:
    """评分明细（6维10分制）"""
    track_barrier: float = 0.0          # 赛道壁垒（2.5分）- 灵魂
    earnings_growth: float = 0.0        # 业绩增长（2.5分）- 验证
    profitability: float = 0.0          # 盈利能力（1.5分）- 质量
    prosperity_check: float = 0.0       # 业绩加速度（0.5分）- 原景气度验证，2026-09-27精简后只留净利-营收差
    heat_position: float = 0.0          # 热度/估值（1.0分）- 原热度位置，优化为热度+估值安全边际
    market_cap_elasticity: float = 0.0  # 市值弹性（1.0分）- 赔率

    def total(self) -> float:
        return round(sum([
            self.track_barrier, self.earnings_growth,
            self.profitability, self.prosperity_check,
            self.heat_position, self.market_cap_elasticity
        ]), 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total()
        return d


@dataclass
class FinancialSnapshot:
    """财务快照（用于计算业绩增长和盈利能力）"""
    # 业绩增长相关
    revenue_yoy: Optional[float] = None       # 营收同比(%)
    net_profit_yoy: Optional[float] = None    # 归母净利同比(%)
    deduct_profit_yoy: Optional[float] = None  # 扣非净利同比(%)
    order_growth: Optional[float] = None       # 订单/合同负债增速(%)

    # 盈利能力相关
    gross_margin: Optional[float] = None       # 毛利率(%)
    gross_margin_trend: Optional[str] = None   # 毛利率趋势: up/flat/down
    roe: Optional[float] = None                # ROE加权(%)
    net_margin: Optional[float] = None         # 净利率(%)
    ocf_to_ni: Optional[float] = None          # 经营现金流/净利润
    # 2026-10-05 新增：扣非净利绝对值质量闸
    # 背景：广立微净利 +121.7% 但扣非净利仅 503 万，扣非 EPS 0.0255 元。
    # 原实现只比「扣非增速 vs 净利增速」的差异，不看扣非绝对值，
    # 导致「低基数高增速」被当成高质量增长，PE 静态 386 倍/扣非 2671 倍。
    deduct_profit_abs: Optional[float] = None   # 扣非归母净利绝对值(万元)
    eps_deduct: Optional[float] = None          # 扣非 EPS(元)

    # 其他
    total_mv: Optional[float] = None           # 总市值（亿元）


@dataclass
class StockScore:
    """单只股票的完整评分结果"""
    code: str
    name: str
    sector: str
    score: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    financial: FinancialSnapshot = field(default_factory=FinancialSnapshot)
    notes: List[str] = field(default_factory=list)
    pool: str = "reserve"  # candidate/observation/reserve/removed
    last_updated: str = ""
    barrier_detail: Optional[dict] = None  # 赛道壁垒8维明细（barrier_engine输出）

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "sector": self.sector,
            "score": self.score.to_dict(),
            "financial": asdict(self.financial),
            "notes": self.notes,
            "pool": self.pool,
            "last_updated": self.last_updated,
            "total_score": self.score.total(),
            "barrier_detail": self.barrier_detail,
        }


# ============================================================
# 评分函数
# ============================================================

def score_track_barrier(track_score: float, notes: list) -> float:
    """赛道壁垒（2.5分）- 8维评估结果直接用"""
    s = max(0.0, min(2.5, track_score))
    if s >= 2.2:
        notes.append(f"赛道壁垒极高({s:.1f}/2.5)")
    elif s >= 1.8:
        notes.append(f"赛道壁垒较高({s:.1f}/2.5)")
    elif s >= 1.3:
        notes.append(f"赛道壁垒中等({s:.1f}/2.5)")
    else:
        notes.append(f"赛道壁垒一般({s:.1f}/2.5)")
    return s


def score_earnings_growth(fin: FinancialSnapshot, notes: list) -> float:
    """
    业绩增长（2.5分）
    - 营收增速权重: 0.8分
    - 净利增速权重: 1.0分
    - 扣非/质量: 0.4分
    - 订单/合同负债: 0.3分
    """
    s = 0.0

    # 营收增速（0.8分）
    if fin.revenue_yoy is not None:
        if fin.revenue_yoy >= 50:
            s += 0.8
            notes.append(f"营收高增({fin.revenue_yoy:.1f}%)")
        elif fin.revenue_yoy >= 30:
            s += 0.6
            notes.append(f"营收较快增长({fin.revenue_yoy:.1f}%)")
        elif fin.revenue_yoy >= 15:
            s += 0.4
            notes.append(f"营收稳健增长({fin.revenue_yoy:.1f}%)")
        elif fin.revenue_yoy >= 0:
            s += 0.2
            notes.append(f"营收微增({fin.revenue_yoy:.1f}%)")
        else:
            s += 0.0
            notes.append(f"营收下滑({fin.revenue_yoy:.1f}%)")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0，不给中性兜底分
        notes.append("营收数据缺失")

    # 净利增速（1.0分）
    if fin.net_profit_yoy is not None:
        if fin.net_profit_yoy >= 80:
            s += 1.0
            notes.append(f"净利爆发({fin.net_profit_yoy:.1f}%)⭐")
        elif fin.net_profit_yoy >= 50:
            s += 0.8
            notes.append(f"净利高增({fin.net_profit_yoy:.1f}%)")
        elif fin.net_profit_yoy >= 30:
            s += 0.6
            notes.append(f"净利较快增长({fin.net_profit_yoy:.1f}%)")
        elif fin.net_profit_yoy >= 15:
            s += 0.4
            notes.append(f"净利稳健增长({fin.net_profit_yoy:.1f}%)")
        elif fin.net_profit_yoy >= 0:
            s += 0.2
            notes.append(f"净利微增({fin.net_profit_yoy:.1f}%)")
        elif fin.net_profit_yoy >= -20:
            s += 0.1
            notes.append(f"净利小幅下滑({fin.net_profit_yoy:.1f}%)")
        else:
            s += 0.0
            notes.append(f"净利大幅下滑({fin.net_profit_yoy:.1f}%)⚠️")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("净利数据缺失")

    # 扣非质量（0.4分）
    if fin.deduct_profit_yoy is not None and fin.net_profit_yoy is not None:
        diff = abs(fin.deduct_profit_yoy - fin.net_profit_yoy)
        if diff < 10:
            s += 0.4
            notes.append("扣非与净利一致，质量高")
        elif diff < 30:
            s += 0.2
            notes.append(f"扣非与净利有差异({diff:.0f}pct)，需关注")
        else:
            s += 0.1
            notes.append(f"扣非与净利差异大({diff:.0f}pct)，非经常性损益多⚠️")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("扣非数据缺失")

    # 订单/合同负债（0.3分）
    if fin.order_growth is not None:
        if fin.order_growth >= 50:
            s += 0.3
            notes.append(f"订单高增({fin.order_growth:.1f}%)，未来业绩有保障")
        elif fin.order_growth >= 20:
            s += 0.2
            notes.append(f"订单较快增长({fin.order_growth:.1f}%)")
        elif fin.order_growth >= 0:
            s += 0.1
            notes.append(f"订单平稳({fin.order_growth:.1f}%)")
        else:
            s += 0.0
            notes.append(f"订单下滑({fin.order_growth:.1f}%)⚠️")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("订单数据缺失")

    return round(s, 2)


def score_profitability(fin: FinancialSnapshot, notes: list) -> float:
    """
    盈利能力（1.5分）
    - 毛利率: 0.6分（2026-10-05 从 0.7 下调）
    - 毛利率趋势: 0.2分（从 0.3 下调）
    - ROE: 0.4分（2026-10-05 从 0.3 上调）
    - 扣非利润质量: 0.2分（2026-10-05 新增）
    - 现金流质量: 0.1分（从 0.2 下调）

    2026-10-05 权重调整理由：
    原权重 ROE 仅占 0.3/1.5 = 20%，一家ROE 2.18% 的公司扣光 ROE 分
    只掉 0.3 分（10分制），惩罚过轻，导致广立微「盈利能力 1.2/1.5」
    在 ROE 2.18% 的情况下仍进候选池第 1。
    ROE 是盈利质量的硬核指标，权重提到 0.4；腾出的 0.1 分给新增的
    扣非利润质量闸——只看增速差看不出「低基数高增速」，必须看绝对值。
    """
    s = 0.0

    # 毛利率（0.6分）
    if fin.gross_margin is not None:
        if fin.gross_margin >= 50:
            s += 0.6
            notes.append(f"毛利率极高({fin.gross_margin:.1f}%)⭐")
        elif fin.gross_margin >= 40:
            s += 0.47
            notes.append(f"毛利率高({fin.gross_margin:.1f}%)")
        elif fin.gross_margin >= 30:
            s += 0.34
            notes.append(f"毛利率较高({fin.gross_margin:.1f}%)")
        elif fin.gross_margin >= 20:
            s += 0.21
            notes.append(f"毛利率一般({fin.gross_margin:.1f}%)")
        else:
            s += 0.09
            notes.append(f"毛利率低({fin.gross_margin:.1f}%)")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("毛利率数据缺失")

    # 毛利率趋势（0.2分）
    if fin.gross_margin_trend == "up":
        s += 0.2
        notes.append("毛利率持续提升🟢")
    elif fin.gross_margin_trend == "flat":
        s += 0.13
        notes.append("毛利率稳定")
    elif fin.gross_margin_trend == "down":
        s += 0.03
        notes.append("毛利率下滑🔴⚠️")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("毛利率趋势数据缺失")

    # ROE（0.4分，2026-10-05 从 0.3 上调）
    # 权重上调后阶梯同步收紧：<5% 视为盈利能力硬伤，扣满 0.4
    if fin.roe is not None:
        if fin.roe >= 15:
            s += 0.4
            notes.append(f"ROE优秀({fin.roe:.1f}%)")
        elif fin.roe >= 10:
            s += 0.28
            notes.append(f"ROE良好({fin.roe:.1f}%)")
        elif fin.roe >= 5:
            s += 0.14
            notes.append(f"ROE一般({fin.roe:.1f}%)")
        elif fin.roe >= 3:
            s += 0.05
            notes.append(f"ROE偏低({fin.roe:.1f}%)⚠️")
        else:
            s += 0.0
            notes.append(f"ROE过低({fin.roe:.1f}%)🔴⚠️盈利能力硬伤")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("ROE数据缺失")

    # 扣非利润质量（0.2分，2026-10-05 新增）
    # 核心：低基数高增速是伪质量。广立微扣非 503 万、EPS 0.0255 元，
    # 净利增速 +121.7% 很好看，但扣非撑不起 136 亿市值。
    if fin.eps_deduct is not None:
        if fin.eps_deduct >= 0.50:
            s += 0.2
            notes.append(f"扣非EPS充足({fin.eps_deduct:.2f}元)🟢")
        elif fin.eps_deduct >= 0.20:
            s += 0.14
            notes.append(f"扣非EPS尚可({fin.eps_deduct:.2f}元)")
        elif fin.eps_deduct >= 0.05:
            s += 0.05
            notes.append(f"扣非EPS偏低({fin.eps_deduct:.2f}元)⚠️利润薄")
        else:
            s += 0.0
            notes.append(f"扣非EPS极低({fin.eps_deduct:.3f}元)🔴⚠️低基数高增速，非经常性损益驱动")
    elif fin.deduct_profit_abs is not None:
        # 无 EPS 时退化为绝对值档位（万元）
        if fin.deduct_profit_abs >= 10000:
            s += 0.2
            notes.append(f"扣非净利充足({fin.deduct_profit_abs / 10000:.2f}亿)🟢")
        elif fin.deduct_profit_abs >= 3000:
            s += 0.14
            notes.append(f"扣非净利尚可({fin.deduct_profit_abs / 10000:.2f}亿)")
        elif fin.deduct_profit_abs >= 800:
            s += 0.05
            notes.append(f"扣非净利偏低({fin.deduct_profit_abs:.0f}万)⚠️")
        else:
            s += 0.0
            notes.append(f"扣非净利极低({fin.deduct_profit_abs:.0f}万)🔴⚠️利润规模不支撑估值")
    else:
        s += 0.0
        notes.append("扣非利润数据缺失")

    # 现金流质量（0.1分，从 0.2 下调）
    if fin.ocf_to_ni is not None:
        if fin.ocf_to_ni >= 1.0:
            s += 0.1
            notes.append(f"现金流健康(OCF/NI={fin.ocf_to_ni:.2f})🟢")
        elif fin.ocf_to_ni >= 0.5:
            s += 0.06
            notes.append(f"现金流尚可(OCF/NI={fin.ocf_to_ni:.2f})")
        elif fin.ocf_to_ni >= 0.3:
            s += 0.02
            notes.append(f"现金流偏紧(OCF/NI={fin.ocf_to_ni:.2f})🟡")
        else:
            s += 0.0
            notes.append(f"现金流差(OCF/NI={fin.ocf_to_ni:.2f})🔴⚠️")
    else:
        s += 0.0  # 2026-09-27 修复：缺数据给0
        notes.append("现金流数据缺失")

    return round(s, 2)


def score_prosperity_check(fin: FinancialSnapshot, sector_heat_change: Optional[float] = None, notes: list = None) -> float:
    """
    业绩加速度（0.5分）—— 原「景气度验证」的精简版

    2026-09-27 校准：原实现用「订单增速 + 营收增速 + 毛利率趋势」三个子项，
    与 earnings_growth / profitability 高度共线（24只实测相关性 0.909）。
    同一批财务数据被三个维度重复计分，维度重叠，权重反推会失效。
    现只保留唯一独立信号「业绩加速度」（净利增速 - 营收增速），其余删除。

    满分从 1.5 降到 0.5。
    """
    if notes is None:
        notes = []
    s = 0.0

    # 业绩加速度 = 净利增速 - 营收增速（经营杠杆 / 规模效应）
    # 这是「利润增长是否快于收入增长」的独立信号，不由单一增速决定。
    if fin.revenue_yoy is not None and fin.net_profit_yoy is not None:
        leverage = fin.net_profit_yoy - fin.revenue_yoy
        if leverage >= 30:
            s += 0.5
            notes.append(f"业绩加速释放(净利-营收差={leverage:.0f}pct)⭐")
        elif leverage >= 10:
            s += 0.37
            notes.append(f"利润快于收入({leverage:.0f}pct)，规模效应显现")
        elif leverage >= 0:
            s += 0.25
            notes.append(f"利润增速匹配营收({leverage:.0f}pct)")
        elif leverage >= -10:
            s += 0.13
            notes.append(f"利润慢于营收({leverage:.0f}pct)，略承压")
        else:
            s += 0.0
            notes.append(f"利润大幅慢于营收({leverage:.0f}pct)⚠️")
    else:
        s += 0.0
        notes.append("业绩加速度待核实")

    return round(s, 2)


def score_heat_position(sector_heat_change: Optional[float],
                        price_change_5d: Optional[float] = None,
                        peg: Optional[float] = None,
                        fin: FinancialSnapshot = None,
                        sector_heat_level: Optional[str] = None,
                        notes: list = None,
                        popularity_score: Optional[float] = None) -> float:
    """
    热度/估值位置（1.0分）—— 升级版
    
    不追热点，但也不排斥合理热度。核心是「景气度向上 + 估值还不贵」。
    
    权重分配：
    - 板块热度变化：0.4分（行业景气度的市场验证）
    - PEG估值安全边际：0.4分（贵不贵，比涨没涨更重要）
    - 股价位置/5日涨跌：0.2分（短期情绪）
    """
    if notes is None:
        notes = []
    s = 0.0

    # === 1. 板块热度变化（0.4分）===
    # 紫苏叶偏好：景气度在好转但还没到人人都知道的程度
    if sector_heat_change is not None:
        if 5 <= sector_heat_change <= 20:
            s += 0.4
            notes.append(f"板块温和升温({sector_heat_change:.1f}%)，景气度好转🟢")
        elif sector_heat_change > 20:
            s += 0.25
            notes.append(f"板块热度飙升({sector_heat_change:.1f}%)，注意追高风险⚠️")
        elif 0 <= sector_heat_change < 5:
            s += 0.3
            notes.append(f"板块热度平稳({sector_heat_change:.1f}%)")
        elif -10 <= sector_heat_change < 0:
            s += 0.15
            notes.append(f"板块降温中({sector_heat_change:.1f}%)，可观察")
        else:
            s += 0.05
            notes.append(f"板块遇冷({sector_heat_change:.1f}%)，需警惕⚠️")
    else:
        s += 0.0
        notes.append("板块热度数据缺失")

    # === 2. PEG估值安全边际（0.4分）===
    # PEG < 0.8 = 便宜；0.8-1.2 = 合理；>1.5 = 贵
    # 2026-09-27 修复：原实现 peg 缺失时用净利增速当「估值安全边际」给分，
    # 概念混淆（高增长 ≠ 便宜，反而常更贵）。现改为缺 PEG 给 0 分。
    if peg is not None:
        if peg <= 0.5:
            s += 0.4
            notes.append(f"PEG极低({peg:.2f})，严重低估⭐")
        elif peg <= 0.8:
            s += 0.35
            notes.append(f"PEG便宜({peg:.2f})，估值有安全边际🟢")
        elif peg <= 1.2:
            s += 0.25
            notes.append(f"PEG合理({peg:.2f})")
        elif peg <= 1.8:
            s += 0.12
            notes.append(f"PEG偏贵({peg:.2f})⚠️")
        else:
            s += 0.0
            notes.append(f"PEG很贵({peg:.2f})，透支未来⚠️⚠️")
    else:
        s += 0.0
        notes.append("PEG数据缺失，估值维度不给分（待核实）")

    # === 3. 股价位置（0.2分）===
    # 紫苏叶偏好：还没启动或者刚启动的
    if price_change_5d is not None:
        if -3 <= price_change_5d <= 3:
            s += 0.2
            notes.append(f"股价平稳(5日{price_change_5d:+.1f}%)，位置舒适")
        elif 3 < price_change_5d <= 8:
            s += 0.15
            notes.append(f"温和上涨(5日{price_change_5d:+.1f}%)")
        elif price_change_5d > 8:
            s += 0.08
            notes.append(f"短期涨幅大(5日{price_change_5d:+.1f}%)，等回调")
        elif -8 <= price_change_5d < -3:
            s += 0.12
            notes.append(f"回调中(5日{price_change_5d:+.1f}%)，关注买点")
        else:
            s += 0.05
            notes.append(f"暴跌(5日{price_change_5d:+.1f}%)，先查基本面")
    else:
        s += 0.0
        notes.append("股价位置数据缺失")

    # === 4. 冷门度调整（2026-09-19 新增：补白毛方法论「认知差」维度）===
    # 只做 ±0.15 调整，不改动上面三项原权重，保持历史评分可比
    if popularity_score is not None:
        ps = float(popularity_score)
        if ps >= 0.75:
            s = min(1.0, s + 0.15)
            notes.append(f"冷门度{ps:.2f}：研报/机构覆盖少，仍属紫苏叶🟢(+0.15)")
        elif ps < 0.40:
            s = max(0.0, s - 0.15)
            notes.append(f"冷门度{ps:.2f}：已金枪鱼化（覆盖高/市值大），不再冷门⚠️(-0.15)")
        else:
            notes.append(f"冷门度{ps:.2f}：关注度中等")
    else:
        notes.append("冷门度数据待核实")

    return round(s, 2)


def score_market_cap_elasticity(total_mv: Optional[float], notes: list) -> float:
    """
    市值弹性（1.0分）
    越小弹性越大
    """
    if total_mv is None:
        notes.append("市值数据缺失")
        return 0.0  # 2026-09-27 修复：原给 0.6（默认中小盘），改为缺数据给 0，不给假分

    if total_mv < 50:
        s = 1.0
        notes.append(f"极小市值({total_mv:.0f}亿)，弹性极大⭐")
    elif total_mv < 100:
        s = 0.85
        notes.append(f"小市值({total_mv:.0f}亿)，弹性大")
    elif total_mv < 200:
        s = 0.7
        notes.append(f"中小市值({total_mv:.0f}亿)，弹性不错")
    elif total_mv < 400:
        s = 0.5
        notes.append(f"中市值({total_mv:.0f}亿)，弹性一般")
    elif total_mv < 800:
        s = 0.3
        notes.append(f"中大盘({total_mv:.0f}亿)，弹性较小")
    else:
        s = 0.15
        notes.append(f"大盘股({total_mv:.0f}亿)，弹性小")

    return s


def determine_pool(total_score: float) -> str:
    """根据总分确定池子（阈值 6.5/5.5/4.5，与 config.POOL_THRESHOLDS 对齐）
    2026-09-27 校准：满分由 10 降至 9.0（删 prosperity_check 重叠的 1.0 分），
    阈值同步整体下移 1 分，保持原「75%/65%/55%」分档语义。
    """
    if total_score >= 6.5:
        return "candidate"
    elif total_score >= 5.5:
        return "observation"
    elif total_score >= 4.5:
        return "reserve"
    else:
        return "removed"


def score_tuna_penalty(tuna_severity: Optional[int], notes: list) -> float:
    """
    金枪鱼化惩罚（负分）

    紫苏叶的核心定义是「产业链上游不起眼但不可替代的隐形瓶颈」。
    一旦被市场发现（研报密集 / 主动资金重仓）或者长成大盘股，
    它就不再是紫苏叶，而是金枪鱼。

    === 2026-10-17 调整 ===

    判定结构从「三条中两条」重写为两个独立理由，各自单条成立：
      变热了：研报 > 20 篇  或  主动资金占流通 > 8%
      变大了：总市值 > 250 亿

    severity 语义随之简化：
      0 = 健康（两条都没命中）
      1 = 命中一类（变热 或 变大）
      2 = 两类都命中

    扣分只作为出池前的缓冲。真正决定去留的是 pool_manager 的
    连续两周确认 + 出池动作，不是这里的扣分。
    """
    if tuna_severity is None:
        return 0.0
    if tuna_severity <= 0:
        return 0.0
    if tuna_severity == 1:
        notes.append("金枪鱼化(命中一类)：已偏离紫苏叶前提，扣0.8分🔴")
        return -0.8
    notes.append("金枪鱼化(变热+变大都中)：已是金枪鱼，扣1.5分🔴⚠️")
    return -1.5


def calculate_full_score(
    code: str, name: str, sector: str,
    track_barrier_score: float,
    fin: FinancialSnapshot,
    sector_heat_change: Optional[float] = None,
    price_change_5d: Optional[float] = None,
    peg: Optional[float] = None,
    sector_heat_level: Optional[str] = None,
    popularity_score: Optional[float] = None,
    tuna_severity: Optional[int] = None,
) -> StockScore:
    """
    计算完整的6维评分

    Args:
        code: 股票代码
        name: 股票名称
        sector: 赛道
        track_barrier_score: 赛道壁垒分（0-2.5）
        fin: 财务快照
        sector_heat_change: 板块热度变化(%)
        price_change_5d: 5日涨跌幅(%)
        peg: PEG估值（PE/净利增速）
        sector_heat_level: 热度等级 hot/cold/normal
        popularity_score: 冷门度 0~1（越高越冷门）
        tuna_severity: 金枪鱼化严重度 0~3（2026-10-05 新增，负分惩罚档位）

    Returns:
        StockScore 对象
    """
    notes = []
    sb = ScoreBreakdown()

    # 1. 赛道壁垒（灵魂）
    sb.track_barrier = score_track_barrier(track_barrier_score, notes)

    # 2. 业绩增长（验证）
    sb.earnings_growth = score_earnings_growth(fin, notes)

    # 3. 盈利能力（质量）
    sb.profitability = score_profitability(fin, notes)

    # 4. 景气度验证（原资金面，全自动版）
    sb.prosperity_check = score_prosperity_check(fin, sector_heat_change, notes)

    # 5. 热度/估值（原热度位置，升级版）
    sb.heat_position = score_heat_position(
        sector_heat_change, price_change_5d, peg, fin, sector_heat_level, notes,
        popularity_score=popularity_score
    )

    # 6. 市值弹性（赔率）
    sb.market_cap_elasticity = score_market_cap_elasticity(fin.total_mv, notes)

    # 7. 金枪鱼化惩罚（2026-10-05 新增）—— 扣在总分上
    tuna_penalty = score_tuna_penalty(tuna_severity, notes)

    total = round(sb.total() + tuna_penalty, 2)
    # 评分体系总分仍是 10 分制，惩罚后允许为负；下限截到 0
    total = max(0.0, total)
    pool = determine_pool(total)

    return StockScore(
        code=code, name=name, sector=sector,
        score=sb, financial=fin, notes=notes,
        pool=pool, last_updated=datetime.now().isoformat(),
    )
