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
  // indefinido e a guarda abaixo deixa o formulario submeter normal, sem
  // pergunta -- nunca trava o clique em silencio esperando uma Promise que
  // nunca chega.
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-confirm-simulated-switch]");
    if (!form) return;
    if (form.dataset.saSimuladaConfirmada === "1") return; // segunda passagem, ja confirmada
    const movementCount = Number(form.dataset.positionMovementCount || "0");
    if (movementCount <= 1) return;
    const portfolioSelect = form.querySelector('select[name="portfolio_id"]');
    const chosen = portfolioSelect && portfolioSelect.selectedOptions[0];
    if (!chosen || chosen.dataset.simulated !== "true") return;
    if (!window.sharedauth || !window.sharedauth.confirmar) return; // ver comentario acima
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
