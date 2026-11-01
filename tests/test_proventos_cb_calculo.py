"""O líquido dos proventos do CB (regra pura, sem banco nem rede).

Fixtures sintéticas no formato do contrato `patrimonio/v4`: valores inventados,
mas com a mesma estrutura do que o CB publica (IRRF do mesmo ativo e dia,
corretagem do dia sem ativo, taxas e estornos). A regra de produto, decidida em
18/10/2026: o provento é o LÍQUIDO do caixa; o bruto e as retenções ficam ao lado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.dividends.cb_income import (
    REASON_NO_INSTRUMENT,
    REASON_NOT_POSITIVE,
    REASON_ORPHAN_WITHHOLDING,
    REASON_UNKNOWN_TICKER,
    build_proventos,
    split_by_weight,
)
from app.patrimonio.cb_client import CbConfig, read_income_entries
from tests.proventos_cb_sintetico import CORTE, INSTITUICOES, TICKERS, XP, lancamento

D = Decimal


def calcular(entradas, **kwargs):
    kwargs.setdefault("cutoff", CORTE)
    kwargs.setdefault("broker_by_institution", INSTITUICOES)
    kwargs.setdefault("known_tickers", TICKERS)
    return build_proventos(entradas, **kwargs)


# --- rateio ---------------------------------------------------------------------


def test_o_rateio_soma_exatamente_o_total_ao_centavo():
    partes = split_by_weight(D("10.00"), [D("1"), D("1"), D("1")])

    assert partes == [D("3.34"), D("3.33"), D("3.33")]
    assert sum(partes) == D("10.00")


def test_o_rateio_e_proporcional_ao_valor_do_credito():
    # Corretagem de 30% de 100 sobre créditos de 60 e 40.
    assert split_by_weight(D("30.00"), [D("60"), D("40")]) == [D("18.00"), D("12.00")]


# --- o líquido do aluguel -------------------------------------------------------


def test_aluguel_desconta_irrf_do_mesmo_ativo_e_a_corretagem_do_dia():
    dia = date(2026, 1, 29)
    resultado = calcular(
        [
            lancamento(1, "credito", "352.56", dia, ativo="RAIZ4"),
            lancamento(2, "irrf_aluguel", "79.32", dia, ativo="RAIZ4"),
            lancamento(3, "corretagem_aluguel", "105.76", dia),
        ]
    )

    (provento,) = resultado.proventos
    assert (provento.gross, provento.withheld, provento.net) == (D("352.56"), D("185.08"), D("167.48"))
    assert (provento.kind, provento.ticker, provento.broker) == ("aluguel", "RAIZ4", "Genial")
    assert provento.payment_date == dia
    assert resultado.review == []


def test_irrf_de_outro_ativo_ou_de_outro_dia_nao_entra_no_credito():
    resultado = calcular(
        [
            lancamento(1, "credito", "100.00", date(2026, 3, 2), ativo="RAIZ4"),
            lancamento(2, "irrf_aluguel", "22.50", date(2026, 3, 2), ativo="SMTO3"),
            lancamento(3, "irrf_aluguel", "22.50", date(2026, 3, 3), ativo="RAIZ4"),
        ]
    )

    (provento,) = resultado.proventos
    assert provento.withheld == D("0.00")
    assert [item.reason for item in resultado.review] == [REASON_ORPHAN_WITHHOLDING] * 2


def test_corretagem_do_dia_e_rateada_entre_os_alugueis_na_proporcao_do_valor():
    dia = date(2026, 2, 5)
    resultado = calcular(
        [
            lancamento(1, "credito", "60.00", dia, ativo="RAIZ4"),
            lancamento(2, "credito", "40.00", dia, ativo="SMTO3"),
            lancamento(3, "corretagem_aluguel", "30.00", dia),
        ]
    )

    por_ativo = {item.ticker: item for item in resultado.proventos}
    assert por_ativo["RAIZ4"].withheld == D("18.00")
    assert por_ativo["SMTO3"].withheld == D("12.00")
    assert resultado.withheld_total == D("30.00")


def test_corretagem_de_um_dia_nao_vai_para_aluguel_de_outro_dia_nem_de_outra_conta():
    resultado = calcular(
        [
            lancamento(1, "credito", "50.00", date(2026, 2, 5), ativo="RAIZ4"),
            lancamento(2, "corretagem_aluguel", "15.00", date(2026, 2, 6)),
            lancamento(3, "corretagem_aluguel", "15.00", date(2026, 2, 5), conta=XP, instituicao="scp-xp-investimentos"),
        ]
    )

    assert resultado.proventos[0].withheld == D("0.00")
    assert len(resultado.review) == 2


def test_taxas_pequenas_e_estornos_do_dia_entram_no_mesmo_valor_da_conta():
    dia = date(2026, 7, 2)
    resultado = calcular(
        [
            lancamento(1, "credito", "100.00", dia, ativo="SMTO3"),
            lancamento(2, "corretagem_aluguel", "30.00", dia),
            lancamento(3, "taxa_aluguel", "0.06", dia, ativo="WEGE3"),
            lancamento(4, "estorno", "13.30", dia),
            lancamento(5, "corretagem_aluguel", "13.30", dia),
        ]
    )

    assert resultado.proventos[0].withheld == D("30.06")
    assert resultado.proventos[0].net == D("69.94")


def test_estorno_com_relancamento_igual_em_dia_sem_aluguel_nao_gera_pendencia():
    """30/06: o CB estorna e relança a mesma corretagem, sem crédito de aluguel no dia."""
    dia = date(2026, 6, 30)
    resultado = calcular(
        [
            lancamento(1, "estorno", "26.59", dia),
            lancamento(2, "corretagem_aluguel", "26.59", dia),
        ]
    )

    assert resultado.proventos == [] and resultado.review == []


def test_retencao_sem_credito_de_aluguel_no_dia_vai_para_a_fila():
    resultado = calcular([lancamento(1, "corretagem_aluguel", "9.00", date(2026, 4, 1))])

    assert [item.reason for item in resultado.review] == [REASON_ORPHAN_WITHHOLDING]


def test_liquido_nao_positivo_vai_para_a_fila_em_vez_de_virar_provento():
    dia = date(2026, 5, 4)
    resultado = calcular(
        [
            lancamento(1, "credito", "0.10", dia, ativo="RAIZ4"),
            lancamento(2, "taxa_aluguel", "0.10", dia),
        ]
    )

    assert resultado.proventos == []
    assert [item.reason for item in resultado.review] == [REASON_NOT_POSITIVE]


# --- dividendo e JCP --------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["dividendo", "jcp"])
def test_dividendo_e_jcp_nao_tem_retencao_o_liquido_e_o_credito(tipo):
    dia = date(2026, 3, 10)
    resultado = calcular(
        [
            lancamento(1, "credito", "786.96", dia, ativo="EALT4", kind=tipo, conta=XP, instituicao="scp-xp-investimentos"),
            # Uma corretagem de aluguel do mesmo dia e conta não toca dividendo.
            lancamento(2, "corretagem_aluguel", "5.00", dia, conta=XP, instituicao="scp-xp-investimentos"),
        ]
    )

    (provento,) = resultado.proventos
    assert (provento.gross, provento.withheld, provento.net) == (D("786.96"), D("0"), D("786.96"))
    assert (provento.kind, provento.broker) == (tipo, "XP")


# --- fila de revisão ------------------------------------------------------------


def test_credito_sem_ativo_ou_com_ticker_desconhecido_vai_para_a_fila_sem_gravar():
    dia = date(2026, 8, 12)
    resultado = calcular(
        [
            lancamento(1, "credito", "247.69", dia, ativo="XPTO3", kind="dividendo"),
            lancamento(2, "credito", "10.00", dia, ativo=None, kind="jcp"),
        ]
    )

    assert resultado.proventos == []
    assert {(item.entry_id, item.reason) for item in resultado.review} == {
        ("1", REASON_UNKNOWN_TICKER),
        ("2", REASON_NO_INSTRUMENT),
    }
    assert all(item.message for item in resultado.review)


def test_credito_de_ativo_desconhecido_ainda_recebe_a_sua_parte_da_corretagem():
    """A retenção do dia foi cobrada sobre todos os aluguéis, mesmo o que está na fila."""
    dia = date(2026, 1, 19)
    resultado = calcular(
        [
            lancamento(1, "credito", "60.00", dia, ativo="RAIZ4"),
            lancamento(2, "credito", "40.00", dia, ativo="WEGE3"),
            lancamento(3, "corretagem_aluguel", "30.00", dia),
        ],
        known_tickers={"RAIZ4"},
    )

    assert resultado.proventos[0].withheld == D("18.00")
    assert [item.reason for item in resultado.review] == [REASON_UNKNOWN_TICKER]


# --- corte, moeda e instituição ------------------------------------------------


def test_so_entra_o_realizado_a_partir_do_corte_em_reais_nas_instituicoes_mapeadas():
    resultado = calcular(
        [
            lancamento(1, "credito", "10.00", date(2025, 12, 31), ativo="BBAS3", kind="dividendo"),
            lancamento(2, "credito", "20.00", date(2026, 1, 1), ativo="BBAS3", kind="dividendo"),
            lancamento(3, "credito", "30.00", date(2026, 2, 1), ativo="BBAS3", kind="dividendo", moeda="USD"),
            lancamento(4, "credito", "40.00", date(2026, 2, 1), ativo="BBAS3", kind="dividendo", instituicao="avenue"),
        ]
    )

    assert [item.origin_id for item in resultado.proventos] == ["2"]
    assert (resultado.before_cutoff, resultado.other_currency, resultado.other_institution) == (1, 1, 1)


def test_corte_configuravel():
    entradas = [lancamento(1, "credito", "20.00", date(2026, 6, 1), ativo="BBAS3", kind="dividendo")]

    assert calcular(entradas, cutoff=date(2026, 7, 1)).proventos == []
    assert len(calcular(entradas, cutoff=date(2026, 6, 1)).proventos) == 1


def test_o_mesmo_conjunto_de_lancamentos_sempre_produz_o_mesmo_resultado():
    """Idempotência da regra: reprocessar não muda valor nem identidade."""
    dia = date(2026, 1, 29)
    entradas = [
        lancamento(1, "credito", "352.56", dia, ativo="RAIZ4"),
        lancamento(2, "irrf_aluguel", "79.32", dia, ativo="RAIZ4"),
        lancamento(3, "corretagem_aluguel", "105.76", dia),
    ]

    assert calcular(entradas).proventos == calcular(list(reversed(entradas))).proventos


# --- leitura do contrato v4 -----------------------------------------------------


def _item(recurso, source_id, payload):
    return {"resource": recurso, "source_id": source_id, "payload": {"source_id": source_id, **payload}}


def test_cliente_le_so_os_lancamentos_de_renda_realizados_do_contrato_v4():
    conta = _item("account", "a1", {"name": "Conta 03", "institution": "genial", "currency": "BRL"})
    base = {"account_id": "a1", "category_id": "c1", "currency": "BRL", "entry_type": "receita"}
    itens = [
        conta,
        _item(
            "cash_entry", "e1",
            {**base, "description": "x", "status": "realizado", "realized_date": "2026-01-08",
             "planned_amount": "0.79", "realized_amount": "0.79",
             "instrument": "smto3", "income_kind": "aluguel", "income_role": "credito"},
        ),
        _item(
            "cash_entry", "e2",
            {**base, "description": "y", "status": "a_vencer", "realized_date": None,
             "planned_amount": "5.00", "realized_amount": None, "income_kind": "jcp", "income_role": "credito"},
        ),
        _item(
            "cash_entry", "e3",
            {**base, "description": "supermercado", "status": "realizado", "realized_date": "2026-01-09",
             "planned_amount": "9.00", "realized_amount": "9.00"},
        ),
        _item(
            "cash_entry", "e4",
            {**base, "description": "irrf", "entry_type": "despesa", "status": "realizado",
             "realized_date": "2026-01-08", "planned_amount": "0.17", "realized_amount": "0.17",
             "instrument": "SMTO3", "income_kind": "aluguel", "income_role": "irrf_aluguel"},
        ),
    ]

    renda = read_income_entries(
        CbConfig("http://cb.test/patrimonio/v4", "t"),
        fetcher=lambda url, token: {"contrato": "patrimonio/v4", "items": itens},
    )

    assert [(e.entry_id, e.role, e.flow, e.instrument, e.amount, e.institution) for e in renda.entries] == [
        ("e1", "credito", "receita", "SMTO3", D("0.79"), "genial"),
        ("e4", "irrf_aluguel", "despesa", "SMTO3", D("0.17"), "genial"),
    ]
    assert renda.not_realized == 1


# --- exterior: Avenue em dólar e cupom de título (01/11/2026) ------------------

AVENUE = {**INSTITUICOES, "avenue": "Avenue"}
EM_DOLAR = {"Avenue": "USD"}


def no_exterior(entradas, **kwargs):
    kwargs.setdefault("broker_by_institution", AVENUE)
    kwargs.setdefault("known_tickers", TICKERS | {"CGC", "ORCL-3.95-25-03-51"})
    kwargs.setdefault("currency_by_broker", EM_DOLAR)
    return calcular(entradas, **kwargs)


def test_cupom_de_titulo_vira_provento_em_dolar_com_o_simbolo_do_cadastro():
    dia = date(2026, 9, 28)
    resultado = no_exterior(
        [lancamento(1, "credito", "39.50", dia, ativo="ORCL 3.95 25/03/51", kind="cupom",
                    conta="conta-a", instituicao="avenue", moeda="USD")]
    )

    (provento,) = resultado.proventos
    assert (provento.ticker, provento.kind, provento.currency, provento.net) == (
        "ORCL-3.95-25-03-51", "cupom", "USD", D("39.50"),
    )
    assert provento.broker == "Avenue" and resultado.review == []


def test_aluguel_da_avenue_desconta_o_ir_retido_do_mesmo_ativo_e_dia():
    dia = date(2026, 9, 17)
    resultado = no_exterior(
        [
            lancamento(1, "credito", "9.04", dia, ativo="CGC", conta="conta-a", instituicao="avenue", moeda="USD"),
            lancamento(2, "irrf_aluguel", "2.71", dia, ativo="CGC", conta="conta-a", instituicao="avenue", moeda="USD"),
        ]
    )

    (provento,) = resultado.proventos
    assert (provento.ticker, provento.gross, provento.withheld, provento.net) == ("CGC", D("9.04"), D("2.71"), D("6.33"))


def test_cada_corretora_so_entra_na_sua_moeda():
    """A Avenue tem conta em reais e em dólar; o CB assume os proventos dela em dólar.
    E a Genial continua só em reais."""
    dia = date(2026, 5, 4)
    resultado = no_exterior(
        [
            lancamento(1, "credito", "51.25", dia, ativo="CGC", kind="dividendo",
                       conta="conta-ab", instituicao="avenue", moeda="BRL"),
            lancamento(2, "credito", "10.00", dia, ativo="RAIZ4", kind="dividendo", moeda="USD"),
        ]
    )

    assert resultado.proventos == [] and resultado.other_currency == 2


def test_sem_moeda_por_corretora_vale_reais_como_antes():
    dia = date(2026, 9, 28)
    resultado = calcular(
        [lancamento(1, "credito", "39.50", dia, ativo="CGC", kind="cupom",
                    conta="conta-a", instituicao="avenue", moeda="USD")],
        broker_by_institution=AVENUE,
    )

    assert resultado.proventos == [] and resultado.other_currency == 1


@pytest.mark.parametrize(
    ("ativo", "simbolo"),
    [("EALT4", "EALT4"), ("BRK.B", "BRK.B"), ("ORCL 3.95 25/03/51", "ORCL-3.95-25-03-51"),
     ("PETBRA 6.85 05/06/15", "PETBRA-6.85-05-06-15")],
)
def test_o_ativo_do_cb_vira_simbolo_de_ticker_valido(ativo, simbolo):
    from app.dividends.cb_income import symbol_for_instrument
    from app.quotes.reference_data import _SYMBOL_PATTERN

    assert symbol_for_instrument(ativo) == simbolo
    assert _SYMBOL_PATTERN.fullmatch(simbolo)
