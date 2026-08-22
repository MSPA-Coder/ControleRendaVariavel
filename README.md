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

A tela **Configurações** define modo, intervalos e agenda, e permite solicitar
uma atualização. O agente consulta o servidor e o RTD somente quando a agenda
está ativa e o ProfitChart está aberto. Sem o agente, a aplicação permanece
utilizável; as cotações aparecem indisponíveis ou desatualizadas e cadastros
continuam funcionando.

`poll-rtd` e `probe-rtd-direct` também dependem de Excel/COM e servem apenas
para diagnóstico no ambiente Python isolado do Windows. Não os execute no
contêiner `web`.

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

## Validação

Execute a verificação do projeto no estágio `quality`:

```powershell
docker compose --profile quality run --rm quality
```

Para mudanças de runtime, também reconstrua a imagem e confira `/health`. Para
alterações apenas documentais, valide o Compose, links, caminhos, buscas
residuais e `git diff --check`.

## Documentação viva

- [Guia de engenharia](AGENTS.md)
- [Contrato funcional de ações](docs/planilha-acoes.md)
- [Contrato funcional de opções](docs/planilha-opcoes.md)
- [Implantação no VPS](docs/deployment-vps.md)
