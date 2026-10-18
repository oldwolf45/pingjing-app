# -*- coding: utf-8 -*-
"""
pool_rotation.py — 紫苏叶池子进出轮换（2026-10-17 新增）

这个模块只干三件事，别的都不管：
  1. 体检     —— 给池内每只票拉冷门度三指标，判定该不该进金枪鱼池
  2. 迁移     —— 把历史上被错误归入"移除池"的金枪鱼改判回金枪鱼池
  3. 补票     —— 定额制：出几只补几只，只从"情报已录入 + 财务已拉全"的票里挑

为什么单独一个模块
------------------
`pool_manager` 管的是"状态机"（周计数、连续确认、池子迁移），它不该知道
sector_map.json 在哪、情报文件怎么读。而补票的资格判断恰恰依赖这些文件。
把资格判断放这里，pool_manager 保持纯粹的状态机，两边职责不打架。

用法
----
    python pool_rotation.py                     # 体检报告 + 补票预演（不写盘）
    python pool_rotation.py --apply             # 真写盘
    python pool_rotation.py --migrate           # 迁移历史金枪鱼（先预演）
    python pool_rotation.py --migrate --apply   # 迁移并写盘
    python pool_rotation.py --refill-only       # 只算补票候选，不碰冷门度
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config as cfg
from pool_manager import ThreePoolManager, POOL_NAMES, ACTIVE_POOLS
from popularity_engine import (evaluate as evaluate_popularity, judge_tuna,
                              judge_back, HOT_REPORTS, HOT_ACTIVE_RATIO, BIG_MV_YI)

DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_FILE = os.path.join(DATA_DIR, "tracking_state.json")
SCORE_CACHE = os.path.join(DATA_DIR, "full_pool_score_cache.json")
SECTOR_MAP = os.path.join(BASE_DIR, "config", "sector_map.json")
INTEL_DIR = os.path.join(BASE_DIR, "赛道数据库", "intel")


# ======================================================================
# 基础数据读取
# ======================================================================
def _code6(x):
    return re.sub(r"[^0-9]", "", str(x))[-6:]


def load_sector_map():
    try:
        return json.load(open(SECTOR_MAP, encoding="utf-8")).get("map") or {}
    except Exception:
        return {}


def intel_of(code6, smap=None):
    """→ (情报dict, 赛道名, intel_key)；情报文件读不到返回 (None, sector, '')"""
    smap = smap if smap is not None else load_sector_map()
    ent = smap.get(code6) or {}
    sector = ent.get("sector") or ""
    key = ent.get("intel_key") or ""
    it = None
    if key:
        fp = os.path.join(INTEL_DIR, key + ".json")
        if os.path.exists(fp):
            try:
                it = json.load(open(fp, encoding="utf-8"))
            except Exception:
                it = None
    return it, sector, key


def load_scores():
    """全池评分缓存 → {code6: {total, barrier, pool, name, ...}}"""
    try:
        return json.load(open(SCORE_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def load_names():
    """代码 → 名称。pool_names.json 是手工维护的老名单，
    全市场反查新增的票不在里面，所以再补一层 universe_quotes.json
    （扫描时用腾讯行情拿的 4949 只名称，现成的，不用额外请求）。"""
    try:
        names = json.load(open(os.path.join(DATA_DIR, "pool_names.json"),
                               encoding="utf-8"))
    except Exception:
        names = {}
    p = os.path.join(DATA_DIR, "universe_quotes.json")
    if os.path.exists(p):
        try:
            for c6, q in json.load(open(p, encoding="utf-8")).items():
                if c6 not in names and (q or {}).get("name"):
                    names[c6] = q["name"]
        except Exception:
            pass
    return names


# ======================================================================
# 一、体检：池内票的金枪鱼判定
# ======================================================================
def health_check(manager, verbose=True):
    """给所有活跃池的票拉冷门度，返回 {code: pop}（不写盘、不改池子）"""
    active = [ts for ts in manager.stocks.values() if ts.current_pool in ACTIVE_POOLS]
    out, hits = {}, []
    if verbose:
        print("\n" + "=" * 74)
        print("🩺 池内体检：%d 只活跃标的" % len(active))
        print("=" * 74)
        print("%-8s %-7s %6s %8s %8s  %s" %
              ("代码", "名称", "研报", "主动%", "市值亿", "判定"))
        print("-" * 74)

    for ts in active:
        try:
            pop = evaluate_popularity(ts.code)
        except Exception as e:
            if verbose:
                print("%-8s %-7s 冷门度获取失败: %s" % (ts.code, ts.name, str(e)[:40]))
            continue
        out[ts.code] = pop
        d = pop.get("details") or {}
        flag = pop.get("tuna_flag")
        if flag:
            why = {"hot": "变热", "big": "变大", "both": "变大+变热"}.get(
                pop.get("tuna_reason"), "金枪鱼化")
            hits.append((ts.code, ts.name, why, pop.get("tuna_reasons") or []))
            mark = "🐟 " + why
        else:
            ok, miss = judge_back(d.get("reports_12m"), d.get("active_ratio_pct"),
                                  d.get("total_mv_yi"))
            mark = "✅ 仍冷门" if ok else "· 留池（差：%s）" % "、".join(miss)
        if verbose:
            print("%-8s %-7s %6s %8s %8s  %s" % (
                ts.code, ts.name,
                d.get("reports_12m"), d.get("active_ratio_pct"),
                d.get("total_mv_yi"), mark))
    if verbose and hits:
        print("\n  ⚠️ 命中金枪鱼判定 %d 只：" % len(hits))
        for c, n, why, rs in hits:
            print("     %s %s —— %s（%s）" % (c, n, why, "；".join(rs) or "-"))
    return out


# ======================================================================
# 二、迁移：把误判进"移除池"的金枪鱼改判回金枪鱼池
# ======================================================================
def migrate(manager, apply=False, verbose=True):
    """历史上分数破线被移出的票，如果其实是"变大变热"，改判到金枪鱼池

    注意：迁移不是"出池动作"，是给已经在池外的票重新分配去向，
    所以不套"连续 2 周确认"——那是对还在池内的票的约束。
    """
    removed = [ts for ts in manager.stocks.values() if ts.current_pool == "removed"]
    kept_tuna, kept_remove = [], []
    if verbose:
        print("\n" + "=" * 74)
        print("🔁 迁移：移除池 %d 只，按真实去向重新分拣" % len(removed))
        print("=" * 74)

    for ts in removed:
        try:
            pop = evaluate_popularity(ts.code)
        except Exception:
            kept_remove.append((ts, "冷门度取不到，维持移除"))
            continue
        d = pop.get("details") or {}
        if pop.get("tuna_flag"):
            why = {"hot": "变热", "big": "变大", "both": "变大+变热"}.get(
                pop.get("tuna_reason"), "金枪鱼化")
            kept_tuna.append((ts, why, pop))
            if verbose:
                print("  🐟 %s %s → 金枪鱼池（%s｜市值%s亿 研报%s 主动%s%%）" % (
                    ts.code, ts.name, why, d.get("total_mv_yi"),
                    d.get("reports_12m"), d.get("active_ratio_pct")))
        else:
            kept_remove.append((ts, ts.remove_reason or "分数长期破线"))
            if verbose:
                print("  ❌ %s %s → 维持移除（市值%s亿 研报%s 主动%s%%）" % (
                    ts.code, ts.name, d.get("total_mv_yi"),
                    d.get("reports_12m"), d.get("active_ratio_pct")))

    if verbose:
        print("\n  小结：改判金枪鱼 %d 只，维持移除 %d 只" % (len(kept_tuna), len(kept_remove)))

    # 不管 apply 与否都做内存迁移——否则后续的"缺口/补票"是基于迁移前的旧状态算的，
    # 预演看到的名额缺口会比真实情况少（少了改判出去的那几只）。
    # 落盘由 main() 的 --apply 统一控制。
    for ts, why, pop in kept_tuna:
        manager._move_pool(ts, "removed", "tuna", "历史迁移：%s" % why, tuna_info=pop)
    if apply:
        _write_config_tuna_snapshot(manager)
    return kept_tuna, kept_remove


def _write_config_tuna_snapshot(manager):
    """把金枪鱼池快照回写 config.py 的 TUNA_POOL（人工可读底稿）"""
    rows = []
    for ts in manager.stocks.values():
        if ts.current_pool != "tuna":
            continue
        snap = ts.exit_snapshot or {}
        rows.append({
            "code": ts.code, "name": ts.name, "sector": ts.sector,
            "exit_date": ts.exit_date or datetime.now().strftime("%Y-%m-%d"),
            "exit_reason": snap.get("reason") or ts.tuna_reason or "",
            "exit_mv": snap.get("total_mv_yi"),
            "tuna_status": ts.tuna_status or "watching",
        })
    path = os.path.join(BASE_DIR, "config", "tuna_pool.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  💾 金枪鱼池快照 → config/tuna_pool.json（%d 只）" % len(rows))


# ======================================================================
# 三、补票：定额制，只从够格的票里挑
# ======================================================================
def build_candidates(manager, min_score=None, verbose=True):
    """合格候选池 → 按分数降序

    进池硬门槛（已与用户确认）：
      1. 情报已录入：sector_map 有记录 且 intel_key 指向的情报文件能读到
      2. 财务数据拉全：full_pool_score_cache 里有分（total 不为空）
      3. 分数 ≥ 储备线
      4. 不在现有任何池里（含金枪鱼池、移除池——移除池的票除非分数回升否则不复活）
    """
    min_score = cfg.POOL_THRESHOLDS["reserve"] if min_score is None else min_score
    smap = load_sector_map()
    scores = load_scores()
    names = load_names()
    in_pool = set(manager.stocks)
    rows = []
    drop = {"无评分": 0, "市值超限": 0, "待补情报": 0,
            "分数不够": 0, "已在池": 0, "科创板不可买": 0}

    for code, s in scores.items():
        c6 = _code6(code)
        if c6 in in_pool:
            drop["已在池"] += 1
            continue
        if not cfg.is_buyable(c6):
            drop["科创板不可买"] += 1
            continue
        total = s.get("total")
        if total is None:
            drop["无评分"] += 1
            continue
        # 市值硬上限（用户 2026-10-17 定：>250 亿 = 已"变大"，不属紫苏叶）
        # 单独计数，不能跟"分数不够"混在一起——否则看不清候选库到底缺什么
        mv = s.get("total_mv_yi")
        if mv is None:
            mv = (s.get("quote") or {}).get("total_mv") or \
                 (s.get("financial") or {}).get("total_mv")
        if mv is not None and mv > BIG_MV_YI:
            drop["市值超限"] += 1
            continue
        it, sector, key = intel_of(c6, smap)
        if cfg.ENTRY_REQUIRE_INTEL and (not sector or it is None):
            drop["待补情报"] += 1
            continue
        if total < min_score:
            drop["分数不够"] += 1
            continue
        rows.append({
            "code": c6,
            "name": (names.get(c6) or (s.get("name")) or c6),
            "sector": sector,
            "intel_key": key,
            "track_barrier": (s.get("barrier") or {}).get("score"),
            "entry_score": total,
            "total_mv_yi": mv,
            "pool": _pool_of(total),
        })

    rows.sort(key=lambda x: x["entry_score"], reverse=True)
    if verbose:
        print("\n" + "=" * 74)
        print("🎯 补票候选池（门槛：情报已录入 + 财务有分 + 分数≥%.1f）" % min_score)
        print("=" * 74)
        for r in rows:
            print("  %-8s %-7s %5.2f分  %-22s %s" % (
                r["code"], r["name"], r["entry_score"], r["sector"],
                POOL_NAMES.get(r["pool"], r["pool"])))
        print("\n  合计 %d 只可补｜剔除：%s" % (
            len(rows), "、".join("%s %d" % (k, v) for k, v in drop.items() if v)))
    return rows


def _pool_of(total):
    t = cfg.POOL_THRESHOLDS
    if total >= t["candidate"]:
        return "candidate"
    if total >= t["observation"]:
        return "observation"
    return "reserve"


# ======================================================================
# 主流程
# ======================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只预演）")
    ap.add_argument("--migrate", action="store_true", help="执行历史移除池→金枪鱼池迁移")
    ap.add_argument("--refill-only", action="store_true", help="只算补票候选")
    ap.add_argument("--capacity", type=int, default=None, help="覆盖名额上限")
    args = ap.parse_args()

    cap = args.capacity or cfg.POOL_CAPACITY
    manager = ThreePoolManager()
    if os.path.exists(STATE_FILE) and manager.load_from_file(STATE_FILE):
        print("✅ 载入跟踪状态（%d 只）" % len(manager.stocks))
    else:
        print("⚠️ 未找到跟踪状态，使用 config 初始名单")

    counts = {p: len(manager.get_pool_stocks(p)) for p in POOL_NAMES}
    print("   " + "｜".join("%s %d" % (POOL_NAMES[p], n) for p, n in counts.items()))
    print("   名额 %d｜在池 %d｜空余名额 %d" %
          (cap, manager.active_count(), manager.slots_left(cap)))

    if args.migrate:
        migrate(manager, apply=args.apply)

    if not args.refill_only:
        pops = health_check(manager)
        # 把本周体检结果记进状态机（金枪鱼计数 +1），否则"连续2周确认"永远起不来
        # 首周不计数的判断在 pool_manager._tuna_gate 里，这里不重复
        marked = []
        for code, pop in pops.items():
            if pop.get("tuna_flag") and manager.stocks[code].current_pool in ACTIVE_POOLS:
                if manager.mark_tuna_week(code, pop):
                    marked.append(code)
        if marked:
            print("\n  📝 本周已记账 %d 只：%s" % (len(marked), "、".join(marked)))

    cands = build_candidates(manager)
    res = manager.refill(cands, capacity=cap, dry_run=not args.apply)
    if res["added"]:
        print("   补进：" + "、".join("%s %s" % (c["code"], c["name"]) for c in res["added"]))
    if res["slots_left"]:
        print("   ⚠️ 名额空着 %d 个（候选库不够格，不降标准硬补）" % res["slots_left"])

    if args.apply:
        manager.save_to_file(STATE_FILE)
        _write_config_tuna_snapshot(manager)
        print("\n💾 已写入 %s" % STATE_FILE)
    else:
        print("\n（预演模式，未写盘。加 --apply 执行）")


if __name__ == "__main__":
    main()
