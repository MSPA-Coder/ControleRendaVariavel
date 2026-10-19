# Arquitetura

## Comandos e consultas de posições

`positions/use_cases.py` coordena os aportes e encerramentos de ações/opções:
o dono entra explicitamente, e o commit/rollback inclui chave idempotente,
posição, movimentos, transações e direito histórico ao ticker. As rotas só
convertem formulário, confirmação e resultado em HTTP. Os módulos `closure`
aplicam as mudanças sem commit parcial; a API antiga de encerramento delega
ao caso de uso para preservar consumidores existentes.

`positions/queries.py` contém as consultas de grade com dono, moeda e filtros
explícitos. Os adaptadores em rotas convertem preferências e parâmetros HTTP;
não existe um repository genérico sobre SQLAlchemy. O histórico de acesso ao
ticker mora em `accounts/entitlements.py`, sem dependência das rotas.

O compartilhamento entre instrumentos é pontual (transação, chave de envio e
entradas/saídas tipadas). Abertura cronológica de ações, contratos de opções e
replay continuam separados: suas diferenças são regras, não duplicação a
eliminar por uma superclasse.

## Visão geral

Aplicação Flask monolítica, renderizada no servidor, organizada em três
camadas: adaptação HTTP, domínio e persistência. A separação existe onde paga
por si — o domínio financeiro é calculado sem requisição e sem ORM — e não onde
só acrescentaria indireção.

```text
navegador
    ↓  HTML completo ou fragmento HTMX
app/routes                     adaptação HTTP: formulário, filtro, template
    ↓
app/core/domain e os módulos de cálculo    regras financeiras, sem Flask e sem ORM
    ↓
app/models → SQLAlchemy → PostgreSQL
```

Fora dessa pilha existe um único ator: o **agente RTD**, um processo Windows
que envia cotações à aplicação por HTTPS autenticado. Ele tem seção própria
abaixo.

Não há API JSON de negócio, fila, broker, cache externo nem provedor de login
externo. As exceções são `/health`, endpoint operacional para as sondas, e os
três endpoints `/api/collector/*`, que existem só para o agente.

## Interface: HTMX, não uma API

Toda a interface é HTML montado em Jinja. As atualizações incrementais usam
HTMX: o servidor devolve um fragmento do próprio template e o navegador o troca
no lugar certo.

**Cada tela tem uma URL só.** A rota decide a *forma* da resposta pelo cabeçalho
`HX-Request` — página inteira ou fragmento —, e não o *endereço*.
`app/routes/helpers.py::is_htmx_request` é o único ponto que lê esse cabeçalho
para essa decisão. Isso mantém a navegação sem JavaScript como caminho completo,
e mantém favorito, F5 e link compartilhado válidos.

| Página | Fragmento devolvido a `HX-Request` |
|---|---|
| `/` (Carteira) | a região de resultados, ao filtrar |
| `/transactions` | a tabela de transações |
| `/dividends` | a tabela de proventos |
| `/options` | a região de resultados de opções |
| `/performance` | o bloco do relatório mensal |
| `/quotes` | o histórico de cotações |
| `/analysis/exposure-*` | o bloco de exposição e seu gráfico |
| `/users` | a lista de contas |
| `/tables/*` | a tabela do cadastro editado |
| `/partials/collector-heartbeat`, `/partials/rtd-service` | só existem como fragmento |

`HX-Request` é sinal de **apresentação**, nunca prova de autorização ou de
origem: o cliente o define e pode forjá-lo. A autorização é aplicada no
servidor, igual para os dois tipos de requisição.

### As duas exceções: rotas que não devolvem HTML

O agente RTD (`/api/collector/*`) e a publicação de patrimônio
(`/patrimonio/v4/*`) falam JSON, e as duas são máquina a máquina:
não têm sessão, e a permissão delas é um token conferido em tempo constante
dentro da própria view. Estão declaradas em `PUBLIC_ENDPOINTS` — uma rota nova
nasce protegida, e entrar nessa lista é decisão consciente, com o motivo
escrito.

A publicação de patrimônio entrega **posições, proventos e cotações** a um
consolidador externo (hoje, o Wealthfolio), que soma isto ao caixa publicado pelo
Controle Bancário. Ele não escreve nada, não recebe nada e não conhece o
consolidador. O contrato é `patrimonio/v4`: todo valor viaja como **texto**
(`float` não representa 0,10), titular e instituição são identificados pelo
**nome normalizado** — o que os dois sistemas compartilham — e nada é somado
entre moedas.

Três coisas ficam **sempre** de fora, com a contagem na cobertura: carteira
simulada (não é patrimônio), opções (ainda não publicadas) e, no valor a mercado,
a posição sem cotação (a posição vai, com `market_value` nulo). Omissão contada
é omissão visível.

O teto de requisições vale também para quem apresenta o token correto: o token
autoriza a integração, mas não transforma uma consulta cara em um caminho
ilimitado. O consolidador deve agrupar pedidos ou usar uma exportação controlada
quando precisar de volume maior.

`PATRIMONIO_TITULAR` e `PATRIMONIO_OWNER_ID` são obrigatórios para publicar.
Titular é a identidade externa; `PATRIMONIO_OWNER_ID` é o usuário financeiro
explicitamente autorizado. Toda consulta do publicador aplica esse `owner_id`,
inclusive posições, carteiras, proventos, transações e histórico.

O snapshot limita a janela de preços históricos a `PATRIMONIO_MAX_HISTORICO_DIAS`,
com padrão de dez anos. Isso protege o worker contra reconstruções sem limite;
exportações mais antigas devem ser produzidas por fluxo controlado.

### Contratos v1 a v3 (retirados)

Os contratos `patrimonio/v1` (resumo), `v2` (dashboard) e `v3` (atividades,
categorias, metadata, renda, desempenho, eventos e histórico por posição) serviam
ao NetWorth, aposentado em 29/09/2026, e foram retirados em 03/10/2026: o nginx
não registra acesso a eles desde 28/09, o Wealthfolio só lê o v4 (o patch dele
recusa qualquer outro caminho), e o histórico está no Git. O `PATRIMONIO_TOKEN`
antigo, que os autorizava, deixou de existir (Compose, código e CI).

### Snapshot inicial v4 para integração com carteira externa

`GET /patrimonio/v4/metadata`, `GET /patrimonio/v4/snapshot`,
`GET /patrimonio/v4/activities`, `GET /patrimonio/v4/changes` e
`GET /patrimonio/v4/ledger` publicam dados somente-leitura, com escopo
explícito de `PATRIMONIO_OWNER_ID`.
As rotas v4 usam o token exclusivo `PATRIMONIO_INTEGRATION_TOKEN`;
o `PATRIMONIO_TOKEN` antigo deixou de existir. Os cursores v4
são assinados pelo token exclusivo, e sua rotação invalida cursores emitidos
anteriormente. O snapshot contém posições abertas de ações em carteiras
reais, proventos persistidos e cotações atuais e diárias de tickers que o owner
já deteve. A janela de preços históricos é limitada por
`PATRIMONIO_MAX_HISTORICO_DIAS`, que é um limite e não uma garantia de cobertura
para todo o período. O consumidor pode chamar o snapshot com
`?include_prices=false` para receber posições e proventos sem a série de preços;
`coverage.prices.complete` e `coverage.prices.omitted` declaram essa omissão.
O endpoint de atividades publica resumos de resultados de transações encerradas
e proventos persistidos. Não contém os lotes nem os preços de execução, portanto
`complete_trades` continua indisponível. Ele usa o token exclusivo v4 e um
`high_watermark` comum às páginas; o consumidor deve reiniciar a leitura se o
watermark mudar entre páginas.
Preço ou valor de mercado ausente permanece nulo, sem estimativa; a cobertura
informa as contagens de posições sem preço e excluídas.
Cada resposta leva `snapshot_id`, e os recursos levam `source_id` estável e
opaco. Cada item de `holdings` publica `instrument_type: "equity"`, identificando
de forma explícita e estável o tipo das posições abertas de ações que compõem o
escopo atual. `coverage.holdings.complete` declara `true` porque a lista contém
todas as posições abertas de ações em carteiras reais do owner, inclusive as sem
cotação; carteiras simuladas e opções ficam fora desse escopo. O consumidor só
deve reconciliar holdings ausentes dentro desse escopo quando essa cobertura for
completa. Posições declaram `price_kind` e `valuation_method`: o preço avaliado
usa cotação específica do lado quando disponível e recorre ao preço geral do
coletor somente quando necessário. A série de preços do coletor é marcada como
informativa e não como o preço usado na avaliação da posição.

Metadados e snapshot declaram opções e trades completos indisponíveis. O CRV
não conserva execuções suficientes para reconstruir um ledger completo de
compras e vendas para o histórico já existente; eventos de quantidade e
encerramentos agregados não são substitutos. A partir da revisão
`20260928_0024`, o encerramento total de uma posição de ações também copia cada
`PositionMovement` para `position_movement_archive`, com seus campos
financeiros e contexto da carteira. A tabela começa vazia: não existe backfill
porque os movimentos antigos já foram apagados e não podem ser reconstruídos
com fidelidade. Essa retenção não altera a capacidade `complete_trades`, que
permanece indisponível até haver um contrato de exportação e cobertura histórica
suficientes. Em paralelo, `GET /patrimonio/v4/ledger` publica por páginas o
arquivo prospectivo de movimentos de posições de ações encerradas. Cada
movimento preserva variação, preço, quantidade resultante, custo médio,
resultado realizado e referência à transação de origem; o evento `close` é
derivado da transação final e zera a quantidade. `metadata` e o envelope do
recurso declaram `completeness=prospective`, a revisão inicial e
`backfill=false`. A rota não publica caixa: compras, vendas e resultados não
devem ser relançados como movimento operacional do Controle Bancário.
Proventos são fatos analíticos e podem também estar registrados no
Controle Bancário; o consumidor não deve lançá-los novamente como movimento de
caixa. A renda lida do CB (`origin = 'cb'`, a partir de 01/01/2026) sai com o
campo opcional `origin: "cb"`, `amount` líquido e `duplicate_risk =
already_recorded_as_controle_bancario_entry`; a linha de planilha que o CB
passou a cobrir (substituída) não é publicada. O feed de mudanças é uma outbox de invalidação transacional: o contador
singleton é atualizado dentro da mesma transação que muda a origem, e seu lock
preserva a ordem de commit dos cursores. Cada invalidação exige a leitura de um
novo snapshot coerente; ela não tenta reconstruir fatos operacionais. Após
aplicar o snapshot, o consumidor persiste o high watermark retornado. A
outbox guarda 30 dias: `flask expurgar-outbox-v4`, no timer diário do
`manutencao`, apaga o que é mais velho, menos a linha mais nova de cada dono e
a mais nova sem dono, para que um checkpoint antigo continue vendo que houve
mudança (`app/patrimonio/outbox.py`). Regravar uma cotação de histórico com o
mesmo preço não emite invalidação. Proventos representam o valor recebido, sem
discriminação de imposto. Carteiras simuladas e opções não entram no snapshot.
As rotas mantêm `Cache-Control: no-store` e usam `REPEATABLE READ` para compor
uma resposta coerente.

### O endereço que chega à barra

Um formulário HTML serializa todos os seus campos ao ser enviado, inclusive os
que estão vazios ou no valor padrão. Sem tratamento, a Carteira sem nenhum
filtro aplicado aparecia na barra como
`/?portfolio_id=all&broker=` — nada ali foi escolhido por
ninguém.

`app/core/url_limpa.py` monta o endereço equivalente sem esse ruído, e o
`after_request` `_canonizar_url` (em `app/__init__.py`) o entrega no cabeçalho
`HX-Replace-Url`. O navegador troca a barra sem recarregar. O filtro continua na
URL quando é um filtro de verdade: `?broker=XP` aparece exatamente quando
alguém escolheu XP.

O filtro global de moeda usa esse mesmo contrato: `currency=BRL`, `USD` ou
`ALL`. Sem parâmetro, o recorte é BRL. Ele se propaga por navegação e HTMX e é
aplicado antes dos cálculos das telas financeiras; `ALL` exibe os dois grupos,
mas nunca os soma. O estado é da requisição, não da sessão nem do banco: duas
abas podem escolher moedas distintas, sem que uma resposta atrasada reverta a
outra. O seletor e o modo discreto ficam no popover aberto pelo ícone de filtro
do cabeçalho.

Duas decisões de desenho, que o módulo documenta e os testes protegem:

- **parâmetro desconhecido é preservado, não descartado.** Um filtro novo
  acrescentado sem lembrar de `FILTROS_PADRAO` continua funcionando, e no pior
  caso aparece com o valor padrão junto. Descartar tudo que não está na tabela
  faria esse mesmo filtro sumir do endereço em silêncio, quebrando favorito e
  link sem nada apontar para a causa;
- **estado de interface nunca chega à barra.** `expanded`, `expanded_tickers` e
  `expanded_years` dizem como a tela está desenhada, não quais dados ela mostra;
  mudam a cada clique e não interessam a quem recebe o link.

Página e fragmento compartilham a URL, então **qualquer cache introduzido à
frente da aplicação precisa considerar `HX-Request`**. Hoje isso não é um
problema porque nada armazena: o Nginx do VPS faz proxy sem `proxy_cache`.

O único cache é o do navegador, e só para estáticos: `url_for` acrescenta
`?v=<hash do conteúdo>` a cada arquivo de `static/` (do app e dos blueprints),
e a resposta cuja versão bate com o arquivo sai com um ano de `max-age` e
`immutable` (`app/core/estaticos.py`). Sem `v`, ou com um `v` antigo, o arquivo
continua revalidando. Por isso nenhum template monta caminho de estático à mão.

### JavaScript próprio

`app/static/app.js` cobre só o que HTML e HTMX não resolvem: menu, foco,
ocultação de valores, confirmação de ação destrutiva e o desligamento do estilo
que o HTMX injetaria sem nonce. Não renderiza dado nenhum.

Os quatro arquivos `*-chart.js` desenham os gráficos com Chart.js local. Eles
recebem a série pronta do servidor, em atributos `data-*` do contêiner do
gráfico — nunca em script inline, que a CSP não admite. Não calculam nada e não
chamam a aplicação.

## Formato regional (Brasil/EUA)

Cada usuário escolhe em Minhas preferências como datas e números aparecem e são
digitados (`UserPreference.regional_format`, padrão `br`). É só UX: banco,
cálculos, importação, API e o agente RTD seguem em ISO e decimal com ponto.

- `app/core/regional.py` guarda o formato da requisição numa `ContextVar`,
  preenchida em `before_request` e devolvida em `teardown_request`; o valor fica
  na sessão como o tema. Fora de requisição (CLI, coletor) vale Brasil.
- Números passam por `_number` (`app/core/presentation.py`); datas, pelos
  filtros `udate`, `ushort` e `uday_month`. Valores de `<input>` seguem ISO.
  Texto de tela em Python usa `regional.formatar_*`.
- `app/static/regional.js` troca `input[type=date|month]` e os decimais
  (`step` fracionário ou `data-regional-kind="decimal"`) por um campo de texto no
  formato escolhido; o original fica escondido, com o mesmo `name`, o mesmo valor
  e o atributo `data-sensitive-input`. Campo novo de data ou valor não precisa de
  código.

## Inicialização e configuração

`app.create_app()` é a factory. Ela, em ordem:

1. resolve segredos e monta a URL do PostgreSQL;
2. aplica a configuração e recusa iniciar sem `SECRET_KEY` ou sem banco;
3. configura sessão, CSRF, login, rate limit e cabeçalhos defensivos, todos
   vindos do SharedAuth;
4. registra o gerenciador do coletor, os blueprints, os comandos de CLI e os
   filtros Jinja;
5. religa os limites de rota e as isenções de CSRF.

`create_app()` **não consulta o banco**. Nenhuma tabela é criada, nenhuma
migração é aplicada e nenhuma linha é lida durante a construção — o serviço
`migrate` do Compose roda `flask db upgrade` e termina com sucesso antes de
`web` iniciar, e ele próprio precisa carregar a aplicação só para descobrir a
configuração do banco, antes de o schema existir.

O passo 5 tem um motivo que não é óbvio no código. `csrf` e `limiter` só existem
depois de `iniciar_csrf`/`iniciar_limiter`, uma instância por `create_app()` —
singleton de módulo vazaria isenção de CSRF e zeraria contador de rate limit
entre aplicações no mesmo processo. Por isso as rotas que precisariam decorar no
import são religadas depois de registradas. `RouteLimit.__call__` devolve uma
função *nova*: descartar o retorno em vez de reatribuir a `view_functions` deixa
o limite decorado e nunca aplicado.

### Segredos

`SECRET_KEY`, a senha do PostgreSQL, o token de patrimônio e os tokens de leitura
e escrita do agente vêm de arquivo, nunca do ambiente do contêiner: `NOME_FILE`
aponta o caminho e `sharedauth.secrets.resolver_segredo` o lê. `NOME` direto
continua aceito para execução manual e injeção de teste, mas não é o contrato do
Compose.

`app/core/secret_files.py` guarda o que só este projeto tem: o agente RTD roda no
Windows, fora de contêiner, e lê os valores de `.secrets/` na raiz do projeto.
Um consumidor único não justifica mover para a biblioteca.

### Flags

`sharedauth.config.ler_flag` é chamado aqui com `estrito=False`. É uma escolha
deste app: `FORCE_HTTPS` e `TRUST_PROXY_HEADERS` são propriedades da
implantação, e um valor irreconhecível cai no padrão em vez de impedir a subida
— ele apenas não liga a folga.

### Custo por render

Dois `context_processor` alimentam a casca de todas as telas, e os dois têm
guarda de custo, porque o que roda em toda página roda muitas vezes:

- `_collector_heartbeat_context` só consulta nos endpoints de
  `HEARTBEAT_ENDPOINTS` (`app/__init__.py`), que são os que de fato mostram o
  pulso do coletor;
- `_theme_context` guarda o tema na sessão depois da primeira leitura. Quem
  grava o tema em Configurações chama `esquecer_tema_da_sessao()`, então a troca
  aparece na página seguinte sem esperar a sessão expirar.

## Módulos

### `app/routes`

Recebe requisições, interpreta formulário e filtro, chama o domínio e monta a
resposta. Cinco blueprints: `portfolio` (a maior parte das telas), `options`,
`auth`, `users` e `account`.

O blueprint `portfolio` é definido em `app/routes/__init__.py`, e não em um dos
módulos, de propósito: `positions.py`, `transactions.py`, `tables.py`,
`quotes.py`, `settings.py`, `health.py` e os demais penduram cada um uma fatia
de rotas na **mesma** instância. Os nomes de endpoint continuam
`portfolio.<view>` mesmo com a implementação repartida, e os templates não mudam
quando um arquivo é dividido.

`app/routes/helpers.py` é o ponto comum das rotas: consultas reaproveitadas,
filtros selecionados, séries de preço, eventos do extrato e os dados dos
gráficos. É o maior arquivo da camada e o lugar onde uma consulta cara aparece
para vários leitores de uma vez.

### Domínio financeiro

`app/core/domain.py` é o núcleo puro: custo médio ponderado, resultado de operação,
métricas de posição, replay de extrato e plano de encerramento. Sem Flask, sem
ORM, sem I/O. `Decimal` do início ao fim, com arredondamento explícito — a
política de arredondamento é do domínio, não do driver.

Em volta dele:

| Módulo | Responsabilidade |
|---|---|
| `app/positions/closure.py`, `app/options/closure.py` | ciclo de vida da posição: abertura, aumento, ajuste e encerramento total ou parcial |
| `app/positions/ledger.py` | preserva o extrato antes de a posição encerrada ser apagada |
| `app/positions/holdings_history.py` | quantidade histórica e fluxo, base do TWR |
| `app/performance/monthly.py` | reduz a série diária a um ponto por mês |
| `app/performance/risk.py`, `app/options/greeks.py` | KPIs de risco e sensibilidades de opção |
| `app/positions/portfolio.py`, `app/options/portfolio.py` | agregação para exibição, por corretora e por mercado |
| `app/performance/dividends.py` | proventos por período e por ticker |
| `app/quotes/history.py` | o que importar de cotação e a gravação única por (ticker, dia), usada pela tela, pela CLI e pelo coletor |
| `app/patrimonio/fotografia.py` | a fotografia do patrimônio publicada ao NetWorth, hoje ou numa data passada |
| `app/core/validation.py`, `app/core/presentation.py` | entrada e saída: parse de decimal, filtros Jinja |

Os dois módulos de encerramento mantêm **três registros em dia, sempre juntos e
nunca nas rotas**: `Position` (o estado consolidado), `PositionMovement` (o
extrato que explica como se chegou nele) e `Transaction` (o que a aba Transações
mostra). Uma rota que atualizasse um deles sozinha produziria uma carteira que
não bate com o próprio extrato.

`app/positions/ledger.py` existe porque encerrar uma posição por inteiro a
apaga, e o extrato vai junto em cascata. O relatório de performance precisa
desses lançamentos para incluir posições encerradas — sem eles a série teria
viés de sobrevivência, mostrando só o que deu certo.

Duas convenções valem para todo o cálculo estatístico: contabilidade é
`Decimal`; modelo contínuo (desvio padrão, percentil, covariância) é `float`
internamente e vira `Decimal` na fronteira de saída. E o drawdown da carteira é
medido sobre o índice TWR, nunca sobre o patrimônio bruto — um aporte grande
criaria um pico artificial, e uma retirada pareceria uma perda que nunca
aconteceu. Os próprios módulos explicam o porquê em detalhe; os contratos
normativos estão em [`docs/planilha-acoes.md`](planilha-acoes.md) e
[`docs/planilha-opcoes.md`](planilha-opcoes.md).

### Coleta de cotações

Em `app/collector/`, `rtd.py` define o instrumento e a leitura normalizada;
`rtd_direct.py` lê o RTD direto do `IRtdServer` do ProfitPro (sem Excel) e é o
único provedor; `providers.py` mantém um provedor aberto entre ciclos;
`loop.py` é o laço único de coleta e `profit_detector.py` responde se o
ProfitChart está aberto; `heartbeat.py` resume a última leitura persistida
**sem expor valor de cotação**; `settings.py` valida intervalos e agenda.

`app/quotes/history_import.py` é a outra fonte de preço: séries diárias do
Yahoo, usadas por performance e risco. Ele decide qual preço gravar — ajustado
só para ticker de referência, que ninguém detém e contra o qual nunca haverá
renda cadastrada. A data de cada barra é a do fuso da bolsa informado pelo
próprio Yahoo (o câmbio é carimbado à meia-noite de Londres, não em UTC), e o
dia corrente não é importado: só entram dias encerrados. O dia de hoje dos
tickers com RTD continua chegando pelo coletor.

### Apoio

`app/accounts/authorization.py` (papel `admin`, sobre
`sharedauth.access.requer_papel`), `app/accounts/users.py` (contas),
`app/core/privacy.py` (ocultação de valores na tela), `app/core/themes.py`,
`app/core/instrument_status.py`, `app/core/pricing_settings.py`,
`app/quotes/reference_data.py` e `app/cli.py` (`auditoria`, `poll-rtd`,
`probe-rtd-direct`, `import-position-history`, `users`).

## O agente RTD no Windows

COM/RTD não roda no contêiner Linux. Essa é a única exceção ao runtime em
Docker, e ela foi desenhada para não ampliar a superfície do servidor:

```text
ProfitChart (RTD/COM) → agente Windows → HTTPS autenticado → aplicação → PostgreSQL
```

### Produção contínua e coleta local sob demanda

Dois processos usam o mesmo laço de `app/collector/loop.py`, cada um com
origem, destino, lock e evento de parada próprios:

- `app.collector.remote_agent` consulta a configuração por HTTPS e entrega
  ao VPS. A tarefa remota inicia no logon/09:40 e pode reiniciar em falha.
  Não cria Flask, não lê `.env` nem depende de PostgreSQL local.
- `poll-rtd` consulta e grava exclusivamente no banco local. A tarefa local
  só começa com `scripts/rtd-local.ps1 -Action Start`, sem gatilhos nem
  reinício automático. Banco/configuração local indisponível encerra o processo.

`-Action Stop` sinaliza um evento Windows: a espera pelo próximo prazo
acorda sem polling, o laço fecha seu provedor e o processo termina. Um ciclo
em andamento conclui antes de sair. O local parado não mantém um serviço ou
thread verificando se deve voltar. Início/parada pertencem ao Windows,
nunca aos workers Flask. Os dois processos leem o RTD direto do `IRtdServer`
do ProfitPro; não há ponte pelo Excel nem escolha de modo.

Ambas as tarefas usam token interativo e `conhost.exe --headless`: COM depende
da sessão do usuário, e não da sessão 0. A instalação remota migra a antiga
tarefa única. Não há alternância de destino, nem consulta periódica ao banco
local feita pela produção.

A escrita de um ciclo continua centralizada em `persist_readings`, incluindo
o snapshot diário. O remoto consulta `/api/collector/configuration`, envia
`/api/collector/quotes` e reporta falhas em `/api/collector/failure`.

Há dois relógios independentes, configurados em **Configurações**. O
**intervalo entre leituras** determina quando o agente pode consultar o
ProfitChart e entregar uma nova cotação. O **intervalo de verificação do
agente** determina apenas quando ele busca pedidos e alterações de configuração
no servidor; essa consulta HTTPS não abre nem consulta o ProfitChart. Um pedido
manual ou uma alteração da configuração de coleta antecipa uma leitura assim
que a próxima verificação a recebe. A agenda limita somente a leitura RTD.

A aba **Ações** não faz polling contínuo: com base na hora da última cotação e
no intervalo entre leituras, o navegador agenda uma única atualização do
fragmento da carteira logo após o próximo ciclo esperado. Se a cotação ainda
não tiver chegado, espera o próximo ciclo de leitura antes de tentar de novo.
Assim o cálculo visual acompanha a entrega de cotações sem conexão persistente
nem requisições a cada poucos segundos.

O servidor nunca inicia conexão para o computador Windows e nunca recebe acesso
ao ambiente local. Os três endpoints exigem tokens Bearer separados: o token de
leitura só consulta configuração, e o token de escrita publica cotações e
falhas. Ambos são comparados com `hmac.compare_digest` e são os únicos isentos
de CSRF — não há navegador nem sessão do outro lado. O corpo é limitado a 512 KB.

`REMOTE_COLLECTOR_ENABLED` identifica a instância receptora no VPS. Sua tela
pode pausar/retomar via `collector_paused`, mantendo o agente disponível.
A instância local orienta Start/Stop no Windows e recusa a antiga escrita de
pausa. Não existe controle web capaz de iniciar um processo local parado.
O POST legado de troca de destino responde 410.

O arquivo de estado remoto só é regravado se agenda ou intervalo de
verificação mudarem. Em indisponibilidade remota o agente espera o próximo
prazo de configuração; um prazo de cotação vencido não provoca laço ocupado.
O provedor RTD reconecta os tópicos a cada ciclo e espera o primeiro snapshot
completo antes de publicar: um campo ainda ausente não vira cotação.

**Sem o coletor, a aplicação continua utilizável.** Cotações aparecem
indisponíveis ou desatualizadas, e nenhum cadastro depende delas. O estado
exibido vem do pulso persistido, não de uma sondagem do host.

## Persistência

`app/models.py` define, além dos enums do domínio:

| Tabela | Papel |
|---|---|
| `users` | contas, papel e estado de acesso |
| `app_settings` | infraestrutura e agenda globais do coletor; campos pessoais antigos preservados para adoção do legado |
| `user_preferences` | tema, taxa de cálculo e comparação privados por usuário |
| `user_ticker_entitlements` | primeira posse confirmada; preserva acesso à cotação após encerramento ou exclusão |
| `brokers`, `tickers` | referências globais, mantidas por administradores |
| `portfolios`, `portfolio_tickers` | carteiras privadas e seus catálogos; associação de catálogo não concede acesso a preços |
| `positions`, `position_movements` | posição de ações e seu extrato |
| `option_expirations`, `option_contracts`, `option_positions`, `option_position_movements` | o mesmo par, para opções |
| `transactions` | o que a aba Transações mostra |
| `dividends` | proventos, por tipo de renda; `origin` (`planilha` ou `cb`), `origin_id`, `gross_amount`, `withheld_amount` e `superseded_at` (revisões `20261018_0031` e `20261018_0032`) |
| `quotes`, `option_quotes` | última leitura global por ticker ou contrato; leituras atrasadas não substituem as mais recentes |
| `quote_history` | série diária de preço |
| `position_ledger_archive` | extrato preservado de posição encerrada |
| `position_movement_archive` | cópia integral dos movimentos de ações encerradas após a revisão `20260928_0024` |
| `execution_imports`, `executions` | negócios importados do myProfit e os lotes que os trouxeram (revisão `20261018_0030`); não são posição nem transação |
| `myprofit_positions`, `reconciliation_notes` | a "Posição detalhada" do myProfit por data e as diferenças da conciliação que o mantenedor explicou (revisão `20261018_0033`); só a conciliação mensal lê |

A transação é delimitada no caso de uso que inicia a escrita — nunca em camada
inferior, nunca aberta durante uma chamada externa. Invariantes concorrentes são
protegidas no banco: a gestão de contas serializa com advisory lock
(`_ADMIN_MUTATION_LOCK`) para impedir que duas requisições simultâneas removam o
último administrador.

`pool_pre_ping` está ligado: sem ele, uma conexão que sobrou morta no pool
depois de o PostgreSQL reiniciar só é descartada quando o SQLAlchemy tenta
usá-la, e a requisição que a pegou responde 500.

O schema evolui só por revisões em `migrations/versions/`. Banco vazio nasce de
`alembic upgrade head`, nunca de `create_all()` ou `stamp`. Backup, retenção e
restauração pertencem ao BackupRestore, projeto irmão, e não são replicados
aqui.

## Execuções do myProfit e conferência com o CB

Decisão de fronteira (18/10/2026, corte em 01/01/2026): o Controle Bancário
(CB) é dono de **todo movimento de dinheiro**, inclusive o das contas de
corretora; o CRV é dono dos **papéis** (posição, preço médio, resultado por
operação, opções, risco). A compra e a venda têm dois registros: a liquidação
no CB e a operação aqui. As operações passarão a entrar no CRV pelo relatório
**Histórico de investimentos** do myProfit (que lê a B3), em vez de digitadas.
O cálculo de IR continua no myProfit; nada daqui o faz.

Esta etapa só **registra** as execuções e **confere** cada pregão contra a
liquidação do CB. Ela não altera posições, movimentos, transações nem
proventos; aplicar as execuções às posições é uma etapa posterior, com ensaio e
backup.

### Tabelas

| Tabela | O que guarda |
|---|---|
| `execution_imports` | um lote gravado: nome do arquivo, período, `sha256`, linhas lidas, criadas e alteradas, data. Só existe lote quando algo foi gravado |
| `executions` | uma linha por negócio: corretora, data do pregão, documento (coluna `Número`, p. ex. `B3_GENIAL_27012026`), ativo, grupo, lado, quantidade (negativa na venda), preços e totais com e sem taxas, day trade, moeda, ordem da linha no documento, `content_hash` e `in_scope` |

`total_with_fees` já vem com o sinal do caixa (crédito positivo, débito
negativo) e é a verdade da conferência. `side` (Compra/Venda) e `cash_operation`
(Crédito/Débito) ficam como o relatório os trouxe: os day trades de futuro têm
os dois em sentidos aparentes opostos e não são "corrigidos". Todo valor é
`numeric(24, 8)` com `CheckConstraint` contra `NaN`/`Infinity`; `owner_id` está em
tudo e a chave estrangeira composta `(import_id, owner_id)` impede ligar uma
execução ao lote de outro dono.

`in_scope` marca o que a conferência e o uso futuro consideram: **ações, opções
e ETF da B3**, sem o documento de aluguel (`B3_ALUGUEL_*`, tarifas de centavos).
Índice futuro, Tesouro Direto e o exterior (Ações EUA, ETF USA) ficam gravados e
marcados como fora de escopo.

### Importador (`app/executions/myprofit.py`)

Lê o XLSX com `openpyxl` (limite de 2 MB). As duas primeiras linhas são
`Emitido por: <nome> - <CPF>` e `Período: dd/mm/aaaa até dd/mm/aaaa`: só as
duas datas do período são lidas, e o nome e o CPF **nunca** vão para o
resultado, para mensagem de erro, para log nem para o banco (o nome do arquivo
gravado também perde qualquer sequência que pareça CPF). A terceira linha é o
cabeçalho, com as 16 colunas exatas do relatório; faltando uma, o arquivo é
recusado com o nome dela. A data de negociação vem como número serial do Excel
(46028 = 06/01/2026). Grupo desconhecido, quantidade zero, valor não finito ou
sinal impossível recusam a linha, e **uma linha recusada impede a gravação do
arquivo inteiro**.

**Idempotência.** O relatório não traz identificador por negócio, e há linhas
legitimamente iguais no mesmo documento (quatro vendas de HODL11 com a mesma
quantidade, por exemplo). A linha é identificada por **documento + ordem da
linha no documento**, e o `content_hash` (SHA-256 dos campos do negócio) diz se
ela mudou. A restrição única é `(owner_id, document, line_order)`, mais forte
que a chave `(dono, documento, ordem, hash)` do desenho: o mesmo par com outro
hash é uma linha **alterada**, não uma segunda linha. Reexecutar a importação
não duplica nem perde linha; linhas gravadas que o arquivo não traz mais são
apenas contadas, nunca apagadas. A instituição do relatório (GENIAL, XP, Avenue,
Nomad) casa com a corretora do CRV por uma tabela configurável
(`MYPROFIT_INSTITUICOES`, JSON no ambiente); instituição sem correspondência
impede a gravação.

### Entrada: ensaio por padrão

`flask --app app:create_app importar-negocios-myprofit <arquivo> [--usuario U]
[--gravar]` e a tela `/executions/import` (login, CSRF e dono da sessão). Os dois
fazem primeiro o **ensaio** (novas, repetidas, alteradas, fora de escopo,
pendências) e só gravam com `--gravar` ou com o botão "Gravar", que exige enviar
de novo o mesmo arquivo (o `sha256` do ensaio precisa bater). A gravação é uma
transação do caso de uso (`app/executions/service.py`); uma gravação concorrente
das mesmas linhas é recusada pela restrição única.

### Cliente do CB e conferência por pregão

`app/patrimonio/cb_client.py` lê o `GET {CB_PATRIMONIO_URL}/snapshot` do contrato
`patrimonio/v4` com o Bearer `CB_PATRIMONIO_TOKEN` (segredo de arquivo,
`CB_PATRIMONIO_TOKEN_FILE`), somente leitura, e filtra os lançamentos da
categoria "Operações em Bolsa". A liquidação de BM&F (`Bm&F` na descrição) fica
fora, como os futuros. A data do pregão vem de `settlement.pregao_date`, campo
opcional que o CB está acrescentando ao `cash_entry`; enquanto ele não está em
produção, `pregao_da_descricao` a extrai de `Operações Bolsa D+2 Pr 27/01/2026
Nc. 543216` (função pura, isolada e pronta para ser removida). Sem URL e token, a
tela mostra **"CB não configurado"** e lista só as execuções do CRV.

`app/executions/conferencia.py` compara, por **corretora, data do pregão e
moeda**, a soma assinada da liquidação do CB (conta(s) da corretora) com a soma
de `total_with_fees` das execuções em escopo. Estados: **confere** (diferença
absoluta até a tolerância, `CONFERENCIA_TOLERANCIA`, padrão R$ 5,00),
**diferença**, **só no CB** e **só no CRV**; sem CB disponível o estado é "sem
comparação". A diferença (CRV − CB) é o **custo implícito** do pregão: o que a
corretora e a liquidação levaram além das taxas da B3 que o myProfit já conta (o
CRV não guarda custos). A correspondência corretora ↔ contas do CB é por
instituição, em `DEFAULT_BROKER_INSTITUTIONS` (Genial = conta 8; XP = SCP XP
Investimentos, conta 10; Nomad = 13; Avenue = 12 e 26; Revolut = 14) e pode ser
substituída por `CB_CORRESPONDENCIA_CONTAS` (JSON). A tela é
`/executions/reconciliation` (página ou fragmento HTMX, filtros de corretora e
período) e o comando é `flask --app app:create_app conferir-pregoes [--usuario U]
[--corretora C] [--inicio AAAA-MM-DD] [--fim AAAA-MM-DD]`.

Rede e token no VPS1 (em produção desde 18/10/2026): o `web` do CRV entra na rede
interna `patrimonio-internal` e o `web` do CB responde nela como `cb-api:8000`,
então `CB_PATRIMONIO_URL=http://cb-api:8000/patrimonio/v4` é alcançável sem sair do
host. O token é uma cópia do `PATRIMONIO_INTEGRATION_TOKEN` do CB em
`.secrets/cb_patrimonio_token` (`CB_PATRIMONIO_TOKEN_FILE`, só no `web`). O CB
redireciona http para https (`USE_HTTPS`) e confia no cabeçalho do proxy; o salto
interno não passa pelo Nginx, então o cliente declara `X-Forwarded-Proto: https`
nas URLs `http`.

### Conciliação mensal com o myProfit

`docs/conciliacao-mensal.md` é o roteiro. O código fica em `app/executions/`:

* `posicao_myprofit.py` lê a "Posição detalhada" (XLSX ou TSV; o TSV é o que o
  navegador embutido extrai, e `load_sheets` em `myprofit.py` atende os dois
  relatórios). Pura, com a mesma regra de privacidade do importador: a data final
  do período é a data da posição, e o nome e o CPF nunca saem.
* `conciliacao.py` compara, por ativo da B3 (ações, opções e ETF), a posição do
  myProfit com as posições do CRV na mesma data (`crv_holdings(as_of)`, que desfaz
  pelos movimentos e pelas transações fechadas o que veio depois; quantidade exata, custo médio com
  tolerância de 0,1%) e a quantidade vendida nas execuções do período com a
  quantidade fechada nas transações do CRV. Só leitura; `compare_positions`,
  `compare_sales` e `apply_notes` são puras. `ReconciliationNote` guarda a
  diferença explicada, por (tipo, ativo), que passa a "reconhecida".
* Comandos: `importar-posicao-myprofit <arquivo> [--gravar]` (ensaio por padrão; a
  posição da mesma data é substituída), `conciliar-myprofit [--inicio] [--fim]
  [--posicao] [--tudo]` (sai com código 1 se houver pendência) e
  `reconhecer-conciliacao posicao|venda ATIVO "nota" [--remover]`.

### Proventos do CB (a partir de 01/01/2026)

O CB é o dono de todo movimento de dinheiro, inclusive o das contas de
corretora; o CRV é dono dos papéis. Por isso, **de 01/01/2026 em diante** os
proventos em reais das corretoras que o CB cobre (Genial e XP, configuráveis em
`PROVENTOS_CB_CORRETORAS`; corte em `PROVENTOS_CB_CORTE`, AAAA-MM-DD) não nascem
na planilha: são lidos do CB e gravados em `dividends` com `origin = 'cb'`. Dólar
(Nomad), Avenue e tudo anterior ao corte seguem pela planilha (`origin =
'planilha'`) até haver extrato com o evento; as 40 linhas até 31/12/2025 ficam
congeladas e aparecem como o subtotal "Até 31/12/2025 · saldo de corte".

* **Leitura** (`app/patrimonio/cb_client.py`, `read_income_entries`): os
  lançamentos que o CB-1 marcou com `income_kind`/`income_role` no contrato v4
  (`instrument`, `income_kind` aluguel|jcp|dividendo, `income_role` credito|
  irrf_aluguel|corretagem_aluguel|taxa_aluguel|estorno), só os realizados. A
  categoria não é consultada.
* **Valor** (`app/dividends/cb_income.py`, função pura): o provento é o **líquido
  do caixa**. Dividendo e JCP não têm retenção: líquido = crédito. Aluguel:
  líquido = crédito − IRRF do **mesmo ativo, conta e dia** − (corretagem + taxas −
  estornos) da conta no dia, **rateados** entre os créditos de aluguel do dia na
  proporção do valor (maior resto: a soma das partes é exata ao centavo). `amount`
  guarda o líquido, `gross_amount` o crédito e `withheld_amount` o retido. Estorno
  e relançamento do mesmo valor num dia sem aluguel têm efeito zero e são
  ignorados. Retenção sem crédito de aluguel para recebê-la, líquido não positivo
  e estornos maiores que as cobranças do dia vão para a fila de revisão.
* **Fila de revisão**: crédito sem `instrument`, sem tipo, ou com ticker que o
  CRV não cadastra (o comando **nunca cria ticker**) não é gravado. Aparece na
  tela `/dividends/cb` (ensaio ao vivo, somente leitura) e na saída do comando,
  com o que fazer.
* **Importação**: `flask --app app:create_app importar-proventos-cb [--usuario U]
  [--corte AAAA-MM-DD] [--aplicar]`. O padrão é o **ensaio**; `--aplicar` grava só
  linhas `origin = 'cb'`, de forma idempotente (chave `origin_id`, o id do
  lançamento de crédito, com índice único parcial por dono; uma linha cujo valor
  mudou no CB é atualizada, e uma que sumiu do CB é só listada, nunca apagada).
  A chave de importação da planilha (`import_key`) virou índice parcial
  `WHERE origin = 'planilha'` na revisão `20261018_0032`, porque dois créditos
  iguais do CB no mesmo dia são dois lançamentos.
* **Substituição da planilha** (`app/dividends/substituicao.py`, pura, e
  `cb_sync.py`): as linhas de planilha em reais, nas corretoras do CB, com
  pagamento a partir do corte, seriam contadas em dobro. `flask ...
  substituir-proventos-planilha-2026 [--aplicar] [--desfazer] [--sim]` pareia cada
  crédito do CB com as linhas da planilha (mesma corretora, ativo e tipo; data da
  planilha de 0 a 5 dias antes; valor igual ao crédito em dividendo e JCP e a
  57,5% do bruto em aluguel, tolerância de centavos; um crédito pode juntar até
  quatro linhas). Só as pareadas são marcadas (`superseded_at`); linha sem par fica
  ativa e listada como **divergência**. Nada é apagado e `--desfazer` reverte.
  Roda depois de `importar-proventos-cb --aplicar` e, em produção, só com backup
  validado e autorização do mantenedor.
* **Telas**: cada linha mostra a origem; a do CB é **somente leitura** (editar e
  excluir respondem 403: a correção é no CB), a substituída não aparece nem se
  edita; o valor mostra líquido, bruto e retenções; formulário e importação por
  planilha recusam pagamento em reais das corretoras do CB a partir do corte,
  apontando o CB. YOC, DY e cotas das linhas sem cotas (as do CB) são calculados
  sobre as posições (`app/dividends/yields.py`) com a mesma definição da
  importação da planilha, sem gravar.

### Esquema `leitura`: o contrato para quem lê o banco de fora

Quem lê o PostgreSQL direto (hoje, o FinancasMCP, por um usuário `mcp_leitura`
só de leitura) não deve ler as tabelas: acopla o próprio SQL ao schema e
reescreve as regras do domínio. Em 24/09/2026 uma coluna removida quebrou o
`crv_carteira` em produção. A revisão `20261003_0027` cria o esquema `leitura`
com views que carregam essas regras, e o usuário de leitura recebe `SELECT` só
nelas.

| View | O que carrega |
|---|---|
| `leitura.posicao` | posições abertas de ações e opções juntas: preço que vale (última cotação ou fechamento anterior), valor de mercado e resultado em aberto com o sinal do lado (`SELL` é negativo) |
| `leitura.operacao`, `leitura.provento` | operações com o resultado bruto gravado, e proventos por tipo, com os campos opcionais nulos quando não informados. Desde a revisão `20261018_0031` a view exclui as linhas de planilha **substituídas** pelo CB (senão o mesmo dinheiro apareceria duas vezes) e `valor` das linhas do CB é o líquido do caixa |
| `leitura.cotacao`, `leitura.cotacao_historico`, `leitura.opcao`, `leitura.cotacao_opcao` | cotação atual e série diária, contratos de opção e suas cotações |
| `leitura.movimento_posicao`, `leitura.movimento_opcao`, `leitura.posicao_historica` | extrato das posições e o histórico preservado das encerradas |
| `leitura.ativo`, `leitura.carteira`, `leitura.corretora`, `leitura.carteira_ativo` | cadastros |

Ficam de fora, de propósito, usuários, senhas, sessões, preferências e
auditoria. O PostgreSQL recusa `DROP COLUMN` e `ALTER ... TYPE` de coluna que uma
view lê. Quem precisar mudar uma coluna lida aqui escreve a revisão dependendo
de `20261003_0027`, recria a view e atualiza `tests/test_esquema_leitura.py`: a
quebra deixa de aparecer em produção, no leitor, e passa a reprovar a revisão na
suíte, que aplica todas elas a um banco vazio. Uma view nova só chega ao usuário
de leitura depois de rodar de novo `python -m financas_mcp.usuario_leitura`
(ver o FinancasMCP).

## Segurança e implantação

- **autenticação por padrão**: `requer_login` nega toda requisição sem sessão;
  `PUBLIC_ENDPOINTS` é a lista curta e explícita do que fica de fora — login,
  health, os três endpoints do agente e os estáticos;
- **autorização no servidor**: `operador` opera a carteira, `admin` também
  acessa Configurações e contas. Esconder o item no template é apresentação, não
  controle: botão ausente não impede ninguém de chamar a rota;
- **matriz de acesso**: somente `admin` cria, altera, desativa ou redefine
  contas, muda configurações e aciona o coletor; `operador` cria, edita,
  encerra e exclui itens da carteira, mas não administra acesso nem o runtime;
- escrita de navegador exige CSRF; a CSP não admite `unsafe-inline`, e todo
  asset é local;
- cookies `HttpOnly` e `SameSite=Lax`; sessão de 12 horas, inclusive o
  "lembrar-me" — o padrão do Flask-Login seriam 365 dias, o que não cabe num
  sistema com posição, custo e provento pessoais;
- em produção, TLS termina no Nginx, e `FORCE_HTTPS`/`TRUST_PROXY_HEADERS` ligam
  cookies `Secure` e o tratamento dos cabeçalhos do proxy. A fábrica recusa
  subir com `TRUST_PROXY_HEADERS=true` e `FORCE_HTTPS=false` juntos (CRV-03):
  confiar no proxy sem exigir HTTPS deixaria o cookie de sessão sem `Secure`.

**Isolamento financeiro (12/09/2026):** carteira, posições, transações,
proventos, movimentos e arquivos pertencem ao usuário autenticado. Toda rota
financeira consulta e altera somente esse escopo; `admin` não ganha leitura de
outro usuário por seu papel. Referências de mercado e sua manutenção são
globais: administração e coletor podem escrevê-las, enquanto a leitura de
cotações exige o vínculo histórico usuário–ticker. Preferências de apresentação
e análise também pertencem ao usuário; agenda e infraestrutura do coletor são
globais.

Para ações, a cotação global preserva separadamente OCP (posição comprada) e
OVD (posição vendida), com o horário observado de cada lado. A carteira usa o
lado da posição e uma leitura atrasada não substitui um valor mais recente. O
último negócio e seu histórico continuam globais por ticker.

Cada usuário altera tema, taxa de cálculo e referência para Beta em
**Preferências** (`/preferences`). O prazo de alerta de cotação é global e só
é alterado por administrador em `/settings`, junto da configuração do coletor.
FKs compostas impedem relações financeiras entre donos
distintos, inclusive em escritas fora das rotas; nomes de carteira são únicos
por usuário. IDs explícitos fora do escopo recebem 404, e IDs de filtro/formulário
malformados recebem 400 com aviso também em HTMX. IDs fora da faixa no caminho
da rota não chegam ao banco.

Os relatórios em `docs/security-audit/` registram decisões históricas. Suas
declarações de acervo comum ou exceções de “uso pessoal” foram substituídas por
este contrato de isolamento e não autorizam exceções de segurança.

**Modo discreto**, agora disponível no popover de filtros do cabeçalho, mascara a leitura casual da tela e cobre
os gráficos. Ele não é uma fronteira de segurança; os dados continuam na
resposta/DOM para que os gráficos possam ser renderizados no navegador. Quem
precisa de confidencialidade contra inspeção do navegador precisa de um
contrato distinto, com dados omitidos no servidor.

Cadastros sem vínculo são excluídos. Se alguma chave estrangeira protege um
fato financeiro ou o extrato de posições encerradas, a tentativa de remoção
arquiva o cadastro: ele sai dos novos formulários, preserva o histórico e pode
ser reativado na tela de cadastro.

A imagem roda sob Gunicorn (dois workers, quatro threads), com usuário não-root
e filesystem raiz somente leitura; testes, requisitos de desenvolvimento,
segredos e certificados locais não entram no estágio `runtime`. O PostgreSQL
roda como `postgres`, também com raiz somente leitura, todas as capabilities
removidas, `no-new-privileges` e os diretórios transitórios em `tmpfs`; o volume
de dados é a única área gravável persistente. O Compose publica a aplicação em
`127.0.0.1:5301` e o banco em `127.0.0.1:5302`.

O rate limit da aplicação usa `memory://`: com dois workers, o contador é por
processo, não é compartilhado e zera a cada reinício. **A proteção coordenada
fica na borda** — o vhost deste projeto, versionado em
`../_manutencao/vps/nginx/controle-renda-variavel`, aplica uma zona
`limit_req` compartilhada ao `POST /login` definida em
`../_manutencao/vps/nginx/conf.d/00-comum.conf`, e isso é requisito da
implantação atual. Outra topologia precisa manter proteção equivalente na borda
ou adotar armazenamento compartilhado para o limitador. As rotas fora do gate de
sessão têm limite próprio aplicado pela própria aplicação: as três do agente
coletor (`/api/collector/*`), `60 per minute; 2000 per hour`, e as rotas v4 de
atividades, mudanças e extrato, `60 per minute; 1200 per hour`, que valem também
para quem apresenta o token, pelo motivo escrito na seção da publicação.

Detalhes de operação, publicação e verificação estão em
[`docs/deployment-vps.md`](deployment-vps.md).

## Critérios para evolução

### Envios de aporte

Os formulários de nova posição (ações e opções) identificam cada intenção por
`submit_token`. A chave `(dono, instrumento, token)` e o hash da entrada ficam
em `position_submissions`, na mesma transação do movimento e da posição. Um
reenvio com a mesma entrada não grava novamente; reutilizar a chave com outros
valores é conflito. Outra intenção recebe outra chave, mesmo com valores iguais.
Confirmações e falhas fazem rollback, portanto não consomem a chave. Envios sem
chave válida são recusados; formulários abertos antes da implantação precisam
ser reabertos. As chaves não expiram automaticamente, para não permitir replay
tardio. A tabela guarda só o hash, não uma cópia do formulário.


- regra financeira deve ser testável sem requisição HTTP e sem banco;
- a transação abrange o caso de uso completo que altera dados;
- mudança persistente cria nova revisão Alembic, aditiva e imutável depois de
  aplicada;
- cálculo alterado é conferido contra os exemplos normativos dos contratos
  funcionais, com teste de domínio proporcional;
- o que roda em toda página precisa de justificativa de custo — uma agregação
  sem leitor envelhece sem que nada falhe;
- nova abstração precisa reduzir complexidade concreta do código atual, não
  antecipar uma futura;
- código que sirva a dois ou mais dos projetos irmãos e não dependa de banco ou
  domínio é candidato ao SharedAuth; pode nascer aqui e subir quando provar
  que serve aos outros.
