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

1. Copie `.env.example` para `.env` e troque `SECRET_KEY`.
2. Suba banco e web:

   ```powershell
   docker compose up --build -d
   ```

3. Abra [http://localhost:8000](http://localhost:8000) e cadastre as posições.
4. No Windows, crie o ambiente do coletor e instale o extra RTD:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[rtd]"
   ```

5. Com `DATABASE_URL` apontando para `localhost:5433`, inicie o coletor:

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
docker compose build
```

Testes que exercem persistência devem usar PostgreSQL descartável, nunca SQLite.
O health check fica em `/health`; dados calculados também estão disponíveis em
`/api/portfolio`.

## Segurança e operação

- Ações de escrita usam CSRF.
- Segredos e credenciais vêm do ambiente.
- O coletor abre uma instância privada e oculta do Excel, fecha o workbook sem
  salvar e não altera `Trades.xlsm`.
- Não exponha o PostgreSQL ou a aplicação diretamente na internet sem TLS,
  autenticação e um proxy reverso.

O mapeamento auditável das fórmulas está em
[`docs/planilha-acoes.md`](docs/planilha-acoes.md) e
[`docs/planilha-opcoes.md`](docs/planilha-opcoes.md).
