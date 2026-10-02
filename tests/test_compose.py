"""Propriedades da implantação declaradas no `compose.yaml`.

O arquivo é carregado como YAML e conferido pelo valor (docs/TESTES.md, T4):
reordenar chaves ou reescrever comentários não reprova nada aqui.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.domain import MARKET_TIMEZONE

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yaml"


def _servicos() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_o_app_roda_no_fuso_do_mercado() -> None:
    """Sem `TZ`, o contêiner roda em UTC e `date.today()` devolve o dia
    seguinte entre 21h e 24h de São Paulo: um ajuste de posição feito na noite
    do último dia do mês seria gravado no mês seguinte. O fuso declarado tem de
    ser o mesmo que o app usa para o dia de mercado."""
    for nome in ("web", "migrate"):
        ambiente = _servicos()[nome]["environment"]
        assert ambiente.get("TZ") == MARKET_TIMEZONE.key, nome


def test_tokens_patrimoniais_separados_no_runtime_e_no_quality() -> None:
    servicos = _servicos()
    web = servicos["web"]
    quality = servicos["quality"]

    assert web["environment"]["PATRIMONIO_TOKEN_FILE"] == "/run/secrets/patrimonio_token"
    assert web["environment"]["PATRIMONIO_INTEGRATION_TOKEN_FILE"] == (
        "/run/secrets/patrimonio_integration_token"
    )
    assert "patrimonio_token" in web["secrets"]
    assert "patrimonio_integration_token" in web["secrets"]
    assert quality["environment"]["PATRIMONIO_INTEGRATION_TOKEN_FILE"] == (
        "/run/secrets/patrimonio_integration_token_quality"
    )
    assert "patrimonio_integration_token_quality" in quality["secrets"]


def test_todo_servico_roda_sem_privilegios_extras() -> None:
    """Nenhum contêiner ganha escrita na raiz, capability ou escalada.

    O banco pode gravar PGDATA pelo volume, e só; o resto grava em tmpfs. Um
    serviço novo sem endurecimento aparece pelo nome.
    """
    fora = {
        nome: campo
        for nome, servico in _servicos().items()
        for campo, ok in (
            ("read_only", servico.get("read_only") is True),
            ("cap_drop", "ALL" in servico.get("cap_drop", [])),
            ("security_opt", "no-new-privileges:true" in servico.get("security_opt", [])),
            ("pids_limit", bool(servico.get("pids_limit"))),
        )
        if not ok
    }
    assert not fora, f"serviços sem endurecimento: {fora}"
