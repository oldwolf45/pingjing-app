"""健康检查与系统信息。"""

from __future__ import annotations

from fastapi import APIRouter

from app.db import session
from app import legacy_bridge

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict:
    """健康检查。返回 200 表示服务活着。"""
    return {"ok": True, "status": "ok"}


@router.get("/api/info")
def info() -> dict:
    """系统信息：版本、数据库状态、算法完整性。"""
    conn = session.get_conn()
    algo_integ = legacy_bridge.verify_integrity()
    db_ok = True
    schema_version = None
    try:
        schema_version = session.meta_get(conn, "schema_version", "0")
    except Exception:
        db_ok = False

    return {
        "ok": db_ok,
        "database": {
            "ok": db_ok,
            "schema_version": schema_version,
        },
        "algo": {
            "ok": algo_integ["ok"],
            "version": algo_integ["version"],
            "problems": algo_integ["problems"],
        },
    }
