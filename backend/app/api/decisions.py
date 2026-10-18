"""决策清单 API（T5.1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import session
from app.services import decisions

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class SetBody(BaseModel):
    code: str
    decision: str
    note: str | None = None
    week: str | None = None


@router.get("")
def week(week: str | None = None) -> dict:
    conn = session.get_conn()
    return decisions.week_list(conn, week)


@router.post("")
def set_decision(body: SetBody) -> dict:
    conn = session.get_conn()
    try:
        return decisions.set_decision(
            conn, code=body.code, week=body.week or decisions.week_key(),
            decision=body.decision, note=body.note)
    except decisions.DecisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/weeks")
def weeks(limit: int = 12) -> dict:
    conn = session.get_conn()
    return {"items": decisions.weeks_with_notes(conn, limit)}


@router.get("/options")
def options() -> dict:
    return {"items": [{"value": k, "label": v}
                      for k, v in decisions.DECISION_LABELS.items()],
            "currentWeek": decisions.week_key()}
