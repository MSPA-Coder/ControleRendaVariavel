# Controle de Renda Variavel

Aplicacao Flask para acompanhar posicoes de acoes e opcoes, cotacoes, proventos,
resultado e alocacao de carteira. A fonte operacional de dados e PostgreSQL; o
schema e sempre criado e atualizado pelas migracoes Alembic.

## Execucao

Aplicacao, banco, migracoes, testes, lint, tipagem, auditoria e build executam
em Docker. A unica excecao e a integracao RTD baseada em Excel/COM, que roda no
Windows porque essas APIs nao existem no container Linux.

```text
Aplicacao web (Docker) -> PostgreSQL (Docker)
           |
           +-> controlador RTD local (Windows) -> Excel/Profit COM
```

O controlador local e opcional. Sem ele, a aplicacao permanece funcional e
exibe o estado do coletor como indisponivel ou sem leitura; nenhuma operacao de
cadastro depende do RTD.

## Inicio rapido

1. Copie `.env.example` para `.env` e defina valores proprios para `SECRET_KEY`
   e `POSTGRES_PASSWORD`.

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

5. Para encerrar a pilha:

   ```powershell
   docker compose down
   ```

O PostgreSQL fica exposto apenas em `127.0.0.1:5435`. Os segredos nao sao
versionados; `.env` e `.docker-local` sao arquivos locais ignorados pelo Git.

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

O Profit deve estar aberto, autenticado e com RTD habilitado. O coletor mantem
uma instancia privada do Excel e nunca grava em `Trades.xlsm`.

## Operacao

- Todas as rotas, exceto `/login` e `/health`, exigem autenticacao.
- Escritas usam CSRF; `SECRET_KEY` e obrigatoria fora dos testes.
- Para TLS atras de proxy reverso, use `FORCE_HTTPS=true` e
  `TRUST_PROXY_HEADERS=true`; os cookies de sessao e de lembranca tornam-se
  `Secure`.
- O limitador usa memoria com um processo Gunicorn. Para escalar horizontalmente,
  configure `RATELIMIT_STORAGE_URI` com Redis compartilhado.
- O backup diario pode ser agendado com `scripts/backup.ps1`; os dumps ficam em
  `backups/`, tambem ignorado pelo Git.

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
docker compose --profile test down --volumes --remove-orphans
```

O servico `test` usa PostgreSQL descartavel e cria o schema exclusivamente por
`alembic upgrade head`. O servico `quality` executa Ruff, mypy e pip-audit. A
CI repete esse fluxo em Docker para cada push e pull request.
