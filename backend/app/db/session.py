"""数据库连接：WAL + 完整同步 + 外键 + busy_timeout。

单文件 SQLite，不做连接池（单进程本地应用，并发量极低）。
每个线程一个连接（sqlite3 默认 check_same_thread=True，FastAPI 线程池会换线程）。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app import paths

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA_VERSION = 1

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = FULL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
)


def connect(db_file: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_file) if db_file else paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    for p in PRAGMAS:
        conn.execute(p)
    return conn


def get_conn(db_file: str | Path | None = None) -> sqlite3.Connection:
    """线程内复用连接。"""
    key = str(db_file) if db_file else "default"
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = {}
        _local.conns = cache
    conn = cache.get(key)
    if conn is None:
        conn = connect(db_file)
        cache[key] = conn
    return conn


def reset_connections() -> None:
    """测试用：关掉当前线程所有连接。"""
    cache = getattr(_local, "conns", None) or {}
    for conn in cache.values():
        try:
            conn.close()
        except Exception:
            pass
    _local.conns = {}


@contextmanager
def transaction(conn: sqlite3.Connection | None = None):
    """显式事务。写入一律走这里，保证原子。

    连接上已经有未提交事务时（调用方先 INSERT 再进来），用 SAVEPOINT 而非
    BEGIN —— SQLite 不允许事务套事务，直接 BEGIN 会抛
    "cannot start a transaction within a transaction"。用保存点后，
    内层失败只回滚内层，外层调用方的工作不受牵连。
    """
    c = conn or get_conn()
    with _write_lock:
        nested = c.in_transaction
        sp = "sp_nested_tx"
        if nested:
            c.execute(f"SAVEPOINT {sp}")
        else:
            c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            if nested:
                c.execute(f"RELEASE {sp}")
            else:
                c.execute("COMMIT")
        except Exception:
            try:
                if nested:
                    c.execute(f"ROLLBACK TO {sp}")
                    c.execute(f"RELEASE {sp}")
                else:
                    c.execute("ROLLBACK")
            except Exception:
                pass
            raise


@contextmanager
def readonly(conn: sqlite3.Connection | None = None):
    c = conn or get_conn()
    yield c


def table_names(conn: sqlite3.Connection | None = None) -> list[str]:
    c = conn or get_conn()
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def view_names(conn: sqlite3.Connection | None = None) -> list[str]:
    c = conn or get_conn()
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def quick_check(conn: sqlite3.Connection | None = None) -> str:
    c = conn or get_conn()
    row = c.execute("PRAGMA quick_check").fetchone()
    return row[0] if row else "unknown"


def integrity_check(conn: sqlite3.Connection | None = None) -> str:
    c = conn or get_conn()
    row = c.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "unknown"


def journal_mode(conn: sqlite3.Connection | None = None) -> str:
    c = conn or get_conn()
    return c.execute("PRAGMA journal_mode").fetchone()[0]


def get_schema_version(conn: sqlite3.Connection | None = None) -> int:
    c = conn or get_conn()
    try:
        row = c.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["v"]) if row and row["v"] is not None else 0


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """空库初始化：执行 schema.sql。已存在的表/视图/触发器都是 IF NOT EXISTS。"""
    c = conn or get_conn()
    sql_file = Path(__file__).resolve().parent / "schema.sql"
    sql = sql_file.read_text(encoding="utf-8")
    with _write_lock:
        c.executescript(sql)
        c.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name) VALUES (?, ?)",
            (SCHEMA_VERSION, "001_init"),
        )
        c.commit()


def meta_get(key: str, default: str | None = None, conn: sqlite3.Connection | None = None) -> str | None:
    c = conn or get_conn()
    row = c.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_conn()
    with _write_lock:
        c.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        c.commit()


def setting_get(key: str, default: str | None = None, conn: sqlite3.Connection | None = None) -> str | None:
    c = conn or get_conn()
    row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def setting_set(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_conn()
    with _write_lock:
        c.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        c.commit()


def audit(action: str, target: str = "", detail: str | None = None, actor: str = "app",
          conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_conn()
    with _write_lock:
        c.execute(
            "INSERT INTO audit_log(actor, action, target, detail) VALUES (?,?,?,?)",
            (actor, action, target, detail),
        )
        c.commit()
