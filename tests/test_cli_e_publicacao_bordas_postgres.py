"""CLI com códigos de saída reais e limites da publicação autenticada."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_financial_isolation_http import review_case as review_case

from app import db
from app.models import ReconciliationNote, User

pytestmark = pytest.mark.banco


def executar(app, args):
    with app.app_context():
        return app.test_cli_runner().invoke(args=args)


@pytest.fixture
def caso(review_case, monkeypatch):
    app, dados = review_case
    monkeypatch.setitem(app.config, "PATRIMONIO_INTEGRATION_TOKEN", "token-qa-sintetico-publicacao-sem-uso-operacional")
    monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", dados["user_ids"][0])
    monkeypatch.setitem(app.config, "PATRIMONIO_TITULAR", "Titular sintético")
    with app.app_context():
        dados["username"] = db.session.get(User, dados["user_ids"][0]).username
    yield app, dados
    with app.app_context():
        db.session.rollback()
        db.session.execute(delete(ReconciliationNote).where(ReconciliationNote.owner_id.in_(dados["user_ids"])))
        db.session.commit()


@pytest.mark.parametrize("tipo", ["posicao", "venda"])
def test_cli_reconhecimento_atualiza_mesmo_registro_e_remove_sem_afetar_outro_dono(caso, tipo):
    app, dados = caso
    ativo = "QA" + uuid4().hex[:8].upper()
    args = ["reconhecer-conciliacao", tipo, ativo.lower()]
    assert executar(app, [*args, "Motivo inicial"]).exit_code == 0
    assert executar(app, [*args, "Motivo atualizado"]).exit_code == 0
    with app.app_context():
        registro = db.session.scalars(select(ReconciliationNote).where(ReconciliationNote.owner_id == dados["user_ids"][0])).one()
        assert registro.asset == ativo and registro.note == "Motivo atualizado"
        db.session.add(ReconciliationNote(owner_id=dados["user_ids"][2], kind=tipo, asset=ativo, note="De outro dono"))
        db.session.commit()
    assert executar(app, [*args, "--remover"]).exit_code == 0
    assert executar(app, [*args, "--remover"]).exit_code == 0
    with app.app_context():
        registros = db.session.scalars(select(ReconciliationNote).where(ReconciliationNote.asset == ativo)).all()
        assert [(r.owner_id, r.note) for r in registros] == [(dados["user_ids"][2], "De outro dono")]


@pytest.mark.parametrize("nota", ["", "x" * 301])
def test_cli_recusa_nota_invalida_sem_gravar(caso, nota):
    app, dados = caso
    resultado = executar(app, ["reconhecer-conciliacao", "posicao", "QATEST", nota])
    assert resultado.exit_code == 1 and "Error:" in resultado.output
    with app.app_context():
        assert db.session.scalar(select(ReconciliationNote.id).where(ReconciliationNote.owner_id == dados["user_ids"][0])) is None


@pytest.mark.parametrize("situacao", ["sem_owner", "usuario_ausente"])
def test_cli_exige_dono_valido_antes_de_gravar(caso, monkeypatch, situacao):
    app, dados = caso
    args = ["reconhecer-conciliacao", "venda", "QATEST", "Explicação"]
    if situacao == "sem_owner":
        monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", "")
    else:
        args.extend(["--usuario", "usuario-que-nao-existe-" + uuid4().hex])
    resultado = executar(app, args)
    assert resultado.exit_code == 1 and "Error:" in resultado.output
    with app.app_context():
        assert db.session.scalar(select(ReconciliationNote.id).where(ReconciliationNote.owner_id == dados["user_ids"][0])) is None


@pytest.mark.parametrize("comando", ["importar-negocios-myprofit", "importar-posicao-myprofit"])
@pytest.mark.parametrize("defeito", ["corrompido", "excesso_tamanho"])
def test_cli_recusa_arquivo_invalido_com_erro_controlado(caso, tmp_path, comando, defeito):
    app, _ = caso
    arquivo = tmp_path / "relatorio.xlsx"
    arquivo.write_bytes("arquivo sintético inválido".encode() if defeito == "corrompido" else b"x" * (2 * 1024 * 1024 + 1))
    resultado = executar(app, [comando, str(arquivo), "--gravar"])
    assert resultado.exit_code == 1
    assert "Error:" in resultado.output and "Traceback" not in resultado.output


@pytest.mark.parametrize("consulta", ["limit=0", "limit=501", "limit=abc", "after=inválido", "after=" + "x" * 257])
def test_feed_recusa_paginacao_ou_cursor_invalido(caso, consulta):
    app, _ = caso
    headers = {"Authorization": "Bearer " + app.config["PATRIMONIO_INTEGRATION_TOKEN"]}
    assert app.test_client().get("/patrimonio/v4/changes?" + consulta, headers=headers).status_code == 400


def test_cursor_http_fica_vinculado_ao_dono_e_ao_token(caso, monkeypatch):
    app, dados = caso
    cliente = app.test_client()
    headers = {"Authorization": "Bearer " + app.config["PATRIMONIO_INTEGRATION_TOKEN"]}
    resposta = cliente.get("/patrimonio/v4/changes?limit=1", headers=headers)
    assert resposta.status_code == 200 and resposta.headers["Cache-Control"] == "no-store"
    cursor = resposta.json["next_cursor"]
    assert cliente.get("/patrimonio/v4/changes", query_string={"after": cursor}, headers=headers).status_code == 200
    monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", dados["user_ids"][2])
    assert cliente.get("/patrimonio/v4/changes", query_string={"after": cursor}, headers=headers).status_code == 400
    monkeypatch.setitem(app.config, "PATRIMONIO_OWNER_ID", dados["user_ids"][0])
    monkeypatch.setitem(app.config, "PATRIMONIO_INTEGRATION_TOKEN", "nova-raiz-sintetica-rotacionada-qa")
    novos_headers = {"Authorization": "Bearer " + app.config["PATRIMONIO_INTEGRATION_TOKEN"]}
    assert cliente.get("/patrimonio/v4/changes", query_string={"after": cursor}, headers=novos_headers).status_code == 400


@pytest.mark.parametrize("rota,consulta", [("snapshot", "include_prices=talvez"), ("ledger", "page_size=0"),
                                          ("activities", "page_size=0")])
def test_publicacao_recusa_parametros_invalidos(caso, rota, consulta):
    app, _ = caso
    headers = {"Authorization": "Bearer " + app.config["PATRIMONIO_INTEGRATION_TOKEN"]}
    assert app.test_client().get(f"/patrimonio/v4/{rota}?{consulta}", headers=headers).status_code == 400
