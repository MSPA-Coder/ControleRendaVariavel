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
    // `data-quote-chart-period` (e outros controles puramente client-side,
    // sem `name`) não devem recarregar a página — eles só redesenham um
    // gráfico local; ver quote-history-chart.js.
    filterForm
      .querySelectorAll("select:not([data-quote-chart-period]), input[type=checkbox]:not([data-rtd-toggle])")
      .forEach((field) => {
        field.addEventListener("change", () => filterForm.requestSubmit());
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
