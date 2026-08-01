(function () {
  "use strict";
  function parseData(container, attr) { try { return JSON.parse(container.dataset[attr] || "[]"); } catch (_) { return []; } }
  function formatCurrency(value, currency) { return (currency === "USD" ? "US$ " : "R$ ") + value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
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
    ctx.strokeStyle = "#c6d4df"; ctx.fillStyle = "#66788a"; ctx.font = "12px sans-serif";
    for (var tick = 0; tick <= 4; tick += 1) { var yy = top + plotHeight * tick / 4, value = max - span * tick / 4; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(width - right, yy); ctx.stroke(); ctx.fillText(formatCurrency(value, currency), 2, yy + 4); }
    var step = plotWidth / rows.length, body = Math.max(2, Math.min(16, step * .62));
    rows.forEach(function (row, index) {
      var x = left + step * (index + .5), rising = row.close >= row.open;
      ctx.strokeStyle = rising ? "#167245" : "#d82323"; ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.moveTo(x, y(row.high)); ctx.lineTo(x, y(row.low)); ctx.stroke();
      var yOpen = y(row.open), yClose = y(row.close), bodyTop = Math.min(yOpen, yClose), bodyHeight = Math.max(1, Math.abs(yClose - yOpen));
      ctx.fillRect(x - body / 2, bodyTop, body, bodyHeight);
      if (rows.length <= 18 || index % Math.ceil(rows.length / 8) === 0) { ctx.fillStyle = "#66788a"; ctx.fillText(row.label, x - body, height - 14); }
    });
  }
  function render(chartType, period) {
    var container = document.getElementById("quote-history-chart"); if (!container) return;
    var dates = parseData(container, "dates"), prices = parseData(container, "prices").map(Number), rows = aggregate(dates, prices, period);
    if (!rows.length) return;
    var currency = container.dataset.currency || "BRL";
    if (period !== "daily") { drawCandles(container, rows, currency); return; }
    if (typeof Chart === "undefined") return;
    container.replaceChildren(); var canvas = document.createElement("canvas"); container.appendChild(canvas);
    new Chart(canvas.getContext("2d"), { type: chartType, data: { labels: rows.map(function (row) { return row.label; }), datasets: [{ data: rows.map(function (row) { return row.close; }), borderColor: "#214f78", backgroundColor: "#214f78", pointRadius: 2, tension: .15 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (item) { return formatCurrency(item.parsed.y, currency); } } } }, scales: { y: { ticks: { callback: function (value) { return formatCurrency(value, currency); } } } } } });
  }
  function init() {
    var type = document.querySelector("[data-quote-chart-type]"), period = document.querySelector("[data-quote-chart-period]");
    function redraw() { var selected = period ? period.value : "daily"; if (type) type.disabled = selected !== "daily"; render(type ? type.value : "line", selected); }
    redraw(); if (type) type.addEventListener("change", redraw); if (period) period.addEventListener("change", redraw);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
