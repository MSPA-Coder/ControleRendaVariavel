from __future__ import annotations

from dataclasses import asdict

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Broker, Market, Portfolio, PortfolioTicker, Ticker
from app.reference_data import (
    parse_broker,
    parse_portfolio_create,
    parse_portfolio_update,
    parse_ticker,
)
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    investable_ticker_records,
    is_htmx_request,
    portfolio_ticker_has_positions,
    ticker_has_holdings,
    ticker_records,
)


def _tables_redirect(endpoint: str) -> ResponseReturnValue:
    return redirect(url_for(f"portfolio.{endpoint}"))


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@bp.get("/tables/brokers")
def table_brokers() -> str:
    return render_template("table_brokers.html", brokers=broker_records())


@bp.get("/tables/tickers")
def table_tickers() -> str:
    return render_template("table_tickers.html", tickers=ticker_records(), markets=Market)


@bp.post("/tables/brokers")
def create_broker() -> ResponseReturnValue:
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower())
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        db.session.add(Broker(**asdict(data)))
        db.session.commit()
        flash("Corretora adicionada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("O nome ou a sigla da corretora já está cadastrado.", "error")
    return _tables_redirect("table_brokers")


@bp.post("/tables/brokers/<int:broker_id>")
def update_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower()),
                Broker.id != broker.id,
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        broker.name = data.name
        broker.acronym = data.acronym
        db.session.commit()
        flash("Corretora atualizada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("O nome ou a sigla da corretora já está cadastrado.", "error")
    return _tables_redirect("table_brokers")


@bp.post("/tables/brokers/<int:broker_id>/delete")
def delete_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    db.session.delete(broker)
    try:
        db.session.commit()
        flash("Corretora excluída.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            "A corretora não pode ser excluída enquanto possuir posições, "
            "transações ou proventos vinculados.",
            "error",
        )
    return _tables_redirect("table_brokers")


@bp.post("/tables/tickers")
def create_ticker() -> ResponseReturnValue:
    try:
        data = parse_ticker(request.form)
        if db.session.scalar(select(Ticker).where(Ticker.symbol == data.symbol)) is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        db.session.add(Ticker(**asdict(data)))
        db.session.commit()
        flash("Ticker adicionado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Esse ticker já está cadastrado.", "error")
    return _tables_redirect("table_tickers")


@bp.post("/tables/tickers/<int:ticker_id>")
def update_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    try:
        data = parse_ticker(request.form)
        duplicate = db.session.scalar(
            select(Ticker).where(Ticker.symbol == data.symbol, Ticker.id != ticker.id)
        )
        if duplicate is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        if data.is_benchmark and not ticker.is_benchmark and ticker_has_holdings(ticker.id):
            raise ValueError(
                "Esse ticker já está em uso em uma posição, transação, provento ou "
                "contrato de opção; não pode virar referência de comparação."
            )
        for key, value in asdict(data).items():
            setattr(ticker, key, value)
        db.session.commit()
        flash("Ticker atualizado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Esse ticker já está cadastrado.", "error")
    return _tables_redirect("table_tickers")


@bp.post("/tables/tickers/<int:ticker_id>/delete")
def delete_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    db.session.delete(ticker)
    try:
        db.session.commit()
        flash("Ticker excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            "O ticker não pode ser excluído enquanto possuir posições, "
            "transações ou proventos vinculados.",
            "error",
        )
    return _tables_redirect("table_tickers")


def _portfolios_results_context(
    *, selected_portfolio_id: int | None = None, management_open: bool = False
) -> dict[str, object]:
    """Contexto da tela de Carteiras.

    Extraído da listagem porque os formulários de CRUD e de associação de
    tickers também o montam: eles respondem ao HTMX com esta mesma região já
    atualizada, no mesmo padrão de ``app.routes.quotes._quote_history_context``.
    """
    portfolios = list(db.session.scalars(select(Portfolio).order_by(Portfolio.name)))
    selected_portfolio: Portfolio | None = None
    if selected_portfolio_id is not None:
        selected_portfolio = next(
            (portfolio for portfolio in portfolios if portfolio.id == selected_portfolio_id), None
        )
    if selected_portfolio is None and portfolios:
        selected_portfolio = portfolios[0]

    associated_tickers: list[Ticker] = []
    available_tickers: list[Ticker] = []
    associated_tickers_by_portfolio: dict[int, list[Ticker]] = {
        portfolio.id: [] for portfolio in portfolios
    }
    for portfolio_id, ticker in db.session.execute(
        select(PortfolioTicker.portfolio_id, Ticker)
        .join(Ticker, Ticker.id == PortfolioTicker.ticker_id)
        .order_by(PortfolioTicker.portfolio_id, Ticker.symbol)
    ):
        associated_tickers_by_portfolio.setdefault(portfolio_id, []).append(ticker)
    # Cada carteira tem seu próprio botão de inclusão; portanto, a lista de
    # opções disponíveis também precisa ser separada por carteira.
    eligible = investable_ticker_records()
    available_tickers_by_portfolio = {
        portfolio.id: [
            ticker
            for ticker in eligible
            if ticker.id
            not in {associated.id for associated in associated_tickers_by_portfolio[portfolio.id]}
        ]
        for portfolio in portfolios
    }
    if selected_portfolio is not None:
        associated_ids = set(
            db.session.scalars(
                select(PortfolioTicker.ticker_id).where(
                    PortfolioTicker.portfolio_id == selected_portfolio.id
                )
            )
        )
        # Só tickers investíveis (exclui referências de comparação, como o
        # próprio CRUD de posições) fazem sentido como conteúdo de uma
        # carteira — ver ``investable_ticker_records``.
        associated_tickers = [ticker for ticker in eligible if ticker.id in associated_ids]
        available_tickers = [ticker for ticker in eligible if ticker.id not in associated_ids]

    return {
        "portfolios": portfolios,
        "selected_portfolio": selected_portfolio,
        "associated_tickers": associated_tickers,
        "available_tickers": available_tickers,
        "associated_tickers_by_portfolio": associated_tickers_by_portfolio,
        "available_tickers_by_portfolio": available_tickers_by_portfolio,
        "portfolios_management_open": management_open,
    }


def _portfolios_response(
    selected_portfolio_id: int | None, *, status: int | None = None
) -> ResponseReturnValue:
    """Resposta dos formulários de Carteiras: fragmento atualizado no HTMX,
    ou o redirect de sempre sem JavaScript (mesmo padrão de
    ``app.routes.quotes._quote_management_response``).

    Um ``status`` explícito (ver ``update_portfolio``) força a renderização
    do fragmento mesmo fora do HTMX: um redirect sempre vira 200 depois de
    seguido, e um erro de validação precisa do código de status na própria
    resposta (mesmo padrão de ``app.routes.settings._render_settings``, que
    resolve o mesmo problema para um formulário de página inteira)."""
    management_open = request.form.get("portfolios_management_open") == "1"
    context = _portfolios_results_context(
        selected_portfolio_id=selected_portfolio_id,
        management_open=management_open,
    )
    if is_htmx_request() or status is not None:
        return render_template("partials/portfolios_results.html", **context), status or 200
    if selected_portfolio_id is None:
        return redirect(url_for("portfolio.table_portfolios"))
    return redirect(url_for("portfolio.table_portfolios", portfolio_id=selected_portfolio_id))


@bp.get("/tables/portfolios")
def table_portfolios() -> ResponseReturnValue:
    """Carteiras: página inteira, ou só a região trocada pelo seletor de
    carteira e pelos formulários de CRUD/associação. A mesma URL serve os
    dois casos, então o seletor empurra ao histórico o endereço real da
    página — ``HX-Request`` decide apenas a forma da resposta."""
    context = _portfolios_results_context(
        selected_portfolio_id=_int_or_none(request.args.get("portfolio_id"))
    )
    if is_htmx_request():
        return render_template("partials/portfolios_results.html", **context)
    return render_template("table_portfolios.html", **context)


@bp.post("/tables/portfolios")
def create_portfolio() -> ResponseReturnValue:
    try:
        data = parse_portfolio_create(request.form)
        duplicate = db.session.scalar(
            select(Portfolio).where(func.lower(Portfolio.name) == data.name.lower())
        )
        if duplicate is not None:
            raise ValueError("Já existe uma carteira com esse nome.")
        portfolio = Portfolio(**asdict(data))
        db.session.add(portfolio)
        db.session.commit()
        flash("Carteira criada.", "success")
        return _portfolios_response(portfolio.id)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma carteira com esse nome.", "error")
    return _portfolios_response(None)


def _requested_simulated_change(portfolio: Portfolio) -> bool:
    """``True`` quando o POST tenta mudar ``simulated`` explicitamente.

    O formulário legítimo de edição não envia o campo (ver
    ``partials/portfolios_results.html``): só um POST manual chega aqui com
    ``simulated`` presente. Comparar contra o valor atual — em vez de só
    checar a presença do campo — evita um falso positivo ao reenviar o
    mesmo formulário de edição da própria carteira Simulada."""
    if "simulated" not in request.form:
        return False
    requested = request.form.get("simulated", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }
    return requested != portfolio.simulated


@bp.post("/tables/portfolios/<int:portfolio_id>")
def update_portfolio(portfolio_id: int) -> ResponseReturnValue:
    """``simulated`` não é lido do formulário de edição: é fixado na criação
    e não pode ser alterado depois (ver ``PortfolioUpdateInput``): trocar de
    real para simulada, ou o
    contrário, exigiria criar ou apagar transações em massa. Uma posição
    pode trocar de carteira livremente; a carteira em si, não.

    O formulário legítimo nunca envia ``simulated``; uma tentativa direta
    (POST manual) é recusada com 422 explícito, em vez de ser ignorada em
    silêncio como antes."""
    portfolio = db.get_or_404(Portfolio, portfolio_id)
    if _requested_simulated_change(portfolio):
        flash(
            "A natureza da carteira (real ou simulada) não muda depois de "
            "criada. Para mover um investimento entre real e simulada, mova "
            "a posição — não a carteira.",
            "error",
        )
        return _portfolios_response(portfolio_id, status=422)
    try:
        data = parse_portfolio_update(request.form)
        duplicate = db.session.scalar(
            select(Portfolio).where(
                func.lower(Portfolio.name) == data.name.lower(), Portfolio.id != portfolio.id
            )
        )
        if duplicate is not None:
            raise ValueError("Já existe uma carteira com esse nome.")
        portfolio.name = data.name
        portfolio.currency = data.currency
        portfolio.description = data.description
        db.session.commit()
        flash("Carteira atualizada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma carteira com esse nome.", "error")
    return _portfolios_response(portfolio_id)


@bp.post("/tables/portfolios/<int:portfolio_id>/delete")
def delete_portfolio(portfolio_id: int) -> ResponseReturnValue:
    portfolio = db.get_or_404(Portfolio, portfolio_id)
    db.session.delete(portfolio)
    try:
        db.session.commit()
        flash("Carteira excluída.", "success")
        return _portfolios_response(None)
    except IntegrityError:
        db.session.rollback()
        flash(
            "A carteira não pode ser excluída enquanto possuir posições ou "
            "transações vinculadas.",
            "error",
        )
    return _portfolios_response(portfolio_id)


@bp.post("/tables/portfolios/<int:portfolio_id>/tickers")
def add_portfolio_ticker(portfolio_id: int) -> ResponseReturnValue:
    portfolio = db.get_or_404(Portfolio, portfolio_id)
    ticker_id = _int_or_none(request.form.get("ticker_id"))
    ticker = db.session.get(Ticker, ticker_id) if ticker_id is not None else None
    if ticker is None:
        flash("Selecione um ticker cadastrado.", "error")
        return _portfolios_response(portfolio.id)
    if db.session.get(PortfolioTicker, (portfolio.id, ticker.id)) is not None:
        flash("Esse ticker já está associado a essa carteira.", "error")
        return _portfolios_response(portfolio.id)
    db.session.add(PortfolioTicker(portfolio_id=portfolio.id, ticker_id=ticker.id))
    try:
        db.session.commit()
        flash("Ticker associado à carteira.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Esse ticker já está associado a essa carteira.", "error")
    return _portfolios_response(portfolio.id)


@bp.post("/tables/portfolios/<int:portfolio_id>/tickers/<int:ticker_id>/delete")
def remove_portfolio_ticker(portfolio_id: int, ticker_id: int) -> ResponseReturnValue:
    portfolio = db.get_or_404(Portfolio, portfolio_id)
    link = db.session.get(PortfolioTicker, (portfolio_id, ticker_id))
    if link is None:
        flash("Esse ticker não está associado a essa carteira.", "error")
        return _portfolios_response(portfolio.id)
    if portfolio_ticker_has_positions(portfolio_id, ticker_id):
        flash(
            "Esse ticker não pode ser desassociado enquanto houver posição dele "
            "nesta carteira.",
            "error",
        )
        return _portfolios_response(portfolio.id)
    db.session.delete(link)
    db.session.commit()
    flash("Ticker desassociado da carteira.", "success")
    return _portfolios_response(portfolio.id)
