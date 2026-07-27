(() => {
  const filterForm = document.querySelector("[data-auto-submit]");
  if (filterForm) {
    filterForm.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => filterForm.requestSubmit());
    });
  }

  const portfolio = document.querySelector("[data-refresh-seconds]");
  if (!portfolio) return;
  const refreshApi = portfolio.dataset.refreshApi || "/api/portfolio";

  const toMilliseconds = (seconds) => {
    const parsed = Number.parseInt(seconds, 10);
    return Number.isInteger(parsed) && parsed >= 1 ? parsed * 1000 : 2000;
  };

  let intervalMs = toMilliseconds(portfolio.dataset.refreshSeconds);

  const scheduleRefresh = () => window.setTimeout(refreshPortfolio, intervalMs);

  async function refreshPortfolio() {
    if (document.hidden) {
      scheduleRefresh();
      return;
    }
    try {
      const response = await fetch(`${refreshApi}${window.location.search}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (response.ok) {
        const payload = await response.json();
        intervalMs = toMilliseconds(payload.poll_interval_seconds);
        window.location.reload();
        return;
      }
    } catch {
      // A badge already communicates stale/error state after the next successful response.
    }
    scheduleRefresh();
  }

  scheduleRefresh();
})();
