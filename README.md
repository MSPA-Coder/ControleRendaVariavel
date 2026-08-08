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
| Cadastros | Corretoras, Tickers, Vencimentos, Contratos (`/tables/*`) |
| Operacao | Configuracoes (`/settings`), Login (`/login`), Health (`/health`) |

Posicoes podem ser **reais** ou **hipoteticas**, e os filtros de tipo e
corretora valem para toda a area de carteira e analise. Um ticker marcado
como **referencia** (`is_benchmark`) nao e negociavel: ele so alimenta os
comparadores de indice dos graficos de Cotacoes e Performance e o calculo de
Beta no relatorio de Risco.

Valores monetarios usam `Decimal` de ponta a ponta e moedas nunca sao
somadas entre si: totais, pesos e percentuais sao sempre calculados por
moeda.

### API JSON

`/api/portfolio` (paginada), `/api/options`, `/api/collector-heartbeat` e
`/api/rtd-service` respondem JSON para o proprio frontend. Todas exigem
sessao autenticada e respondem `401` sem ela.

## Execucao

Aplicacao, banco, migracoes, testes, lint, tipagem, auditoria e build
executam em Docker. A unica excecao e a integracao RTD baseada em
Excel/COM, que roda no Windows porque essas APIs nao existem no container
Linux.

```text
Aplicacao web (Docker) -> PostgreSQL (Docker)
           |
           +-> controlador RTD local (Windows) -> Excel/Profit COM
```

O controlador local e opcional. Sem ele, a aplicacao permanece funcional e
exibe o estado do coletor como indisponivel ou sem leitura; nenhuma operacao
de cadastro depende do RTD.

## Inicio rapido

1. Copie `.env.example` para `.env` e defina valores proprios para
   `SECRET_KEY` e `POSTGRES_PASSWORD`.

2. Inicie a pilha. O servico `migrate` aplica as revisoes Alembic antes do
   servidor web iniciar.

   ```powershell
   docker compose up --build -d
   ```

3. Crie o administrador dentro do container:

   ```powershell
   docker compose exec web flask --app app:create_app users create-admin
   ```

4. Acesse [http://127.0.0.1:5003](http://127.0.0.1:5003).

5. Para encerrar a pilha (sem apagar dados):

   ```powershell
   docker compose down
   ```

O PostgreSQL fica exposto apenas em `127.0.0.1:5435`. Os segredos nao sao
versionados; `.env` e `.docker-local` sao arquivos locais ignorados pelo Git.

> **Os dados operacionais vivem no volume `postgres_data`.** Nunca use
> `docker compose down --volumes` neste projeto: o volume e declarado no
> mesmo arquivo Compose dos servicos de teste, e a flag apaga o banco de
> producao junto. Para limpar so o ambiente de teste, use o comando
> especifico da secao [Validacao](#validacao).

## Comandos de linha

Executados dentro do container (`docker compose exec web flask --app app:create_app <comando>`):

| Comando | Efeito |
|---|---|
| `users create-admin` | Cria o usuario administrador |
| `users deactivate` | Desativa um usuario |
| `import-position-history` | Importa historico de cotacoes desde a abertura de cada posicao |
| `poll-rtd` | Le o RTD e grava as cotacoes (host Windows) |
| `probe-rtd-direct` | Testa o contrato IRTDServer sem abrir o Excel (host Windows) |

## RTD no Windows (opcional)

Quando for necessaria a cotacao em tempo real pelo Excel/Profit, prepare um
ambiente Python local somente para o controlador e coletor COM:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
.\scripts\start.ps1
```

`start.ps1` cria um token local em `.docker-local`, inicia o controlador em
`127.0.0.1:8765` e sobe a pilha Docker. O controller nao e exposto na rede.
Para encerrar o coletor, o Excel associado e os containers:

```powershell
.\scripts\stop.ps1
```

O Profit deve estar aberto, autenticado e com RTD habilitado. O coletor
mantem uma instancia privada do Excel e nunca grava em `Trades.xlsm`.

O coletor grava um snapshot por ticker por dia em `quote_history`, nao a
cada leitura; a cotacao instantanea fica em `quotes`/`option_quotes`. O
indicador de coletor no cabecalho mostra `online`, `stale`, `error` ou
`waiting` conforme a ultima leitura persistida.

### Perfil operacional do host

O perfil local em `.docker-local/operational-profile` e gerenciado pela aba
Settings: `test` nao inicia Docker, Profit ou controlador no logon;
`production` habilita a tarefa agendada **ControleRendaVariavel RTD
Production** para o usuario conectado. A tarefa aguarda o Docker Desktop,
executa `docker compose up -d` e mantem o controlador RTD em primeiro plano,
com ate 999 retentativas de um minuto apos falhas. Ela nao usa privilegios
elevados e nao roda fora da sessao interativa, necessaria ao COM/Profit.

No perfil `test`, use `scripts/start.ps1` como entrada oficial: ele sobe a
pilha e o controlador, mas deixa o coletor desligado ate a ativacao manual
na interface. Assim o Profit pode permanecer fechado sem gerar tentativas ou
erros RTD. No perfil `production`, o supervisor espera um Profit interativo
estar aberto e estavel antes de iniciar o coletor; quedas do coletor usam
retentativas com espera progressiva.

Ao mudar de `production` para `test`, o coletor e a inicializacao futura sao
desligados, mas os containers da sessao atual continuam ativos. Use
`scripts/stop.ps1` para encerra-los. A mudanca inversa habilita a tarefa e
passa a supervisionar o coletor imediatamente, sem iniciar o Profit
automaticamente.

O backend usa a interface reproduzivel abaixo para trocar o modo; ela nunca
persiste PID nem encerra processos que nao tenham sido autenticados e
validados como o controlador deste projeto:

```powershell
.\scripts\rtd-automation.ps1 -Action Enable
.\scripts\rtd-automation.ps1 -Action Disable
.\scripts\rtd-automation.ps1 -Action Status
```

## Operacao

- Todas as rotas, exceto `/login` e `/health`, exigem autenticacao.
- Escritas usam CSRF; `SECRET_KEY` e obrigatoria fora dos testes.
- Para TLS atras de proxy reverso, use `FORCE_HTTPS=true` e
  `TRUST_PROXY_HEADERS=true`; os cookies de sessao e de lembranca tornam-se
  `Secure`.
- O limitador usa memoria com um processo Gunicorn. Para escalar
  horizontalmente, configure `RATELIMIT_STORAGE_URI` com Redis compartilhado.
- O backup diario pode ser agendado com `scripts/backup.ps1`; os dumps ficam
  em `backups/`, tambem ignorado pelo Git.

As formulas e contratos funcionais estao em
[`docs/planilha-acoes.md`](docs/planilha-acoes.md) e
[`docs/planilha-opcoes.md`](docs/planilha-opcoes.md).

## Validacao

Execute todos os controles pelo perfil Docker de testes:

```powershell
docker compose --profile test config --quiet
docker compose --profile test build
docker compose --profile test run --rm quality
docker compose --profile test run --rm test
docker compose up --build -d
Invoke-WebRequest http://127.0.0.1:5003/health
```

Para descartar os containers de teste sem tocar no volume operacional:

```powershell
docker compose --profile test rm -sf test test-db
```

O servico `test` usa PostgreSQL descartavel e cria o schema exclusivamente
por `alembic upgrade head`. O servico `quality` executa Ruff, mypy e
pip-audit. A CI repete esse fluxo em Docker para cada push e pull request.

As imagens copiam o codigo-fonte (nao ha bind mount no perfil de teste):
depois de alterar qualquer arquivo, refaca `docker compose --profile test
build` antes de rodar os testes, senao a execucao usa a imagem anterior.

### Isolamento e desempenho da suite

O schema de teste e construido uma unica vez por sessao pytest, com
`alembic upgrade head`. Entre os testes, apenas os *dados* sao reiniciados:
todas as tabelas sao truncadas, as linhas semeadas pelas proprias migracoes
sao restauradas e as sequencias sao realinhadas. Cada teste comeca do mesmo
estado deterministico de um banco recem-migrado, sem pagar as migracoes de
novo. Testes que migram o schema de proposito usam a fixture
`rebuild_schema`, que o reconstroi ao terminar.

A suite nunca acessa o banco operacional: ela usa `TEST_DATABASE_URL`, que
aponta para o servico descartavel `test-db`.

### Classificacao de testes por risco

Cada teste carrega ao menos um marcador pytest equivalente as categorias do
AGENTS.md (`critical`, `business_rule`, `security`, `migration_persistence`,
`observable_contract`, `interface_smoke`, `architecture`, `e2e`); veja
`[tool.pytest.ini_options]` em `pyproject.toml` para a lista completa.
`critical` cobre dinheiro, integridade, transacoes, migracoes, autorizacao e
seguranca.

Na CI, o subconjunto `critical` roda primeiro, sem cobertura e com `-x`,
para reduzir o tempo de diagnostico; a suite completa (com cobertura) roda
em seguida, sempre sem filtros parciais. Para reproduzir isso localmente:

```powershell
docker compose --profile test run --rm test pytest -q -x --tb=short -m critical
docker compose --profile test run --rm test
```
