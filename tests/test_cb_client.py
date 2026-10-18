"""Cliente somente leitura do CB (contrato patrimonio/v4), sem rede."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.patrimonio.cb_client import (
    SETTLEMENT_CATEGORY,
    CbConfig,
    CbUnavailableError,
    institution_identity,
    parse_config,
    pregao_da_descricao,
    read_settlements,
)

CONTA_GENIAL = "controle-bancario:account:aaa"
CONTA_XP = "controle-bancario:account:bbb"
CATEGORIA_BOLSA = "controle-bancario:category:111"
CATEGORIA_OUTRA = "controle-bancario:category:222"


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("Operações Bolsa D+2 Pr 27/01/2026 Nc. 543216", date(2026, 1, 27)),
        ("Operações em bolsa - Operações Bolsa D+2 Pr 28/08/2026 Nc. 11466", date(2026, 8, 28)),
        ("Operações Bolsa D+1 Pr 02/06/2026 Nc. ********", date(2026, 6, 2)),
        ("Operações Bolsa D+2 Pr 31/02/2026 Nc. 1", None),
        ("Dividendos Brraizacnpr6", None),
        ("", None),
        (None, None),
    ],
)
def test_pregao_sai_da_descricao_da_liquidacao(descricao, esperado):
    assert pregao_da_descricao(descricao) == esperado


def _conta(source_id, instituicao, nome="Conta 03"):
    return {
        "resource": "account",
        "source_id": source_id,
        "payload": {
            "source_id": source_id,
            "name": nome,
            "institution": institution_identity(instituicao),
            "institution_name": instituicao,
            "currency": "BRL",
        },
    }


def _categoria(source_id, nome):
    return {"resource": "category", "source_id": source_id, "payload": {"source_id": source_id, "name": nome}}


def _lancamento(source_id, conta, categoria, descricao, valor, *, tipo="receita", realizado=True, **extra):
    return {
        "resource": "cash_entry",
        "source_id": source_id,
        "payload": {
            "source_id": source_id,
            "account_id": conta,
            "category_id": categoria,
            "description": descricao,
            "entry_type": tipo,
            "status": "realizado" if realizado else "a_vencer",
            "currency": "BRL",
            "planned_amount": valor,
            "realized_amount": valor if realizado else None,
            **extra,
        },
    }


def _snapshot(*items):
    return lambda url, token: {"contrato": "patrimonio/v4", "items": list(items)}


def test_le_so_a_categoria_de_operacoes_em_bolsa_com_valor_assinado():
    itens = [
        _conta(CONTA_GENIAL, "Genial"),
        _categoria(CATEGORIA_BOLSA, "Operações em Bolsa"),
        _categoria(CATEGORIA_OUTRA, "Mercado"),
        _lancamento("e1", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bolsa D+2 Pr 27/01/2026 Nc. 1", "1000.50"),
        _lancamento("e2", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bolsa D+2 Pr 26/03/2026 Nc. 2", "250.00", tipo="despesa"),
        _lancamento("e3", CONTA_GENIAL, CATEGORIA_OUTRA, "Supermercado Pr 27/01/2026", "10.00", tipo="despesa"),
    ]

    resultado = read_settlements(CbConfig("http://cb.test/patrimonio/v4", "t"), fetcher=_snapshot(*itens))

    assert [(s.entry_id, s.pregao_date, s.amount) for s in resultado.items] == [
        ("e1", date(2026, 1, 27), Decimal("1000.50")),
        ("e2", date(2026, 3, 26), Decimal("-250.00")),
    ]
    assert resultado.items[0].institution == "genial"
    assert resultado.without_pregao == []


def test_categoria_e_comparada_sem_acento_nem_caixa():
    itens = [
        _conta(CONTA_GENIAL, "Genial"),
        _categoria(CATEGORIA_BOLSA, "OPERACOES EM BOLSA"),
        _lancamento("e1", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bolsa D+2 Pr 27/01/2026", "1.00"),
    ]
    assert SETTLEMENT_CATEGORY == "Operações em Bolsa"

    assert len(read_settlements(CbConfig("http://cb.test", "t"), fetcher=_snapshot(*itens)).items) == 1


def test_settlement_oficial_tem_precedencia_sobre_a_descricao():
    """Quando o CB-1 chegar, `settlement.pregao_date` vale mais que o texto."""
    itens = [
        _conta(CONTA_GENIAL, "Genial"),
        _categoria(CATEGORIA_BOLSA, "Operações em Bolsa"),
        _lancamento(
            "e1", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bolsa D+2 Pr 27/01/2026", "5.00",
            settlement={"pregao_date": "2026-01-26", "note": "543216"},
        ),
        _lancamento("e2", CONTA_GENIAL, CATEGORIA_BOLSA, "sem data nenhuma", "7.00",
                    settlement={"pregao_date": "2026-02-03", "note": None}),
    ]

    resultado = read_settlements(CbConfig("http://cb.test", "t"), fetcher=_snapshot(*itens))

    assert [s.pregao_date for s in resultado.items] == [date(2026, 1, 26), date(2026, 2, 3)]


def test_lancamento_sem_pregao_identificavel_fica_fora_mas_contado():
    itens = [
        _conta(CONTA_GENIAL, "Genial"),
        _categoria(CATEGORIA_BOLSA, "Operações em Bolsa"),
        _lancamento("e1", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações em bolsa", "9.00"),
    ]

    resultado = read_settlements(CbConfig("http://cb.test", "t"), fetcher=_snapshot(*itens))

    assert resultado.items == []
    assert [s.entry_id for s in resultado.without_pregao] == ["e1"]


def test_liquidacao_e_ajuste_de_bmf_ficam_fora_do_escopo_mas_contados():
    itens = [
        _conta(CONTA_GENIAL, "Genial"),
        _categoria(CATEGORIA_BOLSA, "Operações em Bolsa"),
        _lancamento("e1", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bm&F Pr 11/09/2026 Nc. 145720", "0.50", tipo="despesa"),
        _lancamento("e2", CONTA_GENIAL, CATEGORIA_BOLSA, "Ajuste Day Trade Bm&F 11/09/2026", "2.00"),
        _lancamento("e3", CONTA_GENIAL, CATEGORIA_BOLSA, "Operações Bolsa D+2 Pr 28/08/2026 Nc. 1", "9.00"),
    ]

    resultado = read_settlements(CbConfig("http://cb.test", "t"), fetcher=_snapshot(*itens))

    assert [s.entry_id for s in resultado.items] == ["e3"]
    assert [s.entry_id for s in resultado.futures] == ["e1", "e2"]
    assert resultado.without_pregao == []


def test_pede_o_snapshot_com_o_token_do_consumidor():
    chamadas = []

    def fetcher(url, token):
        chamadas.append((url, token))
        return {"items": []}

    read_settlements(CbConfig("http://cb-api:8000/patrimonio/v4", "segredo-do-consumidor"), fetcher=fetcher)

    assert chamadas == [("http://cb-api:8000/patrimonio/v4/snapshot", "segredo-do-consumidor")]


@pytest.mark.parametrize("resposta", [[], {"sem": "items"}, {"items": "x"}])
def test_resposta_fora_do_contrato_e_recusada(resposta):
    with pytest.raises(CbUnavailableError, match="contrato"):
        read_settlements(CbConfig("http://cb.test", "t"), fetcher=lambda url, token: resposta)


def test_sem_url_ou_sem_token_nao_ha_configuracao():
    assert parse_config("", "token") is None
    assert parse_config("http://cb.test", "") is None
    assert parse_config(None, None) is None


def test_url_que_nao_e_http_e_recusada():
    with pytest.raises(CbUnavailableError, match="http"):
        parse_config("file:///etc/passwd", "token")


def test_o_token_nao_aparece_no_repr_da_configuracao():
    assert "segredo-do-consumidor" not in repr(CbConfig("http://cb.test", "segredo-do-consumidor"))
