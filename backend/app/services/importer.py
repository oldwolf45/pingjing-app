"""现有 JSON 导入 + 导入报告。

原则：
  1. 不许静默丢弃——每个跳过的文件、每个解析失败的字段都要进报告
  2. 池子只认 tracking_state.json（config.py 的四池名单已过期，实测 20 条冲突）
  3. 文本与解析值双存（"72元" 这种自由文本直接转数字会丢信息）
  4. 只追加表用去重键判断，重复导入不产生重复行
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app import paths

# 仓位建议上限（PRD 3.3：原始 15% 必须裁剪到 10% 并标注）
POSITION_CAP = 10.0

VALID_POOLS = {"candidate", "observation", "reserve", "tuna", "removed"}

# 历史目录里格式完全不同的旧文件
LEGACY_SKIP = {
    "跟踪表_2026-09-19.json": "旧格式（更新时间/板块跟踪/个股跟踪），字段与现结构不对应，已归档不进库",
}

HISTORY_FILES = (
    "跟踪状态_2026-09-24.json",
    "跟踪状态_2026-09-25.json",
    "跟踪状态_2026-10-02.json",
)

_BACKUP_PATTERN = re.compile(r"tracking_state_backup.*\.json$", re.I)


def guess_market(code: str) -> str:
    c = str(code)
    if c.startswith(("60", "68", "9")):
        return "sh"
    if c.startswith(("0", "3", "2")):
        return "sz"
    if c.startswith(("4", "8")):
        return "bj"
    return ""


def parse_price(text: Any) -> float | None:
    """'72元' → 72.0；'-' → None。解析不出来就返回 None，由报告列出。"""
    if text is None:
        return None
    t = str(text).strip()
    if t in ("", "-", "—", "无", "None", "nan"):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None


def _dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


@dataclass
class ImportReport:
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    source_root: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    parse_failures: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    final_counts: dict = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def fail(self, where: str, field_name: str, value: Any, reason: str) -> None:
        self.parse_failures.append(
            {"位置": where, "字段": field_name, "原值": repr(value)[:80], "原因": reason}
        )

    def skip(self, file: str, reason: str) -> None:
        self.skipped.append({"文件": file, "原因": reason})

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def to_markdown(self) -> str:
        lines = [
            "# 导入报告",
            "",
            f"- 数据源：`{self.source_root}`",
            f"- 开始：{self.started_at}",
            f"- 结束：{self.finished_at or '未完成'}",
            "",
            "## 一、导入后各表实际条数",
            "",
            "| 指标 | 值 |",
            "|---|---:|",
        ]
        if self.final_counts:
            for k, v in self.final_counts.items():
                if isinstance(v, dict):
                    lines.append(f"| {k} | {', '.join(f'{a}={b}' for a, b in v.items())} |")
                else:
                    lines.append(f"| {k} | {v} |")
        else:
            lines.append("| （未统计） | 0 |")

        lines += ["", "## 一·补 导入过程计数", "", "| 项 | 数量 |", "|---|---:|"]
        shown = {k: v for k, v in self.counts.items() if not k.startswith("_")}
        for k, v in shown.items():
            lines.append(f"| {k} | {v} |")
        if not shown:
            lines.append("| （无） | 0 |")

        lines += ["", "## 二、解析失败的字段", ""]
        if self.parse_failures:
            lines += ["| 位置 | 字段 | 原值 | 原因 |", "|---|---|---|---|"]
            for f in self.parse_failures:
                lines.append(f"| {f['位置']} | {f['字段']} | `{f['原值']}` | {f['原因']} |")
        else:
            lines.append("无。所有字段均解析成功。")

        lines += ["", "## 三、跳过的文件", ""]
        if self.skipped:
            lines += ["| 文件 | 原因 |", "|---|---|"]
            for s in self.skipped:
                lines.append(f"| `{s['文件']}` | {s['原因']} |")
        else:
            lines.append("无。")

        lines += ["", "## 四、归档", ""]
        if self.archived:
            for a in self.archived:
                lines.append(f"- `{a}`")
        else:
            lines.append("无。")

        lines += ["", "## 五、提示", ""]
        if self.warnings:
            for w in self.warnings:
                lines.append(f"- {w}")
        else:
            lines.append("无。")

        return "\n".join(lines) + "\n"

    def write(self, out_dir: Path | None = None) -> Path:
        d = out_dir or paths.export_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"导入报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        tmp = f.with_suffix(".md.tmp")
        tmp.write_text(self.to_markdown(), encoding="utf-8")
        tmp.replace(f)
        return f


# ------------------------------------------------------------------
# 单表写入
# ------------------------------------------------------------------

def upsert_stock(conn, code: str, name: str, status: str = "active") -> None:
    conn.execute(
        "INSERT INTO stocks(code, name, market, status) VALUES (?,?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market",
        (code, name or code, guess_market(code), status),
    )


def upsert_profile(conn, code: str, rec: dict, report: ImportReport, where: str) -> None:
    def price(field_name: str, text_key: str) -> tuple[str | None, float | None]:
        text = rec.get(text_key)
        val = parse_price(text)
        if text not in (None, "", "-") and val is None:
            report.fail(where, text_key, text, "价格文本无法解析成数字")
        return (str(text) if text is not None else None), val

    buy_text, buy_val = price("ideal_buy_price", "ideal_buy")
    stop_text, stop_val = price("stop_loss_price", "stop_loss")
    tgt_text, tgt_val = price("target_price", "target")

    raw_pos = rec.get("position_suggest")
    try:
        raw_pos = float(raw_pos) if raw_pos is not None else None
    except (TypeError, ValueError):
        report.fail(where, "position_suggest", raw_pos, "仓位不是数字")
        raw_pos = None
    capped = raw_pos is not None and raw_pos > POSITION_CAP
    cap_val = min(raw_pos, POSITION_CAP) if raw_pos is not None else None

    conn.execute(
        """INSERT INTO stock_profiles(
                code, sector, core_logic, upgrade_conditions, downgrade_warning,
                ideal_buy_text, ideal_buy_price, stop_loss_text, stop_loss_price,
                target_text, target_price, position_suggest_raw, position_suggest_cap,
                position_capped, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))
           ON CONFLICT(code) DO UPDATE SET
                sector=COALESCE(excluded.sector, stock_profiles.sector),
                core_logic=excluded.core_logic,
                upgrade_conditions=excluded.upgrade_conditions,
                downgrade_warning=excluded.downgrade_warning,
                ideal_buy_text=excluded.ideal_buy_text,
                ideal_buy_price=excluded.ideal_buy_price,
                stop_loss_text=excluded.stop_loss_text,
                stop_loss_price=excluded.stop_loss_price,
                target_text=excluded.target_text,
                target_price=excluded.target_price,
                position_suggest_raw=excluded.position_suggest_raw,
                position_suggest_cap=excluded.position_suggest_cap,
                position_capped=excluded.position_capped""",
        (
            code, rec.get("sector"), rec.get("core_logic"), rec.get("upgrade_conditions"),
            rec.get("downgrade_warning"), buy_text, buy_val, stop_text, stop_val,
            tgt_text, tgt_val, raw_pos, cap_val, 1 if capped else 0,
        ),
    )


def upsert_pool_state(conn, code: str, rec: dict, report: ImportReport, where: str) -> None:
    pool = rec.get("current_pool") or "reserve"
    if pool not in VALID_POOLS:
        report.fail(where, "current_pool", pool, "非法的池名，按 reserve 处理")
        pool = "reserve"

    conn.execute(
        """INSERT INTO pool_states(
                code, pool, weeks_in_pool, entry_week, entry_score, price_ready,
                consecutive_weeks_above, consecutive_weeks_below, tuna_weeks,
                tuna_reason, tuna_hits, tuna_status, exit_date, exit_snapshot,
                back_weeks, back_miss, remove_reason, last_judged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
                pool=excluded.pool,
                weeks_in_pool=excluded.weeks_in_pool,
                entry_week=excluded.entry_week,
                entry_score=excluded.entry_score,
                price_ready=excluded.price_ready,
                consecutive_weeks_above=excluded.consecutive_weeks_above,
                consecutive_weeks_below=excluded.consecutive_weeks_below,
                tuna_weeks=excluded.tuna_weeks,
                tuna_reason=excluded.tuna_reason,
                tuna_hits=excluded.tuna_hits,
                tuna_status=excluded.tuna_status,
                exit_date=excluded.exit_date,
                exit_snapshot=excluded.exit_snapshot,
                back_weeks=excluded.back_weeks,
                back_miss=excluded.back_miss,
                remove_reason=excluded.remove_reason""",
        (
            code, pool, int(rec.get("weeks_in_pool") or 0), rec.get("entry_week") or None,
            rec.get("entry_score"), 1 if rec.get("price_ready", True) else 0,
            int(rec.get("consecutive_weeks_above") or 0),
            int(rec.get("consecutive_weeks_below") or 0),
            int(rec.get("tuna_weeks") or 0), rec.get("tuna_reason"),
            _dumps(rec.get("tuna_hits")), rec.get("tuna_status") or None,
            rec.get("exit_date") or None,
            _dumps(rec.get("exit_snapshot")) if rec.get("exit_snapshot") else None,
            int(rec.get("back_weeks") or 0), _dumps(rec.get("back_miss")),
            rec.get("remove_reason") or None,
            rec.get("last_updated"),
        ),
    )


def _snapshot_exists(conn, code: str, date: str, total: float) -> bool:
    row = conn.execute(
        "SELECT 1 FROM score_snapshots WHERE code=? AND as_of_date=? AND total=? LIMIT 1",
        (code, date, total),
    ).fetchone()
    return row is not None


def insert_snapshot(conn, code: str, entry: dict, report: ImportReport,
                    where: str, source: str = "history",
                    algo_version: str | None = None) -> bool:
    score = entry.get("score") or {}
    total = score.get("total", entry.get("total_score"))
    if total is None:
        report.fail(where, "score.total", entry.get("score"), "缺少总分，无法入库")
        return False

    date = entry.get("date")
    if not date:
        lu = entry.get("last_updated") or ""
        date = lu[:10] if len(lu) >= 10 else None
    if not date:
        report.fail(where, "date", entry.get("last_updated"), "缺少评分日期")
        return False

    if _snapshot_exists(conn, code, date, float(total)):
        return False

    fin = entry.get("financial") or {}
    conn.execute(
        """INSERT INTO score_snapshots(
                code, as_of_date, track_barrier, earnings_growth, profitability,
                prosperity_check, heat_position, market_cap_elasticity, total, pool,
                total_mv_yi, financial, barrier_detail, notes, source, algo_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            code, date,
            score.get("track_barrier"), score.get("earnings_growth"),
            score.get("profitability"), score.get("prosperity_check"),
            score.get("heat_position"), score.get("market_cap_elasticity"),
            float(total), entry.get("pool"),
            fin.get("total_mv"),
            _dumps(fin) if fin else None,
            _dumps(entry.get("barrier_detail")) if entry.get("barrier_detail") else None,
            _dumps(entry.get("notes")) if entry.get("notes") else None,
            source, algo_version,
        ),
    )
    report.add("score_snapshots")
    return True


def _insert_event(conn, ev: dict, report: ImportReport) -> bool:
    code = ev.get("code")
    if not code:
        report.fail("changes", "code", ev, "缺少股票代码")
        return False
    exists = conn.execute(
        "SELECT 1 FROM pool_events WHERE code=? AND from_pool=? AND to_pool=? AND change_date=? LIMIT 1",
        (code, ev.get("from_pool", ""), ev.get("to_pool", ""), ev.get("change_date", "")),
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO pool_events(code, name, from_pool, to_pool, reason, change_date) "
        "VALUES (?,?,?,?,?,?)",
        (code, ev.get("name"), ev.get("from_pool", ""), ev.get("to_pool", ""),
         ev.get("reason"), ev.get("change_date", "")),
    )
    report.add("pool_events")
    return True


# ------------------------------------------------------------------
# 各数据源导入
# ------------------------------------------------------------------

def import_tracking_state(conn, path: Path, report: ImportReport) -> dict[str, dict]:
    """主状态文件 → stocks / stock_profiles / pool_states / score_snapshots / pool_events。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    stocks = raw.get("stocks") or {}

    for code, rec in stocks.items():
        where = f"{path.name}:{code}"
        upsert_stock(conn, code, rec.get("name") or code)
        report.add("stocks")
        upsert_profile(conn, code, rec, report, where)
        report.add("stock_profiles")
        upsert_pool_state(conn, code, rec, report, where)
        report.add("pool_states")

        for e in rec.get("score_history") or []:
            insert_snapshot(conn, code, e, report, where, source="history")

        # 当前分单独一条（可能比 score_history 里最新那条还新）
        cur = rec.get("current_score")
        if cur:
            insert_snapshot(conn, code, cur, report, where, source="current")

    for ev in raw.get("changes") or []:
        _insert_event(conn, ev, report)

    report.add("changes_scanned", len(raw.get("changes") or []))
    return stocks


def import_history_dir(conn, hist_dir: Path, report: ImportReport) -> None:
    if not hist_dir.is_dir():
        report.skip(str(hist_dir), "目录不存在")
        return

    for f in sorted(hist_dir.glob("*.json")):
        if _BACKUP_PATTERN.search(f.name):
            report.skip(f.name, "backup 文件，与有效快照重复")
            continue
        if f.name in LEGACY_SKIP:
            report.skip(f.name, LEGACY_SKIP[f.name])
            continue
        if f.name not in HISTORY_FILES:
            report.skip(f.name, "不在已知的有效快照清单内")
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            report.skip(f.name, f"JSON 解析失败：{exc}")
            continue

        n = 0
        for code, rec in (raw.get("stocks") or {}).items():
            for e in rec.get("score_history") or []:
                if insert_snapshot(conn, code, e, report, f"{f.name}:{code}", source="history"):
                    n += 1
        report.add("history_snapshots_added", n)


def import_sector_map(conn, path: Path, report: ImportReport) -> None:
    """赛道映射 57 只。不在跟踪池的票建成 status='watch'，只为补全赛道归属。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    mp = raw.get("map") or {}
    for code, val in mp.items():
        if isinstance(val, str):
            sector, _intel_key = val, None
        else:
            sector, _intel_key = val.get("sector"), val.get("intel_key")

        exists = conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone()
        if not exists:
            upsert_stock(conn, code, code, status="watch")
            report.add("stocks_watch_only")
        conn.execute(
            "INSERT INTO stock_profiles(code, sector) VALUES (?,?) "
            "ON CONFLICT(code) DO UPDATE SET sector=excluded.sector",
            (code, sector),
        )
        report.add("sector_map")


def import_intel_dir(conn, intel_dir: Path, report: ImportReport) -> None:
    if not intel_dir.is_dir():
        report.skip(str(intel_dir), "目录不存在")
        return
    for f in sorted(intel_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            report.skip(f.name, f"JSON 解析失败：{exc}")
            continue
        sector = d.get("sector")
        if not sector:
            report.skip(f.name, "缺少 sector 字段")
            continue
        conn.execute(
            """INSERT INTO sector_intel(
                    sector, cr3, players_count, substitution_rate, substitution_urgency,
                    cert_cycle_months, cert_level, market_size_bn, market_growth,
                    policy_level, note, raw, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))
               ON CONFLICT(sector) DO UPDATE SET
                    cr3=excluded.cr3, players_count=excluded.players_count,
                    substitution_rate=excluded.substitution_rate,
                    substitution_urgency=excluded.substitution_urgency,
                    cert_cycle_months=excluded.cert_cycle_months,
                    cert_level=excluded.cert_level,
                    market_size_bn=excluded.market_size_bn,
                    market_growth=excluded.market_growth,
                    policy_level=excluded.policy_level,
                    note=excluded.note, raw=excluded.raw""",
            (
                sector, d.get("cr3"), d.get("players_count"), d.get("substitution_rate"),
                d.get("substitution_urgency"), d.get("cert_cycle_months"), d.get("cert_level"),
                d.get("market_size_bn"), d.get("market_growth"), d.get("policy_level"),
                d.get("_note") or d.get("note"),
                json.dumps(d, ensure_ascii=False),
            ),
        )
        report.add("sector_intel")


MANUAL_META_KEYS = {"name", "gross_margin_trend", "source", "updated", "note", "备注", "period"}


def import_manual_financial(conn, path: Path, report: ImportReport) -> None:
    if not path.exists():
        report.skip(path.name, "文件不存在")
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    for code, fields in raw.items():
        if code.startswith("_"):
            report.skip(f"{path.name}:{code}", "说明性键，不是数据")
            continue
        if not isinstance(fields, dict):
            report.fail(path.name, code, fields, "值不是对象")
            continue

        exists = conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone()
        if not exists:
            upsert_stock(conn, code, fields.get("name") or code, status="watch")
        elif fields.get("name"):
            conn.execute("UPDATE stocks SET name=? WHERE code=?", (fields["name"], code))

        for k, v in fields.items():
            if k in MANUAL_META_KEYS or isinstance(v, (dict, list)):
                # 元数据（名称/趋势标签/来源/更新日），不是财务数值，不算解析失败
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                if v is not None:
                    report.fail(f"{path.name}:{code}", k, v, "无法转为数字，已跳过")
                continue
            conn.execute(
                "INSERT INTO financial_manual(code, period, field, value) VALUES (?,?,?,?) "
                "ON CONFLICT(code, period, field) DO UPDATE SET value=excluded.value",
                (code, "latest", k, fv),
            )
            report.add("financial_manual")


def import_score_cache(conn, path: Path, report: ImportReport,
                       algo_version: str | None = None) -> None:
    """只补**跟踪池里**没有快照的票。

    缓存里有 100+ 只全市场评分，绝大多数不在池内；把它们全导进来会把 stocks 表
    从 57 撑到 145，且详情页出现一堆没人跟踪的票。所以按 pool_states 过滤。
    """
    if not path.exists():
        report.skip(path.name, "文件不存在")
        return
    cache = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    scanned = 0
    for code, rec in cache.items():
        in_pool = conn.execute("SELECT 1 FROM pool_states WHERE code=?", (code,)).fetchone()
        if not in_pool:
            scanned += 1
            continue
        has = conn.execute(
            "SELECT 1 FROM score_snapshots WHERE code=? LIMIT 1", (code,)
        ).fetchone()
        if has:
            continue

        entry = dict(rec)
        entry.setdefault("date", (rec.get("last_updated") or "")[:10] or None)
        if not entry.get("date"):
            report.fail(f"{path.name}:{code}", "date", rec, "缓存条目没有日期")
            continue
        if not entry.get("notes"):
            entry["notes"] = ["迁移补分：取自评分缓存"]
        if insert_snapshot(conn, code, entry, report, f"{path.name}:{code}",
                           source="migration_cache", algo_version=algo_version):
            added += 1
    report.add("cache_entries_not_in_pool_ignored", scanned)
    report.add("cache_snapshots_added", added)


def archive_originals(src_root: Path, items: list[Path], report: ImportReport) -> None:
    """原始文件归档到数据目录 legacy/，只读留存。"""
    dest = paths.data_dir() / "legacy"
    dest.mkdir(parents=True, exist_ok=True)
    for p in items:
        if not p.exists():
            continue
        target = dest / p.name
        if target.exists():
            continue
        try:
            shutil.copy2(p, target)
            report.archived.append(str(target))
        except OSError as exc:
            report.warn(f"归档失败 {p.name}：{exc}")


# ------------------------------------------------------------------
# 总入口
# ------------------------------------------------------------------

def run_full_import(conn, source_root: str | Path,
                    algo_version: str | None = None) -> ImportReport:
    root = Path(source_root)
    report = ImportReport(source_root=str(root))

    if not root.is_dir():
        raise FileNotFoundError(f"源目录不存在：{root}")

    state_file = root / "data" / "tracking_state.json"
    if not state_file.exists():
        raise FileNotFoundError(f"缺少主状态文件：{state_file}")

    conn.execute("BEGIN IMMEDIATE")
    try:
        import_tracking_state(conn, state_file, report)
        import_history_dir(conn, root / "历史", report)
        import_sector_map(conn, root / "config" / "sector_map.json", report)
        import_intel_dir(conn, root / "赛道数据库" / "intel", report)
        import_manual_financial(conn, root / "data" / "manual_financial.json", report)
        import_score_cache(conn, root / "data" / "full_pool_score_cache.json",
                           report, algo_version=algo_version)

        # 一致性检查：每只跟踪票必须有快照
        missing = conn.execute(
            """SELECT s.code FROM stocks s
                WHERE s.status='active'
                  AND NOT EXISTS (SELECT 1 FROM score_snapshots x WHERE x.code=s.code)"""
        ).fetchall()
        if missing:
            report.warn("以下票没有评分快照，详情页会空白：" +
                        "、".join(r["code"] for r in missing))

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    archive_candidates = [
        state_file,
        root / "config" / "sector_map.json",
        root / "data" / "manual_financial.json",
    ]
    archive_candidates += list((root / "历史").glob("*.json"))
    archive_originals(root, archive_candidates, report)

    report.final_counts = verify_import(conn)
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    return report


def verify_import(conn) -> dict:
    """对账：返回关键指标，供测试和导入报告使用。"""
    def q(sql):
        return conn.execute(sql).fetchone()[0]
    dist = {r["pool"]: r["n"] for r in conn.execute(
        "SELECT pool, COUNT(*) AS n FROM pool_states GROUP BY pool").fetchall()}
    return {
        "stocks": q("SELECT COUNT(*) FROM stocks"),
        "stocks_active": q("SELECT COUNT(*) FROM stocks WHERE status='active'"),
        "stocks_watch": q("SELECT COUNT(*) FROM stocks WHERE status='watch'"),
        "snapshots": q("SELECT COUNT(*) FROM score_snapshots"),
        "snapshot_dates": q("SELECT COUNT(DISTINCT as_of_date) FROM score_snapshots"),
        "pool_events": q("SELECT COUNT(*) FROM pool_events"),
        "pool_dist": dist,
        "in_pool": sum(dist.get(p, 0) for p in ("candidate", "observation", "reserve")),
        "sector_intel": q("SELECT COUNT(*) FROM sector_intel"),
        "profiles_with_sector": q("SELECT COUNT(*) FROM stock_profiles WHERE sector IS NOT NULL"),
        "stocks_without_snapshot": q(
            "SELECT COUNT(*) FROM stocks s WHERE s.status='active' "
            "AND NOT EXISTS (SELECT 1 FROM v_score_current v WHERE v.code=s.code)"),
    }
