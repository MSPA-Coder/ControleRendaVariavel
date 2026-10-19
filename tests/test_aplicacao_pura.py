"""Agrupamento das execuções em negócios e reconhecimento do exercício (sem banco)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.executions.aplicacao import group_trades, is_exercise
from app.models import Execution

D = Decimal


def _e(ident, dia, ativo, qtd, preco, *, lado=None, doc="B3_GENIAL_X", linha=1, taxas="0.1", grupo="acao"):
    quantidade = D(qtd)
    return Execution(
        id=ident, trade_date=date(2026, 11, dia), broker_id=1, asset=ativo, asset_group=grupo,
        side=lado or ("V" if quantidade < 0 else "C"), quantity=quantidade, price_without_fees=D(preco),
        fees_total=D(taxas), currency="BRL", document=doc, line_order=linha,
    )


def test_mesmo_pregao_ativo_e_lado_viram_um_negocio_com_preco_ponderado():
    negocios = group_trades(
        [
            _e(1, 3, "abcd3", "100", "10", linha=1),
            _e(2, 3, "ABCD3", "300", "12", linha=2),
            _e(3, 3, "ABCD3", "-50", "13", linha=3),
            _e(4, 4, "ABCD3", "10", "11", linha=1),
        ]
    )

    assert [(n.trade_date.day, n.side, n.quantity, n.price, n.execution_ids) for n in negocios] == [
        (3, "C", D("400"), D("11.5"), (1, 2)),
        (3, "V", D("50"), D("13"), (3,)),
        (4, "C", D("10"), D("11"), (4,)),
    ]
    assert negocios[0].fees == D("0.2") and negocios[0].asset == "ABCD3"


def test_ordem_do_relatorio_dentro_do_pregao():
    negocios = group_trades([_e(1, 3, "XYZ3", "-5", "9", linha=2), _e(2, 3, "ABC3", "5", "9", linha=1)])

    assert [n.asset for n in negocios] == ["ABC3", "XYZ3"]


def test_exercicio_e_a_serie_com_e_no_fim_so_em_opcao():
    assert is_exercise("RAIZO600E", "opcao")
    assert not is_exercise("RAIZO600", "opcao")
    assert not is_exercise("CPLE6", "acao")
