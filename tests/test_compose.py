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
