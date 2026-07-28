(() => {
  const filterForm = document.querySelector("[data-auto-submit]");
  if (filterForm) {
    filterForm.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => filterForm.requestSubmit());
    });
  }

  const rtdToggle = document.querySelector("[data-rtd-toggle]");
  if (rtdToggle) {
    const rtdLabel = document.querySelector("[data-rtd-label]");
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const rtdApi = rtdToggle.dataset.rtdApi || "/api/rtd-service";

    const setRtdState = (running) => {
      rtdToggle.checked = running;
      if (rtdLabel) rtdLabel.textContent = `RTD ${running ? "ligado" : "desligado"}`;
    };

    const changeRtdState = async (enabled) => {
      rtdToggle.disabled = true;
      try {
        const response = await fetch(rtdApi, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          credentials: "same-origin",
          body: JSON.stringify({ enabled }),
        });
        const payload = await response.json();
        setRtdState(Boolean(payload.running));
        if (!response.ok) throw new Error(payload.error || "Falha ao alterar o RTD.");
      } catch (error) {
        setRtdState(!enabled);
        window.alert(error.message);
      } finally {
        rtdToggle.disabled = false;
      }
    };

    rtdToggle.addEventListener("change", () => changeRtdState(rtdToggle.checked));
    if (!sessionStorage.getItem("rtd-auto-start-attempted")) {
      sessionStorage.setItem("rtd-auto-start-attempted", "true");
      if (!rtdToggle.checked && !rtdToggle.disabled) changeRtdState(true);
    }
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
