"""仪表盘：打开就知道今天要不要动手。"""

from __future__ import annotations

from fastapi import APIRouter

from app.db import session
from app.repo import pools as repo_pools
from app.repo import scores as repo_scores

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard() -> dict:
    conn = session.get_conn()
    return {
        "freshness": repo_pools.coverage(conn),
        "pools": repo_pools.distribution(conn),
        "pending_tuna": repo_pools.pending_tuna(conn),
        "top_scores": repo_scores.top(conn, limit=7),
        "recent_events": repo_pools.recent_events(conn, limit=10),
        "near_upgrade": repo_pools.near_upgrade(conn),
        "latest_score_date": repo_scores.latest_date(conn),
        "snapshot_count": repo_scores.count_snapshots(conn),
    }
