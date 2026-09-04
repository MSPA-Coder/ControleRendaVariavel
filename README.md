# Controle de Renda Variável

Aplicação web de uso pessoal do mantenedor para acompanhar ações e opções:
posições, transações, proventos, cotações, histórico de preços, risco,
performance mensal e exposição. Não é plataforma de negociação, custódia ou
serviço multiusuário. Dados financeiros e configurações persistem no
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

Toda posição pertence a uma carteira. Carteiras simuladas servem apenas para
insight: não geram movimentos ou transações, não consolidam novas entradas e
não podem ser encerradas. Totais permanecem separados por moeda e por natureza
real ou simulada. Tickers de referência alimentam comparadores e cálculos de
risco, mas não são negociáveis.

## Execução com Docker

Copie `.env.example` para `.env`, substitua os valores de exemplo de
`SECRET_KEY` e `POSTGRES_PASSWORD` e provisione os arquivos locais de segredo:

```powershell
Copy-Item .env.example .env
.\scripts\provision-secrets.ps1
```

O script cria `.secrets/secret_key`, `.secrets/postgres_password` e
`.secrets/collector_agent_token` sem exibir os valores. `.env`, `.secrets/`,
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

Excel/COM não existe no contêiner Linux. Essa é a única exceção ao runtime em
Docker: o agente instalado por `scripts/rtd-remote-agent.ps1` executa
`app.remote_collector_agent` no Windows, lê o RTD do Excel/ProfitChart e entrega
as cotações ao servidor por HTTPS autenticado. O servidor nunca inicia conexão
com o Windows nem recebe acesso ao ambiente local.

```text
Excel/ProfitChart -> agente RTD Windows -> HTTPS autenticado -> aplicação -> PostgreSQL
```

Para preparar e instalar o agente:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
.\scripts\provision-collector-agent-token.ps1
.\scripts\rtd-remote-agent.ps1 -Action Install -ApiUrl https://renda-mspa.duckdns.org
```

O mesmo conteúdo de `.secrets/collector_agent_token` deve existir no Windows e
no servidor, transferido por canal seguro. A tarefa guarda apenas a URL e o
caminho do segredo em `.docker-local/remote-collector.env`; o log fica em
`%LOCALAPPDATA%\ControleRendaVariavel\remote-collector.log`. Consulte ou remova
a tarefa com `-Action Status` ou `-Action Uninstall`.

Para coletar no banco local desta máquina, use o modo alternativo abaixo. Ele
inicia no logon do Windows, lê `SECRET_KEY` e a senha PostgreSQL diretamente
dos arquivos em `.secrets/` e grava pela porta local `5302`; os segredos não
entram nos argumentos da tarefa nem no log.

```powershell
.\scripts\rtd-local-agent.ps1 -Action Install
```

A tela **Configurações** separa os dois ritmos: o **intervalo entre leituras**
define quando o agente consulta o RTD e entrega cotações; o **intervalo de
verificação do agente** define somente quando ele busca pedidos e alterações no
servidor, sem consultar o ProfitChart. A agenda restringe as leituras RTD. O
botão de atualização manual é percebido na próxima verificação e antecipa uma
leitura. Em **Ações**, a tela se atualiza perto da próxima leitura esperada, em
vez de consultar o servidor continuamente. Sem o agente, a aplicação permanece
utilizável; as cotações aparecem indisponíveis ou desatualizadas e cadastros
continuam funcionando.

`poll-rtd` e `probe-rtd-direct` também dependem de Excel/COM e servem apenas
para diagnóstico no ambiente Python isolado do Windows. Não os execute no
contêiner `web`.

### Coletor local

Quando a aplicação usa um PostgreSQL local publicado pelo Compose, o mesmo
núcleo de coleta pode gravar diretamente nesse banco pelo Windows. Instale a
tarefa local com:

```powershell
.\scripts\rtd-local-agent.ps1 -Action Install
```

A tarefa executa `poll-rtd --watch` sem abrir janela, aceita apenas uma
instância e reinicia após uma falha. Ela começa no **logon do Windows** e é
encerrada quando a sessão do Windows termina; isso não acompanha o login ou o
logout da sessão web. Consulte ou remova com `-Action Status` ou `-Action
Uninstall`. Não instale o coletor local e o agente remoto ao mesmo tempo para
o mesmo ProfitChart: ambos abririam COM e poderiam disputar a mesma fonte.
O próprio `poll-rtd` mantém um lock interprocesso, portanto o toggle
administrativo ou outro worker não consegue manter um segundo coletor local
ativo. A saída do processo fica em
`%LOCALAPPDATA%\ControleRendaVariavel\local-collector.log`.

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
