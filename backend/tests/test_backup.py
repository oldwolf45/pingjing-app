"""备份与恢复：VACUUM INTO、sha256、保留策略、恢复一致性。"""

from __future__ import annotations

import sqlite3

import pytest

from app import paths
from app.db import session
from app.services import backup


def _seed(conn, n=5):
    for i in range(n):
        conn.execute("INSERT OR IGNORE INTO stocks(code, name) VALUES (?,?)",
                     (f"30{i:04d}", f"票{i}"))
        conn.execute(
            "INSERT INTO score_snapshots(code, as_of_date, total) VALUES (?, '2026-10-17', ?)",
            (f"30{i:04d}", 5.0 + i),
        )
    conn.commit()


def test_backup_creates_verified_file(conn):
    _seed(conn)
    item = backup.create_backup("manual", reason="单测", conn=conn)

    f = paths.backup_dir() / "manual" / item["file"]
    assert f.exists(), "备份文件没生成"
    assert item["verified"] is True
    assert len(item["sha256"]) == 64
    assert item["size"] > 0

    c = sqlite3.connect(str(f))
    try:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 5
    finally:
        c.close()


def test_backup_written_to_index(conn):
    _seed(conn)
    item = backup.create_backup("manual", conn=conn)
    items = backup.list_backups()
    assert any(i["id"] == item["id"] for i in items)
    index_file = paths.backup_dir() / "index.json"
    assert index_file.exists()


def test_backup_requires_existing_db(data_home):
    with pytest.raises(FileNotFoundError):
        backup.create_backup("manual")


def test_restore_brings_data_back(conn):
    _seed(conn, 5)
    item = backup.create_backup("manual", conn=conn)

    # 备份之后再写入 3 条
    for i in range(5, 8):
        conn.execute("INSERT OR IGNORE INTO stocks(code, name) VALUES (?,?)",
                     (f"30{i:04d}", f"票{i}"))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 8

    backup.restore_backup(item["id"])
    session.reset_connections()

    c2 = session.connect()
    assert c2.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 5, "恢复后应回到备份时点"
    c2.close()


def test_restore_rejects_tampered_file(conn):
    _seed(conn)
    item = backup.create_backup("manual", conn=conn)
    f = paths.backup_dir() / "manual" / item["file"]
    # 篡改任意一个字节
    with open(f, "r+b") as fh:
        fh.seek(200)
        b = fh.read(1)
        fh.seek(200)
        fh.write(bytes([b[0] ^ 0xFF]))

    with pytest.raises(RuntimeError) as ei:
        backup.restore_backup(item["id"])
    assert "sha256" in str(ei.value)


def test_prune_keeps_newest(conn):
    _seed(conn, 2)
    ids = []
    for i in range(backup.KEEP["daily"] + 3):
        item = backup.create_backup("daily", reason=f"第{i}次", conn=conn)
        ids.append(item["id"])

    left = backup.list_backups("daily")
    assert len(left) == backup.KEEP["daily"], f"应保留 7 份，实际 {len(left)}"
    assert left[0]["id"] == ids[-1], "最新一份被删了"
    # 文件也要真的删掉
    files = list((paths.backup_dir() / "daily").glob("*.db"))
    assert len(files) == backup.KEEP["daily"]


def test_manual_never_pruned(conn):
    _seed(conn, 1)
    for _ in range(12):
        backup.create_backup("manual", conn=conn)
    assert len(backup.list_backups("manual")) == 12


def test_latest_good(conn):
    _seed(conn, 1)
    a = backup.create_backup("daily", conn=conn)
    b = backup.create_backup("weekly", conn=conn)
    lg = backup.latest_good()
    assert lg is not None
    assert lg["id"] in {a["id"], b["id"]}


def test_backup_does_not_lock_source(conn):
    """备份后源库还能继续写（VACUUM INTO 不应留锁）。"""
    _seed(conn, 1)
    backup.create_backup("manual", conn=conn)
    conn.execute("INSERT OR IGNORE INTO stocks(code, name) VALUES ('999999','测试')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 2
