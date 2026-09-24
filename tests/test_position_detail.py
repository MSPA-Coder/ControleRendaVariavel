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


def test_nenhum_template_tem_script_ou_estilo_inline() -> None:
    """A CSP recusa `<script>` sem `src` e o atributo `style`: o navegador
    descarta em silêncio, e a tela quebra sem erro no servidor."""
    import re

    raiz = Path(__file__).resolve().parents[1] / "app" / "templates"
    sobras = []
    for caminho in raiz.rglob("*.html"):
        fonte = caminho.read_text(encoding="utf-8")
        if re.search(r"<script\b(?![^>]*\bsrc=)[^>]*>", fonte) or re.search(r"\sstyle=", fonte):
            sobras.append(caminho.relative_to(raiz).as_posix())

    assert sobras == []
