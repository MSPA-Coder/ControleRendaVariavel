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
})();
