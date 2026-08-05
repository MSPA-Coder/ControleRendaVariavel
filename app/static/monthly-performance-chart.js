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
      // Ambas as séries já vêm em valor absoluto (R$/US$) do backend: a
      // carteira real e o valor hipotético de aplicar o mesmo capital, na
      // mesma data de cada compra, no benchmark (ver
      // app.monthly_performance.build_benchmark_shadow_series). Sem rebase
      // para %, ao contrário do gráfico de Cotações: aqui as duas curvas já
      // nascem na mesma unidade e no mesmo referencial de aportes, então
      // comparar os valores absolutos é o que faz sentido — rebasear para %
      // a carteira (que recebe aportes) contra o índice (base fixa) foi
      // justamente o problema que motivou essa mudança.
      var benchmarkValues = parseData(container, "benchmarkValues").map(function (value) {
        return value === null ? null : Number(value);
      });
      new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: months,
          datasets: [
            {
              label: "Carteira",
              data: values,
              borderColor: navy,
              backgroundColor: navy,
              pointRadius: 3,
              tension: 0.15,
              spanGaps: true,
            },
            {
              label: benchmarkLabel,
              data: benchmarkValues,
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
            legend: { display: true },
            tooltip: {
              callbacks: {
                label: function (item) {
                  return (
                    item.dataset.label +
                    ": " +
                    (item.parsed.y === null ? "sem dado" : formatCurrency(item.parsed.y, currency))
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
