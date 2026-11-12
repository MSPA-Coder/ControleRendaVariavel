"""O "hoje" do domínio é o dia de São Paulo, não o do relógio do processo.

Sem `TZ` o contêiner roda em UTC: entre 21h e 24h em São Paulo o dia UTC já é o
de amanhã, e um lançamento nasceria datado de amanhã.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

from app.core import domain

RAIZ_APP = Path(__file__).resolve().parents[1] / "app"


def _relogio_em(instante_utc: datetime):
    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return instante_utc.astimezone(tz) if tz else instante_utc

    return Relogio


def test_noite_de_sao_paulo_ainda_e_o_mesmo_dia(monkeypatch) -> None:
    # 22h30 de 11/11 em São Paulo = 01h30 de 12/11 em UTC.
    monkeypatch.setattr(
        domain, "datetime", _relogio_em(datetime(2026, 11, 12, 1, 30, tzinfo=UTC))
    )
    assert domain.hoje_no_mercado() == date(2026, 11, 11)


def test_manha_de_sao_paulo_e_o_dia_do_calendario(monkeypatch) -> None:
    monkeypatch.setattr(
        domain, "datetime", _relogio_em(datetime(2026, 11, 12, 12, 0, tzinfo=UTC))
    )
    assert domain.hoje_no_mercado() == date(2026, 11, 12)


def test_nenhum_modulo_do_app_le_o_dia_do_processo() -> None:
    padrao = re.compile(r"\b(?:date|datetime)\.today\(\)")
    achados = [
        f"{arq.relative_to(RAIZ_APP.parent)}:{n}"
        for arq in RAIZ_APP.rglob("*.py")
        for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1)
        if padrao.search(linha)
    ]
    assert not achados, f"use hoje_no_mercado() de app.core.domain: {achados}"
