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

  // Auto-refresh so com a aba a vista. O `every Ns` do HTMX nao sabe se
  // alguem esta olhando: uma aba de Acoes esquecida em segundo plano
  // continuaria pedindo a carteira inteira a cada ciclo, sem leitor. Cancelar
  // no `htmx:beforeRequest` para a requisicao mas preserva o temporizador,
  // entao o ciclo volta sozinho quando a aba reaparece.
  //
  // O filtro declarativo do HTMX (`every 5s [condicao]`) resolveria isso sem
  // JavaScript, mas ele compila a condicao com `new Function` e a CSP do
  // projeto e `default-src 'self'`, sem `unsafe-eval`.
  const POLL_WHEN_VISIBLE = "[data-poll-when-visible]";

  document.addEventListener("htmx:beforeRequest", (event) => {
    if (!document.hidden) return;
    const element = event.detail && event.detail.elt;
    if (element && element.matches && element.matches(POLL_WHEN_VISIBLE)) {
      event.preventDefault();
    }
  });

  // Ao voltar para a aba, atualiza na hora em vez de esperar o proximo ciclo:
  // o que esta na tela pode ter varios minutos de atraso.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !window.htmx) return;
    document.querySelectorAll(POLL_WHEN_VISIBLE).forEach((element) => {
      window.htmx.trigger(element, "poll-resumed");
    });
  });

  document.addEventListener("click", (event) => {
    const quoteManagementToggle = event.target.closest("[data-quote-management-toggle]");
    if (!quoteManagementToggle) return;
    const quoteManagementCard = document.getElementById("quote-management-card");
    if (!quoteManagementCard) return;
    const isOpen = quoteManagementCard.hidden;
    quoteManagementCard.hidden = !isOpen;
    quoteManagementToggle.setAttribute("aria-expanded", String(isOpen));
    if (isOpen) {
      quoteManagementCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      quoteManagementCard.focus({ preventScroll: true });
    }
  });
})();
