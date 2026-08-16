"""A aplicacao nega por padrao.

Uma rota que deixa de exigir sessao continua respondendo 200 e parecendo
correta: a falha e silenciosa e so aparece quando alguem de fora ja entrou.
"""

from __future__ import annotations

from app import PUBLIC_ENDPOINTS


def _rotas_get_registradas(app):
    for regra in app.url_map.iter_rules():
        if regra.endpoint in PUBLIC_ENDPOINTS:
            continue
        if "GET" not in (regra.methods or set()):
            continue
        # Rotas com parametro exigiriam um valor plausivel; as sem parametro ja
        # cobrem a decisao, que e do `before_request` e nao da rota.
        if regra.arguments:
            continue
        yield regra.rule


def test_existem_rotas_protegidas_para_verificar(app):
    assert list(_rotas_get_registradas(app))


def test_rota_protegida_recusa_acesso_anonimo(app, client):
    for rota in _rotas_get_registradas(app):
        resposta = client.get(rota)
        assert resposta.status_code in (302, 401), (
            f"{rota} respondeu {resposta.status_code} sem sessao"
        )


def test_login_e_publico(client):
    assert client.get("/login").status_code == 200


def test_health_nao_e_barrado_por_sessao(app):
    # O health precisa ser alcancavel sem sessao: e o que o Docker consulta
    # para decidir se o contêiner esta saudavel. Um health atras do login
    # deixaria o orquestrador lendo o redirecionamento como "doente".
    #
    # A rota consulta o banco de verdade, ausente aqui; o que se mede e que ela
    # nao redireciona para o login, nao que o banco responde.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    resposta = app.test_client().get("/health")
    assert resposta.status_code != 302
    assert "/login" not in resposta.headers.get("Location", "")


def test_lista_de_publicos_e_curta_e_conhecida():
    # A lista e de rotas publicas, nao de protegidas: uma rota nova nasce
    # protegida. Acrescentar algo aqui deve ser decisao consciente.
    assert {"auth.login", "portfolio.health", "static"} == PUBLIC_ENDPOINTS
