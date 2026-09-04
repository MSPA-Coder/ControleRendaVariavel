// O HTMX injeta um <style> proprio no carregamento para o indicador de
// requisicao (`htmx.config.includeIndicatorStyles`, `true` por padrao). Esta
// aplicacao nao usa `hx-indicator` nem a classe `htmx-indicator` em lugar
// nenhum, entao esse estilo e peso morto -- e como vem sem nonce, viola
// `style-src 'self'` e aparece como erro no console a cada pagina.
//
// Roda antes de DOMContentLoaded (ambos os scripts sao `defer`, executados na
// ordem do documento), que e quando o HTMX de fato le o config.
htmx.config.includeIndicatorStyles = false;

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
  // alguem esta olhando: uma aba esquecida em segundo plano continuaria
  // pedindo fragmentos sem leitor. As tabelas que ainda usam `every` mantem
  // este cuidado; Acoes usa o agendamento pontual logo abaixo.
  //
  // O filtro declarativo do HTMX (`every 5s [condicao]`) resolveria isso sem
  // JavaScript, mas ele compila a condicao com `new Function` e a CSP do
  // projeto e `default-src 'self'`, sem `unsafe-eval`.
  const POLL_WHEN_VISIBLE = "[data-poll-when-visible]";
  const PORTFOLIO_REFRESH = "[data-quote-refresh-schedule]";
  const QUOTE_REFRESH_GRACE_MS = 5000;
  const attemptedQuoteVersions = new Set();
  let portfolioRefreshTimer;

  // O refresh agendado e o botao +/- fazem requisicoes para o mesmo alvo.
  // Quando coincidem, o navegador pode entregar primeiro a resposta antiga
  // (por exemplo, o refresh) e depois a intencao mais recente (o clique), ou
  // vice-versa. O HTMX nao ordena respostas: sem esta guarda, a resposta
  // antiga pode fechar uma expansao que acabou de ser aberta.
  //
  // O refresh automatico espera enquanto um clique de expansao esta em voo.
  // Sem isso, um refresh iniciado depois do clique seria considerado a
  // intencao mais nova e poderia fechar a linha antes da resposta do clique.
  // A requisicao antiga continua sendo enviada; somente o swap obsoleto e
  // descartado. O mapa e por XHR para distinguir respostas mesmo depois que
  // o fragmento alvo foi substituido por outerHTML.
  const portfolioRequestGenerations = new WeakMap();
  let latestPortfolioRequestGeneration = 0;
  const portfolioExpansionRequests = new Set();

  const targetsPortfolioResults = (detail) => {
    const target = detail && detail.target;
    const requester = detail && detail.elt;
    const requestConfig = detail && detail.requestConfig;
    const configuredRequester = requestConfig && requestConfig.elt;
    return Boolean(
      (target && target.id === "portfolio-results") ||
      (requester && requester.id === "portfolio-results") ||
      (configuredRequester && configuredRequester.id === "portfolio-results"),
    );
  };

  const schedulePortfolioRefresh = () => {
    if (portfolioRefreshTimer) {
      window.clearTimeout(portfolioRefreshTimer);
      portfolioRefreshTimer = undefined;
    }
    if (document.hidden || !window.htmx) return;

    const element = document.querySelector(PORTFOLIO_REFRESH);
    if (!element) return;

    const intervalMs = Number(element.dataset.quoteIntervalMs);
    const retryMs = Number(element.dataset.quoteRetryMs);
    const lastReadAt = Date.parse(element.dataset.quoteLastReadAt || "");
    const safeRetryMs = Number.isFinite(retryMs) && retryMs > 0 ? retryMs : 30000;
    let delayMs = safeRetryMs;

    if (Number.isFinite(lastReadAt) && Number.isFinite(intervalMs) && intervalMs > 0) {
      const nextExpectedAt = lastReadAt + intervalMs + QUOTE_REFRESH_GRACE_MS;
      if (nextExpectedAt > Date.now()) {
        delayMs = nextExpectedAt - Date.now();
      } else {
        // Ao abrir Acoes depois do horário esperado, consulta uma vez já.
        // Se o agente ainda não tiver entregue uma cotação nova, a mesma
        // versão espera o intervalo de configuração antes de tentar de novo.
        const version = element.dataset.quoteLastReadAt;
        if (!attemptedQuoteVersions.has(version)) {
          attemptedQuoteVersions.add(version);
          delayMs = 0;
        }
      }
    }

    portfolioRefreshTimer = window.setTimeout(() => {
      portfolioRefreshTimer = undefined;
      if (!document.hidden && document.querySelector(PORTFOLIO_REFRESH) === element) {
        window.htmx.trigger(element, "quote-refresh-due");
      }
    }, delayMs);
  };

  document.addEventListener("htmx:beforeRequest", (event) => {
    if (!document.hidden) return;
    const element = event.detail && event.detail.elt;
    if (
      element && element.matches &&
      (element.matches(POLL_WHEN_VISIBLE) || element.matches(PORTFOLIO_REFRESH))
    ) {
      event.preventDefault();
    }
  });

  document.addEventListener("htmx:beforeRequest", (event) => {
    // A requisicao que uma guarda anterior impediu nao representa uma nova
    // intencao e nao deve tornar respostas validas obsoletas.
    if (event.defaultPrevented || !targetsPortfolioResults(event.detail)) return;
    const xhr = event.detail && event.detail.xhr;
    const requester = event.detail && event.detail.elt;
    const isExpansionRequest = Boolean(
      requester && requester.matches && requester.matches(".row-toggle"),
    );
    const isScheduledRefresh = Boolean(
      requester && requester.matches && requester.matches(PORTFOLIO_REFRESH),
    );
    if (isScheduledRefresh && portfolioExpansionRequests.size > 0) {
      // O timer nao e uma nova intencao do usuario. Deixa o clique terminar;
      // o afterSwap vai programar o proximo ciclo com a linha ainda aberta.
      event.preventDefault();
      return;
    }
    if (!xhr) return;
    latestPortfolioRequestGeneration += 1;
    portfolioRequestGenerations.set(xhr, latestPortfolioRequestGeneration);
    if (isExpansionRequest) portfolioExpansionRequests.add(xhr);
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    const xhr = event.detail && event.detail.xhr;
    if (xhr) portfolioExpansionRequests.delete(xhr);
  });

  document.addEventListener("htmx:beforeSwap", (event) => {
    if (!targetsPortfolioResults(event.detail)) return;
    const xhr = event.detail && event.detail.xhr;
    const generation = xhr && portfolioRequestGenerations.get(xhr);
    if (generation === undefined || generation === latestPortfolioRequestGeneration) return;

    // Deixar o evento cancelado faz o HTMX executar afterRequest normalmente,
    // mas impede apenas a substituicao visual do fragmento obsoleto.
    event.preventDefault();
  });

  // Ao voltar para a aba, atualiza na hora em vez de esperar o proximo ciclo:
  // o que esta na tela pode ter varios minutos de atraso.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !window.htmx) return;
    document.querySelectorAll(POLL_WHEN_VISIBLE).forEach((element) => {
      window.htmx.trigger(element, "poll-resumed");
    });
    schedulePortfolioRefresh();
  });

  document.addEventListener("DOMContentLoaded", schedulePortfolioRefresh);
  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail && event.detail.target;
    const requester = event.detail && event.detail.requestConfig && event.detail.requestConfig.elt;
    if (
      (target && target.id === "portfolio-results") ||
      (requester && requester.id === "portfolio-results")
    ) {
      schedulePortfolioRefresh();
    }
  });

  document.addEventListener("click", (event) => {
    // Preview instantaneo do tema: marca visualmente a opcao clicada e
    // aplica o data-theme na hora, sem esperar o submit do formulario. O
    // valor so e persistido quando "Salvar configuracoes" for enviado --
    // isto e so o retorno visual do clique no seletor.
    const themeOption = event.target.closest(".theme-option");
    if (themeOption) {
      const input = themeOption.querySelector('input[type="radio"]');
      if (input) {
        document.querySelectorAll(".theme-option").forEach((option) => {
          option.classList.remove("active");
        });
        themeOption.classList.add("active");
        document.documentElement.setAttribute("data-theme", input.value);
      }
    }

    const portfoliosToggle = event.target.closest("[data-portfolios-toggle]");
    if (portfoliosToggle) {
      const portfoliosCard = document.getElementById("portfolios-management-card");
      if (!portfoliosCard) return;
      const isOpen = portfoliosCard.hidden;
      portfoliosCard.hidden = !isOpen;
      portfoliosToggle.setAttribute("aria-expanded", String(isOpen));
      if (isOpen) {
        portfoliosCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      return;
    }
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

  // Trocar a carteira de uma posicao para a Simulada, ao editar, apaga o
  // extrato de movimentos e a transacao aberta espelhada (ver
  // `discard_simulation_history` em app/position_closure.py e
  // app/option_position_closure.py). Editar posicao e operacao rotineira e
  // reversivel -- nao merece confirmacao sempre --, mas essa combinacao
  // especifica destroi historico sem avisar. Por isso a pergunta e
  // condicional: so quando a carteira escolhida no <select> for a Simulada E
  // a posicao ja tiver mais que a abertura no extrato (o mesmo limiar que
  // close_position_form.html usa pra decidir se mostra o extrato -- uma
  // abertura sozinha e recriada identica se a posicao voltar de uma carteira
  // real, entao nesse caso nao ha nada de fato a perder).
  //
  // hx-confirm nao se aplica aqui: position_form.html/option_form.html sao
  // <form method="post"> comuns, sem HTMX -- o componente comum so intercepta
  // `htmx:confirm`. onsubmit inline tambem nao funcionaria -- a CSP do
  // projeto e `default-src 'self'` sem `unsafe-inline`, entao um atributo
  // onsubmit e bloqueado pelo navegador antes mesmo de rodar. Por isso o
  // caminho e o mesmo `window.sharedauth.confirmar()` programatico que o
  // componente expoe pra decisao condicional (ver o docstring de
  // sharedauth-ui.js): promessa, nao `window.confirm()` sincrono, entao o
  // submit tem de ser adiado ate ela resolver.
  //
  // O listener fica delegado no documento; se sharedauth-ui.js nao carregar
  // (ou carregar depois, numa ordem inesperada), `window.sharedauth` fica
  // indefinido. Nesse caso a operação destrutiva deve falhar fechada: deixar
  // submeter sem confirmação seria exatamente o caminho que esta guarda
  // deveria impedir.
  const showConfirmationUnavailable = (form) => {
    let warning = form.querySelector("[data-confirmation-unavailable]");
    if (!warning) {
      warning = document.createElement("p");
      warning.className = "form-warning";
      warning.setAttribute("role", "alert");
      warning.dataset.confirmationUnavailable = "true";
      warning.textContent =
        "Não foi possível abrir a confirmação de segurança. A operação não foi enviada; recarregue a página e tente novamente.";
      form.prepend(warning);
    }
    warning.focus({ preventScroll: false });
  };

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-confirm-simulated-switch]");
    if (!form) return;
    if (form.dataset.saSimuladaConfirmada === "1") return; // segunda passagem, ja confirmada
    const movementCount = Number(form.dataset.positionMovementCount || "0");
    if (movementCount <= 1) return;
    const portfolioSelect = form.querySelector('select[name="portfolio_id"]');
    const chosen = portfolioSelect && portfolioSelect.selectedOptions[0];
    if (!chosen || chosen.dataset.simulated !== "true") return;
    if (!window.sharedauth || !window.sharedauth.confirmar) {
      event.preventDefault();
      showConfirmationUnavailable(form);
      return;
    }
    event.preventDefault();
    window.sharedauth
      .confirmar({
        titulo: "Trocar para carteira Simulada",
        mensagem:
          "Trocar para a carteira Simulada apaga o extrato de movimentos desta posição e não pode ser desfeito. Continuar?",
        severidade: "error",
      })
      .then((ok) => {
        if (!ok) return;
        form.dataset.saSimuladaConfirmada = "1";
        form.requestSubmit();
      });
  });
})();
