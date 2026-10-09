from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from math import log10
from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.core import regional
from app.core.currency import converter_totais
from app.core.validation import parse_finite_decimal
from app.models import (
    Broker,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
)
from app.positions.average_cost_line import (
    Aporte,
    Degrau,
    aportes_da_posicao,
    degraus_da_posicao,
)
from app.positions.closure import (
    close_open_position,
    conflicting_position,
    create_or_merge_position,
    delete_open_transaction_for_position,
    discard_simulation_history,
    duplicate_entry,
    prior_opening,
    record_position_adjustment,
    replay_movements,
    sync_open_transaction_for_position,
)
from app.positions.portfolio import (
    PortfolioView,
    build_portfolio,
    data_quality,
    effective_position_quote,
    position_movement_results,
)
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
    current_owner_id,
    exposure_group_rows,
    grant_ticker_entitlement,
    investable_ticker_records,
    is_htmx_request,
    latest_usd_brl_quote,
    market_exposure_chart_data,
    missing_quote_rows,
    owned_or_404,
    owned_or_404_for_update,
    parse_positive_id,
    poll_interval_seconds,
    portfolio_records,
    position_dividend_allocations,
    positions_query,
    quote_stale_after_seconds,
    real_portfolio_records,
    selected_filters,
    usd_brl_rate_on,
)

RETURN_PERIODS = (
    (7, "Semanal"),
    (30, "Mensal"),
    (90, "Trimestral"),
    (182, "Semestral"),
    (365, "Anual"),
)
RETURN_PERIOD_DAYS = tuple(days for days, _ in RETURN_PERIODS)
RESULT_MODES = (
    ("acao", "Ação"),
    ("proventos", "Proventos"),
    ("acao_proventos", "Ação + Proventos"),
)
RESULT_MODE_LABELS = dict(RESULT_MODES)


# Janelas do gráfico de fechamentos, as mesmas do zoom da aba Cotações.
PERIODOS_DO_GRAFICO = (
    ("1m", "1 mês"),
    ("3m", "3 meses"),
    ("6m", "6 meses"),
    ("1y", "1 ano"),
    ("ytd", "YTD"),
    ("all", "Todo o período"),
)
PERIODO_PADRAO_DO_GRAFICO = "6m"
_MESES_DO_PERIODO = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}


def _inicio_da_janela(periodo: str, ultimo: date) -> date | None:
    """Primeiro dia da janela, contada para trás a partir do último
    fechamento, como no zoom de Cotações. ``None`` é o período inteiro.

    Recuar meses a partir do dia 31 cai no último dia do mês de destino.
    """
    if periodo == "ytd":
        return date(ultimo.year, 1, 1)
    meses = _MESES_DO_PERIODO.get(periodo)
    if meses is None:
        return None
    ano, indice = divmod(ultimo.year * 12 + ultimo.month - 1 - meses, 12)
    return date(ano, indice + 1, min(ultimo.day, calendar.monthrange(ano, indice + 1)[1]))


def _grafico_de_fechamentos(
    fechamentos: list[QuoteHistory], degraus: list[Degrau], aportes: list[Aporte]
) -> dict[str, Any] | None:
    """Geometria simples do histórico da posição, sem JavaScript.

    O gráfico é só uma leitura dos fechamentos que já existem no banco. Ele
    não chama o coletor, não preenche dias ausentes e não tenta transformar o
    preço bruto em valor a mercado: essa transformação continua em
    ``build_portfolio`` e depende dos parâmetros da posição.

    Sobre os fechamentos vão duas referências da posição (ver
    ``app.positions.average_cost_line``):

    - o custo médio em degraus: em cada fechamento vale o degrau mais recente
      até aquela data, e a troca de nível é um trecho vertical no primeiro
      fechamento em que o novo custo vigora;
    - uma linha horizontal por aporte (abertura ou aumento), no preço dele, do
      primeiro fechamento a partir da data do aporte até o último.

    A escala inclui as duas, para nenhuma linha sair do quadro. Os eixos são
    marcas: cinco preços no Y e até seis datas espaçadas no X.
    """
    if len(fechamentos) < 2:
        return None
    largura, altura = 640, 220
    esquerda, direita, topo, base = 70, 24, 12, 28
    valores = [fechamento.price for fechamento in fechamentos]
    datas = [fechamento.recorded_date for fechamento in fechamentos]
    custos: list[Decimal | None] = []
    for dia in datas:
        vigentes = [d.custo_medio for d in degraus if d.desde <= dia]
        custos.append(vigentes[-1] if vigentes else None)
    # Aporte posterior ao último fechamento ainda não tem onde começar.
    inicios = [
        (aporte, next(i for i, dia in enumerate(datas) if dia >= aporte.data))
        for aporte in aportes
        if aporte.data <= datas[-1] and aporte.preco > 0
    ]
    escala = (
        valores
        # Custo zero é permitido no domínio, mas não possui representação em
        # escala logarítmica; a referência correspondente fica oculta.
        + [custo for custo in custos if custo is not None and custo > 0]
        + [aporte.preco for aporte, _ in inicios]
    )
    menor, maior = min(escala), max(escala)
    log_menor, log_maior = log10(float(menor)), log10(float(maior))
    escala_constante = log_menor == log_maior
    if escala_constante:
        margem = log10(1.1)
        log_menor -= margem
        log_maior += margem
    amplitude_log = log_maior - log_menor
    ultimo = len(valores) - 1

    def x(indice: int) -> float:
        return esquerda + (largura - esquerda - direita) * indice / ultimo

    def y(valor: Decimal) -> float:
        return topo + (altura - topo - base) * (log_maior - log10(float(valor))) / amplitude_log

    def valor_da_marca(indice: int) -> Decimal:
        if not escala_constante and indice == 0:
            return menor
        if not escala_constante and indice == 4:
            return maior
        return Decimal(str(10 ** (log_menor + amplitude_log * indice / 4)))

    pontos = [f"{x(indice):.1f},{y(valor):.1f}" for indice, valor in enumerate(valores)]
    pontos_custo: list[str] = []
    anterior: Decimal | None = None
    for indice, custo in enumerate(custos):
        if custo is None or custo <= 0:
            continue
        if anterior is not None and custo != anterior:
            pontos_custo.append(f"{x(indice):.1f},{y(anterior):.1f}")
        pontos_custo.append(f"{x(indice):.1f},{y(custo):.1f}")
        anterior = custo
    marcas_x = min(6, len(valores))
    indices_x = sorted({round(k * ultimo / (marcas_x - 1)) for k in range(marcas_x)})
    return {
        "pontos": " ".join(pontos),
        "pontos_custo": " ".join(pontos_custo),
        "aportes": [
            {
                "pontos": f"{x(inicio):.1f},{y(aporte.preco):.1f} {x(ultimo):.1f},{y(aporte.preco):.1f}",
                "rotulo": aporte.rotulo,
                "preco": aporte.preco,
                "cor": indice % 4,
            }
            for indice, (aporte, inicio) in enumerate(inicios)
        ],
        "eixo_y": [
            {
                "y": f"{topo + (altura - topo - base) * (4 - k) / 4:.1f}",
                "valor": valor_da_marca(k),
            }
            for k in range(5)
        ],
        "eixo_x": [
            {"x": f"{x(indice):.1f}", "rotulo": regional.formatar_dia_mes_ano2(datas[indice])}
            for indice in indices_x
        ],
        "esquerda": str(esquerda),
        "direita": str(largura - direita),
        "base": str(altura - base),
        "largura": str(largura),
        "altura": str(altura),
        "minimo": str(min(valores)),
        "maximo": str(max(valores)),
    }


@dataclass(frozen=True, slots=True)
class PositionInput:
    broker_id: int
    ticker_id: int
    quantity: Decimal
    average_cost: Decimal
    side: Side
    opened_on: date
    target_multiplier: Decimal
    portfolio_id: int


def _parse_form() -> PositionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = parse_positive_id(raw["broker_id"])
        ticker_id = parse_positive_id(raw["ticker_id"])
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        average_cost = parse_finite_decimal(raw["average_cost"], field_name="um custo médio")
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
    if quantity <= 0 or average_cost < 0 or target_multiplier <= 0:
        raise ValueError(
            "Quantidade e multiplicador do target devem ser positivos; custo não pode ser negativo."
        )
    try:
        portfolio_id = parse_positive_id(raw["portfolio_id"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Selecione uma carteira.") from exc
    if db.session.scalar(select(Portfolio.id).where(Portfolio.id == portfolio_id, Portfolio.owner_id == current_owner_id())) is None:
        raise ValueError("Selecione uma carteira cadastrada.")
    return PositionInput(
        broker_id,
        ticker_id,
        quantity,
        average_cost,
        side,
        opened_on,
        target_multiplier,
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
        if part:
            ids.add(parse_positive_id(part))
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
    selected_result_mode = request.args.get("result_mode", "acao")
    if selected_result_mode not in RESULT_MODE_LABELS:
        selected_result_mode = "acao"
    poll_interval = poll_interval_seconds()
    agent_check_interval = agent_check_interval_seconds()
    positions = positions_query(portfolio_id, broker, group_by_broker=group_by_broker)
    dividends_by_position = (
        position_dividend_allocations(
            positions,
            portfolio_id=portfolio_id,
            broker=broker,
        )
        if selected_result_mode != "acao"
        else {}
    )
    portfolio = build_portfolio(
        positions,
        stale_after_seconds=quote_stale_after_seconds(),
        return_period_days=selected_return_days,
        result_mode=selected_result_mode,
        dividends_by_position=dividends_by_position,
    )
    data_da_tela = date.today()
    conversao = converter_totais(
        [(total.currency, total.current_total) for total in portfolio.currency_totals],
        referencia=data_da_tela,
        taxa_usd_brl=usd_brl_rate_on(data_da_tela),
    )
    expanded = expanded_position_ids()
    return {
        "portfolio": portfolio,
        "conversao": conversao,
        "moeda_base": "BRL",
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
        "selected_result_mode": selected_result_mode,
        "selected_result_label": RESULT_MODE_LABELS[selected_result_mode],
        "result_modes": RESULT_MODES,
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


@bp.get("/data-status")
def data_status() -> str:
    """Qualidade dos dados financeiros que sustentam a leitura da carteira."""

    portfolio = build_portfolio(
        positions_query(),
        stale_after_seconds=quote_stale_after_seconds(),
    )
    referencia = date.today()
    conversao = converter_totais(
        [(total.currency, total.current_total) for total in portfolio.currency_totals],
        referencia=referencia,
        taxa_usd_brl=usd_brl_rate_on(referencia),
    )
    return render_template(
        "data_status.html",
        quality=data_quality(portfolio.positions),
        conversao=conversao,
        referencia=referencia,
    )


@bp.get("/positions/<int:position_id>")
def position_detail(position_id: int) -> str:
    """A leitura analítica de uma posição que pertence ao usuário logado."""
    position = owned_or_404(Position, position_id)
    portfolio = build_portfolio(
        [position],
        stale_after_seconds=quote_stale_after_seconds(),
    )
    (item,) = portfolio.positions
    degraus = degraus_da_posicao(position)
    periodo = request.args.get("periodo", PERIODO_PADRAO_DO_GRAFICO)
    if periodo not in dict(PERIODOS_DO_GRAFICO):
        periodo = PERIODO_PADRAO_DO_GRAFICO
    # "Todo o período" é a vida inteira da posição, desde o primeiro dia em
    # que ela existe. Encerrada por inteiro, a posição deixa de existir e esta
    # tela responde 404.
    fechamentos = list(
        db.session.scalars(
            select(QuoteHistory)
            .where(
                QuoteHistory.ticker_id == position.ticker_id,
                QuoteHistory.recorded_date >= min(position.opened_on, degraus[0].desde),
            )
            .order_by(QuoteHistory.recorded_date)
        )
    )
    if fechamentos:
        inicio = _inicio_da_janela(periodo, fechamentos[-1].recorded_date)
        if inicio is not None:
            fechamentos = [f for f in fechamentos if f.recorded_date >= inicio]
    return render_template(
        "position_detail.html",
        periodo=periodo,
        periodos=PERIODOS_DO_GRAFICO,
        item=item,
        position=position,
        movement_results=position_movement_results(
            position, item.metrics.current_price if item.metrics is not None else None
        ),
        fechamentos=fechamentos,
        grafico=_grafico_de_fechamentos(
            fechamentos, degraus, aportes_da_posicao(position)
        ),
    )


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
    candidate = Position(owner_id=current_owner_id(), **asdict(data))
    earlier = prior_opening(candidate)
    if earlier is not None and request.form.get("confirm_prior_opening") != "1":
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
            prior_opening_warning=True,
            prior_opening_date=earlier.opened_on,
        ), 409
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
        position, merged = create_or_merge_position(
            candidate,
            confirm_prior_opening=request.form.get("confirm_prior_opening") == "1",
        )
        grant_ticker_entitlement(user_id=position.owner_id, ticker_id=position.ticker_id, held_on=position.opened_on)
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
        if earlier is not None:
            flash(
                f"Lançamento anterior confirmado para {position.ticker} · {position.broker}: "
                "ele passou a ser a abertura e o extrato foi recalculado em ordem cronológica.",
                "success",
            )
        else:
            flash(
                f"Aporte unificado à posição já existente em {position.ticker} · "
                f"{position.broker}: quantidade somada e custo médio recalculado. "
                "Os parâmetros da posição anterior (multiplicador do target e modo "
                "de resultado) foram preservados.",
                "success",
            )
    else:
        flash("Posição adicionada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.get("/positions/<int:position_id>/edit")
def edit_position(position_id: int) -> str:
    position = owned_or_404(Position, position_id)
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
    position = owned_or_404_for_update(Position, position_id)
    previous_ticker_id = position.ticker_id
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
    # Duas posições com a mesma chave são a mesma exposição contada duas
    # vezes; o índice único `uq_positions_chave` recusaria com erro 500.
    if conflicting_position(position) is not None:
        db.session.rollback()
        flash("Já existe uma posição nesta carteira, corretora, ativo e tipo. Para somar, registre um aporte; para juntar as duas, ajuste uma e exclua a outra.", "error")
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
    if position.ticker_id != previous_ticker_id:
        grant_ticker_entitlement(
            user_id=position.owner_id,
            ticker_id=position.ticker_id,
            held_on=position.opened_on,
        )
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
    position = owned_or_404_for_update(Position, position_id)
    delete_open_transaction_for_position(position.id, position.owner_id)
    db.session.delete(position)
    db.session.commit()
    flash("Posição excluída.", "success")
    return redirect(url_for("portfolio.index"))


def _owned_movement(position_id: int, movement_id: int, *, for_update: bool = False) -> tuple[Position, PositionMovement]:
    position = owned_or_404_for_update(Position, position_id) if for_update else owned_or_404(Position, position_id)
    movement = db.session.get(PositionMovement, movement_id)
    if movement is None or movement.position_id != position.id or movement.owner_id != current_owner_id():
        from flask import abort

        abort(404)
    return position, movement


def _movement_values() -> tuple[Decimal, Decimal, date]:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        price = parse_finite_decimal(raw["price"], field_name="um preço")
        occurred_on = date.fromisoformat(raw["occurred_on"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Informe quantidade, preço e data válidos.") from exc
    if quantity <= 0 or price < 0:
        raise ValueError("A quantidade deve ser positiva e o preço não pode ser negativo.")
    return quantity, price, occurred_on


@bp.get("/positions/<int:position_id>/movements/<int:movement_id>/edit")
def edit_position_movement(position_id: int, movement_id: int) -> str:
    position, movement = _owned_movement(position_id, movement_id)
    if movement.kind is PositionMovementKind.ADJUSTMENT:
        flash("Ajustes são corrigidos pela edição da posição.", "error")
        return redirect(url_for("portfolio.edit_position", position_id=position.id))
    return render_template("position_movement_form.html", position=position, movement=movement)


@bp.post("/positions/<int:position_id>/movements/<int:movement_id>")
def update_position_movement(position_id: int, movement_id: int) -> ResponseReturnValue:
    position, movement = _owned_movement(position_id, movement_id, for_update=True)
    if movement.kind is PositionMovementKind.ADJUSTMENT:
        flash("Ajustes são corrigidos pela edição da posição.", "error")
        return redirect(url_for("portfolio.edit_position", position_id=position.id))
    try:
        quantity, price, occurred_on = _movement_values()
        opening = next(
            (item for item in position.movements if item.kind is PositionMovementKind.OPEN), None
        )
        moves_before_opening = (
            movement.kind is not PositionMovementKind.OPEN
            and opening is not None
            and occurred_on < opening.occurred_on
        )
        if moves_before_opening and request.form.get("confirm_prior_opening") != "1":
            return render_template(
                "position_movement_form.html",
                position=position,
                movement=movement,
                values={"quantity": quantity, "price": price, "occurred_on": occurred_on},
                prior_opening_warning=True,
                prior_opening_date=opening.occurred_on,
            ), 409

        if moves_before_opening:
            # A cronologia do extrato define a abertura. Confirmada a correção,
            # o lançamento editado toma esse papel e a abertura antiga vira um
            # aumento, para que o replay recalcule todos os snapshots.
            opening.kind = PositionMovementKind.INCREASE
            movement.kind = PositionMovementKind.OPEN
        movement.quantity_delta = -quantity if movement.kind is PositionMovementKind.DECREASE else quantity
        movement.price = price
        movement.occurred_on = occurred_on
        position.opened_on = next(
            item.occurred_on for item in position.movements if item.kind is PositionMovementKind.OPEN
        )
        replay_movements(position)
        sync_open_transaction_for_position(position)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("portfolio.edit_position_movement", position_id=position_id, movement_id=movement_id))
    flash("Lançamento atualizado e extrato recalculado.", "success")
    return redirect(url_for("portfolio.index", expanded=position_id))


@bp.post("/positions/<int:position_id>/movements/<int:movement_id>/delete")
def delete_position_movement(position_id: int, movement_id: int) -> ResponseReturnValue:
    position, movement = _owned_movement(position_id, movement_id, for_update=True)
    replacement_opening: PositionMovement | None = None
    if movement.kind is PositionMovementKind.OPEN:
        remaining = sorted(
            (item for item in position.movements if item.id != movement.id),
            key=lambda item: (item.occurred_on, item.id or 0),
        )
        if not remaining or remaining[0].kind is not PositionMovementKind.INCREASE:
            flash(
                "A abertura só pode ser removida quando o próximo lançamento for um aumento.",
                "error",
            )
            return redirect(url_for("portfolio.index", expanded=position_id))
        # Sem a abertura original, o primeiro aumento cronológico é a nova
        # origem da posição. Promovê-lo antes do replay preserva a cadeia de
        # saldos e custos médios a partir da nova data inicial.
        replacement_opening = remaining[0]
        replacement_opening.kind = PositionMovementKind.OPEN
        position.opened_on = replacement_opening.occurred_on
    transaction = (
        db.session.get(Transaction, movement.transaction_id)
        if movement.transaction_id
        else None
    )
    position.movements.remove(movement)
    if transaction is not None:
        db.session.delete(transaction)
    try:
        replay_movements(position)
        sync_open_transaction_for_position(position)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("portfolio.index", expanded=position_id))
    if replacement_opening is not None:
        flash("Abertura removida; o primeiro aumento virou abertura e o extrato foi recalculado.", "success")
    else:
        flash("Lançamento removido e extrato recalculado.", "success")
    return redirect(url_for("portfolio.index", expanded=position_id))


@bp.get("/positions/<int:position_id>/close")
def close_position_form(position_id: int) -> ResponseReturnValue:
    position = owned_or_404(Position, position_id)
    if position.simulated:
        # O botão já não aparece na grade (apresentação); isso cobre quem
        # chega direto pela URL. A guarda que realmente vale está no POST
        # (`close_open_position`), que recusa mesmo sem passar por aqui.
        flash(
            "A carteira Simulada não permite encerramento. Exclua a posição para desfazê-la.",
            "error",
        )
        return redirect(url_for("portfolio.index"))
    default_price = effective_position_quote(position)[0] if position.quote else position.average_cost
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
    # A camada de domínio recebe apenas um id; confira o dono antes de ela
    # adquirir o lock e transformar a posição em transação.
    owned_or_404(Position, position_id)
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
        transaction = close_open_position(
            position_id,
            exit_price,
            closed_on,
            quantity,
            owner_id=current_owner_id(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    if transaction is None:
        flash("A posição já foi encerrada ou não existe.", "error")
        return redirect(url_for("portfolio.transactions"))
    position = db.session.scalar(select(Position).where(Position.id == position_id, Position.owner_id == current_owner_id()))
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
