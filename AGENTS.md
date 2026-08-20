# ControleRendaVariavel — guia de engenharia

## Escopo, leitura e fontes de verdade

Este repositório é uma aplicação Flask para controlar ações, opções, cotações,
risco e performance. PostgreSQL é a fonte operacional de dados; Docker Compose
é a interface de execução. A planilha `Trades.xlsm` é referência histórica de
leitura: não é banco, dependência de runtime nem destino de escrita.

Antes de mudar código, leia este arquivo, `README.md`, `pyproject.toml`,
`compose.yaml`, as migrações relevantes e o contrato funcional afetado:

- `docs/planilha-acoes.md` para ações, performance, risco e RTD;
- `docs/planilha-opcoes.md` para opções;
- comentários próximos ao código para decisões de implementação, interface e
  integração que não sejam contrato funcional.

Em conflito, prevalecem: solicitação explícita atual do mantenedor; este
arquivo; contrato funcional em `docs/`; testes e contratos públicos; código
existente. Atualize a documentação funcional na mesma mudança quando uma regra
de produto mudar; não replique aqui fórmulas, detalhes de HTMX ou comportamento
de tela.

## Execução e operação

O projeto é *container-first*: aplicação, banco, migrações, testes, lint e
build rodam em Docker, nunca exigindo Python, PostgreSQL ou ferramentas do
projeto no host. Comandos usuais, na raiz do repositório:

```powershell
docker compose up --build -d
docker compose down
docker compose --profile quality run --rm quality
docker compose exec web flask --app app:create_app <comando>
Invoke-WebRequest http://127.0.0.1:5301/health
```

O Compose publica a aplicação em `127.0.0.1:5301` e PostgreSQL em
`127.0.0.1:5302`; dentro da rede Compose o banco é `db:5432`. O serviço
`migrate` aplica Alembic antes de `web`. O comando padrão usa somente a imagem
imutável. Desenvolvimento com bind mount é explícito e opt-in:
`docker compose -f compose.yaml -f compose.dev.yaml up`. Nunca use esse perfil
para validar a imagem de runtime ou para uma operação que deva permanecer
imutável.

Os dados operacionais vivem no volume `postgres_data`. Nunca execute
`docker compose down --volumes` fora de ambiente descartável. O backup diário
e a retenção são do BackupRestore, projeto irmão (catálogo, verificação e
ensaio de restauração próprios); este repositório não tem rotina de backup.
Antes de uma alteração persistente, gere e valide backup com
`python cli.py backup --projeto controle_renda_variavel --tipos banco` no
diretório do BackupRestore. Restaurar, adoção de banco legado, transformação
de dados ou exclusão em massa exigem plano, backup validado e autorização
explícita.

## Segurança, dados e Docker

- Autenticação é padrão; somente login, health e estáticos podem ser públicos.
  Autorização é sempre verificada no servidor e toda escrita usa CSRF.
- `SECRET_KEY` e credenciais do banco vêm de arquivos de segredo (`*_FILE` no
  contêiner e `.secrets/` no host), não têm fallback permissivo e não podem
  aparecer em código, imagem, logs, documentação, diffs ou commits. `.env` é
  somente compatibilidade de migração; `.secrets/`, `.docker-local/`,
  certificados e backups permanecem locais/ignorados.
- Preserve CSP sem `unsafe-inline`, assets locais, escape/validação de entrada,
  SQL parametrizado, rate limiting e cookies `HttpOnly`/`SameSite=Lax`;
  habilite cookies `Secure` junto de TLS por `FORCE_HTTPS` e
  `TRUST_PROXY_HEADERS`.
- Produção usa a imagem multi-stage, Gunicorn, usuário não-root, health checks
  e dependências somente de runtime. Não monte código do host, socket Docker
  nem use modo privilegiado em produção. Portas seguem limitadas a localhost
  até que uma mudança de exposição seja explicitamente aprovada.

## Persistência e invariantes financeiros

PostgreSQL é também o backend dos testes com persistência; SQLite não o
substitui. Mudança de schema cria nova revisão Alembic, revisada manualmente.
Não edite uma migração que possa ter sido aplicada. Um banco vazio nasce por
`alembic upgrade head`, nunca por `create_all()`/`stamp`; banco legado sem
`alembic_version` é procedimento administrativo explícito.

Delimite a transação no caso de uso que inicia a escrita; não faça commits
parciais em camadas inferiores nem mantenha transação aberta durante RTD ou
outra chamada externa. Proteja invariantes concorrentes no banco quando
necessário.

Os invariantes financeiros essenciais são:

- valores monetários e quantidades persistidos usam `Decimal`, nunca `float`;
- quantidade e preço médio não são negativos; ticker é normalizado;
- divisão por zero resulta em `None`/não aplicável, nunca infinito ou erro
  silencioso; arredondamento é explícito;
- totais são derivados das posições, e permanecem separados por moeda e por
  natureza real/simulada; dinheiro simulado nunca compõe patrimônio real;
- uma carteira simulada é apenas insight: não gera movimentos/transações, não
  funde posições e não pode ser encerrada;
- mudanças em cálculos devem ser conferidas contra os exemplos normativos em
  `docs/`, com teste de domínio quando proporcional.

## Exceção RTD no host Windows

Excel/COM não roda no contêiner Linux. Por autorização explícita, somente o
controlador/coletor RTD pode usar o ambiente Python isolado do host Windows,
instalado conforme `README.md` e administrado por `scripts/rtd-host.ps1`.
Todo o restante continua em Docker. O controlador escuta apenas em
`127.0.0.1:8765`; a aplicação fala com ele por `host.docker.internal`.

RTD é adaptador externo: normalize e valide valores antes do domínio, exponha
estado/atraso sem sobrescrever a última cotação válida, use timeout e
retentativas limitadas, e nunca bloqueie cadastro por sua indisponibilidade.
Testes e desenvolvimento sem COM usam fake determinístico. Não registre dados
financeiros sensíveis nem credenciais nos logs.

## Validação proporcional

Use a menor validação que dê confiança, registrando controles executados e
omitidos. A suíte mínima é sempre um único comando no estágio `quality`:

```powershell
docker compose --profile quality run --rm quality
```

Ela roda Ruff e os testes de autenticação, autorização, CSRF, cabeçalhos e
grafo de migrações. A CI mínima executa essa mesma sequência em pushes/PRs para
`main` e semanalmente, e o Dependabot acompanha `pip`, Docker e GitHub Actions.
Não há cobertura, mypy, `pip-audit` ou suíte ampla de regressão neste projeto;
a exceção aprovada é compensada por essa suíte mínima, backup validado,
bootstrap Alembic em banco vazio e smoke manual proporcional.

| Mudança | Validação mínima adicional |
|---|---|
| texto ou documento | revisão de links, comandos e coerência com fontes de verdade |
| rota, domínio ou interface | percorrer manualmente o fluxo afetado com cenário real |
| autenticação, autorização, CSRF ou sessão | suíte mínima inteira e confirmação de negação anônima |
| schema ou migração | backup validado, bootstrap em PostgreSQL vazio e health check |
| dependência, Dockerfile ou Compose | build limpo e subida completa da pilha |
| RTD host | fake automatizado e, quando disponível, ticker conhecido sem dados sensíveis |

## Implantação em produção

O sistema roda em um VPS Oracle atrás de Nginx com TLS, em
`https://renda-mspa.duckdns.org`, a partir de
`/home/ubuntu/apps/controle-renda-variavel`. O agente RTD permanece no Windows
e fala com o VPS por HTTPS — ver "Exceção RTD no host Windows".

O código do servidor é espelho do `main`, em sentido único: desenvolvimento na
máquina local, commit, push ao GitHub, e só então implantação. **Não edite
código, não commite e não faça merge no VPS** — `~/deploy.sh renda` aborta ao
encontrar árvore suja, e a *deploy key* do servidor é somente leitura, então um
push de lá falharia de qualquer forma.

`.secrets/` (`secret_key`, `postgres_password`, `rtd_control_token`,
`collector_agent_token`) e `.certs/` não são versionados e vivem apenas no
servidor; um reclone precisa restaurá-los, ou o build falha e o banco fica
inacessível. Os dados ficam no volume
`controle-renda-variavel_postgres_data`, fora da pasta do código: substituir o
diretório do projeto não os afeta. Consulte `docs/deployment-vps.md` antes de
qualquer operação no VPS.

## Evolução de versões

**Faixas de dependência: alargue o teto, mantenha o piso.** O Dependabot roda
com `versioning-strategy: widen`. Quando ele propuser elevar o mínimo, aproveite
apenas a parte que alarga o teto e recuse a que sobe o piso. O piso registra a
compatibilidade mínima efetivamente verificada, não a versão mais nova
disponível: elevá-lo declara uma incompatibilidade que ninguém comprovou e não
muda nada do que é instalado, porque o pip já resolve para a versão mais nova
permitida pela faixa.


Evolua versões deliberadamente: `pyproject.toml` declara o mínimo conhecido e
um teto de incompatibilidade, sem congelar patches compatíveis; toda ampliação
de faixa registra compatibilidade, risco e rollback, reconstrói a imagem e roda
`quality`. A versão do projeto só muda quando a
entrega justificar uma nova versão publicada, em conjunto com a documentação e
validação correspondente. Migrações são aditivas e imutáveis depois de
aplicadas; mudanças incompatíveis usam nova revisão e plano de dados/rollback,
nunca reescrita histórica.
