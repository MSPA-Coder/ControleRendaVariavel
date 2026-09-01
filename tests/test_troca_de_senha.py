"""Senha redefinida por administrador vale ate o primeiro acesso.

Quando um administrador redefine a senha de alguem, essa senha passa a ser
conhecida por duas pessoas. A obrigacao de trocar existe para encurtar essa
janela -- e so vale se for verificada em TODA requisicao. Aplicar o desvio
apenas no login e a falha silenciosa que este arquivo mede: a marca fica
ligada, a tela some da frente, e a pessoa segue usando a senha que o
administrador conhece.

A suite nao toca o banco (ver `conftest.py`): as chamadas de servico tem o
commit, a trava e a trilha substituidos, e o carregamento de usuario e trocado
por um objeto em memoria.
"""

from __future__ import annotations

import pytest
from sharedauth.session import marca_de_sessao

from app import CHAVE_TEMA_NA_SESSAO, PUBLIC_ENDPOINTS, login_manager
from app import user_management as um
from app.models import ROLE_ADMIN, ROLE_OPERADOR, User
from app.themes import DEFAULT_THEME


def _login_as(client, user, monkeypatch):
    # `monkeypatch`, e nao atribuicao direta: neste app o `@user_loader` e
    # registrado no import do modulo, nao dentro de `create_app`. Substituir o
    # callback sem desfazer vazava para TODO teste seguinte -- inclusive os que
    # existem justamente para exercitar o carregador de verdade.
    monkeypatch.setattr(login_manager, "_user_callback", lambda _user_id: user)
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
        # O tema entra no cache da sessao pelo mesmo motivo que o app o guarda
        # la: sem ele, o context processor de `app/__init__.py` consulta
        # `AppSetting` em todo render autenticado -- e esta suite nao tem
        # banco. Uma sessao de verdade chega aqui com o tema ja em cache
        # depois do primeiro render.
        session[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME


def _usuario(**kwargs) -> User:
    padrao = {
        "id": 1,
        "username": "fulano",
        "role": ROLE_OPERADOR,
        "is_active_user": True,
        "must_change_password": False,
    }
    padrao.update(kwargs)
    return User(**padrao)


@pytest.fixture
def sem_banco(monkeypatch):
    """Substitui o que fala com o banco, preservando a decisao de cada caso."""
    trilha: list[tuple] = []
    monkeypatch.setattr(um, "_lock_admin_mutations", lambda: None)
    monkeypatch.setattr(um, "_commit", lambda: None)
    monkeypatch.setattr(
        um, "registrar", lambda *args, **kwargs: trilha.append((args, kwargs))
    )
    return trilha


# --- o portao ------------------------------------------------------------


def test_marca_ligada_desvia_qualquer_rota_para_a_troca(app, client, monkeypatch):
    _login_as(client, _usuario(must_change_password=True), monkeypatch)

    resposta = client.get("/settings", follow_redirects=False)

    assert resposta.status_code == 302
    assert resposta.headers["Location"] == "/minha-senha"


def test_marca_desligada_nao_atrapalha(app, client, monkeypatch):
    # Rota sintetica de proposito: toda rota real protegida deste app consulta
    # o banco, e esta suite nao tem banco. Com o desvio no caminho isso nao
    # aparece -- o portao responde antes da view --, mas o caso "deixa passar"
    # precisa chegar ate a view para provar alguma coisa.
    app.add_url_rule("/rota-sintetica", "portfolio.rota_sintetica", lambda: "chegou")
    _login_as(client, _usuario(must_change_password=False), monkeypatch)

    resposta = client.get("/rota-sintetica")

    assert resposta.status_code == 200
    assert resposta.get_data(as_text=True) == "chegou"


def test_a_tela_de_troca_nao_entra_em_laco(app, client, monkeypatch):
    # A tela que existe para sair da situacao nao pode redirecionar para si
    # mesma. `sharedauth.access` isenta `endpoint_troca` automaticamente.
    _login_as(client, _usuario(must_change_password=True), monkeypatch)

    assert client.get("/minha-senha").status_code == 200


def test_logout_funciona_de_dentro_da_trava(app, client, monkeypatch):
    # Sem isto a pessoa fica presa dentro do aplicativo: todo destino devolve
    # para a tela de troca, inclusive a saida.
    #
    # O logout deste app grava na trilha antes de encerrar a sessao; aqui a
    # gravacao e substituida, porque o que se mede e o portao, nao a trilha.
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr("app.routes.auth.registrar", lambda *a, **k: None)
    monkeypatch.setattr(um.db.session, "commit", lambda: None)
    _login_as(client, _usuario(must_change_password=True), monkeypatch)

    resposta = client.post("/logout", follow_redirects=False)

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


@pytest.mark.parametrize(
    "rota",
    [
        "/static/app.css",
        "/sharedauth/static/flash_messages.css",
        "/sharedauth/ui/sharedauth-ui.css",
        "/health",
    ],
)
def test_estaticos_e_saude_ficam_isentos(app, client, rota, monkeypatch):
    # Sem os estaticos a tela de troca chega sem CSS; sem `/health` o conteiner
    # seria reportado como doente justamente para quem esta com a senha vencida.
    _login_as(client, _usuario(must_change_password=True), monkeypatch)

    resposta = client.get(rota)

    assert resposta.status_code != 302, f"{rota} foi desviada para a troca"


def test_htmx_com_marca_ligada_recebe_hx_redirect(app, client, monkeypatch):
    # Uma troca de fragmento nao pode devolver a tela de troca dentro de um
    # pedaco de pagina -- mesmo motivo do `usar_hx_redirect` do login.
    _login_as(client, _usuario(must_change_password=True), monkeypatch)

    resposta = client.get("/settings", headers={"HX-Request": "true"})

    assert resposta.status_code == 403
    assert resposta.headers["HX-Redirect"] == "/minha-senha"


def test_anonimo_continua_indo_para_o_login(app, client):
    # O portao da troca nao pode roubar o caso do anonimo: quem nao entrou nao
    # tem senha a trocar.
    resposta = client.get("/settings", follow_redirects=False)

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_a_tela_de_troca_nao_e_publica():
    assert "account.change_password" not in PUBLIC_ENDPOINTS


# --- servico: redefinicao pelo administrador -----------------------------


def test_redefinir_gera_senha_temporaria_e_liga_a_marca(monkeypatch, sem_banco):
    alvo = _usuario(id=2)
    alvo.set_password("senha-antiga")
    monkeypatch.setattr(um, "_locked_user", lambda _id: alvo)

    usuario, senha = um.reset_password(2)

    assert usuario is alvo
    assert alvo.must_change_password is True
    assert alvo.check_password(senha) is True
    assert alvo.check_password("senha-antiga") is False


def test_redefinir_nao_repete_a_senha_entre_chamadas(monkeypatch, sem_banco):
    alvo = _usuario(id=2)
    alvo.set_password("senha-antiga")
    monkeypatch.setattr(um, "_locked_user", lambda _id: alvo)

    _, primeira = um.reset_password(2)
    _, segunda = um.reset_password(2)

    assert primeira != segunda


def test_a_senha_temporaria_nao_entra_na_trilha(monkeypatch, sem_banco):
    # Complemento do que `test_auditoria.py` mede lendo o codigo-fonte: aqui a
    # funcao roda de verdade e o registro e inspecionado.
    alvo = _usuario(id=2, username="fulano")
    alvo.set_password("senha-antiga")
    monkeypatch.setattr(um, "_locked_user", lambda _id: alvo)

    _, senha = um.reset_password(2)

    assert senha not in repr(sem_banco)


def test_a_senha_temporaria_nunca_e_guardada_em_texto_claro(monkeypatch, sem_banco):
    alvo = _usuario(id=2)
    monkeypatch.setattr(um, "_locked_user", lambda _id: alvo)

    _, senha = um.reset_password(2)

    valores = [
        str(valor) for chave, valor in vars(alvo).items() if not chave.startswith("_")
    ]
    assert senha not in valores


# --- servico: criacao de conta e bootstrap por CLI -----------------------


def test_conta_nova_nasce_com_a_marca_ligada(monkeypatch, sem_banco):
    # Conta nova tem senha que quem administra escolheu e conhece: e o mesmo
    # caso da redefinicao.
    monkeypatch.setattr(um.db.session, "add", lambda _user: None)
    monkeypatch.setattr(um.db.session, "flush", lambda: None)
    monkeypatch.setattr(um.db.session, "scalar", lambda _stmt: None)

    novo = um.create_user("fulano", ROLE_ADMIN, "senha-boa-123", "senha-boa-123")

    assert novo.must_change_password is True


def test_bootstrap_por_cli_nao_liga_a_marca(monkeypatch, sem_banco):
    # Quem roda o comando tem shell no conteiner e escolheu a propria senha:
    # nao existe o terceiro que a redefinicao pela tela pressupoe.
    monkeypatch.setattr(um.db.session, "add", lambda _user: None)
    monkeypatch.setattr(um.db.session, "flush", lambda: None)
    monkeypatch.setattr(um.db.session, "scalar", lambda _stmt: None)

    novo = um.upsert_from_cli("admin", ROLE_ADMIN, "senha-boa-123")

    # `is not True`, e nao `is False`: o `default=False` da coluna e aplicado
    # pelo SQLAlchemy no flush, e esta suite substitui o commit -- em memoria o
    # atributo ainda e `None`. O que se mede aqui e que o caminho da CLI nao
    # LIGA a obrigacao; a segunda assercao cobre o padrao da coluna.
    assert novo.must_change_password is not True
    assert User.__table__.c.must_change_password.default.arg is False


# --- servico: troca feita pelo dono --------------------------------------


def test_troca_do_dono_desliga_a_marca(sem_banco):
    usuario = _usuario(must_change_password=True)
    usuario.set_password("senha-temporaria")

    um.change_own_password(usuario, "senha-temporaria", "minha-senha-1", "minha-senha-1")

    assert usuario.must_change_password is False
    assert usuario.check_password("minha-senha-1") is True


def test_troca_sem_a_senha_atual_correta_e_recusada(sem_banco):
    # Sem esta conferencia, uma sessao sequestrada vira tomada de conta.
    usuario = _usuario(must_change_password=True)
    usuario.set_password("senha-temporaria")

    with pytest.raises(um.UserManagementError):
        um.change_own_password(usuario, "chute", "minha-senha-1", "minha-senha-1")

    assert usuario.must_change_password is True
    assert usuario.check_password("senha-temporaria") is True


def test_redigitar_a_senha_temporaria_nao_conclui_a_troca(sem_banco):
    # O caso que esvaziaria a obrigacao: a marca se apagaria e a senha que o
    # administrador conhece continuaria valendo.
    usuario = _usuario(must_change_password=True)
    usuario.set_password("senha-temporaria")

    with pytest.raises(um.UserManagementError):
        um.change_own_password(
            usuario, "senha-temporaria", "senha-temporaria", "senha-temporaria"
        )

    assert usuario.must_change_password is True


def test_erro_de_troca_vira_erro_seguro_para_a_tela(sem_banco):
    # `UserManagementError` e o que as rotas capturam para exibir; um
    # `ValueError` cru de `sharedauth` viraria 500 na tela de troca.
    usuario = _usuario()
    usuario.set_password("senha-atual-1")

    with pytest.raises(um.UserManagementError):
        um.change_own_password(usuario, "senha-atual-1", "curta12", "curta12")


# --- a tela de troca -----------------------------------------------------


def test_troca_pela_tela_redireciona_e_libera(app, client, sem_banco, monkeypatch):
    app.config["WTF_CSRF_ENABLED"] = False
    usuario = _usuario(must_change_password=True)
    usuario.set_password("senha-temporaria")
    _login_as(client, usuario, monkeypatch)

    resposta = client.post(
        "/minha-senha",
        data={
            "current_password": "senha-temporaria",
            "new_password": "minha-senha-1",
            "password_confirm": "minha-senha-1",
        },
        follow_redirects=False,
    )

    assert resposta.status_code == 302
    assert usuario.must_change_password is False


def test_troca_recusada_responde_422_e_mantem_a_marca(app, client, sem_banco, monkeypatch):
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(um.db.session, "rollback", lambda: None)
    usuario = _usuario(must_change_password=True)
    usuario.set_password("senha-temporaria")
    _login_as(client, usuario, monkeypatch)

    resposta = client.post(
        "/minha-senha",
        data={
            "current_password": "chute-errado",
            "new_password": "minha-senha-1",
            "password_confirm": "minha-senha-1",
        },
    )

    assert resposta.status_code == 422
    assert usuario.must_change_password is True


# --- a sessao deixa de valer quando a senha muda -------------------------


def test_a_marca_da_senha_entra_no_identificador_de_sessao(app):
    # O Flask-Login guarda o que `get_id()` devolve. So o id nao bastava:
    # trocar a senha nao derrubava sessao aberta em outro lugar.
    usuario = _usuario()
    usuario.set_password("senha-de-teste")

    with app.app_context():
        identificador = usuario.get_id()

    assert identificador.startswith("1:")
    assert len(identificador.split(":", 1)[1]) == 32


def test_o_identificador_muda_quando_a_senha_muda(app):
    usuario = _usuario()
    usuario.set_password("senha-antiga-1")

    with app.app_context():
        antes = usuario.get_id()
        usuario.set_password("senha-nova-123")
        depois = usuario.get_id()

    assert antes != depois


def test_sessao_com_a_marca_antiga_e_recusada(app, client, monkeypatch):
    # O caso que a mudanca existe para resolver: alguem entrou com a senha
    # antiga, o dono trocou, e a sessao daquele alguem tem de cair.
    usuario = _usuario()
    usuario.set_password("senha-antiga-1")
    with app.app_context():
        identificador_antigo = usuario.get_id()

    monkeypatch.setattr(um.db.session, "get", lambda _modelo, _id: usuario)
    usuario.set_password("senha-nova-123")

    with client.session_transaction() as sessao:
        sessao["_user_id"] = identificador_antigo
        sessao["_fresh"] = True
        sessao[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME

    resposta = client.get("/settings", follow_redirects=False)

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_sessao_com_a_marca_atual_continua_valendo(app, client, monkeypatch):
    app.add_url_rule("/rota-sintetica", "portfolio.rota_sintetica", lambda: "chegou")
    usuario = _usuario()
    usuario.set_password("senha-de-teste")
    with app.app_context():
        identificador = usuario.get_id()

    monkeypatch.setattr(um.db.session, "get", lambda _modelo, _id: usuario)
    with client.session_transaction() as sessao:
        sessao["_user_id"] = identificador
        sessao["_fresh"] = True
        sessao[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME

    assert client.get("/rota-sintetica").status_code == 200


def test_identificador_no_formato_antigo_e_recusado(app, client, monkeypatch):
    # Sessao de antes desta mudanca, que guardava so o id. Cair uma vez, no
    # primeiro acesso depois do deploy, e o comportamento desejado.
    usuario = _usuario()
    usuario.set_password("senha-de-teste")
    monkeypatch.setattr(um.db.session, "get", lambda _modelo, _id: usuario)

    with client.session_transaction() as sessao:
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True
        sessao[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME

    resposta = client.get("/settings", follow_redirects=False)

    assert "/login" in resposta.headers["Location"]


def test_trocar_a_propria_senha_nao_derruba_quem_trocou(app, client, monkeypatch, sem_banco):
    # O efeito que se quer e derrubar as OUTRAS sessoes, nao esta.
    app.config["WTF_CSRF_ENABLED"] = False
    usuario = _usuario()
    usuario.set_password("senha-antiga-1")
    with app.app_context():
        identificador = usuario.get_id()

    monkeypatch.setattr(um.db.session, "get", lambda _modelo, _id: usuario)
    with client.session_transaction() as sessao:
        sessao["_user_id"] = identificador
        sessao["_fresh"] = True
        sessao[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME

    resposta = client.post(
        "/minha-senha",
        data={
            "current_password": "senha-antiga-1",
            "new_password": "minha-senha-1",
            "password_confirm": "minha-senha-1",
        },
        follow_redirects=False,
    )

    assert resposta.status_code == 302
    with client.session_transaction() as sessao:
        assert sessao["_user_id"] != identificador
        assert sessao["_user_id"].endswith(
            marca_de_sessao(usuario.password_hash, chave_secreta=app.secret_key)
        )
