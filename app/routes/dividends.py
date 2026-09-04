from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.core.validation import parse_finite_decimal
from app.models import Broker, Dividend, IncomeKind, Ticker
from app.performance.dividends import build_dividend_report
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    investable_ticker_records,
    is_htmx_request,
    open_real_cost_basis_by_ticker,
)


@dataclass(frozen=True, slots=True)
class DividendInput:
    broker_id: int
    ticker_id: int
    amount: Decimal
    payment_date: date
    kind: IncomeKind
    notes: str | None


def _parse_form() -> DividendInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        ticker_id = int(raw["ticker_id"])
        amount = parse_finite_decimal(raw["amount"], field_name="um valor de provento")
        payment_date = date.fromisoformat(raw["payment_date"])
        kind = IncomeKind(raw.get("kind", IncomeKind.DIVIDENDO.value))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    ticker = db.session.get(Ticker, ticker_id)
    if db.session.get(Broker, broker_id) is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError("Esse ticker está marcado como referência de comparação.")
    if amount <= 0:
        raise ValueError("O valor do provento deve ser positivo.")
    if payment_date > date.today():
        raise ValueError("A data de pagamento de um provento recebido não pode estar no futuro.")
    notes = raw.get("notes") or None
    return DividendInput(broker_id, ticker_id, amount, payment_date, kind, notes)


def _parse_int_set(raw: str) -> set[int]:
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
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
        .join(Dividend.broker_ref)
        .join(Dividend.ticker_ref)
        .order_by(Dividend.payment_date.desc(), Dividend.id.desc())
    )
    if broker:
        statement = statement.where(Broker.name == broker)
    records = list(db.session.scalars(statement))

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

    report = build_dividend_report(records, open_real_cost_basis_by_ticker())
    return {
        "selected_broker": broker or "",
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
    db.session.add(Dividend(**asdict(data)))
    db.session.commit()
    flash("Renda registrada.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.get("/dividends/<int:dividend_id>/edit")
def edit_dividend(dividend_id: int) -> str:
    dividend = db.get_or_404(Dividend, dividend_id)
    return render_template(
        "dividend_form.html",
        dividend=dividend,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        income_kinds=list(IncomeKind),
    )


@bp.post("/dividends/<int:dividend_id>")
def update_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = db.get_or_404(Dividend, dividend_id)
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
    db.session.commit()
    flash("Renda atualizada.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.post("/dividends/<int:dividend_id>/delete")
def delete_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = db.get_or_404(Dividend, dividend_id)
    db.session.delete(dividend)
    db.session.commit()
    flash("Renda excluída.", "success")
    return redirect(url_for("portfolio.dividends"))
