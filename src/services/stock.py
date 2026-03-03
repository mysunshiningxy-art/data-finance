"""股票数据服务 — 股票信息查询 & 行情获取

使用 akshare 从东方财富等公开数据源获取股票信息和历史行情。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import akshare as ak
import pandas as pd
from sqlalchemy.orm import Session

from src.models import (
    Stock, StockPrice, StockHolding, StockTransaction,
    StockMarket, Channel, TransactionType, TransactionStatus,
)


def _detect_market(code: str) -> str:
    if code.startswith("6"):
        return StockMarket.SH.value
    if code.startswith(("0", "3")):
        return StockMarket.SZ.value
    if code.startswith(("4", "8")):
        return StockMarket.BJ.value
    return StockMarket.SH.value


def fetch_stock_info(stock_code: str) -> dict | None:
    """从公开数据源获取股票基本信息"""
    try:
        df = ak.stock_individual_info_em(symbol=stock_code)
        if df is None or df.empty:
            return None
        data = dict(zip(df["item"], df["value"]))
        return {
            "code": stock_code,
            "name": data.get("股票简称", ""),
            "market": _detect_market(stock_code),
            "industry": data.get("行业", ""),
        }
    except Exception:
        return None


def ensure_stock_exists(session: Session, stock_code: str, stock_name: str = "") -> Stock:
    """确保股票记录存在"""
    stock = session.get(Stock, stock_code)
    if stock:
        return stock

    info = fetch_stock_info(stock_code)
    if info:
        stock = Stock(**info)
    else:
        stock = Stock(
            code=stock_code,
            name=stock_name or stock_code,
            market=_detect_market(stock_code),
        )
    session.add(stock)
    session.flush()
    return stock


def sync_stock_price(
    session: Session,
    stock_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> int:
    """同步股票历史行情到数据库"""
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=60)

    df = None
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )
    except Exception:
        pass

    # Fallback: try ETF hist API for ETF codes (5xxxxx / 1xxxxx)
    if (df is None or df.empty) and stock_code[:1] in ("5", "1"):
        try:
            df = ak.fund_etf_hist_em(
                symbol=stock_code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception:
            pass

    if df is None or df.empty:
        return 0

    count = 0
    for _, row in df.iterrows():
        d = pd.Timestamp(row["日期"]).date()
        existing = (
            session.query(StockPrice)
            .filter_by(stock_code=stock_code, price_date=d)
            .first()
        )
        if existing:
            continue
        session.add(StockPrice(
            stock_code=stock_code,
            price_date=d,
            open=Decimal(str(row["开盘"])),
            close=Decimal(str(row["收盘"])),
            high=Decimal(str(row["最高"])),
            low=Decimal(str(row["最低"])),
            volume=int(row["成交量"]) if pd.notna(row["成交量"]) else None,
            change_pct=Decimal(str(row["涨跌幅"])) if pd.notna(row["涨跌幅"]) else None,
        ))
        count += 1

    session.commit()
    return count


def get_latest_price(session: Session, stock_code: str) -> Decimal | None:
    rec = (
        session.query(StockPrice)
        .filter_by(stock_code=stock_code)
        .order_by(StockPrice.price_date.desc())
        .first()
    )
    return rec.close if rec else None


def get_latest_price_with_date(session: Session, stock_code: str) -> tuple[Decimal | None, date | None]:
    rec = (
        session.query(StockPrice)
        .filter_by(stock_code=stock_code)
        .order_by(StockPrice.price_date.desc())
        .first()
    )
    if rec:
        return rec.close, rec.price_date
    return None, None


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

@dataclass
class StockHoldingSummary:
    holding_id: int
    stock_code: str
    stock_name: str
    market: str
    channel_name: str
    industry: str
    shares: Decimal
    cost_amount: Decimal
    cost_price: Decimal | None
    latest_price: Decimal | None
    latest_price_date: str | None
    market_value: Decimal | None
    profit: Decimal | None
    profit_rate: Decimal | None


def get_stock_holdings(session: Session, channel_code: str | None = None) -> list[StockHoldingSummary]:
    query = session.query(StockHolding)
    if channel_code:
        ch = session.query(Channel).filter_by(code=channel_code).first()
        if ch:
            query = query.filter_by(channel_id=ch.id)

    results: list[StockHoldingSummary] = []
    for h in query.all():
        if h.shares <= 0 and h.cost_amount == 0:
            continue

        latest_price, price_date = get_latest_price_with_date(session, h.stock_code)
        market_value = (h.shares * latest_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if latest_price else None
        profit = (market_value - h.cost_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if market_value else None
        profit_rate = None
        if profit is not None and h.cost_amount != 0:
            profit_rate = (profit / abs(h.cost_amount) * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        results.append(StockHoldingSummary(
            holding_id=h.id,
            stock_code=h.stock_code,
            stock_name=h.stock.name if h.stock else h.stock_code,
            market=h.stock.market if h.stock else "",
            channel_name=h.channel.name if h.channel else "",
            industry=h.stock.industry or "其他" if h.stock else "其他",
            shares=h.shares,
            cost_amount=h.cost_amount,
            cost_price=h.cost_price,
            latest_price=latest_price,
            latest_price_date=price_date.isoformat() if price_date else None,
            market_value=market_value,
            profit=profit,
            profit_rate=profit_rate,
        ))

    return results


def get_stock_summary(session: Session) -> dict:
    holdings = get_stock_holdings(session)
    total_cost = sum(h.cost_amount for h in holdings)
    total_market = sum(h.market_value for h in holdings if h.market_value)
    total_profit = total_market - total_cost if total_market else Decimal(0)
    total_rate = (total_profit / total_cost * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) if total_cost > 0 else Decimal(0)

    return {
        "stock_count": len(holdings),
        "total_cost": total_cost,
        "total_market_value": total_market,
        "total_profit": total_profit,
        "total_profit_rate": total_rate,
        "holdings": holdings,
    }


def ensure_channel(session: Session, code: str, name: str) -> Channel:
    ch = session.query(Channel).filter_by(code=code).first()
    if ch:
        return ch
    ch = Channel(code=code, name=name)
    session.add(ch)
    session.flush()
    return ch


def add_stock_transaction(
    session: Session,
    stock_code: str,
    channel_code: str,
    channel_name: str,
    txn_type: str,
    txn_date: date,
    price: Decimal,
    shares: Decimal,
    fee: Decimal = Decimal(0),
    note: str = "",
) -> StockTransaction:
    """添加股票交易并更新持仓"""
    stock = ensure_stock_exists(session, stock_code)
    channel = ensure_channel(session, channel_code, channel_name)
    amount = (price * shares).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    txn = StockTransaction(
        stock_code=stock_code,
        channel_id=channel.id,
        txn_type=txn_type,
        txn_date=txn_date,
        price=price,
        shares=shares,
        amount=amount,
        fee=fee,
        status=TransactionStatus.CONFIRMED.value,
        note=note,
    )
    session.add(txn)

    holding = (
        session.query(StockHolding)
        .filter_by(stock_code=stock_code, channel_id=channel.id)
        .first()
    )
    if not holding:
        holding = StockHolding(stock_code=stock_code, channel_id=channel.id)
        session.add(holding)
        session.flush()

    if txn_type == TransactionType.BUY.value:
        holding.shares += shares
        holding.cost_amount += amount + fee
    elif txn_type == TransactionType.SELL.value:
        holding.shares -= shares
        holding.cost_amount -= amount - fee
        if holding.cost_amount < 0:
            holding.cost_amount = Decimal(0)

    if holding.shares > 0 and holding.cost_amount > 0:
        holding.cost_price = (holding.cost_amount / holding.shares).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    session.commit()
    return txn
