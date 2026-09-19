"""As três rotas do agente coletor têm limite de taxa (CRV-02).

`sharedauth.ratelimit.iniciar_limiter` devolve uma instância por aplicação, e
o limite é aplicado depois do registro das rotas em `app/__init__.py`.
`limiter.limit(...)(app.view_functions[endpoint])` devolve uma função nova;
ela precisa permanecer atribuída a `app.view_functions[endpoint]` para que as
requisições passem pelo limitador -- mesmo cuidado do MegaSena e do
ConfortoTermico, registrado na docstring de `aplicar_limite`.

São a única superfície do sistema alcançável sem sessão (estão em
`PUBLIC_ENDPOINTS`), e por isso a única sem cobertura nenhuma do gate de
login. Os tokens do coletor não estão configurados na suíte
(`CONFIG_DE_TESTE`), então `_require_agent_token` recusa com 503 antes de
tocar o banco -- os testes abaixo contam requisições, não autenticam de
verdade, e por isso não precisam de PostgreSQL.
"""

from __future__ import annotations

import pytest

ROTAS_DO_AGENTE = (
    "/api/collector/configuration",
    "/api/collector/quotes",
    "/api/collector/failure",
)


@pytest.mark.parametrize("caminho", ROTAS_DO_AGENTE)
def test_agente_bloqueia_apos_o_limite(client, caminho) -> None:
    metodo = client.get if caminho.endswith("/configuration") else client.post
    for _ in range(60):
        resposta = metodo(caminho)
        assert resposta.status_code == 503, (
            f"{caminho} respondeu {resposta.status_code} sem token configurado "
            "(esperava 503 -- falha fechada)"
        )
    resposta = metodo(caminho)
    assert resposta.status_code == 429, (
        f"{caminho} não bloqueou depois de 60 chamadas em um minuto"
    )


TOKEN_DE_PATRIMONIO = "token-de-teste-com-mais-de-trinta-e-dois-caracteres"


def test_resumo_de_patrimonio_bloqueia_quem_nao_tem_o_token(app) -> None:
    """Sem titular configurado a rota recusa com 503 antes de tocar o banco,
    e o teste conta requisições sem precisar de PostgreSQL."""
    app.config["PATRIMONIO_TOKEN"] = TOKEN_DE_PATRIMONIO
    cliente = app.test_client()
    errado = {"Authorization": "Bearer outro"}

    for _ in range(30):
        assert cliente.get("/patrimonio/v1/resumo", headers=errado).status_code == 401
    assert cliente.get("/patrimonio/v1/resumo", headers=errado).status_code == 429


def test_resumo_de_patrimonio_tambem_limita_quem_tem_o_token(app) -> None:
    """O token autoriza a integração, mas não cria um caminho sem orçamento."""
    app.config["PATRIMONIO_TOKEN"] = TOKEN_DE_PATRIMONIO
    app.config["PATRIMONIO_TITULAR"] = ""
    cliente = app.test_client()
    certo = {"Authorization": f"Bearer {TOKEN_DE_PATRIMONIO}"}

    for _ in range(30):
        # 503: o token passou, e a rota parou na falta de titular.
        assert cliente.get("/patrimonio/v1/resumo", headers=certo).status_code == 503
    assert cliente.get("/patrimonio/v1/resumo", headers=certo).status_code == 429


def test_agente_nao_compartilha_orcamento_com_outra_rota(client) -> None:
    """Um limite por endpoint, não um balde único para os três.

    Esgotar `/configuration` não pode consumir o orçamento de `/quotes` --
    cada rota do agente é chamada com frequência própria pelo agente Windows,
    e um balde compartilhado bloquearia a leitura de configuração por causa
    do volume de gravação de cotações, ou vice-versa.
    """
    for _ in range(60):
        assert client.get("/api/collector/configuration").status_code == 503
    assert client.get("/api/collector/configuration").status_code == 429

    resposta = client.post("/api/collector/quotes")
    assert resposta.status_code == 503, (
        "/api/collector/quotes foi bloqueada pelo orçamento de /configuration"
    )
