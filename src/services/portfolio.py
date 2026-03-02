"""持仓管理 & 收益计算服务"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from src.models import (
    Channel, ChannelType, Holding, Transaction, TransactionType, TransactionStatus,
)
from src.services.fund import ensure_fund_exists, get_latest_nav, get_latest_nav_with_date


# ---------------------------------------------------------------------------
# 渠道管理
# ---------------------------------------------------------------------------

def ensure_channel(session: Session, code: str, name: str, channel_type: str = ChannelType.OTHER.value) -> Channel:
    ch = session.query(Channel).filter_by(code=code).first()
    if not ch:
        ch = Channel(code=code, name=name, channel_type=channel_type)
        session.add(ch)
        session.flush()
    return ch


# ---------------------------------------------------------------------------
# 交易导入
# ---------------------------------------------------------------------------

def import_transactions(session: Session, records: list[dict], channel_code: str, channel_name: str) -> int:
    """
    批量导入交易记录并更新持仓。

    records: 统一格式的交易记录列表 (由 channel.parse_transactions 输出)
    返回成功导入的记录数。
    """
    channel = ensure_channel(
        session, channel_code, channel_name,
        ChannelType.ALIPAY.value if channel_code == "alipay" else ChannelType.OTHER.value,
    )
    count = 0

    for rec in records:
        fund = ensure_fund_exists(session, rec["fund_code"], rec.get("fund_name", ""))

        txn = Transaction(
            fund_code=fund.code,
            channel_id=channel.id,
            txn_type=rec["txn_type"],
            txn_date=rec["txn_date"],
            amount=Decimal(str(rec["amount"])),
            shares=Decimal(str(rec.get("shares", 0))),
            nav=Decimal(str(rec.get("nav", 0))),
            fee=Decimal(str(rec.get("fee", 0))),
            status=rec.get("status", TransactionStatus.CONFIRMED.value),
        )
        session.add(txn)

        if txn.status == TransactionStatus.CONFIRMED.value:
            _update_holding(session, fund.code, channel.id, txn)

        count += 1

    session.commit()
    return count


def _update_holding(session: Session, fund_code: str, channel_id: int, txn: Transaction):
    """根据确认的交易更新持仓"""
    holding = (
        session.query(Holding)
        .filter_by(fund_code=fund_code, channel_id=channel_id)
        .first()
    )
    if not holding:
        holding = Holding(fund_code=fund_code, channel_id=channel_id, shares=Decimal(0), cost_amount=Decimal(0))
        session.add(holding)
        session.flush()

    if txn.txn_type in (TransactionType.BUY.value, TransactionType.BONUS.value):
        holding.shares += txn.shares
        holding.cost_amount += txn.amount
    elif txn.txn_type == TransactionType.SELL.value:
        holding.shares -= txn.shares
        holding.cost_amount -= txn.amount
    elif txn.txn_type == TransactionType.DIVIDEND.value:
        holding.cost_amount -= txn.amount  # 分红降低成本

    if holding.shares > 0:
        holding.cost_nav = (holding.cost_amount / holding.shares).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    else:
        holding.cost_nav = None


# ---------------------------------------------------------------------------
# 持仓查询 & 收益计算
# ---------------------------------------------------------------------------

@dataclass
class HoldingSummary:
    holding_id: int
    fund_code: str
    fund_name: str
    channel_name: str
    industry: str
    shares: Decimal
    cost_amount: Decimal
    cost_nav: Decimal | None
    latest_nav: Decimal | None
    latest_nav_date: str | None
    market_value: Decimal | None
    profit: Decimal | None
    profit_rate: Decimal | None  # 百分比


def get_holdings(session: Session, channel_code: str | None = None) -> list[HoldingSummary]:
    """查询持仓，可按渠道过滤"""
    query = session.query(Holding)
    if channel_code:
        ch = session.query(Channel).filter_by(code=channel_code).first()
        if ch:
            query = query.filter_by(channel_id=ch.id)

    results: list[HoldingSummary] = []
    for h in query.all():
        if h.shares <= 0 and h.cost_amount <= 0:
            continue

        latest_nav, nav_date = get_latest_nav_with_date(session, h.fund_code)
        market_value = (h.shares * latest_nav).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if latest_nav else None
        profit = (market_value - h.cost_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if market_value else None
        profit_rate = None
        if profit is not None and h.cost_amount > 0:
            profit_rate = (profit / h.cost_amount * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        results.append(HoldingSummary(
            holding_id=h.id,
            fund_code=h.fund_code,
            fund_name=h.fund.name if h.fund else h.fund_code,
            channel_name=h.channel.name if h.channel else "",
            industry=h.fund.industry or "其他" if h.fund else "其他",
            shares=h.shares,
            cost_amount=h.cost_amount,
            cost_nav=h.cost_nav,
            latest_nav=latest_nav,
            latest_nav_date=nav_date.isoformat() if nav_date else None,
            market_value=market_value,
            profit=profit,
            profit_rate=profit_rate,
        ))

    return results


def get_portfolio_summary(session: Session) -> dict:
    """获取投资组合汇总"""
    holdings = get_holdings(session)
    total_cost = sum(h.cost_amount for h in holdings)
    total_market = sum(h.market_value for h in holdings if h.market_value)
    total_profit = sum(h.profit for h in holdings if h.profit is not None)
    total_rate = None
    if total_cost > 0:
        total_rate = (total_profit / total_cost * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    return {
        "fund_count": len(holdings),
        "total_cost": total_cost,
        "total_market_value": total_market,
        "total_profit": total_profit,
        "total_profit_rate": total_rate,
        "holdings": holdings,
    }
