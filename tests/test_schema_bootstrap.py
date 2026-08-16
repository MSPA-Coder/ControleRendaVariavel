"""O grafo de migracoes esta integro e tem uma cabeca so.

Nao aplica migracoes: isso e verificacao manual obrigatoria contra PostgreSQL
vazio, como a base registra. O que este arquivo protege e a classe de erro que
a consolidacao de baselines introduz e que o bootstrap manual so revela tarde
-- duas cabecas, revisao duplicada, elo quebrado -- e que aqui custa
milissegundos.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSOES = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _revisoes() -> dict[str, str | None]:
    """Mapeia revision -> down_revision lendo os arquivos, sem importar Alembic."""
    encontradas: dict[str, str | None] = {}
    for arquivo in VERSOES.glob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        revisao = re.search(r"^revision\s*=\s*[\"']([^\"']+)[\"']", texto, re.MULTILINE)
        anterior = re.search(
            r"^down_revision\s*=\s*(?:[\"']([^\"']+)[\"']|None)", texto, re.MULTILINE
        )
        assert revisao, f"{arquivo.name} nao declara `revision`"
        assert anterior, f"{arquivo.name} nao declara `down_revision`"
        chave = revisao.group(1)
        assert chave not in encontradas, f"revision duplicada: {chave}"
        encontradas[chave] = anterior.group(1)
    return encontradas


def test_existem_migracoes():
    assert _revisoes(), "nenhuma migracao encontrada em migrations/versions"


def test_uma_unica_base():
    revisoes = _revisoes()
    bases = [rev for rev, anterior in revisoes.items() if anterior is None]
    assert len(bases) == 1, f"esperava uma baseline, encontrou {bases}"


def test_uma_unica_cabeca():
    revisoes = _revisoes()
    referenciadas = {anterior for anterior in revisoes.values() if anterior}
    cabecas = sorted(set(revisoes) - referenciadas)
    assert len(cabecas) == 1, f"esperava uma cabeca, encontrou {cabecas}"


def test_todo_elo_aponta_para_revisao_existente():
    revisoes = _revisoes()
    for revisao, anterior in revisoes.items():
        if anterior is not None:
            assert anterior in revisoes, f"{revisao} aponta para {anterior}, que nao existe"


def test_cadeia_alcanca_a_base_sem_ciclo():
    revisoes = _revisoes()
    referenciadas = {anterior for anterior in revisoes.values() if anterior}
    cabeca = next(iter(set(revisoes) - referenciadas))
    visitadas: set[str] = set()
    atual: str | None = cabeca
    while atual is not None:
        if atual in visitadas:
            pytest.fail(f"ciclo no grafo de migracoes em {atual}")
        visitadas.add(atual)
        atual = revisoes[atual]
    assert visitadas == set(revisoes), "ha revisoes fora da cadeia principal"
