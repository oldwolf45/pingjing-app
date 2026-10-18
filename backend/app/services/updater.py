"""7 阶段更新编排（DEV_PLAN T4.3）。

阶段顺序钉死，不许调换（与 PRD 第七章一致）：

    1 行情   quote       行情 + 日K（5 日涨跌、20 日板块温度代理）
    2 财务   finance     财务快照（东财真值优先，手工录入只补空缺）
    3 冷门度 popularity  冷门度 + 金枪鱼判定
    4 壁垒   barrier     8 维壁垒评分（情报缺失时沿用上次的兜底分）
    5 评分   score       6 维评分（含金枪鱼惩罚）
    6 池子   pool        池子判定（判定顺序在 pool_rules.decide 里）
    7 落库   persist     评分快照 / 壁垒快照 / 池子状态 / 运行记录 / 审计

**为什么前 6 个阶段一次写都不做**：取消要「在阶段边界生效，不留半截数据」。
评分快照是只追加表，写一半再取消就没法回退。所以全部算在内存里，第 7 步一个事务写完。

**取消的实际语义**：阶段入口检查取消；阶段内部每只票之间也检查（36 只票一批
要跑几十秒，只在阶段边界响应太迟钝）。但无论在哪中断，只要没进第 7 步，
库里就一条业务数据都没多——对外表现仍然是「阶段边界生效」。

**同一交易日不重复计周**：`pool_states.last_judged_at` 等于本次 as_of_date 就跳过判定。
池子按周计，一天点两次「更新」不该算两周。这条也让「连续两次更新结果可复现」成立：
第二次分数一模一样，池子一动不动。

**单只票失败不拖垮整轮**：财务全空的票不评分（记 fail），其余照跑，整体状态报 partial。
不编 0 分顶上——0 分会把票直接推进已移除，等于用故障替用户做了决定。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from app import legacy_bridge
from app.db import session
from app.services import intel, pool_rules, scoring
from app.services.sources import SourceResult, change_pct, tuna_info_from_popularity

# ---------------------------------------------------------------- 阶段定义

STAGES: tuple[tuple[str, str], ...] = (
    ("quote", "行情与日K"),
    ("finance", "财务"),
    ("popularity", "冷门度"),
    ("barrier", "壁垒"),
    ("score", "评分"),
    ("pool", "池子判定"),
    ("persist", "落库"),
)
STAGE_KEYS = tuple(k for k, _ in STAGES)
STAGE_LABELS = dict(STAGES)
STAGE_COUNT = len(STAGES)

# 财务快照里可以由「手工录入」补的字段（与 FinancialSnapshot 对齐）
FIN_FIELDS = (
    "revenue_yoy", "net_profit_yoy", "deduct_profit_yoy", "order_growth",
    "gross_margin", "gross_margin_trend", "roe", "net_margin",
    "ocf_to_ni", "deduct_profit_abs", "eps_deduct", "total_mv",
)
# 判「有没有财务数据」只看这六项：全是 None 说明一条真数都没拿到
FIN_CORE = ("revenue_yoy", "net_profit_yoy", "deduct_profit_yoy",
            "gross_margin", "roe", "ocf_to_ni")

SETTING_BARRIER_OVERRIDE = "barrier.override.{code}"
SETTING_FIN_TREND = "fin_trend.{code}"

_POOL_RANK = {p: i for i, p in enumerate(pool_rules.POOL_ORDER)}


class Cancelled(Exception):
    """跑批被取消。内部信号，不外抛。"""


# ---------------------------------------------------------------- 数据结构

@dataclass
class UpdateOptions:
    as_of_date: str
    kind: str = "manual"                 # weekly / manual / migration
    codes: Sequence[str] | None = None   # None = 全部在池标的
    include_removed: bool = False
    refresh_finance: bool = False        # 忽略当日缓存重新抓
    refresh_popularity: bool = False
    kline_count: int = 30                # 要算 20 日涨跌，至少 21 根


@dataclass
class Progress:
    """一条进度。UI 用它画「第 3/7 阶段 冷门度 12/36」。"""

    stage: str
    label: str
    index: int          # 1-based
    stage_count: int
    current: int
    total: int
    code: str | None = None
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "stage": self.stage, "label": self.label, "index": self.index,
            "stageCount": self.stage_count, "current": self.current,
            "total": self.total, "code": self.code, "message": self.message,
        }


@dataclass
class StageReport:
    key: str
    label: str
    index: int
    total: int
    done: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = ""

    @property
    def key_label(self) -> str:
        return f"{self.key}:{self.done}/{self.total}"

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "index": self.index,
            "total": self.total, "done": self.done,
            "failed": self.failed, "skipped": self.skipped,
            "message": self.message,
        }


@dataclass
class StockWork:
    """一只票在整轮更新中的中间结果。前 6 阶段往里填，第 7 阶段落库。"""

    code: str
    name: str
    sector: str = ""
    pool: str = "reserve"

    quote: dict | None = None
    total_mv: float | None = None
    chg_5d: float | None = None
    chg_20d: float | None = None

    fin_raw: dict | None = None
    manual_fin: dict = field(default_factory=dict)
    financial: Any = None

    popularity: dict | None = None
    tuna_info: dict | None = None

    barrier: Any = None
    score_result: Any = None
    scores: dict | None = None
    decision: Any = None

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    snapshot_id: int | None = None
    barrier_snapshot_id: int | None = None

    @property
    def scored(self) -> bool:
        return self.score_result is not None

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "sector": self.sector,
            "pool": self.pool,
            "totalMvYi": self.total_mv,
            "price": (self.quote or {}).get("price"),
            "chg5d": self.chg_5d, "chg20d": self.chg_20d,
            "scores": self.scores,
            "barrier": getattr(self.barrier, "score", None),
            "tunaFlag": bool((self.tuna_info or {}).get("tuna_flag")),
            "decision": None if self.decision is None else {
                "fromPool": self.decision.from_pool,
                "toPool": self.decision.to_pool,
                "kind": self.decision.kind,
                "reason": self.decision.reason,
            },
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "snapshotId": self.snapshot_id,
        }


@dataclass
class UpdateResult:
    run_id: str
    as_of_date: str
    status: str = "running"    # running / ok / partial / failed / cancelled
    total: int = 0
    ok_count: int = 0
    fail_count: int = 0
    stages: list[StageReport] = field(default_factory=list)
    works: list[StockWork] = field(default_factory=list)
    cancelled_at: str | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    @property
    def moved(self) -> list[StockWork]:
        return [w for w in self.works if w.decision is not None and w.decision.moved]

    def as_dict(self, with_works: bool = True) -> dict:
        out = {
            "runId": self.run_id, "asOfDate": self.as_of_date,
            "status": self.status, "total": self.total,
            "okCount": self.ok_count, "failCount": self.fail_count,
            "startedAt": self.started_at, "finishedAt": self.finished_at,
            "cancelledAt": self.cancelled_at, "error": self.error,
            "stages": [s.as_dict() for s in self.stages],
            "movedCount": len(self.moved),
        }
        if with_works:
            out["works"] = [w.as_dict() for w in self.works]
        return out

    def summary(self) -> str:
        if self.status == "cancelled":
            return f"已取消（{STAGE_LABELS.get(self.cancelled_at or '', self.cancelled_at or '')}）"
        if self.status == "failed":
            return f"更新失败：{self.error or '未知错误'}"
        msg = f"完成 {self.ok_count}/{self.total}"
        if self.fail_count:
            msg += f"，失败 {self.fail_count}"
        if self.moved:
            msg += f"，池子变动 {len(self.moved)} 只"
        return msg


def sector_heat_proxy(chg_by_sector: dict[str, Sequence[float | None]]
                      ) -> tuple[dict[str, float], float | None]:
    """赛道温度代理：同赛道 20 日涨幅均值。

    原算法靠「板块搜索热度」给热度维度打分，那个接口早就取不到了。
    退一步用同赛道标的的真实 20 日涨幅均值——是客观价格数据，不是编的。
    赛道里只有 1 只票时样本太薄，回落到整池均值（第二个返回值）。
    """
    per: dict[str, list[float]] = {}
    allv: list[float] = []
    for sector, values in (chg_by_sector or {}).items():
        for v in values:
            if v is None:
                continue
            per.setdefault(sector, []).append(float(v))
            allv.append(float(v))
    proxy = {s: round(sum(v) / len(v), 2) for s, v in per.items() if len(v) >= 2}
    pool_mean = round(sum(allv) / len(allv), 2) if allv else None
    return proxy, pool_mean


# ---------------------------------------------------------------- 编排器

class Updater:
    """一轮更新的执行器。sources 是鸭子类型，测试里换 FakeSources 就完全离线。"""

    def __init__(self, conn: sqlite3.Connection, sources: Any, *,
                 options: UpdateOptions,
                 progress: Callable[[Progress], None] | None = None,
                 cancel: Any = None) -> None:
        self.conn = conn
        self.sources = sources
        self.opts = options
        self._progress = progress
        self._cancel = cancel
        self._manual_cache: dict[str, dict] = {}
        self._intel_cache: dict[str, dict | None] = {}

    # ---------------- 对外入口 ----------------

    def run(self) -> UpdateResult:
        as_of = self.opts.as_of_date
        res = UpdateResult(run_id=self._new_run_id(as_of), as_of_date=as_of,
                           started_at=_now_iso())
        self._open_run_row(res.run_id)
        try:
            res.works = self._load_targets()
            res.total = len(res.works)
            for index, (key, label) in enumerate(STAGES, 1):
                self._raise_if_cancelled(key, res)
                report = StageReport(key=key, label=label, index=index, total=res.total)
                res.stages.append(report)
                self._emit(key, 0, res.total, message=f"开始{label}")
                getattr(self, f"_stage_{key}")(res, report)
                self._emit(key, report.done, report.total, message=report.message or f"{label}完成")
        except Cancelled:
            res.status = "cancelled"
        except Exception as exc:                      # 编排层意外：不吞，记进结果
            res.status = "failed"
            res.error = f"{type(exc).__name__}: {exc}"
        finally:
            res.finished_at = _now_iso()
            res.ok_count = sum(1 for w in res.works if w.scored)
            res.fail_count = res.total - res.ok_count
            if res.status == "running":
                if res.total == 0:
                    res.status = "ok"
                elif res.ok_count == 0:
                    res.status = "failed"
                    res.error = res.error or "全部标的都没跑出评分"
                elif res.fail_count:
                    res.status = "partial"
                else:
                    res.status = "ok"
            self._close_run_row(res)
        return res

    # ---------------- 进度与取消 ----------------

    def _emit(self, stage: str, current: int, total: int,
              code: str | None = None, message: str = "") -> None:
        if self._progress is None:
            return
        try:
            self._progress(Progress(
                stage=stage, label=STAGE_LABELS.get(stage, stage),
                index=STAGE_KEYS.index(stage) + 1, stage_count=STAGE_COUNT,
                current=current, total=total, code=code, message=message,
            ))
        except Exception:
            # 进度回调是 UI 的事，画错了不该让整轮更新挂掉
            pass

    def _cancelled(self) -> bool:
        return bool(self._cancel is not None and self._cancel.is_set())

    def _raise_if_cancelled(self, stage: str, res: UpdateResult) -> None:
        if self._cancelled():
            res.cancelled_at = stage
            raise Cancelled()

    # ---------------- 物色标的 ----------------

    def _load_targets(self) -> list[StockWork]:
        rows = self.conn.execute(
            """SELECT s.code, s.name, COALESCE(p.sector, '') AS sector,
                      COALESCE(ps.pool, 'reserve') AS pool
                 FROM stocks s
                 LEFT JOIN stock_profiles p ON p.code = s.code
                 LEFT JOIN pool_states ps ON ps.code = s.code
                WHERE s.status <> 'delisted'"""
        ).fetchall()
        wanted = set(self.opts.codes) if self.opts.codes else None
        out: list[StockWork] = []
        for r in rows:
            if wanted is not None and r["code"] not in wanted:
                continue
            if not self.opts.include_removed and r["pool"] == "removed":
                continue
            out.append(StockWork(code=r["code"], name=r["name"],
                                 sector=r["sector"], pool=r["pool"]))
        out.sort(key=lambda w: (_POOL_RANK.get(w.pool, 99), w.code))
        return out

    # ---------------- 1 行情 ----------------

    def _stage_quote(self, res: UpdateResult, rep: StageReport) -> None:
        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("quote", res)

            q = _safe(lambda: self.sources.quote(w.code))
            if q is not None and q.ok and isinstance(q.data, dict):
                w.quote = q.data
                w.total_mv = q.data.get("total_mv_yi")
                if w.name == w.code and q.data.get("name"):
                    w.name = q.data["name"]
            else:
                w.warnings.append(f"行情获取失败：{_err(q)}")

            k = _safe(lambda: self.sources.kline(w.code, count=self.opts.kline_count))
            if k is not None and k.ok and k.data:
                w.chg_5d = change_pct(k.data, 5)
                w.chg_20d = change_pct(k.data, 20)
                if w.chg_5d is None:
                    w.warnings.append("日K不足 2 根，5 日涨跌留空")
            else:
                w.warnings.append(f"日K获取失败：{_err(k)}")

            rep.done += 1
            if w.quote is None:
                rep.failed += 1
            self._emit("quote", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) 行情")

    # ---------------- 2 财务 ----------------

    def _stage_finance(self, res: UpdateResult, rep: StageReport) -> None:
        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("finance", res)

            f = _safe(lambda: self.sources.finance(
                w.code, refresh=self.opts.refresh_finance))
            if f is not None and f.ok and isinstance(f.data, dict):
                w.fin_raw = f.data
            else:
                w.warnings.append(f"财务接口失败：{_err(f)}")
                w.fin_raw = {}

            w.manual_fin = self._manual_financial(w.code)
            fin_dict = self._merge_financial(w)

            if all(fin_dict.get(k) is None for k in FIN_CORE):
                w.errors.append("财务数据全缺（真值没拿到、手工也没录），跳过评分")
                rep.failed += 1
            else:
                w.financial = scoring.make_financial(**fin_dict)
                rep.done += 1

            self._emit("finance", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) 财务")

    def _merge_financial(self, w: StockWork) -> dict:
        """真值优先，手工只补空缺。

        手工数据大量过期（比如广立微净利同比手工 572%，真值只有 121.7%），
        拿手工盖真值等于用旧记忆否定刚查到的数。所以反过来：真值先占位，
        手工只填真值没有的字段（订单增速、扣非绝对值这些接口拿不到）。
        """
        live = w.fin_raw or {}
        out: dict[str, Any] = {}

        out["revenue_yoy"] = live.get("revenue_yoy")
        out["net_profit_yoy"] = live.get("net_profit_yoy")
        out["deduct_profit_yoy"] = live.get("deduct_profit_yoy")
        out["gross_margin"] = live.get("gross_margin")
        out["roe"] = live.get("roe")
        out["ocf_to_ni"] = live.get("ocf_to_ni")
        out["eps_deduct"] = live.get("deduct_eps")

        # 净利率接口不给，但分子分母都在，直接算，不留空
        rev = live.get("revenue")
        np_ = live.get("parent_netprofit")
        if rev and np_ is not None:
            try:
                out["net_margin"] = round(float(np_) / float(rev) * 100, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                out["net_margin"] = None
        else:
            out["net_margin"] = None

        # 市值以行情为准（行情口径是实时总市值），行情没有才用财务里的
        out["total_mv"] = w.total_mv

        for k in FIN_FIELDS:
            if out.get(k) is None and w.manual_fin.get(k) is not None:
                out[k] = w.manual_fin[k]

        trend = self._setting(SETTING_FIN_TREND.format(code=w.code))
        if out.get("gross_margin_trend") is None and trend in ("up", "flat", "down"):
            out["gross_margin_trend"] = trend

        return {k: out.get(k) for k in FIN_FIELDS}

    # ---------------- 3 冷门度 ----------------

    def _stage_popularity(self, res: UpdateResult, rep: StageReport) -> None:
        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("popularity", res)

            p = _safe(lambda: self.sources.popularity(
                w.code, force=self.opts.refresh_popularity))
            if p is not None and p.ok and isinstance(p.data, dict):
                w.popularity = p.data
                w.tuna_info = tuna_info_from_popularity(p.data)
            else:
                w.warnings.append(f"冷门度获取失败：{_err(p)}")

            rep.done += 1
            if w.popularity is None:
                rep.failed += 1
            self._emit("popularity", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) 冷门度")

    # ---------------- 4 壁垒 ----------------

    def _stage_barrier(self, res: UpdateResult, rep: StageReport) -> None:
        be = legacy_bridge.load()["barrier"]
        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("barrier", res)

            if w.financial is None:
                rep.skipped += 1
                self._emit("barrier", i, rep.total, code=w.code, message="跳过：无财务")
                continue

            info = self._sector_intel(w.sector)
            extra = be.build_extra_from_financial(w.financial, w.manual_fin or None)
            try:
                w.barrier = be.score_track_barrier(
                    extra, info or {},
                    manual_override=self._barrier_override(w.code),
                    fallback=self._fallback_barrier(w.code),
                )
                rep.done += 1
            except Exception as exc:
                w.errors.append(f"壁垒评分失败：{type(exc).__name__}: {exc}")
                rep.failed += 1

            self._emit("barrier", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) 壁垒 {_fmt(getattr(w.barrier, 'score', None))}")

    # ---------------- 5 评分 ----------------

    def _stage_score(self, res: UpdateResult, rep: StageReport) -> None:
        per_sector: dict[str, list[float | None]] = {}
        for w in res.works:
            per_sector.setdefault(w.sector, []).append(w.chg_20d)
        heat_proxy, pool_heat = sector_heat_proxy(per_sector)

        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("score", res)

            if w.financial is None or w.barrier is None:
                rep.skipped += 1
                self._emit("score", i, rep.total, code=w.code, message="跳过：缺财务或壁垒")
                continue

            heat = heat_proxy.get(w.sector)
            if heat is None:
                heat = pool_heat
                if heat is not None:
                    w.warnings.append("同赛道样本不足 2 只，热度用整池均值代理")

            try:
                w.score_result = scoring.score(
                    code=w.code, name=w.name, sector=w.sector,
                    track_barrier_score=float(getattr(w.barrier, "score", 0.0) or 0.0),
                    financial=w.financial,
                    sector_heat_change=heat,
                    price_change_5d=w.chg_5d,
                    peg=self._peg(w),
                    sector_heat_level=None,
                    popularity_score=(w.tuna_info or {}).get("popularity_score"),
                    tuna_severity=(w.tuna_info or {}).get("tuna_severity"),
                )
                w.scores = scoring.score_dict(w.score_result)
                rep.done += 1
            except Exception as exc:
                w.errors.append(f"评分失败：{type(exc).__name__}: {exc}")
                rep.failed += 1

            self._emit("score", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) 评分 {_fmt((w.scores or {}).get('total'))}")

    def _peg(self, w: StockWork) -> float | None:
        """简易 PEG = PE / 净利同比。净利为负或 PE 缺失就不算——硬凑出来的 PEG 更害人。"""
        pe = (w.quote or {}).get("pe")
        g = (w.fin_raw or {}).get("net_profit_yoy")
        if not pe or not g or float(pe) <= 0 or float(g) <= 0:
            return None
        try:
            return round(float(pe) / float(g), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    # ---------------- 6 池子判定 ----------------

    def _stage_pool(self, res: UpdateResult, rep: StageReport) -> None:
        today = self.opts.as_of_date
        for i, w in enumerate(res.works, 1):
            self._raise_if_cancelled("pool", res)

            if w.scores is None:
                rep.skipped += 1
                self._emit("pool", i, rep.total, code=w.code, message="跳过：无评分")
                continue

            state = pool_rules.load_state(self.conn, w.code)
            if state is None:
                w.errors.append("池子状态缺失（pool_states 无此行），跳过判定")
                rep.failed += 1
                self._emit("pool", i, rep.total, code=w.code, message="跳过：无池子状态")
                continue

            if state.last_judged_at == today:
                # 同一天点第二次「更新」不该算第二周
                w.decision = pool_rules.Decision(
                    code=w.code, from_pool=state.pool, kind="frozen",
                    reason="同一交易日已判定，不重复计周", state=state,
                )
                w.pool = state.pool
                rep.skipped += 1
            else:
                w.decision = pool_rules.decide(
                    state, total_score=float(w.scores["total"]),
                    tuna_info=w.tuna_info, today=today,
                )
                w.pool = (w.decision.state.pool if w.decision.state else state.pool)
                rep.done += 1

            self._emit("pool", i, rep.total, code=w.code,
                       message=f"{w.name}({w.code}) {_pool_msg(w.decision)}")

    # ---------------- 7 落库 ----------------

    def _stage_persist(self, res: UpdateResult, rep: StageReport) -> None:
        as_of = self.opts.as_of_date
        with session.transaction(self.conn):
            for i, w in enumerate(res.works, 1):
                if w.score_result is None:
                    rep.skipped += 1
                    continue
                w.snapshot_id = scoring.persist(
                    self.conn, w.score_result, as_of, source="live",
                    run_id=res.run_id, total_mv_yi=w.total_mv, pool=w.pool,
                )
                if w.barrier is not None:
                    w.barrier_snapshot_id = self._persist_barrier(w, as_of)
                if w.quote:
                    self._persist_quote(w)
                if w.decision is not None:
                    pool_rules.apply_decision(self.conn, w.decision, as_of)
                rep.done += 1
                self._emit("persist", i, rep.total, code=w.code,
                           message=f"{w.name}({w.code}) 落库")

            self.conn.execute(
                "INSERT INTO audit_log(actor, action, target, detail) VALUES (?,?,?,?)",
                ("app", "update.run", res.run_id,
                 json.dumps({"asOfDate": as_of, "ok": res.ok_count,
                             "fail": res.fail_count, "moved": len(res.moved)},
                            ensure_ascii=False)),
            )
        rep.message = f"写入 {rep.done} 只"

    def _persist_quote(self, w: StockWork) -> None:
        """最新行情 UPSERT。持仓市值、仓位占比、价格时间戳都靠它。"""
        q = w.quote or {}
        self.conn.execute(
            """INSERT INTO quotes(code, price, prev_close, pct, open, high, low,
                   turnover_pct, pe, total_mv_yi, float_mv_yi, quote_time,
                   quoted_at, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'tencent',datetime('now','localtime'))
               ON CONFLICT(code) DO UPDATE SET
                   price=excluded.price, prev_close=excluded.prev_close,
                   pct=excluded.pct, open=excluded.open, high=excluded.high,
                   low=excluded.low, turnover_pct=excluded.turnover_pct,
                   pe=excluded.pe, total_mv_yi=excluded.total_mv_yi,
                   float_mv_yi=excluded.float_mv_yi, quote_time=excluded.quote_time,
                   quoted_at=excluded.quoted_at, source=excluded.source,
                   updated_at=excluded.updated_at""",
            (w.code, q.get("price"), q.get("prev_close"), q.get("pct"),
             q.get("open"), q.get("high"), q.get("low"), q.get("turnover_pct"),
             q.get("pe"), q.get("total_mv_yi"), q.get("float_mv_yi"),
             q.get("time"), _now_iso()),
        )

    def _persist_barrier(self, w: StockWork, as_of: str) -> int:
        b = w.barrier
        cur = self.conn.execute(
            """INSERT INTO barrier_snapshots(
                   code, as_of_date, score, confidence, dimensions,
                   source, needs_review)
               VALUES (?,?,?,?,?,?,?)""",
            (w.code, as_of, b.score, b.overall_confidence,
             json.dumps([d.to_dict() for d in b.dimensions], ensure_ascii=False),
             b.source, 1 if b.needs_review else 0),
        )
        return int(cur.lastrowid)

    # ---------------- 数据来源小组件 ----------------

    def _manual_financial(self, code: str) -> dict:
        if code in self._manual_cache:
            return self._manual_cache[code]
        rows = self.conn.execute(
            "SELECT field, value FROM financial_manual WHERE code=? AND period='latest'",
            (code,),
        ).fetchall()
        out = {r["field"]: r["value"] for r in rows if r["value"] is not None}
        self._manual_cache[code] = out
        return out

    def _sector_intel(self, sector: str) -> dict | None:
        if not sector:
            return None
        if sector in self._intel_cache:
            return self._intel_cache[sector]
        try:
            info = intel.load_intel(sector, self.conn)
        except Exception:
            info = None
        self._intel_cache[sector] = info
        return info

    def _setting(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _barrier_override(self, code: str) -> float | None:
        raw = self._setting(SETTING_BARRIER_OVERRIDE.format(code=code))
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _fallback_barrier(self, code: str) -> float | None:
        """情报没录入时的兜底分：沿用这只票上一次算出来的壁垒分。

        直接用历史值而不是 0，是为了避免「情报被删 → 自动分暴跌 → 池子错乱」。
        找不到历史就用 0（首次进池的票本来就没历史，0 分是诚实的）。
        """
        row = self.conn.execute(
            """SELECT track_barrier FROM score_snapshots
                WHERE code=? AND track_barrier IS NOT NULL
                ORDER BY as_of_date DESC, id DESC LIMIT 1""",
            (code,),
        ).fetchone()
        return float(row["track_barrier"]) if row else None

    # ---------------- 运行记录 ----------------

    def _new_run_id(self, as_of: str) -> str:
        return f"{as_of}-{uuid.uuid4().hex[:8]}"

    def _open_run_row(self, run_id: str) -> None:
        with session.transaction(self.conn):
            self.conn.execute(
                """INSERT INTO score_runs(run_id, kind, status, algo_version, started_at)
                   VALUES (?,?,'running',?,?)""",
                (run_id, self.opts.kind, scoring.algo_version(), _now_iso()),
            )

    def _close_run_row(self, res: UpdateResult) -> None:
        summary = {
            "asOfDate": res.as_of_date,
            "stages": [s.as_dict() for s in res.stages],
            "errors": [{"code": w.code, "errors": w.errors}
                       for w in res.works if w.errors][:50],
            "warnings": [{"code": w.code, "warnings": w.warnings}
                         for w in res.works if w.warnings][:50],
            "moved": [{"code": w.code, "from": w.decision.from_pool,
                       "to": w.decision.to_pool, "reason": w.decision.reason}
                      for w in res.moved],
        }
        try:
            with session.transaction(self.conn):
                self.conn.execute(
                    """UPDATE score_runs SET status=?, finished_at=?, total=?,
                           ok_count=?, fail_count=?, summary=? WHERE run_id=?""",
                    (res.status, res.finished_at, res.total, res.ok_count,
                     res.fail_count, json.dumps(summary, ensure_ascii=False), res.run_id),
                )
        except Exception:
            # 收尾写失败不该盖掉已经跑出来的结果，状态已经带回给调用方了
            pass


# ---------------------------------------------------------------- 便捷入口

def run_update(conn: sqlite3.Connection, sources: Any, *,
               as_of_date: str | None = None,
               kind: str = "manual",
               codes: Sequence[str] | None = None,
               include_removed: bool = False,
               refresh_finance: bool = False,
               refresh_popularity: bool = False,
               progress: Callable[[Progress], None] | None = None,
               cancel: Any = None) -> UpdateResult:
    opts = UpdateOptions(
        as_of_date=as_of_date or datetime.now().strftime("%Y-%m-%d"),
        kind=kind, codes=codes, include_removed=include_removed,
        refresh_finance=refresh_finance, refresh_popularity=refresh_popularity,
    )
    return Updater(conn, sources, options=opts,
                   progress=progress, cancel=cancel).run()


# ---------------------------------------------------------------- 小工具

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(fn: Callable[[], Any]) -> Any:
    """取数调用兜住异常：一个源炸了不该让整轮更新挂掉。"""
    try:
        return fn()
    except Exception as exc:
        return SourceResult(ok=False, source="updater",
                            error=f"{type(exc).__name__}: {exc}")


def _err(res: Any) -> str:
    if res is None:
        return "无返回"
    if getattr(res, "skipped", None):
        return f"跳过（{res.skipped}）"
    return getattr(res, "error", None) or "未知错误"


def _fmt(v: Any) -> str:
    return "-" if v is None else f"{float(v):.2f}"


def _pool_msg(dec: Any) -> str:
    if dec is None:
        return "无判定"
    if dec.moved:
        return f"{pool_rules.POOL_LABELS.get(dec.from_pool, dec.from_pool)}→" \
               f"{pool_rules.POOL_LABELS.get(dec.to_pool, dec.to_pool)}"
    return dec.reason or dec.kind
