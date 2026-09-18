"""Contrato puro do intervalo usado pelo dashboard patrimonial v2."""

from datetime import date
from decimal import Decimal

import pytest

from app.patrimonio.dashboard import _monthly_performance_points, parse_period, period_window
from app.positions.holdings_history import PortfolioFlowPoint


@pytest.mark.parametrize("period", ["week", "month", "quarter", "semester", "year", "all"])
def test_v2_aceita_periodos_publicados(period):
    assert parse_period(period) == period


def test_v2_rejeita_periodo_desconhecido():
    with pytest.raises(ValueError, match="Período inválido"):
        parse_period("daily")


def test_janela_de_periodo_e_inclusiva():
    reference = date(2026, 9, 18)
    start, end = period_window(reference, "week")
    assert (start, end) == (date(2026, 9, 12), reference)


def test_twr_parcial_soma_somente_os_pontos_do_intervalo_recebido():
    pontos = [
        PortfolioFlowPoint(
            observed_date=date(2026, 9, 12),
            value=Decimal("100"),
            net_flow=Decimal("100"),
            income_by_kind={},
        ),
        PortfolioFlowPoint(
            observed_date=date(2026, 9, 18),
            value=Decimal("110"),
            net_flow=Decimal("0"),
            income_by_kind={"dividendo": Decimal("2")},
        ),
    ]

    (mes,) = _monthly_performance_points(pontos)

    assert mes["data"] == "2026-09-18"
    assert mes["fluxo_neutralizado"] == "100.00"
    assert mes["renda_total"] == "2.00"
    assert mes["retorno_acumulado"] == "0.12"
