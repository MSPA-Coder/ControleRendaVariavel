"""A aplicacao nega por padrao.

Uma rota que deixa de exigir sessao continua respondendo 200 e parecendo
correta: a falha e silenciosa e so aparece quando alguem de fora ja entrou.
"""

from __future__ import annotations

import re

from app import PUBLIC_ENDPOINTS
from app.routes.auth import _local_next_url

#: Substitui `<int:id>`, `<path:filename>` e afins por um valor navegavel.
_PARAMETRO = re.compile(r"<(?:(?P<tipo>[^:<>]+):)?[^<>]+>")


def _url_plausivel(regra) -> str:
    """URL concreta para uma rota, com ou sem parametro.

    Rotas com parametro ja foram puladas aqui, com o argumento de que "as sem
    parametro ja cobrem a decisao, que e do `before_request` e nao da rota".
    O argumento e verdadeiro sobre ONDE a decisao mora e falso sobre o que a
    suite mede: no MegaSena, a unica rota parametrizada nao publica era
    justamente a que estava errada, e o filtro a descartava. Custa pouco
    verificar todas.

    O `before_request` recusa antes de a view rodar, entao um id inexistente
    nao chega a consultar o banco -- que esta ausente nesta suite.
    """

    def valor(m: re.Match[str]) -> str:
        return "1" if (m.group("tipo") or "") in ("int", "float") else "x"

    return _PARAMETRO.sub(valor, regra.rule)


def _rotas_get_registradas(app):
    for regra in app.url_map.iter_rules():
        if regra.endpoint in PUBLIC_ENDPOINTS:
            continue
        if "GET" not in (regra.methods or set()):
            continue
        yield _url_plausivel(regra)


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
    assert {
        "auth.login",
        "portfolio.health",
        "portfolio.collector_agent_configuration",
        "portfolio.collector_agent_quotes",
        "portfolio.collector_agent_failure",
        "static",
        # CSS do banner de mensagem, que `login.html` usa. Sem isto o
        # `requer_login` bloquearia o próprio arquivo que estiliza "Usuário ou
        # senha inválidos" e a mensagem apareceria sem cor nenhuma.
        #
        # `sharedauth_ui.static` (modal e toast) NÃO entra: só é referenciado em
        # `base.html`, e `login.html` não o estende. Deixar de fora é
        # deliberado, não esquecimento -- superfície pública é o que esta lista
        # existe para manter curta.
        "sharedauth.static",
    } == PUBLIC_ENDPOINTS


def test_login_aceita_apenas_destino_local_sem_barra_invertida():
    assert _local_next_url("/portfolio?tab=summary") == "/portfolio?tab=summary"
    assert _local_next_url("//externo.test") is None
    assert _local_next_url("/\\externo.test") is None
    assert _local_next_url("/%5cexterno.test") is None
    assert _local_next_url("/%255cexterno.test") is None
    assert _local_next_url("/%2f%2fexterno.test") is None
    assert _local_next_url("/%252f%252fexterno.test") is None
    assert _local_next_url("https://externo.test") is None


def test_login_recusa_destino_com_separador_aninhado_alem_de_tres_camadas():
    destino = "/%5cexterno.test"
    for _ in range(6):
        destino = destino.replace("%", "%25")

    assert _local_next_url(destino) is None
