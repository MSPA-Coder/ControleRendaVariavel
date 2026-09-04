(function () {
  "use strict";
  function parseData(container, attr) { try { return JSON.parse(container.dataset[attr] || "[]"); } catch (_) { return []; } }
  function formatCurrency(value, currency) { return (currency === "USD" ? "US$ " : "R$ ") + value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function formatPercent(value) { return (value >= 0 ? "+" : "") + value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%"; }
  function periodKey(dateString, period) {
    if (period === "daily") return dateString;
    if (period === "monthly") return dateString.slice(0, 7);
    if (period === "yearly") return dateString.slice(0, 4);
    var date = new Date(dateString + "T00:00:00Z");
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
    return date.toISOString().slice(0, 10);
  }
  function aggregate(dates, prices, period) {
    var rows = new Map();
    dates.forEach(function (date, index) {
      var value = prices[index]; if (!Number.isFinite(value)) return;
      var key = periodKey(date, period), row = rows.get(key);
      if (!row) { rows.set(key, { label: key, open: value, high: value, low: value, close: value }); return; }
      row.high = Math.max(row.high, value); row.low = Math.min(row.low, value); row.close = value;
    });
    return Array.from(rows.values());
  }
  function latestQuoteDate(dates, prices) {
    var latest = null;
    dates.forEach(function (date, index) {
      if (!date || !Number.isFinite(prices[index])) return;
      if (latest === null || date > latest) latest = date;
    });
    return latest;
  }
  function zoomedSeries(dates, prices, zoom, sharedLatest) {
    if (zoom === "all") return { dates: dates, prices: prices };
    var months = { "1m": 1, "3m": 3, "6m": 6, "1y": 12 }[zoom];
    if (!months || !dates.length) return { dates: dates, prices: prices };
    var latest = sharedLatest || latestQuoteDate(dates, prices);
    if (!latest) return { dates: [], prices: [] };
    var cutoff = new Date(latest + "T00:00:00Z");
    cutoff.setUTCMonth(cutoff.getUTCMonth() - months);
    var cutoffDate = cutoff.toISOString().slice(0, 10);
    var selectedDates = [], selectedPrices = [];
    dates.forEach(function (value, index) {
      if (value >= cutoffDate && (!sharedLatest || value <= sharedLatest)) {
        selectedDates.push(value); selectedPrices.push(prices[index]);
      }
    });
    return { dates: selectedDates, prices: selectedPrices };
  }
  // Reduz uma série agregada a um mapa `label -> close`, para alinhar com
  // outra série (índice de referência) que pode ter datas diferentes.
  function closesByLabel(rows) {
    var map = new Map();
    rows.forEach(function (row) { map.set(row.label, row.close); });
    return map;
  }
  // Rebase para evolução percentual desde a primeira data em que as duas
  // séries têm cotação. A mesma âncora é essencial para que a comparação não
  // esconda uma diferença de período inicial entre os ativos.
  function rebaseToPercent(labels, closes, anchorLabel) {
    var base = closes.get(anchorLabel);
    if (base === undefined || base === 0) return labels.map(function () { return null; });
    return labels.map(function (label) {
      var value = closes.get(label);
      if (value === undefined) return null;
      return (value / base - 1) * 100;
    });
  }
  function drawCandles(container, rows, currency) {
    container.replaceChildren();
    var canvas = document.createElement("canvas"), ratio = window.devicePixelRatio || 1, width = 900, height = 300;
    canvas.width = width * ratio; canvas.height = height * ratio; canvas.style.width = "100%"; canvas.style.height = "100%";
    canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", container.getAttribute("aria-label") || "Candles de cotacoes");
    container.appendChild(canvas);
    var ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
    var values = rows.flatMap(function (row) { return [row.low, row.high]; });
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values), span = max - min || 1;
    var left = 54, right = 16, top = 16, bottom = 38, plotWidth = width - left - right, plotHeight = height - top - bottom;
    function y(value) { return top + (max - value) / span * plotHeight; }
    ctx.strokeStyle = "#c9d8e2"; ctx.fillStyle = "#5c7180"; ctx.font = "12px sans-serif";
    for (var tick = 0; tick <= 4; tick += 1) { var yy = top + plotHeight * tick / 4, value = max - span * tick / 4; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(width - right, yy); ctx.stroke(); ctx.fillText(formatCurrency(value, currency), 2, yy + 4); }
    var step = plotWidth / rows.length, body = Math.max(2, Math.min(16, step * .62));
    rows.forEach(function (row, index) {
      var x = left + step * (index + .5), rising = row.close >= row.open;
      ctx.strokeStyle = rising ? "#007f5f" : "#b42318"; ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.moveTo(x, y(row.high)); ctx.lineTo(x, y(row.low)); ctx.stroke();
      var yOpen = y(row.open), yClose = y(row.close), bodyTop = Math.min(yOpen, yClose), bodyHeight = Math.max(1, Math.abs(yClose - yOpen));
      ctx.fillRect(x - body / 2, bodyTop, body, bodyHeight);
      if (rows.length <= 18 || index % Math.ceil(rows.length / 8) === 0) { ctx.fillStyle = "#5c7180"; ctx.fillText(row.label, x - body, height - 14); }
    });
  }
  // Modo comparação: duas linhas (ticker selecionado x índice de
  // referência), ambas em evolução percentual desde o primeiro ponto em
  // comum, no mesmo eixo. Substitui o candlestick porque OHLC não tem um
  // equivalente comparável para duas séries sobrepostas.
  function drawComparison(container, primaryLabel, primaryRows, benchmarkLabel, benchmarkRows) {
    var primaryCloses = closesByLabel(primaryRows), benchmarkCloses = closesByLabel(benchmarkRows);
    var commonLabels = Array.from(primaryCloses.keys()).filter(function (label) {
      return benchmarkCloses.has(label);
    }).sort();
    // Sem uma data comum não existe uma comparação percentual válida. Limpar
    // o container também evita deixar um gráfico anterior parecendo atual.
    if (!commonLabels.length) { container.replaceChildren(); return false; }
    if (typeof Chart === "undefined") return;
    var firstCommon = commonLabels[0];
    var labels = Array.from(new Set(primaryRows.map(function (row) { return row.label; }).concat(
      benchmarkRows.map(function (row) { return row.label; })
    ))).filter(function (label) { return label >= firstCommon; }).sort();
    var primarySeries = rebaseToPercent(labels, primaryCloses, firstCommon);
    var benchmarkSeries = rebaseToPercent(labels, benchmarkCloses, firstCommon);
    container.replaceChildren();
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", container.getAttribute("aria-label") || "Comparação de evolução percentual");
    container.appendChild(canvas);
    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: primaryLabel, data: primarySeries, borderColor: "#0a2a43", backgroundColor: "#0a2a43", pointRadius: 2, tension: .15, spanGaps: true },
          { label: benchmarkLabel, data: benchmarkSeries, borderColor: "#b45309", backgroundColor: "#b45309", pointRadius: 2, tension: .15, spanGaps: true, borderDash: [6, 3] },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: true },
          tooltip: { callbacks: { label: function (item) { return item.dataset.label + ": " + (item.parsed.y === null ? "sem dado" : formatPercent(item.parsed.y)); } } },
        },
        scales: { y: { ticks: { callback: function (value) { return formatPercent(value); } } } },
      },
    });
    return true;
  }
  function render(chartType, period, zoom) {
    var container = document.getElementById("quote-history-chart"); if (!container) return;
    var primaryDates = parseData(container, "dates"), primaryPrices = parseData(container, "prices").map(Number);
    var currency = container.dataset.currency || "BRL";
    var benchmarkLabel = container.dataset.benchmarkLabel;
    if (benchmarkLabel) {
      var benchmarkDates = parseData(container, "benchmarkDates"), benchmarkPrices = parseData(container, "benchmarkPrices").map(Number);
      // Ambas as séries recebem a mesma data final para que 1m/3m/6m/1y
      // represente exatamente a mesma janela de comparação. O menor último
      // ponto evita que a janela contenha um trecho sem benchmark (ou ticker).
      var comparisonLatest = [latestQuoteDate(primaryDates, primaryPrices), latestQuoteDate(benchmarkDates, benchmarkPrices)]
        .filter(function (date) { return date !== null; })
        .reduce(function (latest, date) { return latest === null || date < latest ? date : latest; }, null);
      var primary = zoomedSeries(primaryDates, primaryPrices, zoom, comparisonLatest);
      var dates = primary.dates, prices = primary.prices;
      var primaryRows = aggregate(dates, prices, period);
      var benchmark = zoomedSeries(benchmarkDates, benchmarkPrices, zoom, comparisonLatest);
      benchmarkDates = benchmark.dates; benchmarkPrices = benchmark.prices;
      var benchmarkRows = aggregate(benchmarkDates, benchmarkPrices, period);
      if (!primaryRows.length || !benchmarkRows.length) { container.replaceChildren(); return; }
      drawComparison(container, container.dataset.label || "Selecionado", primaryRows, benchmarkLabel, benchmarkRows);
      return;
    }
    var primary = zoomedSeries(primaryDates, primaryPrices, zoom);
    var dates = primary.dates, prices = primary.prices;
    var rows = aggregate(dates, prices, period);
    if (!rows.length) return;
    if (period !== "daily") { drawCandles(container, rows, currency); return; }
    if (typeof Chart === "undefined") return;
    container.replaceChildren(); var canvas = document.createElement("canvas"); container.appendChild(canvas);
    new Chart(canvas.getContext("2d"), { type: chartType, data: { labels: rows.map(function (row) { return row.label; }), datasets: [{ data: rows.map(function (row) { return row.close; }), borderColor: "#0a2a43", backgroundColor: "#0a2a43", pointRadius: 2, tension: .15 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (item) { return formatCurrency(item.parsed.y, currency); } } } }, scales: { y: { ticks: { callback: function (value) { return formatCurrency(value, currency); } } } } } });
  }
  function init() {
    var type = document.querySelector("[data-quote-chart-type]"), period = document.querySelector("[data-quote-chart-period]"), zoom = document.querySelector("[data-quote-chart-zoom]");
    var container = document.getElementById("quote-history-chart");
    var comparing = !!(container && container.dataset.benchmarkLabel);
    function redraw() { var selected = period ? period.value : "daily"; if (type) type.disabled = comparing || selected !== "daily"; render(type ? type.value : "line", selected, zoom ? zoom.value : "all"); }
    redraw(); if (type) type.addEventListener("change", redraw); if (period) period.addEventListener("change", redraw); if (zoom) zoom.addEventListener("change", redraw);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  // HTMX troca a regiao de conteudo sem recarregar a pagina, e o Chart.js
  // vive num <canvas> que e substituido junto. Redesenhar aqui e o que
  // mantem o grafico existindo depois de um filtro. A checagem pelo
  // conteudo do fragmento trocado evita redesenhar quando a troca foi em
  // outra parte da pagina (o indicador do coletor, por exemplo), o que
  // duplicaria instancias sobre o mesmo canvas.
  document.addEventListener("htmx:afterSwap", function (event) {
    var swapped = event.target;
    if (swapped && swapped.querySelector && swapped.querySelector("#quote-history-chart")) init();
  });
})();
