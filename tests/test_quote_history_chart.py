from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "quote-history-chart.js").read_text(encoding="utf-8")


def test_comparacao_rebaseia_as_duas_series_na_primeira_data_comum():
    assert "var commonLabels = Array.from(primaryCloses.keys()).filter" in SCRIPT
    assert "return benchmarkCloses.has(label);" in SCRIPT
    assert "var firstCommon = commonLabels[0];" in SCRIPT
    assert "rebaseToPercent(labels, primaryCloses, firstCommon)" in SCRIPT
    assert "rebaseToPercent(labels, benchmarkCloses, firstCommon)" in SCRIPT


def test_comparacao_descarta_labels_anteriores_e_nao_desenha_sem_intersecao():
    assert ").filter(function (label) { return label >= firstCommon; }).sort();" in SCRIPT
    assert "if (!commonLabels.length) { container.replaceChildren(); return false; }" in SCRIPT


def test_zoom_da_comparacao_usa_a_mesma_data_final_nas_duas_series():
    assert "var comparisonLatest = [latestQuoteDate(primaryDates, primaryPrices), latestQuoteDate(benchmarkDates, benchmarkPrices)]" in SCRIPT
    assert "zoomedSeries(primaryDates, primaryPrices, zoom, comparisonLatest)" in SCRIPT
    assert "zoomedSeries(benchmarkDates, benchmarkPrices, zoom, comparisonLatest)" in SCRIPT
    assert "value >= cutoffDate && (!sharedLatest || value <= sharedLatest)" in SCRIPT


def test_cada_aporte_de_posicao_aberta_vira_referencia_tracejada():
    template = (
        ROOT / "app" / "templates" / "partials" / "quotes_results.html"
    ).read_text(encoding="utf-8")

    assert 'parseData(container, "openPositionLines")' in SCRIPT
    assert "borderDash: [7, 4]" in SCRIPT
    assert "label >= line.openedOn ? entryPrice : null" in SCRIPT
    assert "data-open-position-lines" in template
