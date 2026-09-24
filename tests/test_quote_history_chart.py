from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "quote-history-chart.js").read_text(encoding="utf-8")


# O gráfico de comparação calcula no navegador, e nenhum teste executa JS. A
# sentinela confere só o que define a conta -- as duas séries rebaseadas na
# mesma data --; o resto do gráfico se verifica olhando a tela.


@pytest.mark.sentinela_front
def test_comparacao_rebaseia_as_duas_series_na_primeira_data_comum():
    assert "var commonLabels = Array.from(primaryCloses.keys()).filter" in SCRIPT
    assert "return benchmarkCloses.has(label);" in SCRIPT
    assert "var firstCommon = commonLabels[0];" in SCRIPT
    assert "rebaseToPercent(labels, primaryCloses, firstCommon)" in SCRIPT
    assert "rebaseToPercent(labels, benchmarkCloses, firstCommon)" in SCRIPT
