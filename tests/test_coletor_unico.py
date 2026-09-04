"""Garante que a aplicação web não seja um mecanismo de coleta.

Quem lê o ProfitChart é a tarefa do Windows; a aplicação recebe cotações,
mostra estado e grava a pausa. Nenhum caminho de ``create_app`` pode iniciar,
supervisionar ou encerrar um coletor -- com Gunicorn criando uma fábrica por
worker, isso significaria um candidato a coletor por worker disputando a
mesma sessão COM.
"""

from __future__ import annotations

import threading

import pytest
from conftest import CONFIG_DE_TESTE

from app import create_app

# Mesma configuração do conftest, `creator` incluso: nenhuma app montada nos
# testes deve ser capaz de abrir socket para o banco.
BASE = dict(CONFIG_DE_TESTE)


def _app(**extra):
    return create_app({**BASE, **extra})


@pytest.mark.parametrize("remoto", [True, False])
def test_a_aplicacao_nao_registra_servico_de_coletor(remoto):
    extensoes = _app(REMOTE_COLLECTOR_ENABLED=remoto).extensions

    assert "rtd_service" not in extensoes, (
        "a aplicacao web voltou a ser dona de um coletor: se for mesmo "
        "necessario, decida antes quem encerra o processo da tarefa do Windows"
    )


@pytest.mark.parametrize("remoto", [True, False])
def test_criar_a_aplicacao_nao_sobe_thread_de_supervisao(remoto):
    # Esta guarda ja custou caro uma vez: cada `create_app()` da suite subia no
    # Windows uma thread sondando o ProfitChart por `powershell.exe` a cada
    # 2 segundos. A suite ficava inexecutavel na maquina de quem desenvolve e
    # verde no CI, que e Linux -- o pior resultado possivel para um teste.
    antes = {thread.name for thread in threading.enumerate()}

    _app(REMOTE_COLLECTOR_ENABLED=remoto)

    novas = {thread.name for thread in threading.enumerate()} - antes
    assert novas == set(), f"create_app iniciou threads: {novas}"


def test_a_fabrica_de_producao_tambem_nao_inicia_coletor():
    # A configuração de produção não pode mudar o ciclo de vida.
    extensoes = create_app(
        {
            **BASE,
            "TESTING": False,
            "SECRET_KEY": "test-secret",
            "REMOTE_COLLECTOR_ENABLED": False,
        }
    ).extensions

    assert "rtd_service" not in extensoes


def test_nao_ha_configuracao_de_conexao_ativa_com_o_host():
    # A aplicacao nao se conecta ao host Windows; apenas o coletor inicia
    # conexoes e entrega as cotacoes ao servidor.
    config = _app().config

    assert "RTD_CONTROL_URL" not in config
    assert "RTD_CONTROL_TOKEN" not in config
