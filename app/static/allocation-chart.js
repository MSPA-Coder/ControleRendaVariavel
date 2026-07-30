(function () {
  "use strict";

  var PALETTE = [
    "#214f78", "#3d7fb5", "#7fb0d6", "#a9cbe6", "#c6d4df",
    "#167245", "#4fa876", "#8ccaa6", "#e2b93b", "#d88a23",
  ];

  function parseData(container, attr) {
    try {
      return JSON.parse(container.dataset[attr] || "[]");
    } catch (err) {
      return [];
    }
  }

  function formatPercent(value) {
    return (value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%";
  }

  function renderAllocationCharts() {
    if (typeof Chart === "undefined") return;
    var containers = document.querySelectorAll(".allocation-chart");
    var style = getComputedStyle(document.documentElement);
    var ink = style.getPropertyValue("--ink").trim() || "#142536";

    containers.forEach(function (container) {
      var labels = parseData(container, "labels");
      var weights = parseData(container, "weights").map(Number);
      if (!labels.length) return;

      var canvas = document.createElement("canvas");
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", container.getAttribute("aria-label") || "Alocação por ativo");
      container.appendChild(canvas);

      new Chart(canvas.getContext("2d"), {
        type: "pie",
        data: {
          labels: labels,
          datasets: [
            {
              data: weights,
              backgroundColor: labels.map(function (_, i) {
                return PALETTE[i % PALETTE.length];
              }),
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "right", labels: { color: ink } },
            tooltip: {
              callbacks: {
                label: function (item) {
                  return item.label + ": " + formatPercent(item.parsed);
                },
              },
            },
          },
        },
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAllocationCharts);
  } else {
    renderAllocationCharts();
  }
})();
