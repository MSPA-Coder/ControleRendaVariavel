from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import PositionMovementKind, Side
from app.portfolio import position_movement_results

ROOT = Path(__file__).parents[1]


def movement(identifier, kind, quantity, price, result=None):
    return SimpleNamespace(
        id=identifier,
        kind=kind,
        quantity=Decimal(quantity),
        price=Decimal(price),
        result=None if result is None else Decimal(result),
    )


def position(side, result_mode="L", *movements):
    return SimpleNamespace(
        side=side,
        result_mode=result_mode,
        movements=list(movements),
    )


@pytest.mark.parametrize(
    ("side", "mode", "expected"),
    [
        (Side.BUY, "L", Decimal("199.92")),
        (Side.BUY, "B", Decimal("200")),
        (Side.SELL, "L", Decimal("-199.92")),
        (Side.SELL, "B", Decimal("-200")),
    ],
)
def test_aporte_exibe_resultado_hipotetico_por_lote(side, mode, expected):
    opening = movement(1, PositionMovementKind.OPEN, "100", "10")

    results = position_movement_results(position(side, mode, opening), Decimal("12"))

    assert results == {1: expected}


def test_aumento_usa_quantidade_do_lote_e_cotacao_com_multiplicador_ja_aplicado():
    opening = movement(1, PositionMovementKind.OPEN, "25", "10")
    increase = movement(2, PositionMovementKind.INCREASE, "45", "8")

    # 25 * (12 - 10) + 45 * (12 - 8) seria o total; cada linha recebe só
    # sua própria contribuição. O multiplicador não é aplicado novamente.
    results = position_movement_results(
        position(Side.BUY, "B", opening, increase), Decimal("12")
    )

    assert results == {1: Decimal("50"), 2: Decimal("180")}


def test_encerramento_preserva_realizado_e_ajuste_nao_tem_resultado():
    decrease = movement(3, PositionMovementKind.DECREASE, "10", "14", "40")
    adjustment = movement(4, PositionMovementKind.ADJUSTMENT, "0", "12")

    results = position_movement_results(
        position(Side.BUY, "L", decrease, adjustment), Decimal("20")
    )

    assert results == {3: Decimal("40"), 4: None}


def test_aporte_sem_cotacao_fica_indisponivel_mas_realizado_continua_visivel():
    opening = movement(1, PositionMovementKind.OPEN, "10", "10")
    decrease = movement(2, PositionMovementKind.DECREASE, "2", "11", "2")

    results = position_movement_results(position(Side.BUY, "B", opening, decrease), None)

    assert results == {1: None, 2: Decimal("2")}


def test_extrato_de_acoes_recebe_mapa_e_partial_compartilhado_tem_fallback():
    actions_template = (
        ROOT / "app" / "templates" / "partials" / "portfolio_results.html"
    ).read_text(encoding="utf-8")
    movements_template = (
        ROOT / "app" / "templates" / "partials" / "position_movements.html"
    ).read_text(encoding="utf-8")

    assert "movement_results_by_position" in actions_template
    assert "movement_results is defined" in movements_template
    assert "movement.result" in movements_template
