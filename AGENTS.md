# ControleRendaVariavel — guia de engenharia

## Escopo e fontes de verdade

Este repositório contém uma aplicação Flask de uso pessoal do mantenedor para
controlar ações, opções, cotações, risco e performance. PostgreSQL é a fonte
operacional de dados e configurações; Docker Compose é a interface de execução.
A planilha `Trades.xlsm` é somente referência funcional de leitura.

Antes de mudar código, leia este arquivo, `README.md`, `pyproject.toml`,
`compose.yaml`, as migrações relevantes e o contrato afetado:

- `docs/architecture.md` para a forma interna: camadas, módulos, contrato HTMX,
  coleta de cotações e limites de transação;
- `docs/development.md` para ambiente, o que a suíte cobre e o que ela
  deliberadamente não cobre, e validação proporcional;
- `docs/planilha-acoes.md` para ações, performance, risco e RTD;
- `docs/planilha-opcoes.md` para opções;
- `docs/deployment-vps.md` para operação no VPS.

Em conflito, prevalecem: solicitação explícita atual do mantenedor; este
arquivo; contratos funcionais em `docs/`; testes e contratos públicos; código
existente. Atualize o contrato funcional na mesma mudança quando uma regra de
produto mudar. Não replique nos documentos detalhes internos que o código
expressa melhor.

## Execução e persistência

Aplicação, PostgreSQL, migrações e build rodam em Docker, e é lá que testes e
lint são validados antes de commitar. Não instale ferramentas do projeto no
Python global do host. Comandos usuais:

```powershell
docker compose up --build -d
docker compose down
docker compose --profile quality run --build --rm quality
docker compose exec web flask --app app:create_app <comando>
Invoke-WebRequest http://127.0.0.1:5301/health
```

### Loop rápido no host

O portão `quality` custa dezenas de segundos por rodada -- caro demais para o
ciclo de edição. Para isso existe um venv do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

O `.venv/` é uma pasta do projeto, já ignorada pelo Git: não altera o Python
do sistema nem o PATH, e apagar a pasta desfaz a instalação por inteiro. A
proibição que vale é outra, e continua de pé -- nada de instalar dependências
do projeto no Python global do Windows.

`sharedauth` vem de repositório privado: o `git` precisa estar autenticado,
ou instale do clone local na tag que `pyproject.toml` fixa.

Os dois ambientes acham defeitos diferentes, então nenhum substitui o outro.
Foi o venv que revelou dois defeitos só-Windows que o contêiner nunca
mostrou (ver o docstring de `tests/conftest.py`); e é o contêiner que tem
`ruff` e `pip-audit` na versão que a CI usa. Itere no venv e passe pelo
`quality` antes de commitar.

O Compose publica a aplicação em `127.0.0.1:5301` e PostgreSQL em
`127.0.0.1:5302`; na rede interna, o banco é `db:5432`. `migrate` aplica
Alembic antes de `web`. O runtime padrão usa a imagem sem bind mount; edição ao
vivo exige `docker compose -f compose.yaml -f compose.dev.yaml up` e não serve
para validar a imagem imutável.

Dados financeiros e configurações vivem no volume `postgres_data`. Nunca use
`docker compose down --volumes` fora de ambiente descartável. Backup, retenção
e restauração são responsabilidade exclusiva do BackupRestore; não replique
seus procedimentos ou detalhes internos neste repositório. Alteração destrutiva
de dados exige backup validado e autorização explícita.

PostgreSQL também é o backend dos testes com persistência; SQLite não o
substitui. Mudança de schema cria nova revisão Alembic, revisada manualmente.
Não edite uma migração que possa ter sido aplicada. Banco vazio nasce por
`alembic upgrade head`, nunca por `create_all()` ou `stamp`; adoção de banco
legado é procedimento administrativo explícito.

## Segurança e runtime

- Autenticação é padrão; somente os endpoints explicitamente públicos de
  login, health, estáticos e agente coletor podem dispensar sessão. A API do
  agente exige Bearer token próprio. Autorização é verificada no servidor e
  toda escrita de navegador usa CSRF.
- Sessão, CSRF, rate limiting da aplicação, controle de acesso, hash de senha,
  senha temporária e trava de troca pendente, destino pós-login seguro e a
  marca que amarra a sessão à senha em vigor,
  cabeçalhos de segurança, CSP, formatação pt-BR e health vêm de SharedAuth.
  Não reimplemente localmente. `_number` em `presentation.py` é apenas um
  adaptador para regras de apresentação deste projeto.
- `SECRET_KEY`, senha do banco e token do agente vêm de arquivos de segredo
  (`*_FILE` no contêiner e `.secrets/` no host). Não os registre em código,
  imagem, logs, documentação, diffs ou commits. `.env`, `.secrets/`,
  `.docker-local/`, `.certs/` e backups permanecem locais e ignorados.
- Preserve CSP sem `unsafe-inline`, assets locais, validação e escape de
  entrada, SQL parametrizado e cookies `HttpOnly`/`SameSite=Lax`. Em produção,
  habilite cookies `Secure` com `FORCE_HTTPS` e `TRUST_PROXY_HEADERS` atrás do
  proxy TLS.
- Produção usa imagem multi-stage, Gunicorn com dois workers, usuário não-root,
  filesystem somente leitura, health checks e dependências de runtime. Não
  monte código do host, socket Docker nem use modo privilegiado em produção.

O padrão `RATELIMIT_STORAGE_URI=memory://` mantém um contador independente por
worker e o zera em reinícios. Na topologia de produção, a configuração de
Nginx versionada em `../_manutencao/vps/nginx/` é requisito e limita de forma
compartilhada somente o `POST /login`. Outra topologia ou múltiplas instâncias
exigem storage compartilhado para o limitador da aplicação ou proteção
equivalente no edge.

## Invariantes financeiros

Delimite a transação no caso de uso que inicia a escrita. Não faça commits
parciais em camadas inferiores nem mantenha transação aberta durante RTD ou
outra chamada externa. Proteja invariantes concorrentes no banco.

- valores monetários e quantidades persistidos usam `Decimal`, nunca `float`;
- quantidade e preço médio não são negativos; ticker é normalizado;
- divisão por zero resulta em `None`/não aplicável; arredondamento é explícito;
- totais derivam das posições e permanecem separados por moeda e por natureza
  real ou simulada;
- carteira simulada não gera movimentos ou transações, não consolida posições
  e não pode ser encerrada;
- mudanças em cálculos devem ser conferidas contra os exemplos normativos dos
  contratos funcionais, com teste de domínio proporcional.

## Exceção RTD no Windows

Excel/COM não roda no contêiner Linux. Somente o ambiente Python isolado do
agente RTD pode executar no host Windows; o restante continua em Docker.

O mecanismo operacional é `scripts/rtd-agent.ps1` → uma tarefa Windows que
executa `poll-rtd --watch`. Uma tarefa, um processo, um destino por vez: o
laço (`app/collector_loop.py`) é o mesmo, e a tela de Configurações escolhe se
as cotações vão ao VPS por HTTPS (`app/remote_collector_agent.py`) ou ao
PostgreSQL desta máquina (`app/collector_database.py`). No destino remoto o
servidor nunca abre conexão para o Windows. `REMOTE_COLLECTOR_ENABLED` habilita
os endpoints e o estado remoto -- e é o que decide se esta instância mostra o
botão de destino, porque só o banco da máquina do ProfitChart é consultado pelo
coletor. A aplicacao web nao inicia, supervisiona nem encerra coletor algum:
o que a tela oferece e pausar e retomar a coleta, gravando
`app_settings.collector_paused`, que o coletor le no proximo intervalo de
verificacao. O estado exibido vem do pulso persistido
(`collector_heartbeat.py`), alimentado pelos dois destinos.

Sem o agente, a aplicação continua utilizável e informa cotações indisponíveis
ou desatualizadas; cadastros não dependem de RTD. Normalize e valide leituras
antes do domínio, preserve a última cotação válida, use timeouts e retentativas
limitadas e não registre credenciais nem dados financeiros sensíveis. Testes e
desenvolvimento sem COM usam provedores determinísticos.

## Validação proporcional

A interface de validação do projeto é:

```powershell
docker compose --profile quality run --build --rm quality
```

O `--build` faz parte do comando, não é refinamento: o serviço não monta o
código do host e `docker compose run` reutiliza a imagem existente sem
reconstruí-la. Sem ele, a validação roda o código anterior e passa.

Registre o que foi executado e omitido. Além do comando acima:

| Mudança | Validação adicional |
|---|---|
| documentação | links, caminhos, comandos, buscas residuais, `git diff --check` e `docker compose config --quiet` |
| rota, domínio ou interface | percorrer o fluxo afetado com cenário real |
| autenticação, autorização, CSRF ou sessão | confirmar também a negação anônima |
| schema ou migração | backup validado, bootstrap em PostgreSQL vazio e health check |
| dependência, Dockerfile ou Compose | build limpo e subida completa da pilha |
| RTD Windows | provedor fake e, quando disponível, ticker conhecido sem dados sensíveis |

A CI valida Compose, executa o estágio `quality`, audita dependências Python e
varre a imagem servida. Não afrouxe controles para contornar achados: atualize
a dependência ou imagem; exceções sem correção disponível devem ser explícitas
e justificadas. A varredura da imagem roda em contêiner com `docker save` e
`--input`, sem montar o socket Docker. O serviço `web` mantém nome de imagem
fixo para oferecer um alvo estável à inspeção.

## Produção e versões

A produção roda atrás de Nginx com TLS em
`https://renda-mspa.duckdns.org`, a partir de
`/home/ubuntu/apps/controle-renda-variavel`. O servidor espelha `main`: não
edite, faça commit ou merge no VPS. Consulte `docs/deployment-vps.md` antes de
operá-lo.

Ao atualizar dependências, alargue o teto compatível e preserve o piso mínimo
já verificado. O Dependabot usa `versioning-strategy: widen`. Elevar o piso
declara uma incompatibilidade e só deve ocorrer com justificativa e validação.
Toda ampliação de faixa reconstrói a imagem e roda `quality`. Migrações são
aditivas e imutáveis depois de aplicadas; mudanças incompatíveis usam nova
revisão e estratégia explícita de dados e rollback.
