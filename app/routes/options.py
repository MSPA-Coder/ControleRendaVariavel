from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    AppSetting,
    Broker,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    Portfolio,
    Side,
    Ticker,
)
from app.option_portfolio import build_option_portfolio
from app.option_position_closure import (
    close_open_position,
    create_or_merge_position,
    delete_open_transaction_for_position,
    discard_simulation_history,
    duplicate_entry,
    record_position_adjustment,
    sync_open_transaction_for_position,
)
from app.pricing_settings import DEFAULT_RISK_FREE_RATE_ANNUAL
from app.routes.helpers import (
    brokers,
    investable_ticker_records,
    is_htmx_request,
    option_contracts,
    option_expirations,
    portfolio_records,
    selected_filters,
)
from app.validation import parse_finite_decimal

bp = Blueprint("options", __name__)


@dataclass(frozen=True, slots=True)
class OptionPositionInput:
    broker_id: int
    contract_id: int
    quantity: Decimal
    average_cost: Decimal
    target_price: Decimal | None
    side: Side
    opened_on: date
    result_mode: str
    portfolio_id: int


def _positions(
    portfolio_id: int | None = None, broker: str | None = None
) -> list[OptionPosition]:
    statement = (
        select(OptionPosition)
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
    return list(db.session.scalars(statement).unique())


def expanded_position_ids() -> set[int]:
    """Posições de opção com o extrato aberto na grade.

    Mesma mecânica de ``routes.positions.expanded_position_ids`` (ações): o
    estado vive na própria URL (``?expanded=3,7``), não no navegador, para
    sobreviver à atualização automática que substitui o fragmento inteiro.
    """
    raw = request.args.get("expanded", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def toggle_expanded_url(expanded: set[int], position_id: int) -> str:
    """Endereço da própria grade com o extrato desta posição invertido."""
    args: dict[str, Any] = request.args.to_dict(flat=True)
    target = expanded ^ {position_id}
    if target:
        args["expanded"] = ",".join(str(identifier) for identifier in sorted(target))
    else:
        args.pop("expanded", None)
    return url_for("options.index", **args)


def _brokers() -> list[Broker]:
    return list(db.session.scalars(select(Broker).order_by(Broker.name)))


def _contracts() -> list[OptionContract]:
    statement = (
        select(OptionContract)
        .options(
            joinedload(OptionContract.ticker_ref),
            joinedload(OptionContract.underlying_ticker_ref),
            joinedload(OptionContract.expiration),
        )
        .order_by(OptionExpiration.exercise_date, OptionContract.id)
        .join(OptionContract.expiration)
    )
    return list(db.session.scalars(statement).unique())


def _parse_position() -> OptionPositionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        contract_id = int(raw["contract_id"])
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        average_cost = parse_finite_decimal(raw["average_cost"], field_name="um custo médio")
        target_price = (
            parse_finite_decimal(raw["target_price"], field_name="um target")
            if raw.get("target_price")
            else None
        )
        side = Side(raw["side"])
        opened_on = date.fromisoformat(raw["opened_on"])
        portfolio_id = int(raw["portfolio_id"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if db.session.get(Broker, broker_id) is None:
        raise ValueError("Selecione uma corretora cadastrada.")
    contract = db.session.get(OptionContract, contract_id)
    if contract is None:
        raise ValueError("Selecione um contrato de opção cadastrado.")
    if db.session.get(Portfolio, portfolio_id) is None:
        raise ValueError("Selecione uma carteira cadastrada.")
    if quantity <= 0 or average_cost < 0 or (
        target_price is not None and target_price < 0
    ):
        raise ValueError("Quantidade deve ser positiva e preços não podem ser negativos.")
    result_mode = raw.get("result_mode", "").upper()
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    return OptionPositionInput(
        broker_id,
        contract_id,
        quantity,
        average_cost,
        target_price,
        side,
        opened_on,
        result_mode,
        portfolio_id,
    )


@bp.get("/options")
def index() -> str:
    """Opcoes: pagina inteira, ou so a regiao de resultados para o HTMX.

    Filtros de Carteira e Corretora seguem o mesmo ponto de entrada de Ações
    (``selected_filters``), com padrão "Todas" — só Ações abre já filtrada na
    carteira BRL (ver ``routes.positions.portfolio_results_context``).
    """
    settings = db.session.get(AppSetting, 1)
    risk_free_rate = (
        settings.risk_free_rate_annual if settings else DEFAULT_RISK_FREE_RATE_ANNUAL
    )
    portfolio_id, broker, selected_portfolio_id = selected_filters()
    portfolio = build_option_portfolio(
        _positions(portfolio_id, broker),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
        risk_free_rate_annual=risk_free_rate,
    )
    expanded = expanded_position_ids()
    context = {
        "portfolio": portfolio,
        "poll_interval_seconds": settings.poll_interval_seconds if settings else 2,
        "expanded_positions": expanded,
        "expand_urls": {
            view.position.id: toggle_expanded_url(expanded, view.position.id)
            for view in portfolio.positions
        },
        "selected_portfolio_id": selected_portfolio_id,
        "selected_broker": broker or "",
        "portfolios": portfolio_records(),
        "brokers": brokers(),
    }
    if is_htmx_request():
        return render_template("partials/options_results.html", **context)
    return render_template("options.html", **context)


@bp.get("/options/new")
def new_position() -> str:
    return render_template(
        "option_form.html",
        position=None,
        brokers=_brokers(),
        contracts=_contracts(),
        sides=Side,
        portfolios=portfolio_records(),
    )


@bp.post("/options/positions")
def create_position() -> ResponseReturnValue:
    try:
        data = _parse_position()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "option_form.html",
            position=request.form,
            brokers=_brokers(),
            contracts=_contracts(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 422
    candidate = OptionPosition(**asdict(data))
    # Dois cliques em Salvar chegam como dois cadastros iguais, e o segundo é
    # indistinguível de um aporte real. Só o usuário sabe qual dos dois é
    # (mesmo mecanismo de ``routes.positions.create_position``, para ações).
    if request.form.get("confirm_duplicate") != "1" and duplicate_entry(candidate) is not None:
        return render_template(
            "option_form.html",
            position=request.form,
            brokers=_brokers(),
            contracts=_contracts(),
            sides=Side,
            portfolios=portfolio_records(),
            duplicate_warning=True,
        ), 409
    try:
        # Carteira Simulada não funde uma segunda entrada — mesma
        # guarda de ``routes.positions.create_position``, para ações.
        position, merged = create_or_merge_position(candidate)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return render_template(
            "option_form.html",
            position=request.form,
            brokers=_brokers(),
            contracts=_contracts(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 409
    db.session.commit()
    if merged:
        flash(
            f"Aporte unificado à posição já existente em "
            f"{position.contract.ticker_ref.symbol} · {position.broker}: quantidade "
            "somada e custo médio recalculado. O target da posição anterior foi "
            "preservado.",
            "success",
        )
    else:
        flash("Posição de opção adicionada.", "success")
    return redirect(url_for("options.index"))


@bp.get("/options/positions/<int:position_id>/edit")
def edit_position(position_id: int) -> str:
    position = db.get_or_404(OptionPosition, position_id)
    return render_template(
        "option_form.html",
        position=position,
        movement_count=len(position.movements),
        brokers=_brokers(),
        contracts=_contracts(),
        sides=Side,
        portfolios=portfolio_records(),
    )


@bp.post("/options/positions/<int:position_id>")
def update_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(OptionPosition, position_id)
    try:
        data = _parse_position()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "option_form.html",
            position=request.form,
            edit_mode=True,
            position_id=position_id,
            movement_count=len(position.movements),
            brokers=_brokers(),
            contracts=_contracts(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 422
    previous_quantity = position.quantity
    previous_average_cost = position.average_cost
    was_simulated = position.simulated
    for key, value in asdict(data).items():
        setattr(position, key, value)
    # Relacionamento em cache não percebe sozinho a troca de FK — mesmo
    # motivo de ``routes.positions.update_position``, para ações.
    db.session.expire(position, ["portfolio_ref"])
    # Troca de carteira entre a Simulada e uma real — mesmo mecanismo de
    # ``routes.positions.update_position``, para ações.
    if position.simulated and not was_simulated:
        discard_simulation_history(position)
    record_position_adjustment(position, previous_quantity, previous_average_cost)
    sync_open_transaction_for_position(position)
    db.session.commit()
    flash("Posição de opção atualizada.", "success")
    return redirect(url_for("options.index"))


@bp.post("/options/positions/<int:position_id>/delete")
def delete_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(OptionPosition, position_id)
    delete_open_transaction_for_position(position.id)
    db.session.delete(position)
    db.session.commit()
    flash("Posição de opção excluída.", "success")
    return redirect(url_for("options.index"))


@bp.get("/options/positions/<int:position_id>/close")
def close_position_form(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(OptionPosition, position_id)
    if position.simulated:
        # Mesma guarda de ``routes.positions.close_position_form``, para
        # ações; a que vale de fato é a do POST (``close_open_position``).
        flash(
            "A carteira Simulada não permite encerramento. Exclua a posição para desfazê-la.",
            "error",
        )
        return redirect(url_for("options.index"))
    default_price = position.quote.last_price if position.quote else position.average_cost
    return render_template(
        "close_option_form.html",
        position=position,
        default_price=default_price,
        default_date=date.today().isoformat(),
        movements=position.movements,
    )


@bp.post("/options/positions/<int:position_id>/close")
def close_position(position_id: int) -> ResponseReturnValue:
    """Encerra uma posição de opção por inteiro ou apenas a quantidade
    informada (ver ``routes.positions.close_position``, o mesmo fluxo para
    ações)."""
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
        return redirect(url_for("options.close_position_form", position_id=position_id))
    if exit_price < 0:
        flash("O preço de saída não pode ser negativo.", "error")
        return redirect(url_for("options.close_position_form", position_id=position_id))
    try:
        transaction = close_open_position(position_id, exit_price, closed_on, quantity)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("options.close_position_form", position_id=position_id))
    if transaction is None:
        flash("A posição já foi encerrada ou não existe.", "error")
        return redirect(url_for("portfolio.transactions"))
    position = db.session.get(OptionPosition, position_id)
    if position is None:
        flash("Posição de opção encerrada e registrada em Transações.", "success")
    else:
        flash(
            "Encerramento parcial registrado em Transações; o saldo continua "
            "na carteira.",
            "success",
        )
    return redirect(url_for("portfolio.transactions"))


@bp.get("/tables/options/expirations")
def table_expirations() -> ResponseReturnValue:
    return render_template("table_expirations.html", expirations=option_expirations())


@bp.get("/tables/options/contracts")
def table_contracts() -> ResponseReturnValue:
    return render_template(
        "table_contracts.html",
        contracts=option_contracts(),
        tickers=investable_ticker_records(),
        expirations=option_expirations(),
        option_types=OptionType,
    )


@bp.post("/tables/options/expirations")
def create_expiration() -> ResponseReturnValue:
    try:
        call_code = request.form["call_code"].strip().upper()
        put_code = request.form["put_code"].strip().upper()
        exercise_date = date.fromisoformat(request.form["exercise_date"])
        if len(call_code) != 5 or len(put_code) != 5:
            raise ValueError("Os códigos devem seguir o formato 2026A.")
        db.session.add(
            OptionExpiration(
                call_code=call_code,
                put_code=put_code,
                exercise_date=exercise_date,
            )
        )
        db.session.commit()
        flash("Vencimento adicionado.", "success")
    except (KeyError, ValueError, IntegrityError):
        db.session.rollback()
        flash("Vencimento inválido ou já cadastrado.", "error")
    return redirect(url_for("options.table_expirations"))


@bp.post("/tables/options/expirations/<int:expiration_id>/delete")
def delete_expiration(expiration_id: int) -> ResponseReturnValue:
    db.session.delete(db.get_or_404(OptionExpiration, expiration_id))
    try:
        db.session.commit()
        flash("Vencimento excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("O vencimento possui contratos e não pode ser excluído.", "error")
    return redirect(url_for("options.table_expirations"))


@bp.post("/tables/options/expirations/<int:expiration_id>")
def update_expiration(expiration_id: int) -> ResponseReturnValue:
    expiration = db.get_or_404(OptionExpiration, expiration_id)
    try:
        call_code = request.form["call_code"].strip().upper()
        put_code = request.form["put_code"].strip().upper()
        exercise_date = date.fromisoformat(request.form["exercise_date"])
        if len(call_code) != 5 or len(put_code) != 5:
            raise ValueError
        expiration.call_code = call_code
        expiration.put_code = put_code
        expiration.exercise_date = exercise_date
        db.session.commit()
        flash("Vencimento atualizado.", "success")
    except (KeyError, ValueError, IntegrityError):
        db.session.rollback()
        flash("Vencimento inválido ou duplicado.", "error")
    return redirect(url_for("options.table_expirations"))


@bp.post("/tables/options/contracts")
def create_contract() -> ResponseReturnValue:
    try:
        ticker_id = int(request.form["ticker_id"])
        underlying_ticker_id = int(request.form["underlying_ticker_id"])
        expiration_id = int(request.form["expiration_id"])
        option_type = OptionType(request.form["option_type"])
        strike = parse_finite_decimal(request.form["strike"], field_name="um strike")
        if strike < 0 or ticker_id == underlying_ticker_id:
            raise ValueError
        ticker = db.session.get(Ticker, ticker_id)
        underlying = db.session.get(Ticker, underlying_ticker_id)
        if ticker is None or underlying is None:
            raise ValueError
        if ticker.is_benchmark or underlying.is_benchmark:
            raise ValueError
        db.session.add(
            OptionContract(
                ticker_id=ticker_id,
                underlying_ticker_id=underlying_ticker_id,
                expiration_id=expiration_id,
                option_type=option_type,
                strike=strike,
            )
        )
        db.session.commit()
        flash("Contrato de opção adicionado.", "success")
    except (KeyError, ValueError, ArithmeticError, IntegrityError):
        db.session.rollback()
        flash("Contrato inválido ou ticker já associado a uma opção.", "error")
    return redirect(url_for("options.table_contracts"))


@bp.post("/tables/options/contracts/<int:contract_id>/delete")
def delete_contract(contract_id: int) -> ResponseReturnValue:
    db.session.delete(db.get_or_404(OptionContract, contract_id))
    try:
        db.session.commit()
        flash("Contrato excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("O contrato possui posições e não pode ser excluído.", "error")
    return redirect(url_for("options.table_contracts"))


@bp.post("/tables/options/contracts/<int:contract_id>")
def update_contract(contract_id: int) -> ResponseReturnValue:
    contract = db.get_or_404(OptionContract, contract_id)
    try:
        ticker_id = int(request.form["ticker_id"])
        underlying_ticker_id = int(request.form["underlying_ticker_id"])
        strike = parse_finite_decimal(request.form["strike"], field_name="um strike")
        if strike < 0 or ticker_id == underlying_ticker_id:
            raise ValueError
        ticker = db.session.get(Ticker, ticker_id)
        underlying = db.session.get(Ticker, underlying_ticker_id)
        if ticker is None or underlying is None:
            raise ValueError
        if ticker.is_benchmark or underlying.is_benchmark:
            raise ValueError
        contract.ticker_id = ticker_id
        contract.underlying_ticker_id = underlying_ticker_id
        contract.expiration_id = int(request.form["expiration_id"])
        contract.option_type = OptionType(request.form["option_type"])
        contract.strike = strike
        db.session.commit()
        flash("Contrato atualizado.", "success")
    except (KeyError, ValueError, ArithmeticError, IntegrityError):
        db.session.rollback()
        flash("Contrato inválido ou ticker já associado a uma opção.", "error")
    return redirect(url_for("options.table_contracts"))
