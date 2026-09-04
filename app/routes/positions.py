from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app import db
from app.core.validation import parse_finite_decimal
from app.models import Broker, Portfolio, Position, Side, Ticker
from app.positions.closure import (
    close_open_position,
    create_or_merge_position,
    delete_open_transaction_for_position,
    discard_simulation_history,
    duplicate_entry,
    record_position_adjustment,
    sync_open_transaction_for_position,
)
from app.positions.portfolio import PortfolioView, build_portfolio, position_movement_results
from app.routes import bp
from app.routes.helpers import (
    agent_check_interval_seconds,
    allocation_chart_data,
    broker_exposure_chart_data,
    broker_records,
    brokers,
    converted_allocation_chart_data,
    converted_broker_exposure_chart_data,
    converted_market_exposure_chart_data,
    exposure_group_rows,
    investable_ticker_records,
    is_htmx_request,
    latest_usd_brl_quote,
    market_exposure_chart_data,
    missing_quote_rows,
    poll_interval_seconds,
    portfolio_records,
    positions_query,
    quote_stale_after_seconds,
    real_portfolio_records,
    selected_filters,
)

RETURN_PERIODS = (
    (7, "Semanal"),
    (30, "Mensal"),
    (90, "Trimestral"),
    (182, "Semestral"),
    (365, "Anual"),
)
RETURN_PERIOD_DAYS = tuple(days for days, _ in RETURN_PERIODS)


@dataclass(frozen=True, slots=True)
class PositionInput:
    broker_id: int
    ticker_id: int
    quantity: Decimal
    average_cost: Decimal
    side: Side
    opened_on: date
    quote_multiplier: Decimal
    target_multiplier: Decimal
    result_mode: str
    portfolio_id: int


def _parse_form() -> PositionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        ticker_id = int(raw["ticker_id"])
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        average_cost = parse_finite_decimal(raw["average_cost"], field_name="um custo médio")
        quote_multiplier = parse_finite_decimal(
            raw["quote_multiplier"], field_name="um multiplicador de cotação"
        )
        target_multiplier = parse_finite_decimal(
            raw["target_multiplier"], field_name="um multiplicador de target"
        )
        opened_on = date.fromisoformat(raw["opened_on"])
        side = Side(raw["side"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    ticker = db.session.get(Ticker, ticker_id)
    if db.session.get(Broker, broker_id) is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError(
            "Esse ticker está marcado como referência de comparação e não pode "
            "ter posição própria."
        )
    if quantity <= 0 or average_cost < 0 or quote_multiplier <= 0 or target_multiplier <= 0:
        raise ValueError(
            "Quantidade e multiplicadores devem ser positivos; custo não pode ser negativo."
        )
    result_mode = raw.get("result_mode", "").upper()
    try:
        portfolio_id = int(raw["portfolio_id"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Selecione uma carteira.") from exc
    if db.session.get(Portfolio, portfolio_id) is None:
        raise ValueError("Selecione uma carteira cadastrada.")
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    return PositionInput(
        broker_id,
        ticker_id,
        quantity,
        average_cost,
        side,
        opened_on,
        quote_multiplier,
        target_multiplier,
        result_mode,
        portfolio_id,
    )


def expanded_position_ids() -> set[int]:
    """Posicoes com o extrato aberto na carteira.

    O estado vive na propria URL (`?expanded=3,7`), e nao no navegador,
    porque a tabela se substitui inteira a cada atualizacao automatica: o
    fragmento devolvido carrega os mesmos argumentos, entao o que estava
    aberto continua aberto depois da troca. E uma lista separada por virgula,
    e nao um parametro repetido, porque `url_for(..., **request.args)` so
    enxerga o primeiro valor de cada chave.
    """
    raw = request.args.get("expanded", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def toggle_expanded_url(expanded: set[int], position_id: int) -> str:
    """Endereco da propria carteira com o extrato desta posicao invertido."""
    args: dict[str, Any] = request.args.to_dict(flat=True)
    target = expanded ^ {position_id}
    if target:
        args["expanded"] = ",".join(str(identifier) for identifier in sorted(target))
    else:
        args.pop("expanded", None)
    return url_for("portfolio.index", **args)


def portfolio_results_context() -> dict[str, object]:
    """Contexto da regiao de resultados da carteira.

    Compartilhado entre a pagina inteira e o fragmento atualizado por HTMX,
    para que os dois nunca divirjam.
    """
    # Abre em "Todas", que desde a mudança do filtro quer dizer todas as
    # carteiras **reais** — as simuladas só aparecem quando escolhidas. Foi o
    # que dispensou o padrão fixo em BRL: ele escondia as posições em USD
    # toda vez que a tela abria.
    portfolio_id, broker, selected_portfolio_id = selected_filters()
    group_by_broker = request.args.get("group_by_broker") == "1"
    try:
        selected_return_days = int(request.args.get("return_days", "365"))
    except ValueError:
        selected_return_days = 365
    if selected_return_days not in RETURN_PERIOD_DAYS:
        selected_return_days = 365
    selected_return_label = dict(RETURN_PERIODS)[selected_return_days]
    poll_interval = poll_interval_seconds()
    agent_check_interval = agent_check_interval_seconds()
    portfolio = build_portfolio(
        positions_query(portfolio_id, broker, group_by_broker=group_by_broker),
        stale_after_seconds=quote_stale_after_seconds(),
        return_period_days=selected_return_days,
    )
    expanded = expanded_position_ids()
    return {
        "portfolio": portfolio,
        # Resultado hipotético por aporte é exclusivo do extrato de Ações.
        # Cada mapa usa o mesmo snapshot de cotação já calculado para a linha;
        # não há uma nova consulta por movimento nem escrita dinâmica no ORM.
        "movement_results_by_position": {
            view.position.id: position_movement_results(
                view.position,
                view.metrics.current_price if view.metrics is not None else None,
            )
            for view in portfolio.positions
        },
        "expanded_positions": expanded,
        "expand_urls": {
            view.position.id: toggle_expanded_url(expanded, view.position.id)
            for view in portfolio.positions
        },
        "group_by_broker": group_by_broker,
        "poll_interval_seconds": poll_interval,
        "quote_refresh_retry_seconds": max(poll_interval, agent_check_interval),
        "selected_broker": broker or "",
        "selected_portfolio_id": selected_portfolio_id,
        "portfolios": portfolio_records(),
        "selected_return_days": selected_return_days,
        "selected_return_label": selected_return_label,
        "return_periods": RETURN_PERIODS,
    }


@bp.get("/")
def index() -> str:
    """Carteira: pagina inteira, ou so a regiao de resultados para o HTMX.

    A mesma URL serve os dois casos, entao o filtro pode empurrar ao
    historico o endereco real da pagina (`/?broker=...`) em vez do endereco
    de um fragmento. `HX-Request` decide apenas a forma da resposta; a
    autorizacao e identica nos dois caminhos.
    """
    results = portfolio_results_context()
    results["include_heartbeat_oob"] = is_htmx_request()
    if is_htmx_request():
        return render_template("partials/portfolio_results.html", **results)
    # O estado do coletor nao vem mais daqui: o liga/desliga mora em
    # Configuracoes e o pulso, na barra do menu, se atualiza por conta propria.
    return render_template("index.html", brokers=brokers(), **results)


@bp.get("/positions/new")
def new_position() -> str:
    return render_template(
        "position_form.html",
        position=None,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        sides=Side,
        portfolios=portfolio_records(),
    )


@bp.post("/positions")
def create_position() -> ResponseReturnValue:
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 422
    candidate = Position(**asdict(data))
    # Dois cliques em Salvar chegam como dois cadastros iguais, e o segundo é
    # indistinguível de um aporte real. Só o usuário sabe qual dos dois é.
    if request.form.get("confirm_duplicate") != "1" and duplicate_entry(candidate) is not None:
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
            duplicate_warning=True,
        ), 409
    try:
        # Carteira Simulada não funde uma segunda entrada: rejeita em
        # vez de tratar como aporte. `duplicate_entry` acima não pega esse
        # caso porque uma posição simulada nunca tem movimento algum no
        # extrato, então nunca é vista como "idêntica ao anterior".
        position, merged = create_or_merge_position(candidate)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 409
    db.session.commit()
    if merged:
        flash(
            f"Aporte unificado à posição já existente em {position.ticker} · "
            f"{position.broker}: quantidade somada e custo médio recalculado. "
            "Os parâmetros da posição anterior (delta da cotação, multiplicador "
            "do target e modo de resultado) foram preservados.",
            "success",
        )
    else:
        flash("Posição adicionada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.get("/positions/<int:position_id>/edit")
def edit_position(position_id: int) -> str:
    position = db.get_or_404(Position, position_id)
    return render_template(
        "position_form.html",
        position=position,
        movement_count=len(position.movements),
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        sides=Side,
        portfolios=portfolio_records(),
    )


@bp.post("/positions/<int:position_id>")
def update_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "position_form.html",
            position=request.form,
            edit_mode=True,
            position_id=position_id,
            movement_count=len(position.movements),
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 422
    previous_quantity = position.quantity
    previous_average_cost = position.average_cost
    was_simulated = position.simulated
    for key, value in asdict(data).items():
        setattr(position, key, value)
    # `position.simulated` lê `portfolio_ref.simulated`: um relacionamento
    # já carregado (pelo `was_simulated` acima) fica em cache no objeto e
    # não percebe sozinho que `portfolio_id` acabou de mudar — expirar
    # força a releitura pela FK nova antes de qualquer decisão que dependa
    # dela daqui pra baixo (aqui, em `record_position_adjustment` e em
    # `sync_open_transaction_for_position`).
    db.session.expire(position, ["portfolio_ref"])
    # Troca de carteira entre a Simulada e uma real: ao entrar na
    # Simulada, apaga a linha aberta e o extrato — as duas funções abaixo só
    # sabem adicionar, nunca apagar. Ao sair da Simulada não há nada
    # dedicado a fazer: o extrato está vazio e a linha aberta não existe, e é
    # exatamente esse estado que as duas funções tratam como "posição sem
    # histórico ainda" e preenchem sozinhas.
    if position.simulated and not was_simulated:
        discard_simulation_history(position)
    record_position_adjustment(position, previous_quantity, previous_average_cost)
    sync_open_transaction_for_position(position)
    db.session.commit()
    flash("Posição atualizada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.post("/positions/<int:position_id>/delete")
def delete_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    delete_open_transaction_for_position(position.id)
    db.session.delete(position)
    db.session.commit()
    flash("Posição excluída.", "success")
    return redirect(url_for("portfolio.index"))


@bp.get("/positions/<int:position_id>/close")
def close_position_form(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    if position.simulated:
        # O botão já não aparece na grade (apresentação); isso cobre quem
        # chega direto pela URL. A guarda que realmente vale está no POST
        # (`close_open_position`), que recusa mesmo sem passar por aqui.
        flash(
            "A carteira Simulada não permite encerramento. Exclua a posição para desfazê-la.",
            "error",
        )
        return redirect(url_for("portfolio.index"))
    default_price = position.quote.last_price if position.quote else position.average_cost
    return render_template(
        "close_position_form.html",
        position=position,
        default_price=default_price,
        default_date=date.today().isoformat(),
        movements=position.movements,
    )


@bp.post("/positions/<int:position_id>/close")
def close_position(position_id: int) -> ResponseReturnValue:
    """Encerra a posição por inteiro ou apenas a quantidade informada.

    A quantidade é opcional: sem ela, encerra tudo — o comportamento anterior
    a este formulário ganhar o campo. A validação contra a quantidade em
    carteira fica em ``close_open_position``, onde a posição está travada;
    conferir aqui, antes do lock, aceitaria um valor que deixou de ser válido
    no meio do caminho.
    """
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        exit_price = parse_finite_decimal(raw["exit_price"], field_name="um preço de saída")
        closed_on = date.fromisoformat(raw["closed_on"])
        quantity = (
            parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
            if raw.get("quantity")
            else None
        )
    except (KeyError, ValueError, ArithmeticError):
        flash("Informe um preço de saída, uma quantidade e uma data válidos.", "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    if exit_price < 0:
        flash("O preço de saída não pode ser negativo.", "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    try:
        transaction = close_open_position(position_id, exit_price, closed_on, quantity)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    if transaction is None:
        flash("A posição já foi encerrada ou não existe.", "error")
        return redirect(url_for("portfolio.transactions"))
    position = db.session.get(Position, position_id)
    if position is None:
        flash("Posição encerrada e registrada em Transações.", "success")
    else:
        flash(
            "Encerramento parcial registrado em Transações; o saldo continua "
            "na carteira.",
            "success",
        )
    return redirect(url_for("portfolio.transactions"))


def _render_exposure(
    template_context: Callable[[PortfolioView], dict[str, object]],
) -> str:
    """Renderiza uma das paginas de Analise > Exposicao.

    As tres paginas compartilham filtros, consulta e fragmento; so mudam os
    rotulos e qual recorte da carteira alimenta o grafico. Com `HX-Request`
    devolve so a regiao trocada pelo filtro.
    """
    portfolio_id, broker, selected_portfolio_id = selected_filters()
    # Exposição continua excluindo a carteira Simulada incondicionalmente
    # o filtro de Carteira aqui só oferece carteiras reais
    # (ver `real_portfolio_records`), mas a exclusão fica explícita na
    # consulta também, para o caso de uma URL manual apontar para a Simulada.
    portfolio = build_portfolio(
        positions_query(portfolio_id, broker, exclude_simulated=True),
        stale_after_seconds=quote_stale_after_seconds(),
    )
    context = {
        "portfolio": portfolio,
        "brokers": brokers(),
        "selected_broker": broker or "",
        "selected_portfolio_id": selected_portfolio_id,
        "portfolios": real_portfolio_records(),
        "group_rows": [],
        "group_heading": "",
        "missing_quote_rows": missing_quote_rows(portfolio.positions),
        **template_context(portfolio),
    }
    if is_htmx_request():
        return render_template("partials/exposure_results.html", **context)
    return render_template(context.pop("template"), **context)  # type: ignore[arg-type]


@bp.get("/analysis/exposure-asset")
def exposure_asset() -> str:
    return _render_exposure(
        lambda portfolio: {
            "template": "exposure_asset.html",
            "allocation_charts": allocation_chart_data(portfolio.positions),
            "converted_chart": converted_allocation_chart_data(
                portfolio.positions, latest_usd_brl_quote()
            ),
            "heading": "Alocacao por ativo",
            "subject": "ativo",
        }
    )


@bp.get("/analysis/exposure-broker")
def exposure_broker() -> str:
    return _render_exposure(
        lambda portfolio: {
            "template": "exposure_broker.html",
            "allocation_charts": broker_exposure_chart_data(portfolio.broker_groups),
            "converted_chart": converted_broker_exposure_chart_data(
                portfolio.broker_groups, latest_usd_brl_quote()
            ),
            "group_rows": exposure_group_rows(
                portfolio.broker_groups, lambda group: group.broker
            ),
            "group_heading": "Corretora",
            "heading": "Exposicao por corretora",
            "subject": "corretora",
        }
    )


@bp.get("/analysis/exposure-market")
def exposure_market() -> str:
    return _render_exposure(
        lambda portfolio: {
            "template": "exposure_market.html",
            "allocation_charts": market_exposure_chart_data(portfolio.market_groups),
            "converted_chart": converted_market_exposure_chart_data(
                portfolio.market_groups, latest_usd_brl_quote()
            ),
            "group_rows": exposure_group_rows(
                portfolio.market_groups, lambda group: group.market.value
            ),
            "group_heading": "Mercado",
            "heading": "Exposicao por mercado",
            "subject": "mercado",
        }
    )
