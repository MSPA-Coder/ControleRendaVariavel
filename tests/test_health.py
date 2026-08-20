"""A sonda de saude depende do banco e diz isso em vez de estourar.

A suite nao tem banco (ver `conftest.py`), o que aqui e vantagem: e exatamente
o cenario "banco inalcancavel" que a rota precisa reportar.

Antes de a rota vir de `sharedauth.health`, a excecao do banco subia e virava
500 -- com traceback no log a cada sonda, de 60 em 60 segundos. Para o Docker
o efeito era o mesmo, mas 503 e a resposta correta para indisponibilidade
temporaria.
"""

from __future__ import annotations


def test_health_reporta_erro_quando_o_banco_esta_inalcancavel(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    resposta = app.test_client().get("/health")
    assert resposta.status_code == 503
    assert resposta.get_json() == {
        "servico": "controle-renda-variavel",
        "status": "erro",
    }


def test_health_nao_vaza_detalhe_de_infraestrutura(app):
    # A resposta de erro nao pode virar reconhecimento gratuito: nada de host,
    # porta, nome de banco ou traceback.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    corpo = app.test_client().get("/health").get_data(as_text=True).lower()
    for termo in ("postgres", "psycopg", "localhost", "5432", "traceback", "password"):
        assert termo not in corpo
