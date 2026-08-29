from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from flask_login import UserMixin  # type: ignore[import-untyped]
from sharedauth.passwords import conferir_hash, gerar_hash
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import Base
from app.pricing_settings import DEFAULT_RISK_FREE_RATE_ANNUAL
from app.themes import DEFAULT_THEME


class Market(StrEnum):
    B3 = "B3"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


class Side(StrEnum):
    BUY = "C"
    SELL = "V"


class CollectorMode(StrEnum):
    EXCEL = "excel"
    DIRECT = "direct"


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


class TransactionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class IncomeKind(StrEnum):
    """Natureza da renda recebida por um ativo.

    A distinção não é fiscal, é de PRECIFICAÇÃO: o histórico de cotações
    importado do Yahoo já embute dividendo e JCP no preço quando usa
    ``adjclose``, mas nenhuma fonte de preço embute aluguel de ações. Ver
    ``app.quote_history_import``, que por isso passou a gravar o ``close``
    nominal — com a série nominal, as três rendas entram no retorno pelo
    cadastro, explicitamente e sem contagem dupla.
    """

    DIVIDENDO = "dividendo"
    JCP = "jcp"
    ALUGUEL = "aluguel"


class PositionMovementKind(StrEnum):
    """Natureza de um lançamento no extrato de uma posição.

    ``ADJUSTMENT`` cobre a edição direta da posição (quantidade ou custo
    médio alterados no formulário), para que o extrato continue explicando
    o estado atual mesmo quando ele não veio de uma compra ou venda."""

    OPEN = "open"
    INCREASE = "increase"
    DECREASE = "decrease"
    ADJUSTMENT = "adjustment"


ROLE_ADMIN = "admin"
ROLE_OPERADOR = "operador"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_OPERADOR})


class User(Base, UserMixin):  # type: ignore[misc]
    """Usuário da aplicação.

    O papel separa quem opera a carteira de quem muda como o sistema funciona:
    `operador` registra transações e consulta relatórios; `admin` além disso
    altera as configurações do coletor, os parâmetros de precificação e o
    benchmark — decisões que afetam todos os números exibidos, não só os
    próprios lançamentos.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_OPERADOR, server_default=ROLE_OPERADOR)
    is_active_user: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def set_password(self, password: str) -> None:
        self.password_hash = gerar_hash(password)

    def check_password(self, password: str) -> bool:
        return conferir_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        return self.is_active_user

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "poll_interval_seconds BETWEEN 1 AND 3600",
            name="poll_interval_seconds_range",
        ),
        CheckConstraint(
            "agent_check_interval_seconds BETWEEN 5 AND 3600",
            name="agent_check_interval_seconds_range",
        ),
        CheckConstraint(
            "collector_schedule_start_time < collector_schedule_end_time",
            name="collector_schedule_time_range",
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
        CheckConstraint(
            "theme IN ('institutional', 'light', 'dark', 'solarized_light', 'solarized_dark', 'dracula', 'nord', 'monokai', 'gray', 'soft_light', 'soft_dark', 'corporate_blue', 'emerald')",
            name="theme_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    theme: Mapped[str] = mapped_column(String(24), default=DEFAULT_THEME, server_default=DEFAULT_THEME)
    collector_mode: Mapped[CollectorMode] = mapped_column(
        Enum(CollectorMode, name="collector_mode"), default=CollectorMode.EXCEL
    )
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    agent_check_interval_seconds: Mapped[int] = mapped_column(Integer, default=30)
    """Intervalo do agente Windows para consultar alterações e pedidos no VPS."""
    collector_schedule_weekdays: Mapped[str] = mapped_column(String(13), default="0,1,2,3,4")
    """Dias da semana (0=segunda, 6=domingo) em que a coleta remota pode operar."""
    collector_schedule_start_time: Mapped[time] = mapped_column(Time, default=time(9, 45))
    collector_schedule_end_time: Mapped[time] = mapped_column(Time, default=time(18, 10))
    risk_free_rate_annual: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=DEFAULT_RISK_FREE_RATE_ANNUAL
    )
    """Taxa livre de risco anual usada nas gregas de opções (Black-Scholes).
    Editável em Configurações; não é obtida automaticamente."""
    benchmark_ticker_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickers.id", ondelete="SET NULL"), nullable=True
    )
    """Ticker usado como referência para o Beta. Tipicamente um
    índice cadastrado manualmente (ex.: Ibovespa), sem coletor RTD — ver
    ``routes.quotes`` para o lançamento manual de cotações. ``None``
    desativa o cálculo de Beta em todos os relatórios de risco."""
    benchmark_ticker_ref: Mapped[Ticker | None] = relationship()
    stale_alert_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Segundos sem leitura para considerar uma cotação desatualizada,
    definido manualmente pelo usuário em Configurações. ``None`` mantém o
    cálculo automático (``routes.helpers.quote_stale_after_seconds``),
    baseado no intervalo de coleta configurado."""
    collector_refresh_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Pedido pendente do botão para o agente Windows executar uma leitura."""
    collector_agent_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collector_agent_status: Mapped[str] = mapped_column(String(16), default="waiting")
    collector_agent_error: Mapped[str | None] = mapped_column(String(250), nullable=True)
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
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    """Marca um ticker como referência de comparação (ex.: BOVA11, IBOV,
    USDBRL=X) em vez de um ativo detido em carteira.

    Tickers de referência: aparecem nos comparadores de evolução das
    cotações e da performance (``app.routes.helpers.benchmark_candidates``)
    e têm sua cotação diária atualizada junto com os demais ativos
    (``app.routes.helpers.quote_update_targets``), mas ficam de fora dos
    formulários de Posições, Transações, Proventos e Contratos de Opção
    (``app.routes.helpers.investable_ticker_records``) — não representam
    algo que se possa comprar/vender na carteira. Ver validação cruzada em
    ``app.routes.tables`` que impede marcar como referência um ticker já
    usado em uma posição ou em um contrato de opção, e vice-versa."""
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


class Portfolio(Base):
    """Uma carteira: BRL, USD ou Simulada, além de quaisquer outras que o
    usuário cadastrar por ``app.routes.portfolios``.

    É dona da posição (``Position.portfolio_id`` / ``OptionPosition.portfolio_id``
    / ``Transaction.portfolio_id``, FK obrigatória): decide, sem ambiguidade,
    em qual carteira uma posição específica está. ``PortfolioTicker`` é um
    cadastro N:N diferente, que só diz quais tickers *podem* ser lançados em
    cada carteira; nada impede um mesmo ticker de estar associado a mais de
    uma.

    ``simulated=True`` marca a carteira Simulada (ou qualquer outra carteira
    de teste que venha a existir): posições nela ficam fora de Risco,
    Performance e exposição e não geram transação. ``currency`` é ``None``
    para carteiras simuladas —
    elas não representam dinheiro real e podem misturar tickers de moedas
    diferentes."""

    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("currency IS NULL OR currency IN ('BRL', 'USD')", name="currency_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PortfolioTicker(Base):
    """Associação N:N que define os tickers permitidos em cada carteira.

    Não
    confundir com ``Position.portfolio_id``, que diz em qual carteira uma
    posição específica **está** (ver docstring de ``Portfolio``)."""

    __tablename__ = "portfolio_tickers"

    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), primary_key=True
    )

    portfolio_ref: Mapped[Portfolio] = relationship()
    ticker_ref: Mapped[Ticker] = relationship()


class Position(Base):
    """Uma posição em aberto, já consolidada: um único ticker por corretora,
    tipo (C/V) e carteira.

    Um novo aporte no mesmo ativo não cria outra linha aqui — ele soma a
    quantidade e recalcula o custo médio ponderado da posição existente (ver
    ``app.position_closure.create_or_merge_position``). O histórico de como
    se chegou à quantidade e ao custo médio atuais fica em
    ``PositionMovement``.
    """

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
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    quote: Mapped[Quote | None] = relationship(
        back_populates="position", cascade="all, delete-orphan", uselist=False
    )
    movements: Mapped[list[PositionMovement]] = relationship(
        back_populates="position",
        cascade="all, delete-orphan",
        order_by="PositionMovement.occurred_on, PositionMovement.id",
    )
    broker_ref: Mapped[Broker] = relationship(back_populates="positions")
    ticker_ref: Mapped[Ticker] = relationship(back_populates="positions")
    portfolio_ref: Mapped[Portfolio] = relationship()

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

    @property
    def simulated(self) -> bool:
        """``True`` quando a posição está na carteira Simulada
        (``portfolio_ref.simulated``). Usada pelas guardas de carteiras simuladas (sem
        merge, sem extrato, sem encerramento) e pelo agrupamento dos totais
        de ``app.portfolio.build_portfolio`` por (moeda, simulada)."""
        return self.portfolio_ref.simulated


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


class PositionMovement(Base):
    """Um lançamento do extrato de uma ``Position``: a abertura, cada aumento,
    cada encerramento parcial e cada ajuste manual.

    É o que permite à Carteira abrir a posição consolidada e mostrar as
    entradas individuais que a formaram. Guarda tanto o movimento
    (``quantity_delta``, ``price``) quanto o estado resultante
    (``resulting_quantity``, ``resulting_average_cost``): o custo médio
    vigente depois de cada aporte é um fato histórico, do mesmo tipo de
    ``Transaction.result``, e não é recalculado depois.

    O extrato acompanha a posição: encerrar totalmente (ou excluir) a posição
    apaga seus movimentos em cascata, porque o resultado realizado já fica
    registrado em ``transactions``, que sobrevive à posição.
    """

    __tablename__ = "position_movements"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("resulting_quantity > 0", name="resulting_quantity_positive"),
        CheckConstraint(
            "resulting_average_cost >= 0", name="resulting_average_cost_non_negative"
        ),
        CheckConstraint(
            "quantity_delta NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quantity_delta_finite",
        ),
        CheckConstraint(
            "price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="price_finite",
        ),
        CheckConstraint(
            "resulting_quantity NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="resulting_quantity_finite",
        ),
        CheckConstraint(
            "resulting_average_cost NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="resulting_average_cost_finite",
        ),
        CheckConstraint(
            "result IS NULL OR result NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="result_finite",
        ),
        CheckConstraint(
            "(kind = 'DECREASE') = (result IS NOT NULL)",
            name="result_only_on_decrease",
        ),
        CheckConstraint(
            "transaction_id IS NULL OR kind = 'DECREASE'",
            name="transaction_only_on_decrease",
        ),
        CheckConstraint(
            "(kind IN ('OPEN', 'INCREASE') AND quantity_delta > 0) OR "
            "(kind = 'DECREASE' AND quantity_delta < 0) OR "
            "kind = 'ADJUSTMENT'",
            name="quantity_delta_sign_matches_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[PositionMovementKind] = mapped_column(
        Enum(PositionMovementKind, name="position_movement_kind")
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    """Variação de quantidade, com sinal: positiva na abertura e nos aumentos,
    negativa nos encerramentos parciais, qualquer valor (inclusive zero, quando
    só o custo mudou) em um ajuste manual."""
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    """Custo unitário do aporte, preço de saída do encerramento parcial ou
    custo médio informado no ajuste."""
    occurred_on: Mapped[date] = mapped_column(Date)
    result: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    """Resultado realizado do encerramento parcial (mesma fórmula de
    ``domain.operation_result``). ``None`` nos demais tipos de movimento."""
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    """A transação fechada gerada por este encerramento parcial. É o que
    permite desfazer a operação quando essa transação é excluída — devolvendo
    a quantidade à posição em vez de deixar a baixa órfã (ver
    ``position_closure.revert_partial_close``). ``None`` nos demais tipos de
    movimento, que não produzem transação própria."""
    resulting_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    resulting_average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped[Position] = relationship(back_populates="movements")

    @property
    def quantity(self) -> Decimal:
        """Quantidade movimentada, sem sinal — o sentido já está em ``kind``."""
        return abs(self.quantity_delta)


class PositionLedgerArchive(Base):
    """Extrato preservado de uma posição JÁ ENCERRADA (ação ou opção).

    Encerrar uma posição por inteiro apaga a linha de ``positions``, e
    ``PositionMovement`` vai junto em cascata. Isso está certo para a
    Carteira — o resultado realizado sobrevive em ``Transaction`` —, mas
    quebrava o relatório de performance depois que a série passou a ser
    reconstruída do extrato: toda posição encerrada sumia do histórico, e o
    retorno passava a medir só os ativos que continuaram na carteira.

    É somente-adição e guarda o mínimo para responder "quanto deste ticker
    havia nesta data": o saldo resultante de cada lançamento, já com o sinal
    do lado, mais uma linha zerando a posição na data do encerramento. Não
    duplica o extrato exibido na Carteira, que acompanha a posição viva e
    continua sendo apagado com ela.

    **Excluir** uma posição não escreve aqui, ao contrário de **encerrar**:
    excluir é desfazer, e o que foi desfeito não deve reaparecer no
    histórico de desempenho.
    """

    __tablename__ = "position_ledger_archive"
    __table_args__ = (
        CheckConstraint(
            "instrument IN ('stock', 'option')", name="instrument_valid"
        ),
        CheckConstraint(
            "resulting_signed_quantity NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="resulting_signed_quantity_finite",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), index=True
    )
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"), index=True
    )
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("brokers.id", ondelete="RESTRICT"), index=True
    )
    instrument: Mapped[str] = mapped_column(String(6))
    """``stock`` ou ``option``. Junto com ``source_position_id`` reproduz a
    chave de posição usada por ``app.holdings_history.HoldingEvent``, que
    precisa distinguir as duas porque ``Position`` e ``OptionPosition`` têm
    sequências de id independentes."""
    source_position_id: Mapped[int] = mapped_column(Integer)
    resulting_signed_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"), index=True
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
    movements: Mapped[list[OptionPositionMovement]] = relationship(
        back_populates="position",
        cascade="all, delete-orphan",
        order_by="OptionPositionMovement.occurred_on, OptionPositionMovement.id",
    )
    portfolio_ref: Mapped[Portfolio] = relationship()

    @property
    def broker(self) -> str:
        return self.broker_ref.name

    @property
    def currency(self) -> str:
        """A moeda de uma posição de opção vem do ticker do **contrato**
        (``OptionContract.ticker_id``), nunca do ativo-objeto
        (``underlying_ticker_id``): é o contrato que tem preço e é negociado,
        não o subjacente."""
        return self.contract.ticker_ref.currency

    @property
    def simulated(self) -> bool:
        """Mesma ideia de ``Position.simulated``, para posições de opção."""
        return self.portfolio_ref.simulated


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


class OptionPositionMovement(Base):
    """Um lançamento do extrato de uma ``OptionPosition``: a abertura, cada
    aumento, cada encerramento parcial e cada ajuste manual.

    Espelha ``PositionMovement`` (ver a documentação lá) trocando a posição de
    ações pela de opções. O algoritmo que produz e reaplica estes lançamentos
    é compartilhado entre os dois — ver ``app.domain.replay_statement`` e
    ``app.position_closure`` — e só a persistência muda.
    """

    __tablename__ = "option_position_movements"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("resulting_quantity > 0", name="resulting_quantity_positive"),
        CheckConstraint(
            "resulting_average_cost >= 0", name="resulting_average_cost_non_negative"
        ),
        CheckConstraint(
            "quantity_delta NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="quantity_delta_finite",
        ),
        CheckConstraint(
            "price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="price_finite",
        ),
        CheckConstraint(
            "resulting_quantity NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="resulting_quantity_finite",
        ),
        CheckConstraint(
            "resulting_average_cost NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="resulting_average_cost_finite",
        ),
        CheckConstraint(
            "result IS NULL OR result NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="result_finite",
        ),
        CheckConstraint(
            "(kind = 'DECREASE') = (result IS NOT NULL)",
            name="result_only_on_decrease",
        ),
        CheckConstraint(
            "transaction_id IS NULL OR kind = 'DECREASE'",
            name="transaction_only_on_decrease",
        ),
        CheckConstraint(
            "(kind IN ('OPEN', 'INCREASE') AND quantity_delta > 0) OR "
            "(kind = 'DECREASE' AND quantity_delta < 0) OR "
            "kind = 'ADJUSTMENT'",
            name="quantity_delta_sign_matches_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    option_position_id: Mapped[int] = mapped_column(
        ForeignKey("option_positions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[PositionMovementKind] = mapped_column(
        Enum(PositionMovementKind, name="position_movement_kind")
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    """Variação de quantidade, com sinal: positiva na abertura e nos aumentos,
    negativa nos encerramentos parciais, qualquer valor (inclusive zero, quando
    só o custo mudou) em um ajuste manual."""
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    """Custo unitário do aporte, preço de saída do encerramento parcial ou
    custo médio informado no ajuste."""
    occurred_on: Mapped[date] = mapped_column(Date)
    result: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    """Resultado realizado do encerramento parcial (mesma fórmula de
    ``domain.operation_result``). ``None`` nos demais tipos de movimento."""
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    """A transação fechada gerada por este encerramento parcial. ``None`` nos
    demais tipos de movimento, que não produzem transação própria."""
    resulting_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    resulting_average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped[OptionPosition] = relationship(back_populates="movements")

    @property
    def quantity(self) -> Decimal:
        """Quantidade movimentada, sem sinal — o sentido já está em ``kind``."""
        return abs(self.quantity_delta)


class Transaction(Base):
    """Uma operação de renda variável: aberta (espelha uma ``Position`` em
    aberto) ou fechada (compra + venda, com o resultado já realizado —
    "registro de operações de venda (realizadas), não
    apenas posições abertas"). As fechadas alimentam win rate, profit
    factor, payoff ratio e tempo médio em posição.

    Não é um livro-razão completo de lotes/FIFO: cada linha representa
    um ciclo completo de abertura (e, quando fechada, também de
    fechamento), no mesmo espírito de ``Position``.

    Toda ``Position`` criada em Carteira ganha automaticamente uma linha
    aqui com ``status=OPEN`` (ver ``app.position_closure``), para que a
    aba Transações mostre tanto as posições abertas quanto as já
    encerradas, filtráveis pelo status. Ao encerrar a posição por inteiro,
    a mesma linha é atualizada para ``status=CLOSED`` em vez de criar uma
    nova.

    Um encerramento **parcial** produz duas linhas: uma nova, fechada, com
    a quantidade encerrada e o resultado realizado, e a linha aberta
    original reduzida ao saldo que continua na carteira. As duas apontam
    para a mesma posição de origem (ver ``source_position_id``).
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
        CheckConstraint(
            "num_nonnulls(ticker_id, option_contract_id) = 1",
            name="exactly_one_instrument",
        ),
        # A unicidade de "no máximo uma transação aberta por posição" é
        # particionada por tipo de instrumento (ação/opção). ``Position`` e
        # ``OptionPosition`` têm sequências de id independentes, então o
        # mesmo inteiro pode identificar as duas ao mesmo tempo; um índice
        # único só sobre ``source_position_id`` faria a segunda linha aberta
        # colidir com a primeira por pura coincidência de id.
        Index(
            "uq_transactions_open_source_position_stock",
            "source_position_id",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND ticker_id IS NOT NULL"),
        ),
        Index(
            "uq_transactions_open_source_position_option",
            "source_position_id",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND option_contract_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("brokers.id", ondelete="RESTRICT"), index=True
    )
    ticker_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickers.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    """``None`` para uma transação de opção, que usa ``option_contract_id``
    no lugar. Exatamente um dos dois é preenchido (CHECK
    ``exactly_one_instrument``)."""
    option_contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("option_contracts.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    """``None`` para uma transação de ação, que usa ``ticker_id`` no lugar."""
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
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"), index=True
    )
    source_position_id: Mapped[int | None] = mapped_column(Integer)
    """Enquanto ``status == OPEN``, é o id da posição espelhada por esta
    linha (``Position`` para uma transação de ação, ``OptionPosition`` para
    uma de opção — ver ``option_contract_id``), usado para localizá-la e
    atualizá-la ao editar/encerrar a posição. Para linhas fechadas
    automaticamente a partir de uma posição encerrada, preserva esse mesmo id
    como referência histórica, mesmo após a posição ser excluída. ``None``
    para transações lançadas manualmente, sem posição de origem.

    A unicidade é **parcial** e particionada por instrumento (índices
    ``uq_transactions_open_source_position_stock`` /
    ``..._option``, ambos restritos a ``status='OPEN'``): há no máximo uma
    linha aberta espelhando cada posição, mas uma posição encerrada em
    parcelas produz várias linhas fechadas apontando para o mesmo id. A
    partição por instrumento existe porque ``Position`` e ``OptionPosition``
    têm sequências de id independentes e por isso podem colidir."""
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    broker_ref: Mapped[Broker] = relationship()
    ticker_ref: Mapped[Ticker | None] = relationship()
    option_contract_ref: Mapped[OptionContract | None] = relationship()
    portfolio_ref: Mapped[Portfolio] = relationship()

    @property
    def broker(self) -> str:
        return self.broker_ref.name

    @property
    def is_option(self) -> bool:
        return self.option_contract_id is not None

    @property
    def ticker(self) -> str:
        """Símbolo do ticker de ações, ou o símbolo do próprio contrato de
        opção quando ``is_option``."""
        if self.option_contract_id is not None:
            assert self.option_contract_ref is not None  # narrows for mypy; CHECK-enforced
            return self.option_contract_ref.ticker_ref.symbol
        assert self.ticker_ref is not None  # narrows for mypy; CHECK-enforced
        return self.ticker_ref.symbol

    @property
    def currency(self) -> str:
        if self.option_contract_id is not None:
            assert self.option_contract_ref is not None  # narrows for mypy; CHECK-enforced
            return self.option_contract_ref.ticker_ref.currency
        assert self.ticker_ref is not None  # narrows for mypy; CHECK-enforced
        return self.ticker_ref.currency

    @property
    def days_held(self) -> int | None:
        if self.closed_on is None:
            return None
        return (self.closed_on - self.opened_on).days


class Dividend(Base):
    """Renda recebida por um ativo: dividendo, JCP ou aluguel de ações.

    O nome da tabela é herdado de quando só havia dividendo; ``kind``
    (``IncomeKind``) é que diz de qual renda se trata. A distinção existe
    porque as três entram no retorno da carteira e o usuário precisa saber
    quanto cada uma rendeu — o resultado por preço médio e preço de saída,
    sozinho, mascara a renda.

    Não classifica o tipo fiscal nem retém imposto: é o valor efetivamente
    recebido, por ativo/corretora/data.
    """

    __tablename__ = "dividends"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="amount_finite",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[IncomeKind] = mapped_column(
        Enum(IncomeKind, name="income_kind"), default=IncomeKind.DIVIDENDO
    )
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
    """Série temporal de cotações por ativo, base dos relatórios de
    histórico e dos KPIs de risco (volatilidade, Sharpe, drawdown, VaR, Beta
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


class AuditLog(Base):
    """Trilha de auditoria: quem fez o quê, quando e de onde.

    Só estado e acesso -- entrada, saída, gestão de contas e as escritas que
    alteram a carteira. Consulta não entra; ver o docstring de
    `app/auditoria.py`.

    `user_id` é `SET NULL` e não `CASCADE`: apagar uma conta não pode apagar o
    registro do que ela fez. É o oposto do que o resto do schema faz, e é
    deliberado -- uma trilha que some junto com o autor não serve para nada.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user_ref: Mapped[User | None] = relationship()
