"""Execuções do myProfit contra o PostgreSQL: constraints, idempotência,
isolamento por dono, privacidade do cabeçalho e fluxo HTTP com ensaio."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.executions.conferencia import MATCH, ONLY_CB, ONLY_CRV, build_reconciliation
from app.executions.myprofit import read_execution_report
from app.executions.service import apply_import, plan_import
from app.models import Broker, Execution, ExecutionImport, User
from app.patrimonio.cb_client import CbConfig
from tests.myprofit_sintetico import (
    CPF_FICTICIO,
    CPF_SO_DIGITOS,
    TITULAR_FICTICIO,
    linha,
    relatorio_xlsx,
)
from tests.test_cb_client import (
    CATEGORIA_BOLSA,
    CONTA_GENIAL,
    _categoria,
    _conta,
    _lancamento,
    _snapshot,
)
from tests.test_financial_isolation_http import TokenParser, client_for

pytestmark = pytest.mark.banco

DIA = date(2026, 1, 27)


@pytest.fixture
def cenario(sessao, app_com_banco, monkeypatch):
    sufixo = uuid4().hex[:8]
    dono = User(username=f"exec-{sufixo}", password_hash="hash")
    outro = User(username=f"exec-outro-{sufixo}", password_hash="hash")
    corretora = Broker(name=f"Genial {sufixo}", acronym=sufixo[:6])
    sessao.add_all([dono, outro, corretora])
    sessao.flush()
    monkeypatch.setitem(
        app_com_banco.config, "MYPROFIT_INSTITUICOES", json.dumps({"GENIAL": corretora.name})
    )
    return dono, outro, corretora


def _plano(dono, linhas):
    return plan_import(dono.id, read_execution_report(relatorio_xlsx(linhas)))


def _venda(documento="B3_GENIAL_27012026", quantidade=-100, total="1000.00"):
    return linha(DIA, documento, "ABCD3", quantidade, total)


def test_primeira_importacao_grava_e_a_segunda_nao_duplica(sessao, cenario):
    dono, _, corretora = cenario
    linhas = [_venda(quantidade=-100, total="1000.00"), _venda(quantidade=-70, total="700.00")]

    primeira = _plano(dono, linhas)
    lote = apply_import(dono.id, primeira, file_name="historico.xlsx")
    segunda = _plano(dono, linhas)

    assert (len(primeira.new_rows), primeira.repeated, len(primeira.changed)) == (2, 0, 0)
    assert (lote.row_count, lote.rows_created, lote.rows_updated) == (2, 2, 0)
    assert (len(segunda.new_rows), segunda.repeated, len(segunda.changed)) == (0, 2, 0)
    assert not segunda.can_write
    gravadas = list(sessao.scalars(select(Execution).where(Execution.owner_id == dono.id)))
    assert len(gravadas) == 2
    assert {item.broker_id for item in gravadas} == {corretora.id}


def test_linhas_identicas_no_mesmo_documento_sao_todas_gravadas_e_reimportar_nao_duplica(sessao, cenario):
    dono, _, _ = cenario
    quatro_iguais = [_venda(quantidade=-10, total="100.00")] * 4

    apply_import(dono.id, _plano(dono, quatro_iguais), file_name=None)
    de_novo = _plano(dono, quatro_iguais)

    assert sessao.scalar(select(text("count(*)")).select_from(Execution).where(Execution.owner_id == dono.id)) == 4
    assert (len(de_novo.new_rows), de_novo.repeated) == (0, 4)


def test_linha_acrescentada_a_um_documento_ja_importado_entra_como_nova(sessao, cenario):
    dono, _, _ = cenario
    apply_import(dono.id, _plano(dono, [_venda(quantidade=-10, total="100.00")]), file_name=None)

    plano = _plano(dono, [_venda(quantidade=-10, total="100.00"), _venda(quantidade=-20, total="200.00")])

    assert (len(plano.new_rows), plano.repeated, len(plano.changed)) == (1, 1, 0)
    assert plano.new_rows[0].line_order == 2


def test_linha_alterada_e_detectada_no_ensaio_e_atualizada_so_ao_gravar(sessao, cenario):
    dono, _, _ = cenario
    apply_import(dono.id, _plano(dono, [_venda(total="1000.00")]), file_name=None)

    plano = _plano(dono, [_venda(total="1000.37")])

    assert (len(plano.new_rows), plano.repeated, len(plano.changed)) == (0, 0, 1)
    antes = sessao.scalar(select(Execution).where(Execution.owner_id == dono.id))
    assert antes.total_with_fees == Decimal("1000.00")
    lote = apply_import(dono.id, plano, file_name=None)
    sessao.refresh(antes)
    assert antes.total_with_fees == Decimal("1000.37")
    assert antes.import_id == lote.id and lote.rows_updated == 1


def test_o_ensaio_nao_grava_nada(sessao, cenario):
    dono, _, _ = cenario

    _plano(dono, [_venda()])

    assert sessao.scalar(select(text("count(*)")).select_from(Execution).where(Execution.owner_id == dono.id)) == 0
    assert sessao.scalar(select(text("count(*)")).select_from(ExecutionImport).where(ExecutionImport.owner_id == dono.id)) == 0


def test_instituicao_sem_corretora_correspondente_impede_a_gravacao(sessao, cenario):
    dono, _, _ = cenario
    plano = _plano(dono, [linha(DIA, "Corretora_X", "ABCD3", -1, "10.00", instituicao="CORRETORA INEXISTENTE")])

    assert not plano.can_write
    assert any("CORRETORA INEXISTENTE" in problema for problema in plano.problems)
    with pytest.raises(ValueError, match="pendências"):
        apply_import(dono.id, plano, file_name=None)


def test_linha_recusada_impede_a_gravacao_do_arquivo_inteiro(sessao, cenario):
    dono, _, _ = cenario
    plano = _plano(dono, [_venda(), linha(DIA, "D", "ABCD3", -1, "10.00", grupo="Grupo Inventado")])

    assert not plano.can_write
    assert len(plano.new_rows) == 1


def test_dono_so_ve_e_compara_com_as_proprias_execucoes(sessao, cenario):
    dono, outro, _ = cenario
    apply_import(dono.id, _plano(dono, [_venda()]), file_name=None)

    plano_do_outro = _plano(outro, [_venda()])

    assert len(plano_do_outro.new_rows) == 1, "a linha do primeiro dono não pode contar como repetida do outro"
    apply_import(outro.id, plano_do_outro, file_name=None)
    for usuario in (dono, outro):
        assert sessao.scalar(select(text("count(*)")).select_from(Execution).where(Execution.owner_id == usuario.id)) == 1


def test_nome_e_cpf_do_cabecalho_nao_chegam_a_nenhuma_coluna_de_texto(sessao, cenario):
    dono, _, _ = cenario
    apply_import(dono.id, _plano(dono, [_venda()]), file_name=f"Historico {CPF_FICTICIO}.xlsx")

    colunas = sessao.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name IN ('executions', 'execution_imports') "
            "AND data_type IN ('character varying', 'text')"
        )
    ).all()
    assert colunas
    for tabela, coluna in colunas:
        achados = sessao.execute(
            text(
                f"SELECT count(*) FROM {tabela} WHERE owner_id = :dono AND ("  # noqa: S608 - nomes vindos do catálogo
                f"{coluna} ILIKE :nome OR {coluna} LIKE :cpf OR {coluna} LIKE :digitos)"
            ),
            {"dono": dono.id, "nome": f"%{TITULAR_FICTICIO.split()[0]}%", "cpf": f"%{CPF_FICTICIO}%", "digitos": f"%{CPF_SO_DIGITOS}%"},
        ).scalar_one()
        assert achados == 0, f"{tabela}.{coluna} guarda dado do cabeçalho"


def test_banco_recusa_o_que_o_dominio_proibe(sessao, cenario):
    dono, _, corretora = cenario
    lote = apply_import(dono.id, _plano(dono, [_venda()]), file_name=None)
    base = {
        "owner_id": dono.id, "import_id": lote.id, "broker_id": corretora.id, "institution": "GENIAL",
        "trade_date": DIA, "document": "DOC", "line_order": 1, "content_hash": "a" * 64, "asset": "ABCD3",
        "asset_group": "acao", "side": "V", "cash_operation": "C", "quantity": Decimal("-1"),
        "price_without_fees": Decimal("1"), "price_with_fees": Decimal("1"), "fees_total": Decimal("0"),
        "total_without_fees": Decimal("1"), "total_with_fees": Decimal("1"), "day_trade": False,
        "currency": "BRL", "in_scope": True,
    }
    invalidos = [
        ("quantity_non_zero", {"quantity": Decimal("0")}),
        ("quantity_finite", {"quantity": Decimal("NaN")}),
        ("total_with_fees_finite", {"total_with_fees": Decimal("NaN")}),
        ("price_with_fees_finite", {"price_with_fees": Decimal("NaN")}),
        ("prices_non_negative", {"price_without_fees": Decimal("-1")}),
        ("fees_non_negative", {"fees_total": Decimal("-0.01")}),
        ("side_valid", {"side": "X"}),
        ("asset_group_valid", {"asset_group": "cripto"}),
        ("currency_valid", {"currency": "brl"}),
        ("line_order_positive", {"line_order": 0}),
    ]
    for restricao, mudanca in invalidos:
        with pytest.raises(IntegrityError, match=restricao), sessao.begin_nested():
            sessao.add(Execution(**{**base, **mudanca}))
            sessao.flush()


def test_banco_recusa_documento_e_ordem_repetidos_para_o_mesmo_dono(sessao, cenario):
    dono, _, _ = cenario
    apply_import(dono.id, _plano(dono, [_venda()]), file_name=None)
    existente = sessao.scalar(select(Execution).where(Execution.owner_id == dono.id))

    with pytest.raises(IntegrityError, match="uq_executions_owner_document_line"), sessao.begin_nested():
        sessao.add(
            Execution(
                owner_id=dono.id, import_id=existente.import_id, broker_id=existente.broker_id,
                institution="GENIAL", trade_date=DIA, document=existente.document, line_order=existente.line_order,
                content_hash="b" * 64, asset="ABCD3", asset_group="acao", side="V", cash_operation="C",
                quantity=Decimal("-1"), price_without_fees=Decimal("1"), price_with_fees=Decimal("1"),
                fees_total=Decimal("0"), total_without_fees=Decimal("1"), total_with_fees=Decimal("1"),
                day_trade=False, currency="BRL", in_scope=True,
            )
        )
        sessao.flush()


def test_banco_recusa_execucao_ligada_a_lote_de_outro_dono(sessao, cenario):
    dono, outro, _ = cenario
    lote_do_outro = apply_import(outro.id, _plano(outro, [_venda()]), file_name=None)
    existente = sessao.scalar(select(Execution).where(Execution.owner_id == outro.id))

    with pytest.raises(IntegrityError, match="fk_executions_import_owner"), sessao.begin_nested():
        existente.owner_id = dono.id
        sessao.flush()
    assert lote_do_outro.owner_id == outro.id


def _fetcher(*liquidacoes):
    itens = [_conta(CONTA_GENIAL, "Genial"), _categoria(CATEGORIA_BOLSA, "Operações em Bolsa")]
    for indice, (dia, valor, tipo) in enumerate(liquidacoes):
        itens.append(
            _lancamento(
                f"e{indice}", CONTA_GENIAL, CATEGORIA_BOLSA,
                f"Operações Bolsa D+2 Pr {dia:%d/%m/%Y} Nc. {indice}", valor, tipo=tipo,
            )
        )
    return _snapshot(*itens)


def test_conferencia_de_ponta_a_ponta_com_cb_simulado(sessao, cenario, app_com_banco, monkeypatch):
    dono, outro, corretora = cenario
    monkeypatch.setitem(app_com_banco.config, "CB_CORRESPONDENCIA_CONTAS", json.dumps({corretora.name: ["genial"]}))
    linhas = [
        linha(date(2026, 1, 27), "B3_GENIAL_27012026", "ABCD3", -300, "1000.00"),
        linha(date(2026, 1, 27), "B3_GENIAL_27012026", "ABCD3", -70, "250.00"),
        linha(date(2026, 3, 20), "B3_GENIAL_20032026", "OPCAO1", 10, "-300.00", grupo="Opções"),
        linha(date(2026, 4, 1), "B3_GENIAL_01042026", "ABCD3", -5, "40.00"),
        linha(date(2026, 6, 26), "B3_ALUGUEL_GENIAL_26062026", "ABCD3", 1, "-0.04"),
        linha(date(2026, 1, 6), "B3_BMF_GENIAL_06012026", "WING26", -1, "44.13", grupo="Índice Futuro", obs="DayTrade"),
    ]
    apply_import(dono.id, _plano(dono, linhas), file_name=None)
    apply_import(outro.id, _plano(outro, [_venda(total="999999.00")]), file_name=None)
    cb = _fetcher(
        (date(2026, 1, 27), "1252.10", "receita"),   # 1250,00 no myProfit: custo implícito de 2,10
        (date(2026, 3, 20), "300.00", "despesa"),    # confere exato
        (date(2026, 5, 5), "80.00", "receita"),      # só no CB
    )

    resultado = build_reconciliation(
        dono.id,
        broker=corretora.name,
        fetcher=cb,
        config_factory=lambda: CbConfig("http://cb.test/patrimonio/v4", "t"),
    )

    estados = {linha.pregao: linha for linha in resultado.rows}
    assert resultado.cb_status == "ok"
    assert estados[date(2026, 1, 27)].state == MATCH
    assert estados[date(2026, 1, 27)].crv_amount == Decimal("1250.00")
    assert estados[date(2026, 1, 27)].implicit_cost == Decimal("-2.10")
    assert estados[date(2026, 3, 20)].implicit_cost == Decimal("0.00")
    assert estados[date(2026, 4, 1)].state == ONLY_CRV
    assert estados[date(2026, 5, 5)].state == ONLY_CB
    assert date(2026, 6, 26) not in estados, "taxa de aluguel não é pregão"
    assert date(2026, 1, 6) not in estados, "futuro fica fora de escopo"
    assert resultado.executions_out_of_scope == 2
    assert max(linha.crv_amount or 0 for linha in resultado.rows) < 100_000, "execução de outro dono vazou"


def test_sem_configuracao_do_cb_a_conferencia_diz_nao_configurado_e_nao_quebra(sessao, cenario):
    dono, _, corretora = cenario
    apply_import(dono.id, _plano(dono, [_venda()]), file_name=None)

    resultado = build_reconciliation(dono.id, broker=corretora.name, config_factory=lambda: None)

    assert resultado.cb_status == "nao_configurado"
    assert "não configurado" in resultado.cb_message
    assert [linha.state for linha in resultado.rows] == ["sem_cb"]


@pytest.fixture
def usuario_http(app_com_banco, monkeypatch):
    sufixo = uuid4().hex[:8]
    with app_com_banco.app_context():
        usuario = User(username=f"exec-http-{sufixo}", role="operador", is_active_user=True, must_change_password=False)
        usuario.set_password("Synthetic-exec-only-2026!")
        corretora = Broker(name=f"Genial {sufixo}", acronym=sufixo[:6])
        db.session.add_all([usuario, corretora])
        db.session.commit()
        ids = (usuario.id, corretora.id, corretora.name, usuario.get_id())
    monkeypatch.setitem(app_com_banco.config, "MYPROFIT_INSTITUICOES", json.dumps({"GENIAL": ids[2]}))
    monkeypatch.setitem(app_com_banco.config, "CB_PATRIMONIO_URL", "")
    try:
        yield app_com_banco, ids
    finally:
        with app_com_banco.app_context():
            db.session.rollback()
            db.session.execute(delete(Execution).where(Execution.owner_id == ids[0]))
            db.session.execute(delete(ExecutionImport).where(ExecutionImport.owner_id == ids[0]))
            db.session.execute(delete(Broker).where(Broker.id == ids[1]))
            db.session.execute(delete(User).where(User.id == ids[0]))
            db.session.commit()


def _enviar(client, token, conteudo, acao, **extra):
    return client.post(
        "/executions/import",
        data={"csrf_token": token, "action": acao, "report": (BytesIO(conteudo), "historico.xlsx"), **extra},
        content_type="multipart/form-data",
    )


def _contar(app, usuario_id):
    with app.app_context():
        return db.session.scalar(
            select(text("count(*)")).select_from(Execution).where(Execution.owner_id == usuario_id)
        )


def test_fluxo_http_ensaio_nao_grava_e_gravar_exige_o_mesmo_arquivo(usuario_http):
    app, (usuario_id, _, _, sessao_id) = usuario_http
    client, token = client_for(app, sessao_id)
    conteudo = relatorio_xlsx([_venda(), _venda(quantidade=-2, total="20.00")])
    sha = read_execution_report(conteudo).sha256

    ensaio = _enviar(client, token, conteudo, "ensaio")
    assert ensaio.status_code == 200
    assert _contar(app, usuario_id) == 0
    html = ensaio.get_data(as_text=True)
    assert TITULAR_FICTICIO not in html and CPF_FICTICIO not in html
    parser = TokenParser()
    parser.feed(html)
    assert f'value="{sha}"' in html

    outro_arquivo = relatorio_xlsx([_venda(quantidade=-3, total="30.00")])
    recusado = _enviar(client, token, outro_arquivo, "gravar", expected_sha256=sha)
    assert recusado.status_code == 422
    assert _contar(app, usuario_id) == 0

    gravado = _enviar(client, token, conteudo, "gravar", expected_sha256=sha)
    assert gravado.status_code == 302
    assert _contar(app, usuario_id) == 2

    repetido = _enviar(client, token, conteudo, "gravar", expected_sha256=sha)
    assert repetido.status_code == 200
    assert _contar(app, usuario_id) == 2


def test_gravar_pelo_http_exige_csrf_e_login(usuario_http):
    app, (usuario_id, _, _, sessao_id) = usuario_http
    client, _ = client_for(app, sessao_id)
    conteudo = relatorio_xlsx([_venda()])

    sem_csrf = client.post(
        "/executions/import",
        data={"action": "gravar", "report": (BytesIO(conteudo), "h.xlsx")},
        content_type="multipart/form-data",
    )
    anonimo = app.test_client().post(
        "/executions/import", data={"action": "gravar", "report": (BytesIO(conteudo), "h.xlsx")},
        content_type="multipart/form-data",
    )

    assert sem_csrf.status_code in (400, 403)
    assert anonimo.status_code in (302, 400, 401, 403)
    assert _contar(app, usuario_id) == 0


def test_tela_de_conferencia_responde_pagina_e_fragmento_e_recusa_post(usuario_http):
    app, (usuario_id, _, _, sessao_id) = usuario_http
    client, token = client_for(app, sessao_id)

    pagina = client.get("/executions/reconciliation")
    fragmento = client.get("/executions/reconciliation", headers={"HX-Request": "true"})
    data_ruim = client.get("/executions/reconciliation?start=ontem")
    post = client.post("/executions/reconciliation", data={"csrf_token": token})

    assert pagina.status_code == 200 and "CB não configurado" in pagina.get_data(as_text=True)
    assert fragmento.status_code == 200 and "<html" not in fragmento.get_data(as_text=True)
    assert data_ruim.status_code == 400
    assert post.status_code == 405


def test_comando_faz_ensaio_por_padrao_grava_com_gravar_e_nao_duplica(usuario_http, tmp_path, monkeypatch):
    app, (usuario_id, _, _, _) = usuario_http
    monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", str(usuario_id))
    arquivo = tmp_path / "historico.xlsx"
    arquivo.write_bytes(relatorio_xlsx([_venda(), _venda(quantidade=-2, total="20.00")]))
    runner = app.test_cli_runner()

    with app.app_context():
        ensaio = runner.invoke(args=["importar-negocios-myprofit", str(arquivo)])
        depois_do_ensaio = _contar(app, usuario_id)
        gravado = runner.invoke(args=["importar-negocios-myprofit", str(arquivo), "--gravar"])
        repetido = runner.invoke(args=["importar-negocios-myprofit", str(arquivo), "--gravar"])
        conferencia = runner.invoke(args=["conferir-pregoes"])

    assert ensaio.exit_code == 0 and "2 nova(s)" in ensaio.output and "nada foi gravado" in ensaio.output
    assert depois_do_ensaio == 0
    assert gravado.exit_code == 0 and "Gravado o lote" in gravado.output
    assert repetido.exit_code == 0 and "2 repetida(s)" in repetido.output and "Nada a gravar" in repetido.output
    assert _contar(app, usuario_id) == 2
    assert conferencia.exit_code == 0 and "CB não configurado" in conferencia.output
    for saida in (ensaio.output, gravado.output, repetido.output, conferencia.output):
        assert TITULAR_FICTICIO not in saida and CPF_FICTICIO not in saida


def test_comando_recusa_arquivo_com_pendencia_e_dono_ausente(usuario_http, tmp_path, monkeypatch):
    app, (usuario_id, _, _, _) = usuario_http
    arquivo = tmp_path / "historico.xlsx"
    arquivo.write_bytes(relatorio_xlsx([linha(DIA, "X", "ABCD3", -1, "10.00", instituicao="DESCONHECIDA")]))
    runner = app.test_cli_runner()

    with app.app_context():
        sem_dono = runner.invoke(args=["importar-negocios-myprofit", str(arquivo)])
        monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", str(usuario_id))
        pendente = runner.invoke(args=["importar-negocios-myprofit", str(arquivo), "--gravar"])

    assert sem_dono.exit_code != 0 and "--usuario" in sem_dono.output
    assert pendente.exit_code != 0 and "DESCONHECIDA" in pendente.output
    assert _contar(app, usuario_id) == 0
