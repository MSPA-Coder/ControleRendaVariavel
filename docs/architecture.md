# Arquitetura

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
app/domain e os módulos de cálculo    regras financeiras, sem Flask e sem ORM
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

O agente RTD (`/api/collector/*`) e o resumo de patrimônio
(`GET /patrimonio/v1/resumo`) falam JSON, e as duas são máquina a máquina:
não têm sessão, e a permissão delas é um token conferido em tempo constante
dentro da própria view. Estão declaradas em `PUBLIC_ENDPOINTS` — uma rota nova
nasce protegida, e entrar nessa lista é decisão consciente, com o motivo
escrito.

O resumo de patrimônio publica **posições e proventos** para um consolidador
externo, que soma isto ao caixa publicado pelo Controle Bancário. Ele não
escreve nada, não recebe nada e não conhece o consolidador. O contrato é
`patrimonio/v1`: todo valor viaja como **texto** (`float` não representa 0,10),
titular e instituição são identificados pelo **nome normalizado** — o que os
dois sistemas compartilham — e nada é somado entre moedas.

Três coisas ficam **sempre** de fora, com a contagem no envelope: carteira
simulada (não é patrimônio), opções (ainda não publicadas) e posição sem
cotação. Omissão contada é omissão visível.

`PATRIMONIO_TITULAR` é obrigatório para publicar. Aqui `owner_id` aponta para o
**usuário** do aplicativo, e do outro lado titular é a pessoa dona do dinheiro:
são conceitos diferentes com o mesmo nome, e a rota prefere recusar a adivinhar.

### O endereço que chega à barra

Um formulário HTML serializa todos os seus campos ao ser enviado, inclusive os
que estão vazios ou no valor padrão. Sem tratamento, a Carteira sem nenhum
filtro aplicado aparecia na barra como
`/?portfolio_id=all&broker=&return_days=365` — nada ali foi escolhido por
ninguém.

`app/core/url_limpa.py` monta o endereço equivalente sem esse ruído, e o
`after_request` `_canonizar_url` (em `app/__init__.py`) o entrega no cabeçalho
`HX-Replace-Url`. O navegador troca a barra sem recarregar. O filtro continua na
URL quando é um filtro de verdade: `?broker=XP` aparece exatamente quando
alguém escolheu XP.

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

### JavaScript próprio

`app/static/app.js` cobre só o que HTML e HTMX não resolvem: menu, foco,
ocultação de valores, confirmação de ação destrutiva e o desligamento do estilo
que o HTMX injetaria sem nonce. Não renderiza dado nenhum.

Os quatro arquivos `*-chart.js` desenham os gráficos com Chart.js local. Eles
recebem a série pronta do servidor, em atributos `data-*` do contêiner do
gráfico — nunca em script inline, que a CSP não admite. Não calculam nada e não
chamam a aplicação.

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

`SECRET_KEY`, a senha do PostgreSQL e o token do agente vêm de arquivo, nunca do
ambiente do contêiner: `NOME_FILE` aponta o caminho e
`sharedauth.secrets.resolver_segredo` o lê. `NOME` direto continua aceito para
execução manual e injeção de teste, mas não é o contrato do Compose.

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

- `_collector_heartbeat_context` só consulta nos cinco endpoints que de fato
  mostram o pulso do coletor;
- `_theme_context` guarda o tema na sessão depois da primeira leitura. Quem
  grava o tema em Configurações chama `esquecer_tema_da_sessao()`, então a troca
  aparece na página seguinte sem esperar a sessão expirar.

## Módulos

### `app/routes`

Recebe requisições, interpreta formulário e filtro, chama o domínio e monta a
resposta. Quatro blueprints: `portfolio` (a maior parte das telas), `options`,
`auth` e `users`.

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
| `position_closure.py`, `option_position_closure.py` | ciclo de vida da posição: abertura, aumento, ajuste e encerramento total ou parcial |
| `position_ledger.py` | preserva o extrato antes de a posição encerrada ser apagada |
| `holdings_history.py` | quantidade histórica e fluxo, base do TWR |
| `monthly_performance.py` | reduz a série diária a um ponto por mês |
| `risk.py`, `greeks.py` | KPIs de risco e sensibilidades de opção |
| `portfolio.py`, `option_portfolio.py` | agregação para exibição, por corretora e por mercado |
| `dividend_report.py` | proventos por período e por ticker |
| `validation.py`, `presentation.py` | entrada e saída: parse de decimal, filtros Jinja |

Os dois módulos de encerramento mantêm **três registros em dia, sempre juntos e
nunca nas rotas**: `Position` (o estado consolidado), `PositionMovement` (o
extrato que explica como se chegou nele) e `Transaction` (o que a aba Transações
mostra). Uma rota que atualizasse um deles sozinha produziria uma carteira que
não bate com o próprio extrato.

`position_ledger.py` existe porque encerrar uma posição por inteiro a apaga, e o
extrato vai junto em cascata. O relatório de performance precisa desses
lançamentos para incluir posições encerradas — sem eles a série teria viés de
sobrevivência, mostrando só o que deu certo.

Duas convenções valem para todo o cálculo estatístico: contabilidade é
`Decimal`; modelo contínuo (desvio padrão, percentil, covariância) é `float`
internamente e vira `Decimal` na fronteira de saída. E o drawdown da carteira é
medido sobre o índice TWR, nunca sobre o patrimônio bruto — um aporte grande
criaria um pico artificial, e uma retirada pareceria uma perda que nunca
aconteceu. Os próprios módulos explicam o porquê em detalhe; os contratos
normativos estão em [`docs/planilha-acoes.md`](planilha-acoes.md) e
[`docs/planilha-opcoes.md`](planilha-opcoes.md).

### Coleta de cotações

`rtd.py` define o instrumento e a leitura normalizada; `rtd_direct.py` lê o RTD
direto do `IRtdServer` do ProfitPro (sem Excel) e é o único provedor;
`providers.py` mantém um provedor aberto entre ciclos; `loop.py` é o laço único
de coleta e `profit_detector.py` responde se o ProfitChart está aberto;
`heartbeat.py` resume a última leitura persistida **sem expor valor de
cotação**; `settings.py` valida intervalos e agenda.

`quote_history_import.py` é a outra fonte de preço: séries diárias do Yahoo,
usadas por performance e risco. Ele decide qual preço gravar — ajustado só para
ticker de referência, que ninguém detém e contra o qual nunca haverá renda
cadastrada.

### Apoio

`authorization.py` (papel `admin`, sobre `sharedauth.access.requer_papel`),
`user_management.py` (contas), `privacy.py` (ocultação de valores na tela),
`themes.py`, `instrument_status.py`, `pricing_settings.py`, `reference_data.py`
e `cli.py` (`poll-rtd`, `probe-rtd-direct`, `import-position-history`, `users`).

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
ao ambiente local. Os três endpoints exigem Bearer token próprio, comparado com
`hmac.compare_digest`, e são os únicos isentos de CSRF — não há navegador nem
sessão do outro lado. O corpo é limitado a 512 KB.

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
| `user_preferences` | tema, taxa de cálculo, comparação e alerta privados por usuário |
| `user_ticker_entitlements` | primeira posse confirmada; preserva acesso à cotação após encerramento ou exclusão |
| `brokers`, `tickers` | referências globais, mantidas por administradores |
| `portfolios`, `portfolio_tickers` | carteiras privadas e seus catálogos; associação de catálogo não concede acesso a preços |
| `positions`, `position_movements` | posição de ações e seu extrato |
| `option_expirations`, `option_contracts`, `option_positions`, `option_position_movements` | o mesmo par, para opções |
| `transactions` | o que a aba Transações mostra |
| `dividends` | proventos, por tipo de renda |
| `quotes`, `option_quotes` | última leitura global por ticker ou contrato; leituras atrasadas não substituem as mais recentes |
| `quote_history` | série diária de preço |
| `position_ledger_archive` | extrato preservado de posição encerrada |

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

Cada usuário altera tema, taxa de cálculo, referência para Beta e prazo de alerta
em **Preferências** (`/preferences`). A configuração administrativa do coletor
continua em `/settings`. FKs compostas impedem relações financeiras entre donos
distintos, inclusive em escritas fora das rotas; nomes de carteira são únicos
por usuário. IDs explícitos fora do escopo recebem 404, e IDs de filtro/formulário
malformados recebem 400 com aviso também em HTMX. IDs fora da faixa no caminho
da rota não chegam ao banco.

Os relatórios em `docs/security-audit/` registram decisões históricas. Suas
declarações de acervo comum ou exceções de “uso pessoal” foram substituídas por
este contrato de isolamento e não autorizam exceções de segurança.

O botão de olho é **Modo discreto**: mascara a leitura casual da tela e cobre
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
ou adotar armazenamento compartilhado para o limitador. As três rotas do agente
coletor (`/api/collector/*`), que ficam fora do gate de sessão, têm limite
próprio de `60 per minute; 2000 per hour` aplicado pela própria aplicação — são
a única superfície alcançável sem sessão.

Detalhes de operação, publicação e verificação estão em
[`docs/deployment-vps.md`](deployment-vps.md).

## Critérios para evolução

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
