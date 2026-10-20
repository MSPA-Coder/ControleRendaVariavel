"""Conciliação mensal contra o PostgreSQL: instantâneo da posição, dono,
ativos de fora da B3 e reconhecimentos."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.executions.conciliacao import (
    ACKNOWLEDGED,
    FEES,
    MATCH,
    ONLY_CRV,
    ONLY_EXECUTIONS,
    ONLY_MYPROFIT,
    QUANTITY,
    build_conciliation,
    crv_holdings,
    myprofit_holdings,
    remove_note,
    save_position_snapshot,
    set_note,
)
from app.executions.myprofit import read_execution_report
from app.executions.posicao_myprofit import read_position_report
from app.executions.service import apply_import, plan_import
from app.models import (
    Broker,
    Market,
    MyprofitPosition,
    OptionContract,
    OptionExpiration,
    OptionType,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    ReconciliationNote,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from tests.myprofit_posicao_sintetico import linha_posicao, posicao_tsv
from tests.myprofit_sintetico import linha, relatorio_xlsx

pytestmark = pytest.mark.banco

D = Decimal
INICIO, FIM = date(2026, 1, 1), date(2026, 10, 31)


@pytest.fixture
def cenario(sessao, app_com_banco, monkeypatch):
    import json

    sufixo = uuid4().hex[:6].upper()
    dono = User(username=f"conc-{sufixo.lower()}", password_hash="hash")
    outro = User(username=f"conc-outro-{sufixo.lower()}", password_hash="hash")
    corretora = Broker(name=f"Genial {sufixo}", acronym=sufixo)
    sessao.add_all([dono, outro, corretora])
    sessao.flush()
    real = Portfolio(name=f"Real {sufixo}", owner_ref=dono, currency="BRL")
    do_outro = Portfolio(name=f"Real outro {sufixo}", owner_ref=outro, currency="BRL")
    sessao.add_all([real, do_outro])
    tickers = {
        nome: Ticker(
            symbol=f"{nome}{sufixo}", trading_name=nome, market=mercado, rtd_market_code=codigo,
            currency=moeda,
        )
        for nome, mercado, codigo, moeda in (
            ("ACAOA", Market.B3, "B", "BRL"),
            ("ACAOB", Market.B3, "B", "BRL"),
            ("EUA", Market.NYSE, "N", "USD"),
        )
    }
    sessao.add_all(tickers.values())
    sessao.flush()
    monkeypatch.setitem(
        app_com_banco.config, "MYPROFIT_INSTITUICOES", json.dumps({"GENIAL": corretora.name})
    )
    return {
        "dono": dono, "outro": outro, "corretora": corretora, "real": real,
        "do_outro": do_outro, "t": tickers, "sufixo": sufixo,
    }


def _posicao(c, ticker, carteira, quantidade, custo, *, dono=None):
    return Position(
        owner_id=(dono or c["dono"]).id, broker_id=c["corretora"].id, ticker_id=ticker.id,
        portfolio_id=carteira.id, quantity=D(quantidade), average_cost=D(custo), side=Side.BUY,
        opened_on=date(2026, 3, 2),
    )


def _fechada(c, ticker, carteira, quantidade, dia, *, dono=None):
    return Transaction(
        owner_id=(dono or c["dono"]).id, broker_id=c["corretora"].id, ticker_id=ticker.id,
        portfolio_id=carteira.id, quantity=D(quantidade), average_cost=D("10"),
        exit_price=D("12"), side=Side.BUY, opened_on=date(2025, 1, 2), closed_on=dia,
        status=TransactionStatus.CLOSED, result=D("2") * D(quantidade),
    )


def _snapshot(c, sessao, linhas, ate=FIM):
    report = read_position_report(posicao_tsv(linhas, ate=ate))
    save_position_snapshot(c["dono"].id, report)
    sessao.flush()
    return report


def _vendas(c, sessao, linhas):
    plano = plan_import(c["dono"].id, read_execution_report(relatorio_xlsx(linhas)))
    apply_import(c["dono"].id, plano, file_name="sintetico.xlsx")
    sessao.flush()


def _estados(achados):
    return {a.asset: a.state for a in achados}


def test_snapshot_da_mesma_data_substitui_e_a_mais_recente_vale(sessao, cenario):
    c = cenario
    _snapshot(c, sessao, [linha_posicao("AAA3", 10, "1")], ate=date(2026, 9, 30))
    _snapshot(c, sessao, [linha_posicao("AAA3", 20, "1"), linha_posicao("BBB3", 5, "2")], ate=FIM)
    removidas, gravadas = save_position_snapshot(
        c["dono"].id, read_position_report(posicao_tsv([linha_posicao("AAA3", 30, "1")], ate=FIM))
    )

    assert (removidas, gravadas) == (2, 1)
    data, posicao = myprofit_holdings(c["dono"].id)
    assert data == FIM and posicao["AAA3"].quantity == D("30") and "BBB3" not in posicao
    antiga_data, antiga = myprofit_holdings(c["dono"].id, date(2026, 9, 30))
    assert antiga_data == date(2026, 9, 30) and antiga["AAA3"].quantity == D("10")


def test_snapshot_guarda_so_o_que_a_conciliacao_compara_e_marca_o_escopo(sessao, cenario):
    c = cenario
    _snapshot(c, sessao, [linha_posicao("AAA3", 10, "1"), linha_posicao("CGC", 3, "5", grupo="Ações EUA")])

    linhas = {
        m.asset: m.in_scope
        for m in sessao.scalars(select(MyprofitPosition).where(MyprofitPosition.owner_id == c["dono"].id))
    }
    assert linhas == {"AAA3": True, "CGC": False}
    _, posicao = myprofit_holdings(c["dono"].id)
    assert set(posicao) == {"AAA3"}


def test_posicoes_confere_ignora_exterior_e_outro_dono(sessao, cenario):
    c = cenario
    a, b, eua = c["t"]["ACAOA"], c["t"]["ACAOB"], c["t"]["EUA"]
    sessao.add_all(
        [
            _posicao(c, a, c["real"], "100", "10.00"),
            _posicao(c, eua, c["real"], "7", "100.00"),
            _posicao(c, b, c["do_outro"], "50", "3.00", dono=c["outro"]),
        ]
    )
    sessao.flush()
    _snapshot(c, sessao, [linha_posicao(a.symbol, 100, "10.0001")])

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.positions) == {a.symbol: MATCH}
    assert resultado.pending == []


def test_posicao_diferente_e_ativo_que_so_um_lado_tem(sessao, cenario):
    c = cenario
    a, b = c["t"]["ACAOA"], c["t"]["ACAOB"]
    sessao.add(_posicao(c, a, c["real"], "100", "10.00"))
    sessao.flush()
    _snapshot(c, sessao, [linha_posicao(a.symbol, 130, "10"), linha_posicao(b.symbol, 5, "2")])

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.positions) == {a.symbol: QUANTITY, b.symbol: ONLY_MYPROFIT}
    assert len(resultado.pending) == 2


def test_posicao_so_no_crv_quando_o_myprofit_nao_a_tem(sessao, cenario):
    c = cenario
    a = c["t"]["ACAOA"]
    sessao.add(_posicao(c, a, c["real"], "100", "10.00"))
    sessao.flush()
    _snapshot(c, sessao, [linha_posicao("OUTRO3", 1, "1")])

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.positions)[a.symbol] == ONLY_CRV


def test_sem_posicao_gravada_so_as_vendas_sao_conciliadas(sessao, cenario):
    c = cenario

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert resultado.as_of is None and resultado.positions == []


def test_vendas_somam_as_partes_e_acusam_o_ciclo_que_falta(sessao, cenario):
    c = cenario
    a, b = c["t"]["ACAOA"], c["t"]["ACAOB"]
    sessao.add_all(
        [
            _fechada(c, a, c["real"], "300", date(2026, 3, 10)),
            _fechada(c, a, c["real"], "70", date(2026, 3, 10)),
            _fechada(c, a, c["real"], "40", date(2025, 12, 30)),
        ]
    )
    sessao.flush()
    _vendas(
        c,
        sessao,
        [
            linha(date(2026, 3, 10), "B3_GENIAL_10032026", a.symbol, -300, "3600"),
            linha(date(2026, 3, 10), "B3_GENIAL_10032026", a.symbol, -70, "840"),
            linha(date(2026, 4, 1), "B3_GENIAL_01042026", b.symbol, -600, "7200"),
            linha(date(2026, 4, 1), "B3_GENIAL_01042026", b.symbol, 50, "-500"),
        ],
    )

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.sales) == {a.symbol: MATCH, b.symbol: ONLY_EXECUTIONS}
    # A venda do ano passado não entra no período, nem na soma do CRV.
    antes = build_conciliation(c["dono"].id, start=date(2025, 1, 1), end=date(2025, 12, 31))
    assert _estados(antes.sales) == {a.symbol: ONLY_CRV}


def test_venda_de_outro_dono_nao_entra(sessao, cenario):
    c = cenario
    a = c["t"]["ACAOA"]
    sessao.add(_fechada(c, a, c["do_outro"], "100", date(2026, 3, 10), dono=c["outro"]))
    sessao.flush()

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert resultado.sales == []


def test_opcao_fechada_entra_pelo_codigo_da_opcao(sessao, cenario):
    c = cenario
    sufixo = c["sufixo"]
    objeto = c["t"]["ACAOA"]
    opcao = Ticker(
        symbol=f"OP{sufixo}", trading_name="opção", market=Market.B3, rtd_market_code="B", currency="BRL"
    )
    vencimento = OptionExpiration(call_code=f"{sufixo[:4]}A", put_code=f"{sufixo[:4]}M", exercise_date=date(2027, 1, 15))
    sessao.add_all([opcao, vencimento])
    sessao.flush()
    contrato = OptionContract(
        ticker_id=opcao.id, underlying_ticker_id=objeto.id, expiration_id=vencimento.id,
        option_type=OptionType.CALL, strike=D("2.00"),
    )
    sessao.add(contrato)
    sessao.flush()
    sessao.add(
        Transaction(
            owner_id=c["dono"].id, broker_id=c["corretora"].id, option_contract_id=contrato.id,
            portfolio_id=c["real"].id, quantity=D("16300"), average_cost=D("0.09"),
            exit_price=D("0.12"), side=Side.BUY, opened_on=date(2025, 11, 7),
            closed_on=date(2026, 1, 28), status=TransactionStatus.CLOSED, result=D("489"),
        )
    )
    sessao.flush()
    _vendas(c, sessao, [linha(date(2026, 1, 28), "B3_GENIAL_28012026", opcao.symbol, -16300, "1952.41", grupo="Opções")])

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.sales) == {opcao.symbol: MATCH}


def _movimento(c, posicao, tipo, delta, dia, quantidade_final, custo_final, *, transacao=None):
    return PositionMovement(
        owner_id=c["dono"].id, position_id=posicao.id, kind=tipo, quantity_delta=D(delta),
        price=D(custo_final), occurred_on=dia, resulting_quantity=D(quantidade_final),
        resulting_average_cost=D(custo_final),
        result=D("0") if tipo == PositionMovementKind.DECREASE else None,
        transaction_id=transacao.id if transacao is not None else None,
    )


def test_posicao_do_crv_e_a_da_data_do_myprofit_e_nao_a_de_hoje(sessao, cenario):
    """O caso de 19/10/2026: o que foi vendido depois da data não é "só no myProfit".

    ACAOA: vendida inteira depois da data (só resta a transação fechada).
    ACAOB: comprada a mais e vendida em parte depois da data (posição viva).
    Uma terceira, aberta depois da data, não existia nela.
    """
    c = cenario
    a, b = c["t"]["ACAOA"], c["t"]["ACAOB"]
    nova = Ticker(
        symbol=f"NOVA{c['sufixo']}", trading_name="nova", market=Market.B3, rtd_market_code="B", currency="BRL"
    )
    sessao.add(nova)
    sessao.flush()
    data = date(2026, 9, 30)
    vendida_depois = _fechada(c, a, c["real"], "300", date(2026, 10, 5))
    viva = _posicao(c, b, c["real"], "120", "11.00")
    aberta_depois = _posicao(c, nova, c["real"], "10", "5.00")
    aberta_depois.opened_on = date(2026, 10, 10)
    sessao.add_all([vendida_depois, viva, aberta_depois])
    sessao.flush()
    parcial = _fechada(c, b, c["real"], "30", date(2026, 10, 12))
    parcial.source_position_id = viva.id
    sessao.add(parcial)
    sessao.flush()
    sessao.add_all(
        [
            _movimento(c, viva, PositionMovementKind.OPEN, "100", date(2026, 3, 2), "100", "10.00"),
            _movimento(c, viva, PositionMovementKind.INCREASE, "50", date(2026, 10, 8), "150", "11.00"),
            _movimento(c, viva, PositionMovementKind.DECREASE, "-30", date(2026, 10, 12), "120", "11.00", transacao=parcial),
        ]
    )
    sessao.flush()
    _snapshot(c, sessao, [linha_posicao(a.symbol, 300, "10"), linha_posicao(b.symbol, 100, "10")], ate=data)

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert resultado.as_of == data
    assert _estados(resultado.positions) == {a.symbol: MATCH, b.symbol: MATCH}
    na_data = crv_holdings(c["dono"].id, data)
    assert (na_data[b.symbol].quantity, na_data[b.symbol].average) == (D("100"), D("10.00"))
    hoje = crv_holdings(c["dono"].id)
    assert set(hoje) == {b.symbol, nova.symbol} and hoje[b.symbol].quantity == D("120")


def test_ajuste_de_cadastro_depois_da_data_vale_para_tras(sessao, cenario):
    c = cenario
    a = c["t"]["ACAOA"]
    posicao = _posicao(c, a, c["real"], "100", "10.00")
    sessao.add(posicao)
    sessao.flush()
    sessao.add_all(
        [
            _movimento(c, posicao, PositionMovementKind.OPEN, "90", date(2026, 3, 2), "90", "10.00"),
            _movimento(c, posicao, PositionMovementKind.ADJUSTMENT, "10", date(2026, 10, 15), "100", "10.00"),
        ]
    )
    sessao.flush()
    _snapshot(c, sessao, [linha_posicao(a.symbol, 100, "10")], ate=date(2026, 9, 30))

    assert _estados(build_conciliation(c["dono"].id, start=INICIO, end=FIM).positions) == {a.symbol: MATCH}


def test_custo_medio_explicado_pelas_taxas_das_execucoes(sessao, cenario):
    c = cenario
    a = c["t"]["ACAOA"]
    sessao.add(_posicao(c, a, c["real"], "300", "1.0000"))
    sessao.flush()
    _vendas(c, sessao, [linha(date(2026, 3, 2), "B3_GENIAL_02032026", a.symbol, 300, "-300.00", taxas="1.38")])
    _snapshot(c, sessao, [linha_posicao(a.symbol, 300, "1.0046")])

    resultado = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(resultado.positions) == {a.symbol: FEES}
    assert resultado.pending == []


def test_comando_recusa_posicao_com_data_futura(cenario, app_com_banco, tmp_path, monkeypatch):
    """Pedida com data futura, o myProfit devolve a posição de hoje com aquele rótulo."""
    monkeypatch.setitem(app_com_banco.config, "PATRIMONIO_OWNER_ID", str(cenario["dono"].id))
    futura = tmp_path / "posicao-futura.tsv"
    futura.write_bytes(posicao_tsv([linha_posicao("AAA3", 1, "1")], ate=date.today() + timedelta(days=12)))
    passada = tmp_path / "posicao-passada.tsv"
    passada.write_bytes(posicao_tsv([linha_posicao("AAA3", 1, "1")], ate=date.today() - timedelta(days=1)))
    runner = app_com_banco.test_cli_runner()

    with app_com_banco.app_context():
        recusada = runner.invoke(args=["importar-posicao-myprofit", str(futura)])
        aceita = runner.invoke(args=["importar-posicao-myprofit", str(passada)])

    assert recusada.exit_code != 0 and "data futura" in recusada.output
    assert aceita.exit_code == 0 and "Ensaio" in aceita.output


def test_reconhecimento_tira_a_pendencia_e_pode_ser_removido(sessao, cenario):
    c = cenario
    b = c["t"]["ACAOB"]
    _vendas(c, sessao, [linha(date(2026, 4, 1), "B3_GENIAL_01042026", b.symbol, -600, "7200")])
    assert len(build_conciliation(c["dono"].id, start=INICIO, end=FIM).pending) == 1

    set_note(c["dono"].id, "venda", b.symbol, "atribuição: prêmio no custo da ação")
    set_note(c["dono"].id, "venda", b.symbol, "atribuição: prêmio no custo da ação (revisto)")
    sessao.flush()
    reconhecida = build_conciliation(c["dono"].id, start=INICIO, end=FIM)

    assert _estados(reconhecida.sales) == {b.symbol: ACKNOWLEDGED}
    assert reconhecida.pending == []
    assert reconhecida.sales[0].note.endswith("(revisto)")
    assert len(list(sessao.scalars(select(ReconciliationNote).where(ReconciliationNote.owner_id == c["dono"].id)))) == 1
    assert remove_note(c["dono"].id, "venda", b.symbol) is True
    assert remove_note(c["dono"].id, "venda", b.symbol) is False
    assert len(build_conciliation(c["dono"].id, start=INICIO, end=FIM).pending) == 1
