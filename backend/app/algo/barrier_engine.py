# -*- coding: utf-8 -*-
"""
🍃 紫苏叶策略 V2.0 - 赛道壁垒自动评分引擎（barrier_engine）
=============================================================
把「赛道壁垒 2.5 分」从 config.py 里的手填死分，升级为
「8 维规则卡 + 财报直算 + 赛道情报 + 置信度分级」的自动评分。

权重体系（与 SKILL.md 第7节 8维评估一致，满分18 → 折算2.5）：
  1. tech_barrier      技术壁垒高度    3 分（代理指标组合，灵魂维度）
  2. local_substitution 国产替代紧迫度  3 分（赛道情报）
  3. customer_cert      客户认证制      2 分（赛道情报 + 客户集中度代理）
  4. upstream_bargain   上游议价能力    2 分（财报直算，100%自动）
  5. market_space       行业空间/增速   2 分（赛道情报）
  6. competition        竞争格局        3 分（赛道情报）
  7. iteration_speed    产品迭代速度    1 分（代理指标，慢=壁垒高）
  8. policy_support     政策支持        2 分（赛道情报）

设计原则（命门）：
- 全自动：8维评分零人工介入，跑完直接出分，无「拍板」环节
- 代理指标 ≠ 猜：每个代理都有财报字段支撑，规则写死、可复核
- 每维输出「分数 + 置信度 + 证据来源」，缺数据标 missing，绝不给假分
- 情报缺失自动标注，由每周定时任务自动刷新，不要求用户触发
- config 里手动设置 barrier_manual_override 可锁分（可选能力，默认不用）
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
from datetime import datetime


# ============================================================
# 8 维权重表（满分 18 → 折算 2.5）
# ============================================================
BARRIER_WEIGHTS = {
    "tech_barrier":       3,
    "local_substitution": 3,
    "customer_cert":      2,
    "upstream_bargain":   2,
    "market_space":       2,
    "competition":        3,
    "iteration_speed":    1,
    "policy_support":     2,
}
BARRIER_MAX_RAW = sum(BARRIER_WEIGHTS.values())  # 18
BARRIER_MAX_SCORE = 2.5  # 折算后的赛道壁垒满分

BARRIER_DIM_NAMES = {
    "tech_barrier":        "技术壁垒高度",
    "local_substitution":  "国产替代紧迫度",
    "customer_cert":       "客户认证制",
    "upstream_bargain":    "上游议价能力",
    "market_space":        "行业空间/增速",
    "competition":         "竞争格局",
    "iteration_speed":     "产品迭代速度",
    "policy_support":      "政策支持",
}

# 需要人工复核的关键维度（灵魂：技术+替代+格局）
KEY_DIMENSIONS = ("tech_barrier", "local_substitution", "competition")


@dataclass
class BarrierDimension:
    """单个维度的评分结果"""
    key: str
    name: str
    max_score: float          # 该维满分
    score: float              # 得分（0-max_score）
    confidence: int           # 置信度 0-100
    source: str               # 数据来源描述
    evidence: str = ""        # 关键证据
    missing: bool = False     # 是否缺数据

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BarrierResult:
    """赛道壁垒完整评分结果"""
    raw_score: float          # 8维原始总分（0-18）
    score: float              # 折算后赛道壁垒分（0-2.5）
    overall_confidence: int   # 综合置信度 0-100
    dimensions: List[BarrierDimension] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    needs_review: bool = False      # 是否需要人工复核
    review_reason: str = ""         # 复核原因
    source: str = "auto"            # auto / manual_override
    notes: List[str] = field(default_factory=list)
    updated: str = ""

    def to_dict(self) -> dict:
        return {
            "raw_score": self.raw_score,
            "score": self.score,
            "overall_confidence": self.overall_confidence,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "missing": self.missing,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "source": self.source,
            "notes": self.notes,
            "updated": self.updated,
        }


# ============================================================
# 分段打分辅助函数
# ============================================================

def _bracket(value: float, brackets: List[tuple]) -> float:
    """
    按分段表打分（0-1）。
    brackets: [(下限, 分值), ...]，从高到低，命中第一个 value >= 下限 的分值
    示例: _bracket(35, [(50,1.0),(40,0.8),(30,0.6),(20,0.4),(0,0.2)])
    """
    for low, score in brackets:
        if value >= low:
            return score
    return brackets[-1][1]


# ============================================================
# 各维度评分函数
# ============================================================

def _score_tech_barrier(extra: dict, intel: dict) -> BarrierDimension:
    """
    技术壁垒高度（3分）- 灵魂维度（全自动规则计算，无需人工）
    代理指标组合：研发费用率 + 毛利率(技术溢价) + 全球参与者数 + 发明专利占比
    每个指标的分值和来源都留痕（证据=数据来源，不是让人拍板，是可复核）
    """
    rd_ratio = extra.get("rd_ratio")
    gross_margin = extra.get("gross_margin")
    global_players = intel.get("global_players")
    patent_ratio = intel.get("invent_patent_ratio")

    subs = []  # (指标名, 0-1分, 证据)
    if rd_ratio is not None:
        s = _bracket(rd_ratio, [(25, 1.0), (15, 0.85), (10, 0.7), (5, 0.5), (2, 0.3), (0, 0.15)])
        subs.append(("研发费用率", s, f"研发费用率{rd_ratio:.1f}%"))
    if gross_margin is not None:
        s = _bracket(gross_margin, [(55, 0.9), (45, 0.8), (35, 0.65), (25, 0.5), (0, 0.3)])
        subs.append(("毛利率(技术溢价)", s, f"毛利率{gross_margin:.1f}%"))
    if global_players is not None and global_players > 0:
        # 玩家越少 = 壁垒越高（反向档位）
        if global_players <= 3:
            s = 1.0
        elif global_players <= 5:
            s = 0.88
        elif global_players <= 8:
            s = 0.72
        elif global_players <= 15:
            s = 0.5
        elif global_players <= 30:
            s = 0.3
        else:
            s = 0.15
        subs.append(("全球参与者数", s, f"全球约{global_players}家能做"))
    if patent_ratio is not None:
        s = _bracket(patent_ratio, [(50, 0.9), (30, 0.75), (15, 0.55), (0, 0.35)])
        subs.append(("发明专利占比", s, f"发明专利占比{patent_ratio:.0f}%"))

    if not subs:
        return BarrierDimension(
            key="tech_barrier", name="技术壁垒高度", max_score=3,
            score=0.0, confidence=25, source="缺数据",
            missing=True, evidence="无研发/专利/行业数据，无法自动评估",
        )

    avg = sum(s for _, s, _ in subs) / len(subs)
    score = round(avg * 3, 2)
    confidence = min(95, 60 + 20 * len(subs) // 2)
    evidence = "；".join(e for _, _, e in subs)
    source = "代理指标组合: " + "+".join(n for n, _, _ in subs)

    dim = BarrierDimension(
        key="tech_barrier", name="技术壁垒高度", max_score=3,
        score=score, confidence=confidence, source=source, evidence=evidence,
    )
    # 数据点少 → 置信度下调（信息不足，保守对待，不阻塞自动评分）
    if len(subs) <= 2:
        dim.confidence = min(dim.confidence, 55)
    return dim


def _score_local_substitution(intel: dict) -> BarrierDimension:
    """国产替代紧迫度（3分）"""
    rate = intel.get("substitution_rate")
    urgency = intel.get("substitution_urgency", "medium")

    if rate is None:
        return BarrierDimension(
            key="local_substitution", name="国产替代紧迫度", max_score=3,
            score=0.0, confidence=30, source="缺数据",
            missing=True, evidence="国产替代率未录入（招股书/研报）",
        )

    # 替代率越低 = 越紧迫 = 分数越高（紫苏叶核心逻辑：卡脖子=机会）
    if rate <= 10:
        s = 1.0        # 极度紧迫（10%以内=卡脖子）
    elif rate <= 20:
        s = 0.92       # 非常紧迫
    elif rate <= 35:
        s = 0.78       # 较紧迫
    elif rate <= 50:
        s = 0.6        # 中等紧迫
    elif rate <= 70:
        s = 0.4        # 替代过半，空间收窄
    else:
        s = 0.18       # 基本完成替代，机会不大
    # 紧迫度标签微调
    if urgency == "high":
        s = min(1.0, s + 0.08)
    elif urgency == "low":
        s = max(0.1, s - 0.1)

    return BarrierDimension(
        key="local_substitution", name="国产替代紧迫度", max_score=3,
        score=round(s * 3, 2), confidence=80, source="赛道情报(招股书/研报)",
        evidence=f"国产替代率{rate:.0f}%，紧迫度{urgency}",
    )


def _score_customer_cert(intel: dict, extra: dict) -> BarrierDimension:
    """客户认证制（2分）"""
    cycle = intel.get("cert_cycle_months")
    cert_level = intel.get("cert_level")  # high/medium/low
    top5 = extra.get("top5_customer")

    sub_scores = []
    sources = []
    if cycle is not None:
        s = _bracket(cycle, [(24, 1.0), (12, 0.8), (6, 0.55), (0, 0.25)])
        sub_scores.append(s)
        sources.append(f"认证周期{cycle:.0f}个月")
    elif cert_level:
        s = {"high": 0.95, "medium": 0.65, "low": 0.3}.get(cert_level, 0.5)
        sub_scores.append(s)
        sources.append(f"认证壁垒等级:{cert_level}")
    if top5 is not None:
        s = _bracket(top5, [(60, 0.15), (40, 0.1), (20, 0.0), (0, -0.05)])
        sub_scores.append(s)
        sources.append(f"前五大客户占比{top5:.0f}%")

    if not sub_scores:
        return BarrierDimension(
            key="customer_cert", name="客户认证制", max_score=2,
            score=0.0, confidence=35, source="缺数据",
            missing=True, evidence="认证周期/客户集中度未录入",
        )

    # 子项满分加权：认证周期=1.2（主），客户集中度=0.8（辅助）
    weight_map = {"cycle": 1.2, "cert_level": 1.2, "top5": 0.8}
    # 按出现顺序对应（cycle/cert_level 互斥）
    used_weights = []
    if cycle is not None:
        used_weights.append(1.2)
    elif cert_level:
        used_weights.append(1.2)
    if top5 is not None:
        used_weights.append(0.8)
    total_w = sum(used_weights) if used_weights else 2.0
    weighted = sum(s * w for s, w in zip(sub_scores, used_weights))
    raw = min(1.0, max(0.0, weighted / total_w))
    score = round(raw * 2, 2)
    confidence = 80 if cycle is not None else (60 if top5 is not None else 40)
    return BarrierDimension(
        key="customer_cert", name="客户认证制", max_score=2,
        score=score, confidence=confidence,
        source="赛道情报 + 财报代理", evidence="；".join(sources),
    )


def _score_upstream_bargain(extra: dict) -> BarrierDimension:
    """上游议价能力（2分）- 财报直算，100%自动"""
    gross_margin = extra.get("gross_margin")
    ap_days = extra.get("ap_days")
    top5_supplier = extra.get("top5_supplier")

    subs = []
    sources = []
    if gross_margin is not None:
        subs.append(_bracket(gross_margin, [(50, 0.9), (40, 0.75), (30, 0.6), (20, 0.4), (0, 0.2)]))
        sources.append(f"毛利率{gross_margin:.1f}%")
    if ap_days is not None:
        subs.append(_bracket(ap_days, [(120, 0.7), (60, 0.5), (30, 0.35), (0, 0.2)]))
        sources.append(f"应付账款周转{ap_days:.0f}天")
    if top5_supplier is not None:
        # 供应商越分散 = 越不依赖单一上游 = 议价越强（<40%给满分）
        s = _bracket(top5_supplier, [(40, 0.4), (60, 0.25), (0, 0.1)])
        subs.append(s)
        sources.append(f"前五大供应商占比{top5_supplier:.0f}%")

    if not subs:
        return BarrierDimension(
            key="upstream_bargain", name="上游议价能力", max_score=2,
            score=0.0, confidence=40, source="缺数据",
            missing=True, evidence="无毛利率/应付数据",
        )

    # 三项满分=0.9+0.7+0.4=2.0，直接相加 = 满分，部分缺数据时按已填项归一化（防缺数据拉低）
    weights = []
    if gross_margin is not None:
        weights.append(0.9)
    if ap_days is not None:
        weights.append(0.7)
    if top5_supplier is not None:
        weights.append(0.4)
    total_w = sum(weights) if weights else 2.0
    filled_sum = sum(subs)
    filled_max = sum(weights) if weights else 2.0
    # 按已填子项的满分配比，折算成 2.0 满分体系（缺数据不扣分，只降置信度）
    score = round(min(2.0, filled_sum / filled_max * 2.0), 2) if filled_max > 0 else 0.0
    # 毛利率是核心证据
    confidence = 90 if gross_margin is not None else 50
    # 只有1项数据 → 置信度下调
    if len(subs) == 1:
        confidence = min(confidence, 55)
    return BarrierDimension(
        key="upstream_bargain", name="上游议价能力", max_score=2,
        score=score, confidence=confidence,
        source="财报直算", evidence="；".join(sources),
    )


def _score_market_space(intel: dict) -> BarrierDimension:
    """行业空间/增速（2分）"""
    growth = intel.get("market_growth")
    size = intel.get("market_size_bn")  # 亿元

    subs = []
    sources = []
    if growth is not None:
        # 增速越高越好（新兴赛道弹性大）
        if growth >= 30:
            s = 1.0
        elif growth >= 20:
            s = 0.9
        elif growth >= 12:
            s = 0.75
        elif growth >= 6:
            s = 0.55
        else:
            s = 0.3
        subs.append(s)
        sources.append(f"行业增速{growth:.0f}%")
    if size is not None:
        # 规模=天花板，太小没空间太大没弹性，中间最优
        if 50 <= size <= 500:
            s = 1.0     # 百级规模，弹性+空间平衡
        elif 20 <= size < 50:
            s = 0.7     # 偏小赛道
        elif 500 < size <= 2000:
            s = 0.8     # 大空间但弹性弱
        elif size < 20:
            s = 0.4     # 太细分
        else:
            s = 0.5     # 超大体量
        subs.append(s)
        sources.append(f"行业规模约{size:.0f}亿")

    if not subs:
        return BarrierDimension(
            key="market_space", name="行业空间/增速", max_score=2,
            score=0.0, confidence=30, source="缺数据",
            missing=True, evidence="行业规模/增速未录入（研报）",
        )

    # 子项满分：增速=1.0，规模=1.0，各占一半
    weights = []
    if growth is not None:
        weights.append(1.0)
    if size is not None:
        weights.append(1.0)
    total_w = sum(weights) if weights else 2.0
    weighted_sum = sum(s * w for s, w in zip(subs, weights))
    raw = weighted_sum / total_w
    score = round(raw * 2, 2)
    confidence = 78 if len(subs) == 2 else 55
    return BarrierDimension(
        key="market_space", name="行业空间/增速", max_score=2,
        score=score, confidence=confidence,
        source="赛道情报(研报/招股书)", evidence="；".join(sources),
    )


def _score_competition(intel: dict) -> BarrierDimension:
    """竞争格局（3分）"""
    cr3 = intel.get("cr3")
    players = intel.get("players_count")

    subs = []
    sources = []
    if cr3 is not None:
        subs.append(_bracket(cr3, [(70, 1.0), (50, 0.8), (30, 0.55), (0, 0.3)]))
        sources.append(f"CR3≈{cr3:.0f}%")
    if players is not None and players > 0:
        # 玩家越少 = 格局越好（反向档位）
        if players <= 3:
            s = 1.0
        elif players <= 5:
            s = 0.85
        elif players <= 8:
            s = 0.7
        elif players <= 15:
            s = 0.5
        elif players <= 30:
            s = 0.3
        else:
            s = 0.15
        subs.append(s)
        sources.append(f"主要玩家约{players}家")

    if not subs:
        return BarrierDimension(
            key="competition", name="竞争格局", max_score=3,
            score=0.0, confidence=30, source="缺数据",
            missing=True, evidence="CR3/玩家数量未录入（招股书/研报）",
        )

    # 子项满分各 1.5（CR3=1.5, 玩家数=1.5），合计 3.0
    weights = []
    if cr3 is not None:
        weights.append(1.5)
    if players is not None and players > 0:
        weights.append(1.5)
    total_w = sum(weights) if weights else 3.0
    weighted_sum = sum(s * w for s, w in zip(subs, weights))
    raw = weighted_sum / total_w
    score = round(raw * 3, 2)
    confidence = 80 if len(subs) == 2 else 55
    return BarrierDimension(
        key="competition", name="竞争格局", max_score=3,
        score=score, confidence=confidence,
        source="赛道情报(招股书/研报)", evidence="；".join(sources),
    )


def _score_iteration_speed(extra: dict, intel: dict) -> BarrierDimension:
    """
    产品迭代速度（1分）- 代理指标
    方向注意：迭代「慢」= 技术稳定 = 壁垒高；迭代「快」= 容易被新玩家切入
    """
    rd_ratio = extra.get("rd_ratio")
    rd_growth = extra.get("rd_growth")
    patent_year = intel.get("patent_year")

    sub_scores = []
    sources = []
    if rd_ratio is not None and rd_growth is not None:
        # 高研发投入 + 稳定迭代 = 高壁垒
        if rd_ratio >= 15 and rd_growth < 20:
            sub_scores.append(0.8)
            sources.append(f"研发投入{rd_ratio:.0f}%且增速稳({rd_growth:.0f}%)，技术迭代慢而稳")
        elif rd_ratio >= 15:
            sub_scores.append(0.6)
            sources.append(f"研发投入{rd_ratio:.0f}%但迭代较快({rd_growth:.0f}%)")
        elif rd_ratio >= 5:
            sub_scores.append(0.45)
            sources.append(f"研发投入{rd_ratio:.0f}%，技术迭代中性")
        else:
            sub_scores.append(0.2)
            sources.append(f"研发投入仅{rd_ratio:.0f}%，技术门槛存疑⚠️")
    elif rd_ratio is not None:
        s = _bracket(rd_ratio, [(15, 0.6), (5, 0.45), (0, 0.2)])
        sub_scores.append(s)
        sources.append(f"研发费用率{rd_ratio:.0f}%")
    if patent_year is not None:
        sub_scores.append(0.1 if patent_year > 50 else 0.05)
        sources.append(f"年专利申请{patent_year}项")

    if not sub_scores:
        return BarrierDimension(
            key="iteration_speed", name="产品迭代速度", max_score=1,
            score=0.0, confidence=40, source="缺数据",
            missing=True, evidence="研发/专利数据未录入",
        )

    raw = sum(sub_scores) / len(sub_scores)
    score = round(raw * 1, 2)
    confidence = 60 if (rd_ratio is not None and rd_growth is not None) else 45
    return BarrierDimension(
        key="iteration_speed", name="产品迭代速度", max_score=1,
        score=score, confidence=confidence,
        source="代理指标(财报+情报)", evidence="；".join(sources),
    )


def _score_policy_support(intel: dict) -> BarrierDimension:
    """政策支持（2分）"""
    level = intel.get("policy_level")  # high/medium/low/none
    notes = intel.get("policy_notes", "")

    if not level:
        return BarrierDimension(
            key="policy_support", name="政策支持", max_score=2,
            score=0.0, confidence=40, source="缺数据",
            missing=True, evidence="政策支持未录入",
        )

    s = {"high": 1.0, "medium": 0.65, "low": 0.3, "none": 0.1}.get(level, 0.35)
    return BarrierDimension(
        key="policy_support", name="政策支持", max_score=2,
        score=round(s * 2, 2), confidence=80, source="赛道情报(政策检索)",
        evidence=notes or f"政策等级:{level}",
    )


# ============================================================
# 主入口
# ============================================================

def _intel_has_data(intel: dict) -> bool:
    """判定赛道情报是否有效（至少填了一个关键字段）"""
    if not intel:
        return False
    return any(intel.get(k) not in (None, "")
               for k in ("cr3", "players_count", "global_players",
                         "substitution_rate", "cert_cycle_months", "cert_level",
                         "market_size_bn", "market_growth", "policy_level",
                         "invent_patent_ratio", "patent_year"))


def score_track_barrier(
    extra: dict,
    intel: dict,
    manual_override: Optional[float] = None,
    fallback: Optional[float] = None,
    auto_notes: Optional[List[str]] = None,
) -> BarrierResult:
    """
    计算赛道壁垒自动评分（0-2.5）

    Args:
        extra: 个股财报代理字段 dict:
            rd_ratio      研发费用率(%)
            rd_growth     研发投入增速(%)
            gross_margin  毛利率(%)
            ap_days       应付账款周转天数
            top5_customer 前五大客户占比(%)
            top5_supplier 前五大供应商占比(%)
        intel: 赛道情报 dict（来自 sector_intel.py / 赛道数据库/intel/*.json）:
            cr3, players_count, global_players, substitution_rate,
            substitution_urgency, cert_cycle_months, cert_level,
            market_size_bn, market_growth, policy_level, policy_notes,
            invent_patent_ratio, patent_year, human_reviewed, updated, sources
        manual_override: 人工覆盖分（0-2.5），设置后直接采用（config barrier_manual_override）
        fallback: 兜底分（0-2.5），赛道情报未录入时沿用 config 的人工分，
                  避免"缺情报 → 自动分暴跌 → 池子错乱"
        auto_notes: 附加备注

    Returns:
        BarrierResult
    """
    extra = extra or {}
    intel = intel or {}
    notes = list(auto_notes or [])

    # ---- 1. 人工覆盖优先（config barrier_manual_override）----
    if manual_override is not None:
        s = max(0.0, min(BARRIER_MAX_SCORE, manual_override))
        # 如果也有赛道情报自动分，对比差异
        if _intel_has_data(intel):
            auto_res = _compute_auto(extra, intel, notes)
            if abs(auto_res.score - s) >= 0.3:
                notes.append(f"⚠️ 自动分{auto_res.score:.2f}与人工分{s:.2f}差异≥0.3，建议复核赛道情报")
        result = BarrierResult(
            raw_score=s / BARRIER_MAX_SCORE * BARRIER_MAX_RAW,
            score=round(s, 2),
            overall_confidence=100,
            source="manual_override",
            notes=notes,
            updated=datetime.now().isoformat(timespec="seconds"),
        )
        return result

    # ---- 2. 赛道情报未录入 → 沿用人工兜底分（平滑迁移）----
    if not _intel_has_data(intel):
        s = max(0.0, min(BARRIER_MAX_SCORE, fallback if fallback is not None else 0.0))
        notes.append("🟡 赛道情报未录入，沿用人工兜底分（补齐情报后自动接管）")
        result = BarrierResult(
            raw_score=s / BARRIER_MAX_SCORE * BARRIER_MAX_RAW,
            score=round(s, 2),
            overall_confidence=40,
            source="fallback_manual",
            needs_review=True,
            review_reason="赛道情报未录入",
            notes=notes,
            updated=datetime.now().isoformat(timespec="seconds"),
        )
        return result

    # ---- 3. 自动评分 ----
    return _compute_auto(extra, intel, notes)


def _compute_auto(extra: dict, intel: dict, notes: list) -> BarrierResult:
    """8维自动评分主流程"""
    dims = [
        _score_tech_barrier(extra, intel),
        _score_local_substitution(intel),
        _score_customer_cert(intel, extra),
        _score_upstream_bargain(extra),
        _score_market_space(intel),
        _score_competition(intel),
        _score_iteration_speed(extra, intel),
        _score_policy_support(intel),
    ]

    raw = round(sum(d.score for d in dims), 2)
    track = round(raw / BARRIER_MAX_RAW * BARRIER_MAX_SCORE, 2)

    # 综合置信度：按权重加权
    total_w = sum(BARRIER_WEIGHTS[d.key] for d in dims)
    confidence = int(round(
        sum(d.confidence * BARRIER_WEIGHTS[d.key] for d in dims) / total_w
    ))

    missing = [d.key for d in dims if d.missing]

    # 复核判定（全自动语义）：置信度低或灵魂维度缺数据 = 数据缺口，系统自动标注，
    # 情报由每周自动刷新任务补齐，不要求用户介入
    needs_review = False
    review_reason = ""
    if confidence < 60:
        needs_review = True
        review_reason = f"综合置信度仅{confidence}%"
    low_key = [d for d in dims if d.key in KEY_DIMENSIONS and d.confidence < 50]
    if low_key:
        needs_review = True
        review_reason += ("；" if review_reason else "") + \
            "关键维度缺数据: " + "、".join(d.name for d in low_key)

    # 备注
    if missing:
        notes.append("赛道壁垒缺数据维度: " + "、".join(
            BARRIER_DIM_NAMES[k] for k in missing))
    if needs_review:
        notes.append(f"🟡 赛道壁垒需人工复核（{review_reason}）")
    else:
        notes.append(f"赛道壁垒自动评分(置信度{confidence}%)")

    # 人工复核过赛道情报 → 提升整体可信度
    if intel.get("human_reviewed"):
        confidence = min(95, confidence + 5)

    return BarrierResult(
        raw_score=raw,
        score=track,
        overall_confidence=confidence,
        dimensions=dims,
        missing=missing,
        needs_review=needs_review,
        review_reason=review_reason,
        source="auto",
        notes=notes,
        updated=datetime.now().isoformat(timespec="seconds"),
    )


# ============================================================
# 便捷入口
# ============================================================

def build_extra_from_financial(fin, manual_data: dict = None) -> dict:
    """
    从财务快照 + 手动数据构建 extra 代理字段
    fin: scoring_engine.FinancialSnapshot
    manual_data: data/manual_financial.json 里该股的 dict
    """
    extra = {}
    code = getattr(fin, "code", None)
    if fin.gross_margin is not None:
        extra["gross_margin"] = fin.gross_margin
    if manual_data:
        for k in ("rd_ratio", "rd_growth", "ap_days", "top5_customer", "top5_supplier"):
            if k in manual_data:
                extra[k] = float(manual_data[k])
    return extra


if __name__ == "__main__":
    # 自测：用广立微示例数据跑一遍
    print("🧪 barrier_engine 自测")
    print("=" * 50)
    intel = {
        "cr3": 85, "players_count": 3, "global_players": 3,
        "substitution_rate": 15, "substitution_urgency": "high",
        "cert_cycle_months": 24, "cert_level": "high",
        "market_size_bn": 100, "market_growth": 20,
        "policy_level": "high", "policy_notes": "国家级战略，大基金重点投",
    }
    extra = {"gross_margin": 55.0, "rd_ratio": 20.0, "rd_growth": 15.0}
    r = score_track_barrier(extra, intel)
    print(f"原始分: {r.raw_score}/18 → 赛道壁垒 {r.score}/2.5")
    print(f"置信度: {r.overall_confidence}%  数据缺口: {r.needs_review} ({r.review_reason})")
    for d in r.dimensions:
        flag = "❌缺数据" if d.missing else f"✅{d.confidence}%"
        print(f"  {d.name}({d.max_score}): {d.score} [{flag}] {d.evidence}")
