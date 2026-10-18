"""运维类 API：数据源健康（T5.7）、设置（T5.8）、导出（T5.10）、补录（T5.9）。"""

from __future__ import annotations

import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import paths
from app.db import session
from app.services import backup, exporter, manual_entry
from app.services import source_health as health
from app.services.sources import make_client

router = APIRouter(prefix="/api/ops", tags=["ops"])


# ---------------------------------------------------------------- 数据源

@router.get("/sources")
def sources() -> dict:
    return health.sources(session.get_conn())


class ToggleBody(BaseModel):
    name: str
    enabled: bool


@router.post("/sources/toggle")
def toggle(body: ToggleBody) -> dict:
    return health.set_enabled(session.get_conn(), body.name, body.enabled)


class TestBody(BaseModel):
    name: str
    code: str = health.PROBE_CODE


@router.post("/sources/test")
def test_source(body: TestBody) -> dict:
    conn = session.get_conn()
    return health.test_source(body.name, conn=conn, client=make_client(conn),
                              code=body.code)


# ---------------------------------------------------------------- 设置

@router.get("/settings")
def settings() -> dict:
    conn = session.get_conn()
    return {
        "versions": exporter.versions(conn),
        "backups": backup.list_backups()[:10],
        "privacy": {
            "localOnly": True,
            "text": "所有数据都存在你自己的电脑上，不上传、不联网同步、不收集任何使用信息。",
        },
    }


@router.post("/backup")
def do_backup() -> dict:
    conn = session.get_conn()
    return backup.create_backup("manual", reason="用户手动备份", conn=conn)


class RestoreBody(BaseModel):
    id: str


@router.post("/restore")
def do_restore(body: RestoreBody) -> dict:
    return backup.restore_backup(body.id)


@router.post("/open-folder")
def open_folder(which: str = "data") -> dict:
    """打开数据目录。桌面版才有效，网页里点了没反应是正常的。"""
    target = {
        "data": paths.data_dir(), "backup": paths.backup_dir(),
        "export": paths.export_dir(), "log": paths.log_dir(),
    }.get(which, paths.data_dir())
    target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))        # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])       # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(target)])   # noqa: S603,S607
        return {"ok": True, "path": str(target)}
    except Exception as exc:
        return {"ok": False, "path": str(target), "error": str(exc)}


# ---------------------------------------------------------------- 导出

@router.post("/export")
def do_export(fmt: str = "json") -> dict:
    conn = session.get_conn()
    try:
        return exporter.export(conn, fmt)
    except exporter.ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------- 添加标的

class AddStockBody(BaseModel):
    code: str
    name: str
    sector: str | None = None
    totalScore: float | None = None
    note: str | None = None
    # 故意留一个字段接住前端的误传：传了也不生效，进池落点由后端硬编码
    forcePool: str | None = None


@router.post("/stocks")
def add_stock(body: AddStockBody) -> dict:
    conn = session.get_conn()
    from datetime import date
    try:
        return manual_entry.add_stock(
            conn, code=body.code, name=body.name, sector=body.sector or "",
            total_score=body.totalScore, today=date.today().strftime("%Y-%m-%d"),
            note=body.note,
        )
    except manual_entry.ManualEntryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
