"""推荐追踪 API（T5.5 / T5.6）。

对外只给"新建"和"读"两个动作。改和删根本没有路由——不是没做，
是不允许做（PRD：推荐快照只追加）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import session
from app.services import recommendations

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


class CreateBody(BaseModel):
    code: str
    asOfDate: str | None = None
    thesis: str | None = None


@router.get("")
def listing(limit: int = 200) -> dict:
    conn = session.get_conn()
    return {"items": recommendations.list_recs(conn, limit),
            "stats": recommendations.stats(conn)}


@router.get("/stats")
def stats() -> dict:
    return recommendations.stats(session.get_conn())


@router.post("")
def create(body: CreateBody) -> dict:
    conn = session.get_conn()
    from datetime import date
    try:
        return recommendations.create(
            conn, code=body.code,
            as_of_date=body.asOfDate or date.today().strftime("%Y-%m-%d"),
            thesis=body.thesis)
    except recommendations.RecommendationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
