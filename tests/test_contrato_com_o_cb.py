"""O que o CRV e o Controle Bancário (CB) dizem um ao outro. Sem banco nem rede.

Os outros testes do cliente do CB leem um snapshot escrito à mão neste
repositório: se o CB renomear um campo, eles continuam verdes e a produção
quebra. Aqui o cliente lê `tests/contrato/exemplo-cb-snapshot-vinculo.json`, que
é a resposta **real** do CB (o snapshot v4 com o token do vínculo), gerada e
conferida pela suíte dele (`tests/test_contrato_com_o_crv.py`, no CB). O arquivo
é cópia da canônica em `manutencao/docs/contratos/exemplos/`: quando o CB muda o
que publica, a suíte dele reprova, a canônica é atualizada e a mudança chega
aqui pelo `sincronizar.py`.

No outro sentido, `crv-fechamento-1.schema.json` é o que o Roteiro de fechamento
do CB aceita: cada item que este sistema monta precisa cumpri-lo, ou o CB o
descarta calado.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.dividends.cb_income import build_proventos
from app.patrimonio.cb_client import CbConfig, read_income_entries, read_settlements
from app.routes import fechamento

D = Decimal
CONTRATO = Path(__file__).parent / "contrato"
EXEMPLO = json.loads((CONTRATO / "exemplo-cb-snapshot-vinculo.json").read_text(encoding="utf-8"))
SCHEMA_V4 = json.loads((CONTRATO / "patrimonio-v4.schema.json").read_text(encoding="utf-8"))
SCHEMA_DO_FECHAMENTO = json.loads((CONTRATO / "crv-fechamento-1.schema.json").read_text(encoding="utf-8"))
CONFIG = CbConfig("http://cb.test/patrimonio/v4", "t")


def _cb(url, token):
    return EXEMPLO


def test_o_exemplo_e_o_recorte_do_vinculo_e_cumpre_o_contrato_v4():
    validador = Draft202012Validator({"$ref": "#/$defs/cb_item", "$defs": SCHEMA_V4["$defs"]})
    erros = [erro.message for item in EXEMPLO["items"] for erro in validador.iter_errors(item)]

    assert not erros, erros[:5]
    assert EXEMPLO["scope"]["institution_type"] == "Corretora"
    assert {item["resource"] for item in EXEMPLO["items"]} == {"account", "category", "cash_entry"}


def test_liquidacoes_do_exemplo_real_do_cb():
    resultado = read_settlements(CONFIG, fetcher=_cb)

    assert [(s.institution, s.currency, s.pregao_date, s.amount, s.instrument, s.quantity) for s in resultado.items] == [
        ("genial", "BRL", date(2026, 1, 27), D("1252.10"), None, None),
        ("genial", "BRL", date(2026, 3, 20), D("-300.00"), None, None),
        # Digitada à mão no CB: sem a marca do extrato, o pregão sai da descrição.
        ("genial", "BRL", date(2026, 4, 1), D("40.00"), None, None),
        ("avenue", "USD", date(2026, 6, 16), D("-4750.00"), "CGC", D("5000")),
    ]
    assert [s.description for s in resultado.futures] == ["Operações Bm&F Pr 06/01/2026 Nc. 126168"]
    assert resultado.without_pregao == []
    assert resultado.category_found is True
    assert resultado.institutions == {"genial", "avenue"}


def test_cobertura_e_carteira_do_exemplo_real_do_cb():
    resultado = read_settlements(CONFIG, fetcher=_cb)

    assert resultado.coverage == {("genial", "BRL"): date(2026, 9, 30), ("avenue", "USD"): date(2026, 9, 30)}
    assert resultado.holdings == {
        ("avenue", "USD"): {date(2026, 9, 30): {"BRK.B": D("1.56365"), "CGC": D("5000")}}
    }


def test_renda_do_exemplo_real_do_cb_vira_provento_liquido():
    renda = read_income_entries(CONFIG, fetcher=_cb)

    assert renda.not_realized == 0
    assert [(e.institution, e.instrument, e.kind, e.role, e.flow, e.amount) for e in renda.entries] == [
        ("genial", "WEGE3", "dividendo", "credito", "receita", D("8.00")),
        ("genial", "EALT4", "jcp", "credito", "receita", D("15.00")),
        ("genial", "RAIZ4", "aluguel", "credito", "receita", D("10.00")),
        ("genial", "RAIZ4", "aluguel", "irrf_aluguel", "despesa", D("1.50")),
        ("avenue", "ORCL 3.95 25/03/51", "cupom", "credito", "receita", D("39.50")),
    ]

    resultado = build_proventos(
        renda.entries,
        cutoff=date(2026, 1, 1),
        broker_by_institution={"genial": "Genial", "avenue": "Avenue"},
        known_tickers={"WEGE3", "EALT4", "RAIZ4", "ORCL-3.95-25-03-51"},
        currency_by_broker={"Avenue": "USD"},
    )

    assert resultado.review == []
    assert sorted((p.ticker, p.kind.lower(), p.currency, p.gross, p.withheld, p.net) for p in resultado.proventos) == [
        ("EALT4", "jcp", "BRL", D("15.00"), D("0"), D("15.00")),
        ("ORCL-3.95-25-03-51", "cupom", "USD", D("39.50"), D("0"), D("39.50")),
        # O aluguel entra líquido: o crédito menos o IRRF do mesmo ativo, conta e dia.
        ("RAIZ4", "aluguel", "BRL", D("10.00"), D("1.50"), D("8.50")),
        ("WEGE3", "dividendo", "BRL", D("8.00"), D("0"), D("8.00")),
    ]


# --- crv-fechamento/1: o que o Roteiro de fechamento do CB aceita -------------------

def _item_cumpre(item: dict) -> None:
    validador = Draft202012Validator({"$ref": "#/$defs/item", "$defs": SCHEMA_DO_FECHAMENTO["$defs"]})
    erros = [erro.message for erro in validador.iter_errors(item)]
    assert not erros, (item.get("chave"), erros)


def _resumo(**mudancas):
    resumo = {
        "status": "ok", "mensagem": "", "avisos": [], "aguardando_depois_do_corte": 0,
        "contagem": {"confere": 12, "diferenca": 0, "so_cb": 0, "so_crv": 0, "aguardando_extrato": 0, "sem_cb": 0},
        "carteira": {"conferidas": 18, "ultima": date(2026, 9, 30), "diferencas": []},
        "proventos": {"status": "ok", "iguais": 91, "faltam": 0, "fila": 0, "ausentes": 0, "problemas": []},
    }
    resumo.update(mudancas)
    return resumo


CONTAGEM = _resumo()["contagem"]


@pytest.mark.parametrize(
    "resumo",
    [
        _resumo(),
        _resumo(status="indisponivel", mensagem="Não foi possível alcançar o CB."),
        _resumo(avisos=["Nenhuma conta do CB corresponde à corretora XP."]),
        _resumo(contagem={**CONTAGEM, "diferenca": 1, "so_crv": 2}),
        _resumo(contagem={**CONTAGEM, "aguardando_extrato": 3}),
        _resumo(contagem={**CONTAGEM, "aguardando_extrato": 1}, aguardando_depois_do_corte=1),
        _resumo(carteira={"conferidas": 0, "ultima": None, "diferencas": []}),
        _resumo(carteira={"conferidas": 2, "ultima": date(2026, 9, 30), "diferencas": ["CGC em 30/09: extrato 7500, CRV 9808"]}),
        _resumo(proventos={"status": "ok", "iguais": 89, "faltam": 2, "fila": 1, "ausentes": 1, "problemas": ["x"]}),
        _resumo(proventos={"status": "nao_configurado", "mensagem": "CB não configurado"}),
    ],
)
def test_itens_que_dependem_do_cb_cumprem_o_contrato_em_todo_estado(monkeypatch, resumo):
    monkeypatch.setattr(fechamento, "_resumo_dos_pregoes", lambda owner, dia: resumo)
    ate = date(2026, 9, 30)

    for montar in (fechamento._item_dos_pregoes, fechamento._item_da_carteira, fechamento._item_dos_proventos_do_cb):
        _item_cumpre(montar(1, ate))


def test_o_schema_do_fechamento_e_valido_e_tem_o_contrato_que_a_rota_emite():
    Draft202012Validator.check_schema(SCHEMA_DO_FECHAMENTO)

    assert SCHEMA_DO_FECHAMENTO["$defs"]["resposta"]["properties"]["contrato"]["const"] == fechamento.CONTRATO


def test_o_schema_reprova_estado_e_etapa_que_o_cb_nao_conhece():
    """Sem isto, um schema frouxo passaria um item que o CB descarta."""
    validador = Draft202012Validator({"$ref": "#/$defs/item", "$defs": SCHEMA_DO_FECHAMENTO["$defs"]})
    bom = {"chave": "pregoes", "etapa": "conferencias", "titulo": "Pregões", "estado": "em_dia", "texto": "12 conferem"}

    assert not list(validador.iter_errors(bom))
    for mudanca in ({"estado": "ok"}, {"etapa": "pregoes"}, {"texto": ""}, {"passos": "um passo"}):
        assert list(validador.iter_errors({**bom, **mudanca})), mudanca
    sem_titulo = {chave: valor for chave, valor in bom.items() if chave != "titulo"}
    assert list(validador.iter_errors(sem_titulo))
