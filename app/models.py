from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import Base


class Market(StrEnum):
    B3 = "B3"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


class Side(StrEnum):
    BUY = "C"
    SELL = "V"


class PositionKind(StrEnum):
    REAL = "real"
    HYPOTHETICAL = "hypothetical"


class CollectorMode(StrEnum):
    EXCEL = "excel"
    DIRECT = "direct"


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "poll_interval_seconds BETWEEN 1 AND 3600",
            name="poll_interval_seconds_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    collector_mode: Mapped[CollectorMode] = mapped_column(
        Enum(CollectorMode, name="collector_mode"), default=CollectorMode.EXCEL
    )
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    positions: Mapped[list[Position]] = relationship(back_populates="broker_ref")


class Ticker(Base):
    __tablename__ = "tickers"
    __table_args__ = (
        CheckConstraint("rtd_market_code IN ('B', 'Y', 'N')", name="rtd_market_code_valid"),
        CheckConstraint("currency IN ('BRL', 'USD')", name="currency_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), unique=True)
    market: Mapped[Market] = mapped_column(Enum(Market, name="market"))
    rtd_market_code: Mapped[str] = mapped_column(String(1))
    currency: Mapped[str] = mapped_column(String(3))
    positions: Mapped[list[Position]] = relationship(back_populates="ticker_ref")
    option_contract: Mapped[OptionContract | None] = relationship(
        back_populates="ticker_ref",
        foreign_keys="OptionContract.ticker_id",
        uselist=False,
    )
    underlying_option_contracts: Mapped[list[OptionContract]] = relationship(
        back_populates="underlying_ticker_ref",
        foreign_keys="OptionContract.underlying_ticker_id",
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("average_cost >= 0", name="average_cost_non_negative"),
        CheckConstraint("quote_multiplier > 0", name="quote_multiplier_positive"),
        CheckConstraint("target_multiplier > 0", name="target_multiplier_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("brokers.id", ondelete="RESTRICT"), index=True
    )
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    side: Mapped[Side] = mapped_column(Enum(Side, name="position_side"), default=Side.BUY)
    opened_on: Mapped[date] = mapped_column(Date)
    quote_multiplier: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("1"))
    target_multiplier: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("1.5"))
    result_mode: Mapped[str] = mapped_column(String(1), default="L")
    position_kind: Mapped[PositionKind] = mapped_column(
        Enum(PositionKind, name="position_kind"), default=PositionKind.REAL
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    quote: Mapped[Quote | None] = relationship(
        back_populates="position", cascade="all, delete-orphan", uselist=False
    )
    broker_ref: Mapped[Broker] = relationship(back_populates="positions")
    ticker_ref: Mapped[Ticker] = relationship(back_populates="positions")

    @property
    def broker(self) -> str:
        return self.broker_ref.name

    @property
    def ticker(self) -> str:
        return self.ticker_ref.symbol

    @property
    def market(self) -> Market:
        return self.ticker_ref.market

    @property
    def rtd_market_code(self) -> str:
        return self.ticker_ref.rtd_market_code

    @property
    def currency(self) -> str:
        return self.ticker_ref.currency


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        CheckConstraint("last_price >= 0", name="last_price_non_negative"),
        CheckConstraint("previous_close >= 0", name="previous_close_non_negative"),
    )

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    last_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    previous_close: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    instrument_status: Mapped[str] = mapped_column(String(16), default="")
    source_status: Mapped[str] = mapped_column(String(16), default="online")
    error_message: Mapped[str | None] = mapped_column(String(250))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    position: Mapped[Position] = relationship(back_populates="quote")


class OptionExpiration(Base):
    __tablename__ = "option_expirations"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_code: Mapped[str] = mapped_column(String(5), unique=True)
    put_code: Mapped[str] = mapped_column(String(5), unique=True)
    exercise_date: Mapped[date] = mapped_column(Date, unique=True)
    contracts: Mapped[list[OptionContract]] = relationship(back_populates="expiration")


class OptionContract(Base):
    __tablename__ = "option_contracts"
    __table_args__ = (CheckConstraint("strike >= 0", name="strike_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), unique=True
    )
    underlying_ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT")
    )
    expiration_id: Mapped[int] = mapped_column(
        ForeignKey("option_expirations.id", ondelete="RESTRICT")
    )
    option_type: Mapped[OptionType] = mapped_column(
        Enum(OptionType, name="option_type")
    )
    strike: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    ticker_ref: Mapped[Ticker] = relationship(
        back_populates="option_contract", foreign_keys=[ticker_id]
    )
    underlying_ticker_ref: Mapped[Ticker] = relationship(
        back_populates="underlying_option_contracts",
        foreign_keys=[underlying_ticker_id],
    )
    expiration: Mapped[OptionExpiration] = relationship(back_populates="contracts")
    positions: Mapped[list[OptionPosition]] = relationship(back_populates="contract")


class OptionPosition(Base):
    __tablename__ = "option_positions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("average_cost >= 0", name="average_cost_non_negative"),
        CheckConstraint("target_price IS NULL OR target_price >= 0", name="target_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("brokers.id", ondelete="RESTRICT"), index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("option_contracts.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    side: Mapped[Side] = mapped_column(Enum(Side, name="position_side"))
    opened_on: Mapped[date] = mapped_column(Date)
    result_mode: Mapped[str] = mapped_column(String(1), default="L")
    position_kind: Mapped[PositionKind] = mapped_column(
        Enum(PositionKind, name="position_kind"), default=PositionKind.REAL
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    broker_ref: Mapped[Broker] = relationship()
    contract: Mapped[OptionContract] = relationship(back_populates="positions")
    quote: Mapped[OptionQuote | None] = relationship(
        back_populates="position", cascade="all, delete-orphan", uselist=False
    )

    @property
    def broker(self) -> str:
        return self.broker_ref.name


class OptionQuote(Base):
    __tablename__ = "option_quotes"
    __table_args__ = (
        CheckConstraint("last_price >= 0", name="last_price_non_negative"),
        CheckConstraint("previous_close >= 0", name="previous_close_non_negative"),
        CheckConstraint("underlying_price >= 0", name="underlying_price_non_negative"),
    )

    option_position_id: Mapped[int] = mapped_column(
        ForeignKey("option_positions.id", ondelete="CASCADE"), primary_key=True
    )
    last_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    previous_close: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    underlying_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    instrument_status: Mapped[str] = mapped_column(String(16), default="")
    source_status: Mapped[str] = mapped_column(String(16), default="online")
    error_message: Mapped[str | None] = mapped_column(String(250))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    position: Mapped[OptionPosition] = relationship(back_populates="quote")
