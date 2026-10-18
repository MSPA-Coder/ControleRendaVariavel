# Desenvolvimento e validação

O projeto usa Docker para aplicação, PostgreSQL, migrações, lint e testes. No
host são necessários apenas Docker Desktop, Git e um editor; o venv do laço
rápido, descrito no `AGENTS.md`, é opcional. A exceção obrigatória é o agente
RTD, que roda no Windows — ver a última seção.

## Ambiente

```powershell
Copy-Item .env.example .env
.\scripts\provision-secrets.ps1
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

`provision-secrets.ps1` gera, sem exibir os valores, `.secrets/secret_key`, a
senha administrativa `.secrets/postgres_password`, a senha do runtime
`.secrets/postgres_app_password`, uma senha sintética para o banco efêmero da
suíte e a senha correspondente do papel de runtime, além dos tokens separados
de leitura e escrita para o coletor. Em instalações antigas,
valores ainda presentes no `.env` servem apenas como migração para arquivos que
não existam. Por padrão arquivos existentes são preservados; `-Force` rotaciona
os segredos deliberadamente, exigindo tratar a senha do banco, a troca dos
tokens e a invalidação das sessões abertas.

Em uma cópia antiga que ainda guardava valores no `.env`, execute também
`.scripts\provision-secrets.ps1 -MigrateDotEnv` depois de confirmar os arquivos
em `.secrets`; o comando substitui os valores por referências locais e preserva
as demais variáveis.

Falta um arquivo que o script não cria, porque não é segredo gerado aqui:

- `.certs/local-root-ca.crt` — o Compose declara esse arquivo como secret, então
  ele **precisa existir** ou `docker compose config` falha antes de qualquer
  coisa. Numa máquina cujo antivírus ou proxy intercepta TLS, ele guarda a raiz
  a acrescentar ao truststore da imagem, sem a qual o `pip install` do build não
  valida a cadeia. Em máquina sem interceptação, um arquivo vazio basta:
  `New-Item -ItemType File .certs\local-root-ca.crt`.

Sem `compose.dev.yaml`, o Compose não monta o código do host. Cada subida
reconstrói as imagens sem cache a partir das tags base móveis; a CI executa
testes e auditorias antes de integrar atualizações de dependências. O serviço
`migrate` roda `flask db upgrade` e termina com sucesso antes de `web` iniciar;
`create_app()` só monta a aplicação e não consulta o banco.

A aplicação fica em <http://127.0.0.1:5301> e o PostgreSQL em `127.0.0.1:5302`
por padrão (`POSTGRES_BIND_ADDRESS` pode alterar o endereço de publicação).
O primeiro acesso precisa de uma conta:

```powershell
docker compose exec web flask --app app:create_app users create-admin
```

O banco usa dois papéis: `investimentos` fica apenas no `migrate` e no
provisionamento; `web` e o agente RTD usam `investimentos_app`. Bancos novos
nascem com checksums (`POSTGRES_INITDB_ARGS=--data-checksums`). A configuração
não habilita checksums retroativamente em um volume existente: isso exige
backup validado, parada limpa e o procedimento operacional documentado no
relatório da implantação.

## Validação automatizada

O comando oficial executa Ruff, o piloto mypy e toda a suíte pytest na imagem `quality`:

```powershell
docker compose --profile quality run --build --rm quality
```

A imagem `quality` instala exatamente o que a imagem servida instala, mais as
ferramentas de teste — o que se valida é o ambiente que roda, não um arquivo de
requisitos.

O mypy roda em modo estrito sobre os arquivos explícitos de `[tool.mypy]`:
cálculos de posições, histórico de quantidades, validação, filtro de moeda e
entradas dos novos comandos. Imports fora dessa fronteira não são analisados
(`follow_imports=skip`): este piloto não afirma verificar o ORM nem todas as
rotas. Não foram adicionados `Any` ou supressões para obter um check verde.
Para executá-lo sozinho: `docker compose --profile quality run --build --rm quality mypy`.
Amplie a lista por fronteira quando o código correspondente estiver pronto.

**`--build` mantém o comando explícito.** O serviço `quality` não monta o código
do host; Compose reconstrói sem cache a partir das tags base atuais antes de
executar os testes. A CI semanal repete esse build, os testes e as auditorias
para verificar atualizações de sistema e dependências.

A suíte protege os contratos que valem para toda requisição: negação por padrão,
autorização por papel, CSRF, cabeçalhos e CSP, health check, resolução de
segredo, configuração de runtime, persistência dos filtros entre trocas de
fragmento, o endereço canônico da barra e a integridade do grafo de migrações.
Do domínio, ela cobre o que decide número na tela: quantidade histórica e fluxo
(`holdings_history`), redução mensal e TWR (`monthly_performance`), preservação
do extrato de posição encerrada (`position_ledger`), o coletor único e o agente
remoto.

**A suíte usa PostgreSQL efêmero no perfil `quality`.** Ela cobre as regras
puramente funcionais e os contratos que precisam do ORM, inclusive ownership,
restrições e a cadeia Alembic. Para mudança de schema, acrescente um ensaio que
crie dados legados sintéticos em schema descartável e confirme tanto a migração
quanto a recusa segura da pré-condição. Nunca aponte esse perfil para o banco
operacional.

A CI valida o Compose, reconstrói a imagem `quality` sem cache, executa o
estágio, audita com `pip-audit` as dependências instaladas — pergunta diferente
da que o Dependabot responde, que é "saiu versão nova?" — e varre a imagem
servida com Trivy, cobrindo os pacotes de sistema que o `pip-audit` não vê. A
varredura roda em contêiner, com `docker save` e `--input`, sem montar o socket
do Docker.

Não afrouxe um controle para contornar achado: atualize a dependência ou a
imagem. Vulnerabilidade sem correção publicada sai por `--ignore-vuln ID` com um
comentário dizendo por quê — cada exceção vira uma decisão explícita e datada,
em vez de um vermelho permanente que se aprende a ignorar.

### Piloto de navegador (dados descartáveis)

Além do `quality`, o Chromium executa os fluxos financeiros em
`compose.browser.yaml`: números e datas BR/EUA, confirmação/cancelamento,
reenvio idempotente e encerramento parcial. Não substitui os testes de
concorrência e autorização com PostgreSQL. Não publica portas nem monta
segredos ou volumes operacionais. Contas e senha do bootstrap são sintéticas.

```powershell
New-Item -ItemType Directory -Force .browser-results
docker compose -f compose.browser.yaml down
docker compose -f compose.browser.yaml build --pull
docker compose -f compose.browser.yaml up --no-build --abort-on-container-exit --exit-code-from browser-tests
# Guarde o código de saída anterior; depois encerre somente essa pilha:
docker compose -f compose.browser.yaml down
```

A CA local já descrita acima é a única entrada do host; `BROWSER_CA_FILE`
permite indicar outra localização. Em Linux, dê escrita ao diretório
`.browser-results` para o usuário do runner (`chmod 777` em CI descartável).
Falhas salvam capturas apenas dos dados fictícios nesse diretório ignorado.
Cada execução começa com banco vazio em tmpfs. A CI `Navegador` repete o
mesmo comando sem instalar Node ou navegadores no host.

### Diante de um teste vermelho

Decida primeiro de quem é o defeito, e diga isso em voz alta:

- **do código** → corrija o código, nunca a asserção;
- **da asserção** → corrija a asserção, e a nova precisa ser mais forte ou mais
  precisa que a antiga, nunca mais permissiva;
- **do ambiente** → prove, comparando o mesmo comando contra o commit anterior,
  antes de descartar.

## Validação proporcional

Além do comando automatizado:

| Mudança | Validação adicional |
|---|---|
| documentação | links, caminhos, comandos, buscas residuais, `git diff --check` e `docker compose config --quiet` |
| rota, template ou HTMX | percorrer o fluxo afetado no navegador, com cenário real |
| cálculo financeiro | conferir contra os exemplos normativos de [`planilha-acoes.md`](planilha-acoes.md) ou [`planilha-opcoes.md`](planilha-opcoes.md) |
| autenticação, autorização, sessão ou CSRF | exercitar login, logout, uma operação mutante e a negação anônima |
| schema ou migração | backup validado, cadeia completa em PostgreSQL vazio, `upgrade` e `downgrade` da revisão alterada, health check |
| dependência, Dockerfile ou Compose | build sem cache quando pertinente, subida completa da pilha e `/health` |
| RTD | provedor determinístico e, quando disponível, um ticker conhecido, sem dado sensível no log |

Registre o que foi executado **e o que foi omitido**. Uma validação que não
aconteceu e não foi dita vira, na leitura seguinte, uma que aconteceu.

## Schema

O schema evolui somente por novas revisões em `migrations/versions/`. Não
reescreva revisão que possa ter sido aplicada, não use `create_all()` como
bootstrap e não use `stamp` no lugar de uma migração. Banco vazio nasce de
`alembic upgrade head`.

Gerar uma revisão exige o override de desenvolvimento: o serviço `web` roda com
filesystem raiz somente leitura e, sem o bind mount, o arquivo gerado não teria
onde ser gravado nem chegaria ao repositório.

```powershell
docker compose -f compose.yaml -f compose.dev.yaml run --rm web flask --app app:create_app db revision --autogenerate -m "descricao"
docker compose exec web flask --app app:create_app db upgrade
```

Revise toda migração gerada: o autogenerate não enxerga renomeação, migração de
dados nem constraint que dependa de conteúdo.

Os dados financeiros vivem no volume `postgres_data`. Nunca use
`docker compose down --volumes` fora de ambiente descartável. Backup, retenção e
restauração são responsabilidade exclusiva do BackupRestore; alteração
destrutiva de dados exige backup validado e autorização explícita.

## Trabalhar sem o RTD

COM/RTD não roda no contêiner Linux, então o caminho real de coleta não existe
no ambiente de desenvolvimento. Isso não bloqueia nada: **sem o agente, a
aplicação continua utilizável**, as cotações aparecem indisponíveis ou
desatualizadas e nenhum cadastro depende delas.

Para exercitar a coleta, use os provedores determinísticos da suíte em vez de
COM. `poll-rtd` e `probe-rtd-direct` falam com o `IRtdServer` do ProfitPro e só
rodam no ambiente Python isolado do Windows, com o ProfitChart aberto — não os
execute no contêiner `web`.

A produção é instalada por `scripts/rtd-agent.ps1 -Action Install` e usa
`python -m app.collector.remote_agent`, sem Flask ou banco local. O local é
iniciado/parado por `scripts/rtd-local.ps1 -Action Start|Stop`, sempre gravando
nesta máquina. Não há alternância de destino nem um vigia ocioso. Locks por
destino permitem coexistência, mas recusam duplicação do mesmo coletor.

Além do quality, valide no Windows: `probe-rtd-direct` e alguns ciclos de
`poll-rtd`, Start repetido sem duplicar processo, Stop encerrando o processo
local, ausência de processo local após Stop e entrega remota com banco local
parado. A indisponibilidade do banco deve encerrar o local com erro e não
disparar reinício automático. A suíte usa provedores fake e cobre prazos,
isolamento, parada e a decodificação do RTD direto; o teste COM real continua
específico do Windows.

O caminho do agente remoto (`REMOTE_COLLECTOR_ENABLED=true`) pode ser exercitado
sem Windows chamando os endpoints `/api/collector/*` com o Bearer token de
`.secrets/collector_agent_read_token` para configuração e
`.secrets/collector_agent_write_token` para cotações/falhas. São superfícies
separadas de propósito.
