"""Garante que o agente Windows seja o único mecanismo de coleta RTD.

Nenhum caminho de ``create_app`` produz um cliente HTTP de coletor. O processo
do host lê o RTD e entrega as cotações; a aplicação apenas as recebe.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.rtd_service import RtdServiceManager

BASE = {
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://test:test@localhost:5432/test",
    "TESTING": True,
}


def _app(**extra):
    return create_app({**BASE, **extra})


@pytest.mark.parametrize("remoto", [True, False])
def test_o_servico_de_coletor_nunca_e_um_cliente_http(remoto):
    servico = _app(REMOTE_COLLECTOR_ENABLED=remoto).extensions["rtd_service"]

    assert isinstance(servico, RtdServiceManager), (
        "voltou a existir um segundo tipo de servico de coletor: se for outro "
        "modo de coleta, decida antes qual dos dois le o ProfitChart"
    )


def test_o_servico_existe_sempre():
    # `rtd_service()` em `routes/helpers.py` le direto de `extensions`; nao
    # registrar nada levantaria KeyError na tela de Configuracoes em vez de
    # mostrar o coletor como indisponivel.
    for remoto in (True, False):
        assert _app(REMOTE_COLLECTOR_ENABLED=remoto).extensions["rtd_service"] is not None


def test_com_agente_remoto_o_estado_e_indisponivel():
    # Com o agente ligado, quem coleta e o processo do host. A aplicacao
    # reporta indisponivel ate receber o pulso do agente.
    servico = _app(REMOTE_COLLECTOR_ENABLED=True).extensions["rtd_service"]

    assert servico.available is False
    assert servico.is_running is False
    assert servico.status == "unavailable"


def test_a_suite_nao_supervisiona_o_profitchart():
    # `available` do gerenciador vale `sys.platform == "win32"`. Sem esta
    # guarda, cada `create_app()` da suite subia no Windows uma thread sondando
    # o ProfitChart por `powershell.exe` a cada 2 segundos: a suite ficava
    # inexecutavel na maquina de quem desenvolve e verde no CI, que e Linux.
    # Verde onde ninguem olha e quebrado onde se trabalha e o pior resultado
    # possivel para um teste.
    servico = _app(REMOTE_COLLECTOR_ENABLED=False).extensions["rtd_service"]

    assert servico._background_supervision is False


def test_a_fabrica_local_nao_inicia_supervisor_por_worker():
    # A configuração de produção não deve mudar o ciclo de vida: cada worker
    # criado pelo Gunicorn recebe um serviço inerte até um start() explícito.
    servico = create_app(
        {
            **BASE,
            "TESTING": False,
            "SECRET_KEY": "test-secret",
            "REMOTE_COLLECTOR_ENABLED": False,
        }
    ).extensions["rtd_service"]

    assert servico._background_supervision is False
    assert servico._supervisor_thread is None


def test_nao_ha_configuracao_de_conexao_ativa_com_o_host():
    # A aplicacao nao se conecta ao host Windows; apenas o agente inicia
    # conexoes e entrega as cotacoes ao servidor.
    config = _app().config

    assert "RTD_CONTROL_URL" not in config
    assert "RTD_CONTROL_TOKEN" not in config
