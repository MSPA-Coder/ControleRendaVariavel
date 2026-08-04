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

  function formatPercent(value) {
    return (
      (value >= 0 ? "+" : "") +
      value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) +
      "%"
    );
  }

  // Rebase para "evolução percentual desde o primeiro ponto disponível":
  // cada série usa sua PRÓPRIA primeira cotação como base, para comparar o
  // valor absoluto da carteira (em R$/US$) com um índice de referência
  // (preço de outro ativo, ou uma taxa de câmbio) no mesmo eixo percentual.
  // Entradas `null` (mês sem cotação do índice) permanecem `null`.
  function rebaseToPercent(values) {
    var base = null;
    return values.map(function (value) {
      if (value === null || value === undefined || !Number.isFinite(value)) return null;
      if (base === null) base = value;
      if (base === 0) return null;
      return (value / base - 1) * 100;
    });
  }

  function renderChart(container) {
    if (typeof Chart === "undefined") return;
    var months = parseData(container, "months");
    var values = parseData(container, "values").map(Number);
    var currency = container.dataset.currency || "BRL";
    var benchmarkLabel = container.dataset.benchmarkLabel;
    if (!months.length) return;

    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      container.getAttribute("aria-label") || "Evolução patrimonial mensal"
    );
    container.appendChild(canvas);

    var style = getComputedStyle(document.documentElement);
    var navy = style.getPropertyValue("--navy").trim() || "#0a2a43";
    var line = style.getPropertyValue("--line").trim() || "#c9d8e2";
    var ink = style.getPropertyValue("--ink").trim() || "#142b3c";

    if (benchmarkLabel) {
      var benchmarkRaw = parseData(container, "benchmarkValues").map(function (value) {
        return value === null ? null : Number(value);
      });
      var portfolioPct = rebaseToPercent(values);
      var benchmarkPct = rebaseToPercent(benchmarkRaw);
      new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: months,
          datasets: [
            {
              label: "Carteira",
              data: portfolioPct,
              borderColor: navy,
              backgroundColor: navy,
              pointRadius: 3,
              tension: 0.15,
              spanGaps: true,
            },
            {
              label: benchmarkLabel,
              data: benchmarkPct,
              borderColor: "#b45309",
              backgroundColor: "#b45309",
              pointRadius: 3,
              tension: 0.15,
              spanGaps: true,
              borderDash: [6, 3],
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: ink }, grid: { display: false } },
            y: { ticks: { color: ink, callback: formatPercent }, grid: { color: line } },
          },
          plugins: {
            legend: { display: true },
            tooltip: {
              callbacks: {
                label: function (item) {
                  return (
                    item.dataset.label +
                    ": " +
                    (item.parsed.y === null ? "sem dado" : formatPercent(item.parsed.y))
                  );
                },
              },
            },
          },
        },
      });
      return;
    }

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
