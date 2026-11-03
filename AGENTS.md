# ControleRendaVariavel — guia de trabalho

<!-- comum: início. Cópia de _manutencao/compartilhados/AGENTS-comum.md; mude lá e rode sincronizar.py --aplicar. -->
## Regras comuns aos repositórios

**O que espera o "sim" do mantenedor:** commit e push (quando ele pedir),
deploy, qualquer ação no VPS, e apagar dados, volumes ou backups
(`docker compose down -v` inclusive). Na dúvida sobre algo irreversível,
pergunte; no resto, decida e diga o que decidiu.

**Ciclo.** Altere, rode a verificação completa deste repositório (seção
"Validação"), commite direto no `main` e dê `git push`: sem PR, sem push
forçado, sem rebase do `main` publicado. A CI roda depois, só como aviso, mas
vermelho no `main` se investiga. O Dependabot chega por PR, com merge manual. O
deploy é `~/deploy.sh <app>` no VPS1: monta a imagem a partir do `main`,
confere `/health` e volta sozinho se falhar. O VPS só espelha o `main`; nada se
edita lá. Imagem-base nova (correção do Debian ou do Python) só quando ele
pedir: testa na máquina e depois `~/deploy.sh <app> --refresh`. Ao terminar,
diga o que rodou, o resultado e o que deixou de validar.

**Dados e segredos.** Mudança destrutiva ou conversão de dados reais começa por
backup conferido no BackupRestore. Segredos ficam fora do Git (`.secrets/` ou
configuração do usuário) e não aparecem em código, imagem, log, diff,
documento nem relatório.

**Dependências.** Alargue o teto e preserve o piso já verificado: elevar o piso
afirma uma incompatibilidade, e isso exige prova. Vulnerabilidade se corrige
atualizando; exceção só explícita, com motivo e data. Enquanto o Dependabot não
aceitar Python 3.15, ele não propõe atualização de `uv` e o grafo do GitHub fica
parado no lock de 13/10/2026: confira alerta pela versão do `uv.lock`, e
atualize à mão (`uv lock --upgrade-package <pacote>`) quando o `pip-audit` ou o
Trivy apontarem.

**Testes.** `docs/TESTES.md` diz o que testar e como. Diante de vermelho,
decida de quem é o defeito; nunca escreva código para um teste passar. Não
rode duas verificações do mesmo repositório ao mesmo tempo: a segunda recria o
banco de teste e derruba a primeira.

**Cópias.** Este bloco, `docs/TESTES.md` e os arquivos listados em
`_manutencao/compartilhados/sincronizar.py` são cópias: mude a canônica lá e
rode `python compartilhados/sincronizar.py --aplicar` no `_manutencao`.

**Este arquivo.** Guarde aqui só o que o código não diz: limites, decisões com
o motivo, armadilhas e comandos. História fica no `git log`; decisão com
alternativas vira ADR; detalhe de implementação fica no código. Para
acrescentar uma regra, retire ou funda outra: `sincronizar.py` reprova este
arquivo acima de 900 palavras fora deste bloco.
<!-- comum: fim -->

## O sistema

Aplicação Flask com dados financeiros privados por usuário: ações, opções,
cotações, risco e performance. PostgreSQL é a fonte de dados e configurações;
a planilha `Trades.xlsm` é só referência funcional de leitura. No ar em
`https://renda-mspa.duckdns.org` (deploy `~/deploy.sh renda`; no VPS,
`/home/ubuntu/apps/controle-renda-variavel`).

Faz parte da frota com o ControleBancario e o MegaSena: mesmo SharedAuth,
mesmo formato de Compose e de `quality`. Divergir é permitido quando for
escolha consciente. O ConfortoTermico segue trilha própria, e diferença em
relação a ele não é débito.

Leia só o documento do assunto da mudança:

- `docs/architecture.md`: camadas, contrato HTMX, coleta de cotações,
  transações, esquema `leitura` e "Proventos do CB";
- `docs/development.md`: ambiente e o que a suíte deliberadamente não cobre;
- `docs/planilha-acoes.md` e `docs/planilha-opcoes.md`: as regras de cálculo,
  com os exemplos normativos;
- `docs/conciliacao-mensal.md`: a rotina mensal com o myProfit;
- `docs/deployment-vps.md`: operação no VPS;
- `docs/CONCEITOS_FINANCEIROS.md`: vocabulário comum com o CB e o Wealthfolio;
- `docs/travas.md`: locks de banco e o que cada um protege.

Quando uma regra de produto mudar, atualize o documento que a define na mesma
mudança.

## Validação

```powershell
docker compose --profile quality run --build --rm quality
```

Ruff e pytest em paralelo, com um banco de teste novo por worker e por
execução; toda execução aplica a cadeia inteira de Alembic a um banco vazio.
Rode antes de todo commit de código.

A suíte tem duas camadas (docstring de `tests/conftest.py`): a maior parte
recusa conexão de propósito, e os testes `@pytest.mark.banco` falam com o
`db-teste`, efêmero e separado do `db` com dados reais. Atomicidade e
concorrência novas vão nessa camada.

- Mudança visível: suba a pilha (`docker compose up --build -d`, app em
  `127.0.0.1:5301`, banco em `127.0.0.1:5302`) e percorra o fluxo.
- Cálculo: confira contra os exemplos normativos do documento da planilha.
- Autenticação, autorização, CSRF ou sessão: confirme também a negação anônima.
- Só documentação: `git diff --check` e
  `python ../_manutencao/compartilhados/sincronizar.py`.
- Docker ou dependência: build limpo, pilha completa e `/health`.
- RTD: provedor fake; com o ProfitPro disponível, um ticker conhecido.

## Invariantes

- Valores e quantidades persistidos em `Decimal`, nunca `float`. Quantidade e
  preço médio não são negativos; ticker é normalizado; divisão por zero dá
  `None` (não aplicável); arredondamento é explícito.
- Totais derivam das posições e ficam separados por moeda.
- A transação é delimitada no caso de uso que inicia a escrita: nada de commit
  parcial em camada inferior, nem transação aberta durante RTD ou outra chamada
  externa. Invariante concorrente se protege no banco.
- Proventos em reais das corretoras que o CB cobre (Genial e XP), pagos a
  partir de 01/01/2026, vêm do CB (`origin = 'cb'`, só leitura, valor líquido
  do caixa). Planilha e formulário os recusam; a linha de planilha que o CB
  passou a cobrir é marcada como substituída, nunca apagada.
- Migração: revisão Alembic nova, revisada à mão; não se edita revisão que
  possa ter sido aplicada; banco vazio nasce por `alembic upgrade head`, nunca
  por `create_all()` ou `stamp`. Colunas lidas pelo esquema `leitura` não são
  removidas nem mudam de tipo sem recriar a view na mesma revisão.
- Autenticação é padrão: só login, health, estáticos e a API do agente coletor
  (que exige Bearer token próprio) dispensam sessão. Sessão, CSRF, limite de
  login, senhas, cabeçalhos, CSP, formatação pt-BR e health vêm do SharedAuth;
  `_number` em `app/core/presentation.py` é só adaptador de apresentação.

## Coleta RTD no Windows

COM/RTD não roda no contêiner Linux: só o `.venv` do agente RTD roda no host
(`uv sync --locked --extra rtd --python 3.15`). Os dois coletores leem direto
do `IRtdServer` do ProfitPro (`app/collector/rtd_direct.py`) e compartilham
`app/collector/loop.py`, com locks e controles de parada separados:

- produção: `scripts/rtd-agent.ps1` instala uma tarefa do Windows que roda
  `python -m app.collector.remote_agent` e sempre entrega ao VPS, sem Flask nem
  segredos do banco local. No VPS, o botão pausa e retoma pelo campo
  `collector_paused`; o servidor nunca abre conexão para o Windows
  (`REMOTE_COLLECTOR_ENABLED` marca o receptor);
- local: `scripts/rtd-local.ps1 -Action Start` / `-Action Stop`, sempre para o
  PostgreSQL local, sob demanda, sem vigia nem reinício.

Sem agente, a aplicação continua utilizável e mostra cotação indisponível ou
desatualizada. Leitura se normaliza antes do domínio, a última cotação válida
se preserva, e timeout e retentativa são limitados.

## Armadilhas

- O limite de login da aplicação usa `memory://`: um contador por worker,
  zerado no reinício. Em produção quem limita de verdade o `POST /login` é o
  Nginx versionado em `../_manutencao/vps/nginx/`; outra topologia precisa de
  storage compartilhado ou proteção equivalente no edge.
- Backup, retenção e restauração são do BackupRestore; não replique os
  procedimentos aqui. `docker compose down --volumes` só em ambiente
  descartável: os dados vivem no volume `postgres_data`.
- Segredos (`SECRET_KEY`, senha do banco, token do agente) vêm de arquivos
  (`*_FILE` no contêiner, `.secrets/` no host).
- A varredura da imagem roda como contêiner, com `docker save` e `--input`; o
  serviço `web` mantém nome de imagem fixo para ser o alvo.
