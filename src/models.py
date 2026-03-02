from datetime import datetime, date
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    String, Numeric, Date, DateTime, Enum, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ChannelType(str, PyEnum):
    ALIPAY = "alipay"
    TIANTIAN = "tiantian"
    DIRECT = "direct"  # 直销
    OTHER = "other"


class FundType(str, PyEnum):
    STOCK = "stock"        # 股票型
    BOND = "bond"          # 债券型
    HYBRID = "hybrid"      # 混合型
    MONEY = "money"        # 货币型
    INDEX = "index"        # 指数型
    QDII = "qdii"
    OTHER = "other"


class TransactionType(str, PyEnum):
    BUY = "buy"            # 买入
    SELL = "sell"          # 卖出
    DIVIDEND = "dividend"  # 分红
    BONUS = "bonus"        # 红利再投


class TransactionStatus(str, PyEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Fund(Base):
    """基金基本信息"""
    __tablename__ = "funds"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    fund_type: Mapped[str] = mapped_column(String(20), default=FundType.OTHER.value)
    industry: Mapped[str | None] = mapped_column(String(30))
    manager: Mapped[str | None] = mapped_column(String(50))
    company: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    nav_records: Mapped[list["NAVRecord"]] = relationship(back_populates="fund")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="fund")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="fund")


class NAVRecord(Base):
    """基金净值记录"""
    __tablename__ = "nav_records"
    __table_args__ = (
        UniqueConstraint("fund_code", "nav_date", name="uq_nav"),
        Index("idx_nav_fund_date", "fund_code", "nav_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String(10), ForeignKey("funds.code"))
    nav_date: Mapped[date] = mapped_column(Date)
    nav: Mapped[Decimal] = mapped_column(Numeric(10, 4))          # 单位净值
    acc_nav: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))  # 累计净值
    daily_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))  # 日涨幅 %

    fund: Mapped["Fund"] = relationship(back_populates="nav_records")


class Channel(Base):
    """渠道"""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    channel_type: Mapped[str] = mapped_column(
        String(20), default=ChannelType.OTHER.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    holdings: Mapped[list["Holding"]] = relationship(back_populates="channel")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="channel")


class Holding(Base):
    """持仓记录 — 每个渠道每只基金一条"""
    __tablename__ = "holdings"
    __table_args__ = (
        UniqueConstraint("fund_code", "channel_id", name="uq_holding"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String(10), ForeignKey("funds.code"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    shares: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)        # 持有份额
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)    # 累计投入成本
    cost_nav: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))           # 持仓成本净值
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    fund: Mapped["Fund"] = relationship(back_populates="holdings")
    channel: Mapped["Channel"] = relationship(back_populates="holdings")


class Transaction(Base):
    """交易记录"""
    __tablename__ = "transactions"
    __table_args__ = (
        Index("idx_txn_fund", "fund_code"),
        Index("idx_txn_date", "txn_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String(10), ForeignKey("funds.code"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    txn_type: Mapped[str] = mapped_column(String(20))
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))       # 交易金额
    shares: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))  # 确认份额
    nav: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))     # 确认净值
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=0) # 手续费
    status: Mapped[str] = mapped_column(
        String(20), default=TransactionStatus.CONFIRMED.value
    )
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    fund: Mapped["Fund"] = relationship(back_populates="transactions")
    channel: Mapped["Channel"] = relationship(back_populates="transactions")
