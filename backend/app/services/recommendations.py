"""推荐追踪（DEV_PLAN T5.5 / T5.6）。

推荐 = 一次"我认为这只票会赚"的书面承诺，落库之后**永不修改**。
所以它有三层保护：
  1. 数据库层：BEFORE UPDATE / DELETE 触发器 RAISE(ABORT)
  2. 仓储层：本模块只有 insert 和 get，一个 update/delete 方法都不提供
  3. 审计层：每次写入记 audit_log

**三档统计分开算，绝不合成"成功率"**（PRD 5.6 硬规则）：
  hard_miss     硬失误   —— 期间最深回撤踩到 -15%，看错了
  flat          到期不赚 —— 满 45 个交易日收益 ≤0，没亏大但也没赚
  lag_benchmark 跑输大盘 —— 到期收益低于同期沪深300

一只票可以同时命中多档（比如又亏又输给大盘）。合并成一个数字就掩盖了
"是看错方向"还是"方向对了但没跑赢"——这两种错误该改的东西完全不同。

样本 <10 条时 `rate` 返回 None，前端显示「—」。不拿 3 条样本算成功率。
"""

from __future__ import annotations

import json
import sqlite3

from app.db import session

TIERS = ("hard_miss", "flat", "lag_benchmark")
TIER_LABELS = {
    "hard_miss": "硬失误",
    "flat": "到期不赚",
    "lag_benchmark": "跑输大盘",
}
TIER_HINTS = {
    "hard_miss": "期间最深回撤触及 -15%",
    "flat": "满 45 个交易日收益 ≤ 0",
    "lag_benchmark": "到期收益低于同期沪深300",
}

MIN_SAMPLE = 10          # 少于这个数不给成功率（PRD 验收 32）
JUDGE = {"version": "v1-45d-hs300", "days": 45,
         "hard_miss_dd_pct": -15.0, "benchmark": "hs300"}
JUDGE_VERSION = JUDGE["version"]


from app.exceptions import ValidationError  # noqa: E402


class RecommendationError(ValidationError):
    pass


# ---------------------------------------------------------------- 写入（只追加）

def create(conn: sqlite3.Connection, *, code: str, as_of_date: str,
           thesis: str | None = None, snapshot: dict | None = None,
           judge_version: str = JUDGE_VERSION) -> dict:
    """固一张推荐快照。

    同一只票同一天同一判定版本只留一条：重复调用返回已有的那条，
    不重复插。判断依据和当时的价格必须一起固化，否则事后无法复核。
    """
    exist = conn.execute(
        """SELECT * FROM recommendations
            WHERE code=? AND as_of_date=? AND judge_version=?""",
        (code, as_of_date, judge_version)).fetchone()
    if exist:
        return dict(exist)

    stock = conn.execute("SELECT name FROM stocks WHERE code=?", (code,)).fetchone()
    if not stock:
        raise RecommendationError(f"{code} 不在标的库里")

    score = conn.execute(
        "SELECT * FROM v_score_current WHERE code=?", (code,)).fetchone()
    quote = conn.execute("SELECT * FROM quotes WHERE code=?", (code,)).fetchone()
    price_cents = (int(round(quote["price"] * 100))
                   if quote and quote["price"] else None)
    pool = score["pool"] if score else None
    total = score["total"] if score else None

    snap = dict(snapshot or {})
    if score is not None and not snap:
        snap = {
            "total": score["total"],
            "pool": score["pool"],
            "dims": {k: score[k] for k in (
                "track_barrier", "earnings_growth", "profitability",
                "prosperity_check", "heat_position", "market_cap_elasticity")},
            "totalMvYi": score["total_mv_yi"],
            "asOfDate": score["as_of_date"],
        }

    with session.transaction(conn):
        cur = conn.execute(
            """INSERT INTO recommendations(code, name, as_of_date, pool, total_score,
                   price_cents, thesis, snapshot, judge_version)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (code, stock["name"], as_of_date, pool, total, price_cents, thesis,
             json.dumps(snap, ensure_ascii=False), judge_version),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES ('app','rec.create',?,?)",
            (code, f"{as_of_date} 分数{total} 价{price_cents}"),
        )
    return dict(conn.execute("SELECT * FROM recommendations WHERE id=?",
                             (cur.lastrowid,)).fetchone())


def track(conn: sqlite3.Connection, *, rec_id: int, as_of_date: str,
          price_cents: int | None, benchmark_pct: float | None = None,
          trading_days: int | None = None) -> dict:
    """记一次跟踪点。(rec_id, as_of_date) 唯一，重复记同一天不重复插。"""
    rec = conn.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)).fetchone()
    if not rec:
        raise RecommendationError("推荐记录不存在")

    exist = conn.execute(
        "SELECT * FROM recommendation_tracks WHERE rec_id=? AND as_of_date=?",
        (rec_id, as_of_date)).fetchone()
    if exist:
        return dict(exist)

    pct = None
    if price_cents is not None and rec["price_cents"]:
        pct = round((price_cents - rec["price_cents"]) / rec["price_cents"] * 100, 2)

    with session.transaction(conn):
        cur = conn.execute(
            """INSERT INTO recommendation_tracks(rec_id, as_of_date, price_cents,
                   pct_change, benchmark_pct, trading_days)
               VALUES (?,?,?,?,?,?)""",
            (rec_id, as_of_date, price_cents, pct, benchmark_pct, trading_days),
        )
    return dict(conn.execute("SELECT * FROM recommendation_tracks WHERE id=?",
                             (cur.lastrowid,)).fetchone())


# ---------------------------------------------------------------- 判定

def evaluate_one(conn: sqlite3.Connection, rec: dict) -> dict[str, dict]:
    """单条推荐的三个档位判定。返回 {tier: {verdict, detail}}。"""
    tracks = [dict(r) for r in conn.execute(
        """SELECT * FROM recommendation_tracks WHERE rec_id=?
            ORDER BY as_of_date ASC, id ASC""", (rec["id"],)).fetchall()]
    entry = rec["price_cents"]
    out: dict[str, dict] = {}

    if not entry or not tracks:
        return {t: {"verdict": "pending", "detail": "还没有跟踪数据"} for t in TIERS}

    priced = [t for t in tracks if t["price_cents"]]
    lowest = min((t["price_cents"] for t in priced), default=None)
    dd = round((lowest - entry) / entry * 100, 2) if lowest else None

    # 硬失误：期间最深回撤踩线就成立，不需要等到期
    if dd is None:
        out["hard_miss"] = {"verdict": "pending", "detail": "缺价格"}
    elif dd <= JUDGE["hard_miss_dd_pct"]:
        out["hard_miss"] = {"verdict": "hit",
                            "detail": f"最深回撤 {dd}%（阈值 {JUDGE['hard_miss_dd_pct']}%）"}
    else:
        out["hard_miss"] = {"verdict": "miss", "detail": f"最深回撤 {dd}%"}

    # 后面两档要等到期（45 个交易日）
    last = priced[-1] if priced else None
    days = max((t["trading_days"] or 0) for t in tracks) if tracks else 0
    matured = days >= JUDGE["days"]
    for tier in ("flat", "lag_benchmark"):
        if not matured:
            out[tier] = {"verdict": "pending",
                         "detail": f"跟踪 {days}/{JUDGE['days']} 个交易日"}
            continue
        pct = last["pct_change"] if last else None
        if pct is None:
            out[tier] = {"verdict": "pending", "detail": "缺价格"}
            continue
        if tier == "flat":
            out[tier] = ({"verdict": "hit", "detail": f"到期收益 {pct}%"}
                         if pct <= 0 else
                         {"verdict": "miss", "detail": f"到期收益 {pct}%"})
        else:
            bench = last["benchmark_pct"]
            if bench is None:
                out[tier] = {"verdict": "pending", "detail": "缺大盘数据"}
            elif pct < bench:
                out[tier] = {"verdict": "hit",
                             "detail": f"收益 {pct}% < 大盘 {bench}%"}
            else:
                out[tier] = {"verdict": "miss",
                             "detail": f"收益 {pct}% ≥ 大盘 {bench}%"}
    return out


def persist_evaluation(conn: sqlite3.Connection, rec: dict) -> list[dict]:
    """把判定结果落库。UNIQUE(rec_id, tier, judge_version) 保证一个版本只判一次。"""
    results = evaluate_one(conn, rec)
    version = rec.get("judge_version") or JUDGE_VERSION
    written = []
    with session.transaction(conn):
        for tier in TIERS:
            r = results[tier]
            exist = conn.execute(
                """SELECT * FROM recommendation_evaluations
                    WHERE rec_id=? AND tier=? AND judge_version=?""",
                (rec["id"], tier, version)).fetchone()
            if exist:
                written.append(dict(exist))
                continue
            cur = conn.execute(
                """INSERT INTO recommendation_evaluations(rec_id, tier, verdict, detail,
                       judge_version) VALUES (?,?,?,?,?)""",
                (rec["id"], tier, r["verdict"], r["detail"], version),
            )
            written.append(dict(conn.execute(
                "SELECT * FROM recommendation_evaluations WHERE id=?",
                (cur.lastrowid,)).fetchone()))
    return written


# ---------------------------------------------------------------- 统计

def stats(conn: sqlite3.Connection) -> dict:
    """四张卡的数据。

    total 是累计推荐数；每档单独给 hit / evaluated / pending，
    **不给综合成功率**。样本不足时 rate=None，前端必须显示「—」。
    """
    total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    tiers = {}
    for tier in TIERS:
        row = conn.execute(
            """SELECT
                   SUM(CASE WHEN verdict='hit' THEN 1 ELSE 0 END) AS hit,
                   SUM(CASE WHEN verdict='miss' THEN 1 ELSE 0 END) AS miss,
                   SUM(CASE WHEN verdict='pending' THEN 1 ELSE 0 END) AS pending
                 FROM recommendation_evaluations WHERE tier=?""", (tier,)).fetchone()
        hit = row["hit"] or 0
        miss = row["miss"] or 0
        pending = row["pending"] or 0
        judged = hit + miss
        tiers[tier] = {
            "label": TIER_LABELS[tier],
            "hint": TIER_HINTS[tier],
            "hit": hit,
            "miss": miss,
            "evaluated": judged,
            "pending": pending,
            "sampleEnough": judged >= MIN_SAMPLE,
            # 样本不够就是 None，不四舍五入、不给估算值
            "rate": (round(hit / judged * 100, 1) if judged >= MIN_SAMPLE else None),
            "sampleNote": (f"样本不足 {judged} / 需要 {MIN_SAMPLE}"
                           if judged < MIN_SAMPLE else ""),
        }
    return {
        "total": total,
        "minSample": MIN_SAMPLE,
        "judgeVersion": JUDGE_VERSION,
        "judge": JUDGE,
        "tiers": tiers,
        # 空态卡：命中率按评分档、维度归因都要样本 ≥10，先告诉前端能不能画
        "byScoreBand": {"available": total >= MIN_SAMPLE,
                        "note": "" if total >= MIN_SAMPLE
                        else f"样本不足 {total} / 需要 {MIN_SAMPLE}"},
        "byDimension": {"available": total >= MIN_SAMPLE,
                        "note": "" if total >= MIN_SAMPLE
                        else f"样本不足 {total} / 需要 {MIN_SAMPLE}"},
    }


def list_recs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        """SELECT r.*, s.name AS stockName
             FROM recommendations r
             LEFT JOIN stocks s ON s.code = r.code
            ORDER BY r.as_of_date DESC, r.id DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["snapshotDecoded"] = json.loads(d["snapshot"]) if d["snapshot"] else None
        except (ValueError, TypeError):
            d["snapshotDecoded"] = None
        d["tracks"] = [dict(t) for t in conn.execute(
            """SELECT * FROM recommendation_tracks WHERE rec_id=?
                ORDER BY as_of_date ASC""", (r["id"],)).fetchall()]
        d["evaluations"] = {e["tier"]: {"verdict": e["verdict"], "detail": e["detail"]}
                            for e in conn.execute(
                "SELECT * FROM recommendation_evaluations WHERE rec_id=?",
                (r["id"],)).fetchall()}
        out.append(d)
    return out


def track_all(conn: sqlite3.Connection, as_of_date: str,
              benchmark_pct: float | None = None,
              trading_days: int | None = None) -> int:
    """给所有未到期的推荐补一个跟踪点（最新价）。返回新增条数。"""
    rows = conn.execute(
        """SELECT r.id, q.price FROM recommendations r
             LEFT JOIN quotes q ON q.code = r.code
            WHERE r.as_of_date <= ?""", (as_of_date,)).fetchall()
    added = 0
    for r in rows:
        price_cents = int(round(r["price"] * 100)) if r["price"] else None
        before = conn.execute(
            "SELECT 1 FROM recommendation_tracks WHERE rec_id=? AND as_of_date=?",
            (r["id"], as_of_date)).fetchone()
        if before:
            continue
        track(conn, rec_id=r["id"], as_of_date=as_of_date, price_cents=price_cents,
              benchmark_pct=benchmark_pct, trading_days=trading_days)
        added += 1
    return added
