# Base compartilhada de engenharia

<!-- SHARED-ENGINEERING-BASE:BEGIN version="1.5" -->

## Governança deste bloco-base

Este bloco estabelece as orientações compartilhadas e permanentes do mantenedor
para seus projetos Python e Flask.

Ele é controlado pelo mantenedor e não deve ser alterado, resumido, removido,
deslocado, enfraquecido ou substituído automaticamente durante implementação,
correção, refatoração, atualização de documentação ou manutenção ordinária.

Uma alteração neste bloco somente é autorizada quando o mantenedor solicitar
explicitamente, na conversa atual, uma mudança na **base compartilhada de
engenharia**. Um pedido genérico para melhorar, atualizar, organizar ou corrigir
o projeto não autoriza modificar este bloco.

Se uma tarefa entrar em conflito com estas orientações:

1. não reescreva a orientação conflitante;
2. explique objetivamente o conflito;
3. proponha a menor exceção necessária;
4. aguarde autorização explícita quando a exceção alterar segurança,
   integridade, persistência, produção ou confiabilidade;
5. registre a exceção na seção específica do projeto.

Arquivos `AGENTS.md` em subdiretórios podem acrescentar comandos e regras
locais, mas não devem enfraquecer esta base. Não crie nem altere
`AGENTS.override.md` sem autorização explícita do mantenedor.

Mudanças aprovadas nesta base devem:

- incrementar sua versão;
- registrar uma justificativa curta;
- ser propagadas deliberadamente aos projetos que usam a mesma base;
- preservar os marcadores de início e fim;
- atualizar a verificação automática de integridade, quando existente.

### Histórico de mudanças

- **1.5** — Substitui o portão único de encerramento por anéis de validação
  proporcionais ao raio da mudança, move os controles de ambiente para CI e
  release, exige que os anéis sejam executáveis e trata a duração da suíte
  como propriedade do projeto.
- **1.4** — Reconhece o truncamento com restauração das linhas semeadas pelas
  migrações como mecanismo válido de isolamento entre testes, permitindo
  construir o schema uma única vez por sessão sem reexecutar as migrações a
  cada teste.
- **1.3** — Classifica testes por risco, prioriza controles críticos e exige correção de falhas preexistentes identificadas na validação.
- **1.2** — Exige bootstrap de bancos novos por revisões Alembic e adoção legada explícita e verificada.
- **1.1** — Acrescenta diretrizes para HTMX e explicita o desenvolvimento
  container-first, mantendo no host somente as ferramentas de orquestração.

A flexibilidade técnica prevista neste documento não autoriza o próprio agente a
apagar ou reescrever as orientações. Soluções alternativas devem ser
implementadas como decisões justificadas ou desvios aprovados, mantendo a base
legível e estável.

## Intenção e liberdade técnica

Os projetos usam Python e Flask, com PostgreSQL como único banco operacional e
Docker para empacotamento e implantação.

PostgreSQL também é o banco utilizado em todos os testes que exercem
persistência. Testes unitários de cálculos, validações e regras puras não usam
banco de dados. SQLite somente é permitido quando for o objeto explícito da
funcionalidade testada, como a leitura de uma base legada; não é usado para
simular PostgreSQL.

### Ambiente de desenvolvimento container-first

O host mantém somente Codex, Git, GitHub CLI, Docker Desktop, editor e os
componentes necessários à execução do Docker. Python, PostgreSQL, `psql`,
Node.js quando necessário, dependências, migrações, testes, lint, análise de
tipos, build e demais ferramentas do projeto são executados em contêineres.

- O projeto deve oferecer comandos Docker Compose reproduzíveis para iniciar a
  aplicação e o banco, aplicar migrações e executar testes e controles de
  qualidade.
- Não presuma que executáveis do projeto estejam instalados ou disponíveis no
  `PATH` do host.
- Uma ferramenta ausente deve ser incorporada à imagem ou ao serviço adequado,
  com versão controlada; não deve ser instalada no host como correção pontual.
- Use volumes e caches somente quando preservarem isolamento e
  reprodutibilidade, evitando arquivos do repositório pertencentes a `root`.
- Serviços de desenvolvimento e teste usam credenciais, redes, bancos e volumes
  separados da produção.
- O acesso ao daemon Docker equivale a acesso privilegiado ao host. Não monte o
  socket do Docker em contêineres nem use modo privilegiado sem necessidade
  concreta, avaliação de risco e autorização explícita.

Essas escolhas são a base atual, não uma proibição de evolução. Bibliotecas,
ferramentas, padrões ou componentes adicionais podem ser adotados quando
trouxerem benefício concreto de segurança, confiabilidade, desempenho,
simplicidade ou manutenção.

Toda divergência relevante deve registrar:

- problema concreto que pretende resolver;
- benefício esperado;
- impacto de compatibilidade;
- riscos e alternativas consideradas;
- estratégia de migração ou rollback;
- testes e validações aplicáveis.

Não transforme uma implementação existente em restrição permanente sem razão de
domínio, segurança, operação ou manutenção.

## Arquitetura proporcional

Use uma fábrica de aplicação e organize funcionalidades em módulos ou
blueprints. O fluxo de referência é:

```text
HTTP -> validação e normalização -> caso de uso -> persistência
```

Esse fluxo orienta responsabilidades, mas não obriga a existência de uma pasta
ou classe para cada camada.

- Rotas tratam HTTP, autenticação, autorização, entrada e resposta.
- Casos de uso ou serviços coordenam regras de negócio e limites transacionais.
- Persistência concentra acesso ao banco e consultas reutilizáveis.
- Templates apresentam dados; não concentram regras de negócio.
- Regras importantes devem ser testáveis sem uma requisição HTTP.
- Prefira composição a herança e funções simples para lógica sem estado.
- Use classes quando houver estado coerente, invariantes, ciclo de vida ou
  polimorfismo real.
- Não crie interfaces, repositories ou camadas vazias para satisfazer um
  desenho teórico.

Application Factory, Service Layer, Unit of Work, Repository, Adapter, Strategy,
Policy e Value Object são padrões disponíveis, não uma lista obrigatória. Adote
um padrão quando ele reduzir uma complexidade concreta ou proteger um contrato
importante.

## Persistência e integridade

PostgreSQL é a fonte de verdade do ambiente operacional e dos testes de
persistência.

- Represente dados estruturados e relacionamentos estáveis com colunas, tabelas
  e relações explícitas.
- Use `JSONB` para dados semiestruturados, atributos variáveis ou documentos
  externos cuja estrutura possa evoluir. Não o use apenas para evitar modelagem
  relacional.
- Proteja invariantes também no banco, quando aplicável, com `NOT NULL`,
  `UNIQUE`, `CHECK`, foreign keys e constraints apropriadas.
- Crie índices a partir das consultas e volumes esperados; não indexe
  mecanicamente todas as colunas.
- Índices GIN ou de expressão para `JSONB` devem corresponder a consultas reais
  e ter benefício verificável.
- Toda alteração persistente de schema usa uma nova revisão Alembic.
- Bancos novos são criados exclusivamente por `alembic upgrade head`; não use
  `create_all()` seguido de `stamp` como atalho de bootstrap operacional.
- A adoção de schema legado sem `alembic_version` é uma operação administrativa
  explícita, com verificação estrutural, backup validado e registro da revisão;
  ela não ocorre automaticamente no startup.
- Migrações autogeradas são candidatas e precisam de revisão manual.
- Não reescreva uma migração que possa ter sido aplicada em outro banco.
- Alterações destrutivas ou transformações de dados reais exigem backup
  validado, plano de migração e autorização explícita.
- Não mantenha adaptadores, condicionais ou tipos especiais apenas para
  compatibilidade com SQLite, salvo quando o produto possuir funcionalidade
  explícita de importação ou leitura desse formato.

## Transações, concorrência e efeitos externos

O caso de uso que inicia uma operação de escrita é responsável por seu limite
transacional. Camadas inferiores não fazem `commit` por conta própria.

- Uma operação composta deve concluir integralmente ou sofrer rollback integral.
- Validações que protegem integridade concorrente não devem existir apenas no
  código; use constraints, locks ou níveis de isolamento quando necessários.
- `Read Committed` é o padrão normal do PostgreSQL.
- Locks, controle otimista, `Repeatable Read` ou `Serializable` devem responder
  a uma condição de corrida identificada e possuir testes no PostgreSQL.
- Quando houver falha de serialização, o caso de uso deve poder repetir a
  transação completa com segurança.
- Não mantenha transações abertas durante chamadas externas lentas sem
  necessidade.
- Banco de dados, e-mail, APIs e dispositivos externos não formam uma única
  transação atômica.
- Quando a consistência com um sistema externo for crítica, considere
  idempotência, retentativas controladas, registro de estado ou padrão Outbox.
- Retentativas automáticas só são seguras para operações idempotentes ou
  protegidas contra duplicidade.

## Testes e qualidade

Use o teste mais rápido que ofereça confiança suficiente, sem substituir o banco
de produção por outro dialeto.

- Testes unitários de cálculos, normalização, validações e regras de domínio não
  usam banco.
- Dependências externas são substituídas por fakes ou adapters controlados
  quando o objetivo não for validar a própria integração.
- Testes de models, repositories, serviços com persistência e rotas que gravam
  dados usam PostgreSQL descartável.
- Migrações, constraints, transações, rollback, concorrência, locks, isolamento,
  `JSONB`, índices e consultas específicas são validados em PostgreSQL.
- A suíte nunca acessa o banco operacional.
- O schema de teste é criado com `alembic upgrade head`.
- `create_all()` somente pode ser usado quando o teste deliberadamente não
  avaliar migrações e isso não reduzir a confiança no schema.
- Cada teste deve ser isolado por rollback transacional, savepoint, schema,
  banco próprio ou truncamento das tabelas com restauração das linhas semeadas
  pelas migrações.
- O schema pode ser construído uma única vez por sessão de teste, desde que o
  mecanismo de isolamento reponha o estado de dados de um banco recém-migrado
  antes de cada teste e que testes que alterem o schema o reconstruam ao
  terminar.
- Testes paralelos não compartilham estado mutável.
- Fixtures criam somente os dados necessários e não dependem da ordem de
  execução.
- Relógio, aleatoriedade, rede e serviços externos são controlados quando
  puderem tornar o teste não determinístico.
- Testes HTTP protegem autenticação, autorização, validação e contratos
  observáveis.
- Testes em navegador ficam reservados às jornadas críticas.
- Correções devem incluir um teste que demonstre a falha quando isso for viável.
- Teste comportamento e invariantes, não detalhes internos acidentais.
- Cobertura é uma rede de segurança, não um objetivo isolado.
- SQLite só aparece em testes de funcionalidades que realmente leem, importam ou
  transformam arquivos SQLite.

Para projetos novos, prefira pytest, Ruff, análise de tipos gradual com mypy ou
ferramenta equivalente, auditoria de dependências e CI. Suítes existentes em
`unittest` podem permanecer quando forem confiáveis; não as reescreva apenas
para uniformizar ferramentas.

### Classificação, manutenção e falhas encontradas

Organize a suíte por risco e finalidade. Cada teste deve pertencer a pelo menos
uma categoria equivalente a: crítico, regra de negócio, segurança, migração e
persistência, contrato observável, smoke de interface, arquitetura ou jornada
E2E. A ferramenta do projeto pode representar essas categorias por marcadores,
pastas, convenções de nome ou outra configuração verificável.

- Testes de dinheiro, regras de negócio, integridade, transações, migrações,
  autorização e segurança são controles críticos e não devem ser removidos
  apenas para reduzir volume ou duração.
- Testes de markup, texto incidental, existência de arquivo ou detalhe interno
  só são mantidos quando protegem um contrato de segurança, acessibilidade,
  compatibilidade ou operação. Consolide testes redundantes nesse nível sem
  reduzir a cobertura do risco correspondente.
- Regressões reais de interface, JavaScript ou atualizações parciais devem ser
  protegidas por teste funcional ou E2E proporcional; uma inspeção estática de
  código não é substituto suficiente quando o risco é de comportamento no
  navegador.
- A CI deve executar primeiro os controles críticos para reduzir o tempo de
  diagnóstico e preservar uma etapa posterior de validação completa, incluindo
  os controles aplicáveis e jornadas E2E críticas.

Falhas preexistentes encontradas em testes, lint, análise de tipos, migrações,
build, auditorias ou smoke tests devem ser investigadas e corrigidas na mesma
tarefa quando a causa estiver dentro do repositório. Após corrigir, execute
primeiro o controle que falhou, depois os controles relacionados e repita a
validação final aplicável. Solicite direção antes de prosseguir somente quando
a correção exigir mudança de produto, ação destrutiva, acesso externo ou
coordenação fora do repositório.

## Estratégia de validação progressiva

A validação deve ser **proporcional ao raio de alcance da mudança**, não
uniforme. Um ajuste de marcação e uma alteração de schema não oferecem o mesmo
risco e não devem custar o mesmo.

Organize os controles em anéis. Cada anel custa mais e protege mais, e existe
um momento natural para cobrá-lo:

| Anel | Quando | Conteúdo |
|---|---|---|
| 1 — edição | a cada alteração | o menor teste que reproduz ou protege o comportamento |
| 2 — commit | antes de cada commit | lint e a suíte rápida; a suíte completa quando a mudança for de raio amplo |
| 3 — push | antes de publicar | suíte completa, lint e análise de tipos |
| 4 — integração | CI e release | cobertura, auditoria de dependências, imagem de produção, smoke da pilha e jornadas E2E |

São de **raio amplo**, e por isso exigem a suíte completa já no anel 2:
schema e migrações, models, dependências, configuração de empacotamento ou de
contêiner, e a própria infraestrutura de teste. Elas podem quebrar qualquer
coisa, então o custo de descobrir tarde é maior que o de rodar cedo.

Mudanças que tocam autenticação, autorização, CSRF ou sessão sempre executam
os controles de segurança, qualquer que seja o anel.

O anel 3 é o que garante a integridade do conjunto: como ele roda a suíte
completa antes de publicar, nenhum commit intermediário escapa da validação —
ele apenas deixa de pagá-la a cada iteração. Por isso o anel 2 pode ser
barato sem que isso reduza a confiança final.

Os anéis devem ser **executáveis**, não apenas descritos: ofereça um comando
por anel e, quando fizer sentido, ligue-os aos ganchos de commit e push do
Git. Uma política que depende de memória a cada tarefa não é uma política.

### Ciclo de desenvolvimento

Durante a implementação:

1. identifique o comportamento, o risco e os consumidores afetados;
2. execute primeiro o menor teste que reproduza ou proteja esse comportamento;
3. depois de corrigido, execute os testes do módulo ou caso de uso relacionado;
4. quando houver persistência, execute os testes PostgreSQL correspondentes;
5. amplie a seleção quando a mudança afetar contratos compartilhados, segurança,
   arquitetura ou múltiplos consumidores.

Durante esse ciclo:

- use saída compacta, como `-q`, `-x` e `--tb=short`;
- interrompa na primeira falha quando isso acelerar o diagnóstico;
- use `--last-failed` apenas para corrigir falhas já conhecidas;
- não calcule cobertura repetidamente;
- não execute E2E, auditoria completa ou build de produção após cada edição;
- não repita uma validação cujo código e dependências relevantes não mudaram;
- mantenha registro dos comandos já executados durante a tarefa.

### Validação de encerramento

Quando a implementação estiver estável, execute o anel 3 uma única vez, sem
filtros parciais como caminhos específicos, `-k`, `--last-failed` ou exclusões
de testes lentos — salvo quando a própria configuração oficial da suíte
separar etapas complementares que também serão executadas.

Uma alteração posterior invalida o anel 3 anterior, mas **não** exige repetir
todos os anéis a cada iteração: execute primeiro o teste que falhou ou foi
afetado, depois os testes relacionados, e repita o anel 3 uma única vez quando
o código voltar a ficar estável.

O anel 4 pertence à CI e ao release. Execute-o localmente apenas quando a
mudança alcançar empacotamento, dependências ou a pilha de execução — nesses
casos ele é parte do encerramento, não um extra.

Mudanças exclusivamente documentais não exigem a suíte completa, salvo quando a
documentação for validada ou consumida por testes, empacotamento ou automação.

### Custo da suíte

Uma suíte cara empurra o time a validar de menos. Trate a duração como
propriedade do projeto, não como fatalidade:

- meça periodicamente os testes mais lentos e corrija gargalos reais;
- verifique se o custo está nos testes ou no *setup* — repetir migrações,
  reconstruir a aplicação ou recriar conexões a cada teste costuma dominar o
  tempo total;
- reutilize um PostgreSQL descartável durante a mesma tarefa quando isso não
  comprometer isolamento;
- aplique migrações novamente durante a iteração somente quando models ou
  revisions mudarem;
- use rollback, savepoints, truncamento, bancos ou schemas separados para
  manter testes independentes;
- adote execução paralela quando o isolamento entre workers estiver garantido,
  por exemplo com um banco por worker;
- preserve uma execução completa em ambiente limpo na CI, mesmo quando a
  validação local já tiver sido concluída.

Falhas preexistentes encontradas em qualquer anel devem ser investigadas e
corrigidas na mesma tarefa quando a causa estiver dentro do repositório.

Resultados bem-sucedidos devem ser apresentados de forma resumida; detalhes
completos são necessários principalmente para falhas.

Ao concluir uma tarefa, informe:

- anéis executados e testes direcionados;
- resultado;
- controles omitidos e respectivos motivos.


## Segurança e interface

- Valide e normalize toda entrada nas fronteiras.
- A autorização é aplicada no servidor; menus e botões são apenas apresentação.
- Escritas originadas no navegador usam proteção CSRF.
- Preserve escape de HTML, CSP, cookies seguros, hosts confiáveis, limites de
  requisição e validação defensiva de uploads.
- Segredos nunca ficam no código, imagem, logs, mensagens de erro ou valores
  padrão de produção.
- Logs não expõem senhas, tokens, conteúdo financeiro ou dados pessoais
  desnecessários.
- APIs e respostas JSON possuem contratos coerentes; mudanças coordenam backend,
  consumidores, testes e documentação.
- HTML deve ser semântico e acessível.
- CSS e JavaScript permanecem em assets próprios quando a política de segurança
  impedir código inline.

### HTMX e interação orientada pelo servidor

HTMX pode ser adotado como mecanismo preferencial para interações incrementais
orientadas pelo servidor quando reduzir JavaScript próprio e preservar a
simplicidade do fluxo Flask e HTML.

- O servidor continua sendo a fonte de verdade. Atributos `hx-*`, estados da
  interface e respostas parciais não substituem validação, autenticação,
  autorização, CSRF, transações ou invariantes de domínio.
- `HX-Request` e demais cabeçalhos do cliente são sinais de negociação de
  apresentação, nunca prova de autorização ou origem confiável.
- Endpoints HTMX usam semântica HTTP coerente: operações seguras não alteram
  estado; escritas usam métodos adequados e proteção CSRF.
- Respostas parciais são produzidas por templates e componentes reutilizáveis,
  sem duplicar regras de negócio no navegador.
- Preserve HTML semântico, acessibilidade, foco, mensagens de erro, estados de
  carregamento e navegação/histórico quando a interação alterar conteúdo ou URL.
- Ofereça resposta completa ou degradação progressiva quando isso for viável e
  trouxer benefício concreto de robustez, acessibilidade ou testabilidade.
- JavaScript complementar fica em assets próprios e é reservado a comportamentos
  que HTMX e HTML não resolvam com clareza. Evite handlers inline quando forem
  incompatíveis com a CSP do projeto.
- Fixe a versão do HTMX. Prefira asset local versionado; quando usar CDN,
  autorize explicitamente a origem na CSP e use integridade do sub-recurso
  quando disponível. Nunca dependa de uma versão `latest` em produção.
- Testes HTTP cobrem tanto requisições normais quanto requisições HTMX nos
  fluxos em que a resposta ou o comportamento diferir.

## Produção e Docker

O artefato de produção é uma imagem construída e reproduzível.

- Use servidor WSGI apropriado; nunca o servidor de desenvolvimento do Flask.
- A imagem de produção contém somente código e dependências de runtime.
- Execute com usuário não-root e menor privilégio possível.
- Não monte o código-fonte do host no contêiner de produção.
- Dados persistentes ficam em volumes ou serviços próprios.
- Segredos são concedidos somente aos serviços que precisam deles,
  preferencialmente como arquivos.
- Preserve health checks, limites de logs, timeouts e desligamento gracioso.
- A aplicação só inicia depois que o banco estiver saudável e as migrações
  tiverem sido aplicadas por uma etapa controlada.
- Bind mounts, debugger e dependências de desenvolvimento pertencem ao Compose
  de desenvolvimento ou Dev Container.
- Use builds multi-stage quando eles reduzirem de forma útil o tamanho, as
  dependências ou a superfície de ataque.
- A infraestrutura de testes usa credenciais, volumes e bancos descartáveis
  separados da implantação operacional.

## Desempenho, confiabilidade e manutenção

- Meça antes de otimizar.
- Investigue paginação, N+1, plano de consulta e índices antes de adicionar
  cache.
- Cache exige política explícita de invalidação.
- Integrações externas usam Adapter e possuem timeout.
- Retentativas e circuit breaker são adicionados conforme o risco real.
- Falhas esperadas ficam observáveis e produzem mensagens seguras.
- Dependências devem ter faixas ou locks reproduzíveis e atualização deliberada.
- Prefira bibliotecas maduras quando elas reduzirem risco.
- Prefira a biblioteca padrão quando ela resolver o problema com clareza.
- Atualize código, testes, migrações e documentação como uma única mudança
  coerente.
- Preserve alterações locais não relacionadas.
- Informe toda validação que não pôde ser executada.

<!-- SHARED-ENGINEERING-BASE:END -->

# Orientações específicas do projeto

## Leitura inicial e fonte de verdade

Antes de alterar código:

1. leia este arquivo por inteiro;
2. leia `README.md`, `pyproject.toml`, `compose.yaml` e as migrações existentes;
3. consulte `docs/planilha-acoes.md` para o contrato funcional reproduzido da
   planilha;
4. preserve a planilha `Trades.xlsm` apenas como referência: ela não é banco de
   dados, dependência de runtime nem destino de escrita.

Em caso de divergência, a ordem de precedência é:

1. solicitação explícita atual do mantenedor;
2. base compartilhada de engenharia;
3. invariantes documentadas em `docs/planilha-acoes.md`;
4. testes automatizados e contratos públicos;
5. implementação existente.

## Produto e tecnologia

Este projeto substitui a aba **Ações** da planilha `Trades.xlsm` por uma
aplicação web para acompanhamento de posições, cotações em tempo real, resultado
e alocação de carteira.

- Backend: Python e Flask com application factory.
- Persistência operacional: PostgreSQL com SQLAlchemy e Alembic.
- Cotações: Adapter RTD isolado do domínio e configurável por ambiente.
- Interface: HTML semântico, CSS e JavaScript próprios, sem regra financeira
  duplicada no navegador.
- Empacotamento e implantação: Docker Compose.

O servidor RTD é um sistema externo. Indisponibilidade, atraso ou valor inválido
devem ser visíveis sem corromper a última cotação válida nem bloquear operações
de cadastro.

## Arquitetura

Mantenha os limites:

```text
HTTP/HTML/JSON -> validação -> casos de uso -> domínio -> persistência
                                      |
                                      +-> adapter de cotações RTD
```

- Fórmulas financeiras ficam em funções de domínio puras e testadas.
- O adapter RTD traduz o protocolo externo para um contrato interno pequeno.
- Rotas não acessam diretamente COM, RTD ou detalhes do PostgreSQL.
- Atualizações periódicas de cotação não fazem `commit` parcial por ativo.
- O frontend recebe resultados calculados pelo backend e não é fonte de verdade.

## Invariantes financeiras

- Valores monetários e quantidades persistidos usam `Decimal`, nunca `float`.
- Cada posição possui ticker normalizado, quantidade não negativa e preço médio
  não negativo.
- Cotação, custo, valor de mercado, resultado e percentuais preservam a precisão
  observada na planilha e têm política explícita de arredondamento.
- Divisão por zero produz estado definido (`None`/não aplicável), nunca erro
  silencioso ou infinito.
- Totais são calculados a partir das posições persistidas; não são armazenados
  como valores independentes.
- Toda tradução de fórmula da planilha deve ter teste com exemplos extraídos da
  própria aba **Ações**.

## Integração RTD

- O ProgID, tópico, intervalo de atualização e timeout são configuráveis.
- A integração deve executar apenas em ambiente Windows quando depender de COM.
- Testes e desenvolvimento sem o servidor usam um fake determinístico.
- Valores recebidos são normalizados e validados antes de entrar no domínio.
- A última atualização e a condição da fonte (`online`, `stale`, `error`) devem
  ficar observáveis na interface e nos logs.
- Retentativas são limitadas e não mantêm transações de banco abertas.

## Preservação dos dados operacionais

Os dados de produção ficam no volume Compose `postgres_data`, declarado no
mesmo `compose.yaml` dos serviços de teste.

- **Nunca** execute `docker compose down --volumes` neste repositório fora de
  um runner descartável: a flag remove o volume operacional junto com os de
  teste.
- Para descartar apenas o ambiente de teste, use
  `docker compose --profile test rm -sf test test-db`.
- O serviço `test-db` é um PostgreSQL separado, sem volume nomeado; a suíte
  usa `TEST_DATABASE_URL` e nunca alcança o banco operacional.

## Interface

A interface é HTML semântico renderizado pelo servidor, com **HTMX** para as
atualizações incrementais e CSS/JavaScript próprios para o que é puramente
visual.

- HTMX fica em `app/static/vendor/`, com **versão fixa** e servido pela
  própria origem: a CSP é `default-src 'self'` e não há exceção para CDN.
- O token CSRF vai em toda requisição HTMX pelo atributo `hx-headers` do
  `<body>` — atributo, não handler inline, que a CSP bloquearia.
- `HX-Request` decide apenas a **forma** da resposta. Use
  `app.routes.helpers.is_htmx_request`; nunca o trate como autenticação,
  autorização ou prova de origem.
- Cada região atualizável tem uma parcial em `app/templates/partials/`,
  usada **tanto** pela página inteira quanto pela resposta HTMX. Não crie uma
  segunda cópia da apresentação.
- Um fragmento que se atualiza sozinho precisa devolver seus próprios
  atributos `hx-*`; sem isso o ciclo morre depois da primeira troca.
- O filtro aponta `hx-get` para a **própria página**, não para uma rota de
  fragmento: é isso que faz o histórico receber a URL real.
- Fontes e bibliotecas são auto-hospedadas pelo mesmo motivo da CSP.
- O JavaScript próprio cobre só o que HTMX não resolve: menu, painéis
  recolhíveis e os gráficos Chart.js. Os gráficos redesenham no evento
  `htmx:afterSwap`, checando o conteúdo do fragmento trocado para não
  duplicar instâncias sobre o mesmo canvas.
- Testes HTTP cobrem os dois caminhos sempre que a resposta diferir.

## Verificação específica

Os anéis de validação são executáveis por `scripts/check.ps1`, que roda tudo
em contêiner e reconstrói as imagens antes de testar — elas copiam o código,
então sem rebuild a execução valida a versão anterior.

| Comando | Anel | Conteúdo | Custo |
|---|---|---|---|
| `.\scripts\check.ps1 quick` | 1–2 | ruff + `tests/unit` | ~15s |
| `.\scripts\check.ps1 commit` | 2 | ruff + unitários; suíte completa se a mudança for de raio amplo | 15s a 35s |
| `.\scripts\check.ps1 push` | 3 | ruff + mypy + suíte completa em paralelo | ~85s |
| `.\scripts\check.ps1 all` | 4 | acima + pip-audit + cobertura + imagem de produção + smoke | ~3min |

Os ganchos versionados em `.githooks` chamam os anéis 2 e 3 automaticamente
(`git config core.hooksPath .githooks` para instalar). Pular um gancho com
`--no-verify` é uma decisão consciente do mantenedor, não rotina.

São de raio amplo neste projeto, e disparam a suíte completa já no commit:
`app/models.py`, `migrations/`, `pyproject.toml`, `compose.yaml`, `Dockerfile`
e `tests/conftest.py`.

A suíte usa um banco PostgreSQL por worker do pytest-xdist, criado sob demanda
a partir de `TEST_DATABASE_URL`. É isso que torna a execução paralela segura:
nenhum worker enxerga ou trunca os dados de outro.

Durante o desenvolvimento, execute primeiro os testes unitários das fórmulas e
do parser RTD. Quando o RTD real estiver acessível, valide ao menos um ticker
conhecido sem registrar dados financeiros sensíveis nos logs.

## Desvios aprovados da base compartilhada

- **Controlador e coletor RTD/COM no Windows** — autorizado explicitamente pelo
  mantenedor em 2026-08-01. Como Excel e COM não são executáveis no contêiner
  Linux, somente esse controlador e coletor podem usar um ambiente Python
  isolado no host Windows. Aplicação web, banco, migrações, testes, qualidade,
  build e demais comandos continuam obrigatoriamente em Docker.
