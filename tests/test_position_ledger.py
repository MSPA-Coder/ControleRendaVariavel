"""Preservação do extrato ao encerrar uma posição.

Encerrar apaga a posição e seu extrato em cascata. O arquivo preserva os
lançamentos necessários para que a performance inclua posições encerradas e
não sofra viés de sobrevivência.

Estes testes exercitam ``app.positions.ledger`` sem banco: a função recebe os
lançamentos já lidos e devolve as linhas a gravar. A sessão é substituída por
um coletor, porque o que importa aqui é QUAIS linhas nascem, não o SQL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import Side
from app.positions import ledger as position_ledger


class _SessionSpy:
    """Coletor no lugar de ``db.session``: guarda o que seria gravado."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)


@pytest.fixture
def sessao(monkeypatch):
    spy = _SessionSpy()
    monkeypatch.setattr(position_ledger.db, "session", spy)
    return spy


def _arquivar(sessao, *, side=Side.BUY, entries, closed_on=date(2026, 6, 30)):
    position_ledger.archive_closed_position(
        instrument="stock",
        position_id=7,
        ticker_id=1,
        portfolio_id=2,
        broker_id=3,
        side=side,
        entries=entries,
        closed_on=closed_on,
    )
    return [
        (linha.occurred_on, linha.resulting_signed_quantity) for linha in sessao.added
    ]


def test_arquiva_cada_lancamento_e_zera_na_data_do_encerramento(sessao):
    # A linha final zerada e o que faz a serie parar de contar o ativo. Sem
    # ela, a ultima quantidade conhecida valeria para sempre e a posicao
    # encerrada seguiria "aberta" no relatorio para todo o futuro.
    linhas = _arquivar(
        sessao,
        entries=[
            (date(2026, 1, 10), Decimal("100")),
            (date(2026, 3, 10), Decimal("150")),
        ],
    )

    assert linhas == [
        (date(2026, 1, 10), Decimal("100")),
        (date(2026, 3, 10), Decimal("150")),
        (date(2026, 6, 30), Decimal("0")),
    ]


def test_posicao_vendida_arquiva_quantidade_negativa(sessao):
    # `resulting_quantity` e sempre positivo no schema (CHECK); o sinal do
    # lado e aplicado uma vez so, aqui, para o leitor nao ter de saber disso.
    linhas = _arquivar(
        sessao, side=Side.SELL, entries=[(date(2026, 1, 10), Decimal("100"))]
    )

    assert linhas == [
        (date(2026, 1, 10), Decimal("-100")),
        (date(2026, 6, 30), Decimal("0")),
    ]


def test_posicao_sem_extrato_ainda_registra_o_encerramento(sessao):
    # Posicao antiga, anterior ao extrato: mesmo sem lancamento nenhum, a
    # linha zerada precisa existir para o leitor saber que ela acabou.
    assert _arquivar(sessao, entries=[]) == [(date(2026, 6, 30), Decimal("0"))]


def test_arquivo_reproduz_a_chave_de_posicao_com_o_instrumento(sessao):
    # `Position` e `OptionPosition` tem sequencias de id independentes: sem o
    # rotulo do instrumento, uma acao e uma opcao com o mesmo id virariam a
    # mesma posicao na linha do tempo.
    position_ledger.archive_closed_position(
        instrument="option",
        position_id=7,
        ticker_id=1,
        portfolio_id=2,
        broker_id=3,
        side=Side.BUY,
        entries=[(date(2026, 1, 10), Decimal("5"))],
        closed_on=date(2026, 6, 30),
    )

    assert {linha.instrument for linha in sessao.added} == {"option"}
    assert {linha.source_position_id for linha in sessao.added} == {7}
