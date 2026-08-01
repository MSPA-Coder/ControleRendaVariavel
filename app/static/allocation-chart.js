(function () {
  "use strict";

  var PALETTE = ["#214f78", "#3d7fb5", "#7fb0d6", "#a9cbe6", "#c6d4df", "#167245", "#4fa876", "#8ccaa6", "#e2b93b", "#d88a23"];

  function parseData(container, attr) {
    try { return JSON.parse(container.dataset[attr] || "[]"); } catch (_) { return []; }
  }
  function formatPercent(value) {
    return (value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%";
  }
  function render(container, chartType) {
    var labels = parseData(container, "labels");
    var weights = parseData(container, "weights").map(Number);
    if (!labels.length || !weights.some(function (weight) { return weight > 0; })) {
      container.textContent = "Sem dados de alocacao para exibir.";
      return;
    }
    if (typeof Chart === "undefined") {
      container.textContent = "Biblioteca do grafico nao foi carregada.";
      return;
    }
    container.replaceChildren();
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", container.getAttribute("aria-label") || "Alocacao por ativo");
    container.appendChild(canvas);
    var ink = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim() || "#142536";
    new Chart(canvas.getContext("2d"), {
      type: chartType,
      data: { labels: labels, datasets: [{ data: weights, backgroundColor: labels.map(function (_, index) { return PALETTE[index % PALETTE.length]; }) }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: chartType === "bar" ? { x: { ticks: { color: ink } }, y: { ticks: { color: ink, callback: formatPercent }, beginAtZero: true, max: 1 } } : {},
        plugins: { legend: { display: chartType !== "bar", position: "right", labels: { color: ink } }, tooltip: { callbacks: { label: function (item) { return item.label + ": " + formatPercent(item.parsed); } } } }
      }
    });
  }
  function init() {
    document.querySelectorAll(".allocation-chart").forEach(function (container) {
      var selector = container.closest(".exposure-table").querySelector("[data-allocation-chart-type]");
      render(container, selector ? selector.value : "pie");
      if (selector) selector.addEventListener("change", function () { render(container, selector.value); });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
