(function () {
  "use strict";

  function parseData(container, attr) {
    try {
      return JSON.parse(container.dataset[attr] || "[]");
    } catch (err) {
      return [];
    }
  }

  function formatCurrency(value, currency) {
    var prefix = currency === "USD" ? "US$ " : "R$ ";
    return (
      prefix +
      value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    );
  }

  function renderChart(container) {
    if (typeof Chart === "undefined") return;
    var months = parseData(container, "months");
    var values = parseData(container, "values").map(Number);
    var currency = container.dataset.currency || "BRL";
    if (!months.length) return;

    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      container.getAttribute("aria-label") || "Evolução patrimonial mensal"
    );
    container.appendChild(canvas);

    var style = getComputedStyle(document.documentElement);
    var navy = style.getPropertyValue("--navy").trim() || "#214f78";
    var line = style.getPropertyValue("--line").trim() || "#c6d4df";
    var ink = style.getPropertyValue("--ink").trim() || "#142536";

    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: months,
        datasets: [
          {
            label: "Valor da carteira",
            data: values,
            borderColor: navy,
            backgroundColor: navy,
            pointRadius: 3,
            tension: 0.15,
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
                return formatCurrency(value, currency);
              },
            },
            grid: { color: line },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                return formatCurrency(item.parsed.y, currency);
              },
            },
          },
        },
      },
    });
  }

  function renderAllCharts() {
    document.querySelectorAll(".monthly-performance-chart").forEach(renderChart);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAllCharts);
  } else {
    renderAllCharts();
  }
})();
