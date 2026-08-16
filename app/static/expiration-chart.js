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
    var parts = isoDate.split("-");
    if (parts.length !== 3) return isoDate;
    var months = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
    return parts[2] + "-" + months[parseInt(parts[1], 10) - 1] + "-" + parts[0].slice(2);
  }

  function formatCurrencyBRL(value) {
    return value.toLocaleString("pt-BR", {
      style: "currency",
      currency: "BRL",
      maximumFractionDigits: 0,
    });
  }

  function renderExpirationChart() {
    var container = document.getElementById("expiration-chart");
    if (!container || typeof Chart === "undefined") return;

    var expirations = parseData(container, "expirations");
    var notional = parseData(container, "notional").map(Number);
    var unwind = parseData(container, "unwind").map(Number);
    if (!expirations.length) return;

    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      "Gráfico de barras mostrando notional e valor de desmontagem por data de vencimento"
    );
    container.appendChild(canvas);

    var style = getComputedStyle(document.documentElement);
    var navy = style.getPropertyValue("--navy").trim() || "#0a2a43";
    var line = style.getPropertyValue("--line").trim() || "#c9d8e2";
    var ink = style.getPropertyValue("--ink").trim() || "#142b3c";

    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: expirations.map(formatShortDate),
        datasets: [
          {
            label: "Notional",
            data: notional,
            backgroundColor: navy,
          },
          {
            label: "Desmontar (valor atual)",
            data: unwind,
            backgroundColor: line,
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
})();
