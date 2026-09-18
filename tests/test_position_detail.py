"""A tela analítica de uma posição reaproveita o domínio, sem recalcular P/L."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.routes.positions import _grafico_de_fechamentos


def test_grafico_de_fechamentos_exige_serie_minima() -> None:
    assert _grafico_de_fechamentos([]) is None
    assert _grafico_de_fechamentos([SimpleNamespace(price=Decimal("10"))]) is None


def test_grafico_de_fechamentos_usa_coordenadas_svg_com_ponto() -> None:
    grafico = _grafico_de_fechamentos(
        [SimpleNamespace(price=Decimal("10")), SimpleNamespace(price=Decimal("15"))]
    )

    assert grafico is not None
    assert grafico["pontos"] == "12.0,168.0 628.0,12.0"
    assert grafico["minimo"] == "10"
    assert grafico["maximo"] == "15"


def test_template_da_posicao_respeita_csp_e_reaproveita_o_extrato() -> None:
    template = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "position_detail.html"
    ).read_text(encoding="utf-8")

    assert "<script" not in template
    assert "style=" not in template
    assert 'include "partials/position_movements.html"' in template
