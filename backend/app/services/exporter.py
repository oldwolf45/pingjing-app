"""导出（DEV_PLAN T5.10）。

两个格式，各自解决一个问题：
  JSON  完整备份视角——所有表、所有字段，给"换机器/给维护者看"用
  CSV   给人看/给人算——每张表一个文件，Excel 双击能开

CSV 的三个坑，这里都处理了：
  1. **编码**：Excel 在中文 Windows 上默认按 GBK 猜，UTF-8 会变乱码。
     写 UTF-8 **带 BOM**（utf-8-sig），Excel 才认得。
  2. **公式注入**：以 = + - @ 开头的字符串会被 Excel 当公式执行。
     前面加个单引号，宁可显示难看一点也不执行。
  3. **换行和逗号**：字段里有逗号/引号/换行必须整体加引号，这是 csv 模块的事，
     但它不会帮你处理公式注入和前导零，所以数字列保持原样、文本列做转义。

导出写到数据目录下的 exports/，文件名带时间戳，不覆盖历史。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app import paths
from app import legacy_bridge
from app.db import session
from app.services.migrator import TARGET_VERSION

# 导出顺序：先业务、后运维，人打开文件夹时先看到重要的
EXPORT_TABLES = (
    "stocks", "stock_profiles", "pool_states", "pool_events",
    "score_snapshots", "barrier_snapshots", "quotes",
    "financial_periods", "financial_manual", "sector_intel",
    "recommendations", "recommendation_tracks", "recommendation_evaluations",
    "portfolios", "trades", "decision_notes",
    "source_calls",
)

FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


from app.exceptions import OperationError  # noqa: E402


class ExportError(OperationError):
    pass


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _target_dir(out_dir: Path | None) -> Path:
    d = Path(out_dir) if out_dir else paths.export_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.OperationalError:
        return []


def _safe_cell(value: Any) -> Any:
    """文本里以 = + - @ 开头的，前面加单引号，避免 Excel 当公式跑。"""
    if value is None or isinstance(value, (int, float)):
        return value
    s = str(value)
    if s.startswith(FORMULA_PREFIX):
        return "'" + s
    return s


def export_json(conn: sqlite3.Connection, out_dir: Path | None = None,
                tables: Iterable[str] | None = None) -> dict:
    d = _target_dir(out_dir)
    data = {}
    for t in (tables or EXPORT_TABLES):
        data[t] = _rows(conn, t)
    payload = {
        "exportedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app": "紫苏叶策略",
        "schemaVersion": session.get_schema_version(conn),
        "targetVersion": TARGET_VERSION,
        "algoVersion": legacy_bridge.algo_version(),
        "tables": {t: len(rows) for t, rows in data.items()},
        "data": data,
    }
    path = d / f"紫苏叶导出-{_stamp()}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return {"path": str(path), "bytes": path.stat().st_size,
            "tables": payload["tables"], "format": "json"}


def export_csv(conn: sqlite3.Connection, out_dir: Path | None = None,
               tables: Iterable[str] | None = None) -> dict:
    d = _target_dir(out_dir)
    folder = d / f"紫苏叶导出-{_stamp()}"
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for t in (tables or EXPORT_TABLES):
        rows = _rows(conn, t)
        counts[t] = len(rows)
        path = folder / f"{t}.csv"
        cols: list[str] = []
        seen = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        # utf-8-sig：带 BOM，Excel 才不会把中文读成乱码
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([_safe_cell(r.get(c)) for c in cols])
    return {"path": str(folder), "files": len(counts),
            "rows": counts, "format": "csv"}


def export(conn: sqlite3.Connection, fmt: str = "json",
           out_dir: Path | None = None) -> dict:
    if fmt == "json":
        return export_json(conn, out_dir)
    if fmt == "csv":
        return export_csv(conn, out_dir)
    raise ExportError("只支持 json 或 csv")


def versions(conn: sqlite3.Connection) -> dict:
    """设置页要的三个版本号。"""
    return {
        "app": "0.1.0",
        "schema": session.get_schema_version(conn),
        "schemaTarget": TARGET_VERSION,
        "algo": legacy_bridge.algo_version(),
        "dataDir": str(paths.data_dir()),
        "dbPath": str(paths.db_path()),
        "backupDir": str(paths.backup_dir()),
        "exportDir": str(paths.export_dir()),
        "logDir": str(paths.log_dir()),
    }
