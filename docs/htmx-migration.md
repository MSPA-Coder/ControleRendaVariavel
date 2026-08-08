# Migração para HTMX — plano e estado

Documento de trabalho: descreve as fases da conversão da interface para HTMX
e registra o que já foi concluído. Serve como ponto de retomada entre
sessões. Deve ser removido quando a migração terminar e o README passar a
descrever o estado final.

- **Branch:** `feature/htmx`
- **Ponto de restauração:** `4c32d3e` em `main` (auditoria e refatoração,
  suíte verde, 295 testes)

## Por que

Hoje não há HTMX no projeto. A interatividade vive em `app/static/app.js`,
com `fetch` manual contra endpoints JSON. O caso mais grave é o auto-refresh
da carteira: ele consulta `/api/portfolio`, ignora quase todo o payload e
chama `window.location.reload()` — uma recarga de página inteira a cada
2 segundos.

Objetivos, nesta ordem:

1. trocar recarga total por troca de fragmento;
2. tirar do navegador a construção de DOM a partir de JSON;
3. manter servidor como fonte de verdade, com CSRF, autorização e
   validação inalterados;
4. reduzir o JavaScript próprio ao que HTMX não resolve.

## Regras que a migração deve respeitar

Da base compartilhada de engenharia (`AGENTS.md`):

- versão do HTMX **fixada**, asset **local** versionado — nunca `latest` nem
  CDN sem autorização explícita na CSP;
- `HX-Request` é sinal de apresentação, **nunca** prova de autorização;
- escritas continuam usando método HTTP adequado e proteção CSRF;
- respostas parciais saem de templates reutilizáveis, sem duplicar regra de
  negócio no navegador;
- preservar HTML semântico, acessibilidade, foco, mensagens de erro e
  histórico/URL quando a interação mudar conteúdo;
- testes HTTP cobrem **tanto** a requisição normal quanto a requisição HTMX
  nos fluxos em que a resposta diferir.

Restrição de ambiente: a CSP é `default-src 'self'; object-src 'none'`, sem
`unsafe-inline` e sem `unsafe-eval`. Portanto: nada de handler inline, nada
de `hx-vals` com prefixo `js:`, nada de expressão avaliada em `hx-trigger`.

Dados: o volume `postgres_data` não pode ser apagado em nenhuma fase. A
suíte usa `test-db`, descartável.

## Fases

Cada fase termina com validação (`quality` + `test`) e um commit próprio.
As imagens de teste copiam o código-fonte, então **toda** fase precisa de
`docker compose --profile test build` antes de rodar os testes.

### Fase 1 — Fundação

- Vendorizar HTMX (versão fixa) em `app/static/vendor/`, com licença.
- Carregar em `base.html`.
- Definir o envio de CSRF em todas as requisições HTMX (header
  `X-CSRFToken`, via atributo, sem JS inline).
- Helper de backend para distinguir requisição HTMX de navegação normal.
- Testes: asset servido, token presente, CSP intacta.

### Fase 2 — Coletor e RTD

- Parciais HTML para o indicador de coletor e para o controle do RTD.
- Polling via `hx-trigger="every 10s"` em vez de `setInterval` + `fetch`.
- Toggle do RTD via `hx-post`.
- Remover o JS correspondente de `app.js`.
- Decidir o destino de `/api/collector-heartbeat` e `/api/rtd-service`.
- Testes HTMX e não-HTMX.

### Fase 3 — Auto-refresh da carteira

- Parcial com a tabela de posições e os totais.
- `hx-get` + `hx-trigger="every Ns"` + troca de fragmento, eliminando o
  `window.location.reload()`.
- Decidir o destino de `/api/portfolio` (hoje tem paginação e cache TTL).
- Testes.

### Fase 4 — Filtros

- Trocar o `data-auto-submit` por `hx-get` + `hx-trigger="change"` +
  `hx-push-url="true"` nas páginas com filtro.
- Preservar URL, histórico e foco.
- Paralelizável por página (templates disjuntos).
- Testes por página.

### Fase 5 — Limpeza e documentação

- Remover de `app.js` o que ficou órfão; manter o que é puramente
  client-side (mega menu, cards, gráficos).
- Remover endpoints JSON sem consumidor, com os testes correspondentes.
- Atualizar `README.md` e a seção "Estado atual da interface" do
  `AGENTS.md`, que hoje declara que HTMX não é usado.

### Fase 6 — Encerramento

- Validação completa, commit final, tarefa **V1.81**.

## Estado

| Fase | Situação | Commit |
|---|---|---|
| 1 — Fundação | **concluída** | `htmx 2.0.10` vendorizado, CSRF via `hx-headers`, `is_htmx_request()`, 5 testes |
| 2 — Coletor e RTD | **concluída** | parciais auto-atualizáveis, 2 endpoints JSON removidos, app.js 240→131 linhas |
| 3 — Carteira | pendente | — |
| 4 — Filtros | pendente | — |
| 5 — Limpeza e docs | pendente | — |
| 6 — Encerramento | pendente | — |

## Decisões tomadas

- **HTMX 2.0.10**, baixado do unpkg (dist oficial do pacote `htmx.org`),
  conferido contra o espelho jsDelivr — mesmo SHA-256
  (`71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de`).
  Servido de `app/static/vendor/htmx.min.js`.
- **Endpoints JSON sem consumidor serão removidos**, junto com os testes de
  contrato correspondentes, preservando sempre as asserções de autenticação
  (401/302). `/health` permanece: é infraestrutura, não frontend.

## Achados do levantamento

- `/api/options` **não tem nenhum teste**. É o endpoint mais fácil de
  converter sem quebrar a suíte, e também o mais desprotegido.
- `/api/portfolio`, `/api/collector-heartbeat` e `/api/rtd-service` têm
  testes `critical`/`security`/`observable_contract` que fixam o formato
  JSON. Ao virarem parciais, as asserções de formato mudam, mas as de
  autenticação precisam sobreviver.
- `data-catalog-card` é **código morto**: `app.js:70-93` procura esse
  atributo, e nenhum template o possui. Remover na fase 5.
- `data-nav-group`/`data-mega`/`data-group` em `base.html` não são lidos por
  JS nenhum; o menu funciona por classe. Verificar antes de remover.
- O auto-refresh de `index.html` e `options.html` compartilha o mesmo bloco
  genérico de `app.js` (`[data-refresh-seconds]`): converter os dois juntos.
- `header_controls` é capturado em `base.html` com `{% set %}` e só renderiza
  se não estiver vazio — um parcial que reconstrua essa área precisa devolver
  o `<form>` inteiro, não só o campo alterado.

## Defeito pré-existente encontrado (fora do escopo desta migração)

`base.html` carrega Google Fonts de `fonts.googleapis.com` e
`fonts.gstatic.com`, mas a CSP ativa é `default-src 'self'` — verificado no
cabeçalho da aplicação em execução. As três famílias (Space Grotesk, Inter,
IBM Plex Mono) **são bloqueadas pelo navegador** e a interface cai nas fontes
do sistema. Corrigir exige decidir entre: vendorizar as fontes, liberar as
duas origens na CSP, ou remover os links. Aguarda decisão do mantenedor.
