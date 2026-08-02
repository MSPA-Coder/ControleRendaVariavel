(() => {
  const megaWraps = document.querySelectorAll(".mega-wrap");
  const navScrim = document.querySelector("[data-nav-scrim]");

  if (megaWraps.length && navScrim) {
    const closeNavigation = () => {
      megaWraps.forEach((wrap) => {
        const button = wrap.querySelector(".grp-btn");
        const mega = wrap.querySelector(".mega");
        if (button) {
          button.classList.remove("open");
          button.setAttribute("aria-expanded", "false");
        }
        if (mega) mega.classList.remove("show");
      });
      navScrim.hidden = true;
    };
    const openNavigation = (wrap) => {
      const button = wrap.querySelector(".grp-btn");
      const mega = wrap.querySelector(".mega");
      if (button) {
        button.classList.add("open");
        button.setAttribute("aria-expanded", "true");
      }
      if (mega) mega.classList.add("show");
      navScrim.hidden = false;
    };

    megaWraps.forEach((wrap) => {
      const button = wrap.querySelector(".grp-btn");
      if (!button) return;
      button.addEventListener("click", () => {
        const isOpen = button.classList.contains("open");
        closeNavigation();
        if (!isOpen) openNavigation(wrap);
      });
    });
    navScrim.addEventListener("click", closeNavigation);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !navScrim.hidden) closeNavigation();
    });
  }

  const filterForm = document.querySelector("[data-auto-submit]");
  if (filterForm) {
    filterForm.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => filterForm.requestSubmit());
    });
  }

  const quoteManagementToggle = document.querySelector("[data-quote-management-toggle]");
  const quoteManagementCard = document.getElementById("quote-management-card");
  if (quoteManagementToggle && quoteManagementCard) {
    quoteManagementToggle.addEventListener("click", () => {
      const isOpen = quoteManagementCard.hidden;
      quoteManagementCard.hidden = !isOpen;
      quoteManagementToggle.setAttribute("aria-expanded", String(isOpen));
      if (isOpen) {
        quoteManagementCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        quoteManagementCard.focus({ preventScroll: true });
      }
    });
  }

  const catalogCards = document.querySelectorAll("[data-catalog-card]");
  if (catalogCards.length) {
    const openCatalogCardFromHash = () => {
      const cardId = window.location.hash.slice(1);
      catalogCards.forEach((card) => {
        card.open = card.id === cardId;
      });
    };

    openCatalogCardFromHash();
    window.addEventListener("hashchange", openCatalogCardFromHash);

    catalogCards.forEach((card) => {
      card.addEventListener("toggle", () => {
        if (!card.open) return;
        catalogCards.forEach((otherCard) => {
          if (otherCard !== card) otherCard.open = false;
        });
        if (card.id && window.location.hash !== `#${card.id}`) {
          window.history.replaceState(null, "", `#${card.id}`);
        }
      });
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

    const refreshRtdService = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(rtdApi, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "RTD unavailable.");
        rtdToggle.disabled = !payload.available;
        setRtdState(Boolean(payload.running));
      } catch {
        rtdToggle.disabled = true;
      }
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
    refreshRtdService();
    window.setInterval(refreshRtdService, 10_000);
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
