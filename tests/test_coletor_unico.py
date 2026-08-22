"""Existe um mecanismo de coleta, e so um.

Historico curto, porque explica o que este arquivo protege:

- ate 18/08 havia dois modos. O **controlador local** era um servidor HTTP no
  host Windows que esta aplicacao comandava por `host.docker.internal`; o
  **agente remoto** e o processo Windows que entrega cotacoes ao servidor por
  HTTPS e nunca aceita conexao de fora.
- a migracao para o VPS tornou o primeiro redundante, mas nao o removeu. As
  duas tarefas agendadas ficaram instaladas juntas, contra a regra que o
  proprio README enunciava, e a antiga passou a falhar a cada logon.
- pior: `compose.yaml` fixava `RTD_CONTROL_URL` e o segredo estava montado no
  VPS, entao a aplicacao remota instanciava o cliente de um controlador que
  nao existe -- gastando uma resolucao de nome condenada a cada leitura de
  estado. Nao aparecia na tela, porque `settings.html` ja escolhia o bloco
  certo: a interface estava certa e o objeto por baixo, errado.
- em 2026-08-22 o modo antigo foi removido inteiro.

O que resta e uma invariante simples, e o teste existe para que ela continue
simples: **nenhum caminho de `create_app` produz um cliente HTTP de coletor.**
Quem le RTD e o processo do host; esta aplicacao so recebe.
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
    # reporta indisponivel de proposito -- e o mesmo trio que a chamada HTTP
    # ao controlador devolvia quando falhava, entao a tela nao mudou.
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


def test_nao_ha_configuracao_de_controlador_local():
    # A URL vinha fixa de `compose.yaml` e o token de um segredo montado. Os
    # dois sairam; se voltarem, e porque alguem restaurou o modo antigo sem
    # decidir qual processo le o ProfitChart.
    config = _app().config

    assert "RTD_CONTROL_URL" not in config
    assert "RTD_CONTROL_TOKEN" not in config
