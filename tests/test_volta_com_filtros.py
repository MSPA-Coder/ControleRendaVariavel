"""A escrita volta para a tela de origem com a moeda, a carteira e a corretora.

Auditoria de 09/10/2026 (CP1/CP2): salvar, excluir ou encerrar terminava num
redirect sem query, e quem olhava as posições em dólar voltava às de reais.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlsplit

import pytest
from flask import Flask, redirect

from app.core.volta import endereco_de_volta, instalar_volta_com_filtros

PAGINAS = {
    "/": "portfolio.index",
    "/transactions": "portfolio.transactions",
    "/dividends": "portfolio.dividends",
    "/tables/brokers": "portfolio.table_brokers",
    "/positions/new": "portfolio.new_position",
    "/login": "auth.login",
}


@pytest.fixture
def mini_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    for caminho, endpoint in PAGINAS.items():
        app.add_url_rule(caminho, endpoint=endpoint, view_func=lambda: "ok", methods=["GET"])

    def salvar_para(destino: str):
        return lambda: redirect(destino)

    app.add_url_rule("/salvar-lista", "salvar_lista", salvar_para("/"), methods=["POST"])
    app.add_url_rule("/salvar-transacoes", "salvar_transacoes", salvar_para("/transactions"), methods=["POST"])
    app.add_url_rule("/salvar-corretora", "salvar_corretora", salvar_para("/tables/brokers"), methods=["POST"])
    app.add_url_rule("/salvar-expandido", "salvar_expandido", salvar_para("/?expanded=7"), methods=["POST"])
    app.add_url_rule("/salvar-login", "salvar_login", salvar_para("/login"), methods=["POST"])
    app.add_url_rule("/erro-form", "erro_form", salvar_para("/positions/new"), methods=["POST"])
    instalar_volta_com_filtros(app)
    return app


def _local(resposta) -> tuple[str, dict[str, str]]:
    partes = urlsplit(resposta.headers["Location"])
    return partes.path, dict(parse_qsl(partes.query, keep_blank_values=True))


def test_volta_pelo_campo_do_formulario_leva_moeda_carteira_e_corretora(mini_app: Flask) -> None:
    resposta = mini_app.test_client().post(
        "/salvar-lista",
        data={"volta": "/?currency=USD&portfolio_id=3&broker=XP&result_mode=mais&return_days=30"},
    )

    caminho, query = _local(resposta)
    assert caminho == "/"
    # Mesma tela: o que ela mostrava volta, menos o estado de interface.
    assert query == {"currency": "USD", "portfolio_id": "3", "broker": "XP", "result_mode": "mais"}


def test_em_outra_tela_so_voltam_os_filtros_globais(mini_app: Flask) -> None:
    resposta = mini_app.test_client().post(
        "/salvar-transacoes",
        data={"volta": "/?currency=ALL&portfolio_id=3&broker=XP&result_mode=mais"},
    )

    caminho, query = _local(resposta)
    assert caminho == "/transactions"
    assert query == {"currency": "ALL", "portfolio_id": "3", "broker": "XP"}


def test_tabelas_cadastrais_so_recebem_a_moeda(mini_app: Flask) -> None:
    """Ali `portfolio_id` é a carteira em edição, não um filtro."""
    resposta = mini_app.test_client().post(
        "/salvar-corretora", data={"volta": "/?currency=USD&portfolio_id=3&broker=XP"}
    )

    assert _local(resposta) == ("/tables/brokers", {"currency": "USD"})


def test_moeda_padrao_e_valor_vazio_ficam_fora_da_url(mini_app: Flask) -> None:
    resposta = mini_app.test_client().post(
        "/salvar-lista", data={"volta": "/?currency=BRL&portfolio_id=&broker="}
    )

    assert resposta.headers["Location"] == "/"


def test_o_que_a_rota_ja_escolheu_nao_e_sobrescrito(mini_app: Flask) -> None:
    resposta = mini_app.test_client().post(
        "/salvar-expandido", data={"volta": "/?currency=USD&expanded=2"}
    )

    assert _local(resposta) == ("/", {"expanded": "7", "currency": "USD"})


def test_formulario_de_pagina_volta_para_a_lista_de_onde_foi_aberto(mini_app: Flask) -> None:
    lista = "/?currency=USD&portfolio_id=3"
    pagina = f"/positions/new?volta={quote(lista, safe='')}"
    resposta = mini_app.test_client().post("/salvar-lista", data={"volta": pagina})

    assert _local(resposta) == ("/", {"currency": "USD", "portfolio_id": "3"})


def test_erro_de_validacao_no_formulario_mantem_a_volta(mini_app: Flask) -> None:
    lista = "/?currency=USD"
    pagina = f"/positions/new?volta={quote(lista, safe='')}"
    resposta = mini_app.test_client().post("/erro-form", data={"volta": pagina})

    caminho, query = _local(resposta)
    assert caminho == "/positions/new"
    assert query == {"volta": lista}


def test_sem_campo_usa_hx_current_url_e_depois_o_referer(mini_app: Flask) -> None:
    cliente = mini_app.test_client()

    htmx = cliente.post("/salvar-lista", headers={"HX-Current-URL": "http://localhost/?currency=USD"})
    referer = cliente.post("/salvar-lista", headers={"Referer": "http://localhost/?currency=ALL"})
    de_fora = cliente.post("/salvar-lista", headers={"Referer": "https://outro.exemplo/?currency=USD"})

    assert _local(htmx) == ("/", {"currency": "USD"})
    assert _local(referer) == ("/", {"currency": "ALL"})
    assert de_fora.headers["Location"] == "/"


def test_login_nao_recebe_filtros(mini_app: Flask) -> None:
    resposta = mini_app.test_client().post("/salvar-login", data={"volta": "/?currency=USD"})

    assert resposta.headers["Location"] == "/login"


@pytest.mark.parametrize(
    "tentativa",
    [
        "/?currency=EUR&portfolio_id=3",
        "//outro.exemplo/?currency=USD",
        "https://outro.exemplo/?currency=USD",
        "/\\outro.exemplo?currency=USD",
    ],
)
def test_origem_invalida_nunca_muda_o_destino(mini_app: Flask, tentativa: str) -> None:
    resposta = mini_app.test_client().post("/salvar-lista", data={"volta": tentativa})

    caminho, query = _local(resposta)
    assert caminho == "/"
    assert "currency" not in query or query["currency"] in {"USD", "ALL"}


def test_get_nao_e_alterado(mini_app: Flask) -> None:
    resposta = mini_app.test_client().get("/", headers={"Referer": "http://localhost/?currency=USD"})

    assert resposta.status_code == 200


def test_endereco_de_volta_so_aceita_caminho_local() -> None:
    assert endereco_de_volta("/transactions?currency=USD") == ("/transactions", [("currency", "USD")])
    assert endereco_de_volta("https://x.example/") is None
    assert endereco_de_volta("//x.example/") is None
    assert endereco_de_volta("transactions") is None
    assert endereco_de_volta("") is None
    assert endereco_de_volta(None) is None
