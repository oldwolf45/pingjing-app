"""池子判定（PRD 附录 A，DEV_PLAN T4.4）。

这是整个系统最容易写错的地方，所以规则全部提成纯函数：
输入一份状态 + 本周数据，输出"要不要动、动到哪、计数变成多少"，
落库另走 apply_decision。这样 9 条验收可以逐个场景钉死，不用起数据库。

判定顺序（A.9，不许调换）：

    ⓿ 进池当周（price_ready=False）→ 跳过 ②，但 ③ 照跑
    ① 已在金枪鱼池 → 只跑回归判定，结束
    ② 命中出池线（变热 / 变大）→ 计数 +1，满 2 周移入金枪鱼池，结束
    ③ 分数升降级（6.5 / 5.5 / 4.5 阈值，升级 2 周 / 降级 2 周 / 移除 4 周）

为什么顺序不能反：一只票变大变热后冷门度分会掉、金枪鱼扣分会加，
先跑 ③ 会把它推向"已移除"——出口就错了。

为什么 ⓿ 不能顺手把 ③ 也短路：weeks_in_pool 是在这里递增的，
跳掉 ③ 会让它永远不涨，price_ready 就永远翻不了 True，新票会被永久冻住。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from app import legacy_bridge

POOL_CAPACITY = 28
ACTIVE_POOLS = ("candidate", "observation", "reserve")
POOL_ORDER = ["candidate", "observation", "reserve", "tuna", "removed"]
POOL_LABELS = {
    "candidate": "候选池", "observation": "观察池", "reserve": "储备池",
    "tuna": "金枪鱼池", "removed": "已移除",
}


@dataclass
class PoolState:
    """一只票的池子状态。字段与 pool_states 表一一对应。"""

    code: str
    name: str = ""
    pool: str = "reserve"
    weeks_in_pool: int = 0
    price_ready: bool = True
    consecutive_weeks_above: int = 0
    consecutive_weeks_below: int = 0
    tuna_weeks: int = 0
    tuna_reason: str | None = None
    tuna_hits: list[str] = field(default_factory=list)
    back_weeks: int = 0
    exit_date: str | None = None
    exit_snapshot: dict | None = None
    remove_reason: str | None = None
    last_judged_at: str | None = None   # 上次判定的日期，用来挡"一天点两次更新算两周"


@dataclass
class Decision:
    """一次判定的结果。to_pool 为 None 表示池子不变。"""

    code: str
    from_pool: str
    to_pool: str | None = None
    reason: str = ""
    kind: str = "none"   # none / move / pending_tuna / tuna_back / frozen
    state: PoolState | None = None

    @property
    def moved(self) -> bool:
        return self.to_pool is not None and self.to_pool != self.from_pool


def algo_consts() -> dict[str, Any]:
    """阈值一律从 algo/config.py 读，绝不在业务层另写一份。"""
    cfg = legacy_bridge.load()["config"]
    return {
        "thresholds": dict(cfg.POOL_THRESHOLDS),
        "upgrade_weeks": cfg.UPGRADE_WEEKS,
        "downgrade_weeks": cfg.DOWNGRADE_WEEKS,
        "remove_weeks": cfg.REMOVE_WEEKS,
        "capacity": cfg.POOL_CAPACITY,
        "tuna_confirm_weeks": cfg.TUNA_CONFIRM_WEEKS,
        "tuna_back_weeks": cfg.TUNA_BACK_WEEKS,
        "new_entry_no_price_weeks": cfg.NEW_ENTRY_NO_PRICE_WEEKS,
    }


def tier_for_score(total_score: float) -> str:
    """按分数定层：≥6.5 候选 / ≥5.5 观察 / ≥4.5 储备 / 否则移除。"""
    mods = legacy_bridge.load()
    return mods["scoring"].determine_pool(total_score)


def entry_pool(total_score: float, manual: bool = False) -> str:
    """新票进池的落点（A.7 两个入口）。

    补票：按分数定层。能进来就说明情报、财务、市值、分数四关都过了，
          分数本身就是最完整的判断依据，压到储备池等于用防御规则否定评分机制。
    人工添加：只能进储备池。人工加的票通常没情报（壁垒走 1.2 兜底，
          总分最高 6.0），也没经过一次完整更新验证，储备池是观察位。
    """
    return "reserve" if manual else tier_for_score(total_score)


def eligible_for_refill(*, intel_ok: bool, finance_ok: bool,
                        total_score: float | None, total_mv_yi: float | None) -> bool:
    """补票四条件，缺一不可（A.7 入口①）。"""
    if not intel_ok or not finance_ok or total_score is None:
        return False
    if total_mv_yi is None or total_mv_yi > 250:
        return False
    return total_score >= 4.5


MV_LIMIT_YI = 250.0


def gate_verdict(c: dict) -> dict:
    """一只候选票过不过补票闸门，以及被挡的具体理由。

    PRD 验收第 17 条要求「市值超限」和「分数不够」**分开计数**：
    市值 300 亿被挡下，不是它不够好，是它已经不符合紫苏叶的选股前提
    （要找小而冷门的上游）。混在一起数，看报告的人会以为选股标准出问题了。
    """
    mv = c.get("total_mv_yi")
    if mv is None:
        return {"eligible": False, "blocked": True, "blocked_by": "market_cap",
                "block_reason": "缺市值数据，无法判定市值上限"}
    if float(mv) > MV_LIMIT_YI:
        return {"eligible": False, "blocked": True, "blocked_by": "market_cap",
                "block_reason": f"市值{float(mv):g}亿>{MV_LIMIT_YI:g}亿上限"}
    if not c.get("intel_ok"):
        return {"eligible": False, "blocked": True, "blocked_by": "intel",
                "block_reason": "情报未录入（壁垒只能走兜底分）"}
    if not c.get("finance_ok"):
        return {"eligible": False, "blocked": True, "blocked_by": "finance",
                "block_reason": "财务缺数据，无法评分"}
    score = c.get("total_score")
    if score is None:
        return {"eligible": False, "blocked": True, "blocked_by": "score",
                "block_reason": "没有评分"}
    if float(score) < 4.5:
        # 唯一「不是硬闸门」的一档：分数不够，属于标准内的正常淘汰
        return {"eligible": False, "blocked": False, "blocked_by": "score",
                "block_reason": f"分数{float(score):g}低于 4.5"}
    return {"eligible": True, "blocked": False, "blocked_by": None, "block_reason": ""}


def screen_candidates(candidates: Iterable[dict], slots_left: int) -> dict:
    """把候选票过一遍闸门，按理由分开计数。

    返回 picked（已按分数降序、已定层）+ counts + verdicts（每只的判定与理由）。
    名额空着是允许的结果——补不上就不补，不降标准（A.7）。
    """
    rows = list(candidates)
    verdicts: list[dict] = []
    ok: list[dict] = []
    counts = {
        "total": len(rows),
        "picked": 0,
        "blocked_market_cap": 0,
        "blocked_intel": 0,
        "blocked_finance": 0,
        "score_short": 0,
        "slots_left": max(0, int(slots_left)),
    }

    for c in rows:
        v = gate_verdict(c)
        item = {"code": str(c.get("code")), "name": c.get("name"),
                "total_score": c.get("total_score"),
                "total_mv_yi": c.get("total_mv_yi"), **v}
        verdicts.append(item)
        if v["eligible"]:
            ok.append(c)
            continue
        if v["blocked_by"] == "market_cap":
            counts["blocked_market_cap"] += 1
        elif v["blocked_by"] == "intel":
            counts["blocked_intel"] += 1
        elif v["blocked_by"] == "finance":
            counts["blocked_finance"] += 1
        else:
            counts["score_short"] += 1

    ok.sort(key=lambda x: (-(x.get("total_score") or 0), str(x.get("code"))))
    picked = ok[: counts["slots_left"]]
    for c in picked:
        c["pool"] = entry_pool(c["total_score"], manual=False)
    counts["picked"] = len(picked)
    counts["slots_left_after"] = counts["slots_left"] - len(picked)
    return {"picked": picked, "counts": counts, "verdicts": verdicts}


def plan_refill(slots_left: int, candidates: Iterable[dict]) -> list[dict]:
    """出一只补一只。补不上就空着，绝不降标准硬补。

    candidates 每项至少要有：code / name / total_score / intel_ok / finance_ok /
    total_mv_yi。按分数从高到低挑，进哪一池由 score 决定（entry_pool）。
    """
    return screen_candidates(candidates, slots_left)["picked"]


def _move(state: PoolState, to_pool: str, reason: str,
          today: str, tuna_info: dict | None = None) -> Decision:
    new = replace(state, pool=to_pool, weeks_in_pool=0,
                  consecutive_weeks_above=0, consecutive_weeks_below=0)
    if to_pool == "tuna":
        new.exit_date = today
        new.exit_snapshot = {
            "exit_date": today,
            "exit_reason": (tuna_info or {}).get("tuna_reason"),
            "exit_reasons": list((tuna_info or {}).get("tuna_reasons") or []),
            "details": dict((tuna_info or {}).get("details") or {}),
            "exit_mv": ((tuna_info or {}).get("details") or {}).get("total_mv_yi"),
        }
        new.tuna_weeks = 0
    if to_pool == "removed":
        new.remove_reason = reason
    if to_pool in ACTIVE_POOLS:
        # 进池当周不出买入价（A.7），下周确认后才给数字
        new.price_ready = False
        new.tuna_weeks = 0
        new.back_weeks = 0
    return Decision(code=state.code, from_pool=state.pool, to_pool=to_pool,
                    reason=reason, kind="move", state=new)


def _check_tuna_back(state: PoolState, tuna_info: dict | None, today: str) -> Decision:
    """金枪鱼池：三条同时满足连续 4 周 → 回储备池（不是候选池）。

    tuna_info 为 None（本次没拉到冷门度数据）时，不算断档也不算满足，
    保持原计数不变——数据缺失不该被当成"回归失败"。
    """
    consts = algo_consts()
    new = replace(state)
    if not tuna_info:
        return Decision(code=state.code, from_pool="tuna", kind="frozen",
                        reason="未取到冷门度数据，回归计数保持不变", state=new)

    d = tuna_info.get("details") or {}
    mods = legacy_bridge.load()
    ok, miss = mods["popularity"].judge_back(
        d.get("reports_12m"), d.get("active_ratio_pct"), d.get("total_mv_yi"))

    if ok:
        new.back_weeks += 1
        if new.back_weeks >= consts["tuna_back_weeks"]:
            return _move(state, "reserve",
                         f"连续{consts['tuna_back_weeks']}周满足回归条件"
                         f"（市值<200亿/研报≤10篇/主动资金≤3%）", today)
        return Decision(code=state.code, from_pool="tuna", kind="tuna_back",
                        reason=f"回归条件满足 {new.back_weeks}/{consts['tuna_back_weeks']} 周",
                        state=new)

    new.back_weeks = 0
    return Decision(code=state.code, from_pool="tuna", kind="tuna_back",
                    reason="回归中断：" + "、".join(miss), state=new)


def _check_exit_line(state: PoolState, tuna_info: dict, today: str) -> Decision:
    """② 出池线：变热 / 变大，各自单条成立即可，连续 2 周出池。"""
    consts = algo_consts()
    new = replace(state)
    new.tuna_weeks += 1
    new.tuna_reason = tuna_info.get("tuna_reason")
    reasons = list(tuna_info.get("tuna_reasons") or [])
    if reasons:
        new.tuna_hits = (new.tuna_hits + [f"{today}: " + "、".join(reasons)])[-8:]

    if new.tuna_weeks >= consts["tuna_confirm_weeks"]:
        label = {"hot": "变热", "big": "变大", "both": "变大且变热"}.get(
            new.tuna_reason or "", "金枪鱼化")
        reason = f"连续{consts['tuna_confirm_weeks']}周{label}"
        if reasons:
            reason += f"（{'、'.join(reasons)}）"
        return _move(state, "tuna", reason, today, tuna_info=tuna_info)

    return Decision(
        code=state.code, from_pool=state.pool, kind="pending_tuna",
        reason=f"命中出池线第 {new.tuna_weeks} 周"
               f"（{new.tuna_reason or '-'}），还需 "
               f"{consts['tuna_confirm_weeks'] - new.tuna_weeks} 周确认",
        state=new,
    )


def _check_score(state: PoolState, total_score: float, today: str) -> Decision:
    """③ 分数升降级。"""
    consts = algo_consts()
    new = replace(state)
    target = tier_for_score(total_score)
    current = state.pool

    if target == current:
        # 同池：把反向的连续计数清掉
        if POOL_ORDER.index(target) < POOL_ORDER.index(current):
            new.consecutive_weeks_above = 0
        elif POOL_ORDER.index(target) > POOL_ORDER.index(current):
            new.consecutive_weeks_below = 0
        return Decision(code=state.code, from_pool=current, kind="none",
                        reason=f"分数 {total_score:.2f} 仍在{POOL_LABELS.get(current, current)}",
                        state=new)

    if POOL_ORDER.index(target) < POOL_ORDER.index(current):
        new.consecutive_weeks_above += 1
        new.consecutive_weeks_below = 0
        if new.consecutive_weeks_above >= consts["upgrade_weeks"]:
            return _move(state, target,
                         f"连续{consts['upgrade_weeks']}周评分≥"
                         f"{consts['thresholds'].get(target, 0)}分", today)
        return Decision(code=state.code, from_pool=current, kind="none",
                        reason=f"接近升级 {new.consecutive_weeks_above}/"
                               f"{consts['upgrade_weeks']} 周", state=new)

    new.consecutive_weeks_below += 1
    new.consecutive_weeks_above = 0
    need = consts["remove_weeks"] if target == "removed" else consts["downgrade_weeks"]
    if new.consecutive_weeks_below >= need:
        return _move(state, target,
                     f"连续{need}周评分<{consts['thresholds'].get(current, 0)}分", today)
    return Decision(code=state.code, from_pool=current, kind="none",
                    reason=f"接近降级 {new.consecutive_weeks_below}/{need} 周", state=new)


def decide(state: PoolState, *, total_score: float, tuna_info: dict | None,
           today: str) -> Decision:
    """单只票的一次判定。纯函数：不改传入的 state。

    在池周数统一在这里加：每个更新周期只 +1，发生迁移时由 _move 归零。
    分散到各分支里加，第一次进池那周会被加两次。
    """
    consts = algo_consts()
    new = replace(state)
    new.tuna_hits = list(state.tuna_hits)

    # ⓿ 进池当周：没有买入价、还没建仓，不该因为"变热"被清出
    first_week = not new.price_ready

    if not first_week:
        # ① 已在金枪鱼池 → 只跑回归
        if new.pool == "tuna":
            dec = _check_tuna_back(new, tuna_info, today)
            return _add_week(state, dec, consts)

        # ② 出池线
        if tuna_info and tuna_info.get("tuna_flag"):
            dec = _check_exit_line(new, tuna_info, today)
            return _add_week(state, dec, consts)

        # 没命中 → 金枪鱼计数归零（必须连续）
        new.tuna_weeks = 0
        new.tuna_hits = []

    # ③ 分数升降级（⓿ 不短路这里，否则 weeks_in_pool 永远不涨，price_ready 翻不了身）
    dec = _check_score(new, total_score, today)
    return _add_week(state, dec, consts)


def _add_week(old: PoolState, dec: Decision, consts: dict) -> Decision:
    """没有迁移就 +1 在池周数；顺带把 price_ready 翻过来。"""
    if dec.state is None or dec.moved:
        return dec
    weeks = old.weeks_in_pool + 1
    dec.state.weeks_in_pool = weeks
    if not dec.state.price_ready and weeks >= consts["new_entry_no_price_weeks"]:
        dec.state.price_ready = True
    return dec


# ----------------------------------------------------------------------
# 落库
# ----------------------------------------------------------------------
def load_state(conn: sqlite3.Connection, code: str) -> PoolState | None:
    row = conn.execute(
        """SELECT s.code, s.name, p.* FROM stocks s
             JOIN pool_states p ON p.code = s.code WHERE s.code = ?""", (code,)
    ).fetchone()
    if not row:
        return None
    return PoolState(
        code=row["code"], name=row["name"] or "", pool=row["pool"],
        weeks_in_pool=row["weeks_in_pool"] or 0,
        price_ready=bool(row["price_ready"]),
        consecutive_weeks_above=row["consecutive_weeks_above"] or 0,
        consecutive_weeks_below=row["consecutive_weeks_below"] or 0,
        tuna_weeks=row["tuna_weeks"] or 0,
        tuna_reason=row["tuna_reason"],
        tuna_hits=json.loads(row["tuna_hits"]) if row["tuna_hits"] else [],
        back_weeks=row["back_weeks"] or 0,
        exit_date=row["exit_date"],
        exit_snapshot=json.loads(row["exit_snapshot"]) if row["exit_snapshot"] else None,
        remove_reason=row["remove_reason"],
        last_judged_at=row["last_judged_at"],
    )


def save_state(conn: sqlite3.Connection, st: PoolState, today: str) -> None:
    conn.execute(
        """UPDATE pool_states SET
               pool=?, weeks_in_pool=?, price_ready=?,
               consecutive_weeks_above=?, consecutive_weeks_below=?,
               tuna_weeks=?, tuna_reason=?, tuna_hits=?, tuna_status=?,
               exit_date=?, exit_snapshot=?, back_weeks=?, remove_reason=?,
               last_judged_at=?, updated_at=datetime('now','localtime')
           WHERE code=?""",
        (st.pool, st.weeks_in_pool, 1 if st.price_ready else 0,
         st.consecutive_weeks_above, st.consecutive_weeks_below,
         st.tuna_weeks, st.tuna_reason,
         json.dumps(st.tuna_hits, ensure_ascii=False) if st.tuna_hits else None,
         "watching" if st.pool == "tuna" else None,
         st.exit_date,
         json.dumps(st.exit_snapshot, ensure_ascii=False) if st.exit_snapshot else None,
         st.back_weeks, st.remove_reason,
         today, st.code),
    )


def apply_decision(conn: sqlite3.Connection, dec: Decision, today: str) -> None:
    """把判定写进 pool_states；池子真的变了才追加 pool_events。"""
    if dec.state is None:
        return
    save_state(conn, dec.state, today)
    if dec.moved:
        conn.execute(
            """INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date)
               VALUES (?,?,?,?,?,?)""",
            (dec.code, dec.state.name, dec.from_pool, dec.to_pool,
             dec.reason, today),
        )


def in_pool_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pool_states WHERE pool IN ('candidate','observation','reserve')"
    ).fetchone()
    return int(row["n"])


def slots_left(conn: sqlite3.Connection, capacity: int = POOL_CAPACITY) -> int:
    return max(0, capacity - in_pool_count(conn))
