# Controle de Renda Variável

Aplicação web para acompanhar ações e opções por usuário autenticado:
posições, transações, proventos, cotações, histórico de preços, risco,
performance mensal e exposição. Não é plataforma de negociação, custódia ou
corretagem. Dados financeiros persistem no
PostgreSQL; a planilha `Trades.xlsm` é apenas referência funcional e não faz
parte do runtime.

## Stack e arquitetura

- Flask e SharedAuth no servidor, com páginas HTML atualizadas por HTMX e
  gráficos em Chart.js;
- PostgreSQL 17 e migrações Alembic;
- Gunicorn com dois workers na imagem de runtime;
- Docker Compose como interface oficial para aplicação, banco, migrações,
  qualidade e build;
- Nginx com TLS como entrada da topologia de produção.

As páginas cobrem carteira de ações e opções, transações, proventos, cotações,
risco, performance, exposições, cadastros e configurações. Os contratos
detalhados de cálculos e comportamento ficam na documentação funcional.

Toda posição e carteira pertence ao usuário autenticado. Corretoras, tickers,
contratos, vencimentos e cotações são referências globais; cotações só ficam
visíveis para tickers que o usuário já possuiu. Carteiras simuladas servem apenas para
insight: não geram movimentos ou transações, não consolidam novas entradas e
não podem ser encerradas. Totais permanecem separados por moeda e por natureza
real ou simulada. Tickers de referência alimentam comparadores e cálculos de
risco, mas não são negociáveis.

Tema, taxa de cálculo e comparação são alterados em **Preferências** pela
própria conta. O prazo de alerta de cotação e a administração do coletor e das
referências globais exigem o papel administrativo, em **Configurações**. Antes de atualizar uma instalação
existente para o isolamento por usuário, siga o [roteiro de migração](docs/deployment-vps.md#isolamento-financeiro--revisão-20260912_0016).

## Execução com Docker

Copie `.env.example` para `.env` e provisione os arquivos locais de segredo:

```powershell
Copy-Item .env.example .env
.\scripts\provision-secrets.ps1
```

O script gera `.secrets/secret_key`, `.secrets/postgres_password`, uma senha
sintética para a suíte e tokens separados de leitura e escrita do coletor, sem
exibir os valores. Se uma instalação antiga ainda tiver `SECRET_KEY` ou
`POSTGRES_PASSWORD` no `.env`, eles são aceitos apenas para preencher arquivos
que ainda não existam; depois rode `.scripts\provision-secrets.ps1 -MigrateDotEnv`
para trocar os valores por referências aos arquivos. `.env`, `.secrets/`,
`.certs/` e `.docker-local/` são locais e ignorados pelo Git.

Suba a pilha, crie o administrador e acesse a aplicação:

```powershell
docker compose up --build -d
docker compose exec web flask --app app:create_app users create-admin
Invoke-WebRequest http://127.0.0.1:5301/health
```

A interface fica em <http://127.0.0.1:5301> e o PostgreSQL é publicado apenas
em `127.0.0.1:5302`. O serviço `migrate` aplica as revisões Alembic antes de
`web` iniciar.

Quem perde a senha é atendido por um administrador em `/users`: o botão
**Redefinir** sorteia uma senha temporária, mostrada uma única vez na tela de
quem redefiniu, para ser entregue fora do sistema. Contas criadas por essa tela
recebem o mesmo tratamento. Enquanto a troca estiver pendente, toda requisição
da pessoa cai em `/minha-senha` — só o logout, o `/health` e os arquivos
estáticos escapam. A troca exige a senha atual e recusa repetir a senha
temporária. `/minha-senha` também está sempre disponível pela barra superior,
sem obrigação. O comando `users create-admin` é a exceção: quem o roda escolheu
a própria senha e não fica com troca pendente.

Duas garantias vieram junto, compartilhadas com os outros apps Flask do
mantenedor: o destino pós-login (`?next=`) é validado por
`sharedauth.access.url_proximo_seguro`, que só aceita caminho interno — sem
isso a tela de login vira um redirecionador aberto; e a sessão carrega uma
marca da senha em vigor (`sharedauth.session`), então **trocar a senha derruba
as sessões abertas em outros lugares**, e não só a atual. Quem troca a própria
senha continua conectado; quem tinha entrado com a senha antiga cai no próximo
acesso.

O Compose padrão usa a imagem sem montar o código do host. Para edição ao vivo,
use explicitamente:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml up
```

Encerre sem apagar os dados:

```powershell
docker compose down
```

Os dados vivem no volume `postgres_data`; não use
`docker compose down --volumes` no ambiente operacional. Backup, retenção e
restauração pertencem ao projeto irmão BackupRestore.

Comandos administrativos são executados no contêiner:

```powershell
docker compose exec web flask --app app:create_app users create-admin
docker compose exec web flask --app app:create_app users deactivate USUARIO
docker compose exec web flask --app app:create_app import-position-history
```

## Cotações RTD no Windows

Dois processos independentes leem o ProfitChart na sessão interativa do Windows:

- **Produção:** agente automático, sempre para o VPS por HTTPS. Não carrega
  configuração ou credenciais do PostgreSQL local e continua quando o Docker
  de desenvolvimento está parado.
- **Local:** iniciado somente quando necessário, sempre para o PostgreSQL
  desta máquina. Ao parar, o processo termina (com sua sessão RTD); nenhum
  vigia fica consultando banco, arquivo ou botão para saber quando reiniciar.

Os dois leem o RTD direto do servidor COM do ProfitPro (`IRtdServer`), sem
Excel; exigem o ProfitChart aberto na sessão interativa do Windows.

Os intervalos e agendas são próprios de cada destino. Como referência, use
300 segundos no VPS e 120 no local. Consultar configuração não lê RTD; entre
prazos os processos dormem. O arquivo de estado remoto só é gravado quando a
agenda ou o intervalo de verificação muda.

Prepare o ambiente isolado do agente, se ainda não existir:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
.\scripts\provision-collector-agent-token.ps1
.\scripts\rtd-agent.ps1 -Action Install -ApiUrl https://renda-mspa.duckdns.org
```

A instalação cria a tarefa **ControleRendaVariavel Coletor Remoto**, com
gatilhos no logon e às 09:40 e reinício em caso de falha.
Ela guarda somente URL, caminho do token e opções RTD em
`.docker-local/remote-collector.env`. O mesmo token deve existir nos dois
lados, provisionado por canal seguro. Nenhum segredo vai aos argumentos ou logs.
O VPS nunca inicia conexão para o Windows.

Para consultar localmente, com Docker e ProfitChart disponíveis:

```powershell
.\scripts\rtd-local.ps1 -Action Start
.\scripts\rtd-local.ps1 -Action Status
.\scripts\rtd-local.ps1 -Action Stop
```

Para criar atalhos de iniciar/parar em `.docker-local`, execute
`./scripts/rtd-local.ps1 -Action Shortcuts`.

A tarefa **ControleRendaVariavel Coletor Local** não tem gatilho automático nem
reinício automático. Repetir Start não abre outro coletor. Stop acorda o
processo por um evento do Windows e espera concluir o ciclo em andamento antes
de encerrar. Se o banco local ficar indisponível, o processo local encerra com
erro e deve ser iniciado novamente após recuperar o ambiente. O remoto continua
independente.

Os logs ficam em `%LOCALAPPDATA%\ControleRendaVariavel`: `remote-collector.log`
para os ciclos remotos, `remote-runner.log` para falhas de inicialização e
`local-runner.log` para o local. Consulte/remova a tarefa remota com
`rtd-agent.ps1 -Action Status` ou `-Action Uninstall`; isso não para o local.

A tela de Configurações de cada instância controla sua agenda e intervalos.
A pausa na tela do VPS preserva o agente remoto para retomada.
A tela local orienta iniciar/parar no Windows; ela não oferece um checkbox
que deixe um processo aguardando habilitação. O pedido **Atualizar cotações
agora** só é atendido se o respectivo coletor estiver iniciado e dentro da agenda.
Não há troca de destino: cada processo tem o seu, fixo.

`poll-rtd` (uma leitura ou `--watch`) sempre grava localmente.
`python -m app.collector.remote_agent` sempre entrega ao VPS.
Esses comandos e `probe-rtd-direct` exigem o ambiente RTD do Windows;
COM/RTD não roda no contêiner web.

## Segurança e produção

Todas as páginas, exceto login e health, exigem autenticação. O papel
`operador` acessa a operação da carteira; `admin` também acessa Configurações.
Escritas usam CSRF. Em produção, TLS termina no Nginx e
`FORCE_HTTPS=true`/`TRUST_PROXY_HEADERS=true` tornam os cookies seguros e
habilitam o tratamento correto dos cabeçalhos do proxy.

O Flask-Limiter usa `RATELIMIT_STORAGE_URI=memory://` por padrão. Como o
Gunicorn executa dois workers, esse contador é por processo, não é compartilhado
e zera a cada reinício. Na topologia de produção atual, o Nginx versionado em
`../_manutencao/vps/nginx/` é requisito: ele aplica no edge um limite
compartilhado somente ao `POST /login`. Outra topologia, especialmente com
múltiplas instâncias, precisa de armazenamento compartilhado para o limitador
da aplicação ou proteção equivalente no edge.

Para detalhes de segredos, Nginx, publicação e verificações operacionais, veja
[Implantação no VPS](docs/deployment-vps.md).

Mensagens de log que carregam texto de fora (parâmetro de requisição,
exceção de biblioteca) passam por `sharedauth.logs.sanitizar_log`. Ela é
rede, não garantia: redige por reconhecimento de padrão e não substitui a
disciplina de nunca colocar um segredo na mensagem em primeiro lugar — ver
`sharedauth.secrets`, cujas exceções nunca carregam o valor lido. Um rótulo
novo a reconhecer entra em `sharedauth.logs.CHAVES_SENSIVEIS`, na biblioteca,
nunca numa cópia local.

## Validação

Execute a verificação do projeto no estágio `quality`:

```powershell
docker compose --profile quality run --build --rm quality
```

`--build` é necessário: o serviço `quality` não monta o código do host, e
`docker compose run` só reconstrói quando a imagem não existe — sem ele, o
comando passa em verde sobre a versão anterior do código.

Para mudanças de runtime, também reconstrua a imagem e confira `/health`. Para
alterações apenas documentais, valide o Compose, links, caminhos, buscas
residuais e `git diff --check`.

## Documentação viva

- [Guia de engenharia](AGENTS.md)
- [Arquitetura](docs/architecture.md)
- [Desenvolvimento e validação](docs/development.md)
- [Contrato funcional de ações](docs/planilha-acoes.md)
- [Contrato funcional de opções](docs/planilha-opcoes.md)
- [Implantação no VPS](docs/deployment-vps.md)
