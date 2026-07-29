(() => {
  const filterForm = document.querySelector("[data-auto-submit]");
  if (filterForm) {
    filterForm.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => filterForm.requestSubmit());
    });
  }

  const catalogCards = document.querySelectorAll("[data-catalog-card]");
  if (catalogCards.length) {
    const catalogStateKey = "catalog-open-cards";
    const openCards = new Set(JSON.parse(sessionStorage.getItem(catalogStateKey) || "[]"));
    const saveOpenCards = () => {
      sessionStorage.setItem(
        catalogStateKey,
        JSON.stringify([...catalogCards]
          .filter((card) => card.open)
          .map((card) => card.dataset.catalogCard)),
      );
    };
    catalogCards.forEach((card) => {
      card.open = openCards.has(card.dataset.catalogCard) || card.id === window.location.hash.slice(1);
      card.addEventListener("toggle", saveOpenCards);
    });
    document.querySelectorAll("[data-catalog-card] form").forEach((form) => {
      form.addEventListener("submit", saveOpenCards);
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

  const heartbeat = document.querySelector("[data-collector-heartbeat]");
  if (heartbeat) {
    const heartbeatApi = heartbeat.dataset.heartbeatApi || "/api/collector-heartbeat";
    const stateLabel = heartbeat.querySelector("[data-heartbeat-state]");
    const timeLabel = heartbeat.querySelector("[data-heartbeat-time]");
    const labels = {
      online: "Coletor online",
      stale: "Coletor atrasado",
      error: "Coletor com erro",
      waiting: "Coletor aguardando leitura",
    };
    const renderHeartbeat = ({ status, last_read_at: lastReadAt }) => {
      heartbeat.className = `collector-heartbeat is-${status}`;
      heartbeat.dataset.heartbeatStatus = status;
      if (stateLabel) stateLabel.textContent = labels[status] || "Coletor indisponível";
      if (timeLabel) {
        timeLabel.textContent = lastReadAt
          ? `Última leitura: ${new Intl.DateTimeFormat("pt-BR", {
              dateStyle: "short", timeStyle: "medium",
            }).format(new Date(lastReadAt))}`
          : "Sem leitura registrada";
      }
    };
    renderHeartbeat({
      status: heartbeat.dataset.heartbeatStatus,
      last_read_at: heartbeat.dataset.lastReadAt || null,
    });
    const refreshHeartbeat = async () => {
      if (!document.hidden) {
        try {
          const response = await fetch(heartbeatApi, {
            headers: { Accept: "application/json" }, credentials: "same-origin",
          });
          if (response.ok) renderHeartbeat(await response.json());
        } catch {
          // Keep the last known heartbeat visible while the next poll retries.
        }
      }
      window.setTimeout(refreshHeartbeat, 10_000);
    };
    window.setTimeout(refreshHeartbeat, 10_000);
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
