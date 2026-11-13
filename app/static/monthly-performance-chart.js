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
    return window.regional.formatarMoeda(value, currency);
  }

  function formatMonth(iso) {
    return window.regional.formatarMes(iso);
  }

  // Meses sem dado entre o primeiro e o último entram como null: o gráfico
  // mostra a lacuna em vez de ligar os vizinhos por uma reta.
  function fillMissingMonths(months, series) {
    var index = new Map();
    months.forEach(function (month, i) { index.set(month.slice(0, 7), i); });
    var first = months[0].slice(0, 7), last = months[months.length - 1].slice(0, 7);
    var year = parseInt(first.slice(0, 4), 10), month = parseInt(first.slice(5, 7), 10);
    var labels = [], filled = series.map(function () { return []; });
    for (var guard = 0; guard < 1200; guard += 1) {
      var key = String(year).padStart(4, "0") + "-" + String(month).padStart(2, "0");
      var original = index.get(key);
      labels.push(original === undefined ? key + "-01" : months[original]);
      series.forEach(function (values, s) {
        var value = original === undefined ? null : values[original];
        filled[s].push(value === undefined ? null : value);
      });
      if (key >= last) break;
      month += 1;
      if (month > 12) { month = 1; year += 1; }
    }
    return { labels: labels, series: filled };
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
      container.getAttribute("aria-label") || "Evolução da exposição líquida mensal"
    );
    container.appendChild(canvas);

    var style = getComputedStyle(document.documentElement);
    var navy = style.getPropertyValue("--accent").trim() || "#0b6e4f";
    var line = style.getPropertyValue("--line").trim() || "#c9d8e2";
    var ink = style.getPropertyValue("--ink").trim() || "#142b3c";

    var xScale = {
      ticks: {
        color: ink,
        callback: function (value) { return formatMonth(this.getLabelForValue(value)); },
      },
      grid: { display: false },
    };
    var titleCallback = function (items) { return items.length ? formatMonth(items[0].label) : ""; };

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
      var filled = fillMissingMonths(months, [values, benchmarkValues]);
      new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: filled.labels,
          datasets: [
            {
              label: "Exposição líquida",
              data: filled.series[0],
              borderColor: navy,
              backgroundColor: navy,
              pointRadius: 3,
              tension: 0.15,
              spanGaps: false,
            },
            {
              label: benchmarkLabel,
              data: filled.series[1],
              borderColor: "#b45309",
              backgroundColor: "#b45309",
              pointRadius: 3,
              tension: 0.15,
              spanGaps: false,
              borderDash: [6, 3],
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: xScale,
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
                title: titleCallback,
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

    var filledSingle = fillMissingMonths(months, [values]);
    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: filledSingle.labels,
        datasets: [
          {
            label: "Exposição líquida",
            data: filledSingle.series[0],
            borderColor: navy,
            backgroundColor: navy,
            pointRadius: 3,
            tension: 0.15,
            spanGaps: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: xScale,
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
              title: titleCallback,
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

  // HTMX troca a regiao de conteudo sem recarregar a pagina, e o Chart.js
  // vive num <canvas> que e substituido junto. Redesenhar aqui e o que
  // mantem o grafico existindo depois de um filtro. A checagem pelo
  // conteudo do fragmento trocado evita redesenhar quando a troca foi em
  // outra parte da pagina (o indicador do coletor, por exemplo), o que
  // duplicaria instancias sobre o mesmo canvas.
  document.addEventListener("htmx:afterSwap", function (event) {
    var swapped = event.target;
    if (swapped && swapped.querySelector && swapped.querySelector(".monthly-performance-chart")) renderAllCharts();
  });
})();
