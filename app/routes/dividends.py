from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.orm import contains_eager
from werkzeug.exceptions import HTTPException

from app import db
from app.core.currency_filter import ALL
from app.core.validation import parse_finite_decimal
from app.dividends.cb_sync import (
    cb_brokers,
    comes_from_cb,
    cutoff_date,
    plan_cb_import,
    refusal_message,
)
from app.dividends.importer import (
    MAX_WORKBOOK_BYTES,
    DividendImportRow,
    DividendWorkbookError,
    read_dividend_workbook,
)
from app.dividends.yields import DerivedYield, derive_yields
from app.executions.conferencia import cb_config_from_app
from app.models import Broker, Dividend, IncomeKind, Quote, Ticker
from app.patrimonio.cb_client import CbUnavailableError, read_income_entries
from app.patrimonio.queries import position_timeline
from app.performance.dividends import build_dividend_report
from app.positions.holdings_history import QuantityTimeline, closing_price_on
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    current_owner_id,
    grant_ticker_entitlement,
    investable_ticker_records,
    is_htmx_request,
    open_real_average_cost_by_ticker,
    owned_or_404,
    parse_positive_id,
    price_series_by_ticker,
    selected_currency_filter,
)


@dataclass(frozen=True, slots=True)
class DividendInput:
    broker_id: int
    ticker_id: int
    amount: Decimal
    payment_date: date
    kind: IncomeKind
    com_date: date | None
    yield_on_cost: Decimal | None
    dividend_yield: Decimal | None
    quotas: Decimal | None


def _parse_form() -> DividendInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = parse_positive_id(raw["broker_id"])
        ticker_id = parse_positive_id(raw["ticker_id"])
        amount = parse_finite_decimal(raw["amount"], field_name="um valor de provento")
        payment_date = date.fromisoformat(raw["payment_date"])
        kind = IncomeKind(raw.get("kind", IncomeKind.DIVIDENDO.value))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    ticker = db.session.get(Ticker, ticker_id)
    broker = db.session.get(Broker, broker_id)
    if broker is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError("Esse ticker está marcado como referência de comparação.")
    if amount <= 0:
        raise ValueError("O valor do provento deve ser positivo.")
    if payment_date > date.today():
        raise ValueError("A data de pagamento de um provento recebido não pode estar no futuro.")
    if comes_from_cb(currency=ticker.currency, broker_name=broker.name, payment_date=payment_date):
        raise ValueError(refusal_message(broker.name))
    try:
        com_date = date.fromisoformat(raw["com_date"]) if raw.get("com_date") else None
        yield_on_cost = (
            parse_finite_decimal(raw["yield_on_cost"], field_name="o YOC")
            if raw.get("yield_on_cost")
            else None
        )
        dividend_yield = (
            parse_finite_decimal(raw["dividend_yield"], field_name="o DY")
            if raw.get("dividend_yield")
            else None
        )
        quotas = (
            parse_finite_decimal(raw["quotas"], field_name="as cotas")
            if raw.get("quotas")
            else None
        )
    except (ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if any(value is not None and value < 0 for value in (yield_on_cost, dividend_yield, quotas)):
        raise ValueError("YOC, DY e cotas não podem ser negativos.")
    return DividendInput(
        broker_id,
        ticker_id,
        amount,
        payment_date,
        kind,
        com_date,
        yield_on_cost,
        dividend_yield,
        quotas,
    )


def _parse_int_set(raw: str) -> set[int]:
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.add(parse_positive_id(part))
    return ids


def _parse_token_set(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _toggle_url(param: str, current: set[Any], token: Any) -> str:
    """Endereço da própria tela de Proventos com ``token`` invertido dentro
    do conjunto ``current`` guardado em ``param`` — mesma mecânica de
    ``routes.positions.toggle_expanded_url``, generalizada para os dois
    drill-downs desta tela (tickers e anos), que usam tipos de token
    diferentes (``int`` e ``"ano-moeda"``) mas o mesmo desenho: o estado
    aberto/fechado vive na URL, não no navegador, então uma atualização por
    HTMX (broker, outro `+`) nunca fecha o que já estava aberto.
    """
    args: dict[str, Any] = request.args.to_dict(flat=True)
    target = current ^ {token}
    if target:
        args[param] = ",".join(str(item) for item in sorted(target, key=str))
    else:
        args.pop(param, None)
    return url_for("portfolio.dividends", **args)


def _cutoff_subtotals(records: list[Dividend], cutoff: date) -> list[tuple[str, Decimal, int]]:
    """Saldo de corte: o que foi recebido antes do corte, por moeda (moeda, total, linhas).

    Até o corte os proventos são o histórico da planilha, congelado; daí em
    diante vêm do CB. O subtotal separa as duas fases nos relatórios.
    """
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for record in records:
        if record.payment_date < cutoff:
            totals[record.currency] = totals.get(record.currency, Decimal("0")) + record.amount
            counts[record.currency] = counts.get(record.currency, 0) + 1
    return [(currency, totals[currency], counts[currency]) for currency in sorted(totals)]


def _derived_yields(records: list[Dividend], average_cost: dict[int, Decimal]) -> dict[int, DerivedYield]:
    """YOC e DY das linhas sem cotas (as do CB), sobre as posições do dono."""
    pending = [record for record in records if record.quotas is None]
    if not pending:
        return {}
    ticker_ids = sorted({record.ticker_id for record in pending})
    # Uma só leitura das posições do dono (e do arquivo das encerradas), repartida
    # por corretora em memória: o número de consultas não cresce com as linhas.
    events = position_timeline(current_owner_id(), include_options=False, ticker_ids=ticker_ids)
    timelines: dict[int, QuantityTimeline] = {}

    def quantity_at(broker_id: int, ticker_id: int, on: date) -> Decimal:
        if broker_id not in timelines:
            timelines[broker_id] = QuantityTimeline([event for event in events if event.broker_id == broker_id])
        return timelines[broker_id].long_quantity_at(ticker_id, on)

    try:
        series = price_series_by_ticker(ticker_ids)
    except HTTPException:
        # Ticker sem direito de consulta de cotação: sem DY, mas a tela segue.
        series = {ticker_id: [] for ticker_id in ticker_ids}

    def price_on(ticker_id: int, on: date) -> Decimal | None:
        found = closing_price_on(series.get(ticker_id, []), on)
        return found[1] if found else None

    return derive_yields(pending, quantity_at=quantity_at, average_cost=average_cost, price_on=price_on)


def dividends_results_context() -> dict[str, object]:
    """Contexto da região de resultados de Proventos.

    Compartilhado entre a página inteira e o fragmento atualizado por HTMX,
    para que os dois nunca divirjam.
    """
    broker = request.args.get("broker") or None
    expanded_tickers = _parse_int_set(request.args.get("expanded_tickers", ""))
    expanded_years = _parse_token_set(request.args.get("expanded_years", ""))

    statement = (
        select(Dividend)
        .where(Dividend.owner_id == current_owner_id(), Dividend.superseded_at.is_(None))
        .join(Dividend.broker_ref)
        .join(Dividend.ticker_ref)
        # Os joins já trazem corretora e ticker: sem isto, cada linha
        # buscava os dois de novo, uma consulta por valor distinto.
        .options(contains_eager(Dividend.broker_ref), contains_eager(Dividend.ticker_ref))
        .order_by(Dividend.payment_date.desc(), Dividend.id.desc())
    )
    if broker:
        statement = statement.where(Broker.name == broker)
    records = list(db.session.scalars(statement))
    selected_currency = selected_currency_filter()
    if selected_currency != ALL:
        records = [record for record in records if record.currency == selected_currency]

    totals_by_currency: dict[str, Decimal] = {}
    kind_currency_totals: dict[tuple[str, str], Decimal] = {}
    for record in records:
        totals_by_currency[record.currency] = (
            totals_by_currency.get(record.currency, Decimal("0")) + record.amount
        )
        kind_key = (record.kind, record.currency)
        kind_currency_totals[kind_key] = (
            kind_currency_totals.get(kind_key, Decimal("0")) + record.amount
        )
    # Ordem de IncomeKind (dividendo, JCP, aluguel), não alfabética — mesma
    # ordem das colunas do card "Por ativo" logo abaixo.
    totals_by_kind_currency = [
        (kind.value, currency, kind_currency_totals[(kind.value, currency)])
        for kind in IncomeKind
        for currency in sorted(
            currency for (raw_kind, currency) in kind_currency_totals if raw_kind == kind.value
        )
    ]

    quotes_by_ticker = {
        ticker_id: quote
        for ticker_id, quote in db.session.execute(select(Quote.ticker_id, Quote.last_price))
        if quote > 0
    }
    average_cost = open_real_average_cost_by_ticker()
    derived = _derived_yields(records, average_cost)
    report = build_dividend_report(
        records,
        average_cost_by_ticker=average_cost,
        quotes_by_ticker=quotes_by_ticker,
        derived_quotas={dividend_id: item.quotas for dividend_id, item in derived.items()},
    )
    cutoff = cutoff_date()
    return {
        "selected_broker": broker or "",
        "cutoff": cutoff,
        "cutoff_last_day": cutoff - timedelta(days=1),
        "cutoff_subtotals": _cutoff_subtotals(records, cutoff),
        "derived": derived,
        "totals_by_currency": sorted(totals_by_currency.items()),
        "totals_by_kind_currency": totals_by_kind_currency,
        "income_kinds": list(IncomeKind),
        "report": report,
        "expanded_tickers": expanded_tickers,
        "ticker_toggle_urls": {
            total.ticker_id: _toggle_url("expanded_tickers", expanded_tickers, total.ticker_id)
            for total in report.by_ticker
        },
        "expanded_years": expanded_years,
        "year_toggle_urls": {
            f"{total.year}-{total.currency}": _toggle_url(
                "expanded_years", expanded_years, f"{total.year}-{total.currency}"
            )
            for total in report.by_year
        },
    }


@bp.get("/dividends")
def dividends() -> str:
    """Proventos: página inteira, ou só a região de resultados para o HTMX.

    A mesma URL serve os dois casos, então o filtro pode empurrar ao
    histórico o endereço real da página (`/dividends?...`) em vez do
    endereço de um fragmento. `HX-Request` decide apenas a forma da
    resposta; a autorização é idêntica nos dois caminhos.
    """
    results = dividends_results_context()
    if is_htmx_request():
        return render_template("partials/dividends_results.html", **results)
    return render_template(
        "dividends.html",
        brokers=broker_records(),
        **results,
    )


@bp.get("/dividends/cb")
def dividends_cb() -> str:
    """Ensaio ao vivo dos proventos do CB e a fila de revisão (só leitura).

    Nada é gravado aqui: quem grava é o comando ``importar-proventos-cb
    --aplicar``. A fila lista o que o CRV não consegue importar sozinho (crédito
    sem ativo ou com ticker que o CRV não cadastra), sem criar ticker.
    """
    status, message, plan = "ok", "", None
    try:
        config = cb_config_from_app()
        if config is None:
            status = "nao_configurado"
        else:
            plan = plan_cb_import(current_owner_id(), read_income_entries(config))
    except CbUnavailableError as exc:
        status, message = "indisponivel", str(exc)
    return render_template(
        "dividends_cb.html",
        status=status,
        message=message,
        plan=plan,
        cutoff=cutoff_date(),
        brokers=cb_brokers(),
    )


@bp.get("/dividends/new")
def new_dividend() -> str:
    return render_template(
        "dividend_form.html",
        dividend=None,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        income_kinds=list(IncomeKind),
    )


@bp.post("/dividends")
def create_dividend() -> ResponseReturnValue:
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "dividend_form.html",
            dividend=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            income_kinds=list(IncomeKind),
        ), 422
    dividend = Dividend(owner_id=current_owner_id(), **asdict(data))
    db.session.add(dividend)
    grant_ticker_entitlement(
        user_id=dividend.owner_id, ticker_id=dividend.ticker_id, held_on=dividend.payment_date
    )
    db.session.commit()
    flash("Renda registrada.", "success")
    return redirect(url_for("portfolio.dividends"))


def _import_key(row: DividendImportRow, *, ticker_id: int, broker_id: int) -> tuple[object, ...]:
    return (ticker_id, broker_id, row.kind, row.payment_date, row.amount)


def _same_imported_values(dividend: Dividend, row: DividendImportRow) -> bool:
    return (
        dividend.com_date == row.com_date
        and dividend.yield_on_cost == row.yield_on_cost
        and dividend.dividend_yield == row.dividend_yield
        and dividend.quotas == row.quotas
        and dividend.invested_total == row.invested_total
        and dividend.market_total == row.market_total
        and dividend.average_price == row.average_price
        and dividend.quoted_price == row.quoted_price
        and dividend.amount_per_share == row.amount_per_share
    )


def _import_form() -> str:
    return render_template("dividend_import.html", cutoff=cutoff_date(), cb_brokers=cb_brokers())


@bp.route("/dividends/import", methods=["GET", "POST"])
def import_dividends() -> ResponseReturnValue:
    if request.method == "GET":
        return _import_form()
    uploaded = request.files.get("workbook")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".xlsx"):
        flash("Selecione uma planilha no formato XLSX.", "error")
        return _import_form(), 422
    content = uploaded.stream.read(MAX_WORKBOOK_BYTES + 1)
    try:
        workbook = read_dividend_workbook(content)
    except DividendWorkbookError as exc:
        flash(str(exc), "error")
        return _import_form(), 422

    owner_id = current_owner_id()
    tickers = {ticker.symbol.upper(): ticker for ticker in db.session.scalars(select(Ticker))}
    brokers = {broker.name.casefold(): broker for broker in db.session.scalars(select(Broker))}
    existing: dict[tuple[object, ...], Dividend] = {
        (record.ticker_id, record.broker_id, record.kind, record.payment_date, record.amount): record
        for record in db.session.scalars(select(Dividend).where(Dividend.owner_id == owner_id))
    }
    created = updated = ignored = 0
    failures = list(workbook.rejected_rows)
    missing_tickers: set[str] = set()
    for row in workbook.rows:
        ticker = tickers.get(row.ticker)
        if ticker is None:
            missing_tickers.add(row.ticker)
            continue
        broker = brokers.get(row.broker.casefold())
        if broker is None:
            failures.append(f"Linha {row.source_row}: corretora '{row.broker}' não está cadastrada.")
            continue
        if ticker.is_benchmark:
            failures.append(f"Linha {row.source_row}: {row.ticker} é um ticker de referência.")
            continue
        if comes_from_cb(currency=ticker.currency, broker_name=broker.name, payment_date=row.payment_date):
            failures.append(f"Linha {row.source_row}: {refusal_message(broker.name)}")
            continue
        key = _import_key(row, ticker_id=ticker.id, broker_id=broker.id)
        dividend = existing.get(key)
        if dividend is None:
            dividend = Dividend(
                owner_id=owner_id,
                ticker_id=ticker.id,
                broker_id=broker.id,
                amount=row.amount,
                kind=row.kind,
                payment_date=row.payment_date,
                com_date=row.com_date,
                yield_on_cost=row.yield_on_cost,
                dividend_yield=row.dividend_yield,
                quotas=row.quotas,
                invested_total=row.invested_total,
                market_total=row.market_total,
                average_price=row.average_price,
                quoted_price=row.quoted_price,
                amount_per_share=row.amount_per_share,
            )
            db.session.add(dividend)
            existing[key] = dividend
            grant_ticker_entitlement(user_id=owner_id, ticker_id=ticker.id, held_on=row.payment_date)
            created += 1
        elif _same_imported_values(dividend, row):
            ignored += 1
        else:
            dividend.com_date = row.com_date
            dividend.yield_on_cost = row.yield_on_cost
            dividend.dividend_yield = row.dividend_yield
            dividend.quotas = row.quotas
            dividend.invested_total = row.invested_total
            dividend.market_total = row.market_total
            dividend.average_price = row.average_price
            dividend.quoted_price = row.quoted_price
            dividend.amount_per_share = row.amount_per_share
            updated += 1
    db.session.commit()
    flash(f"Importação concluída: {created} criado(s), {updated} atualizado(s), {ignored} duplicado(s) ignorado(s).", "success")
    if failures:
        flash(f"{len(failures)} linha(s) rejeitada(s): " + " ".join(failures[:5]), "error")
    if missing_tickers:
        flash("Tickers não cadastrados: " + ", ".join(sorted(missing_tickers)) + ".", "error")
    return redirect(url_for("portfolio.dividends"))


def _editable_dividend(dividend_id: int) -> Dividend:
    """Provento do dono que a tela pode alterar: só linha de planilha e ativa.

    Linha do CB é somente leitura (o dinheiro é do CB; a correção é lá) e linha
    substituída só volta pelo comando de desfazer, então a tela não a enxerga.
    """
    dividend = owned_or_404(Dividend, dividend_id)
    if dividend.superseded_at is not None:
        abort(404)
    if dividend.from_cb:
        abort(403, description="Provento do Controle Bancário é somente leitura; corrija o lançamento no CB.")
    return dividend


@bp.get("/dividends/<int:dividend_id>/edit")
def edit_dividend(dividend_id: int) -> str:
    dividend = _editable_dividend(dividend_id)
    return render_template(
        "dividend_form.html",
        dividend=dividend,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        income_kinds=list(IncomeKind),
    )


@bp.post("/dividends/<int:dividend_id>")
def update_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = _editable_dividend(dividend_id)
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "dividend_form.html",
            dividend=request.form,
            edit_mode=True,
            dividend_id=dividend_id,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            income_kinds=list(IncomeKind),
        ), 422
    for key, value in asdict(data).items():
        setattr(dividend, key, value)
    grant_ticker_entitlement(
        user_id=dividend.owner_id, ticker_id=dividend.ticker_id, held_on=dividend.payment_date
    )
    db.session.commit()
    flash("Renda atualizada.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.post("/dividends/<int:dividend_id>/delete")
def delete_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = _editable_dividend(dividend_id)
    db.session.delete(dividend)
    db.session.commit()
    flash("Renda excluída.", "success")
    return redirect(url_for("portfolio.dividends"))
