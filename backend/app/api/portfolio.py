"""持仓与交易 API（T5.2 / T5.3 / T5.4）。

录入交易的接口**不接受成本价**——成本价是算出来的，不是填的（PRD 5.4）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import session
from app.services import portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeBody(BaseModel):
    code: str
    side: str
    shares: int
    priceCents: int
    feeCents: int = 0
    tradeDate: str | None = None
    note: str | None = None
    portfolioId: int | None = None


class VoidBody(BaseModel):
    tradeId: int
    note: str | None = None
    tradeDate: str | None = None
    portfolioId: int | None = None


class CashBody(BaseModel):
    cashCents: int
    portfolioId: int | None = None


def _pid(conn, given: int | None) -> int:
    return given or portfolio.ensure_default(conn)["id"]


@router.get("")
def overview(portfolioId: int | None = None) -> dict:
    conn = session.get_conn()
    pid = _pid(conn, portfolioId)
    data = portfolio.valuation(conn, pid)
    data["trades"] = portfolio.list_trades(conn, pid, limit=200)
    data["portfolios"] = portfolio.list_portfolios(conn)
    return data


@router.post("/trades")
def add_trade(body: TradeBody) -> dict:
    conn = session.get_conn()
    pid = _pid(conn, body.portfolioId)
    try:
        trade = portfolio.add_trade(
            conn, portfolio_id=pid, code=body.code, side=body.side,
            shares=body.shares, price_cents=body.priceCents,
            fee_cents=body.feeCents, trade_date=body.tradeDate, note=body.note)
    except portfolio.PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trade": trade, "valuation": portfolio.valuation(conn, pid)}


@router.post("/void")
def void_trade(body: VoidBody) -> dict:
    conn = session.get_conn()
    pid = _pid(conn, body.portfolioId)
    try:
        trade = portfolio.void_trade(
            conn, portfolio_id=pid, trade_id=body.tradeId,
            trade_date=body.tradeDate, note=body.note)
    except portfolio.PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trade": trade, "valuation": portfolio.valuation(conn, pid)}


@router.post("/cash")
def set_cash(body: CashBody) -> dict:
    conn = session.get_conn()
    pid = _pid(conn, body.portfolioId)
    try:
        portfolio.set_cash(conn, pid, body.cashCents)
    except portfolio.PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return portfolio.valuation(conn, pid)
