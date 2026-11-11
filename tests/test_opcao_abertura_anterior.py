"""Opção com data anterior à abertura exige confirmação, como a ação (e0e88ad).

Sem a confirmação, um lote de opção com ano errado reescrevia a data de
abertura e o custo médio da posição existente, e o extrato era reaplicado.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app import db
from app.models import OptionPosition, PositionMovementKind
from app.options.closure import create_or_merge_position
from tests.test_ciclo_opcoes_banco import aporte
from tests.test_ciclo_opcoes_banco import cenario as cenario
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco

ABERTURA = date(2026, 1, 2)  # a data de `aporte()`


def _anterior(cenario, dias=30, quantidade="50", custo="5"):
    lote = aporte(cenario, quantidade, custo)
    lote.opened_on = ABERTURA - timedelta(days=dias)
    return lote


def test_lote_anterior_a_abertura_sem_confirmacao_e_recusado_e_a_posicao_fica_igual(sessao, cenario):
    posicao, _ = create_or_merge_position(aporte(cenario, "100", "2"))
    sessao.flush()

    with pytest.raises(ValueError, match="exige confirmação"):
        create_or_merge_position(_anterior(cenario))

    sessao.refresh(posicao)
    assert (posicao.opened_on, posicao.quantity, posicao.average_cost) == (
        ABERTURA, Decimal("100"), Decimal("2")
    )
    assert [m.kind for m in posicao.movements] == [PositionMovementKind.OPEN]


def test_lote_anterior_confirmado_vira_a_abertura_e_o_extrato_e_reaplicado(sessao, cenario):
    posicao, _ = create_or_merge_position(aporte(cenario, "100", "2"))
    sessao.flush()

    reconstruida, fundiu = create_or_merge_position(_anterior(cenario), confirm_prior_opening=True)
    sessao.flush()

    assert fundiu is True
    assert reconstruida.id == posicao.id
    assert reconstruida.opened_on == ABERTURA - timedelta(days=30)
    assert reconstruida.quantity == Decimal("150")
    assert [(m.kind, m.resulting_quantity) for m in sorted(
        reconstruida.movements, key=lambda m: m.occurred_on
    )] == [
        (PositionMovementKind.OPEN, Decimal("50")),
        (PositionMovementKind.INCREASE, Decimal("150")),
    ]


def test_aporte_na_mesma_data_ou_depois_nao_pede_confirmacao(sessao, cenario):
    create_or_merge_position(aporte(cenario, "100", "2"))
    depois = aporte(cenario, "10", "3")
    depois.opened_on = ABERTURA + timedelta(days=1)

    posicao, fundiu = create_or_merge_position(depois)

    assert fundiu is True
    assert posicao.quantity == Decimal("110")


def test_tela_de_opcoes_pede_confirmacao_para_lote_anterior(review_case):
    app, data = review_case
    client, token = client_for(app, data["ids"][2])
    hoje = date.today()

    def formulario(dias, **extra):
        return {
            "csrf_token": token, "submit_token": uuid4().hex,
            "broker_id": data["broker"], "contract_id": data["contract"],
            "portfolio_id": data["portfolio_ids"][2], "quantity": "5",
            "average_cost": "4", "side": "V",
            "opened_on": (hoje - timedelta(days=dias)).isoformat(), **extra,
        }

    assert client.post("/options/positions", data=formulario(5)).status_code == 302

    aviso = client.post("/options/positions", data=formulario(30))

    assert aviso.status_code == 409
    assert "Data anterior à abertura registrada." in aviso.get_data(as_text=True)
    with app.app_context():
        posicao = db.session.query(OptionPosition).filter_by(
            owner_id=data["user_ids"][2], side="V"
        ).one()
        assert (posicao.opened_on, posicao.quantity) == (hoje - timedelta(days=5), Decimal("5"))

    confirmado = client.post(
        "/options/positions", data=formulario(30, confirm_prior_opening="1")
    )

    assert confirmado.status_code == 302
    with app.app_context():
        posicao = db.session.query(OptionPosition).filter_by(
            owner_id=data["user_ids"][2], side="V"
        ).one()
        assert (posicao.opened_on, posicao.quantity) == (hoje - timedelta(days=30), Decimal("10"))
        assert [m.kind for m in sorted(posicao.movements, key=lambda m: m.occurred_on)] == [
            PositionMovementKind.OPEN, PositionMovementKind.INCREASE,
        ]
