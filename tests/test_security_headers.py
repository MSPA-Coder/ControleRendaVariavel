"""A Content-Security-Policy e os cabecalhos defensivos chegam ao cliente.

Este arquivo existe com o mesmo nome nos quatro projetos do mantenedor. Uma
politica que afrouxa nao quebra nada visivelmente -- a pagina continua
carregando --, entao so um teste percebe.
"""

from __future__ import annotations

import pytest

CABECALHOS_ESPERADOS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
}


@pytest.mark.parametrize(("cabecalho", "valor"), sorted(CABECALHOS_ESPERADOS.items()))
def test_cabecalho_defensivo_presente(client, cabecalho, valor):
    resposta = client.get("/login")
    assert resposta.headers.get(cabecalho) == valor


def test_permissions_policy_restringe_dispositivos(client):
    # Ate a saida do Flask-Talisman havia dois escritores para este cabecalho,
    # e o resultado dependia da ordem de registro dos `after_request` (que o
    # Flask executa invertida). Agora ha um so, vindo de `sharedauth.security`.
    # `browsing-topics` sobreviveu ao Talisman: e mais restritivo que nao
    # declarar, entao subiu para o conjunto comum dos quatro projetos.
    politica = client.get("/login").headers.get("Permissions-Policy", "")
    for recurso in (
        "camera=()",
        "microphone=()",
        "geolocation=()",
        "browsing-topics=()",
    ):
        assert recurso in politica


def test_csp_fecha_img_src_sem_data_uri(client):
    # Esta aplicacao nao tem favicon embutido nem imagem em `data:`. A folga
    # que MegaSena e ControleBancario precisam nao vale para ca -- consolidar
    # as quatro politicas nao pode virar a uniao delas.
    csp = client.get("/login").headers.get("Content-Security-Policy", "")
    assert "img-src 'self'" in csp
    assert "data:" not in csp


def test_csp_presente_e_fechada_na_propria_origem(client):
    csp = client.get("/login").headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_nao_admite_inline_nem_origem_externa(client):
    csp = client.get("/login").headers.get("Content-Security-Policy", "")
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "http://" not in csp
    assert "https://" not in csp
