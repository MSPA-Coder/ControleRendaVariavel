from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("America/Sao_Paulo")
"""Fuso de referência do mercado. Define o "dia" de um snapshot de cotação
e a formatação de horários de leitura exibidos na interface."""

ZERO = Decimal("0")
ONE = Decimal("1")
LIQUID_FACTOR = Decimal("0.9996")
MONEY_QUANT = Decimal("0.01")
COST_QUANT = Decimal("0.00000001")
"""Escala das colunas de custo e quantidade (``Numeric(24, 8)``). O custo
médio ponderado é arredondado explicitamente aqui em vez de deixar o driver
truncar em silêncio na gravação — a política de arredondamento é do domínio."""


def q_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def weighted_average_cost(
    held_quantity: Decimal,
    held_average_cost: Decimal,
    added_quantity: Decimal,
    added_average_cost: Decimal,
) -> Decimal:
    """Custo médio de uma posição depois de um aporte no mesmo ativo.

    É a média ponderada pela quantidade — o mesmo critério do preço médio
    usado na apuração brasileira e na aba **Ações** da planilha. Um aumento
    de posição altera o custo médio, mas nunca realiza resultado: o que foi
    pago antes continua valendo pelo que foi pago.
    """
    total_quantity = held_quantity + added_quantity
    if total_quantity <= ZERO:
        raise ValueError("A quantidade resultante de um aporte deve ser positiva.")
    total_cost = held_quantity * held_average_cost + added_quantity * added_average_cost
    return q_cost(total_cost / total_quantity)


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == ZERO else numerator / denominator


def operation_result(
    side: str,
    quantity: Decimal,
    average_cost: Decimal,
    current_price: Decimal,
    result_mode: str,
) -> Decimal:
    """Equivalent to ResultadoOperacao for the arguments used by sheet Ações."""
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    direction = ONE if side == "C" else -ONE
    gross = direction * quantity * (current_price - average_cost)
    return gross * LIQUID_FACTOR if result_mode == "L" else gross


def signed_period_return(
    total_return: Decimal | None,
    elapsed_days: int,
    period_days: int,
) -> Decimal | None:
    """Project a position return to the selected workbook horizon."""
    if total_return is None or elapsed_days <= 0 or period_days <= 0:
        return None
    sign = -ONE if total_return < ZERO else ONE
    base = ONE + abs(total_return)
    with localcontext() as context:
        context.prec = 28
        exponent = Decimal(period_days) / Decimal(elapsed_days)
        return sign * (base**exponent - ONE)


@dataclass(frozen=True, slots=True)
class PositionMetrics:
    days: int
    current_price: Decimal
    daily_variation: Decimal | None
    result: Decimal
    return_pct: Decimal | None
    period_return: Decimal | None
    stop_gain: Decimal
    distance_to_target: Decimal | None
    breakeven: Decimal | None
    unwind_value: Decimal
    build_value: Decimal


def calculate_position(
    *,
    side: str,
    quantity: Decimal,
    average_cost: Decimal,
    raw_price: Decimal,
    previous_close: Decimal,
    quote_multiplier: Decimal,
    target_multiplier: Decimal,
    opened_on: date,
    result_mode: str,
    return_period_days: int = 365,
    today: date | None = None,
) -> PositionMetrics:
    current = raw_price * quote_multiplier
    previous = previous_close * quote_multiplier
    direction = ONE if side == "C" else -ONE
    result = operation_result(side, quantity, average_cost, current, result_mode)
    invested = quantity * average_cost
    return_pct = safe_div(result, invested)
    days = ((today or date.today()) - opened_on).days
    stop_gain = average_cost * target_multiplier
    distance_to_target = safe_div(stop_gain, current)
    if distance_to_target is not None:
        distance_to_target -= ONE
    breakeven = (
        safe_div(current, average_cost)
        if average_cost < current
        else safe_div(average_cost, current)
    )
    if breakeven is not None:
        breakeven = breakeven - ONE if average_cost < current else -(breakeven - ONE)
    daily = safe_div(previous, current)
    if daily is not None:
        daily = direction * (ONE - daily)
    return PositionMetrics(
        days=days,
        current_price=current,
        daily_variation=daily,
        result=result,
        return_pct=return_pct,
        period_return=signed_period_return(return_pct, days, return_period_days),
        stop_gain=stop_gain,
        distance_to_target=distance_to_target,
        breakeven=breakeven,
        unwind_value=direction * quantity * current,
        build_value=-direction * quantity * average_cost,
    )


# --- Livro de posições (ações e opções) -------------------------------------
#
# O ciclo de vida de uma posição — abertura, aumento, ajuste, encerramento
# total ou parcial e o extrato que os registra — segue o mesmo algoritmo para
# ações (``Position``/``PositionMovement``) e para opções
# (``OptionPosition``/``OptionPositionMovement``): só a persistência muda.
# As funções abaixo isolam esse algoritmo sem depender de sessão, Flask ou
# modelo ORM, para que ``app.position_closure`` (ações) e seu equivalente de
# opções possam chamar a mesma lógica em vez de duplicá-la.


@dataclass(frozen=True, slots=True)
class StatementEntry:
    """Um lançamento do extrato, reduzido aos valores que o replay precisa.

    Espelha ``PositionMovement``/``OptionPositionMovement`` sem carregar a
    linha do banco: ``kind`` aceita tanto o enum do modelo quanto uma string
    simples, porque ambos comparam igual a ``"open"``, ``"increase"`` etc.
    (``PositionMovementKind`` é um ``StrEnum``).
    """

    kind: str
    quantity_delta: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class ReplayedEntry:
    """O saldo resultante depois de um lançamento do extrato."""

    resulting_quantity: Decimal
    resulting_average_cost: Decimal


def replay_statement(entries: Sequence[StatementEntry]) -> list[ReplayedEntry] | None:
    """Recalcula o saldo (quantidade, custo médio) depois de cada lançamento.

    ``None`` quando o extrato está vazio ou não começa com uma abertura: sem
    um lançamento de abertura não há de onde partir, e quem chama deve manter
    o estado gravado em vez de zerá-lo (ver ``position_closure.replay_movements``).

    Um aumento redefine o custo médio (média ponderada pela quantidade); um
    encerramento parcial só reduz a quantidade; um ajuste substitui os dois
    pelo valor informado no lançamento.
    """

    if not entries or entries[0].kind != "open":
        return None
    quantity = ZERO
    average_cost = ZERO
    replayed: list[ReplayedEntry] = []
    for entry in entries:
        if entry.kind == "open":
            quantity, average_cost = entry.quantity_delta, entry.price
        elif entry.kind == "increase":
            average_cost = weighted_average_cost(
                quantity, average_cost, entry.quantity_delta, entry.price
            )
            quantity += entry.quantity_delta
        elif entry.kind == "decrease":
            quantity += entry.quantity_delta
        else:
            quantity += entry.quantity_delta
            average_cost = entry.price
        replayed.append(
            ReplayedEntry(resulting_quantity=quantity, resulting_average_cost=average_cost)
        )
    return replayed


@dataclass(frozen=True, slots=True)
class ClosureSplit:
    """O resultado de encerrar (total ou parcialmente) uma posição."""

    closing_quantity: Decimal
    remaining_quantity: Decimal
    result: Decimal
    is_total: bool


def plan_position_closure(
    *,
    held_quantity: Decimal,
    average_cost: Decimal,
    side: str,
    result_mode: str,
    opened_on: date,
    closed_on: date,
    exit_price: Decimal,
    requested_quantity: Decimal | None,
) -> ClosureSplit:
    """Valida e calcula o encerramento de uma posição, total ou parcial.

    ``requested_quantity`` ausente (``None``) encerra tudo o que está em
    carteira. Levanta ``ValueError`` com a mesma mensagem que o usuário vê
    quando a data de encerramento antecede a abertura, ou quando a
    quantidade não é positiva ou supera o que está em carteira — mesma regra
    para ações e opções, só quem persiste o resultado muda.
    """

    if closed_on < opened_on:
        raise ValueError("A data de encerramento não pode ser anterior à data de abertura.")
    closing_quantity = held_quantity if requested_quantity is None else requested_quantity
    if closing_quantity <= ZERO or closing_quantity > held_quantity:
        raise ValueError(
            "A quantidade encerrada deve ser positiva e não pode superar a "
            "quantidade em carteira."
        )
    result = operation_result(side, closing_quantity, average_cost, exit_price, result_mode)
    remaining_quantity = held_quantity - closing_quantity
    return ClosureSplit(
        closing_quantity=closing_quantity,
        remaining_quantity=remaining_quantity,
        result=result,
        is_total=remaining_quantity == ZERO,
    )


def is_duplicate_entry(
    *,
    last_kind: str,
    last_quantity_delta: Decimal,
    last_price: Decimal,
    last_occurred_on: date,
    candidate_quantity: Decimal,
    candidate_price: Decimal,
    candidate_occurred_on: date,
) -> bool:
    """Um aporte repete, valor por valor, o último lançamento de abertura ou
    aumento do extrato.

    Serve à confirmação extra no cadastro: dois cliques em Salvar chegam como
    dois aportes iguais em milissegundos, e o segundo é indistinguível de um
    aporte real (ver ``position_closure.duplicate_entry``). Um encerramento
    ou ajuste nunca é considerado duplicata de um novo aporte.
    """

    if last_kind not in {"open", "increase"}:
        return False
    return (
        last_quantity_delta == candidate_quantity
        and last_price == candidate_price
        and last_occurred_on == candidate_occurred_on
    )
