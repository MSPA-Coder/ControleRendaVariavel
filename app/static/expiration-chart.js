(function () {
  "use strict";

  function parseData(container, attr) {
    try {
      return JSON.parse(container.dataset[attr] || "[]");
    } catch (err) {
      return [];
    }
  }

  function formatShortDate(isoDate) {
    return window.regional.formatarDataCurta(isoDate);
  }

  function formatCurrencyBRL(value) {
    return window.regional.formatarMoeda(value, "BRL", 0);
  }

  function renderExpirationChart() {
    var container = document.getElementById("expiration-chart");
    if (!container || typeof Chart === "undefined") return;
    if (container.dataset.chartInitialized === "true") return;

    var expirations = parseData(container, "expirations");
    var notional = parseData(container, "notional").map(Number);
    var unwind = parseData(container, "unwind").map(Number);
    if (!expirations.length) return;

    container.dataset.chartInitialized = "true";
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      "Gráfico de barras mostrando valor nocional e valor de desmontagem por data de vencimento"
    );
    container.appendChild(canvas);

    var style = getComputedStyle(document.documentElement);
    var navy = style.getPropertyValue("--accent").trim() || "#0b6e4f";
    var secondary = style.getPropertyValue("--muted-2").trim() || "#8a968e";
    var line = style.getPropertyValue("--line").trim() || "#c9d8e2";
    var ink = style.getPropertyValue("--ink").trim() || "#142b3c";

    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: expirations.map(formatShortDate),
        datasets: [
          {
            label: "Valor nocional",
            data: notional,
            backgroundColor: navy,
          },
          {
            label: "Desmontar (valor atual)",
            data: unwind,
            backgroundColor: secondary,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: ink }, grid: { display: false } },
          y: {
            ticks: {
              color: ink,
              callback: function (value) {
                return formatCurrencyBRL(value);
              },
            },
            grid: { color: line },
          },
        },
        plugins: {
          legend: { labels: { color: ink } },
          tooltip: {
            callbacks: {
              label: function (item) {
                return item.dataset.label + ": " + formatCurrencyBRL(item.parsed.y);
              },
            },
          },
        },
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderExpirationChart);
  } else {
    renderExpirationChart();
  }
  document.addEventListener("htmx:afterSwap", renderExpirationChart);
})();
