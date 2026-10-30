"""股票池与个股详情（只读）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import session
from app.repo import pools as repo_pools
from app.repo import scores as repo_scores
from app.repo import stocks as repo_stocks
from app.services import plr as plr_algo
from app.services.sources import fetch_kline, fetch_quote, make_client

router = APIRouter(prefix="/api", tags=["pools"])


@router.get("/pools")
def list_pool(pool: str = Query("all", description="candidate/observation/reserve/tuna/removed/all"),
              q: str = "", sort: str = "score", desc: bool = True,
              limit: int = 500) -> dict:
    conn = session.get_conn()
    try:
        items = repo_stocks.list_stocks(conn, pool=pool, keyword=q, sort=sort, desc=desc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "pool": pool,
        "count": len(items),
        "distribution": repo_pools.distribution(conn)["counts"],
        "items": items[:limit],
    }


@router.get("/stocks/{code}")
def stock_detail(code: str) -> dict:
    conn = session.get_conn()
    base = repo_stocks.get_stock(code, conn)
    if base is None:
        raise HTTPException(status_code=404, detail=f"没有这只票：{code}")

    score = repo_scores.detail(conn, code)
    return {"stock": base, "score": score}


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 20) -> dict:
    conn = session.get_conn()
    return {"keyword": q, "items": repo_stocks.search(conn, q, limit=limit)}


@router.get("/stocks/{code}/plr")
def stock_plr(code: str) -> dict:
    """V5.0 盈亏比技术信号参考项（hermes 口径）。

    实时拉当前价 + 近 250 根日K算 PLR=(w2-price)/(price-s1)。
    与紫苏叶基本面买点/止损是两套体系，仅作参考，不参与评分与池子判定。
    数据源不可达或信号算不出时 plr 为 None，宁缺毋假。
    """
    conn = session.get_conn()
    if repo_stocks.get_stock(code, conn) is None:
        raise HTTPException(status_code=404, detail=f"没有这只票：{code}")

    client = make_client(conn)
    q = fetch_quote(client, code)
    price = (q.data or {}).get("price") if q.ok else None
    kl = fetch_kline(client, code, count=plr_algo.WINDOW + 20)
    klines = kl.data if kl.ok else []

    result = plr_algo.compute_plr(klines or [], price)
    result["code"] = code
    result["price"] = price
    result["ref_score"] = plr_algo.plr_to_score(result)  # 0~1 参考分，不计入评分引擎
    result["data_ok"] = bool(q.ok and kl.ok and price is not None)
    if not q.ok:
        result["error"] = f"行情源失败：{q.error}"
    elif not kl.ok:
        result["error"] = f"日K源失败：{kl.error}"
    return result
