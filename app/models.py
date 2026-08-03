from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from flask_login import UserMixin  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from app import Base
from app.pricing_settings import DEFAULT_RISK_FREE_RATE_ANNUAL


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


class TransactionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class User(Base, UserMixin):  # type: ignore[misc]
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active_user: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        return self.is_active_user


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "poll_interval_seconds BETWEEN 1 AND 3600",
            name="poll_interval_seconds_range",
        ),
        CheckConstraint(
            "risk_free_rate_annual BETWEEN 0 AND 1",
            name="risk_free_rate_annual_range",
        ),
        CheckConstraint(
            "risk_free_rate_annual NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="risk_free_rate_annual_finite",
        ),
        CheckConstraint(
            "stale_alert_seconds IS NULL OR stale_alert_seconds BETWEEN 1 AND 86400",
            name="stale_alert_seconds_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    collector_mode: Mapped[CollectorMode] = mapped_column(
        Enum(CollectorMode, name="collector_mode"), default=CollectorMode.EXCEL
    )
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    risk_free_rate_annual: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=DEFAULT_RISK_FREE_RATE_ANNUAL
    )
    """Taxa livre de risco anual usada nas gregas de opções (Black-Scholes).
    Editável em Configurações; não é obtida automaticamente (ver Fase G)."""
    benchmark_ticker_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickers.id", ondelete="SET NULL"), nullable=True
    )
    """Ticker usado como referência para o Beta (Fase D). Tipicamente um
    índice cadastrado manualmente (ex.: Ibovespa), sem coletor RTD — ver
    ``routes.quotes`` para o lançamento manual de cotações. ``None``
    desativa o cálculo de Beta em todos os relatórios de risco."""
    benchmark_ticker_ref: Mapped[Ticker | None] = relationship()
    stale_alert_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Segundos sem leitura para considerar uma cotação desatualizada,
    definido manualmente pelo usuário em Configurações. ``None`` mantém o
    cálculo automático (``routes.helpers.quote_stale_after_seconds``),
    baseado no intervalo de coleta configurado."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    acronym: Mapped[str] = mapped_column(String(40), unique=True)
    positions: Mapped[list[Position]] = relationship(back_populates="broker_ref")


class Ticker(Base):
    __tablename__ = "tickers"
    __table_args__ = (
        CheckConstraint("rtd_market_code IN ('B', 'Y', 'N')", name="rtd_market_code_valid"),
        CheckConstraint("currency IN ('BRL', 'USD')", name="currency_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), unique=True)
    trading_name: Mapped[str] = mapped_column(String(80))
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
        CheckConstraint(
            "quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quantity_finite",
        ),
        CheckConstraint(
            "average_cost NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="average_cost_finite",
        ),
        CheckConstraint(
            "quote_multiplier NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quote_multiplier_finite",
        ),
        CheckConstraint(
            "target_multiplier NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="target_multiplier_finite",
        ),
        CheckConstraint("result_mode IN ('L', 'B')", name="result_mode_valid"),
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
        CheckConstraint(
            "last_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="last_price_finite",
        ),
        CheckConstraint(
            "previous_close NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="previous_close_finite",
        ),
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
    __table_args__ = (
        CheckConstraint("strike >= 0", name="strike_non_negative"),
        CheckConstraint(
            "strike NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="strike_finite",
        ),
    )

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
        CheckConstraint(
            "quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quantity_finite",
        ),
        CheckConstraint(
            "average_cost NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="average_cost_finite",
        ),
        CheckConstraint(
            "target_price IS NULL OR target_price NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="target_finite",
        ),
        CheckConstraint("result_mode IN ('L', 'B')", name="result_mode_valid"),
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
        CheckConstraint(
            "last_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="last_price_finite",
        ),
        CheckConstraint(
            "previous_close NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="previous_close_finite",
        ),
        CheckConstraint(
            "underlying_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="underlying_price_finite",
        ),
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


class Transaction(Base):
    """Uma operação de renda variável: aberta (espelha uma ``Position`` em
    aberto) ou fechada (compra + venda, com o resultado já realizado —
    item 5/Fase A: "registro de operações de venda (realizadas), não
    apenas posições abertas"). As fechadas alimentam win rate, profit
    factor, payoff ratio e tempo médio em posição (Fase C).

    Não é um livro-razão completo de lotes/FIFO: cada linha representa
    um ciclo completo de abertura (e, quando fechada, também de
    fechamento), no mesmo espírito de ``Position``. Uma posição fechada
    parcialmente deve ser lançada como duas transações com quantidades
    proporcionais, se for o caso.

    Toda ``Position`` criada em Carteira ganha automaticamente uma linha
    aqui com ``status=OPEN`` (ver ``app.position_closure``), para que a
    aba Transações mostre tanto as posições abertas quanto as já
    encerradas, filtráveis pelo status. Ao encerrar a posição, a mesma
    linha é atualizada para ``status=CLOSED`` em vez de criar uma nova.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("average_cost >= 0", name="average_cost_non_negative"),
        CheckConstraint(
            "exit_price IS NULL OR exit_price >= 0", name="exit_price_non_negative"
        ),
        CheckConstraint(
            "closed_on IS NULL OR closed_on >= opened_on",
            name="closed_on_not_before_opened_on",
        ),
        CheckConstraint(
            "quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quantity_finite",
        ),
        CheckConstraint(
            "average_cost NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="average_cost_finite",
        ),
        CheckConstraint(
            "exit_price IS NULL OR exit_price NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="exit_price_finite",
        ),
        CheckConstraint(
            "result IS NULL OR result NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="result_finite",
        ),
        CheckConstraint("result_mode IN ('L', 'B')", name="result_mode_valid"),
        CheckConstraint(
            "(status = 'OPEN' AND closed_on IS NULL AND exit_price IS NULL "
            "AND result IS NULL) OR "
            "(status = 'CLOSED' AND closed_on IS NOT NULL AND exit_price IS NOT NULL "
            "AND result IS NOT NULL)",
            name="status_fields_consistency",
        ),
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
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    side: Mapped[Side] = mapped_column(Enum(Side, name="position_side"), default=Side.BUY)
    opened_on: Mapped[date] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date, index=True)
    result_mode: Mapped[str] = mapped_column(String(1), default="L")
    result: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    """Resultado realizado, calculado no momento do fechamento (mesma
    fórmula de ``domain.operation_result``) e persistido — não recalculado
    depois, pois é um fato histórico. ``None`` enquanto a transação estiver
    aberta (``status == TransactionStatus.OPEN``)."""
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status"), default=TransactionStatus.CLOSED
    )
    position_kind: Mapped[PositionKind] = mapped_column(
        Enum(PositionKind, name="position_kind"), default=PositionKind.REAL
    )
    source_position_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    """Enquanto ``status == OPEN``, é o id da ``Position`` espelhada por esta
    linha (usado para localizá-la e atualizá-la ao editar/encerrar a
    posição). Para linhas fechadas automaticamente a partir de uma posição
    encerrada, preserva esse mesmo id como referência histórica, mesmo após
    a posição ser excluída. ``None`` para transações lançadas manualmente,
    sem posição de origem."""
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    broker_ref: Mapped[Broker] = relationship()
    ticker_ref: Mapped[Ticker] = relationship()

    @property
    def broker(self) -> str:
        return self.broker_ref.name

    @property
    def ticker(self) -> str:
        return self.ticker_ref.symbol

    @property
    def currency(self) -> str:
        return self.ticker_ref.currency

    @property
    def days_held(self) -> int | None:
        if self.closed_on is None:
            return None
        return (self.closed_on - self.opened_on).days


class Dividend(Base):
    """Provento recebido (dividendo, JCP, rendimento) — item 5: relatório
    de Proventos. Não classifica o tipo fiscal do provento; é um registro
    simples de valor recebido por ativo/corretora/data."""

    __tablename__ = "dividends"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="amount_finite",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("brokers.id", ondelete="RESTRICT"), index=True
    )
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    payment_date: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    broker_ref: Mapped[Broker] = relationship()
    ticker_ref: Mapped[Ticker] = relationship()

    @property
    def broker(self) -> str:
        return self.broker_ref.name

    @property
    def ticker(self) -> str:
        return self.ticker_ref.symbol

    @property
    def currency(self) -> str:
        return self.ticker_ref.currency


class QuoteHistory(Base):
    """Série temporal de cotações por ativo — item 5/relatório 6, e
    pré-requisito da Fase D (volatilidade, Sharpe, drawdown, VaR, Beta
    precisam de retornos diários).

    Granularidade deliberadamente DIÁRIA, não a cada poll do coletor
    (que roda a cada poucos segundos): um "upsert" por
    (ticker, recorded_date) mantém sempre o último preço observado no
    dia, sem inflar a tabela com milhares de linhas idênticas por ativo
    por dia. É isso que os KPIs de risco de fato precisam (retornos
    diários), e é o suficiente para o gráfico de série histórica."""

    __tablename__ = "quote_history"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint(
            "price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="price_finite",
        ),
        UniqueConstraint("ticker_id", "recorded_date", name="uq_quote_history_ticker_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    recorded_date: Mapped[date] = mapped_column(Date, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    ticker_ref: Mapped[Ticker] = relationship()
