"""Consultas financeiras com dono e filtros explícitos; sem contexto HTTP."""

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.core.currency_filter import ALL
from app.models import Broker, OptionContract, OptionPosition, Portfolio, Position, Ticker


def stock_positions(
    owner_id: int,
    portfolio_id: int | None = None,
    broker: str | None = None,
    *,
    group_by_broker: bool = True,
    exclude_simulated: bool = False,
    currency: str = ALL,
) -> list[Position]:
    order_columns = (
        (Ticker.currency, Broker.name, Ticker.symbol, Position.opened_on)
        if group_by_broker
        else (Ticker.currency, Ticker.symbol, Broker.name, Position.opened_on)
    )
    statement = (
        select(Position)
        .where(Position.owner_id == owner_id)
        .join(Position.broker_ref)
        .join(Position.ticker_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
            # `build_portfolio` lê `position.simulated` (via `portfolio_ref`)
            # para cada posição, para agrupar os totais por (moeda, simulada)
            # — sem eager load aqui, cada leitura seria uma consulta própria.
            joinedload(Position.portfolio_ref),
            # O extrato entra em uma consulta única para as posições da página:
            # a Carteira consulta o tamanho dele em toda linha, para decidir se
            # mostra o `+`, e sem isso seria uma consulta por posição.
            selectinload(Position.movements),
        )
        .order_by(*order_columns)
    )
    if portfolio_id is not None:
        statement = statement.where(Position.portfolio_id == portfolio_id)
    if portfolio_id is None or exclude_simulated:
        # "Todas" quer dizer todas as **reais**: carteira simulada é insight,
        # e misturá-la no conjunto padrão faria o número somado na tela se
        # ler como patrimônio. Para vê-la, escolhe-se ela no filtro.
        #
        # `exclude_simulated` força a exclusão mesmo com uma carteira
        # simulada escolhida na URL: é o que as páginas de Exposição
        # (Análise) usam, onde ela nunca entra. O resultado
        # fica vazio nesse caso, o que é seguro.
        statement = statement.join(Position.portfolio_ref).where(Portfolio.simulated.is_(False))
    if broker:
        statement = statement.where(Broker.name == broker)
    selected_currency = currency
    if selected_currency != ALL:
        statement = statement.where(Ticker.currency == selected_currency)
    return list(db.session.scalars(statement).unique())


def option_positions(
    owner_id: int,
    portfolio_id: int | None = None, broker: str | None = None, *, currency: str = ALL
) -> list[OptionPosition]:
    statement = (
        select(OptionPosition)
        .where(OptionPosition.owner_id == owner_id)
        .join(OptionPosition.broker_ref)
        .options(
            joinedload(OptionPosition.broker_ref),
            joinedload(OptionPosition.quote),
            joinedload(OptionPosition.contract).joinedload(OptionContract.ticker_ref),
            joinedload(OptionPosition.contract).joinedload(
                OptionContract.underlying_ticker_ref
            ),
            joinedload(OptionPosition.contract)
            .joinedload(OptionContract.expiration),
            # `build_option_portfolio` lê `position.simulated` (via
            # `portfolio_ref`) para cada posição — sem eager load aqui, cada
            # leitura seria uma consulta própria.
            joinedload(OptionPosition.portfolio_ref),
            # O extrato entra em uma consulta única para as posições da
            # página: a grade consulta o tamanho dele em toda linha, para
            # decidir se mostra o `+` (mesmo padrão de
            # ``routes.helpers.positions_query`` para ações).
            selectinload(OptionPosition.movements),
        )
        .order_by(OptionPosition.opened_on, OptionPosition.id)
    )
    if portfolio_id is not None:
        statement = statement.where(OptionPosition.portfolio_id == portfolio_id)
    if broker:
        statement = statement.where(Broker.name == broker)
    selected_currency = currency
    if selected_currency != ALL:
        statement = statement.join(OptionPosition.contract).join(OptionContract.ticker_ref).where(
            Ticker.currency == selected_currency
        )
    return list(db.session.scalars(statement).unique())


