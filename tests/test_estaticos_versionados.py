"""Estáticos com a versão na URL ficam um ano em cache; os demais revalidam.

Ver `app/core/estaticos.py` para o porquê.
"""
from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

import pytest
from flask import url_for

from app.core.estaticos import UM_ANO_EM_SEGUNDOS

ROOT = Path(__file__).resolve().parents[1]


def _versao_de(arquivo: Path) -> str:
    return sha256(arquivo.read_bytes()).hexdigest()[:12]


def test_url_do_estatico_carrega_o_hash_do_conteudo(app):
    with app.test_request_context():
        url = url_for("static", filename="app.css")
    assert url == f"/static/app.css?v={_versao_de(ROOT / 'app' / 'static' / 'app.css')}"


def test_estaticos_de_blueprint_tambem_sao_versionados(app):
    with app.test_request_context():
        url = url_for("sharedauth_ui.static", filename="sharedauth-ui.css")
    assert re.search(r"\?v=[0-9a-f]{12}$", url)


def test_arquivo_inexistente_fica_sem_versao(app):
    with app.test_request_context():
        assert url_for("static", filename="nao-existe.css") == "/static/nao-existe.css"


def test_versao_certa_ganha_cache_de_um_ano(app, client):
    with app.test_request_context():
        url = url_for("static", filename="app.js")
    resposta = client.get(url)
    assert resposta.status_code == 200
    cache = resposta.cache_control
    assert cache.max_age == UM_ANO_EM_SEGUNDOS
    assert cache.immutable
    assert cache.public
    assert not cache.no_cache


@pytest.mark.parametrize("consulta", ["", "?v=000000000000"])
def test_sem_versao_ou_com_versao_antiga_continua_revalidando(client, consulta):
    # Uma página de antes do deploy pede a URL antiga e recebe o conteúdo
    # novo: guardá-lo por um ano sob o endereço antigo seria errado.
    resposta = client.get(f"/static/app.js{consulta}")
    assert resposta.status_code == 200
    assert resposta.cache_control.max_age is None
    assert resposta.cache_control.no_cache


def test_pagina_de_login_referencia_estaticos_versionados(client):
    pagina = client.get("/login").get_data(as_text=True)
    referencias = re.findall(r'(?:href|src)="(/[^"]*static[^"]*)"', pagina)
    assert referencias
    assert all("?v=" in referencia for referencia in referencias)
