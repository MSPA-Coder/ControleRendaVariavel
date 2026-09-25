"""Anualização de perdas e variação diária: onde o sistema diverge da planilha.

Decisão de 15/09/2026, a partir do laudo dos oito repositórios (item 2.4 do
plano de ação):

- a projeção do retorno no período (carteira de ações) e o retorno anualizado
  da aba Risco usavam ``sinal(r) * ((1 + |r|) ** (período / dias) - 1)``, a
  forma da planilha. Ela coincide com a capitalização composta para ganho e
  erra para perda: -30% em 100 dias virava -160,6% ao ano. Passaram à composta;
- a variação diária de ações e opções era ``s * (1 - fechamento / atual)``,
  medida contra o preço atual. Passou a ``s * (atual / fechamento - 1)``:
  fechamento 100 e atual 110 é +10%, e não +9,09%.

Nenhum desses cálculos tinha teste. Os valores esperados saem da definição de
capitalização composta, não da planilha -- que é justamente a fonte do erro.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.domain import calculate_position, signed_period_return
from app.models import OptionType
from app.options.metrics import calculate_option
from app.performance.risk import annualized_return_from_prices

HOJE = date(2026, 9, 15)

# 0,7 ** 3,65 - 1 e 1,3 ** 3,65 - 1: queda e alta de 30% em 100 dias, por ano.
QUEDA_DE_30_EM_100_DIAS_AO_ANO = Decimal("-0.7280")
ALTA_DE_30_EM_100_DIAS_AO_ANO = Decimal("1.6055")


def _perto(obtido, esperado: Decimal, tolerancia: str = "0.0005") -> bool:
    return abs(Decimal(str(obtido)) - esperado) <= Decimal(tolerancia)


def _posicao(*, lado="C", custo="10", atual="7", fechamento="7", dias=100, quantidade="100"):
    return calculate_position(
        side=lado,
        quantity=Decimal(quantidade),
        average_cost=Decimal(custo),
        raw_price=Decimal(atual),
        previous_close=Decimal(fechamento),
        target_multiplier=Decimal("1.5"),
        opened_on=HOJE - timedelta(days=dias),
        today=HOJE,
    )


# --------------------------------------------------------------------------
# Retorno no período (carteira de ações)
# --------------------------------------------------------------------------


def test_perda_e_projetada_pela_composta_e_nunca_passa_de_menos_cem():
    projetado = signed_period_return(Decimal("-0.30"), 100, 365)

    assert _perto(projetado, QUEDA_DE_30_EM_100_DIAS_AO_ANO)
    assert projetado > Decimal("-1")


def test_ganho_projetado_continua_igual_ao_da_planilha():
    """Para ganho, a forma da planilha e a composta coincidem."""
    assert _perto(signed_period_return(Decimal("0.30"), 100, 365), ALTA_DE_30_EM_100_DIAS_AO_ANO)


def test_perda_total_projeta_menos_cem_por_cento():
    assert signed_period_return(Decimal("-1"), 100, 365) == Decimal("-1")


def test_perda_maior_que_o_investido_nao_tem_projecao():
    """Possível numa venda: recomprar por mais do que o dobro do preço."""
    assert signed_period_return(Decimal("-1.5"), 100, 365) is None


def test_perda_em_janela_curta_nao_explode():
    projetado = signed_period_return(Decimal("-0.20"), 1, 365)

    assert Decimal("-1") <= projetado < Decimal("-0.99")


@pytest.mark.parametrize(("dias", "periodo"), [(0, 365), (-3, 365), (100, 0)])
def test_sem_dias_decorridos_ou_sem_periodo_nao_se_aplica(dias, periodo):
    assert signed_period_return(Decimal("0.10"), dias, periodo) is None


def test_compra_em_queda_mostra_a_perda_anualizada_composta():
    metricas = _posicao(custo="10", atual="7")

    assert metricas.return_pct == Decimal("-0.3")
    assert _perto(metricas.period_return, QUEDA_DE_30_EM_100_DIAS_AO_ANO)


def test_venda_com_perda_acima_do_investido_fica_sem_projecao():
    metricas = _posicao(lado="V", custo="10", atual="25", fechamento="25")

    assert metricas.return_pct == Decimal("-1.5")
    assert metricas.period_return is None


# --------------------------------------------------------------------------
# Retorno anualizado da aba Risco (CAGR por preços)
# --------------------------------------------------------------------------


def _serie(primeiro: str, ultimo: str, dias: int) -> list[tuple[date, Decimal]]:
    return [(HOJE - timedelta(days=dias), Decimal(primeiro)), (HOJE, Decimal(ultimo))]


def test_cagr_de_queda_nunca_passa_de_menos_cem_por_cento():
    assert _perto(annualized_return_from_prices(_serie("100", "70", 100)), QUEDA_DE_30_EM_100_DIAS_AO_ANO)


def test_cagr_de_alta_continua_igual():
    assert _perto(annualized_return_from_prices(_serie("100", "130", 100)), ALTA_DE_30_EM_100_DIAS_AO_ANO)


def test_cagr_de_queda_em_janela_curta_fica_perto_de_menos_cem():
    """Antes, -20% em 1 dia virava um número que nem cabia em Decimal."""
    valor = annualized_return_from_prices(_serie("100", "80", 1))

    assert -1.0 <= valor < -0.99


# --------------------------------------------------------------------------
# Variação diária, contra o fechamento
# --------------------------------------------------------------------------

VARIACOES = [
    ("C", "100", "110", "0.10"),
    ("C", "100", "90", "-0.10"),
    ("V", "100", "110", "-0.10"),
    ("V", "100", "90", "0.10"),
]


@pytest.mark.parametrize(("lado", "fechamento", "atual", "esperado"), VARIACOES)
def test_variacao_diaria_de_acao_e_medida_contra_o_fechamento(lado, fechamento, atual, esperado):
    metricas = _posicao(lado=lado, custo="100", atual=atual, fechamento=fechamento, quantidade="1")

    assert metricas.daily_variation == Decimal(esperado)


@pytest.mark.parametrize(("lado", "fechamento", "atual", "esperado"), VARIACOES)
def test_variacao_diaria_de_opcao_segue_a_mesma_convencao(lado, fechamento, atual, esperado):
    metricas = calculate_option(
        side=lado,
        option_type=OptionType.CALL,
        quantity=Decimal("100"),
        average_cost=Decimal("1"),
        current_price=Decimal(atual),
        previous_close=Decimal(fechamento),
        strike=Decimal("30"),
        underlying_price=Decimal("31"),
        opened_on=HOJE - timedelta(days=5),
        expiration_date=HOJE + timedelta(days=20),
        today=HOJE,
    )

    assert metricas.daily_variation == Decimal(esperado)


def test_variacao_diaria_sem_fechamento_nao_se_aplica():
    assert _posicao(atual="7", fechamento="0").daily_variation is None
