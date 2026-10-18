"""更新任务 API：起一轮更新、查进度、取消。

前端进度面板（UpdatePanel.vue）只认这三个接口。
取消不在这个接口里等结果——立刻返回，前端继续轮询到 status=cancelled 为止。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import update_job

router = APIRouter(prefix="/api/update", tags=["update"])


class StartBody(BaseModel):
    kind: str = "manual"
    asOfDate: str | None = None
    codes: list[str] | None = None


@router.post("/start")
def start(body: StartBody | None = None) -> dict:
    b = body or StartBody()
    return update_job.manager.start(kind=b.kind, as_of_date=b.asOfDate, codes=b.codes)


@router.get("/status")
def status() -> dict:
    return update_job.manager.status()


@router.post("/cancel")
def cancel() -> dict:
    return update_job.manager.cancel()
