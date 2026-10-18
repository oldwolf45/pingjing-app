# -*- coding: utf-8 -*-
"""
🍃 紫苏叶策略 V2.0 - 三池动态跟踪管理器
- 候选池 / 观察池 / 储备池 / 移除池
- 每周重新评分，动态调整
- 连续N周触发升降级
"""

import json
import os
import shutil
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict

from scoring_engine import StockScore, calculate_full_score, FinancialSnapshot, ScoreBreakdown
from config import (
    CANDIDATE_POOL, OBSERVATION_POOL, RESERVE_POOL, REMOVED_POOL, TUNA_POOL,
    POOL_THRESHOLDS, UPGRADE_WEEKS, DOWNGRADE_WEEKS, REMOVE_WEEKS,
    POOL_CAPACITY, TUNA_CONFIRM_WEEKS, TUNA_BACK_WEEKS, NEW_ENTRY_NO_PRICE_WEEKS,
    is_buyable,
)
from popularity_engine import judge_back


POOL_NAMES = {
    "candidate": "🏆 候选池",
    "observation": "👀 观察池",
    "reserve": "🔍 储备池",
    "tuna": "🐟 金枪鱼池",
    "removed": "❌ 已移除",
}

# 顺序即"好坏"排序，前面的池子级别更高。
# tuna / removed 都排在最后：金枪鱼是"已长大出池"，移除是"被证伪出池"，
# 两者都不再参与分数升降级，只是出口不同。
POOL_ORDER = ["candidate", "observation", "reserve", "tuna", "removed"]

# 活跃池（占名额的池）
ACTIVE_POOLS = ("candidate", "observation", "reserve")


@dataclass
class TrackedStock:
    """跟踪中的个股"""
    code: str
    name: str
    sector: str
    track_barrier: float
    ideal_buy: str = "-"
    stop_loss: str = "-"
    target: str = "-"
    position_suggest: float = 0.0
    core_logic: str = ""
    upgrade_conditions: Optional[str] = None
    downgrade_warning: Optional[str] = None
    current_pool: str = "reserve"
    # 历史评分（用于连续N周判断）
    score_history: List[dict] = field(default_factory=list)
    # 当前分数
    current_score: Optional[dict] = None
    # 其他元数据
    last_updated: str = ""
    weeks_in_pool: int = 0
    consecutive_weeks_above: int = 0   # 连续高于阈值的周数
    consecutive_weeks_below: int = 0   # 连续低于阈值的周数

    # === 2026-10-17 新增：金枪鱼进出 + 新进池观察期 ===
    entry_week: str = ""               # 进池周（YYYY-MM-DD）
    price_ready: bool = True           # 买入价是否可给；新进池当周为 False
    entry_score: Optional[float] = None  # 进池当周分数（用于"按分数定层"的留痕）
    # 金枪鱼化计数
    tuna_weeks: int = 0                # 连续命中金枪鱼判定的周数
    tuna_reason: str = ""              # hot / big / both
    tuna_hits: List[str] = field(default_factory=list)   # 每周命中的具体项
    tuna_status: str = ""              # 出池后：watching（跟踪待回归）/ archived（归档不再跟）
    exit_date: str = ""                # 出池日期
    exit_snapshot: dict = field(default_factory=dict)    # 出池时的研报/主动资金/市值快照
    # 金枪鱼回归计数
    back_weeks: int = 0
    back_miss: List[str] = field(default_factory=list)   # 最近一次回归还差哪几项
    # 移除原因（业绩崩 / 逻辑证伪 / 分数破线）
    remove_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "sector": self.sector,
            "track_barrier": self.track_barrier,
            "ideal_buy": self.ideal_buy,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "position_suggest": self.position_suggest,
            "core_logic": self.core_logic,
            "upgrade_conditions": self.upgrade_conditions,
            "downgrade_warning": self.downgrade_warning,
            "current_pool": self.current_pool,
            "score_history": self.score_history,
            "current_score": self.current_score,
            "last_updated": self.last_updated,
            "weeks_in_pool": self.weeks_in_pool,
            "consecutive_weeks_above": self.consecutive_weeks_above,
            "consecutive_weeks_below": self.consecutive_weeks_below,
            "entry_week": self.entry_week,
            "price_ready": self.price_ready,
            "entry_score": self.entry_score,
            "tuna_weeks": self.tuna_weeks,
            "tuna_reason": self.tuna_reason,
            "tuna_hits": self.tuna_hits,
            "tuna_status": self.tuna_status,
            "exit_date": self.exit_date,
            "exit_snapshot": self.exit_snapshot,
            "back_weeks": self.back_weeks,
            "back_miss": self.back_miss,
            "remove_reason": self.remove_reason,
        }


@dataclass
class PoolChange:
    """池子变动记录"""
    code: str
    name: str
    from_pool: str
    to_pool: str
    reason: str
    change_date: str = ""

    def __post_init__(self):
        if not self.change_date:
            self.change_date = datetime.now().strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return asdict(self)


class ThreePoolManager:
    """
    三池动态跟踪管理器

    规则：
    - 候选池：≥7.5分
    - 观察池：6.5-7.5分
    - 储备池：5.5-6.5分
    - 移除：<5.5分

    升降级需要连续N周满足条件（防止波动）
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.stocks: Dict[str, TrackedStock] = {}
        self.changes: List[PoolChange] = []
        self.last_update: str = ""
        self._load_initial_data()

    def _load_initial_data(self):
        """从config加载初始数据"""
        def make_stock(info, pool_name):
            return TrackedStock(
                code=info["code"],
                name=info["name"],
                sector=info["sector"],
                track_barrier=info["track_barrier"],
                ideal_buy=info.get("ideal_buy", "-"),
                stop_loss=info.get("stop_loss", "-"),
                target=info.get("target", "-"),
                position_suggest=info.get("position_suggest", 0),
                core_logic=info.get("core_logic", ""),
                upgrade_conditions=info.get("upgrade_conditions"),
                downgrade_warning=info.get("downgrade_warning"),
                current_pool=pool_name,
                weeks_in_pool=1,
            )

        for info in CANDIDATE_POOL:
            if is_buyable(info["code"]):
                self.stocks[info["code"]] = make_stock(info, "candidate")

        for info in OBSERVATION_POOL:
            if is_buyable(info["code"]):
                self.stocks[info["code"]] = make_stock(info, "observation")

        for info in RESERVE_POOL:
            if is_buyable(info["code"]):
                self.stocks[info["code"]] = make_stock(info, "reserve")

    def load_from_file(self, path: str):
        """从JSON文件加载跟踪数据"""
        if not os.path.exists(path):
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.stocks = {}
            for code, info in data.get("stocks", {}).items():
                ts = TrackedStock(
                    code=info["code"],
                    name=info["name"],
                    sector=info["sector"],
                    track_barrier=info.get("track_barrier", 0),
                    ideal_buy=info.get("ideal_buy", "-"),
                    stop_loss=info.get("stop_loss", "-"),
                    target=info.get("target", "-"),
                    position_suggest=info.get("position_suggest", 0),
                    core_logic=info.get("core_logic", ""),
                    upgrade_conditions=info.get("upgrade_conditions"),
                    downgrade_warning=info.get("downgrade_warning"),
                    current_pool=info.get("current_pool", "reserve"),
                    score_history=info.get("score_history", []),
                    current_score=info.get("current_score"),
                    last_updated=info.get("last_updated", ""),
                    weeks_in_pool=info.get("weeks_in_pool", 1),
                    consecutive_weeks_above=info.get("consecutive_weeks_above", 0),
                    consecutive_weeks_below=info.get("consecutive_weeks_below", 0),
                    entry_week=info.get("entry_week", ""),
                    price_ready=info.get("price_ready", True),
                    entry_score=info.get("entry_score"),
                    tuna_weeks=info.get("tuna_weeks", 0),
                    tuna_reason=info.get("tuna_reason", ""),
                    tuna_hits=info.get("tuna_hits", []) or [],
                    tuna_status=info.get("tuna_status", ""),
                    exit_date=info.get("exit_date", ""),
                    exit_snapshot=info.get("exit_snapshot", {}) or {},
                    back_weeks=info.get("back_weeks", 0),
                    back_miss=info.get("back_miss", []) or [],
                    remove_reason=info.get("remove_reason", ""),
                )
                self.stocks[code] = ts

            self.last_update = data.get("last_update", "")
            self.changes = [PoolChange(**c) for c in data.get("changes", [])]
            return True
        except Exception as e:
            print(f"加载跟踪数据失败: {e}")
            return False

    def save_to_file(self, path: str):
        """保存到JSON文件"""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        data = {
            "last_update": datetime.now().isoformat(),
            "stocks": {code: ts.to_dict() for code, ts in self.stocks.items()},
            "changes": [c.to_dict() for c in self.changes[-50:]],  # 保留最近50条
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def update_score(self, code: str, stock_score: StockScore, tuna_info: dict = None):
        """
        更新一只股票的评分，并检查是否需要调池

        tuna_info（2026-10-17 新增，来自 popularity_engine.evaluate）
        ---------------------------------------------------------
        {
          'tuna_flag': bool,
          'tuna_reason': 'hot' | 'big' | 'both' | None,
          'tuna_reasons': ['研报25篇>20', ...],     # 人话命中项
          'details': {'reports_12m': 25, 'active_ratio_pct': 9.1, 'total_mv_yi': 320}
        }
        传 None 表示本次没跑冷门度检查（如 quick 模式），金枪鱼计数保持不动。
        """
        if code not in self.stocks:
            return

        ts = self.stocks[code]
        score_dict = stock_score.to_dict()
        score_dict["date"] = datetime.now().strftime("%Y-%m-%d")
        total = stock_score.score.total()

        # 更新当前分数
        ts.current_score = score_dict
        ts.last_updated = datetime.now().isoformat()

        # 判断今天是否已评分过（必须在写入之前判断，否则覆盖后无从比较）
        # 用 date 集合判断，不依赖 history 长度——覆盖模式下长度恒为1会误判
        same_day = any(x.get("date") == score_dict["date"] for x in ts.score_history)

        # 添加到历史（同日重跑覆盖，不追加）
        # 历史踩坑：无条件 append 导致同日多次运行产生大量重复条目
        # （2026-09-25 实测 170 条中 146 条为同日重复，86%），
        # 使 score_history 失去时间序列意义，无法做"评分→收益"验证。
        if ts.score_history and ts.score_history[-1].get("date") == score_dict["date"]:
            ts.score_history[-1] = score_dict          # 同日：覆盖最后一条
        else:
            ts.score_history.append(score_dict)        # 新的交易日：追加
        if len(ts.score_history) > 12:  # 保留最近12周
            ts.score_history = ts.score_history[-12:]

        # 检查池子变动
        # is_new_day=False：同日重跑只更新分数，不递增周计数
        # （否则一天跑14次 = 在池14周）
        self._check_pool_change(ts, total, is_new_day=not same_day, tuna_info=tuna_info)

        # 新进池观察期到期：当周不给买入价，满 NEW_ENTRY_NO_PRICE_WEEKS 周后放行
        if (not same_day and not ts.price_ready
                and ts.weeks_in_pool > NEW_ENTRY_NO_PRICE_WEEKS):
            ts.price_ready = True
            print(f"  💰 {ts.name}({ts.code}) 新进池观察期满，买入价可给")

    def _check_pool_change(self, ts: TrackedStock, total_score: float,
                           is_new_day: bool = True, tuna_info: dict = None):
        """检查是否需要调整池子

        is_new_day=False 表示同一天的重复评分（重跑），此时只更新分数，
        不递增 weeks_in_pool / consecutive_weeks_* 计数。
        历史踩坑：原实现按"调用次数"计数，同一天跑 14 次就被记成"在池14周"，
        导致升降级判定（连续N周）建立在假计数上。

        2026-10-17 新增：金枪鱼闸门
        ----------------------------
        金枪鱼判定优先于分数升降级。原因：一只票"变大变热"之后，冷门度分会掉、
        金枪鱼扣分会加，它会在分数体系里被推向移除池——那是错的出口。
        它的正确出口是金枪鱼池（继续跟踪，满足三条可回归）。
        """
        current = ts.current_pool

        # 同日重跑：分数已更新，但绝不推进任何周计数，直接返回。
        # 必须放在"首次评分"分支之前——否则只有1条历史的标的会每次都被当成首次评分。
        if not is_new_day:
            return

        # ===== 闸门一：已在金枪鱼池 → 只走回归判定，不参与分数升降级 =====
        if current == "tuna":
            self._check_tuna_back(ts, tuna_info)
            return

        # ===== 闸门二：命中金枪鱼判定 → 走金枪鱼出口 =====
        if self._tuna_gate(ts, tuna_info):
            return

        # 没命中 → 计数归零（金枪鱼判定必须连续）
        if ts.tuna_weeks:
            ts.tuna_weeks = 0
            ts.tuna_hits = []

        # 首次评分（没有历史）：只设置初始池子，不计算连续周数
        if len(ts.score_history) <= 1:
            # 确认初始池子是否正确（阈值从 POOL_THRESHOLDS 读，避免与 config 不一致）
            cand = POOL_THRESHOLDS["candidate"]
            obs = POOL_THRESHOLDS["observation"]
            res = POOL_THRESHOLDS["reserve"]
            if total_score >= cand and current != "candidate":
                ts.current_pool = "candidate"
            elif obs <= total_score < cand and current != "observation":
                ts.current_pool = "observation"
            elif res <= total_score < obs and current != "reserve":
                ts.current_pool = "reserve"
            elif total_score < res and current != "removed":
                # 首次就低于储备线，也先不直接移除，给观察期
                pass
            ts.weeks_in_pool += 1
            return

        # 判断目标池子
        if total_score >= POOL_THRESHOLDS["candidate"]:
            target = "candidate"
        elif total_score >= POOL_THRESHOLDS["observation"]:
            target = "observation"
        elif total_score >= POOL_THRESHOLDS["reserve"]:
            target = "reserve"
        else:
            target = "removed"

        if target == current:
            # 在当前池子，重置相反方向的计数
            if POOL_ORDER.index(target) < POOL_ORDER.index(current):
                ts.consecutive_weeks_above = 0
            elif POOL_ORDER.index(target) > POOL_ORDER.index(current):
                ts.consecutive_weeks_below = 0
            ts.weeks_in_pool += 1
            return

        # 目标池子不同，检查连续周数
        target_idx = POOL_ORDER.index(target)
        current_idx = POOL_ORDER.index(current)

        if target_idx < current_idx:
            # 升级方向
            ts.consecutive_weeks_above += 1
            ts.consecutive_weeks_below = 0

            # 需要连续N周才能升级
            if ts.consecutive_weeks_above >= UPGRADE_WEEKS:
                self._move_pool(ts, current, target, f"连续{UPGRADE_WEEKS}周评分≥{POOL_THRESHOLDS.get(target, 0)}分")
            else:
                ts.weeks_in_pool += 1
        else:
            # 降级方向
            ts.consecutive_weeks_below += 1
            ts.consecutive_weeks_above = 0

            # 降级阈值
            threshold_weeks = DOWNGRADE_WEEKS if target != "removed" else REMOVE_WEEKS

            if ts.consecutive_weeks_below >= threshold_weeks:
                reason = f"连续{threshold_weeks}周评分<{POOL_THRESHOLDS.get(current, 0)}分"
                if target == "removed":
                    ts.remove_reason = reason
                self._move_pool(ts, current, target, reason)
            else:
                ts.weeks_in_pool += 1

    # ------------------------------------------------------------------
    # 🐟 金枪鱼进出（2026-10-17 新增）
    # ------------------------------------------------------------------
    def _tuna_gate(self, ts: TrackedStock, tuna_info: dict) -> bool:
        """命中金枪鱼判定 → 记一周并攒够确认周数就出池；返回 True 表示已处理

        抽成独立方法是为了能单独调用（`mark_tuna_week`）：
        周中做一次体检、想把这一周记进去时，不必伪造一个分数对象走完整流程。
        """
        if not (tuna_info and tuna_info.get("tuna_flag")):
            return False

        # 首周不计金枪鱼周数（PRD A.7 / A.9 ⓿）：
        # 进池当周没有买入价、还没建仓，不该因为"变热"被清出去。
        # 这个判断放在这里而不是调用方，是因为有两条路径会走到金枪鱼计数
        # （update_score 的闸门二、mark_tuna_week），放这里才能一次覆盖。
        if not getattr(ts, "price_ready", True):
            print(f"  ⏸ {ts.name}({ts.code}) 进池当周不计金枪鱼周数（当周无买入价）")
            return False

        current = ts.current_pool
        ts.tuna_weeks += 1
        ts.tuna_reason = tuna_info.get("tuna_reason") or ""
        hits = tuna_info.get("tuna_reasons") or []
        today = datetime.now().strftime("%Y-%m-%d")
        if hits:
            ts.tuna_hits.append(f"{today}: " + "、".join(hits))
            ts.tuna_hits = ts.tuna_hits[-8:]

        if ts.tuna_weeks >= TUNA_CONFIRM_WEEKS:
            reason = "连续%d周%s（%s）" % (
                TUNA_CONFIRM_WEEKS,
                {"hot": "变热", "big": "变大", "both": "变大且变热"}.get(
                    ts.tuna_reason, "金枪鱼化"),
                "、".join(hits) if hits else ts.tuna_reason)
            self._move_pool(ts, current, "tuna", reason, tuna_info=tuna_info)
        else:
            ts.weeks_in_pool += 1
            print(f"  🐟 {ts.name}({ts.code}) 命中金枪鱼判定第 {ts.tuna_weeks} 周"
                  f"（{ts.tuna_reason}｜{'、'.join(hits) if hits else '-'}），"
                  f"还需 {TUNA_CONFIRM_WEEKS - ts.tuna_weeks} 周确认")
        return True

    def mark_tuna_week(self, code: str, tuna_info: dict) -> bool:
        """只推进金枪鱼计数，不碰分数、不碰其他池判定

        用途：周中单独跑一次体检，把这一周的金枪鱼判定记进状态里，
        不必等下一次完整评分。已在金枪鱼池的票调用本方法无意义，直接返回 False。
        """
        ts = self.stocks.get(code)
        if not ts or ts.current_pool == "tuna":
            return False
        if ts.current_pool == "removed":
            return False
        return self._tuna_gate(ts, tuna_info)

    def _check_tuna_back(self, ts: TrackedStock, tuna_info: dict = None):
        """金枪鱼池的回归判定：三条同时满足，连续 4 周

        回归线比出池线更严（滞回区间）：
            出池：市值>250亿 或 研报>20篇 或 主动资金>8%
            回归：市值<200亿 且 研报≤10篇 且 主动资金≤3%
        中间那段区间谁也不动，避免在边界上反复横跳。

        tuna_info 为 None（本次没拉冷门度数据）时，不算断档也不算满足，
        保持原计数不变。
        """
        if not tuna_info:
            ts.weeks_in_pool += 1
            return

        d = tuna_info.get("details") or {}
        ok, miss = judge_back(d.get("reports_12m"),
                              d.get("active_ratio_pct"),
                              d.get("total_mv_yi"))
        ts.back_miss = miss
        if ok:
            ts.back_weeks += 1
            print(f"  🐟 {ts.name}({ts.code}) 满足回归条件 "
                  f"({ts.back_weeks}/{TUNA_BACK_WEEKS} 周)")
            if ts.back_weeks >= TUNA_BACK_WEEKS:
                self._move_pool(ts, "tuna", "reserve",
                                f"连续{TUNA_BACK_WEEKS}周满足回归条件"
                                f"（市值<200亿/研报≤10篇/主动资金≤3%）")
        else:
            if ts.back_weeks:
                print(f"  🐟 {ts.name}({ts.code}) 回归中断，差：{'、'.join(miss)}")
            ts.back_weeks = 0
            ts.weeks_in_pool += 1

    def _move_pool(self, ts: TrackedStock, from_pool: str, to_pool: str,
                   reason: str, tuna_info: dict = None):
        """移动池子"""
        ts.current_pool = to_pool
        ts.weeks_in_pool = 1
        ts.consecutive_weeks_above = 0
        ts.consecutive_weeks_below = 0

        # 出池到金枪鱼池：固化出池时点快照，后面做归回判定/复盘都要用
        if to_pool == "tuna":
            d = (tuna_info or {}).get("details") or {}
            ts.exit_date = datetime.now().strftime("%Y-%m-%d")
            ts.tuna_status = "watching"
            ts.back_weeks = 0
            ts.exit_snapshot = {
                "reports_12m": d.get("reports_12m"),
                "active_ratio_pct": d.get("active_ratio_pct"),
                "total_mv_yi": d.get("total_mv_yi"),
                "n_research": d.get("n_research"),
                "hold_report_date": d.get("hold_report_date"),
                "reason": (tuna_info or {}).get("tuna_reason"),
            }
        # 从金枪鱼池回归：清掉金枪鱼计数，重新按储备池走
        elif from_pool == "tuna":
            ts.tuna_status = ""
            ts.back_weeks = 0
            ts.back_miss = []
            ts.tuna_weeks = 0

        change = PoolChange(
            code=ts.code, name=ts.name,
            from_pool=from_pool, to_pool=to_pool,
            reason=reason,
        )
        self.changes.append(change)
        print(f"  📢 {ts.name}({ts.code}): {POOL_NAMES[from_pool]} → {POOL_NAMES[to_pool]} ({reason})")

    def add_stock(self, stock_info: dict, pool: str = "reserve",
                  price_ready: bool = None):
        """添加新股票到跟踪池

        2026-10-17：新进池默认 price_ready=False（当周不给买入价），
        次周数据确认后才置 True。传 price_ready=True 可显式跳过观察期
        （用于人工补录的历史票）。
        """
        code = stock_info["code"]
        if not is_buyable(code):
            print(f"  ⚠️ {code} 是科创板，跳过")
            return False

        if code in self.stocks:
            ts0 = self.stocks[code]
            # 已在移除池/金枪鱼池的票被重新补进来：复活，按新票处理
            if ts0.current_pool in ("removed", "tuna"):
                old_pool = ts0.current_pool
                ts0.current_pool = pool
                ts0.weeks_in_pool = 1
                ts0.entry_week = datetime.now().strftime("%Y-%m-%d")
                ts0.price_ready = bool(NEW_ENTRY_NO_PRICE_WEEKS == 0) if price_ready is None else price_ready
                ts0.remove_reason = ""
                ts0.consecutive_weeks_above = 0
                ts0.consecutive_weeks_below = 0
                ts0.back_weeks = 0
                self.changes.append(PoolChange(
                    code=code, name=ts0.name, from_pool=old_pool, to_pool=pool,
                    reason="复活补入",
                ))
                print(f"  ♻️ {ts0.name}({code}) 从"
                      f"{POOL_NAMES.get(old_pool, old_pool)}复活到 {POOL_NAMES[pool]}")
                return True
            print(f"  ℹ️ {stock_info.get('name', code)} 已在跟踪中")
            return False

        ts = TrackedStock(
            code=code,
            name=stock_info.get("name", code),
            sector=stock_info.get("sector", ""),
            track_barrier=stock_info.get("track_barrier", 1.0),
            ideal_buy=stock_info.get("ideal_buy", "-"),
            stop_loss=stock_info.get("stop_loss", "-"),
            target=stock_info.get("target", "-"),
            position_suggest=stock_info.get("position_suggest", 0),
            core_logic=stock_info.get("core_logic", ""),
            upgrade_conditions=stock_info.get("upgrade_conditions"),
            downgrade_warning=stock_info.get("downgrade_warning"),
            current_pool=pool,
            weeks_in_pool=1,
            entry_week=datetime.now().strftime("%Y-%m-%d"),
            entry_score=stock_info.get("entry_score"),
            price_ready=(NEW_ENTRY_NO_PRICE_WEEKS == 0) if price_ready is None else price_ready,
        )
        self.stocks[code] = ts
        tag = "" if ts.price_ready else "（当周不给买入价）"
        # 补入事件必须进变动记录，否则首页「最近池子变动」时间线看不到📥补入
        self.changes.append(PoolChange(
            code=code, name=ts.name, from_pool="new", to_pool=pool,
            reason="补票进池" + ("" if ts.price_ready else "（当周不给买入价）"),
        ))
        print(f"  ✅ 新增 {ts.name}({code}) 到 {POOL_NAMES[pool]}{tag}")
        return True

    # ------------------------------------------------------------------
    # 📐 定额制：名额统计 + 补票
    # ------------------------------------------------------------------
    def active_count(self) -> int:
        """当前占名额的票数（候选+观察+储备）"""
        return sum(1 for ts in self.stocks.values() if ts.current_pool in ACTIVE_POOLS)

    def slots_left(self, capacity: int = None) -> int:
        """还空着几个名额"""
        cap = POOL_CAPACITY if capacity is None else capacity
        return max(0, cap - self.active_count())

    def refill(self, candidates: List[dict], capacity: int = None,
               dry_run: bool = False) -> dict:
        """定额补齐：出几只补几只，不够格就空着

        candidates 由调用方先筛好（情报已录入、财务已拉全），并按优先级排好序。
        这里只负责「按名额取前 N 个」，不再做资格判断——资格判断放到
        pool_rotation.build_candidates()，因为那需要读 sector_map / 情报文件，
        不适合塞进 pool_manager。

        返回：{'added': [...], 'skipped': [...], 'slots_left': n}
        """
        cap = POOL_CAPACITY if capacity is None else capacity
        need = max(0, cap - self.active_count())
        added, skipped = [], []

        if need == 0:
            return {"added": [], "skipped": [], "slots_left": 0}

        for c in candidates:
            if len(added) >= need:
                break
            code = c.get("code")
            if not code or code in self.stocks:
                skipped.append((code, "已在池/无代码"))
                continue
            if not is_buyable(code):
                skipped.append((code, "科创板不可买"))
                continue
            if not dry_run:
                if not self.add_stock(c, pool=c.get("pool", "reserve")):
                    skipped.append((code, "add_stock 拒绝"))
                    continue
            added.append(c)

        left = need - len(added)
        print(f"\n  📐 定额补齐：名额 {cap}｜在池 {self.active_count()}｜"
              f"缺口 {need}｜补进 {len(added)}｜空着 {left}")
        return {"added": added, "skipped": skipped, "slots_left": left}

    def get_pool_stocks(self, pool: str) -> List[TrackedStock]:
        """获取某个池子的所有股票，按评分降序"""
        stocks = [ts for ts in self.stocks.values() if ts.current_pool == pool]
        stocks.sort(key=lambda x: (
            x.current_score.get("total_score", 0) if x.current_score else 0
        ), reverse=True)
        return stocks

    def get_all_stocks(self) -> List[TrackedStock]:
        """获取所有跟踪股票，按池子+评分排序"""
        result = []
        for pool in POOL_ORDER:
            result.extend(self.get_pool_stocks(pool))
        return result

    def get_recent_changes(self, limit: int = 10) -> List[PoolChange]:
        """获取最近的池子变动"""
        return self.changes[-limit:][::-1]
