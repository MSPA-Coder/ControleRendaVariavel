"""Montagem do dashboard JSON ``patrimonio/v2``.

Este módulo só agrega dados já persistidos. Nenhum movimento ou transação é
publicado individualmente; os dois entram apenas como insumos de cálculos.
"""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.core.domain import MARKET_TIMEZONE, operation_result, safe_div
from app.models import Side, Transaction
from app.patrimonio import queries
from app.positions.holdings_history import (
    DividendEvent,
    QuantityTimeline,
    portfolio_flow_series,
    prorate_dividends,
    twr_index_series,
)
from app.positions.portfolio import effective_position_quote

CENT = Decimal("0.01")
CONTRACT = "patrimonio/v2"
SYSTEM = "controle-renda-variavel"
PERIODS = frozenset({"week", "month", "quarter", "semester", "year", "all"})
DEFAULT_MAX_PUBLIC_HISTORY_DAYS = 3650


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _subtract_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 - months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def period_window(
    reference: date,
    period: str,
    *,
    max_history_days: int = DEFAULT_MAX_PUBLIC_HISTORY_DAYS,
) -> tuple[date, date]:
    if period == "all":
        return reference - timedelta(days=max_history_days), reference
    if period == "week":
        return reference - timedelta(days=6), reference
    months = {"month": 1, "quarter": 3, "semester": 6, "year": 12}
    return _subtract_months(reference, months[period]), reference


def parse_period(value: str | None) -> str:
    period = (value or "all").strip().lower()
    if period not in PERIODS:
        raise ValueError("Período inválido: use week, month, quarter, semester, year ou all.")
    return period


def _position_quality(position, reference: date) -> dict[str, object]:
    quote = position.quote
    if quote is None:
        return {"status": "missing", "motivo": "cotacao_ausente", "observado_em": None}
    _, observed = effective_position_quote(position)
    age = max((reference - observed.astimezone(MARKET_TIMEZONE).date()).days, 0)
    status = quote.source_status or "unknown"
    if status == "error":
        reason = "erro_na_fonte"
    elif age > 1:
        status, reason = "stale", "cotacao_desatualizada"
    else:
        reason = None
    return {
        "status": status,
        "motivo": reason,
        "fonte": SYSTEM,
        "observado_em": observed.isoformat(),
        "idade_dias": str(age),
    }


def _current_position(
    position,
    reference: date,
    *,
    titular: str | None = None,
    historical: bool = False,
) -> dict[str, object]:
    if historical:
        quality = {
            "status": "unavailable",
            "motivo": "cotacao_historica_nao_reconstruida",
            "observado_em": None,
        }
        quote = None
    else:
        quality = _position_quality(position, reference)
        quote = position.quote
    price = None
    result = cost = gross = ret = None
    if quote is not None:
        price, _ = effective_position_quote(position)
        cost = position.quantity * position.average_cost
        gross = position.quantity * price
        result = operation_result(
            position.side.value,
            position.quantity,
            position.average_cost,
            price,
            position.result_mode,
        )
        ret = safe_div(result, cost)
    link = f"/positions/{position.id}"
    row = {
        "id": f"{SYSTEM}:posicao:{position.id}",
        "titular": titular,
        "instituicao": _identity(position.broker),
        "instrumento": position.ticker,
        "classe": "acao",
        "mercado": position.market.value,
        "moeda": position.currency,
        "carteira": {
            "id": f"{SYSTEM}:carteira:{position.portfolio_id}",
            "nome": position.portfolio_ref.name,
            "simulada": bool(position.portfolio_ref.simulated),
        },
        "lado": position.side.value,
        "quantidade": _number(position.quantity),
        "preco": _number(price),
        "valor_a_mercado": _money(
            (Decimal("1") if position.side == Side.BUY else Decimal("-1")) * gross
            if gross is not None
            else None
        ),
        "preco_em": quality.get("observado_em"),
        "fonte_do_preco": SYSTEM,
        "situacao_do_preco": quality["status"],
        "exposicao_bruta": _money(gross),
        "custo_medio": _number(position.average_cost) if quote is not None else None,
        "custo_total": _money(cost),
        "resultado_nao_realizado": _money(result),
        "retorno_nao_realizado": _number(ret),
        "qualidade_preco": quality,
        "qualidade_do_preco": quality,
        "endereco": link,
        "endereco_transacao": "/transactions",
    }
    if quote is None:
        reason = "custo_e_resultado_dependem_de_cotacao"
        row["motivo_valores"] = reason
    return row


def _income_by_currency(dividends: list, period_start: date, reference: date) -> list[dict[str, object]]:
    totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    by_kind: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for item in dividends:
        if period_start <= item.payment_date <= reference:
            currency = item.currency
            by_kind[(currency, "all")][item.kind.value] += item.amount
            totals[(currency, "all")] += item.amount
    currencies = sorted({currency for currency, _ in totals})
    return [
        {
            "moeda": currency,
            "total": _money(totals[(currency, "all")]),
            "por_tipo": {
                kind: _money(value) for kind, value in sorted(by_kind[(currency, "all")].items())
            },
            "endereco": "/dividends",
        }
        for currency in currencies
    ]


def _realized(transactions: list[Transaction]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in transactions:
        grouped[(tx.currency, "opcao" if tx.is_option else "acao")].append(tx)
    result = []
    for currency, instrument in sorted(grouped):
        records = grouped[(currency, instrument)]
        values = [tx.result for tx in records if tx.result is not None]
        gains = sum((value for value in values if value > 0), Decimal("0"))
        losses = sum((value for value in values if value < 0), Decimal("0"))
        winners = sum(value > 0 for value in values)
        result.append(
            {
                "moeda": currency,
                "instrumento": instrument,
                "ganhos": _money(gains),
                "perdas": _money(losses),
                "resultado": _money(gains + losses),
                "transacoes": len(records),
                "vencedoras": winners,
                "win_rate": _number(safe_div(Decimal(winners), Decimal(len(records)))),
                "endereco": "/transactions",
            }
        )
    return result


def _monthly_performance_points(daily_points) -> list[dict[str, object]]:
    index = dict(twr_index_series(daily_points))
    monthly: dict[date, dict[str, object]] = {}
    for point in daily_points:
        month = point.observed_date.replace(day=1)
        row = monthly.setdefault(
            month,
            {
                "data": point.observed_date,
                "valor_a_mercado": point.value,
                "fluxo_neutralizado": Decimal("0"),
                "renda_total": Decimal("0"),
                "renda_por_tipo": defaultdict(lambda: Decimal("0")),
                "indice_twr": index[point.observed_date],
            },
        )
        row["data"] = point.observed_date
        row["valor_a_mercado"] = point.value
        row["fluxo_neutralizado"] += point.net_flow
        row["indice_twr"] = index[point.observed_date]
        for kind, amount in point.income_by_kind.items():
            row["renda_por_tipo"][kind] += amount
            row["renda_total"] += amount
    return [
        {
            "data": row["data"].isoformat(),
            "valor_a_mercado": _money(row["valor_a_mercado"]),
            "fluxo_neutralizado": _money(row["fluxo_neutralizado"]),
            "renda_total": _money(row["renda_total"]),
            "renda_por_tipo": {
                kind: _money(value)
                for kind, value in sorted(row["renda_por_tipo"].items())
            },
            "indice_twr": _number(row["indice_twr"]),
            "retorno_acumulado": _number(row["indice_twr"] - Decimal("1")),
        }
        for _month, row in sorted(monthly.items())
    ]


def _performance(
    reference: date, start: date, dividends: list, owner_id: int
) -> list[dict[str, object]]:
    # A fotografia publicada inclui ações. Manter opções fora da série evita
    # comparar um desempenho que contém instrumentos omitidos com um total que
    # não os contém.
    events = [
        event for event in queries.performance_events(reference, owner_id)
        if event.position_key[0] == "stock"
    ]
    tickers = queries.tickers(event.ticker_id for event in events)
    by_currency: dict[str, list] = defaultdict(list)
    for event in events:
        ticker = tickers.get(event.ticker_id)
        if ticker is not None:
            by_currency[ticker.currency].append(event)
    prices = queries.quote_series(tickers, start=start, end=reference)
    # A performance report uses proventos rateados by the same position
    # timeline as the existing HTML performance report.
    total_timeline = QuantityTimeline(events)
    result = []
    for currency, currency_events in sorted(by_currency.items()):
        ids = {event.ticker_id for event in currency_events}
        raw = [
            DividendEvent(item.payment_date, item.ticker_id, item.amount, item.kind.value)
            for item in dividends
            if item.ticker_id in ids
        ]
        income = prorate_dividends(raw, QuantityTimeline(currency_events), total_timeline)
        daily = [
            point
            for point in portfolio_flow_series(currency_events, prices, income)
            if start <= point.observed_date <= reference
        ]
        result.append(
            {
                "moeda": currency,
                "metodo": "TWR",
                "fluxo_metodo": "variacao_de_quantidade_a_preco_de_mercado",
                "inicio": start.isoformat(),
                "fim": reference.isoformat(),
                "pontos": _monthly_performance_points(daily),
                "endereco": "/performance",
            }
        )
    return result


def _identity(name: str) -> str:
    from app.routes.patrimonio import identidade

    return identidade(name)


def build_dashboard(
    *,
    titular: str,
    titular_nome: str | None = None,
    owner_id: int,
    reference: date,
    period: str,
    start_override: date | None = None,
    max_history_days: int = DEFAULT_MAX_PUBLIC_HISTORY_DAYS,
) -> dict[str, object]:
    today = datetime.now(MARKET_TIMEZONE).date()
    from app.routes.patrimonio import _Foto, _fotografar_hoje, _fotografar_passado

    # Keep the v1 list byte-for-byte compatible in shape. The enriched v2
    # list is separate so an unquoted position is visible to the dashboard.
    foto = _Foto(titular=titular)
    omitted = (
        _fotografar_hoje(foto, owner_id)
        if reference == today
        else _fotografar_passado(foto, reference, owner_id)
    )
    if reference == today:
        current_lines = [
            _current_position(position, reference, titular=titular)
            for position in queries.real_positions(owner_id)
        ]
    else:
        current_lines = [
            {
                **line,
                "carteira": None,
                "lado": None,
                "exposicao_bruta": _money(abs(Decimal(line["valor_a_mercado"]))),
                "custo_medio": None,
                "custo_total": None,
                "resultado_nao_realizado": None,
                "retorno_nao_realizado": None,
                "motivo_valores": "custo_historico_nao_confiavel",
            }
            for line in foto.linhas
        ]
    enriched_by_id = {line["id"]: line for line in current_lines}
    # The v2 ``posicoes`` list remains the v1 list, with the requested
    # position metrics added where there is a live source row. Consumers that
    # need missing quotes explicitly can use ``posicoes_atuais``.
    for line in foto.linhas:
        extra = enriched_by_id.get(line["id"])
        if extra is not None:
            keys = (
                "carteira",
                "lado",
                "exposicao_bruta",
                "custo_medio",
                "custo_total",
                "resultado_nao_realizado",
                "retorno_nao_realizado",
                "qualidade_preco",
                "qualidade_do_preco",
                "motivo_valores",
            )
            line.update({key: extra[key] for key in keys if key in extra})
    desde, _ = period_window(reference, period, max_history_days=max_history_days)
    if start_override is not None:
        desde = start_override
    # A lista legada keeps v1's one-year publication window even when the v2
    # dashboard asks for ``all`` performance. New aggregates use ``desde``.
    v1_desde = reference - timedelta(days=365)
    dividends = queries.dividends(min(v1_desde, desde), reference, owner_id)
    txs = queries.closed_transactions(desde, reference, owner_id)
    v1_dividends = [
        {
            "id": f"{SYSTEM}:provento:{item.id}",
            "titular": titular,
            "instituicao": foto.instituicao(item.broker),
            "instrumento": item.ticker,
            "tipo": item.kind.value,
            "moeda": item.currency,
            "valor": _money(item.amount),
            "data": item.payment_date.isoformat(),
        }
        for item in dividends
        if item.payment_date >= v1_desde
    ]
    portfolio_rows = [
        {
            "id": f"{SYSTEM}:carteira:{portfolio.id}",
            "nome": portfolio.name,
            "moeda": portfolio.currency,
            "simulada": bool(portfolio.simulated),
            "ativa": bool(portfolio.is_active),
            "endereco": "/tables/portfolios",
        }
        for portfolio in queries.portfolios(owner_id)
        if not portfolio.simulated
    ]
    endpoints = {
        "posicoes": "/",
        "transacoes": "/transactions",
        "proventos": "/dividends",
        "performance": "/performance",
        "qualidade": "/data-status",
    }
    estados_dos_precos: dict[str, int] = defaultdict(int)
    for linha in current_lines:
        qualidade = linha.get("qualidade_preco") or linha.get("qualidade_do_preco") or {}
        if isinstance(qualidade, dict):
            estados_dos_precos[str(qualidade.get("status") or "unknown")] += 1
    if omitted.get("sem_cotacao"):
        qualidade_geral = "parcial"
    elif any(estado in estados_dos_precos for estado in ("stale", "error")):
        qualidade_geral = "atencao"
    else:
        qualidade_geral = "ok"
    return {
        "contrato": CONTRACT,
        "sistema": SYSTEM,
        "papel": "investimento",
        "gerado_em": datetime.now(UTC).isoformat(),
        "data_de_referencia": reference.isoformat(),
        "periodo": {
            "nome": period,
            "inicio": desde.isoformat(),
            "fim": reference.isoformat(),
        },
        "titulares": [{"id": titular, "nome": titular_nome or titular}],
        "instituicoes": [foto.instituicoes[key] for key in sorted(foto.instituicoes)],
        "contas": [],
        "totais_por_moeda": [
            {"moeda": moeda, "total": _money(data["total"]), "linhas": data["linhas"]}
            for moeda, data in sorted(foto.totais.items())
        ],
        "posicoes": foto.linhas,
        "proventos": v1_dividends,
        "proventos_desde": v1_desde.isoformat(),
        "ativos_alternativos": [],
        "omitidas": omitted,
        "carteiras": portfolio_rows,
        "posicoes_atuais": current_lines,
        "desempenho_por_moeda": _performance(reference, desde, dividends, owner_id),
        "ganhos_realizados_por_moeda": _realized(txs),
        "renda_por_moeda": _income_by_currency(dividends, desde, reference),
        "enderecos": endpoints,
        "qualidade": {
            "status": qualidade_geral,
            "omitidas": omitted,
            "precos_por_estado": dict(sorted(estados_dos_precos.items())),
        },
        "motivos_historicos": (
            {"custo": "custo_historico_nao_confiavel", "resultado": "resultado_historico_nao_confiavel"}
            if reference != today
            else {}
        ),
    }
