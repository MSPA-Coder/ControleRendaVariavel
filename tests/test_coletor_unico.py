"""Um coletor de cada vez, e a regra e do codigo.

O README ja mandava: "Nao mantenha simultaneamente o coletor local
(`rtd-host.ps1`) e o agente remoto sobre o mesmo ProfitChart... apenas um deles
deve fazer a leitura RTD de cada vez."

Ate 2026-08-22 isso era so uma frase, e o VPS desobedecia sem sintoma:

- `compose.yaml` fixa `RTD_CONTROL_URL: http://host.docker.internal:8765` --
  um conceito de Docker Desktop no Windows, que nao resolve num Linux;
- o segredo `rtd_control_token` esta montado no VPS, entao o token nao e vazio;
- com as duas condicoes verdadeiras, a aplicacao remota instanciava
  `RemoteRtdService` e gastava uma resolucao de nome condenada a cada leitura
  de estado do coletor.

Nao aparecia na tela porque `settings.html` ja escolhe o bloco certo por
`remote_collector_enabled`: a interface estava certa e o objeto por baixo,
errado. E o formato de defeito que este conjunto de projetos vem caçando --
a protecao existe no documento e nao no codigo.

Estes testes fecham a regra nos dois sentidos. So o primeiro nao bastaria: um
`rtd_service` sempre indisponivel passaria nele e teria matado o modo local.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.rtd_service import RemoteRtdService

CONTROLADOR = {
    "RTD_CONTROL_URL": "http://host.docker.internal:8765",
    "RTD_CONTROL_TOKEN": "token-de-teste-nao-usado-em-execucao-real",
}

BASE = {
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://test:test@localhost:5432/test",
    "TESTING": True,
}


def _app(**extra):
    return create_app({**BASE, **extra})


def test_com_agente_remoto_nao_fala_com_controlador_local():
    app = _app(REMOTE_COLLECTOR_ENABLED=True, **CONTROLADOR)

    assert not isinstance(app.extensions["rtd_service"], RemoteRtdService)


def test_com_agente_remoto_o_estado_e_indisponivel():
    # Mesmo trio que a chamada HTTP falhando ja devolvia. A mudanca tira a
    # chamada, nao muda o que a aplicacao reporta.
    app = _app(REMOTE_COLLECTOR_ENABLED=True, **CONTROLADOR)
    servico = app.extensions["rtd_service"]

    assert servico.available is False
    assert servico.is_running is False
    assert servico.status == "unavailable"


def test_sem_agente_remoto_o_controlador_local_continua_valendo():
    # Controle positivo: o modo local nao foi removido, so deixou de coexistir.
    app = _app(REMOTE_COLLECTOR_ENABLED=False, **CONTROLADOR)

    assert isinstance(app.extensions["rtd_service"], RemoteRtdService)


@pytest.mark.parametrize(
    "controlador",
    [
        {"RTD_CONTROL_URL": "", "RTD_CONTROL_TOKEN": ""},
        {"RTD_CONTROL_URL": "http://host.docker.internal:8765", "RTD_CONTROL_TOKEN": ""},
        {"RTD_CONTROL_URL": "", "RTD_CONTROL_TOKEN": "token-de-teste"},
    ],
)
def test_sem_controlador_configurado_usa_o_gerenciador_local(controlador):
    app = _app(REMOTE_COLLECTOR_ENABLED=False, **controlador)

    assert not isinstance(app.extensions["rtd_service"], RemoteRtdService)


def test_o_servico_existe_sempre():
    # `rtd_service()` em `routes/helpers.py` le direto de `extensions`; nao
    # registrar nada levantaria KeyError na tela de Configuracoes em vez de
    # mostrar o coletor como indisponivel.
    for remoto in (True, False):
        app = _app(REMOTE_COLLECTOR_ENABLED=remoto, **CONTROLADOR)
        assert app.extensions["rtd_service"] is not None
