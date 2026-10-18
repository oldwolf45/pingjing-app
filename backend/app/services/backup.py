"""备份服务：VACUUM INTO（不是复制文件）+ sha256 索引 + 分层保留。

为什么必须 VACUUM INTO：
  WAL 模式下，数据还在 -wal 文件里没落盘，直接 copy .db 会拿到一个缺事务的坏库。
  VACUUM INTO 会先做一致性快照再写出，单文件、已整理、可直接打开。

保留策略（架构 5.3）：
  daily 7 / weekly 8 / pre_migration 5 / manual 不限
  **永不自动删除最近一次成功的备份**
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from app import paths

KEEP = {
    "daily": 7,
    "weekly": 8,
    "pre_migration": 5,
    "app": 5,          # 启动自检自动备份
    "manual": None,    # 手动备份不自动清理
}

INDEX_NAME = "index.json"


def _backup_root() -> Path:
    return paths.backup_dir()


def _kind_dir(kind: str) -> Path:
    d = _backup_root() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_index() -> list[dict]:
    p = _backup_root() / INDEX_NAME
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(items: list[dict]) -> None:
    p = _backup_root() / INDEX_NAME
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def create_backup(kind: str = "manual", reason: str = "", db_file: str | Path | None = None,
                  conn: sqlite3.Connection | None = None) -> dict:
    """生成一个备份文件并登记到 index.json。返回索引条目。"""
    if kind not in KEEP:
        raise ValueError(f"未知备份类型：{kind}")

    src = Path(db_file) if db_file else paths.db_path()
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在：{src}")

    # WAL 落盘：保证后续快照能读到全部已提交事务
    if conn is not None:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:
            pass

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = _kind_dir(kind) / f"zisuye_{kind}_{stamp}.db"
    seq = 2
    while target.exists():
        target = _kind_dir(kind) / f"zisuye_{kind}_{stamp}_{seq}.db"
        seq += 1

    con = conn or sqlite3.connect(str(src))
    try:
        # VACUUM INTO 不接受已存在的目标文件
        if target.exists():
            target.unlink()
        con.execute("VACUUM INTO ?", (str(target),))
    finally:
        if conn is None:
            con.close()

    # 备份本身必须能打开且完整，否则等于没备份
    chk = sqlite3.connect(str(target))
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if ok != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"备份完整性检查失败：{ok}")

    item = {
        "id": target.stem,
        "kind": kind,
        "file": str(target.name),
        "dir": kind,
        "size": target.stat().st_size,
        "sha256": sha256_file(target),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": reason,
        "verified": True,
    }
    items = load_index()
    # 同一秒内可能连续备份多次，created_at 精度不够，用单调 seq 保证顺序稳定
    item["seq"] = max((i.get("seq", 0) for i in items), default=0) + 1
    items.append(item)
    _save_index(items)
    prune(kind)
    return item


def _sort_key(x: dict) -> tuple:
    return (x.get("created_at", ""), x.get("seq", 0))


def list_backups(kind: str | None = None) -> list[dict]:
    items = load_index()
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    return sorted(items, key=_sort_key, reverse=True)


def prune(kind: str) -> list[str]:
    """按保留份数清理旧备份。永不删最近一次成功备份。"""
    keep_n = KEEP.get(kind)
    removed: list[str] = []
    if keep_n is None:
        return removed

    items = load_index()
    same = sorted([i for i in items if i.get("kind") == kind], key=_sort_key, reverse=True)
    if len(same) <= keep_n:
        return removed

    newest = same[0]
    victims = [i for i in same[keep_n:] if i["id"] != newest["id"]]
    victim_ids = {v["id"] for v in victims}
    for v in victims:
        f = _kind_dir(kind) / v["file"]
        try:
            f.unlink(missing_ok=True)
        except OSError:
            continue
        removed.append(v["id"])

    kept = [i for i in items if i["id"] not in victim_ids]
    _save_index(kept)
    return removed


def restore_backup(backup_id: str, db_file: str | Path | None = None) -> dict:
    """用备份覆盖当前库。覆盖前先把当前库另存为 app 类备份，避免回滚不可逆。"""
    items = load_index()
    item = next((i for i in items if i["id"] == backup_id), None)
    if item is None:
        raise KeyError(f"找不到备份：{backup_id}")

    src = _kind_dir(item["kind"]) / item["file"]
    if not src.exists():
        raise FileNotFoundError(f"备份文件已丢失：{src}")

    if sha256_file(src) != item.get("sha256"):
        raise RuntimeError("备份文件校验失败（sha256 不一致），拒绝恢复")

    target = Path(db_file) if db_file else paths.db_path()

    # 恢复前先给现状留个底
    safety = None
    if target.exists():
        safety = create_backup(kind="app", reason=f"恢复前自动备份（即将恢复到 {backup_id}）",
                               db_file=target)

    # 用 sqlite3 backup API 而不是文件复制：目标库可能正被连接持有
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(target))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()

    # WAL 文件必须清掉，否则旧的 WAL 会盖回新数据
    for suffix in ("-wal", "-shm"):
        p = Path(str(target) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    return {"restored": item, "safety_backup": safety}


def latest_good() -> dict | None:
    """最近一次成功的备份（用于"一键还原"默认选项）。"""
    items = [i for i in load_index() if i.get("verified")]
    if not items:
        return None
    return sorted(items, key=_sort_key, reverse=True)[0]
