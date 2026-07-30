# Controle de Renda Variável

Aplicação Flask que reproduz a aba **Ações** de `Trades.xlsm`: posições por lote,
cotações RTD, resultado bruto/líquido, retorno, retorno anualizado, target,
breakeven, valores de montagem/desmontagem, pesos da carteira, dias e status do
instrumento.

A aba **Opções** reproduz posições de calls e puts, com contratos, ativo-objeto,
strike, vencimentos, cotações RTD, breakeven, notional, resultado e totais por
exercício. O calendário de calls e puts é mantido em **Tabelas → Contratos e
vencimentos de opções**.

## Arquitetura de execução

O Excel/RTD usa COM e, portanto, o coletor roda no Windows. A aplicação web e o
PostgreSQL podem rodar em Docker:

```text
Servidor RTD -> Excel COM -> flask poll-rtd -> PostgreSQL <- aplicação web
```

O coletor mantém uma única instância privada do Excel e um workbook temporário
durante toda a execução de `poll-rtd --watch`. As fórmulas só são recriadas
quando a lista de posições muda. No encerramento, workbook, Excel e COM são
fechados de forma graciosa.

As cotações são persistidas como snapshots. Se o coletor parar, a tela mantém a
última cotação válida e sinaliza `stale`; uma falha é sinalizada como `error`.

## Início rápido

1. Copie `.env.example` para `.env`, troque `SECRET_KEY` e defina
   `POSTGRES_PASSWORD` (o `docker compose` recusa subir sem eles).
2. Suba banco e web:

   ```powershell
   docker compose up --build -d
   ```

3. Crie o usuário administrador (a aplicação exige login em todas as rotas,
   exceto `/login` e `/health`):

   ```powershell
   docker compose exec web flask --app app:create_app users create-admin
   ```

4. Abra [http://127.0.0.1:5003](http://127.0.0.1:5003), faça login e cadastre
   as posições.
5. No Windows, crie o ambiente do coletor e instale o extra RTD:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
   ```

6. Com `DATABASE_URL` apontando para `localhost:5435`, inicie o coletor:

   ```powershell
   .\.venv\Scripts\flask.exe --app app:create_app poll-rtd --watch
   ```

O ProgID padrão é `rtdtrading.rtdserver`, o mesmo da planilha. Os tópicos seguem
`TICKER_MERCADO_0`, com campos `ULT`, `FEC` e `EST`. Os códigos de mercado
observados são `B`, `Y` e `N`.

### Protótipo sem Excel

O comando abaixo testa diretamente o contrato COM `IRTDServer` exposto pelo
ProfitPro. Ele não abre o Excel, não grava no banco e não substitui o coletor
operacional:

```powershell
.\.venv\Scripts\flask.exe --app app:create_app probe-rtd-direct `
  --ticker AURE3 --market-code B
```

O ProfitPro precisa estar aberto, autenticado e com o RTD habilitado. O comando
informa apenas se `ULT`, `FEC` e `EST` foram recebidos e validados; os valores
financeiros não são escritos no terminal. Enquanto o protótipo não demonstrar
estabilidade equivalente em execução contínua, `poll-rtd --watch` continua
usando a sessão persistente e oculta do Excel.

Ao abrir a página **Ações** pela primeira vez em uma sessão do navegador, a
aplicação inicia automaticamente o `poll-rtd --watch` se ele estiver desligado.
O toggle **RTD**, ao lado de **Limpar**, mostra o estado do processo e permite
ligá-lo ou desligá-lo. O processo é gerenciado pelo controlador local do
Windows. Ao desligar, o controlador permanece ativo para receber um novo clique
e encerra o coletor, o Excel oculto e o Profit em modo de automação que tenham
sido criados por aquela execução.

Na execução com Docker, o COM continua no Windows e a aplicação web comunica-se
com um controlador local autenticado. Suba a pilha pelo PowerShell, na raiz do
projeto:

```powershell
.\scripts\start.ps1
```

Esse script cria um token aleatório em `.docker-local`, inicia o controlador
oculto no Windows e reconstrói a aplicação. Para desligar a pilha, o coletor RTD
e o controlador:

```powershell
.\scripts\stop.ps1
```

## Campos de cadastro

Além de posições, o app também registra **Transações** (operações já
encerradas, com resultado realizado — use "Encerrar posição" a partir de
uma posição aberta, ou lance manualmente) e **Proventos** (dividendos/JCP
recebidos). O coletor RTD também grava um snapshot diário de cada ticker
em `quote_history`, base para KPIs de risco (volatilidade, Sharpe, VaR)
em uma fase futura.

- `Corretora`, `Ticker`, `Quantidade`, `Custo médio`, `Tipo` (`C`/`V`) e data
  inicial correspondem às entradas da planilha.
- A aba **Tabelas** mantém os cadastros de corretoras e tickers.
- Mercado, código RTD (`B`, `Y` ou `N`) e moeda (`BRL` ou `USD`) pertencem ao
  cadastro do ticker e são reutilizados por todos os lotes desse ativo.
- `Delta da cotação`: reproduz a célula `C1` (padrão `1`).
- `Multiplicador do target`: reproduz `Custo * 1,5`.
- `Resultado`: `L` aplica o fator líquido `0,9996`; `B` mantém o bruto.

Cada compra pode permanecer como lote separado, inclusive quando houver várias
linhas do mesmo ticker.

O campo **Posição** classifica cada lote como `real` ou `hipotética`. Ele fica no
formulário de cadastro/edição e não ocupa uma coluna na grade principal. O
painel oferece filtros por classificação e corretora, agrupa os lotes por
corretora e mostra subtotais na moeda de cada grupo.

## Configurações do coletor

A aba **Configurações** permite escolher o coletor operacional:

- **Excel**: mantém uma instância oculta do Excel durante toda a execução;
- **RTD direto**: conversa com o servidor COM do ProfitPro sem iniciar o Excel.

Na mesma aba, o intervalo entre leituras pode ser configurado de 1 a 3600
segundos. O `poll-rtd --watch` consulta essa configuração a cada ciclo e troca o
provider com encerramento gracioso quando o modo é alterado. O ProfitPro deve
permanecer aberto, autenticado e com RTD habilitado em ambos os modos.

## Desenvolvimento e validação

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,rtd]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app
.\.venv\Scripts\pip-audit.exe
docker compose build
```

Testes que exercem persistência devem usar PostgreSQL descartável, nunca
SQLite; o schema de teste é criado a partir das migrações reais do Alembic
(`tests/conftest.py`). Por padrão os testes usam um segundo banco na mesma
instância descartável do `docker compose` (`investimentos_test` em
`localhost:5435`); aponte `TEST_DATABASE_URL` para outra instância se
necessário. O GitHub Actions (`.github/workflows/ci.yml`) roda `ruff`,
`mypy`, `pytest` (contra um serviço PostgreSQL descartável) e `pip-audit` em
todo push/PR. O health check fica em `/health`; dados calculados também
estão disponíveis em `/api/portfolio` (paginado, ver `page`/`per_page`).

## Segurança e operação

- Login obrigatório (Flask-Login) em toda a aplicação, exceto `/login` e
  `/health`. Crie/redefina o usuário administrador com
  `flask users create-admin`; desative um usuário com
  `flask users deactivate <username>`.
- Limite de tentativas (Flask-Limiter) em `/login` e nas rotas `/api/*`.
  Em memória por padrão (processo único); aponte `RATELIMIT_STORAGE_URI`
  para um Redis compartilhado ao rodar múltiplos workers.
- Cabeçalhos de segurança e CSP (Flask-Talisman). `FORCE_HTTPS=false` por
  padrão (uso na rede local); ative ao colocar a aplicação atrás de um
  proxy reverso com TLS (ver abaixo) — isso também passa a exigir cookies
  `Secure`.
- Ações de escrita usam CSRF.
- Segredos e credenciais vêm do ambiente; `docker compose` não sobe sem
  `SECRET_KEY` e `POSTGRES_PASSWORD` definidos.
- O coletor abre uma instância privada e oculta do Excel, fecha o workbook
  sem salvar e não altera `Trades.xlsm`.
- Backup diário do PostgreSQL: agende `scripts/backup.ps1` no Agendador de
  Tarefas do Windows (usa `docker compose exec db pg_dump`, formato
  `custom`, com retenção de 30 dias em `backups/`). Restaure com
  `pg_restore -U investimentos -d investimentos backups/arquivo.dump`.
- Não exponha o PostgreSQL ou a aplicação diretamente na internet sem TLS,
  autenticação e um proxy reverso (ex.: Caddy ou nginx terminando TLS na
  frente do container `web`, com `TRUST_PROXY_HEADERS=true` e
  `FORCE_HTTPS=true`).

O mapeamento auditável das fórmulas está em
[`docs/planilha-acoes.md`](docs/planilha-acoes.md) e
[`docs/planilha-opcoes.md`](docs/planilha-opcoes.md).
