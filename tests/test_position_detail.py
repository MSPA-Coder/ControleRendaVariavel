"""A tela analítica de uma posição reaproveita o domínio, sem recalcular P/L."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.positions.average_cost_line import Aporte, Degrau
from app.routes.positions import _grafico_de_fechamentos, _inicio_da_janela


def _fechamento(dia: int, preco: str) -> SimpleNamespace:
    return SimpleNamespace(recorded_date=date(2026, 1, dia), price=Decimal(preco))


def test_grafico_de_fechamentos_exige_serie_minima() -> None:
    assert _grafico_de_fechamentos([], [], []) is None
    assert _grafico_de_fechamentos([_fechamento(1, "10")], [], []) is None


# Área de plotagem: x de 70 a 616, y de 12 (topo) a 192 (base).


def test_grafico_de_fechamentos_usa_coordenadas_svg_com_ponto() -> None:
    grafico = _grafico_de_fechamentos([_fechamento(1, "10"), _fechamento(2, "15")], [], [])

    assert grafico is not None
    assert grafico["pontos"] == "70.0,192.0 616.0,12.0"
    assert grafico["pontos_custo"] == ""
    assert grafico["aportes"] == []
    assert grafico["minimo"] == "10"
    assert grafico["maximo"] == "15"


def test_custo_medio_muda_de_nivel_no_fechamento_em_que_passa_a_valer() -> None:
    """Aumento no dia 2 (sem pregão) eleva o custo de 10 para 12; o degrau
    aparece no primeiro fechamento seguinte, o do dia 3."""
    grafico = _grafico_de_fechamentos(
        [_fechamento(1, "10"), _fechamento(3, "14"), _fechamento(4, "12")],
        [Degrau(date(2026, 1, 1), Decimal("10")), Degrau(date(2026, 1, 2), Decimal("12"))],
        [],
    )

    assert grafico is not None
    # x: 70, 343, 616; y: 10 -> 192, 12 -> 102, 14 -> 12.
    assert grafico["pontos_custo"] == "70.0,192.0 343.0,192.0 343.0,102.0 616.0,102.0"


def test_escala_inclui_custo_medio_fora_da_faixa_dos_fechamentos() -> None:
    grafico = _grafico_de_fechamentos(
        [_fechamento(1, "10"), _fechamento(2, "15")],
        [Degrau(date(2026, 1, 1), Decimal("20"))],
        [],
    )

    assert grafico is not None
    assert grafico["pontos_custo"] == "70.0,12.0 616.0,12.0"
    # A legenda continua falando só dos fechamentos.
    assert (grafico["minimo"], grafico["maximo"]) == ("10", "15")


def test_cada_aporte_e_uma_linha_no_proprio_preco_desde_o_seu_fechamento() -> None:
    """Aporte anterior ao primeiro fechamento começa na borda; aporte sem
    pregão no dia começa no fechamento seguinte; aporte depois do último
    fechamento ainda não tem onde começar e fica fora, inclusive da escala."""
    grafico = _grafico_de_fechamentos(
        [_fechamento(1, "10"), _fechamento(3, "14"), _fechamento(4, "12")],
        [],
        [
            Aporte(date(2025, 12, 31), Decimal("11"), "Abertura em 31/12/2025"),
            Aporte(date(2026, 1, 2), Decimal("13"), "Aumento em 02/01/2026"),
            Aporte(date(2026, 1, 5), Decimal("9"), "Aumento em 05/01/2026"),
        ],
    )

    assert grafico is not None
    assert [(a["pontos"], a["rotulo"]) for a in grafico["aportes"]] == [
        ("70.0,147.0 616.0,147.0", "Abertura em 31/12/2025"),
        ("343.0,57.0 616.0,57.0", "Aumento em 02/01/2026"),
    ]
    assert [marca["valor"] for marca in grafico["eixo_y"]] == [10, 11, 12, 13, 14]


def test_eixo_x_marca_primeira_e_ultima_data_com_no_maximo_seis_marcas() -> None:
    fechamentos = [_fechamento(dia, "10") for dia in range(1, 11)]

    grafico = _grafico_de_fechamentos(fechamentos, [], [])

    assert grafico is not None
    rotulos = [marca["rotulo"] for marca in grafico["eixo_x"]]
    assert len(rotulos) == 6
    assert (rotulos[0], rotulos[-1]) == ("01/01/26", "10/01/26")


def test_janela_do_grafico_conta_para_tras_a_partir_do_ultimo_fechamento() -> None:
    ultimo = date(2026, 9, 24)
    assert _inicio_da_janela("1m", ultimo) == date(2026, 8, 24)
    assert _inicio_da_janela("6m", ultimo) == date(2026, 3, 24)
    assert _inicio_da_janela("1y", ultimo) == date(2025, 9, 24)
    assert _inicio_da_janela("ytd", ultimo) == date(2026, 1, 1)
    assert _inicio_da_janela("all", ultimo) is None


def test_janela_que_recua_de_dia_31_cai_no_fim_do_mes_e_cruza_o_ano() -> None:
    assert _inicio_da_janela("3m", date(2026, 5, 31)) == date(2026, 2, 28)
    assert _inicio_da_janela("3m", date(2026, 1, 15)) == date(2025, 10, 15)


def test_nenhum_template_tem_script_ou_estilo_inline() -> None:
    """A CSP recusa `<script>` sem `src` e o atributo `style`: o navegador
    descarta em silêncio, e a tela quebra sem erro no servidor."""
    import re

    raiz = Path(__file__).resolve().parents[1] / "app" / "templates"
    templates = list(raiz.rglob("*.html"))
    sobras = []
    for caminho in templates:
        fonte = caminho.read_text(encoding="utf-8")
        # HTML não diferencia caixa: `<SCRIPT>` e `STYLE=` valem o mesmo.
        if re.search(r"<script\b(?![^>]*\bsrc=)[^>]*>", fonte, re.IGNORECASE) or re.search(
            r"\sstyle=", fonte, re.IGNORECASE
        ):
            sobras.append(caminho.relative_to(raiz).as_posix())

    # Sem este piso, um caminho errado não acharia template nenhum e a
    # varredura passaria vazia.
    assert len(templates) >= 10
    assert sobras == []
