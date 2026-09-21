"""Contrato analítico v3 publicado para o shell de patrimônio.

Estes testes são deliberadamente pequenos e sem banco: autorização, método
HTTP e o envelope de capacidades não dependem de dados financeiros. Os testes
de conteúdo do contrato v2 continuam no arquivo histórico; a camada com banco
fica reservada para fixtures que tenham um cenário financeiro explícito.
"""

import pytest

TOKEN = "token-de-teste-com-mais-de-trinta-e-dois-caracteres"
ROTAS = (
    "/patrimonio/v3/income",
    "/patrimonio/v3/performance",
    "/patrimonio/v3/events",
)


@pytest.mark.parametrize("rota", ROTAS)
def test_recursos_analiticos_exigem_bearer(client, app, rota):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    assert client.get(rota).status_code == 401


@pytest.mark.parametrize("rota", ROTAS)
def test_recursos_analiticos_sao_get_only(client, app, rota):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    assert client.post(rota, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 405


def test_metadata_declara_as_series_v3_sem_expor_dados(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN

    resposta = client.get(
        "/patrimonio/v3/metadata", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["contrato"] == "patrimonio/v3"
    assert corpo["capacidades"]["escrita"] is False
    assert corpo["capacidades"]["income"] is True
    assert corpo["capacidades"]["performance"] is True
    assert corpo["capacidades"]["events"] is True
    assert "itens" not in corpo


@pytest.mark.parametrize("rota", ROTAS)
def test_recurso_analitico_sem_publicacao_configurada_e_503(client, app, rota):
    app.config["PATRIMONIO_TOKEN"] = ""
    assert client.get(rota).status_code == 503
