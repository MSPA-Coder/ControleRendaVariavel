# Controle de Renda Variavel

Aplicacao Flask para acompanhar carteira de acoes e opcoes: posicoes,
cotacoes em tempo real, transacoes, proventos, historico de precos, risco,
performance mensal e exposicao. A fonte operacional de dados e PostgreSQL; o
schema e sempre criado e atualizado pelas migracoes Alembic.

## Funcionalidades

| Area | Paginas |
|---|---|
| Carteira | Posicoes (`/`), Opcoes (`/options`) |
| Registros | Transacoes (`/transactions`), Proventos (`/dividends`) |
| Analise | Risco (`/risk`), Performance (`/performance`), Exposicao por Ativo, por Corretora e por Mercado (`/analysis/exposure-*`) |
| Mercado | Cotacoes e historico (`/quotes`) |
| Cadastros | Corretoras, Tickers, Carteiras, Vencimentos, Contratos (`/tables/*`) |
| Operacao | Configuracoes (`/settings`), Login (`/login`), Health (`/health`) |

Toda posicao pertence a uma **carteira** (BRL, USD, Simulada ou outra que o
usuario cadastrar em Cadastros > Carteiras). O filtro de carteira e o de
corretora valem para toda a area de carteira e analise. A carteira marcada
como **simulada** e so para insight: nao gera transacao nem movimento, nao
funde entradas repetidas e nao encerra posicao -- serve apenas para criar,
editar e excluir. Um ticker marcado como **referencia** (`is_benchmark`) nao
e negociavel: ele so alimenta os comparadores de indice dos graficos de
Cotacoes e Performance e o calculo de Beta no relatorio de Risco.

Valores monetarios usam `Decimal` de ponta a ponta e moedas nunca sao
somadas entre si: totais, pesos e percentuais sao sempre calculados por
moeda -- e, dentro da mesma moeda, carteira simulada nunca soma com carteira
real.

### Interface

As paginas sao renderizadas pelo servidor e atualizadas por
[HTMX](https://htmx.org), servido localmente em versao fixa. Trocar um filtro
ou receber uma cotacao nova substitui apenas a regiao afetada, preservando a
URL e o historico do navegador; nao ha recarga de pagina.

Nao existe API JSON: o servidor responde HTML, inclusive nos fragmentos. O
unico endpoint que devolve JSON e `/health`, usado pelo health check do
container.

O JavaScript proprio cobre apenas o que o HTMX nao resolve: o menu, os
paineis recolhiveis e os graficos Chart.js.

## Execucao

Aplicacao, banco, migracoes, verificacao estatica e build executam em Docker.
A unica excecao e a integracao RTD baseada em
Excel/COM, que roda no Windows porque essas APIs nao existem no container
Linux.

O comando Compose padrao usa a imagem imutavel, sem montar o codigo do host.
Para desenvolvimento com edicao ao vivo, use explicitamente
`docker compose -f compose.yaml -f compose.dev.yaml up`; esse perfil monta o
diretorio de trabalho em `/app` e nao deve ser usado como validacao de runtime.

```text
Aplicacao web (Docker) -> PostgreSQL (Docker)
           |
           +-> controlador RTD local (Windows) -> Excel/Profit COM
```

### Coletor Windows para o VPS

Em produção remota, o ProfitChart continua no Windows. O VPS recebe cotações
por HTTPS autenticado e nunca tenta abrir conexão para o computador local. A
tela **Configurações** do sistema remoto define o intervalo de leitura, o
intervalo de verificação, os dias e horários de funcionamento do agente e
oferece o botão **Atualizar cotações agora**. A mesma agenda vale para ativos
da B3 e americanos. Fora da agenda, ou se o ProfitChart estiver fechado, o
agente não consulta o VPS; um pedido manual permanece pendente até a próxima
janela ativa. O intervalo de verificação determina quanto tempo o agente leva
para perceber pedidos e alterações; o último valor recebido fica em
`%LOCALAPPDATA%\ControleRendaVariavel\remote-collector-state.json`, sem
segredos, e é reutilizado se o VPS estiver temporariamente inacessível.

Depois de publicar a aplicação no VPS, crie o segredo uma vez e copie o mesmo
arquivo, por canal seguro, para `.secrets/collector_agent_token` no VPS. Em
seguida, instale o agente no Windows, informando a URL HTTPS pública:

```powershell
.\scripts\provision-collector-agent-token.ps1
.\scripts\rtd-remote-agent.ps1 -Action Install -ApiUrl https://rendavariavel-mspa.duckdns.org
```

O arquivo local `.docker-local/remote-collector.env` guarda apenas a URL e o
caminho do segredo; é ignorado pelo Git. O log operacional fica em
`%LOCALAPPDATA%\ControleRendaVariavel\remote-collector.log`. Para consultar ou
remover a tarefa: `-Action Status` ou `-Action Uninstall`.

Não mantenha simultaneamente o coletor local (`rtd-host.ps1`) e o agente
remoto sobre o mesmo ProfitChart. A aplicação continua utilizável nos dois
ambientes, mas apenas um deles deve fazer a leitura RTD de cada vez.

O controlador local e opcional. Sem ele, a aplicacao permanece funcional e
exibe o estado do coletor como indisponivel ou sem leitura; nenhuma operacao
de cadastro depende do RTD.

## Inicio rapido

1. Copie `.env.example` para `.env` e defina valores proprios para
   `SECRET_KEY` e `POSTGRES_PASSWORD`.

2. Provisione os arquivos locais de segredo. O script apenas lê `.env`, cria
   `.secrets/secret_key`, `.secrets/postgres_password` e o token local RTD;
   ele nunca exibe valores, altera o banco ou sobe contêineres:

   ```powershell
   .\scripts\provision-secrets.ps1
   ```

3. Inicie a pilha. O servico `migrate` aplica as revisoes Alembic antes do
   servidor web iniciar.

   ```powershell
   docker compose up --build -d
   ```

4. Crie o administrador dentro do container:

   ```powershell
   docker compose exec web flask --app app:create_app users create-admin
   ```

5. Acesse [http://127.0.0.1:5301](http://127.0.0.1:5301).

6. Para encerrar a pilha (sem apagar dados):

   ```powershell
   docker compose down
   ```

O PostgreSQL fica exposto apenas em `127.0.0.1:5302`. O Compose entrega os
segredos aos contêineres como arquivos em `/run/secrets`; eles não entram em
variáveis de ambiente nem no arquivo Compose resolvido. `.secrets`, `.env` e
`.docker-local` são locais e ignorados pelo Git. Depois de validar a migração,
remova manualmente as duas chaves de `.env` se não precisar mais dele como
origem para uma rotação deliberada.

> **Os dados operacionais vivem no volume `postgres_data`.** Nunca use
> `docker compose down --volumes` neste projeto: a flag apaga o banco
> operacional.

## Comandos de linha

Executados dentro do container (`docker compose exec web flask --app app:create_app <comando>`):

| Comando | Efeito |
|---|---|
| `users create-admin` | Cria ou atualiza um usuario; `--role admin` (padrao) ou `--role operador` |
| `users deactivate` | Desativa um usuario |
| `import-position-history` | Importa historico de cotacoes desde a abertura de cada posicao |

`poll-rtd` e `probe-rtd-direct` dependem de Excel/COM e so podem executar no
ambiente Python do host Windows. A operacao normal do coletor e supervisionada
pelo controlador RTD e acionada pela aba Settings; nao execute esses comandos
no container `web`.

## RTD no Windows (opcional)

Quando for necessaria a cotacao em tempo real pelo Excel/Profit, prepare um
ambiente Python local somente para o controlador e coletor COM:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
.\scripts\rtd-host.ps1 -Action Install
```

`rtd-host.ps1 -Action Install` registra, para o usuario atual e sem
elevacao, uma tarefa agendada que sobe no logon e mantem o controlador RTD
residente em `127.0.0.1:8765` (nunca exposto na rede). A tarefa inicia
`pythonw.exe` diretamente, sem abrir terminal; se o processo encerrar, o
Agendador o reinicia automaticamente. Diagnosticos operacionais ficam em
`%LOCALAPPDATA%\ControleRendaVariavel\rtd-controller.log` e
`%LOCALAPPDATA%\ControleRendaVariavel\rtd-collector.log`, com rotacao local.
Essa e a unica instalacao manual; depois de instalada, a tarefa cuida do
resto sozinha a cada logon: espera o Docker Desktop, sobe a pilha (`docker compose up -d`,
rede de seguranca — os servicos `db`/`web` ja usam
`restart: unless-stopped` e normalmente voltam sozinhos) e serve o
controlador. Nao ha mais `start.ps1`/`stop.ps1`: ligar e desligar o coletor
e feito pelo toggle da aba Settings, com a aplicacao ja no ar.

```powershell
.\scripts\rtd-host.ps1 -Action Uninstall
.\scripts\rtd-host.ps1 -Action Status
```

Depois de instalar, valide um ciclo completo de logoff/logon: nenhum teste
automatizado cobre a tarefa agendada em si, só o código que ela invoca.
Confirme que o Docker sobe, `/health` responde e o indicador do coletor sai
de `waiting`.

O Profit deve estar aberto, autenticado e com RTD habilitado. O coletor
mantem uma instancia privada do Excel e nunca grava em `Trades.xlsm`.

O controlador RTD no host usa os mesmos arquivos `.secrets/secret_key`,
`.secrets/postgres_password` e `.secrets/rtd_control_token`; também aceita caminhos explícitos por
`SECRET_KEY_FILE`, `POSTGRES_PASSWORD_FILE`, `DATABASE_URL_FILE` e
`RTD_CONTROL_TOKEN_FILE` quando a operação usa um cofre externo. Arquivo
ausente, vazio ou ilegível encerra a inicialização; valores de `.env` permanecem
apenas como compatibilidade durante a migração.

Por segurança, o controlador só executa o `docker.exe` dos locais padrão do
Docker Desktop e o script versionado `scripts/rtd-host.ps1`; não configure
substitutos por `PATH` ou `RTD_AUTOMATION_SCRIPT`.

Uma instalação Docker Desktop em outro caminho não é suportada: o controlador
falha antes de executar Compose, em vez de executar um binário herdado do
ambiente. Antes de instalar o controlador, reinstale ou mova o Docker Desktop
para um dos caminhos suportados. Se for necessário rollback desta proteção,
restaure a versão anterior do projeto e execute novamente `rtd-host.ps1`; não
crie um override de ambiente para contorná-la.

O coletor grava um snapshot por ticker por dia em `quote_history`, nao a
cada leitura; a cotacao instantanea fica em `quotes`/`option_quotes`. O
indicador de coletor no cabecalho mostra `online`, `stale`, `error` ou
`waiting` conforme a ultima leitura persistida.

### Inicialização do coletor local

No logon, o controlador RTD inicia e espera o ProfitChart interativo. O
coletor só consulta o RTD dentro da agenda salva em **Configurações**; fora
dela, permanece ocioso. O botão de ligar/desligar continua disponível na
tela de Configurações para interromper ou retomar o coletor manualmente.

## Operacao

- Todas as rotas, exceto `/login` e `/health`, exigem autenticacao.
- Dois papeis: `operador` alcanca a operacao da carteira; `admin` alcanca alem
  disso `/settings`, que altera coletor, precificacao e benchmark — parametros
  que mudam os numeros exibidos a todos. Um operador que acesse `/settings`
  diretamente recebe 403.
- Escritas usam CSRF; `SECRET_KEY` e obrigatoria fora dos testes.
- Para TLS atras de proxy reverso, use `FORCE_HTTPS=true` e
  `TRUST_PROXY_HEADERS=true`; os cookies de sessao e de lembranca tornam-se
  `Secure`.
- No VPS, use também `REMOTE_COLLECTOR_ENABLED=true` e mantenha
  `COLLECTOR_AGENT_TOKEN_FILE` como segredo do contêiner. A API privada do
  coletor exige esse token e não aceita sessão de navegador em seu lugar.
- O limitador usa memoria com um processo Gunicorn. Para escalar
  horizontalmente, configure `RATELIMIT_STORAGE_URI` com Redis compartilhado.
- O backup diario pode ser agendado com `scripts/backup.ps1`; os dumps ficam
  em `backups/`, tambem ignorado pelo Git.

As formulas e contratos funcionais estao em
[`docs/planilha-acoes.md`](docs/planilha-acoes.md) e
[`docs/planilha-opcoes.md`](docs/planilha-opcoes.md).

## Verificacao

O projeto mantém uma suíte focada de segurança e fumaça junto do Ruff no estágio
`quality` da imagem. A CI mínima executa a mesma sequência em pushes/PRs para
`main` e semanalmente, e o Dependabot acompanha `pip`, Docker e GitHub Actions.
Não há cobertura, mypy, `pip-audit` dentro da imagem nem uma suíte ampla de
regressão. Antes de alterações persistentes, gere e valide um backup
com `scripts/backup.ps1`. A baseline Alembic cria um PostgreSQL novo com o
schema e catalogos iniciais.

```powershell
docker compose --profile quality run --rm quality
```

Para validar a aplicacao, reconstrua e confira o health check:

```powershell
docker compose up --build -d
Invoke-WebRequest http://127.0.0.1:5301/health
```
